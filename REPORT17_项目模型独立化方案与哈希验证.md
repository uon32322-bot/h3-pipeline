# REPORT17 · 项目模型完全独立化方案与哈希验证

> 日期：2026-09-17
> 触发：辉哥规范 —— **「正式部署时，本项目重新建立自己的文件夹，模型也独立下载使用，不与其他项目共享。」**
> 判据来源：`hf-mirror.com/api/models/{repo}?blobs=true` 的 `lfs.sha256`（官方权威哈希）+ 服务器实测
> 性质：部署执行文档（Phase 0 步骤 ③b 的展开）

---

## 1. 一条结论先行

**共享库里那四件权重，sha256 与官方逐项一致 —— 它们就是官方原件，没有被替换或降秩过。**
所以「模型独立化」这件事**不需要重新解决"模型对不对"的问题**，只需要解决"归属"问题：让本项目拥有自己的副本，不再依赖别人。

---

## 2. 哈希验证结果（实测，非推测）

哈希基准取自 HuggingFace 镜像 API 的 `lfs.sha256` 字段（LFS 对象的原生 sha256，权威值）。

| 文件（相对 `models/`） | 服务器实测 sha256 | 官方 sha256 | 判定 |
|---|---|---|---|
| `diffusion_models/minimax_h3_fl2va_pruned_int8_convrot.safetensors` | `e889202c…c47a` | `e889202c…c47a` | ✅ **一致** |
| `text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors` | `35a88d51…f2c6` | `35a88d51…f2c6` | ✅ **一致** |
| `vae/minimax_h3_video_vae_fp16.safetensors` | `7c1f1314…e522` | `7c1f1314…e522` | ✅ **一致** |
| `vae/minimax_h3_audio_vae_fp32.safetensors` | `8e505d95…db48` | `8e505d95…db48` | ✅ **一致** |
| `loras/minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors` | `2339acdf…b111e` | `2339acdf…b111e` | ✅ 一致（已入库本项目目录） |

完整值：

```
fl2va_pruned_int8_convrot  e889202c41dafb67b10d67b97f0d8541508036a6090af23425a5c2615d03c47a
qwen3vl_32b_nvfp4_awq      35a88d51044231fe332301d7a62aa81e3f2cba62febeb446e2c1e3e0ef76f2c6
video_vae_fp16             7c1f131492e7eddacaac9069a61b81bdd39de5cc96561e677c5eab1cdce5e522
audio_vae_fp32             8e505d95dd1561d47abd43d4238fd40d9bb1ae9e147ed0a4cba778d76ae4db48
fl2v_turbo_8step_comfyui   2339acdf19bfe123f46b971ea35d367a84adb85de43627e1eceafa5a5b2b111e
```

### ⚠️ 重要方法论：验证必须用 sha256，**不能用文件大小**

在核对官方 `lightx2v/Minimax-h3-Turbo` 仓库时发现铁证：

| 文件 | size | sha256 |
|---|---|---|
| `minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors` | 1,956,193,000 | `2339acdf…` |
| `minimax_h3_fl2v_turbo_8step_v1.0_768p_comfyui_bf16.safetensors` | **1,956,193,000** | `08cfe946…` ← **不同** |

**两个文件字节数完全相同，内容却不同。** 这意味着任何"按大小核对"的流程都可能放过一个错版本。**本项目一切模型验证一律以 sha256 为准**（这条已写进部署指南验收项）。

---

## 3. 独立性方案：项目自持

### 3.1 目录结构（目标态）

```
/root/autodl-tmp/h3p/                  ← 项目根，本项目独占
├── comfy/                             ← ComfyUI 代码副本（官方发布版，custom_nodes 留空 = 纯官方节点）
├── models/                            ← 全部真文件，零软链
│   ├── diffusion_models/  minimax_h3_fl2va_pruned_int8_convrot.safetensors
│   ├── text_encoders/     qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors
│   ├── vae/               minimax_h3_video_vae_fp16.safetensors
│   │                      minimax_h3_audio_vae_fp32.safetensors
│   ├── loras/             minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors
│   └── embeddings/        （预留：10 个官方风格 embedding，可选）
├── input/ output/ temp/               ← 生成素材与产物
├── logs/                              ← 运行日志、实验记录
├── models_manifest.txt                ← 独立资产凭据（文件 / 获取方式 / sha256）
└── models/（find -type l 必须为空）    ← 独立性硬指标
```

### 3.2 独立性边界（说明白，免得误解）

| 层 | 是否独立 | 说明 |
|---|---|---|
| ComfyUI 代码 | ✅ 独立 | 项目自己的副本 |
| 模型权重（4 件 + LoRA） | ✅ **独立（本次改变）** | 真文件，不软链、不依赖共享库 |
| 输入输出 / 日志 / 商品素材 | ✅ 独立 | 全在项目根内 |
| 自定义节点 | ✅ 独立（且为空） | 只用官方核心节点 |
| Python 运行时 / CUDA 驱动 / conda 环境 | ❌ **共用** | 属系统级环境，重装无隔离收益、风险高、耗时数小时 |

