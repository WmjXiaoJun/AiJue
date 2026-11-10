# PART_PIPELINE — 生产流水线 / 数据合同 / 测试 全貌审计

- 审计对象：AiJue 智能体工作台（`D:\BaiduSyncdisk\wmj_work\XJ\AiJue`）
- 审计时间：2026-11-05
- 方法：只读扫描 + 脚本统计（`glob`/`ast`/`json`/`grep '^\s*def test_'`），**未执行任何渲染、未修改任何源文件**
- 统计基准：文件系统实际状态（不是文档声称的状态）

## 0. 总量速览

| 维度 | 数量 |
|---|---|
| contract JSON 文件 | **353**（v5 **295** / v6 **17** / templates_v3 16 / formats 17 / characters 3 / 仓库根 5） |
| pipeline Python 模块 | **88** 个文件，**50,307** 行（核心 62 个 / 44,626 行；agents 9 个；pipeline/tests 17 个） |
| 顶测试套件 | **21** 文件、**343** 用例、10,381 行（`tests/`） |
| 内嵌测试套件 | **17** 文件、**185** 用例（`pipeline/tests/`） |
| 测试总计 | **38 文件 / 528 用例** |
| v5 模板合同 | **33**（内建 21 + custom 12） |
| v6 智能体工作流 | **16**（只覆盖内建 16，33 个模板中 17 个无 workflow） |
| 有 final.mp4 的成片目录 | **templates_3d 30 + templates_2d 9 + templates_v3 18 + templates_v5 1 + episodes 3 = 61** |
| outputs 体积 | ≈ **13.5 GB**（主要目录：templates_v5 5.2 GB + templates_3d 3.9 GB + templates_v3 3.4 GB + template_demos 1.2 GB） |

---

## 1. pipeline/ 模块与阶段归属

流水线的目标链路：**分镜 → 声音 → 剪辑 → 2D/H3 渲染 → 合成 → 分镜表 → QC → 发布**。
下表按阶段把 62 个核心模块归类（行数为实测值，不是估算）。

### 1.1 分镜 / 故事层（story）

| 模块 | 行 | 职责 | 关键入口 |
|---|---|---|---|
| `template_v3.py` | 762 | 模板即节目形态：舞台/站位/动作/镜头语言/H3 配置的加载与校验 | `load`, `load_file`, `load_presets` |
| `v5_story.py` | 240 | V5 模板 acts → 逐镜计划（场景/角色/机位/台词） | `build_plan`, `plan_duration`, `stage_targets` |
| `storyboard_v3.py` | 898 | 模板 + LLM 台本 → v3 shot 序列（兼容 render.py） | `build_assets`, `normalize_h3_mode` |
| `shot_grammar.py` | 269 | 镜头语法：景别/角度/高度/运动 → H3 prompt | `plan_for_shot`, `build_h3_prompt` |
| `action_library.py` | 539 | 动作库 → H3 四段式 prompt（动作/运镜/身份锚点/场景/声音） | `resolve_action`, `build_action_variants` |
| `editorial.py` | 625 | **剪辑档案层**：per-cut 景别/运镜/转场/J-L 参数化 | `load_editorial`, `build_edit_plan` |

### 1.2 声音层（voice / TTS）

| 模块 | 行 | 职责 |
|---|---|---|
| `tts_engine.py` | 122 | edge-tts 封装：mp3 + 词级 VTT，失败重试、静音兜底 |
| `tts_breeze.py` | 410 | Breeze TTS 2 本地客户端（角色化合成、GPU 让位、健康检查） |
| `voiced.py` | 454 | V4 语音编排：TTS + **语音驱动镜头时长**（无 atempo 变速） |
| `v5_voices.py` | 292 | 角色固定声音 + **声音锁**（reference audio 复现） |
| `v5_audio.py` | 422 | 风格化 BGM 程序化合成（synthwave/future_bass/guqin/pipa/bells，48k 立体声） |
| `mouth_sync.py` | 113 | 用 TTS 音频能量包络驱动口型（替代文本等分时间轴） |
| `lipsync.py` | 910 | MuseTalk 1.5 provider（vendor 在 `local_models/lipsync/MuseTalk`），人脸检测/推理 |

### 1.3 剪辑层（editorial / editing）

| 模块 | 行 | 职责 |
|---|---|---|
| `editing.py` | 645 | V5.4 剪辑执行：**每个剪辑点独立转场** + J/L cut 音频重叠 + 甩镜 |
| `timeline.py` | 191 | V4 声明式时间线（OpenFilm 式非破坏剪辑） |
| `edit_v4.py` | 231 | timeline.json → MP4 渲染 |
| `ass_builder.py` | 247 | 全局 ASS 字幕/花字轨道（时间轴 → 单轨 ASS） |

### 1.4 2D 渲染层

