#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P6 回归测试：H3 首尾帧暂存 + 「无视频不得判通过」（全离线）。

来自全链真跑（S0→S9）暴露的两个真 bug：
  Bug A：ComfyUI 的 LoadImage 只接受 --input-directory 白名单内的路径，
         喂 output/... 下的首尾帧 → `Invalid image file` → 每段都失败。
  Bug B：s7_judge 在「视频路径不存在」时返回 pass=True ⇒ 段被冻结成「达标」
         （假通过），一路骗到 S8 才炸出来。缺文件是「没生成出来」，不是「判过了」。

覆盖：
  T1 _stage_for_h3 把首尾帧复制进 input 白名单目录，返回路径都在该目录内
  T2 暂存文件与源文件字节一致（不得改图）
  T3 缺首/尾帧时不暂存、返回 (None, None)
  T4 s7_judge 缺视频必须 pass=False（回归：曾返回 True 造成假通过）
  T5 s6_generate 在 h3_generate 恒失败时按 N 次换 seed 重抽，到顶明确失败
  T6 dry 模式不产生暂存文件

跑法:  python3 tests/test_p6_h3staging.py
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


tmp = Path(tempfile.mkdtemp(prefix="p6test_"))
orig_in = O.CFG.get("h3_input_dir")

try:
    stage_dir = tmp / "comfy_input"
    O.CFG["h3_input_dir"] = str(stage_dir)

    job = O.Job(product_images=["x.png"], product_text="t")
    orch = O.Orchestrator(job, tmp / "run", dry=False)

    # 造两张源图（在 output 下，模拟真实场景）
    src_f = orch.out / "img" / "seg1_first.png"
    src_l = orch.out / "img" / "seg1_last.png"
    src_f.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (768, 1344), (180, 180, 180)).save(src_f)
    Image.new("RGB", (768, 1344), (200, 200, 200)).save(src_l)
    seg = O.Segment(idx=1, seconds=8.0, prompt="p", first=str(src_f), last=str(src_l))

    f1, f2 = orch._stage_for_h3(seg)

    # ── T1 路径落在白名单目录内 ──
    check("T1a 首帧暂存路径在 input 目录内", bool(f1) and Path(f1).parent == stage_dir, f1)
    check("T1b 尾帧暂存路径在 input 目录内", bool(f2) and Path(f2).parent == stage_dir, f2)
    check("T1c 源文件不在 input 目录内（模拟白名单外）", src_f.parent != stage_dir)

    # ── T2 字节一致（不得改图）──
    check("T2a 暂存首帧与源字节一致", Path(f1).read_bytes() == src_f.read_bytes())
    check("T2b 暂存尾帧与源字节一致", Path(f2).read_bytes() == src_l.read_bytes())

    # ── T3 缺帧不暂存 ──
    seg_bad = O.Segment(idx=9, seconds=8.0, prompt="p", first="", last="")
    check("T3 缺首/尾帧返回 (None, None)", orch._stage_for_h3(seg_bad) == (None, None))

    # ── T4 缺视频不得判通过 ──
    seg_nv = O.Segment(idx=5, seconds=8.0, prompt="p", video="")
    v = orch.s7_judge(seg_nv)
    check("T4a 缺视频 pass=False", v.get("pass") is False, v)
    check("T4b 缺视频请求重抽 redraw=True", v.get("redraw") is True)
    check("T4c 缺视频不判为结构型（允许换 seed）", v.get("structural") is False, v)

    # ── T5 h3_generate 恒失败 ⇒ 按 N 次重抽后明确失败 ──
    orch2 = O.Orchestrator(job, tmp / "run2", dry=False)
    orch2.segments = [O.Segment(idx=1, seconds=8.0, prompt="p",
                                first=str(src_f), last=str(src_l))]
    calls = {"n": 0}
    orig_gen, orig_judge = O.Orchestrator.h3_generate, O.Orchestrator.s7_judge

    def fail_gen(self, seg, prefix):
        calls["n"] += 1
        return None

    def never_pass(self, seg):
        return {"pass": False, "redraw": True, "structural": False}

    O.Orchestrator.h3_generate, O.Orchestrator.s7_judge = fail_gen, never_pass
    gacha = O.CFG["gacha_n"]
    try:
        raised = False
        try:
            orch2.s6_generate()
        except O.CircuitBreak as e:
            raised = True
            check("T5b 到顶失败信息明确", "到顶失败" in str(e) or "N=" in str(e), str(e)[:60])
        check("T5a 恒失败时按 N 次重抽（%d 次）" % gacha, calls["n"] == gacha, calls["n"])
        check("T5c 未产出视频时明确失败（不静默续跑）", raised)
        check("T5d 没有任何段被冻结", not any(s.frozen for s in orch2.segments))
    finally:
        O.Orchestrator.h3_generate, O.Orchestrator.s7_judge = orig_gen, orig_judge

    # ── T6 dry 不暂存 ──
    orch3 = O.Orchestrator(job, tmp / "run3", dry=True)
    seg3 = O.Segment(idx=2, seconds=8.0, prompt="p", first=str(src_f), last=str(src_l))
    orch3.h3_generate(seg3, "video/T")
    check("T6 dry 模式不写暂存文件", not any(stage_dir.glob("h3stg_seg02_*")))

finally:
    if orig_in is not None:
        O.CFG["h3_input_dir"] = orig_in
    shutil.rmtree(tmp, ignore_errors=True)

    # ── T7 产物路径解析（回归：只取文件名 ⇒ 恒判「未产出」）──
    import tempfile as _tf2
    with _tf2.TemporaryDirectory() as td2:
        base = Path(td2)
        (base / "video").mkdir()
        made = base / "video" / "H3_SEG1_00001_.mp4"
        made.write_bytes(b"x" * 2048)
        # 正常 4 段格式
        got = O.parse_h3_output("OUTPUT videos video H3_SEG1_00001_.mp4\nelapsed = 848.3s\n",
                                base, "video/H3_SEG1")
        check("T7a 正确拼出绝对路径", got == str(made), got)
        # 无 OUTPUT 行 ⇒ 前缀兜底
        got2 = O.parse_h3_output("prompt_id = abc\nelapsed = 1s\n", base, "video/H3_SEG1")
        check("T7b 无 OUTPUT 行时按前缀兜底", got2 == str(made), got2)
        # 完全不存在的产物 ⇒ None（不得瞎编路径）
        got3 = O.parse_h3_output("OUTPUT videos video NOPE.mp4\n", base, "video/H3_SEG9")
        check("T7c 产物不存在时返回 None（不瞎编）", got3 is None, got3)
        # 旧实现的错法：只取最后 token 会得到裸文件名，其路径不存在的目录下
        check("T7d 裸文件名不可直接当路径用",
              not Path("H3_SEG1_00001_.mp4").exists())

print()
if FAIL:
    print("FAILED %d: %s" % (len(FAIL), FAIL))
    sys.exit(1)
print("P6 ALL PASS")
