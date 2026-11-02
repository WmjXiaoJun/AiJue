# V19：全局 UI 框架重设计 + 角色清理

> 用户（2026-11-02 17:54，附图纸片人示例）：「这种角色全部都删除了。还有你看整体的 UI 框架，现在乱乱的啊，我需要你全面的重新设计。」

## 0. 设计读

**Reading this as: 面向中文内容创作者的 AI 视频生产工作台（高密度产品 UI，非营销页），品牌语言 = 电影感暗金 + 底片颗粒；方向 = 统一 App Shell：240px 主轨 + 64px 顶栏 + 页内上下文 tabs + 单一内容容器。**

- 设计拨盘：`VARIANCE 4 / MOTION 3 / DENSITY 5`（产品 UI，克制、信息密度中等偏高、动效只用于状态反馈）
- 不引入第三方设计系统：沿用现有 `cinema.css` 品牌 tokens，只做「一套 shell + 一套组件规范」
- 不用 emoji、不用纯黑阴影、不用 AI 紫渐变；一张页面只保留一个主 CTA

## 1. 现状问题（审计，截图 `tmp/v19/`）

| 页面 | 现有外壳 | 问题 |
|---|---|---|
| `#/projects` 项目大厅 | 独立 rail「造片局 CINEMA STUDIO」+ 页面内 hero/卡片 | 与 V18 页面品牌/字幕不一致 |
| `#/pipeline`、`#/characters` | 独立 rail「造片局 ONE-CLICK STUDIO / CAST & VOICE」 | 与项目大厅 rail 视觉近似但代码/样式各一套；编号 01-05 混乱 |
| `#/cinema/*` | 独立 sidebar「CINEMA STUDIO」+ 专属顶栏/项目条 | 又一套导航；与全局 6 项重复 |
| `#/v5/*` | 独立 sidebar「V5 世界」+ 专属顶栏 | 第三/第四套；同一功能入口多处 |
| `#/episodes/<eid>/*` | 通栏 workspace 头 + 01-06 tabs + 右上角三处跳转链接 | 与其它页 header 结构/宽度/字号不一致；`返回平台` 重复出现 |
| 角色页 | 32 角色卡（其中 8 个纸片人占位） | 占位角色视觉质量差（用户要求删除） |

结论：**不是局部修饰，而是外壳重构成一套 AppShell**（Redesign - Overhaul，保留 IA 与品牌 tokens）。

## 2. 目标信息架构（一套 Shell）

```
┌───────────────┬─────────────────────────────────────────────────────────────┐
│ 造片局        │ 顶栏 64px: 面包屑/页标题  ··············  状态chips | 主CTA │
│ ZAOPIANJU     ├─────────────────────────────────────────────────────────────┤
│               │ 上下文 tabs（仅模块页有：Cinema / V5 / 工作台 / 控制台步骤）  │
│ ● 项目大厅 01 │                                                             │
│ ● 一键成片 02 │ 内容区（统一 padding 28px、最大宽 1600、卡片 12px 圆角）    │
│ ● 角色与声音03│                                                             │
│ ● 模板库 04   │                                                             │
│ ● V5 世界 05  │                                                             │
│ ● 设置 06     │                                                             │
│               │                                                             │
│ （底部）GPU/后端状态                                               │
└───────────────┴─────────────────────────────────────────────────────────────┘
```

- **主轨 240px**（≤1280px 收起为 72px 图标轨），只有 6 个一级入口 + 底部系统状态；不带二级导航
- **顶栏统一 64px**：左侧「模块名 · 页面名」+ 可选面包屑（项目/期）；右侧统一状态 chips（后端 / GPU / pipeline / 任务数）+ 一个主 CTA（项目大厅=新建项目，其余=一键成片）
- **上下文 tabs**：Cinema（总览/模板库/镜头台/生成队列/剪辑台/成片&QC）、V5（总览/模板/角色/场景/画廊/渲染台）、工作台（01剧本…06 3D 模板）都改为顶栏下方的水平 tabs，不再各自画 sidebar
- 一级入口点进去默认页：项目大厅 / 一键成片(选模板) / 角色与声音 / Cinema 模板库 / V5 画廊 / 设置弹层
- 删除所有「返回平台 / Cinema 3D ↗ / 一键成片 ↗」等重复跳转链接；返回项目 = 点主轨「项目大厅」

## 3. 视觉规范（锁死）

- 色板：底 `#0b0b0f` / panel `#121216` / raised `#17171c` / border `rgba(201,162,39,.16)` / 文本 `#f2ead8` / 次级 `#9a958a` / 金 `#c9a227` / 玉 `#4f8f7c` / 危险 `#b4524a`
- 字体：品牌/大标题 KaiTi（现有）；UI = 系统无衬线；数字/路径 = mono
- 圆角：卡片 12px、控件 8px、chip 全圆；**全站一套**
- 间距：内容 padding 28px；卡片 gap 16/24；section 间距 32
- 阴影：低饱和黑金（`0 12px 40px rgba(0,0,0,.35)`），不用纯黑硬阴影；hover 只做边框/亮度变化
- 空态/加载/错误：骨架屏 + 明确文案 + 下一步按钮；不再只有 spinner
- 响应式：≥1600 内容居中 max 1600；1280-1600 自适应；1024-1280 图标轨；<1024 单栏（rail 抽屉）

## 4. 角色清理（数据）

删除 8 个「纸片人/占位」角色（用户附图即其中之一）：
`custom_lin`（林同（示例））+ `real_host`、`real_street_runner`、`real_street_owner`、`real_cafe_owner`、`real_cafe_regular`、`real_kitchen_chef`、`real_kitchen_regular`（7 个写实角色无概念图，渲染为灰纸片人）。

- 删除 `contract/v5/characters/real_*.json`、`contract/v5/custom/characters/real_*.json`、`contract/v5/custom/characters/custom_lin.json`
- 3 个写实模板 cast 重映射到现有高质量角色（保持 19 模板目录有效）：
  - `real_street`：host→laozhou，figure_a→xueba，figure_b→tianmei
  - `real_cafe`：host→laozhou，figure_a→tianmei，figure_b→dawei
  - `real_kitchen`：host→laozhou，figure_a→pangzi，figure_b→xueba
- `contract/v5/voice_casting.json` 移除这 8 个角色（保留 24 个），同步自查 22 模板 cast 无悬空引用
- 校验：`v5_catalog.load_catalog()` errors 0；`GET /api/v5/casting?template=real_*` 返回新 cast；角色页 24 卡、无纸片人
- 清理后需确认 3D 模板重生成不受影响（STYLE3D/semantic 已缓存，重生成时会用新 cast，属预期）

## 5. 验收

1. 8 条路由全部渲染在**同一 AppShell**（项目大厅 / 一键成片 / 角色与声音 / Cinema·模板库 / Cinema·成片&QC / V5·画廊 / 工作台·剧本 / 设置弹层）：无双 sidebar、无重复品牌块、无重复「返回平台」
2. 导航高亮/上下文 tabs 正确；1024/1280/1600 三档截图正常
3. 角色页 24 角色、无纸片人；casting/voices/试听仍正常
4. `tsc` 0 error、`vite build` 通过；控制台无 JS error
5. 截图 before/after 各 8 张 + 文档 + commit

## 6. 分工

- **tpl-frontend**：AppShell + 全站迁移（本轮唯一前端写者）
- **studio-a**：角色删除 + 模板 cast 重映射 + voice_casting 清理 + 校验
- **Lead**：审计/规范/复核/E2E/截图/commit/交付
