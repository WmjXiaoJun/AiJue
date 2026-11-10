# V52 · 全量代码审查与修复

> 需求（用户 2026-11-05）：**看一下所有代码和逻辑，有什么不对的地方请你完善**。
> 方法：4 个只读审查员（backend / pipeline / agents+contracts / frontend）分头审 → 汇总 → 我逐条修 → 回归验证。
> 明细报告：`docs/audit/REVIEW_BACKEND.md`、`REVIEW_PIPELINE.md`、`REVIEW_AGENTS.md`、`REVIEW_FRONTEND.md`。

---

## 1. 修复清单（本轮已改，均有代码/验证）

### P0

| 问题 | 位置 | 修法 |
|---|---|---|
| 设了 `BREEZE_TTS_KEEP` 时 `style` 未绑定 → 成片一行没渲 | `pipeline/render_v5.py:1028` | 把 `style = _style(...)` 从 `if` 块里提回函数级（已用 AST/源码复核：4 空格缩进） |
| 改角色音色会把 custom-only 角色合同冲成 `{id,voice}`（label/persona/appearance 全丢） | `backend/app/routers/v5_studio.py` `put_voice` | 有内建基线才写 partial；customer-only 读现有文件深合并后整体写；校验失败不落盘（实测 PUT 后 11 个字段全在） |

### P1

| 问题 | 修法 |
|---|---|
| `render_2d_hq` / `muse_lipsync` 永久替换 `templates3d.load_template`（同进程后续全串模板） | 进 `try/finally` 恢复 `orig_load`；`_load_template` 按 `_tid` 分别找 builtin/custom |
| `_semantic_brief` 抛 `SystemExit`（BaseException）绕过 `except Exception` → run/job 永久 running | 改抛 `RuntimeError`；单集/季/Muse 三个 worker 改 `except BaseException` 并写 failed |
| `--stages shotz` 拼错 → 0 阶段、status ok、退出码 0 | `_expand` 对未知 stage / start 直接 `ValueError` |
| Muse 在 V5 模板上会误取 3D 看板镜头并覆盖 | `muse_lipsync` 前置判断 `is_shorts()`，V5 栈直接 skipped 并给原因 |
| 发布多语种/动态版取不到片时静默回退中文母版，记录却写 lang=en | 保留回退（兼容既有 API 契约），但记录写 `meta.video_fallback=true`，预检可据此判失败 |
| 发布去重命中的新 `PublishRecord` 永久 queued（幽灵记录） | 命中时把记录标 `status=duplicate` + error，保留审计但不再永远 queued（`test_publishing` 14 条全过） |

### P2

