# REPORT4 — Ref2VA 最快落地路径选型

> 检索条件（同前）：成熟稳定 · 质量好且有样片 · 5s 片进 5 分钟 · 给地址  
> 检索时间：2026-09-11



---

## 一、结论先行

**最快不是换实例，是在你现有 4090 服务器上补 1 个文件。**

盘点现有服务器（`connect.westc.seetacloud.com:39494`）：

| 组件                                           | 现状                                | Ref2VA 是否需要重下         |
| -------------------------------------------- | --------------------------------- | --------------------- |
| ComfyUI 程序 + 节点                              | ✅ 已装（含 Promptor / Extender / ASR） | ❌ 复用                  |
| text encoder `qwen3vl_32b_...nvfp4_awq`      | ✅ 已装                              | ❌ 复用（FL2VA/Ref2VA 共用） |
| video VAE fp16 + audio VAE fp32              | ✅ 已装                              | ❌ 复用                  |
| Turbo LoRA `minimax_h3_turbo_v4_step600_ema` | ✅ 已装                              | ⚠️ 可先试，见 §三           |
| **Ref2VA diffusion model**                   | ❌ **缺**                           | ✅ **只缺这一个**           |

磁盘：`/root/autodl-tmp` 250G / 可用 **210G** —— 装 19.5GB 权重毫无压力。

→ 迁移成本 = **下 1 个文件 + 建 1 个软链**，其余全复用。

---

## 二、现成云实例对照（"开机即用"排序）

| #     | 实例 / 镜像                         | Ref2VA 预装                               | 地址                                                                                | 备注                                                                                                                                                       |
| ----- | ------------------------------- | --------------------------------------- | --------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **A** | 优云智算 Compshare「MiniMax H3 Int8」 | ✅ **明确预装** `ref2va_pruned_int8_convrot` | <https://compshare.cn/images/cH5ZIckXuwee>                                        | 镜像标题即「ComfyUI MiniMax H3 音视频生成镜像（Ref2VA）」。90GB 镜像 / ComfyUI 0.30.0 / Supervisor 托管 / 自动监听 8188 / 累计部署 67 次・运行 457h。**推荐 48G 显存 + 96G 内存**（24G 需降分辨率或开卸载） |
| **B** | 仙宫云「MiniMax H3 导演台」社区镜像         | ✅ 含多参考图生视频（R2V）工作流                      | xiangongyun.com 搜「MiniMax H3 导演台」                                                 | 作者「AI搅拌手」。4 套工作流（单节点流 + 外接多参考流），保留示例素材可一键跑。推荐 4090 48G                                                                                                   |
| **C** | RunningHub 云端工作流                | ✅ 免部署                                   | runninghub.cn 搜作者「AI搅拌手」                                                          | 浏览器即用，注册送 1000 点。适合"只想看效果、不想碰环境"                                                                                                                         |
| **D** | RunComfy 工作流（ref2va + Turbo 4步） | ✅ 成品工作流                                 | runcomfy.com/comfyui-workflows/minimax-h3-comfyui-4-step-reference-to-video-audio | 图已接好线，模型齐备                                                                                                                                               |
| **E** | AutoDL 社区镜像                     | ⚠️ 需逐个确认是否含 ref2va                      | autodl.com 搜 MiniMax / ComfyUI                                                    | 5090 32G ≈3 元/小时，最便宜，但要自己核对镜像内容                                                                                                                          |

**对 A 的评价**：这是唯一一个"平台方镜像 + 明确写了 Ref2VA 预装 + 有运行时长背书"的组合，四项条件全中。短板是推荐 48G 显存——它的 int8 pruned 权重（19.5GB）在 24G 卡上要开模型卸载，首次冷加载数十 GB 需数分钟。

---

## 三、权重与加速件清单（ComfyUI 本地/自建）

### 基础权重

| 文件                                                            | 大小       | 放哪                         | 来源                              |
| ------------------------------------------------------------- | -------- | -------------------------- | ------------------------------- |
| `minimax_h3_ref2va_pruned_int8_convrot.safetensors`           | ~19.5 GB | `models/diffusion_models/` | Comfy-Org/MiniMax-H3            |
| *（省显存备选）* `minimax_h3_ref2va_pruned_int4_convrot.safetensors` | 11.3 GB  | 同上                         | Merserk/MiniMax-H3-INT4-ConvRot |

> 国内加速：`HF_ENDPOINT=https://hf-mirror.com`，或走 ModelScope 的 Comfy-Org/MiniMax-H3。

### 加速 LoRA —— **必须用 Ref2VA 版本**

| 文件                                                                | 步数 / 分辨率             | 来源                                          |
| ----------------------------------------------------------------- | -------------------- | ------------------------------------------- |
| `minimax_h3_ref2v_turbo_8step_v1.0_768p_comfyui_bf16.safetensors` | 8 步 / 768p（1344×768） | lightx2v/Minimax-h3-Turbo（2026-09-04 发布，最新） |
| `minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors`      | 4 步 / 544p           | 同仓（Ref2VA 第一个蒸馏版，仍是 v0.1）                   |
| `minimax_h3_turbo_v4_step600_ema.safetensors`                     | 6–8 步 / 混合           | larryvrh —— **你服务器上已有**，社区称覆盖 Ref2V，可先试     |

