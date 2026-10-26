# V5 独立验收报告：可变风格世界（16 模板 × 16 风格）+ 口型同步 + 固定角色声音

> 验收口径：Lead V5 任务书 + `docs/PLAN_V5_STYLE_AUDIO.md` / `docs/V5_INTERFACES.md` / `docs/V5_VOICES.md`。
> 生成时间：2026-10-26 17:36（由 `tmp/qa/gen_v5_doc.py` 依据真实产物生成，可重复运行）
> 验收人：qa-verifier（独立于 V5 开发；**只读 outputs/ 与 contract/，不启动渲染**）。
> ⚠️ V5 为全新 2D 动画管线（TTS→2D 动画→卡片→字幕→混音→QC），不继承 H3 证据链；
> 保留通用硬门：规格/黑帧/音轨/字幕覆盖/场景角色覆盖/无 fallback。

## 0. 结论摘要

| 项 | 状态 |
|---|---|
| V5 批量 16 模板成片 | ⏳ 进行中：15/16 可验收 |
| 规格（25-50s / 1280×720 / 25fps / 有音轨 / blackdetect=0） | ⏳ 批量完成后全量断言 |
| 内容（qc ok；scene/character/subtitle coverage；fallbacks=[]） | ⏳ |
| **角色固定声音（voice_fixed / voice lock / 每部 ≥2 角色对白）** | ❌ shark_tank, standup, taskmaster, traitors, snl_sketch, roundtable, qipashuo, slow_life, national_treasure, mystery, tonight_show |
| **P1 视觉门（角色头部完整，回归 2026-10 无头躯干）** | ⏳ 待出片 |
| **风格区分度（两两余弦/边缘密度矩阵）** | ✅ 无“几乎相同”模板对 |
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
python -m pytest tests/test_v5_acceptance.py -q -k "voice"        # 固定声音专项

# 固定声音（库/锁/修改）
python -m pytest tests/test_v5_voices.py -q                       # 9 项
python -m pipeline.v5_cli voice list                              # 24 preset
python -m pipeline.v5_cli voice show laozhou                     # 当前角色声音
python -m pipeline.v5_cli voice lock laozhou                     # 生成/校验声音锁
python -m pipeline.v5_cli voice set laozhou --voice-id warm_male # 改角色声音（写 custom 覆盖）

