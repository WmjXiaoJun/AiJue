
> 声音：每个角色使用 `contract/v5/voices.json` 的固定 voice preset（voice_id），首次生成声音锁后跨镜/跨集 voice clone 一致；修改与试听见 `docs/V5_VOICES.md`。
# V5 角色库（24 角色 / 程序化立绘 / 口型 viseme）

> 契约：`docs/V5_INTERFACES.md` §3（函数签名不得改）；规划：`docs/PLAN_V5_STYLE_AUDIO.md` §3.4。
> 实现：`pipeline/characters_v5.py`（纯 PIL/numpy，无 GPU/网络/第三方美术素材）；数据：`contract/v5/characters/*.json`。
> 预览：`tmp/v5_char_sheet.py` → `tmp/v5_characters/`。校验：`python -m pipeline.v5_cli validate`（见 §8）。

## 1. 文件与产物

| 路径 | 内容 |
|---|---|
| `contract/v5/characters/{id}.json` | 24 个内建角色（历史 12 + 现代 12）；`custom/characters/` 同名深合并覆盖（字典递归、数组替换） |
| `pipeline/characters_v5.py` | `load_characters` / `render_character` / `mouth_states` / `viseme_from_text` / `viseme_timeline` / `render_pose_sheet` |
| `tmp/v5_char_sheet.py` | 预览生成脚本：24 角色 × pose sheet、8 风格对比、全员总览、脸部特写、口型对比 |
| `tmp/v5_characters/` | 预览产物（见 §7） |
| `contract/v5/schema/character.schema.json` | 角色 JSON Schema（`validate` 以此为准） |

## 2. 24 角色清单

| id | 名称 | 时代 | 视觉识别点（发型/服装/配饰/眼型） | voice seed |
|---|---|---|---|---|
| zhuge | 诸葛亮 | 历史 | 纶巾 + 白底蓝缘汉服 + 羽扇；狭长眼、长髯 | 20262001 |
| caocao | 曹操 | 历史 | 王冠 + 暗红黑汉服 + 佩剑；锐眼、络腮胡 | 20262002 |
| sunwu | 孙武 | 历史 | 束发 + 墨绿汉服 + 卷轴/剑；平直眼、短须、额带 | 20262003 |
| qinshihuang | 秦始皇 | 历史 | 冕冠 + 黑金帝王袍 + 佩剑/玉佩；冷眼、长髯 | 20262004 |
| sushi | 苏轼 | 历史 | 幞头 + 米白长袍（绿腰带）+ 笔/卷；温和眼、颏须 | 20262005 |
| libai | 李白 | 历史 | 披发 + 白底青缘汉服 + 酒壶/剑；亮眼、短须、美人痣 | 20262006 |
| wuzetian | 武则天 | 历史 | 高髻 + 凤冠 + 红金帝王袍 + 玉佩/团扇；丹凤眼、美人痣 | 20262007 |
| mulan | 花木兰 | 历史 | 马尾 + 红甲盔 + 剑/弓；坚定眼、雀斑 | 20262008 |
| guanyu | 关羽 | 历史 | 披发 + 绿甲 + 额带 + 青龙刀；丹凤眼、长髯、刀疤眉 | 20262009 |
| zhangqian | 张骞 | 历史 | 束发 + 毛皮帽 + 棕旅袍 + 节杖/卷轴；沉稳眼、短须 | 20262010 |
| zhenghe | 郑和 | 历史 | 官帽 + 深蓝官袍 + 罗盘/卷轴；暖眼、短须 | 20262011 |
| simaqian | 司马迁 | 历史 | 发髻（无帽）+ 米灰长袍（棕带）+ 笔/卷；深邃眼、黑眼圈、长髯 | 20262012 |
| laozhou | 老周 | 现代 | 背头 + 深蓝西装（金领带）+ 手表/麦克风；锐眼、胡茬、笑纹 | 20262013 |
| ali | 阿栗 | 现代 | 双马尾 + 紫卫衣（青蓝边）+ 耳机；大圆眼、雀斑 | 20262014 |
| dawei | 大魏 | 现代 | 光头 + 黑夹克（红衬衫）+ 眼镜；细长眼、胡须、皱纹 | 20262015 |
| tianmei | 甜妹 | 现代 | 双丸子头 + 粉色连衣裙 + 发夹/手机；大眼、雀斑、腮红 | 20262016 |
| xueba | 学霸 | 现代 | 整齐短发 + 浅蓝开衫 + 圆框眼镜/书；圆眼 | 20262017 |
| pangzi | 胖子 | 现代 | 板寸 + 绿 T 恤 + 零食；眯眯眼（笑眼）、八字胡、加宽体型 | 20262018 |
| juanwang | 卷王 | 现代 | 两侧铲青 + 白衬衫（蓝领带）+ 笔电/咖啡；疲惫眼、黑眼圈 | 20262019 |
| lengmian | 冷面 | 现代 | 大背头 + 黑高领 + 墨镜；面无表情 | 20262020 |
| mengxin | 萌新 | 现代 | 波波头 + 浅蓝oversize卫衣 + 双肩包/手机；大圆眼、雀斑 | 20262021 |
| producer | 制片 | 现代 | 低发髻 + 深灰西装（金边）+ 场记板/耳麦；锐眼、干练 | 20262022 |
| robot | 机器人 | 现代 | 金属方头 + LED 蓝眼 + 天线 + 金属躯干；电子嘴（viseme 条） | 20262023 |
| mystery_guest | 神秘嘉宾 | 现代 | 兜帽斗篷 + 面罩 + 发光眼；始终不露脸 | 20262024 |

