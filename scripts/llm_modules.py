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
    """多层容错: markdown包装+括号匹配+截断+单对象包数组

    2026-09-23 修复: 当 LLM 返回单个 dict (像 beat), 自动包成 list[dict]
    (因为模型常误解 prompt, 把 5 段当成 1 个对象输出)
    """
    raw = raw.strip()
    raw = re.sub(r"^```json\s*", "", raw)
    raw = re.sub(r"```\s*$", "", raw)
    raw = raw.strip()

    # 2026-09-23 新增: 包数组容错 (单 dict → list[dict])
    beat_like_keys = {"segment", "description", "keyframe", "lastframe", "cell_actions", "time_range", "beat_id"}

    def _maybe_wrap_list(obj):
        """如果 obj 是 dict 且 keys 像 beat, 包成 [obj]; 否则返回原 obj"""
        if isinstance(obj, dict) and any(k in obj for k in beat_like_keys):
            return [obj]
        return obj

    try:
        obj = json.loads(raw)
        return _maybe_wrap_list(obj)
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
                        obj = json.loads(raw[idx:i+1])
                        return _maybe_wrap_list(obj)
                    except Exception:
                        break
        if last_close > idx:
            try:
                obj = json.loads(raw[idx:last_close+1])
                return _maybe_wrap_list(obj)
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


def detect_model_gender(model_image_path: str) -> str:
    """检测模特图性别 (2026-09-23 新增, 修复"男模特+女声")

    输入: 模特图路径
    输出: "female" | "male" | "neutral" (无法判断时)

    主模型 gem-3.7-flash, 备用 tt-5.6-luna
    """
    if not model_image_path or not Path(model_image_path).exists():
        return "neutral"
    data_uri = img_b64_uri(model_image_path)
    prompt = """你是模特图性别识别专家。只输出 JSON, 不要 markdown 包装.

|严格只输出 1 个字段:
{"gender": "female" 或 "male", "confidence": 0.0~1.0, "age_range": "20-25/25-30/30-40/40+"}

|判定标准 (按视觉特征, 不看服装):
1. **脸型轮廓**: female 通常鹅蛋脸/圆脸/柔和下颌; male 通常方脸/下颌角明显/颧骨突出
2. **五官**: female 通常眉峰柔和/眼大; male 通常眉骨突出/眼型偏窄
3. **皮肤质感**: female 通常更细腻光滑; male 通常毛孔/纹理更明显
4. **颈部**: female 通常颈线细长; male 通常喉结明显

|如果画面模糊/侧脸/戴口罩无法判断, gender 返回 "neutral"."""

    models = [PRIMARY_MODEL, SECONDARY_MODEL]
    last_err = None
    for m in models:
        try:
            raw = call_lk888(
                m,
                [{"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": data_uri}},
                    {"type": "text", "text": prompt}
                ]}],
                temperature=0.2, max_tokens=200, timeout=60,
            )
            result = parse_json_strict(raw)
            if isinstance(result, dict):
                g = str(result.get("gender", "")).lower().strip()
                if g in ("female", "male"):
                    return g
                if g == "neutral":
                    return "neutral"
            last_err = f"{m}: parse failed"
        except Exception as e:
            last_err = f"{m}: {e}"
            continue
    print(f"  [detect_gender] 失败: {last_err}, fallback 'neutral'")
    return "neutral"


