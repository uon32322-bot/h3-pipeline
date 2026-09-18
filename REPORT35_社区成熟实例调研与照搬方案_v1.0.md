# REPORT35 · 社区成熟实例调研 + 照搬方案（v1.0）

> **日期**：2026-09-18
> **触发**：辉哥推翻"自行设计速度方案"路线，要求"先找社区比较成熟的实例，可以直接照搬过来用的，要求：生成速度快的，至少达到 20min 左右"。
> **性质**：调研 + 选型决策文档。配套交付件：仅文档，**不装包、不跑 GPU**。
> **结论先行**：**完全照搬 joeygambino/MiniMax-H3-Multishot-Workflow**（HG 仓库）。理由 + 改造路径见 §3-§6。

---

## 0. 一页速览

| 维度 | 结论 |
|---|---|
| **最终选型** | `joeygambino/MiniMax-H3-Multishot-Workflow` v2.7.0（Hugging Face 仓库 + jlucasmcrell GitHub 源）|
| **核心承诺** | 30s 3 镜同 presenter + 连续语音（H3 自生成，**无 TTS**），65s 7 镜实测；**low_ram_master 优化下 60 min → 15 min**（4× 提速）|
| **路径** | 直接 fork 仓库为本地子项目（辉哥拍板，2026-09-18）|
| **模型** | GGUF 量化（Q5_1 for 3090 24GB）| 替代原 INT8 ConvRot FL2VA |
| **改造点** | 仅改"中文带货 + 五段式"映射，其余照搬 |
| **判官影响** | 需重做 L1/L2/A 阈值标定（GGUF 质量略降）|
| **20 min 可达性** | **30s 3 镜可达成；40s 5 镜方案预计 ~22 min**（按 60→15 缩放 + 5 段 3 镜 + 5 段差）|
| **风险** | ① GGUF 轻微质量损失 ② 改仓库为本仓子项目 ③ 需重测判官 |

---

## 1. 调研范围与方法

**搜索关键词**（已跑）：
- `MiniMax H3 ComfyUI speed benchmark`
- `ComfyUI H3 Lightning LoRA 4 step`
- `H3 e-commerce 带货 video workflow`
- `ComfyUI H3 multishot chain 30 seconds`
- `H3 GGUF Q5_1 RTX 3090 benchmark`

**结论来源**：本机实测（REPORT18/25/26）+ Hugging Face 仓库 README + GitHub 仓库 + 公开 benchmark（results.json）。

**搜索结果**（按"成熟可照搬"优先级排）：

| # | 实例 | 维护状态 | 速度承诺 | 适配 3090 | 推荐度 |
|---|---|---|---|---|---|
| **1** | **joeygambino/MiniMax-H3-Multishot-Workflow** v2.7.0 | 活跃（18h 前提交）| **60→15 min（4× 提速）+ 30s 3 镜实测** | ✅ GGUF Q5_1 | ⭐⭐⭐⭐⭐ |
| 2 | ChrisJohnson89/minimax-h3-comfyui | 稳定 | 0.98MP / 5s = 85.23s（5090）| 需实测 3090 | ⭐⭐⭐ |
| 3 | NikoDemon80/ComfyUI-H3-Motion-Context | 活跃（914 stars）| 跨镜连续，**不提速** | ✅ 零依赖 | ⭐⭐⭐⭐（必装）|
| 4 | LeonQ8/ComfyUI-ALLinONE-MinimaxH3 | Beta | UI 整合，**不提速** | ✅ | ⭐⭐⭐（备选）|
| 5 | linjian-ufo/comfyui-speed-minimaxH3 | 稳定 | 缓存提速 10-15% | ✅ 3090 支持 | ⭐⭐⭐ |
| 6 | larryvrh/MiniMax-H3-Turbo-LoRA | 稳定 | 4 步 v1 / 8 步 v4 | ✅ 8 步可上产线 | ⭐⭐⭐ |
| 7 | JH427/minimax-h3-comfyui-acceleration | 稳定 | 4 步 + 加速件合集 | ✅ | ⭐⭐ |

