#!/usr/bin/env python3
"""
judge_l0.py - L0 算术判官（8 项，纯算术，零模型，<1s/段）

实现 judge_config.yaml L0 段：
- L0-01: 解码成功
- L0-02: 帧数符合 17k+5 网格
- L0-03: 分辨率 = 声明值
- L0-04: 时长 = 声明值 ±1 帧
- L0-05: 音轨存在且非全静音
- L0-06: 无黑屏 / 全白帧段
- L0-07: 无长静音 >1.5s
- L0-08: 容器完整可播

零依赖：仅 stdlib + ffmpeg/ffprobe
GGUF 适配：路径自动支持 fl2va / ref2va / safetensors 输出
"""
import json
import os
import re
import subprocess
import sys
from typing import Optional


# 17k+5 帧网格（24fps）
# 合法帧数 = 17k + 5 (k=0,1,2,...)
VALID_FRAMES_24FPS = set(17 * k + 5 for k in range(50))  # 5 to 850
# 完整列表（参考 REPORT30 §2.3）
EXPLICIT_GRID = [22, 39, 56, 73, 90, 107, 124, 141, 158, 175,
                  192, 209, 226, 243, 260, 277, 294, 311, 328, 345, 362]


def run_ffprobe(video_path: str) -> dict:
    """跑 ffprobe，提取 stream 信息。失败抛异常。"""
    cmd = [
        "ffprobe", "-v", "error",
        "-print_format", "json",
        "-show_format", "-show_streams",
        "-count_frames",
        video_path
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            return {"error": f"ffprobe returncode={r.returncode}", "stderr": r.stderr[:500]}
        return json.loads(r.stdout)
    except subprocess.TimeoutExpired:
        return {"error": "ffprobe timeout"}
    except FileNotFoundError:
        return {"error": "ffprobe not found in PATH"}
    except json.JSONDecodeError as e:
        return {"error": f"ffprobe json parse: {e}"}


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


def L0_01_decode(video_path: str) -> dict:
    """L0-01: 解码成功"""
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_format", video_path],
        capture_output=True, text=True, timeout=30
    )
    if r.returncode == 0:
        return _result("L0-01", "yes", 0, 0)
    return _result("L0-01", "no", r.returncode, 0)


def L0_02_frame_count_mod(video_path: str, declared_frames: Optional[int] = None) -> dict:
    """L0-02: 帧数符合 17k+5 网格"""
    info = run_ffprobe(video_path)
    if "error" in info:
        return _result("L0-02", "na", None, None, "low", info["error"])

    streams = info.get("streams", [])
    video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    if not video_stream:
        return _result("L0-02", "no", 0, "17k+5 grid")

    nb_frames = video_stream.get("nb_frames")
    if nb_frames is None:
        try:
            nb_frames = int(video_stream.get("nb_read_frames", 0))
        except (ValueError, TypeError):
            nb_frames = 0

    # ffprobe 返回 nb_frames 通常是 string (json "124"), 需要 int 化
    try:
        nb_frames = int(nb_frames)
    except (ValueError, TypeError):
        nb_frames = 0

    is_valid = (nb_frames in EXPLICIT_GRID) or (nb_frames in VALID_FRAMES_24FPS)
    if is_valid:
        return _result("L0-02", "yes", nb_frames, "17k+5 grid")
    return _result("L0-02", "no", nb_frames, "17k+5 grid")


def L0_03_resolution(video_path: str, declared_w: int, declared_h: int) -> dict:
    """L0-03: 分辨率 = 声明值"""
    info = run_ffprobe(video_path)
    if "error" in info:
        return _result("L0-03", "na", None, None, "low", info["error"])
    streams = info.get("streams", [])
    video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    if not video_stream:
        return _result("L0-03", "no", 0, f"{declared_w}x{declared_h}")

    actual_w = video_stream.get("width", 0)
    actual_h = video_stream.get("height", 0)
    if actual_w == declared_w and actual_h == declared_h:
        return _result("L0-03", "yes", f"{actual_w}x{actual_h}", f"{declared_w}x{declared_h}")
    return _result("L0-03", "no", f"{actual_w}x{actual_h}", f"{declared_w}x{declared_h}")


def L0_04_duration(video_path: str, declared_frames: int, fps: int = 24) -> dict:
    """L0-04: 时长 = 声明值 ±1 帧"""
    info = run_ffprobe(video_path)
    if "error" in info:
        return _result("L0-04", "na", None, None, "low", info["error"])
    streams = info.get("streams", [])
    video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    if not video_stream:
        return _result("L0-04", "no", 0, declared_frames / fps)

    nb_frames = video_stream.get("nb_frames")
    if nb_frames is not None:
        try:
            nb_frames = int(nb_frames)
            actual_dur = nb_frames / fps
        except (ValueError, TypeError):
            nb_frames = None

    if nb_frames is None:
        dur_str = video_stream.get("duration") or info.get("format", {}).get("duration", "0")
        try:
            actual_dur = float(dur_str)
        except (ValueError, TypeError):
            return _result("L0-04", "na", None, None, "low", "no duration")

    expected_dur = declared_frames / fps
    tolerance = 1 / fps
    if abs(actual_dur - expected_dur) <= tolerance:
        return _result("L0-04", "yes", actual_dur, f"{expected_dur}±{tolerance:.4f}s")
    return _result("L0-04", "no", actual_dur, f"{expected_dur}±{tolerance:.4f}s")


