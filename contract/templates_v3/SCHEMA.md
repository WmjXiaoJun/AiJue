# Template v3 Schema（模板即节目形态）

> 目标：模板定义舞台/角色站位/道具/动作/镜头语言/H3 配置；每镜默认走 ComfyUI H3。校验器在 pipeline/template_v3.py。

## 必填字段
| 字段 | 类型 | 说明 |
|---|---|---|
| id | string | 模板 id（如 tonight_show） |
| label | string | 中文名 |
| style | string | style_presets 的 preset（cinematic_3d / history_ink_2d 等） |
| aspect / target_duration | string / number | 16:9 / 300（5 分钟目标） |
| stage | object | 舞台：type、plate、layout、props、ground_y、camera_zones |
| cast | array | 角色：role/character/block/props/wardrobe；必须 >=2 个角色 |
| action_library | array | 动作库：id + h3_prompt + 可选 prop；至少 6 个 |
| segments | array | 段落：id/name/duration/beats/shots；beats 与 shots 非空 |
| camera_language | object | default/wide/reaction/close 的运镜 |
| packaging / audio | object | 包装/字幕/转场/BGM/音效 |
| h3 | object | workflow（04/05）、duration 范围、fps、resolution |

## stage
```json
{
  "type": "3d_set|2d_plate|h3_scene",
  "plate": "scene_plates 的文件名（含 __preset）",
  "layout": {"zone_id": [x_norm, y_norm], "ground_y": 0.86},
  "props": [{"id":"cue_cards","zone":"desk","interaction":"pick"}],
  "camera_zones": ["front","desk_swap","sofa_ots","window_wide"]
}
```

## cast
```json
{"role":"host","character":"laozhou","block":"desk_center","props":["cue_cards"],"wardrobe":"navy_suit"}
```

## segments[].shots[]
每个 shot 必须含：intent、size（Wide/Medium/Close…）、angle、movement、action（action_library id）、props、h3_mode（audio_ref/fl2v）、camera_zone；
可选 line（台词占位；实际台词由 LLM 台本生成并适配到 beat）。

## 校验规则
- stage.type ∈ {3d_set,2d_plate,h3_scene}；plate 资产必须存在或允许生成；
- cast.character ∈ 现有角色；block 必须存在于 stage.layout；
- action_library 的 id 必须被 segments.shots[].action 引用至少一次；
- segment duration 之和与 target_duration 误差 ≤15%；
- h3.workflow ∈ {04_i2v_fl2v_4step_sla,05_ref2va_4step_sla}；
- 16 模板必须各不相同的 stage.plate + cast 站位组合 + action 使用集合（相似度检查）。

## 与 pipeline v3 映射
- template_v3.load(template_id) → Stage/Cast/Segments；
- storyboard_v3.build(template, script) → shots（beat→shot，台词填充到 shot.line）；
- stage.compose_first_frame(shot) → H3 首帧（角色站位/道具/相机 zone）；
- h3_comfy.generate(shot, first_frame, prompt) → ComfyUI workflow 04/05，记录 prompt_id；
- render_v3.assemble(shots) → 剪辑/转场/包装/混音 → QC。