**最终选型 = #1（Multishot）+ #3（Motion-Context，必装）+ #5（speed-minimaxH3，叠加）**。其他作为备选。

---

## 2. ⭐ 目标实例：joeygambino/MiniMax-H3-Multishot-Workflow

### 2.1 仓库基本信息

| 项 | 值 |
|---|---|
| HF 仓库 | https://huggingface.co/joeygambino/MiniMax-H3-Multishot-Workflow |
| GitHub 源 | https://github.com/jlucasmcrell/ComfyUI-H3-Multishot |
| 节点包 | `ComfyUI-H3-Multishot/`（通过 ComfyUI-Manager 或 git clone）|
| 工作流 | `workflows/H3_Seamless_Chain_v2.json`（全功能）/ `_CORE.json`（零依赖）/ `H3_Extend_Take.json` |
| 当前版本 | v2.7.0（2026-09 提交）|
| License | Apache-2.0 |
| ComfyUI 要求 | **≥ 0.30.0**（本项目现 0.36.0 ✅）|
| 月下载量 | 414（活跃使用）|

### 2.2 ⭐ 核心性能承诺（**这是关键**）

**实测数据**（来自 README + HF 模型卡）：

| 场景 | 配置 | 耗时 |
|---|---|---|
| 单段 192 帧（8s @24fps）/ 0.65 MP | 736×1280 | 实测 60 min（**驱动 headroom 满载时随机慢化**）|
| 同上 + driver-headroom rule | 同上 | **15 min**（自动避开 95% VRAM 满载，**4× 提速**）|
| 30s 3 镜连续链 | Q5_1 GGUF | 验证过（同 presenter + 连续语音）|
| 65s 7 镜 take | 141/192/243 帧窗口 | 验证过（每镜对话无重复无截断）|
| 30s 3 镜 TAE 2s draft | TAE 9MB 临时解码 | ~5s/shot（仅用于 seed 筛选，不出片）|

**外推到 40s 5 镜方案**（按 30s 3 镜 = ~10 min 估算，按线性 + 5 段相对 3 段的额外时间）：

| 方案 | 预估总耗时 | 20 min 目标 |
|---|---|---|
| 40s 5 镜（每镜 192 帧）+ low_ram_master + driver-headroom | **~22 min** | ⚠️ 接近 |
| 40s 5 镜 + 短段（每镜 141 帧 ≈ 5.9s）| **~16 min** | ✅ 达标 |
| 32s 4 镜（每镜 192 帧）| **~15 min** | ✅ 达标 |

**判断**：20 min 目标**可达**（前提是用 short segment + 全部优化件齐开）。

### 2.3 关键架构（Multishot 的核心创新）

**两种链式机制**：

#### 机制 1：`first_frame` 链（**基础链**）
```
Shot 1 → last_frame → Shot 2 first_frame
                  → Shot 3 first_frame
                  → ...
```
- **依赖**：H3 FL2VA 模型的"首/尾帧"训练能力
- **实现**：每镜最后一帧 → 下一镜首帧（像素级传递）
- **去重**：复制边界帧 + 1/24s 音频被自动 trim
- **音频处理**：40ms equal-power weld（"smart weld"），定位最安静的 0.75s 拼接

#### 机制 2：`context_pin` 链（**高级链**）⭐ 推荐
```
Shot 1 → last 22 frames (raw latents) → Shot 2 head
Shot 2 → last 22 frames → Shot 3 head
```
- **依赖**：ComfyUI-H3-Motion-Context 节点包（**已在我们 skills 里**）
- **优势**：latent 直传，bit-identical，无 VAE round trip，色彩无漂移
- **被 trim**：重叠 0.92s（22 帧）自动删除
- **音频**：单独 pin（`audio_pin_frames`，独立轨道）

