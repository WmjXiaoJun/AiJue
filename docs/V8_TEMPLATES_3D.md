# V8：16 个模板 3D 化（本地 H3 批量重制 + 平台换新）

> 需求：「现在效果是可以了，但没有用到现在截图这套；帮我把 16 个模板都生成一下，然后全部都换了」。
> 方案：复用每个模板原有 31–45s 时间线 / 旁白 TTS / 片头片尾卡，把画面全部替换为本地 ComfyUI MiniMax-H3 生成的 3D 电影镜头；成片写 `outputs/templates_3d/`，前端 Cinema Studio 新增「模板库 · 3D」，旧 V5 成片画廊优先播放 3D 版。

## 1. 流水线（新增 `pipeline/templates3d.py`）

| 步骤 | 说明 |
|---|---|
| brief 生成 | 读 `contract/v5/templates/<id>.json` + `outputs/templates_v5/<id>/work/episode.json`：每镜 = 模板 3D 影调（16 套场景/影调表）+ 场景描述 + 角色 3D 描述（从角色 JSON 外观生成）+ 镜头运动 + negative；写 `contract/v5/cinema3d/templates3d/<id>.json` |
| 镜头生成 | 本地 ComfyUI H3 T2VA（4step turbo，768×432，~54–110s/镜），写 `outputs/templates_3d/<id>/shots/<sid>.mp4` + meta；断点续跑/进度 `outputs/templates_3d/progress.json` |
| 剪辑 | 复用 `render_v5.assemble()`：per-cut 转场 / 字幕 / 风格 BGM / 调色 / 包装 / 两遍 loudnorm；片头片尾卡沿用旧品牌卡；输出 `outputs/templates_3d/<id>/final.mp4`（1280×720/25fps）+ `report.json` |
| 批量 | `--generate`（GPU 串行）、`--assemble-watch`（边生成边剪辑，CPU 与 GPU 并行）、`--status` |

复现：
```powershell
python -m pipeline.templates3d --generate --templates all --resolution 768x432
python -m pipeline.templates3d --assemble-watch
python -m pipeline.templates3d --status
```

## 2. 后端（新增 `backend/app/routers/cinema_templates.py`）

- `GET /api/cinema/templates` / `/status`（轻量轮询）/ `/{tid}`（含逐镜状态）
- `POST /api/cinema/templates/generate` `{templates, force?}` → job
- `POST /api/cinema/templates/assemble` `{templates, force?}` → job
- 任务复用 `/api/cinema/jobs` 队列；状态以产物为准（final.mp4/report.json 存在即 done），避免双写 progress.json 竞争。
- 海报：3D 成片优先抽帧 `outputs/templates_3d/<id>/poster.jpg`，未生成时回落旧 2D 海报。

## 3. 前端

- Cinema Studio 新增 tab **「模板库 · 3D」**（02）：16 张模板卡（海报/状态/进度/成片时长大小）、汇总总进度、全部生成/全部合成、播放弹层、逐镜抽屉、3s 轮询。
- 旧 **V5 世界 → 成片画廊**：有 3D 成片的模板显示金色「3D」徽章、播放 3D 版、时长用 3D 版，可「切换 2D 原版」；新增「16 模板 3D 制作进度 → #/cinema/templates」入口。
- 顶栏统计新增「模板 3D x/16」。

## 4. 验收（已完成）

**16/16 模板全部生成并剪辑完成**（2026-11-01 14:58–17:35，本地 ComfyUI H3，120 个镜头，零失败）：

| 模板 | id | 时长 | 大小 | 模板 | id | 时长 | 大小 |
|---|---|---|---|---|---|---|---|
| 美式深夜秀 | tonight_show | 41.61s | 17.5MB | 典籍 | classics | 42.25s | 18.0MB |
| 历史夜话 | history_talk | 36.13s | 11.5MB | 蒙面歌手 | masked_singer | 41.70s | 20.9MB |
| 圆桌派 | roundtable | 41.78s | 14.9MB | 国宝 | national_treasure | 41.06s | 13.4MB |
| 热辣对话 | hot_ones | 38.58s | 12.2MB | 悬疑现场 | mystery | 34.82s | 11.4MB |
| 奇葩说 | qipashuo | 35.72s | 11.9MB | 创投路演 | shark_tank | 31.51s | 14.6MB |
| 慢生活 | slow_life | 35.74s | 13.1MB | 周六夜现场 | snl_sketch | 38.53s | 11.9MB |
| 脱口秀 | standup | 41.31s | 11.1MB | 任务大师 | taskmaster | 32.66s | 12.3MB |
| 叛徒圆桌 | traitors | 37.92s | 14.9MB | 历史悬案 | history_mystery | 40.83s | 12.6MB |

全部 **1280×720 / 25fps / H.264+AAC**，时长与旧 2D 版逐支一致（复用原时间线与旁白 TTS），每支含字幕、BGM、风格包装、统一 loudnorm。

- API：`GET /api/cinema/templates` → `{total:16, done:16, assembled:16, clips_done:120, clips_total:120}`
- 前端：Cinema Studio「模板库 · 3D」16/16 已完成、总进度 100%；V5 世界成片画廊 16 支全部显示 3D 版（金色「3D」徽章 + 3D 时长 + 3D 海报），可「切换 2D 原版」。
- 证据：`tmp/v8/16_templates_3d_montage.png`（16 支各一帧）、`tmp/v8/finals/<id>.png`（逐支 8 帧）、`tmp/v8/ui/templates_done.png`、`tmp/v8/ui/v5_gallery_done.png`、`outputs/templates_3d/<id>/report.json`。


## 5. 已知边界

1. 本地 H3 768×432 放大到 720p 略软；角色/场景为 AI 再创作，跨镜角色一致性有限（同一模板内会出现配角差异）。
2. 片头/片尾沿用旧 2D 品牌卡（1.4/1.6s），其余画面全部 3D。
3. 生成全程约 2–2.5 小时 GPU（120 镜），期间 ComfyUI 串行，不能并行做其它本地生成。
