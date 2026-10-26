# V5 数据字段速查（contract/v5/SCHEMA.md）

> JSON Schema（Draft 2020-12）在 `schema/` 下；本文是给人看的字段说明表。
> 校验器以 schema + 本文为准：`python -m pipeline.v5_cli validate [--strict]`。
> 通用规则：id `^[a-z][a-z0-9_]{1,60}$`（文件名=id）；颜色 `#RGB|#RRGGBB|#RRGGBBAA`；
> 未知字段允许（`additionalProperties: true`，便于扩展），但引用必须存在。

## 0. 目录 / 覆盖语义

| 目录 | 对象 | schema |
|---|---|---|
| `styles/` | 视觉风格 | `style.schema.json` |
| `scenes/` | 分层绘景场景 | `scene.schema.json` |
| `characters/` | 角色 | `character.schema.json` |
| `templates/` | V5 模板 | `template.schema.json` |
| `props/` | 道具 | `prop.schema.json` |
| `packs/` | 风格包 | `pack.schema.json` |
| `outputs/.../timeline.json`（render_v5 产物） | 时间线 | `timeline.schema.json` |
| `custom/{kind}/` | 用户覆盖 / 自定义 | 与内建同一个 schema；`custom/` 优先，深合并（dict 递归、数组替换） |

- 覆盖内建对象：文件名/id 与内建相同，可只写要改的字段（partial）；
- 新增对象：id 必须 `custom_` 前缀，且必须满足完整 schema；
- 文件名与 id 不一致 → 错误；custom 新对象 id 不正确 → 错误。

---

## 1. style（视觉风格）

| 字段 | 必需 | 类型 | 说明 |
|---|---|---|---|
| `id` / `label` | ✅ | string | id / 中文显示名 |
| `version` | | int | 版本号（默认 1） |
| `description` / `tags` | | string / string[] | 说明与标签 |
| `extends` | | id | 继承另一个 style（可选，使用方展开） |
| `technique` | ✅ | string[] | 技法原语：`ink_wash` `woodblock` `parchment_engrave` `museum_gold` `silk_gongbi` `hologram` `vhs` `film_grain` `noir` `oil_impasto` `baroque_candle` `blueprint` `collage_cutout` `claymation` `chalk_sketch` `neon_toon` … |
| `palette` | ✅ | object | 6-8 色 hex；**必需 `bg`/`fg`/`accent`**（单色或颜色数组），其余键自由（`paper` `shadow` `highlight` `seal` `ink` `neon` `glass` `floor` …） |
| `texture` | | string[] | `paper_grain` `canvas` `scanline` `halftone` `clay_noise` `chalk_dust` `grid` `bokeh` … |
| `line` | | object | `{style, width, color, jitter, opacity}`（`brush_edge`/`woodcut`/`engrave`/`thick_outline`/`none`/`sketch`） |
| `character_treatment` | | string | 角色风格化：`ink_brush` `cutout_paper` `clay` `pixel` `halftone` `oil` `chalk` `toon` `gongbi` `engrave` `hologram` `vhs` `film` `noir` `blueprint` `collage` `candle` |
| `typography` | | object | `title_font` `subtitle` `lower_third` `text_color` `seal_color` `vertical` `letter_spacing` |
| `transition` | | string | `ink_wipe` `scroll_unroll` `spotlight_iris` `tape_jump` `film_burn` `candle_fade` `blueprint_wipe` `sticker_pop` `stopmotion_jump` `chalk_wipe` `neon_wipe` `glitch_wipe` `blinds_wipe` `gold_cut` … |
| `audio` | | object | `{bgm, sfx: string|string[], ambience, ducking}` |
| `h3` | | object | `{style_prompt(推荐), negative_prompt, motion, guidance}` |
| `finish` / `grade` / `packaging` | | object | 渲染/调色/包装参数（自由扩展，`style_v5` 读取） |

## 2. scene（分层绘景场景）

