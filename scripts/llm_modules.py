#!/usr/bin/env python3
"""LLM 模块化函数 (灵炫 lk888.ai, 双模型主生产)

主生产模型 (2026-09-21 拍板):
  - gem-3.7-flash: 主力 (快 + 稳定 + JSON 干净)
  - tt-5.6-luna:   备用 (VLM 强, 处理复杂识别)

调用方式 (OpenAI 兼容 API):
  - base: https://api.lk888.ai/v1
  - key: ~/.config/h3p/secrets.env LK888_KEY

fallback 链:
  - gem-3.7-flash 失败 → tt-5.6-luna → 规则版

usage:
  from llm_modules import build_copy_v2, build_storyboard_v2, recognize_product
  info = recognize_product(image_path, product_text)
  copy = build_copy_v2(info, product_text)
  beats = build_storyboard_v2(info, copy)
"""
import json, os, re, base64, urllib.request
from pathlib import Path
from typing import Dict, List

LK888_BASE = "https://api.lk888.ai/v1"
PRIMARY_MODEL = "gem-3.7-flash"
SECONDARY_MODEL = "tt-5.6-luna"
PRIMARY_TIMEOUT = 60
SECONDARY_TIMEOUT = 180  # VLM 慢


def _load_key():
    """从 ~/.config/h3p/secrets.env 读 LK888_KEY"""
    secrets = os.path.expanduser("~/.config/h3p/secrets.env")
    if not os.path.exists(secrets):
        raise RuntimeError("secrets.env 不存在")
    for line in open(secrets):
        line = line.strip()
        if line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        if k == "LK888_KEY":
            return v
    raise RuntimeError("LK888_KEY 未配置")


def call_lk888(model: str, messages: list, temperature: float = 0.7,
                max_tokens: int = 1024, json_mode: bool = False,
                timeout: int = None) -> str:
    """调灵炫 OpenAI 兼容 API"""
    if timeout is None:
        timeout = SECONDARY_TIMEOUT if "tt-5.6-luna" in model else PRIMARY_TIMEOUT
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    req = urllib.request.Request(
        LK888_BASE + "/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {_load_key()}",
                 "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())["choices"][0]["message"]["content"]


def parse_json_strict(raw: str):
    """多层容错: markdown包装+括号匹配+截断"""
    raw = raw.strip()
    raw = re.sub(r"^```json\s*", "", raw)
    raw = re.sub(r"```\s*$", "", raw)
    raw = raw.strip()
    try:
        return json.loads(raw)
    except Exception:
        pass
    for ch_open, ch_close in [("{", "}"), ("[", "]")]:
        idx = raw.find(ch_open)
        if idx < 0:
            continue
        depth = 0
        last_close = -1
        for i in range(idx, len(raw)):
            if raw[i] == ch_open:
                depth += 1
            elif raw[i] == ch_close:
                depth -= 1
                last_close = i
                if depth == 0:
                    try:
                        return json.loads(raw[idx:i+1])
                    except Exception:
                        break
        if last_close > idx:
            try:
                return json.loads(raw[idx:last_close+1])
            except Exception:
                pass
    raise RuntimeError(f"JSON 解析失败")


def img_b64_uri(path: str) -> str:
    """图片 → data URI (base64)"""
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    e = Path(path).suffix.lstrip(".").lower()
    mime = "image/jpeg" if e in ("jpg", "jpeg") else f"image/{e}"
    return f"data:{mime};base64,{b64}"


# ============ 任务 1: 产品识别 (VLM) ============
def recognize_product(image_path: str, product_text: str = "",
                       primary_first: bool = True) -> dict:
    """主调用 gem-3.7-flash, 失败 fallback tt-5.6-luna

    输入: image_path (产品图), product_text (用户文字信息)
    输出: {category, name, color, shape, key_attributes, skin_tone_match, usage_scene, product_form}
    """
    data_uri = img_b64_uri(image_path)
    prompt = f"""你是产品识别专家。只输出 JSON 对象, 不要 markdown 包装.

**重要 (8 维度 + 否定锚点 + 物理形态优先)**:
1. **物理形态先于颜色**: 必填"包装形态"字段, 写"翻盖塑料软管/金色方管/矮胖圆瓶/..."而不是"塑料瓶"
2. **形态细到细节**: 管长/粗细/瓶口类型/是否有翻盖/是否有按钮
3. **不要做联想**: 只输出你看图能确认的事实, 不杜撰
4. **避免幻觉化**: 红+金+管身 ≠ 一定是口红 (可能是香水笔/眼线笔), 必须看真实形态

输出格式 (严格按字段顺序):
{{
  "category": "口红/洁面乳/护肤品/家电/服饰/食品/...",
  "name": "品牌+产品名",
  "color": "主色调 (如'正红色'/'白色'/'银灰色')",
  "shape": "包装形态 (必须细: '翻盖塑料软管白色管身银色翻盖'/'金色方管磁吸盖'/'矮胖圆瓶按压泵头')",
  "key_attributes": ["卖点1", "卖点2", "卖点3"],
  "skin_tone_match": "冷白皮/暖黄皮/百搭/不限",
  "usage_scene": "通勤/约会/家居/学习/...",
  "product_form": "tube/bottle/jar/compact/box (用其中一个值)"
}}

{f'用户文字信息(优先, 当与图冲突时以文字为准): {product_text[:400]}' if product_text else ''}"""
    models = [PRIMARY_MODEL, SECONDARY_MODEL] if primary_first else [SECONDARY_MODEL, PRIMARY_MODEL]
    last_err = None
    for m in models:
        try:
            raw = call_lk888(
                m,
                [{"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": data_uri}},
                    {"type": "text", "text": prompt}
                ]}],
                temperature=0.3, max_tokens=800,
            )
            result = parse_json_strict(raw)
            if not isinstance(result, dict):
                last_err = f"{m} 返回非 dict"
                continue
            result["_model"] = m
            return result
        except Exception as e:
            last_err = f"{m}: {e}"
            continue
    return {"_error": last_err or "all failed"}