| 问题 | 修法 |
|---|---|
| 单并发"检查后占位"竞态（连发两次都能过） | Muse sync/video 在请求锁内先占 `_current/_vcurrent`；agent 单集/季把 `queued` 也算占用 |
| `/api/v6/muse/sync` 的 clip/audio 可用 `../` 读任意文件 | 新增 `_safe_input_path`：只允许 `outputs/`、`tmp/` 下；模板 id 走 `ID_RE` |
| 角色创建不校验 id（`..\..\escape_char` 可写出 OUTPUTS_DIR） | 新增 `CHAR_ID_RE` + 拒绝 `..`/斜杠 |
| 季 run 的 `with_muse`/`dry_run` 用 `bool("false")==True` 反转语义 | 新增 `_as_bool()` 严格解析 |
| 季 run 目录没有季级证据，前端三链接必然 404；media 正则 40 字符上限 | 季目录写 `run.json`/`summary.md`/`events.jsonl`，返回三个 URL；正则放宽到 6–80 |
| 合集导出缓存只看文件存在 → 单集重渲后合集不变；且 hd 覆盖横版 manifest | 缓存比对每集 sha1 + variant；manifest 按变体分流（manifest.json / _1080p / _vertical） |
| `utils.read_json` 遇 BOM/GBK/UnicodeDecodeError 直接抛；`write_json` 会写出 NaN | `read_json` 依次尝试 utf-8-sig/utf-8/gbk；`write_json` 递归把 NaN/Inf 转 null 并 `allow_nan=False` |
| `probe_video` 只 catch `ZeroDivisionError`，ffprobe 给 `N/A` 时 500 | 同时 catch `ValueError` |
| 14 处 `Path.write_text/read_text` 未指定编码（中文 Windows 默认 cp936） | 批量补 `encoding="utf-8"`（ass_builder/capcut/dynamic/edit_v4/h3_comfy/localize/mock_shot/render/tts_engine） |
| Muse 复用旧产物时从"已改过的镜头"再取样（越改越差） | 从 `shots/_orig/` 原件取样，回写才用目标路径（实测日志出现"原件"） |
| 跨进程锁会因 mtime>900s 抢占**活着**的 owner；释放时不校验 owner | 只回收死进程；活进程需 >6 小时；释放前校验 pid |
| 同步分在无脸时仍 `ok:true` 且 `motion_mean=NaN` | 有效帧 <8 或无脸 → `ok:false`；NaN 一律转为 None |
| `measure_sync` 把权重可读当成"已跑 SyncNet" | 只标 `mouth_audio_corr`，SyncNet 字段仅报 ready/reason |
| 工作流 `stages` 浅拷贝，原地改 params 会污染 DEFAULT_STAGES | 改 `copy.deepcopy` |
| `save_workflow` 不校验 id（`../evil` 可写出 contract/v6） | 加 `^[a-z][a-z0-9_]{1,60}$` 白名单 |
| `latest_runs` 按路径字典序，`season_` 恒压过更新的单集 run | 改按文件 mtime 排序 |
| `patch_template` 只认内建目录，11 个 custom 模板改不了 | builtin 不存在时回落 `custom/templates` |
| `deliver.py` 音频可长于视频（无 `-shortest`）；`--hd` 是死参数 | 补 `-shortest`；新增 `--sd` 并真正透传 |
| `GET /api/v5/voice/{character}` 在 v5_catalog 被 `/{kind}/{item_id}` 遮蔽（死路由） | 删掉重复实现，保留 v5_studio 的完整版并留注释 |

---

## 2. 未修（记入 backlog，附原因）

| 项 | 原因 |
|---|---|
| `templates3d_hq` 缓存 key 不含 prompt/seed，改 prompt 重渲仍命中旧关键帧 | 需要给 hq 增加 `--force` 语义并改缓存目录命名，影响存量 tmp 缓存，单独一轮做 |
| `render_v5` 台词镜不封顶 / `_trim_to_target` 整镜删除 | 涉及节奏算法，需带 A/B 样片验证 |
| `qc_v5` 的 lip_sync 硬门永远缺失 | 需要先把 motion2d 的 viseme 时间轴上报到 qc_extra，再开门 |
| 视频 raw 拷贝失败回退旧素材 | 需改成硬失败并清理旧文件，影响长片重跑策略 |
| `set_voice` 改的是全局角色（所有模板共享音色） | 属产品语义问题（per-template 覆盖要新增合同字段） |
| 季合同 `same_cast:true` 与三集实际 cast 不一致 | 内容侧决定：要么改合同，要么重选角；不该由代码替用户改 |
| 发布任务 spec JSON 无 TTL、outputs 目录写死仓库路径 | 运维项，不影响正确性 |
| SyncNet LSE 分、Muse apply 原子回滚 | 前者缺权重，后者需要事务化镜头替换方案 |
| 前端 P1/P2 竞态与季证据链接 | 已交前端审查员按报告修（本轮并行进行） |

---

## 3. 验证

| 验证 | 结果 |
|---|---|
| `python -m pytest tests -q` | **491 passed / 36 skipped / 0 failed**（改动前后一致） |
| 全部 GET 端点探针（58 条） | 0 个 5xx（404/400 均为缺真实 id 的预期响应） |
| 季证据 | `/media/agent_runs/season_.../{summary.md,run.json,events.jsonl}` 全 200 |
| 合集三变体 | force 后 `horizontal_16x9 / horizontal_1080p / vertical_9x16` 各自 manifest；再调 `cached:true` 且 sha 对齐 |
| Muse 幂等 | 二次 apply 日志出现"原件"，`shots/_orig` 保留原始镜头 |
| 角色音色 PUT | 写入后 custom 角色 11 个字段齐全（修复前只剩 id/voice） |

---

