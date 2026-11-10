# 编排层与合同审查（V49 · 第三轮 · 增量）

- 范围：`pipeline/agents/{registry,orchestrator,workflow,ops,roles,cli,art,__init__,__main__}.py`、`contract/v6/workflows/*.json`(33)、`contract/v6/art/pose_policy.json`、`contract/v5/series/*.json`(1)、`backend/app/routers/v6_agent.py`，验收标准读 `docs/PLAN_V49_SHORTS_SEASON.md`。
- 本轮定位：**对前两轮 27 条逐条回归**（已修 / 部分修 / 未修 / 回归），只报新发现。
- 方法：全部结论都有真跑证据——只读枚举 + FastAPI `TestClient` + 打桩（patch `new_context` / `call_tool` / `run_template`，绕开 GPU），脚本落在 `docs/audit/_tools/r3_*.py`。**没有跑任何真渲染**。
- 副作用（已复原，见文末「审查副作用」）：探针触发过一次 `apply_op(set_stage_param)`，会把合同重写成等价字节但丢尾换行；已补回，`git diff --numstat` 恢复成与同族文件一致的 `31/2`。

## 概览

| 项目 | 实测值 | 命令 |
|---|---|---|
| 工作流合同 | **33** 份（`*.json`，含递归） | `(Get-ChildItem contract/v6/workflows -Recurse -Filter *.json).Count` |
| 模板合同 | 33 = **21 base + 12 custom**（`contract/v5/templates` 21 + `contract/v5/custom/templates` 12，含 `custom_holo_talk`） | `r3_enum_templates_stacks.py` |
| 栈分布 | **22 `v5_render` + 11 `hq_shorts`**，与 `is_shorts()` 判定 100% 一致（0 mismatch） | 同上 |
| 季合同 | 1 份 `undercity_arc.json`（3 集，`season` 块有 `target_count/rules`，**无 `episodes[]`**） | `read` |
| 阶段/工具一致性 | JSON 里的 tool **全部**在 `TOOLS`（0 缺失）；`TOOLS` 里 **`set_pose` 无人引用** | `r3_enum_tools_vs_workflows.py` |
| 依赖图 | 0 悬空 depends / 0 重复 stage id / 0 环 / 0「enabled 依赖 disabled」 | `r3_enum_deps_drift.py` |
| 合同 vs 代码常量 | 33 份 stage 定义与 `DEFAULT_STAGES`/`SHORTS_STAGES` **零漂移** | 同上 |

**结论：新增 8 条 P1（P1-7 按 lead 复核拆成 7a=代码 / 7b=文档）/ 17 条 P2 / 7 条 P3；前两轮 27 条 = 已修 17 · 部分修 5 · 未修 2 · 不在本轮范围 3。**

> **lead 复核结论已并入**：① **P1-3 定性为「两个真实调用方并发」**（保持 P1，见其「定性」段）；② **P1-7 拆分**为 **P1-7a（代码缺陷：`qc_report.json` 双写 + 时长上限门）** 与 **P1-7b（文档事实错误：PLAN 第 3 节 `templates_2d`）**，修法方向按 lead 拍板「**以代码为准、文档跟进**」。

**对 lead 基线的两处校正**
1. `contract/v6/workflows/` 实测 **33 份**，不是 35（`Get-ChildItem` 平铺与递归都是 33，见上表；没有子目录、没有 `.broken` 残留）。
2. `contract/v5/custom/templates/` 12 份 ✓ 正确，但其中 `custom_holo_talk` 走 V5 栈（`is_shorts=False`），所以「12 个 custom 短片」严格说只有 11 个短片 + 1 个 V5 示例。

---

## 前两轮回归核对表

### REVIEW_AGENTS.md（第一轮 15 条）

| # | 原标题 | 现状 | 现在的证据 |
|---|---|---|---|
| 1 | `render_2d_hq/muse` 永久替换 `t3.load_template` | **已修** | `registry.py:266-276` `_load_template(_tid)` 尊重入参；`:314-316`、`:501-515` 都在 `finally` 里 `t3.load_template = orig_load` |
| 2 | `check_story` 抛 SystemExit 穿透 `except Exception` | **部分修** | `registry.py:211` 已改 `RuntimeError`；`v6_agent.py:199/406/446` 已 `except BaseException`。**但 `orchestrator.py:153` 仍是 `except Exception`** → 任何工具抛 SystemExit 时 run 证据停摆（实测见 P2-9） |
| 3 | `--stages` 拼错 → 0 阶段 status ok 退出 0 | **已修** | `orchestrator.py:44-47` 抛 ValueError；`cli.py:72-74` 捕获 → `[error] 未知阶段: ['asemble']` + `exit=2`（`r3_cli_plans.py` 段 D）。**衍生新问题见 P2-1** |
| 4 | muse 在 V5 栈串到 templates_3d 产线 | **部分修** | `registry.py:408-410` 非短片直接 `{"skipped": True}` 早退，不再碰 3D 看板。**但 skipped 仍被记为 `status="ok"`**（`orchestrator.py:149` 不看返回值）→ 见 P1-5 |
| 5 | muse apply 无回滚、非原子 | **已修** | `registry.py:461-487`：`tmp + os.replace` 原子替换、`replaced` 列表 + `shutil.copy2` 回滚、`_orig` 只建一次 |
| 6 | run.json 裸 `json.dumps`（Path/NaN） | **已修** | `orchestrator.py:98-102` 改 `utils.write_json(..., indent=2)` + try/except；`utils.write_json` 已 `_finite + allow_nan=False` |
| 7 | 工作流 JSON 坏了静默回退默认栈 | **已修** | `workflow.py:136-142` 置 `degraded`；`orchestrator.py:76-78` 先拒绝再建目录；`ops.py:117-119` 拒绝改写 |
| 8 | `default_workflow` 浅拷贝污染常量 | **已修** | `workflow.py:119` `copy.deepcopy(stages)` |
| 9 | edit 只认内建目录（custom 一律 FileNotFound） | **部分修** | `patch_template` 已回退 custom（`ops.py:98-102`）；**`set_voice` 未修**：`ops.py:42-43` 只拼 `contract/v5/characters/`，实测 `custom_old_inventor` `base=False custom=True` |
| 10 | set_voice 忽略 template、全局改音色 | **部分修** | `ops.py:66-73` 新增 `scope:"global"` + `affected_templates`；**`rerun` 仍只拼到前 5 个模板** |
| 11 | 季 same_cast 假绿、无校验 | **已修** | 数据 `same_cast: false`（`undercity_arc.json:35`）；`v6_agent.py:309-316` 新增交集告警 |
| 12 | `latest_runs` 按路径字典序 | **已修** | `orchestrator.py:203-213` 改 `st_mtime` 排序。**衍生新问题见 P3-1（季 run.json 混入，run_id=None）** |
| 13 | muse `depends: []` → 换片不重跑 qc/publish | **未修** | `workflow.py:41-44`、`:75-78` 仍是 `depends: []`，33 份合同也都是 `[]`。仅补了 muse 内部 `evaluate_hq_qc`（`registry.py:519`），`publish`/`template_summary.json` 仍不更新 → P1-5 |
| 14 | render_2d_hq 把 duration/bytes 写成字符串 | **已修** | `registry.py:330-338` `_num()` 强转 float/int |
| 15 | save_workflow 不校验 id/stages | **已修** | `workflow.py:146-168` 校验 id 白名单 + 非空 + 重复 id + 依赖存在 + tool∈TOOLS。**残余：不查环、不查 stack** → P2-3 |

### REVIEW_AGENTS_ROUND2.md（第二轮 12 条）

