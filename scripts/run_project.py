#!/usr/bin/env python3
"""项目级端到端入口 - 唯一推荐调用方式

用法:
    # 1. 创建项目 + 准备素材
    python run_project.py --project shoes_5seg_v5 create \\
        --product /path/to/product.png \\
        --person /path/to/person.png \\
        --product-text /path/to/product.txt

    # 2. 跑 LLM (产品识别 + 文案 + 5 段分镜)
    python run_project.py --project shoes_5seg_v5 llm

    # 3. 跑灵炫 i2i 生成 5 张宫格图 (带 VLM 判官自动重生成)
    python run_project.py --project shoes_5seg_v5 grids

    # 4. 切分宫格图 -> 30 个 cell
    python run_project.py --project shoes_5seg_v5 cells

    # 5. 生成 5 段 H3 workflow (不写死 cell 路径, 从项目读)
    python run_project.py --project shoes_5seg_v5 workflows

    # 6. 提交 ComfyUI 渲染 5 段 H3
    python run_project.py --project shoes_5seg_v5 render

    # 7. 拼接 40s 完整视频
    python run_project.py --project shoes_5seg_v5 concat

    # 一键全跑
    python run_project.py --project shoes_5seg_v5 all
"""
import argparse
import os
import sys
import json
import time

# 让脚本能 import 同目录下的其他模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from project_state import ProjectState, get_project_root, is_server, list_projects


def cmd_create(args):
    """创建项目 + 准备素材"""
    proj = ProjectState(args.project)
    print(f"=== 创建项目 {args.project} ===")
    print(f"项目目录: {proj.dir}")

    # 复制素材
    import shutil
    if args.product:
        shutil.copy(args.product, proj.input_product_path())
        print(f"  ✓ product: {proj.input_product_path()}")
    if args.person:
        shutil.copy(args.person, proj.input_person_path())
        print(f"  ✓ person: {proj.input_person_path()}")
    if args.product_text:
        shutil.copy(args.product_text, os.path.join(proj.input_dir, "product.txt"))
        print(f"  ✓ product.txt: {proj.input_dir}/product.txt")

    print(f"\n项目就绪. 接下来跑: --project {args.project} llm")


def cmd_llm(args):
    """跑 LLM (识别 + 文案 + 5 段分镜)"""
    proj = ProjectState(args.project)
    print(f"=== 跑 LLM ({args.project}) ===")

    # 强 reload (避免污染)
    for m in ["llm_modules", "segment_to_grid", "grid_prompt_v6", "grid_judge"]:
        if m in sys.modules:
            del sys.modules[m]
    import llm_modules

    text = open(os.path.join(proj.input_dir, "product.txt")).read()
    image = proj.input_product_path()
    person = proj.input_person_path()

    print("[1/3] 产品识别...", flush=True)
    info = llm_modules.recognize_product(image, text)
    print(f"  → {info.get('_model', '?')}: {info.get('category', '')}")

    print("[2/3] 文案生成...", flush=True)
    copy = llm_modules.build_copy_v2(info, text)
    print(f"  → {copy.get('_model', '?')}: {copy.get('tagline', '')[:80]}")

    # 2026-09-23 新增: 独立生成主播腔口播稿 (跟脚本分离, 借鉴 Streamer-Sales + yao + H3 官方)
    print("[2.5/3] 独立口播稿生成 (主播腔, 跟脚本分离)...", flush=True)
    try:
        vo_result = llm_modules.build_voiceover_segments(info, copy)
        copy["voiceover_segments"] = [
            seg.get("text", "") for seg in vo_result.get("voiceover_segments", [])
        ]
        copy["voiceover_segments_full"] = vo_result.get("voiceover_segments", [])
        print(f"  → {vo_result.get('_model', '?')}: 6 cell 主播腔")
        # 显示每个 cell 内容
        for seg in vo_result.get("voiceover_segments", []):
            print(f"    cell {seg.get('cell_id', '?')} [{seg.get('emotion', '')}]: {seg.get('text', '')}")
    except Exception as e:
        print(f"  → 失败: {e}, 用脚本衍生 voiceover_segments")

    print("[3/3] 分镜 (5 段 × 1 镜 v3)...", flush=True)
    beats = llm_modules.build_storyboard_v3(info, copy)
    print(f"  → {beats[0].get('_model', '?')}: 共 {len(beats)} 段")

    # 保存到项目 (单一 source of truth)
    proj.save_storyboard(beats, copy, info)
    print(f"\n✓ storyboard 已保存到 {proj.manifest_path}")


