# 数据 / 测试 / 文档 一致性体检（AUDIT_data_tests_docs）

- 仓库：`D:\BaiduSyncdisk\wmj_work\XJ\AiJue`（Python 3.11.9 / pytest 9.0.3 / torch 2.14.0+cpu / Windows）
- 审计时间：2026-11-06 23:25 → 2026-11-07
- 方法：**全部用脚本核对**（不改源码、不改合同、不改输出）。脚本与证据 JSON：
  - `docs/audit/_tools/audit_contract.py` → `contract_audit.json`（清单三处一致性 / 资源引用 / JSON 合法性 / 交叉引用）
  - `docs/audit/_tools/audit_semantic_shots.py` → `semantic_shots.json`（语义 brief 镜数 / 时长 / shot id 重复）
  - `docs/audit/_tools/audit_docs_claims.py` → `docs_claims.json`（CLI / API 路由 / 默认值 / 模板计数 / outputs 现状）
  - 日志（gitignore 内）：`tmp/audit/pytest_tests_utf8.log`、`tmp/audit/pytest_pipeline_utf8.log`、`tmp/audit/pt_group{1,2}.log`
- 只读纪律：没有执行任何渲染、没有写 `contract/` 或 `outputs/`；唯一新增文件是本文 + `docs/audit/_tools/*`。

## 概览（每项一句话结论）

| 项 | 一句话结论 |
|---|---|
| **A 合同与资产** | 合同层是**健康**的（370 个 JSON 全部合法、必填字段齐、contract validate 0 error / 0 warning、runtime 33 模板 ↔ 33 工作流完全对齐），但**语义 brief 层已经漂了**：30 个 `templates3d/*_semantic.json` 被本地扩写到 63–93 镜（`git show HEAD` 基线全部在 **4–11 镜**），其中 13 个 brief 出现 `v5_e001` 重复 2–9 次，且 9 个短片模板的 brief 时长 = 目标时长的 **16–22 倍**。 |
| **B 测试现状** | `pytest tests` = **4 failed / 311 passed / 57 skipped**（9–11s），`pytest pipeline/tests` = **16 failed / 170 passed**（63s）：**两套都是红的**。20 条红里 **真 bug 只有 1 条**（`lipsync.mouth_audio_sync` 对缺失输入抛 `CalledProcessError`），**1 条数据漂移**（brief 4→89）、**1 条测试夹具过期**（`_Ctx` 缺 `seed`）、**17 条本地产物缺失**（`outputs/scene_plates/*.png` 与 `final.mp4` 全被清空）。另外文档里的 `--timeout=300` 直接报 `unrecognized arguments`（未装 `pytest-timeout`）。 |
| **C 文档漂移** | 路由与 CLI 子命令**全部真实存在**（9/9 逐条命中），但**数字与格式类声称普遍过期**：工具 13→**18**、角色 50→**26**、模板拆分 16+17→**21+12**、工作流"33 份 v2"→**16 份 v1 + 17 份 v2**、V5 栈 17→**22**、`pytest` 用例数 343/185→**372/186**；`--only-missing` 只存在于季 API（CLI 没有）；`dry_run` 默认其实是 `not with_muse`。 |

---

## A 合同与资产

### A.1 模板清单三处一致性（脚本：`audit_contract.py`）

| 来源 | 数量 | id 集合 |
|---|---|---|
| `contract/templates_v3/*.json`（v3 历史层） | **16** | 16 内建综艺 |
| `contract/v5/cinema3d/templates3d/*.json`（去 `_semantic` / `.bak`） | **30**（文件 46） | 16 内建 + 3 real_* + 11 custom_tpl_* |
| `contract/v6/workflows/*.json` | **33** | 全部 33 模板 |
| `contract/v5/templates` + `contract/v5/custom/templates`（`v5_catalog.load_catalog` 真实注册表） | **21 + 12 = 33** | 33 |

不一致清单：

| 现象 | 具体 | 判定 |
|---|---|---|
| 只有工作流、没有 templates3d 合同/brief | `custom_holo_talk`、`relic_talk`、`relic_talk_3d` | **P2**（这 3 个本来就不是 3D 样片线，缺 brief 可接受，但 templates3d 目录语义 = "33 模板全量 brief" 已不成立） |
| 只有合同、没有工作流 | **无** | ✅（`workflows_without_runtime_template` = 空，`runtime_templates_without_workflow` = 空） |
| workflows 条数与声称 | 声称 33 → 实际 33 | ✅ |
| **"33 个模板（16 内建 + 17 custom）"** | 实际 **21 内建 + 12 custom**（`custom_counts`：templates=12） | **P2 文档漂移**（总数对，拆法错） |
| templates3d 文件数 46 vs id 数 30 | 16 个 `<id>.json` + 30 个 `<id>_semantic.json` | 说明：`<id>.json`（非 semantic）只剩 16 个内建，custom/real 只有 `_semantic` 版本 —— 命名不对称，脚本已按 `_semantic` 归一 |

