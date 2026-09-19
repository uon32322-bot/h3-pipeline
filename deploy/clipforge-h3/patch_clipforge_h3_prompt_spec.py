#!/usr/bin/env python3
"""Align ClipForge's shot `prompt` field with the H3 official prompting spec.

Two requirements from the operator:
  1. ClipForge prompts must follow H3's official prompt skill requirements
     (three-section format + the chained-shot rules from PROMPTING.md).
  2. The people-realism LoRA's trigger words must sit at the correct position
     (end of integrated_multimodal_description, before overall_soundscape).

Before: the prompt field was asked for as generic diffusion tags
("cinematic, soft lighting, macro shot") - wrong for H3, which is a
narrative-conditioned model, and missing every chained-shot rule.

After: the prompt field is asked for as H3 three-section text.
"""
import re
import shutil
import time

F = "/root/clipforge_src/src/lib/script-engine/prompts.ts"
shutil.copy2(F, f"{F}.bak-{time.strftime('%Y%m%d-%H%M%S')}")
src = open(F).read()

if "H3_PROMPT_SPEC" in src:
    print("prompts.ts: ALREADY PATCHED")
    raise SystemExit(0)

SPEC = '''

/**
 * H3 official prompting spec (injected into the script-generation prompt).
 *
 * The video model is a locally-deployed MiniMax H3 driven by the Multishot
 * chain sampler. Its prompt format is NOT diffusion tag soup - it is a
 * three-section narrative block, and a wrong format makes H3 emit ambience
 * with no speech at all.
 *
 * Sources: ComfyUI-H3-Multishot/PROMPTING.md (six chained-shot rules) and the
 * operator's own production templates (h3-gateway/prompts_v2.py, which is
 * where the realism-LoRA trigger words and their position come from).
 */
export const H3_PROMPT_SPEC = `
【H3 官方提示词规范 —— prompt 字段必须严格遵守（本项目视频模型为本地 MiniMax H3 链式多镜）】

① 三段式结构（每镜的 prompt 就是这一整段文本）：
   第一行（必须是整个 prompt 的第一行，原样照抄，一字不改）：
   For the target video, at 0.00 seconds into the target video, <Picture 1> is fully referenced.
   （空一行）
   integrated_multimodal_description: <风格/画质>，<主体外观与场景>，<动作变化>，<说话与台词>
   overall_soundscape: <环境音 + 物理动作音 + 非语言人声，1-4 句>
   non_diegetic_music: <只有观众听得到的配乐，1-3 句；没有就写 N/A>
   写完最后一行结束，不要加任何额外段落、标题或解释。

② 人物真实度触发短语（realism LoRA 触发词，位置固定不可移动）：
   必须把这句原样放在 integrated_multimodal_description 段的【末尾、紧接 overall_soundscape 之前】：
   true-to-life skin texture preserved, photorealistic real-person look, not CGI
   位置放错（比如放到第一行、或放到 overall_soundscape 之后）触发词不生效。

③ 六条链式铁律（每镜都要满足，这是官方 PROMPTING.md 的硬要求）：
   1. Airlock：除第 1 镜外，每镜必须【以上一镜的精确结束编排开场】——同样的人、同样的位置、
      同样的构图、同样的道具状态——并静默约 2 秒后才开口说话。
   2. 给这段静默找事做：写呼吸、重心微移、视线变化。相机静止，人不要静止。
   3. 稳稳收尾：每镜结尾回到稳定编排、台词说完，并留约 2 秒余量。
   4. 一句台词不跨镜：镜长 8 秒（192 帧）时台词约 20 个英文词或 30 个中文字；
      说不完就把整句挪到下一镜，绝不拆句。
   5. 逐字重复：每镜必须【逐字重复】人物的完整外观描述 + 场景与光线描述（不是改写、不是同义替换）。
      改写描述是"中途换脸"的首要原因。
   6. 每镜改变一个物理元素：动作必须是【物理且不可逆】的（撕掉、倒出、合上、拿起后放下），
      不要写情绪或运镜。自检法：如果两镜的动作行可以互换而脚本仍然读得通，模型也分不清这两镜。

④ 禁止项（违反即出废片）：
   - 禁否定句：不写 "no X"、"does not move"、"without Y" —— CFG=1.0 没有负向分支，
     否定会把概念喂给模型（"no glossy highlights" 会画出高光）。
     一律改写成【正向 + 明确落点】。
   - 禁静止短语：不写 "goes still"、"stays motionless"、"remains frozen" —— 会冻结整帧。
   - 禁 SD 风格标签堆砌：不写 "cinematic, 8k, masterpiece, high contrast, ultra detailed, best quality"。
     H3 吃叙事语句，不吃标签。
   - 禁在镜与镜的边界改变场景（会造成双人/双道具）：场景变化要写在该镜中段。
   - 禁绝对位置与占比：不写 "at frame LEFT"、"occupies 30% of the frame"，
     改写成 "in the position anchored by <Picture 1>"。
`;

'''

