# REPORT33 · GPU 仲裁器队列模式修复 + 空闲释放（15 min）

**日期**：2026-09-18
**触发**：辉哥指令 —— ①修复服务器队列模式，优先数字人任务，其他项目任务待数字人任务完成后可推进；②常驻 GPU 规则：若 30 min 无任务，所有项目的模型都释放出 GPU。**同日追加**：阈值由 30 min 收紧为 **15 min**。
**对象**：共享服务 `/opt/shared/gpu_arbiter.py`（:6200）+ 客户端库 `/opt/shared/gpu_lock.py`
**影响面**：同机 5 个业务（dh 数字人 / h3-gateway / heygem / tts / 本项目 h3p）
**备份**：`/opt/shared/{gpu_arbiter,gpu_lock}.py.bak-20260918-1259`、`gpu_arbiter.py.bak-20260918-1306`

---

## 一、结论先行

| 需求 | 状态 | 证据 |
|---|---|---|
| 数字人任务绝对优先 | ✅ | `dh:test(prio=9)` 排在 `h3p:probe(prio=0)` **之前** |
| 其他项目待数字人完成后推进 | ✅ | 队列按 `rank → priority → seq`；不打断在跑任务，跑完立即让位 |
| 无任务即释放所有模型 | ✅ | **阈值 15 min**（原 30 min）；隔离实例实测自动触发；手动 `POST /idle_free` 可即触发 |
| 队列模式健壮性 | ✅ | 修复 4 处：业务优先级 / flock 空转 / 僵尸条目 / 超时误回退 |

**直接收益**：整机 `free` 从 **272 MiB → 7477 MiB**（6011 卸掉 8368→432 MiB）。修复前机器已满到只剩 272 MiB 可用。

---

## 二、修复 1：队列真正的两级排序（核心需求）

### 问题
原排序键 `(priority, seq)`。而实测传参：

| 服务 | label | priority | rank(新) |
|---|---|---|---|
| dh worker | `dh:h3` / `dh:va` | 1 | **0** |
| h3-gateway | `h3:digital-human` / `vace:digital-human` | 1 | **0** |
| h3-gateway | `h3:ecom` | 2 | 50 |
| heygem | `heygem` | 2 | 50 |
| tts | `tts` | 2 | 50 |
| **本项目 h3p** | `h3p:*` | 1 | 50 |

**数字人与电商同为 `priority=1`** ⇒ 谁先提交谁先跑，拿不到"数字人绝对优先"。

### 关键坑：不能靠 `project` 字段判定
实测**同机 5 个服务没有一家设了 `GPU_PROJECT`**，`/status` 里 `project` 全是 `"unknown"`（见下）。所以 rank 判定必须走 **label 的 token**：

```
label 拆 token (按 : / 空格) → 与 {dh, digital-human, digitalhuman} 求交集 → 命中即 DH_RANK
dh:va                → {dh,va}                → rank 0 ✅
h3:digital-human     → {h3,digital-human}     → rank 0 ✅
vace:digital-human   → {vace,digital-human}   → rank 0 ✅
h3:ecom              → {h3,ecom}              → rank 50 ✅ (电商，非数字人)
h3p:gen / heygem / tts                        → rank 50 ✅
```

新排序键：**`(rank, priority, seq)`**。效果（实测，故意让 dh 带最差 priority）：

```
1) dh:test            rank=0  prio=9
2) h3:digital-human   rank=0  prio=9
3) h3p:probe          rank=50 prio=0   ← priority 最好也插不到数字人前面
4) tts                rank=50 prio=3
5) heygem             rank=50 prio=3
```

### 语义边界（重要）
**只改排队优先级，不抢占。** dh 任务到达时若其他项目正在跑，**不会打断**（H3 长任务打断 = 白烧算力），而是挤到队首，等当前任务 `release` 后立刻授予。这正是"其他项目任务待数字人任务完成后可推进"。

### 已知副作用
纯优先级无老化 —— 若 dh 持续有任务，其他项目会饥饿。当前按辉哥要求实现（dh 优先），如需兼顾可加 `wait_s` 老化提权（`GPU_AGING_AFTER`），暂未启用。

---

## 三、修复 2~4：队列模式的另外三个真缺陷

### 缺陷 2：flock 层长期空转（上一轮已报告，本轮修掉）
`check_foreign_lock()` 用**主 fd** 做 `try_lock()` + `unlock()` 探活。而 flock 对**同一 fd 幂等** ⇒ 仲裁器自持锁时 `try_lock()` 也返回成功 ⇒ 紧接着 `unlock()` 把**租约期间的锁一并放掉**。

**实测证据**：修复前 arbiter 声称 `active=dh:va`，但 `/proc/locks` 里 gpu.lock **0 条记录**。

