# PART_BACKEND — AiJue 后端能力全貌盘点

> 审计范围：`backend/`（只读，未修改任何源文件）
> 生成时间：2026-11-05
> 统计口径：`@router.{get,post,put,delete,patch}` + `@app.get` 装饰器逐一计数（行号即装饰器所在行）

## 0. 总览

| 指标 | 数值 |
|---|---|
| `backend/app/routers/*.py` 文件数 | **16**（含 `__init__.py`；其中 2 个是 CLI 包装、0 路由） |
| 实际注册路由模块数 | **14**（13 个 router 文件 + `main.py` 内置 `APIRouter`） |
| 端点总数 | **142** = 137 API + 4 个 `/media/v5/*` 静态媒体 + 1 个 SPA 根 `/` |
| 方法分布 | GET 76 · POST 52 · PATCH 7 · PUT 3 · DELETE 4 |
| 功能域数 | **17** |
| 后端 python 文件总数 | 29（routers 16 + app 根 13） |
| `main.py` 体量 | 2648 行 / 95.9 KB；52 个 `@router.*` + 1 个 `@app.get` |

## 1. routers/ 文件清单与端点统计

| 文件 | 行数 | APIRouter prefix | 端点 | 方法分布 | 实际路径示例 |
|---|---|---|---|---|---|
| `v6_agent.py` | 222 | 无（路径写全） | 10 | GET 6 / POST 3 / PUT 1 | `/api/v6/agent/overview`、`/api/v6/agent/run`、`/media/agent_runs/{run_id}/{file}` |
| `cinema_templates.py` | 2734 | `/api/cinema/templates` | 8 | GET 5 / POST 3 | `GET ""`、`/status`、`/generate`、`/assemble`、`/{tid}/publish-package` |
| `cinema3d.py` | 1236 | `/api/cinema` | 10 | GET 6 / POST 3 / PATCH 1 | `/overview`、`/shots`、`PATCH /shots/{sid}`、`/generate`、`/assemble`、`/workers` |
| `pipeline_ui.py` | 1297 | `/api` | 9 | POST 5 / GET 4 | `/pipeline/run`、`/pipeline/run/{job_id}/cancel`、`/pipeline/batch`、`/projects/{pid}/apply-template` |
| `v5_studio.py` | 768 | 无（路径写全） | 13 | GET 10 / PUT 1 / POST 1 / DELETE 1 | `/api/v5/outputs`、`/api/v5/render`、`DELETE /api/v5/render/{job_id}`、`/media/v5/{template}/{file_path:path}` |
| `v5_catalog.py` | 666 | `/api/v5` | 10 | GET 6 / POST 3 / PUT 1 | `/catalog`、`/{kind}/{item_id}`、`/validate`、`/preview/{kind}/{item_id}`、`/voice/{character}/lock` |
| `v5_create.py` | 1475 | `/api/v5` | 6 | POST 3 / DELETE 2 / GET 1 | `POST /characters`、`DELETE /characters/{character}`、`POST /templates`、`POST /characters/{character}/art` |
| `v5_series.py` | 184 | `/api/v5` | 3 | GET 2 / POST 1 | `/series`、`/series/{sid}`、`POST /series/{sid}/export` |
| `publish_batches.py` | 373 | `/api/cinema` | 4 | POST 2 / GET 2 | `/publish-packages/batch`、`/publish-packages/batches` |
| `character_audition.py` | 893 | 无（路径写全） | 4 | POST 2 / GET 2 | `POST /api/v5/characters/{character}/audition`、`GET /api/auditions/{job_id}` |
| `character_promo.py` | 803 | 无（路径写全） | 4 | POST 2 / GET 2 | `POST /api/v5/characters/{character}/promo`、`GET /api/promos/{promo_id}` |
| `character_dialogue.py` | 848 | 无（路径写全） | 4 | POST 2 / GET 2 | `POST /api/v5/characters/dialogue`、`GET /api/v5/dialogues` |
| `character_showcase.py` | 647 | 无（路径写全） | 4 | POST 2 / GET 2 | `POST/GET /api/v5/character-showcase/publish-package`、`.../split-packages` |
| `custom_2d_runner.py` | 68 | — | **0** | — | 不是路由：`python -m` 式 CLI 包装（patch `v5_catalog.load_catalog`） |
| `custom_hq_runner.py` | 39 | — | **0** | — | 不是路由：CLI 包装（patch `templates3d.load_template`） |
| `__init__.py` | 2 | — | **0** | — | 空包标记 |
| **合计** | — | — | **89** | — | — |

