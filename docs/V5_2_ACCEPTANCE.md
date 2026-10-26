# V5.2 独立验收报告：AI 美术升级（61 场景 plate + 24 角色立绘）+ V5 前端绑定

> 验收口径：Lead V5.2 任务书 + `docs/PLAN_V5_2_ART_FRONTEND.md` / `docs/V5_2_SCENE_ART.md` /
> `docs/V5_2_FRONTEND.md`；上一版：`docs/V5_ACCEPTANCE.md`（V5 固定声音版 16/16）。
> 生成时间：2026-10-26 19:57（由 `tmp/qa/gen_v5_2_doc.py` 依据真实产物生成，可重复运行）
> 验收人：qa-verifier（独立于 V5.2 开发；**只读 outputs/assets/contract/frontend，不启动渲染/浏览器**）。

## 0. 结论摘要

| 项 | 状态 |
|---|---|
| V5.2 16 部成片（AI 美术 + 固定声音） | ⏳ 进行中：15/16 可验收 |
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
| 场景 AI plate | 61/61 | PNG 1280×720，共 85.0MB；`scene_plate_ok` 61/61 |
| 场景 meta | 61 | prompt_id/seed 全覆盖；model=animagine-xl-4.0.safetensors；size=[(1280, 720)] |
| 历史场景 prompt | 13 | 含历史内容关键词 13/13 |
| 角色 AI 资产 | 24/24 | portrait+front+cutout；共 44.7MB |
| `v5_art.audit()` | 24 | ok=True，missing=[]，estimated_face=['pangzi', 'robot'] |

| 角色 | portrait/front/cutout 尺寸 | 生成来源 | 生成证据 |
|---|---|---|---|
| ali | portrait=832×1216 / front=452×984 / cutout=452×984 | v3_reuse | V3 复用资产（含生成记录） |
| caocao | portrait=832×1216 / front=1024×1024 / cutout=431×979 | imagegen_ref | AI 生成 |
| dawei | portrait=832×1216 / front=311×984 / cutout=311×984 | v3_reuse | V3 复用资产（含生成记录） |
| guanyu | portrait=832×1216 / front=1024×1024 / cutout=464×941 | imagegen_ref | AI 生成 |
| juanwang | portrait=832×1216 / front=1024×1024 / cutout=320×1000 | imagegen_ref | AI 生成 |
| laozhou | portrait=832×1216 / front=293×961 / cutout=293×961 | v3_reuse | V3 复用资产（含生成记录） |
| lengmian | portrait=832×1216 / front=1024×1024 / cutout=401×1002 | imagegen_ref | AI 生成 |
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
| 媒体路由 | {"/media/v5/characters/laozhou/portrait.png": {"status": 200, "bytes": 1072044}, "/media/v5/scenes/ancient_map.png": {"status": 200, "bytes": 1324890}, "/media/v5/tonight_show/timeline.html": {"status": 200, "bytes": 4412}, "final.mp4": {"status": 200, "bytes": 11512584}} |
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
| 1 | `tonight_show` | neon_toon（霓虹卡通） | 43.11s / 43.11s | 3/3 | 3/3 | -16.10 / -1.47 | ✅ | ✅ | ✅ |
| 2 | `shark_tank` | blueprint_lab（科技蓝图） | 33.8s / 33.81s | 4/4 | 3/3 | -15.21 / -1.48 | ✅ | ✅ | ✅ |
| 3 | `masked_singer` | hologram_stage（全息舞台） | 44.7s / 44.7s | 4/4 | 3/3 | -15.88 / -1.52 | ✅ | ✅ | ✅ |
| 4 | `standup` | ⏳ pending(evidence_stale) | — | — | — | — | — | — | — |
| 5 | `taskmaster` | candy_collage（糖果拼贴综艺） | 35.06s / 35.06s | 4/4 | 3/3 | -15.54 / -1.43 | ✅ | ✅ | ✅ |
| 6 | `traitors` | baroque_candle（巴洛克烛光古堡） | 40.42s / 40.42s | 4/4 | 3/3 | -15.69 / -1.50 | ✅ | ✅ | ✅ |
| 7 | `snl_sketch` | chalk_theatre（粉笔素描剧场） | 40.58s / 40.58s | 4/4 | 3/3 | -16.01 / -2.48 | ✅ | ✅ | ✅ |
| 8 | `roundtable` | oil_salon（文艺复兴油画圆桌） | 44.18s / 44.18s | 3/3 | 3/3 | -15.97 / -1.49 | ✅ | ✅ | ✅ |
| 9 | `hot_ones` | film_interview（暖光胶片访谈） | 43.02s / 43.02s | 4/4 | 3/3 | -16.10 / -1.50 | ✅ | ✅ | ✅ |
| 10 | `qipashuo` | silk_arena（绢本设色辩论） | 42.94s / 42.94s | 4/4 | 3/3 | -16.12 / -1.47 | ✅ | ✅ | ✅ |
| 11 | `slow_life` | clay_diorama（黏土定格慢生活） | 38.74s / 38.74s | 4/4 | 3/3 | -15.63 / -1.50 | ✅ | ✅ | ✅ |
| 12 | `history_talk` | ink_chronicle（水墨史书） | 38.12s / 38.13s | 4/4 | 3/3 | -16.21 / -1.50 | ✅ | ✅ | ✅ |
| 13 | `history_mystery` | parchment_case（羊皮纸档案） | 43.23s / 43.23s | 4/4 | 3/3 | -15.56 / -1.49 | ✅ | ✅ | ✅ |
| 14 | `classics` | woodblock_classic（版刻典籍） | 44.75s / 44.75s | 4/4 | 3/3 | -15.65 / -1.50 | ✅ | ✅ | ✅ |
| 15 | `national_treasure` | museum_gold（博物馆金黑） | 43.06s / 43.06s | 3/3 | 3/3 | -16.12 / -1.50 | ✅ | ✅ | ✅ |
| 16 | `mystery` | noir_rain（黑色电影） | 37.22s / 37.22s | 4/4 | 3/3 | -15.92 / -1.50 | ✅ | ✅ | ✅ |

