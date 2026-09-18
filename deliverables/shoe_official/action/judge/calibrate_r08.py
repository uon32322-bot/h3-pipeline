#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""calibrate_r08.py —— 标定 R-08（画面文字落位）措辞

为什么需要：R-08 第一版措辞在**已知缺陷**（D 片文字压在鞋身上）上三票散成
(na,yes,no) ⇒ 判据不可靠。重新写成"有没有任何一行字压在鞋身上"后必须**重新标定**：
  ① 已知缺陷片（D）→ 期望稳定判 no（抓到）
  ② 已知无文字片（C1）→ 期望稳定判 na（不误报）
只有两头都对，R-08 才能进判官团；否则它既抓不到缺陷、又会误伤好片。

用法: calibrate_r08.py
"""
import base64
import importlib.util
import sys

sys.argv = ["calibrate_r08"]
spec = importlib.util.spec_from_file_location("j", "judge_shot.py")
J = importlib.util.module_from_spec(spec)
spec.loader.exec_module(J)

ITEM = "R-08"


def probe(video, prompt_file, n_votes, label):
    prompt = open(prompt_file, encoding="utf-8").read().strip()
    sets = J.vote_sets(video, n_frames=6, votes=n_votes)
    b64 = [[base64.b64encode(f).decode() for f in J.frames_at(video, s)] for s in sets]
    claim = (J.ITEMS[ITEM] +
             "\n\nTHE SHOT DESCRIPTION WRITTEN BY THE FILMMAKER (this is the only "
             "source of truth for what was declared):\n" + prompt[:4000])
    print("=" * 74)
    print("标定 %s ｜ %s ｜ %d 票" % (ITEM, label, n_votes))
    print("  片源 %s" % video)
    print("=" * 74)
    votes = []
    for k in range(n_votes):
        r = J._vlm_call(b64[k], claim, think=False)
        v = str(r.get("verdict", "na")).lower().strip()
        if v not in ("yes", "no", "na"):
            v = "na"
        rtok = r.get("_reasoning_tokens", 0)
        ev = r.get("evidence") or {}
        votes.append({"verdict": v})
        print("   票%d  %-3s  rtok=%-5s region=%s"
              % (k + 1, v, rtok, str(ev.get("region"))[:70]), flush=True)
    top, tie = J._tally(votes)
    from collections import Counter
    c = Counter(x["verdict"] for x in votes)
    print("   " + "-" * 68)
    print("   汇总 %s ｜ 多数=%s%s" % (dict(c), top, "（并列→判 na）" if tie else ""))
    return top, dict(c)


if __name__ == "__main__":
    probe("raw/D_product_dynamic.mp4", "prompt/D_product_dynamic.txt", 6,
          "⚠️ 已知缺陷片（文字压在鞋身上）→ 期望 no")
    probe("raw/C1v3_run.mp4", "prompt/C1_run_v2.txt", 3,
          "✅ 已知无文字片 → 期望 na")
    print("R08-CALIB-DONE")
