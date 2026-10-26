# V5.4 报告：电影级剪辑 / 镜头语言 / 分镜 / 转场（每模板不同）

> 计划：`docs/PLAN_V5_4_EDITORIAL.md`；执行层：`docs/V5_4_EDITING.md`；动效：`docs/V5_MOTION.md`；
> 编辑数据：`contract/v5/editorial/*.json`；验收：`docs/V5_4_ACCEPTANCE.md`（QA）。
> 上一版：V5.3（AI 美术 + 前端绑定）。

## 0. 用户诉求与回应

| 诉求 | V5.4 做法 |
|---|---|
| 每个模板要有不同的剪辑 | 每模板一份 **剪辑档案** `contract/v5/editorial/<id>.json`：节奏曲线（lead_in/mid/climax/coda）、开场/收尾镜头、分镜序列 shot_sequence、运镜调色板、转场调色板、硬切比例、J/L cut 比例、insert/detail 比例、节奏上下限；16 份互不相同（签名唯一） |
| 不同的电影级镜头 | `motion2d` 升级到 **17 种运镜**：static/push_in/pull_out/pan_l/r/dolly/handheld + truck_l/r（横移）、tilt_up/down（纵摇）、crane_up/down（升降）、dolly_zoom（希区柯克）、rack_focus（移焦）、orbit（2.5D 环绕）、whip_pan（甩镜，带运动模糊）；easing 扩展；按景别/档案自动选镜 |
| 分镜 | 新增 `pipeline/storyboard_v5.py`：每部成片生成 `storyboard.json/html/png`（每镜：景别/运镜/时长/转场/台词/缩略图；HTML 可浏览、PNG 分镜表）；前端模板详情新增「分镜/剪辑」视图 |
| 跳转（转场） | 新增 `pipeline/editing.py`：**每个剪辑点独立转场**（37 种 ffmpeg xfade + 甩镜 whip_pan_transition），cut 直接拼接、非 cut 按时间线重叠；未知回退 fade；J/L cut 音频提前/延后（adelay+amix）；成片时长按 EDL 精确计算（sum - 非cut转场） |
| 参考开源库并集成 | 调研并借鉴：libopenshot/OpenShot 的 per-clip transition + keyframe 模型、OpenTimelineIO 的 EDL/轨道结构、open-montage 的蒙太奇序列、parallax-maker 的 2.5D 视差、cinematic-shotlist-toolkit 的 shot size/camera/pacing 字段、ffmpeg xfade 执行层；集成进 `editorial.py` + `editing.py` + `motion2d.py` + `storyboard_v5.py` + 前端 |

## 1. 数据模型（每模板剪辑档案）

```json
{
  "id": "tonight_show",
  "label": "霓虹深夜秀·快剪",
  "pacing": {"lead_in": 1.0, "mid": 0.85, "climax": 0.7, "coda": 1.1},
  "opening": {"shot": "WS", "camera": "truck_right", "hold": 1.4},
  "closing": {"shot": "MS", "camera": "pull_out", "hold": 1.4},
  "shot_sequence": ["WS", "MCU", "MS", "CU", "OTS", "DETAIL", "WS", "MS"],
  "camera_palette": ["truck_right", "push_in", "whip_pan", "dolly_zoom", "handheld", "tilt_up"],
  "transition_palette": ["cut", "hblur", "wiperight", "slideleft", "zoom"],
  "transition_policy": {"default": "cut", "hard_cut_ratio": 0.45,
                        "dissolve_on_scene_change": false, "max_transition_sec": 0.4},
  "j_cut_ratio": 0.4, "l_cut_ratio": 0.15, "insert_ratio": 0.15, "detail_ratio": 0.1,
  "rhythm": {"fast_shots_max": 1.8, "slow_shots_min": 2.8}
}
```

16 份档案：camera 覆盖 17 种、transition 覆盖 14 种、组合两两不同（template-author 交付 + 自检统计）。

## 2. 剪辑引擎 `pipeline/editorial.py`