| 模块 | 行 | 职责 |
|---|---|---|
| `scene_render.py` | **4242** | V5 风格化场景渲染器：可组合视觉原语 → 分层 PIL 图像（最大模块） |
| `motion2d.py` | **3598** | V5 2D 分层动画渲染（相机/视差/角色分层/表情） |
| `v5_primitives_modern.py` | 916 | 现代风格 primitive A（neon_toon/film_interview/noir_rain…） |
| `v5_primitives_modern_b.py` | 1649 | 现代风格 primitive B（6 风格专用绘制原语） |
| `style_v5.py` | **3180** | V5 风格引擎：16 technique 后处理 + 调色/包装/字幕/片头片尾/转场 |
| `v5_art.py` | 2133 | V5.2 角色 AI 美术（SDXL 立绘 + 抠图 + 口型/眨眼 overlay） |
| `v5_scene_art.py` | 755 | V5.2 场景 AI 美术（Animagine 场景 plate，无人物） |
| `render_v5.py` | 1115 | **V5 主渲染引擎**：模板+风格+场景/角色库 → 成片（`render_shot_clips` / `assemble` / `run_qc`） |
| `render_v3.py` | 1140 | v3 渲染：storyboard → stage 首帧 → H3 每镜 → 剪辑/包装/混音 → QC |
| `render.py` | 1364 | 老 render：episode.json → 成片 mp4 |
| `mock_shot.py` | 934 | **mock 镜头**：ffmpeg lavfi 背景 + 立绘 + drawtext（占位实现） |
| `scenes.py` | 435 | 2D 绘景 2.5D 视差 + 全身角色分层 + 3D 片段合成 |

### 1.5 3D / H3 渲染层

| 模块 | 行 | 职责（版本） |
|---|---|---|
| `stage.py` | 1322 | 舞台首帧合成：plate + 多角色 cutout 站位 → H3 首帧/尾帧/卡片帧（v3） |
| `h3_comfy.py` | 775 | ComfyUI H3 客户端：04 fl2v / 05 ref2va 每镜提交 + 可追溯 meta（v3） |
| `h3_v5.py` | 198 | V5.5 H3 混合：hero 镜图生视频，输出 `outputs/templates_v5_hybrid/`（**不覆盖主成片**） |
| `dynamic.py` | 490 | 动态镜头（音驱动口型 / 首尾帧）生成与缓存 |
| `comfyui.py` | 135 | ComfyUI API 适配器（MiniMax H3 workflow） |
| `templates3d.py` | 650 | V8：16 个 V5 模板 3D 化样片（31–45s） |
| `templates3d_semantic.py` | 249 | V14：台词 → 英文画面描述（语义化 shot brief） |
| `templates3d_pipeline.py` | 172 | V14 三阶段：全量生成 → 统一口型 → 剪辑+海报 |
| `templates3d_relip.py` | 163 | V15：口型进生成阶段（对话镜改 ref2va 原生音驱动） |
| `templates3d_hq.py` | **882** | **V16 高清样片**：1024×576 T2VA + 1024×576 ref2va 原生口型 |
| `templates3d_hq_batch.py` | 68 | V16 全量批量（15 模板） |
| `templates3d_batch_semantic.py` | 98 | V14 全量重做（语义 + MuseTalk 口型 + 重剪 + 海报） |
| `templates3d_new_templates.py` | 141 | **V17**：新增写实模板（real_street/real_cafe/real_kitchen） |
| `cinema3d.py` | 240 | V6.5 全 3D《国宝会说话》：briefs → 云端 MiniMax-H3 → 3D 镜头 |
| `cinema3d_local.py` | 198 | V6.5 H3 本地兜底（ComfyUI） |
| `cinema3d_assemble.py` | **1597** | V6.5 全 3D 成片剪辑：3D 镜 + 旁白 TTS + H3 环境声 + BGM → 1280×720/25fps |

### 1.6 合成 / 分镜表 / QC / 发布

| 阶段 | 模块 | 行 | 职责 |
|---|---|---|---|
| 合成 | `render_v5.assemble` / `editing.py` | — | 剪辑点 + 转场 + 混音 + ASS 烧录 → `final.mp4` |
| 合成 | `characters_v5.py` | 2312 | V5 角色库：立绘渲染 + 口型 viseme 时间轴 |
| 分镜表 | `storyboard_v5.py` | 225 | 剪辑计划 + 成片 → storyboard.json/html/png |
| 分镜表 | `storyboard_v3.py` | 898 | v3 版分镜序列（render 兼容） |
| QC | `qc.py` | 130 | 通用成片质检门（时长/黑帧/字幕） |
| QC | `qc_v3.py` | 557 | v3 硬门：时长/分辨率/帧率/音轨/黑帧/字幕/H3 覆盖/可追溯/静态标记 |
| QC | `qc_v5.py` | 280 | V5 硬门：规格 + 风格 + 口型 + 字幕 + 风格指纹 |
| 发布 | `capcut.py` | 742 | 剪映草稿导出（draft_content/draft_meta/materials/SRT） |
| 发布 | `localize.py` | 1269 | 多语种本地化渲染（字幕重排/术语/罗马化） |
| 发布 | `fonts.py` | 88 | 字体落盘（drawtext/libass 相对路径） |
| 基础设施 | `v5_catalog.py` | 961 | **契约目录层**：内建 + custom 覆盖合并、JSON Schema 校验、引用完整性 |
| 基础设施 | `v5_cli.py` | 553 | 契约 CLI（浏览/校验/导出/预览，不占 GPU） |
| 基础设施 | `config.py` / `utils.py` / `cli.py` | 84/196/226 | 二进制路径、ffprobe、日志、CLI 入口 |

### 1.7 V6 智能体编排层（`pipeline/agents/`，9 模块）

