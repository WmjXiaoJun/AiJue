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
## 15. 角色 2.0：文生概念图（SDXL 动漫）

- 模型：**Animagine XL 4.0**（SDXL 动漫，6.94GB，sha256 1d5b43ff…3916 校验通过），ComfyUI 原生 txt2img（28 step / cfg 5.5 / euler_a / 832×1216），12GB 显存可跑，单图约 14–20s。
- CLI：`python -m imagegen.cli generate --spec contract/characters/laozhou.json --outdir outputs/concepts/laozhou --mode all --style anime --seed 42`；portrait / three_view×3 / 4 表情 / scene / sheet / meta。
- 实测：3 角色共 **30 张概念图**（laozhou/ali/dawei），画质达到可用的日漫赛璐璐立绘；设定图 1600×900 含三视图+四表情+配色条。
- 后端：`POST /api/characters/{cid}/concept/generate`（Job type=concept）、`GET /concepts` 懒扫描、`POST /portrait/from-concept` 设为立绘。
- 前端：角色抽屉「AI 概念图」区（风格/提示词/seed/生成进度/图集/设为立绘/H3 动画），角色卡概念图优先并带「AI 概念图」徽标。
- 截图：![概念设定图](screenshots/16_concept_sheet_laozhou.png)

## 16. 概念图 → H3 动画（闭环）

- `POST /api/characters/{cid}/h3-animate` → Job(type=h3) → 调 `integrations/comfyui/run_h3.py`（768×768/4step/124 帧/含音轨）→ outputs/h3/{cid}_h3.mp4 + h3_portrait 回填；ComfyUI 串行锁，超时 20 分钟。
- 已有 3 条 H3 角色镜头 + 本次可用概念图重新生成；前端角色抽屉可直接播放。
- 截图：![H3 角色镜头](screenshots/11_h3_laozhou.png)

## 17. 成片导出：剪映草稿

- `pipeline/capcut.py`（pyJianYingDraft 0.3.1）：从 manifest 生成剪映专业版草稿 + SRT + 审计；`POST /api/episodes/{eid}/export {"type":"capcut"}` 后台任务。
- 实测：`outputs/exports/造片局_EP001/` —— 299.000s / 1280×720 / 25fps；**52 视频段 + 1 旁白音频 + 42 字幕文本**；draft_content.json(243KB)+meta+srt+README+audit ok；含 materials/ 自包含媒体。
- **已知限制**：剪映 10.x 会把草稿 JSON 加密保存，不识别明文草稿（已实测 10.3）；明文草稿适用于 ≤5.9 或按 README 用 materials/ + subtitles.srt 重建；结构本身经 pyJianYingDraft 社区标准校验。
- 测试：pipeline **34 passed**；backend **75 passed**。
## 18. 角色 2.0 最终验收（2026-10-24 凌晨）

### 18.1 文生概念图（SDXL 动漫）
- 模型：Animagine XL 4.0（6,938,434,056 B / sha256 1d5b43ff…3916 校验通过）
- 35 张产物：3 角色 × {portrait 832×1216, three_view×3 1024², expressions×4, scene, sheet 1600×900} + 3 meta；单图 8–20s（首图含加载 14.2s），--mode all 单角色 205–222s
- CLI：`python -m imagegen.cli generate --spec contract/characters/<id>.json --outdir outputs/concepts/<id> --mode all --style anime --seed 42`
- 后端：concept/generate + concepts + portrait/from-concept 真链路均跑通（job done，产物 URL /media/concepts/…）
- 前端：角色抽屉 AI 概念图区（风格/提示词/seed/进度/图集/设为立绘/H3 动画）实测通过
- 截图：![概念图图集](screenshots/20_concept_gallery.png) ![生成中](screenshots/19_concept_generating.png) ![设为立绘](screenshots/21_set_portrait.png)

### 18.2 概念图 → H3 动画
- `POST /api/characters/{cid}/h3-animate` → Job(type=h3) → run_h3.py → outputs/h3/{cid}_h3.mp4（ComfyUI 串行，20 分钟超时）
- 真链路验收：`POST /characters/laozhou/h3-animate` → job **done 121.5s** → outputs/h3/laozhou_h3.mp4（768×768/24fps/h264+aac/5.167s/1,574,453 B，含音轨）；h3 中段帧回填 h3_portrait；Character 回填 h3_video_url/h3_portrait_url；/media 200。
- 前端角色抽屉可播放 H3 视频；截图：![H3 动画](screenshots/22_h3_animation.png)