# ============ 任务 1: 产品识别 (VLM) ============
def recognize_product(image_path: str, product_text: str = "",
                       primary_first: bool = True,
                       detect_model: bool = True,
                       model_image_path: str = None) -> dict:
    """主调用 gem-3.7-flash, 失败 fallback tt-5.6-luna

    输入: image_path (产品图), product_text (用户文字信息)
    输出: {category, name, color, shape, key_attributes, skin_tone_match, usage_scene, product_form, model_gender}

    2026-09-23 修复: 同时检测模特性别 (用 model_image_path), 写到 model_gender 字段,
    供 build_segment_prompt 注入"young woman/man"避免"男模特+女声"问题.
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
格式: {"tagline":"1-2句钩子","selling_points":["s1","s2","s3"],"scene":"使用场景","cta":"点击下方小黄车直接下单","voiceover_segments":["段1台词","段2台词","段3台词","段4台词","段5台词"]}

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
5. 形态贯穿: 卖点里描述使用动作时, 必须符合产品形态 (例: 软管 = 挤压; 瓶 = 按压)

**voiceover_segments (新增, 借鉴 yao 5 层口播结构 + ClipForge [pause] 标记)**:

为 5 段视频**分别**生成**带分层停顿**的台词, 每段独立一句, 总字数严格控制:

| 段号 | 视频时段 | 字数 | 结构 | 示例 |
|------|----------|------|------|------|
| 段 1 (钩子) | 0-8s | 8-13 字 | 紧迫提问 + 痛点 | "还在搬整台破壁机?出门带不了!" |
| 段 2 (SP1) | 8-16s | 10-15 字 | 产品形态 + 第一个卖点 | "细高圆柱杯旋盖即榨,30秒出汁!" |
| 段 3 (SP2) | 16-24s | 10-15 字 | 第二个卖点 | "USB-C充电15杯,续航整天!" |
| 段 4 (SP3) | 24-32s | 10-15 字 | 第三个卖点 | "灰色挂绳一扣,300ml免拆塞包就走!" |
| 段 5 (CTA) | 32-40s | 5-8 字 | 强推 + CTA | "鲜享榨汁杯,点击下方小黄车!" |

**绝对禁止**:
- ❌ 同一段超过 15 字 (8 秒视频念不完, 用户听不清)
- ❌ 5 段台词相似 (避免重复念同一句)
- ❌ 数字杜撰 / 性能夸大 (只写产品信息里有的卖点)
- ❌ 把产品误描述为其他品类

**口语化 6 条硬约束** (借鉴 huashu-douyin-script):
1. 用第一人称 / 第二人称 ("你/姐妹们") 像跟朋友说话, 不用"消费者/用户"
2. 动词具体 (旋/拧/扣/按/撕, 而不是"使用/操作")
3. 加语气词 ("呀/啊/呢/啦" 等) 让语调有起伏
4. 数字用阿拉伯 (39块, 不是三十九元)
5. 用"!" 加强重音, 关键卖点加【】标记
6. 短句为主, 不写超过 8 字的连续长句

**📌 关键: 真人口语化风格 (2026-09-23 新增, 用户反馈"主播腔词藻堆砌, 不像真人说话")**

❌ 主播腔模板 (听起来假):
- "哎呀家人们!今天给大家安利一支神仙洁面!" ← 模板词堆砌
- "闭眼入!冲就完了!29块9!" ← 形容词堆
- "你好我好大家好!" ← 空洞招呼

✅ 真人说话 (听起来像人):
- "姐妹们,你们有没有这种感觉?早上洗完脸吧,过一会儿就紧绷得难受"
- "你看这个白色小管子,挤出来的泡沫跟奶油一样绵"
- "我现在自己也在用,用了二十多天,脸摸起来软软的"
- "我妈以前用皂基洗面奶烂过脸,后来换成这个才好"

**真人感 6 大规则**:
1. **场景代入**: "你们有没有遇到过这种问题..." 优于 "今天给大家推荐..."
2. **亲身体验**: "我自己也在用" / "我妈以前..." / "闺蜜说..." 优于 "根据产品介绍..."
3. **口语词**: 用的/这/那/啥/呗/吧/喽/诶, 不用 "此时此刻/综上所述/因此"
4. **重复强调**: "而且而且" / "真的真的" / "重点重点", 重复是真人特点
5. **具体细节**: "挤出来的泡沫跟奶油一样" 优于 "泡沫非常绵密"
6. **自然停顿**: 用"、"。"分割短句, 不是一气呵成

**禁词表** (生成的文案中, 这些词出现 1 次以上就必须改):
- "闭眼入/冲就完了/绝绝子/炸裂/家人们/宝宝们/老铁们/安利/种草"
  (这些是模板词,真人很少用,主播才说)
- "此时此刻/由此可见/综上所述/值得注意的是" (书面语, 真人不说)
- "震撼/惊艳/引爆/YYDS/绝绝子" (网络流行语堆砌)
- "非常/特别/极其" (程度副词, 真人用具体词代替)

**真人感文案示例** (few-shot, 让 LLM 学风格):

【示例 1: 洁面乳 (替换前 → 替换后)】
❌ 替换前: "哎呀家人们!今天给大家安利一支神仙洁面!洗完脸紧绷拔干?别再硬扛!闭眼入!"
✅ 替换后: "姐妹们,你们有没有这种感觉?早上洗完脸吧,过一会儿就紧绷得难受。我之前一直用皂基洗面奶,后来脸都洗得发红了,才换的氨基酸的。你看这个白色软管,挤出来的泡沫跟奶油一样绵,洗完脸摸起来不紧绷。我现在自己也在用,用了二十多天,脸上摸着软软的,一点儿也不假滑。"

【示例 2: 榨汁机 (替换前 → 替换后)】
❌ 替换前: "哎呀家人们!还在搬整台破壁机?细高圆柱杯旋盖即榨!USB-C充电15杯!冲就完了!"
✅ 替换后: "姐妹们,我以前在家用那种大破壁机,占地方不说,声音还特别大,早上用一次全家都被吵醒。后来我换了这种小圆柱杯,你看就这么一拧,水果扔进去,30秒果汁就打出来了,而且USB-C充电,我试了一下充一次能榨15杯,够用一整天。"

**怎么用 few-shot**:
在 user_p 里 **把示例全文贴进去**, 让 LLM 学真人说话的句式、停顿、用词. 然后让它基于产品信息生成对应版本.
    """
    user_p = f"""产品:{product_info.get('name','?')}, 类别:{product_info.get('category','')}, 形态:{product_info.get('shape', product_info.get('product_form',''))}, 卖点:{sp_str}, 用户文字:{product_text[:400] if product_text else ''}\n\n请生成严格 5 段式 JSON, 文案中必须体现产品物理形态, voiceover_segments 必须 5 个字符串"""
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
    """10 镜分镜 (5 段 * 2 镜, 每段 4s, 总时长 20s)

    输入: product_info, copy
    输出: [{beat_id, time_range:[s,e], keyframe|scene, description|visual, segment:N}, ...] 乘 10

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

**核心架构: 40s 完整视频 = 5 段 x 8s = 10 镜 (每段 2 镜, 每镜 4s)**
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
    """规则版 10 镜 (5 段 * 2 镜, 每镜 4s, 总 40s)"""
    cat = info.get("category", "其他")
    name = info.get("name", "产品")
    # 5 段 * 2 镜 = 10 镜
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


# ============ 任务 3b: 5 段 x 1 镜 v3 (一段一卖点) ============

def build_storyboard_v3(product_info: dict, copy: dict) -> list:
    """5 段 x 1 镜 (每段 8s, 每段只表达 1 个核心主题)

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

    # 2026-09-23 修复: 强制注入模特图性别 (避免 LLM 写错"Female presenter" 实际是男模)
    model_gender = product_info.get("model_gender", "neutral")  # female / male / neutral
    if model_gender == "male":
        gender_desc_en = "An Asian male (25-35 yo, short black hair, clean-shaven or light stubble, casual shirt)"
        gender_desc_cn = "男模特 (亚洲男性, 25-35岁, 短发, 干净或轻微胡渣, 休闲衬衫)"
    elif model_gender == "female":
        gender_desc_en = "An Asian female (25-35 yo, shoulder-length hair, natural makeup, casual top)"
        gender_desc_cn = "女模特 (亚洲女性, 25-35岁, 肩长发, 淡妆, 休闲上衣)"
    else:
        gender_desc_en = "An Asian person (25-35 yo, gender-neutral styling)"
        gender_desc_cn = "亚洲模特 (25-35岁, 性别中性风格)"

    sys_p = f"""你是带货视频分镜师。只输出严格 JSON 数组, 正好 5 个对象, 不要 markdown 包装。

    |**架构**: 5 段 x 1 镜 (每镜 8s, 总 40s), 段内 6 cell 由程序自动展开
    - 段 1 (0-8s): 钩子+痛点 (1 个分镜对象)
    - 段 2 (8-16s): SP1 演示 (1 个分镜对象, 6 cell 围绕 SP1 展开)
    - 段 3 (16-24s): SP2 演示 (1 个分镜对象, 6 cell 围绕 SP2 展开)
    - 段 4 (24-32s): SP3 演示 (1 个分镜对象, 6 cell 围绕 SP3 展开)
    - 段 5 (32-40s): 三色 + CTA (1 个分镜对象, 6 cell 围绕色彩+行动展开)

    |**模特硬约束** (CRITICAL, 2026-09-23 修复):
    - **必须使用 {gender_desc_en}** 作为视频中的唯一主角
    - **绝对禁止** 写 "Female presenter" / "Asian female" / "she" — 模特是 **{gender_desc_cn}**
    - **绝对禁止** 出现性别不匹配的代词 (她/他), 必须用 "the model" / "they"
    - 所有 cell_actions 中提到主角时, 用 "{gender_desc_en}" 这个完整描述
    - 如果 model_gender == "male", 所有代词必须用 "he/his/him"
    - 如果 model_gender == "female", 所有代词必须用 "she/her/her"

    |**字段** (每段必须包含, 不能缺):
    - segment: 1-5
    - time_range: [起始s, 结束s] (8 秒一段)
    - description: 一句话描述本段 8s 内 6 cell 围绕的共同主题
    - keyframe: 第 1 cell 的具体画面 (开头 0-1.3s)
    - lastframe: 第 6 cell 的具体画面 (结尾 6.7-8.0s)
    - **cell_actions** (新增, 6 元素数组, 关键修复 2026-09-23):
      严格 6 个元素, 每个元素是 **1 个英文短句 (30-60 词)**, 必须按以下公式生成:
      "Subject动作 + 接触的产品部位 + 视觉反馈结果"

      **每个 cell_actions 必须形成**完整动作链**, 6 个步骤不能跳过任何中间环节:
      - cell_actions[0] (SETUP, 0-1.3s): {gender_desc_en} + 产品 + 场景的开场姿势, 产品必须已入画
      - cell_actions[1] (TRIGGER, 1.3-2.7s): 第 1 个可见动作, 手指/手开始接触产品的具体部位 (例: thumb+index fingers pinch lid edge)
      - cell_actions[2] (BUILDUP, 2.7-4.0s): 动作深入, 产品开始显示**视觉变化** (例: juice swirling, foam rising, fruit dropping in)
      - cell_actions[3] (CLIMAX, 4.0-5.3s): 卖点最强烈呈现, 产品**最终状态**完全展现 (例: full juice, sealed cap, locked clip)
      - cell_actions[4] (DECAY, 5.3-6.7s): 模特手从产品移开, **展示完成效果** (例: hands release, results in plain view)
      - cell_actions[5] (RESULT, 6.7-8.0s): 收尾确认姿势, 与 cell_actions[0] **镜像构图**

      **绝对禁止**:
      - ❌ 跳过 cell 2-3 直接到 cell 4 (模型经常跳过中间步骤)
      - ❌ 产品在 cell 2 突然工作 (cell 1 还没操作)
      - ❌ 凭空出现物品 (水果必须从手里/碗里出来)
      - ❌ 抽象描述如 "the action happens" (必须具体到手指 + 接触点)
      - ❌ 性别写错 (必须与 model_gender 一致)

      **典型好例子 (男模, 电饭锅)**:
      1. "{gender_desc_en} stands behind a kitchen counter holding one box-shaped white rice cooker in front view"
      2. "His right index finger presses firmly onto the chrome top-mounted lid release latch"
      3. "The damped lid unlocks with an audible click and smoothly glides upward, releasing a gentle billow of steam"
      4. "The top lid reaches full vertical extension, revealing golden jasmine rice filled to the brim"
      5. "He pulls both hands back gracefully to chest level, directing focus to the fresh steaming rice texture"
      6. "He smiles warmly into the camera lens beside the open steaming cooker, mirroring the opening pose"

    每段 description 必须是 1 个完整连贯场景描述 (不是 6 个动作列表)。

    |**8 维度**:
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
                temperature=0.6, max_tokens=8000, timeout=180,  # 2026-09-23 修复: 6000→8000 避免截断
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
                    # 2026-09-23 修复: 容错 cell_actions (LLM 偶发不输出时回退到 description 拆 6 段)
                    if not isinstance(b.get("cell_actions"), list) or len(b["cell_actions"]) != 6:
                        # 用 description/keyframe/lastframe 拼 6 句 fallback
                        desc = b.get("description", "")
                        kf = b.get("keyframe", "")
                        lf = b.get("lastframe", "")
                        b["cell_actions"] = [
                            kf or f"Cell 1 SETUP: {desc}",
                            f"Cell 2 TRIGGER: beginning the demonstration of {desc[:80]}",
                            f"Cell 3 BUILDUP: action deepens, product starts showing function ({desc[:80]})",
                            f"Cell 4 CLIMAX: selling point at peak demonstration ({desc[:80]})",
                            f"Cell 5 DECAY: action tapers, benefit visible ({desc[:80]})",
                            lf or f"Cell 6 RESULT: closing pose mirroring opening",
                        ]
                    b["_model"] = m
                return beats
            last_err = f"{m}: parse_json returned non-list"
        except Exception as e:
            last_err = f"{m}: {e}"
            continue
    return _fallback_storyboard_v3(product_info)


# ============ 任务 2.5: 独立口播稿生成 (借鉴 Streamer-Sales + yao + H3 官方) ============
def build_voiceover_segments(product_info: dict, copy: dict, persona: str = "乐乐喵_萝莉") -> dict:
    """独立主播口播稿生成 — 跟脚本分离, 借鉴 Streamer-Sales + yao + H3 官方

    Args:
        product_info: 产品识别结果 (含 name/shape/color/category)
        copy: 脚本 (含 tagline/selling_points/cta)
        persona: 主播人设 (乐乐喵_萝莉/丹丹琳/...)

    Returns:
        {"voiceover_segments": [{cell_id, timing_start, timing_end, text, emotion, pacing, volume, audio_directive, visual_sync_note}, ...]}

    关键修复 (2026-09-23):
      - 跟脚本分离 (重叠 < 30%) — 让 LLM 独立主播腔
      - 6 cell 节拍对齐 (0-1.3s / 1.3-2.7s / ... / 6.7-8.0s)
      - 主播腔词典 (Streamer-Sales role_type)
      - 文本标记约定 (【】/(停顿)/(上扬))
      - H3 官方 voiceover 格式 (says in an off-screen voiceover + lips remain completely closed)
    """
    info_s = "\n".join([f"{k}:{v}" for k, v in product_info.items()
                        if v and not str(k).startswith("_") and not isinstance(v, list)])

    sys_p = f"""# Role: 抖音金牌带货主播「{persona}」

