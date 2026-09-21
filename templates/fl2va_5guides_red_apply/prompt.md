# FL2VA + 5 AddGuide 官方六段式 Prompt（涂抹口红示例）

来源：用户指定宫格图 `图片_2026-09-21_10-49-11-555.png`（3 行 × 2 列），
切片成 cell1-cell6（384×448），首帧用 cell1，5 个 AddGuide 中间锚用 cell2-6，
尾帧用 cell6（镜像 cell1 但满红唇）。

## 渲染配置

| 参数 | 值 |
|---|---|
| UNET | `minimax_h3_fl2va_pruned_int8_convrot.safetensors` |
| LoRA | `minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors` (strength 1.0) |
| VAE (video) | `minimax_h3_video_vae_fp16.safetensors` |
| VAE (audio) | `minimax_h3_audio_vae_fp32.safetensors` |
| CLIP | `qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors` |
| Sampler | `res_multistep` |
| Scheduler | `simple` |
| Steps | 8 |
| Resolution | `9:16 (Portrait Widescreen)` (768×1344) |
| Length | 192 帧 (8 秒) |
| Seed | 42 |

## AddGuide 中间锚点

| frame_idx | 时间 | cell | 动作 |
|---|---|---|---|
| 0 | 0s | cell1 (首帧) | 微笑抬眼持口红（裸唇） |
| 24 | 1s | cell2 | 低头闭眼拧金盖 |
| 72 | 3s | cell3 | 膏体贴下唇（开始涂抹） |
| 120 | 5s | cell4 | 膏体贴上唇（特写） |
| 144 | 6s | cell5 | 闭眼抿嘴仰头（设色） |
| 168 | 7s | cell6 | 满红唇抬眼 |
| 191 | 8s | cell6 (尾帧) | 满红唇微笑持口红（镜像 cell1） |

## 渲染实测

| 指标 | 值 |
|---|---|
| 出片时长 | 11 分 52 秒 |
| 文件大小 | 1.76 MB |
| 分辨率 | 768×1344 |
| 帧率 | 24 fps |

## 视觉验收（看视频）

- 起始帧：cell1 镜像（裸唇+持口红+抬眼微笑）
- sec3（3s 锚点）：膏体贴下唇，下唇部分红，上唇自然色（涂抹顺序对）
- 末帧：cell1 镜像（满红唇+持口红+抬眼微笑）
- 人物真实度：显著提升（眼型/脸型/鼻/发型/服装/表情全部一致）
- 崩坏：无（无双口红/无脸变形/无涂抹顺序错乱）

## 官方六段式 prompt（节选，完整版见 build_workflow.py）

```yaml
subject_definitions:
<Subject 1> is the fictional young Asian female model from <Picture 1>...
<Subject 2> is the gold square lipstick from <Picture 1>...

summary:
[keyframe completion] The target video shows <Subject 1> applying <Subject 2>...

retention_analysis:
<Subject 1> (appears in the entire clip): fully_preserved - retain her facial identity...
<Subject 2> (appears in frames 0-6s and 7-8s): fully_preserved - retain exact gold square shape...

detailed_description:
Live-action authentic TikTok/Reels UGC style...
[Shot 1] At 00:00.000, a chest-up medium close-up opens on...
[Shot 2] At 00:01.500, cut to <Subject 1> lifting her head back up...
[Shot 3] At 00:03.000, the bullet is gliding across the right side of her lower lip...
[Shot 4] At 00:05.000, a tight close-up shows the bullet completing...
[Shot 5] At 00:06.500, still in close-up, she relaxes her mouth back...
Between 00:07.000 and 00:08.000, the camera pulls back slightly...

overall_soundscape:
Quiet indoor room ambience, soft natural light hum.

non_diegetic_music:
N/A
```

## 与之前 R2V 方案对比

| 链路 | 出片 | 真实度 | 涂抹顺序 | 产品崩坏 |
|---|---|---|---|---|
| R2V (散文 prompt) | 5:22 | ⭐ | ❌ | ✅ |
| R2V (官方六段式) | 5:32 | ⭐⭐ | ❌ | ❌（双口红）|
| **FL2VA + 5 AddGuide** | **11:52** | **⭐⭐⭐⭐⭐** | **✅** | **✅** |

## 后续改进方向

1. 把该模板接入 orchestrator 的"一键生成"链路（前端提交按钮 → 后端生成 → 视频）
2. 适配其他品类（粉底/眼影/腮红）的 prompt 改写
3. 帧数可调（4-15s 范围）
4. 9:16 是默认，可改 1:1 / 16:9
