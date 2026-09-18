#!/bin/bash
# ============================================================================
# run_final.sh —— 运动鞋带货片的【最终定稿】生成命令（可逐字复现）
#   实地路径：服务器 /root/autodl-tmp/h3p/
#   写这份脚本的目的：run_official.sh 里跑的是 A 的**初版**（镜 4 冲过头 + 中段文案糊）。
#   下面两条才是最终交付用的命令，留档以便同 seed 复现与后续 A/B。
# ============================================================================
cd /root/autodl-tmp/h3p/scripts || exit 1
PY=/root/autodl-tmp/h3p/venv/bin/python
I=/root/autodl-tmp/h3p/input
P=/root/autodl-tmp/h3p/prompt

# --- A 产品主片 10.0s / 243 帧 / 768x1344 / 8 步 / 3 张独立锚定照 -------------
# 首帧=anchor_1_hero  中段锚点=anchor_2_material@5.5s(AddGuide)  尾帧=anchor_3_endcopy
# 最终稿 prompt = A3_product_final.txt（镜 3 文案挪到鞋体上方留白带；镜 4 改 truck 保持距离）
echo "===== START A 产品主片  $(date '+%F %T') ====="
$PY test_fl2v_official.py \
    $I/anchor_1_hero.png $I/anchor_3_endcopy.png $P/A3_product_final.txt \
    10.0 0.98 8 H3OFF/A3 2026091821 \
    --mid "$I/anchor_2_material.png@5.5" --aspect 9:16 --port 6011
echo "===== END A  rc=$?  $(date '+%F %T') ====="

# --- B 真人穿着片 5.0s / 124 帧 / 无画面文案 -------------------------------
echo "===== START B 真人穿着片  $(date '+%F %T') ====="
$PY test_fl2v_official.py \
    $I/c3_first.png $I/c3_last.png $P/B_wearer.txt \
    5.0 0.98 8 H3OFF/B 2026091822 --aspect 9:16 --port 6011
echo "===== END B  rc=$?  $(date '+%F %T') ====="
echo "ALL-DONE $(date '+%F %T')"
