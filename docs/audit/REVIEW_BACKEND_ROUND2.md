# REVIEW_BACKEND_ROUND2 — 第二轮审查（只读；产物仅写 tmp/audit2/ 与本文件）

- 审查人：review-backend
- 范围 A：`cinema_templates.py` / `pipeline_ui.py` / `character_{audition,promo,dialogue,showcase}.py` / `{db,models,schemas,config}.py`
- 范围 B：`v5_studio.put_voice`、`muse.py`、`v6_agent.py`、`v5_series.py`、`main.py`、`jobs.py` 的回归（含 `config.OUTPUTS_DIR` 混用、`from .. import config` 位置、异常路径）
- 方法：`git diff` 逐块比对 + 全文读改动函数 + 3 个 TestClient/直调脚本（`tmp/audit2/proof.py`、`proof_m1.py`、`proof_m2.py`）；DB/outputs 全部重定向到 `tmp/audit2/`，`JOBS_AUTORUN=0`，`V5_CONTRACT_ROOT=tmp/audit2/v5`（仓库 contract/outputs 未被改写）。

## 总览

| # | 严重度 | 位置 | 一句话 |
|---|---|---|---|
| 1 | **P0** | muse.py:200,227,230 | `muse_sync` 缺 `global _current` → `POST /api/v6/muse/sync` 走到占位检查即 `UnboundLocalError`(500) |
| 2 | **P0** | muse.py:323,338,341 | `muse_video_generate` 缺 `global _vcurrent` → `POST /api/v6/muse/video` **每次** 500（已实测） |
| 3 | **P0** | muse.py:332-334 | 修好 #2 后立刻暴露：`template`/`name` 未校验 → `generate_video` 在 outputs 之外 mkdir+写 mp4（已用 worker 实测） |
| 4 | **P1** | main.py:2268-2274,2314,2330 | 新增的 `video_fallback` 只标记"回退到 render_url"，真正的 lang/version 回退（发中文母版）仍为 `False`（已实测） |
| 5 | **P2** | character_audition.py:739-748+790-792（promo:652-670+705-707，dialogue:528-560+744） | 互斥"检查"与"占位"分处两个锁块，中间隔 40+ 行构造 → 并发双击可同时通过，GPU 单并发失效 |
| 6 | **P2** | v5_series.py:20-22,216-228 | 缓存命中要对每集成片做全量 sha1（命中也要读完全部片子）；导出仍无锁；`OUT_2D/OUT_3D/OUT_SERIES` 仍写死 `repo/outputs` |
| 7 | **P2** | pipeline/lipsync.py:163、pipeline/muse_video.py:32、pipeline/agents/orchestrator.py:15 | backend 已切 `config.OUTPUTS_DIR`，pipeline 层仍写 `repo/outputs` → 覆写环境变量后两侧再次分叉（证据 URL 404 / 找不到对白对） |
| 8 | **P3** | v6_agent.py:362-369 | 季 run 的 `dry_run` 仍写 `status=ok`（run.json/summary.md 也是 ok），stages 全 planned；`with_muse` 仍不联动 dry_run |
| 9 | **P3** | jobs.py:42-56,497-503 | `_prune_job_files` 全局按 mtime 只留 300 个（会误删其它运行中任务的 spec 输入）；`GEN_SHOT_TIMEOUT` 非法值被 catch 成"启动失败" |
| 10 | **P3** | character_showcase.py:214-225,308-317 | 发布包缓存只比对 title/vertical/mode，不比对源 reel → 重跑试镜合集后仍返回旧 zip（`_split_package` 反而做了 signature） |
| 11 | **P3** | pipeline_ui.py:248-255,263-272 | 读 contract 的 `break` 无条件在第一次成功读取后执行 → 内建模板的 custom 覆盖（keyframe_candidates、label）被忽略；与 cinema_templates 的 custom 优先语义相反 |
| 12 | **P3** | muse.py:202 | `int(payload.get("limit"))` 非数字 → ValueError → 500（同模块其它错误都是 400） |
| 13 | **P3** | character_*.py `_JOBS`/`_PROMOS`/`_DIALOGUES` | 内存字典无上限，每项含完整 `character_data`+segments，长跑进程单调增长 |

---

## P0-1 `/api/v6/muse/sync` 100% UnboundLocalError

**位置**：`backend/app/routers/muse.py:200`（def）、`:226-230`（占位块）