| 模块 | 行 | 职责 |
|---|---|---|
| `roles.py` | 73 | 8 个角色定义（producer/story_architect/voice_director/art_director/editor/video_director/qa/publisher） |
| `registry.py` | 253 | **阶段 = 工具**：build_story / synthesize_voices / plan_editing / render_shots / render_h3 / assemble_episode / build_storyboard / evaluate_qc / write_report |
| `workflow.py` | 105 | 读写 `contract/v6/workflows/<template>.json` |
| `orchestrator.py` | 194 | 按工作流逐阶段执行、落 run 证据、支持 `--from/--stages` 增量 |
| `ops.py` | 172 | 修改操作（set_voice / patch_editorial / patch_template / patch_workflow），返回受影响阶段 |
| `art.py` | 120 | generate_art / set_pose / list_art（受 `contract/v6/art/pose_policy.json` 约束） |
| `cli.py` / `__main__.py` / `__init__.py` | 158/7/12 | `python -m pipeline.agents list/inspect/run/edit/status/workflow` |

---

## 2. contract/ 合同清点

### 2.1 总量

| 目录 | JSON 数 | 说明 |
|---|---|---|
| **contract/v5** | **295** | V5 主合同树（含 2 个顶层 JSON） |
| contract/v6 | 17 | workflows 16 + art 1 |
| contract/templates_v3 | 16 | v3 时代模板快照（历史层） |
| contract/formats | 17 | 节目形态定义（老格式层） |
| contract/characters | 3 | 老角色层（被 v5/characters 取代） |
| contract 根 | 5 | `demo_episode.json` `scene_map.json` `scene_descriptions.json` `style_map.json` `style_presets.json` |
| **合计** | **353** | |

### 2.2 v5 分类型条目数（具体数字）

| 合同类型 | 目录 | 条目数 | 备注 |
|---|---|---|---|
| 模板 | `v5/templates` | **21** | 内建 16 综艺 + 3 写实 + 2 长片(relic) |
| 自定义模板 | `v5/custom/templates` | **12** | v22/v23 各 1 + v45 4 + v46 6 + 示例 1 |
| 场景 | `v5/scenes` | **73** | 分层绘景合同（layers/camera_zones/anchors/variants/depth） |
| 自定义场景 | `v5/custom/scenes` | **1** | |
| 角色 | `v5/characters` | **24** | id/label/era/persona/role_pool/voice/appearance/poses/mouth/style_overrides/art |
| 自定义角色 | `v5/custom/characters` | **26** | **custom 角色数已超过内建** |
| 道具 | `v5/props` | **44** | |
| 自定义道具 | `v5/custom/props` | **0** | 空目录 |
| 风格 | `v5/styles` | **16** | woodblock_classic / parchment_case / ink_chronicle / film_interview / hologram_stage / noir_rain / museum_gold / silk_arena / oil_salon / blueprint_lab / clay_diorama / chalk_theatre / vhs_late_show / candy_collage / neon_toon / baroque_candle |
| 自定义风格 | `v5/custom/styles` | **1** | `custom_holo_paper` |
| 剪辑档案 | `v5/editorial` | **16** | 只覆盖内建 16 综艺模板 |
| 素材包 | `v5/packs` | **2** | pack_history_chronicle / pack_modern_studio |
| 自定义素材包 | `v5/custom/packs` | **0** | 空目录 |
| Schema | `v5/schema` | **7** | character / pack / prop / scene / style / template / timeline |
| 系列 | `v5/series` | **1** | `undercity_arc`（3 部） |
| 3D 合同 | `v5/cinema3d` | **2** | relic_talk_3d_briefs.json / reuse_map.json |
| 3D 模板（46 镜 brief） | `v5/cinema3d/templates3d` | **46** | 全 3D 长片逐镜 brief |
| 美术提示词 | `v5/art` | **1** | `prompts.json` |
| 声音库 | `v5/voices.json` | **24 presets** | 固定音色库 |
| 声音选角 | `v5/voice_casting.json` | map + characters | 角色 → 音色映射 |
| **v5 合计** | | **295** | |

### 2.3 模板清单（33 个：id + label + 风格 + 类型 + 幕数 + 成片落点）

类型判定依据：`render_type=="2d"` → 2D；`tags` 含 `realistic` → 写实；`tags` 含 `3d` → 3D；其余 16 个内建模板走「V5 分镜 + templates3d 3D 重制」。

