#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MiniMax H3 Ref2VA —— 参考图/参考视频/参考音频 生视频（含原生音频）

与 test_h3.py（FL2VA 首尾帧）并列的另一条链路。
节点图谱对齐官方 R2V 模板：UNETLoader(ref2va) → LoraLoaderModelOnly(turbo LoRA)
→ H3 显存/稀疏注意力优化 → MiniMaxH3ReferenceToVideo → SamplerCustomAdvanced
→ VAEDecode + VAEDecodeAudio → CreateVideo → SaveVideo

用法:
  test_r2v.py --prompt q.txt --ref P1.png --ref P2.png \
              --length 73 --mp 0.4 --steps 8 --aspect "9:16 (Portrait Widescreen)" \
              --prefix video/R2V-TEST --seed 1 \
              [--audio vo.wav]        # 参考音频 → <Audio j>（音色参考）
              [--ref-video rv_a.mp4]  # 参考视频 → <Video k>（动作/画风参考）
              [--ref-video-audio rv_a.wav]  # 第 k 条参考视频的音轨（自动配对）
              [--lora 文件名] [--lora-strength 1.0]
              [--ref-size match|max]  # max = 2048 短边，身份保真最好但慢数倍
              [--sparse-budget F]     # 稀疏注意力视频预算，默认 0.3；1.0 ≈ 全注意力
              [--sparse-off]          # 直接旁路 H3SparseAttentionAdvanced（纯全注意力）

参考图在提示词里用 <Picture i> 引用（i 从 1 开始，顺序 = --ref 给的顺序）；
参考音频用 <Audio j>；参考视频用 <Video k>。
--ref-video 走 LoadVideo → GetVideoComponents(images) → ref_video_N，
节点内部按 24fps 取帧、截断到生成帧数（不足 5 帧会报错），且经 VAE 编码后
作为 ref block 参与每一步采样（不参与去噪），Qwen 侧则以 2fps + 时间戳看视频。
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

PROMPT_PATH = opt("--prompt")
if not PROMPT_PATH:
    raise SystemExit("必须给 --prompt")
PROMPT = open(PROMPT_PATH, encoding="utf-8").read().strip()

REF_IMAGES = multi("--ref")
REF_AUDIOS = multi("--audio")
REF_VIDEOS = multi("--ref-video")
REF_VIDEO_AUDIOS = multi("--ref-video-audio")
LENGTH = int(opt("--length", "73"))
MP = float(opt("--mp", "0.4"))
STEPS = int(opt("--steps", "8"))
PREFIX = opt("--prefix", "video/R2V-TEST")
SEED = int(opt("--seed", "123456789"))
ASPECT = opt("--aspect", "9:16 (Portrait Widescreen)")
REF_SIZE = opt("--ref-size", "match")
LORA = opt("--lora", "minimax_h3_ref2v_turbo_8step_v1.0_768p_comfyui_bf16.safetensors")
LORA_STR = float(opt("--lora-strength", "1.0"))
SAMPLER = opt("--sampler", "res_multistep")
SCHEDULER = opt("--scheduler", "simple")
UNET = opt("--unet", "minimax_h3_ref2va_pruned_int8_convrot.safetensors")
SPARSE_BUDGET = float(opt("--sparse-budget", "0.3"))
SPARSE_OFF = "--sparse-off" in args
MODEL_LINK = ["6", 0] if SPARSE_OFF else ["7", 0]

frames = LENGTH + (5 - (LENGTH % 17)) % 17
if frames < 5:
    frames = 5

