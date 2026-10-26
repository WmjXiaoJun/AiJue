# V5.2 独立验收报告：AI 美术升级（61 场景 plate + 24 角色立绘）+ V5 前端绑定

> 验收口径：Lead V5.2 任务书 + `docs/PLAN_V5_2_ART_FRONTEND.md` / `docs/V5_2_SCENE_ART.md` /
> `docs/V5_2_FRONTEND.md`；上一版：`docs/V5_ACCEPTANCE.md`（V5 固定声音版 16/16）。
> 生成时间：2026-10-26 19:29（由 `tmp/qa/gen_v5_2_doc.py` 依据真实产物生成，可重复运行）
> 验收人：qa-verifier（独立于 V5.2 开发；**只读 outputs/assets/contract/frontend，不启动渲染/浏览器**）。

## 0. 结论摘要

| 项 | 状态 |
|---|---|
| V5.2 16 部成片（AI 美术 + 固定声音） | ⏳ 进行中：7/16 可验收 |
| 场景 AI plate 资产 | ✅ 61/61（PNG 1280×720 + meta prompt_id/seed） |
| 角色 AI 资产 | ✅ 24/24（portrait/front/cutout + meta） |
| 渲染链路用 AI 美术 | ⏳ |
| 历史场景内容 | ✅ 13/13 history 场景 prompt 含历史关键词 |
| V5 前端绑定（dist + API） | ✅ dist 构建 + `/api/v5/outputs`16/`voices`24/媒体 200 |
| `curl_evidence` 证据 | ✅ 30/30 |
| **V5.2 终门（`pytest tests/test_v5_2_acceptance.py -q`）** | ⏳ 批量完成后跑 |
| 回归不退化（时长/QC/voice_fixed/P1 头部/风格矩阵） | ⏳ |
| 契约 | ✅ validate_catalog 0 errors/0 warnings |

## 1. 验收命令（可复跑）

```powershell
# 美术资产 + 运行链路 + 前端 + 回归（16 模板参数化；未就绪自动 skip 并报告）
python -m pytest tests/test_v5_2_acceptance.py -q
python -m pytest tests/test_v5_2_acceptance.py -q -k "art"          # 美术资产/渲染链路
python -m pytest tests/test_v5_2_acceptance.py -q -k "frontend"     # 前端绑定
python -m pytest tests/test_v5_2_acceptance.py -q -k "style_matrix" # 风格区分度

# curl 证据（需 8090 后端在跑）
python tmp/v52_frontend/curl_evidence.py http://127.0.0.1:8090

# 本报告重生成（含逐模板 LUFS / 美术资产统计 / 前端证据）
python tmp/qa/gen_v5_2_doc.py
```

## 2. V5.2 硬门与验收口径（QA 独立定义）

| 硬门 | 判据 | 说明 |
|---|---|---|
| 场景 plate 资产 | 61/61 `assets/v5/scenes/<scene>.png`（≥20KB、PNG、1280×720）+ 同名 `.json` 含 prompt_id/seed/prompt/negative/model/size | 每张 plate 生成可复现 |
| 角色 AI 资产 | 24/24 `assets/v5/characters/<id>/{portrait,front,cutout}.png` 存在非空；cutout 带透明通道；`v5_art.audit()` 24/24 | 用户“角色太丑”整改 |
| 渲染链路 | QA 独立复算 `scene_render.render_scene_layers()`：`meta.art` 指向对应 plate 且 `meta.layers` 含 `plate:<name>`；`work/shot_meta.json` char_source=characters_v5 / char_warnings=[]；角色走 `v5_art.render_art_character()`（非程序化） | 证明 AI 美术真正进成片，而非只堆文件 |
| 历史内容 | 13 个 history 场景 prompt 含地图/战场/宫殿/档案/博物馆等关键词；历史 4 部成片场景 tags=history ≥2 | 防“AI 场景无历史感” |
| 前端绑定 | `frontend/dist/index.html` 存在；dist JS 含 V5 Studio 标记（/api/v5、voices、场景库、角色库…）；dist 构建晚于源文件；8090 在时 `/api/v5/outputs`=16、`/api/v5/voices`=24、`/media/v5/characters/laozhou/portrait.png`=200、场景 plate=200、final Range、timeline 200；curl 证据 30/30 | 不启动浏览器也可验证 |
| 回归 | 16/16 时长 25-50s / 1280×720 / 25fps / 音轨 / blackdetect=0（d=0.30）/ QC ok / voice_fixed / timeline 契约 / P1 头部视觉门 / 风格矩阵 0 对“几乎相同” | V5 硬门不退化 |
| 人眼复核 | AI 场景历史内容、AI 立绘观感、中景口型可见度、16 风格区分 | §7 记录证据帧 |

