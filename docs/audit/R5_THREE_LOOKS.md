# R5 · 一个模板 × 三种观感 × 5 分钟（tonight_show 出片与验收）

> 目标：一个模板（`tonight_show` 美式深夜秀）出 **2D 动画 / 3D 动画 / 真实** 三种观感的 300s 成片，
> 用它们验收字幕、特效字/包装、画质等功能。所有渲染 **GPU 串行**（RTX 3080 Ti 12GB，ComfyUI 127.0.0.1:8188）。
> 本文件随跑随更新；最终数字以各 look 的 `work/qc_<look>.json` + `tmp/r5_frames/*.json` 为准。

---

## 0. 入口与产物

```powershell
# 计划（不写盘）
python scripts/rebuild_all_looks_5min.py --only tonight_show \
    --looks 3d_anim,2d_anim,realistic --target 300 --dry-run --force

# 单 look 全量（串行；不带 --force 时复用输入指纹缓存）
python scripts/rebuild_all_looks_5min.py --only tonight_show --looks 2d_anim --target 300

# 验收（CPU）
python docs/audit/_tools/r5_verify_all.py --tid tonight_show --looks 3d_anim,2d_anim,realistic --target 300
```

| 观感 | 成片 | 镜头目录 | 渲染分支 |
|---|---|---|---|
| 2D 动画 | `outputs/templates_3d/tonight_show/final_300s_2d_anim.mp4` | `shots_2d_anim/` | `--style-mode 2d --2d-style arcane_cinematic`；台词镜 = SDXL 关键帧 + ref2va，其余 T2VA |
| 3D 动画 | `.../final_300s_3d_anim.mp4` | `shots_3d_anim/` | `--look 3d_anim --identity keyframe`；台词镜 = 立绘锁脸关键帧 + ref2va，其余 T2VA（文本锁脸） |
| 真实 | `.../final_300s_realistic.mp4` | `shots_realistic/` | `--look realistic --identity keyframe`（同上，写实提示词） |

每 look 的证据：`work/episode_<look>.json`（id 空间=brief）、`work/hq_run_<look>.json`（每镜 meta/降级/重复 id）、
`work/final_<look>.ass`（字幕快照，`outputs/fillers/<tid>/final.ass` 是共享的会被覆盖）、`work/qc_<look>.json`、
`longform_300s_<look>.json`（compose 证据）、`tmp/r5_frames/<tid>_<look>_*.png` + `<tid>_<look>_qc.json`。

---

## 1. 计划（dry-run）

```
tonight_show brief_shots = 74（台词镜 7：v5_002…v5_008，其余 67 镜无台词 → 靠 Tag/Punch 包装）
每个 look：
  shots/                     → shots_bak_<ts>/             （改名保留，不删）
  HQ 渲进干净的 shots/       → 成功后原子切换：
  shots/                     → shots_<look>/               （旧 shots_<look> → shots_<look>_bak_<ts>）
  shots_bak_<ts>/            → shots/                      （生产目录复原）
  final = outputs/templates_3d/tonight_show/final_300s_<look>.mp4
```

---

## 2. 冒烟（40s，2d_anim）

- 第一次（`--force --render-limit 6`，6 镜真渲 ~17 min）：**失败** — `t3.assemble` 的覆盖率断言拒绝 6/74。
  修复：`templates3d_hq.main` 在 `--limit` 模式下跳过 assemble（`hq_run.json.assemble="skipped(limit)"`），
  non-limit 路径不变；失败自动回滚生效（`shots/` 复原，半成品进 `shots_failed_<ts>/`）。
- 复跑：`ok=True dur=40.0s`，23s（6 镜走输入指纹缓存）。
  产物 `final_40s_2d_anim.mp4` 6.4MB / 1280×720 / 25fps / aac；`source_clips=6, fillers=0, unique_shots=9`；
  `final_2d_anim.ass` 21 条 Dialogue（Main 5 / Punch 4 / Tag 3 / Title3D 9）。

