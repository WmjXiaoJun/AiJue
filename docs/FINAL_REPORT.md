# 造片局 · AI 微综艺 — 最终交付报告

> 构建时间：2026-10-23 夜 ～ 2026-10-24 凌晨 ｜ 目标：横屏 5–10 分钟 AI 微综艺成片平台（可运行 demo）｜ 品牌：**造片局**（品类：AI 微综艺）

![项目列表](screenshots/01_projects.png)

## 1. 一句话结论

**可运行的平台 demo 已交付**：浏览器里能完成「项目 → 编剧间 → 资产库 → 分镜台 → 生成台 → 成片房」全流程，后端能跑通「生成 52 个镜头 → 渲染 5 分钟成片」的真实链路；另附一条已渲染好的 299 秒样例成片。MiniMax H3 真实生成适配器已写好但未实际调用（缺你的 ComfyUI 地址/凭据），当前为 mock 画面。

## 2. 今晚交付清单

| 交付物 | 路径 | 状态 |
|---|---|---|
| 可运行平台（前端 + 后端） | `backend/` + `frontend/` | ✅ 已跑通 |
| 成片流水线（mock 画面 + 中文 TTS + 字幕/花字/BGM + ffmpeg 合成） | `pipeline/` | ✅ 已跑通 |
| 样例成片 1（流水线直出，角色版） | `outputs/demo/final.mp4` | ✅ 299s / 1280×720 / 31,687,266 B（约 30.2 MiB） |
| 样例成片 2（平台端到端渲染，角色版） | `outputs/episodes/ep_001/final.mp4` | ✅ 299s / 1280×720 / 27,220,652 B（约 26 MiB） |
| 演示项目数据 | 1 项目 / 1 集 / 5 资产 / 52 镜头 / 41 条台词 | ✅ 已入库 |
| 测试 | backend 45 passed；pipeline 17 passed | ✅ |
| 截图 | `docs/screenshots/`（8 张） | ✅ |
| 文档 | README / 方案 / 架构 / 决策 / API / CLI / 验收指南 | ✅ |

## 3. 怎么启动（3 步）

```powershell
# 1) 构建前端（首次）
.\scripts\build-frontend.ps1

# 2) 启动（保持窗口开着）
.\scripts\dev.ps1

# 3) 浏览器打开
#    http://127.0.0.1:8090/
```

服务当前已在 **http://127.0.0.1:8090/** 运行（如已停止，按上面第 2 步重启）。

## 4. 已验证的结果

### 4.1 后端 / API
- `GET /api/health` → 200 `{"status":"ok","version":"1.0.0","mode":"mock"}`
- 种子数据：1 项目（proj_zaopianju_demo）/ 1 集（ep_001）/ 5 资产 / 52 镜头
- 任务：52 个 shot 任务 + 3 次 render（70.2s 冷启动 / 40.1s 缓存命中 / 36.1s 干净重渲），全部 done；镜头 52/52 ready，进度 100%
- 干净重渲（job_616a901c8f，36.1s）：52/52 片段 SHA1 与生成台 shots 一致，`clips_from_shots=52`；final.mp4 = 299.0s / 1280×720 / 15,406,461 B
- 端到端：`POST /api/episodes/ep_001/render` → job 进度 0→100 → `episode.status=ready` → `/media/episodes/ep_001/final.mp4` 可访问
- pytest：**45 passed**

### 4.2 流水线
- `python -m pipeline.cli render --episode contract/demo_episode.json --workdir outputs/demo --out outputs/demo/final.mp4`
- 结果：**299.0s / 1280×720 / 25fps / H.264 High + AAC 48k stereo / 16,446,462 B / 冷启动 70.8s**
- 52 镜头全生成；41 条 edge-tts 中文旁白（0 失败）；51 条 ASS 事件（旁白字幕 + 人名条 + 章节标）；片头片尾/BGM/花字齐全
- 平台闭环：成片房渲染会优先复用「生成台」产出的 shots/{sid}.mp4（clips_from_shots=52，全量复用 ~39–60s），片段重新生成后旧缓存按指纹自动失效
- pytest：**17 passed**（含「平台片段复用/缓存失效」用例）

