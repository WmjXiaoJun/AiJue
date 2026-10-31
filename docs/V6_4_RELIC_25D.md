# V6.4 报告：5 分钟文物解说 Demo 升级 2.5D 角色/场景 + 项目成片打通

> 承接 V6.3（5 分钟「国宝会说话」Demo）。本轮按「继续修改」把角色与场景升级到 2.5D 变体，并把 Demo 接进项目的「成片」页。

## 1. 2.5D 资源变体（新增）

| 资源 | 说明 |
|---|---|
| `assets/v5/scenes/<scene>_25d.png` | 12 个文物解说场景的 AAM XL 2.5D plate（博物馆/展柜/考古/藏书/长城/江河/钟厅等），1280×720，seed 20261101 |
| `assets/v5/characters/<id>/front_25d_raw.png` + `cutout_25d.png` | 文物解说 cast（laozhou/simaqian/sushi/wuzetian）2.5D 全身 + 稳健抠图（边界 flood-fill + 软边） |
| 环境变量开关 | `V5_CHAR_VARIANT=25d` → `character_art` 优先 `cutout_25d.png`；`V5_SCENE_VARIANT=25d` → `scene_render` 优先 `<scene>_25d.png`；缺失自动回退原资产 |

实现：`pipeline/v5_art.py`（character_art 变体）、`pipeline/scene_render.py`（_scene_plate_path 变体）；脚本 `tmp/v58_scene25d.py`、`tmp/v58_cutout25d.py`。

## 2. Demo 重渲（2.5D 版）

```powershell
$env:V5_CHAR_VARIANT='25d'; $env:V5_SCENE_VARIANT='25d'
python -m pipeline.cli render_v5 --template relic_talk --duration 300
```

- 成片：`outputs/templates_v5/relic_talk/final.mp4`，**306.31s（5:06）**，1280×720/25fps；
- **QC 11/11 PASS**（时长/分辨率/音频/blackdetect/场景/角色/字幕/固定音色/风格）；
- 新场景 plate 明显更精致（博物馆穹顶、教堂式展厅、长城/江河、钟厅等），角色使用 2.5D 全身 cutout；
- 抽帧：`tmp/v58_relic/stills_25d.png`；场景总览：`tmp/v58_relic/scenes25d_sheet.png`；角色 cutout 预览：`tmp/v58_relic/cutout25d_preview.png`。

## 3. 项目 ↔ Demo 打通

- 项目：`proj_59fc69016c`「造片局 · 国宝会说话（5分钟 Demo）」，episode `ep_5610123283`；
- 已把 episode `render_url` 指向 `/media/templates_v5/relic_talk/final.mp4`，状态 ready/100%；
- 前端项目 → 第 1 期 → **成片 tab 直接播放 5 分钟 Demo**（CDP 实测 video.currentSrc 正确，截图 `tmp/v6_frontend/screenshots/v6_relic_episode_finish.png`）。

## 4. 已知问题 / 下一步

1. `museum_gold` 风格整体偏黄、气氛偏重；可换 `ink_chronicle` 或新增「现代博物馆」风格再对比；
2. 2.5D 全身仍是"干净动漫"而非参考图那种半写实 CG（AAM img2img 保构图但细节提升有限）；
3. H3 hero 镜头（每模板 1–2 个）尚未接入本片；可给 3 件国宝各做 1 个 H3 特写镜；
4. 字幕/包装沿用 V5 体系，长片可再加章节卡与文物信息条。
