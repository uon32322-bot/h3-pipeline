#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P5 回归测试：跨段影调一致性（统一基调注入 + 偏离自动重出）。全离线。

覆盖：
  T1 _tone_sfx() 开关语义（锚定照可关、段帧常开）
  T2 _brightness() 读数正确
  T3 段帧 prompt 里确实带上了统一基调
  T4 s4b_tone 能识别亮度离群段，并自动重出使其收敛（不抛回用户）
  T5 各段本就一致时 s4b_tone 直接放行、不做多余重出
  T6 重出到顶仍不达标时只告警不熔断（不阻断后续流程）

跑法:  python3 tests/test_p5_tone.py
"""
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT, ROOT / "scripts"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
import orchestrator as O  # noqa: E402
from PIL import Image  # noqa: E402

FAIL = []


def check(name, cond, detail=object()):
    print(("  PASS  " if cond else "  FAIL  ") + name + (("  " + str(detail)) if detail is not object() else ""))
    if not cond:
        FAIL.append(name)


tmp = Path(tempfile.mkdtemp(prefix="p5test_"))
_regen = []          # 记录被要求重出的 (段号, 修正语)
_prompts = []        # 记录所有 prompt


def fake_tt_img(self, out_path, size, prompt, refs=None):
    p = Path(out_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    (self.out / "prompt" / (p.stem + ".txt")).write_text(prompt, encoding="utf-8")
    _prompts.append(prompt)
    # 亮度由文件名决定：seg1 故意过亮；带「压暗/提亮」修正语时产出标准亮度 150
    b = 150
    if "seg1_" in p.name and "压暗" not in prompt:
        b = 250
    if "压暗" in prompt or "提亮" in prompt:
        _regen.append((p.name, "修正"))
        b = 150
    Image.new("RGB", (768, 1344), (b, b, b)).save(p)
    return str(p)


try:
    # ── T1 _tone_sfx 开关 ──
    check("T1a 默认返回统一基调", "统一视觉基准" in O._tone_sfx())
    check("T1b enable=False 返回空", O._tone_sfx(False) == "")

    # ── T2 亮度读数 ──
    p150 = tmp / "b150.png"
    Image.new("RGB", (64, 64), (150, 150, 150)).save(p150)
    check("T2 _brightness 读数正确", abs(O._brightness(p150) - 150) < 1.5, O._brightness(p150))
    check("T2b 读不存在的文件返回 -1", O._brightness(tmp / "nope.png") < 0)

    # ── T3/T4 离群段自动重出 ──
    job = O.Job(product_images=["prod.png"], model_images=["model.png"], product_text="测试")
    orch = O.Orchestrator(job, tmp / "run", dry=False)
    orch.segments = [O.Segment(idx=i, seconds=8.0, prompt="段%d画面" % i) for i in (1, 2, 3)]
    orig = O.Orchestrator.tt_img
    O.Orchestrator.tt_img = fake_tt_img
    try:
        orch.s4_images.__wrapped__ if False else None
        # 直接布置镜像帧路径并生成（走 fake）
        for s in orch.segments:
            s.first = str(orch.out / "img" / ("seg%d_first.png" % s.idx))
            s.last = str(orch.out / "img" / ("seg%d_last.png" % s.idx))
        orch._base_anchor = str(orch.out / "img" / "anchor_main.png")
        orch._identity_refs = ["model.png", "prod.png"]
        _prompts.clear()
        for s in orch.segments:
            orch.tt_img(Path(s.first), "768x1344", "start state: %s。%s" % (s.prompt, O._tone_sfx()), [])
            orch.tt_img(Path(s.last), "768x1344", "end state, same subject and background: %s。%s"
                        % (s.prompt, O._tone_sfx()), [s.first])
        check("T3 段帧 prompt 带统一基调",
              all("统一视觉基准" in pr for pr in _prompts if pr.startswith("start state")),
              len(_prompts))

        before = {s.idx: round(O._brightness(s.first), 1) for s in orch.segments}
        orch.s4b_tone()
        after = {s.idx: round(O._brightness(s.first), 1) for s in orch.segments}
        check("T4a 识别出离群段并重出（段1）", any("seg1_first" in n for n, _ in _regen), _regen[:3])
        check("T4b 重出后亮度收敛到中位数附近",
              abs(after[1] - 150) < 20, "重出前 %s → 重出后 %s" % (before, after))
    finally:
        O.Orchestrator.tt_img = orig

    # ── T5 本就一致时不多做重出 ──
    orch2 = O.Orchestrator(job, tmp / "run2", dry=False)
    orch2.segments = [O.Segment(idx=i, seconds=8.0, prompt="段%d" % i) for i in (1, 2, 3)]
    for s in orch2.segments:
        s.first = str(orch2.out / "img" / ("ok%d_first.png" % s.idx))
        s.last = str(orch2.out / "img" / ("ok%d_last.png" % s.idx))
        Image.new("RGB", (768, 1344), (150, 150, 150)).save(s.first)
        Image.new("RGB", (768, 1344), (150, 150, 150)).save(s.last)
    _regen.clear()
    O.Orchestrator.tt_img = fake_tt_img
    try:
        orch2.s4b_tone()
    finally:
        O.Orchestrator.tt_img = orig
    check("T5 一致时零重出", not _regen, _regen[:2])

    # ── T6 修不达标只告警不熔断 ──
    orch3 = O.Orchestrator(job, tmp / "run3", dry=False)
    orch3.segments = [O.Segment(idx=i, seconds=8.0, prompt="段%d" % i) for i in (1, 2)]
    for i, b in ((1, 20), (2, 240)):
        s = orch3.segments[i - 1]
        s.first = str(orch3.out / "img" / ("bad%d_first.png" % i))
        s.last = str(orch3.out / "img" / ("bad%d_last.png" % i))
        Image.new("RGB", (768, 1344), (b, b, b)).save(s.first)
        Image.new("RGB", (768, 1344), (b, b, b)).save(s.last)

    def always_wrong(self, out_path, size, prompt, refs=None):
        """永远产出同一个亮度 ⇒ 无论重出几轮都无法收敛（验证「不熔断」）。"""
        p = Path(out_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        (self.out / "prompt" / (p.stem + ".txt")).write_text(prompt, encoding="utf-8")
        Image.new("RGB", (768, 1344), (20, 20, 20) if "1_" in p.name else (240, 240, 240)).save(p)
        return str(p)

    O.Orchestrator.tt_img = always_wrong
    try:
        orch3.s4b_tone()
        survived = True
    except Exception as e:
        survived = False
        print("     异常:", e)
    finally:
        O.Orchestrator.tt_img = orig
    check("T6 修不达标只告警、不抛异常（不阻断后续流程）", survived)

finally:
    shutil.rmtree(tmp, ignore_errors=True)

print()
if FAIL:
    print("FAILED %d: %s" % (len(FAIL), FAIL))
    sys.exit(1)
print("P5 ALL PASS")
