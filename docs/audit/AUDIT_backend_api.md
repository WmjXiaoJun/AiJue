# 后端 API 层审查（第三轮 · 增量）

- 审查人：audit-backend-api（子智能体）；范围：`backend/app/main.py`、`backend/app/routers/*.py`、`backend/app/{jobs,db,config,schemas,models,llm,comfyui,script_parser,seed,character_files,concept_files}.py`
- **基线声明（时间 2026-11-06 23:25–23:40）**：`backend/app/main.py` 的 mtime 在审查期间漂移 **23:27:55 → 23:28:59**（外部 WIP，两次改动）；用例基线 `python -m pytest tests -q` = **4 红 / 311 绿 / 57 跳过**；本轮所有 `file:line` 以 23:29 之后的版本为准。
- 基线：工作区未被本审查改动（只读）。**注意基线在漂移**：本轮观测到 `backend/app/main.py` 在 23:27:55 与 23:28:59 两次被外部改动（WIP），下面所有 `file:line` 以 23:29 之后的版本为准：
  - `main.py` 2700 行 / sha256 前 16 位 `715C66E07DC5EAA6`
  - `jobs.py` `15EE703275A43B5A`、`routers/v6_agent.py` `04B7E54A0FB9A985`、`routers/v5_series.py` `215DF5261C4FB386`、`routers/muse.py` `C35B66E9F1EE7AAF`、`routers/longform.py` `91FDF265EE462C88`
- 本轮定位：**前两轮（`REVIEW_BACKEND.md` / `REVIEW_BACKEND_ROUND2.md`）之后的新缺陷** + 逐条回归核对。前两轮已列过的问题不重复上报，只给结论与现证据（见「前两轮回归核对表」）。
- 方法：全文/分段读码 + 自写探针（`docs/audit/_tools/api_audit_probe.py`，只读、`ZAOPIANJU_JOBS_AUTORUN=0`、不写库不落产物）+ `python -m pytest tests -q`。
- **只读边界声明**：本审查未改 `backend/`、`contract/` 下任何源码/合同（`git status` 里那些 `M` 全是本审查开始前就存在的 WIP；本轮只新增 `docs/audit/AUDIT_backend_api.md` 与 `docs/audit/_tools/api_audit_probe.py`）。但有一处不可避免的写入：`import backend.app.main` 会在导入期跑 `init_db()` + `_recover_stale_jobs()`（见 P1-3），所以**任何** TestClient 冒烟都会改写 `backend/data/zaopianju.db`（本轮实测 mtime 23:27:59）。
- 验证命令（可直接复现）：

```bash
# 1) 路由枚举 / Mount 顺序 / 路径拼接探针（不改源码）
python docs/audit/_tools/api_audit_probe.py routes paths
# 2) TestClient 探针（含外联 metaso.cn 一次；加 --no-net 跳过）
python docs/audit/_tools/api_audit_probe.py http
# 3) 后端用例基线
python -m pytest tests -q -rf
# 4) 冒烟（历史上曾因 WIP 断在 NameError，见「概览」）
python -c "import backend.app.main as m; print(len(m.app.routes))"
```

## 概览

一句话结论：**前两轮的 26 条里有 16 条真修好、6 条只修了一半、3 条仍未修、1 条被修成反向误报**；本轮新增 13 条缺陷，其中 3 条 P1（反斜杠路径穿越读任意 `.json`、非数字参数 500、导入期把在跑任务判 failed），其余 P2 集中在"错误体形状不统一 / 绝对路径外泄 / 记账字段口径错 / 后台线程与子进程收尾"。
（定级经 Lead 复核调整：原 P1-3 `video_fallback` 降 **P2-10**（无消费方，仅记账口径错）；原 P2-6 导入期副作用升 **P1-3**（`run.ps1` 默认 `--reload`，保存一次即触发）。）

| 类别 | 计数 |
|---|---|
| 本轮新增 P0 | 0 |
| 本轮新增 P1 | 3 |
| 本轮新增 P2 | 10 |
| 前两轮回归：已修 | 16 |
| 前两轮回归：部分修（残留见新条目） | 6 |
| 前两轮回归：仍未修 | 3 |
| 前两轮回归：修出反向问题 | 1 |

**过程中观测到一个瞬时 P0（已消失，留证）**：23:27:55 那版 `main.py` 末尾新增了 `@app.post("/api/settings/metaso-test")`，函数签名用了 `Body` 但 `from fastapi import ...` 没有导入 `Body` → `import backend.app.main` 直接 `NameError: name 'Body' is not defined`（`main.py:2676`），**整个后端起不来**；且该路由定义在 `app.mount("/", SPAStaticFiles...)` 之后，即使补上导入也会被 SPA 挂载遮蔽。23:28:59 的版本把 `Body` 加进导入行（现 `main.py:22`）并把路由移到 mount 之前（现 `main.py:2617`），实测 `POST /api/settings/metaso-test -> 200`。现网可达，但见 P2-4 的顺序脆弱性。

## P1 重要（功能不可用 / 契约破 / 数据可被越权读取）

### [P1] P1-1 三处 `{id}.json` 直接拼接：Windows 反斜杠穿越可读任意 JSON 文件

