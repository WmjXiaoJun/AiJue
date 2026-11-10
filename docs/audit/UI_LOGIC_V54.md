# UI 逻辑说明与 V54 修复（#/agent / 长版 / 成片库）

- 日期：2026-11-05
- 范围：`frontend/src`（只读审计 + 修复）；后端只读对照 `backend/app/routers/*.py`；实测后端 `http://127.0.0.1:8090`
- 交付物：本文档 + 代码修复 + 3 张截图（`tmp/ui/v54/`）
- 结论速览：**导航/状态机是「hash 即状态」的单层结构，无全局 store；端到端 6 个环节里 4 个真断点（删除成片缺后端 DELETE、成片库缺 3 个模板、成片时长未回填、长版接口刚上线）；本轮修掉 5 项 UI 问题，2 项待后端。**

---

## 0. 一句话总览

一个 `AppShell`（左轨 7 项 + 顶栏）之下，`Shell` 用 `useState<ViewState>` 决定渲染哪个页面，`ViewState` 由 `location.hash` 解析而来（`parseHash`），浏览器前进/后退靠 `hashchange` 回写。所有页面自己拉数据、自己轮询，没有全局状态库、没有 localStorage——**刷新页面 = 从 hash 重建全部状态**。

---

## 1. 导航 / 状态机

### 1.1 路由实现（无 react-router）

| 位置 | 作用 |
|---|---|
| `App.tsx:19` `type ViewState` | 7 种 mode：projects / workspace / cinema / pipeline / agent / characters / library |
| `App.tsx:70` `parseHash()` | hash → 路由对象（eid/tab/cinema/pipeline/characters/agent/library） |
| `App.tsx:33` `legacyRedirectHash()` | 旧 URL 收敛（`/v5/*`、`/cinema/*`、`/v5/agent`、`/v6/agent` 等） |
| `App.tsx:143` `boot()` | 首屏：health → projects → 按 hash 建 view；失败置 offline |
| `App.tsx:302` `hashchange` | 前进/后退/地址栏改 hash → 重新解析并切页 |
| `App.tsx:16` `TAB_KEYS` | 工作台 tab：**script / assets / storyboard / generate / finish（5 个）** |

> 注：任务描述里的「工作台 6 个 tab」是 V19 之前的旧口径；V20 起实际是 5 个（`App.tsx:16`，`Studio.tsx` 的 tab 渲染一一对应）。

### 1.2 hash → 页面 → 数据源 → 刷新时机

| hash | 组件 | 主数据源（API） | 刷新时机 |
|---|---|---|---|
| `#/projects` | `platform/PlatformHome` | `GET /api/health`、`GET /api/projects`、`GET /api/settings`（设置弹层） | 进入页面 + 手动刷新；离线时 `boot` 每 4s 重试（`App.tsx` offline 效果）；健康心跳 15s |
| `#/episodes/<eid>/<tab>` | `studio/Studio`（`ProjectStudio`） | `studioApi.episode/jobs/shots/costs/assets/...`；script/assets/storyboard/generate/finish 五个子组件各自调用 | 有活跃 job 时 2.5s 轮询（`Studio.tsx:97`），否则不轮询；切 tab 不重新拉 overview |
| `#/library/templates` | `LibraryPage` → `cinema/Templates`（embedded） | `GET /api/cinema/templates`（3s/2s 轮询，页面隐藏跳过）+ `GET /api/v5/catalog` | 3s/2s 轮询（`Templates.tsx:175`）、`visibilitychange` 回到前台补一次 |
| `#/library/styles|scenes` | `LibraryPage` → `v5world/Styles|Scenes` | `/api/v5/catalog`（父级加载一次） | 只随父级刷新 |
| `#/library/final` | `LibraryPage` → `FinalLibrary` | `GET /api/cinema/templates`（30 条）、`GET /api/v5/series`、`publishPackageBatchApi.list` | 进入页面一次；批量导出 3s 轮询（`LibraryPage.tsx:690`）；模板批量 3s（`:138`）；运行中标记 10s（`:158`） |
| `#/pipeline` | `pipeline/PipelineConsole` | `/api/v5/catalog`、`/api/cinema/templates`、`/api/v5/voices`、`/api/v5/casting`、`POST /api/pipeline/run` + `GET /api/pipeline/run/{id}`、`/api/settings`、**V54 `/api/v5/longform/*`** | 进入页面一次（`loadAll` `PipelineConsole.tsx:123`）；pipeline run 1.5s 轮询（`:325`）；长版 run 中 3s、空闲不轮询 |
| `#/characters` | `characters/CharactersPage` | 角色 CRUD + 试镜 / 宣传片 / 双人对白 job | 三个 job 各 3s 轮询（`CharactersPage.tsx:504/696/926`），终态停止 |
| `#/cinema[/shots|jobs|assemble]` | `cinema/CinemaStudio` | `/api/cinema/overview`、`/shots`、`/jobs`、`/workers`、`/assemble` | overview 3s（有活动 job）/12s（空闲）（`CinemaStudio.tsx:80`） |
| `#/agent[/season]` | `agent/AgentPage` | `/api/v6/agent/overview|series|runs`、`/api/v5/catalog`、`/api/cinema/templates`、`/api/v6/muse/status|jobs`、`/api/v6/muse/video/*` | 进入页面一次（`AgentPage.tsx:269` 6 路并发）；单集 run 1.5s（`:720`）、muse 口型 2s（`:903`）、muse 视频 2s（`:1024`）、季状态切换系列时拉一次 |