# ============ 任务 2: 文案生成 (LLM) ============
def build_copy_v2(product_info: dict, product_text: str = "") -> dict:
    """5 段式带货文案

    输入: product_info (识别结果), product_text (用户文字)
    输出: {tagline, selling_points[3], scene, cta}
    """
    info_s = "\n".join([f"{k}:{v}" for k, v in product_info.items()
                        if v and not str(k).startswith("_") and not isinstance(v, list)])
    sp_str = ",".join(product_info.get("key_attributes", [])[:3]) \
             if isinstance(product_info.get("key_attributes"), list) else ""
    sys_p = """5 段式带货口播稿专家。回复必须是严格 JSON, 不要其他文字, 不要 markdown.
格式: {"tagline":"1-2句钩子","selling_points":["s1","s2","s3"],"scene":"使用场景","cta":"点击下方小黄车直接下单"}

**8 维度 + 否定锚点 + 物理形态优先** (必须贯穿文案):
1. **物理形态先于颜色**: 文案中提到产品时, 必须描述"形态+颜色", 不能只说颜色
   ✅ "翻盖塑料软管白色管身" / "金色方管磁吸盖" / "矮胖圆瓶按压泵头"
   ❌ "白色管身" / "金色管" (只有颜色, 没有形态)
2. **否定锚点 (negation)**: 文案不要把产品描述成其它品类
   例: 洁面乳 ≠ 口红 ≠ 香水 ≠ 粉底; 牙膏 ≠ 洁面乳; 耳机 ≠ 音箱
   写到卖点时, 不要让读者误以为是其他产品
3. **形态决定动作**: 不同形态对应不同使用动作
   tube (软管) → 挤压 / squeeze
   bottle (瓶) → 按压 / 倾倒 / 喷
   jar (罐) → 挖取 / scoop
   compact (盒) → 打开 / 翻盖
4. **避免幻觉化联想**: 不要因为颜色联想品类
   红色软管 + 金色翻盖 → 可能是香水笔/眼线笔/护手霜, 不一定是口红

**5 段式硬约束** (来自 skill: copywriting-judge-optimization + storyboard-script-quality):
1. 钩子必须用"危言耸听/反常识/提问"之一 (例: "地铁再吵也别硬扛!"), 禁用"大家好"开场
2. 3 条卖点必须"1 个具体画面 + 1 句购买理由" (例: 涂完去吃饭不掉色), 禁用空泛词 (顺/利落/显精神/好用)
3. 禁止杜撰数字 (如"白两度"/"16小时持妆"), 数字必须与产品一致
4. ≤25字/段, 口语化, 像说话不像写作
5. 形态贯穿: 卖点里描述使用动作时, 必须符合产品形态 (例: 软管 = 挤压; 瓶 = 按压)"""
    user_p = f"产品:{product_info.get('name','?')}, 类别:{product_info.get('category','')}, 形态:{product_info.get('shape', product_info.get('product_form',''))}, 卖点:{sp_str}, 用户文字:{product_text[:400] if product_text else ''}\n\n请生成严格 5 段式 JSON, 文案中必须体现产品物理形态"
    last_err = None
    for m in [PRIMARY_MODEL, SECONDARY_MODEL]:
        try:
            raw = call_lk888(
                m,
                [{"role": "system", "content": sys_p},
                 {"role": "user", "content": user_p}],
                temperature=0.8, max_tokens=1500,
            )
            result = parse_json_strict(raw)
            if not isinstance(result, dict):
                last_err = f"{m} 返回非 dict"
                continue
            result["_model"] = m
            return result
        except Exception as e:
            last_err = f"{m}: {e}"
            continue
    return _fallback_copy(product_info)


