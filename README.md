# H3 带货视频流水线

> 视频上传 **产品图 + 模特图 +（可选）产品文字信息** → 系统自动生成完整带货视频

## 项目概述

基于 MiniMax H3 + ComfyUI 0.36.0 的端到端带货视频生产流水线，部署在共享 3090 服务器（AutoDL nmb2）。
包含 14 个锁定决策（D1-D14）、41 项判官、3 套时长档（32/40/50s）、5 段式带货结构（钩子/痛点/证明/信任/CTA）。

## 关键文档

| 文档 | 用途 |
|---|---|
| [`部署方案与实施进展_20260918.md`](部署方案与实施进展_20260918.md) | 项目基线、5 套历史成片、闸门规则 |
| `REPORT18_极速链路选型与提速整合.md` | 提速档位定义 (T0/T1/T2/T3) |
| `REPORT29_带货结构层.md` | 五段式 + 9 源交叉验证 |
| `REPORT30_音频层与30-50s时长重构.md` | L/V/N 路由 + 时长档 |
| `REPORT31_补L_L镜落点与中文口型标定.md` | A 段判官 |
| `REPORT33_GPU仲裁器队列修复与空闲释放.md` | GPU 共享纪律 |
| `REPORT34_带货视频深化方案_v3.0.md` | 4 方向深化方案（速度/文案/动作/判官）|
| `REPORT35_社区成熟实例调研与照搬方案_v1.0.md` | joeygambino Multishot 照搬 + 5 镜中文带货 |
| `REPORT36_社区实例照搬执行_20260918.md` | W1-W4 实际执行报告（待写）|

## 目录结构

```
h3-pipeline/
├── 部署方案与实施进展_20260918.md   # 主入口
├── REPORT*.md                         # 历史报告
├── judge_config.yaml                  # 判官配置契约
├── shotlist_schema.json               # shotlist 接口契约
├── 带货视频生成SOP.md                  # 9 步 SOP
├── 部署指南_判官团抽卡生产流水线.md    # 操作指南
├── templates/                         # 4 个模板
├── deliverables/                      # 5 套历史成片
├── external_repos/                    # (gitignored) 第三方 fork
│   ├── multishot/                     # joeygambino/MiniMax-H3-Multishot-Workflow
│   └── ComfyUI-sol-attn-saganaki/     # Saganaki22/ComfyUI-sol-attn
├── output/                            # 生成的视频输出
├── prompt/                            # prompt 文件
├── log/  logs/  temp/  tmp/  img/      # 运行产物
└── (新) scripts/                      # 判官代码（已 push 到服务器）
    ├── judge_l0.py
    ├── judge_l1.py
    ├── judge_audio.py
    └── judge_shot.py
```

## 当前状态（2026-09-18）

- ✅ **环境冻结**（`env_manifest.txt` + addendum）
- ✅ **5 节点包** 装到 6011（Multishot / GGUF / Motion-Context / RES4LYF / sol-attn）
- ✅ **GGUF Q5_1** 下载（25.92 GB, sha256 验证）
- ✅ **3 镜 + 5 镜中文带货 workflow**（`H3_Chain_*.json`）
- ✅ **判官 L0/L1/A/Shot** 全部实现（23 项，warn_only 模式）
- ⏸️ **3 镜 demo 端到端**（API 格式问题未解决，pending）
- ❌ **5 镜生产 demo**（依赖 3 镜 demo 先跑通）

## 快速开始

```bash
# 服务器端（已部署）
ssh nmb2
cd /root/autodl-tmp/h3p
bash scripts/start_comfyui.sh  # 启动 ComfyUI 6011

# 上传 workflow + 跑生成
# (用 Python 调 /prompt API, 见 REPORT35 §3)

# 跑判官
python3 scripts/judge_shot.py output/H3XXX/clip.mp4 768 1344 192 24 8.0
```

## 4 条硬规范

1. **模型白名单**：生图只准 `tt-image-2`，判官只准 `tt-5.6-luna`，视频禁用云端
2. **存储全落数据盘**：服务器上一律 `/root/autodl-tmp/`，系统盘不留任何产物
3. **判官隔离**：判官权重不上 3090，异步解耦
4. **每环节 <70 阻断 + 重生不通过则返工**（详见部署方案）

## License

本项目代码部分私有（不公开商用）。第三方仓库（external_repos/）保留各自上游 license。
