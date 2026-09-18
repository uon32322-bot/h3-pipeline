#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ROI 感知的动作密度分析。

全画面平均会把"局部小动作"稀释掉（人物坐着不动、只有盖子开合时尤其明显）。
本脚本先用「首/尾锚点的差异区域」自动求出 ROI，再只在 ROI 内计算逐帧运动量。

用法: density_roi.py <video> <first_anchor.png> <last_anchor.png> [label]
"""
import glob, os, subprocess, sys, tempfile
import numpy as np
from PIL import Image

FPS = 12
H, W = 120, 216  # 统一到低分辨率做分析


def prep(p):
    return np.asarray(Image.open(p).convert("L").resize((W, H), Image.LANCZOS), dtype=np.float32)


def analyze(vid, first, last, label=""):
    a, b = prep(first), prep(last)
    diff = np.abs(a - b)
    thr = max(6.0, float(diff.max()) * 0.30)
    roi = diff > thr
    # 形态学轻微膨胀，把动作边缘包进来
    for _ in range(2):
        r = roi.copy()
        r[1:, :] |= roi[:-1, :]; r[:-1, :] |= roi[1:, :]
        r[:, 1:] |= roi[:, :-1]; r[:, :-1] |= roi[:, 1:]
        roi = r
    frac = roi.mean()
    tmp = tempfile.mkdtemp()
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", vid, "-vf",
                    "fps=%d,scale=%d:%d" % (FPS, W, H), os.path.join(tmp, "%04d.png")], check=True)
    fs = sorted(glob.glob(os.path.join(tmp, "*.png")))
    arr = [np.asarray(Image.open(f).convert("L"), dtype=np.float32) for f in fs]
    if roi.sum() == 0:
        return None
    d = np.array([float(np.abs(arr[i][roi] - arr[i - 1][roi]).mean()) for i in range(1, len(arr))])
    k = np.array([1, 2, 3, 2, 1], dtype=np.float32); k /= k.sum()
    ds = np.convolve(d, k, mode="same")
    t = np.arange(1, len(arr)) / FPS
    total = len(arr) / FPS
    mx = float(ds.max())
    athr = mx * 0.25
    active = ds > athr
    bursts = []
    i = 0
    while i < len(active):
        if active[i]:
            j, gap = i, 0
            while j + 1 < len(active):
                if active[j + 1]:
                    j += 1; gap = 0
                elif gap < 2:
                    j += 1; gap += 1
                else:
                    break
            bursts.append((i, j)); i = j + 1
        else:
            i += 1
    bursts = [(x, y) for x, y in bursts if (y - x) / FPS >= 0.2]
    act = sum((y - x) / FPS for x, y in bursts)
    ina = np.zeros(len(ds), dtype=bool)
    for x, y in bursts:
        ina[x:y + 1] = True
    jit = float(np.abs(np.diff(ds, n=2))[ina[1:-1]].mean()) if ina[1:-1].any() else 0.0
    print("=" * 78)
    print("%s   %s   总时长 %.2fs" % (os.path.basename(vid), label, total))
    print("  ROI 占全画面: %.1f%%（自动取自首尾锚点的差异区域）" % (100 * frac))
    print("  ROI 内运动曲线(每%.2fs): %s" % (3 / FPS,
          " ".join("%.1f" % v for v in ds[::3])))
    print("  动作段数: %d" % len(bursts))
    for x, y in bursts:
        print("     · %.2fs ~ %.2fs  (%.2fs)" % (x / FPS, y / FPS, (y - x) / FPS))
    print("  动作总时长: %.2fs   覆盖率: %.0f%%" % (act, 100 * act / max(total, .01)))
    print("  过渡抖动度: %.3f" % jit)
    print()


if __name__ == "__main__":
    v, f, l = sys.argv[1], sys.argv[2], sys.argv[3]
    lab = sys.argv[4] if len(sys.argv) > 4 else ""
    analyze(v, f, l, lab)
