echo "=== COMFYUI VERSION ==="
cat /root/ComfyUI/comfyui_version.py 2>/dev/null
cd /root/ComfyUI && git log -1 --format="commit=%H date=%ad subj=%s" 2>/dev/null
echo "--- running proc cwd/args ---"
ls -l /proc/80234/cwd 2>/dev/null; tr '\0' ' ' < /proc/80234/cmdline 2>/dev/null; echo

echo "=== models/ top level ==="
ls -la /root/ComfyUI/models/ 2>/dev/null

for d in diffusion_models checkpoints loras vae text_encoders clip_vision audio_encoders model_patches; do
  echo "--- models/$d ---"
  ls -la /root/ComfyUI/models/$d 2>/dev/null | head -40
done

echo "=== extra_model_paths.yaml ==="
cat /root/ComfyUI/extra_model_paths.yaml 2>/dev/null || echo "NONE"

echo "=== LARGE FILES (h3/minimax/wan related) ==="
find /root /root/autodl-tmp -maxdepth 7 \( -iname "*minimax*" -o -iname "*h3*" \) -type f -size +50M 2>/dev/null | grep -viE "\.git/|node_modules" | head -60 | while read f; do echo "$(du -h "$f" | cut -f1)  $f"; done

echo "=== DIR SIZES ==="
du -sh /root/ComfyUI/models 2>/dev/null
du -sh /root/autodl-tmp/ComfyUI-models 2>/dev/null
du -sh /root/autodl-tmp/daihuan_v2 2>/dev/null
du -sh /root/autodl-tmp/ComfyUI-H3-Multishot 2>/dev/null

echo "=== NET ==="
curl -sI --max-time 8 https://huggingface.co 2>&1 | head -2
curl -sI --max-time 8 https://www.modelscope.cn 2>&1 | head -2
