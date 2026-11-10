# AiJue 优化实施方案（施工单）

> 配套审查报告：`docs/reviews/2026-11-05-code-review.md`
> 版本：2026-11-05 · 状态：**未开工**（本文只给方案，不含任何代码改动）
> 适用对象：接下来动手改这个仓库的人（可能是你，也可能是接手的智能体）

---

## 0. 这份文档怎么用

审查报告回答"哪里不好"，本文回答"**先改哪、改成什么样、怎么验、改坏了怎么退**"。

- 每个任务是一张独立的施工单：现状证据 → 目标 → 改动点 → 步骤（含代码骨架）→ 验证 → 回滚 → 工时。
- 代码骨架**不是最终实现**，只固定接口形状与关键判据，避免施工时走偏。
- 所有结论按 2026-11-05 的磁盘快照写；仓库有并行改动，**开工前先跑 §1 的复核命令**。

**范围**：只做 T1–T6（约 4.5 人日）。T7/T8 是后续项，本文只给方向。
**范围外**：渲染内核重写、两条栈合并、微服务化——理由见审查报告 §7。

---

## 1. 开工前 30 秒复核

```powershell
git -C D:\BaiduSyncdisk\wmj_work\XJ\AiJue status --porcelain | Measure-Object   # 确认未提交改动是否还在
Select-String -LiteralPath pipeline\templates3d_hq.py -Pattern "prompt_sig|base_seed" -Context 2,2
Select-String -LiteralPath pipeline\agents\registry.py -Pattern "reuse_shots" -Context 2,2
Select-String -LiteralPath backend\app\main.py -Pattern "_recover_stale_jobs" -Context 0,12
```

三条都要能看到预期代码，才按本文施工：

| 复核项 | 预期现状 | 若已变化 |
|---|---|---|
| 关键帧校验 | 已有 `prompt_sig`（crc32）与 `base_seed` | T3 只剩"片段层 + 镜头层"两处 |
| 镜头复用 | `reuse_shots` 默认 True，只判文件大小 | T3 可能已部分完成，按实际收窄 |
| 重启语义 | `queued/running` 一律标 `failed` | T5 可能已部分完成 |

**并行改动的处理**：先把工作区现有改动（含 V52 那轮）提交或 stash 成基线，再开 `opt/cache-fingerprint` 之类的小分支，一项一个 commit。不要在 100 个未提交改动上直接叠新改动——出问题无法二分。

---

## 2. 目标（Definition of Done）

全部做完后，下面 6 条**都能用命令验证**：

| # | 目标 | 验证方式 |
|---|---|---|
| D1 | 新机器一条命令起服务 | `powershell -File scripts\bootstrap.ps1` 后 `uvicorn` 起来 |
| D2 | 每次推送自动跑四步门禁 | CI 绿；故意改坏一个合同 → CI 红 |
| D3 | 改一句台词，镜头必重渲 | 改 `episode.json` 一行 → 该镜 mtime 与 `spec_hash` 变 |
| D4 | 什么都不改，二次运行 0 重算 | 日志 `reused=7/7`，GPU 时间 ≈ 0 |
| D5 | 第 3 镜崩溃，重启只补 3 镜之后 | `resume` 后日志 `skip 2 (done)` |
| D6 | 两条 GPU 任务并发不 OOM | 第二条日志 `waiting for gpu.h3`，串行完成 |

---

## 3. 任务总览

| ID | 任务 | 工时 | 依赖 | 风险 | 主要收益 |
|---|---|---:|---|---|---|
| T1 | 依赖与环境锁定 | 0.5 人日 | — | 低 | D1；让后面一切可验证 |
| T2 | CI 四步门禁 + 数字单一事实源 | 0.5 人日 | T1 | 低 | D2 |
| T3 | 三层缓存内容指纹 | 0.75 人日 | T1 | **中**（首次全量重渲） | D3/D4 |
| T4 | 跨进程 GPU 锁 | 0.5 人日 | — | 低 | D6 |
| T5 | 显式续跑 + 进度 + 任务心跳 | 1.0 人日 | T1 | 中（改表结构） | D5 |
| T6 | 编排超时/重试/取消 + run_id 唯一 | 0.75 人日 | T5 | 中 | 失败可恢复、可停止 |
| T7 | 可观测性（后续） | 1.0 人日 | T2 | 低 | 定位提速 |
| T8 | 安全收口（后续） | 0.5 人日 | T2 | 低 | 内网可用 |

