#!/usr/bin/env python3
"""Replace the ClipForge H3 prompt spec block with the corrected version.

The spec body is read from /root/h3_spec_body.ts (shipped alongside this
script) to avoid quoting hell in nested string literals.
"""
import re
import shutil
import time

F = "/root/clipforge_src/src/lib/script-engine/prompts.ts"
BODY = "/root/h3_spec_body.ts"

shutil.copy2(F, f"{F}.bak-{time.strftime('%Y%m%d-%H%M%S')}")
src = open(F).read()
new_block = open(BODY).read().rstrip() + "\n"

pat = re.compile(r"/\*\*\n \* H3 (official )?prompting spec.*?export const H3_PROMPT_SPEC = `.*?`;\n", re.S)
if pat.search(src):
    src = pat.sub(lambda m: new_block, src, count=1)
    print("spec: REPLACED")
else:
    print("spec: BLOCK NOT FOUND")
    raise SystemExit(1)

OLD4 = ('4. prompt 字段必须用英文并严格遵守【H3 官方提示词规范】（三段式；'
        '真实度触发短语 true-to-life skin texture preserved, photorealistic real-person look, '
        'not CGI 放在 integrated_multimodal_description 段末尾；六条链式铁律；禁止项全部避开）。'
        '禁止写 cinematic / 8k / masterpiece 这类扩散模型标签。')
NEW4 = ('4. prompt 字段必须用英文并严格遵守【H3 官方提示词规范】：以触发词 r34l1sm 开头（第一位），'
        '后面接自由叙述（相机与构图 → 风格 → 人物外观 → 场景 → 动作 → 台词）；'
        '不写任何标签式结构或指令行；禁止项全部避开。')
if OLD4 in src:
    src = src.replace(OLD4, NEW4, 1)
    print("note 4: FIXED")
else:
    print("note 4: not found (checking for leftovers)")
    left = [l for l in src.splitlines() if "true-to-life" in l]
    for l in left:
        print("   leftover:", l.strip()[:120])

# system role line
OLD3 = "- prompt 字段用英文写，必须遵守下方【H3 官方提示词规范】的三段式与六条链式铁律（含真实度触发短语的固定位置）"
NEW3 = "- prompt 字段用英文写，必须以触发词 r34l1sm 开头，并遵守下方【H3 官方提示词规范】的自由叙述写法与禁止项"
if OLD3 in src:
    src = src.replace(OLD3, NEW3, 1)
    print("system role: FIXED")
else:
    print("system role: not found")

# 三段式 mention inside the spec injection line
OLD5 = "- prompt: 英文 prompt，严格按下方【H3 官方提示词规范】写成三段式（不要写标签堆砌）"
NEW5 = "- prompt: 英文 prompt，必须以 r34l1sm 开头，并严格按下方【H3 官方提示词规范】写成自由叙述"
if OLD5 in src:
    src = src.replace(OLD5, NEW5, 1)
    print("field rule: FIXED")
else:
    print("field rule: not found")

open(F, "w").write(src)
print("--- verify ---")
for i, line in enumerate(open(F).read().splitlines(), 1):
    if "r34l1sm" in line or "true-to-life" in line or "三段式" in line:
        print(f"  {i}: {line.strip()[:110]}")
