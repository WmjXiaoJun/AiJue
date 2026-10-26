# V4 交付报告：语音重构（Breeze TTS 2）+ OpenFilm 式时间线剪辑

> 规划：`docs/PLAN_V4_AUDIO_EDIT.md`；上一版：`docs/V3_REPORT.md` / `docs/V3_ACCEPTANCE.md`。
> 目标：修复 V3 语音问题（edge-tts 机械音、atempo 1.2-1.45× 加速、长句截断、BGM 无 ducking），
> 并参考 OpenFilm（film.html/tracks/clips 的声明式剪辑）升级剪辑层。

## 0. 问题与修复对照

| V3 问题 | V4 修复 |
|---|---|
| edge-tts 音色机械、三角色区分弱 | Breeze TTS 2（官方 PyTorch 权重）Voice Design：三角色独立音色描述 + 固定 seed（基频实测 laozhou 82Hz / ali 164Hz / dawei 104Hz） |
| 台词被 atempo 1.2–1.45× 压缩进固定 5.17s 镜头 | **先配音、后定镜头时长**：镜头时长 = H3 网格向上取整(语音时长+0.10s lead+0.30s tail)，atempo 恒 1.0 |
| 长句超镜头被截断 | 超 14.68s 台词按标点自动切分多镜；自然语速不再截断 |
| BGM 固定音量、无 ducking | `final_encode(duck_bgm=True)`：BGM 经 sidechaincompress 由旁白触发自动压低 |
| 一次性 ffmpeg 拼接，剪辑不可检查 | `timeline.json` + `timeline.html`（V1/A1/A2/A3/S1 五轨）+ `pipeline/edit_v4.py` 独立时间线渲染器 |
| 字幕只按镜头窗口 | 字幕绝对时间来自语音时长，A1 轨与 S1 轨同源 |

## 1. 部署（本机）

- 代码：`local_models/breeze-tts`（官方 PyTorch 推理 clone）
- 权重：`local_models/breeze-tts-2`（7.0GB：2×safetensors + audio_tokenizer；HF/ModelScope `BreezeBlue/breeze-tts-2`）
- 运行环境：`local_models/breeze-venv`（Python 3.11 + torch 2.9.1+cu128 + transformers 4.57.3 + qwen-tts 0.1.1，独立 venv，不动主环境）
- 服务：`python scripts/tts_server.py start|status|stop|say`，API `http://127.0.0.1:7861`（24kHz mono，batch 端点）
- 显存调度：TTS（~7.7GB）与 H3（~11GB）分时复用；渲染流程在配音完成后自动 `stop_server`，渲染前调 ComfyUI `/free` 释放显存
- 许可：代码 Apache-2.0；**模型权重/自托管输出为 research / non-commercial**（demo 用途，商用需 BreezeBlue 授权）

## 2. 实测（待填最终数据）

### 2.1 语音

| 项 | V3（edge-tts） | V4（Breeze TTS 2） |
|---|---|---|
| 音色数 | 3（edge 预设，区分一般） | 3（Voice Design，基频 82/164/104 Hz，可辨） |
| atempo | 1.0–1.36（长句加速） | **1.0** |
| 截断 | 旧版 s001 截断 ~1.3s | **0** |
| 生成速度 | 秒级 | batch ~1.5-3s/条（3090Ti 12GB，per-character batch） |
| 输出 | mp3 24k | 24k mono PCM → 48k stereo |

### 2.2 tonight_show 40s（V4，2026-10-26 09:14 完成）

| 项 | 结果 |
|---|---|
| 成片 | **43.28s** / 1280×720 / 25fps / 有音轨 / 9.3MB |
| 镜头 | 8 镜（6 H3 + 2 卡片）；语音驱动：s001 10.13s（台词 9.12s）、s002 5.17s、s003 6.58s、s004 5.17s、s005 5.88s、s006 5.88s |
| 语音 | provider=**breeze**、6/6 对话镜、**atempo 全 1.0**、truncated=[]、无切分 |
| H3 证据 | 6/6 h3=true（05_ref2va），prompt_id 唯一，黑帧 0，字幕覆盖 1.0 |
| 响度 | **-15.7 LUFS / TP -1.5 dBTP / LRA 17.3**（BGM sidechain ducking 已启用） |
| 时间线 | `timeline.json`（V1 8 / A1 6 / A2 1 / A3 6 / S1 6）+ `timeline.html` |
| QC | ok=true / deliverable=true / 10+audio_pace 全绿；耗时 986s（GPU 串行，长镜头占比高） |

### 2.3 history_talk 40s（V4，2026-10-26 09:32 完成）