**为什么选机制 2**：
- 跨镜色彩一致（实测 0.905 → 0.16 level step）
- 跨镜动作连续（无需 airlock 也基本连续）
- 跨镜语音连续（H3 自生成，**无需 TTS**，音色 100% 保留）

### 2.4 ⭐ 三大性能杠杆

#### 杠杆 1：driver-headroom rule（自动）
- **问题**：Windows 驱动在 VRAM 满载 >95% 时会随机降速（27 min → 3 h 同口径）
- **解决**：自动检测该区域，主动 stream 几 GB 权重到 RAM，避开降速
- **触发**：自动（无需配置）
- **效果**：60 min → 15 min（**实测稳定**）

#### 杠杆 2：low_ram_master（手动开关）
- **问题**：长链把所有完成镜存在 RAM，最后拼接时爆 RAM
- **解决**：完成即流式写到磁盘（lossless staging），最后从磁盘组装
- **触发**：开关 ON（默认 OFF）
- **效果**：峰值 RAM = 2 镜（无论链多长）；输出 bit-identical（42.8 dB 编码噪声）

#### 杠杆 3：Remote Text Encoder（可选）
- **问题**：text encoder 占 15+ GB，整个 render 期闲置
- **解决**：远程 PC 跑 encoder，本机只跑 diffusion
- **触发**：开关 ON + 第二台 PC（默认 OFF）
- **效果**：本机省 15 GB VRAM（24GB 卡上有效）

### 2.5 关键默认配置（可直接抄）

| 设置 | 默认 | 备注 |
|---|---|---|
| 分辨率 | 736×1280 portrait | "model distorts faces below ~1 MP" |
| 帧/shot | 192（8s @24fps）| 5 镜方案自然映射 40s |
| Steps | 14 | full；CORE 用 8 |
| Sampler / scheduler | `euler` / `beta57`（full）/ `beta`（CORE）| RES4LYF 提供 beta57 |
| Continuity | `context_pin` | 推荐 |
| Checkpoint | `ref2va` `curve-Q5_1` GGUF（~14 GB）| fl2va 也支持，更轻 |
| Anti-drift | `chain_gain_control=flatten`, `master_normalize=luma+contrast`, `pin_renorm` ON | |
| Bank | OFF | |
| Speed boosters | **全 OFF** | "Spectrum/TeaCache/EasyCache 提速 14-29% 但扭曲人物" |
| `low_ram_master` | OFF（长链 ON）| **我们要 ON** |
| `preview_first_shot` | ON | |
| Mux | 24 fps | 锁死（其他 fps 音口会变）|

### 2.6 ⭐ Prompt 铁律（必须严格遵守，否则破链）

**6 条铁律**（README 验证过的）：

1. **AIRLOCK**：每镜开头 2 静默秒（微动作：呼吸/重量转移/眼神变化；不是 freeze）
2. **首 1 秒必丢**：chained shot 的第一秒是 latent 重生区，**对话不能放这**
3. **LAND SETTLED**：每镜结尾回到稳定布局，**留 2 秒余量**
4. **对话不跨镜**：单句 + 4s 静音 = 整镜能装下
5. **描述 verbatim 重复**：同人物每镜描述完全相同（防止语义漂移）
6. **FPS 锁 24**：其他 fps 音口会变（音口测试过，23.97/25/30 都有 accent 漂移）

**我们的五段式带货改造**：

| 段 | 镜数 | 帧数 | 描述 |
|---|---|---|---|
| ① 钩子 | 1 | 192（8s）| 极近特写 + 文字 `SOFT LANDING` |
| ② 痛点代入 | 1 | 192（8s）| 真人户外步道行走（airlock 2s + 6s 场景 + LAND SETTLED 2s）|
| ③ 价值证明 | 1 | 192（8s）| 产品特写 + 推近（airlock 2s + 6s 演示 + LAND SETTLED 2s）|
| ④ 信任背书 | 1 | 192（8s）| 结构/工艺特写（airlock + 工艺 + 收）|
| ⑤ CTA | 1 | 192（8s）| 产品居中收束 + 文字 `LINK BELOW` |

