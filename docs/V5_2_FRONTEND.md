# V5.2 前端绑定：V5 世界页 + V5 Studio API

> 目标：把 V5 全量能力（16 成片 / 17 风格 / 61 场景 / 25 角色 / 44 道具 / 渲染）接进现有 React 工作台。
> 该页面与原有「项目 → 剧集 → 六个 tab」工作流并存，不改变既有接口与页面。
> V5.3 追加：新增「模板库」tab（每个模板的风格/工作流/角色/场景可见可改），并删除设置里的 V3/V4 旧「工作流模板/声音映射」面板。
> 计划：`docs/PLAN_V5_2_ART_FRONTEND.md` §4；契约：`contract/v5/`；接口：`docs/V5_INTERFACES.md`。

## 1. 页面结构（`#/v5`）

入口：左侧栏 **「V5 世界」**（Sidebar nav，deep link：`http://127.0.0.1:8090/#/v5`）。
页面顶部：标题 + 实时规模（成片/风格/场景/角色/模板/道具）+ 刷新；子 tab 支持 deep link（顺序：成片画廊 / 模板库 / 风格墙 / 场景库 / 角色库 / 渲染）：

| 子 tab | 深链 | 功能 |
|---|---|---|
| 成片画廊 | `#/v5/gallery` | 已渲染模板卡（封面/风格 badge/时长/分辨率/镜头数/QC 通过·检查数），点击播放 `<video>`；打开 timeline.html / qc_report.json；可切换「含自定义示例」 |
| 模板库 | `#/v5/templates` | 16+1 模板卡：风格（标签+色板）/时长/tags/description；**本模板工作流 7 步**（① 分镜 acts→shots ② 固定声音 TTS：cast→voice_id ③ 场景 AI plate+style finish ④ 角色 AI 立绘+口型 ⑤ motion2d 镜头动画 ⑥ 字幕/包装 ⑦ 混音/QC）；角色卡（AI 立绘/voice_id/seed → 链角色库）；场景缩略图；按钮：工作流详情 / 播放成片 / 时间线 / **编辑模板 JSON**（GET/PUT，dry_run 校验或保存 custom 覆盖）/ **用此模板渲染**（跳渲染 tab 并预选） |
| 风格墙 | `#/v5/styles` | 17 风格卡：色板 swatch、技法、角色处理、转场、标签、custom 标识；点击弹层看完整 style JSON（纹理/线条/audio/h3 prompt）+ 代表场景渲染预览 |
| 场景库 | `#/v5/scenes` | 61 场景网格：AI plate 缩略图（未生成时回退程序化渲染）、9/10 层、机位数、标签；搜索 + tag 过滤 + 按风格调色预览；详情弹层（大图/layers/camera_zones/anchors/描述） |
| 角色库 | `#/v5/characters` | 25 角色卡：AI 立绘（缺失 → 程序化预览 → 首字占位）、性别/年龄/role_pool、固定 voice_id + seed、声音锁状态；**试听**（POST preview → 播 `/media/v5/voice/<id>.wav`）；**改声音**（voice_id 预设下拉 + seed/cfg_scale/instruction → PUT 保存 → **重建声音锁**，提示需重渲）；**重建锁** |
| 渲染 | `#/v5/render` | 模板下拉/时长/force → `POST /api/v5/render` → 轮询 `GET /api/v5/render/{job}` 显示状态/进度条/日志尾/最近任务；完成自动刷新画廊；可取消 |

### 1.1 模板库（V5.3 新增，`#/v5/templates`）

用户需求：「每个模板有自己的工作流/场景/角色，要能在前端看到、能改」。

- **列表**：17 张模板卡（16 内建 + `custom_holo_talk`），每卡显示 label/id、风格 badge + 色板、时长/acts/角色数/场景数/成片时长、tags、description、
  7 步工作流缩略条（**① 分镜 ② TTS ③ 场景 ④ 立绘 ⑤ 镜头 ⑥ 包装 ⑦ QC**）、cast 头像条、场景缩略图条；
- **工作流详情弹层**（点「工作流」或深链 `#/v5/templates/<id>`）：
  - ① acts 列表（scene/camera/duration/台词 role/镜头文字/B-roll）与 `acts → shots` 数；
  - ② cast 角色 → `voice_id + seed + 声音锁`（点击跳角色库改声音）；
  - ③ 场景去重后缩略图（`/media/v5/scenes/<id>.png`，缺失回退程序化预览）+ style finish；
  - ④ 角色 AI 立绘 + lipsync 模式；⑤ camera 分布与平均 camera_strength；⑥ style typography + template packaging；⑦ 混音（bgm/sfx/ducking）+ 成片 QC/时长/分辨率；
