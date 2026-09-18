# AGENTS.md · Agent 协作规范

> 本文件定义 AI Agent 在本项目中的协作规范。所有 Agent 操作必须遵守。

---

## 1. 改动追踪

**每次改动完成后，都必须创建一个对应的 git commit，以便后续追踪和回滚。**

- **commit 颗粒度**：每个独立的逻辑改动（功能 / 修复 / 重构 / 配置）单独一个 commit
- **commit message 格式**：
  - `<type>: <中文一句话描述>`
  - type ∈ {init, feat, fix, refactor, docs, test, chore, perf, style}
  - 关键改动可在 message body 里附 `Refs: REPORTxx` 或 `Refs: <file:line>` 引用
- **禁忌**：
  - ❌ 一次 commit 改 N 个不相关的东西（不便回滚）
  - ❌ 跳过 commit 让用户"自己看 diff"（用户没时间翻）
  - ❌ 把临时调试代码 commit 进去（污染历史）
- **commit 频率**：
  - 任务中间的小步改动也要 commit（即使没完成，例如 `WIP: W3.5a 接 s7_judge`）
  - 阶段性产出更要 commit（如"✅ W3.5a 完成：s7_judge 接入 judge_shot.py"）
- **回滚策略**：用户要求回滚时，按 `git log` 找最近稳定 commit → `git revert <hash>` 或 `git reset --hard <hash>`（后者慎用）

---

## 2. 测试与验证

**每次改动后，都必须编写或更新相关测试，并在交付给用户前，确保所有测试和验证全部通过。**

### 2.1 测试范围

每次改动至少要覆盖以下验证（适用范围内）：

| 改动类型 | 必须验证 |
|---|---|
| 配置文件 | Python 语法检查、JSON/YAML schema 校验 |
| Shell 脚本 | `bash -n` 语法 + dry-run（如果有） |
| Python 脚本 | 编译通过 + 用真实数据跑一次 + 输出格式检查 |
| 工作流（JSON） | 节点 schema 校验（API 提交测试） |
| 服务器端配置 | 重启服务后 API 健康检查 + 关键节点可见 |
| 判官/测试代码 | 用已有成片跑一次，比对预期 verdict |

### 2.2 验证纪律

- **每改必测**：改完不测试 = 没改
- **必须用真实数据**：测试数据来自现有成片（5 套历史 deliverables）或 sandbox 构造，**禁止纯 mock**
- **失败必须报**：测试失败时，**立即停下向用户报告**，禁止静默通过或自动重试
- **修复后重测**：bug 修复后必须重跑原测试 + 回归测试
- **交付前最终验证**：用户验收前必须跑一遍完整链路（不是单测）

### 2.3 验证流程

1. **本地验证**（开发机）：语法、单元测试
2. **服务器验证**（如适用）：远程服务健康检查 + 真实数据跑
3. **集成验证**：端到端跑一次（例：判官链 + orchestrator + 实际视频文件）
4. **记录结果**：在 commit message 或对应的 REPORT 中记录"验证证据"

### 2.4 失败处理

- **测试 3 次仍失败** → 停止自动重试，向用户报告
- **报告内容**：测试名 + 输入 + 预期 + 实际 + 错误堆栈前 20 行
- **不掩盖**：禁止把失败测试 disable / skip，必须直面

---

## 3. 与本项目其他规范的关系

- **本文件优先**：与 README.md、部署方案、其他 REPORT 冲突时，本文件优先
- **不替代技术规范**：闸门规则（D1-D14）、判官契约（judge_config.yaml）、schema 契约（shotlist_schema.json）继续有效
- **变更需用户拍板**：本文件本身是 agent 行为规范，**改本文件需要用户明确同意**

---

## 4. 例外条款

- **文档/注释微调**（typo 修正、链接更新、标点）：可合并到下一个功能 commit
- **临时调试输出**（`print`、注释、孤立脚本）：**禁止入 commit**，实验完即删
- **大文件回写**（如 25 MB PNG 重生成）：用 `git status` 确认文件未变，避免无意义 diff
- **服务器端代码**（scripts/、comfy/ 等）：本地仓不直接编辑，通过 SCP 推到服务器。本仓里只保留 `external_repos/` 的本地 fork（已经 gitignore）

---

## 5. 自检清单（每次 commit 前过一遍）

- [ ] 改动文件全部 `git add`？
- [ ] commit message 用了 `<type>: <描述>` 格式？
- [ ] 没有把 `__pycache__`、`.DS_Store`、调试 print 混进去？
- [ ] 至少跑过 1 次真实数据验证？
- [ ] 如果有 schema 变更，更新了对应 JSON schema / yaml？
- [ ] 如果是新功能，commit message 说明了用户能感知什么？
