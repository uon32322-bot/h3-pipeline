# 项目架构说明 (P0/P1/P2 修复记录)

## 背景

H3 带货视频流水线之前有几个关键问题:

1. **input/ 目录混乱**: 多个项目 (shoes_v3 / shoes_v5) 的 60 个 cell 文件混在一起, 容易误读旧数据
2. **build_segment_workflow.py 硬编码**: `seg{N}_cell1.png` 路径写死, 不接受外部传入
3. **文案/脚本/灵炫 prompt/H3 prompt 三者不一致**: 每个 prompt 独立构造, 容易脱节
4. **/tmp/shoe_*.png 散落**: 无项目隔离, 重跑会被覆盖

## 解决方案

### P0 项目隔离

`scripts/project_state.py` 提供 `ProjectState` 状态管理器:

```
projects/
  ├── shoes_5seg_v5/
  │   ├── input/
  │   │   ├── product.png
  │   │   ├── person.png
  │   │   └── product.txt
  │   ├── output/
  │   │   ├── grids/
  │   │   ├── cells/
  │   │   ├── workflow/
  │   │   └── videos/
  │   └── manifest.json
  └── shoes_5seg_v6/
      └── ...
```

每个项目**完全独立**, 不可能串数据。

### P1 串联一致性 (单一 source of truth)

`ProjectState` 提供 3 个 prompt 派生器, 共享同一 `storyboard` dict:

```python
proj = ProjectState("shoes_5seg_v5")
storyboard = proj.get_storyboard()       # 唯一 source

lingxuan_p = proj.build_lingxuan_prompt(storyboard[0])  # 灵炫 6 cell
h3_p       = proj.build_h3_prompt(storyboard[0])         # H3 6 AddGuide
voiceover  = proj.build_voiceover(storyboard[0])         # 口播稿

# 3 个 prompt 永远一致, 不会脱节
```

### P2 build_segment_workflow.py 修复

旧代码:
```python
"8":  {"class_type": "LoadImage", "inputs": {"image": f"{seg_name}_cell1.png"}},
# 硬编码 seg_name = f"seg{segment_idx}"
```

新代码:
```python
def build_segment_workflow_6cell(segment_idx, ..., cell_paths=None):
    if cell_paths and len(cell_paths) == 6:
        cell_filenames = [os.path.basename(p) for p in cell_paths]
    else:
        # 向后兼容
        cell_filenames = [f"{seg_name}_cell{i}.png" for i in range(1, 7)]
    # 接受外部传入, 不再硬编码
```

## 端到端入口 (新)

```bash
python scripts/run_project.py --project <name> <step>
```

可用 steps:
- `create` 创建项目 + 准备素材
- `llm` 跑产品识别 + 文案 + 5 段分镜
- `grids` 跑灵炫宫格图 (带 VLM 判官自动重生成)
- `cells` 切分宫格图 → 6 cell × 5 段
- `workflows` 生成 5 段 H3 workflow
- `render` 提交 ComfyUI 渲染 5 段
- `concat` 拼接 5 段 → 40s 完整视频
- `all` 一键全跑

## 验证

- 段 2 H3 视频 9/10 完美闭环 (脸一致 + 鞋一致 + 中间锚驱动)
- 5 段 v5 40s 视频已拼接成功
- 段 1 v5 宫格图经判官自动重生成通过 (第 1 次失败 score=42, 第 2 次通过 score=72)

## 已知问题

- 段 3 宫格图连贯性欠佳 (LLM 把"按压中底"理解为"穿鞋")
- 跨段人物一致性仍需加强 (i2i 锁脸不够强)
