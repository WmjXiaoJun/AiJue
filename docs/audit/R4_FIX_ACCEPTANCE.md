# R4_FIX_ACCEPTANCE — 第三轮审查整改验收报告

- **整改时间**：2026-11-06 23:45 – 2026-11-07 00:40（Asia/Shanghai）
- **依据**：`docs/audit/REVIEW_ROUND3.md`（4 条 P0 / 21 条 P1 / 22 条 P2 摘要）与六份分域报告
- **执行**：5 路并行（backend / render+脚本 / 编排+合同 / 前端 / Lead 亲自处理 P0 资产与时间线）
- **原则**：每项都要有可执行验证；资产类缺陷用程序化代用资产 + 合成片端到端验证；真 GPU 渲染不在本轮范围（会明确标注）

---

## 0. 验收速览

| 指标 | 整改前 | 整改后 | 证据 |
|---|---|---|---|
| **pytest（tests + pipeline/tests）** | **20 failed / 491 passed / 57 skipped** | **0 failed / 512 passed / 57 skipped** | `python -m pytest tests pipeline/tests -q`（92s） |
| 仓库根 `pytest` | 递归 4763 个外部测试，10 分钟无结果 | **565 条 0.25s 收完** | `python -m pytest --collect-only -q` |
| 内建模板场景底板 | **0/21 命中**（V5 栈硬失败 `scenes.py:380`） | **21/21 命中**，strict 校验 16/16 ok / warnings 0 | `scripts/gen_scene_plates_procedural.py` → `tmp/audit_plate_resolve2.py` |
| 角色 cutout 可解析 | **26/26 解析失败**（`CHARS_DIR=outputs/characters` 只剩 3 个 meta） | **26/26 命中**（`cutout_2d` / `cutout_3d` 各一份） | `scripts/make_cutout_layout.py`（52 份派生）+ `config.CHARS_ROOT` |
| semantic brief 重复 id | **13/30 份、55 处重复**（`v5_e001` ×2–9） | **0 重复**，30 份都带 `act_index` | `scripts/repair_semantic_briefs.py` → `tmp/audit_semantic_ids.py` |
| HQ `assemble`（brief 时间线） | 必失败：`RuntimeError (exit 4294967294)` | **端到端出片**（合成 3 镜 → final.mp4）+ 缺镜给可读错误 | `tests/test_v49_timeline.py` 4 条全绿 |
| 33 份工作流 dry-run | 33/33 planned（但 `muse.depends=[]` 空跑全绿） | 33/33 planned，`muse.depends=["shots"]`，`partial` 生效 | `tmp/audit_dryrun_all_workflows.py` |
| API 路由 | 161 条 / 0 重复 | 162 条 / 0 重复，`/api/v5/catalog` 仍 33 模板 26 角色 | `tmp/audit_smoke_api.py` |
| 服务 | 8090 跑整改前代码 | **已重启**，`/api/health` 200（`mode=comfyui`）、`/` 200 | `backend/run-detached.ps1` |

**一句话**：审查报告里"今天跑不出一部片"的四条都是**资产/时间线断链**，本轮已把链路接通并用合成片证明了端到端可出片；真片仍需 GPU 渲染（见 §5 未完成项）。

---

## 1. P0 逐条

### P0-1 场景底板全丢 → V5 栈硬失败 ✅ 已修（程序化代用资产）
- **改法**：
  - 新增 `scripts/gen_scene_plates_procedural.py`：读 `contract/templates_v3/*.json` 的 `stage.plate`（16 个 `<scene>__<style>`）与 `contract/scene_map.json` 的 v5 场景名（10 个），生成 **26 张 1280×720** 底板；按后缀分派调色（ink/cinematic/noir/neon/theatre/vhs/game/gold/clay/warmtalk/roundtable/debateink），画地面/家具剪影/顶灯/软光/暗角/颗粒，**不画人、不画字**；带亮度归一（mean≈96、floor 26）避免撞 QC `blackdetect` 门。
  - 产物标记：`outputs/scene_plates/procedural.json` 每张写 `procedural: true` + `note: 非 AI 绘景`，将来 `scripts/gen_scene_plates.py`（SDXL）覆盖后不会被误当 AI 作品。
  - **拆掉"借底板"陷阱**：`style.py:131-139` 与 `style_v2.py:125-129` 删除「任选一张存在的底板」兜底（`late_night_desk/history_study/roundtable_studio` 三个硬编码 alt）—— 缺就是缺，上层拿到 `missing` 显式失败，不再让 16 部片静默共用同一背景。
