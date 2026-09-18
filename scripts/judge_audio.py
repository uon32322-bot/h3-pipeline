#!/usr/bin/env python3
"""
judge_audio.py - A 段音频判官（5 项，CPU）

实现 judge_config.yaml audio_judge 段：
- A-01: 音轨存在且非静音 (复用 L0-05)
- A-02: 口播内容与文案逐字一致 (faster-whisper)
- A-03: 口型与语音同步 (lipsync2)
- A-04: 语音在段内结束 (尾部空闲 < 1.0s)
- A-05: 跨段音色一致 (F0 中位数 + 频谱质心)

GGUF 适配：H3 自生成连续语音，无 TTS。audio 来源是 mp4 内嵌音轨或独立 wav。
"""
import json
import os
import re
import subprocess
import sys
import wave
from typing import Optional


def _result(item_id, verdict, value, threshold, confidence="high", note=None):
    r = {
        "item_id": item_id,
        "verdict": verdict,
        "evidence": {"frame": None, "region": None, "value": value, "threshold": threshold},
        "confidence": confidence,
    }
    if note:
        r["note"] = note
    return r


def extract_audio(video_path: str, output_wav: str) -> bool:
    """从 mp4 抽 wav (16kHz mono)"""
    try:
        r = subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-i", video_path,
             "-vn", "-ac", "1", "-ar", "16000", "-acodec", "pcm_s16le", output_wav],
            capture_output=True, text=True, timeout=30
        )
        return r.returncode == 0 and os.path.exists(output_wav)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def A_01_audio_present(video_path: str) -> dict:
    """A-01: 音轨存在且非静音（复用 L0-05 逻辑 + 更宽松阈值）"""
    # 简化版：直接看 stream
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a",
         "-show_entries", "stream=codec_name", "-of", "default=nw=1", video_path],
        capture_output=True, text=True, timeout=10
    )
    if not r.stdout.strip():
        return _result("A-01", "no", "no_audio_stream", "> -50dB", "high")

    # 抽 audio 算 RMS
    wav = "/tmp/_a01.wav"
    if not extract_audio(video_path, wav):
        return _result("A-01", "na", None, "> -50dB", "low", "ffmpeg extract failed")

    # 用 astats 算 RMS
    r2 = subprocess.run(
        ["ffmpeg", "-v", "info", "-i", wav,
         "-af", "astats=metadata=1:reset=1",
         "-f", "null", "-"],
        capture_output=True, text=True, timeout=30
    )
    matches = re.findall(r"RMS[ _]level(?:\s*dB)?[:=]\s*(-?\d+\.?\d*)", r2.stderr + r2.stdout)
    if matches:
        rms_db = max(float(m) for m in matches)
        try:
            os.unlink(wav)
        except OSError:
            pass
        if rms_db > -50:
            return _result("A-01", "yes", rms_db, "> -50dB")
        return _result("A-01", "no", rms_db, "> -50dB", "high")

    return _result("A-01", "na", None, "> -50dB", "low", "astats parse failed")


def A_02_voiceover_consistency(video_path: str, expected_text: str) -> dict:
    """A-02: 口播内容与文案逐字一致 (faster-whisper int8 CPU)"""
    if not expected_text:
        return _result("A-02", "na", None, 0.9, "low", "no expected text")

    wav = "/tmp/_a02.wav"
    if not extract_audio(video_path, wav):
        return _result("A-02", "na", None, 0.9, "low", "ffmpeg extract failed")

    try:
        from faster_whisper import WhisperModel
        # int8 CPU, base model (74M params)
        model = WhisperModel("base", device="cpu", compute_type="int8")
        segments, info = model.transcribe(wav, language="zh", beam_size=1, vad_filter=False)
        detected = "".join([s.text for s in segments]).strip()
    except ImportError:
        try:
            os.unlink(wav)
        except OSError:
            pass
        return _result("A-02", "na", None, 0.9, "low", "faster_whisper not available")
    except Exception as e:
        try:
            os.unlink(wav)
        except OSError:
            pass
        return _result("A-02", "na", None, 0.9, "low", f"whisper: {e}")
    finally:
        try:
            os.unlink(wav)
        except OSError:
            pass

    # 中文相似度（去掉空格标点）
    import re
    clean_exp = re.sub(r'[\s,.。?!;:]', '', expected_text.lower())
    clean_det = re.sub(r'[\s,.。?!;:]', '', detected.lower())
    if not clean_det:
        return _result("A-02", "no", 0.0, 0.9, "high", f"whisper detected empty (expected: {expected_text[:50]})")
    # 用 SequenceMatcher
    from difflib import SequenceMatcher
    sim = SequenceMatcher(None, clean_exp, clean_det).ratio()
    if sim >= 0.9:
        return _result("A-02", "yes", sim, 0.9, "medium", f"detected: {detected[:80]}")
    return _result("A-02", "no", sim, 0.9, "medium", f"detected: {detected[:80]}")


