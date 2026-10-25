# 角色概念图生成（Image Studio）契约 v1

目标：用本机 ComfyUI + SDXL 动漫模型，从角色 spec 生成**原创动漫概念图/三视图/表情**，并可一键交给 H3 做动画。

## 1. 模型
- Checkpoint：`ComfyUI/models/checkpoints/animagine-xl-4.0.safetensors`（SDXL 动漫，约 6.9GB；下载中）
- 工作流：原生 SDXL txt2img（CheckpointLoaderSimple → CLIPTextEncode×2 → EmptyLatentImage → KSampler → VAEDecode → SaveImage），step 28 / cfg 5.5 / euler_a / 1024 基准，12GB 显存可跑。
- 输出目录：`outputs/concepts/{cid}/`
- 生成耗时目标：单图 ≤ 60s（首图含模型加载可 2-3 分钟）。

## 2. CLI（imagegen 包）
```
python -m imagegen.cli generate --spec <character spec.json> --outdir <dir> \
  [--mode portrait|three_view|expressions|scene] [--style anime|illustrious|guoman] \
  [--prompt-extra "bonus tag"] [--seed 123] [--size 832x1216|1024x1024] [--count 1]
```
产物：
- portrait.png（832×1216，half-body）
- three_view/ {front,side,three_quarter}.png（同一角色三视图，prompt 变体 + 同 seed 家族）
- expressions/{neutral,happy,surprised,thinking}.png（表情变体）
- sheet.png（Pillow 合成：立绘 + 三视图 + 表情 + 名字/配色条）
- meta.json {id, mode, model, prompt, negative, seed, size, elapsed, files}
CLI 最后一行 JSON 摘要；失败 exit 1。必须离线可重复（固定 seed）。

## 3. Prompt 构建
由 spec.appearance 自动拼装 anime tag：`masterpiece, best quality, anime style, solo, {gender}, {hair_color} {hair_style}, {eye_color} eyes, {outfit}, {accessories}, upper body, looking at viewer, studio lighting, dark background, {accent} accent`；negative 固定 `lowres, bad anatomy, bad hands, text, watermark, extra fingers, deformed, realistic`。支持 --prompt-extra 追加。

## 4. 后端 API（新增）
```
POST /api/characters/{cid}/concept/generate {modes?:["portrait","three_view"], style?, prompt_extra?, seed?} -> Job(type=concept)
GET  /api/characters/{cid}/concepts -> {portrait, three_view:{...}, expressions:{...}, sheet, meta}
POST /api/characters/{cid}/portrait/from-concept {path} -> Character   # 把概念图设为角色立绘，并同步 cutout（若有）
POST /api/characters/{cid}/h3-animate {image?:path, prompt?} -> Job(type=h3)   # 调 integrations/comfyui/run_h3.py，默认用 portrait
```
- 概念图任务后台线程调 imagegen CLI；产物 URL /media/concepts/{cid}/...
- h3 任务调 run_h3.py，产物 outputs/h3/{cid}_h3.mp4，中段帧回填 h3_portrait.png。

## 5. 前端（角色工坊）
- 角色详情抽屉新增「AI 概念图」区：风格选择（anime/illustrious/guoman）、额外提示词、生成按钮、任务进度、图集（立绘/三视图/表情）、「设为角色立绘」「用此图生成 H3 动画」。
- 角色卡优先展示概念图（若存在），否则显示 AI 3D portrait。