**触发**：任何能走到占位检查的请求（MuseTalk 可用 + 有对白对，或显式 clip/audio）。

**为什么错**：本轮"请求内先占位"的修复在函数体里新增了赋值 `_current = job_id`（230），但函数缺 `global _current`，于是 Python 把 `_current` 判定为局部变量，第 227 行的读 `if _current:` 抛 `UnboundLocalError`。`_worker` 里有 `global`，只有请求处理器没有。

**实测**（`tmp/audit2/proof_m2.py`，monkeypatch availability 让流程走到 227）：

~~~text
P0-a /sync raised: UnboundLocalError cannot access local variable '_current' where it is not associated with a value
~~~

**最小修法**：

~~~python
def muse_sync(payload: dict = Body(default=None)) -> dict:
    global _current          # 补这一行（与 _worker 一致）
~~~

---

## P0-2 `/api/v6/muse/video` 每次 500

**位置**：`muse.py:323`（def）、`:337-341`

**触发**：任意 POST（不需要先决条件——异常发生在参数校验之后、任何分支之前）。

**为什么错**：同 #1：`_vcurrent = job_id`（341）使 `_vcurrent` 成为局部变量，338 行读它直接 `UnboundLocalError`。

**实测**：

~~~text
P0-b /video -> 500
P0-b /video raised: UnboundLocalError cannot access local variable '_vcurrent' where it is not associated with a value
~~~

**最小修法**：`def muse_video_generate(...): global _vcurrent`。**必须与 #3 同一提交修**（见下）。

---

## P0-3 `/api/v6/muse/video` 越界写盘（#2 修好后立即生效）

**位置**：`muse.py:332-334`

~~~python
template = str(payload.get("template") or "muse")
name = str(payload.get("name") or f"muse_{...}")
out = VIDEO_ROOT / (template if template != "muse" else "") / f"{name}.mp4"
spec = {**payload, "provider": provider, "out": str(out)}
~~~

**触发**：`{"provider":"stub","allow_stub":true,"template":"../../../tmp/x","name":"pwn"}`（或任何 provider）。`allow_stub` 只是让 stub 通过，路径在分发前就已算好。

**为什么错**：`muse_sync` 的输入已加 `_safe_input_path`（outputs/tmp 白名单），但**输出路径**没做同样的白名单/ID 校验；`pipeline/muse_video.generate_video` 会 `out.parent.mkdir(parents=True, exist_ok=True)` 然后 ffmpeg 写文件（stub 也写），于是可在任意目录建目录 + 落 `.mp4`（内容可以是占位片，也能覆盖同名文件）。

**实测**（绕过 500，直接调 worker 验证写盘能力）：

~~~text
P0-c worker write outside outputs: True | size: 4347 | inside outputs: False | job status: ok
~~~

**最小修法**：

~~~python
if not ID_RE.match(template):
    raise HTTPException(400, f"非法 template: {template!r}")
name = re.sub(r"[^a-z0-9_\-]", "", name.lower())[:60] or f"muse_{int(time.time())}"
out = (VIDEO_ROOT / (template if template != "muse" else "") / f"{name}.mp4").resolve()
if not str(out).startswith(str(VIDEO_ROOT.resolve())):
    raise HTTPException(400, "输出路径不合法")
~~~

---

## P1-4 `video_fallback` 标记抓错了回退分支（新字段给出假保证）

**位置**：`main.py:2266-2274`（取片分支）、`:2314`（`fallback_flag = bool(locals().get("video_fallback"))`）、`:2330`

**触发**：只有中文母版时发布 `lang=en`（或 `version=dynamic`）。

**为什么错**：`_publish_video_candidates()` 的候选表最后一项**就是** `final.mp4`，所以 `next(c for c in candidates if c.is_file())` 在"英文版不存在"时会命中中文母版，`video_path is not None` → `video_fallback` 不会被赋值 → 记录里 `video_fallback=False`。这个字段唯一能标记的是"按语言/版本也没找到，最后走了 render_url"这一种情况，而那不是问题场景。

**实测**（`tmp/audit2/proof.py`）：

~~~text
M2) record: lang=en video=/media/episodes/ep_e42e88daa2/final.mp4 fallback=False rel=episodes/ep_e42e88daa2/final.mp4
~~~

→ 记录自称"未回退"，实际发的是中文母版。

**最小修法**（二选一）：

