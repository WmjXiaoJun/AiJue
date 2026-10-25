# integrations/comfyui —— MiniMax H3 本机真实生成接入

把本机 ComfyUI 里的 **MiniMax H3（int8 + 4step turbo LoRA）** 接成「造片局」可脚本化调用的
图生视频（i2v / r2v）能力：一条命令完成 **启动服务 → UI 工作流转 API prompt → 传入首帧 →
提交生成 → 轮询 → 取回 mp4 → ffprobe 校验 → 抽帧 → JSON 摘要**。

> 只依赖本机 ComfyUI 与 Python 标准库，不联网、不需要任何 API key。
> 本目录脚本不修改 ComfyUI 仓库代码，只向 `ComfyUI/input/zaopianju/` 写首帧、
> 从 `ComfyUI/output/zaopianju/` 取产物（均为 ComfyUI 自身的数据目录）。

## 0. 本机实测结论（2026-10-24，RTX 3080 Ti 12GB）

| 项目 | 结果 |
|---|---|
| 样片 | `outputs/h3/test_001.mp4`：**768×768 / 24fps / 124 帧 / 5.17s / H.264 + AAC 32kHz 立体声 / 0.62MB** |
| 端到端耗时（首次，含模型加载） | **2m05s**（提交→完成 124.7s） |
| 采样 | 4 步（Euler + simple + 4step LoRA），~51s；首步含 25s 模型初始化 |
| 显存 | 峰值 **11.90GB / 12GB**（4 条全程 nvidia-smi 采样，avg GPU util 77.8%；dynamic VRAM offload，NORMAL_VRAM + async weight offloading） |
| GPU 利用率 | 采样期间 ~80–100% |
| 模型加载 | 每个 prompt 都会重新 staged/offload 一次（日志 `prepared for dynamic VRAM loading`），模型准备 ~25–40s（int8 diffusion 20GB + Qwen3-VL 15GB + VAE 5GB，BaiduSyncdisk 盘）；单条约 2–2.5 分钟 |
| ffprobe 校验 | duration=5.167s, nb_frames=124, fps=24/1, h264 yuv420p, aac 32000Hz 2ch ✅ |

样片画面：输入是 SDXL 立方体静帧，H3 真实生成了缓慢推镜 + 光尘运动的视频（`test_001_fmid.png` 与首帧透视已不同）。首次用 `tmp/test_input.png` 跑通的记录：`outputs/h3/test_001.mp4` + `test_001.meta.json`。

### 角色镜头样片（2026-10-24 00:32–00:38，参数完全一致：768×768 / 4step / length=124 / seed=20261024 / 24fps）

| 角色 | 视频 | 首帧（角色立绘） | 中段帧（角色卡） | 文件大小 | 提交→完成 |
|---|---|---|---|---|---|
| 老周 laozhou | `outputs/h3/laozhou_h3.mp4` | `outputs/characters/laozhou/portrait.png` | `outputs/characters/laozhou/h3_portrait.png` | 1.09MB | 149s |
| 阿栗 ali | `outputs/h3/ali_h3.mp4` | `outputs/characters/ali/portrait.png` | `outputs/characters/ali/h3_portrait.png` | 1.74MB | 199s（含排队 ~150s） |
| 大魏 dawei | `outputs/h3/dawei_h3.mp4` | `outputs/characters/dawei/portrait.png` | `outputs/characters/dawei/h3_portrait.png` | 0.99MB | 324s（含排队 ~2m45s） |

三条视频 ffprobe 全部通过：**5.167s / 768×768 / h264 yuv420p / 24fps / 124 帧 / AAC 32kHz 2ch**。
三条是**串行排队**跑的（ComfyUI 单 worker，12GB 不能并发），所以 `gen_sec` 里含排队等待；单独跑一条约 2–2.5 分钟。
中段帧（`*_fmid.png`，768×768）另存为 `outputs/characters/{id}/h3_portrait.png`，供平台角色卡 / 成片静帧直接使用。
（另有 `outputs/h3/laozhou_h3_rawtest.mp4`：用透明底 raw 立绘直接跑的对照版本，白底，仅作对照留档。）

