# V6 智能体编排（Agent Orchestration）

> 参考：[ArcReel](https://github.com/ArcReel/ArcReel)（AI Agent 驱动的开源视频工作台：
> 小说 → 角色/场景/道具设计 → 剧本 → 分镜图 → 视频，跨镜头角色与场景一致，多供应商模型，可导出剪映草稿）。
> 本项目把同样的「智能体编排」思路接到 AiJue 已有的 16 模板 V5 流水线上。

## 1. 为什么需要智能体层

V5 流水线很长：模板 → 分镜脚本 → 固定音色 TTS → 剪辑档案（景别/运镜/转场/J-L）→ 2D 逐镜渲染 → per-cut 合成 → 分镜表 → QC。
每次修改（换音色/调节奏/换风格/启用 H3）都要人工决定"改哪个文件、重跑哪些阶段、是否通过验收"。
V6 用 **智能体 + 可编辑工作流 + 增量重跑 + 验收门** 把这件事标准化：

- 每个模板一份 `contract/v6/workflows/<template>.json`（可被智能体编辑）；
- 8 个角色智能体（制片/编剧/声音/美术/剪辑/视频/质检/发布），各自只调用授权工具；
- 所有修改 = JSON 合同 + `edit` 操作，自动给出受影响阶段与重跑命令；
- 每次执行有 `outputs/agent_runs/<run_id>/`（run.json / events.jsonl / summary.md / 模板汇总）。

## 2. 角色与工具

| 角色 | 工具 | 说明 |
|---|---|---|
| producer | workflow/run/report | 编排、增量重跑范围、交付汇总 |
| story_architect | `build_story` | 模板 → 分镜脚本/台词/节奏（`pipeline/v5_story.py`） |
| voice_director | `synthesize_voices`, `set_voice` | 固定音色 TTS（Breeze）、音色切换 |
| art_director | `generate_art`, `set_pose`, `list_art` | 角色/场景 AI 资产、姿态资产启停（`contract/v6/art/pose_policy.json`，`characters_v5` 运行时遵守） |
| editor | `plan_editing`, `build_storyboard`, `patch_editorial` | 剪辑档案、分镜表 |
| video_director | `render_shots`, `render_h3` | 17 运镜 2D 渲染 / H3 图生视频 hero 镜 |
| qa | `evaluate_qc` | QC 16 项硬门 + 抽帧 |
| publisher | `write_report` | 成片/报告/批量汇总 |

工具全部复用现有模块，不重复实现渲染逻辑：
`v5_story`、`render_v5`（TTS/镜头/assemble/QC）、`editorial`、`storyboard_v5`、`motion2d`、`h3_v5`、`qc_v5`。

## 3. 工作流 JSON（可编辑）

```json
{
  "version": 1,
  "id": "tonight_show",
  "name": "霓虹深夜秀 工作流",
  "variables": {"template": "tonight_show", "duration": 40, "seed": 20261101},
  "stages": [
    {"id": "story", "agent": "story_architect", "tool": "build_story", "depends": [], "enabled": true},
    {"id": "voice", "agent": "voice_director", "tool": "synthesize_voices", "depends": ["story"], "enabled": true},
    {"id": "editorial", "agent": "editor", "tool": "plan_editing", "depends": ["voice"], "enabled": true},
    {"id": "shots", "agent": "video_director", "tool": "render_shots", "depends": ["editorial"], "enabled": true},
    {"id": "h3", "agent": "video_director", "tool": "render_h3", "depends": ["editorial"], "enabled": false,
     "params": {"hero_shots": 2}},
    {"id": "assemble", "agent": "editor", "tool": "assemble_episode", "depends": ["shots"], "enabled": true},
    {"id": "storyboard", "agent": "editor", "tool": "build_storyboard", "depends": ["assemble"], "enabled": true},
    {"id": "qc", "agent": "qa", "tool": "evaluate_qc", "depends": ["assemble"], "enabled": true},
    {"id": "publish", "agent": "publisher", "tool": "write_report", "depends": ["qc"], "enabled": true}
  ]
}
```

- 16 个模板已生成（`scripts/gen_agent_workflows.py`）。
- 智能体可增删阶段、改依赖、启停、改参数（HERO 镜数量/时长等）。

## 4. CLI（任何智能体都能调用）

```bash
python -m pipeline.agents list                     # 角色 + 16 模板工作流
python -m pipeline.agents inspect --template tonight_show
python -m pipeline.agents run --template history_talk --duration 40 --goal "端到端验证"
python -m pipeline.agents run --template history_talk --stages shots,assemble,qc   # 增量
python -m pipeline.agents run --template tonight_show --start editorial            # 从某阶段起
python -m pipeline.agents edit --template classics --op set_voice --char guanyu --value legacy_v4_laozhou
python -m pipeline.agents edit --template classics --op set_pose --char zhuge --pose point --json '{"enabled": false}'
python -m pipeline.agents edit --template classics --op patch_editorial --json-file tmp/patch.json
python -m pipeline.agents edit --template classics --op patch_workflow --json '{"enable":["h3"]}'
python -m pipeline.agents status --template history_talk
```

`edit` 返回 `affected_stages` + 建议的重跑命令；修改写回 `contract/`，可先 `--dry-run` 预览工作流。

## 5. 实测

- **端到端 run**（history_talk，2026-10-26 23:00:02）：`story 0.0s → voice 0.1s（缓存）→ editorial 0.1s → shots 22.6s → assemble 8.6s → storyboard 0.4s → qc 2.7s → publish 0.0s`，状态 **ok**，成片存在；证据 `outputs/agent_runs/20261026_230002_history_talk/`。
- **修改操作**（已逐项验证 + 还原）：
  - `set_voice`：zhuge `strategist_qinglang → legacy_v4_laozhou → 还原`；
  - `patch_editorial`：hard_cut_ratio `0.55 → 0.45 → 还原`；
  - `patch_workflow`：启用/禁用 h3 阶段；
  - 每次返回受影响阶段（如改音色 → voice/shots/assemble/storyboard/qc/publish）。

## 6. 与 ArcReel 的对照 / 差异

| 能力 | ArcReel | AiJue V6 |
|---|---|---|
| 智能体编排 | 小说→角色/场景/剧本/分镜/视频 全链路 Agent | 16 模板工作流 + 8 角色智能体 + 工具注册表 |
| 跨镜头一致性 | 角色/场景/道具参考图一致性 | 固定角色 AI 立绘 + 场景 plate + 16 风格后处理 |
| 视频生成 | 多供应商（Veo/Grok/Seedance/Nano Banana 等） | H3 本地图生视频（hero 镜）+ 2D 电影级动效（默认） |
| 声音 | 配音/音色 | Breeze TTS 固定音色 + 音频驱动口型（mouth_sync） |
| 剪辑 | 分镜图/导出剪映 | 每模板剪辑档案（景别/运镜/转场/J-L）+ per-cut 合成 |
| 智能体接入方式 | 内置 Agent 服务 | `AGENTS.md` + CLI + JSON 合同（任何编码智能体可驱动） |

## 7. 下一步

1. **前端智能体面板**：Agent 运行/日志/工作流编辑（复用 `/api/v5` 风格新增 `/api/v6/agent/*`）。
2. **多供应商适配器**：H3 / 其它视频模型统一 `providers/` 接口（ArcReel 模式）。
3. **参考图→视频模式**：单张角色档案图直接生成镜头（ArcReel reference-to-video）。
4. **剪映草稿导出**：分镜/时间线导出可编辑工程。
5. **审批门**：QC 失败/成本超预算时暂停，等待人工或上级智能体确认。

## 8. HTTP API（`backend/app/routers/v6_agent.py`）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v6/agent/overview` | 8 角色 + 16 模板工作流概览 |
| GET | `/api/v6/agent/workflow/{template}` | 读取工作流 JSON |
| PUT | `/api/v6/agent/workflow/{template}` | 保存（智能体编辑）工作流 |
| POST | `/api/v6/agent/run` | 启动后台 run（单并发；支持 dry_run/stages/goal） |
| GET | `/api/v6/agent/runs` | 内存 job + 磁盘 run 列表 |
| GET | `/api/v6/agent/runs/{job_id}` | job 状态/日志/结果 |
| POST | `/api/v6/agent/edit` | 修改：set_voice/set_pose/patch_editorial/patch_template/patch_workflow |
| GET | `/api/v6/agent/art` | 24 角色资产 + 姿态策略 |
| POST | `/api/v6/agent/pose` | 启停角色姿态资产 |
| GET | `/media/agent_runs/{run_id}/{file}` | run 证据文件（summary.md/run.json/events.jsonl） |

## 9. 前端「智能体」面板

`frontend/src/components/v5/AgentPanel.tsx`（V5 世界 → 智能体 tab，`#/v5/agent`）：

- **模板工作流**：下拉选模板、阶段表（agent/tool/desc/params、启停开关）、保存/重载；
- **运行智能体**：目标/时长/阶段过滤、试运行(dry-run)/开始运行、实时日志、阶段结果 chip、run 证据链接；
- **智能体修改**：5 种 op（改声音/姿态/剪辑/模板/工作流）+ JSON 编辑 + `affected_stages` + 一键填入受影响阶段；
- **美术资产**：24 角色缩略图 + point/think 姿态启停；
- **运行记录**：内存 job + 磁盘 run（summary.md 链接）。

截图/断言（8090 正式后端）：`tmp/v6_frontend/screenshots/v6_agent_panel.png`、
`tmp/v6_frontend/agent_ui_check.json`（**13/13 PASS**：面板/角色 chips/10 阶段/5 操作/24 角色/运行记录/dry-run job/edit API/art API）。
