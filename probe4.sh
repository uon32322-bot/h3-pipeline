echo "=== 节点来源定论（/object_info 的 python_module） ==="
python3 - <<'PY'
import json, urllib.request
names = ["MiniMaxH3TurboLoRA","MiniMaxH3TurboSampler","H3SparseAttentionAdvanced","H3MemoryOptimization",
         "ResolutionSelector","MiniMaxH3ImageToVideo","MiniMaxH3AddGuide","MiniMaxH3SigmaShift",
         "MiniMaxH3ReferenceToVideo","LoraLoaderModelOnly","BasicScheduler","SamplerCustomAdvanced"]
for c in names:
    try:
        d = json.load(urllib.request.urlopen("http://127.0.0.1:6006/object_info/" + c, timeout=15))
    except Exception as e:
        print("%-28s ERR %s" % (c, e)); continue
    if not d:
        print("%-28s ABSENT" % c); continue
    i = d.get(c, {})
    print("%-28s module=%-46s cat=%s" % (c, i.get("python_module"), i.get("category")))
PY

echo
echo "=== 活跃解释器的官方模板包 ==="
/root/miniconda3/bin/python -c "import comfyui_workflow_templates_json as t, os; p=os.path.dirname(t.__file__); print('PKG', p); import glob; [print(' ', os.path.basename(f)) for f in sorted(glob.glob(p+'/templates/*minimax_h3*'))]" 2>&1 | head -20

echo
echo "=== 系统盘（/）占用大头 ==="
du -x -h -d1 / 2>/dev/null | sort -h | tail -12
echo "--- / 上 >500M 的文件 ---"
find / -xdev -type f -size +500M 2>/dev/null | head -20 | while read f; do echo "$(du -h "$f" | cut -f1)  $f"; done

echo
echo "=== 挂载关系（确认 models 是否共享） ==="
mount 2>/dev/null | grep -iE "ComfyUI|autodl-tmp|models" | head -20

echo
echo "=== 谁在用这份共享模型库 ==="
ls -l /proc/*/fd 2>/dev/null | grep -oE "/root/autodl-tmp/ComfyUI-models/[^ ]*" | sort | uniq -c | sort -rn | head -10
for p in $(pgrep -f "main.py|app.py" 2>/dev/null | head -12); do
  cwd=$(readlink /proc/$p/cwd 2>/dev/null); echo "pid=$p cwd=$cwd cmd=$(tr '\0' ' ' < /proc/$p/cmdline 2>/dev/null | cut -c1-90)"
done

echo
echo "=== 其他 ComfyUI 实例的模型配置 ==="
for d in /root/autodl-tmp/aisiyi-comfyui /root/autodl-tmp/comfy-8188 /root/autodl-tmp/ComfyUI-H3-Multishot; do
  echo "--- $d ---"; ls -d $d 2>/dev/null && cat $d/extra_model_paths.yaml 2>/dev/null | head -12
  ls -la $d/models 2>/dev/null | head -6
done
echo "--- /root/ComfyUI/user/default/workflows ---"
ls /root/ComfyUI/user/default/workflows 2>/dev/null | head -20
echo "--- loras 全量（含子目录） ---"
find /root/autodl-tmp/ComfyUI-models/loras -type f | while read f; do echo "$(du -h "$f"|cut -f1)  $f"; done
