# V49 交付报告 · 短片线（季）+ 智能体编排上线

> 决策：**B 短片线**（用户 2026-11-05）｜附加要求：智能体编排要真正用起来
> 计划：`docs/PLAN_V49_SHORTS_SEASON.md` ｜ 基线：`v48.1` @ `2189202`
> 结论：**M1–M5 全部落地**；M6（1080P 交付）按计划留 backlog。

---

## 0. 一页结果

| 里程碑 | 状态 | 硬证据 |
|---|---|---|
| **M1 验收门归零** | ✅ | `pytest tests -q` → **491 passed / 0 failed / 36 skipped**；`v5_cli validate --strict` → `ok:true, errors:[], warnings:[]` |
| **M2 智能体编排上线** | ✅ | 页面 `#/agent`（三栏 + 季面板，0 console error）；`/api/v6/agent/overview` 覆盖 **33/33 模板**；33 份可编辑工作流；短片栈 5 阶段 |
| **M3 季（Season）** | ✅ | 合同加 `season{}`；`/series`、`/season/{sid}/run`、`/season/{sid}/status`；dry-run 实测 3 集 `planned`，证据落盘 |
| **M4 合集导出升级** | ✅ | 片头卡 + 分集卡 + 串片 52.8s/1280×720/sha1；竖版 1080×1920；二次调用 `cached:true` |
| **M5 文档与契约打扫** | ✅ | README 补 V7–V49；AGENTS.md 重写为 V49；`/api/v5/outputs` 回填 20/21 `final_url`；progress.json 19 → 30 |
| **M6 1080P 交付** | ⏳ backlog | 成片仍 1280×720（见 §6） |

---

## 1. M1 · 验收门归零（5 红 → 0）

| 原红项 | 修法 | 落点 |
|---|---|---|
| `relic_talk` / `relic_talk_3d` 缺 `packaging`（2 个用例） | 补 `packaging`（字幕/转场/片头） | `contract/v5/templates/relic_talk*.json` |
| `custom_lin` 陈旧断言 | 换成真实存在的 `custom_old_inventor`，保留“自定义示例可加载 + 单项校验通过”语义 | `tests/test_v5_catalog.py` |
| V47 新角色缺 `front/cutout/face_norm`；`caocao` meta 缺 `face_norm` | 用仓库同源算法补资产（`three_view/front.png` → `front.png`；`remove_white` → `cutout.png`；`_norm_geom(face_geometry(cutout))` → `face_norm`），**未回滚任何图片** | `scripts/repair_v5_char_art_v49.py` + 3 个 `meta.json` |
| V5.2 基线陈旧（`count != 16`、旧 `/media/v5/*` 404） | 改为按事实断言：`count >= 21`、`final_url` 非空必须 200/206、为空必须 `legacy:true`；媒体探测改 `/media/templates_3d/tonight_show/final.mp4` | `tests/test_v5_2_acceptance.py` |

复现：`python -m pytest tests -q`；`python -m pipeline.v5_cli validate --strict`。

---

## 2. M2 · 智能体编排上线

### 2.1 两条生产栈（V49 关键设计）

| 栈 | 模板 | 阶段 | 入口 |
|---|---|---|---|
| `v5_render` | 16 内建综艺 + `custom_holo_talk` | story → voice → art? → editorial → shots → h3? → assemble → storyboard → qc → publish | `pipeline/render_v5.py` |
| `hq_shorts` | 全部 `custom_tpl_*`（12 个） | story → art? → **shots(`render_2d_hq`)** → qc → publish | `pipeline/templates3d_hq.py` |

判定：`pipeline/agents/workflow.py::is_shorts()`（有 `render_style` 或 id 以 `custom_tpl_` 开头）；栈写进工作流 `stack` 字段。

### 2.2 新增工具（`pipeline/agents/registry.py`）

| 工具 | 智能体 | 作用 |
|---|---|---|
| `check_story` | story_architect | 短片分幕/台词/角色/时长 + 语义 brief 核对，写 `episode.json`，缺 brief 直接报错 |
| `render_2d_hq` | video_director | 调 `templates3d_hq`（关键帧 + H3 + 合成），日志回流 run 面板，custom 模板自动 patch `load_template`，默认 `--reuse-shots` |
| `evaluate_hq_qc` | qa | 成片存在 / 时长 ≥ 0.6×target / 海报 / 音轨 / ≥1280 宽，写 `qc_shorts.json` |
| `write_shorts_report` | publisher | 交付摘要 + `summary.md` |

### 2.3 接口与页面

| 项 | 值 |
|---|---|
| 概览 | `GET /api/v6/agent/overview` → 33 模板（`kind` 内建 21 / 自定义 12；`render_type` 2d 9 / 3d 21 / realistic 3；`stack`、`ready`、`series`、阶段全量） |
| 工作流 | `GET/PUT /api/v6/agent/workflow/{t}`（33 份合同 v2，带 `stack`） |
| 运行 | `POST /run`（`stages` / `dry_run` / `goal`）+ `GET /runs/{job}`（日志 + 证据三件套） |
| 编辑 | `POST /edit`（`set_voice / patch_editorial / patch_template / patch_workflow / reset_workflow`） |
| 页面 | `#/agent`（单集工作流）、`#/agent/season`（季生产）；导航第 3 项「智能体」 |
| 前端文件 | `components/agent/AgentPage.tsx`(1459)、`lib/agent.ts`(556)、`styles/agent.css`(536)；`App.tsx`/`AppShell.tsx`/`icons.tsx` 小改 |
| 自测 | 1600 / 1024 / 860 三档布局；0 console error；0 个 ≥400 响应；截图 `tmp/ui/v49/*.png` |