### 1.3 持久化

- **URL hash**：唯一导航状态；工作台 tab 也编码在 hash（`#/episodes/<eid>/<tab>`），刷新/分享可恢复。
- **sessionStorage**：只有一条 `v23_pipeline_template`（`App.tsx:87` 读、`:159/:269` 清，`LibraryPage` 创建模板后写入），用于「创建即生成 → 打开管线并预选模板」。
- **localStorage**：**没有使用**（全仓库 0 处）。
- **表单/筛选状态**：全部是组件内 `useState`，切页即丢（例如成片库的筛选/搜索、智能体的模板选择）。

---

## 2. 端到端走查（新建项目 → 发布）

主链路：项目大厅 → 工作台（跑脚本/资产/分镜/生成）→ 成片库 → 发布包。

| 环节 | 入口 | 背后 API | 状态 | 证据 |
|---|---|---|---|---|
| ① 新建项目 | 大厅「新建项目」 | `POST /api/projects`、`GET /api/projects/{id}/episodes` | 通（无剧集时 toast 提示等后端初始化） | `App.tsx` `handleProjectCreated`/`openProject` |
| ② 写脚本 | `#/episodes/<eid>/script` | `studioApi.script/generateScript` | 通 | `studio/Scripts.tsx` |
| ③ 分镜 / 资产 | tab assets / storyboard | `studioApi.assets/shots/storyboard` | 通 | `studio/Assets.tsx`、`Storyboard.tsx` |
| ④ 生成镜头 | tab generate | `studioApi.generate` + jobs 2.5s | 通（有活跃 job 才轮询） | `Studio.tsx:97` |
| ⑤ 成片库 | `#/library/final` | `/api/cinema/templates` | **部分断**（见 B2/B3） | `LibraryPage.tsx:88` |
| ⑥ 发布 | 成片卡「发布包」/ 管线第 6 步 | `/api/cinema/publish_package`、`/api/settings` | 通 | `PublishPackageModal`、`StepPreflight` |

### 断点清单（真实问题）