### 18.3 剪映草稿导出
- `POST /api/episodes/{eid}/export {"type":"capcut"}` → pipeline.capcut.py（pyJianYingDraft 0.3.1）
- 产物 outputs/exports/造片局_EP001：draft_content.json（canvas 1280×720/25fps/299s；52 视频段 + 1 旁白 + 42 字幕）、draft_meta_info.json、materials/、subtitles.srt、README.txt、audit ok
- 限制：本机剪映 10.3 加密草稿 JSON，不识别明文草稿；明文格式适用于 ≤5.9 或按 README 用 materials/+srt 重建
- 截图：![导出成功](screenshots/23_export_success.png)

### 18.4 终态与测试
- backend pytest：**75 passed**；pipeline pytest：**34 passed**；前端 build 通过（dist 254KB JS/78KB CSS）
- 服务：http://127.0.0.1:8090（backend/run-detached.ps1 可独立常驻）；ComfyUI 8188 在线
- 已知：三视图/表情为独立生成（seed 家族相似，非像素级同一脸）；ComfyUI 单 worker 串行；剪映 10.x 加密限制；H3 单条约 2–5 分钟。
## 19. 角色 2.1：IP-Adapter 一致性 + 概念图驱动成片（2026-10-24 早）

### 19.1 IP-Adapter 一致性
- 安装 ComfyUI_IPAdapter_plus（cubiq）；模型：ip-adapter-plus_sdxl_vit-h（847,517,512 B，sha256 3f5062b8…e6581 ✓）+ CLIP-ViT-H-14（3,944,552,236 B，sha256 036e6e2b…b18d ✓）。
- imagegen 新增 --ref / --ip-adapter-strength（默认 0.65）/ --ip-adapter-end-at（0.85）；工作流 SDXL + IPAdapterUnifiedLoader + IPAdapterAdvanced（weight_type=linear）。
- 实测：3 角色 × 7 张（三视图 3 + 表情 4）= 21 张 consistent 图，1024×1024，单图 11–13s（模型驻留后），seed 42，无 OOM。
- 三视图采用低 weight（0.35/end 0.90）+ 背景负向词重跑，最终为**白底/单人全身**、发色服装与 portrait 一致；表情保留 weight 0.65 版本。
- 效果：脸型/发色/服装一致；IP-Adapter 仍可能带入立绘的光效风格（表情图上更明显），属已知取舍。
- 截图：![一致性图集](screenshots/25_consistent_gallery.png)

### 19.2 概念图驱动 H3（覆盖旧版本）
- laozhou/ali/dawei 三条 H3 全部用 outputs/concepts/{id}/portrait.png 驱动重生成：768×768 / 5.167s / 124 帧 / 含音轨；耗时 116–223s/条；中段帧回填 h3_portrait。
- 旧版保留 {id}_h3_prev.mp4；Character.h3_video_url 指向新文件。

### 19.3 成片升级（SDXL 概念角色）
- pipeline 素材优先级按 CONSISTENCY.md 落地：H3 视频 → h3 中段帧 → concepts/{id}/portrait.png（832×1216 竖构图裁胸像 + 右侧画窗 + Ken Burns）→ Blender cutout → 2D → 文字卡。
- outputs/demo/final.mp4：299.000s / 1280×720 / 25fps / 34,719,840 B（33.1MiB）/ blackdetect 0 条 / 52/52 角色画面 / H3 3/3 概念图驱动；冷渲 50.9s。
- 平台 outputs/episodes/ep_001/final.mp4：299.000s / 27,288,915 B / mtime 09:14:55（同一优先级重渲，/media 200）。
- 修复：角色镜头重复人名条（ASS 不再输出 Lower，51→49 事件）、字幕基线恢复 655。
- 截图：![成片角色抽帧](screenshots/28_final_characters_sheet.png) ![角色卡](screenshots/26_character_cards_sdxl.png) ![H3 概念驱动](screenshots/27_h3_concept.png)

### 19.4 测试与状态
- pipeline：38 passed（新增概念素材优先级/竖构图裁切/概念表情/指纹/人名条去重）
- backend：**76 passed**（新增 consistent 懒扫描 + from-concept 简写路径）；pipeline 38 passed；frontend build 通过（dist 257KB JS/78KB CSS）
- 服务：8090 常驻（run-detached）；ComfyUI 8188 在线（IP-Adapter 节点已加载）
- 已知：三视图风格受 IP-Adapter 背景影响；H3 串行 2–5 分钟/条；剪映 10.x 加密草稿限制仍在。
## 20. H3 双 Provider：本地 ComfyUI（生产）+ MetaSo 云端（测试）