- **验证**：`tmp/audit_plate_resolve2.py` → `模板 21；plate 命中 21；缺失 0`（level 全为 `style_v2`，fallback=False）；`template_v3.validate_dir(expect=16, strict_plates=True)` → `ok=True, warnings=0`；`tests/test_v3_acceptance.py::test_templates_validate_strict` 转绿。

### P0-2 episode / brief 两套 id 空间 → assemble 必失败 ✅ 已修（时间线解析 + 前置门）
- **改法**（`pipeline/templates3d.py`）：
  - 新增 `episode_from_brief()`：把 semantic brief 转成 `render_v5.assemble` 能吃的 episode 结构（id/type/duration/line/scene/roles/speaker/act_index），保留 base 的 `meta`/`cast`/`style`。
  - 新增 `resolve_timeline(tid)`：按**镜头实际落盘覆盖率**在 `out3d（带 timeline_source 标记）/ templates_v5 / semantic_brief` 三条时间线里择优；最高覆盖率 `< 0.9` 直接抛可读错误（列缺哪些 id、给出候选覆盖率），**不再把缺失路径喂给 ffmpeg**。
  - `assemble()`：先做 mp4 齐备硬门，再对缺配音的镜头**按镜头时长补静音**（`work/audio/<sid>.wav`），校验通过后才把选中时间线写成 `OUT3D/<tid>/work/episode.json`（带 `timeline_source`，失败不污染磁盘）。
  - 陈旧 `OUT3D/<tid>/work/episode.json`（无标记）不再抢候选 —— 那是 V5 时间线的旧副本（`tonight_show` 11 镜 vs 78 镜）。
- **验证**：新增 `tests/test_v49_timeline.py`（4 条，全部绿）：① brief 时间线被选中且 ratio=1.0；② 端到端 `assemble` 用 3 个 lavfi 假镜头出 `final.mp4`（>10KB，时长 >0.6s）；③ 只渲 1/3 时抛「缺 v5_002 …」且错误里**不出现 ffmpeg**；④ 校验失败时**不写** `work/episode.json`。

### P0-3 改一句台词重跑会覆盖别的配音 ✅ 已修（内容寻址）
- **改法**（fix-render）：`tts_breeze.py` 输出名改 `<role>_<sha1(text)[:10]>.wav`；三阶段（内容寻址分流 → 只提交缺句 → 按原始下标回填），批内重复句只合成一次；每条 wav 写 `<wav>.meta.json`（text_sha1/voice_sig/seed/cfg/bytes）。`render_v5._voice_entry_ok` 命中要求「文件在 + bytes 未变 + text_sha1 一致 + voice_key 一致」，否则重合成。
- **验证**：`docs/audit/_tools/r4_tts_content_addressing.py`（假 HTTP 跑真实 `synthesize`）：run1 `streamb_laozhou_32a0da7314.wav`(5324B)；run2 改第 1 句 → 新句落 `…ab6045b09f.wav`(5804B)，**A 仍 5324B 未被覆盖**，第 2 句 `reused=true`；run3 全部 reused。

