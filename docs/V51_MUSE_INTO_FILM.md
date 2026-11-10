# V51 · Muse 真正进片 + 季批量口型 + 1080P 交付

> 背景：V50 把 MuseTalk 接成了「可选阶段」，但成片默认没被改动。本轮把它做成**能进片**，并把季/交付补齐。
> 基线：`master`（未提交）｜ 2026-11-05

---

## 1. 阶段参数可覆盖（不改合同）

```powershell
# CLI
python -m pipeline.agents run --template custom_tpl_v46_cyber --stages muse '
  --stage-params '{\"muse\":{\"apply\":true}}'
# API
POST /api/v6/agent/run { template, stages:["muse"], stage_params: { muse: { apply: true } } }
```

实现：`run_template(..., stage_params=...)` 在调用工具前与合同里的 `params` 合并（合同不变、run 内生效）。
PowerShell 传 JSON 要给内层引号加反斜杠（`\"`），否则被命令行吃掉。

---

## 2. Muse 进片（apply）实测

`custom_tpl_v46_cyber`（2 个对白镜）：

| 项 | 结果 |
|---|---|
| 口型 | `v5_002` / `v5_003` 各 ~20s，同步分 0.21 / 0.16（lag −1） |
| 回写 | `shots/v5_00{2,3}.mp4` 被替换，原件备份 `shots/_orig/` |
| 重合成 | `templates3d.assemble` → `final.mp4` 时长 **14.142s**（目标 14.12） |
| sha 变化 | `7cfbd90eaef0` → **`5d58619d395a`**（确实进片了，不是空跑） |
| QC | `final_exists / poster / duration / audio / resolution` 全绿 |

---

## 3. 季批量口型（3 集一次跑完）

```powershell
POST /api/v6/agent/season/undercity_arc/run '
  { "with_muse": true, "muse_apply": true, "dry_run": false }
```

- 结果：3 集全部 `ok`；每集各自 `muse → apply → reassemble`；
- 新成片 sha：undercity `ccdaa2d7` / robot `9e0543df` / duologue `250cbf4f`（三集都变了）；
- 每集 `shots/_orig/` 都有原件；QC 全绿（14.142s / 13.746s / 19.120s）。

> ⚠️ `season/run` 的 `dry_run` **默认 true**（安全），真跑必须显式 `"dry_run": false`。

---

## 4. 修掉一个真 bug：合集导出读到旧片

现象：Muse 进片后重新 `export?force=true`，sha 仍是 `6da529dfda`（没变）。

原因：短片在两个目录各有一份 `final.mp4`（`templates_2d` 是 V45 的旧产物、`templates_3d` 是本次 Muse 后的），
导出固定按「先 2D 后 3D」取，永远拿到旧的那份。

修法：`_newest()` —— 同一模板多份成片时按 **mtime 取最新**；`/api/v5/series`、导出、`/api/v6/agent/*` 的 ready/final_url 全部统一这个口径。

修后实测：

| 变体 | 分辨率 | sha1 |
|---|---|---|
| 横版 | 1280×720 / 52.8s | `356dae3b75` |
| 1080P | 1920×1080 | `34bc09e472` |
| 竖版 | 1080×1920 | `e0fa5f0c71` |

（manifest 里 `episodes[].sha1` = `ccdaa2 / 9e0543 / 250cbf`，与磁盘一致。）

---

## 5. 单集 1080P / 竖版交付

```powershell
python -m pipeline.deliver --template custom_tpl_v46_cyber --hd
python -m pipeline.deliver --template custom_tpl_v46_cyber --vertical
python -m pipeline.deliver --all --hd          # 全部有 final 的模板
```

- 产物：`outputs/templates_{2d,3d}/<tid>/final_1080p.mp4`（1920×1080）与 `final_9x16.mp4`（1080×1920 中心裁切），**不覆盖原片**；
- 实测：cyber → 1080p 9.17MB / 竖版 8.41MB，2.5s 转码；
- 幂等：已存在直接 `cached`，`--force` 才重转。

---

## 6. 状态表

| 能力 | 状态 | 证据 |
|---|---|---|
| Muse 口型生成 | ✅ | 20s/3s 镜；同步分 0.16–0.21 |
| Muse 进片（apply） | ✅ | cyber final sha 变化 + QC 全绿 |
| 季批量口型 | ✅ | 3 集一次 ok，三集 sha 全变 |
| 合集导出取最新 | ✅ | 修 bug 后 sha 三变体各自不同 |
| 单集 1080P / 竖版 | ✅ | `pipeline/deliver.py` |
| SyncNet LSE 分 | ⛔ | 权重损坏（886MB zip 不完整） |
| Replicate 真跑 | ⏳ | 等 `REPLICATE_API_TOKEN`（适配器+mock 已验证） |
| 2D/3D 目录镜像 | ⚠️ 已知债 | 同一短片两份成片，靠 newest-wins 兜底 |
