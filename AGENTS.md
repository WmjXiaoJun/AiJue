# AGENTS.md — AiJue 智能体工作台（V6 Agent Orchestration）

本仓库是一个 **AI 视频生产工作台**：16 个模板（风格/场景/角色/剪辑各不相同）+ 固定声音 + 分镜/转场 + H3 3D hero 镜。
本文件告诉任何编码智能体（Claude Code / Codex / DeepSeek Harness / Cursor 等）如何**用智能体方式编排、修改、重跑**整条流水线。

## 1. 智能体角色（pipeline/agents/roles.py）

| 角色 | 职责 | 可调工具 |
|---|---|---|
| producer（制片人） | 编排工作流、决定重跑范围、汇总交付 | workflow/run/report |
| story_architect（编剧） | 模板 → 分镜脚本、台词、节奏 | build_story |
| voice_director（声音导演） | 角色音色、TTS、语速/停顿 | synthesize_voices, set_voice |
| art_director（美术） | 角色/场景 AI 资产、风格一致性、姿态资产启停 | generate_art, set_pose, list_art |
| editor（剪辑师） | 剪辑档案（景别/运镜/转场/J-L）、分镜表 | plan_editing, build_storyboard, patch_editorial |
| video_director（视频导演） | 2D 动效渲染 / H3 hero 镜 | render_shots, render_h3 |
| qa（质检） | QC 硬门、抽帧、验收报告 | evaluate_qc |
| publisher（发布） | 成片/报告/批量汇总 | write_report |

## 2. 工作流（contract/v6/workflows/<template>.json）

每个模板一份**可编辑工作流**：`stages[]` 定义阶段、负责智能体、工具、依赖、是否启用。
默认阶段：story → voice → editorial → shots → assemble → storyboard → qc → publish（h3 可选）。
修改工作流 = 改 JSON（增删阶段/改依赖/改参数），然后 `python -m pipeline.agents run` 增量执行。

## 3. 命令

```bash
# 查看所有模板/工作流
python -m pipeline.agents list

# 查看某模板工作流
python -m pipeline.agents inspect --template tonight_show

# 智能体式整条重跑（可 --from/--stages 增量）
python -m pipeline.agents run --template tonight_show --duration 40 --goal "重做片头节奏"

# 只改声音（声音导演）
python -m pipeline.agents edit --template classics --op set_voice --char guanyu --value legacy_v4_laozhou
# 只改剪辑档案（剪辑师）
python -m pipeline.agents edit --template classics --op patch_editorial --json '{"pacing":{"climax":0.6}}'
# 改模板（风格/角色/场景）
python -m pipeline.agents edit --template classics --op patch_template --json '{"style":"ink_chronicle"}'
# 启用/禁用 H3 阶段
python -m pipeline.agents edit --template classics --op patch_workflow --json '{"enable":["h3"]}'
```

## 4. 规则

- **只改数据，不改代码**：模板/剪辑档案/角色音色/工作流都是 JSON 合同（`contract/`），智能体优先用 `edit` 命令修改。
- **增量重跑**：op 会返回受影响的 stages；`run --stages ...` 只跑必要阶段（TTS/镜头有缓存，避免全量重渲）。
- **验收门**：每次 run 必须通过 `qc`（16 项硬门）才算交付；失败自动保留 run 目录证据。
- **H3 成本**：每镜约 2–3 分钟 GPU；启用 h3 阶段前确认 hero 镜数量（`--hero-shots`）。
- 运行产物：`outputs/agent_runs/<run_id>/`（run.json / events.jsonl / summary.md）。

## 5. 与 ArcReel 的对应

参考 [ArcReel](https://github.com/ArcReel/ArcReel)（AI Agent 驱动的小说→角色/场景/剧本/分镜/视频工作台）：
- ArcReel：Agent 编排 + 跨镜头一致性 + 多供应商模型 + 导出剪映；
- 本项目：Agent 编排层（本文件/`pipeline/agents/`）+ 16 模板工作流 + 固定音色 + per-cut 剪辑 + H3 视频 + 分镜/QC。
