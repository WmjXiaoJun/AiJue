# contract/v5 —— V5「可变风格世界」数据契约

V5 把「风格 / 场景 / 角色 / 道具 / 模板」全部变成 JSON 契约，渲染器（`pipeline/style_v5.py`、
`scene_render.py`、`characters_v5.py`、`motion2d.py`）只读契约、不再硬编码节目形态。
用户可以在 `custom/` 下覆盖任意对象，或新增 `custom_` 前缀的自定义对象。

> 字段说明表：见 [`SCHEMA.md`](SCHEMA.md)；用户教程：`docs/V5_CUSTOMIZATION.md`；
> 模块接口：`docs/V5_INTERFACES.md`；总规划：`docs/PLAN_V5_STYLE_AUDIO.md`。

## 目录

```
contract/v5/
  styles/{style_id}.json        # 视觉风格（技法/调色/纹理/线条/角色处理/包装/转场/音效/H3 prompt）
  scenes/{scene_id}.json        # 场景（layers[] 绘制指令 + camera_zones + anchors + variants）
  characters/{char_id}.json     # 角色（人设/声音/外观/姿态/口型/风格覆盖/可选 PNG 立绘）
  props/{prop_id}.json          # 道具（横幅/卷轴/文物/奖杯/手机/数据板…）
  templates/{template_id}.json  # V5 模板（style + cast + acts 场景路线 + packaging）
  packs/{pack_id}.json          # 风格包（风格+场景+角色+道具+模板打包引用）
  schema/*.schema.json          # JSON Schema（Draft 2020-12，含 timeline.schema.json）
  custom/                       # 用户覆盖（优先级最高；同名深合并，数组替换）
  SCHEMA.md  README.md
```

## 优先级与合并规则

1. `custom/{kind}/{id}.json` **优先于** `{kind}/{id}.json`；
2. 同名即覆盖：**深合并**（dict 递归合并；list / 标量整体替换）——因此覆盖片段可以只写要改的字段：
   ```json
   {"id": "laozhou", "voice": {"instruction": "低沉沙哑的老牌主持人音色"}}
   ```
3. 指定 `V5_CONTRACT_ROOT` 环境变量可以切换整个契约根目录（测试 / 多套自定义包）；
4. `load_catalog()` 永不因单个坏文件崩溃：解析错误收进 `catalog["errors"]`，由校验器报出。

## 校验（CI 门）

```bash
python -m pipeline.v5_cli validate            # 人类可读 + 最后一行 JSON；exit 0/1
python -m pipeline.v5_cli validate --strict   # warning 也视为失败（CI 用）
```

校验内容：JSON Schema、引用完整性（template.style→styles、cast→characters、acts[].scene→scenes、
props→props、style_overrides→styles、packs 各 id 列表）、custom id/文件命名规则、资源存在性
（角色 PNG 立绘、场景背景图、PNG alpha）。渲染模块就绪时，还会对照 `scene_render` 的 primitive
注册表检查 `scenes[].layers[].type`（聚合成 1 条 warning，不刷屏）。

render_v5 产物时间线也可顺手校验（`timeline.schema.json` + 轨道/片段语义）：

```bash
python -m pipeline.v5_cli validate --timeline outputs/templates_v5/tonight_show/timeline.json
```

> 注：V5 成片时长由 TTS 语音自然时长驱动（`render_v5.tts_and_pace` 重算每镜时长），
> 模板 `acts[].duration` / `target_duration` 只是占位提示，不做时长对齐校验。

## 浏览 / 导出自定义副本

```bash
python -m pipeline.v5_cli list-styles
python -m pipeline.v5_cli list-scenes --root contract/v5
python -m pipeline.v5_cli list-characters
python -m pipeline.v5_cli list-templates
python -m pipeline.v5_cli list-props
python -m pipeline.v5_cli list-packs
python -m pipeline.v5_cli export --kind styles --id neon_toon        # 导出到 custom/styles/
python -m pipeline.v5_cli export --kind characters --id laozhou --out tmp/my_custom
python -m pipeline.v5_cli preview --kind style --id neon_toon         # 渲染模块未就绪时提示
```

## API（FastAPI，backend/app/routers/v5_catalog.py）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v5/catalog` | 目录摘要（可按 `?kind=styles` 过滤；`?full=true` 返回完整对象） |
| GET | `/api/v5/{kind}/{id}` | 读取对象（内建 + custom 合并后的完整值） |
| PUT | `/api/v5/{kind}/{id}` | 保存 custom 覆盖（先校验；有内建基线写片段、纯 custom 写完整对象；`?dry_run=true` 只校验） |
| POST | `/api/v5/validate` | 全目录校验（body 可带 `{"strict": true}`） |
| GET | `/api/v5/preview/{kind}/{id}` | 预览 PNG（style/scene/character/template；渲染模块未就绪 → 501） |

kind 支持复数或单数（`styles`/`style`）。

## 约定

- id：`^[a-z][a-z0-9_]{1,60}$`，文件名必须与 id 一致；用户新建对象用 `custom_` 前缀；
- 颜色：`#RGB` / `#RRGGBB` / `#RRGGBBAA`；场景 layers[].color 也可写 palette token（`accent:1` / `shadow` / `glass`）；
- 数组语义是替换：覆盖 `cast` / `layers` / `palette.bg` 等数组时请写**完整新数组**；
- 不写 GPU 依赖、不在契约里放生成产物；图片资源放仓库已有目录并在字段里写相对路径。
