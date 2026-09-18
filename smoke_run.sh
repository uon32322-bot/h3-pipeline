cd /root/ComfyUI/input
nohup /root/miniconda3/bin/python test_r2v.py \
  --prompt q_v_ref.txt --ref ref_model.png --ref ref_product.png \
  --length 24 --mp 0.4 --steps 4 --aspect "9:16 (Portrait Widescreen)" \
  --prefix "video/V6_SMOKE" --seed 20260911 \
  --ref-video rv_a.mp4 --sparse-off > /root/ComfyUI/input/v6/log_SMOKE.txt 2>&1
echo done $?
grep -E "RESULT|SUBMIT|Error" /root/ComfyUI/input/v6/log_SMOKE.txt