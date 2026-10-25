# V3 实施接口契约（P0→P2 协作基线）

> 目标：按 `docs/PLAN_V3_TEMPLATE_REFACTOR.md` 落地 v3：模板=节目形态；每镜 stage 首帧 → ComfyUI H3 → 剪辑 → QC；静态只做 previs/兜底并在 QC 标记。
> 本文件是各模块之间**唯一的接口约定**。改动接口先改这里，再通知 Lead。

## 0. 目录与产物

```
contract/templates_v3/*.json         16 个模板（tonight_show/history_talk/... 见 §4）
pipeline/template_v3.py              加载 + 校验 + CLI（P0，已由 Lead 完成）
pipeline/stage.py                    舞台首帧合成（plate + 多角色站位 + 相机 zone + 卡片帧）
pipeline/action_library.py           动作库 → H3 prompt（action/camera/identity/scene）
pipeline/storyboard_v3.py            模板 + 台本 → shot 序列（render.py 兼容）
pipeline/h3_comfy.py                 ComfyUI H3 客户端（04/05 workflow；记录 prompt_id/workflow/seed）
pipeline/render_v3.py                编排：storyboard → stage → H3 每镜 → 剪辑/包装/混音 → QC
pipeline/qc_v3.py                    v3 QC 门（H3 覆盖/可追溯/静态标记/黑帧/时长/字幕）
scripts/render_templates_v3.py       批量 16 模板渲染（GPU 串行、可断点续跑）
scripts/gen_v3_plates.py             生成 4 张新场景 plate（SDXL，不用 Blender）
outputs/templates_v3/{id}/final.mp4
outputs/templates_v3/{id}/shots/          每镜成片片段（h3 或 card）
outputs/templates_v3/{id}/frames/         抽帧 + contact_sheet.png
outputs/templates_v3/{id}/h3_meta.json    每镜 H3 证据（workflow/prompt_id/seed/ref_image/action）
outputs/templates_v3/{id}/qc_report.json  QC 报告
outputs/templates_v3/{id}/work/           episode.json / tts / audio / dynamic / manifest.json
outputs/templates_v3/batch_report.json    16 模板批量结果
```

## 1. pipeline/template_v3.py（已实现，勿改签名）

```python
load(template_id, template_dir=None) -> dict          # 找不到抛 TemplateError
load_file(path) -> dict
validate(template, *, template_dir=None, presets=None, characters=None,
         strict_plates=True) -> {"id","ok","errors","warnings","stats"}
validate_dir(dir, *, expect=None, strict_plates=True) -> {"ok","total","passed","failed","results"}
plate_path(template) -> Path | None
shots(template) -> list[dict]        # 展平 segments.shots，附 segment_id/segment_name/segment_index/shot_index/index
cast_for(template) -> list[dict]     # cast + contract/characters 资料（含 character_id/name/voice/accent_color）
cast_by_role(template) -> dict
actions_by_id(template) -> dict
props_by_id(template) -> dict
stage_layout(template) -> dict       # {zone:[x,y], ground_y:float}
template_signature(template) -> tuple
load_all(template_dir=None) -> dict[str, dict]
```

CLI：`python -m pipeline.template_v3 --validate contract/templates_v3 --expect 16`

## 2. pipeline/stage.py（stage-dev 负责）

```python
DEFAULT_W, DEFAULT_H = 1024, 576      # 16:9；≈768² 像素预算，H3 32 倍数；成片再升 1280×720

load_plate(template) -> Path                       # 复用 template_v3.plate_path；缺失 raise
cutout_for(template, character_id) -> dict         # {"path","style","fallback","kind"}；style: cutout_3d/cutout_2d
describe_scene(template) -> str                    # stage.scene_desc + plate 名兜底

compose_first_frame(template, shot, out_png, *, width=1024, height=576,
                    workdir=None, index=0) -> dict
# 返回：{"path","width","height","plate","camera_zone","crop":[x,y,w,h]（plate 归一化）,
#        "characters":[{"role","character","block","bbox":[x,y,w,h]（输出像素）,"cutout","cutout_style","cutout_fallback"}],
#        "props":[...], "ground_y":..., "style":..., "h3_input": True}
# 规则：
# - 背景 = plate 按 camera_zone 取景（front/wide/desk_swap/sofa_ots/window_wide/...，
#   未知 zone 用全画面）；zone 表见模块内 ZONE_VIEWS，可扩展；
# - cast 全部入场：block → 站位；按 ground_y/block.y 落脚；按 shot.size 定高度比例
#   （Wide 0.42H / Full 0.52H / Medium 0.60H / Medium Close-up 0.56H / Close-up 0.86H）；
#   同一 block 只放一个角色；说话人（shot.speaker_role 若给）居中/在前；
# - 2D 风格 preset（character_style=cutout_2d）用 cutout_2d，3D 用 cutout_3d，缺失回退并记 fallback=True；
# - 柔和椭圆投影 + 轻调色（不烘焙文字/字幕/包装，避免 H3 生成文字伪影）；
# - 输出 PNG（RGB）。

compose_last_frame(template, shot, first_meta, out_png, *, width=None, height=None) -> dict | None
# fl2v 尾帧：在首帧基础上按 shot.movement 做 3-8% 推/移/摇（PIL 裁切重采样），
# 返回 {"path","width","height","motion"}；无相机运动时返回 None（调用方用首帧兜底）。

compose_card_frame(template, shot, out_png, *, width=1024, height=576) -> dict
# 片头/片尾/манифест卡片帧（静态，允许非 H3；QC 须标记 static_card）。
```
注意：`shot` 是 storyboard_v3 产出的 dict，含 `role`（说话角色 role）、`speaker`（角色资产 id）、`v3`（模板原始 shot：action/size/movement/camera_zone/props/h3_mode/intent）。