## Profile
- 你是抖音平台 5 年金牌带货主播, 单场 GMV 千万级
- 称呼客户为「家人们」「宝宝们」「姐妹们」「老铁们」
- 擅长在 30 秒内让从没听过你名字的人下单

## Background
带货口播稿**跟书面脚本有 3 个根本区别**:
1. **不重复脚本**: 脚本是给画面看的 (含卖点/参数/形态), 口播是给人听的 (含情绪/痛点/承诺). 内容重叠**不超过 30%**.
2. **用短句**: 每句不超过 15 字 (人说话 15 字以上会换气/听感断裂)
3. **有节奏**: 不是平铺直叙, 每 5-8 秒一个小高潮 (钩子/转折/承诺)

## 主播腔风格 (乐乐喵_萝莉人设)
1. **称呼开场**: 「家人们/宝宝们/姐妹们/老铁们」
2. **数字用阿拉伯**: 「今天 39 块」不是「今天三十九元」
3. **大量语气词**: 哎呀/哇塞/真的/绝了/上头/闭眼入/冲就完了/拍就完了
4. **具体数字锚点**: 「1 瓶 39, 拍 2 送 1」不是「很便宜」
6. **痛点放大**: 「姐妹们, 你们是不是也这样, 早上起来脸油得能煎蛋?」
7. **身份代入**: 「如果你是混油皮, 一定要试」
8. **结局承诺**: 「坚持用 28 天, 你会回来感谢我」
9. **紧迫感**: 「库存只剩 200 件, 拍完就没了」
10. **对比锚定**: 「专柜一瓶 199, 今天 39, 还送一堆小样」
11. **感官调动**: 视觉/触觉/味觉/嗅觉

