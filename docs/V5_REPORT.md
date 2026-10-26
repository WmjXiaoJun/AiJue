# V5 交付报告：16 种风格世界 + 口型同步 + 全量可自定义

> 规划：`docs/PLAN_V5_STYLE_AUDIO.md`；接口：`docs/V5_INTERFACES.md`；Lip-sync 选型：`docs/LIPSYNC_FEASIBILITY.md`；
> 验收：`docs/V5_ACCEPTANCE.md`（QA）；自定义教程：`docs/V5_CUSTOMIZATION.md`；动效：`docs/V5_MOTION.md`；角色：`docs/V5_CHARACTERS.md`；风格规范：`docs/V5_STYLE_GUIDE.md`。
> 上一版：V4（Breeze TTS 2 语音 + OpenFilm 式时间线，`docs/V4_REPORT.md`）。

## 0. 用户诉求与 V5 回答

| 诉求 | V5 做法 |
|---|---|
| 嘴形和语音对不上 | **对白镜不再依赖 H3 的弱口型**：`motion2d` 用 2D 角色 + TTS 音素时间轴驱动 viseme 口型（与语音同源、天然同步、零漂移）；同时交付 **MuseTalk 1.5 lip-sync 库**（实测 48ms/帧、6.85GB 显存、MIT）作为 H3 B-roll 的可选后处理（`pipeline/lipsync.py`），近景/低分辨率按规则跳过 |
| 16 模板只有一种风格 | 新建 **风格引擎**：16 个风格各自拥有独立技法/调色/纹理/线条/角色处理/字幕包装/转场/配乐；16 个模板逐一重画**场景库 61 个**（含历史的地图、战场、军议、宫殿、档案、博物馆、关隘、船队等），不再是同一张动漫 plate |
| 历史要有历史画面（地图/战争等） | 历史 4 模板场景路线覆盖：`ancient_map`（等高线/行军路线/城池/罗盘/史印）、`battlefield`（远/近山、军旗、36 兵 3 纵深、7 骑、40 矛、烟尘）、`war_council`（军帐/沙盘/火把）、`palace_hall`（拱廊/屏风/宝座/宫灯/长幡）、`archive_vault`、`excavation_site`、`great_wall` 等 13 个历史场景 |
| 角色/场景可以多加 | 角色 **24 个**（历史 12：诸葛亮/曹操/孙武/秦始皇/苏轼/李白/武则天/花木兰/关羽/张骞/郑和/司马迁；现代 12），全部程序化立绘（口型 7 态/眨眼/4 姿态/按风格 treatment）；场景 **61 个**（48 现代 + 13 历史）；道具 **44 个**；风格包 **2 个** |
| 用户全部可自定义 | `contract/v5/` 六类 JSON 契约（styles/scenes/characters/props/templates/packs）+ `custom/` 覆盖目录（同名深合并、数组替换）+ JSON Schema 校验 + CLI（`python -m pipeline.v5_cli validate/list/export/preview`）+ FastAPI（`/api/v5/catalog|{kind}/{id}|validate|preview`）+ 教程 `docs/V5_CUSTOMIZATION.md` |
| 先规划再实施 | 先落 `docs/PLAN_V5_STYLE_AUDIO.md` + `docs/V5_INTERFACES.md`（模块契约），再按 V5.0–V5.4 分阶段实施 |

## 1. 架构（V5 渲染链路）

```
contract/v5（styles/scenes/characters/props/templates/packs + custom 覆盖）
        │  v5_catalog.load_catalog() + validate（schema/引用/资源）
        ▼
v5_story.build_plan(template)  →  acts → 逐镜 plan（场景/角色/机位/台词）
        ▼
render_v5.tts_and_pace()       →  Breeze TTS 2 按角色 voice instruction 合成 → 语音驱动镜头时长
        ▼
render_v5.render_shot_clips()  →  scene_render.render_scene_layers（分层：bg/mid/fg + depth + anchors + camera_zones）
                                   characters_v5.render_character（24 角色立绘 + 风格 treatment + 口型帧）
                                   motion2d.render_shot（视差/镜头/口型平滑/眨眼/呼吸/出入场/景深/暗角 → h264）
                                   style_v5.title_card / end_card（片头片尾风格卡）
        ▼
render_v5.assemble()           →  xfade 转场 + 风格化字幕 + 风格 BGM（v5_audio 程序化合成分轨）+ ducking + 两遍 loudnorm
        ▼
qc_v5.evaluate()               →  规格/黑帧/场景/角色/字幕/风格指纹；timeline.json/html + style_report.json
```

