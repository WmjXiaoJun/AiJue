# REVIEW_BACKEND — backend/ 代码审查（只读，产物仅写 tmp/ 与本文件）

- 审查人：review-backend（子智能体）
- 范围：`backend/app/main.py`、`backend/app/routers/*.py`（v5_studio / v5_catalog / v5_create / v5_series / v6_agent / muse / cinema3d / cinema_templates / pipeline_ui / character_* / publish_batches）、`backend/app/{models,db,jobs,config,schemas}.py`
- 方法：全文读关键模块 + `tmp/audit_routes.py` 导出 155 条真实路由表顺序 + 3 个 TestClient 复现脚本（`tmp/audit_proof1.py` / `audit_proof2.py` / `audit_proof3.py`），DB 与 outputs 全部重定向到 `tmp/`，`ZAOPIANJU_JOBS_AUTORUN=0`（不起 GPU、不跑渲染），`V5_CONTRACT_ROOT` 指向 `tmp/audit_v5`（仓库 contract 未被改写，`git status contract/v5/custom` 干净）。
- 已知问题（SyncNet 权重损坏 / templates_2d+3d 双份成片 / 无鉴权 / frontend dead code / README 旧章节）按约定未列入。

## 总览（按严重度）

| # | 严重度 | 位置 | 一句话 |
|---|---|---|---|
| 1 | **P0** | router/v5_studio.py:542-564 | `PUT /api/v5/voice/{character}` 把 custom-only 角色合同整个覆盖成 `{"id","voice"}`，且写后才校验 → 返回 400 但角色定义已丢 |
| 2 | **P1** | main.py:2251-2260 | 发布 `version=dynamic` / `lang!=zh` 取不到片时静默回退到中文静态母版成片 |
| 3 | **P1** | main.py:2302-2335 + jobs.py:1636-1659 | publish 去重按 (dry_run,lang,version,platforms) 命中旧 job：新 PublishRecord 永久 queued（幽灵记录），新标题/描述不生效，接口仍返回 200 |
| 4 | **P2** | main.py:852-854 + character_files.py:17 + concept_files.py:31 | `POST /projects/{pid}/characters` 不校验 `spec.id`，`..\..\` 可让角色产物目录逃出 outputs |
| 5 | **P2** | muse.py:208-220（+237/302 同理）、v6_agent.py:212-217、410-414 | 单并发"占位"竞态：`_current/_current_job` 只在 worker 里赋值，两个并发请求都能通过检查 → 两个 GPU 任务齐跑 |
| 6 | **P2** | muse.py:186-194 | `/api/v6/muse/sync` 的 clip/audio 直接拼 `_REPO_ROOT` 后 resolve，无 outputs 包含性校验（绝对路径、`..` 均通过） |
| 7 | **P2** | jobs.py:486-487 | shot 任务 `run_cli` 无 timeout，且全站没有"取消 job"端点 → gen-shot 挂住后 job/shot 永久 running，retry 还拒绝 running |
| 8 | **P2** | v5_series.py:197-218 | `POST /api/v5/series/{sid}/export` 的缓存只看文件是否存在，任一集重渲后合集仍是旧的；且整段无锁，并发导出互踩 concat.txt/final.mp4 |
| 9 | **P2** | main.py:2082-2099 | 发布预检"多语种版本"只要 Localization 行存在就 pass（draft/failed 也算），与 #2 叠加 → 检查通过但发出的是中文母版 |
| 10 | **P2** | v6_agent.py:385-386,390-406,362-369 | 季生产默认 `dry_run=true`，`with_muse=true` 时 job 仍报 `ok`（只统计 failed），实际全部 planned；`bool("false")` 陷阱 |
| 11 | **P3** | jobs.py:464-465,646-647,806,992,1458,1567 | 每个 job 的 spec/manifest JSON 写进 `backend/data/jobs` 后永不清理（无 TTL、无上限） |
| 12 | **P3** | v5_studio.py:79-89,206-211,708；v6_agent.py:62-64；muse.py:37-39；main.py:1062/1065/1083/1095 | 读写目录口径不一致：一半走 `config.OUTPUTS_DIR`，一半写死 `repo/outputs`，`ZAOPIANJU_OUTPUTS_DIR` 一覆写就"写这边读那边" |
| 13 | **P3** | v5_catalog.py:187 vs 498 | `GET /api/v5/voice/{character}` 被同 router 先注册的 `/{kind}/{item_id}` 遮蔽（活的是 v5_studio.py:512 那份），属死路由 |

---

## P0-1 `PUT /api/v5/voice/{character}` 覆盖 custom-only 角色合同（写后 400，数据已丢）

**位置**：`backend/app/routers/v5_studio.py:533-581`（关键 542-564）

**触发**：对任意"只在 `contract/v5/custom/characters/` 里存在、没有内建基线"的角色（现网有 `custom_street_kid`、`custom_old_inventor`）调用 `PUT /api/v5/voice/{cid}`，body 只带 `{"instruction": "..."}` 或任意非空字段。

```bash
python tmp/audit_proof1.py     # 输出见下
```
```
A) before keys: ['appearance','era','id','label','mouth','persona','poses','role_pool','style_overrides','tags','voice']
A) PUT status 400 {"detail":"角色声音解析失败: ... 缺少 seed ..."}
A) after  keys: ['id', 'voice']
A) label after = None | appearance kept = False
A) catalog row after: [{'id':'custom_street_kid','label':'custom_street_kid','role_pool':[],'gender':None,'appearance 全丢'}]
```

**为什么错**：`payload = {"id": character, "voice": updates}` 是**局部覆盖**语义，只在"存在内建基线、custom 文件做深合并"时成立。custom-only 角色的 custom 文件**就是**完整合同，`write_json_atomic` 直接把它替换成 2 个 key → `label/persona/appearance/poses/tags/style_overrides` 全丢。更糟的是顺序：先 `write_json_atomic`（561），后 `_resolved_voice`（564）才发现新 voice 缺 `voice_id/seed` → 抛 400，**接口报错但已经写坏**，前端没有任何回滚依据。同文件 `v5_catalog.put_item`（v5_catalog.py:235-247）已明确处理过这个 case（"对纯 custom 对象必须落盘完整对象"），voice 这条路径漏了。

**最小修法**（~12 行）：
```python
path = v5_catalog.custom_path("characters", character, root=catalog.get("root") or None)
existing_file = _read_json_file(path)                 # 纯 custom = 完整合同
base_char = (catalog.get("characters") or {}).get(character) or {}
merged_voice = {**(base_char.get("voice") or {}), **updates}
if existing_file and not (catalog_root/"characters"/f"{character}.json").is_file():
    payload = {**existing_file, "id": character, "voice": merged_voice}   # full
