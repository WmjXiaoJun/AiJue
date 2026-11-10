# AGENTS.md — AiJue 智能体工作台（V49 · 短片线 + 季）

本仓库是一个 **AI 视频生产工作台**：**33 个模板**（21 个 V5 综艺/实拍风 + 12 个 custom 短片/示例，见 `contract/v5/templates` 与 `contract/v5/custom/templates`）+ 固定声音 + 分镜/转场 + 2D 关键帧与 H3 视频。
本文件告诉任何编码智能体（Claude Code / Codex / DeepSeek Harness / Cursor 等）如何**用智能体方式编排、修改、重跑**整条流水线。

> **工程事实（2026-11 第三轮整改后核对）**：模板 21+12=33 · 工作流 33 份 · 工具 18 个（`pipeline/agents/registry.py::TOOLS`）· 角色 26 个（`assets/v5/characters`）· 大厅节目形态 17 种（`contract/formats`）· 用例 613 条（`pytest.ini` 限定 `tests` + `pipeline/tests`；2026-11-08 实测 574 passed / 39 skipped / 0 failed，113.3s）。
> 场景底板与角色 cutout 目前是**程序化代用资产**（`scripts/gen_scene_plates_procedural.py` / `scripts/make_cutout_layout.py`，`outputs/scene_plates/procedural.json` 有 `procedural:true` 标记），可用 SDXL 脚本覆盖为真绘景。
> 整改记录：`docs/audit/R4_FIX_ACCEPTANCE.md`；缺陷清单：`docs/audit/REVIEW_ROUND3.md`。

> **V49 主线（用户决策）**：短片线——一个世界 = 一季，一季 = N 集（默认 3 集、13–25s/集），每集由智能体工作流一次生产，一季由「季生产」串成合集。
> 计划与验收：`docs/PLAN_V49_SHORTS_SEASON.md`。

## 0. 两条生产栈（选错栈 = 白跑）

| 栈 | 适用模板 | 阶段 | 渲染入口 |
|---|---|---|---|
| **V5 渲染栈** | 21 个 V5 模板（`contract/v5/templates/*.json`，含 `custom_holo_talk` 等示例） | story → voice → art? → editorial → shots → h3? → assemble → storyboard → qc → publish | `pipeline/render_v5.py`（scene_render + motion2d） |
| **HQ 短片栈** | 全部 `custom_tpl_*`（V22/V23 的 3D、V44+ 的 2D 子风格） | story → art? → **shots(`render_2d_hq`)** → qc → publish | `pipeline/templates3d_hq.py`（关键帧 + H3 + 合成） |

栈由 `pipeline/agents/workflow.py::is_shorts()` 判定（模板带 `render_style` 或 id 以 `custom_tpl_` 开头 → HQ 栈），写进工作流合同的 `stack` 字段。

## 1. 智能体角色（pipeline/agents/roles.py）

| 角色 | 职责 | 可调工具 |
|---|---|---|
| producer（制片人） | 编排工作流、决定重跑范围、汇总交付 | workflow/run/report |
| story_architect（编剧） | 模板 → 分镜脚本、台词、节奏；短片合同核对 | build_story / **check_story** |
| voice_director（声音导演） | 角色音色、TTS、语速/停顿 | synthesize_voices, set_voice |
| art_director（美术） | 角色/场景 AI 资产、风格一致性、姿态资产启停 | generate_art, set_pose, list_art |
| editor（剪辑师） | 剪辑档案（景别/运镜/转场/J-L）、分镜表 | plan_editing, build_storyboard, patch_editorial |
| video_director（视频导演） | 2D 动效渲染 / H3 hero 镜 / 短片 HQ 渲染 / 口型精修 | render_shots, render_h3, **render_2d_hq**, **muse_lipsync** |
| qa（质检） | QC 硬门、抽帧、验收报告 | evaluate_qc / **evaluate_hq_qc** |
| publisher（发布） | 成片/报告/批量汇总 | write_report / **write_shorts_report** |

## 2. 工作流（contract/v6/workflows/<template>.json）

每个模板一份**可编辑工作流**（33 份，v2 格式带 `stack`）：`stages[]` 定义阶段、负责智能体、工具、依赖、是否启用、参数。
改工作流 = 改 JSON（增删阶段/改依赖/改参数），或调 `PUT /api/v6/agent/workflow/{template}`。

## 3. 命令（CLI 与页面等价）

