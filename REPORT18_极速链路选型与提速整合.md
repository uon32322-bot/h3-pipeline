# REPORT18 · 极速链路选型与提速整合

> 日期：2026-09-17
> 目的：在**不破坏画质**的前提下，把单卡（RTX 3090）的 H3 生成速度压到极限；整合进既有的「判官团 + 抽卡」生产流水线
> 方法：社区方案全量检索 → 逐项**在本机真实环境下**核验可用性 → 分层归因 → 给出可照抄的装机清单与配方
> 标注规则：✅ **实测**（有出处或本机验证） / 🔶 **公开实测**（他人机器实测，非本机） / ❓ **推算**（无实测，仅结构推导）

---

## 0. 结论速览

**一句话：最快的方案不是一个节点，而是「结构性手段（免费、收益最大）+ 官方内核手段（零安装）」。单卡 3090 的理论极限约是当前基准的 1/3；再往上只能上多卡。**

### 三档链路（按"今天就能上"排序）

| 档 | 做了什么 | 装机成本 | 预计提速 | 质量风险 | 建议 |
|---|---|---|---|---|---|
| **T0 官方纯净档** | 只动**官方内核参数**：kitchen INT8 注意力 + TorchCompile + 成片 latent 放大 + 内核升 0.36.0 | **零安装** | **1.4–1.6×** ❓ | 极低（全官方组件） | ✅ **立刻上**，这是产线默认 |
| **T1 低风险增量档** | 再装：SageAttention 2.2.0 + VAE Speedup | 装 2 个包 | **1.7–1.9×** ❓ | 低–中（可回退） | ✅ 建议上，装完即可量化 |
| **T2 激进档** | 再上：FastH3 4 步 + FirstBlockCache 块缓存 | 装节点包 + 换 LoRA | **3.3–3.7×** ❓ | **高**（降步数糊运动） | ⚠️ **仅草稿/试镜档**，成片不用 |
| **T3 极限档** | 租 8 卡实例跑 RunningHub Lightning（开源） | 租 8 卡 + 354GB 磁盘 | **12.2×** 🔶（8×6000D 实测） | 中（BF16 权重但算术已变） | ⚠️ 需报预算，非本地路径 |

> 上面三档的倍数来自配套的 **`speedup_stack.html` 提速叠加台**（分段 Amdahl 模型：去噪 80% / 视频解码 13% / 音频解码 2% / 文本编码 3% / 封装 2%，同组互斥项取最优而不叠乘）。它算出的正是 **T0 = 1.54× / T1 = 1.72× / T2 = 3.50×**。**这是排序工具，不是报价依据**——作用占比是结构估算，本机未实测。

### 三条必须先纠正的流行说法（详见 §4）

| 说法 | 真相 |
|---|---|
| "加 EasyCache 就快一半" | ❌ **在 8 步 Turbo 下实测 −0.7%，等于空转**；且有画质退化报告 |
| "SGLang Pack 3.17× 提速" | ⚠️ 那是 **8 卡 TP2/Ulysses4 对比单卡原生**，**单卡不适用** |
| "开 CUDA13 + INT8 提速 3 倍" | ⚠️ **我们的机器已经是 torch 2.14+cu130 + INT8 convrot 权重**，前提早已满足；真增益在「打开 INT8 注意力后端」这个**零安装开关**上 |

### 一个反直觉的结论：层与层会互相稀释

用叠加台算出来的结果里，最值得记住的不是倍数，而是这个：

| 组合 | 总提速 | 相对上一档的增量 |
|---|---|---|
| T0（官方纯净，6 项） | 1.54× | — |
| T1（再装 SageAttention + VAE Speedup） | 1.72× | **只多 12%** |

**原因**：分辨率层已经把去噪段砍掉近一半，SageAttention 能抓的绝对值随之变小（Amdahl 的必然结果）。

⇒ **实务含义**：**先装便宜的，边际收益会自己衰减**。不要为了"再挤 10%"去上高风险的块缓存 —— 那是拿复现性和画质去换已经不多的剩余空间。真正的跳变（3.5×）来自 **T2 那一层的降步数**，而它只能给草稿用。

---

## 1. 本机实测条件（选型前提，全部现场核验）

