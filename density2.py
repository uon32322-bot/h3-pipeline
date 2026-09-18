#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""动作结构分析（多峰版）：量化动作段数、覆盖时长、过渡抖动。

用法: density2.py <video> [更多...]
"""
import glob, os, subprocess, sys, tempfile
import numpy as np
from PIL import Image

FPS = 12


def analyze(path):
    tmp = tempfile.mkdtemp()
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", path,
                    "-vf", f"fps={FPS},scale=180:100", os.path.join(tmp, "%04d.png")], check=True)
    fs = sorted(glob.glob(os.path.join(tmp, "*.png")))
    arr = [np.asarray(Image.open(f).convert("L"), dtype=np.float32) for f in fs]
    d = np.array([float(np.abs(arr[i] - arr[i - 1]).mean()) for i in range(1, len(arr))])
    # 轻平滑，去掉编码噪声
    k = np.array([1, 2, 3, 2, 1], dtype=np.float32); k /= k.sum()
    ds = np.convolve(d, k, mode="same")
    t = np.arange(1, len(arr)) / FPS
    total = len(arr) / FPS
    mx = float(ds.max())
    thr = max(1.5, mx * 0.25)
    active = ds > thr
    # 找所有连续活跃区间（允许 2 帧以内空洞）
    bursts = []
    i = 0
    while i < len(active):
        if active[i]:
            j = i
            gap = 0
            while j + 1 < len(active):
                if active[j + 1]:
                    j += 1; gap = 0
                elif gap < 2:
                    j += 1; gap += 1
                else:
                    break
            bursts.append((i, j))
            i = j + 1
        else:
            i += 1
    # 过滤掉 <0.25s 的碎片
    bursts = [(a, b) for a, b in bursts if (b - a) / FPS >= 0.25]
    act_time = sum((b - a) / FPS for a, b in bursts)
    in_act = np.zeros(len(ds), dtype=bool)
    for a, b in bursts:
        in_act[a:b + 1] = True
    jitter = float(np.abs(np.diff(ds, n=2))[in_act[1:-1]].mean()) if in_act[1:-1].any() else 0.0
    return dict(name=os.path.basename(path), total=total, curve=ds, t=t, bursts=bursts,
                act_time=act_time, bg=float(np.median(ds[~in_act])) if (~in_act).any() else 0.0,
                jitter=jitter, mx=mx, act_dens=float(ds[in_act].mean()) if in_act.any() else 0.0)


def show(r):
    print("=" * 76)
    print("%s   总时长 %.2fs" % (r["name"], r["total"]))
    line = []
    for i, v in enumerate(r["curve"][::3]):
        line.append("%.1f" % v)
    print("  运动曲线(每%.2fs): %s" % (3 / FPS, " ".join(line)))
    print("  动作段数: %d" % len(r["bursts"]))
    for a, b in r["bursts"]:
        print("     · %.2fs ~ %.2fs  (%.2fs)" % (a / FPS, b / FPS, (b - a) / FPS))
    print("  动作总时长: %.2fs   覆盖率: %.0f%%" % (r["act_time"], 100 * r["act_time"] / max(r["total"], .01)))
    print("  动作段内平均运动量: %.2f   静息段运动量(中位): %.2f   信噪比: %.1fx"
          % (r["act_dens"], r["bg"], r["act_dens"] / max(r["bg"], .01)))
    print("  过渡抖动度(二阶差分): %.3f   <- 越小越顺滑" % r["jitter"])
    print()


if __name__ == "__main__":
    for p in sys.argv[1:]:
        show(analyze(p))