- **编辑模板 JSON**（深链 `#/v5/templates/<id>?edit=1`）：`GET /api/v5/templates/{id}` 取合并后的完整 JSON → 编辑 →「只校验（dry_run）」或「保存为 custom 覆盖」（PUT `/api/v5/templates/{id}`，后端 schema/引用校验，失败显示 errors，成功后刷新）；
- **用此模板渲染**：跳转渲染 tab 并把模板下拉预选为该模板。

### 1.2 设置弹窗清理（V5.3）

设置里属于 V3/V4 旧管线（ComfyUI H3 workflow + edge-tts voice_map）的两块已删除：

- ❌「工作流模板（API 格式）」textarea（`form.workflow_template`）
- ❌「声音映射」（`voiceMap` 只读 `<pre>`）

`SettingsModal.tsx` 同时清掉 state/form/save payload 中的 `workflow_template` 与 `voiceMap` 读写；**后端 Settings API 与 `types.ts` 字段保持不动**（V3/V4 兼容）。其余设置（生成模式/分辨率/TTS/H3/上传/LLM）不变。

## 2. 文件清单（本次新增/修改）

```
backend/app/routers/v5_studio.py      # 新增：outputs / media / voice PUT / render job
backend/app/main.py                   # +2 行（import + include_router，studio 先于 catalog 注册）
frontend/src/lib/v5.ts                # 新增：V5 类型 + API 封装 + 媒体/预览 URL helper
frontend/src/components/V5Studio.tsx  # 页面壳（标题/规模/tabs/数据加载/deep link；V5.3 加「模板库」）
frontend/src/components/v5/shared.tsx        # 多级回退图片/色板/标签/QC 徽标/视频弹层
frontend/src/components/v5/Gallery.tsx       # 成片画廊
frontend/src/components/v5/Templates.tsx     # V5.3 模板库（模板卡/7 步工作流/编辑 JSON/跳渲染）
frontend/src/components/v5/StyleWall.tsx     # 风格墙 + 详情弹层
frontend/src/components/v5/SceneLibrary.tsx  # 场景库（搜索/tag/风格预览/详情）
frontend/src/components/v5/CharacterLibrary.tsx # 角色库 + 声音试听
frontend/src/components/v5/VoiceEditor.tsx   # 改声音弹层（PUT + 重建声音锁）
frontend/src/components/v5/RenderPanel.tsx   # 渲染提交 + job 轮询（支持外部预选模板）
frontend/src/styles/v5.css            # 样式（v5- 前缀，复用设计变量；含模板库/工作流/JSON 编辑器）
frontend/src/App.tsx                  # +v5 view mode + #/v5 路由；原有 projects/workspace 不变
frontend/src/components/Sidebar.tsx   # +「V5 世界」入口（active 态）
frontend/src/components/TopBar.tsx    # +v5 面包屑（其余分支不变）
frontend/src/components/SettingsModal.tsx # V5.3 删除「工作流模板/声音映射」旧面板（仅 UI）
frontend/src/main.tsx                 # +import v5.css
```