| # | 原标题 | 现状 | 现在的证据 |
|---|---|---|---|
| 1 | 输出根三处口径不一 | **已修（残 1 处）** | `config.OUTPUTS_ROOT` 单一真相（`config.py:11`），`templates3d.py:33` `OUT3D = config.OUTPUTS_ROOT/"templates_3d"`；实测 `_outputs_root()/"templates_3d" == OUT3D → True`。**`orchestrator.py:169-172` 仍硬编码 `REPO/"outputs"`** → P2-15 |
| 2 | patch_workflow 在 degraded 合同上覆盖并砖化 | **已修** | `ops.py:117-119` `if wf.get("degraded"): raise ValueError(...)` |
| 3 | 悬空 depends / 未知工具能存能跑、run 照样 ok | **已修** | `workflow.py:160-168` 保存期拦截；`orchestrator.py:120-126` 运行期 missing → `failed` + `break`（打桩实测：`[('a','ok'),('b','ok'),('c','skipped'),('d','failed','依赖未完成或不存在: [c]')]`，`run.status=failed`） |
| 4 | muse 原件/缓存不随 shots 重渲失效 | **部分修** | `registry.py:321-326` 仅在 `ctx.force or not reuse_shots` 时清 `shots/_orig` + `outputs/muse/<tid>`；`reuse_shots=true`（默认）下单镜因 prompt 变更重渲时不清理 → P2-16 |
| 5 | muse QC 刷新失败被吞成成功 | **已修** | `registry.py:518-520` `qc = evaluate_hq_qc(ctx, {})` 无 try，注释「失败必须冒泡」 |
| 6 | 季自检脚本假绿 | **已修（脚本侧）** | `scripts/check_seasons.py:43-70` 已覆盖 same_style(None 也算差)/same_world/episode_sec/acts/target_count。**编排层 `_series_entry` 只做 same_cast+same_style 两件** → P2-13（双份逻辑漂移） |
| 7 | tests 往真实 `contract/v6/workflows/` 写坏合同 | **不在本轮范围**（tests/） | 现场无 `zzz_broken_test.json` 残留，33 份合同干净 |
| 8 | 两条测试恒真 | **不在本轮范围**（tests/） | — |
| 9 | 测试 monkeypatch `_outputs_root` 掩盖第 1 条 | **不在本轮范围**（tests/） | — |
| 10 | degraded 留空 run 目录；`latest_runs` stat 无 try | **已修** | `orchestrator.py:75-81` load/degraded 先于 `mkdir`；`:207-211` `try/except OSError`。**同类残留：`_expand` 失败仍留 running 目录** → P2-1 |
| 11 | set_voice rerun 只给一条 | **未修** | `ops.py:70-73` `[:5]`，`laozhou` 实测出现在 29 个模板 cast 里 |
| 12 | migrate_v50_musev 无 `__main__`、插入顺序反 | **已修** | `scripts/migrate_v50_musev_stage.py:16` `def main()`、`:53` `if __name__`、`:38-44` 锚点改成 muse 之后，`:27-32` 还会纠正已有文件的错序 |

---

## P0

**无。** 判定口径：P0 = 已在正常使用路径上稳定毁数据/毁成片/无法恢复。本轮 7 条 P1 都满足「一行修复 / 有明确绕过」的条件：
- 穿越（P1-1）只读、且 `/api/v6/*` 本来就无鉴权；
- 撞车（P1-3）需要同模板同秒并发，当前 33 个模板的真实使用是单发；
- `with_muse` 真跑（P1-4）虽然在默认路径上，但前端目前发的是显式 `dry_run`。

真正会让人「以为交付了、其实没交付」的是 P1-5（muse 空跑全绿）与 P1-7a/7b（验收门与 PLAN 口径不一致），但它们可被 PLAN 的逐条验收当场抓住，不构成 P0。

---

## P1

