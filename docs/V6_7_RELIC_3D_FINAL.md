# V6.7 交付：5 分钟《国宝会说话》全 3D 电影感成片

> 承接 V6.5/V6.6 两条 3D 样片尝试。本轮按用户要求「全 3D、能看、本地 H3 模型」完成 **325.21s 全 3D 成片**：
> 云端 MiniMax-H3 生成前半段 24 镜（真实 3D CG 电影级），云端余额耗尽后改用**本地 ComfyUI MiniMax-H3（T2VA 4step turbo）**补齐其余 33 镜，
> 统一影调/锐化/颗粒后剪成 5 分钟成片，接入项目成片页。

## 1. 交付物

| 项 | 路径 | 说明 |
|---|---|---|
| **成片** | `outputs/templates_v5/relic_talk_3d/final.mp4` | 1280×720 / 25fps / H.264+AAC / 约 5:25 |
| 剪辑报告 | `outputs/templates_v5/relic_talk_3d/work_assemble/final_report.json` | 时长/黑帧/响度/字幕/每镜时长偏差/unify_look 参数 |
| 抽帧证据 | `outputs/templates_v5/relic_talk_3d/frames_final/` + `contact_sheet.png` | 8 帧 + 宫格 |
| 逐镜素材 | `outputs/templates_v5/relic_talk_3d/shots/<sid>.mp4` | 1344×768（云端）/ 768×432（本地） |
| 逐镜抽帧 | `outputs/templates_v5/relic_talk_3d/frames/<sid>_sheet.png` | 2×2 抽帧 |
| 生成报告 | `outputs/templates_v5/relic_talk_3d/generation_report.json`（云端）/ `local_generation_report.json`（本地） | 每镜状态/耗时/成本/prompt_id |
| 视觉 QA | `tmp/v61_3d/qa_review.json` / `qa_review.md` | 逐镜评分 + regen 清单 |
| 项目绑定 | 项目 `proj_59fc69016c` / `ep_5610123283` | 成片页 `render_url` 指向本片 |

## 2. 设计（相对 V6.4 的重新设计）

- **叙事**：保留原 49 句解说词与固定音色 TTS（老周/太史公/东坡），从「2D 立绘站着说台词」改为**旁白驱动的文物纪录短片**：讲人话、看画面。
- **画面**：每个 episode 镜头 = 一条真正 3D CG 镜头；博物馆（中式漆柱/格窗/展柜/体积光）、考古现场、古战场/铸造、越王勾践、少年画师、编钟乐师、青绿画中世界。
- **风格锁**：`high-end 3D animated feature film shot, cinematic CGI, physically based materials, volumetric light, shallow depth of field, warm amber + mineral blue-green`（写入 `contract/v5/cinema3d/relic_talk_3d_briefs.json`，54 镜逐镜 subject/camera/env/ambience）。
- **卡片**：片头 3D 博物馆 + 金字标题「国宝会说话 / 造片局 · 5 分钟文物解说」；片尾「下期见」。
- **剪辑**：纪录片式硬切 + 片头/三件国宝章节/片尾交叉溶解；字幕白字淡金描边；三件国宝章节小标。
- **声音**：旁白 + 每镜 H3 原声环境床（-20dB）+ `museum_gold` BGM（旁白 ducking），两遍 loudnorm 到 -16 LUFS。

## 3. 生产流水线（新增）

| 模块 | 作用 |
|---|---|
| `contract/v5/cinema3d/relic_talk_3d_briefs.json` | 54 镜 3D 视觉 brief（可编辑数据合同） |
| `pipeline/cinema3d.py` | 云端 MetaSo MiniMax-H3 逐镜生成器（768P，并发/缓存/重试/成本统计/抽帧） |
| `pipeline/cinema3d_local.py` | **本地 ComfyUI MiniMax-H3 T2VA 生成器**（4step turbo 768×432，串行、断点续跑） |
| `pipeline/cinema3d_assemble.py` | 剪辑器：卡片/字幕/BGM/环境床/转场/统一影调/两遍 loudnorm/QC 报告 |
| `outputs/templates_v5/relic_talk_3d/work/episode3d.json` | 3D 版时间线（片头 5s/片尾 4s，总长 325.21s） |