## 4. 结论

- 两个 P0（渲染分支缩进、音色写入毁数据）已修并复验；
- 6 条 P1（模板替换污染、SystemExit 卡 job、阶段名静默、Muse 越栈、发布静默回退、幽灵发布记录）已修；
- 16 条 P2/P3 已修；剩余项都是需要产品决策或单独一轮工程量的，已在 §2 列清。

---

## 5. 前端修复（同日，agent-ui）

报告：`docs/audit/REVIEW_FRONTEND.md`（14 条）。已修：

- **P1 工作流加载竞态**：`wfSeqRef` 序号守卫 + 保存前比对 draft 归属 + 后端回显 template 比对（实测延迟 history_talk 响应后保存到 duologue，PUT 只打了 duologue）；
- **P1 季 run `job_id=null`**：直接提示「全部集数已出片」，不再进 queued 锁死；
- **P2**：切模板不丢进行中的 run（`jobTemplate` + 警告条）；季切换加序号守卫；口型失败项不再渲染空 `href`；`boot()` 的 404 不再误判离线。
- **P3**：provider 下拉补 replicate/useapi_muse；季 `last_run.status` 显示；`stage_params` / `muse_apply` 透传；`loadAll` try/finally；非对象 JSON 明确报错；Muse chip hover 关闭；空 url 占位；轮询 404 自动解除运行态。

构建：`index-DsTboWjp.js 697.07 kB / gzip 206.70 kB`，`index-BcrHjtIw.css 346.45 kB / gzip 54.80 kB`；截图 `tmp/ui/v49/agent_v52.png`。

---

## 6. 补修（2026-11-05 第二轮：把 §2 的“未修”全部修掉）

| 原 backlog | 本轮修法 | 验证 |
|---|---|---|
| `templates3d_hq` 缓存不含 prompt → 改台词仍吃旧关键帧 | 缓存有效性加入 **prompt 指纹（crc32）+ base_seed**；`hq.main` 新增 `--force`（忽略关键帧与镜头缓存），`render_2d_hq` 在 `ctx.force` 时透传 | 编译 + 逻辑复核；旧 meta 缺 `prompt_sig` 会触发一次重建（预期） |
| `render_v5` 台词镜不封顶 + 整镜删 b-roll | 语音优先：不再用 `min()` 假封顶，超长记 `over_max_sec` 并告警；`_trim_to_target` 改为**先缩短 b-roll（≥1.2s）**，不再直接删镜 | 编译 |
| `qc_v5` 的 `lip_sync` 硬门永远缺失 | `run_qc` 统计对白镜 `speech_duration`，写 `qc_extra["lip_sync"]{ok,provider:viseme,shots}`；缺语音直接判失败 | 编译 + 数据流复核 |
| `video_raw`/`narration` 拷贝失败回退旧素材 | 改为 `RenderV5Error` 硬失败（拒绝用上一轮画面/音轨出片） | 编译 |
| `set_voice` 改全局角色却不告知 | 返回 `scope:"global"` + `affected_templates`（所有用到该角色的模板） | 编译 |
| 季合同 `same_cast:true` 与实际 cast 不一致 | 合同改为 `same_cast:false, same_world:true` + `cast_note`；`/api/v6/agent/series` 返回 `season_warnings`；新增 `scripts/check_seasons.py`（cast/风格/集数自检） | 实测 warnings=0；脚本 exit 0 |
| 工作流 JSON 损坏静默回退默认栈 | `load_workflow` 返回 `degraded/degraded_reason`，`run_template` 直接拒绝执行并说明 | 编译 |
| muse apply 非原子、无回滚 | 临时文件 + `os.replace`；回写失败或重合成失败都从 `_orig/` 恢复；成功后**刷新 QC** | 编译；幂等实测见 V51 |
| 同步分没考虑 `start` 偏移 | `_audio_env`/`mouth_audio_sync`/`measure_sync` 增加 `start`，API 与 agent 透传 | 编译 |
| `mouth_sync` 的 `lead` 未使用、字符 `i%len` 循环 | `lead` 帧强制闭口；字符位置按「发声进度」比例映射 | 实测：lead=0.2 时前 0.2s 全 closed |
| `render_v5` 用 `except TypeError` 兜底旧版 motion2d（吞真错误） | 改用 `inspect.signature` 判断 `mouth_strength`，不再捕获 TypeError | 编译 |
| gen-shot `run_cli` 无超时 | 加 `GEN_SHOT_TIMEOUT`（默认 3600s） | `test_jobs` 适配并全过 |
| job spec/manifest 永不清理 | 新增 `_prune_job_files(keep=300)`，每次写 spec 后调用 | 编译 |
| 输出目录写死仓库 `outputs/` | 路由统一用 `app.config.OUTPUTS_DIR`；pipeline 侧用 `ZAOPIANJU_OUTPUTS_DIR` 优先的 `_outputs_root()` | `import app.main` OK；58 条 GET 0 个 5xx |
| 预检把 draft 本地化当“已有版本” | 只统计 `status=ready` 且 `video_url` 非空的记录 | 编译 |
| SyncNet 权重损坏却无法自检/重下 | 新增 `scripts/fetch_syncnet.py`（校验 zip 完整性；`SYNCNET_URL` 可下载重装）；`available()` 如实报 `syncnet_ready=false` + reason | 实测 `--check` 报 BAD（845MB 不完整）并给指引 |

