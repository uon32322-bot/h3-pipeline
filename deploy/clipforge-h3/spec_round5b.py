#!/usr/bin/env python3
"""Round 5b: tighten what the consumer-analysis layer exposed.

Observed in the first generation with the consumer layer:
  * Dialogue became rich (39-52 chars) but overshot - 45-52 chars cannot be
    spoken inside the fixed 8 s shot, so it gets compressed or dropped.
  * A "wired earphones" pain-point shot reappeared even though section 9 forbids
    showing any other earphone.
  * Locations still jump (subway -> office -> running) without the continuous
    storyline section 11 asks for.
"""
import shutil
import time

F = "/root/clipforge_src/src/lib/script-engine/prompts.ts"
shutil.copy2(F, f"{F}.bak-{time.strftime('%Y%m%d-%H%M%S')}")
src = open(F).read()

if "台词上限" in src:
    print("round5b: ALREADY")
    raise SystemExit(0)

OLD = "     🔴 硬性校验：写完每一句台词后【自己数字数】；少于 30 字必须补足（补细节、补场景、"
NEW = """     🔴 台词上限：每镜台词不得超过 38 字。实测 45-52 字的台词在 8 秒内根本念不完，
     会被模型压缩甚至吞字。34-38 字是唯一合格区间（下限防空档、上限防念不完）。
     🔴 硬性校验：写完每一句台词后【自己数字数】；少于 30 字必须补足（补细节、补场景、"""
if OLD in src:
    src = src.replace(OLD, NEW, 1)
    print("dialogue cap: PATCHED")
else:
    print("dialogue cap anchor: NOT FOUND")

# 把"有线耳机"这条禁令再说得更硬（LLM 反复违反）
OLD2 = "   - 🔴【绝不允许出现任何别的耳机/竞品/旧款】"
NEW2 = """   - 🔴🔴 痛点镜【最容易犯的错】：不要写"缠成一团的有线耳机""旧耳机""别人的耳机"来做对比 ——
     这条已被用户明确投诉过两次。痛点只能用身体感受与环境表达：
     ✅ "她皱着眉，用手指按住耳侧，周围是外放短视频和哭闹声"
     ✅ "她把音量开到最大，眉头还是紧锁着"
     ❌ "她手上拿着一团缠住的有线耳机线"
     画面里从头到尾只能有【主角那一款产品】。
   - 🔴【绝不允许出现任何别的耳机/竞品/旧款】"""
if OLD2 in src:
    src = src.replace(OLD2, NEW2, 1)
    print("wired ban: HARDENED")
else:
    print("wired ban anchor: NOT FOUND")

# 场景动线：强调 3-5 镜内不要跨太多场所
OLD3 = "     （注意：相邻两镜的场所必须是【时间上相邻】的两个地方；地铁跳到办公桌、又跳回地铁 = 不合格）"
NEW3 = """     （注意：相邻两镜的场所必须是【时间上相邻】的两个地方；地铁跳到办公桌、又跳回地铁 = 不合格）
   - 🔴 5 镜片的场所数量控制在 2-3 个以内（例如"地铁车厢 → 走出站台 → 办公室工位"），
     不要 5 镜跨 5 个地方。场景越集中，观众越不容易出戏。"""
if OLD3 in src:
    src = src.replace(OLD3, NEW3, 1)
    print("scene count: PATCHED")
else:
    print("scene count anchor: NOT FOUND")

open(F, "w").write(src)
print("--- verify ---")
for i, line in enumerate(open(F).read().splitlines(), 1):
    if any(k in line for k in ["台词上限", "痛点镜【最容易犯的错】", "5 镜片的场所数量控制"]):
        print(f"  {i}: {line.strip()[:100]}")
