# V36：双人对戏 + 试镜合集发布包

> 用户：「下一步 1 2 都做」+「能根据角色生成视频了吧」
> 1. **双人对戏**：两个角色按正反打交替说话（各自立绘 + 背景/机位 + 各自音色 + 字幕），拼成一段对话视频。
> 2. **试镜合集发布包**：把已有的「10 角色试镜合集」reel 做成发布包（视频 + 封面 + 元数据 + README + 各角色 SRT），可下载手动上传。

## 1. 后端

### A. 双人对戏（新 router `character_dialogue.py`）
`POST /api/v5/characters/dialogue`
body：
```jsonc
{
  "character_a": "laozhou", "character_b": "tianmei",
  "style": "realistic",                    // 两个角色都用该风格立绘
  "background": "cafe", "camera": "medium",
  "title": "老周 & 甜妹 · 深夜咖啡馆",       // 可选，片头卡
  "lines": [
    {"speaker": "a", "text": "这么晚还来喝咖啡？"},
    {"speaker": "b", "text": "睡不着，想找个人聊聊。"},
    {"speaker": "a", "text": "那就聊聊吧。"},
    {"speaker": "b", "text": "你不怕我说到天亮？"}
  ]
}
```
- 校验：两角色存在且立绘齐全；lines 2–6、每行 ≤60 字、总时长 ≤16s；speaker ∈ a/b；background/camera 复用 V34 枚举
- 任务：queued → running(stage `line_i_tts` / `line_i_generate` / `assemble`) → done；每行一个镜头：说话角色立绘 + 同背景/机位 + 该角色音色 TTS + 字幕烧录
- 拼接：片头卡 2.2s（title，可选）+ 各镜正片（不说话的角色不出镜，正反打）；concat 1024×576/24fps/AAC；poster + 合并 SRT
- 输出 `outputs/character_demos/dialogue_<id>.mp4`；持久化 `outputs/dialogue_jobs/*.json`
- `GET /api/v5/dialogues/{id}`、`POST .../cancel`、`GET /api/v5/dialogues?limit=5`；与 3D/audition/promo/dialogue 互斥 409
- 自测：真实生成 4 行（laozhou/tianmei，cafe+medium）→ done；ffprobe 1024×576+音轨；抽帧正反打（两张不同角色）+ 字幕；poster/srt 200；参数 400；取消；写 tmp/v36/backend_selftest.md

### B. 试镜合集发布包（新 endpoint）
`POST /api/v5/character-showcase/publish-package`
body `{title?: str, force?: bool}`（source 固定为 `outputs/character_demos/character_audition_reel.mp4`，缺失 400）
- 构建 zip 到 `outputs/character_demos/publish/<slug>.zip`：
  - `video/character_audition_reel.mp4`
  - `cover/cover.jpg`（片头或 1/4 处抽帧）
  - `metadata.json`：title/description/tags/时长/分辨率/角色清单（10 个 id+label）/生成时间
  - `README.txt`：上传步骤 + 说明（字幕已烧录）
  - `subtitles/<character>.srt`：各角色试镜 SRT（来自 tmp/v33、tmp/v34 的 demo srt，或 outputs/audition_jobs 扫描）
- 返回 `{ok, package_url, size_bytes, files[], cached}`
- `GET /api/v5/character-showcase/publish-package` 列已生成包
- 自测：构建 → zip 解压校验 6+ 文件、metadata 可解析、video 200；cached/force；写 tmp/v36（可并入报告）

## 2. 前端
- **双人对戏**：角色与声音页 header 增加「双人对戏」按钮 → 弹层：
  - 选角色 A / 角色 B（下拉，展示立绘小图）；风格、背景、机位、标题（可选）
  - 台词行编辑：每行选 A/B 说话 + 台词，默认 4 行，可加/删（2–6 行）；预计时长显示
  - 提交 → 进度卡（第 i/n 句 配音/生成、拼接中）+ 取消；完成播放 + 下载视频/海报/SRT；最近双人对戏列表