- 背景：本机 H3 单条 5s 镜头约 116–223s；为快速测试/迭代，接入用户提供的 MetaSo 云端 MiniMax H3 API（同模型同规格）。
- 实测（2026-10-24）：
  - t2v 5s/768P 16:9：16.2s 完成；
  - i2v 5s/768P（概念图驱动）：上传<1s + 创建 1–3s + 生成 ~21s + 下载 <1s，合计 23–25s/条；
  - 3 角色：laozhou 24.6s / ali 23.5s / dawei 24.3s；输出 768×1120（跟随立绘比例）/ 5.175s / h264+aac；
  - 价格 0.09 元/秒（5s≈0.45 元），2K 0.15 元/秒。
- 集成：integrations/metaso/h3.py（CLI）+ README；协议 /v1/files/upload → mm_file://、/v2/video_generation、/v2/query/video_generation/{id}；Key 存本地 backend/data/metaso.env（gitignore），仓库仅 .env.example。
- 策略：**默认 provider=local**（生产成片仍本机生成）；metaso 仅测试用，产物单独 outputs/h3/metaso/ 或 {id}_h3_metaso.mp4，不覆盖本地 H3；Settings 可切换 provider。
- 对比图：![本地 vs MetaSo](screenshots/29_metaso_compare.png)

### 20.1 测试产物
- outputs/h3/metaso/{laozhou,ali,dawei}_metaso.mp4（各 5.175s/768×1120/含音轨）与 .meta.json（task_id/timeline/预估费用/ffprobe）
- outputs/h3/metaso/compare_local_vs_metaso.png（3 角色本地 vs 云端抽帧对比）
### 20.2 平台集成与端到端验收（2026-10-24）
- Settings 新增：h3_provider（local 默认 | metaso）、metaso_base_url、metaso_resolution（768P/2K）、metaso_duration（4-15）、metaso_key_configured（只读布尔，明文不回传）；PATCH 可写入/清除 key 到 backend/data/metaso.env。
- 真实端到端：
  - MetaSo：PATCH provider=metaso → POST /characters/laozhou/h3-animate → job_3f1c019e9d **done 24.1s** → outputs/h3/laozhou_h3_metaso.mp4（4,584,847B / 768×1120 / 5.175s / h264+aac），h3_metaso_url 回填，/media 200；本地 laozhou_h3.mp4 前后 sha1 一致（未被覆盖）。
  - local：job_bc80c07273 done **120.1s** → h3_video_url/h3_portrait_url 回填；与本机 ComfyUI 行为一致。
- 前端：设置弹窗「H3 生成方式」双选项（本地·生产 ↔ MetaSo 云端·测试）、Key 已配置状态、¥0.09/秒 · 5s≈¥0.45 提示；角色抽屉按钮与播放器显示 provider 徽标（云端测试·MetaSo）。实测 UI 切 provider → 云端 H3 job 24s done → 抽屉播放 ali_h3_metaso.mp4；随后恢复 provider=local。
- 安全：key 只在 backend/data/metaso.env / 环境变量；GET /settings 不含明文；仓库仅 .env.example。
- 测试：backend **83 passed**（新增 h3 provider 用例）、pipeline 38 passed。
- 默认策略：生产成片仍 local；metaso 仅测试/快速迭代，产物独立存放。
## 21. ComfyUI 本地升级（2026-10-24）
- 0.39.0 → 0.42.0（116 commits），前端包 1.57.1、模板 0.11.96；自定义节点全部更新（KJNodes/LTXVideo/VHS/rgthree），LTXVideo 依赖补齐；模型全部在位。
- 冒烟：SDXL 概念图 14s；IP-Adapter 一致性 3 视图 38.4s；本地 H3 i2v 119s（768×768/5.167s/含音轨）；1,442 节点可用；8090/8188 均 200。
- 提示：KJNodes triton 可选缺失（不影响）；回滚 tag pre-update-20261024（核心+4 节点）。
- 详见 docs/COMFYUI_UPGRADE_2026-10-24.md。
## 22. 台本驱动闭环（产品化，2026-10-24 晚）
- 背景：此前分镜是格式模板骨架（lines 为空），用户台本进不来；本轮补齐「写台本 → 生成分镜 → 渲染成片」自服务链路。
- 后端：Episode.script（含 SQLite 迁移）；POST /projects/{pid}/episodes 新建剧集；PATCH /episodes 保存台本；POST /storyboard/generate {force} 从台本解析分镜（对白/旁白/括号动作 reaction/未知说话人回退/自动片头片尾/时长 clamp）；POST /episodes/{eid}/shots 追加、DELETE /shots/{sid} 删除重排；render 改为序列化实际集（不再硬编码 demo JSON）。
- 台本格式契约：contract/SCRIPT_FORMAT.md。
- E2E：demo 项目第 2 集 ep_e1015494fa（title「AI 做的综艺，观众看得出来吗？」，Lead 定稿 434 字台本）→ 21 分镜（title 1 / host 6 / reverse 11 / reaction 2 / end 1），storyboard 总时长 75.95s → 渲染 job_3591291c37 done 24s。
  - 成片：outputs/episodes/ep_e1015494fa/final.mp4，10,406,670 B，75.95s / 1280×720 / 25fps / 1898 帧 / h264+aac；blackdetect 0；17/17 台词 TTS 音色与资产一致；21/21 字幕与分镜一致；21/21 镜头角色覆盖。
  - 抽帧：outputs/episodes/ep_e1015494fa/frames_verify/（laozhou/ali/dawei/characters_sheet/title/end + verify.json）。
