#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""宫格裁帧 + 首尾帧一致性自检。

用法:
  python3 frames.py crop    <grid.png> <out_prefix>          # 2x2 宫格 -> 4 张单帧
  python3 frames.py metrics <imgA.png> <imgB.png> [out.png]  # 两帧零成本自检
  python3 frames.py sheet   <out.png> <img1> <img2> ...      # 生成对比接触表
"""
import os
import sys

import numpy as np
from PIL import Image, ImageDraw

TARGET = (1280, 720)  # 16:9，与 H3 出片比例一致


def load16x9(path, inset=0):
    im = Image.open(path).convert("RGB")
    if inset:
        w, h = im.size
        im = im.crop((inset, inset, w - inset, h - inset))
    return im.resize(TARGET, Image.LANCZOS)


def crop_grid(path, prefix, inset=8):
    im = Image.open(path).convert("RGB")
    W, H = im.size
    half_w, half_h = W // 2, H // 2
    names = []
    for idx, (col, row) in enumerate([(0, 0), (1, 0), (0, 1), (1, 1)], start=1):
        box = (col * half_w + inset, row * half_h + inset,
               (col + 1) * half_w - inset, (row + 1) * half_h - inset)
        panel = im.crop(box).resize(TARGET, Image.LANCZOS)
        out = "%s_p%d.png" % (prefix, idx)
        panel.save(out)
        names.append(out)
        print("  %s  <- %s  %s" % (out, box, panel.size))
    print("宫格原始尺寸: %dx%d  每格约 %dx%d" % (W, H, half_w, half_h))
    return names


def metrics(a_path, b_path, out_path=None):
    a = np.asarray(load16x9(a_path), dtype=np.float32)
    b = np.asarray(load16x9(b_path), dtype=np.float32)
    diff = np.abs(a - b).mean(axis=2)  # 0..255
    H, W = diff.shape

    mae = float(diff.mean())
    changed = float((diff > 30).mean() * 100.0)

    # 边框带（四边各 12%）——会被"进入画面的手臂"污染，仅作参考
    bh, bw = int(H * 0.12), int(W * 0.12)
    border = np.zeros((H, W), dtype=bool)
    border[:bh, :] = border[-bh:, :] = True
    border[:, :bw] = border[:, -bw:] = True
    border_mae = float(diff[border].mean())
    center_mae = float(diff[~border].mean())

    # 纯净背景区：顶部 15% 行 + 左侧 65% 列（此处无手臂/主体，只反映机位与光照）
    BH, BW = int(H * 0.15), int(W * 0.65)
    bg = np.zeros((H, W), dtype=bool)
    bg[:BH, :BW] = True
    bg_mae = float(diff[bg].mean())

    print("  整体平均像素差 MAE      : %.2f / 255" % mae)
    print("  变化像素占比 (>30)      : %.2f %%" % changed)
    print("  纯背景区 MAE(机位/光照) : %.2f   <- 公平指标，越小越说明同机位同光照" % bg_mae)
    print("  边框带 MAE(含手臂干扰)  : %.2f" % border_mae)
    print("  中心区 MAE(动作变化量)  : %.2f" % center_mae)

    verdict = []
    if bg_mae < 4:
        verdict.append("✅ 背景/机位一致性 好")
    elif bg_mae < 10:
        verdict.append("⚠️ 背景/机位一致性 一般")
    else:
        verdict.append("❌ 背景/机位漂移，插值易变形或硬切")
    if changed < 12:
        verdict.append("✅ 状态变化集中且局部")
    elif changed < 30:
        verdict.append("⚠️ 状态变化偏大")
    else:
        verdict.append("❌ 状态变化过大，建议拆分")
    for v in verdict:
        print("  " + v)

    if out_path:
        hm = np.zeros((H, W, 3), dtype=np.uint8)
        hm[..., 0] = np.clip(diff * 3, 0, 255)
        hm[..., 1] = np.clip(diff * 1.2, 0, 255)
        Image.fromarray(hm).save(out_path)
        print("  差异热力图 -> %s" % out_path)
    return dict(mae=mae, changed=changed, border_mae=border_mae, center_mae=center_mae)


def sheet(out_path, paths, labels=None):
    ims = [load16x9(p) for p in paths]
    cols = len(ims)
    tw, th = 480, 270
    pad, lab = 10, 24
    W = cols * tw + (cols + 1) * pad
    H = th + 2 * pad + lab
    canvas = Image.new("RGB", (W, H), (245, 244, 240))
    d = ImageDraw.Draw(canvas)
    for i, im in enumerate(ims):
        x = pad + i * (tw + pad)
        canvas.paste(im.resize((tw, th), Image.LANCZOS), (x, pad + lab))
        t = labels[i] if labels else os.path.basename(paths[i])
        d.text((x + 2, pad + 6), t, fill=(40, 40, 40))
    canvas.save(out_path)
    print("接触表 -> %s (%dx%d)" % (out_path, W, H))


if __name__ == "__main__":
    mode = sys.argv[1]
    if mode == "crop":
        crop_grid(sys.argv[2], sys.argv[3])
    elif mode == "metrics":
        out = sys.argv[4] if len(sys.argv) > 4 else None
        metrics(sys.argv[2], sys.argv[3], out)
    elif mode == "sheet":
        sheet(sys.argv[2], sys.argv[3:])
    else:
        print(__doc__)
