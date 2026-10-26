# V5.4 计划：电影级剪辑 / 镜头语言 / 分镜 / 转场（每模板不同）

> 用户诉求："当然还要有不同的剪辑 不同的电影级的镜头 分镜 跳转 你都看看 参考个个库和开源的代码 全部集成好"
> 现状：V5 每模板已不同 **风格/场景/角色/声音**；但剪辑层较弱：
> - 镜头运动仅 7 种（static/push_in/pull_out/pan_l/r/dolly/handheld），每镜随机/固定；
> - 转场每模板只有 1 种（style_v5.transition 单值，全片同一 xfade）；
> - 没有**分镜表**（shot size / 镜头语言 / 剪辑节奏 / J-L cut / insert / 蒙太奇）；
> - 前端模板库只展示 7 步工作流，缺分镜/剪辑可视化。

## 1. 开源参考（调研结论）

| 来源 | 借鉴点 |
|---|---|
| [libopenshot / OpenShot](https://www.openshot.org/zh-hans/libopenshot/) | 专业剪辑库的 **Clip + Transition + Keyframe** 数据模型：每段带 in/out、每转场带类型/时长/参数；我们借鉴其"每个剪辑点独立转场"的做法（而不是全片一种转场） |
| [open-montage](https://pypi.org/project/open-montage/) | 蒙太奇/剪辑序列 → 时间线 EDL；借鉴其"镜头序列 + 节奏"表达 |
| [tellers-timeline](https://pypi.org/project/tellers-timeline/) | 时间线/轨道模型（video/audio/subtitle 分层），用于我们的 timeline.json 扩展 |
| [OpenTimelineIO](https://opentimeline.io/) | 行业时间线交换格式（OTIO）：输出 EDL 可被 Resolve/Blender 读取（我们导出简化 OTIO JSON） |
| [parallax-maker](https://github.com/provos/parallax-maker) | 2.5D 视差/景深分层：镜头运动带 depth 层次（我们已有 depth，升级运动曲线） |
| [cinematic-shotlist-toolkit](https://pypi.org/project/cinematic-shotlist-toolkit/0.1.0/) | 镜头清单生成（subject/action/setting/camera/style/lighting/pacing/frame）：借鉴其 **shot size + camera + pacing** 字段体系做分镜数据 |
| ffmpeg xfade | 转场执行层：per-boundary `xfade`（fade/dissolve/wipe/slide/circle/radial/hblur/vblur/pixelize/zoom…） |

结论：V5.4 不改渲染引擎大框架，新增 **剪辑层（editorial）**：每个模板有独立"剪辑/镜头语言/分镜/转场"档案，渲染时按档案生成 EDL 并执行。

## 2. 数据：每模板剪辑档案 `contract/v5/editorial/<template_id>.json`

```json
{
  "id": "history_talk",
  "label": "水墨史书·沉静叙事",
  "pacing": {"lead_in": 1.35, "mid": 1.0, "climax": 0.85, "coda": 1.25},
  "opening": {"shot": "EWS", "camera": "crane_down", "hold": 2.2},
  "closing": {"shot": "MS", "camera": "pull_out", "hold": 2.0},
  "shot_sequence": ["EWS", "WS", "MS", "OTS", "CU", "DETAIL", "MS", "WS", "MCU"],
  "camera_palette": ["crane_down", "dolly_in", "pan_right", "rack_focus", "push_in", "pull_out"],
  "transition_palette": ["cut", "cut", "dissolve", "whip_left", "fade"],
  "transition_policy": {"default": "cut", "hard_cut_ratio": 0.5,
                        "dissolve_on_scene_change": true, "max_transition_sec": 0.6},
  "j_cut_ratio": 0.3, "l_cut_ratio": 0.2,
  "insert_ratio": 0.2, "detail_ratio": 0.15,
  "rhythm": {"fast_shots_max": 2.2, "slow_shots_min": 3.2},
  "notes": "历史叙事：慢推/升降 + 长镜头，转场以硬切为主、场景切换用溶接"
}
```

16 个模板各一份，**互不相同**（不同 shot_sequence/镜头调色板/转场调色板/节奏比例）。

## 3. 剪辑引擎 `pipeline/editorial.py`

- `build_edit_plan(template, shots, style, *, seed)` → 每镜：
  - `shot_size`（EWS/WS/MS/MCU/CU/ECU/OTS/DETAIL/INSERT）
  - `camera`：type/strength/ease/shake（从 camera_palette 按 template 节奏与 shot_size 映射）
  - `focus`：`camera_zone` 名称（宽景 wide / 中景 medium / 特写/细节 detail/close；并保证角色头安全）
  - `transition_out`：{mode: cut|dissolve|fade|wipeleft|wiperight|wipeup|wipedown|hblur|vblur|circleopen|radial|pixelize, duration}
  - `j_cut`/`l_cut`：音频重叠秒数
  - `speed`：保留字段（V5.4.1 再做速度坡道）
  - `insert`/`detail`：用 scene 局部 crop（2.5D 细节镜）标记
- 约束（验收）：每模板 ≥6 种 shot_size（含 DETAIL/INSERT）、≥4 种 camera、≥3 种 transition、无相邻同 size、开/结尾符合档案、硬切比例符合。
- 输出 `plan["edit"]`（EDL）+ 每镜 `shot["edit"]`；timeline.json 增加 `edit` 轨（OpenTimelineIO 风格简化结构）。
- `storyboard plan` → `storyboard.json` + `storyboard.html` + `storyboard.png`（每镜缩略图+镜头信息）。

## 4. 动画/剪辑执行

- `motion2d`（motion-dev）：新增 camera：`truck_left/right`（横移）、`tilt_up/down`、`crane_up/down`（升降+微推）、`dolly_zoom`（希区柯克变焦）、`rack_focus`（前后景虚实变化）、`orbit`（2.5D 环绕）、`whip_pan`（快速甩镜，带运动模糊）；easing 增加 `in_out_sine/expo/back`；保持现有接口。
- `pipeline/editing.py`（render-dev）：
  - `xfade_chain(shots, clips, boundaries)`：每个剪辑点独立 xfade（cut 段直接拼接），支持 hblur/vblur/circleopen/radial/pixelize/zoomin 等；
  - `mix_audio_edit(shots, workdir, total, cuts)`：J/L cut（下镜音频提前/上镜音频延后）用 adelay+amix 实现；
  - `whip_pan_transition(clipA, clipB, ...)`：必要时用两镜首尾帧生成甩镜过渡（ffmpeg gblur+scroll）。
- `render_v5`：`render_shot_clips` 使用 editorial 的 camera/zone；`assemble` 用 `editing.xfade_chain` + `mix_audio_edit`；输出 storyboard 文件；timeline 记录 edit 轨。

## 5. 前端（stage-dev）

模板库详情新增「**分镜 / 剪辑**」视图：
- 分镜表：每镜卡片（序号/时长/shot size/镜头运动/转场到下一镜/台词/缩略图），按 acts 分组；
- 剪辑时间线：横向条（每个 shot 宽度=时长、颜色=shot size、连接处标转场类型/J-L cut）；
- 剪辑档案：pacing 曲线、camera/transition 调色板、j/l cut 比例、opening/closing（来自 `/api/v5/editorial/{template}` 或 catalog 附带）；
- 模板卡增加"剪辑签名"（如 `8 镜 · 6 种景别 · 5 种运镜 · 4 种转场`）。
后端：`GET /api/v5/editorial/{template}`（读 contract/v5/editorial/）、`GET /api/v5/storyboard/{template}`（读 outputs storyboard.json）/或直接从模板+成片 timeline 计算；`/media/v5/<t>/storyboard.png`。

## 6. 阶段

| 阶段 | 内容 | 验收 |
|---|---|---|
| V5.4.0 | editorial 数据模型 + 16 份档案 + editorial.py + 1 模板 pilot（history_talk） | plan 多样性约束全过；pilot 成片抽帧可见多种镜头/转场 |
| V5.4.1 | motion2d 新 camera + editing.py per-boundary 转场/J-L cut | 单测 + pilot 成片 QC PASS |
| V5.4.2 | render_v5 全链路接入 + storyboard 产物 | timeline 含 edit 轨；storyboard.png/html 生成 |
| V5.4.3 | 前端分镜/剪辑视图 + 截图 | build 通过 + 截图证据 |
| V5.4.4 | 16 部重渲 + QA（剪辑多样性硬门） | 16/16 QC + 剪辑唯一性矩阵 + P1 全过 |

## 7. 分工

| Owner | 文件 |
|---|---|
| template-author | `contract/v5/editorial/*.json`（16 份剪辑档案，互不相同） |
| motion-dev | `pipeline/motion2d.py`（新 camera 类型 + easing，接口兼容） |
| render-dev | `pipeline/editing.py`（xfade_chain/per-boundary 转场/J-L cut 混音/甩镜） |
| Lead | `pipeline/editorial.py`、`pipeline/storyboard_v5.py`、`pipeline/render_v5.py` 集成、重渲/批处理、docs |
| stage-dev | `frontend/src/components/v5/Storyboard.tsx` 等 + 后端 editorial/storyboard API |
| qa-verifier | 剪辑多样性测试 + 16/16 终门 + `docs/V5_4_ACCEPTANCE.md` |
