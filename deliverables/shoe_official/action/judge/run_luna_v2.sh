#!/bin/bash
# 判官团重判（v2）—— 判据升级到 18 项后的统一复检
#   新增：R-07（画面文字落位是否合规）；R-04 收窄为纯逐字一致
#   修正：--bg macro 时 L2 P-05 强制 na（与 L1-11 同源）
# 全部走 tt-5.6-luna（白名单唯一允许值），三级判官全部出 GPU
cd "$(dirname "$0")/.." || exit 1
PY=/Users/admin/.workbuddy/binaries/python/envs/default/bin/python
echo "############ C1 棚拍跑步（声明已修，v3 锚点） ############"
$PY -u judge_shot.py raw/C1v3_run.mp4 --name "C1v3_跑步_luna_v2" --prompt prompt/C1_run_v2.txt \
    --first run/runA3.png --bg studio --frames 6 --workers 4 --votes 3 \
    --out judge/C1v3_judge_luna_v2.json
echo "############ C2 户外散步 ############"
$PY -u judge_shot.py raw/C2_walk_outdoor.mp4 --name "C2_户外散步_luna_v2" --prompt prompt/P2b_gait_outdoor.txt \
    --first outdoor/outA.png --bg outdoor --frames 6 --workers 4 --votes 3 \
    --out judge/C2_judge_luna_v2.json
echo "############ D 产品动态卖点（macro 微距） ############"
$PY -u judge_shot.py raw/D_product_dynamic.mp4 --name "D_产品动态卖点_luna_v2" --prompt prompt/D_product_dynamic.txt \
    --first ../anchors/anchor_1_hero.png --cuts "2.500,5.500,7.100,8.700" --bg macro \
    --frames 6 --workers 4 --votes 3 --out judge/D_judge_luna_v2.json
echo "LUNA-V2-DONE"