def _fallback_copy(info: dict) -> dict:
    """规则版文案 (兜底)"""
    name = info.get("name", "产品")
    sp = info.get("key_attributes", ["好用", "实惠", "质量好"])
    if not isinstance(sp, list):
        sp = ["好用", "实惠", "质量好"]
    sp = sp[:3] if len(sp) >= 3 else sp + ["值得入手"] * (3 - len(sp))
    return {
        "tagline": f"{name}, 你值得拥有",
        "selling_points": sp,
        "scene": "日常使用",
        "cta": "点击下方小黄车直接下单",
        "_model": "fallback",
    }


# ============ 任务 3: 分镜脚本 (LLM) ============
def build_storyboard_v2(product_info: dict, copy: dict) -> list:
    """6 镜分镜 (按品类动态动作)

    输入: product_info, copy
    输出: [{beat_id, time_range:[s,e], keyframe, description}, ...] x 6
    """
    cat = product_info.get("category", "")
    name = product_info.get("name", "")
    pf = product_info.get("product_form", "tube")
    shape = product_info.get("shape", "")  # 包装形态 (详细)
    color = product_info.get("color", "")
    tagline = copy.get("tagline", "") if isinstance(copy, dict) else ""
    sp = (copy.get("selling_points", ["卖点"]) if isinstance(copy, dict) else ["卖点"])[:3]
    sys_p = """8s 带货分镜师。回复必须是严格 JSON 数组, 不要 markdown 包装.

**字段名严格规范 (避免被识别为"格式错")**:
- 每镜用以下任一格式, **统一字段名**:
  - 格式 A (我们): {beat_id, time_range:[s,e], keyframe, description}
  - 格式 B (gem 自然): {shot_id, time:"0-2s", scene, visual, audio}
- 如果你倾向输出 4 镜, 时间累加必须 = 8s (例: 0-2, 2-4, 4-6, 6-8)
- 如果你倾向输出 6 镜, 时间累加必须 ≤8s (例: 0-1.3, 1.3-2.6 ... 6.5-8.0)
- 每镜 visual/description 必须包含: 主体外观 + 产品形态(形态+颜色) + 手部动作(拇指/食指/掌心/虎口) + negation (NOT 口红/NOT 香水)
- description 可以包含 audio (口播) 信息, 用"|"分隔

6 元素 (推荐) [{beat_id|shot_id,time_range|time:[s,e]或"0-2s",keyframe|scene,description|visual}]

**8 维度 + 否定锚点 + 物理形态优先** (来自 skill: h3-prompt-8-dimensions-and-hallucination-defense):

| 维度 | 必填 | 示例 |
|---|---|---|
| subject | 人物外观 | 椭圆脸/单眼皮/小尖鼻/长直发 |
| product_form | **物理形态先于颜色** | 白色塑料软管+翻盖 / 金色方管+磁吸盖 / 矮胖圆瓶+按压泵头 |
| hand_action | 手部细到指节 | 拇指+食指捏住 / 掌心托住 / 虎口卡住 |
| purpose | 动作目的 | 挤压膏体到掌心 / 展示口红外观 |
| quantity | 数量限定 | only ONE product visible |
| negation | 否定锚点 | NOT lipstick / NOT perfume / NOT foundation / no red color |
| camera | 镜头景别 | close-up on hands / macro on face |
| time_text | 时间范围 | Between 0.000s and 1.300s |

**形态约束 (避免幻觉化)**:
- 描述产品时必须**形态+颜色**组合: "白色塑料软管" 而非 "白色管身"
- 不同形态对应不同使用动作:
  tube (软管) → 拇指食指挤压 / squeeze
  bottle (瓶) → 按压 / 倾倒 / 喷
  jar (罐) → 挖取
  compact (盒) → 翻开 / 打开
- 否定锚点必填: 每镜 description 里至少出现 1 个 negation 词 (不是口红/不是香水/不要红色)

**4 类硬约束** (来自 skill: storyboard-script-quality):
1. **时长严格**: 总时长 6 镜累加 ≤8s, 单镜 ≤1.3s (典型 0.0-1.3/1.3-2.6/2.6-3.9/3.9-5.2/5.2-6.5/6.5-8.0)
2. **单镜动作唯一**: 每镜只允许 1 个主动作 (如"涂抹"), 最多 1 个次动作; 禁止 2+ 动作串联
3. **禁止时序跳变词**: 不用"随后/接着/然后/连续切换/三晚/三天后"
4. **镜1镜6镜像**: 起始微笑+结尾微笑展示
5. **中间 2 镜是核心动作**: 涂抹类=膏体贴+涂抹, 穿戴类=穿戴+展示, 食品类=开包装+品尝
6. **产品前 3 秒入画**: 镜1画面必须让产品实体出现 (拿手里/摆桌面/人物使用)

**卖点→分镜 强对应 (来自 K5 评测, 关键硬约束)**:
- **每个文案卖点 (selling_points) 必须被 6 镜中至少 1 镜直接体现**, 不能丢
- **tagline 的"钩子元素"必须出现在镜1或镜6** (开场钩子或结尾呼应)
- **scene 描述的场景必须是镜2-5 的核心场景**
- 6 镜**整体必须能"看完就懂"3 个卖点**, 不能只展示产品外观不展示卖点动作

**卖点映射示例**:
- 文案: "扔进洗衣机洗50次" → 分镜镜5: 必须有"洗衣机/手洗/挂晾"动作
- 文案: "100s棉滑得像没布" → 分镜镜3-4: 必须有"手抚被套面料/脸贴被套"特写
- 文案: "A类母婴级, 宝宝都能睡" → 分镜镜2: 必须有"宝宝入画躺下"动作
- 文案: "化纤被套捂出红疹" → 分镜镜1: 必须有"化纤被套对比/红疹/问题展示" (痛点开场)"""
    user_p = f"""产品:{name}
类别:{cat}
**物理形态 (必填, 详细到材质+结构):** {shape or pf}
颜色:{color}
**tagline (钩子, 镜1或镜6 必须呼应):** {tagline}
**selling_points (3 个, 每个必须被至少 1 镜直接体现):**
  - SP1: {sp[0] if len(sp) > 0 else ''}
  - SP2: {sp[1] if len(sp) > 1 else ''}
  - SP3: {sp[2] if len(sp) > 2 else ''}
scene: {copy.get('scene', '') if isinstance(copy, dict) else ''}

请输出严格 6 镜 JSON 数组:
- 镜1: 对应 tagline 钩子 (开场)
- 镜2-5: 对应 3 个 SP (每个 SP 至少 1 镜)
- 镜6: 镜像镜1, 呼应钩子或收尾展示

每镜 description 必须包含:
1. 具体产品形态描述 (形态+颜色)
2. 手部动作 (拇指/食指/掌心/虎口等)
3. negation 词 (NOT 口红/NOT 香水 等)
4. 对应哪个 SP 或 tagline 元素
5. 时长累加 ≤8s"""
    last_err = None
    for m in [PRIMARY_MODEL, SECONDARY_MODEL]:
        # gem-3.7-flash 用 2500 tokens (更长的 8 维度 prompt 需要)
        max_tok = 2500 if m == PRIMARY_MODEL else 3000
        timeout = 90 if m == SECONDARY_MODEL else 90  # gem 慢的时候也 90s
        try:
            raw = call_lk888(
                m,
                [{"role": "system", "content": sys_p},
                 {"role": "user", "content": user_p}],
                temperature=0.6, max_tokens=max_tok, timeout=timeout,
            )
            beats = parse_json_strict(raw)
            if not isinstance(beats, list):
                last_err = f"{m} 返回非 list"
                continue
            # 字段映射: gem-3.7-flash 用 shot_id/time/visual/audio/text_overlay
            # 我们的标准是 beat_id/time_range/description/keyframe
            # 这里做容错: visual→description, scene→keyframe, time→time_range
            for i, b in enumerate(beats):
                if not isinstance(b, dict):
                    beats[i] = {"beat_id": i+1, "time_range": [i*1.3, (i+1)*1.3],
                                "keyframe": f"sec{i}", "description": f"镜{i+1}: (内容缺失)"}
                    continue
                # 描述 (visual 优先, 否则拼接 audio)
                if "description" not in b:
                    desc_parts = []
                    if "visual" in b: desc_parts.append(str(b["visual"]))
                    if "audio" in b: desc_parts.append("口播: " + str(b["audio"]))
                    if not desc_parts and "scene" in b:
                        desc_parts.append(str(b["scene"]))
                    b["description"] = " | ".join(desc_parts) if desc_parts else f"镜{i+1}: (内容缺失)"
                # 镜号
                if "beat_id" not in b:
                    b["beat_id"] = b.get("shot_id", i+1)
                # 时间 (time "0-2s" 格式 → [0.0, 2.0])
                if "time_range" not in b:
                    time_str = str(b.get("time", ""))
                    m = re.match(r'(\d+(?:\.\d+)?)\s*[-~]\s*(\d+(?:\.\d+)?)', time_str)
                    if m:
                        b["time_range"] = [float(m.group(1)), float(m.group(2))]
                    else:
                        b["time_range"] = [i*1.3, (i+1)*1.3]
                # keyframe (scene → keyframe)
                if "keyframe" not in b:
                    b["keyframe"] = b.get("scene", f"sec{i}")
            # 长度调整: 多了截 6, 少了用 fallback 补
            if len(beats) > 6:
                beats = beats[:6]
                # 重新校准 time_range 到 0-8s 等分
                for i, b in enumerate(beats):
                    b["time_range"] = [i * 8.0 / 6, (i + 1) * 8.0 / 6]
            elif len(beats) < 6:
                # 用 fallback 补齐缺口 (但保留 gem 已生成的部分)
                fallback = _fallback_storyboard(product_info)
                while len(beats) < 6:
                    beats.append(fallback[len(beats)])
            # 强制 time_range 累加 ≤8s (重新分配)
            for i, b in enumerate(beats):
                b["time_range"] = [i * 8.0 / 6, (i + 1) * 8.0 / 6]
                b["beat_id"] = i + 1
                if not isinstance(b.get("description"), str) or len(b["description"]) < 5:
                    b["description"] = f"镜{i+1}: (内容缺失)"
            for b in beats:
                b["_model"] = m
            return beats
        except Exception as e:
            last_err = f"{m}: {e}"
            continue
    return _fallback_storyboard(product_info)


