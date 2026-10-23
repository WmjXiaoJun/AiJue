# 造片局 Pipeline CLI 契约 v1
入口：python -m pipeline.cli <cmd>（在仓库根目录运行；Windows PowerShell）
统一：stdout 打印 JSON 行（最后一行是结果）；失败 exit code != 0；日志走 stderr。
环境变量：FFMPEG_BIN / FFPROBE_BIN 可覆盖；默认使用 tools/node_modules/ffmpeg-static/ffmpeg.exe 与 ffprobe-static。

## 1) gen-shot
python -m pipeline.cli gen-shot --spec <shot_spec.json> --out <clip.mp4> [--mock]
shot_spec.json: {shot:{...Shot 字段...}, assets:[...Asset...], episode:{aspect,resolution,fps},
                 mode:"mock|comfyui", params:{}}
输出 JSON: {"ok":true,"path":"...","duration":6.0,"provider":"mock"}
mock 模式必须离线可跑（ffmpeg lavfi 绘制 + 打字/图形），用于 demo 与无 H3 环境。
comfyui 模式：读取 Settings 的 comfyui_url/workflow_template，提交 API-format 工作流并取回视频（demo 可不联网实测，仅实现+单元测试）。

## 2) tts
python -m pipeline.cli tts --text "..." --voice zh-CN-YunxiNeural --out <a.mp3> [--vtt <a.vtt>]
使用 edge-tts；输出 mp3(+vtt 词级时间戳)。失败时 exit 1。

## 3) render
python -m pipeline.cli render --episode <episode.json> --workdir <dir> --out <final.mp4> [--resolution 1280x720] [--fps 25] [--no-bgm]
流程：确保每镜头片段存在(缺失则 mock 生成)→ 逐镜头合成音轨/字幕 → 拼接 → 片头片尾/花字/转场/混音/响度 → 输出 MP4。
进度：stdout 打印 "PROGRESS <0-100> <message>"；最后一行 JSON：{"ok":true,"path":"...","duration":296.0,"shots":52,"size_bytes":...}
要求：全程 ffmpeg 子进程；不依赖网络；中文字体用 C:/Windows/Fonts/msyh.ttc 或 Deng.ttf（字幕样式在 pipeline/templates 内定义）。

## 约定
- episode.json 即 contract/demo_episode.json 的结构（shots 为最终镜头列表）。
- 所有中间文件放 workdir；可重复执行（已存在则复用）。
