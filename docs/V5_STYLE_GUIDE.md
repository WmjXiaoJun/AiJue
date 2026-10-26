# V5 风格视觉规范（V5_STYLE_GUIDE）

> 版本：V5.0（2026-10-26）
> 配套：`docs/PLAN_V5_STYLE_AUDIO.md` §3、`docs/V5_INTERFACES.md` §1§2、`contract/v5/SCHEMA.md`
> 实现：`pipeline/scene_render.py`（分层绘景 primitive）+ `pipeline/style_v5.py`（调色/纹理/包装/字幕/卡片/转场）
> 本文结构：**A. 通用规范框架**（所有 16 风格共用）→ **B. style-core-dev 负责的 4 个历史风格**（完整规范）→
> **C. 其余 12 风格**（占位，由 render-dev / h3-dev 后续补齐）。

---

# A. 通用规范框架

## A.1 16 风格总表

| # | style_id | 中文名 | technique（契约键） | 主色印象 | 负责人 | 状态 |
|---|---|---|---|---|---|---|
| 1 | `neon_toon` | 霓虹卡通 | `neon_toon` | 青蓝+品红+琥珀 | render-dev | 由队友补规范 |
| 2 | `ink_chronicle` | 水墨史书 | `ink_wash` | 宣纸米+墨黑+朱砂 | style-core-dev | ✅ 本文 §B.1 |
| 3 | `woodblock_classic` | 版刻典籍 | `woodblock` | 纸黄+墨黑+朱红 | style-core-dev | ✅ 本文 §B.2 |
| 4 | `parchment_case` | 羊皮纸档案 | `parchment_engrave` | 羊皮棕+铁锈红+青灰 | style-core-dev | ✅ 本文 §B.3 |
| 5 | `museum_gold` | 博物馆金黑 | `museum_gold` | 黑金+玉青 | style-core-dev | ✅ 本文 §B.4 |
| 6 | `silk_arena` | 绢本设色 | `silk_gongbi` | 绢米+石青+朱砂 | h3-dev | 由队友补规范 |
| 7 | `hologram_stage` | 全息舞台 | `hologram` | 紫+电光蓝+全息白 | render-dev | 由队友补规范 |
| 8 | `vhs_late_show` | 80s VHS | `vhs` | 暖黄+洋红+青 | render-dev | 由队友补规范 |
| 9 | `film_interview` | 暖光胶片 | `film_grain` | 暖橙+奶油+深棕 | render-dev | 由队友补规范 |
| 10 | `noir_rain` | 黑色电影 | `noir` | 黑白+冷蓝+琥珀 | render-dev | 由队友补规范 |
| 11 | `oil_salon` | 文艺复兴油画 | `oil_impasto` | 深褐+暗金+象牙 | h3-dev | 由队友补规范 |
| 12 | `baroque_candle` | 巴洛克烛光 | `baroque_candle` | 暗红+金+墨绿 | h3-dev | 由队友补规范 |
| 13 | `blueprint_lab` | 科技蓝图 | `blueprint` | 蓝图蓝+白+橙 | render-dev | 由队友补规范 |
| 14 | `candy_collage` | 糖果拼贴 | `collage_cutout` | 糖果粉+黄+青 | h3-dev | 由队友补规范 |
| 15 | `clay_diorama` | 黏土定格 | `claymation` | 陶土+鼠尾草绿+奶油 | h3-dev | 由队友补规范 |
| 16 | `chalk_theatre` | 粉笔素描 | `chalk_sketch` | 黑板绿+粉笔白+红幕 | h3-dev | 由队友补规范 |

> **硬要求**：任何两个风格必须在「调色 / 纹理 / 线条 / 字体排版 / 构图」四者上同时可辨；
> 仅换调色不算不同风格。验收时看 `tmp/v5_preview/contact_sheet_all.png` 与
> `style_report.json` 的调色指纹（16 部两两直方图/边缘密度差异，见 PLAN §6.2）。

## A.2 style JSON 字段（`contract/v5/styles/<id>.json`）

必填（校验器强制）：`id` `label` `technique[]` `palette{bg[],fg[],accent[]}`
强烈建议（缺失会掉区分度/包装）：

