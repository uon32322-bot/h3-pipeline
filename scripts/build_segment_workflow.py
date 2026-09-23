#!/usr/bin/env python3
"""FL2VA + 5 AddGuide 中间锚 —— 段内 6 cell 宫格图 8s 测试视频配置

每段 6 cell 宫格图 (3 行 × 2 列, 完美 9:16 比例)
H3 AddGuide 5 个中间锚 (frame_idx: 24/72/120/144/168)
8s = 192 帧, 24fps

修复点 (相对旧 2 cell 版本):
  1. ✅ 6 cell (1:1 cell → 9:16 画布) 替代 2 cell (避免拉伸)
  2. ✅ 5 AddGuide 中间锚 (替代 1 个) 让动作更细腻
  3. ✅ 段间动作细化, 每段 1 个核心动作
"""
import json
import os
from typing import Optional

H3P = "/root/autodl-tmp/h3p"


# === 通用 prompt (适合所有 5 段, 段间差异由 cell 内容决定) ===
def build_segment_prompt(seg_idx: int, product_info: dict = None,
                          segment_desc: str = "",
                          cell_actions: list = None,
                          voiceover_text: str = "",
                          model_gender: str = "neutral",
                          voiceover_full: dict = None) -> str:
    """生成单段 H3 prompt (对齐官方 H3 skill 9 条硬约束 + 崩坏缓解)

    关键修复 (2026-09-23, 修复"画面不连贯 + 产品不一致 + 配音不自然 + 男模特女声"):
      - 接受 cell_actions (6 cell 显式动作描述, 来自宫格图元数据)
      - 接受 voiceover_text (本段台词, H3 模型自动合成中文语音)
      - 接受 model_gender ("female"/"male"/"neutral") 决定 H3 原生音色
      - Subject 2 包含 info.shape + color 完整形态 (修复"产品不一致")
      - detailed_description 用 6 cell 显式动作序列 (修复"动作不连贯/凭空出现")
      - overall_soundscape 按性别描述音色 (修复"男模特+女声")

    根据 product_info 动态生成主体描述:
    - 人物: 通用模型描述 (男女通用, 不锁脸/性别, 让 H3 first_frame 决定)
    - 产品: 从 product_info.shape (详细形态) + color + product_form 完整拼写

    9 条硬约束 (来自 h3-fl2va-addguide-fine-grain-storyboard skill):
      1. 时长严格 (8s = 192 帧)
      2. 单镜单动作 (每段 1 个核心动作)
      3. 禁时序跳变词 (不用"随后/接着/然后")
      4. 手部细到指节 (拇指/食指/掌心)
      5. 物理形态先于颜色 + negation (NOT other category)
      6. 镜1镜6镜像 (开头结尾同一构图)
      7. 中间2镜核心动作 (climax 是镜头重点)
      8. 产品前3秒入画 (镜1 必须有产品)
      9. only ONE product (不出现第二个)

    Args:
        seg_idx: 段号 (1-5)
        product_info: 包含 product_form/color/name/category/selling_points/shape
        segment_desc: 本段文案描述 (从 storyboard 取)
        cell_actions: 6 个 cell 的显式动作描述 (从宫格图 cell 1-6 提取)
                      每个 cell 一个短句, 描述具体动作 + 接触点 + 视觉反馈
        voiceover_text: 本段中文台词 (H3 模型自动合成语音 + 同步口型)
        model_gender: 模特性别 ("female"/"male"/"neutral"), 决定 TTS 音色
    """
    # 解析产品信息
    name = (product_info or {}).get("name", "产品")
    pf = (product_info or {}).get("product_form", "")  # 物理形态简称 (bottle/jar/tube)
    color = (product_info or {}).get("color", "")
    shape = (product_info or {}).get("shape", "")  # 详细形态描述 (修复产品一致性)
    sp_list = (product_info or {}).get("selling_points", [])
    if isinstance(sp_list, str):
        sp_list = [s.strip() for s in sp_list.split("|") if s.strip()][:3]
    if not sp_list:
        sp_list = ["核心卖点"]
    sp_str = ", ".join(sp_list[:3])

    # 主体 1 描述 (人物, 通用, 让 H3 first_frame 决定外貌)
    subject1 = "<Subject 1> is the model from <Picture 1> (the reference image). Preserve the model's facial identity, hairstyle, body proportions, and outfit exactly across the entire clip."

    # 主体 2 描述 (产品, 动态) — 修复"产品不一致"
    # **关键**: 用 info.shape 完整形态描述 + color + product_form, 让 H3 看到 Picture 1 里的精确产品
    shape_detail = shape if shape else (pf or name)
    subject2 = (
        f"<Subject 2> is the EXACT {name} from <Picture 1>.\n"
        f"Physical form: {shape_detail}\n"
        f"Color: {color or 'as shown in Picture 1'}\n"
        f"A single {name} only, never two, never three, never a reflection, never a duplicate, never a similar-looking different product. "
        f"Preserve the EXACT product shape, color, material, and details from <Picture 1> throughout the entire clip. "
        f"The product is {name}, NOT any other category (NOT lipstick, NOT perfume, NOT cup, NOT bottle of different brand)."
    )

    # 段主题 (用 LLM 输出的 segment_desc, 避免硬编码)
    seg_theme = segment_desc if segment_desc else f"段 {seg_idx} 演示 {name} 的核心卖点 ({sp_str})"

    # === 6 cell 显式动作序列 (修复"动作不连贯") ===
    # 如果传了 cell_actions (从宫格图元数据提取), 用它; 否则用默认占位
    if cell_actions and len(cell_actions) >= 6:
        cells_block = "\n".join([
            f"[Cell {i+1} — {'SETUP' if i == 0 else 'TRIGGER' if i == 1 else 'BUILDUP' if i == 2 else 'CLIMAX' if i == 3 else 'DECAY' if i == 4 else 'RESULT'}]: {cell_actions[i].strip()}"
            for i in range(6)
        ])
    else:
        # fallback: 抽象描述 (老逻辑, 已知会导致动作不连贯)
        cells_block = f"""[Cell 1 — SETUP]: <Subject 1> holds the {name} in display position.
[Cell 2 — TRIGGER]: <Subject 1> begins the demonstration of {seg_theme}.
[Cell 3 — BUILDUP]: <Subject 1> deepens the demonstration.
[Cell 4 — CLIMAX]: The product's {sp_list[0] if sp_list else 'core selling point'} is fully visible.
[Cell 5 — DECAY]: <Subject 1> returns to neutral pose.
[Cell 6 — RESULT]: <Subject 1> holds the {name} in closing confirmation pose."""

    # === 音轨 prompt (修复"配音不自然"——用 H3 原生 TTS + 适配模特性别) ===
    gender_word = {
        "female": "young Chinese woman (mid-20s, friendly and slightly upbeat, pitch around 180-220 Hz, warm timbre, conversational pace)",
        "male":   "young Chinese man (mid-20s, friendly and slightly upbeat, pitch around 110-140 Hz, warm timbre, conversational pace)",
        "neutral": "young Chinese adult speaker (mid-20s, friendly and slightly upbeat, pitch around 150-180 Hz, warm timbre, conversational pace)",
    }.get(model_gender, "young Chinese adult speaker (mid-20s, friendly and slightly upbeat, pitch around 150-180 Hz, warm timbre, conversational pace)")

    voice_pitch_desc = {
        "female": "a warm female voice with natural feminine pitch",
        "male":   "a warm male voice with natural masculine pitch",
        "neutral": "a clear, gender-appropriate voice matching the model's appearance",
    }.get(model_gender, "a clear voice")

    pronoun = "Her voice" if model_gender == "female" else "His voice" if model_gender == "male" else "The voice"

    if voiceover_text:
        # H3 模型自动合成中文 + 同步口型 (按性别决定音色 + ClipForge [pause] 强制停顿)
        # 加 [pause 0.4s] 让 H3 在句中停顿, 模仿带货主播节奏感
        # 自动在标点处加 pause (借鉴 ClipForge)
        pause_augmented = (
            voiceover_text
            .replace("。", ". [pause 0.3s]")
            .replace("，", ", [pause 0.2s]")
            .replace("! ", "! [pause 0.4s] ")
            .replace("? ", "? [pause 0.4s] ")
            .replace("！", "! [pause 0.4s]")
            .replace("？", "? [pause 0.4s]")
            .replace("、", ", [pause 0.15s]")
        )

        # 2026-09-23 修复: 用 voiceover_full 注入 emotion + audio_directive,
        # 让 H3 模型按节拍控制语气 (excited/pain_point/urgent 等)
        emotion_block = ""
        if voiceover_full:
            emotion = voiceover_full.get("emotion", "")
            pacing = voiceover_full.get("pacing", "")
            volume = voiceover_full.get("volume", "")
            audio_dir = voiceover_full.get("audio_directive", "")
            if emotion or audio_dir:
                emotion_block = (
                    f"\n<Subject 1> delivers this voiceover with this specific delivery: "
                    f"emotion={emotion or 'neutral'}, pacing={pacing or 'medium'}, "
                    f"volume={volume or 'medium'}. {audio_dir}. "
                )

        soundscape_block = (
            f"<Subject 1> speaks as a {gender_word}. "
            f"She/he delivers this voiceover in clear, fluent Mandarin Chinese with lip movements perfectly synced to the speech, with natural pauses between phrases: \"{pause_augmented}\".{emotion_block}"
            f"The Chinese speech is the dominant audio. No background music, no ambient noise. "
            f"The voice must match <Subject 1>'s gender as shown in <Picture 1>: {voice_pitch_desc}."
        )
    else:
        soundscape_block = (
            f"<Subject 1> speaks as a {gender_word} in clear Mandarin Chinese. "
            f"{pronoun} is the dominant audio. No background music, no ambient noise."
        )

    # === SLCT Visual Bible (借鉴 Creatify SLCT 框架 + xixihhhh/clipforge 跨段一致性) ===
    # 跨段只允许变 "Action", 其他字段保持恒定, 让产品/人物/光照/技术参数在 5 段一致
    visual_bible = (
        f"\n[VISUAL BIBLE — CONSTANT ACROSS ALL 5 SEGMENTS, DO NOT CHANGE]:\n"
        f"- Subject (Product): {name}, {shape_detail}, color {color or 'as in Picture 1'}, product_form={pf}\n"
        f"- Lighting: soft natural indoor light, even illumination, no harsh shadows\n"
        f"- Camera style: handheld smartphone UGC style, close-up / macro framing\n"
        f"- Technical: 9:16 vertical format, realistic photography (NOT 3D/CGI/illustration)\n"
        f"- Setting: clean indoor location matching <Picture 1>\n"
        f"- Model appearance: <Subject 1> from <Picture 1> (face, hair, body, outfit stay IDENTICAL across all 6 panels and across the entire 8-second clip)\n"
        f"Only ACTION, camera angle, and emotion change between segments. All other visual parameters stay locked.\n"
    )

    return f"""subject_definitions:
{subject1}
{subject2}
{visual_bible}

summary:
[keyframe completion] Segment {seg_idx}/5 of 5-segment 40-second video. The target video shows <Subject 1> demonstrating <Subject 2> ({name}) in a continuous 8-second one-take shot. The video begins with a product display pose (镜1 = SETUP), transitions through the key feature demonstration (镜2-5 = TRIGGER→BUILDUP→CLIMAX→DECAY), and ends with a closing confirmation pose mirroring the opening (镜6 = RESULT mirror of 镜1). This segment's theme: {seg_theme}.

retention_analysis:
<Subject 1> (appears in the entire clip): fully_preserved - retain facial identity, hairstyle, body proportions, outfit throughout. Skin texture has natural pores, no plastic-looking, no airbrushed effect. Hand details: 拇指+食指捏持 / 掌心托住 / 虎口卡住, NOT blurry hands, NOT missing fingers, NOT extra fingers.
<Subject 2> (appears in all 6 panels of <Picture 1>): fully_preserved - retain the EXACT product shape, color, material, and details from <Picture 1>. Physical form: {shape_detail}. Color: {color}. The product is {name}, NOT any other category, NOT a different brand. Never morphs, never duplicates, never shows mirror reflection. Product is visible in frame within first 3 seconds (镜1 must contain product).
<Picture 1> (scene anchor): fully_preserved - background and lighting consistent throughout.

detailed_description:
Live-action authentic TikTok/Douyin UGC style with realistic skin texture, smartphone camera, stable tripod-steady framing, no camera shake, realistic shadows, no plastic-looking skin, no airbrushed effect, no makeup overlay, no color grading. One continuous shot, no cuts, no time jumps, no sequence jumps. 8 seconds exact duration.

This segment demonstrates: {seg_theme}.

The video must follow this EXACT 6-cell action sequence from <Picture 1> (each cell corresponds to one continuous 1.3-second interval, totaling 8 seconds):

{cells_block}

CRITICAL: Each AddGuide keyframe (frame_idx=24/72/120/144/168) is a STRONG visual constraint. The video MUST visually match cell 2-5 at those exact moments.

STRICT cell-by-cell timing (DO NOT skip any step):
- Seconds 0.0-1.3 (Cell 1 SETUP): exactly per Cell 1 action
- Seconds 1.3-2.7 (Cell 2 TRIGGER): exactly per Cell 2 action
- Seconds 2.7-4.0 (Cell 3 BUILDUP): exactly per Cell 3 action
- Seconds 4.0-5.3 (Cell 4 CLIMAX): exactly per Cell 4 action
- Seconds 5.3-6.7 (Cell 5 DECAY): exactly per Cell 5 action
- Seconds 6.7-8.0 (Cell 6 RESULT): exactly per Cell 6 action

Do NOT skip cells 2-3 to jump to cell 4. Do NOT have the product start working without the model's prior action. Do NOT add objects from nowhere — every object must come from <Subject 1>'s hands. The AddGuide anchors are STRONGER than the prompt description — if there is a conflict, follow the anchor image.

[Shot 1 — SETUP] At 00:00.000, the segment opens with <Subject 1> in a clean indoor setting matching <Picture 1>. The product ({name}) must appear in frame within the first 3 seconds. <Subject 1> performs the action from Cell 1.

[Shot 2 — TRIGGER] At 00:01.333, <Subject 1> performs the action from Cell 2. Hand details are precise: thumb + index finger grip, or palm support. The product is held steadily, no shaking.

[Shot 3 — BUILDUP] At 00:03.000, <Subject 1> performs the action from Cell 3. The product's key feature begins to show its function. Hand movements are smooth and purposeful.

[Shot 4 — CLIMAX] At 00:05.000, <Subject 1> performs the action from Cell 4. The product's {sp_list[0] if sp_list else 'core selling point'} is fully visible and clearly demonstrated. This is the most visually dramatic moment.

[Shot 5 — DECAY] At 00:06.333, <Subject 1> performs the action from Cell 5. The product's transformation or benefit is now visible.

[Shot 6 — RESULT, mirror of Shot 1] At 00:07.000, <Subject 1> performs the action from Cell 6. Camera framing, posture, and product position mirror the opening frame, creating a satisfying visual loop.

Hard constraints enforced: 1 single take (no cuts), 8 seconds exact (no time jumps), no "随后/接着/然后" transitions, hands always precise (指节 detail), only ONE product in frame (never two, never a reflection), product visible in first 3 seconds, no plastic-looking skin, no airbrushed effect, skin texture has natural pores.

No additional people, no other products, no mirrors, no reflections, no extraneous accessories appear in the scene at any point. The {name} stays consistent throughout. <Subject 1>'s face, body, and outfit stay consistent throughout.

overall_soundscape:
{soundscape_block}

non_diegetic_music:
N/A"""


