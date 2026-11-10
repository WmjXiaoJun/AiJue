# V49 · 功能全景梳理 + 计划执行核对（审计报告）

> 生成时间：2026-11-05 07:5x ｜ 基线：`master` @ `2189202`（v48.1）
> 方法：代码/合同/产物/测试实测（3 个并行审计：后端 / 前端 / 流水线），不做猜测；每个数字都有落盘证据。
> 明细：[docs/audit/PART_BACKEND.md](audit/PART_BACKEND.md)、[docs/audit/PART_FRONTEND.md](audit/PART_FRONTEND.md)、[docs/audit/PART_PIPELINE.md](audit/PART_PIPELINE.md)

---

## 0. 一页结论

| 问题 | 结论 |
|---|---|
| **功能做完了吗** | 做完了，且**远超原始计划**：142 个 API、15 个页面、88 个流水线模块（5.0 万行）、33 个模板、50 个角色、74 个场景、528 个测试用例、13.5 GB 成片产物 |
| **现在在按计划做吗** | **一半是，一半不是**。①节奏上：是在按计划做（13 天 172 次提交、76 份版本文档，每版一主题一交付）；②方向上：**已经不在原始计划里**（原始计划是 5–10 分钟、1080P、70–100 镜的长片；最近 12 版做的是 14–19 秒的 2D 片段 + 系列拼片）；③验收上：**不在**（自家验收门 5 红 / 486 绿，见 §5.3） |
| **验收现状（全量实测）** | `python -m pytest tests -q` → **5 failed / 486 passed / 36 skipped**：红线全部来自「产品线往前走了、验收基线没跟着走」 |
| **最危险的偏差** | 唯一两个 5 分钟长片模板 `relic_talk` / `relic_talk_3d` 恰好是当前**唯一校验不过**的合同（缺 `packaging`）；而最近 12 版新增的 2D 短片全部校验绿、全部有测试——资源正在往"短片"这一侧堆积 |
| **一句话** | 平台能力已经够用，缺的不是功能，是**把主线重新钉死**（长片 or 短片系列），然后把验收门修绿 |

---

## 1. 功能全景（7 层，从用户入口到产物）

### 1.1 创作入口层（前端 15 页）

无 react-router，`App.tsx` 用 `useState<ViewState>` + hash 双向同步（`parseHash`/`hashFor`/`hashchange`），另有 13 条旧 URL 重定向兜底。

| # | 页面 | 路由 | 用户能干什么 | 主要后端 |
|---|---|---|---|---|
| 1 | 项目大厅 | `#/projects` | 续做、项目卡、最近任务、新建项目 | `GET/POST /api/projects`、`GET /api/jobs` |
| 2 | 编剧间 | `#/episodes/{eid}/script` | 台本编辑、AI 生成台本、一键转分镜 | `POST …/generate-script`、`…/storyboard/generate` |
| 3 | 资产库 | `…/assets` | 角色工坊（概念图/三视图/表情/H3/锁定）、固定资产、场景卡 | `POST /api/characters/{c}/concept/generate` 等 |
| 4 | 分镜台 | `…/storyboard` | 镜头表 + 抽屉（台词/时长/版本/删除/重生成） | `/api/shots/*`、`…/generate-dynamic` |
| 5 | 生成台 | `…/generate` | 任务队列（2s 轮询）、重试、费用分布 | `GET /api/jobs`、`…/costs`、`…/retry` |
| 6 | 成片房 | `…/finish` | 播放、渲染、多语种、剪映导出、海外发布 | `…/render｜export｜localize｜publish` |
| 7 | 3D 镜头台 | `#/cinema/shots` | 56 镜网格、来源筛选、单镜 brief、批量重生成 | `GET /api/cinema/shots`、`POST /api/cinema/generate` |
| 8 | 生成队列 | `#/cinema/jobs` | ComfyUI/MetaSo 在线态、进度/日志/取消 | `/api/cinema/jobs`、`/api/cinema/workers` |
| 9 | 剪辑台 | `#/cinema/assemble` | 环境声/影调/调色 → 合成 → 轮询 → 成片 | `POST /api/cinema/assemble`、`GET /api/cinema/report` |
| 10 | 一键成片 | `#/pipeline` | 6 步向导、批量队列、发布包 | `POST /api/pipeline/run｜batch` |
| 11 | 角色与声音 | `#/characters` | 32 角色网格、试听、试镜/宣传片/双人对戏/切条包/合集包 | `/api/v5/voice/*`、`/api/v5/characters/*` |
| 12 | 模板库 | `#/library/templates` | 19 模板总表、逐镜进度、生成/合成、批量队列、新建模板向导 | `/api/cinema/templates/*`、`POST /api/v5/templates` |
| 13 | 风格墙 | `#/library/styles` | 17 种风格（色板/技法/转场/预览） | `GET /api/v5/catalog`、`/api/v5/styles/{id}` |
| 14 | 场景库 | `#/library/scenes` | 74 个分层场景 + 风格筛选 + 详情 | `GET /api/v5/scenes/{id}`、`/preview/scene/{id}` |
| 15 | 成片库 | `#/library/final` | 成片画廊、QC、重新合成、批量发布包、**系列串片 + 导出合集** | `/api/cinema/templates`、`/api/v5/series` |