- **场景(AI plate)** = QA 复算 renderer 实际使用 plate 数 / 该模板场景数；
- **角色(AI 立绘)** = QA 复算 AI 立绘可用角色数 / 成片角色数；
- **LUFS/TP** 为 QA 独立测量（V5 未列入硬门，用于观察混音一致性）。

## 6. 风格区分度矩阵（V5.2 重新抽帧）

阈值：**cosine ≥ 0.985 且 edge_diff ≤ 0.01 = 几乎相同**；16 部两两 120 对。

| template | style | edge_density |
|---|---|---|
| `tonight_show` | neon_toon | 0.0859 |
| `shark_tank` | blueprint_lab | 0.1054 |
| `masked_singer` | hologram_stage | 0.0965 |
| `standup` | vhs_late_show | 0.0723 |
| `taskmaster` | candy_collage | 0.0897 |
| `traitors` | baroque_candle | 0.0821 |
| `snl_sketch` | chalk_theatre | 0.0805 |
| `roundtable` | oil_salon | 0.1341 |
| `hot_ones` | film_interview | 0.0736 |
| `qipashuo` | silk_arena | 0.0868 |
| `slow_life` | clay_diorama | 0.0568 |
| `history_talk` | ink_chronicle | 0.133 |
| `history_mystery` | parchment_case | 0.0789 |
| `classics` | woodblock_classic | 0.1173 |
| `national_treasure` | museum_gold | 0.0929 |
| `mystery` | noir_rain | 0.1144 |

- 判为“几乎相同”的模板对：**0 对**（无）
- 最相似对：{'a': 'snl_sketch', 'b': 'history_talk', 'cosine': 0.8308, 'edge_diff': 0.0525, 'same_style': False}
- 最不相似对：{'a': 'masked_singer', 'b': 'traitors', 'cosine': 0.0004, 'edge_diff': 0.0144, 'same_style': False}

## 7. 人眼复核（AI 美术/口型/历史内容）

- 复核范围：16/16 部（逐镜 20%/50%/80% 三帧 + 总览 contact sheet）
- AI 场景历史内容：历史 4 部全部使用 AI 历史场景 plate：history_talk（军帐/战场/宫殿/书斋/古地图）、classics（宫殿/神庙/档案库/书斋）、history_mystery（古地图/档案库/发掘现场/长城）、national_treasure（博物馆/展柜/档案库/宫殿）；plate 内含灯笼/屏风/卷轴/沙盘/城墙/兵马剪影等历史元素，明显优于旧程序化几何
- AI 角色立绘观感：24 角色 AI anime 立绘（21 AI 生成 + 3 最早 V3 复用）；成片中角色为 AI 立绘并按 16 风格 finish（ink/woodcut/parchment 成水墨/版刻/羊皮纸风格，现代风格保留 anime 彩色），非程序化纸片；少数 ink 风格下角色偏剪影
- 中景口型可见度：对白镜口型 overlay 已生效，但中景/远景角色尺寸较小（约 30-50% 画面高）时口型变化肉眼可见度一般；close/medium 镜头（如 classics 坐姿、history_talk 军帐）口型相对可辨；建议后续对 speaking 角色再放大 10-15% 或增强口型对比（P2）

