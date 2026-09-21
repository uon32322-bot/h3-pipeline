#!/usr/bin/env python3
"""5 段端到端: 把 10 镜分镜 → 5 张独立 2-cell 宫格图 (用 image-to-image 锁定人物产品)

输入: build_storyboard_v2 返回的 10 镜 beats + reference_person_path + product_path
输出: 5 张宫格图路径 (段 1-5), 每张都用男模特参考图
"""
import sys, os, json, base64, time, urllib.request
from pathlib import Path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from grid_prompt_v6 import build_grid_prompt

LK888_BASE = "https://api.lk888.ai/v1"


def _load_key():
    for line in open(os.path.expanduser("~/.config/h3p/secrets.env")):
        line = line.strip()
        if line.startswith("#") or "=" not in line: continue
        k, v = line.split("=", 1)
        if k == "LK888_KEY": return v
    raise RuntimeError("no LK888_KEY")


def slice_10_to_5(beats: list) -> list:
    """10 镜 → 5 段, 每段 2 镜"""
    if len(beats) != 10:
        from llm_modules import _fallback_storyboard
        info = beats[0].get("_info", {}) if beats and isinstance(beats[0], dict) else {}
        beats = _fallback_storyboard(info)
    segments = []
    for seg_idx in range(5):
        start_idx = seg_idx * 2
        seg_beats = beats[start_idx:start_idx + 2]
        segments.append({
            "segment": seg_idx + 1,
            "time_range": [seg_idx * 8, (seg_idx + 1) * 8],
            "beats": seg_beats,
        })
    return segments


def build_segment_grid_prompt(segment: dict, info: dict, copy: dict,
                                product_text: str = "") -> str:
    """为单段生成 2-cell 宫格图 prompt (1 行 × 2 列)"""
    beats = segment["beats"]
    return build_grid_prompt(info, beats, product_text, cell_count=len(beats))


def _b64(p):
    with open(p, "rb") as f:
        return base64.b64encode(f.read()).decode()


def generate_grid_i2i(prompt: str, person_image_path: str,
                       output_path: str) -> dict:
    """用 image-to-image 编辑接口, 锁定模特脸

    接口: POST /v1/images/edits (multipart/form-data)
    - image: 模特参考图 (锁定人物脸/身形/风格)
    - prompt: 文字描述 (动作 + 产品形态)
    - model: tt-image-2
    """
    boundary = "----formboundary789"
    person_b64 = _b64(person_image_path)
    # multipart body (CRLF 必须真换行, 不是 \\r\\n)
    CRLF = "\r\n"
    body = (
        f"--{boundary}{CRLF}"
        f'Content-Disposition: form-data; name="model"{CRLF}{CRLF}tt-image-2{CRLF}'
        f"--{boundary}{CRLF}"
        f'Content-Disposition: form-data; name="prompt"{CRLF}{CRLF}{prompt}{CRLF}'
        f"--{boundary}{CRLF}"
        f'Content-Disposition: form-data; name="size"{CRLF}{CRLF}720x1280{CRLF}'
        f"--{boundary}{CRLF}"
        f'Content-Disposition: form-data; name="image"; filename="person.png"{CRLF}'
        f'Content-Type: image/png{CRLF}{CRLF}'
    ).encode() + base64.b64decode(person_b64) + (
        f"{CRLF}--{boundary}--{CRLF}"
    ).encode()

    req = urllib.request.Request(
        LK888_BASE + "/images/edits",
        data=body,
        headers={
            "Authorization": f"Bearer {_load_key()}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            resp = json.loads(r.read().decode())
        b64_data = resp["data"][0]["b64_json"]
        img_bytes = base64.b64decode(b64_data)
        with open(output_path, "wb") as f:
            f.write(img_bytes)
        return {"_ok": True, "size_kb": len(img_bytes) // 1024, "path": output_path}
    except Exception as e:
        return {"_error": str(e)}


# === 5 段端到端 ===
def render_5_segments(image_path: str, product_text: str, output_dir: str,
                       reference_person_path: str,
                       product_id: str = "shoe") -> dict:
    """5 段端到端 (只生成宫格图, 不跑 H3)"""
    os.makedirs(output_dir, exist_ok=True)
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from llm_modules import (
        recognize_product, build_copy_v2, build_storyboard_v2,
        _fallback_storyboard
    )

    start = time.time()
    # 1. 识别
    print(f"[1/3] 产品识别...", flush=True)
    info = recognize_product(image_path, product_text)
    print(f"  → {info.get('_model')}: {info.get('category')}, 形态: {info.get('shape','')[:60]}", flush=True)

    # 2. 文案
    print(f"[2/3] 文案生成...", flush=True)
    copy = build_copy_v2(info, product_text)
    print(f"  → {copy.get('_model')}: {copy.get('tagline','')[:80]}", flush=True)

    # 3. 分镜 (10 镜分 5 段)
    print(f"[3/3] 分镜 (10 镜分 5 段)...", flush=True)
    beats = build_storyboard_v2(info, copy)
    if not beats or len(beats) < 10:
        print(f"  ! LLM 输出 {len(beats)} 镜, fallback 补齐", flush=True)
        beats = _fallback_storyboard(info)
    segments = slice_10_to_5(beats)
    print(f"  → 模型: {beats[0].get('_model')}", flush=True)

    # 4. 生成 5 张宫格图 (每段 1 张, 用 image-to-image)
    print(f"[4/4] 生成 5 张宫格图 (image-to-image, 锁定人物)...", flush=True)
    grids = []
    for seg in segments:
        seg_num = seg["segment"]
        prompt = build_segment_grid_prompt(seg, info, copy, product_text)
        out_path = f"{output_dir}/seg{seg_num}_grid.png"
        print(f"  段{seg_num} prompt {len(prompt)}字 生成中...", flush=True)
        res = generate_grid_i2i(prompt, reference_person_path, out_path)
        print(f"    → {res}", flush=True)
        grids.append({"segment": seg_num, "path": out_path, "result": res})

    # 5. 保存
    result = {
        "recognition": info,
        "copy": copy,
        "storyboard": beats,
        "segments": segments,
        "grids": grids,
        "elapsed_sec": time.time() - start,
    }
    json_path = f"{output_dir}/result.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"\n总耗时: {time.time()-start:.1f}s, 保存 {json_path}", flush=True)
    return result