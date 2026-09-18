#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""音频内容一致性验证（log-mel + DTW）

要回答的问题：H3 输出的音轨，内容上到底是不是我锚定进去的那句配音？
方法：把两段音频都转成 log-mel 序列，用 DTW 求对齐代价（越小越像）。
对照：拿另一句配音做参照，比较"输出 vs 本句"与"输出 vs 他句"的代价差。

用法: audio_match.py <输出视频或音频> <参考配音.wav> [--other 另一句.wav]
"""
import subprocess, sys, numpy as np

def opt(n, d=None):
    a = sys.argv
    return a[a.index(n) + 1] if n in a else d

TARGET = sys.argv[1]
REF = sys.argv[2]
OTHER = opt("--other")
SR, NFFT, HOP, NMEL = 16000, 400, 160, 40

def load_mono(path):
    p = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-ac", "1", "-ar", str(SR),
                        "-f", "s16le", "-"], capture_output=True)
    return np.frombuffer(p.stdout, dtype=np.int16).astype(np.float32) / 32768.0

def trim_active(a, ratio=0.06):
    hop = 800
    n = len(a) // hop
    e = np.sqrt((a[:n * hop].reshape(n, hop) ** 2).mean(axis=1))
    idx = np.where(e > e.max() * ratio)[0]
    return a[idx[0] * hop:(idx[-1] + 1) * hop] if len(idx) else a

def mel_fb():
    def hz2mel(f): return 2595 * np.log10(1 + f / 700.0)
    def mel2hz(m): return 700 * (10 ** (m / 2595.0) - 1)
    pts = mel2hz(np.linspace(hz2mel(60), hz2mel(SR / 2 - 100), NMEL + 2))
    bins = np.floor((NFFT + 1) * pts / SR).astype(int)
    fb = np.zeros((NMEL, NFFT // 2 + 1))
    for m in range(1, NMEL + 1):
        l, c, r = bins[m - 1], bins[m], bins[m + 1]
        if r <= l: r = l + 1
        for k in range(l, min(c, NFFT // 2 + 1)):
            fb[m - 1, k] = (k - l) / max(1, c - l)
        for k in range(c, min(r, NFFT // 2 + 1)):
            fb[m - 1, k] = (r - k) / max(1, r - c)
    return fb

FB = mel_fb()
WIN = np.hanning(NFFT)

def logmel(a):
    a = trim_active(a)
    n = 1 + max(0, (len(a) - NFFT) // HOP)
    if n < 3: return np.zeros((3, NMEL), np.float32)
    idx = np.arange(NFFT)[None, :] + HOP * np.arange(n)[:, None]
    fr = a[idx] * WIN
    S = np.abs(np.fft.rfft(fr, axis=1))
    M = np.log(S @ FB.T + 1e-6)
    M = (M - M.mean(axis=1, keepdims=True)) / (M.std(axis=1, keepdims=True) + 1e-6)
    return M.astype(np.float32)

def dtw(A, B):
    """返回归一化 DTW 距离（cosine 距离矩阵）"""
    An = A / (np.linalg.norm(A, axis=1, keepdims=True) + 1e-6)
    Bn = B / (np.linalg.norm(B, axis=1, keepdims=True) + 1e-6)
    D = 1.0 - An @ Bn.T
    N, M = D.shape
    C = np.full((N + 1, M + 1), np.inf); C[0, 0] = 0
    for i in range(1, N + 1):
        row = D[i - 1]
        prev = C[i - 1]
        cur = C[i]
        for j in range(1, M + 1):
            cur[j] = row[j - 1] + min(prev[j], cur[j - 1], prev[j - 1])
    return C[N, M] / (N + M)

t = logmel(load_mono(TARGET))
r = logmel(load_mono(REF))
print("目标 %s : %d 帧 ｜参考 %s : %d 帧" % (TARGET.split('/')[-1], len(t), REF.split('/')[-1], len(r)))
d_self = dtw(t, r)
print("DTW 距离（输出 vs 本句参考）  = %.4f" % d_self)

if OTHER:
    o = logmel(load_mono(OTHER))
    d_other = dtw(t, o)
    d_base = dtw(r, o)
    print("DTW 距离（输出 vs 另一句）    = %.4f" % d_other)
    print("DTW 距离（本句 vs 另一句，基线）= %.4f" % d_base)
    print()
    if d_self < d_other * 0.85:
        print("=> 判定：输出内容显著更接近本句配音，音频锚点内容被保留 ✅")
    elif d_self < d_other:
        print("=> 判定：输出略偏向本句，弱证据 ⚠️")
    else:
        print("=> 判定：未能区分，音频内容不可控 ❌")
