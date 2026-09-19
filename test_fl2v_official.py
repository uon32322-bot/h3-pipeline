#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MiniMax H3 FL2VA —— 官方原生链路（只用 ComfyUI 核心节点，零社区依赖）

为什么要有这个脚本（REPORT16 的整改结论）：
  旧 test_fl2v.py 用了 4 个社区包节点，官方原生链路从未跑过：
      MiniMaxH3TurboLoRA        ← custom_nodes/ComfyUI-MiniMax-H3-Turbo
      MiniMaxH3TurboSampler     ← custom_nodes/ComfyUI-MiniMax-H3-Turbo
      H3SparseAttentionAdvanced ← custom_nodes/H3-Optimizations
      H3MemoryOptimization      ← custom_nodes/H3-Optimizations
  本脚本改用官方模板 video_minimax_h3_i2v.json（subgraph 展开）同款节点：
      LoraLoaderModelOnly + KSamplerSelect(res_multistep) + BasicScheduler(simple)

官方链路（连线已按官方 subgraph 的 origin/target slot 还原）：
  UNETLoader ──> LoraLoaderModelOnly ─┬─> BasicGuider ──────┐
  CLIPLoader ─┐                       └─> BasicScheduler ─┐ │
  VAELoader(video) ─┬─> MiniMaxH3ImageToVideo ─┬ positive ─┘ │
  VAELoader(audio) ─┘   ├ first_frame/last_frame└ LATENT ────┴─> SamplerCustomAdvanced
                        └ length(17k+5 帧网格)                     ├─> VAEDecode ──────┐
  RandomNoise(seed) ─────────────────────────────────────────────┘  └─> VAEDecodeAudio ┴─> CreateVideo(24fps) ─> SaveVideo
  KSamplerSelect(res_multistep) ─────────────────────────────────────> (sampler)

⭐ GPU 租约（2026-09-18 接入全局仲裁器）：
  本机是【共享 GPU】（同一张 3090 上还跑着 digital-human / h3-gateway / heygem / tts
  四个服务），机器自带仲裁器 gpu_arbiter.py@127.0.0.1:6200 + 官方客户端 gpu_lock.py
  （/opt/shared/，与 /root/autodl-tmp/relocate/shared 同一份）。
  ⇒ 本脚本【不再自行轮询 nvidia-smi 显存】，改为调 gpu_lock.acquire() 排队拿租约；
     锁逻辑 100% 由现成的 gpu_lock.py 承担，本文件只做参数传递与生命周期管理。

  acquire 是【阻塞】的：前面有任务就排队等，等到才提交 ComfyUI。
  租约覆盖【提交 → 轮询 /history 直到出片】全程，finally 保证释放。

  ⚠️ 硬约束：必须走 arbiter 模式。gpu_lock 在 arbiter 不可达时会【静默回退 flock】，
     而 flock 无心跳 ⇒ 仲裁器会在 STALE_LOCK_TIMEOUT(默认 3600s) 后判死锁并 SIGKILL
     本进程。我们的 40s 五段片约 86min ⇒ 必被杀。所以 mode != "arbiter" 时直接拒绝
     开跑（除非显式 --allow-flock-fallback）。

用法:
  test_fl2v_official.py FIRST LAST PROMPT_FILE SECONDS MP STEPS PREFIX [SEED]
                        [--mid PNG@秒] [--audio WAV@秒] [--port 6011] [--dry]
                        [--no-lease] [--priority N] [--vram-mb N]
                        [--lease-timeout 秒] [--lease-label 名]

示例:
  ./test_fl2v_official.py anc/f1.png anc/f2.png q.txt 2 0.4 8 video/H3OFF 123456789 --port 6011
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid

