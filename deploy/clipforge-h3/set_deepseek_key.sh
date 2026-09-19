#!/bin/bash
# 交互式写入 DeepSeek API Key —— 不回显、不进 bash history、不经过任何对话
set -e
ENVF=/root/clipforge_src/.env.local
[ -f "$ENVF" ] || { echo "缺少 $ENVF"; exit 1; }

printf '请粘贴 DeepSeek API Key（输入时不显示），然后回车: '
read -s K
echo
[ -n "$K" ] || { echo "未输入，已取消"; exit 1; }

# 去掉旧值后追加
grep -v '^DEEPSEEK_API_KEY=' "$ENVF" > "$ENVF.tmp"
mv "$ENVF.tmp" "$ENVF"
printf 'DEEPSEEK_API_KEY=%s\n' "$K" >> "$ENVF"
chmod 600 "$ENVF"
unset K

echo "✓ Key 已写入 $ENVF（权限 600）"
echo "✓ 当前 DEEPSEEK_* 配置："
grep '^DEEPSEEK_' "$ENVF" | sed 's/\(DEEPSEEK_API_KEY=\).*/\1***(已隐藏)/'
