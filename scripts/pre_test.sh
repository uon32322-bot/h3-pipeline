#!/bin/bash
# 测试前自动 git commit 状态点（不传任何参数时，默认「测试前状态」）
set -u
R=$(git rev-parse --show-toplevel 2>/dev/null) || { echo "不在 git 工作区"; exit 1; }
MSG="${1:-测试前状态}"
cd "$R" || exit 1
git add -A 2>/dev/null
git diff --cached --quiet && { echo "ℹ️  工作区干净"; exit 0; }
TS=$(date '+%Y-%m-%d %H:%M:%S')
git commit -m "测试前状态 ($TS)

$MSG"
echo "  HEAD: $(git rev-parse --short HEAD)"
