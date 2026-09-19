# REPORT37 · ClipForge × H3 整合部署实录（A1 路线）

> 日期：2026-09-19
> 授权：辉哥拍板「A1 路线」+「项目只是部署给一个用户使用」
> 性质：部署实录（架构 / 改动 / 验证证据 / 已知问题）
> 上游：REPORT36 v1.1（ClipForge 服务器可行性）、REPORT35（社区实例调研）

---

## 0. 一句话结论

> **ClipForge 的 atlas-cloud provider 已改指本地 H3 Bridge —— ClipForge UI 触发 → 本地 H3 出片链路打通，端到端提交成功。**

```
ClipForge (hao:3000)  ──►  AtlasCloudProvider.baseUrl
                              ↓ (改为 H3_BRIDGE_URL)
                        SSH 隧道 (hao 本地端口)
                              ↓
                        H3 Bridge (nmb2:8900)   ← 新增，Atlas API 兼容层
                              ↓
                        ComfyUI (nmb2:6011) · H3 INT8 + 4步 Turbo LoRA
                              ↓
                        视频文件 → hao:8901/files/{task_id}.mp4
```

---

## 1. 为什么是 A1（而不是 monkey-patch / A2）

| 路线 | 结论 |
|---|---|
| monkey-patch（instrumentation.ts hook Module.require） | ❌ 实测未生效——Next.js 16 Turbopack 的模块加载无法可靠拦截 provider 工厂 |
| A2（自写 ClipForge-Lite） | ⏸️ 工程量大（2.5 天），放弃现成的 4 判官/钩子库/模板 |
| **A1（改 ClipForge 源码 1 行）** | ✅ **最小改动 + 全部功能可用** |

**AGPL-3.0 判定**：单用户私有部署（无对外网络服务）→ AGPL 第 13 条不触发。
辉哥明确：「项目只是部署给一个用户使用」。

---

## 2. 基础设施：SSH 隧道（hao → nmb2）

hao 无法直连 nmb2（AutoDL 只暴露 SSH 端口），且 gpuhub 的 Bridge 只在 nmb2 内网。

**方案**：在 hao 上用 `sshpass + autossh` 建立三条本地端口转发。

```
hao:8900 → nmb2:8900   (H3 Bridge API)
hao:8901 → nmb2:8901   (媒体文件服务)
hao:6011 → nmb2:6011   (ComfyUI，调试用)
```

脚本：`hao:/root/start_h3_tunnel.sh`（密码文件 `/root/.nmb2_pw`，权限 600）

**验证**：`curl hao:8900/health` → `{"bridge":"ok","comfyui":true}`

---

## 3. ClipForge 改动（唯一 1 处源码改动）

**文件**：`/root/clipforge_src/src/lib/providers/atlas-cloud.ts`（构造函数）

```ts
constructor(config: ProviderConfig) {
  const resolvedBaseUrl =
    config.baseUrl || process.env.H3_BRIDGE_URL || 'http://127.0.0.1:8900/api/v1'
  super({ ...config, baseUrl: resolvedBaseUrl })
  console.log('[H3 Bridge] AtlasCloudProvider baseUrl =', ...)   // 诊断日志
}
```

**为什么这一处就够**：ClipForge 前端不传 `baseUrl`（providers state 无该字段），
API route 传 `p.baseUrl ?? ""` → 空字符串 falsy → 回落到 env / 硬编码默认值。

**`.env.local`**：
```
H3_BRIDGE_URL=http://127.0.0.1:8900/api/v1
H3_FILE_BASE=http://127.0.0.1:8901
```

---

## 4. H3 Bridge（新增服务：nmb2:8900 + nmb2:8901）

**文件**：`nmb2:/root/h3_bridge_v2.py`（FastAPI，约 430 行）

**职责**：把 Atlas Cloud API 协议翻译成 ComfyUI `/prompt` 协议。