## 3. pipeline/action_library.py（stage-dev 负责）

```python
resolve_action(template, action_id) -> dict          # action_library 条目，缺失 raise KeyError
camera_phrase(movement, size=None) -> str            # 运镜英文短语（供 H3 prompt）
subject_definitions(template) -> str                 # "Subject 1 is the host (laozhou)..."
scene_sentence(template) -> str                      # 场景一句英文（stage.describe_scene）
build_h3_prompt(template, shot, *, mode, style_prefix=None) -> str
# 结构（H3 官方四段）：
# subject_definitions: ...\n\nsummary: One continuous <size> shot ... <action.h3_prompt> ...
# <camera_phrase>; keep faces/hairstyles/outfits/background stable; <style_prefix>\n\n
# overall_soundscape: ...\n\nnon_diegetic_music: N/A
# mode=fl2v 时 summary 首句改为 "integrated_multimodal_description: [Shot 1] A smooth cinematic move
# from the first frame to the last frame: ..."
build_action_variants(template) -> dict              # 供 storyboard 选择动作
```
约束：prompt 里**不写台词文本**（台词只进音频）；动作必须来自模板 action_library（可加 props 描述）。

## 4. pipeline/h3_comfy.py（h3-dev 负责）

```python
DEFAULT_SERVER = "http://127.0.0.1:8188"
DEFAULT_SEED = 20261026
WORKFLOWS = {"audio_ref": "05_ref2va_4step_sla", "fl2v": "04_i2v_fl2v_4step_sla"}

health() -> {"ok","server","queue_running","queue_pending","workflows":[..]}
workflow_for_mode(mode) -> str
generate(shot, first_image, out_mp4, *, mode, prompt, duration,
         audio_wav=None, last_image=None, seed=None,
         width=1024, height=576, fps=24, workdir=None, timeout=1800) -> dict
# 内部调 integrations/comfyui/run_h3_ref.py（其 stdout 最后一行 JSON 已含 prompt_id/workflow/seed/gen_sec）；
# 返回并原子写 <out stem>.meta.json：
# {"h3":true,"ok":true,"mode","workflow","prompt_id","seed","duration","length","width","height","fps",
#  "ref_image","last_image","audio","action","gen_sec","path","probe":{...},"created_at":...}
# 失败 raise H3Error（不允许静默静态兜底；兜底只能由 render_v3 显式 --fallback-static 开启并写 QC 标记）
normalize_clip(src, dst, *, duration, width=1280, height=720, fps=25) -> dict
# H3 原生 768²/1024×576/24fps → 成片规格 1280×720/25fps/精确时长（trim/tpad）
meta_ok(meta) -> bool           # 必须 h3=true + workflow + prompt_id + seed 齐全
```
关键：每镜 meta 必须含 `workflow / prompt_id / seed / ref_image / action`；`prompt_id` 来自 ComfyUI `/history`。

## 5. pipeline/storyboard_v3.py（render-dev 负责）

```python
build_plan(template, target_duration, *, script=None, include_cards=True,
           seed=20261026) -> {"episode":{...},"assets":[...],"shots":[...],
                              "cards":{"title":{...},"end":{...}},"meta":{...}}
```
规则：
- `target_duration` 30-45（demo）或 300（5 分钟版）；H3 单镜固定 5.17s（H3 最小 124 帧），
  cards：title 2.0s + end 2.5s；shots 数 = round((target - cards)/5.17)；
- 先按模板 `segments[].shots[]` 顺序取镜，不够则跨段循环；每镜继承模板 shot 的
  size/angle/movement/action/props/camera_zone/h3_mode/intent；