### A.2 模板引用的资源是否真实存在

| 检查 | 结果 |
|---|---|
| `cast[].character` → `contract/v5/characters/<id>.json` | **0 缺失**（33 模板全部命中） |
| 角色资产 `assets/v5/characters/<id>/meta.json` + `portrait.png` | **0 缺失** |
| 角色 `voice.voice_id` → `contract/v5/voices.json`(+custom) 的 24 个 preset | **0 未注册** |
| `acts[].scene` → `contract/v5/scenes/*.json`（含 custom/scenes，共 74） | **0 缺失** |
| 场景 plate `assets/v5/scenes/<scene>.png` | **0 缺失** |
| `style` → `contract/v5/styles`(+custom) 17 个风格 | **0 未注册** |
| `render_style` → `pipeline/templates3d_hq.py::STYLE_2D_STYLES`（`arcane_cinematic / guoman / jp_anime / retro_cel / soft_watercolor`） | **0 未注册** |
| **`acts[].scene` → `contract/scene_map.json`（17 key）/ `scene_descriptions.json`（15 key）** | **33/33 模板都命中缺失**（268 条去重命中） |
| `contract/templates_v3/*.json` 的 `stage.plate` → `outputs/scene_plates/<plate>.png` | **16/16 缺失**（该目录只剩 `meta.json` / `meta_3d.json` / `meta_styles.json`） |

结论：

1. **v5 合同 ↔ 资产是完整的**（角色/场景/风格/音色全绿），这是本轮最正面的一条。
2. `scene_map.json` / `scene_descriptions.json` 是 **v3 时代遗留**（17 / 15 个旧场景），**不是 v5 的 scene 注册表**（v5 注册表是 `contract/v5/scenes`，74 个）。把这两张表当"模板 scene 必在表内"的校验依据会得到 33/33 假阳性 —— 建议在文档里降级为"v3 专用"，或直接标记 deprecated。
3. `templates_v3` 的 16 张 plate **全部不在仓库里**（`outputs/` 被 `.gitignore` 忽略），这是 B 部分 19 条红的根因。

### A.3 `contract/**/*.json` 合法性 / 空文件 / 必填字段

| 指标 | 值 |
|---|---|
| JSON 文件总数 | **370**（`PART_PIPELINE` 记为 353，已变化：v6 workflows 16→33） |
| 非法 JSON | **0** |
| 空文件 / `{}` / `[]` | **0** |
| 缺必填字段 | **0**（workflow 逐个校验 `id/version/stages/stack`、stage id 唯一、`depends` 目标存在、`tool` ∈ TOOLS 18 个） |
| `python -m pipeline.v5_cli validate` | `== OK errors=0 warnings=0 counts={'styles':17,'scenes':74,'characters':26,'props':44,'templates':33,'packs':2}` —— **PLAN §3 硬门 #1 仍绿** |

> 备注：`contract/v5/custom/characters` 26 个覆盖片段 + 内建 24 个 = catalog 去重后 **26 个角色**（不是 50）。

### A.4 交叉引用完整性

| 检查 | 结果 |
|---|---|
| series `members[].template` 是否存在模板 | ✅ `undercity_arc` 3 集全部存在；`cover=custom_tpl_v46_undercity` 存在 |
| series `season` 与 PLAN 归档口径 | ✅ `target_count=3`、`episode_sec=[13,25]`、`acts=[4,6]`，与 `docs/PLAN_V49_SHORTS_SEASON.md` §4 一致 |
| workflow `stack` 与 `is_shorts()` 判定一致 | ✅ 33/33（`v5_render` 22 + `hq_shorts` 11） |
| **语义 brief 镜数 / 时长 vs 模板 `acts` / `target_duration`** | ❌ 见 A.5（30/30 命中） |

### A.5 语义 brief 漂移（新增证据，`audit_semantic_shots.py`）

30 个 `templates3d/*_semantic.json` 全部存在，但内容与模板合同已不对应：

| 模板组 | 模板数 | acts | brief 镜数 | brief 时长合计 | 模板 target_duration | 时长比 |
|---|---|---|---|---|---|---|
| 内建综艺 | 16 | 7–9 | 63–79 | 295–315s | 38s | **7.8–8.3×** |
| 写实 real_* | 3 | 6 | 64 | 306–309s | 34–35s | **8.8–9.1×** |
| v22/v23 | 2 | 4–6 | 81–83 | 315–317s | 15.15–23.45s | **13.5–20.8×** |
| **V45/V46 短片（HQ 栈）** | **9** | **4–6** | **89–93** | **307–314s** | **13.68–19.14s** | **16.1–22.5×** |