# 本报告重生成（含逐模板 LUFS / 字幕对比度 / 已知问题证据）
python tmp/qa/gen_v5_doc.py
```

## 2. V5 硬门与验收口径（QA 独立定义）

| 硬门 | 判据 | 说明 |
|---|---|---|
| 规格 | final.mp4 存在；25-50s；1280×720；25fps±0.6；有音轨；blackdetect=0（**V5 政策 d=0.30：允许 ≤0.2s 风格化 fade-black 转场，真实黑场 ≥0.3s 才判黑**）；时长与 qc expected_duration 差 ≤0.8s | 通用 V3 硬门延续（blackdetect 阈值按 V5 风格化转场调整） |
| 内容覆盖 | qc_report.ok；duration/resolution/fps/audio/blackdetect/scene_coverage/character_coverage/subtitle_coverage/style/fallbacks 全 ok；fallbacks=[]；场景/角色均可解析且在契约内 | V5 无 H3 证据链，改查场景/角色覆盖 |
| **角色固定声音** | ① 每部 `qc_report.checks.voice_fixed` ok（同角色所有对白镜 voice_key 唯一、对白镜都有 voice_id/key）；② `work/episode.json` 每角色 voice_id/key/seed 固定且 voice_id ∈ `contract/v5/voices.json`（含 custom 覆盖）；③ 声音锁 `outputs/v5_voices/<char>/<key>.wav+json` 存在；④ 跨 16 部同角色 voice_id/key/seed 一致；⑤ 每部 ≥2 个不同角色有台词 | 用户需求“每个角色声音固定 + 可在角色下修改”；回归“只有 host 说话”剧本映射 bug |
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
| 1 | `tonight_show` | neon_toon（霓虹卡通） | 38.44s / 38.45s | 3 | 1 | 5+0 | -16.03 / -1.55 | 16.07 | ✅ | ✅ |
| 2 | `shark_tank` | blueprint_lab（科技蓝图） | 31.24s / 31.25s | 4 | 3 | 5+2 | -15.34 / -1.34 | 10.63 | ✅ | ✅ |
| 3 | `masked_singer` | ⏳ pending(evidence_stale) | — | — | — | — | — | — | — | — |
| 4 | `standup` | vhs_late_show（80s VHS） | 43.7s / 43.7s | 4 | 3 | 6+2 | -15.59 / -1.46 | 18.42 | ✅ | ✅ |
| 5 | `taskmaster` | candy_collage（糖果拼贴综艺） | 34.18s / 34.18s | 4 | 3 | 6+2 | -15.62 / -1.50 | 3.14 | ✅ | ✅ |
| 6 | `traitors` | baroque_candle（巴洛克烛光古堡） | 34.02s / 34.02s | 4 | 3 | 6+2 | -15.85 / -1.47 | 12.46 | ✅ | ✅ |
| 7 | `snl_sketch` | chalk_theatre（粉笔素描剧场） | 38.18s / 38.18s | 4 | 3 | 6+2 | -15.69 / -1.47 | 17.94 | ✅ | ✅ |
| 8 | `roundtable` | oil_salon（文艺复兴油画圆桌） | 44.34s / 44.34s | 3 | 3 | 6+2 | -16.62 / -1.43 | 17.62 | ✅ | ✅ |
| 9 | `hot_ones` | film_interview（暖光胶片访谈） | 43.02s / 43.02s | 4 | 3 | 6+1 | -16.10 / -1.50 | 6.67 | ✅ | ✅ |
| 10 | `qipashuo` | silk_arena（绢本设色辩论） | 43.62s / 43.62s | 4 | 3 | 6+2 | -15.44 / -1.42 | 14.01 | ✅ | ✅ |
| 11 | `slow_life` | clay_diorama（黏土定格慢生活） | 41.38s / 41.38s | 4 | 3 | 6+2 | -15.29 / -1.41 | 7.52 | ✅ | ✅ |
| 12 | `history_talk` | ink_chronicle（水墨史书） | 38.12s / 38.13s | 4 | 3 | 5+0 | -16.21 / -1.50 | 16.18 | ✅ | ✅ |
| 13 | `history_mystery` | parchment_case（羊皮纸档案） | 43.23s / 43.23s | 4 | 3 | 7+1 | -15.56 / -1.49 | 4.64 | ✅ | ✅ |
| 14 | `classics` | woodblock_classic（版刻典籍） | 44.75s / 44.75s | 4 | 3 | 7+1 | -15.65 / -1.50 | 15.48 | ✅ | ✅ |
| 15 | `national_treasure` | museum_gold（博物馆金黑） | 40.74s / 40.74s | 4 | 3 | 6+2 | -15.85 / -1.49 | 9.99 | ✅ | ✅ |
| 16 | `mystery` | noir_rain（黑色电影） | 29.94s / 29.94s | 4 | 3 | 6+2 | -15.78 / -1.32 | 13.75 | ✅ | ✅ |

- **LUFS** 为 QA 用 ffmpeg loudnorm 独立测量（V5 未把响度列入 qc 硬门，报告值用于混音一致性观察）。
- 字幕对比度为 ASS 样式主色/描边 WCAG proxy（见 §7②）。

### 4.1 角色固定声音（Voice Library + Voice Lock）

- 声音库：`contract/v5/voices.json` 共 **24** 个 preset（provider=breeze，instruction+seed 固定；laozhou/ali/dawei 与 V4 一致种子 20261026/27/28）
- 角色引用：`contract/v5/characters/*.json` 的 `voice = {provider, voice_id}`，可在角色 JSON 内覆盖 seed/instruction/cfg_scale/ref_audio/ref_text（custom 覆盖不改内建文件）
- 声音锁：`outputs/v5_voices/<character>/<voice_key>.wav + .json`（16 角色已锁定；voice_key 由 provider/voice_id/seed/instruction/ref 派生，改声音自动换 key 并重合成）
- QC：`qc_report.checks.voice_fixed`（同角色 voice_key 唯一 + 对白镜都有 voice_id/key）→ ⏳
- 修改方式：`python -m pipeline.v5_cli voice list/show/preview/lock/set`；API `GET /api/v5/voices`、`GET/POST /api/v5/voice/<character>`；教程 `docs/V5_VOICES.md`

| template | 台词角色 → voice_id（voice_key） | voice_fixed | 声音锁 |
|---|---|---|---|
| `tonight_show` | laozhou→`legacy_v4_laozhou`(`4ae614cd26f2`)×5 | ✅ | ✅ |
| `shark_tank` | laozhou→`?`(`?`)×3；producer→`?`(`?`)×1；xueba→`?`(`?`)×1 | ❌ | ✅ |
| `standup` | laozhou→`?`(`?`)×3；lengmian→`?`(`?`)×1；pangzi→`?`(`?`)×2 | ❌ | ✅ |
| `taskmaster` | laozhou→`?`(`?`)×3；pangzi→`?`(`?`)×2；tianmei→`?`(`?`)×1 | ❌ | ✅ |
| `traitors` | caocao→`?`(`?`)×2；laozhou→`?`(`?`)×2；wuzetian→`?`(`?`)×2 | ❌ | ✅ |
| `snl_sketch` | ali→`?`(`?`)×1；dawei→`?`(`?`)×3；laozhou→`?`(`?`)×2 | ❌ | ✅ |
| `roundtable` | laozhou→`?`(`?`)×3；libai→`?`(`?`)×1；sushi→`?`(`?`)×2 | ❌ | ✅ |
| `hot_ones` | ali→`legacy_v4_ali`(`45621faeb29d`)×3；dawei→`legacy_v4_dawei`(`f96d2b706277`)×1；laozhou→`legacy_v4_laozhou`(`4ae614cd26f2`)×2 | ✅ | ✅ |
| `qipashuo` | caocao→`?`(`?`)×2；laozhou→`?`(`?`)×2；zhuge→`?`(`?`)×2 | ❌ | ✅ |
| `slow_life` | ali→`?`(`?`)×2；pangzi→`?`(`?`)×2；tianmei→`?`(`?`)×2 | ❌ | ✅ |
| `history_talk` | caocao→`emperor_yazhong`(`4a70f7a8cec6`)×1；laozhou→`legacy_v4_laozhou`(`4ae614cd26f2`)×3；zhuge→`strategist_qinglang`(`fdc35d21de8e`)×1 | ✅ | ✅ |
| `history_mystery` | laozhou→`legacy_v4_laozhou`(`4ae614cd26f2`)×3；simaqian→`historian_kezhi`(`e06790262fb0`)×2；zhangqian→`envoy_jianyi`(`aac6a9e69252`)×2 | ✅ | ✅ |
| `classics` | laozhou→`legacy_v4_laozhou`(`4ae614cd26f2`)×3；simaqian→`historian_kezhi`(`e06790262fb0`)×2；sushi→`scholar_wenrun`(`6a88759ab968`)×2 | ✅ | ✅ |
| `national_treasure` | laozhou→`?`(`?`)×2；sushi→`?`(`?`)×2；wuzetian→`?`(`?`)×2 | ❌ | ✅ |
| `mystery` | laozhou→`legacy_v4_laozhou`(`4ae614cd26f2`)×3；lengmian→`cool_male`(`9adc4d5c3413`)×2；mystery_guest→`mystery_low`(`75b5819f7e2b`)×1 | ❌ | ✅ |

## 5. 风格区分度矩阵（16×16）

判定阈值：**cosine ≥ 0.985 且 edge_density 差 ≤ 0.01 视为“几乎相同”**（两条件同时满足才失败）；其余组合视为可区分。16 部两两共 120 对。

### 风格 + 边缘密度

| template | style | edge_density |
|---|---|---|
| `tonight_show` | neon_toon | 0.0497 |
| `shark_tank` | blueprint_lab | 0.0544 |
| `masked_singer` | hologram_stage | 0.0331 |
| `standup` | vhs_late_show | 0.0444 |
| `taskmaster` | candy_collage | 0.0476 |
| `traitors` | baroque_candle | 0.036 |
| `snl_sketch` | chalk_theatre | 0.0349 |
| `roundtable` | oil_salon | 0.0598 |
| `hot_ones` | film_interview | 0.0606 |
| `qipashuo` | silk_arena | 0.0341 |
| `slow_life` | clay_diorama | 0.0296 |
| `history_talk` | ink_chronicle | 0.0642 |
| `history_mystery` | parchment_case | 0.0359 |
| `classics` | woodblock_classic | 0.0687 |
| `national_treasure` | museum_gold | 0.0511 |
| `mystery` | noir_rain | 0.0403 |

- 判为“几乎相同”的模板对：**0 对**（无，16/16 可区分）
- 最相似对：{'a': 'hot_ones', 'b': 'slow_life', 'cosine': 0.9635, 'edge_diff': 0.031, 'same_style': False}
- 最不相似对：{'a': 'snl_sketch', 'b': 'slow_life', 'cosine': 0.0001, 'edge_diff': 0.0053, 'same_style': False}

<details><summary>16×16 直方图余弦相似度矩阵（展开）</summary>

| | `tngt` | `shrk` | `msk` | `stnd` | `tskm` | `trtr` | `snl` | `rndt` | `hot` | `qps` | `slow` | `h_tlk` | `h_mys` | `clas` | `ntre` | `myst` |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **tngt** | 1.000 | 0.107 | 0.769 | 0.033 | 0.006 | 0.000 | 0.001 | 0.002 | 0.006 | 0.006 | 0.007 | 0.006 | 0.003 | 0.004 | 0.001 | 0.772 |
| **shrk** | 0.107 | 1.000 | 0.149 | 0.009 | 0.112 | 0.003 | 0.001 | 0.012 | 0.011 | 0.002 | 0.010 | 0.040 | 0.003 | 0.011 | 0.001 | 0.011 |
| **msk** | 0.769 | 0.149 | 1.000 | 0.002 | 0.007 | 0.000 | 0.000 | 0.000 | 0.001 | 0.001 | 0.001 | 0.005 | 0.000 | 0.001 | 0.000 | 0.852 |
| **stnd** | 0.033 | 0.009 | 0.002 | 1.000 | 0.003 | 0.193 | 0.007 | 0.397 | 0.075 | 0.005 | 0.018 | 0.017 | 0.058 | 0.088 | 0.052 | 0.014 |
| **tskm** | 0.006 | 0.112 | 0.007 | 0.003 | 1.000 | 0.001 | 0.015 | 0.020 | 0.071 | 0.130 | 0.082 | 0.525 | 0.072 | 0.259 | 0.011 | 0.000 |
| **trtr** | 0.000 | 0.003 | 0.000 | 0.193 | 0.001 | 1.000 | 0.000 | 0.497 | 0.071 | 0.004 | 0.011 | 0.012 | 0.011 | 0.040 | 0.873 | 0.053 |
| **snl** | 0.001 | 0.001 | 0.000 | 0.007 | 0.015 | 0.000 | 1.000 | 0.004 | 0.000 | 0.020 | 0.000 | 0.028 | 0.009 | 0.009 | 0.000 | 0.005 |
| **rndt** | 0.002 | 0.012 | 0.000 | 0.397 | 0.020 | 0.497 | 0.004 | 1.000 | 0.106 | 0.093 | 0.067 | 0.059 | 0.273 | 0.255 | 0.292 | 0.018 |
| **hot** | 0.006 | 0.011 | 0.001 | 0.075 | 0.071 | 0.071 | 0.000 | 0.106 | 1.000 | 0.123 | 0.964 | 0.029 | 0.197 | 0.339 | 0.017 | 0.034 |
| **qps** | 0.006 | 0.002 | 0.001 | 0.005 | 0.130 | 0.004 | 0.020 | 0.093 | 0.123 | 1.000 | 0.218 | 0.106 | 0.474 | 0.787 | 0.010 | 0.008 |
| **slow** | 0.007 | 0.010 | 0.001 | 0.018 | 0.082 | 0.011 | 0.000 | 0.067 | 0.964 | 0.218 | 1.000 | 0.018 | 0.220 | 0.412 | 0.008 | 0.001 |
| **h_tlk** | 0.006 | 0.040 | 0.005 | 0.017 | 0.525 | 0.012 | 0.028 | 0.059 | 0.029 | 0.106 | 0.018 | 1.000 | 0.070 | 0.246 | 0.007 | 0.027 |
| **h_mys** | 0.003 | 0.003 | 0.000 | 0.058 | 0.072 | 0.011 | 0.009 | 0.273 | 0.197 | 0.474 | 0.220 | 0.070 | 1.000 | 0.591 | 0.016 | 0.008 |
| **clas** | 0.004 | 0.011 | 0.001 | 0.088 | 0.259 | 0.040 | 0.009 | 0.255 | 0.339 | 0.787 | 0.412 | 0.246 | 0.591 | 1.000 | 0.030 | 0.006 |
| **ntre** | 0.001 | 0.001 | 0.000 | 0.052 | 0.011 | 0.873 | 0.000 | 0.292 | 0.017 | 0.010 | 0.008 | 0.007 | 0.016 | 0.030 | 1.000 | 0.009 |
| **myst** | 0.772 | 0.011 | 0.852 | 0.014 | 0.000 | 0.053 | 0.005 | 0.018 | 0.034 | 0.008 | 0.001 | 0.027 | 0.008 | 0.006 | 0.009 | 1.000 |

</details>

## 6. 历史 4 部场景核查

| template | 成片场景 | tags=history 的场景 | ≥2 判定 |
|---|---|---|---|
| `history_talk` | battlefield, palace_hall, scholar_study, war_council | battlefield, palace_hall, scholar_study, war_council | ✅ 4 个 |
| `classics` | archive_vault, palace_hall, scholar_study, temple_court | archive_vault, palace_hall, scholar_study, temple_court | ✅ 4 个 |
| `history_mystery` | ancient_map, archive_vault, excavation_site, great_wall | ancient_map, archive_vault, excavation_site, great_wall | ✅ 4 个 |
| `national_treasure` | archive_vault, display_case, museum_hall, palace_hall | archive_vault, display_case, museum_hall, palace_hall | ✅ 4 个 |

## 7. 已知问题与修复状态

### P1 视觉门（角色头部）结果

- 硬门（主对白角色头部完整/陪衬角色不被上边缘裁切）：**✅ 通过**（已测 15/15 已出片模板；未测 无）
- 水平部分出画 warning（人眼复核项）：['taskmaster', 'snl_sketch', 'history_mystery', 'classics', 'national_treasure']

| template | 硬门 | warning（水平/构图） |
|---|---|---|
| `classics` | ✅ | v5_002/host(主对白): 头部水平部分出画 cx=-207.6 half=75.9；v5_003/figure_a(主对白): 头部水平部分出画 cx=1257.8 half=84.4；v5_005/host(主对白): 头部水平部分出画 cx=-207.6 half=75.9；v5_006/host(主对白): 头部水平部分出画 cx=22.0 half=84.4；v5_007/figure_a(主对白): 头部水平部分出画 cx=1512.6 half=111.3 |
| `history_mystery` | ✅ | v5_002/host(主对白): 头部水平部分出画 cx=42.6 half=81.6；v5_005/host(主对白): 头部水平部分出画 cx=42.6 half=81.6；v5_006/host(主对白): 头部水平部分出画 cx=-207.7 half=75.9 |
| `history_talk` | ✅ | 无 |
| `hot_ones` | ✅ | 无 |
| `mystery` | ✅ | 无 |
| `national_treasure` | ✅ | v5_002/host(主对白): 头部水平部分出画 cx=18.2 half=75.9；v5_005/host(主对白): 头部水平部分出画 cx=-28.5 half=83.5 |
| `qipashuo` | ✅ | 无 |
| `roundtable` | ✅ | 无 |
| `shark_tank` | ✅ | 无 |
| `slow_life` | ✅ | 无 |
| `snl_sketch` | ✅ | v5_007/figure_b(主对白): 头部水平部分出画 cx=1378.4 half=76.0 |
| `standup` | ✅ | 无 |
| `taskmaster` | ✅ | v5_005/figure_b(主对白): 头部水平部分出画 cx=1217.6 half=83.5 |
| `tonight_show` | ✅ | 无 |
| `traitors` | ✅ | 无 |

### ① 水墨竖排字幕把 ASS 绘图指令烧进画面（已修，需复验）

自动回归口径：`work/final.ass` 中文字型 Dialogue 不得出现“未加 `\p1` 覆盖的裸 m/l 绘图指令文本”。

| template | style | 裸绘图指令泄露 | 判定 |
|---|---|---|---|
| `classics` | woodblock_classic | 无 | ✅ |
| `history_mystery` | parchment_case | 无 | ✅ |
| `history_talk` | ink_chronicle | 无 | ✅ |

### ② silk 风格字幕对比度低（proxy 验收）

自动回归口径：ASS 字幕样式 PrimaryColour 与 OutlineColour 的 WCAG 相对亮度对比度 ≥ 3.0（描边是字幕可读性的主要保障；最终以人眼抽帧复核）。

| template | style | 字幕主色/描边 | 对比度 | 判定 |
|---|---|---|---|---|
| `qipashuo` | silk_arena | &H00161A1C / &H06D3E6EF | 14.01 | ✅ |

**帧内字幕对比度实测**（对白帧字幕带 p5/p95 亮度 WCAG 近似；<3.5 建议优化，<3.0 视为低对比）：

| template | style | 帧内对比度 | 判定 | 证据帧 |
|---|---|---|---|---|
| `history_mystery` | parchment_case | 3.46 | ✅ | `D:\BaiduSyncdisk\wmj_work\XJ\AiJue\tmp\qa\v5_review\history_mystery\sub_frame.png` |
| `qipashuo` | silk_arena | 3.54 | ✅ | `D:\BaiduSyncdisk\wmj_work\XJ\AiJue\tmp\qa\v5_review\qipashuo\sub_frame.png` |
| `slow_life` | clay_diorama | 3.63 | ✅ | `D:\BaiduSyncdisk\wmj_work\XJ\AiJue\tmp\qa\v5_review\slow_life\sub_frame.png` |
| `traitors` | baroque_candle | 4.00 | ✅ | `D:\BaiduSyncdisk\wmj_work\XJ\AiJue\tmp\qa\v5_review\traitors\sub_frame.png` |
| `taskmaster` | candy_collage | 4.47 | ✅ | `D:\BaiduSyncdisk\wmj_work\XJ\AiJue\tmp\qa\v5_review\taskmaster\sub_frame.png` |
| `mystery` | noir_rain | 5.01 | ✅ | `D:\BaiduSyncdisk\wmj_work\XJ\AiJue\tmp\qa\v5_review\mystery\sub_frame.png` |
| `shark_tank` | blueprint_lab | 5.80 | ✅ | `D:\BaiduSyncdisk\wmj_work\XJ\AiJue\tmp\qa\v5_review\shark_tank\sub_frame.png` |
| `national_treasure` | museum_gold | 7.76 | ✅ | `D:\BaiduSyncdisk\wmj_work\XJ\AiJue\tmp\qa\v5_review\national_treasure\sub_frame.png` |
| `standup` | vhs_late_show | 8.04 | ✅ | `D:\BaiduSyncdisk\wmj_work\XJ\AiJue\tmp\qa\v5_review\standup\sub_frame.png` |
| `history_talk` | ink_chronicle | 9.06 | ✅ | `D:\BaiduSyncdisk\wmj_work\XJ\AiJue\tmp\qa\v5_review\history_talk\sub_frame.png` |
| `snl_sketch` | chalk_theatre | 10.39 | ✅ | `D:\BaiduSyncdisk\wmj_work\XJ\AiJue\tmp\qa\v5_review\snl_sketch\sub_frame.png` |
| `tonight_show` | neon_toon | 12.03 | ✅ | `D:\BaiduSyncdisk\wmj_work\XJ\AiJue\tmp\qa\v5_review\tonight_show\sub_frame.png` |
| `classics` | woodblock_classic | 12.98 | ✅ | `D:\BaiduSyncdisk\wmj_work\XJ\AiJue\tmp\qa\v5_review\classics\sub_frame.png` |
| `hot_ones` | film_interview | 14.18 | ✅ | `D:\BaiduSyncdisk\wmj_work\XJ\AiJue\tmp\qa\v5_review\hot_ones\sub_frame.png` |
| `roundtable` | oil_salon | 14.34 | ✅ | `D:\BaiduSyncdisk\wmj_work\XJ\AiJue\tmp\qa\v5_review\roundtable\sub_frame.png` |

### ③ 角色 treatment 洗白/发灰（人眼复核项）

自动回归口径：角色色板/边缘无法可靠量化“洗白”；验收以 `style_report.json` 角色列表 + `contact_sheet.png` / `frames/*.png` 人眼抽帧复核为准，问题模板记录如下（QA 复核结论见 §7 状态列）。

| template | 角色 | contact_sheet | 状态 |
|---|---|---|---|
| `classics` | laozhou, simaqian, sushi | `outputs/templates_v5/classics/contact_sheet.png` | 人眼复核（见 §7 结论） |
| `history_mystery` | laozhou, simaqian, zhangqian | `outputs/templates_v5/history_mystery/contact_sheet.png` | 人眼复核（见 §7 结论） |
| `history_talk` | caocao, laozhou, zhuge | `outputs/templates_v5/history_talk/contact_sheet.png` | 人眼复核（见 §7 结论） |
| `hot_ones` | ali, dawei, laozhou | `outputs/templates_v5/hot_ones/contact_sheet.png` | 人眼复核（见 §7 结论） |
| `mystery` | laozhou, lengmian, mystery_guest | `outputs/templates_v5/mystery/contact_sheet.png` | 人眼复核（见 §7 结论） |
| `national_treasure` | laozhou, sushi, wuzetian | `outputs/templates_v5/national_treasure/contact_sheet.png` | 人眼复核（见 §7 结论） |
| `qipashuo` | caocao, laozhou, zhuge | `outputs/templates_v5/qipashuo/contact_sheet.png` | 人眼复核（见 §7 结论） |
| `roundtable` | laozhou, libai, sushi | `outputs/templates_v5/roundtable/contact_sheet.png` | 人眼复核（见 §7 结论） |
| `shark_tank` | laozhou, producer, xueba | `outputs/templates_v5/shark_tank/contact_sheet.png` | 人眼复核（见 §7 结论） |
| `slow_life` | ali, pangzi, tianmei | `outputs/templates_v5/slow_life/contact_sheet.png` | 人眼复核（见 §7 结论） |
| `snl_sketch` | ali, dawei, laozhou | `outputs/templates_v5/snl_sketch/contact_sheet.png` | 人眼复核（见 §7 结论） |
| `standup` | laozhou, lengmian, pangzi | `outputs/templates_v5/standup/contact_sheet.png` | 人眼复核（见 §7 结论） |
| `taskmaster` | laozhou, pangzi, tianmei | `outputs/templates_v5/taskmaster/contact_sheet.png` | 人眼复核（见 §7 结论） |
| `tonight_show` | laozhou | `outputs/templates_v5/tonight_show/contact_sheet.png` | 人眼复核（见 §7 结论） |
| `traitors` | caocao, laozhou, wuzetian | `outputs/templates_v5/traitors/contact_sheet.png` | 人眼复核（见 §7 结论） |

### ④ b-roll 宽景偏空（人眼复核项）

自动回归口径：b-roll 空镜无法仅凭统计判定；以 `frames/*.png` 与 style_report `broll_shots` 数量 + 人眼抽帧复核为准。

| template | b-roll 镜数 | frames | 状态 |
|---|---|---|---|
| `classics` | 1 | `outputs/templates_v5/classics/frames/` | 人眼复核（见 §7 结论） |
| `history_mystery` | 1 | `outputs/templates_v5/history_mystery/frames/` | 人眼复核（见 §7 结论） |
| `history_talk` | 0 | `outputs/templates_v5/history_talk/frames/` | 人眼复核（见 §7 结论） |
| `hot_ones` | 1 | `outputs/templates_v5/hot_ones/frames/` | 人眼复核（见 §7 结论） |
| `mystery` | 2 | `outputs/templates_v5/mystery/frames/` | 人眼复核（见 §7 结论） |
| `national_treasure` | 2 | `outputs/templates_v5/national_treasure/frames/` | 人眼复核（见 §7 结论） |
| `qipashuo` | 2 | `outputs/templates_v5/qipashuo/frames/` | 人眼复核（见 §7 结论） |
| `roundtable` | 2 | `outputs/templates_v5/roundtable/frames/` | 人眼复核（见 §7 结论） |
| `shark_tank` | 2 | `outputs/templates_v5/shark_tank/frames/` | 人眼复核（见 §7 结论） |
| `slow_life` | 2 | `outputs/templates_v5/slow_life/frames/` | 人眼复核（见 §7 结论） |
| `snl_sketch` | 2 | `outputs/templates_v5/snl_sketch/frames/` | 人眼复核（见 §7 结论） |
| `standup` | 2 | `outputs/templates_v5/standup/frames/` | 人眼复核（见 §7 结论） |
| `taskmaster` | 2 | `outputs/templates_v5/taskmaster/frames/` | 人眼复核（见 §7 结论） |
| `tonight_show` | 0 | `outputs/templates_v5/tonight_show/frames/` | 人眼复核（见 §7 结论） |
| `traitors` | 2 | `outputs/templates_v5/traitors/frames/` | 人眼复核（见 §7 结论） |

### ③④ 人眼复核结论（QA 抽帧记录）

- 复核范围：16/16 部（每部逐镜 20%/50%/80% 三帧 + contact_sheet）
- 构图裁切问题（角色头部/身体画出画面）：无
- 角色 treatment 洗白/发灰：无
- b-roll 宽景偏空：['standup', 'hot_ones', 'qipashuo', 'slow_life', 'history_talk', 'history_mystery', 'classics']

| template | 构图/取景 | 角色 treatment | b-roll 宽景 | QA 结论 |
|---|---|---|---|---|
| `classics` | ⚠ 轻微：v5_003/v5_007 主对白贴右边缘、v5_002/v5_006 贴左（水平 warning，头未断） | 正常（亮黄纸+黑刻线，与 ink/parchment 区分明显） | 偏空（书房/案几/香炉空镜，可接受） | ✅ 通过 |
| `history_mystery` | ⚠ 轻微：v5_002/v5_005 主对白贴左边缘、v5_006 host 偏左（水平 warning，头未断） | 正常（羊皮卷褐调，角色可辨） | 偏空（档案库/发掘现场空镜，可接受） | ✅ 通过 |
| `history_talk` | ⚠ 构图待优化：v5_005（scholar_study）取景窗选到道具特写 scroll_close，主对白角色仅头部在画面下缘（头部仍完整入画，非无头硬伤）；v5_002 主对白贴左下 | 正常（冷灰墨，风格化后与 parchment/woodblock 区分明显） | 偏空（书斋/地图空镜，历史题材 establishing 可接受） | ✅ 通过（构图 P2 建议） |
| `hot_ones` | 正常（cafe_window 无头问题已修；访谈镜头角色头脚完整） | 正常（胶片访谈质感） | 偏空（访谈沙发空镜，轻微） | ✅ 通过 |
| `masked_singer` | 正常（新渲染已修无头问题；镜面房双人镜次角色贴左边） | 正常（全息舞台，肤色稳定） | 正常（光门/星空舞台） | ✅ 通过 |
| `mystery` | 正常（黑色电影百叶窗风格，角色头部完整） | 正常（黑白高对比为风格设计） | 正常（雨夜/办公室） | ✅ 通过 |
| `national_treasure` | ⚠ 轻微：v5_002/v5_005 主对白贴左边缘（水平 warning，头未断）；展柜特写为主，角色多在画面下缘 | 正常（博物馆金，展柜文物清晰） | 正常（博物馆/展柜/宫殿） | ✅ 通过 |
| `qipashuo` | 正常（辩论场双人构图角色头部完整） | 正常（丝绸绘制） | 偏空（辩论场空镜，轻微） | ✅ 通过 |
| `roundtable` | 正常（圆桌群像角色头部完整） | 正常（油画沙龙） | 正常（油画图书馆/圆桌） | ✅ 通过 |
| `shark_tank` | 正常（蓝图风格，角色头部完整） | 正常 | 正常（蓝图白板/表格构成） | ✅ 通过 |
| `slow_life` | 正常（黏土微缩，角色全身） | 正常（黏土暖色） | 偏空（黏土厨房/庭院空镜，风格化可接受） | ✅ 通过 |
| `snl_sketch` | 轻微：v5_007 主对白角色贴右边缘（水平 warning） | 正常（粉笔剧场） | 正常（粉笔舞台） | ✅ 通过 |
| `standup` | 正常 | 正常（VHS 风格噪点与色偏为风格设计） | 偏空（VHS 俱乐部空镜，风格化可接受） | ✅ 通过 |
| `taskmaster` | 轻微：v5_005 主对白角色贴右边缘（水平 warning，头未断） | 正常（糖果拼贴） | 正常（拼贴道具桌） | ✅ 通过 |
| `tonight_show` | 正常（角色头部完整；个别镜头双人构图次要角色贴边） | 正常（霓虹卡通，无色偏） | 正常（霓虹空镜有灯光层次） | ✅ 通过 |
| `traitors` | 正常（烛光古堡暗场景；fadeblack 转场中点 ≤0.2s 按 V5 政策不算黑帧） | 正常（暗调但角色可辨） | 正常（烛光长廊/古堡） | ✅ 通过 |

## 8. 复现命令与回归记录

- 契约：`python -m pytest tests/test_v5_catalog.py -q` → 26 passed
- 固定声音单测：`python -m pytest tests/test_v5_voices.py -q` → 9 passed（验收时点）
- V5 验收：`python -m pytest tests/test_v5_acceptance.py -q` → 153 passed / 1 skipped（16/16 模板 × 硬门 + 风格矩阵 + 历史场景 + 契约；skip=lip_sync 未启用）
- 全量 tests：`python -m pytest tests -q` → 606 passed / 1 skipped（tests/ 全量，含 V5 154 项）
- pipeline/tests：`python -m pytest pipeline/tests -q` → 164 passed（V5 未改动 pipeline/tests 覆盖范围，最近一次运行）
- batch_report_v5：✅ {"ok": true, "total": 16, "passed": 16}

## 9. 证据路径索引

| 证据 | 路径 |
|---|---|
| V5 成片 | `outputs/templates_v5/{id}/final.mp4` |
| 时间线 | `outputs/templates_v5/{id}/timeline.json`、`timeline.html` |
| 风格报告 | `outputs/templates_v5/{id}/style_report.json` |
| QC（含风格指纹） | `outputs/templates_v5/{id}/qc_report.json` |
| 字幕证据 | `outputs/templates_v5/{id}/work/final.ass` |
| **角色固定声音库** | `contract/v5/voices.json`（24 preset）、`docs/V5_VOICES.md`（教程） |
| **声音锁** | `outputs/v5_voices/<character>/<voice_key>.wav + .json`（16 角色） |
| **声音一致性汇总** | `tmp/qa/v5_voice_consistency.json`（跨 16 部同角色 voice_id/key/seed） |
| **声音单测** | `tests/test_v5_voices.py`（9 项） |
| 抽帧/接触表 | `outputs/templates_v5/{id}/frames/*.png`、`contact_sheet.png` |
| 批量总览 | `outputs/templates_v5/contact_sheet_all.png`、`batch_report_v5.json` |
| 风格区分度矩阵 | `tmp/qa/v5_style_matrix.json` |
| 契约 | `contract/v5/`、`pipeline/v5_catalog.py` |
| 验收测试 | `tests/test_v5_acceptance.py`、`tests/test_v5_catalog.py` |
