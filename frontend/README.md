# 造片局 · AI 微综艺 — 前端（frontend/）

Vite + React 18 + TypeScript 的深色影棚风制片工作台。无 UI 框架、无路由库、无状态库；
图标为内联 SVG，样式手写 CSS（`src/styles/`）。构建产物 `frontend/dist` 由后端 FastAPI 同源托管在
`http://127.0.0.1:8090/`。

## 开发

    cd frontend
    npm install
    npm run dev            # http://127.0.0.1:5173，/api 与 /media 反向代理到 127.0.0.1:8090

后端未启动时会显示「等待后端」覆盖页并每 4s 自动重连，不会白屏。

### 直连 / 跨域调试

    $env:VITE_API_BASE = "http://127.0.0.1:8090/api"; npm run dev

## 构建与验收

    npm run build          # tsc --noEmit && vite build -> frontend/dist

后端启动后打开 http://127.0.0.1:8090/ 即为构建产物（后端对 frontend/dist 做 StaticFiles + SPA fallback）。
未构建时后端根路径会返回后端状态说明页。

## 本地 mock API（后端未就绪时可选）

    node mock/server.mjs 8091
    $env:VITE_API_BASE = "http://127.0.0.1:8091/api"; npm run dev

按 `contract/API.md` 契约模拟项目/剧集/资产/镜头/任务/版本/渲染/费用，任务进度真实推进；
用于纯前端联调，不参与交付。

## 界面结构

- `Sidebar`：品牌 Logo、项目列表、节目模板、设置、后端连接状态
- `TopBar`：当前集面包屑、任务中心（进行中任务数）、费用、设置
- `ProjectList`：项目卡片（封面渐变 / 期数 / 预算消耗 / 进入工作台）、新建项目
- `Workspace`：剧集头部 + 五步 tab（本轮询 episode / jobs / shots，2s）
  - 编剧间：台本编辑器（读写 episode.script、保存/脏标记/未保存提示、示例一键填入）+ 从台本生成分镜 + 按段落分组的台词预览
  - 资产库：角色工坊（原创角色卡 / 详情抽屉 / 新建角色）+ 固定资产（主持/嘉宾/场景卡，声音、人设、锁定）
  - 分镜台：表格/宫格切换、筛选、多选批量生成、添加镜头 / 编辑台词时长 / 删除镜头（二次确认）、点击打开右侧详情抽屉
  - 生成台：任务列表（类型/状态/进度/费用/错误/重试）+ 镜头版本视频、选用
  - 成片房：渲染成片、进度、`<video>` 成片、费用汇总、包装配置
- `ShotDrawer`：镜头详情 + 版本列表（`<video>` 预览 / 选用 / 生成新版本）
- 角色工坊（contract/CHARACTER.md）：3D/2D 多风格原创角色；工坊卡片、角色详情抽屉（设定三视图 / 四表情 / 转面视频 / 重新生成）、
  新建角色弹窗（风格多选 + 外观预设 + 实时预览）。创建 → 自动 `POST /generate` → 轮询 Job → 刷新卡片；
  分镜台按 `asset_id`/`speaker` 匹配角色头像，成片房展示「角色画风」。
  角色接口未就绪时展示空态 + 提示（不伪造数据、不阻塞其它模块）。
- AI 概念图（contract/CONCEPT.md）：角色详情抽屉内「AI 概念图」区块 — 风格（anime / illustrious / guoman）+ 额外提示词 + seed
  → `POST /characters/{cid}/concept/generate`（1.6s 轮询 Job）→ 图集（立绘 / 三视图 / 四表情 / 设定总览），每图可
  「设为角色立绘」（`POST /portrait/from-concept`）与「用此图生成 H3 动画」（`POST /h3-animate`，完成后播放 `/media/h3/...`）。
  角色卡优先展示 `GET /characters/{cid}/concepts` 的 portrait（带「AI 概念图」徽标），否则回落 Blender portrait。
