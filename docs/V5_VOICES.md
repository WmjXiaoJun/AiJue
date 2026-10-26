# V5 角色固定声音（Voice Library + Voice Lock）

> 目标：**每个角色的声音固定**（同一角色在任何模板/任何一集里音色一致），且**可以在角色下修改**。
> 相关：`docs/V5_CHARACTERS.md`、`docs/V5_CUSTOMIZATION.md`、`contract/v5/voices.json`、
> `pipeline/v5_voices.py`、`pipeline/tts_breeze.py`（voice clone）。

## 1. 设计

```
contract/v5/voices.json                 # 声音库：24 个固定 preset（instruction/seed/cfg_scale）
contract/v5/characters/<id>.json        # 角色：voice: {"provider":"breeze","voice_id":"<preset>"}
        │  v5_voices.resolve_voice()
        ▼
角色完整声音 spec（voice_id/instruction/seed/cfg_scale/key）
        │  v5_voices.ensure_voice_lock()
        ▼
固定参考音频 outputs/v5_voices/<character>/<voice_key>.wav + .json
        │  Voice Clone（ref_audio + ref_text）
        ▼
该角色所有台词（跨镜头/跨模板/跨集）同一音色
```

- **声音固定**：角色 JSON 只引用 `voice_id`；instruction/seed 存在声音库 preset 里，修改必须显式改 preset 或角色覆盖。
- **声音锁**：首次为该角色合成一段标准参考音频（"大家好，我是XX。这是我的固定声音…"），
  之后用 Breeze 的 **Voice Clone**（`ref_audio`+`ref_text`）合成所有台词 → 复刻同一音色，避免 Voice Design 的随机漂移。
- **修改在角色下**：改角色 JSON 的 `voice` 块，或 `custom/characters/<id>.json` 覆盖；改完重建锁、重渲即可。
- laozhou / ali / dawei 的 preset **与 V4 语音完全一致**（同 instruction + 同 seed：20261026/27/28），跨版本音色不变。

## 2. 声音库（`contract/v5/voices.json`）

24 个 preset，例如：

| voice_id | 用途 | seed |
|---|---|---|
| `legacy_v4_laozhou` / `legacy_v4_ali` / `legacy_v4_dawei` | 与 V4 完全一致（老周/阿栗/大魏） | 20261026 / 20261027 / 20261028 |
| `strategist_qinglang` | 诸葛亮 | 20263001 |
| `emperor_yazhong` | 曹操 | 20263002 |
| `commander_dichen` | 孙武 | 20263003 |
| `ruler_dark` | 秦始皇 | 20263004 |
| `scholar_wenrun` / `poet_haofang` | 苏轼 / 李白 | 20263005 / 20263006 |
| `empress_weiyi` / `heroine_sashuang` | 武则天 / 花木兰 | 20263007 / 20263008 |
| `warrior_houzhong` / `envoy_jianyi` / `navigator_kai` / `historian_kezhi` | 关羽/张骞/郑和/司马迁 | 20263009–20263012 |
| `bright_female` `young_male` `warm_male` `fast_male` `cool_male` `soft_male` `steady_male` `robot_flat` `mystery_low` | 现代角色 | 20263013–20263021 |

用户可在 `contract/v5/custom/voices.json` 覆盖/新增 preset（同名深合并）：
```json
{"version": 1, "presets": {
  "my_voice": {"label": "自定义男声", "instruction": "35岁男性，偏低，语速慢…",
               "seed": 20269999, "cfg_scale": 4.0}
}}
```

## 3. 在角色下修改声音（三种方式）

### 3.1 直接改角色 JSON（推荐）
`contract/v5/characters/zhuge.json`：
```json
"voice": {
  "provider": "breeze",
  "voice_id": "strategist_qinglang",   // 引用声音库 preset
  "seed": 20263001,                     // 可选：覆盖 preset seed（让音色微调）
  "cfg_scale": 4.0,
  "instruction": "……"                    // 可选：完全覆盖音色描述
}
```
改完执行：
```powershell
python -m pipeline.v5_cli validate --strict
python -m pipeline.v5_cli voice lock --character zhuge --force   # 重建固定参考音频
python scripts/render_templates_v5.py --only history_talk --refresh --force
```

