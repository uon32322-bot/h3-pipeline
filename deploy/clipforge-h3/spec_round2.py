#!/usr/bin/env python3
"""Three more spec rules from the operator's review of the first new-spec film.

  1. Dialogue word budget: measured Chinese delivery is ~3.9 chars/second, so an
     8 s shot needs ~31 characters. The first film had 10-20 characters per shot
     and left a 5.8 s silent gap.
  2. Dialogue must match the picture: whatever the line mentions has to actually
     happen in that same shot's visual description.
  3. Realism must go higher: people shots framed mid-close/close so the face is
     readable, natural light, and no skin adjectives at all (they command the
     model to airbrush; r34l1sm owns skin quality).
"""
import shutil
import time

F = "/root/clipforge_src/src/lib/script-engine/prompts.ts"
shutil.copy2(F, f"{F}.bak-{time.strftime('%Y%m%d-%H%M%S')}")
src = open(F).read()

if "镜长 × 3.9" in src:
    print("budget rule: ALREADY")
    raise SystemExit(0)

# ---- 1 & 2: append to section 4
OLD4 = '   - 台词直接写进叙述：and says in Chinese: "台词原文"（中文原样，不翻译、不用 <d> 标签）'
NEW4 = '''   - 台词直接写进叙述：and says in Chinese: "台词原文"（中文原样，不翻译、不用 <d> 标签）
   - 🔴 台词字数 = 镜长（秒）× 3.9（中文实测语速）。8 秒镜 ≈ 31 字，6 秒镜 ≈ 23 字，
     5 秒镜 ≈ 20 字，4 秒镜 ≈ 16 字。字数写少了会在成片里留下大段无声空档
     （实测曾有 2.1s→7.9s 共 5.8 秒的空白），绝对不能短。
   - 🔴 台词内容必须与画面严格对应：台词提到的动作 / 物品 / 场景，必须在【同一镜】的
     画面描述里真实发生（台词说"开盖即连"，该镜画面就必须有开盖动作；台词说"地铁噪音"，
     该镜就必须是地铁场景）。严禁台词讲 A、画面演 B。
   - 同一条台词在一镜内只出现一次，不要重复写两遍。'''
if OLD4 in src:
    src = src.replace(OLD4, NEW4, 1)
    print("budget + alignment: PATCHED")
else:
    print("section 4 anchor: NOT FOUND")

# ---- 3: new section 8 on realism
OLDX = "   - 禁在画面里出现任何文字/字幕，除非该镜本身就是文字卡。"
NEWX = '''   - 禁在画面里出现任何文字/字幕，除非该镜本身就是文字卡。

⑧ 人物真实度（硬指标：接近真人，近景脸部不能粗糙、不能有 AI 感）：
   - 有人出镜的镜，取景必须是【近景或中近景】——脸部清晰、占据画面足够比例、
     能看清皮肤纹理；禁止全身远景 / 背身 / 纯侧脸 / 闭眼低头（这些会浪费人物镜的
     真实度展示机会，也会让观众看不清产品使用效果）。
   - 光线写【自然光或真实环境光】（natural daylight / soft indoor ambient light /
     window light），禁止 flat studio lighting（平光棚拍 = 典型 AI 味）。
   - 🔴 皮肤质感【只由 r34l1sm 触发词负责】。prompt 里不要写任何皮肤形容词：
     smooth / flawless / perfect skin / glowing / dewy / radiant / airbrushed
     —— 它们等于命令模型磨皮。需要约束时只写正向落点：visible real skin texture。
   - 不写 cinematic beauty / model-like / perfect face / symmetrical face 这类美化词。'''
if OLDX in src:
    src = src.replace(OLDX, NEWX, 1)
    print("section 8 realism: INSERTED")
else:
    print("section 8 anchor: NOT FOUND")

open(F, "w").write(src)
print("--- verify ---")
for i, line in enumerate(open(F).read().splitlines(), 1):
    if any(k in line for k in ["镜长 × 3.9", "台词内容必须与画面严格对应", "人物真实度（硬指标", "只由 r34l1sm 触发词负责"]):
        print(f"  {i}: {line.strip()[:110]}")
