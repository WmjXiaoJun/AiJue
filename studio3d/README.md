# studio3d · 造片局角色工坊（程序化原创动漫角色生成器）

用 **Blender 5.0 无头渲染 + Python 程序化建模** 从一份角色 spec（JSON）生成**完全原创**的动漫角色立绘、
表情、三视图设定稿与转面视频。不导入任何外部模型/贴图，所有几何、材质、配色均由代码生成，
因此可离线、可重复、可批量。

## 依赖
- Blender 5.0.1（默认 `C:/Program Files/Blender Foundation/Blender 5.0/blender.exe`，可用环境变量 `BLENDER_BIN` 覆盖）
- Python 3.11 + Pillow + numpy（用于合成深色影棚背景 / 设定稿排版）
- ffmpeg（默认读取 `tools/node_modules/ffmpeg-static/ffmpeg.exe`，可用 `FFMPEG_BIN` 覆盖）用于 turntable H.264 编码

## 用法
```bash
# 单角色单风格
python -m studio3d.cli create --spec contract/characters/laozhou.json \
    --outdir outputs/characters/laozhou --styles 3d_toon

# 多风格（3D 首选 + 2D 平涂正交）
python -m studio3d.cli create --spec contract/characters/laozhou.json \
    --outdir outputs/characters/laozhou --styles 3d_toon,2d_cel

# 仅重出部分产物（调试用）
python -m studio3d.cli create --spec ... --outdir ... --styles 3d_toon --only portrait,expressions
```
成功时 exit 0，stdout 最后一行为 JSON 摘要：
`{"ok":true,"id":"laozhou","outdir":"...","styles":["3d_toon"],"files":[...],"elapsed_sec":12.3}`
失败 exit 1（stderr/JSON 里带 error），单次 Blender 调用有 175s 超时保护。

## 产物
```
<outdir>/
  portrait.png              768x768 半身像（深色影棚背景）
  sheet.png                 1600x900 设定三视图（正面/侧面/四分之三 + 名字/人设/配色条/色值）
  expressions/neutral|happy|surprised|thinking.png   512x512 表情
  turntable.mp4             720x720 / 24fps / H.264 转面（48 帧 = 2s）
  meta.json                 {id,name,styles,renderer,elapsed_sec,palette,files,...}
  cutout/portrait.png       透明背景 RGBA（供成片抠像叠背景）
  cutout/expressions/*.png  透明背景表情
  style_<style>/            每种风格一套完整产物（含各自 raw/ 与 cutout/）
```
根目录 = spec 里 `style.primary` 风格；`meta.json.files` 给根目录相对路径，
`styles_detail.<style>.renders` 给每个 Blender 渲染任务耗时。

## 角色 spec（contract/CHARACTER.md §1）
必填字段与取值见契约；未填字段由 `presets.py` 补默认值。支持：
- `appearance.hair_style`: short_swept / spiky / bob / twin_tail / ponytail / long_straight / bald / bun
- `appearance.outfit.type`: suit / hoodie / jacket / shirt / dress / coat（含领口/袖口/accent 配色）
- `appearance.accessories`: glasses / watch / headphones / earring
- `appearance.facial_hair`: none / stubble / goatee；`brow`: thick / soft / thin；`marks`: warm_smile / star_clip / scar_brow
- `style`: 3d_toon / 3d_cel / 2d_cel / 2d_paint / anime / guoman（2D 系列走正交相机 + 平涂 Emission + 强描边）

## 实现结构
```
studio3d/
  cli.py                CLI 编排：校验 spec → 逐风格调用 Blender → PIL 合成 → ffmpeg 编码 → meta.json
  presets.py            风格预设、调色板派生（肤色/发色/服装/描边/背景）、spec 归一化
  compose.py            PIL 合成：深色影棚背景（径向渐变+accent 光晕+暗角）、1600x900 设定稿排版
  blender/
    lib.py              bpy 几何/材质底层：椭球、锥体、放样、沿路径变半径管（平行传输标架）、
                        圆角盒、五角星、inverted-hull 描边、toon/cel/flat/paint 材质节点
    character.py        角色构建器：头型/耳朵/脖子/分层动漫眼（眼白/虹膜渐变/瞳孔/双高光/上下睫毛）、
                        眉/嘴/腮红/胡须、8 种发型（发帽+发束）、身体比例、6 种服装、配饰、表情重建
    render.py           无头渲染入口：3 点柔光、相机取景（肖像/表情/三视图/转面）、EEVEE 设置
```
渲染风格：
- **3d_toon** = EEVEE + Diffuse→ShaderToRGB→3 阶 ColorRamp（常数插值）色带 + inverted-hull 描边 + 柔和三点光
- **3d_cel** = 2 阶硬边色带 + 更粗描边 + 边缘提亮
- **2d_cel** = 正交相机 + 纯色 Emission 平涂 + 强描边 + 纯色背景
- **2d_paint** = 正交 + 屏幕空间竖向渐变的厚涂感 Emission + 描边

配色统一由 `accent_color` + 肤色/发色/服装色派生（`presets.palette_for`），保证同一角色跨风格配色一致。

## 性能（本机 RTX 3080 Ti / Blender 5.0.1 实测）
| 产物 | 耗时 |
| --- | --- |
| portrait 768² | ~1.2s |
| 4 表情 512² | ~5s |
| sheet（3 视图 440x820） | ~4s |
| turntable 48 帧 720² + 编码 | ~15-35s |
| 单角色单风格全量 | 15-45s |
| laozhou 双风格全量（验收命令） | ~45s |

## 说明与取舍
- 程序化风格化建模：角色为**原创**动漫化造型，不追求写实；同一 spec 永远生成同一角色（确定性）。
- turntable 默认 48 帧 @24fps = 2s（在渲染预算内保证质量；契约示例为 4s，需要时可调大帧数重跑）。
- 三视图与转面用简化的标准站姿，不含骨骼绑定/动画。
- 2D 系列使用与 3D 相同的程序化模型 + 正交相机平涂渲染（非手绘），用于演示「多风格」链路。