最刺眼的一行（也是 B-② 红的直接原因）：

```
custom_tpl_v46_undercity  acts=4  brief_shots=89  brief_duration=313.64s  target=14.12s  22.21×
```

**HEAD vs 工作区**（同一文件的 git 证据，能直接定性"测试过期"还是"数据漂移"）：

| 文件 | `git show HEAD:` 里的 shots | 工作区 shots |
|---|---|---|
| `custom_tpl_v46_undercity_semantic.json` | **4** | **89** |
| `tonight_show_semantic.json` | **11**（无重复 id） | **76**（`v5_e001` × 9） |

全量核对（30 个 brief 逐个 `git show HEAD:<path>` 数 shots）：**HEAD 基线镜数分布 = {4, 6, 7, 8, 9, 10, 11}（min 4 / max 11），工作区 = 63–93** → 扩写是 **30/30 全量**，不是个别文件。

`git status -- contract/v5/cinema3d/templates3d/` = **30 M + 30 ??（`.bak`）** → 30 个 brief 全部被本地脚本改写且未提交；`tests/test_v45_style_polish.py` **未被修改**（不在 status 里）。
→ 结论：`len(brief["shots"]) == len(tpl["acts"])` 在 HEAD 上**成立**，红是**数据被扩写**造成的，不是断言写错；但这条断言本身也过于脆弱（见 P1-4）。

### A.6 shot id 重复（新增证据，根因已定位）

13 个 brief 出现重复 id，全部是 `v5_e001`：

| 模板 | `v5_e001` 出现次数 |
|---|---|
| `tonight_show` / `history_mystery` / `roundtable` | **9** |
| `shark_tank` / `snl_sketch` | 7 |
| `hot_ones` | 5 |
| `custom_tpl_v46_duologue` / `mystery` / `traitors` | 4 |
| `history_talk` / `slow_life` | 3 |
| `qipashuo` / `real_street` | 2 |

**根因（`scripts/expand_3d_shots.py`）**：

```python
# s67
clone["id"] = f"v5_e{added + 1:03d}"      # 计数器每次运行都从 1 开始，且不与已有 id 去重
...
# s88-92
backup = path.with_suffix(".json.bak")
if not backup.is_file():                   # 只有第一次跑才备份
    shutil.copy2(path, backup)
data["shots"] = shots                      # 永远 append，不幂等
```

- 每跑一次就追加一批 `v5_e001..v5_eNNN`；跑 N 次 → `v5_e001` 出现 N 次（`tonight_show` = 9 次，说明至少跑了 9 次）。
- 消费端把 `shot["id"]` 直接当文件名：`pipeline/templates3d_hq.py:913/918/930-931/980`（`out_shots / f"{shot['id']}.mp4"`）→ **重复 id 的 9 个镜头共写同一个 mp4**，实际产出序列会静默少 8 镜；同时 line 913 用**全部** brief shots 迭代 → 88 个额外镜头就是 22× 的 H3 渲染成本。
- 另外 30 个 `.bak` 落在 `contract/` 里且**未被 git 跟踪**（`.gitignore` 没盖 `.bak`），属于合同目录污染。

---

## B 测试现状

### B.1 命令与实测输出

| 命令 | 结果 |
|---|---|
| `python -m pytest tests -q -x --timeout=300 -p no:cacheprovider` | ❌ **命令本身失败**：`ERROR: usage: __main__.py [...] __main__.py: error: unrecognized arguments: --timeout=300`（环境没有 `pytest_timeout`）。建议把 `--timeout` 从所有脚本/文档里去掉，或把 `pytest-timeout` 写进 dev 依赖。 |
| `python -m pytest tests -q -p no:cacheprovider` | **4 failed, 311 passed, 57 skipped in 9.36s**（4 次采样：11.13s / 11.27s / 9.36s / 9.76s，结论稳定）。实测**不需要 GPU、不下载权重**（torch 是 `2.14.0+cpu`，57 个 skip 全是"产物未落盘 / 参数化为空"，不是 GPU 门槛）。 |
| `python -m pytest tests -q --collect-only` | **372 tests collected in 0.24s**（收集不慢，不存在"torch import 卡死"——本仓 torch 走 CPU 且测试不 import torch） |
| `python -m pytest pipeline/tests -q -p no:cacheprovider` | **16 failed, 170 passed in 62.89s**（186 collected） |

> 与 Lead 基线一致（4/311/57）。为拿全清单我去掉了 `-x`（`-x` 会在第一条红就停，拿不到分类），这是本报告唯一偏离指定命令的地方。