> 注：`custom_2d_runner.py` / `custom_hq_runner.py` 放在 `routers/` 里但既不是 router 也未被 import，属于**目录错置**的 CLI 工具。

## 2. main.py 挂载拓扑

`app = FastAPI(...)` → `app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=False)`（main.py:204-211，demo 无鉴权）→ `router = APIRouter(prefix="/api")`（main.py:213）。

### 2.1 `include_router`（14 次调用，main.py:2547-2571）= 13 个外部 router 模块 + 1 个 main 内置 `router`

| 行 | 语句 | 前缀 |
|---|---|---|
| 2547 | `app.include_router(router)` | `/api`（main.py 内置 52 端点） |
| 2549 | `character_dialogue_router` | `/api/v5/dialogues`、`/api/v5/characters/dialogue` |
| 2550 | `character_showcase_router` | `/api/v5/character-showcase` |
| 2552 | `v5_studio_router` | `/api/v5/*` + `/media/v5/*` |
| 2554 | `v5_series_router` | `/api/v5/series` |
| 2555 | `v5_catalog_router` | `/api/v5` |
| 2557 | `v6_agent_router` | `/api/v6/agent`、`/media/agent_runs` |
| 2559 | `cinema3d_router` | `/api/cinema` |
| 2561 | `cinema_templates_router` | `/api/cinema/templates` |
| 2563 | `publish_batches_router` | `/api/cinema/publish-packages` |
| 2565 | `character_audition_router` | `/api/v5/characters/*/audition`、`/api/auditions/*` |
| 2567 | `character_promo_router` | `/api/v5/characters/*/promo`、`/api/promos/*` |
| 2569 | `pipeline_ui_router` | `/api/pipeline`、`/api/projects/{pid}/apply-template` |
| 2571 | `v5_create_router` | `/api/v5/characters`、`/api/v5/templates` |

**顺序敏感**：2548 注释明确 `character_dialogue`/`character_showcase` 必须早于 `v5_catalog`，否则被 `/api/v5/{kind}/{item_id}` 抢占（v5_catalog.py:187 的通配路由）。这是当前唯一的真实路由冲突隐患点。

### 2.2 直接挂载 / 非 router

| 类型 | 位置 | 内容 |
|---|---|---|
| `@app.get("/")` | main.py:2645 | SPA 占位页（`frontend/dist` 缺失时返回 `PLACEHOLDER_HTML`） |
| `app.mount("/media")` | main.py:2591 | `MediaStaticFiles(OUTPUTS_DIR)`，覆写 `.zip` → `application/zip` |
| `app.mount("/")` | main.py:2638 | `SPAStaticFiles(FRONTEND_DIST, html=True)`，404 回落 `index.html`（`/api*` 除外） |

## 3. 功能域（17 个）