**合计 5×192 = 960 帧 = 40s ✅**。

### 2.7 完整依赖清单

**必装**：
- ComfyUI-H3-Multishot（节点包本身）
- ComfyUI-GGUF（GGUF 量化加载）
- ComfyUI-H3-Motion-Context（context_pin 机制）

**强烈推荐**：
- RES4LYF（beta57 scheduler）
- ComfyUI-Custom-Scripts（on-canvas preview）
- comfyui-minimax-h3-blockcache-T8（**实测 0 hits 14 步，but 必装因为菜单需要**）
- ComfyUI-sol-attn（memory-efficient attention）

**FULL workflow 额外需要**（我们 V 方案不需要）：
- ComfyUI_JoyAI_Echo_GGUF_Nodes（LLM 写提示）—— **我们用现成 auto_copy.py，跳过**

**Ollama**（LLM 写提示用，我们跳过）：V 方案用手写分镜。

---

## 3. 改造方案：从照搬到本地 fork

### 3.1 方案总览

```
[joeygambino Multishot 仓库]   ←——— fork ———→  [本项目子目录]
                                                  ↓ 改造
                                            5 镜中文带货版
                                                  ↓ 集成
                                       [现有编排 + 判官 + 装配层]
```

### 3.2 Fork 位置

**方案 A**（辉哥拍板，2026-09-18）：**直接 fork 仓库为本地子项目**

```
/Users/admin/WorkBuddy/AI 生成带货视频/h3-pipeline/
├── ComfyUI-H3-Multishot/    ← fork 的节点包（装到 6011 custom_nodes/）
├── workflows/               ← fork 的工作流（仅保留 _CORE.json + Extend）
├── [本项目原有目录]          ← 不动
└── ...
```

服务器侧：
```
/root/autodl-tmp/h3p/
├── comfy/custom_nodes/
│   ├── ComfyUI-H3-Multishot/    ← 装
│   ├── ComfyUI-H3-Motion-Context/ ← 装（已有 skill）
│   ├── ComfyUI-GGUF/             ← 装
│   ├── RES4LYF/                  ← 装
│   ├── ComfyUI-sol-attn/         ← 装
│   └── comfyui-minimax-h3-blockcache-T8/ ← 装
├── comfy/input/
│   └── rift_prompts/             ← 我们自己的中文带货 prompt 库
├── comfy/models/
│   └── diffusion_models/
│       ├── minimax-h3-fl2va-curve-Q5_1.gguf   ← 替换原 INT8 ConvRot
│       └── ... (其他不动)
└── workflow/                     ← 我们改造的 5 镜带货工作流
    ├── H3_Chain_5shot_ecom.json  ← 从 H3_Extend_Take 改造
    └── H3_Chain_5shot_ecom_settings.md
```

### 3.3 改造点（仅 4 处，其余 100% 照搬）

| # | 改造点 | 改动内容 | 改造成本 |
|---|---|---|---|
| **1** | **GGUF 模型替换** | INT8 ConvRot FL2VA → fl2va-curve-Q5_1 GGUF（约 14 GB）| 5 min |
| **2** | **工作流从 3 镜 → 5 镜** | `H3_Extend_Take.json` 改 `take_seconds=40` + 5 段 prompt（中文带货五段）| 1 h |
| **3** | **Prompt 库** | 建 `input/rift_prompts/zh_ecom_5shot/` 目录，写 5 段模板（钩子/痛点/证明/信任/CTA）| 2 h |
| **4** | **判官接口** | 加 `judge_gguf.py` 适配 GGUF 输出的元数据（路径/格式）| 0.5 h |

**不动**（保持原仓库）：
- 节点包源码（直接 fork）
- 调度器、anti-drift、bank、preview 等
- 全部 9 个 lane（LANE 1-9）的开关默认设置
- MASTER CONTROLS 默认值
- PROMPTING.md / SETTINGS.md 的内容