| 项 | 结果 |
|---|---|
| 成片 | **39.54s** / 1280×720 / 25fps / 有音轨 / 11.8MB |
| 镜头 | 7 镜（5 H3 + 2 卡片），语音驱动自然时长 |
| 语音 | provider=**breeze**、5/5 对话镜、**atempo 1.0**、truncated=[] |
| H3 证据 | 5/5 h3=true（05_ref2va）、prompt_id 唯一、黑帧 0、字幕覆盖 1.0 |
| 响度 | **-16.8 LUFS / TP -1.7 dBTP**（ducking 启用） |
| 时间线 | `timeline.json` + `timeline.html`（五轨） |
| QC | ok=true / deliverable=true / audio_pace 全绿；耗时 925s |

### 2.4 A/B 试听包（V3 edge+atempo vs V4 Breeze 自然语速）

- `tmp/v4_voice_ab/AB_round1.wav`：同文本 3 句 edge（前）→ 3 句 Breeze（后）
- `tmp/v4_voice_ab/AB_tonight_show_narration.wav`（85.2s）：V3 旁白 40.69s + 0.6s 间隔 + V4 旁白 43.29s
- `tmp/v4_voice_ab/AB_history_talk_narration.wav`（81.4s）：V3 旁白 40.69s + V4 旁白 39.54s
- 整片对照：`outputs/v3_legacy_audio/{id}/final_v3_edge.mp4` vs `outputs/templates_v3/{id}/final.mp4`
- 音色基频（中位估算）：Breeze laozhou 82Hz / ali 164Hz / dawei 104Hz；V3 edge 95/136/153Hz（dawei 反而偏高，角色区分弱）

### 2.5 响度修复（批量抽检发现：单遍 loudnorm 欠调）

批量 V4 首版（09:33 启动的进程加载了修复前代码）暴露出**单遍 loudnorm 在稀疏/音乐床内容上欠调**：

| 模板 | 修复前 LUFS | 修复后 LUFS | 方法 |
|---|---|---|---|
| classics | -18.15（越界） | **-16.30** / TP -1.47 | workdir 重建 + 两遍 loudnorm |
| hot_ones | -17.53（越界） | **-16.28** / TP -1.47 | 同上 |
| mystery | -17.13（越界） | **-16.09** / TP -1.50 | 同上 |
| tonight_show / history_talk / history_mystery / masked_singer | 已达标 | -15.73 / -16.77 / -15.81 / 范围内 | — |

修复链路（render-dev）：
1. `pipeline/render.py::final_encode` 改为**两遍 loudnorm**：先把最终混音（旁白+BGM ducking+SFX）渲成 f32 wav → `loudnorm print_format=json` 测量 → 以 measured_I/TP/LRA/thresh + target_offset + `linear=true` 应用；编码后再做 ≤2 轮**闭环校正**（只重编码音频，`-c:v copy`）。
2. `pipeline/qc_v3.py` 新增 `loudness` 硬门（-17~-15 LUFS、TP ≤ -1.0），qc_report 带 `loudness` check 与 stats。
3. `scripts/fix_loudness.py`：不占 GPU 修复已落盘成片（优先 workdir 重建、复用生产剪辑/字幕/包装/混音；无 workdir 时仅音频重封装），修复后全量重评 QC 并回写 qc_report，自动备份 `final.preloud.mp4`。
4. 批量结束后统一 `--root outputs/templates_v3 --fix` 全量收口（已达标自动 skip）。

## 2.6 V4 硬门（QA 独立验收口径）

- `duration`：与 plan 实际时长差 ≤0.6s（语音驱动后以 plan 为准）
- `audio_pace`：atempo 全 1.0、truncated=[]、语音+lead/tail 装进镜头
- `voice`：provider=breeze、voice_map 与配音文件齐全
- `timeline`：version=4、五轨 V1/A1/A2/A3/S1 覆盖、V1 时间连续、A1 对齐、A2 duck
- `loudness`：integrated -17~-15 LUFS、TP ≤ -1.0（QA 独立 ffmpeg 测量，与 qc_report 差 ≤0.6dB）
- `H3`：非卡片镜 100% h3=true、workflow 05、prompt_id 唯一、seed/ref_image/action 齐、fallbacks=[]
- `blackdetect`=0、字幕覆盖=1.0、1280×720/25fps/有音轨

`outputs/templates_v3/{id}/timeline.json`：