### 工作流模板

- 官方 R2V 模板：`video_minimax_h3_r2v.json`（Comfy-Org/workflow_templates）
- 加速版：ModelTC/Minimax-H3-Turbo 仓内 `video_minimax_h3_ref2v_lightx2v_turbo.json`

---

## 四、速度实测数据（判断 5s 能否进 5 分钟）

| 配置                       | 分辨率 / 帧数        | 耗时                         |
| ------------------------ | --------------- | -------------------------- |
| 20 步，无 Turbo             | 960×544 / 73 帧  | **69 s**                   |
| 8 步 + Ref Turbo          | 960×544 / 73 帧  | **31 s**                   |
| 4 步 + Ref Turbo          | 960×544 / 73 帧  | **21 s**                   |
| 20 步 + FirstBlockCache   | 576×832 / 158 帧 | 216 s（对比 266 s，**-18.6%**） |
| 参考：8GB 卡 4 步 @736×416 5s | —               | 5～6.5 分钟（显存瓶颈，非算法瓶颈）       |

**读法**：

- 4090 上 Ref2VA + 8 步，纯采样约 31s，加上加载/TE/VAE 解码，5s 片墙钟远低于 5 分钟 ✅
- 8GB 卡那行说明"慢"来自显存不足而非模型——**24G 卡不是瓶颈**
- FirstBlockCache 只在步数高（20 步级）时有意义；配 4 步 LoRA 基本无效（4 次判断没有命中机会）

**纠偏**：社区常说的 Turbo "5× 加速"只指**采样步数**（约 20→4）。模型加载、文本编码、视频/音频 VAE 解码都不缩水，所以墙钟不会是 5 倍。

---

## 五、四个坑（已有人踩过）

1. **LoRA 不能混用**。FL2VA 的 Turbo LoRA 套在 Ref2VA 权重上会静默降质或不生效；两边是不同权重。
2. **Ref2VA ≠ FL2VA**。权重不同，不能互相替代；任务也不同（Ref2VA 才是唯一支持图像/视频/音频参考条件的）。
3. **Hybrid 是画质路线，不是速度路线**。社区把两者合并成 `minimax_h3_hybrid_fl2va_ref2va_bXX-49`（4 个变体，各 ~21GB，smhfacct 仓）+ 需要装 `ComfyUI_MinimaxH3HybridLoader` 节点。做的是"用 FL2VA 的画质换参考能力"，下载量与工程复杂度都更高。
4. **别叠过多加速**。已记录的死路：w4a8 量化、步数过低导致爆音（`v4 LoRA + 6 步`出现过声音异常）。你机器上还要避开 Kitchen INT8 / FROST 后端（4090 = SM89 上失败，用 BF16 Triton）。

---

## 六、三条可选动作

| 选项        | 动作                                                              | 成本          | 适合                       |
| --------- | --------------------------------------------------------------- | ----------- | ------------------------ |
| **A（推荐）** | 现有服务器补 Ref2VA：下 19.5GB → 软链 → 跑 r2v 模板 → 直接用你的产品图/模特图做**参考生视频** | 1 次下载，≈0 迁移 | 想立刻拿到 Ref2VA 能力，且复用已有素材链 |
| **B**     | 起一台 Compshare「MiniMax H3 Int8」实例做对照                             | 按小时计费       | 想横向比"平台镜像 vs 自建"的速度与画质   |
| **C**     | 只评估，不动                                                          | 0           | 先看清楚再决定                  |

---

## 附：本次检索的关键地址

- Ref2VA 权重（ComfyUI 版）：<https://huggingface.co/Comfy-Org/MiniMax-H3>
- INT4/INT8/NVFP4 量化合集：<https://huggingface.co/Abiray/Minimax-H3-nvfp4-INT4-INT8-Convrot>
- INT4 ConvRot（低显存）：<https://huggingface.co/Merserk/MiniMax-H3-INT4-ConvRot>
- Ref2VA Turbo LoRA：<https://huggingface.co/lightx2v/Minimax-h3-Turbo>
- Turbo 代码与示例工作流：<https://github.com/ModelTC/Minimax-H3-Turbo>
- Hybrid 合并权重：<https://huggingface.co/smhfacct/Minimax-H3-fl2va-ref2va-hybrid-models>
- Hybrid 加载节点：<https://github.com/scottmudge/ComfyUI_MinimaxH3HybridLoader>
- 优云智算 Ref2VA 镜像：<https://compshare.cn/images/cH5ZIckXuwee>
- 官方 R2V 工作流模板：<https://github.com/Comfy-Org/workflow_templates>
