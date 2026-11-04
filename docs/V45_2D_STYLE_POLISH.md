# V45：2D 风格精修（不同 checkpoint + 角色年龄气质 + IP-Adapter 权重）

> 用户确认三项都做：①每种风格用不同 checkpoint + 提示词；②关键帧提示词按角色年龄/气质加强（例：老周 42 岁要成熟）；③调低 IP-Adapter 权重（0.75 偏高，脸稳但背景抽象）；并补齐 4 种风格样片。
> 目标：4 种风格各出一条真实 4 幕样片（同模板结构、同 cast），可直接对比；风格标签改「吉卜力治愈系」。

## 1. 4 种风格 v2（checkpoint + 提示词 + IP 权重）
| 风格 id | 名称 | imagegen checkpoint/style | 提示词要点 | IP-Adapter |
|---|---|---|---|---|
| `jp_anime` | 日漫电影感 | anime | Japanese anime film still, cinematic key visual, detailed lineart, soft cel shading, film grain | 0.58 |
| `guoman` | 国漫国风 | guoman | Chinese guofeng animation film key visual, ink-wash accents, Chinese architecture, elegant color | 0.58 |
| `retro_cel` | 复古赛璐璐 | illustrious | 90s retro cel anime, bold outlines, vintage screenshot, slight film grain, limited palette | 0.52 |
| `soft_watercolor` | 吉卜力治愈系（label 更新） | anime | hand-painted watercolor anime, Ghibli-inspired soft warm palette, gentle healing atmosphere, painterly backgrounds | 0.52 |

- hq 新增 `--ip-strength`（默认按风格，可覆盖）；关键帧生成记录实际权重
- 背景细化：关键帧 prompt 追加 `detailed background, environment art, rich scene detail`；IP 权重降低让背景更充分生成

## 2. 角色还原度（年龄/气质）
- 读取角色合同 `appearance.age`、`gender`、`persona`：
  - 年龄：≥35 → `mature adult, age-accurate, middle-aged appearance, no youthful face`；<30 → `young adult, age-accurate`；并生成负面词（老角色禁 `young/teen/chibi/baby face`，年轻角色禁 `middle-aged/elderly`）
  - 气质：persona 中文关键词映射（老练/冷幽默/控场稳 → `composed, experienced, calm`；语速快/乐观 → `energetic, cheerful` 等）
- 关键帧 prompt 里加入 `adult male, age 42, mature` 等硬约束；B-roll 同风格但不强加角色年龄

## 3. 样片（4 条，真实生成）
- 新建 4 个 2D 模板（同一 cast：laozhou/ali/tianmei，同一 4 幕结构/场景），分别 `render_style=jp_anime / guoman / retro_cel / soft_watercolor`
- 每个模板 auto_generate 真实生成（约 8–12 分钟/条，共约 40 分钟）
- 保留 4 个模板作为「2D 风格示例」；删除旧 demo 模板（custom_tpl_v41_2d_demo / custom_tpl_v44_guoman）避免重复
- 抽帧 `tmp/v45/<style>/frame_01~06.png`；成片 ffprobe/耗时写入报告

## 4. 前端
- 第 4 种风格 label 改为「吉卜力治愈系」，卡片说明更新（手绘水彩/吉卜力风）
- 向导/卡片/chip 显示风格不变，其余提示同步

## 5. 验收
1. 4 条样片均 done，成片可播放；抽帧风格差异明显（日漫/国风/赛璐璐/治愈）
2. 老周在样片中呈现 40+ 成熟男性（不再少年感）
3. 背景细节较 V44 更完整（IP 权重 0.52–0.58）
4. 3D/写实 dry-run 不受影响；tsc/build/console 正常
5. docs/V45 + commit

## 6. 分工
- **tpl-backend**：风格 checkpoint/提示词/IP 权重/角色年龄气质 + 4 个 demo 模板与 4 条样片 + 抽帧
- **tpl-frontend**：吉卜力治愈系 label/说明更新 + 截图
- **Lead**：审 4 条样片、对比图、清理旧 demo、docs、commit
