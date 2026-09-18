#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Ref2VA 参考一致性自检 —— 参考图 vs 成片关键帧

用法:
  refcheck.py <video.mp4> --ref 参考图1 [--ref 参考图2 ...] [--out 前缀]

做三件事（全部零成本，不调模型）:
  1. 抽 N 帧 → 与参考图并排拼成对照板（人眼判主体是否被保留）
  2. 全局调色板相似度：对每张参考图算 HSV 二维直方图，与成片各帧比
     Bhattacharyya 系数（越接近 1 越像；>0.5 视为同色系）
  3. 主体色相占比：统计成片帧里参考图主色相附近像素的占比
     → 判断"产品到底有没有出现在画面里、出现多久"
"""
import json, os, subprocess, sys

import numpy as np


def probe_video(p):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,r_frame_rate",
         "-show_entries", "format=duration", "-of", "json", p],
        capture_output=True, text=True).stdout
    d = json.loads(out)
    st = d["streams"][0]
    num, den = st["r_frame_rate"].split("/")
    return int(st["width"]), int(st["height"]), float(num) / float(den), float(d["format"]["duration"])


def load_video(p, w, h, fps, n):
    """抽 n 帧为 (n,h,w,3) float32 RGB"""
    step = max(1, int(round(fps))) / max(fps, 1)
    cmd = ["ffmpeg", "-v", "error", "-i", p,
           "-vf", f"fps={fps},scale={w}:{h}", "-frames:v", str(n),
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    raw = subprocess.run(cmd, capture_output=True).stdout
    a = np.frombuffer(raw, dtype=np.uint8)
    if a.size < w * h * 3:
        return np.zeros((0, h, w, 3), np.float32)
    k = a.size // (w * h * 3)
    return a[:k * w * h * 3].reshape(k, h, w, 3).astype(np.float32)


def load_rgb(p, w, h):
    cmd = ["ffmpeg", "-v", "error", "-i", p, "-vf", f"scale={w}:{h}",
           "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    raw = subprocess.run(cmd, capture_output=True).stdout
    a = np.frombuffer(raw, dtype=np.uint8)
    if a.size < w * h * 3:
        return None
    return a[:w * h * 3].reshape(h, w, 3).astype(np.float32)


def rgb2hsv(a):
    a = a / 255.0
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    mx = np.max(a, -1); mn = np.min(a, -1)
    df = mx - mn
    h = np.zeros_like(mx)
    m = (df > 1e-6)
    rr = m & (mx == r); gg = m & (mx == g) & ~rr; bb = m & ~rr & ~gg
    h[rr] = (60 * ((g - b) / np.where(df == 0, 1, df)) % 360)[rr]
    h[gg] = (60 * ((b - r) / np.where(df == 0, 1, df)) + 120)[gg]
    h[bb] = (60 * ((r - g) / np.where(df == 0, 1, df)) + 240)[bb]
    s = np.where(mx > 1e-6, df / np.where(mx == 0, 1, mx), 0)
    return h, s, mx


def palette_hist(hsv, hbins=30, sbins=8):
    h, s, v = hsv
    keep = (v > 0.12) & (s > 0.08)
    hh = np.clip((h[keep] / 360.0 * hbins).astype(int), 0, hbins - 1)
    ss = np.clip((s[keep] * sbins).astype(int), 0, sbins - 1)
    hist = np.zeros((hbins, sbins), np.float64)
    np.add.at(hist, (hh, ss), 1.0)
    if hist.sum() > 0:
        hist /= hist.sum()
    return hist


def bhatt(p, q):
    return float(np.sqrt(p * q).sum())


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    vid = sys.argv[1]
    args = sys.argv[2:]

    def multi(name):
        out = []
        for i, a in enumerate(args):
            if a == name and i + 1 < len(args):
                out.append(args[i + 1])
        return out

    def opt(name, d=None):
        return args[args.index(name) + 1] if name in args else d

    refs = multi("--ref")
    outpre = opt("--out", "refcheck")

    W, H, FPS, DUR = probe_video(vid)
    print(f"成片: {W}x{H} @{FPS:.2f}fps  {DUR:.2f}s")
    frames = load_video(vid, 320, int(320 * H / W), FPS, 12)
    print(f"抽帧: {len(frames)} 帧\n")

    print(f"{'参考图':<26}{'调色板相似度':>14}{'主色相':>10}{'主色占比':>10}")
    print("-" * 62)
    for rp in refs:
        ref = load_rgb(rp, 320, int(320 * H / W))
        if ref is None:
            print(f"{os.path.basename(rp):<26} 读取失败")
            continue
        rh = palette_hist(rgb2hsv(ref))
        hh, ss, vv = rgb2hsv(ref)
        keep = (vv > 0.12) & (ss > 0.08)
        dom = float(np.median(hh[keep])) if keep.sum() > 100 else -1.0

        sims, covs = [], []
        for f in frames:
            sims.append(bhatt(rh, palette_hist(rgb2hsv(f))))
            fh, fs, fv = rgb2hsv(f)
            if dom >= 0:
                d = np.abs(((fh - dom + 180) % 360) - 180)
                covs.append(float(((d < 20) & (fs > 0.15) & (fv > 0.15)).mean()))
        print(f"{os.path.basename(rp):<26}{np.mean(sims):>14.3f}"
              f"{dom:>10.0f}{np.mean(covs) * 100:>9.1f}%")

    # ---- 对照板（统一宽度后纵向堆叠，vstack 要求等宽）----
    ROWW, ROWH = 1680, 420
    tmp = []
    for i, rp in enumerate(refs):
        t = f"/tmp/_rc_ref{i}.png"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", rp,
                        "-vf", f"scale=-1:{ROWH},pad={ROWW}:{ROWH}:(ow-iw)/2:0:white",
                        "-frames:v", "1", t], check=False)
        tmp.append(t)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", vid,
                    "-vf", f"fps=2,scale=-1:{ROWH},tile=6x1,pad={ROWW}:{ROWH}:(ow-iw)/2:0:white",
                    "-frames:v", "1", "/tmp/_rc_frames.png"], check=False)
    n = len(tmp)
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", "/tmp/_rc_frames.png"]
    for t in tmp:
        cmd += ["-i", t]
    fc = "".join(f"[{i+1}:v]" for i in range(n)) + f"[0:v]vstack=inputs={n+1}"
    cmd += ["-filter_complex", fc, "-frames:v", "1", f"{outpre}_board.png"]
    subprocess.run(cmd, check=False)
    print(f"\n对照板: {outpre}_board.png（顶行=成片抽帧，下方=各参考图）")


if __name__ == "__main__":
    main()
