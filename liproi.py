#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""固定 ROI 的口型-音频同步量化

当自动搜索 ROI 被头发/镜头推进干扰时，用手动指定的嘴部窗口做干净测量。

用法:
  liproi.py <video.mp4> <x> <y> <w> <h> [--fps 24]

输出：嘴部逐帧运动量 vs 音频包络的互相关（只看 ±8 帧时滞）。
"""
import json, subprocess, sys

import numpy as np

D2 = "/Users/admin/.workbuddy/binaries/python/envs/default/bin/python"


def probe(p):
    o = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                        "-show_entries", "stream=width,height,r_frame_rate",
                        "-show_entries", "format=duration", "-of", "json", p],
                       capture_output=True, text=True).stdout
    d = json.loads(o); s = d["streams"][0]
    n, k = s["r_frame_rate"].split("/")
    return int(s["width"]), int(s["height"]), float(n) / float(k), float(d["format"]["duration"])


def main():
    if len(sys.argv) < 6:
        raise SystemExit(__doc__)
    vid = sys.argv[1]
    x, y, w, h = (int(v) for v in sys.argv[2:6])
    fps_opt = None
    if "--fps" in sys.argv:
        fps_opt = float(sys.argv[sys.argv.index("--fps") + 1])

    W, H, FPS, DUR = probe(vid)
    fps = fps_opt or FPS
    print(f"成片 {W}x{H} @{FPS:.2f}fps {DUR:.2f}s   采样 {fps}fps")
    print(f"嘴部 ROI: x={x} y={y} w={w} h={h}")

    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", vid, "-vf", f"fps={fps},crop={w}:{h}:{x}:{y}",
         "-f", "rawvideo", "-pix_fmt", "gray", "-"], capture_output=True).stdout
    n = len(raw) // (w * h)
    g = np.frombuffer(raw[:n * w * h], np.uint8).reshape(n, h, w).astype(np.float32)
    print(f"抽帧 {n} 帧")

    # 逐帧运动量：相邻帧平均绝对差
    mot = np.array([np.abs(g[i] - g[i - 1]).mean() for i in range(1, n)])

    # 音频包络
    aw = "/tmp/_liproi.wav"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", vid, "-ar", "16000", "-ac", "1", aw])
    import wave
    wf = wave.open(aw, "rb")
    a = np.frombuffer(wf.readframes(wf.getnframes()), np.int16).astype(np.float32)
    sr = wf.getframerate(); wf.close()
    hop = int(sr / fps)
    m = len(a) // hop
    env = np.abs(a[:m * hop]).reshape(m, hop).max(axis=1)

    L = min(len(mot), len(env)); mot, env = mot[:L], env[:L]
    mn = (mot - mot.mean()) / (mot.std() + 1e-9)
    en = (env - env.mean()) / (env.std() + 1e-9)

    print(f"\n嘴部运动均值 {mot.mean():.2f} 峰值 {mot.max():.2f}")
    print(f"音频包络均值 {env.mean():.0f} 峰值 {env.max():.0f}")
    print("\n时滞扫描（正=画面滞后于音频）:")
    best = (0, -9)
    for lag in range(-8, 9):
        if lag >= 0:
            c = float(np.dot(mn[lag:], en[:L - lag]) / (L - lag))
        else:
            c = float(np.dot(mn[:L + lag], en[-lag:]) / (L + lag))
        mark = ""
        if c > best[1]:
            best = (lag, c); mark = ""
        print(f"  {lag:+3d} 帧 ({lag / fps * 1000:+6.0f} ms): r={c:+.3f}{mark}")
    print(f"\n=> 峰值 r={best[1]:+.3f} @ {best[0]:+d} 帧 ({best[0] / fps * 1000:+.0f} ms)")
    print(f"   零时滞 r={float(np.dot(mn, en) / L):+.3f}")
    print(f"\n嘴部运动前 16 帧: {' '.join('%.1f' % v for v in mot[:16])}")
    print(f"音频包络前 16 帧: {' '.join('%.0f' % v for v in env[:16])}")


if __name__ == "__main__":
    main()