**顺序**：T1 → T2 → T4（可与 T3 并行）→ T3 → T5 → T6。T4 先做，因为它是纯新增、零回归风险，且能立刻止损 OOM。

---

## 4. T1 · 依赖与环境锁定

**现状**：根目录无 `requirements.txt` / `pyproject.toml` / lock；`README` 只有一行 `pip install`；`v5_catalog` 缺 `jsonschema` 时降级为 warning。

**目标**：任何一台干净 Windows 机器，15 分钟内能跑到"服务起来 + 测试能跑 + 合同校验通过"。

**改动点（全部新增）**

1. `pyproject.toml`：项目元数据 + 运行时依赖（fastapi / uvicorn / sqlalchemy / pydantic / pillow / numpy / jsonschema / httpx / python-multipart）。
2. `uv.lock`（`uv lock` 生成）或 `requirements.lock.txt`（`pip-compile`）。
3. `scripts/bootstrap.ps1`：

```powershell
$ErrorActionPreference = "Stop"
if (-not (Test-Path .venv)) { py -3.11 -m venv .venv }
. .\.venv\Scripts\Activate.ps1
python -m pip install -U pip
pip install -r requirements.lock.txt
# 前端（可选，用 --skip-frontend 跳过）
if (Test-Path frontend) { Push-Location frontend; npm ci; npm run build; Pop-Location }
python -m pipeline.v5_cli validate --strict
```

4. `jsonschema` 改成**硬依赖**：在 `v5_catalog.py` 的 ImportError 分支里直接 `raise RuntimeError("缺少 jsonschema：请安装依赖")`，不再 warning 放行。

**验证**

```powershell
python -c "import fastapi, sqlalchemy, jsonschema, pytest; print('deps ok')"
python -m pipeline.v5_cli validate --strict
python -m pytest tests -q --collect-only | Select-Object -Last 3
```

**回滚**：纯新增文件，删掉即可；`v5_catalog` 的硬依赖改成 warning 回退。

**工时**：0.5 人日（含在本机实测一遍）。

---

## 5. T2 · CI 四步门禁 + 数字单一事实源

**现状**：无 `.github`、无 lint；README 同一页出现 142/150 端点、343+185/491 用例两套数字。

**目标**：推送即跑，5 分钟内给结论；README 数字不再手写。

**改动点**

1. `ruff.toml`：

```toml
line-length = 100
target-version = "py311"
[lint]
select = ["E", "F", "B", "BLE", "I"]
ignore = ["E501"]
[lint.per-file-ignores]
"tests/**" = ["BLE001"]
```

   > 存量 420 处 `except Exception` 不要求一次清零：先 `BLE001` 设 warning，新增代码必须干净，存量按目录逐步加白。

2. `.github/workflows/ci.yml`（或 GitLab CI，取决于你放哪）：

```yaml
name: ci
on: [push, pull_request]
jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.11" }
      - run: pip install -r requirements.lock.txt
      - run: ruff check .
      - run: python -m pytest tests pipeline/tests -q
      - run: python -m pipeline.v5_cli validate --strict
      - run: python scripts/check_workflows.py
```

3. `scripts/check_workflows.py`（新增，轻量）：33 份 workflow 的 `stages` 唯一、`tool` 在注册表、`depends` 无环、`enabled` 与 `optional` 一致。

4. `scripts/repo_stats.py`（新增）：统计 py 文件数/行数、路由数、合同数、测试用例数，`--write-readme` 更新 README 的"平台/内容/测试"三行，`--check` 比对不一致则非零退出。

**验证**：故意把 `contract/v6/workflows/classics.json` 的某个 `depends` 指向不存在的 stage → CI 红且报出字段路径；恢复后绿。

**回滚**：删 workflow 文件即可；`ruff` 配置可 `select` 缩减。

**工时**：0.5 人日。

---

## 6. T3 · 三层缓存内容指纹（核心正确性修复）

