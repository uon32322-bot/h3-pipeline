#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
S3.5 · 音轨先行（audio-first）构建器
运动鞋 40s 带货片 · V 片（全画外音）

顺序铁律：先出音轨 → 再按音轨句边界反推镜/段边界。
理由：H3 的 AddGuide(audio) 锚「锚到帧号、裁到剩余片长」⇒ 音轨时长必须先定。

输出：
  vo/track_full.wav        32 kHz 单声道，总长 = 目标时长（±0.3 s）
  vo/track_manifest.json   逐句起止 + 镜/段映射 + 闸C 校验值
"""
import json, os, shutil, subprocess, sys

BASE = "/Users/admin/WorkBuddy/AI 生成带货视频/h3-pipeline/deliverables/shoe_40s"
VOICE = "Tingting"          # zh_CN
RATE = 240                  # say -r：实测 4.42 有效字/秒（落 4–5 区间）
SR = 32000
TARGET = 40.000             # 成片目标时长
LEAD = 0.40                 # 头留白（开场前静音）
TAIL = 0.60                 # 尾留白（CTA 收束定格，必须留）
GAP_MIN, GAP_MAX = 0.15, 0.80   # 句间空隙允许区间（超出则说明字数要调）
FPS = 24
LUFS = -16                  # 口播整体响度目标（社交视频常用 -16 ~ -14 LUFS）
TP = -1.5                   # 真峰上限 dBTP

# (key, 段, 口播句, 覆盖镜位说明)
SENTS = [
    ("s1_hook",   "①", "你脚上那双跑鞋，可能选错了。"),
    ("s2_pain",   "②", "早上挤地铁，晚上还要举铁，一双鞋从早穿到晚，脚底板发麻、后跟发酸。"),
    ("s3a_proof", "③", "这双鞋拿贾卡网布做鞋面，透气孔密到能看见光；中底压了三层泡棉，落地那一下先软后弹。"),
    ("s3b_hook2", "③", "把它翻过来看：缓震层叠了三道，后跟还收了一道弧；跑起来脚踝不容易往外翻。"),
    ("s4_trust",  "④", "说实话它不算轻，单只接近三百克；但正是这份分量，让你跑到最后两公里还站得稳。"),
    ("s5_cta",    "⑤", "现在下单，两色可选，尺码从三六到四六；不满意七天可退。点下方小黄车。"),
]


def run(*a, **kw):
    return subprocess.run(a, check=True, **kw)


def dur(p):
    return float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", p],
        capture_output=True, text=True, check=True).stdout.strip())


def nchar(s):
    return sum(1 for c in s if c not in "，。；：、！？ ")


def main():
    parts = os.path.join(BASE, "vo/parts")
    os.makedirs(parts, exist_ok=True)

    # ---- 1) 逐句合成（原生语速 -r 240）----
    raw = []
    for key, seg, txt in SENTS:
        aiff = os.path.join(parts, key + ".aiff")
        wav = os.path.join(parts, key + "_raw.wav")
        run("say", "-v", VOICE, "-r", str(RATE), "-o", aiff, txt)
        run("ffmpeg", "-y", "-loglevel", "error", "-i", aiff, "-ar", str(SR), "-ac", "1", wav)
        raw.append((key, seg, txt, wav, dur(wav)))

    total_raw = sum(r[4] for r in raw)
    n_gap = len(raw) - 1
    speech_target = TARGET - LEAD - TAIL - GAP_MIN * n_gap
    tempo = total_raw / speech_target
    tempo = max(0.5, min(2.0, tempo))

    # ---- 2) 语速对齐（>1.02 才压，atempo 保音高）----
    fitted = []
    for key, seg, txt, wav, d0 in raw:
        out = os.path.join(parts, key + ".wav")
        if tempo > 1.02:
            run("ffmpeg", "-y", "-loglevel", "error", "-i", wav,
                "-filter:a", f"atempo={tempo:.5f}", "-ar", str(SR), "-ac", "1", out)
        else:
            shutil.copy(wav, out)
        fitted.append((key, seg, txt, out, dur(out)))

    total_fit = sum(f[4] for f in fitted)

    # ---- 3) 动态求解句间空隙，使总长恒 = TARGET ----
    slack = TARGET - LEAD - TAIL - total_fit
    gap = slack / n_gap
    gap = max(GAP_MIN, min(GAP_MAX, gap))

    sil_gap = os.path.join(parts, "_sil_gap.wav")
    run("ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
        "-i", f"anullsrc=r={SR}:cl=mono", "-t", f"{gap:.4f}", sil_gap)
    sil_lead = os.path.join(parts, "_sil_lead.wav")
    run("ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
        "-i", f"anullsrc=r={SR}:cl=mono", "-t", f"{LEAD:.4f}", sil_lead)
    sil_tail = os.path.join(parts, "_sil_tail.wav")
    run("ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
        "-i", f"anullsrc=r={SR}:cl=mono", "-t", f"{TAIL:.4f}", sil_tail)

    manifest_sents, concat_list, t = [], [sil_lead], LEAD
    for i, (key, seg, txt, wav, d) in enumerate(fitted):
        st, en = t, t + d
        manifest_sents.append({
            "key": key, "seg": seg, "text": txt, "chars": nchar(txt),
            "start": round(st, 3), "end": round(en, 3), "dur": round(d, 3),
            "cps": round(nchar(txt) / d, 2),
        })
        concat_list.append(wav)
        t = en
        if i < len(fitted) - 1:
            concat_list.append(sil_gap)
            t += gap
    concat_list.append(sil_tail)

    lst = os.path.join(parts, "_list.txt")
    open(lst, "w").write("".join(f"file '{p}'\n" for p in concat_list))
    track_raw = os.path.join(BASE, "vo/_track_raw.wav")
    run("ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
        "-i", lst, "-ar", str(SR), "-ac", "1", track_raw)

    # ---- 3b) 响度归一（社交视频口播目标 -16 LUFS / 真峰 -1.5 dBTP）----
    track = os.path.join(BASE, "vo/track_full.wav")
    run("ffmpeg", "-y", "-loglevel", "error", "-i", track_raw,
        "-filter:a", f"loudnorm=I={LUFS}:TP={TP}:LRA=11",
        "-ar", str(SR), "-ac", "1", track)
    total = dur(track)

    # ---- 4) 音轨 → 镜/段边界（audio-first 反推）----
    def fr(s):  # 秒 → 相对 17k+5 网格的最近帧
        return min((17 * k + 5 for k in range(1, 22)), key=lambda g: abs(g / FPS - s))

    shots, boundaries = [], []
    for s in manifest_sents:
        boundaries += [s["start"], s["end"]]
    # 镜边界 = 句边界（③ 内部再切，由分镜表人工指定）
    for s in manifest_sents:
        shots.append({"voice": s["key"], "seg": s["seg"],
                      "start": s["start"], "end": s["end"],
                      "frames": fr(s["end"] - s["start"])})

    total_chars = sum(s["chars"] for s in manifest_sents)
    report = {
        "mode": "V", "voice": VOICE, "rate": RATE, "sr": SR,
        "target_dur": TARGET, "actual_dur": round(total, 3),
        "dur_err": round(total - TARGET, 3),
        "lead": LEAD, "tail": TAIL, "gap": round(gap, 4),
        "atempo": round(tempo, 4),
        "raw_speech_dur": round(total_raw, 3),
        "chars_total": total_chars,
        "cps_overall": round(total_chars / TARGET, 2),
        "sentences": manifest_sents,
        "boundaries_sec": [round(b, 3) for b in boundaries],
    }
    json.dump(report, open(os.path.join(BASE, "vo/track_manifest.json"), "w"),
              ensure_ascii=False, indent=1)

    # ---- 5) 打印 ----
    print(f"音色={VOICE}  -r={RATE}  目标={TARGET:.3f}s  实际={total:.3f}s  误差={total-TARGET:+.3f}s")
    print(f"语音原长={total_raw:.3f}s  atempo={tempo:.4f}  头留白={LEAD}s  尾留白={TAIL}s  句间空隙={gap:.4f}s")
    print(f"总字数={total_chars}  整体语速={total_chars/TARGET:.2f} 字/秒")
    print()
    print(f"{'句':<12}{'段':<3}{'起':>8}{'止':>8}{'时长':>8}{'字':>4}{'字/秒':>7}  闸C")
    ok = True
    for s in manifest_sents:
        flag = "✅" if 3.5 <= s["cps"] <= 5.5 else "⚠️"
        if flag != "✅":
            ok = False
        print(f"{s['key']:<12}{s['seg']:<3}{s['start']:>8.3f}{s['end']:>8.3f}"
              f"{s['dur']:>8.3f}{s['chars']:>4}{s['cps']:>7}  {flag}")
    print()
    lo, hi = TARGET * 4, TARGET * 5
    print(f"闸C 总字数区间 [{lo:.0f}, {hi:.0f}] → 实际 {total_chars} "
          f"{'✅' if lo <= total_chars <= hi else '⚠️'}")
    print(f"闸C 时长误差 ≤0.3s → {abs(total-TARGET):.3f} {'✅' if abs(total-TARGET)<=0.3 else '⚠️'}")
    print(f"闸C 逐句语速 3.5–5.5 → {'✅' if ok else '⚠️'}")


if __name__ == "__main__":
    main()