画面预览：

![laozhou](../../outputs/h3/laozhou_h3_fmid.png)
![ali](../../outputs/h3/ali_h3_fmid.png)
![dawei](../../outputs/h3/dawei_h3_fmid.png)

## 1. 目录

```
integrations/comfyui/
├── run_h3.py               # ★ 主 CLI：确保服务并生成一条 i2v 视频
├── workflow_to_api.py      # ★ UI workflow JSON -> /prompt API JSON 转换器（可单独用）
├── start_comfyui.ps1       # 后台启动 ComfyUI（日志落 logs/，幂等）
├── stop_comfyui.ps1        # 按命令行精确停止 ComfyUI
├── prompts/                # 提示词样例（UTF-8 文本，--prompt-file 用）
├── api_workflows/          # 转换出的 API prompt JSON（每次生成自动保存，可复现）
├── logs/                   # ComfyUI 服务日志 + run_*.log + GPU 采样 csv
└── tools/                  # 开发期探针（workflow/schema 转储、SaveVideo 格式验证）
```

产物目录：`outputs/h3/` —— `<name>.mp4`、`<name>.meta.json`、`<name>_f000.png` / `_fmid.png` 抽帧。

## 2. 前置条件

- **ComfyUI**：`D:\BaiduSyncdisk\wmj_work\XJ\ComfyUI`，venv `.venv\Scripts\python.exe`，ComfyUI **0.39.0**，torch 2.11.0+cu130。
- **模型**（均已就位）：
  - `models/diffusion_models/minimax_h3_fl2va_pruned_int8_convrot.safetensors`（20.97GB int8）
  - `models/loras/minimax_h3_fl2v_turbo_4step_v1.0_768p_comfyui_bf16.safetensors`（4step turbo）
  - `models/text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors`
  - `models/vae/minimax_h3_video_vae_fp16.safetensors`、`models/vae/minimax_h3_audio_vae_fp32.safetensors`
- **ffmpeg/ffprobe**：默认用仓库内 `tools/node_modules/ffmpeg-static` 与 `tools/node_modules/ffprobe-static`，可用环境变量 `FFMPEG_BIN` / `FFPROBE_BIN` 覆盖。
- GPU：RTX 3080 Ti 12GB（12GB 可跑 768p，见 §9 限制）。

## 3. 启动 / 停止 ComfyUI

```powershell
# 方式 A：脚本（幂等，日志自动落 integrations/comfyui/logs/）
powershell -ExecutionPolicy Bypass -File integrations/comfyui/start_comfyui.ps1
powershell -ExecutionPolicy Bypass -File integrations/comfyui/stop_comfyui.ps1

# 方式 B：run_h3.py 自带自动拉起（服务不在线时）
python integrations/comfyui/run_h3.py ...   # 默认探测 http://127.0.0.1:8188 并按需启动
```

### ⚠️ 必踩坑：Windows 下重定向日志必须强制 UTF-8，否则 ComfyUI 启动即崩

自定义节点启动时会打印 emoji / 中文（如 🎉），当 stdout/stderr 被重定向到文件时，Python 3.13 默认使用系统 GBK 编码写 stderr，抛出：

```
UnicodeEncodeError: 'gbk' codec can't encode character '\U0001f389' ...
```

该异常发生在 logging 的 warning 路径里，**会直接把 ComfyUI 启动过程打崩（进程退出）**。所以任何后台启动/重定向都必须带：

```powershell
$env:PYTHONUTF8='1'; $env:PYTHONIOENCODING='utf-8'; $env:PYTHONUNBUFFERED='1'
```

`start_comfyui.ps1` 与 `run_h3.py` 均已内置。前台直接运行（不重定向）不会触发。
另注：ComfyUI 日志里的中文/进度条在日志文件中是 UTF-8，终端按 GBK 看会乱码，用 `Get-Content -Encoding utf8` 或编辑器打开即可。

## 4. 模型 / 工作流映射

