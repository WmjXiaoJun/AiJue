# 成片导出（剪映草稿 / 时间线）契约 v1

目标：把成片时间线导出为剪映专业版可导入的草稿文件夹；同时产出 SRT/清单。

## 1. 现状依据
本机安装剪映专业版（%LOCALAPPDATA%\JianyingPro），已有真实草稿样例：
`%LOCALAPPDATA%\JianyingPro\User Data\Projects\com.lveditor.draft\<draft>\{draft_content.json, draft_meta_info.json, draft_settings, ...}`
导出器必须兼容该结构（v5/v6 风格）。

## 2. CLI（pipeline.capcut）
```
python -m pipeline.capcut export --manifest <export_manifest.json> --outdir <dir> [--name 造片局_EP001] [--copy-media]
```
Manifest 结构：
```json
{
  "name": "造片局_EP001",
  "width": 1920, "height": 1080, "fps": 30, "duration": 299.0,
  "video": [{"path": "outputs/demo/final.mp4", "start": 0, "duration": 299.0, "type":"main"}],
  "audio": [{"path": "outputs/demo/audio/narration.wav", "start": 0, "duration": 299.0, "volume": 0.9}],
  "subtitles": [{"start": 0.5, "end": 3.2, "text": "..."}]
}
```
产物：
- `{outdir}/{name}/draft_content.json`、`draft_meta_info.json`、`draft_settings`、materials/（copy-media 时复制媒体）
- `{outdir}/{name}/subtitles.srt`、`export_manifest.json`、`README.txt`（导入步骤）
- 打印 JSON 摘要及草稿目录路径。
- 提供单元测试：结构关键字段（canvas_config/tracks/materials/duration）存在且合法。

## 3. 后端/前端
- POST /api/episodes/{eid}/export {type:"capcut"} -> Job(type=export)，后台调 CLI，产物落 outputs/exports/{name}/，返回路径。
- 成片房新增「导出剪映草稿」按钮 + 成功后展示路径/打开说明（复制到剪映草稿目录或用剪映导入）。