g = {
    "1": {"class_type": "UNETLoader", "inputs": {"unet_name": UNET, "weight_dtype": "default"}},
    "2": {"class_type": "CLIPLoader", "inputs": {
        "clip_name": "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors", "type": "minimax", "device": "default"}},
    "3": {"class_type": "VAELoader", "inputs": {"vae_name": "minimax_h3_video_vae_fp16.safetensors"}},
    "4": {"class_type": "VAELoader", "inputs": {"vae_name": "minimax_h3_audio_vae_fp32.safetensors"}},
    "5": {"class_type": "LoraLoaderModelOnly", "inputs": {
        "model": ["1", 0], "lora_name": LORA, "strength_model": LORA_STR}},
    "6": {"class_type": "H3MemoryOptimization", "inputs": {
        "model": ["5", 0], "fused_qkv": "auto", "mlp_memory": "auto", "chunk_rows": 4096,
        "preserve_precision": True, "precision_mode": "Auto", "qkv_streaming_mode": "Auto",
        "embedding_memory_mode": "Auto", "kitchen_v_memory_mode": "Standard"}},
    "7": {"class_type": "H3SparseAttentionAdvanced", "inputs": {
        "model": ["6", 0], "video_budget": SPARSE_BUDGET, "early_steps": 2, "early_kv": 0.5,
        "late_steps": 0, "late_kv": 0.5, "backend": SPARSE_BACKEND,
        "early_schedule": "Ramp", "video_token_order": "1x8x8"}},
    "8": {"class_type": "ResolutionSelector", "inputs": {
        "aspect_ratio": ASPECT, "megapixels": MP, "multiple": 32}},
}

# ---- 参考图：ref_image_0, ref_image_1, ... （0 基命名，顺序即 <Picture i> 的 i）----
ref_map = {}
for i, fn in enumerate(REF_IMAGES):
    nid = str(300 + i)
    g[nid] = {"class_type": "LoadImage", "inputs": {"image": fn}}
    ref_map[f"ref_image_{i}"] = [nid, 0]

# ---- 独立参考音频：ref_audio_0, ... → <Audio j> ----
aud_map = {}
for j, fn in enumerate(REF_AUDIOS):
    nid = str(350 + j)
    g[nid] = {"class_type": "LoadAudio", "inputs": {"audio": fn}}
    aud_map[f"ref_audio_{j}"] = [nid, 0]

# ---- 参考视频：LoadVideo → GetVideoComponents(images) → ref_video_k → <Video k> ----
vid_map = {}
for k, fn in enumerate(REF_VIDEOS):
    lv, gvc = str(400 + 2 * k), str(401 + 2 * k)
    g[lv] = {"class_type": "LoadVideo", "inputs": {"file": fn}}
    g[gvc] = {"class_type": "GetVideoComponents", "inputs": {"video": [lv, 0]}}
    vid_map[f"ref_video_{k}"] = [gvc, 0]          # 输出 0 = images（帧序列）

# ---- 参考视频音轨：ref_video_audio_k（与同序号 ref_video 自动配对）----
vad_map = {}
for k, fn in enumerate(REF_VIDEO_AUDIOS):
    nid = str(450 + k)
    g[nid] = {"class_type": "LoadAudio", "inputs": {"audio": fn}}
    vad_map[f"ref_video_audio_{k}"] = [nid, 0]

node9 = {"class_type": "MiniMaxH3ReferenceToVideo", "inputs": {
    "clip": ["2", 0], "vae": ["3", 0], "audio_vae": ["4", 0], "prompt": PROMPT,
    "width": ["8", 0], "height": ["8", 1], "length": frames,
    "ref_image_size": REF_SIZE}}
if ref_map:
    node9["inputs"]["ref_images"] = ref_map
if aud_map:
    node9["inputs"]["ref_audios"] = aud_map
if vid_map:
    node9["inputs"]["ref_videos"] = vid_map
if vad_map:
    node9["inputs"]["ref_video_audios"] = vad_map
g["9"] = node9

g["10"] = {"class_type": "RandomNoise", "inputs": {"noise_seed": SEED}}
g["12"] = {"class_type": "KSamplerSelect", "inputs": {"sampler_name": SAMPLER}}
g["13"] = {"class_type": "BasicScheduler", "inputs": {
    "model": MODEL_LINK, "scheduler": SCHEDULER, "steps": STEPS, "denoise": 1.0}}
g["11"] = {"class_type": "BasicGuider", "inputs": {"model": MODEL_LINK, "conditioning": ["9", 0]}}
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
print(f"submitted prompt_id={pid} refs={REF_IMAGES} audios={REF_AUDIOS} "
      f"ref_videos={REF_VIDEOS} ref_video_audios={REF_VIDEO_AUDIOS} len={frames} "
      f"mp={MP} steps={STEPS} ref_size={REF_SIZE} lora={LORA} seed={SEED}", flush=True)
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
