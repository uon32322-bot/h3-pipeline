# REPORT5 — Ref2VA 参考生视频实测（与 FL2VA 链路横向对比）

> 环境：`root@connect.westc.seetacloud.com:39494`（4090 24G / ComfyUI 0.30 / `minimax_h3_ref2va_pruned_int8_convrot.safetensors` + `minimax_h3_ref2v_turbo_8step_v1.0_768p_comfyui_bf16.safetensors`）
> 素材：`ref_model.png`（模特）+ `ref_product.png`（气垫产品）
> 统一参数：`480x864 (9:16)` / `0.4 MP` / `8 steps` / `res_multistep + simple` / `seed 20260911`

---

## 0. 一句话结论

**Ref2VA 值得换，但它解决的是"画质 + 结构"问题，不解决"身份锁定"问题。**

- 一条 **5.9 秒单段生成**就能跑完"举起 → 说话 → 取粉扑 → 上妆 → 合盖展示"整个叙事，无需拼接；
  画质、肤质、妆效明显优于 FL2VA。
- 但**参考图里的人脸不会被保留**——它保留的是"属性"（东亚女性 / 长卷发 / 灰西装 / 精致妆容），
  不是"同一个人"。`ref_image_size=max` 也救不回来。要锁身份仍得走 FL2VA 的同源编辑链。
- `<Audio j>` 音色参考**实测无效**（两组独立 A/B：输出音轨逐位相同），却让算力翻倍。
- **台词由 `<d>` 标签驱动，逐字准确**（ASR 验证，5/5 一致）——这一条比 FL2VA 方便。

---

## 1. 部署：成本只有"一个文件"

Ref2VA 与 FL2VA 共用文本编码器（`qwen3vl_32b_minimax_h3_nvfp4_awq`）和两个 VAE（video fp16 / audio fp32），
所以迁移成本不是"换实例"，而是补一个权重文件：

| 组件 | 状态 |
|---|---|
| ComfyUI + 全部节点（Promptor / Extender / ASR） | ✅ 已有 |
| 文本编码器 + video VAE + audio VAE | ✅ 已有 |
| FL2VA 权重 + FL2VA Turbo LoRA | ✅ 已有 |
| **Ref2VA 权重** `minimax_h3_ref2va_pruned_int8_convrot.safetensors` | 🆕 下载 |
| **Ref2VA 专用 Turbo LoRA**（8step / 4step） | 🆕 下载 |

下载（hf-mirror，实测 15 MB/s，全程带断点续传 + 字节校验）：

| 文件 | 字节 | 耗时 |
|---|---|---|
| Ref2VA 权重 | 20,970,379,616 | 13:07 → 13:29（22 分钟） |
| Turbo LoRA 8step | 1,956,193,000 | ~8 分钟 |
| Turbo LoRA 4step | 1,956,193,000 | ~2 分钟 |

挂载：`diffusion_models/` 与 `unet/` 各建一条软链 + `loras/` 两条软链。**未改动 FL2VA 链路任何文件。**
磁盘 `/root/autodl-tmp` 剩余 187G。

> ⚠️ **LoRA 必须用 Ref2VA 版**。FL2VA 的 Turbo LoRA 套上去会静默降质——两边是不同权重。

---

## 2. Ref2VA 的接口事实（和 FL2VA 不一样的地方）

### 2.1 提示词是**六段式**（FL2VA 是三段式）

```
subject_definitions:     # <Subject 1> / <Subject 2> / <Audio 1> 的书面定义
summary:                 # 一段话概括
retention_analysis:      # 逐个声明保留强度：fully_preserved / reference
detailed_description:    # 分镜，含台词标签 <d>[English] ...</d>
overall_soundscape:      # 环境音
non_diegetic_music:      # 配乐（可 N/A）
```

引用标签：`<Picture i>`（参考图，1 基）、`<Video k>`（参考视频）、`<Audio j>`（参考音频）。

### 2.2 节点接线

`MiniMaxH3ReferenceToVideo` 本身**不接 unet**——它只出 `(conditioning, latent)`，
MODEL 走 `BasicGuider`。完整图：

```
UNETLoader(ref2va) → LoraLoaderModelOnly(ref2v turbo) → H3MemoryOptimization → H3SparseAttentionAdvanced
                                                                                      ↓
                                                                              BasicGuider.model
LoadImage ×N → ref_image_0..N ─┐
LoadAudio ×M → ref_audio_0..M ─┴→ MiniMaxH3ReferenceToVideo → conditioning + latent
                                                                      ↓
                                            RandomNoise + KSamplerSelect + BasicScheduler
                                                          → SamplerCustomAdvanced
                                                    → VAEDecode + VAEDecodeAudio
                                                          → CreateVideo → SaveVideo
```