| # | id | label | style | 类型 | 幕 | 目标时长 | 成片落点 |
|---|---|---|---|---|---|---|---|
| 1 | `classics` | 典籍 | woodblock_classic | 3D 样片 | 9 | 38s | templates_3d/classics 18.06MB |
| 2 | `history_mystery` | 历史悬案 | parchment_case | 3D 样片 | 9 | 38s | templates_3d/history_mystery 14.52MB |
| 3 | `history_talk` | 历史夜话 | ink_chronicle | 3D 样片 | 9 | 38s | templates_3d/history_talk 13.08MB |
| 4 | `hot_ones` | 热辣对话 | film_interview | 3D 样片 | 8 | 38s | templates_3d/hot_ones 14.00MB |
| 5 | `masked_singer` | 蒙面歌手 | hologram_stage | 3D 样片 | 8 | 38s | templates_3d/masked_singer 19.94MB |
| 6 | `mystery` | 悬疑现场 | noir_rain | 3D 样片 | 8 | 38s | templates_3d/mystery 9.73MB |
| 7 | `national_treasure` | 国宝 | museum_gold | 3D 样片 | 8 | 38s | templates_3d/national_treasure 15.93MB |
| 8 | `qipashuo` | 奇葩说 | silk_arena | 3D 样片 | 8 | 38s | templates_3d/qipashuo 13.43MB |
| 9 | `real_cafe` | 咖啡对谈 | film_interview | 写实 | 6 | 34s | templates_3d/real_cafe 12.04MB |
| 10 | `real_kitchen` | 深夜食堂 | film_interview | 写实 | 6 | 34s | templates_3d/real_kitchen 11.01MB |
| 11 | `real_street` | 城市夜访 | film_interview | 写实 | 6 | 35s | templates_3d/real_street 15.14MB |
| 12 | `relic_talk` | 国宝会说话 | museum_gold | 2.5D 长片 | **54** | **300s** | **无 final**（templates_v5/relic_talk 58 镜 mp4） |
| 13 | `relic_talk_3d` | 国宝会说话 · 3D | museum_gold | **3D** | **54** | **300s** | **templates_v5/relic_talk_3d/final.mp4 96MB ✅** |
| 14 | `roundtable` | 圆桌派 | oil_salon | 3D 样片 | 8 | 38s | templates_3d/roundtable 15.14MB |
| 15 | `shark_tank` | 创投路演 | blueprint_lab | 3D 样片 | 7 | 38s | templates_3d/shark_tank 14.74MB |
| 16 | `slow_life` | 慢生活 | clay_diorama | 3D 样片 | 8 | 38s | templates_3d/slow_life 12.66MB |
| 17 | `snl_sketch` | 周六夜现场 | chalk_theatre | 3D 样片 | 8 | 38s | templates_3d/snl_sketch 11.74MB |
| 18 | `standup` | 脱口秀 | vhs_late_show | 3D 样片 | 8 | 38s | templates_3d/standup 11.54MB |
| 19 | `taskmaster` | 任务大师 | candy_collage | 3D 样片 | 8 | 38s | templates_3d/taskmaster 11.94MB |
| 20 | `tonight_show` | 美式深夜秀 | neon_toon | 3D 样片 | 9 | 38s | templates_3d/tonight_show 17.23MB |
| 21 | `traitors` | 叛徒圆桌 | baroque_candle | 3D 样片 | 8 | 38s | templates_3d/traitors 14.60MB |
| 22 | `custom_holo_talk` | 全息史料夜话（示例） | custom_holo_paper | 示例 | 3 | 30s | **无任何成片** |
| 23 | `custom_tpl_v22_radio` | 深夜电台 · 城市来信 | vhs_late_show | 2D | 6 | 23.45s | templates_3d 8.33MB（无 2D 目录） |
| 24 | `custom_tpl_v23_shop` | 深夜便利店 · 十分钟 | film_interview | 2D | 4 | 15.15s | templates_3d 3.60MB |
| 25 | `custom_tpl_v45_guoman` | V45 2D·国漫国风 | neon_toon | **2D** (guoman) | 4 | 14.12s | templates_2d 4.84MB |
| 26 | `custom_tpl_v45_jp_anime` | V45 2D·日漫电影感 | neon_toon | **2D** (jp_anime) | 4 | 14.12s | templates_2d 4.49MB |
| 27 | `custom_tpl_v45_retro_cel` | V45 2D·复古赛璐璐 | neon_toon | **2D** (retro_cel) | 4 | 14.12s | templates_2d 4.70MB |
| 28 | `custom_tpl_v45_soft_watercolor` | V45 2D·吉卜力治愈系 | neon_toon | **2D** (soft_watercolor) | 4 | 14.12s | templates_2d 5.10MB |
| 29 | `custom_tpl_v46_council` | V46 2D·议会夜审 | neon_toon | **2D** (arcane_cinematic) | 4 | 14.12s | templates_2d 4.83MB |
| 30 | `custom_tpl_v46_cyber` | V46 2D·赛博电影感 | neon_toon | **2D** (arcane_cinematic) | 4 | 14.12s | templates_2d 4.16MB |
| 31 | `custom_tpl_v46_duologue` | V46 2D·装配夜话 | neon_toon | **2D** (arcane_cinematic) | 6 | 19.14s | templates_2d 6.56MB |
| 32 | `custom_tpl_v46_robot` | V46 2D·机器人组装 | neon_toon | **2D** (arcane_cinematic) | 4 | 13.68s | templates_2d 4.91MB |
| 33 | `custom_tpl_v46_undercity` | V46 2D·底城往事 | neon_toon | **2D** (arcane_cinematic) | 4 | 14.12s | templates_2d 5.35MB |

> 幕数差异明显：内建综艺 7–9 幕、写实 6 幕、custom 4–6 幕、relic 长片 54 幕。

### 2.4 v6 工作流阶段矩阵（16 个工作流完全同构）

| 阶段 id | agent | tool | optional | 启用数 / 总数 |
|---|---|---|---|---|
| `story` | story_architect | build_story | false | **16/16** |
| `voice` | voice_director | synthesize_voices | false | **16/16** |
| `art` | art_director | generate_art | true | **0/16（全关）** |
| `editorial` | editor | plan_editing | false | **16/16** |
| `shots` | video_director | render_shots | false | **16/16** |
| `h3` | video_director | render_h3 | true | **0/16（全关）** |
| `assemble` | editor | assemble_episode | false | **16/16** |
| `storyboard` | editor | build_storyboard | false | **16/16** |
| `qc` | qa | evaluate_qc | false | **16/16** |
| `publish` | publisher | write_report | false | **16/16** |

16 个工作流的 stages 数组**逐字节同构**（只差 `variables.template/duration/seed`），即「可编辑工作流」目前没有任何模板化差异。

---

## 3. tests/ 测试清点