| # | 功能域 | 端点 | 代表端点 | 宿主文件 |
|---|---|---|---|---|
| D1 | 健康 / 项目 / Episode / 资产 CRUD | 12 | `GET /api/health`、`POST /api/projects`、`GET/PATCH /api/episodes/{eid}`、`PATCH /api/assets/{aid}` | main.py:500-764 |
| D2 | 角色工坊（角色 CRUD、立绘生成、概念图、H3 动画、素材扫描） | 10 | `POST /api/characters/{cid}/generate`、`POST /api/characters/{cid}/concept/generate`、`POST /api/characters/{cid}/portrait/from-concept`、`POST /api/characters/{cid}/h3-animate`、`GET /api/characters/{cid}/files` | main.py:821-1242 |
| D3 | 脚本生成（LLM 台本） | 1 | `POST /api/projects/{pid}/generate-script` | main.py:1768 |
| D4 | 分镜 / 镜头 / 多版本 / 动态镜 | 9 | `POST /api/episodes/{eid}/shots`、`PATCH /api/shots/{sid}`、`POST /api/shots/{sid}/versions/{vid}/select`、`POST /api/episodes/{eid}/shots/generate-dynamic`、`POST /api/episodes/{eid}/storyboard/generate` | main.py:1330-1555 |
| D5 | 渲染 / Job 队列 / 重跑 | 4 | `POST /api/episodes/{eid}/render`、`GET /api/jobs`、`GET /api/jobs/{jid}`、`POST /api/jobs/{jid}/retry` | main.py:1556-1604 + jobs.py |
| D6 | 导出 / 本地化 | 4 | `POST /api/episodes/{eid}/export`、`POST /api/episodes/{eid}/localize`、`GET /api/localizations/{lid}` | main.py:1255-1326 |
| D7 | 发布（upload-post） | 5 | `GET /api/publish/config`、`GET /api/episodes/{eid}/publish/preflight`、`POST /api/episodes/{eid}/publish`、`GET /api/publishes/{pid}` | main.py:1952-2354 |
| D8 | 设置 / LLM / 成本 / SSE | 7 | `GET/PATCH /api/settings`、`GET /api/llm/config`、`POST /api/llm/test`、`GET /api/episodes/{eid}/costs`、`GET /api/events`(SSE) | main.py:1712-2507 |
| D9 | V5 契约库（模板/场景/角色/道具 读写 + 校验 + 预览） | 5 | `GET /api/v5/catalog`、`GET/PUT /api/v5/{kind}/{item_id}`、`POST /api/v5/validate`、`GET /api/v5/preview/{kind}/{item_id}` | v5_catalog.py:81-370 |
| D10 | TTS 音色（音色表 / 选角 / 锁定 / 试听 / 保存） | 7 | `GET /api/v5/voices`、`GET /api/v5/casting`、`GET/PUT /api/v5/voice/{character}`、`POST /api/v5/voice/{character}/lock`、`POST .../preview` | v5_catalog.py:426-565、v5_studio.py:493-540 |
| D11 | V5 可视化创建（角色/模板增删 + 素材批生成） | 6 | `POST /api/v5/characters`、`DELETE /api/v5/characters/{character}`、`POST /api/v5/characters/{character}/art`、`POST /api/v5/templates`、`DELETE /api/v5/templates/{template}` | v5_create.py |
| D12 | V5 成片画廊 / 剪辑档案 / 分镜 / 渲染队列 | 7 | `GET /api/v5/outputs`、`GET /api/v5/editorial/{template}`、`GET /api/v5/storyboard/{template}`、`POST/GET/DELETE /api/v5/render[/{job_id}]` | v5_studio.py:246-766 |
| D13 | 角色衍生内容（试镜 / 宣传片 / 双人对戏 / 合集发布包） | 16 | `POST /api/v5/characters/{character}/audition`、`POST .../promo`、`POST /api/v5/characters/dialogue`、`POST/GET /api/v5/character-showcase/publish-package` | character_audition/promo/dialogue/showcase.py |
| D14 | 系列剧集（合集导出） | 3 | `GET /api/v5/series`、`GET /api/v5/series/{sid}`、`POST /api/v5/series/{sid}/export` | v5_series.py |
| D15 | V6 Agent 编排（工作流读写 / run / edit / art / pose） | 10 | `GET /api/v6/agent/overview`、`PUT /api/v6/agent/workflow/{template}`、`POST /api/v6/agent/run`、`POST /api/v6/agent/edit`、`POST /api/v6/agent/pose` | v6_agent.py |
| D16 | Cinema 3D 工作室 + 16 模板 3D 化 + 发布包/批量导出 | 22 | `GET /api/cinema/overview`、`POST /api/cinema/generate`、`GET /api/cinema/report`、`GET /api/cinema/templates`、`POST /api/cinema/templates/{tid}/publish-package`、`POST /api/cinema/publish-packages/batch` | cinema3d.py + cinema_templates.py + publish_batches.py |
| D17 | Pipeline 全流程编排（3D 模板队列 / 批量 / 项目切模板） | 9 | `POST /api/pipeline/run`、`GET /api/pipeline/run/{job_id}`、`POST /api/pipeline/batch`、`GET /api/pipeline/batches`、`POST /api/projects/{pid}/apply-template` | pipeline_ui.py |