## 3. 新增后端 API（`backend/app/routers/v5_studio.py`）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v5/outputs?include_custom=&include_missing=` | 扫描 `outputs/templates_v5/*/`，默认返回 16 个内建模板产物；字段：`id,label,style,style_label,style_tags,duration,resolution,fps,qc_ok,qc_checks,qc_total,shots,scenes,characters,final_url,timeline_url,qc_url,style_report_url,poster_url,mtime` |
| GET | `/media/v5/{template}/{file}` | 白名单：`final.mp4` `timeline.html` `qc_report.json` `style_report.json` `timeline.json` `contact_sheet.png` `frames/frame_NNN.png`；`resolve()` 后校验在模板目录内（防穿越，非白名单/越界 404） |
| GET | `/media/v5/characters/{id}/{file}` | 角色 AI 立绘：`portrait/front/cutout/thumb.(png/jpg/webp)`（`assets/v5/characters/<id>/`，缺失 404 → 前端回退） |
| GET | `/media/v5/scenes/{file}` | 场景 AI plate（`assets/v5/scenes/<id>.png`，缺失 404 → 前端回退程序化预览） |
| GET | `/media/v5/voice/{id}.wav` | 角色试听产物（`tmp/v5_voice/<id>.wav`） |
| GET | `/api/v5/voice/{character}` | 读 resolved 声音 + 声音锁状态（`needs_relock`/`stale_locks`）※ 先于 catalog 的通配路由注册，避免被 `/{kind}/{id}` 拦截 |
| PUT | `/api/v5/voice/{character}` | body `{voice_id?, seed?, cfg_scale?, instruction?, ref_text?, provider?}` → 校验（voice_id 必须在声音库、id 必须是 character）→ 原子写 `contract/v5/custom/characters/<id>.json`（只含 id+voice）→ 返回 resolved spec / previous_key / needs_relock |
| POST | `/api/v5/render` | body `{template, duration=40, force=false}` → 202 + job（单并发；已在跑 → 409 带 running job）；子进程 `python -m pipeline.cli render_v5 --template ... --duration ... [--force]`（`sys.executable`，CREATE_NO_WINDOW，UTF-8 管道） |
| GET | `/api/v5/render` | 最近 job 列表 + 当前 running |
| GET | `/api/v5/render/{job_id}` | job 状态：`status=queued/running/done/failed/canceled`、`progress 0-100`（解析子进程 `PROGRESS n` 行）、`log_tail`（≤240 行）、`elapsed/returncode/error/result` |
| DELETE | `/api/v5/render/{job_id}` | 取消（terminate 子进程，状态置 canceled） |
| GET | `/api/v5/templates/{id}` | 模板完整 JSON（内建 + custom 合并）：`cast/acts/packaging/audio/lipsync/meta` 等（V5.3 模板库用） |
| PUT | `/api/v5/templates/{id}?dry_run=` | 保存模板 custom 覆盖：`{"item": {...}}`；`dry_run=true` 只做 schema+引用校验（400 返回 `{message, kind, id, errors, warnings}`）。partial 覆盖语义：缺省键沿用内建值 |

> 既有 V5 契约 API（`/api/v5/catalog`、`/{kind}/{id}`、`/validate`、`/preview/...`、`/voices`、`/voice/*/preview|lock`）由 `v5_catalog.py` 提供，本次未改动。

## 4. 前端实现要点

- **API 层**：`lib/v5.ts` 独立封装（60-90s 超时；试听/锁 240-600s），路径相对 `API_BASE=/api`，dev 走 Vite proxy；`VITE_API_BASE` 直连后端时 `mediaUrl()` 自动补 origin。
- **404 容错 / 占位**：`SmartImage` 依次尝试 `AI 资产 → 程序化 preview API → 占位`（onError 自动切下一档）；美术资产未生成的场景/角色不会白屏。
- **声音**：角色库批量拉 `GET /api/v5/voice/{id}`（25 个并行）展示 voice_id/seed/lock；「试听」先 POST 生成 wav 再播 `/media/v5/voice/<id>.wav?t=...`；「改声音」PUT 后展示 `needs_relock` 提示并可直接「重建声音锁」。
- **渲染 job**：提交后 1.5s 轮询；`done` 自动刷新画廊并 toast；`failed` 显示错误 + 日志尾；离开页面轮询随组件卸载停止，job 在服务端继续。
- **deep link**：`#/v5/<tab>` 双向同步（切 tab 用 history.replaceState），便于分享/截图；模板库支持 `#/v5/templates/<id>[/?edit=1]` 直达详情/编辑弹层。
- **模板库（V5.3）**：列表批量加载 17 个模板完整 JSON（catalog 摘要 + `GET /templates/{id}`）；详情打开时并行补 cast 声音（`GET /voice/{character}`）与 style 详情；「编辑 JSON」本地 `JSON.parse` 先挡语法错误，PUT 失败直接展示后端 `errors`；「用此模板渲染」通过 `V5Studio` 的 `presetTemplate` 传给渲染面板预选。
- **设置清理（V5.3）**：`SettingsModal` 删除「工作流模板（API 格式）」「声音映射」两块及其 state/form/save 字段；后端 API 不动。
- **不破坏原工作台**：`App.tsx` 只新增 `view.mode="v5"` 分支；projects / workspace 渲染分支与 Sidebar/TopBar 原有 props 保持不变（仅新增可选 props）。

## 5. 启动 / 构建

