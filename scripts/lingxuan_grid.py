#!/usr/bin/env python3
"""灵炫 (lk888.ai / TT-Image-2) 6 宫格图生成模块.

调用 lk888.ai API 生成 6 宫格图 (3x2), 切 cell1-6.png.
依据 nmb2 生产脚本 ttimg.py 的协议封装.

用法:
  from lingxuan_grid import generate_6panel_grid
  cells = generate_6panel_grid(
      product_image="/path/to/pic.jpg",
      product_text="A类母婴级纯棉四件套",
      beats=[...6 镜描述...],
      output_dir="/path/to/cells/",
  )
"""
import os
import sys
import json
import time
import urllib.request
import urllib.error
import urllib.parse
import base64
import mimetypes
from pathlib import Path
from typing import List

# === lk888.ai 配置 ===
BASE = "https://api.lk888.ai"
MODEL = "tt-image-2"
KEY = None  # 启动时从 h3secrets.lk888_key() 读

def _key():
    """从 h3secrets 读 key (跟 ttimg.py 同源, 600 权限, 不入 git)"""
    global KEY
    if KEY:
        return KEY
    from pathlib import Path as P
    boot_root = next(
        (p for p in [P(__file__).resolve().parent, *P(__file__).resolve().parent.parents]
         if (p / "h3secrets.py").exists()), None
    )
    if boot_root is None:
        raise SystemExit("[boot] 找不到 h3secrets.py, fail-closed")
    sys.path.insert(0, str(boot_root))
    from h3secrets import lk888_key
    KEY = lk888_key()
    return KEY


