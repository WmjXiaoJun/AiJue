# contract/v5/custom —— 用户自定义目录

这里放**用户覆盖**，优先级高于 `contract/v5/` 内建对象。两种用法：

## 1. 覆盖内建对象（同名，可只写要改的字段）

```
custom/characters/laozhou.json     # 只改 voice，其余继承内建
custom/styles/neon_toon.json       # 只改 palette.accent，其余继承内建
```

规则：**深合并**——dict 递归合并、**数组/标量整体替换**。所以改数组时要写完整新数组：

```json
// custom/styles/neon_toon.json —— 数组替换示例
{"id": "neon_toon", "palette": {"accent": ["#ff2fd6", "#33e6ff", "#ffb347", "#8ef7ff"]}}
```

导出可编辑副本（会写到这里）：

```bash
python -m pipeline.v5_cli export --kind styles --id neon_toon
python -m pipeline.v5_cli export --kind characters --id laozhou
python -m pipeline.v5_cli export --kind templates --id history_talk
```

## 2. 新增自定义对象（id 必须 `custom_` 前缀）

本目录自带 3 个可运行的示例（互相引用；角色用内建 laozhou）：

| 文件 | 内容 |
|---|---|
| `custom/styles/custom_holo_paper.json` | 全息宣纸风格（ink_wash + hologram + rice_paper） |
| `custom/scenes/custom_holo_gallery.json` | 全息画廊场景（layers + camera_zones + anchors） |
| `custom/templates/custom_holo_talk.json` | 30s 模板：custom_holo_paper + laozhou + custom_holo_gallery |

校验：

```bash
python -m pipeline.v5_cli validate                        # 人可读 + 最后一行 JSON
python -m pipeline.v5_cli validate --strict               # CI 用（warning 也算失败）
python -m pipeline.v5_cli list-templates                  # 应能看到 custom_holo_talk
```

## 注意事项

- 文件名必须与 `id` 完全一致；
- 覆盖片段可以省略 `id`? **不行**：请显式写 `id`，校验器会检查文件名与 id 一致；
- 新对象必须满足完整 schema（必填字段不可省），不能只写片段；
- 图片资源写相对仓库根或 `contract/v5/` 的路径（如 `assets/characters/my_char.png`）；
- 推送到仓库前请跑 `validate`；如果只想本地试，可设 `V5_CONTRACT_ROOT` 指向自己的副本目录；
- 本目录示例建议保留（3 类示例），新增自定义请另起文件名。

更多教程见 `docs/V5_CUSTOMIZATION.md`。