def build_segment_workflow_6cell(segment_idx: int, product_id: str = "shoes",
                                    product_name: str = "running_shoes",
                                    product_info: Optional[dict] = None,  # type: ignore
                                    cell_paths: Optional[list] = None,
                                    segment_desc: str = "",
                                    cell_actions: list = None,
                                    voiceover_text: str = "",
                                    model_gender: str = "neutral",
                                    voiceover_full: dict = None):
    """构建单段 H3 workflow (6 cell 宫格图 + 5 AddGuide)

    关键改进:
      - 6 cell (3 行 × 2 列) 替代 2 cell, 避免拉伸
      - 5 AddGuide 中间锚 (frame_idx 24/72/120/144/168) 替代 1 个
      - 每段 8s = 192 帧
      - **P2 修复**: 接受 cell_paths 参数, 不再硬编码 seg{N}_cell1.png
        - 如果传 cell_paths (推荐): 严格使用传入路径, 不读 input/ 根目录
        - 如果不传: 退回旧行为 seg{N}_cell{1-6}.png (仅向后兼容)
    """
    seg_name = f"seg{segment_idx}"

    # P2: 优先用传入的 cell_paths, 否则 fallback 到旧硬编码
    if cell_paths and len(cell_paths) == 6:
        # ComfyUI LoadImage 期望: input/ 子目录里的 basename
        # 我们的 cell 在 projects/<name>/output/cells/, ComfyUI 找不到
        # **强制 copy 到 input/ + 用项目名前缀**避免冲突
        import shutil as _shutil_copy
        import re as _re_copy
        prefix = _re_copy.sub(r'[^a-zA-Z0-9_-]', '_', product_id)
        cell_filenames = []
        for i, src_path in enumerate(cell_paths, 1):
            base = os.path.basename(src_path)
            new_name = f"{prefix}_{base}"
            dst = os.path.join("/root/autodl-tmp/h3p/input", new_name)
            try:
                # 仅当文件不存在或不同才 copy (避免重复 IO)
                if not os.path.exists(dst) or os.path.getmtime(src_path) > os.path.getmtime(dst):
                    _shutil_copy.copy2(src_path, dst)
            except Exception as e:
                print(f"[warn] copy cell {src_path} → {dst} 失败: {e}")
            cell_filenames.append(new_name)
    else:
        # 向后兼容 (旧硬编码)
        cell_filenames = [f"{seg_name}_cell{i}.png" for i in range(1, 7)]
    # 段描述 (从 storyboard 取)
    seg_desc = ""
    try:
        # 尝试从 segment_dict 取 (如果调用者传了)
        seg_desc = (segment_idx and locals().get('segment_dict', {}).get('description', '')) or ""
    except Exception:
        pass
    prompt = build_segment_prompt(segment_idx, product_info or {}, seg_desc, cell_actions, voiceover_text, model_gender, voiceover_full)

    # 段间 seed 变化, 让 5 段视频不重复
    seed = 42000 + segment_idx * 100

    nodes = {
        # 模型加载
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "minimax_h3_fl2va_pruned_int8_convrot.safetensors", "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors", "type": "minimax", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": "minimax_h3_video_vae_fp16.safetensors"}},
        "4": {"class_type": "VAELoader", "inputs": {"vae_name": "minimax_h3_audio_vae_fp32.safetensors"}},
        # LoRA 加速
        "5": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["1", 0], "lora_name": "minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors", "strength_model": 1.0}},
        # 分辨率 9:16
        "6": {"class_type": "ResolutionSelector", "inputs": {"resolution": "9:16 (Portrait Widescreen)"}},
        # 加载 6 cell 宫格图
        "8":  {"class_type": "LoadImage", "inputs": {"image": cell_filenames[0]}},  # 首帧 (锚)
        "20": {"class_type": "LoadImage", "inputs": {"image": cell_filenames[1]}},
        "22": {"class_type": "LoadImage", "inputs": {"image": cell_filenames[2]}},
        "24": {"class_type": "LoadImage", "inputs": {"image": cell_filenames[3]}},
        "26": {"class_type": "LoadImage", "inputs": {"image": cell_filenames[4]}},
        "28": {"class_type": "LoadImage", "inputs": {"image": cell_filenames[5]}},  # 尾帧 (锚)
        # H3 Image-to-Video (首帧 cell1 + 尾帧 cell6)
        "7": {"class_type": "MiniMaxH3ImageToVideo", "inputs": {
            "clip": ["2", 0], "vae": ["3", 0],
            "prompt": prompt, "width": 768, "height": 1344, "length": 192,
            "first_frame": ["8", 0], "last_frame": ["28", 0]}},
        # 采样设置
        "10": {"class_type": "RandomNoise", "inputs": {"noise_seed": seed}},
        "11": {"class_type": "BasicGuider", "inputs": {"model": ["5", 0], "conditioning": ["7", 0]}},
        "12": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "res_multistep"}},
        "13": {"class_type": "BasicScheduler", "inputs": {"scheduler": "simple", "steps": 8, "denoise": 1.0, "model": ["5", 0]}},
        # 5 AddGuide 中间锚 (官方 H3 设计) — 所有 AddGuide 都基于 first_frame latent (2026-09-23 验证)
        # 之前错误: 链式传递 latent `["21", 1]` — MiniMaxH3AddGuide 只有 1 个 output (positive), index 1 不存在
        # 官方设计: AddGuide 修改 latent in-place, 输出 positive 给下一个 AddGuide, latent 都从 first_frame 引
        "21": {"class_type": "MiniMaxH3AddGuide", "inputs": {
            "positive": ["7", 0], "latent": ["7", 1], "image": ["20", 0],
            "vae": ["3", 0], "frame_idx": 24}},
        "23": {"class_type": "MiniMaxH3AddGuide", "inputs": {
            "positive": ["21", 0], "latent": ["7", 1], "image": ["22", 0],
            "vae": ["3", 0], "frame_idx": 72}},
        "25": {"class_type": "MiniMaxH3AddGuide", "inputs": {
            "positive": ["23", 0], "latent": ["7", 1], "image": ["24", 0],
            "vae": ["3", 0], "frame_idx": 120}},
        "27": {"class_type": "MiniMaxH3AddGuide", "inputs": {
            "positive": ["25", 0], "latent": ["7", 1], "image": ["26", 0],
            "vae": ["3", 0], "frame_idx": 144}},
        "29": {"class_type": "MiniMaxH3AddGuide", "inputs": {
            "positive": ["27", 0], "latent": ["7", 1], "image": ["28", 0],
            "vae": ["3", 0], "frame_idx": 168}},
        # 采样 + 输出
        "30": {"class_type": "BasicGuider", "inputs": {"model": ["5", 0], "conditioning": ["29", 0]}},
        "31": {"class_type": "SamplerCustomAdvanced", "inputs": {
            "noise": ["10", 0], "guider": ["30", 0],
            "sampler": ["12", 0], "sigmas": ["13", 0], "latent_image": ["7", 1]}},
        "32": {"class_type": "VAEDecode", "inputs": {"samples": ["31", 0], "vae": ["3", 0]}},
        "33": {"class_type": "VAEDecodeAudio", "inputs": {"samples": ["31", 0], "vae": ["4", 0]}},
        "34": {"class_type": "CreateVideo", "inputs": {"images": ["32", 0], "fps": 24, "audio": ["33", 0]}},
        "35": {"class_type": "SaveVideo", "inputs": {
            "video": ["34", 0], "filename_prefix": f"{product_id}_seg{segment_idx}_v6cell",
            "format": "auto", "codec": "auto"}},
    }
    return nodes