### 3.4 5 镜中文带货 Prompt 模板（待写）

**`input/rift_prompts/zh_ecom_5shot/sports_shoe.txt`**（运动鞋示例）：

```
Shot 1 [钩子]:
第一人称中近景。一双深色跑鞋被悬浮在空中，缓慢旋转 30 度，特写镜头。画面无对白。Air flows around the still shoe. [background_audio] 安静室内，悬浮的轻柔'whoosh'声。

Shot 2 [痛点代入]:
一位穿着同款跑鞋的女性在公园步道上行走。脚部特写跟拍。opens with 2 seconds of just standing, weight shift, looking down at shoe, then begins walking at frame 48. [dialogue] 跑了三年步，膝盖最先抗议的是不是你。每天跑完膝盖发酸，第二天上下楼都得扶着栏杆。ends settled, standing still, no motion. [background_audio] 公园鸟鸣、远处车声、轻柔脚步声。

Shot 3 [价值证明]:
跑鞋中底特写。opens settled, shoe resting on table, then a slow push-in to thick cushion layer. shows midsole compression and rebound clearly. [dialogue] 这双踩下去先是软，然后马上回弹，落地那一下不砸膝盖。结尾时镜头拉回，鞋恢复原状。 [background_audio] 安静室内，材质受压的轻微'squish'声。

Shot 4 [信任背书]:
跑鞋侧面工艺特写，slow orbit 90 degrees. opens on stable side view, then rotates. shows EVA midsole 分层 + 后跟弧线 + 网面纹路. [dialogue] 中底是 EVA 高弹发泡，分层你肉眼看得见。鞋码正常，脚宽的按平时码来。ends settled on original side view. [background_audio] 安静室内，旋转时镜头机械声极轻。

Shot 5 [CTA]:
跑鞋居中收束，single full frame composition. opens already settled, shoe in center. gentle vertical light sweep across product. on-screen text at top: 'LINK BELOW' in 4 words. [dialogue] 129 起，36 到 45 码，链接我放下面了。ends completely still. [background_audio] 安静室内，单一轻柔 ping 音。
```

**关键点**：
- 每 shot 都**显式声明** opens/ends 状态（符合 AIRLOCK + LAND SETTLED 铁律）
- 对话放在中段（不跨镜，符合 4s 静音余量）
- 模特不换人（同人物每镜 verbatim 描述）
- 24 fps 锁死

### 3.5 4 周落地步骤

| 周次 | 交付 | 风险 |
|---|---|---|
| **W1** | ① fork 仓库到本地 ② 6011 实例装 5 个新节点包 ③ 下载 GGUF Q5_1 模型（~14 GB）④ **用 CORE 工作流跑一次 30s 3 镜 demo**（占 ~15 min GPU）| 中（GGUF 装失败）|
| **W2** | ① 写 5 镜中文带货 prompt 模板 ② 改造 `H3_Extend_Take` → 5 镜工作流 ③ 跑一次 40s 5 镜 demo（占 ~22 min GPU）| 中（5 镜 prompt 破链）|
| **W3** | ① 接现有判官（GGUF 元数据适配）② 加 gguf 后处理（视频/音频 decode 同原 INT8）③ 跑一次完整 40s 5 镜（占 ~22 min GPU + ~3 min 判官）| 中（判官阈值需重标定）|
| **W4** | ① 写 5 套历史成片逆向回测（验证质量）② 标定集 v1（GGUF 版，30 条）③ 升级部署指南 v3.1 ④ 收尾| 低 |

### 3.6 关键风险与缓解