else:
    payload = {"id": character, "voice": merged_voice}                    # partial（有内建基线）
# 先 _resolved_voice()/validate_item() 再 write；写后解析失败只返回 warning，不再吞掉 400
```

---

## P1-2 发布静默回退：dynamic / 非中文 → 发出中文静态母版

**位置**：`backend/app/main.py:2251-2260`；候选表 `main.py:1831-1843`

**触发**：集目录只有 `final.mp4`，调用
```
POST /api/episodes/{eid}/publish {"version":"dynamic","dry_run":true,"platforms":["youtube"]}
POST /api/episodes/{eid}/publish {"lang":"en","dry_run":true,"platforms":["youtube"]}
```
**实测**（`tmp/audit_proof1.py`）：
```
C) dynamic publish status: 200          D) en publish status: 200
publishes: [('pub_7498','queued','en','final','/media/episodes/ep_x/final.mp4'),
            ('pub_063f','queued','zh','dynamic','/media/episodes/ep_x/final.mp4')]
```
两条记录的 `video_url` 都指向**同一个中文 `final.mp4`**。

**为什么错**：`_publish_video_candidates` 返回的是"希望顺序"列表，但代码用 `next(c for c in candidates if c.is_file())` 取第一个存在的——列表最后一项永远是 `final.mp4`，所以"英文版不存在"会退化成"发中文版"，但记录的 `lang=en`、`meta.version=dynamic`、标题/描述却是按 en/dynamic 生成的（`_localized_publish_meta` 取不到又落到品牌标题）。用户以为发了英文动态版，实际把中文静态母版推到了外部平台，事后无法从记录里看出来。

**最小修法**（~6 行）：不要跨版本/跨语种回退——
```python
if version == "dynamic":
    candidates = [base / "final_dynamic.mp4"]
elif lang != "zh":
    candidates = [base / "localized" / lang / "final.mp4"]