### P1-1 `season/{sid}` 没有 id 校验：Windows 下用 `..%5C` 可读任意 `*.json`（路径穿越）
- **位置**：`backend/app/routers/v6_agent.py:278-282`（`_series_entry` 直接 `SERIES_DIR / f"{sid}.json"`）；入口 `:460`（`season_run`）、`:512`（`season_status`）
- **问题**：`sid` 是原始 path 参数，不做 `ID_RE` 校验。Starlette 不解码 `%5C`、不拦反斜杠，Windows 上 `\` 是路径分隔符 → `..\..\v6\art\pose_policy` 能落到 `contract/v6/art/pose_policy.json`。
- **证据**：
  ```bash
  python docs/audit/_tools/r3_api_media_traversal.py     # 段 2
  # GET /api/v6/agent/season/..%5C..%5Cv6%5Cart%5Cpose_policy/status
  #   -> 200 {"ok":true,"series":{"id":"pose_policy","label":"pose_policy",...}}
  # GET /api/v6/agent/season/..%2F..%2Fcontract%2F...  -> 404（正斜杠被 starlette 拦）
  # GET /api/v6/agent/season/undercity_arc/status       -> 200（正常）
  ```
- **影响**：无鉴权的只读文件泄露（任意 `*.json`，上限 404 报错文案还会回显拼接后的相对路径）；顺带把 `contract/v5/series` 之外的文件当季合同解析，前端会拿到 `members: []` 的假季。同一文件里 `/media/agent_runs/{run_id}/{file}` 做了 `resolve() + startswith(root)`，两个入口安全策略不一致。
- **建议修法**：`if not ID_RE.match(sid): raise HTTPException(404, "非法系列 id")`（和 `_tpl` 同款）；`path = (SERIES_DIR / f"{sid}.json").resolve()` 后再断言 `path.is_relative_to(SERIES_DIR.resolve())`。

### P1-2 `--stages publish` / `--stages qc` 的依赖闭包会自动重跑 GPU 阶段
- **位置**：`pipeline/agents/orchestrator.py:52-63`（`_expand` 把所有依赖加进 `want` 并**照常执行**）
- **问题**：依赖展开是「补进待执行列表」，不是「假定已完成」。于是「只发布」会先把 story→shots 全部重跑一遍；缓存能挡住大部分，但 `--force`、缓存键变化（改 brief/prompt）、或 `reuse_shots` 被关掉时就是真重渲。
- **证据**：
  ```bash
  python docs/audit/_tools/r3_cli_plans.py
  # A 短片 --stages publish --dry-run
  #   exit=0 status=planned stages=[('story','planned'),('shots','planned'),('qc','planned'),('publish','planned')]
  # C V5  --stages qc --dry-run
  #   stages=[story,voice,editorial,shots,assemble,qc]
  ```
  短片 `shots` = `render_2d_hq`（约 6–10 分钟 GPU，`registry.py:256-338`），V5 `shots` = `render_shots`。
- **影响**：产品文案（AGENTS.md §3「`--stages shots,qc,publish`」「op 会返回受影响的 stages；只跑必要阶段」）与实际语义相反：最贵的阶段会被顺手拉起来。一次误操作 = 一集 GPU 预算。
- **建议修法**：区分「依赖已就绪」与「依赖要执行」：`_expand` 补进来的依赖若已有产物（`shots/` 非空、`episode.json` 存在、`qc_shorts.json` 存在……）就标记 `status="cached"` 直接跳过；或给 `--stages` 加 `--no-deps`（只校验依赖产物是否齐全，不执行）。至少把闭包里补进来的阶段在 run.json 里标成 `implicit`，日志明示「因依赖自动加入」。

### P1-3 `run_id` 只有秒级精度：同模板同秒并发直接撞车，证据被覆盖（本轮实测到真实撞车）
- **位置**：`pipeline/agents/orchestrator.py:79-81`
  ```python
  run_id = time.strftime("%Y%m%d_%H%M%S") + f"_{template_id}"
  run_dir = run_root / run_id
  run_dir.mkdir(parents=True, exist_ok=True)      # exist_ok=True → 撞车不报错
  ```
- **问题**：无 pid/uuid 后缀，`mkdir(exist_ok=True)` 把撞车变成静默复用：`events.jsonl` 以 `"a"` 追加（`orchestrator.py:95-96`）、`run.json`/`summary.md`/`workflow.json` 直接覆盖（`:90`、`:100`、`:188`）。API 的「单并发」只是进程内 `_jobs` 扫描（`v6_agent.py:225-230`），CLI 与 API、多 uvicorn worker、季 worker 跨进程都不受约束。
- **证据 1（打桩确定性复现）**：
  ```bash
  python docs/audit/_tools/r3_orch_semantics.py    # 段 F
  # run_id1 = 20261106_232732_demo | run_id2 = 20261106_232732_demo | same dir = True
  ```
- **证据 2（本轮真实撞车，非打桩）**：
  ```bash
  Get-Content outputs/agent_runs/20261106_233032_tonight_show/events.jsonl
  # {"kind":"start",...}  ×6 plan …{"kind":"end","status":"planned"}
  # {"kind":"start",...}                    ← 第二个进程在同一秒又 start 了一次
  Get-Content outputs/agent_runs/20261106_233032_tonight_show/run.json
  # status: "running", stages: []           ← 后一次覆盖了前一次的证据
  Get-Content outputs/agent_runs/20261106_233032_tonight_show/summary.md
  # 仍然是前一次那 6 个 planned 阶段的表格
  ```
  同批还出现成对目录，证明是「两个进程各跑了一遍同一个 dry-run 循环」而不是一次 run 写了两遍：
  ```bash
  python -c "from pathlib import Path; [print(p.name) for p in sorted(Path('outputs/agent_runs').iterdir()) if p.is_dir() and '20261106_2330' <= p.name[:15] <= '20261106_2333']"
  # 20261106_233028_custom_tpl_v46_duologue
  # 20261106_233029_custom_tpl_v46_duologue / 20261106_233029_tonight_show
  # 20261106_233032_custom_tpl_v46_duologue / 20261106_233032_tonight_show   ← 本次撞车的一对
  # 20261106_233351_custom_tpl_v46_duologue / 20261106_233351_tonight_show
  ```
- **定性（lead 复核确认）**：证据由本轮两个审查进程无意并发产生（同一秒、两个独立 python 进程、走 `run_template` 默认 `RUN_ROOT`），即**两个真实调用方**——与「UI 连点 / CLI 与页面同时触发」同构；不是单进程内线程。因此这条既是可复现证据，也不需要假设产品内部存在并发。
- **影响**：run.json 与 summary.md / events.jsonl 互相矛盾（一个目录两套 run），`latest_runs` 会读到 `status=running` 的僵尸；正式产物（`outputs/templates_3d/<tid>`）也会被两个进程同时写。
- **建议修法**：`run_id = time.strftime("%Y%m%d_%H%M%S") + "_" + template_id + "_" + os.urandom(4).hex()`（4 字节随机后缀），配合 `run_dir.mkdir(parents=True, exist_ok=False)`，捕获 `FileExistsError` 后重试 3 次；比加跨进程锁简单，且对「多进程 / CLI+页面 / 多 uvicorn worker」一律有效。

### P1-4 季 `with_muse=true` 会把 `dry_run` 默认翻成真跑，并静默吞掉用户传的 `stages`
- **位置**：`backend/app/routers/v6_agent.py:462-464`、`:474-480`
  ```python
  dry_run = _as_bool(payload.get("dry_run"), not with_muse)   # with_muse → 默认 False
  ...
  if with_muse:
      stages = ["muse"]        # 用户传的 stages 被丢弃
      only_missing = False     # 用户传的 only_missing 被丢弃
  ```
- **问题**：AGENTS.md §4/§5 与 PLAN M3 都写「`dry_run` 默认 true」，但一个只带 `{"with_muse": true}` 的 POST（前端勾一个开关就会发出）等于「对全部集数真跑 GPU」。
- **证据**：
  ```bash
  python docs/audit/_tools/r3_api_season.py     # 段 D2 / D3（打桩 run_template，只记录参数）
  # D2 {"with_muse":true} -> dry_run=False, episodes=3 集
  # run_template 收到: [{template:'...undercity', 'stages':['muse'], 'dry_run':False}, ×3]
  # D3 {"with_muse":true,"muse_apply":true,"stages":"story,shots"}
  #   -> 实际调用 stages=['muse'], stage_params={'muse':{'apply':True,'reassemble':True}}
  #      （用户要的 story,shots 被无声丢弃）
  ```
- **影响**：① 未经确认烧 GPU（3 集 × 每集 muse 2 镜）；② 用户明确要跑 story+shots 却被换成 muse，即使真跑也拿不到想要的结果，且响应里不回显最终 `stages`（`v6_agent.py:506-507` 只回 episodes/dry_run/with_muse）。
- **建议修法**：`dry_run` 默认恒 `True`（要真跑必须显式 `dry_run:false`）；`with_muse` 只作为 `stages` 的「追加」而非替换（`stages = [*(stages or ["muse"]), "muse"]`，去重）；`only_missing` 不要强改；响应回显最终 `{stages, dry_run, stage_params, only_missing}`。

### P1-5 `muse` 阶段 `depends: []` + 「skipped 记 ok」：季批量口型是一条全绿的空跑（第一轮 #13/#4 残留）
- **位置**：`pipeline/agents/workflow.py:41-44`（DEFAULT）、`:75-78`（SHORTS）、33 份合同同款；`orchestrator.py:148-152`（不看返回值里的 `skipped`）
- **问题**：两件事叠在一起：
  1. `muse.depends=[]` → `_expand(["muse"])` 只返回 muse，不拉 `shots`；镜头不存在时 `muse_lipsync` 返回 `{"skipped": True, "reason": "没有「镜头 + 音频」对"}`。
  2. orchestrator 只看「有没有抛异常」→ 记 `status="ok"`，run.status 也是 `ok`。
- **证据**：
  ```bash
  python docs/audit/_tools/r3_orch_semantics.py    # 段 H
  # _expand(SHORTS_STAGES, ['muse']) = ['muse']                    ← 没有任何依赖被补进来
  # _expand(SHORTS_STAGES, ['qc'])   = ['story','shots','qc']
  python docs/audit/_tools/r3_cli_plans.py         # B
  # 短片 --stages muse --dry-run -> stages=[('muse','planned')]
  python -c "from pipeline import lipsync; print(lipsync.dialogue_pairs('custom_tpl_v46_duologue',2))"
  # []      ← 当前 33 个模板一个都没有可用镜头对
  ```
  AGENTS.md:58 把 `run --stages muse` 写成正式用法；季 `with_muse` 也走这条。
- **影响**：现在跑 `--stages muse` / 季 `with_muse`，100% 是「什么都没做，但 run/季节状态全绿」。加上 muse 只覆盖前 `limit` 个镜头（默认 2）却返回 `ok`，交付方拿不到「这集口型只做了 2/6 镜」的信息。
- **建议修法**：`muse.depends` 改成 `["shots"]`（V5 栈的 muse 直接删掉这条阶段，或 `depends=["assemble"]`）；`orchestrator` 里 `if isinstance(result, dict) and result.get("skipped"): record["status"]="skipped"; run.status 至少 "partial"`；`muse_lipsync` 返回值带 `coverage: f"{applied}/{len(pairs)}"`，orchestrator 把 `applied == 0` 当失败。

### P1-6 `/media/agent_runs/{run_id}/{file}` 的 40 字符闸门：长模板的 run 证据永久 404
- **位置**：`backend/app/routers/v6_agent.py:46-47`（`RUN_MEDIA`）、`:548-549`
  ```python
  if not re.match(r"^[a-z0-9_]{8,40}$", run_id) or file not in RUN_MEDIA: 404
  ```
- **问题**：`run_id = 16 + len(template_id)`，所以 `template_id` 超过 24 字符的模板，其所有 run 证据都打不开。
- **证据**：
  ```bash
  python docs/audit/_tools/r3_api_validate.py   # 段 1
  # run_id 超 40 的模板: [('custom_tpl_v45_soft_watercolor', 46)]
  python docs/audit/_tools/r3_api_season.py   # 段 C
  # 20261105_160602_custom_tpl_v46_cyber            len= 36 -> 200
  # 20261105_112758_custom_tpl_v45_soft_watercolor  len= 46 -> 404  ```
  （`custom_tpl_v45_soft_watercolor` 历史上真跑过：`outputs/agent_runs/20261105_112758_custom_tpl_v45_soft_watercolor/` 存在。）
