# V46：赛博电影感（Arcane 风绘画感半写实 2D 风格）

> 需求：按用户提供的「赛博英雄传 / Arcane 风」参考图，新增一种「绘画感半写实动画」模板风格。
> 状态：风格已接入（第 5 种 2D 风格）+ 示例模板 `custom_tpl_v46_cyber` 真实出片。

## 1. 风格定义（`pipeline/templates3d_hq.py` → `STYLE_2D_STYLES["arcane_cinematic"]`）

| 项 | 值 |
|---|---|
| label | 赛博电影感（short：赛博） |
| checkpoint | `DreamShaperXL_Turbo_v2_1.safetensors` |
| LoRA | `arcane-style-xl.safetensors`（SDXL LoRA，community Arcane 风；strength 0.85） |
| 采样 | cfg 4.0 / 16 步 / `dpmpp_sde` + `karras`（turbo 底模） |
| IP-Adapter | 0.45 → 0.55 / style transfer |
| 关键帧 prompt | arcane style, league of legends painted animation, cinematic semi-realistic animation film still, painterly brushwork, volumetric light, teal and amber palette |
| 背景句 | detailed painted background, cinematic depth, atmospheric perspective, painterly environment |
| 负面 | multiple characters, 2boys, twin, close-up, photo, 3d render, cartoon outline, flat colors + 通用关键帧负面 |
| 视频前缀覆盖 | `prefix`：cinematic painted animation, semi-realistic stylized film still, painterly textures, volumetric lighting, film grain（不再用「flat colors / cel-shaded」） |
| 视频 finish 覆盖 | `art_style` = painted semi-realistic animation art style；`finish` = painterly brushwork, volumetric light, semi-realistic shading |

说明：H3 的视频提示词原本硬编码「2D anime / flat colors」，会让赛博样片被拉回平涂；这版给风格加了 `prefix / quality / art_style / finish` 四个覆盖点，B-roll、ref2va、关键帧都能吃到同一套风格词。

## 2. LoRA 支持

- `imagegen` 早已支持 `--lora/--lora-strength`（`LoraLoaderModelOnly`）；本次把 `lora / lora_strength` 加进风格表，`_generate_keyframe` 自动透传。
- Arcane LoRA 元数据：`ss_base_model_version=sdxl_base_v0-9`、训练底模 `dreamshaperXL10_alpha2`，与本机 DreamShaper Turbo 同族，实测可用。

## 3. 示例模板 `custom_tpl_v46_cyber`

- 2D / `render_style=arcane_cinematic`，4 幕同 cast（老周/阿栗/甜妹），合同 + episode + semantic brief 齐备。
- 生成结果：见 §4；海报走「有台词镜头关键帧」。

## 4. 实测结果

- 首跑（旧视频提示词）：520.2s / 4,008,415B（平涂词影响视频观感）
- 复跑（新 prefix/art_style，关键帧缓存复用）：**470.2s / 4,158,147B**，成片第 3 秒为绘画感半写实中景，H3 没有被拉回平涂
- 五风格对比：`tmp/v46/films_compare_v46.png`（3×2 网格）；关键帧：`tmp/v16/keyframes/custom_tpl_v46_cyber/arcane_cinematic_dreamshaperxlt_ip45/`
- 关键帧（老周）：1024×576，绘画感半写实、西装领带、青橙光比，与参考图的厚涂质感一致。

## 4.5 赛博场景 + 两个场景模板

新增 3 个场景（`contract/v5/scenes/`，图层栈复制现有可渲染场景后改 id/label/tags/seed）：

| scene | label | 基础图层栈 | plate |
|---|---|---|---|
| `undercity_street` | 底城霓虹巷 | street_night | `assets/v5/scenes/undercity_street.png` |
| `arcane_workshop` | 发明家工坊 | archive_vault | `assets/v5/scenes/arcane_workshop.png` |
| `council_hall` | 议会审判厅 | palace_hall | `assets/v5/scenes/council_hall.png` |

两个 4 幕模板（同 cast / 同台词，音频复用）：

- `custom_tpl_v46_undercity`「V46 2D·底城往事」：底城 broll → 老周底城台词 → 阿栗工坊台词 → 议会 broll
- `custom_tpl_v46_council`「V46 2D·议会夜审」：议会 broll → 老周议会台词 → 阿栗工坊台词 → 底城 broll

### 出片与抽卡实测（2026-11-04 23:00–23:23）

| 模板 | 时长 | 产物 | 说明 |
|---|---|---|---|
| `custom_tpl_v46_undercity` | 520.2s → 复跑（选卡） | 5,351,419B → **5,306,548B** | 每镜 3 候选：#0 街景中景 / #1 工坊全景，人工 pick `#0 / #1` |
| `custom_tpl_v46_council` | 510.2s | 4,825,620B | 议会 broll + 老周议会台词 + 阿栗工坊台词 + 底城 broll |

选卡图：`tmp/v16/keyframes/custom_tpl_v46_undercity/arcane_cinematic_dreamshaperxlt_ip45_c3/candidates/v5_002_sheet.jpg`、`v5_003_sheet.jpg`；picks 写入 `tmp/v16/keyframe_picks.json`。

模板合同新增可选字段 `keyframe_candidates`（1–6），后端 `_generate_hq_command` 会透传成 `--keyframe-candidates`，UI 重跑也能抽卡。

