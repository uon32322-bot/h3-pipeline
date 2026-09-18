#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""口型-音频同步量化验证

思路：嘴部 ROI 的逐帧变化量（视觉"动嘴"强度）应当与音频响度包络同相。
      对两者做互相关，最佳延迟接近 0 且峰值相关高 => 音画同步。

用法:
  lipsync.py <video.mp4> [--roi x,y,w,h] [--fps 24]

不带 --roi 时使用按 480x864 标定的默认嘴部区域（可先跑 --dump 看位置）。
"""
import subprocess, sys, numpy as np

def opt(name, default=None):
    a = sys.argv
    return a[a.index(name) + 1] if name in a else default

VID = sys.argv[1]
FPS = float(opt("--fps", "24"))
ROI = opt("--roi")
W, H = 480, 864
if ROI:
    x, y, w, h = [int(v) for v in ROI.split(",")]
else:
    # 按 480x864 竖屏、模特脸部居中偏上标定的嘴部区域
    x, y, w = int(0.47 * W), int(0.24 * H), int(0.15 * W)
    h = int(0.055 * H)

def frames_gray(path, x, y, w, h):
    cmd = ["ffmpeg", "-v", "error", "-i", path,
           "-vf", "crop=%d:%d:%d:%d,format=gray" % (w, h, x, y),
           "-f", "rawvideo", "-pix_fmt", "gray", "-"]
    p = subprocess.run(cmd, capture_output=True)
    buf = np.frombuffer(p.stdout, dtype=np.uint8)
    n = len(buf) // (w * h)
    return buf[:n * w * h].reshape(n, h, w).astype(np.float32)

def audio_env(path, fps):
    cmd = ["ffmpeg", "-v", "error", "-i", path, "-ac", "1", "-ar", "24000",
           "-f", "s16le", "-"]
    p = subprocess.run(cmd, capture_output=True)
    a = np.frombuffer(p.stdout, dtype=np.int16).astype(np.float32) / 32768.0
    step = int(24000 / fps)
    n = len(a) // step
    return np.sqrt((a[:n * step].reshape(n, step) ** 2).mean(axis=1))

def z(x):
    return (x - x.mean()) / (x.std() + 1e-9)

fr = frames_gray(VID, x, y, w, h)
if len(fr) < 4:
    print("抽帧失败"); sys.exit(1)

# 嘴部逐帧变化量（相邻帧 MAE），首帧补 0
mouth = np.concatenate([[0.0], np.abs(np.diff(fr, axis=0)).mean(axis=(1, 2))])
env = audio_env(VID, FPS)
n = min(len(mouth), len(env))
mouth, env = mouth[:n], env[:n]

print("画幅 %dx%d  嘴部 ROI x=%d y=%d w=%d h=%d  帧数 %d  时长 %.2fs"
      % (W, H, x, y, w, h, n, n / FPS))

# 音频活跃窗口 vs 视觉动嘴窗口
th_a, th_m = env.max() * 0.12, mouth.max() * 0.20
act_a = np.where(env > th_a)[0]
act_m = np.where(mouth > th_m)[0]
def span(idx, fps):
    return "%.2f–%.2fs" % (idx[0] / fps, idx[-1] / fps) if len(idx) else "无"
print("音频活跃窗口 : %s（阈值 %.1f%% 峰值）" % (span(act_a, FPS), 12))
print("动嘴窗口     : %s（阈值 %.1f%% 峰值）" % (span(act_m, FPS), 20))

# 互相关：正延迟表示视觉滞后音频
a, m = z(env), z(mouth)
best = max(((float(np.dot(a[:n - k], m[k:]) / (n - k)), k) for k in range(0, int(0.6 * FPS))),
           key=lambda t: t[0])
print("最佳互相关 %.3f  @ 视觉滞后音频 %.0f 帧（%.0f ms）" % (best[0], best[1], best[1] / FPS * 1000))

# 仅在音频活跃段内比较包络形状
if len(act_a) > 3:
    lo, hi = act_a[0], act_a[-1] + 1
    r = float(np.corrcoef(z(env[lo:hi]), z(mouth[lo:hi]))[0, 1])
    print("音频活跃段内包络相关 %.3f（0=无关，>0.4 视为同步）" % r)

print("嘴部运动前 20 帧:", " ".join("%.1f" % v for v in mouth[:20]))
print("音频包络前 20 帧:", " ".join("%.1f" % (v * 100) for v in env[:20]))
