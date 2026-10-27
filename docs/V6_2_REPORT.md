# V6.2 报告：2.5D 半写实角色（AAM XL，非真人照片）+ H3 2.5D 人物片段

> 用户要求：**不要用真人**；但角色细节要像参考「角色档案」那样（高细节、精致、有质感）。
> 上一轮 V6.1 的 RealVisXL 写实 portrait 属于"真人照片感"，按用户意见**不作为生产角色**（资产保留为实验）。

## 1. 模型与选型过程

| 尝试 | 结果 | 结论 |
|---|---|---|
| RealVisXL V5.0 写实 | 真人照片感（用户否决） | 仅作结构/实验参考，不用于生产 |
| Animagine XL 4.0（原动漫） | 干净但细节/质感不足 | 保留为原资产 |
| RealVis→Animagine img2img 2.5D | 有 2.5D 感但出现面部色块/发光点等 artifacts | 不稳定，未采用 |
| **AAM XL（Lykon/AAM_XL_AnimeMix）** | **2.5D 半写实动漫 CG，高细节、非真人** | ✅ 采用 |

- 下载 `AAM_XL_Anime_Mix.safetensors`（6.46GiB）→ `ComfyUI/models/checkpoints/`
- 也下载了 `2.5DRealistic` LoRA（7.2MB，Pony 系，备用未启用）
- prompt：`2.5D semi-realistic anime, detailed face, detailed skin shading, glossy lips, detailed eyes, soft cinematic lighting, korean beauty, high detail, 8k`；
  negative 排除 `photo/photorealistic/real human/flat/low detail`

## 2. 24 角色 2.5D portrait（已完成）

脚本：`scripts/gen_v5_aam_portraits.py`
产物：`assets/v5/characters/<id>/portrait_25d.png` + `portrait_25d_meta.json`（model/seed/prompt/seconds）
- **24/24 成功**（约 12-14s/张，总计 ~6 分钟）；不覆盖原动漫资产；
- contact sheet：`tmp/v57_25d/aam_sheet24.png`
- **前端角色库已切换为 portrait_25d**（回退 portrait.png），视觉为精致 2.5D 插画、非真人照片；声音锁/试听照常。
- 说明：AAM 生成的面部为"通用精致脸"，身份主要靠 prompt（发色/发型/服装/性别/配饰）保持；如需更强身份一致可后续加 IP-Adapter（已有 AAM+IPA 试验：`tmp/v57_25d/aam_ipa/`）。

## 3. H3 2.5D 人物片段（已完成）

脚本：`tmp/v57_h3_25d.py`
流程：2.5D portrait → 1024×576 电影画幅（虚化背景+前景人像）→ 角色固定音色 TTS 台词 → H3 `audio_ref` → 归一化 1280×720/25fps。
- 产物：`tmp/v57_25d/h3/{ali,zhuge,laozhou}_h3.mp4`；拼接 demo：`tmp/v57_25d/h3_demo_25d.mp4`（15.02s/1280×720/含音频）
- H3 prompt 明确 **2.5D anime character illustration / not photorealistic**，输出保持风格化（非真人），人物有自然说话嘴型与头动；
- 抽帧：`tmp/v57_25d/h3_frames_25d.png`；明细：`tmp/v57_25d/h3/h3_report.json`
- 单条成本约 2–2.5 分钟 GPU。

## 4. 当前状态

- 角色展示：2.5D 半写实（AAM）24 角色，非真人；原动漫立绘保留回退；
- 2D 成片：仍用原动漫全身立绘（写实/2.5D 全身与风格化场景混用还有风险，下一步再升级）；
- H3：2.5D 人物对白/介绍片段 demo 已完成（3 角色）；
- 智能体侧：`character_art` 可扩展返回 portrait_25d 字段（下一步）。

## 5. 下一步

1. **2.5D 全身/三视图/cutout**：用 AAM + img2img（动漫 front 构图保持）生成 2.5D 全身白底，再抠图替换 2D 电影角色；
2. **2.5D 场景 plate**：AAM 生成 61 场景，统一角色/场景画风；
3. **H3 hero 镜按模板接入**：每模板 1–2 镜（约 1–1.5h GPU）；
4. 可选：IP-Adapter 强化 24 角色身份一致性。
