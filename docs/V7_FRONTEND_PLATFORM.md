# V7 前端平台设计：Cinema Studio（3D 电影制片工作台）

> 背景：V6.7 已交付 5 分钟全 3D 成片（`outputs/templates_v5/relic_talk_3d/final.mp4`）。
> 本轮把「3D 电影制片」从命令行 pipeline 提升为**前端软件平台可操作的产品线**：选片 → 镜头台 → 生成队列 → 剪辑台 → 成片/QC。
> 目标不是静态页面，而是**具备真实功能**：读真实 shots/briefs/report，编辑 prompt，触发本地/云端生成与剪辑，播放成片，查看 QC。

## 1. 信息架构（IA）

新增顶级入口 **Cinema Studio**（`#/cinema/<tab>`），独立于旧「微综艺工作台 / V5 世界」：

| Tab | 路由 | 功能 |
|---|---|---|
| 总览 Overview | `#/cinema` | 项目卡（国宝会说话 3D）、镜头构成（云端/本地/复用/缺失）、成片状态、最近任务、快捷操作 |
| 镜头台 Shots | `#/cinema/shots` | 56 镜列表/网格：缩略图（sheets）、台词、时长、来源标签（cloud/local/reuse）、prompt 摘要；单镜抽屉（预览 mp4、编辑 brief、查看 meta、单镜重生成） |
| 生成队列 Jobs | `#/cinema/jobs` | 生成/剪辑任务列表与实时日志、进度、取消；批量生成入口（本地 H3 / 云端 MetaSo） |
| 剪辑台 Assemble | `#/cinema/assemble` | 剪辑参数（环境声/统一影调/定向调色/输出）、一键合成、任务进度 |
| 成片 & QC | `#/cinema/final` | 成片播放、QC 指标（时长/分辨率/黑帧/LUFS/字幕覆盖/每镜偏差）、抽帧/联系表、下载、设为项目成片 |

## 2. 设计语言（与成片一致：博物馆金黑 × 青绿）

- 底色 `#07080b` / 面板 `#0e1014` / 发丝线 `rgba(201,162,39,.14)`；文字 `#e8e3d6` 主、`#9b968a` 次。
- 主色 **museum gold** `#c9a227`（强调/选中/CTA），辅色 **mineral jade** `#4f8f7c`（本地/成功），警示 `#c2543f`。
- 字体：中文标题 `"KaiTi","STKaiti","SimSun",serif`（书卷气）；正文 `"DengXian","Microsoft YaHei",system-ui`；数字/ID 用等宽 `Consolas, "SF Mono"`。
- 版式：左竖排 rail（72px 图标 + 文字提示），顶栏 64px（片名/项目/状态/操作），内容区 max-width 1440 / 32px gutter；卡片圆角 10px，细金边，hover 微抬升。
- 细节：片门颗粒（CSS 噪点 3%）、暗角、金色渐变标题、进度条 shimmer、来源色点（cloud=gold、local=jade、reuse=slate、missing=red）。
- 交互：全部按钮有 loading/disabled；破坏性操作二次确认；toast 反馈；轮询进行中任务。

## 3. 数据与 API 契约（新 router `backend/app/routers/cinema3d.py`）

统一前缀 `/api/cinema`：

```
GET  /overview
  -> {project:{id,name,episode_id,render_url,status}, template:{id,label,style,target_duration},
      shots:{total,cloud,local,reuse,missing}, final:{exists,duration,size,mtime,url},
      jobs:{active, recent:[...]}}
GET  /shots
  -> {episode:{total_duration}, shots:[{id,index,duration,line,kind,source,url,sheet_url,
      valid,meta,brief:{subject,camera,env,ambience,chars,local_setting_key},edit_start}]}
PATCH /shots/{sid}
  body {subject?,camera?,ambience?,env?,chars?} -> 写 contract/v5/cinema3d/relic_talk_3d_briefs.json，返回 {brief}
POST /generate
  body {provider:"local"|"metaso", shots:[sid...], force?:bool, resolution?:"768x432"}
  -> {job_id}
POST /assemble
  body {ambience_db?:-20, unify_look?:true, shot_grade?:true} -> {job_id}
GET  /jobs   /jobs/{id}     -> 任务状态/进度/日志（最近 200 行）
POST /jobs/{id}/cancel
GET  /report                -> final_report.json + qc 摘要 + 抽帧 url
GET  /workers               -> {comfyui:bool, comfyui_queue:int, metaso_key:bool}
```

