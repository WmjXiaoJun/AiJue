# V20：彻底统一信息架构（合并 V5 世界 + Cinema + 角色/模板/成片）

> 用户：「V5 世界是不是和现在的功能也重复啊？请你再仔细看一下然后设计，需要完全统一的一套。」
> 结论：确实重复。V5 世界 6 个 tab 与现有页面重叠 5 个：

| V5 世界 tab | 重复对象 | 处理 |
|---|---|---|
| 成片画廊 gallery | Cinema「成片 & QC」+ Final | 合并为全局「成片库」 |
| 模板库 templates | Cinema「模板库 · 3D」（同一批 19 模板） | 合并进「模板与素材」 |
| 角色库 characters | 全局「角色与声音」 | 删除 V5 角色库（保留全局页） |
| 渲染台 render | 一键成片 / Cinema 生成队列（且是已淘汰的 2D render_v5） | 下线（保留后端 API，不进入 UI） |
| 风格墙 styles | 唯一资产 | 合并进「模板与素材 · 风格」 |
| 场景库 scenes | 唯一资产 | 合并进「模板与素材 · 场景」 |

另：Cinema「总览」与项目大厅统计重复；工作台「06 3D 模板」与模板库重复。

## 1. 统一后的信息架构（唯一一套）

一级导航（AppShell 主轨，6 项）：

```
01 项目大厅     #/projects
02 一键成片     #/pipeline
03 角色与声音   #/characters
04 模板与素材   #/library/<tab>     tabs: templates(19 3D) | styles(17) | scenes(70)
05 成片库       #/library/final     19 支 3D 成片 + QC/报告/播放
06 设置         弹层
```

- **3D 制作台**（原 Cinema Studio）不再是一级入口：只保留 `镜头台 / 生成队列 / 剪辑台` 三个上下文 tab，从「一键成片 · 第 4 步」「模板与素材 · 模板卡」「成片库 · 卡片」进入；路由 `#/cinema/<shots|jobs|assemble>` 兼容旧链接，`#/cinema/overview|templates|final` 重定向到新页面。
- **V5 世界整体下线**（路由与导航都移除），旧链接重定向：
  - `#/v5/gallery` → `#/library/final`
  - `#/v5/templates` → `#/library/templates`
  - `#/v5/styles` → `#/library/styles`
  - `#/v5/scenes` → `#/library/scenes`
  - `#/v5/characters` → `#/characters`
  - `#/v5/render` → `#/pipeline`
- 工作台 tabs：01 剧本间 / 02 资产库 / 03 分镜台 / 04 生成台 / 05 成片房（**移除 06 3D 模板**，改由模板与素材/一键成片进入）。

## 2. 页面内容归属（唯一数据源）

| 页面 | 内容 | 复用组件 |
|---|---|---|
| 模板与素材 · 模板 | 19 个 3D 模板（海报/时长/状态/写实筛选）| `cinema/Templates`（适配无页头版） |
| 模板与素材 · 风格 | 17 个风格墙（色板/技法/示例帧）| `v5world/Styles` |
| 模板与素材 · 场景 | 70 个分层场景 | `v5world/Scenes` |
| 成片库 | 19 支成片（播放/时长/大小/报告/QC/重新合成）| `cinema/Final`（+ 画廊式筛选） |
| 3D 制作台 | 镜头台 / 生成队列 / 剪辑台 | `cinema/Shots`、`cinema/Jobs`、`cinema/Assemble` |
| 角色与声音 | 24 角色 + 24 音色库（已统一）| 现有 `characters/CharactersPage` |
| 一键成片 | 6 步 pipeline 控制台 | 现有 `pipeline/PipelineConsole` |

禁止再出现：V5 世界导航/路由、L5 角色库、2D 渲染台入口、Cinema 总览、工作台 06 tab、任何「两个入口做同一件事」。

## 3. 验收

