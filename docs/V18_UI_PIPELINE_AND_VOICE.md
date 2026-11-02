# V18：UI 全流程生成控制台 + 角色/声音重设计

> 用户反馈（2026-11-02）：「我的 UI 上也能全流程生成；现在的设计我感觉操作不起来；还有角色、角色的声音，请重新设计好。」
> 目标：把「选模板 → 角色与声音 → 剧本 → 生成 3D → 合成 → 成片/发布」做进平台 UI，所有阶段可点、可见进度、可重试；角色页重做成「角色卡 + 声音卡（音色库/试听/绑定）」。

## 1. 现有能力与缺口（审计结论）

**已有**
- 19 个 3D 模板（16 重做 + 3 写实）与生成/合成接口：`POST /api/cinema/templates/generate`、`POST /api/cinema/templates/assemble`（job 化，后台线程）
- 剪辑/成片：`pipeline.templates3d_hq`（1024×576 T2VA + ref2va 原生口型 + 剪辑 + 海报）
- 声音系统：`contract/v5/voices.json` 24 个 preset（breeze Voice Design）、`pipeline/v5_voices.py`（resolve/lock）、`GET /api/v5/voices`、`GET/PUT /api/v5/voice/{character}`、`/media/v5/voice/<id>.wav`（已有部分试听 wav）
- 角色目录：`GET /api/v5/catalog`（characters/templates/scenes/styles），模板 `cast[]` 含 role→character

**缺口（用户觉得「操作不起来」的原因）**
1. 没有一条「从模板到成片」的可视化流程：Cinema 只按模板批量生成/合成；工作台（剧本/分镜/渲染/成片）是另一套 2D/动态流程，二者割裂
2. 3D 生成耗时长（20–45 分钟/模板），UI 只有 3s 轮询模板状态，没有阶段/逐镜进度、没有取消、没有日志
3. 角色页（studio Assets）只有数据库角色的 voice 下拉（无试听、无音色库）；V5 世界角色页只读展示 voice_id；`previewVoice` 前端有封装但**后端无 preview 接口**（404）
4. 项目切换模板：Cinema Templates3D 页调用 `PATCH /api/projects/{pid} {format_id}` 但格式 id 是 `*_5min`，与模板 id 不对应，体验是「点了没反应/报错」
5. 按钮状态/空态/失败重试不统一

## 2. V18 交付范围

### A. 制片控制台（新页面 `#/pipeline`，导航「一键成片」）
Stepper 六步，全部在页内完成：
1. **选模板**：19 个 3D 模板卡（海报/时长/状态），也可从「我的项目」进入
2. **角色与声音**：模板 cast 自动列出每角色：立绘/名字/人格/当前音色 + 「试听」「换声音」（调音色库弹层，可试听 24 个 preset，保存后写 custom 覆盖）
3. **剧本预览**：逐镜台词/时长/说话人（读 semantic json 或 episode）
4. **生成 3D**：一键运行（复用已有镜头可选）；阶段进度：准备 → 生成镜头 i/N（T2VA/口型）→ 合成 → 海报；可取消、失败重试
5. **成片**：播放器（`/media/templates_3d/<tid>/final.mp4`）、时长/大小、重新合成、打开模板库
6. **发布前检查**：publish preflight 摘要（账号状态如实展示「未连接」），按钮跳设置

侧栏/顶部：**一键全流程**（自动跑 2→5，跳过已有产物），实时日志尾（可选展开）。

### B. 角色与声音页（新页面 `#/characters`，导航「角色与声音」）
- 角色网格：立绘/首字占位、名字、era、persona 摘要、**声音 chip（preset label）**、「试听」按钮（内联播放）、「换声音」
- 角色详情抽屉：资料/外观/概念图入口 + 声音卡（音色库列表：label/描述/seed，试听、应用、恢复默认）
- 批量：全部试听（顺序播放，可停）、按模板筛选 cast
- 保存：`PUT /api/v5/voice/{character}`（写 `contract/v5/custom/characters/<id>.json` 覆盖）

### C. 后端 API（V18）
1. `POST /api/v5/voice/{character}/preview` body `{text?}` → 生成/复用试听 wav（breeze 可用则 breeze，否则 edge-tts fallback），返回 `{ok, character, url, cached}`
2. `GET /api/v5/casting?template=<tid>` → cast 列表（role/character/label/persona/voice{voice_id,label,provider}/portrait_url）
3. `GET /api/v5/voices?with_preview=1`（扩展现有）→ presets + `preview_url`（有 wav 时）
4. `POST /api/pipeline/run` body `{template_id, reuse_shots?: bool}` → `{job_id, template_id, stages}`；
   `GET /api/pipeline/run/{job_id}` → `{status, stage, stage_index, progress:{done,total}, log_tail[], elapsed, final_url, error}`
   `POST /api/pipeline/run/{job_id}/cancel`；`GET /api/pipeline/runs` 最近任务
   实现：子进程 `python -m pipeline.templates3d_hq --template <tid> [--reuse-shots]`，解析日志 `[i/N]` 出进度；阶段映射：prepare/generate/lipsync/assemble/poster/done；单并发（已有运行则 409）
5. `POST /api/projects/{pid}/apply-template` body `{template_id}` → 映射 format：`contract/formats/<tid>_5min.json` 存在则用，否则 `film_interview_5min`/首个可用 + project.template_id 记录（如无字段则写 format_id 映射并返回说明）

