# REPORT28 · L2 判据升级（R-04/R-08 拆分 + P-05 macro 同源）与 D 片真缺陷定位

> 上游：REPORT27（L2 判官模型切换到 `tt-5.6-luna`）
> 触发：模型切换后对 C2 / D 复检，**D 片被判 REJECT**
> 日期：2026-09-18　｜　执行：金刚
> **结论：① 判据层修掉两处缺陷（R-04 颗粒度错误 → 拆出 R-08；`--bg macro` 漏传给 L2 → P-05 强制 na）；② D 片的 REJECT 里，一条是判据越界、一条是纯误杀，但它揭示的画面问题是真的；真缺陷根因 = Shot 3 的构图声明与文字落位声明自相矛盾，修它需要重生成 D，等辉哥拍板。**

---

## 一、起因

辉哥指令换掉 L2 判官模型（`doubao-seed-2-1-pro` → `tt-5.6-luna`）后，按 REPORT27 §六 的规矩「**凡在 L2 缺席期间通过验收的片子都要重跑一遍**」，对 C1 / C2 / D 三镜复检：

| 镜 | 复检结果 |
|---|---|
| C1（棚拍跑步，声明已修） | ✅ PASS |
| C2（户外散步） | ✅ PASS（L2 `yes=16 / no=0 / na=1`） |
| **D（产品动态卖点，六分镜）** | ❌ **REJECT** —— `R-04`、`P-05` 各判 `no` |

---

## 二、D 片的 REJECT 必须拆成两层看

⚠️ 这是本轮最重要的认知：**「判官判了 no」≠「片子有缺陷」**。D 的两条 no 性质完全不同。

| 判项 | 票型 | 判官给的区域描述 | 性质判定 |
|---|---|---|---|
| **`R-04`** | 2/3 (yes,no,no) | *"upper-left overlay text 'Air Flows Right Through' sitting on the shoe mesh rather than the pale backdrop"* | ⚠️ **判据越界**（claim 只问逐字，判官判了位置）**＋它揭示的画面问题是真的** |
| **`P-05`** | 2/3 (no,yes,no) | *"left and top edges of sneakers cropped out of frame"* | ⚪ **纯误杀** —— D 声明 `--bg macro`，满画幅是**设计** |

### 2.0 终版判据复判结果（**判据修完之后再看 D**）

| 镜 | 判定 | 关键项 | 说明 |
|---|---|---|---|
| C1 | ✅ PASS | `R-08 na`（无文字） | 18 项全通过 |
| C2 | ✅ PASS | `R-08 na`（无文字） | 18 项全通过 |
| **D** | ❌ **REJECT** | **`R-08 no 2/3 (no,yes,no)`** | **真缺陷由正确的判项抓到** |
| | | `R-04 yes 3/3` | **逐字一致** —— 反证原 R-04 判 `no` 确属**越界** |
| | | `P-05 na 3/3` | `--bg macro` 同源生效（原判 `no` 确属**误杀**） |

⇒ **两条原判 no 的定性被实测证实**：`R-04` 是判据越界、`P-05` 是纯误杀；
而真缺陷（文字压在鞋身上）**换到 `R-08` 之后被判出来了** —— 这正是"把真缺陷交给正确的判项"的意义。

报告：`judge/C1v3_judge_luna_v2.json` / `judge/C2_judge_luna_v2.json` / `judge/D_judge_luna_v2.json`

### 2.1 `R-04` 为什么是「判据越界」

原 claim 原文：

> *Every on-screen text line visible in the frames **matches the text stated in the shot description word for word**. Answer na if the shot description declares no on-screen text and none is visible.*

claim 问的**只是逐字一致**。而 D 片两行叠字（`Air Flows Right Through` / `Soft Landing, Light As Air`）与提示词**逐字完全一致** ⇒ 按 claim 字面应答 `yes`。

判官却把提示词里的**落位要求**一起判了 —— 属**判据颗粒度错误**：一个 claim 里塞了两个事实（逐字 + 位置），任一不合就借 `R-04` 的名义否决整镜。

### 2.2 但它揭示的画面问题**是真的**（不能靠改判据掩盖）

逐字没问题 ≠ 落位没问题。见 §三 证据：**文字确实压在鞋身上**，且首帧还被左边缘裁掉一个字。

---

## 三、证据链（全分辨率人工核验）

### 3.1 帧号扫描