### B.2 `tests/` 4 条红：分类

| # | 用例 | 真实错误 | 分类 |
|---|---|---|---|
| ① | `test_v3_acceptance.py::test_templates_validate_strict` | `16/16 严格校验未通过：{... 'stage.plate: 资产缺失 outputs/scene_plates/xxx.png' ...}`（16 个模板各一条） | **环境缺依赖（本地产物缺失）**。`outputs/scene_plates/` 只剩 3 个 meta json，0 张 png；`outputs/` 在 `.gitignore` 内，无版本可回溯 |
| ② | `test_v45_style_polish.py::test_cyber_scene_templates_and_scenes` | `assert 89 == 4`（brief 89 镜 vs 模板 4 幕） | **数据漂移**（HEAD 是 4 镜 → 断言当时成立）。断言设计脆弱，见 P1-4 |
| ③ | `test_v53_agents.py::test_muse_video_skips_without_provider` | `AttributeError: '_Ctx' object has no attribute 'seed'`（`registry.py:541`） | **测试夹具过期**。真 `StageContext` 有 `seed`（`registry.py:26`）；`_Ctx`（`tests/test_v53_agents.py:24-36`）没跟上新增字段 |
| ④ | `test_v5_2_acceptance.py::test_frontend_api_live` | `/media/templates_3d/tonight_show/final.mp4 Range -> 404` | **环境缺依赖**：8090 服务**确实活着**（`GET /api/v5/voices` → 200），但 `outputs/templates_3d` **31 个目录里 0 个 `final.mp4`** |

### B.3 `pipeline/tests` 16 条红：分类

| 分类 | 数量 | 用例 |
|---|---|---|
| **缺 `outputs/scene_plates/*.png`（真·产物缺失）** | **13** | `test_scenes.py` 3（`history_study.png` 打不开）、`test_style.py` 1、`test_style_v2.py` 1（`debate_hall__debateink.png`）、`test_v3_stage.py` 7（`StageError: 缺少 stage.plate 资产: late_night_desk__cinematic / history_study__ink / archive_room__ink`）、`test_v3_render.py::test_render_v3_orchestration_mock_h3`（`AssertionError: 应产出 final.mp4`，上游就是 plate 缺失→渲染中止） |
| **缺角色 cutout 资产** | **1** | `test_v3_stage.py::test_history_talk_uses_cutout_2d` → `{'fallback': True, 'kind': 'missing', 'note': 'cutout_2d: 文件缺失；cutout_3d: 文件缺失'}` |
| **上游无成片导致 ffprobe 失败** | **1** | `test_v4_render_integration.py::test_render_v3_v4_speech_driven_mock` → `ffprobe ... final.mp4: No such file or directory`（同一根因：plate 缺失 → 不产 final） |
| **真 bug** | **1** | `test_lipsync_unit.py::test_measure_sync_returns_contract_dict` → `subprocess.CalledProcessError`（ffprobe 失败）。测试调用 `L.measure_sync("fake.mp4","fake.wav")` 并期望返回 `{"ok": false, "reason": ...}`；`pipeline/lipsync.py:1056` 的 `info = probe(clip_mp4)` **没有异常兜底**，对不存在的输入直接抛 `CalledProcessError`，违反 `measure_sync` 自己声明的 dict 契约 |

### B.4 现在 `pytest` 是绿还是红

**红。** 20 条红的总账：

| 归类 | 条数 | 说明 |
|---|---|---|
| 真 bug | **1** | `lipsync.mouth_audio_sync` 缺缺失输入的兜底（`pipeline/lipsync.py:1056`） |
| 数据漂移（brief 被本地扩写；断言在 HEAD 上成立，但设计也脆弱 → P1-4） | **1** | `test_cyber_scene_templates_and_scenes` 用"幕数=镜数"当合同 |
| 测试夹具过期 | **1** | `tests/test_v53_agents.py::_Ctx` 缺 `seed` |
| 环境/产物缺失（本地 `outputs/` 被清空 + 依赖 8090 活服务） | **17** | 见 B.2 ①②④、B.3 前 3 行 |

即：**测试代码本身没有 17 条产品 bug**；红的绝大部分是"仓库里没有产物"这条环境事实的投影。

### B.5 仓库根跑 `pytest` 会卡死（真实开发体验缺陷 + 一行修法）

- 根目录**没有** `pytest.ini` / `pyproject.toml` / `setup.cfg` / `tox.ini` → 无 `testpaths`、无 `norecursedirs`。
- 全仓 `test_*.py` 共 **4763** 个：`tmp/` **381** 个、`local_models/` **4345** 个（MuseTalk 的上游测试），`tests/`（21）+ `pipeline/tests/`（16）合计只有 **37** 个。
- 我用等价的静态证据替代了那次 10 分钟实跑（Lead 已实测），结论：根级收集会把 4000+ 个外部用例拖进来。