UNET = "minimax_h3_fl2va_pruned_int8_convrot.safetensors"
CLIP = "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"
VAE_V = "minimax_h3_video_vae_fp16.safetensors"
VAE_A = "minimax_h3_audio_vae_fp32.safetensors"
LORA_8STEP = "minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors"
SAMPLER = "res_multistep"
SCHEDULER = "simple"
# 带货短视频默认竖版 9:16。
# ⚠️ 档位名必须与 ComfyUI `comfy_extras/nodes_resolution.py` 里 `class AspectRatio` 的
#    枚举值【逐字一致】（含括号内容），否则 ResolutionSelector 的 combo 校验会直接拒绝。
ASPECT = "9:16 (Portrait Widescreen)"
# 下方为该枚举的全部 8 个合法档位（键=简写，值=节点真名）
ASPECT_CHOICES = {
    "9:16": "9:16 (Portrait Widescreen)",   # 竖版 · 带货短视频默认
    "16:9": "16:9 (Widescreen)",            # 横版 · 官方基准 1344x768
    "1:1": "1:1 (Square)",
    "4:3": "4:3 (Standard)",
    "3:4": "3:4 (Portrait Standard)",
    "2:3": "2:3 (Portrait Photo)",
    "3:2": "3:2 (Photo)",
    "21:9": "21:9 (Ultrawide)",
}
# 上表对应的比例对（逐条对齐 ComfyUI nodes_resolution.py 的 ASPECT_RATIOS）
ASPECT_PAIRS = {
    "1:1 (Square)": (1, 1),
    "2:3 (Portrait Photo)": (2, 3),
    "3:2 (Photo)": (3, 2),
    "3:4 (Portrait Standard)": (3, 4),
    "4:3 (Standard)": (4, 3),
    "9:16 (Portrait Widescreen)": (9, 16),
    "16:9 (Widescreen)": (16, 9),
    "21:9 (Ultrawide)": (21, 9),
}
MULTIPLE = 32       # 分辨率对齐粒度（H3 要求 32 的倍数）
RATIO_TOL = 0.02    # 首尾帧比例容差（2%）

# ---- 全局 GPU 仲裁器接入 ----
# 租约客户端的实现在同目录 gpu_lease.py；它只做参数翻译 + finally 释放，
# 真正的锁/排队/心跳由机器自带 /opt/shared/gpu_lock.py 承担（与同机
# digital-human / h3-gateway / heygem / tts 四个服务用的是同一套）。
LEASE_PRIORITY = 1      # 与 dh(digital-human) 同为 1 ⇒ 平等 FIFO，不插队也不被饿死
                        # （仲裁器排序键 = (priority, seq)，数值越小越优先）
LEASE_VRAM_MB = 21000   # 名义占用；≤ 24576-2048=22528，否则 /acquire 直接拒绝
LEASE_TIMEOUT = 7200.0  # 排队最长等 2h（40s 五段约 86min，留足余量）
LEASE_LABEL = "h3p:gen"  # label 须为 "<stage>:<detail>"，stage = 冒号前部分
FREE_COMFY_PATH = "/free"


def resolve_px(aspect, mp, multiple=MULTIPLE):
    """复刻 ComfyUI ResolutionSelector.execute() 的算法（本地预判画布尺寸）。
    已真机反证：16:9@0.98MP → 1344x768（=官方基准）；9:16@0.98MP → 768x1344。"""
    w_r, h_r = ASPECT_PAIRS[aspect]
    scale = (mp * 1024 * 1024 / (w_r * h_r)) ** 0.5
    return (round(w_r * scale / multiple) * multiple,
            round(h_r * scale / multiple) * multiple)


def frame_dev(path, tw, th):
    """首尾帧与画布的比例相对偏差。
    ⚠️ 实测 H3 源码 nodes_minimax_h3.py：first_frame 走 plain stretch（直接拉伸）、
       last_frame 走 aspect-preserving cover-crop（保比例中心裁剪）
       ⇒ 比例不符 = 首帧变形 / 尾帧丢构图，且不报错（静默毁片）。"""
    from PIL import Image
    w, h = Image.open(path).size
    return w, h, abs(w / h - tw / th) / (tw / th)


def snap_frames(seconds):
    """官方公式：max(5, round(a*24)) + (5 - (max(5, round(a*24)) % 17)) % 17"""
    f = int(max(5, round(seconds * 24)))
    return f + (5 - (f % 17)) % 17


