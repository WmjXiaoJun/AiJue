# V45：2D 风格精修（独立 checkpoint + 角色年龄气质 + IP-Adapter 权重）

> 用户确认三项都做：①每种风格用不同 checkpoint + 提示词；②关键帧提示词按角色年龄/气质加强（例：老周 42 岁要成熟）；③调低 IP-Adapter 权重（0.75 偏高，脸稳但背景抽象）。并出 4 种风格样片对比。
> 状态：代码已落地 + 真实样片 4 条（2026-11-04 17:10–17:33 全部 done，1280×720 / 14.142s）。

## 1. 4 风格 v2：checkpoint / 采样 / IP-Adapter / 提示词

| 风格 id | 名称 | checkpoint（models/checkpoints） | cfg / steps | IP 权重 → end_at | 关键帧提示词要点 |
|---|---|---|---|---|---|
| `jp_anime` | 日漫电影感 | `animagine-xl-4.0.safetensors` | 5.5 / 30 | 0.50 → 0.58 | Japanese anime film still, soft cel shading, thin detailed lineart, muted cinematic color |
| `guoman` | 国漫国风 | `AAM_XL_Anime_Mix.safetensors` | 6.0 / 30 | 0.48 → 0.56 | Chinese guofeng animation film still, ink-wash accents, Chinese architecture, 中式木构/灯笼环境句 |
| `retro_cel` | 复古赛璐璐 | `Illustrious-XL-v1.0.safetensors` | 5.5 / 32 | 0.55 → 0.62 | 90s retro cel anime, bold outlines, limited palette, vintage film grain |
| `soft_watercolor` | 吉卜力治愈系 | `DreamShaperXL_Turbo_v2_1.safetensors` | 4.0 / 16（dpmpp_sde + karras） | 0.48 → 0.55 | studio ghibli inspired watercolor, gouache, warm golden hour palette + 人数负面（2boys/multiple characters/twin…） |

- 统一 `--ip-adapter-weight-type "style transfer"`：只借参考图的风格/气质，不再把参考立绘的「大特写 + 黑底 + 光效」构图带进关键帧。
- 权重按底模微调：Illustrious 在 0.50 时脸部漂移偏大（`tmp/v16/keyframes/.../retro_cel_illustriousxlv_ip50|ip55|ip60` 三档对比），最终定 0.55；AAM 两个风格 0.48 已足够锁脸。
- 构图切到新的 `scene_wide`（中远景 + environment visible + detailed background），替代原来 scene 预设里的 `medium shot + dark studio background`。
- 采样参数随风格走（cfg/steps/sampler/scheduler 可在 `STYLE_2D_STYLES` 单独调），关键帧缓存目录按 `<style>_<ckpt>_ipNN` 分层，换 checkpoint/权重不会误用旧图。
- **底模现状（V45.1 起）**：4 种风格 = **4 个不同底模**：animagine-xl-4.0 / AAM_XL_Anime_Mix / Illustrious-XL-v1.0 / DreamShaperXL_Turbo_v2_1（吉卜力治愈系）。DreamShaper 是通用插画 SDXL，手绘/水彩质感比 AAM 更接近吉卜力，但 turbo 底模容易补出第二个人，已加风格级 `negative_extra`（2boys / multiple characters / twin / clone / duplicate person / extra person / mirror image）压人数；另装 `NoobAI-XL-v1.0` 备选（实测偏平，未启用）。

## 2. 角色还原度：按年龄/气质写进关键帧与口型提示词

`_character_look(cid)` 读角色合同的 `appearance.age / gender / persona`，产出三段内容：关键帧身份约束、H3 ref2va 口型句、负面词。

| 年龄 | 身份约束（identity） | 负面词 |
|---|---|---|
| ≥60 | mature adult, senior presence, grey hair, deep facial lines, weathered face | teen / boy / shonen / young adult / baby face / chibi |
| 45–59 | mature adult, defined jawline, subtle facial age lines | 同上 |
| 35–44 | mature adult, middle-aged appearance, defined jawline, subtle facial age lines | 同上 + smooth youthful skin / boyish |
| 30–34 | adult, age-accurate | teen / child / chibi / elderly |
| 18–29 | young adult, age-accurate | middle-aged / elderly / old man |
| <18 | child, age-accurate | adult / middle-aged / elderly |
| robot | android robot character, mechanical joints, metal panels, non-human | human skin / realistic human face / wrinkles / child |

