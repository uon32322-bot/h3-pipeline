# REPORT16 · 服务器模型与链路审计（nmb2 · RTX 3090）

> 日期：2026-09-17
> 触发：辉哥怀疑「之前的模型选错了」，要求核查服务器上模型是否适配部署方案，不对则重下替换，或与其他项目隔离
> 服务器：`ssh -p 36229 root@connect.nmb2.seetacloud.com`（原 4090 服务器已弃用）
> 判据来源：**官方模板自带的 Model Links + 官方 subgraph 节点图 + ComfyUI `/object_info` 运行态**（非推测）

---

## 0. 结论先行

| # | 结论 | 状态 |
|---|---|---|
| **1** | **四件核心权重全部正确，无需替换** | ✅ 已逐项核对官方清单 |
| **2** | **真正错的是「加载链路」**：我们脚本里有 4 个节点来自社区包，官方原生链路从未跑过 | ❌ 需整改 |
| **3** | **官方 8 步 Turbo LoRA 服务器上根本没有**（只有降秩变体 + 早期社区版） | ⚠️ 已下载 |
| **4** | GPU 是 **RTX 3090**，不是 4090 —— 指南里的耗时基准全部不适用 | ⚠️ 需重标定 |
| **5** | **系统盘 100% 满**（剩 172 MB），元凶是 17 GB 备份 tgz | ❌ 待确认清理 |
| **6** | 模型库与另一个 ComfyUI 实例（`aisiyi-comfyui`）**共用**，且共享实例正被其他项目占用 | ⚠️ 需隔离 |

> 一句话：**不是模型文件选错了，是「谁来加载、用哪个 LoRA、跑在哪条链路上」错了。**

---

## 1. 审计对象

| 项 | 实际 | 与方案的差距 |
|---|---|---|
| GPU | NVIDIA **RTX 3090** 24 GB（sm_86，驱动 595.71.05） | 方案写的是 4090 —— 显存同容量，**算力约为 4090 的 50–60%**，且无 Ada 的 FP8 通路 |
| ComfyUI | **0.33.0**（commit `f41564`） | ✅ 满足「≥ 0.30.0」 |
| 运行实例 | `/root/ComfyUI`，端口 6006，`--enable-manager` | 与他人共用，10:03 启动，当前有任务驻留显存 17.3 GB |
| torch | 2.14.0+cu130，arch 含 sm_86 | ✅ 可跑 |
| 数据盘 | `/root/autodl-tmp` 300 G，剩 **99 G** | ✅ 够 |
| 系统盘 | `/` 30 G，**剩 172 M（100%）** | ❌ 会导致下载/临时文件/日志写入失败 |

---

## 2. 四件核心权重：逐项核对（✅ 全部正确）

判据来自**官方 I2V 模板内嵌的 MarkdownNote#117「Model Links」**，不是我们自己的记忆。

| 目录 | 官方要求文件 | 服务器实际 | 结论 |
|---|---|---|---|
| `diffusion_models/` | `minimax_h3_fl2va_pruned_int8_convrot.safetensors` | 同名，20.97 GB | ✅ |
| `text_encoders/` | `qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors` | 同名，15.7 GB | ✅ |
| `vae/` | `minimax_h3_video_vae_fp16.safetensors` | 同名，4.9 GB | ✅ |
| `vae/` | `minimax_h3_audio_vae_fp32.safetensors` | 同名，578 MB | ✅ |

**运行态交叉验证**：`/proc/80234/fd` 显示该实例加载的正是这四个文件（路径 `/root/autodl-tmp/ComfyUI-models/...`）。
⇒ **权重这一层没有问题，重下这 41 GB 是纯浪费。**（`/root/ComfyUI/models` 与 `/root/autodl-tmp/ComfyUI-models` 是同一份存储。）

---

## 3. 决定性证据①：节点归属（`python_module`）

用 ComfyUI 自己的 `/object_info` 接口读每个节点的注册文件：

| 我们脚本用的节点 | 来自哪个模块 | 归属 |
|---|---|---|
| `MiniMaxH3ImageToVideo` | `comfy_extras.nodes_minimax_h3` | ✅ **官方核心** |
| `ResolutionSelector` | `comfy_extras.nodes_resolution` | ✅ 官方核心 |
| `BasicScheduler` / `SamplerCustomAdvanced` | `comfy_extras.nodes_custom_sampler` | ✅ 官方核心 |
| **`MiniMaxH3TurboLoRA`** | `custom_nodes.ComfyUI-MiniMax-H3-Turbo` | ❌ **社区包** |
| **`MiniMaxH3TurboSampler`** | `custom_nodes.ComfyUI-MiniMax-H3-Turbo` | ❌ **社区包** |
| **`H3SparseAttentionAdvanced`** | `custom_nodes.H3-Optimizations` | ❌ **社区包** |
| **`H3MemoryOptimization`** | `custom_nodes.H3-Optimizations` | ❌ **社区包** |

