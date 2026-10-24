# 造片局 · 视频流水线（pipeline/）

**角色画面** 生成 + edge-tts 中文旁白 + ffmpeg 合成的「5 分钟 AI 微综艺」成片流水线。
实现 `contract/PIPELINE_CLI.md` 的 gen-shot / tts / render 三个子命令；
镜头说话人对应角色工坊素材（`outputs/characters/{id}/`）时自动合成原创动漫角色画面，
素材缺失时回退文字卡视觉。默认全程离线（TTS 除外）、不依赖 GPU / ComfyUI。

## 快速开始

在仓库根目录（Windows PowerShell）：

```powershell
# 1) 生成单镜头片段（mock）
python -m pipeline.cli gen-shot --spec shot_spec.json --out outputs/clip.mp4 --mock

# 2) 单条配音（edge-tts）+ 词级 vtt
python -m pipeline.cli tts --text "今天这期，我们聊一个得罪人的话题。" `
    --voice zh-CN-YunxiNeural --out tmp/a.mp3 --vtt tmp/a.vtt

# 3) 渲染整期（约 5 分钟成片）
python -m pipeline.cli render --episode contract/demo_episode.json `
    --workdir outputs/demo --out outputs/demo/final.mp4
```

可选参数：`--resolution 1280x720`、`--fps 25`、`--no-bgm`、
`--limit N`（只渲染前 N 个镜头，调试用）、`--force`（忽略缓存）、
`--use-h3`（尝试用 `outputs/h3/<sid>.mp4` 的真实 H3 动漫镜头替换对应镜头，默认关）。

环境变量：`FFMPEG_BIN` / `FFPROBE_BIN` 覆盖二进制路径（默认 tools/node_modules 内置版）；
`PIPELINE_SCRATCH_DIR` 覆盖 gen-shot 的临时目录（默认系统临时目录，用于字体与 textfile）；
`CHARACTERS_DIR` 覆盖角色素材根目录（默认 `outputs/characters/`）；
`H3_DIR` 覆盖 `--use-h3` 读取真实 H3 镜头的目录（默认 `outputs/h3/`，按 `<sid>.mp4` 匹配）。

## 产物

```
outputs/demo/
├─ final.mp4            # 成片 1280x720 / 25fps / H.264 + AAC：角色画面（H3/立绘）、字幕/花字/人名条、BGM（loudnorm -16 LUFS）
├─ manifest.json        # 本次渲染摘要（时长/大小/耗时/TTS 失败数/复用数）
├─ final.ass            # 本片字幕轨（样式模板见 pipeline/templates/）
├─ clips/sXXX.mp4       # 每镜头 mock 片段（可复用）
├─ tts/<hash>.mp3/.vtt  # 逐条旁白与词级时间戳（按 文本+音色 哈希缓存）
├─ audio/sXXX.wav       # 每镜头音轨（旁白 + 音效，精确对齐镜头时长）
├─ video_raw.mp4        # 拼接后的无音轨视频
├─ audio/narration.wav  # 拼接后的完整旁白
├─ bgm/soft_pad.wav     # ffmpeg 合成的柔和和弦垫（32s 循环）
└─ fonts/               # 复制自系统的 2 个中文字体（Deng.ttf / msyhbd.ttc）
```

stdout 协议：渲染过程中持续打印 `PROGRESS <0-100> <message>`，最后一行是 JSON 摘要
（`{"ok":true,"path","duration","shots","size_bytes",...}`）；失败时 exit code != 0
且最后一行 `{"ok":false,"error":...}`；日志全部走 stderr。

## 流水线步骤

1. **镜头片段**：来源优先级 = 平台生成台版本 `<workdir>/shots/<sid>.mp4`（ffprobe 校验通过则直接复用，
   参数不一致时自动缩放/补边/改帧率/裁齐到镜头时长后再用，失败才回退）→ `workdir/clips` 缓存 → mock 生成。
   mock 生成按 Shot.type 用 ffmpeg lavfi + 角色立绘/转面 + drawtext 合成差异化画面
   （**角色素材可用时优先角色画面**，缺失自动回退纯文字卡）：
   - `title_card` 角色蒙太奇背景（模糊+压暗）+ 大字标题 + 出品/期数角标
   - `host_to_camera` 节目暖黄背景 + 主持人立绘/转面面板 + 主持人名牌
   - `shot_reverse` 蓝/红节目背景 + 当前说话人立绘面板 + 发言状态
   - `reaction` 暗红聚光 + 表情特写面板（mood/台词选 neutral/happy/surprised/thinking）
   - `b_roll` 冷蓝科技网格 + 扫描线 + 时间码 + 主持人小窗
   - `meme_card` 黑底 + 花字大字（punchline）+ 主持人小窗
   - `end_card` 角色蒙太奇背景 + 品牌收尾 + 期数
   - `guest_intro` 单角色立绘 + 名字介绍（兜底）