额外基础设施（不计入 17 域）：4 个 `/media/v5/*` 媒体端点（v5_studio.py:415-448）+ 1 个 `@app.get("/")`。D1-D8 合计 52（= main.py 内置 router），D9-D17 合计 85，+4 媒体 +1 SPA = **142**。

### QC 的位置（无独立端点）

QC 不是 API，而是产物字段：`v5_studio.py:163-235` 读 `qc_report.json` 聚合成 `qc_ok/qc_checks/qc_total/qc_url`；`cinema3d.py:1149-1202` `_build_qc()` 塞进 `GET /api/cinema/report`；`cinema_templates.py:2409-2518` 在发布包 `metadata.json`/`README` 里写 QC 摘要。QA 硬门在 `pipeline/qc*.py`，后端只做只读呈现。

## 4. 后端已实现 / 前端未接 与 半成品

### 4.1 前端零引用（`grep -r frontend/src` 无任何命中）

| 端点 | 文件:行 | 证据 |
|---|---|---|
| **V6 Agent 全量 10 个**：`GET /api/v6/agent/overview`、`GET/PUT /api/v6/agent/workflow/{template}`、`POST /api/v6/agent/run`、`GET /api/v6/agent/runs`、`GET /api/v6/agent/runs/{job_id}`、`POST /api/v6/agent/edit`、`GET /api/v6/agent/art`、`POST /api/v6/agent/pose`、`GET /media/agent_runs/{run_id}/{file}` | v6_agent.py:61-213 | 前端 `/api/v6`、`agent/` 关键词均 0 命中；`App.tsx` 无 Agent 路由 |
| `POST /api/v5/validate`（整目录 Schema 校验） | v5_catalog.py:148 | 前端无 `/validate` |
| `GET /api/v5/series/{sid}`（系列详情） | v5_series.py:97 | `v5.ts:430-436` 只调 list + export |
| `DELETE /api/v5/characters/{character}` | v5_create.py:930 | 全前端 DELETE 仅 2 处（`/shots/`、`/v5/render/`） |
| `DELETE /api/v5/templates/{template}` | v5_create.py:1450 | 同上 |
| `GET /api/cinema/templates/status` | cinema_templates.py:1733 | 前端无 `templates/status` |
| `GET /api/cinema/templates/{tid}/publish-package`（列出已生成包） | cinema_templates.py:2682 | `cinema.ts:353` 只发 POST；`PublishPackageModal.tsx:91` 只用 POST |
| `POST /api/projects/{pid}/apply-template` | pipeline_ui.py:1257 | 前端无 `apply-template`/`applyTemplate` |
| `GET /api/events`（SSE 推送） | main.py:2511 | 前端无 `EventSource`、无 `/events`；main.py:2509 注释自认「前端默认轮询」 |

### 4.2 半成品 / TODO（真实断链）

