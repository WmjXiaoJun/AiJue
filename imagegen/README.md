# imagegen —— SDXL 动漫角色概念图生成（ComfyUI 本地）

从 `contract/characters/*.json` 的角色 spec 自动拼 Animagine XL 4.0 风格 tag，调用本机 ComfyUI 的
**原生 SDXL txt2img** 工作流生成原创动漫概念图（立绘 / 三视图 / 表情 / 场景），并用 Pillow 合成角色
设定图 `sheet.png`。整套离线、固定 seed 可复现，产物可直接作为 H3 图生视频首帧（见 `integrations/comfyui/`）。

## 0. 现状

| 项目 | 值 |
|---|---|
| 模型 | `ComfyUI/models/checkpoints/animagine-xl-4.0.safetensors`（6,938,434,056 bytes / 6.46GiB，SDXL 动漫；sha256 `1d5b43ff75b6ab598502d4c779d2fbfa3dceca51c60c3b609640a60772333916` 已校验） |
| 后端 | 本机 ComfyUI 0.39.0，`http://127.0.0.1:8188`，RTX 3080 Ti 12GB |
| 工作流 | 原生 SDXL txt2img：CheckpointLoaderSimple → CLIPTextEncode×2 → EmptyLatentImage → KSampler → VAEDecode → SaveImage |
| 默认参数 | steps=28, cfg=5.5, sampler=euler_ancestral, scheduler=normal |
| 尺寸 | portrait 832x1216，其它 1024x1024（均 8 对齐） |
| seed | 默认 42；同一角色内第 k 张用 seed+k（同 seed 家族），可复现 |

## 0.1 实测（2026-10-24，RTX 3080 Ti 12GB，ComfyUI 0.39.0）

| 项目 | 实测 |
|---|---|
| 模型下载 | Animagine XL 4.0 6.94GB：HuggingFace 单连接只有 ~0.5-1MB/s 且频繁断流，改用 `imagegen/tools/download_model.py` 多连接分块（40 conns / 32MB chunk，峰值 **8.2MB/s**，约 13 分钟）；下载后 **sha256 校验通过** |
| 首图（含模型加载） | portrait 832x1216 **14.2s** |
| 单图（模型驻留后） | 832x1216 ~8-14s；1024x1024 ~8-26s |
| `--mode all`（9 图 + sheet） | 单角色 **205-222s** |
| 3 角色全套（3 CLI 并行提交、ComfyUI 串行执行） | 约 **7 分钟**，产出 27 图 + 3 张 sheet |
| sheet.png | 1600x900，Pillow 合成 ~0.5s |

样例（每角色 9 图 + sheet）：`outputs/concepts/{laozhou,ali,dawei}/`

- `portrait.png` 832x1216：深色影棚底 + 角色 accent 色光效，赛璐璐动漫风；
- `three_view/{front,side,three_quarter}.png` 1024x1024：白底单人全身参考视图；
- `expressions/{neutral,happy,surprised,thinking}.png` 1024x1024：半身表情变体；
- `scene.png` 1024x1024（电影感场景图）、`sheet.png` 1600x900（设定图）、`meta.json`（每张图 prompt/seed/尺寸）。


## 0.2 已完成产物 / 复现命令（3 角色全套）

`outputs/concepts/` 下 3 个角色 × 11 文件 = **33 个文件（约 25MB）**：

```
outputs/concepts/laozhou/   ali/   dawei/
├── portrait.png              832x1216
├── three_view/front.png      three_view/side.png      three_view/three_quarter.png
├── expressions/neutral.png   happy.png   surprised.png   thinking.png
├── scene.png                 1024x1024
├── sheet.png                 1600x900（立绘+三视图+4表情+配色条+人设）
└── meta.json                 mode/model/steps/cfg/seed/negative + 每张图的 prompt/seed/尺寸
```

复现（三条命令；ComfyUI 串行执行，约 7 分钟）：

```powershell
python -m imagegen.cli generate --spec contract/characters/laozhou.json --outdir outputs/concepts/laozhou --mode all --style anime --seed 42
python -m imagegen.cli generate --spec contract/characters/ali.json    --outdir outputs/concepts/ali    --mode all --style anime --seed 42
python -m imagegen.cli generate --spec contract/characters/dawei.json  --outdir outputs/concepts/dawei  --mode all --style anime --seed 42
```

单图耗时（从 ComfyUI 输出文件落盘间隔统计，40 张实测）：