def cmd_grids(args):
    """跑灵炫 i2i 生成 5 张宫格图 (带判官)"""
    proj = ProjectState(args.project)
    print(f"=== 跑灵炫宫格图 ({args.project}) ===")

    for m in ["llm_modules", "segment_to_grid", "grid_prompt_v6", "grid_judge"]:
        if m in sys.modules:
            del sys.modules[m]
    import segment_to_grid
    import grid_judge

    beats = proj.get_storyboard()
    info = proj.get_info()
    copy = proj.get_copy()
    person = proj.input_person_path()

    # 5 段 × 1 镜 -> 5 segments
    segments = segment_to_grid.slice_5_to_5(beats)

    for seg in segments:
        seg_num = seg["segment"]
        prompt = proj.build_lingxuan_prompt(seg, cell_count=6)
        out_path = proj.grid_path(seg_num)
        print(f"\n--- 段 {seg_num} ---", flush=True)

        # 判官 + 自动重生成
        def regenerate():
            return segment_to_grid.generate_grid_i2i(prompt, person, out_path)

        verdict = grid_judge.judge_and_retry(
            image_path=out_path,
            regenerate_fn=regenerate,
            product_name=info.get("name", ""),
            product_form=info.get("product_form", ""),
            max_retries=3,
        )
        # 2026-09-23 修复: 检查 verdict.final_ok, 不通过则跳过 (避免假成功)
        if not verdict.get("final_ok"):
            print(f"  ❌ 段{seg_num} 失败 (3 次重生成后仍不通过): {out_path}", flush=True)
            print(f"     history: {verdict.get('history', [])[-1]}", flush=True)
            continue  # 跳过 register_grid, 不写 manifest
        proj.register_grid(seg_num, out_path)
        print(f"  ✓ 段{seg_num} OK: {out_path}")


def cmd_cells(args):
    """切分宫格图 -> 6 cell × 5 段 = 30 cell"""
    proj = ProjectState(args.project)
    print(f"=== 切分 cell ({args.project}) ===")

    from PIL import Image

    manifest = proj.load_manifest()
    grids = manifest.get("grids", {})
    if not grids:
        print(f"❌ 没有宫格图, 请先跑 grids")
        return

    for seg_key, grid_path in grids.items():
        seg_idx = int(seg_key.replace("seg", ""))
        if not os.path.exists(grid_path):
            print(f"  段 {seg_idx}: 缺失 {grid_path}")
            continue
        img = Image.open(grid_path)
        w, h = img.size
        cw, ch = w // 2, h // 3
        cell_paths = []
        for cell_num in range(1, 7):
            row, col = (cell_num - 1) // 2, (cell_num - 1) % 2
            cell = img.crop((col * cw, row * ch, (col + 1) * cw, (row + 1) * ch))
            out_path = proj.cell_path(seg_idx, cell_num)
            cell.save(out_path)
            cell_paths.append(out_path)
        proj.register_cells(seg_idx, cell_paths)
        print(f"  段 {seg_idx}: 6 cell ✓")