**CLI 与页面等价**：`python -m pipeline.agents list|inspect|run|edit`（`list` 已包含 12 个 custom 模板并显示 `stack`）。

---

## 3. M3 · 季（Season）

- 合同（可选字段，向后兼容）：`contract/v5/series/<sid>.json` 增补 `season: {order, target_count, style_tag, rules{episode_sec, acts, same_cast, same_style}}`。
- 接口：`GET /api/v6/agent/series`（每集 `ready / final_url / poster_url / last_run / stages / stack`）、`POST /api/v6/agent/season/{sid}/run`（`dry_run` / `only_missing`）、`GET /api/v6/agent/season/{sid}/status`。
- 季生产：单并发、按集顺序、逐集写 `outputs/agent_runs/season_<sid>_<ts>/<run_id>/`；失败集不阻塞后续集；`latest_runs()` 改为 `rglob` 以发现嵌套 run。
- 实测（dry-run）：3 集全部 `planned`，日志按集分段，状态回填 `season/status`。

---

## 4. M4 · 合集导出升级

`POST /api/v5/series/{sid}/export?force=true[&vertical=true]`

| 项 | 实测 |
|---|---|
| 横版 | **52.8s** / 1280×720 / 15.6MB / `sha1=6da529dfdaea…` / build 2.9s |
| 结构 | `title(2.2s) → [ep_card(1.2s) → episode] × 3` |
| 竖版 | **52.8s / 1080×1920 / 23.7MB**（`crop=ih*9/16` + lanczos） |
| manifest | `variant / resolution / sha1 / ffmpeg / season / episodes[order、sha1、duration] / segments[]` |
| 缓存 | 同参数二次调用 `cached:true` 且 sha1 一致 |
| 产物 | `series.mp4 / series_9x16.mp4 / manifest.json / manifest_vertical.json / ep_card_0{1,2,3}.{jpg,mp4} / title_card.mp4 / concat*.txt` |

---

## 5. M5 · 文档与契约打扫

- `README.md`：追加「V7–V49 概览」（15 页面 / 142 API / 33 模板 / 智能体页 / 季 / 产物清单 / 测试规模）。
- `AGENTS.md`：重写为 V49（两条栈、13 个工具、季合同、页面入口、验收门）。
- `/api/v5/outputs`：`final_url` 回退到 `templates_2d/3d`（20/21 有片，`relic_talk` 如实标 `legacy:true`）。
- `outputs/templates_3d/progress.json`：按磁盘重建，19 → **30**（`no_final: []`）。
- 新增脚本：`scripts/seed_v6_workflows.py`、`scripts/rebuild_templates3d_progress.py`、`scripts/repair_v5_char_art_v49.py`。

---

## 6. 未做 / 已知限制

| 项 | 说明 |
|---|---|
| M6 1080P | 成片仍 1280×720；合集竖版已 1080×1920，横版 1080p 需加 `--deliver-1080`（backlog） |
| 季真跑 | 本轮只验 dry-run 与单集 `story` 真跑；整季真渲 3 集 ≈ 25 分钟 GPU，需人工触发 |
| `render_2d_hq` 全链路 | argv 有单测、`story` 阶段真跑通过；`shots` 真渲未在本轮执行（会重写现有成片） |
| 页面 backlog | `/art` 姿态面板、runs 历史列表、阶段增删（当前可改 enabled/params/顺序） |
| 鉴权 | 142 端点仍无鉴权（本地 demo 前提） |

---

## 7. 复现清单

```powershell
# 1) 验收
python -m pytest tests -q
python -m pipeline.v5_cli validate --strict

# 2) 33 份工作流 + 列表
python scripts/seed_v6_workflows.py
python -m pipeline.agents list

# 3) 短片单集：只跑 story（便宜）
python -m pipeline.agents run --template custom_tpl_v46_duologue --stages story

# 4) 页面
#    http://127.0.0.1:8090/#/agent 与 /#/agent/season

# 5) 季计划（dry-run）
Invoke-WebRequest -Method Post -ContentType 'application/json' `
  -Body '{"dry_run":true,"only_missing":false}' `
  http://127.0.0.1:8090/api/v6/agent/season/undercity_arc/run -UseBasicParsing

# 6) 合集导出（横版 / 竖版）
Invoke-WebRequest -Method Post http://127.0.0.1:8090/api/v5/series/undercity_arc/export?force=true
Invoke-WebRequest -Method Post "http://127.0.0.1:8090/api/v5/series/undercity_arc/export?force=true&vertical=true"
```

---

## 8. 下一步（backlog 顺序）

1. **季真跑一次**（3 集，only_missing=false，dry_run=false）→ 出季合集，验证「一季 = 一条生产命令」。
2. **`render_2d_hq` 真渲一集**（GPU）：验证关键帧抽卡 + 复用已有镜头 + 新 QC 门。
3. **M6 1080P**：合集与单集成片各验证一次。
4. **新季**：同世界 3 集（沿用 `season.rules`），产第二季合同。
5. 页面补 `/art` 面板与 runs 历史。