| 端点 | 作用 |
|---|---|
| `GET /health` | 健康检查（含 ComfyUI 连通性） |
| `GET /api/v1/models` | 返回 3 个 H3 模型（text/image/reference-to-video） |
| `POST /api/v1/model/generateVideo` | 接收 Atlas 请求 → 建 workflow → 提交 ComfyUI → 返回 `{data:{id}}` |
| `GET /api/v1/model/prediction/{id}` | 轮询状态 → `succeeded` 时返回 `result.videoUrls` |
| `POST /api/v1/model/uploadMedia` | 接收首帧/参考图 |
| `:8901` `/files/{task_id}.mp4` | 媒体服务（ClipForge 取片） |

### 4.1 三个必须踩过的坑（已修）

| # | 现象 | 根因 | 修法 |
|---|---|---|---|
| 1 | ComfyUI 400 `Required input is missing: filename_prefix / vae_name` | 硬编码 widget 名 → 与节点真实 schema 不符 | 改为**动态拉取 `/object_info`**，按真实 widget 顺序做位置映射 |
| 2 | 输出 `240x424`（应为 `720x1280`） | 面积+宽高比公式写反 | `w=√(area·rw/rh)`, `h=√(area·rh/rw)` |
| 3 | 只跑 `shot 1/1`（应为 5 镜） | 用 `widgets_values[0]` 覆盖 script → **位置错位**（wv[0] 实为 width），`shot_count` 未设 | 改为**转换后按字段名写入**：`inputs["script"]`, `inputs["shot_count"]=脚本中 "Shot N:" 计数` |

### 4.2 关键设计决定

- **不要用 widgets_values 位置索引改任何字段**——节点间的 widget 顺序不一致，
  一律在 `wf_to_prompt_api()` 之后按**输入名**覆盖。
- **长任务**：Bridge 后台任务 + ClipForge 5s 轮询；超时上限 90 分钟（本地 5 镜 ≈ 20–40 min）。
- **媒体回传**：生成后复制到 `SERVED_DIR/{task_id}.mp4`，
  URL 用 `SERVED_URL_BASE`（=hao 视角的 `http://127.0.0.1:8901`）。

---

## 5. 验证证据（实测）

### 5.1 ClipForge 确实走 Bridge

```
ClipForge dev log:
  [H3 Bridge] AtlasCloudProvider baseUrl = http://127.0.0.1:8900/api/v1
              | incoming config.baseUrl = "" | env H3_BRIDGE_URL = http://127.0.0.1:8900/api/v1

Bridge access log (nmb2):
  INFO: 127.0.0.1:50326 - "GET /api/v1/models HTTP/1.1" 200 OK

耗时对比：以前打真 Atlas 1503 ms → 现在 186 ms（约 8×，走本地隧道）
```

### 5.2 Bridge 端到端出片（第一次，240x424 调试尺寸）

```
[h3_d78c3d85bb0d] NEW TASK dur=8 ratio=9:16 prompt_len=3324
[h3_d78c3d85bb0d] schemas fetched: 9 node types
[h3_d78c3d85bb0d] submitted -> 69f88b65-a4f5-42bb-a0d8-45b6b7976640
[h3_d78c3d85bb0d] SUCCEEDED core_00004_.mp4 -> http://127.0.0.1:8901/files/h3_d78c3d85bb0d.mp4
耗时 36 秒（小尺寸）
```

### 5.3 修复后再次提交（5 镜 / 720x1280）

```
workflow built: sampler=8 shots=5 dims=720x1280 frames=192 schemas=9
queue inputs: {"width":720,"height":1280,"frames_per_shot":192,"steps":4}
sampler shot_count: 5 | script len: 3324
ComfyUI log: [H3Multishot] shot 1/5 (192f @ 720x1280)
```

**状态**：生成中（GPU 100%）。

---

## 6. DeepSeek 接入（已改造：支持服务器 env 兜底）

### 6.1 原始情况

ClipForge 的配置由 `zustand + persist` 存进**浏览器 localStorage**：

```
src/lib/stores/settings-store.ts:
   import { persist } from "zustand/middleware"
   llm: { provider, baseUrl, apiKey, model, visionModel }
```

⇒ **服务器端拿不到也改不了**，只能由用户在浏览器 UI 里配。

### 6.2 改造（本次）：DEEPSEEK_* env 兜底