## 6 Cell 节拍模板 (必须严格对应)
| cell | timing | 字数 | 情绪 | 任务 | 画面 |
| ---- | ------ | ---- | ---- | --- | --- |
| 1 | 0-1.3s | 8-12 | excited | 钩子/招呼 | 主播入场 |
| 2 | 1.3-2.7s | 8-12 | persuasive | 主推卖点 | 产品特写 |
| 3 | 2.7-4.0s | 8-12 | pain_point | 痛点放大 | 痛点场景 |
| 4 | 4.0-5.3s | 8-12 | demonstrate | 演示/功能 | 动作演示 |
| 5 | 5.3-6.7s | 8-12 | trust | 信任/试用 | 反馈/对比 |
| 6 | 6.7-8.0s | 8-12 | urgent | CTA/价格 | 价格+购物车 |

## 文本标记约定
- 【】= 必须加重语气, 例: 「今天【只要 39】」
- 「」= 商品名/关键术语
- (停顿) = 0.5s 静默
- (上扬) / (低声) / (快速) / (缓慢) = 情绪指令

## 跟脚本分离 (关键!)
- ❌ 脚本写"白色翻盖塑料软管挤出奶油泡" → 口播不能这样
- ✅ 口播应说"你看这个白色小管子, 挤出来的泡沫绵密得跟奶油一样!"
- ❌ 脚本暴露技术细节 (氨基酸成分/500ml)
- ✅ 口播要翻译: 氨基酸成分 → 「成分很温和」/ 500ml → 「大容量够用 3 个月」

## Output Format (严格 JSON)
{{
  "voiceover_segments": [
    {{
      "cell_id": 1,
      "timing_start": 0.0,
      "timing_end": 1.3,
      "text": "哎呀家人们, 看过来!",
      "emotion": "excited",
      "pacing": "fast",
      "volume": "loud",
      "audio_directive": "(兴奋招呼, 快速, 镜头正对主播)",
      "visual_sync_note": "主播对镜头招手"
    }},
    ... 共 6 个 cell
  ]
}}

## Initialization
你是「{persona}」. 根据【商品信息】+【书面脚本】, 输出 6 cell 主播腔独立配音稿.
- 跟脚本内容重叠**不超过 30%**
- 严格按 6 cell 时间节拍 (0-8s)
- 每 cell 8-15 字
- 严格按 JSON 输出
- 禁止解释, 禁止开场白, 直接输出 JSON
"""

    user_p = f"""# 商品信息
{info_s}

# 书面脚本 (供你参考, 不要照抄!)
tagline: {copy.get('tagline', '')}
selling_points: {copy.get('selling_points', [])}
scene: {copy.get('scene', '')}
cta: {copy.get('cta', '')}

