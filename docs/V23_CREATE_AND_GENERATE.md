# V23：创建模板 → 立即生成 3D（一步完成）

> 承接 V21/V22：模板创建与 3D 生成已打通，但需要两步（创建 → 去一键成片点生成）。本轮把「创建后立即生成 3D」做成一个开关，创建成功即自动提交 pipeline job，并在 UI 内直接看进度。

## 1. 后端

### POST /api/v5/templates 增加可选 `auto_generate: bool = false`
- 创建成功后（template/episode/TTS/semantic 都完成后），若 `auto_generate=true`：
  - 调用 pipeline_ui 的启动逻辑（建议抽一个 `start_pipeline_job(template_id, reuse_shots=False, dry_run=False) -> dict`，供两个 router 复用），启动后台 job
  - 单并发：若已有 running job → **创建仍成功**，返回 `pipeline: {started: false, reason: "busy", running_job_id: ...}`，不报错
  - 成功启动 → 返回 `pipeline: {started: true, job_id, status: "queued"|"running"}`
- 响应保持向后兼容（无 auto_generate 时字段可省略或 `pipeline: null`）
- 校验：dry_run 模式不启动；未知 style 等仍 400；创建失败不残留 job
- 自测：创建 4 镜小模板 + auto_generate=true → job 已启动、状态 202/queued→running；另有 busy 场景返回 started:false 且创建成功

## 2. 前端

### 新建模板向导 step 4（预览/创建）
- 增加 checkbox：「创建后立即生成 3D（约 10–45 分钟，后台任务）」，默认 **勾选**，下方小字说明「生成期间可离开页面，进度在『一键成片』可见」
- 提交时带 `auto_generate: true`
- 成功且 `pipeline.started=true`：
  - 关闭向导、刷新列表、toast「模板已创建，3D 生成已开始」
  - 展示 banner/按钮「查看生成进度 →」，跳转 `#/pipeline` 并自动选中该模板、定位到第④步（可用 `#/pipeline?template=<id>` 或 sessionStorage 传递，组件解析后清掉）
  - 模板库卡片若该模板有 running job → 显示「生成中」badge（读 `/api/pipeline/runs` 匹配 template_id，10s 轮询可选）
- `pipeline.started=false`（busy）→ toast 提示「已有任务运行，模板已创建；可在生成队列/一键成片稍后生成」
- 无 auto_generate 时行为与今天一致

### UI 顺带优化
- 创建按钮 loading 文案区分「创建中…」/「创建并生成中…」
- 向导 step4 摘要展示「预计生成时长 10–45 分钟」与「将生成 4 镜」
- 保持现有 shell/视觉规范

## 3. 验收
1. UI：勾选「创建后立即生成」→ 创建 4 镜模板 → 自动跳转进度页并显示 running；后端 job 真实启动
2. API：`auto_generate=true` 返回 job_id；busy 场景返回 started:false 且模板创建成功
3. 生成完成后：模板库卡片变「已完成」、成片库出现新片、可播放（Lead 真实跑一支验证）
4. `tsc` 0 / build 通过 / console 无 JS error；截图 `tmp/v23/`
5. docs/V23 + commit

## 4. 分工
- **tpl-backend**：POST /api/v5/templates auto_generate + 复用 helper + busy 处理 + 自测
- **tpl-frontend**：向导 checkbox、提交带参、成功跳转进度、生成中 badge、UI 文案
- **Lead**：真实跑一支 4 镜模板验证全链路 + 截图 + docs + commit


## 5. 交付结果（2026-11-03 15:08）

### 后端（tpl-backend，34/34 自测 PASS）
- `pipeline_ui.start_pipeline_job(...)` 抽取复用；`POST /api/pipeline/run` 变薄封装
- `POST /api/v5/templates` 新增 `auto_generate` / `dry_run`；`_maybe_autostart` 在 template/episode/TTS/semantic **全部完成后**启动 job；响应新增 `pipeline` 字段（started/job_id/status 或 busy/error；无参时 null，向后兼容）
- busy 语义：已有 running job 时创建仍成功，返回 `started:false, reason:"busy", running_job_id`
- 自测：4 幕模板 auto_generate → job 启动（queued→running→cancel 验证状态机）；busy/dry_run/无参/未知 style 全 PASS；测试数据已清理

### 前端（tpl-frontend）
- 向导 step4：checkbox「创建后立即生成 3D（约 10–45 分钟）」默认勾选 + 「将生成 N 镜 / 预计 10–45 分钟」摘要；loading「创建并生成中…」
- 成功分支：`pipeline.started=true` → toast「模板已创建，3D 生成已开始」+ banner「查看生成进度 →」→ `#/pipeline?template=<id>` 自动选中模板并定位第④步（URL 清理）；busy → 「已有任务运行，模板已创建」
- 模板库卡：`/api/pipeline/runs` 10s 轮询 → 运行中模板显示「生成中」badge
- `tsc` 0 / build 通过；6 张状态截图 `tmp/v23/ui/`

### Lead 真实 E2E（完整链路）
| 项 | 值 |
|---|---|
| 模板 | `custom_tpl_v23_shop` · 深夜便利店「十分钟」/ film_interview / 4 幕（2 对话 + 2 b-roll）/ 15.15s |
| 创建 | `POST /api/v5/templates` + `auto_generate:true` → **19.7s** 完成，返回 `pipeline:{started:true, job_id:"pl_240ad03b4f3b"}` |
| 生成 | job 真实 GPU：4 镜 T2VA+ref2va → 剪辑 → 海报，**874.1s ≈ 14.6 分钟**，done 4/4 |
| 成片 | **15.15s / 3.6MB / 1280×720 / 25fps / 有音轨**；poster/report/timeline 齐全 |
| 当前状态 | 模板库 **21/21 完成、151/151 镜头**；成片库 21 支 |
| 证据 | 抽帧 `tmp/v23/verify/contact_sheet.png`（16mm 便利店场景，字幕与台词一致）；UI：`tmp/v23/lead/01_library_running_badge_real.png`（运行中 badge 真实数据）、`02_pipeline_autoselect_running.png`（自动定位第④步）、`03_library_done_after.png`、`04_final_library_after.png` |
| 直链 | `/media/templates_3d/custom_tpl_v23_shop/final.mp4` |

- 说明：真实 E2E 轮询客户端在任务完成瞬间遇到一次本地连接中止（httpx ReadError），但后端 job 已正常完成（status=done、final.mp4 已产出）；不影响交付
- 产物：模板合同 `contract/v5/custom/templates/custom_tpl_v23_shop.json` + semantic brief 入库（第 21 个模板）

### 使用方式
1. 模板与素材 →「新建模板」→ 第④步保持勾选「创建后立即生成 3D」→ 创建
2. 成功后点「查看生成进度 →」自动跳到一键成片第④步看逐镜进度；也可稍后在模板库/成片库查看结果

### 遗留
- 生成期间离开页面不影响任务（后台 job + 落盘）；回来可在「一键成片 → 第④步」或模板卡 badge 看到状态
- busy 时模板照常创建，稍后手动生成
