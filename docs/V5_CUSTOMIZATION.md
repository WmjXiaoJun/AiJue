# V5 用户自定义教程（改 JSON / 加角色 / 加场景 / 加模板 / 覆盖风格）

> 适用：`contract/v5/` 数据契约（V5「可变风格世界」）。
> 字段全集：[`contract/v5/SCHEMA.md`](../contract/v5/SCHEMA.md)；
> 目录说明：[`contract/v5/README.md`](../contract/v5/README.md)；
> 模块接口：`docs/V5_INTERFACES.md`。
>
> 本文所有示例都是**可运行**的：仓库里 `contract/v5/custom/` 已带一套完整的 4 件套示例
> （`custom_holo_paper` 风格 + `custom_lin` 角色 + `custom_holo_gallery` 场景 + `custom_holo_talk` 模板），
> `python -m pipeline.v5_cli validate` 对它们是绿的。

---

## 0. 5 分钟快速开始

```bash
# 1) 看目录里有什么
python -m pipeline.v5_cli list-styles
python -m pipeline.v5_cli list-scenes
python -m pipeline.v5_cli list-characters
python -m pipeline.v5_cli list-props
python -m pipeline.v5_cli list-packs
python -m pipeline.v5_cli list-templates

# 2) 校验（人类可读 + 最后一行 JSON；exit code 0=通过）
python -m pipeline.v5_cli validate
python -m pipeline.v5_cli validate --strict       # CI 用：warning 也算失败
#   顺手校验 render_v5 产物时间线（timeline.schema.json）
python -m pipeline.v5_cli validate --timeline outputs/templates_v5/tonight_show/timeline.json

# 3) 导出内置对象 → 可编辑副本（默认写 contract/v5/custom/{kind}/）
python -m pipeline.v5_cli export --kind characters --id laozhou
python -m pipeline.v5_cli export --kind props --id scroll --out tmp/my_props

# 4) 改完再校验
python -m pipeline.v5_cli validate
```

改文件不需要改代码：渲染器每次从 `contract/v5/` 和 `contract/v5/custom/` 重新读目录
（`load_catalog()`，无缓存）；后端 API 也是每次请求实时扫描。

---

## 1. 目录、优先级与合并规则

```
contract/v5/
  styles/  scenes/  characters/  props/  templates/  packs/   # 内建
  custom/                                                      # 用户覆盖（优先级最高）
    styles/  scenes/  characters/  props/  templates/  packs/
```

- `custom/` 里的同名对象**覆盖**内建对象：**深合并**（dict 递归；list / 标量整体替换）；
- 因此覆盖可以只写"要改的字段"（partial）：

```json
// contract/v5/custom/characters/laozhou.json —— 只改音色
{"id": "laozhou", "voice": {"instruction": "42岁男性，低沉沙哑的老牌主持人音色，语速偏快"}}
```

- **数组语义是替换**，不是拼接：改 `palette.accent`、`cast`、`acts`、`layers` 时要写完整新数组；
- 在 `custom/` 里**新建**对象：id 必须 `custom_` 前缀（避免和以后新增的内建对象撞名）；
- 覆盖内建对象：id/文件名与内建相同即可（不需要 `custom_` 前缀）；
- 文件命名：**文件名必须等于 id**（`neon_toon.json` ↔ `"id": "neon_toon"`）；
- 换一整套目录（多套自定义包/测试隔离）：设置环境变量 `V5_CONTRACT_ROOT=<你的目录>`。

---

## 2. 改 JSON：四类常见覆盖

### 2.1 改风格调色（partial + 数组替换）

```json
// contract/v5/custom/styles/neon_toon.json
{
  "id": "neon_toon",
  "palette": {
    "accent": ["#ff2fd6", "#33e6ff", "#ffb347", "#8ef7ff"]
  },
  "transition": "glitch_wipe"
}
```

> 只改数组里某一项做不到（数组是整体替换），请写完整新数组。

### 2.2 改角色声音 / 外观（partial）

```json
// contract/v5/custom/characters/zhuge.json
{
  "id": "zhuge",
  "voice": {"provider": "breeze", "instruction": "40岁男性，清朗沉稳，语速再慢一点，讲大道理时有停顿"},
  "appearance": {"outfit": {"type": "hanfu", "main": "#e8e0cc", "accent": "#3d5a80", "hat": "lun_jin"}}
}
```

### 2.3 改模板时间线 / 台词（partial）