| 帧 | 时间 | 构图 | 文字 |
|---|---|---|---|
| f125 | 5.21 s | 中近景，**鞋上方有干净浅色背景带** | 无 |
| **f132** | **5.50 s** | 与 `anchor_2` 锚点**吻合**，背景带仍在 | 无 |
| **f140** | **5.83 s** | ⚠️ **已推成满画幅特写**（背景带消失） | ⚠️ 文字出现，**压在鞋身上**，首字被左边缘裁掉 |
| f155 | 6.46 s | 满画幅特写 | ⚠️ 文字横跨鞋身，`Flows` 段落**压在黑网格上**（深灰压深色，几乎不可读） |
| f170 | 7.08 s | 满画幅特写（Shot 3 尾） | ⚠️ 文字仍在鞋上 |
| f180 | 7.50 s | 满画幅特写 | 文字已按声明淡出 ✅ |
| f215/f230/f242 | 8.96–10.08 s | 中景，鞋上方有背景带 | `Soft Landing, Light As Air` **正确落在浅色背景上** ✅ |

> 证据图：`action/frames/D_R04_证据.png`（左列 = 声明构图与 5.50 s 实帧；右三列 = 文字压鞋的三帧）

### 3.2 提示词的原话（落位要求写得非常死）

> *The single English line "Air Flows Right Through" appears as an integrated on-screen text element **in that clean pale backdrop band above the shoes**, offset to the left, laid out on one single line and drawn in solid fully opaque lettering at roughly one tenth of the frame height, **standing directly on the pale backdrop** with strong contrast, **staying completely clear of the dark mesh and never overlapping, touching or drifting onto the shoes***

⇒ 声明要求「站在浅色背景带上、绝不与鞋重叠」，**实际把字压在了鞋上**。这是货真价实的**声明不符**。

---

## 四、根因：**构图声明与文字落位声明自相矛盾**

把 Shot 3 的两条声明放在一起看，矛盾立刻现形：

| Shot 3 的两条声明 | 冲突点 |
|---|---|
| ① 构图：*the camera arrives at the **close material framing** established by Picture 2 and stops there, with the open breathable weave … in **sharp detail*** | 满画幅特写 ⇒ **鞋填满整帧，上方不存在「干净背景带」** |
| ② 文字：*in that **clean pale backdrop band above the shoes** … never overlapping … the shoes* | 要求文字站在 → **那条带子已经不存在了** |

⇒ **两个声明互斥**。生成器无法同时满足，只能把文字丢到画面顶部 —— 而顶部正好是鞋。

**为什么 5.50 s 就吻合锚点、5.83 s 却成了满画幅？** 抽帧实测：`f132 (5.50 s) ≈ anchor_2`（中近景、有背景带），`f140 (5.83 s)` 已收紧为满画幅。即**锚点对齐没问题**（三个锚点 0.00 / 5.50 / 10.08 s 全部吻合），是**锚点之间的镜头走位**把构图推紧了 —— 而提示词那句 *"arrives at the close material framing … and stops there"* 要求它**停在** `anchor_2` 的构图上，实际却继续推近。

> 归类：与 C1 同属**「声明滞后/失配于实际设计」**，不是随机崩坏 ⇒ **按 SOP 属「禁止重抽」类，改的是声明（或设计），不是换 seed。**

---

## 五、修了什么（判据层，零 GPU 成本）

### 5.1 代码：`judge_shot.py`

| # | 改动 | 说明 |
|---|---|---|
| 1 | **拆项**：`R-04` 收窄为纯逐字一致；**新增 `R-08`** 只管落位是否合规 | 按 `judge_form.atomic`（一项 = 一个可观察的二值事实）拆成两个原子事实，**各判各的，不再互相借道**。判项总数 **17 → 18** |
| 2 | **`--bg` 传进 L2**：`l2(..., bg=a.bg)`；`--bg macro` 时 **`P-05` 强制 `na`** | 与 `L1-11` **同源**。修正前 `--bg` 只传给 L1，**L2 完全不知道背景声明** ⇒ 把「按设计发生的满画幅」当成「主体出框」误杀。强制 na 连 VLM 都不必调，顺带省 3 次调用 |
| 3 | `--bg` 参数说明同步（macro ⇒ L1-11 + L2 P-05 均 na） | 防后人踩同坑 |

> `judge_shot.py` md5 同步：`bde2755edb9709a300068cb8dad25ac2`（技能包 / `action/` 两份一致）
> 语法校验通过；判项集合实测 `R-01…R-06 + R-08 / W-01…W-07 / P-01…P-06` **共 18 项、无重复**（**R 段刻意跳过 R-07**，原因见 §5.3）。

### 5.2 文档