def build_graph(first, last, prompt, seconds, mp, steps, prefix, seed, mid=None, audio=None,
                lora=LORA_8STEP, aspect=ASPECT):
    g = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": UNET, "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": CLIP, "type": "minimax", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": VAE_V}},
        "4": {"class_type": "VAELoader", "inputs": {"vae_name": VAE_A}},
        "5": {"class_type": "LoraLoaderModelOnly", "inputs": {
            "model": ["1", 0], "lora_name": lora, "strength_model": 1.0}},
        "6": {"class_type": "ResolutionSelector", "inputs": {
            "aspect_ratio": aspect, "megapixels": mp, "multiple": 32}},
        "7": {"class_type": "MiniMaxH3ImageToVideo", "inputs": {
            "clip": ["2", 0], "vae": ["3", 0], "prompt": prompt,
            "width": ["6", 0], "height": ["6", 1], "length": snap_frames(seconds),
            "first_frame": ["8", 0], "last_frame": ["9", 0]}},
        "8": {"class_type": "LoadImage", "inputs": {"image": first}},
        "9": {"class_type": "LoadImage", "inputs": {"image": last}},
        "10": {"class_type": "RandomNoise", "inputs": {"noise_seed": seed}},
        "11": {"class_type": "BasicGuider", "inputs": {"model": ["5", 0], "conditioning": ["7", 0]}},
        "12": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": SAMPLER}},
        "13": {"class_type": "BasicScheduler", "inputs": {
            "model": ["5", 0], "scheduler": SCHEDULER, "steps": steps, "denoise": 1.0}},
        "14": {"class_type": "SamplerCustomAdvanced", "inputs": {
            "noise": ["10", 0], "guider": ["11", 0], "sampler": ["12", 0],
            "sigmas": ["13", 0], "latent_image": ["7", 1]}},
        "15": {"class_type": "VAEDecode", "inputs": {"samples": ["14", 0], "vae": ["3", 0]}},
        "16": {"class_type": "VAEDecodeAudio", "inputs": {"samples": ["14", 0], "vae": ["4", 0]}},
        "17": {"class_type": "CreateVideo", "inputs": {"images": ["15", 0], "fps": 24.0, "audio": ["16", 0]}},
        "18": {"class_type": "SaveVideo", "inputs": {
            "video": ["17", 0], "filename_prefix": prefix, "format": "auto", "codec": "auto"}},
    }
    # ---- 锚点链：AddGuide 插在 MiniMaxH3ImageToVideo 与 BasicGuider 之间 ----
    # ⚠️ 多个锚点必须【串联】而不是并联：每个 AddGuide 都是往 positive 上 append 一个
    #    keyframe（`keyframes = list(positive[0][1].get("minimax_keyframes", []))` + append），
    #    并联的话后一个会从旧 positive 出发 ⇒ 前一个的 keyframe 被丢掉。
    # 🔴 2026-09-20 修复：mid/audio 改为**支持多个**。原实现只有单个 mid，
    #    而调用方（orchestrator）会循环追加多对 `--mid`，argparse 后值覆盖前值
    #    ⇒ 中间锚点被静默丢弃（锚点丢失直接造成动作/主体漂移，日志完全看不出）。
    #    兼容旧调用：传单个 tuple 时自动包成列表。
    if mid and not isinstance(mid, list):
        mid = [mid]
    if audio and not isinstance(audio, list):
        audio = [audio]
    tail = ["7", 0]
    _nid = 20
    for _m in (mid or []):                     # 画面锚点（可多个，必须【串联】）
        _img, _sec = _m
        _nid += 1; _lid = str(_nid)
        _nid += 1; _gid = str(_nid)
        g[_lid] = {"class_type": "LoadImage", "inputs": {"image": _img}}
        g[_gid] = {"class_type": "MiniMaxH3AddGuide", "inputs": {
            "positive": tail, "latent": ["7", 1],
            "frame_idx": int(round(_sec * 24)), "vae": ["3", 0], "image": [_lid, 0]}}
        tail = [_gid, 0]
    for _a in (audio or []):                   # 语音锚点（L 口型同步，可多个）
        _wav, _sec = _a
        _nid += 1; _lid = str(_nid)
        _nid += 1; _gid = str(_nid)
        g[_lid] = {"class_type": "LoadAudio", "inputs": {"audio": _wav}}
        g[_gid] = {"class_type": "MiniMaxH3AddGuide", "inputs": {
            "positive": tail, "latent": ["7", 1],
            "frame_idx": int(round(_sec * 24)), "audio_vae": ["4", 0], "audio": [_lid, 0]}}
        tail = [_gid, 0]
    if tail != ["7", 0]:
        g["11"]["inputs"]["conditioning"] = tail
    return g


