# V26：发布包本地导出（视频/封面/字幕/元数据 zip）

> 发布账号还没连（Upload-Post 需要 OAuth），但「成片 → 可上传的发布物料」现在就能做：一键导出发布 zip（成片 + 封面 + 中文字幕 + 元数据 + 平台说明），用户可手动上传 YouTube/TikTok/Instagram。

## 1. 后端
### POST /api/cinema/templates/{template_id}/publish-package
body `{platform?: "youtube"|"tiktok"|"instagram"|"generic" = "generic", force?: bool=false}`
- 校验模板存在、final.mp4 存在（无成片 400）
- 构建 zip 到 `outputs/templates_3d/<template_id>/publish/<slug>_<platform>.zip`（已存在且非 force → 直接复用返回 `cached:true`）
- 包内容：
  - `video/final.mp4`（成片）
  - `cover/cover.jpg`（poster.jpg；无则从 final 抽帧）
  - `subtitles/zh.srt`（由 `work/final.ass` 转 srt；若 ass 缺失则从 timeline.json/episode 镜头台词合成；再不行空文件+warning）
  - `metadata.json`：`{title, description, tags[], template_id, template_label, style, cast[], duration_sec, resolution, fps, size_bytes, generated_at, platform, qc:{...}}`
  - `timeline.json`、`report.json`（存在则复制）
  - `README.txt`：上传步骤 + 平台注意（TikTok/IG 需 9:16 可另行裁切；YouTube 建议标题/简介/标签；封面建议 1280×720）
- 返回 `{ok, template_id, package_url:"/media/templates_3d/<tid>/publish/<file>.zip", size_bytes, files:[{name,size}], cached, platform}`
- 需 `GET /api/cinema/templates/{tid}/publish-package` 列出已生成包（可选）
- 安全：template_id 只允许 `[a-z][a-z0-9_]{1,60}`；zip 内文件名安全；清理旧包（保留最近 5 个）

### 元数据文案
- title：`template_label`（或 `label · 一句话看点`，从 template description 取）
- description：`description` + cast/style 自动生成一段；tags：来自 template tags + style + "AI视频"等
- 平台差异：youtube 加 `#Shorts` 提示？不强制；README 说明

### 自测
- 对已有成片模板（如 custom_tpl_v23_shop）构建 zip → 200、zip 存在、解压校验 5–7 个文件、srt 非空、metadata.json 可解析
- 无成片模板 400；force 重建；cached 复用；非法 id 400；zip 通过 /media 200（Content-Type application/zip）

## 2. 前端
- 成片库卡片：新增「**发布包**」按钮（icon 下载）→ 调 POST → 弹出**导出结果弹层**：文件清单（名称/大小）+「下载 zip」+「复制元数据」+ 平台提醒（TikTok/IG 9:16 提示）
- 一键成片第⑥步「发布前检查」：增加「导出发布包」按钮（当前模板）
- 下载用 blob（`fetch(package_url)` → a.download）；成功 toast；失败/400 友好提示
- 截图：导出中、弹层文件清单、下载完成 toast、无成片提示

## 3. 验收
1. 对 21 个模板任一（含 demo 模板）导出成功，zip 可下载解压；srt 与元数据正确
2. UI 弹层文件清单与下载可用；无成片/失败有可读提示
3. `tsc` 0 / build 通过 / console 无 JS error；截图 tmp/v26/
4. docs/V26 + commit

## 4. 分工
- **tpl-backend**：publish-package 构建 API（zip/srt/元数据/缓存/清理）+ 自测
- **tpl-frontend**：成片库卡「发布包」+ 导出弹层 + 下载/复制/提示；pipeline 第⑥步按钮
- **Lead**：真实导出验证（打开 zip 检查 srt/metadata）、截图、docs、commit