> 判定标准：**其他项目对共享模型库做任何操作（改动、替换、删除），本项目都不受影响。** 这是"不共享"的可验证定义。

### 3.3 为什么不许去改共享库

`/root/autodl-tmp/ComfyUI-models/` 被 `aisiyi-comfyui` 等项目通过 `extra_model_paths.yaml` 共同指向。**动它会伤到别人**——这正是"项目自持"路线的理由，而不是去替换共享库里的文件。

---

## 4. 两档获取方式（实测数据支撑）

执行脚本：`h3-pipeline/deploy_independent.sh`（已随技能包归档）

| 档位 | 参数 | 做法 | 实测耗时 | 适用 |
|---|---|---|---|---|
| **auto（默认）** | `deploy_independent.sh auto` | 先算共享库同源文件 sha256；**等于官方值则同机复制**，不等则自动转下载 | **≈1 分钟**（复制 33 s + 校验） | 推荐：已有证据证明源文件 = 官方原件 |
| copy | `... copy` | 强制复制；源哈希不符即拒绝执行 | ≈1 分钟 | 需要"确认来源后才复制"的强制门 |
| **download** | `... download` | 从 `hf-mirror` 官方源全新下载 5 个文件 | **≈48 分钟** | 要求来源链完全自证、不信任同机文件时 |

实测速度：数据盘写入 **1.3 GB/s**、顺序读 **5.8 GB/s**、`hf-mirror` 下载 **14.9 MB/s**。

> **一句话判断**：sha256 一致 ⇒ 复制产物与下载产物是**同一串比特序列**，物理上不可区分。区别只在"用了哪条路"。用 1 分钟换 48 分钟是纯赚——但只要你想让来源链完全自证，`download` 档随时可用。
> ※ 脚本是**幂等**的：已存在且哈希正确的文件会跳过；若已用复制档落盘、之后想改走全新下载，需先删除对应副本再以 `download` 重跑。

### 4.1 空间核算（已核）

| 项 | 值 |
|---|---|
| 数据盘 `/root/autodl-tmp` | 300 GiB 总，**97.4 GiB 可用** |
| 本次需落盘 | **≈39.5 GiB**（四件权重，十进制 42.5 GB）+ 8 步 LoRA 1.9 GiB（已落） |
| 落盘后剩余 | **≈56 GiB** ✅ 安全 |

各文件体积：FL2VA **20.97 GB** / 文本编码器 **15.69 GB** / 视频 VAE **5.21 GB** / 音频 VAE **0.61 GB** / 8 步 LoRA 1.96 GB。

---

## 5. 正式部署执行清单（照做即可）

| 步 | 命令 / 动作 | 验收 |
|---|---|---|
| ① | 上传 `deploy_independent.sh` 至服务器 | 文件在位 |
| ② | `chmod +x deploy_independent.sh && ./deploy_independent.sh auto` | 5 个部件**全部** `✅ sha256 校验通过`；末行 `全部部件就绪，项目模型已完全独立` |
| ③ | `find /root/autodl-tmp/h3p/models -type l \| wc -l` | 输出 **0**（零软链 = 真独立） |
| ④ | `cat /root/autodl-tmp/h3p/models_manifest.txt` | 5 行部件 + 获取方式 + sha256 齐全 |
| ⑤ | 归档 `models_manifest.txt` 到本项目工作区 | 作为独立资产凭据留痕 |

失败处理：脚本对每个部件独立处理，**任一件失败不会污染其他件**；失败件保留 `.part` 支持断点续传重跑；哈希不符的文件会被自动删除（不让可疑文件留在库里）。若下载中断，重跑同一命令即可续传。

---

## 6. 两个待确认的阻塞项（不在本报告可自行决定的范围）

| # | 事项 | 为什么必须处理 | 性质 |
|---|---|---|---|
| **A** | 系统盘 `/` **30 GB 100% 满（剩 172 MB）** | 元凶是 `/root/带货_teardown_backup_20260916.tgz`(**14.4 GB**) + 3 个 ecom 备份(**3.0 GB**)。不清理时，ComfyUI 启动、日志写入、临时文件、装包都可能直接失败 | ⚠️ **破坏性，需辉哥确认**（是否还有其他副本） |
| **B** | 获取方式认哪一档（auto 复制 ≈1 min / download ≈48 min） | 影响部署排期 | 决策 |

> 注：模型落盘本身**只写数据盘**，不受系统盘满影响；但**跑 ComfyUI 会受影响**，所以 A 是 Phase 0 剩下的真实阻塞项。

---

## 7. 与既有文档的关系

- 本报告 = 部署指南 **Phase 0 步骤 ③b** 的展开，指南已同步为 **v2.4**（新增 §1.1「模型独立性」硬规范 + 验收项「模型独立已落实」）。
- 上游证据：**REPORT16**（服务器模型与链路审计 —— 定论「不是模型文件错，是加载链路错」）。
- 技能包已同步：`SKILL.md` 新增《链路审计修正 §7 · 项目模型完全独立（v2.4 生产规范）》。
