#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""timeline.py —— H3 成片的【近景指数】时间轴（只输出站得住的量）。

──── 为什么只有"深色占比"一个指标 ────
本片背景是【蓝→白渐变】、产品是黑/白/银，且既有黑网面也有白中底。踩过两个坑：

 坑1 全画幅平均 R−B 当色偏探针 → 无效。
     背景偏蓝（R−B 为负），产品中性。产品占画幅一变，全画幅均值就跟着变。
     实测：旧稿 A 末帧全画幅 Δ(R−B) = +22（看着像严重偏色），
     但末帧 vs 锚定照实测 Δ(R−B) = −0.94、MAD 3.21 ⇒ 根本没偏色。
     结论：全画幅 R−B 是【构图代理量】，不是色偏探针。

 坑2 四角中位色 / 局部梯度阈值当"背景分割" → 也无效。
     背景是渐变，角点是最饱和的蓝，其余留白比它亮 ⇒ 大片留白被误判成产品
     （实测"产品区 R−B"报 −34，纯属误判）；梯度阈值则受压缩噪声干扰，
     背景里到处都超阈值。⇒ 无监督分割在这条片子上不成立，不要再用。

所以本脚本只保留真正可解释的量：
  · 近景指数 = 深色（明度<50）像素占比。黑网面是最暗、最"实"的元素，
    镜头推近 → 黑网面占画幅上升 ⇒ 该值单调上升。【判据：>30 = 已推成满画幅特写】
  · 平均亮度 = 全画幅明度均值。满画幅深色特写时显著下降，可作交叉验证。

色彩保真与锚定保真【不靠本脚本】，靠 cmp_frames.py 与锚定照做逐像素比对
（MAD / Δ(R−B)），那才是可解释、可复核的证据链。

用法: timeline.py <mp4> <步长> [输出png]
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


def frames_of(mp4):
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                        "-show_entries", "stream=nb_frames,width,height",
                        "-of", "csv=p=0", mp4], capture_output=True, text=True)
    w, h, n = [int(x) for x in r.stdout.strip().split(",")]
    return w, h, n


def extract(mp4, idxs, tmpdir):
    outdir = os.path.join(tmpdir, "tl")
    os.makedirs(outdir, exist_ok=True)
    for i in idxs:
        out = os.path.join(outdir, "t%05d.png" % i)
        if not os.path.exists(out):
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", mp4,
                            "-vf", "select=eq(n\\,%d)" % i, "-vsync", "0",
                            "-frames:v", "1", out], check=True)
    return outdir


def metrics(p):
    a = np.asarray(Image.open(p).convert("RGB").resize((192, 336))).astype(np.float32)
    luma = a @ np.array([0.2126, 0.7152, 0.0722], np.float32)
    return float((luma < 50).mean() * 100), float(luma.mean())


def main():
    mp4, step = sys.argv[1], int(sys.argv[2])
    outpng = sys.argv[3] if len(sys.argv) > 3 else None
    w, h, n = frames_of(mp4)
    idxs = list(range(0, n, step))
    if idxs[-1] != n - 1:
        idxs.append(n - 1)
    tmpdir = os.path.join(os.path.dirname(os.path.abspath(mp4)), "_tmp_tl")
    d = extract(mp4, idxs, tmpdir)

    rows = [metrics(os.path.join(d, "t%05d.png" % i)) for i in idxs]
    print("=== %s ｜ %dx%d ｜ %d 帧 ｜ 每 %d 帧取样 ===" % (os.path.basename(mp4), w, h, n, step))
    print("  %-11s %-11s %-9s" % ("帧(秒)", "近景指数", "平均亮度"))
    peak = max(rows)[0]
    for i, (dk, lu) in zip(idxs, rows):
        flag = ""
        if dk > 30:
            flag = " ←已推成满画幅特写"
        print("  f%-4d %-7.2fs %-11.1f %-9.1f%s" % (i, i / 24.0, dk, lu, flag))
    # 峰值段的持续时间 —— 判断"冲过头"是否形成一整个拖沓的段落
    over = [i for i, (dk, _) in zip(idxs, rows) if dk > 30]
    if over:
        print("  ⇒ 近景指数 >30 的区间：f%d–f%d（%.2f–%.2fs，共 %.2fs）"
              % (over[0], over[-1], over[0] / 24.0, over[-1] / 24.0,
                 (over[-1] - over[0]) / 24.0))
    print("  ⇒ 峰值 %.1f%% 出现在 f%d（%.2fs）" % (peak, idxs[rows.index(max(rows))],
                                                idxs[rows.index(max(rows))] / 24.0))

    if outpng:
        try:
            f = ImageFont.truetype(FONT, 15)
        except Exception:
            f = ImageFont.load_default()
        CW, CH, MB, MT = 8, 110, 56, 26
        W = len(idxs) * CW + MB + 20
        H = CH * 2 + MT * 3 + 12
        im = Image.new("RGB", (W, H), (255, 255, 255))
        dr = ImageDraw.Draw(im)

        def plot(k, vals, title, color, vmax):
            top = MT + k * (CH + MT)
            dr.text((MB, top - 18), title, fill=(20, 20, 20), font=f)
            dr.line((MB, top, MB + len(idxs) * CW, top), fill=(210, 210, 210))
            dr.line((MB, top + CH, MB + len(idxs) * CW, top + CH), fill=(210, 210, 210))
            for j, v in enumerate(vals):
                x = MB + j * CW
                hh = int(min(abs(v) / vmax, 1.0) * CH)
                dr.rectangle((x + 1, top + CH - hh, x + CW - 2, top + CH), fill=color)
            dr.text((MB, top + CH + 2), "0s", fill=(110, 110, 110), font=f)
            dr.text((MB + len(idxs) * CW - 30, top + CH + 2),
                    "%.1fs" % ((n - 1) / 24.0), fill=(110, 110, 110), font=f)

        plot(0, [r[0] for r in rows], "① 近景指数 = 深色(明度<50)占比%（>30 = 满画幅深色特写）",
             (60, 90, 160), 60)
        plot(1, [r[1] for r in rows], "② 平均亮度（满画幅特写时下降，作交叉验证）",
             (40, 130, 100), 180)
        im.save(outpng)
        print("->", outpng)

    for fn in os.listdir(d):
        os.remove(os.path.join(d, fn))
    os.rmdir(d)


if __name__ == "__main__":
    main()