| 类型 | 尺寸 | 中位耗时 | 区间 |
|---|---|---|---|
| portrait | 832x1216 | ~17s（首图含模型加载） | 8-20s |
| three_view / expressions / scene | 1024x1024 | **~8.2s** | 7.9-10s |
| sheet.png（Pillow 合成，不占 GPU） | 1600x900 | ~0.5s | - |
| `--mode all` 单角色（9 图 + sheet） | - | ~205-222s | - |

## 0.3 IP-Adapter 一致性（task-16，2026-10-24，RTX 3080 Ti 12GB）

| 项目 | 实测 |
|---|---|
| 自定义节点 | `ComfyUI_IPAdapter_plus`（cubiq，`git clone --depth 1`；**仓库无 requirements.txt、基础 IP-Adapter 不需要 insightface**，只有 FaceID 节点才需要） |
| IP-Adapter 模型 | `models/ipadapter/ip-adapter-plus_sdxl_vit-h.safetensors` 847,517,512 B，sha256 `3f5062b8400c94b7159665b21ba5c62acdcd7682262743d7f2aefedef00e6581` ✓ |
| CLIP Vision | `models/clip_vision/CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors` 3,944,552,236 B，sha256 `036e6e2bd49697511f4f8b8cb5ee465f93025f7a69a145eadeb9a881ace9b18d` ✓ |
| 产物 | 3 角色 × 7 图 = **21 张**：`outputs/concepts/{id}/consistent/`（three_view 3 + expressions 4 + meta.json） |
| 工作流 | SDXL txt2img + `IPAdapterUnifiedLoader`(preset) + `IPAdapterAdvanced`(weight / start_at / end_at / weight_type) |
| 表情参数（最终） | weight **0.65** / end_at **0.85** / preset `PLUS (high strength)` / weight_type linear / seed 42 |
| 三视图参数（最终） | weight **0.35** / end_at **0.90** / weight_type **style transfer** + `--negative-extra` 压火苗/拼贴；side 单独用干净 front.png 当 ref（weight 0.6/linear/end 0.85） |
| 耗时 | 单图 GPU **~11-13s**（模型驻留后；首图含加载 ~40s）；3 个 CLI 并行提交时 `gen_sec` 含排队，平均 ~31s |
| 显存 | 峰值 **11.66GB / 12GB**（CLIPVision 1.2GB + SDXL 4.9GB 动态换载），**无 OOM** |

效果与取舍（重要）：

- **weight 0.65 / end 0.85**：脸型/发色/服装/配色一致性最好（同一张脸），但会把 portrait 的「深色底 + 角色 accent 色火焰」
  风格一起带进三视图 → 三视图变成半身特写+火焰背景。这个特性对 **表情图** 正好（表情本来就该是半身close-up）；
- **weight 0.35 / style transfer**：弱化风格迁移后三视图恢复「白底单人全身」，同时保持发色/服装/配色一致；
- **side 视角**容易生成「大头+小全身」拼贴，因此 side 单独用**干净的 front.png 当 ref**重跑（weight 0.6/linear），得到干净的侧身全身；
- 结论：**expressions 用强权重 0.65；three_view 用弱权重 0.35/style transfer，side 用 front-ref 修补**。

## 0.4 IP-Adapter 安装（一次性）

```powershell
# 1) 自定义节点（无 requirements.txt）
cd D:\BaiduSyncdisk\wmj_work\XJ\ComfyUI\custom_nodes
git clone --depth 1 https://github.com/cubiq/ComfyUI_IPAdapter_plus.git

# 2) 模型（多连接下载器，带 sha256 校验）
python imagegen/tools/download_model.py --url https://huggingface.co/h94/IP-Adapter/resolve/main/sdxl_models/ip-adapter-plus_sdxl_vit-h.safetensors --out "<ComfyUI>/models/ipadapter/ip-adapter-plus_sdxl_vit-h.safetensors" --sha256 3f5062b8400c94b7159665b21ba5c62acdcd7682262743d7f2aefedef00e6581 --connections 16 --chunk-mb 16
python imagegen/tools/download_model.py --url https://huggingface.co/laion/CLIP-ViT-H-14-laion2B-s32B-b79K/resolve/main/model.safetensors --out "<ComfyUI>/models/clip_vision/CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors" --sha256 036e6e2bd49697511f4f8b8cb5ee465f93025f7a69a145eadeb9a881ace9b18d --connections 16 --chunk-mb 16

# 3) 重启 ComfyUI（脚本在 integrations/comfyui/，PYTHONUTF8 已内置）
powershell -ExecutionPolicy Bypass -File integrations/comfyui/stop_comfyui.ps1
powershell -ExecutionPolicy Bypass -File integrations/comfyui/start_comfyui.ps1

# 4) 自检：nodes_ready / model_bytes / clip_vision_bytes
python -m imagegen.cli check
```

