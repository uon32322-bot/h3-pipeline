#!/bin/bash
cat > /root/ComfyUI/input/v6/batch4.sh <<'SH'
#!/bin/bash
cd /root/ComfyUI/input
P=/root/miniconda3/bin/python
run(){ CASE=$1; shift
  echo "=== $CASE start $(date +%T)"
  $P test_r2v.py --prompt q_v_ref.txt --ref ref_model.png --ref ref_product.png \
     --length 56 --mp 0.4 --steps 8 --aspect "9:16 (Portrait Widescreen)" \
     --prefix "video/V6_$CASE" --seed 20260911 "$@" > v6/log_$CASE.txt 2>&1
  echo "=== $CASE end $(date +%T)"; grep -E "RESULT|SUBMIT FAILED" v6/log_$CASE.txt
}
run Y1 --sparse-budget 0.3
run Y2 --sparse-budget 0.3 --ref-video rv_a.mp4
run Y3 --sparse-budget 1.0
run Y4 --sparse-budget 1.0 --ref-video rv_a.mp4
run Y5 --sparse-off
run Y6 --sparse-off --ref-video rv_a.mp4
echo ALLDONE4
SH
chmod +x /root/ComfyUI/input/v6/batch4.sh
cd /root/ComfyUI/input && setsid nohup ./v6/batch4.sh > v6/batch4.log 2>&1 < /dev/null &
sleep 3
cat v6/batch4.log