## 3. 字段说明（对照 `character.schema.json`）

| 字段 | 说明 |
|---|---|
| `id` / `label` / `era` | id 必须与文件名一致（`^[a-z][a-z0-9_]{1,60}$`）；era = `history` / `modern` |
| `persona` | LLM 台本/人设描述（中文） |
| `role_pool` | 可承担的模板角色：`host` / `figure_a` / `guest_b` / `narrator` / `expert` … |
| `voice.provider` | `breeze`（Breeze TTS 2 Voice Design） |
| `voice.instruction` | 中文音色描述（性别/年龄/音色/语气），如「40岁男性，清朗沉稳，略带沙哑的书卷气」 |
| `voice.seed` | 唯一整数（本库 20262001–20262024，按清单顺序） |
| `voice.cfg_scale` | 音色设计引导强度 3.5–5.0 |
| `appearance.gender` | `male` / `female` / `neutral` / `other`（robot=neutral，由 outfit.type=robot 触发机器人绘制） |
| `appearance.age` | 1–150；≥45 自动加皱纹/法令纹，≤26 更年轻 |
| `appearance.skin/hair/eye` | `#rrggbb` |
| `appearance.hair_style` | `ancient_guan` / `topknot` / `scholar_bun` / `high_chignon` / `long_loose` / `ponytail` / `twin_tail` / `twin_bun` / `bob` / `short_swept` / `neat_short` / `crew_short` / `under_cut` / `slick_back` / `bald` / `curly` / `hood` / `robot_antenna` |
| `appearance.brow` | `soft` / `long` / `thick` / `angled` / `straight`（眉毛粗细与角度） |
| `appearance.beard` | `none` / `moustache` / `short` / `full` / `long_goatee` / `braided` / `stubble` |
| `appearance.outfit` | `{type, main, accent, hat?, pants?, shoes?}`；type 支持 hanfu / robe / imperial_robe / official_robe / traveler_robe / armor / suit / blazer / jacket / hoodie / oversize_hoodie / tee / shirt / cardigan / turtleneck / dress / gown / cloak / robot |
| `outfit.hat` | `lun_jin` / `futou` / `official_hat` / `eunuch_hat` / `crown` / `phoenix_crown` / `helmet` / `headband` / `fur_hat` / `king_hat` / `hood` / `none` |
| `appearance.accessories` | feather_fan / sword / scroll / brush / book / glasses / sunglasses / headphones / headset / mic / trophy / clipboard / laptop / coffee / phone / snack / compass / staff / bow / guandao / flagon / jade_pendant / hairclip / backpack / watch / mask / glow / antenna / led_panel |
| `appearance.eye_style` | `sharp` / `narrow` / `cold` / `phoenix` / `steady` / `warm` / `bright` / `determined` / `round` / `round_big` / `big` / `smiley` / `tired` / `deadpan` / `deep` / `led`（眼型/眼白/瞳孔/高光/睫毛/眼袋） |
| `appearance.face_ratio` | 脸宽系数 0.82–1.22（胖瘦/脸型） |
| `appearance.marks` | `smile_lines` / `freckles` / `scar_brow` / `beauty_mark` / `dark_circles` 等 |
| `appearance.head_ratio` / `chibi` / `body_width` | 头身比 / Q 版程度（0–0.5）/ 体型加宽（默认 1.0） |
| `poses` | `["front","side","point","think"]`（渲染器支持这 4 种姿态） |
| `mouth` | viseme 帧列表（固定 7 种，见 §6） |
| `style_overrides` | `{style_id: {line, shade, ...}}`：按风格覆盖线宽等（仅允许引用已存在的 style id） |
| `image` / `image_alpha` | 可选：用户透明 PNG 立绘，优先于程序化外观（V5.3 起用于自定义角色） |