- 对白镜口型：`characters_v5.viseme_timeline(text, duration)` → motion2d 逐帧换嘴型（2-3 帧过渡 + 张口间插闭口帧），**与 TTS 时间轴同源**。
- 可选 lip-sync（H3 片段）：`pipeline/lipsync.py`（MuseTalk 1.5，独立 venv，`sync_clip()`；`available()/measure_sync()` 接口见 V5_INTERFACES §5）。
- 产物：`outputs/templates_v5/{id}/{final.mp4,timeline.json,timeline.html,qc_report.json,style_report.json,contact_sheet.png,shots/,frames/,work/}`。

## 2. 16 个风格（各不相同）

| # | 模板 | 风格 | 视觉世界/技法 | 代表场景 |
|---|---|---|---|---|
| 1 | tonight_show | neon_toon | 霓虹 3D 卡通演播厅（霓虹灯管/玻璃幕墙/体积光） | neon_skyline, late_night_studio, backstage_neon, neon_alley |
| 2 | history_talk | ink_chronicle | 水墨长卷（宣纸/飞白/淡彩/朱印/竖排字幕） | ancient_map, war_council, battlefield, palace_hall, scholar_study |
| 3 | classics | woodblock_classic | 版刻典籍（木刻线/纸黄/双线书框/朱印） | scholar_study, archive_vault, palace_hall, temple_court |
| 4 | history_mystery | parchment_case | 羊皮纸考古档案（铜版排线/红线证据板/剖面网格） | excavation_site, ancient_map, archive_vault, great_wall |
| 5 | national_treasure | museum_gold | 博物馆金黑（大理石/展柜/聚光/金边） | museum_hall, display_case, archive_vault, palace_hall |
| 6 | qipashuo | silk_arena | 绢本设色辩论（工笔绢面/双屏对垒/墨圈舞台） | silk_debate_hall, scroll_split, court_audience, ink_arena |
| 7 | masked_singer | hologram_stage | 全息演唱会（粒子网格/激光/镜面） | hologram_stage, mirror_room, audience_star_sea, light_tunnel |
| 8 | standup | vhs_late_show | 80s VHS（扫描线/色差/砖墙霓虹） | comedy_club_vhs, brick_wall, dressing_room_vhs, city_vhs |
| 9 | hot_ones | film_interview | 暖光胶片访谈（16mm 颗粒/胶片边框） | interview_sofa, cafe_window, kitchen_film, film_strip |
| 10 | mystery | noir_rain | 黑色电影（百叶窗硬影/雨夜/高反差） | noir_office, rainy_street, interrogation_room, police_archive |
| 11 | roundtable | oil_salon | 文艺复兴油画（厚涂/明暗对照/金框） | oil_roundtable, oil_library, candle_gallery |
| 12 | traitors | baroque_candle | 巴洛克烛光古堡（哥特拱顶/烛火/天鹅绒） | castle_hall_baroque, candle_corridor, stone_roundtable, crypt |
| 13 | shark_tank | blueprint_lab | 科技蓝图（蓝图网格/白板/数据可视化） | pitch_boardroom, blueprint_wall, whiteboard_lab, prototype_lab |
| 14 | taskmaster | candy_collage | 糖果拼贴综艺（纸片剪贴/贴纸/半调网点） | game_stage_collage, prop_table, leaderboard_collage, confetti_stage |
| 15 | slow_life | clay_diorama | 黏土定格（手作质感/微缩景/移轴） | clay_kitchen, clay_garden, clay_street, clay_diorama |
| 16 | snl_sketch | chalk_theatre | 粉笔素描剧场（黑板/手绘线/红幕） | chalk_stage, sketch_backdrop, costume_room_chalk, audience_sketch |

每风格还包含：独立色板（6-9 hex）、纹理/线条/角色 treatment、字幕家族（水墨竖排/版刻纸框/档案条/金字牌/深墨描边…）、片头片尾卡、转场、BGM preset（程序化合成 15 种）+ SFX pack。

## 3. Lip-sync（实测选型，详见 docs/LIPSYNC_FEASIBILITY.md）

