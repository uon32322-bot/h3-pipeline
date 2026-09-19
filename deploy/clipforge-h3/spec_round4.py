#!/usr/bin/env python3
"""Round 4: operator's four notes on the R3 film.

  1. Skin should read as a polished TVC/commerce model, not a bare-faced
     close-up with visible freckles. (Still not airbrushed - that was the
     plastic look we removed.)
  2. The script is too shallow: build on the pain point and the hook, and pay
     real attention to scene design.
  3. Scene changes must not happen abruptly - no sudden background swap.
  4. Keep removing the AI feel from both people and environments.

Also fixes the dialogue budget gap: this pipeline renders a fixed 8 s per shot,
so lines must be written for 8 s regardless of ClipForge's own duration field.
"""
import shutil
import time

F = "/root/clipforge_src/src/lib/script-engine/prompts.ts"
shutil.copy2(F, f"{F}.bak-{time.strftime('%Y%m%d-%H%M%S')}")
src = open(F).read()

if "场景设计（脚本层" in src:
    print("round4: ALREADY")
    raise SystemExit(0)

# --- 1) dialogue budget: fixed 8 s per shot
OLD = "   - 🔴 台词字数 = 镜长（秒）× 3.9（中文实测语速）。8 秒镜 ≈ 31 字，6 秒镜 ≈ 23 字，"
NEW = """   - 🔴🔴 本项目【实际渲染时长固定为每镜 8 秒】——因此台词一律按 8 秒写：
     每镜台词字数 = 30~33 字（中文实测语速 3.9 字/秒），并且 duration 字段一律填 8。
     不要按 3/4/5 秒那种短镜去写台词，那样念完会剩好几秒静音（实测出现过 7.3 秒空档）。
   - 台词字数 = 镜长（秒）× 3.9（中文实测语速）。8 秒镜 ≈ 31 字，6 秒镜 ≈ 23 字，"""
if OLD in src:
    src = src.replace(OLD, NEW, 1)
    print("dialogue budget: PATCHED")
else:
    print("dialogue budget: NOT FOUND")

# --- 2) rewrite section 8: polished TVC skin (not bare, not plastic)
OLD8 = "⑧ 人物真实度（硬指标：接近真人，近景脸部不能粗糙、不能有 AI 感）："
NEW8 = """⑧ 人物真实度与皮肤质感（硬指标：带货/TVC 模特级，接近真人且【精致】）：
   - 🔴 皮肤标准 = 专业带货模特 / TVC 模特：【细腻、匀净、干净、有质感】，
     保留真实皮肤的微纹理（看得出是真人皮肤），但**不要素人瑕疵脸**
     （不要在描述里强调 freckles / blemishes / spots / 缺牙 / 油痘）。
     目标：像"化过精致淡妆的专业模特"，既不是素面朝天，也不是磨皮塑料。
   - 正向写法：refined even-toned skin with visible natural skin texture,
     clean natural makeup, healthy skin, soft natural complexion
   - 妆造：写 professional clean natural makeup / neat light makeup，发型整洁（wind-blown 才算动感）
   - 仍然【禁止】磨皮类词：flawless / airbrushed / plastic / waxy / porcelain / doll-like
     这几类词会让皮肤变塑料，是本项目明确否决过的效果。
   - 取景：近景或中近景，脸部清晰、占据画面足够比例；禁全身远景 / 背身 / 闭眼低头。
   - 光线：自然光或专业柔光（natural daylight / soft diffused key light / window light），
     禁止 flat studio lighting（平光棚拍 = 典型 AI 味）。"""
if OLD8 in src:
    src = src.replace(OLD8, NEW8, 1)
    print("section 8 skin: REWRITTEN")
else:
    print("section 8: NOT FOUND")

# --- 3) new sections 10-12 appended after section 9
OLD9 = """⑨ 产品一致性（铁律，全片同一件产品，逐镜检查）："""
NEW9 = """⑩ 场景设计（脚本层硬要求——脚本肤浅的根因就是场景没设计）：
   - 每一镜都必须写明【具体、有生活质感的生活场景】，不是抽象环境：
     ❌ 弱："在地铁里" / "在办公室" / "在户外"
     ✅ 强："早高峰地铁车厢，人挤人，她单手抓着扶手、另一只手还拎着没吃完的早餐"
   - 场景本身要【承载痛点或钩子】：拥挤/嘈杂/赶时间/被人侧目 —— 让观众一秒代入。
   - 全片场景要有一条【连贯动线】：同一个人一段可读的经历
     （例：清晨出门 → 早高峰通勤 → 到工位 → 午休跑步 → 晚上回家），
     而不是 5 个互不相关的场景拼贴。
   - 每镜的钩子/痛点必须落在具体场景里（"地铁嘈杂听不清" 优于 "噪音大"）。

⑪ 场景衔接（防"忽然换背景"——用户明确投诉过）：
   - 🔴 相邻两镜之间不要突然更换背景。二选一：
     (a) 同一场景内换景别 / 换角度（背景保持一致，只变机位与人物动作）；
     (b) 有明确过渡动作的空间转换（走出车门 / 推开办公室门 / 走进健身房 / 坐到桌前），
         让观众看见移动过程，而不是画面硬跳。
   - 每镜的场景与光线描述必须【逐字重复】（同 §⑤）—— 这是背景不跳的机制保证。
   - 服装与妆造全片一致（除剧情明确要求换装）；发型不做无理由变化。
   - 严禁：镜1 地铁 → 镜2 突然户外 → 镜3 突然室内，中间没有任何过渡。

⑫ 去 AI 感（人物 + 场景一起做）：
   - 场景必须有【真实世界的生活痕迹与瑕疵】：地砖反光、墙面磨损、桌面水渍、
     真实杂物、略微不齐的摆放 —— 一尘不染的影棚感本身就是 AI 感。
   - 光线必须【有来源】：写清光从哪来（窗光 / 顶灯 / 台灯 / 屏幕光），
     不要均匀无影的全亮画面（无影 = AI 感）。
   - 人物：保留自然微动作（呼吸、眨眼、重心移动）、衣服真实褶皱、自然碎发。
   - 禁完美词：perfect / spotless / pristine / immaculate / idealized。
   - 场景镜的质感参照"手机随手拍的纪录片质感"，而非"渲染出来的空棚"。

⑨ 产品一致性（铁律，全片同一件产品，逐镜检查）："""
if OLD9 in src:
    src = src.replace(OLD9, NEW9, 1)
    print("sections 10-12: INSERTED")
else:
    print("section 9 anchor: NOT FOUND")

open(F, "w").write(src)
print("--- verify ---")
for i, line in enumerate(open(F).read().splitlines(), 1):
    if any(k in line for k in ["实际渲染时长固定为每镜 8 秒", "皮肤标准 = 专业带货模特", "场景设计（脚本层硬要求", "场景衔接（防", "去 AI 感（人物 + 场景一起做）"]):
        print(f"  {i}: {line.strip()[:104]}")
