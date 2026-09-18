#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
canvas_fit.py —— 把任意比例的商品图「零重绘」几何适配到 H3 指定画布。

为什么必须做这一步（Gate-P P-07）：
    实测 H3 源码 comfy_extras/nodes_minimax_h3.py：
      · first_frame → plain stretch（直接拉伸，比例不符 = 静默变形）
      · last_frame  → aspect-preserving cover-crop（保比例裁剪，比例不符 = 静默丢构图）
    两者都【不报错】。所以提交前必须让图与画布【逐像素同尺寸】。

为什么不交给 TT Image2 重绘：
    实测 TT Image2 的 edit 会改变产品细节（鞋型/纹理），且其「状态跃迁」能力弱。
    商品图的几何适配应该是【无损的纯几何操作】，重绘只用于「制造动作状态差」。

算法（背景自然外推，不是纯色填充）：
    ① 按画布宽度等比缩放原图 → 得到 h0 高
    ② 用左右边缘空白列取每行背景代表色
    ③ 对顶部/底部若干行的背景色做线性外推（斜率带阻尼防过冲）
    ④ 外推色填充上/下延展区，与原图边缘天然连续

用法：
    python canvas_fit.py 输入图 宽x高 输出图 [--anchor center|top|bottom] [--debug]
例：
    python canvas_fit.py 主图.png 768x1344 first.png
