#!/usr/bin/env python3
"""LLM 模块化函数 (build_copy_v2 + build_storyboard_v2)

通过 DeepSeek API 强化:
  - build_copy_v2: 5 段式带货文案 (tagline/selling_points/scene/cta)
  - build_storyboard_v2: 动态 6 镜分镜 (按品类出 ≤1.3s 动作)

fallback 链:
  - DeepSeek API 失败 → build_copy / build_storyboard (规则版本)

usage:
  from llm_modules import build_copy_v2, build_storyboard_v2
  copy = build_copy_v2(product_info, product_text)
  storyboard = build_storyboard_v2(product_info, copy)
"""
import json
import os
import re
from typing import Dict, List

# DeepSeek 配置 (从 secrets.env 读)
def load_deepseek_config():
    """从 ~/.config/h3p/secrets.env 读 DeepSeek 配置"""
    secrets_path = os.path.expanduser("~/.config/h3p/secrets.env")
    cfg = {"base_url": None, "model": "deepseek-chat", "api_key": None}
    if not os.path.exists(secrets_path):
        return cfg
    for line in open(secrets_path):
        line = line.strip()
        if line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        if k == "DEEPSEEK_BASE_URL":
            cfg["base_url"] = v
        elif k == "DEEPSEEK_MODEL":
            cfg["model"] = v
        elif k == "DEEPSEEK_API_KEY":
            cfg["api_key"] = v
    return cfg