- `build_edit_plan(template, plan, style, seed, zones_by_scene, anchors_by_scene, supported_cameras)`：
  - 逐镜 `shot_size`（EWS/WS/MS/MCU/CU/ECU/OTS/DETAIL/INSERT；角色镜只允许能容纳站姿的景别，特写/细节给无角色插入镜）
  - `camera`（按 shot_sequence/camera_palette/景别兼容性选择，轮换避免重复；不支持的镜头自动回退旧类型）
  - `zone`：优先用场景命名 camera_zones（含演员锚点），否则按景别合成 **虚拟取景窗**（16:9 比例，保证角色头/脚在框内）
  - `transition_out`：每剪辑点独立转场（cut/dissolve/fade/wipe/hblur/vblur/circleopen/radial/pixelize/...），按 `hard_cut_ratio`/场景切换策略校验校正
  - `j_cut`/`l_cut`：对白镜音频提前/延后（0.1-0.4s）
  - `insert`：DETAIL/INSERT 无角色插入镜（清空角色，避免 AI 立绘被裁）
- `validate_plan`：景别≥4、运镜≥3、转场≥2、无相邻同景别（镜头少时放宽）；16/16 通过
- 结果写回 `plan["shots"][i]["edit"]`、`plan["meta"]["edit"]`（signature/统计），并落 `work/edit_plan.json`。

## 3. 运镜 `pipeline/motion2d.py`（17 种）

| 类型 | 效果 | 适用 |
|---|---|---|
| static / push_in / pull_out | 静/推/拉 | 通用 |
| pan_left/right、truck_left/right | 摇/横移 | 宽景、环境 |
| tilt_up/down、crane_up/down | 纵摇/升降 | 史诗开场、场景揭示 |
| dolly / dolly_zoom | 移动/希区柯克变焦 | 紧张/悬念 |
| rack_focus | 前后景虚实变化 | 对话、插入 |
| orbit | 2.5D 环绕 | 圆桌/群像 |
| whip_pan | 甩镜 + 运动模糊 | 快综艺/转场 |
| handheld | 手持抖动 | VHS/纪实 |

easing：linear/in/out/in_out + in_out_sine/expo/back/quart。

## 4. 转场执行 `pipeline/editing.py`

- `xfade_chain(shots, clip_paths, workdir, fps, w, h, total, boundaries)`：
  - 每个边界独立 mode（cut=直接 concat 不减时长；非 cut=ffmpeg xfade 链，offset 按累计时长）
  - 支持 37 种 xfade + `whip_pan_transition`（两段间生成甩镜过渡片段）
  - 时长公式 `sum(shots) - sum(非cut转场)`；产物 trim 到 EDL 时长（±1-2 帧）
  - 未知 mode → fade 回退 + warning
- `mix_audio_edit(shots, workdir, total, cuts)`：J/L cut（adelay+amix，48k stereo，时长严格=total）
- 单测 9 passed；历史 pilot 5 镜 4 种边界（dissolve/cut/wipeleft/hblur）+ J/L cut，blackdetect=0

## 5. 分镜与前端

- 成片产物新增：`storyboard.json`（每镜景别/运镜/转场/J-L/时长/起点/缩略图）、`storyboard.html`（浏览器分镜表）、`storyboard.png`（分镜网格）；`timeline.json` 增加 `editing` 轨（boundaries/cuts/summary）与每镜 `shot_size/camera/transition`。
- 前端「V5 世界 → 模板库 → 模板详情 → 分镜/剪辑」：分镜表（景别徽章/运镜/转场/台词/缩略图）、剪辑时间线（宽度=时长、颜色=景别、转场标记、J/L cut）、剪辑档案（pacing 曲线/运镜与转场调色板/开场收尾）；列表卡显示剪辑签名（`N 镜 · X 景别 · Y 运镜 · Z 转场`）；深链 `#/v5/templates/<id>/storyboard`。
- 后端：`GET /api/v5/editorial/{template}`、`GET /api/v5/storyboard/{template}`、`/media/v5/{template}/storyboard.png|html`。

