# V12：发布取片修复 / V5 媒体 URL 修复 / 3D 重生成实跑

> 用户指令：「2 3 4 全做，1 等全部都 OK 了再开始做」（1 = 配置 Upload-Post）。
> 本步完成遗留项 2/3/4，1 保持待配置。

## 1. 发布取片跟随 render_url（遗留 2）

- 后端 `main.py` 新增 `resolve_episode_video(eid, render_url)`：
  - `render_url` 以 `/media/` 开头 → 映射 `outputs/<path>`（文件存在且 >0 字节则采用）；
  - 否则回退 `outputs/episodes/<eid>/final.mp4`；
  - 都无 → None。
- `GET /api/episodes/{eid}/publish/preflight` 的 video 检查与 `POST /api/episodes/{eid}/publish` 统一使用该 helper，detail 显示 outputs 相对路径。
- 实测：`ep_5610123283` video=pass「91.6MB templates_v5/relic_talk_3d/final.mp4」；`ep_001` pass「26.0MB episodes/ep_001/final.mp4」；无成片期 fail。未配置发布仍返回可读 400、不写记录。

## 2. `/api/v5/*` 媒体 URL 修复（遗留 3）

- `backend/app/routers/v5_studio.py` 所有媒体 URL 生成改为真实路径：`/media/templates_v5/<id>/final.mp4`、`contact_sheet.png`、`timeline.json`、`qc.json`、`style_report.json`；storyboard/render 产物同理；旧 `/media/v5/...` 路由保留兼容。
- 实测：`GET /api/v5/outputs` 的 final/poster URL **36/36 HTTP 200**；catalog 无媒体 URL。
- 新 V5 世界前端原本用真实路径兜底，无需改动；旧 `/media/v5` 兼容路由保留。

## 3. 3D 模板「重新生成」真实链路（遗留 4）

- 通过 API 真实触发（非按钮 cancel 路径）：
  1. `POST /api/cinema/templates/generate` `{"templates":["tonight_show"],"force":true}` → job `cjob_d7cbe7ffd1`，本地 ComfyUI H3 逐镜生成 9/9（20:00→20:12，~75s/镜）。
  2. `POST /api/cinema/templates/assemble` `{"templates":["tonight_show"],"force":true}` → job `cjob_30879d04fb`，剪辑完成（~2 分钟）。
- 结果：`outputs/templates_3d/tonight_show/final.mp4` 41.61s / 18.0MB / 1280×720 / 新 mtime；poster.jpg 同步重抽；成片抽帧见 `tmp/v12/tonight_3d_v2_sheet.png`。
- 说明：真实重生成会覆盖该模板 3D 成片（约 9 镜 × 54–110s + 2 分钟剪辑），工作台按钮已带 confirm 提示。

## 4. 待办（遗留 1，等配置）

- Upload-Post 未配置（无 API Key / 账号）：真实发布需在设置里配置后启用；发布前检查面板已能标记阻断项，配好即可发布并产生发布记录。

## 5. 验证

- 后端改动静态/隔离测试 53/53 + HTTP 27/27（详细见 `tmp/v12/api_selftest.md`，tpl-backend 产出）；`pytest tests/test_api.py tests/test_publishing.py` 41 passed。
- 8090 重启 health ok（新 PID 见后端报告）；前端 `tsc`/build 未受影响。
- 发布前检查 UI（成片房）在 video pass 后只剩 `config` 阻断（Upload-Post 未配置）。
