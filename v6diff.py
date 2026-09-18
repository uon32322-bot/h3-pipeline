#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V6 内容级比对：mp4 的 md5 不可用作判据（内嵌了 prompt/文件名等元数据）。
本工具解码成 raw 帧和 PCM 后逐位/逐像素比较。

用法: v6diff.py <文件A.mp4> <文件B.mp4> [更多...]
输出：帧内容哈希、逐帧像素差、音轨 PCM 哈希与相关
"""
import hashlib
import os
import subprocess as sp
import sys

import numpy as np

FF = None
try:
    import imageio_ffmpeg
    FF = imageio_ffmpeg.get_ffmpeg_exe()
except Exception:
    FF = "ffmpeg"


def probe(path):
    out = sp.run([FF.replace("ffmpeg", "ffprobe") if False else FF, "-v", "error",
                  "-i", path, "-f", "null", "-"],
                 capture_output=True, text=True)
    return out.stderr


def frames(path, W=192, H=336):
    """解码为灰度帧栈 [T,H,W]（float32 0-255）。"""
    p = sp.run([FF, "-v", "error", "-i", path, "-vf", f"scale={W}:{H}",
                "-f", "rawvideo", "-pix_fmt", "gray", "-"],
               capture_output=True)
    buf = np.frombuffer(p.stdout, np.uint8)
    n = len(buf) // (W * H)
    if n == 0:
        return np.zeros((0, H, W), np.float32)
    return buf[:n * W * H].reshape(n, H, W).astype(np.float32)


def audio(path, SR=16000):
    p = sp.run([FF, "-v", "error", "-i", path, "-vn", "-ac", "1", "-ar", str(SR),
                "-f", "f32le", "-"],
               capture_output=True)
    if len(p.stdout) < 4:
        return np.zeros(0, np.float32)
    return np.frombuffer(p.stdout, np.float32)


def h(b):
    return hashlib.md5(b if isinstance(b, bytes) else b.tobytes()).hexdigest()[:12]


if __name__ == "__main__":
    paths = sys.argv[1:]
    vids, auds = {}, {}
    for p in paths:
        name = os.path.basename(p).replace(".mp4", "")
        f = frames(p)
        a = audio(p)
        vids[name], auds[name] = f, a
        ah = h(np.clip(a * 1000, -32768, 32767).astype(np.int16)) if len(a) else "-"
        print(f"{name:12s} 帧={f.shape[0]:4d} 帧哈希={h(f)} 音轨样本={len(a):7d} "
              f"音轨哈希={ah} 音轨RMS={np.sqrt((a**2).mean()) if len(a) else 0:.5f}")

    names = list(vids)
    print("\n=== 逐帧像素差（0-255，越大越不同）===")
    print(f"{'':14s}" + "".join(f"{n:>12s}" for n in names))
    for a in names:
        row = f"{a:12s}  "
        for b in names:
            fa, fb = vids[a], vids[b]
            n = min(len(fa), len(fb))
            d = np.abs(fa[:n] - fb[:n]).mean() if n else float("nan")
            row += f"{d:12.2f}"
        print(row)

    print("\n=== 音轨相关 / 最大差 ===")
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            aa, ab = auds[a], auds[b]
            n = min(len(aa), len(ab))
            if n < 100:
                print(f"{a} vs {b}: 无音轨可比")
                continue
            x, y = aa[:n].astype(np.float64), ab[:n].astype(np.float64)
            md = float(np.abs(x - y).max())
            r = float(np.corrcoef(x, y)[0, 1]) if x.std() > 1e-9 and y.std() > 1e-9 else float("nan")
            print(f"{a} vs {b}: r={r:+.4f}  最大差={md:.5f}  "
                  f"{'PCM 完全相同' if md == 0 else ''}")

    print("\n=== 帧内容是否逐位相同（同长度时）===")
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            fa, fb = vids[a], vids[b]
            n = min(len(fa), len(fb))
            if n and h(fa[:n]) == h(fb[:n]):
                print(f"{a} vs {b}: 前 {n} 帧逐位相同 ✅")
