# 造片局 · AI 微综艺

> 横屏 5–10 分钟 AI 微综艺成片平台（demo）：**选模式 → 写台本 → 管资产 → 出分镜 → 批量生成 → 包装成片**。
> 品牌：造片局 ｜ 品类：AI 微综艺 ｜ 构建时间：2026-10-23 夜

![项目列表](docs/screenshots/01_projects.png)

## 这是什么

一个可运行的全栈 demo：以「AI 圆桌综艺」为第一个节目模式，把一期的台本、资产、52 个分镜、生成任务、时间线与成片串成一条流水线，最终输出 5 分钟横屏成片（示例为 mock 画面 + 真实中文 TTS/字幕/花字/BGM）。

- 前端：ArcReel 式项目制工作台（编剧间 / 资产库 / 分镜台 / 生成台 / 成片房）
- 后端：FastAPI + SQLite，任务后台线程，ComfyUI(MiniMax H3) 适配器 + mock provider
- 流水线：ffmpeg 合成 + edge-tts 中文配音 + ASS 字幕/花字 + 片头片尾 + BGM，CLI 可独立运行

## 快速开始（Windows）

```powershell
# 0) 首次：安装依赖（前端 + ffmpeg 二进制 + Python 依赖）
cd tools; npm install; cd ..
pip install fastapi "uvicorn[standard]" sqlalchemy pydantic pillow edge-tts pytest httpx

# 1) 构建前端
.\scripts\build-frontend.ps1

# 2) 启动（http://127.0.0.1:8090/）
.\scripts\dev.ps1
```

打开 http://127.0.0.1:8090/ → 项目「造片局 · 样片」→ 进入工作台。

已有样例成片（无需启动）：[outputs/demo/final.mp4](outputs/demo/final.mp4)（299s / 1280×720 / 15.7MB）。

## 一键自检

```powershell
.\scripts\verify.ps1
```

包含：pytest、ffmpeg 版本、样例成片参数、API 健康检查。

## 功能一览

| 模块 | 能力 |
|---|---|
| 编剧间 | 按段落展示台本/台词/镜头数/时长，一键生成分镜 |
| 资产库 | 主持/嘉宾/场景卡，声音、人设、锁定状态 |
| 分镜台 | 52 镜头，表格/宫格切换，状态过滤，批量生成，详情抽屉 |
| 生成台 | 任务队列（进度/费用/失败/重试）、多版本预览与选用 |
| 成片房 | 一键渲染、进度、成片播放、费用汇总、包装配置 |
| 设置 | 生成模式（mock/comfyui）、ComfyUI 地址、工作流模板、分辨率、TTS、计费单价 |

![分镜台](docs/screenshots/04_storyboard.png)

## 目录结构

```
backend/      FastAPI 应用（API、SQLite、任务、ComfyUI 适配器）
frontend/     Vite + React + TS 工作台（构建产物 dist 由后端托管）
pipeline/     视频流水线（mock 画面、TTS、ASS、ffmpeg 合成）
contract/     API/CLI 契约、样例剧集（只读）
docs/         方案、架构、决策、演示指南、最终报告、截图
scripts/      启动/构建/渲染/截图/自检脚本
outputs/      样例成片与截图（gitignore）
tools/        ffmpeg/ffprobe 二进制（npm 安装）
```

## 演示内容

《AI 会取代综艺导演吗？》第 001 期：3 位虚构 AI 嘉宾（老周/阿栗/大魏）围绕「AI 与综艺导演」的 5 分钟圆桌，含冷开场、嘉宾介绍、两个话题、结尾反转。全部内容为虚构演示。

## 技术栈

FastAPI · SQLAlchemy · SQLite · Vite · React · TypeScript · FFmpeg 6.1 · edge-tts · ComfyUI（预留 MiniMax H3）

## 已知限制（demo 边界）

- **未实际调用 MiniMax H3**：本机只有 ComfyUI 环境，缺少可调用的 H3 服务地址/凭据；ComfyUI 适配器代码与单测已提供，Settings 切换后即可接真实生成。
- 画面为 **mock 示意片段**（渐变 + 中文标签 + 轻动效），用于验证全链路；不是真实人物/场景视频。
- TTS 使用 edge-tts **在线合成**（需要网络）；失败时静音兜底。
- 转场为硬切，字幕烧录；剪映草稿/OTIO 导出为后续项。
- 长片质感（口型、多人同框、运镜）需接真实 H3/云模型后验证。

## 文档

- 产品/技术方案：[docs/ai-variety-platform-plan.md](docs/ai-variety-platform-plan.md)
- 架构说明：[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- 决策记录：[docs/decisions.md](docs/decisions.md)
- API 契约：[contract/API.md](contract/API.md)
- 流水线 CLI：[contract/PIPELINE_CLI.md](contract/PIPELINE_CLI.md)
- 明早验收指南：[docs/DEMO_GUIDE.md](docs/DEMO_GUIDE.md)
- 最终报告：[docs/FINAL_REPORT.md](docs/FINAL_REPORT.md)
