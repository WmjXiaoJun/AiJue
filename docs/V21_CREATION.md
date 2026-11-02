# V21：创建模板 + 创建角色（UI 可视化创建）+ UI 继续优化

> 用户（2026-11-02 18:55）：「好优化 UI 吧继续；还有现在不能创建模板和创建角色么？还有现在每个模板是不同的工作流么？」

## 0. 先回答「每个模板是不同的工作流么」

**不是。** 现状分层：
- **3D 生成流水线是同一套**：`pipeline.templates3d_hq`（1024×576 T2VA → ref2va 原生口型 → 剪辑 → 海报），19 个模板只是**模板合同不同**（style / cast / scenes / acts 台词 / semantic brief / 目标时长），走同一条流水线。
- `contract/v6/workflows/*.json` 是遗留的「Agent 编排出场配置」（16 个老模板各一份，但 stages 结构完全相同：story→voice→art→editorial→shots→h3→assemble→storyboard→qc→publish，仅 seed/duration/params 不同），当前 3D 生成**不走**它；新模板/写实模板也没有。→ 本轮不改工作流引擎；如需要，后续可统一成一份默认 workflow + 模板变量。

## 1. V21 交付：可视化创建

### A. 新建角色（角色与声音页 →「新建角色」）
表单：名字 / era(现代/历史) / persona / 性别 / 年龄 / 发型 / 主色 / 服装类型 / 配饰 / 音色（24 preset 试听选择）
→ 创建后：写入 `contract/v5/custom/characters/<id>.json`，刷新目录并打开新角色详情（可继续换声音/生成概念图/试听）。

**后端 API**
- `POST /api/v5/characters` body `{id?, label, era, persona?, voice_id?, appearance{gender,age,hair,hair_style,outfit{type,main,accent}}, role_pool?, tags?}`
  - id 缺省生成 `custom_<hex8>`（^[a-z][a-z0-9_]{1,60}$）；补默认 poses/mouth/style_overrides；`v5_catalog.validate_item(..., as_custom=True)` 校验后写 custom 文件；voice_id 需在 voices.json；返回 `{ok,id,item,preview_url}`
- `DELETE /api/v5/characters/{id}`：仅允许删除 custom-only 角色；若被任何模板 cast 引用 → 400 并列出模板；否则删文件。

### B. 新建模板（模板与素材 · 模板 tab →「新建模板」）
向导：
1. 基本信息：id（英文小写，可自动生成 custom_tpl_xxx）/ 中文名 / 风格（17 种）/ 画幅 16:9 / 目标时长
2. 角色 cast：从 24 角色里选 3–5，填角色名（默认角色 label），指定 role（host/figure_a/…）
3. 分幕（4–8 幕）：每幕选场景（70 个场景）、说话角色（或 B-roll 无台词）、台词（中文，1–2 句）
4. 预览：每幕预计 TTS 时长/画面描述提示 → 创建
创建后：模板出现在「模板与素材 · 模板」和「一键成片 · 选模板」（状态 pending，可走「开始生成 3D」）。

**后端 API**
- `POST /api/v5/templates` body `{id?, label, style, aspect?, description?, cast:[{role,character,name}], acts:[{scene,camera?,speaker?,text,duration?}], auto_brief?:true}`
  - 校验：style/character/scene 存在；acts 4–10；dialogue 的 speaker 必须在 cast；id 唯一（内建+custom）
  - 构造 template JSON（acts id=act01..，line=role，text，broll=无 text）；写 `contract/v5/custom/templates/<id>.json`
  - **生成 TTS + episode**：对白幕用角色解析音色合成 24k mono wav（breeze 可用则 breeze，否则 edge 兜底）→ 测时长 → shot duration = speech+0.6；B-roll 静音 wav + duration；写 `outputs/templates_v5/<id>/work/episode.json` + `work/audio/v5_001..wav`；回写 acts 时长、target_duration=总和
  - **生成 semantic brief**：调用 `pipeline.templates3d_semantic` 的 build（LLM），写 `contract/v5/cinema3d/templates3d/<id>_semantic.json`；失败给 warning 不阻塞
  - 返回 `{ok,id,label,shots:n,total_duration,audio:n,brief:bool,warnings:[]}`
- `DELETE /api/v5/templates/{id}`：仅 custom；无 final 才允许（false 则 400）；删 template/semantic/outputs work。
- **纳入现有列表**：`GET /api/cinema/templates`/`status` 需包含 custom 模板（status=pending、clips 0、final 不存在、poster 占位），`POST /api/pipeline/run {template_id: 新id, reuse_shots:false}` 可真实生成；`GET /api/v5/casting?template=新id` 返回其 cast。

## 2. UI 继续优化（tpl-frontend 顺带）
- 角色页/模板页加「新建」主 CTA；创建向导为右侧抽屉或分步弹层，风格与 shell 一致（暗金、卡片 12px）
- 创建中 loading、失败错误可读（400 校验错误逐条展示）、成功 toast + 列表刷新并高亮新建项
- 空态：目录为空时的引导；所有按钮 disabled/loading 状态一致
- 保持主轨 6 项/统一 shell，不新增一级导航

## 3. 验收
1. UI 可创建角色：新角色出现在角色页，可试听/换声；`GET /api/v5/catalog` characters +1；预览图 200
2. UI 可创建模板：新模板出现在模板与素材/一键成片，`/api/v5/casting` 正确；`outputs/templates_v5/<id>/work/episode.json` + 每镜 wav 存在；semantic brief 存在；`/api/cinema/templates` 包含且 pending；`POST /api/pipeline/run {reuse_shots:false}` 返回 202（可真实生成）
3. 删除：未被引用 custom 角色/模板可删；被引用 400 可读
4. `tsc` 0 error / build 通过 / console 无 JS error；截图 `tmp/v21/`
5. docs/V21 + commit

## 4. 分工
- **tpl-backend**：A/B 后端 API + 列表纳入 + 校验/删除 + 自测（角色 1 个、模板 1 个真实创建，检查 episode/audio/brief；pipeline run 只验证 202/409 不真跑 GPU）
- **tpl-frontend**：「新建角色」表单 +「新建模板」向导 + 创建后刷新/高亮 + UI 状态优化 + 截图
- **Lead**：契约/复核/E2E/截图/commit/交付
