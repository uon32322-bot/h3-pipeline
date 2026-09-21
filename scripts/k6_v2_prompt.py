#!/usr/bin/env python3
"""H3 视频 prompt 生成 (基于新宫格图 cell1-6 真实内容, 8 维度 + 官方 YAML).

来源: /Users/admin/Desktop/new_grid_k6.png (720x1280, 3行×2列)
  cell1 (左上): 模特指脸颊 + 敏感泛红 + 皱眉
  cell2 (右上): 挤压白色洁面乳到左手 + 微笑 + 低眉
  cell3 (左中): 低头 + 双手准备 (泡沫开始)
  cell4 (右中): 双手搓泡沫 + 闭眼微笑
  cell5 (左下): 双手捧水洗脸 + 闭眼 + 水流
  cell6 (右下): 微笑 + 展示白色塑料软管 + 指脸颊
"""

# === Subject 1: 人物 ===
SUBJECT_1 = """<Subject 1> is a young East Asian woman, round face, single-eyelid almond eyes with subtle under-eye, small pointed nose, thin natural-pink lips, long straight black hair with a side part, wearing a white cat-ear towel headband and a cream beige knit bathrobe. Her skin has natural texture with visible pores, no plastic-looking, no airbrushed effect. Preserve her facial identity, hairstyle, headband, bathrobe, and body proportions exactly across the entire clip."""

# === Subject 2: 产品 (锁白色矮胖圆管+翻盖) ===
SUBJECT_2 = """<Subject 2> is a white plastic squeeze tube cleanser (the EXACT product from <Picture 1> reference image), a single short cylindrical tube with a flip-cap top. The tube body is white plastic with a small subtle raised dot/grip at the bottom. It is an AMINO ACID FACIAL CLEANSER, NOT a lipstick, NOT a perfume, NOT a foundation bottle, NOT any other cosmetic product. Preserve the exact white tube shape, flip cap, and small bottom grip dot. Only one cleanser tube ever appears."""

# === 6 镜详细描述 (按 cell 真实内容, 8 维度) ===
SHOTS_TEXT = """[Shot 1] Between 0.000s and 1.000s, the woman faces the camera with a concerned expression, her left cheek appears flushed red and sensitive. Her right index finger gently touches her left cheek, pointing out the skin concern. Her lips are slightly parted, natural pink. She wears a white cat-ear headband and a cream beige knit bathrobe. The background is a warm indoor bathroom with soft natural light from the left. Product: NONE visible in this frame. Quantity: only the woman, no other person. Negation: no product, no mirror, no reflection, no other people.

[Shot 2] Between 1.000s and 2.300s, the woman tilts her head down and to the right, smiling softly. Her left hand holds the white plastic squeeze tube horizontally with its flip cap open. Her right hand is positioned below the tube opening to catch the cleanser. White cream cleanser extrudes from the tube opening, dropping into her cupped right palm. The tube body is white with a small grip dot at the bottom; the cap is open and held to the side. Product: the SAME white plastic squeeze tube cleanser with flip cap open, dispensing cream. Quantity: only ONE product visible. Negation: not a lipstick, no red color, no gold cap, no metal cap, no slanted bullet tip, no lip application.

[Shot 3] Between 2.300s and 3.600s, the woman looks down at her hands. Her right hand holds the white plastic squeeze tube vertically, opening pointing downward. Her left palm faces up, receiving a generous amount of white cream cleanser. The cleanser cream forms a small peaked mound on her palm. Product: the SAME white plastic squeeze tube, held vertically, opening pointing down, with a small mound of white cream in the left palm. Quantity: only ONE product. Negation: not a lipstick, no red slanted tip, no bullet protruding.

[Shot 4] Between 3.600s and 4.900s, the woman's hands are positioned together in front of her chest. Her right hand rubs against her left palm in a circular motion, generating abundant creamy white foam lather between the two palms. The foam is rich, abundant, with visible micro-bubbles and a slight sheen. Product: NO product tube visible in this frame, only the foam lather. Quantity: abundant foam. Negation: no lipstick, no applicator, no product tube.

[Shot 5] Between 4.900s and 6.200s, the woman closes her eyes gently and tilts her head slightly back. Her right hand cups clear water that streams downward from her face. Her left hand also gently splashes water on her cheek. Water droplets and foam rinse down from her face. Her skin appears clean and dewy. Product: NO product tube visible. Quantity: only the woman with water and remaining foam residue. Negation: no lipstick, no product tube, no red color.

[Shot 6] Between 6.200s and 8.000s, the woman faces the camera with a bright, satisfied smile showing her teeth. Her skin looks clean, refreshed, and glowing. Her right hand holds the SAME white plastic squeeze tube cleanser vertically in front of her right cheek, displaying the product to the viewer. Her left index finger lightly touches her left cheek (mirroring Shot 1's gesture). The tube's flip cap is closed. Product: the SAME white plastic squeeze tube cleanser with closed flip cap, held vertically, clearly visible. Quantity: only ONE product. Negation: no second product, no lipstick, no mirror."""