- 前端：编剧间台本编辑（读写/保存/示例/从台本生成分镜）、新建剧集、分镜添加/删除/编辑；截图 docs/screenshots/32_script_editor.png、33_script_storyboard.png。
- 测试：backend 96 passed（新增 tests/test_script.py）；pipeline 40 passed（新增台本 type 映射回归）。
### 22.1 前端与全链路截图
- 台本编辑+保存：docs/screenshots/32_script_editor.png
- 从台本生成分镜（20 镜 / 63.7s，分段摘要）：docs/screenshots/33_script_storyboard.png
- 分镜增删（二次确认+镜号重排）：docs/screenshots/34_shot_edit.png
- 新建剧集：docs/screenshots/35_create_episode.png
- 说明：新建剧集默认继承项目最新一集的角色资产（backend 支持 copy_assets_from/no_copy_assets），保证新集可直接渲染。
### 22.2 新建剧集资产继承与 ep_003 全链路
- POST /projects/{pid}/episodes 默认从项目最新一集继承角色资产（copy_assets_from/no_copy_assets 可选；跨项目 400）；台本解析的 speaker 绑定到新集自己的资产（不再指向源集）。
- ep_003（ep_e6a182d7d1「AI 剪辑师会抢活吗？」）：补 3 资产 + 台本 force 重建 20 镜/63.7s → 渲染 job_e94dc161bb done 28s → outputs/episodes/ep_e6a182d7d1/final.mp4（8,701,439 B）63.7s / 1280×720 / 25fps / 1592 帧 / blackdetect 0 / 16/16 TTS 音色 / 20/20 字幕 / 20/20 角色画面。
- 三集产物：ep_001 299s / ep_002 75.95s / ep_003 63.7s，均 API 渲染 + 自动验收。
- 测试：backend 101 passed（新增资产继承用例）；pipeline 40 passed。
## 23. 多语种本地化（出海）v1（2026-10-24 晚）
- 目标：同一成片一键产出多语配音+字幕版本（不改画面），优先 en/es，支持 ja/ko/pt；契约 contract/LOCALIZATION.md。
- 翻译：本机 Ollama **qwen2.5:7b**（默认 provider=ollama；回退 NLLB-200-600M；mock 可测）；人名罗马化 Zhou/Ali/Dawei，品牌固定 Zaopianju，术语表约束；19 条对白约 5.5s，66 条约 12s。
- 配音：edge-tts 多语音色（en-Guy/Aria/Jenny、es-Alvaro/Elvira/Dalia、ja/ko/pt 同表），每镜 atempo 0.85–1.25 拟合原镜头时长，混音 loudnorm 与中文版一致。
- 字幕/包装：目标语 ASS（字体/行宽按语言）、srt 软字幕、片头/片尾/名字条/段落标签本地化；发布元数据 title/description/hashtags。
- 后端：Localization 表 + POST /episodes/{eid}/localize + GET localizations、Job(type=localize)、Settings localize_langs/localize_translator/localize_voices（含 --voices-json 传参）；110 passed。
- pipeline：pipeline/localize.py（本地化 episode manifest 重生成 per-lang clips + 多语 TTS + 目标语 ASS/SRT + 合成 + meta），pipeline tests **52 passed**。
- 前端：成片房「多语种出海版本」面板（EN/ES/JA/KO/PT 多选、进度、播放、下载 mp4/srt、发布元数据复制、失败重试）；截图 37/38。
- 实测（最终版）：ep_002 EN 75.95s/10.5MB、ES 75.95s/10.7MB；ep_001 EN 299.00s/34.3MB（41 条 TTS、44 条字幕、blackdetect 0）；画面包装全目标语、无中文残留；翻译 19 条≈5.5s、66 条≈12s、120 条约 22s。
  - 包装本地化已修复并复验：左上品牌 ZAOPIANJU · AI MICRO-VARIETY、右上镜头标签、名字条、片头片尾全部目标语，画面无中文残留；复验 3 个版本时长/blackdetect 0/字幕数 21/21/44 全过。
  - 截图：37_multilang_generating.png、38_multilang_en_play.png、39_ep001_en_title.png、40_ep001_en_mid.png、41_ep002_en_mid.png。
