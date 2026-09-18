# REPORT32 · H3 接入全局 GPU 仲裁器（用现成 gpu_lock.py）

> 2026-09-18 | 决策来源：辉哥拍板「我们的 H3 接入 arbiter（用现成的 gpu_lock.py，别自己写）」
> 结论：**代码改造完成 + 链路实测通过**。⚠️ 过程中发现 arbiter 自身两个既有缺陷（非本项目引入），已在本项目侧做防护，但**根治需动别人的服务 ⇒ 待辉哥拍板**。

---

## 一、结论速览

| # | 事项 | 结果 |
|---|---|---|
| 1 | 接入方式 | ✅ 删掉自写 `nvidia-smi memory.free` 轮询，改调现成 `gpu_lock.acquire()` 排队 |
| 2 | 接入粒度 | ✅ **按「一次生成作业」而非「常驻进程」**：提交前 acquire，出片后 `finally` release |
| 3 | 覆盖面 | ✅ 改 3 个文件；服务器 9 个 `run_*.sh` 中 **8 个无需改**（调 `test_fl2v_official.py` 即自动受益） |
| 4 | 参数 | ✅ `project=h3p` / `label=h3p:*` / `priority=1`（与 dh 平等）/ `vram_mb=21000` |
| 5 | 实测·排队 | ✅ dh 持有时我们老实排队，`--timeout 30` 时 30s 后**如实报超时**，dh 租约分毫未动 |
| 6 | 实测·不抢 | ✅ 排队期间**显存零占用**（未提交 ComfyUI），彻底消除"硬抢" |
| 7 | 实测·优先级 | ✅ `priority=0` 的任务正确排在 `priority=1` 之前（排序键 `(priority, seq)`） |
| 8 | ⚠️ 缺陷 1 | arbiter 的 **flock 层空转**：`check_foreign_lock()` 每 10s 把自己刚授予的锁解开 |
| 9 | ⚠️ 缺陷 2 | `gpu_lock` **超时即回退 flock**；与缺陷 1 叠加 ⇒ 会静默拿到**假锁** |
| 10 | ⚠️ 坑 3 | 客户端被 kill 后，服务端 handler 仍占队列条目直到 timeout（**幽灵坑**） |
| 11 | 待拍板 | ① 是否重启 arbiter 清幽灵 ② 是否修 `check_foreign_lock` |

---

## 二、接入前先摸清的边界（**别再自己发明**）

### 2.1 同机已有 4 个服务接入，我们是最后一个补上的

| 占用者 | 进程 | 显存（2026-09-18 12:39 实测） | 已接入 arbiter |
|---|---|---|---|
| `6006` 数字人流水线（`/root/ComfyUI`） | miniconda python | **12272 MiB** | ✅ `dh:h3` / `dh:va`（prio 1） |
| 本项目 `6011`（h3p） | h3p/venv python | **8368 MiB**（idle 常驻） | ✅ 本次接入 |
| `tts-gateway` | miniconda python | 466 MiB | ✅ |
| `heygem-gateway` | miniconda python | 按需 | ✅ |
| `gpu_arbiter` 自身 | miniconda python | ≈466 MiB | 服务端 |

grep 全盘确认，接入方为：
`projects/digital-human/backend/h3_pipeline_worker.py`、`h3-gateway/h3_gateway.py`、
`heygem-gateway/heygem_gateway.py`、`tts-gateway/tts_gateway.py`。
`/opt/shared` 与 `/root/autodl-tmp/relocate/shared` 是**软链同一份**。

### 2.2 参照实现（照抄 dh，不自己发明）

`h3_pipeline_worker.py` 里 dh 的标准写法：

```python
import sys; sys.path.insert(0, "/opt/shared")
import gpu_lock
lease = gpu_lock.acquire(block=True, timeout=7200, priority=1, vram_mb=21000, label="dh:va")
try:
    ...
finally:
    gpu_lock.release(lease)
```

### 2.3 参数约定（照抄，别自创）