```bash
# 1) 后端（8090；已有 V5 路由）
python -m uvicorn app.main:app --port 8090 --app-dir backend

# 2) 前端开发（5173，proxy /api /media → 8090）
cd frontend && npm run dev
#   → http://127.0.0.1:5173/#/v5

# 3) 生产构建（tsc --noEmit + vite build → frontend/dist，后端 8090 直接托管）
cd frontend && npm run build
#   → http://127.0.0.1:8090/#/v5

# 4) 契约校验（不占 GPU）
python -m pipeline.v5_cli validate
```

构建结果（V5.3 末次）：
```
> tsc --noEmit && vite build
✓ 79 modules transformed.
dist/index.html                   0.59 kB
dist/assets/index-LnfDir3k.css  129.38 kB │ gzip:  21.36 kB
dist/assets/index-CNJUdq8T.js   386.52 kB │ gzip: 119.46 kB
✓ built in 616ms
```

## 6. 自测证据（`tmp/v52_frontend/`）

### 6.1 API curl/httpx 证据（对 8090，30/30 PASS）

`python tmp/v52_frontend/curl_evidence.py http://127.0.0.1:8090` → `evidence/summary.json`

- `GET /api/v5/outputs`：**16 条**（字段齐全，含 final/timeline/qc/poster url）
- `include_custom=true`：17 条（含 custom_holo_talk）
- `/media/v5/tonight_show/final.mp4`：200/206（Range 请求可播放，`video/mp4`）
- `timeline.html` / `qc_report.json` / `contact_sheet.png` / `style_report.json`：200
- 路径穿越 `../../app/main.py` 等 3 组：均 404
- `GET /api/v5/voices`：24 预设；`GET /api/v5/voice/laozhou`：resolved voice
- `PUT /api/v5/voice/laozhou`：200，写入 custom 覆盖后 GET 生效；未知 voice_id → 400；测试后已回滚删除
- `POST /api/v5/render`：非法模板 400；合法 → 202 job；并发第二个 → 409；`GET /api/v5/render/{id}` 有状态/进度/日志；`DELETE` 取消成功；取消后 tonight_show final.mp4 未被覆盖
- 证据文件：`tmp/v52_frontend/evidence/{outputs,outputs_custom,voices,voice_laozhou_before,voice_laozhou_put,render_start,render_job,render_list,render_cancel,summary}.json`

### 6.1b 模板库 / 设置清理证据（V5.3，对 8090）

`python tmp/v52_frontend/templates_evidence.py http://127.0.0.1:8090` → **8/8 PASS**（`evidence/templates_summary.json`）：
- `GET /api/v5/catalog` templates=17（16 内建 + custom）
- `GET /api/v5/templates/history_talk`：9 acts / 3 cast / style ink_chronicle，含 `cast/acts/packaging/audio/lipsync/meta`
- `GET /api/v5/templates/custom_holo_talk`：`custom=true`
- `PUT /api/v5/templates/history_talk?dry_run=true` 合法 → 200 `validated`（**未写盘**）
- PUT `acts` 缺必填字段 → **400 + errors**；PUT 场景引用不存在 → **400**（不落盘）

`python tmp/v52_frontend/settings_check.py`（CDP 点击设置 + 文本断言）→ **7/7 PASS**（`evidence/settings_panel_check.json`）：
- 设置弹窗已打开；`工作流模板` / `声音映射` / `h3_roundtable` 均**不再出现**；生成模式/分辨率等基础设置仍在；截图 `settings_modal_clean.png`

`python tmp/v52_frontend/templates_ui_check.py`（CDP 交互）→ **12/12 PASS**（`evidence/templates_ui_check.json`）：
- tab 顺序 `成片画廊 → 模板库`；**17 个模板卡**；卡片含 7 步工作流缩略
- 详情弹层 **7 步工作流**、acts 列表、场景缩略图、cast 声音卡、角色 AI 立绘
- 编辑 JSON 弹层加载完整模板 JSON，含 dry_run/保存按钮
- 「用此模板渲染」跳渲染 tab 且**模板下拉预选正确**

### 6.2 页面截图（Chrome headless，8090 托管 dist）

> 截图重拍于 **2026-10-26 19:35（16 部 AI 美术全量重渲、16/16 QC PASS 之后）**：
> 成片封面为新 AI 场景/角色，角色立绘全部 AI 美术，场景库 plate 为新 AI 绘景。
> 模板库/设置截图于 V5.3（20:0x）补拍，均为当前 8090 最新 dist。