| 方案 | 帧耗时 | 峰值显存 | 结论 |
|---|---|---|---|
| **MuseTalk 1.5**（默认 provider，MIT） | **48.4ms/帧 ≈20.7fps**（含加载 17.1s/72帧） | **6845MB** | ✅ 中景可用；动漫脸可驱动，下半脸略软；近景（脸>150px）不建议 |
| Wav2Lip（开源版） | 更快但 96px 上采样糊 | ~4-6GB | ❌ 许可证禁商用，排除 |
| LatentSync 1.5 | ~0.5-1s/帧 | ~18-20GB | ❌ 12GB 不可行 |
| **2D viseme rig（V5 对白镜默认）** | CPU 实时 | 0 | ✅ 与 TTS 同源、零漂移、风格可控（V5 全部对白镜用此路径） |

- 人脸检测：**OpenCV YuNet**（22ms/帧 CPU，动漫脸可用；face-alignment/mediapipe 不可用）；贴回用羽化椭圆掩码（无 face-parsing）。
- 素材扫描：全仓 141 个 H3 片段脸宽中位数 **87.5px**、最大 241px，**无 ≥300px 镜头** → 近景线不成立，规则：脸宽 ≤120px 处理、≥150px 跳过。
- GPU 争用：不独占时慢 2.3–3.2× → 必须串行独占。
- 事故复盘：集成期曾因 venv/系统解释器转发条件写错导致进程风暴（10,675 进程），已修复 + 加深度闸/超时/单测回归（见 FEASIBILITY §事故复盘）。

## 4. 用户自定义（contract/v5）

```
contract/v5/
  styles/ scenes/ characters/ props/ templates/ packs/   # 内建 16 风格 / 61 场景 / 25 角色 / 44 道具 / 16 模板 / 2 包
  schema/*.schema.json                                    # JSON Schema（style/scene/character/prop/template/pack/timeline）
  custom/                                                 # 用户覆盖（同名深合并、数组替换；优先级最高）
```
- 校验：`python -m pipeline.v5_cli validate [--strict]`（schema + 引用完整性 + 资源存在性 + primitive 对照）；`--timeline` 校验成片时间线。
- 浏览/导出/预览：`list-styles|list-scenes|list-characters|list-templates|list-props|list-packs`、`export --kind --id`、`preview --kind --id`（不占 GPU）。
- API：`GET /api/v5/catalog`、`GET/PUT /api/v5/{kind}/{id}`（保存前校验）、`POST /api/v5/validate`、`GET /api/v5/preview/{kind}/{id}`。
- 自定义示例：`contract/v5/custom/` 下 `custom_holo_paper`（风格）/`custom_lin`（角色）/`custom_holo_gallery`（场景）/`custom_holo_talk`（模板）自洽可渲染。
- 新增角色/场景/模板教程见 `docs/V5_CUSTOMIZATION.md`。

## 5. 产物与验收（待填最终数据）

- 16 部成片：`outputs/templates_v5/{id}/final.mp4`（30-45s、1280×720/25fps）
- 总览：`outputs/templates_v5/contact_sheet_all.png`、`batch_report_v5.json`
- 风格区分度：QA `tests/test_v5_acceptance.py` + `tmp/qa/v5_style_matrix.json`
- 契约/自定义：`pytest tests/test_v5_catalog.py` 26 passed；API 19/19 PASS
- Lip-sync 单测：`pytest pipeline/tests/test_lipsync_unit.py` 11 passed
- 动效自检：`verify_motion.py` 45/45 PASS


| # | 模板 | 风格 | 时长(s) | 镜头 | 场景数 | QC |
|---|---|---|---|---|---|---|
| 1 | classics | woodblock_classic | 39.11 | 9 | 4 | ✅ |
| 2 | history_mystery | parchment_case | 39.43 | 9 | 4 | ✅ |
| 3 | history_talk | ink_chronicle | 43.75 | 9 | 5 | ✅ |
| 4 | hot_ones | film_interview | 41.54 | 8 | 4 | ✅ |
| 5 | masked_singer | hologram_stage | 39.30 | 8 | 4 | ✅ |
| 6 | mystery | noir_rain | 29.94 | 8 | 4 | ✅ |
| 7 | national_treasure | museum_gold | 40.74 | 8 | 4 | ✅ |
| 8 | qipashuo | silk_arena | 43.62 | 8 | 4 | ✅ |
| 9 | roundtable | oil_salon | 44.34 | 8 | 3 | ✅ |
| 10 | shark_tank | blueprint_lab | 31.24 | 7 | 4 | ✅ |
| 11 | slow_life | clay_diorama | 41.38 | 8 | 4 | ✅ |
| 12 | snl_sketch | chalk_theatre | 38.18 | 8 | 4 | ✅ |
| 13 | standup | vhs_late_show | 43.70 | 8 | 4 | ✅ |
| 14 | taskmaster | candy_collage | 34.18 | 8 | 4 | ✅ |
| 15 | tonight_show | neon_toon | 41.19 | 9 | 4 | ✅ |
| 16 | traitors | baroque_candle | 34.02 | 8 | 4 | ✅ |

