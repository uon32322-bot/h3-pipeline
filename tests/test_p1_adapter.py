#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P1 回归测试：ClipForge → side_a 适配器（全离线，不打网络）。

覆盖本次实测暴露的 5 个真实缺陷，防止回归：
  T1 适配器自带 --selftest 通过
  T2 visual 与 h3_prompt 必须分家（否则 H3 提示词会串进图像层毁首帧）
  T3 时长收敛：48s → ≤ 验收区间，且 hook/cta 必保、丢弃顺序符合结构优先级
  T4 运镜必须全部落在 orchestrator 白名单内
  T5 手部动作闸门能抓住「另一只手」这类三只手根因
  T6 fidelity_class=unknown（S0 之前的默认值）不得触发 argparse exit 2

跑法:  python3 tests/test_p1_adapter.py
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import clipforge_adapter as A  # noqa: E402

FAIL = []


def check(name, cond, detail=object()):
    print(("  PASS  " if cond else "  FAIL  ") + name + (("  " + str(detail)) if detail is not object() else ""))
    if not cond:
        FAIL.append(name)


# ── 假响应：复刻实测里 ClipForge 的真实结构（6 镜 × 8s = 48s）──
FAKE = {"scripts": [{
    "title": "地铁静音神器", "styleType": "pain_point", "totalDuration": 48,
    "shots": [
        {"shotId": 1, "type": "hook", "duration": 8, "camera": "推近",
         "description": "早高峰地铁车厢，她单手抓着扶手，另一只手按着耳侧。",
         "prompt": "r34l1sm, A static camera frames a young woman...",
         "voiceover": "每天挤地铁耳朵受罪，这耳机一戴世界就安静了真的", "visualSource": "ai_generate"},
        {"shotId": 2, "type": "pain_point", "duration": 8, "camera": "缓慢推近特写",
         "description": "同一车厢，旁边乘客手机外放短视频。",
         "prompt": "r34l1sm, A static camera frames...",
         "voiceover": "外放的声音比耳机还大，戴了等于没戴你说气不气人", "visualSource": "ai_generate"},
        {"shotId": 3, "type": "product_reveal", "duration": 8, "camera": "旋转",
         "description": "站台上她从充电盒取出耳机戴入右耳。",
         "prompt": "r34l1sm, A static camera frames...",
         "voiceover": "半入耳才有的通透感，戴一天耳朵也不会胀得难受", "visualSource": "ai_generate"},
        {"shotId": 4, "type": "demo", "duration": 8, "camera": "静置",
         "description": "办公室工位，她戴着耳机，轻触耳机切换设备。",
         "prompt": "r34l1sm, A static camera frames...",
         "voiceover": "双击切通透模式，同事跟你说话不用摘耳机多方便啊", "visualSource": "ai_generate"},
        {"shotId": 5, "type": "social_proof", "duration": 8, "camera": "推近",
         "description": "午休时间她在楼下跑步，耳机稳稳戴在耳中。",
         "prompt": "r34l1sm, A static camera frames...",
         "voiceover": "跑步狂甩都掉不下来，这个佩戴感是真的服气了", "visualSource": "ai_generate"},
        {"shotId": 6, "type": "cta", "duration": 8, "camera": "推近",
         "description": "家中沙发上，她手持充电盒。",
         "prompt": "r34l1sm, A static camera frames...",
         "voiceover": "链接放下面了，自己去看看就知道了别怪我没提醒", "visualSource": "ai_generate"},
    ]}]}

# ── T1 自带自检 ──
r = subprocess.run([sys.executable, str(ROOT / "scripts" / "clipforge_adapter.py"), "--selftest"],
                   capture_output=True, text=True)
check("T1 适配器 --selftest 通过", r.returncode == 0, r.stdout.strip().splitlines()[-1] if r.stdout else r.stderr[:120])

# ── T2 visual / h3_prompt 分家 ──
sa = A.to_side_a(FAKE, product_name="降噪耳机", category="tech", target_duration=30)
leaked = [s["idx"] for s in sa["shots"]
          if "r34l1sm" in s.get("visual", "") or "camera" in s.get("visual", "").lower()]
check("T2a visual 不含 H3 提示词（r34l1sm / camera）", not leaked, leaked)
check("T2b h3_prompt 独立携带且以 r34l1sm 开头",
      all(s.get("h3_prompt", "").startswith("r34l1sm") for s in sa["shots"]))

# ── T3 时长收敛 + 结构优先级 ──
check("T3a 总时长收敛进 30–40s 验收区间",
      30 <= sa["_meta"]["final_duration"] <= 40, sa["_meta"]["final_duration"])
types = [s["_cf_type"] for s in sa["shots"]]
check("T3b hook 必保（首镜）", types[0] == "hook", types)
check("T3c cta 必保（末镜）", types[-1] == "cta", types)
check("T3d demo 未被丢（卖点承载）", "demo" in types, types)
check("T3e 被丢镜次已记入 _meta（不静默消失）", len(sa["_meta"]["dropped_shot_idx"]) == 6 - len(types),
      sa["_meta"]["dropped_shot_idx"])
check("T3f 取舍后无时长空档（start 连续）",
      all(abs(sa["shots"][i]["start"] - sa["shots"][i - 1]["end"]) < 1e-6
          for i in range(1, len(sa["shots"]))))

# ── T4 运镜白名单 ──
bad_cam = [s["camera"] for s in sa["shots"] if s["camera"] not in A.CAMERA_WHITELIST]
check("T4 运镜全部落在 orchestrator 白名单内", not bad_cam, bad_cam)

# ── T5 手部动作闸门 ──
g = A.gate_p3(sa["shots"], requested_duration=30)
hits = A._match_hand_verbs("她单手抓着扶手，另一只手按着耳侧，拿起耳机推开盒盖")
check("T5a 「另一只手」被识别为多手信号", "另一只手" in hits, hits)
check("T5b 子串去重（拧开命中时不重复计 拧）",
      A._match_hand_verbs("拧开瓶盖") == ["拧开"], A._match_hand_verbs("拧开瓶盖"))
check("T5c 多手动作触发结构性 error", any("手部动作" in e for e in g["errors"]), g["errors"][:1])

# ── T6 fidelity=unknown 不得撞 argparse 的 exit 2 ──
with tempfile.TemporaryDirectory() as td:
    raw = Path(td) / "raw.json"
    raw.write_text(json.dumps(FAKE, ensure_ascii=False), encoding="utf-8")
    r6 = subprocess.run([sys.executable, str(ROOT / "scripts" / "clipforge_adapter.py"),
                         "--raw-in", str(raw), "--name", "测试", "--duration", "30",
                         "--fidelity", "unknown", "--out", str(Path(td) / "sa.json")],
                        capture_output=True, text=True)
    check("T6 --fidelity unknown 返回 0（不撞 argparse 的 2）", r6.returncode == 0,
          "rc=%d %s" % (r6.returncode, (r6.stderr or "")[:120]))

print()
if FAIL:
    print("FAILED %d: %s" % (len(FAIL), FAIL))
    sys.exit(1)
print("P1 ALL PASS")
