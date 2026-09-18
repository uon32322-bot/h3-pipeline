#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""状态轨迹追踪：看每个目标状态在时间轴上的哪个位置"最像"，验证锚点是否按时落位。

用法: state_track.py <video.mp4> <label1:img1> <label2:img2> ...
"""
import glob, os, subprocess, sys, tempfile
import numpy as np
from PIL import Image

STEP = 2  # 每 2 帧取一帧（12fps 采样）


def load_small(p):
    im = Image.open(p).convert("L").resize((216, 120), Image.LANCZOS)
    return np.asarray(im, dtype=np.float32)


def main():
    vid = sys.argv[1]
    targets = []
    for a in sys.argv[2:]:
        lab, _, path = a.partition(":")
        targets.append((lab, load_small(path)))

    tmp = tempfile.mkdtemp()
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", vid, "-vf", "scale=216:120",
                    os.path.join(tmp, "%04d.png")], check=True)
    fs = sorted(glob.glob(os.path.join(tmp, "*.png")))
    frames = [np.asarray(Image.open(f).convert("L"), dtype=np.float32) for f in fs]
    n = len(frames)
    print("=" * 74)
    print("视频:", os.path.basename(vid), " 总帧数:", n)
    fidx = list(range(0, n, STEP))
    for lab, tgt in targets:
        curve = np.array([float(np.abs(frames[i] - tgt).mean()) for i in fidx])
        i_min = int(curve.argmin())
        f_min = fidx[i_min]
        # 到达阈值：首次进入"距最小值 +3"的区间
        thr = curve.min() + 3.0
        arrival = None
        for k, v in enumerate(curve):
            if v <= thr:
                arrival = fidx[k]
                break
        print("-" * 74)
        print("目标状态「%s」" % lab)
        print("  最相似帧: frame %d (%.2fs)   MAE=%.2f" % (f_min, f_min / 24.0, curve.min()))
        print("  首次达到(阈值%.1f)于 frame %d (%.2fs)" % (thr, arrival if arrival is not None else -1,
                                              (arrival or 0) / 24.0))
        bar = ""
        for k, v in enumerate(curve):
            if k % 4 == 0:
                bar += "▁▂▃▄▅▆▇█"[min(7, int(v / max(curve.max(), 0.01) * 7.99))]
        print("  相似度轨迹(每%d帧, 越矮越像): %s" % (STEP * 4, bar))
    print()


if __name__ == "__main__":
    main()