| 字段 | 作用 | 读取方 |
|---|---|---|
| `palette` | 6-8 色，`bg/fg/accent/paper` + 角色（`shadow/highlight/mid/ink/water/wood/stone/glow/seal/metal/gold/jade/sky`） | scene_render + style_v5 |
| `texture[]` | 纸纹/扫描线/半调/黏土噪点等，自动映射到 `finish` 参数 | style_v5.finish_params |
| `line{style,width,color,jitter,opacity}` | 线条语言：`brush_edge/woodcut/engrave/thick_outline/sketch/none` | scene_render primitive |
| `character_treatment` | 角色后处理：`ink_brush/cutout_paper/clay/pixel/halftone/oil/chalk/toon/gongbi/engrave/gold_silhouette` | style_v5.apply_finish(kind=character) |
| `typography{title_font,subtitle,lower_third,text_color,seal_color,vertical,letter_spacing}` | 字幕/卡片排版预设名（自由命名） | style_v5.subtitle_ass / 卡片 |
| `transition` | 语义转场名，映射 ffmpeg xfade（见 A.5） | style_v5.transition |
| `audio{bgm,sfx[],ambience,ducking}` | BGM/SFX 提示（V5.4 音频用） | render_v5 |
| `h3{style_prompt,negative_prompt,motion,guidance}` | 可选 H3 B-roll prompt | style_v5.style_prompt |
| `finish{...}` | 后处理参数覆盖（见 A.3） | style_v5.apply_finish |
| `grade{eq,hue,colorbalance,curves,extra}` | ffmpeg 调色链（缺省按 technique 兜底） | style_v5.grade_filter |
| `packaging{tag,tag_color,bars,frame,seal,seal_color,brand,watermark}` | 上下条/画框/印章/频道 tag | style_v5.packaging_filters |

**颜色规则**：scene layer 的 `color` 优先写 **palette 角色名**（`"accent"` / `"accent:1"` 取第二个 accent /
`"shadow"`），也允许显式 `"#b23a2e"`；palette 里没有的角色按以下顺序回退：
① 标准回退链（paper→bg, mid→fg, ground→mid→bg, water→mid→bg, glow/seal/metal→accent, stone→mid, wood→accent）；
② **语义关键字匹配**（未知角色名按字面映射到最接近的标准角色，如 `brick/board/stone→stone`、
`grid/glass/neon/blue→mid`、`gold/butter/clay→accent`、`wine/red→accent`、`paper_white/cream→highlight`、
`tape/metal→fg`，见 `scene_render.ROLE_KEYWORDS`）；③ 最后 fg/mid/bg/paper 兜底。
因此**队友自定义 palette 少写某个角色不会导致整场渲染失败**（debug 日志会记录回退），palette 完全为空才报错。
**场景里不写死 hex**，否则风格换皮时颜色不跟着变。

## A.3 后处理参数（`finish`，16 technique 共享）

`style_v5.apply_finish(img, style, kind="plate"|"character")` 的流程：
**调色 → technique 特征特效 → texture 映射 → 通用叠加 → （character）描边**。

| 参数 | 范围/量级 | 效果 |
|---|---|---|
| `brightness` / `contrast` / `saturation` | 0.9-1.2 / 0.95-1.4 / 0.1-1.4 | 基础调色 |
| `temperature` / `tint` | -0.25~0.25 | 暖冷（R/B）/ 绿品（G）偏移 |
| `paper` | 0.08-0.28 | 纸纹/斑驳（**>0.3 会发灰**） |
| `edge_ink` | 0.10-0.35 | 边缘墨线（越高越“画”） |
| `hatch` | 0.20-0.35 | 暗部排线（铜版/木刻），工笔/油画勿用 |
| `vignette` | 0.12-0.46 | 压角（叙事/悬疑更高） |
| `grain` | 0.03-0.14 | 胶片/纸颗粒 |
| `bloom` | 0.1-0.5 | 高光泛光（金/霓虹/烛光高） |
| `gold_foil` | 0.2-0.35 | 金粉（museum_gold） |
| `silk_sheen` | 0.2-0.3 | 绢面柔光（silk_gongbi） |
| `scanlines` / `chroma_shift` / `wobble` / `glitch` | 0.1-0.8 | VHS/全息/故障带 |
| `halftone` / `clay_shade` / `chalk_dust` / `canvas` / `brush_strokes` / `crackle` / `emboss` | 0.12-0.35 | 拼贴/黏土/粉笔/油画破局 |
| `sharpen` | 0.2-0.4 | 金属/文物锐度 |

**量级参考**：`paper 0.2`≈明显纸纹、`edge_ink 0.3`≈强墨边、`hatch 0.3`≈明显排线。
`kind="character"` 时强度自动 ×0.55-0.72 并追加 character_treatment 描边/贴纸白边。