> 注意：`start/stop_comfyui.ps1` 含中文，Windows PowerShell 5.1 需要 **UTF-8 BOM** 才能正确解析
> （无 BOM 会 parse error，导致 stop 失效）；仓库里这两个脚本已加 BOM 并修正进程匹配（按 ComfyUI 路径 + main.py）。

## 2.1 一致性模式（`--ref` + IP-Adapter）

```powershell
# 一致性三视图（最终推荐参数：白底全身 + 脸/发色/服装一致）
python -m imagegen.cli generate --spec contract/characters/laozhou.json ^
  --mode three_view --ref outputs/concepts/laozhou/portrait.png ^
  --outdir outputs/concepts/laozhou/consistent --seed 42 ^
  --ip-adapter-strength 0.35 --ip-adapter-end-at 0.90 --ip-adapter-weight-type "style transfer" ^
  --negative-extra "flames, fire, energy, abstract background, collage, close-up"

# 一致性表情（强权重，close-up 一致脸）
python -m imagegen.cli generate --spec contract/characters/laozhou.json ^
  --mode expressions --ref outputs/concepts/laozhou/portrait.png ^
  --outdir outputs/concepts/laozhou/consistent --seed 42 ^
  --ip-adapter-strength 0.65 --ip-adapter-end-at 0.85

# 只补 side 视角（用干净 front 当 ref，避免大头拼贴）
python -m imagegen.cli generate --spec contract/characters/laozhou.json ^
  --mode three_view --views side --ref outputs/concepts/laozhou/consistent/three_view/front.png ^
  --outdir outputs/concepts/laozhou/consistent --seed 44 ^
  --ip-adapter-strength 0.6 --ip-adapter-end-at 0.85
```

一致性参数：

| 参数 | 默认 | 说明 |
|---|---|---|
| `--ref` | 空 | 参考图（通常 portrait.png）；给了就启用 IP-Adapter 工作流 |
| `--ip-adapter-strength` | 0.65 | IPAdapter weight（0.3-0.8；越高越像但风格/构图也会被参考图带偏） |
| `--ip-adapter-end-at` | 0.85 | 在第几步停止注入（0.5-0.9） |
| `--ip-adapter-preset` | `PLUS (high strength)` | IPAdapterUnifiedLoader preset（SDXL 用 PLUS） |
| `--ip-adapter-weight-type` | linear | `linear / ease in / ease out / style transfer / composition` |
| `--negative-extra` | 空 | 追加 negative（一致性模式下压参考图带来的火苗/拼贴） |
| `--views` | 全部 | 只跑指定视角（`side` / `front,three_quarter`） |

产物：`consistent/three_view/{front,side,three_quarter}.png`、`consistent/expressions/{neutral,happy,surprised,thinking}.png`、
`consistent/meta.json`（含 `ip_adapter` 参数与每张图的 ref/seed/gen_sec/prompt）。

## 1. 前置

- 依赖：Python 3.11、Pillow（仓库已装 11.3.0）、本机 ComfyUI 与 Animagine checkpoint；
- 不需要额外 pip 安装：ComfyUI 客户端逻辑复用 `integrations/comfyui/`（`run_h3.py` 的启动/提交/轮询 +
  `workflow_to_api.py` 的 `/object_info` 拉取）；
- ComfyUI 未启动时自动拉起（沿用 `integrations/comfyui` 逻辑，内置 `PYTHONUTF8=1`，避免 Windows GBK
  emoji 崩溃）；也可手动：`powershell -ExecutionPolicy Bypass -File integrations/comfyui/start_comfyui.ps1`。

## 2. 快速开始

```powershell
# 自检：服务在线 + checkpoint 就绪
python -m imagegen.cli check

# 单张立绘（832x1216）
python -m imagegen.cli generate --spec contract/characters/laozhou.json ^
  --outdir outputs/concepts/laozhou --mode portrait --style anime --seed 42

# 全套：立绘 + 三视图 + 4 表情 + sheet.png
python -m imagegen.cli generate --spec contract/characters/laozhou.json ^
  --outdir outputs/concepts/laozhou --mode all --style anime --seed 42

# 只重拼设定图（图已存在时）
python -m imagegen.cli generate --spec contract/characters/laozhou.json ^
  --outdir outputs/concepts/laozhou --mode sheet
```