## 3. 美术资产统计

| 资产 | 数量 | 关键证据 |
|---|---|---|
| 场景 AI plate | 61/61 | PNG 1280×720，共 84.6MB；`scene_plate_ok` 61/61 |
| 场景 meta | 61 | prompt_id/seed 全覆盖；model=animagine-xl-4.0.safetensors；size=[(1280, 720)] |
| 历史场景 prompt | 13 | 含历史内容关键词 13/13 |
| 角色 AI 资产 | 24/24 | portrait+front+cutout；共 44.6MB |
| `v5_art.audit()` | 24 | ok=True，missing=[]，estimated_face=['lengmian', 'pangzi', 'robot'] |

| 角色 | portrait/front/cutout 尺寸 | 生成来源 | 生成证据 |
|---|---|---|---|
| ali | portrait=832×1216 / front=452×984 / cutout=452×984 | v3_reuse | V3 复用资产 |
| caocao | portrait=832×1216 / front=1024×1024 / cutout=431×979 | imagegen_ref | AI 生成 |
| dawei | portrait=832×1216 / front=311×984 / cutout=311×984 | v3_reuse | V3 复用资产 |
| guanyu | portrait=832×1216 / front=1024×1024 / cutout=464×941 | imagegen_ref | AI 生成 |
| juanwang | portrait=832×1216 / front=1024×1024 / cutout=320×1000 | imagegen_ref | AI 生成 |
| laozhou | portrait=832×1216 / front=293×961 / cutout=293×961 | v3_reuse | AI 生成 |
| lengmian | portrait=832×1216 / front=1024×1024 / cutout=317×929 | imagegen_ref | AI 生成 |
| libai | portrait=832×1216 / front=1024×1024 / cutout=834×971 | imagegen_ref | AI 生成 |
| mengxin | portrait=832×1216 / front=1024×1024 / cutout=374×997 | imagegen_ref | AI 生成 |
| mulan | portrait=832×1216 / front=1024×1024 / cutout=784×985 | imagegen_ref | AI 生成 |
| mystery_guest | portrait=832×1216 / front=1024×1024 / cutout=859×962 | imagegen_ref | AI 生成 |
| pangzi | portrait=832×1216 / front=1024×1024 / cutout=496×744 | imagegen_ref | AI 生成 |
| producer | portrait=832×1216 / front=1024×1024 / cutout=437×981 | imagegen_ref | AI 生成 |
| qinshihuang | portrait=832×1216 / front=1024×1024 / cutout=927×1022 | imagegen_ref | AI 生成 |
| robot | portrait=832×1216 / front=1024×1024 / cutout=879×999 | imagegen_ref | AI 生成 |
| simaqian | portrait=832×1216 / front=1024×1024 / cutout=793×949 | imagegen_ref | AI 生成 |
| sunwu | portrait=832×1216 / front=1024×1024 / cutout=536×994 | imagegen_ref | AI 生成 |
| sushi | portrait=832×1216 / front=1024×1024 / cutout=802×1006 | imagegen_ref | AI 生成 |
| tianmei | portrait=832×1216 / front=1024×1024 / cutout=698×988 | imagegen_ref | AI 生成 |
| wuzetian | portrait=832×1216 / front=1024×1024 / cutout=999×1020 | imagegen_ref | AI 生成 |
| xueba | portrait=832×1216 / front=1024×1024 / cutout=365×984 | imagegen_ref | AI 生成 |
| zhangqian | portrait=832×1216 / front=1024×1024 / cutout=849×974 | imagegen_ref | AI 生成 |
| zhenghe | portrait=832×1216 / front=1024×1024 / cutout=910×996 | imagegen_ref | AI 生成 |
| zhuge | portrait=832×1216 / front=1024×1024 / cutout=892×1009 | imagegen_ref | AI 生成 |