## A.4 scene JSON 与绘景 primitive

scene 必填：`id` `layers[]`；建议：`label/tags/aspect/camera_zones/anchors/variants/depth`
（schema 见 `contract/v5/SCHEMA.md` §2）。

layer 字段：`type`（必填，必须是已注册 primitive）、`layer ∈ background|midground|foreground`
（不写按 primitive 默认）、`opacity`、`clip[x,y,w,h]`、`seed`、`styles[]`（仅这些风格渲染）、
`variants{variant:{参数覆盖}}`；其余键全由 primitive 自己解释，**坐标/尺寸一律 0-1 归一化**。

**契约 primitive（43 个，全部已实现）**：
`paper canvas sky_gradient mountains terrain_contours rivers city_markers route_arrows
compass_rose seal_stamp bamboo_slips scroll lanterns columns arches screens desk shelves
glass_case spotlights neon_signs skyline banners soldier_silhouettes horses spears smoke
rain snow candle_glow particles vignette grain halftone grid blueprint_lines brick_wall
torn_paper stickers clay_shapes chalk_lines hologram_grid film_border`

**历史包追加 primitive（style-core-dev, `scene_render.py`）**：
`marble terrain_contours waves fog tents sand_table torches ladder throne incense artifact
stalls crowd boats wall_gate cross_section_grid`

**队友扩展 primitive（由 `_load_extensions()` 自动加载，不占本文件）**：
`pipeline/v5_primitives_modern.py`（render-dev）、`pipeline/v5_primitives_modern_b.py`（h3-dev）、
`pipeline/v5_primitives_history.py`（历史包可选扩展）。

**返回值契约**（`render_scene_layers`，勿改）：

```python
{"background": RGBA(1600×900 不透明), "midground": RGBA, "foreground": RGBA|None,
 "depth": {"background":0.02,"midground":0.12,"foreground":0.30},
 "anchors": {role:(x,y)}, "camera_zones": {zone:[x,y,w,h]},
 "meta": {"scene","style","variants","variant","layers","elapsed_ms",...}}
```

`depth` 越大视差移动越多（motion2d 用）；`anchors` 是角色脚底锚点（归一化）；`camera_zones` 是取景窗。

**性能预算**：1600×900 单场景 `render_scene_layers` ≤ **1500ms**（实测 4 风格×13 场景 mean≈385ms，
max≈818ms）；`apply_finish` 另 +150-250ms（1080p 级）。

## A.5 转场（`style_v5.transition(style) -> (语义名, ffmpeg xfade 名)`）

语义值 → xfade 映射（未知值按子串匹配后 fallback `fade`，不报错）：

| 语义 | xfade | 典型风格 |
|---|---|---|
| `ink_wipe` / `ink_bleed` | fade | ink_chronicle |
| `scroll_unroll` / `scroll_roll` | wipeleft / wiperight | 卷轴类 |
| `woodblock_wipe` / `woodblock_cut` | wipeleft / fadeblack | woodblock_classic |
| `redline` | smoothleft | parchment_case |
| `spotlight` / `spotlight_iris` / `spotlight_close` | circleopen / circleopen / circleclose | museum_gold |
| `tape_jump` / `tape_glitch` | hblur / pixelize | vhs_late_show |
| `film_burn` / `film_cut` | fadewhite / dissolve | film_interview |
| `neon_wipe` / `light_wipe` / `glitch_wipe` | wiperight / smoothright / pixelize | neon/hologram |
| `blinds_wipe` / `candle_fade` / `blueprint_wipe` | vertopen / fadeblack / smoothleft | noir/baroque/blueprint |
| `sticker_pop` / `stopmotion_jump` / `chalk_wipe` / `silk_wipe` / `oil_wipe` | zoomin / fade / wipeup / fade / dissolve | 对应风格 |

## A.6 字幕版式（`subtitle_ass` 的 family）

`subtitle_ass(style, events, out_ass, width, height)` 按 **technique 主键**选版式（不依赖字体文件路径）：

| family | 判定 | 版式 |
|---|---|---|
| `ink` | technique=ink_wash 或 subtitle 预设名含 `ink` | **右侧竖排**（逐字 `\N`，长文自动分列）+ 红色方印 |
| `woodblock` | technique=woodblock | 底部居中 + **纸色实底框**（BorderStyle=3）+ 深墨字 |
| `parchment` | technique=parchment_engrave | 底部 + **档案条**（`CASE FILE // 文本`），米色字/暗底 |
| `museum` | technique=museum_gold | 下方居中 + **金色字/黑底牌** + 上下金线 |
| `silk` | technique=silk_gongbi | 底部居中 + 绢色框 + 字距加大 |
| `modern` | 其余 | 底部居中 + 描边阴影（颜色取 style fg/accent） |