默认工作流：`04_i2v_fl2v_4step_sla.json`（ComfyUI `user/default/workflows/` 下），它是「LoadImage → MiniMaxH3ImageToVideo(first_frame) → 4step 采样 → VAE 解码视频+音频 → MP4」的完整链路：

| 节点 id | 节点类型 | 关键输入（run_h3.py 会自动覆盖） |
|---|---|---|
| 1 | UNETLoader | `unet_name=minimax_h3_fl2va_pruned_int8_convrot.safetensors` |
| 2 | LoraLoaderModelOnly | `lora_name=...turbo_4step_v1.0_768p...`，strength 1.0 |
| 3 | MiniMaxH3SigmaShift | shift_video=6, shift_audio=3（Turbo 768p 官方参数） |
| 4 | CLIPLoader | Qwen3-VL 32B nvfp4，type=minimax |
| 5 / 6 | VAELoader | 视频 VAE fp16 / 音频 VAE fp32 |
| **7** | **MiniMaxH3ImageToVideo** | **prompt / width / height / length；first_frame ← 18** |
| 8 | BasicGuider | 无 CFG 的蒸馏引导（Guidance 1） |
| **9** | **RandomNoise** | **noise_seed**（固定 seed 可复现） |
| 10 / 11 | KSamplerSelect / BasicScheduler | euler / simple / steps=4 / denoise=1 |
| 12 | SamplerCustomAdvanced | 输出 LATENT（视频+音频联合） |
| 13 / 14 | VAEDecodeTiled / VAEDecodeAudio | 256/64/24/8 分块解码（12GB 关键） |
| 15 | CreateVideo | fps=24, 8bit, sRGB, codec=none |
| **16** | **SaveVideo** | **filename_prefix**（动态控件 format=mp4 / format.codec=h264） |
| **18** | **LoadImage** | **image=zaopianju/<首帧>.png**（相对 ComfyUI/input） |

其它可用工作流：

| 工作流 | 用途 | 差异 |
|---|---|---|
| `04_i2v_fl2v_4step_sla.json` | **i2v 图生视频（默认）** | LoadImage → `first_frame`（首帧） |
| `MiniMax-H3-Local-12GB.json` | t2v 文生视频 | first_frame/last_frame 不接线，纯文本生成 |
| `05_ref2va_4step_sla.json` | r2v 参考图生视频 | MiniMaxH3ReferenceToVideo + `ref_images.ref_image_0`，参考图影响人物身份/风格 |

> H3 节点 schema（`/object_info` 实测）：`MiniMaxH3ImageToVideo` required=`clip, vae, prompt, width, height, length`，optional=`first_frame, last_frame`；width/height step=32、默认 1344×768；`length` step=17、默认 124（≈5.17s@24fps，训练区间约 124–362）。`MiniMaxH3ReferenceToVideo` required 另含 `ref_image_size`（match/max），optional 含 `ref_images / ref_videos / ref_audios`（COMFY_AUTOGROW_V3，扁平键名 `ref_images.ref_image_0`）。
> 音频由模型原生生成（`VAEDecodeAudio` → `CreateVideo`），不是后配。

## 5. 快速开始（生成一条视频）

```powershell
# 一条 768p / 4step / 5.17s 的 i2v（服务没开会自动拉起）
python integrations/comfyui/run_h3.py ^
  --image tmp/test_input.png ^
  --prompt-file integrations/comfyui/prompts/test_001.txt ^
  --out outputs/h3/test_001.mp4 ^
  --width 768 --height 768 --length 124 --seed 20261024 --timeout 900
```

`run_h3.py` 参数：