汇总：**16/16 QC PASS**；时长 29.94–44.34s；1280×720/25fps。



| # | 模板 | 风格 | 时长(s) | 镜头 | 场景数 | QC |
|---|---|---|---|---|---|---|
| 1 | classics | woodblock_classic | 39.11 | 9 | 4 | ✅ |
| 2 | history_mystery | parchment_case | 39.43 | 9 | 4 | ✅ |
| 3 | history_talk | ink_chronicle | 43.75 | 9 | 5 | ✅ |
| 4 | hot_ones | film_interview | 41.54 | 8 | 4 | ✅ |
| 5 | masked_singer | hologram_stage | 39.30 | 8 | 4 | ✅ |
| 6 | mystery | noir_rain | 29.94 | 8 | 4 | ✅ |
| 7 | national_treasure | museum_gold | 40.74 | 8 | 4 | ✅ |
| 8 | qipashuo | silk_arena | 43.62 | 8 | 4 | ✅ |
| 9 | roundtable | oil_salon | 44.34 | 8 | 3 | ✅ |
| 10 | shark_tank | blueprint_lab | 31.24 | 7 | 4 | ✅ |
| 11 | slow_life | clay_diorama | 41.38 | 8 | 4 | ✅ |
| 12 | snl_sketch | chalk_theatre | 38.18 | 8 | 4 | ✅ |
| 13 | standup | vhs_late_show | 43.70 | 8 | 4 | ✅ |
| 14 | taskmaster | candy_collage | 34.18 | 8 | 4 | ✅ |
| 15 | tonight_show | neon_toon | 41.19 | 9 | 4 | ✅ |
| 16 | traitors | baroque_candle | 34.02 | 8 | 4 | ✅ |

汇总：**16/16 QC PASS**；时长 29.94–44.34s；1280×720/25fps。



### 5.2 角色固定声音（V5.1）

- 新增 **声音库** `contract/v5/voices.json`：24 个固定 preset（instruction+seed+cfg_scale），
  laozhou/ali/dawei 与 V4 完全一致（seed 20261026/27/28），其余 21 个角色一人一 preset；
- 角色 JSON 只引用 `voice_id`（可在角色下覆盖 `seed/instruction/cfg_scale/ref_audio/ref_text`）；
- **声音锁**：首次为角色生成固定参考音频 `outputs/v5_voices/<角色>/<voice_key>.wav`，
  之后所有台词用 **Voice Clone**（ref_audio+ref_text）合成 → 同角色跨镜/跨集音色一致；
- 修改：`python -m pipeline.v5_cli voice set/preview/lock` 或直接改角色 JSON，教程 `docs/V5_VOICES.md`；
- QC 新增 `voice_fixed`：同角色所有对白镜 voice_key 必须一致；timeline/style_report 记录 voice_id/key/ref。


| # | 模板 | 风格 | 时长(s) | 镜头 | 场景数 | QC |
|---|---|---|---|---|---|---|
| 1 | classics | woodblock_classic | 44.75 | 8 | 4 | ✅ |
| 2 | history_mystery | parchment_case | 43.23 | 8 | 4 | ✅ |
| 3 | history_talk | ink_chronicle | 38.12 | 5 | 4 | ✅ |
| 4 | hot_ones | film_interview | 43.02 | 7 | 4 | ✅ |
| 5 | masked_singer | hologram_stage | 44.70 | 7 | 4 | ✅ |
| 6 | mystery | noir_rain | 37.22 | 8 | 4 | ✅ |
| 7 | national_treasure | museum_gold | 43.06 | 6 | 3 | ✅ |
| 8 | qipashuo | silk_arena | 42.94 | 7 | 4 | ✅ |
| 9 | roundtable | oil_salon | 44.18 | 8 | 3 | ✅ |
| 10 | shark_tank | blueprint_lab | 33.80 | 7 | 4 | ✅ |
| 11 | slow_life | clay_diorama | 38.74 | 8 | 4 | ✅ |
| 12 | snl_sketch | chalk_theatre | 40.58 | 8 | 4 | ✅ |
| 13 | standup | vhs_late_show | 42.66 | 6 | 3 | ✅ |
| 14 | taskmaster | candy_collage | 35.06 | 8 | 4 | ✅ |
| 15 | tonight_show | neon_toon | 43.11 | 7 | 3 | ✅ |
| 16 | traitors | baroque_candle | 40.42 | 8 | 4 | ✅ |

