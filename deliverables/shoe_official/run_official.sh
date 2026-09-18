#!/bin/bash
# 官方 SOP 版带货片：A 产品主片（10s / 243 帧 / 3 锚定照）+ B 真人穿着片（5s / 124 帧）
cd /root/autodl-tmp/h3p/scripts || exit 1
PY=/root/autodl-tmp/h3p/venv/bin/python
I=/root/autodl-tmp/h3p/input
P=/root/autodl-tmp/h3p/prompt

echo "===== START A 产品主片  $(date '+%F %T') ====="
$PY test_fl2v_official.py \
    $I/anchor_1_hero.png $I/anchor_3_endcopy.png $P/A_product.txt \
    10.0 0.98 8 H3OFF/A 2026091821 \
    --mid "$I/anchor_2_material.png@5.5" --aspect 9:16 --port 6011
echo "===== END A  rc=$?  $(date '+%F %T') ====="

echo "===== START B 真人穿着片  $(date '+%F %T') ====="
$PY test_fl2v_official.py \
    $I/c3_first.png $I/c3_last.png $P/B_wearer.txt \
    5.0 0.98 8 H3OFF/B 2026091822 --aspect 9:16 --port 6011
echo "===== END B  rc=$?  $(date '+%F %T') ====="
echo "ALL-DONE $(date '+%F %T')"
