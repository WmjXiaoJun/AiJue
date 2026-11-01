# V15：口型改到「生成阶段」——H3 ref2va 原生音驱动（替换 MuseTalk 后处理）

> 用户反馈：「能不能生成视频的时候就把嘴形弄好了，现在这个会出现马赛克啊嘴形」。
> 上一版 V14 用 MuseTalk 后处理（把嘴部区域贴回原视频）在部分镜头出现贴片/马赛克感；本版改为**生成时就用音频驱动**（H3 `05_ref2va`，首帧 + 该镜 TTS 音频），不在成片上做二次贴嘴。

## 1. 方案对比

| 方案 | 原理 | 嘴部画质 | 单镜耗时 | 结果 |
|---|---|---|---|---|
| V14 MuseTalk 后处理 | T2VA 生成画面 → 检测人脸 → 生成嘴部贴回（椭圆羽化 mask） | 贴片区域可能出现马赛克/色块、边缘轻微不自然 | ~25–45s/镜 | ❌ 用户可见马赛克 |
| **V15 H3 ref2va 原生** | 首帧图 + 该镜 TTS wav → H3 音驱动生成（音频与画面同一扩散过程） | **原生生成，无贴片**，嘴型与音频同源 | **~57–90s/镜**（768×432，5–8s 镜头） | ✅ 本版采用 |
| V13 H3 ref2va 1024×576（测试） | 同上，更高分辨率 | 原生，清晰度更高 | ~224s/镜 | 成本高，未采用 |

## 2. 实现

新增 `pipeline/templates3d_relip.py`：
1. 遍历 16 个模板的 **97 个有台词镜头**：
   - 取当前镜头首帧（已有语义画面构图，保证场景/角色与文本对应）
   - 以该镜 TTS `audio/<sid>.wav` 为音频条件
   - `python integrations/comfyui/run_h3_ref.py --mode audio_ref --image first.png --audio sid.wav --out ... --duration <shot> --width 768 --height 432 --fps 24`
   - 成功则替换 `shots/<sid>.mp4`（不再做任何嘴部贴片）
2. 全部完成后统一重新剪辑（`templates3d.assemble`）+ 重抽 `poster.jpg`。
3. 旧 MuseTalk 版成片备份 `outputs/_archive_v14_musetalk/`（对比用）。
4. 无台词 b-roll 镜头保持原 T2VA 画面不变。

## 3. 验证

（待全量完成后填入：16/16 时长/大小、抽帧对比图 `tmp/v15/musetalk_vs_ref2va.png`、嘴部 2× 放大图 `tmp/v15/relip_zoom.png`）

## 4. 结论与后续

- 生成阶段原生口型 = 无贴片马赛克；代价是对话镜变成「以首帧为基础的说话镜头」（运动幅度较小），这是 H3 ref2va 的模型特性。
- 如果后续要更强的「边说话边走动/换机位」，需要模型层面的 audio-driven video（本地暂只有 ref2va / 云端 H3 更高分辨率），或回到贴片方案但用更高分辨率 + 更好的 mask（可评估 LatentSync）。
