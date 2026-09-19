#!/usr/bin/env python3
"""Patch ClipForge so LLM calls fall back to DEEPSEEK_* env vars.

Single-user private deployment: the UI stores LLM settings in browser
localStorage, so a server-hosted instance needs an env fallback to be
configurable from the server side.
"""
import shutil
import time

F = "/root/clipforge_src/src/lib/script-engine/generator.ts"
shutil.copy2(F, f"{F}.bak-{time.strftime('%Y%m%d-%H%M%S')}")
src = open(F).read()

ANCHOR = "/** Create an OpenAI client (shared factory: SDK retries + free-pool 402 retry, see lib/llm-error) */"

HELPER = '''/**
 * Server-side LLM env fallback (single-user private deployment).
 *
 * ClipForge persists LLM settings in the browser's localStorage (zustand persist),
 * so a server-hosted instance whose UI has no LLM configured would have no key at
 * all. Fill any missing field from DEEPSEEK_* in .env.local.
 */
export function withLLMEnvFallback(config: LLMConfig | undefined): LLMConfig {
  const c = (config ?? {}) as Partial<LLMConfig>;
  const apiKey = c.apiKey || process.env.DEEPSEEK_API_KEY || "";
  const baseUrl = c.baseUrl || process.env.DEEPSEEK_BASE_URL || "https://api.deepseek.com";
  const model = c.model || process.env.DEEPSEEK_MODEL || "deepseek-chat";
  const visionModel = c.visionModel || process.env.DEEPSEEK_VISION_MODEL || undefined;
  return { apiKey, baseUrl, model, ...(visionModel ? { visionModel } : {}) };
}

'''

PATCHES = [
    (
        "helper",
        None,
        None,
    ),
    (
        "generateScript",
        "export async function generateScript(input: ScriptInput): Promise<GeneratedScript[]> {\n"
        "  const client = createClient(input.llmConfig);",
        "export async function generateScript(input: ScriptInput): Promise<GeneratedScript[]> {\n"
        "  input = { ...input, llmConfig: withLLMEnvFallback(input.llmConfig) };\n"
        "  const client = createClient(input.llmConfig);",
    ),
    (
        "generateScriptStream",
        "): AbortController {\n  const abortController = new AbortController();",
        "): AbortController {\n"
        "  input = { ...input, llmConfig: withLLMEnvFallback(input.llmConfig) };\n"
        "  const abortController = new AbortController();",
    ),
    (
        "analyzeProduct",
        "  config: LLMConfig,\n): Promise<string> {\n  const client = createClient(config);",
        "  config: LLMConfig,\n): Promise<string> {\n"
        "  config = withLLMEnvFallback(config);\n"
        "  const client = createClient(config);",
    ),
]

# helper first
if "withLLMEnvFallback" in src:
    print("helper: ALREADY PRESENT")
else:
    src = src.replace(ANCHOR, HELPER + ANCHOR, 1)
    print("helper: INSERTED")

for name, old, new in PATCHES[1:]:
    if new in src:
        print(f"{name}: ALREADY")
    elif old in src:
        src = src.replace(old, new, 1)
        print(f"{name}: PATCHED")
    else:
        print(f"{name}: PATTERN NOT FOUND")

open(F, "w").write(src)
print("--- verify ---")
for i, line in enumerate(open(F).read().splitlines(), 1):
    if "withLLMEnvFallback" in line:
        print(f"  {i}: {line.strip()}")