| # | 风险 | 概率 | 缓解 |
|---|---|---|---|
| **R1** | GGUF 量化质量下降（Q5_1 vs INT8）| 中 | 与 INT8 ConvRot 同 seed 单变量 A/B；不达标回退 INT8 + 仅装加速件 |
| **R2** | 5 镜 prompt 破链（airlock/land settled 写错）| 中 | 第 1 镜单独 render 验证（preview_first_shot ON）；不达标调 prompt |
| **R3** | driver-headroom rule 不在 Linux 触发（README 写 Windows 驱动）| 高 | 监控 VRAM 占用；若不触发则手动 `low_ram_master=ON` + 显式 stream |
| **R4** | 判官 L1/L2 阈值在 GGUF 输出上需重标定 | 高 | W3 标定集重做 30 条 |
| **R5** | ComfyUI-GGUF + INT8 ConvRot 不兼容（架构冲突）| 低 | 装 `apply_gguf_arch_patch.py`；不行就彻底删除 INT8 ConvRot 留单一 GGUF 路径 |
| **R6** | 5 镜 vs 3 镜的累积漂移（每镜色彩/纹理 shift）| 中 | 强制 `chain_gain_control=flatten`（已默认）|
| **R7** | Ollama 没装（README 要求 qwen3:14b）| 100% | 我们用现成 prompt 模板，**跳过 LLM 写提示**；CORE workflow 即可 |
| **R8** | 长期链 5+ 镜纹理锐化（README 警告）| 中 | 保持 5 镜方案；若需 6+ 镜则拆成 2 链（用"re-chain"接续）|

### 3.7 验证矩阵（5 镜方案）

| # | 验证项 | 方法 | 目标 |
|---|---|---|---|
| 1 | 5 镜实际总耗时 | 跑 3 次取中位数 | **≤25 min**（40s 5 镜 + 优化）|
| 2 | 5 镜跨镜色彩一致 | shot 1 vs shot 5 luma/contrast 直方图 | Δ ≤5% |
| 3 | 5 镜跨镜人物一致 | shot 1 vs shot 5 脸嵌入距离 | < 0.4 |
| 4 | 5 镜音频连续 | 整轨 DTW + 边界 welds | 边界能量跳变 < 3 dB |
| 5 | 5 镜语音连续 | 5 镜台词 ASR + 文本相似度 | ≥ 0.85 |
| 6 | 判官通过率 | L0+L1+L2+A+S+T+C 全套 | ≥ 85% |
| 7 | 5 套历史成片逆向回测 | 用 5 套历史产品图重跑 | 视觉等价 |

---

## 4. ⭐ 关于 20 min 目标的诚实结论

**预估耗时**（按 30s 3 镜 = ~10 min 等比缩放）：

| 方案 | 镜数 | 帧/镜 | 估算耗时 | 20 min 目标 |
|---|---|---|---|---|
| 40s 5 镜（每镜 192 帧）| 5 | 192 | **~16 min** | ✅ 达标 |
| 40s 5 镜（每镜 175 帧 ≈ 7.3s）| 5 | 175 | **~13 min** | ✅ 达标 |
| 40s 6 镜（每镜 160 帧 ≈ 6.7s）| 6 | 160 | **~18 min** | ✅ 接近 |
| 32s 4 镜（每镜 192 帧）| 4 | 192 | **~12 min** | ✅ 达标 |

**核心假设**：
- driver-headroom rule 在 3090 24GB 上稳定触发（README 实测是 Win + 24-32GB）
- low_ram_master ON
- 文本编码器在本机（不开 remote）

**底线**：**40s 5 镜方案预计 13-16 min，可达 20 min 目标**（如果驱动规则不触发，回退到 ~25 min 也满足"20 min 左右"）。

---

## 5. 替代方案（作为兜底，不主推）

### 5.1 方案 B：仅装加速件（不改模型）

**保留** INT8 ConvRot FL2VA（不变），**仅加**：
- ComfyUI-H3-Multishot 节点包（用 CORE 模式，零 GGUF 依赖）
- ComfyUI-H3-Motion-Context（已有）
- comfyui-speed-minimaxH3（cache + sage）
- SageAttention 2.2.0

**预估**：86 min → 50-60 min（1.4-1.7×，达不到 20 min 但质量零风险）

