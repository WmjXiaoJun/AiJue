# V6.5 报告：全 3D 重设计（「国宝会说话」3D 版）

用户要求：把 5 分钟文物解说 Demo **做成全 3D 的，重新设计**。

## 1. 方案对比与选择

| 方案 | 结果 |
|---|---|
| Blender 5.0 程序化真 3D 场景（原生建模/渲染） | 已跑通（相机/灯光/道具/角色可渲染，~0.3s/帧），但人物与场景是原始几何体，质量达不到可交付的"3D 动画电影"水准 |
| **H3「3D animated film / Pixar-style」重渲（采用）** | 用 2.5D 场景/角色资产合成首帧 → H3 以 3D 动画电影 prompt 图生视频 → **真正的 3D CG 动画画风**（体积光、3D 场景、3D 角色、口型随音频） |

## 2. 3D 重设计管线（新增）

`tmp/v59_3d_full_worker.py` / `tmp/v59_3d_pilot.py`：
1. 取 `relic_talk` 的 54 个非卡片镜头（景别/运镜/台词/音频沿用 V5 editorial 时间线）；
2. 每镜：2.5D 场景 plate + 2.5D 角色 cutout 合成 1024×576 首帧；
3. H3 图生视频（有台词镜用 `audio_ref` 真口型；b-roll 用 `fl2v`），prompt：
   `3D animated film still, Pixar-style 3D CG character, stylized 3D animation, volumetric museum lighting, Unreal Engine 5 render, detailed 3D environment, cinematic, high quality`；
4. 归一化 1280×720/25fps → 逐镜 `shots3d/<id>.mp4`；
5. 按 editorial 转场/J-L 拼接 + 原 TTS 旁白 + 字幕/BGM → `final.mp4`。

## 3. 已完成：全 3D 样片（65s）

- `outputs/templates_v5/relic_talk_3d/final_pilot.mp4`：10 镜、**64.96s**、1280×720/25fps、含字幕/BGM；
- 画风：**Pixar 式 3D CG**——3D 博物馆大厅（体积光、穹顶、金色立柱）、3D 角色（老周/太史公/文物人物）、3D 展柜与文物；
- 抽帧：`tmp/v59_3d/pilot_frames.png`；
- 项目 episode 的成片已临时指向该 3D 样片（前端项目→成片可播放）。

## 4. 完整 5 分钟 3D 版

- detached worker 正在后台渲染剩余镜头（约 44 镜 × ~2.2 分钟 ≈ 95–100 分钟，ETA ~17:30）；
- 完成后输出 `outputs/templates_v5/relic_talk_3d/final.mp4`（306s），再把项目 episode/模板卡指向完整版；
- 新模板：`contract/v5/templates/relic_talk_3d.json`「国宝会说话 · 3D」（18 个内置模板），关联项目 `proj_59fc69016c`、格式 `relic_talk_5min`。

## 5. 已知限制

1. H3 每镜独立生成，**角色跨镜一致性有限**（同一角色不同镜会有细节漂移），需要 prompt/参考图或首尾帧策略进一步约束；
2. 每镜 2–3 分钟 GPU，5 分钟全 3D 约 1.5–2 小时；
3. 3D 场景由 AI 生成，文物（剑/画/钟）细节不保证考据准确；
4. 字幕/包装沿用 V5 体系，3D 版尚未做专属片头/章节卡。