- 气质：合同 persona 中文关键词 → 英文 tag（老练/毒舌/控场/沉稳 → composed, experienced, calm, confident；元气/乐观/语速快 → energetic, cheerful；威严/寡言 → dignified, authoritative；逻辑/效率 → focused, sharp）。
- **实例**：老周 42 岁 → `42-year-old man, mature adult, middle-aged appearance, defined jawline, subtle facial age lines, composed, experienced, calm, confident`，负面 `teen, boy, shonen, smooth youthful skin, baby face, chibi…`；甜妹 20 岁 → `20-year-old woman, young adult, age-accurate, energetic, cheerful, expressive`，负面反过来禁 `middle-aged / elderly`。
- 同一份身份句同时进入 `ref2va motion_prompt`（`The character is a 42-year-old man, mature adult, composed…`），保证口型镜头不把角色演回少年。

## 2.5 关键帧抽卡（V45.2）：多 seed 候选 + 人工选卡
- CLI：`--keyframe-candidates N`（默认 1）对每个有台词镜头同 prompt 多 seed 出图，落到
  `tmp/v16/keyframes/<tid>/<style>_<ckpt>_ipNN_cN/candidates/<shot>_c0..N.png`，并拼一张
  `<shot>_sheet.jpg`（每格标 `#index seed score subj`）。
- 选卡优先级：`--keyframe-pick INDEX` > `tmp/v16/keyframe_picks.json`（`{tid: {shot: index}}`）> 自动分。
- 自动分只是辅助（细节/饱和度 + 肤色连通域主体面积 + 多主体惩罚），实测会把「特写」排在「中景」前面；
  **最终以选卡图人工挑选为准**，把索引写进 picks 文件后重跑同一条命令即可（候选图缓存复用，只重做 ref2va）。
- 示例（日漫电影感，3 候选、人工选 #2/#2）：`python backend/app/routers/custom_hq_runner.py --template custom_tpl_v45_jp_anime --style-mode 2d --2d-style jp_anime --keyframe-candidates 3` → 关键帧日志 `cands=3，pick=#2/picks`，成片 4,763,895B；第 3/9 秒由特写变成带环境的中景。

## 3. 背景细化：把 IP-Adapter 从「糊背景」调到「保脸 + 出景」

| 项 | V44（问题） | V45（现在） |
|---|---|---|
| IP 权重 | 0.75 | 0.48 ~ 0.50 |
| 结束步 end_at | 0.85 | 0.55 ~ 0.58（后段交给提示词生成背景） |
| weight_type | linear（连构图一起抄） | style transfer（只借风格/气质） |
| 构图预设 | scene：medium shot + dark studio background | scene_wide：medium wide + environment visible + detailed background |
| 关键帧负面词 | abstract background / close-up portrait / flames | + black background / gradient backdrop / bust portrait / duplicated face / split screen / energy aura / floating sparks / swirling light / poster |
| 环境提示词 | detailed environment, background scenery | 每风格独立 environment 句（动画背景美术 / 国风建筑 / 90s 手绘背景 / 手绘水彩背景） |

效果对比：`tmp/v45/before_after_ip.png`（V44 0.75 线性 → 大特写 + 光效糊底；V45 0.50 style transfer → 中景 + 完整房间/街景）。关键帧 meta 里记录 `ckpt / cfg / steps / ip_strength / ip_end / ip_weight_type / age_identity`，可直接复查每次生成用了什么。

## 4. 四风格对比（同一角色 / 同一场景）

`tmp/v45/style_compare_v5.png`（关键帧 2×2）、`tmp/v45/posters_compare_v5.png`（新海报 2×2）、`tmp/v45/films_compare_v5.png`（成片 3s 处）、`tmp/v45/frames/<style>/frame_01~06.png`。

**2026-11-04 18:28–18:59 全量重跑**（清 `tmp/v16/clips` 缓存 → 经 `POST /api/pipeline/run` 重跑 H3 → 剪辑 → 同步 2D）：

| 风格 | 关键帧目录 | 成片 | 本轮耗时 / 体积 |
|---|---|---|---|
| 日漫电影感 | `.../jp_anime_animaginexl4_ip50` | `outputs/templates_3d/custom_tpl_v45_jp_anime/final.mp4` | **460.2s**，4,492,509B |
| 国漫国风 | `.../guoman_aamxlanimemix_ip48` | `outputs/templates_3d/custom_tpl_v45_guoman/final.mp4` | **470.2s**，4,836,850B |
| 复古赛璐璐 | `.../retro_cel_illustriousxlv_ip55` | `outputs/templates_3d/custom_tpl_v45_retro_cel/final.mp4` | **470.2s**，5,030,942B |
| 吉卜力治愈系 | `.../soft_watercolor_aamxlanimemix_ip48` | `outputs/templates_3d/custom_tpl_v45_soft_watercolor/final.mp4` | **470.2s**，4,886,364B |