```json
// contract/v5/custom/templates/history_talk.json
{
  "id": "history_talk",
  "acts": [
    {"id": "act01", "duration": 4.0, "scene": "ancient_map", "camera": "pan_right", "line": ""},
    {"id": "act02", "duration": 8.0, "scene": "war_council", "camera": "push_in",
     "line": "host", "text": "如果官渡之战曹操赢了，历史会怎么走？"}
  ]
}
```

### 2.4 改场景层（partial）

```json
// contract/v5/custom/scenes/late_night_studio.json
{
  "id": "late_night_studio",
  "camera_zones": {"wide": [0, 0, 1, 1], "host_close": [0.22, 0.18, 0.36, 0.5]},
  "anchors": {"host": [0.34, 0.9], "guest_a": [0.66, 0.9]}
}
```

---

## 3. 加角色

1. 新建 `contract/v5/custom/characters/custom_<你的id>.json`，**完整**字段（新对象不继承别人）；
2. 建议同时在 `custom/styles` 里给它指定 `style_overrides`（不写则由风格默认处理）；
3. 校验通过后即可被任何模板 `cast` 引用。

最小可用角色（完整可运行，参考 `custom/characters/custom_lin.json`）：

```json
{
  "id": "custom_announcer",
  "label": "播报员",
  "persona": "冷静利落的新闻播报员，语速快、信息密度高。",
  "role_pool": ["host", "narrator"],
  "voice": {
    "provider": "breeze",
    "instruction": "30岁女性，干练清脆的播报腔，语速偏快，句尾利落",
    "seed": 20263001,
    "cfg_scale": 4.0
  },
  "appearance": {
    "gender": "female",
    "age": 30,
    "skin": "#f2d6c3",
    "hair": "#2b2b33",
    "hair_style": "short_bob",
    "eye": "#2a2a2a",
    "brow": "straight",
    "outfit": {"type": "blazer", "main": "#1f2a3a", "accent": "#e0a33e", "shirt": "#f4f1e8"},
    "accessories": ["earpiece"],
    "marks": ["sharp_smile"],
    "head_ratio": 1.1,
    "chibi": 0.2
  },
  "poses": ["front", "side", "point", "think"],
  "mouth": ["closed", "a", "o", "e", "i", "u", "m"],
  "style_overrides": {"neon_toon": {"line": 1.2, "shade": 0.4}}
}
```

**用自己的透明 PNG 立绘**：把图片放到仓库已有目录（如 `assets/characters/custom_announcer.png`），
在角色 JSON 加 `"image": "assets/characters/custom_announcer.png"`（相对仓库根或 `contract/v5/`）。
校验器会检查文件存在，且 PNG 默认要求带 alpha（`"image_alpha": false` 可关闭检查）。

---

## 4. 加场景

场景 = 一串**分层绘制指令**（layers）+ 机位取景窗（camera_zones）+ 角色脚底锚点（anchors）。
`layers[].type` 必须由 `pipeline/scene_render.py` 注册（内置原语清单见 `docs/V5_INTERFACES.md` §1 与
`contract/v5/SCHEMA.md` §2）；`color` 可以写 hex，也可以写 palette token（`accent:1`、`shadow`、`glass`）。

```json
// contract/v5/custom/scenes/custom_rooftop.json
{
  "id": "custom_rooftop",
  "label": "霓虹天台",
  "tags": ["modern", "neon", "night"],
  "aspect": [16, 9],
  "size": [1600, 900],
  "seed": 7001,
  "layers": [
    {"type": "sky_gradient", "layer": "background", "top": "bg", "bottom": "bg:1"},
    {"type": "skyline", "layer": "background", "color": "shadow", "height": 0.4, "opacity": 0.85},
    {"type": "neon_signs", "layer": "midground", "color": "accent:1", "count": 6, "opacity": 0.8},
    {"type": "stage_floor", "layer": "midground", "color": "floor", "horizon": 0.74},
    {"type": "railings", "layer": "foreground", "color": "highlight", "opacity": 0.4},
    {"type": "vignette", "layer": "foreground", "strength": 0.22}
  ],
  "camera_zones": {
    "wide": [0.0, 0.0, 1.0, 1.0],
    "host_close": [0.28, 0.16, 0.36, 0.5],
    "city_detail": [0.3, 0.1, 0.5, 0.5]
  },
  "anchors": {"host": [0.42, 0.9], "guest_a": [0.7, 0.9]},
  "variants": ["night", "rain"]
}
```