规模：组件 38（在链 30 / 孤儿 8）、lib API 函数 119、真实调用点 120、后端覆盖率 ≈78%。

### 1.2 后端 API 层（17 域 / 142 端点）

`backend/app/main.py`（2648 行，内置 1 个 APIRouter 52 条）+ 16 个 router 文件（14 个真路由，2 个是 CLI monkey-patch）。

| 域 | 端点 | 代表接口 |
|---|---|---|
| D1 项目/Episode/资产 | 12 | `POST /api/projects`、`PATCH /api/assets/{aid}` |
| D2 角色工坊 + H3 | 10 | `POST /api/characters/{c}/concept/generate`、`/h3-animate` |
| D3 台本生成 | 1 | `POST /api/projects/{pid}/generate-script` |
| D4 分镜/镜头/版本 | 9 | `…/shots/generate-dynamic`、`…/versions/{vid}/select` |
| D5 渲染/Job | 4 | `POST /api/episodes/{eid}/render`、`POST /api/jobs/{jid}/retry` |
| D6 导出/本地化 | 4 | `/export`、`/localize`、`/localizations/{lid}` |
| D7 发布 | 5 | `/publish/preflight`、`POST …/publish`、`/publishes/{pid}` |
| D8 设置/LLM/成本/SSE | 7 | `/api/settings`、`/api/llm/test`、`/api/events` |
| D9 V5 契约库 | 5 | `GET /api/v5/catalog`、`GET/PUT /api/v5/{kind}/{item_id}` |
| D10 TTS 音色 | 7 | `/api/v5/voices`、`/casting`、`/voice/{c}/lock` |
| D11 V5 创建 | 6 | `POST /api/v5/templates`、`DELETE /api/v5/characters/{c}` |
| D12 画廊/剪辑/分镜/渲染 | 7 | `/api/v5/outputs`、`/editorial/{t}`、`POST /api/v5/render` |
| D13 角色衍生内容 | 16 | audition / promo / dialogue / showcase |
| D14 系列 | 3 | `GET /api/v5/series/{sid}`、`POST …/export` |
| D15 V6 Agent | 10 | `POST /api/v6/agent/run`、`PUT …/workflow/{t}` |
| D16 Cinema3D + 模板 | 22 | `/api/cinema/report`、`POST /cinema/templates/{tid}/publish-package` |
| D17 流水线编排 | 9 | `POST /api/pipeline/run`、`/pipeline/batch` |

### 1.3 生产流水线层（88 个模块 / 50,307 行）