def _import_gpu_lease():
    """导入同目录的 gpu_lease.py（租约客户端）。"""
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    try:
        import gpu_lease
    except ImportError as e:
        raise RuntimeError(
            "找不到同目录的 gpu_lease.py（%s）: %s\n"
            "   ⇒ 共享 GPU 上必须接入全局仲裁器；请确认 gpu_lease.py 与 gpu_lock.py 就位。"
            % (here, e))
    return gpu_lease


def free_comfy_cached(host):
    """调 ComfyUI 的 /free 释放已加载模型缓存，把显存让给后续任务。

    本机既定公约（dh 的 h3_pipeline_worker 每次出片后都调它清自己的 6006）：
    出片后主动释放，别让常驻模型白占显存。⚠️ 代价：下次生成要重新 load 模型。
    """
    try:
        post(host, FREE_COMFY_PATH, {"unload_models": True, "free_memory": True})
        print("[lease] 已通知 %s 释放模型缓存" % host)
    except Exception as e:
        print("[lease] ⚠️ /free 调用失败（不影响出片）: %s" % e)


def acquire_gpu_lease(host, priority, vram_mb, timeout, label, allow_flock=False):
    """向全局仲裁器申请 GPU 租约（阻塞排队）。

    ⚠️ 本函数不轮询显存、不自行加锁 —— 全部委托给同目录 gpu_lease.py，
       而 gpu_lease.py 又只调机器自带的 /opt/shared/gpu_lock.py。
    """
    gpu_lease = _import_gpu_lease()
    return gpu_lease.acquire_lease(priority=priority, vram_mb=vram_mb, timeout=timeout,
                                   label=label, allow_flock=allow_flock)


def release_gpu_lease(lease, host=None, free_comfy=False):
    """释放租约（幂等；进程被 SIGKILL 时仲裁器会在 LEASE_TTL 120s 后自动回收）。"""
    if lease is None:
        return
    if free_comfy and host:
        free_comfy_cached(host)
    _import_gpu_lease().release_lease(lease)