## 24. 成片 3.0：全动态镜头（口型 / 首尾帧 / 转场，2026-10-24 深夜）

### 24.1 用户反馈与根因
- 反馈：成片里只有一个人有嘴形和动作，其它角色都是静的；还要求注意首尾帧、镜头与转镜。
- 根因：旧版 ep_001 全片只用了 3 条 H3 镜头（每角色 1 条），其余 49 镜是静帧 + Ken Burns；且早期 H3 默认 prompt 写的是「42 岁男主持人」，会把非主持人角色也生成成男主持（已修）。

### 24.2 技术方案（本轮实现）
- 逐镜动态化：pipeline/dynamic.py 为每个镜头生成动态 clip；静帧只保留片头/片尾/花字卡。
- 说话镜头：H3 05_ref2va（ref_image=该镜静态首帧 + ref_audio=该镜 TTS wav）→ 口型跟随 TTS；输出静音后改用原 TTS 音轨。
- 反应/动作镜头：H3 04_i2v_fl2v（first_frame=该镜首帧，last_frame=下一镜首帧，match cut 连续）→ 首尾帧可控动作。
- 转场：xfade（18 个预设，默认 fade 0.3s）+ whoosh 音效轨；不再全硬切静帧。
- 每镜 TTS 单独导出 audio/shot_{sid}.wav；dynamic cache（shot/首尾帧/音频/CLI 版本 hash）；失败可回退 static。
- H3 动态 CLI：integrations/comfyui/run_h3_ref.py（audio_ref / fl2v，中性 prompt 保持参考图角色身份）；ComfyUI 工作流 04/05 直接复用。
- 参考库对照见 docs/REFERENCES.md（ArcReel 交互/资产先行、OpenMontage pipeline/质检、Toonflow 工作流形态；H3 节点直接用）。

### 24.3 验收（ep_002，21 镜）
- outputs/episodes/ep_e1015494fa/final_dynamic.mp4：75.95s / 1280×720 / 25fps / 11,752,877 B / blackdetect 0。
- 动态覆盖：17 个 audio_ref（音驱动口型）+ 2 个 fl2v（首尾帧）+ 2 个片头片尾卡；19/19 非卡片镜头动态，无 static-fallback。
- 3 角色身份正确（老周/阿栗/大魏抽帧核对），字幕与台词对齐，名字条/品牌/镜头标签由 ASS 补画；xfade 转场 20 处 + whoosh 生效。
- 测试：pipeline 58 passed；backend 116 passed；frontend build 通过。
- 抽帧：docs/screenshots/43_dynamic_ep002_sheet.png（t=2/10/14/22/40/65s）。

### 24.4 ep_001（299s 主片）
- 已后台启动全片动态渲染：52 镜中 44 个动态镜头（41 台词 + 反应/动作），输出 outputs/episodes/ep_001/final_dynamic.mp4；H3 单镜约 2–3.5 分钟，预计 2.5–3 小时完成（本轮结束时仍在渲染）。
- 完成后按同标准验收：299s / blackdetect 0 / 44 动态覆盖 / 3 角色口型 / 转场 / 首尾帧连续。

