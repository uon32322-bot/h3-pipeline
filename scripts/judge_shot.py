#!/usr/bin/env python3
"""
judge_shot.py - 判官编排器（L0 算术 + L1 本地 + A 音频 + L2 远端 VLM）

聚合四级判官结果，遵循 judge_config.yaml aggregation 规则：
- L0 任何 high-confidence no → 硬否决（technical）
- R 段任一 no（非 low）→ 否决（structure）
- W 段任一 no（非 low）→ 否决（structure）
- P 段 no 计数 >= 2 → 否决（perception）
- 全 na 不计入否决，但计入覆盖率
- 单条 na 比例 > 40% → 警告

首期策略：全部只告警，不否决（标定集未建）—— warn_only=True 时 pass 恒 True，
但 **raw_pass 保留真实判定**，禁止把「没判成」和「判过了」混为一谈。

GGUF 适配：路径自动支持 H3 自生成音频（mp4 内嵌）

⚠️ 2026-09-20 修复记录（审计发现）：
  ① L2 VLM 判官此前是 **stub**（aggregate_voting 直接 return items，零 VLM 调用）
     ⇒ 崩坏拦截层在生产链上从未运行。现接真实现 judge_l2.py。
  ② 原聚合器只认 L0/L1/A 三段，**R/W/P 的否决规则从未实现**（judge_config.yaml
     写了规则，代码里没有对应分支）⇒ 现补齐。
  ③ L2 失败必须**响亮告警**，绝不静默降级成 na（历史上曾出现「17 项全静默 na，
     报告却显示跑成功」）。
"""
import argparse
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

# L2 判官实现（可选依赖：缺文件时若未启用 L2 不影响 L0/L1/A 链路）
_judge_l2 = None
try:
    import judge_l2 as _judge_l2  # noqa: E402
except Exception as _e:  # pragma: no cover
    _judge_l2 = None
    _L2_IMPORT_ERR = _e


# ============= 投票聚合 =============
def aggregate_voting(items: list, voting_count: int = 3) -> dict:
    """兼容旧接口。

    ⚠️ 历史：本函数曾是 stub（单次结果=最终结果，无 VLM 调用）。
    真实 L2 投票现在由 judge_l2.l2() 内部完成（vote_sets 生成 votes 组**不同帧集**，
    因为同一 payload 重复调用会给出相反结论 —— 见 judge_l2.vote_sets 注释）。
    本函数保留为「把多票原始结果聚成单条」的纯函数，不再承担调用职责。
    """
    if not items:
        return {}
    cnt = {v: sum(1 for it in items if str(it.get("verdict", "na")).lower() == v)
           for v in ("yes", "no", "na")}
    top = max(cnt, key=lambda v: cnt[v])
    return {"verdict": top, "votes": cnt, "n": len(items)}


def _seg_of(item_id: str) -> str:
    """判项 ID → 段：L0/L1/A/L2 之外的 R/W/P 归 L2。"""
    if item_id.startswith("L0"):
        return "L0"
    if item_id.startswith("L1"):
        return "L1"
    if item_id.startswith("A-"):
        return "A"
    return "L2"


