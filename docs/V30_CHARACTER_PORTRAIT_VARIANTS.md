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
