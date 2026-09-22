#!/usr/bin/env python3
"""项目状态管理器 - 唯一 source of truth

设计原则:
1. **项目隔离**: 每个项目独立目录, 不读 input/ 根目录, 不写 /tmp
2. **串联一致性**: 文案/分镜/灵炫 prompt/H3 prompt/口播稿, 全部从同一 storyboard 派生
3. **manifest.json**: 记录所有产物路径 + 来源指针, 任何脚本读 manifest 就能找到产物

使用:
    from project_state import ProjectState
    proj = ProjectState("shoes_5seg_v5")
    storyboard = proj.get_storyboard()       # 单一 source of truth
    lingxuan_p = proj.build_lingxuan_prompt(storyboard[0])
    h3_p = proj.build_h3_prompt(storyboard[0])
    voiceover = proj.build_voiceover(storyboard[0])
"""
import os
import json
import re
import time
from pathlib import Path
from typing import Optional


# === 项目根目录 (本地 + server 统一) ===
PROJECT_ROOT_LOCAL = "/Users/admin/Desktop/h3p_projects"
PROJECT_ROOT_SERVER = "/root/autodl-tmp/h3p/projects"


def is_server() -> bool:
    """判断当前是 server 还是 local"""
    return os.path.exists("/root/autodl-tmp/h3p")


def get_project_root() -> str:
    """根据环境返回项目根目录"""
    return PROJECT_ROOT_SERVER if is_server() else PROJECT_ROOT_LOCAL


