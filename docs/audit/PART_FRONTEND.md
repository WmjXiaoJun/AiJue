# AiJue 前端页面与交互能力审计（PART_FRONTEND）

- 审计对象：`frontend/src/`（只读，未修改任何源文件）
- 审计时间：2026-11-05
- 审计范围：页面、路由、组件、API 封装、样式、占位/未就绪文案；后端以 `backend/app/**/*.py` 的路由表做对账
- 结论可信度：所有数字均由文件枚举 + 正则提取得到，路由以 `App.tsx` 的 `parseHash()` 分支为准

---

## 0. 结论速览

| 指标 | 数值 | 说明 |
|---|---|---|
| 页面（可路由挂载） | **15** | 项目大厅 1 + 工作台 5 tab + 3D 制作 3 tab + 一键成片 1 + 角色与声音 1 + 素材/成片库 4 tab |
| 路由（hash 规范路由） | **15** | 无 react-router，全部由 `App.tsx` 自研 hash 解析 |
| 旧 URL 重定向规则 | **13** | 10 条显式映射 + 3 条通配兜底（V20 IA 迁移遗留） |
| 组件文件（.tsx） | **38** | 其中 **30 在调用链内**，**8 为孤儿（3 808 行死代码）** |
| lib API 封装函数 | **119**（+1 唯一、+41 门面重复） | 6 组 API 对象 + `studioApi` 门面 |
| 前端触达的后端端点 | **101 条路径 / ≈110 个 method+path** | 后端 141 条路由（去重 140） |
| lib .ts 文件 | **12** | 11 个 `lib/*.ts` + `types.ts` |
| 样式文件 | **19** 个 CSS / **14 192** 行 | 全部在 `src/styles/` |
| 占位/未就绪文案 | **约 20 处**，**全部与后端现状不符（stale guard）** | 无「敬请期待」「TODO」「即将上线」字样 |

**三句话结论**

1. 前端是一套**自研 hash 路由 + 视图状态机**（`ViewState` 联合类型），不是 react-router；15 条规范路由全部有实现，旧 `#/v5/*`、`#/cinema/overview` 等被 13 条重定向规则兜住。
2. 前端调用的 **101 条后端路径在后端 100% 命中**，不存在「前端已实现、后端缺接口」的硬缺口；真正的问题是相反的——**界面里约 20 条「接口尚未就绪（Vxx 实现中）」提示已过期**，后端早已落地，误导用户与排障。
3. 真正的前端缺口是**可达性**而非接口：`#/cinema/*` 没有全局导航入口；项目模板切换（`PATCH /projects/{pid}` / `POST /projects/{pid}/apply-template`）只有死代码 `Templates3D` 在用；V6 Agent 编排层（10 条路由）**零 UI**，只能在 CLI 用。

---

## 1. 目录结构与规模

| 目录 | 文件数 | 行数 | 职责 |
|---|---|---|---|
| `src/` 根 | 2（App.tsx 490 / main.tsx 18） | 508 | 路由状态机 + 引导屏 + 后端离线重连；入口仅挂 `base.css`/`components.css`/`ui-consistency.css` |
| `src/components/` | 38 | ≈22 660 | 全部页面与业务组件 |
| ├ `platform/` | 1 | 538 | 项目大厅（首页） |
| ├ `studio/` | 7 | 6 473 | 项目工作台外壳 + 5 个 tab + 死页 Templates3D(1 049) |
| ├ `cinema/` | 8 | 3 909 | 3D 制作台 3 tab + 模板库 + 死页 Overview/Final |
| ├ `library/` | 2 | 1 851 | 模板与素材 / 成片库 + 新建模板向导 |
| ├ `pipeline/` | 3 | 1 608 | 一键成片控制台 + 6 步面板 + 步骤条 |
| ├ `characters/` | 2 | 3 184 | 角色与声音页（2 823）+ 新建角色抽屉 |
| ├ `v5world/` | 7 | 2 629 | 风格墙/场景库在用；外壳+4 子页为死代码 |
| ├ `shell/` `publish/` `voice/` | 3 | 1 030 | AppShell 全局外壳、发布包弹层、音色库弹层 |
| └ 根级原子件 | 5 | 1 438 | `SettingsModal`(801) `icons`(281) `ui`(283) `Toast`(37) `ErrorBoundary`(36) |
| `src/lib/` | 12 | 5 251 | `api` `cinema` `creation` `pipeline` `studio` `v5` `voice` `templates` `format` `download` + `types.ts` |
| `src/styles/` | 19 | 14 192 | 19 份全局 CSS（无 CSS Modules / CSS-in-JS / tailwind） |
| **合计** | **71** | **≈42 611** | tsx 23 121（含 App 490 / main 18）+ ts 5 251 + css 14 192 |