### 仍未闭环的唯一一项（不是代码问题）

- `local_models/lipsync_models/syncnet/latentsync_syncnet.pt` 是**下载中断的 845MB 残缺文件**（zip 中央目录缺失）。
  代码侧已做：不冒充分数、状态可查、下载/校验脚本就绪。
  要出真正的 LSE-C/LSE-D，需要你提供可用的权重（或给 `SYNCNET_URL`），再补 30 行推理调用（SyncNet config 已在 `local_models/lipsync/MuseTalk/configs/training/syncnet.yaml`）。

### 本轮验证

| 项 | 结果 |
|---|---|
| `pytest tests -q` | **491 passed / 36 skipped / 0 failed** |
| `python -c "import app.main"` | OK（config 模块不再混用） |
| 58 条 GET 探针 | 0 个 5xx |
| `/api/v6/agent/series` | `season_warnings=[]`，3 集全 ready |
| `scripts/check_seasons.py` | `[OK] undercity_arc · 3 集`，exit 0 |
| `scripts/fetch_syncnet.py --check` | 如实报 BAD + 指引，exit 2 |

### 补修过程中发现并修掉的回归

- 第一轮批量补 `encoding="utf-8"` 的脚本误伤了 `utils.write_text(path, text)`（它本身已是 UTF-8），
  导致 `templates3d.assemble` 里 `write_text() got an unexpected keyword argument encoding` —— 已从 9 个文件 14 处移除多余参数；
  **Muse apply 的回滚机制在这次失败里被真实触发并验证**（`muse 重合成失败，已回滚镜头`），修复后完整链路重跑通过：回写 → 合成 14.142s → QC 已刷新。

---

## 7. 第三轮（第二轮审查的回归修复，2026-11-05）

第二轮审查（`REVIEW_{BACKEND,PIPELINE,AGENTS}_ROUND2.md`）指出**我自己的修复引入了 3 个 P0**，已全部修掉并验证。

### P0（全部是我上一轮改出来的）

| 问题 | 后果 | 修法 | 验证 |
|---|---|---|---|
| `muse_sync` 里 `_current = job_id` 没有 `global` | `POST /api/v6/muse/sync` 100% 500（UnboundLocalError） | 函数首行补 `global _current` | 实测 200，job `muse_f3d1d5c089` |
| `muse_video_generate` 同样缺 `global _vcurrent` | `/api/v6/muse/video` 每次 500 | 补 `global _vcurrent` | 实测 200 |
| `/muse/video` 的 `template`/`name` 未校验就拼输出路径 | `../..` 可让 ffmpeg 写出输出目录 | `ID_RE` 校验 template、`name` 白名单化、`out.resolve()` 前缀断言 | `../../evil` 被 400；`name=../../evil` 落成 `muse_video/evil.mp4` |

### P1

