#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用 faster-whisper 转写音频/视频，验证"到底说了什么"

用法: asr.py <文件1> [文件2 ...]
"""
import os, subprocess, sys, tempfile

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

MODEL = os.environ.get("ASR_MODEL", "base.en")


def find_ffmpeg():
    """服务器 PATH 里没有 ffmpeg，优先用 imageio_ffmpeg 自带的"""
    import shutil
    if shutil.which("ffmpeg"):
        return "ffmpeg"
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and os.path.exists(exe):
            return exe
    except Exception:
        pass
    return "ffmpeg"


FFMPEG = find_ffmpeg()


def extract(path):
    """任意媒体 → 16k 单声道 wav"""
    tmp = tempfile.mktemp(suffix=".wav")
    subprocess.run([FFMPEG, "-y", "-v", "error", "-i", path,
                    "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", tmp], check=False)
    return tmp


def main():
    from faster_whisper import WhisperModel
    print(f"[asr] 模型 {MODEL}  HF_ENDPOINT={os.environ['HF_ENDPOINT']}", flush=True)
    model = WhisperModel(MODEL, device=os.environ.get("ASR_DEVICE", "cuda"),
                         compute_type=os.environ.get("ASR_CT", "float16"))
    for p in sys.argv[1:]:
        wav = extract(p)
        if not os.path.exists(wav) or os.path.getsize(wav) < 1000:
            print(f"\n### {p}\n  (音频提取失败)")
            continue
        segs, info = model.transcribe(wav, beam_size=5, vad_filter=True,
                                      condition_on_previous_text=False)
        print(f"\n### {p}")
        print(f"  语言={info.language} 置信={info.language_probability:.2f} "
              f"时长={info.duration:.2f}s")
        got = []
        for s in segs:
            print(f"  [{s.start:6.2f} → {s.end:6.2f}] {s.text.strip()}")
            got.append(s.text.strip())
        print(f"  全文: {' '.join(got)}")
        os.remove(wav)


if __name__ == "__main__":
    main()