### 3.1 `tests/`（顶套件，21 文件 / **343** 用例 / 10,381 行）

| 文件 | 用例 | 行 | 覆盖主题 |
|---|---|---|---|
| `test_template_v3.py` | **70** | 1022 | v3 模板加载/校验（用例最多的单文件） |
| `test_api.py` | 27 | 543 | 后端 HTTP API 端到端 |
| `test_v5_catalog.py` | 26 | 611 | V5 合同目录、custom 覆盖、Schema 校验、引用完整性 |
| `test_v5_acceptance.py` | 20 | 960 | V5 验收：render_v5 + motion2d + qc_v5 全链 |
| `test_v45_style_polish.py` | 18 | 216 | V45 2D 风格打磨（templates3d_hq / templates3d 引用） |
| `test_script.py` | 18 | 454 | 台本生成 / LLM 脚本 |
| `test_characters.py` | 17 | 460 | 角色库、音色、外观 |
| `test_concept.py` | 16 | 552 | 概念图 / 立绘 / capcut |
| `test_llm.py` | 16 | 338 | LLM 供应商适配 |
| `test_v3_acceptance.py` | 15 | 849 | v3 验收 |
| `test_publishing.py` | 14 | 624 | 发布包 / 本地化 / 上传 |
| `test_v5_4_acceptance.py` | 14 | 892 | V5.4 剪辑：editing/editorial/motion2d/render_v5/storyboard_v5 |
| `test_v5_2_acceptance.py` | 13 | 776 | V5.2 美术：v5_art / scene_render / render_v5 |
| `test_jobs.py` | 10 | 279 | 后台作业调度 |
| `test_v4_acceptance.py` | 10 | 655 | V4 音频/剪辑验收 |
| `test_localization.py` | 9 | 296 | 多语种本地化 |
| `test_v5_voices.py` | 9 | 125 | 声音库/选角/声音锁 |
| `test_comfyui.py` | 8 | 159 | ComfyUI 适配 |
| `test_h3_provider.py` | 7 | 254 | H3 provider |
| `test_dynamic.py` | 6 | 274 | 动态镜头/口型 |
| `conftest.py` | 0 | 63 | fixture |

**v4x 系列实测**：`test_v4_acceptance.py`（10）、`test_v45_style_polish.py`（18）、`test_v5_2_acceptance`（13）、`test_v5_4_acceptance`（14）——v4x 实际只有 **2 个** v4 时代文件（28 用例），v5.x 反而是主力（47 用例）。

### 3.2 `pipeline/tests/`（内嵌套件，17 文件 / **185** 用例）

| 文件 | 用例 | 覆盖 |
|---|---|---|
| `test_pipeline.py` | **31** | 老 pipeline 全链 + ass_builder |
| `test_v3_h3.py` | 19 | H3 提交/重试/meta 证据（**H3 唯一实质测试**） |
| `test_v3_render.py` | 19 | v3 渲染 + h3_comfy |
| `test_v3_evidence.py` | 16 | 可追溯性证据 |
| `test_v3_stage.py` | 16 | 舞台首帧合成 |
| `test_localize.py` | 12 | 本地化渲染 |
| `test_editing.py` | 11 | 剪辑执行 |
| `test_lipsync_unit.py` | 11 | MuseTalk 单元 |
| `test_v4_loudnorm.py` | 11 | 响度归一 |
| `test_capcut.py` | 9 | 剪映草稿 |
| `test_v4_audio.py` | 8 | V4 音频 |
| `test_dynamic.py` | 7 | 动态镜头 |
| `test_style.py` / `test_style_v2.py` | 5 / 5 | 风格系统 v1/v2 |
| `test_scenes.py` | 4 | 场景合成 |
| `test_v4_render_integration.py` | **1** | V4 渲染集成（仅冒烟） |

### 3.3 测试覆盖缺口（实测 grep 结果）

| 模块 | 行 | 被测试直接引用 |
|---|---|---|
| `cinema3d_assemble.py` | 1597 | **无** |
| `cinema3d_local.py` | 198 | **无** |
| `h3_v5.py` | 198 | **无** |
| `style_v5.py` | 3180 | **无直接引用**（仅经 templates3d_hq 间接） |
| `pipeline/agents/*`（9 模块） | 950 | **无任何测试文件引用** |
| `motion2d.py` | 3598 | 3 个（v5_acceptance / v5_4_acceptance / v5_catalog） |
| `scene_render.py` | 4242 | 2 个 |

结论：**覆盖率与代码体量严重脱节**——最大的 3 个模块（`scene_render` 4242 行、`motion2d` 3598 行、`style_v5` 3180 行）由 2–3 个验收文件兜底，而 V6 编排层、3D 全片剪辑、H3 混合模式共 ~3.9k 行**零测试**。

---

## 4. outputs/ 产出清点

### 4.1 成片落点（以 `final.mp4` 为准）

