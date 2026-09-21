#!/usr/bin/env python3
"""LLM 评测脚本 (全自动 4 LLM × 3 产品, 不人工干预)

约束:
  1. 4 个 LLM 全自动对比 (gem-3.7-flash / tt-5.6-luna / tt-5.5 / gem-3.8-flash)
  2. 每个 LLM 输出 = 产品识别 + 5 段文案 + 6 镜分镜
  3. 每个产品的"最佳脚本"× 4 LLM 各生成 1 张宫格图
  4. 评审: 由人工 (用户) 看宫格图 + 报告

输入:
  python evaluate_llm.py
  (参数硬编码: 3 个产品 + 4 个 LLM)

输出:
  远端: /tmp/llm_eval/<product>_<model>.json (12 份)
  远端: /tmp/llm_eval/<product>_<model>_grid.png (4 份)
  本地: /Users/admin/Desktop/llm_eval/<product>_<model>_grid.png (4 份)
  本地: /Users/admin/Desktop/llm_eval/report.md (对比报告)
"""
import json
import os
import sys
import base64
import re
import time
from pathlib import Path
from typing import Dict, List

# === 配置 ===
H3P = "/root/autodl-tmp/h3p"
EVAL_DIR_REMOTE = "/tmp/llm_eval"
EVAL_DIR_LOCAL = "/Users/admin/Desktop/llm_eval"
LLM_MODELS = [
    "gem-3.7-flash",
    "tt-5.6-luna",
    "tt-5.5",
    "gem-3.8-flash",
]

# 3 个产品 (你后面填充)
PRODUCTS = [
    {
        "id": "K6_cleanser",
        "name": "氨基酸温和洁面乳",
        "image_local": "/Users/admin/Desktop/工具/测试产品/K6_清洁日化_洁面/主图.png",
        "image_remote": "/tmp/eval_K6_cleanser.png",
        "text_local": "/Users/admin/Desktop/工具/测试产品/K6_清洁日化_洁面/产品信息.txt",
    },
    # 产品 2 和 3 — 你填
    {
        "id": "PRODUCT_2_ID",
        "name": "PRODUCT_2_NAME",
        "image_local": "/Users/admin/Desktop/PATH/TO/PRODUCT_2.png",
        "image_remote": "/tmp/eval_PRODUCT_2.png",
        "text_local": "/Users/admin/Desktop/PATH/TO/PRODUCT_2_info.txt",
    },
    {
        "id": "PRODUCT_3_ID",
        "name": "PRODUCT_3_NAME",
        "image_local": "/Users/admin/Desktop/PATH/TO/PRODUCT_3.png",
        "image_remote": "/tmp/eval_PRODUCT_3.png",
        "text_local": "/Users/admin/Desktop/PATH/TO/PRODUCT_3_info.txt",
    },
]

# === 灵炫 lk888.ai 配置 ===
LK888_BASE = "https://api.lk888.ai/v1"


def _lk888_key():
    secrets = os.path.expanduser("~/.config/h3p/secrets.env")
    for line in open(secrets):
        line = line.strip()
        if line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        if k == "LK888_KEY":
            return v
    raise RuntimeError("LK888_KEY not found")


