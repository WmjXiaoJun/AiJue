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
