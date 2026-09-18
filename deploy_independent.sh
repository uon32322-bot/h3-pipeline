#!/bin/bash
# ============================================================================
# H3 带货视频流水线 · 完全独立部署（本项目自建文件夹 + 模型独立，不与任何项目共享）
# ----------------------------------------------------------------------------
# 用法：  deploy_independent.sh {auto|copy|download}
#   auto     （默认）先校验共享库文件是否与官方 sha256 一致：一致则同机复制，
#            不一致则从官方源下载。最省时间且来源可证。
#   copy     强制同机复制（要求共享库文件 sha256 = 官方值，否则拒绝）
#   download 强制从官方源（hf-mirror）全新下载，最彻底
#
# 原则：
#   1. 项目根 = /root/autodl-tmp/h3p/ ，模型 = 该目录下的真文件（不软链）
#   2. 每件权重下载/复制后必须通过官方 sha256 校验，否则视为失败
#   3. 幂等：已存在且校验通过的文件直接跳过
#   4. 只读共享库、只写本项目目录；不修改任何其他项目的文件
# ============================================================================
set -u

ROOT=/root/autodl-tmp/h3p
M=$ROOT/models
SHARED=/root/autodl-tmp/ComfyUI-models
MODE="${1:-auto}"
HF=https://hf-mirror.com
LOG=$ROOT/logs/deploy_$(date +%Y%m%d_%H%M%S).log
MANIFEST=$ROOT/models_manifest.txt

mkdir -p $M/{diffusion_models,text_encoders,vae,loras,embeddings} $ROOT/logs
exec > >(tee -a "$LOG") 2>&1

say(){ printf '\n\033[1;36m%s\033[0m\n' "$*"; }
ok(){  printf '  \033[1;32m✅ %s\033[0m\n' "$*"; }
bad(){ printf '  \033[1;31m❌ %s\033[0m\n' "$*"; }
inf(){ printf '  · %s\n' "$*"; }

# ---------------------------------------------------------------------------
# 部件清单：  相对路径 | 官方 sha256 | 来源仓库 | 本地共享库中的同名文件（用于 copy 模式）
# 哈希基准来自 hf-mirror API 的 lfs.sha256（官方权威值）
# ---------------------------------------------------------------------------
FILES=(
"diffusion_models/minimax_h3_fl2va_pruned_int8_convrot.safetensors|e889202c41dafb67b10d67b97f0d8541508036a6090af23425a5c2615d03c47a|Comfy-Org/MiniMax-H3|$SHARED/diffusion_models/minimax_h3_fl2va_pruned_int8_convrot.safetensors"
"text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors|35a88d51044231fe332301d7a62aa81e3f2cba62febeb446e2c1e3e0ef76f2c6|Comfy-Org/MiniMax-H3|$SHARED/text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"
"vae/minimax_h3_video_vae_fp16.safetensors|7c1f131492e7eddacaac9069a61b81bdd39de5cc96561e677c5eab1cdce5e522|Comfy-Org/MiniMax-H3|$SHARED/vae/minimax_h3_video_vae_fp16.safetensors"
"vae/minimax_h3_audio_vae_fp32.safetensors|8e505d95dd1561d47abd43d4238fd40d9bb1ae9e147ed0a4cba778d76ae4db48|Comfy-Org/MiniMax-H3|$SHARED/vae/minimax_h3_audio_vae_fp32.safetensors"
"loras/minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors|2339acdf19bfe123f46b971ea35d367a84adb85de43627e1eceafa5a5b2b111e|Comfy-Org/MiniMax-H3|$SHARED/loras/minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors"
)

sha_of(){ [ -f "$1" ] && sha256sum "$1" 2>/dev/null | cut -d' ' -f1 || echo MISSING; }

total_need=0
for e in "${FILES[@]}"; do
  p="${e%%|*}"; rest="${e#*|}"; src="${rest#*|}"
  # 仅统计尚未就绪的体积
  want="${e#*|}"; want="${want%%|*}"
  cur=$(sha_of "$M/$p")
  [ "$cur" = "$want" ] && continue
  s=$([ -f "$SHARED/$p" ] && stat -c %s "$SHARED/$p" 2>/dev/null || echo 0)
  total_need=$((total_need + s))
done

say "═══ H3 独立部署开始 ═══  模式=$MODE"
inf "项目根      : $ROOT"
inf "模型目录    : $M  （真文件，独立自持）"
inf "共享库(只读): $SHARED"
avail=$(df -B1 $ROOT | tail -1 | awk '{print $4}')
inf "数据盘可用  : $((avail/1024/1024/1024)) GiB ；本次尚需约 $((total_need/1024/1024/1024)) GiB"
if [ "$avail" -lt $((total_need + 5368709120)) ]; then
  bad "空间不足（需预留 5 GiB 余量），中止"
  exit 1
