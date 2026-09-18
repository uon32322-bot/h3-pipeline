#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MiniMax H3 FL2VA —— 首尾帧 + 任意中间锚点(AddGuide) 测试脚本

用法:
  test_h3.py --first A.png --last B.png --prompt q.txt --length 124 \
             --guide C.png@62 [--guide D.png@100] --prefix video/X --seed 1

参数:
  --first / --last   首帧 / 尾帧图（ComfyUI input 内的文件名）
  --prompt           提示词文件路径
  --length           总帧数（会自动吸附到 17k+5 网格）
  --guide FILE@IDX   中间锚点，可重复；@ 后面是帧号（支持 -1 表示倒数）
  --mp / --steps / --prefix / --seed
"""
import json, os, sys, time, urllib.error, urllib.request, uuid

HOST = "http://127.0.0.1:6006"
SPARSE_BACKEND = os.environ.get("SPARSE_BACKEND", "BF16 Triton")

args = sys.argv[1:]
def opt(name, default=None):
    if name in args:
        return args[args.index(name) + 1]
    return default
def multi(name):
    out = []
    for i, a in enumerate(args):
        if a == name and i + 1 < len(args):
            out.append(args[i + 1])
    return out

FIRST = opt("--first")
LAST = opt("--last")
PROMPT = open(opt("--prompt"), encoding="utf-8").read().strip()
LENGTH = int(opt("--length", "124"))
MP = float(opt("--mp", "0.4"))
STEPS = int(opt("--steps", "8"))
PREFIX = opt("--prefix", "video/H3-TEST")
SEED = int(opt("--seed", "123456789"))
ASPECT = opt("--aspect", "16:9 (Widescreen)")
GUIDES = multi("--guide")
AUDIOS = multi("--audio")   # 格式 文件名@帧号（省略 @帧号 则锚在 0 帧）

frames = LENGTH + (5 - (LENGTH % 17)) % 17
if frames < 5:
    frames = 5

g = {
    "1": {"class_type": "UNETLoader", "inputs": {
        "unet_name": "minimax_h3_fl2va_pruned_int8_convrot.safetensors", "weight_dtype": "default"}},
    "2": {"class_type": "CLIPLoader", "inputs": {
        "clip_name": "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors", "type": "minimax", "device": "default"}},
    "3": {"class_type": "VAELoader", "inputs": {"vae_name": "minimax_h3_video_vae_fp16.safetensors"}},
    "4": {"class_type": "VAELoader", "inputs": {"vae_name": "minimax_h3_audio_vae_fp32.safetensors"}},
    "5": {"class_type": "MiniMaxH3TurboLoRA", "inputs": {
        "model": ["1", 0], "lora_name": "minimax_h3_turbo_v4_step600_ema.safetensors",
        "strength": 1.0, "low_vram": False}},
    "6": {"class_type": "H3MemoryOptimization", "inputs": {
        "model": ["5", 0], "fused_qkv": "auto", "mlp_memory": "auto", "chunk_rows": 4096,
        "preserve_precision": True, "precision_mode": "Auto", "qkv_streaming_mode": "Auto",
        "embedding_memory_mode": "Auto", "kitchen_v_memory_mode": "Standard"}},
    "7": {"class_type": "H3SparseAttentionAdvanced", "inputs": {
        "model": ["6", 0], "video_budget": 0.3, "early_steps": 2, "early_kv": 0.5,
        "late_steps": 0, "late_kv": 0.5, "backend": SPARSE_BACKEND,
        "early_schedule": "Ramp", "video_token_order": "1x8x8"}},
    "8": {"class_type": "ResolutionSelector", "inputs": {
        "aspect_ratio": ASPECT, "megapixels": MP, "multiple": 32}},
    "19": {"class_type": "LoadImage", "inputs": {"image": FIRST}},
    "20": {"class_type": "LoadImage", "inputs": {"image": LAST}},
    "9": {"class_type": "MiniMaxH3ImageToVideo", "inputs": {
        "clip": ["2", 0], "vae": ["3", 0], "prompt": PROMPT,
        "width": ["8", 0], "height": ["8", 1], "length": frames,
        "first_frame": ["19", 0], "last_frame": ["20", 0]}},
    "10": {"class_type": "RandomNoise", "inputs": {"noise_seed": SEED}},
    "12": {"class_type": "MiniMaxH3TurboSampler", "inputs": {}},
    "13": {"class_type": "BasicScheduler", "inputs": {
        "model": ["7", 0], "scheduler": "simple", "steps": STEPS, "denoise": 1.0}},
}

# ---- AddGuide 链：每个中间锚点一个节点，positive 串起来，latent 原地写入 ----
prev_pos = ["9", 0]
guide_ids = []
for i, spec in enumerate(GUIDES):
    fn, _, idx = spec.rpartition("@")
    if not fn:
        raise SystemExit("guide 格式应为 文件名@帧号: " + spec)
    nid = str(100 + i)
    img_id = str(200 + i)
    g[img_id] = {"class_type": "LoadImage", "inputs": {"image": fn}}
    g[nid] = {"class_type": "MiniMaxH3AddGuide", "inputs": {
        "positive": prev_pos, "latent": ["9", 1], "vae": ["3", 0], "audio_vae": ["4", 0],
        "image": [img_id, 0], "frame_idx": int(idx)}}
    prev_pos = [nid, 0]
    guide_ids.append(nid)

# ---- Audio guide 链：把一段真实音频锚定到指定帧（音画同步的硬手段）----
for j, spec in enumerate(AUDIOS):
    fn, sep, idx = spec.rpartition("@")
    if not sep:
        fn, idx = spec, "0"
    nid = str(100 + len(GUIDES) + j)
    aud_id = str(400 + j)
    g[aud_id] = {"class_type": "LoadAudio", "inputs": {"audio": fn}}
    g[nid] = {"class_type": "MiniMaxH3AddGuide", "inputs": {
        "positive": prev_pos, "latent": ["9", 1], "audio_vae": ["4", 0],
        "audio": [aud_id, 0], "frame_idx": int(idx)}}
    prev_pos = [nid, 0]
    guide_ids.append(nid)

g["11"] = {"class_type": "BasicGuider", "inputs": {"model": ["7", 0], "conditioning": prev_pos}}
g["14"] = {"class_type": "SamplerCustomAdvanced", "inputs": {
    "noise": ["10", 0], "guider": ["11", 0], "sampler": ["12", 0],
    "sigmas": ["13", 0], "latent_image": ["9", 1]}}
g["15"] = {"class_type": "VAEDecode", "inputs": {"samples": ["14", 0], "vae": ["3", 0]}}
g["16"] = {"class_type": "VAEDecodeAudio", "inputs": {"samples": ["14", 0], "vae": ["4", 0]}}
g["17"] = {"class_type": "CreateVideo", "inputs": {"images": ["15", 0], "fps": 24.0, "audio": ["16", 0]}}
g["18"] = {"class_type": "SaveVideo", "inputs": {
    "video": ["17", 0], "filename_prefix": PREFIX, "format": "auto", "codec": "auto"}}

cid = str(uuid.uuid4())
req = urllib.request.Request(
    HOST + "/prompt",
    data=json.dumps({"prompt": g, "client_id": cid}).encode(),
    headers={"Content-Type": "application/json"},
)
try:
    r = json.load(urllib.request.urlopen(req, timeout=60))
except urllib.error.HTTPError as e:
    print("SUBMIT FAILED", e.code, flush=True)
    print(e.read().decode()[:3000], flush=True)
    sys.exit(1)

pid = r["prompt_id"]
print(f"submitted prompt_id={pid} first={FIRST} last={LAST} len={frames} mp={MP} steps={STEPS} "
      f"guides={GUIDES} seed={SEED}", flush=True)
t0 = time.time()
while True:
    time.sleep(5)
    try:
        h = json.load(urllib.request.urlopen(f"{HOST}/history/{pid}", timeout=30))
    except Exception:
        continue
    if pid in h:
        el = time.time() - t0
        st = h[pid].get("status", {})
        print(f"elapsed={el:.1f}s  status={st.get('status_str')}  completed={st.get('completed')}", flush=True)
        files = []
        for nid, o in h[pid].get("outputs", {}).items():
            for k in ("images", "videos", "gifs", "audio"):
                for it in (o.get(k) or []):
                    files.append(it.get("filename"))
        print("outputs:", files, flush=True)
        if st.get("status_str") == "error":
            for m in st.get("messages", []):
                if m[0] in ("execution_error", "execution_interrupted"):
                    print(json.dumps(m[1], ensure_ascii=False)[:2500], flush=True)
        print(f"RESULT elapsed_seconds={el:.1f} frames={frames}", flush=True)
        break
    if time.time() - t0 > 3600:
        print("TIMEOUT after 3600s", flush=True)
        break
