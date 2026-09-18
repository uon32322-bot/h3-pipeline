#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""口型-音频同步量化验证 v2 —— 自动定位嘴部 ROI

v1 用固定 ROI 会落在高频纹理/编码噪声上，导致相关性被基线淹没。
v2 做法：
  1. 抽出整个脸部区域的所有帧（灰度）
  2. 2x2 均值下采样抑制编码噪声
  3. 算相邻帧差分图，用积分图快速求任意子窗口的"动嘴强度"序列
  4. 在脸部区域内搜索与音频响度包络相关性最高的窗口 => 自动锁定嘴部
  5. 报告最佳窗口的互相关峰值与时滞

用法: lipsync2.py <video.mp4> [--face 480x864] [--fps 24]
"""
import subprocess, sys, numpy as np

def opt(n, d=None):
    a = sys.argv
    return a[a.index(n) + 1] if n in a else d

VID = sys.argv[1]
FPS = float(opt("--fps", "24"))

def probe_size(path):
    p = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                        "-show_entries", "stream=width,height", "-of", "csv=p=0", path],
                       capture_output=True, text=True)
    w, h = p.stdout.strip().split(",")[:2]
    return int(w), int(h)

W, H = probe_size(VID)
# 脸部搜索区：收紧到脸部本身（避免把手/产品的运动误判成嘴）
fx, fy = int(0.28 * W), int(0.09 * H)
fw, fh = int(0.46 * W), int(0.32 * H)

def face_frames(path):
    cmd = ["ffmpeg", "-v", "error", "-i", path,
           "-vf", "crop=%d:%d:%d:%d,format=gray" % (fw, fh, fx, fy),
           "-f", "rawvideo", "-pix_fmt", "gray", "-"]
    p = subprocess.run(cmd, capture_output=True)
    b = np.frombuffer(p.stdout, dtype=np.uint8)
    n = len(b) // (fw * fh)
    f = b[:n * fw * fh].reshape(n, fh, fw).astype(np.float32)
    # 2x2 均值下采样，抑制高频编码噪声
    h2, w2 = fh // 2 * 2, fw // 2 * 2
    return f[:, :h2, :w2].reshape(n, h2 // 2, 2, w2 // 2, 2).mean(axis=(2, 4))

def audio_env(path, fps):
    p = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-ac", "1", "-ar", "24000",
                        "-f", "s16le", "-"], capture_output=True)
    a = np.frombuffer(p.stdout, dtype=np.int16).astype(np.float32) / 32768.0
    st = int(24000 / fps); n = len(a) // st
    return np.sqrt((a[:n * st].reshape(n, st) ** 2).mean(axis=1))

fr = face_frames(VID)
env = audio_env(VID, FPS)
n = min(len(fr) - 1, len(env))
fr, env = fr[:n + 1], env[:n]
print("画幅 %dx%d  搜索区 x=%d y=%d w=%d h=%d  帧数 %d  时长 %.2fs"
      % (W, H, fx, fy, fw, fh, n + 1, (n + 1) / FPS))

diff = np.abs(np.diff(fr, axis=0)).mean(axis=0)      # (h2/2, w2/2) 时间平均差分
d_all = np.abs(np.diff(fr, axis=0)).sum(axis=0)      # 每像素的总变化
H2, W2 = fr.shape[1], fr.shape[2]

# 每帧的差分图 -> 用积分图求任意窗口的逐帧平均差分
D = np.abs(np.diff(fr, axis=0))                      # (n, H2, W2)
cs = np.zeros((n, H2 + 1, W2 + 1), dtype=np.float64)
cs[:, 1:, 1:] = D.cumsum(axis=1).cumsum(axis=2)

def win_series(x, y, w, h):
    s = (cs[:, y + h, x + w] - cs[:, y, x + w] - cs[:, y + h, x] + cs[:, y, x])
    return s / (w * h)

zc = lambda a: (a - a.mean()) / (a.std() + 1e-9)
ze = zc(env)

# 只在音频活跃段内评估（沉默段无信息）
th = env.max() * 0.15
act = np.where(env > th)[0]
lo, hi = (act[0], act[-1] + 1) if len(act) > 4 else (0, n)
print("音频活跃段 %.2f–%.2fs" % (lo / FPS, hi / FPS))
seg = slice(lo, hi)

best = []
ws = [(  int(0.10*W), int(0.04*H)), (int(0.14*W), int(0.05*H)), (int(0.18*W), int(0.06*H))]
for (ww, hh) in ws:
    WW, HH = ww // 2, hh // 2
    for y in range(0, H2 - HH, 2):
        for x in range(0, W2 - WW, 2):
            s = win_series(x, y, WW, HH)[seg]
            if s.std() < 1e-6:
                continue
            r = float(np.corrcoef(zc(s), ze[seg])[0, 1])
            best.append((r, x * 2, y * 2, WW * 2, HH * 2))
best.sort(reverse=True)
print("\n与音频包络相关性最高的候选嘴部窗口：")
for r, x, y, w, h in best[:5]:
    print("  r=%+.3f  原图坐标 x=%d y=%d w=%d h=%d" % (r, fx + x, fy + y, w, h))

if best:
    r, x, y, w, h = best[0]
    s = win_series(x // 2, y // 2, w // 2, h // 2)
    print("\n最佳窗口逐帧互相关（正=视觉滞后音频）：")
    for k in range(0, int(0.6 * FPS) + 1):
        if n - k < 8: break
        c = float(np.dot(zc(s[:n - k]), zc(env[k:])) / (n - k))
        if k % 3 == 0:
            print("   滞后 %2d 帧 (%3.0f ms): r=%+.3f" % (k, k / FPS * 1000, c))
    rr = [float(np.dot(zc(s[:n-k]), zc(env[k:]))/(n-k)) for k in range(int(0.6*FPS))]
    kb = int(np.argmax(rr))
    print("  => 峰值 r=%+.3f @ 滞后 %d 帧 (%.0f ms)" % (rr[kb], kb, kb / FPS * 1000))
    print("  嘴部运动前 16 帧:", " ".join("%.1f" % v for v in s[:16]))
    print("  音频包络前 16 帧:", " ".join("%.0f" % (v * 100) for v in env[:16]))