### P0-4 交付面为零 + 造成它的破坏性脚本 ✅ 成因已修，成片待真渲
- **成因改造**（fix-render）：
  - `rebuild_all_looks_5min.py`：`shots/` → `shots_bak_<stamp>`（rename）→ 渲进干净 `shots/` → 成功 `os.replace` 到 `shots_<look>`，失败自动回滚并留 `shots_failed_<stamp>`；**永不直接 rmtree 生产目录**；`--dry-run` / 退出码。
  - `clear_stale_render_urls.py`：重写，`__main__` 守卫 + **默认 dry-run** + `--apply` 才 commit + 只清 `/media/`。
  - `fix_loudness.py`：先备份再改（audio-only 与 rebuild 路径统一）。
  - `run_episode.py`：看 `templates3d_hq` 退出码，失败不 compose，`main` 返回 1。
- **现状**：`outputs/templates_3d/*/final.mp4` 仍为空 —— 真片需要 GPU（H3/关键帧），本轮**没有**跑 GPU。能力已恢复（P0-2 已用合成片端到端证明），补片请按 §6 的命令跑。

---

## 2. P1 逐条状态

> **21 条：20 条全修、1 条部分（内存优化，见 §5）**。下表每行都附落点与验证方式；⚠️ 部分 仅剩内存一项。

