# V5.2 场景 AI 美术管线（task-9）

> 目标：把 V5 的 61 个场景从「程序化几何绘制」换成 **SDXL Animagine XL 4.0 动漫场景 plate**（无人物的背景美术），
> 运行时再由 `style_v5.apply_finish` 套 16 种风格做差异化。对应计划：`docs/PLAN_V5_2_ART_FRONTEND.md` §3。

## 1. 交付物

| 路径 | 内容 |
|---|---|
| `pipeline/v5_scene_art.py` | 单场景生成：`generate_scene_plate(scene_id, out_png, *, prompt/negative/seed/size/force)`；prompt 解析（显式 > `contract/v5/art/prompts.json` > scene.art > 内置 fallback）；ComfyUI SDXL txt2img + 轮询/取图/落盘；资产契约校验 |
| `scripts/gen_v5_scene_art.py` | 批量串行生成：`--only id1,id2\|pilot\|history`、`--seed`、`--force`、`--dry-run`、`--sheet`、`--size`、`--limit`；进度 `tmp/v52_art/scene_progress.json`；失败不阻断 |
| `assets/v5/scenes/<scene_id>.png` | 1280×720（16:9）anime 场景 plate（无人物） |
| `assets/v5/scenes/<scene_id>.json` | 复现证据：`prompt/negative/seed/size/prompt_id/gen_sec/model/steps/cfg/sampler/scheduler/prompt_source/created_at` |

资产数量：见 §5（61/61）。场景清单来源：`contract/v5/scenes/*.json` 60 个核心场景 + prompts.json 里的
`custom_holo_gallery` 示例场景 = 61 个。

## 2. 生成参数（与最早 3 个角色同一条管线）

- 模型：`animagine-xl-4.0.safetensors`（ComfyUI 8188 本地，`imagegen.workflow.build_workflow` 的 SDXL txt2img 图）
- steps 28 / cfg 5.5 / sampler `euler_ancestral` / scheduler `normal`
- 尺寸：1280×720（16:9，8 的倍数）；无人物（positive 含 `no humans`，negative 压人像/文字/写实）
- 默认 seed：`20261101`（同批次固定，便于复现；per-scene seed 可覆盖）
- 队列：ComfyUI 共享队列（与 render-dev 角色图串行），**不重启不改 ComfyUI**
- prompt：优先 `contract/v5/art/prompts.json` 的 `scenes[<id>]`（template-author 维护，完整 prompt 不再二次加前缀）；
  缺条目时依次回退 `contract/v5/scenes/<id>.json` 的 `art.prompt` → 本模块 `FALLBACK_PROMPTS` → label/tags 自动拼。

## 3. 复现命令

```bash
# 只校验 prompt 解析（不占 GPU）
python scripts/gen_v5_scene_art.py --dry-run --only pilot

# 6 张历史 pilot + contact sheet
python scripts/gen_v5_scene_art.py --only pilot --sheet tmp/v52_scene_pilot/sheet.png

# 全量 61 张（串行、失败不阻断、断点续跑：已存在且 prompt/seed/size/model 一致会跳过）
python scripts/gen_v5_scene_art.py --seed 20261101 --sheet tmp/v52_scene_pilot/sheet_all.png

# 只重生成个别场景（例如 prompt 调整后）
python scripts/gen_v5_scene_art.py --only ancient_map,battlefield --seed 20261101 --force

# 资产数量校验
python -c "import glob; print(len(glob.glob('assets/v5/scenes/*.png')), len(glob.glob('assets/v5/scenes/*.json')))"
```

## 4. pilot 结果（历史场景 6 张，已完成）

命令：`python scripts/gen_v5_scene_art.py --only pilot --sheet tmp/v52_scene_pilot/sheet.png`
结果：6/6 成功，总耗时 52.7s（均 8.8s/张），contact sheet `tmp/v52_scene_pilot/sheet.png`。

| 场景 | gen_sec | seed | 历史内容 |
|---|---|---|---|
| ancient_map | 10.1 → 12.1 → **12.0（最终）** | 20261101 | 最终为**俯视整幅地图**（色块区域/道路/方形城标/河流/罗盘符号/印章纹），prompt_id=182bbd27… |
| battlefield | 8.2 → 16.1 → **16.2（最终）** | 20261101 | 最终回退 v1 表达：**军旗阵 + 远山 + 烟尘 + 日落 + 远景骑兵剪影**，prompt_id=629cae7e… |
| war_council | 8.0 | 20261101 | 沙盘长桌/地图卷/灯笼，军议感成立 |
| palace_hall | 8.0 | 20261101 | 红柱/龙椅壁龛/立幅宫灯，宫殿感强 |
| excavation_site | 8.0 | 20261101 | 探方网格/工具台/遗迹 |
| museum_hall | 8.0 | 20261101 | 拱廊/玻璃展柜/聚光 |

> prompt 迭代记录（只动这两个场景，其余 59 张不变）：v1 pilot → v2（template-author 按"地图本体/军阵"改写，
> 但 negative 里 `architecture/buildings` 压掉城标图标、`no soldiers` 压掉军阵 → 反而更弱）→ v3
> （ancient_map 用本地打样验证的俯视 cartography prompt；battlefield 回退 v1 的"军旗+骑兵剪影"表达）。
> 两版 prompt 已由 template-author 写回 `contract/v5/art/prompts.json`，同 seed 20261101 重生成。
> 修复前后对照：`tmp/v52_scene_pilot/sheet_history_fix.png`（v1）、`sheet_history_v2.png`（v2）、
> `sheet_history_v3.png`（最终）。

