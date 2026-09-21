#!/usr/bin/env python3
"""FL2VA + AddGuide 中间锚 (5 个中间锚) —— 涂抹口红 8s 测试视频成功配置

链路: MiniMaxH3ImageToVideo (first_frame + last_frame)
     + 5 个 MiniMaxH3AddGuide 中间锚 (frame_idx = 24/72/120/144/168)
     + 官方六段式 YAML prompt (Ref2VA keyframe completion 格式)

来源：用户指定宫格图 `图片_2026-09-21_10-49-11-555.png`（3 行 × 2 列）
切片 cell1-cell6（384×448），首帧 cell1 + 5 AddGuide 用 cell2-6 + 尾帧 cell6

实测：11 分 52 秒出片，1.76 MB，人物真实度显著提升（无崩坏，无双口红）
"""
import json

H3P = "/root/autodl-tmp/h3p"

prompt = """subject_definitions:
<Subject 1> is the fictional young Asian female model from <Picture 1>, wearing a cream beige cotton crew-neck T-shirt, with long straight black hair, round face, single-eyelid almond eyes, small pointed nose, and thin natural-pink lips. Preserve her facial identity, hairstyle, body proportions, and outfit exactly across the entire clip.
<Subject 2> is the gold square lipstick from <Picture 1>, a single lipstick tube (rectangular flat gold metallic box) with a red slanted bullet protruding from the top. Preserve the exact product shape, gold color, and red bullet. Only one lipstick ever appears; no second lipstick, no mirror, no extra product appears in any frame.

summary:
[keyframe completion] The target video shows <Subject 1> applying <Subject 2> in a continuous 8-second one-take shot. The video begins with her bare-lipped smile holding the lipstick, transitions through opening the cap, gliding the bullet across her lower lip, gliding across her upper lip, pressing her lips together, and ends with her full red-lip smile holding the lipstick, mirroring the start pose.

retention_analysis:
<Subject 1> (appears in the entire clip): fully_preserved - retain her facial identity (round face, single-eyelid almond eyes, small pointed nose, thin natural-pink lips), hairstyle (long straight black hair with side part), body proportions, and cream beige cotton crew-neck T-shirt throughout. Skin texture has natural pores, no plastic-looking, no airbrushed effect.
<Subject 2> (appears in frames 0-6s and 7-8s): fully_preserved - retain exact gold square shape, red slanted bullet. Only ONE lipstick ever visible. Never morphs, never duplicates, never shows a mirror reflection of the product.
<Picture 1> (scene anchor): fully_preserved - warm beige home interior background, soft natural window light from the left, no other objects on the table.

detailed_description:
Live-action authentic TikTok/Reels UGC style with realistic skin texture, smartphone camera, stable tripod-steady framing, no camera shake, realistic shadows, no plastic-looking skin, no oversaturated colors, no cinematic commercial look. One continuous shot, no cuts.

[Shot 1] At 00:00.000, a chest-up medium close-up opens on <Subject 1> holding <Subject 2> (the gold lipstick) between her right thumb and index finger near her chin. She looks directly at the camera with a gentle smile, bare-lipped, lips in natural pink. The camera holds this composition stable. The cream beige cotton crew-neck T-shirt is visible on her shoulders. Soft natural window light from the left casts a gentle shadow on the right side of her face. Warm beige home interior background, slightly out of focus.

Between 00:00.500 and 00:01.500, <Subject 1> looks down at the lipstick in her hands and gently closes her eyes. Both hands come up: left hand cradles the gold base, right hand grips the gold cap. Her thumb pushes the cap upward, sliding it off the tube to expose the red slanted bullet. Her expression is calm and focused, eyes softly closed. The cap is held in the right hand after removal, the lipstick tube with red bullet held in the left hand.

[Shot 2] At 00:01.500, cut to <Subject 1> lifting her head back up, eyes opening, looking at the lipstick in her left hand. The right hand (holding the cap) moves down and out of frame. She raises the lipstick (held in left hand between thumb and index finger) toward her face.

Between 00:02.000 and 00:03.000, she tilts her head slightly up, parts her lips gently (mouth slightly open). She brings the lipstick toward her lower lip. The red slanted bullet makes contact with her lower lip on the left side of her lower lip. She begins to glide the bullet slowly from the left side of her lower lip to the right side in a smooth continuous motion.

[Shot 3] At 00:03.000, the bullet is gliding across the right side of her lower lip. She continues the glide motion to complete coverage of her lower lip with the red color. Her upper lip is still natural pink at this point. Her eyes are focused down at her lips during the application.

Between 00:04.000 and 00:05.000, she moves the lipstick up to her upper lip. The bullet now touches the left side of her upper lip. She begins to glide the bullet slowly from the left side of her upper lip to the right side. Her lower lip is now fully red.

[Shot 4] At 00:05.000, a tight close-up shows the bullet completing the glide across the right side of her upper lip. Her upper lip is now also fully red, matching the lower lip. She lowers the lipstick away from her face, out of frame. Both her lips are now vivid red.

Between 00:05.500 and 00:06.500, she closes her eyes and tilts her head slightly upward (chin up). She presses her lips together firmly (pursed lip expression) to set the color evenly. Her expression is serene and satisfied, eyes softly closed, lips pressed together.

[Shot 5] At 00:06.500, still in close-up, she relaxes her mouth back to a natural position, lips slightly parted. Both lips are vivid red. She tilts her head back down to a neutral position and slowly opens her eyes, looking toward the camera.

Between 00:07.000 and 00:08.000, the camera pulls back slightly to the original chest-up framing. <Subject 1> is now holding <Subject 2> (the lipstick) in her right hand near her chin again, mirroring the start pose. She looks directly at the camera with a soft satisfied smile revealing her vivid red lips. The composition matches <Picture 1> in framing, posture, and hand position, but with full red lips instead of bare lips.

No additional people, no other products, no mirrors, no reflections, no cups, no bottles, no bags, no accessories appear in the scene at any point. The single lipstick stays in one hand throughout. The cream beige cotton crew-neck T-shirt stays the same color and shape throughout.

overall_soundscape:
Quiet indoor room ambience, soft natural light hum.

non_diegetic_music:
N/A"""