---

## 3. 验收口径（含 Lead 要求的三项质量抽检）

1. **时长/规格**：`ffprobe` ≥ 299s、1280×720、25fps、有音轨。
2. **无脸/失控构图**：每 10s 抽 1 帧（300s→30 帧），Haar 人脸检测统计「检测不到脸」的帧；>20% 判该 look 不合格。
3. **人物复制**：同一帧 ≥2 张「尺寸接近 + 水平分离 + 灰度相关 ≥0.55」的人脸 → 记「疑似」并给抽帧路径。
4. **字幕/包装**：`final_<look>.ass` 的 Dialogue 条数按 style 分（Main/Punch/Name/Tag/Title3D）；
   Main 应与「有台词镜头数」≈1:1（本模板 7 镜有台词 → 7 条，其余靠 Tag/Punch/Title3D）。
5. **画质**：`fillers/(fillers+source_clips)` 空镜占比、`blackdetect` 黑场段数、`qc_<look>.json` 是否齐全。

---

## 4. 参照：classics 旧三观感（Lead 肉眼结论的量化复核，step=10s）

| look | 无脸帧/抽帧 | 无脸占比 | 多人脸帧 | 复制疑似 | 判定 |
|---|---|---|---|---|---|
| 3d_anim | 15/30 | **50.0%** | 5 | 1（t=230s，3 脸，相关 0.9+） | **FAIL** |
| 2d_anim | 5/29 | 17.2% | 17 | 2 | pass |
| realistic | 5/29 | 17.2% | 15 | 1 | pass |

- 3d 无脸时间点：`0,10,20,60,100,110,120,130,140,150,160,180,190,260,290`；
- 定性：`classics` brief 里 `framing=close` 有 24 镜，而 `tonight_show` 只有 7 镜（wide 29 / medium 15）→
  `3d_anim` 的「无脸特写」与 close-up 镜头数量强相关；`3d_anim` 除台词镜外**不用 IP-Adapter 锁脸**，
  纯文本 prompt + `LOOK_PREFIX["3d_anim"]`（"feature film still / shallow DOF"）更易产出胸/道具特写。

---

## 5. tonight_show 三观感结果

> 状态：2d_anim 全量渲染中（74 镜，2026-11-07 00:56 起）。下表随完成逐行填。

| look | 时长 | 1280×720/25fps | 音轨 | 无脸帧 | 无脸占比 | 复制疑似 | Main | Punch | Tag | Title3D | 空镜占比 | 黑场 | 降级镜 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2d_anim | TODO | | | | | | | | | | | | |
| 3d_anim | TODO | | | | | | | | | | | | |
| realistic | TODO | | | | | | | | | | | | |

对比图：`tmp/r5_frames/compare_tonight_show.png`（同时间点三观感并排）

---

## 6. 失败与修复记录

1. **`t3.assemble` 在 `--limit` 冒烟下必失败**（覆盖率断言）→ `templates3d_hq.main` 在 limit 模式跳过 assemble，
   并写 `hq_run.json.assemble`；非 limit 不变。
2. **`unique_fill.compose` 的 `allowed` 过滤**用 `episode.json` 的 id 过滤源镜头；旧实现 episode 来自
   `v5_story.build_plan`（V5 时间线 id），与 brief 渲染的 id 交集极小 → 真镜头被丢、成片变空镜。
   修：`_write_episode` 改为**从 semantic brief 派生**（id 集合 == 实际渲染集合），无 brief 才回退 V5 计划。
3. **`outputs/fillers/<tid>/final.ass` 是跨 look 共享的**（后跑覆盖前一个）→ 批处理每 look 快照
   `work/final_<look>.ass` + `work/hq_run_<look>.json`，保证每条的包装证据可分别复核。
4. `--render-limit` / `templates3d_hq --limit` 为冒烟模式（只渲前 N 镜），非交付路径。