~~~python
expect = (base / "localized" / lang / "final.mp4") if lang != "zh" else \
         ((base / "final_dynamic.mp4") if version == "dynamic" else base / "final.mp4")
video_fallback = (video_path != expect)          # 用"是否等于期望文件"判定，别用分支走没走
~~~

或按上轮建议直接不让候选表包含 `final.mp4`（取不到就 400）；同时把 `meta.video_fallback` 升格为 `fallback_from`（记录实际用了哪个文件）。

---

## P2-5 角色三兄弟的 GPU 互斥是 "check-then-act"（并发可越过）

**位置**：`character_audition.py:739-748`（检查块）→ `:750-792`（造 job）→ `:790-792`（占位）；`character_promo.py:652-670`→`:671-707`；`character_dialogue.py:528-560`（`_mutex_check` 自持锁）→`:744`

**触发**：同一端点并发两次（双击 / 前端重试）。

**为什么错**：检查在第一个 `with _LOCK` 块里，占位（`_JOBS[job_id]=job; _ACTIVE_JOB=job_id`）在**几十行之后**的另一个块里。中间窗口 `_ACTIVE_JOB` 仍为空 → 第二个请求同样通过检查 → 两个线程同时跑 H3/ffmpeg，正是这三个模块要防的"GPU 单并发"。对照 `cinema3d._reserve_job`（536-570）与 `pipeline_ui`（610-646）都是"检查+建 job+占位"在同一 `with _LOCK` 内完成。

**最小修法**：把 job 构造挪进检查所在的锁块（或先占位再补字段），锁外只做线程启动。

---

## P2-6 系列导出：缓存命中代价 + 无锁 + config 混用未清完

**位置**：`v5_series.py:20-22`（`OUT_2D/OUT_3D/OUT_SERIES = REPO/"outputs/..."`）、`:216-228`（缓存 sha 比对）、`:197-206`（无锁）

三点：

1. 缓存判断对每集 `_sha1(clip)` 全量哈希（5×100MB），只为回答"要不要重建"；`(mtime_ns, size)` 足够。
2. `export_series` 全程无锁，`title_card.mp4 / ep_card_XX.mp4 / concat.txt / series.mp4 / manifest.json` 都是固定路径：并发导出互相覆盖（ffmpeg 读到被改写的 concat.txt → 内容错乱或 `check=True` 500）。
3. 本轮只把 URL/封面/卡片的**读**改成 `config.OUTPUTS_DIR`，`OUT_2D/OUT_3D/OUT_SERIES` 仍是 `REPO/outputs`：一覆盖环境变量就"写 repo、URL 指向 config" → 导出成功但 `/media/series/...` 404。

**最小修法**：`OUT_* = Path(config.OUTPUTS_DIR) / ...`；缓存键用 `(mtime_ns, size)`；`with _export_lock(sid):` 包住构建段。

---

## P2-7 backend 已切 config.OUTPUTS_DIR，pipeline 层仍写 repo/outputs

**位置**：`pipeline/lipsync.py:163`（`OUT_ROOTS = REPO/outputs/templates_{2d,3d}`）、`pipeline/muse_video.py:32`（`OUT_ROOT = REPO/outputs/muse_video`）、`pipeline/agents/orchestrator.py:15`（`RUN_ROOT = REPO/outputs/agent_runs`）、`pipeline/render_v5.py:30`

**触发**：设置 `ZAOPIANJU_OUTPUTS_DIR`（测试隔离 / 多实例 / 换盘）。

**为什么错**：muse/v6_agent/v5_studio/v5_create 已切到 `config.OUTPUTS_DIR`，但这些 pipeline 常量仍在 `REPO/outputs`：

- `/api/v6/muse/sync`（模板模式）用 `lipsync.dialogue_pairs` 扫 `REPO/outputs/templates_3d` → 覆写后永远"没有对白对"；
- `/api/v6/agent/run` 把 run 证据写进 `REPO/outputs/agent_runs`，而 `/media/agent_runs/...` 从 `config.OUTPUTS_DIR` 提供 → evidence URL 404；
- `muse_video` 默认 out 落在 `REPO/outputs/muse_video`，而 `_media_url` 以 `config.OUTPUTS_DIR` 求相对路径 → 返回空串。

**最小修法**：pipeline 侧统一读 `ZAOPIANJU_OUTPUTS_DIR`（或共享一个 `outputs_root()`）；backend 侧不要 import 时固化（改成函数内取）。

