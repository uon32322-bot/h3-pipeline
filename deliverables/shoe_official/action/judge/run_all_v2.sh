#!/bin/bash
cd "$(dirname "$0")/.." || exit 1
PY=/Users/admin/.workbuddy/binaries/python/envs/default/bin/python
echo "############ C1 单镜（跑姿，无双锚点切镜）############"
$PY -u judge_shot.py raw/C1_run.mp4 --name "C1_棚拍跑步" --prompt prompt/C1_run.txt \
    --first run/runA.png --frames 6 --workers 4 --votes 3 --out judge/C1_judge.json
echo "############ C2 单镜（户外散步）############"
$PY -u judge_shot.py raw/C2_walk_outdoor.mp4 --name "C2_户外散步" --prompt prompt/P2b_gait_outdoor.txt \
    --first outdoor/outA.png --frames 6 --workers 4 --votes 3 --out judge/C2_judge.json
echo "############ D 六分镜（声明的切点 2.5/5.5/7.1/8.7；微距镜关闭出框检查）############"
$PY -u judge_shot.py raw/D_product_dynamic.mp4 --name "D_产品动态卖点" --prompt prompt/D_product_dynamic.txt \
    --cuts "2.500,5.500,7.100,8.700" --macro \
    --frames 6 --workers 4 --votes 3 --out judge/D_judge.json
echo "ALL-JUDGE-V2-DONE"
