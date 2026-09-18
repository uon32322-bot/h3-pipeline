#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
时长档 × 分段方案 成本模型（768×1344 / 8 步 Turbo / 3090 显存受限）

标度（实测锚点重拟合，两端精确）：
    t(f) = 431 × (f/124)^1.6697  秒
    实测：124 帧 = 431.0 s（7.18 min）｜243 帧 = 1325.5 s（22.09 min）
    指数由两点反解：P = ln(1325.5/431) / ln(243/124) = 1.6697
    （旧写 P=1.70 会在 243 帧处高估 2%）
    362 帧为外推（边际 ≈ 176.6 s GPU / 成片秒）

两种成本口径（结论相反 ⇒ 分段数不听成本的）：
  R 口径（可逐段重抽）：段越短越省（t 超线性 ⇒ Σt(短) < t(长)）
  O 口径（要求一次过）  ：段越少越省（省的是重抽期望，不是单次）
"""
import math

A, P = 431.0, 1.6697
CAP = 362          # 单段上限 = 15.083 s @24fps
FPS = 24


def t(f):
    """单段生成秒数"""
    return A * (f / 124.0) ** P


def grid(lo=5, hi=CAP):
    """合法段帧数 = 17k+5"""
    return [17 * k + 5 for k in range(1, 22) if lo <= 17 * k + 5 <= hi]


def fmt(f):
    return f"{f:>3}f = {f/FPS:>6.3f}s = {t(f)/60:>5.1f} min"


# 已定方案：运动鞋 40s 五段
PLAN_40 = [
    ("G1", 243, "镜1–3 钩子+痛点"),
    ("G2", 362, "镜4–6 价值证明+续命钩+上脚"),
    ("G3", 175, "镜7 信任背书"),
    ("G4", 107, "镜8 CTA 收束"),
    ("G5",  73, "镜9 CTA 定格"),
]

print("=" * 74)
print("合法段帧数（17k+5，≤362）：", grid())
print("=" * 74)
print()
print("【单段耗时曲线】")
for f in [73, 90, 107, 124, 141, 175, 192, 226, 243, 294, 328, 362]:
    print("   ", fmt(f))
print()
print("【边际成本】每多 1 秒成片要多少 GPU 秒（越长越贵 ⇒ 分段的诱惑）")
for f in [124, 243, 362]:
    print(f"    {f:>3}f（{f/FPS:5.2f}s）：{t(f)/(f/FPS):6.1f} s GPU / 成片秒")
print()
print("【40 s 档 · 已定五段方案】")
tot_f = sum(p[1] for p in PLAN_40)
tot_t = sum(t(p[1]) for p in PLAN_40)
for name, f, note in PLAN_40:
    print(f"    {name}  {fmt(f):<34} {note}")
print(f"    {'合计':<4}{tot_f:>3}f = {tot_f/FPS:>6.3f}s = {tot_t/60:>5.1f} min")
assert tot_f == 960, tot_f
print(f"    ✅ 五段之和 = 960 f 恰好 = 40.000 s（无需裁切）")
print()
print("【对照：同样 960 f 只用 4 段会被迫超限 / 用短段更省】")
# 960 拆 4 段：Σ(17k+5)=17Σk+20=960 ⇒ Σk=55.29 非整数 ⇒ 不可能
print("    4 段：17Σk+20=960 ⇒ Σk=55.29 ✗ 无整数解 ⇒ 4 段必有一处裁切/超限")
alt = [243, 362, 175, 180]   # 180 非法
print(f"    反例 {alt} → 180 不在 17k+5 网格 ✗")
print()
print("【R 口径演示：同样 180 f 的 CTA 段，1 段 vs 2 段】")
one = t(180)
two = t(107) + t(73)
print(f"    1 段 180f（非法示意）≈ {one/60:.1f} min ｜ 2 段 107+73 = {two/60:.1f} min"
      f"  ⇒ 拆开省 {(1-two/one)*100:.0f}%")
print()
print("【三档时长预算（可重抽 R 口径）】")
for tier, total_f, note in [("32 s", 770, "4 段"), ("40 s", 960, "5 段"), ("50 s", 1195, "6–7 段")]:
    # 用等分近似给一个量级
    n = {"32 s": 4, "40 s": 5, "50 s": 6}[tier]
    each = [x for x in grid() if abs(x - total_f / n) < 40]
    print(f"    {tier:<6} ≈ {total_f}f = {total_f/FPS:.2f}s ｜ {note}"
          f" ｜ 单段均 ~{total_f/n:.0f}f（最近网格 {min(each, key=lambda g: abs(g-total_f/n))}）")
print()
print("    ⚠️ 成片总长不必贴 17k+5 网格 —— 只有『段』必须。")
print("       各段可生成长度之和 ≥ 目标即可，多余帧在装配时裁掉。")
