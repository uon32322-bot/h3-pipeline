for i in $(seq 1 80); do
  sleep 25
  if grep -q 'ALLDONE4' /root/ComfyUI/input/v6/batch4.log 2>/dev/null; then break; fi
done
cat /root/ComfyUI/input/v6/batch4.log
echo '--- GPU ---'
nvidia-smi --query-gpu=utilization.gpu,memory.used --format=csv