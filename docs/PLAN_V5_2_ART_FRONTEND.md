# V5.2 计划：AI 美术升级 + V5 前端绑定

> 触发：用户反馈「生成的角色和场景都太丑了；都没绑定到前端；最早 3 个角色是可以的」。
> 现状：V5 角色/场景是程序化几何绘制（`characters_v5.py` / `scene_render.py`），风格区分做到了但美术粗糙；
> 最早的 3 个角色（laozhou/ali/dawei）走的是 **SDXL Animagine XL 4.0 动漫立绘 + 白底泛洪抠图**（`imagegen` + `scripts/make_2d_cutouts.py`），用户认可。
> 前端：`frontend/` 是 React+Vite「造片局」工作台（project/script/assets/storyboard/generate/finish 六个 tab），
> 后端 FastAPI 8090，V5 API（`/api/v5/*`）已存在但前端没有任何 V5 入口/页面。

## 1. 目标

1. **美术升级**：24 个角色 + 61 个场景全部换成 AI 生成动漫美术（与最早 3 角色同一条 SDXL 管线、同一画风）；保留 16 风格差异化（风格 finish/调色/纹理叠加在 AI plate/立绘上）。
2. **前端绑定**：在 React 工作台里加「V5 世界」页面：
   - 16 部成片画廊（播放/QC/时间线/风格标签）；
   - 16 风格墙、61 场景库、24 角色库（立绘卡 + 固定声音试听/修改）；
   - 声音编辑（`voice_id`/`seed`/`instruction` 修改 + 重建声音锁 + 试听）；
   - 触发生成（选模板/时长/是否重渲 → 后端 job + 进度轮询）。
3. **重渲 16 部**并 QA 复验；更新文档/README；提交。

## 2. 美术管线（角色）

```
contract/v5/characters/<id>.json appearance
   │ tmp/gen_v52_char_specs.py 适配 → imagegen V3 spec（tmp/v52_art/specs/<id>.json）
   ▼ imagegen.cli generate（SDXL Animagine XL 4.0，ComfyUI 8188，串行）
portrait 832x1216（角色卡/近景）      three_view/front 1024x1024（白底全身，抠图）
   ▼ make_2d_cutouts.remove_white（泛洪去白 + 羽化 + crop）
assets/v5/characters/<id>/{portrait.png, front.png, cutout.png, mouth_*.png, meta.json}
   ▼ characters_v5 新接口 art_character(id)
AI 立绘 + style_v5.apply_finish 风格化 + 口型/眨眼 overlay（见 §2.1）
```

### 2.1 口型/眨眼（AI 立绘上）
- 用 YuNet 检测脸部/嘴部框（`pipeline/tts` 已有 CV 依赖；或复用 lipsync 的 YuNet 检测器）。
- 口型 overlay：在嘴部区域按 viseme 画「动漫风嘴型」（闭合线 / 小 o / 半开 / 大开 / 扁 e / 圆 i / 抿 m），
  边缘用肤色/线稿色羽化，随 viseme 时间轴切换（2-3 帧过渡），与 TTS 同源 → 口型同步不退化。
- 眨眼：检测眼睛框，画 1-2 帧眼睑闭合线（或降低眼睛区域亮度）→ 自然眨眼。
- 风险：嘴 overlay 与画风一致性 → pilot 先验 1 个角色（zhuge），合格再批量。
- 备选：每角色额外生成 `expressions/{neutral,talking,blink}` 3 张（IP-Adapter 一致性），用脸部区域交叉淡入实现口型/眨眼；pilot 对比两种取更好者。

## 3. 美术管线（场景）

```
contract/v5/scenes/<scene>.json（现有 61 个：layers/anchors/camera_zones）
   │ contract/v5/art/prompts.json scenes[scene]（SDXL 场景 prompt + negative）
   ▼ imagegen/ComfyUI SDXL txt2img（1024x1024 或 1280x720 比例）
assets/v5/scenes/<scene>.png（风格无关的基础 anime plate）
   ▼ 运行时：style_v5.apply_finish(style) + 程序化 mid/fg overlay（现有 scene_render 扩展：plate 模式）
```

- 场景 prompt 必须包含**历史内容**：古地图（行军路线/城池/罗盘）、战场（远山/军旗/雾气）、军帐沙盘、宫殿、藏书楼、博物馆展柜、发掘现场、关隘等。
- plate 模式兼容旧程序化：`scene_render.render_scene_layers()` 若 `scene.art` 存在 → 载入 plate 为 background，mid/fg 用程序化 fx（光斑/雾/前景剪影）+ depth 仍生成；`camera_zones/anchors` 不变。
- 每场景可生成 1-2 个 variants（不同 seed）供风格化。

## 4. 前端绑定（V5 Studio）

新增 `frontend/src/components/V5Studio.tsx` + 相关子组件，导航加「V5 世界」。