else:
    video_path = resolve_episode_video(episode.id, episode.render_url)  # 唯一保留 render_url 回退的分支
video_path = video_path if lang=="zh" and version=="final" else next((c for c in candidates if c.is_file()), None)
if video_path is None: raise HTTPException(400, f"缺少 {lang}/{version} 成片：{candidates[0]}")
```

---

## P1-3 publish 去重吞掉新请求 + 幽灵 `PublishRecord`

**位置**：`main.py:2302-2335`（建记录后调 enqueue）；`jobs.py:1636-1659`（去重键）

**触发**：同一集在已有排队/运行中的 publish job 时再发一次（哪怕标题、描述、hashtags、成片都换了）。`JOBS_AUTORUN=0` 下最直观：
```
B) publish#1 -> 200 job=job_22163c216c
B) publish#2 -> 200 job=job_22163c216c        # 同一个 job
B) publishes: [('pub_d46','queued','zh','final',...), ('pub_c16','queued','zh','final',...)]
B) jobs:      [('job_22163c216c','publish','queued')]      # 只有 1 个 job，2 条记录
E) TITLE-A / TITLE-B 两次请求 → 同一个 job（title 不在去重键里）
E) records: [('TITLE-B','queued'), ('TITLE-A','queued')]
```

**为什么错**：`main.publish_episode` 先 `db.add(record); db.flush()` 再 `enqueue_publish_job`；而后者命中 active job 时直接 `return active, False`，新建的 record 既没被删、也没被那个 job 认领（job.params.publish_id 指向旧 record）。结果：① 第二条记录永久 `queued`，`GET /episodes/{eid}/publishes` 出现永远不动的幽灵发布，`_recover_stale_jobs` 重启后还会把它标 failed；② 新请求的 title/description/hashtags/video 全部丢弃（去重键里只有 dry_run/lang/version/platforms），接口却返回 200 —— 用户以为发布已受理；③ 若第一条其实已经发完，用户再点一次想改标题重发，会被"合并"，平台侧不会收到第二次发布。

**最小修法**：让 job 与 record 一一对应——
```python
# jobs.enqueue_publish_job(db, episode, record, params)
if active is not None:
    db.delete(record)              # 或复用 active.params["publish_id"] 对应记录并回写新 meta
    return active, False
# 并把 content 指纹纳入去重键：hash((title, description, hashtags, video_rel))
```
（`main.publish_episode` 侧：`if not created: return active_job` 时同时返回"未新建发布，沿用 job X"的提示。）

---

## P2-4 角色 id 未校验 → 产物目录逃出 outputs

**位置**：`main.py:846-895`（`character_id = str(provided_id or "").strip() or new_id(...)`，无 `ID_RE`）；受害点 `character_files.py:16-17`、`concept_files.py:30-31`

**触发/实测**（`tmp/audit_proof3.py`）：
```
POST /api/projects/{pid}/characters {"spec":{"id":"..\\..\\escape_char", ...}} -> 200 "..\..\escape_char"
H) character_dir resolved = D:\...\tmp\escape_char          # 已在 OUTPUTS_DIR 之外
H) inside outputs? = False
H) v5 create same id -> 400 （v5_create 有 _ID_RE 校验，V3 角色 API 没有）
```

**为什么错**：V3 角色工坊允许客户端自带 `spec.id`，而所有产物路径都是 `OUTPUTS_DIR/"characters"/id` / `OUTPUTS_DIR/"concepts"/id` 裸拼接。Windows 下 `\` 也是分隔符，URL 里 `{cid}` 可以带 `..\..\`（`/` 不行、`\` 可以），随后 `POST /characters/{cid}/concept/generate` → `concept_files.concept_dir(cid).mkdir(parents=True)` 或 `portrait/from-concept` 的 `shutil.copy2` 就会在 outputs 之外建目录/落文件。同一仓库的 `v5_create` 与 `v5_studio` 都做了 id 白名单，只有这条老 API 漏了。

**最小修法**：
```python
CHARACTER_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,60}$")
if provided_id is not None and not CHARACTER_ID_RE.fullmatch(str(provided_id).strip()):
    raise HTTPException(400, f"角色 id 不合法: {provided_id!r}")
