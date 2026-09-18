# Hypit × MiniMax H3 可行性评估

> 评估对象：`github.com/hypit-ai/hypit`（Hypit.AI）
> 评估时间：2026-09-17
> 证据来源：仓库源码 + 官方文档（均为当日拉取的真实文件），非二手转述
> 仓库快照：创建 2026-07-29，**6,177 stars / 741 forks**，TypeScript，最后提交 2026-09-16

---

## 结论速览

**一句话：这不是"能不能结合"的问题 —— H3 已经是 Hypit 的一等公民，官方包里就写着 `@hypit/minimax-h3`。收益是真的，但收益的位置和你想的不一样：它补的是"编排与复用"，不是"模型能力"。真正的拦路虎是许可证，不是技术。**

| # | 判定 | 证据等级 |
|---|---|---|
| 1 | ✅ H3 是 Hypit 官方支持模型，非 DIY 集成 | **源码确证** |
| 2 | ✅ 通过它你能拿到本地 ComfyUI **拿不到**的 3 项 H3 能力（参考视频/2K/15s） | **规格确证**，效果未实测 |
| 3 | ✅ 收益真实存在，但集中在编排层（词锚定时间线、批量变体、资产复用、成本闸门） | **文档确证** |
| 4 | ❌ 它**不解决**崩坏（B/C 类照旧），也**不解决**身份保持 | 原理推定 |
| 5 | ⚠️ **多租户 SaaS 被许可证明令禁止** → 你的一键成站网站必须先买商业许可 | **许可证原文确证** |

---

## 一、Hypit 到底是什么（先纠正一个常见误读）

官方定位原文：

> Hypit gives AI agents a language and system to create video. Drop in a video, and your agent clones it as a complete workflow: footage, captions, B-roll and effects, **all anchored to words instead of seconds**.

**它不是视频生成模型，是一个"视频编译器 + 编排系统"。** 技术形态是：

| 组成 | 内容 |
|---|---|
| 语言 | **SVML**（源文件，描述画面/字幕/B-roll/特效） + **SVS**（组件包，可复用 kits） |
| 编译器 | 把 SVML 编译成可渲染的执行图 |
| 渲染 | `@hypit/provider-hyperframes-local` —— **headless Chromium 并发渲染 + FFmpeg**（示例用到 64 进程） |
| 生成（可选） | 通过 Provider 调外部模型：`gpt-image` / `seedance` / `seedream` / `nano-banana` / `grok-imagine` / `mimo-speech` / `fishaudio-speech` / `elevenlabs-speech` / **`minimax-h3`** |
| 对齐 | WhisperX 逐词时间戳 |
| 后端能力 | `background-removal`、`volcengine-matting`（抠像）、`yt-dlp`（下载参考片）、`browser-capture` |

⭐ **关键区分：官方明说"生成模型不是必需的"** —— 只做字幕、动效、代码渲染画面，可以不调任何生成模型，编译成一条成片，模型服务费为 0。

### 「Clone」的真实含义（这点最容易误读）

**Clone ≠ 把参考视频的像素喂给生成模型做替换。**
Clone = **Agent 看片 → 理解结构 → 反工程成 SVML → 用你自己的素材重新填充**。

官方快速开始原文：

> Agent 会**理解原片为什么有效**，包括图形、字幕如何落在具体词语上，再根据你的目标**改写剧本、设计画面**。

所以「换产品」的过程是：结构（镜头顺序/节奏/字幕落点/B-roll 时机/特效触发）复用 → 画面元素重新生成 → 用你的产品素材。

⚠️ **这与你之前设想的"抄对标视频 + 像素级替换"是两回事。** 但请注意：**这恰好是合规的做法** —— 它复用结构而不是复制画面，规避了直接的著作权/肖像侵权风险。这与 REPORT7 建议的「自建动作原子库 + 结构复用」方向一致。

---

## 二、⭐ 决定性发现：H3 已在官方发行版里