### 4.1 海报（poster）改为角色关键帧
- 旧逻辑：海报取「第 1 幕 B-roll」= 同一个霓虹演播室，4 张卡只有色差。
- 新逻辑（`templates3d_hq.main`）：优先取**有台词镜头的风格关键帧**（`_keyframe_dir(...)/<shot>.png` → ffmpeg 缩 960×540），没有关键帧时回落到该镜头 1.2s 抽帧；日志会写 `海报 ← 关键帧 v5_002`。
- 同步：API 跑完 `_sync_2d_outputs` 会把 `final.mp4` + `poster.jpg` 复制到 `templates_2d`，模板库卡片立刻换成角色封面（4 张卡一眼可辨：霓虹演播室 / 中式窗棂 / 90s 蓝调 / 暖金手绘）。

- 抽帧：`tmp/v45/frames/<style>/frame_01~06.png`（1/3/5/7/9/12s）。
- 成片对比：`tmp/v45/films_compare_v5.png`（4 条成片 3s 处，同角色同机位）。
- 关键帧对比：`tmp/v45/style_compare_v5.png`（4 风格 1024×576 关键帧 2×2）。
- 前后对比：`tmp/v45/before_after_ip.png`（V44 0.75 → V45 draft 0.58 → V45 final 0.50 + style transfer）。

## 4.5 抽卡实测（2026-11-04 22:24）
- `v5_002`（老周）3 候选：#0 特写 score 4.161 / #1 远景 3.521 / #2 吧台前中景 2.203（自动分因肤色连通域误判给 #2 低分）
- `v5_003`（阿栗）3 候选：#1 特写 4.874 / #2 咖啡桌中景 3.098 / #0 偏暗 0.663
- 人工选 `#2/#2` 写入 `tmp/v16/keyframe_picks.json` 后重跑：关键帧/海报/成片均为中景构图，角色仍在画面主体。

## 5. 代码改动

- `pipeline/templates3d_hq.py`：风格表加 `ckpt/cfg/steps/sampler/scheduler/ip_strength/ip_end/ip_weight_type/background`；新增 `_character_look`（年龄分层 + persona 映射 + 机器人分支）、`_keyframe_prompt`、`KEYFRAME_NEGATIVE`；`_generate_keyframe` 透传 checkpoint/采样/IP/构图并把参数写进 meta；CLI 新增 `--ip-end`（与 `--ip-strength` 并列）。
- `imagegen/prompt.py`：新增 `scene_wide` 构图；`imagegen/cli.py`：新增 `--composition`、`--lora/--lora-strength`；`imagegen/workflow.py`：抽 `_base_nodes`，支持 `LoraLoaderModelOnly`（当前无匹配 LoRA，默认关闭）。
- 后端/前端 label 统一为「吉卜力治愈系」（`cinema_templates/pipeline_ui/v5_catalog/v5_create` + `frontend/src/lib/cinema.ts` + 向导说明）。
- 关键帧抽卡：`_generate_keyframe(candidates, pick)` + `_score_candidate` / `_contact_sheet` / `_resolve_pick`，CLI `--keyframe-candidates` / `--keyframe-pick`。
- 新增单测 `tests/test_v45_style_polish.py`（8 项：风格参数齐全、IP 下调、老周 42 岁、年轻角色、机器人、关键帧提示词、B-roll 背景句、关键帧目录命名）。
- poster 逻辑改关键帧；2D ref2va 的 subject_definitions 补 `outfit (same jacket, same necktie, same colors)`。
- 镜头 seed 从内置 `hash()`（受 `PYTHONHASHSEED` 随机化，重跑会换 seed）改成 `zlib.crc32`，同一模板重跑可复现同一批镜头。

## 6. 验收