## 4. 程序化立绘渲染（`render_character`）

### 4.1 绘制层序（透明底 RGBA）
1. 地面椭圆投影 → 2. 后发/长袍/披风背层 → 3. 腿/裤/裙/腿甲 + 鞋 → 4. 躯干与服装（交领/翻领/胸甲/兜帽/机器人面板，含衣褶与侧光）→ 5. 手臂（按 pose 不同）→ 6. 脖子/头/耳朵 → 7. 胡须（**先画胡须、后画五官**，保证嘴可见）→ 8. 五官（眉/眼/鼻/腮红/皱纹/雀斑/疤/痣/口型）→ 9. 前发/帽子 → 10. think 托腮手（盖在脸前）→ 11. 配饰 → 12. 披风兜帽阴影。

### 4.2 姿态差异
- `front`：正面，双臂自然下垂、双手圆润；
- `side`：半侧脸——头偏转（head_cx 右移）、单眼可见、侧面鼻梁外凸、单侧手臂、身体收窄；
- `point`：右臂前上抬 30°、指尖伸出（食指），身体微转；
- `think`：左臂上臂下垂、前臂抬到下巴、手托腮（绘制在脸部之后，形成遮挡关系），眉/眼参数配合。

### 4.3 景别/画布
- `size` = (w, h) 输出像素；自动 2× 超采样（面积 ≤1.2M px 时）再 LANCZOS 下采样，抗锯齿；
- 头身比：`heads = clamp(5.6 - 3.0*chibi, 3.3, 5.6) / head_ratio`，`chibi=0` 约 5.6 头身、`chibi=0.35` 约 4.5 头身；
- 角色脚底锚在画面 96.5% 高度，与 `motion2d` 的 anchor 对齐；单角色渲染 **~90ms @512×768**（目标 ≤0.2s）。

### 4.4 风格后处理（`character_treatment`）
优先调用 `pipeline/style_v5.py::apply_finish(img, style, kind="character")`（已落盘，实测 49–90ms，保留 alpha）；
缺失/失败时用内置简化版（`_finish_builtin`），至少保证以下差异：

| treatment | 效果 |
|---|---|
| `toon` / `neon_toon` | 高饱和 + 暗部描边，平涂卡通 |
| `ink_brush` | 宣纸淡彩底 + 保本色 + 墨线勾边 + 纸纹噪点（不再黑剪影） |
| `woodcut` | 高对比 + 5 级色调 + 斜排线（刻版感） |
| `engrave` | 羊皮纸色 + 细排线（铜版画感） |
| `cutout_paper` | 剪贴纸片硬阴影（偏移投影）+ 白纸边 + 纸纹 |
| `clay` | 柔化 + 顶部受光 + 黏土噪点 |
| `pixel` | 7× 像素化 + 6 级量化 + 硬 alpha |
| `halftone` | 印刷网点 + 强调色网点 |
| `chalk` | 去饱和 + 白色粉笔颗粒 + 纸底 |
| `candle` / `baroque` / `oil` | 暖色偏移 + 边缘轮廓光 + 底部压暗 + 高对比 |

