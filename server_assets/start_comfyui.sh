#!/usr/bin/env bash
# ============================================================================
# H3 带货视频流水线 · 本项目 ComfyUI 独立实例启动器
#
#  用法：  bash start_comfyui.sh [vram模式]
#          vram模式 ∈ {auto|low|normal|high|novram}   默认 auto
#
#  设计要点（破一条即失控）：
#    ① 显式指定 4 个目录 → 全部落数据盘，系统盘不留任何本项目产物
#    ② --extra-model-paths-config → 只读本项目 $ROOT/models，不碰共享实例
#    ③ 独立端口 6011 → 与共享实例 6006 互不干扰
#    ④ 用 $ROOT/venv/bin/python → 依赖不写进系统 python
#    ⑤ 显存策略：本机 3090 24G 上还有他人项目常驻（heygem 约 7.8G）
#       ⇒ 可用显存可能只有 ~15G，而 H3 生成峰值约 20G
#       ⇒ 默认走 auto（ComfyUI 自动分块），必要时降级 low
# ============================================================================
set -u

ROOT="${H3P_ROOT:-/root/autodl-tmp/h3p}"
COMFY="$ROOT/comfy/ComfyUI"
PY="$ROOT/venv/bin/python"
PORT="${H3P_PORT:-6011}"
VRAM_MODE="${1:-auto}"
LOG="$ROOT/logs/comfyui_$(date +%Y%m%d_%H%M%S).log"
PIDFILE="$ROOT/logs/comfyui.pid"

say(){ printf '\n\033[1;36m%s\033[0m\n' "$*"; }
ok(){  printf '  \033[1;32m✅ %s\033[0m\n' "$*"; }
bad(){ printf '  \033[1;31m❌ %s\033[0m\n' "$*"; }
inf(){ printf '  · %s\n' "$*"; }

# ---------- 0. 前置检查 ----------
say "[0] 前置检查"
[ -d "$COMFY" ] || { bad "ComfyUI 未就位：$COMFY（先 clone）"; exit 1; }
[ -x "$PY" ]    || { bad "venv python 缺失：$PY"; exit 1; }
ok "ComfyUI  : $COMFY"
ok "python   : $($PY -V 2>&1)"
ok "venv     : $PY"

# 已在本项目目录之外？—— 防呆：脚本必须在数据盘
case "$ROOT" in
  /root/autodl-tmp/*) ok "项目根在数据盘：$ROOT" ;;
  *) bad "项目根不在数据盘（$ROOT）—— 违反 §1.8 存储硬规范，拒绝启动"; exit 1 ;;
esac

# ---------- 1. 环境重定向（缓存/临时全进数据盘） ----------
say "[1] 数据盘环境重定向"
source "$ROOT/scripts/env_datadisk.sh" >/dev/null 2>&1
ok "TMPDIR=$TMPDIR"
ok "PIP_CACHE_DIR=$PIP_CACHE_DIR"
ok "HF_HOME=$HF_HOME"

# ---------- 2. 目录参数 ----------
say "[2] 目录落位（全部数据盘）"
for d in comfy input output temp; do mkdir -p "$ROOT/$d"; done
# 🚨 base-directory 下的子结构必须预建齐：ComfyUI 在 prestartup 阶段会
#    os.listdir("$base/custom_nodes")，目录不存在直接 FileNotFoundError 崩溃
#    （2026-09-17 实测踩坑：main.py execute_prestartup_script()）
for d in custom_nodes models user; do mkdir -p "$ROOT/comfy/$d"; done
inf "--base-directory   $ROOT/comfy"
inf "  └ 已预建 custom_nodes / models / user（缺则启动崩溃）"
inf "--input-directory  $ROOT/input"
inf "--output-directory $ROOT/output"
inf "--temp-directory   $ROOT/temp"

# ---------- 3. 端口占用检查 ----------
say "[3] 端口检查"
if (ss -lnt 2>/dev/null || netstat -lnt 2>/dev/null) | grep -q ":$PORT "; then
  bad "端口 $PORT 已被占用 —— 换 H3P_PORT 或先停旧进程"; exit 1
fi
ok "端口 $PORT 空闲"

# ---------- 4. 显存现状 ----------
say "[4] 显存现状"
if command -v nvidia-smi >/dev/null 2>&1; then
  nvidia-smi --query-gpu=memory.total,memory.used,memory.free --format=csv,noheader | sed 's/^/    /'
  FREE_MB=$(nvidia-smi --query-gpu=memory.free --format=csv,noheader,nounits 2>/dev/null | head -1)
  inf "可用 ${FREE_MB} MiB（H3 生成峰值约 20 GiB ⇒ 不足时自动分块，速度换显存）"
fi

# ---------- 5. 组装启动参数 ----------
say "[5] 启动"
case "$VRAM_MODE" in
  low)    VRAM_ARGS="--lowvram" ;;
  normal) VRAM_ARGS="--normalvram" ;;
  high)   VRAM_ARGS="--highvram" ;;
  novram) VRAM_ARGS="--novram" ;;
  *)      VRAM_ARGS="" ;;   # auto：交给 ComfyUI 自动管理
esac

ARGS=(
  main.py
  --port "$PORT"
  --listen 0.0.0.0
  --enable-manager
  --enable-cors-header "*"
  --extra-model-paths-config "$ROOT/comfy/extra_model_paths.yaml"
  --base-directory "$ROOT/comfy"
  --input-directory "$ROOT/input"
  --output-directory "$ROOT/output"
  --temp-directory "$ROOT/temp"
  --preview-method none
  --disable-auto-launch
)
[ -n "$VRAM_ARGS" ] && ARGS+=("$VRAM_ARGS")

inf "cd $COMFY && $PY ${ARGS[*]}"
cd "$COMFY" || exit 1
setsid nohup "$PY" "${ARGS[@]}" > "$LOG" 2>&1 < /dev/null &
echo $! > "$PIDFILE"

# ---------- 6. 启动确认 ----------
say "[6] 启动确认"
for i in $(seq 1 30); do
  sleep 2
  if grep -qE "To see the GUI go to|Starting server" "$LOG" 2>/dev/null; then
    ok "服务已监听（耗时约 $((i*2))s）"
    grep -aE "To see the GUI go to|Starting server" "$LOG" | tail -2 | sed 's/^/    /'
    break
  fi
  if ! kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    bad "进程已退出 —— 见日志 $LOG"
    tail -25 "$LOG" | sed 's/^/    /'
    exit 1
  fi
done
(ss -lnt 2>/dev/null || netstat -lnt 2>/dev/null) | grep ":$PORT " | sed 's/^/    /'
echo
ok "PID=$(cat $PIDFILE)  日志=$LOG"
echo "  停止：kill \$(cat $PIDFILE)"
echo "  健康：curl -s http://127.0.0.1:$PORT/system_stats | head -c 300"