2. **TTS**：edge-tts（zh-CN-YunxiNeural / XiaoxiaoNeural / YunjianNeural 按 asset.voice 映射），
   词级时间戳写 vtt；失败重试 3 次，仍失败则写 1s 静音并标记 `tts_failures`，不中断渲染。
3. **镜头音轨**：旁白不足镜头时长补静音、超出则 atempo 加速（上限 1.45x）对齐，
   前导 150ms；title/meme 等镜头叠加 whoosh/ding 合成音效。
4. **拼接**：concat demuxer + 统一 h264/yuv420p/25fps 参数，`-c copy` 秒级完成。
5. **字幕/花字**：全局 ASS（`pipeline/templates/subtitle_variety.ass` 定义样式，
   综艺风白字粗描边）：逐镜头旁白字幕、非 meme 镜头的 punchline 花字、lower_third 人名条、
   segment 章节小标。meme 镜头的 punchline 已在片段内绘制，避免重影。
6. **BGM**：ffmpeg aevalsrc 合成 C-Em-F-G 柔和和弦垫（32s）循环，音量 -22dB 左右，片头淡入/片尾淡出；
   `--no-bgm` 可关闭。
7. **最终编码**：一次 ffmpeg 完成 ASS 烧入 + 旁白/BGM 混音 + loudnorm（I=-16, TP=-1.5, LRA=11）
   + H.264 (veryfast/crf20) + AAC 192k + faststart。

## 角色画面接入（角色工坊）

角色素材来自两处（都可用环境变量覆盖根目录）：

```
outputs/concepts/{id}/            # SDXL 概念图（imagegen，CONCEPTS_DIR 可覆盖）
├─ portrait.png                   # 832x1216 竖构图概念立绘
├─ expressions/{neutral,happy,surprised,thinking}.png
├─ three_view/{front,side,three_quarter}.png / sheet.png / meta.json

outputs/characters/{id}/          # 角色工坊（studio3d/studio2d，CHARACTERS_DIR 可覆盖）
```

角色工坊素材目录明细（`CHARACTERS_DIR` 可覆盖，默认 `outputs/characters/`，由 studio3d 角色工坊产出）：

```
outputs/characters/{id}/
├─ cutout/portrait.png             # 3D 透明抠像立绘（首选，直接叠节目背景）
├─ cutout/expressions/*.png        # 透明表情（reaction 用）
├─ portrait.png                    # 3D 立绘（深色影棚背景，抠像缺失时用）
├─ expressions/{neutral,happy,surprised,thinking}.png
├─ h3_portrait.png                 # H3 真实动漫中段帧（说话镜头外的镜头优先用）
├─ turntable.mp4                   # 360° 转面（预留，带运动感）
├─ meta.json                       # 可选：name/title/accent_color/style
├─ 2d/portrait.png                 # 2D 立绘（3D 缺失时使用）
├─ 2d/expressions/*.png
└─ style_<name>/...                # 多风格输出（按 contract 的 style.primary 优选）
```

- **素材优先级**（contract/CONSISTENCY.md）：
  ① `outputs/h3/{id}_h3.mp4`（概念图驱动的 H3 视频）→
  ② `outputs/characters/{id}/h3_portrait.png`（H3 中段帧）→
  ③ **`outputs/concepts/{id}/portrait.png`（SDXL 概念立绘，832x1216 竖构图）** →
  ④ `outputs/characters/{id}/cutout/portrait.png`（Blender 透明抠像）→
  ⑤ `2d/portrait.png`（2D 平涂）→ ⑥ 文字卡兜底。
  表情/转面逐文件独立回退（例如 H3 中段帧缺失、概念图存在时直接用概念立绘）。
