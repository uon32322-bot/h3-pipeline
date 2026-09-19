#!/bin/bash
# SSH tunnel: hao local ports -> nmb2 (Bridge 8900, FileServer 8901, ComfyUI 6011)
pkill -f "ssh.*-L 8900" 2>/dev/null
sleep 1
sshpass -f /root/.nmb2_pw autossh -M 0 -f -N \
  -o StrictHostKeyChecking=no \
  -o UserKnownHostsFile=/dev/null \
  -o ServerAliveInterval=30 \
  -o ServerAliveCountMax=3 \
  -o ExitOnForwardFailure=yes \
  -p 36229 \
  -L 0.0.0.0:8900:127.0.0.1:8900 \
  -L 0.0.0.0:8901:127.0.0.1:8901 \
  -L 0.0.0.0:6011:127.0.0.1:6011 \
  root@connect.nmb2.seetacloud.com
echo "tunnel started"