events 兼容 `[{"start","end","text"}]` 与 render_v3 的 5 元组 `(start,end,layer,style,text)`。

## A.7 片头/片尾卡（`title_card` / `end_card`）

`style_v5.card_background` 用「technique → 内置卡片场景」渲染背景（`CARD_SCENES`，每风格一组
primitive 组合 + `apply_finish`）→ 再按 **family** 排版文字：

| family | 卡片排版 |
|---|---|
| `ink` | 纸+水墨远山；标题**竖排右侧**、朱印、左下副标题+朱线 |
| `woodblock` | 纸+木刻山；**双线书框**+角饰、居中标题、右下朱印 |
| `parchment` | 羊皮纸+等高线+剖面；**虚线档案框**+`CASE FILE` 印章+左对齐标题+编号线 |
| `museum` | 大理石+聚光；**黑金铭牌**（双金框+金色标题+展签小字+顶部聚光） |
| `modern` | technique 背景 + **暗色标题安全区（scrim）** + 描边标题 + 风格色细线 |

两条通用规则（pilot 修复）：
1. **文本安全区**：复杂/霓虹/高光背景必须加 scrim（暗色渐变带），标题再加描边（`_text_outlined`），
   保证任何背景上可读；
2. **副标题去重**：`subtitle == text` 时自动不画第二行（避免 pilot 里同一标题出现两遍）。

## A.8 包装（`packaging_filters`）

读取 `packaging{}`：`bars ∈ none|thin|letterbox|gold_frame|red_tape|scanlines|blueprint`、
`frame ∈ none|thin|double|gold`、`tag`（右上）、`seal`（左上朱印）、`brand`（右下小字，`watermark` 控制）。
**字体路径注意**：传入的 `font_file` 可能是**相对 workdir** 的 `fonts/Deng.ttf`（fonts.py 约定），
存在性判断用 `(Path(workdir)/font_file).is_file()`；filter 里保持相对路径。

## A.9 音频提示（V5.4 使用）

`audio.bgm` / `audio.sfx[]` / `audio.ambience` / `audio.ducking` 仅作约定，BGM/SFX 资产由音频组补；
历史包建议：古琴/战鼓/木鱼/翻页/弦乐悬疑/编钟/管弦，环境音：风声/雨声/厅堂混响。

## A.10 新增风格 / 场景 / primitive 的标准流程

1. 新增 style：复制同类 style JSON 改 `id/label/technique/palette/finish` → 跑
   `python -m pipeline.style_v5 --style <id> --print-filter` 自检 → `title_card/end_card` 出 2 张预览；
2. 新增 scene：只写 `type`+`layer`+少量参数，先 `python -m pipeline.scene_render --list-primitives`
   确认 primitive 存在 → `--scene <id> --style <id> --out ...` 预览 → `--check-scenes` 全绿；
3. 新增 primitive：在 `pipeline/v5_primitives_*.py` 里
   `def my_prim(img, spec, ctx)` + `register("my_prim", my_prim)`，
   **显式写 layer**（scene 里指定），颜色用 `ctx.color("accent")`，坐标用 `ctx.PX/PY/S`；
   不要改 `scene_render.py`（自动加载点 `_load_extensions()` 已预留）；
4. **验收**：`python tmp/v5_preview_all.py` 全绿 + 每风格 contact sheet 人工抽检 + 调色指纹差异。

## A.11 QA 检查清单（每风格）

- [ ] 调色：与相邻风格直方图均值差异 > 12/255（RGB 任一通道）；
- [ ] 纹理：`apply_finish` 有 technique 专属特征（不是只换 eq）；`--print-filter` 有非空 grade；
- [ ] 线条：scene 渲染里线条语言与 style.line 一致（墨线/木刻/排线/无）；
- [ ] 字体/字幕：`subtitle_ass` 生成成功，family 正确、竖排/金/纸框可辨；
- [ ] 卡片：`title_card`+`end_card` 出图、标题不截断、不与背景撞色（scrim 生效）；
- [ ] 包装：`packaging_filters` 字符串含 drawtext（tag/seal/brand 按需）；
- [ ] 转场：`transition()` 返回的 xfade 名在 ffmpeg 支持列表内；
- [ ] 场景：6-10 层、≥2 variants、≥3 camera_zones、≥2 anchors；历史包必须能看出地图/战争/文物元素；
- [ ] 性能：单场景 render ≤ 1500ms（1600×900）。

