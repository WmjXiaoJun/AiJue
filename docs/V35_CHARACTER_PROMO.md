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