def A_03_lip_sync(video_path: str) -> dict:
    """A-03: 口型与语音同步 (lipsync2 stub)"""
    # lipsync2.py 是项目内脚本，未实现
    return _result("A-03", "na", None, 0.30, "low", "lipsync2 not yet implemented; use only with L-mode audio")


def A_04_audio_ends_in_segment(video_path: str, declared_duration_s: float) -> dict:
    """A-04: 语音在段内结束（尾部空闲 < 1.0s）"""
    wav = "/tmp/_a04.wav"
    if not extract_audio(video_path, wav):
        return _result("A-04", "na", None, "< 1.0s", "low", "ffmpeg extract failed")

    try:
        # 用 silencedetect 找末尾的最后一个 silence_end
        r = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", wav,
             "-af", "silencedetect=noise=-30dB:d=0.3",
             "-f", "null", "-"],
            capture_output=True, text=True, timeout=30
        )
        # parse 所有 silence_end
        ends = re.findall(r"silence_end:\s*(\d+\.?\d*)", r.stderr)
        if not ends:
            # 没找到 silence 段 = 整段都在说话，结尾可能没空
            r_dur = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=nw=1:nk=1", wav],
                capture_output=True, text=True, timeout=10
            )
            try:
                actual_dur = float(r_dur.stdout.strip())
            except (ValueError, TypeError):
                return _result("A-04", "na", None, "< 1.0s", "low", "duration parse failed")
            tail_idle = actual_dur - declared_duration_s
            if abs(tail_idle) < 1.0:
                return _result("A-04", "yes", tail_idle, "< 1.0s", "medium")
            return _result("A-04", "no", tail_idle, "< 1.0s", "medium")

        last_end = float(ends[-1])
        # 获取 wav 总时长
        with wave.open(wav, "rb") as wf:
            total_s = wf.getnframes() / wf.getframerate()
        tail_idle = total_s - last_end
        if tail_idle < 1.0:
            return _result("A-04", "yes", tail_idle, "< 1.0s", "medium")
        return _result("A-04", "no", tail_idle, "< 1.0s", "medium")
    finally:
        try:
            os.unlink(wav)
        except OSError:
            pass


def A_05_cross_segment_timbre(video_path: str, prev_video_path: Optional[str] = None) -> dict:
    """A-05: 跨段音色一致 (F0 中位数 + 频谱质心)"""
    if not prev_video_path:
        return _result("A-05", "na", None, "|ΔF0| ≤ 15%", "low", "no prev segment")

    import numpy as np

    def extract_f0(path):
        wav = "/tmp/_a05.wav"
        if not extract_audio(path, wav):
            return None
        try:
            # 用 librosa 提 F0
            import librosa
            y, sr = librosa.load(wav, sr=16000)
            f0, _, _ = librosa.pyin(y, fmin=80, fmax=400, sr=sr)
            f0_clean = f0[~np.isnan(f0)]
            return float(np.median(f0_clean)) if len(f0_clean) > 0 else None
        except ImportError:
            return None
        finally:
            try:
                os.unlink(wav)
            except OSError:
                pass

    f0_a = extract_f0(prev_video_path)
    f0_b = extract_f0(video_path)
    if f0_a is None or f0_b is None:
        return _result("A-05", "na", None, "|ΔF0| ≤ 15%", "low", "F0 extraction failed (librosa or pyin)")

    # ΔF0 比例
    delta = abs(f0_b - f0_a) / max(f0_a, 1e-3)
    if delta <= 0.15:
        return _result("A-05", "yes", delta, "|ΔF0| ≤ 15%", "medium", f"F0: {f0_a:.1f} -> {f0_b:.1f} Hz")
    return _result("A-05", "no", delta, "|ΔF0| ≤ 15%", "medium", f"F0: {f0_a:.1f} -> {f0_b:.1f} Hz")


def run_audio_judge(video_path: str, expected_text: str = "",
                     prev_video_path: Optional[str] = None,
                     declared_duration_s: float = 0.0) -> list:
    """跑 A 段全部 5 项"""
    results = []
    results.append(A_01_audio_present(video_path))
    results.append(A_02_voiceover_consistency(video_path, expected_text))
    results.append(A_03_lip_sync(video_path))
    if declared_duration_s > 0:
        results.append(A_04_audio_ends_in_segment(video_path, declared_duration_s))
    else:
        results.append(_result("A-04", "na", None, "< 1.0s", "low", "no declared duration"))
    results.append(A_05_cross_segment_timbre(video_path, prev_video_path))
    return results


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: judge_audio.py <video_path> [expected_text] [prev_video_path] [duration_s]", file=sys.stderr)
        sys.exit(1)

    video_path = sys.argv[1]
    expected_text = sys.argv[2] if len(sys.argv) > 2 else ""
    prev_video = sys.argv[3] if len(sys.argv) > 3 else None
    duration = float(sys.argv[4]) if len(sys.argv) > 4 else 0.0

    if not os.path.exists(video_path):
        print(f"ERROR: video not found: {video_path}", file=sys.stderr)
        sys.exit(2)

    results = run_audio_judge(video_path, expected_text, prev_video, duration)
    print(json.dumps(results, indent=2, ensure_ascii=False))