**建议（一行配置，需在仓库根新增 `pytest.ini`）**：

```ini
[pytest]
testpaths = tests pipeline/tests
norecursedirs = .git tmp local_models outputs tools frontend node_modules .pytest_cache
```

（我按纪律没有创建该文件。）

### B.6 skip 画像（57 条，全部环境门控）

| 原因族 | 条数 |
|---|---|
| `V5/V5.2/V5.4/V4 批量进行中：可验收 0/16` | 13 |
| `got empty parameter set for (tid)/(template_id)`（参数化为空） | 32 |
| `outputs/templates_v3 尚无任何模板落盘（等 Lead 渲染）` / `渲染进行中：已落盘 0/16` / `history_talk 尚未渲染…` | 6 |
| `后端 8090 未重启，V5.4 路由 404` | 1 |
| 其他（legacy 归档、全局 prompt_id 复核） | 5 |

---

## C 文档漂移（声称 → 现实 → 结论）

依据：`AGENTS.md`、`README.md`、`docs/PLAN_V49_SHORTS_SEASON.md`；核对脚本 `audit_docs_claims.py`。

### C.1 CLI（AGENTS §3）

| 声称 | 现实（`build_parser()` 实测） | 结论 |
|---|---|---|
| `list` / `inspect` / `run` / `edit` | 全部存在 | ✅ |
| `--stage-params` | `run --stage-params` 存在（`{"muse":{"apply":true}}` 可用） | ✅ |
| `--only-missing` | **CLI 没有这个开关**；`only_missing` 只是 `POST /api/v6/agent/season/{sid}/run` 的字段（默认 `True`） | ❌ **P2**（把 API 字段写成了 CLI 参数） |
| （未声称） | 还有 `status`、`workflow` 两个子命令；`run` 还有 `--start/--dry-run/--force/--seed/--goal`；`edit` 还有 `--json-file/--stage`、`op` 还有 `set_stage_param/reset_workflow` | P3：文档没覆盖，但不影响可用性 |
| 示例 `edit --op patch_template/ patch_workflow {"enable":["h3"]}` | `ops.py` 的 `patch_template` / `patch_workflow`（支持 `enable/disable/params/stages`）都真实存在 | ✅ |
| `python scripts/seed_v6_workflows.py` + `--force` | 存在，`--force` = 覆盖 | ✅ |

### C.2 API 路由（AGENTS §4/§5）

| 声称 | 现实（从 `app.routes` 逐条匹配） | 结论 |
|---|---|---|
| `GET /api/v6/agent/series` | 命中 | ✅ |
| `POST /api/v6/agent/season/{sid}/run`（`dry_run` / `only_missing`） | 命中；`only_missing` 默认 True ✅、`dry_run` 默认 `not with_muse` | ⚠️ **P2**：AGENTS 说"`dry_run` 默认 true"，但勾 `with_muse:true` 时默认变**真跑** |
| `GET /api/v6/agent/season/{sid}/status` | 命中 | ✅ |
| `PUT /api/v6/agent/workflow/{template}` | 命中（`GET` 也在） | ✅ |
| `POST /api/v5/series/{sid}/export?force=true` | 命中（`v5_series` prefix `/api/v5`） | ✅ |
| `GET /api/v6/muse/status`、`POST /api/v6/muse/sync`、`GET /api/v6/muse/jobs/{id}` | 全部命中；另还有 `/video`、`/video/status`、`/video/jobs/{id}` 未写进 AGENTS | ✅（P3：漏记 3 个） |
| 端点总数（README "142 个 API 端点"） | `method+path = 159` / `unique path = 141` | P3：口径近似，不算漂移 |

### C.3 默认值与常量

| 声称 | 现实 | 结论 |
|---|---|---|
| 默认 3 集 | 代码**没有默认值**；`undercity_arc.season.target_count = 3` 是合同数据，`check_seasons.py:68` / `v6_agent.py:321` 只做一致性校验 | ⚠️ **P2**：是"当前这一季的配置"，不是"系统默认" |
| 13–25s / 集 | 合同 `season.rules.episode_sec=[13,25]` + PLAN §4 一致；9 个短片模板实际 13.68–19.14s ✅ | ✅ |
| `max_frames=600` | `pipeline/lipsync.py:887 / 907 / 1042` 三处默认 600 | ✅ |
| muse 锁路径 `tmp/v5_lipsync/.muse.lock` | `pipeline/lipsync.py:802` | ✅ |
| workflows 33 份 | 33 份 ✅ | ✅ |
| workflows "**v2 格式带 `stack`**" | `stack` 33/33 都有 ✅；但 `version` = **16 份 v1 + 17 份 v2**（v1 恰好是 16 个内建综艺） | ❌ **P2** |
| 两条栈：V5 栈 = "16 内建 + `custom_holo_talk`" | 实际 `v5_render` **22**（16 内建 + real_cafe/real_kitchen/real_street + relic_talk + relic_talk_3d + custom_holo_talk），`hq_shorts` **11**（全部 `custom_tpl_*`） | ❌ **P2** |
| `render_2d_hq` 默认 `--reuse-shots` | `workflow.py:73` `{"reuse_shots": True}` ✅，工作流 JSON 里 `custom_tpl_v46_duologue` 也带 `reuse_shots: true` | ✅ |