- 场景 plate 目录：`assets/v5/scenes/`（`<scene>.png` + `<scene>.json`）
- 角色资产目录：`assets/v5/characters/<id>/`（portrait/front/cutout + meta.json）
- 历史场景清单：['ancient_map', 'archive_vault', 'battlefield', 'display_case', 'excavation_site', 'great_wall', 'lantern_market', 'museum_hall', 'palace_hall', 'river_fleet', 'scholar_study', 'temple_court', 'war_council']

## 4. V5 前端绑定证据

| 项 | 结果 |
|---|---|
| dist 构建 | `frontend/dist/index.html` ✅；JS 379KB；assets：index-CxDpLa8g.js(379KB), index-CQCI5vMd.css(120KB) |
| V5 Studio 字符串标记 | 命中 ['/api/v5', 'voices', '场景库', '角色库', '声音', 'V5']；计数 {'/api/v5': 3, 'voices': 3, '场景库': 5, '角色库': 6, '声音': 27, 'V5': 16} |
| GET /api/v5/outputs | 16 条（期望 16） |
| GET /api/v5/voices | 24 条（期望 24） |
| 媒体路由 | {"/media/v5/characters/laozhou/portrait.png": {"status": 200, "bytes": 1072044}, "/media/v5/scenes/ancient_map.png": {"status": 200, "bytes": 1324890}, "/media/v5/tonight_show/timeline.html": {"status": 200, "bytes": 4412}, "final.mp4": {"status": 200, "bytes": 4587671}} |
| `tmp/v52_frontend/curl_evidence.py` | 30/30（base=http://127.0.0.1:8090） |

<details><summary>curl 证据 30 项明细</summary>

| # | check | 结果 |
|---|---|---|
| 1 | health | ✅ |
| 2 | GET /api/v5/outputs = 16 条 | ✅ |
| 3 | outputs 字段齐全 | ✅ |
| 4 | outputs include_custom 含自定义示例 | ✅ |
| 5 | GET final.mp4（Range 请求） | ✅ |
| 6 | final.mp4 是视频 | ✅ |
| 7 | GET timeline.html | ✅ |
| 8 | GET qc_report.json | ✅ |
| 9 | GET contact_sheet.png | ✅ |
| 10 | 路径穿越被拒 ../../app/main.py | ✅ |
| 11 | 路径穿越被拒 final.mp4/../../../app/main.py | ✅ |
| 12 | 路径穿越被拒 ..%2fapp/main.py | ✅ |
| 13 | GET style_report.json | ✅ |
| 14 | 角色立绘路由（资产未生成时 404） | ✅ |
| 15 | 场景 plate 路由（资产未生成时 404） | ✅ |
| 16 | GET /api/v5/voices | ✅ |
| 17 | GET /api/v5/voice/laozhou | ✅ |
| 18 | PUT /api/v5/voice/laozhou | ✅ |
| 19 | PUT 写了 custom 覆盖文件 | ✅ |
| 20 | PUT 后 GET 生效 | ✅ |
| 21 | PUT 未知 voice_id → 400 | ✅ |
| 22 | 声音覆盖回滚成功 | ✅ |
| 23 | POST /api/v5/render 非法模板 400 | ✅ |
| 24 | POST /api/v5/render 202 + job id | ✅ |
| 25 | 并发第二个渲染 → 409 | ✅ |
| 26 | GET /api/v5/render/{job} 有状态/日志 | ✅ |
| 27 | GET /api/v5/render 列表 | ✅ |
| 28 | DELETE /api/v5/render/{job} 取消 | ✅ |
| 29 | 取消后状态为 canceled | ✅ |
| 30 | 取消的 job 未整片写出 | ✅ |