**动机**：单用户私有部署下，让服务器 `.env.local` 成为权威配置源，
不依赖浏览器 localStorage（换浏览器/清缓存就丢配置）。

| # | 文件 | 改动 |
|---|---|---|
| 1 | `src/lib/script-engine/generator.ts` | 新增 `withLLMEnvFallback()`，在 `generateScript` / `generateScriptStream` / `analyzeProduct` 三个入口应用 |
| 2 | 6 个 LLM route | "缺少 LLM 配置"守卫加 `&& !process.env.DEEPSEEK_API_KEY` 逃生口 |

**6 个 route**：`llm/script`、`llm/publish`、`topic/script`、`ad-template/generate`、
`project/[id]/script-judge`、`project/[id]/dub`

**兜底逻辑**（`withLLMEnvFallback`）：
```ts
apiKey  ← config.apiKey      || process.env.DEEPSEEK_API_KEY
baseUrl ← config.baseUrl     || process.env.DEEPSEEK_BASE_URL || https://api.deepseek.com
model   ← config.model       || process.env.DEEPSEEK_MODEL     || deepseek-chat
```
**优先级**：浏览器 UI 配置 > 服务器 env > 内置默认值。

### 6.3 `.env.local`

```
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-chat
# DEEPSEEK_API_KEY 由 `set_deepseek_key.sh` 交互式写入（read -s，不回显/不进 history）
```

### 6.4 Key 注入方式（安全）

```bash
ssh -t hao 'bash /root/set_deepseek_key.sh && bash /root/cf_start.sh'
```
脚本用 `read -s` 读取 Key → 追加进 `.env.local`（chmod 600）→ 重启 ClipForge。
**Key 不经过任何对话、不进 bash history、不进 git。**

### 6.5 验证（无 Key 状态）

```
POST /api/llm/script  {"productName":"测试无线耳机", ...}
→ {"error":"请配置 LLM 参数（baseUrl、apiKey、model）"}
```
✅ 守卫正确拦截（无 Key）；写入 Key 后同一请求将放行并调用 DeepSeek。

### 6.6 成本

DeepSeek 写脚本 ≈ ¥0.001/次；按 100 条/天估算 ≈ **¥3/月**。
（对比本地 Qwen：需下 9 GB 模型 + 与 H3 抢 24 GB 显存 → 不划算，见 REPORT38）

---

## 7. 端到端验证结果（2026-09-19 16:5x）

### 7.1 DeepSeek ✅ 生效

```
POST /api/ai/... /api/llm/script   {"productName":"无线蓝牙耳机", ...}
→ HTTP 200 | 15.7s
→ {"scripts":[{"title":"降噪耳机天花板","styleType":"pain_point","totalDuration":28,
   "shots":[{"shotId":1,"type":"hook","duration":3,
             "description":"地铁车厢内，主角戴着无线蓝牙耳机...",
             "camera":"镜头急速推近主体，冲击力强，开场抓眼",
             "voiceover":"地铁上吵到崩溃？这副耳机一戴，世界瞬间安静。",
             "prompt":"Close-up of a young Asian man wearing wireless earbuds..."},
            ...]}]}
```

**脚本结构完全匹配 H3 需求**：每镜含
`prompt`（英文，可直接喂 H3）/ `voiceover`（中文旁白）/ `camera`（运镜）/ `duration` / `visualSource`。

### 7.2 视频路由 ✅ 无 UI 配置可用

改造 `api/ai/video/route.ts` 用**带默认值的解构**（`provider` / `model` / `apiKey` 三项 env 兜底），
一次覆盖全部下游使用点（`createProvider`、`modelId`、`ai_tasks` 记录）。

**验证**：不带 provider / model / apiKey 调用 → 请求抵达 Bridge（Bridge 日志出现对应 task），
证明链路通、不再被"缺少 API Key"拦截。

### 7.3 发现并修复：Bridge 缺 model 校验 ⚠️

**问题**：Bridge 接受**任意** model 字符串并提交 ComfyUI。
探针用 `__probe_nonexistent__` 时被接受 → **占用一个 GPU 队列位**（ComfyUI 串行，会白跑 30 分钟）。