附加验证：`tmp/v52_scene_pilot/style_preview.png` —— 3 张历史 plate × 6 个历史风格
（ink_chronicle/woodblock_classic/parchment_case/museum_gold/silk_arena/oil_salon）经
`style_v5.apply_finish` 后的效果，确认 AI plate 与 16 风格 finish 链路兼容。

## 5. 全量结果（61/61）

命令：`python scripts/gen_v5_scene_art.py --seed 20261101 --sheet tmp/v52_scene_pilot/sheet_all.png`

| 指标 | 结果 |
|---|---|
| 场景数 | **61/61**（60 个 `contract/v5/scenes/*.json` + `custom_holo_gallery`） |
| PNG / JSON | **61 PNG + 61 JSON**，全部 1280×720；`assets/v5/scenes/` |
| 本次批量 | 57 张新生成 + 4 张复用 pilot（ancient_map/battlefield 之后按 prompt 迭代各又重生成 2 次，最终 v3 已落盘） |
| 失败 | **0**（`tmp/v52_art/scene_progress.json` 里 `failed=0 / failures=[]`） |
| 批量耗时 | 520.2s（约 8.7 分钟，含共享队列等待）；平均 9.1s/张，单张 8.0–16.1s |
| 生成总耗时（按 meta 累加） | 539.0s |
| seed | 20261101（全量统一；ancient_map/battlefield 重生成同 seed） |
| prompt 来源 | **prompts.json 61/61**（内置 fallback 未被触发） |
| 复现证据 | 每张 `<scene_id>.json` 含 `prompt/negative/seed/size/prompt_id/gen_sec/model/steps/cfg/sampler/scheduler/prompt_source/created_at` |

资产数量校验：

```bash
python -c "import glob; print(len(glob.glob('assets/v5/scenes/*.png')), len(glob.glob('assets/v5/scenes/*.json')))"
# -> 61 61
```

全量 contact sheet：`tmp/v52_scene_pilot/sheet_all.png`（61 张缩略图，按 scene_id 排序）。
失败清单：无（若单张失败，脚本会继续并在 `scene_progress.json` / 最终 JSON 里列 `failures[]`，
重跑 `--only <失败的 id>` 即可断点续跑）。

## 5.1 运行时接入验证（scene_render plate 模式）

Lead 已在 `pipeline/scene_render.py` 接入 plate 模式（`SCENE_ART_DIR`，可用 `V5_SCENE_ART_DIR` 覆盖；
找不到 plate 自动回退程序化）。本地验证：

- 8 组「AI plate × 风格」渲染：ancient_map×ink_chronicle、palace_hall×woodblock_classic、
  battlefield×parchment_case、museum_hall×museum_gold、silk_debate_hall×silk_arena、
  clay_kitchen×clay_diorama、castle_hall_baroque×baroque_candle、ancient_map×oil_salon
  → `tmp/v52_scene_pilot/plate_runtime/sheet.png`
- 结论：plate 作为 background 经 `style_v5.apply_finish` 后 16 风格差异明显（水墨/版刻/羊皮纸/黑金/
  绢本/黏土/烛光/油画），程序化 mid/fg overlay 与 depth 正常，可直接支撑 16 部重渲。

## 5.2 与 prompts.json 的关系

- `contract/v5/art/prompts.json` 由 template-author 维护（scenes 61/61 + characters 段），
  本管线**只读取 scenes 段、绝不覆盖整文件**；prompts 调整后用 `--force --only <id>` 重生成即可。
- 若 prompts.json 缺某场景：回退顺序 `scene.art.prompt` → 本模块 `FALLBACK_PROMPTS`（内置 59 条历史/现代
  prompt，覆盖到与 prompts.json 合并前的所有场景）→ label/tags 自动拼。
- prompt 组装：prompts.json 的 prompt 视为完整成品（不再二次加前缀/后缀）；fallback/自动拼才包
  `PROMPT_PREFIX/PROMPT_SUFFIX`。negative 同理优先用 prompts.json 的 negative。

## 6. 备注 / 已知项

- 历史场景验收点：地图（路线/城池/罗盘）、战场（军旗/军阵）、军帐沙盘、宫殿、藏书楼、博物馆展柜、
  考古发掘、关隘、船队 —— 13 个历史场景 prompt 均含 `chinese ancient setting, historical, ancient china` + 具体内容。
- 9 个需要远景剪影的场景（audience_\*、battlefield、great_wall、lantern_market、river_fleet、neon_skyline、
  city_vhs、rainy_street）使用放宽 negative（允许远处 silhouette，禁 detailed faces/foreground people）。
- `scene_render.render_scene_layers()` 的 plate 模式由 Lead 接入：`V5_SCENE_ART_DIR` 可覆盖资产目录，
  找不到 plate 自动回退程序化绘制；本管线只负责出图与 meta 证据。
- 每次生成都可追溯：`assets/v5/scenes/<id>.json` 里记录 ComfyUI `prompt_id`、seed、prompt/negative、
  模型/采样参数与耗时；需要重生成时 `--force` 即可。
