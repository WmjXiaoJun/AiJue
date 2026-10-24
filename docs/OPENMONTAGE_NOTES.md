# OpenMontage 参考笔记与本平台落地

> 仓库：https://github.com/calesthio/OpenMontage（开源 agentic 视频生产系统：12 条 pipeline、100+ tools、700+ agent skill / 制作知识文件）。

## 我们读了什么
- `skills/creative/video-gen-prompting.md`：镜头类型/运镜/机位高度/角度/POV/光线/镜头/主体转场/身份锚定 + 通用 prompt 公式；
- `skills/creative/video-stitching.md`：拼接决策树、转场选择、转场时长按节奏、音频协调、质检清单与常见坑；
- `skills/pipelines/cinematic/edit-director.md`：按情绪切、保护强瞬间、声音推动剪辑、质量门；
- `skills/creative/cinematic.md`：画幅/镜头时长/音频分层/LUT/反主观形容词；
- `skills/creative/broll-planning.md`：B-roll 需求提取、stock vs 生成决策、POV 关键词、素材质检；
- `pipeline_defs/*.yaml`：executive-producer + research/proposal/script/scene/asset/edit/compose/publish + reviewer/checkpoint 阶段与审核标准；
- `skills/creative/lip-sync-usage.md`、`subtitle-sync.md`、`sound-design.md`（后续口型/字幕/音效增强参考）。

## 本平台已落地
| OpenMontage 能力 | 本平台实现 |
|---|---|
| 镜头语法（景别/机位/角度/运镜/光线/目的） | `pipeline/shot_grammar.py` + `contract/SHOT_GRAMMAR.md` |
| 转场决策树（hard cut/crossfade/fade black/L-cut/J-cut） | `pipeline/render.py` xfade 18 预设 + whoosh；按镜头 grammar 记录 transition |
| 镜头级验证片 | `scripts/render_template_demos.py`：16 个模板各出一支 30-50s 验证片（TTS/字幕/名字条/景别标签/xfade） |
| 12 条 pipeline 的分阶段编排 | 我们的 编剧间→分镜台→角色工坊→生成台→成片房→发布 与 H3/渲染/发布 Job 编排 |
| 质检门（reviewer/checkpoint） | 成片验收脚本：时长/帧数/blackdetect/TTS 覆盖/字幕一致/角色覆盖；publish 预检 |
| provider 抽象 | localize（Ollama/NLLB）、H3 本地/MetaSo、Upload-Post 发布、generate provider 预留 |
| 音频分层/响度 | loudnorm + BGM + 转场 whoosh；后续接 ambience/Foley 分层 |

## 差异与后续（按 OpenMontage 继续大量参考）
1. 把 ShotPlan 注入 H3 ref2va/fl2v prompt（身份锚点 + 景别/机位/运镜），让动态镜头有差异化镜头语言；
2. 增加 reviewer/checkpoint 阶段：脚本节奏、分镜覆盖、镜头连续性自动质检；
3. 转场从全局 xfade 升级为按镜头关系的选择（同场景 hard cut、话题切换 crossfade、段落 fade black）；
4. 音频分层：ambience/Foley 轨、音乐 turn 与静音设计；
5. 口型精修参考其 lip-sync-usage；字幕同步参考 subtitle-sync；
6. 模板骨架生成按 plan_segment() 展开为多镜头（当前模板仍一 segment 一镜）。
## 已进一步落地（2026-10-25）
- pipeline/qc.py：技术质检门（时长/规格/黑帧/字幕覆盖/镜头数/说话人/manifest），对应 OpenMontage reviewer + edit-director quality gate；ep_001/002 动态版与 16 个模板验证片全部通过。
- scripts/render_template_demos.py + scripts/make_template_showcase.py：镜头级验证片与巡礼视频（16/16）。
- 进行中：ShotPlan 注入 H3 动态镜头（contract/SHOT_PLAN_INJECTION.md），使景别/机位/运镜差异化；h3-comfy 正在实测 H3 对镜头 prompt 的可控性。
