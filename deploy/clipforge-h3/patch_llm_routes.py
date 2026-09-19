#!/usr/bin/env python3
"""Let LLM routes proceed when DEEPSEEK_API_KEY is present in env.

Minimal change: wrap each "missing LLM config" guard with an env escape hatch,
so a server-configured instance works even though the browser UI is unset.
The actual key/baseUrl/model resolution already happens in
script-engine/generator.ts via withLLMEnvFallback().
"""
import re
import shutil
import time

FILES = [
    "/root/clipforge_src/src/app/api/ad-template/generate/route.ts",
    "/root/clipforge_src/src/app/api/llm/script/route.ts",
    "/root/clipforge_src/src/app/api/llm/publish/route.ts",
    "/root/clipforge_src/src/app/api/topic/script/route.ts",
    "/root/clipforge_src/src/app/api/project/[id]/script-judge/route.ts",
    "/root/clipforge_src/src/app/api/project/[id]/dub/route.ts",
]

# matches:  <indent>if (!llmConfig?... ) {
PATTERN = re.compile(r"^([ \t]*)if \((!llmConfig\?[^\n]*?)\) \{$", re.M)

for path in FILES:
    try:
        src = open(path).read()
    except FileNotFoundError:
        print(f"MISSING  {path}")
        continue

    if "DEEPSEEK_API_KEY" in src:
        print(f"ALREADY  {path}")
        continue

    matches = list(PATTERN.finditer(src))
    if not matches:
        print(f"NO MATCH {path}")
        continue

    shutil.copy2(path, f"{path}.bak-{time.strftime('%Y%m%d-%H%M%S')}")

    def repl(m):
        indent, cond = m.group(1), m.group(2)
        return (
            f"{indent}if (({cond}) && !process.env.DEEPSEEK_API_KEY) {{"
        )

    new_src, n = PATTERN.subn(repl, src)
    open(path, "w").write(new_src)
    print(f"PATCHED  {path}  ({n} guard(s))")
