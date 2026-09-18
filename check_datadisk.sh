#!/usr/bin/env bash
# ============================================================================
# H3 带货视频流水线 · 数据盘合规自检（v2.7）
#
#  用法：  bash check_datadisk.sh
#  性质：  【只读】—— 只报告，不删除、不移动任何文件
#
#  依据：  部署指南 §1.8 —— 所有部署与数据必须落数据盘 /root/autodl-tmp/
#  退出码： 0 = 全绿；1 = 有告警（系统盘存在本项目产物）
# ============================================================================
set -u

ROOT="${H3P_ROOT:-/root/autodl-tmp/h3p}"
WARN=0
note() { printf '  %s %s\n' "$1" "$2"; }
warn() { WARN=$((WARN + 1)); note "⚠️ " "$1"; }
ok()   { note "✅" "$1"; }

echo "============================================================"
echo " 数据盘合规自检  |  H3P_ROOT=$ROOT"
echo " 时间：$(date '+%F %T')"
echo "============================================================"

# ---------- 1. 两块盘容量 ----------
echo
echo "[1] 磁盘容量"
df -h / /root/autodl-tmp 2>/dev/null | sed 's/^/    /' || warn "df 读取失败"

# 系统盘使用率超过 80% 报警
USE=$(df -P / 2>/dev/null | awk 'NR==2{gsub("%","",$5); print $5}')
if [ -n "${USE:-}" ]; then
  if [ "$USE" -ge 80 ]; then warn "系统盘使用率 ${USE}%（≥80% 需清理，否则实例可能异常）"
  else ok "系统盘使用率 ${USE}%"; fi
fi

# ---------- 2. 系统盘高危缓存（应为空 / 已软链到数据盘） ----------
echo
echo "[2] 系统盘上的高危缓存（本项目不应在此产生数据）"
# 判定：目录不存在 = ✅；是软链且指向数据盘 = ✅（已重定向）；实体目录在系统盘 = ⚠️
DATA_DISK="/root/autodl-tmp"
check_cache() {
  d="$1"
  if [ ! -e "$d" ] && [ ! -L "$d" ]; then
    ok "$d 不存在"
    return
  fi
  if [ -L "$d" ]; then
    tgt=$(readlink -f "$d" 2>/dev/null)
    case "$tgt" in
      "$DATA_DISK"/*) ok "$d → $tgt（已软链到数据盘）" ;;
      *)              warn "$d 是软链但指向系统盘：$tgt（应指向 $DATA_DISK 下）" ;;
    esac
    return
  fi
  sz=$(du -sh "$d" 2>/dev/null | cut -f1)
  warn "$d = ${sz:-?}（实体目录在系统盘，应重定向到 \$H3P_ROOT/cache）"
}
for d in "$HOME/.cache" "$HOME/.cache/pip" "$HOME/.cache/huggingface" "$HOME/.cache/torch" \
         "$HOME/.triton" "$HOME/.cache/whisper" "$HOME/.cache/torch_extensions"; do
  check_cache "$d"
done
# huggingface 可能被独立挂成实体目录（即便 .cache 是软链）
if [ -d "$HOME/.cache/huggingface" ] && [ ! -L "$HOME/.cache/huggingface" ]; then
  HF_REAL=$(readlink -f "$HOME/.cache/huggingface" 2>/dev/null)
  case "$HF_REAL" in
    "$DATA_DISK"/*) : ;;
    *) warn "HF 缓存实体位于系统盘：$HF_REAL" ;;
  esac
fi

# ---------- 3. 系统盘上 /root 下的大目录（非 autodl-tmp） ----------
echo
echo "[3] 系统盘 /root 下占用 Top（应只有 autodl-tmp 是大头）"
if [ -d /root ]; then
  du -sh /root/* 2>/dev/null | sort -rh | head -8 | sed 's/^/    /'
else
  ok "/root 不存在（非 AutoDL 环境，本节跳过）"
fi
# 明确检查本项目是否被误放在系统盘
for bad in "/root/h3p" "/root/comfy" "/root/models" "/root/output"; do
  if [ -e "$bad" ]; then warn "系统盘存在本项目同名目录：$bad（应迁至 $ROOT）"; fi
done

# ---------- 4. 临时目录 ----------
echo
echo "[4] 临时目录"
if [ -d "$ROOT/tmp" ]; then
  ok "\$TMPDIR 目标存在：$ROOT/tmp（$(du -sh "$ROOT/tmp" 2>/dev/null | cut -f1)）"
else
  warn "$ROOT/tmp 不存在 —— 请先 source env_datadisk.sh"
fi
TMP_SZ=$(du -sh /tmp 2>/dev/null | cut -f1)
echo "    /tmp 当前体积：${TMP_SZ:-?}（>5G 且含本项目中间文件需注意）"

# ---------- 5. Python 环境（是否污染系统 python） ----------
echo
echo "[5] Python 环境"
PY_PREFIX=$(python3 -c 'import sys; print(sys.prefix)' 2>/dev/null || echo "unknown")
echo "    python3 -m venv 前缀：$PY_PREFIX（venv 内应为 $ROOT/venv）"
if [ -d "$ROOT/venv/bin" ]; then
  ok "venv 已存在：$ROOT/venv"
else
  warn "venv 未创建 —— source env_datadisk.sh 会自动创建"
fi
# 系统 python 是否被装了重型包
if python3 -c 'import torch' 2>/dev/null; then
  TORCH_AT=$(python3 -c 'import torch,os; print(os.path.dirname(torch.__file__))' 2>/dev/null)
  case "$TORCH_AT" in
    */venv/*|*autodl-tmp*) ok "torch 在数据盘路径：$TORCH_AT（若为系统路径但 ComfyUI 自带，可接受）" ;;
    *) note "ℹ️ " "torch 位于 $TORCH_AT —— 若这是共享 ComfyUI 自带环境则正常；本项目自有依赖请装进 \$ROOT/venv" ;;
  esac
fi

# ---------- 6. 数据盘项目结构 ----------
echo
echo "[6] 数据盘项目结构"
if [ -d "$ROOT" ]; then
  for sub in models comfy input output temp logs venv cache; do
    if [ -d "$ROOT/$sub" ]; then ok "$ROOT/$sub"
    else warn "$ROOT/$sub 缺失"; fi
  done
  echo "    项目根体积：$(du -sh "$ROOT" 2>/dev/null | cut -f1)"
else
  warn "项目根 $ROOT 不存在 —— 需重建（见部署指南 Phase 0 步骤 ③）"
fi

# ---------- 7. 结论 ----------
echo
echo "============================================================"
if [ "$WARN" -eq 0 ]; then
  echo " ✅ 全绿：所有缓存 / 环境 / 数据均在数据盘，系统盘无本项目产物"
else
  echo " ⚠️  $WARN 项告警 —— 请在装包与下载之前先 source env_datadisk.sh"
  echo "     提示：本脚本【只报告不清理】。确认后再自行迁移对应目录到 $ROOT。"
fi
echo "============================================================"
exit $([ "$WARN" -eq 0 ] && echo 0 || echo 1)