汇总：**16/16 QC PASS**；时长 33.80–44.75s；1280×720/25fps。



| # | 模板 | 风格 | 时长(s) | 镜头 | 场景数 | QC |
|---|---|---|---|---|---|---|
| 1 | classics | woodblock_classic | 44.75 | 8 | 4 | ✅ |
| 2 | history_mystery | parchment_case | 43.23 | 8 | 4 | ✅ |
| 3 | history_talk | ink_chronicle | 38.12 | 5 | 4 | ✅ |
| 4 | hot_ones | film_interview | 43.02 | 7 | 4 | ✅ |
| 5 | masked_singer | hologram_stage | 44.70 | 7 | 4 | ✅ |
| 6 | mystery | noir_rain | 37.22 | 8 | 4 | ✅ |
| 7 | national_treasure | museum_gold | 43.06 | 6 | 3 | ✅ |
| 8 | qipashuo | silk_arena | 42.94 | 7 | 4 | ✅ |
| 9 | roundtable | oil_salon | 44.18 | 8 | 3 | ✅ |
| 10 | shark_tank | blueprint_lab | 33.80 | 7 | 4 | ✅ |
| 11 | slow_life | clay_diorama | 38.74 | 8 | 4 | ✅ |
| 12 | snl_sketch | chalk_theatre | 40.58 | 8 | 4 | ✅ |
| 13 | standup | vhs_late_show | 42.66 | 6 | 3 | ✅ |
| 14 | taskmaster | candy_collage | 35.06 | 8 | 4 | ✅ |
| 15 | tonight_show | neon_toon | 43.11 | 7 | 3 | ✅ |
| 16 | traitors | baroque_candle | 40.42 | 8 | 4 | ✅ |

汇总：**16/16 QC PASS**；时长 33.80–44.75s；1280×720/25fps。


## 6. 复现

```powershell
# 1) 契约校验
python -m pipeline.v5_cli validate --strict

# 2) 单个模板渲染（TTS 需要 GPU；--refresh 复用缓存 TTS、纯 CPU 重渲视觉）
python -m pipeline.cli render_v5 --template history_talk --duration 40
python -m pipeline.cli render_v5 --template history_talk --duration 40 --force

# 3) 批量 16 模板（--refresh 复用 TTS 缓存；--force 重跑全部）
python scripts/render_templates_v5.py --duration 40 --refresh
python scripts/render_templates_v5.py --duration 40 --force --only tonight_show,history_talk

# 4) 可选 H3 lip-sync（MuseTalk，独立 venv；需独占 GPU）
python -m pipeline.lipsync --clip outputs/templates_v3/tonight_show/work/h3/v3_s001.mp4 `
  --audio outputs/templates_v3/tonight_show/work/audio/v3_s001.wav --out tmp/lipsync_test.mp4

# 5) 预览/导出自定义
python -m pipeline.v5_cli preview --kind scene --id ancient_map --style ink_chronicle --out tmp/map.png
python -m pipeline.v5_cli export --kind styles --id neon_toon
```

## 7. 已知限制 / 后续

1. 16 部为 **2D motion design**（程序化立绘+场景），不是 H3 电影感镜头；H3 仍保留在 V4 链路。若需要 H3 对白镜，可用 MuseTalk 后处理（近景跳过）。
2. 角色为程序化几何立绘（脸/手简化），不是精修美术；可通过 `custom/` 用 PNG 立绘替换（V5.1+ 支持图片素材路径）。
3. 字幕/包装的字体为系统字体（`pipeline/fonts.py`），未内置商用字库；发布前请替换为已授权字体。
4. `measure_sync` 未接 SyncNet，口型"同步度分数"暂缺；V5 对白镜走 viseme（无需分数）。
5. 16 模板的 5 分钟长版/多集未实现；当前为 30-45s demo。
6. 进程风暴事故已修复（lipsync 转发），并补 3 条回归单测。
7. 用户自定义 UI（/v5 页面）未做，当前为 JSON + CLI + API；UI 属 V5.3 可选。
