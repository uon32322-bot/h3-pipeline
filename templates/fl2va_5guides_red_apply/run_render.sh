#!/bin/bash
# FL2VA + 5 AddGuide 跑批脚本
# 用法:
#   ./run_render.sh              # 默认路径
#   WF=/path WF_OUT=/path ./run_render.sh
#
# 依赖:
#   - ComfyUI 已启动, :6011
#   - /root/autodl-tmp/h3p/input/ 下有 fl2va_cell1.png ~ fl2va_cell6.png
#
# 步骤:
#   1. 把 build_workflow.py 生成的 JSON 包成 {"prompt": nodes} 提交
#   2. 轮询 history 直到 completed
#   3. 产物输出到 /root/autodl-tmp/h3p/output/red_apply_fl2va_5guides_*.mp4
set -u
H3P="${H3P:-/root/autodl-tmp/h3p}"
WF="${WF:-$H3P/out/test_apply/fl2va_5guides_workflow.json}"
LOG="${LOG:-$H3P/out/test_apply/fl2va_5g_run.log}"
COMFY="${COMFY:-http://127.0.0.1:6011}"

WRAP=/tmp/fl2va_5g_wrap.json

# 1. 包 {"prompt": nodes}
python3 -c "
import json
d = json.load(open('$WF'))
json.dump({'prompt': d}, open('$WRAP', 'w'))
"

echo "=== FL2VA + 5 AddGuide 提交 ===" > $LOG
echo "WF=$WF" >> $LOG

# 2. 提交
RESP=$(curl -s -m 30 -X POST "$COMFY/prompt" \
  -H "Content-Type: application/json" \
  --data-binary "@$WRAP" 2>&1)
echo "$RESP" >> $LOG
PID=$(echo "$RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('prompt_id',''))" 2>/dev/null)
echo "prompt_id = $PID" >> $LOG

if [ -z "$PID" ]; then
  echo "❌ 没拿到 prompt_id" >> $LOG
  exit 1
fi

# 3. 轮询 (最多 25 分钟)
START=$(date +%s)
LAST=""
while true; do
  ELAPSED=$(($(date +%s) - START))
  if [ $ELAPSED -gt 1500 ]; then
    echo "TIMEOUT 25min" >> $LOG
    break
  fi
  MSG=$(curl -s -m 5 "$COMFY/history/$PID" 2>/dev/null | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    if '$PID' not in d: print('pending'); sys.exit()
    e = d['$PID']
    if e.get('status',{}).get('completed'): print('COMPLETED')
    else:
        msgs = e.get('status',{}).get('messages',[])
        if msgs:
            tail = msgs[-1]
            if isinstance(tail, list) and len(tail)>=2: print(str(tail[1])[:200])
            else: print(str(tail)[:200])
        else: print('running')
except Exception as ex: print(f'err:{ex}')
")
  if [ "$MSG" = "COMPLETED" ]; then
    echo "DONE ${ELAPSED}s" >> $LOG
    curl -s -m 6 "$COMFY/history/$PID" 2>/dev/null | python3 -c "
import sys, json
e = json.load(sys.stdin)['$PID']
for n,o in (e.get('outputs') or {}).items():
    if o.get('videos'):
        for v in o['videos']:
            print(n, v.get('subfolder',''), v.get('filename'))" >> $LOG
    break
  fi
  if [ "$MSG" != "$LAST" ] && [ -n "$MSG" ]; then
    echo "[${ELAPSED}s] $MSG" >> $LOG
    LAST="$MSG"
  fi
  sleep 10
done