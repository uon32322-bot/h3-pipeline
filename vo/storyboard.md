# 完整带货视频分镜 · 模特使用产品 + 音画同步

产品：粉色气垫粉底（玫瑰浮雕盖 + 金环 + 镜面 + 米色粉芯 + 粉色粉扑）
模特：灰西装 + 米色高领，亮米色房间坐姿
画幅：9:16 竖屏 / 24fps / 0.4MP / 8 步 / FL2VA-INT8 + Turbo v4 + BF16 Triton

## 分镜表（5 段 × 3.0s = 15s）

| 段 | 起点帧 | 终点帧 | 画面动作 | 口播（S1 温暖清晰女声） |
|---|---|---|---|---|
| ① | R_p1 | R_p2 | 右手把合盖气垫从胸前举到脸旁 | This cushion compact is my everyday base. |
| ② | R_p2 | R_p3 | 拇指按开盖子，镜面与米色粉芯露出 | One press, and the mirror does the rest. |
| ③ | R_p3 | R_p4 | 左手捏起粉色粉扑，悬于粉芯上方 | The puff picks up just the right amount. |
| ④ | R_p4 | R_p5 | 粉扑举到脸颊并轻贴 | A soft press, and it melts right in. |
| ⑤ | R_p5 | R_p6 | 移开手、合上盖子、手持产品看向镜头微笑 | Light, fresh, and it lasts all day. |

## 状态帧链（每段从自身起点派生，控制累积漂移）

```
R_p1 ──edit──> R_p2 ──edit──> R_p3 ──edit──> R_p4 ──edit──> R_p5 ──edit──> R_p6
(已有)         (已有)         (已有)         (取粉扑)       (粉扑贴脸)     (合盖收尾)
```

## 音画同步机制

1. 每段口播用 TTS 单独生成一句，转 32kHz 立体声
2. 通过 `MiniMaxH3AddGuide` 的 **audio 输入**把该句锚定到该段第 0 帧
   - 音频被编码为 `audio_latent`，作为 keyframe 每步重注入、不参与去噪 → 输出音轨保留原配音
   - 画面在同一去噪过程中生成 → 口型/动作与配音天然对齐
3. 段与段拼接后形成连续口播

## 段长选择依据

3.0s（73 帧）段：口播句约 2.2–2.6s，动作自然时长约 1.2–2.0s。
比值 ≈ 2×，按实测规则应加 1 个中间锚点；但真实素材中"稳定姿态锚点"会导致"动—停—动"。
**策略：先不加锚点出片，观察哪段动作糊了再针对性补锚点。**
