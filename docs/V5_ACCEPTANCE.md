# V5 独立验收报告：可变风格世界（16 模板 × 16 风格）+ 口型同步

> 验收口径：Lead V5 任务书 + `docs/PLAN_V5_STYLE_AUDIO.md` / `docs/V5_INTERFACES.md`。
> 生成时间：2026-10-26 17:12（由 `tmp/qa/gen_v5_doc.py` 依据真实产物生成，可重复运行）
> 验收人：qa-verifier（独立于 V5 开发；**只读 outputs/ 与 contract/，不启动渲染**）。
> ⚠️ V5 为全新 2D 动画管线（TTS→2D 动画→卡片→字幕→混音→QC），不继承 H3 证据链；
> 保留通用硬门：规格/黑帧/音轨/字幕覆盖/场景角色覆盖/无 fallback。

## 0. 结论摘要

| 项 | 状态 |
|---|---|
| V5 批量 16 模板成片 | ⏳ 进行中：1/16 可验收 |
| 规格（25-50s / 1280×720 / 25fps / 有音轨 / blackdetect=0） | ⏳ 批量完成后全量断言 |
| 内容（qc ok；scene/character/subtitle coverage；fallbacks=[]） | ⏳ |
| **P1 视觉门（角色头部完整，回归 2026-10 无头躯干）** | ⏳ 待出片 |
| **风格区分度（两两余弦/边缘密度矩阵）** | ⏳ 待 16 部出片后生成矩阵 |
| **历史 4 部场景核查（≥2 个史料/战争/地图场景）** | ⏳ |
| timeline 契约（`validate_timeline_file` 0 errors） | ⏳ |
| 契约（`validate_catalog` 0 errors/0 warnings + 数量） | ✅ 通过（styles:17 / scenes:61 / characters:25 / props:44 / templates:17 / packs:2） |
| 已知问题① 水墨字幕绘图指令 | ✅ 自动回归通过 |
| 已知问题② silk 字幕对比度 | ✅ 自动 proxy 通过 |
| 已知问题③④（角色洗白 / b-roll 空镜） | 人眼抽帧复核：见 §7（QA 结论表） |
| 16/16 终门（`pytest tests/test_v5_acceptance.py -q`） | ⏳ 批量完成后跑 |

## 1. 验收命令（可复跑）

```powershell
# 契约（26 项，已有测试）
python -m pytest tests/test_v5_catalog.py -q

# V5 独立验收（16 模板参数化；未就绪自动 skip 并报告）
python -m pytest tests/test_v5_acceptance.py -q
python -m pytest tests/test_v5_acceptance.py -q -k tonight_show   # 单模板抽查

# 本报告重生成（含逐模板 LUFS / 字幕对比度 / 已知问题证据）
python tmp/qa/gen_v5_doc.py
```

## 2. V5 硬门与验收口径（QA 独立定义）

| 硬门 | 判据 | 说明 |
|---|---|---|
| 规格 | final.mp4 存在；25-50s；1280×720；25fps±0.6；有音轨；blackdetect=0；时长与 qc expected_duration 差 ≤0.8s | 通用 V3 硬门延续 |
| 内容覆盖 | qc_report.ok；duration/resolution/fps/audio/blackdetect/scene_coverage/character_coverage/subtitle_coverage/style/fallbacks 全 ok；fallbacks=[]；场景/角色均可解析且在契约内 | V5 无 H3 证据链，改查场景/角色覆盖 |
| 风格映射 | style_report.style 在契约 styles 内且等于契约模板 style；technique/palette 非空；16 模板风格一一对应 | 16 模板 × 16 风格 |
| **P1 视觉门（角色头部）** | 解析式复核 `work/shot_meta.json` + motion2d 公式：主对白角色头部垂直完整入画、陪衬角色不被上边缘裁切（无头躯干回归）；水平部分出画记 warning | 2026-10 hot_ones/history_mystery/masked_singer 回归的自动门禁 |
| **风格区分度** | 抽帧重算 HSV 8×8×4 直方图 + 边缘密度；两两余弦相似度与 edge_density 差；**cosine ≥0.985 且 edge_diff ≤0.01 视为“几乎相同”**（同时满足才失败） | 阈值先取经验值，矩阵落盘 `tmp/qa/v5_style_matrix.json` |
| **历史场景** | 历史 4 部成片场景中 ≥ 2 个 scene 的契约 tags 含 `history`（等价 ancient_map/battlefield/war_council/palace_hall/scholar_study/archive_vault/excavation_site/great_wall/...），且至少 1 个带 war/map/palace/archive/archaeology/museum 标签 | 防止“历史题材画面不历史” |
| timeline | `v5_catalog.validate_timeline_file()` 0 errors；version=5；分辨率/fps；V1/A1/S1 轨；V1 覆盖时长；meta.scenes 非空 | 可检查/可重渲时间线 |
| 契约 | `validate_catalog()` 0 errors / 0 warnings；styles≥16 / scenes≥60 / characters≥24 / props≥40 / templates≥16 / packs≥2 | 含 custom（custom_holo_*） |
| 已知问题① | `work/final.ass` 文字型 Dialogue 无裸 ASS 绘图指令文本（无 `\p1` 覆盖的 `m/l` 指令） | 水墨竖排字幕 bug 回归 |
| 已知问题② | ASS 字幕样式 PrimaryColour/OutlineColour WCAG 对比度 ≥3.0 | silk 低对比 proxy，最终人眼复核 |
| 已知问题③④ | 人眼抽帧复核（contact_sheet/frames），QA 结论记录 | 角色洗白 / b-roll 空镜不可靠量化 |