### 4.3 前端
- `npm run build` 成功：JS 222KB / CSS 58KB
- 五 tab 全部接真实 API；2s 轮询；深链 SPA fallback 正常；console error 0
- 已验证：批量生成 → 任务完成 → 版本可播放 → 选用；渲染成片 → 成片房播放；镜像头 52/52、100%、已成片
- 数据一致性：episode.shots_done=52、progress=100、render_url=/media/episodes/ep_001/final.mp4（后端懒重算修复后复验）

### 4.4 截图
| 页面 | 图 |
|---|---|
| 项目列表 | ![projects](screenshots/01_projects.png) |
| 编剧间 | ![script](screenshots/02_script.png) |
| 分镜台 | ![storyboard](screenshots/04_storyboard.png) |
| 生成台（任务/版本/播放） | ![generate](screenshots/05_generate.png) |
| 成片房（成片播放/费用） | ![finish](screenshots/06_finish.png) |

## 5. 演示项目

**《AI 会取代综艺导演吗？》第 001 期**（全部虚构）

- 主持人老周 / 嘉宾阿栗（AI 内容创业者）/ 大魏（传统综艺导演）
- 6 个段落：片头 → 冷开场 → 嘉宾介绍 → 话题一（AI 与导演）→ 话题二（现场改台本）→ 结尾反转
- 52 个镜头、41 条台词、成片 4:59

## 6. 架构

```
浏览器 (React 工作台)
   │ /api 轮询
FastAPI (8090) ── SQLite
   ├─ shot 任务 ──> python -m pipeline.cli gen-shot ──> outputs/episodes/{eid}/shots/*.mp4
   └─ render 任务 ─> python -m pipeline.cli render  ──> final.mp4
                     └─ edge-tts / ASS / ffmpeg / ComfyUI(预留)
产物 /media/* ──> <video>
```

- 生成：默认 mock（ffmpeg 生成示意画面）；Settings 可切 `comfyui`，适配器按 API-format 工作流提交/轮询/下载，已单测。
- TTS：edge-tts（中文 3 个音色），需网络；失败静音兜底。
- 渲染：ffmpeg 6.1（libx264/libass），299s 成片冷启动 70.8s / 缓存命中 40–47s（16 核）。

## 7. 能力边界（重要，别误读）

1. **不是真实 H3 生成**：demo 画面是 mock 示意（渐变 + 中文标签 + 轻动效），用于验证流水线；真实人物/场景需接你的 ComfyUI H3。
2. **H3 适配器未实测**：本机没有可调用的 H3 服务地址/凭据；代码、工作流模板、单测已就绪，填 Settings 即可试。
3. TTS 依赖在线 edge-tts；离线环境会静音兜底。
4. 转场为硬切；剪映草稿/OTIO 导出未实现（方案已列）。
5. 这是 **demo 级平台**：单机 SQLite、无线程池持久化、无鉴权/多租户。

## 8. 今晚实际完成 & 未完成

**完成**：平台前后端、五步工作台、任务/版本/费用、流水线 mock 生成 + TTS + 字幕/花字 + 合成、ComfyUI 适配器、样例成片 ×2、测试、截图、文档、自检脚本。

**未完成（明确后续项）**：真实 H3 联调、镜头级成本接 H3 计费、口型/多人同框、剪映草稿导出、OTIO/FCPXML、多集/多租户/权限、Postgres/队列生产化。

## 9. 下一步建议（按优先级）

1. 你把 ComfyUI H3 地址 + 工作流 API JSON 配进「设置」，我跑 1 个镜头真实生成验证；
2. 真实镜头替换 mock 后重渲染 ep_001，检查一致性与口型；
3. 接剪映草稿导出（后期精修动线）；
4. 扩第二个节目模式（如「AI 吐槽大会」）验证模板复用；
5. 生产化：Postgres + 任务队列 + 多租户。

## 10. 今晚协作记录

- Lead：方案/契约/演示剧本/品牌/脚本/集成/QA/验收/文档
- backend-core：FastAPI + SQLite + 任务 + ComfyUI 适配器 + 角色 API（60 单测）
- frontend-ui：Vite+React 五步工作台 + 真实 API 联调 + 构建
- media-render：角色画面/edge-tts/ASS/ffmpeg 渲染（17 单测）
- character-3d：Blender 程序化角色生成器（studio3d）+ 3 角色素材 + 2D 变体
- h3-comfy：ComfyUI MiniMax H3 真实生成接入 + 3 条角色镜头