样式分层（谁引谁）：`base`+`components`+`ui-consistency` 全局；页面级追加 `platform` / `studio`+`studio-a`+`studio-b`+`studio-3d` / `cinema`+`cinema-shots`+`cinema-final`+`cinema-templates` / `library` / `v18` / `creation` / `settings` / `shell` / `publish` / `v5world`。

---

## 2. 路由机制（已确认：无 react-router）

- `main.tsx` 只做 `createRoot().render(<StrictMode><ErrorBoundary><App/></ErrorBoundary></StrictMode>)`，**没有任何 Router / BrowserRouter / Routes**。
- `App.tsx` 用 `useState<ViewState>` 做视图状态机：

```ts
type ViewState =
  | { mode: "projects" }                                        // → PlatformHome
  | { mode: "workspace"; project: Project; episode: Episode }    // → ProjectStudio（tab 另存）
  | { mode: "cinema" }                                          // → CinemaStudio（tab 从 hash 读）
  | { mode: "pipeline" }                                        // → PipelineConsole
  | { mode: "characters" }                                      // → CharactersPage
  | { mode: "library"; tab: LibraryTab };                       // → LibraryPage（templates|styles|scenes|final）
```

- hash ↔ state 双向同步：`parseHash()`（App.tsx:63-94）解析，`hashFor()`（:96）写回，`hashchange` 监听（:224-271）支持前进/后退与直开链接。
- `legacyRedirectHash()`（:29-54）在每次 boot / hashchange 前执行，**13 条规则**：

| 旧 URL | 新 URL |
|---|---|
| `#/v5/gallery` | `#/library/final` |
| `#/v5/templates` | `#/library/templates` |
| `#/v5/styles` | `#/library/styles` |
| `#/v5/scenes` | `#/library/scenes` |
| `#/v5/characters` | `#/characters` |
| `#/v5/render` | `#/pipeline` |
| `#/cinema/overview` | `#/projects` |
| `#/cinema/templates` | `#/library/templates` |
| `#/cinema/final` | `#/library/final` |
| `#/cinema` | `#/cinema/shots` |
| `#/v5/<任意>` | `#/library/final` |
| `#/cinema/<other>` | `#/library/templates` \| `#/library/final` \| `#/projects` |
| `#/episodes/{id}/templates3d` | `#/episodes/{id}/script` |

- 全局导航（`shell/AppShell.tsx:72-78`）只有 6 项：项目大厅 / 一键成片 / 角色与声音 / 模板与素材 / 成片库 / 设置（设置是 signal 打开弹层，无独立路由）。

---

## 3. 路由表（15 条规范路由 → 页面 → 功能 → 后端依赖）