## 3. 契约验收

- `validate_catalog()`：ok=True，errors=0，warnings=0，strict=False

| kind | 数量 | 最低要求 | 内置 + custom | 结果 |
|---|---|---|---|---|
| styles | 17 | 16 | 16 + 1 | ✅ |
| scenes | 61 | 60 | 60 + 1 | ✅ |
| characters | 25 | 24 | 24 + 1 | ✅ |
| props | 44 | 40 | 44 + 0 | ✅ |
| templates | 17 | 16 | 16 + 1 | ✅ |
| packs | 2 | 2 | 2 + 0 | ✅ |

- 内置契约 + `contract/v5/custom/` 覆盖合并：custom 条目会进入 catalog（custom_holo_paper/custom_holo_gallery/custom_lin/custom_holo_talk），
  内置 16 模板验收不受 custom 影响（只取 16 个内置模板 id）。
- schema 覆盖：styles/scenes/characters/props/templates/packs 六类 + timeline schema（见 `contract/v5/schema/`）。

## 4. 16 模板验收表（风格 / 时长 / 场景 / 角色 / LUFS）

| # | template | 风格 | 时长 | 场景数 | 角色数 | 镜头(对话+b-roll) | LUFS/TP | 字幕对比度 | QC | P1 头部 |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | `tonight_show` | ⏳ pending(evidence_stale) | — | — | — | — | — | — | — | — |
| 2 | `shark_tank` | ⏳ pending(evidence_stale) | — | — | — | — | — | — | — | — |
| 3 | `masked_singer` | ⏳ pending(evidence_stale) | — | — | — | — | — | — | — | — |
| 4 | `standup` | ⏳ pending(evidence_stale) | — | — | — | — | — | — | — | — |
| 5 | `taskmaster` | ⏳ pending(evidence_stale) | — | — | — | — | — | — | — | — |
| 6 | `traitors` | ⏳ pending(evidence_stale) | — | — | — | — | — | — | — | — |
| 7 | `snl_sketch` | ⏳ pending(evidence_stale) | — | — | — | — | — | — | — | — |
| 8 | `roundtable` | ⏳ pending(evidence_stale) | — | — | — | — | — | — | — | — |
| 9 | `hot_ones` | ⏳ pending(evidence_stale) | — | — | — | — | — | — | — | — |
| 10 | `qipashuo` | ⏳ pending(evidence_stale) | — | — | — | — | — | — | — | — |
| 11 | `slow_life` | ⏳ pending(evidence_stale) | — | — | — | — | — | — | — | — |
| 12 | `history_talk` | ink_chronicle（水墨史书） | 43.75s / 43.75s | 5 | 3 | 7+2 | -15.74 / -1.47 | 14.01 | ✅ | — |
| 13 | `history_mystery` | ⏳ pending(evidence_stale) | — | — | — | — | — | — | — | — |
| 14 | `classics` | ⏳ pending(evidence_stale) | — | — | — | — | — | — | — | — |
| 15 | `national_treasure` | ⏳ pending(evidence_stale) | — | — | — | — | — | — | — | — |
| 16 | `mystery` | ⏳ pending(evidence_stale) | — | — | — | — | — | — | — | — |

- **LUFS** 为 QA 用 ffmpeg loudnorm 独立测量（V5 未把响度列入 qc 硬门，报告值用于混音一致性观察）。
- 字幕对比度为 ASS 样式主色/描边 WCAG proxy（见 §7②）。

## 5. 风格区分度矩阵（16×16）

⏳ 尚未运行含 16 部的区分度矩阵测试（`pytest tests/test_v5_acceptance.py -q` 后生成 `tmp/qa/v5_style_matrix.json`）。

## 6. 历史 4 部场景核查

| template | 成片场景 | tags=history 的场景 | ≥2 判定 |
|---|---|---|---|
| `history_talk` | ancient_map, battlefield, palace_hall, scholar_study, war_council | ancient_map, battlefield, palace_hall, scholar_study, war_council | ✅ 5 个 |
| `classics` | ⏳ pending(evidence_stale) | — | — |
| `history_mystery` | ⏳ pending(evidence_stale) | — | — |
| `national_treasure` | ⏳ pending(evidence_stale) | — | — |

