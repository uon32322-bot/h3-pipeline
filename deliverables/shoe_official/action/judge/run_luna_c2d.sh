#!/bin/bash
cd "$(dirname "$0")/.." || exit 1
PY=/Users/admin/.workbuddy/binaries/python/envs/default/bin/python
echo "############ C2 户外散步 ############"
$PY -u judge_shot.py raw/C2_walk_outdoor.mp4 --name "C2_户外散步_luna" --prompt prompt/P2b_gait_outdoor.txt \
    --first outdoor/outA.png --bg outdoor --frames 6 --workers 4 --votes 3 --out judge/C2_judge_luna.json
echo "############ D 产品动态卖点 ############"
$PY -u judge_shot.py raw/D_product_dynamic.mp4 --name "D_产品动态卖点_luna" --prompt prompt/D_product_dynamic.txt \
    --first ../anchors/anchor_1_hero.png --cuts "2.500,5.500,7.100,8.700" --bg macro \
    --frames 6 --workers 4 --votes 3 --out judge/D_judge_luna.json
echo "LUNA-C2D-DONE"