nodes = {
    "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "minimax_h3_fl2va_pruned_int8_convrot.safetensors", "weight_dtype": "default"}},
    "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors", "type": "minimax", "device": "default"}},
    "3": {"class_type": "VAELoader", "inputs": {"vae_name": "minimax_h3_video_vae_fp16.safetensors"}},
    "4": {"class_type": "VAELoader", "inputs": {"vae_name": "minimax_h3_audio_vae_fp32.safetensors"}},
    "5": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["1", 0], "lora_name": "minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors", "strength_model": 1.0}},
    "6": {"class_type": "ResolutionSelector", "inputs": {"resolution": "9:16 (Portrait Widescreen)"}},
    "8":  {"class_type": "LoadImage", "inputs": {"image": "fl2va_cell1.png"}},
    "9":  {"class_type": "LoadImage", "inputs": {"image": "fl2va_cell6.png"}},
    "20": {"class_type": "LoadImage", "inputs": {"image": "fl2va_cell2.png"}},
    "22": {"class_type": "LoadImage", "inputs": {"image": "fl2va_cell3.png"}},
    "24": {"class_type": "LoadImage", "inputs": {"image": "fl2va_cell4.png"}},
    "26": {"class_type": "LoadImage", "inputs": {"image": "fl2va_cell5.png"}},
    "28": {"class_type": "LoadImage", "inputs": {"image": "fl2va_cell6.png"}},
    "7": {"class_type": "MiniMaxH3ImageToVideo", "inputs": {
        "clip": ["2", 0], "vae": ["3", 0],
        "prompt": prompt, "width": 768, "height": 1344, "length": 192,
        "first_frame": ["8", 0],
        "last_frame": ["9", 0]}},
    "10": {"class_type": "RandomNoise", "inputs": {"noise_seed": 42}},
    "11": {"class_type": "BasicGuider", "inputs": {"model": ["5", 0], "conditioning": ["7", 0]}},
    "12": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "res_multistep"}},
    "13": {"class_type": "BasicScheduler", "inputs": {"scheduler": "simple", "steps": 8, "denoise": 1.0, "model": ["5", 0]}},
    "21": {"class_type": "MiniMaxH3AddGuide", "inputs": {"positive": ["7", 0], "latent": ["7", 1], "image": ["20", 0], "vae": ["3", 0], "frame_idx": 24}},
    "23": {"class_type": "MiniMaxH3AddGuide", "inputs": {"positive": ["21", 0], "latent": ["7", 1], "image": ["22", 0], "vae": ["3", 0], "frame_idx": 72}},
    "25": {"class_type": "MiniMaxH3AddGuide", "inputs": {"positive": ["23", 0], "latent": ["7", 1], "image": ["24", 0], "vae": ["3", 0], "frame_idx": 120}},
    "27": {"class_type": "MiniMaxH3AddGuide", "inputs": {"positive": ["25", 0], "latent": ["7", 1], "image": ["26", 0], "vae": ["3", 0], "frame_idx": 144}},
    "29": {"class_type": "MiniMaxH3AddGuide", "inputs": {"positive": ["27", 0], "latent": ["7", 1], "image": ["28", 0], "vae": ["3", 0], "frame_idx": 168}},
    "30": {"class_type": "BasicGuider", "inputs": {"model": ["5", 0], "conditioning": ["29", 0]}},
    "31": {"class_type": "SamplerCustomAdvanced", "inputs": {"noise": ["10", 0], "guider": ["30", 0], "sampler": ["12", 0], "sigmas": ["13", 0], "latent_image": ["7", 1]}},
    "32": {"class_type": "VAEDecode", "inputs": {"samples": ["31", 0], "vae": ["3", 0]}},
    "33": {"class_type": "VAEDecodeAudio", "inputs": {"samples": ["31", 0], "vae": ["4", 0]}},
    "34": {"class_type": "CreateVideo", "inputs": {"images": ["32", 0], "fps": 24, "audio": ["33", 0]}},
    "35": {"class_type": "SaveVideo", "inputs": {"video": ["34", 0], "filename_prefix": "red_apply_fl2va_5guides", "format": "auto", "codec": "auto"}}
}

out_path = f"{H3P}/out/test_apply/fl2va_5guides_workflow.json"
import os; os.makedirs(os.path.dirname(out_path), exist_ok=True)
json.dump(nodes, open(out_path, "w"), indent=2)
print(f"saved: {out_path}")
print(f"prompt 长度: {len(prompt)} chars")