# === 切分 6 cell 工具函数 ===
def split_6cell_grid(grid_path: str, seg_idx: int, output_dir: str = "/root/autodl-tmp/h3p/input"):
    """切分 1 张 6 cell 宫格图 (3行2列) 为 6 个独立 cell PNG

    宫格图布局: 3 行 × 2 列
    cell 顺序 (官方 H3 AddGuide 约定):
      cell 1 (左上)   cell 2 (右上)
      cell 3 (左中)   cell 4 (右中)
      cell 5 (左下)   cell 6 (右下)
    """
    seg_name = f"seg{seg_idx}"
    script = f'''
from PIL import Image
import os, shutil
img = Image.open("{grid_path}")
w, h = img.size
cw, ch = w // 2, h // 3
for idx, (row, col) in enumerate([(0,0),(0,1),(1,0),(1,1),(2,0),(2,1)], start=1):
    cell = img.crop((col*cw, row*ch, (col+1)*cw, (row+1)*ch))
    out = f"{output_dir}/{seg_name}_cell{idx}.png"
    cell.save(out)
    print(f"  cell{{idx}}: {{out}}")
print(f"原图: {{w}}x{{h}}, cell: {{cw}}x{{ch}}")
'''
    return script


if __name__ == "__main__":
    import sys
    seg = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    print(f"=== 测试段{seg} 6 cell workflow 构建 ===")
    nodes = build_segment_workflow_6cell(seg, product_id="shoes")
    print(f"节点数: {len(nodes)}")
    print(f"AddGuide 节点: {[k for k, v in nodes.items() if 'MiniMaxH3AddGuide' in v.get('class_type', '')]}")