def call_lk888(model: str, messages: list, temperature: float = 0.7,
                max_tokens: int = 1024, json_mode: bool = False) -> str:
    """调灵炫 OpenAI 兼容 API"""
    import requests
    headers = {
        "Authorization": f"Bearer {_lk888_key()}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    resp = requests.post(f"{LK888_BASE}/chat/completions",
                          headers=headers, json=payload, timeout=60)
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def encode_image(image_path: str) -> str:
    """本地图片 → data URI"""
    with open(image_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    ext = Path(image_path).suffix.lstrip(".").lower()
    mime = "image/jpeg" if ext in ("jpg", "jpeg") else f"image/{ext}"
    return f"data:{mime};base64,{b64}"


# ============ 任务 1: 产品识别 ============
def task_recognize_product(model: str, image_path: str, product_text: str) -> dict:
    """用 LLM VLM 识别产品"""
    data_uri = encode_image(image_path)
    json_prompt = f"""你是化妆品/护肤品/消费品识别专家。
请仔细看这张产品图，提取以下信息并以严格 JSON 格式输出 (只输出 JSON, 不要其他):

{{
  "category": "口红/唇釉/粉底/眼影/腮红/洁面乳/护肤品/家电/服饰/食品/...",
  "name": "品牌+产品名",
  "color": "主色调",
  "shape": "包装形态 (方形管/圆管/翻盖塑料软管/矮胖圆瓶/...",
  "key_attributes": ["卖点1", "卖点2", "卖点3"],
  "skin_tone_match": "冷白皮/暖黄皮/百搭/不限",
  "usage_scene": "通勤/约会/家居/学习/...",
  "product_form": "tube/bottle/jar/compact/box/bag (用其中一个值)"
}}

如果用户给了产品文字信息, 以它为准: """ + (product_text or "(无)")

    try:
        raw = call_lk888(
            model,
            messages=[{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": data_uri}},
                {"type": "text", "text": json_prompt},
            ]}],
            temperature=0.3, max_tokens=600, json_mode=True,
        )
        return json.loads(raw)
    except Exception as e:
        return {"_error": str(e), "_model": model}


# ============ 任务 2: 文案 ============
def task_build_copy(model: str, product_info: dict, product_text: str) -> dict:
    """用 LLM 生成 5 段式带货文案"""
    system_prompt = """你是美妆/消费品带货博主, 中文母语, 擅长写 30-40s 短视频带货口播稿。
    文案要符合:
    1. 口语化 (像说话不像写作)
    2. 不要杜撰数字
    3. 卖点用具体画面描述
    4. 不要虚假承诺
    5. 段落要短, 每段不超过 25 字

    5 段式结构:
    1. tagline: 1-2 句开场钩子
    2. selling_points: 3 条卖点 (短句, 每条 ≤15 字)
    3. scene: 1 句使用场景
    4. cta: 1 句购买引导 (固定: "点击下方小黄车直接下单")"""

    info_summary = "\n".join([f"  {k}: {v}" for k, v in product_info.items()
                              if v and not k.startswith("_")])
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

只输出 JSON。"""

    try:
        raw = call_lk888(model,
                          [{"role": "system", "content": system_prompt},
                           {"role": "user", "content": user_prompt}],
                          temperature=0.8, max_tokens=600, json_mode=True)
        return json.loads(raw)
    except Exception as e:
        return {"_error": str(e), "_model": model}


# ============ 任务 3: 分镜脚本 ============
def task_build_storyboard(model: str, product_info: dict, copy: dict) -> list:
    """用 LLM 生成 6 镜分镜"""
    cat = product_info.get("category", "其他")
    name = product_info.get("name", "产品")
    color = product_info.get("color", "")
    product_form = product_info.get("product_form", "tube")
    tagline = copy.get("tagline", "") if isinstance(copy, dict) else ""

    system_prompt = """你是 8 秒带货短视频分镜师。
约束:
1. 总时长 ≤ 8 秒, 每镜 ≤ 1.3 秒
2. 每镜必须有具体手部/嘴唇/产品位置
3. 镜 1 和镜 6 必须形成"前→后"镜像 (起始微笑 + 结尾微笑展示)
4. 涂抹类 (口红/粉底/眼影): 中间至少 2 镜"膏体贴+涂抹"
5. 输出用中文描述
6. 描述要细到每个手指动作
7. 严格使用参考图中的产品形态 (如翻盖塑料软管 vs 旋转口红管, 不要混淆)

JSON 输出格式:
[
  {"beat_id": 1, "time_range": [0.0, 1.0], "keyframe": "...", "description": "..."},
  {"beat_id": 2, "time_range": [1.0, 3.0], "keyframe": "...", "description": "..."},
  ... 共 6 镜
]"""

    user_prompt = f"""请为以下产品生成 6 镜分镜:

产品: {name}
类别: {cat}
主色: {color}
产品形态: {product_form} (重要 - 必须保持完全一致)
开场钩子: {tagline}

