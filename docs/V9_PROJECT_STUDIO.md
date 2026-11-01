# V9 设计：Project Studio（项目工作台重做）

> 需求：截图圈出的旧项目工作台 5 个 tab（编剧间 / 资产库 / 分镜台 / 生成台 / 成片房）还是旧 UI（「AI 微综艺」视觉、信息密度低、部分空态）。「好的 开始设计吧」→ 用 Cinema Studio 同一套设计语言重做这 5 个 tab，全部接真实 API、可操作。

## 1. 目标

- 视觉与 Cinema Studio 完全一致（深色电影感 / 博物馆金 / KaiTi 标题 / 发丝线 / 颗粒暗角），不再是旧的黄色霓虹后台风。
- 5 个 tab 全部具备真实功能（不是只读摆设）：保存台本、AI 生成台本、生成分镜、角色概念图生成/锁定、镜头编辑/生成/版本选择、任务重试、渲染成片、导出/多语种/发布。
- 路由沿用 `#/episodes/<eid>/<tab>`（tab = script|assets|storyboard|generate|finish），项目列表点进去即新工作台。

## 2. 信息架构

```
Project Studio（全屏，复用 cm-app 设计令牌）
├─ 顶栏：← 返回平台 | 项目名 / 第 N 期（大标题）| 状态/时长/分辨率 chips | 右：镜头 x/y、费用、进行中任务、刷新
├─ Tab 条：01 编剧间 SCRIPT | 02 资产库 ASSETS | 03 分镜台 STORYBOARD | 04 生成台 RENDER | 05 成片房 FINISHING
└─ 内容区（各自页面）
```

| Tab | 功能（真实 API） |
|---|---|
| 编剧间 | 台本编辑器（`episode.script`，保存 = PATCH /episodes/{id}；字数/段落统计）；AI 生成台本（POST /projects/{pid}/generate-script，topic/duration/tone → 回填）；「生成分镜」入口（POST /episodes/{id}/storyboard/generate）；镜头/时长预估 |
| 资产库 | 角色卡（角色列表 / 概念图生成 POST /characters/{cid}/concept/generate + 轮询 / 角色文件 / 锁定）；固定资产（assets：角色/场景/声线，PATCH locked）；新建角色（POST /projects/{pid}/characters + 概念图）；图片 lightbox |
| 分镜台 | 镜头表/卡片（52 镜，状态/台词/时长/视觉/花字）；镜头编辑（PATCH /shots/{id}）；单镜生成（POST /shots/{id}/generate）；动态生成（POST /shots/{id}/generate-dynamic）；版本列表/选择（GET /shots/{id}/versions / POST select）；删除镜头；批量「生成全部分镜」 |
| 生成台 | 任务队列（GET /jobs?episode_id，2s 轮询；状态/进度/费用/错误）；重试（POST /jobs/{id}/retry）；费用汇总（GET /episodes/{id}/costs）；按类型/状态筛选；批量生成入口 |
| 成片房 | 成片播放（episode.render_url）；一键渲染（POST /episodes/{id}/render + 轮询进度）；导出剪映草稿（POST /episodes/{id}/export）；多语种出海（POST /episodes/{id}/localize + 列表）；发布（GET /publish/config、POST /episodes/{id}/publish、GET /publishes） |

## 3. 组件与分工

| 文件 | 负责 |
|---|---|
| `frontend/src/lib/studio.ts` | 类型/常量/`studioApi`（api 包装 + waitJob 轮询）/格式化/状态标签 |
| `frontend/src/components/studio/Studio.tsx` | 全屏 shell：顶栏、5 tab、内容挂载、episode 状态刷新 |
| `frontend/src/styles/studio.css` | shell + 共享页面元件（kpi/toolbar/panel/table/drawer/field/空态） |
| `studio/Scripts.tsx`、`studio/Assets.tsx` | teammate A（编剧间 + 资产库） |
| `studio/Storyboard.tsx`、`studio/Render.tsx`、`studio/Finishing.tsx` | teammate B（分镜台 + 生成台 + 成片房） |
| `studio-a.css` / `studio-b.css` | 各自页面样式（前缀 `st-script-*` / `st-assets-*` / `st-board-*` / `st-render-*` / `st-finish-*`） |

## 4. 设计语言（复用 cinema.css 令牌）

