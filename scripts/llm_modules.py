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
    """10 镜分镜 (5 段 × 2 镜, 每段 4s, 总时长 20s)

    输入: product_info, copy
    输出: [{beat_id, time_range:[s,e], keyframe|scene, description|visual, segment:N}, ...] x 10

    段分配 (40s 完整视频拆 5 段, 每段 8s, 每段含 2 镜):
    段 1 (0-8s):   镜 1-2 = 钩子 + 痛点
    段 2 (8-16s):  镜 3-4 = 产品亮相 + 卖点 1
    段 3 (16-24s): 镜 5-6 = 卖点 2 + 卖点 3
    段 4 (24-32s): 镜 7-8 = 使用场景/生活化
    段 5 (32-40s): 镜 9-10 = CTA + 收尾展示

    每镜 time_range 是 **整段视频** 的绝对时间 (0-40s), 用于 H3 段间同步
    每镜 time_range[0]/[1] 标识属于哪一段 (floor(t/8) + 1)
    """
    cat = product_info.get("category", "")
    name = product_info.get("name", "")
    pf = product_info.get("product_form", "tube")
    shape = product_info.get("shape", "")  # 包装形态 (详细)
    color = product_info.get("color", "")
    tagline = copy.get("tagline", "") if isinstance(copy, dict) else ""
    sp = (copy.get("selling_points", ["卖点"]) if isinstance(copy, dict) else ["卖点"])[:3]
    sys_p = """40s 完整带货视频分镜师。回复必须是严格 JSON 数组, 不要 markdown 包装.

**核心架构: 40s 完整视频 = 5 段 × 8s = 10 镜 (每段 2 镜, 每镜 4s)**
- 段 1 (0-8s):   镜 1-2 = 钩子(0-4s) + 痛点(4-8s)
- 段 2 (8-16s):  镜 3-4 = 产品亮相(8-12s) + 卖点1演示(12-16s)
- 段 3 (16-24s): 镜 5-6 = 卖点2演示(16-20s) + 卖点3演示(20-24s)
- 段 4 (24-32s): 镜 7-8 = 使用场景(24-28s) + 生活化(28-32s)
- 段 5 (32-40s): 镜 9-10 = CTA转化(32-36s) + 收尾展示(36-40s)

**每镜 time_range 用 40s 全局时间**:
- 镜 1: [0.0, 4.0]   镜 2: [4.0, 8.0]
- 镜 3: [8.0, 12.0]  镜 4: [12.0, 16.0]
- 镜 5: [16.0, 20.0] 镜 6: [20.0, 24.0]
- 镜 7: [24.0, 28.0] 镜 8: [28.0, 32.0]
- 镜 9: [32.0, 36.0] 镜 10: [36.0, 40.0]

**字段名严格规范**:
- 推荐字段: {beat_id, time_range:[s,e], keyframe, description}
- 或: {shot_id, time:"0-4s", scene, visual, audio}
- 必须加 segment 字段 (1-5 标识属于哪一段)
- 每镜 visual/description 必须包含: 主体外观 + 产品形态(形态+颜色) + 手部动作 + negation

**8 维度 + 否定锚点 + 物理形态优先** (来自 skill: h3-prompt-8-dimensions):

| 维度 | 必填 | 示例 |
|---|---|---|
| subject | 人物外观 | 椭圆脸/单眼皮/小尖鼻/长直发 |
| product_form | **物理形态先于颜色** | 飞织网面鞋面+EVA缓震中底 / 白色塑料软管+翻盖 |
| hand_action | 手部细到指节 | 拇指+食指捏住 / 掌心托住 / 虎口卡住 |
| purpose | 动作目的 | 挤压膏体 / 展示鞋外观 |
| quantity | 数量限定 | only ONE product visible |
| negation | 否定锚点 | NOT lipstick / NOT red color |
| camera | 镜头景别 | close-up / macro |
| time_text | 时间范围 | Between 0.000s and 4.000s |

**每段内容硬约束 (5 段必须全覆盖)**:
- 段 1 钩子: 文案 tagline 直接用作开场钩子 (前 3 秒抓眼球)
- 段 1 痛点: 文案暗示的痛点可视化 (旧产品问题 / 不便场景)
- 段 2 产品亮相: 产品首次完整入画 + 物理形态展示
- 段 2-3 卖点演示 (SP1+SP2+SP3): 每个卖点必须 1 镜直接体现, 不能丢
- 段 4 使用场景: 真实使用环境 (通勤/居家/运动), 让用户代入
- 段 5 CTA: CTA 话术 + 小黄车手势 + 收尾展示 (产品全景或人物感谢)

**4 类硬约束** (来自 skill: storyboard-script-quality):
1. **时长严格**: 10 镜累加 = 40s 整, 每镜 4s (不可偏移)
2. **单镜动作唯一**: 每镜只允许 1 个主动作, 最多 1 个次动作
3. **禁止时序跳变词**: 不用"随后/接着/然后/连续切换/三晚/三天后"
4. **段内镜 1-2 衔接**: 段 1 内镜 1 (0-4s) 和镜 2 (4-8s) 必须动作连续 (不是硬切)
5. **跨段衔接**: 段 N 尾帧 (t=N*8s) 动作要与段 N+1 首帧 (t=N*8s) 自然过渡
6. **产品前 3 秒入画**: 镜 1 画面必须让产品实体出现

**卖点→分镜 强对应**:
- SP1 → 镜 4 (段 2 卖点 1)
- SP2 → 镜 5 (段 3 卖点 2)
- SP3 → 镜 6 (段 3 卖点 3)
- 每个 SP 在自己镜里被"具体画面"演示 (不是只说卖点词)"""
    user_p = f"""产品:{name}
类别:{cat}
**物理形态 (必填, 详细到材质+结构):** {shape or pf}
颜色:{color}
**tagline (钩子, 段 1 镜 1 必须直接用):** {tagline}
**selling_points (3 个, SP1→镜4, SP2→镜5, SP3→镜6):**
  - SP1: {sp[0] if len(sp) > 0 else ''}
  - SP2: {sp[1] if len(sp) > 1 else ''}
  - SP3: {sp[2] if len(sp) > 2 else ''}
scene: {copy.get('scene', '') if isinstance(copy, dict) else ''}

请输出严格 10 镜 JSON 数组, 每镜必须含 segment 字段 (1-5):
- 段 1 (segment:1): 镜 1-2, 钩子 + 痛点
- 段 2 (segment:2): 镜 3-4, 产品亮相 + SP1 演示
- 段 3 (segment:3): 镜 5-6, SP2 + SP3 演示
- 段 4 (segment:4): 镜 7-8, 使用场景/生活化
- 段 5 (segment:5): 镜 9-10, CTA + 收尾

每镜 description 必须包含:
1. 具体产品形态描述 (形态+颜色)
2. 手部动作 (拇指/食指/掌心/虎口等)
3. negation 词 (NOT 口红/NOT 香水 等)
4. 对应哪个 SP 或 tagline 元素
5. time_range 用 40s 全局时间 (例: 镜 5 用 [16.0, 20.0])"""
    last_err = None
    for m in [PRIMARY_MODEL, SECONDARY_MODEL]:
        # gem-3.7-flash 用 4000 tokens (10 镜 8 维度需要更长)
        max_tok = 4000 if m == PRIMARY_MODEL else 4000
        timeout = 120 if m == SECONDARY_MODEL else 120  # gem 长 prompt 慢
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
    """规则版 10 镜 (5 段 × 2 镜, 每镜 4s, 总 40s)"""
    cat = info.get("category", "其他")
    name = info.get("name", "产品")
    # 5 段 × 2 镜 = 10 镜
    segment_actions = {
        "洁面": [
            ("双手手背轻触脸颊,展示T区油光", "右手握洁面乳管身,左手推开翻盖"),
            ("右手拇指食指挤压管身,在左手掌挤出2cm乳白膏体", "双手掌心沾温水打圈揉搓,5秒拉丝出绵密泡沫"),
            ("泡沫覆于面部,指腹打圈清洁鼻翼", "清水冲净,双手食指弹脸颊特写,展示净澈素颜"),
            ("日常通勤使用场景,清爽不紧绷", "敏感肌安心使用画面,朋友推荐"),
            ("镜头拉远,小黄车CTA手势指向画面左下角", "双手合十微笑说谢谢观看"),
        ],
        "口红": [
            ("手持金色口红管,展示产品外观", "膏体贴下唇特写"),
            ("右手拇指食指握管涂抹上唇", "抿唇晕染推开"),
            ("微笑展示完整唇色", "露齿微笑"),
            ("约会场景,精致妆容", "通勤场景,自然提气色"),
            ("小黄车CTA指向手势", "产品全景展示+感谢观看"),
        ],
        "鞋": [  # 男士运动鞋
            ("右手握飞织网面鞋展示", "俯拍鞋面飞织纹理特写"),
            ("左脚穿鞋站立,展示透气网面", "原地踏步展示轻量化"),
            ("EVA中底踩踏回弹特写", "橡塑大底防滑纹路特写"),
            ("通勤场景,穿鞋行走", "健身房场景,穿鞋运动"),
            ("三色展示飞织深灰蓝/纯黑/白灰", "小黄车CTA手势+感谢观看"),
        ],
        "运动鞋": [
            ("右手握飞织网面鞋展示", "俯拍鞋面飞织纹理特写"),
            ("左脚穿鞋站立,展示透气网面", "原地踏步展示轻量化"),
            ("EVA中底踩踏回弹特写", "橡塑大底防滑纹路特写"),
            ("通勤场景,穿鞋行走", "健身房场景,穿鞋运动"),
            ("三色展示飞织深灰蓝/纯黑/白灰", "小黄车CTA手势+感谢观看"),
        ],
        "空气炸锅": [
            ("展示空气炸锅外观", "打开炸锅盖"),
            ("放入食材", "旋转定时旋钮"),
            ("运行展示", "食材变焦过程"),
            ("放入整鸡", "烤制完成出锅"),
            ("成品展示+小黄车CTA", "感谢观看+产品全景"),
        ],
        "耳机": [
            ("展示充电盒", "打开盒盖"),
            ("取出耳机", "戴到耳朵"),
            ("触摸操作", "听音乐享受"),
            ("通勤降噪场景", "办公场景佩戴"),
            ("产品全景+小黄车CTA", "感谢观看"),
        ],
        "牙膏": [
            ("展示牙膏管", "挤压膏体到牙刷"),
            ("刷牙动作", "漱口"),
            ("展示亮白牙齿", "自信微笑"),
            ("早晚使用场景", "家庭装推荐"),
            ("小黄车CTA+感谢观看", "产品全景"),
        ],
        "积木": [
            ("展示积木包装", "倒出积木块"),
            ("拼接两块积木", "堆叠成型"),
            ("展示完成造型", "旋转展示"),
            ("孩子玩耍场景", "亲子互动"),
            ("小黄车CTA+感谢观看", "产品全景"),
        ],
        "零食": [
            ("展示零食包装", "撕开包装"),
            ("展示产品内容物", "拿起一片"),
            ("放入口中品尝", "咀嚼表情"),
            ("聚会分享场景", "追剧场景"),
            ("小黄车CTA+感谢观看", "产品全景"),
        ],
        "水果": [
            ("展示完整水果", "切开水果"),
            ("展示果肉特写", "品尝一块"),
            ("甜美表情", "新鲜对比"),
            ("家庭分享场景", "送礼场景"),
            ("小黄车CTA+感谢观看", "产品全景"),
        ],
    }
    # 匹配品类
    cat_actions = {
        "洁面": segment_actions["洁面"],
        "口红": segment_actions["口红"],
        "鞋": segment_actions["鞋"],
        "运动鞋": segment_actions["运动鞋"],
        "空气炸锅": segment_actions["空气炸锅"],
        "耳机": segment_actions["耳机"],
        "牙膏": segment_actions["牙膏"],
        "积木": segment_actions["积木"],
        "零食": segment_actions["零食"],
        "水果": segment_actions["水果"],
    }
    actions = None
    for k, v in cat_actions.items():
        if k in cat or k in name:
            actions = v
            break
    if not actions:
        actions = [
            ("手持产品展示外观", "产品细节特写"),
            ("产品首次入画", "核心动作演示"),
            ("主要卖点展示", "次要卖点展示"),
            ("使用场景1", "使用场景2"),
            ("小黄车CTA手势", "感谢观看+产品全景"),
        ]
    # 构建 10 镜分 5 段
    beats = []
    for seg_idx in range(5):  # 5 段
        seg_actions = actions[seg_idx]
        for mir_idx in range(2):  # 每段 2 镜
            mir_id = seg_idx * 2 + mir_idx + 1
            t0 = seg_idx * 8 + mir_idx * 4
            t1 = t0 + 4
            beats.append({
                "beat_id": mir_id,
                "segment": seg_idx + 1,
                "time_range": [float(t0), float(t1)],
                "keyframe": f"sec{mir_id-1}",
                "description": f"镜{mir_id} (段{seg_idx+1}): {seg_actions[mir_idx]}",
                "_model": "fallback",
            })
    return beats


