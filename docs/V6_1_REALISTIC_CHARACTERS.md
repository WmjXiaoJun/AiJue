# V6.1 报告：写实角色（RealVisXL）+ H3 真人口型片段
> ⚠️ **V6.2 更新**：按用户意见「不要用真人」，本文件的 RealVisXL 写实 portrait 不作为生产角色（保留为实验资产）；生产已切换为 AAM XL 2.5D 半写实 portrait（见 docs/V6_2_REPORT.md）。


> 用户目标：角色要像参考「角色档案」那样有细节（2.5D 真人感），然后交给 H3 生成。
> 上一阶段限制：只有 Animagine XL（动漫模型），角色立绘仍是精细动漫。

## 1. 新模型（已下载并接入）

- **RealVisXL V5.0 fp16**（Photorealistic SDXL，6.46GiB）
  → `ComfyUI/models/checkpoints/RealVisXL_V5.0_fp16.safetensors`
- `imagegen` 新增 **`realistic` style preset**（`imagegen/prompt.py`）：photorealistic/raw photo/真实皮肤/专业摄影 前缀，negative 排除 anime/cartoon/illustration/cel shading；negative 基础词里的 `realistic` 在该 style 下自动移除。
- `pipeline/v5_art.py` 支持 **`V5_ART_CKPT`** 环境变量切换 checkpoint（不改代码即可换模型）。
- ComfyUI 已识别两个 checkpoint：`RealVisXL_V5.0_fp16.safetensors` / `animagine-xl-4.0.safetensors`。

## 2. 24 角色写实 portrait（已完成）

脚本：`scripts/gen_v5_realistic_portraits.py`
产物：`assets/v5/characters/<id>/portrait_real.png` + `real_meta.json`（model/seed/prompt/seconds）
- 24/24 成功（约 12s/张，总计 ~5 分钟）；contact sheet：`tmp/v57_real/portrait_real_sheet.png`
- 质量：真人感皮肤/五官/发型/服装细节，接近参考图；仍保留角色设定（发色/服装/配饰/身份）。
- 不覆盖原动漫资产（portrait/front/cutout 保留），2D 电影风格不受影响。

## 3. H3 写实人物片段（已生成 demo）

脚本：`tmp/v57_h3_portrait.py`（可迁移为正式 pipeline）
流程：
1. 写实 portrait → 1024×576 电影感画幅（虚化背景 + 前景人像）；
2. 用角色**固定音色** TTS 生成台词（`pipeline.v5_voices.preview`，Breeze）；
3. H3 `audio_ref` 图生视频（1024×576/24fps，约 2–2.5 分钟/条）→ 归一化 1280×720/25fps；
4. 产物：`tmp/v57_real/h3/{ali,zhuge,laozhou}_h3.mp4`；拼接 demo：`tmp/v57_real/h3_demo.mp4`（15.02s/1280×720/含音频）。
- 实测：人物有自然说话嘴型/头动（H3 audio_ref 本身按音频对齐口型），比 2D mouth_sync 更真实。
- 抽帧证据：`tmp/v57_real/h3_frames.png`；明细：`tmp/v57_real/h3/h3_report.json`。

## 4. 接入方式

```bash
# 生成/补生成写实 portrait（默认跳过已有）
python scripts/gen_v5_realistic_portraits.py
python scripts/gen_v5_realistic_portraits.py --only ali,zhuge --force

# 列出写实资产状态
python scripts/gen_v5_realistic_portraits.py --list

# 单角色 H3 片段（见 tmp/v57_h3_portrait.py：改 CLIPS 列表即可批量）
python tmp/v57_h3_portrait.py
```

智能体侧：`pipeline.agents.art.list_art` 已返回 `portrait_real` 字段，`art_director` 可查询/生成（`generate_art` 工具的生成路径仍走 anime 流程，后续加 `realistic=True` 分支）。

## 5. 已知限制 / 下一步

1. **写实全身/多视图**：当前 RealVisXL + IP-Adapter 以 portrait 参考生成全身时倾向返回半身/灰底（测试 `tmp/v57_real/assets/ali_real`）；要得到参考图那种「正/侧/背三视图 + 纯白底 cutout」，需要换 composition prompt + 更低 IP-Adapter 权重 + 白底校验重试，或先用写实 portrait 再 H3 生成三视图（成本高）。
2. 2D 电影仍用动漫立绘（写实身体会与风格化场景冲突）；写实角色目前用于 **H3 hero 镜 / 角色档案页 / 宣传片段**。
3. H3 每镜 2–3 分钟 GPU：全量 16 模板 × 1–2 hero 镜 ≈ 1–1.5 小时（可按模板分批）。
4. 后续：写实场景 plate（同一模型）、参考图→三视图→cutout、H3 混合按模板启用（`h3_v5` 增加 portrait 模式）。
