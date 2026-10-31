# V6.5 全 3D 重设计：5 分钟《国宝会说话》电影感纪念片

> 触发：用户反馈「现在生成的东西完全不能看」「你要做成全3D的 请重新设计」「哪怕有一个能看的也行」。
> 结论：放弃 2.5D 贴图 / 本地 H3 卡通重绘 / Blender 拼装三条失败路线，改走 **云端 MiniMax H3 文生视频的 3D CG 电影镜头** 路线（已实测）。

## 0. 问题诊断（为什么之前的不能看）

| 尝试 | 产物 | 问题 |
|---|---|---|
| V6.4 2.5D | `outputs/templates_v5/relic_talk/final.mp4` | 动漫立绘贴在暗黄 2.5D plate 上，像贴纸；人物无表演；不是 3D |
| 本地 H3 卡通化 | `outputs/templates_v5/relic_talk_3d/shots3d/*`（已归档） | 2.5D 首帧 → H3 int8 4step 重绘成畸形卡通脸，风格崩坏 |
| Blender 拼装 | `.../frames3d/*`（已归档） | 几何体小人 + 空场景，像未完成 prototpye |

根因：**输入画面本身不是 3D CG**，任何视频模型都只能把「动漫贴纸」重绘成更差的画。视频质量的上限由首帧/画面决定。

## 1. 关键技术验证（本轮已完成实测）

`tmp/v60_cloud/` 下 4 条 5s 实测（MetaSo 云端 MiniMax-H3，768P，单条 17–25s / 0.45 元）：

- `test_sword_768p.mp4`：博物馆金黑大厅 + 越王勾践剑展柜，体积光/推镜 —— 电影级 3D CG；
- `test_character_768p.mp4`：白衣长须老者（讲解人）站在展厅，真实 3D 人物、自然微动作；
- `test_battle_768p.mp4`：古战场晨雾、残盾、旌旗、剑插大地 —— 史诗 3D 场景；
- `test_scroll_768p.mp4`：《千里江山图》青绿长卷在案上徐徐展开 —— 特写质感在线。

**结论：云端 H3 文生视频即可稳定产出「3D 电影镜头」，不再需要首帧合成。**
成本：5 分钟 ≈ 50 镜 × 0.45 元 ≈ 25 元；耗时：3 路并发约 10–15 分钟。

## 2. 成片设计

- **定位**：文物纪录短片（旁白叙事），不再做「动漫人物站着说台词」。
- **结构不变**：片头 → 第一件 越王勾践剑 → 第二件 千里江山图 → 第三件 曾侯乙编钟 → 结尾「千年的对话」。
- **旁白**：沿用现有 49 句解说词与固定音色 TTS（`outputs/templates_v5/relic_talk/work/audio/*.wav`），镜头时长与现有 episode 时间线一致（总长 319.2s）。
- **画面**：每个 episode 镜头 = 一条云端 H3 3D 镜头；讲解人/历史人物只作为「画面里的人物」（不看镜头说话），避免口型错位。
- **视觉风格锁**（每条 prompt 必带）：
  `high-end 3D animated feature film shot, cinematic CGI, physically based materials, volumetric light, shallow depth of field, warm amber museum light with mineral blue-green accents, no text, no subtitles, no watermark, no logo`。
- **人物**（少量镜头，统一描述）：讲解人老周（白须、靛蓝汉服）、太史公（深褐汉服持简）、东坡（宋式便服）、越王勾践（青铜甲）、王希孟（青衫少年画师）、曾侯乙乐师。
- **音效/配乐**：镜头内保留 H3 环境声（低音量铺底）+ 统一 guqin/弦乐 BGM（`v5_audio.ensure_bgm`，旁白 ducking）；不使用 H3 自带音乐（避免每镜换曲）。
- **剪辑**：纪录片式硬切为主，片头/章节/片尾用 3D 标题卡；字幕雅致（细金边、低位置、不挡主体）。
- **包装**：片头标题「国宝会说话 / 造片局」、三件国宝章节小标、片尾「下期见」。

## 3. 流水线

### 3.1 数据（可编辑）
`contract/v5/cinema3d/relic_talk_3d_briefs.json`
```json
{"template":"relic_talk","style_lock":"...","shots":[
  {"id":"v5_001","dur":8.0,"subject":"...","camera":"...","env":"...","ambience":"...","notes":"..."}
]}
```

