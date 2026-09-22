#!/usr/bin/env python3
"""FL2VA + 1 AddGuide 中间锚 —— 段内 2 cell 宫格图 8s 测试视频配置

链路: MiniMaxH3ImageToVideo (first_frame + last_frame)
     + 1 个 MiniMaxH3AddGuide 中间锚 (frame_idx = 96, 中间位置)
     + 段内只有 2 cell, 中间锚 1 个就够

每段 8s = 192 帧
- 帧 0: 首帧 (cell 1)
- 帧 96: 中间锚 (cell 2 衍生)
- 帧 192: 尾帧 (cell 2)
"""
import json, os

H3P = "/root/autodl-tmp/h3p"

prompt = """subject_definitions:
<Subject 1> is a young East Asian male model, round face, single-eyelid almond eyes, small pointed nose, short black hair, athletic build, wearing a white crew-neck T-shirt and dark shorts. Preserve his facial identity, hairstyle, body proportions, and outfit exactly across the entire clip.
<Subject 2> is the men's lightweight breathable running shoe from <Picture 1> (the product image), a single pair of grey-blue feather-knit mesh running shoes with white EVA midsole and black rubber outsole. Preserve the exact product shape, color, and details. Only ONE pair ever appears; no second pair, no reflection.

summary:
[keyframe completion] The target video shows <Subject 1> demonstrating <Subject 2> (the running shoes) in a continuous 8-second one-take shot. The video begins with a product display pose, transitions through the key feature demonstration, and ends with a confirmation pose mirroring the start.

retention_analysis:
<Subject 1> (appears in the entire clip): fully_preserved - retain his facial identity, short black hair, athletic build, white T-shirt, and dark shorts throughout.
<Subject 2> (appears as the only product in product-visible shots): fully_preserved - retain the exact grey-blue feather-knit mesh upper, white EVA midsole, black rubber outsole. The product is a running shoe, NOT a sneaker, NOT a slipper, NOT a boot.
<Picture 1> (scene anchor): fully_preserved - modern living room background, natural light.

detailed_description:
Live-action authentic TikTok/Douyin style with realistic skin texture, smartphone camera, stable tripod-steady framing, no camera shake, realistic shadows, no plastic-looking skin. One continuous shot, no cuts.

[Shot 1] At 00:00.000, a medium shot opens on <Subject 1> standing in a modern living room. He holds <Subject 2> (the running shoes) in his right hand near his waist. He looks directly at the camera with a confident smile. The white T-shirt and dark shorts are clearly visible. Warm natural light from the left. Modern living room background, slightly out of focus.

Between 00:01.000 and 00:04.000, <Subject 1> transitions to demonstrating the shoe features. He bends down to put on the shoes, then stands up and starts walking in place, showing the breathable mesh and cushioned midsole in action. His posture is relaxed, movements natural.

[Shot 2] At 00:04.000, he stands facing the camera, showing the shoes on his feet from multiple angles. His hands gesture to the shoes highlighting the feather-knit upper and white midsole. His expression is confident and satisfied.

Between 00:06.000 and 00:08.000, he returns to the initial pose, holding one shoe in his right hand, smiling confidently at the camera, mirroring the start position. The composition matches <Picture 1> in framing.

No additional people, no other products, no mirrors, no reflections appear at any point. The single pair of grey-blue running shoes stays consistent throughout. The white T-shirt and dark shorts stay the same color throughout.

overall_soundscape:
Quiet indoor room ambience.

non_diegetic_music:
N/A"""