# 你的任务
基于以上脚本, 生成一份 6 cell 主播腔独立配音稿.
跟脚本内容重叠不超过 30%, 重点是情绪/痛点/承诺, 不是参数/形态.
直接输出 JSON."""

    last_err = None
    for m in [PRIMARY_MODEL, SECONDARY_MODEL]:
        try:
            raw = call_lk888(
                m,
                [{"role": "system", "content": sys_p},
                 {"role": "user", "content": user_p}],
                temperature=0.85, max_tokens=4000, timeout=180,  # 2026-09-23 修复: 0.7→0.85 鼓励主播腔发挥
            )
            result = parse_json_strict(raw)
            if not isinstance(result, dict):
                last_err = f"{m}: returned non-dict"
                continue
            # 容错: 如果 LLM 输出 list (罕见), wrap 成 dict
            if isinstance(result, list):
                result = {"voiceover_segments": result}
            # 校验 schema
            segs = result.get("voiceover_segments", [])
            if not isinstance(segs, list) or len(segs) != 6:
                last_err = f"{m}: returned {len(segs) if isinstance(segs, list) else 'no'} segments, expected 6"
                continue
            # 容错: 补全缺字段
            for i, seg in enumerate(segs, 1):
                if not isinstance(seg, dict):
                    last_err = f"{m}: segment {i} not dict"
                    break
                if "text" not in seg or not seg.get("text"):
                    seg["text"] = f"哎呀家人们, 看过来! (cell {i})"
                if "cell_id" not in seg:
                    seg["cell_id"] = i
                if "timing_start" not in seg:
                    seg["timing_start"] = (i - 1) * 1.3
                if "timing_end" not in seg:
                    seg["timing_end"] = min(i * 1.3, 8.0)
            else:
                result["_model"] = m
                result["_persona"] = persona
                return result
            continue
        except Exception as e:
            last_err = f"{m}: {e}"
            continue

    print(f"  [build_voiceover_segments] 失败: {last_err}, fallback '脚本衍生'")
    # Fallback: 从脚本+selling_points 拼主播腔 (不独立, 但能用)
    return _fallback_voiceover_segments(product_info, copy, persona)


def build_voiceover_synced(product_info: dict, copy: dict, storyboard: list, persona: str = "乐乐喵_萝莉") -> dict:
    """同步版配音生成 — 配音内容**严格跟随画面 cell_actions** (2026-09-23 修复)

    关键问题: 之前 build_voiceover_segments 只参考脚本 (tagline/SP/CTA),
    生成的主播腔是抽象的"人说话", 不一定跟画面 cell 1-6 动作一致.
    用户反馈: "配音内容太简单, 和画面内容无法同步" (配音说"煮粥"时, 画面已经切到"开盖展示")

    修复: 把 5 段分镜的 cell_actions (画面 6 步动作链) 喂给 LLM,
    让 LLM **为每个 cell 生成匹配的配音**, 严格在 1.3s 时间点描述对应动作.

    输入:
        product_info: 产品识别结果
        copy: 书面脚本
        storyboard: 5 段分镜, 每段含 cell_actions (6 步) + description + keyframe
        persona: 主播人设

    输出:
        {
            "voiceover_segments_full": [
                {cell_id, segment, timing_start, timing_end, text, emotion, pacing, volume, audio_directive, visual_sync_note},
                ... 共 30 个 (5 段 × 6 cell)
            ]
        }
    """
    info_s = "\n".join([f"{k}:{v}" for k, v in product_info.items()
                        if v and not str(k).startswith("_") and not isinstance(v, list)])

    # 整理 5 段 × 6 cell = 30 个 cell_actions 输入 LLM
    cell_actions_all = []
    for seg in storyboard[:5]:
        seg_id = seg.get("segment", "?")
        desc = seg.get("description", "")
        cell_actions = seg.get("cell_actions", [])
        if not isinstance(cell_actions, list) or len(cell_actions) != 6:
            cell_actions = ["动作 1", "动作 2", "动作 3", "动作 4", "动作 5", "动作 6"]
        for i, ca in enumerate(cell_actions, 1):
            t_start = (i - 1) * 1.3
            t_end = min(i * 1.3, 8.0)
            cell_actions_all.append({
                "segment": seg_id,
                "cell_id": (seg_id - 1) * 6 + i,  # 1-30 全局
                "local_cell": i,  # 段内 1-6
                "timing_start": t_start,
                "timing_end": t_end,
                "segment_desc": desc[:80],
                "cell_action": str(ca)[:200],
            })

    # 写成紧凑文本
    cells_text = ""
    for c in cell_actions_all:
        cells_text += f"[段{c['segment']} cell{c['local_cell']} {c['timing_start']:.1f}-{c['timing_end']:.1f}s] {c['cell_action']}\n"

    sys_p = f"""# Role: 抖音金牌带货主播「{persona}」

## 关键任务: 配音内容严格跟随画面动作 (2026-09-23 用户要求)

你将看到 30 个 cell, 每个 cell 对应视频中 1.3 秒的画面动作.
**每个 cell 必须生成 1 句配音, 配音内容描述**该 cell 画面正在发生的事**.

### 强约束
1. **每 cell 1 句, 8-15 字** (1.3 秒念完)
2. **内容必须描述画面动作** (不是抽象概念, 不是参数/形态/价格)
3. **画外音风格** — 不需要跟人物口型对齐, 描述画面即可
4. **不要杜撰数字** — 数字必须是动作/视觉可见的 (例: 倒计时 18s, 24 小时)
5. **总配音: 5 段 × 6 cell = 30 cell**, 每段配音连续念完 6 cell 后, 段末留静音

### 错误示例 (❌)
- cell 1 显示"按预约键调 30 分钟", 配音说"哎呀家人们看过来" (跟画面无关)
- cell 5 显示"开盖展示米饭", 配音说"24 小时预约" (跟画面无关)