| 编号 | 断点 | 触发 | 为什么错 | 证据 |
|---|---|---|---|---|
| **B1** | 成片**无法删除** | 成片库 → 删除 | 后端只有 3 个 DELETE：取消渲染 job、删自定义角色、删自定义模板；**没有删除成片的接口** | `backend/app/routers/v5_studio.py:778`、`v5_create.py:932/1452`；本轮已把 UI 接好并默认关闭（`lib/v5.ts:425` `FINAL_DELETE_ENABLED=false`） |
| **B2** | 成片库只列 **30/33** 个模板 | 打开成片库 | 数据源是 `/api/cinema/templates`（30 条：16 内建 + 2 写实 + 12 custom），缺 `relic_talk`、`relic_talk_3d`、`custom_holo_talk` → 即使将来出片也不会出现 | 实测 `/api/cinema/templates`=30 vs `/api/v5/catalog`=33；`LibraryPage.tsx:88` |
| **B3** | 成片卡显示 `0:00`、尺寸 `—` | 打开成片库 | `/api/cinema/templates` 的 `final.duration/size` 只在该模板有 3D final 时才非零；当前 9 支有 final 的才有值，其余为 0 → 「达标/未达标」不能直接比 | 实测 `tonight_show.final={exists:false,duration:0,size:0}`；本轮已让「目标时长」徽标在 `duration=0` 时显示「时长未记录」而不是假「未达标」（`steps.tsx:261`） |
| **B4** | 5 分钟长版接口曾 404 | 长版面板 | 17:29 前 `GET /api/v5/longform/status` 404；现已上线（实测 200，返回单 job 形态） | 本轮实测；面板对 404 有「未接入 + 30s 自检」兜底（`LongformPanel.tsx`） |
| **B5** | 工作台 tab 数 | 文档 vs 代码 | 代码是 5 个（`App.tsx:16`），不是 6 个 | `App.tsx:16` |
| **B6** | 陈旧剧集深链 | `#/episodes/<已删除>/script` | V52 已修：单独 catch 404 → 提示 + 回大厅，不再误判「后端离线」 | `App.tsx` `boot()` 内 episode try/catch |
| **B7** | 角色页试镜/宣传/对白 404 | 后端未实现时 | 有兜底文案，不会白屏 | `CharactersPage.tsx` 三处 404 分支 |
| **B8** | 长版批量会**真占 GPU** | 点「全部模板 5 分钟」 | UI 未加二次确认（因为是用户主动点击，且按钮文案已写明 30 个） | `LongformPanel.tsx` |

> 「永远 loading / 假按钮」：全仓库未发现 `href="#"`、空 `onClick`、TODO/敬请期待占位（grep 0 命中）。唯一「点了没反应」的是 B1 的删除——本轮已改成显式禁用 + 原因，而不是死按钮。

---

## 3. 本轮修复（V54）

| # | 修复 | 文件 |
|---|---|---|
| 1 | 成片库：**单条删除 + 批量删除 + 确认弹层**（选择模式、全选已出片、删除所选 N、确认弹层列出条目与时长）；后端缺接口 → 确认按钮禁用并写明需要的 API 与开关 | `library/LibraryPage.tsx`（`delMode` 545、工具栏、卡片、Modal）、`lib/v5.ts:425` |
| 2 | 成片库数据源仍是 `/api/cinema/templates`（B2 未改，需后端补列表或前端改源；见「未修」） | — |
| 3 | 管线页模板卡：**目标时长徽标**（目标 Xs + 达标/未达标/时长未记录） | `pipeline/steps.tsx`（`StepTemplates` 新增 `targets`） |
| 4 | 管线页：**5 分钟长版面板**（本模板 300s / 全部模板 300s / 状态 chip / 进度条 / 最近任务 / 404 自检） | `pipeline/LongformPanel.tsx`（新）、`PipelineConsole.tsx:542`、`lib/longform.ts`（新） |
| 5 | 智能体页：左栏每条显示目标时长（如 `38s`）、中栏头部「时长 38s + 长版 未达标/达标」、顶栏 chip「5 分钟 2/33」 | `agent/AgentPage.tsx`（1266-1290、2455-2470、chips） |
| 6 | 长版 API 客户端兼容后端实际形态（单 job：`job_id/status/templates[]/items[]/log_tail[]`，并把 `items[].out` 磁盘路径转 `/media/...`） | `lib/longform.ts` `normalizeLongformState` |
| 7 | 目标时长未知时不误判「未达标」 | `pipeline/steps.tsx:261` |

**新增文件**
- `frontend/src/lib/longform.ts`（186 行）：类型 + 归一化 + `longformApi.status/run` + `LONGFORM_DURATION=300`
- `frontend/src/components/pipeline/LongformPanel.tsx`（244 行）
- `frontend/src/styles/v54.css`（76 行）：长版面板 / 删除弹层 / 时长徽标