### D. 操作逻辑修复
- 所有生成/合成按钮：无产物可点、运行中禁用（显示「运行中 i/N」）、完成显示「重生成/重新合成」、失败显示「重试」
- 页面级错误条（可复制错误文本）；任务取消二次确认
- 空态引导：无成片 → 去生成；无声音 → 去角色页

## 3. 验收
1. 从 UI（或 API 等价）跑通一条 pipeline job：阶段进度可见、完成后成片可播；取消可用
2. 角色页可试听/切换/保存音色（保存后 `GET /api/v5/voice/{character}` 变化）
3. `tsc` 0 error + `vite build`；截图（控制台各步骤、运行中、完成、角色/声音页、音色弹层）
4. docs/V18 + commit；E2E 证据 `tmp/v18/`

## 4. 分工
- **tpl-backend**：C.1–C.5 后端 API（pipeline job + voice preview + casting + apply-template），自测 HTTP + 实跑一个 assemble-only job
- **tpl-frontend**：A/B 两个新页面 + 导航/路由 + D 操作状态，接后端 API，tsc/build，截图
- **studio-a**：角色声音选角方案（32 角色 → 24 preset 映射、每角色试听句、角色页文案），写 `contract/v5/voice_casting.json`（不改代码）
- **Lead**：契约/集成/E2E（真实跑一条 job）/截图复核/docs/commit


## 5. 交付结果（2026-11-02 17:20）

### 已完成
- **后端**（tpl-backend，46/46 HTTP 自测 PASS）：
  - `backend/app/routers/pipeline_ui.py`：`POST /api/pipeline/run`（202 单并发 409）、`GET /run/{id}`（status/stage/stage_index/progress/log_tail/elapsed/final_url/error）、`POST /run/{id}/cancel`、`GET /runs`、`POST /api/projects/{pid}/apply-template`；子进程跑 `pipeline.templates3d_hq`，job 落 `outputs/pipeline_jobs/`
  - `v5_catalog.py`：`GET /api/v5/casting?template=`（19/19 模板）、重写 `POST /api/v5/voice/{character}/preview`（breeze 优先→edge 兜底、缓存、支持 `voice_id` 参数试听音色库）、`/voices` 扩展 provider/preview_url
  - `v5_studio.py`：兼容 `provider=edge` 写实角色，GET/PUT voice 不再 500
- **前端**（tpl-frontend，tsc 0 / build 通过 / 页面 console 无 JS 错误）：
  - `#/pipeline` 制片控制台：6 步（选模板 / 角色与声音 / 剧本预览 / 生成 3D / 成片 / 发布前检查）+ 顶部「一键全流程」+ 阶段 i/N 进度 + 日志尾 + 取消（二次确认）+ 失败重试 + 409 提示
  - `#/characters` 角色与声音：32 角色网格 / 详情抽屉 / 24 preset 音色库弹层（试听·应用·恢复默认）/ 批量全部试听 / 搜索与模板 cast 筛选
  - 路由与导航：平台首页 / Cinema / V5 / 工作台均加「一键成片」入口
- **角色声音选角**（studio-a）：`contract/v5/voice_casting.json`（32 角色 → 24 preset，含 reason 与试听句）+ `contract/v5/custom/characters/*.json` 32 个 voice 覆盖 + `docs/V18_VOICE_CASTING.md`；`validate_catalog` errors 0；22 模板 cast 内 0 撞音
- **流水线复用**：`templates3d_hq --reuse-shots`（已有镜头直接重剪辑；实测 real_street 10s 完成、输出大小一致）

### 验证证据
- API E2E（`tmp/v18/e2e.py`）：**12/12 PASS** —— pipeline run(202)→轮询→done(7/7)→final_url 200；voice preview 200 + wav 200；casting 200；voices 200；PUT/GET voice 生效；apply-template 200
- UI E2E（tpl-frontend 脚本 + Lead Playwright `tmp/v18/ui_e2e_final.py`）：选模板 → 角色试听（触发 `/api/v5/voice/real_host/preview`）→ 剧本 → 复用合成运行中 → 完成 → 成片播放（tonight_show 41s/17.2MB）；取消→已取消；音色应用→GET 验证→恢复默认
- 截图：`tmp/v18/ui/01_pipeline_templates_1680.png` … `12_platform_home_1680.png`（16 张）；Lead 复拍：`tmp/v18/ui/pipeline_step1.png`、`characters_page.png`
- 后端自测报告：`tmp/v18/backend_selftest.md`；前端说明：`tmp/v18/NOTES.md`

### 已知事项 / 建议
1. 真实完整生成（非 reuse）单模板 20–45 分钟：UI 已支持从「生成 3D」发起并展示逐镜进度；本次 E2E 用 reuse_shots 快速验证状态机，真实 GPU 全流程可随时点「开始生成」跑
2. 音色试听：当前环境 breeze 服务未启动 → 回退 edge-tts（音色接近但非 Breeze preset 原始音色）；启动 breeze（local_models/breeze-venv）后自动使用
3. `/voices.preview_url` 现为 null（preset 无独立 wav），音色库试听走 preview 接口 `voice_id` 参数；后续可批量预生成
4. 写实角色 custom 覆盖中 `provider=edge` 与 breeze voice_id 混用：后端已兜底（edge 按 instruction 映射）；如需严格 Breeze 音色，可启动 breeze 服务或统一 provider
5. 项目切换模板：real_* 暂无同名 format，apply-template 回退 `classics_5min`（返回 `mapped=fallback_first`）；如需 exact 映射，可补 `contract/formats/real_*_5min.json`