# ============ 任务 4: 宫格图 prompt 构建 (v6) ============


# ============ 任务 3b: 5 段 × 1 镜 v3 (一段一卖点) ============

def build_storyboard_v3(product_info: dict, copy: dict) -> list:
    """5 段 × 1 镜 (每段 8s, 每段只表达 1 个核心主题)

    段 1: 钩子+痛点 (8s)
    段 2: SP1 (8s)
    段 3: SP2 (8s)
    段 4: SP3 (8s)
    段 5: 三色/多色 + CTA (8s)

    段内 6 cell 由 build_segment_grid_prompt 程序展开
    """
    cat = product_info.get("category", "")
    name = product_info.get("name", "")
    pf = product_info.get("product_form", "")
    color = product_info.get("color", "")
    tagline = copy.get("tagline", "") if isinstance(copy, dict) else ""
    sp = (copy.get("selling_points", ["卖点"]) if isinstance(copy, dict) else ["卖点"])[:3]

    sys_p = """你是带货视频分镜师。只输出严格 JSON 数组, 正好 5 个对象, 不要 markdown 包装。

**架构**: 5 段 × 1 镜 (每镜 8s, 总 40s), 段内 6 cell 由程序自动展开
- 段 1 (0-8s): 钩子+痛点 (1 个分镜对象)
- 段 2 (8-16s): SP1 演示 (1 个分镜对象, 6 cell 围绕 SP1 展开)
- 段 3 (16-24s): SP2 演示 (1 个分镜对象, 6 cell 围绕 SP2 展开)
- 段 4 (24-32s): SP3 演示 (1 个分镜对象, 6 cell 围绕 SP3 展开)
- 段 5 (32-40s): 三色 + CTA (1 个分镜对象, 6 cell 围绕色彩+行动展开)

**字段**:
- segment: 1-5
- time_range: [起始s, 结束s] (8 秒一段)
- description: 一句话描述本段 8s 内 6 cell 围绕的共同主题
- keyframe: 第 1 cell 的具体画面 (开头 0-1.3s)
- lastframe: 第 6 cell 的具体画面 (结尾 6.7-8.0s)

每段 description 必须是 1 个完整连贯场景描述 (不是 6 个动作列表)。

**8 维度**:
- subject: 椭圆脸/单眼皮/小尖鼻
- product_form: 物理形态先于颜色
- hand_action: 拇指+食指捏住 / 掌心托住
- purpose: 动作目的
- quantity: only ONE product visible
- negation: NOT 口红 / NOT 香水
- camera: close-up / macro
- time_text: Between 0.000s and 8.000s"""

    user_p = f"""产品:{name} ({cat})
物理形态:{pf}
颜色:{color}
钩子(段1): {tagline}
SP1 (段2): {sp[0] if len(sp) > 0 else ''}
SP2 (段3): {sp[1] if len(sp) > 1 else ''}
SP3 (段4): {sp[2] if len(sp) > 2 else ''}

请输出正好 5 个 JSON 对象的数组, time_range 严格用:
段1: [0.0, 8.0]
段2: [8.0, 16.0]
段3: [16.0, 24.0]
段4: [24.0, 32.0]
段5: [32.0, 40.0]"""

    last_err = None
    for m in [PRIMARY_MODEL, SECONDARY_MODEL]:
        try:
            raw = call_lk888(
                m,
                [{"role": "system", "content": sys_p},
                 {"role": "user", "content": user_p}],
                temperature=0.6, max_tokens=3000, timeout=120,
            )
            beats = parse_json_strict(raw)
            if isinstance(beats, list):
                if len(beats) != 5:
                    last_err = f"{m}: returned {len(beats)} beats, expected 5"
                    continue
                for i, b in enumerate(beats, 1):
                    if "segment" not in b:
                        b["segment"] = i
                    b["beat_id"] = i
                    if "time_range" not in b:
                        b["time_range"] = [float((i-1)*8), float(i*8)]
                    if not isinstance(b.get("description"), str) or len(b["description"]) < 5:
                        b["description"] = f"段{i}: (内容缺失)"
                    b["_model"] = m
                return beats
            last_err = f"{m}: parse_json returned non-list"
        except Exception as e:
            last_err = f"{m}: {e}"
            continue
    return _fallback_storyboard_v3(product_info)


