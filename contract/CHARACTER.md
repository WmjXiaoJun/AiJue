# 角色工坊（Character Studio）契约 v1

目标：平台生成**完全原创的动漫角色**，支持 3D 与 2D 多种风格，可依托 Blender 程序化建模+卡通渲染；角色资产可被「资产库/分镜台/成片房」直接使用。

## 1. 角色 Spec（JSON）
```json
{
  "id": "laozhou",
  "name": "老周",
  "title": "主持人",
  "persona": "老练、冷幽默、爱抛梗",
  "voice": "zh-CN-YunxiNeural",
  "accent_color": "#F5C518",
  "style": { "primary": "3d_toon", "variants": ["3d_toon", "2d_cel"] },
  "appearance": {
    "gender": "male", "age": 42, "height_ratio": 1.0, "head_scale": 1.15, "chibi": 0.25,
    "skin": "#F2D6C3", "hair_color": "#2B2B33", "hair_style": "short_swept",
    "eye_color": "#2B2B33", "eye_size": 1.0, "brow": "thick", "facial_hair": "stubble",
    "outfit": { "type": "suit", "main": "#2F3A4F", "accent": "#F5C518", "shirt": "#F4F1E8" },
    "accessories": ["watch"],
    "marks": ["warm_smile"]
  }
}
```
字段约定：
- gender: male|female|neutral；age 16–70；chibi 0–0.4（越大越 Q）；
- hair_style: short_swept|spiky|bob|twin_tail|ponytail|long_straight|bald|bun；
- outfit.type: suit|hoodie|jacket|shirt|dress|coat；accessories: glasses|watch|headphones|earring|none；
- style 预设：3d_toon（3D 卡通渲染）、3d_cel（3D 赛璐璐）、2d_cel（2D 平涂赛璐璐）、2d_paint（2D 厚涂）、anime（日漫）、guoman（国漫）。
- 颜色一律 #RRGGBB。

## 2. Blender 生成器 CLI（studio3d 包）
```
python -m studio3d.cli create --spec <spec.json> --outdir <dir> [--styles 3d_toon,2d_cel] [--size 512]
```
产物（outdir 下）：
```
portrait.png            # 半身像 768x768，深色影棚背景
sheet.png               # 设定三视图（正面/侧面/四分之三）+ 名字/配色条，1600x900
expressions/neutral.png
expressions/happy.png
expressions/surprised.png
expressions/thinking.png
turntable.mp4           # 720x720 / 24fps / 2–4s / H.264，360° 转面（demo 用 2s=48 帧）
meta.json               # {id, name, styles, renderer, elapsed, palette, files}
style3d_toon/...        # 按 style 分别输出（若多风格）
```
- 必须离线、可重复；Windows 路径用正斜杠或双反斜杠；
- Blender 路径默认 `C:/Program Files/Blender Foundation/Blender 5.0/blender.exe`，可用环境变量 `BLENDER_BIN` 覆盖；
- 每次调用打印 JSON 摘要（最后一行）：`{"ok":true,"outdir":"...","files":[...],"elapsed_sec":...}`；
- 失败 exit 1，stderr 说明；不得卡死（超时 180s 退出）。

## 3. 后端 API（新增，demo 无鉴权）
```
GET  /api/projects/{pid}/characters -> Character[]
POST /api/projects/{pid}/characters {spec} -> Character
GET  /api/characters/{cid} -> Character
PATCH /api/characters/{cid} {name?,persona?,voice?,accent_color?,spec?} -> Character
POST /api/characters/{cid}/generate {styles?:[]} -> Job       # 调用 studio3d CLI，产物落 outputs/characters/{cid}/
GET  /api/characters/{cid}/files -> {portrait,sheet,turntable,expressions:{...},meta}
```
Character: {id,project_id,asset_id,name,title,persona,voice,accent_color,style,spec,portrait_url,sheet_url,turntable_url,expressions,status,created_at}
- 创建角色时同步在 assets 表生成 type=character 的资产（供分镜/渲染引用）；
- 生成任务复用现有 Job 体系（type=character）。

## 4. 渲染约定
- 分镜镜头的 speaker 对应角色时，mock 画面优先使用该角色 `portrait.png`/`turntable.mp4`（按镜头类型选：host_to_camera 用 portrait；reaction 用 expressions；b_roll 可叠背景）；
- 成片房渲染沿用 pipeline 的镜头片段来源优先级，不新增协议；
- 角色素材目录：`outputs/characters/{cid}/`，静态访问 `/media/characters/{cid}/...`。
