#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P2 回归测试：S4 身份强化接线 + S8 真拼接（全离线，合成测试片，不打网络/不动 GPU）。

覆盖本次实测暴露的缺口：
  T1 S8 拼接帧数正确（源帧数 − (段数−1)，即每个接缝丢 1 帧去重）
  T2 S8 保留音轨（段间 40ms 等功率焊接）
  T3 S8 单段走直拷贝（不重编码）
  T4 S8 无可用段时明确失败，绝不产出空成片
  T5 S2c 分组把 ClipForge 的 h3_prompt 带进 Segment（图像层/视频层分家）
  T6 h3_generate 优先使用 seg.h3_prompt（而非场景叙述）

跑法:  python3 tests/test_p2_s4s8.py
"""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# orchestrator 在仓库根或 scripts/ 下（本地与生产布局不同）——两处都加进搜索路径
for _p in (ROOT, ROOT / "scripts"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
import orchestrator as O  # noqa: E402

FAIL = []


def check(name, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + (("  " + str(detail)) if detail else ""))
    if not cond:
        FAIL.append(name)


def probe_frames(path):
    r = json.loads(subprocess.run(
        [O.FFPROBE, "-v", "error", "-print_format", "json",
         "-show_streams", "-select_streams", "v:0", str(path)],
        capture_output=True, text=True).stdout)["streams"][0]
    return int(r.get("nb_frames", 0))


def make_clip(path: Path, seconds: float = 2.0, with_audio: bool = True):
    cmd = [O.FFMPEG, "-y", "-v", "error",
           "-f", "lavfi", "-i", "testsrc2=size=320x568:rate=24:duration=%s" % seconds]
    if with_audio:
        cmd += ["-f", "lavfi", "-i", "sine=frequency=440:duration=%s" % seconds]
        cmd += ["-c:a", "aac", "-shortest"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)]
    subprocess.run(cmd, check=True, capture_output=True)
    return path


tmp = Path(tempfile.mkdtemp(prefix="p2test_"))
try:
    if shutil.which("ffmpeg") is None:
        print("  SKIP  未找到 ffmpeg，P2 拼接测试无法运行")
        sys.exit(0)

    c1, c2, c3 = (make_clip(tmp / ("c%d.mp4" % i)) for i in (1, 2, 3))
    src_frames = sum(probe_frames(p) for p in (c1, c2, c3))

    job = O.Job(product_images=[], product_text="P2 拼接测试")
    orch = O.Orchestrator(job, tmp / "run", dry=False)
    orch.segments = [O.Segment(idx=i, seconds=2.0, prompt="s%d" % i, video=str(p))
                     for i, p in enumerate((c1, c2, c3), 1)]

    # ── T1/T2 拼接 ──
    out = Path(orch.s8_post())
    got = probe_frames(out)
    check("T1 拼接帧数 = 源帧数 − (段数−1)", got == src_frames - 2, "源%d → 成片%d" % (src_frames, got))
    check("T2 成片保留音轨", orch._has_audio(str(out)))

    # ── T3 单段直拷贝 ──
    orch2 = O.Orchestrator(job, tmp / "run2", dry=False)
    orch2.segments = [O.Segment(idx=1, seconds=2.0, prompt="s1", video=str(c1))]
    out2 = Path(orch2.s8_post())
    check("T3 单段走直拷贝（帧数不变）", probe_frames(out2) == probe_frames(c1),
          "%d vs %d" % (probe_frames(out2), probe_frames(c1)))

    # ── T4 无可用段 → 明确失败 ──
    orch3 = O.Orchestrator(job, tmp / "run3", dry=False)
    orch3.segments = [O.Segment(idx=1, seconds=2.0, prompt="s", video=str(tmp / "nope.mp4"))]
    raised = False
    try:
        orch3.s8_post()
    except O.CircuitBreak:
        raised = True
    check("T4 无可用段时 CircuitBreak（不产出空成片）", raised)

    # ── T5 S2c 把 h3_prompt 带进 Segment ──
    orch4 = O.Orchestrator(job, tmp / "run4", dry=False)
    rows = [{"idx": 1, "start": 0.0, "end": 8.0, "frames": 192, "seconds_snapped": 8.0,
             "purpose": "", "visual": "地铁车厢，她按着耳侧", "camera": "推近",
             "h3_prompt": "r34l1sm, A static camera frames a young woman...",
             "text": {"onscreen_en": "", "voiceover_zh": "台词", "post_zh": ""}}]
    segs = orch4.s2c_group(rows)
    check("T5 Segment 带 h3_prompt", bool(segs and segs[0].h3_prompt), segs[0].h3_prompt[:40] if segs else "无")
    check("T5b prompt（图像层）与 h3_prompt（视频层）不同",
          segs[0].prompt != segs[0].h3_prompt)

    # ── T6 h3_generate 用 h3_prompt ──
    orch5 = O.Orchestrator(job, tmp / "run5", dry=True)
    seg = O.Segment(idx=1, seconds=8.0, prompt="画面叙述ABC", h3_prompt="r34l1sm 视频提示词XYZ")
    orch5.h3_generate(seg, "video/T")
    written = (orch5.out / "prompt" / "seg1.txt").read_text(encoding="utf-8")
    check("T6 h3_generate 优先写 h3_prompt", "视频提示词XYZ" in written, written[:40])
    seg2 = O.Segment(idx=2, seconds=8.0, prompt="仅图像层叙述")
    orch5.h3_generate(seg2, "video/T")
    written2 = (orch5.out / "prompt" / "seg2.txt").read_text(encoding="utf-8")
    check("T6b 无 h3_prompt 时回落场景叙述", "仅图像层叙述" in written2, written2[:40])

    # ── T7 S4 成功时必须保留段（回归：尾帧判断漏 `not` ⇒ 图全生成却报 0/N）──
    from PIL import Image as _Image
    seen_refs = []

    def fake_tt_img(self, out_path, size, prompt, refs=None):
        seen_refs.append(list(refs or []))
        p = Path(out_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        _Image.new("RGB", (768, 1344), (205, 205, 205)).save(p)
        (self.out / "prompt" / (p.stem + ".txt")).write_text(prompt, encoding="utf-8")
        return str(p)

    job7 = O.Job(product_images=["prod.png"], model_images=["model.png"],
                 product_text="测试产品")
    orch7 = O.Orchestrator(job7, tmp / "run7", dry=False)
    orig_tt = O.Orchestrator.tt_img
    O.Orchestrator.tt_img = fake_tt_img
    try:
        orch7.s4_images([O.Segment(idx=1, seconds=8.0, prompt="镜1画面"),
                         O.Segment(idx=2, seconds=8.0, prompt="镜2画面")])
    finally:
        O.Orchestrator.tt_img = orig_tt

    check("T7 S4 成功时保留段（回归：漏 not ⇒ 0/N）", len(orch7.segments) == 2, len(orch7.segments))
    check("T7b 每段首尾帧路径已写入", all(x.first and x.last for x in orch7.segments))
    # 调用顺序受并发影响，且母版（2 参考）/锚定照（1 参考）也在其中 ⇒ 用 any 判定
    check("T7c 段首帧带身份参考（锚图+模特图+产品图 = 3 张）",
          any(len(r) == 3 for r in seen_refs),
          sorted(set(len(r) for r in seen_refs)))
    check("T7d 段尾帧从首帧同源派生（仅 1 张参考）",
          any(len(r) == 1 for r in seen_refs))

finally:
    shutil.rmtree(tmp, ignore_errors=True)

print()
if FAIL:
    print("FAILED %d: %s" % (len(FAIL), FAIL))
    sys.exit(1)
print("P2 ALL PASS")