官方核心里的 H3 节点**只有 4 个**：`MiniMaxH3ImageToVideo` / `MiniMaxH3ReferenceToVideo` / `MiniMaxH3AddGuide` / `MiniMaxH3SigmaShift`。

**⇒ 我们调了半个月的 `--sparse-budget`（稀疏注意力档位）、内存优化，全是社区包 `H3-Optimizations` 的超参，不是官方行为。**
REPORT6 里「改稀疏档位 → 帧差 20–35」这个"真模型参数改动"的证据，实际证据强度要下调：它证明的是**社区包超参有效**，不能直接外推成官方模型的参数敏感性。

---

## 4. 决定性证据②：官方链路长什么样（从官方 subgraph 还原）

官方 `video_minimax_h3_i2v.json` 把主图封装在 subgraph「Image to Video (MiniMax H3)」里，展开后 21 个节点，确切连线：

```
UNETLoader(fl2va_pruned_int8_convrot) ──┐
                                        ├─> LoraLoaderModelOnly(8step LoRA, strength 1.0) ──┐
CLIPLoader(qwen3vl_32b_nvfp4_awq) ──┐   │                                                  │
VAELoader(video_vae_fp16) ──────┐   │   │                                                  │
VAELoader(audio_vae_fp32) ──┐   │   │   │                                                  │
                            │   │   │   │                                                  │
LoadImage(first_frame) ─────┼───┼───┼───┼──> MiniMaxH3ImageToVideo                          │
LoadImage(last_frame)  ─────┼───┼───┼───┘    ├─ positive(CONDITIONING) ──> BasicGuider ────┤
ComfyMathExpression(17k+5) ─┘   │   │        └─ LATENT ──────────────> SamplerCustomAdvanced
                                │   └──────────> (vae)                     ▲   ▲   ▲
                                └──────────────> (vae)                     │   │   └─ SIGMAS ← BasicScheduler(model, simple, steps)
                                                                           │   └──── SAMPLER ← KSamplerSelect(res_multistep)
                                                                           └──────── NOISE ← RandomNoise(seed)
SamplerCustomAdvanced ──> VAEDecode(video vae) ──┐
                      └─> VAEDecodeAudio(audio vae) ┴─> CreateVideo(24) ──> SaveVideo
```

**官方用的是 `LoraLoaderModelOnly`（核心节点）+ `KSamplerSelect(res_multistep)` + `BasicScheduler(simple)`**——没有任何 Turbo 专用节点、没有稀疏注意力节点、没有内存优化节点。

### 我们脚本 vs 官方（差异表）

| 环节 | 我们的 `test_fl2v.py` | 官方模板 | 影响 |
|---|---|---|---|
| LoRA 加载 | `MiniMaxH3TurboLoRA`（社区） | `LoraLoaderModelOnly`（核心） | 加载语义可能不同 |
| 内存优化 | `H3MemoryOptimization`（社区） | 无 | 可能改变精度路径 |
| 稀疏注意力 | `H3SparseAttentionAdvanced`（社区） | 无 | **多了一个官方不存在的变量** |
| 采样器 | `MiniMaxH3TurboSampler`（社区） | `KSamplerSelect=res_multistep` | 采样行为可能不同 |
| Turbo LoRA 文件 | `minimax_h3_turbo_v4_step600_ema`（592 MB 早期社区版） | `minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16` | **质量基线完全不同** |
| 首尾帧 / 帧数公式 / 分辨率 | ✅ 一致 | ✅ | — |

---

## 5. 决定性证据③：官方 8 步 LoRA 的降秩变体

服务器 `loras/` 里与 8 步相关的只有：

| 文件 | 大小 | 判定 |
|---|---|---|
| `minimax_h3_fl2v_turbo_8step_v1.0_768p_comfyui_resized_avg_rank_64_bf16.safetensors` | 927 MB | ⚠️ **降秩变体**（名字自述 `resized_avg_rank_64`） |
| `minimax_h3_turbo_v4_step600_ema_pruned_comfyui.safetensors` | 592 MB | ⚠️ 早期社区版（运行时实际挂的就是它） |

**本次下载的官方文件 metadata 已解析**（`/root/autodl-tmp/h3p/models/loras/`）：

```
training_rank : 128                       ← 官方是 rank 128
base_model    : Comfy-Org/MiniMax-H3 minimax_h3_fl2va_bf16.safetensors
target_format : ComfyUI generic LoRA
training_scale: 0.0625
tensors       : 624
```

⇒ **降秩变体把 rank 从 128 压到 64，是官方 LoRA 的有损近似。** 用它跑出来的画质/动态，与官方 8 步不是同一件事——这正是"感觉哪里不对"的来源之一。