`style_overrides[style_id]` 可乘性地调整线宽（`line`）等。

**跨镜身份一致性修正（2026-10-26 treatment 修复）**：
- **洗白修正（ink_brush/engrave/woodcut/cutout_paper）**：`_tinted()` 亮度自适应——暗部（L<0.35）纸色融合量 ×0.12（保留 ≥85% 原色）；hair / outfit.main / accent / pants 等身份主色走 `key=True` 通道，纸色融合 ≤0.12（老周不再白发、阿栗紫帽不再变浅紫、大魏黑西装不再变浅灰）。
- **finish 后身份回填**：`_finish_preserve_identity()` 用原图亮度 + 角色 hair/outfit 主色做 masking，对风格化结果回填 ≥0.85 原色；同时把「原图非白、风格后近白」的伪白像素还原（修 silk/clay 外圈白边）。
- **chalk 专用**：`_finish_chalk()` 不用 style_v5 的灰化 chalk，改为「黑板绿底 + 粉笔白填充 + 深绿加粗描边 + 深色五官 + chalk dust 颗粒」。
- **暖调修正**：`_finish_warm_correct()` 烛光/油画把底部压暗 0.28→约 0.10，左右轮廓光 ×1.5（oil 再减 1/3）。
- **边缘去白边**：`_finish_matte_cleanup()` 轻微收 alpha + 边缘 RGB 预乘压暗，避免合成到深色场景时出现白圈。
- **渲染缓存**：`render_character()` 按（角色外观签名 + 风格签名 + pose/mouth/blink/size）LRU 缓存 24 张；首帧约 140–200ms（512×768，含 style_v5 finish），**缓存命中 ≈0.4ms**；数据热更新后调用 `clear_cache()`。

## 5. 口型与 viseme

### 5.1 `mouth_states()`（契约固定顺序）
`["closed", "a", "o", "e", "i", "u", "m"]`

### 5.2 口型形状与像素宽度（实测，300×450 半身，角色 ali）
| viseme | 形状 | 设计宽度系 | 实测嘴宽 px | 相对 closed |
|---|---|---|---|---|
| closed | 抿线 + 下唇 | 0.55 | 17 | 1.00 |
| a | 纵向大张（露上齿/舌） | 0.92 | 32 | **1.88** |
| o | 圆口 | 0.72 | 20 | 1.18 |
| e | 横向张口露齿（最宽） | 1.10 | 36 | **2.12** |
| i | 窄口（露齿） | 0.62 | 12 | 0.71 |
| u | 小圆口（嘴唇前突） | 0.58 | 12 | 0.71 |
| m | 抿唇鼓起 | 0.80 | 25 | 1.47 |
→ **最宽/最窄比 2.12 ≥ 1.6 目标**（`tmp/v5_characters/summary.json` 的 `mouth_metrics` 每次自检；阈值 `ok: true`）。
嘴部中心位于脸部中线、`head_cy + 0.285*head_h`（随头身比缩放）。

### 5.3 `viseme_from_text(text)` / `viseme_timeline(text, duration, lead=0.10)`
- 中文 → pypinyin（主环境若安装）→ 声母/韵母规则 → viseme；**当前运行环境未安装 pypinyin**，按契约用「字符哈希确定性轮换」兜底（同一文本恒定输出，便于复现；装 `pypinyin` 后自动切换为音素级映射，无需改代码）。
- 映射规则（韵母 → viseme）：`a/ia/ua/ai/ao(部分)/an/ang/ian/iao/uan/uang…→a`；`o/uo/ao/iong/ong→o`；`e/ei/ie/er/en/eng→e`；`i/in/ing→i`；`u/ou/iu/un→u`；声母 `b/p/m → m`；标点/空格 → `closed`。
- `viseme_timeline` 返回**绝对秒**：`[{"t0","t1","viseme"}]`，时间轴起点 0，台词从 `lead`（默认 0.10s）开始，结束于 `lead + duration`；标点按停顿权重（，0.65 / 。1.35 / 空格 0.35…）分摊时长，相邻同口型合并。示例：
  ```
  viseme_timeline("欢迎来到造片局，今天聊AI。", 4.0)
  → [{"t0":0.1,"t1":0.386,"viseme":"e"}, ..., {"t0":3.9,"t1":4.1,"viseme":"closed"}]
  ```