仓库里存在 `packages/minimax-h3/`，README 原文：

> Exact author/compute contracts and package-owned author Surfaces for MiniMax H3 video generation.

它把 H3 的**三种模式建模成三个独立 Surface**（而不是一个动态端口）：

```xml
<h3:TextVideo id="idea" prompt={prompt} duration="6" resolution="768P" aspect-ratio="9:16"/>

<h3:FrameVideo id="motion" prompt={motionPrompt} duration="6" resolution="2K"
  first-frame={cover.image} last-frame={ending.image}/>

<h3:ReferenceVideo id="montage" prompt={montagePrompt} duration="8" resolution="768P" aspect-ratio="9:16">
  <h3:Reference image={person.image}/>
  <h3:Reference video={gesture.video}/>
</h3:ReferenceVideo>
```

**并且 HypiHub（官方推荐的托管服务）把 `minimax-h3` 列为 canonical model name：**

> The mapping uses HypiHub's canonical model names: `gpt-image-2`, `seedream-5-lite`, **`minimax-h3`**, `grok-imagine-video` and the individual Seedance names. … Video requests use `/videos`, preserving reference images, **reference videos** and first/last frames in their distinct fields.

示例项目的默认 runtime profile 也已经直连：

```json
"endpoints": {
  "hypihub.default": { "use": "@hypit/provider-hypihub", "config": { "baseUrl": "https://hypit.ai" } },
  "media.local":     { "use": "@hypit/provider-media-local" },
  "hyperframes.local":{ "use": "@hypit/provider-hyperframes-local", "config": { "workers": 2 } }
}
```

⇒ **开箱即用的一等公民**，不是需要自己写适配的实验性接法。

### H3 在 Hypit 里的真实规格（源码 `minimaxH3Ports` 逐字抄录）

| 端口 | 类型 / 取值 | 数量限制 |
|---|---|---|
| `prompt` | text | 1，**≤7000 字符** |
| `duration` | integer | 1，**4–15 整秒** |
| `resolution` | enum `768P` \| `2K` | 0–1 |
| `aspectRatio` | enum `21:9` `16:9` `4:3` `1:1` `3:4` `9:16` | 0–1 |
| `referenceImage` | media:image | **0–9** |
| `referenceVideo` | media:video | **0–3** |
| `referenceAudio` | media:audio | 0–3 |
| `firstFrame` / `lastFrame` | media:image | 0–1 各 |

注释原文：

> `768P` and `2K` are the model's own two output tiers — H3-Base renders at 768p and H3-Regenerate-2K re-renders from the original context — so the spelling is MiniMax's, not any gateway's.
> What MiniMax H3 accepts is a property of the trained model, not of whichever service resells it.

⭐ **这段等于官方文档侧印证了我们 REPORT8 的三层架构结论**：`H3-Base`（768p，已开源，我们本地那套）+ `H3-Regenerate-2K`（2K，未开源，只能走 API）。

### ⚠️ 硬约束：帧锚定与参考锚定互斥（新发现，直接影响你的流水线）

源码 `requires` 段逐字：

| 约束 | 含义 |
|---|---|
| `atMostOneOf(referenceImage, firstFrame)` | 参考图 ↔ 首帧 **互斥** |
| `atMostOneOf(referenceImage, lastFrame)` | 参考图 ↔ 尾帧 **互斥** |
| `atMostOneOf(referenceVideo, firstFrame/lastFrame)` | 参考视频 ↔ 首尾帧 **互斥** |
| `atMostOneOf(referenceAudio, firstFrame/lastFrame)` | 参考音频 ↔ 首尾帧 **互斥** |
| `requiresAnyOf(referenceAudio) ← [referenceImage \| referenceVideo]` | 单给音频不行，必须配图或视频 |
| `atMostOneOf(aspectRatio, firstFrame/lastFrame)` | 首尾帧模式**不吃画幅参数**（继承图片） |
| `weightedTotal ≤ 12`（图/视频/音频各计 1） | 参考素材总预算 |

