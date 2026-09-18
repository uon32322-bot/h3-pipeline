ROOT=/root/autodl-tmp/h3p
LORA=minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors
echo "=== 下载状态 ==="
ls -l $ROOT/models/loras/ 2>/dev/null | grep -v "^total"
tail -2 $ROOT/logs/dl_lora.log 2>/dev/null | cut -c1-200
echo
echo "=== 完整性校验 ==="
if [ -f $ROOT/models/loras/$LORA ]; then
  SZ=$(stat -c %s $ROOT/models/loras/$LORA)
  echo "size=$SZ  expect=1956193000"
  [ "$SZ" = "1956193000" ] && echo "SIZE OK" || echo "SIZE MISMATCH / 仍在下载"
  echo "--- sha256（前 16MB 抽样，用于记录版本指纹）---"
  head -c 16777216 $ROOT/models/loras/$LORA | sha256sum | cut -c1-64
  echo "--- safetensors 头部解析 ---"
  python3 - <<'PY'
import json, struct
p = "/root/autodl-tmp/h3p/models/loras/minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors"
with open(p, "rb") as f:
    n = struct.unpack("<Q", f.read(8))[0]
    if n > 10_000_000:
        print("header too large, likely incomplete:", n); raise SystemExit
    h = json.loads(f.read(n))
keys = [k for k in h if k != "__metadata__"]
print("tensors:", len(keys))
print("metadata:", h.get("__metadata__"))
print("sample keys:", keys[:5])
PY
fi
echo
echo "=== 隔离目录总览 ==="
find $ROOT -maxdepth 2 -not -path "*/comfy/*" | sort | head -30
du -sh $ROOT $ROOT/comfy $ROOT/models 2>/dev/null
echo
echo "=== 磁盘 ==="
df -h / /root/autodl-tmp | tail -2