场景库：73 个场景（新增 3 个），模板库 2D 筛选 7 条；两页 0 console error、0 404。

### 抽卡入口（全链路）

1. **向导**：2D 风格下方新增「关键帧抽卡」下拉（1 张 / 2 张推荐 / 3 张），默认 2；值随创建请求提交 `keyframe_candidates`。
2. **创建 API**：`v5_create` 校验 1–6 并写入模板合同 `keyframe_candidates` + `meta.keyframe_candidates`（1 不落盘）。
3. **重跑**：`_generate_hq_command` 读合同并追加 `--keyframe-candidates N` → hq 逐镜多 seed 出候选、拼选卡图、按 picks/自动分选一张。
4. **实测**：`custom_tpl_v46_council`（合同 `keyframe_candidates: 2`）经 API 重跑，日志 `cands=2，pick=#0/auto`，无需手写命令。

## 4.6 新角色 + 机器人组装模板（V47）

两个新角色（`contract/v5/custom/characters/`，立绘 + 三视图在 `assets/v5/characters/`）：

| id | 名称 | 年龄 | 音色 | 用途 |
|---|---|---|---|---|
| `custom_old_inventor` | 老秦 | 66 | `steady_male` | 底城老发明家，host |
| `custom_street_kid` | 小满 | 12 | `young_male` | 底城小子，figure_a |

立绘 prompt 追加了硬年龄约束（`elderly man, 66 years old, deeply wrinkled face, white full beard…` / `12 year old boy, child proportions, round face…`）；只靠合同的 age 字段，底模会把 66 岁画成灰发青年，必须有显式年龄句。

新场景 `robot_assembly`（机器人组装车间）：图层栈复制 `arcane_workshop`，plate 为「半成品人形机器人 + 机械臂 + 焊花」。

模板 `custom_tpl_v46_robot`「V46 2D·机器人组装」，4 幕 13.68s：

1. broll 组装车间（dolly）
2. 老秦：「这台老伙计，还差一颗心。」（push_in）
3. 小满：「那我们把心脏装进去，试试？」（static）
4. broll 底城霓虹巷（dolly）

创建时自动 TTS（4 段音频）+ LLM 语义 brief；`keyframe_candidates: 2`。

### 双人对戏模板（V47）

`custom_tpl_v46_duologue`「V46 2D·装配夜话」：老秦 × 小满 6 幕正反打（19.1s）

1. 组装车间 b-roll
2. 老秦：「它要是醒了，会不会不认我们？」
3. 小满：「不会，它记得你的手。」
4. 老秦：「机器记得的，只有力道。」
5. 小满：「那你就，轻一点。」
6. 底城霓虹巷 b-roll

同一场景内交替给两个角色，落 4 段 TTS 音频；`keyframe_candidates: 2`。注意：自动 brief 的 `look` 默认继承模板 style 的「霓虹演播厅」，必须改写成车间 look，否则 b-roll 会渲染成 talk show。

## 4.7 系列（V48）

- 合同：`contract/v5/series/undercity_arc.json`（label/description/cover/members[{template,label}]）。
- 接口：`GET /api/v5/series` 返回系列 + 每集成片/海报 URL、时长、ready 状态；cover 取封面模板海报。
- 前端「成片库」顶部新增系列横条：封面 + 集数 + 风格 + 可用集数/总时长 + 「连续播放」；播放器弹窗按队列自动续播，底部可点任意一集切换（未生成的集禁用）。
- 当前系列「底城往事 · 三部曲」：底城往事 14.12s → 机器人组装 13.68s → 装配夜话 19.14s，合计 46.9s。

### 导出合集（V48.1）

- `POST /api/v5/series/{sid}/export?force=true`：用封面海报压暗 + 系列名/简介/风格标签生成 2.2s 片头卡，再与各集 `final.mp4` 串成一个 1280×720 / 25fps 的 mp4。
- 产物：`outputs/series/<sid>/{series.mp4,manifest.json,poster.jpg,title_card.mp4,concat.txt}`；接口返回 `url/manifest_url/duration/bytes`，带缓存（同参数第二次直接返回 `cached: true`）。
- 实测：`undercity_arc` → 49.14s / 15,411,269B / build 1.7s；`/media/series/undercity_arc/series.mp4` 200。
- 前端系列卡新增「导出合集」「下载合集 0:49」，合成中禁用按钮，完成后 toast 提示。

## 5. 前端

- `RenderStyle` 增加 `arcane_cinematic`；风格元信息「赛博电影感 / 赛博 / 绘画感半写实动画（Arcane 风）：厚涂笔触 + 青橙光比 + 电影景深」。
- 向导 2D 风格网格从固定 4 列改为 `repeat(auto-fit, minmax(170px, 1fr))`：1600px 下 5 张一排（每张 182px），窄屏自动换行。
- 后端 4 处 label 映射 + `_RENDER_STYLES` 校验同步；模板卡徽标显示「2D·赛博」。

## 6. 用法

```powershell
# 新建：向导里选 2D → 「赛博电影感」
# 或在已有模板上直接跑
python backend/app/routers/custom_hq_runner.py --template custom_tpl_v46_cyber \
  --style-mode 2d --2d-style arcane_cinematic
```