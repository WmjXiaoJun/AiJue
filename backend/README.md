# 造片局 · AI 微综艺 -- 后端（backend/）

FastAPI + SQLAlchemy + SQLite 的 demo 后端：实现 contract/API.md 与 contract/CHARACTER.md
全部端点，托管 outputs/ 生成产物，并在 frontend/dist 存在时托管前端构建产物。

## 快速启动

在仓库根目录（D:\BaiduSyncdisk\wmj_work\XJ\AiJue）执行：

    python -m uvicorn app.main:app --port 8090 --app-dir backend

或使用脚本（自动切到仓库根、默认 --reload）：

    powershell -ExecutionPolicy Bypass -File backend\run.ps1 -Port 8090

**长期运行（推荐给 demo/演示）**：用独立进程启动，关终端/Agent 会话后服务不退出：

    powershell -ExecutionPolicy Bypass -File backend\run-detached.ps1          # 启动（8090）
    powershell -ExecutionPolicy Bypass -File backend\run-detached.ps1 -Stop    # 停止

pid 与日志落在 backend/data/（server-8090.pid / server-8090.out.log / server-8090.err.log）。
scripts/dev.ps1 是前台调试入口；需要后台常驻时用 run-detached.ps1。

打开：

- http://127.0.0.1:8090/api/health          健康检查
- http://127.0.0.1:8090/docs                OpenAPI 交互文档
- http://127.0.0.1:8090/api/projects        demo 项目（已播种）
- http://127.0.0.1:8090/media/...           outputs/ 下生成文件

## 数据与播种

- SQLite 文件：backend/data/zaopianju.db（启动时自动建表 + 轻量迁移）
- 空库自动从 contract/demo_episode.json 播种：1 项目 / 1 集 / 5 资产 / 52 镜头
- 角色播种：contract/characters/{laozhou,ali,dawei}.json -> 3 个 Character，
  分别关联 host / guest_a / guest_b 资产（每次启动幂等补齐，老库也会补）
- 已有数据则跳过，可重复启动；强制重建：

      cd backend
      python -m app.seed --force

## 目录结构

    backend/
      app/
        config.py    路径/环境变量配置
        db.py        引擎、Session、建表与启动初始化
        models.py    ORM：Project/Episode/Asset/Shot/ShotVersion/Job/Setting
        schemas.py   Pydantic 请求/响应模型
        seed.py      空库播种 / 强制重建
        jobs.py      后台任务线程（shot / render / character / concept / h3 / export）
        comfyui.py   ComfyUI（MiniMax H3）适配器，httpx 可注入 mock
        character_files.py  角色产物扫描（outputs/characters/{cid}/ -> URL）
        concept_files.py    概念图产物扫描（outputs/concepts/{cid}/ -> URL）
        workflows/   ComfyUI 工作流模板示例
        main.py      FastAPI 应用与全部 /api 端点（含角色工坊）
      run.ps1        启动脚本
      requirements.txt
      data/          运行期生成（SQLite + 任务 spec 临时文件）

## 后台任务

- shot 任务：POST /api/shots/{sid}/generate。后台线程执行

      python -m pipeline.cli gen-shot --spec <tmp.json> --out outputs/episodes/{eid}/shots/{sid}.mp4 --mock

  （cwd = 仓库根；mock 模式追加 --mock）成功后写 ShotVersion、回填 Shot.selected_version_id，
  Job 状态 done；失败则 Job.failed + error，镜头 failed。
- render 任务：POST /api/episodes/{eid}/render。后台线程执行

      python -m pipeline.cli render --episode contract/demo_episode.json --workdir outputs/episodes/{eid} --out outputs/episodes/{eid}/final.mp4

  stdout 中 PROGRESS <0-100> <message> 行会实时更新 Job.progress / Episode.progress；
  成功后 Episode.render_url = /media/episodes/{eid}/final.mp4。
- CLI 不存在或报错：任务标记 failed 并记录 error，不会让 API 进程崩溃。
- 费用：mock 模式 0；comfyui 模式 = settings.h3_price_per_second * 实际生成时长（CLI 返回的
  duration，缺省用镜头计划时长）。费用结构即使为 0 也会写入 Job/ShotVersion 并在 /costs 展示。
- demo 无需 Redis/Celery，任务为进程内守护线程。服务启动时会把上一个进程遗留的
  queued/running 任务标记 failed（error 注明「服务重启，任务中断，可点重试」），
  避免前端出现永远转圈的幽灵任务；用户可在任务中心点重试重新入队。

## 角色工坊（Character Studio，contract/CHARACTER.md）

Character：{id,project_id,asset_id,name,title,persona,voice,accent_color,style,spec,
portrait_url,sheet_url,turntable_url,expressions,status,created_at}

端点（demo 无鉴权）：

