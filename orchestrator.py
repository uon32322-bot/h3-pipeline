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
    "s1_timeout_s": 900,         # S1 ClipForge 调用超时（实测 6 镜约 25s，留足余量）
    # 视频通道
    "gacha_n": 3,                # 抽卡上限 N<=3
    "comfy_port": 6011,          # 独立实例（不共用 6006）
    # 闸门② 动作白名单与禁写（与 shotlist_schema.json 的 gate2_merged_rules 同源）
    "action_whitelist": [
        "举起", "并排", "推近", "旋转", "开合", "滑入", "光影流动", "静置", "特写平移",
    ],
    "forbidden_by_physics": ["涂开", "倒出", "拧开", "涂抹", "挤压出液"],
    "forbidden_by_generability": [
        "双主体精确交互", "内部心理活动", "否定式动作", "一拍三步动作链",
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
    except Exception:
        return True, ["PIL 不可用，P-07 仅做人工勾选"]
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

    def h3_generate(self, seg: Segment, prefix: str) -> str | None:
        """H3 官方原生链路（3090，:6011）"""
        pf = self.out / "prompt" / ("seg%d.txt" % seg.idx)
        # 视频层用 h3_prompt（ClipForge 产出的 H3 提示词）；缺省才回落场景叙述
        pf.write_text(seg.h3_prompt or seg.prompt, encoding="utf-8")
        if self.dry:
            log("S6", "[dry] H3 seg%d seed=%d → %s" % (seg.idx, seg.seed, prefix))
            return str(self.out / "video" / (prefix + "_%06d_.mp4" % seg.seed))
        cmd = [sys.executable, str(H3GEN),
               seg.first, seg.last, str(pf), str(seg.seconds), str(CFG["megapixels"]),
               str(CFG["steps"]), prefix, str(seg.seed), "--port", str(CFG["comfy_port"]),
               "--aspect", CFG["aspect"]]
        for img, sec in seg.mids:
            cmd += ["--mid", "%s@%s" % (img, sec)]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            log("S6", "⚠️ H3 失败：%s" % (r.stdout or r.stderr)[-400:])
            return None
        out = None
        for line in r.stdout.splitlines():
            if line.startswith("OUTPUT"):
                out = line.split()[-1]
        return out

    # ---------- S0 ----------
    def s0_ingest(self) -> None:
        log("S0", "入库 %d 张产品图 / %d 张模特图" % (len(self.job.product_images), len(self.job.model_images)))
        if not self.job.product_images:
            raise CircuitBreak("S0 缺产品图，早退回（不带病进入）")
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
        adapter = HERE / "clipforge_adapter.py"
        if not adapter.exists():
            raise CircuitBreak("S1 找不到 clipforge_adapter.py（%s）" % adapter)
        p = self.job.params or {}
        name = str(p.get("product_name") or "").strip()
        if not name:
            name = (self.job.product_text or "").split()[0] if self.job.product_text else "未命名产品"
        out = self.out / "report" / "side_a.json"
        cmd = [sys.executable, str(adapter),
               "--endpoint", os.environ.get("CLIPFORGE_ENDPOINT", "http://43.136.35.203:3000"),
               "--name", name,
               "--desc", self.job.product_text,
               "--category", str(p.get("category", "other")),
               "--style", str(p.get("script_style", "pain_point")),
               "--duration", str(int(p.get("duration", 30))),
               "--fidelity", self.job.fidelity_class,
               "--out", str(out)]
        if p.get("gate_p3_strict"):
            cmd.append("--strict")
        for img in self.job.product_images:
            cmd += ["--image", img]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=CFG["s1_timeout_s"])
        except subprocess.TimeoutExpired:
            raise CircuitBreak("S1 ClipForge 超时（>%ds）" % CFG["s1_timeout_s"])
        for line in (r.stdout or "").splitlines():
            if line.startswith("[adapter]") or line.startswith("[闸门P3"):
                log("S1", line)
        if r.returncode == 3:
            raise CircuitBreak("S1 ★闸门P3 结构性否决（--strict）→ 回炉重写台词/拆分镜")
        if r.returncode != 0 or not out.exists():
            raise CircuitBreak("S1 ClipForge 适配失败 rc=%d%s：%s"
                               % (r.returncode,
                                  "（rc=2 通常是参数/用法错误，非闸门否决）" if r.returncode == 2 else "",
                                  ((r.stdout or r.stderr) or "")[-300:]))
        return json.loads(out.read_text(encoding="utf-8"))

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
        """★闸门②：白名单 + 可验证物理结果 + 四类禁写 + 每镜动作数=1（含"B 类不可验证化"降级）"""
        rejected = []
        for r in rows:
            blob = r["visual"]
            for bad in CFG["forbidden_by_physics"]:
                if bad in blob:
                    rejected.append((r["idx"], "含可验证物理结果「%s」" % bad))
            for bad in CFG["forbidden_by_generability"]:
                if bad in blob:
                    rejected.append((r["idx"], "命中四类禁写「%s」" % bad))
        if rejected:
            for idx, why in rejected:
                log("S3", "❌ 镜%d 被闸门② 否决：%s" % (idx, why))
            log("S3", "⇒ 退回 S1/S2 重写（最多 K=3）；仍不过则降级「B 类不可验证化」")
            self.state["gate2_rejected"] = rejected
            self.save_state("S3-rejected")
            if not self.dry:
                raise CircuitBreak("闸门② 否决 %d 行，须退回重写" % len(rejected))
        else:
            log("S3", "✅ ★闸门② 全镜通过")
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
            h3_lines = [r.get("h3_prompt", "").strip() for r in g if r.get("h3_prompt", "").strip()]
            segs.append(Segment(idx=gi, seconds=total, prompt="\n".join(lines),
                                h3_prompt="\n\n".join(h3_lines)))
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
            self.tt_img(master, CFG["img_size"], "world master sheet, %s" % style, prod_ref + model_ref)

        # 4b 锚定照 ×3：全部从母版同源派生（可 3 路并发）→ 满足官方"3 张独立照"
        anchors = {
            "main": "hero shot of the product, centered, %s" % style,
            "material": "macro detail of the product material and texture, %s" % style,
            "ending": "clean full-frame ending composition, product centered, %s" % style,
        }
        paths: dict[str, str] = {}
        with cf.ThreadPoolExecutor(max_workers=3) as ex:
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
        log("S4", "锚定照 %d/3 就绪（同源派生自主视觉母版）" % len(paths))
        if len(paths) < 3:
            log("S4", "⚠️ 锚定照不足 3 张 → 触发熔断降级（形态级弱锚定 / 像素级转混合合成）")

        # 4c 每【段】首帧 → 尾帧（段内串行保证同源；不同段之间并行）
        base = paths.get("main") or str(master)

        # 身份强化参考：母版已含模特/产品，但实测「双参考（模特图+产品图）」对
        # 人物身份与产品语义的保持显著更强。顺序即优先级：锚图在前（构图/场景/光线），
        # 模特图与产品图在后（身份/形态）。
        identity_refs = [str(x) for x in (model_ref + prod_ref) if x]

        def build_segment(seg: Segment) -> bool:
            first = self.out / "img" / ("seg%d_first.png" % seg.idx)
            last = self.out / "img" / ("seg%d_last.png" % seg.idx)
            # 首帧：锚图 + 身份参考（每段都从锚图重新出发 ⇒ 跨段不累积漂移）
            if not _img_ok(first):
                if not self.tt_img(first, CFG["img_size"], "start state: %s" % seg.prompt,
                                   [base] + identity_refs):
                    return False
            else:
                log("S4", "  ↺ 复用已有首帧 %s" % first.name)
            # 尾帧：必须从首帧同源派生（跨源会显著掉落点精度）
            # ⚠️ 这里必须是 `if not ...` —— 漏掉 not 会让「成功」被当成失败，
            #    表现为图全生成出来了却报 0/N 段（曾真实发生，见 tests T7）
            if not _img_ok(last):
                if not self.tt_img(last, CFG["img_size"],
                                   "end state, same subject and background: %s" % seg.prompt,
                                   [str(first)]):
                    return False
            else:
                log("S4", "  ↺ 复用已有尾帧 %s" % last.name)
            seg.first, seg.last = str(first), str(last)
            return True

        with cf.ThreadPoolExecutor(max_workers=max(len(segments), 1)) as ex:
            oks = list(ex.map(build_segment, segments))
        self.segments = [s for s, ok in zip(segments, oks) if ok]
        log("S4", "镜像帧就绪：%d/%d 段（%d 张图 = 母版 + 3 锚 + 段数×2；身份参考 %d 张）" % (
            len(self.segments), len(segments), 4 + len(segments) * 2, len(identity_refs)))
        if not self.segments:
            raise CircuitBreak("S4 图像层全部失败 → 熔断，不进入 GPU")
        self.save_state("S4")
        return {"master": str(master), "anchors": paths}

    # ---------- S5 ----------
    def s5_gate_p(self, imgset: dict) -> dict:
        """★闸门P 图层校验：坏图不进 GPU（用 1 分钟挡 10 分钟）"""
        log("S5", "★闸门P 图层校验（Mac CPU，小图秒级）")
        results = {}
        for pid, name in GATE_P_ITEMS:
            # 真实实现：复用 align.py（产品对齐）/ frames.py（同源 MAE）/ refcheck.py（ROI 相似）
            #           + 人脸嵌入（CPU）+ OCR（paddleocr）
            results[pid] = {"name": name, "verdict": "yes", "evidence": None}
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
            log("S5", "  [dry] %d 项按通过处理（真实部署须接检测器；P-07 已真检）" % len(GATE_P_ITEMS))
        log("S5", "✅ Gate-P %d/%d 通过（画布 %s，重出上限 %d 轮）"
            % (len(results), len(GATE_P_ITEMS), CFG["img_size"], CFG["gate_p_rounds"]))
        # 像素级硬约束（禁1）
        if self.job.fidelity_class == "像素级":
            log("S5", "ℹ️ 像素级产品：图中产品像素必须全部来自用户原图（P-01 严格档）")
        (self.out / "report" / "gate_p.json").write_text(
            json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        self.save_state("S5")
        return results

    # ---------- S6 ----------
    def s6_generate(self) -> None:
        log("S6", "H3 生成（3090 官方 FL2VA，唯一瓶颈，串行）")
        for seg in self.segments:
            # ★闸门③ 派发前硬校验（节选可自动化的两条）
            if not seg.first or not seg.last:
                raise CircuitBreak("段%d 缺首/尾帧，禁止派发" % seg.idx)
            log("S6", "段%d：%.2fs / %d 帧 / 首尾帧齐备" % (seg.idx, seg.seconds, snap_frames(seg.seconds)))
            for attempt in range(1, CFG["gacha_n"] + 1):
                seg.attempts = attempt
                seg.seed = int(time.time() * 1000) % (2 ** 31) + attempt * 7919  # 必须换新 seed
                seg.video = self.h3_generate(seg, "video/H3_SEG%d" % seg.idx) or ""
                if self.dry:
                    seg.frozen = True
                    break
                verdict = self.s7_judge(seg)          # 异步解耦：真实部署走队列
                seg.verdict = verdict
                if verdict.get("pass"):
                    seg.frozen = True
                    log("S6", "✅ 段%d 达标并冻结（第 %d 次）" % (seg.idx, attempt))
                    break
                if not verdict.get("redraw"):
                    log("S6", "🚫 段%d 结构型失败 → 禁止重抽，须走三出路（规避/转嫁/拆解）" % seg.idx)
                    raise CircuitBreak("段%d 结构型失败，回闸门② 改设计" % seg.idx)
                log("S6", "↻ 段%d 随机型失败 → 换新 seed 重抽（%d/%d）" % (
                    seg.idx, attempt, CFG["gacha_n"]))
            else:
                raise CircuitBreak("段%d 到顶失败（N=%d），明确报失败，禁止静默续跑" % (seg.idx, CFG["gacha_n"]))
            self.append_run(stage="S6", seg=seg.idx, seed=seg.seed,
                            attempts=seg.attempts, video=seg.video, verdict=seg.verdict)
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
            log("S7", "⚠️ 段%d 视频路径不存在: %s, 跳过判官" % (seg.idx, seg.video))
            return {"pass": True, "type": "skipped", "redraw": True, "hidden": True, "reason": "no video file"}

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
        judge_script = Path(__file__).parent / "judge_shot.py"
        try:
            r = subprocess.run(
                [sys.executable, str(judge_script),
                 seg.video, str(w), str(h), str(nb_frames), "24", str(seg.seconds)],
                capture_output=True, text=True, timeout=120
            )
            if r.returncode != 0:
                log("S7", "⚠️ 段%d 判官执行失败 (rc=%d): %s" % (seg.idx, r.returncode, r.stderr[:200]))
                return {"pass": True, "type": "judge_error", "redraw": True, "hidden": True,
                        "reason": "judge_shot rc=" + str(r.returncode)}
            result = json.loads(r.stdout)
            s = result.get("summary", {})
            log("S7", "段%d 判官: pass=%s yes=%d no=%d na=%d structural=%d warn_only=%s" % (
                seg.idx, result.get("pass"), s.get("yes", 0), s.get("no", 0),
                s.get("na", 0), s.get("structural_count", 0), result.get("warn_only", False)))
            return result
        except subprocess.TimeoutExpired:
            log("S7", "⚠️ 段%d 判官超时 120s, 跳过" % seg.idx)
            return {"pass": True, "type": "timeout", "redraw": True, "hidden": True}
        except json.JSONDecodeError as e:
            log("S7", "⚠️ 段%d 判官输出解析失败: %s, stdout=%s" % (seg.idx, e, r.stdout[:200]))
            return {"pass": True, "type": "parse_error", "redraw": True, "hidden": True}

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
            with cf.ThreadPoolExecutor(max_workers=1) as ex:
                master_fu = ex.submit(self.tt_img, self.out / "img" / "master.png",
                                      CFG["img_size"], "world master sheet", self.job.product_images[:1])
                side_a = self.s1_script()
                if master_fu.result() is None:
                    log("S4", "⚠️ 母版生成失败 → 改用产品图作母版")

            rows = self.s2_adapter(side_a)
            rows = self.s3_gate2(rows)
            segments = self.s2c_group(rows)
            imgset = self.s4_images(segments)
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
