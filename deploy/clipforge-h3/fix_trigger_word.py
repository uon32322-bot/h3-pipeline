#!/usr/bin/env python3
"""Correct the realism-LoRA trigger word and its position.

My first pass was wrong: I treated the descriptive phrase from the operator's
digital-human template ("true-to-life skin texture preserved, ...") as the
LoRA trigger. The official fal/MiniMax-H3-Realism-People-LoRA model card says:

    Trigger word: r34l1sm
    "Start the prompt with the trigger word r34l1sm, then describe the scene."

So: trigger = `r34l1sm`, position = first token of the scene description.
"""
import shutil
import time

F = "/root/clipforge_src/src/lib/script-engine/prompts.ts"
shutil.copy2(F, f"{F}.bak-{time.strftime('%Y%m%d-%H%M%S')}")
src = open(F).read()

OLD = """② 人物真实度触发短语（realism LoRA 触发词，位置固定不可移动）：
   必须把这句原样放在 integrated_multimodal_description 段的【末尾、紧接 overall_soundscape 之前】：
   true-to-life skin texture preserved, photorealistic real-person look, not CGI
   位置放错（比如放到第一行、或放到 overall_soundscape 之后）触发词不生效。"""

NEW = """② 人物真实度 LoRA 触发词（必须放在 prompt 的第一位，位置错了不生效）：
   本项目挂了 fal 的 MiniMax H3 Realism People LoRA。官方 model card 原文：
   "Trigger word: r34l1sm" / "Start the prompt with the trigger word r34l1sm, then describe the scene."
   → 触发词就是 r34l1sm 这 6 个字符，必须写在【该镜 prompt 的第一个位置】（最开头），
     紧跟一个逗号，然后再写画面与场景描述。例如：
     r34l1sm, 特写：年轻女性戴着无线蓝牙耳机走在通勤地铁上，…（后续描述）
   - 拼写一字不差：r34l1sm（小写 r、数字 3、数字 4、小写 l、数字 1、小写 s、小写 m）
     常见错拼：realism / r34lism / r34l1smn / r3411sm —— 拼错等于没挂 LoRA。
   - 绝不放到段尾、绝不省略。LoRA 官方强度 1.0（0.6-0.8 为轻量档）。
   - 每一镜都要写（每镜 prompt 各自以 r34l1sm 开头）。"""

if "r34l1sm" in src:
    print("trigger fix: ALREADY PRESENT")
elif OLD in src:
    src = src.replace(OLD, NEW, 1)
    open(F, "w").write(src)
    print("trigger fix: PATCHED")
else:
    print("trigger fix: PATTERN NOT FOUND")
    raise SystemExit(1)

print("--- verify ---")
for i, line in enumerate(open(F).read().splitlines(), 1):
    if "r34l1sm" in line or "触发词" in line:
        print(f"  {i}: {line.strip()[:120]}")
