#!/usr/bin/env python3
"""Every shot must carry a prompt, and the H3 sections need their blank lines.

Two defects found by reading real output:
  1. Shots with visualSource=product_image omit `prompt`, so 5 of 7 shots have
     nothing to describe the画面. H3 is a generative model fed per-shot text -
     product_image only supplies the first frame; it does not remove the need
     for a prompt.
  2. The I2VA instruction line and integrated_multimodal_description ended up on
     one line (no blank line between), which the H3 prompt spec requires.
"""
import shutil
import time

F = "/root/clipforge_src/src/lib/script-engine/prompts.ts"
shutil.copy2(F, f"{F}.bak-{time.strftime('%Y%m%d-%H%M%S')}")
src = open(F).read()

OLD = '5. visualSource 为 "product_image" 时，prompt 字段可省略'
NEW = ('5. prompt 字段【每一镜都必须写】，即使是 "product_image" 也不能省 —— '
       'H3 是生成模型，product_image 只决定首帧来源，画面描述仍必须由 prompt 提供')
if "每一镜都必须写" in src:
    print("rule 5: ALREADY")
elif OLD in src:
    src = src.replace(OLD, NEW, 1)
    print("rule 5: PATCHED")
else:
    print("rule 5: NOT FOUND")

# 强调空行（挂在规范 ① 段末）
OLD2 = """   写完最后一行结束，不要加任何额外段落、标题或解释。"""
NEW2 = """   写完最后一行结束，不要加任何额外段落、标题或解释。
   ⚠️ 第一行指令与 integrated_multimodal_description 之间【必须有一个空行】。
   ⚠️ prompt 字段每一镜都要写；不要因为 visualSource 是 product_image 就省略。"""
if "必须有一个空行" in src:
    print("blank line rule: ALREADY")
elif OLD2 in src:
    src = src.replace(OLD2, NEW2, 1)
    print("blank line rule: PATCHED")
else:
    print("blank line rule: NOT FOUND")

open(F, "w").write(src)
print("--- verify ---")
for i, line in enumerate(open(F).read().splitlines(), 1):
    if "每一镜都必须写" in line or "必须有一个空行" in line:
        print(f"  {i}: {line.strip()[:100]}")
