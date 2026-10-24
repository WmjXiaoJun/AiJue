# 镜头计划注入（ShotPlan → H3 动态镜头）v1

目标：让同一角色的每个动态镜头有**差异化景别/机位/运镜**，不再是统一的 medium close-up 全画幅；参考 OpenMontage 5-aspect prompt 公式与镜头语法。

## 1. ShotPlan 来源
- 优先 shot.shot_plan（若平台/模板提供）；否则 `pipeline.shot_grammar.plan_for_type(shot.type, variant=shot.no%3)`。
- 字段：size/angle/height/movement/light/purpose/transition。

## 2. Prompt 组装（audio_ref 说话镜头）
~~~text
subject_definitions: Subject 1 is the character in the reference image, preserving their exact face, hairstyle, hair color, outfit, age and anime cel-shaded art style.
summary: One continuous [SIZE] shot, [ANGLE] angle, [HEIGHT] camera height, of Subject 1 speaking directly to the camera in a dark studio with [LIGHT]. Camera: [MOVEMENT] (only this movement, no zoom/pan mix). Lips move naturally in sync with the reference speech audio; subtle blinking, eyebrow movement and small head nods. Keep identity, hair, outfit and lighting consistent with the reference image; no text, no watermark.
overall_soundscape: the same spoken sentence, close-mic voice, quiet studio room tone.
non_diegetic_music: N/A
~~~
- 景别映射：Medium/Medium Close-up/Close-up/Extreme Close-up/Full/Wide；运镜只能从 translation/rotation/lens/hybrid/stillness 选一类；static 必须零运动。
- 反主观形容词：不写 cinematic/moody/epic，写具体参数（光位/景别/时长/运镜）。

## 3. fl2v（反应/动作镜头）
- prompt 用 ShotPlan + 首尾帧说明：「从 first frame 到 last frame 的 [MOVEMENT]，保持身份/构图稳定」。

## 4. 参考图构图（可选增强）
- close-up/reaction：从概念立绘裁脸部特写作 ref；
- medium：现有画窗构图；
- wide/b_roll：扩大背景/黑边（若 H3 不支持拉远则记录限制）。
- meta 记录 plan/prompt/ref_image；便于 QC 复核。

## 5. 转场（后续）
- 按镜头关系选：同场景 hard cut；话题切换 crossfade；段落 fade through black；对话 L/J-cut。本轮先记录 plan.transition，不强制改混剪实现。

## 6. 验收
- 选 ep_002 的 8 个镜头（host/guest/reaction 各若干）按 ShotPlan 生成：相邻镜头景别/角度至少 2 种差异；抽帧可见中景/特写/低角度等；时长/blackdetect/字幕/口型不回归；meta 有 plan。
- 输出在 outputs/shots_plan_demo/，并出一支 40-60s demo 视频。
- pipeline tests 全绿。
