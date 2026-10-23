# studio2d · 离线程序化 2D 动漫角色生成器

「造片局 · AI 微综艺」角色工坊的 **2D 风格**实现：不依赖任何模型/网络/GPU，纯 Python + Pillow
代码绘制原创日系赛璐璐（anime cel）动漫角色，并输出角色工坊契约（`contract/CHARACTER.md`）规定的资产。

## 快速开始

```bat
python -m studio2d.cli create --spec contract/characters/laozhou.json --outdir outputs/characters/laozhou/2d
```

可选参数：

| 参数 | 说明 |
| --- | --- |
| `--spec` | 角色 spec JSON（必填，按 `contract/CHARACTER.md` 字段） |
| `--outdir` | 产物目录（必填） |
| `--style` | `auto`（默认，从 spec.style 推断）、`2d_cel`、`anime_cel`、`guoman` |
| `--size` | portrait 边长，默认 768 |
| `--no-sheet` | 跳过 sheet.png |

另有 `validate` 子命令：只解析 spec 并打印派生调色板/参数，便于排错：

```bat
python -m studio2d.cli validate --spec contract/characters/ali.json
```

## 产物

```
outputs/characters/<id>/2d/
  portrait.png        # 半身像 768x768，透明背景
  portrait_bg.png     # 半身像 768x768，深色影棚背景
  expressions/
    neutral.png       # 512x512，透明背景
    happy.png
    surprised.png
    thinking.png
  sheet.png           # 1600x900 设定图：立绘 + 四表情 + 名字/身份/配色条
  meta.json           # {id,name,style,renderer:"pillow",palette,files,elapsed_sec,...}
```

CLI 最后一行打印 JSON 摘要（含 ok/outdir/files/elapsed_sec）；失败时 exit 1 并输出 stderr。

## 设计要点

- **4x 超采样**：所有图形先画在 4 倍画布（`Scene`，`shapekit.SS=4`），最后 LANCZOS 缩小，线条平滑（3–5px 描边）。
- **头身比 1:2.5~3**：`head_scale`、`chibi` 参与头/身比例；头部略大是「一眼日漫」的关键。
- **大眼构成**：眼白 → 虹膜竖向渐变 → 深色瞳孔 → 双高光（主光+副光）→ 顶部眼睑投影 → 上睫毛（变宽笔触，眼尾飞挑）→ 双眼皮褶 → 下眼睑线。
- **表情可切换**：`face.EXPR` 控制眼裂开合/闭眼笑弧/视线偏移/眉高/嘴型（微笑、开口笑+牙齿舌头、O 形、平嘴）。
- **发型**：`short_swept/spiky/bob/twin_tail/ponytail/long_straight/bald/bun`，由「发盖 + 发际线基座 + 分层发绺（贝塞尔变宽笔触）+ 高光条」组合；后发（双马尾/长直/马尾/丸子头）在头部之前绘制。
- **服装**：`suit/hoodie/jacket`，含翻领/领口/衬衫 V/领带/口袋巾（suit）、帽兜/抽绳/前兜（hoodie）、立领/拉链/肩部 accent 条（jacket），并绘制袖缝、衣褶、扣子。
- **配饰**：`glasses`（半透明镜片+镜腿）、`headphones`（头梁+耳罩）、`watch`（立绘腕部），由 spec.accessories 驱动。
- **面部标记**：`warm_smile` 笑纹、`star_clip` 星星发夹、`scar_brow` 眉骨疤，由 spec.marks 驱动。
- **配色**：全部取自 spec（skin/hair_color/eye_color/outfit.*/accent_color），并派生阴影/高光；meta.json 输出完整 palette。
- **深色影棚背景**：竖直渐变 + accent 聚光 + 地面反光 + 暗角（`render.make_studio_bg`）。
- **guoman 变体**：`--style guoman` 走暖色调（线稿偏棕、背景更暖），其余同 cel 流程。

## 代码结构

```
studio2d/
  __init__.py    # 版本/渲染器标识
  shapekit.py    # 颜色/几何（Catmull-Rom、变宽笔触、发绺）/Scene 图元/渐变/字体
  spec.py        # spec 读取、风格解析、派生调色板与绘制参数（Character）
  face.py        # 头型、五官、表情、胡须/标记、全部发型
  body.py        # 躯干/腿/手臂、suit/hoodie/jacket、配饰
  render.py      # 组装 portrait / expressions / 立绘 / sheet 与影棚背景
  cli.py         # create / validate 命令行入口
```

## 可重复性

- 完全离线、无随机网络；随机细节（发丝分隔、胡茬）使用 `zlib.crc32(id)` 作为种子，**同一 spec 任意次运行输出一致**。
- 已验证：同一 spec 连续运行两次，portrait.png / sheet.png 的 sha256 相同。

## 依赖

- Python 3.11+
- Pillow（仅此一个第三方依赖；不引入 numpy/cairo 等）
- 中文字体使用系统 `C:/Windows/Fonts/msyh.ttc`（缺失时回退 simhei/simsun，再回退 Pillow 默认字体）

## 与其他模块的边界

- 只写 `studio2d/` 与 `outputs/characters/<id>/2d/`；不修改 `studio3d/`、`pipeline/`、`backend/`、`frontend/`、`contract/`、`docs/`。
- 与 Blender 3D 路线并行：3D 产物在 `outputs/characters/<id>/`（如 `style_3d_toon/`），2D 产物统一收在 `<id>/2d/`，互不覆盖。