### 3.2 生成器 `pipeline/cinema3d.py`（Lead 负责）
- 读 episode.json + briefs.json → 每镜拼 H3 提示词（`integrated_multimodal_description` / `overall_soundscape` / `non_diegetic_music: N/A`）；
- 调 `integrations.metaso.h3` CLI（768P，duration=ceil(shot.dur) 4–15s），**落盘缓存** `outputs/templates_v5/relic_talk_3d/shots/<sid>.mp4` + `<sid>.meta.json`；
- 并发 3、失败重试 2 次、断点续跑（已有有效文件跳过）；
- `--pilot N` 只跑前 N 镜；`--only id1,id2`；`--force`。
- 抽帧证据：`frames/<sid>_sheet.png`（2×2 网格）。

### 3.3 剪辑器 `pipeline/cinema3d_assemble.py`（assembler teammate 负责）
CLI：
```
python -m pipeline.cinema3d_assemble \
  --episode outputs/templates_v5/relic_talk/work/episode.json \
  --shots  outputs/templates_v5/relic_talk_3d/shots \
  --out    outputs/templates_v5/relic_talk_3d/final.mp4 \
  --work   outputs/templates_v5/relic_talk_3d/work
```
- 每镜：裁到 episode 时长（25fps/1280×720，action=保留原构图 crop）；
- 视频：顺剪（硬切）；片头/片尾/章节处可用 0.3–0.5s dissolve；
- 音频：`audio/narration.wav`（按镜拼接每镜 wav、补静音到镜长，参考 `render.concat_audio`）；H3 环境声按镜低音量（-20dB）铺底（可选）；BGM = `v5_audio.ensure_bgm`；
- 字幕：由 episode 的 `line` 生成新 ASS（雅致款）；片头/片尾卡片标题来自 `shot.title`；三件国宝章节小标（v5_006/v5_016/v5_025 起）；
- 最终编码：`render.final_encode(...)`（两遍 loudnorm -16 LUFS、BGM ducking、ASS 烧录）；
- 写 `final_report.json`（ffprobe/时长/分辨率/响度/blackdetect/字幕覆盖/每镜时长偏差）+ 抽帧 `frames_final/*.png` + `contact_sheet.png`。

### 3.4 接口约定
- 输入镜头：`shots/<sid>.mp4`，1344×768 / 24fps / h264+aac，时长 ≥ episode 镜长；
- 卡片镜：`shots/v5_card_title.mp4`、`shots/v5_card_end.mp4`（Lead 生成，纯 3D 画面，文字由 ASS 叠）；
- 旁白：`outputs/templates_v5/relic_talk/work/audio/<sid>.wav`；
- 成片：`outputs/templates_v5/relic_talk_3d/final.mp4`（1280×720/25fps/H.264/AAC/约 5:19）。

## 4. 验收

1. 成片可播、无黑帧（blackdetect=0）、时长 319.2±1s、1280×720/25fps、有音轨；
2. 抽帧 8 张肉眼可看：3D 质感、构图不崩、无明显水印/乱码文字；
3. 字幕 49 句全覆盖、与旁白对齐；响度 -17~-15 LUFS；
4. 项目「造片局 · 国宝会说话（5分钟 Demo）」成片页可播放新片；
5. 文档 `docs/V6_5_RELIC_3D.md` + 证据包 + commit。

## 5. 分工

| 角色 | 负责 | 写文件 |
|---|---|---|
| Lead | briefs、生成器、云端生成、集成、验收、文档、项目绑定 | `contract/v5/cinema3d/*`、`pipeline/cinema3d.py`、`outputs/.../shots/*`、`docs/*` |
| assembler（teammate） | 剪辑/字幕/BGM/编码/QC 模块 | `pipeline/cinema3d_assemble.py`、`tmp/v61_3d/assemble/*` |
| visual-qa（teammate） | 逐镜抽帧审查、问题清单 | `tmp/v61_3d/qa_review.json`、`tmp/v61_3d/qa/*` |

## 6. 成本与时间

- 生成：约 54 条 × 0.45–0.6 元 ≈ 25–32 元；3 路并发约 12–18 分钟；
- 剪辑编码：3–6 分钟；总时长 40–60 分钟可交付。