| # | 条目 | 状态 | 落点 / 验证 |
|---|---|---|---|
| 1 | `season/{sid}` 等三处路径穿越 | ✅ | 新增 `backend/app/ids.py`（`SAFE_ID`/`check_id`/`resolve_under`/`rel_to_repo`/`as_bool`）；`v6_agent.py:296`、`v5_series.py:76`、`main.py:423` 全 400；实测 `..%5C..%5C` → 400 |
| 2 | `--reload` 保存即杀 GPU 任务 | ✅ | `backend/run.ps1` **默认不带** `--reload`（新增 `-Reload`，并自动 `ZAOPIANJU_JOBS_RECOVER=0`）；`main.py:205` 恢复开关 + 跳过日志；run.ps1 加 BOM（PS5.1 解析 0 errors） |
| 3 | 非数字 duration → 500 / 假 200 | ✅ | `longform.py:87`、`v6_agent.py:232/507` → **422**；`_as_bool` 全覆盖（`"false"` 不再判 True） |
| 4 | `--stages publish` 自动重跑 GPU | ✅ | `orchestrator._expand(with_deps=False)` 默认不补依赖 + `dep_assumed` 事件；`--with-deps` 恢复旧行为；`--start`+`--stages` 冲突报错 |
| 5 | run_id 同秒撞车 | ✅ | `_make_run_dir`：`<stamp>_<tid>_<urandom4>` + `mkdir(exist_ok=False)` + 重试 3 次（实测两进程目录不同） |
| 6 | `with_muse` 静默翻真跑 | ✅ | `v6_agent.season_run`：`dry_run` 默认**恒为 True**（要真跑必须显式 `dry_run:false`），并回显 `warnings`；显式 `stages` 不再被覆盖成 `["muse"]`，只做补集。验证 `tmp/verify_season_dryrun.py` 0–3 四例全 PASS |
| 7 | `muse.depends=[]` 空跑全绿 | ✅ | 33 份合同 + 两个常量改 `["shots"]`；run 状态新增 **`partial`**（非 optional 阶段被 skip）+ `skipped/failed/needs_assemble` 字段；`muse_apply` 未重组时置 `needs_assemble=true` |
| 8 | `/media {8,40}` vs `ID_RE {1,60}` | ✅ | `ids.SAFE_ID` 与 `RUN_ID_RE{1,80}` 同源；46 字符 run_id 证据 200，`..%5C` → 400 |
| 9 | 短片验收门三处口径 | ✅ | `evaluate_hq_qc` 双写 `qc_report.json` + `qc_shorts.json`；时长门 `0.6×target ≤ d ≤ max(1.5×target, target+2)`；`write_shorts_report` 断言「成片存在且不旧于 QC」（陈旧 → RuntimeError） |
| 10 | `_expand` 不拓扑排序 | ✅ | `workflow.topo_order()` 稳定拓扑；打乱顺序的合同仍按依赖执行 |
| 11 | T2VA/ref2va 只判体积 | ✅ | 新增 `pipeline/fingerprint.py`；命中要求「指纹 + meta.bytes == 实际体积」，指纹含 prompt/seed/尺寸/length/duration/mode/look/style_id（ref2va 另含首帧+音频内容签名）；`force` 真能重渲 |
| 12 | 关键帧 `_render` 体积短路（毒化） | ✅ | 每张候选图各自指纹且渲完立即写 meta；旧图不再被登记成新 `prompt_sig` |
| 13 | HQ 不写 per-shot meta | ✅ | 每镜写 `shots/<sid>.meta.json`（字段与 `h3_comfy` 同名）+ `work/hq_run.json`（meta_ok/missing_prompt_id/duplicate_ids/fallbacks/cache_miss） |
| 14 | `muse_video.available()` 恒 ok | ✅ | 每 provider 显式 `configured`+`missing[]`，`ok=any(configured)`；`_local_generate` 改 `shlex` argv 列表 + timeout（模板语法不变）；**未配置/无 prompt 时 skip 且零网络**（实测原本会真打 api.apimart.ai 拿 400） |
| 15 | `--duration` 不是下限 | ✅ | 两层：① `v5_story._pad_to_target()` 在 build_plan 阶段补齐（先拉长空镜 ≤6.5s，再插空镜 ≤4.5s/镜、≤40 镜，达标线 `target×0.95`，台词镜不动，`meta.pad` 记录 before/after/extended/added）；② `render_v5` 硬门保留兜底。实测 `custom_holo_talk` 30s：7.8s → **28.5s**；40s：7.8s → **38.0s**；`real_street` 35s：26s → 33.25s（只拉长不插镜）；`tonight_show/classics` 40s 不变（已达标）。回归 `tests/test_v49_duration_pad.py` 4 条 |
| 16 | 内存（smooth_noise 350MB / lipsync 1GB） | ⚠️ 部分 | `motion2d._get_layerset` 缓存键补 duration+seed（避免错误复用）；`smooth_noise` 全尺寸 float32 缓存与 lipsync 整段解码**未改**（涉及画质/速度权衡，见 §5） |
| 17 | 跨目录耦合 | ✅ | 见 P0-2：`assemble` 用 `resolve_timeline` 自选时间线，`templates_v5` 只作候选之一；陈旧 `OUT3D/work/episode.json` 不再抢候选 |
| 18 | 发布 `privacy_level` 硬编码公开 | ✅ | `PRIVACY_LEVELS` 三档映射；`unlisted`+非 youtube **默认拒发且零网络**（`PrivacyRejected`）；`dry_run` 放行并回显 `privacy_levels`/`warnings`；preflight 新增「可见性映射」项；台账写 `meta.privacy_levels` |
| 19 | `rebuild_all_looks_5min` 删生产目录 | ✅ | 见 P0-4 |
| 20 | `App.tsx` 导航竞态 | ✅ | `navSeqRef` 三函数共用，守卫在 hash 写回之前；`tsc --noEmit` EXIT=0、`vite build` EXIT=0（dist 已重建） |
| 21 | 测试基础设施 | ✅ | 新增 `pytest.ini`（`testpaths = tests pipeline/tests`、`norecursedirs`）；根 `pytest` 0.25s 收 565 条 |

