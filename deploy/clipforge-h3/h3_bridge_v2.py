"""
H3 Bridge v2 - Atlas Cloud API compatible service (fixed workflow conversion)

Receives Atlas-format video generation requests from ClipForge (running on hao),
converts them to ComfyUI workflow submissions on the local GPU box (nmb2),
and returns Atlas-format responses.

Fixes in v2:
  - Widget mapping is derived from ComfyUI /object_info (was hardcoded -> 400 errors)
  - Generated video is copied to a served dir under the Atlas task id
  - Returned URL uses SERVED_URL_BASE (reachable from ClipForge via SSH tunnel)

Endpoints:
  GET  /health
  GET  /api/v1/models
  POST /api/v1/model/generateVideo
  GET  /api/v1/model/prediction/{task_id}
  POST /api/v1/model/uploadMedia   (uploaded files served on FILE_PORT)
"""
import os
import json
import asyncio
import logging
import shutil
import uuid
import time
from pathlib import Path
from typing import Optional, Dict, Any, List

import aiohttp
from fastapi import FastAPI, UploadFile, File, HTTPException, BackgroundTasks
from pydantic import BaseModel
import uvicorn

# =============================================================================
# Config
# =============================================================================
COMFYUI_URL = os.environ.get("COMFYUI_URL", "http://127.0.0.1:6011")
BRIDGE_PORT = int(os.environ.get("BRIDGE_PORT", "8900"))
FILE_PORT = int(os.environ.get("FILE_PORT", "8901"))
# Base URL the CLIENT (ClipForge, on hao) should use to fetch generated media.
SERVED_URL_BASE = os.environ.get("SERVED_URL_BASE", f"http://127.0.0.1:{FILE_PORT}")

OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR", "/root/autodl-tmp/h3p/output/video/H3CHAIN"))
WORKFLOW_PATH = os.environ.get(
    "WORKFLOW_PATH",
    "/root/autodl-tmp/h3p/comfy/custom_nodes/ComfyUI-H3-Multishot/workflows/H3_Chain_5shot_zh_ecom.json",
)
SERVED_DIR = Path(os.environ.get("SERVED_DIR", "/tmp/h3_bridge_served"))
UPLOAD_DIR = Path("/tmp/h3_bridge_uploads")
LOG_DIR = Path("/tmp/h3_bridge_logs")

for d in (LOG_DIR, SERVED_DIR, UPLOAD_DIR, OUTPUT_DIR):
    d.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(LOG_DIR / "bridge.log"), logging.StreamHandler()],
)
log = logging.getLogger("h3_bridge")

# =============================================================================
# Model catalog (Atlas Cloud shape)
# =============================================================================
MODELS: List[Dict[str, Any]] = [
    {
        "model": "minimax/h3/text-to-video",
        "type": "Video",
        "displayName": "MiniMax H3 Local (文生视频)",
        "profile": "Local H3 · INT8 + 4-step Turbo LoRA · native audio",
        "categories": ["TEXT-TO-VIDEO"],
        "price": {"actual": {"base_price": "0.0"}},
        "priority": 90038,
    },
    {
        "model": "minimax/h3/image-to-video",
        "type": "Video",
        "displayName": "MiniMax H3 Local (图生视频)",
        "profile": "Local H3 · first-frame guided",
        "categories": ["IMAGE-TO-VIDEO"],
        "price": {"actual": {"base_price": "0.0"}},
        "priority": 90039,
    },
    {
        "model": "minimax/h3/reference-to-video",
        "type": "Video",
        "displayName": "MiniMax H3 Local (参考生视频)",
        "profile": "Local H3 · multi-reference",
        "categories": ["IMAGE-TO-VIDEO", "VIDEO-TO-VIDEO"],
        "price": {"actual": {"base_price": "0.0"}},
        "priority": 90040,
    },
]

TASKS: Dict[str, Dict[str, Any]] = {}
LINK_TYPES = {"VIDEO", "IMAGE", "AUDIO", "MODEL", "CLIP", "VAE", "LATENT", "MASK", "CONDITIONING"}
SKIP_NODES = {"Note", "MarkdownNote"}


class SubmitRequest(BaseModel):
    model: str
    prompt: str
    duration: Optional[int] = 8
    resolution: Optional[str] = "720p"
    ratio: Optional[str] = "9:16"
    generate_audio: Optional[bool] = True
    watermark: Optional[bool] = False
    image: Optional[str] = None
    last_image: Optional[str] = None
    reference_images: Optional[list] = None
    reference_videos: Optional[list] = None
    reference_audios: Optional[list] = None
    seed: Optional[int] = None