**这条对你的意义**：**同一次 H3 请求里，不能既用 FL2VA 首尾帧锚定、又用 Ref2VA 参考锚定。**
你之前实测的两条链路（同源编辑链 / 参考链）在 H3 层是**互斥的两种模式**，一条 workflow 里要用，必须拆成两个 Build。这是一条以前不知道、但会直接改变编排方式的事实。

---

## 三、能拿到的收益（分项，标注证据等级）

| # | 收益 | 具体内容 | 证据等级 |
|---|---|---|---|
| 1 | **词锚定时间线** | 特效/B-roll/字幕挂在**词**上而非秒上。改一句台词，时间轴与素材自动重排 | 文档确证 |
| 2 | **批量变体 + 资产复用** | 换 SKU / 换主播 / 换语言 / 换画幅只重跑变化部分，其余 Output 复用。官方口号「1 command, 100 variants」 | 文档确证（规模未经独立验证） |
| 3 | **成本闸门** | Build 前列出"用哪个账户、做什么、费率/预估费用、哪些还不确定"，**你确认预算才继续**。`hypit pricing <run>` | 文档确证 |
| 4 | **断点复用** | `Build Result` 保留已完成的 Output，后续 Run 复用适用的已有 Output | 文档确证 |
| 5 | **字幕/B-roll/特效/合成** | WhisperX 逐词对齐 + Chromium 并发渲染 + FFmpeg。这块自建极贵 | 源码确证 |
| 6 | **抠像能力（对"混合合成模式"有用）** | 内置 `background-removal` + `volcengine-matting` → REPORT9 里"产品静止 + 固定 alpha 叠加"可自动化 | 源码确证 |

### ⭐ H3 侧的三项"本地拿不到"的能力（这是最硬的一块收益）

| 能力 | 本地 ComfyUI（我们测过） | 经 Hypit → H3 API |
|---|---|---|
| **参考视频（动作参考）** | ❌ **实测零效果**（REPORT6：换人/换场景/时间反转/静态帧，输出音轨 PCM 逐位相同） | ✅ 端口存在，**≤3 段**（`<h3:Reference video=.../>`） |
| **2K 输出** | ❌ 天花板 768p（2K 依赖未开源的 Regenerate-2K） | ✅ `resolution="2K"` 端口存在 |
| **单条时长上限** | 我们实测 124 帧 ≈ 5.17s 为常规段长 | ✅ **4–15 整秒**，9 张参考图 |

⚠️ **边界必须说清**：我只是确认了**端口与规格存在**，Hypit 是**把官方 API 的能力暴露出来**。
「通过官方 API 的 `referenceVideo` 是否真能迁移动作」—— **我尚未实测**（本地那套已证无效，不能外推到官方 API）。
这正是 REPORT8 里留的那个待验证项，现在有了更顺手的验证路径。

---

## 四、拿不到的（诚实泼冷水）

| # | 拿不到什么 | 为什么 |
|---|---|---|
| 1 | **B 类崩坏（状态不守恒）** | 编排层不改模型能力。液量不减、盖子自合、用量不减照旧 —— 这是扩散模型无因果链的架构属性 |
| 2 | **C 类崩坏（接触物理）** | 同上 |
| 3 | **身份保持** | H3 是属性级不是身份级（REPORT5 已测）。Hypit 换的是"脸相关的生成素材"，不能保证还是同一张脸 |
| 4 | **像素级/语义级替换** | Clone 是结构反工程再重生成，不是图层替换 |
| 5 | **一键全自动出片** | Hypit 的设计假设是**有个 Coding Agent 在操作 + 人在预算闸门确认**。全自动要自己包一层 agent |

⇒ **闸门②（动作白名单 + 因果链否决）绝不能因为上了 Hypit 就卸掉。** 上轮那句结论继续成立：
**只装判官/只装编排、卸掉闸门②，是最坏的组合。**

---