- **概念立绘构图**：832x1216 竖图按「顶部安全边 ≈2%」裁成方形胸像后放入右侧 1/3 画窗
  （节目背景 + 细描边 + Ken Burns 缓推），避免竖构图直接 cover 裁切把头部裁掉；
  reaction 镜头优先选用 `concepts/{id}/expressions/` 中按 mood/台词匹配的概念表情。
- **H3 概念图驱动（实测）**：三人的 H3 视频 / 中段帧均由 `outputs/concepts/{id}/portrait.png`
  （laozhou/ali/dawei）经 ComfyUI H3 生成后覆盖 `outputs/h3/` 与
  `outputs/characters/{id}/h3_portrait.png`；成片里 52 个角色镜头全部来自
  SDXL 概念角色（H3 视频 22 个说话镜头 + H3 中段帧 30 个其它镜头，另有片头/片尾/小窗使用 H3 中段帧）。
- **人名条不重复**：角色镜头的人名条由片段内绘制（跟随说话人），ASS 对 `char_shot_ids`
  不再输出 Lower 事件，避免与字幕/名字条叠字；旁白字幕基线保持 `\pos(640,655)`。
- **角色映射**：`shot.speaker`（host/guest_a/...）→ `episode.assets` 里的角色资产 →
  按 `character_id` / 资产 id / 角色名 依次匹配 `outputs/characters/{id}/`，无需改 episode.json。
- **镜头用法**：host_to_camera / reaction / shot_reverse / guest_intro 用对应角色立绘；
  无 speaker 的 reaction 按 visual/台词里出现的角色名推断，兜底主持人；
  title/end 用全阵容蒙太奇背景；meme/b_roll 加主持人小窗（b_roll 保留科技背景）。
- **统一胸像构图**：角色高度 ≈ 画面 91%、底部对齐、置于右侧 1/3、头顶留 9-12% 安全边，
  肩线完整；左下角名字条逐镜头按 `shot.speaker_name` 绑定（反打镜头显示当前说话人）。
- **无黑帧**：片段不再使用 fade-in/out（否则每个切点会黑 1 帧），
  验收：`ffmpeg -i final.mp4 -vf blackdetect=d=0.01:pix_th=0.10 -an -f null NUL` 应无输出。
- **reaction 表情**：`shot.mood` 命中直接用；否则按台词关键词（笑/惊/……/？）选
  happy / surprised / thinking，默认 neutral。
### H3 真实动漫镜头（自动优先）

ComfyUI MiniMax H3 生成的真实动漫镜头（由 SDXL 概念图驱动）会**自动优先于其它立绘**：

| 产物 | 路径 | 用途 |
|---|---|---|
| H3 视频 | `outputs/h3/{id}_h3.mp4`（也支持 `{id}.mp4` / `{id}*.mp4`） | host_to_camera 说话镜头：右侧 1/3「H3 画窗」+ 节目背景，按镜头时长裁剪/慢放（≤1.8x） |
| H3 中段帧 | `outputs/characters/{id}/h3_portrait.png`（也支持 `outputs/h3/{id}_h3_fmid.png`） | 其余角色镜头：画窗 + Ken Burns 缓推（1.0→1.12 慢推） |
| SDXL 概念立绘 | `outputs/concepts/{id}/portrait.png`（832x1216 竖图） | H3 素材缺失时：顶部安全边裁胸像 + 画窗 + Ken Burns |
| Blender 立绘 | `outputs/characters/{id}/cutout/portrait.png` / `portrait.png` | 兜底 |

- H3 视频 768x768/24fps 直接全屏 cover 会把脸放大失真，因此采用「节目背景 + 右侧画窗」构图；
- `H3_DIR` 可覆盖 H3 素材目录；H3 文件变化会通过角色素材指纹自动失效镜头片段缓存。
- **缓存失效**：角色素材 + H3 素材的文件大小/mtime 参与 clips 缓存 key，重新生成后镜头片段自动失效重建。
- 日志/JSON：stderr 打印「角色画面镜头 N / 52」，JSON 摘要含 `characters`（发现角色数）与
  `character_shots`（使用角色画面的镜头数），可用于验收。

## 缓存与复用

