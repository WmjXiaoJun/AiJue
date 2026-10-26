# V5.2 变更说明（AI 美术 + 前端绑定）

> 计划：`docs/PLAN_V5_2_ART_FRONTEND.md`；场景美术：`docs/V5_2_SCENE_ART.md`；前端：`docs/V5_2_FRONTEND.md`；
> 风格化：`docs/V5_STYLE_GUIDE.md §D`；验收：`docs/V5_2_ACCEPTANCE.md`（QA）。

## 0. 用户诉求与回应

| 诉求 | V5.2 做法 |
|---|---|
| 角色和场景太丑 | 全部换成 **SDXL Animagine XL 4.0** 动漫美术：24 角色立绘（portrait 头像 + 全身白底 front + 泛洪抠图 cutout，front 用 IP-Adapter 以 portrait 为参考保证同画风/同身份）+ 61 场景 plate（1280×720，无人物动漫背景）；保留 16 风格靠 `style_v5.apply_finish` 的强风格化链路区分 |
| 最早的 3 个角色可以 | laozhou/ali/dawei **直接复用最早 V3 资产**（`outputs/concepts` 立绘 + `outputs/characters/*/cutout_2d` 抠图），不重生成不改动 |
| 没绑定到前端 | React 工作台新增「V5 世界」页（`frontend/src/components/V5Studio.tsx` + `components/v5/*`，`#/v5`）：成片画廊/风格墙/场景库/角色库（AI 立绘+试听+改声音）/渲染面板；后端 `backend/app/routers/v5_studio.py`：`/api/v5/outputs`、`/media/v5/*`、`GET/PUT /api/v5/voice/{id}`、`POST/GET/DELETE /api/v5/render*` |

## 1. 美术资产

```
assets/v5/
  characters/<id>/{portrait.png, front.png, cutout.png, meta.json}   # 24/24
  scenes/<id>.png + <id>.json                                        # 61/61
contract/v5/art/prompts.json   # 24 角色 + 61 场景 SDXL prompt/negative（统一参数）
```

- **角色**：portrait 832×1216（角色卡/近景）、front 1024×1024（纯白底全身，四角≥238 校验）、cutout（泛洪去白+羽化+bbox 校验 alpha0 0.38-0.72）；front 生成使用 `--ref portrait.png`（IP-Adapter weight 0.35/0.25/0.22，style transfer），与 portrait 同画风；laozhou/ali/dawei 复用 V3（`source=v3_reuse`）。
- **场景**：61 张 1280×720 无人物动漫背景（seed 20261101，prompt 来自 prompts.json，meta 含 prompt_id/seed/gen_sec 可复现）；历史 13 场景覆盖古地图（俯视路线/城标/罗盘/朱印）、战场（军旗/远山/烟尘/骑兵剪影）、军帐沙盘、宫殿、藏书楼、博物馆展柜、考古现场、关隘、船队、庙堂、夜市等；现代 48 场景覆盖演播厅/霓虹街/咖啡馆/办公室/审讯室/古堡/实验室/舞台等。
- **复现**：`python scripts/gen_v5_scene_art.py --seed 20261101`；`python scripts/gen_v5_character_art.py`（front 用 `scripts/regen_v5_character_fronts.py --ref`）。

## 2. 渲染接入

- `pipeline/scene_render.py`：**plate 模式**——`assets/v5/scenes/<id>.png` 存在时载入为 background → `style_v5.apply_finish` → 叠加雾/暗角/顶部渐隐（mid/fg），depth/anchors/camera_zones 沿用场景 JSON；无 plate 自动回退程序化（旧模板零回归）。`meta.art` 记录 `plate:<file>` 或 `procedural`。
- `pipeline/characters_v5.py`：`render_character` 优先 AI 立绘（`v5_art.render_art_character`：cutout→fit→`apply_finish(kind="character")`→口型/眨眼 overlay），无资产回退程序化；`art_character()` 供前端/API 查资产。
- `pipeline/style_v5.py`：`apply_finish` 检测 AI 素材（uniq 色数/hf/拉普拉斯）→ 16 风格强链路（ink 冷灰墨+宣纸+朱印 / woodblock 平涂+粗刻线 / parchment 做旧+排线 / museum 黑金聚光 / neon 青洋红+glow / hologram 全息故障 / vhs 扫描线+REC / film 16mm 片框 / noir 黑白百叶 / oil 厚涂 / baroque 烛光 / blueprint 蓝图白线 / candy 半调撕纸 / clay 移轴 / chalk 黑板粉笔 / silk 绢面工笔）；程序化路径零变化；确定性 seed 由 style.id 派生。
- `pipeline/render_v5.py`：取景修正——角色锚点若在 camera zone 外自动收进 zone（修 battlefield 角色被裁出画面）；对白镜角色高度 0.68×zone_h、b-roll 0.52；TTS 缓存+声音锁齐全时跳过 TTS 服务（纯 CPU 重渲更快）。

## 3. 前端「V5 世界」

- 入口：`http://127.0.0.1:8090/#/v5`（Sidebar 新增「V5 世界」；原项目/工作台分支未改）。
- 5 个子页：成片画廊（16 卡：封面/风格 badge/时长/QC/播放/时间线/QC 报告）、风格墙（17 风格色板/技法/标签/详情）、场景库（61 场景 plate + 搜索/tag/风格调色）、角色库（25 卡：AI 立绘/voice_id/seed/声音锁、试听、改声音→PUT→重建声音锁）、渲染面板（模板/时长/force→后台 job→轮询进度/日志/取消，单并发）。
- API：`/api/v5/outputs`（16）、`/media/v5/{template}/{file}`（Range）、`/media/v5/characters|scenes|voice/*`、`/api/v5/voice/{character}` GET/PUT、`/api/v5/render*`（202/409/进度/取消）。
- 构建：`cd frontend && npm run build`（tsc+vite）；后端 8090 同源托管 `frontend/dist`；dev 5173 proxy `/api`。

## 4. 验收（待 QA 填）

- 16 部重渲（AI 美术+固定声音）QC 硬门 + 风格区分度 + 历史场景内容 + P1 头部；
- 美术资产 24/24 + 61/61 计数/校验；
- 前端 build + API 30/30 + 截图（tmp/v52_frontend/screenshots）；
- 回归：pipeline/tests 175 passed；V5 验收测试（QA 更新）。

## 5. 遗留/P2

1. 部分角色（mystery_guest/robot 无脸、lengmian 肤色估算）口型 overlay 关闭（风格原因，宽景本来不可见）。
2. 16 风格 finish 后个别画面偏暗/高对比（ink/museum），如有具体镜头可调 `_ai_plate_finish` 强度。
3. 前端渲染 job 内存态、单并发（服务重启历史丢失，产物保留）；试听/声音锁依赖 Breeze TTS（未启动 503 toast）。
4. 角色立绘为 AI 生成（SDXL），个别细节（手/道具）可能有瑕疵，可单角色重生成（~25s）。