| 参数 | 默认 | 说明 |
|---|---|---|
| `--image` | 必填 | 首帧图片（png/jpg），会自动拷到 `ComfyUI/input/zaopianju/` |
| `--prompt` / `--prompt-file` | 二选一 | H3 提示词；长提示建议用文件（UTF-8，避免命令行转义） |
| `--out` | 必填 | 输出 mp4 路径（同时生成 `.meta.json` 与抽帧 png） |
| `--workflow` | `04_i2v_fl2v_4step_sla.json` | 工作流文件名（查 ComfyUI `user/default/workflows/`）或绝对路径 |
| `--seed` | 20261024 | 固定 seed 可复现；改 seed 出不同动作/表情 |
| `--length` | 124 | 帧数（24fps，17k+5 网格：124≈5.17s、141≈5.9s、92≈3.8s） |
| `--width/--height` | 自动 | 自动=按输入图长宽比、短边 `--short-side`(768)、32 对齐。如 512² → 768×768、1344×768 → 1344×768 |
| `--short-side` | 768 | 推导分辨率时的短边（768p） |
| `--fps` | 24 | 输出帧率（H3 原生 24fps） |
| `--timeout` | 900 | 等待生成完成的总秒数（12GB 首次含加载，建议 ≥900） |
| `--server` | http://127.0.0.1:8188 | ComfyUI 地址 |
| `--comfy-root` | env `COMFYUI_ROOT` 或本机路径 | ComfyUI 根目录 |
| `--subfolder` | zaopianju | input/output 子目录（隔离本项目的图与产物） |
| `--no-start` | 关 | 不自动启动 ComfyUI（要求已在线） |
| `--dry-run` | 关 | 只转换 + 存 API prompt，不提交 |
| `--set NODE.FIELD=VALUE` | 无 | 直接覆盖 API prompt 任意输入（高级） |

成功时最后一行是 JSON 摘要（同时写入 `<out>.meta.json`）：

```json
{"ok": true, "prompt_id": "86ffc3b1-...", "gen_sec": 124.7,
 "ffprobe": {"duration_sec": 5.167, "video": {"codec": "h264", "width": 768, "height": 768,
             "fps": 24.0, "nb_frames": 124}, "audio": {"codec": "aac", "sample_rate": "32000", "channels": 2},
             "has_audio": true}, "frames": {...}, "elapsed_sec": 125.2}
```

## 6. workflow_to_api.py（单独使用）

把 ComfyUI 前端保存的「UI 格式」工作流（nodes/links/widgets_values）转成 `POST /prompt` 的「API 格式」（class_type + inputs），可直接给 ComfyUI HTTP API / 后端适配器用。

```powershell
# 转换并查看概要（在线拉 /object_info 做 schema 校验）
python integrations/comfyui/workflow_to_api.py 04_i2v_fl2v_4step_sla.json

# 转换 + 覆盖参数 + 落盘
python integrations/comfyui/workflow_to_api.py 04_i2v_fl2v_4step_sla.json ^
  --out integrations/comfyui/api_workflows/04_i2v_api.json ^
  --set 7.prompt="..." --set 7.width=768 --set 7.height=768 ^
  --set 9.noise_seed=42 --set 18.image=zaopianju/laozhou.png
```

转换规则：

1. **连线**：按 `links=[link_id, src_node, src_slot, dst_node, dst_slot, type]` 解析；目标输入名取 `dst_node.inputs[dst_slot].name`，取不到时回退 `/object_info` 的 `input_order`。
2. **控件值**：新版 ComfyUI 保存的 `widgets_values_named` 直接按键名映射（最稳）；老格式没有该字段时，按 `required + optional` 顺序做位置映射，并自动跳过 `control_after_generate` 这类前端附加控件值。
3. **动态控件**：`SaveVideo` 的 `format` 是 COMFY_DYNAMICCOMBO_V3，API prompt 里用**带点的扁平键**：`format`（容器=mp4）、`format.codec`（h264）、`format.codec.encoding`（auto）+ 顶层 `codec`。已用最小图（LoadImage→CreateVideo→SaveVideo）实测通过。
4. 跳过 `mode=2/4`（静音/旁路）和 Note/MarkdownNote/Reroute 等 UI-only 节点；缺必填输入会打印 `[warn]`。
5. `--set` 覆盖值会按 schema 自动转型（INT/FLOAT/BOOLEAN/COMBO/字符串）。

## 7. 直接调 ComfyUI HTTP API（不用这两个脚本时）

