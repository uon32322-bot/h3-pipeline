#!/usr/bin/env python3
"""H3 带货视频端到端流水线 (决策 C, K6 洁面版本)

用法:
  python ecom_k6_cleanser.py
  (无需参数, 全部用本地预设)
"""
import argparse
import os
import sys

# 加仓内 scripts 到 path
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

# 1. 识别产品 (Qwen-VL fallback 规则, 读产品文字)
from ecom_end_to_end import identify_product
# 2. 文案 + 分镜 (LLM 优先, fallback 规则)
from ecom_end_to_end import build_copy, build_storyboard
from llm_modules import build_copy_v2, build_storyboard_v2
# 3. YAML prompt
from ecom_end_to_end import build_prompt
# 4. 渲染
from ecom_end_to_end import render_fl2va_5guides
# 5. 灵炫生图
from lingxuan_grid import generate_6panel_grid

# 预设
PROD_IMG = "/tmp/ecom_k6_cleanser.png"
PROD_TXT_FILE = "/tmp/k6_cleanser.txt"
K5_GRID_REF = "/tmp/k5_grid_ref.png"  # 复用 K5 6 宫格作参考图 (锁定模特)
OUTPUT = "/tmp/ecom_k6_out.mp4"
GRID_DIR = "/tmp/ecom_k6_grid"


def main():
    product_text = open(PROD_TXT_FILE, encoding='utf-8').read()
    print("=" * 60)
    print("  K6 清洁日化_洁面 端到端 (复用 K5 模特)")
    print("=" * 60)

    print(f"\n[1/5] 识别产品 (Qwen-VL fallback 规则)...")
    product_info = identify_product(PROD_IMG, product_text)
    print(f"  ✓ {product_info.get('name', '?')}")

    print(f"\n[2/5] 创作文案 (LLM 强化版)...")
    try:
        copy = build_copy_v2(product_info, product_text)
    except Exception as e:
        print(f"  fallback: {e}")
        copy = build_copy(product_info, product_text)

    print(f"\n[3/5] 生成分镜脚本 (LLM 动态 6 镜)...")
    try:
        storyboard = build_storyboard_v2(product_info, copy)
    except Exception as e:
        print(f"  fallback: {e}")
        storyboard = build_storyboard(product_info, copy)

    # 重写 beats 描述 — 强调产品外观 (避免灵炫幻觉化)
    product_color = product_info.get("color", "")
    # 让 beats 加 "white plastic squeeze tube with flip cap" (K6 是这种)
    beats_enhanced = []
    for b in storyboard:
        d = b["description"]
        # 每个 beat 都强调产品形态 (洁面乳是白色塑料软管)
        if any(k in d for k in ["洁面", "挤", "瓶", "挤泡沫"]):
            d = d + " (产品: 白色塑料软管, 矮胖圆管, 翻盖喷嘴, 挤出白色泡沫, 绝对不是金色/红色/口红形状)"
        beats_enhanced.append({"beat_id": b["beat_id"], "time_range": b["time_range"],
                                "keyframe": b["keyframe"], "description": d})
    storyboard = beats_enhanced
    print(f"  ✓ beats 已强化产品外观描述 (避免灵炫幻觉化)")

    print(f"\n[3.5/5] 自动灵炫生 6 宫格图 (K6 产品图 ref, 720x1280)...")
    beats_desc = [b['description'] for b in storyboard]
    cells = generate_6panel_grid(
        product_image=PROD_IMG,           # K6 产品图
        ref_image=K5_GRID_REF,             # K5 grid 作风格参考 (模特形象)
        product_text=product_text,
        beats=beats_desc,
        output_dir=GRID_DIR,
        size="720x1280",
    )

    print(f"\n[4/5] 对齐官方六段 YAML prompt...")
    prompt = build_prompt(storyboard, product_info, copy)
    print(f"  ✓ prompt 长度: {len(prompt)} chars")

    print(f"\n[5/5] 跑 ComfyUI 渲染 (FL2VA + 5 AddGuide, H3 自带 ambient audio)...")
    output = render_fl2va_5guides(GRID_DIR, prompt, OUTPUT)
    print(f"  ✅ 最终视频: {output}")
    print("=" * 60)


if __name__ == "__main__":
    main()