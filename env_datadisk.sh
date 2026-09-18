#!/usr/bin/env bash
# ============================================================================
# H3 带货视频流水线 · 数据盘环境预置（v2.7）
#
#  用法：  source /root/autodl-tmp/h3p/env_datadisk.sh
#  何时：  每次开实例的【第一步】就 source（在装包 / 下载 / 起 ComfyUI 之前）
#
#  目的：  让虚拟环境、缓存、临时文件、模型、输出全部落在【数据盘】，
#          系统盘（/ 与 /root）不留任何本项目产物。
#
#  依据：  部署指南 §1.8 存储布局硬规范 —— 辉哥要求「所有部署全部在服务器的
#          数据盘上，不要将文件和数据部署在服务器系统盘」。
#
#  为什么：① 系统盘满 = 实例异常（v2.6 现场发生过 / 跑到 100%）
#          ② 系统盘内容不持久（不保存镜像就丢）
#          ③ 数据盘可扩、系统盘难扩
# ============================================================================

export H3P_ROOT="${H3P_ROOT:-/root/autodl-tmp/h3p}"

# ---------- 1. 目录骨架（全部在数据盘） ----------
mkdir -p "$H3P_ROOT"/{models,comfy,input,output,temp,logs,tmp,venv,report,img,video,prompt}
mkdir -p "$H3P_ROOT"/cache/{pip,hf,hf/hub,hf/hub/transformers,torch,triton,xdg,matplotlib,numba,audio}
mkdir -p "$H3P_ROOT"/cache/hf/hub/{models,datasets}

# ---------- 2. 缓存与临时目录重定向 ----------
export PIP_CACHE_DIR="$H3P_ROOT/cache/pip"
export HF_HOME="$H3P_ROOT/cache/hf"
export HUGGINGFACE_HUB_CACHE="$H3P_ROOT/cache/hf/hub"
export TRANSFORMERS_CACHE="$H3P_ROOT/cache/hf/hub/transformers"
export TORCH_HOME="$H3P_ROOT/cache/torch"
export TORCH_EXTENSIONS_DIR="$H3P_ROOT/cache/torch/extensions"
export TRITON_CACHE_DIR="$H3P_ROOT/cache/triton"
export XDG_CACHE_HOME="$H3P_ROOT/cache/xdg"
export MPLCONFIGDIR="$H3P_ROOT/cache/matplotlib"
export NUMBA_CACHE_DIR="$H3P_ROOT/cache/numba"
export TMPDIR="$H3P_ROOT/tmp"
export TEMP="$TMPDIR"
export TMP="$TMPDIR"

# ---------- 3. 模型下载镜像（下官方 8 步 LoRA 用） ----------
export HF_ENDPOINT="${HF_ENDPOINT:-https://hf-mirror.com}"

# ---------- 4. 虚拟环境（不污染系统 python） ----------
if [ ! -d "$H3P_ROOT/venv/bin" ]; then
  echo "[env] 创建 venv：$H3P_ROOT/venv"
  python3 -m venv "$H3P_ROOT/venv" || echo "[env] ⚠️ venv 创建失败，请检查 python3 -m venv 是否可用"
fi
export VIRTUAL_ENV_DISABLE_PROMPT=1

# ---------- 5. 汇总 ----------
cat <<EOF
[env] ✅ 数据盘环境已就绪
      H3P_ROOT              = $H3P_ROOT
      PIP_CACHE_DIR         = $PIP_CACHE_DIR
      HF_HOME               = $HF_HOME
      HUGGINGFACE_HUB_CACHE = $HUGGINGFACE_HUB_CACHE
      TORCH_EXTENSIONS_DIR  = $TORCH_EXTENSIONS_DIR
      TRITON_CACHE_DIR      = $TRITON_CACHE_DIR
      TMPDIR                = $TMPDIR
      HF_ENDPOINT           = $HF_ENDPOINT

      下一步（可选）：
        source $H3P_ROOT/venv/bin/activate      # 需要独立包环境时
        bash  check_datadisk.sh                 # 自检：系统盘是否干净

      起 ComfyUI 时请显式指定目录（否则默认落系统盘）：
        --base-directory   $H3P_ROOT/comfy
        --input-directory  $H3P_ROOT/input
        --output-directory $H3P_ROOT/output
        --temp-directory   $H3P_ROOT/temp
EOF
