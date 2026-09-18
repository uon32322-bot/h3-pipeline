#!/usr/bin/env python3
"""
judge_shot.py - 判官编排器

聚合 L0 + L1 + A 段判官结果，遵循 judge_config.yaml aggregation 规则：
- R 段或 W 段任一 no → 否决（structure）
- P 段 no 计数 >= 2 → 否决（perception）
- L0 任何 high-confidence no → 硬否决（technical）
- 全 na 不计入否决，但计入覆盖率
- 单条 na 比例 > 40% → 警告

首期策略：全部只告警，不否决（标定集未建）
GGUF 适配：路径自动支持 H3 自生成音频（mp4 内嵌）
"""
import json
import os
import sys
from typing import Optional

# 引入子判官
SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPTS_DIR)
import judge_l0  # noqa: E402
import judge_l1  # noqa: E402
import judge_audio  # noqa: E402


# ============= 投票聚合 =============
def aggregate_voting(items: list, voting_count: int = 3) -> dict:
    """对单条判项做 N 票投票（stub: 暂不调 VLM，单次结果直接使用）"""
    # 真实 L2 VLM 投票逻辑:
    # - 调 voting_count 次 VLM (不同 prompt 变体)
    # - majority vote (并列最高票判 na, 不判 yes)
    # 当前 stub: 单次结果 = 最终结果
    return items


def aggregate_results(l0_items: list, l1_items: list, audio_items: list) -> dict:
    """聚合所有判项，遵循 judge_config.yaml aggregation 规则

    Returns:
        {
            "pass": bool,
            "redraw": bool,       # True = 随机型失败可重抽
            "structural": bool,    # True = 结构型失败禁止重抽
            "type": str,            # "random" | "mixed" | "structural"
            "summary": {...},
            "details": [...],
            "na_ratio": float,
            "coverage_warning": bool,
        }
    """
    all_items = []
    for it in l0_items:
        it_copy = dict(it)
        it_copy["segment"] = "L0"
        all_items.append(it_copy)
    for it in l1_items:
        it_copy = dict(it)
        it_copy["segment"] = "L1"
        all_items.append(it_copy)
    for it in audio_items:
        it_copy = dict(it)
        it_copy["segment"] = "A"
        all_items.append(it_copy)

    # 统计
    no_items = [it for it in all_items if it.get("verdict") == "no" and it.get("confidence") == "high"]
    na_items = [it for it in all_items if it.get("verdict") == "na"]

    # L0 任何 high-no → 结构型失败
    l0_no = [it for it in no_items if it.get("segment") == "L0"]
    structural = len(l0_no) > 0

    # P 段 (perception, L1 子集) >= 2 个 no → 否决
    perception_no = [it for it in no_items if it.get("segment") == "L1"
                     and it.get("item_id", "").startswith(("L1-04", "L1-10", "L1-09", "L1-08"))]
    perception_reject = len(perception_no) >= 2

    passed = not structural and not perception_reject
    redraw = not structural  # 随机型失败可重抽

    na_ratio = len(na_items) / max(len(all_items), 1)
    coverage_warning = na_ratio > 0.4

    return {
        "pass": passed,
        "redraw": redraw,
        "structural": structural,
        "type": "structural" if structural else ("random" if not passed else "pass"),
        "summary": {
            "total": len(all_items),
            "yes": sum(1 for it in all_items if it.get("verdict") == "yes"),
            "no": len(no_items),
            "na": len(na_items),
            "structural_count": len(l0_no),
            "perception_no_count": len(perception_no),
        },
        "na_ratio": round(na_ratio, 3),
        "coverage_warning": coverage_warning,
        "details": all_items,
    }


# ============= 顶层封装 =============
def judge_segment(video_path: str,
                  declared_w: int = 768,
                  declared_h: int = 1344,
                  declared_frames: int = 192,
                  fps: int = 24,
                  declared_duration_s: float = 0.0,
                  anchor_frame_path: Optional[str] = None,
                  expected_text: str = "",
                  prev_video_path: Optional[str] = None,
                  gguf_mode: bool = True,
                  warn_only: bool = True) -> dict:
    """判一段视频的完整 L0+L1+A 套件

    Args:
        warn_only: True = 永远返回 pass=True（首期模式，等标定集建好再切硬否决）
    """
    # L0
    l0_items = judge_l0.run_l0(video_path, declared_w, declared_h, declared_frames, fps)

    # L1 (加载 anchor frame)
    import cv2
    anchor_frame = None
    if anchor_frame_path and os.path.exists(anchor_frame_path):
        anchor_frame = cv2.imread(anchor_frame_path)

    l1_items = judge_l1.run_l1(
        video_path, anchor_frame=anchor_frame, anchor_roi=anchor_frame,
        seg_prev_video=prev_video_path, expected_text=expected_text
    )

    # A
    audio_items = judge_audio.run_audio_judge(
        video_path, expected_text=expected_text,
        prev_video_path=prev_video_path,
        declared_duration_s=declared_duration_s
    )

    # 聚合
    result = aggregate_results(l0_items, l1_items, audio_items)

    if warn_only:
        result["pass"] = True
        result["warn_only"] = True
        result["warn_message"] = "首期只告警模式，标定集建好后切硬否决"

    return result


# ============= CLI =============
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: judge_shot.py <video_path> [declared_w] [declared_h] [declared_frames] [fps] [duration_s]", file=sys.stderr)
        sys.exit(1)

    video_path = sys.argv[1]
    w = int(sys.argv[2]) if len(sys.argv) > 2 else 768
    h = int(sys.argv[3]) if len(sys.argv) > 3 else 1344
    frames = int(sys.argv[4]) if len(sys.argv) > 4 else 192
    fps = int(sys.argv[5]) if len(sys.argv) > 5 else 24
    duration = float(sys.argv[6]) if len(sys.argv) > 6 else 0.0

    if not os.path.exists(video_path):
        print(f"ERROR: video not found: {video_path}", file=sys.stderr)
        sys.exit(2)

    result = judge_segment(
        video_path, w, h, frames, fps, duration,
        warn_only=True
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    sys.exit(0)