> 自定义原语：实现方可在 `pipeline/scene_render.py` 用 `register("railings", fn)` 注册；
> 未注册的 type 渲染时会抛 `V5Error`，校验器在模块就绪时会提示 warning。

---

## 5. 加模板

模板 = 一个节目形态：绑定一个 style、一组 cast、按 act 组织的场景路线（scene/camera/line/beats）。
**完整可运行示例**见 `contract/v5/custom/templates/custom_holo_talk.json`，关键结构：

```json
{
  "id": "custom_rooftop_talk",
  "label": "天台夜话（自定义）",
  "style": "neon_toon",
  "target_duration": 30,
  "cast": [
    {"role": "host", "character": "laozhou"},
    {"role": "guest_a", "character": "custom_announcer"}
  ],
  "acts": [
    {"id": "act01", "duration": 12, "scene": "custom_rooftop", "camera": "push_in",
     "camera_zone": "wide", "line": "host",
     "beats": ["霓虹天台亮起", "host 转身面向镜头"], "characters": ["host"]},
    {"id": "act02", "duration": 10, "scene": "custom_rooftop", "camera": "pan_right",
     "camera_zone": "host_close", "line": "guest_a",
     "beats": ["guest 接过话头"]},
    {"id": "act03", "duration": 8, "scene": "custom_rooftop", "camera": "static",
     "line": ""}
  ],
  "packaging": {"subtitle": "neon_toon", "transition": "neon_wipe", "title": "天台夜话"}
}
```

规则：

- `cast[].role` 自定（`host` / `guest_a` / …，唯一即可）；`character` 必须是已存在角色 id；
- `act.line` 写**cast role** 表示该 act 台词归属；`act.line: ""` = 无台词（B-roll/空镜，配 `broll: true`）；
- `act.scene` / `template.style` 必须存在（校验器会报"不存在于 styles/ / scenes/"）；
- `acts[].duration` 只是**占位提示**：V5 成片时长由 TTS 语音自然时长驱动（`render_v5.tts_and_pace`
  会重算每镜时长），不需要强行对齐 `target_duration`，校验器也不做时长偏差检查；
- `act.text` 是实际台词文本（TTS 用）；`act.characters` 限定本 act 出场角色（缺省=全 cast）。

校验 & 渲染：

```bash
python -m pipeline.v5_cli validate
python -m pipeline.cli render_v5 --template custom_rooftop_talk --duration 30
# 成片：outputs/templates_v5/custom_rooftop_talk/final.mp4
```

---

## 5.1 加道具 / 风格包

道具（`contract/v5/props/`，schema `prop.schema.json`；内建 44 个：历史 20 + 现代 24）：

```json
// contract/v5/custom/props/custom_compass.json
{
  "id": "custom_compass",
  "label": "罗盘（自定义）",
  "kind": "compass",
  "description": "行军指北的铜罗盘",
  "tags": ["history", "war", "prop"],
  "appearance": {"shape": "disc", "main": "#b8a888", "accent": "#b23a2e",
                 "material": "bronze", "size": [0.08, 0.08], "glow": 0.0},
  "style": {"default": {"line": 1.0}, "ink_chronicle": {"line": 1.3, "tint": "#1c1a16"}}
}
```

- 必填只有 `id` / `label`；`appearance`（简单形状/配色/材质/相对尺寸）供程序化绘制；
- `style` 是各风格适配覆盖：键可以是 style id 或风格族，渲染器找不到时用 `default`；
- 被 `scene.props` / `act.props` / `cast.props` / `packs[].props` 引用（id 必须存在）。

风格包（`contract/v5/packs/`，schema `pack.schema.json`）把对象 id 列表打包，便于整体挑选：

```json
// contract/v5/custom/packs/custom_holo_pack.json
{
  "id": "custom_holo_pack",
  "label": "全息宣纸包（自定义）",
  "description": "示例自定义包：1 风格 + 1 场景 + 1 角色 + 1 模板",
  "tags": ["custom", "hologram"],
  "styles": ["custom_holo_paper"],
  "scenes": ["custom_holo_gallery"],
  "characters": ["custom_lin"],
  "props": [],
  "templates": ["custom_holo_talk"]
}
```

内置 2 个包可参考/导出：`pack_history_chronicle`、`pack_modern_studio`。

