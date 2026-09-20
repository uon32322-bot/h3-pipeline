# ClipForge 本地改动存档

> ⚠️ **为什么有这个目录**：我们对 ClipForge（`hao:/root/clipforge_src/`）做了源码级改动，
> 但它**不在本仓库**，一旦重装/回滚/换机就会丢失。这里存档改动后的文件副本 + 完整记录。
>
> 存档日期：2026-09-20 ｜ 服务器：`hao` ｜ 路径：`/root/clipforge_src/`

---

## 一、改了哪两个文件

| 文件 | 存档副本 | 服务器路径 |
|---|---|---|
| prompts.ts | `clipforge_patches/prompts.ts` | `src/lib/script-engine/prompts.ts` |
| generator.ts | `clipforge_patches/generator.ts` | `src/lib/script-engine/generator.ts` |

**服务器上的备份**（如需回滚，按时间戳取最新的 `.bak-*`）：
```
prompts.ts.bak-20260920            改动前（09-20 早期）
prompts.ts.bak2-20260920           第二次改动前
prompts.ts.bak-beats-20260920      beats 字段文档改动前
prompts.ts.bak-copy2026            文案规则块改动前
prompts.ts.bak-struct2026          脚本结构规则块改动前
prompts.ts.bak-placeuniq           场所唯一规则改动前
generator.ts.bak-beatsfix          beats 透传改动前
```

---

## 二、prompts.ts 的 6 处改动

### 1. 【画面动作可生成性】规则 4：三步 → **≤2 步**（硬上限）
```
原：一拍内三步以上的手部动作链（禁止）
新：4. 【硬上限：一拍内动作 ≤ 2 步】只允许「一个主动作 + 一个收尾动作」。
    三步及以上的动作链（放入微波炉-关门-按按钮-取出-放桌上）**必须拆镜**
依据：实测 —— 一镜内 5 步动作在 8 秒里物理不可能，模型会跳步压缩，
     拍出「微波炉门还没开、杯子已经在里面」。
```

### 2. 【画面运动硬规则】（新增，对应官方 FL2VA §3.2 金样例结构）
要求：单镜内**不写时间戳**（用 `as… while… until… then…` 连续时序从句）；
禁「镜头运动 ≠ 画面运动」的写法；禁定格句（`then the shot holds…`）；禁参数堆砌。

### 3. `r34l1sm` 处理
从「强制写在首位」改为**默认剥离**。依据：官方 §4.1 要求 `[Shot 1]` 首位写
**风格词**（`Live-action, cinematic`）；而生产链只挂 turbo LoRA、**未挂 realism LoRA**
⇒ 触发词留在首位会抢掉官方风格位。

### 4. `COPYWRITING_RULES_2026`（新增常量 + 接线，第 773/1144 行）
七节文案表达硬规则，依据 09-20 调研：
- 一、痛点必须写**社交事故**（杯沿唇印／口罩内侧红／牙齿口红印／只剩唇线／当众补妆），不写「不好看」
- 二、讲**成膜**原理，不讲「持久」形容词（唯一值得讲的技术故事）
- 三、参数**禁止留在成稿**（五级阶梯 L1参数→L2意思→L3体验→L4场景→L5画面；验收：L1 能被删掉、L5 能被拍出来）
- 四、**禁止上片的数据**（精确持妆时长／成膜秒数／「完全不拔干」／显白色阶／售价销量／前3秒留存数字——全部无可查证来源）
- 五、叙事骨架：**一次涂 → 一整天 → 晚上还得卸**（决策链：价格第一、口碑第二、参数第三）
- 六、三类废稿特征自查（假痛点／假信任／空 CTA）
- 七、去 AI 味（顺序：先保事实 → 再去 AI 味 → 最后才加人味）