---

## P3-8 季 run 的 dry_run 仍被记成 ok

**位置**：`v6_agent.py:362-369`（`status = "failed" if failed else "ok"`）、`:399-406`（`with_muse` 分支不改 dry_run）

**为什么错**：`orchestrator.run_template` 对 dry_run 的 stage 记 `planned`、run.status=`planned`（orchestrator.py:117-121,149），但季 worker 只统计 `failed` → job 与落盘的 `run.json/summary.md/events.jsonl` 全写 `ok`。配合 `dry_run` 默认 True、`with_muse` 不联动 dry_run，默认参数下"季口型批量"会留下"成功但零产出"的证据包。

**最小修法**：`status = "failed" if failed else ("planned" if dry_run else "ok")`；`if with_muse and "dry_run" not in payload: dry_run = False`（或显式 400 要求传 `dry_run:false`）。

---

## P3-9 job 文件裁剪与超时环境变量

**位置**：`jobs.py:42-56`（`_prune_job_files`）、`:497-503`（调用点 + `float(os.environ.get(...))`）

**问题**：① 裁剪按**全局** mtime 只留 300 个，而 `run_localize_job/run_export_job/run_dynamic_shot_job` 的 spec/manifest 是 CLI 的输入，批量并发时可能删掉仍在读的输入文件；② 只在 `run_shot_job` 里调用，其它任务从不触发；③ `GEN_SHOT_TIMEOUT=abc` 的 `ValueError` 落在同一个 `except`，被报成"gen-shot 启动失败"。

**最小修法**：`timeout=float(os.environ.get(...) or 3600)` 提到 try 外并单独校验（非法回落 3600）；裁剪改成启动时一次性 + 按 job 记录（`finished_ts` 早于 N 天）删，而不是全局限量。

---

## P3-10 角色试镜合集发布包缓存不随源片失效

**位置**：`character_showcase.py:214-225`（缓存分支）、`:308-317`（`_cache_matches`）

**为什么错**：`_cache_matches` 只比较 title / vertical / mode。重跑 `character_audition_reel.mp4` 后同标题请求直接返回旧 zip（除 `force=true`）。同文件的 `_split_package` 用 `_split_signature(rows)` 做内容签名，两个包策略不一致。

**最小修法**：`_cache_matches` 增加源 reel 的 `(mtime_ns, size)` 或 sha1 比对，构建时写进 metadata。

---

## P3-11 pipeline_ui 的 contract 读取"内建优先"，与 custom 覆盖语义相反

**位置**：`pipeline_ui.py:248-255`、`:263-272`

**为什么错**：两个循环都先读 `_TEMPLATE_CONTRACT_DIR`（内建），成功即 `break`（`_template_label` 直接 return）。对"内建模板 + custom 覆盖"的组合，custom 里的 `keyframe_candidates` / `label` 永远读不到；而 `cinema_templates._template_contract`（365-372）是 custom `merged.update` 覆盖内建。同仓两套口径。

**最小修法**：按 `(builtin, custom)` 顺序读取并 `merged.update`，再取字段。

---

## P3-12 `limit` 非数字 500

**位置**：`muse.py:202`

~~~python
limit = max(1, min(int(payload.get("limit") or 2), 8))
~~~

`{"limit": "abc"}` → `ValueError` → 500。同模块其它参数问题都返回 400。

**修法**：`try/except (TypeError, ValueError) → HTTPException(400)`，或改用 pydantic body。

## P3-13 角色任务内存字典无上限

`character_audition._JOBS` / `character_promo._PROMOS` / `character_dialogue._DIALOGUES` 只增不减，每项含整份 `character_data` + `segments`；启动时还会从磁盘再灌 200 条。长跑进程内存单调增长。

**修法**：保留最近 N 条（按 `finished_ts` 淘汰 finished），读接口分页。

---

# 回归验证结论（已修项确认）

