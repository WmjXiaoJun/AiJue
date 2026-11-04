# V44：2D 风格子类型（每个模板可选）

> 用户确认：「每个模板可选 2D 风格子类型」可以。为 2D 节目提供 4 种风格子类型，写入模板合同，生成时按子类型选择 SDXL checkpoint/提示词与 B-roll 风格。

## 1. 风格子类型（4 种）
| id | 名称 | imagegen style | 关键帧/ B-roll 追加提示词（要点） |
|---|---|---|---|
| `jp_anime` | 日漫电影感（默认） | anime | Japanese anime film still, cinematic anime key visual, detailed lineart, soft cel shading, film grain |
| `guoman` | 国漫国风 | guoman | Chinese guofeng animation film key visual, elegant ink-wash color accents, detailed Chinese animation |
| `retro_cel` | 复古赛璐璐 | anime | 90s retro cel anime, bold clean outlines, vintage anime screenshot, slight film grain |
| `soft_watercolor` | 治愈手绘 | anime | hand-painted watercolor anime, soft warm palette, gentle healing atmosphere, hand-drawn textures |

- 子类型对 **对话关键帧**（imagegen `--style` 与 `--prompt-extra`）和 **B-roll T2VA prompt** 同时生效；ref2va 的 2D 风格句也拼接对应描述
- 3D/写实模板不显示/不使用该字段

## 2. 合同与接口
- 模板合同新增 `render_style`（仅 2D 有意义；缺省 `jp_anime`）；POST /api/v5/templates 接受并校验（非法 400）；列表/详情返回 `render_style` + 中文 label
- hq CLI：新增 `--2d-style {jp_anime,guoman,retro_cel,soft_watercolor}`（默认 jp_anime）；only style-mode=2d 时生效
- backend 2D 分派读取模板 `render_style` 并透传 `--2d-style`
- 缓存键包含风格（防不同风格复用同一关键帧/镜头）

## 3. 前端
- 新建模板向导 step1：选 2D 后出现「2D 风格」四选一卡片（名称+一句话说明）；提交 `render_style`
- 模板库 2D 卡片显示风格标签（如 2D·国风）；一键成片类型 chip 显示「2D · 国漫国风」
- 截图 tmp/v44/ui/：向导风格选择、模板卡风格标签、一键成片 chip

## 4. 验收
1. 创建 `render_type=2d, render_style=guoman` 的 4 幕模板 → 真实生成（约 8–15 分钟）→ 抽帧为明显的国风动漫（与日漫样本不同）
2. 默认 jp_anime 回归（现有 2D demo）
3. 3D/写实模板不受影响（dry-run 仍 3d）
4. tsc 0 / build / console 无 error；docs/V44 + commit

## 5. 分工
- **tpl-backend**：合同字段/接口/校验 + hq `--2d-style` 与提示词映射/缓存 + 真实国风样本
- **tpl-frontend**：向导风格四选一 + 卡片/chip 标签 + 截图
- **Lead**：真实国风样本审查（与日漫对比）、UI 验证、docs、commit


## 6. 交付结果（2026-11-04 16:20）

### 实现
- 模板合同 `render_style`（jp_anime 默认 / guoman / retro_cel / soft_watercolor，仅 2D）；POST /api/v5/templates 校验并写合同；列表/详情/status/catalog 返回 `render_style` + 中文 label（日漫电影感/国漫国风/复古赛璐璐/治愈手绘）；非法 400
- `pipeline/templates3d_hq.py`：`--2d-style`（默认 jp_anime，仅 2d 生效）
  - 对话关键帧 imagegen `--style` 映射：jp_anime→anime、guoman→guoman、retro_cel→anime、soft_watercolor→anime；prompt-extra 按风格追加
  - B-roll T2VA prompt 同风格词；ref2va 2D 风格句拼接
  - 关键帧缓存 `keyframes/<tid>/<style>/<shot>.png`；2D 镜头缓存 `_2d_v44_<style>`（切风格不复用）
- backend 2D 分派读取 `render_style` 透传 `--2d-style`；3D/写实不传
- 前端：向导选 2D 后出现「2D 风格」四选一卡片；模板卡标签「2D·日漫 / 2D·国风 / 2D·赛璐璐 / 2D·手绘」；一键成片 chip「2D · 国漫国风」等；3D/写实不显示风格

### 真实国风样本（custom_tpl_v44_guoman，4 幕）
- job `pl_52031765be4a` **done 490.5s（8m10s）**
- 成片：**4,601,668B / 14.142s / 1280×720 / 25fps / H.264 / AAC**，poster 已覆盖；`/media/templates_2d/custom_tpl_v44_guoman/final.mp4` 200
- 抽帧：`tmp/v44/frames/frame_01~06.png`（中式木构/灯笼/水墨屏风/宫殿屋檐/雨窗中文霓虹/书法屏风）
- 风格对比：`tmp/v44/2d_jp_vs_guoman.jpg`（左日漫电影感、右国漫国风，同角色同场景）
- UI 验证：模板库 2D 筛选两张卡分别「2D·日漫」「2D·国风」；一键成片 chip「2D 动画 · 国漫国风」+ 第④步「2D · 国漫国风」；console 无 error

### 默认回归
- `custom_tpl_v41_2d_demo` → jp_anime/日漫电影感，成片不变
- 3D/写实 dry-run 仍 `--style-mode 3d`，无 `--2d-style`

### 说明 / 遗留
- 4 风格已全部接入生成映射；真实出片样本：日漫（V43，8m15s）与国风（V44，8m10s）；retro_cel / soft_watercolor 仅接口与映射验证，可继续出样片
- 国风关键帧背景仍有随机性，H3 ref2va 成片会补中式场景；如需更强国风可继续调 prompt/checkpoint
- 成片 1280×720/25fps（H3 镜头 1024×576，V5 剪辑统一输出）