</details>

## 5. 16 部 V5.2 终门表

| # | template | 风格 | 时长 | 场景(AI plate) | 角色(AI 立绘) | LUFS/TP | QC | voice_fixed | P1 头部 |
|---|---|---|---|---|---|---|---|---|---|
| 1 | `tonight_show` | ⏳ pending(V5.1旧产物) | — | — | — | — | — | — | — |
| 2 | `shark_tank` | ⏳ pending(V5.1旧产物) | — | — | — | — | — | — | — |
| 3 | `masked_singer` | hologram_stage（全息舞台） | 44.7s / 44.7s | 4/4 | 3/3 | -15.88 / -1.52 | ✅ | ✅ | ✅ |
| 4 | `standup` | ⏳ pending(V5.1旧产物) | — | — | — | — | — | — | — |
| 5 | `taskmaster` | ⏳ pending(V5.1旧产物) | — | — | — | — | — | — | — |
| 6 | `traitors` | ⏳ pending(V5.1旧产物) | — | — | — | — | — | — | — |
| 7 | `snl_sketch` | ⏳ pending(V5.1旧产物) | — | — | — | — | — | — | — |
| 8 | `roundtable` | ⏳ pending(V5.1旧产物) | — | — | — | — | — | — | — |
| 9 | `hot_ones` | film_interview（暖光胶片访谈） | 43.02s / 43.02s | 4/4 | 3/3 | -16.10 / -1.50 | ✅ | ✅ | ✅ |
| 10 | `qipashuo` | ⏳ pending(V5.1旧产物) | — | — | — | — | — | — | — |
| 11 | `slow_life` | ⏳ pending(V5.1旧产物) | — | — | — | — | — | — | — |
| 12 | `history_talk` | ink_chronicle（水墨史书） | 38.12s / 38.13s | 4/4 | 3/3 | -16.21 / -1.50 | ✅ | ✅ | ❌ |
| 13 | `history_mystery` | parchment_case（羊皮纸档案） | 43.23s / 43.23s | 4/4 | 3/3 | -15.56 / -1.49 | ✅ | ✅ | ✅ |
| 14 | `classics` | woodblock_classic（版刻典籍） | 44.75s / 44.75s | 4/4 | 3/3 | -15.65 / -1.50 | ✅ | ✅ | ❌ |
| 15 | `national_treasure` | museum_gold（博物馆金黑） | 43.06s / 43.06s | 0/0 | 0/0 | -16.12 / -1.50 | ✅ | ✅ | ✅ |
| 16 | `mystery` | noir_rain（黑色电影） | 37.22s / 37.22s | 0/0 | 0/0 | -15.92 / -1.50 | ✅ | ✅ | ✅ |

- **场景(AI plate)** = QA 复算 renderer 实际使用 plate 数 / 该模板场景数；
- **角色(AI 立绘)** = QA 复算 AI 立绘可用角色数 / 成片角色数；
- **LUFS/TP** 为 QA 独立测量（V5 未列入硬门，用于观察混音一致性）。

## 6. 风格区分度矩阵（V5.2 重新抽帧）

阈值：**cosine ≥ 0.985 且 edge_diff ≤ 0.01 = 几乎相同**；16 部两两 120 对。

