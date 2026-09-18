#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cmp_frames.py —— 把成片抽帧与锚定照并排，做「锚定保真」目视核验。

用法: cmp_frames.py <输出png> <标签1> <图1> <标签2> <图2> ...
"""
import os
import subprocess
import sys
import numpy as np
from PIL import Image, ImageDraw, ImageFont

# ⚠️ 标签是中文，必须用带 CJK 字形的字体：Arial / Helvetica 没有中文字形，
#    会渲染成一排方框（□）—— 实测踩过。优先 STHeiti，退 PingFang。
FONT = "/System/Library/Fonts/STHeiti Medium.ttc"
if not os.path.exists(FONT):
    FONT = "/System/Library/Fonts/PingFang.ttc"
if not os.path.exists(FONT):
    FONT = "/System/Library/Fonts/Supplemental/Arial Unicode.ttf"


def load(src, tmpdir):
    """src = mp4:帧号 或 直接图片路径"""
    if ":" in src and src.rsplit(":", 1)[0].lower().endswith(".mp4"):
        mp4, n = src.rsplit(":", 1)
        out = os.path.join(tmpdir, "c_%d.png" % int(n))
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", mp4,
                        "-vf", "select=eq(n\\,%d)" % int(n), "-vsync", "0",
                        "-frames:v", "1", out], check=True)
        return Image.open(out).convert("RGB")
    return Image.open(src).convert("RGB")


def main():
    outpng = sys.argv[1]
    pairs = sys.argv[2:]
    tmpdir = os.path.join(os.path.dirname(outpng), "_tmp_cmp")
    os.makedirs(tmpdir, exist_ok=True)

    items = []
    for k in range(0, len(pairs), 2):
        items.append((pairs[k], load(pairs[k + 1], tmpdir)))

    TW = 300
    th = int(TW * items[0][1].height / items[0][1].width)
    LB = 28
    sheet = Image.new("RGB", (TW * len(items), th + LB), (250, 250, 250))
    d = ImageDraw.Draw(sheet)
    try:
        f = ImageFont.truetype(FONT, 15)
    except Exception:
        f = ImageFont.load_default()
    for k, (tag, im) in enumerate(items):
        x = k * TW
        d.text((x + 5, 6), tag, fill=(20, 20, 20), font=f)
        sheet.paste(im.resize((TW, th), Image.LANCZOS), (x, LB))
    sheet.save(outpng)
    print("->", outpng, "%dx%d" % sheet.size)

    if len(items) == 2:
        a = np.asarray(items[0][1].resize((256, 448))).astype(np.float32)
        b = np.asarray(items[1][1].resize((256, 448))).astype(np.float32)
        mad = float(np.abs(a - b).mean())
        dR = float((a[..., 0] - a[..., 2]).mean() - (b[..., 0] - b[..., 2]).mean())
        print("   逐像素平均绝对差 MAD = %.2f ｜ 暖色漂移 Δ(R-B) = %+.2f"
              % (mad, dR))
        print("   判据：MAD<8 = 高保真；|Δ(R-B)|<6 = 未见偏色")


if __name__ == "__main__":
    main()
