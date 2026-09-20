#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""全链真跑 S0→S9 —— **SageAttention 档**（2026-09-20）

与 run_e2e.py 的区别：
  · 输出到独立目录 out/e2e_sage（不污染历史 e2e_real）
  · 显式记录 sage 是否生效、每段 H3 耗时、判官（含新接的 L2）结果
  · 复用与 run_e2e.py 完全相同的 job 参数（唯一变量 = ComfyUI 开 sage）

前置：ComfyUI 6011 必须已带 --use-sage-attention 启动（start_comfyui.sh 默认开），
      日志需含 "Using sage attention"（本脚本会自检）。
"""
import sys, json, time, subprocess as sp
from pathlib import Path

sys.path.insert(0, '/root/autodl-tmp/h3p/scripts')
import orchestrator as O

OUT = Path('/root/autodl-tmp/h3p/out/e2e_sage')
OUT.mkdir(parents=True, exist_ok=True)

# ---------- 前置自检：sage 真生效（不看日志不算生效）----------
def sage_active():
    logs = sorted(Path('/root/autodl-tmp/h3p/logs').glob('comfyui_*.log'),
                  key=lambda p: p.stat().st_mtime, reverse=True)
    if not logs:
        return None, 'no comfyui log'
    txt = logs[0].read_text(errors='replace')
    return ('Using sage attention' in txt), logs[0].name

ok, lg = sage_active()
print('[pre] ComfyUI 日志 %s → SageAttention %s' % (lg, 'ALIVE ✅' if ok else 'NOT ACTIVE ⛔'))
if ok is False:
    print('[pre] ⛔ 拒绝开跑：sage 未生效，跑出来就不是 1.40× 档的数据')
    sys.exit(3)

job = O.Job(
    product_images=["/tmp/_smoke.png"],
    model_images=["/root/autodl-tmp/h3p/input/model_default_zh_f26.png"],
    product_text="白色陶瓷马克杯 350ml 北欧简约风 可微波炉可洗碗机",
    params={"product_name": "陶瓷马克杯", "category": "home",
            "duration": 30, "script_style": "scene"},
)

orch = O.Orchestrator(job, OUT, dry=False)
t0 = time.time()
rc = orch.run()
mins = (time.time() - t0) / 60
print("=== 全链结束 rc=%d 耗时 %.1f 分钟 ===" % (rc, mins), flush=True)

res = {"rc": rc, "minutes": round(mins, 1), "sage": bool(ok),
       "segments": len(orch.segments),
       "frozen": [s.idx for s in orch.segments if s.frozen],
       "judge": [{"seg": s.idx, "attempts": s.attempts,
                  "pass": (s.verdict or {}).get("pass"),
                  "raw_pass": (s.verdict or {}).get("raw_pass"),
                  "l2": ((s.verdict or {}).get("l2_meta") or {}).get("items"),
                  "l2_status": ((s.verdict or {}).get("l2_meta") or {}).get("l2_error")
                               or ((s.verdict or {}).get("l2_meta") or {}).get("l2_skipped") or "ok"}
                 for s in orch.segments]}
f = OUT / "video" / "final.mp4"
if f.exists():
    res["final"] = str(f)
    res["final_duration"] = round(O.Orchestrator._duration(str(f)), 2)
    v = json.loads(sp.run([O.FFPROBE, "-v", "error", "-print_format", "json",
                           "-show_streams", "-select_streams", "v:0", str(f)],
                          capture_output=True, text=True).stdout)["streams"][0]
    a = json.loads(sp.run([O.FFPROBE, "-v", "error", "-print_format", "json",
                           "-show_streams", "-select_streams", "a", str(f)],
                          capture_output=True, text=True).stdout)["streams"]
    res["size"] = "%sx%s" % (v["width"], v["height"])
    res["frames"] = v.get("nb_frames")
    res["audio"] = a[0]["codec_name"] if a else None
(OUT / "e2e_result.json").write_text(json.dumps(res, ensure_ascii=False, indent=2),
                                     encoding='utf-8')
print(json.dumps(res, ensure_ascii=False), flush=True)