| 项 | 实测值 | 对选型的意义 |
|---|---|---|
| GPU | **NVIDIA RTX 3090 / 24576 MiB** | 单卡；Ampere |
| 计算能力 | **SM 8.6** | SageAttention / Sol-Attn / SLA 均要求 ≥SM80 → ✅ **支持**；CuTe DSL 需 SM90+ → ❌ 走 Triton 回退 |
| 驱动 | 595.71.05 | — |
| PyTorch | **2.14.0+cu130** | ✅ CUDA 13 前提已满足 |
| CUDA | **13.0** | ✅ |
| ComfyUI 内核 | **0.33.0** | ⚠️ **落后**（最新 0.36.0，共享实例已是 0.36.0） |
| 自定义节点数 | **0**（纯官方） | 干净基线；任何加速件都是"新增" |
| 官方内核已有加速节点 | `EasyCache` `LazyCache` `TorchCompileModel` `ModelAttentionBackend` `BlockSparseAttention` `ResolutionSelector` `MiniMaxH3SigmaShift` | ✅ **零安装可用** |
| triton | **3.8.0** ✅ | Sol-Attn / SLA 的 Triton 路径前提 |
| comfy-kitchen | **0.2.31** | ⚠️ 已装；但 Sol-Attn 要求 **≥0.2.33**（升级才能用） |
| **sageattention** | ❌ **未安装**（pip 全环境无此包） | **纠正 REPORT16 的记录**：此前"已装 2.2.0"的说法有误 |
| 磁盘 | autodl-tmp **144 GB 可用** | 装加速件、下 LoRA 都够 |
| 共享实例可参考样本 | `/root/ComfyUI` = **0.36.0** + `ComfyUI-ALLinONE-MinimaxH3` + `ComfyUI-H3-Motion-Context` + `ComfyUI-VideoHelperSuite` | ✅ 一份**活的、已跑通的加速装机样本**，可照抄配方（但不照抄文件——项目要独立） |

> ⚠️ **重要说明**：共享实例的那套节点包属于其他项目，**不能直接复制目录**（违反我们的独立性规范）。正确做法是：**照抄它的配方（版本号 + commit SHA），在我们自己的 `custom_nodes/` 里装我们自己的副本**。

---

## 2. 社区提速方案全景（按作用层分类）

把所有方案按「它作用在哪一层」分清，避免把不同层的东西混在一起比较。

| 层 | 做什么 | 代表方案 |
|---|---|---|
| **0 模型层** | 换基础模型本身 | Base H3 / **FastH3 Dense** / FastH3 VSA / H3 Max（云） |
| **1 权重层** | 降精度省显存 | BF16 / FP8 / **INT8 ConvRot**（我们在用）/ GGUF Q4 / AWQ |
| **2 步数层** | 蒸馏压缩去噪步数 | **LightX2V Turbo 4/8 步**（官方，我们在用）/ larryvrh v4 step600 EMA / RH 后训练模型 / FastH3 4 步 DMD2 |
| **3-A 注意力层（dense）** | 同样的注意力算得更快 | **SageAttention 2 / 3** / **comfy kitchen INT8 注意力** / FlashAttention |
| **3-B 注意力层（sparse）** | 减少注意力计算量 | **SLA** / Sol-Attn / Block Sparse / H3 Sparse Attention / VSA |
| **4 缓存层** | 跳过相似步/块 | **EasyCache / LazyCache** / **FirstBlockCache** / TE-Speed / Cache-DiT / Spectrum |
| **5 解码层** | 加速 VAE 解码 | **VAE Speedup（批量 tile）** / H3VAE_TRT（TensorRT）/ TAESD H3（快速预览） |
| **6 分辨率层** | 在更小画布上算 | **0.4MP 草稿 → latent 放大 → 0.98MP 成片** / MinimaxH3LatentUpscaler3D |
| **7 执行层** | 减少调度/编译开销 | **torch.compile** / CUDA Graphs |
| **8 多卡层** | 横向扩展 | **SGLang TP/Ulysses** / vLLM-Omni / RH Lightning |

**读法**：第 2、6 层是**乘法级**（直接少算），第 3、4、5、7 层是**百分比级**（算得更快），第 8 层是**线性级**（多卡）。我们的三档链路就是按这个顺序叠加。

---

## 3. 逐方案可行性判定（本机口径）