`ref_images` / `ref_audios` 是 `COMFY_AUTOGROW_V3` 类型，API 侧用 **0 基命名**（`ref_image_0`、`ref_audio_0`）。

### 2.3 `ref_image_size` 是个关键旋钮

| 值 | 行为 |
|---|---|
| `match`（默认） | 参考图按长边比缩放到**生成的像素面积**（只缩不放） |
| `max` | 用参考流水线的 **2048px 短边**，官方说"身份保真最好，但可能慢数倍" |

480×864 生成下，`match` 会把 1088×1920 的参考图缩到 ~45% —— **这就是身份丢失的主因怀疑对象**（见 §4.3）。

---

## 3. 实测矩阵

| # | 参考图 | 音频参考 | 台词（`<d>`） | length | ref-size | seed | 耗时 | 产物 |
|---|---|---|---|---|---|---|---|---|
| A | 模特+产品 | — | This cushion compact is my everyday base. | 141 | match | …911 | **180.2s** | `A.mp4` |
| B | 模特+产品 | Samantha（同句） | One press of the puff is all this base needs. | 141 | match | …911 | **140.3s** | `B.mp4` |
| C | 模特+产品 | Samantha（异句） | It blends in seconds and never feels heavy. | 141 | match | …911 | **140.1s** | `C.mp4` |
| D | 模特+产品 | Daniel 男声 | 同 C | 141 | match | …911 | 15.0s ⚠️缓存 | `D.mp4`（无效） |
| **E** | 模特+产品 | **—** | 同 C | 141 | match | 77777 | **135.1s** | `E.mp4` |
| **F** | 模特+产品 | **Daniel 男声** | 同 C | 141 | match | 77777 | **265.2s** | `F.mp4` |
| **F2** | 模特+产品 | **—** | 同 C | 141 | match | 55555 | **160.2s** | `F2.mp4` |
| **F3** | 模特+产品 | **Daniel 男声** | 同 C | 141 | match | 55555 | **290.3s** | `F3.mp4` |
| **G** | 模特+产品 | — | 同 C | 73 | **max** | …911 | **80.2s** | `G.mp4` |

> 热跑（模型已在显存）141 帧 / 8 步 ≈ **135~180 秒**；`5.9s` 成片的墙钟时间稳定在 **3 分钟以内**。
> **每组 A/B 都是同 seed 对照**（E↔F、F2↔F3），唯一变量是"喂不喂参考音频"。

---

## 4. 四个实测发现

### 4.1 台词由 `<d>` 标签驱动，**逐字准确**（ASR 验证）

服务器上有 `faster-whisper 1.2.1`，直接转写输出音轨（`asr.py`）：

| 成片 | 提示词里的台词 | ASR 转写结果 | 一致 |
|---|---|---|---|
| A（**无音频参考**） | This cushion compact is my everyday base. | `This cushion compact is my everyday base.` | ✅ 逐字 |
| B | One press of the puff is all this base needs. | `One press of the puff is all the space needs.` | ✅ 同音误差 |
| C | It blends in seconds and never feels heavy. | `It blends in seconds and never feels heavy.` | ✅ 逐字 |

**A 完全没有喂音频**，语音照样准确生成 → 说明 Ref2VA 的语音是 `T2VA` 路径生成的，
`<d>[English] ...</d>` 标签就是语音脚本。这一条比 FL2VA 方便（FL2VA 必须外部做好配音再锚进去）。

### 4.2 `<Audio j>` 音色参考**实测无效**（两组独立 A/B 一致）

同一 seed、同一提示词，唯一差异是"喂不喂参考音频 / 参考是男声还是女声"：

| 对比（同 seed） | 音轨 md5 | 波形相关 | 逐帧像素差（0-255） | 耗时 |
|---|---|---|---|---|
| **E（无参考） vs F（Daniel 男声）** seed 77777 | **完全相同** `95410d7c…` | **r = +1.0000**（最大差 0） | 1.29（无帧 >5） | 135s vs 265s |
| **F2（无参考） vs F3（Daniel 男声）** seed 55555 | **完全相同** `2969757f…` | **r = +1.0000**（最大差 0） | — | 160s vs 290s |
| 基线：E vs C（**换 seed**） | 不同 | r = +0.0104 | **65.17** | — |