| 文件 | 改动 |
|---|---|
| `SKILL.md` | 判据措辞缺陷表**新增 R-04/R-08 行**；L1-11 可用性表**扩成三列**（加 L2 P-05 列）；成本数字 51 → **54 次/镜** |
| 本报告 | 新增 REPORT28；技能包 `reference/REPORT28.md` 同步 |


### 5.3 ⚠️ 编号坑：新判项**不能叫 R-07**（改名 `R-08`）

拆分时按顺序取名 `R-07`，随后核对配置发现**编号已被占用**：

| 来源 | R-07 的含义 |
|---|---|
| `judge_config.yaml:179` | 「**结尾镜是否单一全画幅（无网格/分屏）**」——官方硬要求 |
| `reference/REPORT15.md:104` | 同上（配置总表） |
| 我最初的新增项 | ~~「画面文字落位是否合规」~~ ← **同名不同义，会让审计对不上号** |

⇒ **改名 `R-08`**，**R 段编号刻意跳过 R-07**。

> ⚠️ **顺带发现的既有缺口（本次未补）**：config 定义的 **R-07 在 `judge_shot.py` 里从未实现**（旧版只做到 R-06）⇒「结尾镜出现网格/分屏」这一失败类**目前无判据覆盖**。
> 为什么本次不补：它是**结构性判项**，误报代价高，**必须先标定**（对照正负样本定阈值/措辞）才能上线 —— 与 `R-01`/`R-05` 两次措辞修正同源教训：**未标定的判据会误杀好片**。

**顺带扫出两处相邻的既有问题（本次只记录，未改）**：

| # | 位置 | 问题 | 建议 |
|---|---|---|---|
| 1 | `gate_p_image_gate.html` | **图片闸门也有一个 `P-05`，含义是「首尾同源」** —— 与视频判官的 `P-05`「主体不出框」**同名不同义**（两个不同闸门各自成表，暂不冲突，但跨表审计易混） | 给图片闸门加前缀（如 `GP-05`）或统一编号空间 |
| 2 | `breakdown_triage.html:44` | 「短英文文字糊」映射到 `L1-06 + R-04`。**R-04 收窄后只管逐字一致，判不了"糊/不可读"** ⇒ 该失败类的判据归属需重新对应 | 文字**可读性**交给 `R-08`（要求保持强对比、避开深色区）或 `P-03`；映射表同步 |

### 5.4 ⛔ 顺带修掉一个**投票聚合的真 bug**：并列票被静默判成 `yes`

对 D 用 18 项新判据复判时，`R-03` / `R-08` 都出现 **`(na,yes,no)` 三票各不同**，报告却写 `yes 1/3`。追下去发现是 `l2()` 的聚合写法有问题：

```python
top = max(cnt, key=lambda v: cnt[v])     # ❌ cnt = {"yes":1,"no":1,"na":1}
```
`max` 在**并列**时按 dict **插入顺序**返回第一个键 ⇒ 恒为 **`yes`**。

| 影响 | 说明 |
|---|---|
| 与设计文档**正好相反** | 文档写的是「三票分歧 ⇒ 判 `na` + `confidence=low`，交人工，**不否决**」，实现却判 `yes` |
| 对 **R/W 段致命** | R/W 是「任一 `no` 即否决」，把分歧吞成 `yes` = 把「**判官其实拿不准**」洗成「**判官确认没问题**」⇒ 假通过 |
| 触发频率 | 实测 D 片一次复判就中 2 项（`R-03`/`R-08`） |

**修法**：抽出 `_tally(vs)` 统一处理 —— 并列最高票 ⇒ `top="na"`；报告写 `"tie": true`；聚合层**显式告警**「三票分歧（已判 na，**非**判定为合格）→ 必须人工复核」。
**验证**：`_tally` **8/8 单测通过**（含 `(na,yes,no) → na, tie=True`、`(yes,yes,no) → yes`、`(yes,na,na) → na`）。

### 5.5 ⚙️ R-08 措辞**重新标定**（第一版不可靠）

第一版 R-08 写成「落位是否符合声明的落位要求（站在哪个面、是否须避开产品）」—— 要求判官**先从长句里抽取要求、再回画面比对**，是**两步推理**。在**已知缺陷**上实测：`(na,yes,no)` 三票散开 ⇒ **既抓不到缺陷**。

⇒ 改成**单一可直接观察的事实**：

> *Answer no if any visible on-screen text line is drawn on top of the product itself, meaning any part of the lettering overlaps the sneakers instead of standing on the plain backdrop. Answer na if no on-screen text is visible in the frames, or if the shot description does not state where the text is supposed to stand.*