```python
import json, time, urllib.request, uuid
api_prompt = json.load(open("integrations/comfyui/api_workflows/test_001.api.json", encoding="utf-8"))
body = json.dumps({"prompt": api_prompt, "client_id": uuid.uuid4().hex}).encode()
req = urllib.request.Request("http://127.0.0.1:8188/prompt", data=body,
                             headers={"Content-Type": "application/json"}, method="POST")
pid = json.load(urllib.request.urlopen(req))["prompt_id"]
while True:  # 轮询
    h = json.load(urllib.request.urlopen("http://127.0.0.1:8188/history/" + pid))
    if pid in h: break
    time.sleep(2)
print(h[pid]["outputs"])   # {"16": {"images": [{"filename": "xxx_00001_.mp4", "subfolder": "zaopianju", "type": "output", ...}]}}
url = "http://127.0.0.1:8188/view?filename=xxx_00001_.mp4&subfolder=zaopianju&type=output"
```

要点：
- 首帧必须先放进 `ComfyUI/input/`（子目录可以，填 `zaopianju/xxx.png`，正斜杠）；
- `SaveVideo` 产物在 `ComfyUI/output/<subfolder>/`，`/history` 的 `outputs.<node>.images[]` 里带 `filename/subfolder/type` 与 `metadata`（width/height/duration/fps/frame_count，可先校验再下载）；
- 队列/进度：`GET /queue`；中断：`POST /interrupt`；服务信息：`GET /system_stats`。

## 8. 提示词写法（H3 原生多模态描述）

H3 的 prompt 是「一段结构化描述」，不是 SD 式 tag。推荐格式（工作流自带样例同款）：

```
integrated_multimodal_description: [Shot 1] <镜头/主体/动作/镜头运动/光线/风格，一段连贯英文>。
Keep the identity, face and outfit of the person in the first frame exactly; only add <...>.

overall_soundscape: <环境声/动作声，一句 stereo ambience>

non_diegetic_music: N/A
```

- i2v 时**务必写**「保持首帧人物的脸/发型/服装不变，只增加微动作」；
- 想要「说话/点头/眨眼」：写 `she is speaking to camera, lips moving naturally, slight head nod, blinking`；
- 动漫角色镜头建议指定 `anime film still, cel-shaded, clean line art, studio lighting` 等风格词，否则 int8 底模容易往写实漂（首次测试用写实提示词会得到写实结果）；
- 音频是模型原生生成，提示词里的 `overall_soundscape` 会直接影响音轨。

## 9. 已知限制 / 注意事项

1. **12GB 显存**：模型用 dynamic VRAM offload（diffusion 20GB + text encoder 15GB 无法全驻留），每条 prompt 都要重新 staged/offload，模型准备 ~25–40s（视磁盘）；实测单条 115–150s，排队时更长。**不要并发提交多条**：ComfyUI 队列本身就是单 worker 串行，12GB 同时跑 2 条 H3 大概率 OOM/极慢。OOM 时依次降：`--length 92` → `--short-side 640` → 分辨率退到 1152×648（32 对齐）。
2. **时长**：由 `length` 决定，24fps、17k+5 网格（124/141/158…）；训练区间约 124–362 帧（5–15s），更长未验证。5s 左右最稳。
3. **分辨率**：默认短边 768（768p）；width/height 必须 32 对齐。竖屏人物可以 768×1024/768×1280，但像素量越大越慢、越吃显存。
4. **音频**：H3 原生输出 stereo AAC 32kHz，内容由 `overall_soundscape` 提示词决定，不是 TTS 配音；成片需要的对白仍走 edge-tts 后混。
5. **seed**：`RandomNoise` 固定 seed 可复现；换 seed 会改变微动作与运镜。同一 seed 不同 ComfyUI 版本/驱动可能有细微差异。
6. **不要改 ComfyUI 仓库文件**：本接入只写 `input/zaopianju/`、`output/zaopianju/` 两个数据目录。
7. **模型全部离线**：不依赖 ComfyUI.org API 节点；换机时同步 §2 的五个模型文件 + 工作流即可。
8. 首次生成若超过 `--timeout`，run_h3 会 `POST /interrupt` 并保留 `logs/run_*.log` 与服务端日志。排查失败先看 `outputs/h3/<name>.meta.json` 的 `error` 与 `server_log`。

