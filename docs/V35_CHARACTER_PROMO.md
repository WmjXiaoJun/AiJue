# V35：角色宣传片（多场景/多机位拼接）

> 试镜视频已支持「单场景 + 单机位 + 多句台词 + 字幕」。本轮做**角色宣传片**：2–3 个场景/机位（各自台词）依次生成，再拼成一条带片头/场景卡的宣传片，用于角色展示/选角。

## 1. 后端（新 router `character_promo.py`，复用 audition 内部能力）
### POST /api/v5/characters/{character}/promo
body：
```jsonc
{
  "style": "realistic",                 // anime|stylized_25d|realistic
  "title": "老周 · 深夜电台主理人",      // 可选，片头副标题
  "seed": 42,                           // 可选
  "shots": [                            // 1–3 镜
    {"lines": ["大家好，我是老周。"], "background": "studio", "camera": "closeup", "subtitles": true},
    {"lines": ["深夜的咖啡馆，最适合聊点真的。"], "background": "cafe", "camera": "medium", "subtitles": true}
  ]
}
```
- 校验：角色/立绘存在；shots 1–3；每镜 lines ≤2 行、每行 ≤60 字、单镜估算 ≤8s；background/camera 合法（复用 V34 枚举）；非法 400
- 任务状态：`queued → running(stage: shot 1/n tts → shot 1/n generate → ... → assemble) → done/failed/canceled`；progress 0–100
- 每镜复用 audition 流程：逐句 TTS（角色音色）→ 拼接 → segments → SRT → H3 audio_ref（1024×576）→ 字幕烧录
- 拼接（assemble）：
  - 片头卡 2.2s（角色名 + title）
  - 每镜前场景卡 1.0s（背景 label · 机位 label）
  - 各镜正片（已烧录字幕）
  - ffmpeg concat 重编码 1024×576 / 24fps / AAC；生成海报帧（片头或第 1 镜中段）
  - 合并 SRT（各镜 segments 按拼接时间轴偏移）
- 输出 `outputs/character_demos/<character>_promo_<job_id>.mp4`、poster、srt；持久化 `outputs/promo_jobs/<id>.json`；重启中断标 failed
- `GET /api/promos/{job_id}`；`POST /api/promos/{job_id}/cancel`；`GET /api/v5/characters/{character}/promos?limit=5`
- 互斥：3D pipeline / audition / 另一 promo 运行中 → 409（reason 可读）

### 自测
- laozhou 2 镜（studio closeup + cafe medium）真实生成 → done；输出 1024×576、有音轨、时长≈片头+场景卡+两镜；抽帧确认片头卡/场景卡/字幕；poster/srt 可访问
- 参数 400（4 镜 / 非法背景 / 单镜超长）；取消；409；持久化；写 tmp/v35/backend_selftest.md

## 2. 前端（角色详情）
- 试镜视频区块 header 增加「生成角色宣传片」按钮
- 宣传片弹层：
  - 风格（写实/动漫/2.5D，默认写实）、标题（可选）
  - 场景行 2 条默认（棚拍·近景 / 咖啡馆·中景），每条：背景下拉、机位下拉、台词 textarea（1–2 行）；可添加第 3 条/删除
  - 预计耗时提示「约 6–12 分钟，可离开页面」
- 进行中卡片：stage（第 i/n 镜 配音/生成 → 拼接中）+ 进度 + 已用时间 + 取消
- 完成：内嵌播放 + 时长/大小/风格 + 下载视频/海报/SRT；最近宣传片列表（≤3，带状态/场景数）
- 截图 tmp/v35/ui/：弹层、进行中、完成播放（片头/场景卡/字幕可见）、最近列表、错误

## 3. 验收
1. UI 生成 2 镜宣传片 → 约 8–10 分钟 → 成片含片头卡+场景卡+字幕，可播放/下载
2. API 真实生成 1 条校验（ffprobe/抽帧），参数 400、取消、409
3. tsc 0 / build / console 无 error；截图 tmp/v35/
4. docs/V35 + commit

## 4. 分工
- **tpl-backend**：promo router（多镜生成/拼接/海报/SRT/状态/取消/持久化）+ 自测
- **tpl-frontend**：宣传片弹层/进度/完成播放/最近列表 + 截图
- **Lead**：真实 2 镜生成验证、截图、docs、commit


## 5. 交付结果（2026-11-04 06:52）

### 后端（21/21 自测 PASS）
- 新增 `backend/app/routers/character_promo.py` + `main.py` 注册 + `character_audition.py` 反向互斥：
  - `POST /api/v5/characters/{id}/promo`（202）；校验角色/立绘、shots 1–3、每镜 lines ≤2/行 ≤60 字/单镜 ≤8s、背景机位枚举；非法 400
  - 任务 `queued→running(stage shot_i_tts / shot_i_generate / assemble)→done`；每镜复用 audition 流程（TTS+segments+SRT+H3+字幕烧录）
  - 拼接：片头卡 2.2s + 每镜场景卡 1.0s + 正片；concat 重编码 1024×576/24fps/AAC；poster + 合并 SRT（偏移）
  - `GET /api/promos/{id}`、`POST .../cancel`、`GET /api/v5/characters/{id}/promos?limit=5`；持久化 `outputs/promo_jobs/*.json`；3D/audition/promo 互斥 409
- 实测：laozhou 2 镜（棚拍近景 + 咖啡馆中景）**248.3s done**，输出 1024×576/24fps/14.59s / 1.13MB；抽帧片头卡/场景卡/两镜字幕均正确；poster/SRT 200；参数 400 七类、取消、409 全 PASS
- 8090 PID 29792

### 前端
- 试镜视频区块新增「生成角色宣传片」：弹层（风格/标题 + 2 镜默认场景 + 可加第 3 镜/删除 + 单镜校验 + 6–12 分钟提示）
- 进行中：第 i/n 镜 配音/生成 → 拼接中 + 进度/已用时间/取消；完成：内嵌播放 + 时长/大小/风格/镜数 + 下载视频/海报/SRT；最近宣传片列表（≤3）
- 截图 `tmp/v35/ui/`（6 张）

### Lead 独立 E2E
- 2 镜宣传片：208.1s done，14.59s / 1,132,862B / 1024×576 + 音轨；片头卡「老周 · 老周·深夜电台主理人」、场景卡、字幕抽帧确认（`tmp/v35/frame_*.jpg`）
- 看片：`/media/character_demos/laozhou_promo_pr_2d08b33a90e7.mp4`（另有 backend 样本 `laozhou_promo_pr_446fc0433e33.mp4`）
- UI（生产构建）：角色详情试镜区显示宣传片入口、内嵌播放真实成片、弹层场景编辑可用（`tmp/v35/lead/`）

### 说明 / 遗留
- 真实耗时约 2 分钟/镜（2 镜 208–248s），前端 loading 建议 4–8 分钟
- 片头/场景卡为 PIL 生成静音段；poster 取片头帧；取消保留中间镜文件但无最终产物
- H3 原生 24fps；3D pipeline 启动时未反向检查 promo（沿用现有范围）
