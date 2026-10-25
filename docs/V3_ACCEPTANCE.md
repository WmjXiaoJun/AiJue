# V3 独立验收报告（qa-verifier）

> 验收口径：`docs/V3_INTERFACES.md` §9（QC 门必须全绿）+ §4（H3 证据链）+ §6（qc_v3 checks）。
> 生成时间：2026-10-26 07:34（本文件由 `tmp/qa/gen_acceptance_doc.py` 依据真实产物生成，可重复运行）
> 验收人：qa-verifier（独立于 render-dev / h3-dev / stage-dev）；**不启动渲染，只读 outputs/ 与 contract/**。

## 0. 结论摘要

| 项 | 状态 |
|---|---|
| P0：16/16 模板校验（strict plate） | ✅ 16/16，0 ERR / 0 warn |
| P0：校验器单测 | ✅ `tests/test_template_v3.py` 89 passed / 0 skipped |
| 模板差异性（签名/plate/Jaccard） | ✅ 签名 16/16 唯一；plate 16/16 唯一；Jaccard 最大 0.125 |
| P1/P2：已渲染成片 | ✅ 已渲染 16/16 |
| P1/P2：已通过逐模板验收 | ✅ 16/16 |
| 批级：prompt_id 全局唯一 / batch_report / 总 contact sheet | ✅ |
| P1/P2：逐模板 QC 全绿 | ✅ |
| P1/P2：blackdetect=0 | ✅ |
| 2D 水墨人审（history_talk 抽帧） | 见 §5（待 Lead 人审定论） |
| P3：tonight_show_5min（**不纳入 P2 16/16 终门**） | ✅ 抽检通过（见 §10） |

## 1. 验收命令（可复跑）

```powershell
# P0：模板 16/16（必备条件，失败即终止验收）
python -m pipeline.template_v3 --validate contract/templates_v3 --expect 16

# P0：校验器单测（tmp fixture，不依赖渲染）
python -m pytest tests/test_template_v3.py -q

# P1/P2：独立验收（读 outputs/templates_v3/*；未渲染自动 skip）
python -m pytest tests/test_v3_acceptance.py -q
python -m pytest tests/test_v3_acceptance.py -q -k "h3 or video or qc_report or metadata"
python -m pytest tests/test_v3_acceptance.py -q -k ink_review

# 回归
python -m pytest tests -q
python -m pytest pipeline/tests -q

# 模板差异独立复核（JSON 证据）
python tmp/qa/review_templates.py          # -> tmp/qa/template_review.json
python tmp/qa/selftest_acceptance.py       # 验收断言自检（fake 产物，正向+反向）

# 本报告重生成
python tmp/qa/gen_acceptance_doc.py
```

## 2. P0：16/16 模板校验 + 校验器单测

- `python -m pipeline.template_v3 --validate contract/templates_v3 --expect 16` → **16/16 通过，exit 0，0 ERR / 0 warn**（strict plate 模式）。
- `python -m pytest tests/test_template_v3.py -q` → **89 passed / 0 skipped**。覆盖必填字段、stage.type、plate 缺失（strict/--allow-missing-plate）、layout/props/camera_zones、cast.block 在 layout、action 未引用、timeline 误差 >15%、h3.workflow、id 与文件名一致、目录 16/16、差异性签名重复、plate 复用警告、CLI 退出码；真实 16 模板目录冒烟自动生效。

## 3. 16 模板独立复核（不复用 validate_dir 的差异性实现）

复核脚本：`tmp/qa/review_templates.py`；机器可读证据：`tmp/qa/template_review.json`。

- id 集合与 §8 表一致（16/16），id ↔ 文件名一致；
- style + stage.plate 与 §8 契约映射逐项一致；
- 差异性签名 = (stage.plate, cast 站位组合, action 使用集合)：**16/16 两两唯一**；
- plate：**16/16 唯一**（0 复用，无需说明复用理由）；16 张 PNG 均存在于 `outputs/scene_plates/`；
- 动作集合 Jaccard：**最大 0.125**（`['history_talk', 'traitors']`），>80% 对数 **0**；动作库并集 136 个不同动作；
- 2D 模板（history_talk / history_mystery / classics / qipashuo）全部 `2d_plate` + `cutout_2d` preset + `*__ink` plate；`standup`/`national_treasure` 亦为 2D preset —— 0 问题。

### 3.1 16 模板形态一览

| # | template | style | stage.plate | stage.type | zones | props | cast | actions | shots | 段时长 |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | `tonight_show` | cinematic_3d | `late_night_desk__cinematic` | 3d_set | 5 | 4 | 3 | 9 | 9 | 300.0s |
| 2 | `shark_tank` | cinematic_3d | `pitch_room__cinematic` | 3d_set | 5 | 6 | 3 | 9 | 10 | 300.0s |
| 3 | `masked_singer` | neon_stage_3d | `stage_music__neon` | 3d_set | 5 | 5 | 3 | 9 | 10 | 300.0s |
| 4 | `standup` | retro_standup_2d | `stage_music__vhs` | 2d_plate | 3 | 5 | 3 | 9 | 9 | 300.0s |
| 5 | `taskmaster` | game_show_3d | `taskmaster_stage__game` | 3d_set | 4 | 6 | 3 | 9 | 10 | 300.0s |
| 6 | `traitors` | roundtable_3d | `castle_hall__roundtable` | 3d_set | 5 | 5 | 3 | 9 | 9 | 300.0s |
| 7 | `snl_sketch` | sketch_3d | `stage_music__theatre` | 3d_set | 4 | 5 | 3 | 9 | 9 | 300.0s |
| 8 | `roundtable` | roundtable_3d | `roundtable_studio__roundtable` | 3d_set | 5 | 5 | 3 | 8 | 9 | 300.0s |
| 9 | `hot_ones` | warm_talk_3d | `talk_show_sofa__warmtalk` | 3d_set | 4 | 5 | 2 | 9 | 9 | 300.0s |
| 10 | `qipashuo` | debate_ink_2d | `debate_hall__debateink` | 2d_plate | 4 | 5 | 3 | 9 | 9 | 300.0s |
| 11 | `slow_life` | clay_life_3d | `kitchen_warm__clay` | 3d_set | 4 | 5 | 2 | 9 | 9 | 300.0s |
| 12 | `history_talk` | history_ink_2d | `history_study__ink` | 2d_plate | 5 | 5 | 3 | 9 | 9 | 300.0s |
| 13 | `history_mystery` | history_ink_2d | `crime_scene__ink` | 2d_plate | 5 | 5 | 3 | 9 | 9 | 300.0s |
| 14 | `classics` | history_ink_2d | `archive_room__ink` | 2d_plate | 4 | 5 | 3 | 9 | 9 | 300.0s |
| 15 | `national_treasure` | museum_gold_2d | `treasure_museum__gold` | 2d_plate | 4 | 5 | 3 | 9 | 9 | 300.0s |
| 16 | `mystery` | noir_mystery_3d | `mystery_room__noir` | 3d_set | 4 | 5 | 3 | 9 | 9 | 300.0s |

## 4. P1/P2：逐模板成片验收（16/16）

验收项（每模板硬门）：非卡片镜 100% `h3=true`；每镜 `workflow∈{04,05}`、`prompt_id` 非空且**全局唯一**、`seed` 存在、`ref_image` 文件存在、`action` 非空；成片 1280×720 / 25fps / 有音轨 / blackdetect=0 / 时长误差 ≤0.6s；`qc_report.ok=true`、`deliverable=true`、`fallbacks=[]`。

| # | template | 状态 | QC ok | deliverable | 非卡片镜 | H3 镜 | meta 数 | prompt_id 唯一 | workflow | 黑帧段 | 成片时长 | 分辨率 | previs/fallback |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | `tonight_show` | ✅ 已渲染 | ✅ | ✅ | 7 | 7 | 9 | 7 | ✅ | 0 | 40.68s | 1280x720 | 正式 / 0 |
| 2 | `shark_tank` | ✅ 已渲染 | ✅ | ✅ | 5 | 5 | 7 | 5 | ✅ | 0 | 30.35s | 1280x720 | 正式 / 0 |
| 3 | `masked_singer` | ✅ 已渲染 | ✅ | ✅ | 5 | 5 | 7 | 5 | ✅ | 0 | 30.35s | 1280x720 | 正式 / 0 |
| 4 | `standup` | ✅ 已渲染 | ✅ | ✅ | 5 | 5 | 7 | 5 | ✅ | 0 | 30.32s | 1280x720 | 正式 / 0 |
| 5 | `taskmaster` | ✅ 已渲染 | ✅ | ✅ | 5 | 5 | 7 | 5 | ✅ | 0 | 30.32s | 1280x720 | 正式 / 0 |
| 6 | `traitors` | ✅ 已渲染 | ✅ | ✅ | 5 | 5 | 7 | 5 | ✅ | 0 | 30.35s | 1280x720 | 正式 / 0 |
| 7 | `snl_sketch` | ✅ 已渲染 | ✅ | ✅ | 5 | 5 | 7 | 5 | ✅ | 0 | 30.35s | 1280x720 | 正式 / 0 |
| 8 | `roundtable` | ✅ 已渲染 | ✅ | ✅ | 5 | 5 | 7 | 5 | ✅ | 0 | 30.35s | 1280x720 | 正式 / 0 |
| 9 | `hot_ones` | ✅ 已渲染 | ✅ | ✅ | 5 | 5 | 7 | 5 | ✅ | 0 | 30.35s | 1280x720 | 正式 / 0 |
| 10 | `qipashuo` | ✅ 已渲染 | ✅ | ✅ | 5 | 5 | 7 | 5 | ✅ | 0 | 30.35s | 1280x720 | 正式 / 0 |
| 11 | `slow_life` | ✅ 已渲染 | ✅ | ✅ | 5 | 5 | 7 | 5 | ✅ | 0 | 30.35s | 1280x720 | 正式 / 0 |
| 12 | `history_talk` | ✅ 已渲染 | ✅ | ✅ | 7 | 7 | 9 | 7 | ✅ | 0 | 40.68s | 1280x720 | 正式 / 0 |
| 13 | `history_mystery` | ✅ 已渲染 | ✅ | ✅ | 5 | 5 | 7 | 5 | ✅ | 0 | 30.35s | 1280x720 | 正式 / 0 |
| 14 | `classics` | ✅ 已渲染 | ✅ | ✅ | 5 | 5 | 7 | 5 | ✅ | 0 | 30.35s | 1280x720 | 正式 / 0 |
| 15 | `national_treasure` | ✅ 已渲染 | ✅ | ✅ | 5 | 5 | 7 | 5 | ✅ | 0 | 30.35s | 1280x720 | 正式 / 0 |
| 16 | `mystery` | ✅ 已渲染 | ✅ | ✅ | 5 | 5 | 7 | 5 | ✅ | 0 | 30.35s | 1280x720 | 正式 / 0 |

### 4.1 批级硬门（16/16 后统一复核）

- 非卡片镜 H3 覆盖：**84/84（100%）**；卡片 32 张（全部 `static_card=true`，允许静态）；
- H3 镜 prompt_id：**84 个、全局唯一**（跨 16 模板无重复）；workflow 全部 `05_ref2va_4step_sla`；seed / ref_image / action 齐全；
- 成片规格：16/16 1280×720 / 25fps / 有音轨；blackdetect 合计 **0 段**；时长误差均 ≤0.6s；字幕覆盖 1.0；
- QC：16/16 `ok=true` + `deliverable=true` + `previs=false` + `fallbacks=[]`；
- `batch_report.json`：`ok=true`、`coverage_ok=true`、`covered_total=16`、`missing_ids=[]`，总 contact sheet `outputs/templates_v3/contact_sheet.png` 存在；
- `pytest tests/test_v3_acceptance.py -q` → **90 passed / 0 skipped**；16 模板差异性签名两两唯一、plate 16/16 唯一。

## 5. 2D 水墨（history_talk）人审证据

抽帧证据（人审用，非自动判定；来自成片 final.mp4，已含 ink_amber 调色/包装/字幕）：

- 4 模板总览：`tmp\qa\ink_review_grid.jpg`
- `history_talk`（3 帧）：`tmp\qa\ink_review\history_talk\frame_0_15pct.jpg`、`tmp\qa\ink_review\history_talk\frame_1_50pct.jpg`、`tmp\qa\ink_review\history_talk\frame_2_85pct.jpg`
- `qipashuo`（3 帧）：`tmp\qa\ink_review\qipashuo\frame_0_15pct.jpg`、`tmp\qa\ink_review\qipashuo\frame_1_50pct.jpg`、`tmp\qa\ink_review\qipashuo\frame_2_85pct.jpg`
- `history_mystery`（3 帧）：`tmp\qa\ink_review\history_mystery\frame_0_15pct.jpg`、`tmp\qa\ink_review\history_mystery\frame_1_50pct.jpg`、`tmp\qa\ink_review\history_mystery\frame_2_85pct.jpg`
- `classics`（3 帧）：`tmp\qa\ink_review\classics\frame_0_15pct.jpg`、`tmp\qa\ink_review\classics\frame_1_50pct.jpg`、`tmp\qa\ink_review\classics\frame_2_85pct.jpg`

契约级确认（4 个模板）：`style∈{history_ink_2d, debate_ink_2d}`、`stage.type=2d_plate`、`*__ink` plate、preset `character_style=cutout_2d`、成片经 ink 系调色（ink_amber / ink_red_gold）。

QA 观感（最终以 Lead 人审定论）：成片调色后呈“国风/墨调”（纸纹/线稿化背景/红金或墨青 palette + INK HISTORY 包装 + 中文字幕）；classics 重生成 plate 在成片里可见书架/梯子/卷轴，slow_life 重生成 plate 的灶台/餐桌/庭院均可见，早前“悬浮/空房”问题已消除。**需 Lead 人审的项**：① H3 对水墨线稿首帧重绘为半写实动画风，水墨笔触感弱；② 角色身份跨镜漂移（ali 发色/耳机色、dawei 胡须/外套细节变化；host 较稳定）；③ 场景/房间基本连续。

## 6. 回归基线

| 时点 | tests/ | pipeline/tests/ | 说明 |
|---|---|---|---|
| P0 前（QA 接手） | 148 passed | 73 passed | 基线 |
| P0 模板完成 | 240 passed / 9 skipped | 101 passed / 2 failed | 2 fail = render_v3 自测假卡片近黑导致的黑尾（render-dev 已修，非模板问题） |
| 渲染中 | 247→248 passed / 4 skipped | 139→142 passed | v3 单测持续新增，全绿 |
| **最终（16/16）** | **327 passed / 0 skipped** | **143 passed** | 全绿 |

## 7. 遗留问题与风险

1. **校验器与接口文档的差异（低风险，Lead 已确认）**：`stage.scene_desc` 文档写“必填”，校验器只 warn 不阻断；16 模板已全部填写，验收以 warning 清单为准。
2. **动作库跨模板相似度**：校验器对 action 集合 Jaccard>80% 只 warn；独立复核最大 0.125，当前无风险，后续新增模板需盯。
3. **plate 策略**：§8 指定 16 张 plate 唯一；校验器对 plate 复用只 warn。当前 0 复用。另：`kitchen_warm__clay`（slow_life）与 `archive_room__ink`（classics）已按 QA 目检反馈重生成，`debate_hall__debateink`（qipashuo）亦已重生成。
4. **演示时长 vs 模板 target_duration**：模板 target_duration=300（5 分钟目标），P2 demo 成片 30–45s；验收按 `work/episode.json` 计划时长核对 ±0.6s，不是按 300s。
5. **GPU 串行与 H3 速度**：实测 ~2.5–3.3 分钟/镜（今晚 tonight_show 7 镜约 14 分钟、history_talk 7 镜约 14 分钟）；16 模板 ≈ 3 小时量级，batch 断点续跑（QC 已过则 skip）。
6. **黑帧风险**：静态卡片/静帧 clip 若近黑会触发 blackdetect；render-dev 已修测试假卡片并加卡片亮度守卫，正式 tonight_show/history_talk 实测 blackdetect=0；其余模板逐个验。
7. **prompt_id 全局唯一性**：依赖 ComfyUI `/history` 返回真实 id；QA 同时检查模板内与跨模板全局唯一，任何复用即验收失败。
8. **H3 身份/场景一致性（需人审，自动 QC 抓不到）**：history_talk 实测同一角色跨镜有身份漂移（ali 发色/耳机色、dawei 胡须/外套细节变化；host 较稳），H3 对 2D ink 首帧重绘为半写实动画风（靠 ink_amber 调色统一）。已提交 Lead 人审，必要时靠 prompt 身份锚点或减少特写。
9. **输出目录约定**：P2 终门只认 §8 的 16 个模板 id；`<template>_<NN>s[_vN]` 版本归档与 `<template>_5min`（P3）不参与 P2 验收；其它非契约目录会被 `test_unexpected_output_dirs` 报错。
10. **批量报告分 chunk 合并**：Lead 夜间序列按 4 chunk 跑，`batch_report.json` 会合并已有产物（本 chunk 外条目 `status=skipped`/`note=existing_output`），QA 已兼容该字段口径。
11. **5 分钟版（P3）**：`tonight_show_5min` 为 P3 附属产物，不在 P2 16×30-45s 验收范围，另行人工抽审。

## 8. 最终回归记录（2026-10-26 07:34）

- `python -m pytest tests -q` → **327 passed / 0 skipped**
- `python -m pytest pipeline/tests -q` → **143 passed**
- `python -m pytest tests/test_v3_acceptance.py -q` → **90 passed / 0 skipped**（16/16 模板硬门全绿）
- `python -m pytest tests/test_template_v3.py -q` → **89 passed / 0 skipped**
- `python -m pipeline.template_v3 --validate contract/templates_v3 --expect 16` → **16/16 通过，0 ERR / 0 warn**

## 9. 证据路径索引

| 证据 | 路径 |
|---|---|
| P0 校验输出 | 命令输出（本文 §2 摘要），CLI `--validate --expect 16` |
| 模板差异复核 | `tmp/qa/template_review.json`（16 模板签名/plate/Jaccard/形态） |
| 验收断言自检 | `tmp/qa/selftest_acceptance.py`（fake 产物正向 7 + 反向 17） |
| 逐模板验收明细 | `tmp/qa/acceptance_results.json`（collect_acceptance.py 采集，含硬门 gates） |
| 模板 previs 目检 | `tmp/qa/previs_grid.png`（16 模板首帧 4×4）+ `tmp/qa/review_previs_smoke.py` |
| 首帧构图目检 | `tmp/qa/stage_check/`（history_talk/slow_life/classics 等合成首帧） |
| 逐模板成片 | `outputs/templates_v3/{id}/final.mp4` |
| H3 证据链 | `outputs/templates_v3/{id}/h3_meta.json` |
| QC 报告 | `outputs/templates_v3/{id}/qc_report.json` |
| 抽帧/contact sheet | `outputs/templates_v3/{id}/frames/` |
| 剧本/计划 | `outputs/templates_v3/{id}/work/episode.json`、`work/manifest.json` |
| 水墨人审帧 | `tmp/qa/ink_review/history_talk/` |
| 批量报告 | `outputs/templates_v3/batch_report.json` |
| 运行序列状态 | `outputs/templates_v3/sequence_state.json`、`sequence.log` |
| 验收测试 | `tests/test_template_v3.py`、`tests/test_v3_acceptance.py` |

## 10. P3 附属产物：tonight_show_5min（**不纳入 P2 16/16 终门**）

> 本节仅记录 P3 抽检结果；P2 终门仍以 §0/§4 的 16/16 为准。P3 产物路径：
> `outputs/templates_v3/tonight_show_5min/`（`--out .../tonight_show_5min/final.mp4`，不覆盖 P2 的 tonight_show 40s 产物）。

抽检脚本：`tmp/qa/check_p3_5min.py`；机器可读结果：`tmp/qa/p3_5min.json`；结论：**✅ 抽检通过**（failed gates：无）。

| 项 | 结果 |
|---|---|
| 成片 | 299.19s / 1280x720 / 25.0fps / audio=True / blackdetect=0 |
| 计划 | 59 镜（非卡片 57 + 卡片 2），计划 299.19s，Δ+0.0s |
| H3 覆盖 | 57 镜 h3=true（应=非卡片镜数）；workflow=['05_ref2va_4step_sla'] |
| prompt_id | 57 个，唯一 57（模板内）；与 40s 版 overlap=0（不同属正常，不做跨版本唯一要求） |
| seed/ref_image/action | True / True / True |
| QC | ok=True deliverable=True previs=False fallbacks=0；checks 全绿=True |
| 字幕 | 对话字幕 75 块；qc 对话镜 22（subtitle_coverage check ok=True）；时间跨度 [2.1, 291.64]；字面墙钟 25/50/75% 采样：0.25=无（非对话镜）(最近 0.5s)、0.5=无（非对话镜）(最近 2.44s)、0.75=无（非对话镜）(最近 0.02s)；对话块分位采样：0.25=✅、0.5=✅、0.75=✅ |
| 音频/BGM | 音量抽样(dB)：0.05=-44.1、0.25=-46.4、0.5=-20.3、0.75=-19.8、0.95=-44.8；音频流 299.19s / 成片 299.19s；BGM 源文件 ['soft_pad.wav'] |
| 抽帧（人审） | `D:\BaiduSyncdisk\wmj_work\XJ\AiJue\tmp\qa\p3_5min\frame_0_03pct.jpg`、`D:\BaiduSyncdisk\wmj_work\XJ\AiJue\tmp\qa\p3_5min\frame_1_33pct.jpg`、`D:\BaiduSyncdisk\wmj_work\XJ\AiJue\tmp\qa\p3_5min\frame_2_66pct.jpg`、`D:\BaiduSyncdisk\wmj_work\XJ\AiJue\tmp\qa\p3_5min\frame_3_96pct.jpg` |

说明：本片 59 镜中只有 22 个**对话镜**按设计带台词字幕（其余为反应/B-roll/空镜与卡片），因此「墙钟 25%/50%/75% 处必有字幕」这一字面采样可能落在非对话镜上（报告内给出最近字幕距离）；实际字幕质量以 `qc_report.subtitle_coverage=1.0（22/22 对话镜）` + 对话字幕时间跨度 + 对话块分位采样三点覆盖共同判定。

> **P3 附属产物，不纳入 P2 16/16 终门**；P2 终门以 §0/§4 的 16/16 为准。
