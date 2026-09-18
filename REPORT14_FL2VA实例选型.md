# REPORT14 · FL2VA 参考图生（锚定照链路）实例选型

> 日期：2026-09-17　辉哥要求：*"针对 FL2VA 参考图生（锚定照链路）模式，搜索一个成熟的实例，要求速度快、生成质量好且稳定、能很好适配带货视频的搭建。选好后阐明理由。"*
> 检索方式：官方文档（ComfyUI / MiniMax）+ 平台工作流市场 + 社区生产级工作流，共 5 类候选。
> 结论先行：**选「官方 ComfyUI 原生 I2V/FL2VA 模板 + 官方 8 步 Turbo LoRA + Sage Attention」，跑在自建 4090 上。** 云端平台只作备用通道。

---

## 0. 结论

| 项 | 选定 |
|---|---|
| **实例（环境）** | 自建 4090 云主机（已有）+ ComfyUI ≥ 0.30.0 |
| **工作流（真正要落地的"实例"）** | 官方模板 `video_minimax_h3_i2v.json`（接 `first_frame` / `last_frame` 即 FL2VA）<br>+ 官方 `video_minimax_h3_multiframe_reference` 模板（时间轴锚点，即我们的"中间锚点"） |
| **提速** | 官方 8 步 Turbo LoRA（`minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16`）+ Sage Attention（官方标注约 **2×** 提速） |
| **画布** | 0.98 MP → 1344×768（16:9）／ 9:16 竖版；步数分两档（试镜 / 成片） |
| **备用** | RunningHub（国内云端，免运维）；须官方 2K/15s 时走 MiniMax API 或 Comfy Cloud |

**一句话理由**：它是唯一**同时**满足"有版本基线可锁、能力完整覆盖带货链路、与我们已有实测同源、零边际成本"的实例——而带货是商用场景，官方路径也是唯一把**商用授权**讲清楚的那条。

---

## 1. 候选清单与对比

| # | 候选 | 类型 | 速度 | 质量/稳定 | 带货适配 | 成本 | 结论 |
|---|---|---|---|---|---|---|---|
| 1 | **官方 ComfyUI 原生 I2V(FL2VA) 模板 + Turbo 8 步 + Sage Attention** | 本地工作流 | ★★★★（Sage 约 2×） | ★★★★★（有基线、可锁版本） | ★★★★★（官方多帧锚点 + 片内多镜） | 0（自有卡） | ✅ **选定** |
| 2 | RunningHub「H3 首帧/尾帧/首尾帧」开箱应用 + 三合一工作流 | 国内云端平台 | ★★★★（3090/4090/A100） | ★★★☆（工作流版本漂移） | ★★★★ | RH 币（每日送 100） | 🟡 备用（免运维） |
| 3 | RunComfy「H3 FLF2V + Turbo LoRA + Stereo Audio」 | 海外云端 | ★★★★（Turbo LoRA） | ★★★☆ | ★★★★（含产品镜头建议） | 按次 | 🟡 备用（海外网络） |
| 4 | Comfy Cloud / MiniMax 官方 API | 托管 API | ★★★★★ | ★★★★★（含官方 2K 与 Context-IR） | ★★★★★（含 referenceVideo） | 按次，**已含商用权** | 🟡 当需要 2K / 15s 时切 |
| 5 | 社区「三合一 / 导演台」类整合工作流 | 社区工作流 | ★★★★（4 步 LoRA） | ★★☆（隐式状态、版本漂移快） | ★★★ | 0 | ❌ 不作主链（可借功能，不作底座） |

---

## 2. 为什么选官方模板（6 条理由，逐条带证据）

### ① 它是唯一"有版本基线可锁"的路径 —— 直接决定我们能不能做工程化

官方文档把可复现要素全部写死了：

| 要素 | 官方钉住的基线 |
|---|---|
| ComfyUI 版本 | **≥ 0.30.0** |
| 工作流模板修订 | `3c1df78` |
| Comfy-Org 权重修订 | `4cc1d817` |
| 默认步数 / Turbo 步数 | 20 步 / 8 步（R2V 为 4 步） |
| 画布 | 0.98 MP = 1344×768（16:9），短边 768，32 倍数对齐 |
| 时长 | `17k+5` 帧网格 @ 24fps，4–15s |

官方原话：*"For a reproducible team workflow, export the workflow JSON and record the ComfyUI version plus every model-file revision."*
⇒ 这正好是部署指南 **Phase 1（runs.jsonl + 种子资产库）** 的前提。社区工作流做不到这一点。