---

# B. style-core-dev 的 4 个历史风格（完整规范）

> 历史 4 模板映射：`history_talk → ink_chronicle`、`classics → woodblock_classic`、
> `history_mystery → parchment_case`、`national_treasure → museum_gold`。
> 共用历史场景库（13 个）：`ancient_map war_council battlefield palace_hall archive_vault
> lantern_market river_fleet great_wall temple_court scholar_study excavation_site museum_hall display_case`

## B.1 `ink_chronicle` 水墨史书

**一句话**：宣纸长卷上的水墨淡彩史书——留白、飞白笔触、朱砂印章，像“会动的水墨绘本”。

| 项 | 规范 |
|---|---|
| technique | `["ink_wash","rice_paper"]` |
| 调色 | bg `#e8dcc4/#d9cbb0`、paper `#efe6d3`、fg `#1c1a16/#3a352c`、accent `#b23a2e/#c9a227`、mid `#6f7d7a`、shadow `#8c8171`、highlight `#fbf6ea`、water `#6b7f8a` |
| 纹理 | `paper_grain` + `ink_bleed`（纸纹 0.18 / 墨边 0.32） |
| 线条 | `brush_edge` 宽 3，抖动 0.6（毛笔飞白；山脊/水岸用 tapered 描边） |
| 角色处理 | `ink_brush`（边缘墨线 + 降饱和融入纸面） |
| 构图 | 大留白（≥35% 空区）、上/左留白放竖排标题、三分法；地图场景用「纸面满铺等高线」 |
| 字幕 | 右侧竖排逐字 + 朱印；不遮人物脸（x≈0.918 列，长文向左分列） |
| 包装 | `bars=thin`（上下朱砂细线）、`seal=史`/`tag=水墨史书`、品牌右下淡字 |
| 转场 | `ink_wipe` → fade（配 xfade 0.6-0.8s） |
| 音频 | bgm 古琴；sfx 战鼓/翻页/运笔；ambience 风声 |
| H3 | “traditional Chinese ink-wash painting (shuimo) on rice paper, visible dry-brush strokes and ink bleed, restrained pale color washes, flat 2D illustration, elegant negative space” |
| 场景首选 | `ancient_map`（古地图）、`war_council`（军议帐）、`battlefield`（战场）、`palace_hall`（宫殿）、`great_wall`（关隘）、`river_fleet`（渡口）、`scholar_study`、`temple_court` |
| 不要 | 3D 高光/照片感/饱和霓虹色/纯黑死白；不要把字幕放左下（与角色锚点冲突） |
| 实测指纹 | mean RGB ≈ `[180,169,153]`，sat 0.16，edge 20.5（最“淡”） |

## B.2 `woodblock_classic` 版刻典籍

**一句话**：木刻版画+线装书——粗黑刻线、平面套色、纸黄、朱印，像翻开的雕版古籍。

| 项 | 规范 |
|---|---|
| technique | `["woodblock"]` |
| 调色 | bg `#e3d3ac/#d3bd8e`、paper `#ead9b8`、fg `#241d16/#4a3b28`、accent `#a83226/#c8a14a`、mid `#8a6a3a`、shadow `#7a6242`、highlight `#f5ead0` |
| 纹理 | `paper_grain` + `wood_grain`（纸纹 0.22 + 木纹纤维） |
| 线条 | `woodcut` 宽 4（闭合粗线、山石排线、边缘黑线） |
| 角色处理 | `cutout_paper`（剪纸白边 + 贴纸阴影） |
| 构图 | 平面化、强框景（书框/窗棂/竹简）；主体居中偏下，上方留题字区 |
| 字幕 | 底部居中 + 纸色实底框 + 深墨字（像印在书页上） |
| 包装 | `frame=double`（双线书框+角饰）、`bars=none`、`seal=典`、`tag=版刻典籍` |
| 转场 | `woodblock_wipe` → wipeleft（刻版擦除） |
| 音频 | bgm 古琴拨弦/木鱼；sfx 木槌/翻页/盖印；ambience 书阁 |
| H3 | “Chinese woodblock print (banhua), carved black lines, flat ink impression, aged yellow paper texture, red seal stamps, minimal shading, 2D print” |
| 场景首选 | `scholar_study`、`archive_vault`、`palace_hall`、`ancient_map`、`temple_court`、`war_council` |
| 不要 | 渐变阴影/柔光/3D；不要细如发丝的线条（木刻要“拙”） |
| 实测指纹 | mean RGB ≈ `[172,149,116]`，sat 0.33，edge 18.9（纸最黄） |