详见 `docs/decisions.md`。
## 11. 角色工坊（原创动漫人物，2026-10-24 追加）

- **要求**：全部角色为平台自主生成的原创动漫人物；支持 3D/2D 多种风格；可依托 Blender 造人。
- **3D 生成器（studio3d/）**：Blender 5.0 程序化建模 + 卡通渲染（EEVEE + 3 阶色带 + inverted-hull 描边 + 三点光），按 spec 生成大眼/发型/服装/配饰；支持预设 3d_toon、3d_cel、2d_cel、2d_paint、anime、guoman；单角色约 10–20s。
- **演示角色（全部原创）**：
  - 老周（主持人，3D 卡通，暖黄 accent，深色短发+西装）
  - 阿栗（AI 内容创业者，2D 赛璐璐为主风格，双马尾+耳机+卫衣）
  - 大魏（传统综艺导演，3D 赛璐璐，光头+眼镜+黑夹克）
- **产物**：每角色 portrait.png(768²)、cutout/ 透明底、4 表情（neutral/happy/surprised/thinking）、2d_cel 变体（老周）、dawei 的 sheet.png(1600×900) + turntable.mp4；meta.json。
- **平台集成**：后端 Character 表 + 6 个端点；前端「角色工坊」（角色卡/详情抽屉/新建角色弹窗/表情/转面/重新生成）；分镜台按 speaker 显示角色头像；成片房展示角色画风。
- **截帧**：![角色工坊](screenshots/09_character_workshop.png)

## 12. 真实 H3 生成（MiniMax H3 · 本机 ComfyUI）

- 环境：本机 ComfyUI + int8 量化 MiniMax H3（20.97GB）+ 4step turbo LoRA + Qwen3-VL 文本编码器 + 视频/音频 VAE；RTX 3080 Ti 12GB；ComfyUI 0.39.0 / torch 2.11+cu130。
- 接入：integrations/comfyui/{workflow_to_api.py, run_h3.py, start_comfyui.ps1}，UI workflow → API prompt，提交/轮询/取回/ffprobe。
- **实测成果**：`outputs/h3/laozhou_h3.mp4` —— 5.167s / 768×768 / 24fps / H.264+AAC（含音轨），4step/124帧，单条约 115–150s（含模型 offload），把 Blender 立绘升级为干净的赛璐璐动漫角色。
- 抽帧：![H3 镜头](screenshots/11_h3_laozhou.png)
- 成果：laozhou/ali/dawei 三条全部完成（各 768×768/5.167s/124 帧/含音轨），产物 outputs/h3/{id}_h3.mp4 + 中段帧 outputs/characters/{id}/h3_portrait.png；单条单独跑 115–150s（含 offload），12GB 显存峰值 11.9GB，ComfyUI 单 worker 串行；成片 52/52 镜头均已使用角色画面。
- 字幕/画面修复：去除片段 fade（blackdetect=0）、角色胸像构图、名字条逐镜头绑定、字幕去空格/强制单行/均衡换行（WrapStyle=2），200s 长句已修。

## 13. 角色化成片

- 成片渲染优先级：H3 视频/中段帧 → Blender cutout → 2d → 文字卡兜底；角色按 speaker 逐镜头绑定名字条与表情。
- 修复：切点单帧黑帧（去除片段 fade）、胸像构图、名字条错位。
- 最终成片：`outputs/demo/final.mp4`（299s / 1280×720 / 31.9MB，全部原创动漫角色；三人 H3 真实生成镜头均已接入）；平台端 ep_001 final.mp4 27.4MB 同步角色化。

## 14. 测试与构建（终态）

- backend：`python -m pytest tests -q` → **60 passed**
- pipeline：`python -m pytest pipeline/tests -q` → **25 passed**（含字幕去空格/换行/角色素材/缓存指纹）
- frontend：`npm run build` 通过（dist 240KB JS / 71KB CSS）
- 角色/H3 资产与截图均已入库文档。