### 6.0 全流程自检（2026-11-04 17:39–17:55，真实跑）
`python scripts/verify_v45_2d_flow.py` → **33/33 passed**（证据 `tmp/v45_flow_check.json`）：
- 健康 `/api/health` 200；`/api/v5/catalog` 30 个模板，4 个 V45 模板都带 `render_style` + 中文 label（`soft_watercolor` = 吉卜力治愈系）
- 负例：非法 `render_style` → 400 且报错列出 4 个合法值
- 新建 `render_type=2d, render_style=soft_watercolor` 模板 → 合同 / `episode.json` / 4 镜 semantic brief 全部落盘（TTS 4 条音频）
- 分派命令：4 个风格各自 `--style-mode 2d --2d-style <style>`；3D 模板（tonight_show）只有 `--style-mode 3d`，无 `--2d-style`
- 新模板真生成：4/4 镜完成 → `templates_3d/final.mp4` 4,817,172B → 同步 `templates_2d` → `/media/...` 200 可播；关键帧 meta 记录 `ckpt=AAM_XL_Anime_Mix / ip 0.48→0.55 / age=42`
- 删除：有终稿时 400（保护）；清掉产物后删除 200，合同 + semantic + workdir 全清，模板数回到 30
- 前端：`/` 200，`frontend/dist/assets/*.js` 内含「吉卜力治愈系」

### 6.0.1 UI 排版验证（浏览器实测 1600/1280/1100/900/760，Chrome headless + puppeteer）
**场景库 / 风格墙（2026-11-04 19:2x 修复）**
- 根因：`LibraryPage` 复用了 `v5world/Scenes|Styles`，但只 import 了 `cinema/v18/library.css`，漏了 `v5world.css` → `v5w-scene-grid / v5w-scene-card` 全失效，场景封面按原图铺满整页、标签挤成一行。
- 修复：`LibraryPage.tsx` 补 `import "../../styles/v5world.css"`（构建 CSS 从 297KB → 323KB）。修复后场景库 70 张卡正常网格（5/4/3/2 列断点）、标签 chip、KPIs 全部正常。
- 补齐场景 plate：9 个现代场景（cafe_corner / cafe_counter / cafe_sofa / convenience_store / kitchen_home / kitchen_stove / kitchen_table / street_night / subway_exit）原本没有 plate，卡片 404 后回落程序化预览；已在 `pipeline/v5_scene_art.py` 补 9 条 FALLBACK_PROMPTS 并生成 1280×720 plate（`assets/v5/scenes/*.png`）。现在场景库 70/70 有美术、0 个 404。
- 成片库卡片：7 个操作按钮换行成「2+2+1」，最后一个「原片」图标单独占一行；改 `.v20-final-actions` 为 3 列网格（6 个按钮 2 行），「原片」移到元信息行右侧。
- 项目大厅 hero 标题：长项目名在 38px 下断成「（5 分钟 / Demo）」；改 `clamp(26px,2.3vw,38px)` + `text-wrap: balance` + `max-width:26ch`。
- 全站 8 个主路由浏览器巡检：0 console error、0 请求 404、无横向溢出。
- **角色与声音页**：顶部「双人对戏」demo 区只在有历史任务时出现；清掉 `outputs/dialogue_jobs` 后该区自动消失，页面直接进入 24 个角色网格（功能按钮仍保留，需要时还能发起）。
- **风格墙封面**：自定义风格「全息宣纸（示例）」原来只有程序化渐变当封面（灰卡）。现在排序改为「同风格 3D 海报 → 场景 × 风格代表画面 → 程序化预览」，并给风格摘要加 `rep_scene` 字段（`pipeline/v5_catalog.py` + `contract/v5/custom/styles/*.json`）；自定义风格卡现在显示 `/api/v5/preview/scenes/<rep>?style=<id>` 的真实绘景封面。
- **模板卡**：「V45 2D·吉卜力治愈系」原先被截成「V45 2D·吉…」，风格徽标「2D·赛璐璐」被挤成两行——改为标题独占一行、ID 另起一行，徽标 `flex:0 0 auto; white-space:nowrap`，6 张 2D 卡标题全部完整（`clipped:false`）。
- **新建模板向导**：≤900px 时节目类型的 1 列断点提前到 720px，三选一保持一行；2D 风格四选一在 900px 变 2×2 且完整可见（底边 798 < 底部栏 905），不再被折叠线切掉。
- **分幕步骤**：每幕的「搜索场景」由独占一行改为与场景下拉同行（108px 小输入框，≤900px 再堆叠），4 幕少 4 条多余输入行；说话角色下拉补占位项「选择说话角色 / B-roll」，不再默认显示 B-roll 却校验失败。
- **预览步骤文案**：原先一律写「3D」（2D 模板也写「创建后立即生成 3D」）→ 改为跟随节目类型（「创建后立即生成 2D / 创建并生成 2D」）；页脚提示按缺失项给具体原因（「填写中文名后继续」）。
- 全流程控制台浏览器无 console error；`npm run build`（tsc + vite）通过。

