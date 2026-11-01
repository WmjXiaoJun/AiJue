# V16：高清样片（1024×576 源 + 运镜/远景 + 原生口型）— 单模板验证版

> 用户反馈：「1 提高清晰度 2 现在没运镜和远景了」→ 先只做一个模板给用户验证，通过后再全量跑。

## 1. 本版改动

| 项 | 旧（V15） | 新（V16 样片） |
|---|---|---|
| 生成分辨率 | 768×432 | **1024×576**（T2VA 与 ref2va 都提升） |
| 对话镜生成 | 首帧 + 音频 → ref2va（默认提示词强制「medium close-up，构图稳定」，所以没有运镜/远景） | **首帧 + 音频 + 自定义提示词**（中景/中远景、保留场景与人、指定 Camera 运镜、允许身体与镜头运动） |
| 无台词镜头 | 768×432 T2VA | 1024×576 T2VA |
| 口型 | 生成阶段原生（ref2va） | 仍为生成阶段原生，无贴片 |
| 剪辑 | 1280×720/25fps | 不变（源更清晰，成片观感更锐利） |

关键发现：`integrations/comfyui/run_h3_ref.py` 的默认 `NEUTRAL_PROMPT` 明确写了
“One continuous medium close-up … Keep the face … and composition of the reference image stable; only the mouth, eyes and head move subtly.”
——这就是 V15 没有运镜/远景的原因。本版通过 `--prompt-file` 传入自定义提示词（语义画面 + 运镜 + 中景/中远景 + 允许镜头运动）。

## 2. 样片范围

只重做了 **tonight_show（美式深夜秀）** 一个模板：
- 9 镜全部 1024×576 重新生成（2 个 b-roll 走 T2VA，7 个对话镜走 T2VA 首帧 + ref2va 原生口型）
- 成片：`outputs/templates_3d/tonight_show/final.mp4`（41.61s / 1280×720 / 17.2MB）
- 旧版对比：
  - MuseTalk 版：`/media/_archive_v14_musetalk/tonight_show.mp4`
  - V15 ref2va 768 版镜头备份：`outputs/_archive_v15_ref2va_768/tonight_show/shots/`
- 对比图：`tmp/v16/hq_vs_old.png`（上=旧 768 MuseTalk，下=新 1024 样片）
- 单帧：`tmp/v16/hq_final_sheet.png`

## 3. 耗时

- 单模板（9 镜：9×T2VA + 7×ref2va）：**04:24→05:10 ≈ 46 分钟**
- 全量 16 模板按同样配置估算 **≈ 11–13 小时**（T2VA 1024 约 140–150s/镜，ref2va 1024 约 200–230s/镜）
- 加速选项（全量时可选用）：ref2va 降到 896×512（约 -20% 时间）、或对话镜复用已有 T2VA 首帧（省一次 T2VA，约 -35%，但构图沿用上一轮）

## 4. 验证方法

1. 直接播放：`http://127.0.0.1:8090/media/templates_3d/tonight_show/final.mp4`
2. 页面播放：`#/cinema/templates` → 美式深夜秀 → 播放
3. 对比旧版（无运镜/近景为主）：`http://127.0.0.1:8090/media/_archive_v14_musetalk/tonight_show.mp4`
4. 重点看：① 画面清晰度 ② 是否有运镜/远景 ③ 对话时嘴型是否自然、无马赛克 ④ 画面与字幕台词是否对得上

## 5. 待用户确认

- ✅ 如果这版可以：我按同样配置全量重做 16 个模板（预计 11–13 小时，可夜间跑，跑完汇报 16 个数据 + 对比图）
- 若要调整：告诉我要改哪项（更激进运镜 / 更远景 / 更高分辨率 1152×648 / 更快方案等），我改完再做一个样片
