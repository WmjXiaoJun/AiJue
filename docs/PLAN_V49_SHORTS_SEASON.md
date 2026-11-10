# PLAN V49 · 短片线（季播短剧）+ 智能体编排上线

> 决策：**B 短片线**（2026-11-05，用户确认）｜ 附加要求：**智能体编排要真正用起来**（不是删掉）
> 基线：`master` @ `2189202`（v48.1）｜ 本文档即 V49 的验收合同，做完一项勾一项。
> **执行状态（2026-11-05）**：M1 ✅ · M2 ✅ · M3 ✅ · M4 ✅ · M5 ✅ · M6 ⏳ — 交付证据见 [docs/V49_SHORTS_SEASON.md](V49_SHORTS_SEASON.md)。

---

## 0. 主线定义（一句话）

**一个世界 = 一季；一季 = N 集短片（默认 3 集、13–25s/集、同一 cast/场景/风格）；每集由智能体工作流一次性生产；一季由「季生产」串成合集。**

和 V48 的区别：V48 是「手工点 3 个模板 → 导出合集」；V49 是「**季是一等公民**」——季有合同、有生产队列、有验收、有可复现的合集导出。

---

## 1. 目标与非目标

| | 内容 |
|---|---|
| **做** | ① 验收门归零；② 智能体页面上线并覆盖全部 33 模板（含 12 个 custom 短片）；③ 季合同 + 季生产 API；④ 合集导出升级（片头卡 + 分集卡 + manifest 指纹 + 竖版）；⑤ 文档/README 对齐 |
| **不做**（本轮） | 新增 2D 风格；重做 H3 提示词；多租户/鉴权；1080P 全量重渲（见 M6，可延后） |
| **不做**（永久） | 回到「5–10 分钟长片」主线；长片能力保留（`relic_talk`/`relic_talk_3d`）但不再扩产 |

---

## 2. 里程碑

### M1 · 验收门归零（0.5 天）

| 项 | 验收 |
|---|---|
| `pytest tests -q` | **0 failed**（当前 5 failed / 486 passed / 36 skipped） |
| 5 条红线 | ①`relic_talk`/`relic_talk_3d` 补 `packaging`；②`custom_lin` 陈旧断言改为校验真实存在的 custom 示例；③老秦/小满补 `front/cutout/face_norm`；④`caocao` meta 补 `face_norm`；⑤V5.2 基线 16→33、`/media/v5/*` → `/media/templates_3d/*` |
| 契约 | `python -m pipeline.v5_cli validate` → `ok:true`、`warnings:[]` |

### M2 · 智能体编排上线（1.5 天）

| 能力 | 验收 |
|---|---|
| 页面 `#/agent` | 导航第 7 项「智能体」；0 console error；窄屏不塌 |
| 覆盖 | 概览列出 **33 个模板**（16 内建 + 17 custom/series 相关），每个带单位 2D/3D/写实标记与"是否已有成片" |
| 工作流 | 可看/可改/可保存（enable、params、stages 顺序），保存落到 `contract/v6/workflows/<id>.json` |
| 运行 | 单集 run：选阶段 + dry-run + goal → 日志流 + 证据（`summary.md/run.json/events.jsonl`）可打开 |
| 短片驱动 | 新增工具 `render_2d_hq`（调 `templates3d_hq`，2D 风格关键帧 + H3 + 合成），custom 2D 模板的默认工作流走它 |
| 编辑 | `set_voice / patch_editorial / patch_template / patch_workflow` 四个 op 在页面上可用 |

### M3 · 季（Season）成为一等公民（1 天）

| 能力 | 验收 |
|---|---|
| 合同 | `contract/v5/series/<sid>.json` 增补 `season:{order,episodes[],target_count,style_tag}`（向后兼容，旧字段保留） |
| 接口 | `GET /api/v6/agent/series`（季 + 每集 ready/last_run）；`POST /api/v6/agent/season/{sid}/run`（`dry_run` / `only_missing`）；`GET /api/v6/agent/season/{sid}/status` |
| 季生产 | 未出片的集按顺序排队跑；单并发；日志按集分段；证据落 `outputs/agent_runs/season_<sid>_<ts>/` |
| 前端 | 成片库系列条 + 智能体页季面板：集状态（已出片/待生产/生产中）、一键季计划（dry-run）、一键季生产 |

