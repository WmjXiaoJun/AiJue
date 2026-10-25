# V3 模板重构交付报告（P0 → P2 + 5 分钟版）

> 依据：`docs/PLAN_V3_TEMPLATE_REFACTOR.md`、`docs/V3_INTERFACES.md`、`docs/V3_ACCEPTANCE.md`（独立验收报告）。
> 执行日期：2026-10-26（夜间 GPU 串行批次）。约束遵循：**不用 Blender**；**默认每镜 H3（ComfyUI）**；静态只允许片头/片尾卡片（previs 仅预览、不进交付）。
> 本报告由 Lead 汇总；逐模板硬门明细见 `docs/V3_ACCEPTANCE.md`（QA 独立复核，非渲染方自证）。

## 0. 结论摘要

| 阶段 | 目标 | 结果 |
|---|---|---|
| P0 | templates_v3 schema + 16 模板 + 校验器 | ✅ `python -m pipeline.template_v3 --validate contract/templates_v3 --expect 16` → **16/16 通过，0 ERR / 0 warn**；16 张场景 plate 唯一、各模板 (plate, cast 站位, action 集合) 签名唯一 |
| P1 | stage 首帧 → ComfyUI H3 每镜 → 剪辑 → QC；先跑 tonight_show / history_talk | ✅ 两个模板各 **40.68s 全 H3**（7/7 非卡片镜 h3=true）；每镜 meta 含 workflow/prompt_id/seed/ref_image/action；QC 10 项全绿、deliverable=true、fallbacks=[] |
| P2 | 补齐 16 模板 + 批量 30-45s 全 H3 demo | ✅ **16/16 模板通过独立验收**（14 个 30.35s + 2 个 40.68s）；累计 **84 个 H3 镜头、84 个全局唯一 prompt_id**、84/84 workflow/prompt_id/seed/ref_image/action 齐全；成片 1280×720/25fps/有音轨、blackdetect 合计 0、字幕覆盖 1.0 |
| P3 | 重点模板 5 分钟版 | ✅（见 §5）tonight_show 5 分钟全 H3 版：`outputs/templates_v3/tonight_show_5min/final.mp4` |

## 1. 环境

- ComfyUI 0.42.0 @ `http://127.0.0.1:8188`；RTX 3080 Ti 12GB；H3 工作流 `04_i2v_fl2v_4step_sla` / `05_ref2va_4step_sla`（4 step SLA）。
- 实测单镜：1024×576 / 124 帧（5.17s）/ audio_ref ≈ 115–127s（GPU 串行）；H3 最小长度 124 帧（~5.17s），故 demo 单镜统一 5.17s。
- Python 3.11 + edge-tts（台词 TTS）+ ffmpeg（合成/包装/调色）；LLM 台本复用 `outputs/llm_scripts/*.json`（16 模板齐全）。

## 2. P0：Template v3（模板 = 节目形态）

- 校验器 `pipeline/template_v3.py`：必填字段/stage.type/layout/props/camera_zones、cast 角色与站位、action_library ≥6 且被 shot 引用、segments duration=300±15%、shots 枚举与 h3_mode、camera_zone 非空且属于 stage、h3.workflow/resolution(32 倍数)、plate 资产存在、目录级 16/16 + 差异签名/动作集合相似度检查。
- 16 个模板：`contract/templates_v3/*.json`（`tonight_show` … `classics`），每模板 4-5 段 / 9-10 镜 / 8-9 动作 / cast 2-3；`stage.scene_desc` 英文场景描述供 H3 prompt。
- 16 张场景 plate：原 12 张 style_v2 plate + 新增 4 张（pitch_room__cinematic / castle_hall__roundtable / crime_scene__ink / archive_room__ink），并重生成了 kitchen_warm__clay、archive_room__ink 两次以修 “菜架近景/空房” 问题；脚本 `scripts/gen_v3_plates.py`。
- 单测：`tests/test_template_v3.py` **89 passed**；QA 独立复核签名/plate/Jaccard（最大 0.125，>80% 对数 0）。

## 3. P1：链路跑通（tonight_show / history_talk）

新模块（接口见 `docs/V3_INTERFACES.md`）：

