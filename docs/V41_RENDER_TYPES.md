# V41：节目类型（2D / 3D / 写实）统一到一个模板节目

> 用户确认设计：**一个模板 = 一个节目**；模板可配置角色（cast）；节目可生成 **2D / 3D / 写实** 三种类型；自定义模板同样支持。
> 现状：3D 与写实（real_* 模板）都走 templates3d_hq；2D 渲染器（`python -m pipeline.cli render_v5`）仍可用但 UI/后端未暴露（实测 tonight_show 2D：41.6s 成片、1280×720/25fps、QC PASS、约 100 秒）。
> 目标：模板增加显式 `render_type`，创建模板时可选择 2D/3D/写实，一键成片/批量生成按类型自动分派。

## 1. 模板合同
- `contract/v5/templates/<id>.json` 与 custom 模板增加可选 `render_type`：`"2d" | "3d" | "realistic"`（缺省推导：id 以 `real_` 开头 → realistic；否则 3d）
- 新建模板 API（POST /api/v5/templates）接受 `render_type`，写入 custom 模板；校验枚举
- 模板列表/详情返回 `render_type` + 中文 label（2D 动画 / 3D 电影 / 写实实拍）

## 2. 生成分派（后端）
- pipeline run（单发/批量）按模板 `render_type` 分派：
  - `3d` / `realistic` → 现有 `templates3d_hq`（custom 用 `custom_hq_runner`）
  - `2d` → 新 2D runner：包装 `pipeline.cli render_v5 --template <tid> --out outputs/templates_2d/<tid>/final.mp4`（custom 模板需要兼容加载：可参考 `custom_hq_runner` 的 patch 方式，不修改 pipeline 代码）
- 任务阶段按类型：3D/写实沿用 prepare→generate→lipsync→assemble→poster；2D 用 render→qc→done（进度解析 PROGRESS 行）
- 产物路径统一：2D `outputs/templates_2d/<tid>/final.mp4` + poster（抽帧）；3D 维持 `outputs/templates_3d/<tid>/final.mp4`
- `cinema_templates` 列表/status/detail 兼容两种目录（有 final 即 done；poster、duration、final_url）
- 批量生成（V25）同样按类型分派，2D 项约 1–3 分钟、3D 项约 15–40 分钟

## 3. 前端
- 新建模板向导 step1：新增「**节目类型**」选择：2D 动画（快，约 1–3 分钟）/ 3D 电影（电影感，约 15–40 分钟）/ 写实实拍（真人质感，约 15–40 分钟）；提示各类型耗时与画面差异
- 模板库卡片：类型徽标（2D / 3D / 写实），筛选可加「全部/2D/3D/写实」
- 一键成片：显示当前模板类型；第④步按类型显示对应耗时提示（2D 快、3D/写实慢）；生成后成片库可播放
- 角色 cast 配置保持现有（向导 step2 选 3–5 角色 + 声音）

## 4. 验收
1. 2D：新建一个 `render_type=2d` 的模板（4 幕）→ 一键成片 → 约 1–3 分钟出成片（1280×720，含音轨/字幕风格按 V5 2D），成片库可播放
2. 3D：已有模板回归生成正常（可用复用镜头快速验证）
3. 写实：现有 real_* 模板类型显示为「写实」，生成走 3D 路径
4. 自定义模板创建时类型可选并持久化；模板库徽标正确
5. tsc 0 / build / console 无 error；截图 tmp/v41/
6. docs/V41 + commit

## 5. 分工
- **tpl-backend**：render_type 合同/接口/分派 + 2D runner（含 custom 兼容）+ 列表兼容 + 自测（真实跑一个 2D 模板）
- **tpl-frontend**：向导类型选择 + 卡片徽标/筛选 + 第④步提示 + 截图
- **Lead**：真实 2D 模板端到端（UI/API 创建→生成→播放）、3D/写实回归、截图、docs、commit


## 6. 交付结果（2026-11-04 14:12）

### 后端（20/20 自测 PASS + dispatch 修复验证）
- 模板合同 `render_type`（2d|3d|realistic；缺省 real_*→realistic，否则 3d）；POST /api/v5/templates 接受并写 custom 合同；列表/status/详情/catalog 返回 `render_type` + 中文 label（2D 动画/3D 电影/写实实拍）
- 2D 渲染路径：新增 `backend/app/routers/custom_2d_runner.py`（patch catalog 支持 custom 模板 → `pipeline.render_v5.main`），输出 `outputs/templates_2d/<tid>/final.mp4` + poster/QC/分镜/timeline
- 分派修复：`pipeline_ui.start_pipeline_job` 读取 render_type；2D → custom_2d_runner（stage render/qc/done、解析 PROGRESS）；3D/写实 → 原 hq；auto_generate / 手动 run / V25 批量全部按类型分派
- 实测（修复后）：创建+auto_generate 2D → done 64.3s；手动 run → 48.0s；批量 → 48.2s；3D/写实 dry-run 未被误分派；2D 成片 1280×720/25fps/AAC，`templates_2d` 目录、/media 200

### 前端
- 新建模板向导 step1 新增「节目类型」三选一（2D 动画≈1–3 分钟 / 3D 电影≈15–40 分钟 / 写实实拍≈15–40 分钟）+ 画面差异提示，提交带 render_type
- 模板库卡片类型徽标（2D/3D/写实）+ 筛选「全部/2D 动画/3D 电影/写实」带计数
- 一键成片：页头类型/耗时 chip、面包屑；第④步「生成 2D / 生成 3D」+ 类型说明；第⑤步成片类型标签；2D final 路径兼容
- `tsc` 0 / build 通过 / console 无 error

### 演示模板与成片（保留）
- 模板：`custom_tpl_v41_2d_demo`「2D 动画 · 城市来信」（neon_toon，cast 老周/阿栗/甜妹，4 幕）
- 成片：**13.488s / 4,189,562B / 1280×720 / 25fps / AAC**，QC PASS，poster/contact_sheet/timeline 齐全
- 看片：`/media/templates_2d/custom_tpl_v41_2d_demo/final.mp4`
- 页面级验证（Playwright）：一键成片选 2D 模板 → 第④步「重新生成」→ 页面提示「2D 成片就绪 · 镜头 4/4」；模板库筛选 2D → 卡片 2D 徽标 → 播放弹层正常播放（`tmp/v41/lead/`）

### 当前类型分布（22 个模板）
| 类型 | 数量 | 说明 |
|---|---|---|
| 2D 动画 | 1 | 自定义 demo（新能力） |
| 3D 电影 | 18 | 内置 + 2 个自定义 3D |
| 写实实拍 | 3 | real_street / real_cafe / real_kitchen（走 3D 流水线，画风写实） |

### 说明 / 遗留
- 一个模板 = 一个节目，cast 在新建模板向导 step2 配置（3–5 人，含声音），三种类型均可自定义模板
- 2D 每次 `--force` 重渲约 48–65 秒，不支持 reuse_shots；2D/3D 混合批量需分开提交（批量已按类型分派，但 API 不允许混合一次提交）
- 写实类型复用 3D hq（写实画风模板），生成耗时与 3D 一致（约 15–40 分钟/条）
- 2D 列表 `final_url` 字段可能为空，但前端已按 `templates_2d` 回退并可播放；poster_url 正常
