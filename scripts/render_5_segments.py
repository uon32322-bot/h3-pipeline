#!/usr/bin/env python3
"""通用 5 段 H3 渲染 + 拼接为 40s 完整视频

输入: 5 张段宫格图 (已生成在远端)
输出: 5 段 H3 mp4 + 1 个 40s 拼接视频

流程:
  1. 切分 5 张宫格图 (每张 → cell1 + cell2)
  2. 上传到 ComfyUI input
  3. 生成 5 段 workflow JSON (2 cell + 1 AddGuide)
  4. 串行提交 5 段到 ComfyUI (避免 GPU 冲突)
  5. ffmpeg concat 5 段为 40s 完整视频
"""
import json, os, subprocess, time, urllib.request
from pathlib import Path


H3P = "/root/autodl-tmp/h3p"
PROD_ID = "shoes"
COMFY = "http://127.0.0.1:6011"


def upload_grid_segments(grids_dir_local: str, remote_grids_dir: str = "/tmp/shoe_5seg"):
    """复制 5 段宫格图到远端"""
    for i in range(1, 6):
        local = f"{grids_dir_local}/seg{i}_grid.png"
        remote = f"{remote_grids_dir}/seg{i}_grid.png"
        # scp
        subprocess.run([
            "scp", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
            "-P", "36229", local,
            f"root@connect.nmb2.seetacloud.com:{remote}",
        ], check=True)
    print(f"✓ 上传 5 段宫格图到 {remote_grids_dir}")


def split_and_upload_cells(grids_dir_remote: str = "/tmp/shoe_5seg"):
    """切分每段宫格图为 cell1 + cell2, 上传到 H3P/input"""
    for i in range(1, 6):
        remote_grid = f"{grids_dir_remote}/seg{i}_grid.png"
        local_png = f"/tmp/seg{i}_cell1.png"
        local_png2 = f"/tmp/seg{i}_cell2.png"
        # 远端用 PIL 切
        subprocess.run([
            "ssh", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
            "-p", "36229", "root@connect.nmb2.seetacloud.com",
            f'''python3 -c "
from PIL import Image
img = Image.open('{remote_grid}')
w, h = img.size
img.crop((0, 0, w // 2, h)).save('/tmp/seg{i}_cell1.png')
img.crop((w // 2, 0, w, h)).save('/tmp/seg{i}_cell2.png')
print(f'  seg{i}: {{w}}x{{h}} -> 2 cells')
"'''
        ], check=True)
        # 上传到 H3P/input
        subprocess.run([
            "scp", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
            "-P", "36229", local_png,
            f"root@connect.nmb2.seetacloud.com:{H3P}/input/seg{i}_cell1.png",
        ], check=True)
        subprocess.run([
            "scp", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
            "-P", "36229", local_png2,
            f"root@connect.nmb2.seetacloud.com:{H3P}/input/seg{i}_cell2.png",
        ], check=True)
    print(f"✓ 切分并上传 10 张 cell 到 H3P/input")


