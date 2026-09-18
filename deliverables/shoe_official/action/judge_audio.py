#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""judge_audio.py —— 判官团【A 段：音频层】执行器（全部本地 CPU，零 GPU）

⭐ 设计纪律（三条，来自本项目实测教训）
  1. 能用算术判的，不要问 VLM ⇒ A 段**一项都不进 L2**，全跑本地 CPU。
  2. **ASR 必须 CPU int8** —— faster-whisper 在 4090 上会 OOM（REPORT5 §6.3 实测）。
     ⇒ GPU 隔离红线延伸：**TTS 与 ASR 都不得占 3090 的 CUDA**。
  3. **新增判项必须两头标定才能上线**（R-08 的教训：未标定的判据会误杀好片）。
     ⇒ 未标定项输出带 "uncalibrated": true，**默认只报数值、不参与否决**；
        确实要用它否决，加 --enforce-uncalibrated（不建议）。

判项
  A-01 音轨存在且非静音        纯算术      > -50 dB                ✅ 已标定（复用 L0-05 口径）
  A-02 口播内容与文案逐字一致  ASR+相似度  ≥ 0.90（容同音误差）    🆕 需标定
  A-03 口型与语音同步          互相关      r ≥ 0.30 且滞后 ≤ 2 帧   🆕 需标定；仅 L 镜
  A-04 语音在段内结束          能量包络    尾部空闲 ≤ 1.0 s        ✅ 纯算术
  A-05 跨段音色一致            F0 中位数   相邻段 |ΔF0| ≤ 15%      🆕 需标定；多段才启用

用法
  # 单段（L 镜：带口播，需查口型同步）
  judge_audio.py seg1.mp4 --mode L --vo "跑了三年步，膝盖最先抗议的，是不是你？" \
                          --manifest vo/track_manifest.json --out judge/A_seg1.json
  # 单段（V 镜：画外音，口型不适用 ⇒ A-03 na）
  judge_audio.py seg2.mp4 --mode V --vo "跑完膝盖发酸…" --out judge/A_seg2.json
  # 多段（查 A-05 跨段音色一致）
  judge_audio.py --segments G1.mp4,G2.mp4,G3.mp4 --mode V --out judge/A_all.json
  # 标定模式（只出数值，不出判定）
  judge_audio.py seg1.mp4 --mode L --calib
