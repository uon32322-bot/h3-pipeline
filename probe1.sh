echo "=== HOST ==="; hostname; date; echo
echo "=== GPU ==="; nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv 2>&1 | head -5
echo "=== DISK ==="; df -h / /root /root/autodl-tmp 2>/dev/null | grep -v tmpfs
echo "=== PYTHON ==="; python3 -V 2>&1
echo "=== /root TOP ==="; ls -la /root 2>/dev/null | head -40
echo "=== AUTODL-TMP TOP ==="; ls -la /root/autodl-tmp 2>/dev/null | head -40
echo "=== COMFY DIRS ==="; find / -maxdepth 4 -type d -name "ComfyUI*" 2>/dev/null | head -20
echo "=== LISTENING PORTS ==="; (ss -lntp 2>/dev/null || netstat -lntp 2>/dev/null) | head -25
echo "=== RUNNING PY ==="; ps aux | grep -iE "python|comfy" | grep -v grep | head -20
