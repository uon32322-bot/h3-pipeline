#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
canvas_motion.py —— 用【纯几何变换】从首帧构造尾帧（镜面运动，零重绘）。

为什么需要它（实测结论）：
    H3 是「首尾帧的忠实插值器」—— 动作幅度由首尾帧决定，提示词只负责风格。
    而 AI 图片编辑（TT Image2 的 edit）实测【做不出状态跃迁】：
      · 要求"鞋身旋转 25°" → 实际只改了光影 + 重绘了鞋面材质（产品失真 ❌）
      · 要求"杯子旋转 45°" → 实际角度完全没变，只改了光影
    ⇒ 对【产品保真】要求高的带货素材，图片层造差异必须走纯几何路线：
      缩放/平移/旋转/光影 —— 数学变换，逐像素可逆、产品零失真。

支持的运动（可叠加）：
    --zoom 1.06        推近（相机靠近）
    --pan 0,20         平移（x,y 像素；正 y = 画面上移 = 产品下移）
    --rotate 2.5       绕中心旋转（度）
    --light 0.06       光影扫动：加一层水平渐变光（0=不加）
    --tint 255,220,180,0.05   轻微暖色调（R,G,B,强度）

用法：
    python canvas_motion.py first.png last.png --zoom 1.06 --light 0.06
"""
import argparse
import os
import sys

import numpy as np
from PIL import Image


def apply_motion(im, zoom=1.0, focus=None, pan=(0, 0), rotate=0.0, light=0.0, tint=None):
    """纯几何变换：旋转 → 缩放 → 按焦点裁切窗口 → 光影/染色。

    ⚠️ 关键设计：**焦点裁切窗口**取代了早期的「中心裁切 + np.roll 平移」。
    实测教训：roll 在位移较大时会把溢出的内容环绕回来，再用边缘行回填 ⇒
    画面顶部/底部出现**整行拉伸的色块**（实测 zoom 1.85 + pan 300 时，
    模特头部被切掉、脖子以上变成一整条肤色带）。且 roll 的方向与
    「想看画面下方」的直觉相反，容易调反。
    ⇒ 改为在放大图层面上**自由选择裁切窗口位置**（focus 归一化坐标 + pan 细调），
      clip 到合法范围 ⇒ 既不会露黑边，也不会有环绕伪影，且构图完全可控。

    focus: (fx, fy) 归一化焦点，0..1；None = 画面正中 (0.5, 0.5)。
           例：推到画面下方的脚部 → focus=(0.5, 0.88)
    pan:   (px, py) 在 focus 基础上的额外像素偏移（原图坐标系，正 y = 看到更下方的内容）
    """
    w, h = im.size
    out = im.convert("RGB")

    # 旋转（先旋转再缩放裁切，避免露边靠裁切范围兜住）
    if rotate:
        out = out.rotate(rotate, resample=Image.BICUBIC, expand=False)

    if zoom != 1.0 or focus is not None or pan != (0, 0):
        z = max(1.0, float(zoom))          # 只放大不缩小，避免出现无内容的边框
        zw, zh = round(w * z), round(h * z)
        big = out.resize((zw, zh), Image.LANCZOS) if z != 1.0 else out

        fx, fy = focus if focus else (0.5, 0.5)
        cx = fx * w * z + pan[0]           # 窗口中心（放大图坐标）
        cy = fy * h * z + pan[1]
        left = int(round(cx - w / 2.0))
        top = int(round(cy - h / 2.0))
        left = max(0, min(left, zw - w))   # clip 保证不越界
        top = max(0, min(top, zh - h))
        out = big.crop((left, top, left + w, top + h))

    a = np.asarray(out, dtype=np.float64)

    # 光影扫动：水平方向的柔和亮带（模拟光源扫过）
    if light:
        x = np.linspace(-1, 1, w)
        band = np.exp(-((x - 0.15) ** 2) / (2 * 0.45 ** 2))     # 峰值偏右的柔光
        a += (band[None, :, None] * 255.0 * float(light))

    if tint:
        r, g, b, s = tint
        color = np.array([r, g, b], dtype=np.float64) / 255.0
        a = a * (1.0 - s) + (a * color[None, None, :]) * s

    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))


def main():
    ap = argparse.ArgumentParser(description="用纯几何变换从首帧构造尾帧")
    ap.add_argument("first")
    ap.add_argument("last")
    ap.add_argument("--zoom", type=float, default=1.0)
    ap.add_argument("--focus", default=None, help="焦点 fx,fy（归一化 0-1），如 0.5,0.88 = 推到画面下方")
    ap.add_argument("--pan", default="0,0", help="在 focus 基础上的额外偏移 x,y 像素（正 y = 看到更下方）")
    ap.add_argument("--rotate", type=float, default=0.0)
    ap.add_argument("--light", type=float, default=0.0)
    ap.add_argument("--tint", default=None, help="R,G,B,强度 如 255,220,180,0.05")
    a = ap.parse_args()

    im = Image.open(a.first).convert("RGB")
    pan = tuple(int(v) for v in a.pan.split(","))
    focus = tuple(float(v) for v in a.focus.split(",")) if a.focus else None
    tint = tuple(float(v) for v in a.tint.split(",")) if a.tint else None

    out = apply_motion(im, zoom=a.zoom, focus=focus, pan=pan,
                       rotate=a.rotate, light=a.light, tint=tint)
    os.makedirs(os.path.dirname(os.path.abspath(a.last)) or ".", exist_ok=True)
    out.save(a.last)

    d = np.abs(np.asarray(im, dtype=np.int16) - np.asarray(out, dtype=np.int16)).max(axis=2)
    print(f"  首帧 {im.size} → 尾帧 {out.size}")
    print(f"  运动: zoom={a.zoom} pan={pan} rotate={a.rotate}° light={a.light} tint={tint}")
    print(f"  首尾差异: max={d.max()} 平均={d.mean():.2f}  （复现噪声基线≈1.08）")
    if out.size != im.size:
        print("  ❌ 尺寸不一致，P-07 会拒绝")
        sys.exit(1)


if __name__ == "__main__":
    main()
