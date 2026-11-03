# V28：角色三视图/表情包 + 竖版中心裁切（两项）

> 两个独立增强：
> A. 角色素材不止立绘：生成 **三视图（front/side/three_quarter）+ 表情包（neutral/happy/surprised/thinking）+ 设定 sheet**，在角色详情展示，供分镜/一致性参考。
> B. 发布包竖版增加 **中心裁切（crop）** 模式，与现有模糊填充（blur_pad）二选一。

## A. 角色三视图/表情包
### 后端（tpl-backend）
- 复用 imagegen CLI：`--mode three_view|expressions|sheet|all`（产物：`three_view/{front,side,three_quarter}.png` 1024×1024、`expressions/{neutral,happy,surprised,thinking}.png`、`sheet.png` 1600×900、`meta.json`）
- `POST /api/v5/characters/{character}/art` 扩展：
  - body 增加 `modes?: string[]`（`portrait|three_view|expressions|sheet`，缺省 `["portrait"]`；可 `["all"]` 展开为全部）
  - 依次串行生成各 mode 到 `assets/v5/characters/<id>/`；保留 portrait 现有缓存逻辑（portrait 已存在且非 force → cached）
  - 返回 `{ok,character,portrait_url,assets:{three_view:{front,side,three_quarter},expressions:{...},sheet},generated:[modes],cached,elapsed_sec,warnings}`
- `GET /api/v5/characters/{character}/assets`：扫描已生成素材（portrait/three_view/expressions/sheet）返回 URL 映射 + 是否存在
- `POST /api/v5/characters` 的 `auto_art` 支持 `auto_art_modes?: string[]`（默认 portrait；可 ["all"]）
- 自测：对测试 custom 角色生成 all（约 4–6 张，40–90s）→ 文件齐全、/media 200；重复 force=false → cached；GET assets 正确；测试角色 DELETE + 清理产物；写 tmp/v28/backend_selftest.md

### 前端（tpl-frontend）
- 角色详情抽屉新增「**角色素材**」区块：立绘/三视图(3)/表情(4)/设定图 缩略图网格 + 生成按钮（生成三视图 / 生成表情包 / 全部生成）+ loading + 错误
- 新建角色抽屉：增加 checkbox「同时生成三视图与表情包（约 1–2 分钟）」默认不勾选；创建时传 `auto_art_modes:["all"]`（若勾选）/默认 portrait
- 素材生成后卡片与详情即时刷新（cache-bust）；点击缩略图可放大预览（简单 lightbox）
- 截图 tmp/v28/ui/：素材区块空态/生成中/生成完成/lightbox/创建勾选

## B. 竖版中心裁切（crop）
### 后端（tpl-backend）
- `_publish_build_vertical` 支持 `vertical_mode="crop"`：中心裁切 9:16（原片中心 405×720 → 放大 1080×1920，lanczos；封面同法）——与 blur_pad 并列，不再返回 400
- metadata.vertical.mode 记录 `crop`；缓存按 mode 校验；README 说明两种模式差异
- 自测：tiktok&vertical&vertical_mode=crop → 包内竖版 1080×1920（画面为裁切中心）、mode=crop；blur_pad 仍正常；写 tmp/v28（或 v27 追加）自测

### 前端（tpl-frontend）
- 发布包弹层：勾选竖版后显示**模式选择**（模糊背景 fill / 中心裁切 crop，默认 blur_pad）；请求带 `vertical_mode`；文件清单/提示显示当前模式；重新打包按当前模式
- 截图 tmp/v28/ui/：crop 选中态、清单、提示

## 验收
1. 角色：真实生成 all 素材 → 详情显示 3+4+1 张、可预览；创建勾选 all 走通
2. 竖版：crop 包 ffprobe 1080×1920、画面中心裁切（抽帧确认无模糊边）、blur_pad 不受影响
3. `tsc` 0 / build / console 无 error；截图 tmp/v28/
4. docs/V28 + commit

