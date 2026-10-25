# OpenMontage 制作方法全量沿用（本平台映射）

> 目标：把 OpenMontage 的「pipeline 阶段 + agent skills + 质量门 + provider 抽象」整体映射到造片局，而不是只借镜头词汇。参考：calesthio/OpenMontage（12 条 production pipeline、100+ tools、700+ agent skill 文件）。

## 1. Pipeline 阶段映射（Executive Producer 模式）
| OpenMontage 阶段/技能 | 造片局实现 | 状态 |
|---|---|---|
| executive-producer（预算/超时/送审） | 后端 Job 编排 + 预算/费用 + 失败不重试 + 重试策略 | ✅ |
| research（题材/参考/风格） | 编剧间话题 + docs/VARIETY_REFERENCES.md + 模板库 | ✅ |
| proposal（方向/分镜提案） | 模板选择 + 从台本生成分镜 | ✅ |
| script（台本/节拍） | 编剧间台本编辑 + SCRIPT_FORMAT 解析 | ✅ |
| scene-plan / shot grammar | pipeline/shot_grammar.py + ShotPlan 注入 H3 | ✅ |
| asset（角色/场景/道具） | 角色工坊（AI 3D/2D/SDXL + IP-Adapter）；场景：2D 绘景（12 张）+ 3D AI 3D 场景（3 条）+ 全身透明 cutout + pipeline/scenes.py 合成 | ✅ |
| edit-director（节奏/情绪/转场） | pipeline/render.py xfade/whoosh + edit 原则 | ✅ 部分 |
| compose（合成/字幕/混音/调色） | pipeline render/localize + ASS/字幕/响度 | ✅ |
| reviewer/checkpoint（质量门） | pipeline/qc.py + 人工审片 | ✅ |
| publish-director（发布） | publish/ + Upload-Post + 发布元数据 | ✅ |

## 2. Agent Skills 沿用清单
| OpenMontage 技能 | 是否沿用 | 本平台落点 |
|---|---|---|
| video-gen-prompting（5-aspect/镜头/运镜/光线） | ✅ | contract/SHOT_GRAMMAR.md、ShotPlan 注入 prompt、H3 可控性实测 |
| video-stitching（转场决策树/时长/音频） | ✅ | render.py xfade 18 预设 + whoosh；后续按镜头关系选择 |
| cinematic（画幅/镜头时长/音频分层/反主观形容词） | ✅ | 模板镜头时长/包装；音频分层待补 ambience |
| b-roll-planning（空镜/素材决策/POV） | ✅ 部分 | b_roll 镜头类型 + scene plates 空镜 |
| image-gen-usage（图像生成/风格/一致性） | ✅ | imagegen/SDXL、角色概念图、2D 场景绘景 |
| 3d-world-generation（3D 世界/空间） | ❌ 已按用户要求停用 | 改为 2D 绘景 + 2D cutout + H3 动态（不再使用 AI 3D） |
| animation-pipeline / hyperframes | ✅ H3 驱动 | 镜头级 2.5D 视差/相机运动（pipeline/scenes.py）；动态镜头 H3 audio_ref/i2v（保留场景） |
| long-form / short-form / storytelling | ✅ 部分 | 5-10 分钟模板 / 竖屏短片扩展位；模板节奏 |
| sound-design / bgm | ✅ 部分 | BGM/转场 whoosh/响度；ambience/Foley 待补 |
| subtitle-sync | ✅ | ASS/SRT + 多语字幕；词级对齐待补 |
| lip-sync-usage | 🚧 | H3 audio_ref 口型（会说话，严格对齐待 LatentSync） |
| color-grading | 待补 | 模板 LUT/色调统一（当前统一美术风格） |
| scene-detect / video-understand | 待补 | 素材分析/参考视频拆解（后续） |

## 3. 制作流程（造片局版）
1. 选模板/新建项目（16 个综艺模板 + 历史夜话/悬案）→ 2. 台本编辑/从台本生成分镜 → 3. 角色工坊（SDXL/AI 3D/2D + H3 动画）→ 4. 场景资产（2D 绘景 / 3D AI 3D set）→ 5. 镜头生成（场景合成 + H3 口型动态 + 首尾帧）→ 6. 剪辑/转场/包装/混音 → 7. QC 质检门 → 8. 多语种本地化 → 9. 一键发布（Upload-Post）。

## 4. 本轮场景制作（解决“全是平面”问题）
- pipeline/scenes.py：2D 绘景 plate + RGBA 角色 cutout 分层 + 2.5D 视差/相机运动（background/foreground 不同速率），景别/机位沿用 ShotPlan；
- outputs/scene_plates/：12 张 2D 动漫场景绘景（深夜秀/圆桌厅/历史书房/辩论场/案发现场/国宝厅/厨房/舞台/访谈沙发/街头/课堂）+ 3 张 3D 渲染风；
- ~~outputs/3d_scenes/~~：已停用（用户要求不用 AI 3D），改为 H3 生成带场景的动态镜头；
- contract/scene_map.json：模板→场景映射；render 支持 scenes 模式；history_talk/tonight_show 等出场景版 demo（30-45s），逐步替换全部模板的平面底。

## 5. 继续沿用（下一轮）
- 场景 plate 加深度层（远景/中景/前景道具）与 3D 渲染层；
- H3 动态镜头融合场景首帧（角色 + 环境一起动），减少纯色底特写；
- 按镜头关系选择转场（同场景 hard cut/话题 crossfade/段落 fade black）；
- color-grading LUT、ambience/Foley、subtitle-sync、lip-sync 精修；
- reviewer 阶段加入脚本节奏/分镜覆盖/连续性检查。
