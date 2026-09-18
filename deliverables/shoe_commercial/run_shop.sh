#!/bin/bash
# H3 带货片三段串行生成（C1 缓震 / C2 透气 / C3 上脚收尾）
# 参数与已验证的 MODEL_REAL 完全一致，仅换首尾帧与提示词 + 新 seed。
cd /root/autodl-tmp/h3p/scripts || exit 1
PY=/root/autodl-tmp/h3p/venv/bin/python
run () {
  echo "===== START $1  $(date '+%F %T') ====="
  $PY test_fl2v_official.py \
      /root/autodl-tmp/h3p/input/${1}_first.png \
      /root/autodl-tmp/h3p/input/${1}_last.png \
      /root/autodl-tmp/h3p/prompt/${2}.txt \
      5.0 0.98 8 H3SHOP/${3} ${4} --aspect 9:16 --port 6011
  echo "===== END $1  rc=$?  $(date '+%F %T') ====="
}
run c1 c1 C1 2026091801
run c2 c2 C2 2026091802
run c3 c3 C3 2026091803
echo "ALL-DONE $(date '+%F %T')"