**标定结果**（`judge/calibrate_r08.py`，两头都要对才准上线）：

| 片 | 用途 | 票型 | 判定 | 期望 | 结论 |
|---|---|---|---|---|---|
| **D**（文字压在鞋身上，已知缺陷） | 抓得到吗 | `no×5, yes×1` | **`no`** | `no` | ✅ **抓到了** |
| **C1**（全程无画面文字） | 会误报吗 | `na×3` | **`na`** | `na` | ✅ **不误报** |

> 唯一那票 `yes` 的 region 是 *"bottom third, text below sneakers on backdrop"* —— 判官把它当成了 Shot 5 那条**本来就合规**的 `Soft Landing, Light As Air`，属抽样串扰；多数票（5/6）判对。
> 标定同时再次印证：`rtok>0` 的票（1584/1808/2116/2344）就是被上游忽略思考链的**慢档**。

---

## 六、待辉哥拍板：**D 片怎么修**（需重生成，故不擅动）

> **当前状态**：终版判据下 D **REJECT 于 `R-08`（2/3）**，且 `R-08` 已通过两头标定（已知缺陷 `no 5/6`｜无文字片 `na`）⇒ **这是判官抓得住的真缺陷**，不是判据误杀。
> 三个选项的取舍与下面一致；**判官不会因为选了 C 就"看不见"** —— 选 C 必须显式放宽 `R-08`。

前提：**文字是 H3 生成时烘焙进画面的**（已核实 `post_shop/overlay/` 只含三条中文叠字 PNG，两条英文文案不在其中）⇒ **改后期素材救不了，必须重生成 D**。

| 选项 | 做法 | 代价 | 评价 |
|---|---|---|---|
| **A（推荐）** | **把这条英文从 Shot 3 挪到 Shot 2**（2.5–5.5 s 中景推进段，鞋小、上方确有干净背景带）；Shot 3 声明 *no on-screen text* | 重生成 D 一次（GPU ≈10 min） | ✅ 保留「画面内英文进 prompt」既定架构；✅ 消除互斥声明；✅ 文案语义（透气）仍挨着材质段 |
| **B** | **两条英文全部改后期叠加**：prompt 全部声明 *no on-screen text*，英文由 `post_shop` 后期叠加 | 重生成 D 一次 + 写叠字 | ✅ 位置 100% 可控、永不再和生成器搏斗；⚠️ 改动「画面内英文进 prompt 逐字」的既定架构（REPORT20/ClipForge 三分流） |
| **C** | **只修判据（已做），画面不动** | 零 GPU | ⚠️ 成片里「字压在鞋上、部分不可读」的瑕疵留存；需**显式接受**该瑕疵 |

> ⚠️ 无论选哪个，**`R-08` 都会把「字压鞋」如实判 `no`** —— 这是设计意图（把真缺陷交给正确的判项去抓），不是障碍。
> 若选 C，等价于**接受该瑕疵**并放宽 `R-08`；建议明确记录这个取舍。

---

## 七、验收清单

- [x] `R-04` 收窄为纯逐字一致；`R-08` 新增且仅管落位（**编号避开 config 既有的 R-07**）
- [x] `--bg macro` 时 **L2 `P-05` 强制 `na`**（与 L1-11 同源），零 API 调用路径实测生效
- [x] 判项集合 18 项、无重复；语法校验通过
- [x] 两份 `judge_shot.py` 副本 md5 一致（`bde2755e…`）
- [x] `SKILL.md` 三处同步（措辞缺陷表 / L1-11 可用性表 / 成本数字）
- [x] D 片 REJECT 的两条 no **逐条定性**（判据越界 vs 纯误杀），并定位真缺陷根因
- [x] D 片证据图产出（`frames/D_R04_证据.png`）
- [x] **C1 / C2 / D 用 18 项新判据复检** —— C1 ✅ PASS / C2 ✅ PASS / **D ❌ REJECT（`R-08`）**
- [x] 投票聚合并列票 bug 修掉（`_tally`，**8/8 单测**）
- [x] `R-08` 措辞重新标定（已知缺陷 `no 5/6`｜无文字片 `na 3/3`）
- [x] D 片修复方案草稿就绪：`prompt/D_product_dynamic_v2.txt`（选项 A，**未执行**）
- [ ] **辉哥对 D 片修复方案拍板**（§六 A / B / C）
- [ ] 拍板后重生成 D 并复判至 PASS

---

## 附：一句话总结

**判官判 `no` 有两种：一种要修片，一种要修判据 —— 而这一轮的 D 片，两种同时出现了。**