def cmd_workflows(args):
    """生成 5 段 H3 workflow (从项目读 cell 路径)"""
    proj = ProjectState(args.project)
    print(f"=== 生成 H3 workflow ({args.project}) ===")

    # 强 reload
    for m in ["llm_modules", "segment_to_grid", "grid_prompt_v6", "grid_judge", "build_segment_workflow"]:
        if m in sys.modules:
            del sys.modules[m]
    import build_segment_workflow as bsw

    beats = proj.get_storyboard()
    manifest = proj.load_manifest()
    cells = manifest.get("cells", {})

    info = proj.get_info()
    copy = proj.get_copy()
    storyboard = proj.get_storyboard()  # [{description, keyframe, lastframe, segment, cell_actions}, ...]
    seg_desc_map = {b.get("segment"): b.get("description", "") for b in storyboard}
    seg_cell_actions_map = {
        b.get("segment"): b.get("cell_actions") for b in storyboard
    }
    # 2026-09-23 新增: 模特性别 (用于 H3 原生 TTS 决定男声/女声)
    model_gender = info.get("model_gender", "neutral")

    # 2026-09-23 新增: 5 段配音 (优先用 voiceover_segments, 借鉴 yao 5 层口播结构)
    voiceover_segments = copy.get("voiceover_segments", [])
    if isinstance(voiceover_segments, list) and len(voiceover_segments) == 5:
        # LLM 显式生成的 5 段配音 (推荐)
        voiceovers = {i + 1: voiceover_segments[i] for i in range(5)}
        print(f"  ✓ 使用 voiceover_segments (5 段独立配音)")
    else:
        # fallback: 从 tagline/SP/CTA 拼 (老逻辑)
        voiceovers = {
            1: copy.get("tagline", "")[:30],
            2: copy.get("selling_points", [""])[0][:30] if len(copy.get("selling_points", [])) > 0 else "",
            3: copy.get("selling_points", ["", ""])[1][:30] if len(copy.get("selling_points", [])) > 1 else "",
            4: copy.get("selling_points", ["", "", ""])[2][:30] if len(copy.get("selling_points", [])) > 2 else "",
            5: copy.get("cta", "")[:30],
        }
        print(f"  ! fallback: 用 tagline/SP/CTA 拼 5 段配音")

    for seg_idx in range(1, 6):
        cell_paths = cells.get(f"seg{seg_idx}", [])
        if len(cell_paths) != 6:
            print(f"  段 {seg_idx}: cell 不全 ({len(cell_paths)}/6), 跳过")
            continue
        seg_desc = seg_desc_map.get(seg_idx, "")
        seg_cell_actions = seg_cell_actions_map.get(seg_idx)  # 2026-09-23 新增: 6 cell 显式动作
        seg_voiceover = voiceovers.get(seg_idx, "")  # 2026-09-23 新增: H3 原生配音
        # 2026-09-23 新增: 主播腔 emotion + audio_directive (从 voiceover_segments_full 拿)
        seg_voiceover_full = None
        vo_full_list = copy.get("voiceover_segments_full", [])
        if isinstance(vo_full_list, list) and len(vo_full_list) >= seg_idx:
            seg_voiceover_full = vo_full_list[seg_idx - 1]
        # 调用 build_segment_workflow 时, 传入项目专属 cell 路径 + product_info + segment_desc + cell_actions + voiceover + gender + voiceover_full
        nodes = bsw.build_segment_workflow_6cell(
            seg_idx, product_id=args.project, cell_paths=cell_paths,
            product_info=info, segment_desc=seg_desc,
            cell_actions=seg_cell_actions,  # 2026-09-23 新增
            voiceover_text=seg_voiceover,  # 2026-09-23 新增: H3 原生 TTS
            model_gender=model_gender,  # 2026-09-23 新增: 男声/女声
            voiceover_full=seg_voiceover_full,  # 2026-09-23 新增: 主播腔 emotion
        )
        wf_path = proj.workflow_path(seg_idx)
        json.dump(nodes, open(wf_path, "w"), indent=2)
        proj.register_workflow(seg_idx, wf_path)
        print(f"  段 {seg_idx}: {len(nodes)} 节点 → {wf_path} (cell_actions={len(seg_cell_actions) if seg_cell_actions else 0}, VO={len(seg_voiceover)}字, gender={model_gender}, emotion={seg_voiceover_full.get('emotion', '') if seg_voiceover_full else 'none'})")