## 10. 切换角色图（已实测：laozhou / ali / dawei 三条）

```powershell
# 角色立绘 -> ComfyUI；run_h3 自动拷到 input/zaopianju/ 并改 LoadImage
python integrations/comfyui/run_h3.py ^
  --image outputs/characters/laozhou/portrait.png ^
  --prompt-file integrations/comfyui/prompts/laozhou_h3.txt ^
  --out outputs/h3/laozhou_h3.mp4 ^
  --width 768 --height 768 --length 124 --seed 20261024 --timeout 1500
```

- 首帧在 `ComfyUI/input/zaopianju/` 里会被命名为 `<输出名>_<原文件名>.png`（如 `laozhou_h3_portrait.png`），
  避免多个角色都叫 `portrait.png` 时互相覆盖（排队/并发时 `LoadImage` 读到别人的图）。
- 角色 prompt 样例在 `prompts/{id}_h3.txt`：强调「保持首帧脸/发型/服装/配色不变 + 说话/点头/眨眼/推眼镜等微动作 + 动漫赛璐璐风」，
  H3 会把 AI 概念 立绘升级成干净的赛璐璐动漫画面（金色/青色/红色轮廓光随角色 accent 色）。
- 中段帧落 `outputs/h3/{id}_h3_fmid.png`，再复制到 `outputs/characters/{id}/h3_portrait.png` 作为角色卡。

- 立绘是方图就 768×768；竖屏可 768×1024（32 对齐）；
- 想固定人物身份更像，用 `05_ref2va_4step_sla.json`（ReferenceToVideo，参考图/参考视频锁身份），run_h3 会自动把参考图接到 `ref_images.ref_image_0`；
- 建议 prompt 里明确「与首帧同一角色、同一服装、同一配色」。

## 11. 故障排查

| 现象 | 原因/处理 |
|---|---|
| 启动即崩，日志末尾 `UnicodeEncodeError: 'gbk'` | 未设 `PYTHONUTF8=1`，见 §3 |
| `POST /prompt` 返回 400 + `node_errors` | 输入名/类型不对；对照 `/object_info`，用 `workflow_to_api.py --set` 修正 |
| 400 提示 `SaveVideo` 的 format 相关 | 动态控件要用扁平点号键（§6.3） |
| `LoadImage` 报找不到图 | 图片没拷进 `ComfyUI/input/`，或路径用了反斜杠（要用 `zaopianju/x.png`） |
| 生成很慢/卡 0% | 首次在加载 20GB 模型（磁盘在 BaiduSyncdisk），看 `logs/comfyui_*.err.log` 是否出现 `prepared for dynamic VRAM loading` |
| OOM / CUDA out of memory | 降 length / 分辨率，见 §9.1；并确保没有别的东西占显存 |
| 服务在线但 `/history` 永不返回 | 看 `GET /queue` 与 `logs/comfyui_*.err.log`；必要时 `POST /interrupt` 后重试 |

## 12. 复现 checklist

```powershell
# 1) 启动服务
powershell -ExecutionPolicy Bypass -File integrations/comfyui/start_comfyui.ps1
# 2) 跑样片（约 2 分钟，含首次模型加载）
python integrations/comfyui/run_h3.py --image tmp/test_input.png ^
  --prompt-file integrations/comfyui/prompts/test_001.txt ^
  --out outputs/h3/test_001.mp4 --width 768 --height 768 --length 124 --seed 20261024
# 3) 校验
powershell -Command "Get-Content outputs/h3/test_001.meta.json -Encoding utf8"
```

实测环境：Windows 11 · RTX 3080 Ti 12GB · ComfyUI 0.39.0 · torch 2.11.0+cu130 · Python 3.13.14（ComfyUI venv）/ 3.11.9（仓库）