| 目录 | 子目录 | 有 final.mp4 | 说明 |
|---|---|---|---|
| `outputs/templates_2d` | 9 | **9** | custom v45 4 + v46 5，全部 final.mp4 + poster.jpg |
| `outputs/templates_3d` | 30 | **30** | 16 内建 + 3 写实 + 11 custom；30/30 都有 report.json（其中 9 份与 templates_2d 同源，7 份字节相同） |
| `outputs/templates_v5` | 34（含 `_fonts`） | **1** | 只有 `relic_talk_3d/final.mp4`（96MB） |
| `outputs/templates_v3` | 18 | **18** | 16 内建 + `tonight_show_40s_v1` + `tonight_show_5min`（69MB） |
| `outputs/episodes` | 3 | **3** | ep_001 27.29MB / ep_e1015494fa / ep_e6a182d7d1 |
| `outputs/template_demos` | 18 | 0 | 17 个 `*_5min` 目录内是 `<id>_5min.mp4`，非 final 命名 |
| `outputs/demo` | — | 0（根级 `final.mp4`） | 299s / 52 镜 / 34.7MB，**mock 生成** |
| `outputs/series/undercity_arc` | — | 0 | 成片名为 `series.mp4`（15.05MB） |
| `outputs/h3` | — | 0 | 单镜 H3 验证素材 |

**去重后真正「一个模板一份成片」的模板数**：
- 2D 成片：**9**（templates_2d）
- 3D 成片：**30**（templates_3d，但其中 9 个是 2D 成片的副本，见 §5.6）
- V5 长片成片：**1**（relic_talk_3d）
- 33 个 v5 模板中，**有任一成片的 = 31**；**完全无成片 = 2**（`relic_talk` 只有 58 个镜片、`custom_holo_talk` 只有 `work/`）

### 4.2 outputs/series

`outputs/series/` 下 **1 个系列**：`undercity_arc`（合同 `contract/v5/series/undercity_arc.json`，label「底城往事 · 三部曲」）。

| 文件 | 大小 | 说明 |
|---|---|---|
| `series.mp4` | 15.05 MB | 49.14s 系列合辑 |
| `title_card.mp4` | 118 KB | 系列片头 |
| `poster.jpg` / `title.jpg` | 117 / 145 KB | 封面 |
| `manifest.json` | — | built_at 2026-11-05 07:39:56，build_sec 1.7 |
| `concat.txt` | — | 拼接清单 |

manifest 记录的 3 集：`custom_tpl_v46_undercity` 14.12s / `custom_tpl_v46_robot` 13.68s / `custom_tpl_v46_duologue` 19.14s（合计 46.94s，manifest 标 duration 49.14s，差值来自 title_card）。

### 4.3 outputs/agent_runs

**8 个 run，全部是 `history_talk`**（2026-10-26 22:59 → 2026-10-27 00:05）。

| run_id | status | 阶段结果 |
|---|---|---|
| 20261026_225932 | **planned** | story=planned，其余全 skipped（只排练未执行） |
| 20261026_225936 | **failed** | story/voice/editorial=ok，**shots=failed** |
| 20261026_230002 | ok | 9 阶段 ok（h3 skipped） |
| 20261026_230114 | ok | 6 阶段 ok |
| 20261026_230155 | ok | 6 阶段 ok |
| 20261027_000505 | **planned** | 全 planned / art+h3 skipped |
| 20261027_000543 | **planned** | 全 planned |
| 20261027_000559 | ok | 6 阶段 ok，shots 23.2s / assemble 8.8s / qc 2.7s |

每个 run 目录含 `run.json` / `events.jsonl` / `summary.md` / `workflow.json`（一次含 `template_summary.json`）。
**关键证据**：`20261027_000559/summary.md` 明写「成片: `outputs/templates_v5/history_talk/final.mp4`」，**该文件现已不存在**。

### 4.4 其他产出目录

| 目录 | 内容 |
|---|---|
| `outputs/pipeline_jobs` | **86** 个 `pl_*.json` 作业记录 |
| `outputs/pipeline_batches` | 12 个 `pb_*.json` |
| `outputs/publish_batches` | 12 个 `pub_*.json` |
| `outputs/cinema_jobs` | 9 个 `cjob_*.json`（3D 长片作业） |
| `outputs/exports` | `造片局_EP001`（剪映草稿：draft_content/draft_meta/draft_settings/materials 53 文件/subtitles.srt）+ `_apicheck` |
| `outputs/characters` + `concepts` | 7 个角色目录（ali/dawei/laozhou + 4 个 char_*），含 cutout_2d/3d_meta |
| `outputs/v5_voices` | **25** 个角色音色目录 + `voice_demo_all.wav` |
| `outputs/scene_plates` | 45 张场景 plate |
| `outputs/llm_scripts` | 33（16 模板 × json+txt） |
| `outputs/llm_episodes` | 16 json |
| `outputs/dynamic_frames` / `localized_frames` | 33 / 7 张中间帧 |
| `outputs/template_demos` | 16 个 `*_5min` + `_showcase` + `_thumbs`，含 `template_showcase.mp4` |

---

## 5. 流水线断点与未完成项（每条附证据）

### 5.1 【严重】V5 主渲染的成片落盘缺失（33/34 目录无 final.mp4）

`render_v5.py:29` 定义 `OUT_ROOT = outputs/templates_v5`，`:55-57` 把成片写成 `OUT_ROOT/<template>/final.mp4`。但：

- `outputs/templates_v5` 34 个目录，全量递归搜索 `final*.mp4` 只命中 **3** 个文件，全部在 `relic_talk_3d/`（`final.mp4` / `final_pilot.mp4` / `final_pilot3d.mp4`）。
- 其余 33 个模板目录有 `shots/`（classics 11 个 `v5_*.mp4`）、`frames/`、`timeline.json`、`storyboard.json/html/png`、`qc_report.json`、`work/`（含 `final.ass`、`final_narration.wav`、`manifest.json`），**唯独没有 final.mp4**。
- `outputs/templates_v5/classics/qc_report.json` 的 duration check 写着 `"成片 42.25s / 计划 42.25s"`——**QC 当时确实探到过一个 42.25s 的 final.mp4**。
- `pipeline/agents/registry.py:163` 的 `evaluate_qc` 会 `probe_video(ctx.paths["out"])`，文件不存在会直接抛错；而 8 个 agent run 里 4 个 status=ok，summary 还打印了成片路径。

