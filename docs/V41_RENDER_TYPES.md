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