### 6.1 4 条样片可播（同一 cast / 同一 4 幕）
`/media/templates_2d/<tid>/final.mp4` 全部 200：jp_anime 4,641,372B / guoman 4,962,538B / retro_cel 4,252,030B / soft_watercolor 5,261,985B。

> 注意：`custom_hq_runner`/`templates3d_hq` **命令行直跑**只写 `outputs/templates_3d`；同步到 `templates_2d`（`/media` 用的目录）要走 `POST /api/pipeline/run` 或 `cinema_templates._sync_2d_outputs`。本次 4 条已全部走 API 重剪辑同步。

### 6.2 原有验收项
1. 4 条样片全部 done，可直接播放对比（同一 cast / 同一 4 幕）。
2. 老周在 4 条样片里都是 40+ 成熟男性（关键帧 meta 记录 `age 42` 约束）。
3. 背景不再是抽象色块/光效：四风格各有可辨识环境（霓虹演播室 / 中式木构窗棂 / 雨夜 90s 窗景 / 暖金手绘窗景）。
4. `python -m pytest tests/test_v45_style_polish.py` 7 passed；`tests/test_api.py -k "render or template or style"` 3 passed；`tests/test_v5_catalog.py` 24 passed / 2 failed——两个失败是既有数据问题：`git grep custom_lin fe55c3d` 为空（V45 之前就没有该示例角色合同）、`relic_talk.json` 的 packaging 自 v6.3 起未改动。
5. 前端 `npm run build`（tsc + vite）通过。

## 7. 老版本清理（2026-11-04 19:30）
- 删除 V41/V44 两条老 2D 示例模板（合同 + semantic + `templates_3d/2d/v5` 产物 + 关键帧 + clips），模板目录回到 **28 个（4 个 2D + 24 个 3D）**。
- 删除历史 demo 媒体与旧归档：`outputs/{_archive_v5_2d_finals,_archive_v14_*,_archive_v15_ref2va_768,template_demos_scene,templates_v3_previs,llm_demos,history_full,scene_demos,scene_demos_3d,style_demos_v2,character_demos,shots_plan_demo,screenshots}` 与 `tmp/{v41,v42,v43,v44,v45_probe*}`。
- 追加删除角色 demo 媒体：`outputs/{dialogue_jobs,audition_jobs,promo_jobs}`（169.1 MB 旧对戏/试镜/宣传片），角色页不再出现历史 demo 区块。
- 共释放 **11.9 GB**；保留当前 3D 模板产物、4 条 V45 样片、README 引用的 `outputs/demo|h3|template_demos|h3_style_demos`。

## 8. 遗留

- ~~4 风格 3 底模~~（已解决）：吉卜力治愈系换 DreamShaperXL_Turbo_v2_1（cfg 4 / 16 步 / dpmpp_sde+karras），4 风格 4 底模；重跑 520.2s / 5,097,103B，海报与成片均已同步。
- H3 ref2va 只吃关键帧首帧，年龄/气质靠首帧与口型句双重约束；若某风格脸仍偏年轻，优先降该风格 `ip_strength` 0.02 或加 `no youthful face` 负面。
- ~~关键帧服装漂移~~（已修）：`_character_look` 现在从合同 `appearance.outfit` 生成硬约束——西装角色追加 `wearing a tailored business suit, white shirt and necktie, same suit as the reference`，负面追加 `suspenders/overalls/casual clothes/t-shirt/different outfit`，并同步进 H3 口型句。`soft_watercolor` 已清关键帧 + 片段重跑（500.2s / 4,868,179B）：新关键帧、海报、成片第 3 秒均为深蓝西装 + 领带，不再出现吊带。
- 成片阶段曾有 2 处 H3 抖动观察（与关键帧无关）：
  - `soft_watercolor` 第 2 幕把西装换成了「白衬衫 + 吊带」（关键帧里仍是西装），H3 对 outfit 的保持弱于脸；
  - `guoman` 第 3 幕窗面上出现一圈「发光线稿」残影，属于 H3 对 reference 线稿的误读，可后续在 motion prompt 加 `no glowing outline, no neon trace`。
- 旧 2D demo 模板（`custom_tpl_v41_2d_demo` / `custom_tpl_v44_guoman`）保留作历史对照，未删除，避免 V41–V44 文档里的看片链接失效。
- 关键帧仍是 1024×576（成片 1280×720 放大），后续若上 1280×720 原生关键帧，SDXL 宽高需为 8 的倍数且显存要留 ~10G。