| 字段 | 必需 | 类型 | 说明 |
|---|---|---|---|
| `id` | ✅ | string | 场景 id |
| `label` / `description` / `tags` | | | 显示名 / 说明 / 标签（`history` `ink` `modern` `neon`…） |
| `aspect` | | int[2] | 画面比例，如 `[16,9]`（默认 16:9） |
| `size` | | int[2] | 内部渲染分辨率（默认 1600x900） |
| `seed` | | int | 默认随机种子 |
| `layers` | ✅ | object[] | 绘制指令，≥1 条；`type` 必须是 `scene_render` 注册的 primitive；`layer` ∈ background/midground/foreground；`color` 可写 hex 或 palette token（`accent:1`/`shadow`/`glass`） |
| `camera_zones` | 推荐 | object | 机位取景窗：`zone → [x,y,w,h]`（归一化）；模板 act.camera_zone 引用 |
| `anchors` | 推荐 | object | 角色脚底锚点：`role 或 character_id → [x,y]`（归一化） |
| `variants` | | string[] | 同名变体标签（如 `campaign`/`treaty`） |
| `props` | | id[] | 引用 `props/` 中的道具 |
| `background_image` | | path | 可选：直接用一张背景图（相对仓库根或 contract/v5） |

内置 primitive 最小集合（见 `docs/V5_INTERFACES.md` §1）：`paper` `canvas` `sky_gradient` `mountains`
`terrain_contours` `rivers` `city_markers` `route_arrows` `compass_rose` `seal_stamp` `bamboo_slips`
`scroll` `lanterns` `columns` `arches` `screens` `desk` `shelves` `glass_case` `spotlights` `neon_signs`
`skyline` `banners` `soldier_silhouettes` `horses` `spears` `smoke` `rain` `snow` `candle_glow`
`particles` `vignette` `grain` `halftone` `grid` `blueprint_lines` `brick_wall` `torn_paper` `stickers`
`clay_shapes` `chalk_lines` `hologram_grid` `film_border`；实现方可追加（校验器就绪时只给 warning）。

## 3. character（角色）

| 字段 | 必需 | 类型 | 说明 |
|---|---|---|---|
| `id` / `label` | ✅ | string | id / 中文名 |
| `persona` | 推荐 | string | 人设（LLM 台本用） |
| `tags` / `era` | | | 标签 / 时代（`history` `modern`…） |
| `role_pool` | ✅ | string[] | 可承担的模板角色：`host` `guest` `figure_a` `narrator` `expert` `suspect`… |
| `voice` | ✅ | object | `provider`（breeze/edge…）、`instruction`（Breeze Voice Design 音色指令，必填）；`seed` `cfg_scale` `rate` `pitch` `voice_id` 可选 |
| `appearance` | ✅ | object | 见下 |
| `appearance.gender` | ✅ | string | 推荐 `male`/`female`/`neutral`/`other`；非人类可写 `robot` 等 |
| `appearance.age` | ✅ | int | 1-150 |
| `appearance.skin`/`hair`/`eye` | ✅ | hex | 肤色 / 发色 / 瞳色 |
| `appearance.hair_style` | ✅ | string | `short_swept` `ancient_guan` `robot_antenna` `long_ponytail` … |
| `appearance.outfit` | ✅ | object | `type`（必需）+ `main`/`accent`（必需 hex）+ `shirt` `pants` `hat` `shoes` |
| `appearance.accessories` / `marks` | | string[] | `feather_fan` `glasses` `headphones` … / `warm_smile` `scar_brow` … |
| `appearance.head_ratio` / `chibi` | | number | 头身比 / Q 版系数 |
| `poses` | 推荐 | string[] | `front` `side` `back` `point` `think` `surprise` `sit` |
| `mouth` | 推荐 | string[] | viseme 帧：`closed` `a` `o` `e` `i` `u` `m` |
| `style_overrides` | | object | `style_id → {line, shade, palette, …}`；引用的 style 必须存在 |
| `image` | | path | 可选：用户提供的透明 PNG 立绘；存在时优先于程序化外观 |
| `image_alpha` | | bool | `image` 是否要求 alpha（默认 true；置 false 可关闭 PNG alpha 检查） |

