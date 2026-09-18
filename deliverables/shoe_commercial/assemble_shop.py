#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""assemble_shop.py —— 把 3 段 H3 成片合成一条完整带货片。

一次 ffmpeg 通过完成四件事：
  ① 3 段 124 帧拼接（concat filter，同规格无需转码中间件）
  ② 后期图文叠加（6 张逐镜层 + 1 张 AIGC 合规常驻层）
  ③ 中文旁白配音（6 句 TTS，按镜位 adelay 入点铺轨）
  ④ 丢弃 H3 原生音轨（未验证内容，带货片用纯旁白更干净）+ 响度标准化

用法：python3 assemble_shop.py C1.mp4 C2.mp4 C3.mp4
"""
import json
import os
import subprocess
import sys

D = os.path.dirname(os.path.abspath(__file__))
FPS = 24
DUR = 15.5          # 124 帧 × 3 / 24fps


def run(cmd):
    print(" ".join(cmd[:6]) + " ...", flush=True)
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        print(r.stderr[-4000:])
        sys.exit(1)
    return r


def main():
    clips = sys.argv[1:4]
    plan = json.load(open(os.path.join(D, "post/vo_plan.json")))
    ov = json.load(open(os.path.join(D, "post/overlay_manifest.json")))

    cmd = ["ffmpeg", "-y", "-loglevel", "error"]
    for c in clips:
        cmd += ["-i", c]
    for m in ov:                                    # 3..8
        cmd += ["-i", os.path.join(D, m["png"])]
    cmd += ["-i", os.path.join(D, "post/overlay/aigc.png")]        # 9
    for i in range(1, 7):                                          # 10..15
        cmd += ["-i", os.path.join(D, "post/v%d.wav" % i)]

    fc = []
    # ① 拼接（只取视频，H3 原生音轨丢弃）
    fc.append("[0:v][1:v][2:v]concat=n=3:v=1:a=0[vcat]")
    # ② 逐镜图文
    prev = "vcat"
    for i, m in enumerate(ov):
        idx = 3 + i
        out = "ov%d" % i
        fc.append("[%s][%d:v]overlay=0:0:enable='between(t,%.3f,%.3f)'[%s]"
                  % (prev, idx, m["t0"], m["t1"], out))
        prev = out
    fc.append("[%s][9:v]overlay=0:0[vfin]" % prev)
    fc.append("[vfin]format=yuv420p[vout]")
    # ③ 旁白铺轨
    labels = []
    for i, st in enumerate(plan["start"]):
        lab = "a%d" % (i + 1)
        fc.append("[%d:a]adelay=%d,aformat=sample_fmts=fltp:channel_layouts=stereo[%s]"
                  % (10 + i, int(round(st * 1000)), lab))
        labels.append("[%s]" % lab)
    fc.append("%samix=inputs=%d:duration=longest:normalize=0,"
              "apad,atrim=0:%.3f,asetpts=PTS-STARTPTS,"
              "loudnorm=I=-16:TP=-1.5:LRA=11[aout]"
              % ("".join(labels), len(labels), DUR))

    cmd += ["-filter_complex", ";".join(fc),
            "-map", "[vout]", "-map", "[aout]",
            "-r", str(FPS), "-c:v", "libx264", "-preset", "slow", "-crf", "18",
            "-profile:v", "high", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "160k", "-ar", "44100", "-ac", "2",
            "-movflags", "+faststart", "-shortest",
            os.path.join(D, "SHOE_COMMERCIAL.mp4")]
    run(cmd)
    print("\n✅ 合成完成: SHOE_COMMERCIAL.mp4")

    # 抽帧核验
    frames = [0, 30, 61, 90, 124, 155, 185, 216, 247, 278, 309, 340, 371]
    os.makedirs(os.path.join(D, "frames"), exist_ok=True)
    for f in frames:
        run(["ffmpeg", "-y", "-loglevel", "error", "-i",
             os.path.join(D, "SHOE_COMMERCIAL.mp4"),
             "-vf", "select='eq(n\\,%d)'" % f, "-vframes", "1",
             os.path.join(D, "frames/f%03d.png" % f)])
    p = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                        "format=duration,size:stream=codec_name,width,height,nb_frames",
                        "-of", "json", os.path.join(D, "SHOE_COMMERCIAL.mp4")],
                       capture_output=True, text=True)
    print(p.stdout)


if __name__ == "__main__":
    main()