- **影响**：页面「打开证据」对该模板恒 404；季 run 的 `season_<sid>_<ts>` 同样吃这个闸门（sid 长一点就超）。而且失败是 404，看起来像「run 不存在」。
- **建议修法**：正则放宽到 `^[A-Za-z0-9_]{8,80}$`（`run_id` 只含 `[0-9_]` + 模板 id），或干脆 `re.fullmatch(r"[a-z0-9_]+", run_id) and len(run_id) <= 80`；更好的做法是路径用 `{run_id:path}` + `resolve()` 白名单（已经在做）。

### P1-7a 短片 QC 不写 `qc_report.json`、时长门只有下限没有上限（代码缺陷，lead 拍板「以代码为准」）
- **位置**：`pipeline/agents/registry.py:341-365`（`evaluate_hq_qc`，只写 `qc_shorts.json`）、`:368-392`（`write_shorts_report` 也只读 `qc_shorts.json`）
- **问题**：
  1. **证据文件名不统一**：短片栈只写 `qc_shorts.json`（`registry.py:360`），V5 栈写 `qc_report.json`（`render_v5.py:1185`，结构 `{"ok":…, "checks":…}`）。任何统一的发布台账/验收脚本都无法只认一个名字；`templates3d*` 全仓没有 `qc_report.json`。
  2. **时长门漏上限**：`duration >= target * 0.6` 且**无上界**——target 14.12s 的片子 8.47s 就过，40s 也过；PLAN §4「13–25s、4–6 幕」没有任何代码校验。
- **证据**：
  ```bash
  python docs/audit/_tools/r3_probe.py     # 段 7
  # target=14.12s -> 只要 >= 8.47s 就过；无上限（40s 也过）
  Select-String -Path pipeline/templates3d*.py -Pattern 'qc_report'   # 无命中（只有 poster）
  python -c "print(open('outputs/templates_3d/custom_tpl_v46_cyber/report.json').read()[:80])"
  # report.json 里没有 qc 字段；目录里有 qc_shorts.json，没有 qc_report.json
  ```
- **影响**：发布台账无法统一消费短片 QC；8.5s 的残片能过 QC 被当成交付；`evaluate_hq_qc` 的 `checks` 里也没有「上界」这一项，事后无法从证据判断是「短了」还是「长了」。
- **建议修法（按 lead 拍板）**：
  1. `evaluate_hq_qc` **双写** `qc_report.json`（与 V5 同名同结构 `{"ok":…, "checks":…, "probe":…}`），**保留 `qc_shorts.json` 作为别名**（双写不删旧文件，避免现有消费方断）；`write_shorts_report` 优先读 `qc_report.json`、回退 `qc_shorts.json`。
  2. 时长门改成**上下界**：`0.6*target <= d <= max(1.5*target, target+2)`，并把 `duration`、`min_duration`、`max_duration` 三个字段都写进 `checks["duration"]`，让证据自带判据。
  3. `write_shorts_report` 增加「成片存在且新于 QC 文件」前置条件（见 P2-11），避免双写后出现「qc_report.json 绿、成片没了」。

### P1-7b PLAN 第 3 节把成片目录写成 `outputs/templates_2d/`（文档事实错误）
- **位置**：`docs/PLAN_V49_SHORTS_SEASON.md:85`（「每集：`outputs/templates_2d/<id>/final.mp4` 存在 + `qc_report.json.ok == true` + `poster.jpg`」）
- **问题**：HQ 短片栈只写 `outputs/templates_3d/`（`templates3d.py:33` `OUT3D = config.OUTPUTS_ROOT/"templates_3d"`、`templates3d_hq.py:987` 海报也写 `t3.OUT3D`）；`outputs/templates_2d/` 实际是另一条历史产线（longform）的目录。
- **证据**：
  ```bash
  python -c "from pathlib import Path; r=Path('outputs/templates_2d'); f=[p for p in r.rglob('*') if p.is_file()]; print('dirs=',len([p for p in r.iterdir() if p.is_dir()]),'files=',len(f),'mp4=',len([p for p in f if p.suffix=='.mp4']), sorted({p.name for p in f}))"
  # dirs= 9 files= 1 mp4= 0 ['longform_300s.json']
  ```
  → `templates_2d` 全树只有 **1 个文件、0 个 mp4**，按 PLAN 第 3 节验收短片线必然 0/3 通过。
- **影响**：验收文档给的是不存在的路径，任何按文档执行的验收都会误判；同时掩盖了 P1-7a 的文件名问题（先卡在路径，根本走不到 `qc_report.json` 那一步）。
- **建议修法（文档侧，与 7a 同批）**：`docs/PLAN_V49_SHORTS_SEASON.md:85` 的 `outputs/templates_2d` 改为 **`outputs/templates_3d`**；同时把该行拆成「V5 栈 → `templates_v5` + `qc_report.json`；HQ 栈 → `templates_3d` + `qc_report.json`（`qc_shorts.json` 为别名）」，与 7a 的双写落地同步。

---

## P2

### P2-1 `_expand` 校验失败会留下 `status: "running"` 的僵尸 run（第一轮 #3/R2#10 的衍生）
- **位置**：`orchestrator.py:104-108`（`event("start")` + `persist()` 在 `_expand` 之前）、`:81`（先 `mkdir`）
- **问题**：未知阶段/未知起始阶段的 ValueError 发生在 run_dir 已建、`run.json` 已写（`status="running"`、`stages=[]`）之后；异常直接抛出，不写 `failed`、不写 `summary.md`、不发 `end` 事件。
- **证据**：
  ```bash
  python -m pipeline.agents run --template tonight_show --stages stor     # exit=2，[error] 未知阶段
  Get-Content outputs/agent_runs/20261106_232714_tonight_show/run.json
  # "status": "running", "stages": [], "ended": "" ；目录里没有 summary.md
  ```
- **影响**：`latest_runs` / `/api/v6/agent/runs` / overview 永久显示一条「running」的 run；页面「打开证据」拿不到 summary.md。
- **建议修法**：`todo = _expand(...)` 挪到 `run_id`/`mkdir`/`persist` 之前（`_expand` 是纯计算）；或 `try: todo = _expand(...) except ValueError: raise` 前先不落 run 目录。

### P2-2 未知模板 → KeyError traceback（CLI 只捕 ValueError）+ 空 run 目录
- **位置**：`registry.py:607`（`catalog["templates"][template_id]`）、`cli.py:72`（只 `except ValueError`）、`v6_agent.py:213`（API 侧有 `_tpl` 保护）
- **证据**：
  ```bash
  python -m pipeline.agents run --template no_such_tpl --dry-run
  # KeyError: 'no_such_tpl'  ... orchestration.py:83 new_context → registry.py:607 ; exit=1
  # outputs/agent_runs/20261106_232714_no_such_tpl/  （空目录）
  ```
- **建议修法**：`new_context` 里 `if template_id not in catalog["templates"]: raise ValueError(f"未知模板: {template_id}；可用 N 个")`；`cmd_run` 顺带 `except (ValueError, FileNotFoundError, RuntimeError)`；`run_template` 里把 `new_context` 挪到建目录之前。

### P2-3 `save_workflow` 不查循环依赖、不查 `stack` 与模板栈一致
- **位置**：`workflow.py:146-168`
- **证据**（隔离 `WORKFLOW_DIR`，未碰真合同）：
  ```bash
  python docs/audit/_tools/r3_api_validate.py    # 段 5
  # 重复 stage id  -> ValueError: stage id 重复: ['a']
  # 未知 tool      -> ValueError: 未知工具: nope（a）
  # 依赖不存在      -> ValueError: a 依赖不存在的阶段: ghost
  # 空 stages     -> ValueError: workflow.stages 必须是非空数组
  # 非法 id       -> ValueError: 非法 workflow.id: ZZZ-T
  # 循环依赖         -> 已保存 zzz_t.json   <== 未拦截
  # stack 写错     -> 已保存 zzz_t.json   <== 未拦截
  ```