# 1) 插入常量（在 PromptTemplate 相关导出之前找一个稳定锚点）
ANCHOR = "/** Script style type */"
if ANCHOR in src:
    src = src.replace(ANCHOR, SPEC.strip() + "\n\n" + ANCHOR, 1)
    print("spec const: INSERTED")
else:
    print("spec const: ANCHOR NOT FOUND")
    raise SystemExit(1)

# 2) 替换 prompt 字段说明
OLD1 = "- prompt: 英文 prompt，用于 AI 图像/视频生成，描述画面主体、风格、光线、构图等"
NEW1 = "- prompt: 英文 prompt，严格按下方【H3 官方提示词规范】写成三段式（不要写标签堆砌）"
if OLD1 in src:
    src = src.replace(OLD1, NEW1, 1)
    print("field rule: PATCHED")
else:
    print("field rule: NOT FOUND")

# 3) 替换"注意事项 4"的 SD 风格要求 -> 引用规范
OLD2 = '4. prompt 字段要用英文，风格描述要专业（如 cinematic, soft lighting, macro shot 等）'
NEW2 = ('4. prompt 字段必须用英文并严格遵守【H3 官方提示词规范】（三段式；'
        '真实度触发短语 true-to-life skin texture preserved, photorealistic real-person look, '
        'not CGI 放在 integrated_multimodal_description 段末尾；六条链式铁律；禁止项全部避开）。'
        '禁止写 cinematic / 8k / masterpiece 这类扩散模型标签。')
if OLD2 in src:
    src = src.replace(OLD2, NEW2, 1)
    print("note 4: PATCHED")
else:
    print("note 4: NOT FOUND")

# 4) system role 里的 prompt 要求
OLD3 = "- prompt 字段用英文写，要具体描述画面构图、光线、色调"
NEW3 = ("- prompt 字段用英文写，必须遵守下方【H3 官方提示词规范】的三段式与六条链式铁律"
        "（含真实度触发短语的固定位置）")
if OLD3 in src:
    src = src.replace(OLD3, NEW3, 1)
    print("system role: PATCHED")
else:
    print("system role: NOT FOUND")

# 5) 把 H3_PROMPT_SPEC 注入到字段规则模板里（在那段模板字符串内插值）
OLD4 = "【画面动作可生成性（description/prompt 硬判据"
NEW4 = "${H3_PROMPT_SPEC}\n\n【画面动作可生成性（description/prompt 硬判据"
if OLD4 in src and "${H3_PROMPT_SPEC}" not in src:
    src = src.replace(OLD4, NEW4, 1)
    print("spec injection: PATCHED")
else:
    print("spec injection: NOT FOUND / ALREADY")

open(F, "w").write(src)
print("--- verify ---")
for i, line in enumerate(open(F).read().splitlines(), 1):
    if "H3_PROMPT_SPEC" in line or "真实度触发短语" in line:
        print(f"  {i}: {line.strip()[:110]}")