def build_segment_workflow(segment_idx: int, product_id: str = "shoes"):
    """构建单段 H3 workflow (2 cell 宫格图 → 1 AddGuide)"""
    seg_name = f"seg{segment_idx}"
    # 通用 prompt (不同段可在后续优化)
    prompt = f"""subject_definitions:
<Subject 1> is a young East Asian male model, round face, single-eyelid almond eyes, small pointed nose, short black hair, athletic build, wearing a white crew-neck T-shirt and dark shorts. Preserve his facial identity, hairstyle, body proportions, and outfit exactly across the entire clip.
<Subject 2> is the men's lightweight breathable running shoe from <Picture 1>, a single pair of grey-blue feather-knit mesh running shoes with white EVA midsole and black rubber outsole. Preserve the exact product shape, color, and details. Only ONE pair ever appears.

summary:
[keyframe completion] The target video shows <Subject 1> demonstrating <Subject 2> (the running shoes) in a continuous 8-second one-take shot for segment {segment_idx} of a 5-segment video.

retention_analysis:
<Subject 1>: fully_preserved - retain facial identity, short black hair, athletic build, white T-shirt, dark shorts.
<Subject 2>: fully_preserved - retain grey-blue mesh, white EVA midsole, black rubber outsole. NOT a sneaker, NOT a slipper, NOT a boot.
<Picture 1>: fully_preserved - modern living room, natural light.

detailed_description:
Live-action authentic TikTok style, smartphone camera, stable framing, no plastic-looking skin. One continuous shot.

[Shot 1] At 00:00.000, <Subject 1> stands in a modern living room holding <Subject 2>. He looks at the camera with a confident smile. White T-shirt and dark shorts visible. Warm natural light from the left.

Between 00:01.000 and 00:04.000, he transitions to a key action demonstrating the shoe features.

[Shot 2] At 00:04.000, he shows the shoes on his feet or highlights a key feature. His hands gesture to the shoes highlighting the feather-knit upper and white midsole.

Between 00:06.000 and 00:08.000, he returns to a confirmation pose matching the start, smiling at the camera.

No additional people, no other products, no mirrors. The single pair of grey-blue running shoes stays consistent.

overall_soundscape:
Quiet indoor room ambience.

non_diegetic_music:
N/A"""
    nodes = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "minimax_h3_fl2va_pruned_int8_convrot.safetensors", "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors", "type": "minimax", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": "minimax_h3_video_vae_fp16.safetensors"}},
        "4": {"class_type": "VAELoader", "inputs": {"vae_name": "minimax_h3_audio_vae_fp32.safetensors"}},
        "5": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["1", 0], "lora_name": "minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors", "strength_model": 1.0}},
        "6": {"class_type": "ResolutionSelector", "inputs": {"resolution": "9:16 (Portrait Widescreen)"}},
        "8":  {"class_type": "LoadImage", "inputs": {"image": f"{seg_name}_cell1.png"}},
        "9":  {"class_type": "LoadImage", "inputs": {"image": f"{seg_name}_cell2.png"}},
        "7":  {"class_type": "MiniMaxH3ImageToVideo", "inputs": {
            "clip": ["2", 0], "vae": ["3", 0],
            "prompt": prompt, "width": 768, "height": 1344, "length": 192,
            "first_frame": ["8", 0], "last_frame": ["9", 0]}},
        "10": {"class_type": "RandomNoise", "inputs": {"noise_seed": 42000 + segment_idx * 100}},
        "11": {"class_type": "BasicGuider", "inputs": {"model": ["5", 0], "conditioning": ["7", 0]}},
        "12": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "res_multistep"}},
        "13": {"class_type": "BasicScheduler", "inputs": {"scheduler": "simple", "steps": 8, "denoise": 1.0, "model": ["5", 0]}},
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


def submit_one_segment(segment_idx: int, timeout_sec: int = 900) -> str:
    """提交 1 段到 ComfyUI, 返回 prompt_id"""
    # 生成 workflow 文件 (直接存 nodes dict, 不嵌套)
    wf_path = f"/tmp/seg{segment_idx}_wf.json"
    nodes = build_segment_workflow(segment_idx, product_id=PROD_ID)
    json.dump(nodes, open(wf_path, "w"), indent=2)

    # 上传 workflow
    subprocess.run([
        "scp", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
        "-P", "36229", wf_path,
        f"root@connect.nmb2.seetacloud.com:{H3P}/out/test_apply/seg{segment_idx}_wf.json",
    ], check=True)

    # 远端提交
    submit_script = f'''
import json, urllib.request, time
with open("{H3P}/out/test_apply/seg{segment_idx}_wf.json") as f:
    nodes = json.load(f)
payload = {{"prompt": nodes}}
req = urllib.request.Request(
    "http://127.0.0.1:6011/prompt",
    data=json.dumps(payload).encode(),
    headers={{"Content-Type": "application/json"}},
    method="POST",
)
with urllib.request.urlopen(req, timeout=30) as r:
    resp = json.loads(r.read().decode())
print(resp.get("prompt_id", ""))
'''
    subprocess.run([
        "ssh", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
        "-p", "36229", "root@connect.nmb2.seetacloud.com",
        f'''cat > /tmp/sub_s{segment_idx}.py << 'EOF'
{submit_script}
EOF
/root/autodl-tmp/h3p/venv/bin/python /tmp/sub_s{segment_idx}.py > /tmp/sub_s{segment_idx}.out 2>&1 &
echo $! > /tmp/sub_s{segment_idx}.pid
sleep 2
cat /tmp/sub_s{segment_idx}.out
'''
    ], check=True)

    # 读 PID
    r = subprocess.run([
        "ssh", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
        "-p", "36229", "root@connect.nmb2.seetacloud.com", "cat /tmp/sub_s{segment_idx}.out"
    ], capture_output=True, text=True, timeout=10)
    prompt_id = r.stdout.strip()
    print(f"  段{segment_idx} prompt_id = {prompt_id}")
    return prompt_id


