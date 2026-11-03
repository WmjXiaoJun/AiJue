# V32：角色试镜视频（产品化）

> V31 已用脚本验证「角色立绘 + 角色声音 → H3 图生视频 + 口型同步」可行（老周 7.29s / 1024×576 / 3m11s）。
> 本轮把它做成产品功能：角色详情一键生成试镜视频（选立绘版本/台词 → 后台任务 → 进度 → 在线播放/下载）。

## 1. 后端（新 router `character_audition.py`）
### POST /api/v5/characters/{character}/audition
body `{text?: str, style?: "anime"|"stylized_25d"|"realistic", seed?: int, prompt_extra?: str, width?: int=1024, height?: int=576}`
- 校验角色存在；style 对应立绘文件存在（realistic→portrait_real.png、stylized_25d→portrait_25d.png、anime→portrait.png），缺失 400 可读提示
- text 缺省：`各位好，我是{label}。这是用我的角色立绘生成的一段试镜。`（≤120 字）
- TTS：复用角色音色（breeze 优先/edge 兜底，同 `/api/v5/voice/{id}/preview` 逻辑），24k mono wav
- 参考图：立绘按 16:9 裁切（人脸居中，顶部偏移），保存到任务目录
- 生成：H3 `integrations/comfyui/run_h3_ref.py --mode audio_ref`（1024×576、25fps、duration=音频时长+0.1）
  - style 对应 prompt：realistic=写实电影感、anime=动漫赛璐璐、stylized_25d=3D 动画感；都要求「保持参考图身份/发型/服装，口型同步」
  - GPU 串行：与 3D pipeline/批量/概念图互斥；有 3D job 或另一 audition 运行中 → 409（带原因）
- 后台任务：状态机 queued→running（stage: tts/crop/generate/done）→done/failed/canceled；输出 `outputs/character_demos/<character>_<job_id>.mp4`，`/media/character_demos/...` 可访问
- 返回 202 `{ok, job_id, character, style, status:"queued", text, estimated_sec:200}`
- 持久化 `outputs/audition_jobs/<job_id>.json`；重启把中断任务标 failed

### GET /api/auditions/{job_id}
`{id,character,style,status,stage,progress,text,output_url,duration_sec,size_bytes,error,created_at,started_at,finished_at,elapsed_sec}`

### POST /api/auditions/{job_id}/cancel
运行中取消（终止 H3 子进程）；未开始直接 canceled

### GET /api/v5/characters/{character}/auditions?limit=10
最近试镜记录（含 status/output_url）

- 自测：真实生成 1 条（laozhou/realistic，默认台词）→ done，/media 200，ffprobe 1024×576 + 音轨；取消 1 条；缺立绘角色 400；并发 409；重启恢复；写 tmp/v32/backend_selftest.md

## 2. 前端（角色详情抽屉）
- 立绘版本下方新增「**试镜视频**」区块：
  - 按钮「生成试镜视频」→ 小弹层：立绘版本（默认当前选中）、台词 textarea（默认文案可改）、提示「约 3–4 分钟，可离开页面」→ 提交
  - 任务进行中：进度卡（排队/配音/生成中，stage + 进度 + 已用时间），可取消；离开页面再回来自动恢复（按角色查最近任务）
  - 完成：内嵌 `<video controls>` 播放 + 下载 + 时长/大小/风格标签；失败可读错误 + 重试
  - 最近试镜列表（最多 3 条）：缩略/状态/播放
- 截图 tmp/v32/ui/：生成弹层、进行中、完成播放、列表、失败

## 3. 验收
1. UI：角色详情生成试镜 → 约 3–4 分钟 → 在线播放；可取消；错误可读
2. API：真实生成 1 条、ffprobe 校验、并发 409、无立绘 400、持久化
3. tsc 0 / build / console 无 error；截图 tmp/v32/
4. docs/V32 + commit

## 4. 分工
- **tpl-backend**：audition 后端（TTS/裁图/H3 子进程/状态/取消/持久化/互斥）
- **tpl-frontend**：角色详情试镜区块（弹层/进度/播放/列表/错误）
- **Lead**：真实生成 1 条验证 + 截图 + docs + commit


## 5. 交付结果（2026-11-03 23:20）

### 后端（20/20 自测 PASS）
- 新增 `backend/app/routers/character_audition.py` + `main.py` 注册：
  - `POST /api/v5/characters/{id}/audition`（202；角色/style/对应立绘校验，缺失 400）
  - `GET /api/auditions/{job_id}`（status/stage/stage_history/progress/output_url/duration/size/error/时间）
  - `POST /api/auditions/{job_id}/cancel`（kill H3 子进程树）
  - `GET /api/v5/characters/{id}/auditions?limit=10`
- 流程：TTS（角色音色，breeze 优先/edge 兜底）→ 立绘 16:9 裁图（顶部偏移 40px，LANCZOS 1024×576）→ H3 `run_h3_ref.py --mode audio_ref`（1024×576、duration=音频+0.1、按风格选 prompt）→ ffprobe 收尾；stage `tts/crop/generate/done`
- 输出 `outputs/character_demos/<char>_<job>.mp4`；持久化 `outputs/audition_jobs/*.json`；重启中断标 failed
- 互斥：3D pipeline 或另一 audition 运行中 → 409（reason 可读）
- 实测：laozhou/realistic 冷启动 **138.2s**、复跑命中 ComfyUI 缓存 9.1s；1024×576 / 5.88s / 866KB / 含音轨；取消/409/缺立绘/重启恢复全 PASS
- 8090 PID 46908

### 前端
- 角色详情立绘版本下方「试镜视频」区块：生成弹层（立绘版本/台词/约 3–4 分钟提示）、进行中进度卡（stage/进度/已用时间/取消）、完成内嵌播放 + 时长/大小/风格 + 下载/重新生成、失败可读错误、最近试镜列表（≤3 条）、离开页面自动恢复
- 截图 `tmp/v32/ui/`（7 张）

### Lead 独立 E2E
- `POST .../audition` laozhou/realistic 自定义台词 → 202；轮询 200.7s 后 done；output `/media/character_demos/laozhou_au_46dfcbc414a5.mp4` → 200 (1,282,732B)；ffprobe **1024×576 + 音轨 + 8.0s**
- UI（生产构建，`tmp/v32/lead/`）：角色详情「试镜视频」内嵌播放真实成片（8s/1.2MB/写实），最近列表显示真实任务（已完成/已取消/跟踪）；生成弹层可用；console 无功能错误
- 说明：首次 E2E 因后端重启竞态被标 failed（已知持久化恢复语义），后端稳定后重跑通过

### 使用
1. 角色与声音 → 打开角色 →「试镜视频」→「生成试镜视频」
2. 选立绘版本（动漫/2.5D/写实）、改台词 → 提交
3. 约 2–4 分钟（GPU 串行）→ 内嵌播放/下载；可取消；离开页面回来自动恢复

### 遗留
- H3 原生 24fps（接口按实际返回）；冷启动 2–3 分钟；取消只终止 CLI，ComfyUI 已提交的 prompt 可能后台跑完
- 3D pipeline 启动时不反向检查 audition（本轮范围外）