### C.4 能力/规模类数字（README §V7–V49）

| 声称 | 现实（catalog / 目录实测） | 结论 |
|---|---|---|
| 33 个模板（16 内建 + 17 custom） | 33 总数 ✅，**21 内建 + 12 custom** | P2 |
| 17 种风格 | 17（16 + custom_holo_paper） | ✅ |
| 74 个场景 | 74（73 + 1 custom） | ✅ |
| **50 个角色** | **26**（24 内建 + 26 custom 覆盖去重；内建/custom 是同一批 id 的覆盖关系，不能相加） | ❌ P2 |
| 44 个道具 | 44 | ✅ |
| 16 份剪辑档案 | 16 | ✅ |
| **8 角色 + 13 工具** | 角色 8 ✅ / 工具 **18**（`assemble_episode, build_story, build_storyboard, check_story, evaluate_hq_qc, evaluate_qc, generate_art, list_art, muse_lipsync, muse_video, plan_editing, render_2d_hq, render_h3, render_shots, set_pose, synthesize_voices, write_report, write_shorts_report`） | ❌ P2 |
| AGENTS §1 角色→工具表 | `roles.py` 的 `tools` 字段**混进了不存在的工具**：`producer: run_workflow/inspect_workflow`（TOOLS 里没有）、`voice_director: set_voice`、`editor: patch_editorial`（这两个是 `ops.py` 的操作，不是 registry 工具）；同时 `check_story / muse_video / assemble_episode / write_shorts_report` **没有挂到任何角色** | P3 |
| tests 规模 "21 文件 343 用例 + 17 文件 185 用例" | **21 个测试文件（+conftest 共 22 个 .py）372 用例** + **pipeline/tests 17 个 .py（16 个测试文件）186 用例**（合计 558） | P2 |
| "V52/V53 回归 491 / 501 passed" | 当前两套合计 481 passed / 20 failed / 57 skipped | P3（历史数字，口径不可复现） |

### C.5 PLAN_V49 的硬门现实

| PLAN §3 硬门 | 现状 | 结论 |
|---|---|---|
| ① `contract` validate：0 error / 0 warning | `pipeline.v5_cli validate` → `OK errors=0 warnings=0` | ✅ 绿 |
| ② `pytest tests -q`：0 failed | **4 failed** | ❌ **P0（验收门现在是红的）** |
| ③ 每集 `outputs/templates_2d/<id>/final.mp4` + `qc_report.json.ok` + `poster.jpg` | `templates_2d` 9 个目录 **0 个 final.mp4** | ❌ 红（产物被清空） |
| ④ 每季 `outputs/series/<sid>/series.mp4` + `manifest.json` | `outputs/series/undercity_arc` 只剩 `manifest*.json` + `concat*.txt`（3 份 manifest / 2 份 concat），**`series.mp4` / `title_card.mp4` / `poster.jpg` 全部不在** | ❌ 红（PART_PIPELINE §4.2 记录的 15.05MB `series.mp4` 已被删除） |
| ⑤ 页面 `#/agent`、`#/library/final` 0 console error | 未在本次范围（8080/8090 后端活） | — |
| ⑥ 每次 run 三件证据 | `outputs/agent_runs` 8 个旧 run 仍在（PART_PIPELINE §4.3） | ✅ 历史证据在 |

---

## 前两轮已有结论对照（`PART_PIPELINE.md` / `REVIEW_BACKEND_ROUND2.md`）