## 4. template（V5 模板）

| 字段 | 必需 | 类型 | 说明 |
|---|---|---|---|
| `id` / `label` | ✅ | string | id / 中文名 |
| `version` / `description` / `tags` | | | 版本 / 说明 / 标签 |
| `style` | ✅ | id | 引用 `styles/<id>.json` |
| `style_overrides` | | object | 本模板对风格参数的深合并覆盖 |
| `target_duration` | ✅ | number | 目标时长（秒，占位提示；成片时长由 TTS 语音自然时长驱动） |
| `aspect` | | int[2] | 默认 [16,9] |
| `cast` | ✅ | object[] | ≥1；每项 `{role, character}`（必需）+ `name` `props` `wardrobe` `voice`；**role 唯一** |
| `acts` | ✅ | object[] | ≥1；每项见下 |
| `act.id` | ✅ | string | `^[a-z][a-z0-9_]{0,60}$` |
| `act.duration` | ✅ | number | >0 秒 |
| `act.scene` | ✅ | id | 引用 `scenes/<id>.json` |
| `act.camera` | ✅ | string | `push_in` `pan_left` `pan_right` `dolly` `static` `orbit` `crane` … |
| `act.camera_zone` | | string | 引用 `scene.camera_zones` 的取景窗名 |
| `act.line` | ✅ | string | 台词归属 cast role；**无台词写 `""`**（b_roll/空镜） |
| `act.text` | | string | 该 act 的台词文本（TTS 用） |
| `act.beats` / `characters` / `props` / `broll` / `camera_strength` / `transition` | | | 叙事节拍 / 出场角色（cast role 或 character id）/ 道具 / 是否 B-roll / 运镜强度 / 转场 |
| `packaging` | 推荐 | object | `subtitle` `transition` `title` `lower_third` `end_card` |
| `lipsync` | | object | `mode ∈ musetalk|wav2lip|viseme|off` |
| `audio` / `h3` / `meta` / `format` | | object/string | 自由扩展 |

## 5. props（道具）

`schema/prop.schema.json`；必需 `id` / `label`（内建 44 个：历史 20 + 现代 24）。

| 字段 | 必需 | 类型 | 说明 |
|---|---|---|---|
| `id` / `label` | ✅ | string | id / 中文名 |
| `kind` | | string | `scroll` `seal` `banner` `torch` `sandbox` `ding` `vase` `jade` `map_tube` `arrow` `tally` `rods` `mirror` `bell` `drum` `bow` `whip` `edict` `pendant` `microphone` `trophy` `button` `whiteboard` `tablet` `phone` `mug` `film_reel` `cassette` `blinds` `candle` `candlestick` `wine_glass` `mask` `stand` `confetti` `pot` `chalk` `eraser` `costume` `crt_tv` `robot_part` `headphones` … |
| `description` / `tags` | | | 说明 / 标签（`history` `modern` `war` `prop` …） |
| `appearance` | 推荐 | object | 程序化绘制输入：`shape`（`scroll_roll`/`rectangle_banner`/`cup`/`blinds`…）、`main`/`accent`/`detail`（hex）、`material`（paper/bronze/jade/…）、`size`（相对尺寸 `[w,h]`，≤4）、`glow`（0-1） |
| `style` | 推荐 | object | 各风格适配覆盖：键可以是 style id 或风格族（`ink_chronicle` / `neon_toon` / `default` …），值自由；渲染器按当前 style 查找，找不到用 `default` |
| `layer` | | object | 可选：作为一条场景绘制指令 `{type, ...}`（type 需 scene_render 注册） |

`scene.props` / `act.props` / `cast.props` 里引用 id；`packs[].props` 里打包引用。

## 6. packs（风格包）

`schema/pack.schema.json`；把多个对象打包成可整体挑选的集合。

| 字段 | 必需 | 类型 | 说明 |
|---|---|---|---|
| `id` / `label` | ✅ | string | id / 中文名 |
| `description` / `tags` / `cover` | | | 说明 / 标签 / 封面图路径（相对仓库根） |
| `styles` `scenes` `characters` `props` `templates` | | id[] | 各目录对象 id 列表；引用必须存在（校验器按 kind 查表） |

