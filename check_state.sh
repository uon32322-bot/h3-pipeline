#!/bin/bash
echo "=== batch4 log ==="
cat /root/ComfyUI/input/v6/batch4.log 2>/dev/null || echo "no batch4.log"
echo "=== Y logs ==="
ls -la /root/ComfyUI/input/v6/log_Y*.txt 2>/dev/null
echo "=== queue ==="
curl -s -m 5 http://127.0.0.1:6006/queue 2>/dev/null | head -c 500
echo
echo "=== running ==="
ps aux | grep -E 'test_r2v|main.py' | grep -v grep | head -5
echo "=== last 5 mp4 ==="
ls -lt /root/ComfyUI/output/video/V6_*.mp4 2>/dev/null | head -5