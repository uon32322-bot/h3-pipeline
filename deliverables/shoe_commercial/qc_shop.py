#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""qc_shop.py —— 带货成片出厂质检（三项硬指标 + 一张核验图）。

① 产品保真：成片"尾帧"应与输入尾帧高度一致（H3 是忠实插值器，不应重绘产品）
② 色调稳定：鞋身区域 R−B 偏移 —— REPORT23 的变色事故是 +43.7，阈值定 ±10
③ 接缝/伪影：顶行/底行横向 std ≈ 0 即色带伪影
另出 12 帧序列图供肉眼终检。
"""
import json
import os
import subprocess
import sys

import numpy as np
from PIL import Image, ImageDraw

D = os.path.dirname(os.path.abspath(__file__))
VID = os.path.join(D, "SHOE_COMMERCIAL.mp4")


def grab(t, out):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", "%.3f" % t,
                    "-i", VID, "-vframes", "1", out], check=True)
    return Image.open(out).convert("RGB")


def rb(im, box):
    a = np.asarray(im.crop(box), dtype=np.float64)
    return float(a[:, :, 0].mean() - a[:, :, 2].mean())


def main():
    os.makedirs(os.path.join(D, "qc"), exist_ok=True)
    # 镜位中点（避开切镜瞬间）
    probes = [(1, 1.20), (2, 3.80), (3, 6.40), (4, 9.00), (5, 11.60), (6, 14.40)]
    ims = {}
    for idx, t in probes:
        ims[idx] = grab(t, os.path.join(D, "qc/s%d.png" % idx))

    print("=" * 74)
    print("① 产品保真 —— 成片尾帧 vs 输入尾帧（复现噪声基线 1.08 / 换 seed 差异 14.0）")
    print("=" * 74)
    # 成片镜2/镜4 的尾帧 ≈ 5.15s / 10.30s
    for idx, t, src in ((2, 5.15, "input/c1_last.png"), (4, 10.30, "input/c2_last.png"),
                        (6, 15.45, "input/c3_last.png")):
        g = grab(t, os.path.join(D, "qc/tail%d.png" % idx))
        p = os.path.join(D, src)
        if not os.path.exists(p):
            print("  （缺输入尾帧 %s，跳过）" % src)
            continue
        s = Image.open(p).convert("RGB")
        if s.size != g.size:
            s = s.resize(g.size)
        d = np.abs(np.asarray(g, dtype=np.int16) - np.asarray(s, dtype=np.int16)).max(axis=2)
        print("  镜%-2d  t=%.2fs  平均差 %6.2f   最大 %3d   %s"
              % (idx, t, d.mean(), d.max(),
                 "✅ 忠实" if d.mean() < 14 else "⚠️ 疑似重绘"))

    print()
    print("=" * 74)
    print("② 色调稳定 —— 鞋身区 R−B 偏移（事故值 +43.7，阈值 ±10）")
    print("=" * 74)
    # 鞋身取样框：镜1–4 产品在画面中带；镜5–6 取脚部
    boxes = {1: (240, 460, 560, 820), 2: (150, 330, 640, 990), 3: (240, 460, 560, 820),
             4: (200, 380, 640, 900), 5: (240, 1080, 560, 1180), 6: (150, 1050, 620, 1250)}
    base = None
    offsets = []
    for idx, t in probes:
        v = rb(ims[idx], boxes[idx])
        if base is None:
            base = v
        off = v - base
        offsets.append(off)
        print("  镜%-2d  t=%5.2fs  R−B=%7.2f   相对镜1 %+6.2f  %s"
              % (idx, t, v, off, "✅" if abs(off) <= 10 else "❌ 超阈"))
    print("  ⇒ 最大偏移 %+.2f  %s" % (max(offsets, key=abs),
                                      "✅ 色调稳定" if max(abs(x) for x in offsets) <= 10 else "❌ 需改提示词"))

    print()
    print("=" * 74)
    print("③ 伪影检测 —— 顶/底行横向 std（≈0 = 拉伸色带）")
    print("=" * 74)
    for idx, t in probes:
        a = np.asarray(ims[idx], dtype=np.float64)
        print("  镜%-2d  顶行std=%6.1f 底行std=%6.1f  %s"
              % (idx, a[0].std(), a[-1].std(),
                 "✅" if min(a[0].std(), a[-1].std()) > 3 else "⚠️ 疑似色带"))

    # 12 帧序列图
    seq = [0.10, 1.40, 2.70, 3.90, 5.30, 6.50, 7.80, 9.10, 10.45, 11.70, 13.00, 14.60]
    tiles = [grab(t, os.path.join(D, "qc/seq%.2f.png" % t)) for t in seq]
    w, h = int(tiles[0].width * 0.30), int(tiles[0].height * 0.30)
    sheet = Image.new("RGB", (w * 6 + 34, (h + 30) * 2 + 12), (232, 235, 240))
    d = ImageDraw.Draw(sheet)
    for i, (t, im) in enumerate(zip(seq, tiles)):
        x = (i % 6) * (w + 5) + 5
        y = (i // 6) * (h + 30) + 26
        d.text((x, y - 20), "t=%.2fs" % t, fill=(20, 20, 20))
        sheet.paste(im.resize((w, h)), (x, y))
    sheet.save(os.path.join(D, "qc/成片十二帧序列.png"))
    print("\n  质检图: qc/成片十二帧序列.png")


if __name__ == "__main__":
    main()
