echo "=== 本地脚本依赖的节点类（对照用） ==="
grep -oE '"class_type": *"[^"]+"' /root/ComfyUI/*.py 2>/dev/null | head -5
echo "--- /proc 中已加载的权重文件 ---"
ls -l /proc/80234/fd 2>/dev/null | grep -iE "safetensors|\.pth" | head -20

echo
echo "=== 官方 H3 节点是否在核心库 ==="
ls /root/ComfyUI/comfy_extras/ 2>/dev/null | grep -iE "minimax|h3"
grep -rlE "MiniMaxH3" /root/ComfyUI/comfy_extras/ 2>/dev/null | head -10
python3 - <<'PY' 2>/dev/null
import glob, re, os
hits = []
for p in glob.glob('/root/ComfyUI/comfy_extras/*.py') + ['/root/ComfyUI/nodes.py']:
    try: t = open(p, encoding='utf-8', errors='ignore').read()
    except: continue
    for m in set(re.findall(r'"(MiniMax[A-Za-z0-9_]*)"', t)) | set(re.findall(r'"(H3[A-Za-z0-9_]*)"', t)):
        hits.append((m, os.path.basename(p)))
for h in sorted(hits): print('CORE-NODE', h[0], '<-', h[1])
PY

echo
echo "=== official workflow templates ==="
python3 -c "import comfyui_workflow_templates as t, os; print(os.path.dirname(t.__file__))" 2>/dev/null
find / -path /proc -prune -o -iname "*minimax*h3*.json" -print 2>/dev/null | head -20
find /root/ComfyUI/user -iname "*.json" 2>/dev/null | head -20

echo
echo "=== custom_nodes 列表（含禁用状态） ==="
ls -la /root/ComfyUI/custom_nodes/ 2>/dev/null

echo
echo "=== sageattention / torch ==="
/root/miniconda3/bin/python -m pip list 2>/dev/null | grep -iE "sage|torch|triton|xformers|accelerate"
python3 -c "import torch; print('torch', torch.__version__, 'cuda', torch.version.cuda, 'arch_list', torch.cuda.get_arch_list())" 2>&1 | tail -2

echo
echo "=== 磁盘精确 ==="
df -h / /root/ComfyUI/models 2>/dev/null
stat -c "%n size=%s blocks=%b links=%h" /root/ComfyUI/models/diffusion_models/minimax_h3_fl2va_pruned_int8_convrot.safetensors 2>/dev/null
du -sh --apparent-size /root/ComfyUI/models 2>/dev/null
du -sh /root/ComfyUI/models/*/ 2>/dev/null

echo
echo "=== 其他项目的模型目录（隔离用） ==="
ls -la /root/autodl-tmp/ComfyUI-models/ 2>/dev/null
ls -la /root/autodl-tmp/models/ 2>/dev/null
echo "--- comfy-8188 ---"
ls -la /root/autodl-tmp/comfy-8188/ 2>/dev/null | head -20
echo "--- daihuan_v2 ---"
ls -la /root/autodl-tmp/daihuan_v2/ 2>/dev/null | head -25
