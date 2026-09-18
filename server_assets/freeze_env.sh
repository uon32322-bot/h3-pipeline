#!/usr/bin/env bash
# ============================================================================
# H3 带货视频流水线 · 环境冻结（Phase 0.5）
#
#  用法：  bash freeze_env.sh
#  产出：  $ROOT/env_manifest.txt
#
#  为什么必须冻结：种子资产库依赖「同 seed + 同 prompt = 逐位相同输出」。
#  一旦内核版本 / 加速档 / 节点包 commit / 启动参数任一变动，该前提失效
#  ⇒ 资产库全部作废。故必须在建库【之前】把这四项钉死落文件。
#
#  依据：部署指南 §2.1 两条顺序铁律 ①「先冻结环境，再建资产库」
# ============================================================================
set -u
ROOT="${H3P_ROOT:-/root/autodl-tmp/h3p}"
COMFY="$ROOT/comfy/ComfyUI"
OUT="$ROOT/env_manifest.txt"
PY="$ROOT/venv/bin/python"

{
echo "# ============================================================================"
echo "# H3 带货视频流水线 · 环境冻结清单 (env_manifest)"
echo "# 冻结时间：$(date '+%F %T %Z')"
echo "# 冻结依据：部署指南 §2.1 铁律①  换库前不得改动本文件任一字段"
echo "# ============================================================================"
echo
echo "## 1. 项目与环境"
echo "PROJECT_ROOT   = $ROOT"
echo "COMFY_DIR      = $COMFY"
echo "VENV_PYTHON    = $PY"
echo "VENV_VERSION   = $($PY -V 2>&1)"
echo
echo "## 2. 内核版本（ComfyUI）"
if [ -f "$COMFY/comfyui_version.py" ]; then
  echo "COMFYUI_VERSION = $(grep -oP '__version__\s*=\s*"\K[^"]+' "$COMFY/comfyui_version.py" 2>/dev/null || echo '?')"
else
  echo "COMFYUI_VERSION = (未就位)"
fi
if [ -d "$COMFY/.git" ]; then
  echo "COMFY_COMMIT    = $(cd "$COMFY" && git rev-parse HEAD 2>/dev/null)"
  echo "COMFY_COMMIT_SHORT = $(cd "$COMFY" && git rev-parse --short HEAD 2>/dev/null)"
  echo "COMFY_COMMIT_DATE  = $(cd "$COMFY" && git log -1 --format=%cd --date=iso 2>/dev/null)"
  echo "COMFY_BRANCH    = $(cd "$COMFY" && git rev-parse --abbrev-ref HEAD 2>/dev/null)"
  echo "COMFY_DIRTY     = $(cd "$COMFY" && git status --porcelain 2>/dev/null | wc -l | tr -d ' ') 处改动（0 = 纯净）"
fi
echo "参照基线（共享实例）= 0.36.0 / d39cdfd"
echo
echo "## 3. 运行库版本（决定 kernel 与算子行为）"
$PY - <<'PYEOF' 2>/dev/null
import importlib
mods = ["torch","triton","torchvision","torchaudio","transformers","safetensors",
        "einops","numpy","scipy","tokenizers","sentencepiece","accelerate","comfy_kitchen",
        "sqlalchemy","alembic","pydantic","av","psutil"]
for m in mods:
    try:
        mod = importlib.import_module(m)
        print("%-18s %s" % (m, getattr(mod, "__version__", "?")))
    except Exception:
        print("%-18s MISSING" % m)
try:
    import torch
    print("torch.cuda      %s" % torch.version.cuda)
    print("cudnn           %s" % (torch.backends.cudnn.version() if torch.backends.cudnn.is_available() else "NA"))
    if torch.cuda.is_available():
        print("gpu             %s" % torch.cuda.get_device_name(0))
        print("gpu_arch(SM)    %s" % (torch.cuda.get_device_capability(0),))
except Exception as e:
    print("torch 探测失败:", e)
PYEOF
echo
echo "## 4. 驱动与硬件"
nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader 2>/dev/null | sed 's/^/GPU  /'
nvidia-smi 2>/dev/null | grep -oP "CUDA Version:\s*\K[0-9.]+" | sed 's/^/DRIVER_CUDA     /'
echo "HOST_KERNEL     = $(uname -r)"
echo "OS              = $(. /etc/os-release 2>/dev/null; echo "$PRETTY_NAME")"
echo "CPU_CORES       = $(nproc)"
echo "MEM_TOTAL       = $(free -g 2>/dev/null | awk 'NR==2{print $2" GiB"}')"
echo
echo "## 5. 启动参数（加速档 = 由此四项共同定义）"
echo "启动器          = $ROOT/scripts/start_comfyui.sh"
echo "端口            = ${H3P_PORT:-6011}"
echo "显存模式        = ${1:-auto}"
echo "--base-directory    $ROOT/comfy"
echo "--input-directory   $ROOT/input"
echo "--output-directory  $ROOT/output"
echo "--temp-directory    $ROOT/temp"
echo "--extra-model-paths-config $ROOT/comfy/extra_model_paths.yaml"
echo "--preview-method    none"
echo "加速档位        = T0（官方纯净档：无自定义加速节点）"
echo "  · kitchen INT8 注意力 : 由 comfy_kitchen 提供（零安装收益）"
echo "  · SageAttention       : 未安装（故不启用 --use-sage-attention）"
echo "  · FastH3 4 步         : 未启用（T2 激进档，仅草稿）"
echo "  · 步数                : 8 步 Turbo LoRA（官方）"
echo "  · 分辨率              : 0.98 MP = 1344x768（禁用 1.0MP）"
echo
echo "## 6. 权重清单与 sha256（模型独立自持）"
if [ -f "$ROOT/models_manifest.txt" ]; then
  cat "$ROOT/models_manifest.txt" | sed 's/^/  /'
else
  echo "  (models_manifest.txt 尚未生成 —— 待部署脚本产出)"
fi
echo "--- 实际落盘文件与哈希 ---"
find "$ROOT/models" -name "*.safetensors" -type f 2>/dev/null | sort | while read -r f; do
  printf "  %-72s %s\n" "${f#$ROOT/models/}" "$(sha256sum "$f" 2>/dev/null | cut -c1-16)…"
done
echo
echo "## 7. 存储合规"
df -h / /root/autodl-tmp 2>/dev/null | sed 's/^/  /'
echo
echo "# ============================================================================"
echo "# 变更纪律：本文件任一字段变更 ⇒ 种子资产库作废，必须重建"
echo "# ============================================================================"
} | tee "$OUT"

echo
echo "✅ 已写入 $OUT"
