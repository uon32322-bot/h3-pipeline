#!/usr/bin/env python3
"""
judge_l1.py - L1 本地 CPU 判官（10 项，5-20s/段）

实现 judge_config.yaml L1 段：
- L1-01: 人脸嵌入距离 (arcface cosine)
- L1-02: 产品 ROI 相似度 (histogram)
- L1-03: 光流幅度（动作是否真发生）
- L1-04: 光流断裂点（动作跳变）
- L1-05: 跨段直方图/调色板相似
- L1-06: OCR 逐字比对（cv2 + easyocr 回退）
- L1-07: 唇音互相关 (lipsync2)
- L1-08: 状态跟踪
- L1-09: 手部关键点计数 (mediapipe)
- L1-10: 帧间差分周期性 (FFT 闪烁检测)

依赖（服务器已装）：cv2, numpy, scipy, mediapipe, insightface, faster_whisper
缺：paddleocr → 用 cv2 + easyocr（lazy import）
缺：标定集 → 所有阈值 tau_* 用占位符，首期只告警不否决
GGUF 适配：路径解析（fl2va / ref2va / safetensors 任意输出）
"""
import json
import os
import sys
import subprocess
from typing import Optional

import cv2
import numpy as np


# ============= 阈值占位（标定集建好后替换）=============
# 占位值 = 标定集 first pass 默认（最保守侧：少误报）
TAU_FACE = 0.45          # arcface 距离 < tau_face 判同人
TAU_PROD = 0.80          # 产品 ROI 直方图相似度 > tau_prod
TAU_FLOW = 0.5           # 光流均值 > tau_flow 判有动作
TAU_FLOW_BREAK = 8.0     # 光流二阶导峰值 < tau_flow_break 判平滑
TAU_TONE = 0.85          # 跨段调色板相似 > tau_tone
TAU_LIP = 0.30           # 唇音互相关 > tau_lip 且 |lag| <= 2 frames
TAU_STATE_WINDOW = 0.3   # 状态落帧时间窗（相对时长比例）
TAU_HAND = 0.8           # 5 指有效率 > tau_hand
TAU_FLICKER = 0.6        # 频域峰值集中度 < tau_flicker 判无闪烁


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