## 五、⚠️ 许可证红线 —— 你的两条业务线结论完全不同

Hypit **不是标准 Apache-2.0**（GitHub 标记 `NOASSERTION`）。实际是以 Apache-2.0 为基础 + 额外条款。原文关键段：

> **Permitted without a commercial license**: obtaining Hypit and running it yourself on your own infrastructure; using it for **your own organization's work, including commercial work and work performed for your clients**; and **single-tenant deployments** operated by and for one organization.
>
> **a. Multi-tenant service**: Unless explicitly authorized … you may not use the Hypit source code, or any derivative work of it, to operate a **multi-tenant environment**, or to offer Hypit's functionality to third parties as a hosted, managed, or software-as-a-service offering.
> *Tenant Definition: one tenant corresponds to one workspace … Operating an environment in which **two or more parties outside your own organization hold separate workspaces** constitutes a multi-tenant service, **whether or not a fee is charged**. *

**逐条对照你的业务：**

| 你的业务线 | 形态 | 结论 |
|---|---|---|
| **电商带货视频 pipeline**（自己出片 / 给客户交付 / 内部工具） | **单租户** | ✅ **明确允许**（"work performed for your clients" 明确写入许可） |
| **一键视频生成网站**（注册登录 + 工作台 + 项目管理 + 消息中心） | **多租户** | ❌ **禁止** —— "两个及以上外部主体各自持有独立 workspace" 就是多租户，**且不论是否收费** |

另外两条：
- **禁止商业再分发**：不能把 Hypit 或其衍生作品作为组件打包卖给第三方（fork 自用可以）。
- **不得移除 CLI / 运行报告 / manifest 中的名称、LOGO、版权信息** —— 以及"任何由它们派生的用户可见界面"。对白标网站是产品级约束。
  （豁免：不向用户展示这些组件的用法不受限。）

✅ 好消息：**输出归你所有**，许可对产出内容无任何限制，可商用。

⇒ **对你的一键成站产品，Hypit 的结论是：先谈商业许可，再谈集成。技术上可行，法务上不通。**

---

## 六、成本与门槛

| 项 | 数值 |
|---|---|
| Hypit 内核 | **$0**（无席位费、无渲染费、无水印） |
| 官方示例单条成片总成本 | **$1.07 – $1.15**（20s / 18s / 26s 三条，主要是模型费） |
| 纯代码渲染工作流 | $0（不调生成模型） |
| 运行环境 | Node **≥22.15.0**（仓库 `.node-version` = **24.14.1**）、pnpm **10.33.0**、TypeScript |
| 渲染依赖 | headless Chromium（示例 64 并发）+ FFmpeg |
| 驱动方式 | **需要 Coding Agent**（Claude Code / Codex），或脚本化 CLI |

**CLI（可脚本化，有 `--json`）**：`check` / `plan` / `pricing` / `build`（`--follow`）/ `status`（`--watch`）/ `inspect` / `activity`

⚠️ **门槛的真相**：对一个已经在跑「本地 H3 + ComfyUI（Python）」的人来说，引入 Hypit 等于**再维护一套完全不同的技术栈（Node/TypeScript/pnpm/Chromium）**。这不是装个包的事，是加一条产线。

---

## 七、建议路线（三档，按成本排序）

### 第 0 档 —— 立刻可做，零成本，不碰许可证
```bash
npx skills add hypit-ai/hypit -g
```
然后拿**自己拍的**参考片跑一条 clone，验证「结构复用到底值多少」。这是唯一能低成本回答"值不值"的动作。

### 第 1 档 —— 最高性价比：只借 H3 的 API 能力，不借 Hypit
把 H3 官方 API（含 `referenceVideo` ≤3、`2K`、15s）接进**你自己的流水线**。这样你可以：
- 补上本地实测无效的「动作参考」
- 补上 768p 天花板
- 完全绕开 Hypit 的许可证

