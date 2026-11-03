# V34：试镜视频支持选背景 / 机位

> 当前试镜视频固定「棚拍 + 中景」。本轮：生成弹层可选**背景**（棚拍/深夜电台/咖啡馆/夜景街头/办公室/古风大殿/未来演播厅）与**机位**（中景/近景/半身），后端把选择翻译成 H3 提示词并记录到任务。

## 1. 后端（character_audition.py 扩展）
### POST /api/v5/characters/{character}/audition 新参数
- `background?: str = "studio"`：`studio | radio | cafe | street | office | ancient_hall | futuristic`
- `camera?: str = "medium"`：`medium | closeup | half_body`
- 非法值 → 400 可读；任务 JSON/GET 返回 `background/camera`（含标签）

### 提示词映射（H3 prompt 拼接）
- 背景 phrase（示例，后端可微调）：
  - studio：`in a dark professional recording studio with soft key light`
  - radio：`in a warm late-night radio studio with a vintage microphone and practical lamps out of focus`
  - cafe：`in a cozy cafe by a window, warm afternoon light, blurred background`
  - street：`on a neon-lit city street at night, bokeh lights, shallow depth of field`
  - office：`in a modern office with city skyline through the window`
  - ancient_hall：`in an ancient Chinese hall with wooden pillars and warm lantern light`
  - futuristic：`in a futuristic control room with blue holographic screens`
- 机位 phrase：
  - medium：`medium close-up, chest-up framing`
  - closeup：`tight close-up on the face, shallow depth of field`
  - half_body：`half-body shot, waist-up framing, more of the outfit visible`
- 原有「保持参考图身份/发型/服装、口型同步」约束保留

### 自测
- 真实生成 1 条：`background=cafe, camera=closeup`（2 句 + 字幕）→ done，抽帧背景为咖啡馆、机位更近；任务字段正确
- 非法 background/camera → 400；默认值（studio/medium）回归；写 tmp/v34/backend_selftest.md

## 2. 前端（试镜弹层）
- 弹层新增两个下拉：
  - 背景：棚拍（默认）/ 深夜电台 / 咖啡馆 / 夜景街头 / 办公室 / 古风大殿 / 未来演播厅
  - 机位：中景（默认）/ 近景 / 半身
- 提交带 `background/camera`；进行中/完成卡片显示「背景 · 机位」标签；最近列表也带标签
- 截图 tmp/v34/ui/：选择器、进行中、完成播放（咖啡馆近景）、错误

## 3. 验收
1. UI 选「咖啡馆 + 近景」→ 生成 → 成片背景为咖啡馆、构图更近
2. API 真实生成校验 + 非法参数 400 + 默认回归
3. tsc 0 / build / console 无 error；截图 tmp/v34/
4. docs/V34 + commit

## 4. 分工
- **tpl-backend**：参数/提示词映射/校验/任务字段 + 自测
- **tpl-frontend**：弹层下拉/标签/截图
- **Lead**：真实生成 1 条（cafe+closeup）验证背景机位、截图、docs、commit


## 5. 交付结果（2026-11-04 00:40）

### 后端（18/18 自测 PASS）
- `POST /api/v5/characters/{id}/audition` 新增 `background`（studio/radio/cafe/street/office/ancient_hall/futuristic，默认 studio）与 `camera`（medium/closeup/half_body，默认 medium）；非法 400
- 202/GET/任务 JSON 返回 `background/background_label/camera/camera_label`（中文 label）
- H3 prompt 映射：7 背景 × 3 机位 phrase 全部拼接，保留身份/发型/服装保持与口型同步约束；映射 21/21 组合通过
- 实测（laozhou，cafe+closeup，2 句 + 字幕）：**done 159.2s**，烧录版 651,354B、1024×576 + 音轨 + 5.88s、SRT 2 条；抽帧确认**咖啡馆窗边暖光 + 近景头部特写 + 字幕**（`tmp/v34/frame_cafe_closeup.png`）
- 默认 studio/medium 回归正常；持久化字段齐全；PID 42912

### 前端
- 试镜弹层新增「背景」「机位」两个下拉（7 背景 / 3 机位）；提交透传；重试保留上次选择
- 进行中/完成卡片与最近列表显示「背景 · 机位」标签；旧任务无字段时回退棚拍/中景
- `tsc` 0 / build 通过；截图 `tmp/v34/ui/`（5 张）+ `tmp/v34/lead/`（2 张）

### 使用
角色详情 → 试镜视频 → 选背景（棚拍/深夜电台/咖啡馆/夜景街头/办公室/古风大殿/未来演播厅）+ 机位（中景/近景/半身）+ 多行台词 + 字幕 → 生成（约 3–4 分钟）

### 说明 / 遗留
- 背景/机位是 H3 提示词引导，模型有随机性，不保证每次装修/景别完全一致（本次实测效果明显）
- H3 原生 24fps；单条冷启动 2.5–4 分钟；GPU 单并发占用时 409
