#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""校验 vo/track_full.wav 与 vo/track_manifest.json 是否一致（A-01 / A-04 实测复核）。"""
import json, subprocess, numpy as np

SR = 32000
TRK = "vo/track_full.wav"
MAN = "vo/track_manifest.json"

raw = subprocess.run(["ffmpeg", "-v", "error", "-i", TRK, "-f", "s16le",
                      "-ac", "1", "-ar", str(SR), "-"],
                     capture_output=True, check=True).stdout
x = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
D = len(x) / SR


def db(a):
    if len(a) == 0:
        return -120.0
    r = float(np.sqrt(np.mean(a ** 2)))
    return 20 * np.log10(max(r, 1e-9))


m = json.load(open(MAN, encoding="utf-8"))
print(f"track={D:.3f}s  samples={len(x)}  manifest.actual_dur={m['actual_dur']}")
print(f"整体 RMS={db(x):.1f} dB   峰值={20*np.log10(max(np.abs(x).max(), 1e-9)):.1f} dBFS")
print()
print(f"{'句':<12}{'窗口':>19}{'RMS':>8}{'峰值':>8}   A-01")
ok = True
for s in m["sentences"]:
    a, b = int(s["start"] * SR), int(s["end"] * SR)
    w = x[a:b]
    r = db(w)
    px = 20 * np.log10(max(np.abs(w).max(), 1e-9))
    flag = "✅有声" if r > -45 else "❌近静音"
    if r <= -45:
        ok = False
    win = "%.3f-%.3f" % (s["start"], s["end"])
    print(f"{s['key']:<12}{win:>19}{r:>8.1f}{px:>8.1f}   {flag}")

print()
chk = [("头留白", 0.0, 0.38)]
for i in range(len(m["sentences"]) - 1):
    e = m["sentences"][i]["end"]
    st = m["sentences"][i + 1]["start"]
    chk.append((f"空隙{i + 1}", e + 0.05, st - 0.05))
chk.append(("尾留白", m["sentences"][-1]["end"] + 0.05, D - 0.02))
print(f"{'静音窗口':<10}{'区间':>19}{'RMS':>8}")
for n, a, b in chk:
    w = x[int(a * SR):int(b * SR)]
    win = "%.3f-%.3f" % (a, b)
    print(f"{n:<10}{win:>19}{db(w):>8.1f}")

last = m["sentences"][-1]["end"]
print()
print("A-01 全句有声:", "✅" if ok else "❌")
print("A-04 末句片内结束:", "✅" if last < D else "❌", "(%.3f < %.3f)" % (last, D))
print("时长误差:", "%.4f s" % abs(D - m["target_dur"]),
      "✅" if abs(D - m["target_dur"]) <= 0.3 else "⚠️")