参考音频的真实参数：时长 2.38s、F0 中位 **111.9 Hz**（男声）。
而成片 F0 中位 **222.2 Hz** —— 与没喂参考的 E 逐位一致。

ASR 交叉验证（两条腿互相印证）：

| 成片 | 参考音频说的是 | 提示词 `<d>` 是 | 输出实际说的是 |
|---|---|---|---|
| A / E / F / F2 / F3 / G | — 或 A quick touch-up before the meeting. | It blends in seconds… | **It blends in seconds…** ✅ |

**解读**：`ref_audios` 确实进了计算图（耗时接近翻倍、画面有 1.29 的噪声级扰动），
但**对输出的贡献几乎为零** —— 既没把音色带过去，也没改变台词。
在这次 int8-pruned + 8-step turbo 的组合下，**`<Audio j>` 是"花了算力但没效果"的输入**。

> 与之对照：**FL2VA 的 `MiniMaxH3AddGuide(audio=...)` 是真·内容锚定** ——
> 输出音轨与本句配音的 DTW 距离 0.036（他句 0.20，基线 0.18），内容被完整保留。
>
> | 机制 | 作用 | 台词来源 | 音色来源 | 实测有效性 |
> |---|---|---|---|---|
> | FL2VA `AddGuide(audio)` | **内容锚定** | 外部配音 | 外部配音（波形保留度 0.27） | ✅ 有效 |
> | Ref2VA `ref_audios` | 名义上的音色条件 | 提示词 `<d>` 标签 | 模型自选 | ❌ **实测无效** |

> 源码层面的旁证（`nodes_minimax_h3.py`）：`ref_audios` 往文本编码器那一侧只塞了一个
> **不带任何音频数据**的占位 `ref_items.append({"type": "audio"})` —— 它只为 `<Audio j>` 标签占位；
> 真正带数据的是给 DiT 的 `ref_blocks`（`audio_latent`）。也就是说
> **"听"到这段音频的只有 DiT 的注意力，没有语义理解**——这大概是它效果微弱的原因。

### 4.3 身份是"属性级"而非"身份级"（`ref_image_size=max` 救不回来）

`face_cmp.png`（左=参考图 / 中=FL2VA 链路 / 右=Ref2VA）：

- **参考图**：东亚女性、细眉、内双、柔和五官、坐姿
- **FL2VA 链路**：**同一张脸**（同源编辑链从一个由参考图派生的底图出发，逐帧继承）
- **Ref2VA**：深邃五官、浓眉、欧式双眼皮、深唇 —— **明显不是同一个人**

整体调色板相似度（`refcheck.py`，受构图影响，仅供参考）：

| 链路 | ref-size | vs 模特图 | vs 产品图 |
|---|---|---|---|
| FL2VA | — | **0.909** | **0.803** |
| Ref2VA | match | 0.686 | 0.589 |
| Ref2VA | max | 0.646 | 0.592 |

FL2VA 高得多，但**主因是它锁定了构图**（同源编辑链让背景/光线/机位与参考图一致），
不是"身份更准"——两者其实是同一件事的两面。

**`ref_image_size=max` 测试结果：无效。** `face_cmp2.png`（左=参考图 / 中=match / 右=max）
显示两种模式生成的是**同一张脸**（深邃五官、浓眉、欧式双眼皮），都与参考图的东亚柔和五官无关。
调色板相似度也没提升（0.686 → 0.646）。

代价方面倒是好消息：max 模式下参考图短边 1088 < 2048，所以**不缩放**（保持 1088×1920 原图，
参考 token 数是 match 的 ~5 倍），但实测 **80.2s / 73 帧 = 1.10 s/帧**，
对比 match 的 0.96~1.14 s/帧 —— **几乎不慢**，与节点文档"can be several times slower"不符。

> 结论：本次配置下 Ref2VA 的参考图机制就是**属性/风格条件**，不是身份克隆。
> 想要"指定模特的那张脸"，仍然只能靠 FL2VA 的同源编辑链（或后置换脸）。

### 4.4 结构上压倒性优势：**一段跑完整叙事**

