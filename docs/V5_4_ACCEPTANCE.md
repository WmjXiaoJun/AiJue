# V5.4 独立验收报告：电影级剪辑 / 镜头语言 / 分镜 / 转场（16 模板各自不同）

> 用户诉求：“每个模板还要有不同的剪辑、电影级镜头、分镜、跳转(转场)”；验收口径见
> `docs/PLAN_V5_4_EDITORIAL.md` / `docs/V5_4_EDITING.md` / `docs/V5_4_REPORT.md`；
> 上一版：`docs/V5_2_ACCEPTANCE.md`（AI 美术 + 前端绑定 16/16）。
> 生成时间：2026-10-26 20:27（由 `tmp/qa/gen_v5_4_doc.py` 依据真实产物生成，可重复运行）
> 验收人：qa-verifier（独立于 V5.4 开发；只读 outputs/contract/frontend，不启动渲染/浏览器）。

## 0. 结论摘要

| 项 | 状态 |
|---|---|
| V5.4 16 部成片（剪辑/分镜/转场） | ⏳ 进行中：8/16 可验收 |
| 剪辑档案（editorial/*.json） | ✅ 16/16 字段齐全，组合唯一；camera 覆盖 17 种 / transition 覆盖 14 种 |
| 剪辑计划复算（build_edit_plan + validate_plan） | ⏳ |
| 分镜产物（storyboard.json/html/png） | ⏳ |
| timeline.editing（boundaries/cuts/summary） | ⏳ |
| per-cut 转场真生效 | ⏳ |
| 时长公式 | ⏳ |
| 剪辑多样性 | ⏳ |
| 16 部原硬门（时长/分辨率/QC/voice_fixed/P1） | ⏳ |
| 风格区分度（16 部 0 对“几乎相同”） | ⏳ |
| 前端分镜/剪辑视图 | dist 分镜标记 ✅ 命中 ['storyboard', '分镜', '剪辑', 'camera_palette', 'transition_palette'] |
| 契约 | ✅ validate_catalog 0 errors/0 warnings |

## 1. 验收命令（可复跑）

```powershell
python -m pytest tests/test_v5_4_acceptance.py -q
python -m pytest tests/test_v5_4_acceptance.py -q -k "editorial"   # 剪辑档案
python -m pytest tests/test_v5_4_acceptance.py -q -k "storyboard"  # 分镜产物
python -m pytest tests/test_v5_4_acceptance.py -q -k "diversity"   # 剪辑多样性/唯一性
python -m pytest tests/test_v5_4_acceptance.py -q -k "per_cut or duration"  # 转场/时长
python tmp/v52_frontend/curl_evidence.py http://127.0.0.1:8090      # 前端证据（需重启后端）
python tmp/qa/gen_v5_4_doc.py                                      # 本报告重生成
```

## 2. V5.4 硬门与验收口径（QA 独立定义）

| 硬门 | 判据 | 说明 |
|---|---|---|
| 剪辑档案 | 16/16 `contract/v5/editorial/<id>.json` 字段齐全；palette ⊆ motion2d 17 运镜 / editing 37 xfade；16 份组合（shot_sequence+camera_palette+transition_palette+比例）两两不同 | 每模板不同剪辑 |
| 剪辑计划 | `editorial.build_edit_plan` 复算 16/16 `validate_plan` OK（≥4 景别/≥3 运镜/≥2 转场/无相邻同景别）；与 `work/edit_plan.json` 逐镜一致 | 防“只写档案不生效” |
| 分镜产物 | `storyboard.json` 镜数 = V1 clips（含卡片）= qc shots+cards；每镜 shot_size/camera/transition_out；png/html 存在；manifest 记录 | 分镜表可交付 |
| EDL/timeline | `timeline.editing.{mode,boundaries,cuts,summary}`；boundaries=镜头数-1；V1 body 镜 shot_size/camera/transition 与 edit_plan 一致 | OpenTimelineIO 风格 EDL |
| per-cut 转场 | manifest.transition=editorial/per-cut；V1 每对相邻镜实际重叠 = 计划边界 duration（cut=0） | 每剪辑点独立转场（非全片一种） |
| 时长公式 | `sum(V1 shot 时长) - 实际重叠 == timeline.duration == final probe == qc.expected`（±0.35s） | EDL 精确时长 |
| 剪辑多样性 | 每部实际 camera ≥4、transition ≥2、景别 ≥4；硬切比例与档案 `hard_cut_ratio` 偏差 ≤0.25；16 部组合唯一；全局运镜 ≥12 种 | 电影级镜头语言 |
| 回归 | 16/16 blackdetect=0（d=0.30）/分辨率 1280×720/25fps/音轨/qc ok/voice_fixed/P1 头部；风格矩阵 0 对“几乎相同” | 不退化 |

## 3. 剪辑档案矩阵（16 份两两不同）

| # | template | label | pacing(l/m/c/coda) | opening→closing | shot_sequence | camera_palette (n) | transition_palette (n) | hard_cut 目标 | J/L/insert/detail |
|---|---|---|---|---|---|---|---|---|---|
| 1 | `tonight_show` | 霓虹深夜秀·快切综艺 | None/None/None/None | WS/truck_right → MS/push_in | WS MS MCU CU OTS MS DETAIL MCU INSERT WS | 7（truck_right, push_in, whip_pan, handheld, tilt_up, rack_focus, dolly_zoom） | 6（cut, cut, hblur, wiperight, slideleft, pixelize） | 0.42 | 0.45/0.12/0.18/0.12 |
| 2 | `shark_tank` | 蓝图路演·数据质询 | None/None/None/None | WS/truck_right → MS/pull_out | WS MS MCU CU DETAIL OTS INSERT WS MCU | 6（truck_right, push_in, dolly_zoom, tilt_up, pan_left, rack_focus） | 6（cut, wipeleft, circleopen, cut, pixelize, slideup） | 0.42 | 0.3/0.18/0.28/0.26 |
| 3 | `masked_singer` | 霓虹舞台·竞演揭面 | None/None/None/None | EWS/crane_up → MCU/pull_out | EWS WS MCU MS CU OTS DETAIL MCU WS INSERT | 7（crane_up, crane_down, orbit, dolly_zoom, push_in, whip_pan, handheld） | 6（fade, dissolve, circleopen, radial, cut, hblur） | 0.34 | 0.3/0.18/0.14/0.12 |
| 4 | `standup` | VHS 单口·长镜冷面 | None/None/None/None | WS/static → MS/pull_out | WS MS MCU OTS MS CU INSERT MS WS DETAIL | 6（static, handheld, push_in, tilt_down, pan_right, dolly） | 5（cut, cut, fade, hblur, dissolve） | 0.66 | 0.18/0.26/0.2/0.1 |
| 5 | `taskmaster` | 糖果竞技·混乱快剪 | None/None/None/None | WS/truck_left → MS/pull_out | WS MCU CU MS DETAIL MCU INSERT WS OTS | 6（truck_left, whip_pan, dolly_zoom, handheld, push_in, tilt_down） | 6（cut, hblur, pixelize, slideleft, radial, circleopen） | 0.34 | 0.4/0.1/0.24/0.16 |
| 6 | `traitors` | 烛光古堡·圆桌密谈 | None/None/None/None | WS/crane_up → CU/pull_out | WS MS CU OTS DETAIL MS MCU WS INSERT | 7（crane_up, orbit, push_in, rack_focus, tilt_up, pull_out, static） | 5（cut, fade, fadeblack, dissolve, wipeup） | 0.55 | 0.18/0.38/0.2/0.2 |
| 7 | `snl_sketch` | 粉笔小品·固定机位 | None/None/None/None | WS/static → MS/pull_out | WS MS OTS CU MCU DETAIL WS MS INSERT | 6（static, push_in, whip_pan, pan_left, handheld, tilt_up） | 6（cut, wipeup, cut, fade, hblur, pixelize） | 0.62 | 0.32/0.2/0.14/0.1 |
| 8 | `roundtable` | 油画圆桌·长谈深描 | None/None/None/None | EWS/crane_down → WS/pull_out | EWS WS MS MCU OTS CU MS DETAIL WS | 7（crane_down, orbit, push_in, pan_left, dolly, rack_focus, pull_out） | 4（cut, dissolve, fade, wipeleft） | 0.5 | 0.16/0.35/0.18/0.2 |
| 9 | `hot_ones` | 暖调访谈·双人对话 | None/None/None/None | WS/push_in → MCU/pull_out | WS MS MCU OTS CU DETAIL MS WS INSERT | 6（push_in, rack_focus, pan_left, dolly, handheld, pull_out） | 5（cut, dissolve, cut, fade, wipeleft） | 0.58 | 0.42/0.25/0.16/0.12 |
| 10 | `qipashuo` | 丝绢辩论·正反交锋 | None/None/None/None | WS/push_in → MS/pull_out | WS MS OTS MCU CU MS DETAIL OTS MCU | 7（push_in, rack_focus, pan_right, orbit, tilt_up, dolly, whip_pan） | 6（cut, dissolve, wipeleft, cut, fade, slideleft） | 0.48 | 0.48/0.18/0.14/0.1 |
| 11 | `slow_life` | 黏土治愈·慢生活 | None/None/None/None | EWS/static → WS/pull_out | EWS WS MS MCU DETAIL MS WS CU INSERT | 7（static, push_in, pan_right, handheld, tilt_up, dolly, pull_out） | 5（fade, cut, dissolve, fade, cut） | 0.4 | 0.1/0.38/0.3/0.22 |
| 12 | `history_talk` | 水墨史书·沉静史诗 | None/None/None/None | EWS/crane_down → MS/pull_out | EWS WS MS OTS CU DETAIL MS WS MCU INSERT | 7（crane_down, dolly, pan_right, push_in, rack_focus, pull_out, tilt_down） | 5（cut, cut, dissolve, fade, wipeleft） | 0.55 | 0.24/0.28/0.24/0.22 |
| 13 | `history_mystery` | 羊皮档案·调查剪辑 | None/None/None/None | WS/push_in → CU/pull_out | WS MS CU OTS DETAIL INSERT MS MCU DETAIL | 7（push_in, rack_focus, pan_left, dolly, tilt_down, pull_out, static） | 6（cut, cut, dissolve, fade, wiperight, vblur） | 0.58 | 0.28/0.18/0.32/0.3 |
| 14 | `classics` | 版刻章回·沉稳叙事 | None/None/None/None | EWS/crane_up → WS/pull_out | EWS MS MCU WS CU OTS MS DETAIL INSERT | 7（crane_up, push_in, pan_left, dolly, rack_focus, pull_out, tilt_up） | 5（cut, dissolve, wipeleft, cut, wipeup） | 0.52 | 0.2/0.32/0.22/0.18 |
| 15 | `national_treasure` | 博物馆金·国宝展陈 | None/None/None/None | EWS/crane_down → WS/pull_out | EWS MS MCU WS CU DETAIL MS OTS INSERT | 7（crane_down, crane_up, push_in, pan_right, dolly, rack_focus, orbit） | 6（cut, fade, dissolve, cut, wipeleft, fadeblack） | 0.55 | 0.15/0.38/0.22/0.28 |
| 16 | `mystery` | 黑色电影·悬疑夜雨 | None/None/None/None | WS/push_in → CU/pull_out | WS MS CU OTS DETAIL MS MCU WS INSERT | 7（push_in, dolly, rack_focus, pan_right, tilt_down, pull_out, handheld） | 5（cut, fade, dissolve, cut, fadeblack） | 0.6 | 0.18/0.42/0.16/0.2 |

- 剪辑档案组合：**16 种 / 16 份唯一**（重复：无）
- camera_palette 覆盖：**17 种**（17 种可选）：crane_down, crane_up, dolly, dolly_zoom, handheld, orbit, pan_left, pan_right, pull_out, push_in, rack_focus, static, tilt_down, tilt_up, truck_left, truck_right, whip_pan
- transition_palette 覆盖：**14 种**（37 种 xfade）：circleopen, cut, dissolve, fade, fadeblack, hblur, pixelize, radial, slideleft, slideup, vblur, wipeleft, wiperight, wipeup

## 4. 16 部 V5.4 终门表

| # | template | 风格 | 时长 | 镜数(卡片) | 景别 | 运镜 | 转场 | 硬切 实际/目标 | J/L/insert | QC | voice_fixed | P1 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | `tonight_show` | ⏳ pending | — | — | — | — | — | — | — | — | — | — |
| 2 | `shark_tank` | ⏳ pending | — | — | — | — | — | — | — | — | — | — |
| 3 | `masked_singer` | hologram_stage（全息舞台） | 41.7s / 41.7s | 9（2） | 5 EWS/MCU/MS/OTS/WS | 4 | 4（cut/dissolve/fade/hblur） | 0.33/0.34 | 1/0/0 | ✅ | ✅ | — |
| 4 | `standup` | ⏳ pending | — | — | — | — | — | — | — | — | — | — |
| 5 | `taskmaster` | ⏳ pending | — | — | — | — | — | — | — | — | — | — |
| 6 | `traitors` | ⏳ pending | — | — | — | — | — | — | — | — | — | — |
| 7 | `snl_sketch` | ⏳ pending | — | — | — | — | — | — | — | — | — | — |
| 8 | `roundtable` | ⏳ pending | — | — | — | — | — | — | — | — | — | — |
| 9 | `hot_ones` | film_interview（暖光胶片访谈） | 38.58s / 38.58s | 10（2） | 4 MCU/MS/OTS/WS | 4 | 3（cut/dissolve/fade） | 0.57/0.58 | 2/1/0 | ✅ | ✅ | — |
| 10 | `qipashuo` | silk_arena（绢本设色辩论） | 35.72s / 35.72s | 10（2） | 4 MCU/MS/OTS/WS | 6 | 5（cut/dissolve/fade/slideleft/wipeleft） | 0.43/0.48 | 3/2/0 | ✅ | ✅ | — |
| 11 | `slow_life` | ⏳ pending | — | — | — | — | — | — | — | — | — | — |
| 12 | `history_talk` | ink_chronicle（水墨史书） | 36.13s / 36.13s | 7（2） | 4 EWS/MS/OTS/WS | 4 | 3（cut/dissolve/fade） | 0.50/0.55 | 1/2/0 | ✅ | ✅ | — |
| 13 | `history_mystery` | parchment_case（羊皮纸档案） | 43.23s / 43.23s | 10（2） | 5 CU/MCU/MS/OTS/WS | 6 | 4（cut/dissolve/fade/vblur） | 0.57/0.58 | 1/1/0 | ✅ | ✅ | — |
| 14 | `classics` | woodblock_classic（版刻典籍） | 42.25s / 42.25s | 10（2） | 5 EWS/MCU/MS/OTS/WS | 6 | 3（cut/dissolve/fade） | 0.57/0.52 | 1/2/0 | ✅ | ✅ | — |
| 15 | `national_treasure` | museum_gold（博物馆金黑） | 41.06s / 41.06s | 8（2） | 5 EWS/MCU/MS/OTS/WS | 4 | 3（cut/dissolve/fade） | 0.60/0.55 | 0/4/0 | ✅ | ✅ | — |
| 16 | `mystery` | noir_rain（黑色电影） | 34.82s / 34.82s | 10（2） | 5 CU/MCU/MS/OTS/WS | 5 | 3（cut/dissolve/fade） | 0.57/0.60 | 2/2/0 | ✅ | ✅ | — |

## 5. 分镜与 EDL 产物

| template | storyboard.json 镜数 | storyboard.png | storyboard.html | boundaries=镜-1 | 计划转场损失 | 实际重叠 | sum(镜头) | 时长公式 |
|---|---|---|---|---|---|---|---|---|
| `masked_singer` | 9 | 831KB | 5.3KB | 8=8 | 3.0s | 3.0s | 44.7s | 41.7s == 41.7s |
| `hot_ones` | 10 | 699KB | 5.6KB | 9=9 | 2.4s | 2.4s | 40.98s | 38.58s == 38.58s |
| `qipashuo` | 10 | 753KB | 5.6KB | 9=9 | 2.7s | 2.7s | 38.42s | 35.72s == 35.72s |
| `history_talk` | 7 | 421KB | 4.3KB | 6=6 | 2.0s | 2.0s | 38.13s | 36.13s == 36.13s |
| `history_mystery` | 10 | 652KB | 5.7KB | 9=9 | 2.4s | 0.0s | 43.23s | 43.23s == 43.23s |
| `classics` | 10 | 782KB | 5.7KB | 9=9 | 2.5s | 2.5s | 44.75s | 42.25s == 42.25s |
| `national_treasure` | 8 | 517KB | 4.9KB | 7=7 | 2.0s | 2.0s | 43.06s | 41.06s == 41.06s |
| `mystery` | 10 | 590KB | 5.5KB | 9=9 | 2.4s | 2.4s | 37.22s | 34.82s == 34.82s |

- 分镜 JSON 结构：`{template,label,style,total,shots[],edit}`；每镜含
  `id/index/type/scene/role/character/line/speaker/duration/start/shot_size/camera/
  transition_out/j_cut/l_cut/insert/thumbnail`；
- `timeline.json` 的 `editing`：`{mode,boundaries[],cuts[],summary}`（summary 含
  signature/camera_types/transitions/hard_cut_ratio/j_cuts/l_cuts/inserts）。

## 6. 剪辑多样性与唯一性

| template | 实际运镜 (n) | 实际转场 (n) | 实际景别 (n) | 硬切 实际/目标 | 结论 |
|---|---|---|---|---|---|
| `tonight_show` | ⏳ | — | — | — | — |
| `shark_tank` | ⏳ | — | — | — | — |
| `masked_singer` | 4（crane_up, dolly_zoom, pull_out, push_in） | 4（cut/dissolve/fade/hblur） | 5（EWS/MCU/MS/OTS/WS） | 0.333/0.34 | ✅ |
| `standup` | ⏳ | — | — | — | — |
| `taskmaster` | ⏳ | — | — | — | — |
| `traitors` | ⏳ | — | — | — | — |
| `snl_sketch` | ⏳ | — | — | — | — |
| `roundtable` | ⏳ | — | — | — | — |
| `hot_ones` | 4（dolly, pull_out, push_in, rack_focus） | 3（cut/dissolve/fade） | 4（MCU/MS/OTS/WS） | 0.571/0.58 | ✅ |
| `qipashuo` | ⏳ | — | — | — | — |
| `slow_life` | ⏳ | — | — | — | — |
| `history_talk` | 4（crane_down, pull_out, push_in, rack_focus） | 3（cut/dissolve/fade） | 4（EWS/MS/OTS/WS） | 0.5/0.55 | ✅ |
| `history_mystery` | 6（dolly, pan_left, pull_out, push_in, rack_focus, tilt_down） | 4（cut/dissolve/fade/vblur） | 5（CU/MCU/MS/OTS/WS） | 0.571/0.58 | ✅ |
| `classics` | 6（crane_up, dolly, pan_left, pull_out, rack_focus, tilt_up） | 3（cut/dissolve/fade） | 5（EWS/MCU/MS/OTS/WS） | 0.571/0.52 | ✅ |
| `national_treasure` | 4（crane_down, orbit, pull_out, rack_focus） | 3（cut/dissolve/fade） | 5（EWS/MCU/MS/OTS/WS） | 0.6/0.55 | ✅ |
| `mystery` | 5（dolly, pan_right, pull_out, push_in, tilt_down） | 3（cut/dissolve/fade） | 5（CU/MCU/MS/OTS/WS） | 0.571/0.6 | ✅ |

- 16 部剪辑组合（signature+运镜集合+转场集合）：**None 种唯一**（重复：无）
- 全局实际运镜：**0 种**：
- 全局实际转场：**0 种**：

## 7. 前端分镜/剪辑绑定证据

| 项 | 结果 |
|---|---|
| dist | JS 414KB；assets={} |
| 分镜/剪辑标记 | 命中 ['storyboard', '分镜', '剪辑', 'camera_palette', 'transition_palette']；计数 {'storyboard': 31, '分镜': 38, '剪辑': 12, 'camera_palette': 4, 'transition_palette': 4} |
| API（8090） | {"editorial_history_talk": 404, "storyboard_history_talk": 404, "media_history_talk_storyboard.png": 404, "media_history_talk_storyboard.html": 404, "editorial_tonight_show": 404, "storyboard_tonight_show": 404, "media_tonight_show_storyboard.png": 404, "media_tonight_show_storyboard.html": 404} |
| 备注 | 后端进程未重启，V5.4 路由 404：['editorial_history_talk', 'storyboard_history_talk', 'media_history_talk_storyboard.png', 'media_history_talk_storyboard.html', 'editorial_tonight_show', 'storyboard_tonight_show', 'media_tonight_show_storyboard.png', 'media_tonight_show_storyboard.html'] |
| curl_evidence | 30/30（base=http://127.0.0.1:8090） |

## 8. 人眼复核（镜头语言/转场/分镜）

⏳ 人眼复核待 freeze 后填写（证据：`tmp/qa/v5_review/{tid}/shots_grid.png`、`outputs/templates_v5/contact_sheet_all.png`、`style_showcase.png`）。

## 9. 风格区分度（回归）

⏳ 待 16 部就绪后运行 `pytest tests/test_v5_4_acceptance.py -q` 生成。

## 10. 回归记录与遗留

- 复现/回归记录：`tmp/qa/final_regression_v5_4.json`
- **V5.4 验收**：⏳ 等批量完成
- **V5.2 回归（美术/前端）**：⏳
- **V5 回归（187 项）**：⏳
- **全量 tests**：⏳
- `batch_report_v5.json`：✅ {"ok": true, "total": 16, "passed": 16}

### 遗留 P2（不阻塞交付）

1. 剪辑/转场速率与镜头运动幅度为人眼复核主观点，未做自动审美评分；
2. J/L cut 为音频重叠（听感），未做波形自动校验；
3. 前端 API 需后端进程重启后生效（质量门以 dist + mock 证据为准）。

## 11. 证据路径索引

| 证据 | 路径 |
|---|---|
| V5.4 成片 | `outputs/templates_v5/{id}/final.mp4` |
| 剪辑档案 | `contract/v5/editorial/<id>.json` |
| 分镜产物 | `outputs/templates_v5/{id}/storyboard.json/.html/.png` |
| 剪辑计划（实渲） | `outputs/templates_v5/{id}/work/edit_plan.json`、`work/episode.json` |
| timeline EDL | `outputs/templates_v5/{id}/timeline.json`（`editing`） |
| 剪辑档案证据 | `tmp/qa/v5_4_editorial_evidence.json` |
| 分镜/EDL/时长证据 | `tmp/qa/v5_4_storyboard_evidence.json`、`tmp/qa/v5_4_timeline_evidence.json` |
| 多样性矩阵 | `tmp/qa/v5_4_edit_matrix.json` |
| 前端证据 | `tmp/qa/v5_4_frontend_evidence.json`、`tmp/v52_frontend/evidence/` |
| 人眼复核帧 | `tmp/qa/v5_review/{tid}/shots_grid.png`、`outputs/templates_v5/contact_sheet_all.png`、`style_showcase.png` |
| 验收测试 | `tests/test_v5_4_acceptance.py`（+ 回归 `test_v5_2_acceptance.py` / `test_v5_acceptance.py`） |