```json
{"version":4,"fps":25,"resolution":[1280,720],"duration":43.3,
 "tracks":[
   {"id":"V1","kind":"video","clips":[{"id":"v3_s001","src":"shots/v3_s001.mp4","in":0.0,"out":10.125,"at":0.0,"kind":"h3"}]},
   {"id":"A1","kind":"voice","clips":[{"id":"v_v3_s001","src":"work/audio/v3_s001.wav","at":0.0,"out":10.125,"speech_duration":9.12,"provider":"breeze"}]},
   {"id":"A2","kind":"music","clips":[{"src":"work/bgm/soft_pad.wav","at":0.0,"gain":0.22,"duck":{"enabled":true}}]},
   {"id":"A3","kind":"sfx","clips":[{"src":"work/sfx/whoosh.wav","at":...,"gain":0.85}]},
   {"id":"S1","kind":"subtitle","clips":[{"text":"...","at":0.1,"out":9.5}]}
 ]}
```

- 预览：`timeline.html`（浏览器打开可读的轨道/片段表）
- 重渲染：`python -m pipeline.edit_v4 --timeline <timeline.json> --out <mp4>`（非破坏；可手工改 in/out/at/gain/duck 后重渲）
- 与 OpenFilm 的差异：OpenFilm 以 `film.html`（网页 + frame(t)）为影片源；本仓库成片源是 H3 mp4 片段，因此 timeline 用 JSON 描述媒体片段，ffmpeg 渲染器执行同一套轨道模型。

## 4. 代码清单

| 文件 | 内容 |
|---|---|
| `pipeline/tts_breeze.py` | Breeze TTS 2 客户端：health/start/stop、Voice Design 三角色、batch 合成、静音/失败回退、ComfyUI /free |
| `scripts/tts_server.py` | 服务管理 CLI（status/start/stop/say） |
| `pipeline/voiced.py` | 语音驱动镜头时长：H3 17k+5 网格、LEAD/TAIL、长句切分、超目标裁镜、每镜音轨 |
| `pipeline/timeline.py` | OpenFilm 式时间线构建/落盘/HTML 预览 |
| `pipeline/edit_v4.py` | 时间线 ffmpeg 渲染器（A1/A2/A3 混音、ducking、字幕） |
| `pipeline/render_v3.py` | V4 路径接入（默认 V4，`--legacy-audio` 回退 V3 对照）、ducking、timeline 产物 |
| `pipeline/qc_v3.py` | 新增 `audio_pace` 硬门（atempo=1.0/无截断/语音装进镜头） |
| `pipeline/h3_comfy.py` / `integrations/comfyui/run_h3_ref.py` | 显式 17k+5 帧数（`--length`），适配可变镜头时长 |
| `pipeline/render.py` | `final_encode(duck_bgm=True)` BGM sidechain ducking |
| `pipeline/tests/test_v4_audio.py` | 8 项单测（H3 网格/切分/配速/时间线/ASS） |

## 5. 复现命令

```powershell
# 1) 启动 Breeze TTS 2 服务（首次加载 ~1-2 分钟）
python scripts/tts_server.py start

# 2) V4 渲染（默认 Breeze 语音 + 自然语速 + timeline）
python -m pipeline.cli render_v3 --template tonight_show --duration 40 --h3 comfy --force

# 3) A/B 对照：回退 V3 edge-tts+atempo 路径
python -m pipeline.cli render_v3 --template tonight_show --duration 40 --h3 comfy --legacy-audio --force

# 4) 时间线重渲染（非破坏）
python -m pipeline.edit_v4 --timeline outputs/templates_v3/tonight_show/timeline.json --out tmp/retimed.mp4

# 5) 单测
python -m pytest pipeline/tests/test_v4_audio.py -q
```

## 6. 已知限制 / 后续

1. Breeze TTS 2 权重为**非商用许可**；正式商用需采购 BreezeBlue 平台授权，或换商用 TTS。
2. 首版三角色使用 Voice Design（无参考音频），音色跨镜一致性靠固定 instruction+seed；如需更稳克隆音色，可用「首条生成音频 + 逐字文本」走 Voice Clone 路径。
3. 自然语速使镜头变长（单镜 5.2–10.1s），H3 生成成本约 +20-30%；demo 总时长随台词自然伸缩，V4 按目标时长自动裁掉尾部多余 H3 镜。
4. 时间线渲染器（`edit_v4`）与主渲染在字幕样式上略有差异（主路径用 ass_builder 综艺包装，edit_v4 用简洁字幕样式）；主交付以 render_v3 成片为准。
5. OpenFilm 本体（Node/Chromium 渲染网页影片）未接入本仓库成片链路，仅借鉴其“轨道/片段/媒体片段/at”的时间线模型；如需真正用 OpenFilm Studio 编辑，可将 timeline 作为导出中间态再做映射。