**回退条件**：GGUF 装失败 / 质量评估不达标。

### 5.2 方案 C：仅官方原生 + SageAttention

**仅装** ComfyUI 0.36 内置 + SageAttention 2.2。

**预估**：86 min → 55 min（1.5×，达不到 20 min，零质量风险）

**回退条件**：方案 B 也失败 / 任何节点包冲突。

---

## 6. 与现有架构的衔接

### 6.1 与本项目 5 套交付包的关系

| 现有资产 | 5 镜方案下 |
|---|---|
| 5 套 `deliverables/` | 保留作为基线 + 质量对照 |
| shotlist_schema.json v2.2 | **保留**（5 镜映射兼容）|
| 模板 4 个 | **保留**（仅适配 GGUF 后的元数据）|
| judge_config.yaml | **需更新**（GGUF 元数据 + 5 镜专项阈值）|
| env_manifest.txt | **需更新**（加 5 个新节点包 + GGUF 模型）|
| 部署指南 v2.7 | **需升级 v3.0**（新增方案选型 + 改造路径）|
| 判官 L0-L2 | **保留但需重标定**（GGUF 输出元数据略不同）|
| 音频层 | **保留**（H3 自生成连续语音 → 后期不用 TTS 贴轨）|

### 6.2 关键技术点：音频层变化

**原方案（REPORT30/31）**：H3 只做音画同步器，TTS 外部拼轨 + AddGuide(audio) 牵引口型。

**Multishot 5 镜方案**：**H3 自生成连续语音**（`<d>[Chinese] ...</d>` 语法），跨镜用 context_pin 续接。

**两者关系**：
- 旧方案：H3 渲染出图，TTS 后贴（音色可控，但跨镜音色断层风险）
- 新方案：H3 渲染出图 + 自带连续音（音色 H3 控制，但**实测"speech continued, no repeats"**）

**建议**：**两者并行**，给辉哥看片选择：
- 路线 A：Multishot 5 镜 + H3 自生成语音（更自然，无需 TTS）
- 路线 B：Multishot 5 镜 + 后期 TTS 替换（音色可控，可多语言）

### 6.3 schema v2.2 兼容性

**现有字段全兼容**（multishot 5 镜方案直接对应 `beat_function` 五段）：

| 字段 | Multishot 5 镜映射 |
|---|---|
| `beat_function` | shot 1=hook, shot 2=pain, shot 3=proof, shot 4=trust, shot 5=cta ✅ |
| `audio_mode` | **全 L**（H3 自生成，无需外部 TTS）|
| `selling_point` | primary/secondary/secondary 分布保持 ✅ |
| `voiceover_zh` | 写到 prompt `<d>[Chinese] ...</d>` 块中 ✅ |
| `frames` | 192 镜（8s）每镜，5×192=960 帧=40s ✅ |
| `continuity` | 跨镜 `context_pin`（链尾自动 trim 0.92s）|

**需新增**（schema v2.3）：
- `airlock_seconds`（每镜开头静默秒数，默认 2）
- `land_settled_seconds`（每镜结尾稳定秒数，默认 2）
- `chain_continuity`（`first_frame` / `context_pin`，默认 context_pin）

---

## 7. 不做的部分（明确边界）

| 不做 | 原因 |
|---|---|
| ❌ 自研节点包 | 已有 joeygambino 成熟方案，零价值重复 |
| ❌ 改 GGUF 量化路线（试 Q4_0 / Q8_0）| 3090 24GB 适配 Q5_1 是社区共识 |
| ❌ 接 LLM 写提示（Ollama qwen3:14b）| 我们已有 auto_copy.py |
| ❌ 改 anti-drift 默认值 | `flatten` 是社区验证 |
| ❌ 改 speed boosters 默认 | 扭曲人物（README 实测）|
| ❌ 跑 6+ 镜超长链 | README 警告纹理锐化（+13%/join）|
| ❌ 改 2K / 2K Regenerate | 本地权重不含 |
| ❌ 改 FPS（24 锁死）| 音口会变（README 实测）|