## B.3 `parchment_case` 羊皮纸档案

**一句话**：考古悬案卷宗——羊皮纸、铜版细排线、红线证据牵引、剖面网格，像摊开的档案袋。

| 项 | 规范 |
|---|---|
| technique | `["parchment_engrave"]` |
| 调色 | bg `#cbb083/#b89a6a`、paper `#d9c49a`、fg `#3b2f24/#5a4a38`、accent `#a63d2f/#4f6b6b`、mid `#7f6a4d`、shadow `#8d7355`、highlight `#efe1c2`、water `#5f7470` |
| 纹理 | `paper_grain` + `crosshatch`（纸纹 0.24 + 暗部排线 0.28 + 裂纹 0.10） |
| 线条 | `engrave` 宽 1.5（细密铜版线，越暗越密） |
| 角色处理 | `engrave`（细线勾边、做旧） |
| 构图 | 档案化：右上角编号/十字定位、底部证据线（朱红横线）、网格/等高线做底 |
| 字幕 | 底部档案条 `档案 // 文本`，米色字 + 暗底 + 红分隔 |
| 包装 | `bars=red_tape`（左上/右下红胶带）、`frame=thin`、`seal=卷`、`tag=CASE FILE` |
| 转场 | `redline` → smoothleft（红线牵引） |
| 音频 | bgm 弦乐悬疑；sfx 纸页/图钉/盖章；ambience 雨声 |
| H3 | “copperplate engraving on aged parchment, fine cross-hatching, sepia archival document, red evidence threads, old cartography and excavation sketches” |
| 场景首选 | `excavation_site`（发掘现场）、`archive_vault`、`ancient_map`、`war_council`、`scholar_study`、`great_wall` |
| 不要 | 高饱和/霓虹/动漫脸；排线不能糊成一片（spacing ≥ 3px） |
| 实测指纹 | mean RGB ≈ `[162,142,114]`，sat 0.30，edge 14.8（最“旧”） |

## B.4 `museum_gold` 博物馆金黑

**一句话**：国宝展厅——黑大理石、玻璃展柜、顶部聚光、金色铭牌，文物像被“请”到聚光灯下。

| 项 | 规范 |
|---|---|
| technique | `["museum_gold"]` |
| 调色 | bg `#0c0c0e/#1a1512`、paper `#121114`、fg `#e8d9b0/#c9a24a`、accent `#d4af37/#7a1f1f`、highlight `#f4e2a8`、stone `#2b2a2e`、metal `#c9a24a`、jade `#4f7f74`、shadow `#050506` |
| 纹理 | `grain` + `bokeh`（暗部颗粒 0.05 + 金色泛光 0.42 + 金粉 0.30） |
| 线条 | `gold_bevel` 宽 2（金色描边/展柜棱线） |
| 角色处理 | `gold_silhouette`（剪影 + 金边/轮廓光） |
| 构图 | 中心对称、低机位仰视、聚光锥引导视线；展柜/文物占中心 40%；上方留黑 |
| 字幕 | 下方居中金字 + 黑底名牌 + 上下金线（“铭牌式”字幕） |
| 包装 | `bars=gold_frame` + `frame=gold`（双金线画框）、`seal=宝`、`tag=国宝 · 博物馆` |
| 转场 | `spotlight_iris` → circleopen（聚光开合） |
| 音频 | bgm 编钟+管弦；sfx 钟磬/聚光灯开关/大理石脚步；ambience 厅堂混响 |
| H3 | “museum exhibition hall, black marble and gold, dramatic spotlights on a glass display case, bronze ritual vessel, volumetric light beams, polished stone reflections” |
| 场景首选 | `museum_hall`、`display_case`、`palace_hall`、`temple_court`、`excavation_site` |
| 不要 | 日光/平光/纸感纹理；不要高饱和彩色（金+黑+玉青三色克制） |
| 实测指纹 | mean RGB ≈ `[47,41,29]`，sat 0.31，edge 12.7（最暗） |

## B.5 历史场景库（13 个）速查