复现命令：

```powershell
# 云端 H3 生成（需 MetaSo 余额）
python -m pipeline.cinema3d
# 本地 H3 补齐缺失镜头（ComfyUI:8188 在线，约 75s/镜）
python -m pipeline.cinema3d_local --resolution 768x432
# 重新剪辑成片
python -m pipeline.cinema3d_assemble
```

## 4. 质量与验收（独立 QC：`tmp/v61_3d/qc_final/qc_report.json`）

| 项 | 结果 |
|---|---|
| 时长 | **325.20s（5:25）**，与 episode3d 325.21 偏差 **-0.01s**；56/56 镜时长偏差 max 0.03s、mean 0.013s |
| 规格 | 1280×720 / 25fps / H.264 yuv420p + AAC 48k 立体声 / 96.0MB / faststart |
| 黑帧 | **blackdetect 0 段**（片尾 1.0s 淡出未触发阈值） |
| 响度 | integrated **-15.98 LUFS** / true peak **-1.46 dBTP** / LRA 15.2 |
| 字幕 | **60 事件**：旁白 **49/49 句 100% 覆盖** + 片头主副标题 3 + 三件国宝章节小标 6 + 片尾「下期见」+ 淡出 1；正文 SimSun 白字淡金描边、底部安全区 |
| 时间线 | 帧对齐 `_edit_start`，max_delta 0.02s；4 处交叉溶解（片头 0.48s + 三章各 0.4s）+ 片尾 1.0s 淡出 |
| 镜头评审 | 54 非卡片：**pass 43 / warn 11 / regen 0**（视觉 QA 逐镜 56 张 2×2 抽帧，`tmp/v61_3d/qa_review.json`） |
| 成片验收 | 视觉 QA 独立验收（两轮）：**pass-with-warn 可交付、无 regen**（`tmp/v61_3d/qa/final_acceptance.md` / `final_acceptance_v2.md`）；028→029 连播重复已修复、v5_030 铭文特写与台词逐字对应；无畸形脸/乱码/水印 |

## 5. 已知问题 / 边界

1. **云 / 本地混剪有 5 处轻微风格跳变**（014→015、015→016、025→026、030→031、032→033）：本地 H3（4step turbo 768×432）比云端 1344×768 偏平、偏亮、背景偶有白色展板；已用统一影调（暖金高光/青绿暗部、暗角、轻颗粒、软源 unsharp 1.0 校准）与章节卡遮挡最大跨度，但细看可辨。
2. **第三幕为纪录片式 B-roll 复用**：026–054 中 23 镜复用已审母素材的不同入点/慢放（`tmp/v61_3d/reuse_report.json`），母素材高密度镜头：027×4、025×3、014×3、009×3、005×3、018×3，会有「回顾蒙太奇」观感；这是本地 H3 长期单镜 stall 后的取舍。
3. **4 处溶解点末帧冻结 0.305–0.40s**（片头卡 5.175s / 源 8.0s 镜）；为保证 325.21s 总时长不动。
4. 文物细节为 AI 再创作（铭文/鸟篆为伪字符、菱形暗纹质感偏均匀），符合「3D 动画电影」而非文物复刻标准。
5. 云端 H3 余额耗尽（HTTP 402），剩余 26 镜未走云端；如后续充值，可用 `python -m pipeline.cinema3d` 断点续跑替换对应镜头后重新 `cinema3d_assemble`。
6. 本地 H3 在「多人 + 全景」复杂 prompt 上偶发极慢（v5_029 曾 >13 分钟 stall），复现时建议降低分辨率/缩短 prompt 或改云端。

## 6. 时间与成本

- 云端：24 镜（含测试/重试）MetaSo H3 约 ¥10.3；余额耗尽后停用；
- 本地：13 镜 H3 生成（015/023/024/026/027/028/030/004/card_end 等）约 25 分钟 RTX 3080 Ti 串行（单镜 57–288s）；第三幕其余 23 镜用**纪录片式 B-roll 复用**（已审母素材不同入点/慢放，`tmp/v61_3d/reuse_report.json`）；
- 剪辑编码：全量约 148s（含缓存）。