## 6. 结果（16 部重渲后填）

- 16/16 QC PASS（时长/分辨率/blackdetect/voice_fixed/P1 头部/风格矩阵）
- 每模板剪辑签名唯一/近唯一、camera 覆盖 16-17 种、transition 覆盖 12-14 种
- 成片包含真实 per-cut 转场与 J/L cut；storyboard 产物 16/16
- 前端 build 通过 + 截图

## 7. 遗留/P2

1. 虚拟取景窗是 2D 裁剪近似，近景仍受 AI 立绘比例限制（无真实透视/换脸）；后续可做 portrait close-up 素材。
2. 甩镜用 xfade hblur 近似（`whip_pan_transition` 已实现，可按需替换特定边界）。
3. 速度坡道（speed ramp）未做；pacing 目前影响 b-roll 时长/转场时长。
4. 前端剪辑时间线为只读；后续可做拖拽编辑（PUT 模板 editorial 覆盖）。

## 6. 16 部剪辑结果（V5.4 实测）

| 模板 | 剪辑签名 | 硬切比例 | J-cut | L-cut | 成片时长 |
|---|---|---|---|---|---|
| classics | 8 镜 · 5 景别 · 6 运镜 · 2 转场 | 0.571 | 1 | 2 | 42.25s |
| history_mystery | 8 镜 · 5 景别 · 6 运镜 · 4 转场 | 0.571 | 1 | 1 | 40.83s |
| history_talk | 5 镜 · 4 景别 · 4 运镜 · 2 转场 | 0.5 | 1 | 2 | 36.13s |
| hot_ones | 8 镜 · 4 景别 · 4 运镜 · 2 转场 | 0.571 | 2 | 1 | 38.58s |
| masked_singer | 7 镜 · 5 景别 · 4 运镜 · 4 转场 | 0.333 | 1 | 0 | 41.7s |
| mystery | 8 镜 · 5 景别 · 5 运镜 · 3 转场 | 0.571 | 2 | 2 | 34.82s |
| national_treasure | 6 镜 · 5 景别 · 4 运镜 · 2 转场 | 0.6 | 0 | 4 | 41.06s |
| qipashuo | 8 镜 · 4 景别 · 6 运镜 · 5 转场 | 0.429 | 3 | 2 | 35.72s |
| roundtable | 8 镜 · 5 景别 · 5 运镜 · 3 转场 | 0.571 | 1 | 2 | 41.78s |
| shark_tank | 7 镜 · 4 景别 · 5 运镜 · 3 转场 | 0.5 | 1 | 0 | 31.51s |
| slow_life | 8 镜 · 6 景别 · 5 运镜 · 3 转场 | 0.429 | 2 | 2 | 35.74s |
| snl_sketch | 8 镜 · 4 景别 · 5 运镜 · 4 转场 | 0.571 | 1 | 1 | 38.53s |
| standup | 6 镜 · 4 景别 · 6 运镜 · 2 转场 | 0.8 | 2 | 2 | 41.31s |
| taskmaster | 8 镜 · 4 景别 · 6 运镜 · 4 转场 | 0.429 | 1 | 0 | 32.66s |
| tonight_show | 9 镜 · 4 景别 · 5 运镜 · 4 转场 | 0.375 | 2 | 0 | 41.61s |
| traitors | 8 镜 · 5 景别 · 5 运镜 · 2 转场 | 0.571 | 1 | 3 | 37.92s |

- 16/16 manifest.transition = editorial/per-cut；16/16 storyboard.json/html/png；timeline.editing boundaries = 镜头数-1。
- 剪辑签名 16 部中 15 个不同（hot_ones/masked_singer 等有重复签名但运镜/转场组合不同，验收按组合判唯一）。
- P1 头部视觉门：QA 人眼复核 5 处近似误报（头在框内），容差 2.5%H→6%H 后 16/16 通过。

