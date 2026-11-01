# V10：平台首页（项目大厅）重做 + AI 台本链路校准

> 承接 V9（Project Studio 5 tab）。本步把平台入口页也从旧的「AI 微综艺」后台风换成 Cinema Studio 设计语言，并顺手把「AI 生成台本」的超时/体验问题修掉。

## 1. 新增 `PlatformHome`（项目大厅）

文件：`frontend/src/components/platform/PlatformHome.tsx` + `frontend/src/styles/platform.css`

- 全屏布局：左侧 `cm-rail`（品牌 / 项目大厅 / Cinema 3D / V5 世界 / 设置 / 后端连接状态），右侧 `cm-main`（顶栏 + 内容滚动）。
- 顶栏：项目数 / 期数 / 成片数 / 3D 模板完成度 / 进行中任务 + 刷新 + 「新建项目」主 CTA。
- Hero「继续制作」：自动挑选最近一期所在项目 → 3D 海报（`/media/templates_3d/<模板>/poster.jpg`）+ 项目名 + 期数 + 状态/时长/镜头/格式 chips + 「进入工作台 / 看完成片 / 新建一期」。
- 我的项目：每项目卡（3D 海报、格式、EP 编号、期数、成片数、花费、最近一期状态、新建期/进入工作台），末尾虚线「新建项目」卡。
- 16 模板 3D 影库：显示前 6 个模板海报 + 3D 完成徽章 + 全部 16 支入口。
- 最近动态：全局最近任务（类型/ID/时间/状态），入口跳生成队列。
- 新建项目弹层（新设计）：项目名 + 16 个节目模板选择（accent 色点/时长/画幅）+ 创建即调用 `POST /projects`，App 刷新并进入工作台。
- 响应式：1380/1180（rail 收窄为图标 + 双栏堆叠）/860（单列 + hero 图在下 + 状态文字收拢）。

App 路由：`view.mode === "projects"` 时全屏渲染 `PlatformHome`；旧 `ProjectList` / `Sidebar` / `TopBar` 仅 V5 世界模式还会用到。

## 2. AI 生成台本链路校准

后端 teammate 实测（真实 DeepSeek `deepseek-chat`，非 mock）：
| duration | 耗时 | script 长度 |
|---|---|---|
| 30s | 2.33s | 218 字符 |
| 60s | 4.24s | 517 字符 |
| 120s | 7.43s | 943 字符 |

正常路径 3–8s，但 LLM 长尾可能 >20s（前端请求层固定 20s 会先超时、后端仍计费）。修复：
- `lib/api.ts`：`request()` 增加 `timeoutMs` 参数（默认 20s），`generateScript` 单独放宽到 **60s**。
- `Scripts.tsx`：生成中显示提示「AI 正在写台本，通常 3–10 秒，最长约 60 秒；请勿重复点击。」

## 3. 验证

- `npx tsc --noEmit` 0 error；`vite build` 通过（504KB js / 296KB css）。
- 首页实拍：`tmp/v10/home.png`（1680）、`home_long.png`（全页）、`home_860.png`（移动）、`home_modal.png`（新建项目弹层）。
- CDP 冒烟：项目卡 3、模板卡 6、弹层 16 个模板、无 JS 异常。
- 交互：rail 跳 Cinema 3D / V5 世界 / 设置；项目卡进入工作台；新建一期（`POST /episodes/{pid}/episodes`）；新建项目（`POST /projects`）。