| 截图 | 内容 |
|---|---|
| `tmp/v52_frontend/screenshots/v5_gallery.png` | 成片画廊：16 卡 AI poster/风格/时长/QC/播放/时间线/QC |
| `tmp/v52_frontend/screenshots/v5_templates.png` | **模板库列表**：17 卡（风格/时长/tags/description/7 步工作流缩略/cast/场景缩略/按钮） |
| `tmp/v52_frontend/screenshots/v5_template_detail.png` | **模板工作流详情**（history_talk）：① 分镜 acts 表 ② cast→voice_id/seed/锁 ③ 场景 AI plate ④ 角色立绘… |
| `tmp/v52_frontend/screenshots/v5_template_edit.png` | **编辑模板 JSON 弹层**：完整 JSON 编辑器 + 只校验(dry_run) / 保存为 custom 覆盖 |
| `tmp/v52_frontend/screenshots/v5_template_jump_render.png` | 「用此模板渲染」→ 渲染 tab 且模板已预选（典籍 · woodblock_classic） |
| `tmp/v52_frontend/screenshots/settings_modal_clean.png` | **设置弹窗（清理后）**：无「工作流模板/声音映射」旧面板 |
| `tmp/v52_frontend/screenshots/v5_styles.png` | 风格墙：17 风格色板/技法/转场/标签 |
| `tmp/v52_frontend/screenshots/v5_scenes.png` | 场景库：61 场景（AI plate）+ 搜索/tag/风格选择 |
| `tmp/v52_frontend/screenshots/v5_characters.png` | 角色库：AI 立绘/声音锁/voice+seed/试听/改声音/重建锁 |
| `tmp/v52_frontend/screenshots/v5_render.png` | 渲染面板：模板/时长/force + 任务状态/日志/最近任务 |
| `tmp/v52_frontend/screenshots/workspace_projects.png` | 回归：原「项目总览」页与 Sidebar 正常（新增 V5 入口） |

复现截图（Chrome headless，任选 8090/5173）：

```powershell
& "C:\Program Files\Google\Chrome\Application\chrome.exe" --headless=new --disable-gpu `
  --hide-scrollbars --window-size=1680,1050 --virtual-time-budget=20000 `
  --screenshot="tmp\v52_frontend\screenshots\v5_gallery.png" "http://127.0.0.1:8090/#/v5/gallery"
```

## 7. 已知限制 / 说明

- **美术资产已全量落地**（24 角色立绘 + 61 场景 plate，16 部已用 AI 美术重渲）；`SmartImage` 的三级回退（AI 资产 → 程序化 preview → 占位）保留，用于未来新增角色/场景资产尚未生成时的 404 容错；
- 场景缩略图首次滚动会调 `/api/v5/preview/scene/<id>`（程序化渲染，约 0.2-0.6s/张），已加 `side preview`/`loading=lazy`；美术 plate 全量后不再需要；
- `/api/v5/outputs` 默认只返回 catalog 内建模板（16）；自定义示例用「含自定义示例」开关或 `?include_custom=true`；
- 渲染任务写内存（服务重启后历史 job 丢失，产物文件保留）；单并发是设计约束（GPU/TTS 串行）；
- 声音「试听/重建锁」依赖 Breeze TTS 服务，未启动时接口返回 503，前端 toast 提示（不阻塞页面其他功能）；
- 模板 JSON 编辑采用 **partial 覆盖**语义：只写想改的键（`PUT` 的 item 作为 custom 片段深合并到内建模板）；保存后需重渲对应模板成片才生效；删除某个键不能通过「不写」实现（需整体替换该字段或直接改 contract 源文件）。

## 8. 验收清单

- [x] `cd frontend && npm run build` 通过（tsc --noEmit + vite build，79 modules）
- [x] `/api/v5/outputs` 16 条、字段齐全（`evidence/outputs.json`）
- [x] `/media/v5/...` 播放/时间线/QC/封面可访问 + 防路径穿越
- [x] 页面可浏览：画廊/模板库/风格墙/场景库/角色库/渲染
- [x] 模板库：17 卡、7 步工作流可见、角色/场景展示、编辑 JSON（dry_run/保存）、跳渲染预选（CDP 12/12）
- [x] 设置弹窗已删除「工作流模板/声音映射」旧面板（CDP 7/7 + 截图）
- [x] 声音：读取/试听（依赖 TTS 服务）/改声音 PUT/重建锁按钮
- [x] 渲染：提交 job → 轮询进度/日志 → 取消；完成自动刷新
- [x] 原 projects/workspace 页面无回归（截图 + 原有测试）