| scene_id | 画面要素 | 关键 camera_zones | anchors |
|---|---|---|---|
| `ancient_map` | 宣纸+等高线+河流+城池朱印+行军红线+罗盘+史印；variants campaign/treaty | wide / map_detail / route / compass | host .16,.92 · figure_a .74,.92 |
| `war_council` | 军帐布幔+军旗+矛架+沙盘+案图+火把+烛+烟；variants night_raid/parley | wide / table_top / host_ots / map_close | host .20,.94 · figure_a .78,.94 |
| `battlefield` | 暮色天+远山+烽火+军旗+三层兵阵剪影+骑兵+矛林+烟尘+雾；variants dawn/dusk | wide / cavalry / left_flank / smoke_sky | host .10,.95 · figure_a .62,.96 |
| `palace_hall` | 连拱+盘龙柱(楹联)+山水屏风+宝座+长幡+宫灯；variants audience/night | wide / throne_close / pillars / hall_deep | host .22,.95 · figure_a .78,.95 |
| `archive_vault` | 双书架+木梯+案几+卷轴+竹简+浮尘+烛光；variants dusty/lit | wide / desk_top / shelf_close / scroll_close | host .30,.96 · figure_a .74,.96 |
| `lantern_market` | 夜空+屋顶天际线+摊位+灯笼串+人群+蒸汽+光尘；variants festival/late_night | wide / lanterns_close / stalls_down / crowd_sea | host .14,.96 · figure_a .80,.96 |
| `river_fleet` | 黄昏天+远山+江波+战船帆影+旗+船灯+江雾；variants sailing/anchored | wide / fleet / water_detail / sails | host .16,.95 · figure_a .78,.95 |
| `great_wall` | 冷色天+群山+城墙垛口+城门楼+烽火台+军旗+雪；variants beacon/siege | wide / gate_close / beacon / wall_walk | host .18,.96 · figure_a .76,.96 |
| `temple_court` | 连拱+立柱+长幡+香炉+供案+宫灯+香烟；variants offering/festival | wide / altar / incense_close / banners_high | host .20,.96 · figure_a .80,.96 |
| `scholar_study` | 窗棂+书架+案几笔墨+卷轴+竹简+烛+浮尘；variants night_reading/morning | wide / desk_top / scroll_close / shelves_right | host .28,.97 · figure_a .72,.97 |
| `excavation_site` | 地形等高线+探方透视网格+剖面土层网格+记录卷+物证印+尘；variants trench/finds | wide / section_close / trench_down / tag_close | host .14,.96 · figure_a .86,.96 |
| `museum_hall` | 黑大理石+连拱+立柱+三束聚光+双玻璃展柜(鼎/瓶)+光尘；variants gold/jade | wide / case_close / hall_columns / spotlights_top | host .30,.98 · figure_a .74,.98 |
| `display_case` | 大理石+地面网格+双聚光+大展柜(鼎/玉璧)+金色铭牌；variants artifact/label_focus | wide / case_close / label / floor | host .16,.98 · figure_a .84,.98 |

历史模板推荐场景路线（act 顺序）：

| 模板 | style | 推荐路线 |
|---|---|---|
| `history_talk` 历史夜话 | ink_chronicle | `ancient_map` → `war_council` → `battlefield` → `palace_hall` → `great_wall` → `archive_vault` |
| `classics` 典籍 | woodblock_classic | `scholar_study` → `archive_vault` → `temple_court` → `palace_hall` |
| `history_mystery` 历史悬案 | parchment_case | `excavation_site` → `archive_vault` → `ancient_map` → `great_wall` |
| `national_treasure` 国宝 | museum_gold | `museum_hall` → `display_case` → `palace_hall` → `temple_court` → `excavation_site` |

## B.6 复现与自测命令

```bash
# 生成 4 风格 + 13 场景 JSON（幂等，可重跑）
python tmp/v5_preview_gen_assets.py

# 单场景预览（含 apply_finish）
python -m pipeline.scene_render --scene ancient_map --style ink_chronicle --out tmp/v5_preview/ancient_map__ink_chronicle.png
python -m pipeline.scene_render --scene museum_hall --style museum_gold --width 1600 --height 900

# 全量：4 风格 × 13 场景 + contact sheet + 卡片 + 变体 + 性能/指纹报告
python tmp/v5_preview_all.py

# 卡片/调色/转场自检
python -m pipeline.style_v5 --style ink_chronicle --print-filter
python -m pipeline.style_v5 --style museum_gold --card both --title 国宝 --subtitle "展品 001" --out tmp/v5_preview/cards
python -m pipeline.scene_render --check-scenes

# 产物
#   tmp/v5_preview/contact_sheet_all.png     总览（4×6）
#   tmp/v5_preview/sheet_<style>.png         每风格 13 场景
#   tmp/v5_preview/contact_sheet_cards.png   4 风格片头/片尾
#   tmp/v5_preview/sheet_variants.png        场景变体
#   tmp/v5_preview/preview_report.json       性能/指纹/字幕/转场
```