```bash
python -m pipeline.v5_cli list-props
python -m pipeline.v5_cli list-packs
python -m pipeline.v5_cli export --kind props --id scroll --out tmp/my_props
python -m pipeline.v5_cli export --kind packs --id pack_modern_studio --out tmp/my_packs
```

---

## 6. 覆盖风格（推荐工作流）

```bash
# 1) 导出内置风格成可编辑副本（写到 contract/v5/custom/styles/）
python -m pipeline.v5_cli export --kind styles --id neon_toon
# 2) 编辑 contract/v5/custom/styles/neon_toon.json（只写要改的字段）
# 3) 校验 + 预览（预览需要渲染模块就绪）
python -m pipeline.v5_cli validate
python -m pipeline.v5_cli preview --kind style --id neon_toon --out tmp/neon_preview.png
```

风格里影响"像不像这个风格"的关键字段：`technique`、`palette`、`texture`、`line`、
`character_treatment`、`transition`、`audio`、`h3.style_prompt`。

---

## 7. 字段速查

| kind | 必填（硬） | 高频可选 |
|---|---|---|
| style | `id` `label` `technique` `palette{bg,fg,accent}` | `texture` `line` `character_treatment` `typography` `transition` `audio` `h3` `finish` `packaging` |
| scene | `id` `layers[]`（每条 `type`） | `camera_zones` `anchors` `aspect` `size` `seed` `tags` `variants` `props` |
| character | `id` `label` `role_pool` `voice{provider,instruction}` `appearance{gender,age,skin,hair,hair_style,eye,outfit{type,main,accent}}` | `persona` `poses` `mouth` `style_overrides` `image` `image_alpha` |
| template | `id` `label` `style` `target_duration` `cast[].{role,character}` `acts[].{id,duration,scene,camera,line}` | `packaging` `aspect` `lipsync` `act.camera_zone` `act.text` `act.beats` `act.characters` `act.broll` |
| props | `id` `label` | `kind` `tags` `appearance{shape,main,accent,material,size}` `style{风格适配}` `layer` |
| packs | `id` `label` | `description` `tags` `styles[]` `scenes[]` `characters[]` `props[]` `templates[]` `cover` |

完整说明与示例：[`contract/v5/SCHEMA.md`](../contract/v5/SCHEMA.md)。

---

## 8. CLI 命令

| 命令 | 作用 |
|---|---|
| `python -m pipeline.v5_cli list-styles\|list-scenes\|list-characters\|list-templates\|list-props\|list-packs` | 列表 + 摘要（最后一行 JSON） |
| `python -m pipeline.v5_cli list [--kind styles]` | 一次列出全部/某种 |
| `python -m pipeline.v5_cli validate [--strict] [--root DIR] [--timeline TIMELINE_JSON]` | 校验目录；`--timeline` 附带校验 render_v5 产物；exit 0/1 |
| `python -m pipeline.v5_cli export --kind KIND --id ID [--out DIR]` | 导出内置对象为 custom 副本 |
| `python -m pipeline.v5_cli preview --kind KIND --id ID [--out PNG] [--style STYLE_ID] [--size 1280x720]` | 渲染预览（无渲染模块 → "模块未就绪"，exit 2） |

公共参数 `--root`（默认环境变量 `V5_CONTRACT_ROOT` 或仓库 `contract/v5`）。
stdout 约定：人类可读行在前，**最后一行是 JSON 结果**；失败 exit code != 0。

---

## 9. API（FastAPI，V5 契约路由）

启动：`python -m uvicorn app.main:app --port 8090 --app-dir backend`

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v5/catalog` | 目录摘要（`?kind=styles` 过滤，`?full=true` 返回完整对象） |
| GET | `/api/v5/{kind}/{id}` | 读取对象（内建+custom 合并后的完整值） |
| PUT | `/api/v5/{kind}/{id}` | 保存 custom 覆盖：**先校验，通过才写** `contract/v5/custom/{kind}/{id}.json`；`?dry_run=true` 只校验 |
| POST | `/api/v5/validate` | 全目录校验：body `{"strict": false}` 可选 |
| GET | `/api/v5/preview/{kind}/{id}` | 预览 PNG（`?style=...` 用于 scene/character）；渲染模块未就绪返回 **501** |

kind 支持复数或单数（`styles`/`style`）。PUT 语义：

- 覆盖**内建对象**：body 可以只写要改的字段（partial），落盘片段，加载时深合并；
- 覆盖**纯 custom 对象**（没有内建基线）：自动落盘"已合并的完整对象"，避免下次加载缺字段；
- **新对象**：id 必须 `custom_` 前缀，且 body 必须是完整合法对象；
- 响应 400 时 `detail.errors` 里是校验错误清单（文件/字段级）。

示例（PowerShell 用 `curl.exe` / `Invoke-RestMethod`）：

```bash
curl.exe http://127.0.0.1:8090/api/v5/catalog
curl.exe http://127.0.0.1:8090/api/v5/characters/laozhou
curl.exe -X POST http://127.0.0.1:8090/api/v5/validate -H "Content-Type: application/json" -d "{\"strict\": false}"
curl.exe -X PUT "http://127.0.0.1:8090/api/v5/characters/laozhou?dry_run=true" ^
  -H "Content-Type: application/json" -d "{\"id\":\"laozhou\",\"voice\":{\"instruction\":\"新的音色描述\"}}"