# === Summary + Negations ===
SUMMARY = """[keyframe completion] The target video shows <Subject 1> using <Subject 2> (an amino acid facial cleanser) in a continuous 8-second one-take shot. The sequence progresses from identifying a skin concern (Shot 1) to dispensing cleanser (Shot 2-3), lathering (Shot 4), washing face (Shot 5), and final showcase with clean refreshed skin (Shot 6)."""

RETENTION = """<Subject 1> (appears in the entire clip): fully_preserved - retain her facial identity (round face, single-eyelid almond eyes, small pointed nose, thin natural-pink lips), white cat-ear headband, cream beige knit bathrobe, and natural skin texture throughout.
<Subject 2> (appears as the only product in Shots 2, 3, and 6): fully_preserved - retain the exact white plastic squeeze tube shape, flip cap, and small grip dot at the bottom. The product is a cleanser, NOT a lipstick, NOT a perfume bottle.
<Picture 1> (scene anchor): fully_preserved - warm indoor bathroom background, soft natural light from the left, warm beige tones."""

GLOBAL_NEGATION = """No additional people, no other products, no mirrors, no reflections appear at any point. The single product shown is a white plastic squeeze tube cleanser with white cream, never a lipstick, never a perfume bottle, never a foundation bottle, never an eyelash curler, never a red product of any kind. The product never touches the model's lips. The model's lips remain natural pink throughout, never red, never glossy, never colored."""

DETAILED = f"""Live-action authentic TikTok/Reels UGC style with realistic skin texture, smartphone camera, stable tripod-steady framing, no camera shake, realistic shadows, no plastic-looking skin, no airbrushed effect, no makeup overlay, no color grading. One continuous shot, no cuts.

{SHOTS_TEXT}

{GLOBAL_NEGATION}"""

PROMPT = f"""subject_definitions:
{SUBJECT_1}
{SUBJECT_2}

summary:
{SUMMARY}

retention_analysis:
{RETENTION}

detailed_description:
{DETAILED}

overall_soundscape:
Quiet indoor bathroom ambience, soft water dripping sounds during face washing.

non_diegetic_music:
N/A"""

if __name__ == "__main__":
    print(f"prompt 长度: {len(PROMPT)} chars")
    print(f"\n=== PROMPT ===\n{PROMPT[:3000]}")
    # 保存到本地供 row + 推送远端
    import os
    os.makedirs('/tmp/k6_v2', exist_ok=True)
    with open('/tmp/k6_v2/prompt.txt','w') as f:
        f.write(PROMPT)
    # 推送到远端
    import subprocess
    env = os.environ.copy()
    env['SSH_ASKPASS'] = os.path.expanduser('~/.ssh/askpass_nmb2.sh')
    env['SSH_ASKPASS_REQUIRE'] = 'force'
    SOPTS = ["-o","StrictHostKeyChecking=no","-o","UserKnownHostsFile=/dev/null","-o","ConnectTimeout=10"]
    H = "root@connect.nmb2.seetacloud.com"
    r = subprocess.run(['scp'] + SOPTS + ['-P','36229',
        '/tmp/k6_v2/prompt.txt',
        f'{H}:/tmp/k6_v2/prompt.txt'],
        capture_output=True, text=True, env=env, timeout=15)
    print(f'\nprompt pushed: {r.returncode}')