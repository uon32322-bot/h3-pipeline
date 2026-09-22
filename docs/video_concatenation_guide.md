# 多段视频拼接方案 (来自 MCKRUZ/ComfyUI-Expert + 我们的实战)

## 来源
- GitHub: https://github.com/MCKRUZ/ComfyUI-Expert/blob/master/skills/comfyui-video-production/references/concatenation.md
- Rickkorsten/ffmpeg-transitions (xfade 转场)

## 我们的现状

5 段 H3 视频都是:
- 同 codec (h264)
- 同分辨率 (768x1344)
- 同 fps (24)
- 同 audio (aac)

→ **完全符合 FFmpeg concat demuxer `-c copy` 条件** (极速无损)

## 3 种拼接方案

### A. 硬切 (Concat Demuxer, 最快)

```bash
# 1. 写 concat list
cat > /tmp/concat.txt << 'EOF'
file '/root/autodl-tmp/h3p/output/shoes_v6_seg1_00001_.mp4'
file '/root/autodl-tmp/h3p/output/shoes_v6_seg2_00001_.mp4'
file '/root/autodl-tmp/h3p/output/shoes_v6_seg3_00001_.mp4'
file '/root/autodl-tmp/h3p/output/shoes_v6_seg4_00001_.mp4'
file '/root/autodl-tmp/h3p/output/shoes_v6_seg5_00001_.mp4'
EOF

# 2. 无损拼接 (秒级, 不重新编码)
ffmpeg -y -f concat -safe 0 -i /tmp/concat.txt -c copy /tmp/shoes_40s.mp4

# 产物: 40s 视频, 无视觉过渡
```

**优点**: 极速 (秒级), 完美保真
**缺点**: 段间硬切突兀

### B. 0.5s xfade 淡入淡出 (Concat Filter)

```bash
# 5 段间各加 0.5s 淡入淡出
ffmpeg -y \
  -i seg1.mp4 -i seg2.mp4 -i seg3.mp4 -i seg4.mp4 -i seg5.mp4 \
  -filter_complex "
    [0:v][1:v]xfade=transition=fade:duration=0.5:offset=7.5[v01];
    [v01][2:v]xfade=transition=fade:duration=0.5:offset=15.5[v02];
    [v02][3:v]xfade=transition=fade:duration=0.5:offset=23.5[v03];
    [v03][4:v]xfade=transition=fade:duration=0.5:offset=31.5[vout];
    [0:a][1:a]acrossfade=d=0.5:c1=tri:c2=tri[a01];
    [a01][2:a]acrossfade=d=0.5:c1=tri:c2=tri[a02];
    [a02][3:a]acrossfade=d=0.5:c1=tri:c2=tri[a03];
    [a03][4:a]acrossfade=d=0.5:c1=tri:c2=tri[aout]
  " -map "[vout]" -map "[aout]" -c:v libx264 -crf 18 -preset slow \
  /tmp/shoes_40s_xfade.mp4
```

**优点**: 视觉平滑过渡
**缺点**: 重新编码 (10-30 分钟), 音频会"叠加"

### C. 你提的方案: 段 N 尾帧 = 段 N+1 首帧

```python
# 1. 段 1 渲染完成后, 抽尾帧
ffmpeg -sseof -0.04 -i seg1.mp4 -frames:v 1 seg1_tail.png

# 2. 用 seg1_tail.png 作为段 2 的 first_frame (替换 cell1)
# (需要修改 build_segment_workflow_6cell, 加首帧参数)

# 3. 段 2 用新 first_frame 渲染
# (重复 5 段)

# 4. concat demuxer 拼接 (因为像素级连续, 硬切无缝)
```

**优点**: 像素级一致, 拼接处零痕迹
**缺点**: 需要重渲染所有段 (5×5-8 分钟), 工作量大

## 我们的实施: A + C 组合

