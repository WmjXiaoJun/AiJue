# 开源依赖与借鉴清单（2026-11 统计）

> 统计方法：`ast` 解析全仓 `*.py`（跳过 `local_models/`、`node_modules/`、`outputs/`、`tmp/`）统计真实 `import`；
> npm 读 `package.json`；模型/权重读代码里的 checkpoint、LoRA 文件名与 `docs/`；参考项目读 `docs/REFERENCES.md`、
> `docs/ai-variety-platform-plan.md`、`docs/FINAL_REPORT.md` 与本地 `local_models/` 目录。
>
> 许可证以**各上游仓库的 LICENSE 文件**为准；本表只记录"我们用了什么、用在哪、怎么用的"，不对许可证作判断。

---

## 0. 三种关系，别混

| 关系 | 含义 | 例子 |
|---|---|---|
| **A. 直接依赖**（装进环境里调用） | 代码里 `import` / npm 依赖 | FastAPI、Pillow、ffmpeg-static、React |
| **B. 直接使用的模型与推理栈** | 权重跑在本机或云端，我们只写调用与编排 | MuseTalk 1.5、H3 节点、SDXL 底模、DeepSeek API |
| **C. 只借鉴设计，不复制代码** | 交互/流程/质检规则的参考 | ArcReel（AGPL）、OpenMontage、Toonflow |

---

## A. Python 直接依赖（按引用文件数排序）

| 包 | 用途 | 引用文件 | 主要落点 |
|---|---|---|---|
| **Pillow** (`PIL`) | 图像合成、抽帧、立绘/底板后处理、字幕卡 | 49 | `pipeline/scene_render.py`、`pipeline/style_v5.py`、`pipeline/templates3d_hq.py`、`scripts/gen_scene_plates_procedural.py` |
| **pytest** | 测试（508 用例） | 37 | `tests/`、`pipeline/tests/` |
| **numpy** | 像素/波形运算、程序化底板 | 21 | `pipeline/style_v5.py`、`pipeline/lipsync.py`、`pipeline/characters_v5.py` |
| **FastAPI** | 后端 API（162 条路由） | 20 | `backend/app/main.py`、`backend/app/routers/*` |
| **OpenCV** (`cv2`) | 视频解码/帧差/口型区域分析 | 4 | `pipeline/lipsync.py`、`pipeline/motion2d.py` |
| **httpx** | ComfyUI/LLM/发布 的外部 HTTP | 12 | `backend/app/{llm,comfyui}.py`、`publish/upload_post.py` |
| **SQLAlchemy** | 项目/集/镜头/发布记录 持久化（SQLite） | 8 | `backend/app/{db,models,jobs}.py` |
| **Pydantic** | 请求/响应模型 | 9 | `backend/app/schemas.py`、各 router |
| **Starlette** | FastAPI 底座（静态托管/StreamingResponse） | 1 | `backend/app/main.py` |
| **jsonschema** | 合同校验（`contract/v5`） | 1 | `pipeline/v5_catalog.py` |
| **pypinyin** | 人名条/拼音注音（包装） | 1 | `pipeline/characters_v5.py` |
| **edge-tts** | 云端 TTS 备用通道 | 2 | `pipeline/tts_engine.py`、`integrations/comfyui/tools/h3_dynamic_probe.py` |
| **pyJianYingDraft** | 导出剪映草稿（后期精修） | 1 | `pipeline/capcut.py` |
| **torch / torchvision** | 口型/翻译推理（只在 lipsync venv 与 localize 里） | 2 | `pipeline/lipsync.py`、`localize/translator.py` |
| **transformers** | 翻译模型（NLLB 等） | 2 | `localize/translator.py`、`pipeline/lipsync.py` |
| **musetalk**（本地仓库） | MuseTalk 1.5 推理包 | 1 | `pipeline/lipsync.py` |

> 没有用到的（避免误解）：LangChain / LlamaIndex / diffusers / rembg / mediapipe / insightface / scenedetect / librosa /
> soundfile / whisper 包 / openai SDK / requests / aiohttp / PyYAML —— 全部**不在**依赖里，是自研或走 CLI/HTTP。

## B. 音视频与工具链

| 组件 | 用途 | 说明 |
|---|---|---|
| **FFmpeg / ffprobe**（`tools/node_modules/ffmpeg-static`、`ffprobe-static`） | 全部转码/拼接/滤镜/响度/抽帧 | 仓库内置二进制，`pipeline/config.py` 按"环境变量 → 仓库内置 → PATH"解析；Windows 上 `pipeline/utils.py::ffmpeg` 统一加 `-hide_banner -nostdin -y` |
| **Vite 5 + TypeScript 5 + React 18** | 前端（无 router、无 UI 库，手写 CSS） | `frontend/package.json`：`react`、`react-dom`、`@vitejs/plugin-react`、`typescript`、`vite`、`@types/*` |
| **ComfyUI 0.42.0**（本机 `127.0.0.1:8188`，torch 2.11.0+cu130） | H3 图生视频/参考生视频的推理宿主 | `integrations/comfyui/{run_h3.py,run_h3_ref.py,workflow_to_api.py}`；只用它的 HTTP API + 工作流 JSON，**不作为框架底座** |

## C. 直接使用的模型 / 权重 / 服务