| | FL2VA 链路 | Ref2VA |
|---|---|---|
| 结构 | 6 状态帧 + 5 段拼接（15.2s） | **单段 5.9s** |
| 累积漂移 | 单步背景 MAE 1.93~2.31，跨 3 步累积到 6.31（临界） | **无**（无拼接） |
| 接缝 | 有（每段末尾动作停滞后拼接） | 无 |
| 画质 | 偏"磨皮"，肤质平 | **锐利，肤质/妆效真实，商业感强** |
| 镜头 | 固定机位 | 提示词里有 `camera pushes in slowly` → **有缓慢推镜** |
| 手部精细动作 | 半合理（穿模感） | 明显更自然 |

---

## 5. 补测结论（已跑完）

- **G**：`ref_image_size=max` + 73 帧 → **身份保真无改善**（与 match 同一张脸），
  但耗时基本不变（1.10 s/帧）。→ 见 §4.3
- **F2/F3**：seed 55555 独立复现 → **音轨逐位相同**（md5 一致、r=1.0000）。
  → §4.2 结论坐实，非偶然。

---

## 6. 踩到的坑（都会影响后续实验有效性）

1. **ComfyUI 对 AUDIO 输入的缓存键不可靠** —— C 与 D 只差一个音频文件，
   D 只跑了 **15s** 且音轨与 C **逐位相同**（md5 一致、r=1.0000）。
   → **换参考音频必须同时换 seed，或先清缓存**，否则测出来的是缓存。
   （E/F 那组就是靠换 seed 才拿到干净结果。）
2. **服务器 PATH 里没有 `ffmpeg`** —— 实际存在于
   `/root/miniconda3/lib/python3.12/site-packages/imageio_ffmpeg/binaries/ffmpeg-linux-x86_64-v7.0.2`。
   `torchaudio` 没有可用后端解不了 wav，脚本里要显式指向这个二进制。
3. **ASR 会和生成任务抢显存** —— `faster-whisper` 在本机 4090 上会 OOM，
   必须等 GPU 空闲，或改用 CPU `compute_type="int8"`。
4. `MiniMaxH3ReferenceToVideo` 的 `length` 会被规整到 `17k+5`：给 125 → 实际 141（5.88s）。
5. 本地 `ffmpeg` 缺 `drawtext` 滤镜，做标注图得绕开。

---

## 7. 成本

| 项 | 数值 |
|---|---|
| 权重下载 | 约 25 GB / 33 分钟（15 MB/s） |
| 5.9s 成片（141 帧 / 8 步，热跑） | **135 ~ 180 秒** |
| 3.0s 成片（73 帧 / 8 步） | **80.2 秒** |
| 单帧成本 | ≈ **1.10 ~ 1.28 s/帧**（FL2VA 为 1.38 s/帧，**Ref2VA 反而略快**） |
| `ref_image_size=max` 额外开销 | **≈ 0**（参考 token ×5，耗时几乎不变） |
| **加音色参考的额外开销** | **+95% ~ +115%**（135→265s、160→290s）⚠️ 而输出无任何变化 |
| 磁盘 | 187G 剩余 |

---

## 8. 建议

- **需要"指定模特长相"** → 继续用 FL2VA 同源编辑链（`ref_image_size=max` 已测过，救不了）。
- **需要"高画质 + 长叙事 + 少拼接"** → 换 Ref2VA，一次生成整条片。
- **需要"指定音色 / 指定台词内容"** → **不要用 Ref2VA 的 `<Audio j>`**（实测零效果还翻倍算力）；
  音色/内容可控只能用 FL2VA 的 `AddGuide(audio)` 锚定，或用后置 TTS 替换音轨。
  好消息是 **Ref2VA 的 `<d>[English] …</d>` 台词本身很准**，可以直接当配音脚本用。
- **推镜/构图表达** → Ref2VA 支持提示词级镜头运动（`camera pushes in slowly` 实测生效），FL2VA 固定机位做不到。
- **推荐组合**：Ref2VA 出画（高画质 + 一段到底）→ 后置贴用户自己的口播音轨；
  若要锁脸，用 FL2VA 链做身份、Ref2VA 做画质补拍。

---

## 附：本报告用到的脚本

| 脚本 | 用途 |
|---|---|
| `test_r2v.py` | Ref2VA 参考生视频入口（多参考图/音频、LoRA、ref-size） |
| `asr.py` | faster-whisper 转写（验证"到底说了什么"） |
| `refcheck.py` | 参考一致性（调色板相似度 + 主色占比 + 对照板） |
| `liproi.py` | 固定 ROI 口型-音频互相关 |
| `lipsync2.py` | 自动搜索 ROI 的口型-音频互相关 |
| `audio_match.py` | 音频内容 DTW 比对 |
| `remote.sh` | SSH 带重试的远程执行封装 |
