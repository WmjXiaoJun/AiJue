# LIPSYNC_FEASIBILITY — V5 口型同步（Lip-sync）选型、实测与接入方案

> 结论先行：**MuseTalk 1.5 在本机（RTX 3080 Ti 12GB / Windows / Python 3.11）跑通并可用**。
> 实测 **48–52ms/帧（≈19–21fps 处理吞吐，不含加载）**、**峰值显存 6.85GB（fp16）**、**1024×576 原分辨率原帧率输出**、主角色锁定后检测稳定（YuNet 20–27ms/帧）。
> 质量上**中景/远景（脸宽 ≤120px，本仓库 141 个 H3 片段的中位数只有 87.5px）可用**；**近景/特写（脸宽 ≥150px）会明显变软、偶发糊边，不建议用**——V5 近景本来就有 2D viseme 方案，H3 B-roll 用 MuseTalk 即可。
> 许可：MuseTalk 为 **MIT**（可商用）；Wav2Lip 是 **仅限研究/学术/个人、禁止商用**，不可作为产品默认。

- 负责人：lipsync-dev（V5）
- 日期：2026-10-26
- 实测机器：RTX 3080 Ti 12GB，Windows，主 Python 3.11.9，独立 venv `local_models/lipsync-venv`
- 相关产物：`tmp/v5_lipsync/`（`metrics.json`、`mouth_compare*.png`、`lipsync_mid_v3_s001_3s.mp4`、`lipsync_near_slow_life_s004.mp4`、`near_shot_scan.json`）

---

## 1. 背景与目标

V4 成片用户反馈"嘴形与语音对不上"。现有链路：

```
Breeze TTS 2 (7861) → 每镜 H3 视频 (ComfyUI 05 audio_ref, 1024x576/24fps)
→ 归一化 1280x720/25fps → 拼接
```

H3 的 audio_ref 只做"粗口型"，音素级同步不足。本任务目标：

1. 验证一个**快**（近实时、显存 ≤8GB）的 lip-sync 库能否在 Windows + 3080 Ti 跑通；
2. 给出量化数据（耗时/显存/分辨率/质量）；
3. 落地 `pipeline/lipsync.py`（`available()` / `sync_clip()` / `measure_sync()`），供 `render_v3`/V5 串行调用；
4. 给出默认 provider 与回退/跳过条件。

约束：不修改 `outputs/`、`contract/templates_v3`、`render_v3.py`；GPU 必须串行（不与 H3 渲染争卡）。

---

## 2. 候选库横向对比

| 候选 | 分辨率/脸区 | 速度（本机实测或官方） | 显存 | Windows 可行性 | 许可 | 集成成本 | 结论 |
|---|---|---|---|---|---|---|---|
| **MuseTalk 1.5**（TMElyralab） | 256×256 脸区，输出保持原视频分辨率 | **实测 52–55ms/帧 ≈18–19fps**（干净 GPU，3080 Ti fp16）；官方称 V100 上 30fps+ | **实测峰值 6.85GB**（官方最低 4GB fp16/RTX 3050Ti 可跑） | ✅ 本机已跑通；依赖 diffusers/transformers/whisper-tiny，**不用 mmpose/face-parse** | **MIT**（`local_models/lipsync/MuseTalk/LICENSE`，TME 2024）→ 可商用 | 中：vendored 推理 + 自实现检测/crop/贴回（本次已完成） | **推荐默认**（中景/远景 B-roll） |
| Wav2Lip（Rudrabha） | 96×96 嘴部 | 业界最快（96px，GPU 上可 >50fps） | ~2–3GB | ✅ 可跑（依赖旧，需自写最小推理；检测器可复用 YuNet） | **仅研究/学术/个人，禁止商用**（训练集 LRS2，README「any form of commercial use is strictly prohibited」） | 中 | **不采用**：许可不适合产品；且 96px 糊边比 MuseTalk 更重 |
| LatentSync 1.5 / 1.6（ByteDance） | 512×512 | 官方 1.5 需 ≥8GB、1.6 需 ≥18GB；扩散模型多步，速度显著慢于 MuseTalk | 8GB（1.5）/18GB（1.6） | 理论可行，但 1.6 超 12GB 卡；1.5 需下载 ~5GB 权重 + 多步采样 | **Apache-2.0**（可商用） | 高（权重/环境/时间步调参） | **本轮不实测**：12GB 卡上 1.5 勉强、速度无优势；作为"质量优先"备选记录 |
| 2D viseme 口型（V5 motion2d 内置） | 2D cutout 嘴部贴图 | 无模型推理，CPU 实时 | 0（CPU） | ✅ 已在 V5 使用 | 自研 | 已有 | **V5 对话镜默认**；H3 是整帧光栅视频，无独立嘴层 → **无法直接用于 H3 片段** |

