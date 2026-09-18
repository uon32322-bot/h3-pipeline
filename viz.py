#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""绘图辅助：统一中文字体与对照表版式。"""
from PIL import Image, ImageDraw, ImageFont

FONT_PATH = "/System/Library/Fonts/Hiragino Sans GB.ttc"


def font(size):
    try:
        return ImageFont.truetype(FONT_PATH, size)
    except Exception:
        return ImageFont.load_default()


def sheet(items, out, cols, title, cell=(300, 169), label_w=150, pad=6,
          bg=(248, 247, 244), note=None):
    """items: [(path, label), ...] 按行优先排列。"""
    rows = (len(items) + cols - 1) // cols
    cw, ch = cell
    W = label_w + cols * (cw + pad) + pad
    H = 46 + rows * (ch + 26) + (30 if note else 12)
    c = Image.new("RGB", (W, H), bg)
    d = ImageDraw.Draw(c)
    d.text((pad + 2, 12), title, fill=(28, 28, 28), font=font(17))
    for i, (p, lab) in enumerate(items):
        r, col = divmod(i, cols)
        x = label_w + col * (cw + pad)
        y = 44 + r * (ch + 26)
        try:
            im = Image.open(p).convert("RGB").resize((cw, ch), Image.LANCZOS)
        except Exception:
            continue
        c.paste(im, (x, y))
        d.rectangle([x, y, x + cw - 1, y + ch - 1], outline=(198, 195, 188))
        d.text((x + 2, y + ch + 4), lab, fill=(70, 70, 70), font=font(14))
    if note:
        d.text((pad + 2, H - 24), note, fill=(110, 110, 110), font=font(13))
    c.save(out)
    return c.size


def motion_chart(series, out, title, W=980, H=430, xmax=None):
    """series: [(label, times[], values[], color), ...] 绝对时间轴的运动曲线对比。"""
    pl, pr, pt, pb = 64, 20, 104, 56
    c = Image.new("RGB", (W, H), (250, 249, 246))
    d = ImageDraw.Draw(c)
    d.text((pl, 14), title, fill=(28, 28, 28), font=font(16))
    iw, ih = W - pl - pr, H - pt - pb
    xmax = xmax or max(max(t) for _, t, _, _ in series)
    mx = max(max(v) for _, _, v, _ in series) * 1.18
    for k in range(5):
        y = pt + ih * k / 4
        d.line([pl, y, pl + iw, y], fill=(228, 226, 220))
        d.text((10, y - 8), "%.0f" % (mx * (1 - k / 4)), fill=(120, 120, 120), font=font(12))
    for k in range(6):
        x = pl + iw * k / 5
        d.line([x, pt, x, pt + ih], fill=(240, 238, 233))
        d.text((x - 10, pt + ih + 8), "%.1f" % (xmax * k / 5), fill=(120, 120, 120), font=font(12))
    d.line([pl, pt + ih * 0.75, pl + iw, pt + ih * 0.75], fill=(214, 130, 130), width=1)
    for i, (lab, ts, vals, col) in enumerate(series):
        pts = [(pl + iw * t / xmax, pt + ih * (1 - v / mx)) for t, v in zip(ts, vals)]
        d.line(pts, fill=col, width=3, joint="curve")
        lx = pl + (i % 2) * (iw // 2)
        ly = 36 + (i // 2) * 26
        d.line([lx, ly + 6, lx + 26, ly + 6], fill=col, width=3)
        d.text((lx + 32, ly - 1), lab, fill=(58, 58, 58), font=font(13))
    d.text((pl, H - 30), "时间（秒）→   同一动作弧、同一对首尾锚点；曲线越早拉满且越晚回落 = 死时间越少",
           fill=(118, 118, 118), font=font(12))
    d.text((pl + iw - 130, pt + ih * 0.75 - 18), "动作判定阈值", fill=(190, 100, 100), font=font(11))
    c.save(out)
    return c.size