按品类给建议:
- 涂抹类 (口红/粉底/眼影/腮红): 中间 2 镜"膏体贴+涂抹"
- 折叠类 (四件套/毛巾/床品): 中间 2 镜"折叠+展开"
- 穿戴类 (衣服/鞋子/包): 中间 2 镜"穿戴+展示"
- 清洁类 (洁面乳/洗面奶): 中间 2 镜"挤泡沫+搓泡"
- 食品类 (零食/饮料): 中间 2 镜"开包装+品尝"
- 数码类 (耳机/手机): 中间 2 镜"上手+操作"
- 玩具类 (积木/玩偶): 中间 2 镜"拼接+展示"

只输出 JSON 数组。"""

    try:
        raw = call_lk888(model,
                          [{"role": "system", "content": system_prompt},
                           {"role": "user", "content": user_prompt}],
                          temperature=0.6, max_tokens=1200, json_mode=True)
        m = re.search(r'\[.*\]', raw, re.S)
        if not m:
            raise RuntimeError("未找到 JSON 数组")
        beats = json.loads(m.group(0))
        if not isinstance(beats, list) or len(beats) != 6:
            raise RuntimeError(f"不是 6 镜, 实际 {len(beats) if isinstance(beats, list) else '?'} 镜")
        for b in beats:
            for k in ("beat_id", "time_range", "description"):
                if k not in b:
                    raise RuntimeError(f"beat 缺字段 {k}")
        return beats
    except Exception as e:
        return [{"_error": str(e), "_model": model}]


# ============ 宫格图生成 (灵炫 tt-image-2.5-flare) ============
def generate_grid(product_info: dict, beats: list, output_remote: str,
                 output_local: str = None):
    """调灵炫 tt-image-2.5-flare 生成 720x1280 9:16 宫格图"""
    import requests
    beats_str = "\n".join([f"  Panel {i+1}: {b['description']}" for i, b in enumerate(beats)])
    prompt = f"""A 6-panel grid (3 columns × 2 rows) showing the sequential use of the product.

Product: {product_info.get('name','')} ({product_info.get('category','')})

Each panel:
{beats_str}

