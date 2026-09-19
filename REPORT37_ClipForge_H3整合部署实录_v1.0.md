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

## 7. 已知问题 / 待办

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