- GET   /api/projects/{pid}/characters             角色列表（含懒同步磁盘产物）
- POST  /api/projects/{pid}/characters             创建角色；兼容扁平对象与 {spec:{...}} 包裹
- GET   /api/characters/{cid}                      角色详情
- PATCH /api/characters/{cid}                      {name?,title?,persona?,voice?,accent_color?,spec?}
- POST  /api/characters/{cid}/generate             {styles?:[]} -> Job(type=character)
- GET   /api/characters/{cid}/files                {portrait,sheet,turntable,expressions,meta}

约定：

- 扁平创建体示例：{name,title,persona,voice,accent_color,style:{primary,variants},appearance:{...}}，
  id 由后端生成（char_xxx）；{spec:{id,...}} 包裹时沿用 spec.id。
- 创建角色时同步在 assets 表生成 type=character 的资产（挂到项目第 1 集），
  其 images 指向 /media/characters/{cid}/portrait.png；PATCH 角色会同步资产字段。
- style.primary 预设：3d_toon / 3d_cel / 2d_cel / 2d_paint / anime / guoman；
  accent_color 必须 #RRGGBB。
- generate 任务：后台线程执行

      python -m studio3d.cli create --spec <tmp.json> --outdir outputs/characters/{cid} --styles 3d_toon,2d_cel

  （cwd = 仓库根，stdout 最后一行 JSON）。产物约定：
  portrait.png / sheet.png / turntable.mp4 / expressions/{neutral,happy,surprised,thinking}.png / meta.json。
  成功后回填 portrait_url 等字段、expressions、status=ready，并同步 Asset.images；失败 exit!=0 或
  未产出 portrait.png 时 Job.failed + error，角色 status=failed，不拖垮 API 进程。
- 产物经 /media 静态访问：/media/characters/{cid}/portrait.png 等。
- GET .../files 与 GET 角色详情都会懒扫描 outputs/characters/{cid}/，即使角色是外部 CLI 直接生成、
  未经后端任务，也会自动回填 URL/status。状态口径：只要 portrait.png 存在即 status=ready；
  sheet/turntable 可缺失（前端显示占位），生成中的角色保持 generating。
- jobs 表新增可选 character_id（启动时对老库自动 ALTER TABLE jobs ADD COLUMN character_id）。

## AI 概念图 / H3 动画（contract/CONCEPT.md）

端点：

- POST /api/characters/{cid}/concept/generate
  body {modes?:["portrait","three_view","expressions","scene"], style?:anime|illustrious|guoman,
        prompt_extra?, seed?} -> Job(type=concept)
- GET  /api/characters/{cid}/concepts -> {portrait, three_view:{front,side,three_quarter},
        expressions:{neutral,happy,surprised,thinking}, sheet, meta}
- POST /api/characters/{cid}/portrait/from-concept {path} -> Character
- POST /api/characters/{cid}/h3-animate {image?, prompt?} -> Job(type=h3)

后台任务：

- concept 任务按 modes 逐个调用（cwd = 仓库根，ComfyUI 串行锁内）：

      python -m imagegen.cli generate --spec <tmp spec> --outdir outputs/concepts/{cid} --mode <mode>         [--style anime] [--prompt-extra "..."] [--seed 42]

  产物回填 Character.concept_portrait_url / concept_sheet_url / concept_three_view /
  concept_expressions，/media 访问路径 /media/concepts/{cid}/...；GET .../concepts 会懒扫描目录，
  外部直接生成的概念图也会被识别。
- portrait/from-concept：把 path（/media/... URL 或 outputs 下相对/绝对路径）复制为
  outputs/characters/{cid}/portrait_from_concept.png（如概念图旁有 cutout/ 一并同步），
  更新 Character.portrait_url/status=ready 与关联 Asset.images；角色卡/资产库随即使用概念图立绘。
- h3 任务调用：

      python integrations/comfyui/run_h3.py --image <path> --prompt "..."         --out outputs/h3/{cid}_h3.mp4 --width 768 --height 768 --length 124

  ComfyUI 串行锁内执行，子进程超时 20 分钟（>= 契约 15 分钟）；成功后回填
  Character.h3_video_url 与 h3_portrait_url（ffmpeg 抽中段帧到 outputs/characters/{cid}/h3_portrait.png，
  run_h3.py 自带 _fmid.png 侧车帧也会被懒扫描识别）。
- CLI 未就绪 / 失败时 Job.failed + error（含 stderr 摘要），不拖垮 API 进程。

## 成片导出（contract/EXPORT.md：剪映草稿）

