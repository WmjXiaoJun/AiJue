# V30：角色立绘版本（动漫 / 2.5D / 写实）查看与生成

> 现状：`assets/v5/characters/<id>/` 里其实已有三种立绘：
> `portrait.png`（动漫）、`portrait_25d.png`（2.5D 渲染感）、`portrait_real.png`（写实/真人感），24 个角色全都有；
> 但 V5 角色接口/UI 只暴露 `portrait.png`，用户看不到、也生成不了别的版本。
> 目标：角色详情可切换立绘版本、显示 2.5D/写实；缺失版本可一键生成。

## 1. 后端
- `_art_assets_payload` / `GET /api/v5/characters/{id}/assets` 增加：
  - `portraits: {anime: url|null, stylized_25d: url|null, realistic: url|null}`
  - `files` 纳入 `portrait_25d.png`、`portrait_real.png`（存在才带）
- `POST /api/v5/characters/{id}/art`：`style` 决定头像落盘文件名
  - `anime|illustrious|guoman` → `portrait.png`
  - `stylized_25d` → `portrait_25d.png`
  - `realistic` → `portrait_real.png`
  - cached 判断按「该 style 对应文件」；force 重新生成
  - 返回 `portrait_url` 指向对应版本文件；`assets.portraits` 同步
- 生成流程不变（imagegen style 已支持 realistic/stylized_25d；GPU 串行）
- 自测：已有角色（如 laozhou）GET assets 三版本 URL 均 200；临时 custom 角色 `style=realistic` 生成 → 写入 `portrait_real.png`、返回 URL；再次 cached；`force` 重建；写 tmp/v30/backend_selftest.md

## 2. 前端
- 角色详情顶部立绘区增加「**立绘版本**」切换（动漫 / 2.5D / 写实）：
  - 选中有图的版本 → 显示对应图（cache-bust）
  - 选中缺失版本 → 显示占位 +「生成此版本」按钮（调 art style，loading 10–60s，完成刷新）
  - 当前版本可「重新生成」
- 角色卡保留动漫立绘；若有 2.5D/写实版本，可加小标记（可选）
- 角色素材区块保留三视图/表情/设定图逻辑（不冲突）
- 截图 tmp/v30/ui/：版本切换（动漫/2.5D/写实）、缺失版本生成中、生成完成、错误态

## 3. 验收
1. 打开已有角色详情 → 切 2.5D/写实立即显示对应立绘（24 角色素材已存在）
2. 新建角色默认只有动漫 → 切写实显示「生成此版本」→ 生成成功 → 立绘切换显示
3. `tsc` 0 / build / console 无 error；截图 tmp/v30/
4. docs/V30 + commit

## 4. 分工
- **tpl-backend**：assets.portraits + style→文件名 + cached/force + 自测
- **tpl-frontend**：立绘版本切换 + 缺失生成 + 截图
- **Lead**：真实切换/生成验证（现有角色三版本 + 临时角色生成写实）、截图、docs、commit


## 5. 交付结果（2026-11-03 22:02）

### 现状澄清（回答用户问题）
- 角色**没有真正的 3D 模型文件**；但每个角色磁盘上已有三种立绘：
  - `portrait.png` 动漫、`portrait_25d.png` 2.5D 渲染感、`portrait_real.png` 写实（真人感）
  - 24 个内建角色**三种全部已有**（assets/v5/characters/<id>/）
- 3D 观感来自 H3 视频生成（成片里是 3D 动画/写实电影质感），不是角色资产本身
- 此前 UI 只暴露动漫版；本轮补齐查看与生成

### 后端（23/23 自测 PASS）
- `GET /api/v5/characters/{id}/assets` 新增 `portraits:{anime,stylized_25d,realistic}`（缺失 null；`portrait` 保持 anime 兼容）；`files` 纳入 portrait_25d/portrait_real
- `POST /api/v5/characters/{id}/art` 按 style 落盘：anime/illustrious/guoman→`portrait.png`；stylized_25d→`portrait_25d.png`；realistic→`portrait_real.png`；cached 按版本；force 重建；返回 `portrait_file/portrait_variant/portrait_url`
- 生成用对应 checkpoint：2.5D=`AAM_XL_Anime_Mix`、写实=`RealVisXL_V5.0_fp16`（`V5_ART_CKPT` 可覆盖）；先 staging 再落盘，**不覆盖已有 anime portrait**
- 实测：laozhou 三版本 `/media` 200（anime 1.07MB / 2.5D 1.27MB / real 1.67MB）；临时角色 realistic 12.4s→portrait_real.png、stylized_25d 12.4s→portrait_25d.png；同 style cached 26ms；anime sha256 未变；测试角色已清理

### 前端
- 角色详情立绘区「**立绘版本**」切换：动漫 / 2.5D / 写实（有图显示、缺失占位）；缺失版本「生成此版本」（loading 10–60s）；当前版本可重新生成；生成完成即时刷新
- 与「角色素材」（三视图/表情/sheet）区块共存；角色卡保持动漫立绘
- 截图 `tmp/v30/ui/`（7 张：三版本、缺失、生成中、完成、错误）

### Lead 独立验收
- laozhou assets 三版本 URL 全 200；临时角色 `style=realistic` 23s 生成 → portrait_real.png 200，assets.portraits 正确；临时角色已删除
- UI（生产构建）：详情切 2.5D/写实显示对应立绘（`tmp/v30/lead/` 3 张）；console 仅遗留预览回退 404（非本次功能）
- 视觉对比：`tmp/v30/variants_compare.jpg`（6 角色 × 动漫/2.5D/写实）、`tmp/v30/films_3d_compare.jpg`（6 支 3D 成片角色帧）

### 说明 / 遗留
- 早期手工生成的写实版部分角色与动漫设定有偏差（如曹操写实版偏女性化）；现在可用「重新生成写实版」逐个修正
- 单版本生成约 12s（并发时 20–60s）；写实版用 RealVisXL，与动漫版风格差异明显
- 角色卡未加版本小标记（避免列表额外请求）