# =============================================================================
# ComfyUI helpers
# =============================================================================
async def comfy_get(path: str, timeout: int = 20):
    async with aiohttp.ClientSession() as s:
        async with s.get(f"{COMFYUI_URL}{path}", timeout=aiohttp.ClientTimeout(total=timeout)) as r:
            return r.status, (await r.json() if r.status == 200 else await r.text())


async def fetch_node_schemas(node_types) -> Dict[str, dict]:
    """Fetch required-input schemas (widget order) for the given node types."""
    schemas: Dict[str, dict] = {}
    types = [t for t in sorted(set(node_types)) if t not in SKIP_NODES]
    async with aiohttp.ClientSession() as s:
        for i in range(0, len(types), 5):
            batch = types[i:i + 5]
            params = "&".join(f"node_names={t}" for t in batch)
            try:
                async with s.get(
                    f"{COMFYUI_URL}/object_info?{params}",
                    timeout=aiohttp.ClientTimeout(total=25),
                ) as r:
                    if r.status != 200:
                        log.warning(f"schema fetch HTTP {r.status} for {batch}")
                        continue
                    data = await r.json()
                    for t in batch:
                        info = data.get(t)
                        if info:
                            schemas[t] = info.get("input", {}).get("required", {})
            except Exception as e:
                log.warning(f"schema fetch failed for {batch}: {e}")
    return schemas


def wf_to_prompt_api(wf: dict, schemas: Dict[str, dict]) -> dict:
    """Convert a ComfyUI UI-workflow into /prompt API format.

    Widget values are matched positionally against the widget inputs reported by
    /object_info — the same strategy the verified v14 runner uses.
    """
    link_index: Dict[int, tuple] = {}
    for link in wf.get("links", []):
        if len(link) == 6:
            link_id, from_node, from_slot, to_node, to_slot, _ = link
        elif len(link) == 5:
            from_node, from_slot, to_node, to_slot, _ = link
            link_id = None
        else:
            continue
        if link_id is not None:
            link_index[link_id] = (str(from_node), from_slot)

    node_ids = {str(n["id"]) for n in wf.get("nodes", [])}
    prompt_api: Dict[str, Any] = {}
    for n in wf.get("nodes", []):
        if n["type"] in SKIP_NODES:
            continue
        nid = str(n["id"])
        inputs: Dict[str, Any] = {}

        # 1) linked inputs
        for inp in n.get("inputs", []):
            if not isinstance(inp, dict):
                continue
            name, link = inp.get("name"), inp.get("link")
            if name and link is not None and link in link_index:
                fn, fs = link_index[link]
                inputs[name] = [fn, fs]

        # 2) widget inputs, positional per /object_info
        schema = schemas.get(n["type"], {})
        wv = list(n.get("widgets_values") or [])
        widget_names = []
        for iname, ispec in schema.items():
            if isinstance(ispec, list) and len(ispec) >= 1:
                t = ispec[0]
                if isinstance(t, list):
                    t = "OPTIONS"
                if t in LINK_TYPES:
                    continue
                widget_names.append(iname)
        for i, wname in enumerate(widget_names):
            if i >= len(wv):
                break
            val = wv[i]
            if wname in inputs:
                continue
            # a 2-element list pointing at a node id is a leftover link, not a value
            if isinstance(val, list) and len(val) == 2 and str(val[0]) in node_ids:
                continue
            inputs[wname] = val

        prompt_api[nid] = {"class_type": n["type"], "inputs": inputs}
        if n.get("title"):
            prompt_api[nid]["_meta"] = {"title": n["title"]}
    return prompt_api


