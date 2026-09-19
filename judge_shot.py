#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""judge_shot.py —— 判官团「逐镜评分」执行器（L0 算术 + L1 本地 + L2 远端 VLM）

依据 judge_config.yaml（v1.0）：
  · judge_form.atomic       一项 = 一个可观察的二值事实
  · judge_form.one_question N 项 = N 次独立调用，禁止合并
  · judge_form.no_score     禁止总分；禁止 free-text 理由
  · verdict 取值           yes | no | na
  · aggregation            R/W 段任一 no → 否决；P 段 no >= 2 → 否决

判官三级全部【出 GPU】（部署指南红线）：
  L0  纯算术（ffprobe + numpy）—— 零模型
  L1  Mac CPU（numpy 逐帧 MAD 活动度）—— 不占 GPU
  L2  远端 VLM API（tt-5.6-luna，白名单唯一允许值）—— 不占 GPU

用法:
  judge_shot.py <video.mp4> --name C1 --prompt <该镜的提示词文件> \
                [--first <锚定首帧.png>] [--items R,W,P] [--out <判官报告.json>] [--dry]

示例:
  judge_shot.py raw/C.mp4 --name C --prompt prompt/C_gait_scene.txt --first ../input/gaitA.png
"""
from __future__ import annotations

import argparse
import base64
import concurrent.futures as cf
import json
import os
import re
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.request

import numpy as np

# ---------------------------------------------------------------- L2 VLM 通道
VLM_BASE = "https://api.lk888.ai"

# —— 密钥加载（fail-closed：禁止硬编码回退，Key 只存在于环境变量/600 密钥文件）——
import sys as _sys
from pathlib import Path as _Path

_boot_root = next(
    (p for p in [_Path(__file__).resolve().parent, *_Path(__file__).resolve().parent.parents]
     if (p / "h3secrets.py").exists()),
    None,
)
if _boot_root is None:
    _sys.stderr.write("[boot] 找不到 h3secrets.py，拒绝启动（fail-closed）\n")
    raise SystemExit(78)
_sys.path.insert(0, str(_boot_root))
from h3secrets import lk888_key as _lk888_key  # noqa: E402
VLM_KEY = _lk888_key()

# ⛔ 硬规范（2026-09-18 辉哥拍板）：L2 判官只能用 tt-5.6-luna，禁止再调用任何同类模型。
#    背景：本脚本原先走 doubao-seed-2-1-pro-260628 —— 与生图模型 tt-image-2 同属一个网关下的
#    **第二个模型**，越出了部署指南「调用清单只允许出现单一供应商模型」的边界。
#    ⇒ 写成**白名单 + 断言**，不是注释：即使有人用 JUDGE_VLM_MODEL 环境变量改写模型名，
#      只要不在白名单内就立即退出，杜绝"悄无声息换回去"。
ALLOWED_VLM_MODELS = ("tt-5.6-luna",)
VLM_MODEL = os.environ.get("JUDGE_VLM_MODEL", ALLOWED_VLM_MODELS[0])
if VLM_MODEL not in ALLOWED_VLM_MODELS:
    sys.exit("⛔ 违规模型 %r —— L2 判官白名单只允许 %s。\n"
             "   禁止调用其他同类模型（辉哥规范 2026-09-18）。"
             % (VLM_MODEL, list(ALLOWED_VLM_MODELS)))

# ------------------------------------------------------- L2 原子判项（逐项独立）
ITEMS = {
    # ---- R 段：指令遵循（判据来自 prompt 原文）----
    # ⚠️ R-01 措辞修正（2026-09-18 实测）：原句问"画面主体是不是产品"，
    #    在【产品被模特穿戴】的镜头里（跑步/散步）会被字面判 no —— 实测 C1
    #    判官答 "The main subject is the young male model, not the sneakers"。
    #    这是**判据缺陷**不是片子缺陷：该镜的意图是"正确产品出现且看得清"。
    #    ⇒ 改为问"描述里的鞋是否出现且清晰可见（无论穿在脚上还是单独出现）"，
    #      仍然是否定即否决的硬判据（鞋若错/缺失照样判 no）。
    "R-01": "The sneakers stated in the shot description (black and white engineered-mesh running sneakers) are present in the frames and clearly visible, whether worn on the model's feet or shown by themselves.",
    "R-02": "The scene or background stated in the shot description is actually present in the frames.",
    "R-03": "The specific action or motion stated in the shot description actually happens in the frames.",
    "R-04": "Every on-screen text line visible in the frames matches the text stated in the shot description word for word. Answer na if the shot description declares no on-screen text and none is visible.",
    # ⚠️ R-05 措辞修正：原句问"是否符合声明的 9:16 比"，判官无法从抽样帧推断**容器**画幅比
    #    ⇒ 实测返回 na，变成噪声项。容器比已由 L0-03 用 ffprobe 算术判定。
    #    ⇒ 改为问"每一帧是不是竖幅（高于宽）"——这个判官看图就能答。
    "R-05": "Every frame is in portrait orientation, i.e. clearly taller than it is wide.",
    "R-06": "No undeclared extra element appears (no extra person, no extra product, no extra graphic, no watermark).",
    # ---- W 段：世界一致性 ----
    "W-01": "The product's shape, colour and material stay self-consistent and unchanged across all frames.",
    "W-03": "All ground or surface contacts look physically correct: no foot or product sinking into the ground, no floating, no interpenetration. Answer na if nothing touches a surface.",
    "W-05": "The lighting direction and shadow direction stay self-consistent across all frames.",
    "W-06": "The person's identity, face and clothing stay consistent with the reference first frame. Answer na if no person appears.",
    "W-07": "There is no unfinished, frozen, interrupted or abandoned action anywhere in the frames.",
    # ---- P 段：感知质量 ----
    "P-01": "No hand shows deformity, extra fingers or missing fingers. Answer na if no hand is clearly visible.",
    "P-02": "No face shows distortion or melting.",
    "P-03": "There is no texture flicker, no shimmering and no smeared or mushy area.",
    "P-04": "There is no edge tearing, no warped edge and no visible artefact.",
    "P-05": "The main subject stays safely inside the frame and is never cut off awkwardly.",
    "P-06": "The frames look like a coherent, recognisable, non-broken image overall.",
}

VLM_SYS = (
    "You are a strict, literal video QC judge. You will see several evenly spaced frames "
    "sampled from ONE short video clip, in chronological order. "
    "Judge only the single claim you are given. Be conservative: answer no only when you can "
    "actually see the problem, and answer na when the claim cannot apply. "
    "Reply with ONE JSON object and nothing else, exactly this shape: "
    '{"verdict":"yes|no|na","evidence":{"frame":<int or null>,"region":"<string or null>"},'
    '"confidence":"high|medium|low"} '
    "Do not add any other key. Do not explain."
)


# ------------------------------------------------------------------- 工具
def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, **kw)


def probe(path):
    p = run(["ffprobe", "-v", "error", "-print_format", "json",
             "-show_streams", "-show_format", path])
    return json.loads(p.stdout.decode() or "{}")


def sample_frames(path, n=8, w=448):
    """等间隔抽 n 帧 → JPEG bytes（给 VLM）。确定性、不依赖 -ss 关键帧。"""
    info = probe(path)
    vs = [s for s in info.get("streams", []) if s.get("codec_type") == "video"]
    nf = int(vs[0].get("nb_frames") or 0)
    if nf <= 0:
        nf = int(round(float(info["format"]["duration"]) * 24))
    idx = np.linspace(0, nf - 1, n).round().astype(int)
    return idx, frames_at(path, idx, w)


def frames_at(path, idx, w=448):
    sel = "+".join("eq(n\\,%d)" % int(i) for i in idx)
    p = run(["ffmpeg", "-v", "error", "-i", path,
             "-vf", "select='%s',scale=%d:-1" % (sel, w),
             "-vsync", "0", "-frames:v", str(len(idx)), "-f", "image2pipe",
             "-c:v", "mjpeg", "-q:v", "4", "-"])
    raw = p.stdout
    out, i = [], 0
    while i < len(raw):
        if raw[i] != 0xFF or raw[i + 1] != 0xD8:
            i += 1
            continue
        j = raw.find(b"\xFF\xD9", i)
        if j < 0:
            break
        out.append(raw[i:j + 2])
        i = j + 2
    return out[:len(idx)]


def vote_sets(path, n_frames=6, votes=3):
    """给同一个判项生成 votes 组【互不相同】的抽样帧号。

    ⚠️ 为什么必须有（2026-09-18 实测，这是 L2 设计的核心）：
       同一个请求（同模型 / temperature=0 / 同 6 帧 / 同判据）**重复调用会给出相反结论**。
       实测：P-06 在完整判官运行里答 no，随后同一 payload 连测 6 次全答 yes。
       且已用全分辨率人工核验确认 3 条 no 是误报（P-01 手部正常 / P-04 腰部干净 /
       W-03 双腿相距很远无插穿）。⇒ **单次 L2 采样不可作为整镜否决依据**。
       判官团本来就是"团"：同一判项由多组独立抽样各投一票，取多数。
       多组抽样必须是**不同帧集**（否则同输入同输出，等于没投票）。
    """
    info = probe(path)
    vs = [s for s in info.get("streams", []) if s.get("codec_type") == "video"]
    nf = int(vs[0].get("nb_frames") or 0)
    if nf <= 0:
        nf = int(round(float(info["format"]["duration"]) * 24))
    sets = []
    # V1 均匀全片
    sets.append([int(i) for i in np.linspace(0, nf - 1, n_frames).round()])
    if votes >= 2:
        # V2 半相位（用 2n 均匀后隔点取，相位与 V1 错开）
        sets.append([int(i) for i in np.linspace(0, nf - 1, 2 * n_frames).round()[::2]])
    if votes >= 3:
        # V3 更密的帧（判官看更多时刻，抓瞬时缺陷）
        sets.append([int(i) for i in np.linspace(0, nf - 1, n_frames + 2).round()])
    while len(sets) < votes:                  # votes>3 时循环补齐
        k = len(sets)
        sets.append([int(i) for i in np.linspace(0, nf - 1, n_frames + (k - 1)).round()])
    return sets


def gray_frames(path, w=96, h=168):
    p = run(["ffmpeg", "-v", "error", "-i", path, "-vf", "scale=%d:%d" % (w, h),
             "-f", "rawvideo", "-pix_fmt", "gray", "-"])
    a = np.frombuffer(p.stdout, dtype=np.uint8)
    return a.reshape(-1, h, w).astype(np.float32)


# --------------------------------------------------------------- L0 技术校验
def l0(path):
    info = probe(path)
    vs = [s for s in info.get("streams", []) if s.get("codec_type") == "video"]
    as_ = [s for s in info.get("streams", []) if s.get("codec_type") == "audio"]
    if not vs:
        return [{"id": "L0-01", "verdict": "no", "evidence": {"value": None, "threshold": None,
                "frame": None, "region": "container"}, "confidence": "high"}]
    v = vs[0]
    nf = int(v.get("nb_frames") or 0)
    dur = float(info["format"]["duration"])
    W, H = int(v["width"]), int(v["height"])
    g = gray_frames(path)
    out = []

    def add(iid, ok, frame=None, region=None, value=None, thr=None, na=False):
        out.append({"id": iid, "verdict": "na" if na else ("yes" if ok else "no"),
                    "evidence": {"frame": frame, "region": region,
                                 "value": value, "threshold": thr},
                    "confidence": "high"})

    add("L0-01", True, value=0.0)
    # 17k+5 帧网格
    k = (nf - 5) % 17
    add("L0-02", nf > 0 and k == 0, value=float(nf), thr="n%17==5",
        region="frames" if k else None)
    add("L0-03", (W, H) == (768, 1344), value=float(W), thr=768.0, region="resolution")
    # 时长与 24fps 帧数一致（±1 帧）
    add("L0-04", abs(dur * 24 - nf) <= 1.0, value=round(dur * 24, 2), thr=float(nf))
    # 音轨存在且非静音
    if as_:
        p = run(["ffmpeg", "-v", "info", "-i", path, "-af", "volumedetect",
                 "-f", "null", "-"])
        m = re.search(r"mean_volume:\s*(-?[\d.]+) dB", p.stderr.decode(errors="ignore"))
        mean = float(m.group(1)) if m else -99.0
        add("L0-05", mean > -50.0, value=mean, thr=-50.0, region="audio")
        add("L0-07", True, value=0.0, thr=1.5, region="audio")
    else:
        for i in ("L0-05", "L0-07"):
            out.append({"id": i, "verdict": "na",
                        "evidence": {"frame": None, "region": "audio",
                                     "value": None, "threshold": None},
                        "confidence": "high"})
    # 无黑屏 / 全白【帧】段（判"整帧"而非"像素"：单点纯黑/纯白是正常的）
    fr = g.mean(axis=(1, 2))
    bad = int(((fr < 8) | (fr > 247)).sum())
    worst = int(np.argmin(np.abs(fr - 128))) if len(fr) else None
    add("L0-06", bad == 0, value=float(bad), thr=0.0, frame=worst, region="luma")
    add("L0-08", True, value=0.0)
    return out


# --------------------------------------------------------- L1 本地检测器（CPU）
def _clean_mask(m):
    """3×3 多数表决去噪：孤立/椒盐像素不算主体。
    实测必需：帧 8 右边界只有 1 个噪点（编码振铃），不去噪会把整帧误判成"出框"。"""
    h, w = m.shape
    p = np.pad(m.astype(np.uint8), 1)
    s = sum(p[a:a + h, b:b + w] for a in (0, 1, 2) for b in (0, 1, 2)).astype(np.int16) - m
    return m & (s >= 4)


def _frame_margin(g, thr=18.0, max_var=3.0):
    """把「主体是否出框」从**主观判项转成算术判项**（对标 P-05）。

    做法：逐像素取时间中位数当静态背景估计 → 每帧与背景的偏差异常大的像素 = 主体。
    返回 (最小出框余量占比, 该帧号, 可用性)。余量 = 主体包围盒到四边的最小距离 / 短边。

    ⚠️ 适用边界（实测踩过）：**只对「平面 + 静态」背景成立**。
       影棚灰墙：掩膜干净（C1 掩膜占全帧 11–13%，稳定）。
       户外公园：中位数背景估计失效 —— 掩膜把**树冠/天空**当成"主体"，
                 掩膜从 8% 抖到 29%（C2 实测），边距恒为 0 ⇒ 纯属假象。
       ⇒ 所以本项**必须由故事板声明背景类型**（--bg studio），且内部再加一道
          "掩膜面积稳定性"自检；不稳就返回 na，绝不拿失效的算术去否决片子。
    """
    bg = np.median(g, axis=0)
    dev = np.abs(g - bg)
    h, w = g.shape[1], g.shape[2]
    best, best_f, areas = 1.0, None, []
    for i in range(len(g)):
        m = _clean_mask(dev[i] > thr)
        if m.sum() < 12:            # 主体太小 → 该帧无有效估计
            continue
        areas.append(int(m.sum()))
        rows = np.where(m.any(axis=1))[0]
        cols = np.where(m.any(axis=0))[0]
        mar = min(int(cols[0]), int(rows[0]), w - 1 - int(cols[-1]), h - 1 - int(rows[-1]))
        frac = mar / float(min(h, w))
        if frac < best:
            best, best_f = frac, i
    if len(areas) < 4:
        return None, None, "主体过小，无有效估计"
    if max(areas) > max_var * max(1, min(areas)):
        return None, None, "背景非平整/非静态（掩膜面积 %.0f→%.0f 抖动 >%.1f×）" % (
            min(areas), max(areas), max_var)
    return best, best_f, "ok"


def l1(path, first_anchor=None, cuts=(), bg="studio"):
    """cuts = 故事板【已声明】的切镜时刻（秒）。
    ⚠️ 为什么必须传：多锚点片段在锚定时刻会**硬切**（官方 SOP 本来就预期有切镜）。
       不告诉判官这些切点，L1-04 会把"按设计发生的切镜"误判成"动作断裂" ⇒ 把合格片判死。
       判官必须区分：**按设计发生的跳变**（解释得通）vs **凭空出现的跳变**（缺陷）。

    bg  = 故事板声明的背景类型，决定 L1-11（出框余量）能不能用：
        studio  = 平面静态影棚背景 → L1-11 启用（算术有效）
        outdoor = 户外/非平整背景 → L1-11 判 na（中位数背景估计失效，见 _frame_margin）
        macro   = 微距/特写镜     → L1-11 判 na（主体满画幅是**设计**，不是缺陷）
    ⚠️ 与 cuts 同源逻辑：判官必须区分【按设计发生的】与【凭空出现的】。"""
    out = []
    g = gray_frames(path, 96, 168)
    cuts_f = sorted(int(round(c * 24)) for c in cuts)

    def add(iid, ok, value=None, thr=None, frame=None, region=None, na=False):
        out.append({"id": iid, "verdict": "na" if na else ("yes" if ok else "no"),
                    "evidence": {"frame": frame, "region": region,
                                 "value": value, "threshold": thr},
                    "confidence": "high"})

    # --- L1-03 光流幅度（用逐帧 MAD 作 CPU 友好的运动活动度代理）+ 平台检测 ---
    mad = np.abs(np.diff(g, axis=0)).mean(axis=(1, 2))
    act = float(np.median(mad))
    # 平台：连续 0.5s(12 帧) 低于中位数 25%
    thr_plateau = max(0.35, act * 0.25)
    low = mad < thr_plateau
    run_len, worst, worst_at = 0, 0, -1
    for i, flag in enumerate(low):
        run_len = run_len + 1 if flag else 0
        if run_len > worst:
            worst, worst_at = run_len, i
    plateau_s = worst / 24.0
    add("L1-03", act >= 0.5 and plateau_s < 0.5, value=round(act, 3), thr=0.5,
        frame=worst_at, region="whole-frame",
        na=False)
    # --- L1-04 断裂点 ---
    # ⚠️ 标定过的判据（用 5 条正负对照样本标的：P1 无动作 / P2a 小动作 /
    #    C1 跑步 / C2 散步 / D 产品 5 段）：
    #    ① 窗口必须**长于动作周期**：k=25 帧（≈1 s）> 步态周期（~0.4 s）。
    #       原先取 9 帧（≈一个步态周期），局部中位数会落在"步态低谷"，
    #       于是**正常峰值被判成尖峰** —— 实测 C1 第 51 帧 mad=9.05 被误判，
    #       而它其实落在该段 p90=7.39 的自然范围内。
    #    ② 参照量改用**局部 p90**：真断裂远超同段所有峰值；周期性运动的峰值互相接近。
    #    ③ 比值判据 + 绝对守卫（+4.0），避免在极安静片段上把噪声当断裂。
    k = 25
    pad = np.pad(mad, (k // 2, k // 2), mode="edge")
    loc_p90 = np.array([np.percentile(pad[i:i + k], 90) for i in range(len(mad))])
    thr_i = np.maximum(3.0 * loc_p90, loc_p90 + 4.0)
    idx_hi = np.where(mad > thr_i)[0]
    # 只有【不在已声明切点附近】的尖峰才算缺陷 —— 按设计发生的硬切不判负
    unexplained = [int(i) for i in idx_hi
                   if not any(abs(int(i) - c) <= 12 for c in cuts_f)]
    spike_u = float(max([mad[i] / max(1e-6, loc_p90[i]) for i in unexplained],
                        default=0.0))
    n_hi = len(unexplained)
    add("L1-04", n_hi == 0, value=round(spike_u, 2), thr=3.0,
        frame=(max(unexplained, key=lambda i: mad[i] / max(1e-6, loc_p90[i]))
               if unexplained else None),
        region="motion-break")
    out[-1]["kind"] = "random" if n_hi <= 2 else "structural"
    out[-1]["spikes"] = n_hi
    out[-1]["declared_cuts_frames"] = cuts_f
    out[-1]["spikes_at_declared_cuts"] = int(
        sum(1 for i in idx_hi if any(abs(int(i) - c) <= 12 for c in cuts_f)))
    # --- L1-10 闪烁（帧间交替抖动）---
    # ⚠️ 判据同样标定过。走过两个弯路：
    #    弯路① 用"全幅 MAD 的 FFT 峰" → 步态本身就是强周期信号，被误判成闪烁；
    #    弯路② 改看"背景两侧竖带" → 全身/大幅动作时肢体仍会进竖带，还是被污染
    #            （实测 C1 仍报 51.9 vs 33.9）。
    #    正确判据 = **帧间交替性**：闪烁是"隔一帧回弹"，所以
    #      相邻帧差 mad1 大，而隔帧差 mad2 反而更小（比值 < 1）。
    #      真实运动是持续累积的 ⇒ mad2/mad1 ≈ 1 或更大。
    #    实测（标定集）：C1=1.64｜C2=1.78｜D=2.02 —— 全部 >1 ⇒ **无闪烁**。
    mad2 = np.abs(g[2:] - g[:-2]).mean(axis=(1, 2))
    m1 = float(np.median(mad))
    ratio = float(np.median(mad2) / max(1e-6, m1))
    flicker = (ratio < 0.55) and (m1 > 0.3)
    add("L1-10", not flicker, value=round(ratio, 3), thr=0.55,
        region="frame-alternation")
    # --- L1-11 出框余量（把 P-05 从主观判项转成算术判项）---
    # 仅当故事板声明【平面静态影棚背景】且非微距镜时才启用；其余一律 na（不拿失效算术否决片子）
    if bg == "studio":
        mfrac, mframe, why = _frame_margin(g)
        if why != "ok":
            out.append({"id": "L1-11", "verdict": "na",
                        "evidence": {"frame": None, "region": "subject-in-frame",
                                     "value": None, "threshold": None},
                        "confidence": "high", "note": why})
        else:
            add("L1-11", mfrac >= 0.02, value=round(mfrac, 4), thr=0.02,
                frame=mframe, region="subject-in-frame")
    else:
        out.append({"id": "L1-11", "verdict": "na",
                    "evidence": {"frame": None, "region": "subject-in-frame",
                                 "value": None, "threshold": None},
                    "confidence": "high",
                    "note": "背景类型=%s，出框余量算术不适用" % bg})
    # --- L1-02 产品保真（成片首帧 vs 锚定首帧；几何零重绘时应极小）---
    if first_anchor and os.path.exists(first_anchor):
        a = np.asarray(_rgb(first_anchor), dtype=np.float32)
        b = np.asarray(_rgb_from_video(path), dtype=np.float32)
        if a.shape == b.shape:
            mad0 = float(np.abs(a - b).mean())
            drb = float((b[..., 0] - b[..., 2]).mean() - (a[..., 0] - a[..., 2]).mean())
            add("L1-02", mad0 < 8.0 and abs(drb) < 6.0, value=round(mad0, 2),
                thr=8.0, frame=0, region="product")
            add("L1-05", abs(drb) < 6.0, value=round(drb, 2), thr=6.0,
                frame=0, region="tone")
        else:
            add("L1-02", True, na=True)
            add("L1-05", True, na=True)
    else:
        for i in ("L1-02", "L1-05"):
            out.append({"id": i, "verdict": "na",
                        "evidence": {"frame": None, "region": None,
                                     "value": None, "threshold": None},
                        "confidence": "high"})
    return out


def _rgb(path):
    from PIL import Image
    return Image.open(path).convert("RGB")


def _rgb_from_video(path):
    from PIL import Image
    import io
    p = run(["ffmpeg", "-v", "error", "-i", path, "-vf", "select='eq(n\\,0)'",
             "-frames:v", "1", "-f", "image2pipe", "-c:v", "png", "-"])
    return Image.open(io.BytesIO(p.stdout)).convert("RGB")


# ------------------------------------------------------------------ L2 VLM 判官
def _vlm_call(frames_b64, claim, retries=5, think=False):
    """⚠️ 必须把失败**显式打出来**，不能静默降级成 na。
    实测踩坑：3 个判官 × 6 并发 = 18 路同时打 API ⇒ 全部被限流，
    17 个判项**全部静默返回 na**，报告却看起来"跑成功了"。
    ⇒ 现在：429/5xx 用指数退避重试；失败打印原因；na 带 _error 字段。

    ⭐ 根因（2026-09-18 实测定位）：原来的 doubao-seed-2-1-pro 是**推理模型**，
       判图时会先吐 5,000+ 个 reasoning token（实测 93.4 s／rtok=5046／甚至 151 s 上游 502）。
       max_tokens 管不住推理 token，内容还会被思考挤掉 ⇒ 解析失败 ⇒ 静默 na。
       ⇒ 判官是「单一可观察二值事实」的原子判定（judge_form.atomic），
          **本来就不需要推理链**；关闭思考是合法且必要的。

    🔄 现役模型 = tt-5.6-luna（辉哥规范 2026-09-18；白名单硬约束见文件头）。
       ⚠️ 它**同样是推理模型**（网关别名实际打到 grok-4.6 / grok-4.6-build），
          且 thinking:{"type":"disabled"} 只**部分生效** —— 实测同一 payload 两种结果交替：
            · 生效档   → 2.1–2.4 s / rtok=0            （多数）
            · 被忽略档 → 6.7–53.6 s / rtok=276–2646    （少数，路由飘到另一个后端）
          已试 reasoning_effort=none/minimal、enable_thinking=false、chat_template_kwargs
          共 4 种写法，**都不能比 thinking:disabled 更稳**，故保留该写法。
       ⇒ timeout 定 **90 s**（覆盖实测最坏 53.6 s + 余量），让卡死的请求尽快失败、走退避重试。

    ⛔ 已废弃的错误做法（2026-09-18 实测翻车，**务必不要再加回来**）：
       曾对「rtok > 1200」的响应**丢弃重抽**，想以此把被忽略的那次换成 2 s 档。
       单发/小并发下看着有效（C1 首跑触发 2 次、复跑 4 次），但**生产并发下彻底失控**：
       C2+D 连跑出现 **25 次重抽、0 个判项完成**，并伴随 200 s 超时，判官跑 16 分钟
       仍未收敛 ⇒ **直接逼近单镜生成时长（9.4–11.4 min），隔离红线告急**。
       根因：快/慢是**上游路由**决定的（不是请求本身有问题），重抽大概率仍落慢后端；
       而并发重抽又进一步压垮上游 ⇒ 正反馈雪崩。
       ⇒ 正确策略：**慢就接受**（能解析出 verdict 就用，rtok 只记录供成本审计），
         保总耗时可控 —— 判官的要求是「藏在生成后面」，不是「单次快」。"""
    content = [{"type": "text", "text":
                "CLAIM TO JUDGE (one single yes/no/na question):\n%s" % claim}]
    for b in frames_b64:
        content.append({"type": "image_url",
                        "image_url": {"url": "data:image/jpeg;base64," + b}})
    payload = {"model": VLM_MODEL, "temperature": 0,
               "messages": [{"role": "system", "content": VLM_SYS},
                            {"role": "user", "content": content}],
               "max_tokens": 900 if think else 400}
    if not think:
        payload["thinking"] = {"type": "disabled"}   # ⭐ 关思考链：2.5s vs 93s
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(
                VLM_BASE + "/v1/chat/completions",
                data=json.dumps(payload).encode(),
                headers={"Authorization": "Bearer " + VLM_KEY,
                         "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=90) as r:
                d = json.loads(r.read().decode())
            # rtok 只做记录（成本审计用），**不再据此重抽** —— 见函数 docstring 的翻车记录
            rtok = ((d.get("usage") or {}).get("completion_tokens_details")
                    or {}).get("reasoning_tokens") or 0
            txt = d["choices"][0]["message"]["content"] or ""
            m = re.search(r"\{.*\}", txt, re.S)
            if not m:
                raise ValueError("no json: " + txt[:200])
            out = json.loads(m.group(0))
            if isinstance(out, dict):
                out["_reasoning_tokens"] = rtok
            return out
        except Exception as e:
            last = e
            code = getattr(e, "code", None)
            # 402 实测是**瞬时配额抖动**（同一 payload 时通时不通），不是欠费 → 必须退避重试：
            #   同一请求 402 与 200 会交替出现；小图恒通、大图偶发。
            if code in (429, 402):
                wait = max(8.0, 3.0 * (2 ** i))
            else:
                wait = 2.0 ** (i + 1)
            if i < retries - 1:
                print("       ⚠️ VLM 调用失败(%s%s) → %.1fs 后重试 %d/%d"
                      % (type(e).__name__, "/%s" % code if code else "",
                         wait, i + 1, retries - 1), flush=True)
                time.sleep(wait)
    print("       ✗ VLM 调用最终失败：%s" % str(last)[:180], flush=True)
    return {"verdict": "na", "evidence": {"frame": None, "region": None},
            "confidence": "low", "_error": str(last)[:200]}


def l2(path, shot_prompt, segs, n_frames=6, workers=4, think=False, votes=3):
    """L2 远端 VLM 判官（**多组抽样投票制**）。

    ⚠️ 必须【项级并发】：判项天然彼此独立（judge_form.one_question_per_call），
       串行跑 17 项 × 每次 20–90 s = 5–25 min/镜，判官就从"藏在生成后面"变成新瓶颈。
       ⚠️ 必须【投票】：单次采样已实测不可靠（同一 payload 结论会翻转，见 vote_sets 注释）。
       17 项 × 3 票 = 51 次调用；tt-5.6-luna 单次 2–15 s（少数 50 s+）/ workers=4
       ⇒ 单镜约 1.5–3 min，仍远小于单镜生成 9.4–11.4 min ⇒ 判官依然"藏在生成后面"（红线不破）。
    投票规则：多数票；三票各不同（1:1:1）⇒ 判 na + confidence=low（交人工，不否决）。
    """
    sets = vote_sets(path, n_frames=n_frames, votes=votes)
    frame_sets = [frames_at(path, s) for s in sets]
    b64_sets = [[base64.b64encode(f).decode() for f in fs] for fs in frame_sets]
    todo = [iid for iid in ITEMS if iid.split("-")[0] in segs]

    def one_vote(iid, k):
        claim = (ITEMS[iid] +
                 "\n\nTHE SHOT DESCRIPTION WRITTEN BY THE FILMMAKER (this is the only "
                 "source of truth for what was declared):\n" + shot_prompt.strip()[:4000])
        r = _vlm_call(b64_sets[k], claim, think=think)
        v = str(r.get("verdict", "na")).lower().strip()
        if v not in ("yes", "no", "na"):
            v = "na"
        ev = r.get("evidence") or {}
        fi = ev.get("frame")
        idxk = sets[k]
        if isinstance(fi, (int, float)) and 0 <= int(fi) < len(idxk):
            fi = int(idxk[int(fi)])
        else:
            fi = None
        return {"vote": k, "verdict": v, "frame": fi, "region": ev.get("region"),
                "confidence": r.get("confidence", "low"), "error": r.get("_error")}

    jobs = [(iid, k) for iid in todo for k in range(len(sets))]

    def run_job(j):
        return j[0], one_vote(j[0], j[1])

    per = {}
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        for f in cf.as_completed([ex.submit(run_job, j) for j in jobs]):
            iid, r = f.result()
            per.setdefault(iid, []).append(r)

    out = []
    for iid in sorted(per):
        vs = per[iid]
        cnt = {v: sum(1 for r in vs if r["verdict"] == v) for v in ("yes", "no", "na")}
        top = max(cnt, key=lambda v: cnt[v])
        confs = [r["confidence"] for r in vs if r["verdict"] == top]
        best = next((r for r in vs if r["verdict"] == top and r["frame"] is not None), vs[0])
        out.append({
            "id": iid, "verdict": top,
            "evidence": {"frame": best.get("frame"), "region": best.get("region"),
                         "value": None, "threshold": None},
            "confidence": ("low" if cnt[top] * 2 <= len(vs) else
                           ("high" if "high" in confs else "medium")),
            "votes": "%d/%d (%s)" % (cnt[top], len(vs),
                                     ",".join(r["verdict"] for r in vs)),
        })
        print("   L2 %-5s %-3s  票 %s" % (iid, top, out[-1]["votes"]), flush=True)
    return out


# 每个判项对应的崩坏类型与处置（对标 judge_config.yaml 的 breakdowns 表）
REMEDY = {
    "L0-01": ("technical", "不成片：解码失败，重生成"),
    "L0-02": ("technical", "不成片：帧数不符合 17k+5 网格，检查段长参数"),
    "L0-03": ("technical", "不成片：分辨率不符，检查 mp/aspect"),
    "L0-04": ("technical", "不成片：时长不符，检查段长参数"),
    "L0-05": ("technical", "不成片：音轨静音，检查 audio VAE 接线"),
    "L0-06": ("technical", "不成片：出现全黑/全白帧段"),
    "L0-07": ("technical", "不成片：长静音 > 1.5s"),
    "L0-08": ("technical", "不成片：容器损坏"),
    "L1-01": ("random", "身份漂移 → 换 seed 重抽（有效）"),
    "L1-02": ("random", "产品漂移 → 换 seed 重抽（有效）"),
    "L1-03": ("structural", "动作未发生 / 空转 → **禁止重抽**，加大锚定帧相位差或加中间锚点"),
    "L1-04": ("random", "帧间跳变 → 孤立单帧属随机型，换 seed 重抽；持续偏高属结构型，拆镜"),
    "L1-05": ("random", "色调漂移 → 统一调色 + 重抽"),
    "L1-10": ("random", "纹理闪烁 → 换 seed 重抽"),
    "L1-11": ("structural", "主体出框/贴边 → **禁止重抽**，改锚点构图（加大负空间）或改景别"),
}


def aggregate(res, low_conf_is_veto=False):
    def no_of(pref):
        return [r["id"] for r in res
                if r["id"].startswith(pref) and r["verdict"] == "no"]

    l0_no = no_of("L0")
    l1_no = no_of("L1")
    RW = [r for r in res if r["id"][0] in "RW"]
    P = [r for r in res if r["id"][0] == "P"]
    rw_no = [r["id"] for r in RW if r["verdict"] == "no"]
    p_no = [r["id"] for r in P if r["verdict"] == "no"]
    all_na = [r["id"] for r in RW if r["verdict"] == "na"]
    na_ratio = len(all_na) / max(1, len(RW))
    # ⚠️ 置信度闸门：VLM_SYS 明确要求"只有真的能看到问题才说 no"，confidence 就是为这个收的。
    #    no + confidence=low ⇒ 降级为【人工复核项】，不单独否决整镜。
    #    实测依据：C1 关思考链时 P-01/P-04 对干净画面误报 no（全分辨率复核证实无缺陷），
    #    这类低置信误报若不降级，会把合格片票死。high/medium 的 no 仍然照常否决。
    low_conf = [r["id"] for r in RW + P
                if r["verdict"] == "no" and str(r.get("confidence", "")).lower() == "low"]
    if low_conf_is_veto:
        low_conf = []

    verdict, reasons, fixes = "PASS", [], []
    if l0_no:
        verdict = "REJECT"
        reasons.append("L0 技术校验不通过: " + ", ".join(l0_no))
    if l1_no:
        verdict = "REJECT"
        reasons.append("L1 本地检测器不通过: " + ", ".join(l1_no))
    rw_v = [i for i in rw_no if i not in low_conf]
    p_v = [i for i in p_no if i not in low_conf]
    if rw_v:
        verdict = "REJECT"
        reasons.append("R/W 段否决项: " + ", ".join(rw_v))
    if len(p_v) >= 2:
        verdict = "REJECT"
        reasons.append("P 段 no>=2: " + ", ".join(p_v))
    for iid in l0_no + l1_no + rw_no + p_no:
        if iid in REMEDY:
            kind, act = REMEDY[iid]
            # L1-04 用实测性质覆盖默认
            if iid == "L1-04":
                item = next((r for r in res if r["id"] == iid), None)
                if item and item.get("kind") == "random":
                    act = "帧间跳变（孤立单帧尖峰）= 随机型 → 换 seed 重抽"
            fixes.append("%s［%s］%s" % (iid, kind, act))
    warn = []
    if na_ratio > 0.4:
        warn.append("R/W 段 na 比例 %.0f%% > 40%% → 人工复核" % (na_ratio * 100))
    for i in low_conf:
        warn.append("%s 判 no 但 confidence=low → 降级为人工复核，不否决整镜" % i)
    return {"verdict": verdict, "l0_no": l0_no, "l1_no": l1_no, "rw_no": rw_no,
            "p_no": p_no, "low_conf_no": low_conf, "na_ratio": round(na_ratio, 3),
            "warnings": warn, "reasons": reasons, "remedies": fixes}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--name", default="SHOT")
    ap.add_argument("--prompt", required=True, help="该镜的提示词文件（判据来源）")
    ap.add_argument("--first", default=None, help="锚定首帧（L1-02 保真比对）")
    ap.add_argument("--cuts", default="",
                    help="故事板【已声明】的切镜时刻（秒，逗号分隔），如 \"5.5,8.7\"。"
                         "多锚点片段必须在锚定时刻硬切，声明后 L1-04 才不会把切镜判成断裂。")
    ap.add_argument("--items", default="R,W,P", help="要判的段，如 R,W / R,W,P")
    ap.add_argument("--frames", type=int, default=6)
    ap.add_argument("--workers", type=int, default=4, help="L2 判项并发数")
    ap.add_argument("--votes", type=int, default=3,
                    help="L2 每个判项投几票（不同帧集各一票，取多数）。1 = 单次采样（不推荐）")
    ap.add_argument("--bg", default="studio", choices=["studio", "outdoor", "macro"],
                    help="故事板声明的背景/景别类型，决定 L1-11 出框余量是否可用："
                         "studio 平面静态影棚（启用）｜outdoor 户外（na）｜macro 微距特写（na）")
    ap.add_argument("--think", action="store_true",
                    help="L2 打开思考链（更准但 ~35× 慢）。默认关闭——见 _vlm_call 注释")
    ap.add_argument("--lowconf-veto", action="store_true",
                    help="低置信 no 也否决（默认降级为人工复核）")
    ap.add_argument("--out", default=None)
    ap.add_argument("--dry", action="store_true", help="只跑 L0/L1")
    a = ap.parse_args()

    segs = [s.strip() for s in a.items.split(",")]
    shot_prompt = open(a.prompt, encoding="utf-8").read().strip()

    print("=" * 74)
    print("判官团逐镜评分 · %s" % a.name)
    print("  片源 %s" % a.video)
    print("  判据 %s" % a.prompt)
    print("  背景声明 %s ｜ L2 思考链 %s ｜ L2 并发 %d ｜ L2 投票 %d 票/项" %
          ({"studio": "studio 平面静态影棚（L1-11 启用）",
            "outdoor": "outdoor 户外（L1-11 na）",
            "macro": "macro 微距特写（L1-11 na）"}[a.bg],
           "开" if a.think else "关", a.workers, a.votes))
    print("=" * 74)

    print("[L0] 技术校验（纯算术）")
    r0 = l0(a.video)
    for r in r0:
        print("   %-6s %-3s" % (r["id"], r["verdict"]))
    cuts = tuple(float(x) for x in a.cuts.split(",") if x.strip())
    print("[L1] 本地检测器（Mac CPU）"
          + ("　｜已声明切点 %s s" % list(cuts) if cuts else ""))
    r1 = l1(a.video, a.first, cuts, bg=a.bg)
    for r in r1:
        print("   %-6s %-3s  value=%s thr=%s" %
              (r["id"], r["verdict"], r["evidence"]["value"], r["evidence"]["threshold"]))
    r2 = []
    if not a.dry:
        print("[L2] 远端 VLM 判官（%s，%d 项并发）" % (VLM_MODEL, a.workers))
        r2 = l2(a.video, shot_prompt, segs, n_frames=a.frames, workers=a.workers,
                think=a.think, votes=a.votes)
    else:
        print("[L2] 已跳过（--dry）")

    res = r0 + r1 + r2
    agg = aggregate(res, low_conf_is_veto=a.lowconf_veto)
    print("-" * 74)
    print("聚合判定: %s" % agg["verdict"])
    for x in agg["reasons"]:
        print("   ✗ " + x)
    for x in agg.get("remedies", []):
        print("   ↳ 处置: " + x)
    for x in agg["warnings"]:
        print("   ⚠ " + x)
    if agg["verdict"] == "PASS":
        print("   ✅ 该镜通过判官团，可冻结")
    print("-" * 74)

    report = {"shot": a.name, "video": a.video, "prompt_file": a.prompt,
              "judge_model": VLM_MODEL, "ts": time.strftime("%F %T"),
              "items": res, "aggregate": agg}
    outp = a.out or (os.path.splitext(a.video)[0] + "_judge.json")
    with open(outp, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print("报告 → %s" % outp)
    return 0 if agg["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
