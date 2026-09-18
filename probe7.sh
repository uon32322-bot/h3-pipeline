echo "=== GPU 现状 ==="
nvidia-smi --query-gpu=utilization.gpu,memory.used,memory.total --format=csv,noheader
nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv,noheader 2>/dev/null | head
echo
echo "=== HF 直连（官方 8 步 LoRA）==="
curl -sIL --max-time 20 "https://huggingface.co/lightx2v/Minimax-h3-Turbo/resolve/main/minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors" 2>&1 | grep -iE "^HTTP/|content-length|^location" | head -8
echo "=== hf-mirror 镜像 ==="
curl -sIL --max-time 20 "https://hf-mirror.com/lightx2v/Minimax-h3-Turbo/resolve/main/minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors" 2>&1 | grep -iE "^HTTP/|content-length" | head -6
echo
echo "=== 下载工具 ==="
for t in huggingface-cli hf aria2c wget curl git-lfs; do printf "%-18s %s\n" "$t" "$(which $t 2>/dev/null || echo MISSING)"; done
echo "=== HF 环境变量 ==="
env | grep -iE "HF_|HUGGING" || echo "(none)"
echo
echo "=== KJNodes 是否装了（官方 Sage Attention 接入依赖）==="
ls /root/ComfyUI/custom_nodes 2>/dev/null | grep -i kj || echo "NO-KJNODES"
echo "=== ComfyUI 原生 sage 启动参数支持 ==="
grep -n "sage" /root/ComfyUI/comfy/cli_args.py 2>/dev/null | head -5
echo
echo "=== 数据盘余量 ==="
df -h /root/autodl-tmp | tail -1