| 前轮结论 | 本轮状态 | 标记 |
|---|---|---|
| PART §2.1 contract JSON 353 个 | 现在 **370**（v6 workflows 16→33） | **已变化** |
| PART §2.4 / §5.2 "V6 只覆盖 16/33 模板，17 个无 workflow" | **33/33 全覆盖**，stack 与 `is_shorts` 逐条一致 | **已变化（已修）** |
| PART §5.11 `custom_holo_talk` 无 workflow / 无 editorial | workflow 已补（现 33 份），editorial 仍 16 份（custom 全部无 editorial） | **部分已变化** |
| PART §4.1 `templates_3d` 30/30 有 `final.mp4`、`templates_2d` 9/9 | **templates_3d 31 目录只有 3 个 `final_300s_*.mp4`（classics，2026-11-06 重渲中）；templates_2d 0；templates_v5 0/34（连 relic_talk_3d 96MB 也没了）；templates_v3 0/18；`outputs/series/undercity_arc` 的 `series.mp4` 也已删除** | **已变化（产物被清空/重渲）**，与 B-④ 一致 |
| PART §5.1 "templates_v5 33/34 无 final，只有 relic_talk_3d 有" | 现在 **34/34 全无** | **已变化（更糟）** |
| PART §3.3 / §5.12 "pipeline/agents 9 模块零测试引用" | 现在有 `tests/test_v53_agents.py`（10 条，覆盖 registry/workflow/ops/deliver/utils） | **已变化（已补，部分）** |
| PART §4.4 "outputs/scene_plates 45 张场景 plate" | 现在 **0 张**（只剩 3 个 meta json） | **已变化**，是 B 部分 14 条红的直接原因 |
| PART §5.3 H3 全关 / §5.5 art 全关 | 33 份工作流里 `h3=0/33`、`art=0/33`、`muse=0/33`、`musev=0/33` 启用 | **未变化（已知）** |
| PART §2.2 "角色 24 内建 + 26 custom"（相加 50，README 的 50 即此口径） | catalog 去重 **26**；本轮把"50"定性为**口径错误** | 本轮新证据 |
| REVIEW_BACKEND_ROUND2 P2-7 "pipeline 层仍写 `repo/outputs`" | 仍在（`registry.py` / `orchestrator.py` 走 `_outputs_root()`/`REPO/outputs`），本轮未复核其行为影响 | 已知，未变化 |
| — | **新增**：语义 brief 被本地扩写（30 M）+ `v5_e001` 重复 id（13 文件）+ 短片 brief 22× 目标时长 | **本轮新证据** |

---

## P0 / P1 / P2 汇总

### P0（阻断交付或会让产物错）

| ID | 标题 | 证据 | 建议 |
|---|---|---|---|
| **P0-1** | 30 个 `templates3d/*_semantic.json` 被本地扩写且未收敛，短片 HQ 栈的镜头输入不可信（89 镜 / 313s vs 4 幕 / 14s） | `git status` = 30 M + 30 `.bak`；HEAD `undercity`=4 镜、工作区=89；`templates3d_hq.py:913` 用全部 brief shots 迭代 | 二选一：① `git checkout --` 这 30 个 brief 回到 4–11 镜基线；② 把扩写产物移出 `contract/`（如 `tmp/` 或 `contract/v5/cinema3d/expanded/`）并在 `check_story` 加"brief 时长 ≈ target_duration ±30%"的门 |
| **P0-2** | `outputs/` 产物被清空：`templates_3d 31 目录 0 final.mp4`、`templates_2d 0`、`templates_v5 0/34`、`templates_v3 0/18`、`series/undercity_arc` 只剩 manifest（`series.mp4` 没了）、`scene_plates 0 png` | `audit_docs_claims.py` → `outputs` 表 + `scene_plates.png=0` + `outputs/series/*` 实际列表；**17 条测试红由此产生**（B.2 ①②④ + B.3 前 3 行） | 重跑 `assemble` 补齐 `templates_v5/<id>/final.mp4`；重新生成 16 张 `scene_plates/*.png`（`scripts/gen_v3_plates.py`）；重导季合集（`POST /api/v5/series/undercity_arc/export?force=true`）；把"验收所需的产物"从 `.gitignore` 里单独放行（或建 `delivery_manifest.json` + 校验脚本） |

### P1（真 bug / 会误导判断）

| ID | 标题 | 证据 | 建议 |
|---|---|---|---|
| **P1-1** | 13 个 brief 的 shot id 重复（`v5_e001` ×2–9），消费端用 id 当文件名 → 镜头互相覆盖、镜数静默丢失；扩写脚本不幂等 | `expand_3d_shots.py:67`（计数从 1 起）+ `:88-92`（只首跑备份）；`templates3d_hq.py:931` | id 加模板/时间前缀（`v5_{tid}_e{n}`）或 uuid4；写入前对现有 id 去重；`.bak` 落到 `tmp/` 并加 `.gitignore` |
| **P1-2** | `--timeout=300` 不可执行（环境无 `pytest-timeout`）；根级 `pytest` 会递归 4763 个外部 `test_*.py` | 命令报 `unrecognized arguments`；`tmp/` 381 + `local_models/` 4345 | 新增 `pytest.ini`（`testpaths = tests pipeline/tests` + `norecursedirs`）；去掉 `--timeout` 或把插件写进 dev 依赖 |
| **P1-3** | 真 bug：`lipsync.mouth_audio_sync` 对缺失输入抛 `CalledProcessError`，违反 `measure_sync` 的 `{"ok", "reason"}` 契约 | `pipeline/lipsync.py:1056` `info = probe(clip_mp4)`（无 try） | `try/except` 包住 `probe` + `_ffmpeg_frames_in`，返回 `{"ok": False, "reason": ...}` |
| **P1-4** | 测试把"brief 镜数 == 模板幕数"当合同，任何 brief 扩写都会红（且不校验时长） | `tests/test_v45_style_polish.py:166` | 改成覆盖式断言：brief 的 `scene` 集合 ⊇ 模板 acts 的 `scene` 集合 + `abs(sum(brief.duration) - target_duration) / target_duration < 0.35` |