结论：**成片曾生成过，之后被清理/迁移，交付台账（run summary / qc_report）与磁盘现状不一致**。这是整条流水线最硬的断点：现在无法从 `templates_v5` 复现任何一份 2D V5 成片，只能重跑。

### 5.2 【严重】V6 智能体编排只覆盖 16/33 模板

- `contract/v6/workflows` = **16** 个；`contract/v5/templates` + `custom/templates` = **33** 个。
- 无 workflow 的 17 个：12 个 custom（v22/v23/v45×4/v46×6 全部）+ 3 个 real_* + relic_talk + relic_talk_3d。
- 无 editorial 归档的也是同一批 17 个（`contract/v5/editorial` = 16）。
- 后果：AGENTS.md 宣称的「每个模板一份可编辑工作流 / `python -m pipeline.agents run --template <任意模板>`」对 12 个 custom 与 3 个写实模板**不成立**，这些模板的成片只能由专用脚本产出（`templates3d_hq.py` / `templates3d_new_templates.py` / `cinema3d.py`）。

### 5.3 【严重】H3 阶段全关，编排链路上的 H3 从未产出成片

- 16/16 工作流 `h3.enabled=false`（`render_h3` 工具在全仓无一次 enabled）。
- `render_h3` 的输出目录 `outputs/templates_v5_hybrid/`（`registry.py:128` 注释明写）**在磁盘上不存在**。
- `outputs/h3/` 只有单镜验证素材：`laozhou_h3.mp4` / `ali_h3.mp4` / `dawei_h3.mp4` / `test_001.mp4` + `shotplan_tests/`（A/B/C/D 四种镜位对比 + `CONCLUSION.md`）。
- 真正跑通的 3D 路径是**旁路**：`templates3d_hq.py` 直接调 ComfyUI，`outputs/templates_3d/progress.json` 里有逐镜 `prompt_id` + `seconds`（如 classics 8 镜 54–99s/镜）→ 证明 GPU 链路可用，但**没有走 V6 编排器的 h3 stage**。

### 5.4 【严重】demo 成片是 mock 占位

- `outputs/demo/clips/s001.meta.json`：`{"source": "mock", "version": "mock-v12-concepts", "type": "title_card"}`。
- `outputs/demo/manifest.json`：`clips_from_h3 = 0`、`clips_from_shots = 0`、`clips_reused = 0`、52 镜、299s、34.7MB。
- 生成器是 `mock_shot.py`（934 行）：ffmpeg lavfi 背景 + 立绘 + drawtext，**不是 AI 视频**。
- 这份 299s「5 分钟样片」被 `outputs/exports/造片局_EP001` 当作发布素材引用（export_manifest 的 52 条 video 全部指向 `outputs/demo/clips/*`）。

### 5.5 【中】art 阶段全关，姿态策略近乎空表

- 16/16 工作流 `art.enabled=false`。
- `contract/v6/art/pose_policy.json` 只有 1 个角色：`{"characters": {"zhuge": {"enabled": ["point"]}}}`，且 `updated_at` 为空字符串。
- 24 内建角色 + 26 custom 角色中，仅 1 个有姿态白名单。

### 5.6 【中】templates_3d 里混入 2D 成片副本（9/30 与 templates_2d 同源，其中 7 份字节完全相同）

| 模板 | templates_2d hash | templates_3d hash | 同一文件 |
|---|---|---|---|
| custom_tpl_v45_guoman | bb75117c9292 | bb75117c9292 | **是** |
| custom_tpl_v45_retro_cel | bfd349bda14c | bfd349bda14c | **是** |
| custom_tpl_v45_soft_watercolor | b19282a1e52c | b19282a1e52c | **是** |
| custom_tpl_v46_council | d0f952079c65 | d0f952079c65 | **是** |
| custom_tpl_v46_cyber | bca5545fd752 | bca5545fd752 | **是** |
| custom_tpl_v46_duologue | 661306330d93 | 661306330d93 | **是** |
| custom_tpl_v46_robot | db1b0d04c566 | db1b0d04c566 | **是** |
| custom_tpl_v45_jp_anime | 01d3cdd9fac2 | b1ed13345733 | 否 |
| custom_tpl_v46_undercity | 8e4383830b0a | da2d9fe3e302 | 否 |

→ `templates_3d` 的「30 个 3D 成片」实际只有 **23 个真 3D + 7 个 2D 副本**，目录语义被污染。

### 5.7 【中】progress.json 与磁盘不一致，交付台账不可信

`outputs/templates_3d/progress.json` 记录 **19** 个模板，其中只有 **4** 个带 `final` + `probe` + `assembled_at`（`tonight_show` / `history_mystery` / `real_street` / `real_cafe`），其余 15 个 status 停在 `"generated"`（只生成了镜片，没写回剪辑结果）。
但磁盘上 `templates_3d` **30/30** 目录都有 `final.mp4` 且 `report.json` 齐全（含 `probe` + `assemble` + 逐边界 transition）。
→ `report.json` 是可靠台账，`progress.json` 已腐坏（后续批次没回写）。

### 5.8 【中】编排器把「只排练」当正常状态

