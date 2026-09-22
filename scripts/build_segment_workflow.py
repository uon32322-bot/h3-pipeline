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
import json, os

H3P = "/root/autodl-tmp/h3p"


# === 通用 prompt (适合所有 5 段, 段间差异由 cell 内容决定) ===
def build_segment_prompt(seg_idx: int, product_info: dict = None) -> str:
    """生成单段 H3 prompt (6 cell 宫格图配套, 含主体锁定)"""
    return f"""subject_definitions:
<Subject 1> is the fictional young Asian male model from <Picture 1> (the reference image), round face, single-eyelid almond eyes, small pointed nose, short black hair, athletic build, wearing a white crew-neck T-shirt and dark shorts. Preserve his facial identity, hairstyle, body proportions, and outfit exactly across the entire clip.
<Subject 2> is the men's lightweight breathable running shoe from <Picture 1>, a single pair of grey-blue feather-knit mesh running shoes with white EVA midsole and black rubber outsole. Preserve the exact product shape, color, and details. Only ONE pair ever appears; no second pair, no reflection, no extra product.

summary:
[keyframe completion] Segment {seg_idx}/5 of 5-segment 40-second video. The target video shows <Subject 1> demonstrating <Subject 2> (the running shoes) in a continuous 8-second one-take shot. The video begins with a product display pose, transitions through the key feature demonstration, and ends with a confirmation pose mirroring the start.

retention_analysis:
<Subject 1> (appears in the entire clip): fully_preserved - retain his facial identity (round face, single-eyelid almond eyes, small pointed nose), short black hair, athletic build, white crew-neck T-shirt, dark shorts throughout. Skin texture has natural pores, no plastic-looking, no airbrushed effect.
<Subject 2> (appears in all 6 panels of <Picture 1>): fully_preserved - retain the exact grey-blue feather-knit mesh upper, white EVA midsole, black rubber outsole. The product is a running shoe, NOT a sneaker, NOT a slipper, NOT a boot. Never morphs, never duplicates, never shows mirror reflection.
<Picture 1> (scene anchor): fully_preserved - modern living room background, soft natural light.

detailed_description:
Live-action authentic TikTok/Douyin UGC style with realistic skin texture, smartphone camera, stable tripod-steady framing, no camera shake, realistic shadows, no plastic-looking skin, no airbrushed effect, no makeup overlay, no color grading. One continuous shot, no cuts.

[Shot 1] At 00:00.000, a chest-up medium close-up opens on <Subject 1> standing in a modern living room. He holds <Subject 2> (the running shoes) in his right hand near his waist. He looks directly at the camera with a confident smile. The white crew-neck T-shirt and dark shorts are clearly visible. Warm natural light from the left casts a gentle shadow on the right side of his face. Modern living room background, slightly out of focus. The single pair of grey-blue running shoes is visible, held naturally.

Between 00:00.500 and 00:02.500, <Subject 1> gently lifts the shoes to chest level, displaying them to the camera. His right hand rotates the shoes slowly to show the side profile, revealing the white EVA midsole and the mesh texture of the upper. His expression is calm and confident. The shoes stay in the same position relative to the camera frame.

[Shot 2] At 00:02.500, the camera cuts to a close-up on <Subject 1>'s hands placing the shoes on a clean surface. His right hand brings the shoes down gently onto the floor. His left hand stays near the shoes, fingers extended, not touching yet. The shoes are now visible in full, showing both the side profile and the laces. Soft natural light highlights the mesh texture.

Between 00:03.500 and 00:05.000, <Subject 1>'s right hand rotates the shoes 180 degrees to show the back heel and the outsole. His left hand gestures to the outsole, highlighting the black rubber pattern. The shoes stay centered in the frame.

[Shot 3] At 00:05.000, the camera angle shifts to show <Subject 1> kneeling down to put on the shoes. His right foot slides into the right shoe, then his left foot into the left shoe. His hands pull the heel tab up to secure the fit. His expression is focused, looking down at his feet.

Between 00:05.500 and 00:07.000, <Subject 1> stands up and starts walking in place, demonstrating the shoes on his feet. The camera follows his lower body, showing the shoes from the front as he walks. His steps are light and natural, showcasing the cushioning of the EVA midsole.

[Shot 4] At 00:07.000, a side-angle view shows <Subject 1> walking across the living room, the shoes clearly visible on his feet. His arms swing naturally at his sides. The grey-blue mesh upper and white midsole are visible from the side. The camera tracks his movement smoothly.

Between 00:07.500 and 00:08.000, <Subject 1> stops walking and stands facing the camera with a confident smile, his feet shoulder-width apart, the shoes fully visible. His hands return to his sides. The composition mirrors <Picture 1> in framing, posture, and hand position.

No additional people, no other products, no mirrors, no reflections, no cups, no bottles, no bags, no accessories appear in the scene at any point. The single pair of grey-blue running shoes stays consistent throughout. The white T-shirt and dark shorts stay the same color throughout. <Subject 1>'s face, body, and outfit stay consistent throughout.

overall_soundscape:
Quiet indoor room ambience, soft natural light hum.

non_diegetic_music:
N/A"""


def build_segment_workflow_6cell(segment_idx: int, product_id: str = "shoes",
                                    product_name: str = "running_shoes",
                                    product_info: dict = None):
    """构建单段 H3 workflow (6 cell 宫格图 + 5 AddGuide)

    关键改进:
      - 6 cell (3 行 × 2 列) 替代 2 cell, 避免拉伸
      - 5 AddGuide 中间锚 (frame_idx 24/72/120/144/168) 替代 1 个
      - 每段 8s = 192 帧
    """
    seg_name = f"seg{segment_idx}"
    prompt = build_segment_prompt(segment_idx, product_info)

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
        "8":  {"class_type": "LoadImage", "inputs": {"image": f"{seg_name}_cell1.png"}},  # 首帧 (锚)
        "20": {"class_type": "LoadImage", "inputs": {"image": f"{seg_name}_cell2.png"}},
        "22": {"class_type": "LoadImage", "inputs": {"image": f"{seg_name}_cell3.png"}},
        "24": {"class_type": "LoadImage", "inputs": {"image": f"{seg_name}_cell4.png"}},
        "26": {"class_type": "LoadImage", "inputs": {"image": f"{seg_name}_cell5.png"}},
        "28": {"class_type": "LoadImage", "inputs": {"image": f"{seg_name}_cell6.png"}},  # 尾帧 (锚)
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
        # 5 AddGuide 中间锚 (核心改进)
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