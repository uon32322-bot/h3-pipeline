#!/bin/zsh
# 在 H3 生产服务器（nmb2 4090）上执行远程脚本（stdin 传入），自动重试 SSH 偶发失败
# 用法: ./remote2.sh <本地脚本文件>
HOST=connect.nmb2.seetacloud.com
PORT=36229
PASS='83HeROPNEhg5'

for i in 1 2 3 4 5 6; do
  out=$(sshpass -p "$PASS" ssh -o StrictHostKeyChecking=no -o ConnectTimeout=25 \
        -o ServerAliveInterval=15 -p "$PORT" root@"$HOST" 'bash -s' < "$1" 2>&1)
  rc=$?
  if [ $rc -eq 0 ]; then
    print -r -- "$out"
    exit 0
  fi
  print -r -- "[retry $i] rc=$rc"
  sleep 3
done
print -r -- "$out"
exit 1