### 3.1 ✅ 零安装可用（官方内核内，今天就能上）

| 方案 | 节点/开关 | 做什么 | 报称增益 | 质量风险 | 判定 |
|---|---|---|---|---|---|
| **Kitchen INT8 注意力** | `ModelAttentionBackend` → `comfy kitchen attention` | 量化 INT8 注意力后端（官方口径：仅 Nvidia/AMD 可用） | 未标定 | 低（官方组件） | ⭐ **首选实验项**——与我们的 INT8 ConvRot 权重天然匹配 |
| **TorchCompile** | `TorchCompileModel` | 编译执行图，减少调度开销，防 OOM | 未标定 | 低 | ✅ 建议开启 |
| **分辨率分层 + latent 放大** | `ResolutionSelector` + 内嵌 latent upscaler | 0.4MP 生成 → 放大 → 近 720p；0.5MP → 近 1080p | **3–5×**（768p 相对 480p 的耗时比） | 中（放大引入偏移） | ⭐ **结构级最大收益**，草稿必用 |
| **Sigma Shift 调参** | `MiniMaxH3SigmaShift` | 对齐音视频去噪地平线（现默认 video 12 / audio 3；社区推荐 video 12 / audio **6**） | 间接 | 低 | ✅ 值得 A/B |
| **内核升级 0.33.0 → 0.36.0** | 换官方 revision | TAESD H3 快速预览解码器 / VAE 优化 / EasyCache 音频修复 / **补齐缺失特殊 token（修生成异常）** / prompt embeddings / token 级噪声掩码 | 未标定 | 极低（纯官方） | ⭐ **性价比最高的一项**：零节点、可锁版本 |
| **EasyCache / LazyCache** | `EasyCache` | 跳过相似步 | 20 步下 **21min→11min** 🔶；**8 步下 −0.7%（空转）**🔶 | **有退化报告**（线条闪烁/绿背景） | ❌ **不进产线**（见 §4.1） |

### 3.2 🔧 需安装，低–中风险（T1 档）

| 方案 | 来源 | 报称增益 | 质量风险 | 装机要点 | 判定 |
|---|---|---|---|---|---|
| **SageAttention 2.2.0** | `pip install sageattention`（cu130 wheel） | **≈2×**（注意力段） 🔶 | 中：INT8 QK 路径可能让**眼睛糊/旋涡** → 回退开关 `low_precision_attention=False` | wheel 必须匹配 cu130 + torch 2.14 | ✅ 装，但**必须过眼睛检查** |
| **VAE Speedup（批量 tile 解码）** | ComfyUI-TeaCache 包的 `MiniMax H3 VAE Speedup` | **≤2×**（解码段） 🔶 | **极低**：5090 上逐位相同；**3090 上实测偏差仅 fp16 舍入 2.4e-4**（远小于 1/256 像素步长）🔶 | 需装该包；接在 video VAELoader 之后 | ⭐ **本报告中最"干净"的一项加速**——3090 已被单独验证过 |
| **SLA Attention（SLA Draft 配方）** | `ComfyUI-PlagueKind-Nodes @6ca3037` | 未标定 | 中：SLA kernel 失败**自动回退 dense**（安全）；但**不能与 Block Sparse / SageAttention 叠加** | 必须配 turbo LoRA；SLA 必须是**最后一个 model patch**，直连 guider + scheduler | ✅ 试；配方照抄见 §7 |
| **SageAttention 补丁（KJNodes 路径）** | `ComfyUI-KJNodes @3f20054` | 同上 | 同上 | ALLinONE 包的"High Quality 预设"用它 | 🟡 与 `--use-sage-attention` 二选一，别重复打 |
| **Spectrum 去噪加速** | ALLinONE 包 `SpectrumApplyMiniMaxH3` | 未标定 | 中：官方注"画质掉了就关" | degree=1；Block Cache T8 要插在 Spectrum **之前** | 🟡 可试，草稿档 |
| **跨段链式（不重编码）** | `ComfyUI-H3-Motion-Context-MultiRef @87de57b` | 省重编码 | 低 | 用于多段拼接 | ✅ 与我们的"片内多镜"互补 |

### 3.3 ⚠️ 需安装，高风险 / 破坏版本锁定（T2 档）

