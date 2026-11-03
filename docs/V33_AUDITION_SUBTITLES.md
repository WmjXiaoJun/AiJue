# V33：试镜视频支持多句台词 + 烧录字幕

> 当前试镜视频只支持单句台词、无字幕。本轮：多行台词（每行一句，逐句 TTS 后拼成一条完整音频）→ H3 生成 → **自动生成 SRT 并烧录到视频**（也可返回 srt 链接）。

## 1. 后端（character_audition.py 扩展）
### POST /api/v5/characters/{character}/audition 新参数
- `lines?: string[]`（每行一句；最多 6 行、每行 ≤80 字；与 `text` 二选一，lines 优先）
- `subtitles?: bool = true`（默认烧录字幕）
- 台词为空 → 默认文案单行（同 V32）
- 台词总时长限制：TTS 合计 ≤ **12s**（超过 400「台词过长，请精简到 12 秒内」）

### 生成流程
1. 逐句 TTS（同一角色音色；breeze 优先/edge 兜底），句间插 0.35s 静音，拼接成完整 wav；记录每句 `start/end` 秒
2. H3 `run_h3_ref.py --mode audio_ref`（1024×576，duration=总音频+0.1，同 V32 风格 prompt）
3. 生成 SRT（`<job>_subtitles.srt`，按 segments 时间轴）
4. `subtitles=true` 时用 ffmpeg 烧录（`subtitles=...:force_style`：白字黑边、底部居中、字号约 18、MarginV 24）→ 输出 `<char>_<job>_sub.mp4`；失败仅 warning，保留无字幕版
5. 任务字段新增：`lines[]`、`segments:[{text,start,end}]`、`srt_url`、`subtitles_burned`、`output_url`（烧录版）、`plain_output_url`（无字幕版）

### 自测
- 3 句台词 + subtitle=true → done；输出视频 ffprobe 1024×576 + 音轨；duration≈总 TTS+0.35*pause；srt 文件 3 条、时间轴递增；烧录版可访问
- subtitle=false → 无字幕版 + srt_url 仍有；lines 超过 6 行/单行超长/总时长>12s → 400
- 取消/409/持久化回归；写 tmp/v33/backend_selftest.md

## 2. 前端（角色详情试镜弹层）
- 台词输入改为 textarea「每行一句台词（最多 6 行）」：显示行数/预计时长；空行忽略；行数超限提示
- 增加 checkbox「烧录字幕」默认勾选；说明「白字黑边，底部居中」
- 生成中进度卡显示「第 i/n 句配音 / 生成视频」阶段；完成播放烧录版；额外给「无字幕版下载」「SRT 下载」入口；最近列表标注「字幕」徽标
- 截图 tmp/v33/ui/：多行输入、字幕开关、进行中（多句）、完成播放（可见字幕）、srt 下载、错误（台词过长）

## 3. 验收
1. UI：输入 3 行台词 → 生成 → 视频中可见字幕、时长≈台词合计
2. API：真实生成 3 句 + 字幕；srt 3 条；ffprobe 校验；参数错误 400
3. tsc 0 / build / console 无 error；截图 tmp/v33/
4. docs/V33_AUDITION_SUBTITLES.md + commit

## 4. 分工
- **tpl-backend**：多行 TTS/拼接/SRT/烧录/参数校验 + 自测
- **tpl-frontend**：多行输入/字幕开关/多句进度/完成播放与 srt 下载 + 截图
- **Lead**：真实生成 3 句带字幕验证、截图、docs、commit
