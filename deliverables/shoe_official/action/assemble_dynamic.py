#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""assemble_dynamic.py —— 合成【动态卖点版】带货片（N 片段通用）。

  C1 棚拍跑步（真人在前，建立动态）→ C2 户外散步（场景演示）→ D 产品动态卖点片（收束于文案）

  版本 1  SOP 官方版   直接拼接，保留 H3 原生音轨，**不加任何后期文字**
                       → DYNAMIC_SOP_FILM.mp4
  版本 2  带货投放版   在官方版之上叠中文带货信息层 + AIGC 合规标识
                       → DYNAMIC_COMMERCE.mp4

音频：片段各带 H3 原生音轨；每个接缝做 0.25 s 交叉淡化避免音乐断点，再补静音到画面总长。

用法: assemble_dynamic.py <片段1.mp4> <片段2.mp4> ... <片段N.mp4>
"""
import json
import os
import subprocess
import sys

from PIL import Image, ImageDraw, ImageFont

D = os.path.dirname(os.path.abspath(__file__))
FPS = 24
W, H = 768, 1344
F_ZH = "/System/Library/Fonts/STHeiti Medium.ttc"
BAR_TOP, BAR_H = 1186, 98
BG = (14, 16, 20, 178)
WHITE = (255, 255, 255, 255)
XFADE = 0.25

# 三条中文信息条（全部来自 产品信息.txt，不新增未经确认的表述）
ZH_TEXTS = [
    "真人上脚演示 · 单只约 250g",
    "高回弹缓震 · 贾卡网面透气",
    "￥129 起 · 36–45 码 · 点击下方链接",
]
# 与画面内英文文案（位于画面中上区）错开：中文条只在最底部字幕区
COPY_BOTTOM_LIMIT = 0.72     # 画面内英文文案最低不超过 0.72H
BAR_TOP_FRAC = BAR_TOP / H   # 0.882


def run(cmd):
    print("  $ ffmpeg ...", flush=True)
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        print(r.stderr[-3000:])
        sys.exit(1)


def probe(p):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                        "format=duration:stream=codec_type,width,height,nb_frames",
                        "-of", "json", p], capture_output=True, text=True)
    return json.loads(r.stdout)


def nframes(p):
    return int([s for s in probe(p)["streams"] if s["codec_type"] == "video"][0]["nb_frames"])


def text_w(d, t, f):
    return sum(d.textlength(c, font=f) for c in t)


def bar_layer(text, max_w=W - 56):
    """字号自适应：写死字号会让长文本两端被硬裁（实测踩过）。"""
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    size, pad = 46, 42
    while size > 16:
        f = ImageFont.truetype(F_ZH, size)
        if text_w(d, text, f) + pad * 2 <= max_w:
            break
        size -= 1
    tw = text_w(d, text, f)
    x0 = W / 2 - tw / 2 - pad
    # 断言：不越界
    assert x0 >= 0 and x0 + tw + pad * 2 <= W, "信息条越界"
    assert BAR_TOP / H > COPY_BOTTOM_LIMIT, "信息条与画面内文案争同一纵带"
    d.rounded_rectangle((x0, BAR_TOP, x0 + tw + pad * 2, BAR_TOP + BAR_H),
                        radius=BAR_H // 2, fill=BG)
    bb = d.textbbox((0, 0), text, font=f)
    d.text((W / 2 - tw / 2, BAR_TOP + (BAR_H - (bb[3] - bb[1])) / 2 - bb[1]),
           text, font=f, fill=WHITE)
    return im, size


def aigc_layer():
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    f = ImageFont.truetype(F_ZH, 24)
    t = "AIGC 生成"
    tw = text_w(d, t, f)
    d.rounded_rectangle((24, H - 56, 24 + tw + 26, H - 20), radius=18, fill=(0, 0, 0, 130))
    d.text((37, H - 50), t, font=f, fill=(255, 255, 255, 190))
    return im


def build_overlay(n, bounds):
    """按 N 段实际时间轴重建中文信息条。bounds = [(t0,t1), ...]"""
    out = os.path.join(D, "post_shop/overlay")
    os.makedirs(out, exist_ok=True)
    manifest = []
    for i in range(n):
        t0, t1 = bounds[i]
        txt = ZH_TEXTS[i] if i < len(ZH_TEXTS) else ZH_TEXTS[-1]
        im, sz = bar_layer(txt)
        p = os.path.join(out, "seg%d.png" % (i + 1))
        im.save(p)
        manifest.append(dict(idx=i + 1, t0=round(t0, 4), t1=round(t1, 4),
                             png="post_shop/overlay/seg%d.png" % (i + 1), zh=txt, pt=sz))
        print("  段%d  %.3f–%.3f  %s（%dpt）" % (i + 1, t0, t1, txt, sz))
    aigc_layer().save(os.path.join(out, "aigc.png"))
    json.dump(manifest, open(os.path.join(D, "post_shop/overlay_manifest.json"), "w"),
              ensure_ascii=False, indent=1)
    return manifest


def assemble(clips, out, total, overlays=None):
    n = len(clips)
    cmd = ["ffmpeg", "-y", "-loglevel", "error"]
    for c in clips:
        cmd += ["-i", c]
    if overlays:
        for m in overlays:
            cmd += ["-i", os.path.join(D, m["png"])]
        cmd += ["-i", os.path.join(D, "post_shop/overlay/aigc.png")]

    fc = ["%sconcat=n=%d:v=1:a=0[vcat]"
          % ("".join("[%d:v]" % i for i in range(n)), n)]
    prev = "vcat"
    if overlays:
        for i, m in enumerate(overlays):
            idx = n + i
            fc.append("[%s][%d:v]overlay=0:0:enable='between(t,%.4f,%.4f)'[ov%d]"
                      % (prev, idx, m["t0"], m["t1"], i))
            prev = "ov%d" % i
        fc.append("[%s][%d:v]overlay=0:0[vov]" % (prev, n + len(overlays)))
        prev = "vov"
    fc.append("[%s]format=yuv420p[vout]" % prev)

    # 音频：逐接缝交叉淡化，再补静音到画面总长
    a = "[0:a]"
    for i in range(1, n):
        fc.append("%s[%d:a]acrossfade=d=%.2f:c1=tri:c2=tri[a%d]"
                  % (a, i, XFADE, i))
        a = "[a%d]" % i
    fc.append("%saformat=sample_fmts=fltp:channel_layouts=stereo,"
              "apad,atrim=0:%.4f,asetpts=PTS-STARTPTS,"
              "loudnorm=I=-16:TP=-1.5:LRA=11[aout]" % (a, total))

    cmd += ["-filter_complex", ";".join(fc), "-map", "[vout]", "-map", "[aout]",
            "-r", str(FPS), "-c:v", "libx264", "-preset", "slow", "-crf", "18",
            "-profile:v", "high", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "160k", "-ar", "44100", "-ac", "2",
            "-movflags", "+faststart", "-shortest", out]
    run(cmd)


def main():
    clips = sys.argv[1:]
    if len(clips) < 2:
        print(__doc__)
        sys.exit(1)
    ns = [nframes(c) for c in clips]
    total = sum(ns) / FPS
    print("顺序与帧数：")
    acc = 0
    bounds = []
    for c, k in zip(clips, ns):
        t0, t1 = acc / FPS, (acc + k) / FPS
        bounds.append((t0, t1))
        print("   %-28s %3d 帧  %.3f–%.3f s" % (os.path.basename(c), k, t0, t1))
        acc += k
    print("   合计 %d 帧 / %.3f s" % (sum(ns), total))

    ov = build_overlay(len(clips), bounds)

    assemble(clips, os.path.join(D, "DYNAMIC_SOP_FILM.mp4"), total, None)
    assemble(clips, os.path.join(D, "DYNAMIC_COMMERCE.mp4"), total, ov)

    for f in ("DYNAMIC_SOP_FILM.mp4", "DYNAMIC_COMMERCE.mp4"):
        p = os.path.join(D, f)
        v = [s for s in probe(p)["streams"] if s["codec_type"] == "video"][0]
        print("  %-24s %sx%s  %s 帧  %.3fs  %.2f MB"
              % (f, v["width"], v["height"], v["nb_frames"],
                 float(probe(p)["format"]["duration"]), os.path.getsize(p) / 1048576))


if __name__ == "__main__":
    main()