| 区块 | 功能 | API |
|---|---|---|
| 成片画廊 | 16 模板卡：封面（contact_sheet/frames）、视频播放、风格/时长/QC、时间线 HTML 打开 | `GET /api/v5/outputs`、`GET /media/v5/{template}/final.mp4` |
| 风格墙 | 16 风格：色板/技法/tag/seal、代表场景缩略图 | `GET /api/v5/catalog`、`GET /api/v5/preview/{kind}/{id}` |
| 场景库 | 61 场景：plate 缩略图、tags、风格预览 | 同上 |
| 角色库 | 24 角色：AI 立绘卡；声音试听；改 `voice_id`/`seed`/`instruction`；重建声音锁 | `GET /api/v5/characters`、`PUT /api/v5/voice/{id}`、`POST /api/v5/voice/{id}/preview|lock`、`GET /api/v5/voices` |
| 渲染面板 | 选模板/时长/force → 提交后台渲染；轮询进度/日志；完成后刷新画廊 | `POST /api/v5/render`、`GET /api/v5/render/{job}` |

后端新增（`backend/app/routers/v5_catalog.py` 或新 `v5_studio.py`）：
- `GET /api/v5/outputs`：扫描 `outputs/templates_v5/*/`（final.mp4/timeline.json/qc_report.json/style_report.json/contact_sheet.png）+ 媒体 URL。
- `PUT /api/v5/voice/{character}`：保存 `custom/characters/<id>.json`（voice 覆盖），返回合并后的 resolved spec。
- `POST /api/v5/render`：`{template, duration, force}` → 启动 `python -m pipeline.cli render_v5 ...` 子进程（GPU/TTS 串行 lock），返回 job id；`GET /api/v5/render/{job}` 返回状态/进度/日志尾部。
- 静态媒体：`/media/v5/{template}/final.mp4` 等（FastAPI StaticFiles 或 FileResponse）。
- `POST /api/v5/voice/{character}/lock`、`/preview` 已有；补 `PUT` 保存。

前端构建/验证：`cd frontend; npm run build`；dev 5173（已有 proxy）；截图用 `scripts/screenshot.ps1`（如可用）或 Playwright（若已装）。

## 5. 阶段

| 阶段 | 内容 | 验收 |
|---|---|---|
| V5.2.0 pilot | zhuge 角色（portrait/front/cutout/mouth overlay）+ 2 个场景 plate + 风格化 | 人眼看图：接近最早 3 角色、历史场景有地图/战场；口型 overlay 不违和 |
| V5.2.1 批量 | 24 角色（portrait+front+cutout）+ 61 场景 plate；`assets/v5/` + `contract/v5/art/prompts.json` + `meta.json` | 24/24、61/61 资产齐；生成用时/seed/失败可复现 |
| V5.2.2 管线接入 | characters_v5 AI 立绘 + 口型/眨眼；scene_render plate 模式；单模板 pilot（history_talk） | 单部成片 QC PASS + 抽帧人眼 OK |
| V5.2.3 前端 | V5 Studio 页面 + 后端 API；导航接入；`npm run build` 通过 | 页面可浏览/播放/试听/改声音/触发渲染（截图证据） |
| V5.2.4 重渲+QA | 16 部重渲（新美术+固定声音）；qa 复验；文档/README/提交 | 16/16 QC PASS + QA 验收 + 视觉抽检 |

## 6. 风险与对策

| 风险 | 对策 |
|---|---|
| SDXL 历史服饰/场景画不准 | prompt 强化 + negative；每场景 2 seed 选优；失败场景退回程序化 plate |
| 24 角色画风不一致 | 同一 checkpoint/采样参数/风格前缀；IP-Adapter 参考最早 3 角色图（可选） |
| 立绘抠图残留白边 | 复用 `remove_white` 泛洪 + 羽化；深色背景立绘另生成白底 front |
| 口型 overlay 违和 | pilot 验收；改用 expressions 交叉淡入；最差保留程序化角色（仅少数模板） |
| GPU 排队（ComfyUI 串行 ~100 图） | 后台串行生成（~25-35 min），期间做前端/文档；进度落 `tmp/v52_art/progress.json` |
| 前端联调时间 | 先接口后页面；用现有 `api.ts`；mock 兜底 |
| 渲染链路回归 | 保留 V5 原程序化路径为 fallback（`characters.art` 缺失时用旧渲染），灰度逐模板切换 |

## 7. 文件边界（并行）

| Owner | 文件 |
|---|---|
| render-dev | `pipeline/v5_art.py`（角色美术生成/加载/口型 overlay）、`characters_v5.py` 集成、`tmp/v52_art/*` 脚本 |
| h3-dev | `pipeline/v5_scene_art.py`、`scripts/gen_v5_scene_art.py`、`assets/v5/scenes/*`、`contract/v5/art/prompts.json` 的 scenes 段 |
| template-author | `contract/v5/art/prompts.json`（characters 段 + 24 角色 art 字段）、`contract/v5/characters/*.json` 的 `art` 字段 |
| stage-dev | `frontend/src/components/V5Studio.tsx` 等前端文件、`backend/app/routers/v5_studio.py`、`backend/app/main.py` 注册 |
| Lead | `pipeline/scene_render.py` plate 接入、`pipeline/render_v5.py` 集成、`docs/*`、重渲/验收/提交 |
| qa-verifier | 验收测试/报告（V5.2.4） |
