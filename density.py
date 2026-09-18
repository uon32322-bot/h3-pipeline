#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""动作密度分析：量化一段视频里"动作占了多长时间、分布在哪、有多密"。

用法: density.py <video> [更多视频...]
"""
import glob, os, subprocess, sys, tempfile
import numpy as np
from PIL import Image

FPS = 8  # 采样帧率


def analyze(path):
    tmp = tempfile.mkdtemp()
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", path,
                    "-vf", f"fps={FPS},scale=180:100", os.path.join(tmp, "%04d.png")],
                   check=True)
    fs = sorted(glob.glob(os.path.join(tmp, "*.png")))
    if len(fs) < 3:
        return None
    arr = [np.asarray(Image.open(f).convert("L"), dtype=np.float32) for f in fs]
    d = np.array([float(np.abs(arr[i] - arr[i - 1]).mean()) for i in range(1, len(arr))])
    t = np.arange(1, len(arr)) / FPS
    total = len(arr) / FPS
    mx = float(d.max())
    thr = mx * 0.25
    # 动作窗口：连续超过阈值 25% 的区间（放宽 1 帧容差）
    active = d > thr
    idx = np.where(active)[0]
    if len(idx) == 0:
        return dict(path=path, total=total, mx=mx, start=0, end=0, dur=0, dens=0,
                    outside=0.0, curve=d, t=t, fps=FPS)
    # 取包含峰值的最长连续段
    peak_i = int(d.argmax())
    s = peak_i
    while s - 1 >= 0 and active[s - 1]:
        s -= 1
    e = peak_i
    while e + 1 < len(active) and active[e + 1]:
        e += 1
    start_t = float(t[s]) - 1.0 / FPS
    end_t = float(t[e])
    dur = max(0.0, end_t - start_t)
    in_win = d[s:e + 1]
    dens = float(in_win.mean()) if len(in_win) else 0.0
    outside_mask = np.ones(len(d), dtype=bool)
    outside_mask[s:e + 1] = False
    outside = float(d[outside_mask].mean()) if outside_mask.any() else 0.0
    return dict(path=path, total=total, mx=mx, start=start_t, end=end_t, dur=dur,
                dens=dens, outside=outside, curve=d, t=t, fps=FPS)


def main():
    for p in sys.argv[1:]:
        r = analyze(p)
        if not r:
            print(p, "-> 无法分析")
            continue
        n = len(r["curve"])
        step = max(1, n // 20)
        print("=" * 68)
        print("文件:", os.path.basename(p))
        print("总时长: %.2fs (%d 帧采样 @%dfps)" % (r["total"], n + 1, r["fps"]))
        print("运动曲线(每 %.2fs):" % (step / r["fps"]),
              " ".join("%.1f" % v for v in r["curve"][::step]))
        print("峰值运动: %.2f @ %.2fs" % (r["mx"], float(r["t"][r["curve"].argmax()])))
        print("动作窗口: %.2fs ~ %.2fs   持续 %.2fs   占全片 %.0f%%"
              % (r["start"], r["end"], r["dur"], 100 * r["dur"] / max(r["total"], 0.01)))
        print("窗口内平均运动量(密度): %.2f    窗口外平均: %.2f    信噪比: %.1fx"
              % (r["dens"], r["outside"], r["dens"] / max(r["outside"], 0.01)))
        print()
    return r


if __name__ == "__main__":
    main()