| # | 页面 | 路由（hash） | 入口组件 | 用户可见功能（1–2 句） | 主要后端依赖 |
|---|---|---|---|---|---|
| 1 | 项目大厅 | `#/projects` | `platform/PlatformHome.tsx` | 续做 hero + 项目卡（含格式/最新期/3D 徽标）+ 最近任务动态 + 新建项目弹窗（选 16 模板）；全站设置弹层唯一入口 | `GET /api/projects`、`GET /api/projects/{pid}/episodes`、`GET /api/jobs`、`GET /api/cinema/templates`、`POST /api/projects`、`GET/PATCH /api/settings` |
| 2 | 工作台 · 编剧间 | `#/episodes/{eid}/script` | `studio/Scripts.tsx` | 台本编辑器（保存）、AI 生成台本（LLM）、一键从台本生成分镜脚本、台本结构速览 | `PATCH /api/episodes/{eid}`、`POST /api/projects/{pid}/generate-script`、`POST /api/episodes/{eid}/storyboard/generate` |
| 3 | 工作台 · 资产库 | `#/episodes/{eid}/assets` | `studio/Assets.tsx`（1 970 行，最大页面） | 角色工坊（列表/筛选/抽屉：概念图 tabs、生成控制、H3 动画、锁定一致性、资料编辑、新建角色）+ 固定资产分组表 + 场景卡 + 图片 lightbox | `GET/POST /api/episodes/{eid}/assets`、`PATCH /api/assets/{aid}`、`GET/POST /api/projects/{pid}/characters`、`PATCH /api/characters/{cid}`、`POST /api/characters/{cid}/generate`、`/concept/generate`、`/portrait/from-concept`、`/h3-animate`、`GET /api/characters/{cid}/files`、`/concepts` |
| 4 | 工作台 · 分镜台 | `#/episodes/{eid}/storyboard` | `studio/Storyboard.tsx` | 镜头表（搜索/状态筛选/排序）+ 镜头抽屉（改台词/时长/视觉/花字，单镜生成、动态 H3 生成、版本切换、删除） | `GET/POST /api/episodes/{eid}/shots`、`PATCH/DELETE /api/shots/{sid}`、`POST /api/shots/{sid}/generate`、`GET /api/shots/{sid}/versions`、`POST /api/shots/{sid}/versions/{vid}/select`、`POST /api/episodes/{eid}/shots/generate-dynamic` |
| 5 | 工作台 · 生成台 | `#/episodes/{eid}/generate` | `studio/Render.tsx` | 任务队列（2 s 轮询）+ 状态/类型筛选 + 搜索 + 失败重试 + 费用分布 + 视频/成片 lightbox | `GET /api/jobs?episode_id=`、`GET /api/episodes/{eid}/costs`、`POST /api/jobs/{jid}/retry`、`GET /api/jobs/{jid}` |
| 6 | 工作台 · 成片房 | `#/episodes/{eid}/finish` | `studio/Finishing.tsx` | 成片播放 + 一键渲染（waitJob 进度）+ 多语种出海 + 导出剪映草稿 + 海外发布（平台/隐私）+ 发布记录 + 发布前检查 | `POST /api/episodes/{eid}/render｜export｜localize｜publish`、`GET /api/episodes/{eid}/localizations｜publishes｜costs`、`GET /api/publish/config`、`GET /api/episodes/{eid}/publish/preflight` |
| 7 | 3D 镜头台 | `#/cinema/shots` | `cinema/Shots.tsx` | 56 镜网格：搜索 / 来源筛选（cloud/local/reuse/missing）/ 批量重生成 / 单镜抽屉改 brief | `GET /api/cinema/shots`、`PATCH /api/cinema/shots/{sid}`、`POST /api/cinema/generate` |
| 8 | 生成队列 | `#/cinema/jobs` | `cinema/Jobs.tsx` | 运行环境状态（ComfyUI / MetaSo 在线性）+ 生成/合成任务列表：进度、实时日志、取消 | `GET /api/cinema/jobs`、`GET /api/cinema/jobs/{id}`、`POST /api/cinema/jobs/{id}/cancel`、`GET /api/cinema/workers` |
| 9 | 剪辑台 | `#/cinema/assemble` | `cinema/Assemble.tsx` | 合成参数（环境声 / 统一影调 / 定向调色）→ 提交合成 → 轮询进度与日志 → 查看最新成片文件 | `POST /api/cinema/assemble`、`GET /api/cinema/jobs/{id}`、`GET /api/cinema/report`、`GET /api/cinema/overview` |
| 10 | 一键成片控制台 | `#/pipeline`（可带 `?template=`，V23 创建即生成） | `pipeline/PipelineConsole.tsx` | 6 步向导：选模板 → 角色与声音 → 剧本预览 → 生成成片 → 成片 → 发布前检查；含批量队列与发布包导出 | `GET /api/v5/catalog`、`GET /api/cinema/templates`、`GET /api/v5/casting`、`GET /api/v5/voices`、`POST /api/pipeline/run`、`GET /api/pipeline/run/{id}`、`POST /api/pipeline/run/{id}/cancel`、`POST /api/pipeline/batch`、`GET /api/pipeline/batches`、`GET /api/settings` |
| 11 | 角色与声音 | `#/characters` | `characters/CharactersPage.tsx`（2 823 行） | 32 角色网格 + 声音 chip/批量试听 + 详情抽屉（资料/外观/概念图/音色库）+ 试镜（机位/风格/背景）+ 角色宣传片 + 双人对戏 + 切条包 + 合集发布包 + 新建角色 | `GET /api/v5/catalog`、`GET /api/v5/{kind}/{id}`、`GET /api/v5/voices`、`PUT /api/v5/voice/{c}`、`POST /api/v5/voice/{c}/preview`、`POST /api/v5/characters/{c}/audition｜promo`、`GET …/auditions｜…/promos`、`GET /api/auditions/{id}`、`GET /api/promos/{id}`、`POST /api/v5/characters/dialogue`、`GET /api/v5/dialogues`、`POST/GET /api/v5/character-showcase/split-packages｜publish-package`、`POST /api/v5/characters` |
| 12 | 模板库（模板） | `#/library/templates` | `library/LibraryPage.tsx` + `cinema/Templates.tsx`（embedded） | 19 模板总表 + 分类/搜索 + 逐模板镜头进度抽屉 + 生成/重新生成 3D + 合成 + 批量选择（批量生成/批量合成、批量队列持久恢复）+ 发布包导出 + 新建模板向导（4 步） | `GET /api/cinema/templates`、`GET /api/cinema/templates/{id}`、`POST /api/cinema/templates/generate｜assemble`、`POST /api/cinema/templates/{tid}/publish-package`、`POST /api/pipeline/batch`、`GET /api/pipeline/batches`、`POST /api/cinema/publish-packages/batch`、`POST /api/v5/templates` |
| 13 | 风格墙 | `#/library/styles` | `v5world/Styles.tsx` | 17 种视觉风格卡（色板/技法/角色处理/转场/预览图）+ 搜索 + 详情节；预览优先用该风格 3D 海报 | `GET /api/v5/catalog`、`GET /api/v5/styles/{id}`、`GET /api/v5/preview/{kind}/{id}` |
| 14 | 场景库 | `#/library/scenes` | `v5world/Scenes.tsx` | 61 个分层绘景场景：缩略图 + style 筛选 + 搜索 + 详情（图层/机位） | `GET /api/v5/catalog`、`GET /api/v5/scenes/{id}`、`GET /api/v5/preview/scene/{id}` |
| 15 | 成片库 | `#/library/final` | `library/LibraryPage.tsx` → `FinalLibrary` | 19 支 3D 成片画廊：播放 / QC 报告 JSON / 重新合成 / 批量导出发布包（平台+竖版两模式）/ 系列串片（片头卡 + 全部集数 mp4） | `GET /api/cinema/templates`、`GET /media/templates_3d/{id}/final.mp4｜report.json`、`POST /api/cinema/publish-packages/batch`、`GET /api/cinema/publish-packages/batches`、`GET /api/v5/series`、`POST /api/v5/series/{sid}/export` |