- **影响**：环被存进合同后，run 一定在第一个阶段 `failed: 依赖未完成`（本报告 P2-1 的失败形态），只能 `reset_workflow` 救；`stack` 写错时 `run_template` 用错栈（`v5_render` 的工作流套在 `custom_tpl_*` 上 → `render_shots` 去打 3D 短片），`orchestrator.py:169-172` 的成片判定也跟着错。
- **建议修法**：拓扑排序检测（Kahn）；`if workflow.get("stack")` 与 `"hq_shorts" if is_shorts(id) else "v5_render"` 不一致就拒绝或直接覆盖成正确值。

### P2-4 `PUT /api/v6/agent/workflow/{template}` 的校验异常直接 500（不是 400）
- **位置**：`v6_agent.py:156-165`（`save_workflow` 的 ValueError 未包成 HTTPException）
- **证据**：
  ```bash
  python docs/audit/_tools/r3_api_validate.py    # 段 7（TestClient raise_server_exceptions=False）
  # status = 500 | body = Internal Server Error
  # traceback: v6_agent.py:163 save_workflow → workflow.py:168 ValueError: 未知工具: no_such_tool（story）
  ```
- **影响**：页面上「保存工作流」写错 tool 时只看到 500，前端拿不到原因；日志里是 traceback 而不是业务错误。
- **建议修法**：`try: save_workflow(wf) except ValueError as exc: raise HTTPException(400, str(exc))`（`agent_edit` 已经是这个写法，`v6_agent.py:523-528`）。

### P2-5 `--stage-params` 是浅更新、且对不存在的阶段名完全静默
- **位置**：`orchestrator.py:146-147`
  ```python
  params = dict(stage.get("params") or {})
  params.update((stage_params or {}).get(sid) or {})      # update = 浅更新
  ```
- **证据**：
  ```bash
  python docs/audit/_tools/r3_orch_semantics.py    # 段 C/D
  # 合同 params={'x':1,'nested':{'k1':1,'k2':2}}，覆盖 {'nested':{'k2':9}}
  # 传入 params: {'x': 1, 'nested': {'k2': 9}}        ← 深合并应保留 k1
  # stage_params={'aa': {...}}（阶段名写错）-> status=ok，无任何告警
  ```
- **影响**：`{"shots":{"style":{...}}}` 这类嵌套参数会被整块替换而不是合入；阶段名打错时「参数改了一晚上没生效」。
- **建议修法**：用 `workflow.merge_dict`（同文件已有）；入口处 `unknown = set(stage_params) - {s['id'] for s in wf['stages']}` → `raise ValueError`（CLI 与 API 都要，`v6_agent.py:219-224` 只校验了「是不是 JSON」）。

### P2-6 `--start` 会静默覆盖 `--stages`
- **位置**：`orchestrator.py:44-51`
- **证据**：`r3_orch_semantics.py` 段 G：`stages=["a"], start="b"` → 实际执行 `a,b,c,d`（`--stages a` 被丢弃后又被依赖闭包补回，纯属巧合；`--start` 更靠后时 `--stages` 直接消失）。
- **建议修法**：两者同时给就报错，或语义定义为「先按 `--stages` 选，再裁掉 `start` 之前的」。

### P2-7 `_expand` 不做拓扑排序：UI 调换阶段顺序后，依赖未完成被判 failed（lead 基线项，已核实）
- **位置**：`orchestrator.py:63`（`return [s for s in stages if s["id"] in want]`，纯保序）+ `:112-126`
- **证据**（打桩，把 shots 拖到 story 前面）：
  ```bash
  python docs/audit/_tools/r3_probe.py     # 段 1
  # [reorder] status=failed stages=[('shots','failed',"依赖未完成或不存在: ['story']")]
  # tool 实际调用: []        ← render_shots 一次都没跑
  # _expand 结果: ['shots','story']
  ```
- **影响**：页面工作流编辑器允许改顺序（`put_workflow` 原样存 `stages`），改完保存 = 这条工作流永久必挂；报错文案「依赖未完成或不存在」也不会告诉你「是顺序问题」。
- **建议修法**：`todo` 排序用拓扑序（同层保持原顺序），或在 `_expand` 里检测「依赖出现在自己之后」并直接 `ValueError("阶段顺序与依赖冲突: shots 在 story 之前")`。

### P2-8 `duration` 覆盖是死代码：模板自带时长永不生效，`--duration 0` 时证据与执行还打架（lead 基线项，已核实）
- **位置**：`orchestrator.py:83-86`
  ```python
  ctx = new_context(template_id, duration=duration, ...)     # 先用形参建 ctx
  if ctx.template.get("duration"):
      duration = float(duration or ctx.template["duration"])  # 只改局部变量
  ```
- **证据**：
  ```bash
  python docs/audit/_tools/r3_probe.py     # 段 2
  # 模板 duration=60, CLI --duration=40 -> new_context(duration=40.0) | 事件记录 40.0
  # --duration=0 -> new_context(duration=0.0) | 事件记录 60.0   ← 记录 60，工具拿 0 秒
  ```
- **影响**：① 模板里写的 `duration` 永远是死数据（默认 40.0 恒真值）；② `--duration 0` 会渲染 0 秒片却把 60 写进证据。
- **建议修法**：先解析时长再建 ctx：
  ```python
  tpl_duration = float((template or {}).get("duration") or 0)
  duration = float(duration or tpl_duration or 40.0)
  ctx = new_context(template_id, duration=duration, ...)
  ```
  （并且 `duration` 默认值改 `None`，让「没传」和「传 0」可区分。）

### P2-9 工具抛 `SystemExit` 时 run 证据停摆（第一轮 #2 的残留）
- **位置**：`orchestrator.py:153`（`except Exception`）；对照 `v6_agent.py:199/406/446` 已改 `BaseException`
- **证据**：
  ```bash
  python docs/audit/_tools/r3_probe.py     # 段 3
  # run_template 抛出 SystemExit(3)
  # run.json.status = running | summary.md 存在 = False | 阶段记录 = []
  ```
  现存工具里 `render_2d_hq`（`:310-313`）和 `check_story`（`:211`）已改，但任何新工具/下游 `argparse`/`sys.exit` 仍会踩。
- **建议修法**：`except BaseException as exc:` + `if isinstance(exc, (KeyboardInterrupt, SystemExit))` 时也落 `failed` 再 re-raise（保住 Ctrl-C 语义，但证据先写完）。

### P2-10 「启用 art 阶段」用默认参数必然失败
- **位置**：`workflow.py:28-31`（`art.params = {"character": "", "poses": []}`）、`art.py:49-54`（`raise ValueError("generate_art 需要 character")`）
- **证据**：`r3_probe.py` 段 4 → `ValueError: generate_art 需要 character`。UI 的 affordance 就是「enable 阶段」，AGENTS.md §2 也说「改工作流 = 改 JSON（增删阶段/改依赖/改参数）」，但只 enable 不填 param 就挂。
- **建议修法**：`params.character` 为空时按模板 `cast[0].character` 兜底（`render_shots` 等阶段都从模板取，这里也可以）；或把 `art` 的 enable 与参数必填在 `save_workflow` 里联动校验。

### P2-11 `write_shorts_report` 对「成片不存在」仍产出 `qc_ok: true`
- **位置**：`registry.py:368-392`（只读 `qc_shorts.json`，不校验成片是否存在/是否比 QC 新）
- **证据**：
  ```bash
  python docs/audit/_tools/r3_probe.py     # 段 6（真实目录 custom_tpl_v46_cyber）
  # 磁盘: final.mp4 存在=False | qc_shorts.json ok=True
  # 报告: qc_ok=True duration=None resolution=None
  ```
