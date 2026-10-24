# 参考的开源项目与采用方式

| 参考项目 | 借鉴内容 | 采用方式 |
|---|---|---|
| **ArcReel** | 项目制 + 资产先行 + 分镜/生成工作区 + 任务中心 + 费用追踪 + 版本回滚 + 剪映草稿 | 仅借鉴交互与信息架构，AGPL 代码不复制；导出格式自研 |
| **OpenMontage** | pipeline / agent skill 方法论、质检规则、provider 抽象、FFmpeg 工具层 | 借鉴流程设计；不以其为底座 |
| **Toonflow**（AI 短剧漫剧工具） | 小说→剧本→分镜→关键帧→视频→剪辑的全流程组织方式、画布/工作流形态 | 借鉴流程与关键帧思路；MIT 部分可参考 |
| **ComfyUI + MiniMaxH3 节点** | H3 本地推理：MiniMaxH3ImageToVideo（首帧/尾帧）、MiniMaxH3ReferenceToVideo（ref_images/ref_videos/ref_audios） | 直接使用其节点与工作流（04_i2v_fl2v / 05_ref2va），自研 CLI 包装 |
| **VideoLingo / 同类译制库** | 字幕翻译、配音、时长拟合的思路 | 借鉴译制流程；自研 localize/pipeline（Ollama 翻译 + edge-tts） |

## 本平台对应的实现
- 全动态镜头：pipeline/dynamic.py + integrations/comfyui/run_h3_ref.py
  - 说话镜头：H3 05_ref2va（ref_image + ref_audio）→ 口型跟随 TTS 音频；
  - 反应/动作镜头：H3 04_i2v_fl2v（first_frame + last_frame）→ 首尾帧可控动作；
  - 静帧只用于片头/片尾/花字卡/蒙太奇。
- 转场：pipeline/render.py 的 xfade 预设 + whoosh 音效（cut/xfade 可配置）。
- 分镜/首尾帧/动态版本：backend Shot 字段（first_frame_url/last_frame_url/h3_video_url/dynamic_mode/dynamic_status）+ ShotVersion。
- 多语种：localize/ + pipeline/localize.py（Ollama 翻译、多语 TTS、目标语重生成画面包装）。

## 说明
- 用户反馈「只有一个人有嘴形和动作、其它全没有」的根因：旧版成片只对 3 个角色各用了 1 条 H3 镜头，其余 49 镜是静帧 + Ken Burns。本轮改为逐镜生成动态 clip，且说话镜头用音频驱动口型。
