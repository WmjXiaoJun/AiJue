# 造片局 · 架构说明（demo 版）

## 进程与端口
- 后端：FastAPI（uvicorn），127.0.0.1:8090。负责 API、SQLite、任务线程、静态托管（/media、前端 dist）。
- 前端：Vite + React + TS，构建产物 frontend/dist，由后端同源托管；开发时可 5173 热更新。
- 流水线：pipeline/ 独立 Python 包，CLI 由后端以子进程方式调用；也可单独运行。
- 生成：默认 mock（ffmpeg 生成示意片段）；ComfyUI(MiniMax H3) 适配器预留，Settings 切换。

## 数据流
浏览器 → /api → FastAPI → SQLite
                    ├─ shot 任务 → python -m pipeline.cli gen-shot → outputs/episodes/{eid}/shots/*.mp4
                    └─ render 任务 → python -m pipeline.cli render → outputs/episodes/{eid}/final.mp4
产物 → /media/* → 浏览器 <video>

## 关键目录
backend/app/     FastAPI 应用（main/db/models/schemas/seed/jobs/comfyui）
frontend/src/    React 工作台（项目列表 + 五 tab）
pipeline/        ffmpeg/mock/tts/render
contract/        API 与 CLI 契约、样例剧集（只读）
outputs/         生成产物（gitignore）
docs/            方案、决策、架构、最终报告
scripts/         启动/构建/渲染/截图脚本

## Demo 限制
- 未实际调用 MiniMax H3（本机只有 ComfyUI 环境，缺少可调用的 H3 服务地址/凭据）；ComfyUI 适配器代码与单测已提供。
- 画面为 mock 示意片段（渐变色块 + 中文标签），用于验证「剧本→分镜→生成→时间线→成片」全链路；替换 provider 后即为真实生成。
- TTS 使用 edge-tts 在线合成（已验证），需要网络。
## 角色工坊 & 真实 H3（2026-10-24 追加）
- studio3d/：Blender 5.0 程序化原创动漫角色生成器（3d_toon/3d_cel/2d_cel/2d_paint 预设），CLI create 输出 portrait/cutout/sheet/expressions/turntable/meta。
- outputs/characters/{id}/：角色产物（portrait.png、cutout/、expressions/、style_*）。
- integrations/comfyui/：本机 ComfyUI(MiniMax H3 int8 + 4step turbo LoRA) 真实生成接入；workflow_to_api.py（UI workflow→API prompt）、run_h3.py（提交/轮询/取回/ffprobe）；输出 outputs/h3/。
- 渲染层：pipeline 读取角色素材（H3 视频/中段帧优先 → Blender cutout → 2d → 文字卡兜底），成片全部使用原创动漫角色。
- 后端：Character 表 + /api/characters* 端点 + type=character 任务；portrait 存在即 ready；Studio3D CLI 由任务线程调用。
- 前端：资产库「角色工坊」（角色卡/详情/新建/表情层）、分镜台角色头像、成片房角色画风。