| 上轮问题 | 现状 | 证据 |
|---|---|---|
| P0-1 put_voice 覆盖 custom-only 合同 | **已修好**：`has_base = bool(source.get("base"))` 语义正确（仅 builtin+custom 为 True），builtin-only / custom-only 走 full-merge；实测 custom-only 11 个键全保留、builtin 仍写 partial | `tmp/audit2/proof.py` M4 |
| P1-3 publish 幽灵记录 | **已修好**：命中去重后新记录标 `duplicate` | M2b：`[('B','duplicate'), ('A','queued')]`，同一 job |
| P2-4 角色 id 路径逃逸 | **已修**：`CHAR_ID_RE` + 显式 `..` / `/` / 反斜杠检查（main.py:63,855-859）——代码核对，未再跑脚本 | — |
| P2-9 预检多语种假通过 | **已修**：只统计 `status=="ready"` 且 `video_url` 非空的 Localization（main.py:2095-2101） | 代码核对 |
| P2-5 muse/v6_agent 单并发竞态 | v6_agent **已修**（检查+插入同锁、含 queued）；muse **修出新 P0**（#1/#2） | 见上 |
| P2-6 muse 输入路径白名单 | **已修且写法正确**：`_safe_input_path` 用 `resolve()` + `r in path.parents`，outputs/tmp 双白名单；template_id 回退也加了 `ID_RE` | 代码核对 |
| P3-11 job 文件无限增长 | 部分：新增 `_prune_job_files`，触发点/全局裁剪有副作用（#9） | — |
| P3-12 outputs 口径不一致 | 部分：muse/v6_agent/v5_studio/v5_create 已切 config；v5_series 与 pipeline 层未切（#6/#7） | — |
| `from .. import config` 位置 | **无功能问题**（v6_agent 把它夹在两条 fastapi import 之间；相对导入不依赖 `sys.path`，`app.config` 不反向 import routers，无循环）。仅建议统一分组 | 实测导入正常 |
| 路由表 / SSE | 未改动，`/api/v6/muse/*` 已在 `/media` mount 之前注册 | — |

# 检查过、未见问题的部分

- **`db.py` / `models.py` / `schemas.py` / `config.py`**：迁移表与 ORM 列名逐列对齐，`meta_json`/`spec_json` 映射正确，`ALTER TABLE ... NOT NULL DEFAULT` 在 SQLite 可重复执行；`SettingsUpdate` 白名单式更新（`metaso_key`/`upload_post_api_key`/`llm_api_key` 均不落 DB）。唯一注意点：两 worker 同时启动会并发 ALTER 报 duplicate column（当前单 worker，不列条目）。
- **`cinema_templates.py`**：`_JsonFileCache`（`(mtime_ns,size)` 键 + 半截 JSON 重试 3 次 + 空文件不当爆点）写得稳；发布包构建段（`_PUBLISH_LOCK`、tempdir + `os.replace`、`tmp_zip` 先赋值后 try、竖版失败降级 warning）无问题；`@router.get("")` + `@router.get("/")` 是刻意避免 307。
- **`cinema3d.py`**：`_reserve_job`（检查+插入同锁）与 `_persist_job`（原子替换）是仓内正确范本。
- **`pipeline_ui.py`**：单发/批次的 job 生命周期（检查+占位同锁、线程启动失败回滚、batch 抢锁后回滚单发）正确。
- **`character_{audition,promo,dialogue}.py`**：取消路径（置 canceled → `taskkill /F /T` → 收尾清 `_ACTIVE_*`）、`_load_persisted_*`（重启把 queued/running 标 failed）、`_persist` 原子写均正确；跨模块互斥调用都在持锁之外（仅 dialogue→promo 单向，无反向路径，不会死锁）。问题仅 #5/#13。
- **`character_showcase.py`**：解锁/原子替换/tempdir 清理/竖版失败不阻塞都正确；仅 #10 的缓存键。
- **`v5_studio.put_voice`**：修好（见上表）。
- **`muse.py` 的 `_safe_input_path` / `_recent` / `_media_url` / 收尾 `finally: if _current == job_id`**：正确。问题集中在 #1/#2/#3/#12。

# 复现脚本

- `tmp/audit2/proof.py` —— 发布 fallback 标记 / duplicate 状态 / overview·series 的 load_catalog 次数 / put_voice 两类角色回归
- `tmp/audit2/proof_m1.py` —— 取 `/api/v6/muse/video` 的完整 traceback
- `tmp/audit2/proof_m2.py` —— `/sync` `/video` 两个 UnboundLocalError + 绕过 500 直接调 worker 证明越界写盘
- 环境：`ZAOPIANJU_*=tmp/audit2/*`、`JOBS_AUTORUN=0`、`V5_CONTRACT_ROOT=tmp/audit2/v5`；仓库数据未被改动。