### 24.5 已知限制
- H3 ref2va 输出为全画幅角色（与静态版的右侧演播厅画窗构图不同），已用 ASS 补品牌/名字条/状态；如需保持原画窗构图需二次合成或改 H3 参考图策略。
- 目前每镜用同一中性 prompt，尚未按景别/情绪做运镜差异化（下一步可按 shot type 注入景别/机位/prompt）。
- 口型由 TTS 音轨驱动，仍可能有轻微不同步；如需更精细可加 LatentSync/Wav2Lip 二次对齐。
## 25. 一键发布（海外平台）v1（2026-10-25）
- 参考 MoneyPrinterTurbo 的 Upload-Post 集成方式，实现「成片后一键发布到 YouTube / TikTok / Instagram」。
- 新增 publish/：upload_post.py（/api/upload 表单、YouTube 额外字段、/api/uploadposts/status 轮询、失败不自动重试）+ cli.py（--video/--title/--description/--hashtags/--platforms/--username/--privacy/--dry-run；key 仅环境变量 UPLOAD_POST_API_KEY）。
- 后端：PublishRecord 表；GET /api/publish/config；PATCH /api/settings upload_post_*（api_key 只写 publish.env、GET 只返回是否配置）；POST /api/episodes/{eid}/publish（lang/version 选片；元数据优先 localized/{lang}/meta.json.publish，否则品牌默认「造片局 · AI微综艺」+#造片局 #AI微综艺 #Zaopianju #AIVariety #AIGenerated）；GET publishes 列表/详情；Job type=publish。
- 安全：key 不进 cmd/job JSON/日志；同参数活跃任务复用防重复；失败不自动重试；dry-run 做时长（TikTok≤10min）、大小、配置、横屏预检并返回 warnings/errors。
- 实测：ep_002 英文版 dry-run job done「发布预检完成」；未配置正式发布正确 400；测试 backend **130 passed**。真实上传待用户提供 Upload-Post 凭据后验证。
- 文档：docs/PUBLISHING.md（注册 upload-post.com → 连接三平台 → 取 Key/username → Settings 填写 → 预检/发布 → 限制/合规/排查）。

