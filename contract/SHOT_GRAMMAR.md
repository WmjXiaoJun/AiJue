# 镜头语法（参考 OpenMontage / Shot Grammar v1）

> 来源：OpenMontage 的 `skills/creative/video-gen-prompting.md`（镜头/机位/运镜/光线词汇）、`skills/creative/video-stitching.md`（转场决策树）、`skills/pipelines/cinematic/edit-director.md`（剪辑原则）、`pipeline_defs/*.yaml`（分阶段流水线）。本平台取其方法论，用自研代码实现。

## 1. 镜头级字段（ShotPlan）
每个镜头除 type/duration/line/speaker 外，增加：
| 字段 | 取值 | 说明 |
|---|---|---|
| size | Wide / Full / Medium / Medium Close-up / Close-up / Extreme Close-up / Insert | 景别 |
| angle | eye-level / high-angle / low-angle / dutch / bird's-eye / OTS / POV | 机位角度 |
| height | aerial / overhead / eye / hip / ground | 机位高度（可选） |
| movement | static / dolly-in / dolly-out / pan / truck / zoom / rack-focus / handheld | 运镜（静态必须零运动） |
| light | key+rim / high-key / low-key / practical / ambient / golden hour | 光线 |
| transition | hard_cut / crossfade / fade_black / L_cut / J_cut | 入点转场 |
| purpose | open / establish / explain / dialogue / emotion / reaction / punchline / close | 镜头目的 |

## 2. 镜头类型 → 默认语法（pipeline/shot_grammar.py）
| shot type | 默认景别/机位/运镜/光线 |
|---|---|
| title_card | GRAPHIC · 静止 · 品牌 |
| host_to_camera | Medium · eye-level · slow dolly-in · key+rim |
| shot_reverse | Medium Close-up · eye-level · static/micro-pan · key+rim |
| reaction | Close-up · eye-level · static · low-key practical |
| meme_card | INSERT/GRAPHIC · static |
| b_roll | Wide/Establishing · high-angle · slow push-in · ambient |
| end_card | GRAPHIC · 静止 · 品牌 |

## 3. 运镜三组（不可混用）
- Translation：dolly in/out、truck left/right、pedestal up/down；
- Rotation：pan、tilt、roll；
- Lens-only：zoom、rack focus、pull focus；
- Hybrid：dolly zoom、orbit、crane、whip pan、tracking、handheld；
- **static = 零运动/零变焦/零跟焦**；不要同时写 dolly 和 zoom。

## 4. 转场决策树
| 关系 | 转场 | 时长 |
|---|---|---|
| 同场景连续动作/同话题换角度 | hard cut | 0ms |
| 话题切换 | crossfade | 0.5–1.0s |
| 时间/情绪跳变 | crossfade | 1.0–1.5s |
| 大段落（片头→正片、正片→片尾） | fade through black | 0.5–1.0s |
| 对话交接 | L-cut / J-cut（音频先行 0.3–0.5s） | 0.3–0.5s |

## 5. 生成 Prompt 公式（5 要素，OpenMontage 通用公式）
1. **Subject**：固定角色身份锚点（发型/服装/颜色/年龄/画风）；
2. **Subject motion**：具体动作（说话/点头/挑眉），不要抽象情绪词；
3. **Scene**：地点/时间/氛围/背景；
4. **Spatial framing**：景别 + 机位高度 + 角度 + POV + 构图（左/右画窗、留白）；
5. **Camera**：运镜（从三组里选一类）+ 镜头/景深/焦段。
> 避免主观形容词（cinematic/moody/epic）：用「2.39:1 + 24fps + 8s + 低照度侧光」这类可执行参数替代。

## 6. 剪辑原则（Edit Director）
- 按情绪切，不按信息密度切；
- 保护强瞬间，不要用多余覆盖镜头埋没；
- 声音推动剪辑（ambience/impact/music turn）；
- 片头卡少而精准；节奏变化（长→中→短→短→长），不要连续 3 个同长度镜头。

## 7. 平台落地
- `pipeline/shot_grammar.py`：TYPE_PLANS / TRANSITION_RULES / label()；模板 pattern → 镜头级 plan；
- `scripts/render_template_demos.py`：为 16 个模板生成镜头级验证片（含景别/机位/运镜标签、xfade 转场、TTS/字幕）；
- 现有模板 JSON 的 segments.pattern 仍是粗粒度；后续由 backend 骨架生成时按 plan_segment() 展开成多镜（本轮先用于验证片与文档）。
- 动态 H3 镜头下一步将把 ShotPlan 注入 ref2va/fl2v prompt（身份锚点 + 景别/机位 + 运镜），替代当前统一中性 prompt。
