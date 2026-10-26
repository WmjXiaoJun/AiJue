# V5.4 剪辑执行层：`pipeline/editing.py`

> 目标（用户）："每个模板还要有不同的剪辑、电影级镜头、分镜、跳转(转场)"。
> 本文覆盖剪辑**执行层**：**每个剪辑点独立转场**（per-boundary transition）+ **J/L cut 音频重叠** + 可选甩镜。
> 上游数据/镜头语言由 `pipeline/editorial.py`（Lead）产出，渲染集成由 `render_v5.assemble`（Lead）调用本模块。
> 参考：libopenshot/OpenShot 的 clip+transition 模型、ffmpeg `xfade`、OpenTimelineIO 的 EDL 思路。

## 1. 文件与入口

| 项 | 内容 |
|---|---|
| 模块 | `pipeline/editing.py` |
| 单测 | `pipeline/tests/test_editing.py`（9 项，纯 CPU，ffmpeg lavfi 生成素材，~2s） |
| pilot | `tmp/v54_editing/make_pilot.py` → `tmp/v54_editing/pilot.mp4` + 证据 |
| 依赖 | 系统/仓库内 ffmpeg（`pipeline.config.FFMPEG_BIN`，即 `tools/node_modules/ffmpeg-static/ffmpeg.exe`） |

## 2. API

```python
from pipeline import editing

# 1) 每剪辑点独立转场的视频拼接（无音轨）
video = editing.xfade_chain(
    shots, clip_paths, workdir, fps, width, height, total, boundaries, prog=None)
# -> workdir / "video_edit.mp4"

# 2) J/L cut 混音（48kHz stereo PCM，时长严格 = total）
audio = editing.mix_audio_edit(
    shots, workdir, total, cuts, out_name="final_narration.wav")
# -> workdir / out_name

# 3) 可选：两镜首尾帧生成甩镜过渡片段（hblur 近似，~0.3s）
clip = editing.whip_pan_transition(clip_a, clip_b, out_path,
                                   fps=25, width=1280, height=720, duration=0.30)

# 辅助
editing.expected_duration(shots, boundaries)   # 总时长公式
editing.supported_modes()                      # 37 个可用 mode 名
```

### 2.1 `xfade_chain(...)`

`shots`: `[{"id": "v5_002", "duration": 10.13}, ...]`（可带 `start/audio` 等字段，透传）。
`clip_paths`: `{shot_id: path}`（也接受与 shots 同序的路径列表）；缺省时找 `workdir/clips/<id>.mp4`。
`boundaries`: 长度 = 镜头数 - 1，每项 `{"from","to","mode","duration"}`，也可直接传 mode 字符串；
缺省/不足按 `cut` 补齐，多余忽略并 warning。

- `mode=cut`（别名 `none`/`hard`）→ `concat`，**不缩短**总时长；
- 其它 mode → ffmpeg `xfade`（链式 filter graph，`offset` 由累计时长算出）；
- 未知 mode → warning + 回退 `fade`；
- 每个 clip 先统一：`fps/scale+crop/setsar/format=yuv420p/trim=镜头时长`（不足用末帧 clone 补齐）；
- 主链失败自动退化到“逐边界两两渲染”兜底（`_xfade_chain_fallback`）；
- 产物 H.264 yuv420p，时长误差 ≤ max(0.10s, 2 帧)，超差抛 `RuntimeError`；
- 同时写 `workdir/edit_meta.json`（每边界的 mode/style/duration + expected/actual duration）。

### 2.2 `mix_audio_edit(...)`

`shots` 每项需有 `id`、`duration`，以及：
- `start`（**剪辑后时间线起点**，有非 cut 转场时必须由调用方传入；缺省用累计镜头时长）；
- 音频来源：`audio`/`audio_path`/`wav` 字段，或自动找 `workdir/audio/<id>.wav`、
  `workdir/audio/shot_<id>.wav`、`workdir/<id>.wav`；都没有则该镜静音。

`cuts`: `[{"shot": id, "j_cut": 0.30, "l_cut": 0.25}]`

| 字段 | 含义 | 音频占用区间 |
|---|---|---|
| `j_cut` | 本镜音频**相对画面提前**（下镜声音先进，压到上镜尾部） | `[start - j, start + duration + l]` |
| `l_cut` | 本镜音频**延后结束**（上镜声音延续到下镜开头） | 同上 |

实现：每轨 `aresample→pan stereo→atrim→apad(whole_dur)→adelay`，再 `amix(normalize=0)`
→ `alimiter` → `apad/atrim` 严格裁到 `total`；产物 48kHz stereo `pcm_s16le`，时长误差 ≤ 0.06s。

## 3. 转场模式表

