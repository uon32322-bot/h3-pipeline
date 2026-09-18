set -u
ROOT=/root/autodl-tmp/h3p
SHARED=/root/autodl-tmp/ComfyUI-models
LORA=minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors
URL=https://hf-mirror.com/lightx2v/Minimax-h3-Turbo/resolve/main/$LORA

echo "=== 0. 建立隔离目录骨架 ==="
mkdir -p $ROOT/models/diffusion_models $ROOT/models/text_encoders $ROOT/models/vae $ROOT/models/loras $ROOT/models/embeddings
mkdir -p $ROOT/output $ROOT/input $ROOT/temp $ROOT/logs
echo "OK: $ROOT"

echo
echo "=== 1. 四件已核实权重 -> 软链复用（只读，省 41G） ==="
ln -sfn $SHARED/diffusion_models/minimax_h3_fl2va_pruned_int8_convrot.safetensors $ROOT/models/diffusion_models/
ln -sfn $SHARED/text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors      $ROOT/models/text_encoders/
ln -sfn $SHARED/vae/minimax_h3_video_vae_fp16.safetensors                       $ROOT/models/vae/
ln -sfn $SHARED/vae/minimax_h3_audio_vae_fp32.safetensors                       $ROOT/models/vae/
ls -l $ROOT/models/diffusion_models/ $ROOT/models/text_encoders/ $ROOT/models/vae/ | grep -E "safetensors|->"

echo
echo "=== 2. 官方 8 步 Turbo LoRA -> 真文件下载（快照到我们自己的 loras/） ==="
if [ -s $ROOT/models/loras/$LORA ] && [ "$(stat -c %s $ROOT/models/loras/$LORA)" = "1956193000" ]; then
  echo "已存在且大小正确，跳过下载"
else
  nohup aria2c -x 4 -s 4 -k 4M --file-allocation=none --allow-overwrite=true \
    -d $ROOT/models/loras -o $LORA "$URL" > $ROOT/logs/dl_lora.log 2>&1 &
  echo "download started pid=$!"
fi

echo
echo "=== 3. ComfyUI 代码副本（纯官方节点，custom_nodes 留空） ==="
if [ -f $ROOT/comfy/main.py ]; then
  echo "代码副本已存在，跳过"
else
  if which rsync >/dev/null 2>&1; then
    rsync -a \
      --exclude 'models' --exclude 'custom_nodes' --exclude 'output' --exclude 'temp' \
      --exclude 'input' --exclude '__pycache__' --exclude '.git' \
      /root/ComfyUI/ $ROOT/comfy/
    echo "rsync done"
  else
    echo "NO rsync"
  fi
  mkdir -p $ROOT/comfy/custom_nodes
fi
du -sh $ROOT/comfy 2>/dev/null

echo
echo "=== 4. 现状 ==="
du -sh $ROOT 2>/dev/null
df -h /root/autodl-tmp | tail -1
sleep 6
echo "--- 下载进度 ---"
tail -3 $ROOT/logs/dl_lora.log 2>/dev/null
ls -l $ROOT/models/loras/ 2>/dev/null