| 阶段 | 模块（行数） |
|---|---|
| 分镜/故事 | `template_v3`(762)、`v5_story`(240)、`storyboard_v3`(898)、`shot_grammar`(269)、`action_library`(539)、`editorial`(625) |
| 声音 | `tts_engine`(122)、`tts_breeze`(410)、`voiced`(454)、`v5_voices`(292)、`v5_audio`(422)、`mouth_sync`(113)、`lipsync`(910) |
| 剪辑 | `editing`(645)、`timeline`(191)、`edit_v4`(231)、`ass_builder`(247) |
| 2D | `scene_render`(**4242**)、`motion2d`(**3598**)、`style_v5`(**3180**)、`v5_art`(2133)、`render_v5`(1115)、`render_v3`(1140) |
| 3D / H3 | `stage`(1322)、`h3_comfy`(775)、`templates3d_hq`(**882**)、`cinema3d_assemble`(**1597**)、`templates3d`(650) |
| 分镜表 / QC | `storyboard_v5`(225)、`qc`(130)、`qc_v3`(557)、`qc_v5`(280) |
| 发布 | `capcut`(742)、`localize`(1269) |
| V6 编排 | `roles`(73)、`registry`(253)、`workflow`(105)、`orchestrator`(194)、`ops`(172)、`art`(120)、`cli`(158) |

### 1.4 数据合同层（353 个 JSON，v5 占 295）

| 类别 | 数量 | 说明 |
|---|---|---|
| templates | 21 内建 + 12 custom = **33** | 一个模板 = 一个节目（V41 定调） |
| scenes | 73 + 1 = **74** | 分层场景（paper/grid/street…），带 camera_zones/anchors |
| characters | 24 + 26 = **50** | 每个角色带 voice（breeze preset + seed/cfg）+ 三视图/表情/姿态资产 |
| styles | 16 + 1 = **17** | 水墨/版刻/羊皮纸/霓虹/全息/VHS/胶片/黑色电影/油画/巴洛克/蓝图/糖果/黏土/粉笔/绢本 + 赛博电影感 |
| editorial | **16** | 每模板独立剪辑档案（pacing/运镜/转场/J-L cut） |
| props / packs | 44 / 2 | 道具与风格包 |
| series | **1** | `undercity_arc` 三部曲 |
| v6 workflows | **16** | story/voice/editorial/shots/assemble/storyboard/qc/publish 全开；**art 0/16、h3 0/16 全关** |
| cinema3d templates | 46 | 3D 镜头级模板 |

### 1.5 智能体编排层（V6，⚠️ 有后端无 UI）

- 8 角色智能体（producer/story_architect/voice_director/art_director/editor/video_director/qa/publisher）+ 9 工具 + 16 份可编辑工作流。
- 证据文件：`outputs/agent_runs/<run_id>/{run.json,events.jsonl,summary.md}`。
- **现状：`/api/v6/agent/*` 10 个端点前端零引用、无导航入口；AGENTS.md 描述的"智能体工作台"只能 CLI 用。** 8 个 run 全部是 `history_talk`（ok×4 / failed×1 / planned×3）。

### 1.6 交付与发布层

| 能力 | 入口 | 产物 |
|---|---|---|
| 成片渲染 | 成片房 / 剪辑台 / 一键成片 | `outputs/templates_{2d,3d}/<tpl>/final.mp4` |
| 系列串片 | 成片库系列卡 | `outputs/series/undercity_arc/series.mp4`（49.2s） |
| 导出合集 | `POST /api/v5/series/{sid}/export` | `series.mp4 + manifest.json + poster.jpg + title_card.mp4 + concat.txt` |
| 发布包 | 发布包弹窗 / 批量 | 视频+封面+字幕+元数据 zip，含竖版 9:16 |
| 多语种 | 成片房 | EN/ES/JA/KO/PT（Ollama 翻译 + NLLB 回退） |
| 剪映草稿 | 成片房 | `pipeline/capcut.py`（pyJianYingDraft） |
| 海外发布 | 成片房 | Upload-Post（YouTube/TikTok/IG，缺社交账号授权） |

### 1.7 平台设施层

FastAPI + SQLite（`backend/data/zaopianju.db`）+ 后台线程 Job（DB 与 `backend/data/jobs/*.json` 双轨）+ 进程内 `threading.Lock`（20+ 处）+ SSE `/api/events`（前端未接，改用轮询）+ 设置/LLM 配置 + 费用单价与统计。