The product must appear identically across all 6 panels.
Vertical 9:16 aspect ratio, real photo style, warm lighting."""

    cfg = _lk888_key()
    # 提交任务
    resp = requests.post(
        f"{LK888_BASE}/images/generations",
        headers={"Authorization": f"Bearer {cfg}", "Content-Type": "application/json"},
        json={"model": "tt-image-2.5-flare", "prompt": prompt, "size": "720x1280", "n": 1},
        timeout=60,
    ).json()
    task_id = resp.get("data", {}).get("task_id")
    if not task_id:
        return {"_error": f"create failed: {resp}"}

    # 轮询
    for _ in range(40):
        time.sleep(5)
        st = requests.get(f"{LK888_BASE}/images/status?task_id={task_id}",
                          headers={"Authorization": f"Bearer {cfg}"},
                          timeout=10).json()
        if st.get("is_final"):
            url = st.get("data", {}).get("result_url") or st.get("result_url")
            if url:
                img = requests.get(url, timeout=30).content
                with open(output_remote, "wb") as f:
                    f.write(img)
                if output_local:
                    Path(output_local).parent.mkdir(parents=True, exist_ok=True)
                    with open(output_local, "wb") as f:
                        f.write(img)
                return {"_ok": True, "path": output_remote}
            return {"_error": f"no url: {st}"}

    return {"_error": "timeout"}


# ============ 远端上传 + 执行 ============
def upload_to_remote(local_path: str, remote_path: str):
    """scp 上传"""
    import subprocess
    env = os.environ.copy()
    env["SSH_ASKPASS"] = os.path.expanduser("~/.ssh/askpass_nmb2.sh")
    env["SSH_ASKPASS_REQUIRE"] = "force"
    sopts = ["-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
              "-o", "ConnectTimeout=10"]
    r = subprocess.run(
        ["scp"] + sopts + ["-P", "36229", local_path,
         f"root@connect.nmb2.seetacloud.com:{remote_path}"],
        capture_output=True, text=True, env=env, timeout=60,
    )
    return r.returncode == 0


def run_on_remote(script_content: str) -> tuple:
    """在远端跑 python 脚本, 返回 (stdout, stderr)"""
    import subprocess
    env = os.environ.copy()
    env["SSH_ASKPASS"] = os.path.expanduser("~/.ssh/askpass_nmb2.sh")
    env["SSH_ASKPASS_REQUIRE"] = "force"
    sopts = ["-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
              "-o", "ConnectTimeout=10"]
    H = "root@connect.nmb2.seetacloud.com"
    # 推脚本
    r = subprocess.run(["ssh"] + sopts + ["-p", "36229", H, "cat > /tmp/eval_llm.py"],
                       input=script_content, capture_output=True, text=True, env=env)
    # 跑脚本
    r = subprocess.run(
        ["ssh"] + sopts + ["-p", "36229", H,
         "venv/bin/python /tmp/eval_llm.py 2>&1 | tail -80"],
        capture_output=True, text=True, env=env, timeout=1800,
    )
    return r.stdout, r.stderr


# ============ 主入口 ============
def main():
    os.makedirs(EVAL_DIR_LOCAL, exist_ok=True)

    print("=" * 60)
    print(f"  LLM 评测: {len(LLM_MODELS)} 模型 × {len(PRODUCTS)} 产品 = {len(LLM_MODELS)*len(PRODUCTS)} 套")
    print(f"  模型: {LLM_MODELS}")
    print(f"  产品: {[p['id'] for p in PRODUCTS]}")
    print("=" * 60)

    # 1. 上传图片 + 文字到远端
    for p in PRODUCTS:
        if not Path(p["image_local"]).exists():
            print(f"❌ 图片缺失: {p['image_local']}")
            return
        if not Path(p["text_local"]).exists():
            print(f"❌ 文字信息缺失: {p['text_local']}")
            return
        print(f"\n  上传 {p['id']}:")
        print(f"    图片: {p['image_local']} → {p['image_remote']}")
        upload_to_remote(p["image_local"], p["image_remote"])
        upload_to_remote(p["text_local"], p["text_remote"])

    # 2. 生成远端执行脚本
    # 这里需要把 PRODUCTS 配置序列化进脚本
    import json as _json
    products_json = _json.dumps(PRODUCTS, ensure_ascii=False)
    llms_json = _json.dumps(LLM_MODELS)

    # 远端 eval_llm.py 直接调 lk888
    remote_script = f'''#!/usr/bin/env python3
"""远端评测脚本 — 自动跑 4 LLM × 3 产品, 输出 JSON"""
import json, os, sys, re, time, base64, urllib.request, urllib.error
from pathlib import Path

LK888_BASE = "https://api.lk888.ai/v1"
OUT_DIR = "/tmp/llm_eval"
os.makedirs(OUT_DIR, exist_ok=True)

LLM_MODELS = {llms_json}
PRODUCTS = {products_json}

def _key():
    for line in open("/root/.config/h3p/secrets.env"):
        line = line.strip()
        if line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        if k == "LK888_KEY":
            return v
    raise RuntimeError("LK888_KEY not found")

def _post(path, payload):
    req = urllib.request.Request(LK888_BASE + path,
        data=json.dumps(payload).encode(),
        headers={{"Authorization": "Bearer " + _key(), "Content-Type": "application/json"}},
        method="POST")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())

def _get(path):
    req = urllib.request.Request(LK888_BASE + path,
        headers={{"Authorization": "Bearer " + _key()}}, method="GET")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())

def call_lk888(model, messages, temperature=0.7, max_tokens=1024, json_mode=False):
    payload = {{"model": model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}}
    if json_mode:
        payload["response_format"] = {{"type": "json_object"}}
    return _post("/chat/completions", payload)["choices"][0]["message"]["content"]

def img_b64(path):
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()

def ext_mime(p):
    e = Path(p).suffix.lstrip(".").lower()
    return "image/jpeg" if e in ("jpg","jpeg") else f"image/{{e}}"

# === 任务函数 ===
def task_recognize(model, img, text):
    data_uri = f"data:{{ext_mime(img)}};base64,{{img_b64(img)}}"
    prompt = f"""你是产品识别专家。提取 JSON: {{category, name, color, shape, key_attributes[], skin_tone_match, usage_scene, product_form}}
