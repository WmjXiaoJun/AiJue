# 规划：V4 语音与剪辑重构（Breeze TTS 2 + OpenFilm 式时间线）

> 背景：V3 已交付 16 模板全 H3 demo（`docs/V3_REPORT.md`），但**语音仍有明显问题**：
> 1) edge-tts 音色偏机械、情绪/语气固定，三角色区分度有限；
> 2) 为把台词塞进固定 5.17s 镜头，TTS 被 atempo 加速 **1.2–1.45×**，长句还会被截断（旧 plan 已修部分，但加速仍在）；
> 3) 镜头时长由模板固定，而非由语音自然时长决定 → 口型/语速/节奏不自然；
> 4) 剪辑层较粗：直接 xfade 拼接 + 单条旁白轨，无 J/L cut、无 BGM ducking、无分层音频时间线。
>
> 目标：用 **Breeze TTS 2**（[官方 PyTorch 推理](https://github.com/breezeblue-ai/breeze-tts)，[权重](https://huggingface.co/BreezeBlue/breeze-tts-2)）做角色化语音；剪辑参考 **OpenFilm**（[openfilm/openfilm](https://github.com/openfilm/openfilm)：film.html + tracks + clips + editor 的声明式时间线思路），把成片组装升级为**时间线驱动**的编辑。

## 1. 调研结论（2026-10-26）

### Breeze TTS 2（官方开源权重 + PyTorch 推理）
- 卖点：Artificial Analysis 开源 TTS 排名第一梯队；**Voice Design**（自然语言描述音色，无需参考音频）、Voice Clone、Voice Direction；中英双语；支持语气标记（中文用 `[笑] [叹气] [咳嗽] [清嗓子]`）；24 kHz 单声道输出。
- 部署：`requirements.txt` = torch 2.9.1 / torchaudio 2.9.1 / `qwen-tts==0.1.1` / transformers 4.57.3 / soundfile / fastapi；权重 ~7.0GB（2×safetensors）+ audio_tokenizer 682MB；**eager 推理约 7.7GB 显存（12GB 显卡最低推荐）**——本机 RTX 3080 Ti 12GB 满足，但需与 ComfyUI/H3 错峰（先 TTS 后 H3，或调 ComfyUI `/free` 卸载）。
- 服务化：`python -m breeze_infer.api <model> --host 127.0.0.1 --port 7861`，提供 `/health`、`/v1/audio/speech`（流式 PCM）、**`/v1/audio/speech/batch`**（最多 128 条文本一次合成，返回拼接 PCM + `X-Segment-Bytes` 可切分）——非常适合“每模板 20-60 条台词”的批量合成。
- 许可：代码 Apache-2.0；**模型权重与自托管输出为 research / non-commercial**（本仓库为 demo/研究用途，需在文档注明，商用需走 BreezeBlue 付费平台）。
- 已落地：`local_models/breeze-tts/`（官方代码 clone）、`local_models/breeze-tts-2/`（权重）、`local_models/breeze-venv/`（独立 Python 3.11 venv，torch cu128）。

### OpenFilm（剪辑参考）
- 声明式影片：一个 `film.html`，每个 `<section>` 是一条轨道，clip = 页面/视频/图片/声音 + `#t=in,out` 媒体片段 + `at` + CSS 布局；`frame(t)` 函数逐时刻绘制；Studio 浏览器编辑器可改任意元素；`openfilm render` 出 MP4（Node 22+/Chromium）。
- 对 V4 的启发：**成片 = 可检查/可编辑的时间线**（轨/片段/in-out/at/音量/字幕），而不是一次性 ffmpeg 命令；剪辑动作（trim/cut/J-L cut/ducking）应可追溯、可回放。
- 本机 Node 24 + npm 可用，可在 Phase B 用 `npx -y openfilm@latest` 做小样对照；但 H3 片段是 H.264 mp4（非网页），完全迁移到 OpenFilm 渲染有编解码/性能风险，因此 V4 采用 **OpenFilm 式时间线（JSON + ffmpeg 渲染器）**，OpenFilm 作为参考与可选实验路径。

## 2. 设计：V4 语音与剪辑

### 2.1 语音（`pipeline/tts_breeze.py` + `pipeline/tts_engine.py`）
- Provider 抽象：`TTS_PROVIDER=auto|breeze|edge`；`auto` = Breeze 服务健康则用 Breeze，否则 edge-tts；失败自动回退并记录 `tts_provider`。
- 角色音色（Voice Design，中文 instruction，与台词同语言）：
  - laozhou（host）：42 岁男性深夜秀主持，低频磁性，语速偏慢，冷幽默松弛尾音；
  - ali：24 岁女性嘉宾，明亮清脆，语速略快，俏皮外放；
  - dawei：52 岁男性嘉宾，沉稳略沙哑，语速中慢，保守严肃；
  - 每个角色固定 `seed`（角色级），同角色跨镜音色稳定；`cfg_scale=4` 强化指令遵循。
- 语气标记：从台词标点/情绪词自动注入 `[笑]`/`[叹气]`（保守规则，默认关闭，配置开启）。
- 文本规范化：数字/英文缩写/省略号处理；保留中文标点（模型双语）。
- 输出：24kHz mono PCM → 转 48k stereo wav；写入 `work/tts/{key}.wav` + meta（provider/voice/instruction/seed/duration/chars）。
- 服务生命周期：`scripts/tts_server.py`（start/stop/status/health，subprocess 用 breeze-venv python 起 API，日志 `local_models/logs/tts_server.log`）；render_v3 检测端口 7861 `/health`，未启动则自动拉起（首次加载 ~60-90s），失败回退 edge。

### 2.2 语音驱动的镜头时长（核心修复）
- **先 TTS 后分镜时长**：对所有有台词镜头先自然语速合成；每镜目标时长 = `snap_up_h3(tts_dur + lead 0.10s + tail 0.30s)`，H3 合法长度 = `17k+5` 帧 @24fps（124/141/158/…/362 帧 = 5.17–15.08s，训练范围）。
- 若台词 >14.3s（362 帧容量）：按标点切分为多镜（复用/扩展 storyboard 的切句器），逐镜分配。
- 无台词镜保持 5.17s（或按模板 h3.duration）。
- **彻底移除 atempo 加速**（`_atempo_for` 只对 edge 回退路径保留），成片音轨 = 每镜自然语速 voice + 静音补齐；H3 生成长度与音频严格一致（口型自然）。
- 总时长随台词自然伸缩：demo 目标 30-45s 允许 ±20%，QC 以 plan 实际时长为准；5 分钟版同样自适应（镜头数可能略增/减）。

### 2.3 剪辑（`pipeline/timeline.py` + `pipeline/edit_v4.py`，OpenFilm 式）
- `timeline.json`（落 `outputs/templates_v3/{id}/timeline.json`）：
  ```json
  {"version":4,"fps":25,"resolution":[1280,720],"duration":40.7,
   "tracks":[
     {"id":"V1","kind":"video","clips":[{"src":"shots/v3_s001.mp4","in":0.0,"out":5.4,"at":0.0}]},
     {"id":"V2","kind":"video","clips":[…卡片/花字/叠加…]},
     {"id":"A1","kind":"voice","clips":[{"src":"audio/voice_s001.wav","in":0.08,"out":…,"at":0.0,"gain":1.0}]},
     {"id":"A2","kind":"music","clips":[{"src":"bgm/soft_pad.wav","at":0.0,"out":…,"gain":0.22,"duck":-14}]},
     {"id":"A3","kind":"sfx","clips":[…转场 whoosh…]},
     {"id":"S1","kind":"subtitle","clips":[{"text":"…","at":…,"out":…,"style":"variety"}]}
   ]}
  ```
- `edit_v4.render(timeline)`：单次 ffmpeg filter_complex：
  - 视频：按 in/out trim + `at` 放置，转场（硬切/fade/crossfade）按 timeline；
  - 语音：`adelay` 放到绝对时间（支持 **J-cut：voice.at = shot_start - 0.12s**），fade in/out 去爆音；
  - BGM：**sidechaincompress ducking**（voice 触发，-12~-16dB）+ 淡入淡出；
  - SFX 轨：转场 whoosh/ding 按 `at`；
  - 字幕：timeline 直接给绝对时间 → ass_builder 生成 ASS（自然语速后重新计时）；
  - 响度：**两遍 loudnorm**（第一遍测量，第二遍应用）→ -16 LUFS / TP -1.5；
  - 音轨 48kHz stereo，AAC 192k；视频 1280×720/25fps。
- 附加值：`timeline.json` 可人工调整（拖动/裁剪/J-cut/ducking 参数）后直接 `python -m pipeline.edit_v4 --timeline …` 重渲染，符合 OpenFilm“可编辑成片”的理念；同时输出 `timeline.html`（只读预览表）便于人审。
- 兼容：保留 V3 `render_v3 --h3 comfy --legacy-audio` 开关回退旧路径（对比 A/B）。

### 2.4 QC 增量（`pipeline/qc_v3.py`）
- `audio_pace`：所有对话镜 `atempo==1.0`、`truncated==[]`、`voice_dur ≤ shot_dur - tail`；
- `voice_consistency`：同角色跨镜 provider/voice_id/seed 一致（meta 记录）；
- `timeline`：timeline.json 存在、轨道覆盖所有镜头、总时长差 ≤0.05s；
- `loudness`：成片 LUFS 在 -17~-15（±2）区间；
- `mix`：BGM ducking 生效（对话段 BGM 相对无对话段 -8dB 以上）。
- 保留 V3 全部硬门（H3 覆盖/证据链/黑帧/字幕/静态标记）。

## 3. 实施阶段

| 阶段 | 内容 | 验收 |
|---|---|---|
| V4.0 | 环境：breeze-tts 代码/权重/venv/api server 落地；单条中文合成成功（24kHz wav，3 角色 instruction 区分度试听） | `/health` ok + 3 段 wav + 时长/音色记录 |
| V4.1 | `tts_breeze.py` + provider 抽象 + 角色音色 + 批量合成（batch API）；edge 回退单测 | 单测绿；同角色跨 3 句音色一致 |
| V4.2 | 语音驱动镜头时长：storyboard/render_v3 改为先 TTS 再定镜长；H3 长度 snap；移除 atempo；QC audio_pace | tonight_show 40s：atempo 全 1.0、truncated=[]、H3 镜长=语音时长+tail |
| V4.3 | timeline.json + edit_v4 渲染器（J-cut/ducking/loudnorm 两遍/字幕绝对时间）；`timeline.html` | tonight_show 40s 成片：LUFS 达标、ducking 验证、timeline 全覆盖 |
| V4.4 | A/B 对照 + 重渲：tonight_show 40s、history_talk 40s（新音色/新剪辑）；QA 验收 + 用户试听 | QC 全绿 + 试听通过 |
| V4.5 | 批量：16 模板 30s + 5min 版按新链路重渲（GPU 串行 ~3h） | 16/16 验收 + 5min 抽检 |

## 4. 风险与对策
- **Windows 兼容**：官方推荐 Linux；本机无 flash_attn（代码仅在 flash_attention_2 模式 import），eager + `attn_implementation="eager"` 应可用；CUDA Graphs（--fast-*）先禁用。若 Windows 报错，降级用 WSL2 起 TTS 服务（HTTP 接口不变）。
- **12GB 显存**：TTS 7.7GB 与 H3 11GB 不能共存 → 严格错峰：TTS 阶段先调 ComfyUI `/free` 卸载模型（或 TTS 服务常驻但 H3 前显式 free）；TTS server 常驻不占显存时（模型未加载？）需实测，必要时每模板启停。
- **生成延迟/稳定性**：batch API 单次最多 128 条、`max_new_tokens=1500`；按角色分组 3 次请求；失败回退 edge-tts 并 QC 标记 `tts_provider=edge`。
- **音色一致性**：Voice Design 每次生成可能有细微漂移 → 同角色固定 instruction+seed；实测跨镜音色；必要时改用 Voice Clone（用首条生成作为 ref 音频+文本克隆后续，最稳）。
- **许可**：权重/自托管输出非商用，文档标注；demo 可用。
- **时长不可控**：语音自然时长变化导致 demo 时长浮动 → 验收口径改为 “30-45s ±20%” 并在 plan/manifest 记录实际时长。

## 5. 验收口径（V4）
1. 三角色音色可辨（试听 + 语谱/基频统计辅助）；同角色跨镜音色一致；
2. 所有对话镜 `atempo=1.0`、无截断；镜头时长 = 语音时长 + lead/tail（±0.1s）；
3. 成片：1280×720/25fps/音轨、blackdetect=0、字幕覆盖 1.0、LUFS -17~-15、BGM ducking 生效；
4. H3 证据链不回退（每镜 workflow/prompt_id/seed 齐全）；
5. 交付：tonight_show / history_talk 40s（V4 版）+ 16 模板批量（若时间允许）+ timeline.json/timeline.html。