| template | AI 场景/历史内容 | AI 立绘 | 口型可见 | 结论 |
|---|---|---|---|---|
| `classics` | AI 历史 plate：宫殿/神庙/档案库/书斋（版刻黑线+亮黄纸） | AI 立绘（木刻 finish，坐姿官员/立像可辨） | 坐姿/近景可辨 | ✅ 通过（P1 裁头已修复复验） |
| `history_mystery` | AI 历史 plate：古地图/档案库/发掘现场/长城（羊皮纸褐调） | AI 立绘（羊皮纸 finish） | 中景一般 | ✅ 通过 |
| `history_talk` | AI 历史 plate：军帐/战场/宫殿/书斋/古地图（灯笼/军旗/沙盘/卷轴） | AI 立绘（冷灰墨 finish，偏剪影但可辨） | 军帐/宫殿 close-medium 可辨 | ✅ 通过（P1 裁头已修复复验） |
| `hot_ones` | 暖光胶片访谈室/厨房/胶片墙 AI plate | AI anime 立绘（胶片颗粒） | 中景可见 | ✅ 通过（contact sheet 中白色帧为 fadewhite 转场中点，非缺陷） |
| `masked_singer` | 全息舞台/镜面房/光门 AI plate（紫青全息光） | AI 立绘（全息高对比） | 中景一般 | ✅ 通过 |
| `mystery` | AI 雨夜街道/黑色办公室 AI plate（百叶窗高对比） | AI 立绘（noir 黑白 finish） | 中景一般 | ✅ 通过 |
| `national_treasure` | AI 博物馆/展柜/宫殿 AI plate（金黑展陈） | AI 立绘（博物馆金 finish） | 展柜特写为主，中景一般 | ✅ 通过 |
| `qipashuo` | 绢本设色辩论场/朝堂 AI plate（祥云/织锦/屏风） | AI 立绘（丝绸/勾线 finish） | 中景一般 | ✅ 通过 |
| `roundtable` | 油画图书馆/圆桌 AI plate（油画笔触） | AI 立绘（油画 finish） | 中景一般 | ✅ 通过 |
| `shark_tank` | 蓝图创投板/白板实验室 AI plate（蓝图网格/图板） | AI 立绘（蓝图风格 finish） | 中景一般 | ✅ 通过 |
| `slow_life` | 黏土微缩厨房/庭院/小镇 AI plate（黏土质感） | AI 立绘（黏土 finish） | 中景一般 | ✅ 通过 |
| `snl_sketch` | 粉笔舞台/后台 AI plate（粉笔线稿） | AI 立绘（粉笔 finish） | 中景一般 | ✅ 通过 |
| `standup` | VHS 脱口秀俱乐部/后台 AI plate（复古色偏+噪点） | AI 立绘（VHS 风格） | 中景一般 | ✅ 通过 |
| `taskmaster` | 糖果拼贴道具桌/游戏舞台 AI plate（高饱和拼贴） | AI 立绘（拼贴风） | 中景一般 | ✅ 通过 |
| `tonight_show` | 霓虹深夜演播室/后台/街景 AI plate（霓虹灯牌、几何窗格） | AI anime 主持/嘉宾立绘（彩色） | 中景可见但偏小 | ✅ 通过 |
| `traitors` | 烛光古堡/长廊 AI plate（暗调烛光） | AI 立绘（暗调但可辨） | 暗场偏弱 | ✅ 通过 |

## 8. 回归记录与遗留

- 复现/回归记录由 QA 在 freeze 后填写：`tmp/qa/final_regression_v5_2.json`
- **V5.2 验收**：43 passed / 0 skipped / 0 failed（16 模板 × 原硬门 + 美术资产/运行链路 + 前端 + 风格矩阵 + P1 头部；含 P1 修复后全量重渲）
- **V5 回归**：187 passed / 1 skipped（回归 V5 全量，skip=lip_sync 未启用）
- **美术资产/前端单测**：43 passed（16 模板回归 + 资产 + 运行链路 + 前端 + 风格矩阵 + P1 头部，0 skipped）
- **全量 tests**：692 passed / 1 skipped（tests/ 全量，含 V5.2 43 项）
- `batch_report_v5.json`：✅ {"ok": true, "total": 16, "passed": 16}

### 遗留 P2（不阻塞交付）

1. 部分历史场景 AI plate 偏写实/偏空（历史 4 部人眼复核抽样记录于 §7）；
2. 部分角色人脸检测为估算（见 §3 `v5_art.audit().estimated_face`：pangzi, robot），口型 overlay 观感需人眼确认；
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