curl.exe -o preview.png http://127.0.0.1:8090/api/v5/preview/style/neon_toon
```

Python（httpx）：

```python
import httpx

base = "http://127.0.0.1:8090"
catalog = httpx.get(f"{base}/api/v5/catalog").json()
print(catalog["counts"])
item = httpx.get(f"{base}/api/v5/characters/laozhou").json()["item"]
print(item["voice"]["instruction"])
resp = httpx.put(f"{base}/api/v5/characters/custom_announcer", json=announcer_dict)
print(resp.status_code, resp.json())
```

---

## 10. 常见错误 & 排查

| 现象 | 原因 | 修复 |
|---|---|---|
| `id: 'x' is a required property` | JSON 里没写 id | 加 `"id"` 且与文件名一致 |
| `xxx 不存在于 characters/` | cast/引用指向不存在的对象 | 先创建被引用对象，或改引用 |
| `custom 新建对象 id 必须以 'custom_' 开头` | 在 custom/ 里新建但没前缀 | 改 id+文件名；覆盖内建则同名 |
| `文件名必须与 id 一致` | 文件名/id 不同 | 统一两者 |
| `image 需要带 alpha 的透明图` | 角色 PNG 无透明通道 | 换透明 PNG，或 `image_alpha: false` |
| `packs/xxx: scenes[]=... 不存在于 scenes/` | 风格包引用了不存在/未落盘的对象 | 先创建对象，或从包数组里删掉 |
| `props/xxx: 'label' is a required property` | 道具缺 `label` | 补 `label`（道具必填 id/label） |
| `scenes/: N 个 layer type 未在 scene_render 注册`（WARN） | 场景用了未注册 primitive | 在 scene_render 注册该 primitive，或改用已注册名 |
| `preview` 返回 501 / CLI 提示"模块未就绪" | `style_v5`/`scene_render`/`characters_v5` 还没实现 | 等渲染模块就绪，或只做契约/校验（不阻塞自定义） |
| `python -m pipeline.v5_cli` 找不到命令 | 不在仓库根目录 / venv 不对 | `cd` 到仓库根；用同一 Python 3.11 环境 |
| 改了 JSON 但没生效 | 数组是整体替换、覆盖片段写错层级 | 对照 `validate` 输出；必要时 `export` 重新导出完整对象改 |
| 想让改动只影响本地实验 | 直接改会覆盖内建 | 设 `V5_CONTRACT_ROOT=<副本目录>` 做隔离 |

---

## 11. 一键验收（自定义部分）

```bash
# 契约：schema + 引用 + custom 示例全绿
python -m pipeline.v5_cli validate
python -m pipeline.v5_cli validate --strict

# 4 类自定义示例都在列表里
python -m pipeline.v5_cli list-templates | Select-String custom_
python -m pipeline.v5_cli list-characters | Select-String custom_
python -m pipeline.v5_cli list-scenes | Select-String custom_
python -m pipeline.v5_cli list-styles | Select-String custom_

# 内建库数量（历史 20 + 现代 24 道具、2 个风格包）
python -m pipeline.v5_cli list-props
python -m pipeline.v5_cli list-packs

# render_v5 产物时间线
python -m pipeline.v5_cli validate --timeline outputs/templates_v5/tonight_show/timeline.json

# API 冒烟（TestClient，不占 GPU、不写仓库）
python tmp/v5_api_smoke.py

# 契约单测
python -m pytest tests/test_v5_catalog.py -q
```
