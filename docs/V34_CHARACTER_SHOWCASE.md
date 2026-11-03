# V34 附加交付：8 个角色试镜视频 + 角色试镜合集

> 与 V34 背景/机位功能同批完成：用试镜 API 为 8 个角色各生成 2 句台词 + 烧录字幕的试镜视频，并与已有 3 条（阿栗/木兰/大魏）剪成一条合集。

## 1. 新增 8 条试镜

| 角色 | 台词（2 句） | job | 看片 |
|---|---|---|---|
| 曹操 | 我是曹操。/ 天下大势，台词也要讲节奏。 | `au_2a8fbab08d5c` | `/media/character_demos/caocao_audition_sub.mp4` |
| 甜妹 | 嗨，我是甜妹！/ 今天的心情，也要甜甜的呀。 | `au_1eb2a2ab33ee` | `/media/character_demos/tianmei_audition_sub.mp4` |
| 学霸 | 我是学霸。/ 这道题，我讲三遍你就能懂。 | `au_3d74f461c93a` | `/media/character_demos/xueba_audition_sub.mp4` |
| 诸葛 | 我是诸葛。/ 这一局，先算清楚再开口。 | `au_7f26827a63f0` | `/media/character_demos/zhuge_audition_sub.mp4` |
| 李白 | 我是李白。/ 一杯酒，一首诗，半座长安城。 | `au_d3c2283f209d` | `/media/character_demos/libai_audition_sub.mp4` |
| 武则天 | 我是武则天。/ 这天下，容得下女子说了算。 | `au_c860c0f78005` | `/media/character_demos/wuzetian_audition_sub.mp4` |
| 关羽 | 我是关羽。/ 义字当头，刀下不留无名之辈。 | `au_31e36f9ff8ed` | `/media/character_demos/guanyu_audition_sub.mp4` |
| 秦始皇 | 朕，秦始皇。/ 天下文字车轨，皆要归一。 | `au_059663500e70` | `/media/character_demos/qinshihuang_audition_sub.mp4` |

- 全部 1024×576、有音轨、字幕烧录可见；批量总耗时约 33 分钟（GPU 串行，单条 2.5–4 分钟）

## 2. 合集

- **看片**：http://127.0.0.1:8090/media/character_demos/character_audition_reel.mp4
- 内容：片头 + 10 个角色（阿栗/木兰/大魏/曹操/甜妹/学霸/诸葛/李白/武则天/关羽）名牌 + 试镜片段，共 **72.7s**，1024×576
- 帧预览：`tmp/v34/reel_sheet.jpg`
- 构建脚本：`tmp/v34/build_reel.py`（PIL 名牌卡 + ffmpeg concat 重编码）

## 3. 证据
- 单条视频与 SRT：`tmp/v34/demos/`（8 条）、`tmp/v33/demos/`（前 3 条）
- 合集：`outputs/character_demos/character_audition_reel.mp4`（/media 200）
- 帧 sheet：`tmp/v34/reel_sheet.jpg`、`tmp/v33/audition_demos_sheet.jpg`