如果用户给了文字, 以它为准: {{text or "(无)"}}"""
    try:
        raw = call_lk888(model,
            [{{"role": "user", "content": [
                {{"type": "image_url", "image_url": {{"url": data_uri}}}},
                {{"type": "text", "text": prompt}}
            ]}}], temperature=0.3, max_tokens=600, json_mode=True)
        return json.loads(raw)
    except Exception as e:
        return {{"_error": str(e)}}

def task_copy(model, info, text):
    sys_p = """5 段式带货口播稿.口语化,不杜撰数字,具体画面描述,段落 ≤25 字. 输出 JSON {{tagline, selling_points[3], scene, cta}}"""
    info_s = "\\n".join([f"  {{k}}: {{v}}" for k, v in info.items() if v and not k.startswith("_")])
    user_p = f"产品: {{info.get('name','?')}}, {{info.get('category','')}}\\n卖点: {{info_s}}\\n{{f'用户文字: {{text}}' if text else ''}}\\n输出 JSON"
    try:
        raw = call_lk888(model,
            [{{"role": "system", "content": sys_p}}, {{"role": "user", "content": user_p}}],
            temperature=0.8, max_tokens=600, json_mode=True)
        return json.loads(raw)
    except Exception as e:
        return {{"_error": str(e)}}

def task_storyboard(model, info, copy):
    cat = info.get("category", "")
    name = info.get("name", "")
    pf = info.get("product_form", "")
    tagline = copy.get("tagline", "") if isinstance(copy, dict) else ""
    sys_p = """8s 带货分镜师. 总时长 ≤8s, 每镜 ≤1.3s, 手部细节, 镜1镜6 镜像, 严格用产品形态. JSON 数组 6 镜"""
    user_p = f"产品: {{name}}, 类别: {{cat}}, 形态: {{pf}}, 钩子: {{tagline}}\\n输出 6 镜 JSON 数组"
    try:
        raw = call_lk888(model,
            [{{"role": "system", "content": sys_p}}, {{"role": "user", "content": user_p}}],
            temperature=0.6, max_tokens=1200, json_mode=True)
        m = re.search(r'\\[.*\\\\]', raw, re.S)
        if not m: raise RuntimeError("no JSON array")
        return json.loads(m.group(0))
    except Exception as e:
        return [{{"_error": str(e)}}]

def gen_grid(model_name, info, beats, out_remote):
    beats_s = "\\n".join([f"  Panel {{i+1}}: {{b.get('description','')}}" for i, b in enumerate(beats) if isinstance(b, dict)])
    prompt = f"""6-panel grid 3x2 sequential use. Product: {{info.get('name','')}} ({{info.get('category','')}})