⇒ 后果：任何回退 flock 的进程都能拿到**假锁**，与租约持有者并发抢显存。
⇒ 修复：改用**独立 fd** 探测（关闭即释放，不影响主 fd）。
⇒ 验证：修复后 `/proc/locks` 出现记录，持锁 pid == arbiter pid ✅

### 缺陷 3：僵尸排队条目
`/acquire` 原为同步实现，**感知不到客户端断开**。被 kill 的排队进程会把条目留到自己的 `timeout` 才消失（上一轮实测残留过一条 3600 s 幽灵），期间白占排队位。

⇒ 修复：`/acquire` 改 `async def` + `await request.is_disconnected()`，每 0.5 s 探测一次。
⇒ 验证：kill 客户端 3 s 后队列即空，日志 `客户端断开, 撤销排队 label=h3p:zombie (已等 3s)` ✅（600 s timeout 场景下 0.5 s 内撤销）

### 缺陷 4：排队超时误回退 flock（客户端侧 `gpu_lock.py`）
原 `raise TimeoutError(...)` 写在 `try` 块内，被紧随其后的 `except Exception:` 吞掉 ⇒ **排队超时反而去抢 flock**，叠加缺陷 2 就拿到了假锁。

新规则：
- 服务端明确应答（`ok=False` / HTTP 4xx-5xx）⇒ 仲裁器活着 ⇒ **绝不回退**，直接抛超时；
- 连接层失败 ⇒ 再用 `GET /status` **二次确认**；确认不可达才回退 flock；
- 提供 `allow_flock_fallback=False` 供调用方强制"必须走仲裁器"。

⇒ 验证：`timeout=6` 排队 → 6.0 s 如实报 `acquire timeout`，未回退 ✅

---

## 四、空闲释放（阈值 15 min）

### 机制
`sweeper()` 每 10 s 检查一次：**无 active 租约 + 无排队** 且 `idle_seconds ≥ IDLE_FREE_AFTER` ⇒ 触发释放。
`idle_seconds` 从 `max(启动时刻, 最后 release 时刻, 最后释放时刻)` 算起；释放后由 `last_free_at` 重置计时，**不会反复空转重放**。

**阈值**：`IDLE_FREE_AFTER` 默认 **900 s（15 min）**。原为 1800 s（30 min），2026-09-18 按辉哥指令收紧。
可用环境变量 `GPU_IDLE_FREE_AFTER` 覆盖，无需改代码。

### 三层守卫（防误伤）
| # | 守卫 | 实测 |
|---|---|---|
| ① | 有 active 租约 / 有排队 ⇒ 拒绝 | `{'skipped': 'busy', 'active': 'dh:fake'}` ✅ |
| ② | 当前显存 < `IDLE_FREE_MIN_USED_MB`(1500) ⇒ 本来没什么可放 | `{'skipped': 'vram-low'}` ✅ |
| ③ | 目标自身还有 running/pending ⇒ 跳过该目标 | `comfyui-dh=busy(1)`，**显存 23850→23850 分毫未动** ✅ |

守卫③ 是关键：它能挡住**绕过仲裁器直接调 ComfyUI** 的在跑任务。

**⚠️ 守卫不随阈值缩短而减弱** —— idle 判定要求 `active is None and not queue`，与阈值无关；缩短的只是"等多久才动手"。故 15 min 不会提高误伤概率。

### 释放目标（`/opt/shared/idle_free_targets.json`）
| 目标 | 端点 | 显存 | 纳入 |
|---|---|---|---|
| `comfyui-dh` (:6006) | `POST /free {unload_models,free_memory}` | ~15 G | ✅ |
| `comfyui-h3p` (:6011) | 同上 | ~8.4 G | ✅ |
| `tts` (:6108) | `POST /v1/tts/unload`（Bearer，`token_from` 现读 projects.json） | 0.47 G | ✅ |
| `heygem` (:6109) | `POST /v1/heygem/unload` | 0 | ❌ **有意排除** |

**heygem 为何排除**：它的 unload 会写 `/opt/shared/gpu-maintenance` 维护标志，而该标志**只有 H3 出片结束才 unlink** ⇒ 由仲裁器写会让 heygem **永久不被 watchdog 拉回**。且实测它的官方服务不占显存（不出现在 `nvidia-smi compute-apps`）。目标表里已留注释，需要时可加。

### ⚠️ 权衡：释放的代价是"下次要重载"
`/free` 会卸载模型缓存，下次生成需重新 load（H3 权重加载数十秒~分钟级）。
dh 团队在 `app.py` 里留过注释："队列空闲时 /free 是无意义往返且强制下次 H3 重载" —— 那是针对**短时空闲**的优化。

