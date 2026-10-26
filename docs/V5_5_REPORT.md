# V5.5 报告：3D/电影感混合 + 角色表演动作

> 计划：`docs/PLAN_V5_5_3D_ACTIONS.md`；上一版：V5.4（剪辑/分镜/转场）。

## 0. 用户问题与结论

| 问题 | 结论 |
|---|---|
| 为什么现在全是 2D，没有 3D？ | V5 的定位是 **16 种风格可辨 + 全 CPU 快速重渲**（~45s/部）：AI 场景 plate + AI 立绘 + 2.5D 运镜/景别/转场。真正的 **3D/电影感动画（H3 image-to-video）** 在 V3/V4 链路里存在（`pipeline/h3_comfy.py`，`outputs/templates_v3/*/final.mp4` 18 部）——那是 3D-toon + 程序化美术。要 V5 美术 + 3D 动作 = **H3 混合**，成本约 **2 分钟 GPU/镜**（实测 5.17s 片段 124.7s），16 部×8 镜 ≈ 4.3 小时，且 H3 会重绘细节（画风漂、偶发多出角色）。 |
| 人物都没有动作？ | 已补齐 **2D 表演系统**：24 角色 × **point/think** 姿态变体立绘 + `pose_timeline`（4-6 帧交叉淡化）+ 可见 idle（呼吸/重心/偶发点头）+ 说话前倾 + 可选 walk_in 入场；`render_v5` 已按台词语义分配姿态并全量重渲 16 部。 |

## 1. 2D 角色表演（已落地）

- **姿态资产**：`assets/v5/characters/<id>/{cutout_point.png,cutout_think.png}`，24/24 角色 ×2 姿态（IP-Adapter 以 portrait 为参考保身份；白底/抠图/alpha 校验全过；48/48）。
- **motion2d 参数**（向后兼容）：
  - `pose_timeline: [[t0,t1,"front"|"point"|"think"|"side"|"lean_in"], ...]`，切换 4-6 帧交叉淡化
  - `idle_strength`（默认 1.0）；`gesture`（默认 True）；`perform` 总开关
  - `entrance="walk_in"` + `walk_in_from`（画面外走入，0.8s）
  - 可见表演：呼吸 dy 0~-6px + 1% 挤压、重心 dx±6px、说话前倾 -6px/+2%/0.018 shear/2.4s 点头、偶发点头/侧头（2-4s、确定性 seed）
  - 性能：单帧合成 8.8-17.4ms（≤25ms）
- **render_v5 接线**：每个对白镜按台词关键词生成 pose_timeline（指/看/地图→point；为什么/是否/问题→think；其它→lean_in），`front→动作→front` 节拍；非说话角色保持 front + 自动 idle；首出场镜 walk_in（`V55_PERFORM=1` 开关，默认开）。
- **已知修复**：图片 cutout 立绘的表演变换一度把角色整体旋转/斜切（已修：图片路径 shear 限制、比例/尺寸归一；回归自测 `tmp/v55_motion/image_cutout_perform.png`）。

## 2. 3D/H3 混合 pilot

- 脚本：`tmp/v55_h3_hybrid_pilot.py` → `tmp/v55_h3_assemble_demo.py`
- 流程：V5 当前 AI plate + AI 立绘合成 1024×576 首帧 → H3 image-to-video（audio_ref，用该镜 TTS 音频）→ 归一化 1280×720/25fps/裁剪到镜长 → 替换回 `shots/` → 用 V5 assemble（字幕/BGM/包装/per-cut 转场）出片。
- 结果（tonight_show 3 个对白镜）：`tmp/v55_hybrid/final_h3_demo.mp4`（43.71s，1280×720/25fps/have audio）
- 实测成本：3 镜 ≈ 6-10 分钟（含共享队列排队；单镜 125-195s）。
- 代价/风险：
  - H3 会重绘画面细节（服装/配色/道具漂移，偶发多出人物）→ 需 prompt 调优/更强参考（可用 3D-toon 参考图或降低 motion）
  - 24fps/1024×576 输出需归一化
  - GPU 必须独占串行

## 3. 推荐 rollout（供选择）

| 方案 | 范围 | GPU 成本 | 风险 |
|---|---|---|---|
| A 全 3D | 16 部全 H3 镜 | ~4-6 小时 | 画风/角色一致性漂移、需大量调参 |
| **B 混合（推荐）** | 每模板 1-2 个 hero 镜 H3，其余 2D 表演 | ~1-1.5 小时 | 风格断层（需统一调色/景别衔接） |
| C 2D 表演增强 | 16 部（已完成） | CPU ~20 分钟 | 无 3D 立体感，但动作可见 |

## 4. 文件/提交

- `pipeline/v5_art.py`（姿态生成/读取）、`scripts/gen_v5_character_poses.py`、`assets/v5/characters/`
- `pipeline/motion2d.py`（pose_timeline/表演/walk_in）、`pipeline/render_v5.py`（台词→姿态、`V55_PERFORM`）
- `tmp/v55_*`（自测/对比/H3 demo）
- 文档：`docs/PLAN_V5_5_3D_ACTIONS.md`、本报告
