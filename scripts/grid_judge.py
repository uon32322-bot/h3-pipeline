#!/usr/bin/env python3
"""宫格图判官 —— 用 VLM 检测崩坏 (多手/畸形/重影/产品不一致)

不通过自动重生成 (最多 3 次), 仍不通过则标记 FAIL
"""
import sys, os, json, base64, urllib.request, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _load_key():
    fp = "/root/.config/h3p/secrets.env"
    if not os.path.exists(fp):
        fp = "/root/autodl-tmp/h3p/secrets.env"
    for line in open(fp):
        if "LK888_KEY" in line:
            return line.strip().split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("LK888_KEY not found")


def img_b64_uri(path: str) -> str:
    with open(path, "rb") as f:
        b = base64.b64encode(f.read()).decode()
    return f"data:image/png;base64,{b}"


def judge_grid(image_path: str, product_name: str, product_form: str) -> dict:
    """调灵炫 VLM (gem-3.7-flash) 评审宫格图

    返回 {
        "ok": True/False,
        "issues": ["三只手" / "畸形" / "产品不一致"],
        "score": 0-100,
        "raw": "VLM 原始输出",
    }
    """
    data_uri = img_b64_uri(image_path)
    prompt = f"""你是带货视频宫格图质检员。严格检查这张 6-cell 宫格图 (3行×2列)。

产品: {product_name}
物理形态: {product_form}

**检查项 (任一 FAIL 整图 FAIL)**:
1. **手部数量异常**: 任何人物角色出现 3 只或更多手? (正常人 2 只)
2. **人体畸形**: 多余手指/缺失手指/畸形人脸/双重叠影/畸形身体
3. **产品一致**: 6 个 cell 的产品形态/颜色完全一致?
4. **cell 独立**: 6 个 cell 是否各自独立画面 (不是嵌套宫格)?
5. **比例**: 9:16 竖版, cell 比例正确?

**输出严格 JSON (不要 markdown)**:
{{
  "ok": true/false,
  "issues": ["三只手", "产品颜色不一致"],  // 失败原因列表
  "score": 0-100,
  "description": "一句话总结"
}}"""

    payload = {
        "model": "gem-3.7-flash",
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": data_uri}}
            ]
        }],
        "temperature": 0.2,
        "max_tokens": 600,
    }

    try:
        req = urllib.request.Request(
            "https://api.lk888.ai/v1/chat/completions",
            data=json.dumps(payload).encode(),
            headers={
                "Authorization": f"Bearer {_load_key()}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=120) as r:
            resp = json.loads(r.read().decode())
        raw = resp["choices"][0]["message"]["content"]

        # 容错解析 (有 markdown 包装)
        raw_clean = raw.strip()
        if raw_clean.startswith("```"):
            raw_clean = raw_clean.split("```", 2)[1]
            if raw_clean.startswith("json"):
                raw_clean = raw_clean[4:]
            raw_clean = raw_clean.strip().rstrip("`")
        return json.loads(raw_clean)
    except Exception as e:
        return {"ok": False, "issues": [f"VLM 评审失败: {e}"], "score": 0, "raw": str(e)}


def judge_and_retry(image_path: str, regenerate_fn, product_name: str,
                      product_form: str, max_retries: int = 3) -> dict:
    """判官 + 自动重生成

    regenerate_fn(): 调用 i2i 生成函数 (无参)
    """
    import os as _os_judge
    history = []
    # 第 1 次评审前先生成图 (如果不存在)
    if not _os_judge.path.exists(image_path):
        print(f"  [判官] 第 1 次先生成图...", flush=True)
        regenerate_fn()
        time.sleep(2)  # 等文件写入
    for attempt in range(1, max_retries + 1):
        print(f"  [判官] 第 {attempt}/{max_retries} 次评审...", flush=True)
        verdict = judge_grid(image_path, product_name, product_form)
        verdict["attempt"] = attempt
        history.append(verdict)
        print(f"    score={verdict.get('score', 0)}, ok={verdict.get('ok')}, issues={verdict.get('issues', [])}", flush=True)

        if verdict.get("ok"):
            print(f"  ✓ 评审通过!", flush=True)
            return {"final_ok": True, "history": history}

        if attempt < max_retries:
            print(f"  ✗ 评审未通过, 触发重生成...", flush=True)
            regenerate_fn()
            # 等 1 秒确保文件写入
            time.sleep(1)

    print(f"  ✗✗ {max_retries} 次评审仍未通过, 标记 FAIL", flush=True)
    return {"final_ok": False, "history": history}