| 方案 | 增益 | 为什么高风险 | 判定 |
|---|---|---|---|
| **TE-Speed 块级缓存** | **40–45%** 🔶 | 需要 `patch_model.py` **改 `comfy/ldm/minimax/model.py`** ⇒ **破坏官方代码纯净性**，直接冲击我们的"同 seed 逐位复现"铁律；`cache_depth 0.75` 有画质损失 | ⚠️ 只在**草稿档**用，成片禁用；且必须在独立副本上打补丁 |
| **FirstBlockCache**（TeaCache 包） | **2.58×**（该阶段） 🔶 | **跳过 50 层里的 49 层**，只重算缓存残差 → 输出等价性无从保证 | ⚠️ 必须自测 A/B；不建议进成片 |
| **FastH3 4 步（NVIDIA FastVideo）** | **3.4–3.9×**（4090 实测）🔶 / 官方口径 **up to 14×** | ① 官方配置是 **4×B200 + CUDA13**；② ComfyUI 路线（`comfyui-minimax-h3-audio-T8`）中 **`t2va_only` 才是唯一已验证的 task_family**，`t2va_fl2va` 非默认，`ref2va` 是 `untrained_exp`；③ 仍是 **Preview-v1** | ⚠️ **草稿/试验档**；FL2VA 场景需自证 |
| **Cache-DiT** | 实测 **−0.1% ~ −0.6%（无效）** 🔶（8 卡 20 步重载下） | 官方在重载 H3 上测出**无收益甚至略慢** | ❌ 不采用 |
| **GGUF Q4 量化** | 省显存为主 | 反量化有额外开销；24GB 显存下我们用 INT8 已够 | ❌ 不需要 |

### 3.4 ❌ 本机不适用

| 方案 | 原因 |
|---|---|
| **SGLang Pack（3.17×）** | 报称数据是 **8 卡 TP2/Ulysses4 vs 单卡**，不是单卡内加速（见 §4.2） |
| **RH Lightning（12.2×）** | 安装脚本**硬断言 8 卡**：`assert torch.cuda.device_count() == 8`；需 354GB 磁盘、Python 3.10、SGLang 固定快照。→ 只能作为 T3 租实例方案 |
| **vLLM-Omni 8 卡服务** | 同上，8 卡集群路线 |
| **SelfLift Progressive Sampler** | 实验性适配，论文未验证 H3；1080p 直出（13min→11min）🔶，收益不大且节点不存在于本机 |
| **fp16 强制加速（`--fp16-unet`）** | 那是 **RTX 20 系（Turing，无 bf16 张量核）** 的补救方案；30/40/50 系 bf16 与 fp16 同级，**无收益** |
| **fp8 / nvfp4 量化** | 需 SM90+/SM100+；3090 是 SM86 ⇒ 不可用（我们是 INT8，正好是 30 系能用的那档） |

---

## 4. 去伪：三条被普遍夸大的说法

### 4.1 ❌ "加 EasyCache 就快一半" —— 在 8 步下是空转

| 证据 | 内容 |
|---|---|
| 公开实测（8 步） | 832×480 / 243 帧 / 8 步 / er_sde / turbo LightX2V，**唯一变量是 EasyCache**：无 = 740.8s，有 = 735.9s ⇒ **−0.7%，在误差内** 🔶。原因：EasyCache 是"跳过与上一步相似的步"，**只有 8 步根本没有可跳的机会**，默认阈值 `reuse_threshold=0.2` 从未触发 |
| 公开实测（20 步） | 15s 480p：**21min → 11min**，"画质几乎无损" 🔶（该数值出现在 20 步或 CUDA/INT8 修复场景） |
| 画质反向证据 | 有实测者结论是 **"画质最重要的设置是 EasyCache 关掉"**——同一 seed 下开关 EasyCache 会显著改变线条闪烁与背景色块 🔶 |

**结论**：EasyCache 在**高步数**下有效、在**低步数 Turbo**下无效；且它带有画质副作用。**我们的产线固定用 8 步 Turbo ⇒ EasyCache 不进产线**。若某天回到 20 步兜底档，再重新评估。

### 4.2 ⚠️ "SGLang Pack 3.17× 提速" —— 那是 8 卡比 1 卡

