#!/usr/bin/env python3
"""Add model whitelist validation to the H3 bridge.

Before: any model string was accepted and submitted to ComfyUI, so a typo or a
probe request burned a full GPU slot (~30 min) on a job nobody wanted.

After: unknown models are rejected with 400 before anything touches the queue.
Names containing "h3" resolve to the matching local mode (text / image /
reference), so UI variants still work.
"""
import shutil
import time

F = "/root/h3_bridge_v2.py"
shutil.copy2(F, f"{F}.bak-{time.strftime('%Y%m%d-%H%M%S')}")
src = open(F).read()

if "_resolve_model" in src:
    print("patcher: ALREADY PATCHED")
    raise SystemExit(0)

# 1) ensure HTTPException import
if "from fastapi import" in src and "HTTPException" not in src.split("\n\n")[0]:
    import re
    m = re.search(r"from fastapi import ([^\n]+)", src)
    if m and "HTTPException" not in m.group(1):
        src = src.replace(m.group(0), f"from fastapi import {m.group(1).rstrip()}, HTTPException", 1)
        print("import: HTTPException ADDED")
    else:
        print("import: HTTPException already present (or pattern differs)")
else:
    print("import: skipped")

# 2) insert resolver right before the generateVideo route
ROUTE = '@app.post("/api/v1/model/generateVideo")'
RESOLVER = '''_KNOWN_MODELS = {m["model"] for m in MODELS}


def _resolve_model(name: str) -> str | None:
    """Map an incoming model id onto a local H3 mode, or None if unsupported.

    Unknown ids must be rejected BEFORE queuing: a bad model previously burned a
    full GPU slot on a job that could never succeed.
    """
    if not name:
        return None
    if name in _KNOWN_MODELS:
        return name
    low = name.lower()
    if "h3" in low:
        if "image" in low:
            return "minimax/h3/image-to-video"
        if "reference" in low or "ref2v" in low:
            return "minimax/h3/reference-to-video"
        return "minimax/h3/text-to-video"
    return None


'''
if ROUTE in src:
    src = src.replace(ROUTE, RESOLVER + ROUTE, 1)
    print("resolver: INSERTED")
else:
    print("resolver: ROUTE NOT FOUND")
    raise SystemExit(1)

# 3) validate inside generate_video
OLD = '''async def generate_video(req: SubmitRequest, bg: BackgroundTasks):
    task_id = f"h3_{uuid.uuid4().hex[:12]}"'''
NEW = '''async def generate_video(req: SubmitRequest, bg: BackgroundTasks):
    resolved = _resolve_model(req.model)
    if resolved is None:
        log.warning(f"REJECT unknown model: {req.model!r}")
        raise HTTPException(
            status_code=400,
            detail=f"unsupported model: {req.model!r}; expected one of {sorted(_KNOWN_MODELS)}",
        )
    if resolved != req.model:
        log.info(f"model alias: {req.model!r} -> {resolved!r}")
    req.model = resolved
    task_id = f"h3_{uuid.uuid4().hex[:12]}"'''
if NEW.split("\n")[1] in src:
    print("guard: ALREADY")
elif OLD in src:
    src = src.replace(OLD, NEW, 1)
    print("guard: PATCHED")
else:
    print("guard: PATTERN NOT FOUND")
    raise SystemExit(1)

open(F, "w").write(src)
print("--- verify ---")
for i, line in enumerate(open(F).read().splitlines(), 1):
    if "_resolve_model" in line or "HTTPException(" in line or "req.model = resolved" in line:
        print(f"  {i}: {line.strip()}")