---

## 2. 已交付资产（实测，非文档声称）

| 产物 | 数量 | 证据 |
|---|---|---|
| 3D 成片 | **30/30** | `outputs/templates_3d/*/final.mp4` |
| 2D 短片成片 | **9/9** | `outputs/templates_2d/*/final.mp4`（V45×4 + V46×5） |
| V3 样片 | **18/18** | `outputs/templates_v3/*/final.mp4` |
| V5 流水线成片 | **1/34** ❌ | 只有 `relic_talk_3d/final.mp4`（91.6MB） |
| **5 分钟级长片** | **2 部** | `tonight_show_5min` 299.16s / 65.8MB；`relic_talk_3d` 325.20s / 91.6MB（均 1280×720） |
| 系列合集 | 1 部 49.2s | `outputs/series/undercity_arc/`（14.12+13.68+19.14+2.2 片头） |
| 项目成片 | 3 部 | `outputs/episodes/ep_001` 等（26MB） |
| 角色资产 | 25 角色 × (立绘/2.5D/写实/三视图/4 表情/2 姿态) | `assets/v5/characters/<id>/` |
| 总计 | **≈13.5 GB** | outputs 全量 |

---

## 3. 版本轨迹（V3 → V48.1，13 天 172 次提交）

| 阶段 | 版本 | 主线 | 关键产出 |
|---|---|---|---|
| **引擎期**（10/26–10/27） | V3–V6.7 | 模板即节目、语音、风格世界、3D 重设计 | 16 模板 v3 全 H3、Breeze TTS、16 风格、5 分钟《国宝》全 3D |
| **平台化期**（10/31–11/2 上午） | V7–V20 | Cinema Studio、项目大厅、发布闭环、信息架构统一 | 15 页面骨架、Upload-Post、V20 统一 IA |
| **创作工具期**（11/2 下午–11/4 上午） | V21–V40 | 可视化创建模板/角色、批量、发布包、试镜/对戏/合集、5 分钟一致性片 | 创建向导、角色试镜与宣传片、竖版、切条包、5 分钟肖像片 |
| **2D 化 + 系列期**（11/4 下午–11/5） | V41–V48.1 | 节目类型 2D/3D/写实、2D 改 AI 生成、4 种 2D 风格、赛博电影感、双人对戏、系列 + 导出合集 | 9 部 2D 短片、5 种 2D 子风格、`undercity_arc` 三部曲 49.2s |

节奏：`10-24→11-05` 每天 5–35 次提交；`11-04` 单日 35 次（V43→V46）。**执行强度没有问题。**

---

## 4. 与原始计划（`docs/ai-variety-platform-plan.md`，2026-10-23）逐条对照

| 计划项 | 原定 | 实际 | 判定 |
|---|---|---|---|
| P0 技术验证 | 60–90s 样片（H3+TTS+字幕+FFmpeg） | 16 模板 30–50s 全 H3 样片、`templates_v3` 18 部 | ✅ 超额 |
| P1 核心闭环 | 单集 3–5 分钟粗剪 | `tonight_show_5min` 299s、`relic_talk_3d` 325s | ✅ 达成 |
| P2 成片能力 | 第一集 **6–8 分钟**成片 + 成本报告 | 5 分钟级 2 部；6–8 分钟无；成本只有单价+局部统计 | ⚠️ 差一档 |
| P3 产品化 | 3 集连续生产验证 | 33 模板批量、30 部 3D 成片；但"连续剧集"仅 49s 系列拼片 | ⚠️ 形态变了 |
| MVP 模式 | 横屏圆桌/访谈 | `roundtable` 等 16 模板 + 3 写实 + 12 custom | ✅ |
| 交付规格 | **1080P** 16:9 | **全部 1280×720**（实测 4 部代表片） | ❌ 未做 |
| 单集镜头量 | 70–100 镜 | 常规模板 8–9 幕/44–50s；长片模板 54 幕/358s | ⚠️ 两极分化 |
| 一切可回滚 | 提示词/参考图/参数/输出版本 + 时间线回滚 | 有 shot 版本、keyframe picks、`run_id` 证据；**时间线无回滚** | ⚠️ 部分 |
| 成本可见 | 任务级 provider/时长/单价/费用 + 预算告警 | 有 `/api/jobs/costs` 与单价设置；无预算告警 | ⚠️ 部分 |
| 口型 | MVP 规避优先 | H3 ref2va 原生音驱动 + viseme，已超计划 | ✅ |
| 模板包装是护城河 | 片头/花字/音效/转场/调色/字幕模板化 | 16 份 editorial + 37 种 xfade + ASS 包装 | ✅ |
| 合规内置 | AI 标识/授权清单 | 未见显式实现 | ❌ |