class ProjectState:
    """项目状态管理器 (单一 source of truth)"""

    def __init__(self, project_name: str, base_root: Optional[str] = None):
        self.name = project_name
        self.root = base_root or get_project_root()
        self.dir = os.path.join(self.root, project_name)

        # 标准子目录
        self.input_dir = os.path.join(self.dir, "input")
        self.grids_dir = os.path.join(self.dir, "output", "grids")
        self.cells_dir = os.path.join(self.dir, "output", "cells")
        self.workflow_dir = os.path.join(self.dir, "output", "workflow")
        self.videos_dir = os.path.join(self.dir, "output", "videos")
        self.manifest_path = os.path.join(self.dir, "manifest.json")

        self._ensure_dirs()

    def _ensure_dirs(self):
        """确保所有子目录存在"""
        for d in [self.dir, self.input_dir, self.grids_dir, self.cells_dir,
                  self.workflow_dir, self.videos_dir]:
            os.makedirs(d, exist_ok=True)

    # === Manifest 持久化 ===

    def load_manifest(self) -> dict:
        """加载 manifest.json (项目元数据)"""
        if not os.path.exists(self.manifest_path):
            return {
                "project_name": self.name,
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "storyboard": None,
                "grids": {},     # seg_N -> path
                "cells": {},     # seg_N_cell_N -> path
                "workflows": {}, # seg_N -> path
                "videos": {},    # seg_N -> path
                "final_video": None,
            }
        return json.load(open(self.manifest_path))

    def save_manifest(self, manifest: dict):
        """保存 manifest.json"""
        manifest["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        json.dump(manifest, open(self.manifest_path, "w"), ensure_ascii=False, indent=2)

    # === Storyboard 单一 source of truth ===

    def save_storyboard(self, storyboard: list[dict], copy: dict, info: dict):
        """保存分镜 (5 段 × 1 镜) + 文案 + 产品识别

        所有后续 prompt 都从这里派生, 不会脱节
        """
        manifest = self.load_manifest()
        manifest["storyboard"] = storyboard
        manifest["copy"] = copy
        manifest["info"] = info
        self.save_manifest(manifest)

    def get_storyboard(self) -> list[dict]:
        """获取分镜 (5 段 × 1 镜)"""
        m = self.load_manifest()
        sb = m.get("storyboard")
        if not sb:
            raise RuntimeError(f"项目 {self.name} 没有分镜, 请先调用 save_storyboard()")
        return sb

    def get_copy(self) -> dict:
        m = self.load_manifest()
        copy = m.get("copy")
        if not copy:
            raise RuntimeError(f"项目 {self.name} 没有文案, 请先调用 save_storyboard()")
        return copy

    def get_info(self) -> dict:
        m = self.load_manifest()
        info = m.get("info")
        if not info:
            raise RuntimeError(f"项目 {self.name} 没有产品识别, 请先调用 save_storyboard()")
        return info

    # === 三个 prompt 派生器 (串联一致性核心) ===

    def build_lingxuan_prompt(self, segment: dict, cell_count: int = 6) -> str:
        """从 storyboard segment 派生灵炫 6 cell 宫格图 prompt

        与 H3 prompt 共享同一核心动作描述, 不会脱节
        """
        desc = segment.get("description", "")
        keyframe = segment.get("keyframe", "")
        lastframe = segment.get("lastframe", "")
        seg_idx = segment.get("segment", 0)
        t0 = float((seg_idx - 1) * 8)
        t1 = float(seg_idx * 8)

        # 提取核心动作
        core = re.sub(r'^段\s*\d+[:：]?\s*', '', desc).strip()
        if len(core) > 80:
            core = core[:80] + "..."

        # 6 步连贯: SETUP -> TRIGGER -> BUILDUP -> CLIMAX -> DECAY -> RESULT
        step_beats = [
            {"description": keyframe or f"准备: 产品静置就位, 围绕核心: {core}",
             "time_range": [t0, t0 + 1.3]},
            {"description": f"启动: 第一个动作触发, 围绕核心: {core}",
             "time_range": [t0 + 1.3, t0 + 2.7]},
            {"description": f"中段1: 动作加速进行, 围绕核心: {core}",
             "time_range": [t0 + 2.7, t0 + 4.0]},
            {"description": f"高潮: 动作最强点, 卖点最强展示, 围绕核心: {core}",
             "time_range": [t0 + 4.0, t0 + 5.3]},
            {"description": f"衰减: 强度下降, 围绕核心: {core}",
             "time_range": [t0 + 5.3, t0 + 6.7]},
            {"description": lastframe or f"收尾: 动作结束, 显示最终结果, 围绕核心: {core}",
             "time_range": [t0 + 6.7, t1]},
        ]
        return self._build_grid_prompt_text(step_beats, cell_count)

    def _build_grid_prompt_text(self, beats: list[dict], cell_count: int) -> str:
        """调用 grid_prompt_v6.build_grid_prompt 构造完整 prompt"""
        from grid_prompt_v6 import build_grid_prompt
        info = self.get_info()
        return build_grid_prompt(info, beats, product_text="", cell_count=cell_count)

    def build_h3_prompt(self, segment: dict) -> str:
        """从 storyboard segment 派生 H3 prompt (6 cell AddGuide 中间帧描述)

        关键: cell 描述与灵炫 prompt 完全一致, 保证生成的 6 cell 跟 H3 中间帧一致
        """
        desc = segment.get("description", "")
        keyframe = segment.get("keyframe", "")
        lastframe = segment.get("lastframe", "")
        seg_idx = segment.get("segment", 0)

        # H3 用 6 个 AddGuide 中间帧描述 (与灵炫 6 cell 描述对齐)
        cells_desc = self.build_lingxuan_prompt(segment, cell_count=6)

        # H3 prompt 模板 (对齐官方 H3 6 段 YAML)
        h3_prompt = f"""[6 段 H3 中间帧分镜 - 段 {seg_idx}]

{keyframe or f"段{seg_idx} 镜头 0 (0.0s): " + desc[:100]}

[中间 5 锚点 - 与灵炫 6 cell 宫格图对应]
{cells_desc}

{lastframe or f"段{seg_idx} 镜头 6 (8.0s): " + desc[:100]}

产品形态: {self.get_info().get('product_form', '')}
产品颜色: {self.get_info().get('color', '')}
"""
        return h3_prompt

    def build_voiceover(self, segment: dict) -> str:
        """从 storyboard segment 派生口播稿

        用 copy.sp1/sp2/sp3 对应到段 2/3/4
        """
        copy = self.get_copy()
        seg_idx = segment.get("segment", 0)

        if seg_idx == 1:
            return f"{copy.get('tagline', '')} {copy.get('pain', '')}"
        elif seg_idx == 2:
            return copy.get("sp1_voiceover", copy.get("selling_points", [""])[0:1])
        elif seg_idx == 3:
            return copy.get("sp2_voiceover", "")
        elif seg_idx == 4:
            return copy.get("sp3_voiceover", "")
        elif seg_idx == 5:
            return copy.get("cta", "")
        return ""

    # === 产物路径注册 ===

    def register_grid(self, seg_idx: int, grid_path: str):
        """注册灵炫生成的宫格图路径"""
        m = self.load_manifest()
        m["grids"][f"seg{seg_idx}"] = grid_path
        self.save_manifest(m)

    def register_cells(self, seg_idx: int, cell_paths: list[str]):
        """注册切分后的 6 个 cell 路径"""
        m = self.load_manifest()
        m["cells"][f"seg{seg_idx}"] = cell_paths
        self.save_manifest(m)

    def register_workflow(self, seg_idx: int, workflow_path: str):
        m = self.load_manifest()
        m["workflows"][f"seg{seg_idx}"] = workflow_path
        self.save_manifest(m)

    def register_video(self, seg_idx: int, video_path: str):
        m = self.load_manifest()
        m["videos"][f"seg{seg_idx}"] = video_path
        self.save_manifest(m)

    def register_final_video(self, video_path: str):
        m = self.load_manifest()
        m["final_video"] = video_path
        self.save_manifest(m)

    # === 工具方法 ===

    def grid_path(self, seg_idx: int) -> str:
        """灵炫宫格图路径"""
        return os.path.join(self.grids_dir, f"seg{seg_idx}_grid.png")

    def cell_path(self, seg_idx: int, cell_num: int) -> str:
        """切分后 cell 路径"""
        return os.path.join(self.cells_dir, f"seg{seg_idx}_cell{cell_num}.png")

    def workflow_path(self, seg_idx: int) -> str:
        return os.path.join(self.workflow_dir, f"seg{seg_idx}_workflow.json")

    def video_path(self, seg_idx: int) -> str:
        return os.path.join(self.videos_dir, f"seg{seg_idx}.mp4")

    def final_video_path(self) -> str:
        return os.path.join(self.videos_dir, "final_40s.mp4")

    def input_product_path(self) -> str:
        return os.path.join(self.input_dir, "product.png")

    def input_person_path(self) -> str:
        return os.path.join(self.input_dir, "person.png")


def list_projects() -> list[str]:
    """列出所有项目"""
    root = get_project_root()
    if not os.path.exists(root):
        return []
    return sorted([d for d in os.listdir(root)
                   if os.path.isdir(os.path.join(root, d))
                   and not d.startswith("_")])
