#!/bin/bash
cd "$(dirname "$0")/.." || exit 1
PY=/Users/admin/.workbuddy/binaries/python/envs/default/bin/python
echo "### C1 棚拍跑步（studio） ###"
$PY -u judge_shot.py raw/C1_run.mp4 --name "C1_棚拍跑步" --prompt prompt/C1_run.txt \
    --first run/runA.png --bg studio --dry --out judge/C1_judge_L01.json
echo "### C2 户外散步（outdoor） ###"
$PY -u judge_shot.py raw/C2_walk_outdoor.mp4 --name "C2_户外散步" --prompt prompt/P2b_gait_outdoor.txt \
    --first outdoor/outA.png --bg outdoor --dry --out judge/C2_judge_L01.json
echo "### D 产品动态（macro + 声明切点） ###"
$PY -u judge_shot.py raw/D_product_dynamic.mp4 --name "D_产品动态卖点" --prompt prompt/D_product_dynamic.txt \
    --cuts "2.500,5.500,7.100,8.700" --bg macro --dry --out judge/D_judge_L01.json
echo "L01-DONE"