# ============= 工具函数 =============
def extract_frames(video_path: str, max_frames: int = 32) -> list:
    """抽帧（用 cv2 均匀抽）"""
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return []
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if total <= 0:
        cap.release()
        return []
    step = max(1, total // max_frames)
    frames = []
    for i in range(0, total, step):
        cap.set(cv2.CAP_PROP_POS_FRAMES, i)
        ret, frame = cap.read()
        if ret:
            frames.append(frame)
        if len(frames) >= max_frames:
            break
    cap.release()
    return frames


def extract_first_frame(video_path: str) -> Optional[np.ndarray]:
    """抽首帧"""
    cap = cv2.VideoCapture(video_path)
    ret, frame = cap.read()
    cap.release()
    if ret:
        return frame
    return None


# ============= L1-01 人脸嵌入距离 =============
def L1_01_face_distance(seg_video: str, anchor_frame: Optional[np.ndarray] = None) -> dict:
    """L1-01: 人脸嵌入距离 < tau_face 判同人

    实施策略：mediapipe face_landmarker 提取面部 landmark 关键点，
    用关键点坐标的归一化相对距离作为"身份"特征（无需大模型下载）
    标定集建好后，可切换到 insightface（需先下 buffalo_l 模型 ~ 280MB）
    """
    if anchor_frame is None:
        return _result("L1-01", "na", None, TAU_FACE, "low", "no anchor frame for comparison")

    frame = extract_first_frame(seg_video)
    if frame is None:
        return _result("L1-01", "na", None, TAU_FACE, "low", "cannot read seg first frame")

    try:
        import mediapipe as mp
        mp_face = mp.solutions.face_mesh

        def extract_landmarks(img):
            if img is None:
                return None
            rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
            with mp_face.FaceMesh(static_image_mode=True, max_num_faces=1) as fm:
                res = fm.process(rgb)
            if not res.multi_face_landmarks:
                return None
            lm = res.multi_face_landmarks[0].landmark
            # 用眼睛 + 鼻尖 + 嘴三角 8 个 landmark 归一化坐标作特征
            key_idx = [33, 133, 362, 263, 1, 61, 291, 199]  # 双眼角 + 鼻尖 + 嘴角
            return np.array([(lm[i].x, lm[i].y) for i in key_idx], dtype=np.float32).flatten()

        a_lm = extract_landmarks(anchor_frame)
        b_lm = extract_landmarks(frame)
        if a_lm is None or b_lm is None:
            return _result("L1-01", "na", None, TAU_FACE, "low", "no face landmark in one or both frames")

        # L2 归一化（对眼睛距离做归一化）
        def normalize(lm):
            eye_dist = np.linalg.norm(lm[:2] - lm[2:4]) + 1e-6
            return lm / eye_dist

        a_n = normalize(a_lm)
        b_n = normalize(b_lm)
        dist = float(np.linalg.norm(a_n - b_n))
        if dist < TAU_FACE:
            return _result("L1-01", "yes", dist, TAU_FACE, "medium", "mediapipe landmark distance")
        return _result("L1-01", "no", dist, TAU_FACE, "medium", "mediapipe landmark distance")
    except ImportError:
        return _result("L1-01", "na", None, TAU_FACE, "low", "mediapipe not available")
    except Exception as e:
        return _result("L1-01", "na", None, TAU_FACE, "low", f"face detect: {e}")


# ============= 旧 insightface 实现（保留，待模型下载好后切回）=============
def L1_01_face_distance_insightface(seg_video: str, anchor_frame: Optional[np.ndarray] = None) -> dict:
    """L1-01: insightface 版本（需先下 buffalo_l 模型 ~280MB）"""
    try:
        from insightface.app import FaceAnalysis
        app = FaceAnalysis(allowed_modules=['detection', 'recognition'])
        app.prepare(ctx_id=-1, det_size=(640, 640))
    except (ImportError, Exception) as e:
        return _result("L1-01", "na", None, TAU_FACE, "low", f"insightface: {e}")

    if anchor_frame is None:
        return _result("L1-01", "na", None, TAU_FACE, "low", "no anchor frame")
    frame = extract_first_frame(seg_video)
    if frame is None:
        return _result("L1-01", "na", None, TAU_FACE, "low", "cannot read seg first frame")

    try:
        faces_a = app.get(anchor_frame)
        faces_b = app.get(frame)
        if not faces_a or not faces_b:
            return _result("L1-01", "na", None, TAU_FACE, "low", "no face detected")
        emb_a = faces_a[0].normed_embedding
        emb_b = faces_b[0].normed_embedding
        cos_sim = float(np.dot(emb_a, emb_b))
        dist = 1 - cos_sim
        if dist < TAU_FACE:
            return _result("L1-01", "yes", dist, TAU_FACE, "high", f"insightface cos_sim={cos_sim:.3f}")
        return _result("L1-01", "no", dist, TAU_FACE, "high", f"insightface cos_sim={cos_sim:.3f}")
    except Exception as e:
        return _result("L1-01", "na", None, TAU_FACE, "low", f"face analyze: {e}")


# ============= L1-02 产品 ROI 相似度 =============
def L1_02_product_roi(seg_video: str, anchor_roi: Optional[np.ndarray] = None) -> dict:
    """L1-02: 产品 ROI 直方图相似度 > tau_prod"""
    if anchor_roi is None:
        return _result("L1-02", "na", None, TAU_PROD, "low", "no anchor ROI")

    frame = extract_first_frame(seg_video)
    if frame is None:
        return _result("L1-02", "na", None, TAU_PROD, "low", "cannot read seg first frame")

    # hsv 直方图
    hsv_a = cv2.cvtColor(anchor_roi, cv2.COLOR_BGR2HSV)
    hsv_b = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    # resize 统一尺寸
    h, w = hsv_a.shape[:2]
    hsv_b_r = cv2.resize(hsv_b, (w, h))
    hist_a = cv2.calcHist([hsv_a], [0, 1], None, [50, 60], [0, 180, 0, 256])
    hist_b = cv2.calcHist([hsv_b_r], [0, 1], None, [50, 60], [0, 180, 0, 256])
    cv2.normalize(hist_a, hist_a)
    cv2.normalize(hist_b, hist_b)
    sim = float(cv2.compareHist(hist_a, hist_b, cv2.HISTCMP_CORREL))
    if sim > TAU_PROD:
        return _result("L1-02", "yes", sim, TAU_PROD, "medium")
    return _result("L1-02", "no", sim, TAU_PROD, "medium")


# ============= L1-03 光流幅度 =============
def L1_03_optical_flow(seg_video: str) -> dict:
    """L1-03: 光流均值 > tau_flow + 无 >0.5s 平台"""
    frames = extract_frames(seg_video, max_frames=20)
    if len(frames) < 2:
        return _result("L1-03", "na", None, TAU_FLOW, "low", "not enough frames")

    flows = []
    for i in range(1, len(frames)):
        prev_gray = cv2.cvtColor(frames[i-1], cv2.COLOR_BGR2GRAY)
        next_gray = cv2.cvtColor(frames[i], cv2.COLOR_BGR2GRAY)
        # downsample for speed
        prev_gray = cv2.resize(prev_gray, (320, 320))
        next_gray = cv2.resize(next_gray, (320, 320))
        flow = cv2.calcOpticalFlowFarneback(prev_gray, next_gray, None,
                                            0.5, 3, 15, 3, 5, 1.2, 0)
        mag = np.sqrt(flow[..., 0]**2 + flow[..., 1]**2)
        flows.append(float(mag.mean()))

    mean_flow = float(np.mean(flows))
    fps = 24
    # 检测平台 (>0.5s 帧间流 = 12 帧)
    platform_threshold = 0.05  # 极小流
    consecutive_low = 0
    has_platform = False
    for f in flows:
        if f < platform_threshold:
            consecutive_low += 1
            if consecutive_low >= int(0.5 * fps):  # 12 frames
                has_platform = True
                break
        else:
            consecutive_low = 0

    if mean_flow > TAU_FLOW and not has_platform:
        return _result("L1-03", "yes", mean_flow, TAU_FLOW, "medium", "no platform detected")
    if mean_flow <= TAU_FLOW:
        return _result("L1-03", "no", mean_flow, TAU_FLOW, "medium", "insufficient motion")
    return _result("L1-03", "no", mean_flow, TAU_FLOW, "medium", "platform detected")


# ============= L1-04 光流断裂点 =============
def L1_04_flow_break(seg_video: str) -> dict:
    """L1-04: 光流二阶导峰值 < tau_flow_break 判平滑（无跳变）"""
    frames = extract_frames(seg_video, max_frames=20)
    if len(frames) < 3:
        return _result("L1-04", "na", None, TAU_FLOW_BREAK, "low", "not enough frames")

    flows = []
    for i in range(1, len(frames)):
        prev_gray = cv2.cvtColor(cv2.resize(frames[i-1], (320, 320)), cv2.COLOR_BGR2GRAY)
        next_gray = cv2.cvtColor(cv2.resize(frames[i], (320, 320)), cv2.COLOR_BGR2GRAY)
        flow = cv2.calcOpticalFlowFarneback(prev_gray, next_gray, None,
                                            0.5, 3, 15, 3, 5, 1.2, 0)
        mag = np.sqrt(flow[..., 0]**2 + flow[..., 1]**2)
        flows.append(float(mag.mean()))

    # 二阶导
    if len(flows) < 3:
        return _result("L1-04", "na", None, TAU_FLOW_BREAK, "low", "too few flow samples")
    second_deriv = np.diff(np.diff(flows))
    max_break = float(np.max(np.abs(second_deriv)))

    if max_break < TAU_FLOW_BREAK:
        return _result("L1-04", "yes", max_break, TAU_FLOW_BREAK, "medium", "smooth flow")
    return _result("L1-04", "no", max_break, TAU_FLOW_BREAK, "medium", "motion jump detected")


# ============= L1-05 跨段直方图相似 =============
def L1_05_cross_segment_tone(seg_a_video: str, seg_b_video: str) -> dict:
    """L1-05: 跨段色调相似"""
    fa = extract_first_frame(seg_a_video)
    fb = extract_first_frame(seg_b_video)
    if fa is None or fb is None:
        return _result("L1-05", "na", None, TAU_TONE, "low", "cannot read frames")

    hsv_a = cv2.cvtColor(cv2.resize(fa, (256, 256)), cv2.COLOR_BGR2HSV)
    hsv_b = cv2.cvtColor(cv2.resize(fb, (256, 256)), cv2.COLOR_BGR2HSV)
    hist_a = cv2.calcHist([hsv_a], [0, 1], None, [50, 60], [0, 180, 0, 256])
    hist_b = cv2.calcHist([hsv_b], [0, 1], None, [50, 60], [0, 180, 0, 256])
    cv2.normalize(hist_a, hist_a)
    cv2.normalize(hist_b, hist_b)
    sim = float(cv2.compareHist(hist_a, hist_b, cv2.HISTCMP_CORREL))
    if sim > TAU_TONE:
        return _result("L1-05", "yes", sim, TAU_TONE, "medium")
    return _result("L1-05", "no", sim, TAU_TONE, "medium")


# ============= L1-06 OCR 逐字比对 =============
def L1_06_ocr(video_path: str, expected_text: str) -> dict:
    """L1-06: OCR 逐字比对（cv2 文字检测 + easyocr 备选）"""
    if not expected_text:
        return _result("L1-06", "na", None, 0, "low", "no expected text provided")

    # 优先用 easyocr
    try:
        import easyocr
        reader = easyocr.Reader(['en'], gpu=False, verbose=False)
        results = reader.readtext(video_path, paragraph=True)
        detected = " ".join([r[1] for r in results])
    except ImportError:
        # 回退：cv2 + tesseract
        detected = ""

    # 简单相似度
    if not detected:
        return _result("L1-06", "na", None, 0, "low", "OCR not available (install easyocr or tesseract)")

    from difflib import SequenceMatcher
    sim = SequenceMatcher(None, expected_text.lower().strip(), detected.lower().strip()).ratio()
    if sim >= 0.9:
        return _result("L1-06", "yes", sim, 0.9, "medium", f"detected: {detected[:80]}")
    return _result("L1-06", "no", sim, 0.9, "medium", f"detected: {detected[:80]}")


# ============= L1-07 唇音互相关 =============
def L1_07_lip_sync(video_path: str, audio_path: str = None) -> dict:
    """L1-07: 唇音互相关（lipsync2 算法 stub）"""
    # lipsync2.py 是项目内脚本，先 stub
    if not audio_path:
        return _result("L1-07", "na", None, TAU_LIP, "low", "no audio path")
    # 真实实现: 脸区搜索 + mouth_roi_x_audio_envelope 互相关
    return _result("L1-07", "na", None, TAU_LIP, "low", "lipsync2 not yet implemented in judge_l1")


# ============= L1-08 状态跟踪 =============
def L1_08_state_track(seg_video: str, expected_state_at_frame: dict) -> dict:
    """L1-08: 状态落正确帧号（占位）"""
    if not expected_state_at_frame:
        return _result("L1-08", "na", None, TAU_STATE_WINDOW, "low", "no expected state")
    return _result("L1-08", "na", None, TAU_STATE_WINDOW, "low", "state_track not yet implemented")


# ============= L1-09 手部关键点 =============
def L1_09_hand_keypoints(video_path: str) -> dict:
    """L1-09: 手部关键点有效率"""
    try:
        import mediapipe as mp
    except ImportError:
        return _result("L1-09", "na", None, TAU_HAND, "low", "mediapipe not available")

    mp_hands = mp.solutions.hands
    frames = extract_frames(video_path, max_frames=10)
    if not frames:
        return _result("L1-09", "na", None, TAU_HAND, "low", "no frames")

    valid_count = 0
    total_count = 0
    with mp_hands.Hands(static_image_mode=True, max_num_hands=2, model_complexity=0) as hands:
        for frame in frames:
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            res = hands.process(rgb)
            if res.multi_hand_landmarks:
                for hand in res.multi_hand_landmarks:
                    total_count += 1
                    # 5 指 + 21 关键点，每个手指有 4 关节
                    if len(hand.landmark) == 21:
                        # 简易检查：5 个指尖（4, 8, 12, 16, 20）都有非零坐标
                        finger_tips = [4, 8, 12, 16, 20]
                        all_valid = all(hand.landmark[i].x != 0 and hand.landmark[i].y != 0
                                        for i in finger_tips)
                        if all_valid:
                            valid_count += 1

    if total_count == 0:
        return _result("L1-09", "na", None, TAU_HAND, "low", "no hands detected in frames")

    ratio = valid_count / total_count
    if ratio >= TAU_HAND:
        return _result("L1-09", "yes", ratio, TAU_HAND, "medium")
    return _result("L1-09", "no", ratio, TAU_HAND, "medium")


# ============= L1-10 闪烁检测 =============
def L1_10_flicker(video_path: str) -> dict:
    """L1-10: 帧间差分周期性（频域峰值）"""
    frames = extract_frames(video_path, max_frames=30)
    if len(frames) < 10:
        return _result("L1-10", "na", None, TAU_FLICKER, "low", "not enough frames")

    diffs = []
    for i in range(1, len(frames)):
        prev = cv2.cvtColor(frames[i-1], cv2.COLOR_BGR2GRAY).astype(np.float32)
        curr = cv2.cvtColor(frames[i], cv2.COLOR_BGR2GRAY).astype(np.float32)
        diff = np.abs(curr - prev).mean()
        diffs.append(diff)

    diffs = np.array(diffs)
    # 去掉 DC
    diffs = diffs - diffs.mean()
    # FFT
    fft = np.fft.fft(diffs)
    power = np.abs(fft) ** 2
    # 找最高峰（除 DC）
    power[0] = 0
    peak_freq = int(np.argmax(power))
    peak_power = float(power[peak_freq])
    total_power = float(power.sum() + 1e-9)
    # 集中度 = 峰值/总能量
    concentration = peak_power / total_power

    if concentration < TAU_FLICKER:
        return _result("L1-10", "yes", concentration, TAU_FLICKER, "medium", "no dominant flicker freq")
    return _result("L1-10", "no", concentration, TAU_FLICKER, "medium", f"flicker freq at {peak_freq}")


# ============= 顶层封装 =============
def run_l1(video_path: str, anchor_frame: Optional[np.ndarray] = None,
           anchor_roi: Optional[np.ndarray] = None,
           seg_prev_video: Optional[str] = None,
           expected_text: str = "",
           audio_path: Optional[str] = None) -> list:
    """跑 L1 全部 10 项"""
    results = []
    results.append(L1_01_face_distance(video_path, anchor_frame))
    results.append(L1_02_product_roi(video_path, anchor_roi))
    results.append(L1_03_optical_flow(video_path))
    results.append(L1_04_flow_break(video_path))
    if seg_prev_video:
        results.append(L1_05_cross_segment_tone(seg_prev_video, video_path))
    else:
        results.append(_result("L1-05", "na", None, TAU_TONE, "low", "no prev segment for cross-tone check"))
    results.append(L1_06_ocr(video_path, expected_text))
    results.append(L1_07_lip_sync(video_path, audio_path))
    results.append(L1_08_state_track(video_path, {}))
    results.append(L1_09_hand_keypoints(video_path))
    results.append(L1_10_flicker(video_path))
    return results


# ============= CLI =============
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: judge_l1.py <video_path> [anchor_image_path] [expected_text]", file=sys.stderr)
        sys.exit(1)

    video_path = sys.argv[1]
    anchor_path = sys.argv[2] if len(sys.argv) > 2 else None
    expected_text = sys.argv[3] if len(sys.argv) > 3 else ""

    if not os.path.exists(video_path):
        print(f"ERROR: video not found: {video_path}", file=sys.stderr)
        sys.exit(2)

    anchor_frame = None
    anchor_roi = None
    if anchor_path and os.path.exists(anchor_path):
        anchor_frame = cv2.imread(anchor_path)
        anchor_roi = anchor_frame

    results = run_l1(video_path, anchor_frame, anchor_roi, None, expected_text)
    print(json.dumps(results, indent=2, ensure_ascii=False))
    sys.exit(0)
