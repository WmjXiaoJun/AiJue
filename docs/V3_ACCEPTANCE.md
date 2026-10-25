# V3 独立验收报告（qa-verifier）

> 验收口径：`docs/V3_INTERFACES.md` §9（QC 门必须全绿）+ §4（H3 证据链）+ §6（qc_v3 checks）。
> 生成时间：2026-10-26 02:22（本文件由 `tmp/qa/gen_acceptance_doc.py` 依据真实产物生成，可重复运行）
> 验收人：qa-verifier（独立于 render-dev / h3-dev / stage-dev）；**不启动渲染，只读 outputs/ 与 contract/**。

## 0. 结论摘要

| 项 | 状态 |
|---|---|
| P0：16/16 模板校验（strict plate） | ✅ 16/16，0 ERR / 0 warn |
| P0：校验器单测 | ✅ `tests/test_template_v3.py` 89 passed / 0 skipped |
| 模板差异性（签名/plate/Jaccard） | ✅ 签名 16/16 唯一；plate 16/16 唯一；Jaccard 最大 0.125 |
| P1/P2：成片渲染 | ⏳ 待渲染 |
| P1/P2：逐模板 QC 全绿 | ⏳ |
| P1/P2：blackdetect=0 | ⏳ |
| 2D 水墨人审（history_talk 抽帧） | ⏳ 待渲染 |

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
| 1 | `tonight_show` | ⏳ 待渲染 | — | — | — | — | — | — | — | — | — | — | — |
| 2 | `shark_tank` | ⏳ 待渲染 | — | — | — | — | — | — | — | — | — | — | — |
| 3 | `masked_singer` | ⏳ 待渲染 | — | — | — | — | — | — | — | — | — | — | — |
| 4 | `standup` | ⏳ 待渲染 | — | — | — | — | — | — | — | — | — | — | — |
| 5 | `taskmaster` | ⏳ 待渲染 | — | — | — | — | — | — | — | — | — | — | — |
| 6 | `traitors` | ⏳ 待渲染 | — | — | — | — | — | — | — | — | — | — | — |
| 7 | `snl_sketch` | ⏳ 待渲染 | — | — | — | — | — | — | — | — | — | — | — |
| 8 | `roundtable` | ⏳ 待渲染 | — | — | — | — | — | — | — | — | — | — | — |
| 9 | `hot_ones` | ⏳ 待渲染 | — | — | — | — | — | — | — | — | — | — | — |
| 10 | `qipashuo` | ⏳ 待渲染 | — | — | — | — | — | — | — | — | — | — | — |
| 11 | `slow_life` | ⏳ 待渲染 | — | — | — | — | — | — | — | — | — | — | — |
| 12 | `history_talk` | ⏳ 待渲染 | — | — | — | — | — | — | — | — | — | — | — |
| 13 | `history_mystery` | ⏳ 待渲染 | — | — | — | — | — | — | — | — | — | — | — |
| 14 | `classics` | ⏳ 待渲染 | — | — | — | — | — | — | — | — | — | — | — |
| 15 | `national_treasure` | ⏳ 待渲染 | — | — | — | — | — | — | — | — | — | — | — |
| 16 | `mystery` | ⏳ 待渲染 | — | — | — | — | — | — | — | — | — | — | — |

## 5. 2D 水墨（history_talk）人审证据

⏳ `history_talk` 尚未渲染；渲染后运行 `python -m pytest tests/test_v3_acceptance.py -q -k ink_review` 会自动抽帧到 `tmp/qa/ink_review/history_talk/` 并打印路径供人审。

## 6. 回归基线

| 时点 | tests/ | pipeline/tests/ | 说明 |
|---|---|---|---|
| P0 前（QA 接手） | 148 passed | 73 passed | 基线 |
| P0 模板完成 | 240 passed / 9 skipped | 101 passed / 2 failed | 2 fail = render_v3 自测黑尾（已发 render-dev，待修；非模板问题） |
| 最终 | 以最后一次运行记录为准 | 以最后一次运行记录为准 | 见 §8 遗留问题 |

## 7. 遗留问题与风险

1. **校验器与接口文档的差异（低风险，Lead 已确认）**：`stage.scene_desc` 文档写“必填”，校验器只 warn 不阻断；16 模板已全部填写，验收时以 warning 清单为准。
2. **动作库跨模板相似度**：校验器对 action 集合 Jaccard>80% 只 warn；独立复核最大 0.125，当前无风险，但后续新增模板需盯。
3. **plate 策略**：§8 指定 16 张 plate 唯一；校验器对 plate 复用只 warn。当前 0 复用。
4. **演示时长 vs 模板 target_duration**：模板 target_duration=300（5 分钟目标），P2 demo 成片 30–45s；验收按 `work/episode.json` 计划时长核对 ±0.6s，不是按 300s。
5. **GPU 串行与 H3 速度**：16 模板 × 8–10 镜 × 2–4 分钟/镜 ≈ 数小时；batch 断点续跑（QC 已过则 skip）。
6. **黑帧风险**：静态片尾卡如果底色纯黑会触发 blackdetect（render_v3 自测已复现片尾 2.2s 纯黑）；验收硬门要求黑帧段=0，必须修到 0 或保证片尾卡不是纯黑。
7. **prompt_id 全局唯一性**：依赖 ComfyUI `/history` 返回的真实 id；QA 同时检查模板内与跨模板全局唯一，任何复用即验收失败。
8. **H3 身份/场景一致性**：QA 只做证据链与规格硬门；画面身份漂移需人审（contact sheet + 抽帧），建议 Lead 抽查 2–3 个模板。

## 8. 最终回归记录（待填）

- `python -m pytest tests -q`：⏳
- `python -m pytest pipeline/tests -q`：⏳
- `python -m pytest tests/test_v3_acceptance.py -q`：⏳（等 16 模板渲染落盘）

## 9. 证据路径索引

| 证据 | 路径 |
|---|---|
| P0 校验输出 | 命令输出（本文 §2 摘要），CLI `--validate --expect 16` |
| 模板差异复核 | `tmp/qa/template_review.json`（16 模板签名/plate/Jaccard/形态） |
| 验收断言自检 | `tmp/qa/selftest_acceptance.py`（fake 产物正向 7 + 反向 16） |
| 逐模板成片 | `outputs/templates_v3/{id}/final.mp4` |
| H3 证据链 | `outputs/templates_v3/{id}/h3_meta.json` |
| QC 报告 | `outputs/templates_v3/{id}/qc_report.json` |
| 抽帧/contact sheet | `outputs/templates_v3/{id}/frames/` |
| 剧本/计划 | `outputs/templates_v3/{id}/work/episode.json`、`work/manifest.json` |
| 水墨人审帧 | `tmp/qa/ink_review/history_talk/` |
| 验收测试 | `tests/test_template_v3.py`、`tests/test_v3_acceptance.py` |
