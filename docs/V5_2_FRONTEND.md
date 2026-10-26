# V5.2 前端绑定：V5 世界页 + V5 Studio API

> 目标：把 V5 全量能力（16 成片 / 17 风格 / 61 场景 / 25 角色 / 44 道具 / 渲染）接进现有 React 工作台。
> 该页面与原有「项目 → 剧集 → 六个 tab」工作流并存，不改变既有接口与页面。
> 计划：`docs/PLAN_V5_2_ART_FRONTEND.md` §4；契约：`contract/v5/`；接口：`docs/V5_INTERFACES.md`。

## 1. 页面结构（`#/v5`）

入口：左侧栏 **「V5 世界」**（Sidebar nav，deep link：`http://127.0.0.1:8090/#/v5`）。
页面顶部：标题 + 实时规模（成片/风格/场景/角色/模板/道具）+ 刷新；子 tab 支持 deep link：

| 子 tab | 深链 | 功能 |
|---|---|---|
| 成片画廊 | `#/v5/gallery` | 已渲染模板卡（封面/风格 badge/时长/分辨率/镜头数/QC 通过·检查数），点击播放 `<video>`；打开 timeline.html / qc_report.json；可切换「含自定义示例」 |
| 风格墙 | `#/v5/styles` | 17 风格卡：色板 swatch、技法、角色处理、转场、标签、custom 标识；点击弹层看完整 style JSON（纹理/线条/audio/h3 prompt）+ 代表场景渲染预览 |
| 场景库 | `#/v5/scenes` | 61 场景网格：AI plate 缩略图（未生成时回退程序化渲染）、9/10 层、机位数、标签；搜索 + tag 过滤 + 按风格调色预览；详情弹层（大图/layers/camera_zones/anchors/描述） |
| 角色库 | `#/v5/characters` | 25 角色卡：AI 立绘（缺失 → 程序化预览 → 首字占位）、性别/年龄/role_pool、固定 voice_id + seed、声音锁状态；**试听**（POST preview → 播 `/media/v5/voice/<id>.wav`）；**改声音**（voice_id 预设下拉 + seed/cfg_scale/instruction → PUT 保存 → **重建声音锁**，提示需重渲）；**重建锁** |
| 渲染 | `#/v5/render` | 模板下拉/时长/force → `POST /api/v5/render` → 轮询 `GET /api/v5/render/{job}` 显示状态/进度条/日志尾/最近任务；完成自动刷新画廊；可取消 |

## 2. 文件清单（本次新增/修改）

