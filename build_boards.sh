cd /root/ComfyUI/output/video
X=/root/ComfyUI/input
# 1) 看板：参考视频 vs Y1/Y2/Y3/Y4/Y5/Y6
/root/miniconda3/bin/python $X/v6board.py /tmp/board_y.png \
  "$X/rv_a.mp4:REF-A" "$X/rv_b.mp4:REF-B" \
  V6_Y1_00001_.mp4:Y1 sb=0.3 noVid \
  V6_Y2_00001_.mp4:Y2 sb=0.3 +Vid \
  V6_Y3_00001_.mp4:Y3 sb=1.0 noVid \
  V6_Y4_00001_.mp4:Y4 sb=1.0 +Vid \
  V6_Y5_00001_.mp4:Y5 off noVid \
  V6_Y6_00001_.mp4:Y6 off +Vid 2>&1 | grep -v Warning
echo
# 2) 同提示词下不同参考视频的输出（之前批次）
/root/miniconda3/bin/python $X/v6board.py /tmp/board_v.png \
  "$X/rv_a.mp4:REF-A(真人用产品)" "$X/rv_b.mp4:REF-B(真人办公室)" \
  V6_V0_00001_.mp4:V0 无视频 \
  V6_V1_00001_.mp4:V1 +rv_a \
  V6_V2_00001_.mp4:V2 +rv_b \
  V6_W4_00001_.mp4:W4 rv_a倒放 \
  V6_W5_00001_.mp4:W5 rv_a静帧 \
  V6_X2_00001_.mp4:X2 零参考图+rv_a \
  V6_W3_00001_.mp4:W3 换seed 2>&1 | grep -v Warning
echo
ls -la /tmp/board_y.png /tmp/board_v.png