CLI 最后一行是 JSON 摘要（`ok/outdir/files/images/elapsed_sec`），失败 `exit 1`。

## 3. 产物

```
outputs/concepts/{cid}/
├── portrait.png                      # 832x1216 半身立绘
├── portrait_2.png ...                # --count >1 时
├── three_view/
│   ├── front.png                     # 1024x1024 正面全身
│   ├── side.png                      # 侧面
│   └── three_quarter.png             # 四分之三侧
├── expressions/
│   ├── neutral.png  happy.png  surprised.png  thinking.png
├── scene.png                         # --mode scene（电影感场景图）
├── sheet.png                         # 1600x900 设定图（立绘+三视图+表情+配色条+人设）
└── meta.json                         # id/mode/model/prompt/negative/seed/size/elapsed/files
```

## 4. 参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `--spec` | 必填 | 角色 spec JSON（`contract/characters/*.json`） |
| `--outdir` | `outputs/concepts/{id}` | 产物目录 |
| `--mode` | `portrait` | `portrait / three_view / expressions / scene / sheet / all` |
| `--style` | `anime` | `anime / illustrious / guoman`（前缀与 negative 预设不同） |
| `--seed` | 42 | 基础 seed；同一角色内第 k 张用 seed+k |
| `--size` | 自动 | 覆盖尺寸 `WxH`（默认 portrait 832x1216 / 其它 1024x1024） |
| `--count` | 1 | portrait 张数 |
| `--prompt-extra` | 空 | 追加 tag，逗号分隔（如 `night city, neon lights`） |
| `--steps/--cfg/--sampler/--scheduler` | 28/5.5/euler_ancestral/normal | 采样参数 |
| `--timeout` | 300 | 单图等待秒数（首图含模型加载） |
| `--server/--comfy-root/--no-start` | 127.0.0.1:8188 / 本机 ComfyUI | 服务地址与自动启动 |
| `--skip-existing` | 关 | 已存在的图跳过（断点续跑） |
| `--dry-run` | 关 | 只打印 prompt/workflow，不提交 |
| `--ckpt` | animagine-xl-4.0 | 换底模（V45：2D 风格各自用不同 checkpoint） |
| `--composition` | 按 mode | 覆盖构图预设（V45 关键帧用 `scene_wide`：中远景 + 环境可见） |
| `--lora / --lora-strength` | 空 / 0.9 | 追加 LoRA（`LoraLoaderModelOnly`，模型版底模不匹配时勿用） |
| `--ip-adapter-strength` | 0.65 | IP-Adapter 权重（V45 关键帧降到 0.48~0.50） |
| `--ip-adapter-end-at` | 0.85 | IP-Adapter 结束步（V45 关键帧 0.55~0.58：背景在采样后段自由生成） |
| `--ip-adapter-weight-type` | linear | `linear / style transfer`（V45 用 style transfer 减少构图被参考图带走） |

## 5. Prompt 构建（prompt.py）

把 spec.appearance 映射成 Animagine 友好的 Danbooru tag（而不是直接塞 JSON）：

- `gender/age` → `1boy/1girl, mature male, middle-aged man, ...`
- `hair_color + hair_style` → `black hair, short hair, swept bangs` / `purple hair, twintails` / `bald`
- `eye_color + eye_size` → `blue eyes, big eyes` / `narrow eyes`
- `brow / facial_hair / marks / accessories` → `thick eyebrows, stubble, goatee, scar on eyebrow, glasses, headphones, wristwatch`
- `outfit.type + main/accent` → `navy blue suit, business suit, necktie, white shirt, formal, gold accents`
- `persona/title`（中文关键词粗映射） → `confident expression, smirk, cheerful, authoritative`
- 颜色 `#RRGGBB` → 最近邻动漫颜色词（黑/紫/藏青/金/珊瑚…），避免模型不认识的 hex。
- 构图按用途：portrait=`upper body, looking at viewer, studio lighting, dark background`；
  three_view=`full body, {front view / side / three-quarter}, standing, white background, reference sheet`；
  expressions=`upper body + 表情 tag`；scene=`cinematic anime film still, medium shot, ...`。
- negative 固定：`lowres, bad anatomy, bad hands, text, watermark, extra fingers, deformed, realistic`
  + 按 style/构图追加（多视图加 `multiple views, split screen`，立绘加 `full body`）。