| 模块 | 职责 |
|---|---|
| `pipeline/stage.py` | plate + 多角色 cutout 首帧合成：camera_zone（机位）× shot.size（景别）取景、16:9 强制、脚底锚 ground_y、投影、卡片帧；补掉了旧合成 “crop 横向拉伸 2.3x” 的隐藏 bug |
| `pipeline/action_library.py` | 动作库 → H3 四段式 prompt（subject_definitions / summary(动作+运镜+场景) / overall_soundscape / non_diegetic_music），fl2v 用 integrated_multimodal_description；不写台词 |
| `pipeline/h3_comfy.py` | 04/05 ComfyUI 客户端：子进程调 `run_h3_ref.py`，解析 prompt_id；每镜原子落 meta（h3/workflow/prompt_id/seed/duration/ref_image/action/prompt/probe）；失败 raise，不静默兜底；`normalize_clip` → 1280×720/25fps/精确时长 |
| `pipeline/storyboard_v3.py` | 模板 segments/shots + LLM 台本 → render.py 兼容 shot 序列；语速适配（长句按标点跨镜切分，避免 atempo 上限截断）、首尾句保护、确定性说话人映射、**非卡片镜统一 audio_ref（05_ref2va）** |
| `pipeline/render_v3.py` | 编排：validate → storyboard → TTS/音轨 → 每镜 stage 首帧（fl2v 加尾帧）→ H3（GPU 串行）→ 转场/字幕/包装/混音 → QC → 产物落盘；支持 `--h3 previs` 预览、`--fallback-static`（QC 标记）、同目录渲染锁 |
| `pipeline/qc_v3.py` | v3 QC 硬门：duration/resolution/fps/audio/blackdetect/subtitle_coverage/h3_coverage/traceability(prompt_id 唯一)/static_marked/stage_meta；非卡片静态兜底=失败 |

实测结果（QA 独立验收）：

| 模板 | 时长 | 非卡片镜 H3 | prompt_id | black | QC |
|---|---|---|---|---|---|
| tonight_show（P1 首版 40s，后被 v2 覆盖） | 40.68s | 7/7 | 7 唯一 | 0 | ok/deliverable |
| history_talk | 40.68s | 7/7 | 7 唯一 | 0 | ok/deliverable |
| 14 个批量模板（30s） | 30.32–30.35s | 5/5 各 | 各 5 唯一 | 0 | ok/deliverable |

- 单测：`pipeline/tests/test_v3_stage.py` 16、`test_v3_h3.py` 19、`test_v3_render.py` 16、`test_v3_evidence.py` 16 全绿。
- 证据链反查：`scripts/verify_h3_evidence.py` 逐 prompt_id 调 ComfyUI `/history` 复核 status/workflow/seed 与 meta 一致（真实报告 `tmp/v3_h3/evidence_report.json`）。

## 4. P2：16 模板批量（30-45s 全 H3）

- 批量命令：`python scripts/render_templates_v3.py --h3 comfy --duration 30`（断点续跑：仅 qc_ok+deliverable+fallbacks=0 才 skip；合并 `batch_report.json`，覆盖 16/16）。
- 实际执行：4 个 chunk 串行（masked_singer/taskmaster/roundtable/shark_tank → snl_sketch/slow_life/mystery/qipashuo → national_treasure/traitors/hot_ones/standup → history_mystery/classics），每模板 ~10 分钟。
- **16/16 通过**：`pytest tests/test_v3_acceptance.py -q` → **90 passed / 0 skipped**；累计 84 H3 镜、84 全局唯一 prompt_id、卡片 32 张全 static_card、fallbacks=[]、blackdetect 合计 0、时长误差 ≤0.01s。
- 画面差异化：16 模板 contact sheet `outputs/templates_v3/contact_sheet.png`（各模板舞台/角色阵容/动作库不同；唯一 plate + 唯一签名）。
- 2D 水墨/国风路径：history_talk / qipashuo / history_mystery / classics 使用 `*__ink` plate + `cutout_2d` + ink/gongbi style_prefix + ink 调色；人审帧 `tmp/qa/ink_review_grid.jpg`（结论：呈国风墨调；H3 ref2va 会把线稿重绘为半写实动画风，属模型特性）。

## 5. P3：重点模板 5 分钟版

