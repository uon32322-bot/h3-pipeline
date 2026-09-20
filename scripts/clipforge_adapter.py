#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ClipForge → orchestrator `side_a` 适配器（P1 文字层桥）。

职责边界（严格遵守链路职责，越界即污染）：
  - 只做「字段映射 + 时长累加 + 运镜白名单归一 + P3 闸门预检」
  - **不做内容再创作**：不重写台词、不增删镜头、不合并镜头、不改卖点表述
  - ClipForge 的 Shot[] 是唯一内容来源；本文件只翻译，不创作

side_a 契约（orchestrator.py S1 期望）：
  {
    "product": {"name", "category", "selling_points", "fidelity_class"},
    "hook_type": str,
    "cta": {"text", "position", "urgency"},
    "shots": [{"idx": int, "line": str, "start": float, "end": float,
               "visual": str, "camera": str}]
  }

用法：
  python3 clipforge_adapter.py \
      --endpoint http://43.136.35.203:3000 \
      --name "降噪耳机" --desc "半入耳主动降噪，续航8小时" \
      --category tech --style pain_point --duration 30 \
      [--image p1.png] [--image p2.png] \
      --out side_a.json [--save-raw raw.json]

  python3 clipforge_adapter.py --selftest          # 离线自检（不打网络）
"""
from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

# ── P3 闸门阈值（与 orchestrator CFG 同源，改这里要同步改那边）─────────────
FPS = 24
CHARS_PER_SEC = 3.9          # 实测：中文口播 3.9 字/秒 → 镜长 × 3.9 = 台词字数预算
MIN_COVERAGE = 0.80          # 台词覆盖率下限（低于此值会出现空档）

# ── 运镜白名单归一（orchestrator CFG["action_whitelist"]）────────────────
CAMERA_WHITELIST = ["举起", "并排", "推近", "旋转", "开合", "滑入",
                    "光影流动", "静置", "特写平移"]

_CAMERA_RULES = [
    # 顺序即优先级：先匹配到的胜出
    (("推近", "推进", "推镜", "push in", "dolly in", "zoom in", "特写推"), "推近"),
    (("旋转", "环绕", "绕行", "orbit", "rotate", "arc shot"), "旋转"),
    (("开合", "打开", "开盖", "闭合", "open", "close up the lid"), "开合"),
    (("举手", "举起", "拿起", "抬", "hold up", "raise"), "举起"),
    (("并排", "陈列", "排列", "line up", "align"), "并排"),
    (("光影", "光扫", "光线流动", "light sweep", "light pass"), "光影流动"),
    (("平移", "横移", "滑入", "滑过", "pan", "truck", "dolly"), "特写平移"),
    (("静置", "静止", "固定", "定格", "static", "locked off"), "静置"),
]

# ── P3 闸门：手部动作链（ClipForge OUTPUT_FORMAT「禁四类写法」第 4 条）──
# 词表来自实测崩坏样本反推：镜1「单手抓着扶手，另一只手按着耳侧…拿起…推开盒盖」
# 若只收 拿起/按下 这类词，会漏检掉真实的 4 个手部动作 ⇒ 三只手拦不住。
_HAND_VERBS = [
    # 开合/操作类
    "拧开", "拧", "旋开", "开盖", "盖上", "打开", "推开", "掀起", "抽出",
    # 取放/持握类
    "取出", "拿出", "拿起", "放下", "放进", "放入", "握住", "握着", "手持",
    "抓着", "抓紧", "抓", "托", "举起", "竖起", "递", "接过", "戴上", "摘下", "系上",
    # 触控类
    "点击", "轻点", "轻触", "按下", "按着", "按住", "按压", "拨动", "滑动", "滑动",
    "触摸", "擦拭", "涂抹", "拍打", "打字", "敲击", "比划", "指向", "指着",
    # 物理结果类（同时命中 orchestrator 闸门② 的 forbidden_by_physics）
    "倒出", "倒水", "挤压", "撕", "拆", "翻转", "抽取", "拉出",
    # 多人/双手信号词（出现即说明「一拍多手」，是最强预警）
    "另一只手", "两只手", "双手", "双手同时",
]
# 一拍内允许的手部动词数上限（1 = 只保留一步主动作）
MAX_HAND_VERBS_PER_SHOT = 1


def _match_hand_verbs(text: str) -> list[str]:
    """匹配手部动作词；若短词被更长命中包含（如「拧」⊂「拧开」），只计最长的那一个。"""
    hits = [v for v in _HAND_VERBS if v in text]
    return sorted({h for h in hits if not any(h != o and h in o for o in hits)})

# ── P3 闸门：场所词（用于统计场景多样性）────────────────────────────────
_PLACES = [
    "地铁", "车厢", "办公室", "工位", "健身房", "跑步机", "街道", "马路", "路口",
    "咖啡馆", "餐厅", "厨房", "卧室", "客厅", "浴室", "阳台", "公园", "长椅",
    "校园", "教室", "图书馆", "商场", "专柜", "试衣间", "车内", "驾驶座",
    "飞机", "机场", "高铁", "站台", "电梯", "楼道", "天台", "海边", "泳池",
    "桌面", "书桌", "床头", "化妆台", "摄影棚", "影棚", "纯色背景", "白底",
]


# ══════════════════════════════════════════════════════════════════
# 基础工具
# ══════════════════════════════════════════════════════════════════
def img_to_data_uri(path: str) -> str:
    """本地图片 → data URI（ClipForge 的 imagePathToBase64 会原样透传 data:）。"""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(path)
    mime = mimetypes.guess_type(p.name)[0] or "image/png"
    return "data:%s;base64,%s" % (mime, base64.b64encode(p.read_bytes()).decode("ascii"))


def _post_json(url: str, payload: dict, timeout: int = 300) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def normalize_camera(raw: str) -> str:
    """ClipForge 的运镜描述 → orchestrator 白名单值。"""
    s = (raw or "").strip().lower()
    if not s:
        return "静置"
    if s in CAMERA_WHITELIST:
        return s
    for keys, val in _CAMERA_RULES:
        for k in keys:
            if k in s:
                return val
    return "静置"


def extract_places(*texts: str) -> list[str]:
    blob = " ".join(t or "" for t in texts)
    return [p for p in _PLACES if p in blob]


# ══════════════════════════════════════════════════════════════════
# P3 闸门预检（出片前，判字便宜、出片贵）
# ══════════════════════════════════════════════════════════════════
# ── 时长预算：镜次取舍（不改内容，只做取舍）────────────────────────────
# 结构优先级：越靠前越不可丢。hook（开场钩子）与 cta（转化落点）是带货结构的骨架。
# 实测校正：demo（卖点演示）是带货片的转化承载，绝不能先于 pain_point/social_proof 被丢
_STRUCT_PRIORITY = ["hook", "cta", "product_reveal", "demo", "pain_point", "social_proof"]
# 容忍上限：总时长允许到 请求值 × 此系数（用户验收口径 30–40s ⇒ 1.25 仍合格，取 1.15 更稳）
FIT_TOLERANCE = 1.15


def fit_duration(shots: list[dict], requested: float | None,
                 tol: float = FIT_TOLERANCE) -> tuple[list[dict], list[int]]:
    """按时长预算挑选镜次，**保留结构骨架**（返回 保留的 shots、被丢弃的原索引）。

    只做取舍、不做改写：
      - 首镜（hook）与末镜（cta）无条件保留
      - 其余按结构优先级从高到低补入，直到再补就超出预算
      - 被丢弃的镜次由调用方记入 _meta，绝不静默消失
    """
    if not requested or len(shots) < 2:
        return shots, []
    total = shots[-1]["end"]
    if total <= requested * tol:
        return shots, []

    keep = {0, len(shots) - 1}
    middle = sorted(range(1, len(shots) - 1),
                    key=lambda i: (_STRUCT_PRIORITY.index(shots[i].get("_cf_type", ""))
                                   if shots[i].get("_cf_type", "") in _STRUCT_PRIORITY else 99, i))
    cur = sum(shots[i]["end"] - shots[i]["start"] for i in keep)
    for i in middle:
        d = shots[i]["end"] - shots[i]["start"]
        if cur + d <= requested * tol:
            keep.add(i)
            cur += d
    dropped = [i for i in range(len(shots)) if i not in keep]
    return [shots[i] for i in sorted(keep)], dropped


def _reindex(shots: list[dict]) -> list[dict]:
    """取舍后重算 idx / start / end，消除因丢镜产生的空档。"""
    out, t = [], 0.0
    for k, sh in enumerate(shots, 1):
        dur = sh["end"] - sh["start"]
        sh = dict(sh)
        sh["idx"] = k
        sh["start"] = round(t, 3)
        sh["end"] = round(t + dur, 3)
        out.append(sh)
        t += dur
    return out


def gate_p3(shots: list[dict], requested_duration: float | None = None) -> dict:
    """对 side_a.shots 做出片前预检。

    返回 {"errors": [...], "warnings": [...], "stats": {...}}
    errors  = 必须回炉（结构性）；warnings = 只告警，让流程继续但记录在案。
    """
    errors, warnings = [], []
    hand_counts, place_sets, coverages = [], [], []

    for i, sh in enumerate(shots):
        idx = sh.get("idx", i + 1)
        visual = sh.get("visual", "")
        line = sh.get("line", "")
        dur = max(0.0, sh.get("end", 0.0) - sh.get("start", 0.0))

        # ── 闸门 A：一拍内手部动作链（三只手的直接根因）──
        hits = _match_hand_verbs(visual)
        hand_counts.append(len(hits))
        if len(hits) > MAX_HAND_VERBS_PER_SHOT:
            errors.append(
                "镜%d 一拍内 %d 个手部动作 %s（上限 %d）→ 只保留一步主动作，其余拆镜或交给台词"
                % (idx, len(hits), hits, MAX_HAND_VERBS_PER_SHOT))

        # ── 闸门 B：可验证物理结果（orchestrator 闸门② 同源）──
        for bad in ("涂开", "倒出", "拧开", "涂抹", "挤压出液"):
            if bad in visual:
                errors.append("镜%d 含可验证物理结果「%s」" % (idx, bad))
        for bad in ("双主体精确交互", "内部心理活动", "否定式动作", "一拍三步动作链"):
            if bad in visual:
                errors.append("镜%d 命中四类禁写「%s」" % (idx, bad))

        # ── 闸门 C：台词覆盖率（空档根因）──
        if dur > 0 and line:
            budget = dur * CHARS_PER_SEC
            cov = len(line) / budget if budget else 0
            coverages.append(cov)
            if cov < MIN_COVERAGE:
                warnings.append(
                    "镜%d 台词 %d 字 / 预算 %.0f 字（覆盖 %.0f%%）→ 可能有 %.1fs 空档"
                    % (idx, len(line), budget, cov * 100, dur * (1 - cov)))
            if cov > 1.25:
                # 🔴 2026-09-20 用户拍板：从 warning 升级为 **error**（强制重写而非放行）。
                # 依据：实测 4 镜里 3 镜超标 135–147% ⇒ 音频必然被截断，是「念不完 /
                # 有大段空档」的直接根因。记 error 会让「多候选择优」优先挑字数达标的
                # 候选（与 beats 同一套机制）；再配合 params.gate_p3_strict=True
                # 可硬阻断（但那会在全部候选都超标时整链失败，默认不开）。
                errors.append(
                    "镜%d 台词 %d 字 超出预算 %.0f 字（%.0f%%）→ 必被截断，须压缩到 "
                    "%d 字以内（%.1f 字/秒 × %.1fs）"
                    % (idx, len(line), budget, cov * 100,
                       int(budget), CHARS_PER_SEC, dur))

        # ── 闸门 D：画面内文字（字幕/价签/评价页会烧进成片）──
        for bad in ("字幕", "价格标签", "价签", "评价页", "评分", "评论区", "文字浮层",
                    "屏幕叠加", "叠加价格", "叠加文字", "价格信息", "赠品信息", "二维码",
                    "水印", "文字", "标语", "弹幕", "数字滚动", "LOGO", "logo",
                    "split screen", "split-screen", "side-by-side", "拼图", "九宫格"):
            if bad in visual.lower() or bad in visual:
                warnings.append("镜%d 疑似画面内文字/分屏「%s」→ 会被烧进成片" % (idx, bad))

        place_sets.append(set(extract_places(visual)))

    # ── 闸门 E：场景多样性（N 镜至少覆盖 ⌈N/2⌉ 个场景）──
    all_places = set()
    for s in place_sets:
        all_places |= s
    n = len(shots)
    need = (n + 1) // 2
    if n >= 2 and len(all_places) < need:
        district = len(all_places)
        warnings.append(
            "场景多样性不足：%d 镜只覆盖 %d 个场景（建议 ≥%d）→ 观众会觉得「一直在原地」"
            % (n, district, need))

    # ── 闸门 F：相邻两镜主体必须变（orchestrator 分镜三硬规则第 2 条）──
    for i in range(1, len(shots)):
        if shots[i].get("visual") == shots[i - 1].get("visual"):
            warnings.append("镜%d 与镜%d 画面描述完全相同 → 相邻镜必须至少变一项（景别/机位/场景）"
                            % (shots[i - 1].get("idx", i), shots[i].get("idx", i + 1)))

    # ── 闸门 H：结构化分格动作序列 beats（宫格模式的输入契约）──
    # 为什么是 error 而不是 warning：beats 缺失时宫格只能靠「兜底推导」，
    # 而推导是从**画面描述**里切，产品特写段切出来的是「瓶身是磨砂玻璃质感」
    # 这类静物碎片 ⇒ 宫格与脚本必然不符。
    # 记 error 会让「多候选择优」优先挑带 beats 的候选 ⇒ 形成**真实压力**，
    # 比在 prompt 里写一句「要求」有效（实测重启后仍 0/4 镜输出 beats）。
    _beats_stat = {}
    _no_beat = [s["idx"] for s in shots if not (s.get("beats") or [])]
    if _no_beat:
        errors.append("镜 %s 缺 beats（结构化分格动作序列）—— 宫格模式将退化为"
                      "兜底推导（从画面描述切，会得到静物碎片）" % _no_beat)
    else:
        _nb = [len(s.get("beats") or []) for s in shots]
        _beats_stat["beats_per_shot"] = _nb
        if min(_nb) < 2:
            warnings.append("镜 %s 的 beats 少于 2 条，宫格可能拆不出完整动作流"
                            % [i + 1 for i, v in enumerate(_nb) if v < 2])

    # ── 闸门 G：总时长与请求值偏差（结构层对齐检查）──
    if requested_duration and shots:
        total = shots[-1].get("end", 0.0)
        dev = abs(total - requested_duration) / requested_duration
        if dev > 0.20:
            warnings.append(
                "总时长 %.1fs 与请求 %.0fs 偏差 %.0f%% → ClipForge 的镜头规划与实际时长不同步"
                % (total, requested_duration, dev * 100))

    return {
        "errors": errors,
        "warnings": warnings,
        "stats": {
            **_beats_stat,
            "shots": n,
            "hand_verbs_max": max(hand_counts) if hand_counts else 0,
            "places": sorted(all_places),
            "places_count": len(all_places),
            "coverage_min": min(coverages) if coverages else None,
            "coverage_avg": (sum(coverages) / len(coverages)) if coverages else None,
        },
    }


# ══════════════════════════════════════════════════════════════════
# ClipForge 调用 + 转换
# ══════════════════════════════════════════════════════════════════
def fetch_clipforge(endpoint: str, *, name: str, desc: str, category: str = "other",
                    style: str = "pain_point", duration: int = 30,
                    images: list[str] | None = None,
                    extra_requirements: str | None = None,
                    timeout: int = 300) -> dict:
    """调 ClipForge /api/llm/script，返回原始 JSON。"""
    payload = {
        "productName": name,
        "productDescription": desc,
        "category": category,
        "styleType": style,
        "targetDuration": duration,
        "productImages": [img_to_data_uri(p) for p in (images or [])],
        "insightMode": False,          # 单机部署无转化数据，关掉飞轮以免注入噪声
    }
    if extra_requirements:
        payload["customRequirements"] = extra_requirements[:2000]
    return _post_json(endpoint.rstrip("/") + "/api/llm/script", payload, timeout=timeout)


def _pick_scripts(resp: dict) -> list[dict]:
    for key in ("scripts", "data", "results"):
        v = resp.get(key)
        if isinstance(v, list) and v:
            return v
        if isinstance(v, dict) and v.get("shots"):
            return [v]
    if resp.get("shots"):
        return [resp]
    return []


def to_side_a(resp: dict, *, product_name: str, category: str = "other",
              fidelity_class: str = "形态级",
              target_duration: float | None = None) -> dict:
    """ClipForge 响应 → orchestrator side_a 契约。只做映射，不改内容。"""
    scripts = _pick_scripts(resp)
    if not scripts:
        raise ValueError("ClipForge 响应里找不到 shots（keys=%s）" % list(resp.keys()))
    sc = scripts[0]
    raw_shots = sc.get("shots") or []
    if not raw_shots:
        raise ValueError("ClipForge 返回的脚本 shots 为空")

    shots, t = [], 0.0
    for i, s in enumerate(raw_shots, 1):
        dur = float(s.get("duration") or 0.0)
        if dur <= 0:
            dur = 3.0
        # 🔴 画面描述与视频 prompt 必须分家（实测教训：合并会把 r34l1sm / A static camera
        #    串进「图像层」的 TT Image2 提示词，直接毁掉首尾帧）：
        #      visual    → 只服务图像层（生成首尾帧），绝不含 H3 提示词
        #      h3_prompt → 只服务视频层（喂给 H3）
        visual = (s.get("description") or "").strip()
        h3_prompt = (s.get("prompt") or "").strip()
        shots.append({
            "idx": i,
            "line": (s.get("voiceover") or "").strip(),
            "start": round(t, 3),
            "end": round(t + dur, 3),
            "visual": visual,
            "h3_prompt": h3_prompt,
            # 结构化分格动作序列：每镜 2–3 条「谁+可见动作+对象+结束状态」，
            # 供宫格模式逐格指定（让灵炫照抄，而不是从散文里自己分格）
            "beats": [str(x).strip() for x in (
                s.get("beats") if isinstance(s.get("beats"), list)
                else ([s["beats"]] if s.get("beats") else [])) if str(x).strip()],
            "camera": normalize_camera(s.get("camera", "")),
            "_cf_type": s.get("type", ""),          # 保留 ClipForge 语义，供判官/统计用
            "_cf_visual_source": s.get("visualSource", ""),
        })
        t += dur

    # 时长预算：ClipForge 实测会给 6 镜 × 8s = 48s（请求 30s），必须收敛到验收区间
    shots, dropped = fit_duration(shots, target_duration)
    shots = _reindex(shots)
    if dropped:
        print("[adapter] ⏱ 时长收敛：丢 %d 镜（原索引 %s），保留 %d 镜 → %.1fs（请求 %.0fs）"
              % (len(dropped), [i + 1 for i in dropped], len(shots),
                 shots[-1]["end"] if shots else 0, target_duration))

    first = shots[0] if shots else {}
    cta_shot = next((s for s in reversed(shots) if s.get("_cf_type") == "cta"), shots[-1] if shots else {})
    hook_shot = next((s for s in shots if s.get("_cf_type") == "hook"), first)

    return {
        "product": {
            "name": product_name,
            "category": category,
            "selling_points": [],     # 卖点由 ClipForge 内部使用；此处留空，避免二次创作
            "fidelity_class": fidelity_class,
        },
        "hook_type": sc.get("styleType") or sc.get("style_type") or "",
        "cta": {
            "text": cta_shot.get("line", ""),
            "position": "末镜",
            "urgency": "",
        },
        "shots": shots,
        "_meta": {
            "source": "clipforge",
            "clipforge_title": sc.get("title", ""),
            "clipforge_total_duration": sc.get("totalDuration", t),
            "hook_line": hook_shot.get("line", ""),
            "target_duration": target_duration,
            "dropped_shot_idx": [i + 1 for i in dropped],
            "final_duration": shots[-1]["end"] if shots else 0,
        },
    }


# ══════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════
def _selftest() -> int:
    """离线自检：不打网络，用假响应验证映射 / 运镜归一 / 闸门。"""
    fake = {"scripts": [{
        "title": "测试", "styleType": "pain_point", "totalDuration": 8,
        "shots": [
            {"shotId": 1, "type": "hook", "duration": 4, "description": "地铁车厢里有人捂着耳朵",
             "camera": "缓慢推近特写", "voiceover": "每天挤地铁，耳机一戴世界就安静了，这句话是真的吗",
             "visualSource": "ai_generate"},
            {"shotId": 2, "type": "cta", "duration": 4,
             "description": "桌面上并排陈列充电盒，拧开盖子取出耳机，按下按钮",
             "camera": "静置", "voiceover": "链接放下面了", "visualSource": "ai_generate"},
        ]}]}
    sa = to_side_a(fake, product_name="降噪耳机", category="tech")
    ok = True

    def chk(name, cond, detail=""):
        nonlocal ok
        print(("  PASS  " if cond else "  FAIL  ") + name + (("  " + str(detail)) if detail else ""))
        ok = ok and cond

    chk("shots 数量保持 2", len(sa["shots"]) == 2)
    chk("时长累加 0→4→8", (sa["shots"][0]["start"], sa["shots"][1]["end"]) == (0.0, 8.0))
    chk("运镜归一「缓慢推近特写」→推近", sa["shots"][0]["camera"] == "推近", sa["shots"][0]["camera"])
    chk("运镜「静置」保持", sa["shots"][1]["camera"] == "静置")
    chk("台词原样保留（不创作）", sa["shots"][0]["line"].startswith("每天挤地铁"))

    g = gate_p3(sa["shots"])
    chk("闸门抓住手部动作链（拧开/取出/按下 = 3）", any("手部动作" in e for e in g["errors"]), g["errors"][:1])
    chk("闸门统计场景数 ≥1", g["stats"]["places_count"] >= 1, g["stats"]["places"])

    print()
    print("SELFTEST " + ("ALL PASS" if ok else "FAILED"))
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="ClipForge → side_a 适配器")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--endpoint", default=os.environ.get("CLIPFORGE_ENDPOINT", "http://43.136.35.203:3000"))
    ap.add_argument("--name", help="商品名")
    ap.add_argument("--desc", default="", help="商品描述")
    ap.add_argument("--category", default="other")
    ap.add_argument("--style", default="pain_point")
    ap.add_argument("--duration", type=int, default=30)
    ap.add_argument("--image", action="append", default=[], help="产品图，可多次")
    ap.add_argument("--requirements", default=None)
    ap.add_argument("--out", help="side_a.json 输出路径")
    ap.add_argument("--save-raw", help="原始响应另存")
    ap.add_argument("--raw-in", help="离线模式：直接读已保存的原始响应")
    # 注意：不要把「闸门否决」也用 exit 2 —— argparse 用法错误本身就是 2，会撞码误判
    ap.add_argument("--fidelity", default="形态级", choices=["形态级", "像素级", "unknown"])
    ap.add_argument("--strict", action="store_true",
                    help="闸门 errors 直接判失败（默认只告警 —— 新门禁须先用已验收成片标定）")
    a = ap.parse_args()

    if a.selftest:
        return _selftest()
    # unknown 是 orchestrator 在 S0 之前的默认值（S0 会定级），按形态级处理而不是报错
    fidelity = "形态级" if a.fidelity == "unknown" else a.fidelity
    if not a.name:
        ap.error("需要 --name（或用 --selftest）")

    if a.raw_in:
        resp = json.loads(Path(a.raw_in).read_text(encoding="utf-8"))
    else:
        resp = fetch_clipforge(a.endpoint, name=a.name, desc=a.desc, category=a.category,
                               style=a.style, duration=a.duration, images=a.image,
                               extra_requirements=a.requirements)
        if a.save_raw:
            Path(a.save_raw).write_text(json.dumps(resp, ensure_ascii=False, indent=2),
                                        encoding="utf-8")

    side_a = to_side_a(resp, product_name=a.name, category=a.category,
                       fidelity_class=fidelity, target_duration=float(a.duration))
    gate = gate_p3(side_a["shots"], requested_duration=float(a.duration))

    print("[adapter] %d 镜 / %s" % (len(side_a["shots"]), side_a["_meta"].get("clipforge_title", "")))
    print("[adapter] 运镜: %s" % [s["camera"] for s in side_a["shots"]])
    print("[adapter] 场景: %s (%d 个)" % (gate["stats"]["places"], gate["stats"]["places_count"]))
    for e in gate["errors"]:
        print("[闸门P3 ❌] " + e)
    for w in gate["warnings"]:
        print("[闸门P3 ⚠️] " + w)

    out = a.out or "side_a.json"
    Path(out).write_text(json.dumps(side_a, ensure_ascii=False, indent=2), encoding="utf-8")
    # 闸门评分落盘：供 orchestrator 做「多候选择优」（不是只看一次生成的运气）
    gate_out = str(Path(out).with_suffix("")) + ".gate.json"
    Path(gate_out).write_text(json.dumps(
        {"errors": gate["errors"], "warnings": gate["warnings"], "stats": gate["stats"],
         "score": [len(gate["errors"]), len(gate["warnings"])]},
        ensure_ascii=False, indent=2), encoding="utf-8")
    print("[adapter] → %s" % out)
    print("[adapter] 评分 errors=%d warnings=%d" % (len(gate["errors"]), len(gate["warnings"])))

    if gate["errors"]:
        print("[adapter] ⚠️ %d 条结构性 errors（默认只告警，未阻断）" % len(gate["errors"]))
        if a.strict:
            print("[adapter] ⛔ --strict 已开：回炉重写（exit 3；exit 2 保留给 argparse 用法错误）")
            return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