## 6. 如何新增角色

1. 在 `contract/v5/custom/characters/` 新建 `custom_<id>.json`（新建 id 必须以 `custom_` 开头；或与内建 id 同名做**覆盖**，可只写要改的片段，深合并）。
2. 最小必填：`id`、`label`、`role_pool`、`voice{provider,instruction}`、`appearance{gender,age,skin,hair,hair_style,eye,outfit{type,main,accent}}`；建议同时给 `persona`、`poses`、`mouth`、`style_overrides`。
3. 校验（Schema + 引用 + custom id 规则）：
   ```
   python -m pipeline.v5_cli validate            # 全目录
   python -m pipeline.v5_cli list-characters     # 查看合并后的角色
   python -m pipeline.v5_cli preview --kind characters --id <id> --style neon_toon --out tmp/v5_preview/x.png
   ```
4. 角色立绘如需外部素材：给 `image`（透明 PNG，相对仓库根或 `contract/v5/`）、`image_alpha`（默认 true）；否则用程序化外观字段。
5. 新角色若用新发型/服装/配饰/眼型：`pipeline/characters_v5.py` 的 `_draw_hair_*` / `_draw_torso`（outfit.type 分支）/ `_draw_accessories`（配饰字典）/ `_draw_eye`（eye_style）内扩展，未知值自动回退到安全默认，不会崩。

## 7. 预览产物（`tmp/v5_char_sheet.py`）

```
python tmp/v5_char_sheet.py            # 全量：24 pose sheets + 3 对比图 + 口型自检
python tmp/v5_char_sheet.py --quick    # 只出对比图（更快）
```

| 产物 | 说明 |
|---|---|
| `tmp/v5_characters/{id}_sheet.png` | 每角色 contact sheet：正/侧/指/思考 4 姿态 + 7 口型 + 眨眼 + 标注 |
| `tmp/v5_characters/style_compare_8.png` | **4 历史风格（ink_chronicle/woodblock_classic/parchment_case/museum_gold）+ 4 现代风格（neon_toon/film_interview/noir_rain/clay_diorama）** × 代表角色（zhuge/laozhou/tianmei）8 风格对比 |
| `tmp/v5_characters/all_characters.png` | 24 角色正面总览（6×4，toon_default，便于看服装/配色差异） |
| `tmp/v5_characters/face_grid.png` | 24 角色脸部特写（证明五官可辨，不靠衣服） |
| `tmp/v5_characters/mouth_compare.png` | 同一角色 7 口型 + 眨眼特写 |
| `tmp/v5_characters/summary.json` | 渲染耗时、`mouth_metrics`（含 `ok:true`）、风格清单 |

历史 4 风格 JSON 尚未落盘时，预览用 `tmp/v5_char_sheet.py` 内置的同名 style dict 兜底；落盘后自动改用 `contract/v5/styles/*.json`。

## 8. 校验与当前状态

- 角色 Schema/引用校验：**24/24 通过**（`v5_catalog.validate_item("characters", ...)`）。
- 全 catalog 校验：`python -m pipeline.v5_cli validate` → **errors=0**（16 个风格 + 场景 + 模板齐备后；历史 12 角色的 4 个历史风格 `style_overrides` 已恢复）。
- 渲染冒烟：`tmp/v5_char_smoke.py` 25/25 成功；预览 24 张 pose sheet + 8 风格对比 + 脸部特写 + 口型对比全部生成。

## 9. TODO / 限制

- `side` 为「半侧脸」而非严格 90° 侧脸（程序化 2D 立绘折中）；若 motion2d 需要真侧身可再加 `back`/`profile` 姿态。
- 程序化美术为「可交付动画立绘」级别（非 SDXL 写实）；关键镜头可用用户 `image` 立绘覆盖。
- 许可：**全部由代码程序化生成，无任何第三方美术素材/贴图/模型**；仅预览标题文字使用系统字体（`pipeline/fonts.py` 的 `config.FONT_CANDIDATES`），不下载字体。