# 并在 character_files.character_dir / concept_files.concept_dir 里加 containment assert
```

---

## P2-5 单并发"占位"竞态（muse / v6_agent）

**位置**：`routers/muse.py:208-220`（`_current` 只在 `_worker` 121-128 赋值）、`muse.py:317-327`（`_vcurrent` 同理 264-270）、`v6_agent.py:212-217`、`v6_agent.py:410-414`（`running` 只认 `status=="running"`）

**触发**：连点两次 `POST /api/v6/muse/sync` / `POST /api/v6/agent/run` / `POST /api/v6/agent/season/{sid}/run`（间隔 < 线程启动时间）。

**为什么错**：检查与占位不在同一时刻完成——请求线程只**读** `_current`，真正的赋值发生在被 spawn 的 worker 线程里。两个请求都能看到 `_current is None`，于是两个 Muse/MuseVideo/agent run 同时上 GPU（MuseTalk ~显存独占；run_template 会并发跑 `render_2d_hq`）。同仓库另外 5 处（`v5_studio.py:717`、`pipeline_ui.py:645`/`1121`、`character_audition.py:792`、`character_promo.py:707`、`character_dialogue.py:744`）都是"请求内持锁先写 active_id 再起线程"，cinema3d 的 `_reserve_job`（cinema3d.py:536-570）更是把 check+insert 写在同一把锁里——只有新增的 muse 与 v6_agent 破了这个约定。`v6_agent` 还额外用 `status=="running"` 过滤，连刚建出的 `queued` job 都不算，窗口更大。

**最小修法**：
```python
with _lock:
    if _current: raise HTTPException(409, ...)
    job_id = ...; _jobs[job_id] = {...}
    _current = job_id          # ← 请求内占位，worker 只负责跑完
threading.Thread(...).start()
```
v6_agent 把两处 `running = [...]` 换成 `if _current_job: 409`，并同样在请求内赋值。

---

## P2-6 `/api/v6/muse/sync` 路径不含 containment 校验

**位置**：`routers/muse.py:186-194`

**触发**：`{"clip": "C:/Windows/System32/drivers/etc/hosts", "audio": "../../secret.wav"}` 或任何 `..` 相对串。

**为什么错**：`clip = (_REPO_ROOT / str(payload["clip"])).resolve()` —— pathlib 里"绝对路径直接获胜"，`..` 也会被 resolve 掉，随后只做 `is_file()` 判断。相比同仓 `v5_studio._safe_file`（100-112）与 `main._resolve_output_file`（982-998）都做了 `resolve().relative_to(base)` 校验，这条新接口没有。当前只把内容喂给 MuseTalk（无直接回显），但 `template_id = clip.parent.parent.name` 会被拿去建 `outputs/muse/<template>/`，把任意目录名带进产物树（如 `tmp`、`Windows`），也给后续"按 template 找产物"的展示引入脏数据。

**最小修法**：
```python
OUTS = (_REPO_ROOT / "outputs").resolve()
def _under_outputs(raw: str) -> Path:
    p = (_REPO_ROOT / str(raw)).resolve()
    try: p.relative_to(OUTS)
    except ValueError: raise HTTPException(400, f"路径必须在 outputs 下: {raw}")
    return p
clip, audio = _under_outputs(payload["clip"]), _under_outputs(payload.get("audio") or "")
```

---

## P2-7 shot 任务无 timeout、全站无取消入口 → job 永久 running

**位置**：`jobs.py:486-487`（`run_cli(cmd)`，timeout 缺省 `None`）；`main.py:1594-1603`（只有 retry，没有 cancel）

**触发**：ComfyUI 卡住 / `pipeline.cli gen-shot` 挂在网络或显存等待上。

**为什么错**：其它类型都给了兜底超时（concept 900、h3 20min、dynamic/localize 1800、export 600、publish 3900），唯独最常用的 `run_shot_job` 没有。进程不退 → `db.wait` 一直 block → job 永远 `running`、shot 永远 `generating`；`retry_job`（jobs.py:1929）对 `status=="running"` 直接返回原样，`enqueue_shot_job` 又会把"已有 active shot job"复用它，于是**这一集再也不能重渲这个镜头**，只能重启后端（重启后 `_recover_stale_jobs` 才标 failed）。全站也没有任何 `DELETE /api/jobs/{jid}`。

**最小修法**：`SHOT_TIMEOUT_SECONDS = 1800`，`run_cli(cmd, timeout=SHOT_TIMEOUT_SECONDS)`；另加 `POST /api/jobs/{jid}/cancel`：置 `job.status="canceled"` + `shot.status="pending"`，并让 `run_job` 在每步 checkpoint 检查 `job.status` 后提前退出。

---

## P2-8 系列导出缓存不失效 + 无并发保护

**位置**：`v5_series.py:214-218`（缓存判定）；`197-207`+`248-297`（无锁写同一组文件）

**触发**：`POST /api/v5/series/{sid}/export?force=false` 两次；或在第一次之后重渲了某一集。

**为什么错**：
```python
if final.is_file() and manifest_path.is_file() and not force:
    return {"cached": True, "duration": cached.get("duration"), "sha1": cached.get("sha1")}
