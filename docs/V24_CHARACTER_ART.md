# V24：创建角色 → 生成概念图/立绘（一步到位）

> 现状：角色创建后只有程序化预览（纸片感）；已有 24 个角色都有 `assets/v5/characters/<id>/portrait.png`（imagegen/SDXL 生成），新创建角色没有。
> 目标：新建角色时可选「创建后生成概念图」，生成 SDXL 立绘写入 `assets/v5/characters/<id>/portrait.png`，角色卡/详情直接显示正式立绘；已有角色也可在详情里「重新生成概念图」。

## 1. 现有能力（复用）
- imagegen CLI：`python -m imagegen.cli generate --spec <spec.json> --outdir <dir> --mode portrait [--style anime|illustrious|guoman] [--seed]`
- 角色美术目录：`assets/v5/characters/<id>/portrait.png`，后端 `/media/v5/characters/<id>/...` 已可访问（v5_studio 路由）
- 参考：`tmp/v52_art/specs/*.json` 是现有 24 角色的 imagegen spec（字段：id/name/title/persona/style{primary,variants}/appearance{gender,age,skin,hair_color,hair_style,eye_color,eye_size,brow,facial_hair,outfit{type,main,accent,shirt},accessories,marks}/extra_tags）
- 后端概念图 job（DB 角色）走 `jobs.run_concept_job`，含 GPU 串行锁；本轮为 contract 角色新增独立端点，需共用串行锁（或简单全局锁排队）

## 2. 后端
### POST /api/v5/characters/{character}/art
body `{mode?: "portrait", style?: "anime"|"illustrious"|"guoman", prompt_extra?: str, seed?: int, force?: bool}`
- 仅对 catalog 角色（内建/custom）生效；构造 imagegen spec（contract appearance → imagegen 字段映射，缺省补默认；title/persona 合并进 prompt）
- 串行执行 imagegen（GPU 锁，避免与 3D 生成冲突；同一角色已有生成中返回 409/复用）
- 输出 `assets/v5/characters/<character>/portrait.png`（可选 `sheet.png` 暂不做）；成功返回 `{ok, character, portrait_url:"/media/v5/characters/<id>/portrait.png?t=...", elapsed_sec, style, seed}`
- 失败返回 500 可读错误；`force=false` 且已存在 portrait 时直接返回缓存（`cached:true`）
- 建议抽 `_character_art_spec(character)` + `_run_imagegen(...)`，供创建/详情复用

### POST /api/v5/characters 增加可选 `auto_art: bool = false`
- 角色写入成功后若 `auto_art=true`：同步生成概念图（预计 20–60s，前端超时 ≥120s），返回 `art:{generated, portrait_url, elapsed_sec, cached}`；失败不阻塞创建（`art.generated=false, error`）
- 无 auto_art → art:null（向后兼容）

### 验收自测
- 新建角色 auto_art=true → portrait.png 存在、/media 200、返回 portrait_url；重复调用 force=false 返回 cached
- 已有角色（如 laozhou）调 art force=true → 重新生成成功
- 串行：同时两个 art 请求不会并行抢 GPU（返回 409 或排队）
- 测试角色用完 DELETE 清理；写 tmp/v24/backend_selftest.md

## 3. 前端
- 新建角色抽屉：增加 checkbox「创建后生成概念图（约 20–60 秒）」默认勾选；提交按钮文案「创建并生成中…」；成功后刷新/自动打开详情，卡片显示新立绘（cache-bust `?t=`）
- 角色详情抽屉：新增「生成概念图 / 重新生成」按钮 + loading + 错误提示；成功后卡片与详情立绘即时更新
- 角色卡：优先显示 `assets/v5/characters/<id>/portrait.png`（已有 `characterArtUrl` 回退链），生成后需 cache-bust；无图时保持程序化预览
- UI 文案/状态与 shell 一致；错误可读（400/409/500）
- 截图：创建中、创建成功带立绘、详情重新生成、失败态

## 4. 验收
1. UI：新建角色勾选生成概念图 → 创建完成 → 角色卡显示 SDXL 立绘（非程序化）
2. API：art 端点 cached/force/并发行为正确；auto_art 创建返回 portrait_url
3. tsc 0 / build 通过 / console 无 JS error；截图 tmp/v24/
4. docs/V24 + commit

## 5. 分工
- **tpl-backend**：art 端点 + spec 映射 + GPU 锁 + auto_art + 自测
- **tpl-frontend**：创建抽屉 checkbox/loading、详情重新生成、立绘 cache-bust、截图
- **Lead**：真实创建+生图验证、截图、docs、commit