## 26. 综艺模板库（国内外参考 + 历史综艺，2026-10-25）
- 新增 15 个格式模板（contract/formats/*.json，均为 16:9、300–360s、受支持 shot pattern）：
  - 国外：tonight_show（美式深夜秀）/ snl_sketch（SNL式小品）/ taskmaster（任务大师）/ traitors（叛徒圆桌）/ shark_tank（创智赢家）/ masked_singer（蒙面唱将）/ hot_ones（辣访谈）
  - 国内：qipashuo（奇葩辩论）/ standup（脱口秀大会）/ slow_life（慢生活）/ mystery（推理现场）/ national_treasure（国宝守护）/ classics（典籍对话）
  - 历史（用户点名）：history_talk（历史夜话：主持+历史人物 AI 分身圆桌，348s）/ history_mystery（历史悬案：情景再现+线索推演+投票，359s）
- 参考文档 docs/VARIETY_REFERENCES.md：15 个模板逐条给参考节目/国家平台/模式核心/可借鉴点/我们的差异/来源链接，附「历史综艺为什么适合 AI 微综艺」论证。
- 前端模板选择器已同步 16 个模板（含原 roundtable_5min）；实测 POST /projects {format_id:history_talk_5min} + storyboard/generate → 6 个骨架镜头/348s；示例项目「造片局 · 历史夜话（示例）」已建。
### 24.4 ep_001 全动态成片（已交付，2026-10-25）
- outputs/episodes/ep_001/final_dynamic.mp4：**299.0s / 1280×720 / 25fps / 7475 帧 / 43,143,749 B / blackdetect 0**；
- 动态覆盖 **44/52**（41 个 audio_ref 音驱动口型 + 3 个 fl2v 首尾帧，8 个静态卡：片头/片尾/6 花字卡），0 static-fallback；44/44 动态 clip 帧差与嘴部区域帧差明显；
- xfade fade 0.30s × 51 转场 + 全片 whoosh；3 角色身份正确（老周/阿栗/大魏）；H3 生成耗时 111m35s；
- 抽帧：outputs/dynamic_frames/ep001_v2_characters_sheet.png、ep001_v2_fl2v_matchcut_sheet.png、ep001_v2_transition_sheet.png；verify_dynamic.json；
- 说明：平台静态 shots 仍是旧 chibi 素材，动态版用概念角色重建参考图；静态版如需更新需另跑。

## 27. OpenMontage 镜头级参考与 16 个模板验证片（2026-10-25）
- 参考仓库：calesthio/OpenMontage（12 条 pipeline、100+ tools、700+ agent skill/制作知识）；重点读 video-gen-prompting（镜头/机位/运镜/光线/身份锚定/5-aspect 公式）、video-stitching（转场决策树/时长/音频/质检）、cinematic（画幅/镜头时长/音频分层/反主观形容词）、broll-planning、edit-director、pipeline_defs。详见 docs/OPENMONTAGE_NOTES.md。
- 落地：contract/SHOT_GRAMMAR.md（镜头级字段与规范）、pipeline/shot_grammar.py（TYPE_PLANS / TRANSITION_RULES / 景别机位运镜标签）、scripts/render_template_demos.py（模板 → 镜头级验证片）、scripts/make_template_showcase.py（汇总巡礼）。
- 16 个模板验证片：outputs/template_demos/{format_id}/{format_id}.mp4，每支 27–37s，含 TTS/字幕/名字条/xfade/景别·机位·运镜标签（top-right）；
- 模板巡礼：outputs/template_demos/template_showcase.mp4（66.2s，含每模板 4s 精选 + 标题）；contact sheet：docs/screenshots/48_templates_contact_sheet.png；
- 验证结果：16/16 渲染成功（时长/角色/字幕/xfade 抽查通过），manifest：outputs/template_demos/manifest.json。
- 后续（继续大批参考 OpenMontage）：ShotPlan 注入 H3 ref2va/fl2v prompt；按镜头关系选择转场（同场景 hard cut / 话题切换 crossfade / 段落 fade black）；reviewer/checkpoint 质检门；音频分层（ambience/Foley）；字幕同步/口型精修 skills。
## 28. 质检门（OpenMontage reviewer/checkpoint 落地，2026-10-25）
- 新增 pipeline/qc.py：对成片自动跑质检并输出 JSON 报告：时长（vs 分镜总长/目标）、分辨率/帧率/编码、音频轨、blackdetect、字幕覆盖率、镜头数/片头片尾卡、说话人集合、manifest（dynamic/transition）。
- 参考 OpenMontage 的 reviewer/checkpoint 与 edit-director Quality Gate 思路：先机械门禁（技术规格/黑帧/覆盖），再人审（情绪/节奏）。
- 实测：ep_001 动态版、ep_002 动态版 QC ok=true（299s/75.95s，blackdetect 0，字幕/说话人/镜头数全过）；16 个模板验证片 QC 16/16 ok=true，报告落在各模板目录 qc_report.json。
- 用法：python -m pipeline.qc --video <mp4> --episode-json <episode.json> --out <report.json>；可直接接入成片房渲染后的自动检查。
## 29. H3 镜头语言可控性实测（OpenMontage ShotPlan → H3，2026-10-25）
- 同一角色/音频/seed 下测 4 组 ShotPlan，证据 outputs/h3/shotplan_tests/（4 条 768×768×5.17s + compare_shotplan.png + compare_metrics.json + CONCLUSION.md）。

| 组 | ShotPlan | 实测效果 | 量化（肤色占比 t0.5→2.58→4.72s） |
|---|---|---|---|
| A | medium / eye-level / static | ✅ 中景半身，构图稳定 | 15.3%→15.7%→15.9%（+0.6pp） |
| B | close-up / low-angle / slow dolly-in | ✅ 特写 + 明显推近（低角度弱） | 17.5%→23.3%→30.3%（+12.8pp） |
| C | wide / high-angle / slow push-in | ✅ 广角全身+影棚环境再推到中景（高角度弱） | 7.5%→8.1%→10.6% |
| D | extreme close-up / static | ✅ 大特写充满画面 | 41.7%→41.4%→41.9%（稳定） |

- 可控：景别（medium/close-up/大特写/**wide 可突破参考图半身构图，H3 自补全身+影棚环境**）、推近 dolly-in/push-in、static 锁构图、身份/画风稳定、口型开合+眨眼。
- 弱控/不可控：low/high angle 几乎无效（需换俯/仰拍首帧）；横移/摇镜/升降不执行（默认轻微推近）；wide 环境由模型自编、与其他镜头不连续（建议单独首帧再 i2v）；大特写裁切位置由模型定（需精确时先 crop 首帧）；static 仍有轻微呼吸/头部微动。
- 结论：ShotPlan 注入能实现「景别 + 推拉」的镜头语言差异化，角度需靠首帧构图；已把结论落到 contract/SHOT_PLAN_INJECTION.md 与 pipeline 实现。
## 30. ShotPlan 注入 H3 动态镜头（镜头级差异化，2026-10-25）
- 实现：pipeline/dynamic.py 为每镜生成 ShotPlan（优先 shot.shot_plan，否则 shot_grammar.plan_for_type(type, no%3)），按 OpenMontage 5-aspect 公式拼 prompt（身份锚点 + 景别/角度/高度/光线 + 运镜 + 目的），经 --prompt-file 传给 run_h3_ref；audio_ref/fl2v 都注入；meta 记录 plan/prompt_file/ref_strategy/h3_gen。
- 参考图策略：close-up/reaction 从概念角色裁脸部特写（ref_strategy=face-closeup），medium 用画窗构图；H3 生成尺寸 audio_ref 1024×576、fl2v 768²。
- 8 镜演示：outputs/shots_plan_demo/demo_final.mp4（43.76s / 1280×720 / 7.34MB / 10 镜：7 audio_ref + 1 fl2v + 2 卡）；QC ok=true；抽帧 docs/screenshots/52_shotplan_demo_sheet.png 可见 host 中近景/guest 特写/大特写/广角环境等景别差异。
- H3 可控性实测（task-37）：景别（medium/close-up/extreme close-up/**wide 可突破参考图构图**）、dolly-in/push-in、static、身份锁定 = 可控；low/high angle、横移/摇镜、wide 环境连续性 = 弱控（需换首/尾帧构图解决）。详见 docs/H3_SHOTPLAN_FINDINGS.md 与 docs/screenshots/51_h3_shotplan_control.png。
- 后续：将 ShotPlan 应用于 ep_001/002 全动态重渲（镜头差异化版）；按镜头关系选转场；wide 镜头单独首帧；接 lip-sync 精修。
## 31. 场景升级：2D 绘景 + 真实 3D 场景（OpenMontage 全量沿用，2026-10-25）
- 用户反馈「全是平面、没有 3D/2D 场景」→ 本轮补齐：
  - **2D 场景绘景 12 张**（SDXL/Animagine，1280×720，无人物）：深夜秀办公桌/圆桌演播厅/任务舞台/历史书房/辩论台/推理现场/国宝展厅/暖色厨房/彩虹舞台/访谈沙发/街头夜景/历史课堂；另 3 张 3D 渲染风 plate（3d_roundtable/3d_stage/3d_study）。
  - **真实 3D 场景 3 条**（Blender 5.0 EEVEE 程序化）：3d_studio_wide（横移全景 5s）、3d_studio_host（缓推特写 5s）、3d_stage_pan（摇镜升镜 6s）；含舞台/圆桌/主持台/弧形 LED 屏/灯架/地面反射 + 3D 卡通角色 + 相机动画；1280×720/25fps，单条渲染 32-35s。
  - **全身透明角色 cutout**：laozhou/ali/dawei 每人 front + three_quarter（1024×1536 RGBA，脚底贴边、无阴影，pixels_per_meter≈886.7）。
  - **场景合成 pipeline/scenes.py**：背景 plate + 全身 cutout 分层，按 ShotPlan 景别缩放/站位/脚底锚定地面/柔和投影/2.5D 视差与相机运动；contract/scene_map.json 模板→场景映射。
- 交付：**16 个模板全部出 2D 场景版 demo**（outputs/scene_demos/{template}.mp4，各 30-45s）+ 场景巡礼 outputs/scene_demos/scene_showcase.mp4（77.48s，含 16 个 2D 场景片段 + 3D 场景章节）；抽帧 docs/screenshots/55_all_scene_templates_sheet.png、56_scene_v2_check.png。
- 实现参考：OpenMontage 的 scene/asset 制作、b-roll planning、3d-world-generation、video-gen-prompting、image-gen 等 skills；整体方法映射见 docs/OPENMONTAGE_PRODUCTION.md。
- 已知限制：场景 demo 的角色是**3D 卡通全身 cutout**（与 2D 绘景画风有差异；下一步做 2D动漫全身 cutout/统一渲染风格）；H3 动态镜头仍是全画幅特写，尚未与场景合成融合（下一步：场景首帧 + 角色分层驱动 H3）。