**修复**（`patch_bridge_model_guard.py`）：
```python
def _resolve_model(name):        # 未知 → None → 400 拒绝
    if name in _KNOWN_MODELS: return name
    if "h3" in name.lower():      # UI 变体容错：按 text/image/reference 归一化
        return 对应本地模式
    return None
```
+ `generateVideo` 入口校验，未知 model 直接 `HTTPException(400)`，**不触碰队列**。

**已清理**：被误提交的探针任务已从 ComfyUI 队列删除（`POST /queue {"delete":[...]}`）。

### 7.4 已知缺口（下一步）

| # | 缺口 | 说明 |
|---|---|---|
| 1 | **逐镜 vs 多镜模式** | ClipForge 是**逐镜调用**（每 shot 一次 `/api/ai/video`），Bridge 当前是**多镜一次生成**（`H3MultishotSampler`）。需确认单镜路径（无 `Shot N:` 标记时 `n_shots=1`，Bridge 已天然支持） |
| 2 | **首帧传递** | ClipForge 传 `imageUrl`/`lastImageUrl`（关键帧链接），Bridge 尚未接到 H3 的 first_frame 输入 |
| 3 | **单镜时长** | ClipForge 每镜 3–8s，Bridge 当前固定 192 帧（8s），需支持按 `duration` 换算帧数 |
| 4 | 图片生成 route | `/api/ai/image` 同样有 apiKey 守卫（本次只改了 video） |

### 7.5 运维注意

- ⚠️ `ssh -t hao '... && bash /root/cf_start.sh'`：**伪终端退出会带走子进程**（ClipForge 被 SIGHUP）。
  正确重启方式：`ssh -T hao 'bash /root/cf_start.sh'`（`-T` 禁用伪终端）
- Bridge 重启会**清空内存态 TASKS**（进行中的任务将无法查询，但产物仍在输出目录）
  → 改 Bridge 代码后，**等当前任务跑完再重启**。

---

## 8. 已知问题 / 待办

| # | 项 | 状态 |
|---|---|---|
| 1 | 生成完成后 ClipForge 能否正确取回并入库 | ⏳ 待验证 |
| 2 | ClipForge 前端「AI 生成成片」全流程（商品图 → 脚本 → 出片） | ⏳ 待验证 |
| 3 | DeepSeek Key 配置（用户 SSH 终端或 UI 操作，Key 不进对话） | ⏳ 待用户 |
| 4 | Bridge 的 `shot_count` 依赖 prompt 里 "Shot N:" 标记——ClipForge 自动生成的脚本是否为该格式 | ⏳ 待验证 |
| 5 | 生成中断（ClipForge 关页后任务仍在 ComfyUI 跑，Bridge 内存态丢失） | 设计已知 |
| 6 | 隧道需开机自启（当前手动 `bash /root/start_h3_tunnel.sh`） | 待加固 |

---

## 8. 复现清单（若重建环境）

```bash
# 1) hao：建隧道
bash /root/start_h3_tunnel.sh

# 2) nmb2：起 Bridge
cd /root && SERVED_URL_BASE=http://127.0.0.1:8901 setsid nohup python3 h3_bridge_v2.py > /tmp/bridge.log 2>&1 &

# 3) hao：改 ClipForge 默认 baseUrl（1 行）+ 重启
#    src/lib/providers/atlas-cloud.ts 构造函数见 §3
cd /root/clipforge_src && bash /root/cf_start.sh

# 4) 验证
curl -s http://127.0.0.1:8900/health            # hao 上
curl -s -X POST http://127.0.0.1:3000/api/ai/models -H 'Content-Type: application/json' \
  -d '{"providers":[{"name":"atlas-cloud","apiKey":"dummy"}]}' | head -c 300
```


---

## 9. 提示词对齐修正（2026-09-19 晚 · 三次纠错）

> 本节记录一个**连续三轮才做对**的对齐过程，含两次方向性错误与纠正证据。

### 9.1 目标

把 ClipForge 生成的每镜 `prompt` 对齐 H3 官方提示词要求，并正确处理
**人物真实度 LoRA 的触发词位置**。

### 9.2 三次纠错