原文基准表写得很清楚：**native 用 1 张卡，SGLang 用 8 张卡（TP2/Ulysses4）**。

| 负载 | Native（1 卡） | SGLang（8 卡） | 报称 |
|---|---|---|---|
| Light warm | 1:58 | 0:37.33 | 3.17× |
| Heavy warm | 4:17:09 | 31:19 | 8.21× |

⇒ 这是**横向扩展**的收益，不是单卡加速。**单卡 3090 上装 SGLang Pack 的收益无公开数据**。别为了"3.17×"去装它。

### 4.3 ⚠️ "CUDA13 + INT8 提速 3 倍" —— 前提我们已满足，真开关在别处

那篇教程的场景是：**老环境 + 旧 sageattention wheel**，修好 cu130 + INT8 后 5s 视频 12–13min → 4min。

我们的现状：`torch 2.14.0+cu130` ✅、权重就是 `minimax_h3_fl2va_pruned_int8_convrot` ✅ ⇒ **前提早已满足**。

真正的动作是：**打开 INT8 注意力后端**（`ModelAttentionBackend → comfy kitchen attention`）。这个开关**零安装**，在官方内核里就有，而我们此前从未用过。→ 列为 T0 第一实验项。

---

## 5. 推荐链路（三档，可照抄）

### T0 · 官方纯净档（零安装，今日可上）

```
内核：ComfyUI 0.36.0（或 ≥0.35.1），纯官方节点
模型：minimax_h3_fl2va_pruned_int8_convrot.safetensors（已有，sha256 e889202c…c47a）
LoRA：minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors（已有，sha256 2339acdf…b111e）
图：
  UNETLoader → LoraLoaderModelOnly(8步LoRA) → ModelAttentionBackend(kitchen) → TorchCompileModel → BasicGuider
  ResolutionSelector(0.4MP 草稿 / 0.98MP 成片) → MiniMaxH3ImageToVideo
  MiniMaxH3SigmaShift(video 12 / audio 6，待 A/B)
  RandomNoise + KSamplerSelect(res_multistep) + BasicScheduler(simple, 8, 1) → SamplerCustomAdvanced
  VAEDecode + VAEDecodeAudio → CreateVideo(24fps) → SaveVideo
```
**收益来源**：kernel INT8 注意力（新）+ TorchCompile（新）+ 内核升级（新）+ 既有 8 步 LoRA/分辨率分层。

### T1 · 低风险增量档（装 2–3 个包，全部可回退）

在 T0 基础上加：
1. `pip install sageattention`（cu130 wheel，2.2.0）→ 或改用 `--use-sage-attention` 启动参数（二选一，别重复）
2. `MiniMax H3 VAE Speedup`（来自 ComfyUI-TeaCache 包）→ 接在 video VAELoader 之后，**3090 已单独验证偏差仅 2.4e-4**
3. `SLA Attention`（`ComfyUI-PlagueKind-Nodes @6ca3037`）→ 走 SLA Draft 配方

**注意两条互斥约束**（官方明确）：SLA **不能**与 Block Sparse / SageAttention 叠加；SLA 必须是**最后一个 model patch**。⇒ T1 内部要**按 A/B 分次验证**，不能一把全开。

### T2 · 激进档（只给草稿/试镜）

- FastH3 4 步（LoRA 1,485,626,152 B，sha256 `4ce198c8 3132251b 7fd0de25 03823aa4 9c53983f 068318f6 6cb19eae fb7fcc12`）
- FirstBlockCache 块缓存
- TE-Speed 块缓存（**只在打过补丁的独立副本上**）

**硬规则**：T2 只用于「构图/提示词试镜」；**成片一律走 T0/T1**。理由：4 步在**快速运动**上会糊、拖影（这是被反复记录的主要失效模式），且成片重抽的成本远高于多跑几步。

### T3 · 极限档（多卡，需预算）

租 8 卡实例跑 **RunningHub H3 Lightning（开源）**：8×RTX 6000D 上 5s/1344×768 = **28.7s（12.2×）**，15s 视频 **48.2s（t2va）/ 73.0s（ref2va）** 🔶。
- 前提：354GB 磁盘、CUDA 13、Python 3.10、SGLang 固定快照、TP2+Ulysses4
- ⚠️ 官方声明：**BF16 权重 ≠ 无损输出**（SageAttention 内部量化、蒸馏与 Cache-DiT 都改变了计算）
- ⚠️ 只支持 8 卡（脚本硬断言）；4 卡可改参数但**无官方性能背书**