### ② 能力完整覆盖带货链路，一件不缺

带货链路需要的三件事，官方模板全有原生支持：

| 我们需要的能力 | 官方支持 | 说明 |
|---|---|---|
| 首帧 / 尾帧 / 首+尾帧锚定（参考图生） | ✅ `MiniMaxH3ImageToVideo` | 接 `first_frame` / `last_frame` 即 FL2VA；**T2V 与 I2V 共用 FL2VA 权重** |
| **中间锚点**（动作可控的关键，我们实测"≤1 个"） | ✅ `MiniMaxH3AddGuide`，官方有独立模板 `video_minimax_h3_multiframe_reference` | 每个 Add Guide 把一张静帧（可带音频）**钉到指定 frame_idx**，`round(秒 × 24)`，支持负值从尾部数 |
| **一条片内多镜**（良率乘法级杠杆） | ✅ 提示词层 `[Shot 1] … [Shot 2] At 00:03.500 …` | 官方多帧模板的示例提示词就是"每个 `<Picture N>` 映射到某镜头首帧 + 显式时间戳切镜" |

⇒ 我们的 S1（产品动态化）/ S2（单步演示）两条生产线**不需要任何自制节点**，官方模板直接跑。

### ③ 速度有官方背书的加速手段，且不牺牲结构

- **Sage Attention**：官方原话 *"You can roughly double the generation speed with Sage Attention, with minimal quality loss"*。
  装法：`sageattention` wheel（对齐 PyTorch/CUDA 版本）+ KJNodes 的 `Patch Sage Attention KJ`，**接在 `UNETLoader` 与 `BasicGuider` 之间**，`sage_attention=auto`；或全局 `--use-sage-attention`。
- **8 步 Turbo LoRA**：官方模板自带开关。
- ⚠️ 官方同时警告：*"8-step Turbo LoRA is faster but can reduce motion and audio quality."*
  ⇒ 与我们既有实测口径一致（**用 8 步，不用更激进的 4 步**），并且**换档必须记录**（官方原话：不要在没有记录质量变化的情况下启用 Turbo）。

**速度预期（推算，未实测）**：现有基准 2s 段 81–86s（0.4MP / 8 步 / 4090）→ 叠加 Sage 约 2× 后 ≈ **40–45s/段**。

### ④ 与我们已有实测同源，迁移成本为零

我们这半个月的全部实测（FL2VA-INT8 + Turbo + 稀疏注意力、8 步、0.4MP、2–4.5s 段、中间锚点 ≤1、同 seed 逐位复现）**跑的就是这条路径**。换官方模板等于把同一件事换成官方维护版本，脚本（`test_fl2v.py` / `--guide`）与基准数据全部可继续用。

### ⑤ 成本结构对带货最友好

本地权重零边际成本。按我们的良率模型，一条 30s（5 段 × 2–4.5s）现在是 10–15 分钟；加 Sage 后有望压到 **6–9 分钟/条**，单卡日产能从 70–100 条提升到 **150–200 条量级**。这是"量大 + 要反复抽卡"的业务最需要的。

### ⑥ ⚠️ 商用合规：官方路径是唯一讲清楚的一条（带货的硬约束）

官方文档原文：
> *"Commercial use of locally generated outputs requires a MiniMax commercial license, available through Comfy, the only official reseller. Generations on Comfy Cloud already include commercial rights."*

另一条地域约束（MiniMax 官方部署文档，2026-08-26 修订）：
> Community License 的 **Excluded Territories = 美国 / 欧盟 / 英国 / 韩国**（需单独授权）；中国不在排除区。

**为什么这条是决定性的**：辉哥的业务是**带货视频**，天然商用。如果先用社区工作流把流水线做出来、再发现授权口径不清，返工成本极高。
⇒ **本地路径必须提前锁定 MiniMax 商业许可**；或把"最终成片"这一步放在 Comfy Cloud / 官方 API 上出（已含商用权）。

---

## 3. 落地：三档配置（① ② ③）

### ① 试镜档（抽卡用）—— 480P 级

```
分辨率 0.4 MP ≈ 864×480（32 倍数，9:16 竖版）
步数  8 步（Turbo LoRA）
段长  2–4.5s（动作落前 40–60%）
锚点  首尾帧同源 + 中间锚点 ≤1（Add Guide）
用途  抽卡 / 判官 L0-L1 先筛，通过的段立刻冻结
```