### 现状（三层，判据强度递减）

| 层 | 位置 | 现有判据 | 风险 |
|---|---|---|---|
| 镜头成品 | `templates3d_hq.main()` 的 reuse 分支（约 822-826 行） | `shots/<sid>.mp4` 存在且 >100KB | **最高**：默认开，改台词整段复用 |
| 片段 | `gen_t2va` / `gen_ref2va` | 文件名 `tid_shot_style`，>200KB 即复用 | 高：prompt/seed 变了仍复用 |
| 关键帧 | `_generate_keyframe` | `prompt_sig` + `base_seed` + ip/ckpt | 已修好 |

> 注意：第一层会把后两层的修复**整体短路**——只要镜头文件在，后面根本不执行。

### 方案

**新增 `pipeline/cachekey.py`**

```python
"""内容指纹：所有缓存判据的唯一入口。"""
import hashlib
import json


def content_key(payload: dict) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def shot_spec_hash(shot: dict, style_key: str, keyframe_sig: str = "") -> str:
    """一镜的完整规格指纹：台词/视觉/时长/风格/关键帧，缺一不可。"""
    return content_key({
        "id": str(shot.get("id")),
        "line": str(shot.get("line") or ""),
        "prompt": str(shot.get("prompt") or ""),
        "visual": str(shot.get("semantic_visual") or ""),
        "duration": round(float(shot.get("duration") or 5.0), 3),
        "style": style_key,
        "kf": keyframe_sig,
    })
```

**片段层（`gen_t2va` / `gen_ref2va`）**

```python
key = content_key({"p": prompt, "seed": seed, "style": style_key,
                   "dur": round(duration, 3), "mode": style_mode})
out = WORK / "clips" / f"{tid}_{shot['id']}_t2va1024{suffix}_{key}.mp4"
meta = out.with_suffix(".meta.json")
if not force and out.is_file() and out.stat().st_size > 200_000 and meta.is_file():
    return out
```

写入 meta：`{"key": key, "prompt": prompt, "seed": seed, "style": style_key}`。
签名需要 `force` 参数，由 `main()` 传入（`force=args.force`）。

**镜头层（`main()` 的 reuse 分支）**

```python
spec = shot_spec_hash(shot, style_2d, keyframe_sig)
existing = out_shots / f"{shot['id']}.mp4"
existing_meta = out_shots / f"{shot['id']}.meta.json"
if (args.reuse_shots and not args.force and existing.is_file()
        and existing.stat().st_size > 100_000
        and read_json(existing_meta).get("spec_hash") == spec):
    log(f"[{i}/{len(shots)}] {shot['id']} ok (reuse {spec[:8]})")
    continue
```

`shutil.copy2(final_src, ...)` 之后补写：`{"spec_hash": spec, "source": str(final_src), "sec": ...}`。

**两个开关语义分离**

- `--refresh-cache`：忽略片段/镜头缓存，但保留关键帧（贵）；
- `--force`：全量重渲（现有语义，保持不变）。

**迁移策略（重要，涉及一次性成本）**

旧产物没有 `spec_hash` → 按"未命中"处理 → **下一次运行会重渲全部镜头**。三种选择：

1. 默认重渲（推荐，正确性优先；一季 3 集约 20–30 分钟）；
2. `--adopt-legacy`：只给缺失 meta 的文件**补写当前指纹**（等于承认旧产物即当前规格），适合"我确定没改过内容"的批量迁移；
3. 只对指定模板迁移：`--adopt-legacy --template cyber`。

务必在文档里写清代价，别让用户以为"升级后变慢了"。

### 验证

```powershell
# 1) 基线：不改任何东西跑两次，第二次应 0 重算
python -m pipeline.agents run --template custom_tpl_v46_duologue --stages story,shots
# 期望: reused=7/7；GPU 时间 ≈ 0

# 2) 改一行台词，跑一次
# 期望: 该镜 mtime 变、spec_hash 变、其余镜 reused

# 3) 只改 seed（CLI 参数），片段层应重算
```

单测（`pipeline/tests/test_cachekey.py`）：同 spec → 同 hash；改 `line` / `visual` / `duration` / `seed` 各 +1 字段 → hash 必须变。