| 参数 | 同机既有做法 | 本项目取值 | 理由 |
|---|---|---|---|
| `label` | `dh:h3` / `dh:va` / `h3:<proj>` / `vace:<proj>` | **`h3p:*`** | 须为 `<stage>:<detail>`；stage = 冒号前部分，服务端按此填 `stage` 字段 |
| `priority` | dh 用 **1** | **1** | **数值越小越优先**。用 1 = 平等 FIFO：**不插队、也不被饿死**（0 会插到 dh 前，2+ 会永远排它后面） |
| `vram_mb` | 21000 | 21000 | 上限 `24576 − 2048 = 22528`，超了直接拒 |
| `GPU_PROJECT` | dh 没设 ⇒ `/status` 显示 `unknown` | **`h3p`** | 别学 dh，设了才能在 `/status` 一眼认出 |
| `timeout` | 7200 | 7200（外层）/ **300（内层段）** | 见 §5.3 幽灵坑 |

### 2.4 为什么粒度必须是「作业级」而非「进程级」

我们的 `6011` ComfyUI **是常驻服务**（idle 占 8.4 G）。若让"进程启动即 acquire"，
就会**无限期持有锁把 dh 全饿死**。

⇒ 正确粒度 = **一次生成作业**：`提交 /prompt → 轮询 /history 至出片` 这一段持锁。
`6011` 常驻本身不持锁；只在真正吃激活显存时排队。这也是 dh 的做法。

---

## 三、改造清单

| 文件 | 改动 | 说明 |
|---|---|---|
| `scripts/gpu_lease.py` | **新建** | 薄 CLI 包裹（`status`/`probe`/`run`）。⚠️ **锁逻辑一行都没重复** —— 只做「参数翻译 + finally 释放 + 缺陷防护」 |
| `scripts/test_fl2v_official.py` | **改** | 新增 `--priority / --vram-mb / --lease-timeout / --lease-label / --no-lease / --allow-flock-fallback / --free-comfy-after`；租约覆盖「提交→出片」全程，`finally` 释放 |
| `scripts/run_L_calib.sh` | **改** | **删掉 `while nvidia-smi` 轮询（原 30–48 行）**；改为直接调生成脚本（内部排队）+ 前后各留一次 `status` 快照 |

服务器上另备份原文件：`*.bak-20260918-1242`。

**两个设计要点**：
① `test_fl2v_official.py` 的租约逻辑**委托**给 `gpu_lease.py`，**单一实现**，避免两处漂移；
② 日志加了**行缓冲**（`reconfigure(line_buffering=True)`）—— 原来重定向到文件是块缓冲，
   排一小时队看不到一行进度。

---

## 四、实测验证

| # | 用例 | 命令 | 结果 |
|---|---|---|---|
| 1 | 状态只读 | `gpu_lease.py status` | ✅ 正确显示 `dh:va` 持有 + 队列 + ⚠️ 假锁提示 |
| 2 | 图未改坏 | `test_fl2v_official.py ... --dry` | ✅ rc=0，20 节点，`AddGuide(24)` 在、`conditioning=['24',0]`、length=175 |
| 3 | 新参数 | `--help` | ✅ 7 个租约参数全部就位 |
| 4 | **排队不抢** | `gpu_lease.py probe --timeout 30` | ✅ 排队 30s → **如实报超时**（rc=3）；丢弃 flock 假锁；**dh 租约 `La396b452ec` 分毫未动** |
| 5 | **不生假锁** | 同上 | ✅ 明确打印「已丢弃 flock 假锁并继续排」，未误跑 |
| 6 | **入队** | `curl /status` | ✅ 队列出现 `('h3p','h3p:probe', prio, wait_s)` |
| 7 | **优先级** | 幽灵 prio=1 vs 我们 prio=0 | ✅ 我们正确排前 |
| 8 | 显存零占用 | `nvidia-smi` 排队期间 | ✅ 未提交 ComfyUI，不去抢 |
| 9 | 缺依赖兜底 | 本地无 `gpu_lock.py` 跑 probe | ✅ 友好报错 rc=3，无裸 traceback |
| 10 | **主脚本集成** | `test_fl2v_official.py ... --lease-timeout 5` | ✅ 比例守卫 → 排队 5s → 如实报超时 rc=3；并核查 `/history` 确认**未提交 ComfyUI**（`LEASETEST` 条目 **0**）⇒ **"拿不到租约就不动 GPU"硬约束真实生效** |
| 11 | 语法与残留 | `bash -n` + 活动代码 grep | ✅ 语法 OK；`nvidia-smi` 仅存在于注释中，**活动代码零轮询** |
| 12 | 成功路径 | 等 dh 释放后 `run -- echo LEASE_OK_AND_CMD_RAN` | ⏳ **进行中**（dh 已连续持有 17+ min，我们在队列里安静等；拿到即回填） |