- 命令：`python -m pipeline.cli render_v3 --template tonight_show --duration 300 --h3 comfy --force --out outputs/templates_v3/tonight_show_5min/final.mp4 --workdir outputs/templates_v3/tonight_show_5min/work`
- 实测（2026-10-26 05:38:57 → 07:32:54，**6837.3s ≈ 114 分钟** GPU 串行）：**59 镜（57 非卡片 H3 + 2 卡片）**、成片 **299.19s / 69.0MB / 1280×720 / 25fps / 有音轨**；`h3_coverage=1.0（57/57）`、`prompt_ids=57`、`black_segments=0`、`subtitle_coverage=1.0`、`fallbacks=0`、QC 10 项全绿、`deliverable=true`。
- 产物：`outputs/templates_v3/tonight_show_5min/{final.mp4,h3_meta.json,qc_report.json,shots/,frames/工作目录}`；QA 抽检脚本 `tmp/qa/check_p3_5min.py`（规格/字幕/BGM/证据链+抽帧人审），结果见 `docs/V3_ACCEPTANCE.md` §10（P3 附属产物，不纳入 P2 16/16 终门）。
- 整条夜间序列（plate 重生成 → history_talk → 4 chunk 批量 → tonight_show v2 → 5min）`tmp/run_v3_sequence.py` rc=0，`ALL STEPS DONE`。

## 6. 复现命令

```powershell
# P0：模板校验（16/16）
python -m pipeline.template_v3 --validate contract/templates_v3 --expect 16

# 单测（校验器 / v3 模块）
python -m pytest tests/test_template_v3.py -q
python -m pytest pipeline/tests/test_v3_stage.py pipeline/tests/test_v3_h3.py pipeline/tests/test_v3_render.py pipeline/tests/test_v3_evidence.py -q

# P1：两个参考模板 40s 全 H3
python -m pipeline.cli render_v3 --template tonight_show --duration 40 --h3 comfy --force
python -m pipeline.cli render_v3 --template history_talk --duration 40 --h3 comfy --force

# 预演（不交付）：stage 首帧 + 剪辑/QC 走查，QC 自动标 previs
python -m pipeline.cli render_v3 --template tonight_show --duration 30 --h3 previs --force

# P2：16 模板批量（30s demo，断点续跑）
python scripts/render_templates_v3.py --h3 comfy --duration 30

# P3：5 分钟版
python -m pipeline.cli render_v3 --template tonight_show --duration 300 --h3 comfy --force `
  --out outputs/templates_v3/tonight_show_5min/final.mp4 `
  --workdir outputs/templates_v3/tonight_show_5min/work

# 验收（QA）
python -m pytest tests/test_v3_acceptance.py -q
python scripts/verify_h3_evidence.py --root outputs/templates_v3
```

## 7. 已知限制 / 后续（P3+ 建议）

1. **H3 ref2va 会重绘首帧风格**：05 工作流是参考条件生成，不是首帧锁定；2D ink 首帧会被重绘为半写实国风动画（靠 style_prefix + ink 调色统一）。若要严格 “水墨笔触”，需换 04 fl2v 或训练/换 LoRA，或对 ink 模板单独做后处理。
2. **角色身份跨镜漂移**：单参考图（舞台合成帧）驱动，ali 的发色/耳机颜色、dawei 胡须细节在部分镜头有变化；host 稳定。缓解：多参考图（05 ref_images 支持多图，实测 3 refs 更慢且不一定更好）、每镜角色特写 ref、prompt 身份锚点强化。
3. **5 分钟版镜头语义重复**：模板 shots 作为 “镜头意图库” 循环复用，300s 会重复 9 组意图 6 轮；后续需 storyboard_v3 按 beats 展开更多变体 + LLM 台本按模板 beats 生成（当前台本为通用三人聊天体，3 个模板内容偏弱：snl_sketch/hot_ones/slow_life）。
4. 台本裁切：旧 storyboard 下超长句会被 atempo 上限截断（tonight_show 首版 s001），新 storyboard 已按标点跨镜切分修复。
5. 性能：GPU 串行 ~10 分钟/30s 模板、~2 小时/5 分钟版；批量 16 模板约 2.5 小时（本夜实测）。
