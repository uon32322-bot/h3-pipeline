/**
 * H3 prompting spec (local MiniMax H3 + Multishot chain sampler).
 *
 * Built from three sources of evidence:
 *   1. ComfyUI-H3-Multishot/PROMPTING.md + example_script.txt - the sampler's own
 *      format is free prose, one prompt per shot, "---" between shots. No section
 *      labels and no instruction line.
 *   2. A verified successful run: a 5-shot video used
 *      "Shot 1: <prose> ... The narrator says in Chinese: ... [background_audio] ..."
 *      It contained neither integrated_multimodal_description: nor overall_soundscape:.
 *      Those belong to the SINGLE-SHOT I2VA/FL2VA node - a different code path.
 *   3. fal/MiniMax-H3-Realism-People-LoRA model card: the realism LoRA trigger word
 *      is r34l1sm and it must start the prompt.
 */
export const H3_PROMPT_SPEC = `
【H3 官方提示词规范（本地 MiniMax H3 + Multishot 链式采样器）—— prompt 字段必须严格遵守】

① 格式：每镜的 prompt 就是「r34l1sm, 」+ 一段英文自由叙述。
   照官方 PROMPTING.md 的 example_script.txt 写法：自由叙述，一口气把画面讲清楚。
   不要写标签式结构：不写 integrated_multimodal_description:、不写 overall_soundscape:、
   不写 non_diegetic_music:（那是单镜 I2VA/FL2VA 节点的格式，与本采样器不是同一条代码路径）。
   也不要写 "For the target video, at 0.00 seconds ... <Picture 1> is fully referenced." 这类指令行。

② 叙述顺序（照官方示例，一句话接一句话从头写到尾）：
   相机与构图 → 风格与画质 → 人物的完整外观 → 场景与道具细节 → 本镜动作 → 台词 → 收尾状态
   示例：
   r34l1sm, A static camera frames a 28-year-old Chinese woman from the chest up in a bright
   modern kitchen; live-action, shallow depth of field, fine 35mm grain. She has straight
   black shoulder-length hair and wears a cream oversized knit sweater. She picks up the
   single wireless earbud case from the white countertop and says in Chinese: "……"
   结尾再写她把手放下、回到稳定姿势并停留约 2 秒。

③ 触发词必须在第一位（位置错了 LoRA 不生效）：
   官方 model card 原文：Start the prompt with the trigger word r34l1sm, then describe the scene.
   → prompt 的【第一个字符起】就是 r34l1sm，紧跟一个逗号，然后才开始描述画面。
   - 拼写一字不差：r34l1sm（小写 r、数字 3、数字 4、小写 l、数字 1、小写 s、小写 m）
     常见错拼：realism / r34lism / r34l1smn / r3411sm —— 拼错等于没挂 LoRA。
   - 每一镜的 prompt 都各自以 r34l1sm 开头，绝不放到段尾、绝不省略。
   - 不要再抄写任何"皮肤/真实度"形容词串来加强真实感（例如 true-to-life skin texture
     preserved 之类）：真实度由 r34l1sm 触发词负责，堆形容词只会自相矛盾（磨皮/塑料感）。

④ 台词与环境音写法（照官方示例）：
   - 台词直接写进叙述：and says in Chinese: "台词原文"（中文原样，不翻译、不用 <d> 标签）
   - 一句台词不跨镜：镜长 8 秒约 30 个中文字；说不完就整句挪到下一镜，绝不拆句。
   - 环境音用方括号标注：[background_audio] quiet indoor studio, a soft whoosh, then stillness

⑤ 镜间一致性（逐镜生成的命脉）：每镜必须【逐字重复】人物的完整外观描述 + 场景与光线描述
   （逐字相同，不是改写、不是同义替换）。改写描述是"中途换脸/换场景"的首要原因。

⑥ 禁止项（违反即出废片）：
   - 禁否定句：不写 no X / does not move / without Y —— CFG=1.0 没有负向分支，否定会把概念
     喂给模型（写 no glossy highlights 反而画出高光）。一律改写成【正向 + 明确落点】。
   - 禁静止短语：不写 goes still / stays motionless / remains frozen / exactly as they were
     —— 会把整帧冻住。要写正向的持续运动（呼吸、重心微移、视线变化）。
   - 禁扩散模型标签：不写 cinematic / 8k / masterpiece / high contrast / ultra detailed / best quality。
   - 禁绝对位置与占比：不写 at frame LEFT / occupies 30% of the frame。
   - 禁在镜与镜的边界改变场景（会造成双人/双道具），场景变化要写在该镜中段。
   - 禁在画面里出现任何文字/字幕，除非该镜本身就是文字卡。
`;