### 正确示例 (✅)
- cell 1 显示"按预约键调 30 分钟", 配音说"按预约键调到 30 分钟"
- cell 5 显示"开盖展示米饭", 配音说"你看这米粒粒分明"
- cell 6 显示"模特满意微笑", 配音说"口感跟现煮一样"

## Output Format (严格 JSON)
{{
  "voiceover_segments_full": [
    {{
      "cell_id": 1,
      "segment": 1,
      "local_cell": 1,
      "timing_start": 0.0,
      "timing_end": 1.3,
      "text": "按预约键调 30 分钟",
      "emotion": "demonstrate",
      "pacing": "medium",
      "volume": "medium",
      "audio_directive": "(操作演示, 平静, 跟随画面动作)",
      "visual_sync_note": "模特按触屏预约键"
    }},
    ... 共 30 个
  ]
}}

## 初始化
直接输出 JSON. 严格按 30 个 cell 输出. 禁止解释, 禁止开场白."""

    user_p = f"""# 商品信息
{info_s}

# 书面脚本 (供背景, 不要照抄)
tagline: {copy.get('tagline', '')}
selling_points: {copy.get('selling_points', [])}
cta: {copy.get('cta', '')}

# 30 cell 画面动作 (1.3s/个, 严格 1:1 配音)
{cells_text}

# 你的任务
为 30 个 cell 各自生成 1 句画外音, 严格描述画面动作.
- 数字必须是画面可见的 (触屏显示, 倒计时, 数字图标)
- 8-15 字/句
- emotion 从 [excited / persuasive / pain_point / demonstrate / trust / urgent] 选
- 直接输出 JSON."""

    last_err = None
    for m in [PRIMARY_MODEL, SECONDARY_MODEL]:
        try:
            raw = call_lk888(
                m,
                [{"role": "system", "content": sys_p},
                 {"role": "user", "content": user_p}],
                temperature=0.7, max_tokens=8000, timeout=300,  # 30 cell × 100 token = 3000
            )
            result = parse_json_strict(raw)
            if not isinstance(result, dict):
                last_err = f"{m}: returned non-dict"
                continue
            segs = result.get("voiceover_segments_full", [])
            if not isinstance(segs, list):
                last_err = f"{m}: voiceover_segments_full not list"
                continue
            if len(segs) != 30:
                last_err = f"{m}: returned {len(segs)} segments, expected 30"
                continue
            # 容错补全
            for i, seg in enumerate(segs, 1):
                if not isinstance(seg, dict):
                    seg = {"text": f"cell {i} 配音"}
                    segs[i-1] = seg
                if "text" not in seg or not seg.get("text"):
                    seg["text"] = cell_actions_all[i-1]["cell_action"][:15]
                if "cell_id" not in seg:
                    seg["cell_id"] = i
                if "segment" not in seg:
                    seg["segment"] = cell_actions_all[i-1]["segment"]
                if "local_cell" not in seg:
                    seg["local_cell"] = cell_actions_all[i-1]["local_cell"]
                if "timing_start" not in seg:
                    seg["timing_start"] = cell_actions_all[i-1]["timing_start"]
                if "timing_end" not in seg:
                    seg["timing_end"] = cell_actions_all[i-1]["timing_end"]
                if "emotion" not in seg:
                    seg["emotion"] = "demonstrate"
                if "audio_directive" not in seg:
                    seg["audio_directive"] = "(画外音, 跟随画面)"
                if "visual_sync_note" not in seg:
                    seg["visual_sync_note"] = cell_actions_all[i-1]["cell_action"][:80]
            result["_model"] = m
            result["_persona"] = persona
            return result
        except Exception as e:
            last_err = f"{m}: {e}"
            continue

    print(f"  [build_voiceover_synced] 失败: {last_err}, fallback")
    # Fallback: 把 cell_action 前 15 字直接当配音
    segs = []
    for c in cell_actions_all:
        segs.append({
            "cell_id": c["cell_id"],
            "segment": c["segment"],
            "local_cell": c["local_cell"],
            "timing_start": c["timing_start"],
            "timing_end": c["timing_end"],
            "text": c["cell_action"][:12],
            "emotion": "demonstrate",
            "pacing": "medium",
            "volume": "medium",
            "audio_directive": "(画外音, 跟随画面)",
            "visual_sync_note": c["cell_action"][:80],
        })
    return {"voiceover_segments_full": segs, "_model": "fallback", "_persona": persona}


def _fallback_voiceover_segments(info: dict, copy: dict, persona: str = "乐乐喵_萝莉") -> dict:
    """规则版 voiceover_segments (兜底)"""
    name = info.get("name", "产品")
    tagline = copy.get("tagline", "")
    sp = copy.get("selling_points", [])
    if isinstance(sp, str):
        sp = [s.strip() for s in sp.split("|") if s.strip()][:3]
    sp = sp[:3] if len(sp) >= 3 else sp + ["保证"] * (3 - len(sp))
    cta = copy.get("cta", "点击下方小黄车直接下单")


def build_voiceover_structured(product_info: dict, copy: dict, storyboard: list = None, persona: str = "乐乐喵_萝莉") -> dict:
    """5 段式带货口播稿 — 严格钩子 + 3 卖点 + 催单结构 (2026-09-23 用户要求)

    用户反馈: 之前的配音"没有按照带货视频的结构生成, 开头基本没感觉到钩子"
    之前的 build_voiceover_segments 生成 6 cell 跟画面同步, 但缺乏"5 段式带货结构".

    修复: 强制 5 段, 每段有明确的角色:
      段 1 (0-8s)   = 钩子 (痛点/反问) + 介绍产品
      段 2 (8-16s)  = 卖点 1 (产品形态 + 数字) + 演示场景
      段 3 (16-24s) = 卖点 2 (使用场景代入) + 情绪
      段 4 (24-32s) = 卖点 3 (一锅多用 / 多功能) + 信任
      段 5 (32-40s) = 信任收尾 (大容量 + 内胆) + 价格催单

    输入: product_info, copy (含 selling_points 3 条), storyboard (可选, 用于更精准匹配画面)
    输出: {"voiceover_segments": [
        {seg, text, emotion, rate, vol, purpose},  # 共 5 段
    ]}

    调用: gem-3.7-flash (PRIMARY) -> tt-5.6-luna (SECONDARY)
    """
    info_s = "\n".join([f"{k}:{v}" for k, v in product_info.items()
                        if v and not str(k).startswith("_") and not isinstance(v, list)])

    sp = copy.get("selling_points", [])
    if isinstance(sp, str):
        sp = [s.strip() for s in sp.split("|") if s.strip()][:3]
    sp = sp[:3] if len(sp) >= 3 else sp + ["实用"] * (3 - len(sp))
    cta = copy.get("cta", "点击下方小黄车直接下单")
    tagline = copy.get("tagline", "")

    # 5 段画面内容提示 (从 storyboard 拿)
    segment_desc = ""
    if storyboard and isinstance(storyboard, list):
        for s in storyboard[:5]:
            seg_id = s.get("segment", "?")
            desc = s.get("description", "")
            cell_actions = s.get("cell_actions", [])
            ca_short = " | ".join([str(c)[:40] for c in cell_actions[:3]]) if isinstance(cell_actions, list) else ""
            segment_desc += f"\n段 {seg_id} 画面: {desc[:80]} (动作: {ca_short})"

    sys_p = f"""# Role: 抖音金牌带货主播「{persona}」