---

## 4. 次级交互面（弹层 / 抽屉，无独立路由）

| 组件 | 触发点 | 能力 | 后端 |
|---|---|---|---|
| `SettingsModal`（801 行） | AppShell 06「设置」（signal） | 三段：①生成与渲染（生成模式 / TTS 提供方 / 分辨率 / 帧率 / H3 单价 / ComfyUI 地址 / H3 生成方式）②海外发布（Upload-Post API Key / username / enabled / 平台多选 / YouTube 可见性）③AI 模型（provider / model / base url / api key + 连通性测试） | `GET/PATCH /api/settings`、`GET /api/llm/config`、`GET /api/llm/providers`、`POST /api/llm/test` |
| `publish/PublishPackageModal` | 模板库 / 一键成片 | 发布包平台 + 竖版（blur_pad / crop）选择 + 构建进度 + 下载 zip | `POST /api/cinema/templates/{tid}/publish-package` |
| `voice/VoicePicker` | 角色页 / 一键成片 / 步骤面板 | 24 preset 列表、试听、应用、恢复默认音色 | `GET /api/v5/voices`、`GET/PUT /api/v5/voice/{c}`、`POST …/preview` |
| `characters/CreateCharacterDrawer` | 角色页 | 新建角色（label / persona / 外观 11 字段 / 配件 / 角色池） | `POST /api/v5/characters` |
| `library/CreateTemplateWizard` | 模板库 | 4 步新建模板（基本信息 → cast → 分幕 → 预览创建），提交后合成 TTS/episode/semantic brief | `POST /api/v5/templates` |
| `cinema/ShotDrawer` | 3D 镜头台 | 单镜 brief 编辑（主体/运镜/音效/环境） | `PATCH /api/cinema/shots/{sid}` |
| `pipeline/PipelineStepper` | 一键成片 | 6 步状态机步骤条（完成/当前/待办态） | 无 |