官方下载地址（模板自述，非我编造）：
`https://huggingface.co/lightx2v/Minimax-h3-Turbo/resolve/main/minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors`
（HF 直连被墙；**已改用 `hf-mirror.com` 镜像**，HTTP 200，1,956,193,000 字节）

---

## 6. 决定性证据④：Sage Attention 其实从未接上

- 官方提速路径 = **KJNodes 的 `Patch Sage Attention KJ`**。
- 服务器 `custom_nodes/` 里 **没有 ComfyUI-KJNodes**（`NO-KJNODES`）。
- 我们脚本里那个 `SPARSE_BACKEND="BF16 Triton"` 是社区的**稀疏注意力**，与 Sage Attention 是两件事。

⇒ REPORT14 里"官方 8 步 Turbo + Sage Attention ≈ 2× 提速 → 40–45 s/段"这条推算，**两个前提都没落地**。
好消息：ComfyUI 原生支持 `--use-sage-attention` 启动参数（已在 `cli_args.py` 确认），`sageattention 2.2.0` 也已装，**不装 KJNodes 也能启用**。

---

## 7. 隔离方案（已执行 + 待执行）

### 7.1 为什么必须隔离

| 理由 | 证据 |
|---|---|
| 模型库被共用 | `aisiyi-comfyui/extra_model_paths.yaml` → `base_path: /root/autodl-tmp/ComfyUI-models/` |
| 实例被共用 | 6006 实例当前驻留 17.3 GB 显存，有其他项目在跑 |
| 社区节点会漂移 | 14 个社区 H3 包（含 `H3-Optimizations`、`ComfyUI-MiniMax-H3-Turbo`），随时可能被更新，直接改变输出 |
| 原地替换会伤别人 | 共享 loras 里既有他人正在用的文件，不能覆盖 |

### 7.2 已执行（非破坏性，全部新建）

```
/root/autodl-tmp/h3p/                    ← 本流水线专用根目录（不再碰共享库）
├── comfy/                               ← ComfyUI 代码副本（64 MB，custom_nodes 留空 = 纯官方节点）
├── models/
│   ├── diffusion_models/minimax_h3_fl2va_pruned_int8_convrot.safetensors  → 软链复用（只读）
│   ├── text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors         → 软链复用（只读）
│   ├── vae/minimax_h3_video_vae_fp16.safetensors                          → 软链复用（只读）
│   ├── vae/minimax_h3_audio_vae_fp32.safetensors                          → 软链复用（只读）
│   └── loras/minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors    ← 真文件，官方版
├── input/  output/  temp/  logs/
```

**设计原则**：
1. **大权重软链复用**（41 GB 不必复制两份）——它们是只读的、已核验正确的，复制是浪费；
2. **`loras/` 用真文件**——这里才是会发生分歧的地方，必须各用各的；
3. **`custom_nodes` 留空**——本流水线只用官方核心节点，从根上杜绝社区包漂移。

### 7.3 待执行（需辉哥确认）

| # | 动作 | 性质 | 说明 |
|---|---|---|---|
| A | 起独立 ComfyUI 实例（端口 6011，`--use-sage-attention`） | 新建 | 与他人实例彻底分开 |
| B | 用官方链路跑冒烟（0.4 MP / 2s / 8 步），实测 3090 耗时 | 新建 | **替换指南里失效的 4090 基准** |
| C | 清理系统盘 17 GB 备份 tgz | ⚠️ **破坏性** | `/root/带货_teardown_backup_20260916.tgz`(14 G) + 3 个 ecom 备份 | 
| D | 是否把共享库里的社区 LoRA 换掉 | ❌ **不建议** | 会影响其他项目 |

---

## 8. 诚实边界

- ✅ **已证实**：四件权重要求与实际一致 · 四个社区节点的归属（运行态接口）· 官方 8 步 LoRA 缺失及其降秩替代 · KJNodes 缺失 · 系统盘 100% 满
- ⚠️ **不能证实**：**旧 4090 服务器已弃用，无法回溯验证当时实际跑的到底是哪条链路**。本报告的"之前跑的是社区链路"依据是**我们本地脚本的节点图**（`test_fl2v.py` 明确使用 4 个社区节点）+ 该脚本引用的 LoRA 名。
- ⚠️ **一处不一致需在冒烟时确认**：本地脚本写的是 `minimax_h3_turbo_v4_step600_ema.safetensors`，本服务器现存的是 `..._ema_pruned_comfyui.safetensors`，文件名不同（大概率是旧服务器与新服务器命名差异）。**新脚本已不再依赖这个文件。**
- ❓ **未测**：3090 上官方链路的实际耗时、官方 8 步 LoRA 与降秩变体的画质差、nvfp4 文本编码器在 sm_86 上的实际加载开销
