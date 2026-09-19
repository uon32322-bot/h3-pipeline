#!/usr/bin/env python3
"""Two residual gaps after the round-2 spec.

  1. Product-only shots still dropped their dialogue. Three shots had a voiceover
     but no "says in Chinese" in the prompt, so those shots would render silent
     and reopen the very gap we are trying to close. Product shots need the line
     as an OFF-SCREEN voiceover instead.
  2. Shots whose selling point involves ratings or price still wrote the
     UI into the picture (a review page, on-screen text).
"""
import shutil
import time

F = "/root/clipforge_src/src/lib/script-engine/prompts.ts"
shutil.copy2(F, f"{F}.bak-{time.strftime('%Y%m%d-%H%M%S')}")
src = open(F).read()

if "画外音也必须写进 prompt" in src:
    print("off-screen rule: ALREADY")
    raise SystemExit(0)

OLD = "   - 若该镜确实无人说话（纯产品特写），prompt 里要写明没有人出镜，但仍必须有画面动作。"
NEW = '''   - 🔴 若该镜是纯产品特写（没有人出镜）但 voiceover 里仍有台词，必须把台词写成【画外音】：
     A voiceover says in Chinese: "台词原文" while no one is visible in the frame
     画外音同样必须真的写进 prompt —— 漏掉的话该镜会全静音，整片出现大段空档。
   - 只有当该镜确实一句台词都没有时，才写"无人出镜且无对白"，并且仍必须有画面动作。
   - 台词的播放时长要与镜长匹配：若台词字数明显超出该镜能念完的量（镜长秒数 × 3.9），
     就把句子压到预算内，不要写一长段念不完的话。'''
if OLD in src:
    src = src.replace(OLD, NEW, 1)
    print("off-screen rule: PATCHED")
else:
    print("off-screen anchor: NOT FOUND")

OLD2 = "   - 禁画面内文字：不写 price tag showing…、review page、text on screen、subtitle、caption"
NEW2 = '''   - 禁画面内文字：不写 price tag showing…、review page、text on screen、subtitle、caption
     • 特别注意：即使卖点涉及评分、评价、价格、链接，也【绝对不要】把这些画进画面
       （禁止 review page / rating stars / score 9.2 / price tag / link text / 二维码）。
       这类信息一律交给后期字幕层，画面里只演"使用场景与产品本身"。'''
if OLD2 in src:
    src = src.replace(OLD2, NEW2, 1)
    print("ui ban: HARDENED")
else:
    print("ui ban anchor: NOT FOUND")

open(F, "w").write(src)
print("--- verify ---")
for i, line in enumerate(open(F).read().splitlines(), 1):
    if any(k in line for k in ["画外音也必须写进 prompt", "一律交给后期字幕层", "台词播放时长要与镜长匹配"]):
        print(f"  {i}: {line.strip()[:110]}")
