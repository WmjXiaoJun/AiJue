# 规划：模板差异化风格系统 + LLM 内容生成（本地 / API Key）

> 目标：① 16 个模板不再共用一套视觉，各自有辨识度（3D 动漫 / 2D 国风 / 黏土 / 霓虹 / 黑色电影 / 复古等）；② 台本/分镜/标题/多语等内容由 LLM 生成，支持本地 Ollama 与 OpenAI 兼容 API（带 Key 配置）；先规划、后升级。

## 一、现状与问题
- 当前风格只有「3D 动漫 / 2D 动漫」两档，16 模板共用极少数场景 plate，模板辨识度不足；
- 台本仍靠手写/模板样例，没有 LLM 生成入口；Ollama 只用于本地翻译；
- H3 3D 路径已验证（tonight_show_3d），但 3D 角色资产有道具残留，需要按风格逐模板精修。

## 二、总体架构
### 2.1 风格系统 v2（模板级风格 Profile）
- `contract/style_presets.json`：定义 12 个基础风格 preset（prompt 前缀/负向词/色板/材质/光线/运镜/转场/调色/字体包装）；
- `contract/templates_style.json`（或并入 style_presets）：16 个模板各自绑定 preset + 模板级覆盖（色调、转场、镜头节奏、包装样式）→ **每个模板都有独立视觉参数**；
- 资产命名：`outputs/scene_plates/{scene}__{style}.png`、`outputs/characters/{id}/{style}/front.png`；缺失自动回退，但要求 16 模板全部有专属资产才算通过；
- 渲染：pipeline/style_v2.py 解析 profile → 选 plate/cutout → H3 prompt 注入风格前缀 → 调色/转场/包装按 profile；render `--style-profile`；
- 验收：16 模板各出 30-45s demo，抽帧对比「一眼能看出不同风格」。

### 2.2 LLM 内容生成（本地 + API Key）
- 抽象层：`backend/app/llm.py`（或 `pipeline/llm.py`）统一 `complete(prompt, json_schema=None)`；provider：
  - `ollama`：本地 `OLLAMA_BASE_URL`（默认 11434）、`LLM_MODEL`（qwen2.5:7b）；
  - `openai_compatible`：`LLM_BASE_URL`（如 https://api.deepseek.com/v1、https://api.openai.com/v1）、`LLM_API_KEY`、`LLM_MODEL`；
  - `mock`：无配置/测试用固定返回；
- 配置：设置页「AI 模型」区块（provider 切换 / base_url / model / API Key 掩码 / 连接测试）；Key 只写 `backend/data/llm.env`，不入库明文、不写日志；
- 生成任务：① 台本生成（话题/模板/时长/语气→分段台本，JSON schema 校验）② 分镜/ShotPlan prompt ③ 场景描述（style preset 辅助）④ 标题/简介/标签 ⑤ 多语本地化（替换现有 Ollama 直连）⑥ QC reviewer 建议；
- API：`GET/PATCH /api/settings` 增加 llm_*；`POST /api/llm/test`；`POST /api/projects/{id}/generate-script`；`GET /api/llm/providers`；
- 前端：设置 → AI 模型；编剧间「AI 生成台本」对话框（话题/模板/时长/语气、生成中状态、预览/采纳/重生成）；
- 验收：无 Key 用本地 Ollama 生成一段 60s 台本并落入编剧间；填 API Key 后同一入口可切换 provider；Key 不在响应/日志中出现；pytest + build 全绿。

## 三、16 模板风格矩阵（12 基础 preset + 模板级覆盖）
| 模板 | 风格 preset | 视觉方向 |
|---|---|---|
| tonight_show_5min | cinematic_3d | 深夜秀 3D 电影感，暖冷对比、浅景深 |
| shark_tank_5min | cinematic_3d | 创投路演 3D，冷蓝+金色点缀 |
| masked_singer_5min | neon_stage_3d | 霓虹舞台 3D，强逆光/雾气 |
| standup_5min | retro_standup_2d | 复古录像带 2D，暖黄/VHS 颗粒 |
| taskmaster_5min | game_show_3d | 综艺游戏 3D，高饱和彩色/硬光 |
| traitors_5min | roundtable_3d | 暗色圆桌 3D，烛光/阴影 |
| snl_sketch_5min | sketch_3d | 小品舞台 3D，剧院红光/幕布 |
| roundtable_5min | roundtable_3d | 圆桌演播厅 3D，冷蓝科技感 |
| hot_ones_5min | warm_talk_3d | 暖光访谈 3D，柔光/木质 |
| qipashuo_5min | debate_ink_2d | 国风辩论 2D，水墨/红金 |
| slow_life_5min | clay_life_3d | 黏土定格 3D，柔和手作质感 |
| history_talk_5min | history_ink_2d | 水墨历史 2D，烛光/宣纸 |
| history_mystery_5min | history_ink_2d | 水墨悬疑 2D，冷青/暗红 |
| classics_5min | history_ink_2d | 典籍 2D，暖褐/卷轴 |
| national_treasure_5min | museum_gold_2d | 国宝 2D，金/青绿重彩 |
| mystery_5min | noir_mystery_3d | 黑色电影 3D，冷蓝/百叶窗光影 |

## 四、升级阶段（P0→P4）
| 阶段 | 内容 | 负责 | 验收 |
|---|---|---|---|
| **P0 规划** | 本文件 + style_presets.json + LLM 契约 + 任务拆分 | Lead | 文档/契约落盘，任务建好 |
| **P1 LLM** | backend LLM provider(ollama/openai_compatible/mock) + settings + generate-script API + 前端设置/编剧间 AI 生成 + tests | backend-core / frontend-ui | 本地生成可采纳；API Key 可切换；Key 不泄露；测试绿 |
| **P2 风格资产** | 16 模板专属 scene plate + 角色风格变体（按 preset 家族，先 8-10 个高辨识场景）；自动白底/透明度/尺寸校验 | h3-comfy + Lead | 每模板至少 1 张专属 plate + 角色可合成 |
| **P3 渲染升级** | pipeline/style_v2 + 调色/转场/包装；16 模板各出 30-45s 风格 demo；重点模板出 3D H3 demo（8 镜） | media-render | 16/16 风格可辨识；抽帧对比；QC 通过 |
| **P4 成片** | 选 1-2 个模板做完整 5 分钟风格版（2D/3D 各一） | media-render + Lead | 286s 级成片 + QC + 文档 |

## 五、风险与对策
- 3D 角色资产漂移/道具残留 → 多候选自动挑图（白底、单人、bbox 校验），失败回退 2D preset；
- H3 生成慢（≈2-4 分钟/镜）→ 只对重点模板跑 H3，静态 demo 用分层合成；批量放后台队列；
- LLM 输出不稳定 → JSON schema + 重试/回退 mock + 人工采纳；API Key 只存本地 env；
- 风格板过多导致资产成本高 → 先做 12 preset、16 模板覆盖；共享 preset 内用色调/转场区分。

## 六、立即开始（P0→P1/P2 并行）
1. 落盘 style_presets.json（12 preset + 16 模板映射 + 模板覆盖）；
2. backend-core 实现 LLM 抽象 + 设置 + generate-script；frontend-ui 实现设置/编剧间入口；
3. h3-comfy 生成风格 plate（先 8 个高辨识场景：cinematic/noir/neon/clay/ink/museum/retro/game）；
4. media-render 实现 style_v2 渲染，等资产到位后批量出 16 模板风格 demo。