**改动文件（行数）**
| 文件 | 行数 | 改动 |
|---|---|---|
| `library/LibraryPage.tsx` | 1505 | 删除模式 + 工具栏 + 卡片勾选/删除按钮 + 确认 Modal + v54.css 引入 |
| `pipeline/PipelineConsole.tsx` | 655 | 挂长版面板 + `targetDurations` memo + 传 `targets` |
| `pipeline/steps.tsx` | 1003 | `StepTemplates` 新增 `targets`，卡片渲染时长徽标 |
| `lib/v5.ts` | 597 | `FINAL_DELETE_ENABLED/FINAL_DELETE_PATH/deleteFinalOutput` |
| `agent/AgentPage.tsx` | 2573 | 时长徽标 + 长版 chip + 头部时长/达标 |

---

## 4. 截图与验证（1600×1200，无头 Chromium，最终构建）

| 截图 | 内容 | console error |
|---|---|---|
| `tmp/ui/v54/library_delete.png` | 成片库 → 删除模式 → 勾 3 项 → 确认弹层（3 条、不可撤销、后端缺接口说明、确认禁用） | **0** |
| `tmp/ui/v54/pipeline_longform.png` | 管线第 1 步：5 分钟长版面板（本模板 300s · 美式深夜秀 / 全部模板 5 分钟（30 个）/ 刷新状态 / 最近任务 `custom_tpl_v46_cyber ok 100% 成片`）+ 每张卡「目标 38s / 时长未记录」 | **0** |
| `tmp/ui/v54/agent_duration.png` | 智能体页：顶栏「5 分钟 2/33」、左栏每条 `38s`、头部「时长 38s + 长版 未达标」 | **0** |

- 断言：成片库 30 卡 / 9 已出片；删除模式勾选 3 → 弹层 3 行、确认按钮 disabled；长版面板「本模板 300s · 美式深夜秀」可用、「全部模板 5 分钟（30 个）」可用、最近任务带 `/media/templates_3d/custom_tpl_v46_cyber/final_300s.mp4`；智能体 33 模板 / 12 阶段 / 33 个时长徽标。
- 非预期 4xx：**0**（长版接口实测 200）。
- build：`tsc --noEmit && vite build` ✅ —— `dist/assets/index-C-cNashi.js 710.10 kB │ gzip 210.60 kB`、`index-DMO6P9Ak.css 350.15 kB │ gzip 55.51 kB`（磁盘 756,679 B / 350,166 B）。

---

## 5. 未修项与原因

1. **删除成片（B1）**：后端没有 `DELETE` 成片接口。UI 已全部接好，启用方式：后端提供路径（建议 `DELETE /api/v5/outputs/{template}`，删除 `outputs/templates_2d|3d/<tid>/final.mp4` 与 `final_300s.mp4`），然后把 `lib/v5.ts` 的 `FINAL_DELETE_PATH` 改成真实路径、`FINAL_DELETE_ENABLED` 置 `true`。**未自行造接口**。
2. **成片库覆盖 30/33（B2）**：需后端把 `relic_talk / relic_talk_3d / custom_holo_talk` 纳入 `/api/cinema/templates`，或前端改用 `/api/v5/outputs`（后者字段更全：final_url/duration/qc）。改数据源会牵动成片卡/批量导出/QC 链接，风险高于本轮范围，先记录。
3. **成片时长未回填（B3）**：`/api/cinema/templates` 对无 3D final 的模板返回 0；若要让「达标」对全部 33 个模板都准，需要后端补 `final.duration`（或前端改读 `/api/v5/outputs.duration`）。
4. **长版真实批量未触发**：只验证了面板/状态/进度/接口形态（GET 200、POST 契约与按钮禁用逻辑），未真跑 30 个模板（会占 GPU 十几分钟并改写成片）。
5. **筛选/选择不持久化**：成片库筛选、智能体选中模板都是组件态，切页即丢（现有设计，未改）。

---

## 6. 后续建议（按优先级）

1. 后端补 `DELETE /api/v5/outputs/{template}`（+ 批量），前端 1 行开关即上线删除。
2. 后端补 `/api/cinema/templates` 的 `final.duration/size`，或让成片库改读 `/api/v5/outputs`（33 条、字段全）。
3. 长版跑批加「二次确认 + 预计耗时」，并在面板里展示每集进度（当前只有 job 级进度）。
4. 智能体页把「5 分钟长版」与「单集 run」合并成一个队列视图（现在长版在管线页、口型在智能体页，两个队列互不可见）。
