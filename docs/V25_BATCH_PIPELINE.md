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