def _post(path, payload, timeout=60):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": "Bearer " + _key(), "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _get(path, timeout=60):
    req = urllib.request.Request(
        BASE + path,
        headers={"Authorization": "Bearer " + _key()},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def as_data_uri(path):
    if path.startswith("http"):
        return path
    mime = mimetypes.guess_type(path)[0] or "image/png"
    with open(path, "rb") as f:
        return "data:%s;base64,%s" % (mime, base64.b64encode(f.read()).decode("ascii"))


def _retry(fn, tries=4, base=1.6, label="req"):
    """指数退避"""
    import time as _t
    last = None
    for i in range(tries):
        try:
            return fn()
        except Exception as e:
            if isinstance(e, urllib.error.HTTPError):
                if not (e.code >= 500 or e.code == 429):
                    raise
            last = e
            if i < tries - 1:
                _t.sleep(base ** (i + 1))
    raise last


def build_grid_prompt(product_text: str, beats: List[str]) -> str:
    """
    6 宫格 (3x2) prompt 模板.
    借鉴冷萃咖啡成功案例的格式 + 品类通用.
    """
    beats_str = "\n".join([f"  Panel {i+1}: {b}" for i, b in enumerate(beats)])

    # 关键: 强调产品外观, 避免灵炫幻觉化包装颜色
    prompt = f"""A 6-panel grid (3 columns × 2 rows) showing the sequential use of the EXACT product in a single continuous scene.

CRITICAL PRODUCT DESCRIPTION (preserve exactly):
{product_text}

The product must appear in EXACTLY the color, packaging type, and label shown in the reference image. Do NOT change the product color, packaging style, or add any labels/text that is not in the reference. If the reference shows a white bottle, the bottle must stay white. If the reference shows a gold tube, the tube must stay gold.

Read panels LEFT-TO-RIGHT, TOP-TO-BOTTOM (sequence 1→2→3, then 4→5→6).

Each panel shows a different moment in time:

{beats_str}

Requirements:
- All 6 panels must show the SAME person (same face, same clothing) demonstrating the SAME product
- The product must look identical across all 6 panels (no color change, no shape change)
- Each panel is a different moment in time (chronological sequence)
- Real photo style, NOT illustration, NOT cartoon, NOT anime
- Warm natural lighting, soft shadows, no harsh contrast
- Clean composition, the subject and product clearly visible in each panel
- NO text overlay, NO watermark, NO logo in any panel
- Vertical 9:16 aspect ratio (768×1344) suitable for short video

The 6-panel sequence tells a complete story of using this product from start to finish."""
    return prompt


def call_lingxuan(prompt: str, ref_image_path: str, size: str = "1536x1024") -> str:
    """调 lk888.ai API, 返回本地保存的图路径."""
    print(f"  [灵炫] 提交任务 (model={MODEL}, size={size})")

    # 提交任务
    params = {"size": size, "quality": "auto", "n": 1}
    if ref_image_path:
        params["images"] = [as_data_uri(ref_image_path)]
    payload = {"model": MODEL, "prompt": prompt, "params": params}
    resp = _retry(lambda: _post("/v1/media/generate", payload), label="create")
    if resp.get("code") not in (200, 0):
        raise RuntimeError(f"灵炫创建失败: {json.dumps(resp, ensure_ascii=False)[:500]}")
    task_id = resp.get("data", {}).get("task_id") or resp.get("task_id")
    print(f"  [灵炫] task_id={task_id}")

    # 轮询状态
    t0 = time.time()
    last_prog = None
    while time.time() - t0 < 420:
        try:
            r = _retry(lambda: _get(f"/v1/media/status?task_id={urllib.parse.quote(str(task_id))}"), tries=3, label="status")
        except Exception as e:
            time.sleep(4)
            continue
        state = r.get("state") or r.get("data", {}).get("state")
        prog = r.get("progress") or r.get("data", {}).get("progress")
        if prog != last_prog:
            print(f"   [{time.time()-t0:4.0f}s] state={state} progress={prog}")
            last_prog = prog
        if r.get("is_final") or r.get("data", {}).get("is_final"):
            # lk888.ai 真实字段: result_url (顶层, 不在 data 里)
            img_url = (
                r.get("result_url")
                or r.get("url")
                or r.get("data", {}).get("result_url")
                or r.get("data", {}).get("url")
                or r.get("data", {}).get("image_url")
            )
            if not img_url:
                raise RuntimeError(f"无图 URL: {json.dumps(r, ensure_ascii=False)[:300]}")
            break
        time.sleep(4)
    else:
        raise TimeoutError(f"灵炫任务 {task_id} 超时")

    # 下载图
    local = "/tmp/lingxuan_grid.png"
    req = urllib.request.Request(img_url, headers={"User-Agent": "curl/8"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        with open(local, "wb") as f:
            f.write(resp.read())
    size_kb = os.path.getsize(local) // 1024
    print(f"  [灵炫] ✅ 出图: {local} ({size_kb} KB)")
    return local


def slice_grid_to_cells(grid_path: str, output_dir: str, cols: int = 3, rows: int = 2) -> List[str]:
    """把宫格图切成 cell1-N.png (左→右, 上→下)"""
    from PIL import Image
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    img = Image.open(grid_path).convert("RGB")
    w, h = img.size
    cell_w = w // cols
    cell_h = h // rows

    paths = []
    for r in range(rows):
        for c in range(cols):
            x0, y0 = c * cell_w, r * cell_h
            x1, y1 = x0 + cell_w, y0 + cell_h
            cell = img.crop((x0, y0, x1, y1))
            idx = r * cols + c + 1
            cell_path = f"{output_dir}/cell{idx}.png"
            cell.save(cell_path, "PNG", optimize=True)
            paths.append(cell_path)
            print(f"  [切片] cell{idx}: {cell.size} → {cell_path}")
    return paths


def generate_6panel_grid(product_image: str, product_text: str,
                          beats: List[str], output_dir: str,
                          ref_image: str = None,
                          size: str = "720x1280") -> List[str]:
    """
    一键: 灵炫出 6 宫格图 + 切片成 cell1-6.png
    size: 默认 720x1280 (9:16 竖版) — cell 比例 ≈ 2:3, 接近画布比例, 减少变形
    ref_image: 参考图 (默认 = product_image, 用 K5 grid 作 ref 时可锁定模特)
    """
    if ref_image is None:
        ref_image = product_image
    prompt = build_grid_prompt(product_text, beats)
    grid_path = call_lingxuan(prompt, ref_image, size=size)
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    import shutil
    target_grid = f"{output_dir}/grid.png"
    shutil.copy(grid_path, target_grid)
    cells = slice_grid_to_cells(target_grid, output_dir, cols=3, rows=2)
    return cells