---

## 5. 组件清单（38 个 .tsx：30 在链 / 8 孤儿）

| 分组 | 组件 | 行数 | 状态 |
|---|---|---|---|
| 根 | App.tsx | 490 | 路由状态机 |
| 根 | main.tsx | 18 | 入口 |
| platform | PlatformHome | 538 | 路由页 |
| studio | Studio（ProjectStudio 外壳） | 254 | 路由页外壳 |
| studio | Scripts / Assets / Storyboard / Render / Finishing | 558 / 1 970 / 950 / 595 / 1 097 | 5 个 tab 页 |
| studio | **Templates3D** | 1 049 | **孤儿**（Studio.tsx 只渲染 5 个 tab，无 templates3d 分支；App 也有重定向把它转到 script） |
| cinema | CinemaStudio（外壳） | 208 | 路由页外壳 |
| cinema | Shots / Jobs / Assemble | 545 / 463 / 480 | 3 个 tab 页 |
| cinema | Templates | 1 002 | 复用页（LibraryPage embedded） |
| cinema | **Overview** / **Final** | 336 / 562 | **孤儿**（无任何 import） |
| library | LibraryPage / CreateTemplateWizard | 1 287 / 564 | 路由页 + 向导 |
| pipeline | PipelineConsole / steps / PipelineStepper | 605 / 958 / 45 | 路由页 + 6 步面板 |
| characters | CharactersPage / CreateCharacterDrawer | 2 823 / 361 | 路由页 + 抽屉 |
| v5world | Styles / Scenes | 403 / 365 | 路由页（#/library/styles|scenes） |
| v5world | **V5World 外壳 / Gallery / Render / Characters / Templates** | 237 / 301 / 448 / 406 / 469 | **孤儿链**（V5World 无 import，其 4 子页只有它引用） |
| 原子 | icons / ui / Toast / ErrorBoundary / AppShell | 281 / 283 / 37 / 36 / 345 | 全站复用 |

**死代码合计：3 808 行 ≈ tsx 总量 16.5%**（Overview 336 + Final 562 + Templates3D 1 049 + V5World 237 + Gallery 301 + Render(v5) 448 + Characters(v5) 406 + Templates(v5) 469）。

---

## 6. lib API 封装统计

| 文件 | 行数 | API 对象 | 方法数 | 端点前缀 | 备注 |
|---|---|---|---|---|---|
| `lib/api.ts` | 306 | `api` | **50** | `/api/*`（projects / episodes / shots / assets / jobs / characters / publish / llm / settings） | 核心 client；含 4 处 400/422 自动回退（character spec、portrait、h3 provider、dynamic shot）；`generateScript` 兼容 Job 轮询（1.5 s × 120 次） |
| `lib/cinema.ts` | 745 | `cinemaApi`(15) + `publishPackageBatchApi`(4) | **19** | `/api/cinema/*` | 模板/镜头/合成/任务/报告/worker + 发布包批量 |
| `lib/creation.ts` | 796 | `creationApi`(4) `auditionApi`(4) `promoApi`(4) `dialogueApi`(4) `splitPackageApi`(2) `showcaseApi`(2) | **20** | `/api/v5/*` + `/api/auditions` `/api/promos` | 角色/模板创建、试镜、宣传片、双人对戏、切条包、合集包；含 `isCreationNotReady` 404 判定 |
| `lib/pipeline.ts` | 228 | `pipelineApi`(4) + `pipelineBatchApi`(4) | **8** | `/api/pipeline/*` | 单发 run 与批量 batch |
| `lib/studio.ts` | 273 | `studioApi` | **41** | 复用 `api` + 1 条新增 | **门面**：40 个方法直接转发 `api.*`，唯一新增 `publishPreflight` → `GET /episodes/{eid}/publish/preflight`（另有 `waitJob` 轮询包装器，供成片房渲染进度复用） |
| `lib/v5.ts` | 571 | `v5api` | **17** | `/api/v5/*` | catalog / item / putItem / series / voices / voice / render / editorial / storyboard；另有 13 个 URL 构造器（media / preview） |
| `lib/voice.ts` | 332 | `voiceApi` | **5** | `/api/v5/casting｜voices｜voice/{c}` | 归一化 + `fallbackCasting` 兜底 |
| `lib/templates.ts` | 1 467 | —（`TEMPLATES` 16 模板常量 + `templateLabel`） | 0 | — | 纯数据：16 模板 id/label/风格/时长 |
| `lib/format.ts` | 437 | —（19 函数 + 32 常量表） | 0 | — | 状态/风格/发型/服装/音色标签映射 |
| `lib/download.ts` | 28 | —（`downloadUrlAsBlob`） | 0 | — | blob 下载 |
| `types.ts` | 387 | — | 0 | — | 全量 TS 类型 |

