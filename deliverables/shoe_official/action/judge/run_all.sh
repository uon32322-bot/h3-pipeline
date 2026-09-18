#!/bin/bash
cd "$(dirname "$0")/.." || exit 1
PY=/Users/admin/.workbuddy/binaries/python/envs/default/bin/python
$PY -u judge_shot.py raw/C1_run.mp4 --name "C1_棚拍跑步" --prompt prompt/C1_run.txt \
    --first run/runA.png --frames 6 --workers 3 --out judge/C1_judge.json
$PY -u judge_shot.py raw/C2_walk_outdoor.mp4 --name "C2_户外散步_重抽" --prompt prompt/P2b_gait_outdoor.txt \
    --first outdoor/outA.png --frames 6 --workers 3 --out judge/C2_judge.json
$PY -u judge_shot.py raw/D_product_dynamic.mp4 --name "D_产品动态卖点" --prompt prompt/D_product_dynamic.txt \
    --first ../anchors/anchor_1_hero.png --cuts "2.5,5.5,7.1,8.7" --frames 6 --workers 3 \
    --out judge/D_judge.json
echo "ALL-JUDGE-DONE"