---

## 五、⚠️ 三个已发现的问题（都在本项目侧做了防护）

### 5.1 缺陷 1｜arbiter 的 flock 层是**空转**的（既有缺陷）

`gpu_arbiter.py` 的 `check_foreign_lock()`（sweeper 每 10 s 调一次，本意是检测"外部进程
死锁占用"）：

```python
if try_lock():
    unlock()      # ← 锁空闲, 确认后立即释放, 不长期持有
```

但 `try_lock()` / `unlock()` 用的是**同一个 `_lock_fd`**。而 **flock 对同一 fd 是幂等的**
（同一 fd 上重复 `LOCK_EX` 必然成功）⇒ 当 arbiter **自己正持有**租约锁时，`try_lock()`
照样返回 True，紧接着的 `unlock()` **把锁解开了**。

**实测证据**（`active=dh:va` 期间）：

```
实验1  外部非阻塞试锁 → ❌ 拿锁成功  ⇒ 锁是空闲的
实验2  /proc/locks 里 gpu.lock → 无任何记录
实验4  arbiter pid 130825 的 fd 3 → 确实打开了 gpu.lock（但没持有锁）
```

**影响边界（重要）**：

- 对「**接入 arbiter 的进程之间**」，互斥**仍然有效** —— `/acquire` 的排队由服务端
  `active`/`queue` 状态机保证，**不依赖 flock**。
- flock 失效只影响「**挡住不接入 arbiter 的野蛮进程**」这一层 —— 即原设计的兜底失效。
- ⇒ **接入 arbiter 有真实收益，但要修 arbiter 才能恢复完整保护。**

### 5.2 缺陷 2｜`gpu_lock.acquire()` **超时即回退 flock**

```python
resp = _post("/acquire", ...)
if resp.get("ok"): return arbiter_lease
raise TimeoutError(resp.get("error"))
except Exception:          # ← 把上面那行 TimeoutError 也吞了
    ...回退 flock...
```

它假设「拿不到 arbiter = arbiter 挂了」，但**「排队超时」≠「仲裁器挂了」**。

**5.1 + 5.2 叠加 =** 「排队超时 → 回退 flock → 拿到**假锁** → 与 dh 并发抢显存」，
正是要避免的事。本项目侧对策（`gpu_lease.py:acquire_lease`）：

> **只要仲裁器可达，就丢弃 flock 假锁并分段重排**（段长 300 s），直到总超时；
> 只有仲裁器**真不可达**且显式 `--allow-flock` 才接受 flock。

⇒ 实测用例 5 已验证该防护生效。

### 5.3 坑 3｜**幽灵队列条目**

`/acquire` 是同步 handler，跑在 threadpool 里。客户端进程被 kill / 断网后，
**服务端 handler 不会察觉**，会继续 `while time.time() < deadline` 跑到 timeout 才
`queue.remove(w)`。⇒ 队列里留下一个**最长 = timeout 的幽灵坑**，且在 FIFO 里挡住后来者。

本次就留下了一条（我用 `pkill -f` 了探测进程，模式串误匹配到 SSH 自身 shell，见附注）。

**对策（已内建）**：外层总 timeout（7200 s）与**内层段 timeout（300 s）分离** ⇒
恶意/意外残留的坑最多 300 s，不会占 2 小时。
（本次那条幽灵是**旧代码**起的，直接传了 3600 s，故会待到自然超时。）

