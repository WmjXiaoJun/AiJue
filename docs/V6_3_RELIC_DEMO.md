# V6.3 报告：5 分钟「国宝会说话」文物解说 Demo（关联项目 + 节目模板）

## 1. 交付

- **成片**：`outputs/templates_v5/relic_talk/final.mp4`
  - 时长 **306.31s（5:06）**，1280×720/25fps，含字幕/包装/BGM；
  - 风格 `museum_gold`（博物馆金黑）；**QC 11/11 PASS**（时长/分辨率/fps/音频/blackdetect/场景/角色/字幕/固定音色/风格/fallbacks）。
- **节目模板**：`contract/v5/templates/relic_talk.json`（54 幕 / 49 句解说 / 3 角色 / 12 场景）
  - 三件国宝：越王勾践剑 → 千里江山图 → 曾侯乙编钟；
  - cast：host=laozhou（老周）、figure_a=simaqian（太史公）、figure_b=sushi（东坡）；
  - 剪辑档案：`contract/v5/editorial/relic_talk.json`（V5.4 自动生成：54 镜·7 景别·7 运镜·4 转场）。
- **节目格式**：`contract/formats/relic_talk_5min.json`（与 16 个 5min 节目格式并列，可在「节目模板/新建项目」里选到）。
- **项目关联**：通过 API 创建项目 **「造片局 · 国宝会说话（5分钟 Demo）」**（`proj_59fc69016c`，format=`national_treasure_5min`）；
  - 模板 `project.project_id` 指向该项目；`outputs/templates_v5/relic_talk/project.json` 落盘项目/格式/成片映射；
  - 前端：模板库出现「国宝会说话」卡（300s/54 幕/成片 306.3s），侧边栏项目列表出现该项目。

## 2. 本轮修复

1. **新模板最终编码失败**：`final_encode`/`render_mix_wav` 固定读 `audio/narration.wav`，新模板目录没有历史缓存 → 报 `Error opening input file audio/narration.wav`。
   修复：`render_v5.assemble` 在 J/L 混音后把本次 narration 同步到 `workdir/audio/narration.wav`。
2. **QC 字幕覆盖误报（长片）**：`qc_v5` 字幕窗口按累计时长计算，未使用 V5.4 per-cut 的 `_edit_start`，长片累积漂移导致 24/43 误报。
   修复：窗口起点优先用 `shot["_edit_start"]`；修复后 49 句字幕全部覆盖，QC PASS。

## 3. 2.5D 全身（本轮尝试，未用于本片）

- 用 AAM XL 对 anime front 做 img2img 生成 2.5D 全身（laozhou/sushi/wuzetian/simaqian）；
- 白底/抠图不稳定（部分背景非纯白、抠图误伤），暂不替换 2D 电影立绘；
- 本片仍用原动漫全身立绘；2.5D portrait 用于角色库/H3 片段（V6.2）。

## 4. 复现

```bash
python scripts/gen_relic_template.py                     # 生成/更新 5min 模板（54 幕）
python -m pipeline.cli render_v5 --template relic_talk --duration 300

# 或走智能体工作流
python -m pipeline.agents run --template relic_talk --duration 300 --goal "5min 文物解说"
```

## 5. 证据

- 成片抽帧：`tmp/v58_relic/stills.png`（片头/剑/画/钟/结尾）
- 模板库截图：`tmp/v6_frontend/screenshots/v6_relic_template.png`（含国宝会说话卡片）
- 项目列表截图：`tmp/v6_frontend/screenshots/v6_relic_project.png`
- render 日志：`tmp/v58_relic/render3.log`