def aggregate_results(l0_items: list, l1_items: list, audio_items: list,
                      l2_items: Optional[list] = None,
                      l2_meta: Optional[dict] = None) -> dict:
    """聚合所有判项，遵循 judge_config.yaml aggregation 规则

    Returns:
        {
            "pass": bool,
            "raw_pass": bool,     # 未经 warn_only 修饰的真实判定
            "redraw": bool,       # True = 随机型失败可重抽
            "structural": bool,   # True = 结构型失败禁止重抽
            "type": str,          # "random" | "mixed" | "structural" | "pass"
            "summary": {...},
            "details": [...],
            "na_ratio": float,
            "coverage_warning": bool,
        }
    """
    all_items = []
    for it in l0_items:
        c = dict(it); c["segment"] = "L0"; all_items.append(c)
    for it in l1_items:
        c = dict(it); c["segment"] = "L1"; all_items.append(c)
    for it in audio_items:
        c = dict(it); c["segment"] = "A"; all_items.append(c)
    for it in (l2_items or []):
        c = dict(it)
        c["segment"] = _seg_of(str(it.get("id", "")))
        all_items.append(c)

    def no_of(seg=None, pref=None):
        out = []
        for it in all_items:
            if it.get("verdict") != "no":
                continue
            if seg and it.get("segment") != seg:
                continue
            if pref and not str(it.get("id", "")).startswith(pref):
                continue
            out.append(it)
        return out

    def strong_no(seg=None, pref=None):
        """非 low 置信度的 no（low = 投票并列，交人工，不否决）。"""
        return [it for it in no_of(seg, pref) if it.get("confidence") != "low"]

    # L0 任何 no（high 或 medium）→ 结构型失败
    l0_no = strong_no("L0")
    # R / W 段任一 no → structure 否决
    r_no = strong_no(pref="R-")
    w_no = strong_no(pref="W-")
    # P 段 no >= 2 → perception 否决
    p_no = strong_no(pref="P-")
    # L1 感知子集（原规则保留）
    l1_perc = strong_no("L1")
    l1_perc = [it for it in l1_perc
               if str(it.get("item_id", it.get("id", ""))).startswith(
                   ("L1-04", "L1-10", "L1-09", "L1-08"))]

    structural = bool(l0_no or r_no or w_no)
    perception_reject = (len(p_no) >= 2) or (len(l1_perc) >= 2)
    raw_pass = not structural and not perception_reject

    # 否决类型：结构型优先，其次感知型可重抽
    if structural:
        vtype = "structural"
    elif perception_reject:
        vtype = "random"
    else:
        vtype = "pass"

    na_items = [it for it in all_items if it.get("verdict") == "na"]
    na_ratio = len(na_items) / max(len(all_items), 1)
    coverage_warning = na_ratio > 0.4

    result = {
        "pass": raw_pass,
        "raw_pass": raw_pass,
        "redraw": not structural,          # 随机型失败可重抽；结构型禁止
        "structural": structural,
        "type": vtype,
        "summary": {
            "total": len(all_items),
            "yes": sum(1 for it in all_items if it.get("verdict") == "yes"),
            "no": len([it for it in all_items if it.get("verdict") == "no"]),
            "na": len(na_items),
            "structural_count": len(l0_no),
            "r_no_count": len(r_no),
            "w_no_count": len(w_no),
            "p_no_count": len(p_no),
            "perception_no_count": len(p_no) + len(l1_perc),
            "l2_items": len(l2_items or []),
        },
        "na_ratio": round(na_ratio, 3),
        "coverage_warning": coverage_warning,
        "details": all_items,
    }
    if l2_meta:
        result["l2_meta"] = l2_meta
    return result


# ============= L2 接线 =============
def run_l2(video_path: str, shot_prompt: str, segs: str = "RWP",
           votes: int = 3, n_frames: int = 6, workers: int = 4) -> tuple:
    """跑 L2 VLM 判官，返回 (items, meta)。

    ⚠️ 硬约束：
      · 绝不静默降级 —— 全部调用失败必须抛出/标记 l2_error，让上层看得见。
      · judges 不允许「模型不可达 ⇒ 全 na ⇒ 看起来跑成功」。
    """
    if _judge_l2 is None:
        raise RuntimeError("judge_l2 不可导入（%s）—— 无法运行 L2 判官" %
                           globals().get("_L2_IMPORT_ERR"))
    if not shot_prompt or not shot_prompt.strip():
        raise RuntimeError("L2 需要该镜的 prompt 原文作为判据来源（R 段），"
                           "但未提供 --prompt ⇒ 拒绝静默跳过")
    items = _judge_l2.l2(video_path, shot_prompt, segs,
                         n_frames=n_frames, workers=workers, votes=votes)
    model = getattr(_judge_l2, "VLM_MODEL", "?")
    errs = [it for it in items if it.get("verdict") == "na"
            and it.get("evidence", {}).get("value") == "error"]
    all_na = items and all(it.get("verdict") == "na" for it in items)
    meta = {"model": model, "items": len(items), "votes": votes,
            "n_frames": n_frames, "workers": workers, "error_items": len(errs)}
    if not items:
        raise RuntimeError("L2 返回 0 个判项 —— 判据清单为空或 segs 过滤掉了全部项")
    if all_na:
        meta["l2_error"] = "ALL_NA"
        sys.stderr.write(
            "\n" + "=" * 72 +
            "\n⛔ L2 判官【全部返回 na】—— 大概率是 VLM 端点/密钥/限流问题，"
            "\n   不是「片子没问题」。历史事故：17 个判项全静默 na，报告显示跑成功。"
            "\n   请检查：h3secrets.lk888_key() / api.lk888.ai 连通性 / 配额。"
            "\n" + "=" * 72 + "\n")
    return items, meta


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
                  warn_only: bool = True,
                  shot_prompt_path: Optional[str] = None,
                  l2_mode: str = "auto",
                  l2_votes: int = 3,
                  l2_nframes: int = 6,
                  l2_workers: int = 4) -> dict:
    """判一段视频的完整 L0+L1+A(+L2) 套件

    Args:
        warn_only: True = pass 恒 True（首期模式，等标定集建好再切硬否决）；
                   raw_pass 始终保留真实判定。
        l2_mode:   auto（有 prompt 就跑）/ on（必须跑，失败即报错）/ off（不跑）
    """
    # ---- L0 ----
    l0_items = judge_l0.run_l0(video_path, declared_w, declared_h, declared_frames, fps)

    # ---- L1 ----
    import cv2
    anchor_frame = None
    if anchor_frame_path and os.path.exists(anchor_frame_path):
        anchor_frame = cv2.imread(anchor_frame_path)

    l1_items = judge_l1.run_l1(
        video_path, anchor_frame=anchor_frame, anchor_roi=anchor_frame,
        seg_prev_video=prev_video_path, expected_text=expected_text
    )

    # ---- A ----
    audio_items = judge_audio.run_audio_judge(
        video_path, expected_text=expected_text,
        prev_video_path=prev_video_path,
        declared_duration_s=declared_duration_s
    )

    # ---- L2 ----
    l2_items, l2_meta = [], {}
    shot_prompt = ""
    if shot_prompt_path and os.path.exists(shot_prompt_path):
        shot_prompt = open(shot_prompt_path, encoding="utf-8").read()

    if l2_mode != "off":
        if l2_mode == "on" or (l2_mode == "auto" and shot_prompt.strip()):
            try:
                l2_items, l2_meta = run_l2(
                    video_path, shot_prompt, votes=l2_votes,
                    n_frames=l2_nframes, workers=l2_workers)
            except Exception as e:
                # 响亮告警：绝不静默当「没这项」
                sys.stderr.write("\n⚠️⚠️ L2 判官未运行：%s: %s\n" % (type(e).__name__, e))
                l2_meta = {"l2_error": "%s: %s" % (type(e).__name__, e)}
                if l2_mode == "on":
                    raise
        else:
            l2_meta = {"l2_skipped": "未提供 --prompt，L2 无判据来源（R 段）"}
            sys.stderr.write("\n⚠️ L2 已跳过：%s\n" % l2_meta["l2_skipped"])

    # ---- 聚合 ----
    result = aggregate_results(l0_items, l1_items, audio_items, l2_items, l2_meta)

    if warn_only:
        result["pass"] = True
        result["warn_only"] = True
        result["warn_message"] = ("首期只告警模式：pass 恒 True；真实判定见 raw_pass。"
                                  "标定集建好后切硬否决。")

    return result


