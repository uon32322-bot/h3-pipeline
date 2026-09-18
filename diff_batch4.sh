cd /root/ComfyUI/output/video
/root/miniconda3/bin/python /root/ComfyUI/input/v6diff.py \
  V6_Y1_00001_.mp4 V6_Y2_00001_.mp4 \
  V6_Y3_00001_.mp4 V6_Y4_00001_.mp4 \
  V6_Y5_00001_.mp4 V6_Y6_00001_.mp4 \
  V6_W1b_00001_.mp4 V6_W2_00001_.mp4 \
  V6_W4_00001_.mp4 V6_W5_00001_.mp4 2>&1 | grep -v Warning | sed -n '1,40p'
echo '...'
/root/miniconda3/bin/python /root/ComfyUI/input/v6diff.py \
  V6_Y1_00001_.mp4 V6_Y2_00001_.mp4 \
  V6_Y3_00001_.mp4 V6_Y4_00001_.mp4 \
  V6_Y5_00001_.mp4 V6_Y6_00001_.mp4 \
  V6_W1b_00001_.mp4 V6_W2_00001_.mp4 \
  V6_W4_00001_.mp4 V6_W5_00001_.mp4 2>&1 | grep -v Warning | sed -n '40,90p'