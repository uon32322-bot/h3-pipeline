#!/usr/bin/env python3
"""Add a consumer-perspective analysis layer ahead of shot generation.

Operator's requirement: the script ClipForge produces must itself be built on
commerce structure and consumer logic - product attributes, target audience,
selling points translated into buying points, product pain points, and usage
scenarios - instead of the operator hand-editing shots afterwards.

What exists today: a golden-3-second hook library and retention rules. What is
missing is the analysis layer that those hooks and shots should be derived from.
"""
import shutil
import time

F = "/root/clipforge_src/src/lib/script-engine/prompts.ts"
shutil.copy2(F, f"{F}.bak-{time.strftime('%Y%m%d-%H%M%S')}")
src = open(F).read()

if "消费者视角分析" in src:
    print("consumer layer: ALREADY")
    raise SystemExit(0)

ANCHOR = "export const RETENTION_CONVERSION_RULES = `"

SPEC = '''/**
 * Consumer-perspective analysis layer (runs BEFORE shot generation).
 *
 * The operator's complaint was that hand-editing shots was the only way to get
 * commerce structure into a script. This makes the structure inherent: every
 * shot must be traceable back to an attribute, an audience, a buying point, a
 * pain point and a scenario.
 */
export const CONSUMER_ANALYSIS_SPEC = `
【消费者视角分析层 —— 必须先完成分析，再写分镜】

生成任何分镜之前，先完成下面五项分析；每一个分镜都必须能对应回这份分析。

① 产品属性（客观事实，不许编造）
   从商品信息里提取可验证的规格：材质、尺寸、重量、颜色款式、功能参数
   （降噪深度、续航时长、防水等级、充电接口、连接协议等）。
   属性回答"它是什么"，不带情绪、不许臆造数字。

② 适用人群（越具体越好）
   写"什么生活状态的人最需要它"，不要泛泛写"年轻人/上班族"：
   ✅ "每天通勤 1 小时以上、地铁上想安静听歌却被外放吵到的人"
   ✅ "每周跑 3-4 次、出汗多又怕耳机滑落的人"
   人群越具体，后面的场景和台词越有代入感。

③ 卖点 → 买点（最关键的一步：features → benefits）
   每一个产品属性都必须翻译成"对用户的好处"。台词说好处，不说参数。
   - 主动降噪 40dB → 地铁上一开，瞬间安静，能听清歌、不被外放吵到
   - 单次续航 8 小时 → 早上戴上，一整天不用想充电
   - IPX5 防水 → 跑步出汗、淋点雨都不怕，甩头也不掉
   - 双设备连接 → 电脑手机来回切，不用反复断开重连
   🔴 台词里禁止直接念参数（"降噪 40 分贝""续航 8 小时"），一律说体验
      （"地铁一开降噪，世界瞬间静音"）。参数交给后期字幕层，画面不出现文字。

④ 产品痛点（用户为什么想换）
   必须写成【能被画面直接演出来的具体场景】，不要抽象词：
   ❌ 抽象："音质不好""戴着不舒服"
   ✅ 具体："地铁上音量开到最大还是听不清，摘下耳机发现耳朵被压得生疼"
   ✅ 具体："开会时耳机漏音，旁边同事抬头看了我一眼，尴尬到想钻地缝"
   至少写 2 条，其中至少 1 条必须能一镜演出来。

⑤ 使用场景（when & where）
   列出 3-5 个真实场景，并选出最适合拍片的 3-4 个：
   早高峰地铁通勤 / 办公室工位 / 午休楼下跑步 / 健身房 / 高铁飞机 / 家里做家务 …
   每个场景要带具体细节才有质感（"早高峰人多到转身都难""工位上两个杯子一台显示器"）。
   场景之间要能构成一条生活动线（见场景衔接条款）。

【分镜必须回扣分析（逐镜自检）】
- 每一镜都要能指出：它在服务哪个痛点、给到哪个买点、发生在哪个场景。
- HOOK 必须取自上面最扎心的一条痛点，或最爽的一条买点。
- 全片至少覆盖 3 个【不同】的买点 —— 不要 5 个镜都在讲降噪。
- 痛点镜与解法镜要成对：先让观众疼，再给解，中间不要跳。
- 台词按消费者说话的习惯写（第一人称、口语），不要写成产品说明书。
`;

'''

if ANCHOR in src:
    src = src.replace(ANCHOR, SPEC + ANCHOR, 1)
    print("consumer layer: INSERTED")
else:
    print("anchor NOT FOUND")
    raise SystemExit(1)

# 在脚本生成的 user prompt 里注入（紧接留人规则之后引用）
OLD2 = "【留人与转化硬规则】"
NEW2 = "${typeof CONSUMER_ANALYSIS_SPEC === 'string' ? '' : ''}\n【留人与转化硬规则】"
# 上面这行只为占位不生效；真正注入走下面的字段规则区

OLD3 = "- voiceover: 中文配音文案，字数约等于 duration x 3；"
NEW3 = ("- consumer_analysis: 可选对象，包含 attributes / target_audience / benefits / pain_points / scenarios "
        "五个数组或字符串，用于记录本次分析（便于追溯；不写也能通过，但写了更利于后续复核）\n"
        "- voiceover: 中文配音文案，字数约等于 duration x 3；")
if OLD3 in src:
    src = src.replace(OLD3, NEW3, 1)
    print("output field: ADDED")
else:
    print("output field anchor: NOT FOUND")

# 把规范本体注入到 user prompt（挂在字段规则之前的模板里）
OLD4 = "【画面动作可生成性（description/prompt 硬判据"
NEW4 = "${CONSUMER_ANALYSIS_SPEC}\n\n【画面动作可生成性（description/prompt 硬判据"
if OLD4 in src and "${CONSUMER_ANALYSIS_SPEC}" not in src:
    src = src.replace(OLD4, NEW4, 1)
    print("spec injection: PATCHED")
else:
    print("spec injection: not found / already present")

open(F, "w").write(src)
print("--- verify ---")
for i, line in enumerate(open(F).read().splitlines(), 1):
    if "CONSUMER_ANALYSIS_SPEC" in line or "consumer_analysis:" in line:
        print(f"  {i}: {line.strip()[:104]}")
