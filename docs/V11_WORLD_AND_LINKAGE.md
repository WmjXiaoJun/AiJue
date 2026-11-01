# V11：V5 世界重做 / 项目↔3D 打通 / 发布闭环 / 清理瘦身

四项一起交付（2026-11-01 19:00–20:05）。

## 1. V5 世界换新（替换旧 V5Studio）

新目录 `frontend/src/components/v5world/` + `frontend/src/styles/v5world.css`，全屏 `cm-app` 外壳 + `cm-rail`（6 tab）+ `cm-topbar`（18 成片 / 16 支 3D / 17 风格 61 场景 / 17 QC 通过）：

| Tab | 页面 |
|---|---|
| 01 成片画廊 | 16 模板成片 + 全部成片(18)；3D 优先（`/media/templates_3d/<id>/poster.jpg` + 金色 3D 徽章 + `final.mp4`），可切回 2D 原版；播放弹层、时间线/QC、KPI |
| 02 模板库 | 16 模板卡（3D 海报/场景路线/风格/时长/幕数/卡司），搜索+风格筛选，逐幕详情弹层，3D 版播放 / 转渲染 |
| 03 风格墙 | 17 风格（palette 色块、技法、角色处理、转场、模板数），详情弹层（texture/line/typography/audio/H3 prompt） |
| 04 场景库 | 61 场景（缩略图三级回退、层数/机位/画幅/变体），style 筛选 + 标签 + 搜索 + 详情弹层 |
| 05 角色库 | 25 角色（立绘/25D/程序化回退、性别/年龄/声线/角色池），筛选 + 抽屉详情 |
| 06 渲染台 | 模板 select + 时长 + force → `v5api.render`，1.5s 轮询进度/日志/取消，最近任务 |

`App.tsx`：`view.mode === "v5"` 全屏渲染 `<V5World/>`。旧 `V5Studio`/`components/v5/*` 已删除。

## 2. 项目 ↔ 3D 模板打通

- Project Studio 新增 **06「3D 模板」tab**（`studio/Templates3D.tsx` + `studio-3d.css`）：
  - 项目 format_id ↔ 3D 模板映射（`national_treasure_5min → national_treasure`），当前模板大卡：3D 海报/成片播放/镜头完成/时长/大小、QC 信息（`GET /api/cinema/templates/{id}`）、逐镜构成。
  - 16 模板切换网格：高亮当前、点卡片可播放预览、**「切换为本项目模板」→ `PATCH /api/projects/{pid} {format_id}`**（后端新增），顶部 chip / 播放器 / QC 联动刷新。
  - 「重新生成 3D」→ `cinemaApi.generateTemplates({templates:[id], force:true})`（409 优雅提示）；「打开 Cinema 模板库」入口。
- 后端新增 `PATCH /api/projects/{pid}`（name/format_id/aspect，校验模板存在与画幅）。

## 3. 发布闭环

- 后端新增 `GET /api/episodes/{eid}/publish/preflight`：5 项检查（成片就绪 / 标题 / 多语种版本 / 发布配置 / 视频可访问）+ `ok`/`publishable`/`warnings`。
- 前端成片房「导出与发布」新增 **发布前检查面板**：自动加载 + 手动重查，逐项红/金/绿状态与详情，显示阻断原因与 warnings；发布按钮保留（未配置时后端返回可读 400）。
- 实测：`ep_001` → render pass / title pass / localization warn / config fail / video pass → `publishable=false`（Upload-Post 未配置）；`ep_5610123283` video fail（render_url 指向模板目录，发布取片目录不一致，已记录待决策）；未配置时 `POST /publish` 400 可读、不写发布记录。

## 4. 清理瘦身

- 删除 39 个无引用文件（用 import 图可达性分析确认）：旧 `Workspace.tsx`、`components/tabs/*`(6)、`components/v5/*`(10)、`V5Studio.tsx`、`ProjectList.tsx`、`Sidebar.tsx`、`TopBar.tsx`、`TemplateModal/NewProjectModal`、旧 shot 组件（`ShotDrawer/ShotFormModal/NewCharacterModal/AiScriptModal/Character*/ConceptStudio/LocalizationPanel/PublishingPanel` 等）、`hooks/usePolling.ts`、`lib/agent.ts`、`lib/publishing.ts`。
- `App.tsx`：删除不可达的旧 app-shell（Sidebar/TopBar/ProjectList/V5Studio/旧 modals），`templates3d` 纳入正式 `TabKey`/hash 路由；Studio 内部本地 tab 状态移除。
- 剩余：`vite-env.d.ts`（类型引用，保留）；旧 `styles/{layout,components,views,v5}.css` 仍被 `SettingsModal/Toast/BootScreen` 等使用，暂留。
- 结果：前端源码 77 → 38 个 ts/tsx 文件；构建 js 500KB / css 313KB。

## 5. 验证

- `npx tsc --noEmit` 0 error；`vite build` 通过。
- 截图：`tmp/v11/final/1_home.png`、`2_v5_gallery.png`、`3_studio_3d.png`、`4_finish_preflight.png`；V5 六页证据 `tmp/v11/v5world/`（15 张 + cdp 脚本）。
- 交互：深链 `#/episodes/<eid>/templates3d` 直达；切换模板 `PATCH` 200（已还原）；preflight 三集 200；V5 播放/2D 切换/详情抽屉；清理后全站主页/工作台/Cinema/V5 全部可打开。
- 数据清理：删除 teammate 测试产生的 `proj_7b3c38eb14`（造片局 · 新节目，含 1 期），DB 备份在 `tmp/v11/db_backup_before_cleanup.db`。

## 6. 已知遗留

1. `/api/v5/outputs` 返回的 `/media/v5/...` 路径 404（实际产物在 `templates_v5/`），V5 世界已改用真实路径；后端可后续统一 URL。
2. 发布取片目录与 `render_url` 不一致（ep_5610123283），发布前需重渲染到 `outputs/episodes/<eid>/final.mp4` 或后端改为跟随 render_url。
3. Upload-Post 未配置（无 Key/账号），发布链路已验证到「检查 + 可读 400」，真实发布需凭证。
4. 3D 模板 tab 的「重新生成 3D」未实跑（120 镜 GPU 成本高），409 链路已就绪。
