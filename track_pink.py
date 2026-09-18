#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""产品轨迹追踪：按粉色分割找出产品质心，画出它在时间上的运动轨迹。

用法: track_pink.py <video> [视频2 ...]
"""
import glob, os, subprocess, sys, tempfile
import numpy as np
from PIL import Image

FPS = 12


def track(path):
    tmp = tempfile.mkdtemp()
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", path, "-vf",
                    "fps=%d,scale=240:432" % FPS, os.path.join(tmp, "%04d.png")], check=True)
    fs = sorted(glob.glob(os.path.join(tmp, "*.png")))
    cx, cy, area = [], [], []
    for f in fs:
        a = np.asarray(Image.open(f).convert("RGB"), dtype=np.float32)
        r, g, b = a[:, :, 0], a[:, :, 1], a[:, :, 2]
        # 粉色：R 明显高于 G/B，且整体偏亮
        m = (r - g > 14) & (r - b > 20) & (r > 150) & (g > 110) & (r < 250)
        if m.sum() < 12:
            cx.append(np.nan); cy.append(np.nan); area.append(0); continue
        ys, xs = np.nonzero(m)
        cx.append(float(xs.mean())); cy.append(float(ys.mean())); area.append(int(m.sum()))
    return np.array(cx), np.array(cy), np.array(area), len(fs)


def show(path, label=""):
    cx, cy, ar, n = track(path)
    t = np.arange(n) / FPS
    ok = ~np.isnan(cx)
    if ok.sum() < 3:
        print("%s: 未能稳定分割出产品" % path)
        return
    print("=" * 76)
    print("%s  %s" % (os.path.basename(path), label))
    # 位置序列（每约 0.25s）
    k = max(1, n // 20)
    print("  产品质心 x(每%.2fs): %s" % (k / FPS, " ".join("%.0f" % v if v == v else "-" for v in cx[::k])))
    print("  产品质心 y(每%.2fs): %s" % (k / FPS, " ".join("%.0f" % v if v == v else "-" for v in cy[::k])))
    print("  产品像素面积(每%.2fs): %s" % (k / FPS, " ".join("%d" % v for v in ar[::k])))
    # 位移速率
    vx = np.diff(cx[ok]); vy = np.diff(cy[ok])
    sp = np.sqrt(vx ** 2 + vy ** 2)
    tt = t[ok][1:]
    print("  移动速率峰值 %.2f px/帧 @%.2fs" % (float(np.nanmax(sp)), float(tt[np.nanargmax(sp)])))
    print("  前段(前1/3)平均速率 %.2f ｜ 中段 %.2f ｜ 后段 %.2f" % (
        float(np.nanmean(sp[:len(sp) // 3])),
        float(np.nanmean(sp[len(sp) // 3:2 * len(sp) // 3])),
        float(np.nanmean(sp[2 * len(sp) // 3:]))))
    # 停顿检测：速率低于峰值 15% 的连续区间
    thr = float(np.nanmax(sp)) * 0.15
    slow = sp < thr
    runs, i = [], 0
    while i < len(slow):
        if slow[i]:
            j = i
            while j + 1 < len(slow) and slow[j + 1]:
                j += 1
            if (j - i) / FPS >= 0.3:
                runs.append((tt[i], tt[j]))
            i = j + 1
        else:
            i += 1
    print("  停顿区间(>0.3s):", "、".join("%.2f~%.2fs" % (a, b) for a, b in runs) or "无")
    # 漂移总量
    print("  总位移: Δx=%.0fpx Δy=%.0fpx ｜ 面积变化 %.0f%%" % (
        float(np.nanmax(cx[ok]) - np.nanmin(cx[ok])),
        float(np.nanmax(cy[ok]) - np.nanmin(cy[ok])),
        100 * (ar[ok][-1] - ar[ok][0]) / max(ar[ok][0], 1)))
    print()


if __name__ == "__main__":
    for i, p in enumerate(sys.argv[1:]):
        show(p)
