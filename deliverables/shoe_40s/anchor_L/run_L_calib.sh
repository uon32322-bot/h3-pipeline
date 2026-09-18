#!/bin/bash
# ============================================================
# L 口型同步标定（中文，零数据 -> 首次实测）
#   目的：验证「H3 audio guide 牵引的**中文**口型是否与输入语音同步」
#   A 轨 = 真实句④语音   B 轨 = 静音（只改音频这一个变量，其余全同）
#   判据：r(A) 显著 > r(B)（嘴动确由语音驱动）且 |滞后| <= 2 帧
# 设计要点：
#   * 首帧=尾帧同图 => 静止脸，排除人体位移干扰（A-03 误判的根因）
#   * 提示词要求「头肩不动、不做手势、不换姿势」=> 画面里唯一的运动就是嘴
#   * 段长 175 帧 = 7.292s，与 40s 片 G3 段【逐位一致】=> 标定即预演
#
# ⭐ 2026-09-18 改造：GPU 等待改为【接入全局仲裁器】，删掉自写轮询。
#   旧做法（本文件 v1）：自己 while 轮询 `nvidia-smi memory.free`，靠"抢空档"提交。
#   后果：同机 dh(digital-human) 的批量任务被我们的轮询硬抢显存，
#         单条吞吐从 509s 掉到 1044s（拖慢一倍）——这就是自写轮询的代价。
#   新做法：直接调 test_fl2v_official.py —— 它内部用 gpu_lock.acquire() 排队拿租约，
#          锁 / 排队 / 优先级 / 心跳续租 / 过期回收 全部由机器自带的
#          /opt/shared/gpu_lock.py 承担（同机 digital-human / h3-gateway /
#          heygem / tts 四个服务用的就是同一套）。本脚本不再判断显存、不再 sleep。
#   ⇒ 排队期间阻塞在这里，完全不碰 GPU；轮到时才提交。
#   ⚠️ 别再往本文件里加 nvidia-smi 轮询 —— 那是在跟仲裁器抢，属于已知错误做法。
# 用法：nohup ./run_L_calib.sh > /dev/null 2>&1 &
# ============================================================
set -u
cd /root/autodl-tmp/h3p/scripts || exit 1
PY=/root/autodl-tmp/h3p/venv/bin/python
I=/root/autodl-tmp/h3p/input
P=/root/autodl-tmp/h3p/prompt
LOG=/root/autodl-tmp/h3p/logs/L_calib.log

mkdir -p /root/autodl-tmp/h3p/logs
echo "===== L-CALIB START $(date '+%F %T') =====" >> "$LOG"
# 留痕一次仲裁器现状（只读快照，不是轮询）
$PY gpu_lease.py status >> "$LOG" 2>&1

run_track () {   # $1=tag  $2=wav  $3=seed
  echo "" >> "$LOG"
  echo "===== $1 轨 START $(date '+%F %T')  audio=$2 =====" >> "$LOG"
  # 内部流程：acquire 租约（阻塞排队）→ 提交 ComfyUI → 轮询到出片 → finally 释放
  $PY test_fl2v_official.py \
      $I/anchor_L_first.png $I/anchor_L_last.png $P/L_talk.txt \
      7.292 0.98 8 "H3L/CAL_$1" "$3" \
      --audio "$2@0" --aspect 9:16 --port 6011 \
      --lease-label "h3p:calib-$1" --lease-timeout 7200 >> "$LOG" 2>&1
  echo "===== $1 轨 END rc=$? $(date '+%F %T') =====" >> "$LOG"
}

run_track A s4_trust_full.wav  2026091831     # 真实句④语音
run_track B silence_control.wav 2026091831    # 静音（同 seed！只换音频 => 单变量）

echo "" >> "$LOG"
echo "L-CALIB-ALL-DONE $(date '+%F %T')" >> "$LOG"
$PY gpu_lease.py status >> "$LOG" 2>&1
ls -lt /root/autodl-tmp/h3p/output/H3L/ >> "$LOG" 2>&1