任务实现：线程内 `subprocess` 调 `python -m pipeline.cinema3d_local / cinema3d / cinema3d_assemble`，日志行入内存 deque，进度按行数/已知总量估算；状态同时落盘 `outputs/cinema_jobs/<job_id>.json`。

## 4. 用户旅程

1. 打开 `#/cinema` → 看到「国宝会说话 · 3D」项目卡：56/56 镜就绪、成片 5:25、QC 全绿。
2. 镜头台 → 按「本地 H3/云端」筛选 → 点某镜 → 抽屉里看视频/抽帧/meta，改 prompt → 单镜重生成 → 队列里看实时日志。
3. 剪辑台 → 调环境声/统一影调 → 一键合成 → 进度 → 自动跳成片 & QC。
4. 成片页 → 播放 5:25 成片、QC 指标、下载、设成项目成片（写 episode render_url）。

## 5. 代码组织

- 后端：`backend/app/routers/cinema3d.py`（新），`main.py` 挂载。
- 前端：
  - `src/components/cinema/CinemaStudio.tsx`（shell/tab 路由）、`Overview.tsx`、`Shots.tsx`、`ShotDrawer.tsx`、`Jobs.tsx`、`Assemble.tsx`、`Final.tsx`
  - `src/lib/cinema.ts`（类型 + API client）、`src/styles/cinema.css`（设计系统 + shell + overview）、`styles/cinema-shots.css`、`styles/cinema-final.css`
  - `App.tsx` 增加 `#/cinema` 路由；`Sidebar.tsx` 增加「Cinema 3D」入口。

## 6. 验收（真实功能）

1. `#/cinema` 五页均可打开，数据来自真实 API（56 镜 / 325.2s / QC 数字）。
2. 镜头台能筛选、打开抽屉、播放镜头 mp4、编辑 prompt 并保存（回写 briefs JSON）。
3. 触发单镜本地 H3 重生成 → 任务出现在队列、进度/日志实时、完成后镜头缩略图/预览刷新（或失败给出可读错误）。
4. 剪辑台能触发 assembly 并按真实日志进度，完成后成片页播放新片。
5. QC 页数字与 `final_report.json` 一致。
6. 截图证据：5 个 tab 各 1 张 + 抽屉/任务态截图；`npm run build` 通过；commit。


## 7. 交付记录（本轮已实现并验证）

| 层 | 交付 | 验证 |
|---|---|---|
| 后端 | `backend/app/routers/cinema3d.py`：10 个 `/api/cinema/*` 端点 + 线程任务队列（生成/剪辑/取消/日志/进度/落盘；briefs 缓存按 mtime 失效、响应 no-store、复用镜 `reuse_src`） | 28/28 TestClient + 28/28 真实 HTTP；`PATCH brief → GET 立即生效` 8/8；真实 `assemble` 任务端到端成功（325.2s / 0 黑帧 / -15.98 LUFS / 60 字幕事件） |
| 前端 | `#/cinema` 五页：总览 / 镜头台（56 镜筛选/抽屉/播放/编辑 brief/批量生成）/ 生成队列（Worker 状态+实时日志）/ 剪辑台（参数+进度）/ 成片 & QC（播放器+6 项 QC+抽帧+下载） | `tsc --noEmit` 0 error；`vite build` 通过；headless Chrome 截图 5 页 + 抽屉；CDP 实测「打开抽屉 → 改 subject → 保存 brief」toast 成功 |
| 导航 | 旧平台侧栏新增「Cinema 3D」入口；`#/cinema/<tab>` 独立全屏工作室 | 截图确认 |
| 数据 | 真实数据：56/56 镜、云端 22 / 本地 9 / 复用 25 / 缺失 0、成片 5:25 / 96.0MB | API `/overview` `/shots` `/report` `/workers` |

### 截图证据
`tmp/v7/final_shots/`：`overview.png`、`shots.png`、`drawer_open.png`、`drawer_saved.png`、`jobs_running.png`、`assemble.png`、`final.png`、`final_long.png`。

### 遗留 / 下一步
1. 镜头抽屉的「单镜生成」会真实占用本地 GPU（约 60–200s/镜），演示时建议先选 `v5_card_end` 等已缓存镜头；
2. 云端 H3 依赖 MetaSo 余额（402 时后端返回「云端余额不足，请改用本地 H3」）；
3. 缩略图目前用镜头抽帧 sheet；后续可换 `<video>` 首帧海报；
4. 发布/上传（YouTube 等）未接入本工作室，仍走旧「成片房」发布面板。