## 任务: 生成 5 段式带货口播稿 (5 句, 段间画外音)

视频是 5 段拼接 (每段 8 秒, 总 40 秒), 你必须为每段生成 1 句配音, 段间 0.3-0.5 秒自然停顿.
**严格 5 段式结构, 不能省任何段**:

### 段 1 (0-8秒) 钩子 + 痛点 + 介绍产品 (40-50 字)
- **必须有"姐妹们/家人们/宝宝们"称呼开头**
- **用提问/反问/震惊开场** (例: "还在为...烦恼吗?")
- **快速切痛点** (1 句)
- **亮产品** + 1 个**承诺** (例: "这问题, 一台炊匠 IH 电饭煲就能解决")
- 节奏: **急促 + 热情** (rate +15%)

### 段 2 (8-16秒) 卖点 1 + 演示场景 (40-50 字)
- 突出**第 1 个卖点** (从 selling_points 选)
- **加画面可见的数字** (1300W / 30 分钟 / 0:30 倒计时)
- **演示场景代入** (例: "你看, 按预约键 30 分钟, 选蛋糕功能")
- 节奏: 平稳专业 (rate +5%)

### 段 3 (16-24秒) 卖点 2 + 场景故事 (30-40 字)
- 突出**第 2 个卖点** (从 selling_points 选)
- **场景代入 + 情绪** (例: "睡前预约煮粥, 早上掀盖就有热乎粥")
- 节奏: 稍慢 + 信任 (rate +5%)

### 段 4 (24-32秒) 卖点 3 + 一锅多用 (30-40 字)
- 突出**第 3 个卖点** (从 selling_points 选)
- **一锅多用 / 满足全家口味**
- 节奏: 稳定 (rate +3%)

### 段 5 (32-40秒) 信任收尾 + 价格催单 (35-45 字)
- **大容量 + 内胆好洗 + 信任**
- **必须有价格/优惠/催单** (例: "今天立减 100, 戳小黄车下单")
- 节奏: 急促 + 催单 (rate +18%)

## 禁词 (出现 1 次以上必须改)
- "哎呀家人们/闭眼入/冲就完了/绝绝子/YYDS" (模板词)
- 任何杜撰数字 (产品信息没有的数字)
- "此时此刻/由此可见" (书面语)

## Output Format (严格 JSON)
{{
  "voiceover_segments": [
    {{
      "seg": 1,
      "text": "姐妹们!还在为煮饭夹生烦恼吗?这台炊匠 IH 电饭煲, 一键预约 24 小时, 早上就有香喷喷的米饭!",
      "emotion": "excited",
      "purpose": "钩子+痛点+产品"
    }},
    {{
      "seg": 2,
      "text": "1300W IH 立体加热, 你看按预约键 30 分钟, 选蛋糕功能, 倒计时启动, 早餐自动好!",
      "emotion": "demonstrate",
      "purpose": "卖点 1: IH + 24h 预约演示"
    }},
    {{
      "seg": 3,
      "text": "睡前预约煮粥 6 小时, 早上掀盖就有热乎粥, 不用早起看锅!",
      "emotion": "trust",
      "purpose": "卖点 2: 煮粥场景代入"
    }},
    {{
      "seg": 4,
      "text": "12 大菜单, 柴火饭糙米饭煲仔饭蒸蛋糕, 一锅搞定全家口味!",
      "emotion": "persuasive",
      "purpose": "卖点 3: 多功能"
    }},
    {{
      "seg": 5,
      "text": "4 升大容量够 6 口人, 食品级内胆好洗耐用!今天立减 100 戳小黄车下单!",
      "emotion": "urgent",
      "purpose": "信任+催单"
    }}
  ]
}}

## 初始化
直接输出 JSON. 严格 5 段结构, 每段 1 句, 禁词表不可违反, 数字必须来自产品信息."""
    user_p = f"""# 商品信息 (来自产品图 + 文字, 真实可靠)
{info_s}

# 3 个核心卖点 (从产品信息提炼)
SP1: {sp[0] if len(sp) > 0 else ''}
SP2: {sp[1] if len(sp) > 1 else ''}
SP3: {sp[2] if len(sp) > 2 else ''}