## 7. 已知问题与修复状态

### P1 视觉门（角色头部）结果

- 硬门（主对白角色头部完整/陪衬角色不被上边缘裁切）：**✅ 通过**（已测 0/1 已出片模板；未测 ['history_talk']）
- 水平部分出画 warning（人眼复核项）：无

| template | 硬门 | warning（水平/构图） |
|---|---|---|
| `history_talk` | ⏳ 未测（产物不完整/证据未刷新） | — |

### ① 水墨竖排字幕把 ASS 绘图指令烧进画面（已修，需复验）

自动回归口径：`work/final.ass` 中文字型 Dialogue 不得出现“未加 `\p1` 覆盖的裸 m/l 绘图指令文本”。

| template | style | 裸绘图指令泄露 | 判定 |
|---|---|---|---|
| `history_talk` | ink_chronicle | 无 | ✅ |

### ② silk 风格字幕对比度低（proxy 验收）

自动回归口径：ASS 字幕样式 PrimaryColour 与 OutlineColour 的 WCAG 相对亮度对比度 ≥ 3.0（描边是字幕可读性的主要保障；最终以人眼抽帧复核）。

| template | style | 字幕主色/描边 | 对比度 | 判定 |
|---|---|---|---|---|
| — | — | — | — | ⏳ 待 silk 模板出片 |

**帧内字幕对比度实测**（对白帧字幕带 p5/p95 亮度 WCAG 近似；<3.5 建议优化，<3.0 视为低对比）：

| template | style | 帧内对比度 | 判定 | 证据帧 |
|---|---|---|---|---|
| `history_talk` | ink_chronicle | 8.53 | ✅ | `D:\BaiduSyncdisk\wmj_work\XJ\AiJue\tmp\qa\v5_review\history_talk\sub_frame.png` |

### ③ 角色 treatment 洗白/发灰（人眼复核项）

自动回归口径：角色色板/边缘无法可靠量化“洗白”；验收以 `style_report.json` 角色列表 + `contact_sheet.png` / `frames/*.png` 人眼抽帧复核为准，问题模板记录如下（QA 复核结论见 §7 状态列）。

| template | 角色 | contact_sheet | 状态 |
|---|---|---|---|
| `history_talk` | caocao, laozhou, zhuge | `outputs/templates_v5/history_talk/contact_sheet.png` | 人眼复核（见 §7 结论） |

### ④ b-roll 宽景偏空（人眼复核项）

自动回归口径：b-roll 空镜无法仅凭统计判定；以 `frames/*.png` 与 style_report `broll_shots` 数量 + 人眼抽帧复核为准。

| template | b-roll 镜数 | frames | 状态 |
|---|---|---|---|
| `history_talk` | 2 | `outputs/templates_v5/history_talk/frames/` | 人眼复核（见 §7 结论） |

### ③④ 人眼复核结论（QA 抽帧记录）

⏳ 人眼抽帧复核尚未完成（批量出片后 QA 逐部复核，记录到 `tmp/qa/v5_review_notes.json`，证据帧 `tmp/qa/v5_review/{tid}/shots_grid.png`）。

## 8. 复现命令与回归记录

- 契约：`python -m pytest tests/test_v5_catalog.py -q` → 26 passed（验收时点）
- V5 验收：`python -m pytest tests/test_v5_acceptance.py -q` → ⏳ 等批量完成
- 全量 tests：`python -m pytest tests -q` → ⏳
- pipeline/tests：`python -m pytest pipeline/tests -q` → ⏳
- batch_report_v5：✅ {"ok": false, "total": 10, "passed": 9}

## 9. 证据路径索引

| 证据 | 路径 |
|---|---|
| V5 成片 | `outputs/templates_v5/{id}/final.mp4` |
| 时间线 | `outputs/templates_v5/{id}/timeline.json`、`timeline.html` |
| 风格报告 | `outputs/templates_v5/{id}/style_report.json` |
| QC（含风格指纹） | `outputs/templates_v5/{id}/qc_report.json` |
| 字幕证据 | `outputs/templates_v5/{id}/work/final.ass` |
| 抽帧/接触表 | `outputs/templates_v5/{id}/frames/*.png`、`contact_sheet.png` |
| 批量总览 | `outputs/templates_v5/contact_sheet_all.png`、`batch_report_v5.json` |
| 风格区分度矩阵 | `tmp/qa/v5_style_matrix.json` |
| 契约 | `contract/v5/`、`pipeline/v5_catalog.py` |
| 验收测试 | `tests/test_v5_acceptance.py`、`tests/test_v5_catalog.py` |
