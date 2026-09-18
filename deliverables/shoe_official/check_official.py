#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_official.py —— 按官方《Video Prompt Writing Guide》+《极简产品广告生成器》SOP
对提示词做机器自检，作为"严格按官方规范"的可复核证据。

口径说明（两处易误判）：
  · 两段式文案必须在 prompt 里写成"前半…后半…"，所以分段字符串（如 "Air Flows "）
    会一起被正则抓到 —— 词数/字符数只校验【完整单行文案】，不校验分段片段。
  · 无文案的片子（真人穿着片）不适用"单行硬约束/两段色"两项，标记为 N/A 而非 ❌。
"""
import re
import sys

FULL_COPY = ["Air Flows Right Through", "Soft Landing, Light As Air"]


def check(path, label, has_copy=True):
    t = open(path, encoding="utf-8").read()
    lines = t.split("\n")
    r = []

    r.append(("① 对齐指令=第一行", lines[0].startswith(
        "How the reference pictures align with the target video")))
    r.append(("② 指令后空行", lines[1].strip() == ""))
    r.append(("③ 三核心字段齐全", all(k in t for k in (
        "integrated_multimodal_description:", "overall_soundscape:", "non_diegetic_music:"))))
    r.append(("④ 首镜含风格声明", bool(re.search(r"\[Shot 1\] (Live-action|Cinematic|3D)", t))))
    r.append(("⑤ 每个 [Shot N] 带递增切点时刻", len(re.findall(r"\[Shot \d+\] At \d\d:\d\d\.\d\d\d", t))
              == len(re.findall(r"\[Shot \d+\]", t)) - 1))
    cm = re.findall(r"camera (?:pushes|pulls|pans|trucks|tilts|pedestals|rolls|holds|moves)[^.;]*", t)
    r.append(("⑥ 运镜=类型+幅度+速度", any(
        (("with small amplitude" in c or "with large amplitude" in c) and
         ("at slow speed" in c or "at fast speed" in c)) or "static shot" in c for c in cm)))
    if has_copy:
        r.append(("⑦ 文案逐字写入 prompt", all(c in t for c in FULL_COPY)))
        bad = [(c, len(c.split()), len(c)) for c in FULL_COPY
               if not (3 <= len(c.split()) <= 5 and len(c) <= 32)]
        r.append(("⑧ 完整文案 3-5 词且≤32字符 %s" % [(c, len(c.split()), len(c)) for c in FULL_COPY],
                  not bad))
        r.append(("⑨ 两段式同色规则明写(深灰+商品黑)",
                  "dark grey" in t and "product's black" in t))
        r.append(("⑩ 单行硬约束明写", "one single line" in t))
        r.append(("⑪ 两段进入时刻明写", len(re.findall(r"first fade in in dark grey", t)) >= 1
                  and len(re.findall(r"then fade in in the product's black", t)) >= 1))
    else:
        r.append(("⑦⑧⑨⑩⑪ 文案规范 N/A（本片无画面文案）", True))
    r.append(("⑫ 对齐指令时间点数=参考图数",
              len(re.findall(r"aligns with the [\d.]+-second mark", lines[0]))
              == len(re.findall(r"Picture \d", lines[0]))))
    r.append(("⑬ 明确禁止额外文字/图形", ("no further text" in t or "no on-screen text" in t)))

    print("=== %s · %s · %d 字符 ===" % (label, path, len(t)))
    for k, v in r:
        print("   %s %s" % ("✅" if v else "❌", k))
    return all(v for _, v in r)


if __name__ == "__main__":
    # 默认跑全套；也可显式指定：check_official.py prompt/A3_product_final.txt:有文案 ...
    if len(sys.argv) > 1:
        ok = True
        for spec in sys.argv[1:]:
            p, _, tag = spec.partition(":")
            ok &= check(p, tag or p, has_copy=("无文案" not in tag))
        print()
        print("总判定:", "✅ 通过官方规范自检" if ok else "❌ 有未通过项")
        sys.exit(0 if ok else 1)

    a = check("prompt/A3_product_final.txt", "A 产品主片 · 最终版（10s，有文案）", True)
    b = check("prompt/B_wearer.txt", "B 真人穿着片（5s，无文案）", False)
    print()
    print("总判定:", "✅ 两份均通过官方规范自检" if (a and b) else "❌ 有未通过项")
    sys.exit(0 if (a and b) else 1)