| 严重度 | 问题 | 位置 | 说明 |
|---|---|---|---|
| **高** | 动态镜 CLI 是占位，且目标模块无 CLI | `jobs.py:747-761`（`_dynamic_cli_cmd`，`TODO(task-31)`）+ `jobs.py:812/847` | 硬编码 `python -m pipeline.dynamic --spec <json> --out <mp4>`，但 `pipeline/dynamic.py`（489 行）**没有 `main()`、没有 argparse**，暴露的是 `ensure_dynamic_clips()`。故 `POST /api/episodes/{eid}/shots/generate-dynamic` 与 `POST /api/shots/{sid}/generate`（`mode=dynamic`）必然以非 0 退出失败 |
| 中 | 预览渲染可返回 501 | `v5_catalog.py:161-181` | `v5_cli.PreviewNotReady` → 501「渲染模块未就绪」；前端 `previewUrl()` 直接当 `<img src>` 用，会静默裂图 |
| 中 | 目录错置的 CLI 包装 | `routers/custom_2d_runner.py`、`routers/custom_hq_runner.py` | 0 路由、未被 import，靠 monkey-patch `load_catalog`/`load_template` 兜 custom 模板；放在 routers/ 下易被误当作 API |
| 低 | SPA 双分支占位 | `main.py:2597-2647` | `frontend/dist` 缺失时 `/` 返回硬编码 HTML，与真实前端无关联 |
| 低 | 发布平台未连接时静默降级 | `main.py:1963-1976` | upload-post 未连接返回空串占位，preflight 转 warn 不报错，前端可能误以为可发 |

### 4.3 其它观察

- **无鉴权**：`allow_origins=["*"]`(main.py:207) + 全库 `Security(...)/HTTPBearer/OAuth2/api_key_header` 命中 **0**，`Depends()` 只有 `get_db` 一种。142 个端点全部裸奔，含 `POST /api/v5/templates`、`POST /api/cinema/templates/generate`、`POST /api/v5/voice/{character}/lock` 这类可写盘/烧 GPU 的端点。
- **单并发全靠进程内 `threading.Lock`**（20+ 处）：`v5_studio._RENDER_LOCK`(568)、`pipeline_ui:620` 409 守卫、`cinema_templates._PUBLISH_LOCK`(1966)/`_FINAL_LOCK`(613)/`_SHOT_PROBE_LOCK`(962)/`_PIPE_SCAN_LOCK`(1106)、`cinema3d._VALID_LOCK`(303)/`_FINAL_LOCK`(424)、`v6_agent._job_lock`(47)、`v5_create._ART_STATE_LOCK`(69)、`character_showcase._BUILD_LOCK`(40)。多 worker 部署即失效。
- **job 持久层双轨**：DB `Job` 表 + `backend/data/jobs/*.json`（现存 **135** 个 json），`main.py:190-202` 启动时把未完成任务批量标 failed。
- **`v5_catalog` 通配路由** `GET/PUT /api/v5/{kind}/{item_id}` 与 `/api/v5/series/{sid}`、`/api/v5/character-showcase/*` 形状相同，依赖注册顺序（见 §2.1）——后续新增 `/api/v5/<词>/<词>` 端点必须插在 2555 之前。

## 5. 结论

1. **规模**：14 个路由模块、142 个端点、17 个功能域，重心在 V5 2D 模板流水线（D9-D13，41 端点；连 D14 共 44）与 Cinema 3D（D16，22 端点）。
2. **完整度**：D1-D8（V3/V4 内核，52 端点）前后端联调完整；D9-D13、D16、D17 主体已接；**D15（V6 Agent）整个域是纯后端**，前端 0 引用，等于已交付但未上桌。
3. **真实缺口只有一条硬断链**：动态镜（`generate-dynamic` / `mode=dynamic`）指向不存在的 CLI——这是全后端唯一带 task 号的 TODO（`jobs.py:750 TODO(task-31)`）；其余 8 处未接端点属「能力已备、UI 未给入口」，可低成本补齐（删除角色/模板、系列详情、目录校验、发布包列表、templates/status、apply-template、SSE）。
4. **最大系统性风险**：142 个端点无鉴权 + CORS `*`，其中 20+ 个端点可写盘或拉起 GPU/H3 子进程。demo 定位下可接受，但任何对外暴露都必须先加鉴权（当前无中间件可插拔点，需新增依赖）。