| 问题 | 修法 |
|---|---|
| 输出根三处口径不一（`templates3d.OUT3D` 写仓库、registry 读 env）→ 设 `ZAOPIANJU_OUTPUTS_DIR` 后 QC 对好片报全红 | 新增 `pipeline.config.OUTPUTS_ROOT`（env 优先），`templates3d`/`registry`/`lipsync`/`muse_video`/`orchestrator`/`deliver` 全部改读它；v5_series 写路径也统一 `app.config.OUTPUTS_DIR` |
| `hq._render` 的体积短路先于 force → `--force` 无效，还把旧图写成新 `prompt_sig`（缓存毒化） | `_render` 加 `not force` 条件 |
| trim 改“缩 b-roll”却没重建音轨 → 音画错位（实测 drift 3.4s） | trim 标记 `_trimmed_duration`，随后按新时长重建静音轨并更新 meta |
| `patch_workflow` 不看 `degraded` → 合同坏了就把默认 12 阶段覆盖进用户文件（砖化） | degraded 直接 `ValueError` |

### P2

| 问题 | 修法 |
|---|---|
| 新 `lip_sync` 门恒真（只判 speech_duration） | 用 `mouth_sync.envelope_timeline + coverage_ratio` 真算张嘴覆盖，<5% 判失败，报告里带每镜 coverage |
| `save_workflow` 不校验悬空依赖/未知 tool/重复 id | 加 `_validate`（依赖必须存在、tool 必须在 `TOOLS`） |
| 悬空依赖被静默 skipped，run 仍 ok | 依赖缺失 → `failed` + 中断 |
| 镜头重渲后 muse 仍用旧 `_orig`/`_muse` 覆盖 | `render_2d_hq` 在 force/非 reuse 时清理 `shots/_orig` 与 `outputs/muse/<tid>` |
| muse 后 QC 失败被吞 → “QC 红、run 绿” | QC 失败直接抛错 |
| `video_fallback` 恒 False（候选表本就含 final.mp4） | 改为“非 zh/final 且最终落到 final.mp4”才算 fallback |
| 季 dry-run 也返回 status=ok | dry-run 返回 `planned`；`with_muse` 默认改真跑（`dry_run` 显式优先） |
| `check_seasons` 假绿（None 风格被过滤、不校验 episode_sec/acts/same_world） | 补齐四项校验 |
| `pipeline_ui` 读合同先内建后 custom 且无条件 break → custom 覆盖失效 | custom 优先，读到才 break |
| 角色试镜/宣传/对谈的“检查-占位”跨两个锁块 | 插入前在同一把锁里二次检查；`_JOBS/_PROMOS/_DIALOGUES` 上限 50 |
| 秀场 zip 缓存只比 title/mode | 加 `reel_sig=(size,mtime)` |
| `gen_relic_template` / `gen_agent_workflows` 的 `REPO=Path(".")` | 改 `parents[1]` |
| `localize.prepare_source` 对 ja/zh 也罗马化人名 | 目标语言为 zh/ja 时保持原名 |
| `migrate_v50_musev_stage` 无 `__main__` 守卫、musev 顺序在 muse 前 | 加 `main()` 守卫；按 `SHORTS_STAGES` 顺序重排（33 份合同已修正，二次运行 0 变更） |
| `_prune_job_files` 可能删正在读的 spec | 只删“超量且 >7 天” |

### 验证

| 项 | 结果 |
|---|---|
| `pytest tests -q` | **501 passed / 36 skipped / 0 failed**（新增 `tests/test_v53_agents.py` 10 条） |
| 真实 V5 渲染（tonight_show，11 镜） | 成片 15.45MB / 41.6s / 1280×720；QC `lip_sync: 7 个对白镜 viseme 覆盖达标` |
| `/api/v6/muse/sync`、`/api/v6/muse/video` | 200；恶意 template 400；恶意 name 落盘在 `outputs/muse_video/` |
| 合集导出 | 并发有锁；二次调用 `cached=true` 且 sha 不变 |
| 58 条 GET 探针 | 0 个 5xx |
| `check_seasons.py` | `[OK] undercity_arc · 3 集` |

> 备注：验证 `set_voice` 返回结构时误触了一次写入（把 `guanyu` 音色改成 `legacy_v4_laozhou`），已用 `git checkout -- contract/v5/characters/guanyu.json` 还原，仓库当前无该文件改动。
