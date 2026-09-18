#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_shop_layer.py —— 为【带货投放版】生成中文信息层。

与 SOP 官方版的关系（必须说清楚）：
  · 官方《极简产品广告生成器》SOP 默认**禁止**把画面文案退化成后期字幕 ⇒ 官方版不加任何后期文字。
  · 带货投放版是在官方成片**之上**再叠一层中文带货信息，属**有意扩展**，
    触发条件是 SOP 允许的例外："只有用户明确要求后期叠字"（辉哥要求"完整的产品卖点"）。
  · 位置：只在**最底部字幕区**，不与 A 片画面内英文文案（位于画面中上区）争同一视觉区。

文案全部来自 产品信息.txt，不新增任何未经确认的功效表述。
"""
import json
import os

from PIL import Image, ImageDraw, ImageFont

W, H = 768, 1344
F_ZH = "/System/Library/Fonts/STHeiti Medium.ttc"

BAR_TOP, BAR_H = 1186, 98
BG = (14, 16, 20, 178)
WHITE = (255, 255, 255, 255)
SUB = (255, 214, 102, 255)     # 价格强调（暖黄，不与产品色冲突）

# 时间段严格对齐两片拼接后的时间轴：B(0–5.167) → A(5.167–15.292)
SEGS = [
    (0.0000, 5.1667, "真人上脚 · 单只约 250g"),
    (5.1667, 10.6667, "轻弹缓震运动跑鞋 · 跑步通勤两相宜"),
    (10.6667, 15.2917, "￥129 起 · 36–45 码 · 点击下方链接"),
]


def font(path, size):
    return ImageFont.truetype(path, size)


def text_w(d, t, f):
    return sum(d.textlength(c, font=f) for c in t)


def bar_layer(text, size=46, top=BAR_TOP, h=BAR_H, fill=WHITE, bg=BG, max_w=W - 56):
    """⚠️ 字号必须自适应：写死 46px 时，16 字中文（"轻弹缓震运动跑鞋 · 跑步通勤两相宜"）
       ≈ 736px，加 2×42 padding = 820px > 画布 768px ⇒ 左右两端被硬裁掉
       （实测 seg2 少了"两相宜"、seg3 少了"点击下方链接"）。
       这里按可用宽度反解字号，并留 4px 安全余量。"""
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    pad = 42
    while size > 16:
        f = font(F_ZH, size)
        if text_w(d, text, f) + pad * 2 <= max_w:
            break
        size -= 1
    tw = text_w(d, text, f)
    x0 = W / 2 - tw / 2 - pad
    d.rounded_rectangle((x0, top, x0 + tw + pad * 2, top + h), radius=h // 2, fill=bg)
    bb = d.textbbox((0, 0), text, font=f)
    d.text((W / 2 - tw / 2, top + (h - (bb[3] - bb[1])) / 2 - bb[1]), text, font=f, fill=fill)
    return im, size


def aigc_layer():
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    f = font(F_ZH, 24)
    t = "AIGC 生成"
    tw = text_w(d, t, f)
    d.rounded_rectangle((24, H - 56, 24 + tw + 26, H - 20), radius=18, fill=(0, 0, 0, 130))
    d.text((37, H - 50), t, font=f, fill=(255, 255, 255, 190))
    return im


def main():
    out = "post_shop/overlay"
    os.makedirs(out, exist_ok=True)
    manifest = []
    for i, (t0, t1, txt) in enumerate(SEGS, 1):
        im, sz = bar_layer(txt)
        p = "%s/seg%d.png" % (out, i)
        im.save(p)
        manifest.append(dict(idx=i, t0=t0, t1=t1, png=p, zh=txt, pt=sz))
        print("  段%d  %.3f–%.3f  %s（字号 %dpt）" % (i, t0, t1, txt, sz))
    ap = "%s/aigc.png" % out
    aigc_layer().save(ap)
    print("  合规层 aigc.png（全程常驻）")
    json.dump(manifest, open("post_shop/overlay_manifest.json", "w"),
              ensure_ascii=False, indent=1)

    # 预览：叠在浅底上核验可读性与位置
    sheet = Image.new("RGB", (W // 2 * 3 + 28, H // 2 + 34), (236, 239, 243))
    d = ImageDraw.Draw(sheet)
    for i, m in enumerate(manifest):
        base = Image.new("RGBA", (W, H), (120, 152, 178, 255))
        base.alpha_composite(Image.open(m["png"]))
        d.text((i * (W // 2 + 10) + 8, 8), "段%d" % m["idx"], fill=(20, 20, 20))
        sheet.paste(base.convert("RGB").resize((W // 2, H // 2)), (i * (W // 2 + 10) + 6, 28))
    sheet.save("post_shop/overlay_preview.png")
    print("  预览 post_shop/overlay_preview.png")


if __name__ == "__main__":
    main()