**这是我推荐的路线。** Hypit 最大的价值是对"没有自己流水线的人"，而你**已经有流水线了**。

### 第 2 档 —— 若要走 Hypit：先做两件事
1. 用 `hypit pricing` 实测你自己品类的真实单条成本（$1.15 是他们的示例，不是你的）
2. **网站产品线先谈商业许可**；带货交付线（单租户）可直接用

---

## 八、待验证清单（边界，未实测的不写结论）

| # | 待验证 | 方法 | 成本 |
|---|---|---|---|
| 1 | 官方 API 的 `referenceVideo` 是否真能迁移动作 | 经 HypiHub 或 MiniMax API 跑 A/B（同 seed 换参考视频） | 需 API 额度 |
| 2 | `2K` 档的真实画质增量 | 同提示词出 768P vs 2K 对比 | 需 API 额度 |
| 3 | HypiHub 目录当前是否真的提供 `minimax-h3` | 查目录（Provider 文档明说"部署的当前目录可能省略已实现的模型"） | 需账户 |
| 4 | Clone 的结构还原保真度 | 拿一条自己的爆款跑 clone，人工比对节奏/字幕落点 | 零 GPU |
| 5 | 「100 variants」在真实审核成本下的净收益 | 跑 10 个变体，统计人工审核耗时 | 零 GPU |

---

## 九、附录：关键文件与命令

**已验证的仓库路径（均为当日真实文件）**
| 路径 | 内容 |
|---|---|
| `packages/minimax-h3/src/index.ts` | **H3 端口表 + 三个 Surface 定义 + 互斥约束**（本次最关键，9.1KB） |
| `packages/minimax-h3/README.md` | H3 Surface 用法与三种模式说明 |
| `packages/provider-hypihub/README.md` | **`minimax-h3` canonical name 映射**、`/videos` 字段、模型映射表 |
| `LICENSE` | 许可证全文（多租户/再分发/LOGO 三条） |
| `docs/zh/quickstart.md` | Clone 的真实流程与预算闸门 |
| `docs/zh/guide/providers.md` | Model / Provider / Endpoint / Profile 四层与 BYOK |
| `examples/provider-package/README.md` | **自己写 Provider 的完整范式**（接本地 H3 走这条） |
| `examples/podcast/hypit.runtime.json` | 默认 runtime profile（hypihub + local media + hyperframes） |
| `.node-version` / `package.json` | Node 24.14.1 / pnpm 10.33.0 / Node ≥22.15.0 |
| `packages/seedance-kits/kits/motion-reference-v1.svs` | 「动作参考」kit（Seedance 版），可作 H3 参考视频编排的样板 |

**命令**
```bash
npx skills add hypit-ai/hypit -g          # 安装 skill
hypit check / plan / pricing / build      # 校验 / 计划 / 报价 / 构建
hypit build --follow                      # 跟随构建进度
hypit status --watch                      # 观察状态
hypit inspect --output <name>             # 检查产出
```

---

## 十、与既有报告的关系（本次对前作的修正）

| 前作结论 | 本次修正 / 补充 |
|---|---|
| REPORT8：官方是三层架构，本地只有 H3-Base | ✅ **得到官方文档侧印证**（Hypit 源码原文写明 768p=Base、2K=Regenerate-2K） |
| REPORT8：参考视频本地无效，但官方 API 支持 V2V | ✅ **找到可落地路径**：Hypit 已把 `referenceVideo`（≤3）建成正式端口 |
| REPORT6：Ref2VA 参考视频零效果 | ⚠️ **结论不变，但适用范围收窄**：仅指本地 ComfyUI 适配层；官方 API 路径**仍待实测** |
| REPORT9/10：文字/商标、多镜、素材用途等提示词手法 | ➕ **新增一条 H3 层硬约束**：帧锚定与参考锚定**互斥**，不能混用 |
| REPORT11：判官团不能替代闸门② | ✅ **加固**：Hypit 也不替代闸门② —— 编排层不改模型能力 |