- **平台生成台闭环**：若 `<workdir>/shots/<sid>.mp4` 存在且 ffprobe 校验通过（1280x720 / 25fps / h264 / 时长误差 ≤0.2s），
  直接复制进 `clips/`（日志打印「复用平台片段」），**不再 mock 生成**；即「生成台选版本 → 成片房渲染」闭环。
  参数不一致时先标准化（scale/pad/fps/tpad 补帧 + 裁切）复用，仍失败才回退 mock 生成。
- 镜头片段缓存：`clips/sXXX.meta.json` 记录「shot JSON + 分辨率/fps + 视觉版本 +
  **平台片段指纹(大小+mtime) + 角色素材指纹**」哈希，命中则跳过；生成台重新生成 `shots/<sid>.mp4`
  或角色工坊重新生成 `outputs/characters/{id}/` 后指纹变化，**clips 旧缓存自动失效并改用新素材**。
  JSON 摘要里的 `clips_from_shots` / `clips_from_h3` / `character_shots` 分别表示
  平台版本复用数 / H3 真镜头数 / 角色画面镜头数。
- TTS：按 `文本+音色+语速` 哈希缓存 mp3/vtt，重复台词只合成一次。
- 镜头音轨：按 TTS 结果 + 时长 + 语速补偿 + 音效 哈希缓存。
- 修改 `pipeline/mock_shot.py` 的视觉后请递增 `render.CLIP_VERSION`，让旧片段失效。
- 全流程重复执行时，只有最终编码与拼接会重新跑（约 1-2 分钟）。

## 耗时（实测，16 核 / 24 线程，Windows 11，52 镜头 / 299s 成片）

| 场景 | 实测耗时 | 说明 |
|---|---|---|
| 单镜头 gen-shot | 0.3-0.9s | 100% 新生成 |
| 单条 tts | 约 2.4s（网络） | 词级 vtt 一并产出 |
| **整片 render 冷启动** | **70.8s** | 52 片段 + 41 条 TTS + 音轨 + 编码 |
| **整片 render 全缓存复用** | **46.9s** | 只剩 concat + 最终编码 |
| **平台片段复用（52 镜头，生成台已有 shots/）** | **39.2s** | 52 片段直接 copy（不 mock）+ 缓存 TTS + 音轨 + 编码 |
| --limit 3 平台片段复用 | 7.5s | 3 片段 copy + 2 条 TTS + 编码 |
| --limit 2 调试渲染 | 4.5s | 含 2 片段 + 1 条 TTS |
| **角色版整片（H3 + 角色立绘 + Blender 兜底，52 镜头）** | **54-61s** | 52 角色画面 + 缓存音轨 + 编码 |
| **SDL 概念角色版整片（H3 概念图驱动，52 镜头全新生成）** | **50.9s** | 52 角色画面 + 缓存音轨 + BGM + 编码 |
| 角色版整片（全缓存复用） | 34.1s | 只剩 concat + 编码 |

产物实测（SDXL 概念角色版）：299.000s / 1280x720 / 25fps / 34,719,840 B（33.1 MiB）/
7475 帧 / 角色画面镜头 52/52 / H3 概念图驱动 3/3（laozhou/ali/dawei 源=`outputs/concepts/{id}/portrait.png`）/
blackdetect(d=0.01) = 0 条。远低于「整片 ≤15 分钟」要求。

## 测试

```powershell
python -m pytest pipeline/tests -q
```

覆盖：ASS 时间/颜色/事件生成、mock 片段 filter 构建（8 种镜头类型）、TTS vtt 格式、
ComfyUI 适配器（httpx 用 monkeypatch 模拟，不联网）、平台生成台片段校验与复用、标准化失败回退、
角色素材发现/3D→2D 优先级/角色映射/表情选择/角色素材指纹缓存失效、
SDXL 概念图发现与第 3 优先级/竖构图顶部安全边裁切/概念表情/概念素材指纹失效、
多语种本地化（翻译/人名罗马化/术语固定/目标语 ASS/时长拟合/voice 选择/本地化 clips 无中文）。

## 依赖

