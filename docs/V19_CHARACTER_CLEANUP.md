# V19 角色清理（纸片人角色删除 + 写实模板 cast 重映射）

> 用户反馈（2026-11-02）：角色页里有 8 个「灰纸片人」占位角色（无概念图），要求全部删除。本次为纯数据层清理：不改前端/后端/pipeline、不改 outputs 成片、不触发 GPU、不 git。

## 1. 删除清单（15 个文件 / 8 个角色）

| 角色 id | 名字 | 删除文件 |
|---|---|---|
| `custom_lin` | 林同（示例） | `contract\v5\custom\characters\custom_lin.json` |
| `real_host` | 沈舟 | `contract\v5\characters\real_host.json`<br>`contract\v5\custom\characters\real_host.json` |
| `real_street_runner` | 陈野 | `contract\v5\characters\real_street_runner.json`<br>`contract\v5\custom\characters\real_street_runner.json` |
| `real_street_owner` | 李静 | `contract\v5\characters\real_street_owner.json`<br>`contract\v5\custom\characters\real_street_owner.json` |
| `real_cafe_owner` | 周岚 | `contract\v5\characters\real_cafe_owner.json`<br>`contract\v5\custom\characters\real_cafe_owner.json` |
| `real_cafe_regular` | 高远 | `contract\v5\characters\real_cafe_regular.json`<br>`contract\v5\custom\characters\real_cafe_regular.json` |
| `real_kitchen_chef` | 老赵 | `contract\v5\characters\real_kitchen_chef.json`<br>`contract\v5\custom\characters\real_kitchen_chef.json` |
| `real_kitchen_regular` | 阿凯 | `contract\v5\characters\real_kitchen_regular.json`<br>`contract\v5\custom\characters\real_kitchen_regular.json` |

这 8 个角色都只有文字 persona、没有概念图，角色页渲染为灰色纸片人；删除后角色页从 32 卡降到 **24 卡**（均有概念图/正式音色绑定）。

## 2. 3 个写实模板 cast 重映射

| 模板 | role | 旧角色 | 新角色 |
|---|---|---|---|
| `real_street` | host | `real_host`（沈舟） | `laozhou`（老周） |
| `real_street` | figure_a | `real_street_runner`（陈野） | `xueba`（学霸） |
| `real_street` | figure_b | `real_street_owner`（李静） | `tianmei`（甜妹） |
| `real_cafe` | host | `real_host`（沈舟） | `laozhou`（老周） |
| `real_cafe` | figure_a | `real_cafe_owner`（周岚） | `tianmei`（甜妹） |
| `real_cafe` | figure_b | `real_cafe_regular`（高远） | `dawei`（大魏） |
| `real_kitchen` | host | `real_host`（沈舟） | `laozhou`（老周） |
| `real_kitchen` | figure_a | `real_kitchen_regular`（阿凯） | `pangzi`（胖子） |
| `real_kitchen` | figure_b | `real_kitchen_chef`（老赵） | `xueba`（学霸） |

同时把示例模板 `custom_holo_talk` 的 cast 从 `custom_lin` 改写为 `laozhou`（保持 22 个模板全部可解析），并同步更新 `contract/v5/custom/README.md` 的示例说明。

## 3. voice_casting.json

删除 8 个角色的 `map` / `characters` 条目后：`map` = **24** 人，`characters` 别名 = **24** 人，两者一致；每个 role 的 preset 绑定不变。

## 4. 校验

```powershell
python tmp/v19/character_cleanup_check.py
```

实际输出（2026-11-02，存证 `tmp/v19/character_cleanup_check_output.txt`）：

```text
[1] load_catalog: errors=0  characters=24 (期望 0 / 24)  templates=22
    characters/ 文件 24 个；custom/characters 覆盖 24 个
[2] contract/ 已删 id 引用: 0 处（期望 0）
[3] 模板 cast: 22 个模板，悬空 0 处（期望 0）
    19 个 3D 模板: 缺失 0 个 []；cast 悬空 0 个 []
    real_street: [('host', 'laozhou'), ('figure_a', 'xueba'), ('figure_b', 'tianmei')]
    real_cafe: [('host', 'laozhou'), ('figure_a', 'tianmei'), ('figure_b', 'dawei')]
    real_kitchen: [('host', 'laozhou'), ('figure_a', 'pangzi'), ('figure_b', 'xueba')]
[4] voice_casting: map=24 characters=24 equal=True presets=24
[5] GET /api/v5/catalog: characters=24，已删残留 []
    GET /api/v5/casting?template=real_street: [('host','laozhou'), ('figure_a','xueba'), ('figure_b','tianmei')]
        voices: laozhou→legacy_v4_laozhou，xueba→young_male，tianmei→bright_female
    GET /api/v5/casting?template=real_cafe: [('host','laozhou'), ('figure_a','tianmei'), ('figure_b','dawei')]
    GET /api/v5/casting?template=real_kitchen: [('host','laozhou'), ('figure_a','pangzi'), ('figure_b','xueba')]
    GET /api/v5/voice/real_host: HTTP 404；GET /api/v5/voice/custom_lin: HTTP 404
    GET /api/v5/voices: 24 个 preset（期望 24）
PASS：load_catalog errors 0；角色 24；contract 无已删 id 引用；22 模板 cast 全部可解析
      （含 19 个 3D 模板）；voice_casting 24 条且 map=别名；后端 3 个写实模板均为新 cast。
```

## 5. 影响说明

1. **3 支写实成片已生成**（`outputs/templates_3d/real_street|real_cafe|real_kitchen/final.mp4`）不受影响；它们是用旧角色外观渲染的，本次只改数据，不重渲染。
2. **重生成时**会按模板新 cast（老周/学霸/甜妹/胖子/大魏）重新生成镜头与外观，属预期变化。
3. `outputs/templates_v5/real_*/work/episode.json` 的 `meta.cast` 仍指旧角色 id（本任务不改 outputs）；若要直接重跑 3D 生成（`templates3d_new_templates.py` 读 episode），需先按新模板 cast 重建 episode 数据或更新 semantic brief，否则角色描述会退化为通用人物。
4. 删除角色的试听 wav/声音锁（`outputs/v5_voices/`、`tmp/v5_voice/`）未清理，不影响目录/页面；如要彻底清理可手动删除对应目录。
5. 角色页、casting、voices 均从 catalog 实时读取，后端无需重启即可看到 24 卡与新 cast。

## 6. 遗留

1. `outputs/templates_v5/real_*/work/episode.json` 仍有旧 cast（未改 outputs，见影响说明 3）。
2. `docs/V18_VOICE_CASTING.md` 的 32 角色表已过时（顶部已加 V19 提示，保留历史存档）。
3. 删除的 8 个角色的历史音色/试听缓存仍在 outputs/tmp（不影响功能）。
4. 后续若新增角色，需要同步 voice_casting.json 与 `contract/v5/characters/`，并跑本校验脚本。