def post(host, path, payload):
    req = urllib.request.Request(host + path, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def get(host, path, timeout=60):
    with urllib.request.urlopen(host + path, timeout=timeout) as r:
        return json.loads(r.read().decode())


def main():
    # 日志常被 `>> "$LOG"` 重定向 ⇒ Python 会切成块缓冲，排队时看不到进度。改行缓冲。
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(line_buffering=True)
        except Exception:
            pass
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("first"); ap.add_argument("last"); ap.add_argument("prompt_file")
    ap.add_argument("seconds", nargs="?", type=float, default=5.0)
    ap.add_argument("mp", nargs="?", type=float, default=0.4)
    ap.add_argument("steps", nargs="?", type=int, default=8)
    ap.add_argument("prefix", nargs="?", default="video/H3-OFFICIAL")
    ap.add_argument("seed", nargs="?", type=int, default=123456789)
    ap.add_argument("--mid", action="append", default=None,
                    help="中间锚点，格式 PNG@秒，如 anc/m.png@1.5（可重复；多个必须串联见 build_graph 注释）")
    ap.add_argument("--audio", action="append", default=None,
                    help="⭐ 语音锚点（L 口型同步模式）：音轨锚在指定帧，格式 WAV@秒，"
                         "如 vo/s4.wav@0。音频放 ComfyUI 的 input/ 目录（LoadAudio 按文件名取）。"
                         "每去噪步重注入 audio_latent ⇒ 音轨内容保留、画面口型被牵引对齐该音频。")
    ap.add_argument("--port", type=int, default=6011)
    ap.add_argument("--host", default="http://127.0.0.1")
    ap.add_argument("--lora", default=LORA_8STEP)
    ap.add_argument("--aspect", default="9:16", choices=list(ASPECT_CHOICES),
                    help="宽高比档位（默认 9:16 竖版，带货短视频用）")
    ap.add_argument("--allow-ratio-mismatch", action="store_true",
                    help="跳过首尾帧比例守卫（默认拦截：比例不符会静默造成首帧拉伸/尾帧裁切）")
    # ---- 全局 GPU 租约（默认开启，替代自写 nvidia-smi 轮询）----
    ap.add_argument("--no-lease", action="store_true",
                    help="不申请 GPU 租约直接提交（仅限离线/单机调试；共享机上会与别的项目抢显存）")
    ap.add_argument("--priority", type=int, default=LEASE_PRIORITY,
                    help="租约优先级，数值【越小越优先】，默认 %d（与 dh 平等，不插队）。"
                         "0 会插到 dh 前面，2 会永远排在 dh 之后" % LEASE_PRIORITY)
    ap.add_argument("--vram-mb", type=int, default=LEASE_VRAM_MB,
                    help="名义显存占用 MB，默认 %d（上限 22528）" % LEASE_VRAM_MB)
    ap.add_argument("--lease-timeout", type=float, default=LEASE_TIMEOUT,
                    help="排队最长等待秒数，默认 %d" % int(LEASE_TIMEOUT))
    ap.add_argument("--lease-label", default=LEASE_LABEL,
                    help="租约标签，格式 <stage>:<detail>，默认 %s（会显示在 /status 里）" % LEASE_LABEL)
    ap.add_argument("--allow-flock-fallback", action="store_true",
                    help="允许在 arbiter 不可达时回退 flock（危险：无心跳，>3600s 被杀）")
    ap.add_argument("--free-comfy-after", action="store_true",
                    help="出片后调 ComfyUI /free 释放模型缓存让显存给别的项目（下次生成需重新 load）")
    ap.add_argument("--dry", action="store_true", help="只打印图，不提交")
    a = ap.parse_args()

    prompt = open(a.prompt_file, encoding="utf-8").read().strip()
    # 支持多个锚点（--mid 可重复）。原实现只取单个 ⇒ 调用方循环追加时被覆盖丢失。
    def _parse_anchors(items):
        out = []
        for _it in (items or []):
            _p, _, _sec = str(_it).partition("@")
            out.append((_p, float(_sec or 0)))
        return out or None
    mid = _parse_anchors(a.mid)
    audio = _parse_anchors(a.audio)
    if mid:
        print("画面锚点 %d 个：" % len(mid),
              ", ".join("%s@%ss" % (m[0].split("/")[-1], m[1]) for m in mid))
    if audio:
        print("语音锚点 %d 个：" % len(audio),
              ", ".join("%s@%ss" % (x[0].split("/")[-1], x[1]) for x in audio))

    # ---- 首尾帧比例守卫（防御前移：把会「静默毁片」的比例错误挡在 GPU 之外）----
    asp = ASPECT_CHOICES[a.aspect]
    tw, th = resolve_px(asp, a.mp, MULTIPLE)
    checked, bad = 0, []
    for tag, p, mode in (("首帧", a.first, "plain stretch 整体拉伸变形"),
                         ("尾帧", a.last, "cover-crop 中心裁切（丢边缘构图）")):
        if not os.path.exists(p):
            continue
        checked += 1
        w, h, dev = frame_dev(p, tw, th)
        if dev > RATIO_TOL:
            bad.append((tag, p, w, h, dev, mode))
    if bad and not a.allow_ratio_mismatch:
        print("⛔ 首尾帧比例守卫拦截（画布 %dx%d，比例 %.4f）" % (tw, th, tw / th))
        for tag, p, w, h, dev, mode in bad:
            print("   %s %s = %dx%d（比例 %.4f，偏差 %.2f%%）→ 会被 %s"
                  % (tag, os.path.basename(p), w, h, w / h, dev * 100, mode))
        print("   ⇒ 图片层请按 %dx%d 出图（TT Image2 的 size 直接传 \"%dx%d\"）" % (tw, th, tw, th))
        print("   ⇒ 确要强行提交：加 --allow-ratio-mismatch")
        return 2
    if bad:
        if a.allow_ratio_mismatch:
            print("⚠️ 比例守卫已被 --allow-ratio-mismatch 跳过：%d 张比例不符，GPU 侧将发生拉伸/裁切"
                  % len(bad))
    elif checked:
        print("✅ 首尾帧比例守卫通过（画布 %dx%d，已检 %d 张）" % (tw, th, checked))

    graph = build_graph(a.first, a.last, prompt, a.seconds, a.mp, a.steps, a.prefix, a.seed, mid, audio,
                        a.lora, aspect=asp)

    if a.dry:
        print(json.dumps(graph, ensure_ascii=False, indent=2))
        return 0

    host = "%s:%d" % (a.host, a.port)

    # ================= 全局 GPU 租约（接入 arbiter，非自写轮询）=================
    lease = None
    if a.no_lease:
        print("⚠️ --no-lease：未申请 GPU 租约。共享机上会与 dh/h3-gateway 抢显存，"
              "仅在确认卡空闲时使用")
    else:
        try:
            lease = acquire_gpu_lease(host, a.priority, a.vram_mb, a.lease_timeout,
                                      a.lease_label, a.allow_flock_fallback)
        except Exception as e:
            print(str(e))
            return 3
    # 租约覆盖【提交 → 轮询至出片】全程；finally 保证任何路径（含 Ctrl-C / 异常）都释放
    try:
        cid = str(uuid.uuid4())
        t0 = time.time()
        try:
            res = post(host, "/prompt", {"prompt": graph, "client_id": cid})
        except urllib.error.HTTPError as e:
            print("SUBMIT FAILED", e.code)
            print(e.read().decode()[:4000])
            return 1
        pid = res.get("prompt_id")
        print("prompt_id =", pid, "| frames =", snap_frames(a.seconds), "| steps =", a.steps,
              "| mp =", a.mp, "| seed =", a.seed, "| lora =", os.path.basename(a.lora))

        # 🔴 2026-09-20 修复：原 `while True` 无总超时 ⇒ ComfyUI 卡队列/节点 hang 时
        #    本进程会一直占着 GPU 租约空转（租约只在进程退出时释放）；且无论
        #    status_str 是 success 还是 error 都 return 0 ⇒ 上游只能看到
        #    「执行完成但未找到产物」，排障信息被抹掉。
        _deadline = time.time() + float(os.environ.get("H3_POLL_TIMEOUT_S", "3600"))
        while True:
            if time.time() > _deadline:
                print("⛔ 轮询超时 %.0fs（H3_POLL_TIMEOUT_S）—— ComfyUI 可能卡队列或节点 hang"
                      % (time.time() - t0))
                return 4
            time.sleep(5)
            try:
                h = get(host, "/history/" + pid)
            except Exception as e:
                print("  poll err", e); continue
            if pid in h:
                entry = h[pid]
                _st = entry.get("status", {}).get("status_str")
                print("status =", _st)
                for nid, out in (entry.get("outputs") or {}).items():
                    for key in ("videos", "gifs", "images"):
                        for item in (out.get(key) or []):
                            print("OUTPUT", key, item.get("subfolder", ""), item.get("filename"))
                print("elapsed = %.1fs" % (time.time() - t0))
                if _st != "success":
                    print("⛔ ComfyUI 执行未成功（status_str=%s），打印错误明细并返回 5：" % _st)
                    for _m in (entry.get("status", {}).get("messages") or [])[-6:]:
                        print("  comfy:", str(_m)[:500])
                    return 5
                return 0
            print("  ...running %.0fs" % (time.time() - t0))
    finally:
        release_gpu_lease(lease, host, a.free_comfy_after)


if __name__ == "__main__":
    sys.exit(main())
