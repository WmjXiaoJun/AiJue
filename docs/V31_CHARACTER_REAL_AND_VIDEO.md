# V31：写实立绘批量重生成（v2）+ 角色驱动试镜视频

> 用户：「角色有 3D 角色/真人角色么」→ 确认 24 角色已有 动漫/2.5D/写实 三版；「好的开始做吧 还有 然后在根据角色生成一遍视频我看一下」
> 交付：① 24 个角色写实立绘批量重生成（带中文族裔/年龄/服装约束，修复旧版偏差）；② 用角色立绘 + 角色声音生成一支 7.29s 试镜视频（H3 图生视频 + 口型同步）。

## 1. 写实立绘批量重生成

### 问题（旧版）
- 旧 `portrait_real.png` 为 2026-10 早期生成：部分角色与设定不符（如曹操写实版偏女性化）、年龄/服装漂移
- imagegen 默认 `realistic` 提示词（RealVisXL）偏向西方面孔，直接重生成会出现族裔漂移

### 做法
- 用 `POST /api/v5/characters/{id}/art {style:"realistic", prompt_extra, force:true}`
- 按角色合同自动拼 `prompt_extra`：`Chinese + era（modern/历史）`、`East Asian facial features`、性别、年龄、发色+发型、服装类型+主色、配饰；robot/mystery_guest 单独提示词
- 逐角色串行（GPU 锁），先备份旧图到 `tmp/v31/backup_real/`（v1 再备份 `backup_real_v1/`）

### 结果
| 轮次 | 角色数 | 总耗时 | 单角色 | 说明 |
|---|---|---|---|---|
| v1（无族裔约束） | 24 | 266.8s | 10.4–12.4s | 修复了曹操性别，但出现西方面孔漂移 |
| **v2（中文族裔+年龄+服装）** | 24 | **298.6s** | 10.4–12.5s | **24/24 全部成功**，东亚面孔/性别/服装一致性明显改善 |

- 产物：`assets/v5/characters/<id>/portrait_real.png` 24 张（832×1216，约 31.6MB），已提交
- 对比：`tmp/v31/real_v1_v2_compare.jpg`（旧/v1/v2 三行对比）、`tmp/v31/real_all_24_v2.jpg`（24 角色 v2 总览）
- 角色详情「立绘版本」切「写实」即可看到新图（cache-bust 已生效）

## 2. 角色驱动试镜视频（demo）

### 方法
1. 选角色：老周（laozhou）
2. 台词：`各位好，我是老周。今天这段戏，是用我的一张角色立绘直接生成的。`
3. TTS：`POST /api/v5/voice/laozhou/preview`（edge 兜底）→ 7.32s 音频
4. 参考图：写实立绘 v2 裁 16:9 → `tmp/v31/laozhou_ref_v2_169.png`（1024×576）
5. H3 图生视频（口型同步）：
   ```
   python integrations/comfyui/run_h3_ref.py --mode audio_ref \
     --image tmp/v31/laozhou_ref_v2_169.png --audio tmp/v5_voice/laozhou.wav \
     --out tmp/v31/laozhou_real_talk_v2.mp4 --duration 7.32 \
     --width 1024 --height 576 --fps 25 --seed 77 \
     --prompt-file tmp/v31/prompt_real_v2.txt
   ```

### 结果
| 项 | 值 |
|---|---|
| 成片 | **7.29s / 1.21MB / 1024×576 / 25fps / H.264+AAC 有音轨** |
| 生成耗时 | **191.7s（3m11s）** |
| 内容 | 老周（写实立绘）在深夜电台麦克风前说话，口型与台词同步，navy 西装+金领带保持 |
| 访问 | `http://127.0.0.1:8090/media/character_demos/laozhou_real_talk.mp4` |
| 帧 sheet | `outputs/character_demos/laozhou_real_talk_sheet.png` / `tmp/v31/laozhou_real_talk_v2_sheet.png` |
| 参考图/音频 | `outputs/character_demos/laozhou_ref_169.png`、`laozhou_line.wav` |

- 同一方法可对任意角色生成（换 portrait 参考图 + 角色 TTS），单条约 3–4 分钟
- 说明：这是「立绘 → 图生视频 + 口型同步」的试镜 demo，不是完整 3D 分镜流水线；身份/服装保持较好，但年龄/背景会被模型轻微改写
- 旧版（v1 写实立绘 + 弱提示词）对比视频：`tmp/v31/laozhou_real_talk.mp4`（效果较差，已保留作对比）

## 3. 证据与提交
- 对比图：`tmp/v31/real_v1_v2_compare.jpg`、`real_all_24_v2.jpg`
- UI：`tmp/v31/lead/01_laozhou_real_v2.png`（角色详情写实版新图）
- 视频：`outputs/character_demos/laozhou_real_talk.mp4`（/media 200）
- 提交：24 张新写实立绘（assets）+ 本文档

## 4. 遗留 / 后续
- 2.5D 立绘未重生成（如需要可同法批量，约 5 分钟）
- 写实生成使用 RealVisXL + 文本约束，个别角色（如历史人物）服装/年代感仍可再调
- 可产品化：角色详情加「生成试镜视频」按钮（立绘 + 台词 + 声音 → H3 出片，约 3–4 分钟）