```bash
# 补齐/重建 33 份工作流合同（只补缺失；--force 全量覆盖）
python scripts/seed_v6_workflows.py

# 查看所有模板/工作流/栈
python -m pipeline.agents list

# 查看某模板工作流
python -m pipeline.agents inspect --template tonight_show

# 智能体式整条重跑（base 模板）
python -m pipeline.agents run --template tonight_show --duration 40 --goal "重做片头节奏"

# 短片栈：只跑 story（便宜，验证合同/brief）
python -m pipeline.agents run --template custom_tpl_v46_duologue --stages story

# 短片栈：真渲一集（GPU ~6–10 分钟；shots 阶段 = templates3d_hq）
python -m pipeline.agents run --template custom_tpl_v46_duologue --stages shots,qc,publish

# 短片栈：Muse 口型（MuseTalk 1.5，GPU 串行 ~20s/3s 镜头）
python -m pipeline.agents run --template custom_tpl_v46_duologue --stages muse

# 智能体式修改
python -m pipeline.agents edit --template classics --op set_voice --char guanyu --value legacy_v4_laozhou
python -m pipeline.agents edit --template classics --op patch_editorial --json '{"pacing":{"climax":0.6}}'
python -m pipeline.agents edit --template classics --op patch_template --json '{"style":"ink_chronicle"}'
python -m pipeline.agents edit --template classics --op patch_workflow --json '{"enable":["h3"]}'
```

**页面入口**：`http://127.0.0.1:8090/#/agent`（模板列表 / 工作流编辑 / 单集运行 / 季面板）。

## 4. 季（Season）：一季 = 一个系列合同

- 合同：`contract/v5/series/<sid>.json`（`members[]` 有序；`cover` 取封面模板海报）。
- API：`GET /api/v6/agent/series`、`POST /api/v6/agent/season/{sid}/run`（`dry_run` / `only_missing`）、`GET /api/v6/agent/season/{sid}/status`。
- 证据：季生产会建 `outputs/agent_runs/season_<sid>_<ts>/<run_id>/`，每集一套 `run.json / events.jsonl / summary.md`。
- 合集：`POST /api/v5/series/{sid}/export?force=true` → `outputs/series/<sid>/{series.mp4,manifest.json,title_card.mp4,concat.txt}`。

## 5. Muse（MuseTalk 1.5）口型生成

- 环境：代码 `local_models/lipsync/MuseTalk`、权重 `local_models/lipsync_models/`、venv `local_models/lipsync-venv`（torch cu128）。
- API：`GET /api/v6/muse/status`、`POST /api/v6/muse/sync`（`{template, limit}` 或 `{clip, audio}`）、`GET /api/v6/muse/jobs/{id}`；产物 `outputs/muse/<tid>/<shot>_muse.mp4`。
- 阶段：工作流 `muse`（默认关）。`params.apply=true` → 回写 `shots/<sid>.mp4`（原件 `shots/_orig/`）并重新 `assemble`。
- 参数覆盖：`--stage-params '{"muse":{"apply":true}}'`（API 同名字段 `stage_params`），只对本次 run 生效。
- 季批量：`POST /api/v6/agent/season/{sid}/run {with_muse:true, muse_apply:true, dry_run:false}`（`dry_run` 默认 true）。
- 并发：跨进程锁 `tmp/v5_lipsync/.muse.lock`（死主按 PID 回收）；`max_frames=600`。
- 文档：`docs/V50_MUSE_GENERATION.md`、`docs/V51_MUSE_INTO_FILM.md`、`docs/MUSE_VIDEO_INTEGRATION.md`。

## 6. 规则

- **只改数据，不改代码**：模板/剪辑档案/角色音色/工作流都是 JSON 合同（`contract/`），智能体优先用 `edit` 命令修改。
- **增量重跑**：op 会返回受影响的 stages；`run --stages ...` **只跑列出的阶段**（依赖只用于排序，不再自动补齐；要旧行为加 `--with-deps`）。TTS/镜头有缓存，避免全量重渲。短片栈的 `render_2d_hq` 默认 `--reuse-shots`（已有镜头直接复用），`force` 时才重渲。
- **验收门**：base 模板过 `qc`；短片过 `evaluate_hq_qc`（成片存在/时长上下界 `0.6×target ≤ d ≤ max(1.5×target, target+2)`/海报/音轨/分辨率），并双写 `qc_report.json` + `qc_shorts.json`。失败自动保留 run 目录证据。
- **H3 成本**：短片单集约 6–10 分钟 GPU；季生产前确认集数与是否已有成片（`only_missing`）。
- 运行产物：`outputs/agent_runs/<run_id>/`（run.json / events.jsonl / summary.md / episode.json）。

## 7. 与 ArcReel 的对应

参考 [ArcReel](https://github.com/ArcReel/ArcReel)（AI Agent 驱动的小说→角色/场景/剧本/分镜/视频工作台）：
- ArcReel：Agent 编排 + 跨镜头一致性 + 多供应商模型 + 导出剪映；
- 本项目：Agent 编排层（本文件/`pipeline/agents/`）+ 33 模板工作流（两条栈）+ 季生产 + 固定音色 + per-cut 剪辑 + H3 视频 + 分镜/QC。
