#!/bin/bash
cd /Users/admin/WorkBuddy/2026-09-11-07-42-55/h3-pipeline
HOST=connect.westc.seetacloud.com; PORT=39494; PASS='BYhOphRPBpQB'
mkdir -p v6
echo "=== 连通性 ==="
sshpass -p "$PASS" ssh -o StrictHostKeyChecking=no -o ConnectTimeout=20 -p "$PORT" root@"$HOST" 'echo SSH_OK; uptime' 2>&1 | head -5
echo "=== 回传看板 ==="
for f in /tmp/board_y.png /tmp/board_v.png; do
  b=$(basename "$f")
  sshpass -p "$PASS" scp -o StrictHostKeyChecking=no -o ConnectTimeout=20 -P "$PORT" root@"$HOST":"$f" v6/"$b" && echo "ok $b"
done
ls -la v6/*.png 2>/dev/null