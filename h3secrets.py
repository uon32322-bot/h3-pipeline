#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""统一密钥加载（fail-closed）。

规则：
  1) 优先读环境变量；
  2) 其次读 ~/.config/h3p/secrets.env（chmod 600，且在仓库之外）；
  3) 都没有 ⇒ 直接报错退出（EX_CONFIG=78），**绝不回退到硬编码默认值**。

任何密钥都不得写入源码、git、日志或对话。
"""
import os
import sys
from pathlib import Path

__all__ = ["get", "lk888_key"]

_SEARCH = []
if os.environ.get("H3P_SECRETS"):
    _SEARCH.append(Path(os.environ["H3P_SECRETS"]))
_SEARCH += [
    Path.home() / ".config" / "h3p" / "secrets.env",
    Path.home() / ".h3p" / "secrets.env",
]
# 仓库内的 .secrets.env（已在 .gitignore 中）——最后兜底，仍非常量
_SEARCH.append(Path(__file__).resolve().parent / ".secrets.env")

_CACHE = {}


def _load_file(p):
    out = {}
    try:
        text = p.read_text(encoding="utf-8")
    except Exception:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        out[k.strip()] = v
    return out


def get(name, required=True):
    """取密钥。required=True 且找不到时直接退出，不返回 None。"""
    if os.environ.get(name):
        return os.environ[name]
    if name not in _CACHE:
        val = None
        for p in _SEARCH:
            if p and p.exists():
                val = _load_file(p).get(name)
                if val:
                    break
        _CACHE[name] = val
    val = _CACHE[name]
    if val:
        return val
    if required:
        sys.stderr.write(
            "[secrets] 缺少 %s：请设置环境变量，或写入 ~/.config/h3p/secrets.env（chmod 600）。\n"
            "[secrets] 已查找：%s\n"
            "[secrets] 出于安全考虑不回退硬编码默认值 —— 拒绝继续执行。\n"
            % (name, [str(p) for p in _SEARCH])
        )
        raise SystemExit(78)
    return None


def lk888_key():
    return get("LK888_KEY")
