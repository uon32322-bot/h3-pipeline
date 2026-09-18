#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Run one MiniMax H3 Ultra Boost T2V generation through the ComfyUI API and time it."""
import json, sys, time, urllib.request, uuid, os

import os
HOST = "http://127.0.0.1:6006"
SPARSE_BACKEND = os.environ.get("SPARSE_BACKEND", "BF16 Triton")
SECONDS = float(sys.argv[1]) if len(sys.argv) > 1 else 5.0
MP = float(sys.argv[2]) if len(sys.argv) > 2 else 0.4
STEPS = int(sys.argv[3]) if len(sys.argv) > 3 else 6

frames = int(max(5, round(SECONDS * 24)))
frames = frames + (5 - (frames % 17)) % 17

PROMPT = (
    "实拍感 UGC 带货短视频风格，手机竖屏手持拍摄质感，自然光，浅景深，画面干净真实。\n"
    "Scene overview: 一位年轻女生坐在明亮的居家客厅窗边，手里拿着一支保湿精华，"
    "面对镜头自然讲解，语气轻松有亲和力。\n"
    "[0s-2s] Shot 1: 中近景，女生抬头看向镜头微笑，手里轻轻转动精华瓶。\n"
    "[2s-4s] Shot 2: 特写，她在手背抹开精华，镜头轻微前推。\n"
    "[4s-5s] Shot 3: 回到中近景，举瓶到镜头前点头肯定。\n"
    "Camera: 手持轻微晃动，缓慢前推。\n"
    "Audio: 女声轻松自然的中文口播，室内轻微环境音，轻柔背景音乐。\n"
    "No text, subtitles, logos or watermarks."
)

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
        "aspect_ratio": "16:9 (Widescreen)", "megapixels": MP, "multiple": 32}},
    "9": {"class_type": "MiniMaxH3ImageToVideo", "inputs": {
        "clip": ["2", 0], "vae": ["3", 0], "prompt": PROMPT,
        "width": ["8", 0], "height": ["8", 1], "length": frames}},
    "10": {"class_type": "RandomNoise", "inputs": {"noise_seed": 123456789}},
    "11": {"class_type": "BasicGuider", "inputs": {"model": ["7", 0], "conditioning": ["9", 0]}},
    "12": {"class_type": "MiniMaxH3TurboSampler", "inputs": {}},
    "13": {"class_type": "BasicScheduler", "inputs": {
        "model": ["7", 0], "scheduler": "simple", "steps": STEPS, "denoise": 1.0}},
    "14": {"class_type": "SamplerCustomAdvanced", "inputs": {
        "noise": ["10", 0], "guider": ["11", 0], "sampler": ["12", 0],
        "sigmas": ["13", 0], "latent_image": ["9", 1]}},
    "15": {"class_type": "VAEDecode", "inputs": {"samples": ["14", 0], "vae": ["3", 0]}},
    "16": {"class_type": "VAEDecodeAudio", "inputs": {"samples": ["14", 0], "vae": ["4", 0]}},
    "17": {"class_type": "CreateVideo", "inputs": {"images": ["15", 0], "fps": 24.0, "audio": ["16", 0]}},
    "18": {"class_type": "SaveVideo", "inputs": {
        "video": ["17", 0], "filename_prefix": "video/H3-UltraBoost-TEST",
        "format": "auto", "codec": "auto"}},
}

cid = str(uuid.uuid4())
req = urllib.request.Request(
    HOST + "/prompt",
    data=json.dumps({"prompt": g, "client_id": cid}).encode(),
    headers={"Content-Type": "application/json"},
)
try:
    r = json.load(urllib.request.urlopen(req, timeout=60))
except urllib.error.HTTPError as e:
    print("SUBMIT FAILED", e.code)
    print(e.read().decode()[:3000])
    sys.exit(1)

pid = r["prompt_id"]
print(f"submitted prompt_id={pid}  seconds={SECONDS} mp={MP} steps={STEPS} frames={frames}")
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
        print(f"elapsed={el:.1f}s  status={st.get('status_str')}  completed={st.get('completed')}")
        outs = h[pid].get("outputs", {})
        files = []
        for nid, o in outs.items():
            for k in ("images", "videos", "gifs", "audio"):
                for it in (o.get(k) or []):
                    files.append(it.get("filename"))
        print("outputs:", files)
        if st.get("status_str") == "error":
            for m in st.get("messages", []):
                if m[0] in ("execution_error", "execution_interrupted"):
                    print(json.dumps(m[1], ensure_ascii=False)[:2500])
        print(f"RESULT elapsed_seconds={el:.1f}")
        break
    if time.time() - t0 > 3600:
        print("TIMEOUT after 3600s"); break