```
只判断"文件存在"，不比较各集 `final.mp4` 的 mtime/sha1 —— **单集重渲后合集永远不变**，而 `manifest.json` 里还留着旧的 `episodes[].sha1`，前端拿到的"缓存命中"是错的。同一函数用固定的 `concat.txt` / `title_card.mp4` / `final.mp4` / `manifest.json` 路径，没有 per-sid 锁：两个并发导出会互相覆盖 concat 列表和输出（`subprocess` 里 ffmpeg 正在读的 list 文件被改 → 内容错乱或 `check=True` 失败 500）。

**最小修法**：
```python
digest = sha1("".join(f"{m['template']}:{(_newest(m['template'],'final.mp4')).stat().st_mtime_ns}" ...))
if cached and cached.get("members_digest") == digest and not force: return cached
with _series_lock(sid):        # 模块级 dict[str, threading.Lock]
    ...                        # 真正的构建
```

---

## P2-9 发布预检"多语种版本"假通过

**位置**：`main.py:2082-2099`

**触发**：只 `POST /episodes/{eid}/localize {"langs":["en"]}`（job 排队、Localization 仍是 `draft`），随后 `GET /episodes/{eid}/publish/preflight`。

**实测**（`tmp/audit_proof2.py`）：
```
F) localize: 200 ['queued']
F) preflight loc check: [{'id':'localization','status':'pass','detail':'英语（1 个版本）'}]
```

**为什么错**：检查只 `SELECT` 了 `Localization.lang` 的去重集合，没看 `status`/`video_url`。于是"仅有一个 draft 记录 / 渲染 failed 的记录"也会被报成"有英语版本"，叠加 #2（en 发布静默发中文母版）就是"预检全绿 + 发出错误语言"的组合。`message` 里的"（1 个版本）"同样会误导运营。

**最小修法**：`langs = {row.lang for row in loc_rows if row.status == "ready" and row.video_url}`；并把 detail 改成区分 `ready/failed/pending`，或在 `/publish` 的 `lang != zh` 分支要求同 lang 的 ready 记录（与 #2 一起改）。

---

## P2-10 季生产：默认 dry_run + `with_muse` 报 ok + bool 陷阱

**位置**：`v6_agent.py:385-386`（`only_missing`/`dry_run` 默认值）、`390-406`、`362-369`（job 状态只按 failed 判定）；`orchestrator.py:117-121,149`（dry_run → `planned`）

**触发**：`POST /api/v6/agent/season/{sid}/run {"with_muse": true}`（漏传 `dry_run:false`）；或前端把布尔传成字符串 `"false"`。

**为什么错**：
1. `dry_run` 默认 True 是刻意的安全默认，但 `with_muse` 分支把 `stages` 改成 `["muse"]`、`only_missing=False` 后**没有**联动 `dry_run`；worker 里 `run_template(dry_run=True)` 让每个 stage 变成 `planned`，而 `_season_worker` 只统计 `status=="failed"` → `job["status"]="ok"`。于是 `GET /api/v6/agent/runs/{id}` 显示"完成/ok"，`episodes[].stages` 却全是 `planned`，用户以为口型已批量跑完。
2. `bool(payload.get("only_missing", True))` / `bool(payload.get("with_muse"))` / `bool(payload.get("muse_apply"))`：body 是裸 `dict`（`Body(default=None)`，无 pydantic 校验），`{"dry_run": "false"}` → `True`、`{"with_muse": "false"}` → **强制只跑 muse**，语义反转。
3. `stage_params` 是 dict 时整体透传没问题，但 `muse_apply` 用 `{**stage_params, "muse": {...}}` 直接覆盖用户在 `stage_params.muse` 里写的其它键（如 `limit`）。

**最小修法**：
```python
def _bool(v, d=False):
    if isinstance(v, str): return v.strip().lower() in ("1","true","yes","on")
    return d if v is None else bool(v)