**结论：P0/P1 达成，P2 差一档（6–8 分钟没做、1080P 没做），P3 被"多模板批量"替代了"多集连续"。**

---

## 5. "现在是否按计划在做"——分三层回答

### 5.1 节奏层：**是**
- 76 份版本文档、172 次提交、每版「一个诉求 → 一份文档 → 一次提交」；
- V41→V48.1 连续 8 版全部落在同一主线（2D 节目 + 风格 + 系列），没有反复横跳；
- 每版都有实测产物（成片/截图/日志），不是空文档。

### 5.2 方向层：**否（已偏离原始计划，且没有新计划文件）**
- 原始计划的交付物是 **5–10 分钟 / 1080P / 70–100 镜的单集**；
- V41 之后的主线变成 **4–6 幕 / 13–19 秒的 2D 片段**，最近 11 个模板全部 ≤19.14s；
- 唯一守住长片的是 `relic_talk`(2.5D) / `relic_talk_3d`(3D)，且它们是 V6.3–V6.7 的产物，**V41 之后没有任何新增长片**；
- `docs/` 下没有 `PLAN_V49` 或等价文件，最新文档 V46 也没有「下一步」章节 —— **计划已经不在纸面上，只在对话里**。

### 5.3 验收层：**否（门是红的：5 failed / 486 passed / 36 skipped）**

实测 `python -m pytest tests -q`（33.6s），红线 5 条，根因只有 4 个：

| 失败用例 | 直接原因 | 属于哪次改动 |
|---|---|---|
| `test_v5_catalog::test_real_catalog_all_green_and_quantities` | strict 校验 2 条 warning：`relic_talk`/`relic_talk_3d` 缺 `packaging` | V6.3–V6.7 长片模板一直没补 packaging |
| `test_v5_acceptance::test_catalog_contract` | 同上（另一处断言） | 同上 |
| `test_v5_catalog::test_real_catalog_smoke` | 断言 `custom/characters/custom_lin.json` 存在，实际已随 V19 角色清理删除 | 测试未同步 |
| `test_v5_2_acceptance::test_character_art_assets` | `custom_old_inventor`/`custom_street_kid` 缺 `front.png`/`cutout.png`/`face_norm`；`caocao` 工作区版 meta 缺 `face_norm` | **V47 新角色只做了立绘**；caocao 是未提交的临时重生成 |
| `test_v5_2_acceptance::test_frontend_api_live` | `outputs.count=21 != 16`（硬编码旧基线）+ `/media/v5/tonight_show/final.mp4 -> 404`（V11 旧片已清理） | V5.2 验收基线整体陈旧 |

**共同根因：产品从 16 模板扩到 33、从长片扩到 2D 短片之后，验收脚本里写死的数量/路径/资产清单没跟着改。** 不是"代码坏了"，是"计划与验收脱钩"。

---

## 6. 当前红灯清单（按优先级）