# CTA + tagline
tagline: {tagline}
cta: {cta}

# 5 段画面内容 (从分镜)
{segment_desc}

# 你的任务
基于商品信息和 5 段画面, 生成 5 段式带货口播稿.
- 段 1 必须有"姐妹们/家人们/宝宝们"开头, 钩子提问 + 切痛点 + 亮产品
- 段 2-4 每段对应 1 个 SP, 加画面可见数字
- 段 5 信任收尾 + 价格催单
- 数字必须真实 (从产品信息), 不能编造
- 直接输出 JSON, 5 段必须全, 禁词不出现"""

    last_err = None
    for m in [PRIMARY_MODEL, SECONDARY_MODEL]:
        try:
            raw = call_lk888(
                m,
                [{"role": "system", "content": sys_p},
                 {"role": "user", "content": user_p}],
                temperature=0.8, max_tokens=2000, timeout=180,
            )
            result = parse_json_strict(raw)
            if not isinstance(result, dict):
                last_err = f"{m}: returned non-dict"
                continue
            if isinstance(result, list):
                result = {"voiceover_segments": result}
            segs = result.get("voiceover_segments", [])
            if not isinstance(segs, list):
                last_err = f"{m}: voiceover_segments not list"
                continue
            if len(segs) != 5:
                last_err = f"{m}: returned {len(segs)} segments, expected 5"
                continue
            # 容错补全
            for i, seg in enumerate(segs, 1):
                if not isinstance(seg, dict):
                    seg = {"text": f"段 {i} 配音", "seg": i}
                    segs[i-1] = seg
                if "text" not in seg or not seg.get("text"):
                    seg["text"] = f"段 {i} 配音"
                if "seg" not in seg:
                    seg["seg"] = i
                if "emotion" not in seg:
                    seg["emotion"] = "demonstrate"
                if "purpose" not in seg:
                    seg["purpose"] = f"段 {i}"
            result["_model"] = m
            result["_persona"] = persona
            return result
        except Exception as e:
            last_err = f"{m}: {e}"
            continue

    print(f"  [build_voiceover_structured] 失败: {last_err}, fallback")
    # Fallback: 用 selling_points 拼 5 段模板
    segs = [
        {"seg": 1, "text": f"姐妹们!还在为煮饭烦恼吗?这台{name}, 24 小时预约, 一键搞定全家早餐!", "emotion": "excited", "purpose": "钩子"},
        {"seg": 2, "text": f"{sp[0]}, 你看按一下, 自动启动, 早餐自动好!", "emotion": "demonstrate", "purpose": f"卖点1: {sp[0]}"},
        {"seg": 3, "text": f"{sp[1]}, 睡前预约, 早上掀盖就有!", "emotion": "trust", "purpose": f"卖点2: {sp[1]}"},
        {"seg": 4, "text": f"{sp[2]}, 一锅搞定全家口味!", "emotion": "persuasive", "purpose": f"卖点3: {sp[2]}"},
        {"seg": 5, "text": "大容量够全家用, 内胆好洗耐用!今天立减, 戳小黄车下单!", "emotion": "urgent", "purpose": "信任+催单"},
    ]
    return {"voiceover_segments": segs, "_model": "fallback", "_persona": persona}


def _fallback_voiceover_segments(info: dict, copy: dict, persona: str = "乐乐喵_萝莉") -> dict:
    """规则版 voiceover_segments (兜底)"""
    name = info.get("name", "产品")
    tagline = copy.get("tagline", "")
    sp = copy.get("selling_points", [])
    if isinstance(sp, str):
        sp = [s.strip() for s in sp.split("|") if s.strip()][:3]
    sp = sp[:3] if len(sp) >= 3 else sp + ["保证"] * (3 - len(sp))
    cta = copy.get("cta", "点击下方小黄车直接下单")
    # 简化: 用模板生成主播腔
    templates = [
        f"哎呀家人们, 看过来!",
        f"今天给大家安利「{name}」!",
        f"姐妹们, 你们是不是也有这个烦恼?",
        f"你看这个白色小管子, 挤出来 —— (停顿) 哇! 泡沫绵密得跟奶油一样!",
        f"洗完摸摸脸 —— (低声) 不紧绷, 不假滑, 真的【闭眼入】!",
        f"今天【只要 39】, 库存只剩 200 件, {cta}!",
    ]
    emotions = ["excited", "persuasive", "pain_point", "demonstrate", "trust", "urgent"]
    pacings = ["fast", "medium", "medium", "varied", "slow", "fast"]
    volumes = ["loud", "medium", "medium", "medium", "medium", "loud"]
    directives = [
        "(兴奋招呼, 快速, 镜头正对主播)",
        "(热情推荐, 中速, 略微俯身)",
        "(痛点共鸣, 略微上扬)",
        "(演示+夸张反应)",
        "(使用演示, 触觉调动)",
        "(紧迫召唤, 快速上扬)",
    ]
    visual_notes = [
        "主播对镜头招手",
        "产品入镜特写",
        "痛点场景特写",
        "挤泡沫慢动作",
        "主播摸脸",
        "价格大字弹出",
    ]
    return {
        "voiceover_segments": [
            {
                "cell_id": i + 1,
                "timing_start": i * 1.3,
                "timing_end": min((i + 1) * 1.3, 8.0),
                "text": t,
                "emotion": emotions[i],
                "pacing": pacings[i],
                "volume": volumes[i],
                "audio_directive": directives[i],
                "visual_sync_note": visual_notes[i],
            }
            for i, t in enumerate(templates)
        ],
        "_model": "fallback",
        "_persona": persona,
    }


def _fallback_storyboard_v3(info: dict) -> list:
    """规则版 5 段 x 1 镜 (每段 8s, 一段一卖点)"""
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
        data=json.dumps({"model": model, "prompt": prompt, "size": "720*1280", "n": 1}).encode(),
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