- `--prompt-extra` 追加在角色 tag 之后、品质 tag 之前。

## 6. 与 H3 打通（角色图 → 动画）

生成的 `portrait.png`（或 `three_view/*.png`）直接喂 `integrations/comfyui/run_h3.py`：

```powershell
python integrations/comfyui/run_h3.py ^
  --image outputs/concepts/laozhou/portrait.png ^
  --prompt-file integrations/comfyui/prompts/laozhou_h3.txt ^
  --out outputs/h3/laozhou_h3.mp4 ^
  --width 768 --height 768 --length 124 --seed 20261024
```

> 注意：concept portrait 是 832x1216 竖图，run_h3 默认按「短边 768、32 对齐」推导为 768x1120；
> 想固定 768x768 就显式传 `--width 768 --height 768`。

## 7. 已知限制 / 注意事项

1. **ComfyUI 单 worker 串行**：多张图逐张排队提交，一次 CLI 调用内顺序执行；不要多进程抢同一队列。
2. **首图慢**：首个 prompt 需加载 6.9GB SDXL（约 20-40s，视磁盘），之后同 checkpoint 复用显存，单图约 15-25s。
3. **显存**：SDXL 1024² / 832x1216 在 12GB 上可跑；与 H3 工作流切换时 ComfyUI 会自动卸载/加载模型，可能多花 30-60s。
4. **一致性**：已实现 IP-Adapter 一致性模式（`--ref`，见 §2.1）。经验：expressions 用 weight 0.65 脸最一致；
   three_view 用 0.35/style transfer 才能保住白底全身构图；side 用干净 front 当 ref 修。IP-Adapter 会连带传递参考图
   的底色/风格（如 accent 色火焰），必要时用 `--negative-extra` 压制。
5. **风格漂移**：negative 已含 `realistic`；要更厚涂/国漫用 `--style guoman` 或 `--prompt-extra` 追加风格词。
6. **离线可复现**：模型文件本地；固定 seed + 参数同机可复现，跨 GPU/驱动可能有细微差异。

## 8. tools/ 辅助脚本

- `tools/download_model.py`：多连接分块下载大模型（HuggingFace 单连接限速/断流时用），支持续传 + 大小/sha256 校验：

```powershell
python imagegen/tools/download_model.py ^
  --url https://huggingface.co/cagliostrolab/animagine-xl-4.0/resolve/main/animagine-xl-4.0.safetensors ^
  --out "D:/BaiduSyncdisk/wmj_work/XJ/ComfyUI/models/checkpoints/animagine-xl-4.0.safetensors" ^
  --sha256 1d5b43ff75b6ab598502d4c779d2fbfa3dceca51c60c3b609640a60772333916 ^
  --connections 40 --chunk-mb 32
```

- `tools/rebuild_meta.py`：从产物 PNG 内嵌的 ComfyUI prompt 元数据重建 `meta.json`
  （ComfyUI 的 SaveImage 会把完整 API prompt 写进 PNG 文本块，所以增量跑单个 mode 也不会丢记录）：

```powershell
python imagegen/tools/rebuild_meta.py --spec contract/characters/laozhou.json --outdir outputs/concepts/laozhou
```

## 9. 一致性产物复现（3 角色）

```powershell
# 表情（强权重，3 角色）
foreach ($id in @("laozhou","ali","dawei")) {
  python -m imagegen.cli generate --spec "contract/characters/$id.json" --mode three_view --ref "outputs/concepts/$id/portrait.png" --outdir "outputs/concepts/$id/consistent" --seed 42 --ip-adapter-strength 0.35 --ip-adapter-end-at 0.90 --ip-adapter-weight-type "style transfer" --negative-extra "flames, fire, energy, abstract background, collage, close-up"
  python -m imagegen.cli generate --spec "contract/characters/$id.json" --mode expressions --ref "outputs/concepts/$id/portrait.png" --outdir "outputs/concepts/$id/consistent" --seed 42 --ip-adapter-strength 0.65 --ip-adapter-end-at 0.85
  python -m imagegen.cli generate --spec "contract/characters/$id.json" --mode three_view --views side --ref "outputs/concepts/$id/consistent/three_view/front.png" --outdir "outputs/concepts/$id/consistent" --seed 44 --ip-adapter-strength 0.6 --ip-adapter-end-at 0.85
}
```

产物：`outputs/concepts/{id}/consistent/`（three_view 3 + expressions 4 + meta.json），
并被后端 `GET /api/characters/{cid}/concepts` 以 `consistent` 字段透传。

