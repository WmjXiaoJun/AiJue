# V6.5 全 3D 重设计：云端 MiniMax-H3 电影级 3D 路线（实施记录）

> 用户要求把 5 分钟文物解说 Demo **做成全 3D、重新设计**。
> 本文记录本会话落地结果（与 `docs/PLAN_V6_5_RELIC_3D.md` 的设计一致）。

## 1. 路线结论

| 尝试 | 结果 | 结论 |
|---|---|---|
| 2.5D 贴图/动漫立绘（V6.4） | 像贴纸、非 3D | 放弃 |
| 本地 ComfyUI H3（动漫首帧） | 卡通畸形、风格崩 | 放弃（pilot 归档到 `tmp/archive_relic3d_pilot/`） |
| Blender 程序化真 3D | 几何体小人、场景空洞 | 原型可用但达不到交付质量（`tmp/blender/relic3d_build.py`） |
| **云端 MetaSo MiniMax-H3 文生视频** | **电影级 3D CG / 写实光影**（博物馆体积光、剑刃微距、青铜铸造、考古发掘、越王勾践） | ✅ 采用 |

云端 H3 实测：768P、8–15s/镜、约 28s 生成、¥0.45–1.35/镜，**不需要首帧**，画面真实 3D CG。

## 2. 已交付：90.75s 全 3D 样片

- 成片：`outputs/templates_v5/relic_talk_3d/final_pilot3d.mp4`（**1280×720/25fps/90.75s**，含旁白/字幕/BGM）；
- 内容：博物馆片头 → 展厅漫步 → 老周出镜解说 → 剑刃微距 → 「文物不会说话」 → 越王勾践剑与铭文 → 青铜铸造 → 越王勾践 → 考古发掘；
- 抽帧：`tmp/v59_3d/cloud_pilot_frames.png`；
- 项目「造片局 · 国宝会说话（5分钟 Demo）」的成片已指向该 3D 样片，前端可直接播放。

## 3. 生产管线（pipeline/cinema3d.py + cinema3d_assemble.py）

- `contract/v5/cinema3d/relic_talk_3d_briefs.json`：每镜 subject/camera/env/ambience/chars（3D 电影化 brief）；
- `pipeline/cinema3d.py`：episode.json + briefs → 每镜拼云端 H3 prompt → `outputs/templates_v5/relic_talk_3d/shots/<sid>.mp4` + meta（provider/task_id/cost）+ 抽帧 sheet；支持 `--pilot/--only/--force/--concurrency`、断点续跑；
- `pipeline/cinema3d_assemble.py`：3D 镜头 → 剪辑/字幕/旁白/BGM/环境床 → 成片；
- 目前 **24/56 镜**已生成；其余镜头在补渲时遇到云端 **429 concurrent video task limit**（账号并发限制 1–2），已启动 detached 串行重试（`tmp/v61_retry_missing.py`，每镜 5 次冷却重试）。

## 4. 下一步

1. 等补渲完成（约 30–60 分钟，取决于并发限制恢复），跑完整 5 分钟 3D 成片（`python -m pipeline.cinema3d` 断点续跑 + `pipeline.cinema3d_assemble`）；
2. 完整版替换项目 episode 成片与模板卡（`contract/v5/templates/relic_talk_3d.json`）；
3. 若并发限制长期存在：改为「本地排队 + 云端 1 并发」的夜间批量，或按镜购买更高并发；
4. 可选：3D 片头/章节卡（已由 briefs 的 title/end 卡覆盖）、H3 原声环境床混音参数微调。
