#!/usr/bin/env python3
"""Harden the H3 spec with the three gaps found in the first new-spec output.

Observed in real output (7 shots):
  1. Not one shot contained its dialogue - ClipForge keeps dialogue in the
     `voiceover` field and never writes it into `prompt`, so H3 renders
     ambience with no speech at all.
  2. Almost every shot was a product close-up; nobody appeared on camera.
  3. One shot asked for a split-screen, two asked for on-screen text
     (a price tag, a review page) - both forbidden by the official guide.
"""
import re
import shutil
import time

F = "/root/clipforge_src/src/lib/script-engine/prompts.ts"
shutil.copy2(F, f"{F}.bak-{time.strftime('%Y%m%d-%H%M%S')}")
src = open(F).read()

if "每一镜的 prompt 都必须包含台词" in src:
    print("hardening: ALREADY")
    raise SystemExit(0)

# ④ 段强化：台词必须真的写进 prompt
OLD4 = """④ 台词与环境音写法（照官方示例）：
   - 台词直接写进叙述：and says in Chinese: "台词原文"（中文原样，不翻译、不用 <d> 标签）"""
NEW4 = """④ 台词与环境音写法（照官方示例）—— 这一条最常被写漏，漏了成片就是哑的：
   - 🔴 每一镜的 prompt 里都必须【真的出现】该镜要说的话，写成：
     and says in Chinese: "台词原文"
     台词内容取自该镜的 voiceover 字段（原样，不翻译、不用 <d> 标签）。
   - 只把台词放进 voiceover 字段【不算】—— H3 只认 prompt 文本，prompt 里没有台词就不会生成人声。
   - 若该镜确实无人说话（纯产品特写），prompt 里要写明没有人出镜，但仍必须有画面动作。
   - 台词直接写进叙述：and says in Chinese: "台词原文"（中文原样，不翻译、不用 <d> 标签）"""
if OLD4 in src:
    src = src.replace(OLD4, NEW4, 1)
    print("rule 4: HARDENED")
else:
    print("rule 4: NOT FOUND")

# 新增 ⑦ 出镜与镜头分布（放在 ⑥ 禁止项之前）
OLD6 = "⑥ 禁止项（违反即出废片）："
NEW6 = """⑥ 出镜与镜头分布（带货结构要求，逐镜检查）：
   - 全片【至少 3 镜必须有人出镜】：半身或近景、能看清脸、有真实肤质的人。
     纯产品特写镜最多 2 镜 —— 整片全是产品特写会失去信任感与口播承载。
   - 相邻两镜的主体必须不同（人物镜 / 产品镜 / 使用场景交替出现）；
     严禁连续两镜是同一主体、同一景别、同一构图（那会被看成卡帧或重复镜头）。

⑦ 禁止项（违反即出废片）："""
if OLD6 in src:
    src = src.replace(OLD6, NEW6, 1)
    print("section 6 -> 7: INSERTED")
else:
    print("section marker: NOT FOUND")

# 禁止项内容补强（分屏 / 画面内文字）
OLDX = "   - 禁在画面里出现任何文字/字幕，除非该镜本身就是文字卡。"
NEWX = """   - 禁分屏/拼贴：不写 split-screen、side-by-side、grid、four-panel —— 官方明确禁，会出四宫格。
   - 禁画面内文字：不写 price tag showing…、review page、text on screen、subtitle、caption
     —— H3 会把它渲染成糊字或乱码贴字。需要价格/评价信息时，用后期字幕层加，不要交给画面。
   - 禁在画面里出现任何文字/字幕，除非该镜本身就是文字卡。"""
if OLDX in src:
    src = src.replace(OLDX, NEWX, 1)
    print("forbidden list: HARDENED")
else:
    print("forbidden list: NOT FOUND")

# 重新编号 ⑦->⑧（原禁止项后面的小节没有编号，无需处理）
open(F, "w").write(src)
print("--- verify ---")
for i, line in enumerate(open(F).read().splitlines(), 1):
    if any(k in line for k in ["每一镜的 prompt 里都必须", "至少 3 镜必须有人出镜", "禁分屏/拼贴", "禁画面内文字"]):
        print(f"  {i}: {line.strip()[:110]}")