### 回滚

新增 `cachekey.py` 独立可删；`gen_t2va/ref2va` 与 `main()` 的改动各自一个 commit，可单独 revert。回滚后旧文件仍在（新命名多一个 `_<key>` 后缀，旧文件不会自动删除，需一次性清理）。

### 工时

0.75 人日（含迁移验证与 3 条单测）。

---

## 7. T4 · 跨进程 GPU 锁

**现状**：`jobs.py` 的 `GPU_LOCK` 是 `threading.Lock()`；文件锁只有 Muse 与 v3；SDXL/H3 无互斥。

**目标**：任何进程、任何入口抢同一块 GPU 时都排队，且能看到"谁在占"。

**改动点**

1. 新增 `pipeline/gpu_lock.py`，复用 `lipsync.py` 的 PID 回收逻辑（不要新写一套）：

```python
class GpuLock:
    def __init__(self, name: str, timeout: float = 3600.0, stale: float = 21600.0):
        self.path = REPO / "tmp" / "locks" / f"gpu.{name}.lock"
        self.timeout, self.stale = timeout, stale

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.time() + self.timeout
        while True:
            try:
                fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode())
                os.close(fd)
                return self
            except FileExistsError:
                self._reclaim_if_dead()
                if time.time() > deadline:
                    raise TimeoutError(f"等待 GPU 锁超时: {self.path.name} (holder={self.holder()})")
                time.sleep(1.0)

    def __exit__(self, *exc):
        self.path.unlink(missing_ok=True)
```

`_reclaim_if_dead()`：读 PID → `os.kill(pid, 0)` 判活 → 死的删锁；活且 mtime 超过 `stale` 也不抢（沿用 V52 已修正的规则：活进程需 >6 小时才回收）。

2. 接入点（三处，都是 GPU 入口）：
   - `templates3d_hq._render`：`with GpuLock("sdxl", timeout=900): subprocess.run(imagegen...)`；
   - `templates3d_hq.gen_t2va` / `gen_ref2va`：`with GpuLock("h3", timeout=1800): local.submit(...); local.wait_and_fetch(...)`；
   - `pipeline/lipsync.py`：把自建锁改为 `GpuLock("muse")`（行为不变，统一实现）。

3. 观测：`GET /api/v6/agent/gpu` 返回 `{"holders": {"sdxl": 12345, "h3": null}, "waiting": 1}`；等待日志统一格式 `waiting for gpu.h3 (holder=12345, waited=12s)`。

**验证**

```powershell
# 两个终端同时跑
python -m pipeline.agents run --template X --stages shots     # A
python -m pipeline.agents run --template Y --stages shots     # B
# 期望: B 的日志出现 waiting for gpu.h3；A 完成后 B 继续；无 CUDA OOM
```

**回滚**：`GpuLock` 是新增模块，接入点各自一处 `with`，revert 单个 commit 即可回到原状。

**工时**：0.5 人日。

---

## 8. T5 · 显式续跑 + 进度 + 任务心跳

**现状**：`dispatch` 用 daemon 线程；重启把 `queued/running` 全标 `failed`；`retry` 整单重跑；已完成镜头靠 `reuse_shots` 隐式跳过，用户看不到"跑到哪"。

**目标**：把"隐式缓存复用"变成"显式断点"，让"崩了"变成"继续"。

**改动点**

1. **run 级状态**：`outputs/agent_runs/<run_id>/state.json`

```json
{
  "run_id": "20261105_142233_a1b2_cyber",
  "template": "custom_tpl_v46_cyber",
  "stages": {
    "shots": {
      "status": "running",
      "shots": {
        "s1": {"status": "ok", "spec_hash": "9f3c...", "file": "shots/s1.mp4", "sec": 41.2},
        "s2": {"status": "ok", "spec_hash": "1a77...", "file": "shots/s2.mp4", "sec": 38.9},
        "s3": {"status": "failed", "error": "CUDA OOM", "attempt": 1}
      }
    }
  }
}
```

写入用 `tmp + os.replace`（项目已有惯例）；每镜完成即 flush，不要等阶段结束。