| # | 问题 | 证据 | 影响 |
|---|---|---|---|
| R1 | 契约 strict 校验失败：`relic_talk` / `relic_talk_3d` 无 `packaging` | 模板 JSON 无该键；测试实测报 2 warning | 长片模板不能算"可交付" |
| R2 | `test_real_catalog_smoke` 断言 `custom/characters/custom_lin.json`——文件已不存在（角色清理时删除，测试未同步） | `contract/v5/custom/characters/` 26 个文件无 custom_lin | 测试陈旧，误报红 |
| R3 | `outputs/templates_v5` 34 目录**仅 1 个 final.mp4**；`GET /api/v5/outputs` 返回 **21 条，全部 `final_url` 为空、`qc_ok:true`** | 接口实测 | 旧 V5 画廊是「21 条无片的画廊」，且没有一处报错；全站可见的 30 部成片在 `templates_3d`（另一条链） |
| R4 | **V6 Agent 层零 UI**（10 端点、16 工作流、9 工具全部只能 CLI） | 前端 grep `/api/v6` = 0 命中；无导航项 | AGENTS.md/README 的核心承诺不可达 |
| R5 | `/api/episodes/{eid}/shots/generate-dynamic` 断链：`jobs.py` 调 `python -m pipeline.dynamic`，而 `pipeline/dynamic.py` **没有 main/argparse** | `jobs.py:747-761` TODO(task-31) | 动态镜生成必失败 |
| R6 | 142 个端点**无鉴权**、`allow_origins=["*"]`、并发靠进程内锁 | main.py:207、全库 0 个 Security 依赖 | 本地 demo 可接受，对外即事故 |
| R7 | 三套渲染栈并存（`templates_v3` / `templates_v5` / `templates_3d`），同模板最多 3 份不同成片，无 delivery manifest | outputs 结构 | 不知道该信哪份"最新成片" |
| R8 | 前端死代码 3,808 行（16.5%）：`cinema/Overview|Final`、`studio/Templates3D`、`v5world/*` 5 个组件 | 前端审计 | 维护成本 + 误导后来者 |
| R9 | 20 余处「接口尚未就绪（V21/V25/…实现中）」文案全是 stale，功能早已可用 | 前端审计命中清单 | 用户被告知"做不了"，实际能做 |
| R10 | 文档落后：README 停在 V6、AGENTS.md 描述 V6 编排、V47/V48 无独立文档（塞在 V46 里） | 文档树 | 新人按文档操作会走错路 |
| R11 | `outputs/templates_v3/progress.json` 记 19 模板，磁盘实际 30 | 产物对照 | 状态源不可信 |
| R12 | 测试覆盖与体量脱节：`pipeline/agents`(950 行)、`cinema3d_assemble`(1597 行) 零测试 | 测试统计 | 编排层改动无网 |
| R13 | V47 两个新角色（老秦/小满）只有 `portrait.png`，缺 `front/cutout/face_norm` | 资产盘点 + 验收测试实测 | 模板能出片，但试镜/口型 overlay 缺料，角色资产合同不完整 |
| R14 | 工作区脏且会踩雷：`caocao` 的 meta/portrait/three_view 未提交，新 meta 没有 `face_norm` | `git status` + 测试实测 | 直接提交会把角色资产验收测试打红，要么补 `face_norm` 要么回滚 |
| R15 | 旧 V5 画廊链是死链：`/api/v5/outputs` + `v5world/*` 5 个组件都不在导航里，返回的 21 条成片全无 `final_url` | 接口 + 前端审计 | 想看成片只能走 `#/library/final`（cinema 链），两套画廊并存 |

---

## 7. 建议：把"计划"重新钉死的 6 步

| 步骤 | 动作 | 工作量 | 产出 |
|---|---|---|---|
| ① 先把门修绿（5 红） | a) `relic_talk`/`relic_talk_3d` 补 `packaging`；b) 删/改 stale 的 `custom_lin` 断言；c) 给老秦/小满补 `front/cutout/face_norm`（或把断言收窄为"有立绘即可"）；d) V5.2 基线 16→33、旧的 `/media/v5/*` 改 `/media/templates_3d/*` | 1 天 | `pytest tests -q` → 0 failed |
| ② 定主线（二选一，必须落文件） | **A 长片线**：把 `relic_talk` 打成可复制产品（6–8 分钟 + 1080P）；**B 短片线**：承认短剧形态，把 series 做成"一季 N 集 + 导出合集" | 1 天出 `PLAN_V49` | 一份可验收的里程碑表 |
| ③ 收敛渲染栈 | 三套 → 一套（建议 `templates_3d` 为唯一入口，`templates_v5` 只留长片） | 1–2 天 | delivery manifest（谁是最新成片） |
| ④ 恢复交付规格 | 720p 成片 → 1080P（源 1280×720 可上采样，或关键镜 1024×576→更高） | 视主线 | 符合原计划 1.1 |
| ⑤ Agent 层二选一 | 上线（前端加入口）or 下线（删 10 端点，AGENTS.md 改口径） | 1 天 | 不再有"写了没入口"的模块 |
| ⑥ 文档对齐 | README 补到 V48、V47/V48 独立文档、AGENTS.md 与真实入口一致 | 0.5 天 | 文档可信 |