def _fallback_storyboard_v3(info: dict) -> list:
    """规则版 5 段 × 1 镜 (每段 8s, 一段一卖点)"""
    cat = info.get("category", "其他")
    name = info.get("name", "产品")
    sp = info.get("selling_points", ["卖点1", "卖点2", "卖点3"])
    if isinstance(sp, str):
        sp = [s.strip() for s in sp.split("|") if s.strip()][:3]
    if not sp or len(sp) < 3:
        sp = ["卖点1", "卖点2", "卖点3"]

    # 通用 5 段模板 (不依赖品类)
    seg1_desc = f"用户场景痛点展示, 对比传统不便与新产品优势, 引出 {name} 解决方案。"
    seg1_kf = f"人物疲惫使用旧方式 (没有 {name}), 镜头捕捉困扰表情。"
    seg1_lf = f"人物眼睛发亮, 拿起 {name} 准备展示。"

    seg2_desc = f"演示 {sp[0]} (段 2 单一卖点, 6 cell 围绕此卖点展开)。"
    seg2_kf = f"{name} 静置就位, 准备演示 {sp[0]}。"
    seg2_lf = f"{sp[0]} 演示效果清晰可见, {name} 在镜头前展示卖点。"

    seg3_desc = f"演示 {sp[1]} (段 3 单一卖点, 6 cell 围绕此卖点展开)。"
    seg3_kf = f"{name} 重新就位, 准备演示 {sp[1]}。"
    seg3_lf = f"{sp[1]} 演示效果清晰可见, {name} 在镜头前展示卖点。"

    seg4_desc = f"演示 {sp[2]} (段 4 单一卖点, 6 cell 围绕此卖点展开)。"
    seg4_kf = f"{name} 重新就位, 准备演示 {sp[2]}。"
    seg4_lf = f"{sp[2]} 演示效果清晰可见, {name} 在镜头前展示卖点。"

    seg5_desc = f"展示 {name} 多色/多款 + CTA 行动呼吁。"
    seg5_kf = f"{name} 多色款静置陈列展示。"
    seg5_lf = f"主播手举 {name} 主推款, 热情指向镜头发出购买号召。"

    beats = []
    for i, (desc, kf, lf) in enumerate([
        (seg1_desc, seg1_kf, seg1_lf),
        (seg2_desc, seg2_kf, seg2_lf),
        (seg3_desc, seg3_kf, seg3_lf),
        (seg4_desc, seg4_kf, seg4_lf),
        (seg5_desc, seg5_kf, seg5_lf),
    ], start=1):
        beats.append({
            "beat_id": i,
            "segment": i,
            "time_range": [float((i-1)*8), float(i*8)],
            "description": desc,
            "keyframe": kf,
            "lastframe": lf,
            "_model": "fallback",
        })
    return beats

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