**统计口径**

- **去重后的请求函数 = 119**（50 + 19 + 20 + 8 + 17 + 5），加 `studioApi.publishPreflight` 这 1 个唯一端点 = **120 个真实后端调用点**。
- 若把 `studioApi` 41 个门面方法计入，表面函数总数 = **160**，其中 40 个是纯转发，**统计时不应重复计数**。
- **唯一端点 = 101 条路径 / ≈110 个 (method, path) 组合**（`/projects` `/episodes/{eid}` `/settings` `/v5/voice/{c}` `/v5/{kind}/{id}` `/v5/render` 等 9 条路径各有 GET+POST/PATCH/PUT/DELETE 两个方法）。

---

## 7. 前后端接口对账

后端路由总量 **141 条（去重 140，`GET /api/v5/voice/{character}` 在 `v5_studio` 与 `v5_catalog` 各注册一次）**，前端触达 **≈110 个 method+path**，覆盖率约 **78%**。

### 7.1 前端调用 → 后端：**零缺口**

逐条核对 101 条路径，全部命中后端路由。常见易错项已确认存在：

| 前端调用 | 后端实现 | 结论 |
|---|---|---|
| `POST /api/episodes/{eid}/shots/generate-dynamic` | main.py:1455 | 命中 |
| `GET /api/episodes/{eid}/publish/preflight` | main.py（GET publish/preflight） | 命中 |
| `PATCH /api/projects/{pid}`（仅死代码 Templates3D 调用） | main.py（PATCH projects/{pid}） | 命中 |
| `POST /api/v5/characters/{c}/art`、`GET /api/v5/characters/{c}/assets` | v5_create.py | 命中 |
| `POST /api/v5/characters/{c}/audition`、`GET …/auditions`、`GET /api/auditions/{id}`、`POST …/cancel` | character_audition.py | 命中 |
| `POST /api/v5/character-showcase/split-packages`、`publish-package` | character_showcase.py | 命中 |
| `POST /api/cinema/publish-packages/batch` 等 4 条 | publish_batches.py（prefix `/api/cinema`） | 命中 |
| `GET /api/v5/preview/{kind}/{item_id}` | v5_catalog.py | 命中 |

### 7.2 后端有、前端**无任何入口**（能力空转，12 项）

| 后端能力 | 路由 | 前端现状 |
|---|---|---|
| **V6 Agent 编排层（整套）** | `GET /api/v6/agent/overview`、`GET/PUT /api/v6/agent/workflow/{template}`、`POST /api/v6/agent/run`、`GET /api/v6/agent/runs`、`GET …/runs/{id}`、`POST /api/v6/agent/edit`、`GET /api/v6/agent/art`、`POST /api/v6/agent/pose`、`GET /media/agent_runs/…`（**10 条**） | **零引用**（grep `v6/agent` = 0）。AGENTS.md 描述的核心编排/增量重跑只能在 CLI 用 |
| SSE 事件推送 | `GET /api/events` | **零引用**（无 `EventSource`），全部靠 setInterval 轮询 |
| 项目模板切换 | `POST /api/projects/{pid}/apply-template` | **零引用**；`PATCH /projects/{pid}` 仅被死代码 Templates3D 使用 → **「给项目换模板」实际不可达** |
| 合同校验 | `POST /api/v5/validate` | 零引用 |
| 删除角色 / 模板 | `DELETE /api/v5/characters/{c}`、`DELETE /api/v5/templates/{t}` | 零引用（能建不能删） |
| 模板状态快照 | `GET /api/cinema/templates/status` | 未用（前端从总表 + progress 推断） |
| 单系列详情 | `GET /api/v5/series/{sid}` | 未用（只列表 + 导出） |

### 7.3 路由层隐患

