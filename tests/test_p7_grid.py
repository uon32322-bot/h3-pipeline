#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P7 宫格模式回归测试（全离线）。

背景（2026-09-20 用户拍板 + GRID6 实证）：一张宫格图承载整段动作，
切分后首/尾格作 FL2VA 锚、中间格作中间锚点。实证 6格/8s/4锚点 →
近静止帧 0%%（对照无锚 10%%），锚点秒帧与切片 MAE 6.6–13.9 ⇒ 动作可控。

  T1 宫格 prompt 合规自检通过（12 条官方要求全在）
  T2 自检**不是桩**：故意改坏模板须能报出缺失条款
  T3 中间锚点时间戳均匀映射（间隔 = seconds/(n+1)）
  T4 合成宫格图 → 切成正确格数、比例统一
  T5 负例：格线数与请求不符 / 空白格 → 拒绝切片（返回 0 格）
  T6 🔴 红线：宫格**原图**绝不能进 H3（s6 命令只允许切片，不允许 *_grid.png）
  T7 CFG grid_mode 默认 False（保守，不影响现有链路）

跑法:  python3 tests/test_p7_grid.py
"""
import subprocess, sys, types, tempfile
from pathlib import Path
import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
SRC = (ROOT / "orchestrator.py").read_text(errors="replace")
_fails = []

def check(name, cond, detail=""):
    print("  %s  %s%s" % ("PASS" if cond else "FAIL", name,
                          "" if cond else "   ← %s" % (detail,)))
    if not cond:
        _fails.append(name)

# 载入：模块头（含 CFG）+ 宫格 helper 段（两者分处文件前后）
_pre = SRC[:SRC.index("@dataclass\nclass Segment")]
# 注意：必须从 _frame_mae 起（宫格 helpers 依赖它做退化检测）；
# 只取「宫格模式」注释起会漏掉 _frame_mae ⇒ 切片静默返回 0（测试脚手架坑）
_hb = SRC[SRC.index("def _frame_mae"):SRC.index("\ndef _strip_inline_voiceover")]
M = types.ModuleType("orch_p7"); M.__file__ = str(ROOT / "orchestrator.py")
sys.modules["orch_p7"] = M
exec(compile(_pre, "orch_p7", "exec"), M.__dict__)
exec(compile(_hb, "orch_p7_h", "exec"), M.__dict__)
M.__dict__.setdefault("log", lambda s, m: None)
M.log = lambda s, m: None


def synth_grid(cols, rows, cw=320, ch=400, gutter=14, distinct=True):
    """造一张规整宫格图：白底 + gutter 白缝 + 每格不同灰阶内容。"""
    W = cols * cw + (cols + 1) * gutter
    H = rows * ch + (rows + 1) * gutter
    im = Image.new("RGB", (W, H), (255, 255, 255))
    for r in range(rows):
        for c in range(cols):
            x0 = gutter + c * (cw + gutter); y0 = gutter + r * (ch + gutter)
            i = r * cols + c
            base = (40 + i * 30) if distinct else 128
            a = np.full((ch, cw, 3), base, dtype=np.uint8)
            a[:, :cw // 3] = min(255, base + 60)      # 左条亮
            a[ch // 2:, :] = max(0, base - 25)         # 下半暗
            im.paste(Image.fromarray(a), (x0, y0))
    return im


TMP = Path(tempfile.mkdtemp(prefix="p7_"))
print("P7 宫格模式回归\n")

# ── T1 合规自检 ──
miss = M.check_grid_prompt()
check("T1 宫格 prompt 合规（12 条官方要求全在）", miss == [], miss[:3])
check("T1b 模板含格数/风格插值位", "{cells}" in M.GRID_PROMPT_TPL and "{style}" in M.GRID_PROMPT_TPL)

# ── T2 自检不是桩 ──
_orig = M.GRID_PROMPT_TPL
M.GRID_PROMPT_TPL = _orig.replace("白色细缝", "XX").replace("同一个人", "YY")
miss2 = M.check_grid_prompt()
check("T2 自检能报出被改坏的条款（非桩）", len(miss2) >= 2, miss2[:3])
M.GRID_PROMPT_TPL = _orig

# ── T3 中间锚点时间戳 ──
check("T3a 4 格/8s → 1.6/3.2/4.8/6.4",
      M._grid_mid_anchors(["a", "b", "c", "d"], 8.0) ==
      [("a", 1.6), ("b", 3.2), ("c", 4.8), ("d", 6.4)],
      M._grid_mid_anchors(["a", "b", "c", "d"], 8.0))
check("T3b 空列表 → 空（不崩）", M._grid_mid_anchors([], 8.0) == [])

# ── T4 正例切片 ──
g = TMP / "good.png"; synth_grid(2, 3).save(g)
cells = M._slice_grid(g, TMP / "o1", "good", 2, 3)
check("T4a 合成 2x3 → 6 格", len(cells) == 6, len(cells))
if cells:
    rats = [Image.open(c).size[0] / Image.open(c).size[1] for c in cells]
    check("T4b 各格比例统一为 9:16", all(abs(r - 9 / 16) < 0.01 for r in rats),
          ["%.3f" % r for r in rats])

# ── T5 负例 ──
check("T5a 请求 3x3（图是 2x3）→ 拒绝", len(M._slice_grid(g, TMP / "o2", "n33", 3, 3)) == 0)
check("T5b 请求 2x2（图是 2x3）→ 拒绝", len(M._slice_grid(g, TMP / "o3", "n22", 2, 2)) == 0)
check("T5c 请求 1x3（图是 2x3）→ 拒绝", len(M._slice_grid(g, TMP / "o4", "n13", 1, 3)) == 0)
blank = TMP / "blank.png"; Image.new("RGB", (768, 1344), (255, 255, 255)).save(blank)
check("T5d 全白图 → 拒绝", len(M._slice_grid(blank, TMP / "o5", "blank", 2, 3)) == 0)
flat = TMP / "flat.png"; synth_grid(2, 3, distinct=False).save(flat)
check("T5e 全同内容格 → 拒绝（格线仍在但空白/重复）",
      len(M._slice_grid(flat, TMP / "o6", "flat", 2, 3)) == 0)

# ── T6 🔴 红线：宫格原图绝不进 H3 ──
i = SRC.index("def h3_generate")
body = SRC[i:i + 2000]
check("T6a h3_generate 里 --mid 只来自 seg.mids", '"--mid"' in body and "seg.mids" in body)
check("T6b 送 H3 的锚点路径不含宫格原图（*_grid.png）",
      "_grid.png" not in body.split("--mid")[-1], body[-160:])
check("T6c 宫格原图仅用于切片（s4_grid 内），不赋值给 seg.first/last",
      "seg.first" not in SRC[SRC.index("def s4_grid"):SRC.index("def s4_images")])

# ── T7 保守默认 ──
check("T7 grid_mode 默认 False（零回归）", M.CFG.get("grid_mode") is False, M.CFG.get("grid_mode"))

# ── T8 分格动作序列（第4步：准确性/一致性根）──
b = M.derive_beats("她把杯子放进微波炉，关上门，按下按钮，等待加热，取出杯子，捧在手里微笑", 6)
check("T8a 推导出 6 格动作", len(b) == 6, b)
check("T8b 各格内容互不相同（真分格，非复制）", len(set(b)) >= 4, b)
check("T8c 首格=放入的动作", "放进微波炉" in b[0], b[0])
b2 = M.derive_beats("只有一个动作", 3)
check("T8d 句子不足时按格数补齐（不崩）", len(b2) == 3, b2)
check("T8e 空输入 → 空列表", M.derive_beats("", 6) == [])

lines = M.beat_lines(b, 6)
check("T9a 每格一行且编号 1..6", all(("第%d格：" % i) in lines for i in range(1, 7)))
check("T9b 行数 == 格数", len(lines.splitlines()) == 6, len(lines.splitlines()))
check("T9c beats 不足时用 fallback 兜底", "兜底句" in M.beat_lines(["只有一格"], 3, "兜底句"))

# ── T10 渲染后的 prompt 精确含逐格指定 ──
full = M.GRID_PROMPT_TPL.format(rows=3, cols=2, cells=6, style="clean bright",
                                beat_lines=M.beat_lines(b, 6))
check("T10a 含逐格指定标题", "逐格指定" in full)
check("T10b 6 格全部出现在 prompt 里", all(("第%d格：" % i) in full for i in range(1, 7)))
check("T10c 唯一动作串出现在 prompt（不是只给散文）", "放进微波炉" in full)
check("T10d 合规自检仍全过", M.check_grid_prompt() == [], M.check_grid_prompt()[:2])

# ── T11 s4_grid 优先用 seg.beats ──
i = SRC.index("def s4_grid"); sb = SRC[i:SRC.index("def s4_images")]
check("T11a s4_grid 读 seg.beats 优先", 'getattr(seg, "beats"' in sb)
check("T11b beats 不足才兜底推导", "derive_beats" in sb and "_need" in sb)
check("T11c beat_lines 进入模板渲染", "beat_lines=beat_lines" in sb)
check("T11d Segment 有 beats 字段", "beats: list[str]" in SRC)

# ── T12 一致性门禁（B）：逐格核画面 vs 脚本该格动作 ──
check("T12a judge_grid_vs_beats 存在", "def judge_grid_vs_beats" in SRC)
i = SRC.index("def s4_grid"); sb = SRC[i:SRC.index("def s4_images")]
check("T12b 门禁接在 s4_grid 切片之后", "judge_grid_vs_beats(cells" in sb)
check("T12c 一致性不达标 => 拒绝该宫格（return []）",
      "拒绝该宫格" in sb and "return []" in sb.split("拒绝该宫格")[1][:400])
check("T12d VLM 未判成 => skipped，不算通过", "不算通过" in SRC and "skipped" in SRC)
check("T12e 门禁默认开启 + 阈值 70%",
      M.CFG.get("grid_consistency_gate") is True and M.CFG.get("grid_min_align_pct") == 70.0,
      (M.CFG.get("grid_consistency_gate"), M.CFG.get("grid_min_align_pct")))
check("T12f 复用 judge_l2 的 _vlm_call（不另起一路模型）",
      "from judge_l2 import _vlm_call" in SRC)

# ── T13 闸门② 的 K 次退回重写回路（A）──
check("T13a Gate2Reject 是 CircuitBreak 的子类（可单独捕获）",
      "class Gate2Reject(CircuitBreak)" in SRC)
check("T13b 定义顺序正确（CircuitBreak 在前，否则导入即 NameError）",
      SRC.index("class CircuitBreak") < SRC.index("class Gate2Reject"),
      "顺序反了 ⇒ 导入时 NameError（py_compile 查不出来）")
check("T13c s3_gate2 抛 Gate2Reject（原为 CircuitBreak ⇒ 承诺重写却直接失败）",
      "raise Gate2Reject(" in SRC)
check("T13d run() 有 K 次重写回路", "gate2_rewrite_k" in SRC and "for _k in range(1, _k_max + 1)" in SRC)
check("T13e 只有 Gate2Reject 触发重写（其它熔断立即失败）",
      "except Gate2Reject as _e:" in SRC)
check("T13f 默认 K=3 / 不降级（响亮失败）",
      M.CFG.get("gate2_rewrite_k") == 3 and M.CFG.get("gate2_degrade_after_k") is False,
      (M.CFG.get("gate2_rewrite_k"), M.CFG.get("gate2_degrade_after_k")))
check("T13g 重写会重新调 ClipForge（换一批候选，不是重放同一份）",
      "_one_script_round" in SRC and "self.s1_script()" in SRC)
check("T13h 导入可执行（真导入而非仅编译）",
      subprocess.run([sys.executable, "-c",
        "import sys;sys.path.insert(0,'%s');import orchestrator as o;assert issubclass(o.Gate2Reject,o.CircuitBreak)"
        % ROOT], capture_output=True).returncode == 0)

print()
if _fails:
    print("P7 FAILED %d: %s" % (len(_fails), _fails)); sys.exit(1)
print("P7 ALL PASS")