- 台词：`script` 参数或自动读 `outputs/llm_scripts/{template_id}.json`（16 个已存在）→
  按 segment 顺序填到 shot.line；不足则用模板 beats 生成的占位台词；说话角色按 action/segment 轮转；
- 每条 shot 输出 render.py 兼容字段：id/no/type/duration/speaker/speaker_name/line/visual/
  lower_third/sfx/shot_plan，外加 `role`、`v3`（模板 shot 原样）、`h3_mode`、`action`；
- type 映射：h3_mode=audio_ref → host_to_camera/shot_reverse（按角色）；fl2v → b_roll/reaction；
  卡片 → title_card/end_card；`episode` 含 resolution 1280x720/fps 25/aspect 16:9/format_id。

## 6. pipeline/render_v3.py + pipeline/qc_v3.py（render-dev 负责）

render_v3 主流程（CLI：`python -m pipeline.cli render_v3 --template tonight_show --duration 45 --h3 comfy`）：
1. load+validate 模板（失败即退出，不渲染）；
2. storyboard_v3.build_plan → episode.json（写 work/episode.json）；
3. TTS：复用 `pipeline.render.prepare_tts / build_shot_audio / export_shot_tts_audio / _voice_map`；
4. 每镜：非卡片 → `stage.compose_first_frame`（+fl2v 时 `compose_last_frame`）→
   `h3_comfy.generate`（带 prompt_id）；卡片 → `stage.compose_card_frame` + ffmpeg 静帧 clip；
5. `h3_comfy.normalize_clip` 统一 1280×720/25fps；
6. 剪辑：复用 `pipeline.render.concat_video_xfade / concat_video / build_transition_sfx`，
   字幕 `pipeline.ass_builder.build_ass`，混音 `concat_audio + ensure_bgm`，
   调色/包装 `style_v2.grade_filter / packaging_filters`，`render.final_encode`；
7. QC：`qc_v3.evaluate(...)` → `qc_report.json`；成片/证据/抽帧落 `outputs/templates_v3/{id}/`；
8. 摘要 JSON 落 stdout 最后一行 + `manifest.json`。

qc_v3.evaluate(*, video, plan, shot_metas, template, subtitles=None, h3_require=True) -> dict：
- checks：duration（±0.6s）/resolution 1280x720/fps 25/audio/blackdetect=0/subtitle_coverage/
  h3_coverage（非卡片镜 100% h3=true）/traceability（workflow∈{04,05}、prompt_id 非空唯一、seed 存在）/
  static_marked（静态仅卡片或显式兜底，兜底必须 fallback=true 并列出）/stage_meta（每镜 ref_image 存在）；
- `ok` = 全部 check ok；`fallbacks` 列出静态兜底镜头（QC 报告必须标记，验收不接受非卡片静态兜底）。

## 7. 模板 v3 字段（contract/templates_v3/SCHEMA.md + 扩展）

必填：id/label/style/aspect/target_duration/stage/cast/action_library/segments/camera_language/packaging/audio/h3。
- style ∈ style_presets.presets；target_duration=300（5 分钟目标）；
- stage = {type(3d_set|2d_plate|h3_scene), plate(plate 文件名), scene_desc(英文场景描述，必填), layout{zone:[x,y],... ,ground_y}, props[{id,zone,interaction}], camera_zones[>=2]}
- cast[>=2] = {role, character(laozhou/ali/dawei), block(layout zone), props[], wardrobe}
- action_library[>=6] = {id, h3_prompt(英文动作描述), prop|null, optional?}；默认每个 action 至少被一个 shot 引用（`optional:true` 可豁免）
- segments[>=2] = {id,name,duration,beats[非空],shots[非空]}；segments duration 合计 = target_duration ±15%
- shots[] = {intent,size,angle,movement,action,props,h3_mode(audio_ref|fl2v),camera_zone}
- camera_language = {default,wide,reaction,close}；packaging = {lower_third,transition(∈hard_cut/fade/crossfade/fade_black),bgm,subtitle_style}；audio={ambience,sfx[]}
- h3 = {workflow(05_ref2va_4step_sla|04_i2v_fl2v_4step_sla), duration[2..10], fps(24|25), resolution(32 倍数，如 1024x576)}

## 8. 16 模板 → plate/style 映射（新增 4 张 plate 由 gen_v3_plates.py 生成）

