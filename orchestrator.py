#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""端到端编排骨架 —— 上传产品图/模特图/文字 → 自动出带货视频（REPORT21）

链路（S0–S9）：
  S0 入库与分层   ★闸门① 保真分层（像素级/形态级）
  S1 文字层       ClipForge（Mac CPU，与 GPU 链并行）
  S2 适配转换     11 列节拍表 + 文案三分流
  S3 ★闸门②      动作白名单 + 四类禁写 + 因果链否决
  S3.5 分组       相邻镜合并为「一条片内多镜」，压 S（全链路最强杠杆，指数级）
  S4 图像层       灵炫 TT Image2（云端）：母版 → 锚定照×3 → 每段首尾帧 →（可选）中间锚点
  S5 ★闸门P       图层校验（产品保真/模特一致/规格/异文字/首尾同源/状态单调）
  S6 H3 生成      3090 官方 FL2VA 链路（唯一瓶颈，串行）
  S7 判官团       L0/L1 Mac CPU + L2 远端 VLM（异步，出 GPU）
  S8 后期合成     拼接 + 中文 TTS + 图文叠加 + 合规
  S9 交付归档     成片 + runs.jsonl + 种子资产库

设计约束（来自 REPORT21，破一条即失控）：
  ① 模型白名单（辉哥拍板）：生图只准 **TT Image2**；**灵炫/任何云端的视频模型坚决禁用**；
     文字优先 **GPT5.5 / GEM 3.7**；**没有授权的模型坚决不能用**（启动自检见 _check_model_policy）
  ② TT Image2 与判官都不落 3090（云端 / Mac CPU）
  ③ 形态级才允许 TT Image2 重绘产品；像素级只准真实像素合成
  ④ 同镜首尾帧必须同源派生；宫格整图绝不进 H3
  ⑤ 视频重抽必须换新 seed（同 seed 同 prompt = 逐位相同）；结构型禁止重抽
  ⑥ 图片重出上限 2 轮、视频抽卡上限 N=3，到顶报明确失败，禁止静默续跑
  ⑦ 存储白名单（辉哥要求）：服务器上**一切落数据盘 `/root/autodl-tmp/`**，系统盘不留本项目任何产物
     （脚本根 + 输出目录 + TMPDIR/pip/HF/torch/triton 缓存；启动自检见 _check_storage_policy）

用法：
  orchestrator.py --job job.json [--outdir out] [--dry-run]
  orchestrator.py --demo [--dry-run]          # 用内置样例跑通状态机
  orchestrator.py --job job.json --allow-system-disk   # ⚠️ 仅试跑，生产禁用

上线前务必：`source env_datadisk.sh`（把缓存重定向进数据盘）

依赖：
  scripts/ttimg.py               灵炫 TT Image2 接入（已存在）
  scripts/test_fl2v_official.py  H3 官方原生链路（已存在）
  环境变量 LK888_KEY             灵炫 API Key（不要写死在代码里）
