#!/usr/bin/env python3
"""Product consistency + fix the over-correction that killed all people shots.

Operator's report: one shot shows a person wearing WIRED earphones while the line
talks about wireless earbuds - the picture contradicts the product pitch.

Also: my previous rule ("a product shot may carry the line as an off-screen
voiceover") was taken as licence to make EVERY shot a product shot - the last
generation produced 6/6 product shots and 0 people.

Fixes:
  * new section 9 - one product, verbatim, everywhere; never show another
    earphone type (not even to illustrate the pain point).
  * section 6 - off-screen voiceover is the EXCEPTION (max 2 shots); at least 3
    shots must still have a person on camera speaking.
"""
import shutil
import time

F = "/root/clipforge_src/src/lib/script-engine/prompts.ts"
shutil.copy2(F, f"{F}.bak-{time.strftime('%Y%m%d-%H%M%S')}")
src = open(F).read()

if "产品一致性" in src:
    print("product consistency: ALREADY")
    raise SystemExit(0)

# --- 1) cap the off-screen escape hatch
OLD = "     画外音同样必须真的写进 prompt —— 漏掉的话该镜会全静音，整片出现大段空档。"
NEW = """     画外音同样必须真的写进 prompt —— 漏掉的话该镜会全静音，整片出现大段空档。
     ⚠️ 但画外音只是【少数例外】：整片最多 2 镜可以用"纯产品特写 + 画外音"，
     其余镜必须有人出镜开口说话。不要用这条规则把整片都做成产品特写。"""
if OLD in src:
    src = src.replace(OLD, NEW, 1)
    print("voiceover cap: PATCHED")
else:
    print("voiceover cap: NOT FOUND")

# --- 2) new section 9: product consistency
OLDX = """⑧ 人物真实度（硬指标：接近真人，近景脸部不能粗糙、不能有 AI 感）："""
NEWX = """⑨ 产品一致性（铁律，全片同一件产品，逐镜检查）：
   - 全片只允许出现【同一款产品】：就是本次主推的那一款，颜色、形态、材质、数量、
     外观描述必须【逐字一致】（例如每镜都写 the same white wireless earbud charging case）。
     严禁同一支片子里产品时黑时白、时大时小、时而有盒时而无盒。
   - 🔴【绝不允许出现任何别的耳机/竞品/旧款】—— 包括为了表现痛点而出现的"有线耳机"、
     "旧耳机"、"别人的耳机"。画面里出现另一种产品，观众会直接判成"产品搞错了"。
   - 痛点通过【人物表情 + 动作 + 环境】表达（皱眉、捂耳、嘈杂车厢、旁人侧目），
     不要用"另一种实物产品"做对比。
   - 需要表现"前后对比"时，只用【同一款产品】的开关状态（降噪开 / 关）来表达。
   - 产品数量恒为 1（同一盒 / 同一支），不要出现第二件同款或备用配件。

⑧ 人物真实度（硬指标：接近真人，近景脸部不能粗糙、不能有 AI 感）："""
if OLDX in src:
    src = src.replace(OLDX, NEWX, 1)
    print("section 9 product: INSERTED")
else:
    print("section 9 anchor: NOT FOUND")

# --- 3) strengthen the people-shot requirement
OLD2 = """   - 全片【至少 3 镜必须有人出镜】：半身或近景、能看清脸、有真实肤质的人。"""
NEW2 = """   - 全片【至少 3 镜必须有人出镜】：半身或近景、能看清脸、有真实肤质的人。
     这是硬性下限——若某次生成全是产品特写（人物 0 镜），本次脚本作废，必须重写。"""
if OLD2 in src:
    src = src.replace(OLD2, NEW2, 1)
    print("people floor: HARDENED")
else:
    print("people floor: NOT FOUND")

open(F, "w").write(src)
print("--- verify ---")
for i, line in enumerate(open(F).read().splitlines(), 1):
    if any(k in line for k in ["产品一致性（铁律", "绝不允许出现任何别的耳机", "这是硬性下限", "但画外音只是【少数例外】"]):
        print(f"  {i}: {line.strip()[:108]}")
