# 风格系统：2D 动漫 / 3D 动漫（按模板）

> 用户要求：H3 成片要有立体感，做成真正的 3D 动漫风格；同时保留 3D 选项，按模板定风格。

## 1. 两种风格
| 风格 | 说明 | 适用模板 |
|---|---|---|
| **3d_anime**（默认） | 3D 动漫赛璐璐渲染：强纵深、体积光、真实阴影、景深、前后景分层；H3 prompt 加 3D 前缀；场景 plate 用 `*_3d.png` | 综艺/舞台/秀场/生活类 11 个：tonight_show、roundtable、snl_sketch、taskmaster、traitors、shark_tank、masked_singer、hot_ones、qipashuo、standup、slow_life |
| **2d_anime** | 2D 手绘动漫：干净线稿、平涂分层 + 绘景 | 历史/文化类 5 个：history_talk、history_mystery、classics、national_treasure、mystery |

## 2. 落地方式（无 AI 3D，全部 H3/图像生成）
- 场景：SDXL/SDXL+3D 风格 prompt 生成 1280×720 场景 plate（3D 风格加 `_3d` 后缀）或 3D 渲染风 plate；
- 角色：SDXL 3D 动漫角色立绘（IP-Adapter 保持身份）→ 去白底 cutout；H3 参考图用「场景+角色」合成整帧；
- H3 prompt：`style_map.styles[style].prompt_prefix` + 场景描述 + 镜头语法（ShotPlan），强调 depth/volumetric/DOF（3D）或 line art/flat color（2D）；
- 模板→风格映射：contract/style_map.json；渲染时按 episode.format_id/template 选择，可临时覆盖。

## 3. 验收
- 同一模板出 2D/3D 两版对比（至少 tonight_show 3D、history_talk 2D）；
- 3D 版抽帧要有纵深（前景/中景/背景层次、景深虚化、体积光、阴影），角色为 3D 动漫赛璐璐质感；
- 2D 版保留手绘/国风；QC 通过。