def _fallback_storyboard(info: dict) -> list:
    """规则版 6 镜 (按品类)"""
    cat = info.get("category", "其他")
    name = info.get("name", "产品")
    action_map = {
        "洁面": ("双手手背轻触脸颊,展示T区油光", "右手握洁面乳管身,左手推开翻盖", "右手拇指食指挤压管身,在左手掌挤出2cm乳白膏体", "双手掌心沾温水打圈揉搓,5秒拉丝出绵密泡沫", "泡沫覆于面部,指腹打圈清洁鼻翼", "清水冲净,双手食指弹脸颊特写,展示净澈素颜"),
        "口红": ("手持金色口红管", "旋转出膏体特写", "膏体贴下唇", "抿唇晕染推开", "微笑展示完整唇色", "露齿微笑"),
        "零食": ("撕开包装袋", "展示薯片/小吃", "拿起一片", "放入口中品尝", "咀嚼享受", "满足微笑"),
        "积木": ("拆开积木包装", "倒出彩色积木块", "取两块拼接卡扣", "继续拼接堆叠", "举起来展示成品", "完成造型"),
        "水果": ("展示完整水果", "切开露出果肉", "展示新鲜果肉特写", "品尝一块", "甜美表情", "健康笑容"),
        "运动鞋": ("展示鞋面透气网布", "穿到脚上", "系鞋带特写", "走路展示", "踩踏测试轻量感", "整体造型"),
        "空气炸锅": ("展示空气炸锅外观", "打开炸锅盖", "放入食材", "旋转定时旋钮", "运行展示", "成品出炉"),
        "牙膏": ("挤压牙膏到牙刷", "展示绵密泡沫", "刷牙动作", "漱口", "展示亮白牙齿", "自信微笑"),
        "耳机": ("展示充电盒", "打开盒盖", "取出耳机戴到耳朵", "触摸操作", "听音乐享受表情", "佩戴展示"),
    }
    beats_desc = None
    for k, v in action_map.items():
        if k in cat or k in name:
            beats_desc = v
            break
    if not beats_desc:
        beats_desc = ("手持产品", "打开包装", "使用过程", "使用中", "完成使用", "成品展示")
    beats = []
    times = [(0.0, 1.3), (1.3, 2.6), (2.6, 3.9), (3.9, 5.2), (5.2, 6.5), (6.5, 7.8)]
    for i, (s, e) in enumerate(times):
        beats.append({
            "beat_id": i + 1,
            "time_range": [s, e],
            "keyframe": f"sec{i}",
            "description": f"镜{i+1}: {beats_desc[i]}",
            "_model": "fallback",
        })
    return beats