- **影响**：`template_summary.json` / `summary.md` 会写「QC：通过」，而 `final` 字段指向不存在的文件；季面板与人工验收都会被这条绿骗。任何「删产物不删证据」的操作都会留下这种状态（现在磁盘上就是）。
- **建议修法**：`qc_ok = bool(qc.get("ok")) and final.is_file() and final.stat().st_mtime >= (out/"qc_shorts.json").stat().st_mtime`；不满足就 `qc_ok=False` + `stale=True`。

### P2-12 季「就绪」只看文件存在、`last_run` 把 dry-run 计划当成最近一次生产
- **位置**：`v6_agent.py:289-303`（`ready=bool(_final_of(tid))`、`last_run` 取 mtime 最新 run.json，不看 status/dry_run）、`:483`（`only_missing` 判据）
- **证据**：
  ```bash
  python docs/audit/_tools/r3_api_season.py     # 段 A（真实合同）
  # member[0]: {'ready': False, 'last_run': {'run_id': '20261105_170955_custom_tpl_v46_undercity',
  #                                          'status': 'planned', 'started': '2026-11-05 17:09:55'}}
  ```
  另：`_final_of` 只判 `is_file() and size>0`，不看 QC（`v6_agent.py:81-88`）。
- **影响**：① PLAN §4「新季门槛：上一季 3/3 出片 + 合集导出通过」无法实现——一个 QC 红但文件在的成片会被算作「已出片」，`only_missing` 会跳过它；② 季面板把一次 dry-run 显示成「最后一次生产 = planned」，看起来像生产失败。
- **建议修法**：`ready` 加 QC 门（`qc_shorts.json.ok`/`qc_report.json.ok` 且 mtime 新于成片）；`last_run` 优先取 `status in ("ok","failed")` 且 `not dry_run` 的记录，取不到再退回最新。

### P2-13 编排层完全不校验 `season.rules`，且与 `scripts/check_seasons.py` 是两套逻辑
- **位置**：`v6_agent.py:307-323`（只查 `same_cast`/`same_style`/`target_count`）；`contract/v5/series/undercity_arc.json:26-39`（`episode_sec` / `acts` / `same_world` / `premise` / `style_tag` / `order`）
- **证据**：
  ```bash
  Select-String -Path pipeline/agents/*.py,backend/app/routers/*.py -Pattern 'episode_sec|same_world|premise|style_tag'
  # 零命中（只有 target_count / same_cast / same_style 被用到）
  Select-String -Path scripts/check_seasons.py -Pattern 'episode_sec|acts|same_world'
  # :48 same_world、:53 episode_sec、:60 acts  —— 校验只存在于脚本里
  ```
- **影响**：季生产的「每集 13–25s、4–6 幕、同世界」规则，在 API/季 worker 侧完全不存在；`check_seasons.py` 又不被季生产调用（只有测试/人工跑）。同一套规则两处实现，改一处必漂。
- **建议修法**：把 `check_seasons.py` 的校验抽成 `pipeline/agents/season_rules.py::validate(series) -> warnings`，`_series_entry` 与 `check_season` worker 共用；`season_run` 在入队前跑一遍，有 error 直接 400。

### P2-14 `set_voice` 仍只认内建角色目录；rerun 只给 5 个模板（R1#9/#10、R2#11 残留）
- **位置**：`ops.py:42-43`（`CONTRACT/"characters"/<id>.json`）、`ops.py:70-73`（`affected_templates[:5]`）
- **证据**：
  ```bash
  # custom_old_inventor : base=False custom=True     ← 只存在于 contract/v5/custom/characters/
  # contract/v5/custom/characters/ 共 27 份角色合同
  ```
  `patch_template` 已经做了 builtin→custom 回退（`ops.py:98-102`），`set_voice` 没同步。
- **影响**：`edit --op set_voice --char custom_old_inventor`（季第 2/3 集的主角）直接 `FileNotFoundError: 角色不存在`，文案把「路径没找对」说成「数据不存在」；rerun 文案只列 5 个模板，其余 24 个模板的音色变化无人重跑。
- **建议修法**：抽 `_resolve(kind, id)`（`contract/v5/{kind}/` → `contract/v5/custom/{kind}/`）给 `set_voice`/`patch_editorial`/`patch_template` 共用；`rerun` 给全量命令（`--stages ...` 循环）并附 `affected_count`。

### P2-15 `orchestrator` 的成片路径硬编码 `REPO/outputs`（R2#1 的残留）
- **位置**：`orchestrator.py:169-172`
  ```python
  if (workflow.get("stack") == "hq_shorts"
          and (REPO / "outputs" / "templates_3d" / template_id / "final.mp4").is_file()):
  ```
- **证据**：
  ```bash
  python docs/audit/_tools/r3_probe.py     # 段 5（设 ZAOPIANJU_OUTPUTS_DIR 后）
  # config.OUTPUTS_ROOT = ...\tmp\alt_out
  # registry._outputs_root() = ...\tmp\alt_out
  # templates3d.OUT3D        = ...\tmp\alt_out\templates_3d
  # orchestrator 里的硬编码   = ...\outputs\templates_3d\<tid>\final.mp4
  # 一致? True                 ← 只有 registry 与 templates3d 一致
  ```
- **影响**：设了环境变量（隔离输出/多实例/测试）时，`run.outputs.final` 指向 `repo/outputs` 里的旧成片或空路径，`exists` 判错，季面板/证据链跟着错。
- **建议修法**：`final = Path(config.OUTPUTS_ROOT)/"templates_3d"/template_id/"final.mp4"`（或直接调 `registry._shorts_dir(ctx)`）。

### P2-16 muse 的 `_orig` 只在 `force` / `reuse_shots=false` 时失效（R2#4 残留）
- **位置**：`registry.py:321-326`
  ```python
  if ctx.force or not params.get("reuse_shots", True):
      for stale in (out / "shots" / "_orig", _outputs_root() / "muse" / ctx.template_id): ...
  ```
- **问题**：`templates3d_hq` 默认 `--reuse-shots`，单镜因 prompt/关键帧变化而重渲时走的是「复用其它镜、只重渲这一个」，此时 `_orig/` 与 `outputs/muse/<tid>/<sid>_muse.mp4` 都还在 → 下一次 muse apply 仍拿**旧镜头**的口型结果覆盖**新镜头**。
- **影响**：与 R2#4 完全同型，只是触发条件从 `--force` 换成「改一镜 prompt 后重渲」。短片是逐镜迭代的产线，这是常态路径。
- **建议修法**：缓存键带内容指纹（`sha1(clip)[:8]`），或让 `templates3d_hq` 在重渲某镜时回调删除该镜的 `_orig` 与 muse 产物（最干净：在 `render_2d_hq` 里对比重渲前后的 `shot_meta`/mtime 差集，删对应缓存）。

### P2-17 `muse_apply=true` 单独传（不带 `with_muse`）是静默空操作
- **位置**：`v6_agent.py:474-475`（只往 `stage_params` 塞 muse.apply）vs `orchestrator.py:127`（disabled 阶段不在 `explicit` 就跳过）
- **证据**：
  ```bash
  python docs/audit/_tools/r3_api_season.py     # 段 D5
  # {"muse_apply": true, "dry_run": true} -> stage_params={'muse':{'apply':True,'reassemble':True}}, stages=None
  # 而 muse 阶段 enabled=false + 不在 explicit → 每个 run 都记 "工作流中已禁用"
  ```
- **影响**：API 接受了参数、返回 200、季状态 ok，实际一个 muse 都没跑。AGENTS.md §5 教用户同时给 `with_muse:true`，但 API 不校验这个组合。
- **建议修法**：`if muse_apply and not (with_muse or "muse" in (stages or [])): raise HTTPException(400, "muse_apply 需要 with_muse=true 或 stages 含 muse")`。

---

## P3

### P3-1 `latest_runs` 把季级 `run.json` 也当单集 run 返回（`run_id: null`）
- **位置**：`orchestrator.py:201-224`（`rglob("run.json")` 不区分单集/季）；季记录由 `v6_agent.py:423-424` 写，字段是 `season/status/episodes`，没有 `run_id`/`template`
- **证据**：
  ```bash
  python docs/audit/_tools/r3_api_season.py     # 段 F
  # GET /overview -> count 33 | runs [None, None, '20261106_232714_tonight_show']
  ```