### ② 成片档（交付用）—— 768p 原生画布

```
分辨率 0.98 MP = 1344×768（16:9）或 768×1344（9:16）  ← 不要用 1.0MP（1376×768 超上限）
步数  8 步（若要更稳的动态可回 20 步基准档对比一次再定）
音轨  H3 原生立体声（32kHz）；中文/价格/logo 一律后期
```

### ③ 提速档（省时间，不省质量）

```
在 ① / ② 上挂 Patch Sage Attention KJ（UNETLoader → BasicGuider 之间，sage_attention=auto）
预期 ≈ 2× 提速（官方口径），画质损失"minimal"
验收  同一 (prompt, seed) 开/关 Sage 各跑一次，肉眼 + 帧差双确认后再批量启用
```

### 装机与验收清单

- [ ] ComfyUI 升到 ≥ 0.30.0，从 Template Library > Video 拉 MiniMax H3 I2V 模板
- [ ] 四个文件按目录落位：`fl2va_pruned_int8_convrot`（diffusion_models）/ `qwen3vl_32b_..._nvfp4_awq`（text_encoders）/ `video_vae_fp16` + `audio_vae_fp32`（vae）
- [ ] Turbo LoRA 落 `models/loras/`
- [ ] 导出该工作流 JSON 入库，**记录 ComfyUI 版本 + 三个权重修订号**（Phase 1 要求）
- [ ] 冒烟：跑一条 2s / 0.4MP / 8 步，比对是否落在 81–86s 基准（±15%）
- [ ] 装 Sage Attention，重复冒烟，记录提速比
- [ ] 商用授权：确认走 Comfy 商业许可 or 成片走 Comfy Cloud

---

## 4. 什么时候切备用方案

| 触发条件 | 切到 | 理由 |
|---|---|---|
| 不想管环境/依赖，只要出片 | **RunningHub**（国内：首帧/尾帧/首尾帧三种开箱应用 + 三合一工作流；机型 3090/4090/A100，每日赠 RH 币） | H3 已深度适配，中文界面，国内网络好 |
| 需要官方 **2K**、15s 长片、或真正的 `referenceVideo` 动作迁移 | **MiniMax 官方 API / Comfy Cloud** | 本地权重**不含** H3-Context-IR 与 Regenerate-2K（这两个模块未开源） |
| 海外出片/团队在海外 | RunComfy（含自动装机 Agent） | 注意 Community License 的地域排除条款 |

---

## 5. ❌ 为什么不用社区「三合一 / 导演台」整合工作流作主链

它们确实好用（文/图/参考生三模式合一、二次采样、段间引导），但有三个不适合"生产底座"的性质：

1. **隐式状态**：如"一采/二采"依赖缓存状态与 seed 匹配、"段间引导"自动截前段尾帧（默认 22 帧）、"首尾帧空输入自动降级为文生"——这些都会**在你不注意时改变输出**，与"同 seed 逐位复现"的可复现性目标冲突。
2. **版本漂移快**：十几个小时一次迭代，改动直接落在输出上。
3. **生态绑定**：多参数、多 LoRA 组合（4 步 LoRA + UniBlockSwap 等）虽能上 8GB 显存，但每条都要重新标定质量。

⇒ **可以借它们的"功能思路"**（尤其二次采样 = 我们"试镜档 → 成片档"的同构做法），但**不要把底座压在它们上面**。

---

## 6. 诚实边界

- ✅ 已核实（官方文档原文）：ComfyUI 版本/修订基线、模型文件名与目录、20/8 步与 Turbo 质量取舍、0.98MP 画布与 1.0MP 上限、17k+5 帧网格、Add Guide 的 frame_idx 机制、Sage Attention ≈2× 提速说法、本地商用需商业许可、Community License 地域排除。
- ❓ 未实测：Sage Attention 在我们这套 int8 + 稀疏注意力配置上的**实际提速比**（官方说 ≈2×，我们需自己测）；8 步 vs 20 步在**带货产品镜头**上的画质差（我们此前只测过步数与参考类任务的关系）。
- ⚠️ 待办：MiniMax 商业许可的具体报价与条款（Comfy 是唯一官方转售方）——**这条要在批量生产前落地，不能拖**。
- ⚠️ 平台条款：RunningHub / RunComfy 上"用开源权重生成、平台代算"的商用口径，需各自单独确认（不能默认等同 Comfy Cloud）。