**30 min → 15 min 的实际行为变化**：只在 **15–30 min 这一档空档**里从"不释放"变成"释放"。
其余区段（<15 min 或 >30 min）行为与改前完全一致。

| 空档长度 | 旧（30 min） | 新（15 min） |
|---|---|---|
| < 15 min | 不释放 | 不释放（**不变**） |
| 15–30 min | 不释放，保持热态 | **释放 → 下个任务 cold start** |
| > 30 min | 释放 | 释放（**不变，但早 15 min 动手**） |

⇒ 判断依据：如果 dh 的任务间隙经常落在 15–30 min，新阈值的净影响是"间隙中把显存还回池子，代价是下个任务首段慢一截"。
**反过来说**：这段间隙里其他项目（含我们 h3p）来任务时，就不用挤在 15 G 已被占的卡上了 —— 这正是单卡共享下更划算的地方。

**若某项目需"预热常驻"**：把该目标从 `idle_free_targets.json` 的 `targets` 里移除即可（单项目豁免，无需改代码）。

### 一个诚实的负面结果
`tts` 调用返回 `{"ok":true,"unloaded":true}` 且服务存活，但**显存没降**（466 MiB 仍在）。
原因：那 466 MiB 是 **torch CUDA context 底噪**，不是模型权重，`unload`（`_model=None` + `empty_cache()`）放不掉。
⇒ 结论：纳入 tts 无害且幂等（真加载 VoxCPM 时能放权重），但其 466 MiB **别指望能回收**；要回收只能杀进程。

### 一个已知竞态（可接受，说明边界）
sweeper 判定 `idle=True` 与真正发出 `/free` 之间有几十毫秒窗口。若此时新任务恰好 acquire 并**刚拿到租约、尚未把 prompt 提交给 ComfyUI**，则该 ComfyUI 的 `/queue` 仍为空 ⇒ 守卫③不会拦，模型会被卸掉。
后果**不是错误而是变慢**：ComfyUI 服务仍在，新任务提交时它会自动重载模型。
窗口极窄（sweeper 10 s 一轮 + 该窗口仅几十毫秒），且 15 min 阈值下"刚空闲就立刻来任务"的概率很低。**不改**。

---

## 五、附带的必要能力：状态持久化（重启不丢租约）

**为什么必须有**：重启会清空内存态。若不复原 `active`，新来的 `acquire` 会被**立刻授予** ⇒ 与仍在跑的旧任务并发抢显存（实测两个 H3 满载并行时单条从 ~510 s 翻倍到 1400 s+）。

**实现**：`active` / `seq` / `last_release_at` 落盘 `/opt/shared/arbiter_state.json`（grant / release / expire 时写）；启动时 `_load_state()` 恢复**心跳仍在有效期内**的租约，并把 flock 抢回。

**实测**：本轮共重启 arbiter **4 次**（末次为阈值改 900 s），dh 的租约 `Le96df11ef1` **每次无缝恢复**，held_s 连续累积（463 → 568 → 673 → 742 → 766 → **934 s**），dh 任务全程未受打扰、心跳持续 200 OK。

---

## 六、验证矩阵

| # | 项目 | 方法 | 结果 |
|---|---|---|---|
| 1 | 排序逻辑（单测） | 7 种 label 混排 | dh 系恒在前 ✅ |
| 2 | 队列优先级 | 5 个真实排队客户端 | `dh:*(prio=9)` 排在 `h3p:*(prio=0)` 前 ✅ |
| 3 | 超时不回退 flock | `timeout=6` 排队 | 6.0 s 如实报超时 ✅ |
| 4 | 僵尸条目 | 起排队客户端 → `kill -9` → 观察 | 3 s 后队列空 + 撤销日志 ✅ |
| 5 | flock 层生效 | `/proc/locks` | 有记录，持锁 pid == arbiter ✅ |
| 6 | 租约持久化 | 重启 3 次 | dh 租约无缝恢复 ✅ |
| 7 | 守卫①（有任务） | 伪造 active | `skipped=busy` ✅ |
| 8 | 守卫②（显存低） | 阈值临时调至 999 GB | `skipped=vram-low` ✅ |
| 9 | 守卫③（目标在跑） | 只针对 6006 试释放 | `busy(1)`，显存零变化 ✅ |
| 10 | 真实释放链路 | `POST /idle_free {force,only:h3p}` | 8368 → **432 MiB**，服务 HTTP 200 ✅ |
| 11 | 自动触发路径 | 隔离实例 `:6299`，阈值 6 s | `空闲 10s ≥ 6s, 触发模型释放` ✅ |
| 12 | 不误伤他项目 | 全程监控 6006/租约 | dh 队列照跑、租约连续 ✅ |
| 13 | **阈值改 900 s 生效** | 重启后读 `/status` | `free_after_s = 900.0` ✅ |
| 14 | **15 min 阈值到点触发**（隔离复验） | 隔离实例 `:6299`，阈值 25 s，假释放目标 | `空闲 30s ≥ 25s, 触发模型释放` + 释放调用真实发出 ✅ |
| 15 | 改阈值不干扰生产 | 复验全程监控 6200 | dh 仍持租约（982.6 s）、`free_after_s=900`、队列空 ✅ |

