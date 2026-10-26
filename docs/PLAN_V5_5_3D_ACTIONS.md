# V5.5 计划：3D/电影感混合 + 角色表演动作

> 用户反馈：「为什么现在全是 2D 的没有 3D 的啊？还有人物都没有动作啊」
> 现状诊断：
> - **V5 是 2D AI 美术 + 2.5D 运镜**：每镜是 SDXL 场景 plate + AI 立绘（单张 cutout）+ motion2d 的视差/推拉摇移/景别/转场；人物只有口型/眨眼/极弱的呼吸微动，**没有身体动作/手势/姿态变化**。
> - **3D 管线其实存在**：V3/V4 用 ComfyUI MiniMax **H3 image-to-video**（`pipeline/h3_comfy.py`、`integrations/comfyui/run_h3_ref.py`、workflow 04/05）生成过 16 部「3D 动漫 + 真实动作」成片（`outputs/templates_v3/*/final.mp4`，18 个目录），但那是 V3 旧美术（3D-toon 角色 + 程序化 plate）。
> - V5 之所以是全 2D：为了 16 种截然不同的风格 + 全 CPU 快速重渲（~45s/部）。3D/H3 每镜约 **2 分钟 GPU**（实测 4s→5.17s 片段耗时 124.7s），16 部×8 镜 ≈ 4+ 小时，且 H3 会弱化风格差异。

## 1. 目标

1. **角色有动作（全部 16 部，CPU 可负担）**：
   - 2.5D 角色表演：**姿态变体立绘**（point 指点 / think 沉思 / 可选 side 侧身）+ `pose_timeline` 交叉淡化 + 可见 idle（重心移动/前倾/点头/呼吸）+ 可选 walk_in 入场。
   - 每镜按台词与角色分配姿态（说话者 point/gesture，听者 think/idle；b-roll 侧身），让画面"有人在演"。
2. **3D/电影感（混合模式，可控成本）**：
   - 新增 `h3` 混合：每模板挑选 **N 个 hero 镜头**（对白特写/动作镜）→ 用 V5 首帧（AI plate + AI 立绘合成）作为 H3 image-to-video 参考 → H3 生成真实动作/运镜的 1024×576 clip → 归一化 1280×720 后替换回 EDL → 用 V5 现有 assemble（字幕/包装/BGM/转场）出片。
   - 全片 3D 成本高（128 镜≈4.3h GPU），先做 **pilot（3 镜）** 验证画质/动作/一致性，再决定 rollout：每模板 1-2 hero 镜（16-32 镜≈35-70 分钟）或单模板全 H3。

## 2. 实施阶段

| 阶段 | 内容 | 验收 |
|---|---|---|
| V5.5.A1 | 24 角色 × 2 姿态变体立绘（point/think，AI 生成 + 白底抠图）`assets/v5/characters/<id>/cutout_point.png` | 24/24 量质校验；montage |
| V5.5.A2 | motion2d：`pose_timeline` 姿态切换（4-6 帧淡化）+ 可见 idle/说话动作（前倾/点头/重心） | 动画自测 mp4/sheet；pytest 不回归 |
| V5.5.A3 | render_v5：按镜分配 pose_timeline；16 部重渲 | 16/16 QC + 动作可见（抽帧/人眼） |
| V5.5.B1 | H3 hybrid pilot（tonight_show 3 镜）：首帧合成 → H3 → 替换 → assemble demo | demo mp4；人物真实动作/运镜；成本/质量记录 |
| V5.5.B2 | H3 hybrid 集成 `pipeline/h3_v5.py`（可配置每模板 hero 镜数量） | 单元/CLI；单模板 demo |
| V5.5.B3 | 全量 rollout（按用户确认预算） | 验收 |

## 3. 成本/风险

- H3 实测：1 镜 5.17s = 124.7s（16GB 显存、4step SLA）；**必须独占 GPU、串行**。
- H3 会重绘/轻微改变造型（尤其 2D 动漫参考图），可能出现脸/服装漂移；缓解：H3 prompt 强调 same character/outfit + 低 motion；pilot 先看画质。
- H3 输出 24fps/1024×576，需要归一化 25fps/1280×720。
- 2D 姿态变体由 IP-Adapter 保持身份，但手势/服装细节可能变化 → 人工抽检 + 必要时单角色重生成。

## 4. 文件边界

| Owner | 文件 |
|---|---|
| render-dev | `pipeline/v5_art.py`（pose 资产/接口）、`scripts/gen_v5_character_poses.py`、`assets/v5/characters/`、`tmp/v55_poses/` |
| motion-dev | `pipeline/motion2d.py`（pose_timeline + 表演动作）、`docs/V5_MOTION.md`、`tmp/v55_motion/` |
| Lead | `pipeline/render_v5.py`（pose 分配/渲染接入）、`tmp/v55_h3_hybrid_pilot.py`、`pipeline/h3_v5.py`（若 rollout）、docs、重渲/验收 |
| qa-verifier | `tests/test_v5_5_acceptance.py` + `docs/V5_5_ACCEPTANCE.md` |