| 分类 | mode（ffmpeg xfade 名） |
|---|---|
| 基础 | `fade` / `dissolve` / `fadeblack` / `fadewhite` |
| 划像 | `wipeleft` `wiperight` `wipeup` `wipedown` `wipetl` `wipetr` `wipebl` `wipebr` |
| 滑动 | `slideleft` `slideright` `slideup` `slidedown` `smoothleft` `smoothright` `smoothup` `smoothdown` |
| 形状/特效 | `circleopen` `circleclose` `radial` `pixelize` `hblur` `vblur` `zoomin` `diagtl` `diagbr` |
| 切 | `cut`（别名 `none`/`hard`） |
| 甩镜别名 | `whip_left` `whip_right` `whip` → 映射 `hblur`（需要真实运动模糊时用 `whip_pan_transition` 生成过渡片段） |

未知 mode 统一回退 `fade` 并打 warning，保证编辑档案的笔误不会中断整片渲染。

## 4. 时长公式

```
expected_total = sum(shot.duration) - sum(non_cut_boundary.duration)
```

- cut 边界不扣时长；非 cut 边界按转场时长重叠；
- `xfade_chain` 用该值做最终 `trim` 与校验（±1~2 帧）；
- 例：5 镜 10.13+8.45+3.33+4.13+9.09 = 35.13，dissolve 0.4 + wipeleft 0.35 + hblur 0.30，
  expected = 35.13 − 1.05 = **34.08s**（pilot 实测 34.08s）。

## 5. 单测

```powershell
python -m pytest pipeline/tests/test_editing.py -q
# 9 passed（约 2s）：时长公式 / mode 别名 / cut 链 / 混合 cut+xfade / 未知回退 /
#                 单镜 / J-L cut 混音（48k stereo）/ 无音频静音补齐 / 甩镜过渡
```

## 6. Pilot（history_talk 5 镜）

```powershell
python tmp/v54_editing/make_pilot.py
```

- 素材：`outputs/templates_v5/history_talk/shots/{v5_002..v5_006}.mp4`（先复制到 `tmp/v54_editing/source/`，
  避免与正在进行的渲染读写竞争）；
- 边界：`v5_002→v5_003 dissolve 0.40` · `v5_003→v5_004 cut` · `v5_004→v5_005 wipeleft 0.35` ·
  `v5_005→v5_006 hblur 0.30`（**3 种转场 + 1 硬切**）；
- J/L cut：`v5_003 j_cut=0.30`（下镜声音提前 0.3s）、`v5_004 l_cut=0.25`（上镜声音延后）；
- 产物：`tmp/v54_editing/pilot.mp4`（1280×720 / 25fps / 含 AAC 音轨 / 34.08s）；
- 证据：`pilot_evidence.json`（expected vs actual/音轨时长/blackdetect）、
  `pilot_contact_sheet.png`（每个剪辑点 pre/mid/post 各一帧，可见 dissolve 双影、cut 硬切、
  wipeleft 划像、hblur 水平模糊）；`frames/` 24 张单帧。

实测：

| 项 | 值 |
|---|---|
| expected_total | 34.080s |
| video_edit 时长 | 34.080s |
| final_narration 时长 | 34.080s |
| pilot 时长 / 分辨率 / fps | 34.080s / 1280×720 / 25 |
| blackdetect（d=0.30, pix_th=0.10） | **0 段** |
| 音轨 | AAC 192k（pilot）/ 内部 48k stereo PCM |

## 7. 与 render_v5 集成（Lead）

1. `assemble` 里把“全片同一种 xfade 的 `concat_video_xfade`”替换为：
   ```python
   video = editing.xfade_chain(shots, clip_paths, workdir, fps, width, height,
                               total=edit_total, boundaries=edit_plan["boundaries"])
   audio = editing.mix_audio_edit(shots, workdir, total=edit_total, cuts=edit_plan["cuts"])
   ```
   其中 `shots[*]["start"]` 用 editorial 生成的时间线起点（转场会缩短总长），
   `total=editing.expected_duration(shots, boundaries)`。
2. 若沿用现有 `work/audio/<sid>.wav` 命名，`mix_audio_edit` 可自动解析，无需改 shots；
3. `edit_meta.json` 可写入 timeline.json 的 `edit` 轨；`whip_pan_transition` 可用于编辑档案里的
   `whip_left/right` 特殊镜头（生成独立过渡片段插入两镜之间）；
4. 其它 14 部未接入前，旧路径不受影响（本模块仅新增文件）。

## 8. 复现/验收

```powershell
python -m pytest pipeline/tests/test_editing.py -q      # 9 passed
python tmp/v54_editing/make_pilot.py                   # pilot + 证据
# 验收：pilot 可播放、blackdetect=0、时长 34.08s、3 种转场 + 1 硬切 + J/L cut 生效
```