## B.7 已知限制 / 后续可提升

1. primitive 是程序化几何+纹理，近看偏“图形化”，适合 25fps 视频的中景/远景与快速剪辑；
   特写镜头建议配合 `motion2d` 推近 + `apply_finish` 纹理，或后期接 SDXL 底图（PLAN §7.1）；
2. `seal_stamp`/`scroll`/`bamboo_slips` 的“伪文字”依赖中文字体（`fonts.ensure_fonts`），
   无字体环境会退化为抽象笔画（不报错）；
3. 环境色温/光照未做物理模拟，同一场景跨 4 风格的明暗关系以 palette 为准；
4. 远山/水面等柔边元素用 1/2-1/6 分辨率绘制再放大，超大屏（>4K）需重调模糊半径；
5. 卡片背景 `CARD_SCENES` 与场景库共用 primitive，后续新增 primitive 时应同步补卡片组合。

---

# C. 其余 12 风格（占位，待队友补）

> 规范模板（每个风格按 B.1 的表格填）：一句话定位 / technique / 调色 hex 表 / 纹理 / 线条 /
> 角色处理 / 构图 / 字幕 family / 包装 / 转场 / 音频 / H3 prompt / 场景首选 / 不要 / 实测指纹。
> 状态："JSON 已交付，视觉规范待补"。

| style_id | technique | 需要补的章节 | 负责人 |
|---|---|---|---|
| `neon_toon` | `neon_toon` | C.1 霓虹卡通规范（霓虹管/玻璃幕墙/体积光/光带擦除） | render-dev |
| `silk_arena` | `silk_gongbi` | C.2 绢本设色规范（工笔绢面/双屏对垒/飞白分屏） | h3-dev |
| `hologram_stage` | `hologram` | C.3 全息演唱会规范（粒子网格/激光/镜面/故障转场） | render-dev |
| `vhs_late_show` | `vhs` | C.4 80s VHS 规范（扫描线/色差/砖墙霓虹/磁带字） | render-dev |
| `film_interview` | `film_grain` | C.5 暖光胶片访谈规范（16mm 颗粒/胶片边框/烧边） | render-dev |
| `noir_rain` | `noir` | C.6 黑色电影规范（百叶窗硬影/雨夜/高反差/冷爵士） | render-dev |
| `oil_salon` | `oil_impasto` | C.7 油画圆桌规范（厚涂/明暗对照/金框/裂纹） | h3-dev |
| `baroque_candle` | `baroque_candle` | C.8 巴洛克烛光规范（哥特拱顶/烛火/天鹅绒/管风琴） | h3-dev |
| `blueprint_lab` | `blueprint` | C.9 科技蓝图规范（蓝图网格/白板/数据可视化/参数弹窗） | render-dev |
| `candy_collage` | `collage_cutout` | C.10 糖果拼贴规范（纸片剪贴/贴纸/半调网点/彩带） | h3-dev |
| `clay_diorama` | `claymation` | C.11 黏土定格规范（手作质感/微缩景/移轴/定格跳动） | h3-dev |
| `chalk_theatre` | `chalk_sketch` | C.12 粉笔素描规范（黑板/手绘线/舞台/粉笔擦除） | h3-dev |

**补规范时可直接复用 A.2-A.9 的字段说明**，只写该风格特有的：色板 hex、finish 覆盖值、
字幕/卡片/包装视觉、转场与音频、构图规则、场景清单、3-5 条"不要"。

---

## 附：本文档维护约定

- 新增/修改风格后，同步更新 A.1 总表与对应 §B/§C 小节；
- `finish`/`grade`/`packaging`/`typography` 字段语义不变，只允许追加键；
- 任何“引擎行为”变化（如新增转场映射、字幕 family）必须同时更新 A.3-A.7；
- 本文档与 `contract/v5/styles/*.json` 冲突时，**以 JSON 为准**，文档记录“推荐值/意图”。
