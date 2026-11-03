# V22：从「创建模板」到「真实 3D 成片」全流程验收（新模板 demo）

> 用户：「好的开始下一步」→ 用 V21 的可视化创建能力，真实创建一个全新模板并跑完整 GPU 3D 生成，验收「UI 创建 → TTS 分镜 → 3D 生成 → 成片」全链路。

## 1. 创建（POST /api/v5/templates，13.6s）

| 项 | 值 |
|---|---|
| 模板 id | `custom_tpl_v22_radio` |
| 中文名 | 深夜电台 · 城市来信 |
| 风格 | `vhs_late_show`（80s VHS 深夜秀） |
| 画幅 / 时长 | 16:9 / 23.45s |
| cast | 老周(host·laozhou) / 阿栗(figure_a·ali) / 大魏(figure_b·dawei) |
| 分幕 | 6 幕：城市夜景 b-roll → 主持人开场 → 听众来信 → 雨夜街道 b-roll → 嘉宾回应 → 地铁口收尾 |
| 后端产物 | 6 条 24k mono TTS wav + `episode.json` + semantic brief 6/6（warnings=[]） |

台词：
1. 老周：「各位深夜好，这里是城市来信，我是老周。」
2. 阿栗：「今晚想聊一个很多年没敢说出口的名字。」
3. 大魏：「写信的人说，他其实一直没离开过这座城。」

## 2. 生成（POST /api/pipeline/run，真实 GPU）

| 项 | 值 |
|---|---|
| job id | `pl_08e62538faa2` |
| 阶段 | prepare → generate（6 镜）→ lipsync（3 对话镜 ref2va）→ assemble → poster → done |
| 总耗时 | **1070.6s ≈ 17.8 分钟**（单镜 2.5–4 分钟，1024×576 T2VA + ref2va 原生口型） |
| 成片 | **23.648s / 8.33MB / 1280×720 / 25fps / H.264+AAC** |
| 产物 | final.mp4 + poster.jpg + report.json + timeline.html/json + 6 个逐镜 mp4 |
| 状态 | `/api/cinema/templates` 20/20 done、147/147 clips |

## 3. 验收证据

- 成片直链：`/media/templates_3d/custom_tpl_v22_radio/final.mp4`
- 抽帧：`tmp/v22/verify/contact_sheet.png`（6 帧）、`frame_6s.png`、`frame_16s.png`
- UI：模板库新模板卡「已完成」（`tmp/v22/ui/03_library_template_done.png`）、成片库 20/20（`tmp/v22/ui/04_final_library_new.png`）
- 内容核对：字幕/台词与分幕一致；VHS 深夜电台场景（霓虹灯牌、主持人麦克风、雨夜街道、地铁口）
- 页面 console 无 JS error；pipeline job 状态机与进度上报正常

## 4. 结论

- 「UI 创建模板 → 自动 TTS/分镜/semantic → 一键 3D 生成 → 成片库播放」全链路已打通，新模板可直接进入流水线生成。
- 模板走的是**同一套 3D 流水线**（T2VA→ref2va→剪辑→海报）；差异只在模板合同（风格/角色/场景/台词/时长）。
- 生成耗时与既有 6 镜模板一致（约 18 分钟）。

## 5. 提交与保留

- 新模板合同：`contract/v5/custom/templates/custom_tpl_v22_radio.json`
- semantic brief：`contract/v5/cinema3d/templates3d/custom_tpl_v22_radio_semantic.json`
- 成片/工作产物在 `outputs/templates_3d/custom_tpl_v22_radio/`（gitignore，可随时用「重新生成」再产出）
- 该模板会作为第 20 个 3D 模板出现在「模板与素材」与「一键成片」中；如不想保留可调 `DELETE /api/v5/templates/custom_tpl_v22_radio`（已有 final 时需先清理产物）