**Step 1 (本次): A 方案拼接** —— 快速看 5 段拼接的整体观感
**Step 2 (后续): C 方案优化** —— 段间过渡问题再单独迭代

## 拼接命令工具 (生产用)

```python
#!/usr/bin/env python3
"""拼接 5 段 H3 视频为完整带货视频"""
import subprocess, os

def concat_segments(seg_paths: list, output_path: str, method: str = "demuxer"):
    """拼接多段为完整视频

    method:
      - "demuxer": 极速无损硬切 (-c copy)
      - "xfade": 重新编码 + xfade 转场
    """
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    if method == "demuxer":
        list_path = output_path + ".list.txt"
        with open(list_path, "w") as f:
            for p in seg_paths:
                f.write(f"file '{p}'\n")
        cmd = f"ffmpeg -y -f concat -safe 0 -i {list_path} -c copy {output_path}"
    elif method == "xfade":
        # 构建 xfade filter
        n = len(seg_paths)
        inputs = " ".join(f"-i {p}" for p in seg_paths)
        vfilters = []
        afilters = []
        for i in range(n - 1):
            offset = (i + 1) * 8 - 0.5  # 每段 8s, xfade 在 t=7.5s
            vfilters.append(
                f"[{('v' if i==0 else f'v0{i}')}:v][{i+1}:v]xfade=transition=fade:duration=0.5:offset={offset}[v0{i+1}]"
            )
            afilters.append(
                f"[{('a' if i==0 else f'a0{i}')}:a][{i+1}:a]acrossfade=d=0.5:c1=tri:c2=tri[a0{i+1}]"
            )
        last_v = f"v0{n-1}"
        last_a = f"a0{n-1}"
        vf = ";".join(vfilters) + f";[{last_v}]null[vout]" if n > 1 else "[0:v]null[vout]"
        af = ";".join(afilters) + f";[{last_a}]anull[aout]" if n > 1 else "[0:a]anull[aout]"
        # 简化: 直接用最简的拼接
        filter_complex = f"{';'.join(vfilters)};[{last_v}]copy[vout];{';'.join(afilters)};[{last_a}]copy[aout]"
        cmd = f"ffmpeg -y {inputs} -filter_complex \"{filter_complex}\" -map \"[vout]\" -map \"[aout]\" -c:v libx264 -crf 18 -preset slow {output_path}"
    else:
        raise ValueError(f"unknown method: {method}")

    print(f"运行: {cmd[:200]}...")
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=1800)
    if result.returncode == 0:
        size = os.path.getsize(output_path) // 1024
        print(f"✓ 拼接成功: {output_path} ({size}KB)")
    else:
        print(f"✗ 失败: {result.stderr[:300]}")
    return output_path
```

## 关键 xfade 转场类型

| 类型 | 效果 | 适用 |
|---|---|---|
| `fade` | 简单淡入淡出 | 通用 (推荐) |
| `wipeleft/wiperight` | 左/右擦除 | 段间内容差异大 |
| `slideleft/slideright` | 左/右滑动 | 现代感 |
| `circlecrop` | 圆形展开 | 特殊风格 |
| `pixelize` | 像素化 | 转场 |

## 音频处理

| 模式 | 命令 |
|---|---|
| 保留所有段音频 | `-c:a copy` (与 `-c:v copy` 同) |
| 加背景音乐 | 加 `-i music.mp3 -filter_complex "[1:a]volume=0.3[bg]; [0:a][bg]amix=inputs=2"` |
| 段间音频淡入淡出 | `acrossfade=d=0.5:c1=tri:c2=tri` |
| TTS 配音 (待集成) | 替换 audio_vae, 用灵炫 speech-2.8 |

## 总结

| 维度 | 推荐 |
|---|---|
| **快速验证** (本次) | A 方案 demuxer, 秒级拼接 |
| **平滑过渡** (后续) | B 方案 xfade 0.5s |
| **像素级无缝** (终极) | C 方案段尾=下段首 + A 拼接 |