# ============= CLI =============
def _parse_args(argv):
    ap = argparse.ArgumentParser(
        description="判官编排器：L0(算术) + L1(本地) + A(音频) + L2(远端 VLM)")
    ap.add_argument("video")
    ap.add_argument("declared_w", nargs="?", type=int, default=768)
    ap.add_argument("declared_h", nargs="?", type=int, default=1344)
    ap.add_argument("declared_frames", nargs="?", type=int, default=192)
    ap.add_argument("fps", nargs="?", type=int, default=24)
    ap.add_argument("duration", nargs="?", type=float, default=0.0)
    ap.add_argument("--prompt", default=None,
                    help="该镜的分镜/提示词文件（L2 的 R 段判据来源）")
    ap.add_argument("--anchor", default=None, help="锚定首帧图片（L1 用）")
    ap.add_argument("--prev", default=None, help="上一段视频（连续性判项）")
    ap.add_argument("--expected-text", default="", help="声明会出现的画面内文字")
    ap.add_argument("--l2", dest="l2_mode", default=os.environ.get("H3P_JUDGE_L2", "auto"),
                    choices=["auto", "on", "off"],
                    help="L2 开关：auto=有 prompt 就跑（默认）/ on=必须跑 / off=不跑")
    ap.add_argument("--l2-votes", type=int, default=int(os.environ.get("H3P_JUDGE_L2_VOTES", "3")))
    ap.add_argument("--l2-nframes", type=int, default=6)
    ap.add_argument("--l2-workers", type=int, default=int(os.environ.get("H3P_JUDGE_L2_WORKERS", "4")))
    ap.add_argument("--hard", action="store_true",
                    help="关闭 warn_only（切硬否决）；默认只告警")
    return ap.parse_args(argv)


if __name__ == "__main__":
    a = _parse_args(sys.argv[1:])
    if not os.path.exists(a.video):
        print(f"ERROR: video not found: {a.video}", file=sys.stderr)
        sys.exit(2)

    result = judge_segment(
        a.video, a.declared_w, a.declared_h, a.declared_frames, a.fps, a.duration,
        anchor_frame_path=a.anchor, expected_text=a.expected_text,
        prev_video_path=a.prev,
        warn_only=not a.hard,
        shot_prompt_path=a.prompt,
        l2_mode=a.l2_mode, l2_votes=a.l2_votes,
        l2_nframes=a.l2_nframes, l2_workers=a.l2_workers,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    sys.exit(0)
