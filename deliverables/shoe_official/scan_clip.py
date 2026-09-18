#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scan_clip.py —— 把一条 H3 成片按帧号确定性抽帧，拼成检视扫描图。

用法: scan_clip.py <mp4> <输出png> <帧号,逗号分隔…> [每行张数] [标签前缀]
说明: 用 select=eq(n,N) 精确抽帧（不用 -ss 模糊定位），保证同一帧号每次结果一致。
"""
import subprocess
import sys
import os
from PIL import Image, ImageDraw, ImageFont

# ⚠️ 标签是中文，必须用带 CJK 字形的字体：Arial / Helvetica 没有中文字形，
#    会渲染成一排方框（□）—— 实测踩过。优先 STHeiti，退 PingFang。
FONT = "/System/Library/Fonts/STHeiti Medium.ttc"
if not os.path.exists(FONT):
    FONT = "/System/Library/Fonts/PingFang.ttc"
if not os.path.exists(FONT):
    FONT = "/System/Library/Fonts/Supplemental/Arial Unicode.ttf"


def grab(mp4, idx, tmpdir):
    out = os.path.join(tmpdir, "f%04d.png" % idx)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", mp4,
                    "-vf", "select=eq(n\\,%d)" % idx, "-vsync", "0", "-frames:v", "1", out],
                   check=True)
    return out


def main():
    mp4, outpng, idxs = sys.argv[1], sys.argv[2], [int(x) for x in sys.argv[3].split(",")]
    per = int(sys.argv[4]) if len(sys.argv) > 4 else 7
    tag = sys.argv[5] if len(sys.argv) > 5 else ""

    tmpdir = os.path.join(os.path.dirname(outpng), "_tmp_scan")
    os.makedirs(tmpdir, exist_ok=True)
    imgs = [Image.open(grab(mp4, i, tmpdir)).convert("RGB") for i in idxs]

    TW = 232
    th = int(TW * imgs[0].height / imgs[0].width)
    rows = (len(imgs) + per - 1) // per
    LB = 26
    sheet = Image.new("RGB", (TW * min(per, len(imgs)), (th + LB) * rows), (250, 250, 250))
    d = ImageDraw.Draw(sheet)
    try:
        f = ImageFont.truetype(FONT, 15)
    except Exception:
        f = ImageFont.load_default()
    for k, (i, im) in enumerate(zip(idxs, imgs)):
        r, c = divmod(k, per)
        x, y = c * TW, r * (th + LB)
        d.text((x + 5, y + 5), "f%d (%.2fs)" % (i, i / 24.0), fill=(20, 20, 20), font=f)
        sheet.paste(im.resize((TW, th), Image.LANCZOS), (x, y + LB))
    sheet.save(outpng)
    print("->", outpng, "%dx%d" % sheet.size, "%d 帧" % len(imgs))

    for im in imgs:
        im.close()


if __name__ == "__main__":
    main()
