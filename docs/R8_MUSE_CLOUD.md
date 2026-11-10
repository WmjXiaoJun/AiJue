# R8 · 云端 Muse 出镜（每条都对着实测错误写的）

> 用户口径（2026-11-07）：**视频优先云端 Muse**；**每个镜头必须按角色生成**；长场景要一致、要有**首尾帧**；
> 旧成片全部清掉；图片/语音先用本地。UI 回到"项目制五步创作"。

## 1. 链路

```
brief（台词/场景/角色）
  → plan_segments：按 10s 合并成 N 段（300s ≈ 30 段）
  → 每段参考图：说话角色的立绘 portrait_real.png（铺成 16:9）
  → APIMart Muse Video Ext（固定 10s）
  → 裁/盖掉模型自绘的底部文字（字幕条）
  → 下一段的首帧 = 上一段最后一帧（首尾帧衔接）
  → work/episode.json（timeline_source=muse_cloud）
  → templates3d.assemble（字幕/人名条/花字/章节卡）→ exposure 归一
```

代码：`pipeline/muse_shots.py`（规划/生成/时间线）、`pipeline/muse_video.py`（APIMart 适配）、
`pipeline/exposure.py`（曝光归一）、`pipeline/variety_subs.py`（字幕折行）。
接口：`POST /api/v6/muse/video/template`、`GET /api/v6/muse/video/jobs/{job_id}`、`GET /api/v6/video/provider`。

## 2. 五个真实约束（每条都踩过）

| # | 现象 | 结论 / 处理 |
|---|---|---|
| 1 | `duration must be 10 seconds` | `muse-video-ext` **只支持 10s**，其它值直接 400；代码里强制钳到 10 |
| 2 | `reference image must be an HTTP(S) URL, image data URI, or base64` | 本地路径不认，必须转 **data URI**（`_as_image_ref`） |
| 3 | 2D 关键帧当参考图 → `did not pass content safety review` | **2D 关键帧会被拒**；改用**真实感角色立绘** `portrait_real.png` → 通过，且脸/服装一致 |
| 4 | 内容安全是**概率性**的（同图同 prompt 有时过有时拒） | 三级降级重试：原 prompt+立绘 → 极简 prompt+立绘 → 场景底板 |
| 5 | 轮询中途 `URLError` 直接丢任务（已付费） | 连续失败 <6 次就重试，保留 `task_id` |

另外两条观感问题：

- **模型会在画面底部自己写一行字**（不听 "no text"）→ 生成后用不透明"字幕条"盖住（`_crop_model_text`），我们的 Main 字幕正好压在带上。
- **竖版立绘直接喂会出左右黑边** → `_to_16x9()`：模糊底铺满 + 原图等比居中。

## 3. 角色一致性策略（用户选的 ①+② 混合）

1. **立绘参考**：每段用该句说话人的 `portrait_real.png`（角色 id 来自 brief `character`/`roles`，缺失时回台本按台词反查 speaker）。
2. **首尾帧衔接**：`chain=True` 时用上一段最后一帧当下一段首帧（同场景/同角色连续，实测两段同脸同西装同演播室）。
3. 不做"同一段循环复用"（可读性差）；需要时再加 `reuse_master` 开关。

## 4. 验收口径

- 段数 × 10s ≈ 目标时长；缺段要显式列在 `failed[]`；
- 每段必须有 `speaker → character → 立绘` 链路（`tests/test_r8_muse_shots.py` 锁定）；
- 成片：`templates3d.assemble` 出字幕/包装，`exposure.fix` 把平均亮度抬到 ~104；
- 已知保留项：Muse 自绘的顶部台标（`LIVE` / 频道名）暂时保留。

## 5. 用法

```bash
# 规划（不花钱）
python -m pipeline.muse_shots --template tonight_show --limit 3 --dry-run

# 真跑并合成（30 段 ≈ 300s）
python -m pipeline.muse_shots --template tonight_show --segments 30 --assemble

# 复用已有片段、只重跑失败镜
python -m pipeline.muse_shots --template classics --segments 30 --assemble
```

UI：`#/episodes/<eid>/generate` 的「云端 Muse 生成 · 每镜按角色」卡片（段数/首尾帧衔接/开始生成）。
