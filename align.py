#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""产品本体对齐度检测：估计产品在画面中的左右边界 -> 宽度(尺度)与中心位置。

原理：产品（玻璃瓶）对背景有强竖向边缘。在瓶身所在的水平带内统计 |dI/dx| 的列剖面，
峰值即产品左右轮廓线。宽度差值 = 尺度漂移，中心差值 = 位置漂移。
这两项直接决定 FL2VA 插值会不会产生形变/硬切。

用法: align.py <imgA.png> <imgB.png>
"""
import sys

import numpy as np
from PIL import Image

TARGET = (1280, 720)


def profile(path):
    im = Image.open(path).convert("RGB").resize(TARGET, Image.LANCZOS)
    g = np.asarray(im.convert("L"), dtype=np.float32)
    H, W = g.shape
    gx = np.abs(np.diff(g, axis=1))            # 竖向边缘
    gx = np.concatenate([gx[:, :1], gx], axis=1)  # 补齐到 W 列
    band = gx[int(H * 0.42):int(H * 0.78), :]  # 瓶身水平带
    col = band.mean(axis=0)
    k = np.ones(11) / 11.0
    col = np.convolve(col, k, mode="same")
    return col, W


def edges(col, W):
    lo, hi = int(W * 0.06), int(W * 0.94)
    seg = col[lo:hi]
    p1 = lo + int(np.argmax(seg))
    # 在距 p1 至少 40px 处找第二个峰
    mask = np.ones(W, dtype=bool)
    mask[max(0, p1 - 40):p1 + 40] = False
    mask[:lo] = False
    mask[hi:] = False
    tmp = col.copy()
    tmp[~mask] = -1
    p2 = int(np.argmax(tmp))
    left, right = min(p1, p2), max(p1, p2)
    return left, right, (left + right) / 2.0, right - left


def main():
    a, b = sys.argv[1], sys.argv[2]
    ca, W = profile(a)
    cb, _ = profile(b)
    la, ra, cena, wa = edges(ca, W)
    lb, rb, cenb, wb = edges(cb, W)

    print("  %-14s 产品左边界=%4d  右边界=%4d  中心=%6.1f  宽度=%4d" % (a, la, ra, cena, wa))
    print("  %-14s 产品左边界=%4d  右边界=%4d  中心=%6.1f  宽度=%4d" % (b, lb, rb, cenb, wb))

    dw = (wb - wa) / float(wa) * 100.0
    dc = cenb - cena
    print("  ------------------------------------------------")
    print("  尺度漂移 Δ宽度 : %+.1f%%" % dw)
    print("  位置漂移 Δ中心 : %+.1f px  (占画幅 %.1f%%)" % (dc, abs(dc) / W * 100))

    v = []
    if abs(dw) < 4 and abs(dc) < 12:
        v.append("✅ 产品对齐良好，插值稳定")
    elif abs(dw) < 10 and abs(dc) < 32:
        v.append("⚠️ 产品有可感漂移，插值可能出现轻微形变")
    else:
        v.append("❌ 产品未对齐，插值大概率形变或硬切")
    for x in v:
        print("  " + x)


if __name__ == "__main__":
    main()