> 补充：MuseTalk 官方说明"推荐 25fps 输入（训练帧率）"；本仓库 H3 为 24fps，实测按 24fps 走 Whisper 特征对齐无同步问题（4% 帧率差可忽略），最终成片归一化到 25fps 不受影响。

---

## 3. 环境与实现

### 3.1 环境（独立 venv，不污染主环境）

```powershell
uv venv --python 3.11 local_models/lipsync-venv
uv pip install --python local_models/lipsync-venv/Scripts/python.exe `
  local_models/wheels/torch-2.9.1+cu128-cp311-cp311-win_amd64.whl `
  local_models/wheels/torchaudio-2.9.1+cu128-cp311-cp311-win_amd64.whl
uv pip install --python local_models/lipsync-venv/Scripts/python.exe `
  diffusers transformers accelerate face-alignment mediapipe opencv-python librosa soundfile omegaconf imageio tqdm "numpy<2" einops
```

实测版本：torch 2.9.1+cu128（CUDA 可用）、diffusers 0.42.0、**transformers 5.20.2**、numpy 1.26.x。

- **transformers 5.20.2 与 MuseTalk 兼容**：`WhisperModel.from_pretrained()` + `whisper.encoder(..., output_hidden_states=True)` 正常，**无需回退 4.46**（已实测 4s 音频 → 96 帧 × [50,384] 特征）。
- MuseTalk 的 `vae.py` 只用到 `torchvision.transforms.Normalize`；为避免安装与 torch ABI 强绑定的 torchvision，`pipeline/lipsync.py` 注入了一个**等价最小 shim**（`_install_torchvision_shim()`）。
- 权重（`local_models/lipsync_models/`，共 ~5.18GB）：
  - `musetalkV15/unet.pth` **3,400,074,924 B，sha256=7ebf6c98…b787007，与 HF LFS oid 一致**（注：首轮下载曾被两个 curl 并发写坏成 1.18GB，已重下并校验）
  - `musetalkV15/musetalk.json`（748B，注意官方 HF 路径是**仓库根级** `musetalkV15/`，不是 `models/musetalkV15/`）
  - `sd-vae/`（334.7MB）、`whisper/`（whisper-tiny，151MB）
  - `yunet/face_detection_yunet_2023mar.onnx`（232KB，自行补充，用于人脸检测）
  - `syncnet/latentsync_syncnet.pt`（886MB，`measure_sync` 预留，本轮未接）

### 3.2 推理链路（vendor 引用，零 mmpose / 零 face-parsing）

代码在 `local_models/lipsync/MuseTalk`（`sys.path` 指过去），**不复制大文件进 repo**；不 import 其 `preprocessing.py`（依赖 mmpose/dwpose），改为在 `pipeline/lipsync.py` 自实现：

```
ffmpeg/cv2 读帧 → YuNet 全帧人脸 bbox(+5 landmark)
  → 复刻 MuseTalk bbox 规则：nose=landmark[2]，upper = 2*nose_y - chin_y，bottom = chin_y + 10
  → EMA 平滑 + 锁定目标（防串脸）
  → crop 到 256×256 → sd-vae encode（masked+ref 两层 latent）
  → whisper-tiny 音频特征（50fps 特征 → 按视频 fps 取 10 帧窗口 →[50,384]/帧）
  → UNet 单步（timesteps=[0]）→ VAE decode → 256×256 生成脸
  → 羽化椭圆掩码贴回原帧（覆盖嘴+下巴，替代 BiSeNet face-parsing）
  → ffmpeg rawvideo 管道 + AAC 复用音频重封装
