# V38：三人对戏 + 合集单角色切条包

> 承接 V37：双人对戏已支持各自场景。本轮：
> 1. **三人群聊/多角色对戏**：可选第三角色 C（各自场景、各自音色），2–6 句台词按 speaker a/b/c 轮流切镜。
> 2. **合集单角色切条包**：把每个角色已生成的试镜条单独打包成 zip（每人一条 mp4 + srt + meta + 清单），方便逐个发布。

## 1. 后端

### A. 三人对戏（character_dialogue.py 扩展）
`POST /api/v5/characters/dialogue` 增加：
- `character_c?: str`（可选）；`scene_c?: {background?, camera?}`
- `lines[].speaker` 允许 `"c"`（未提供 character_c 时出现 c → 400）
- 校验：A/B/C 互不相同；三人都要有指定 style 立绘；lines 2–6、每行 ≤60 字、总估算 ≤24s；非法 400
- 任务 JSON 返回 `characters:{a,b,c?}`、`scenes:{a,b,c?}`；每镜带 speaker 角色 id/label 与场景；片头卡默认「A × B × C」；其余（拼接/poster/合并 SRT/互斥/持久化）沿用 V37
- 自测：3 行（A studio/closeup 老周、B cafe/medium 甜妹、C street/medium 大魏）真实生成 → done；抽帧三个不同角色/场景；scenes 含 c；非法（重复角色/c 无角色/speaker c 无 character_c）400；写 tmp/v38/backend_selftest.md

### B. 合集单角色切条包（character_showcase.py 扩展）
`POST /api/v5/character-showcase/split-packages`
body `{force?: bool}`
- 来源：`outputs/audition_jobs/*.json` 中 status=done 且 subtitles_burned 的最近任务；按 character 去重取最新；视频取 `outputs/character_demos/<char>_<job>_sub.mp4`，SRT 取任务 work 目录 `subtitles.srt`
- zip `outputs/character_demos/publish/character_audition_clips.zip`，内容：
  - `clips/<character>/audition.mp4` + `clips/<character>/audition.srt` + `clips/<character>/meta.json`（label/台词/时长/背景/机位/风格）
  - `manifest.json`（角色清单+条目数+总时长）、`README.txt`
- 返回 `{ok,package_url,size_bytes,files[],characters[],cached}`；GET 列包；缺失任务 400
- 自测：构建 → zip 解压校验 12 角色条目、mp4/srt/meta 齐全；cached/force；写 tmp/v38

## 2. 前端
- **双人对戏弹层**：增加「加入第三角色」开关；开启后出现角色 C 下拉 + 场景 C 行；台词行 speaker 下拉增加 C；校验（重复角色/C 未选）提示
- 完成/最近列表显示 2–3 个角色与场景标签
- **试镜合集弹层**：新增「单角色切条包」区块/按钮 → 构建/下载 zip，显示清单（角色数/文件数/大小）
- 截图 tmp/v38/ui/：三人弹层/进度/完成（3 角色抽帧）/最近；切条包弹层/下载

## 3. 验收
1. 真实三人 3 行生成完成（约 6–8 分钟），抽帧三角色不同场景、字幕正常
2. 单角色切条包可下载，zip 含 12 角色条目（mp4+srt+meta）
3. tsc 0 / build / console 无 error；截图 tmp/v38/
4. docs/V38 + commit

## 4. 分工
- **tpl-backend**：dialogue 三人扩展 + split-packages + 自测
- **tpl-frontend**：三人对戏 UI + 切条包 UI + 截图
- **Lead**：真实三人生成 + 切条包验证、截图、docs、commit


## 5. 交付结果（2026-11-04 09:32）

### A. 三人对戏（后端 24/24 PASS）
- `POST /api/v5/characters/dialogue` 增加 `character_c` / `scene_c`；speaker 支持 `c`；A/B/C 互不相同、三人立绘齐全；lines 2–6、每行 ≤60、总估算 ≤24s；新增 7 类 400
- 返回 `characters:{a,b,c}`、`scenes:{a,b,c}` 与每镜角色/场景；片头卡「A × B × C」；其余沿用 V37
- 实测（后端）：老周 studio/closeup × 甜妹 cafe/medium × 大魏 street/medium 3 行 **done，372.4s**；1,945,837B / 17.77s / 1024×576 / 24fps / AAC；三镜场景/角色正确、差异 57–75%；SRT 3 条
- 看片：`/media/character_demos/dialogue_dlg_e65683b51eb2.mp4`
- 帧：`tmp/v38/frame_line1~3.png`（老周棚拍近景 / 甜妹咖啡馆中景 / 大魏霓虹街头）

### B. 单角色切条包（后端 24/24 PASS）
- 新增 `POST/GET /api/v5/character-showcase/split-packages`：取每角色最新 done+subtitles_burned 试镜任务，zip 含 `clips/<char>/{audition.mp4,audition.srt,meta.json}` + `manifest.json` + README；无任务 400；按角色→job 签名缓存，force 重建
- 实测：**8,568,988B / 38 文件 / 12 角色 / 总时长 69.79s**；12 个 meta 可解析、12 个 SRT 非空；cached/force 正常
- 下载：`/media/character_demos/publish/character_audition_clips.zip`
- Lead E2E：下载解压 38 文件（12 mp4 + 12 srt + 12 meta + manifest + README），manifest 含 12 角色

### 前端
- 双人对戏弹层新增「加入第三角色（三人群聊）」开关：角色 C 下拉 + 场景 C 行；台词行 speaker 下拉 A/B/C；校验（重复/C 未选/speaker C 无角色 C）；完成/最近列表显示 2–3 人 + A/B/C 场景标签
- 试镜合集弹层新增「单角色切条包」区块：构建/重新构建（force）、下载 zip、角色数/文件数/大小/cached、角色 chips + 文件清单
- 截图 `tmp/v38/ui/`（8 张）+ `tmp/v38/lead/`（2 张）；`tsc` 0 / build / console 无 error

### 说明 / 遗留
- 三行约 6–7 分钟（loading 建议 ≥10 分钟）；每行 H3 约 5.167s
- 切条包旧任务缺背景/机位字段时会回退棚拍/中景；重新生成某角色后下次构建自动替换
- 三人对戏总时长上限 24s；H3 原生 24fps
