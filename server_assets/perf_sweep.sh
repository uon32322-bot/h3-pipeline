#!/usr/bin/env bash
# ============================================================================
# H3 提速扫参台 —— 逐个配置重启 ComfyUI 并跑同一段，记录墙钟 + md5 + 步速
#
#  设计要点：
#   ① 单变量：每个 case 只改一个（或一组）启动开关，其余全同（同段/同帧/同 prompt/
#      同 seed/同 LoRA/同 steps）→ 差异可归因。
#   ② md5 判等价：--async-offload / --vram-headroom 属内存管理改动、不改数值，
#      若产物 md5 与 sage 基线一致 ⇒ 逐比特相同 ⇒ 零画质风险，无需再逐帧看图。
#      md5 不同则标记 NEEDS_VISUAL，交给 1:1 原生像素复核。
#   ③ 全量留证：每 case 自己的 comfyui 日志 + client 日志 + 一行 JSON 结果。
#   ④ 失败不中断：某 case 起不来/跑挂，写 FAILED 继续下一个。
#
#  用法： nohup bash perf_sweep.sh > /dev/null 2>&1 &
#  查进度： cat $ROOT/logs/perf_sweep.jsonl
#  看当前： tail -3 $ROOT/logs/perf_sweep.jsonl ; tail -3 $ROOT/logs/sw_<case>.log
# ============================================================================
set -u
ROOT="${H3P_ROOT:-/root/autodl-tmp/h3p}"
PY="${PY:-python3}"
PORT="${H3P_PORT:-6011}"
RESULT="$ROOT/logs/perf_sweep.jsonl"
FIRST="$ROOT/input/h3stg_seg01_first.png"
LAST="$ROOT/input/h3stg_seg01_last.png"
PROMPT="$ROOT/output/e2e_real/prompt/seg1.txt"
LORA="minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors"
SEED=12345

log(){ printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*"; }

stop_comfy(){
  pkill -f "main.py --port $PORT" 2>/dev/null
  for i in $(seq 1 20); do
    sleep 2
    pgrep -f "main.py --port $PORT" >/dev/null || break
  done
  pkill -9 -f "main.py --port $PORT" 2>/dev/null
  sleep 3
  log "comfy stopped"
}

wait_ready(){
  for i in $(seq 1 40); do
    sleep 3
    if curl -s -m 3 "http://127.0.0.1:$PORT/system_stats" >/dev/null 2>&1; then
      log "comfy ready (${i}x3s)"; return 0
    fi
  done
  log "comfy READY TIMEOUT"; return 1
}

# run_case <NAME> [ENV=VAL ...]
run_case(){
  local name="$1"; shift
  log "=== CASE $name  env: $* ==="
  stop_comfy
  local clog="$ROOT/logs/sw_${name}_comfy.log"
  # 用生产启动器（含 [7] sage 生效校验）
  env "$@" setsid nohup bash "$ROOT/scripts/start_comfyui.sh" auto > "$clog" 2>&1
  # start_comfyui.sh 自己 nohup 了 ComfyUI；这里只等就绪
  if ! wait_ready; then
    echo "{\"case\":\"$name\",\"status\":\"FAILED_START\",\"env\":\"$*\"}" >> "$RESULT"
    return 1
  fi
  # sage 生效校验（不看日志不算生效）
  local sage_ok="no"
  grep -aq "Using sage attention" "$clog" && sage_ok="yes"
  # 等 GPU 真空闲（避免上一个个例的残留）
  for i in $(seq 1 15); do
    U=$(nvidia-smi --query-gpu=utilization.gpu --format=csv,noheader,nounits | head -1)
    [ "$U" -lt 5 ] && break
    sleep 4
  done

  local t0=$(date +%s)
  $PY "$ROOT/scripts/test_fl2v_official.py" "$FIRST" "$LAST" "$PROMPT" \
      8.0 0.98 8 "$name" "$SEED" --port "$PORT" --aspect 9:16 \
      --lora "$LORA" --no-lease > "$ROOT/logs/sw_${name}.log" 2>&1
  local rc=$?
  local t1=$(date +%s)

  local f; f=$(ls -t "$ROOT/output/${name}_"*.mp4 2>/dev/null | head -1)
  local md5="NONE" size=0
  if [ -n "${f:-}" ] && [ -f "$f" ]; then
    md5=$(md5sum "$f" | cut -d' ' -f1); size=$(stat -c%s "$f")
  fi
  # 从 comfy 日志取步速与内部耗时
  local sit exec_line
  sit=$(grep -aoE '[0-9]+\.[0-9]+s/it' "$clog" | tail -1)
  exec_line=$(grep -a "Prompt executed in" "$clog" | tail -1 | sed 's/.*Prompt executed in //')
  echo "{\"case\":\"$name\",\"status\":\"$([ $rc -eq 0 ] && echo OK || echo CLIENT_RC$rc)\",\"env\":\"$*\",\"sage\":\"$sage_ok\",\"wall_s\":$((t1-t0)),\"comfy_exec\":\"$exec_line\",\"sit\":\"$sit\",\"md5\":\"$md5\",\"size\":$size,\"file\":\"${f:-}\"}" >> "$RESULT"
  log "=== CASE $name DONE wall=$((t1-t0))s exec=$exec_line sit=$sit sage=$sage_ok md5=$md5 ==="
}

: > "$RESULT"
log "sweep start; results -> $RESULT"

# ---- 基线：sage only（用新启动器复测，作为 md5 参照）----
run_case SAGEONLY H3P_SAGE=1
# ---- 单变量 A：vram-headroom 3 ----
run_case HEAD3    H3P_SAGE=1 H3P_HEADROOM=3
# ---- 单变量 B：async-offload 2 ----
run_case ASYNC2   H3P_SAGE=1 H3P_ASYNCOFF=2
# ---- 组合：async + headroom ----
run_case ASYNC3   H3P_SAGE=1 H3P_ASYNCOFF=3
# ---- 组合：async2 + headroom3 ----
run_case COMBO    H3P_SAGE=1 H3P_ASYNCOFF=2 H3P_HEADROOM=3

log "sweep finished"