位置：
- `backend/app/routers/v6_agent.py:278-282`（`_series_entry`），被 `GET /api/v6/agent/series`、`GET /api/v6/agent/season/{sid}/status`、`POST /api/v6/agent/season/{sid}/run` 调用
- `backend/app/routers/v5_series.py:72-76`（`_load`），被 `GET /api/v5/series/{sid}`、`POST /api/v5/series/{sid}/export` 调用
- `backend/app/main.py:352-357`（`_load_format`），`format_id` 来自 `ProjectCreate.format_id`（`schemas.py:31` 只声明 `str`，无 pattern）与 `ProjectUpdate.format_id`（`main.py:601` 仅用"能否解析成功"当校验）

问题：三处都用 `SERIES_DIR / f"{sid}.json"`、`config.FORMATS_DIR / f"{format_id}.json"` 拼路径，**只挡了 `/` 没挡 `\`**。Starlette 的路由参数正则是 `[^/]+`，`%5C` 解码后就是 `\`，在 Windows 上是合法分隔符 → 逃出 `contract/v5/series`、`contract/formats`。

证据（探针实测，`python docs/audit/_tools/api_audit_probe.py http`）：

```text
[http] Windows 反斜杠穿越 GET /api/v6/agent/season/<..\..\..\contract\demo_episode>/status
       -> 200 {"ok":true,"series":{"id":"demo_episode", ... "run_dir":"D:\\...\\outputs\\agent_runs"},"episodes":[]}
[paths] contract/v5/series/{sid}.json 拼接（sid 无正则校验）
       -> exists=True D:\BaiduSyncdisk\wmj_work\XJ\AiJue\contract\demo_episode.json
[paths] contract/formats/{format_id}.json
       '..\\..\\contract\\demo_episode' -> exists=True
       '..\\..\\contract\\v5\\series\\undercity_arc' -> exists=True
```

代码：

```python
# v6_agent.py:278-282
def _series_entry(sid: str) -> dict:
    path = SERIES_DIR / f"{sid}.json"
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"系列不存在: {sid}")
    data = json.loads(path.read_text(encoding="utf-8"))

# v5_series.py:72-76
def _load(sid: str) -> Dict[str, Any]:
    path = SERIES_DIR / f"{sid}.json"