- POST /api/episodes/{eid}/export {type:"capcut"} -> Job(type=export)
- 后台按 contract/EXPORT.md 生成 manifest：视频优先用分镜片段铺时间线（workdir/clips/{sid}.mp4 ->
  outputs/episodes/{eid}/shots/{sid}.mp4，剪映里可逐镜编辑；缺失时回退单段 final.mp4）；音频优先
  workdir/audio/narration.wav；字幕只收对白/反应（过滤花字、片头片尾等包装文本），按镜头时长累计，调用：

      python -m pipeline.capcut export --manifest <tmp manifest> --outdir outputs/exports         --name 造片局_EP001 --copy-media

- 成功后 Job.output_url 为草稿目录（仓库相对路径，如 outputs/exports/造片局_EP001），
  message 同时给出路径；草稿内含 draft_content.json / draft_meta_info.json / subtitles.srt / README.txt。
- 失败时 Job.error 保留 CLI 输出（如缺依赖、素材缺失），任务中心可读。

## 静态托管

- /media -> 仓库 outputs/（生成片段、成片、角色素材、概念图、导出草稿、H3 视频）
- frontend/dist 存在时 / 挂载该目录并做 SPA fallback（未命中路径回落 index.html）
- 前端尚未构建时 / 返回一个后端状态说明页

## 环境变量

- ZAOPIANJU_DATA_DIR      数据目录（默认 backend/data）
- ZAOPIANJU_DB            SQLite 文件路径
- ZAOPIANJU_OUTPUTS_DIR   产物目录（默认 outputs）
- ZAOPIANJU_FRONTEND_DIST 前端构建产物目录（默认 frontend/dist）
- ZAOPIANJU_JOBS_AUTORUN  设为 0 时任务只入队不执行（测试用）

## 测试

在仓库根目录执行：

    python -m pytest tests -q

测试使用临时数据目录并关闭自动任务（ZAOPIANJU_JOBS_AUTORUN=0），
通过注入假的 pipeline / studio3d / imagegen / run_h3 / capcut CLI 验证各类任务成功与失败路径，
不污染开发库、不真连 ComfyUI / Blender。覆盖：contract/API.md 全部端点、
角色工坊 CRUD/资产联动/files/生成任务（tests/test_characters.py）、
概念图/H3/剪映导出（tests/test_concept.py）、ComfyUI 适配器 mock。

## ComfyUI 适配器

backend/app/comfyui.py 实现标准流程：
POST {comfyui_url}/prompt -> 轮询 GET /history/{prompt_id} -> GET /view 下载视频字节。
支持 {{key}} 占位符工作流模板（build_workflow/load_workflow_template），
可注入 httpx.Client，单元测试用 httpx.MockTransport，不需要真实服务器。

shot 任务会把 /api/settings 写入 shot spec 的 settings 字段（comfyui_url / workflow_template /
resolution / fps 等，pipeline CLI 不连数据库），mock 模式下 pipeline 忽略该字段。
backend/app/workflows/h3_roundtable.json 是自带示例工作流模板，真机使用时替换为本机
ComfyUI 导出的 API 格式 H3 工作流即可。demo 默认 generation_mode=mock，不实际调用 H3；
切到 comfyui 但无服务时任务会 failed 并记录连接错误，不影响 API 进程。

## 已知限制（demo）

- mock 画面为 ffmpeg lavfi 绘制的示意片段，不是真实素材；真实生成需切 comfyui 并配置本机
  ComfyUI 地址 + 替换 backend/app/workflows/h3_roundtable.json。
- render 的最终合成由 pipeline 负责：优先复用 workdir/clips 缓存，其次复用平台生成台产物
  outputs/episodes/{eid}/shots/{sid}.mp4，最后才 mock 生成（pipeline 设计，兼顾复现与耗时）；
  后端只负责触发任务、回写 Job/Episode 进度与 render_url，不直接读 DB ShotVersion 视频。
- 任务为进程内守护线程；同一 episode 的 render 应串行触发（后端会自动复用同集活跃任务）。
  服务重启会把遗留 queued/running 任务标记 failed，用户可 retry。
- 若前端构建产物 frontend/dist 在服务启动时不存在，需要重启后端才会挂载 /。
- 角色画面由 studio3d（Blender 程序化建模 + 卡通渲染，外部模块）生成；后端只负责任务编排与
  产物回填。studio3d 未就绪 / Blender 不可用时，角色任务会 failed 并记录 error，API 与前端不受影响。
- 概念图依赖 imagegen + 本机 ComfyUI + SDXL 动漫 checkpoint（animagine-xl-4.0）；checkpoint 损坏/
  未下载完时 imagegen 会报错，后端 Job.failed 记录 ComfyUI execution_error，不影响 API。
- 剪映导出依赖 pipeline.capcut（pyJianYingDraft）。若 CLI 的 --copy-media 有缺陷（例如先复制素材
  后被 create_draft 重建目录清掉），后端 Job 会 failed 并保留可读 error，等 CLI 修复后重试即可。