# ============ 任务 4: 宫格图 prompt 构建 (v6) ============
def build_grid_prompt_v6(info: dict, beats: list, product_text: str = "") -> str:
    """调 grid_prompt_v6 模块"""
    from grid_prompt_v6 import build_grid_prompt
    return build_grid_prompt(info, beats, product_text)


# ============ 任务 5: 宫格图生成 (灵炫 tt-image-2) ============
def generate_grid_image(prompt: str, output_path: str, model: str = "tt-image-2") -> dict:
    """调灵炫生图 (同步模式, 直接返回 b64)"""
    req = urllib.request.Request(
        LK888_BASE + "/images/generations",
        data=json.dumps({"model": model, "prompt": prompt, "size": "720x1280", "n": 1}).encode(),
        headers={"Authorization": f"Bearer {_load_key()}",
                 "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            resp = json.loads(r.read().decode())
        item = resp["data"][0]
        b64 = item.get("b64_json") or item.get("url")
        if b64.startswith("http"):
            req2 = urllib.request.Request(b64, headers={"User-Agent": "curl/8"})
            with urllib.request.urlopen(req2, timeout=30) as resp2:
                with open(output_path, "wb") as f:
                    f.write(resp2.read())
        else:
            with open(output_path, "wb") as f:
                f.write(base64.b64decode(b64))
        return {"_ok": True, "size_kb": os.path.getsize(output_path) // 1024}
    except Exception as e:
        return {"_error": str(e)}


# ============ 端到端接口 (给 ecom_end_to_end 用) ============
def run_full_pipeline(image_path: str, product_text: str, output_dir: str,
                       product_id: str = None) -> dict:
    """完整 4 阶段: 识别 → 文案 → 分镜 → 宫格图

    输入: image_path, product_text, output_dir (本地保存)
    输出: 完整结果 dict + 4 个文件
    """
    os.makedirs(output_dir, exist_ok=True)
    product_id = product_id or Path(image_path).stem

    print(f"[1/4] 产品识别 (VLM)...", flush=True)
    info = recognize_product(image_path, product_text)
    print(f"  → {_model_name(info)}", flush=True)

    print(f"[2/4] 文案生成 (LLM)...", flush=True)
    copy = build_copy_v2(info, product_text)
    print(f"  → {_model_name(copy)}", flush=True)

    print(f"[3/4] 分镜脚本 (LLM)...", flush=True)
    beats = build_storyboard_v2(info, copy)
    print(f"  → {_model_name(beats[0]) if beats else '?'}", flush=True)

    print(f"[4/4] 宫格图生成 (tt-image-2)...", flush=True)
    grid_path = os.path.join(output_dir, f"{product_id}_grid.png")
    grid_prompt = build_grid_prompt_v6(info, beats, product_text)
    grid_res = generate_grid_image(grid_prompt, grid_path)
    print(f"  → {grid_res}", flush=True)

    result = {
        "recognition": info,
        "copy": copy,
        "storyboard": beats,
        "grid_result": grid_res,
        "grid_prompt": grid_prompt,
    }
    json_path = os.path.join(output_dir, f"{product_id}.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"\n全部产物已保存到: {output_dir}/", flush=True)
    return result


def _model_name(obj):
    if isinstance(obj, dict):
        return obj.get("_model", "?")
    if isinstance(obj, list) and obj and isinstance(obj[0], dict):
        return obj[0].get("_model", "?")
    return "?"


if __name__ == "__main__":
    print(f"主生产模型: {PRIMARY_MODEL} + {SECONDARY_MODEL}")
    print(f"基地址: {LK888_BASE}")