fi
ok "空间充足"

# ---------------------------------------------------------------------------
say "─── 逐个部件处理 ───"
declare -a RESULTS=()
fail=0
for e in "${FILES[@]}"; do
  rel="${e%%|*}"; rest="${e#*|}"; want="${rest%%|*}"; rest2="${rest#*|}"
  repo="${rest2%%|*}"; shared_src="${rest2#*|}"
  dst="$M/$rel"
  mkdir -p "$(dirname "$dst")"

  cur=$(sha_of "$dst")
  if [ "$cur" = "$want" ]; then
    ok "$rel  已就绪（sha256 校验通过）"
    RESULTS+=("$(printf '%-62s %s %s' "$rel" "SKIP-OK" "$want")")
    continue
  fi

  printf '\n\033[1;33m[>] %s\033[0m\n' "$rel"
  method=""

  # ---- 决定获取方式 ----
  if [ "$MODE" = "download" ]; then
    method="download"
  else
    if [ -f "$shared_src" ]; then
      inf "校验共享库同源文件（只读，不改动它）…"
      sh=$(sha_of "$shared_src")
      if [ "$sh" = "$want" ]; then
        ok "共享库文件 sha256 = 官方值 → 可安全同机复制"
        method="copy"
      else
        bad "共享库文件 sha256 不符（实际 ${sh:0:16}…）→ 改走官方下载"
        method="download"
      fi
      [ "$MODE" = "copy" ] && [ "$method" != "copy" ] && { bad "copy 模式要求源哈希一致，已中止该件"; fail=1; continue; }
    else
      inf "共享库无此文件 → 走官方下载"
      method="download"
    fi
  fi

  # ---- 执行 ----
  if [ "$method" = "copy" ]; then
    t0=$(date +%s)
    if ! cp "$shared_src" "$dst.part"; then bad "复制失败"; fail=1; continue; fi
    mv -f "$dst.part" "$dst"
    t1=$(date +%s)
    inf "同机复制完成，耗时 $((t1-t0))s"
  else
    url="$HF/$repo/resolve/main/$rel"
    inf "下载源: $url"
    t0=$(date +%s)
    if ! curl -fL --retry 3 --retry-delay 5 -C - -o "$dst.part" "$url"; then
      bad "下载失败（保留 .part 以便断点续传重试）"; fail=1; continue
    fi
    mv -f "$dst.part" "$dst"
    t1=$(date +%s)
    inf "下载完成，耗时 $((t1-t0))s"
  fi

  # ---- 校验 ----
  got=$(sha_of "$dst")
  if [ "$got" = "$want" ]; then
    sz=$(stat -c %s "$dst")
    ok "sha256 校验通过  ($sz bytes)  ${got:0:16}…"
    RESULTS+=("$(printf '%-62s %s %s' "$rel" "${method^^}" "$got")")
  else
    bad "sha256 校验失败！期望 ${want:0:16}… 实际 ${got:0:16}…  → 删除可疑文件"
    rm -f "$dst"
    RESULTS+=("$(printf '%-62s %s %s' "$rel" "FAILED" "$got")")
    fail=1
  fi
done

# ---------------------------------------------------------------------------
say "─── 独立性核验（不允许残留软链）───"
links=$(find $M -type l 2>/dev/null | wc -l)
if [ "$links" -eq 0 ]; then ok "模型目录无任何软链，完全自持"; else bad "仍有 $links 个软链："; find $M -type l -exec ls -la {} \;; fi

say "─── 生成资产清单 manifest ───"
{
  echo "# H3 项目独立模型清单（本项目自持，不与其他项目共享）"
  echo "# 生成时间: $(date '+%Y-%m-%d %H:%M:%S %Z')"
  echo "# 项目根  : $ROOT"
  echo "# 哈希基准: hf-mirror API lfs.sha256（官方权威值）"
  echo "#"
  printf '%-62s %-10s %s\n' "FILE" "METHOD" "SHA256"
  for r in "${RESULTS[@]}"; do echo "$r"; done
  echo
  echo "# --- 目录实况 ---"
  for d in diffusion_models text_encoders vae loras embeddings; do
    echo "[$d]"
    ls -l $M/$d 2>/dev/null | tail -n +2 | awk '{printf "  %10s  %s\n", $5, $9}'
  done
} > $MANIFEST
ok "已写入 $MANIFEST"
cat $MANIFEST | head -20

say "═══ 结果 ═══"
inf "占用: $(du -sh $ROOT | cut -f1)   数据盘剩余: $(df -h $ROOT | tail -1 | awk '{print $4}')"
if [ "$fail" -eq 0 ]; then ok "全部部件就绪，项目模型已完全独立"; else bad "有部件未就绪，请查看上方日志"; fi
inf "日志: $LOG"
exit $fail