隔离实例的好处：**不改生产配置**（端口/锁文件/状态文件/目标表/告警文件全部重定向，释放目标指向不可达端口）⇒ 阈值与释放目标都不必为了测试而污染生产，**绝不会误卸真实模型**。

---

## 七、运维手册

```bash
# 看全局：谁在持有、谁在排队、还有多久触发空闲释放
curl -s http://127.0.0.1:6200/status | python3 -m json.tool

# 释放目标表
curl -s http://127.0.0.1:6200/targets | python3 -m json.tool

# 手动触发释放
curl -sX POST http://127.0.0.1:6200/idle_free -H 'Content-Type: application/json' -d '{}'            # 走全部守卫
curl -sX POST http://127.0.0.1:6200/idle_free -H 'Content-Type: application/json' -d '{"dry_run":true,"force":true}'   # 只报告
curl -sX POST http://127.0.0.1:6200/idle_free -H 'Content-Type: application/json' -d '{"force":true,"only":["comfyui-h3p"]}'  # 只放 6011

# 本项目客户端（技能包 scripts/gpu_lease.py）
gpu_lease.py status          # 同 /status，带中文解读
gpu_lease.py probe           # 申请→立刻释放，验证链路
gpu_lease.py run -- CMD      # 持租约执行，退出即释放
```

**可调参数（环境变量，改后需重启 arbiter）**
| 变量 | 默认 | 含义 |
|---|---|---|
| `GPU_IDLE_FREE_AFTER` | **900**（15 min） | 空闲多少秒后释放 |
| `GPU_IDLE_FREE_ENABLED` | 1 | 0 = 关闭自动释放 |
| `GPU_IDLE_FREE_MIN_USED_MB` | 1500 | 低于此显存占用则跳过整轮 |
| `GPU_IDLE_TARGETS` | `/opt/shared/idle_free_targets.json` | 目标表路径 |
| `GPU_DH_RANK` / `GPU_OTHER_RANK` | 0 / 50 | 业务级 rank |
| `GPU_DH_LABEL_TOKENS` | `dh,digital-human,digitalhuman` | 数字人判定 token |
| `GPU_ARBITER_STATE` | `/opt/shared/arbiter_state.json` | 租约持久化文件 |

**重启方式（安全）**：状态持久化保证租约不丢。用端口号定位 PID，**不要用 `pkill -f gpu_arbiter.py`**（会匹配到 `bash -c` 自身，把承载 SSH 的 shell 一起杀掉 —— 本轮又踩一次）：
```bash
OLD=$(ss -ltnp | grep ':6200' | sed -E 's/.*pid=([0-9]+).*/\1/' | head -1); kill $OLD
nohup /root/miniconda3/bin/python /root/autodl-tmp/relocate/shared/gpu_arbiter.py < /dev/null >> /var/log/gpu-arbiter.log 2>&1 &
```

**回退**
```bash
cd /opt/shared && cp -f gpu_arbiter.py.bak-20260918-1306 gpu_arbiter.py   # 回到"30 min 阈值"版本
# 或再往前：cp -f gpu_arbiter.py.bak-20260918-1259 gpu_arbiter.py && cp -f gpu_lock.py.bak-20260918-1259 gpu_lock.py
# 然后按上面方式重启
```
**只改阈值不改代码**：`GPU_IDLE_FREE_AFTER=1800` 起 arbiter 即可回到 30 min。
⚠️ 回退后旧代码的"重启丢租约 / 超时回退假锁"缺陷会回来，重启前须确认没有任务在跑。

---

## 八、遗留与建议

1. **`gpu_lock.py` 的改动只对新进程生效**。已在跑的 dh worker 是在重启前 import 的旧代码（内存里）。本轮 dh 的 `release` 走旧逻辑调用 `/release`，因 lease_id 匹配仍正常。**下次 dh 重启后自动切到新逻辑**。其余服务同理。
2. **`h3p` 的 6011 目前已被释放（432 MiB）**。下次跑生成会自动重载模型，首段会慢一些，属预期。
3. **未做**：排队老化（防 dh 长期占满导致其他项目饥饿）。当前符合辉哥要求。
4. **建议**：把 `check_foreign_lock` 的独立 fd 修复同步给 dh 侧知晓（动的是共享代码）。
5. `start_services.sh` 未改（启动方式与默认参数一致，无需改动）。
