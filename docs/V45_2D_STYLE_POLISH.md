# V45：2D 风格精修（独立 checkpoint + 角色年龄气质 + IP-Adapter 权重）

> 用户确认三项都做：①每种风格用不同 checkpoint + 提示词；②关键帧提示词按角色年龄/气质加强（例：老周 42 岁要成熟）；③调低 IP-Adapter 权重（0.75 偏高，脸稳但背景抽象）。并出 4 种风格样片对比。
> 状态：代码已落地 + 真实样片 4 条（2026-11-04 17:10–17:33 全部 done，1280×720 / 14.142s）。

## 1. 4 风格 v2：checkpoint / 采样 / IP-Adapter / 提示词

| 风格 id | 名称 | checkpoint（models/checkpoints） | cfg / steps | IP 权重 → end_at | 关键帧提示词要点 |
|---|---|---|---|---|---|
| `jp_anime` | 日漫电影感 | `animagine-xl-4.0.safetensors` | 5.5 / 30 | 0.50 → 0.58 | Japanese anime film still, soft cel shading, thin detailed lineart, muted cinematic color |
| `guoman` | 国漫国风 | `AAM_XL_Anime_Mix.safetensors` | 6.0 / 30 | 0.48 → 0.56 | Chinese guofeng animation film still, ink-wash accents, Chinese architecture, 中式木构/灯笼环境句 |
| `retro_cel` | 复古赛璐璐 | `Illustrious-XL-v1.0.safetensors` | 5.5 / 32 | 0.55 → 0.62 | 90s retro cel anime, bold outlines, limited palette, vintage film grain |
| `soft_watercolor` | 吉卜力治愈系 | `AAM_XL_Anime_Mix.safetensors` | 6.5 / 32 | 0.48 → 0.55 | studio ghibli inspired watercolor, gouache, warm golden hour palette, 手绘水彩环境句 |

- 统一 `--ip-adapter-weight-type "style transfer"`：只借参考图的风格/气质，不再把参考立绘的「大特写 + 黑底 + 光效」构图带进关键帧。
- 权重按底模微调：Illustrious 在 0.50 时脸部漂移偏大（`tmp/v16/keyframes/.../retro_cel_illustriousxlv_ip50|ip55|ip60` 三档对比），最终定 0.55；AAM 两个风格 0.48 已足够锁脸。
- 构图切到新的 `scene_wide`（中远景 + environment visible + detailed background），替代原来 scene 预设里的 `medium shot + dark studio background`。
- 采样参数随风格走（cfg/steps/sampler/scheduler 可在 `STYLE_2D_STYLES` 单独调），关键帧缓存目录按 `<style>_<ckpt>_ipNN` 分层，换 checkpoint/权重不会误用旧图。
- **底模现状**：本机只有 3 个可用动漫/插画 SDXL 底模（animagine / AAM / Illustrious），4 种风格里国风与治愈共用 AAM，靠提示词 + 调色 + 负面词区分；另装了 `NoobAI-XL-v1.0` 备选（实测偏平，未启用）。若要 4 个完全独立底模，只需在风格表里改 `ckpt` 字段。

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

## 4. 四风格对比（同一角色 / 同一场景 / 同一 seed）

`tmp/v45/style_compare_v5.png`（2×2 拼图）+ `tmp/v45/frames/<style>/frame_01~06.png`（成片抽帧）。

| 风格 | 关键帧 | 成片 | 耗时 |
|---|---|---|---|
| 日漫电影感 | `tmp/v16/keyframes/custom_tpl_v45_jp_anime/jp_anime_animaginexl4_ip50` | `outputs/templates_3d/custom_tpl_v45_jp_anime/final.mp4` | **272.8s**，4,641,372B |
| 国漫国风 | `.../guoman_aamxlanimemix_ip48` | `outputs/templates_3d/custom_tpl_v45_guoman/final.mp4` | **~267s**，4,962,538B |
| 复古赛璐璐 | `.../retro_cel_illustriousxlv_ip55` | `outputs/templates_3d/custom_tpl_v45_retro_cel/final.mp4` | **289s**（IP 0.55 复跑），4,252,030B |
| 吉卜力治愈系 | `.../soft_watercolor_aamxlanimemix_ip48` | `outputs/templates_3d/custom_tpl_v45_soft_watercolor/final.mp4` | **~517s**，5,261,985B |

