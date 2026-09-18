#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_anchors.py —— 按官方《极简产品广告生成器》SOP 生成【三张独立锚定照片】。

SOP 原文（步骤 6）：
  · 照片 1：主视觉 / 刁钻主视角锚点
  · 照片 2：材质 / 功能细节锚点
  · 照片 3：结尾文案锚点 —— 必须包含已确认文案，单行，
           字体 SF Pro Display Semibold，文字颜色拆成两段：
           白色科技风「前半黑色或深灰，后半具体商品色」
  · 三张必须是三张独立图片，禁止网格 / 拼贴。

本片设定（白色科技风）：
  · 商品主色 = 黑（贾卡网布鞋面），辅助 = 银白
  · 文案 1（中段/透气）：Air Flows Right Through
  · 文案 2（结尾/缓震+轻量）：Soft Landing, Light As Air
"""
import os

import numpy as np
from PIL import Image, ImageDraw, ImageFont

W, H = 768, 1344
SF = "/System/Library/Fonts/SFNS.ttf"

DARK = (74, 79, 85)        # 前半：深灰（白色科技风允许）
PRODUCT = (18, 18, 18)     # 后半：商品主色（黑）

COPY_MID = ("Air Flows ", "Right Through")
COPY_END = ("Soft Landing, ", "Light As Air")


def sf(size, weight="Semibold"):
    f = ImageFont.truetype(SF, size)
    try:
        f.set_variation_by_name(weight)
    except Exception as e:
        print("   ⚠️ 字重切换失败(%s)，退回默认" % e)
    return f


def draw_line(im, parts, y, size=47, x0=64):
    """单行、两段式配色、带字距的文案（SOP：单行 / 只用两色 / 靠左参与构图）。"""
    d = ImageDraw.Draw(im)
    f = sf(size)
    ls = 1.2
    x = x0
    for i, (txt, col) in enumerate(zip(parts, (DARK, PRODUCT))):
        for ch in txt:
            d.text((x, y), ch, font=f, fill=col + (255,))
            x += d.textlength(ch, font=f) + ls
    return x - x0


def anchor3(base_png, out_png):
    """照片 3：结尾文案锚点 —— 产品收束构图 + 单行文案。"""
    im = Image.open(base_png).convert("RGB")
    if im.size != (W, H):
        im = im.resize((W, H), Image.LANCZOS)
    w = draw_line(im, COPY_END, y=860, size=47, x0=64)
    im.save(out_png)
    return w


def main():
    out = "anchors"
    os.makedirs(out, exist_ok=True)

    # 照片 1：主视觉（已验证的 hero 帧）
    a1 = Image.open("input/c1_first.png").convert("RGB")
    a1.save("%s/anchor_1_hero.png" % out)

    # 照片 2：材质 / 功能细节（网面特写，已验证构图）
    a2 = Image.open("input/c2_last.png").convert("RGB")
    a2.save("%s/anchor_2_material.png" % out)

    # 照片 3：结尾文案锚点（产品收束 + 文案）
    w = anchor3("input/c1_last.png", "%s/anchor_3_endcopy.png" % out)

    print("锚定照 1 主视觉       768x1344  (hero 全貌)")
    print("锚定照 2 材质/功能细节 768x1344  (贾卡网面特写)")
    print("锚定照 3 结尾文案构图  768x1344  文案='%s%s' 单行宽 %.0fpx" %
          (COPY_END[0], COPY_END[1], w))

    # 三联核验图（SOP 要求三张独立成立、光/影/调色统一）
    sheet = Image.new("RGB", (W // 2 * 3 + 28, H // 2 + 40), (238, 240, 244))
    d = ImageDraw.Draw(sheet)
    for i, (p, t) in enumerate((("anchor_1_hero.png", "锚定照1 主视觉"),
                                ("anchor_2_material.png", "锚定照2 材质细节"),
                                ("anchor_3_endcopy.png", "锚定照3 结尾文案"))):
        im = Image.open("%s/%s" % (out, p)).resize((W // 2, H // 2))
        d.text((i * (W // 2 + 10) + 6, 8), t, fill=(20, 20, 20))
        sheet.paste(im, (i * (W // 2 + 10) + 6, 30))
    sheet.save("%s/00_三张锚定照核验.png" % out)
    print("核验图: %s/00_三张锚定照核验.png" % out)


if __name__ == "__main__":
    main()