- **合集发布包**：角色与声音页 header「试镜合集」区块/按钮 → 弹层显示 reel 信息 +「导出发布包」→ 文件清单 + 下载 zip
- 截图 tmp/v36/ui/：双人对戏弹层/进度/完成播放/最近列表；合集发布包弹层/下载

## 3. 验收
1. 真实双人对戏 4 行生成完成（约 8–10 分钟），成片正反打、字幕、两角色音色正确
2. 合集发布包可下载、zip 内容正确
3. tsc 0 / build / console 无 error；截图 tmp/v36/
4. docs/V36 + commit

## 4. 分工
- **tpl-backend**：dialogue router + showcase publish package + 自测
- **tpl-frontend**：双人对戏 UI + 合集发布包 UI + 截图
- **Lead**：真实双人对戏生成 + 发布包验证、截图、docs、commit


## 5. 交付结果（2026-11-04 07:37）

### A. 双人对戏（后端 23/23 复核 PASS）
- 新增 `character_dialogue.py` + `main.py` 注册（**修复与 v5_catalog 通用路由冲突**：dialogue/showcase 注册提前）：
  - `POST /api/v5/characters/dialogue`（202）；校验两角色/立绘、lines 2–6/每行 ≤60/总 ≤16s、speaker、背景机位枚举；非法 400
  - 每行一镜（说话角色立绘 + 同背景机位 + 该角色音色 TTS + 字幕烧录，正反打）；片头卡 2.2s；concat 1024×576/24fps/AAC；poster + 合并 SRT
  - `GET /api/v5/dialogues/{id}`、cancel、`GET ?limit=5`；持久化 `outputs/dialogue_jobs/`；与 3D/audition/promo/dialogue 互斥 409
- 实测（后端）：laozhou × tianmei 4 句 **495.9s done**；2,515,330B / 22.96s / 1024×576 / AAC；抽帧正反打正确、字幕可见
- Lead 独立 E2E：4 句 **516.9s done**，2,515,330B / 22.959s；SRT 4 条；poster 200；抽帧确认老周/甜妹切换
- 看片：`/media/character_demos/laozhou_tianmei_dialogue.mp4`（示例）

### B. 试镜合集发布包
- 新增 `character_showcase.py`：`POST /api/v5/character-showcase/publish-package {title?,force?}`（source 固定 `character_audition_reel.mp4`，缺失 400）→ zip 到 `outputs/character_demos/publish/`；`GET` 列包
- 实测 zip **6,602,457B / 16 文件**：video 6,627,171B、cover 18KB、metadata（72.69s/1024×576/24fps/12 角色）、README、12 个角色 SRT；`/media` 200 application/zip；cached/force 通过
- Lead E2E 下载解压校验 16 文件与 metadata 正确

### 前端
- 角色与声音页 header：「双人对戏」「试镜合集」入口（AppShell 增加 extraActions）
- 双人对戏弹层：A/B 角色下拉 + 立绘小图、风格/背景/机位/标题；台词行编辑（默认 4 行，A/B + 台词，加删 2–6，预计时长）；进度卡（第 i/n 句 配音/生成、拼接中 + 取消）；完成播放 + 下载视频/海报/SRT；最近列表
- 试镜合集弹层：reel 信息 + 导出发布包 → 文件清单（16 文件/6.3MB）+ cached/force + 下载 zip
- 截图 `tmp/v36/ui/`（8 张）+ `tmp/v36/lead/`（4 张）；`tsc` 0 / build 通过

### 说明 / 遗留
- 单行 H3 实际约 5.167s（短句也如此），前端预计按「行数×5s + 2.2s」更准；4 行约 8–10 分钟，loading 建议 ≥10 分钟
- 双人对戏字幕始终烧录、全镜共用一组 background/camera；片头卡固定 2.2s
- H3 原生 24fps；新增长 `/api/v5/{kind}/...` 特定路由必须注册在 v5_catalog 通用路由之前（已修）