```
backend/app/routers/v5_studio.py      # 新增：outputs / media / voice PUT / render job
backend/app/main.py                   # +2 行（import + include_router，studio 先于 catalog 注册）
frontend/src/lib/v5.ts                # 新增：V5 类型 + API 封装 + 媒体/预览 URL helper
frontend/src/components/V5Studio.tsx  # 新增：页面壳（标题/规模/tabs/数据加载/deep link）
frontend/src/components/v5/shared.tsx        # 多级回退图片/色板/标签/QC 徽标/视频弹层
frontend/src/components/v5/Gallery.tsx       # 成片画廊
frontend/src/components/v5/StyleWall.tsx     # 风格墙 + 详情弹层
frontend/src/components/v5/SceneLibrary.tsx  # 场景库（搜索/tag/风格预览/详情）
frontend/src/components/v5/CharacterLibrary.tsx # 角色库 + 声音试听
frontend/src/components/v5/VoiceEditor.tsx   # 改声音弹层（PUT + 重建声音锁）
frontend/src/components/v5/RenderPanel.tsx   # 渲染提交 + job 轮询
frontend/src/styles/v5.css            # 新增样式（全部 v5- 前缀，复用设计变量）
frontend/src/App.tsx                  # +v5 view mode + #/v5 路由；原有 projects/workspace 不变
frontend/src/components/Sidebar.tsx   # +「V5 世界」入口（active 态）
frontend/src/components/TopBar.tsx    # +v5 面包屑（其余分支不变）
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

> 既有 V5 契约 API（`/api/v5/catalog`、`/{kind}/{id}`、`/validate`、`/preview/...`、`/voices`、`/voice/*/preview|lock`）由 `v5_catalog.py` 提供，本次未改动。

## 4. 前端实现要点

- **API 层**：`lib/v5.ts` 独立封装（60-90s 超时；试听/锁 240-600s），路径相对 `API_BASE=/api`，dev 走 Vite proxy；`VITE_API_BASE` 直连后端时 `mediaUrl()` 自动补 origin。
- **404 容错 / 占位**：`SmartImage` 依次尝试 `AI 资产 → 程序化 preview API → 占位`（onError 自动切下一档）；美术资产未生成的场景/角色不会白屏。
- **声音**：角色库批量拉 `GET /api/v5/voice/{id}`（25 个并行）展示 voice_id/seed/lock；「试听」先 POST 生成 wav 再播 `/media/v5/voice/<id>.wav?t=...`；「改声音」PUT 后展示 `needs_relock` 提示并可直接「重建声音锁」。
- **渲染 job**：提交后 1.5s 轮询；`done` 自动刷新画廊并 toast；`failed` 显示错误 + 日志尾；离开页面轮询随组件卸载停止，job 在服务端继续。
- **deep link**：`#/v5/<tab>` 双向同步（切 tab 用 history.replaceState），便于分享/截图。
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

构建结果（本次）：
```
> tsc --noEmit && vite build
✓ 78 modules transformed.
dist/index.html                   0.59 kB
dist/assets/index-CQCI5vMd.css  122.96 kB │ gzip:  20.48 kB
dist/assets/index-CxDpLa8g.js   369.88 kB │ gzip: 115.17 kB
✓ built in 823ms
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

### 6.2 页面截图（Chrome headless，8090 托管 dist）

| 截图 | 内容 |
|---|---|
| `tmp/v52_frontend/screenshots/v5_gallery.png` | 成片画廊：16 卡封面/风格/时长/QC/播放/时间线/QC |
| `tmp/v52_frontend/screenshots/v5_styles.png` | 风格墙：17 风格色板/技法/转场/标签 |
| `tmp/v52_frontend/screenshots/v5_scenes.png` | 场景库：61 场景（AI plate 已生成部分直接显示）+ 搜索/tag/风格选择 |
| `tmp/v52_frontend/screenshots/v5_characters.png` | 角色库：立绘/声音锁/voice+seed/试听/改声音/重建锁 |
| `tmp/v52_frontend/screenshots/v5_render.png` | 渲染面板：模板/时长/force + 任务状态/日志/最近任务 |
| `tmp/v52_frontend/screenshots/workspace_projects.png` | 回归：原「项目总览」页与 Sidebar 正常（新增 V5 入口） |

复现截图（Chrome headless，任选 8090/5173）：

```powershell
& "C:\Program Files\Google\Chrome\Application\chrome.exe" --headless=new --disable-gpu `
  --hide-scrollbars --window-size=1680,1050 --virtual-time-budget=20000 `
  --screenshot="tmp\v52_frontend\screenshots\v5_gallery.png" "http://127.0.0.1:8090/#/v5/gallery"
```

## 7. 已知限制 / 说明

- **美术资产仍在生成**（场景 10+/61、角色 4/24）：缺失的立绘/plate 由 `SmartImage` 回退到程序化 `preview` 或占位；资产落盘后刷新即显示，无需改前端；
- 场景缩略图首次滚动会调 `/api/v5/preview/scene/<id>`（程序化渲染，约 0.2-0.6s/张），已加 `side preview`/`loading=lazy`；美术 plate 全量后不再需要；
- `/api/v5/outputs` 默认只返回 catalog 内建模板（16）；自定义示例用「含自定义示例」开关或 `?include_custom=true`；
- 渲染任务写内存（服务重启后历史 job 丢失，产物文件保留）；单并发是设计约束（GPU/TTS 串行）；
- 声音「试听/重建锁」依赖 Breeze TTS 服务，未启动时接口返回 503，前端 toast 提示（不阻塞页面其他功能）。

## 8. 验收清单

- [x] `cd frontend && npm run build` 通过（tsc --noEmit + vite build）
- [x] `/api/v5/outputs` 16 条、字段齐全（`evidence/outputs.json`）
- [x] `/media/v5/...` 播放/时间线/QC/封面可访问 + 防路径穿越
- [x] 页面可浏览：画廊/风格墙/场景库/角色库/渲染（截图 5 张）
- [x] 声音：读取/试听（依赖 TTS 服务）/改声音 PUT/重建锁按钮
- [x] 渲染：提交 job → 轮询进度/日志 → 取消；完成自动刷新
- [x] 原 projects/workspace 页面无回归（截图 + 原有测试）
