#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ab_scan.py —— 同一组帧号，把两条成片上下并排成对照扫描图。

用途：A（初版）vs A3（修正版）在**完全相同的帧号**上逐帧对照，
      用来判断"改提示词到底改动了什么、有没有修好"。
      帧号用 select=eq(n,N) 精确抽取（不用 -ss 模糊定位），保证可复核。

用法: ab_scan.py <上片> <下片> <输出png> <帧号,逗号分隔…> [上片标签] [下片标签]
"""
import os
import subprocess
import sys
from PIL import Image, ImageDraw, ImageFont

# ⚠️ 标签是中文，必须用带 CJK 字形的字体：Arial / Helvetica 没有中文字形，
#    会渲染成一排方框（□）—— 实测踩过。优先 STHeiti，退 PingFang。
FONT = "/System/Library/Fonts/STHeiti Medium.ttc"
if not os.path.exists(FONT):
    FONT = "/System/Library/Fonts/PingFang.ttc"
if not os.path.exists(FONT):
    FONT = "/System/Library/Fonts/Supplemental/Arial Unicode.ttf"


def grab(mp4, idx, tmpdir):
    out = os.path.join(tmpdir, "%s_%04d.png" % (os.path.basename(mp4)[:6], idx))
    if not os.path.exists(out):
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", mp4,
                        "-vf", "select=eq(n\\,%d)" % idx, "-vsync", "0",
                        "-frames:v", "1", out], check=True)
    return Image.open(out).convert("RGB")


def main():
    top, bot, outpng = sys.argv[1], sys.argv[2], sys.argv[3]
    idxs = [int(x) for x in sys.argv[4].split(",")]
    ttag = sys.argv[5] if len(sys.argv) > 5 else os.path.basename(top)
    btag = sys.argv[6] if len(sys.argv) > 6 else os.path.basename(bot)

    tmpdir = os.path.join(os.path.dirname(outpng), "_tmp_ab")
    os.makedirs(tmpdir, exist_ok=True)

    TW = 300
    probe = grab(top, idxs[0], tmpdir)
    TH = int(TW * probe.height / probe.width)
    LB = 26
    sheet = Image.new("RGB", (TW * len(idxs), (TH + LB) * 2), (250, 250, 250))
    d = ImageDraw.Draw(sheet)
    try:
        f = ImageFont.truetype(FONT, 15)
    except Exception:
        f = ImageFont.load_default()

    for r, (mp4, tag) in enumerate(((top, ttag), (bot, btag))):
        for c, i in enumerate(idxs):
            im = grab(mp4, i, tmpdir)
            x = c * TW
            y = r * (TH + LB)
            d.text((x + 5, y + 4), "%s · f%d (%.2fs)" % (tag, i, i / 24.0),
                   fill=(20, 20, 20), font=f)
            sheet.paste(im.resize((TW, TH), Image.LANCZOS), (x, y + LB))

    sheet.save(outpng)
    print("->", outpng, "%dx%d" % sheet.size, "｜上=%s 下=%s" % (ttag, btag))

    for fn in os.listdir(tmpdir):
        os.remove(os.path.join(tmpdir, fn))
    os.rmdir(tmpdir)


if __name__ == "__main__":
    main()
