#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V6 看板 & 配色相似度：
 1) 把若干视频/图片拼成纵向对比板（同一时间轴抽帧）
 2) 参考配色相似度：把源图/源视频的调色板与生成结果比对（越低越不像）

用法: v6board.py out.png <item1> <item2> ...
  item = path:tag ，视频按 8 帧均匀抽帧，图片按 8 张复制
"""
import os
import sys

import cv2
import numpy as np

W, H = 190, 333
NCOL = 8


def tile_video(p, n=NCOL):
    cap = cv2.VideoCapture(p)
    tot = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 1
    idxs = np.linspace(0, max(tot - 2, 0), n).astype(int)
    out = []
    for i in idxs:
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(i))
        ok, fr = cap.read()
        if not ok:
            fr = np.zeros((H, W, 3), np.uint8)
        out.append(cv2.resize(fr, (W, H)))
    cap.release()
    return out


def tile_image(p, n=NCOL):
    im = cv2.imread(p)
    if im is None:
        im = np.zeros((H, W, 3), np.uint8)
    im = cv2.resize(im, (W, H))
    return [im.copy() for _ in range(n)]


def label(img, text, color=(40, 40, 40)):
    cv2.rectangle(img, (0, 0), (W, 26), color, -1)
    cv2.putText(img, text, (6, 19), cv2.FONT_HERSHEY_SIMPLEX, 0.52,
                (255, 255, 255), 1, cv2.LINE_AA)
    return img


def palette(p, k=6):
    cv2.setRNGSeed(20260911)   # k-means 随机初始化 → 固定种子保证可复现
    cap = cv2.VideoCapture(p)
    fr = []
    for _ in range(12):
        ok, f = cap.read()
        if not ok:
            break
        fr.append(f)
    cap.release()
    if not fr:
        im = cv2.imread(p)
        if im is None:
            return None
        fr = [im]
    big = np.concatenate([cv2.resize(f, (96, 96)) for f in fr[:12]], 0)
    big = big.reshape(-1, 3).astype(np.float32)
    crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 20, 1.0)
    _, lab, cen = cv2.kmeans(big, k, None, crit, 3, cv2.KMEANS_PP_CENTERS)
    w = np.bincount(lab.ravel(), minlength=k) / len(lab)
    return cen, w


def pal_dist(a, b):
    ca, wa = palette(a)
    cb, wb = palette(b)
    d = np.linalg.norm(ca[:, None, :] - cb[None, :, :], axis=2)
    # 双向最近邻加权：越小越像
    return float((d.min(1) * wa).sum() + (d.min(0) * wb).sum())


if __name__ == "__main__":
    out = sys.argv[1]
    items = [a.split(":", 1) for a in sys.argv[2:]]
    rows = []
    for p, tag in items:
        frames = tile_video(p) if p.lower().endswith((".mp4", ".mov", ".webm")) else tile_image(p)
        lab = label(np.zeros((26, W, 3), np.uint8), tag)
        rows.append(np.vstack([lab] + frames))
    board = np.vstack(rows)
    cv2.imwrite(out, board)
    print("board ->", out, board.shape)

    vids = [p for p, t in items if p.lower().endswith((".mp4", ".webm", ".mov"))]
    if len(vids) > 1:
        print("\n=== 配色相似度矩阵（越小越像）===")
        print(f"{'':16s}" + "".join(f"{os.path.basename(v)[:11]:>13s}" for v in vids))
        for a in vids:
            row = f"{os.path.basename(a)[:15]:15s} "
            for b in vids:
                row += f"{pal_dist(a, b):13.1f}"
            print(row)
