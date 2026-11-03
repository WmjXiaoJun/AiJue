# V29：成片库批量导出发布包

> 现有：单支成片可导出发布包（16:9 / 竖版 blur_pad / crop）；批量生成也已支持。
> 本轮：成片库支持**多选 + 批量导出发布包**（一次构建多支，带平台/竖版模式统一选择、进度、逐项下载/全部下载、取消）。

## 1. 后端
### POST /api/cinema/publish-packages/batch
body `{template_ids: string[], platform?: "youtube"|"tiktok"|"instagram"|"generic", vertical?: bool, vertical_mode?: "blur_pad"|"crop", force?: bool}`
- 校验非空/去重/模板存在且已有 final（无成片的项返回 failed，不整体失败）
- 创建 batch job（后台串行线程），逐项调用现有 `_build_publish_package`（复用 lock，避免并发构建同 zip）
- item：`{template_id,label,status:queued|building|done|failed, package_url, size_bytes, files_count, error, started_at, finished_at}`
- 返回 `{batch_id,status:"queued",total,items[],platform,vertical,vertical_mode}`

### GET /api/cinema/publish-packages/batch/{batch_id}
`{id,status:queued|running|done|partial_failed|failed|canceled,current_index,total,items[],summary{done,failed,queued,building},elapsed_sec,platform,vertical,vertical_mode}`

### POST /api/cinema/publish-packages/batch/{id}/cancel
当前项完成后停止，未开始项 canceled。

### GET /api/cinema/publish-packages/batches?limit=10
最近批次。

- 持久化 `outputs/publish_batches/<id>.json`；重启把中断 batch 标 failed
- 自测：批量 3 模板 tiktok+vertical+crop → done 3/3、各项 zip /media 200、大小/文件数正确；full 21 模板不实跑（可选）；取消；空/无成片校验；写 tmp/v29/backend_selftest.md

## 2. 前端（成片库）
- 成片库页头新增「**批量导出**」开关（进入多选模式）：
  - 卡片 checkbox；底部 sticky 条：已选 N + 平台下拉（本地/YouTube/TikTok/Instagram）+ 竖版开关（TikTok/IG 默认勾选）+ 模式选择（模糊/裁切）+「批量导出发布包」「取消选择」
  - 提交成功后打开**批量导出抽屉**：逐项状态（排队/构建中/完成/失败）、包大小/文件数、单项「下载」、底部「全部下载」（顺序 blob 下载）/「取消整批」
  - 3s 轮询；离开页面回来自动恢复运行中批次；完成提示
- 无成片项（不应存在）或失败项显示可读错误
- 截图 tmp/v29/ui/：多选条、导出中、完成列表、全部下载、取消

## 3. 验收
1. API 批量 3 模板 tiktok+crop → done 3/3，zip 均可下载解压（竖版 1080×1920）
2. UI 多选 3 支 → 批量导出 → 抽屉逐项完成、下载/全部下载可用；取消可用
3. tsc 0 / build / console 无 error；截图 tmp/v29/
4. docs/V29 + commit

## 4. 分工
- **tpl-backend**：批量发布包导出 API + 串行执行 + 持久化 + 自测
- **tpl-frontend**：成片库多选 + 批量参数 + 导出抽屉 + 下载/全部下载/取消
- **Lead**：真实批量导出验证（zip 解压抽查）、截图、docs、commit