1. **重复注册**：`GET /api/v5/voice/{character}` 同时在 `v5_studio.py`(include 序 2552) 与 `v5_catalog.py`(2555) 定义；按 include 顺序前者生效，后者永不命中。
2. **同 router 内路径遮蔽**：`v5_catalog.py` 中 `GET /{kind}/{item_id}` 先于 `GET /voice/{character}` 注册，`/api/v5/voice/guanyu` 会先匹配到 item 处理器；目前靠 `v5_studio` 抢先注册兜住，属于**顺序依赖的隐式行为**，改动 include 顺序即回归。
3. **尾斜杠依赖**：`cinemaApi.templates()` 请求 `/api/cinema/templates`，后端该路由注册为 `/`（实际 `/api/cinema/templates/`），依赖 Starlette 307 重定向；GET 可用，若将来改成 POST 会掉 body。

---

## 8. 「占位 / 未就绪 / TODO」文案清点

grep 结果：**「敬请期待」「即将上线」「TODO」「FIXME」「coming soon」全部为 0**；「开发中」1 处、「占位」9 处（多数是 CSS 注释/空态兜底）。真正需要关注的是 **20 余处「接口尚未就绪（后端 Vxx 实现中）」提示，逐条与后端核对后全部过期**：

| 位置 | 文案 | 声称缺失的接口 | 后端实况 | 判定 |
|---|---|---|---|---|
| `CharactersPage.tsx:517/564` | 试镜任务/视频接口尚未就绪（V32） | `/api/v5/characters/{c}/audition`、`/api/auditions/{id}` | character_audition.py 已有 | **stale** |
| `CharactersPage.tsx:709/756` | 宣传片接口尚未就绪（V35） | `/promo`、`/api/promos/{id}` | character_promo.py 已有 | **stale** |
| `CharactersPage.tsx:953/1013` | 双人对戏接口尚未就绪（V36） | `/api/v5/characters/dialogue`、`/api/v5/dialogues` | character_dialogue.py 已有 | **stale** |
| `CharactersPage.tsx:810/1068/1086` | 切条包/合集发布包接口（V38/V36） | `/api/v5/character-showcase/*` | character_showcase.py 已有 | **stale** |
| `CharactersPage.tsx:1272/1310` | 试听接口尚未就绪（V18） | `POST /api/v5/voice/{c}/preview` | v5_catalog.py 已有 | **stale** |
| `CreateCharacterDrawer.tsx:172`、`lib/creation.ts:208` | 创建接口尚未就绪（V21） | `POST /api/v5/characters` | v5_create.py 已有 | **stale** |
| `CreateTemplateWizard.tsx:223` | 创建接口尚未就绪（V21） | `POST /api/v5/templates` | v5_create.py 已有 | **stale** |
| `LibraryPage.tsx:210/211` | 批量接口尚未就绪（V25） | `POST /api/pipeline/batch` 等 | pipeline_ui.py 已有 | **stale** |
| `LibraryPage.tsx:722/723` | 批量导出接口尚未就绪（V29） | `POST /api/cinema/publish-packages/batch` | publish_batches.py 已有 | **stale** |
| `PublishPackageModal.tsx:47` | 发布包接口尚未就绪（V26） | `/api/cinema/templates/{tid}/publish-package` | cinema_templates.py 已有 | **stale** |
| `PipelineConsole.tsx:196/385`、`steps.tsx:354/395` | pipeline / casting / 试听接口尚未就绪（V18） | `/api/pipeline/*`、`/api/v5/casting`、`/voice/{c}/preview` | 三者均已实现 | **stale** |
| `SettingsModal.tsx:543/679/816` | 后端尚未支持 `h3_provider` / `upload_post_*` / `/api/llm/config` | settings + llm | seed.py 有全部字段；main.py:2380-2430（PATCH /settings）已支持 `h3_provider`、`upload_post_api_key`、`llm_provider/base_url/model`；`/llm/config` 存在 | **stale** |
| `Templates3D.tsx:340` | `PATCH /api/projects/{id}` 尚未上线（tpl-backend 开发中） | — | main.py 已有 | **stale + 死页** |

**真正的空态（非缺陷，属正常设计）**：各页「暂无任务 / 暂无日志 / 尚未生成成片 / 暂无 persona / 暂无缩略图」等，共约 25 处，都有对应可点击的下一步动作，无需处理。

**风险**：这些 guard 多写成 `if (status === 404 || status === 405)`，只拦 404/405，因此不会掩盖 500；但**文案层面持续误导**（用户看到「后端 V38 实现中」会以为功能不可用），且 `Templates.tsx:473/1029` 的「后端 /api/cinema/templates 暂未返回数据」把接口空数据与接口缺失混为一谈。

