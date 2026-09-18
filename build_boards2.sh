#!/bin/bash
cd /root/ComfyUI/output/video
X=/root/ComfyUI/input
/root/miniconda3/bin/python $X/v6board.py /tmp/board_y.png \
  "$X/rv_a.mp4::REF-A" "$X/rv_b.mp4::REF-B" \
  V6_Y1_00001_.mp4::Y1-sb0.3-noVid \
  V6_Y2_00001_.mp4::Y2-sb0.3+Vid \
  V6_Y3_00001_.mp4::Y3-sb1.0-noVid \
  V6_Y4_00001_.mp4::Y4-sb1.0+Vid \
  V6_Y5_00001_.mp4::Y5-off-noVid \
  V6_Y6_00001_.mp4::Y6-off+Vid 2>&1 | grep -v Warning
echo
/root/miniconda3/bin/python $X/v6board.py /tmp/board_v.png \
  "$X/rv_a.mp4::REF-A-realUse" "$X/rv_b.mp4::REF-B-office" \
  V6_V0_00001_.mp4::V0-noVid \
  V6_V1_00001_.mp4::V1+rv_a \
  V6_V2_00001_.mp4::V2+rv_b \
  V6_W4_00001_.mp4::W4-rv_a-reverse \
  V6_W5_00001_.mp4::W5-rv_a-still \
  V6_X2_00001_.mp4::X2-noRefImg+rv_a \
  V6_W3_00001_.mp4::W3-newSeed 2>&1 | grep -v Warning
echo
ls -la /tmp/board_y.png /tmp/board_v.png