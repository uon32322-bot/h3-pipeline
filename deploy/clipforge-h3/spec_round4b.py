#!/usr/bin/env python3
"""Round 4b: two residuals after round 4.

  * Dialogue is still short (4 of 6 shots came in at 20-27 chars against the 30-33
    budget), which reopens the silent-gap problem.
  * Shot order still jumps between locations (subway -> office desk -> wooden
    desk -> subway) instead of reading as one continuous storyline.
"""
import shutil
import time

F = "/root/clipforge_src/src/lib/script-engine/prompts.ts"
shutil.copy2(F, f"{F}.bak-{time.strftime('%Y%m%d-%H%M%S')}")
src = open(F).read()

if "逐字数一遍" in src:
    print("round4b: ALREADY")
    raise SystemExit(0)

OLD = """     每镜台词字数 = 30~33 字（中文实测语速 3.9 字/秒），并且 duration 字段一律填 8。
     不要按 3/4/5 秒那种短镜去写台词，那样念完会剩好几秒静音（实测出现过 7.3 秒空档）。"""
NEW = """     每镜台词字数 = 30~33 字（中文实测语速 3.9 字/秒），并且 duration 字段一律填 8。
     不要按 3/4/5 秒那种短镜去写台词，那样念完会剩好几秒静音（实测出现过 7.3 秒空档）。
     🔴 硬性校验：写完每一句台词后【自己数字数】；少于 30 字必须补足（补细节、补场景、
     补卖点），多于 34 字必须删减。实测上一版有 4/6 镜只写了 20-27 字 = 不合格，
     会被打回重写。宁可写得具体、有画面感，也不要一句话就结束。"""
if OLD in src:
    src = src.replace(OLD, NEW, 1)
    print("dialogue hard check: PATCHED")
else:
    print("dialogue anchor: NOT FOUND")

OLD2 = """   - 🔴 相邻两镜之间不要突然更换背景。二选一："""
NEW2 = """   - 🔴🔴【全片场景必须是一条连续动线】——同一人物、同一时段的一段连续经历，
     场所要按生活逻辑推进，不能来回跳。示例动线：
     清晨卧室起床 → 出门进电梯 → 早高峰地铁 → 走进办公室坐下 → 午休到楼下跑步 → 傍晚回家沙发
     （注意：相邻两镜的场所必须是【时间上相邻】的两个地方；地铁跳到办公桌、又跳回地铁 = 不合格）
   - 🔴 相邻两镜之间不要突然更换背景。二选一："""
if OLD2 in src:
    src = src.replace(OLD2, NEW2, 1)
    print("scene storyline: PATCHED")
else:
    print("scene anchor: NOT FOUND")

open(F, "w").write(src)
print("--- verify ---")
for i, line in enumerate(open(F).read().splitlines(), 1):
    if any(k in line for k in ["写完每一句台词后【自己数字数】", "【全片场景必须是一条连续动线】"]):
        print(f"  {i}: {line.strip()[:104]}")