1. 主轨只有 6 项；四处页面里不再出现第二个「模板库/成片画廊/角色库」
2. 旧 URL（#/v5/*、#/cinema/overview|templates|final）全部重定向正确、不白屏
3. 模板与素材 3 tab 数据正确（19 模板 / 17 风格 / 70 场景）；成片库 19/19；制作台 3 tab 可用
4. 一键成片/工作台入口链接到制作台正确；工作台只剩 5 tab
5. `tsc` 0 error、`vite build` 通过、8 条主路由 + 3 个制作台 tab console 无 JS error
6. 截图 before/after（V5 六 tab vs 新页面）到 `tmp/v20/`

## 4. 分工

- **tpl-frontend**：导航/路由重构、`library` 新页面（templates/styles/scenes/final）、Cinema 制作台瘦身、V5 下线与重定向、工作台去 06 tab、入口链接；截图与自测
- **Lead**：审计/规范/复核/E2E/截图/commit/交付


## 5. 交付结果（2026-11-02 18:35）

### 已上线（唯一一套 IA）
- 主轨 6 项：项目大厅 01 / 一键成片 02 / 角色与声音 03 / **模板与素材 04** / **成片库 05** / 设置 06
- 新页面 `#/library/<tab>`（`components/library/LibraryPage.tsx` + `library.css`）：
  - `templates`：19 个 3D 模板（全部/原16/写实筛选 + 搜索；播放/重生成/镜头台）
  - `styles`：17 个风格墙（复用 V5 Styles）
  - `scenes`：70 个场景（复用 V5 Scenes）
  - `final`：19 支成片库（搜索/筛选；播放/报告/QC/重新合成/镜头台/下载）
- **V5 世界下线**：主轨入口与 V5World 路由移除；旧 URL 10/10 重定向 PASS（v5/gallery→library/final、v5/templates→library/templates、v5/styles→library/styles、v5/scenes→library/scenes、v5/characters→characters、v5/render→pipeline）
- **Cinema 制作台瘦身**：只保留 镜头台/生成队列/剪辑台（`#/cinema/shots|jobs|assemble`，不作一级导航）；旧 cinema/overview→projects、cinema/templates→library/templates、cinema/final→library/final
- **工作台**：移除 06 3D 模板 tab（5 tab：剧本/资产/分镜/生成/成片）
- 交叉链接全部更新：项目大厅模板卡→模板与素材；一键成片第 4 步新增「镜头台/生成队列」；成片库卡→镜头台/报告；角色页去 V5 链接

### 验证
- 旧 URL 重定向：**10/10 PASS**（`tmp/v20/verify.py`）
- 主路由 11 条（含制作台 3 tab）渲染正常、**无双 V5/无渲染台/无第二模板库/第二成片画廊**；console 无 JS error（资源 404 为立绘回退，见遗留）
- 模板与素材：19 模板 / 17 风格 / 70 场景；成片库 19/19；制作台 3 tab；工作台 5 tab
- `tsc` 0 error、`vite build` 通过（495KB / gzip 147KB）
- 截图：`tmp/v20/after2/`（11 张 1680 + 3 张 1280）；before（旧 V5 六 tab）：`tmp/v20/before/`

### 遗留
1. 已无路由引用的旧组件（`v5world/V5World.tsx`、`v5world/Gallery.tsx`、`v5world/Templates.tsx`、`v5world/Characters.tsx`、`v5world/Render.tsx`、`studio/Templates3D.tsx`、`cinema/Overview.tsx`、`cinema/Final.tsx`）暂留（Styles/Scenes 仍被 Library 复用其 props 类型），后续可清理
2. 角色立绘多级回退导致控制台资源 404（不影响功能），可后续批量生成统一立绘
3. 2D 渲染台入口已下线，但后端 `/api/v5/render` 仍保留（历史项目兼容）
4. 成片库「报告/QC」依赖产物 `qc_report.json` 存在，个别模板无 QC 文件时按钮提示
