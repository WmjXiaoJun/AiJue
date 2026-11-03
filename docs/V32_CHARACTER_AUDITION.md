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