2. **CLI**：`python -m pipeline.agents resume --run-id <id>`：
   - 读 `state.json`，`status=ok` 的镜头跳过（打 `skip s1 (done)`）；
   - `pending/failed` 的镜头重跑；
   - 未开始的 stage 按原 `depends` 继续。

3. **jobs 表**（`models.Job`）：加三列 `attempt INT DEFAULT 0`、`heartbeat_at DATETIME`、`cancel_requested INT DEFAULT 0`。SQLite 用一次性脚本 `scripts/migrate_jobs_heartbeat.py`：

```python
cols = {c[1] for c in conn.execute("PRAGMA table_info(jobs)")}
if "attempt" not in cols:
    conn.execute("ALTER TABLE jobs ADD COLUMN attempt INTEGER DEFAULT 0")
```

4. **重启语义**：`_recover_stale_jobs()` 改成

```python
for job in stale:
    if job.heartbeat_at and (now - job.heartbeat_at).total_seconds() < 120:
        job.status = "queued"      # 刚还在跑 → 重新入队
    else:
        job.status = "failed"      # 心跳早断 → 判死
        job.error = "服务重启，任务中断，可重试或 resume"
```

5. **心跳**：worker 起一个 30s 的 `threading.Timer` 更新 `heartbeat_at`；任务结束取消。

6. **前端**：运行面板显示 `3/7 镜 · 复用 2 · 当前 s4`，数据来自 `state.json`（`GET /api/v6/agent/runs/{id}/state`）。

**验证**

```powershell
# 跑到第 3 镜时 Ctrl+C / taskkill
python -m pipeline.agents run --template X --stages shots
python -m pipeline.agents resume --run-id 20261105_142233_a1b2_cyber
# 期望日志: skip s1 (done) / skip s2 (done) / s3 开始渲染
```

**回滚**：`state.json` 只写不读时无副作用；`resume` 是新增命令；表结构加列可保留（不回滚列）。旧恢复逻辑保留一个开关 `RECOVER_STRATEGY=legacy`。

**工时**：1.0 人日（后端 0.6 + 前端 0.3 + 迁移脚本 0.1）。

---

## 9. T6 · 编排超时/重试/取消 + run_id 唯一

**现状**：`orchestrator` 异常即 `break`；无 timeout/重试；无 cancel；`run_id` 秒级冲突。

**改动点**

1. **stage 合同扩字段**（向后兼容，缺省值写在代码里）：

```json
{"id": "shots", "tool": "render_2d_hq", "timeout_sec": 1800, "retries": 1,
 "retry_backoff": 30, "continue_on_error": false}
```

2. **重试循环**（替换现有 `try/except`）：

```python
attempts = max(1, int(stage.get("retries", 0)) + 1)
for attempt in range(1, attempts + 1):
    try:
        result = call_tool(stage.get("tool") or "", ctx, params)
        break
    except Exception as exc:
        record.update(attempt=attempt, error=str(exc))
        if attempt == attempts:
            failed = True
            event("stage_fail", stage=sid, error=str(exc), attempt=attempt)
            break
        event("stage_retry", stage=sid, attempt=attempt, error=str(exc))
        time.sleep(float(stage.get("retry_backoff") or 0))
```

3. **超时（要诚实）**：Python 线程无法硬杀，所以分两层：
   - 渲染类工具内部已有子进程 `timeout=`（`imagegen` 600s、H3 1200s、T2VA 1800s）——这是真正的硬超时；
   - 编排层用"看门狗标记"：超过 `timeout_sec` 写 `record.timeout=true`、置 `ctx.cancel_event`，工具在下一个镜头边界自行退出。**不要假装线程能被 kill**。

4. **取消**：`POST /api/v6/agent/runs/{id}/cancel` 写 `cancel_requested` 标记文件 `tmp/cancel/<run_id>`；`templates3d_hq` 的镜头循环每轮检查该文件，命中则 `raise RuntimeError("canceled")`；orchestrator 捕获后把状态置 `canceled`（不计入 failed）。

5. **run_id**：

```python
run_id = f"{time.strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(2)}_{template_id}"
run_dir = run_root / run_id
run_dir.mkdir(parents=True, exist_ok=False)   # 冲突就抛，不静默共用
```

