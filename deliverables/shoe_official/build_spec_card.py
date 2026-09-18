#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_spec_card.py —— 生成《商品规格卡》（9:16 竖版 PNG）。

为什么单独出一张卡：官方 SOP 明文禁止把价格/参数做成画面促销文字，
但这些信息在电商投放里必须能拿到 ⇒ 不塞进片子，单独出图，供详情页/投放文案使用。
内容 100% 来自 产品信息.txt，不新增任何功效表述。
"""
import os

from PIL import Image, ImageDraw, ImageFont

W, H = 768, 1344
F_ZH_B = "/System/Library/Fonts/STHeiti Medium.ttc"
F_ZH = "/System/Library/Fonts/Hiragino Sans GB.ttc"
F_EN = "/System/Library/Fonts/SFNS.ttf"

BG_TOP = (247, 249, 252)
BG_BOT = (226, 233, 241)
INK = (22, 24, 28)
SUB = (110, 118, 128)
ACCENT = (18, 18, 18)
RED = (206, 42, 52)

NAME = "轻弹缓震运动跑鞋"
POINTS = [
    ("01", "高回弹发泡中底", "落地缓震不累脚"),
    ("02", "贾卡网面透气", "久穿不闷脚"),
    ("03", "轻量化设计", "单只仅约 250g"),
]
SPECS = [
    ("鞋面", "贾卡网布"),
    ("中底", "EVA 高弹发泡"),
    ("适用", "跑步 / 通勤 / 日常"),
    ("尺码", "36–45"),
]
PRICE = "￥129 起"


def f(path, size, weight=None):
    ft = ImageFont.truetype(path, size)
    if weight:
        try:
            ft.set_variation_by_name(weight)
        except Exception:
            pass
    return ft


def main():
    im = Image.new("RGB", (W, H), BG_TOP)
    d = ImageDraw.Draw(im)
    for y in range(H):                      # 纵向柔和渐变底
        t = y / H
        d.line([(0, y), (W, y)], fill=tuple(
            int(BG_TOP[i] + (BG_BOT[i] - BG_TOP[i]) * t) for i in range(3)))

    # 头部
    d.text((64, 92), "PRODUCT SHEET", font=f(F_EN, 26, "Semibold"), fill=SUB)
    d.text((64, 132), NAME, font=f(F_ZH_B, 64), fill=INK)
    d.line([(64, 232), (704, 232)], fill=(206, 214, 224), width=2)

    # 三条卖点
    y = 286
    for no, main, sub in POINTS:
        d.text((64, y), no, font=f(F_EN, 40, "Bold"), fill=(178, 188, 200))
        d.text((148, y + 2), main, font=f(F_ZH_B, 44), fill=INK)
        d.text((148, y + 58), sub, font=f(F_ZH, 34), fill=SUB)
        y += 138

    # 规格表
    y += 12
    d.line([(64, y), (704, y)], fill=(206, 214, 224), width=2)
    y += 34
    d.text((64, y), "规格参数", font=f(F_ZH_B, 36), fill=INK)
    y += 66
    for k, v in SPECS:
        d.text((64, y), k, font=f(F_ZH, 34), fill=SUB)
        d.text((220, y), v, font=f(F_ZH_B, 34), fill=INK)
        y += 56

    # 价格
    y += 26
    pw = d.textlength(PRICE, font=f(F_ZH_B, 52)) + 76
    d.rounded_rectangle((64, y, 64 + pw, y + 92), radius=46, fill=RED)
    bb = d.textbbox((0, 0), PRICE, font=f(F_ZH_B, 52))
    d.text((64 + 38, y + (92 - (bb[3] - bb[1])) / 2 - bb[1]), PRICE,
           font=f(F_ZH_B, 52), fill=(255, 255, 255))

    # 合规标识
    d.rounded_rectangle((64, H - 84, 218, H - 40), radius=22, fill=(0, 0, 0, 30))
    d.text((86, H - 76), "AIGC 生成", font=f(F_ZH, 26), fill=(90, 96, 104))

    os.makedirs("deliverables", exist_ok=True) if False else None
    im.save("商品规格卡_运动鞋.png")
    print("商品规格卡_运动鞋.png  768x1344")


if __name__ == "__main__":
    main()