- IP-Adapter 一致性（contract/CONSISTENCY.md）：概念图区优先 `/concepts.consistent` 的 three_view / expressions，
  渲染为「一致性三视图 / 一致性表情（IP-Adapter）」分组；若接口暂不返回 consistent，则按固定路径
  `/media/concepts/{cid}/consistent/meta.json` 探测一次（失败自动回退原始图集，不伪造素材）；刷新按钮可重新探测。
  H3 动画播放使用 cache-bust 重新拉取最新概念图驱动视频；抽屉立绘优先 `Character.portrait_url`（后端切到 SDXL 概念立绘后自动生效）。
- 成片导出（contract/EXPORT.md）：成片房「交付导出」面板 → `POST /episodes/{eid}/export {type:"capcut"}` → 轮询任务，
  展示产物路径、复制剪映草稿目录（`%LOCALAPPDATA%\JianyingPro\User Data\Projects\com.lveditor.draft`）与导入步骤。
- 一键发布（海外）（contract/PUBLISHING.md）：成片房「一键发布（海外）」面板 — 品牌「造片局 · AI微综艺」，
  平台多选（YouTube / TikTok / Instagram，含时长/尺寸限制提示）、语言版本（中文 + EN/ES/JA/KO/PT，预填 localized meta.publish）、
  final/dynamic 版本、标题/描述/Hashtags 可编辑、YouTube 可见性；「预检」走 `dry_run=true` 只做本地校验（不联网），
  「发布」需先配置 Upload-Post 并二次确认；发布记录展示平台 / 状态 / request_id / 错误 / 时间 / 预检 warnings。
  未配置时展示 upload-post.com 注册引导且发布按钮禁用，不伪造成功。Settings 新增「海外发布（Upload-Post）」：
  API Key（密码、只写不读、已配置占位/清除）、username、启用开关、默认平台、YouTube 可见性 / made-for-kids。
- 多语种出海（contract/LOCALIZATION.md）：成片房「多语种出海版本」面板 — 语言多选（EN/ES/JA/KO/PT，默认 EN+ES），
  `POST /episodes/{eid}/localize {langs}` 每语言一个 Job，任务进行中 2.5s 轮询 `GET /episodes/{eid}/localizations`；
  每语言卡片支持视频播放、下载 mp4 / srt、展开发布元数据（title / description / hashtags 一键复制）与失败重试。
  中文原版保留在上方「成片渲染」，多语版本为同画面 + 目标语配音/字幕（localized/{lang}/final.mp4 + subtitles.srt）。
- 台本驱动（contract/SCRIPT_FORMAT.md）：编剧间台本 textarea 读写 `episode.script`（进入加载、脏标记、未保存离开提示），
  保存 `PATCH /episodes/{eid} {script}`；空台本展示示例格式并支持一键填入；「从台本生成分镜」在有镜头时二次确认并以
  `{force:true}` 覆盖，完成后展示 created 数与分段摘要并刷新分镜。剧集头部支持切换剧集 / 新建剧集
  （`POST /projects/{pid}/episodes` 后自动切换）。分镜台支持添加镜头（`POST /episodes/{eid}/shots`：类型/说话人/台词/时长/画面）、
  编辑台词时长（现有 `PATCH /shots/{sid}`）与删除镜头（`DELETE /shots/{sid}`，二次确认，后端自动重排镜号）。

## 数据与容错

- API 相对 `/api`；`mediaUrl()` 统一处理 `/media/...`（VITE_API_BASE 为绝对地址时自动取 origin）
- 轮询失败静默保留上次数据；全局 health 心跳断线显示「重连中」banner
- 单页错误由 `ErrorBoundary` 兜底，渲染异常不白屏
- URL hash 路由：`#/projects`、`#/episodes/ep_001/{script|assets|storyboard|generate|finish}`，可刷新/分享
