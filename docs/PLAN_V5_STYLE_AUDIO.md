# PLAN V5：可变风格世界 + 口型同步 + 全量可自定义

> 版本：V5.0 设计稿（2026-10-26）
> 上游：`docs/PLAN_V4_AUDIO_EDIT.md`（V4 语音重构）、`docs/V4_REPORT.md`、`docs/V4_ACCEPTANCE.md`
> 目标交付：16 个**视觉风格截然不同**的 30-45s 成片（历史类含地图/战争/史料画面），
> 口型与语音同步，角色/场景/风格/模板全部可由用户自定义。

---

## 0. 用户诉求拆解

| 诉求 | 现状问题 | V5 目标 |
|---|---|---|
| 嘴形和语音对不上 | H3 05 audio_ref 只做到"大致跟着说话"，口型精度低；V4 用自然语速放大了违和 | 引入 lip-sync 后处理（MuseTalk 1.5 为主、Wav2Lip 快速回退），QC 增口型同步分 |
| 16 个模板只有一种风格 | 16 个模板共用 ~11 张 SDXL 动漫 plate + 3 个角色 cutout；`style_presets` 只是 prompt 后缀，视觉差异极小 | **风格引擎**：每种风格有独立技法/调色/纹理/线条/角色处理/字幕包装/转场/音效；历史类有地图、战场、文物、史料场景 |
| 历史要有历史画面（地图/战争等） | 只有一张"history_study"书桌 plate | 历史风格场景库：古地图、战场、军议、宫殿、档案库、博物馆、关隘、船队、市井 |
| 角色可以多加、场景可以多加 | 3 角色、11 场景 | 24+ 角色（历史 12 + 现代 12）、60+ 场景、40+ 道具，全部 JSON 库 |
| 用户全部可以自定义 | 模板/风格/角色/场景都是代码内常量 | `contract/v5/` JSON 契约 + 覆盖目录 + 校验器 + CLI + API（+ UI）|
| 先规划文档再实施 | — | 本文档 + 实施阶段/验收标准 |

---

## 1. 现状诊断（为什么看起来只有一种风格）

1. `contract/templates_v3/*.json` 的 `stage.plate` 只指向**一张** plate（如 `history_study__ink`），
   `scene_map.json` 16 模板只对应 ~11 个基础场景；风格差异只体现为 plate 后缀 + 调色。
2. plate 由 `scripts/gen_style_plates.py` 用 `animagine-xl-4.0`（**动漫 SDXL**）生成，
   即使 prompt 写"ink-wash"也仍是动漫渲染 → 16 模板观感接近。
3. 角色 cutout 仅 `outputs/characters/cutout_{2d,3d}_meta.json` 的 3 个角色，所有模板复用。
4. H3 生成时 `action_library` 的 style prompt 影响有限，H3 倾向输出统一"动漫 3D"。
5. 口型：H3 audio_ref 的条件是"语义级"音频对齐，不是音素级 viseme；不同台词长短/语速下
   嘴型漂移明显（用户实测）。

---

## 2. 口型同步方案（Lip-sync）

### 2.1 候选库对比