def map_dims(resolution: Optional[str], ratio: Optional[str]) -> tuple:
    base = {"480p": 480 * 854, "720p": 720 * 1280, "1080p": 1080 * 1920}.get(
        resolution or "720p", 720 * 1280
    )
    rw, rh = {"16:9": (16, 9), "9:16": (9, 16), "1:1": (1, 1)}.get(ratio or "9:16", (9, 16))
    # area = base; keep aspect rw:rh  ->  w = sqrt(base*rw/rh), h = sqrt(base*rh/rw)
    w = int((base * rw / rh) ** 0.5)
    h = int((base * rh / rw) ** 0.5)
    return max(8, (w // 8) * 8), max(8, (h // 8) * 8)


async def build_workflow(model: str, req: SubmitRequest) -> dict:
    with open(WORKFLOW_PATH) as f:
        wf = json.load(f)

    model_name = "minimax_h3_fl2va_pruned_int8_convrot.safetensors"
    fps = 24
    frames = min((req.duration or 8) * fps, 192)  # cap per-shot at 8s for VRAM safety
    width, height = map_dims(req.resolution, req.ratio)
    n_shots = max(1, req.prompt.count("Shot "))

    # locate nodes; we override their API inputs by NAME after conversion,
    # never by positional widgets_values index (ordering is node-specific).
    sampler_id = controls_id = loader_id = None
    for n in wf.get("nodes", []):
        t = n.get("type")
        if t == "H3MultishotSampler":
            sampler_id = str(n["id"])
        elif t == "H3StudioControls":
            controls_id = str(n["id"])
        elif t == "H3ModelLoaderAny":
            loader_id = str(n["id"])

    schemas = await fetch_node_schemas([n["type"] for n in wf.get("nodes", [])])
    prompt_api = wf_to_prompt_api(wf, schemas)

    if loader_id and loader_id in prompt_api:
        prompt_api[loader_id]["inputs"]["model_name"] = model_name
    if controls_id and controls_id in prompt_api:
        prompt_api[controls_id]["inputs"].update(
            {"width": width, "height": height, "frames_per_shot": frames, "steps": 4}
        )
    if sampler_id and sampler_id in prompt_api:
        prompt_api[sampler_id]["inputs"]["script"] = req.prompt
        prompt_api[sampler_id]["inputs"]["shot_count"] = n_shots

    log.info(
        f"workflow built: sampler={sampler_id} shots={n_shots} dims={width}x{height} frames={frames} schemas={len(schemas)}"
    )
    return prompt_api


async def submit_to_comfyui(prompt_api: dict, client_id: str) -> str:
    async with aiohttp.ClientSession() as s:
        async with s.post(
            f"{COMFYUI_URL}/prompt",
            json={"prompt": prompt_api, "client_id": client_id},
            timeout=aiohttp.ClientTimeout(total=60),
        ) as r:
            body = await r.text()
            if r.status != 200:
                raise RuntimeError(f"ComfyUI {r.status}: {body[:600]}")
            return json.loads(body)["prompt_id"]


async def poll_comfyui(prompt_id: str):
    async with aiohttp.ClientSession() as s:
        async with s.get(
            f"{COMFYUI_URL}/history/{prompt_id}", timeout=aiohttp.ClientTimeout(total=15)
        ) as r:
            if r.status != 200:
                return None
            return await r.json()


def extract_video_path(history: dict, prompt_id: str) -> Optional[Path]:
    res = history.get(prompt_id, {})
    for _nid, out in (res.get("outputs") or {}).items():
        for img in out.get("images", []) or []:
            fn = img.get("filename", "")
            if fn.endswith(".mp4"):
                cand = OUTPUT_DIR / fn
                if cand.exists():
                    return cand
                alt = Path("/root/autodl-tmp/h3p/output") / (img.get("subfolder") or "") / fn
                if alt.exists():
                    return alt
    return None


# =============================================================================
# Background runner
# =============================================================================
async def run_h3_task(task_id: str, req: SubmitRequest):
    log.info(f"[{task_id}] start model={req.model} dur={req.duration} ratio={req.ratio}")
    TASKS[task_id].update(status="processing", started_at=time.time())
    try:
        prompt_api = await build_workflow(req.model, req)
        prompt_id = await submit_to_comfyui(prompt_api, f"h3_bridge_{task_id}")
        TASKS[task_id]["prompt_id"] = prompt_id
        log.info(f"[{task_id}] submitted -> {prompt_id}")

        deadline = time.time() + 90 * 60
        while time.time() < deadline:
            await asyncio.sleep(5)
            hist = await poll_comfyui(prompt_id)
            if not hist or prompt_id not in hist:
                continue
            st = hist[prompt_id].get("status", {})
            if st.get("completed"):
                src = extract_video_path(hist, prompt_id)
                if not src:
                    TASKS[task_id].update(status="failed", error="no_output_video")
                    log.error(f"[{task_id}] completed but no mp4 found")
                    return
                dest = SERVED_DIR / f"{task_id}.mp4"
                shutil.copy2(src, dest)
                url = f"{SERVED_URL_BASE}/files/{task_id}.mp4"
                TASKS[task_id].update(
                    status="succeeded",
                    result={"videoUrls": [url], "duration": req.duration},
                    source_file=str(src),
                )
                log.info(f"[{task_id}] SUCCEEDED {src} -> {url}")
                return
            if st.get("status_str") == "error":
                TASKS[task_id].update(
                    status="failed", error=str(st.get("messages", []))[:600]
                )
                log.error(f"[{task_id}] comfy error: {TASKS[task_id]['error']}")
                return
        TASKS[task_id].update(status="failed", error="timeout_90min")
    except Exception as e:
        log.exception(f"[{task_id}] exception")
        TASKS[task_id].update(status="failed", error=str(e)[:600])


# =============================================================================
# App
# =============================================================================
app = FastAPI(title="H3 Bridge (Atlas-compatible)")


@app.get("/health")
async def health():
    try:
        status, _ = await comfy_get("/system_stats", timeout=8)
        comfy_ok = status == 200
    except Exception:
        comfy_ok = False
    return {
        "bridge": "ok",
        "comfyui": comfy_ok,
        "comfyui_url": COMFYUI_URL,
        "served_url_base": SERVED_URL_BASE,
        "tasks_pending": sum(
            1 for t in TASKS.values() if t["status"] in ("submitted", "processing")
        ),
    }


@app.get("/api/v1/models")
async def list_models():
    return {"data": MODELS}


@app.post("/api/v1/model/generateVideo")
async def generate_video(req: SubmitRequest, bg: BackgroundTasks):
    task_id = f"h3_{uuid.uuid4().hex[:12]}"
    TASKS[task_id] = {
        "status": "submitted",
        "model": req.model,
        "prompt": req.prompt[:300],
        "duration": req.duration,
        "created_at": time.time(),
    }
    log.info(f"[{task_id}] NEW TASK model={req.model} dur={req.duration} ratio={req.ratio} prompt_len={len(req.prompt)}")
    bg.add_task(run_h3_task, task_id, req)
    return {"code": 200, "data": {"id": task_id}}


@app.get("/api/v1/model/prediction/{task_id}")
async def get_prediction(task_id: str):
    t = TASKS.get(task_id)
    if not t:
        raise HTTPException(404, f"task {task_id} not found")
    resp: Dict[str, Any] = {
        "id": task_id,
        "model": t.get("model"),
        "status": t["status"],
        "outputs": [],
    }
    if t["status"] == "succeeded":
        resp["result"] = t.get("result", {})
        resp["outputs"] = t.get("result", {}).get("videoUrls", [])
    if t["status"] == "failed":
        resp["error"] = t.get("error", "unknown")
    return resp


@app.post("/api/v1/model/uploadMedia")
async def upload_media(file: UploadFile = File(...)):
    uid = uuid.uuid4().hex[:12]
    dest = UPLOAD_DIR / f"{uid}_{file.filename}"
    dest.write_bytes(await file.read())
    url = f"{SERVED_URL_BASE}/uploads/{dest.name}"
    log.info(f"upload {file.filename} -> {url}")
    return {"code": 200, "data": {"url": url}}


@app.get("/api/v1/tasks")
async def list_tasks():
    return {"data": [
        {k: v for k, v in t.items() if k != "prompt"} | {"id": tid}
        for tid, t in TASKS.items()
    ]}


# =============================================================================
# Media file server (separate port, reachable from ClipForge via SSH tunnel)
# =============================================================================
async def file_server():
    from aiohttp import web

    async def serve(request):
        fname = request.match_info["filename"]
        for base in (SERVED_DIR, UPLOAD_DIR):
            p = base / fname
            if p.exists():
                return web.FileResponse(str(p))
        return web.Response(status=404, text="not found")

    a = web.Application()
    a.router.add_get("/files/{filename}", serve)
    a.router.add_get("/uploads/{filename}", serve)
    runner = web.AppRunner(a)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", FILE_PORT).start()
    log.info(f"media server on :{FILE_PORT} (served dir {SERVED_DIR})")


@app.on_event("startup")
async def _startup():
    asyncio.create_task(file_server())
    log.info(f"bridge on :{BRIDGE_PORT} -> comfy {COMFYUI_URL}, served base {SERVED_URL_BASE}")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=BRIDGE_PORT, log_level="info")