"""
from __future__ import annotations

import re
import argparse
import concurrent.futures as cf
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
FFMPEG = os.environ.get("FFMPEG", "ffmpeg")
FFPROBE = os.environ.get("FFPROBE", "ffprobe")


def _resolve(name: str) -> Path:
    """同名脚本可能同时存在于 h3-pipeline/ 与 skill/scripts/，按序解析。"""
    for c in (HERE / name, HERE / "scripts" / name,
              Path.home() / ".workbuddy" / "skills" / "h3-fl2va-video-pipeline" / "scripts" / name):
        if c.exists():
            return c
    return HERE / name


TTIMG = _resolve("ttimg.py")
H3GEN = _resolve("test_fl2v_official.py")

# ─────────────────────────── 配置 ───────────────────────────

CFG = {
    # 时长网格：17k+5 帧 @24fps（官方公式），段长限制 2–4.5s
    "fps": 24,
    "seg_seconds": 4.5,          # 默认段长（一条片内多镜优先，S 越小越好）
    "seg_max_seconds": 15.0,     # 单段总时长上限（贴 §1.4「片内两镜 S=2，每段 15s」基准）
    "seg_max_shots": 8,          # 单段最多合并几镜（宽松：由时长主导切分）
    "megapixels": 0.98,          # 0.98MP = 1344x768（禁用 1.0MP）
    "steps": 8,                  # 官方 8 步 Turbo
    "aspect": "9:16",
    # 图片通道
    # ⚠️ 图片尺寸必须与 aspect 同比例（实测 H3 源码：首帧被 plain stretch 到画布、
    #    尾帧被 aspect-preserving cover-crop ⇒ 比例不符 = 首帧变形 / 尾帧丢构图）
    "img_size": "auto",          # auto = 由 aspect + megapixels 推导（保证与 H3 画布同尺寸）
    "img_retry": 1,              # 单张重试
    "img_timeout_s": 420,        # 单张轮询上限
    "img_batch_circuit_s": 300,  # 整批熔断：转圈超此值 → 降级
    "gate_p_rounds": 2,          # Gate-P 重出上限
    # ─── P5 跨段一致性（统一影调 + 禁入无关内容）───
    # 实测问题：各段各自造场景 ⇒ 段间亮度 97/187/94/205 剧烈跳变，拼接后忽明忽暗；
    #          且混入无关品牌包装（Milk 1L）与画面内英文文字。
    "series_tone": (
        "整支广告片的统一视觉基准：与其它镜头保持一致的曝光量与白平衡，"
        "影调明亮通透、中间调偏亮，不偏黄也不偏蓝，不出现忽明忽暗的跳变；"
        "画面内不得出现任何文字、字幕、价格、水印、logo、包装上的可读标识；"
        "除本品外不得出现其它品牌或商品的包装与标签"
    ),
    "tone_tolerance": 0.25,      # 段首帧亮度偏离全片中位数的容忍比例
    "tone_rounds": 2,            # 影调修正重出上限
    "series_tone_on_anchors": True,   # 母版/锚定照是否也套统一基调
    # 首尾帧「状态变化量」下限：FL2VA 靠两帧之间插值，差异过小 ⇒ 输出近乎静止
    # ⚠️ 阈值尚未用已验收成片标定 ⇒ 只告警（实测 P5 版主体区变化 14–31%）
    "min_state_change": 0.12,
    # ── 图像层门禁（2026-09-20，见文件内 _frame_mae / _is_grid_image 的说明）──
    # 首尾帧差异下限（MAE，0–255 尺度）。实测<15 的两段成片开头像静态图、整段几乎无动态。
    # FL2VA 从首帧插值到尾帧，差异太小就没有中间量 ⇒ 必成静态。
    "min_frame_mae": 20.0,
    "frame_mae_rounds": 2,
    # ── 宫格模式（09-20 拍板，实证可控）──
    # True 时 S4 改为「每段生成一张宫格图 → 切片 → 首尾格作锚、中间格作中间锚点」。
    # 默认 False：保守，不影响现有链路（准则⑩ 保守修复 > 架构改造）。
    "grid_mode": False,
    "grid_cols": 2,
    "grid_rows": 3,
    # 一致性门禁（B）：逐格核宫格画面 vs 脚本该格动作，不符则拒绝该宫格
    "grid_consistency_gate": True,
    "grid_min_align_pct": 70.0,
    "grid_judge_workers": 4,
    # 动作段 vs 静物段判别：静物段（无动作可拆）不走宫格，省一次灵炫出图
    "grid_require_action": True,
    "grid_min_action_verbs": 2,
    # 产品识别（VL）：让 ClipForge 拿到「真实产品」的文字描述，而非零信息
    "vl_product_analysis": True,
    # L1：动作段用手持锚（产品画进手里再喂）+ 显式锁定产品形态
    "handheld_anchor": True,
    "grid_style": "clean bright commercial kitchen product ad, soft natural daylight",        # 尾帧重出轮数上限（到顶只告警放行，不熔断）
    "grid_gate": True,            # 宫格/拼版图禁止进 H3（官方铁律）
    "s1_timeout_s": 900,         # S1 ClipForge 调用超时（实测 6 镜约 25s，留足余量）
    # S1 多候选择优：ClipForge 每次生成的质量有随机性（实测同一商品有时 5 场景有时 1 场景），
    # 跑 N 个候选后按闸门 P3 的 (errors, warnings) 选最优 —— 只挑不改，不创作内容。
    "s1_candidates": 2,
    # ─── P4 H3 prompt 组装（官方 FL2VA 三段式）───
    # 官方 skills/h3-prompt-writing/references/base-en.txt：
    #   FL2VA 第一行必须是对齐指令 + 一个空行；随后三字段顺序固定
    #   integrated_multimodal_description / overall_soundscape / non_diegetic_music
    # "fl2va3" = 组装为官方三段式；"free" = 直接用 ClipForge 原样 prompt（回退用）
    "h3_prompt_format": "fl2va3",
    "h3_soundscape_default": (
        "Natural indoor room tone with faint fabric movement and soft object handling sounds"
    ),
    "h3_music_default": "N/A",   # 暂无 BGM（ACE-Step 已部署但未接线，勿编造）
    "h3_voice_style": "says naturally",
    # 视频通道
    "gacha_n": 3,                # 抽卡上限 N<=3
    "comfy_port": 6011,          # 独立实例（不共用 6006）
    # ⚠️ ComfyUI 的 LoadImage 只接受 --input-directory 白名单内的路径；
    #    喂白名单外的路径会报 `Invalid image file` 并让整段生成失败（实测全片 4 段全灭）。
    "h3_input_dir": "/root/autodl-tmp/h3p/input",
    # ComfyUI 的 --output-directory（产物路径 = 该目录 / subfolder / filename）
    "comfy_output_dir": "/root/autodl-tmp/h3p/output",
    # ─── 提速旋钮（LoRA 步数）───
    # 实测：8step 版每步 95.7s × 8 = 12:47，加 160s 模型初始化 = 14:08/段。
    # 机器上同时存在 4step 版（1.96GB）；换它理论上把采样砍半，**须 A/B 验画质后再定**。
    # ⚠️ 名字必须与 ComfyUI 可见列表**逐字一致**，否则整段提交被拒（value_not_in_list）。
    #    实测可用（/root/autodl-tmp/h3p/models/loras/）只有这两个：
    "h3_lora": "minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors",          # 1.96GB
    "h3_lora_4step": "minimax_h3_fl2v_turbo_4step_v1.0_768p_comfyui_resized_avg_rank_21_bf16.safetensors",  # 298MB
    # 闸门② 动作白名单与禁写（与 shotlist_schema.json 的 gate2_merged_rules 同源）
    "action_whitelist": [
        "举起", "并排", "推近", "旋转", "开合", "滑入", "光影流动", "静置", "特写平移",
    ],
    "forbidden_by_physics": ["涂开", "倒出", "拧开", "涂抹", "挤压出液"],
    "forbidden_by_generability": [
        "双主体精确交互", "内部心理活动", "否定式动作", "一拍三步动作链",
    ],

    # ── 2026-09-20 新增：从「真实事故」反推的两张表 ──────────────────────
    # 事故：段2 的镜描述「放入微波炉、按下按钮、取出、放桌上」= 5 步动作链，
    #       8 秒内模型跳步压缩 ⇒ 拍出「微波炉门还没开、杯子已经在里面」。
    # 事故：段1/段3 末尾写「then the shot holds for about two seconds」+ 只描述
    #       镜头运动（static camera / slow orbiting camera）而主体无动作
    #       ⇒ 成片开头像静态图、洗碗机镜完全没有动态。
    "forbidden_static_phrases": [
        "holds for about", "hold for about", "stays still", "remains motionless",
        "does not move", "stands motionless", "保持静止", "静置不动",
        "then the shot holds", "画面定格", "静止不动",
    ],
    # 动作链步数上限：超过即判「一拍多步」→ 必须拆镜或只留一步主动作
    # 闸门② 否决后的退回重写次数（0/1=不重写）
    "gate2_rewrite_k": 3,
    "gate2_degrade_after_k": False,   # True=K 用尽后降级放行（默认 False：响亮失败）
    "max_action_steps": 2,
    # 官方 §4.1：关键帧任务从参考图推导风格。我们的锚定图是真实感实拍风 → Live-action
    "h3_style": "Live-action, cinematic",
    # LoRA 触发词（r34l1sm 等）：**默认空=不注入**（无对应 LoRA 时是孤儿 token，
    # 且会抢官方要求的「[Shot 1] 首位写风格」位置）。挂了 realism LoRA 再填。
    "h3_trigger_words": "",
    # true = 动作链超限直接否决；false(默认) = 仅告警（因为要修得动提示词源头，
    # 而 ClipForge 侧的单动作约束尚未落地 ⇒ 先可见、不阻断，等源头修好再开）
    "gate2_action_strict": False,
    # ⚠️ 不要用连接词计数：中文用逗号串联动作（实测段2「放入微波炉，按下按钮加热牛奶，
    #    取出后…放在桌上」连接词 0 个 ⇒ 会漏判）。改为**动作动词去重计数**。
    "action_verbs": [
        "放入", "放进", "取出", "拿起", "端起", "放下", "放在", "按下", "打开", "关上",
        "倒入", "倒出", "搅拌", "擦拭", "冲洗", "翻转", "加热", "拧开", "盖上", "装满",
        "舀出", "涂抹", "挤压", "递出", "举起", "拆开", "组装", "连接", "插入",
        "picks up", "puts in", "places", "takes out", "sets down", "presses", "opens",
        "closes", "pours", "stirs", "wipes", "washes", "lifts", "carries", "inserts",
        "unplugs", "plugs", "assembles", "twists",
    ],
    # 主体动作词：h3_prompt 若只有镜头运动而全无这些词 ⇒ 判「无主体动作」
    "subject_action_words": [
        "hand", "hands", "fingers", "picks", "places", "puts", "lifts", "pours",
        "stirs", "turns", "opens", "closes", "presses", "wipes", "washes", "holds",
        "carries", "sets", "gently moves", "steam rises", "liquid", "pours out",
        "手", "拿起", "放下", "倒入", "搅拌", "打开", "关上", "按下", "擦拭",
        "冲洗", "端起", "放进", "取出", "冒着", "流动", "缓缓",
    ],
}

# ─── 画布几何：图片尺寸由 aspect 推导（根治「两个字段各填各的」）───
# 与 ComfyUI `comfy_extras/nodes_resolution.py` 的 ResolutionSelector 同算法；
# 已真机反证：16:9 @0.98MP → 1344x768（=官方基准）、9:16 @0.98MP → 768x1344。
# ⚠️ 实测 H3 源码 nodes_minimax_h3.py：first_frame 走 plain stretch（直接拉伸）、
#    last_frame 走 aspect-preserving cover-crop（保比例中心裁剪）
#    ⇒ 图片与画布不同比例 = 首帧变形 / 尾帧丢构图，且**不报错**（静默毁片）。
_ASPECT_PAIRS = {
    "1:1": (1, 1), "2:3": (2, 3), "3:2": (3, 2), "3:4": (3, 4),
    "4:3": (4, 3), "9:16": (9, 16), "16:9": (16, 9), "21:9": (21, 9),
}


def derive_img_size(aspect_short, mp, multiple=32):
    """H3 画布 = aspect + megapixels 经 32 对齐后的像素；图片必须按【同尺寸】出图。"""
    w_r, h_r = _ASPECT_PAIRS[aspect_short]
    scale = (mp * 1024 * 1024 / (w_r * h_r)) ** 0.5
    return "%dx%d" % (round(w_r * scale / multiple) * multiple,
                      round(h_r * scale / multiple) * multiple)


if CFG["img_size"] == "auto":
    CFG["img_size"] = derive_img_size(CFG["aspect"], CFG["megapixels"])

# ─── 模型使用原则（辉哥拍板 2026-09-17 · 硬规范，不可绕过） ───
# ① 生图：只允许 TT Image2（灵炫）
# ② 视频：灵炫平台（及任何云端）的视频模型一律禁用 —— 视频只走本项目自持的 H3 权重
# ③ 文字：优先 GPT5.5 / GEM 3.7；没有授权的模型坚决不能用
MODEL_POLICY = {
    "image": {
        "allow": ["tt-image-2"],
        "note": "灵炫生图只准 TT Image2；换模型需辉哥重新授权",
    },
    "video": {
        "allow_local": ["minimax_h3_fl2va_pruned_int8_convrot.safetensors"],
        "allow_cloud": [],           # 空 = 云端视频模型全部禁用
        "note": "灵炫/任何云端的视频模型坚决不能用；视频只走自持 H3",
    },
    "text": {
        "prefer": ["gpt-5.5", "gem-3.7"],
        "require_authorized": True,  # 未授权的模型一律不用
        "note": "文案/改写/翻译优先 GPT5.5、GEM 3.7",
    },
}


def _check_model_policy() -> list[str]:
    """启动自检：确认链路里没有被塞进非白名单模型。返回告警清单。"""
    warns = []
    t = TTIMG.read_text(encoding="utf-8") if TTIMG.exists() else ""
    if 'MODEL = "tt-image-2"' not in t:
        warns.append("ttimg.py 的 MODEL 不是 tt-image-2 —— 违反生图白名单")
    for bad in ("tt-video", "seedance", "kling", "vidu", "hailuo", "runway", "veo"):
        if bad in t.lower() and "deny" not in t.lower():
            warns.append("ttimg.py 疑似引用视频模型关键字：%s" % bad)
    h = H3GEN.read_text(encoding="utf-8") if H3GEN.exists() else ""
    if "minimax_h3_fl2va" not in h:
        warns.append("H3 调用脚本未见 minimax_h3_fl2va 权重 —— 视频链路不在白名单内")
    return warns


# ─── 存储使用原则（辉哥要求 2026-09-17 · 硬规范） ───
# 服务器上「所有部署与数据全部落数据盘 /root/autodl-tmp/，系统盘不留本项目任何产物」。
# 系统盘（overlay /）容量小、不保存镜像就丢、扩容要走工单 —— 且曾跑到 100% 把任务卡死。
STORAGE_POLICY = {
    "server_datadisk": "/root/autodl-tmp",     # AutoDL 数据盘挂载点
    # 这些环境变量必须指进数据盘，否则缓存会悄悄堆到系统盘
    "must_redirect_env": (
        "TMPDIR", "PIP_CACHE_DIR", "HF_HOME", "HUGGINGFACE_HUB_CACHE",
        "TORCH_EXTENSIONS_DIR", "TRITON_CACHE_DIR", "XDG_CACHE_HOME",
    ),
}


def _on_server() -> bool:
    """是否跑在 GPU 服务器上（区别于本地 Mac 开发机）。"""
    return sys.platform.startswith("linux") and Path(STORAGE_POLICY["server_datadisk"]).is_dir()


def _check_storage_policy(outdir: Path) -> tuple[list[str], list[str]]:
    """启动自检：存储是否全部落在数据盘。返回（硬违规, 软告警）。

    硬违规 ⇒ 中止（除非显式 --allow-system-disk）；软告警 ⇒ 只提示，不拦。
    本地 Mac 上不做拦截 —— 这条规范只约束服务器。
    """
    hard: list[str] = []
    soft: list[str] = []
    if not _on_server():
        return hard, soft

    dd = Path(STORAGE_POLICY["server_datadisk"]).resolve()

    def _inside(p: Path) -> bool:
        try:
            p.resolve().relative_to(dd)
            return True
        except ValueError:
            return False

    # ① 项目根（脚本所在目录）必须在数据盘
    if not _inside(HERE):
        hard.append("项目根 %s 不在数据盘 %s —— 代码/脚本本身落在系统盘" % (HERE, dd))
    # ② 输出目录必须在数据盘
    if not _inside(outdir):
        hard.append("输出目录 %s 不在数据盘 %s —— 视频/图片/中间产物会写进系统盘" % (outdir, dd))
    # ③ 缓存类环境变量必须指向数据盘
    for ev in STORAGE_POLICY["must_redirect_env"]:
        v = os.environ.get(ev)
        if not v:
            soft.append("%s 未设置 —— 缓存可能落到系统盘（先 source env_datadisk.sh）" % ev)
        elif not _inside(Path(v)):
            soft.append("%s=%s 指向系统盘 —— 应改到 %s 下" % (ev, v, dd))
    # ④ 明显落在系统盘的临时目录
    if os.environ.get("TMPDIR") in ("", None) and Path("/tmp").is_dir():
        soft.append("/tmp 仍为系统盘默认 tmp —— source env_datadisk.sh 可改到数据盘")
    return hard, soft


# Gate-P 判项（REPORT21 §4.2）
GATE_P_ITEMS = [
    ("P-01", "产品保真"),
    ("P-02", "模特一致"),
    ("P-03", "规格合规"),
    ("P-04", "异文字与水印"),
    ("P-05", "首尾同源"),
    ("P-06", "状态单调"),
    ("P-07", "画布比例一致"),   # 首尾帧须与 H3 画布同比例（否则首帧拉伸 / 尾帧裁切，且不报错）
]


def _check_segments_ratio(segments, canvas, tol=0.02):
    """P-07 的可自动化部分：逐段校验首尾帧与画布同比例。
    返回 (是否全绿, 问题清单)。图片不存在时跳过（不误报）。"""
    try:
        from PIL import Image
    except Exception as _pe:
        # 🔴 不得把「没校验」当「通过」：P-07 是唯一 100% 可自动化、无需判官的
        #    静默毁片防线（首帧被 plain stretch 拉伸、尾帧被 cover-crop 裁切，
        #    H3 全程不报错）。服务器上少一个 Pillow 就全线失效，而日志只会显示正常。
        return False, ["PIL 不可用（%s）⇒ P-07 无法执行，视为**未通过**；装 Pillow 后重跑" % _pe]
    tw, th = (int(v) for v in canvas.split("x"))
    bad = []
    for s in segments:
        for tag, p in (("首帧", s.first), ("尾帧", s.last)):
            if not p or not os.path.exists(p):
                continue
            try:
                w, h = Image.open(p).size
            except Exception as e:
                bad.append("段%d %s 不可读：%s" % (s.idx, tag, e))
                continue
            dev = abs(w / h - tw / th) / (tw / th)
            if dev > tol:
                bad.append("段%d %s = %dx%d（偏差 %.1f%%）" % (s.idx, tag, w, h, dev * 100))
    return (not bad), bad


# ─────────────────────────── 数据模型 ───────────────────────────

@dataclass
class Job:
    product_images: list[str] = field(default_factory=list)
    model_images: list[str] = field(default_factory=list)
    product_text: str = ""
    params: dict = field(default_factory=dict)
    # 运行期
    job_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    fidelity_class: str = "unknown"      # 像素级 | 形态级
    created: float = field(default_factory=time.time)

    @staticmethod
    def load(path: str) -> "Job":
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        known = {k: d.get(k) for k in ("product_images", "model_images", "product_text", "params") if k in d}
        return Job(**known)


@dataclass
class Segment:
    idx: int
    seconds: float
    prompt: str                   # 图像层叙述（喂 TT Image2 生成首尾帧）
    h3_prompt: str = ""           # 视频层提示词（喂 H3），与 prompt 分家
    first: str = ""
    last: str = ""
    mids: list[tuple[str, float]] = field(default_factory=list)
    # 结构化「分格动作序列」：每格一条「谁 + 可见动作 + 对什么对象 + 该格结束状态」。
    # 这是宫格模式**准确性/一致性**的根：由脚本直接指定每格画什么，
    # 而不是把一段散文丢给灵炫让它自己分格（那样宫格与脚本必不一致）。
    beats: list[str] = field(default_factory=list)
    video: str = ""
    seed: int = 0
    attempts: int = 0
    frozen: bool = False
    verdict: dict = field(default_factory=dict)


# ─────────────────────────── 工具 ───────────────────────────

def snap_frames(seconds: float, fps: int = 24) -> int:
    """官方公式：max(5,round(a*24)) + (5 - (max(5,round(a*24)) % 17)) % 17"""
    f = int(max(5, round(seconds * fps)))
    return f + (5 - (f % 17)) % 17


def parse_h3_output(stdout: str, comfy_out, prefix: str, not_before: float = 0.0):
    """从 test_fl2v_official 的 stdout 解析出产物【绝对路径】。

    ⚠️ 它打印的是 `OUTPUT <key> <subfolder> <filename>`（4 段）；
    旧实现只取最后一个 token（纯文件名）⇒ Path(...).exists() 恒为 False
    ⇒ 每段白跑十几分钟后被判「未产出」重抽（实测踩到，见 tests T7）。
    """
    out = None
    for line in (stdout or "").splitlines():
        if not line.startswith("OUTPUT "):
            continue
        parts = line.split()
        cand = (Path(comfy_out) / parts[2] / parts[3]) if len(parts) >= 4 \
            else (Path(comfy_out) / parts[-1])
        if cand.exists():
            out = str(cand)
            break
    if out is None:
        stamp = Path(prefix).name
        # 🔴 2026-09-20 修复：兜底必须限定「本次提交之后」产出。
        #    原实现按 mtime 取最新且无时间界，而 comfy_out 是跨 job 共享目录、
        #    前缀只含段号 ⇒ 本次提交未产出任何文件时，会把上一次尝试、甚至上一个
        #    job 留下的同名旧视频当成本轮产物 ⇒ seed 与画质脱钩、抽卡经验被污染。
        cands = [c for c in Path(comfy_out).rglob(stamp + "*.mp4")
                 if c.is_file() and c.stat().st_mtime >= not_before]
        if cands:
            cands.sort(key=lambda q: q.stat().st_mtime, reverse=True)
            out = str(cands[0])
        else:
            log_once = Path(comfy_out).rglob(stamp + "*.mp4")
            if any(True for _ in log_once):
                print("   ⚠️ 兜底发现同名旧产物但 mtime 早于本次提交（%s）⇒ 判为未产出，拒绝旧货"
                      % stamp)
    return out



# ───────────────────────── 运动增强（2026-09-20）─────────────────────────
# 由来：夜审实测确认三类「画面不动」的成因，均在 prompt 层可干预：
#   ① 静止/定格写法（"then the shot holds for about two seconds"）→ 直接删
#   ② 只有镜头运动、无主体动作（"镜头缓慢环绕马克杯"）⇒ 成片就是一张会平移的
#      静态图（用户实测："洗碗机冲洗的画面只是静态图，没有动态"）
#   ③ FL2VA 架构固有：第一帧就是锚定图，模型从静止起步（用户实测："每段视频
#      的开始帧都是静态图"）—— 只能靠「立即起势」措辞缓解，无法根除
_STATIC_RE = [
    "then the shot holds for about two seconds", "then the shot holds",
    "the shot holds for about two seconds", "holds for about two seconds",
    "and holds for about two seconds", "stays still", "remains motionless",
    "保持静止", "画面定格", "静止不动",
]
_CAM_ONLY_HINT = ("镜头", "camera", "环绕", "orbiting", "推进", "pushes in",
                  "横移", "tracking shot", "pans", "static")


# ═══════════════════ 图像层门禁（2026-09-20）═══════════════════
# 由来：用户实测反馈 + 第一手量化，两条都必须**在进 GPU 之前**拦掉。
#
# 门禁① 首尾帧差异（FL2VA 的命门）
#   官方 §3.2：FL2VA 描述的是「首帧到尾帧的路径」，模型从首帧插值到尾帧。
#   实测三段首尾帧 MAE 只有 4.66 / 7.16 / 12.09（0–255 尺度）——
#   几乎相同 ⇒ 中间没有可插值的变化 ⇒ 成片必然趋近静止。
#   这正是用户反馈的「每段视频的开始帧都是静态图」「洗碗机镜只是静态图」的首因
#   （此前误判成「FL2VA 架构固有，无法根除」——错，锚帧差异是可修的）。
#
# 门禁② 宫格/拼版
#   官方 minimalist-product-ad-generator/SKILL.md 铁律逐字：
#     "Do not create grid layouts, split screens, collage boards, framed panels,
#      product walls, or storyboard sheets."
#   机制（官方原话）："video models may reproduce the panel layout"
#   ⇒ H3 会把宫格版式**复制进成片**（拍出分屏/四宫格/画框/产品墙），
#     也是「三只手 / 凭空多出手机」那类崩坏的机制之一。宫格图禁止进 H3。


def _frame_mae(p1, p2, w: int = 256) -> float:
    """两帧差异 MAE（0=完全相同；越大=可插值变化越多）。

    测不了时返回 999（**保守放行，不误杀** —— 会杀段的判定必须比放行更保守）。
    """
    try:
        import numpy as np
        from PIL import Image
        a = Image.open(p1).convert("RGB")
        b = Image.open(p2).convert("RGB")
        if a.size != b.size:
            b = b.resize(a.size)
        h = max(1, int(w * a.size[1] / max(a.size[0], 1)))
        A = np.asarray(a.resize((w, h)), dtype=np.float32)
        B = np.asarray(b.resize((w, h)), dtype=np.float32)
        return float(np.abs(A - B).mean())
    except Exception:
        return 999.0


def _is_grid_image(p, std_thr: float = 5.0, contrast_thr: float = 30.0,
                   min_span: float = 0.22) -> bool:
    """宫格/拼版检测：找「贯穿整幅、近乎同色、且与画面中位亮度明显不同」的格线。

    判据（2026-09-20 v2；v1 曾在真实图上误判 —— 洗碗机不锈钢篮架被当格线，
    而误判会导致整条链路熔断，代价极高）：
      · 缩到 256 长边；逐行/逐列求 std 与 mean
      · 格线 = std < std_thr（整行近乎同色）+ |mean − 中位亮度| > contrast_thr
      · **一条线不算宫格**：(横≥2) 或 (纵≥2) 或 (横≥1 且 纵≥1)
      · 被线切出的每格边长须 ≥ min_span × 该维度（排除细线/金属丝/纹理）
    ⇒ 单一连续画面（含强横竖结构如金属篮架）不触发；真正的 2×2 / 1×3 宫格必触发。

    测不了时返回 False（**保守放行，不误杀** —— 会杀段的判定必须比放行更保守）。
    """
    try:
        import numpy as np
        from PIL import Image
        im = Image.open(p).convert("L")
        w, h = im.size
        if w >= h:
            nw, nh = 256, max(4, int(256 * h / max(w, 1)))
        else:
            nw, nh = max(4, int(256 * w / max(h, 1))), 256
        a = np.asarray(im.resize((nw, nh)), dtype=np.float32)

        def lines(mat):
            s_ = mat.std(axis=1)
            m_ = mat.mean(axis=1)
            med = float(np.median(m_))
            hit = [i for i in range(2, len(s_) - 2)
                   if s_[i] < std_thr and abs(m_[i] - med) > contrast_thr]
            groups = []
            for i in hit:
                if groups and i - groups[-1][-1] <= 2:
                    groups[-1].append(i)
                else:
                    groups.append([i])
            pos = [g[len(g) // 2] for g in groups]
            n = len(s_)
            cuts = [0] + pos + [n - 1]
            segs = [cuts[k + 1] - cuts[k] for k in range(len(cuts) - 1)]
            if segs and min(segs) < min_span * n:
                return 0
            return len(pos)

        nh_ = lines(a)
        nv_ = lines(a.T)
        return (nh_ >= 2) or (nv_ >= 2) or (nh_ >= 1 and nv_ >= 1)
    except Exception:
        return False

# ═══════════════ 宫格模式（2026-09-20 用户拍板，已实证）═══════════════
# 一张宫格图承载整段动作：格1=首帧、格N=尾帧、中间格=中间锚点。
# 实证 GRID6（6 格 / 8s / 4 中间锚点）：帧间差异均值 17.09、近静止帧 0%
# （对照无锚 10%）；视频在锚点秒的帧与切片原图 MAE 仅 6.6–13.9
# ⇒ 模型确实逐个穿过锚点 ⇒ 动作可控（非模型自编）。
# 🔴 红线：宫格**原图绝不进 H3**（官方：video models may reproduce the panel
#   layout ⇒ 分屏/四宫格/画框被画进成片）。必须切片 + 裁缝 + 统一比例。
BEAT_TPL = "第{i}格：{desc}"


def derive_beats(prompt: str, n: int) -> list:
    """从自由叙述推导 N 格动作序列（**兜底**）。

    理想路径是脚本/适配器直接给结构化 beats（`Segment.beats`）；只有缺失时才回退到这里。
    按中文分句（逗号/句号/分号）切分后均匀取样，保证「每格有独立的一条动作描述」。
    """
    txt = (prompt or "").strip()
    if not txt or n <= 0:
        return []
    # 🔴 先剥掉结构标记：seg.prompt 是**组装后的多镜文本**，含 [Shot N] / At MM:SS.mmm，
    #    直接按逗号切会切出「暖光特写 [Shot 2] At 00:03.042 产品静置」这种垃圾
    #    （实测于 dry 跑）。剥净后剩下的才是真动作描述。
    txt = re.sub(r"\[\s*Shot\s*\d+\s*\]", "，", txt)
    txt = re.sub(r"At\s+\d{1,2}:\d{2}\.\d{2,3}", "，", txt)
    txt = re.sub(r"\[Chinese\][^，,。;；]*", "，", txt)   # 台词不进画面动作
    txt = re.sub(r"\s+", " ", txt).strip()
    parts = [x.strip() for x in re.split(r"[，,。；;]+", txt) if x.strip()]
    if not parts:
        return [txt] * n
    if len(parts) >= n:
        idx = [round(i * (len(parts) - 1) / (n - 1)) for i in range(n)] if n > 1 else [0]
        return [parts[i] for i in idx]
    return [parts[i] if i < len(parts) else parts[-1] for i in range(n)]


def beat_lines(beats: list, cells: int, fallback: str = "") -> str:
    """渲染「每一格画什么」的逐行指定。beats 不足的位置用 fallback 兜底。"""
    out = []
    for i in range(1, cells + 1):
        d = beats[i - 1] if i - 1 < len(beats) and beats[i - 1] else (fallback or "接上一格的中间状态")
        out.append(BEAT_TPL.format(i=i, desc=d))
    return "\n".join(out)


GRID_PROMPT_TPL = (
    "一张 {rows} 行 {cols} 列的连续动作分解图（storyboard grid），共 {cells} 格，"
    "格子之间是清晰的白色细缝，每格是一幅独立完整的画面，格子外没有任何画框、边框或文字。"
    "整张图描述同一个人在同一场景中完成一段连续动作。"
    "格子的先后顺序就是动作的先后顺序，"
    "每一格画什么**必须严格照下面逐格指定**，不得自行改动顺序或省略任何一格：\n"
    "{beat_lines}\n"
    "硬要求：① 每一格里必须始终是同一个人（同一张脸、同一发型）、同一套服装、同一个场景、"
    "同一件产品、同一光线方向与色调；② 按从左到右、从上到下的顺序，动作依次推进，"
    "相邻格之间是连贯的中间状态；③ 每格必须有清晰可见的身体动作或手部动作，"
    "禁止出现只有镜头变化、主体静止不动的格子；④ 每格构图完整，人物与产品不被格子边缘切除；"
    "⑤ 画面里不要出现任何文字、字幕、水印、logo。整体风格：{style}。"
    "⑥ 每一格都必须能独立看出它对应的那一步动作，相邻格之间是连贯的中间状态。"
)


def _grid_mid_anchors(cells: list, seconds: float) -> list:
    """把中间格均匀映射到片长时间轴（不含首尾格）：间隔 = seconds/(n+1)。"""
    n = len(cells)
    if n <= 0:
        return []
    return [(str(c), round(seconds * i / (n + 1), 2)) for i, c in enumerate(cells, 1)]


def _slice_grid(src, outdir, tag: str, cols: int, rows: int,
                target_ratio: float = 9 / 16, margin: int = 10) -> list:
    """宫格图 → 独立完整画面：格线检测 → 逐格裁切 → 裁白缝 → 统一比例。

    返回按阅读顺序（左→右、上→下）的 Path 列表；任何质检不过即返回 []，
    **绝不把坏切片喂给 H3**。质检：格数==cols*rows / 比例==target_ratio /
    每格非「整格同色」（空白格）。
    """
    try:
        import numpy as np
        from PIL import Image
        src, outdir = Path(src), Path(outdir)
        outdir.mkdir(parents=True, exist_ok=True)
        im = Image.open(src).convert("RGB")
        W, H = im.size
        a = np.asarray(im.convert("L"), dtype=np.float32)

        def _lines(mat, axis_len, std_thr=6.0, contrast_thr=18.0):
            sd, mn = mat.std(axis=1), mat.mean(axis=1)
            med = float(np.median(mn))
            hit = [i for i in range(3, axis_len - 3)
                   if sd[i] < std_thr and abs(mn[i] - med) > contrast_thr]
            g = []
            for i in hit:
                if g and i - g[-1][-1] <= 3:
                    g[-1].append(i)
                else:
                    g.append([i])
            out = [int(np.mean(x)) for x in g]
            # 去伪线：① 贴边的（画布边缘的高对比行不是格线，实测 y=3 被误当格线）
            #          ② 与相邻格线过近的（同一格线被拆成两条）
            lo, hi, gap = 0.02 * axis_len, 0.98 * axis_len, 0.02 * axis_len
            out = [x for x in out if lo <= x <= hi]
            merged = []
            for x in out:
                if merged and x - merged[-1] < gap:
                    merged[-1] = (merged[-1] + x) // 2
                else:
                    merged.append(x)
            return merged

        def _bounds(gut, n, total):
            out = []
            for e in [round(total * i / n) for i in range(1, n)]:
                near = [x for x in gut if abs(x - e) < total * 0.12]
                out.append(int(np.mean(near)) if near else e)
            return [0] + out + [total]

        gh, gv = _lines(a, H), _lines(a.T, W)
        # 🔴 格线数必须与请求的行列吻合 —— 否则说明宫格图不是我们要的布局，
        # 盲目等分会**切穿画面内容**（实测：2×3 的图请求 3×3 会切出 9 格垃圾）。
        # 必须**精确吻合**：格线少了 ⇒ 会切穿画面内容；格线多了 ⇒ 布局不是我们要的。
        # （实测「≥n-1」不够严：2×3 的图请求 2×2 也能过，切出来每格含 1.5 格内容）
        if len(gv) != cols - 1 or len(gh) != rows - 1:
            log("S4", "⚠️ 宫格线与请求不符：请求 %d×%d（需 %d 竖/%d 横格线），"
                "实测竖=%d 横=%d ⇒ 拒绝切片（不把坏切片喂给 H3）"
                % (cols, rows, cols - 1, rows - 1, len(gv), len(gh)))
            return []
        ys = _bounds(gh, rows, H)
        xs = _bounds(gv, cols, W)
        cells = []
        for r in range(rows):
            for c in range(cols):
                box = (xs[c] + margin, ys[r] + margin,
                       xs[c + 1] - margin, ys[r + 1] - margin)
                if box[2] - box[0] < 32 or box[3] - box[1] < 32:
                    return []
                ci = im.crop(box)
                w, h = ci.size
                if w / h > target_ratio:
                    nw = int(round(h * target_ratio))
                    x0 = (w - nw) // 2
                    ci = ci.crop((x0, 0, x0 + nw, h))
                else:
                    nh = int(round(w / target_ratio))
                    y0 = max(0, (h - nh) // 2)
                    ci = ci.crop((0, y0, w, y0 + nh))
                if float(np.asarray(ci.convert("L"), dtype=np.float32).std()) < 4.0:
                    return []
                dst = outdir / ("%s_cell%d.png" % (tag, len(cells) + 1))
                ci.save(dst)
                cells.append(dst)
        if len(cells) != cols * rows:
            return []
        # 去退化：**所有**相邻格几乎无差异 ⇒ 宫格没有动作信息，锚点等同虚无（成片必静）。
        # 阈值 5.0 的来历（实测标定，非拍脑袋）：
        #   退化宫格（各格同内容）相邻差异 0.94–3.06（非 0，切分有亚像素偏移）
        #   正常宫格（动作递变）相邻差异 29.6–32.1
        #   ⇒ 5.0 落在两者之间的空档，两边都有 1.6× 以上余量。
        # 只拦「全部相邻对都过低」；局部偏低（某两格接近）留给 s4_grid 告警。
        if len(cells) > 1:
            _d = [_frame_mae(cells[i], cells[i + 1]) for i in range(len(cells) - 1)]
            if max(_d) < 5.0:
                log("S4", "⚠️ 宫格内所有相邻格几乎无差异（最大 %.2f < 5.0）"
                    "⇒ 无动作信息，锚点等同虚无 ⇒ 拒绝切片" % max(_d))
                return []
        return cells
    except Exception as e:
        log("S4", "⚠️ 宫格切片失败：%s" % e)
        return []


# ── 宫格 prompt 合规自检（防未来改动静默破坏官方要求）──
# 每条 = (必须出现的子串, 官方依据)。改模板后若丢条款，check_grid_prompt 会报出来。
GRID_PROMPT_REQUIRED = (
    ("白色细缝", "格间白缝 ⇒ 切片器可检测格线；无白缝则无法可靠切分"),
    ("独立完整的画面", "官方：每格须是 complete standalone product photo"),
    ("没有任何画框、边框或文字", "官方：禁止 framed panels / 画面内文字"),
    ("同一个人", "官方 §3.2：跨格身份一致，否则 H3 在锚点间变形"),
    ("同一件产品", "产品跨格一致，否则产品形态漂移"),
    ("光线方向", "官方 §3.2 + 项目「同场景同光向」要求"),
    ("动作依次推进", "官方 §3.2：可观察的中间变化、逐步收窄"),
    ("身体动作或手部动作", "项目实证：只有镜头运动 ⇒ 拍成静态（洗碗机镜教训）"),
    ("主体静止不动", "同上：显式禁止静止格"),
    ("不被格子边缘切除", "构图完整，否则锚帧缺主体"),
    ("不要出现任何文字", "官方：禁止画面文字/水印/logo"),
    ("整体风格", "官方 §4.1：keyframe 任务的风格须由参考图推导"),
    ("逐格指定", "🔴 准确性根条款：每格画什么必须由脚本指定，不能让灵炫自己分格"),
    ("必须严格照下面", "同上：顺序不可自行改动"),
    ("不得自行改动顺序或省略任何一格", "防灵炫合并/省略格子"),
    ("必须能独立看出它对应的那一步动作", "每格须可辨识对应动作步骤"),
)


def check_grid_prompt(action: str = "把杯子放进微波炉加热后取出",
                      style: str = "clean bright") -> list:
    """渲染宫格 prompt 并返回缺失的必需条款（空列表 = 合规）。"""
    try:
        txt = GRID_PROMPT_TPL.format(
            rows=3, cols=2, cells=6, style=style,
            beat_lines=beat_lines(derive_beats(action, 6), 6, action))
    except Exception as e:
        return ["模板渲染失败：%s" % e]
    return ["缺【%s】(%s)" % (sub, why) for sub, why in GRID_PROMPT_REQUIRED
            if sub not in txt]


# ═══════════ 一致性门禁（B，2026-09-20）：宫格每格 vs 脚本该格动作 ═══════════
# 由来：A 让脚本产出逐格动作、宫格模板逐格指定，但**灵炫仍可能画错**（漏画/画成
# 另一个动作/合并格子）。没有这道门禁，"输入灵炫的一致性"只是意愿、不是保证。
# 复用 judge_l2._vlm_call（同一模型白名单/关思考链/退避重试），逐格做二值判定。
def judge_grid_vs_beats(cells: list, beats: list, workers: int = 4) -> dict:
    """逐格核「这格画面」是否与「脚本指定的该格动作」一致。

    返回 {checked, aligned, misaligned:[(格号, 脚本动作, VLM 原答)], skipped}
    导入失败/全部判不成 ⇒ skipped=True（**不阻塞链路**，但响亮告警）。
    """
    import base64
    try:
        _p = Path(__file__).resolve().parent
        for _c in (_p, _p / "scripts"):      # 双布局兜底（同上）
            if _c.exists() and str(_c) not in sys.path:
                sys.path.insert(0, str(_c))
        from judge_l2 import _vlm_call          # 复用：模型白名单 + 关思考链 + 退避
    except Exception as e:
        log("S4", "⚠️ 一致性门禁不可用（judge_l2 导入失败：%s）⇒ 跳过" % e)
        return {"checked": 0, "aligned": 0, "misaligned": [], "skipped": True}

    def _one(i):
        beat = beats[i] if i < len(beats) else ""
        try:
            b64 = base64.b64encode(Path(cells[i]).read_bytes()).decode()
        except Exception as e:
            return (i, None, "读图失败:%s" % e)
        # 🔴 两问合一：Q1 动作一致性（计入一致率）
        #           Q2 **物理/空间逻辑错误**（单格否决）
        # 由来：实测「口罩外侧画了唇印」被 83% 的平均分掩盖放行
        # ⇒ 不能只看均值：逻辑性错误是**一票否决**级别。
        claim = ("这是一组分镜图里的第 %d 格。请只输出 JSON，不要解释。\n"
                 "评估两件事：\n"
                 "  Q1 \"match\": 这一格画面中的动作，与下面这句脚本指定的动作是否一致？"
                 "（yes/no；画面看不出这个动作、或做的是另一个动作、"
                 "或这格是空白/纯背景看不出人 → no）\n"
                 "  Q2 \"logic_bad\": 这一格存在**物理或空间逻辑错误**吗？（yes/no）\n"
                 "     例：口罩上的唇印画在外侧（应在贴着嘴唇的内侧）、液体往上流、"
                 "容器未打开但物体已在里面、手指数量不对或融合、"
                 "产品形态变形或变成别的东西、主体缺失\n"
                 "     注意：只是「动作不完全对应」不算 logic_bad；必须是**不合理**。\n"
                 "脚本指定：%s" % (i + 1, beat or "（脚本未指定）"))
        try:
            r = _vlm_call([b64], claim, retries=2)
        except Exception as e:
            return (i, None, str(e)[:60], None)
        # r 是 dict（judge_l2 强制 JSON 出参）：{"match": "...", "logic_bad": "...", ...}
        _m = _b = None
        if isinstance(r, dict):
            _m = str(r.get("match", "")).strip().lower() or None
            _b = str(r.get("logic_bad", "")).strip().lower() or None
        if _m not in ("yes", "no") or _b not in ("yes", "no"):
            low = str(r).lower()
            if _m not in ("yes", "no"):
                _m = "yes" if '"match": "yes' in low or "'match': 'yes'" in low else (
                    "no" if "no" in low else None)
            if _b not in ("yes", "no"):
                _b = "yes" if "logic_bad" in low and '"yes' in low.split("logic_bad")[-1][:30] else (
                    "no" if "logic_bad" in low else None)
        return (i, _m, str(r)[:70], _b)

    with cf.ThreadPoolExecutor(max_workers=max(1, int(workers))) as ex:
        rows = list(ex.map(_one, range(len(cells))))
    mis = [(i + 1, beats[i] if i < len(beats) else "", raw)
           for i, v, raw, _b in rows if v == "no"]
    # 🔴 单格否决：物理/空间逻辑错误是**一票否决**级别，不看平均一致率
    #    （由来：实测「口罩外侧画了唇印」被 83% 的平均分掩盖放行）
    bad = [(i + 1, beats[i] if i < len(beats) else "", raw)
           for i, _v, raw, _b in rows if _b == "yes"]
    checked = sum(1 for _, v, _, _b in rows if v in ("yes", "no"))
    if checked == 0:
        log("S4", "⚠️ 一致性门禁：%d 格一格都没判成（VLM 全失败）⇒ **不算通过**，"
            "按未校验处理（不静默放行）" % len(cells))
        return {"checked": 0, "aligned": 0, "misaligned": [], "logic_bad": [], "skipped": True}
    return {"checked": checked, "aligned": checked - len(mis),
            "misaligned": mis, "logic_bad": bad, "skipped": False}


def segment_has_action(beats: list, text: str = "") -> tuple:
    """该段是否有**可拆解的动作序列** —— 决定走宫格还是常规首尾帧。

    依据（2026-09-20 实测）：美妆类产品特写段的脚本画面是
    「瓶身是磨砂玻璃质感 / 银色泵头 / 柔和的自然光从右侧窗户照进来 / 背景是虚化的干花」
    —— 这是**静物描述**，没有动作可拆。硬套宫格只会切出 6 张几乎一样的静物图
    （退化检测能拦，但那是**事后补救**，还白花一次灵炫出图）。
    这里在生成宫格**之前**判掉。

    判据：出现 ≥2 个（身体/手部）动作动词 ⇒ 有动作。
    只有镜头运动词（推近/环绕/静置/特写）**不算**动作 —— 曾实测「洗碗机镜只有
    镜头环绕、零主体动作」被拍成静态图。
    """
    blob = " ".join([text or ""] + [str(b) for b in (beats or [])]).lower()
    if not blob.strip():
        return (False, "无文本")
    acts = {v for v in CFG.get("action_verbs", []) if str(v).lower() in blob}
    subs = {w for w in CFG.get("subject_action_words", []) if str(w).lower() in blob}
    hits = sorted(acts | subs)
    cam = [k for k in ("camera", "镜头", "orbiting", "pushes in", "pans", "static",
                       "环绕", "推进", "横移", "拉远", "特写", "虚化", "背景",
                       "散落", "立着", "放着") if k in blob]
    if len(hits) >= int(CFG.get("grid_min_action_verbs", 2)):
        return (True, "动作词 %d 个 %s" % (len(hits), hits[:6]))
    return (False, "仅 %d 个动作词 %s；景物/镜头词 %s ⇒ 静物段" % (len(hits), hits[:4], cam[:6]))


# ═══════════ 产品识别（VL，2026-09-20）═══════════
# 🔴 为什么必需：ClipForge 的 /api/llm/script **只吃文字**（productName +
#    productDescription），不收图片。product_text 为空时 = ClipForge 零信息，
#    LLM 只能凭 style 提示自由发挥 —— 实测把「唇釉」写成「粉底液」，
#    还编出「泵头是玫瑰金色 / 瓶身是磨砂玻璃 / 美妆蛋和粉底刷」。
#    而我们的图像层一直在画正确的产品 ⇒ 错的是「没做识别就交给文字层」这一步。
VLM_PRODUCT_SYS = (
    "You are a meticulous product-identification analyst for e-commerce short videos. "
    "You examine ONE product photo and report ONLY what is plainly visible. "
    "Never invent hardware, textures or accessories that you cannot see. "
    "If something is genuinely unclear, record it in \"uncertain\" instead of guessing. "
    "Output STRICT JSON only, with no prose and no markdown fences."
)
VLM_PRODUCT_PROMPT = (
    "Examine this product photo and report ONLY what is visible.\n"
    "Return strict JSON with exactly these keys:\n"
    "  name            : product name in Chinese (copy visible branding; if none, name it by form)\n"
    "  category        : product category in Chinese (e.g. 唇釉 / 口红 / 粉底液 / 无线耳机 / 咖啡液)\n"
    "  form            : physical form in Chinese (e.g. 细长方管+旋盖+唇釉刷头 / 瓶装液体 / 罐装)\n"
    "  color           : dominant colour in Chinese\n"
    "  package_text    : ALL text legible on the package, verbatim; empty string if none\n"
    "  visible_features: array of up to 4 VISIBLE material/structure traits, in Chinese\n"
    "  uncertain       : anything you genuinely cannot tell (e.g. 看不出是口红还是唇釉)\n"
    "Do NOT describe anything not present in the image."
)


def vl_product_ready() -> bool:
    """产品识别是否可用（judge_l2 可导入 + 有 Key）。"""
    try:
        _p = Path(__file__).resolve().parent
        for _c in (_p, _p / "scripts"):
            if _c.exists() and str(_c) not in sys.path:
                sys.path.insert(0, str(_c))
        from judge_l2 import _vlm_call  # noqa: F401
        return True
    except Exception:
        return False

def _strip_inline_voiceover(desc: str) -> str:
    """剥离正文里内嵌的台词句 —— 官方 §4.4 要求台词只出现在 <d> 内。

    实测 ClipForge 的 h3_prompt 自带形如
        A voiceover says in Chinese: "……" while no one is visible in the frame.
    而我们的组装器**又**追加了 `The woman (S1) says naturally: <d>[Chinese] …</d>`
    ⇒ 台词出现两次，且第一处不符合官方 <d> 规范（也会让模型把同一句话念两遍）。
    """
    import re as _re
    pat = _re.compile(
        r'\s*(?:A |The )?(?:voiceover|narrator|off-screen voice|woman|man)\s+'
        r'(?:says|says in [A-Za-z ]+|narrates)[^.!?]{0,80}?[:：]\s*'
        r'[""\u201c][^""\u201d]{1,200}[""\u201d]'
        r'(?:\s*while[^.!?]{0,80}?\bframe\.?)?',
        _re.I)
    return pat.sub("", desc).strip()


def _motion_boost(desc: str, subject_words: list) -> tuple:
    """对单镜描述做运动增强。返回 (新描述, 命中的增强项列表)。"""
    out = desc
    hits = []
    low = out.lower()
    # ① 剔除静止/定格写法
    for ph in _STATIC_RE:
        if ph in out or ph.lower() in low:
            import re as _re
            out = _re.sub(_re.escape(ph), "", out, flags=_re.I)
            hits.append("去静止写法:%s" % ph)
            low = out.lower()
    # ② 只有镜头运动、无主体动作 → 追加主体动作要求
    has_subject = any(w.lower() in low for w in subject_words)
    cam_only = any(k.lower() in low for k in _CAM_ONLY_HINT)
    # ⚠️ 语言必须与正文一致：官方要求正文用英文，早期版本给英文 prompt 追加中文
    #    句子 ⇒ 中英混排（实测验证时抓到）。按 CJK 占比判定语言。
    # ⚠️ 只看**前 200 字**：正文里可能内嵌中文台词（"A voiceover says in Chinese: …"），
    #    用全文 CJK 占比会把英文正文误判成中文（实测踩过，导致给英文 prompt 追加中文句）。
    _head = out[:200]
    _cjk = sum(1 for c in _head if "\u4e00" <= c <= "\u9fff")
    zh = _cjk > max(4, len(_head) * 0.10)
    if cam_only and not has_subject:
        out = out.rstrip("。. ") + (
            "。同时主体必须持续有可见动作：产品表面的光随动作流动、"
            "蒸汽/液体/材质高光在整段内连续变化，不得出现完全静止的画面。"
            if zh else
            ", while the subject itself keeps moving continuously: light slides across "
            "the product surface, steam, liquid or material highlights shift throughout "
            "the shot, and the frame is never completely still.")
        hits.append("补主体动作")
    # ③ FL2VA 固有静启 → 明确要求立即起势（官方 §3.2：observable intermediate changes）
    out = out.rstrip("。. ") + (
        "。动作从本镜第 0 秒就开始，不要留静止开场；整段画面必须持续变化直到结尾。"
        if zh else
        ", and the motion starts at the very first frame of this shot: the action is "
        "already under way at 0.00 seconds, never holding still, and the frame keeps "
        "changing visibly until the cut.")
    hits.append("加立即起势")
    return out, hits


def build_h3_prompt(shots: list, seconds: float,
                    soundscape: str = "", music: str = "N/A") -> str:
    """把逐镜内容组装成官方 FL2VA 三段式 prompt。

    **严格对齐**官方 skills/h3-prompt-writing/references/base-en.txt：

    §2.1 首行 = 对齐指令（逐字），后接**一个空行**；N=最后一镜序号、S.SS 两位小数。
    §2.2 三字段固定顺序：integrated_multimodal_description / overall_soundscape /
         non_diegetic_music。
    §3.2 FL2VA 金样例结构（本函数核心）：
         · [Shot 1] 开头**先写整体风格 + 初始构图**
         · 紧跟 **显式锚定首帧**：beginning in the position and framing established by Picture 1
         · 中间变化用**连续时序从句**（as / while / until / then）+ 可观察状态；
           单镜内**不写时间戳**（官方金样例单镜内无时间戳）
         · **显式锚定尾帧**（放在描述末尾）：settles into the pose, spacing, and
           composition established by Picture 2 at the end of the shot.
         · 每个细节都必须对应可见/可听之物（禁参数堆砌、禁抽象词）
    §4.2 镜间切换才用时间戳：[Shot 2] At 00:03.500, the camera cuts to …；首镜不加。
    §4.4 说话人：身份短语写在 <d> 外，<d> 内只有语言标签 + 原样台词。
    §4.3 相机运动 = 类型 + 幅度 + 速度（自然英语，不是标签堆叠）。

    r34l1sm：既违反官方「[Shot 1] 首位写风格」，又是孤立 token（实测 A/B 产物内嵌的
    ComfyUI 图里未加载任何 realism LoRA）⇒ **默认剥离**（不论来自 ClipForge 还是本地）。
    真挂了 realism LoRA 时用 CFG["h3_trigger_words"] 打开，会插到风格之后。
    """
    n = max(1, len(shots))
    align = ("How the reference pictures align with the target video \u2014 "
             "Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; "
             "Picture 2 (from Shot %d) aligns with the %.2f-second mark of the target video."
             % (n, seconds))
    style = CFG.get("h3_style", "Live-action, cinematic")
    tw = (CFG.get("h3_trigger_words") or "").strip()
    parts = []
    for i, sh in enumerate(shots, 1):
        # 视频层优先用 ClipForge 的英文 h3_prompt（本为 H3 而写、符合官方「正文用英文」），
        # 退而用图像层 visual（中文）。历史缺陷：一直用中文 visual 当正文 ⇒ 违反官方
        # Output Rules「Write rewrite sections in English」。
        vis = (sh.get("h3_prompt") or sh.get("visual") or "").strip()
        # 剥离孤儿触发词（源文本常自带 "r34l1sm, "）
        if not tw:
            for _t in ("r34l1sm,", "r34l1sm"):
                if vis[:len(_t) + 1].lower().startswith(_t):
                    vis = vis[len(_t):].lstrip(" ,")
        elif tw not in vis:
            vis = "%s, %s" % (tw, vis)
        vis = _strip_inline_voiceover(vis)
        vis, _mh = _motion_boost(vis, CFG.get("subject_action_words", []))
        if _mh:
            log("S2", "镜%d 运动增强: %s" % (i, ",".join(_mh)))
        # 注意：S2 节拍表把台词放在 text.voiceover_zh（不是顶层 line）；
        # 读错字段会让组装出的 prompt 丢掉全部台词 ⇒ 成片静音。
        line = (sh.get("line") or (sh.get("text") or {}).get("voiceover_zh") or "").strip()
        # 标点清理：中文句号 → 英文；折叠双逗号；去尾部逗号
        vis = (vis.replace("。", ".").replace("，，", ",").replace(",,", ",")
                  .replace(" ,", ",").replace(" .", ".").strip().rstrip(",; "))
        vis = " ".join(vis.split())   # 折叠多余空白（不用 re，模块未导入）
        if i == n:
            # §3.2 尾帧锚定（照官方金样例句式），贴在描述末尾
            vis += (", then settles into the pose, spacing, and composition "
                    "established by Picture 2 at the end of the shot")
        if i == 1:
            # §4.1 先写风格与初始构图 → §3.2 紧跟首帧锚定 → 正文
            piece = "[Shot 1] %s, beginning in the position and framing established by Picture 1. %s" % (style, vis)
        else:
            at = float(sh.get("start", 0.0))
            mm, ss = int(at // 60), at % 60
            piece = "[Shot %d] At %02d:%06.3f, the camera cuts to %s" % (i, mm, ss, vis)
        if line:
            piece += (" The woman (S1) %s: <d>[Chinese] %s</d>"
                      % (CFG.get("h3_voice_style", "says naturally"), line))
        parts.append(piece)
    return ("%s\n\nintegrated_multimodal_description: %s\n\noverall_soundscape: %s\n\n"
            "non_diegetic_music: %s"
            % (align, " ".join(parts),
               soundscape or CFG.get("h3_soundscape_default", ""),
               music or CFG.get("h3_music_default", "N/A")))

def _tone_sfx(enable: bool = True) -> str:
    """P5 统一视觉基准后缀（母版/锚定照/段首尾帧共用）。"""
    if not enable:
        return ""
    s = (CFG.get("series_tone") or "").strip()
    return ("。" + s) if s else ""


def _brightness(path) -> float:
    """图片平均亮度(0-255)。用于跨段影调一致性校验；失败返回 -1。"""
    try:
        import numpy as np
        from PIL import Image
        return float(np.asarray(Image.open(path).convert("L"), dtype=np.float32).mean())
    except Exception:
        return -1.0


def _change_ratio(a, b, thr: int = 8) -> float:
    """两张同尺寸图「变化像素占比」(0~1)。用于首尾帧状态变化量体检；失败返回 -1。"""
    try:
        import numpy as np
        from PIL import Image
        x = np.asarray(Image.open(a).convert("RGB"), dtype=np.float32)
        y = np.asarray(Image.open(b).convert("RGB"), dtype=np.float32)
        if x.shape != y.shape:
            return -1.0
        return float((np.abs(x - y).mean(axis=2) > thr).mean())
    except Exception:
        return -1.0


def _img_ok(p) -> bool:
    """图片已存在且像真图（>1KB）⇒ 视为可复用。

    幂等设计：断点续跑/重跑不重复烧图（单张 ¥0.0444，批量时差别明显），
    也避免同一路径被两次并发写坏。
    """
    try:
        return Path(p).exists() and Path(p).stat().st_size > 1024
    except OSError:
        return False


def log(stage: str, msg: str) -> None:
    print("[%7.1fs] %-6s %s" % (time.time() - T0, stage, msg), flush=True)


T0 = time.time()


class CircuitBreak(Exception):
    """闸门② 否决 —— **可重试**（退回文字层换一批候选重写）。
    与其它 CircuitBreak 区分：只有它才触发 K 次重写回路，其它熔断立即失败。"""


class Gate2Reject(CircuitBreak):
    """熔断：到顶必须报明确失败，禁止静默续跑。"""


# ─────────────────────────── 编排器 ───────────────────────────

class Orchestrator:
    def __init__(self, job: Job, outdir: Path, dry: bool = False, allow_system_disk: bool = False):
        self.job = job
        self.out = Path(outdir)
        self.dry = dry
        self.allow_system_disk = allow_system_disk   # 仅试跑：显式放行系统盘（生产禁用）
        self.segments: list[Segment] = []
        self.runlog = self.out / "runs.jsonl"
        self.state: dict = {"job_id": job.job_id, "stage": "init", "notes": []}
        for sub in ("img", "prompt", "video", "report"):
            (self.out / sub).mkdir(parents=True, exist_ok=True)

    # ---------- 状态落盘 ----------
    def save_state(self, stage: str) -> None:
        self.state["stage"] = stage
        self.state["updated"] = time.time()
        (self.out / "state.json").write_text(
            json.dumps(self.state, ensure_ascii=False, indent=2), encoding="utf-8")

    def append_run(self, **kw) -> None:
        kw.update({"job_id": self.job.job_id, "ts": time.time()})
        with self.runlog.open("a", encoding="utf-8") as f:
            f.write(json.dumps(kw, ensure_ascii=False) + "\n")

    # ---------- 外部调用（可 dry-run 桩替换） ----------
    def tt_img(self, out_path: Path, size: str, prompt: str, refs: list[str] | None = None) -> str | None:
        """灵炫 TT Image2：文生图 / 图片编辑（同源派生）

        🚨 模型白名单：ttimg.py 内固定 MODEL = "tt-image-2"。
        禁止在此处引入灵炫的任何视频模型或其它生图模型（辉哥拍板硬规范）。
        """
        pf = self.out / "prompt" / (out_path.stem + ".txt")
        pf.write_text(prompt, encoding="utf-8")
        if self.dry:
            log("S4", "[dry] ttimg %s refs=%d → %s" % (size, len(refs or []), out_path.name))
            return str(out_path)
        cmd = [sys.executable, str(TTIMG),
               "edit" if refs else "gen", str(out_path), size, str(pf)] + (refs or [])
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=CFG["img_timeout_s"] + 60)
        except subprocess.TimeoutExpired:
            log("S4", "⚠️ ttimg 超时：%s" % out_path.name)
            return None
        if r.returncode != 0 or not out_path.exists():
            log("S4", "⚠️ ttimg 失败：%s\n%s" % (out_path.name, (r.stdout or r.stderr)[-400:]))
            return None
        return str(out_path)

    def _stage_one(self, src: Path, tag: str) -> str | None:
        """把一张图复制进 ComfyUI 的 input 白名单目录，返回可被 LoadImage 接受的路径。"""
        try:
            d = Path(CFG.get("h3_input_dir", "/root/autodl-tmp/h3p/input"))
            d.mkdir(parents=True, exist_ok=True)
            dst = d / ("h3stg_%s.png" % tag)
            shutil.copy2(str(src), str(dst))
            return str(dst)
        except Exception as e:
            log("S6", "⚠️ 暂存失败 %s: %s" % (src, e))
            return None

    def _stage_for_h3(self, seg: Segment) -> tuple:
        """把该段首尾帧暂存进 ComfyUI input 目录。

        ⚠️ ComfyUI LoadImage 只接受 --input-directory 白名单内的路径；喂白名单外的
        路径会报 `Invalid image file`，且**每段都失败但 S7 曾把它当通过**（假通过）。
        历史 FL2VA 实验能跑通是因为素材本来就在 input/ 下。
        """
        if not seg.first or not seg.last:
            return None, None
        return (self._stage_one(Path(seg.first), "seg%02d_first" % seg.idx),
                self._stage_one(Path(seg.last), "seg%02d_last" % seg.idx))

    def h3_generate(self, seg: Segment, prefix: str) -> str | None:
        """H3 官方原生链路（3090，:6011）"""
        pf = self.out / "prompt" / ("seg%d.txt" % seg.idx)
        # 视频层用 h3_prompt（ClipForge 产出的 H3 提示词）；缺省才回落场景叙述
        pf.write_text(seg.h3_prompt or seg.prompt, encoding="utf-8")
        if self.dry:
            log("S6", "[dry] H3 seg%d seed=%d → %s" % (seg.idx, seg.seed, prefix))
            return str(self.out / "video" / (prefix + "_%06d_.mp4" % seg.seed))

        # ★ 暂存到 ComfyUI 的 input 白名单目录（否则 LoadImage 直接拒收）
        f1, f2 = self._stage_for_h3(seg)
        if not f1 or not f2:
            log("S6", "⚠️ 段%d 首尾帧暂存失败" % seg.idx)
            return None
        cmd = [sys.executable, str(H3GEN),
               f1, f2, str(pf), str(seg.seconds), str(CFG["megapixels"]),
               str(CFG["steps"]), prefix, str(seg.seed), "--port", str(CFG["comfy_port"]),
               "--aspect", CFG["aspect"], "--lora", CFG["h3_lora"]]
        for img, sec in seg.mids:
            st = self._stage_one(Path(img), "mid%d_%s" % (seg.idx, sec))
            cmd += ["--mid", "%s@%s" % (st or img, sec)]
        _t_submit = time.time()
        r = subprocess.run(cmd, capture_output=True, text=True)
        # 🔴 rc 有语义，不得一律折叠成「随机失败」。test_fl2v_official.py：
        #    2 = 首尾帧比例守卫否决（**确定性**，重抽必然同样失败）
        #    3 = GPU 租约获取失败（资源问题，不该消耗抽卡次数）
        if r.returncode == 2:
            raise CircuitBreak(
                "段%d H3 rc=2：首尾帧比例守卫否决（确定性失败，非抽卡运气）——重抽无用。"
                " 须按画布 %s 重出图片。尾部输出：%s"
                % (seg.idx, CFG["img_size"], ((r.stdout or "") + (r.stderr or ""))[-300:]))
        if r.returncode == 3:
            log("S6", "⚠️ 段%d H3 rc=3：GPU 租约获取失败（资源问题，非画质问题）" % seg.idx)
        if r.returncode != 0:
            log("S6", "⚠️ H3 失败（rc=%d）：%s" % (r.returncode, (r.stdout or r.stderr)[-400:]))
            return None
        out = parse_h3_output(r.stdout, CFG.get("comfy_output_dir", "/root/autodl-tmp/h3p/output"),
                              prefix, not_before=_t_submit)
        if out is None:
            log("S6", "⚠️ H3 执行完成但未找到产物；stdout 尾部：%s" % ((r.stdout or "")[-300:]))
        else:
            log("S6", "  产物 %s（%.1f MB）" % (Path(out).name, Path(out).stat().st_size / 1e6))
        return out

    # ---------- S0 ----------
    def vl_product_info(self) -> dict | None:
        """产品识别（VL）：读第一张产品图 → 品名/品类/形态/颜色/可见特征。

        失败/判不成一律返回 None（调用方跳过，**绝不静默编造**）。
        """
        import base64
        try:
            _p = Path(__file__).resolve().parent
            for _c in (_p, _p / "scripts"):
                if _c.exists() and str(_c) not in sys.path:
                    sys.path.insert(0, str(_c))
            from judge_l2 import _vlm_call
        except Exception as e:
            log("S0", "⚠️ 产品识别不可用（judge_l2 导入失败：%s）⇒ 跳过" % e)
            return None
        img = Path(self.job.product_images[0])
        if not img.exists():
            log("S0", "⚠️ 产品图不存在：%s" % img)
            return None
        try:
            b64 = base64.b64encode(img.read_bytes()).decode()
        except Exception as e:
            log("S0", "⚠️ 读产品图失败：%s" % e)
            return None
        try:
            r = _vlm_call([b64], VLM_PRODUCT_PROMPT, retries=3,
                          system=VLM_PRODUCT_SYS, want_json=True, max_tokens=1500)
        except Exception as e:
            log("S0", "⚠️ 产品识别调用失败：%s" % e)
            return None
        if not isinstance(r, dict) or r.get("verdict") == "na" or r.get("_error"):
            log("S0", "⚠️ 产品识别未返回有效结果 ⇒ 跳过（不编造）")
            return None
        return r

    def s0_ingest(self) -> None:
        log("S0", "入库 %d 张产品图 / %d 张模特图" % (len(self.job.product_images), len(self.job.model_images)))
        if not self.job.product_images:
            raise CircuitBreak("S0 缺产品图，早退回（不带病进入）")
        # ── 产品识别（VL）：必须在保真分层**之前** —— 画面品牌文字是像素级信号，
        #    而 product_text 为空时分层只能看文件名（实测唇釉被判成「形态级」）。
        if CFG.get("vl_product_analysis", True) and not (self.job.product_text or "").strip():
            info = self.vl_product_info()
            if info:
                try:
                    (self.out / "report" / "product_vl.json").write_text(
                        json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
                except Exception:
                    pass
                _parts = [str(info.get("category") or ""), str(info.get("form") or ""),
                          (str(info.get("color") or "") + "色") if info.get("color") else "",
                          "、".join([str(x) for x in (info.get("visible_features") or [])[:4]]),
                          ("包装文字：" + str(info["package_text"])) if str(info.get("package_text") or "").strip() else ""]
                _desc = "；".join([x for x in _parts if x])
                self.job.product_text = _desc
                _pn = str(info.get("name") or "").strip()
                if _pn:
                    self.job.params = dict(self.job.params or {})
                    self.job.params.setdefault("product_name", _pn)
                log("S0", "★产品识别（VL）：%s ｜ %s" % (_pn or "?", _desc[:120]))
                if str(info.get("uncertain") or "").strip():
                    log("S0", "  ⚠️ 识别不确定项：%s" % str(info["uncertain"])[:100])
            else:
                log("S0", "⚠️ 产品识别未成功 ⇒ 交给 ClipForge 的文字描述为空"
                    "（**风险：LLM 可能自由发挥编造产品**）")
        elif CFG.get("vl_product_analysis", True):
            log("S0", "产品描述由用户提供 ⇒ 跳过 VL 识别（用户优先）")
        txt = self.job.product_text + "\n" + " ".join(self.job.product_images)
        # ★闸门①：像素级信号词（logo/中文标签/价格/复杂印刷）
        pixel_signals = ["logo", "LOGO", "商标", "标签", "价格", "¥", "元", "净含量", "配料", "说明"]
        hit = [s for s in pixel_signals if s in txt]
        self.job.fidelity_class = "像素级" if hit else "形态级"
        self.state["fidelity_class"] = self.job.fidelity_class
        log("S0", "★闸门① 保真分层 = %s%s" % (
            self.job.fidelity_class, ("（命中：%s）" % ",".join(hit)) if hit else ""))
        if self.job.fidelity_class == "像素级":
            log("S0", "  ⇒ 像素级：TT Image2 只准画环境/模特，产品走真实像素合成")
        self.save_state("S0")

    # ---------- S1 ----------
    def _s1_clipforge(self) -> dict:
        """调 clipforge_adapter.py：ClipForge → side_a 契约（含 P3 闸门预检）。

        失败一律 CircuitBreak（禁止静默续跑）。
        P3 闸门**默认只告警**（新门禁须先用已验收成片标定）；params.gate_p3_strict=True 才阻断。
        """
        # HERE 随 orchestrator.py 的落位而变（本地在仓库根 / 生产在 scripts/），
        # 用 _resolve 双路径兜底，避免「一边能跑一边熔断」。
        adapter = _resolve("clipforge_adapter.py")
        if not adapter.exists():
            raise CircuitBreak("S1 找不到 clipforge_adapter.py（%s）" % adapter)
        p = self.job.params or {}
        # ── S0 产品识别(VL)兜底：job 未给品类/描述时，用 VL 结果填 ──
        #    根因修复：job 常缺 category 且 product_text 为空
        #    -> 适配器 pick_hook() 恒回落默认钩子池，品类策略(如唇部→H6试色实测)静默失效
        _vl = {}
        try:
            _vlf = self.out / "report" / "product_vl.json"
            if _vlf.exists():
                _vl = json.loads(_vlf.read_text(encoding="utf-8")) or {}
        except Exception as e:
            log("S1", "⚠️ 读取 product_vl.json 失败（品类兜底失效）: %s" % e)
        name = str(p.get("product_name") or "").strip()
        if not name:
            name = str(_vl.get("name") or "").strip()
        if not name:
            name = (self.job.product_text or "").split()[0] if self.job.product_text else "未命名产品"
        _cat = str(p.get("category") or "").strip() or str(_vl.get("category") or "").strip() or "other"
        _desc = (self.job.product_text or "").strip()
        if not _desc:
            _bits = [str(_vl.get(k) or "") for k in ("name", "category", "form", "color")]
            _bits += [str(x) for x in (_vl.get("visible_features") or [])]
            _desc = " ".join([b for b in _bits if b]).strip()
        if _vl:
            log("S1", "S0 兜底：品类=%s 名称=%s" % (_cat, name))
        out_dir = self.out / "report"
        base_cmd = [sys.executable, str(adapter),
                    "--endpoint", os.environ.get("CLIPFORGE_ENDPOINT", "http://43.136.35.203:3000"),
                    "--name", name,
                    "--desc", _desc,
                    "--category", _cat,
                    "--style", str(p.get("script_style", "pain_point")),
                    "--duration", str(int(p.get("duration", 30))),
                    "--fidelity", self.job.fidelity_class]
        if p.get("gate_p3_strict"):
            base_cmd.append("--strict")
        for img in self.job.product_images:
            base_cmd += ["--image", img]

        k_n = max(1, int(CFG.get("s1_candidates", 2)))
        cands = []
        for k in range(1, k_n + 1):
            cand_out = out_dir / ("side_a_cand%d.json" % k)
            try:
                r = subprocess.run(base_cmd + ["--out", str(cand_out)],
                                   capture_output=True, text=True, timeout=CFG["s1_timeout_s"])
                for _ln in (r.stdout or "").splitlines():
                    if "hook]" in _ln:
                        log("S1", _ln.strip())
            except subprocess.TimeoutExpired:
                log("S1", "候选%d 超时（>%ds）" % (k, CFG["s1_timeout_s"]))
                continue
            for line in (r.stdout or "").splitlines():
                if (line.startswith("[闸门P3") or line.startswith("[adapter] 评分")
                        or line.startswith("[adapter] ⏱")):
                    log("S1", "候选%d %s" % (k, line.strip()))
            if r.returncode == 3:
                log("S1", "候选%d 被闸门否决（--strict）" % k)
                continue
            if r.returncode != 0 or not cand_out.exists():
                log("S1", "候选%d 失败 rc=%d：%s"
                    % (k, r.returncode, ((r.stdout or r.stderr) or "")[-200:]))
                continue
            # ⚠️ 用 with_suffix 而非无界 replace：outdir 若含 ".json" 子串会被整段改写
            gate_f = Path(str(cand_out)).with_suffix("").with_suffix(".gate.json")
            gate = None
            if gate_f.exists():
                try:
                    gate = json.loads(gate_f.read_text(encoding="utf-8"))
                except Exception as _ge:
                    log("S1", "⚠️ 候选%d gate 文件解析失败: %s" % (k, _ge))
            # 🔴 2026-09-20 修复：gate 缺文件/解析失败 ⇒ 置**最差分**。
            #    原实现回落 gate={} ⇒ score=[0,0] ⇒ 反而得满分、被 min() 选为最优，
            #    择优机制被反向污染（且日志显示 errors=0 warnings=0 看着完全正常）。
            if not isinstance(gate, dict):
                score = (10 ** 6, 10 ** 6)
                log("S1", "⚠️ 候选%d 无可用 gate 评分（%s）→ 置最差分" % (k, gate_f.name))
            else:
                score = tuple(gate.get("score") or [len(gate.get("errors", [])),
                                                    len(gate.get("warnings", []))])
            cands.append({"k": k, "gate": gate, "score": score,
                          "data": json.loads(cand_out.read_text(encoding="utf-8"))})
            log("S1", "候选%d 评分 errors=%d warnings=%d" % (k, score[0], score[1]))

        if not cands:
            raise CircuitBreak("S1 ClipForge 全部 %d 个候选均失败" % k_n)
        best = min(cands, key=lambda c: c["score"])
        (out_dir / "side_a.json").write_text(
            json.dumps(best["data"], ensure_ascii=False, indent=2), encoding="utf-8")
        log("S1", "择优：候选%d/%d（errors=%d warnings=%d）。全部候选评分 %s"
            % (best["k"], len(cands), best["score"][0], best["score"][1],
               {c["k"]: list(c["score"]) for c in cands}))
        return dict(best["data"])

    def s1_script(self) -> dict:
        """ClipForge 文字层（P1：已接 clipforge_adapter.py → side_a 契约）。

        契约：**ClipForge 是唯一内容来源**；本方法只做「调用 + 落盘 + 透传」，
        不重写台词、不增删镜头、不改卖点表述（再做创作会破坏带货结构与判官标定）。
        """
        log("S1", "文字层（ClipForge）")
        if self.dry:
            side_a = {
                "product": {"name": "示例产品", "category": "食品饮品",
                            "selling_points": ["0糖0脂", "冷萃12小时", "一支兑一杯"],
                            "fidelity_class": self.job.fidelity_class},
                "hook_type": "自曝反转",
                "cta": {"text": "链接放下面了", "position": "末镜复读", "urgency": "软紧迫"},
                "shots": [
                    {"idx": 1, "line": "就这玩意儿，把我星巴克的钱包给救了", "start": 0.0, "end": 2.5,
                     "visual": "手举起产品，暖光特写", "camera": "推近"},
                    {"idx": 2, "line": "一条挤进冰水，三秒就是一杯", "start": 2.5, "end": 5.5,
                     "visual": "产品静置，液体缓流，光影扫过", "camera": "特写平移"},
                    {"idx": 3, "line": "算下来一杯不到三块钱", "start": 5.5, "end": 8.0,
                     "visual": "产品并排陈列，浅景深", "camera": "静置"},
                    {"idx": 4, "line": "链接我放这了，自己看", "start": 8.0, "end": 10.0,
                     "visual": "产品居中收束", "camera": "旋转"},
                ],
            }
        else:
            side_a = self._s1_clipforge()
        (self.out / "report" / "side_a.json").write_text(
            json.dumps(side_a, ensure_ascii=False, indent=2), encoding="utf-8")
        self.save_state("S1")
        return side_a

    # ---------- S2 ----------
    def s2_adapter(self, side_a: dict) -> list[dict]:
        """side_a → 11 列节拍表 + 文案三分流 + 时长网格吸附（无状态纯函数）"""
        rows, warns = [], []
        for sh in side_a["shots"]:
            dur = sh["end"] - sh["start"]
            frames = snap_frames(dur)
            cam = sh["camera"]
            if cam not in CFG["action_whitelist"]:
                warns.append("镜%d 运镜「%s」不在白名单 → 降级为「静置」" % (sh["idx"], cam))
                cam = "静置"
            rows.append({
                "idx": sh["idx"],
                "start": sh["start"], "end": sh["end"],
                "frames": frames, "seconds_snapped": frames / CFG["fps"],
                "purpose": "", "visual": sh["visual"], "camera": cam,
                # 🔴 h3_prompt 与 visual 分家：前者只进视频层，后者只进图像层
                "h3_prompt": sh.get("h3_prompt", ""),
                # 结构化分格动作序列（来自脚本）：宫格模式逐格指定用
                "beats": sh.get("beats") or [],
                "text": {"onscreen_en": "", "voiceover_zh": sh["line"], "post_zh": ""},
            })
        for w in warns:
            log("S2", "⚠️ " + w)
        self.state["gate2_warnings"] = warns
        log("S2", "适配完成：%d 镜（%s 帧网格已吸附，%d 条 warning）" % (
            len(rows), "17k+5", len(warns)))
        (self.out / "report" / "shotlist.json").write_text(
            json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        self.save_state("S2")
        return rows

    # ---------- S3 ----------
    def s3_gate2(self, rows: list[dict]) -> list[dict]:
        """★闸门②：白名单 + 可验证物理结果 + 四类禁写 + **每镜动作数=1**（2026-09-20 真实现）

        🔴 两处「声明了但没实现」的修复：
          ① docstring 一直写着「每镜动作数=1」，原实现只做两轮子串匹配 ⇒ 动作链检查
             **从未实现**。真实事故：段2 写「放入微波炉、按下按钮、取出、放桌上」5 步，
             8 秒内模型跳步压缩 ⇒ 成片「微波炉门还没开、杯子已经在里面」。
          ② 只扫 r["visual"]（图像层），**不扫实际喂 H3 的文本** ⇒ 闸门覆盖的字段与
             实际执行字段不是同一个（真实事故：段1/3 只描述镜头运动 + 末尾定格写法
             ⇒ 成片开头像静态图、洗碗机镜完全无动态）。
        """
        rejected, warns = [], []
        for r in rows:
            # 合并「真正会进 GPU 的文本」：visual（图像层）+ h3_prompt（视频层）+ 台词
            blob = " ".join([r.get("visual", "") or "",
                             r.get("h3_prompt", "") or "",
                             ((r.get("text") or {}).get("voiceover_zh") or "")])
            low = blob.lower()

            for bad in CFG["forbidden_by_physics"]:
                if bad in blob:
                    rejected.append((r["idx"], "含可验证物理结果「%s」" % bad))
            for bad in CFG["forbidden_by_generability"]:
                if bad in blob:
                    rejected.append((r["idx"], "命中四类禁写「%s」" % bad))

            # ── 静止/定格写法：直接导致「开头像静态图」「整镜无动态」──
            for bad in CFG.get("forbidden_static_phrases", []):
                if (bad in blob) or (bad.lower() in low):
                    _m = "含静止/定格写法「%s」⇒ 成片会像静态图" % bad
                    # 已在 build_h3_prompt 里自动剔除；此处仅确认它没有漏网进 GPU
                    (rejected if CFG.get("gate2_static_strict") else warns).append(
                        (r["idx"], _m) if CFG.get("gate2_static_strict") else "镜%d %s" % (r["idx"], _m))

            # ── 每镜动作数=1：动作动词去重计数（中文用逗号串联，不能靠连接词）──
            hits = sorted({v for v in CFG.get("action_verbs", []) if v.lower() in low})
            if len(hits) > CFG.get("max_action_steps", 2):
                _m = ("一拍内动作链约 %d 步 %s（上限 %d）⇒ 模型会跳步/顺序错乱"
                      % (len(hits), hits[:6], CFG.get("max_action_steps", 2)))
                if CFG.get("gate2_action_strict"):
                    rejected.append((r["idx"], _m))
                else:
                    warns.append("镜%d %s" % (r["idx"], _m))

            # ── 主体动作缺失：只有镜头运动、无任何主体动作词 ⇒ 画面近乎静止 ──
            has_subject = any(w.lower() in low for w in CFG.get("subject_action_words", []))
            cam_only = any(k in low for k in (
                "camera", "镜头", "orbiting", "pushes in", "tracking shot", "pans",
                "static", "环绕", "推进", "横移"))
            if cam_only and not has_subject:
                warns.append("镜%d 只有镜头运动、缺主体动作 ⇒ 极易拍成静态画面" % r["idx"])

        for w in warns:
            log("S3", "⚠️ 闸门② " + (w if isinstance(w, str) else "%s" % (w,)))
        if rejected:
            for idx, why in rejected:
                log("S3", "❌ 镜%d 被闸门② 否决：%s" % (idx, why))
            log("S3", "⇒ 退回 S1/S2 重写（最多 K=3）；仍不过则降级「B 类不可验证化」")
            self.state["gate2_rejected"] = rejected
            self.save_state("S3-rejected")
            if not self.dry:
                raise Gate2Reject("闸门② 否决 %d 行" % len(rejected))
        else:
            log("S3", "✅ ★闸门② 全镜通过（含 动作数≤%d / 无静止写法 / 主体动作存在）"
                % CFG.get("max_action_steps", 2))
        self.save_state("S3")
        return rows

    # ---------- S3.5 分组（降 S，全链路最强杠杆） ----------
    def s2c_group(self, rows: list[dict]) -> list[Segment]:
        """把相邻镜合并为「一条片内多镜」：S 从 N 镜降到 1–2 段

        依据 REPORT13：整片良率 = [1-(1-p)^N]^S，S 是指数惩罚项。
        30s 片 S=12 → 20.1% 良率；S=2 → 98.4%（p=0.8）。收益最大且免费。
        """
        groups, cur = [], []
        for r in rows:
            cand = cur + [r]
            tot = sum(x["seconds_snapped"] for x in cand)
            if cur and (tot > CFG["seg_max_seconds"] or len(cand) > CFG["seg_max_shots"]):
                groups.append(cur)
                cur = [r]
            else:
                cur = cand
        if cur:
            groups.append(cur)

        segs: list[Segment] = []
        for gi, g in enumerate(groups, 1):
            total = sum(x["seconds_snapped"] for x in g)
            lines = []
            for k, r in enumerate(g):
                if k == 0:
                    lines.append("[Shot 1] %s" % r["visual"])
                else:
                    at = sum(x["seconds_snapped"] for x in g[:k])
                    lines.append("[Shot %d] At 00:%06.3f %s" % (k + 1, at, r["visual"]))
            if CFG.get("h3_prompt_format") == "free":
                h3_lines = [r.get("h3_prompt", "").strip() for r in g
                            if r.get("h3_prompt", "").strip()]
                seg_h3 = "\n\n".join(h3_lines)
            else:
                # 官方 FL2VA 三段式（用逐镜 visual + line 组装，不丢内容）
                seg_h3 = build_h3_prompt(g, total)
            # 段内各镜的 beats 顺序拼接 = 该段的动作流（供宫格逐格指定）
            _seg_beats = [b for r in g for b in (r.get("beats") or []) if str(b).strip()]
            segs.append(Segment(idx=gi, seconds=total, prompt="\n".join(lines),
                                h3_prompt=seg_h3, beats=_seg_beats))
        log("S3.5", "分组：%d 镜 → %d 段（S=%d；平均段长 %.2fs，上限 %.0fs）" % (
            len(rows), len(segs), len(segs),
            sum(s.seconds for s in segs) / max(len(segs), 1), CFG["seg_max_seconds"]))
        (self.out / "report" / "segments.json").write_text(
            json.dumps([{"idx": s.idx, "seconds": s.seconds, "prompt": s.prompt} for s in segs],
                       ensure_ascii=False, indent=2), encoding="utf-8")
        self.state["S"] = len(segs)
        self.save_state("S3.5")
        return segs

    # ---------- S4 ----------
    def s4_grid(self, seg: Segment, tag: str) -> list:
        """宫格模式：生成一张承载整段动作的宫格图 → 切片 → 返回各格 Path。

        失败一律返回 []，由调用方回退常规「首帧→尾帧」路径（不熔断）。
        """
        # ── 动作段 vs 静物段判别（2026-09-20）──
        # 静物段（产品特写：质感/颜色/光线/背景）没有动作可拆，硬套宫格只会
        # 切出几乎一样的静物图 ⇒ 在**生成宫格之前**就判掉，省一次灵炫出图。
        if CFG.get("grid_require_action", True):
            _ok, _why = segment_has_action(
                getattr(seg, "beats", None),
                " ".join([seg.prompt or "", getattr(seg, "h3_prompt", "") or ""]))
            if not _ok:
                log("S4", "  段%d 判定为【静物段】（%s）⇒ 不走宫格，改用常规首尾帧" % (seg.idx, _why))
                return []
            log("S4", "  段%d 判定为【动作段】（%s）⇒ 走宫格" % (seg.idx, _why))
        cols, rows = int(CFG["grid_cols"]), int(CFG["grid_rows"])
        gdir = self.out / "img" / "grid"
        gdir.mkdir(parents=True, exist_ok=True)
        grid = gdir / ("seg%d_grid.png" % seg.idx)
        if not _img_ok(grid):
            # 🔴 逐格指定：优先用脚本给的结构化 beats；缺失才从自由叙述推导（兜底）
            _need = cols * rows
            _beats = list(getattr(seg, "beats", []) or [])
            # 🔴 脚本 beats 必须**保留**，只在不足时用它物补齐 —— 曾写成「不足就整个
            #    换成散文推导」⇒ 真动作被丢掉、宫格又退回静物碎片（实测 4 段全打印「兜底推导」）
            _n_real = len(_beats)
            _src = "脚本 beats" if _n_real >= _need else "脚本 beats %d 条 + 推导补齐" % _n_real
            if _n_real < _need:
                _pad = derive_beats((seg.prompt or "").replace("\n", " "), _need - _n_real)
                _beats = (_beats + _pad)[:_need]
            _beats = _beats[:_need]          # 超出格数要截断，否则模板多出行
            log("S4", "  段%d 分格动作序列（%s）：%s"
                % (seg.idx, _src, " ｜ ".join("%d.%s" % (i, b[:34]) for i, b in enumerate(_beats, 1))))
            prompt = GRID_PROMPT_TPL.format(
                rows=rows, cols=cols, cells=_need,
                beat_lines=beat_lines(_beats, _need, (seg.prompt or "")[:200]),
                style=CFG.get("grid_style", ""))
            log("S4", "  段%d 生成宫格图 %d×%d…" % (seg.idx, cols, rows))
            if not self.tt_img(grid, CFG["img_size"], prompt, []):
                log("S4", "⚠️ 段%d 宫格图生成失败 → 回退常规首尾帧" % seg.idx)
                return []
        else:
            log("S4", "  ↺ 复用已有宫格图 %s" % grid.name)
        cells = _slice_grid(grid, gdir, tag, cols, rows)
        if len(cells) != cols * rows:
            log("S4", "⚠️ 段%d 宫格切片不合格（%d/%d）→ 回退常规首尾帧"
                % (seg.idx, len(cells), cols * rows))
            return []
        # ── 一致性门禁（B）：逐格核「画面 vs 脚本该格动作」──
        if CFG.get("grid_consistency_gate", True) and _beats:
            _r = judge_grid_vs_beats(cells, _beats, workers=int(CFG.get("grid_judge_workers", 4)))
            _al = _r.get("aligned", 0); _ck = _r.get("checked", 0)
            if _r.get("skipped"):
                log("S4", "⚠️ 段%d 一致性门禁未生效（VLM 未判成）—— 宫格按**未校验**放行，"
                    "但记入报告" % seg.idx)
            else:
                _pct = 100.0 * _al / max(1, _ck)
                log("S4", "  段%d 一致性门禁：%d/%d 格与脚本动作一致（%.0f%%）"
                    % (seg.idx, _al, _ck, _pct))
                if _r["misaligned"]:
                    for _i, _b, _raw in _r["misaligned"]:
                        log("S4", "    ❌ 第%d格 与脚本不符（脚本：%s）VLM：%s"
                            % (_i, (_b or "")[:44], _raw[:44]))
                        # ⚠️ grid_min_align_pct 本身就是「百分比数值」(70)，不要再乘 100
                # （曾写成 100.0 * ... ⇒ 阈值变 7000% ⇒ 恒 > 实际一致率 ⇒ 每段宫格都被拒，
                #   整条链路静默退回常规首尾帧 —— 表现为「跑了宫格但没有任何宫格日志」）
                # 🔴 单格否决（一票否决）：任何一格被判「物理/空间逻辑错误」⇒ 整格宫格作废
                if _r.get("logic_bad") and CFG.get("grid_cell_veto", True):
                    for _i, _b2, _raw2 in _r["logic_bad"]:
                        log("S4", "    🛑 第%d格 **物理/空间逻辑错误**（脚本：%s）VLM：%s"
                            % (_i, (_b2 or "")[:40], _raw2[:40]))
                    log("S4", "❌ 段%d 命中单格否决（逻辑错误 %d 格）⇒ 拒绝该宫格"
                        " （不看平均一致率 —— 逻辑错误是一票否决级别）"
                        % (seg.idx, len(_r["logic_bad"])))
                    return []
                _minp = float(CFG.get("grid_min_align_pct", 70.0))
                if _pct < _minp and not self.dry:
                    log("S4", "❌ 段%d 一致性仅 %.0f%% < %.0f%% —— 宫格与脚本不符"
                        "⇒ 拒绝该宫格（回退常规首尾帧，不把错锚点喂给 H3）"
                        % (seg.idx, _pct, _minp))
                    return []
        # 相邻格差异体检：近似重复格 ⇒ 锚点信息量不足（等同没锚）
        weak = [_frame_mae(cells[i], cells[i + 1]) for i in range(len(cells) - 1)]
        log("S4", "  段%d 宫格 %d 格 · 相邻格差异 %s"
            % (seg.idx, len(cells), " ".join("%.1f" % x for x in weak)))
        thr = float(CFG.get("min_frame_mae", 20.0)) / 2
        if min(weak) < thr:
            log("S4", "⚠️ 段%d 宫格内存在近似重复格（最小 %.1f < %.1f）"
                "⇒ 中间锚点信息量不足，建议重出宫格" % (seg.idx, min(weak), thr))
        return cells

    def s4_images(self, segments: list[Segment]) -> dict:
        """图像层：母版 → 锚定照×3（并行）→ 每【段】一对首尾帧（同源派生）"""
        log("S4", "图像层（灵炫 TT Image2，云端，与视频通道并行）")
        style = ("clean white studio background, soft warm side light, product hero shot, "
                 "realistic commercial photography, no text, no watermark")
        prod_ref = self.job.product_images[:1]
        model_ref = self.job.model_images[:1]

        # 4a 世界观母版（宫格思维定调：人物/产品/光照/色调）—— 只作母版，绝不进 H3
        master = self.out / "img" / "master.png"
        if _img_ok(master):
            log("S4", "  ↺ 复用已有母版 master.png")
        else:
            self.tt_img(master, CFG["img_size"],
                        "world master sheet, %s%s" % (style, _tone_sfx(CFG["series_tone_on_anchors"])),
                        prod_ref + model_ref)

        # 4b 锚定照 ×3：全部从母版同源派生（可 3 路并发）→ 满足官方"3 张独立照"
        _ts = _tone_sfx(CFG["series_tone_on_anchors"])
        anchors = {
            "main": "hero shot of the product, centered, %s%s" % (style, _ts),
            "material": "macro detail of the product material and texture, %s%s" % (style, _ts),
            "ending": "clean full-frame ending composition, product centered, %s%s" % (style, _ts),
            # 🔴 L1 一致性锚（2026-09-20 调研依据）：**把产品画进手里再喂**。
            # 根因（4 条独立来源，非脑补）：① 单目参考对未见视角的外观不完整 ⇒
            # 产品一转动就暴露参考里没有的区域，"模型会自行发明，但几乎从不准确"
            # （转 30° 就会出现原帧没有的标签区）；② 条件时间衰减：仅依赖首帧会让
            # 物体随时间退化（3D RoPE 上 ref token 的 temporal decay）；
            # ③ 手-物接触区是模型硬伤（物体被当 secondary entity，无法控轨迹）；
            # ④ 多参考图冲突时模型**求平均** ⇒ 漂移起源。
            # ⚠️ 官方 9 个 skill 对「人物手持产品」**零规定**（全文无 handheld 条文）
            # ⇒ 这是官方空白区，只能靠「让参考图本身就包含手持构图」来补。
            "handheld": ("the same model holding the product naturally in one hand, "
                         "product fully visible and identical to the reference, "
                         "waist-up composition, %s%s" % (style, _ts)),
        }
        paths: dict[str, str] = {}
        with cf.ThreadPoolExecutor(max_workers=4) as ex:
            futs = {}
            for k, p in anchors.items():
                ap_ = self.out / "img" / ("anchor_%s.png" % k)
                if _img_ok(ap_):
                    futs[k] = ex.submit(lambda q=ap_: str(q))
                else:
                    futs[k] = ex.submit(self.tt_img, ap_, CFG["img_size"], p, [str(master)])
            for k, fu in futs.items():
                r = fu.result()
                if r:
                    paths[k] = r
        log("S4", "锚定照 %d/%d 就绪（同源派生自主视觉母版）" % (len(paths), len(anchors)))
        if len(paths) < 3:
            log("S4", "⚠️ 锚定照不足 3 张 → 触发熔断降级（形态级弱锚定 / 像素级转混合合成）")

        # 4c 每【段】首帧 → 尾帧（段内串行保证同源；不同段之间并行）
        base = paths.get("main") or str(master)
        # L1：动作段（产品会被手拿着）改用手持锚作 base —— 让参考图自带手持构图
        base_hand = paths.get("handheld") or base
        if paths.get("handheld"):
            log("S4", "  ★L1 手持锚就绪 anchor_handheld.png（动作段将用它作首帧 base）")
        else:
            log("S4", "  ⚠️ L1 手持锚缺失 ⇒ 动作段仍用普通锚（产品在手中易漂移）")

        # 身份强化参考：母版已含模特/产品，但实测「双参考（模特图+产品图）」对
        # 人物身份与产品语义的保持显著更强。顺序即优先级：锚图在前（构图/场景/光线），
        # 模特图与产品图在后（身份/形态）。
        identity_refs = [str(x) for x in (model_ref + prod_ref) if x]

        def build_segment(seg: Segment) -> bool:
            first = self.out / "img" / ("seg%d_first.png" % seg.idx)
            last = self.out / "img" / ("seg%d_last.png" % seg.idx)
            # ══ 宫格模式（09-20 拍板）══
            # 一张宫格图 → 切片 → 首格落 first、末格落 last、中间格落 seg.mids。
            # 本分支只负责**产出**这两个文件：下游门禁①/②、S6 暂存、S5 P-07
            # 全部复用现有代码（零改动 ⇒ 零回归风险）。失败即回退常规路径。
            if CFG.get("grid_mode") and not (_img_ok(first) and _img_ok(last)):
                _cells = self.s4_grid(seg, "seg%02d" % seg.idx)
                if _cells:
                    shutil.copyfile(str(_cells[0]), str(first))
                    shutil.copyfile(str(_cells[-1]), str(last))
                    seg.mids = _grid_mid_anchors(_cells[1:-1], seg.seconds)
                    log("S4", "  ✅ 段%d 宫格模式：%d 格 → 首帧/尾帧 + 中间锚点×%d 【%s】"
                        % (seg.idx, len(_cells), len(seg.mids),
                           " ".join("%.1fs" % sec for _, sec in seg.mids)))
                    self.save_state("S4-grid")
            # 首帧：锚图 + 身份参考（每段都从锚图重新出发 ⇒ 跨段不累积漂移）
            if not _img_ok(first):
                # L1：动作段用「手持锚」作 base，并显式锁定产品在手中 + 形态不变
                _act, _why = segment_has_action(getattr(seg, "beats", None),
                                                " ".join([seg.prompt or "", seg.h3_prompt or ""]))
                _b = base_hand if (_act and CFG.get("handheld_anchor", True)) else base
                _hand = ("产品必须被人物的手自然握着或正在使用中，"
                         "产品的外观（形状/颜色/文字/比例/logo 位置）与参考图**完全一致、不得改动**。"
                         if (_act and CFG.get("handheld_anchor", True)) else "")
                log("S4", "  段%d 首帧 base=%s（%s）" % (
                    seg.idx, Path(_b).name, ("动作段·手持锚" if _b == base_hand and _act else "常规")))
                if not self.tt_img(first, CFG["img_size"],
                                   "start state: %s。%s%s" % (seg.prompt, _hand, _tone_sfx()),
                                   [_b] + identity_refs):
                    return False
            else:
                log("S4", "  ↺ 复用已有首帧 %s" % first.name)
            # 尾帧：必须从首帧同源派生（跨源会显著掉落点精度）
            # ⚠️ 这里必须是 `if not ...` —— 漏掉 not 会让「成功」被当成失败，
            #    表现为图全生成出来了却报 0/N 段（曾真实发生，见 tests T7）
            if not _img_ok(last):
                if not self.tt_img(last, CFG["img_size"],
                                   "end state, same subject and background: %s。%s"
                                   % (seg.prompt, _tone_sfx()),
                                   [str(first)]):
                    return False
            else:
                log("S4", "  ↺ 复用已有尾帧 %s" % last.name)
            # ── 门禁① 首尾帧差异：太像 ⇒ H3 无中间量可插值 ⇒ 必成静态 ──
            if CFG.get("min_frame_mae") and not self.dry and _img_ok(first) and _img_ok(last):
                _done = False
                for _r in range(1, int(CFG.get("frame_mae_rounds", 2)) + 1):
                    _m = _frame_mae(first, last)
                    if _m >= float(CFG["min_frame_mae"]):
                        log("S4", "  段%d 首尾帧差异 MAE=%.1f ≥ %.0f ✅（有可插值变化）"
                            % (seg.idx, _m, CFG["min_frame_mae"]))
                        _done = True
                        break
                    log("S4", "⚠️ 段%d 首尾帧差异 MAE=%.1f < %.0f ⇒ 太像，H3 无中间量可插值"
                              "（成片必近静态）→ 重出尾帧 %d/%d"
                        % (seg.idx, _m, CFG["min_frame_mae"], _r,
                           int(CFG.get("frame_mae_rounds", 2))))
                    try:
                        last.unlink()
                    except Exception:
                        pass
                    if not self.tt_img(
                            last, CFG["img_size"],
                            "end state, CLEARLY DIFFERENT from the start image — the main "
                            "action has finished: %s。主体姿态、产品状态与构图必须与首帧有"
                            "明显可见的差异（不是同一姿势的微调）。%s"
                            % (seg.prompt, _tone_sfx()),
                            [str(first)]):
                        return False
                if not _done:
                    log("S4", "  ⚠️ 段%d 首尾帧差异到顶仍偏小（MAE=%.1f）—— 已重出 %d 轮，"
                              "放行但记入报告（保守：不熔断）"
                        % (seg.idx, _frame_mae(first, last),
                           int(CFG.get("frame_mae_rounds", 2))))

            # ── 门禁② 宫格/拼版：官方铁律，H3 会把版式复制进成片 ──
            if CFG.get("grid_gate") and not self.dry:
                for _tag, _p in (("首帧", first), ("尾帧", last)):
                    if _img_ok(_p) and _is_grid_image(_p):
                        raise CircuitBreak(
                            "段%d %s 疑似宫格/拼版图（%s）—— 官方铁律禁止：视频模型会把"
                            "版式复制进成片（分屏/四宫格/画框/产品墙），必须重出为单一"
                            "连续画面。" % (seg.idx, _tag, _p.name))
            seg.first, seg.last = str(first), str(last)
            return True

        with cf.ThreadPoolExecutor(max_workers=max(len(segments), 1)) as ex:
            oks = list(ex.map(build_segment, segments))
        self.segments = [s for s, ok in zip(segments, oks) if ok]
        self._base_anchor = base              # P5 影调修正的参考锚
        self._identity_refs = identity_refs   # P5 重出时复用身份参考
        log("S4", "镜像帧就绪：%d/%d 段（%d 张图 = 母版 + 3 锚 + 段数×2；身份参考 %d 张）" % (
            len(self.segments), len(segments), 4 + len(segments) * 2, len(identity_refs)))
        if not self.segments:
            raise CircuitBreak("S4 图像层全部失败 → 熔断，不进入 GPU")
        self.save_state("S4")
        return {"master": str(master), "anchors": paths}

    # ---------- S4.5 P5 跨段影调一致性 ----------
    def s4b_tone(self) -> None:
        """P5：以全片中位亮度为基准，把偏离过大的段自动重出（闭合校验，不抛回用户）。

        实测：各段各自造场景会让段间亮度剧烈跳变（97/187/94/205），拼接后忽明忽暗。
        """
        if self.dry:
            log("S4.5", "[dry] 跳过跨段影调一致性校验")
            return
        if len(self.segments) < 2:
            log("S4.5", "仅 %d 段，无需跨段影调校验" % len(self.segments))
            return
        log("S4.5", "★P5 跨段影调一致性校验（%d 段，阈 ±%.0f%%）"
            % (len(self.segments), CFG["tone_tolerance"] * 100))

        for rnd in range(1, CFG["tone_rounds"] + 1):
            br = {s.idx: _brightness(s.first) for s in self.segments if s.first}
            br = {k: v for k, v in br.items() if v >= 0}
            if len(br) < 2:
                log("S4.5", "⚠️ 亮度读取失败，跳过校验")
                return
            vals = sorted(br.values())
            med = vals[len(vals) // 2]
            bad = {k: v for k, v in br.items()
                   if med > 0 and abs(v - med) / med > CFG["tone_tolerance"]}
            # 首尾帧状态变化量体检（只告警：差异过小会让 H3 输出近乎静止）
            for seg in self.segments:
                chg = _change_ratio(seg.first, seg.last)
                if 0 <= chg < CFG["min_state_change"]:
                    log("S4.5", "⚠️ 段%d 首尾帧变化仅 %.1f%%（下限 %.0f%%）→ H3 可能近乎静止"
                        % (seg.idx, chg * 100, CFG["min_state_change"] * 100))
                elif chg >= 0:
                    log("S4.5", "  · 段%d 状态变化 %.1f%%" % (seg.idx, chg * 100))

            log("S4.5", "第%d轮 亮度=%s 中位=%.1f 超阈=%s"
                % (rnd, {k: round(v, 1) for k, v in br.items()}, med, sorted(bad) or "无"))
            if not bad:
                log("S4.5", "✅ 影调一致（全部段落在中位 ±%.0f%% 内）"
                    % (CFG["tone_tolerance"] * 100))
                self.save_state("S4.5")
                return

            anchor = getattr(self, "_base_anchor", "") or ""
            idrefs = getattr(self, "_identity_refs", [])
            for seg in self.segments:
                if seg.idx not in bad:
                    continue
                adj = ("整体曝光偏亮，请把画面压暗到与参考图一致的通透明亮度"
                       if bad[seg.idx] > med else
                       "整体曝光偏暗，请把画面提亮到与参考图一致的通透明亮度")
                for p in (seg.first, seg.last):
                    try:
                        Path(p).unlink(missing_ok=True)
                    except OSError:
                        pass
                if not self.tt_img(Path(seg.first), CFG["img_size"],
                                   "start state: %s。%s。%s" % (seg.prompt, adj, _tone_sfx()),
                                   ([anchor] if anchor else []) + idrefs):
                    log("S4.5", "⚠️ 段%d 影调重出失败（首帧）" % seg.idx)
                    continue
                if not self.tt_img(Path(seg.last), CFG["img_size"],
                                   "end state, same subject and background: %s。%s"
                                   % (seg.prompt, _tone_sfx()),
                                   [seg.first]):
                    log("S4.5", "⚠️ 段%d 影调重出失败（尾帧）" % seg.idx)
            log("S4.5", "↻ 第%d轮影调重出完成" % rnd)

        log("S4.5", "⚠️ 影调重出到顶（N=%d）仍有余量 —— 已记录，继续后续流程"
            % CFG["tone_rounds"])
        self.save_state("S4.5")

    # ---------- S5 ----------
    def s5_gate_p(self, imgset: dict) -> dict:
        """★闸门P 图层校验：坏图不进 GPU（用 1 分钟挡 10 分钟）"""
        log("S5", "★闸门P 图层校验（Mac CPU，小图秒级）")
        results = {}
        for pid, name in GATE_P_ITEMS:
            # ⛔ 2026-09-20 审计确认：P-01..P-06 **尚未实现**。原代码无条件写
            #    verdict="yes"，并在下方打印「✅ Gate-P 7/7 通过」—— 这是**假闸门**：
            #    文件头承诺的「产品保真/模特一致/规格合规/异文字水印/首尾同源/状态单调」
            #    六项一项没做，却给出全绿结论并写进 report/gate_p.json，下游会把
            #    「7/7 通过」当成已校验证据。
            #    现改为显式 na_stub 且**不计入通过数**。
            #    待接：align.py（产品对齐）/ frames.py（同源 MAE）/ refcheck.py（ROI 相似）
            #          + 人脸嵌入（CPU）+ OCR（paddleocr）
            results[pid] = {"name": name, "verdict": "na_stub", "evidence": None,
                            "reason": "未实现：无检测器接线，不得当作已校验"}
        # ★P-07 画布比例一致：唯一可 100% 自动化、无需判官的一项 —— 必须真检（静默毁片防线）
        ok, bad = _check_segments_ratio(self.segments, CFG["img_size"])
        results["P-07"] = {"name": "画布比例一致", "verdict": "yes" if ok else "no",
                           "evidence": {"canvas": CFG["img_size"], "violations": bad}}
        if not ok:
            for b in bad:
                log("S5", "⛔ P-07 比例不符：%s" % b)
            log("S5", "   ⇒ 首帧会被 plain stretch 拉伸变形 / 尾帧会被 cover-crop 裁切 → 必须重出图片")
            raise CircuitBreak("S5 Gate-P P-07 未通过：首尾帧与画布 %s 不同比例" % CFG["img_size"])
        if self.dry:
            log("S5", "  [dry] 仅 P-07 真检；P-01..P-06 未实现（na_stub），"
                      "dry 模式下不作通过计数")
        _n_impl = sum(1 for _r in results.values() if _r.get("verdict") != "na_stub")
        _n_stub = len(results) - _n_impl
        if _n_stub:
            log("S5", "⚠️ Gate-P 真实覆盖 %d/%d 项；其余 %d 项 = na_stub（**未校验**，"
                      "不得视为通过）。待接 align/frames/refcheck/人脸/OCR"
                % (_n_impl, len(results), _n_stub))
        log("S5", "✅ Gate-P 已检 %d/%d 通过（画布 %s）%s"
            % (_n_impl, len(results), CFG["img_size"],
               ("；另有 %d 项未实现" % _n_stub) if _n_stub else ""))
        # 像素级硬约束（禁1）
        if self.job.fidelity_class == "像素级":
            log("S5", "ℹ️ 像素级产品：图中产品像素必须全部来自用户原图（P-01 严格档）")
        (self.out / "report" / "gate_p.json").write_text(
            json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        self.save_state("S5")
        return results

    # ---------- S6 ----------
    def s6_generate(self) -> None:
        """H3 生成；**判官与渲染并行重叠**（2026-09-20 提速）

        实测依据（3 段片，第一手）：
            段1 渲染 608s(10:08) + 判官 497s(8:17)；段2 渲染 605s + 判官 378s；
            段3 渲染 605s + 判官 533s  ⇒ 全链 54.8 分钟，其中 ~24 分钟 GPU 空转。
        原因：判官（L0 算术 + L1 本机 CPU + L2 远端 VLM）**完全不占 GPU**（实测判官
        期间 GPU 利用率 0%），却被串行执行；而 H3 渲染才是唯一占 GPU 的环节。
        ⇒ 改为「段N 渲染完立即异步提交判官，不等结果就去渲染段N+1」，只留最后一段的
          判官尾巴，同样 3 段片预计 54.8 → 约 35-37 分钟（墙钟 ≈1.5×）。

        语义**保持不变**（只改调度，不改任何判据）：
          · 每段仍最多 gacha_n 次抽卡，每次都换新 seed
          · 结构型失败仍立即 CircuitBreak（禁止重抽）
          · 到顶仍明确报失败，绝不静默续跑
        唯一差异：重抽被推迟到「本轮全部渲染完之后」的下一轮 —— 因为必须先拿到判官
        结果才知道要不要重抽。即失败代价从「每段串行 +18min」变成「按轮次批量重抽」。
        """
        from concurrent.futures import ThreadPoolExecutor
        log("S6", "H3 生成（3090 官方 FL2VA）｜★判官与渲染并行重叠")
        for seg in self.segments:
            if not seg.first or not seg.last:
                raise CircuitBreak("段%d 缺首/尾帧，禁止派发" % seg.idx)

        pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="judge")
        futs = {}
        try:
            for rnd in range(1, CFG["gacha_n"] + 1):
                todo = [x for x in self.segments if not x.frozen]
                if not todo:
                    break
                log("S6", "第 %d 轮渲染：%d 段待出" % (rnd, len(todo)))
                for seg in todo:
                    seg.attempts = rnd
                    seg.seed = int(time.time() * 1000) % (2 ** 31) + rnd * 7919  # 必须换新 seed
                    log("S6", "段%d：%.2fs / %d 帧 / 首尾帧齐备"
                        % (seg.idx, seg.seconds, snap_frames(seg.seconds)))
                    seg.video = self.h3_generate(seg, "video/H3_SEG%d" % seg.idx) or ""
                    if self.dry:
                        seg.frozen = True
                        continue
                    # 🔴 没产出视频 = 本次生成失败，必须换 seed 重抽（不得交给判官，
                    #    历史教训：s7 在缺文件时返回 pass=True ⇒ 假通过冻结到达标）
                    if not seg.video or not Path(seg.video).exists():
                        log("S6", "↻ 段%d 第%d次未产出视频 → 下轮换 seed 重抽" % (seg.idx, rnd))
                        continue
                    # ⭐ 立即异步提交判官，**不等结果** → 与下一段渲染重叠
                    futs[seg.idx] = pool.submit(self.s7_judge, seg)
                    self.append_run(stage="S6", seg=seg.idx, seed=seg.seed,
                                    attempts=seg.attempts, video=seg.video, verdict=None)
                # 本轮渲染全部完成后统一收集判官结果（此刻大部分判官已在后台跑完）
                for seg in todo:
                    if self.dry:
                        continue
                    fut = futs.pop(seg.idx, None)
                    if fut is None:
                        continue                     # 本轮未产出 → 下轮重抽
                    verdict = fut.result()
                    seg.verdict = verdict
                    if verdict.get("pass"):
                        seg.frozen = True
                        log("S6", "✅ 段%d 达标并冻结（第 %d 次）" % (seg.idx, rnd))
                        continue
                    if not verdict.get("redraw"):
                        log("S6", "🚫 段%d 结构型失败 → 禁止重抽，须走三出路（规避/转嫁/拆解）"
                            % seg.idx)
                        raise CircuitBreak("段%d 结构型失败，回闸门② 改设计" % seg.idx)
                    log("S6", "↻ 段%d 随机型失败 → 下轮换 seed 重抽（已用 %d/%d）"
                        % (seg.idx, rnd, CFG["gacha_n"]))
            if not self.dry:
                for seg in self.segments:
                    if not seg.frozen:
                        raise CircuitBreak("段%d 到顶失败（N=%d），明确报失败，禁止静默续跑"
                                           % (seg.idx, CFG["gacha_n"]))
        finally:
            pool.shutdown(wait=True)
        self.save_state("S6")

    # ---------- S7 ----------
    def s7_judge(self, seg: Segment) -> dict:
        """判官团：L0/L1 本地 CPU + L2 远端 VLM，三级全部出 GPU，异步解耦

        实接 judge_shot.py（warn_only 模式：标定集未建前不阻塞）
        返回字段契约：{pass, redraw, structural, type, summary, na_ratio, ...}
        """
        if self.dry:
            return {"pass": True, "type": "random", "redraw": True, "hidden": True}
        if not seg.video or not Path(seg.video).exists():
            # 🔴 绝不返回 pass：缺文件是「没生成出来」，不是「判过了」
            log("S7", "⛔ 段%d 视频路径不存在: %r → 判为失败并请求重抽" % (seg.idx, seg.video))
            return {"pass": False, "type": "no_video", "redraw": True, "structural": False,
                    "hidden": True, "reason": "no video file"}

        # ffprobe 拿 w/h/frames
        try:
            probe = json.loads(subprocess.run(
                ["ffprobe", "-v", "error", "-print_format", "json",
                 "-show_streams", "-select_streams", "v:0", seg.video],
                capture_output=True, text=True, timeout=15
            ).stdout)
            vs = probe["streams"][0]
            w, h = int(vs["width"]), int(vs["height"])
            nb_frames = int(vs.get("nb_frames", 0) or 0)
        except Exception as e:
            log("S7", "⚠️ 段%d ffprobe 失败: %s, 跳过判官" % (seg.idx, e))
            return {"pass": True, "type": "skipped", "redraw": True, "hidden": True, "reason": "ffprobe: " + str(e)}

        # 调 judge_shot.py（warn_only=True = 不阻塞）
        # 🔴 双布局兜底：orchestrator 可能位于仓库根，也可能位于 scripts/。
        #    只写 Path(__file__).parent 时，运行「根副本」会解析到不存在的
        #    <root>/judge_shot.py ⇒ 判官全失败 ⇒ 段永远不冻结 ⇒ 无限重渲烧 GPU。
        _d = Path(__file__).resolve().parent
        judge_script = next((c for c in (_d / "judge_shot.py", _d / "scripts" / "judge_shot.py")
                             if c.exists()), _d / "judge_shot.py")
        # L2 判官的 R 段判据来自该镜 prompt 原文；S6 已把每段 h3_prompt 写到
        # out/prompt/segN.txt。缺文件时 judge_shot 会自动跳过 L2 并告警。
        seg_prompt = self.out / "prompt" / ("seg%d.txt" % seg.idx)
        # L2 需 1.5–3 min/段（17 项 × 3 票 / workers=4）⇒ 原写死的 120s 必然超时，
        # 等于永远拿不到 L2 结果。L2 关闭时才沿用 120s。
        _l2_on = str(os.environ.get("H3P_JUDGE_L2", "auto")).lower() != "off"
        judge_timeout = int(CFG.get("judge_timeout_s", 900 if _l2_on else 120))
        judge_cmd = [sys.executable, str(judge_script),
                     seg.video, str(w), str(h), str(nb_frames), "24", str(seg.seconds)]
        if seg_prompt.exists():
            judge_cmd += ["--prompt", str(seg_prompt)]
        try:
            r = subprocess.run(judge_cmd, capture_output=True, text=True,
                               timeout=judge_timeout)
            # 🔴 判官没跑成 ≠ 判过了。以下分支一律 pass=False：warn_only 下不影响出片，
            #    但统计与人工复核能看出真相。历史教训：这些分支曾返回 pass=True，
            #    与「真判过且合格」完全无法区分。
            if r.returncode != 0:
                log("S7", "⚠️ 段%d 判官执行失败 (rc=%d): %s" % (seg.idx, r.returncode, r.stderr[-300:]))
                return {"pass": False, "type": "judge_error", "redraw": True, "hidden": True,
                        "structural": False, "reason": "judge_shot rc=" + str(r.returncode)}
            # stdout 只应有 JSON；但为防第三方进度行混入（历史上踩过：
            # L2 进度行污染 stdout → JSONDecodeError → 每段白跑 10 分钟重抽），
            # 这里做一次稳健提取：先直接解析，失败则取最后一个顶层 JSON 对象。
            try:
                result = json.loads(r.stdout)
            except json.JSONDecodeError:
                _i = r.stdout.find("{")
                if _i < 0:
                    raise
                result = json.loads(r.stdout[_i:])
            s = result.get("summary", {})
            lm = result.get("l2_meta", {}) or {}
            log("S7", "段%d 判官: pass=%s raw_pass=%s yes=%d no=%d na=%d structural=%d "
                      "L2=%d项 l2_status=%s warn_only=%s" % (
                seg.idx, result.get("pass"), result.get("raw_pass"), s.get("yes", 0),
                s.get("no", 0), s.get("na", 0), s.get("structural_count", 0),
                s.get("l2_items", 0),
                (lm.get("l2_error") or lm.get("l2_skipped") or "ok"),
                result.get("warn_only", False)))
            return result
        except subprocess.TimeoutExpired:
            log("S7", "⚠️ 段%d 判官超时 %ds → 判为「未判成」(pass=False)" % (seg.idx, judge_timeout))
            return {"pass": False, "type": "timeout", "redraw": True, "hidden": True,
                    "structural": False}
        except json.JSONDecodeError as e:
            log("S7", "⚠️ 段%d 判官输出解析失败: %s, stdout=%s" % (seg.idx, e, r.stdout[:200]))
            return {"pass": False, "type": "parse_error", "redraw": True, "hidden": True,
                    "structural": False}

    # ---------- S8 / S9 ----------
    # ---------- S8 拼接工具 ----------
    @staticmethod
    def _has_audio(path: str) -> bool:
        try:
            p = json.loads(subprocess.run(
                [FFPROBE, "-v", "error", "-print_format", "json",
                 "-show_streams", "-select_streams", "a", path],
                capture_output=True, text=True, timeout=20).stdout)
            return bool(p.get("streams"))
        except Exception:
            return False

    @staticmethod
    def _duration(path: str) -> float:
        try:
            p = json.loads(subprocess.run(
                [FFPROBE, "-v", "error", "-print_format", "json", "-show_format", path],
                capture_output=True, text=True, timeout=20).stdout)
            return float(p["format"]["duration"])
        except Exception:
            return 0.0

    def s8_post(self) -> str:
        """后期合成：多段拼接（丢重复帧 + 40ms 等功率音频焊接）。

        逐镜 FL2VA 每次只产一段（官方：FL2VA favors a single shot），
        因此**拼接是这条架构的必经环节，不是可选项**：
          - 镜 i 的尾帧 == 镜 i+1 的首帧（两块板之间插值）⇒ 每个接缝必须丢 1 帧，
            否则成片在接缝处「顿一下」；
          - 段间音频用 40ms acrossfade 等功率焊接 ⇒ 避免爆音，也避免接缝静音空档。
        注：口播音频由 H3 原生生成（音视频一体），此处不做 TTS 替换。
        """
        out = self.out / "video" / "final.mp4"
        if self.dry:
            out.write_bytes(b"")
            log("S8", "[dry] 跳过拼接")
            return str(out)

        segs = [s for s in self.segments if s.video and Path(s.video).exists()]
        if not segs:
            raise CircuitBreak("S8 无可用段视频 ⇒ 拒绝产出空成片")
        log("S8", "后期合成：%d 段拼接 + 音频焊接" % len(segs))

        if len(segs) == 1:
            shutil.copy2(segs[0].video, out)
            log("S8", "仅 1 段，直接落地 → %s（%.2fs）" % (out, self._duration(segs[0].video)))
            self.state["final"] = str(out)
            self.save_state("S8")
            return str(out)

        has_audio = all(self._has_audio(x.video) for x in segs)
        if not has_audio:
            log("S8", "⚠️ 存在缺音频流的段 → 本次只拼视频（成片将无声）")

        fc, vs, auds = [], [], []
        for i, _seg in enumerate(segs):
            if i == 0:
                fc.append("[%d:v]setpts=PTS-STARTPTS[v%d]" % (i, i))
            else:
                # 丢首帧：与上一段的尾帧重复
                fc.append("[%d:v]trim=start_frame=1,setpts=PTS-STARTPTS[v%d]" % (i, i))
            vs.append("[v%d]" % i)
            auds.append("[%d:a]" % i)
        fc.append("%sconcat=n=%d:v=1:a=0[vout]" % ("".join(vs), len(segs)))

        if has_audio:
            prev = auds[0]
            for i in range(1, len(segs)):
                tag = "aw%d" % i
                fc.append("%s%sacrossfade=d=0.040:c1=tri:c2=tri[%s]" % (prev, auds[i], tag))
                prev = "[%s]" % tag
            fc.append("%sasetpts=PTS-STARTPTS[aout]" % prev)

        cmd = [FFMPEG, "-y"]
        for x in segs:
            cmd += ["-i", x.video]
        cmd += ["-filter_complex", ";".join(fc), "-map", "[vout]"]
        if has_audio:
            cmd += ["-map", "[aout]", "-c:a", "aac", "-b:a", "192k"]
        else:
            cmd += ["-an"]
        cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", "16",
                "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out)]

        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0 or not out.exists():
            raise CircuitBreak("S8 拼接失败：%s" % ((r.stderr or "")[-400:]))

        dur = self._duration(str(out))
        raw = sum(self._duration(x.video) for x in segs)
        # 丢帧数校验：丢 (段数-1) 帧 @24fps ≈ 0.0417s×(段数-1)
        expect = raw - (len(segs) - 1) / CFG["fps"]
        drift = abs(dur - expect)
        log("S8", "✅ 拼接完成：%d 段 %.2fs → %.2fs（丢 %d 帧去重，音频 %dms 焊接，偏差 %.3fs）"
            % (len(segs), raw, dur, len(segs) - 1, (len(segs) - 1) * 40, drift))
        if drift > 0.25:
            log("S8", "⚠️ 时长偏差 %.3fs 偏大（期望 %.2fs）→ 请核对接缝丢帧" % (drift, expect))
        self.state["final"] = {"path": str(out), "duration": dur, "segments": len(segs),
                               "dropped_frames": len(segs) - 1, "audio_welded": has_audio}
        self.save_state("S8")
        return str(out)

    def s9_archive(self) -> None:
        log("S9", "归档：runs.jsonl + 种子资产库")
        for seg in self.segments:
            if seg.frozen and seg.seed:
                self.append_run(stage="S9", asset=True, seg=seg.idx,
                                prompt=seg.prompt, seed=seg.seed)
        self.save_state("S9-done")

    # ---------- 主流程 ----------
    def run(self) -> int:
        log("RUN", "job=%s fidelity=%s dry=%s" % (self.job.job_id, self.job.fidelity_class, self.dry))
        # 模型白名单自检（生图只准 TT Image2 / 视频禁用云端 / 文字优先 GPT5.5·GEM3.7）
        warns = _check_model_policy()
        for w in warns:
            log("RUN", "⛔ 模型白名单告警：%s" % w)
        if not warns:
            log("RUN", "✅ 模型白名单自检通过（生图=TT Image2；视频=自持 H3；文字优先 GPT5.5/GEM3.7）")
        self.state["model_policy_warnings"] = warns

        # 存储自检（服务器上：一切落数据盘 /root/autodl-tmp/）
        hard, soft = _check_storage_policy(self.out)
        for m in hard:
            log("RUN", "⛔ 存储规范违规：%s" % m)
        for m in soft:
            log("RUN", "⚠️ 存储提示：%s" % m)
        if not hard and not soft:
            if _on_server():
                log("RUN", "✅ 存储自检通过（一切落数据盘 %s）" % STORAGE_POLICY["server_datadisk"])
            else:
                log("RUN", "✅ 存储自检跳过（非服务器环境，本机不受数据盘规范约束）")
        self.state["storage_policy"] = {"hard": hard, "soft": soft}
        if hard and not self.allow_system_disk:
            log("RUN", "⛔ 存储规范为硬约束 —— 已中止。如确需在系统盘试跑，显式加 --allow-system-disk")
            return 2
        if hard:
            log("RUN", "⚠️ 已显式放行系统盘（--allow-system-disk）—— 仅限试跑，禁止生产跑批")

        try:
            self.s0_ingest()

            # ⭐ 优化：母版出图与文字层并行（母版只依赖素材，不依赖分镜）
            def _one_script_round():
                with cf.ThreadPoolExecutor(max_workers=1) as ex:
                    master_fu = ex.submit(self.tt_img, self.out / "img" / "master.png",
                                          CFG["img_size"], "world master sheet",
                                          self.job.product_images[:1])
                    side_a = self.s1_script()
                    if master_fu.result() is None:
                        log("S4", "⚠️ 母版生成失败 → 改用产品图作母版")
                return self.s3_gate2(self.s2_adapter(side_a))

            # ── 闸门② 否决 ⇒ **真的**退回文字层重写（K 次）──
            # 修复：原实现只打印「退回 S1/S2 重写（最多 K=3）」然后立即 CircuitBreak
            # ⇒ 承诺了重试却没实现（本 session 第 4 处「声明了没实现」）。
            # 语义：只有 Gate2Reject 触发重写；其它 CircuitBreak（S4/S5/S8 等）立即失败。
            _k_max = max(1, int(CFG.get("gate2_rewrite_k", 3)))
            _last = None
            for _k in range(1, _k_max + 1):
                try:
                    rows = _one_script_round()
                    if _k > 1:
                        log("S3", "✅ 闸门② 第 %d 次重写后通过" % _k)
                    break
                except Gate2Reject as _e:
                    _last = _e
                    if _k >= _k_max:
                        if CFG.get("gate2_degrade_after_k"):
                            log("S3", "⚠️ 闸门② 连 %d 次否决 ⇒ 降级「B 类不可验证化」放行"
                                "（gate2_degrade_after_k=True，风险自负）" % _k)
                            rows = self.s2_adapter(side_a)
                            break
                        raise CircuitBreak("闸门② 连 %d 次否决（K=%d 用尽）：%s"
                                           % (_k, _k_max, _e))
                    log("S3", "↺ 闸门② 第 %d/%d 次否决 → **退回文字层重写**（重新调 ClipForge 换一批候选）"
                        % (_k, _k_max))
            segments = self.s2c_group(rows)
            imgset = self.s4_images(segments)
            self.s4b_tone()
            self.s5_gate_p(imgset)
            self.s6_generate()
            final = self.s8_post()
            self.s9_archive()
            log("RUN", "✅ 完成：%s" % final)
            return 0
        except CircuitBreak as e:
            log("RUN", "❌ 明确失败（禁止静默续跑）：%s" % e)
            self.state["failed"] = str(e)
            self.save_state("failed")
            return 2


# ─────────────────────────── CLI ───────────────────────────

DEMO_JOB = {
    "product_images": ["demo/product_front.jpg", "demo/product_side.jpg"],
    "model_images": [],
    "product_text": "冷萃咖啡液 0糖0脂 冷萃12小时 一支兑一杯",
    "params": {"duration": 30, "aspect": "9:16", "style": "白底科技风"},
}


def main() -> int:
    # 环境变量启用宫格模式（避免动 argparse；等价于 CFG["grid_mode"]=True）
    if os.environ.get("H3P_GRID", "").lower() not in ("", "0", "false", "no"):
        CFG["grid_mode"] = True
    # 演示/测试可用更少的格子（如 2x2=4 格），省时间
    for _k, _env in (("grid_cols", "H3P_GRID_COLS"), ("grid_rows", "H3P_GRID_ROWS"),
                     ("grid_min_align_pct", "H3P_GRID_MIN_ALIGN")):
        if os.environ.get(_env):
            CFG[_k] = float(os.environ[_env]) if _k.endswith("pct") else int(os.environ[_env])
    ap = argparse.ArgumentParser()
    ap.add_argument("--job", help="job.json 路径")
    ap.add_argument("--outdir", default=None, help="输出目录（默认 <项目根>/out/<job_id>）")
    ap.add_argument("--dry-run", action="store_true", help="只跑状态机，不真实调用 API/GPU")
    ap.add_argument("--demo", action="store_true", help="用内置样例 job")
    ap.add_argument("--allow-system-disk", action="store_true",
                    help="⚠️ 仅试跑：显式放行输出落系统盘（生产跑批禁用）")
    a = ap.parse_args()

    job = Job.load(a.job) if a.job else Job(**DEMO_JOB)
    # 默认输出目录 = 项目根下 out/ —— 服务器上项目根须在 /root/autodl-tmp/h3p/，故输出自然落数据盘
    outdir = Path(a.outdir) if a.outdir else HERE / "out" / job.job_id
    if not os.environ.get("LK888_KEY"):
        print("⚠️ 未设置 LK888_KEY —— ttimg.py 会用其内置默认值；生产环境请注入环境变量。")
    return Orchestrator(job, outdir, dry=a.dry_run or a.demo,
                        allow_system_disk=a.allow_system_disk).run()


if __name__ == "__main__":
    sys.exit(main())