def L0_05_audio_present(video_path: str, silence_threshold_db: float = -50.0) -> dict:
    """L0-05: 音轨存在且非全静音

    用 ffmpeg astats 算 RMS，输出到 stderr，metadata 模式会写 key=value 行
    """
    info = run_ffprobe(video_path)
    if "error" in info:
        return _result("L0-05", "na", None, None, "low", info["error"])
    streams = info.get("streams", [])
    audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if not audio_stream:
        return _result("L0-05", "no", "no_audio_stream", f">{silence_threshold_db}dB RMS")

    # astats 输出在 stderr。ffmpeg 4.4 默认格式: "RMS level dB: -16.089"
    # ffmpeg 5.0+ metadata=1 格式: "RMS_level=-16.089"
    # 兼容两种格式
    cmd = [
        "ffmpeg", "-v", "info",
        "-i", video_path,
        "-af", "astats=metadata=1:reset=1",
        "-f", "null", "-"
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    all_output = r.stderr + r.stdout
    # 两种格式统一匹配
    matches = re.findall(r"RMS[ _]level(?:\s*dB)?[:=]\s*(-?\d+\.?\d*)", all_output)
    if not matches:
        matches = re.findall(r"Peak[ _]level(?:\s*dB)?[:=]\s*(-?\d+\.?\d*)", all_output)
    if matches:
        rms_db = max(float(m) for m in matches)
        if rms_db > silence_threshold_db:
            return _result("L0-05", "yes", rms_db, f">{silence_threshold_db}dB")
        return _result("L0-05", "no", rms_db, f">{silence_threshold_db}dB")
    return _result("L0-05", "na", None, None, "low", f"astats parse failed. stderr tail: {r.stderr[-300:]}")


def L0_06_no_black_frames(video_path: str, luma_threshold: int = 16) -> dict:
    """L0-06: 无黑屏 / 全白帧段"""
    cmd = [
        "ffmpeg", "-v", "error",
        "-i", video_path,
        "-vf", f"blackdetect=d=0.1:pic_th={luma_threshold/255:.3f}",
        "-f", "null", "-"
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    has_black = bool(re.search(r"black_start:\d+\.\d+ black_end:\d+\.\d+", r.stderr))
    if not has_black:
        return _result("L0-06", "yes", 0, f"luma<{luma_threshold}")
    return _result("L0-06", "no", "black_detected", f"luma<{luma_threshold}", "medium")


def L0_07_no_long_silence(video_path: str, max_silence_s: float = 1.5) -> dict:
    """L0-07: 无长静音 >1.5s"""
    cmd = [
        "ffmpeg", "-v", "error",
        "-i", video_path,
        "-af", f"silencedetect=noise=-30dB:d={max_silence_s}",
        "-f", "null", "-"
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    has_long = bool(re.search(r"silence_end:\d+\.\d+", r.stderr))
    if not has_long:
        return _result("L0-07", "yes", 0, f"<{max_silence_s}s")
    return _result("L0-07", "no", "long_silence_detected", f"<{max_silence_s}s", "medium")


def L0_08_container_integrity(video_path: str) -> dict:
    """L0-08: 容器完整可播"""
    cmd = ["ffmpeg", "-v", "error", "-i", video_path, "-f", "null", "-"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if r.returncode == 0 and not r.stderr.strip():
        return _result("L0-08", "yes", 0, "no_decode_error")
    return _result("L0-08", "no", r.stderr[:200], "no_decode_error", "medium")


def run_l0(video_path: str, declared_w: int = 1344, declared_h: int = 768,
           declared_frames: int = 192, fps: int = 24) -> list:
    """跑 L0 全部 8 项"""
    return [
        L0_01_decode(video_path),
        L0_02_frame_count_mod(video_path, declared_frames),
        L0_03_resolution(video_path, declared_w, declared_h),
        L0_04_duration(video_path, declared_frames, fps),
        L0_05_audio_present(video_path),
        L0_06_no_black_frames(video_path),
        L0_07_no_long_silence(video_path),
        L0_08_container_integrity(video_path),
    ]


if __name__ == "__main__":
    if len(sys.argv) < 6:
        print("Usage: judge_l0.py <video_path> <width> <height> <declared_frames> <fps>", file=sys.stderr)
        sys.exit(1)

    video_path = sys.argv[1]
    w = int(sys.argv[2])
    h = int(sys.argv[3])
    frames = int(sys.argv[4])
    fps = int(sys.argv[5])

    if not os.path.exists(video_path):
        print(f"ERROR: video not found: {video_path}", file=sys.stderr)
        sys.exit(2)

    results = run_l0(video_path, w, h, frames, fps)
    print(json.dumps(results, indent=2, ensure_ascii=False))
    exit_code = 0
    for r in results:
        if r.get("verdict") == "no" and r.get("confidence") == "high":
            exit_code = 1
    sys.exit(exit_code)