"""
import argparse
import os
import sys

import numpy as np
from PIL import Image


def row_bg_colors(arr, margin=30):
    """每行的背景代表色：取左右两侧空白列的中位数（抗产品溢出干扰）。"""
    h, w, _ = arr.shape
    m = min(margin, w // 4)
    left = arr[:, :m, :].astype(np.float64)
    right = arr[:, w - m:, :].astype(np.float64)
    return np.median(np.concatenate([left, right], axis=1), axis=1)   # (h,3)


def extrapolate(bg, n, edge_row, from_top=True, fit_rows=60, damp=0.7, tau=40.0):
    """外推 n 行背景，返回 (n, W, 3)。

    ⚠️ 两条实测教训（缺一个就出现肉眼可见的硬接缝）：
    ① 必须【饱和外推】而非线性外推 —— 补 288 行时 线性(斜率×288) 会跑飞出界；
       用 offset(d) = slope * tau * (1 - exp(-d/tau))，d→∞ 时偏移收敛到有限值。
    ② 色带不能是【单色均匀】的 —— 原图边缘整行本身有横向变化（暗角/光晕），
       直接铺单色会丢掉它，接缝照样露馅。⇒ 沿用 edge_row 整行，再叠加逐列偏移。
    """
    w, _ = edge_row.shape                      # edge_row 为单行 (W,3)
    out = np.repeat(edge_row[None, :, :].astype(np.float64), n, axis=0)   # (n,W,3)

    if from_top:
        rows = np.arange(fit_rows)
        seg = bg[:fit_rows]
    else:
        rows = np.arange(fit_rows)
        seg = bg[-fit_rows:]

    offsets = np.zeros(3, dtype=np.float64)
    for c in range(3):
        slope = float(np.polyfit(rows, seg[:, c], 1)[0])
        slope = float(np.clip(slope * damp, -3.0, 3.0))
        d_inf = slope * tau * (1.0 - np.exp(-n / tau))                    # 最远处偏移
        offsets[c] = d_inf

    for i in range(n):
        d = (n - i) if from_top else (i + 1)          # 距原图边缘的行数
        ratio = (1.0 - np.exp(-d / tau)) / max(1e-6, (1.0 - np.exp(-n / tau)))
        out[i] += offsets[None, :] * ratio
    return np.clip(out, 0, 255)


def add_noise_band(colors, sigma=1.2, seed=0):
    """给纯色带加入极轻的噪声，避免出现「塑料感」色块并被压缩器放大成 banding。"""
    rng = np.random.default_rng(seed)
    return np.clip(colors + rng.normal(0, sigma, colors.shape), 0, 255)


def mirror_band(arr, n, from_top=True, decay_to=0.35, ramp=None):
    """镜像延展：直接翻转原图自身靠近边缘的 n 行。

    ⭐ 这是最终采用的做法，理由：
      商品图背景多为【径向光晕】。任何"从边缘往外推"的数值外推都只能延续
      纵向色值趋势，却无法延续【光晕的横向衰减形状】⇒ 人眼永远读得出分界。
      镜像则天然继承完整的光晕几何：接缝处逐像素连续（因为就是同一行），
      远处的衰减形状也正确（像一个自然的上下对称影棚背景）。
    再叠加【光晕振幅缓降】避免整幅看起来是明显的镜像对称。
    """
    h0 = arr.shape[0]
    n = min(n, h0)                                   # 镜像最多取整幅高
    src = arr[:n] if from_top else arr[-n:]
    band = src[::-1].astype(np.float64).copy()
    if not ramp:
        ramp = float(n)
    for i in range(n):
        d = n - i                                    # 距接缝的行数
        t = min(1.0, d / ramp)
        w = t * decay_to                             # 远处往"平坦"靠一点
        if w > 0:
            flat = band[i].mean(axis=0, keepdims=True)
            band[i] = band[i] * (1.0 - w) + flat * w
    return band


def feather_profile(band, edge_row, ramp=None, floor=0.15):
    """光晕振幅渐进衰减 + 横向羽化 —— 消除「剖面被冻结」造成的视觉分界。

    ⚠️ 踩过的坑（三层，逐层才修干净）：
      ① 线性外推 288 行 → 跑飞出界，接缝跳变 24.6/255；
      ② 换饱和外推 + 单色带 → 降到 0.5/255，但【肉眼仍看得到分界】——
         因为延展区每一行的横向剖面被【冻死】，而原图的光晕在逐行扩散，
         人眼抓的是「纹理模式突变」，不是「色值跳变」；
      ③ 大核模糊羽化 → 仍不够，因为模糊只是把光晕摊宽（std 12.8→9.7），
         远端依然是「有光晕但形状不同」，读起来还是两块。
    ⇒ 正解：把横向剖面拆成「行均值（承载纵向渐变）+ 中心化光晕」两部分，
      只对【光晕振幅】做线性衰减（接缝处 α=1 保证连续，远端 α=floor 收敛为平坦），
      光晕形状不动。这才读得出「背景向远处延伸」。
    """
    n = band.shape[0]
    if not ramp:
        ramp = float(n)
    e = edge_row.astype(np.float64)
    center = e - e.mean(axis=0, keepdims=True)          # (W,3) 中心化光晕
    out = np.empty_like(band)
    for i in range(n):
        d = n - i                                       # 距原图边缘的行数
        t = min(1.0, d / ramp)
        alpha = 1.0 - (1.0 - floor) * t                 # 1.0 → floor
        out[i] = band[i].mean(axis=0, keepdims=True) + alpha * center
    return out


def trim_border_rows(im, tol=3.0, probe=8, max_trim=4):
    """裁掉源图的异常边缘行。

    ⚠️ 实测踩坑：源图（AI 生图导出）第 0 行/最后 1 行常是【异常亮边框】
    （实测 163.8 vs 内部 154，Δ=9.6）。不裁的话，延展带会把这个亮边继承过去，
    在接缝内侧留下一条肉眼可见的亮线 —— 而且它不是"接缝"，是"源图自带"，
    所以怎么调外推参数都治不好。
    """
    arr = np.asarray(im.convert("RGB"), dtype=np.float64)
    rows = arr.mean(axis=(1, 2))
    top = bot = 0
    for i in range(max_trim):
        if abs(rows[i] - np.median(rows[probe:probe * 3])) > tol:
            top = i + 1
    for i in range(max_trim):
        if abs(rows[-1 - i] - np.median(rows[-probe * 3:-probe])) > tol:
            bot = i + 1
    if top or bot:
        w, h = im.size
        return im.crop((0, top, w, h - bot)), (top, bot)
    return im, (0, 0)


def fit_canvas(src, tw, th, anchor="center", debug=False):
    im = Image.open(src).convert("RGB")
    im, trimmed = trim_border_rows(im)
    sw, sh = im.size
    if debug and (trimmed[0] or trimmed[1]):
        print(f"    裁掉异常边缘行: 上 {trimmed[0]} px / 下 {trimmed[1]} px")

    # ① 按画布宽度等比缩放
    h0 = max(1, round(sh * tw / sw))
    scaled = im.resize((tw, h0), Image.LANCZOS)
    arr = np.asarray(scaled, dtype=np.float64)

    if debug:
        print(f"    源 {sw}x{sh} → 缩放后 {tw}x{h0}（画布 {tw}x{th}）")

    if h0 >= th:
        # 图比画布更高：按 anchor 裁切（保比例，不拉伸）
        if anchor == "top":
            top = 0
        elif anchor == "bottom":
            top = h0 - th
        else:
            top = (h0 - th) // 2
        out = arr[top:top + th, :, :]
        if debug:
            print(f"    等比已够高 → 裁切 y={top}..{top+th}")
        return Image.fromarray(out.astype(np.uint8)), "crop"

    # ② 需要补背景：上/下延展
    pad_total = th - h0
    if anchor == "top":
        pad_top, pad_bot = 0, pad_total
    elif anchor == "bottom":
        pad_top, pad_bot = pad_total, 0
    else:
        pad_top = pad_total // 2
        pad_bot = pad_total - pad_top

    bg = row_bg_colors(arr)
    top_band = bottom_band = None
    for n_pad, from_top in ((pad_top, True), (pad_bot, False)):
        if n_pad <= 0:
            continue
        edge = arr[0] if from_top else arr[-1]
        band = extrapolate(bg, n_pad, edge, from_top=from_top)       # 饱和外推（不会过冲）
        band = feather_profile(band, edge, ramp=n_pad)               # 光晕振幅缓降
        tag = "饱和外推+光晕缓降"
        band = add_noise_band(band)
        if debug:
            print(f"    {'上' if from_top else '下'}延展 {n_pad} px（{tag} + 光晕缓降 + 轻噪声）")
        if from_top:
            top_band = band
        else:
            bottom_band = band

    parts = [p for p in (top_band, arr, bottom_band) if p is not None]

    out = np.concatenate(parts, axis=0)
    out = np.clip(out, 0, 255)

    if debug:
        print(f"    延展背景 上 {pad_top} px / 下 {pad_bot} px（饱和外推 + 横向羽化 + 轻噪声）")
    return Image.fromarray(out.astype(np.uint8)), "pad"


def main():
    ap = argparse.ArgumentParser(description="商品图零重绘几何适配到 H3 画布")
    ap.add_argument("src")
    ap.add_argument("size", help="目标画布，如 768x1344")
    ap.add_argument("dst")
    ap.add_argument("--anchor", default="center", choices=["center", "top", "bottom"])
    ap.add_argument("--debug", action="store_true")
    a = ap.parse_args()

    tw, th = (int(x) for x in a.size.lower().split("x"))

    # 对齐校验：H3 画布必须是 32 的倍数
    if tw % 32 or th % 32:
        print(f"  ⚠️ 画布 {tw}x{th} 不是 32 的倍数，H3 可能拒绝")

    if a.debug:
        print(f"  输入: {a.src}")
    out, mode = fit_canvas(a.src, tw, th, anchor=a.anchor, debug=a.debug)
    os.makedirs(os.path.dirname(os.path.abspath(a.dst)) or ".", exist_ok=True)
    out.save(a.dst)

    w, h = out.size
    print(f"  {mode:4s} → {w}x{h}  比例 {w/h:.4f}  → {a.dst}")
    if (w, h) != (tw, th):
        print(f"  ❌ 尺寸不符，期望 {tw}x{th}")
        sys.exit(1)


if __name__ == "__main__":
    main()