- **影响**：`/api/v6/agent/runs` 与 overview 的 `runs` 里出现 `run_id: null` 的条目，前端按 run_id 做 key/链接会坏；季 run 也因此吃掉了 `limit` 名额。
- **建议修法**：`if "season" in data and "run_id" not in data: continue`（或给季记录补 `run_id`/`kind:"season"`，让前端分流）。

### P3-2 `/media` 白名单不含 `episode.json`
- **位置**：`v6_agent.py:46-47`；`check_story` 把 `episode.json` 写到 `run_dir`（`registry.py:249`）
- **影响**：短片 story 阶段的唯一结构化证据（分幕/台词/时长/brief 数量）在页面上打不开，只能进服务器看。
- **建议修法**：`RUN_MEDIA` 加 `episode.json`。

### P3-3 `season_status` 没有「生产中」状态
- **位置**：`v6_agent.py:510-516`（只回 `missing`），job 状态在 `_jobs` 里不关联
- **影响**：PLAN M3 前端验收要求「集状态（已出片/待生产/**生产中**）」，现在第三态无法呈现，用户点完季生产只能看日志。
- **建议修法**：`season_status` 里带上「该 sid 的活跃 job 及其当前 episode」，或在 member 上算 `running`。

### P3-4 `_jobs` 无上限、跨进程无锁
- **位置**：`v6_agent.py:49-51`、`:225-237`
- **影响**：进程生命周期内每个 job 的 800 条日志常驻内存；多 uvicorn worker 时 `_jobs` 各自为政，409 形同虚设（与 P1-3 同源）。
- **建议修法**：job 加 TTL 清理（保留最近 50 条/1 小时）；真并发门用文件锁。

### P3-5 `dry_run` 计划会因「enabled 依赖 disabled」被记成 `failed`
- **位置**：`orchestrator.py:120-126`（依赖检查在 dry_run 判断之前）+ `:163`（`failed` 优先于 `planned`）
- **证据**：`r3_orch_semantics.py` 段 B：`dry-run` 时 `d` 阶段 `failed: 依赖未完成或不存在: ['c']`，`run.status=failed`。
- **影响**：UI 的「一键季计划（dry-run）」在用户关掉中间阶段后会显示「计划失败」，而这不是执行失败。
- **建议修法**：dry_run 下把该情形记 `status="blocked"` 且 `run.status="planned"`，并把冲突理由写进 summary。

### P3-6 `TOOLS` 里的 `set_pose` 没有任何工作流引用
- **证据**：`r3_enum_tools_vs_workflows.py` → 「registered but never used: `set_pose`」（33 份合同 0 引用；`artifact` 走 `ops.set_pose`/`/api/v6/agent/pose` 两条别的路）。
- **影响**：注册表与合同脱节，`TOOLS` 的「每个阶段 = 一个工具」不再成立；也说明没有任何工作流能表达「姿态策略」这一步。
- **建议修法**：要么在 `art` 阶段加 `params.pose`，要么承认它是 API-only 工具并从「阶段工具」语义里拆出去（新增 `TOOLS_API_ONLY` 集合）。

### P3-7 `patch_workflow` / `set_stage_param` 空操作也会重写合同文件
- **位置**：`ops.py:144-145`（`set_stage_param` → `patch_workflow` → 无条件 `save_workflow`）、`workflow.py:146-174`
- **证据**：本轮探针 `POST /edit {op:set_stage_param, stage:"shotz"}` 返回 `{"ok": true}`、合同语义不变（`wf_before != wf_after → False`），但文件被重写并**丢掉尾换行**（`git diff --numstat` 从 31/2 变 30/1，已复原）。
- **影响**：不存在的阶段名被静默接受；每次调用都改 mtime、在 git 里制造噪声。
- **建议修法**：`if not changed: return {"ok": True, "noop": True}`；`save_workflow` 写盘时统一补尾换行（与其他写入器一致）。

---

## 验证明细

> 全部在仓库根目录执行（`cd D:\BaiduSyncdisk\wmj_work\XJ\AiJue`），`$env:PYTHONIOENCODING='utf-8'`。脚本只读或打桩，**没有一个跑 GPU**。

### 1) 工具注册表 ↔ 工作流 JSON 比对（要求 1）
```bash
python docs/audit/_tools/r3_enum_tools_vs_workflows.py
```
```
workflow files: 33
-- tools in JSON not in TOOLS --      count: 0
-- depends on nonexistent stage --    count: 0
-- cycles --                          count: 0
-- tool usage (33 份合计) --
  muse_lipsync 33 | muse_video 33 | build_story 22 | synthesize_voices 22 | generate_art 22
  plan_editing 22 | render_shots 22 | render_h3 22 | assemble_episode 22 | build_storyboard 22
  evaluate_qc 22 | write_report 22 | check_story 11 | list_art 11 | render_2d_hq 11
  evaluate_hq_qc 11 | write_shorts_report 11
-- registered but never used --  set_pose
-- stage id sets --
  22 ('v5_render', 12 阶段)   11 ('hq_shorts', 7 阶段)
```
**结论**：`tool` 字段与 `TOOLS` 完全一致，无「运行即失败」的合同；`set_pose` 是唯一无人用的注册项（P3-6）。

### 2) 模板 / 栈 / 文件一一对应（要求 3）
```bash
python docs/audit/_tools/r3_enum_templates_stacks.py
```
```
catalog templates: 33
stack mismatch count: 0 []
templates without workflow file: []      workflow files without template: []
custom_ 里非 custom_tpl_ 前缀: ['custom_holo_talk']
hq_shorts 里 render_style 为空的: ['custom_tpl_v22_radio','custom_tpl_v23_shop']   ← 靠 custom_tpl_ 前缀命中
base: 21  custom_*: 12 | hq_shorts: 11  v5_render: 22
```
**结论**：`is_shorts()` 与实际栈 0 偏差；AGENTS.md:3 的「16 内建 + 17 custom」与 PLAN:42 的同款说法**与数据不符**（实际 21 + 12），PLAN:21 的「12 个 custom 短片」才对（且其中 `custom_holo_talk` 是 V5 栈）。

### 3) 依赖图 / 合同↔常量漂移（要求 2）
```bash
python docs/audit/_tools/r3_enum_deps_drift.py
```
```
=== enabled stage depending on disabled/missing-enabled dependency ===   (none)
=== drift: JSON stages vs current code defaults ===                     (no DRIFT lines)
=== muse stage params per stack ===
  classics / 其余 21 份 v5_render : enabled=False params={'limit':2,'apply':False}
  11 份 hq_shorts                 : enabled=False params={'limit':2,'apply':False,'reassemble':True}
```
**结论**：33 份合同与 `DEFAULT_STAGES`/`SHORTS_STAGES` 零漂移；不存在「enabled 依赖 disabled」的合同。**但 `muse.depends=[]` 全部 33 份都是**（P1-5）。

### 4) 编排语义（打桩 `new_context` / `call_tool` / `load_workflow`，无 GPU）
```bash
python docs/audit/_tools/r3_orch_semantics.py        # 旧名 r3_orch_semantics.py
python docs/audit/_tools/r3_probe.py
```
```
A 全链路（c disabled, d enabled）: status=failed
   [('a','ok'),('b','ok'),('c','skipped','工作流中已禁用'),('d','failed',"依赖未完成或不存在: ['c']")]
B dry-run: 仍写 run.json/events.jsonl/summary.md/workflow.json 4 件；且因 disabled 依赖记 failed
C stage_params 浅合并: {'x':1,'nested':{'k2':9}}          ← 丢了 k1
D stage_params 写错阶段名 'aa': status=ok，无告警
E 显式 stages=['c']: disabled 阶段照跑（设计如此）
F 同秒两次 run: 同一 run_id / 同一目录
G stages=['a'] + start='b': --stages 被覆盖
H _expand(SHORTS,['muse']) = ['muse']
--- r3_probe ---
1 调换顺序: status=failed, shots 一次都没跑
2 duration: 模板 60 永不生效；--duration 0 时证据 60 / 工具 0
3 SystemExit: run.json 停在 running, 无 summary.md
4 generate_art 默认参数: ValueError: generate_art 需要 character
5 输出根: registry/templates3d 一致；orchestrator 硬编码 repo/outputs
6 write_shorts_report: final 不存在但 qc_ok=True, duration=None
7 时长门: target=14.12 → >=8.47s 即过，无上限
```

### 5) CLI 真跑（只读 / dry-run）
```bash
python -m pipeline.agents list
python -m pipeline.agents inspect --template tonight_show
python -m pipeline.agents inspect --template custom_tpl_v46_duologue
python -m pipeline.agents run --template tonight_show --stages stor          # exit=2
python -m pipeline.agents run --template no_such_tpl --dry-run               # KeyError, exit=1
python docs/audit/_tools/r3_cli_plans.py                                     # 4 个 dry-run 计划
```
```
tonight_show:            stack=v5_render  stages=12（story voice art editorial shots h3 muse musev assemble storyboard qc publish）
custom_tpl_v46_duologue: stack=hq_shorts  stages=7 （story art shots muse musev qc publish）
--stages stor        -> [error] 未知阶段: ['stor']；可用: [...]          exit=2
--template no_such_tpl -> KeyError: 'no_such_tpl'（traceback, exit=1）
A 短片 --stages publish --dry-run -> [story,shots,qc,publish]
B 短片 --stages muse   --dry-run -> [muse]
C V5  --stages qc      --dry-run -> [story,voice,editorial,shots,assemble,qc]
D V5  --stages story,asemble      -> exit=2（干净报错）
```

### 6) HTTP 入口（`TestClient`，季 run 用打桩 `run_template` 只记参数）
```bash
python docs/audit/_tools/r3_api_season.py
python docs/audit/_tools/r3_api_media_traversal.py
python docs/audit/_tools/r3_api_validate.py
```
```
GET /api/v6/agent/series          200 count=1 members=3 ready_count=0（last_run.status=planned）
GET /season/..%5C..%5Cv6%5Cart%5Cpose_policy/status   200  ← 穿越成功
GET /media/agent_runs/<46 字符 run_id>/run.json       404
POST /season/run {}                     dry_run=True  episodes=3
POST /season/run {with_muse:true}       dry_run=False stages=['muse']  ← P1-4
POST /season/run {muse_apply:true}      stage_params 生效，但 stages=None → 空操作
POST /season/run（已有 running job）    409
PUT /workflow/{t}（未知 tool）          500 Internal Server Error  ← P2-4
save_workflow（隔离目录）: 环/错 stack 均可保存  ← P2-3
```

### 7) 真实撞车现场（非打桩，本轮 23:30:32 发生）
```bash
Get-Content outputs/agent_runs/20261106_233032_tonight_show/events.jsonl
Get-Content outputs/agent_runs/20261106_233032_tonight_show/run.json
Get-Content outputs/agent_runs/20261106_233032_tonight_show/summary.md
```
```
events: start + 6×plan + end(planned) + start（第二个进程同一秒）
run.json: status=running, stages=[]      ← 覆盖
summary.md: 仍是第一次的 6 阶段表格       ← 未更新
```

---

## 已核对无问题的点

- **工具注册表 ↔ 合同**：33 份合同的所有 `tool` 都在 `TOOLS`；`TOOLS` 18 个工具只有 `set_pose` 无人引用（P3-6）。`call_tool` 的惰性导入回填（`registry.py:591-598`）与 `TOOLS[name]["fn"]` 缓存不会串号。
- **依赖图**：无重复 stage id、无悬空 depends、无环、无「enabled 依赖 disabled」；`save_workflow` 已拦截重复 id / 悬空依赖 / 未知 tool / 非法 id / 空 stages（环与 stack 除外，见 P2-3）。
- **合同 ↔ 代码常量**：33 份 stage 定义（id/agent/tool/depends/enabled/params）与 `DEFAULT_STAGES` / `SHORTS_STAGES` **逐字段零漂移**。
- **栈判定**：`is_shorts()` 与 33 份合同的 `stack` 字段 100% 一致；`custom_tpl_v22_radio` / `v23_shop` 无 `render_style` 由前缀命中（正确，走 `--style-mode 3d`）。
- **短片合同本身**：11 个短片模板 `acts` 齐全、`line` 角色都在 `cast`、`scene` 不缺、语义 brief 均有 prompt 镜头（81–93 个）；`check_story` 的判定逻辑对 11 份全部通过（静态复算，与 `registry.py:215-253` 同判据）。
- **`_expand` 的未知阶段/未知 start 校验**（第一轮 #3）：现在抛 ValueError，CLI 干净退出码 2。
- **degraded 合同**：`load_workflow` 打标记、`run_template` 先拒绝再建目录、`patch_workflow` 拒绝改写（R1#7/R2#2 均已修）。
- **`default_workflow` 深拷贝**：改返回值不再污染 `DEFAULT_STAGES`（`workflow.py:119`）。
- **`persist()` 写盘**：走 `utils.write_json`（`_finite` + `allow_nan=False` + 临时文件 rename），且本身包 try/except 不打断渲染。
- **muse 回写**：`tmp + os.replace` 原子替换，写失败/合成失败都能用 `_orig` 回滚，`_orig` 不会被二次覆盖；合成后强制刷新 `evaluate_hq_qc`，失败冒泡（R1#5/R2#5 已修）。
- **`render_2d_hq` 的 `t3.load_template`**：`finally` 里恢复，且 `_load_template` 尊重 `_tid`（R1#1 已修）。
- **`run_id` 目录权限/清理**：degraded 分支不再留空目录（R2#10 前段已修）。
- **`latest_runs` 排序**：改 mtime，季目录里的 run 也能被正确纳入（R1#12 已修；衍生 P3-1）。
- **`/media/agent_runs` 路径安全**：`resolve() + startswith(root) + 文件白名单`（只有 run_id 长度闸门有问题，见 P1-6）。
- **`_tpl` / `ID_RE`**：`/workflow/{template}`、`/run`、`/edit` 都有模板 id 白名单 + 404，模板不存在不会 500。
- **季 worker 的失败不阻塞后续集**：`_season_worker` 逐集 try/except 并继续（`v6_agent.py:388-409`），符合 PLAN §5「失败集跳过并继续」。
- **`session` 一致性**：`_jobs` 的读写全部在 `_job_lock` 里，单进程不会读到半个 job；`_current_job` 虽未参与判定但无副作用。

---

## 审查副作用（基线已复原）

1. `docs/audit/_tools/r3_api_validate.py` 段 3 调用了 `POST /api/v6/agent/edit {op:set_stage_param}`，该 op 会无条件 `save_workflow` → **`contract/v6/workflows/tonight_show.json` 被重写**：内容语义完全一致（`wf_before == wf_after`，`git diff` 只有原本就存在的 muse/musev/stack 未提交改动），但丢了尾换行（numstat `30/1` vs 同族 `31/2`）。已补回尾换行，现 numstat 恢复 `31/2`，与 `standup.json` 等 15 份同族文件一致。**这正是 P3-7 的现场证据。**
2. 探针在 `outputs/agent_runs/`（`.gitignore` 已忽略）留下若干 run 目录：其中 `20261106_232714_tonight_show`、`20261106_233032_tonight_show` 是 P2-1/P1-3 的僵尸证据（故意保留）；`season_undercity_arc_20261106_232819/232820` 是打桩产生的假季记录（`status: ok`），**已删除**；`20261106_232714_no_such_tpl` 空目录已删除。
3. 未修改 `pipeline/**` 与 `contract/**` 的任何内容（除上面第 1 条已复原的尾换行）；`docs/audit/` 目录本身在 git 里仍是未跟踪状态（`?? docs/audit/`）。