| 轮次 | 我的做法 | 错在哪 | 纠正证据 |
|---|---|---|---|
| ① | 注入"官方三段式"（指令行 + `integrated_multimodal_description:` + `overall_soundscape:` + `non_diegetic_music:`） | **对齐错了节点**——三段式属于**单镜 I2VA/FL2VA 节点**的规范，而我们走的是 **Multishot 链式采样器**，两者不是同一代码路径 | ① `PROMPTING.md` 附带的 `example_script.txt` 是**自由叙述**（无任何标签）；② 一次**已验证成功**的 5 镜成片，其 script 实际形如 `Shot 1: <自由叙述> The narrator says in Chinese: … [background_audio] …`，**既无标签也无指令行** |
| ② | 把 `true-to-life skin texture preserved, photorealistic real-person look, not CGI` 当作**触发词**，并要求放 `integrated_multimodal_description` **段尾** | 那串是**数字人模板里的画质描述短语**，**不是 LoRA 触发词**；位置也错（触发词必须在**开头**） | fal 官方 model card 原文：`Trigger word: r34l1sm` / *"Start the prompt with the trigger word `r34l1sm`, then describe the scene."* —— **辉哥凭记忆指出"应该是第一位"，核实后完全正确** |
| ③ | 触发词写成 `r34l1sm`，但位置仍留在描述段开头 | 仍非"第一位"（前面还有指令行）；且 LLM 把已废弃的描述短语又抄了回来 | `prompts.ts:684` 残留了 ① 轮的旧文案（含那串短语 + "放段末尾"），LLM 照着抄 → 一并清除 |

### 9.3 最终规范（已落地 `prompts.ts` 的 `H3_PROMPT_SPEC`）

```
每镜 prompt = 「r34l1sm, 」+ 一段英文自由叙述

叙述顺序：相机与构图 → 风格与画质 → 人物完整外观 → 场景与道具细节 → 本镜动作 → 台词 → 收尾状态
台词写法：and says in Chinese: "……"     环境音：[background_audio] ...
```

**六条禁止项**（全部来自实测事故）：
1. 禁否定句（`no X` / `does not move`）—— CFG=1.0 无负向分支，否定会把概念喂给模型
2. 禁静止短语（`goes still` / `exactly as they were`）—— 会冻结整帧
3. 禁扩散模型标签（`cinematic` / `8k` / `masterpiece`）
4. 禁绝对位置与占比（`at frame LEFT` / `occupies 30%`）
5. 禁在镜边界改变场景（会出双人/双道具）
6. 禁画面内文字（除非该镜就是文字卡）

**镜间一致性铁律**：每镜**逐字重复**人物完整外观 + 场景光线描述（改写 = 中途换脸）。

### 9.4 同步修掉的另两个缺陷

| # | 缺陷 | 修法 |
|---|---|---|
| 1 | `visualSource="product_image"` 的镜**省略 prompt** → 实测 5/7 镜无画面描述 | prompt 改为**每镜必填**（product_image 只决定首帧来源，H3 仍需画面描述） |
| 2 | 第一行与正文**缺空行** | 规范中明确要求 |

### 9.5 验证结果（三轮后）

```
复测 7 镜：r34l1sm首位=✅×7   无标签=✅×7   无指令行=✅×7   缺失prompt=0/7
```

落地 sample：
```
r34l1sm, A static camera frames a white wireless earbud charging case from a high angle on a
dark grey marble tabletop; live-action, shallow depth of field, fine 35mm grain. ...
[background_audio] quiet indoor studio, a soft whoosh, then stillness
```

### 9.6 教训

- **对齐前先确认目标节点/代码路径**：H3 生态里"单镜 I2VA/FL2VA 节点"与"Multishot 链式采样器"的 prompt 规范**不同**，套错等于没对齐。
- **"已验证成功的成片"是最强证据源**：与其推演规范，不如直接读跑通过的 script 原貌。
- **LoRA 触发词以官方 model card 为准**，不要在自家代码里"认领"看起来像触发的短语。
- **改 prompt 规范后必须清残留**：同一文件里旧文案会继续被 LLM 抄走（本次 684 行残留导致第 ②→③ 轮返工）。