### P2（口径/数据卫生，会在下一轮继续制造误判）

| ID | 标题 | 证据 |
|---|---|---|
| P2-1 | 文档模板拆分 16+17 → 实际 21+12 | `audit_docs_claims.py` → `builtin_templates=21, custom_templates=12` |
| P2-2 | "33 份 v2 工作流" → 16 份 v1 + 17 份 v2 | `workflows.versions = {1:16, 2:17}` |
| P2-3 | "V5 栈 = 16 内建 + custom_holo_talk(17)" → 实际 22 | `workflows.stacks = {v5_render:22, hq_shorts:11}` |
| P2-4 | "8 角色 + 13 工具" → 工具 18；`roles.py` 还挂着 `run_workflow/inspect_workflow` 这类不存在的工具名 | `docs_claims.counts.tools=18`；roles↔TOOLS diff |
| P2-5 | "50 个角色" → 26（24 内建 + 26 custom 覆盖 ≠ 相加） | `catalog.counts.characters=26` |
| P2-6 | CLI 无 `--only-missing`（只是季 API 字段） | `build_parser()` 的 `run` 选项表 |
| P2-7 | `dry_run` 默认值是 `not with_muse`，不是恒定 true | `backend/app/routers/v6_agent.py:464` |
| P2-8 | "默认 3 集"是合同数据（`undercity_arc.season.target_count`），代码无默认 | `v6_agent.py:321`、`check_seasons.py:68` |
| P2-9 | README 测试规模 "343 用例 / 185 用例" → 实测 372 / 186（文件数 21 / 16 与 README 的 21 / 17 基本一致，差在 pipeline/tests 把 `__init__.py` 也数进去了） | `--collect-only` |
| P2-10 | `scene_map.json`(17) / `scene_descriptions.json`(15) 是 v3 遗留，被当成 v5 校验依据 → 33/33 假阳性 | `contract_audit.json` → `resource_summary` |
| P2-11 | `contract/` 内 30 个未跟踪 `.bak` | `git status ?? 30` |

### P3（低风险/顺带）

- `roles.py` 的 `tools` 字段把 ops 当工具（`set_voice` / `patch_editorial`），另有 4 个工具（`check_story/assemble_episode/muse_video/write_shorts_report`）未挂角色。
- README "142 端点" vs 实测 159 method+path / 141 path（口径）。
- README "491 / 501 passed" 历史数字已无法复现（当前两套 481 passed）。
- Muse 还有 `/api/v6/muse/video`、`/video/status`、`/video/jobs/{id}` 三个路由未写进 AGENTS。
- `tests/test_v53_agents.py::_Ctx` 缺 `seed`（应在夹具里补 `self.seed = 20261101`）。

---

## 需复核（2 条，需要非本会话的证据）

1. **89 镜 brief 是否真的会打爆短片渲染成本**：代码路径（`templates3d_hq.py:913/918/930`）确实按 brief 全量迭代，但 `outputs/templates_3d/*/shots` 已被清空，无法用产物反证。请有 GPU 的同学跑
   `python -m pipeline.templates3d_hq --template custom_tpl_v46_undercity --style-mode 2d --2d-style arcane_cinematic --reuse-shots`
   看首行日志是不是 `=== custom_tpl_v46_undercity: 89 镜 … ===`（若是 4 镜，说明还有别处的裁剪逻辑我没找到）。
2. **`outputs/templates_3d/classics/final_300s_{2d_anim,3d_anim,realistic}.mp4`（3 个 89–97MB，2026-11-06 03:21 / 05:13 / 21:20）是谁在渲、算不算新主线**：它与 V49"13–25s 短片"方向冲突，也解释了大面积 `final.mp4` 消失（疑似改名重渲）。需要 Lead/产品确认这 3 份是不是当前交付版，然后再决定 P0-2 的补片范围。