| 依赖 | 说明 |
|---|---|
| Python 3.11 | 标准库 + `edge-tts>=7`；`httpx` 仅 comfyui 模式需要 |
| ffmpeg 6.1.1 | 优先 `tools/node_modules/ffmpeg-static/ffmpeg.exe`，其次 `FFMPEG_BIN` / PATH；需 libx264、libass、freetype、lavfi |
| ffprobe | `tools/node_modules/ffprobe-static/.../ffprobe.exe` 或 `FFPROBE_BIN` |
| 中文字体 | Windows `Deng.ttf`（正文）+ `msyhbd.ttc`（标题/花字），render 时复制到 workdir/fonts |
| 角色素材（可选） | `outputs/h3/` + `outputs/characters/{id}/` + `outputs/concepts/{id}/`；缺失时自动回退文字卡 |
| 剪映草稿导出 | `pyJianYingDraft==0.3.1`（pip 安装，用于生成剪映/CapCut 草稿 JSON）；未装则 capcut 导出会提示安装 |
| 网络 | 仅 edge-tts 需要；其余全程离线 |

不安装任何重量级依赖（无 torch/CUDA/ComfyUI 硬依赖）。

## 剪映草稿导出（pipeline.capcut）

把成片时间线导出为剪映专业版草稿（基于 pyJianYingDraft 0.3.1）：

```powershell
# 1) 从 episode + 渲染工作目录自动生成 manifest
python -m pipeline.capcut build-manifest --episode contract/demo_episode.json `
    --workdir outputs/demo --out outputs/exports/export_manifest.json

# 2) 导出剪映草稿（--copy-media 把媒体复制进草稿 materials/）
python -m pipeline.capcut export --manifest outputs/exports/export_manifest.json `
    --outdir outputs/exports --name 造片局_EP001 --copy-media

# 3) 校验草稿目录
python -m pipeline.capcut validate --draft-dir outputs/exports/造片局_EP001
```

- Manifest 结构见 `contract/EXPORT.md`；`build_manifest_from_workdir()`
  是给后端直接调用的 Python 辅助函数（也可先落 manifest 再调 CLI）。
- 产物：`draft_content.json`（画布/轨道/片段/素材，时间单位微秒）、`draft_meta_info.json`、
  `draft_settings`、`draft_virtual_store.json`、`timeline_layout.json`、
  `materials/`、`subtitles.srt`、`export_manifest.json`、`export_audit.json`、`README.txt`。
- 轨道：视频（镜头片段顺铺）/ 音频（旁白 narration.wav）/ 文本（字幕由 SRT 导入）。
- 导入剪映：把草稿文件夹复制到
  `%LOCALAPPDATA%\JianyingPro\User Data\Projects\com.lveditor.draft\` 后重启剪映，
  或用剪映「导入草稿」；`subtitles.srt` 也可直接给其它剪辑软件。
- 兼容性：本机剪映 10.3.0.13901 的草稿 JSON 是加密格式（现有真实草稿的
  `draft_content.json` 不是明文），本导出器生成的是明文草稿（pyJianYingDraft v5 风格），
  适合 ≤5.9 等支持明文草稿的版本；10.x 若无法直接识别，需用旧版本导入或用
  `materials/` + `subtitles.srt` 手动重建。
- 测试：`python -m pytest pipeline/tests/test_capcut.py -q`（manifest/SRT/草稿 JSON/轨道/素材路径/copy-media）。

## 多语种本地化（pipeline.localize）

同一部成片一键产出多语种配音 + 字幕版本（不改画面素材，只本地化文字包装）：

```powershell
python -m pipeline.localize --episode-json backend/data/jobs/<job>_episode.json `
    --lang en --outdir outputs/episodes/{eid}/localized/en `
    --translator ollama [--voices-json voices.json] [--force-translate]
