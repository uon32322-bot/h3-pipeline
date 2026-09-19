#!/bin/bash
fuser -k 3000/tcp 2>/dev/null
sleep 2
pkill -9 -f 'next-server' 2>/dev/null
pkill -9 -f 'next dev' 2>/dev/null
sleep 2
cd /root/clipforge_src
rm -f /tmp/cf_dev.log
setsid nohup pnpm dev -p 3000 > /tmp/cf_dev.log 2>&1 < /dev/null &
disown
echo "launched"
