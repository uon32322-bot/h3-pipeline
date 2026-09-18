#!/bin/zsh
# 在 H3 服务器上执行远程脚本（stdin 传入），自动重试 SSH 偶发 Permission denied
# 用法: ./remote.sh <本地脚本文件>
HOST=connect.westc.seetacloud.com
PORT=39494
PASS='BYhOphRPBpQB'

for i in 1 2 3 4 5 6; do
  out=$(sshpass -p "$PASS" ssh -o StrictHostKeyChecking=no -o ConnectTimeout=20 \
        -p "$PORT" root@"$HOST" 'bash -s' < "$1" 2>&1)
  rc=$?
  if [ $rc -eq 0 ]; then
    print -r -- "$out"
    exit 0
  fi
  sleep 3
done
print -r -- "$out"
exit 1
