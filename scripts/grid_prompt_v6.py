#!/usr/bin/env python3
"""宫格图 prompt 生成器 (修复版 v6)

基于用户评审反馈修复:
1. 禁止: 文字、海报、广告图、购物车图标、价格标签
2. 禁止: 任何 cell 内嵌套子宫格
3. 禁止: 同一产品的位置/形态变化
4. 要求: 6 个 cell = 6 个独立画面, 每个 1:1 占比
5. 要求: 真实摄影风格 (非 3D/插画/海报)
"""
import json, os, re
from pathlib import Path

# === 强约束 prompt (4 块结构) ===
NEGATIVE_BLOCK = """ABSOLUTELY NO text, NO watermark, NO logo, NO price tag, NO shopping cart icon, NO poster-style graphics, NO captions, NO labels, NO title overlays anywhere in the image.
NO nested sub-grids or mini-panels inside any cell.
NO 3D rendering, NO illustration style, NO infographic style, NO cartoon style. STRICTLY real photographic still frame."""

CONSISTENCY_BLOCK = """The product MUST appear IDENTICAL across all 6 panels:
- Same product shape, size, color, material, packaging
- Same product position (always held in hand or placed at center)
- Same lighting direction
- Same background style"""

CELL_LAYOUT_BLOCK = """LAYOUT: Exactly 6 rectangular cells arranged in 3 rows × 2 columns grid.
Each cell is a FULL independent photograph (NOT a sub-grid, NOT a mini-panel).
Each cell occupies 1/6 of the entire image, no overlaps, no nesting.
Vertical 9:16 aspect ratio for the entire image."""

ACTION_BLOCK_TEMPLATE = """Panel 1 (sec0): {act1}
Panel 2 (sec1): {act2}
Panel 3 (sec2): {act3}
Panel 4 (sec3): {act4}
Panel 5 (sec4): {act5}
Panel 6 (sec5): {act6}"""


def build_grid_prompt(info: dict, beats: list, product_text: str = None) -> str:
    """生成严格的 6 宫格 prompt (4 块结构)"""
    name = info.get("name", "product")
    cat = info.get("category", "")
    pf = info.get("product_form", "tube")
    color = info.get("color", "")
    scene = info.get("usage_scene", "") or "neutral indoor background"

    # 提取 6 镜动作描述
    action_lines = []
    for i, b in enumerate(beats):
        if isinstance(b, dict) and b.get("description"):
            d = b["description"]
            # 去掉"镜 N:" 前缀
            d = re.sub(r'^镜\s*\d+[:：]\s*', '', d)
            d = re.sub(r'^【.*?】\s*', '', d)
            action_lines.append(f"Panel {i+1} ({int(b.get('time_range', [0,0])[0])}s-{int(b.get('time_range', [0,0])[1])}s): {d.strip()}")
    if len(action_lines) < 6:
        # fallback
        default_actions = [
            "Hand holds product up to camera for full display",
            "Hand brings product closer, half-open or twist cap",
            "Main action: product contacts skin/object (applies/places/demonstrates)",
            "Action continues: spread/use/complete core operation",
            "Hand places product down, results visible",
            "Final state: smile/satisfied pose with results in view"
        ]
        action_lines = default_actions

    action_block = "\n".join(action_lines[:6])

    prompt = f"""A vertical 9:16 image containing a 6-cell storyboard grid (3 rows × 2 columns).

PRODUCT: {name} ({cat}) — {pf}, color: {color}
SCENE: {scene}
{f'USER NOTES: {product_text[:200]}' if product_text else ''}

{CELL_LAYOUT_BLOCK}

{CONSISTENCY_BLOCK}

PHOTOGRAPHY STYLE: Real handheld smartphone-style vertical video still, soft natural lighting, shallow depth of field, indoor setting. Look like a real TikTok/Douyin product review screenshot, NOT a commercial poster.

{CONSISTENCY_BLOCK}

ACTION SEQUENCE (6 panels):
{action_block}

{NEGATIVE_BLOCK}

The final image must look like 6 sequential screenshots from one continuous real video, with the SAME product shown identically in each panel, photographed by a hand-held camera in one indoor location."""
    return prompt


def build_strict_negative_suffix() -> str:
    """返回强否定后缀 (可附加到任意 prompt)"""
    return (
        "\n\nCRITICAL: NO text, NO watermark, NO logo, NO price, "
        "NO shopping cart icon, NO caption, NO label, NO poster elements, "
        "NO nested sub-grids, NO 3D render, NO illustration. "
        "Real photo only. Product identical in all 6 panels. "
        "6 separate cells in 3x2 grid."
    )


if __name__ == "__main__":
    # 测试用
    info = {
        "name": "净澈·氨基酸温和洁面乳",
        "category": "面部清洁",
        "product_form": "翻盖塑料软管",
        "color": "白色",
        "usage_scene": "洗手间"
    }
    beats = [
        {"beat_id": 1, "time_range": [0.0, 1.3], "description": "双手手背轻触脸颊,展示T区油光"},
        {"beat_id": 2, "time_range": [1.3, 2.6], "description": "右手握洁面乳管身,左手推开翻盖"},
        {"beat_id": 3, "time_range": [2.6, 3.9], "description": "右手拇指食指挤压管身,挤出乳白膏体"},
        {"beat_id": 4, "time_range": [3.9, 5.2], "description": "双手掌心沾温水打圈揉搓出泡沫"},
        {"beat_id": 5, "time_range": [5.2, 6.5], "description": "泡沫覆于面部指腹打圈清洁鼻翼"},
        {"beat_id": 6, "time_range": [6.5, 7.8], "description": "清水冲净展示净澈素颜"}
    ]
    p = build_grid_prompt(info, beats)
    print(p)
    print("\n\n--- 字符数:", len(p))