| 方案 | 分辨率 | 速度（3080Ti 12GB 预估） | 显存 | Windows | 许可 | 适配我们（动漫脸/中近景） | 结论 |
|---|---|---|---|---|---|---|---|
| **MuseTalk 1.5** | face 256² | 实时级（V100 30fps+；3080Ti 预计 30-60fps，≈0.02-0.04s/帧） | ~8-10GB | 官方支持 Windows（需 ffmpeg） | 代码 MIT，权重 [TMElyralab/MuseTalk](https://huggingface.co/TMElyralab/MuseTalk)（MIT tag） | 潜空间 inpaint，对风格化脸有一定鲁棒性；需人脸 bbox（可自实现，无 mmcv） | **首选** |
| Wav2Lip（开源版） | face 96² | 更快（~0.05s/帧） | ~4-6GB | 是 | 代码非商用限制（历史版本），商业化需 sync.so | 96² 上采样到 1024p 会糊；中景可用、特写差 | **快速回退** |
| LatentSync 1.5 | 512² | 慢（diffusion，~0.5-1s/帧） | ~18-20GB | 是 | 代码 MIT / 权重研究用途？ | 质量最好但 12GB 不可行 | 不用 |
| 2D viseme 口型 rig | 原分辨率 | CPU 实时 | 0 | 是 | 自研 | 与 2D 风格天生一对；但需要改变"对话镜"形态 | **风格化兜底 / V5.2 选配** |
| sync.so 云 API | — | 快 | 0 | 是 | 商业 API，按次计费 | 质量高但需外网+付费 | 备选（离线优先） |

参考：[MuseTalk 官方仓库](https://github.com/TMElyralab/MuseTalk)（实时 30fps+、256²、中文支持）、
[Wav2Lip 仓库](https://github.com/Rudrabha/Wav2Lip)（96²、非商用限制）、
[LatentSync](https://github.com/bytedance/LatentSync)。

### 2.2 选定方案（V5.0）

1. **MuseTalk 1.5 作为默认 lip-sync provider**，只处理"有台词的对话镜"（`shot.line` 非空），
   输入是 H3 归一化后的该镜视频 + 该镜 TTS 音轨 `work/audio/{sid}.wav`（V4 已保证音画同源、lead 0.10s 已烘进 wav）。
   - 不需要 mmcv/mmpose：自实现 face bbox（`face_alignment`/`mediapipe`/YuNet 三选一，按 anime 检测成功率选）；
   - 不需要 face-parse-bisent：用羽化椭圆"下颌+嘴"掩码贴回（对 1024×576 动漫脸足够）；
   - 25fps 对齐（MuseTalk 训练帧率），H3 归一化后即 25fps，避免插帧。
2. **Wav2Lip 回退**：MuseTalk 不可用（依赖/权重/人脸失败）时逐镜回退；仍失败则标记 `lipsync=skipped` 不阻断交付。
3. **2D viseme rig**（V5.2 选配）：对纯 2D 风格模板，对话镜可切换为"cutout + 口型帧 + 眨眼"动画，
   100% 同步、零 GPU；由模板 `lipsync.mode = musetalk | viseme | off` 控制。
4. **量化 QC**：引入 SyncNet 打分（`ByteDance/LatentSync` 的 `latentsync_syncnet.pt`），
   对每镜/全片算 LSE-C（越大越好）与 LSE-D（越小越好）；QC 记录 before/after，
   `lip_sync` 门限：`LSE-C_after ≥ 4.0` 且 `LSE-C_after ≥ LSE-C_before`（dry-run 后按实测校准）。
5. **集成位置**：`render_v3` 在 `normalize_clip` 之后、拼接之前逐镜处理；
   新增 CLI `--lipsync auto|musetalk|wav2lip|off`；临时文件清理策略；失败不阻断（QC 标记）。

### 2.3 风险

- 动漫脸的 face bbox 检测不稳 → 用多检测器投票 + 光流平滑 + 允许手工 bbox 覆盖；
- H3 侧面/遮挡镜头 → 跳过并标记（QC 只要求"有正脸的对白镜"参与）；
- MuseTalk 生成嘴部纹理与动漫画风轻微不一致 → 限制掩码区域只覆盖嘴唇+下巴、做颜色匹配；
- 显存与 H3 分时 → lip-sync 在 H3 全部渲染完后做（串行），或逐镜交替（H3 单镜 → 立即 lip-sync，模型驻留冲突时用 /free 切换）。

---

## 3. 视觉风格体系（V5）

### 3.1 架构

```
contract/v5/
  styles/<style_id>.json         # 风格定义（技法/调色/纹理/线条/角色处理/包装/转场/H3 prompt）
  scenes/<scene_id>.json         # 场景定义（分层绘制指令 + 机位 + 角色锚点 + 变体）
  characters/<char_id>.json      # 角色定义（人设/声音/外观/姿态/口型帧/风格变体）
  props/<prop_id>.json           # 道具（横幅/卷轴/文物/奖杯/手机/数据板…）
  templates/<template_id>.json   # V5 模板（风格 + cast + 场景路线 + 镜头语法 + 文案）
  packs/<pack_id>.json           # 风格包（风格+场景+角色打包，可整体替换）
  schema/*.schema.json           # JSON Schema
  custom/                        # 用户覆盖目录（优先级最高；示例 + README）
```

渲染原则：**风格 = 可组合的视觉原语**，不是单纯 prompt 后缀。
- `technique`：`ink_wash` / `woodblock` / `parchment_engrave` / `museum_gold` / `silk_gongbi` /
  `hologram` / `vhs` / `film_grain` / `noir` / `oil_impasto` / `baroque_candle` / `blueprint` /
  `collage_cutout` / `claymation` / `chalk_sketch` / `neon_toon`
- `palette`：每个风格一套 6-8 色 hex（背景/前景/强调/阴影/高光/纸纹）
- `texture`：paper_grain / canvas / scanline / halftone / clay_noise / chalk_dust / grid / bokeh
- `line`：brush_edge / woodcut / engrave / thick_outline / none / sketch
- `character_treatment`：`ink_brush` / `cutout_paper` / `clay` / `pixel` / `halftone` / `oil` / `chalk` / `toon`
- `packaging`：字幕样式（字体/底色/描边/竖排/印章）、人名条、章节卡、转场、片头片尾
- `audio`：BGM preset（古琴/合成器/爵士/管风琴…）、SFX pack（木鱼/磁带/烛火…）

**渲染器**：`pipeline/style_v5.py` + `pipeline/scene_render.py` + `pipeline/motion2d.py`
- `render_scene(scene_spec, style, size)`：分层 PIL 绘制（天空/远景/中景/地面/建筑/道具/特效/纸纹），
  支持 seed 变化与 16:9 构图；输出可分离的 parallax 图层（背景/中景/前景）。
- `render_character(char, style, pose, mouth, blink)`：按角色外观 spec 程序化绘制（或加载用户提供 PNG），
  按风格做后处理（墨线/剪纸/黏土/像素/半调/油画笔触/粉笔）。
- `motion2d.render_shot(scene, chars, camera, duration)`：分层视差推拉摇移 + 角色微动（点头/呼吸/眨眼）
  + 口型帧（viseme 时间轴）+ 粒子/雨/雪/烛光等特效；逐帧合成 → ffmpeg（25fps）。
- H3 仍保留为"电影感 B-roll"选配（每片 0-2 个 establishing 镜头），生成后套同风格滤镜/调色，
  **不用 H3 做对白**（对白由 2D rig 或 MuseTalk 后处理保证口型）。

### 3.2 16 种风格 × 模板（每种截然不同）

| # | 模板 | 风格 id | 视觉世界/技法 | 主色 | 标志性场景（示例） | 角色处理 | 转场/包装 | 音效/BGM |
|---|---|---|---|---|---|---|---|---|
| 1 | tonight_show 深夜秀 | `neon_toon` | 霓虹 3D 卡通演播厅：霓虹灯带、玻璃幕墙、体积光 | 青蓝+品红+琥珀 | 城市夜景天台、深夜演播厅、后台走廊、霓虹巷 | toon 厚描边+发光边 | 光带擦除/霓虹章节卡 | synthwave |
| 2 | history_talk 历史夜话 | `ink_chronicle` | 水墨长卷：宣纸、飞白、淡彩、印章 | 墨黑+赭石+朱砂 | **古地图、军议帐、宫殿、战场、渡口、关隘** | ink_brush 墨线 | 卷轴展开/竖排印章 | 古琴+战鼓 |
| 3 | classics 典籍 | `woodblock_classic` | 版刻/竹简：木刻线条、纸纹、朱印 | 纸黄+墨黑+朱红 | 竹简案头、刻书坊、藏书楼、诏书、书院 | woodcut | 刻版擦除/线装书页 | 木鱼+翻页 |
| 4 | history_mystery 历史悬案 | `parchment_case` | 羊皮纸考古档案：铜版画线、红线证据板 | 羊皮棕+铁锈红+青灰 | 档案库、发掘现场、地图墙、论证板、墓室 | engrave 细线 | 红线牵引/档案卡 | 弦乐悬疑 |
| 5 | national_treasure 国宝 | `museum_gold` | 博物馆金黑：大理石、展柜、聚光 | 黑金+玉青 | 展厅、展柜、修复室、文物特写、夜巡 | 金边剪影+实物特写 | 聚光开合/铭牌卡 | 编钟+管弦 |
| 6 | qipashuo 奇葩说 | `silk_arena` | 绢本设色辩论：工笔绢面、双屏对垒 | 绢米+石青+朱砂 | 绢本辩论场、观众席、双屏分镜、墨圈舞台 | gongbi 细描 | 绢面卷切/飞白分屏 | 琵琶+鼓点 |
| 7 | masked_singer 蒙面歌手 | `hologram_stage` | 全息演唱会：粒子网格、激光、镜面 | 紫+电光蓝+全息白 | 全息舞台、后台镜屋、观众星海、光隧道 | 全息描边+粒子 | 故障转场/全息字 | future bass |
| 8 | standup 脱口秀 | `vhs_late_show` | 80s VHS：扫描线、色差、砖墙霓虹 | 暖黄+洋红+青 | 喜剧俱乐部、砖墙、后台化妆间、街头 VHS | 复古色差描边 | 磁带跳动/录像字 | 合成器放克 |
| 9 | hot_ones 热辣对话 | `film_interview` | 暖光胶片访谈：16mm 颗粒、胶片边框 | 暖橙+奶油+深棕 | 访谈沙发、厨房、咖啡馆窗、胶片条 | 柔焦+胶片色 | 胶片烧边/场记板 | 爵士钢琴 |
| 10 | mystery 悬疑 | `noir_rain` | 黑色电影：百叶窗硬影、雨夜、高反差 | 黑白+冷蓝+琥珀 | 侦探办公室、雨巷、警局档案、审讯室 | 硬光剪影+雨丝 | 百叶窗转场/雨声 | 冷爵士+雨 |
| 11 | roundtable 圆桌 | `oil_salon` | 文艺复兴油画：厚涂、明暗对照、金框 | 深褐+暗金+象牙 | 油画圆桌、藏书厅、烛光长廊 | 油画笔触 | 画框推近/油画裂纹 | 大提琴 |
| 12 | traitors 叛徒 | `baroque_candle` | 巴洛克烛光古堡：哥特拱顶、烛火、天鹅绒 | 暗红+金+墨绿 | 古堡大厅、烛火走廊、石桌圆桌、地窖 | 蜡烛轮廓光 | 烛火转场/管风琴 | 管风琴+钟声 |
| 13 | shark_tank 创投 | `blueprint_lab` | 科技蓝图：蓝图网格、白板、数据可视化 |  blueprint 蓝+白+橙 | 路演会议室、蓝图墙、白板、原型实验室 | 线稿+数据标注 | 蓝图展开/参数弹窗 | 电子节拍 |
| 14 | taskmaster 任务大师 | `candy_collage` | 糖果拼贴综艺：纸片剪贴、贴纸、半调网点 | 糖果粉+黄+青 | 游戏舞台、道具台、排行榜、彩带雨 | 剪纸+贴纸 | 贴纸弹跳/彩带 | 卡通音效 |
| 15 | slow_life 慢生活 | `clay_diorama` | 黏土定格：手作质感、微缩景、移轴 | 陶土+鼠尾草绿+奶油 | 黏土厨房、花园、街角、微缩场景 | 黏土捏塑 | 定格跳动/手作字幕 | 尤克里里 |
| 16 | snl_sketch 周六夜现场 | `chalk_theatre` | 粉笔素描剧场：黑板、手绘线、舞台 | 黑板绿+粉笔白+红幕 | 粉笔舞台、素描背景、服装间、观众素描 | 粉笔+手绘 | 粉笔擦除/黑板卡 | 现场鼓点 |

> 完整风格 spec（技法参数、调色 hex、场景列表、字体、转场、H3 prompt）见 `contract/v5/styles/*.json`；
> 每个模板的场景路线（act → scene 列表）见 `contract/v5/templates/*.json`。

### 3.3 场景库（60+，分主题包）

- **历史包（history）**：`ancient_map`（古地图+行军路线+城池标记+罗盘+印章）、`war_council`（军帐+沙盘+地图+火把）、
  `battlefield`（远山+烽火+军旗+骑兵剪影+烟尘，多层视差）、`palace_hall`（宫殿+盘龙柱+屏风+宫灯）、
  `archive_vault`（藏书阁/档案库+卷轴架+梯子）、`lantern_market`（市井+灯笼+摊位）、`river_fleet`（渡口+战船+帆）、
  `great_wall`（关隘+城墙+烽火台）、`temple_court`（庙堂+香炉+幡）、`scholar_study`（书案+竹简+烛）、
  `excavation_site`（考古坑+网格+刷子）、`museum_hall`/`display_case`（博物馆）。
- **现代包（modern）**：`late_night_studio`、`neon_skyline`、`backstage`、`comedy_club`、`brick_wall`、
  `interview_sofa`、`kitchen_warm`、`cafe_window`、`noir_office`、`rainy_street`、`interrogation_room`、
  `castle_hall`、`candle_corridor`、`crypt`、`oil_roundtable`、`pitch_boardroom`、`blueprint_wall`、
  `whiteboard`、`game_stage`、`prop_table`、`leaderboard`、`clay_kitchen`、`clay_garden`、
  `chalk_stage`、`sketch_backdrop`、`costume_room`、`hologram_stage`、`mirror_room`、`audience_sea`。
- **通用**：`title_backdrop`（各风格片头）、`end_backdrop`、`transition_fx`。

场景 spec 示例（`ancient_map`）：
```json
{"id":"ancient_map","tags":["history","ink","parchment"],"size":[1600,900],
 "layers":[
   {"type":"paper","palette":"ink_chronicle","grain":0.5},
   {"type":"terrain_contours","seed":11,"color":"#5a4a3a","count":24},
   {"type":"rivers","color":"#6b7f8a","width":[2,5]},
   {"type":"city_markers","count":9,"size":14,"style":"seal"},
   {"type":"route_arrows","color":"#b23a2e","width":4,"arrowheads":true},
   {"type":"compass_rose","x":0.86,"y":0.12,"size":0.12},
   {"type":"seal_stamp","x":0.08,"y":0.88,"text":"史","color":"#b23a2e"},
   {"type":"vignette","strength":0.25}
 ],
 "camera_zones":{"wide":[0,0,1,1],"map_detail":[0.2,0.15,0.5,0.6],"route":[0.3,0.3,0.5,0.5]},
 "anchors":{"host":[0.18,0.9],"figure_a":[0.72,0.9]}}
```

### 3.4 角色库（24+）

- **历史（12）**：诸葛亮 `zhuge`、曹操 `caocao`、孙武 `sunwu`、秦始皇 `qinshihuang`、苏轼 `sushi`、李白 `libai`、
  武则天 `wuzetian`、花木兰 `mulan`、关羽 `guanyu`、张骞 `zhangqian`、郑和 `zhenghe`、司马迁 `simaqian`。
- **现代（12）**：老周 `laozhou`、阿栗 `ali`、大魏 `dawei`、甜妹 `tianmei`、学霸 `xueba`、胖子 `pangzi`、
  卷王 `juanwang`、冷面 `lengmian`、萌新 `mengxin`、制片 `producer`、机器人 `robot`、神秘嘉宾 `mystery_guest`。
- 每个角色：`persona`、`voice`（Breeze Voice Design instruction + seed，可自定）、
  `appearance`（发型/脸型/眉/眼/肤色/服装/配饰/身高比/头身比）、`poses`（front/side/back/point/think/surprise）、
  `mouth`（viseme 帧：闭/张/圆/扁/齿/唇），`style_overrides`（各风格下的线条/材质参数）。
- 用户可新增角色：提供角色 JSON + 透明 PNG 立绘（或用程序化外观字段）。

### 3.5 道具库（40+）

卷轴、竹简、印章、地图、军旗、火把、沙盘、青铜鼎、瓷瓶、玉器、展柜、聚光灯、麦克风、奖杯、按钮、
白板、蓝图、数据板、手机、咖啡杯、胶片盘、录音带、百叶窗、蜡烛、烛台、酒杯、面具、麦克风架、彩带、
黏土道具、粉笔、黑板擦、戏服、镜子、雪花屏电视等。

---

## 4. 用户自定义契约

### 4.1 目录与优先级

```
contract/v5/custom/            # 用户覆盖（git 忽略示例，不覆盖核心）
  styles/characters/scenes/props/templates/packs/*.json
```
优先级：`custom/` > `contract/v5/` 内建。ID 同名即覆盖（深合并：字典递归、数组替换）。
用户也可只在 custom 中放"覆盖片段"（partial），如只改某角色的 voice 或某模板的场景顺序。

### 4.2 校验器

`python -m pipeline.v5 --validate`：
- JSON Schema 校验（`contract/v5/schema/*.json`）
- 引用完整性：template.cast → character、scene → 存在；props 存在；style 存在
- 资源存在性：用户 PNG 立绘/场景图（可选）路径存在且带 alpha（若声明）
- 输出错误清单（ERR/WARN）+ 修复建议；CI 用 `--strict`

### 4.3 CLI / API / UI

- CLI：`python -m pipeline.v5 --list-styles|scenes|characters|templates`、
  `--validate`、`--render --template <id> --duration 40`、`--export-custom <id>`（导出内置为可编辑副本）
- API（backend FastAPI）：
  - `GET /api/v5/catalog`（styles/scenes/characters/templates 列表+摘要）
  - `GET/PUT /api/v5/{kind}/{id}`（读取/保存自定义 JSON，保存前校验）
  - `POST /api/v5/validate`、`POST /api/v5/preview/plate`（快速预览场景/角色渲染，不占 GPU）
- UI（V5.3，可选但计划）：`/v5` 页面：风格画廊、场景选择、角色卡、模板编排、实时预览、导出 JSON。

### 4.4 兼容性

- V5 与 V3/V4 并存：`outputs/templates_v5/` 新目录，V4 产物保留；`render_v3` 不动或加 `--v5` 分支；
- `contract/v5` 与 `contract/style_presets.json` 解耦；V5 引擎不读 V3 模板；
- 音色沿用 Breeze TTS 2（Voice Design），新角色给默认 instruction，用户可改；
- lip-sync 模块独立（`pipeline/lipsync.py`），V4 也可通过 `--lipsync` 使用（回填口型）。

---

## 5. 实施阶段

| 阶段 | 内容 | 产出 | 预估 |
|---|---|---|---|
| **V5.0 口型修复** | MuseTalk 1.5 环境/权重/推理封装；face bbox 自实现；逐镜 lip-sync；SyncNet 打分 | `pipeline/lipsync.py`、`docs/LIPSYNC_FEASIBILITY.md`、2 个 V4 片段前后对比、QC `lip_sync` | 半天（GPU 串行） |
| **V5.1 风格引擎** | `style_v5.py`/`scene_render.py`/`motion2d.py` + 原语库（墨/版刻/羊皮纸/油画/黏土/像素/半调/蓝图/粉笔/VHS/全息/胶片/剪纸/丝绢/烛光/霓虹） | 16 风格渲染器、场景库 60+、可预览 contact sheet | 1-2 天（并行） |
| **V5.2 角色与 2D 动画** | 24 角色 spec + 程序化立绘 + 口型帧 + 眨眼/呼吸/点头 + 角色在场合成 | `pipeline/characters_v5.py`、角色 contact sheet、viseme 动画 | 1 天（并行） |
| **V5.3 叙事与模板** | 16 个 V5 模板：分段/镜头/场景路线/台词脚本（历史类含地图与战场段） | `contract/v5/templates/*.json` + 16 套 storyboard | 1 天（并行） |
| **V5.4 渲染与剪辑** | `pipeline/render_v5.py`：TTS → motion2d 渲染 → lip-sync（对白镜）→ 时间线/ducking/字幕/包装 → QC | 16 部 30-45s 成片 + timeline + QC | 1-2 天（GPU 串行，motion2d 主要 CPU） |
| **V5.5 自定义与验收** | 校验器/CLI/API/UI + 用户示例包 + docs/V5_REPORT、V5_ACCEPTANCE | 可自定义交付 | 1 天 |

关键路径：V5.1/V5.2/V5.3 可并行（不同队友），V5.4 串行依赖。

---

## 6. 验收标准（V5）

1. **16/16 成片**：30-45s、1280×720/25fps、有音轨、无黑帧；每部 `final.mp4 + timeline.json + qc_report.json + style_report.json`。
2. **风格区分度**：16 部两两之间在（调色直方图、边缘/纹理特征、场景内容标签）上有显著差异；
   人工抽检 contact sheet：每部风格一眼可辨；历史 4 部必须出现地图/战争/史料类场景（≥2 个）。
3. **口型同步**：对白镜 `LSE-C_after ≥ 4.0` 且相对 before 提升；全片 lip-sync 覆盖率 ≥ 80% 对白镜（跳过镜需写明原因）。
4. **角色/场景库**：≥24 角色、≥60 场景、≥40 道具；模板可引用任意组合。
5. **自定义**：`--validate` 全绿；示例自定义风格/角色/场景/模板各 1 套通过校验并成功渲染；
   API 契约测试通过；文档齐全（`docs/V5_CUSTOMIZATION.md`）。
6. **回归**：V4 的 16 成片与验收不受影响（`pytest tests -q` 等全绿）。
7. **性能**：motion2d 渲染 ≥ 1 镜/10s（CPU），lip-sync ≤ 2× H3 时长；显存与 H3 分时无冲突。

---

## 7. 风险与对策

1. **程序化美术质量**：PIL 绘制可能"简陋" → 用多层视差、纹理叠加、光照/阴影、动态元素（雨/雪/烛火/粒子）、
   高质量字体与构图规则（三分/引导线）提升质感；关键镜头可用 SDXL 生成底图 + 风格后处理。
2. **MuseTalk 在动漫脸上失败** → bbox 多检测器投票 + 手工覆盖 + Wav2Lip 回退 + 2D viseme rig 兜底；
   QC 记录跳过原因，不阻断交付。
3. **16 风格工作量大** → 原语复用 + 并行实施 + 每风格只做 4-6 个标志性场景；先出 4 个标杆风格再批量套用。
4. **H3 风格污染**：对白镜不走 H3；B-roll 若用 H3，套风格直方图匹配/纹理后处理，或干脆不用 H3。
5. **Windows 依赖风险**（mmcv/编译）→ 已选无 mmcv 路径；所有依赖走独立 venv。
6. **用户自定义安全**：JSON 校验 + 尺寸/数量上限 + 图片格式校验；渲染前 dry-run。
7. **许可**：MuseTalk 代码 MIT/权重 MIT tag；Breeze TTS 权重非商用；Wav2Lip 开源版非商用；
   文档明确 demo 用途与商用路径（sync.so / 商用授权）。

---

## 8. 文档交付清单

- `docs/PLAN_V5_STYLE_AUDIO.md`（本文）
- `docs/LIPSYNC_FEASIBILITY.md`（lip-sync 实测与选型）
- `docs/V5_STYLE_GUIDE.md`（16 风格视觉规范：色板/字体/排版/构图/镜头语法）
- `docs/V5_CUSTOMIZATION.md`（用户自定义契约/示例/API）
- `docs/V5_REPORT.md` + `docs/V5_ACCEPTANCE.md`（实施与 QA 验收）
- `contract/v5/SCHEMA.md` + `contract/v5/README.md`
