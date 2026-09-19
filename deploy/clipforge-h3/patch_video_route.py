#!/usr/bin/env python3
"""Make ClipForge's video route work with zero UI configuration.

Single-user private deployment: the browser UI stores provider settings in
localStorage, so a fresh server-hosted instance has provider/model/apiKey all
unset and every generation request is rejected before it reaches the H3 bridge.

Fix: give the destructured body fields env-backed defaults. Everything
downstream (createProvider, modelId, ai_tasks rows) then uses the effective
values automatically.
"""
import shutil
import time

F = "/root/clipforge_src/src/app/api/ai/video/route.ts"
shutil.copy2(F, f"{F}.bak-{time.strftime('%Y%m%d-%H%M%S')}")
src = open(F).read()

OLD = (
    '  const { provider: providerName, model, prompt, imageUrl, lastImageUrl, mode, '
    'apiKey, baseUrl, options, projectId, shotId, referenceVideoUrls, '
    'referenceImageUrls, referenceAudioUrls } = body;'
)

NEW = (
    '  // Env-backed defaults (single-user private deployment): a fresh instance whose\n'
    '  // UI has no provider configured still resolves to the local H3 bridge.\n'
    '  const {\n'
    '    provider: providerName = process.env.H3_PROVIDER || "atlas-cloud",\n'
    '    model = process.env.H3_DEFAULT_MODEL || "minimax/h3/text-to-video",\n'
    '    prompt, imageUrl, lastImageUrl, mode,\n'
    '    apiKey = process.env.H3_API_KEY || "local-h3",\n'
    '    baseUrl, options, projectId, shotId, referenceVideoUrls,\n'
    '    referenceImageUrls, referenceAudioUrls,\n'
    '  } = body;'
)

if "H3_DEFAULT_MODEL" in src:
    print("route: ALREADY PATCHED")
elif OLD in src:
    src = src.replace(OLD, NEW, 1)
    open(F, "w").write(src)
    print("route: PATCHED")
else:
    print("route: PATTERN NOT FOUND -> actual line:")
    for line in src.splitlines():
        if "provider: providerName" in line:
            print(repr(line))
            break
    raise SystemExit(1)

print("--- route verify ---")
for i, line in enumerate(open(F).read().splitlines(), 1):
    if "H3_PROVIDER" in line or "H3_DEFAULT_MODEL" in line or "H3_API_KEY" in line:
        print(f"  {i}: {line.strip()}")

# ---- .env.local: append H3_* while PRESERVING existing vars (incl. DEEPSEEK_API_KEY)
ENVF = "/root/clipforge_src/.env.local"
lines = [l for l in open(ENVF).read().splitlines() if not l.startswith(("H3_PROVIDER=", "H3_DEFAULT_MODEL=", "H3_API_KEY="))]
existing_keys = [l.split("=")[0] for l in lines if l and not l.startswith("#")]
lines += [
    "H3_PROVIDER=atlas-cloud",
    "H3_DEFAULT_MODEL=minimax/h3/text-to-video",
    "H3_API_KEY=local-h3",
]
open(ENVF, "w").write("\n".join(lines) + "\n")

print("--- .env.local (values of secrets hidden) ---")
for line in open(ENVF).read().splitlines():
    if line.startswith("DEEPSEEK_API_KEY="):
        print("  DEEPSEEK_API_KEY=***PRESERVED***")
    elif line:
        print(f"  {line}")
print(f"  [preserved pre-existing keys: {[k for k in existing_keys if k.startswith('DEEPSEEK_API_KEY')]}]")