内置 2 个：`pack_history_chronicle`（历史 4 风格 + 11 场景 + 12 角色 + 20 道具 + 4 模板）、
`pack_modern_studio`（现代 13 风格 + 48 场景 + 12 角色 + 24 道具 + 12 模板）。

## 7. timeline（render_v5 产物，非目录契约）

`schema/timeline.schema.json`；校验入口 `validate_timeline()` /
`python -m pipeline.v5_cli validate --timeline <timeline.json>`。

| 字段 | 必需 | 类型 | 说明 |
|---|---|---|---|
| `version` / `template` / `duration` | ✅ | int / id / number | 版本（5）/ 模板 id / 总时长（秒） |
| `label` `mode` `fps` `resolution` | | | 显示名 / `v5_motion2d` / 25 / `[1280,720]` |
| `style` | | object | 本片风格快照（id/label/technique/palette/transition） |
| `tracks` | ✅ | object[] | ≥1；每项 `{id, kind, clips}`，`kind ∈ video/voice/music/subtitle/sfx` |
| `clips[]` | ✅ | object[] | 公共字段 `id/at/in/src/out`；video 加 `kind(scene 镜头类型)/scene/role/transition`；voice 加 `text/character/provider/lead_sec/speech_duration`；music 加 `gain/duck/fade_in/fade_out`；subtitle 加 `text/speaker` |
| `meta` | | object | `speech_driven` / `voice_provider` / `lead_sec` / `tail_sec` / `scenes` / `characters` |

语义校验（`validate_timeline`）：
- 必须有 `video` 轨；有 voice 无 subtitle（或反之）给 warning；
- 轨道 id、同轨片段 id 必须唯一；
- 时间：**video/voice/music 的 `out` = 片段时长**（校验 `at+out ≤ duration`）；
  **subtitle 的 `out` = 绝对结束时间**（校验 `out ≤ duration` 且 `out > at`）——render_v5 现状约定。

## 8. 校验输出与级别

`validate_catalog()` / `python -m pipeline.v5_cli validate` 返回：

```json
{"ok": true/false, "errors": ["..."], "warnings": ["..."],
 "stats": {"root": "...", "counts": {...}, "custom_counts": {...}, "schemas": [...]},
 "strict": false}
```

| 级别 | 例子 | 是否阻塞（默认 / --strict） |
|---|---|---|
| ERR | schema 必填缺失、类型错、引用不存在、id/文件名不一致、custom 新 id 无前缀、资源缺失/无 alpha | 阻塞 / 阻塞 |
| WARN | 缺 `h3.style_prompt`、缺 `poses`/`mouth`/`persona`、缺 `camera_zones`/`anchors`、目录为空、未知 layer.type（scene_render 就绪时，聚合 1 条） | 不阻塞 / **阻塞** |

> 注：V5 时长由 TTS 语音自然时长驱动，模板 `acts[].duration` / `target_duration` 只是占位提示，
> **不做时长对齐校验**（render_v5.tts_and_pace 会重算每镜时长）。

## 9. 常见错误对照

| 错误 | 原因 | 修复 |
|---|---|---|
| `id: 'xxx' is a required property` | 文件里没写 id | 写 `"id": "<与文件名一致>"` |
| `xxx: cast[0].character=... 不存在于 characters/` | 引用了未创建角色 | 先创建角色 JSON，或改 cast |
| `custom 新建对象 id 必须以 'custom_' 开头` | custom 里新建对象没前缀 | 改 id/文件名（覆盖内建则同名） |
| `文件名必须与 id 一致` | 文件名和 id 不同 | 重命名文件或改 id |
| `image 需要带 alpha 的透明图` | 角色 PNG 立绘无 alpha | 换透明 PNG，或 `image_alpha: false` 关闭检查 |
| `scenes/: N 个 layer type 未在 scene_render 注册`（WARN） | 场景用了未注册 primitive | 在 scene_render 注册，或场景改用已注册名 |