```

- 翻译：默认 `ollama`（本机 qwen2.5:7b，约 5-9s/集），可 `--translator nllb`（ComfyUI venv）
  或 `mock`（测试）；人名先罗马化（Zhou/Ali/Dawei），品牌/片头/片尾/段落用
  `localize/terms.py` 固定映射，不交给模型；翻译失败回退原文并在 meta 标注。
- **目标语画面重生成（关键）**：不复用中文 clips（品牌角标/镜头标签/名字条/片头片尾会残留中文），
  而是用本地化后的 episode manifest 调 `mock_shot` 重新生成每条 clip（角色/H3/概念图素材不变，
  只替换包装文字），per-lang 缓存；`pipeline/mock_shot.py` 的文案支持 `locale` 覆盖（默认中文，向后兼容）。
- 配音：`localize/terms.py` 的 `VOICES` 多语种音色（en/es/ja/ko/pt，settings/`--voices-json` 可覆盖）；
  每镜 TTS 用 atempo(1.0~1.25) 拟合镜头时长，不足补静音，拼 narration.wav，时长与中文版一致（±0.2s）。
- 字幕/包装：目标语 ASS（字体/行宽按 `terms.FONTS` / `terms.WRAP_WIDTH`，自适应字号避免出画）
  + `subtitles.srt`；片头标题、片尾 CTA、名字条、镜头标签、段落标签全部译文。
- 产物：`final.mp4`（1280x720/25fps）+ `narration.wav` + `subtitles.srt` + `units.json` + `translations.json` + `meta.json`
  （meta 含 lang/时长/翻译模型/耗时/发布元数据 title/description/hashtags）。
- 实测：ep_002 en/es 75.95s（=中文版）、ep_001 en 299s；均 blackdetect=0、字幕/画面无中文、
  名字条/品牌/镜头标签全英文；单语种首次渲染约 25-50s（ep_002 21 镜）到 2 分钟级（ep_001 52 镜），
  全缓存复用约 8-25s。
- 测试：`python -m pytest pipeline/tests/test_localize.py -q`（翻译/术语/人名罗马化/目标语 ASS/
  时长拟合/voice 选择/本地化 manifest/重生成文案无中文/失败回退）。

## 已知限制 / 未做

- **画面 = 原创角色立绘 + 程序化演播厅合成**：角色来自角色工坊 `outputs/characters/` 的
  3D/2D 动漫立绘（Blender 卡通渲染/2D 平涂），不是真实拍摄素材；背景/花字/名条由 ffmpeg 程序化绘制。
  真实 H3 动漫镜头可选接入：`--use-h3` 会把 `outputs/h3/<sid>.mp4` 替换进对应镜头（默认关，
  demo 未联网实测）。
- **角色素材缺失时对应镜头回退文字卡**：渲染不中断，但该镜头不是角色画面；日志/JSON 的
  `character_shots` 可核查覆盖率（素材就绪时应为 52）。
- **2D/3D 混用规则固定为 3D 优先**：如需强制 2D，可把 `2d/` 内容放到角色根目录，
  或用 `CHARACTERS_DIR` 指向纯 2D 素材目录。
- **角色相关只读素材、不改角色工坊**：pipeline 只扫描 `outputs/characters/`，角色生成/编辑在 studio3d 与后端任务中完成。
- **TTS 依赖网络**：edge-tts 需访问微软服务；离线时成片无旁白（静音兜底），字幕仍在。
- **字体依赖 Windows**：默认取 `C:/Windows/Fonts/Deng.ttf` + `msyhbd.ttc`；
  其他平台需装字体并改 `config.FONT_CANDIDATES`。
- **转场为硬切**：未做 xfade/logo 动画；包装风格以字幕、花字、名条、BGM 为主。
- **字幕样式为固定模板**：逐句字幕不做逐字卡拉 OK 效果（vtt 已产出词级时间戳，后续可接）。
- **平台片段只读 `<workdir>/shots/<sid>.mp4` 这一约定路径**：即后端「生成台」写片段的目录。
  其它来源（任意 ShotVersion.video_url、ComfyUI 原始产物、非 h264）需先落到该路径或扩展 `render.ensure_clip()`；
  h264 且参数一致的片段直接 copy 进 concat，参数不一致会先标准化（多一次重编码）。
- **并发写入**：同一 workdir 不可并行 render；平台片段在 render 进行中被替换/删除可能造成该镜头回退 mock，建议生成台与成片房串行。
- **单机单进程**：任务并发由调用方（后端）控制；同一 workdir 不可并行渲染。
- **磁盘占用**：workdir 含片段/音频/字体，52 镜头约 150MB（其中字体仅 Deng.ttf + msyhbd.ttc ≈ 32MB，
  旧的 5 字体缓存会在下次 render 时自动清理）；gen-shot 的字体缓存放在系统临时目录
  （`%TEMP%/zaopianju_pipeline/gen_shot/fonts`，同样只 2 个字体），不再写到输出目录。