---

## 8. 结论（给决策用）

1. **功能不缺**：从选模板 → 生成角色/场景 → 配音 → 剪辑档案 → 2D/3D 渲染 → 合成 → 分镜表 → QC → 发布包 → 系列合集，全链路有代码、有产物、有页面。
2. **计划失焦**：最近 12 版在把"长片平台"做成"短片工作室"，方向是用户点单驱动的，但**没有一份文件承认这次转向**。
3. **验收失守**：全量测试 5 红 / 486 绿；V5 画廊 21 条无片；长片模板缺 packaging。
4. **当下最值钱的一件事**：不是再加一个风格，而是**①修绿验收门 ②写 `PLAN_V49` 把主线钉死**。其余（渲染栈、1080P、Agent 层、文档）都排在这两件之后。

---

## 附录 A · 复现命令（本次审计全部结论都可重跑）

```powershell
# 1) 全量测试（本次：5 failed / 486 passed / 36 skipped）
python -m pytest tests -q

# 2) 契约校验（strict 下会报 relic_talk / relic_talk_3d 缺 packaging）
python -m pipeline.v5_cli validate

# 3) 规模盘点
(Get-ChildItem contract -Recurse -File -Filter *.json).Count          # 353
(Get-ChildItem pipeline -Recurse -File -Filter *.py | Get-Content | Measure-Object -Line).Lines  # ~50k
(Get-ChildItem outputs\templates_3d -Directory | ? { Test-Path (Join-Path $_.FullName 'final.mp4') }).Count  # 30

# 4) 在跑的服务（8090）
Invoke-WebRequest http://127.0.0.1:8090/api/v5/series   -UseBasicParsing | % StatusCode   # 200
Invoke-WebRequest http://127.0.0.1:8090/api/v5/outputs  -UseBasicParsing | % Content | ConvertFrom-Json |
  % { $_.count; $_.outputs[0].final_url }               # 21 / 空

# 5) 成片规格（全部 1280x720）
ffprobe -v error -select_streams v:0 -show_entries stream=width,height,duration -of csv=p=0 `
  outputs\templates_v5\relic_talk_3d\final.mp4        # 1280,720,325.2
```

## 附录 B · 三份明细报告

| 文件 | 内容 |
|---|---|
| [docs/audit/PART_BACKEND.md](audit/PART_BACKEND.md) | 16 个 router、142 端点逐条、17 功能域、后端有前端没接的 9 条、TODO 清单、无鉴权风险 |
| [docs/audit/PART_FRONTEND.md](audit/PART_FRONTEND.md) | 15 页路由表、38 组件（8 孤儿）、119 个 API 函数、覆盖率 78%、stale 文案清单、死代码 3,808 行 |
| [docs/audit/PART_PIPELINE.md](audit/PART_PIPELINE.md) | 88 模块分阶段映射、353 份合同、33 模板清单、528 用例分布、12 条断点（含证据） |

## 附录 C · 版本号与文档号对照（易混点）

| 层次 | 当前值 |
|---|---|
| git 提交号（产品版本） | `v48.1` = `2189202` |
| 文档号 | 最新独立文档 V46（V47/V48/V48.1 折叠在 V46 文档 §4.6–§4.8 内） |
| 合同号 | `contract/v5`（模板/场景/角色）+ `contract/v6`（16 份工作流） |
| 本文档 | V49（功能梳理 + 计划核对，非产品版本） |