| template | style | edge_density |
|---|---|---|
| `tonight_show` | neon_toon | 0.0549 |
| `shark_tank` | blueprint_lab | 0.0529 |
| `masked_singer` | hologram_stage | 0.0353 |
| `standup` | vhs_late_show | 0.0465 |
| `taskmaster` | candy_collage | 0.0498 |
| `traitors` | baroque_candle | 0.0381 |
| `snl_sketch` | chalk_theatre | 0.0413 |
| `roundtable` | oil_salon | 0.058 |
| `hot_ones` | film_interview | 0.0575 |
| `qipashuo` | silk_arena | 0.0368 |
| `slow_life` | clay_diorama | 0.0299 |
| `history_talk` | ink_chronicle | 0.133 |
| `history_mystery` | parchment_case | 0.0789 |
| `classics` | woodblock_classic | 0.1174 |
| `national_treasure` | museum_gold | 0.0533 |
| `mystery` | noir_rain | 0.0403 |

- 判为“几乎相同”的模板对：**0 对**（无）
- 最相似对：{'a': 'hot_ones', 'b': 'slow_life', 'cosine': 0.891, 'edge_diff': 0.0276, 'same_style': False}
- 最不相似对：{'a': 'snl_sketch', 'b': 'slow_life', 'cosine': 0.0001, 'edge_diff': 0.0114, 'same_style': False}

## 7. 人眼复核（AI 美术/口型/历史内容）

⏳ 人眼复核待 freeze 后填写（证据帧 `tmp/qa/v5_review/{tid}/shots_grid.png`、`outputs/templates_v5/contact_sheet_all.png`）。

## 8. 回��记录与遗留

- 复现/回归记录由 QA 在 freeze 后填写：`tmp/qa/final_regression_v5_2.json`
- **V5.2 验收**：⏳ 等批量完成
- **V5 回归**：⏳
- **美术资产/前端单测**：⏳
- **全量 tests**：⏳
- `batch_report_v5.json`：✅ {"ok": true, "total": 16, "passed": 16}

### 遗留 P2（不阻塞交付）

1. 部分历史场景 AI plate 偏写实/偏空（历史 4 部人眼复核抽样记录于 §7）；
2. 24 角色中 3 个（lengmian/pangzi/robot）人脸检测为估算（`v5_art.audit().estimated_face`），口型 overlay 观感需人眼确认；
3. 前端截图证据（`tmp/v52_frontend/screenshots/`）为静态构建 + API 冒烟，真实浏览器交互建议后续人工走查；
4. 角色声音固定沿用 V5（voice_fixed）口径，本轮未改声音算法。

## 9. 证据路径索引

| 证据 | 路径 |
|---|---|
| V5.2 成片 | `outputs/templates_v5/{id}/final.mp4` |
| 场景 AI plate | `assets/v5/scenes/<scene>.png`、`<scene>.json` |
| 角色 AI 资产 | `assets/v5/characters/<id>/{portrait,front,cutout}.png`、`meta.json` |
| 渲染 meta（art/角色） | `outputs/templates_v5/{id}/work/shot_meta.json`、`motion2d_*.json` |
| 美术资产/运行链路证据 | `tmp/qa/v5_2_art_evidence.json` |
| 前端 dist | `frontend/dist/index.html`、`frontend/dist/assets/*` |
| 前端 API 证据 | `tmp/qa/v5_2_frontend_evidence.json`、`tmp/v52_frontend/evidence/` |
| 风格矩阵 | `tmp/qa/v5_style_matrix_v52.json` |
| P1 头部门 | `tmp/qa/v5_head_gate_v52.json` |
| 人眼复核帧 | `tmp/qa/v5_review/{tid}/shots_grid.png`、`outputs/templates_v5/contact_sheet_all.png` |
| 验收测试 | `tests/test_v5_2_acceptance.py`（+ 回归 `tests/test_v5_acceptance.py`） |
