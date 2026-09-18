#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V6 动作迁移量化：把「参考视频」和「生成视频」都转成运动指纹，再做交叉相关。

运动指纹 = 
  mag_t   每帧平均光流幅值（归一化后）→ 运动的"节奏"
  grid    4x4 空间网格的平均光流幅值（归一化后）→ 运动的"落点"

对任意两段视频给：
  corr_rhythm, corr_grid, MS = 0.5*corr_rhythm + 0.5*corr_grid
用法: v6motion.py <视频目录> <名字1> <名字2> ...
"""
import os, sys, json
import numpy as np
import cv2

CELL = (160, 288)   # W,H 下采样，够快又不糊


def load_gray(path, max_frames=200):
    cap = cv2.VideoCapture(path)
    frames = []
    while len(frames) < max_frames:
        ok, fr = cap.read()
        if not ok:
            break
        g = cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY)
        g = cv2.resize(g, CELL, interpolation=cv2.INTER_AREA)
        frames.append(g.astype(np.float32))
    cap.release()
    return frames


def fingerprint(path):
    fr = load_gray(path)
    if len(fr) < 3:
        return None
    mags, grids = [], []
    for a, b in zip(fr[:-1], fr[1:]):
        flow = cv2.calcOpticalFlowFarneback(
            a, b, None, 0.5, 3, 15, 3, 5, 1.2, 0)
        mag = np.sqrt(flow[..., 0] ** 2 + flow[..., 1] ** 2)
        mags.append(float(mag.mean()))
        h, w = mag.shape
        gh, gw = h // 4, w // 4
        g = np.array([[mag[i * gh:(i + 1) * gh, j * gw:(j + 1) * gw].mean()
                       for j in range(4)] for i in range(4)])
        grids.append(g)
    mags = np.array(mags)
    grid = np.mean(np.stack(grids), axis=0)          # 静态：整段运动的空间落点
    # 动态：每帧网格 → 逐格时间序列，拼接后作为"时空"指纹
    grids = np.stack(grids)                          # [T,4,4]
    return {"mag_t": mags, "grid": grid, "grids": grids,
            "n": len(fr), "mag_mean": float(mags.mean())}


def corr(a, b):
    a = np.asarray(a, np.float64).ravel()
    b = np.asarray(b, np.float64).ravel()
    n = min(len(a), len(b))
    a, b = a[:n], b[:n]
    if a.std() < 1e-9 or b.std() < 1e-9:
        return float("nan")
    return float(np.corrcoef(a, b)[0, 1])


def ms(fp1, fp2):
    r = corr(fp1["mag_t"], fp2["mag_t"])
    g = corr(fp1["grids"], fp2["grids"])       # 逐格时间序列（时空指纹）
    gs = corr(fp1["grid"], fp2["grid"])        # 静态空间分布
    vals = [v for v in (r, g) if not np.isnan(v)]
    return {"corr_rhythm": r, "corr_spatiotemporal": g,
            "corr_grid_static": gs,
            "MS": float(np.mean(vals)) if vals else float("nan")}


if __name__ == "__main__":
    d = sys.argv[1]
    names = sys.argv[2:]
    fps = {}
    for n in names:
        p = os.path.join(d, n if n.endswith(".mp4") else n + ".mp4")
        if not os.path.exists(p):
            print("MISSING", p); continue
        fps[n] = fingerprint(p)
        print(f"{n:10s} frames={fps[n]['n']:4d}  mean|flow|={fps[n]['mag_mean']:.3f}")
    print("\n=== 交叉运动相关矩阵 (MS) ===")
    hdr = "           " + "".join(f"{n:>12s}" for n in fps)
    print(hdr)
    for a in fps:
        row = f"{a:10s} "
        for b in fps:
            row += f"{ms(fps[a], fps[b])['MS']:12.3f}"
        print(row)
    print("\n=== 明细 ===")
    for a in fps:
        for b in fps:
            if a < b:
                m = ms(fps[a], fps[b])
                print(f"{a:10s} vs {b:10s}  MS={m['MS']:.3f}  "
                      f"rhythm={m['corr_rhythm']:+.3f}  st={m['corr_spatiotemporal']:+.3f}  "
                      f"grid={m['corr_grid_static']:+.3f}")
    json.dump({k: {"mag_mean": v["mag_mean"], "n": v["n"],
                   "grid": v["grid"].tolist()} for k, v in fps.items()},
              open(os.path.join(d, "motion_fp.json"), "w"), indent=1)