"""
import sys, os, json, subprocess, argparse, math, re, time
import difflib

try:
    import numpy as np
except ImportError:
    sys.exit("⛔ 需要 numpy：/Users/admin/.workbuddy/binaries/python/envs/default/bin/pip install numpy")

FFMPEG = os.environ.get("FFMPEG", "ffmpeg")
FFPROBE = os.environ.get("FFPROBE", "ffprobe")
SR = 24000          # 音频分析采样率（音画同步用）
FPS_DEFAULT = 24.0

# ---- 阈值（未标定项集中在这里，标定后回来改这一个字典）----
TH = {
    "A-01": {"min_db": -50.0, "max_silence_ratio": 0.95},
    "A-02": {"min_similarity": 0.90},
    "A-03": {"min_r": 0.30, "max_lag_frames": 2},
    "A-04": {"max_tail_idle_s": 1.0},
    "A-05": {"max_f0_rel_dev": 0.15},
}
CALIBRATED = {"A-01": True, "A-02": False, "A-03": False, "A-04": True, "A-05": False}


# ---------------------------------------------------------------- 基础工具
def _run(cmd):
    return subprocess.run(cmd, capture_output=True).stdout


def probe(path, entries="stream=codec_type,width,height,r_frame_rate,duration"):
    out = _run([FFPROBE, "-v", "error", "-show_entries", entries,
                "-of", "json", path]).decode("utf-8", "ignore")
    try:
        return json.loads(out)
    except Exception:
        return {}


def has_audio(path):
    info = probe(path, "stream=codec_type")
    return any(s.get("codec_type") == "audio" for s in info.get("streams", []))


def video_duration(path):
    info = probe(path, "format=duration")
    try:
        return float(info["format"]["duration"])
    except Exception:
        return 0.0


def load_audio_mono(path, sr=SR):
    """解码成单声道 float32 [-1,1]。无音轨返回空数组。"""
    if not has_audio(path):
        return np.zeros(0, np.float32)
    raw = _run([FFMPEG, "-v", "error", "-i", path, "-ac", "1", "-ar", str(sr),
                "-f", "s16le", "-"])
    a = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    return a


def envelope(a, sr, hop_ms=10.0):
    """RMS 包络（每 hop_ms 一格）。"""
    st = max(1, int(sr * hop_ms / 1000.0))
    n = len(a) // st
    if n == 0:
        return np.zeros(0, np.float32)
    return np.sqrt((a[:n * st].reshape(n, st) ** 2).mean(axis=1))


def db(x):
    return 20.0 * math.log10(max(float(x), 1e-9))


def _grab_frames(path, crop, w_out=0):
    """抓灰度帧到内存（用于 A-03）。crop=(x,y,w,h)，None=全帧。"""
    vf = []
    if crop:
        vf.append("crop=%d:%d:%d:%d" % crop)
    if w_out:
        vf.append("scale=%d:-1" % w_out)
    vf.append("format=gray")
    raw = _run([FFMPEG, "-v", "error", "-i", path, "-vf", ",".join(vf),
                "-f", "rawvideo", "-pix_fmt", "gray", "-"])
    info = probe(path, "stream=width,height")
    W = H = None
    for s in info.get("streams", []):
        if s.get("codec_type") == "video":
            W, H = s.get("width"), s.get("height")
    if not W:
        return np.zeros((0, 1, 1), np.float32), 0, 0
    if crop:
        W, H = crop[2], crop[3]
    if w_out:
        H = int(round(H * w_out / float(W)))
        W = w_out
    n = len(raw) // (W * H)
    f = np.frombuffer(raw[:n * W * H], dtype=np.uint8).reshape(n, H, W).astype(np.float32)
    return f, W, H


# ---------------------------------------------------------------- A-01 / A-04
def a01(a):
    if len(a) == 0:
        return {"verdict": "no", "value": None, "threshold": TH["A-01"]["min_db"],
                "note": "无音轨（解码为空）"}
    rms = math.sqrt(float((a.astype(np.float64) ** 2).mean()))
    d = db(rms)
    ok = d > TH["A-01"]["min_db"]
    return {"verdict": "yes" if ok else "no", "value": round(d, 2),
            "threshold": TH["A-01"]["min_db"], "note": "整体电平 %.1f dBFS" % d}


def a04(a, dur_s, manifest=None, seg_index=None):
    """语音在段内结束：尾部空闲 ≤ 1.0s；有 manifest 则校验句末不越界。"""
    if len(a) == 0:
        return {"verdict": "na", "value": None, "threshold": TH["A-04"]["max_tail_idle_s"],
                "note": "无音轨 ⇒ 不适用"}
    env = envelope(a, SR, 10.0)
    if len(env) == 0 or env.max() <= 0:
        return {"verdict": "na", "value": None, "threshold": TH["A-04"]["max_tail_idle_s"],
                "note": "全静音 ⇒ 无语音可判"}
    act = np.where(env > env.max() * 0.06)[0]
    t_end = act[-1] * 0.01 if len(act) else 0.0
    tail = max(0.0, dur_s - t_end)
    ok = tail <= TH["A-04"]["max_tail_idle_s"]
    note = "语音止于 %.2fs，尾部空闲 %.2fs" % (t_end, tail)
    # manifest 交叉校验：本段最后一句的句末必须落在段内
    if manifest and seg_index is not None:
        try:
            seg = manifest["segments"][seg_index]
            last_end = max(s["end"] for s in seg.get("sentences", [])) if seg.get("sentences") else None
            if last_end is not None and last_end > dur_s + 0.15:
                ok = False
                note += "；⚠️ 末句句末 %.2fs 超出段长 ⇒ 语音被截断" % last_end
        except Exception:
            pass
    return {"verdict": "yes" if ok else "no", "value": round(tail, 2),
            "threshold": TH["A-04"]["max_tail_idle_s"], "note": note}


# ---------------------------------------------------------------- A-02
def _norm_text(s):
    s = re.sub(r"[\s，。、！？,.!?；;：:“”\"'（）()【】\[\]—–-]", "", s or "")
    return s


def a02(a, tmp_wav, vo_text):
    """口播内容与文案逐字一致（ASR）。faster-whisper 不可用 ⇒ na + 说明。"""
    if not vo_text:
        return {"verdict": "na", "value": None, "threshold": TH["A-02"]["min_similarity"],
                "note": "未提供 --vo 文案 ⇒ 不适用"}
    if len(a) == 0:
        return {"verdict": "no", "value": None, "threshold": TH["A-02"]["min_similarity"],
                "note": "无音轨"}
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        return {"verdict": "na", "value": None, "threshold": TH["A-02"]["min_similarity"],
                "note": "faster-whisper 未安装 ⇒ 无法转写。装法（务必 CPU）；"
                        "pip install faster-whisper（模型 compute_type='int8'，device='cpu'）"}
    # 写 16k 单声道 wav 供 ASR
    _run([FFMPEG, "-v", "error", "-y", "-i", tmp_wav, "-ac", "1", "-ar", "16000", tmp_wav])
    try:
        model = WhisperModel("small", device="cpu", compute_type="int8")   # ⛔ 必须 CPU
        segs, _ = model.transcribe(tmp_wav, language="zh", beam_size=1)
        heard = "".join(s.text for s in segs)
    except Exception as e:
        return {"verdict": "na", "value": None, "threshold": TH["A-02"]["min_similarity"],
                "note": "ASR 失败：%s" % e}
    h, r = _norm_text(heard), _norm_text(vo_text)
    sim = difflib.SequenceMatcher(None, h, r).ratio()
    ok = sim >= TH["A-02"]["min_similarity"]
    return {"verdict": "yes" if ok else "no", "value": round(sim, 3),
            "threshold": TH["A-02"]["min_similarity"],
            "note": "ASR 听到：%s" % (heard.strip()[:120] or "（空）")}


# ---------------------------------------------------------------- A-03
def a03(path, mode, face_ratio=(0.28, 0.09, 0.46, 0.32), fps=FPS_DEFAULT):
    """口型-语音同步：脸区搜索 + 互相关。仅 L 镜适用（V/N ⇒ na）。"""
    if mode != "L":
        return {"verdict": "na", "value": None, "threshold": TH["A-03"]["min_r"],
                "note": "音频模式=%s（非 L 口型同步）⇒ 不适用" % mode}
    if not has_audio(path):
        return {"verdict": "no", "value": None, "threshold": TH["A-03"]["min_r"], "note": "无音轨"}
    info = probe(path, "stream=width,height")
    W = H = 0
    for s in info.get("streams", []):
        if s.get("codec_type") == "video":
            W, H = int(s.get("width") or 0), int(s.get("height") or 0)
    if not W:
        return {"verdict": "na", "value": None, "threshold": TH["A-03"]["min_r"], "note": "无视频流"}
    fx, fy = int(face_ratio[0] * W), int(face_ratio[1] * H)
    fw, fh = int(face_ratio[2] * W), int(face_ratio[3] * H)
    f, FW, FH = _grab_frames(path, (fx, fy, fw, fh))
    if f.shape[0] < 6:
        return {"verdict": "na", "value": None, "threshold": TH["A-03"]["min_r"], "note": "帧数不足"}
    # 2x2 均值下采样抑制编码噪声
    h2, w2 = FH // 2 * 2, FW // 2 * 2
    f = f[:, :h2, :w2].reshape(f.shape[0], h2 // 2, 2, w2 // 2, 2).mean(axis=(2, 4))
    env = envelope(load_audio_mono(path), SR, 1000.0 / fps)
    n = min(f.shape[0] - 1, len(env))
    if n < 8:
        return {"verdict": "na", "value": None, "threshold": TH["A-03"]["min_r"], "note": "可用帧不足"}
    fr, env = f[:n + 1], env[:n]
    D = np.abs(np.diff(fr, axis=0))
    cs = np.zeros((n, D.shape[1] + 1, D.shape[2] + 1), np.float64)
    cs[:, 1:, 1:] = D.cumsum(axis=1).cumsum(axis=2)

    def win(x, y, w, h):
        return (cs[:, y + h, x + w] - cs[:, y, x + w] - cs[:, y + h, x] + cs[:, y, x]) / (w * h)

    zc = lambda v: (v - v.mean()) / (v.std() + 1e-9)
    ze = zc(env)
    thr = env.max() * 0.15
    act = np.where(env > thr)[0]
    lo, hi = (int(act[0]), int(act[-1]) + 1) if len(act) > 4 else (0, n)
    seg = slice(lo, hi)
    H2, W2 = fr.shape[1], fr.shape[2]
    step = 2 if n <= 600 else 3
    best = []
    for (ww, hh) in [(int(0.10 * FW), int(0.04 * FH)), (int(0.14 * FW), int(0.05 * FH)),
                     (int(0.18 * FW), int(0.06 * FH))]:
        WW, HH = ww // 2, hh // 2
        if WW < 2 or HH < 2 or WW >= W2 or HH >= H2:
            continue
        for y in range(0, H2 - HH, step):
            for x in range(0, W2 - WW, step):
                s = win(x, y, WW, HH)[seg]
                if s.std() < 1e-6:
                    continue
                r = float(np.corrcoef(zc(s), ze[seg])[0, 1])
                if not math.isnan(r):
                    best.append((r, x, y, WW, HH))
    if not best:
        return {"verdict": "na", "value": None, "threshold": TH["A-03"]["min_r"],
                "note": "脸区无有效运动信号"}
    best.sort(reverse=True)
    r, x, y, WW, HH = best[0]
    s = win(x, y, WW, HH)
    maxlag = max(2, int(0.5 * fps))
    rr = [float(np.dot(zc(s[:n - k]), zc(env[k:])) / (n - k)) for k in range(1, maxlag + 1)]
    kb = int(np.argmax(rr)) + 1
    rpeak = rr[kb - 1]
    ok = (rpeak >= TH["A-03"]["min_r"]) and (kb <= TH["A-03"]["max_lag_frames"])
    return {"verdict": "yes" if ok else "no", "value": round(rpeak, 3),
            "threshold": TH["A-03"]["min_r"], "lag_frames": kb,
            "region": "嘴部候选 @原图 x=%d y=%d w=%d h=%d" % (fx + x * 2, fy + y * 2, WW * 2, HH * 2),
            "note": "峰值 r=%.3f @ 滞后 %d 帧；音频活跃段 %.2f–%.2fs"
                    % (rpeak, kb, lo / fps, hi / fps)}


# ---------------------------------------------------------------- A-05
def _f0_median(a, sr=SR):
    """自相关法估 F0 中位数（只取有声帧）。零依赖 numpy。"""
    N, HOP = 1024, 512
    n = (len(a) - N) // HOP
    if n < 4:
        return None, None
    idx = np.arange(N)[None, :] + HOP * np.arange(n)[:, None]
    fr = a[idx]
    e = np.sqrt((fr ** 2).mean(axis=1))
    keep = e > e.max() * 0.25
    if keep.sum() < 3:
        return None, None
    fr = fr[keep] * np.hanning(N)
    lo, hi = int(sr / 400), int(sr / 70)          # 70–400 Hz
    f0s = []
    for x in fr:
        x = x - x.mean()
        ac = np.correlate(x, x, "full")[N - 1:]
        if ac[0] <= 0:
            continue
        seg = ac[lo:hi]
        if len(seg) == 0:
            continue
        p = int(np.argmax(seg)) + lo
        if ac[p] / ac[0] > 0.3:                    # 周期性够强才算有声
            f0s.append(sr / p)
    if len(f0s) < 3:
        return None, None
    return float(np.median(f0s)), float(np.std(f0s) / (np.median(f0s) + 1e-9))


def a05(paths):
    res = []
    for p in paths:
        f0, jit = _f0_median(load_audio_mono(p))
        res.append({"file": os.path.basename(p), "f0": None if f0 is None else round(f0, 1),
                    "jit": None if jit is None else round(jit, 3)})
    got = [r for r in res if r["f0"]]
    if len(got) < 2:
        return {"verdict": "na", "value": None, "threshold": TH["A-05"]["max_f0_rel_dev"],
                "per_segment": res, "note": "有声段不足 2 个 ⇒ 不适用"}
    devs = []
    for i in range(len(got) - 1):
        a_, b_ = got[i]["f0"], got[i + 1]["f0"]
        devs.append(abs(a_ - b_) / ((a_ + b_) / 2.0))
    mx = max(devs)
    ok = mx <= TH["A-05"]["max_f0_rel_dev"]
    return {"verdict": "yes" if ok else "no", "value": round(mx, 3),
            "threshold": TH["A-05"]["max_f0_rel_dev"], "per_segment": res,
            "note": "相邻段 F0 最大相对偏差 %.1f%%（阈值 %.0f%%）"
                    % (mx * 100, TH["A-05"]["max_f0_rel_dev"] * 100)}


# ---------------------------------------------------------------- 主流程
def main():
    ap = argparse.ArgumentParser(description="判官团 A 段（音频层）· 本地 CPU · 零 GPU")
    ap.add_argument("video", nargs="?", help="单个视频/音频文件")
    ap.add_argument("--segments", default=None, help="多段（查 A-05），逗号分隔")
    ap.add_argument("--mode", default="V", choices=["L", "V", "N"],
                    help="音频模式：L 口型同步（启用 A-03）｜V 画外音（A-03 na）｜N 纯环境音")
    ap.add_argument("--vo", default=None, help="本段口播文案（A-02 用）")
    ap.add_argument("--vo-file", default=None, help="口播文案文件（一行一句）")
    ap.add_argument("--manifest", default=None, help="vo/track_manifest.json（A-04 交叉校验）")
    ap.add_argument("--seg-index", type=int, default=None, help="本段在 manifest 里的下标（0 基）")
    ap.add_argument("--face", default=None, help="脸区比例 x,y,w,h（0-1），默认 0.28,0.09,0.46,0.32")
    ap.add_argument("--fps", type=float, default=FPS_DEFAULT)
    ap.add_argument("--calib", action="store_true", help="标定模式：只出数值，不出判定")
    ap.add_argument("--enforce-uncalibrated", action="store_true",
                    help="⚠️ 让未标定项也参与否决（不建议，R-08 教训）")
    ap.add_argument("--out", default=None, help="报告 JSON 输出路径")
    a = ap.parse_args()

    paths = [a.video] if a.video else []
    if a.segments:
        paths = [p.strip() for p in a.segments.split(",") if p.strip()]
    if not paths:
        sys.exit("⛔ 需要 video 或 --segments")

    vo = a.vo
    if a.vo_file and os.path.exists(a.vo_file):
        vo = " ".join(l.strip() for l in open(a.vo_file, encoding="utf-8") if l.strip())

    face = tuple(float(x) for x in a.face.split(",")) if a.face else (0.28, 0.09, 0.46, 0.32)
    manifest = json.load(open(a.manifest, encoding="utf-8")) if a.manifest and os.path.exists(a.manifest) else None

    print("=" * 74)
    print("[A 段] 音频判官 · 本地 CPU（零 GPU）｜模式=%s｜文件=%s" % (a.mode, ", ".join(os.path.basename(p) for p in paths)))
    print("=" * 74)
    t0 = time.time()
    items = []

    # ---- 单段项 ----
    p0 = paths[0]
    if os.path.exists(p0):
        aud = load_audio_mono(p0)
        dur = video_duration(p0)
        r = a01(aud); r["id"] = "A-01"; items.append(r)
        r = a04(aud, dur, manifest, a.seg_index); r["id"] = "A-04"; items.append(r)
        r = a02(aud, p0, vo); r["id"] = "A-02"; items.append(r)
        r = a03(p0, a.mode, face, a.fps); r["id"] = "A-03"; items.append(r)
    # ---- 跨段项 ----
    if len(paths) > 1:
        r = a05(paths); r["id"] = "A-05"; items.append(r)
    elif a.mode in ("L", "V"):
        items.append({"id": "A-05", "verdict": "na", "value": None,
                      "threshold": TH["A-05"]["max_f0_rel_dev"],
                      "note": "单段 ⇒ 无跨段可比（多段时用 --segments 启用）"})

    # ---- 汇总 ----
    for it in items:
        it["uncalibrated"] = not CALIBRATED.get(it["id"], False)
        if a.calib:
            it["verdict_calib_only"] = True
    print()
    print("%-6s %-4s %-10s %-8s %s" % ("判项", "判定", "数值", "阈值", "说明"))
    print("-" * 74)
    for it in items:
        v = "—" if a.calib else it["verdict"]
        print("%-6s %-4s %-10s %-8s %s" % (
            it["id"], v,
            "—" if it.get("value") is None else it["value"],
            it.get("threshold"), it.get("note", "")))
    veto = [it["id"] for it in items
            if it["verdict"] == "no" and (not it["uncalibrated"] or a.enforce_uncalibrated)]
    soft = [it["id"] for it in items if it["verdict"] == "no" and it["uncalibrated"]]
    warn = []
    if soft:
        warn.append("未标定项判 no（不否决，需人工复核）：" + ", ".join(soft))
    if any(it["id"] == "A-02" and it["verdict"] == "na" for it in items):
        warn.append("A-02 未执行（缺 ASR）⇒ **口播与文案一致性本次未被检查**，别当成通过")
    verdict = "REJECT" if veto else "PASS"
    print("-" * 74)
    print("[A 段] 判定：%s%s  （耗时 %.1fs）" % (
        verdict, "  否决项：" + ",".join(veto) if veto else "", time.time() - t0))
    for w in warn:
        print("   ⚠️ " + w)

    rep = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "files": paths, "mode": a.mode,
           "vo_declared": vo, "items": items,
           "aggregate": {"verdict": verdict, "veto_items": veto,
                         "uncalibrated_no": soft, "warnings": warn,
                         "calib_mode": bool(a.calib)}}
    if a.out:
        os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
        json.dump(rep, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("   报告 → %s" % a.out)


if __name__ == "__main__":
    main()