### 5. `SCRIPT_STRUCTURE_RULES_2026`（新增常量 + 接线，第 773/1173 行）
四节脚本结构硬规则：
- 一、**每镜必须换场景**；**同片内每个场所最多使用 1 次**（换场景要连光线背景一起换）
- 二、**每镜必须有可执行的身体/手部动作**，禁纯陈列（自检：这一镜里人物的哪只手做了什么？）
- 三、**CTA 只做一件事：引导用户点「下方小黄车」**（理由+指令；价格/链接绝不进画面描述）
- 四、画面描述**禁止任何文字类元素**（价签/字幕/贴纸/评价页/购物车图标/箭头/角标）

### 6. shots 输出契约新增 `beats` 字段
`beats`：该镜动作的**逐步序列**数组（2–4 条），每条 = 谁 + 可见动作 + 对什么对象 + 本步结束状态。
用途：下游按它逐格生成分镜图，再逐格作为视频锚点。
硬要求：① 相邻两条须是连贯中间状态不得跳步；② 每条须含可见身体/手部动作（禁只写镜头运动）；③ 禁把两个物理独立动作合成一条，也禁无意义拆条；④ 每镜 2–4 条。

---

## 三、generator.ts 的 1 处改动（**这是 beats 一直为空的真凶**）

`validateShot()` 返回一个**新建对象**，只列了
`shotId/type/duration/description/camera/visualSource/transition/voiceover/prompt`
+ 条件透传 `stockKeywords/characterId/motion/textOverlay`。

🔴 **`beats` 不在清单里 ⇒ 无论 LLM 怎么输出都被静默丢弃**（该函数里那句注释
`Pass through LLM-generated extended fields … so they are not silently dropped`
说明以前修过同类问题，却漏了 beats）。

修复：
```diff
+  const rawBeats = (shot as Record<string, unknown>).beats;
+  const beats = Array.isArray(rawBeats)
+    ? rawBeats.map(b => typeof b === "string" ? b.trim() : "").filter(b => b.length > 0).slice(0, 8)
+    : [];
   return {
     …
+    ...(Array.isArray(beats) && beats.length && { beats }),
   };
```
实测：修前 4/4 镜 beats 为空 → 修后 4/4 镜各 3 条。

---

## 四、怎么复现这些改动

1. 把本目录的 `prompts.ts` / `generator.ts` 覆盖到
   `/root/clipforge_src/src/lib/script-engine/`
2. 校验 TS：`cd /root/clipforge_src && npx tsc --noEmit -p tsconfig.json`
3. **重启 ClipForge**（`next dev` 的 HMR 对 prompt 常量模块不总是生效，
   实测必须重启才会加载新提示词）：
   ```bash
   bash /root/cf_start.sh     # 内含 fuser -k 3000/tcp + pkill next-server + pnpm dev
   ```

## 五、验收方式（每次改完必查）

```bash
# ① TS 通过
cd /root/clipforge_src && npx tsc --noEmit -p tsconfig.json | grep prompts.ts

# ② 提示词真的接进了发送链路（不能只定义不接线）
grep -n 'COPYWRITING_RULES_2026}\|SCRIPT_STRUCTURE_RULES_2026}' \
  src/lib/script-engine/prompts.ts

# ③ 端到端实测（在 nmb2 上跑适配器，看闸门评分与实际输出）
cd /root/autodl-tmp/h3p && venv/bin/python scripts/clipforge_adapter.py \
  --endpoint http://43.136.35.203:3000 --name "…" --desc "…" \
  --duration 32 --image input/prod_lipstick.jpg --out /tmp/t.json
```

## 六、踩过的坑

1. **TS 模板字符串内禁止未转义反引号** —— 否则直接终止模板串，报 TS1005。
   每次打补丁后必须数反引号奇偶（应为偶数）。
2. **定义 ≠ 接线** —— 新增常量后必须确认 `parts.push(...)` 里有它，
   否则提示词写了却从不发送（本项目已踩过一次）。
3. **改完必须重启 ClipForge** —— 只改文件不重启，实测不生效。
4. **跨机跑补丁脚本要分文件** —— 一个脚本里同时读 nmb2 与 hao 的路径，
   在另一台机上会因路径不存在而**崩在中途**，后面的改动静默不做
   （本次「场所唯一」规则就这样漏掉过一次）。