**顺带修掉的高价值 P2**：`format_id` 零校验（`no_such_format` → **422** + 可用值）；`retry` 对 running 静默 200 → **409**；SSE 在 async 生成器里跑同步 DB → `asyncio.to_thread` + `event: error`；`run_cli` 超时只杀父进程 → `taskkill /T`（`jobs._kill_tree`）；`_jobs` 无上限 → 200 条 FIFO（`v6_agent._remember_job`，实测插 205 条后保留 200）；`jobs.retry_job` 契约写死；`v6_agent` 绝对路径外泄 → `_rel()`；`save_workflow` 加环/值域校验且一律 `ValueError`；`patch_workflow` 空操作不写盘（`noop:true`）；`SystemExit` 不再穿透 orchestrator；`art` 默认参数改 skip；`latest_runs` 过滤季记录并按 `started` 排序；`utils.read_json` gbk 兜底限 <8KB 且失败返回 default；`v5_scene_art._meta_matches` 补 steps/cfg/sampler/scheduler；`motion2d._get_layerset` 缓存键补 duration+seed；`templates3d_hq` 缺 `from pipeline import utils`（`--identity keyframe` 每镜 `NameError` 被吞）；5 个 `scripts/*.ps1` 补 **UTF-8 BOM**（PS 5.1 解析 5/5 OK）。前端 8 条（导航竞态、刷新卡死、三条轮询互斥+可见性判断、`relic_talk_5min`、删除死代码、61 处过期文案、boot 归因+重试上限、Settings 4 字段）。

---

## 3. 资产现状（必须知道的一件"非代码"事）

| 资产 | 现状 | 来源 |
|---|---|---|
| 场景底板 26 张 | **程序化代用**（`procedural: true`），1280×720，无人物无文字，亮度已归一 | `scripts/gen_scene_plates_procedural.py` |
| 角色 cutout 52 份 | 由资产库既有 RGBA cutout **派生布局**（`cutout_2d/front.png` ← `cutout.png`；`cutout_3d/front.png` ← `cutout_25d.png`），provenance 在 `assets/v5/characters/cutout_layout.json` | `scripts/make_cutout_layout.py` |
| 角色资产根 | `pipeline/config.py::CHARS_ROOT`（默认 `assets/v5/characters`，`ZAOPIANJU_CHARS_DIR` 可覆盖）；`style.py/style_v2.py/scenes.py/stage.py` 已改指它（原来写死 `outputs/characters`，只剩 3 个 meta） | 本轮 |
| semantic brief 30 份 | 重复 id 已改 `v5_x###`，每镜带 `act_index`；原文备份为 `*.json.bak`（已加 `.gitignore`） | `scripts/repair_semantic_briefs.py` |

> 要真绘景：`python scripts/gen_scene_plates.py`（SDXL/ComfyUI，需 GPU）。它会直接覆盖同名 png，`procedural.json` 保留旧记录便于追溯。

---

## 4. 复跑清单（验收命令）

```bash
# 1) 测试（应 0 failed）
python -m pytest tests pipeline/tests -q -p no:cacheprovider

# 2) 资产
python scripts/gen_scene_plates_procedural.py            # 补缺失底板
python scripts/make_cutout_layout.py                     # 派生 cutout 布局
python scripts/repair_semantic_briefs.py --dry-run       # brief 体检（幂等）
python tmp/audit_plate_resolve2.py                       # 期望 21/21 命中

# 3) 编排
python tmp/audit_dryrun_all_workflows.py                 # 33/33 planned
python -m pipeline.agents inspect --template tonight_show

# 4) 端点
python tmp/audit_smoke_api.py                            # 162 路由 / 0 重复 / health 200

# 5) 分域自证（队友产出）
python docs/audit/_tools/r4_cache_fingerprint_proof.py   # 缓存指纹：改 prompt 才重渲
python docs/audit/_tools/r4_tts_content_addressing.py    # TTS 内容寻址：不覆盖别人音频
python docs/audit/_tools/r4_muse_available.py            # Muse 可用性诚实判定
python docs/audit/_tools/fix4_verify_agents.py           # 编排 32 项
python docs/audit/_tools/fix4_verify_qc.py               # QC 13 项
python docs/audit/_tools/fix4_verify_contracts.py        # 33/33 合同
python tmp/fix_backend_verify.py                         # 后端 20 项
```

---

## 5. 有意未修 / 需要拍板的（诚实清单）