- 抽帧：`tmp/v45/frames/<style>/frame_01~06.png`（1/3/5/7/9/12s）。
- 成片对比：`tmp/v45/films_compare_v5.png`（4 条成片 3s 处，同角色同机位）。
- 关键帧对比：`tmp/v45/style_compare_v5.png`（4 风格 1024×576 关键帧 2×2）。
- 前后对比：`tmp/v45/before_after_ip.png`（V44 0.75 → V45 draft 0.58 → V45 final 0.50 + style transfer）。

## 5. 代码改动

- `pipeline/templates3d_hq.py`：风格表加 `ckpt/cfg/steps/sampler/scheduler/ip_strength/ip_end/ip_weight_type/background`；新增 `_character_look`（年龄分层 + persona 映射 + 机器人分支）、`_keyframe_prompt`、`KEYFRAME_NEGATIVE`；`_generate_keyframe` 透传 checkpoint/采样/IP/构图并把参数写进 meta；CLI 新增 `--ip-end`（与 `--ip-strength` 并列）。
- `imagegen/prompt.py`：新增 `scene_wide` 构图；`imagegen/cli.py`：新增 `--composition`、`--lora/--lora-strength`；`imagegen/workflow.py`：抽 `_base_nodes`，支持 `LoraLoaderModelOnly`（当前无匹配 LoRA，默认关闭）。
- 后端/前端 label 统一为「吉卜力治愈系」（`cinema_templates/pipeline_ui/v5_catalog/v5_create` + `frontend/src/lib/cinema.ts` + 向导说明）。
- 新增单测 `tests/test_v45_style_polish.py`（7 项：风格参数齐全、IP 下调、老周 42 岁、年轻角色、机器人、关键帧提示词、B-roll 背景句）。

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

### 6.1 4 条样片可播（同一 cast / 同一 4 幕）
`/media/templates_2d/<tid>/final.mp4` 全部 200：jp_anime 4,641,372B / guoman 4,962,538B / retro_cel 4,252,030B / soft_watercolor 5,261,985B。

> 注意：`custom_hq_runner`/`templates3d_hq` **命令行直跑**只写 `outputs/templates_3d`；同步到 `templates_2d`（`/media` 用的目录）要走 `POST /api/pipeline/run` 或 `cinema_templates._sync_2d_outputs`。本次 4 条已全部走 API 重剪辑同步。

### 6.2 原有验收项
1. 4 条样片全部 done，可直接播放对比（同一 cast / 同一 4 幕）。
2. 老周在 4 条样片里都是 40+ 成熟男性（关键帧 meta 记录 `age 42` 约束）。
3. 背景不再是抽象色块/光效：四风格各有可辨识环境（霓虹演播室 / 中式木构窗棂 / 雨夜 90s 窗景 / 暖金手绘窗景）。
4. `python -m pytest tests/test_v45_style_polish.py` 7 passed；`tests/test_api.py -k "render or template or style"` 3 passed；`tests/test_v5_catalog.py` 24 passed / 2 failed——两个失败是既有数据问题：`git grep custom_lin fe55c3d` 为空（V45 之前就没有该示例角色合同）、`relic_talk.json` 的 packaging 自 v6.3 起未改动。
5. 前端 `npm run build`（tsc + vite）通过。

## 7. 遗留

- 4 风格 3 底模（见 §1）；要完全一一对应可补一个 SDXL 动漫底模并改风格表 `ckpt`。
- H3 ref2va 只吃关键帧首帧，年龄/气质靠首帧与口型句双重约束；若某风格脸仍偏年轻，优先降该风格 `ip_strength` 0.02 或加 `no youthful face` 负面。
- 成片阶段仍有 2 处观察到的 H3 抖动（与关键帧无关）：
  - `soft_watercolor` 第 2 幕把西装换成了「白衬衫 + 吊带」（关键帧里仍是西装），H3 对 outfit 的保持弱于脸；
  - `guoman` 第 3 幕窗面上出现一圈「发光线稿」残影，属于 H3 对 reference 线稿的误读，可后续在 motion prompt 加 `no glowing outline, no neon trace`。
- 旧 2D demo 模板（`custom_tpl_v41_2d_demo` / `custom_tpl_v44_guoman`）保留作历史对照，未删除，避免 V41–V44 文档里的看片链接失效。
- 关键帧仍是 1024×576（成片 1280×720 放大），后续若上 1280×720 原生关键帧，SDXL 宽高需为 8 的倍数且显存要留 ~10G。