### M4 · 合集导出升级（0.5 天）

| 项 | 验收 |
|---|---|
| 片头卡 | 2.2s（已有）→ 保留 |
| 分集卡 | 每集前插 1.2s 序号卡（第 N 部 · 标题） |
| manifest | 增补 `episodes[].order/sha1/duration`、`season`、`built_at`、`ffmpeg` |
| 竖版 | `?vertical=true` 出 9:16 合集（1080×1920，中心裁切） |
| 复现 | 同参数第二次返回 `cached:true`；`manifest.sha1` 与产物一致 |

### M5 · 文档与契约打扫（0.5 天）

- README 补 V7→V49 概览（现在停在 V6）；AGENTS.md 与真实入口一致（智能体页面存在、导航路径、CLI 与页面等价）；
- `/api/v5/outputs` 的 21 条空 `final_url` → 指向 `outputs/templates_3d`（或标记 `legacy:true`）；
- `outputs/templates_v3/progress.json` 重建；
- 新增 `docs/V49_*.md` 交付报告。

### M6 · 1080P 交付（可延后，0.5 天）

- `render_v5` / `templates3d_hq` 增 `--deliver-1080`：1280×720 源 → 1920×1080 lanczos 上采样 + 码率 8M；
- 只在合集导出与单集成片各验证 1 次，不做全量重渲。

---

## 3. 每集/每季验收口径（硬门）

1. `contract` validate：0 error / 0 warning；
2. `pytest tests -q`：0 failed；
3. 每集：`outputs/templates_2d/<id>/final.mp4` 存在 + `qc_report.json.ok == true` + `poster.jpg`；
4. 每季：`outputs/series/<sid>/series.mp4` + `manifest.json`（集序、时长、sha1 对齐）；
5. 页面：`#/agent` 与 `#/library/final` 打开 0 console error / 0 404；
6. 智能体：每次 run 有 `run.json/events.jsonl/summary.md` 三件证据。

---

## 4. 季的排产规则（内容侧）

| 项 | 规则 |
|---|---|
| 一季集数 | 3 集起，最多 6 集（一季总时长 45–120s） |
| 每集时长 | 13–25s（4–6 幕） |
| 每集结构 | b-roll 开场 → 2–4 段对白 → b-roll 收尾 |
| 同季约束 | 同 cast、同 2D 子风格、同世界（场景组） |
| 新季门槛 | 上一季 3/3 出片 + 合集导出通过，才开新季 |

---

## 5. 风险与对策

| 风险 | 对策 |
|---|---|
| 2D 短片单集渲染 ~470s，季 3 集 >20 分钟 | 季生产必须后台队列 + 断点续跑（`--reuse-shots`）；UI 只给进度，不阻塞 |
| 智能体工具与 V5 渲染栈耦合（`render_shots` 走 render_v5） | 短片走新工具 `render_2d_hq`，两栈并存但入口分开：base 模板 = V5 栈，custom 2D = HQ 栈 |
| 季合同改动破坏 V48 前端 | 新字段全可选，旧 `members[]` 保留；`/api/v5/series` 输出不变 |
| 生成失败导致季卡住 | 季 run 记录每集状态，失败集跳过并继续；重跑只跑 `only_missing` |

---

## 6. 交付顺序（本轮就做）

```
M1 验收归零 ──► M2 智能体上线（后端工具 + 概览覆盖 + 前端页面）
                     │
                     ▼
              M3 季合同 + 季生产 ──► M4 合集升级 ──► M5 文档
```

**完成标记**：M1/M2/M3 本轮必须绿；M4 视时间；M5 本轮做；M6 记入 backlog。