### 3.2 CLI（自动写入 custom 覆盖，不动内建文件）
```powershell
python -m pipeline.v5_cli voice list
python -m pipeline.v5_cli voice show    --character zhuge
python -m pipeline.v5_cli voice preview --character zhuge --text "天下大势，分久必合。"
python -m pipeline.v5_cli voice set     --character zhuge --voice-id emperor_yazhong
python -m pipeline.v5_cli voice set     --character zhuge --seed 20263999
python -m pipeline.v5_cli voice lock    --character zhuge --force
```
`voice set` 写入 `contract/v5/custom/characters/<id>.json`（只含 voice 块，深合并）。

### 3.3 API
```
GET  /api/v5/voices                         # 声音库列表
GET  /api/v5/voice/{character}              # 当前 resolved 声音 + 锁状态
POST /api/v5/voice/{character}/lock         # 生成/重建声音锁（body: {"force": true}）
POST /api/v5/voice/{character}/preview      # 试听（body: {"text": "..."}），返回 wav 信息
```

## 4. 声音锁（`outputs/v5_voices/`）

```
outputs/v5_voices/<character>/<voice_key>.wav    # 固定参考音频（24kHz mono）
outputs/v5_voices/<character>/<voice_key>.json   # key/seed/voice_id/instruction/ref_text/时长
```
- `voice_key = sha1(provider|voice_id|instruction|seed|cfg_scale|ref)[:12]`：改声音参数 → key 改变 → 旧锁自动失效。
- 锁可复用（跨模板/跨集）；`--force` 重建（例如你换了 instruction 但 seed 不变也可重建）。
- 锁缺失时自动生成（渲染/试听时），失败会回退 Voice Design（仍固定 seed+instruction）并告警。

## 4.1 试听样本（24 角色）

- 每个角色的固定声音参考（锁）导出为 48k stereo 试听：outputs/v5_voices/previews/<character>.wav
- 清单（角色/voice_id/seed/key/时长/参考文本）：outputs/v5_voices/previews/manifest.json
- 24 角色连续试听（每段间隔 0.8s，约 164s）：outputs/v5_voices/voice_demo_all.wav
- 生成/刷新：python tmp/v5_all_voice_previews.py（需要 Breeze TTS 服务，GPU 约 4 分钟；已生成的锁会复用）

## 5. 一致性校验

- **QC `voice_fixed`**：同一角色所有对白镜的 `voice_key` 必须一致、对白镜必须有 voice_id；
  `qc_report.json` 的 checks/stats 里记录每角色 voice_key。
- `v5_voices.validate_library()`：校验声音库 preset 完整性与角色 voice_id 引用（CLI `validate` 侧可扩展）。
- timeline / style_report 记录每镜 `voice_id/voice_key/ref`，便于审计。

## 6. 跨版本映射（与 V4 对齐）

| 角色 | V5 voice_id | V4 音色 | 说明 |
|---|---|---|---|
| 老周 laozhou | `legacy_v4_laozhou` | 同 instruction/seed 20261026 | 完全一致 |
| 阿栗 ali | `legacy_v4_ali` | seed 20261027 | 完全一致 |
| 大魏 dawei | `legacy_v4_dawei` | seed 20261028 | 完全一致 |

## 7. 常见问题

| 现象 | 处理 |
|---|---|
| 改了 seed 但成片音色没变 | 渲染要重建配音：`voice lock --force` 后 `render_v5 --force`（或删 `work/voice/voice_cache.json` + `work/audio`） |
| 角色声音突然变了 | 检查是否有人改了 `voices.json` preset 或角色 `voice`；`voice show` 对比 `voice_key` |
| Voice Clone 报 `ref_audio/ref_text must be provided together` | 锁缺失/损坏；`voice lock --force` 重建 |
| 想用自己录的参考音频 | 角色 voice 里 `{"ref_audio": "path/to.wav", "ref_text": "参考文本"}`，或 `voice set --ref-audio ... --ref-text ...` |
