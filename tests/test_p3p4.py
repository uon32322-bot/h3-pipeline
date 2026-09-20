#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P3/P4 回归测试（全离线）。

P4 —— H3 prompt 组装官方 FL2VA 三段式：
  T1 第一行是对齐指令，其后恰好一个空行
  T2 三字段顺序固定（integrated_multimodal_description / overall_soundscape /
     non_diegetic_music）
  T3 对齐行 N = 最后一镜序号，S.SS = 总时长两位小数，破折号是 em dash
  T4 r34l1sm 默认剥离；描述首位须为官方风格词（Live-action）
  T5 台词必须取到（回归：S2 把台词放在 text.voiceover_zh，读错字段 ⇒ 全片失声）
  T6 <d> 规范：说话人身份/says 在 <d> 外，<d> 内只有 [Chinese] + 原样台词
  T7 切镜格式 `At MM:SS.mmm, the camera cuts to`，首镜不带时间戳
  T8 format="free" 时回退 ClipForge 原样 prompt

P3 —— 多候选择优所需的评分契约：
  T9 适配器落盘 <out>.gate.json，含 score=[errors, warnings]

跑法:  python3 tests/test_p3p4.py
"""
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT, ROOT / "scripts"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
import orchestrator as O  # noqa: E402

FAIL = []


def check(name, cond, detail=object()):
    print(("  PASS  " if cond else "  FAIL  ") + name + (("  " + str(detail)) if detail is not object() else ""))
    if not cond:
        FAIL.append(name)


ROWS = [
    {"idx": 1, "start": 0.0, "end": 8.0, "seconds_snapped": 8.0, "frames": 192,
     "purpose": "", "visual": "地铁车厢，她按着耳侧", "camera": "推近", "h3_prompt": "",
     "text": {"onscreen_en": "", "voiceover_zh": "每天挤地铁耳朵受罪", "post_zh": ""}},
    {"idx": 2, "start": 8.0, "end": 16.0, "seconds_snapped": 8.0, "frames": 192,
     "purpose": "", "visual": "站台上她戴上耳机", "camera": "旋转", "h3_prompt": "",
     "text": {"onscreen_en": "", "voiceover_zh": "半入耳戴一天也不胀", "post_zh": ""}},
]

p = O.build_h3_prompt(ROWS, 16.0)
lines = p.split("\n")

# ── T1 对齐行 + 空行 ──
check("T1a 第一行是对齐指令", lines[0].startswith("How the reference pictures align with the target video"))
check("T1b 对齐行后恰好一个空行", lines[1] == "" and lines[2].startswith("integrated_multimodal_description:"))

# ── T2 三字段顺序 ──
order = [l.split(":")[0] for l in lines if re.match(r"^(integrated_multimodal_description|overall_soundscape|non_diegetic_music):", l)]
check("T2 三字段顺序固定", order == ["integrated_multimodal_description", "overall_soundscape", "non_diegetic_music"], order)

# ── T3 对齐行细节 ──
check("T3a 用 em dash（\\u2014）", "\u2014" in lines[0], [c for c in lines[0][:80] if ord(c) > 127])
check("T3b N = 最后一镜序号（2）", "(from Shot 2) aligns with the 16.00-second" in lines[0], lines[0][-90:])
check("T3c 总时长两位小数 16.00", "16.00-second" in lines[0])

# ── T4 触发词 r34l1sm：默认**剥离** ──
# 官方 §4.1 要求 [Shot 1] 首位写「风格」；r34l1sm 是 LoRA 触发词，生产链实测
# 未挂 realism LoRA（只挂 turbo）⇒ 留在首位会**抢掉官方风格位**且污染正文。
# 契约：默认剥离；仅当显式挂 LoRA 时才注入首位。
desc = next(l for l in lines if l.startswith("integrated_multimodal_description:"))
check("T4a 默认剥离 r34l1sm（不抢官方风格位）", "r34l1sm" not in desc, desc[:80])
check("T4b 描述首位是官方风格词 Live-action", 
      re.search(r"^integrated_multimodal_description:\s*\[Shot 1\]\s*Live-action", desc) is not None,
      desc[:110])

# ── T5 台词必须取到（关键回归）──
check("T5a 台词1 进入 prompt", "每天挤地铁耳朵受罪" in p)
check("T5b 台词2 进入 prompt", "半入耳戴一天也不胀" in p)
check("T5c 两段<d>", p.count("<d>") == 2 and p.count("</d>") == 2, (p.count("<d>"), p.count("</d>")))

# ── T6 <d> 规范 ──
inside = re.findall(r"<d>(.*?)</d>", p)
check("T6a <d> 内只有语言标签+台词", all(re.match(r"^\[Chinese\] .+$", x) for x in inside), inside[:1])
check("T6b 说话人身份/says 在 <d> 外", "The woman (S1) says naturally:" in p.replace("<d>", "|<d>").split("|<d>")[0] or "(S1)" in p.split("<d>")[0])

# ── T7 切镜格式 ──
check("T7a 首镜无时间戳", not re.search(r"\[Shot 1\] At ", p))
check("T7b 镜2 用 At MM:SS.mmm + camera cuts to",
      bool(re.search(r"\[Shot 2\] At 00:08\.000, the camera cuts to ", p)))

# ── T8 free 回退 ──
orig_fmt = O.CFG["h3_prompt_format"]
try:
    O.CFG["h3_prompt_format"] = "free"
    rows2 = [dict(r, h3_prompt="r34l1sm, A static camera frames a woman...") for r in ROWS]
    o = O.Orchestrator(O.Job(product_images=["x.png"], product_text="t"), ROOT / "out" / "p3p4t1", dry=True)
    segs = o.s2c_group(rows2)
    check("T8 free 模式回退 ClipForge 原 prompt",
          segs[0].h3_prompt.startswith("r34l1sm, A static camera frames"), segs[0].h3_prompt[:50])
finally:
    O.CFG["h3_prompt_format"] = orig_fmt

# ── T9 适配器评分契约 ──
tmp = Path(tempfile.mkdtemp(prefix="p3p4t_"))
try:
    FAKE = {"scripts": [{"title": "t", "styleType": "pain_point", "totalDuration": 16, "shots": [
        {"shotId": 1, "type": "hook", "duration": 8, "camera": "推近",
         "description": "她单手抓着扶手，另一只手按着耳侧", "prompt": "r34l1sm, ...",
         "voiceover": "每天挤地铁耳朵受罪啊", "visualSource": "ai_generate"},
        {"shotId": 2, "type": "cta", "duration": 8, "camera": "静置",
         "description": "桌面上并排陈列充电盒", "prompt": "r34l1sm, ...",
         "voiceover": "链接放下面了", "visualSource": "ai_generate"}]}]}
    raw = tmp / "raw.json"
    raw.write_text(json.dumps(FAKE, ensure_ascii=False), encoding="utf-8")
    outp = tmp / "sa.json"
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "clipforge_adapter.py"),
                        "--raw-in", str(raw), "--name", "测试", "--duration", "16",
                        "--out", str(outp)], capture_output=True, text=True)
    gatef = Path(str(outp).replace(".json", ".gate.json"))
    check("T9a 适配器落盘 .gate.json", gatef.exists(), str(gatef))
    if gatef.exists():
        g = json.loads(gatef.read_text(encoding="utf-8"))
        check("T9b gate 含 score=[errors,warnings]",
              isinstance(g.get("score"), list) and len(g["score"]) == 2, g.get("score"))
        check("T9c 手部动作链被计为 errors（多手信号）",
              g["score"][0] >= 1, g.get("errors", [])[:1])
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print()
if FAIL:
    print("FAILED %d: %s" % (len(FAIL), FAIL))
    sys.exit(1)
print("P3/P4 ALL PASS")