def call_deepseek(system: str, user: str, temperature: float = 0.7, max_tokens: int = 1024) -> str:
    """调 DeepSeek API, 返回纯文本"""
    import requests
    cfg = load_deepseek_config()
    if not cfg["api_key"]:
        raise RuntimeError("DEEPSEEK_API_KEY 未配置")

    url = f"{cfg['base_url']}/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {cfg['api_key']}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": cfg["model"],
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},  # 强制 JSON 输出
    }
    resp = requests.post(url, headers=headers, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


# ============ build_copy_v2 (LLM 强化版) ============
def build_copy_v2(product_info: dict, product_text: str = "") -> dict:
    """
    用 DeepSeek 生成 5 段式带货文案.
    输入: product_info (识别结果), product_text (用户文字, 可能为空)
    输出: {tagline, selling_points[3], scene, cta}
    fallback: 任何异常 → 调用规则 build_copy()
    """
    system_prompt = """你是一个美妆带货博主, 中文母语, 擅长写 30-40s 短视频带货口播稿.
文案要符合:
1. 口语化 (像说话不像写作, 禁止"大家好"开场)
2. 不要杜撰数字 (如"16小时持妆"), 数字必须与产品一致
3. 卖点用具体画面描述 (不要"持久不脱色", 要"涂完去吃饭不掉色")
4. 皮肤友好: 不要虚假承诺 (如"一夜祛斑")
5. 段落要短, 每段不超过 25 字 (口播节奏感)

5 段式结构:
1. tagline: 1-2 句开场钩子 (吸引注意力)
2. selling_points: 3 条产品卖点 (短句, 每条 ≤15 字)
3. scene: 1 句使用场景 (如"通勤约会都能用")
4. cta: 1 句购买引导 (固定话术: "点击下方小黄车直接下单")"""

    info_summary = "\n".join([f"  {k}: {v}" for k, v in product_info.items() if v and k != "_raw"])
    user_prompt = f"""请根据以下产品信息生成带货文案 (严格 JSON 格式输出):

{{
  "tagline": "1-2 句开场钩子",
  "selling_points": ["卖点1", "卖点2", "卖点3"],
  "scene": "1 句使用场景",
  "cta": "点击下方小黄车直接下单"
}}

产品识别结果:
{info_summary}

{f"用户文字信息: {product_text}" if product_text else ""}

只输出 JSON, 不要其他说明。"""

    try:
        raw = call_deepseek(system_prompt, user_prompt, temperature=0.8, max_tokens=512)
        # 解析 JSON (DeepSeek response_format=json_object 应返回纯 JSON)
        result = json.loads(raw)
        # 字段校验
        assert "tagline" in result and "selling_points" in result and "scene" in result and "cta" in result
        assert isinstance(result["selling_points"], list) and len(result["selling_points"]) >= 1
        print(f"  [LLM build_copy] tagline: {result['tagline'][:50]}...")
        return result
    except Exception as e:
        print(f"⚠️  build_copy_v2 LLM 失败: {e}, 退回规则")
        return build_copy(product_info, product_text)


# ============ build_storyboard_v2 (LLM 动态 6 镜) ============
def build_storyboard_v2(product_info: dict, copy: dict) -> list:
    """
    用 DeepSeek 动态生成 6 镜分镜.
    输入: product_info (含 category/name/color), copy (文案)
    输出: [{beat_id, time_range, keyframe, description}, ...]
    时间范围累计 ≤ 8s, 每镜 ≤ 1.5s
    fallback: 任何异常 → 调规则 build_storyboard()
    """
    cat = product_info.get("category", "口红")
    name = product_info.get("name", "产品")
    color = product_info.get("color", "")
    tagline = copy.get("tagline", "")

    system_prompt = """你是一个 8 秒带货短视频分镜师.
任务: 根据产品类别, 把使用过程拆成 6 个 ≤1.3 秒的具体动作.
约束:
1. 总时长 ≤ 8 秒, 每镜 ≤ 1.3 秒
2. 每镜必须有具体手部/嘴唇/产品位置 (不要"展示产品", 要"右手拇指+食指拧开金盖")
3. 镜 1 和镜 6 必须形成"前→后"镜像 (起始微笑 + 结尾微笑展示)
4. 涂抹类 (口红/粉底/眼影): 中间至少 2 镜"膏体贴近涂抹"
5. 输出用中文描述
6. 描述要细到每个手指动作 (提升 H3 模型可控性)

JSON 输出格式:
[
  {{"beat_id": 1, "time_range": [0.0, 1.0], "keyframe": "...", "description": "..."}},
  {{"beat_id": 2, "time_range": [1.0, 3.0], "keyframe": "...", "description": "..."}},
  ... (共 6 镜)
]"""

    user_prompt = f"""请为以下产品生成 6 镜分镜:

产品: {name}
类别: {cat}
主色: {color}
开场钩子: {tagline}

{'(注意: 涂抹类产品要中间有 2 镜膏体贴+涂抹动作)' if cat in ('口红', '唇釉', '粉底', '眼影') else ''}

只输出 JSON 数组, 不要其他说明。"""

    try:
        raw = call_deepseek(system_prompt, user_prompt, temperature=0.6, max_tokens=800)
        # 解析 (DeepSeek 可能返回 ```json ... ``` 包裹)
        json_match = re.search(r'\[.*\]', raw, re.S)
        if not json_match:
            raise RuntimeError("未找到 JSON 数组")
        beats = json.loads(json_match.group(0))
        # 校验
        assert isinstance(beats, list) and len(beats) == 6
        for b in beats:
            assert "beat_id" in b and "time_range" in b and "description" in b
        print(f"  [LLM build_storyboard] {len(beats)} 镜生成")
        for b in beats:
            print(f"    镜 {b['beat_id']}: [{b['time_range'][0]:.1f}-{b['time_range'][1]:.1f}s] {b['keyframe']}")
        return beats
    except Exception as e:
        print(f"⚠️  build_storyboard_v2 LLM 失败: {e}, 退回规则")
        return build_storyboard(product_info, copy)


# ============ fallback 链 (从 ecom_end_to_end 导入) ============
# 这里循环引用: 避免循环 import, 用 lazy import
def _lazy_fallback():
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    global build_copy, build_storyboard
    import ecom_end_to_end as _ece
    build_copy = _ece.build_copy
    build_storyboard = _ece.build_storyboard

_lazy_fallback()