def wait_segment(prompt_id: str, segment_idx: int, timeout_sec: int = 900) -> str:
    """等段完成, 返回输出视频路径"""
    start = time.time()
    while time.time() - start < timeout_sec:
        r = subprocess.run([
            "ssh", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
            "-p", "36229", "root@connect.nmb2.seetacloud.com",
            f"curl -s -m 5 'http://127.0.0.1:6011/history/{prompt_id}'"
        ], capture_output=True, text=True, timeout=15)
        try:
            d = json.loads(r.stdout)
            if prompt_id in d:
                outputs = d[prompt_id].get("outputs", {})
                for nid, out in outputs.items():
                    for v in out.get("videos", []):
                        # 找视频
                        remote = f"{H3P}/output/{v.get('filename')}"
                        print(f"  ✓ 段{segment_idx} 完成 → {remote}")
                        return remote
        except Exception:
            pass
        elapsed = int(time.time() - start)
        if elapsed % 30 < 3:
            print(f"  段{segment_idx} 等待中 ({elapsed}s)...")
        time.sleep(5)
    raise RuntimeError(f"段{segment_idx} 超时 {timeout_sec}s")


# === 主流程 ===
def render_all_5_segments():
    """串行跑 5 段 H3, 每段独立"""
    print("=== 5 段 H3 渲染 (串行) ===")
    video_paths = []
    for seg in [1, 2, 3, 4, 5]:
        print(f"\n--- 段{seg} ---")
        pid = submit_one_segment(seg)
        vpath = wait_segment(pid, seg, timeout_sec=600)
        video_paths.append(vpath)
    return video_paths


def concat_5_videos(video_paths: list, output_path: str):
    """ffmpeg concat 5 段为 40s"""
    # 写 concat 列表
    list_path = "/tmp/concat_list.txt"
    with open(list_path, "w") as f:
        for vp in video_paths:
            f.write(f"file '{vp}'\n")
    # 拉回到本地
    subprocess.run([
        "scp", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
        "-P", "36229", list_path,
        f"root@connect.nmb2.seetacloud.com:/tmp/concat_list.txt",
    ], check=True)
    # 远端 concat
    subprocess.run([
        "ssh", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
        "-p", "36229", "root@connect.nmb2.seetacloud.com",
        f"ffmpeg -y -f concat -safe 0 -i /tmp/concat_list.txt -c copy {output_path}",
    ], check=True)
    print(f"✓ 拼接完成: {output_path}")


if __name__ == "__main__":
    # Step 1: 切分 + 上传 cell
    split_and_upload_cells("/tmp/shoe_5seg")

    # Step 2: 跑 5 段
    video_paths = render_all_5_segments()
    print(f"\n=== 5 段视频路径 ===")
    for i, v in enumerate(video_paths, 1):
        print(f"  段{i}: {v}")

    # Step 3: 拼接
    output = "/tmp/shoes_40s_final.mp4"
    concat_5_videos(video_paths, output)
    print(f"\n✅ 完整 40s 视频: {output}")