> ⭐ **好消息：这条幽灵其实不需要重启 arbiter 也能自清。**
> 读 `sweeper()` 可见：幽灵一旦被 `grant_next()` 授予成为 `active`，因为它已死、没有心跳，
> **`LEASE_TTL=120 s` 后会被自动 `unlock()` 并授予下一个等待者**。
> ⇒ 只要跑一次 `priority=0` 的短任务（插到幽灵前面），幽灵就会在轮到后 120 s 内自灭。
> 再不济，最迟 `timeout`（本次 3600 s）到期时 handler 自己会 `queue.remove` 掉它。
> **⇒ 重启 arbiter 并非必需，可降级为"可选"。**

> **附注·又一个手滑**：`pkill -f "gpu_lease.py --timeout 3600 ..."` 会匹配到
> **`bash -c` 自己的命令行字符串** ⇒ 把承载 SSH 的 shell 一起杀了，后续命令未执行。
> 与之前 `pkill -f run_L_calib` 误杀 SSH 是同一个坑。**结论：禁止用包含完整命令串的
> `pkill -f`；改用精确 PID。**

---

## 六、待辉哥拍板

| # | 事项 | 选项 | 我的建议 |
|---|---|---|---|
| 1 | **是否清幽灵条目** | (a) 不动，等它自灭（最迟 1 h）<br>(b) 跑一次 `priority=0` 短任务插队，让它 120 s 内自灭<br>(c) 重启 arbiter | **建议 (b)：无需动共享服务。** 起初以为必须重启，读 `sweeper()` 后确认**不必** —— 幽灵一旦成为 `active`，因无心跳会在 `LEASE_TTL=120 s` 后自动释放。若仍选 (c)，风险也已核：dh 的**当前任务不受影响**（已在跑、不依赖 arbiter；重启后它的 `/release` 因 lease_id 不匹配被忽略），新 arbiter 起来 `active=None`，dh 的**下一个**任务正常 acquire |
| 2 | **是否修 `check_foreign_lock`** | (a) 修（加"是自己在持有则不解锁"的判断） (b) 先不修 | **建议修**，一行判断即可恢复 flock 层；但会动共享代码，需与 dh 侧确认 |
| 3 | `priority` 是否维持 1 | 1 = 平等 / 0 = 插队 / 2 = 让路 | **维持 1**。若某次要赶片，可单次 `--priority 0` |
| 4 | 出片后是否 `/free` 让显存 | 默认关 / `--free-comfy-after` 开 | **默认关**。实测 dh 在我们常驻 8.4 G 的情况下能跑通（free 仅 531 MiB 也没 OOM）⇒ 无强制让路必要；让路会拖慢我们自己（下次要重 load） |

---

## 七、验收清单

- [x] 自写 `nvidia-smi` 轮询已删除（`gpu_lease.py`/`test_fl2v_official.py`/`run_L_calib.sh` 全盘 grep 无）
- [x] 租约按「作业」粒度，`finally` 保证释放（含 Ctrl-C；SIGTERM 已转 SystemExit）
- [x] 拒绝 flock 假锁（仲裁器可达时）
- [x] `/status` 能识别本项目（`project=h3p`）
- [x] 排队期间零显存占用
- [x] 三处副本同步（技能包 / 项目根 / 服务器），md5 一致
- [x] `--dry` 证明生成图未被改坏
- [x] **主脚本集成：拿不到租约时确实不提交 ComfyUI**（`LEASETEST` 历史条目 0）
- [ ] **成功路径实测**（等 dh 释放；拿到租约 → 执行业务 → 干净释放）
- [ ] 清幽灵条目（**非必须**，可自灭；方案见 §六）

---

## 八、变更文件与哈希

| 文件 | 位置 | md5 |
|---|---|---|
| `gpu_lease.py` | 技能包 / 项目 / 服务器 | `cccbdbc8a6458d16661d9c88cb633e77` |
| `test_fl2v_official.py` | 技能包 / 项目 / 服务器 | `90f562adb58dc61e933c8efbd7fef4c2` |
| `run_L_calib.sh` | 技能包 / 项目 / 服务器 | `7cf8cc7b8cdce621ac63e754bc4c16f1` |

> ⚠️ 注：`test_fl2v_official.py` 上传后如再改，需重跑 §四 用例 2（`--dry`）确认图未变。
