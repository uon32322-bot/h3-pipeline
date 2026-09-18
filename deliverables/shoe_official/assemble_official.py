#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""assemble_official.py —— 合成两个交付版本（一次 ffmpeg 各跑一遍）。

  版本 1  SOP 官方版   B + A 直接拼接，保留 H3 原生音轨，**不加任何后期文字**
                       → SHOE_SOP_FILM.mp4
  版本 2  带货投放版   在官方版之上叠中文带货信息层 + AIGC 合规标识
                       → SHOE_COMMERCE.mp4

⭐ 拼接顺序 = **B（真人穿着）在前、A（产品主片）在后**
   ⇒ 结尾落在"产品＋完整单行文案"收束，满足官方 SOP
     「结尾必须是单一全画幅产品收束＋完整单行文案」的硬约束。
   ⚠️ 踩过的坑：早期版本写成 assemble([b, a]) 而 b 其实是第二个参数 —— 结果顺序反了
      （变成 A 在前、B 在最后 ⇒ 片子结尾没有文案）。所以这里改成**显式命名**
      FIRST/SECOND，不再用 a/b 这种容易搞反的变量名，并在打印里回显顺序。

音频：两片各带 H3 原生音频；在接缝处做 0.25 s 交叉淡化避免音乐断点，再补静音到画面总长。

用法: assemble_official.py <前段mp4> <后段mp4>
      例：assemble_official.py raw/B.mp4 raw/A3.mp4
"""
import json
import os
import subprocess
import sys

D = os.path.dirname(os.path.abspath(__file__))
FPS = 24


def run(cmd, quiet=True):
    print("  $ ffmpeg ... %s" % cmd[-1], flush=True)
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        print(r.stderr[-3000:])
        sys.exit(1)


def probe(p):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                        "format=duration:stream=codec_type,width,height,nb_frames",
                        "-of", "json", p], capture_output=True, text=True)
    return json.loads(r.stdout)


def nframes(p):
    return int([s for s in probe(p)["streams"] if s["codec_type"] == "video"][0]["nb_frames"])


def assemble(clips, out, overlays=None, dur=None):
    """clips = [前段, 后段]，按此顺序拼接。"""
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", clips[0], "-i", clips[1]]
    n = 2
    if overlays:
        for m in overlays:
            cmd += ["-i", m["png"]]
            n += 1
        cmd += ["-i", os.path.join(D, "post_shop/overlay/aigc.png")]
        n += 1

    fc = ["[0:v][1:v]concat=n=2:v=1:a=0[vcat]"]
    prev = "vcat"
    if overlays:
        for i, m in enumerate(overlays):
            idx = 2 + i
            fc.append("[%s][%d:v]overlay=0:0:enable='between(t,%.4f,%.4f)'[ov%d]"
                      % (prev, idx, m["t0"], m["t1"], i))
            prev = "ov%d" % i
        fc.append("[%s][%d:v]overlay=0:0[vov]" % (prev, 2 + len(overlays)))
        prev = "vov"
    fc.append("[%s]format=yuv420p[vout]" % prev)

    # 音频：接缝交叉淡化 → 补静音到画面总长
    fc.append("[0:a][1:a]acrossfade=d=0.25:c1=tri:c2=tri,"
              "aformat=sample_fmts=fltp:channel_layouts=stereo,"
              "apad,atrim=0:%.4f,asetpts=PTS-STARTPTS,"
              "loudnorm=I=-16:TP=-1.5:LRA=11[aout]" % dur)

    cmd += ["-filter_complex", ";".join(fc), "-map", "[vout]", "-map", "[aout]",
            "-r", str(FPS), "-c:v", "libx264", "-preset", "slow", "-crf", "18",
            "-profile:v", "high", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "160k", "-ar", "44100", "-ac", "2",
            "-movflags", "+faststart", "-shortest", out]
    run(cmd)


def main():
    first, second = sys.argv[1], sys.argv[2]
    n1, n2 = nframes(first), nframes(second)
    total = (n1 + n2) / FPS
    print("顺序： 前段 %s = %d 帧 / %.3fs   →   后段 %s = %d 帧 / %.3fs   合计 %d 帧 / %.3fs"
          % (os.path.basename(first), n1, n1 / FPS,
             os.path.basename(second), n2, n2 / FPS, n1 + n2, total))
    assert n1 == 124 and n2 == 243, "顺序疑似反了：期望 前段=124帧(B) / 后段=243帧(A)"

    # 版本 1：SOP 官方版
    assemble([first, second], os.path.join(D, "SHOE_SOP_FILM.mp4"), None, total)

    # 版本 2：带货投放版
    ov = json.load(open(os.path.join(D, "post_shop/overlay_manifest.json")))
    assemble([first, second], os.path.join(D, "SHOE_COMMERCE.mp4"), ov, total)

    for f in ("SHOE_SOP_FILM.mp4", "SHOE_COMMERCE.mp4"):
        p = os.path.join(D, f)
        v = [s for s in probe(p)["streams"] if s["codec_type"] == "video"][0]
        print("  %-22s %sx%s  %s 帧  %.3fs  %.2f MB"
              % (f, v["width"], v["height"], v["nb_frames"],
                 float(probe(p)["format"]["duration"]), os.path.getsize(p) / 1048576))


if __name__ == "__main__":
    main()
