# V25：批量生成 / 批量合成（多模板排队）

> 单模板生成 20–45 分钟、pipeline 单并发；21 个模板要逐个点非常低效。本轮做「多选模板 → 批量排队 → 队列进度/取消/逐个出片」。

## 1. 后端（pipeline 队列）
### POST /api/pipeline/batch
body `{template_ids: str[], reuse_shots?: bool=false, stop_on_error?: bool=false}`
- 校验模板存在、非空、去重；已有运行中的 batch → 409（带当前 batch_id）
- 创建 batch job：`{batch_id, status:"queued", items:[{template_id,label,status:"queued"}], total, created_at}`
- 队列执行器（后台线程）：**串行**处理 items：
  - 等待当前 pipeline job 空闲（复用 `start_pipeline_job` / active job 检查）
  - 启动 job（reuse_shots 透传）→ 轮询到 done/failed/canceled
  - 更新 item：job_id/status/stage/progress/final_url/error/started_at/finished_at
  - `stop_on_error=true` 时失败即把剩余标 skipped；默认继续
- 落盘 `outputs/pipeline_batches/<batch_id>.json`；服务重启把中断 batch 标 failed（或恢复 queued 项继续）

### GET /api/pipeline/batch/{batch_id}
返回 `{id, status, current_index, items[], total, created_at, elapsed_sec, summary:{done,failed,canceled,queued,running}}`

### POST /api/pipeline/batch/{batch_id}/cancel
取消当前 job + 未开始项标 canceled；已 done 保留

### GET /api/pipeline/batches?limit=10
最近 batch 列表

### 自测
- 用 reuse_shots=true 批量 3 个模板（快速纯剪辑）→ batch 状态 queued→running→done，items 全部 done 且 final_url 可访问
- 中途 cancel → 当前 canceled、剩余 canceled
- 与单发 run 并发规则：batch 运行时 `POST /api/pipeline/run` 返回 409；单发运行时 batch 409
- 持久化文件存在、重启恢复/失败标记；写 tmp/v25/backend_selftest.md

## 2. 前端（模板与素材 · 模板 tab）
- 顶部「批量选择」开关：开启后模板卡出现 checkbox；底部 sticky 批量条：`已选 N 个 · 预计 X 分钟` +「批量生成」「批量合成（复用已有镜头）」「取消选择」
- 提交 batch 后：弹出/右侧抽屉显示**批量队列面板**：items 列表（模板名/状态/当前 job 阶段进度/final 链接），3s 轮询 `GET /api/pipeline/batch/{id}`；支持「取消整批」；完成后可跳成片库
- 模板卡与成片库：batch 中 running 的模板显示「排队中/生成中 i/N」badge（复用 V23 轮询逻辑，扩展 batch 状态）
- 空态/失败可读；批量条在滚动时固定可见；shell 视觉一致
- 截图：批量选择态、批量条、队列进行中、完成、取消

## 3. 验收
1. UI 多选 2–3 模板 → 批量合成（reuse）→ 队列面板逐个 running→done，成片可播放
2. 取消整批 → 状态正确；与单发任务互斥 409 提示友好
3. `tsc` 0 / build 通过 / console 无 JS error；截图 tmp/v25/
4. docs/V25 + commit

## 4. 分工
- **tpl-backend**：batch API + 队列执行器 + 持久化 + 互斥 + 自测
- **tpl-frontend**：批量选择/批量条/队列面板/badge/取消 + 截图
- **Lead**：真实批量 reuse E2E + 截图复核 + docs + commit


## 5. 交付结果（2026-11-03 16:50）

### 后端（tpl-backend，23/23 自测 PASS）
- `pipeline_ui.py` 新增 `POST /api/pipeline/batch`、`GET /api/pipeline/batch/{id}`、`GET /api/pipeline/batches`、`POST /api/pipeline/batch/{id}/cancel`
- `_run_batch` 后台串行：等 pipeline 空闲 → `start_pipeline_job(batch_id=)`（reuse 透传）→ 轮询 job → 更新 items；`stop_on_error` 失败后剩余 skipped；持久化 `outputs/pipeline_batches/<id>.json`；重启中断标 failed
- 双向互斥：batch 运行时单发 409（reason=batch_running）、单发运行时 batch 409（reason=pipeline_running）
- 实测：reuse 批量 3 模板 32.6s 全 done、final_url 200；取消→canceled；互斥 409；重启恢复

### 前端（tpl-frontend）
- 模板 tab「批量选择」模式：卡片 checkbox（金色描边+点击切换）+ 底部 **sticky 批量条**（已选 N · 预计 X–Y 分钟 +「批量生成」「批量合成（复用已有镜头）」「取消选择」）
- **批量队列右侧抽屉**：summary chips（完成/运行/排队/失败/耗时）+ items（状态/阶段/进度条/百分比/final 入口/错误）+ 3s 轮询 +「取消整批」二次确认 + 完成态「去成片库」
- 模板卡/成片库卡：「排队中」「生成中 i/N」badge（优先于单发 badge）；进入页面自动恢复运行中队列
- 409/404/405/失败友好提示；`tsc` 0 / build 通过 / console 无 JS error

### Lead 验收
- API E2E（`tmp/v25/e2e_batch.py`）：批量 reuse 3 模板（real_street/real_cafe/tonight_show）→ done 3/3、final_url 全部 200（15.1/12.0/17.2MB）；取消整批 → canceled 3；互斥正常
- UI E2E（`tmp/v25/lead_ui.py`）：批量选择 2 模板 → 批量合成 → 队列抽屉 running（1 完成/1 运行/排队）→ 批量完成；console 无错误
- 截图：`tmp/v25/ui/`（7 张前端流程）、`tmp/v25/lead/`（Lead 复拍：选择/运行/完成）
- 证据 JSON：`tmp/v25/batch_verify.json`、`tmp/v25/e2e_result.json`、`outputs/pipeline_batches/*.json`

### 说明
- 批量串行（后端单并发）；实际耗时：合成≈10–13s/模板（复用镜头），完整生成 20–45 分钟/模板
- 服务重启会把运行中 batch 标 failed（需重新提交剩余模板）
- 队列面板终态不自动弹窗（保留在页面）；后续可加全局完成提醒
