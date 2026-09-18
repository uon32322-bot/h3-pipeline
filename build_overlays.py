#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_overlays.py —— 生成带货片后期图文层（透明 PNG，逐镜一张）。

设计原则（与 shotlist_schema 的文案三分流一致）：
  · onscreen_en  → 英文卖点词（角标 pill，顶部；第 5 镜因模特脸部在顶部而下移）
  · post_zh      → 中文卖点条 / 价格 / CTA（底部圆角条）
  · 合规标识     → 独立一张全时段层（AIGC 生成），不随镜头切换
全部走后期叠加而非交给 H3 画字 —— 保证文字 100% 清晰、可审、可改。
"""
import json
import os

from PIL import Image, ImageDraw, ImageFont

W, H = 768, 1344
F_EN = "/System/Library/Fonts/Supplemental/Arial Black.ttf"
F_ZH = "/System/Library/Fonts/STHeiti Medium.ttc"

BG = (14, 16, 20, 165)          # 深色半透明 pill
RED = (226, 45, 55, 245)        # 促销红（中国电商惯例）
WHITE = (255, 255, 255, 255)
SUB = (238, 240, 245, 220)

EN_TOP_Y = 100
EN_BOT_Y = 1026                 # 第 5 镜用（避开模特脸）
ZH_TOP = 1152
ZH_H = 104
PRICE_TOP = 1030
PRICE_H = 88


def font(path, size, index=0):
    try:
        return ImageFont.truetype(path, size, index=index)
    except Exception:
        return ImageFont.truetype(path, size)


def pill(d, box, radius, fill):
    d.rounded_rectangle(box, radius=radius, fill=fill)


def draw_ls(d, text, y, f, fill, ls=6, cx=W // 2, shadow=True):
    """带字距的居中文字（PIL 无原生 letter-spacing）。"""
    ws = [d.textlength(c, font=f) for c in text]
    total = sum(ws) + ls * (len(text) - 1)
    x = cx - total / 2
    if shadow:
        for dx, dy in ((-2, 2), (2, 2)):
            xx = x
            for c, w in zip(text, ws):
                d.text((xx + dx, y + dy), c, font=f, fill=(0, 0, 0, 150))
                xx += w + ls
    for c, w in zip(text, ws):
        d.text((x, y), c, font=f, fill=fill)
        x += w + ls
    return total


def text_w(d, text, f, ls=0):
    return sum(d.textlength(c, font=f) for c in text) + ls * max(0, len(text) - 1)


def en_layer(text, y, size=44, ls=6):
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    f = font(F_EN, size)
    tw = text_w(d, text, f, ls)
    pad = 34
    bh = size + 34
    pill(d, (W / 2 - tw / 2 - pad, y, W / 2 + tw / 2 + pad, y + bh), bh // 2, BG)
    draw_ls(d, text, y + 15, f, WHITE, ls=ls)
    return im


def zh_layer(text, top=ZH_TOP, size=48, fill=WHITE, bg=BG, h=ZH_H):
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    f = font(F_ZH, size)
    tw = text_w(d, text, f)
    pad = 40
    pill(d, (W / 2 - tw / 2 - pad, top, W / 2 + tw / 2 + pad, top + h), h // 2, bg)
    bbox = d.textbbox((0, 0), text, font=f)
    ty = top + (h - (bbox[3] - bbox[1])) / 2 - bbox[1]
    d.text((W / 2 - tw / 2, ty), text, font=f, fill=fill)
    return im


def price_layer(main, sub):
    """红色价格 pill：￥129 起 + 小字副信息。"""
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    # ⚠️ Arial Black 无 ￥(U+FFE5) 字形，实测渲染为空白 ⇒ 价格主字必须用中文字体
    f1 = font(F_ZH, 50)
    f2 = font(F_ZH, 34)
    t1 = text_w(d, main, f1)
    t2 = text_w(d, sub, f2)
    tw = t1 + 46 + t2
    pad = 40
    x0 = W / 2 - tw / 2 - pad
    pill(d, (x0, PRICE_TOP, x0 + tw + pad * 2, PRICE_TOP + PRICE_H), PRICE_H // 2, RED)
    b1 = d.textbbox((0, 0), main, font=f1)
    b2 = d.textbbox((0, 0), sub, font=f2)
    y1 = PRICE_TOP + (PRICE_H - (b1[3] - b1[1])) / 2 - b1[1]
    y2 = PRICE_TOP + (PRICE_H - (b2[3] - b2[1])) / 2 - b2[1]
    cx = W / 2 - tw / 2
    d.text((cx, y1), main, font=f1, fill=WHITE)
    d.text((cx + t1 + 46, y2), sub, font=f2, fill=(255, 238, 238, 255))
    return im


def aigc_layer():
    """合规标识：全程常驻（不随镜头切换）。"""
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    f = font(F_ZH, 26)
    t = "AIGC 生成"
    tw = text_w(d, t, f)
    pill(d, (24, H - 62, 24 + tw + 30, H - 22), 20, (0, 0, 0, 120))
    d.text((39, H - 55), t, font=f, fill=(255, 255, 255, 185))
    return im


SHOTS = [
    dict(idx=1, t0=0.0000, t1=2.5000, en="CUSHION RUNNER", zh="轻弹缓震跑鞋", y=EN_TOP_Y),
    dict(idx=2, t0=2.5000, t1=5.1667, en="HIGH-REBOUND FOAM", zh="EVA 高弹发泡中底 · 落地缓震", y=EN_TOP_Y),
    dict(idx=3, t0=5.1667, t1=7.6667, en="BREATHABLE", zh="贾卡网面 · 透气不闷脚", y=EN_TOP_Y),
    dict(idx=4, t0=7.6667, t1=10.3333, en="JACQUARD MESH", zh="久穿清爽 · 夏天也敢穿", y=EN_TOP_Y),
    dict(idx=5, t0=10.3333, t1=12.8333, en="ON FOOT", zh="真人上脚 · 单只约 250g", y=EN_BOT_Y),
    dict(idx=6, t0=12.8333, t1=15.4999, en="LINK BELOW", zh="36–45 码 · 点击下方链接", y=EN_TOP_Y, price=True),
]


def main():
    out = "post/overlay"
    os.makedirs(out, exist_ok=True)
    manifest = []
    for s in SHOTS:
        im = en_layer(s["en"], s["y"])
        if s.get("price"):
            im.alpha_composite(price_layer("￥129 起", "直播价"))
        im.alpha_composite(zh_layer(s["zh"]))
        p = "%s/shot%d.png" % (out, s["idx"])
        im.save(p)
        manifest.append(dict(idx=s["idx"], t0=s["t0"], t1=s["t1"], png=p,
                             en=s["en"], zh=s["zh"]))
        print("  shot%d  %.3f–%.3f  EN=%-20s ZH=%s" % (s["idx"], s["t0"], s["t1"], s["en"], s["zh"]))
    p = "%s/aigc.png" % out
    aigc_layer().save(p)
    print("  合规层 aigc.png（全程常驻）")
    json.dump(manifest, open("post/overlay_manifest.json", "w"), ensure_ascii=False, indent=1)

    # 拼一张预览（叠在青底上便于肉眼核对）
    sheet = Image.new("RGB", (W // 3 * 6 + 28, H // 3 + 20), (235, 238, 242))
    for i, m in enumerate(manifest):
        t = Image.open(m["png"]).resize((W // 3, H // 3))
        bg = Image.new("RGB", t.size, (118, 150, 175))
        bg.paste(t, (0, 0), t)
        sheet.paste(bg, (i * (W // 3 + 5) + 4, 10))
    sheet.save("post/overlay_preview.png")
    print("  预览 post/overlay_preview.png")


if __name__ == "__main__":
    main()