`run.json` 的 `status` 有 3 种值被混用：`planned`（3 个 run）、`failed`（1 个）、`ok`（4 个）。`planned` 的 run 目录里 `summary.md` / `events.jsonl` 齐全，**看起来像一次正常执行**，但 `stages[].status` 全是 `planned`/`skipped`。`outputs/agent_runs` 里没有任何一个 run 覆盖 `history_talk` 以外的模板。

### 5.9 【轻】QC 门只校验「成片规格」，不校验「成片存在性」

`qc_v5.evaluate` 的 checks 有 duration / resolution / fps / audio / blackdetect / scene_coverage / character_coverage / 风格指纹等，全部依赖 `probe(final)`。一旦 final 被清理，QC 不会留下「missing」记录——它只会抛异常，因此**缺片状态无法从 qc_report 反向发现**（当前 33 份 qc_report 全 `ok:true`，全都没有对应成片）。

### 5.10 【轻】两套并行渲染栈并存，产物目录语义重叠

- 栈 A（2D 分层）：`render_v5.py` + `motion2d` + `scene_render` + `style_v5` → `templates_v5/`
- 栈 B（3D/H3）：`templates3d*.py` + `h3_comfy` + `cinema3d_assemble` → `templates_3d/`
- 栈 C（v3 历史）：`render_v3.py` + `stage.py` → `templates_v3/`

三个 `templates_*` 目录里同一模板最多出现 **3 份不同成片**（如 classics：templates_v3 10.75MB / templates_3d 18.06MB / templates_v5 无），没有 manifest 指明「哪份是当前交付版」。

### 5.11 【轻】空目录 / 空类别

- `contract/v5/custom/props` = 0、`contract/v5/custom/packs` = 0。
- `outputs/templates_v5` 下 14 个目录（`custom_holo_talk` + v22/v23 + v45×4 + v46×6 + real×3）只有 `work/` 或完全无内容。
- `custom_holo_talk` 是「示例模板」，无成片、无 workflow、无 editorial。

### 5.12 【轻】测试与代码体量脱节

见 §3.3：`pipeline/agents/*`（950 行）零引用；`cinema3d_assemble.py`（1597 行）零引用；`h3_v5.py`（198 行）零引用。全仓 528 个用例里，覆盖 3D 全片剪辑链路的只有 `test_v3_h3.py`（19）与 `test_v3_render.py`（19）两个 v3 时代文件。

---

## 6. 结论

### 6.1 现状一句话

**合同层（353 JSON / 33 模板 / 16 工作流 / 16 风格 / 24+26 角色 / 73+1 场景）与代码层（88 模块 / 5 万行）都已完成，测试层（528 用例）偏 v3/v5 验收，而产物层是断裂的**：3D 样片 30 份齐全，V5 2D 成片几乎全部缺失，H3 未接入编排，demo 是 mock 占位。

### 6.2 交付可用性判定

| 交付形态 | 可交付 | 证据 |
|---|---|---|
| 3D 样片（16 内建 + 3 写实 + 11 custom） | ✅ 30/30 | templates_3d 每份都有 final.mp4 + report.json + probe |
| 2D 风格片（v45/v46 九种风格） | ✅ 9/9 | templates_2d 每份 final.mp4 + poster |
| V5 5 分钟长片（relic_talk_3d） | ✅ 1/1 | 96MB / 54 镜 / 全 3D |
| V5 2D 常规成片（内建 16 模板） | ❌ 0/16 | 只有镜片 + timeline + qc_report，final 缺失 |
| V6 智能体一站式重跑 | ⚠️ 16/33 模板可用 | 17 个模板无 workflow，h3/art 阶段全关 |
| 5 分钟综艺 demo | ⚠️ 有片但是 mock | demo/clips/meta source=mock |
| 系列合辑 | ✅ 1 个（undercity_arc） | series.mp4 15MB / 49.14s |

### 6.3 建议的修复顺序（按阻断程度）

1. **重建 V5 成片落盘**：对 16 个内建模板重跑 `assemble`（`registry.assemble_episode` 或 `render_v5` 主入口），补齐 `outputs/templates_v5/<id>/final.mp4`；顺带修 `evaluate_qc` 在 final 缺失时写 `ok:false` 而不是抛异常。
2. **补齐 workflow 与 editorial**：为 12 个 custom + 3 个 real_* + 2 个 relic 生成 `contract/v6/workflows/*.json`（可从同构模板派生）与 editorial 档案，让 33 个模板都能被 `pipeline.agents run` 驱动。
3. **决策 H3 的归属**：要么把 `templates3d_hq` 的 ComfyUI 调用正式注册为 `h3` 阶段的实现（并在 workflow 里启用），要么在文档里明确「H3 只做样片、不进编排」。
4. **清理 outputs 语义**：删掉 `templates_3d` 里 7 份字节相同的 2D 副本（另 2 份 `jp_anime`/`undercity` 为同源重编码，需定版）；为三个 `templates_*` 家族写一份 `delivery_manifest.json` 指明当前交付版；修 `progress.json` 回写。
5. **补测试**：`pipeline/agents`（编排/ops/registry）、`cinema3d_assemble`（1.6k 行剪辑）、`h3_v5` 三块各补一组冒烟 + 契约测试，至少覆盖「final.mp4 存在性」这条断言。
6. **替换 mock demo**：用现有 3D 样片或 relic_talk_3d 重出一版 5 分钟样片，避免发布包引用 `source=mock` 的素材。