---

## 6. 可照抄的装机清单

### 6.1 内核与依赖

| 组件 | 目标版本 | 说明 |
|---|---|---|
| ComfyUI | **0.36.0**（≥0.35.1） | 与共享实例同版本，已验证可跑；含 TAESD H3 / VAE 优化 / 特殊 token 修复 |
| torch | 2.14.0+cu130（**已满足**） | 别动 |
| comfy-kitchen | ≥0.2.31（已满足）；**若要用 Sol-Attn 需 ≥0.2.33** | — |
| triton | 3.8.0（已满足） | Triton 回退路径前提 |
| sageattention | 2.2.0（cu130 wheel） | T1 档才装 |

### 6.2 节点包（T1–T2，按 commit 锁版本）

| 包 | commit | 用途 |
|---|---|---|
| `ComfyUI-PlagueKind-Nodes` | `6ca3037` | SLA Attention（草稿） |
| `ComfyUI-MiniMax-H3-Turbo` | `4274783` | Turbo 预设 |
| `ComfyUI-KJNodes` | `3f20054` | SageAttention 补丁（High Quality 预设） |
| `ComfyUI-VideoHelperSuite` | `4ee72c0` | 预览不落盘 |
| `ComfyUI-H3-Motion-Context-MultiRef` | `87de57b` | 跨段链式（可选） |
| `ComfyUI-SeedVR2_VideoUpscaler` | `4490bd1` | 成片超分（可选） |

> ⚠️ **独立性纪律**：以上一律**克隆到我们自己的 `custom_nodes/`**，不复制、不软链共享实例的目录。
> ⚠️ **版本锁定**：`custom_nodes` 一旦装了就写进 `models_manifest.txt` 同级的一份 `env_manifest.txt`（版本 + commit），否则"同 seed 逐位复现"会失效。

### 6.3 需补下载的权重

| 文件 | 用途 | 备注 |
|---|---|---|
| `minimax_h3_fl2v_turbo_4step_v1.0_768p_comfyui_bf16.safetensors` | 4 步草稿档 | ⚠️ 与 8 步**同尺寸不同哈希**（1,956,193,000）——必须用 sha256 校验，不能用文件大小 |
| FastH3 dense adapter（1.48 GB） | T2 前沿 | sha256 见 §5 T2；Preview-v1，FL2VA 未验证 |

---

## 7. 可照抄的工作流配方

### 7.1 SLA Draft 配方（来自共享实例 ALLinONE 包的参考配方，强证据）

| 项 | 值 |
|---|---|
| 采样器 | **er_sde** |
| 调度器 | **beta** |
| 步数 | **6** |
| LoRA | 8 步 Turbo LoRA，**强度 1.0** |
| 注意力 | SLA（**最后一个 model patch**，直连 guider + scheduler） |
| 搭配 | 必须配 Comfy Kitchen |

> ⚠️ **换采样器 = 换基线**：我们产线现在是 `res_multistep + simple + 8 步`。改 `er_sde + beta + 6 步` 后**旧的可复现资产全部作废**，必须重新标定并单独建资产库。建议：**成片档保持原配方，草稿档用 SLA 配方**，两套分开记账。

### 7.2 接线硬规则（来自多处官方/包文档）

1. **Block Cache 要在 Spectrum 之前**
2. **SLA 必须是最后一个 model patch**，且**不得**与 Block Sparse / SageAttention 同用
3. `TorchCompileModel` 接在 loader 之后、guider 之前
4. VAE Speedup 接在 **video VAELoader** 之后
5. **不要在同一模型上叠多个"拥有采样器/注意力"的节点**（unsupported territory）

---

## 8. A/B 验证协议（每层必须单独量化）

**铁律：一次只开一个变量；同 seed 对照；先验证结果再开下一个。**

