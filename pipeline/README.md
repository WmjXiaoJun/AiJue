# 造片局 · 视频流水线（pipeline/）

mock 画面生成 + edge-tts 中文旁白 + ffmpeg 合成的「5 分钟 AI 微综艺」成片流水线。
实现 `contract/PIPELINE_CLI.md` 的 gen-shot / tts / render 三个子命令，
默认全程离线（TTS 除外）、不依赖 GPU / ComfyUI。

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
`--limit N`（只渲染前 N 个镜头，调试用）、`--force`（忽略缓存）。

环境变量：`FFMPEG_BIN` / `FFPROBE_BIN` 覆盖二进制路径（默认 tools/node_modules 内置版）；
`PIPELINE_SCRATCH_DIR` 覆盖 gen-shot 的临时目录（默认系统临时目录，用于字体与 textfile）。

## 产物

```
outputs/demo/
├─ final.mp4            # 成片 1280x720 / 25fps / H.264 + AAC，带字幕/花字/人名条/BGM（loudnorm -16 LUFS）
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
   mock 生成按 Shot.type 用 ffmpeg lavfi + drawtext 绘制差异化画面：
   - `title_card` 暖黄径向渐变 + 大字标题 + 出品/期数角标
   - `host_to_camera` 暖黄灯光 + 主持人名条 + 大号水印名
   - `shot_reverse` 蓝/红左右渐变 + 说话人高亮 + 双方标签
   - `reaction` 暗红聚光特写 + REACTION 标签 + 说话人小标
   - `b_roll` 冷蓝科技网格 + 扫描线 + 时间码 + 场景说明
   - `meme_card` 黑底 + 花字大字（punchline）
   - `end_card` 品牌收尾 + 期数
   - `guest_intro` 单人介绍（兜底）
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

## 缓存与复用

- **平台生成台闭环**：若 `<workdir>/shots/<sid>.mp4` 存在且 ffprobe 校验通过（1280x720 / 25fps / h264 / 时长误差 ≤0.2s），
  直接复制进 `clips/`（日志打印「复用平台片段」），**不再 mock 生成**；即「生成台选版本 → 成片房渲染」闭环。
  参数不一致时先标准化（scale/pad/fps/tpad 补帧 + 裁切）复用，仍失败才回退 mock 生成。
- 镜头片段缓存：`clips/sXXX.meta.json` 记录「shot JSON + 分辨率/fps + 视觉版本 + **平台片段指纹(大小+mtime)**」哈希，
  命中则跳过；生成台重新生成 `shots/<sid>.mp4` 后指纹变化，**clips 旧缓存自动失效并改用新版本**。
  JSON 摘要里的 `clips_from_shots` 表示本次直接复用平台版本的镜头数。
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

产物实测：299.000s / 1280x720 / 25fps / 15.7MB / 440kbps / 7475 帧。
远低于「整片 ≤15 分钟」要求。

## 测试

```powershell
python -m pytest pipeline/tests -q
```

覆盖：ASS 时间/颜色/事件生成、mock 片段 filter 构建（8 种镜头类型）、TTS vtt 格式、
ComfyUI 适配器（httpx 用 monkeypatch 模拟，不联网）、平台生成台片段校验与复用、标准化失败回退。

## 依赖

| 依赖 | 说明 |
|---|---|
| Python 3.11 | 标准库 + `edge-tts>=7`；`httpx` 仅 comfyui 模式需要 |
| ffmpeg 6.1.1 | 优先 `tools/node_modules/ffmpeg-static/ffmpeg.exe`，其次 `FFMPEG_BIN` / PATH；需 libx264、libass、freetype、lavfi |
| ffprobe | `tools/node_modules/ffprobe-static/.../ffprobe.exe` 或 `FFPROBE_BIN` |
| 中文字体 | Windows `Deng.ttf`（正文）+ `msyhbd.ttc`（标题/花字），render 时复制到 workdir/fonts |
| 网络 | 仅 edge-tts 需要；其余全程离线 |

不安装任何重量级依赖（无 torch/CUDA/ComfyUI 硬依赖）。

## 已知限制 / 未做

- **mock 画面是示意**：渐变 + 文字 + 简单动效，不是真实演播厅/人物素材；接 H3 真视频需 ComfyUI 环境
  （`comfyui` 分支已实现协议，demo 未联网实测）。
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