| # | 项 | 为什么不修 | 建议 |
|---|---|---|---|
| 1 | **真成片（`final.mp4`）** | 需要 GPU（H3 / 关键帧），本轮明确不跑 | 照 `python -m pipeline.agents run --template tonight_show --stages shots,assemble,qc` 跑一条验证；短片用 `custom_tpl_v46_duologue` |
| 2 | `smooth_noise` 350MB 缓存 / lipsync 整段解码 1GB | 属性能优化，改了下采样会影响画质、流式解码要动 ffmpeg 管道 | 单独排期，先加内存上限与告警 |
| 3 | `catalog.root` 绝对路径 | `tests/test_v5_catalog.py` 有 12 条断言它等于 `str(root)`，且 `validate` 会用它反解目录 —— 改成相对会连带破坏合同 | 若要脱敏，走"新增 `root_rel` 字段、保留 `root`"的路子 |
| 4 | 其余 30 处 `HTTPException.detail` 对象 | 分布在 `v5_studio/pipeline_ui/character_*/muse/v5_create/v5_catalog`，改动面大且前端已在用 `err.message` | 统一为字符串时一次做完，别零敲碎打 |
| 5 | `v5_studio._safe_file` 第三套 containment | 行为已是正确的 containment，只是第三份实现 | 下次动那个文件时顺手换 `ids.resolve_under` |
| 6 | `studio2d/` 1838 行死代码 | 整包删除需要确认没有外部脚本引用（本轮只确认"全仓零调用"） | 单独一个 PR 删，附 `git grep` 证据 |
| 7 | Muse `_orig` 逐镜失效 | 需要 `templates3d_hq` 单镜重渲时回调清缓存，跨两个模块 | 与"重渲"入口一起改 |

---

## 6. 本轮改动的文件（按域）

| 域 | 文件 |
|---|---|
| 新增 | `backend/app/ids.py`、`pipeline/fingerprint.py`、`pytest.ini`、`scripts/gen_scene_plates_procedural.py`、`scripts/make_cutout_layout.py`、`scripts/repair_semantic_briefs.py`、`tests/test_v49_timeline.py`、`tests/test_privacy_levels.py` |
| 后端 | `backend/app/main.py`、`routers/{v6_agent,v5_series,longform}.py`、`backend/app/jobs.py`、`backend/run.ps1` |
| 发布 | `publish/upload_post.py` |
| 渲染 | `pipeline/{templates3d,templates3d_hq,render_v5,tts_breeze,qc_v5,lipsync,muse_video,motion2d,scenes,stage,style,style_v2,v5_scene_art,v5_catalog,utils,config}.py` |
| 编排 | `pipeline/agents/{orchestrator,workflow,ops,cli,registry}.py`、`contract/v6/workflows/*.json`（33 份仅改 `muse.depends`） |
| 脚本 | `scripts/{expand_3d_shots,rebuild_all_looks_5min,clear_stale_render_urls,fix_loudness,run_episode}.py` |
| 前端 | `frontend/src/{App.tsx,components/library/LibraryPage.tsx,components/agent/AgentPage.tsx,lib/{templates,v5,agent,api}.ts,types.ts,components/platform/PlatformHome.tsx}` + 61 处文案 + `frontend/dist`（重建） |
| 资产 | `outputs/scene_plates/*.png`（26 + `procedural.json`）、`assets/v5/characters/*/cutout_{2d,3d}/front.png`（52 + `cutout_layout.json`）、`contract/v5/cinema3d/templates3d/*_semantic.json`（30 份修 id + `act_index`） |
| 文档 | `AGENTS.md`（模板数/栈/验收门/`--with-deps`/资产说明）、`docs/audit/R4_FIX_ACCEPTANCE.md`（本文件）、`.gitignore`（`*.json.bak` 等） |

---

## 7. 一句话交接

链路已经通了：**底板在、角色在、时间线对得上、缓存不会假命中、QC 不再是弱门、发布会拦下"永远公开"、测试 0 红**。
剩下的是**跑真片**（GPU）和三个需要拍板的口径（时长配比、内存/流式解码、季 dry_run 默认）——见 §5。
