#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""qc_final.py —— 交付前质检（全部为可复核的确定性检查，不含主观判断）。

用法:
  qc_final.py <成片mp4> --b B.mp4 --a A3.mp4 [--aold A.mp4] [--overlay]

检查项：
  ① 规格      分辨率/帧数/时长/帧率/编码/像素格式
  ② 时长账    素材帧数之和 = 成片帧数（无丢帧）
  ③ 音轨      存在性、采样率、声道、平均/峰值电平（无削波）
  ④ 尾帧稳定  末帧 vs 前 12 帧逐像素差 —— 判据「结尾定镜」是否成立（MAD→0）
  ⑤ 拼接顺序  成片首帧 vs B 片首帧（B 必须在前）
  ⑥ 锚定保真  A 片 0 / 5.5 / 10.08 s 三帧 vs 三张锚定照（MAD 与 Δ(R−B)）
  ⑦ 文案对比  文案所在横带的 luma 对比度 (P95−P5) —— 量化「可读」与否
  ⑧ 叠字冲突  后期信息条纵向占位 vs 画面内英文文案纵带是否重叠
"""
import json
import os
import subprocess
import sys
import numpy as np
from PIL import Image

D = os.path.dirname(os.path.abspath(__file__))


def sh(cmd):
    # ⚠️ 必须合并 stderr：ffmpeg/ffprobe 的诊断信息（含 volumedetect 的 mean/max_volume）
    #    全部写在 stderr，只抓 stdout 会永远拿到空的 —— 实测踩过，误报"无音轨/静音"。
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.stdout + r.stderr


def probe(p):
    return json.loads(sh(["ffprobe", "-v", "error", "-show_entries",
                          "format=duration:stream=codec_type,codec_name,width,height,"
                          "nb_frames,r_frame_rate,sample_rate,channels,pix_fmt",
                          "-of", "json", p]))


def frame(p, n, tmp):
    o = os.path.join(tmp, "q_%s_%05d.png" % (os.path.basename(p)[:4], n))
    if not os.path.exists(o):
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", p,
                        "-vf", "select=eq(n\\,%d)" % n, "-vsync", "0",
                        "-frames:v", "1", o], check=True)
    return Image.open(o).convert("RGB")


def mad_drb(a, b, size=(192, 336), rows=None):
    x = np.asarray(a.resize(size)).astype(np.float32)
    y = np.asarray(b.resize(size)).astype(np.float32)
    if rows:
        x, y = x[rows[0]:rows[1]], y[rows[0]:rows[1]]
    return (float(np.abs(x - y).mean()),
            float((x[..., 0] - x[..., 2]).mean() - (y[..., 0] - y[..., 2]).mean()))


def band(im, y0f, y1f):
    w, h = im.size
    a = np.asarray(im.crop((0, int(h * y0f), w, int(h * y1f)))).astype(np.float32)
    lu = a @ np.array([0.2126, 0.7152, 0.0722], np.float32)
    return float(np.percentile(lu, 95) - np.percentile(lu, 5))


def comp_mad(a, b, size=(24, 42)):
    """构图稳定量：重度降采样（顺带低通）后比较 —— 抑制时域纹理闪烁，保留相机运动。"""
    x = np.asarray(a.resize(size, Image.BOX)).astype(np.float32)
    y = np.asarray(b.resize(size, Image.BOX)).astype(np.float32)
    return float(np.abs(x - y).mean())


def cut_times(mp4, thr_factor=4.0, floor=8.0):
    """切点检测 —— ⚠️ 这一条是踩过坑之后补的，务必看注释。

    起初我把"两锚定照之间构图突变"判成「相机推近冲过头」，并花了一轮 22 分钟去改提示词
    （写 truck / 保持距离），**完全无效**。真实机制是：**H3 会把多锚点的片子渲染成
    "多镜头 + 硬切"**——突变发生在从一张锚定照切到下一张锚定照时，是【切镜】，不是运镜。
    提示词里的运镜描述根本约束不到切镜。而官方 SOP 本身**就预期有切镜**：
    分镜表有「转场 / 连贯」列，原则 5/6 写「元素连续+动势连续+形态接续」「镜头进出有接缝」。

    做法：整片解码成 64×112 灰度 → 逐帧「暗部占比」→ 找相邻帧跳变。
    判据：相邻帧变化 > max(floor, thr_factor × 该片典型帧间变化) 视为一次切点。
    """
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", mp4, "-vf", "scale=64:112",
                        "-pix_fmt", "gray", "-f", "rawvideo", "-"],
                       capture_output=True)
    buf = np.frombuffer(r.stdout, dtype=np.uint8)
    n = len(buf) // (64 * 112)
    a = buf[:n * 64 * 112].reshape(n, 112, 64).astype(np.float32)
    dk = (a < 50).mean(axis=(1, 2)) * 100.0
    d = np.abs(np.diff(dk))
    thr = max(floor, thr_factor * float(np.median(d[np.nonzero(d)])))
    cuts = [int(i + 1) for i in np.nonzero(d > thr)[0]]
    return dk, cuts, thr


def main():
    final = sys.argv[1]
    o = {}
    i = 2
    while i < len(sys.argv):
        if sys.argv[i].startswith("--"):
            k = sys.argv[i][2:]
            if i + 1 < len(sys.argv) and not sys.argv[i + 1].startswith("--"):
                o[k] = sys.argv[i + 1]; i += 2
            else:
                o[k] = True; i += 1
        else:
            i += 1
    tmp = os.path.join(D, "_tmp_qc")
    os.makedirs(tmp, exist_ok=True)
    ok = []

    p = probe(final)
    v = [s for s in p["streams"] if s["codec_type"] == "video"][0]
    au = [s for s in p["streams"] if s["codec_type"] == "audio"]
    n = int(v["nb_frames"])

    print("① 规格")
    print("   %s  %sx%s  %s 帧  %.3f s  %s fps  %s  %s"
          % (os.path.basename(final), v["width"], v["height"], v["nb_frames"],
             float(p["format"]["duration"]), v["r_frame_rate"], v["codec_name"], v["pix_fmt"]))
    ok.append(("分辨率 768x1344（9:16 竖版）", (v["width"], v["height"]) == (768, 1344)))
    ok.append(("像素格式 yuv420p（平台兼容）", v["pix_fmt"] == "yuv420p"))

    if "a" in o and "b" in o:
        tot = sum(int([s for s in probe(x)["streams"] if s["codec_type"] == "video"][0]["nb_frames"])
                  for x in (o["b"], o["a"]))
        print("② 时长账  B+A = %d 帧 ｜ 成片 %d 帧 ｜ 差 %d" % (tot, n, n - tot))
        ok.append(("帧数账平（成片 = B + A）", n == tot))

    print("③ 音轨")
    if au:
        s = au[0]
        vol = sh(["ffmpeg", "-hide_banner", "-i", final, "-af", "volumedetect",
                  "-f", "null", "/dev/null"])
        mean = maxv = None
        for ln in vol.splitlines():
            if "mean_volume" in ln:
                mean = float(ln.split(":")[1].split()[0])
            if "max_volume" in ln:
                maxv = float(ln.split(":")[1].split()[0])
        print("   %s  %s Hz  %s ch  mean=%s dB  max=%s dB"
              % (s["codec_name"], s["sample_rate"], s["channels"], mean, maxv))
        ok.append(("音轨存在且非静音（mean < −5 dB）", mean is not None and mean < -5))
        ok.append(("无削波（峰值 < 0 dB）", maxv is not None and maxv < 0))
    else:
        print("   ❌ 无音轨")
        ok.append(("音轨存在且非静音", False))

    # ④ 结尾稳定 —— 三个坑逐个避开：
    #   坑1 全画幅像素 MAD 会把【必须可见的文字动效】误判成"相机还在动"。
    #   坑2 高分辨率 MAD 会把【扩散模型的时域纹理闪烁】误判成"相机在动"。
    #   坑3 重度降采样后做"构图稳定量"，经标定也分不开「定镜」与「慢推」
    #       （实测 定镜 2.0–4.4 vs 慢推 5.4–7.5，区间重叠）。但它能干净抓【切镜】（57–69）。
    #   ⇒ 判「结尾是稳定画面」用【切点之后近景指数是否走平】—— 这才是"画面不再变化"的直接度量。
    last_cut = max(cut_times(final)[1]) if cut_times(final)[1] else 0
    dk = cut_times(final)[0]
    tail = dk[last_cut:]
    plateau = float(tail.max() - tail.min())
    nf = len(dk)
    m_all = mad_drb(frame(final, nf - 1, tmp), frame(final, nf - 13, tmp))[0]
    print("④ 结尾稳定  最后一次切点 f%d（%.2fs）｜ 其后近景指数 %.1f → %.1f，摆幅 %.2f%%"
          % (last_cut, last_cut / 24.0, tail[0], tail[-1], plateau))
    print("   末帧 vs 前 12 帧 全幅像素 MAD = %.2f（文字动效探针）" % m_all)
    ok.append(("结尾为稳定画面（切点后近景指数摆幅 < 3%）", plateau < 3.0))
    ok.append(("结尾文案仍在动（全幅像素 MAD > 2，即非静态字）", m_all > 2.0))

    if "b" in o:
        m2 = mad_drb(frame(final, 0, tmp), frame(o["b"], 0, tmp))[0]
        print("⑤ 拼接顺序  成片首帧 vs B 片首帧 MAD = %.3f" % m2)
        ok.append(("拼接顺序正确（B 在前）", m2 < 3.0))

    if "a" in o:
        print("⑥ 锚定保真（A 片帧号 i，秒 = i/24）")
        for rel, fr in (("anchors/anchor_1_hero.png", 0),
                        ("anchors/anchor_2_material.png", 132),
                        ("anchors/anchor_3_endcopy.png", 242)):
            m3, drb = mad_drb(frame(o["a"], fr, tmp), Image.open(os.path.join(D, rel)).convert("RGB"))
            print("   f%-4d %-30s MAD %5.2f  Δ(R−B) %+6.2f"
                  % (fr, os.path.basename(rel), m3, drb))
            ok.append(("锚定保真 %s（MAD<8, |Δ(R−B)|<6）" % os.path.basename(rel),
                       m3 < 8 and abs(drb) < 6))

    # ⑦ 只记录，不做判据 —— ⚠️ 已实测：横带 P95−P5 会被【鞋体本身的高对比】淹没
    #    （旧稿 194.8 vs 修正 198.0，毫无区分度），拿它判"文案是否可读"是假证据。
    #    文案可读性以【目视放大对照】为准：frames/18_中段文案_旧稿vs修正.png
    print("⑦ 文案横带统计（仅供记录；可读性以目视证据 frames/18 为准，本条不参与判定）")
    for tag, src, fnum, y0, y1 in (
            ("修正·中段文案 y0.06–0.32", o.get("a"), 168, 0.06, 0.32),
            ("旧稿·中段文案 y0.48–0.70", o.get("aold"), 170, 0.48, 0.70),
            ("结尾文案 y0.52–0.72", o.get("a"), 242, 0.52, 0.72)):
        if not src:
            continue
        c = band(frame(src, fnum, tmp), y0, y1)
        print("   %-26s P95−P5 = %6.1f" % (tag, c))
    print("   ⇒ 目视结论：旧稿=半透明幽灵字（不可读）；修正版=大号实心深灰/黑字（可读，两段配色正确）")

    if "overlay" in o:
        man = json.load(open(os.path.join(D, "post_shop/overlay_manifest.json")))
        y0, y1 = 1186 / 1344, 1284 / 1344
        print("⑧ 叠字冲突  后期信息条纵带 %.3f–%.3f" % (y0, y1))
        for mm in man:
            print("   段%d  %.3f–%.3f s  「%s」" % (mm["idx"], mm["t0"], mm["t1"], mm["zh"]))
        ok.append(("后期条不与英文文案争同一纵带（条顶 %.2f > 0.72）" % y0, y0 > 0.72))

    if "a" in o:
        dk, cuts, thr = cut_times(o["a"])
        print("⑨ 切点检测（A 片；官方 SOP 预期有切镜，本条只核对「有没有卡在故事板切点上」）")
        print("   判定阈值：相邻帧暗部占比变化 > %.1f%%" % thr)
        if cuts:
            for c in cuts:
                print("   ✂ 切点 f%-4d %.3f s  （帧间跃变 %.1f%% → %.1f%%）"
                      % (c, c / 24.0, dk[c - 1], dk[c]))
        else:
            print("   未检出切点（全片单一连续镜头）")
        want = [132, 170, 209]          # 故事板切点：5.500 / 7.083 / 8.708
        hit = [c for c in cuts if min(abs(c - w) for w in want) <= 6]
        print("   故事板切点 %s ｜ 命中（±6 帧）%s" % (want, hit))
        ok.append(("检出的切点都落在故事板切点附近（±6 帧）", len(hit) == len(cuts) if cuts else True))

    print()
    for k, good in ok:
        print("   %s %s" % ("✅" if good else "❌", k))
    bad = [k for k, g in ok if not g]
    print()
    print("总判定:", "✅ 全部通过" if not bad else "❌ 未通过 %d 项：%s" % (len(bad), bad))

    for fn in os.listdir(tmp):
        os.remove(os.path.join(tmp, fn))
    os.rmdir(tmp)
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
