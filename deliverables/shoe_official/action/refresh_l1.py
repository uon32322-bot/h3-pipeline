#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""refresh_l1.py —— 只重算 L1 并把结果合并回已有判官报告。

为什么需要它：L2（远端 VLM）是判官里最贵的一环（17 次 API 调用 / 镜）。
当 L1 的检测器逻辑被修正时，没必要把 L2 再烧一遍 —— 直接换掉 L1 段、
重新聚合即可。这也让"阈值/算法迭代"不再触发全量重判。

用法: refresh_l1.py <判官报告.json> [--first <锚定首帧.png>]
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import judge_shot as J  # noqa: E402


def main():
    rep = sys.argv[1]
    first = None
    if "--first" in sys.argv:
        first = sys.argv[sys.argv.index("--first") + 1]
    cuts = ()
    if "--cuts" in sys.argv:
        cuts = tuple(float(x) for x in
                     sys.argv[sys.argv.index("--cuts") + 1].split(",") if x.strip())
    d = json.load(open(rep, encoding="utf-8"))
    video = d["video"]
    old = {r["id"]: r["verdict"] for r in d["items"] if r["id"].startswith("L1")}
    new = J.l1(video, first, cuts)
    changed = []
    for r in new:
        if old.get(r["id"]) != r["verdict"]:
            changed.append("%s: %s → %s" % (r["id"], old.get(r["id"]), r["verdict"]))
    keep = [r for r in d["items"] if not r["id"].startswith("L1")]
    d["items"] = [r for r in keep if not r["id"].startswith("L0")] + new + \
                 [r for r in keep if r["id"].startswith("L0")]
    d["items"] = sorted(d["items"], key=lambda r: r["id"])
    d["aggregate"] = J.aggregate(d["items"])
    d["l1_refreshed"] = True
    json.dump(d, open(rep, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("L1 重算完成 %s" % rep)
    for c in changed:
        print("   变化 " + c)
    if not changed:
        print("   无变化")
    print("   聚合判定 → %s" % d["aggregate"]["verdict"])
    for x in d["aggregate"].get("remedies", []):
        print("   ↳ " + x)
    print()
    print("   L1 明细：")
    for r in new:
        print("     %-6s %-3s value=%s thr=%s" %
              (r["id"], r["verdict"], r["evidence"]["value"], r["evidence"]["threshold"]))


if __name__ == "__main__":
    main()