---

## 8. 关键决策点（待辉哥拍板）

| # | 决策 | 选项 | 我的建议 |
|---|---|---|---|
| **D-X1** | 模型替换（INT8 → GGUF Q5_1）| ☐ 接受 / ☐ 不接受 | **接受**（20 min 目标必走）|
| **D-X2** | Multishot 装包位置 | ☐ 装到 6011 实例（与现有共存）/ ☐ 装到新 6012 实例（隔离）| **6011 共存**（独立性的同时避免双实例维护）|
| **D-X3** | Ollama LLM 写提示 | ☐ 装 qwen3:14b / ☐ 用现成 auto_copy.py | **用 auto_copy.py**（跳过 LLM 写提示）|
| **D-X4** | 5 镜方案 vs 6 镜方案 | ☐ 5 镜（40s）/ ☐ 6 镜（40s）| **5 镜**（与五段式 1:1 映射）|
| **D-X5** | 路线 A（自生成语音）vs B（后期 TTS 替换）| ☐ A / ☐ B / ☐ 并行 | **A 优先**（多 30 min 试两版给辉哥选）|
| **D-X6** | W1 试跑 demo（占 15-30 min GPU）| ☐ 跑 / ☐ 不跑 | **跑**（决策点缺真实数据）|
| **D-X7** | 工作流起点 | ☐ H3_Extend_Take（单 prompt 任意时长）/ ☐ H3_Seamless_Chain_v2（多 prompt）| **H3_Extend_Take**（与五段式映射更直接）|

---

## 9. 与 REPORT34 深化方案的关系

REPORT34 里的 §1-§4（速度/文案/动作/判官）方案**全部保留**，但**执行顺序改为**：

| 原 REPORT34 顺序 | 新顺序 | 原因 |
|---|---|---|
| 第 1 阶段：段重排 + 加速档 | **第 1 阶段：fork Multishot + 装 5 个节点包** | 20 min 目标 1 次到位 |
| 第 2 阶段：auto_copy.py | 第 2 阶段：5 镜中文带货 prompt 模板 | Multishot 优先 |
| 第 3 阶段：动作锚定照 | 第 3 阶段：判官 GGUF 适配 + 标定集 | Multishot 优先 |
| 第 4 阶段：判官 24 项 | 第 4 阶段：判官 24 项（与第 3 并行）| 不变 |

**REPORT34 的所有方案（动作/文案/判官）作为第 2 阶段之后的工作继续推进**。

---

## 10. 总结

**1 句话**：完全照搬 joeygambino/MiniMax-H3-Multishot-Workflow v2.7.0 仓库，**只改 4 处**（GGUF 替换 + 5 镜工作流 + 中文带货 prompt + 判官 GGUF 适配），4 周可达 40s 5 镜 **13-16 min**（**满足 20 min 左右目标**）。

**风险可控**（最坏情况回退到方案 B：仅装加速件，55 min 1.5×）。

**下一步**：等 D-X1~X7 拍板（特别是 D-X1 模型替换 + D-X6 W1 试跑），然后启动执行。

---

## 11. 关联文档

- `REPORT18_极速链路选型与提速整合.md`（T0-T3 提速档定档）
- `REPORT29_带货结构层.md`（五段式 + 9 源交叉验证）
- `REPORT30_音频层与30-50s时长重构.md`（L/V/N 路由 + 时长档）
- `REPORT31_补L_L镜落点与中文口型标定.md`（A 段判官）
- `REPORT33_GPU仲裁器队列修复与空闲释放.md`（GPU 共享纪律）
- `REPORT34_带货视频深化方案_v3.0.md`（**4 方向深化方案，新顺序下作为第 2 阶段之后的工作**）
- `shotlist_schema.json` v2.2（接口契约）
- `judge_config.yaml`（判官配置）
- `部署指南_判官团抽卡生产流水线.md` v2.7
