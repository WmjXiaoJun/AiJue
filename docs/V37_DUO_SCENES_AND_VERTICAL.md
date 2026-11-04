# V37：双人对戏双场景 + 合集发布包竖版

> 承接 V36：双人对戏目前所有镜头共用一组背景/机位；试镜合集发布包只有 16:9。本轮补齐：
> 1. **双人双场景**：角色 A / 角色 B 各自背景与机位（正反打时各自在自己的场景里）。
> 2. **合集发布包竖版 9:16**：发布包可选附带竖版（模糊填充 / 中心裁切）+ 竖版封面。

## 1. 后端

### A. 双人对戏双场景（character_dialogue.py）
`POST /api/v5/characters/dialogue` body 增加（可选，缺省沿用顶层 background/camera）：
```jsonc
"scene_a": {"background": "studio", "camera": "closeup"},
"scene_b": {"background": "cafe", "camera": "medium"}
```
- 校验：各自枚举合法；顶层 background/camera 仍是默认值
- 每镜按 speaker 使用对应场景（A 行用 scene_a，B 行用 scene_b）；任务 JSON 返回 `scenes:{a:{background,camera,labels}, b:{...}}` 与每镜 `background/camera`
- 片头卡不变；字幕/拼接/互斥/持久化沿用 V36
- 自测：2 行（A studio/closeup，B cafe/medium）真实生成 → done；抽帧 A 镜头为棚拍近景、B 镜头为咖啡馆中景；任务 scenes 字段正确；非法场景 400；写 tmp/v37/backend_selftest.md

### B. 合集发布包竖版（character_showcase.py）
`POST /api/v5/character-showcase/publish-package` body 增加：
- `vertical?: bool=false`（前端默认勾选）
- `vertical_mode?: "blur_pad"|"crop"`（默认 blur_pad）
- vertical=true 时 zip 额外包含：
  - `video/character_audition_reel_vertical.mp4`：1080×1920（blur_pad：背景模糊填充；crop：中心裁切），H.264+AAC，时长同源
  - `cover/cover_vertical.jpg`：1080×1920
  - `metadata.json` 增加 `vertical:{file,resolution,mode}`
  - README 增加竖版说明
- 缓存兼容按 vertical/mode 判断（不匹配自动重建）；竖版失败仅 warning 不阻塞 16:9 包
- 自测：vertical=true 构建 → zip 解压含竖版视频（ffprobe 1080×1920+音轨）与竖版封面；metadata.vertical 正确；vertical=false 回归；force 重建

## 2. 前端
- **双人对戏弹层**：把原来的单一背景/机位拆成两行「角色 A 场景」「角色 B 场景」（各自背景下拉 + 机位下拉），默认 A=棚拍·近景、B=咖啡馆·中景；请求带 scene_a/scene_b
- 完成卡片/最近列表显示「A 场景 · B 场景」标签
- **试镜合集弹层**：新增「附带竖版 9:16（TikTok/IG）」checkbox（默认勾选）+ 模式（模糊填充/中心裁切）；文件清单显示竖版视频/封面；重新构建按当前选项
- 截图 tmp/v37/ui/：双人双场景弹层、完成标签、合集竖版选项、清单含竖版

## 3. 验收
1. 真实双人 2 行（A 棚拍近景 / B 咖啡馆中景）→ 抽帧两角色场景不同
2. 合集发布包 vertical=true → zip 含 1080×1920 竖版视频与封面
3. tsc 0 / build / console 无 error；截图 tmp/v37/
4. docs/V37 + commit

## 4. 分工
- **tpl-backend**：dialogue scene_a/scene_b + 合集 vertical/vertical_mode + 自测
- **tpl-frontend**：双场景 UI + 合集竖版选项/清单 + 截图
- **Lead**：真实双人双场景生成 + 竖版包验证、截图、docs、commit


## 5. 交付结果（2026-11-04 08:22）

### A. 双人对戏双场景（后端 23/23 PASS）
- `POST /api/v5/characters/dialogue` 增加 `scene_a` / `scene_b`（background/camera，缺省沿用顶层）；每镜按 speaker 使用对应场景；任务返回 `scenes:{a,b}`（含中文 label）与每镜场景
- 实测（后端）：laozhou × tianmei 2 行（A studio/closeup，B cafe/medium）**248.2s done**，1,242,384B / 12.59s / 1024×576 / 24fps / AAC；抽帧 A 棚拍近景老周、B 咖啡馆中景甜妹（差异 74.9%），字幕可见；非法场景 4 类 400
- Lead 独立 E2E：2 行 244.1s done，1,241,157B / 12.59s；scenes 字段正确；抽帧确认双场景
- 看片：`/media/character_demos/laozhou_tianmei_dual_scene.mp4`

### B. 合集发布包竖版 9:16（后端 12/12 复核 PASS）
- `POST /api/v5/character-showcase/publish-package` 增加 `vertical`（默认 false）与 `vertical_mode`（blur_pad/crop）；vertical=true 时 zip 额外含 1080×1920 竖版视频（H.264+AAC）、1080×1920 竖版封面、metadata.vertical、README 说明；缓存按开关/mode 判断；失败仅 warning
- 实测：blur_pad **21,995,136B / 18 文件 / 13.0s**；竖版 ffprobe 1080×1920 + 音轨 72.69s；cached=true；vertical=false 回归 16 文件；crop 可重建
- Lead E2E：下载解压 18 文件、竖版 1080×1920/72.688s/含音轨、封面 1080×1920、metadata.vertical 正确
- 下载：`/media/character_demos/publish/character_audition_reel.zip`

### 前端
- 双人对戏弹层拆成「角色 A 场景」「角色 B 场景」两行（各自背景/机位），默认 A=棚拍·近景、B=咖啡馆·中景；完成/最近显示「A 场景 · B 场景」标签
- 试镜合集弹层新增「附带竖版 9:16」checkbox（默认勾选）+ 模糊填充/中心裁切模式；清单竖版视频/封面置顶 + 徽标；重新构建按当前选项
- 截图 `tmp/v37/ui/`（7 张）+ `tmp/v37/lead/`（2 张）；`tsc` 0 / build / console 无 error

### 说明 / 遗留
- 双人对戏每行约 5.167s（2 行 12.59s），真实生成约 4–8 分钟
- 竖版包约 22MB（16:9 约 6.6MB），构建约 13s，之后按 title+vertical+mode 缓存
- 片头卡/烧录字幕不随 scene 变化；H3/竖版均 24fps