| template | style | stage.plate | 舞台/主要站位 |
|---|---|---|---|
| tonight_show | cinematic_3d | late_night_desk__cinematic | 深夜秀 desk + 沙发；host desk / guest 沙发 |
| shark_tank | cinematic_3d | pitch_room__cinematic（新） | 投资路演会议室长桌；host 主位 / 嘉宾两侧 |
| masked_singer | neon_stage_3d | stage_music__neon | 霓虹舞台；host 侧台 / 歌手台中央 |
| standup | retro_standup_2d | stage_music__vhs | 复古单口舞台；host 麦克风位 |
| taskmaster | game_show_3d | taskmaster_stage__game | 综艺任务舞台；host 台侧 / 嘉宾任务区 |
| traitors | roundtable_3d | castle_hall__roundtable（新） | 圆桌密谈；三人圆桌 |
| snl_sketch | sketch_3d | stage_music__theatre | 剧院布景；角色进出 |
| roundtable | roundtable_3d | roundtable_studio__roundtable | 圆桌演播厅；三人 120° |
| hot_ones | warm_talk_3d | talk_show_sofa__warmtalk | 暖光沙发访谈；两人对坐 |
| qipashuo | debate_ink_2d | debate_hall__debateink | 国风辩论台；正反方 |
| slow_life | clay_life_3d | kitchen_warm__clay | 粘土厨房；两人灶台/餐桌 |
| history_talk | history_ink_2d | history_study__ink | 水墨书房；host 书案 / 两人左右席 |
| history_mystery | history_ink_2d | crime_scene__ink（新） | 水墨公堂/悬案现场；三人 |
| classics | history_ink_2d | archive_room__ink（新） | 水墨藏书阁；host 案前 / 两人侧席 |
| national_treasure | museum_gold_2d | treasure_museum__gold | 金彩展厅；host 展柜 / 嘉宾对面 |
| mystery | noir_mystery_3d | mystery_room__noir | 黑色电影案发现场；侦探/嫌疑人 |

角色池（contract/characters/）：laozhou（主持人）、ali、dawei。所有模板 cast 必须 ≥2 人，
`block` 必须来自本模板 stage.layout 的 zone；每模板动作库 ≥6 条且尽量互不相同。

## 9. 验收（QC 门，必须全绿）

- 16/16 模板 `python -m pipeline.template_v3 --validate contract/templates_v3 --expect 16` 通过；
- 每模板 30-45s：非卡片镜头 100% H3（h3=true），每镜 meta 含 workflow/prompt_id/seed/ref_image/action；
- 成片：1280×720/25fps/有音轨/blackdetect=0/字幕覆盖对话镜/时长误差 ≤0.6s；
- 静态只允许片头片尾卡片；任何非卡片静态必须 QC 标记（验收不接受）；
- 2D 水墨路径：history_talk 用 history_ink_2d + 2D cutout，成片抽帧确认水墨绘景；
- 16 模板画面/舞台/内容不同：plate+站位+action 集合签名唯一，批报告附 contact sheet。

## 10. 实测决策（2026-10-26 落地版本，覆盖上文相关约定）

1) **正片非卡片镜统一走 `05_ref2va_4step_sla`（audio_ref）**：
   - 有台词镜 → 该镜 TTS wav 驱动口型；无台词镜（空镜/反应/establishing）→ `audio_wav=None`，h3_comfy 内部生成 1.0s 静音参考音频，meta 记 `audio_silent=true`；
   - 不再用 04 fl2v 出正片：实测 04 帧锁定会把 stage 的 2D cutout 原样烘进画面（“纸片感”），而 05 会重绘为电影感画面；04 代码路径保留（单测/兜底/未来用），且已实测 1024×576 首尾帧可用（run_h3_ref 不再强制 768²）。
2) **H3 生成尺寸统一模板 `h3.resolution=1024x576`**（≈768² 像素预算、H3 32 倍数），`normalize_clip` 统一到成片 1280×720/25fps/精确时长；H3 原生 124 帧（~5.17s）为最小长度，demo 单镜统一 5.17s。
3) **storyboard 语速适配**：长台词按标点切分跨镜，单镜估时 ≤6.6s（5.17s×atempo 上限内），demo 首镜=台本首句、末镜=台本末句，避免 atempo 截断。
4) **ink/gongbi 风格**：`contract/style_presets.json` 的 history_ink_2d/debate_ink_2d/museum_gold_2d `prompt_prefix` 已强化为传统水墨/工笔描述；H3 ref2va 仍会把线稿首帧重绘为半写实国风动画（模型特性），成片靠 ink 调色 + 包装统一，人审口径见 `docs/V3_ACCEPTANCE.md` §5。
5) **产物目录**：P2 16 模板在 `outputs/templates_v3/{id}/`；P3 5 分钟版在 `outputs/templates_v3/tonight_show_5min/`（归档目录，不纳入 16 模板终门）；`tonight_show_40s_v1/` 为首版归档。
6) **终门结果**：16/16 模板通过独立验收，累计 84 个非卡片镜 100% h3=true、84 个全局唯一 prompt_id、blackdetect 合计 0、fallbacks=[]；详见 `docs/V3_ACCEPTANCE.md` 与 `docs/V3_REPORT.md`。