| 模型或服务 | 角色 | 出处 / 落点 |
|---|---|---|
| **MuseTalk 1.5** | 口型精修（`muse` 阶段） | `local_models/lipsync`、`local_models/lipsync_models`；`pipeline/lipsync.py`；上游 `TMElyralab/MuseTalk` |
| **MuseV / MusePose** | Muse 家族的视频生成（可选，本地或云端） | `pipeline/muse_video.py`（`musev_local` / `musepose_local` / 云代理），上游 `TMElyralab/MuseV`、`TMElyralab/MusePose` |
| **Wav2Lip / SyncNet** | 口型对齐的备选与**评测**（sync 指标） | `pipeline/lipsync.py`（`wav2lip` 可选、`syncnet` 评测）；上游 `Rudrabha/Wav2Lip` |
| **LatentSync** | 精修口型的可选增强（文档规划项，未接线） | `docs/ai-variety-platform-plan.md` 提到 |
| **DWPose** | MusePose 的姿态前置 | `local_models/lipsync_models/dwpose/dw-ll_ucoco_384.pth` |
| **MiniMax H3**（ComfyUI 节点：`MiniMaxH3ImageToVideo` / `MiniMaxH3ReferenceToVideo`） | 逐镜动态视频（T2VA / ref2va / fl2v） | `integrations/comfyui/run_h3*.py`、`pipeline/templates3d_hq.py`、`pipeline/h3_v5.py` |
| **SDXL 系底模**：DreamShaperXL（2D 风格）、Illustrious（retro_cel） | 关键帧/场景底板/立绘出图 | `pipeline/templates3d_hq.py::STYLE_2D_STYLES`；`imagegen/`（ComfyUI 出图） |
| **arcane-style-xl LoRA** | 赛博绘画感 2D 子风格 | 同上（`lora: arcane-style-xl.safetensors`） |
| **Breeze TTS 2** | 本地 TTS（主通道之一） | `local_models/{breeze-tts,breeze-tts-2,breeze-venv}`；`pipeline/tts_breeze.py`；上游 `breezeblue-ai/breeze-tts`（权重 `BreezeBlue/breeze-tts-2`） |
| **edge-tts** | 云端 TTS 备用 | `pipeline/tts_engine.py` |
| **DeepSeek（`deepseek-flash` / `deepseek-chat`，OpenAI 兼容）** | 剧本/台词生成（本仓 `outputs/llm_scripts/*.json` 即其产物） | `backend/app/llm.py`、`backend/data/llm.env`；本次修好了"渲染读不到 LLM 剧本"的路径 bug |
| **ollama（本地翻译）** | 多语种译制（文档设计，回退 NLLB） | `localize/translator.py`、`pipeline/localize.py` |

## D. 只借鉴设计、不复制代码

| 项目 | 借鉴了什么 | 我们的做法 | 备注 |
|---|---|---|---|
| **ArcReel**（`ArcReel/ArcReel`） | 项目制、资产先行、分镜/生成工作区、任务中心、费用追踪、版本回滚、剪映草稿 | 自研 `backend/` + `frontend/`；草稿格式自己实现（`pipeline/capcut.py`） | **AGPL-3.0：不复制代码**（`docs/REFERENCES.md`、`docs/decisions.md` 均记录） |
| **OpenMontage**（`calesthio/OpenMontage`） | pipeline / agent skill 方法论、reviewer/checkpoint 质检规则、provider 抽象、FFmpeg 工具层 | QC 硬门（`pipeline/qc*.py`）、provider 抽象（`pipeline/{h3_comfy,muse_video}.py`）、工具注册表（`pipeline/agents/registry.py`） | 不作底座 |
| **Toonflow** | 小说→剧本→分镜→关键帧→视频→剪辑的全流程组织、画布/工作流形态 | 五步创作流 + 关键帧抽卡（`templates3d_hq._score_candidate`） | 仓库自称 MIT 部分可参考 |
| **VideoLingo / 同类译制** | 字幕翻译、配音、时长拟合思路 | 自研 `localize/`（Ollama 翻译 + edge-tts + 重包装） | 只借鉴流程 |
| **parallax-maker**（`provos/parallax-maker`） | 2D 视差运镜思路 | `pipeline/motion2d.py`（相机模型 + 图层视差），自研实现 | — |
| **openfilm**（`openfilm/openfilm`） | 开源影片工作台的功能分区参考 | UI 信息架构参考 | — |

## E. 与本仓"两条栈"的对应关系

| 栈 | 依赖链 |
|---|---|
| **V5 渲染栈**（21 个 V5 模板） | `render_v5.py` → `scene_render`（Pillow/ffmpeg）+ `motion2d`（自研 2D 相机）+ `tts_engine`/`tts_breeze`（TTS）+ `ass_builder`（字幕/特效字）+ `qc_v5` |
| **HQ 短片栈**（12 个 custom） | `templates3d_hq.py` → ComfyUI(H3) 出镜 + `imagegen`(SDXL) 出关键帧 + `templates3d.assemble` 合成 + MuseTalk 口型（可选） |

## F. 合规与工程注意（现状）

1. **AGPL 项目（ArcReel）只借鉴交互，不复制代码** —— 已在 `docs/decisions.md` 第 8 条与 `docs/REFERENCES.md` 写死。
2. **模型权重的许可证独立于代码**：MuseTalk/MuseV/MusePose/Wav2Lip/底模/LoRA 各自有自己的 license（有的限研究用途），
   商用前需逐个核对上游发布页；本表不对其可用性背书。
3. **FFmpeg 二进制**（`ffmpeg-static`）随仓库分发，注意其 LGPL/GPL 构建选项。
4. **API Key 不入库**：`backend/data/*.env`（DeepSeek、Metaso、Upload-Post）在 `.gitignore` 里；
   DeepSeek 只走服务端 `backend/app/llm.py`，前端不接触 key。

---

*统计脚本：`tmp/oss_inventory.py`（可重跑）。若新增依赖，请同时更新本文件与 `backend/requirements.txt` / `frontend/package.json`。*