# main.py:352-357
def _load_format(format_id: str) -> Dict[str, Any]:
    path = config.FORMATS_DIR / f"{format_id}.json"
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}
```

影响：无鉴权即可按 `..\` 读仓库内任意 `.json`（`contract/`、`outputs/`、`backend/data/*.env` 不是 json 但 `backend/app/../*.json` 可以），`season/status` 把读到的 JSON 的 `id/label/description/style/members` 回吐给调用方；`format_id` 还会被写进 `projects.format_id` 并长期驻留（`create_project`/`patch_project` 都只校验"能解析"）。仅限 `.json` + Windows 分隔符，故定 P1 不定 P0。

建议修法（diff 级思路，不改）：

```python
# 三处统一：先正则白名单，再 containment 双保险
ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,60}$")
def _contract_json(base: Path, ident: str, label: str) -> Path:
    if not ID_RE.match(ident or ""):
        raise HTTPException(status_code=400, detail=f"非法 {label}: {ident}")
    path = (base / f"{ident}.json")
    if path.resolve().parent != base.resolve():   # 挡 Windows 反斜杠/其它分隔符
        raise HTTPException(status_code=400, detail=f"非法 {label}: {ident}")
    return path
```
`_load_format` 同样加 `ID_RE`；`ProjectCreate.format_id` 改 `Field(pattern=r"^[a-z][a-z0-9_]{1,60}$")`。

---

### [P1] P1-2 非数字 `duration` → 500（longform）/ 200 + 后台立刻 failed（v6 agent）

位置：
- `backend/app/routers/longform.py:80`（`POST /api/v5/longform/run`）
- `backend/app/routers/v6_agent.py:187`、`:188`（`POST /api/v6/agent/run` 的 worker 内）

问题：`float()`/`int()` 强转请求字段没有兜住。`longform` 直接在请求线程里抛 `ValueError` → FastAPI 500；`v6_agent` 在**提交时不校验**，把原始字符串塞进后台线程，`200` 返回一个 job，紧接着 job 变 failed，错误体是 Python 内部消息。

证据（探针实测）：

```text
[http] POST /api/v5/longform/run duration='abc'（期望 400，实测 500 即为缺陷）
       -> 500 Internal Server Error
[http] POST /api/v6/agent/run duration='abc'
       -> 200 {"ok":true,"job_id":"job_639d099cbc"}
```

代码：

```python
# longform.py:77-82
def run_longform(payload: dict = Body(default=None)) -> Dict[str, Any]:
    payload = dict(payload or {})
    duration = float(payload.get("duration") or 300.0)      # 'abc' -> ValueError -> 500
    if duration < 30:
        raise HTTPException(status_code=400, detail="duration 至少 30 秒")

# v6_agent.py:186-192（worker 线程内，接口已经在 200 之后）
run = run_template(
    str(payload["template"]), duration=float(payload.get("duration") or 40),
    seed=int(payload.get("seed") or 20261101), ...)
```

影响：前端传 `"60"`（字符串）能过，传 `"1:00"`/`""` 就 500 或"起了个死 job"；`job["error"]` 是 `ValueError: could not convert string to float: 'abc'`，前端只能展示英文异常串。同模块 `muse.py:204-206` 已有 `try/except -> 400` 的正确写法（前轮 R2-12 修过），此处是漏网。

建议修法：

```python
# longform
try:
    duration = float(payload.get("duration") or 300.0)
except (TypeError, ValueError) as exc:
    raise HTTPException(status_code=400, detail="duration 必须是数字") from exc
# v6_agent.start_run：提交前就转换并回填，别让非法值进线程
try:
    payload["duration"] = float(payload.get("duration") or 40)
    payload["seed"] = int(payload.get("seed") or 20261101)
except (TypeError, ValueError) as exc:
    raise HTTPException(status_code=400, detail="duration/seed 必须是数字") from exc
```

---

### [P1] P1-3 导入期副作用：`--reload` 下保存一次文件，就把正在跑的 GPU 任务判成 failed

位置：`main.py:153`（`init_db()`）、`main.py:206`（`_recover_stale_jobs()`）、`character_audition.py:900`、`character_dialogue.py:854`、`character_promo.py:809`、`cinema3d.py:853`、`pipeline_ui.py:534`、`pipeline_ui.py:1300`、`publish_batches.py:372`

问题：这 9 处都在**模块导入期**执行，其中 3 处会把 `queued/running` 一律改写成 `failed`。而本仓库的启动脚本**默认带热重载**，所以"改一行代码 → reload → 上一轮进程正在跑的渲染被标失败"是必然路径，不是理论风险。

证据：

```powershell
# backend/run.ps1:25,27 —— 默认就开 --reload
if (-not $NoReload) { $uvicornArgs += "--reload" }
& $python @uvicornArgs
```
```python
# main.py:156-172, 206
def _recover_stale_jobs() -> None:
    with SessionLocal() as db:
        stale = db.query(models.Job).filter(models.Job.status.in_(("queued","running"))).all()
        for job in stale:
            job.status = "failed"           # 进程内线程一退出就没了，故判 failed
            job.error = "服务重启，任务中断，可点重试"
...
_recover_stale_jobs()                       # 模块导入即执行

# publish_batches.py:356-358 / cinema3d.py:844-846 / pipeline_ui.py:534 同构
if data.get("status") in ("queued", "running"):
    data["status"] = "failed"; data["error"] = "后端服务重启，批量导出中断（可重新发起）"
```

影响：`uvicorn --reload` 每次重载都会重新 import → 上一次仍在跑的 `shot`/`render`/`publish`/`concept`/`h3` job 被标 failed（DB 行 + shot/集状态一起改），GPU 子进程还活着（`jobs.run_cli` 的线程随旧进程消失，见 P2-5 同一根因）；用户看到"任务中断，可点重试"，而机器上仍在渲。此外 `pytest`/`python -c "import backend.app.main"` 的任何一次导入也一样会改写**开发机正在跑的**任务状态——`tests/` 大量用 TestClient 导入 app，等于测试会污染真实运行态（本轮实测：我的冒烟把 `backend/data/zaopianju.db` 写于 23:27:59）。多 worker（`--workers 2`/gunicorn）同理会互相误杀。

建议修法：把"启动恢复"从 import 移到 FastAPI `lifespan`/`startup`，并只在"确认自己是唯一实例"时执行：

```python
# main.py
from contextlib import asynccontextmanager
@asynccontextmanager
async def lifespan(app):
    _recover_stale_jobs()      # 这里跑，而不是模块顶层
    yield
app = FastAPI(..., lifespan=lifespan)
```
其余 5 个模块的 `_load_persisted_*()` 同样下沉到各自的 `startup`；再给 job/batch 记录加 `owner_pid`+`boot_id`，只清理"自己上一个 boot"的记录；`--reload` 场景可加环境变量 `SKIP_STALE_RECOVERY=1`（`run.ps1` 里检测 `--reload` 时自动置位）。

## P2 一般（健壮性 / 契约 / 可维护性）

### [P2] P2-1 `HTTPException.detail` 30 处是对象、其余是字符串：前端错误渲染会 `[object Object]`

位置（对象型，节选）：`v5_studio.py:594,737`、`pipeline_ui.py:608,615,623,665,1080,1088,1141`、`character_audition.py:708,721,734,743`、`character_dialogue.py:536,544,550,557`、`character_promo.py:636,648,659,666`、`v5_create.py:996`、`v5_catalog.py:228`、`v5_series.py:221`、`muse.py:227,333`、`v5_create.py:118`（`_err` 工厂，19 处调用随之）
字符串型（对照）：`main.py` 全部 400/404（如 `main.py:2243,2250,2278`）、`v6_agent.py:67,161,257`。

证据：

```python
# v5_studio.py:594-595 —— 对象
raise HTTPException(status_code=400, detail={
    "message": "角色修改校验失败（未写入）", "errors": ok.get("errors") or []})
# v5_series.py:221 —— 对象，而且是 400 里塞 dict
raise HTTPException(status_code=400, detail={"message": "有集数还没有成片", "missing": missing})
# main.py:2250 —— 同一批 API 的字符串
raise HTTPException(status_code=400, detail="至少选择一个发布平台")
```

影响：`ProjectOut`/`JobOut` 之外的错误体没有 schema 约束，前端 `detail` 直出就变 `[object Object]`；同一前端组件在 `/api/v5/...`、`/api/pipeline/...` 与 `/api/episodes/...` 之间必须写两套解析。这也是回归测试 `tests/test_v5_2_acceptance.py::test_frontend_api_live` 的主要风险面（该用例本轮因媒体 404 挂，不是这条引起）。

建议修法：定义 `class ApiErrorDetail(BaseModel): message: str; reason: str|None; errors: list[str] = []` 与 `api_error(status, message, **extra)`；统一 `detail={"message":...}`，并在 `main.py` 加 `@app.exception_handler(HTTPException)` 把字符串 detail 也包成 `{message: ...}`（保持旧字段兼容）。

### [P2] P2-2 绝对路径外泄到前端（`workflow_file` / `run_dir` / `out` / `path`）

位置：`v6_agent.py:142`（`workflow_file`）、`v6_agent.py:336`（`run_dir`，`_series_entry` 返回）、`muse.py:313`（`out`）、`muse.py:359`（响应 `out`）、`v5_create.py`/`v5_catalog.py` 的 `"path": str(path)`（`v5_catalog.py:254`）

证据（探针实测）：

```text
[http] 绝对路径泄漏 GET /api/v6/agent/overview
       workflow_file = D:\BaiduSyncdisk\wmj_work\XJ\AiJue\contract\v6\workflows\classics.json
```
```python
# v6_agent.py:142
"workflow_file": str(WORKFLOW_DIR / f"{tid}.json"),
# v6_agent.py:336
"run_dir": str(RUN_ROOT),
# muse.py:311-313
job["result"] = {... "out": str(path), "out_url": _media_url(path)}
```

影响：把部署机用户名、盘符、仓库绝对路径送给任意浏览器；同一字段在 `/media/...` URL 与本地绝对路径之间摇摆（前端若把 `workflow_file` 当"打开文件"参数会拿到 Windows 盘符）。也给"路径穿越"提供了现成的地图。

建议修法：对外只输出 `config.media_url()` / 仓库相对 posix 路径（`path.resolve().relative_to(config.REPO_ROOT).as_posix()`），绝对路径只进日志。

### [P2] P2-3 SSE 在 async 生成器里跑同步 SQLAlchemy，异常被吞成 `{}`

位置：`main.py:2534-2564`

证据：

```python
@router.get("/events")
async def events(episode_id: Optional[str] = None):
    async def event_stream():
        yield "retry: 2000\n\n"
        for _ in range(150):
            payload_json = "{}"
            try:
                with SessionLocal() as db:          # 同步 IO 在事件循环里
                    rows = query.order_by(...).limit(20).all()
                    payload_json = json.dumps({...})
            except Exception:  # noqa: BLE001 - SSE 断流不 crash
                logger.exception("SSE 查询失败")
            yield f"data: {payload_json}\n\n"
            await asyncio.sleep(2)
```

影响：每 2s 一次同步 SQLite 查询会卡住整个事件循环（所有并发请求一起排队），最长挂 150×2s=5min；DB 一旦坏掉，客户端收到的是**空对象流**而不是错误事件，前端无法区分"没任务"和"后端挂了"。

建议修法：用 `await run_in_threadpool(...)` 或 `asyncio.to_thread` 包 DB 段；异常时 `yield f"event: error\ndata: {json.dumps(...)}\n\n"` 并 `break`；顺便把 `range(150)` 换成"客户端断开或超时"检测（`request.is_disconnected()`）。

### [P2] P2-4 WIP 端点 `/api/settings/metaso-test` 的注册顺序无结构保障（且无鉴权外联）

位置：`main.py:2617-2618`（路由）vs `main.py:2644`（`app.mount("/media")`）、`main.py:2691`（`app.mount("/")`）

证据：当前顺序正确（Mount 在最后，探针实测 `Mount 注册位置: [(158,'Mount','/media'), (159,'Mount','')]`、`挂在 '/' 之后注册的 APIRoute 数=0`），但这个端点**写在 2600 行附近的 mount 区之前、文件末尾的 SPA 分支之后**，是"靠位置活着"的：

```python
# main.py:2617-2618
@app.post("/api/settings/metaso-test", summary="测试 Metaso H3 Key（/v1/models）")
def metaso_key_test(payload: dict = Body(default=None)) -> dict:
    ...
    req = urllib.request.Request(f"{base}/v1/models", headers={"Authorization": f"Bearer {key}"})
```

影响：任何"在文件末尾追加路由"的改动都会静默失效（23:27:55 那版就是这样，返回 SPA 的 index.html/404 而不是 401/200）；且该端点会把**已保存的** `metaso_key()` 发给 `metaso.cn`，任何人 `POST {}` 即可触发（测试也顺手验证了这条：探针不带 key 也返回了真实模型列表）。

建议修法：所有 `@app.*` 路由统一挪到 mount 之前（或改成 `router` 用 `include_router`）；加一条启动自检：遍历 `app.routes`，若 `Mount("/")` 之后仍有 `APIRoute` 就 `raise RuntimeError`；该端点要求显式传 key 或加开关。

### [P2] P2-5 `run_cli` 超时只杀直接子进程，Windows 上留下孙进程

位置：`jobs.py:107-142`

证据：

```python
proc = subprocess.Popen(cmd, ..., stdout=subprocess.PIPE, stderr=subprocess.PIPE, ...)
...
except subprocess.TimeoutExpired:
    timed_out = True
    returncode = -9
    try:
        proc.kill()            # 只杀 python -m pipeline；ffmpeg/comfy 子进程存活
    except Exception:  # noqa: BLE001
        pass
for reader in readers:
    reader.join(timeout=5)     # 孙进程还握着管道 -> 5s 后放弃，流不关
```

影响：超时的 `pipeline.gen-shot`/`render`/`publish.cli` 会留下仍在吃 GPU/网络的 ffmpeg 子进程；`_pump` 线程因管道仍被孙进程持有而 `join(5)` 超时返回，`stderr_text` 截断，任务被判 failed 但机器仍在跑上一个任务。同仓库 `pipeline_ui.py:455-457`、`character_audition.py:403-413`、`cinema3d.py:592-600` 都已用 `taskkill /F /T /PID` 树杀，只有 `jobs.run_cli` 漏了。

建议修法：

```python
def _kill_tree(proc):
    if proc.poll() is not None: return
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                       capture_output=True, timeout=30)
    else:
        proc.kill()
```
在 `TimeoutExpired` 与 `finally` 里都用它，并在返回前显式 `proc.stdout.close(); proc.stderr.close()`。

### [P2] P2-6 `retry_job` 对 running 静默返回 200，接口忽略返回值 → 用户以为重试成功

位置：`jobs.py:1951-1954`（`if job.status == "running": return job, False`）+ `main.py:1601-1610`

证据：

```python
# jobs.py
def retry_job(db, job: models.Job):
    if job.status == "running":
        return job, False          # 什么都没做
# main.py:1606-1610
if job.type == "publish": raise HTTPException(...)
job, _created = jobs.retry_job(db, job)   # _created 被丢弃
return job                                 # 200 + 仍然是 running 的 job
```

影响：前轮 R1-7 说"gen-shot 挂住后 retry 还拒绝 running"，现在的形态是**不拒绝也不提示**：前端点"重试"没有任何报错、job 状态不变，用户只能反复点。配合 P2-5（无取消端点）形成死锁：挂住的 shot job 既不能取消也不能重试。

建议修法：

```python
if job.status == "running":
    raise HTTPException(status_code=409, detail={"message": "任务正在运行，不能重试",
                                                 "reason": "job_running", "job_id": job.id})
```
并给 `POST /jobs/{jid}/cancel`（`jobs.cancel_job` 置 `canceled` + 杀对应 pid；`run_shot_job`/`run_render_job` 在出结果前复检状态）。

### [P2] P2-7 `bool()` 与 `_as_bool()` 混用：`"false"` 字符串被当成 True

位置：`v6_agent.py:189,191`（`start_run` → 原样进 `_run_worker`）、`v6_agent.py:541`（`agent_pose` 的 `enabled`）

证据：

```python
# 同模块 54-62 行专门定义了严格布尔，并写明 "避免 bool('false')==True"
def _as_bool(value, default: bool = False) -> bool: ...
# 但 189/191 行（start_run 路径）没用它
force=bool(payload.get("force")), ...
dry_run=bool(payload.get("dry_run")), ...
# 541 行同理
return agent_art.set_pose(None, {"character": ..., "pose": ...,
                                 "enabled": bool(payload.get("enabled", True))})
```

影响：`{"dry_run": "false"}` → `True`（用户以为要真渲，结果只出计划）；`{"enabled": "false"}` → 姿态资产**被开启**而不是关闭（与 `_as_bool` 的存在意义正相反）。`season_run`（464 行）已用 `_as_bool`，两条路径对同一字段语义不同 → 前端无法预测。

建议修法：189/191/541 全部换 `_as_bool(...)`；`start_run` 里把规范化后的值回填 `payload` 再交给线程。

### [P2] P2-8 longform 子进程无 timeout、异常路径不 kill、`_jobs` 无上限

位置：`longform.py:35-37`（全局字典）、`longform.py:48-73`（worker）、`longform.py:91-108`（提交）

证据：

```python
_jobs: Dict[str, Dict[str, Any]] = {}
_current: Optional[str] = None

def _worker(job_id: str, args: List[str]) -> None:
    job = _jobs[job_id]
    try:
        proc = subprocess.Popen(cmd, cwd=str(REPO), stdout=subprocess.PIPE, ...)  # 无 timeout
        for line in proc.stdout or []:
            job["log"].append(line.rstrip())        # 无上限
        code = proc.wait()
    except BaseException as exc:                    # 捕获后 proc 不 kill、不 close
        job["status"] = "failed"; job["error"] = f"{type(exc).__name__}: {exc}"
```

影响：`scripts/rebuild_all_5min.py` 跑 5 分钟长版（真实是小时级）没有超时上限；一旦 `except` 命中（读流异常/编码错误/`SystemExit`），子进程继续跑并占 GPU，而 `_current` 已被清空 → 允许再起一个，两个重建进程同时写 `outputs/`。`_jobs` 永不清空（每项还带完整 log 列表），长跑进程单调增长。`v6_agent._jobs`、`muse._vjobs` 同样无上限（前轮 R2-13 只覆盖了 `character_*`）。

建议修法：`subprocess.Popen(..., creationflags=CREATE_NO_WINDOW)` + 参照 `cinema3d._timeout_watchdog` 加看门狗；`finally` 里 `_kill_tree(proc)`；`_jobs` 与 log 都加 `keep_last(20)` / `log[-400:]` 裁剪；`except BaseException` 收窄成 `Exception`（`BaseException` 会吞掉 `KeyboardInterrupt`，进程 Ctrl-C 时反而看不到栈）。

### [P2] P2-9 跨模块 GPU 互斥仍是 check-then-act（同模块已双检）

位置：`character_promo.py:636-669`（检查 audition/dialogue）vs `character_promo.py:705-710`（占位）；`character_audition.py:700-738`（检查 promo/dialogue）vs `:790-796`；`character_dialogue.py:554-560` vs `:742-751`

证据：

```python
# promo：先在 _LOCK 里查别人，再出锁构造 40 行 promo 对象，最后第二个 _LOCK 只复查自己
with _LOCK:
    ...
    if audition_active: raise HTTPException(409, ...)     # 652-662
    if _ACTIVE_PROMO: ...                                  # 663-669
promo = {...}                                              # 671-704（无锁）
with _LOCK:
    running = _PROMOS.get(_ACTIVE_PROMO) or {}             # 705-710 只查自己
    _PROMOS[promo_id] = promo; _ACTIVE_PROMO = promo_id
```

影响：前轮 R2-5 提的"双击同一按钮"已修（同模块二次检查）；但**跨模块**仍可越过：promo 检查时 audition 还没占位 → audition 检查时 promo 还没占位 → 两个 GPU 任务齐跑（试镜 + 宣传片会同时抢同一张显卡/同一个 ComfyUI 队列）。窗口是 40 行构造代码 + 一次 `_persist` 写盘，实测毫秒级，双击不同入口即可。

建议修法：抽一个跨模块的 `GPU_LEASE`（进程内 `threading.Lock` 包住"检查+占位"，或复用 `jobs.GPU_LOCK`）：

```python
# character_gpu.py
GPU_LEASE = threading.RLock()
def reserve(kind: str, job_id: str) -> None:
    with GPU_LEASE:
        holder = _current_holder()      # 跨 audition/promo/dialogue/pipeline 统一登记
        if holder: raise HTTPException(409, {...})
        _set_holder(kind, job_id)
```

### [P2] P2-10 `video_fallback` 改成按文件名判定 → 每次多语种发布都被误标"回退到母版"（定级已按复核下调）

> 定级说明：实测 `grep -r video_fallback frontend/src`（*.ts/*.tsx）= **0 命中**，backend 侧只有 `main.py:2269/2273/2333` 写入、无读取方 → 不影响 UI/台账展示，属"记账字段口径错 + 死赋值"的数据完整性问题，故 P2。**保留此条**：它是本轮唯一"已回归"项（R2-P1-4 的修法把原生成本地化版本全判成回退）的证据。

位置：`backend/app/main.py:2262-2273`（取片）、`backend/app/main.py:2315-2317`（判定）、`backend/app/main.py:2333`（写进 `PublishRecord.meta`）

问题：前轮 R2-4 指出"flag 只标记 render_url 回退"，本轮的修法改成了 `Path(video_path).name == "final.mp4"` 的**文件名启发式**。但 `_publish_video_candidates`（`main.py:1838-1850`）给多语种版本的首选路径正是 `outputs/episodes/<eid>/localized/<lang>/final.mp4` —— 文件名同样是 `final.mp4` → **正常的英/西/日版本 100% 被判成 fallback**；而 `version=dynamic` 的 `final_dynamic.mp4` 反而恒为 False。同时 `2273` 行真正置位的 `video_fallback = True` 是**死赋值**，之后再没被读过（`fallback_flag` 在 2316 重新算）。

证据：

```python
# main.py:2262-2273
candidates = _publish_video_candidates(episode, lang, version)
if lang == "zh" and version == "final":
    video_path = resolve_episode_video(episode.id, episode.render_url)
else:
    video_path = next((candidate for candidate in candidates if candidate.is_file()), None)
    if video_path is None:
        video_path = resolve_episode_video(episode.id, episode.render_url)
        video_fallback = True          # 死赋值：后面没用
# main.py:2315-2317
fallback_flag = bool((lang != "zh" or version != "final")
                     and Path(video_path).name == "final.mp4")   # localized/en/final.mp4 也命中
# main.py:1841-1845（candidates 口径）
if lang and lang != "zh":
    candidates.append(base / "localized" / lang / "final.mp4")
if version == "dynamic":
    candidates.append(base / "final_dynamic.mp4")
```

影响：`meta.video_fallback` 是"这段是不是中文母版"的记账值，现在**恰好反了**：真正发英文版的记录被标 True，真正回退成母版的 `dynamic` 记录被标 False。当前无消费方（前端 0 命中），所以是**潜伏**缺陷：一旦后续把发布台账/审计报告接上该字段，就会读到反向信息。同时 `video_fallback` 死赋值说明 2262-2273 这条分支本来想写但没接上，容易在下次改动里被误认为"已经处理过"。

建议修法：不要用文件名猜，用"取了哪条候选"这一事实判定：

```python
picked = None
for cand in candidates:            # 保持顺序即优先级
    if cand.is_file():
        picked = cand
        break
if picked is None:
    picked = resolve_episode_video(episode.id, episode.render_url)
    fallback_flag = picked is not None            # 只有这里才算真回退
else:
    fallback_flag = (lang and lang != "zh" and picked.parts[-3] != "localized") or \
                    (version == "dynamic" and picked.name != "final_dynamic.mp4")
```

## 前两轮回归核对表

> 「已修」= 代码现状与建议修法一致且能给出新证据；「部分修」= 主路径修了但残留（残留都在本轮新条目里）；「仍未修」= 原样；「反向」= 修出了新问题。

| 前轮编号 | 结论 | 现证据（file:line） |
|---|---|---|
| R1-P0-1 `put_voice` 覆盖 custom-only 合同 | **已修** | `v5_studio.py:580-596`：先 `item_source`→`has_base` 判 partial/full，`validate_item` 通过后才 `write_json_atomic` |
| R1-P1-2 发布静默回退到中文母版 | **仍未修** | `main.py:2270-2273`：取不到 `localized/<lang>`/`final_dynamic.mp4` 就回退 `resolve_episode_video`，仅打 flag 不阻断 |
| R1-P1-3 publish 去重吞请求 + 幽灵记录 | **已修** | `main.py:2353-2358`：`if not _created: record.status="duplicate"`；`jobs.py:1682-1683` 返回 `(active, False)` |
| R1-P2-4 角色 `spec.id` 未校验逃逸 outputs | **已修** | `main.py:64` `CHAR_ID_RE`；`main.py:855-861`：`CHAR_ID_RE.match` + `".."/"/"/"\\"` 显式拦截 + 重名 409 |
| R1-P2-5 单并发占位竞态（muse/v6_agent） | **已修** | `muse.py:230-240`（锁内 `_current=job_id` 先占位）；`v6_agent.py:225-237`（锁内插入 `status=queued` 再起线程） |
| R1-P2-6 muse sync 无路径包含校验 | **已修** | `muse.py:81-90` `_safe_input_path()`：仅允许 `outputs/`、`tmp/` 下 |
| R1-P2-7 shot 无 timeout + 全站无取消 | **部分修** | `jobs.py:509-514` 已有 `GEN_SHOT_TIMEOUT`（默认 3600s）；`main.py` 全文 grep `cancel` = 0 命中 → 仍无取消端点（见 P2-6） |
| R1-P2-8 系列导出缓存不失效 + 无锁 | **已修** | `v5_series.py:208-213` `_EXPORT_LOCK.acquire(blocking=False)`；`:232-256` 逐集 size/mtime→sha1 比对 + `variant` 比对 |
| R1-P2-9 预检"多语种"假通过 | **已修** | `main.py:2096-2098`：`status=="ready" and video_url 非空` 才计入 `ready_rows` |
| R1-P2-10 季生产 dry_run 默认 + ok + bool 陷阱 | **部分修** | `v6_agent.py:464` 用 `_as_bool`、`:412` 输出 `planned`；但 `start_run` 的 `:189,:191` 仍 `bool()`（见 P2-7） |
| R1-P3-11 `backend/data/jobs/` 无上限 | **部分修** | `jobs.py:46-64` 有 `_prune_job_files(keep=300)`，但按**全局 mtime** 裁剪且只在写 spec 时触发（前轮 R2-9 已批评） |
| R1-P3-12 outputs 目录口径不一致 | **已修（backend）** | `v6_agent.py:75-77`、`muse.py:40-42`、`v5_series.py:23-26`、`main.py:1033` 全部改走 `config.OUTPUTS_DIR`；pipeline 层仍写死（R2-7，范围外） |
| R1-P3-13 `GET /api/v5/voice/{character}` 死路由 | **已修** | `v5_catalog.py:498-500` 注释说明只保留 `lock/preview` 两个 POST；该文件 `@router` 列表（`:81,148,161,187,208,426,449,503,528`）已无 `GET /voice/{character}` |
| R2-P0-1 `muse_sync` 缺 `global _current` | **已修** | `muse.py:200-201`（`def muse_sync(...)` 后紧跟 `global _current`） |
| R2-P0-2 `muse_video` 缺 `global _vcurrent` | **已修** | `muse.py:327-328` |
| R2-P0-3 muse video 越界写盘 | **已修** | `muse.py:340-344`：`re.sub(r"[^A-Za-z0-9_-]","",name)[:60]` + `VIDEO_ROOT.resolve() not in out.parents` 拦截 |
| R2-P1-4 `video_fallback` 抓错分支 | **反向（新缺陷，P2-10）** | `main.py:2315-2317` 改成文件名启发式 → 原生成本地化版本全判为回退；无消费方故定 P2，但保留作"修出反向"证据 |
| R2-P2-5 三兄弟 GPU 互斥 check-then-act | **部分修** | 同模块双检已加：`character_audition.py:790-796`、`character_promo.py:705-710`、`character_dialogue.py:742-751`；跨模块仍未闭（见 P2-9） |
| R2-P2-6 系列缓存全量 sha1 / 无锁 / 写死 outputs | **已修** | `v5_series.py:244-249` size+mtime 快路径，`:201-213` 锁，`:23-26` `config.OUTPUTS_DIR` |
| R2-P2-7 pipeline 层仍写 repo/outputs | **未核（范围外）** | `pipeline/` 不在本次审查范围；backend 侧已统一（见 R1-12） |
| R2-P3-8 季 run 把 dry_run 记成 ok | **已修** | `v6_agent.py:410-415`：`status = "failed" if failed else ("planned" if dry_run else "ok")`，且写入 `run.json` |
| R2-P3-9 prune/超时 env | **部分修** | `jobs.py:46-64` prune 存在；`jobs.py:511` `float(os.environ.get("GEN_SHOT_TIMEOUT","3600"))` 非法值 → 被 `:512-514 except` 变成"gen-shot 启动失败"（误导排查） |
| R2-P3-10 试镜合集发布包缓存不随源片失效 | **已修** | `character_showcase.py:222-226`：`_cache_matches(cached, title, vertical, vertical_mode, _reel_sig())` |
| R2-P3-11 pipeline_ui 内建优先与 custom 语义相反 | **已修** | `pipeline_ui.py:249`、`:268`：`(_CUSTOM_TEMPLATE_DIR, _TEMPLATE_CONTRACT_DIR)` 顺序，custom 在前 |
| R2-P3-12 muse `limit` 非数字 500 | **已修** | `muse.py:203-206`：`try: int(...) except: HTTPException(400, "limit 必须是整数 1-8")` |
| R2-P3-13 角色任务内存字典无上限 | **已修** | `character_audition.py:797-800`、`character_promo.py:711-714`、`character_dialogue.py:748-751` 均为"超 50 条按 created_ts 裁掉旧的非活跃项" |

## 已核对无问题的点（防误报）

1. **路由无 method+path 重复，也无 Mount 遮蔽**：`python docs/audit/_tools/api_audit_probe.py routes` 输出 `method+path 条目=162 / 重复: 无`，`Mount("/")` 在 index 159（最后），`挂在 '/' 之后注册的 APIRoute 数=0`。前轮 R1-13 那种"同 router 先注册 `/{kind}/{item_id}` 吃掉后续具体路径"的形态在现版本已不存在（`v5_catalog.py` 的 `/{kind}/{item_id}` 在 `:187`，具体的 `voice/{character}/lock|preview` 在 `:503,:528`，是两个 POST 不冲突）。
2. **containment 校验类工具函数是对的**：`v5_studio.py:88-100` `_safe_file()` 同时做 `".." in Path(relative).parts` + `relative_to(base)`；`main.py:989-1005` `_resolve_output_file()` 用 `resolve()` 后 `relative_to(OUTPUTS_DIR)`，`/media/..%2f..` 与绝对路径都被拒（`main.py:1182-1191` 的三条兼容分支也都过这个闸）。**注意**：它们挡的是"用户给的相对路径"，挡不住 P1-1 里那种"id 直接拼文件名"。
3. **`_err()` 不是"`return HTTPException`"误用**：`v5_create.py:117-119` 是工厂函数，全部 19 处调用（`:818,824,828,830,832,842,872,889,940,944,954,978,981,987,1145,1150,1288,1295,1389`）都写成 `raise _err(...)`，没有漏掉 `raise` 的。
4. **`ProjectOut.episode_count` 不是永远 0**：`schemas.py:26` 声明该字段，`models.py:63` 有同名 `@property`，`from_attributes=True` 能读到（易误报点）。
5. **dict 型 body 不会 500**：`Body(...)` 声明成 `dict` 时 FastAPI 先出 422（实测 `POST /api/v6/agent/run` 传 `["not","a","dict"]` / `"x"` 均 422 `dict_type`），本仓库没有"list 当 dict 用"的崩溃面。
6. **测试基线**（供复核参照）：`python -m pytest tests -q -rf` → `4 failed, 311 passed, 57 skipped`，失败项 `test_v3_acceptance.py::test_templates_validate_strict`、`test_v45_style_polish.py::test_cyber_scene_templates_and_scenes`、`test_v53_agents.py::test_muse_video_skips_without_provider`（`pipeline/agents/registry.py:541` `_Ctx` 缺 `seed`，pipeline 层）、`test_v5_2_acceptance.py::test_frontend_api_live`（`/media/templates_3d/tonight_show/final.mp4 Range -> 404`，成片缺失而非代码缺陷）。4 条都不是本轮 backend API 代码缺陷。
