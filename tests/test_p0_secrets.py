#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P0 安全回归测试：密钥 fail-closed + 无硬编码明文。

跑法:  python3 tests/test_p0_secrets.py
退出码 0 = 全通过；1 = 有失败项。
"""
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FAIL = []


def check(name, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + (("  " + str(detail)) if detail else ""))
    if not cond:
        FAIL.append(name)


# T1：secrets 模块可导入并取到 Key
sys.path.insert(0, str(ROOT))
import h3secrets  # noqa: E402

k = h3secrets.lk888_key()
check("T1 能取到 LK888_KEY", isinstance(k, str) and len(k) > 10, "len=%d" % len(k))

# T2：无 env 且空 HOME ⇒ fail-closed（退出码 78）
env = {kk: vv for kk, vv in os.environ.items() if kk not in ("LK888_KEY", "H3P_SECRETS")}
with tempfile.TemporaryDirectory() as td:
    env["HOME"] = td
    r = subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.path.insert(0, %r); import h3secrets; h3secrets.lk888_key()" % str(ROOT)],
        capture_output=True, text=True, env=env)
    check("T2 缺 Key 时 fail-closed(78)", r.returncode == 78, "实际=%d" % r.returncode)

# T3：源码树无硬编码明文 Key
SK = re.compile(r"sk-[A-Za-z0-9]{16,}")
hits = []
for p in ROOT.rglob("*.py"):
    if "external_repos" in p.parts or "__pycache__" in p.parts:
        continue
    try:
        for i, line in enumerate(p.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            if SK.search(line):
                hits.append("%s:%d" % (p.relative_to(ROOT), i))
    except Exception:
        pass
check("T3 源码树无明文 Key", not hits, hits[:5])

# T4：git 跟踪文件无明文 Key
r = subprocess.run(["git", "grep", "-n", "-I", "-E", r"sk-[A-Za-z0-9]{16,}"],
                   cwd=ROOT, capture_output=True, text=True)
check("T4 git 跟踪文件无明文 Key", not r.stdout.strip(), r.stdout.strip()[:200])

# T5：ttimg.py / judge_shot.py 已接入 h3secrets，且不再回到 os.environ.get 回退
for rel, var in [("ttimg.py", "KEY"), ("judge_shot.py", "VLM_KEY")]:
    src = (ROOT / rel).read_text(encoding="utf-8")
    ok = ("_lk888_key()" in src) and ('os.environ.get(\n    "LK888_KEY"' not in src) and ('os.environ.get("LK888_KEY"' not in src)
    check("T5 %s 已接入 h3secrets" % rel, ok)

print()
if FAIL:
    print("FAILED %d: %s" % (len(FAIL), FAIL))
    sys.exit(1)
print("P0 ALL PASS")