## 分工
- **tpl-backend**：A 后端（modes/assets/auto_art_modes）+ B 后端（crop）
- **tpl-frontend**：A 前端（素材区块/勾选/lightbox）+ B 前端（模式选择）
- **Lead**：真实生成 + crop 包验证、截图、docs、commit


## 5. 交付结果（2026-11-03 20:50）

### 后端（34/34 自测 PASS）
**A. 角色素材**
- `POST /api/v5/characters/{id}/art` 增加 `modes?: string[]`（缺省 `["portrait"]`；`["all"]` = portrait+three_view+expressions+sheet；非法 400）
  - 产物：portrait 832×1216、three_view 3×1024×1024、expressions 4×1024×1024、sheet 1600×900、meta.json（共 10 文件）
  - 返回 `assets{portrait,three_view,exprressions,sheet}`、`files`、`generated`、`modes`；cached 按 modes 判断
- 新增 `GET /api/v5/characters/{id}/assets`：URL 映射 + exists + files（10 项）
- 创建支持 `auto_art_modes?: string[]`（默认 portrait，可 all）
- 实测：all 111.4s、9 个 /media URL 全 200；重复 all cached 0.0s；媒体路由已支持 `three_view/...`、`expressions/...` 嵌套
- `v5_studio.py` 同步扩展媒体路由（嵌套素材路径）

**B. 竖版 crop**
- `vertical_mode` 支持 `blur_pad`（默认）与 `crop`：中心裁切 9:16（1280×720→405×720→lanczos 1080×1920，封面同法）；不再 400
- metadata.vertical.mode=crop；缓存按 mode 校验，切换自动重建；README 说明两种模式
- 实测：tiktok+vertical+crop 3.2s、zip 11,321,412B；竖版 ffprobe **1080×1920 / H.264 / 25fps / AAC / 15.12s**；封面 1080×1920；抽帧与期望中心裁切 **SSIM=0.96085**；blur_pad 回归正常
- 8090 PID 43028

### 前端
**A. 角色素材**：详情「角色素材」区块（立绘/三视图 3/表情 4/设定图 网格）+「生成三视图/生成表情包/全部生成」+ loading/错误 + cache-bust + 缩略图 lightbox；新建角色 checkbox「同时生成三视图与表情包」→ `auto_art_modes:["all"]`
**B. 竖版模式**：发布弹层勾选竖版后「模糊背景填充 / 中心裁切 9:16」选择（默认 blur_pad）；请求带 `vertical_mode`；清单 badge「竖版·模糊/竖版·裁切」+ 尺寸/模式；重新打包按当前模式

### Lead 独立验收
- E2E（`tmp/v28/e2e_v28.py`）：创建临时角色 `auto_art_modes:["all"]` → 三视图 3/表情 4/设定图/立绘全部 200，素材拼图 `tmp/v28/character_assets_sheet.png`
- crop 包：zip 11.32MB，竖版 1080×1920 + 音轨 15.12s、封面 1080×1920、metadata.mode=crop；抽帧 `tmp/v28/crop_frame.jpg` 确认中心裁切无模糊边
- UI（生产构建）：详情显示「角色素材」三视图/表情网格；发布弹层 crop 选中显示「裁切」badge；console 无 JS error（`tmp/v28/lead/`）
- 截图：`tmp/v28/ui/`（7 张前端）、`tmp/v28/lead/`（2 张）

### 清理
- 测试角色（custom_522f7c5e / custom_cd306749 等）已 DELETE + 清理 assets；catalog 回到 24 角色
- backend/前端 E2E 期间对 ali/laozhou 生成的内建素材已 revert，canonical 立绘恢复；仓库无新增内建素材改动

### 说明 / 遗留
- `modes:["all"]` 约 1–2 分钟（首轮可能 2–3 分钟，ComfyUI 串行）；请求超时建议 ≥180s
- all 会额外生成 scene.png（不在 assets 返回，可忽略）
- crop 仅适合 16:9 成片的中心 9:16 裁切：左右与底部字幕边缘可能被裁掉（抽帧可见），发布前可预览
- 竖版 crop 与 blur_pad 共用同一 zip 名，切换模式需重新打包/下载