| # | 对照 | 变量 | 记录什么 |
|---|---|---|---|
| A0 | 基线 | 0.36.0 内核 + 8 步 + res_multistep/simple | 耗时 / 帧差基准 |
| A1 | A0 vs 内核升级 | 0.33.0 → 0.36.0 | 耗时差 + **是否修掉生成异常**（特殊 token 修复） |
| A2 | A1 vs kitchen | `comfy kitchen attention` 开/关 | 耗时 + 眼睛/细节检查 |
| A3 | A1 vs TorchCompile | 开/关 | 耗时 + 显存峰值 |
| A4 | A1 vs SageAttention | 装/不装（或 `--use-sage-attention`） | 耗时 + **眼睛专项检查**（INT8 QK 风险） |
| A5 | A1 vs VAE Speedup | 开/关 | 耗时分段（解码段占比）+ 帧差（应 ≈2.4e-4 量级） |
| A6 | A1 vs SLA 6 步 | SLA 配方 vs 8 步基线 | 耗时 + 是否够成片 |
| A7 | A1 vs 分辨率 | 0.4MP vs 0.5MP vs 0.98MP | 耗时的分辨率弹性（验证 3–5× 说法） |
| A8 | A1 vs Sigma Shift | audio 3 vs 6 | 音画同步 |

**验收口径**：每层增益必须 **>10%** 才留下（低于此值不值得增加复杂度/风险）；任何一层导致"同 seed 帧差 > 噪声基线 1.08"要标记为"变了算"，并决定是否接受。

---

## 9. 诚实边界（未实测项，别当结论用）

| # | 未测项 | 现状 |
|---|---|---|
| 1 | **T0/T1/T2 的合计提速倍数** | §0 表里的 1.3–1.7× / 2.0–2.6× / 3–4× 全是 **❓推算**，基于公开实测的分层叠加，**本机一次都没测过** |
| 2 | **3090 上的绝对耗时** | 我们现有的 `20L+43` 拟合公式来自**已弃用的 4090 + 社区节点链路**，在新机新链路上作废 |
| 3 | **kitchen INT8 注意力对 H3 的实际增益** | 官方 tooltip 只说"量化 INT8 注意力"，**无 H3 实测数据** |
| 4 | **SageAttention 在 cu130 + torch 2.14 下的 wheel 兼容性** | 未验证；装不上就退回"不装" |
| 5 | **内核 0.33.0 → 0.36.0 的升级是否会破坏现有复现基线** | 未验证；升级后**必须重跑一遍同 seed 复现测试** |
| 6 | **FastH3 在 FL2VA（首帧）下的表现** | 官方只验证 `t2va_only`；`t2va_fl2va` 非默认，`ref2va` 明确标 `untrained_exp` |
| 7 | **SLA 在成片档的可用性** | 未验证；当前只敢放草稿档 |
| 8 | **Speedup 叠加是否线性** | 不同层的收益可能互相覆盖（例如注意力加速 + 解码加速的占比会变），**不能简单相乘** |

---

## 10. 与部署指南的整合点

| 整合位置 | 内容 |
|---|---|
| 部署指南 §1.5（新增） | 三档链路表 + 本报告索引 |
| 部署指南 Phase 0 | 内核目标版本从 0.30.0+ 改为 **0.36.0**；环境盘点项加入"加速件清点" |
| 部署指南 Phase 1 | 资产库新增 **`env_manifest.txt`**（节点包版本 + commit），与 `models_manifest.txt` 同级 |
| 部署指南 Phase 6 | 标定实验新增 **A0–A8 逐层提速 A/B** |
| 部署指南 §4 验收清单 | 新增：内核版本已锁 / 加速件已锁 commit / 每层增益 >10% 才留 |

---

## 11. 一页速记

1. **最快的结构性手段是免费的**：0.4MP 草稿 + latent 放大 + 片内多镜（降 S）。别把提速希望全押在节点上。
2. **T0 零安装就有收益**：kitchen INT8 注意力、TorchCompile、内核升 0.36.0。**今天就该做**。
3. **EasyCache 在 8 步下是空转，还带画质风险** —— 别装。
4. **最干净的加速项是 VAE Speedup**（3090 已单独验证偏差 2.4e-4）。
5. **最有效的单卡注意力加速是 SageAttention**，但必须过"眼睛检查"。
6. **别信 SGLang 的 3.17×** —— 那是 8 卡比 1 卡。
7. **要真正的 12×，只能租 8 卡**（RunningHub Lightning，开源）。
8. **4 步只给草稿**；成片永远回到 6–8 步。