- 根节点 `cm-app st-app`：继承 `--c-bg/--c-panel/--c-gold/--c-jade/--font-display...` 与 `.cm-card/.cm-btn/.cm-chip/.cm-mono/.cm-loading/.cm-empty/.cm-error/.cm-progress` 等共享类。
- 标题：KaiTi 28–34px；kicker：mono 9.5px 0.34em 金色。
- 卡片：`#0e1015` 渐变 + 1px `rgba(201,162,39,.13)` 发丝线 + 12px 圆角；hover 微亮。
- 数字/ID/时间码：mono；状态色：gold=进行中、jade=完成、red=失败、slate=待处理。
- 页面统一结构：`.st-page` → `.st-page-head`（kicker/h1/sub + actions）→ `.st-kpis`（4–6 个 KPI）→ 工具条 → 内容分栏。

## 5. 验收

1. 旧 `#/episodes/<eid>/script|assets|storyboard|generate|finish` 打开的是新 Project Studio（旧 Workspace 不再渲染）。
2. 5 页数据真实（52 镜 / 3 角色 / 2 场景 / 任务/费用来自 API），无白屏/无 console error。
3. 功能至少验证：保存台本 → 重新加载仍在；改镜头台词/时长 → 列表刷新；任务重试按钮可用；渲染/导出/多语种/发布入口可用（真实触发时给出 job 或可读错误）。
4. `tsc --noEmit` 0 error、`vite build` 通过；5 页截图；commit。


## 6. 交付记录（2026-11-01）

### 6.1 实现
- 新增 `frontend/src/lib/studio.ts`（StudioTabProps / studioApi / 状态与格式化 / waitJob）。
- 新增 `frontend/src/components/studio/Studio.tsx`（全屏 shell：顶栏 + 期数切换 + 5 tab + 指标轮询 + 刷新）。
- 5 个 tab 全部重做并接真实 API：
  - 编剧间 `Scripts.tsx`：台本编辑/脏标记/Ctrl+S 保存、AI 生成台本（topic/时长/语气 → 回填）、生成分镜、台本结构段落定位。
  - 资产库 `Assets.tsx`：角色工坊卡（头像/状态/锁定/概念图抽屉/生成概念 job 进度）、固定资产按类型分组表格、场景卡、新建角色。
  - 分镜台 `Storyboard.tsx`：52/56 镜表格 + 搜索/筛选/排序 + 抽屉编辑（台词/时长/视觉/花字）+ 生成/动态生成 + 版本列表选择 + 删除。
  - 生成台 `Render.tsx`：125 条任务队列（2s 轮询、进度/错误/重试/产物播放）+ 费用分布。
  - 成片房 `Finishing.tsx`：成片播放、渲染成片（job 进度）、多语种出海、导出剪映、发布（Upload-Post 配置态/记录）。
- `frontend/src/App.tsx`：workspace 路由改为全屏渲染 `ProjectStudio`（旧 Workspace 不再渲染，URL 仍是 `#/episodes/<eid>/<tab>`）。
- 样式：`styles/studio.css`（shell + 共享元件）、`studio-a.css`、`studio-b.css`（页面级，全部 `st-*` 前缀，复用 cinema 设计令牌）。

### 6.2 Demo 数据回填（DB 已随提交）
- `ep_001`（AI 会取代综艺导演吗）：回填 **1,454 字 / 52 段**台本（按 52 镜的台词 + 画面描述自动成文）。
- `ep_5610123283`（国宝会说话 5min 3D）：从 `outputs/templates_v5/relic_talk/work/episode.json` 导入 **56 镜**（类型/时长/台词/场景中文化，319.21s）、回填 1,476 字台本（43 段）、创建 3 角色（老周/太史公/东坡，含 SDXL 概念图头像）、6 项固定资产（3 角色 + 3 场景）。
- 数据库：`backend/data/zaopianju.db`（WAL 已 checkpoint；`db-wal/db-shm/jobs` 不再入库）。

### 6.3 验证
- `npx tsc --noEmit` 0 error；`vite build` 通过（492KB js / 264KB css）。
- 5 个 tab 无头 Chrome 实拍：`tmp/v9/final/1_script.png`…`5_finish.png` + `6_script_1280.png` + `7_storyboard_860.png`；CDP 冒烟 5 页均正常渲染（h1 命中）。
- 真实交互：保存台本 PATCH 回读、镜头编辑 PATCH 回读（teammate 验收并还原）；概念图生成 job（SDXL）真实完成 3 张角色头像；120+ 任务真实展示。
- 遗留：① AI 台本为同步 LLM 调用，前端请求层 20s 超时（超长台本可能超时）；② 概念图同名文件缓存（重新生成后可能显示旧图）；③ 角色锁定按 episode 资产归属，跨期按钮禁用并提示；④ 旧 `components/tabs/*` 与 `Workspace.tsx` 已不再被引用（保留作参考，可后续删除）。