Each panel:
{{beats_s}}
Product identical across panels. Vertical 9:16. Real photo."""
    try:
        r = _post("/images/generations",
            {{"model": "tt-image-2.5-flare", "prompt": prompt, "size": "720x1280", "n": 1}})
        tid = r.get("data", {{}}).get("task_id") or r.get("task_id")
        if not tid: return {{"_error": r}}
        for _ in range(60):
            time.sleep(5)
            st = _get(f"/images/status?task_id={{tid}}")
            if st.get("is_final"):
                url = st.get("data", {{}}).get("result_url") or st.get("result_url")
                if url:
                    req = urllib.request.Request(url, headers={{"User-Agent": "curl/8"}})
                    with urllib.request.urlopen(req, timeout=30) as resp:
                        with open(out_remote, "wb") as f:
                            f.write(resp.read())
                    return {{"_ok": True}}
        return {{"_error": "timeout"}}
    except Exception as e:
        return {{"_error": str(e)}}

# === 主循环 ===
for p in PRODUCTS:
    text = Path(p["text_remote"]).read_text(encoding="utf-8")
    for m in LLM_MODELS:
        key = f"{{p['id']}}__{{m}}"
        print(f"=== {{key}} ===")
        result = {{"model": m, "product": p["id"], "text_input": text[:200]}}
        # 1. 识别
        info = task_recognize(m, p["image_remote"], text)
        result["recognition"] = info
        # 2. 文案
        copy = task_copy(m, info, text) if "_error" not in info else {{}}
        result["copy"] = copy
        # 3. 分镜
        beats = task_storyboard(m, info, copy) if isinstance(copy, dict) and "_error" not in copy else []
        result["storyboard"] = beats
        # 4. 宫格图 (用最佳脚本)
        if beats and isinstance(beats[0], dict) and "_error" not in beats[0]:
            grid_result = gen_grid(m, info, beats, f"{{OUT_DIR}}/{{key}}_grid.png")
            result["grid_result"] = grid_result
        with open(f"{{OUT_DIR}}/{{key}}.json", "w") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"  -> {{key}}.json")
'''
    print(f"\n=== 推送脚本到远端 ({len(remote_script)} chars) ===")
    stdout, stderr = run_on_remote(remote_script)
    print("\n--- 远端输出 (尾 60 行) ---")
    print("\n".join(stdout.split("\n")[-60:]))
    if stderr:
        print("STDERR:", stderr[-500:])

    # 3. 拉回所有 grid + 报告
    import subprocess
    env = os.environ.copy()
    env["SSH_ASKPASS"] = os.path.expanduser("~/.ssh/askpass_nmb2.sh")
    env["SSH_ASKPASS_REQUIRE"] = "force"
    sopts = ["-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
              "-o", "ConnectTimeout=10"]
    H = "root@connect.nmb2.seetacloud.com"
    Path(EVAL_DIR_LOCAL).mkdir(parents=True, exist_ok=True)

    print("\n=== 拉回所有 grid 图片 ===")
    for p in PRODUCTS:
        for m in LLM_MODELS:
            remote_grid = f"{EVAL_DIR_REMOTE}/{p['id']}_{m}_grid.png"
            local_grid = f"{EVAL_DIR_LOCAL}/{p['id']}_{m}_grid.png"
            r = subprocess.run(["scp"] + sopts + ["-P", "36229",
                f"{H}:{remote_grid}", local_grid],
                capture_output=True, text=True, env=env, timeout=30)
            if r.returncode == 0:
                print(f"  ✓ {local_grid}")
            else:
                print(f"  ✗ {local_grid}: {r.stderr[:100]}")

    # 4. 生成对比报告
    print("\n=== 生成对比报告 ===")
    report_lines = [
        "# LLM 评测报告",
        "",
        f"对比模型: {', '.join(LLM_MODELS)}",
        f"测试产品: {len(PRODUCTS)} 个",
        "",
        "## 评审指标 (由你)",
        "",
        "1. 产品识别 - 类别准确？卖点具体？",
        "2. 文案 - 口语化？钩子有力？卖点不杜撰？CTA 匹配？",
        "3. 分镜 - 6 镜时间总和 ≤8s？动作细到手指？品类按钮？",
        "4. 宫格图 - 6 格一致？产品形态锁？无乱漂？",
        "",
        "## 宫格图位置",
        "",
    ]
    for p in PRODUCTS:
        report_lines.append(f"### {p['id']} ({p['name']})")
        for m in LLM_MODELS:
            report_lines.append(f"- [{m}]({p['id']}_{m}_grid.png)")
        report_lines.append("")
    report_lines.append("## JSON 输出位置")
    report_lines.append(f"远端: {EVAL_DIR_REMOTE}/<product>_<model>.json")
    report_path = f"{EVAL_DIR_LOCAL}/report.md"
    Path(report_path).write_text("\n".join(report_lines))
    print(f"  ✓ {report_path}")


if __name__ == "__main__":
    main()