---

## 9. 交互与数据刷新模式（无推送，全轮询）

| 场景 | 机制 | 间隔 |
|---|---|---|
| App 健康心跳 | `setInterval` → `GET /api/health` | 15 s |
| 后端离线重连 | `setInterval` → 全量 `boot()` | 4 s |
| 3D 模板库 | `setInterval` → `GET /api/cinema/templates`（页面隐藏暂停） | 3 s / 有活动任务 2 s |
| 工作台生成台 | 轮询 `GET /api/jobs` | 2 s |
| 一键成片 | 轮询 `GET /api/pipeline/run/{id}` | 1.5 s |
| AI 台本 | `generateScript` 内部轮询 `GET /api/jobs/{id}` | 1.5 s × 120 |
| 成片房 waitJob | 轮询任务直至 done/failed | 见 `studio.ts:waitJob` |

其它交互特征：批量队列状态持久化在服务端（离开页面再回来自动恢复 `pipeline/batches`、`publish-packages/batches`）；Toast 全局提示；`ErrorBoundary` 兜底；离线态整屏「等待后端」并给出一键重试 + 启动命令；无表单校验库、无状态管理库、无 i18n（全中文硬编码）。

---

## 10. 统计汇总

| 指标 | 数值 |
|---|---|
| 页面数（路由挂载） | **15** |
| 路由数（hash 规范路由） | **15**（+13 条旧 URL 重定向规则） |
| 组件文件 | **38**（在链 30 / 孤儿 8） |
| 死代码行数 | **3 808**（tsx 的 16.5%） |
| lib API 请求函数 | **119**（去重后真实调用点 120） |
| lib 门面重复方法 | **41**（`studioApi`） |
| 前端触达端点 | **101 路径 / ≈110 method+path** |
| 后端路由 | **141**（去重 140），前端覆盖 **≈78%** |
| lib .ts 文件 | **12** |
| 样式文件 | **19**（14 192 行） |
| tsx / ts / css 行数 | 23 121 / 5 251 / 14 192 |
| 占位/未就绪文案 | **约 20 处，全部 stale** |

---

## 11. 结论

1. **前端完整性高、无接口缺口**。15 条路由、30 个在用页面组件、119 个请求函数构成完整闭环：项目大厅 → 工作台 5 tab → 3D 制作 3 tab → 一键成片 6 步 → 角色工坊 → 模板/风格/场景/成片库。101 条前端调用 100% 有后端实现，这一层不需要「补后端接口」。
2. **真正的债是陈旧的能力声明**。20 余处「接口尚未就绪（Vxx 实现中）」全部过期，属于把「当时的后端进度」写死进 UI 文案的后果；建议统一改成一个可复用的「接口不可用」错误提示，只在真实 404/405 时由错误对象渲染，而不是在每页写死 V32/V35/V36/V38。
3. **V6 编排层是最大的功能落差**。`AGENTS.md` 把 16 模板工作流/阶段重跑/角色音色编辑作为核心能力，后端 10 条 `/api/v6/agent/*` 已就绪，但前端零引用 —— 界面上看不到「选阶段重跑 / 看事件流 / 改工作流 JSON」。这是产品面最大的缺口，且属于**纯前端补齐**即可交付。
4. **可达性缺口 3 处**：①`#/cinema/*` 无全局导航项，只能从成片库「镜头台」或 `steps.tsx` 按钮进入；②项目模板切换（`apply-template` / `PATCH /projects/{pid}`）没有活界面；③删除角色/模板与合同校验（`/api/v5/validate`）无 UI。
5. **可安全清理的死代码 3 808 行**（`cinema/Overview`、`cinema/Final`、`studio/Templates3D`、`v5world/V5World|Gallery|Render|Characters|Templates`），删前只需确认 `#/cinema/final`、`#/cinema/overview`、`#/v5/*` 的重定向规则已覆盖全部外部书签（`App.tsx:33-53` 已覆盖）。
6. **结构风险 3 条**（后端侧，供 Lead 转交）：重复注册 `GET /api/v5/voice/{character}`、`v5_catalog` 内 `/{kind}/{item_id}` 遮蔽 `/voice/{character}`、`/api/cinema/templates` 依赖尾斜杠 307。