```

### 3.3 人脸检测选型（实测对比，动漫/3D 卡通脸）

| 检测器 | 初始化 | 每帧耗时 | 动漫脸效果 |
|---|---|---|---|
| face-alignment 1.5（SFD） | 0.4s（但 2DFAN4 权重从 adrianbulat.com **下载卡死**，已放弃 landmark 路线） | ~20ms | ❌ 不稳定：`s001@0.5s` 检出正确；`s001@2.0s` **0 张**；`s001@5.0s` 把小配角当主脸；`v3_s006/v3_s004` 0 张 |
| mediapipe 1.1.1 | — | — | ❌ 1.1.1 已移除 legacy `mp.solutions` API，不可用（新 API 需另接） |
| **OpenCV YuNet**（`face_detection_yunet_2023mar.onnx`） | <0.1s | **22.7ms（CPU）** | ✅ **好**：主脸 score 0.89–0.91 且 bbox 准确；多脸场景按面积/连续性可稳定选主说话人；自带 5 点 landmark（眼/鼻/嘴）→ 可复刻 MuseTalk crop 规则 |

**结论：YuNet 作为唯一检测器**（CPU 跑，不占显存；22.7ms/帧 对 24fps 视频 ≈ 0.55× 实时，可接受）。全片平均 face_found：中景 72/72、近景 123/124。

### 3.4 贴回掩码（替代 face-parsing）

BiSeNet `79999_iter.pth` 只能从 Google Drive/gdown 取，HF 镜像不可得 → 按 Lead 决策**不依赖 face-parsing**：

- 默认参数 `cy=0.72, rx=0.46, ry=0.30, feather=0.10`（相对 crop 宽高的羽化椭圆，中心在嘴/下巴，覆盖下 1/3 脸）。
- 可 CLI 覆盖：`--mask cy,rx,ry,feather`（"只嘴部"参数建议 `0.68,0.34,0.16,0.08`）。
- 效果：只要掩码内是生成脸、掩码外保留原帧；代价是边界处（下颌/颈）在个别帧有轻微糊边。
- 掩码范围对比见 `tmp/v5_lipsync/mask_variants_near.png`（**说明**：该图是用已生成的 before/after 帧按不同掩码做离线重混模拟，非重新推理）：紧掩码能明显减少下颌/脸颊的糊边，但**嘴部内部的生成伪影（如近景 #20 张嘴帧）无法靠缩小掩码消除**。

### 3.5 ffmpeg 重封装

- 帧走 `rawvideo` 管道（`bgr24`）直接喂 libx264（`-crf 16 -pix_fmt yuv420p`），避免 PNG 落盘；
- 音频用 TTS wav（AAC 192k），`-shortest`；保留原分辨率/帧率（1024×576/24fps）。

---

## 4. 实测数据

数据原文：`tmp/v5_lipsync/metrics.json`。

### 4.1 中景（任务指定素材）

`outputs/templates_v3/tonight_show/work/h3/v3_s001.mp4` + `work/audio/v3_s001.wav`，取 **1.0–4.0s（72 帧）**。

| 指标 | 数值 |
|---|---|
| 输入/输出 | 1024×576 / 24fps（保持原样） |
| 处理帧数 | 72 |
| 模型加载 | **7.88s**（unet 3.4GB + sd-vae + whisper-tiny） |
| VAE 预处理（crop+encode×2） | 1.65s（23ms/帧） |
| UNet 单步 + VAE decode | 1.75s（9 个 batch×8，**174ms/batch ≈ 21.8ms/帧**） |
| 贴回 | 0.078s |
| 编码 | 0.44s |
| **整段总耗时** | **17.14s（72 帧）** |
| **每帧耗时（含检测/前处理/贴回）** | **48.4ms → ≈20.7fps 处理吞吐**（不含模型加载） |
| 端到端吞吐（含加载） | 4.2 fps → 3s 镜头约 17s 出片 |
| **峰值显存（torch peak alloc）** | **6.85GB**（fp16；建议 GPU 空闲 ≥7.5GB 再跑） |
| 人脸检测 | YuNet 22.2ms/帧；**锁定主角色后 50/72 帧检出**，54/72 帧有效贴回，**18 帧目标丢失>3帧 → 跳过贴回保留原帧**（不再出现背景糊影） |
| 素材脸宽 | 57–73px |

### 4.2 近景（本轮 141 个 H3 片段里最大脸）

`outputs/templates_v3/slow_life/work/h3/v3_s004.mp4` + `work/audio/v3_s004.wav`，全片 5.17s/124 帧。

| 指标 | 数值 |
|---|---|
| 处理帧数 | 124 |
| 模型加载 / VAE 预处理 / UNet+decode / 贴回 / 编码 | 8.27 / 2.72 / 3.40 / 0.34 / 0.55 s |
| **整段总耗时** | **21.46s**（5.17s 视频） |
| **每帧耗时** | **52.1ms → ≈19fps 处理吞吐** |
| **峰值显存** | **6.85GB** |
| 人脸检测 | YuNet 22.7ms/帧，face_found 123/124 |
| 素材脸宽 | 166–242px（**最大脸片段**） |

### 4.3 掩码范围对比（近景，同片段）

| 掩码 | 参数 | 总耗时 | 每帧 | 观感 |
|---|---|---|---|---|
| 默认（嘴+下巴） | `cy.72 rx.46 ry.30 feather.10` | 21.46s | 52.1ms | 嘴型变化明显，但下颌/脸颊偶有被抹平的软边 |
| 紧掩码（只嘴部） | `cy.68 rx.34 ry.16 feather.08` | 21.44s | 47.9ms | 下颌/脸颊更接近原帧，糊边显著减少；**嘴部内部的生成伪影仍在**（如 #20 张嘴帧） |

→ 近景若必须用，建议 `--mask 0.68,0.34,0.16,0.08`；但整体仍不推荐近景使用（见 §5/§7）。

### 4.4 GPU 争用下的退化（如实记录）

同参数在第 2/3 次运行时 GPU 被其他任务占用（只剩 0.7–4GB 空闲）：

- 第二次：UNet batch 出现 4.4s/5.5s 等待，**总耗时 39.3s（每帧 233ms）→ 2.3× 慢**；
- 第三次：batch 出现 5.1s/6.7s 等待，**总耗时 54.2s（每帧 385ms）→ 3.2× 慢**。

**结论：必须与 H3/TTS 串行，跑前确认 `nvidia-smi` 空闲 ≥8GB。**

### 4.5 素材侧事实：H3 片段的"近景"并不近

用 YuNet 扫了 **141 个** `outputs/templates_v3/*/work/h3/*.mp4`（每片 3 帧）：

| 脸宽阈值 | 片段数 |
|---|---|
| ≥300px（任务要求的近景线） | **0** |
| ≥200px | 3 |
| ≥150px | 12 |
| ≥120px | 24 |
| ≥100px | 48 |
| ≥80px | 85 |
| 中位数 | **87.5px** |
| 最大 | **241.7×349.1px**（`slow_life/work/h3/v3_s004.mp4` @4.39s） |

→ 当前 H3 输出（1024×576）里**不存在脸宽 ≥300px 的镜头**；`slow_life/v3_s004` 已是最大脸，已用作近景测试（即 4.2 数据）。完整列表：`tmp/v5_lipsync/near_shot_scan.json`。

---

## 5. 质量评估（主观，附对比图）

对比图：`tmp/v5_lipsync/mouth_compare.png`（中景 6 帧）、`tmp/v5_lipsync/mouth_compare_near.png`（近景 8 帧）、`tmp/v5_lipsync/mask_variants_near.png`（掩码范围对比）。

| 场景 | 观察 |
|---|---|
| 中景（脸 57–73px） | ✅ 可用。嘴部随音频变化明显，观感接近"原片在说话"；因为脸只占几十像素，重绘的软边几乎看不出来。 |
| 近景（脸 166–242px） | ⚠️ 边缘可用但不建议。下半脸被重绘后**线稿变软、肤色/阴影被抹平**，个别帧（张嘴/遮挡）出现糊边；与动漫/3D 卡通风有轻微"照片感"违和。 |
| 串脸风险 | 已修：首版在 camera pan 时把旁边蓝发配角当成目标贴嘴；现改为"锁定首帧目标 + 近距离候选 + 丢失沿用上一帧（连续丢 >20 帧才重锁）"。72 帧复测全程锁在主角色，无串脸。 |
| 目标离开画面 | 已修：连续丢失 >3 帧后**不贴回、保留原帧**（`skipped_paste_frames` 会返回跳过的帧数），复测中景 #60 的背景糊影已消失。残留：脸在画面边缘/大幅转头时（如中景 #48）仍可能轻微糊边，建议集成侧人工抽检这类镜头。 |
| 与音频同步 | MuseTalk 用 whisper 特征逐帧对齐（24fps 视频按 24fps 取窗），口型与 TTS 时间轴同源；**本轮无 SyncNet 分数**（见 §8）。 |

---

## 6. 集成方式（`pipeline/lipsync.py`）

### 6.1 API（契约）

```python
from pipeline import lipsync

lipsync.available() -> dict
# V5_INTERFACES §5 契约字段（扁平布尔）：{"ok","musetalk","wav2lip","syncnet"}
# 扩展：{"providers","deps{venv_python,musetalk_code,ffmpeg,unet,unet_config,sd_vae,whisper,yunet,syncnet},
#        "files","unet_bytes","unet_expected_bytes","torch_in_env","venv_python","reason"}

lipsync.sync_clip(clip_mp4, audio_wav, out_mp4, *, provider="musetalk", device="cuda",
                  workdir=None, start=None, duration=None, face_hint=None,
                  dump_frames=(0,), fp16=True, max_frames=600, mask_params=None) -> dict
# V5_INTERFACES §5 契约字段（实测 --json 输出）：
# {"ok":true,"provider":"musetalk","frames":72,"elapsed_sec":17.14,"vram_peak_mb":6845.4,
#  "face_found":true,"before":".../0003_before.png","after":".../0003_after.png"}
# 扩展字段：input/output/processed_frames/skipped_paste_frames/detect/timing{model_load_s,
#           preprocess_vae_s,unet_decode_s,paste_s,per_frame_ms,gpu{peak_alloc_mb,...},
#           total_s,fps_throughput}/mask/dump_dir/before_after[]/crops[]

lipsync.measure_sync(clip_mp4, audio_wav=None, *, device="cuda") -> dict
# 未接入（需 xformers + LatentSync syncnet config）→ 返回 {"ok": false, "reason": "..."}
```

**在系统 python（render_v3 环境）调用是安全的**：检测到当前解释器缺 `diffusers/transformers` 时，会**只转发一次**到 `local_models/lipsync-venv` 子进程（带 `LIPSYNC_FORWARD_DEPTH=1` + `LIPSYNC_TIMEOUT=900`，子进程内禁止再转发），返回解析后的 JSON；`max_frames=600` 拒绝长批量误跑。已实测该路径端到端可用（§4.4 的 39s 那次就是系统 python 调用；耗时被 GPU 争用拉长，链路本身正常）。

### 6.2 CLI

```powershell
# 单镜头（3s 子段，输出到 tmp，不动 outputs/）
local_models\lipsync-venv\Scripts\python.exe -m pipeline.lipsync `
  --clip outputs/templates_v3/tonight_show/work/h3/v3_s001.mp4 `
  --audio outputs/templates_v3/tonight_show/work/audio/v3_s001.wav `
  --out tmp/v5_lipsync/v3_s001_ls.mp4 `
  --start 1.0 --duration 3.0 --dump-frames 3,12,24,36 --json

# 能力自检
local_models\lipsync-venv\Scripts\python.exe -m pipeline.lipsync --check
```

### 6.3 给 render_v3 的接入建议（Lead 侧）

1. `--lipsync` 开关 + 仅对 **dialogue 镜**（`speaker` 非空）在 H3 出片后串行调用一次；
2. 调用前检查 `lipsync.available()["ok"]` 与 `nvidia-smi` 空闲 ≥8GB（或先 `/free` 释放 ComfyUI）；
3. **串行**：一次一个 clip，`sync_clip()` 内部有 `max_frames` 与超时保护；**不要 for 循环并发 spawn**（见 §10）；
4. 失败策略：`sync_clip` 抛异常 → 记录 warning、**保留原 H3 片段**继续渲染（不阻断成片）；
5. 脸宽阈值：可从 `face_hint`/检测结果决定是否处理；建议 `face_w ≤ 120px` 才处理，≥150px 跳过（近景走 2D viseme / 原片）；
6. 输出命名：`{shot}_ls.mp4`，随后仍走现有 1280×720/25fps 归一化。

---

## 7. 推荐与回退策略

| 场景 | 建议 |
|---|---|
| V5 对话镜（2D cutout） | **不用 MuseTalk**，沿用 motion2d 的 2D viseme（时间轴同源，天然同步） |
| H3 B-roll / 外部视频，台词镜，**脸宽 ≤120px** | **默认 provider="musetalk"**（本仓库约 64% 片段脸宽 <100px，中位数 87.5px） |
| 脸宽 120–150px | 可用，接受轻微软化；建议人工抽检 |
| 脸宽 ≥150px（近景/特写） | **跳过**；维持 H3 audio_ref 原片或改 2D viseme 版本 |
| GPU 空闲 <8GB / 正在跑 H3/TTS | **跳过 lip-sync**（排队或降级），严禁争卡（实测争用会慢 4×） |
| 无脸/多脸/目标离画 | `face_found=false` → 跳过该镜 |
| 未来若必须"高质量特写" | 备选 LatentSync 1.5（512²，Apache-2.0，官方 ≥8GB）；需另做权重下载与多步采样调优，本机 12GB 可用但速度无优势 |

---

## 8. 未解决问题（如实）

1. **`measure_sync()` 未实现**：MuseTalk 附带的 LatentSync SyncNet 需要 **xformers**（`AttentionBlock2D` 强制依赖）+ syncnet config，本轮优先保主链路，返回 `{"ok": false, "reason": ...}`。若要量化"同步度提升"，下一步装 xformers（cu128 轮子）并接 `musetalk.models.syncnet.SyncNet`，对 before/after 各算一遍 SyncNet 置信度。
2. **无 face-parsing**：羽化椭圆掩码在近景下颌处偶见糊边；若日后拿到 `79999_iter.pth`（gdown）可换回官方 parsing mask，质量会更好。
3. **风格差异**：MuseTalk 训练集是真人，3D 动漫脸被重绘后下半脸线稿变软；近景尤其明显。无参数可完全消除（可试 `--mask` 只保留嘴部，减小暴露面积）。
4. **边缘/大幅转头帧**：目标虽被检测到但脸在画面边缘或大幅转头时（中景 #48），生成脸仍会轻微糊边；建议集成侧对这类镜头人工抽检，或用 `face_hint`+更保守的掩码。
5. **24fps vs 训练 25fps**：差 4%，实测口型节奏无可感问题；若要极致，可先把片段重采样到 25fps 再推理（多一次转码）。
6. **VAE 权重是 `.bin`**：diffusers 会提示 "unsafe serialization"（仅本机可信权重，可接受）；如要消警可转 safetensors。
7. **未测 Wav2Lip / LatentSync 实机数据**：Wav2Lip 因**许可禁止商用**直接排除；LatentSync 因下载/时间窗未做实测，仅引用官方显存数据，若 Lead 需要可另开窗口。
8. **`measure_sync` 的接口按契约返回 dict**（不是 None），调用方 `if not res["ok"]: 视为无分数`。

---

## 9. 事故复盘：`pipeline.lipsync` chain-spawn 进程风暴（2026-10-26 16:28–16:33）

**现象**：约 5 分钟内 python 进程涨到 1 万+，链式 `venv python -m pipeline.lipsync` 互相 spawn，整机卡顿，由 Lead/render-dev 强杀恢复。

**根因**（我的实现缺陷）：转发判断写成
`inproc = LIPSYNC_INPROC=="1" or not VENV_PY.exists()`，
在 venv 内 `VENV_PY.exists()` 恒 True → `not ...` 恒 False → venv 进程认为"自己不是 venv"，于是 `subprocess.run` 再起一个 `venv python -m pipeline.lipsync`；子进程重复同样判断，**无深度上限、无 timeout**，形成无限进程链。

**修复（已在 `pipeline/lipsync.py` 落地并由单测覆盖）**：
1. 显式 venv 判定：`Path(sys.executable).resolve() == VENV_PY.resolve()`（venv 内永不转发）；
2. 转发深度闸：转发时设 `LIPSYNC_FORWARD_DEPTH=depth+1` + `LIPSYNC_INPROC=1`；入口见 `depth>=1` **直接 raise，绝不 spawn**；
3. `subprocess.run(..., timeout=LIPSYNC_TIMEOUT(默认900s))`，超时明确报错；
4. `sync_clip(max_frames=600)` 安全上限，拒绝误跑长批量；
5. 缺依赖时**报错并列出缺哪个模块**，不再回退成"转发"；
6. 单测回归：`pipeline/tests/test_lipsync_unit.py`（9 个用例，含"转发链中 0 次 spawn"、"转发 env 闸门"、"venv 显式判定"），`python -m pytest pipeline/tests/test_lipsync_unit.py -q` 全过。

**教训**：任何"自动转发到另一个解释器"的逻辑必须有**幂等/深度闸 + 超时 + 单测**；批量脚本禁止无上限循环 spawn。

---

## 10. 复现步骤

```powershell
# 0) 串行前提：确认没有 H3/TTS/渲染在跑
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -match 'render_v3|render_templates_v3|h3' }
nvidia-smi   # 空闲 >= 8GB

# 1) 释放 ComfyUI 显存
Invoke-WebRequest -Method Post -Uri http://127.0.0.1:8188/free `
  -ContentType 'application/json' -Body '{"unload_models":true,"free_memory":true}'

# 2) 单段 lip-sync（中景 3s）
local_models\lipsync-venv\Scripts\python.exe -m pipeline.lipsync `
  --clip outputs/templates_v3/tonight_show/work/h3/v3_s001.mp4 `
  --audio outputs/templates_v3/tonight_show/work/audio/v3_s001.wav `
  --out tmp/v5_lipsync/v3_s001_ls_1_4.mp4 --start 1.0 --duration 3.0 `
  --dump-frames 3,12,24,36,48,60 --json

# 3) 生成对比图（CPU，无需 GPU）
local_models\lipsync-venv\Scripts\python.exe tmp/v5_lipsync/offline_crops.py `
  --clip outputs/templates_v3/tonight_show/work/h3/v3_s001.mp4 --start 1.0 --duration 3.0 `
  --dump tmp/v5_lipsync/runtime/v3_s001_dump --out tmp/v5_lipsync/mouth_compare.png
```

---

## 11. 产物清单

| 文件 | 说明 |
|---|---|
| `pipeline/lipsync.py` | 落地代码：`available()` / `sync_clip()` / `measure_sync()` / CLI（vendor MuseTalk） |
| `pipeline/tests/test_lipsync_unit.py` | 11 个单测（含进程风暴回归 + 契约字段） |
| `tmp/v5_lipsync/lipsync_mid_v3_s001_3s.mp4` | 中景 1.0–4.0s lip-sync 成片（1024×576/24fps） |
| `tmp/v5_lipsync/lipsync_near_slow_life_s004.mp4` | 近景（最大脸 242px）lip-sync 成片（默认掩码） |
| `tmp/v5_lipsync/lipsync_near_slow_life_s004_tight.mp4` | 近景 + 紧掩码（只嘴部） |
| `tmp/v5_lipsync/mouth_compare.png` | 中景 6 帧嘴部 before/after |
| `tmp/v5_lipsync/mouth_compare_near.png` | 近景 8 帧嘴部 before/after |
| `tmp/v5_lipsync/mouth_compare_near_tight.png` | 近景紧掩码 6 帧对比 |
| `tmp/v5_lipsync/mask_variants_near.png` | 掩码范围（默认 vs 仅嘴部）对比 |
| `tmp/v5_lipsync/near_shot_scan.json` | 141 个 H3 片段人脸尺寸扫描 |
| `tmp/v5_lipsync/metrics.json` | 实测数据原文（含 3 个 clean run + 2 个争用 run + 契约 JSON 示例） |