def cmd_render(args):
    """提交 ComfyUI 渲染 5 段 H3"""
    proj = ProjectState(args.project)
    print(f"=== 渲染 H3 ({args.project}) ===")

    import urllib.request

    manifest = proj.load_manifest()
    workflows = manifest.get("workflows", {})

    comfyu_url = "http://127.0.0.1:6011/prompt" if is_server() else None
    if not comfyu_url:
        print("❌ 必须在 server 端运行 render")
        return

    for seg_idx in range(1, 6):
        wf_path = workflows.get(f"seg{seg_idx}")
        if not wf_path or not os.path.exists(wf_path):
            print(f"  段 {seg_idx}: workflow 缺失, 跳过")
            continue
        nodes = json.load(open(wf_path))
        payload = {"prompt": nodes}
        req = urllib.request.Request(comfyu_url,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                resp = json.loads(r.read().decode())
            pid = resp.get("prompt_id", "")
            print(f"  段 {seg_idx}: prompt_id={pid}")
        except Exception as e:
            print(f"  段 {seg_idx}: 提交失败 {e}")


def cmd_concat(args):
    """拼接 5 段 → 40s 完整视频"""
    proj = ProjectState(args.project)
    print(f"=== 拼接 ({args.project}) ===")

    import subprocess as sp
    manifest = proj.load_manifest()
    videos = manifest.get("videos", {})

    list_path = os.path.join(proj.videos_dir, "_concat.txt")
    with open(list_path, "w") as f:
        for seg_idx in range(1, 6):
            v = videos.get(f"seg{seg_idx}")
            if v and os.path.exists(v):
                f.write(f"file '{v}'\n")

    out_path = proj.final_video_path()
    cmd = f"ffmpeg -y -f concat -safe 0 -i {list_path} -c copy {out_path}"
    print(f"运行: {cmd}")
    result = sp.run(cmd, shell=True, capture_output=True, text=True, timeout=120)
    if result.returncode == 0 and os.path.exists(out_path):
        sz = os.path.getsize(out_path) // 1024 // 1024
        proj.register_final_video(out_path)
        print(f"✓ 完成: {out_path} ({sz}MB)")
    else:
        print(f"❌ 失败: {result.stderr[:300]}")


def cmd_list(args):
    """列出所有项目"""
    projs = list_projects()
    print(f"项目根: {get_project_root()}")
    print(f"项目数: {len(projs)}")
    for p in projs:
        print(f"  - {p}")


def main():
    parser = argparse.ArgumentParser(description="H3 项目级端到端入口")
    parser.add_argument("--project", "-p", required=True, help="项目名")
    sub = parser.add_subparsers(dest="cmd", required=True)

    # create
    p_create = sub.add_parser("create", help="创建项目 + 准备素材")
    p_create.add_argument("--product")
    p_create.add_argument("--person")
    p_create.add_argument("--product-text")

    # llm / grids / cells / workflows / render / concat
    sub.add_parser("llm", help="跑 LLM (识别+文案+5段分镜)")
    sub.add_parser("grids", help="跑灵炫宫格图 (带判官)")
    sub.add_parser("cells", help="切分宫格图 → 6 cell × 5 段")
    sub.add_parser("workflows", help="生成 5 段 H3 workflow")
    sub.add_parser("render", help="提交 ComfyUI 渲染 5 段")
    sub.add_parser("concat", help="拼接 5 段 → 40s 完整视频")
    sub.add_parser("list", help="列出所有项目")

    # all
    p_all = sub.add_parser("all", help="一键全跑 (llm→grids→cells→workflows→concat)")

    args = parser.parse_args()

    handlers = {
        "create": cmd_create,
        "llm": cmd_llm,
        "grids": cmd_grids,
        "cells": cmd_cells,
        "workflows": cmd_workflows,
        "render": cmd_render,
        "concat": cmd_concat,
        "list": cmd_list,
    }

    if args.cmd == "all":
        for cmd in ["llm", "grids", "cells", "workflows", "render", "concat"]:
            print(f"\n{'='*60}\n>>> {cmd}\n{'='*60}")
            handlers[cmd](args)
    else:
        handlers[args.cmd](args)


if __name__ == "__main__":
    main()