dry_run = _bool(payload.get("dry_run"), True)
with_muse = _bool(payload.get("with_muse"))
if with_muse and "dry_run" not in payload: dry_run = False   # 或 400 要求显式指定
job["status"] = "failed" if failed else ("planned" if dry_run else "ok")
stage_params = {**stage_params, "muse": {**(stage_params.get("muse") or {}), "apply": True, "reassemble": True}}
```
（`stage_params={"muse":{"apply":true,"reassemble":true}}` 与 `registry.muse_lipsync` 的 `apply/reassemble`（registry.py:388,432）自洽，`muse` stage 默认 `enabled:false` 但 `stages=["muse"]` 走 `explicit` 分支可执行、`depends:[]` 不会被 `_expand` 拉进 shots —— 这部分**没有**问题。）

---

## P3-11 `backend/data/jobs/` 无上限增长

**位置**：`jobs.py:464-465, 646-647, 806, 992-996, 1457-1461, 1567-1568`（每个 job 1–3 个 JSON，写完不删）

**为什么错**：每次 shot/render/dynamic/concept/export/localize 都在 `config.JOBS_TMP_DIR` 落 spec/episode/manifest，退出后既无 TTL 也无数量上限；长期运行后是几万个小文件（Windows 上还拖慢目录操作）。

**最小修法**：`ensure_dirs()` 或启动时按 mtime 清理 >7 天的 `JOBS_TMP_DIR/*.json`；或 `run_job` 的 `finally` 里删掉本 job 的产物。

---

## P3-12 outputs 目录口径不一致（环境变量覆写即失效）

**位置**：写死 `repo/outputs`：`v5_studio.py:86`（fallback）、`206-211`、`708`（`start_render` 的 `out_dir`）、`v6_agent.py:62-64`（`MEDIA_DIRS`）、`muse.py:37-39`（`OUT_ROOT`/`SHOTS_ROOTS`）、`main.py:1062/1065/1083/1095`（manifest 里写死 `"outputs/episodes/..."` 相对串）

**触发**：`ZAOPIANJU_OUTPUTS_DIR` 指向别处（测试隔离、多实例）。

**为什么错**：这些地方读写不同根——例如 `start_render` 把产物写进 `repo/outputs/templates_v5`，而 `_outputs_root()` 读 `config.OUTPUTS_DIR/templates_v5`；导出 manifest 里写死 `outputs/`，`pipeline.capcut` 按仓库根解析 → 找不到镜头片段。测试环境（我这次的 `tmp/audit_outputs*`）就是"写入成功、画廊/导出看不到"。

**最小修法**：统一用 `config.OUTPUTS_DIR`（或 `Path(config.MEDIA_URL_PREFIX).name` 反推），manifest 相对路径用 `path.relative_to(config.REPO_ROOT)` 计算而不是字符串拼接。

---

## P3-13 `GET /api/v5/voice/{character}` 被遮蔽（死路由）

**位置**：`v5_catalog.py:187`（`@router.get("/{kind}/{item_id}")`）先于 `v5_catalog.py:498`（`@router.get("/voice/{character}")`）注册

**证据**：`tmp/audit_routes.py` 导出的真实路由表里，`/api/v5/voice/{character}` 只出现一次（index 71，来自 `v5_studio.py:512`）。v5_catalog 那份永远匹配不到（`/api/v5/voice/xxx` 会先命中最先注册的 `/{kind}/{item_id}`，然后 `_kind_or_404("voice")` → 404）。

**影响**：两份实现并存且行为不同（v5_studio 版有 `needs_relock/stale_locks`，v5_catalog 版没有），谁改错文件谁改不动线上行为。

**最小修法**：删掉 `v5_catalog.py:498-517`（及其 `voice/{character}/lock|preview` 若不想依赖它），保留 v5_studio 一份；或在 v5_catalog 顶部把该路由提到 `/{kind}/{item_id}` 之前。

---

# 检查过、未见问题的部分

- **路由顺序/遮蔽（除 #13）**：`tmp/audit_routes.py` 导出 155 条路由并按注册顺序核对——`/api/v5/{kind}/{item_id}` 之后没有同段数的具体 GET/PUT；`series/{sid}`、`editorial/`、`storyboard/`、`character-showcase/*` 全部先注册；`/media/v5/*` 与 `/media/agent_runs/*` 都在 `app.mount("/media")` 之前注册，不会被静态挂载抢走；`cinema_templates` 的 `@router.get("") + @router.get("/")`（1712-1714）是刻意避免 307 的双装饰器，非重复注册。
- **`backend/app/config.py`**：`_env_path`/`metaso_key`/`save_*_key`/`save_llm_config` 的读-改-写都带 `OSError` 兜底，空值删除行为一致；`media_url()` 越界时回退 `Path(p.name)` 会产出"看起来合法但指错"的 URL（仅当调用方传 outputs 外路径才会触发，现有调用点都在 outputs 内），未单列。未见问题。
- **`backend/app/db.py` / `models.py` / `schemas.py`**：迁移表 `_MIGRATIONS` 与 ORM 列名逐列对齐；`Localization.meta`/`PublishRecord.meta` 用 `meta_json` 列名映射正确；`JobOut` 无 `params`（SSE 不会漏出 manifest/key）；`PublishRecordOut` 对 `meta/platforms` 做了 None→{} 兜底。未见问题。
- **SSE `GET /api/events`（main.py:2512-2542）**：每 2s 用独立 `SessionLocal` 短会话、异常被吞后仍继续推送 `{}`、`model_dump(mode="json")` 保证 datetime 可序列化、150 次后自然结束并由浏览器 `retry: 2000` 重连；未见连接泄漏或会话悬挂问题。
- **`publish_batches.py`**：`_BATCH_LOCK`（RLock）覆盖 check+insert、逐项失败隔离、`_persist` 用 `os.replace` 原子写、启动时把 queued/running 标 failed；与 #5/#8 相反，这里是正确写法。未见问题。
- **`jobs._recover_stale_jobs` / `_mark_failed_isolated`**：按 `job.type` 分别回写 shot/dynamic/episode/localization/publish 状态，与各 `run_*_job` 的改动一致；`run_job` 顶层 `except` 兜底完整。除 #7 的无超时外未见状态机漏洞。
- **`cinema3d._reserve_job`（536-570）/ `_persist_job`（465-474）**：单并发 check+insert 同锁、写盘 `os.replace` 原子、日志刷盘 0.4s 节流但状态变更立即落盘。未见问题。
- **`v5_studio` 媒体路由（434-464）**：`ID_RE`/`TEMPLATE_MEDIA_RE`/`CHAR_MEDIA_RE` 白名单 + `_safe_file` 的 `relative_to` 双重校验，`/media/v5/{template}/{file_path:path}` 无穿越。未见问题。
- **`v5_create`（811-1042 段）**：`_ID_RE` 全量校验、custom 文件存在性判重、删除前检查内建定义与模板引用。未见问题（1386-1472 的删除路径只做了抽查）。

# 未完成全文审查（建议下一轮）

- `routers/cinema_templates.py`（2650 行，只审了 `_publish_*` 段 1940-2650 与 `list_templates`，未审 300-1700 的 job/海报/进度缓存）
- `routers/pipeline_ui.py`（仅审 355-440、批次段；未审 `apply-template` 与 `_ACTIVE` 全路径）
- `routers/character_promo.py` / `character_dialogue.py` / `character_showcase.py`（只核对了 `_ACTIVE_*` 占位模式与 `_PROCS` 生命周期，未审 ffmpeg 拼接细节）

# 复现产物

- `tmp/audit_routes.py` —— 按注册顺序打印 155 条路由（用于遮蔽分析）
- `tmp/audit_proof1.py` —— ①voice 覆盖 ②publish 去重 ③dynamic 回退 ④en 回退
- `tmp/audit_proof2.py` —— ⑤不同 title 仍被去重 ⑥preflight 多语种假通过
- `tmp/audit_proof3.py` —— ⑦角色 id 路径逃逸
- 运行环境：`ZAOPIANJU_*_DIR=tmp/audit_*`、`JOBS_AUTORUN=0`、`V5_CONTRACT_ROOT=tmp/audit_v5`；仓库 `contract/`、`outputs/`、`backend/data/` 未被改动。