def build_segment_workflow(segment_idx: int, product_id: str = "shoes"):
    """构建单段 H3 workflow (2 cell 宫格图 → 1 AddGuide)"""
    seg_name = f"seg{segment_idx}"
    nodes = {
        # 模型加载
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "minimax_h3_fl2va_pruned_int8_convrot.safetensors", "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors", "type": "minimax", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": "minimax_h3_video_vae_fp16.safetensors"}},
        "4": {"class_type": "VAELoader", "inputs": {"vae_name": "minimax_h3_audio_vae_fp32.safetensors"}},
        "5": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["1", 0], "lora_name": "minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors", "strength_model": 1.0}},
        "6": {"class_type": "ResolutionSelector", "inputs": {"resolution": "9:16 (Portrait Widescreen)"}},
        # 加载段内 2 cell
        "8":  {"class_type": "LoadImage", "inputs": {"image": f"{seg_name}_cell1.png"}},
        "9":  {"class_type": "LoadImage", "inputs": {"image": f"{seg_name}_cell2.png"}},
        # H3 Image-to-Video (首帧 + 尾帧)
        "7":  {"class_type": "MiniMaxH3ImageToVideo", "inputs": {
            "clip": ["2", 0], "vae": ["3", 0],
            "prompt": prompt, "width": 768, "height": 1344, "length": 192,
            "first_frame": ["8", 0], "last_frame": ["9", 0]}},
        "10": {"class_type": "RandomNoise", "inputs": {"noise_seed": 42 + segment_idx * 100}},
        "11": {"class_type": "BasicGuider", "inputs": {"model": ["5", 0], "conditioning": ["7", 0]}},
        "12": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "res_multistep"}},
        "13": {"class_type": "BasicScheduler", "inputs": {"scheduler": "simple", "steps": 8, "denoise": 1.0, "model": ["5", 0]}},
        # **关键简化**: 段内只有 1 个 AddGuide (frame_idx=96)
        "21": {"class_type": "MiniMaxH3AddGuide", "inputs": {
            "positive": ["7", 0], "latent": ["7", 1], "image": ["9", 0],
            "vae": ["3", 0], "frame_idx": 96}},
        "30": {"class_type": "BasicGuider", "inputs": {"model": ["5", 0], "conditioning": ["21", 0]}},
        "31": {"class_type": "SamplerCustomAdvanced", "inputs": {
            "noise": ["10", 0], "guider": ["30", 0],
            "sampler": ["12", 0], "sigmas": ["13", 0], "latent_image": ["7", 1]}},
        "32": {"class_type": "VAEDecode", "inputs": {"samples": ["31", 0], "vae": ["3", 0]}},
        "33": {"class_type": "VAEDecodeAudio", "inputs": {"samples": ["31", 0], "vae": ["4", 0]}},
        "34": {"class_type": "CreateVideo", "inputs": {"images": ["32", 0], "fps": 24, "audio": ["33", 0]}},
        "35": {"class_type": "SaveVideo", "inputs": {
            "video": ["34", 0], "filename_prefix": f"{product_id}_{seg_name}",
            "format": "auto", "codec": "auto"}},
    }
    return nodes


# === 跑段 2 验证 ===
if __name__ == "__main__":
    import subprocess

    H = "root@connect.nmb2.seetacloud.com"
    env = os.environ.copy()
    env['SSH_ASKPASS'] = os.path.expanduser('~/.ssh/askpass_nmb2.sh')
    env['SSH_ASKPASS_REQUIRE'] = 'force'
    env['LC_ALL'] = 'C.UTF-8'
    sopts = ['-o','StrictHostKeyChecking=no','-o','UserKnownHostsFile=/dev/null','-o','ConnectTimeout=10']

    # 切分 seg2 宫格图 (左 cell = cell1, 右 cell = cell2)
    print("=== 切分 seg2 宫格图 ===")
    # 2 宫格 1 行 2 列, 用 PIL 切
    from PIL import Image
    img = Image.open("/Users/admin/Desktop/新测试 0921/shoe_5seg_v2/seg2_grid.png")
    w, h = img.size
    print(f"原图: {w}x{h}")
    cell1 = img.crop((0, 0, w // 2, h))
    cell2 = img.crop((w // 2, 0, w, h))
    cell1.save("/tmp/seg2_cell1.png")
    cell2.save("/tmp/seg2_cell2.png")
    print(f"cell1: {cell1.size}, cell2: {cell2.size}")

    # 上传到 H3P input
    subprocess.run(['scp']+sopts+['-P','36229', '/tmp/seg2_cell1.png',
        f'{H}:{H3P}/input/seg2_cell1.png'], capture_output=True, env=env)
    subprocess.run(['scp']+sopts+['-P','36229', '/tmp/seg2_cell2.png',
        f'{H}:{H3P}/input/seg2_cell2.png'], capture_output=True, env=env)
    print("已上传 cell1 + cell2 到 H3P/input")

    # 生成 workflow (直接存 nodes dict, 不嵌套 "nodes" 键)
    nodes = build_segment_workflow(2, product_id="shoes")
    wf_path = "/tmp/seg2_workflow.json"
    json.dump(nodes, open(wf_path, "w"), indent=2)
    print(f"workflow 生成: {wf_path} ({len(nodes)} 节点)")

    # 上传 workflow
    subprocess.run(['scp']+sopts+['-P','36229', wf_path,
        f'{H}:{H3P}/out/test_apply/seg2_workflow.json'], capture_output=True, env=env)
    print(f"workflow 上传: {H3P}/out/test_apply/seg2_workflow.json")