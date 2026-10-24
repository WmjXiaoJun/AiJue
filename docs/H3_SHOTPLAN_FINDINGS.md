# ShotPlan → H3 可控性测试结论（task-37，2026-10-25）

同一角色（outputs/concepts/laozhou/portrait.png）+ 同一段音频（tmp/audio_cn.wav，3.79s）
+ 同 seed 42、768×768、length=124（5.17s），只改 prompt 里的「景别/机位/运镜/光线」：

| 组 | ShotPlan | 文件 | 生成耗时 | 实际效果 |
|---|---|---|---|---|
| A | medium / eye-level / static | A_medium_eye_static.mp4 | 112.5s | ✅ 中景半身（腰以上），构图全片稳定 |
| B | close-up / low-angle / slow dolly-in | B_closeup_lowangle_dollyin.mp4 | 148.9s | ✅ 特写 + ✅ 明显推近（脸占画面 17.5%→30.3%）；低角度不明显 |
| C | wide / high-angle / slow push-in | C_wide_highangle_pushin.mp4 | 116.3s | ✅✅ 广角全身+影棚环境（灯光/幕布/地面），再推近到中景；高角度不明显 |
| D | extreme close-up / static | D_extremecloseup_static.mp4 | 116.3s | ✅ 大特写（脸充满画面，裁到额头/下巴），构图稳定 |

量化（肤色像素占比 t=0.5s → 2.58s → 4.72s，可作景别/推拉代理指标）：

```
A medium/static      skin 15.3% -> 15.7% -> 15.9%   (delta +0.6pp 基本不动)
B close-up/dolly-in  skin 17.5% -> 23.3% -> 30.3%   (delta +12.8pp 明显推近)
C wide/push-in       skin  7.5% ->  8.1% -> 10.6%   (delta +3.1pp 由全身推到中景)
D extreme/static     skin 41.7% -> 41.4% -> 41.9%   (delta +0.2pp 稳定)
```

## 结论：H3（ref2va + audio_ref）对镜头语言的控制力

**真正可控（推荐写进 prompt）**
1. **景别**：medium / close-up / extreme close-up / **wide establishing shot（可超越参考图的半身构图，生成全身+环境）**
2. **推拉运镜**：`slow dolly-in (translation only, no zoom)` / `slow push-in (translation only, no zoom)` 有肉眼可见效果
3. **static**：`static camera, locked-off, zero camera movement` 基本能锁住构图（A/D 全片构图一致）
4. **身份/画风**：参考图脸/发色/服装/赛璐璐风格全片稳定（4 条一致，不换脸）
5. **口型/微动作**：4 条都有嘴部开合 + 眨眼，说话状态成立

**弱可控/不可控（写 prompt 收益低，建议后期/别处解决）**
1. **机位角度**：low-angle / high-angle 只有很弱暗示（B 低角度、C 高角度都不明显）→ 想真正低/高机位需换首帧构图（俯拍/仰拍参考图）
2. **运镜类型/速度**：只稳定实现「推近」；横移/摇镜/升降/变焦基本不按指令走（模型默认轻微推近）
3. **广角镜头**会引入模型自己编的影棚环境（与 pipeline 背景/其他镜头可能不连续），wide 镜头建议单独做首帧再 i2v
4. **极端特写**的裁切位置由模型决定（额头/下巴被裁），需要精确构图时用 crop 后的首帧
5. static 仍存在轻微呼吸/头部微动（不可完全静止，符合口播需要，可接受）

## 推荐 prompt 模板（audio_ref 说话镜头，实测有效）

```text
subject_definitions: Subject 1 is the character in the reference image, preserving their exact face, hairstyle, hair color, outfit, age and anime cel-shaded art style.

summary: One continuous [SIZE] shot, [ANGLE] angle, [HEIGHT] camera height, of Subject 1 speaking directly to the camera in a dark studio with [LIGHT].
Camera: [MOVEMENT] (only this movement, no zoom/pan mix).
Lips move naturally in sync with the reference speech audio; subtle blinking, eyebrow movement and small head nods.
Keep identity, hair, outfit and lighting consistent with the reference image; no text, no watermark.

overall_soundscape: the same spoken sentence, close-mic voice, quiet studio room tone.

non_diegetic_music: N/A
```

[SIZE] 用：`medium (waist-up)` / `close-up (head and shoulders)` / `extreme close-up (face only)` / `wide establishing shot, full body and studio space visible`；
[MOVEMENT] 用：`static camera, locked-off, zero camera movement` 或 `slow dolly-in toward the face (translation only, no zoom)`。

## 文件
- 4 条 clip：A/B/C/D_*.mp4（768×768，5.167s，h264+aac，24fps，124 帧）
- 每条的 8 帧 sheet：*_sheet.png；四条对比大图：compare_shotplan.png；量化指标：compare_metrics.json
- prompt 原文：prompts/{A,B,C,D}_*.txt

生成命令（run_h3_ref.py，audio_ref 模式）：
```powershell
python integrations/comfyui/run_h3_ref.py --mode audio_ref --image outputs/concepts/laozhou/portrait.png --audio tmp/audio_cn.wav --prompt-file outputs/h3/shotplan_tests/prompts/<name>.txt --out outputs/h3/shotplan_tests/<name>.mp4 --duration 5.0 --width 768 --height 768 --seed 42
```