**验证**

- 注入失败：在 `synthesize_voices` 首行 `raise RuntimeError("test")`，配 `retries: 1` → `run.json` 里 `attempt` 出现 1、2；
- 取消：跑 shots 时点取消 → 3 秒内日志出现 `canceled`，状态 `canceled`；
- 并发：同一秒起两次 run → 两个目录。

**回滚**：所有字段有缺省值，老合同不受影响；`cancel` 是新增路由。

**工时**：0.75 人日。

---

## 10. T7 / T8（后续，不在本轮）

**T7 可观测性（1 人日）**

- `logging` 统一配置，JSON formatter：`{"ts","level","run_id","stage","shot","msg","dur_ms"}`；
- 中间件注入 `X-Request-ID`，写进子进程 `env`（`ZAOPIANJU_TRACE_ID`）；
- `outputs/agent_runs/index.json`（每次 run 追加一行摘要），`latest_runs()` 改读索引，不再全盘 `rglob`；
- 保留 `print` 只给 CLI 人读输出，管线内部一律 logger。

**T8 安全收口（0.5 人日）**

- 默认 `127.0.0.1`；`--host 0.0.0.0` 时强制 `ZAOPIANJU_API_TOKEN`，中间件校验 `X-Api-Token`；
- CORS 改显式来源列表（`http://127.0.0.1:5173`、`http://localhost:5173`）；
- 密钥文件用 `icacls` 收紧到当前用户，日志里对 `*_KEY`、`*_TOKEN` 做脱敏断言（测试里加一条）。

---

## 11. 排期建议

**3 天版（只保命）**：T1 + T2（第 1 天）→ T4 + T3 片段/镜头层（第 2 天）→ T5（第 3 天）。T6 顺延。
**5 天版（推荐）**：3 天版 + T6（第 4 天）+ 缓冲与回归（第 5 天）。

每天收工前跑一次 D1–D6 里已完成的条目，别攒到最后一起验。

---

## 12. 验收清单（照抄执行）

```powershell
# D1 环境
python -c "import fastapi, sqlalchemy, jsonschema, pytest; print('ok')"

# D2 门禁
ruff check .
python -m pytest tests pipeline/tests -q
python -m pipeline.v5_cli validate --strict
python scripts/check_workflows.py
python scripts/repo_stats.py --check

# D3/D4 缓存
python -m pipeline.agents run --template custom_tpl_v46_duologue --stages shots   # 第一次
python -m pipeline.agents run --template custom_tpl_v46_duologue --stages shots   # 第二次：应 0 重算

# D5 续跑
python -m pipeline.agents resume --run-id <上次被中断的 run_id>

# D6 GPU 锁
# 两个终端并发跑两个模板的 shots，观察 waiting for gpu.* 日志
```

---

## 13. 风险登记

| 风险 | 触发条件 | 影响 | 缓解 |
|---|---|---|---|
| 首次全量重渲 | T3 上线后旧产物无 `spec_hash` | 一季多花 20–30 分钟 GPU | 提供 `--adopt-legacy`；先在小模板验证 |
| 存量 lint 洪水 | T2 直接开 `BLE001` error | CI 立刻红，无法推进 | 先 warning + 目录白名单，按周收敛 |
| 表结构变更 | T5 加列 | 旧库打不开 | 迁移脚本幂等；启动前 `PRAGMA` 检查 |
| 锁饥饿 | T4 超时设太长 | 任务排队卡住 | 超时 15–30 分钟；`/gpu` 面板可见持有者 |
| 与并行改动冲突 | 仓库有 100 个未提交改动 | merge 冲突、行号漂移 | 先基线提交；一项一 commit；改前跑 §1 复核 |

---

## 14. 一页速记

- 先让环境可复现（T1/T2），否则后面都在猜。
- 缓存只认**内容指纹**，三层都要，镜头层最关键（T3）。
- GPU 只认**文件锁**，进程内锁不算数（T4）。
- 崩溃后要有**显式 resume**，不要靠"文件还在"碰运气（T5）。
- 编排要能**重试、能停、能被定位**（T6）。
- 不动渲染内核、不合并两条栈、不上微服务。
