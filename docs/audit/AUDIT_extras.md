# 周边包与脚本审查（integrations / imagegen / studio2d / publish / localize / scripts）

- 审查人：audit-extras（只读；未改任何源码/合同）
- 时间：2026-11-06
- 范围与体量：`integrations/`(10/1459)、`imagegen/`(8/1518)、`studio2d/`(7/1838)、`publish/`(3/472)、`localize/`(4/238)、`scripts/`(45 个 .py + 5 个 .ps1)
- 方法：真读代码 + 纯函数/子进程探针；**未**跑渲染、未发外网请求、未跑任何会改合同/删文件的脚本
- 探针产物：`docs/audit/_tools/scan_scripts_danger.py`（45 脚本静态危险面扫描）、`docs/audit/_tools/probe_child_stderr_encoding.py`（子进程编码实测）、`tmp/audit3/`（只读取证）

> 与第二轮的关系：`docs/audit/REVIEW_PIPELINE_ROUND2.md` 的范围 A 与本轮完全重叠，但其"范围 A 未见问题"一节对 scripts/ 只报了 2 条（R9/R10），且 **R9/R10 现已在工作树修好**（`git diff` 可见）。本轮做的是深度遍：下面 21 条没有一条与 R1–R12 重复，其中 5 条（P0-1、P1-3、P1-5、P1-7、P2-11）直接推翻 R2 的"范围 A 未见问题"结论——R2 称 `upload_post.py` 与 `fix_loudness.py` 扎实，只审了重试/接口层，没审字段语义与备份时序。

---

## 概览

| 结论 | 数量 | 说明 |
|---|---|---|
| P0 | 1 | `rebuild_all_looks_5min.py` 删除并改写生产镜头目录 `outputs/templates_3d/<tid>/shots` |
| P1 | 8 | 重复 shot id（已污染 13/30 合同；同节另附 lead 实证的 **episode↔brief 两套 id 空间分裂**，该条已记入总报告 P0、本报告不重复计账）、生产库直改无守卫、备份自身失效、渲染失败仍合成、上传可见性硬编码、云端任务证据丢失、失败覆盖 meta、合同批量改无 dry-run |
| P2 | 12 | 子进程编码、死参数/假能力、伪造元数据、硬编码绝对路径、模块级执行、备份缺失等 |
| 已核对无问题 | 18 项 | 见末节（含对本轮 P0/P1/P2 的反证） |

**三个系统性问题**（不是单点 bug）：

1. **`scripts/` 是不可回滚的"手炮"集合**：45 个 .py 里 **36 个会写盘或删文件**，只有 **6 个**带 `--dry-run`（`expand_3d_shots`/`gen_v5_scene_art`/`migrate_v50_muse_stage`/`rebuild_templates3d_progress`/`repair_v5_char_art_v49`/`verify_v45_2d_flow`），**16 个仍未被 git 跟踪**（`git status` 全是 `??`，无历史可回滚）；`seed_v6_workflows.py --force`、`migrate_v50_musev_stage.py`、`gen_agent_workflows.py` 三条会**无备份覆盖 33 份工作流合同**。
2. **"产物即证据"链在多处是假的**：`imagegen` 失败也写 meta、`rebuild_meta.py` 硬编码 steps/cfg/seed、`run_h3_ref.py` 把入参 duration 当实测 duration 上报、`run_episode.py` 不看渲染退出码。
3. **跨进程契约只锁了一半**：id/seed/返回码检查都在，但 `shot id` 的**唯一性**没有任何一方校验（导致 P1-1 的静默重复渲染），且 stderr 编码在"子进程 import 期崩溃"时退化成 `???`（P2-1，已实测）。

---

## P0

### P0-1 · `scripts/rebuild_all_looks_5min.py:69-82` 把生产镜头目录删掉再改名，无备份、无 dry-run

```python
69  live = OUT3D / tid / "shots"              # ← 全栈共用的生产镜头目录
70  target = _shots_dir(tid, look)             # shots_<look>
71  if target.is_dir():
72      shutil.rmtree(target, ignore_errors=True)
73  if live.is_dir():
74      shutil.rmtree(live, ignore_errors=True) # ← 删除已交付镜头
75  code = _render(tid, look, log)              # 里面永远追加 --force
76  shots = sorted(live.glob("*.mp4")) if live.is_dir() else []
77  if not shots:
78      return {"template": tid, "look": look, "ok": False, "stage": "render", ...}  # ← 不恢复
...
82  live.rename(target)                         # ← 再把 shots_<look> 改名成 shots
```

**问题**：`shots/` 是**唯一**的规范镜头目录——`pipeline/templates3d_hq.py:916`、`scripts/run_episode.py:31`、`scripts/rebuild_all_3d_5min.py:58`、`unique_fill.compose(shots_subdir="shots")` 全部按这个名字取镜头。本脚本先 `rmtree` 它，再把自己这一路 look 的产物 `rename` 成它。

**证据**：
- 同一脚本 `_shots_dir()`（第 36 行）自己就用 `shots_{look}`，说明作者知道"每 look 应独立"，却仍删共享目录；
- `_render()` 第 59 行无条件 `cmd.append("--force")` → 每个模板每种 look 全量重渲；
- 第 77-79 行失败分支直接 return，`live` 此时已被删且可能只有半个镜头，**原镜头不可恢复**；
- 无 `--dry-run`、无 `--only` 之外的护栏、无备份（全仓 `grep rmtree` 仅 `verify_v45_2d_flow.py` 是限定 FID 的测试目录）。

**影响**：跑一次就不可逆地毁掉 33 个模板的交付镜头；且跑完 `looks=2d_anim` 后，`shots/` 里躺着 2D 镜头，而栈默认认为它是 3D 渲染结果 → 后续 `--reuse-shots` 会把 2D 镜头当 3D 复用，**错误静默传播**。附带：`_write_episode()` 写同一份 `work/episode.json`，最后跑的 look 覆盖前者。

**建议修法**：
```python
# 1) 绝不碰 live：只在 shots_<look> 内工作，合成时显式传 shots_subdir
# 2) 若确需改 live：先 live.rename(f"shots_bak_{ts}")，成功后再删 bak
# 3) --force 改为透传参数，默认不加
# 4) 加 --dry-run，打印"将删除 X / 将重渲 Y"，退出 0
# 5) episode 按 look 分文件：work/episode_<look>.json
```

---

## P1

### P1-1 · `scripts/expand_3d_shots.py:67` 扩写生成的 shot id 每次都从 `v5_e001` 重来 → 重复 id（**已污染 13/30 合同**）

```python
62  while len(shots) < target and idx < 200:
...
66      clone = dict(src)
67      clone["id"] = f"v5_e{added + 1:03d}"   # added 每次调用都从 0 开始
...
88  backup = path.with_suffix(".json.bak")
89  if not backup.is_file():                   # 备份只建一次，之后再改就不备份了
90      shutil.copy2(path, backup)
91  data["shots"] = shots
92  path.write_text(json.dumps(data, ...))
```

**问题**：只要对同一份 brief 调用第二次（"再加 5 个镜"这种最自然的用法），新镜 id 必然与上一次重复；脚本不查重、不校验唯一性。

**证据（实测，当前工作树）**：30 份 brief 中 **13 份已有重复 id**，最恶劣的是 9 个镜头同 id：
```
contract/v5/cinema3d/templates3d/roundtable_semantic.json   shots=71  v5_e*=61  dup=9  →  v5_e001 ×9
tonight_show 9 / history_mystery 9 / shark_tank 7 / snl_sketch 7 / custom_tpl_v46_duologue 4 / …
HEAD 版本：roundtable shots=10 dup=0   （即污染是工作树里的运行造成的，未见提交）
```
`git status` 显示 30 份 brief 全部 ` M`、30 个 `.json.bak` 未跟踪。

**影响（分三层，前两层我已核实，第三层由 lead 实证）**：

1. **渲染层——白烧 GPU + 互相覆盖**（`pipeline/templates3d_hq.py`）：
   - `:919/931/980` `out_shots / f"{shot['id']}.mp4"` → 同 id 的 9 条 brief 写**同一个文件**，最后只留 1 份 clip，另外 8 次 GPU 白烧；
   - `:937` `seed = 20261102 + crc32(tid + shot["id"])` → 同 id 同 seed，即使文件不覆盖也画不出不同内容；
   - `:734/804/821` 剪辑/首帧/prompt 文件名 `{tid}_{shot['id']}_*.mp4|png|txt`，`>200KB` 直接命中缓存 → 后续重跑连渲都不渲；
   - `_identity_keyframe` 的 `{shot_id}.png` / `{shot_id}.meta.json` 同样互相覆盖。
2. **统计层**：QC 的 unique-shot、字幕时间轴都按 id 索引 → "71 镜"是虚假产能。
3. **成片层——比重复 id 更严重：episode 与 brief 是两套 id 空间**（详见下面"更重的一条"）。

**更重的一条（同一节，`P0` 由 lead 记入总报告，不在本报告重复计账）**：
assemble **不读 brief**，它读的是 `outputs/templates_v5/<tid>/work/episode.json`，而该文件的 id 空间与 `contract/v5/cinema3d/templates3d/<tid>_semantic.json` 已经分裂。lead 实测（`tmp/audit_dup_impact.py`）：

| 模板 | episode | brief | 交叉缺失 |
|---|---|---|---|
| `tonight_show` | 78 镜 / 唯一 id 78 / 无重复 | 76 镜 | brief 的 **65 个 id 在 episode 里不存在**；episode 的 **67 个 id（`fill_000…`）在 brief 里不存在** |
| `custom_tpl_v46_duologue` | 6 镜 / 19.14s | 93 镜 | **87 镜渲染完永不入片** |

后果：① `tonight_show` 78 镜里 67 镜根本不是 brief 里那批镜头（是 filler），真正按 brief 渲染的镜头大面积用不上；② `duologue` 这类"brief 93 镜、episode 6 镜"的模板，扩写出来的镜全是**纯浪费的 GPU 成本**；③ 该分裂在 assemble 处表现为**硬失败**（lead 已实证 `RuntimeError`），不是质量问题而是构建问题。

所以本节的定性是：**重复 id 是 P1（白烧 + 覆盖 + 证据失真）；真正该先修的是 episode↔brief 的 id 对齐**（谁负责生成 episode 就对谁提 P0），修完重复 id 才有意义——否则把 id 去重了，镜头照样进不了片。

**建议修法**：
```python
existing = {str(s.get("id")) for s in shots}
n = 1
while ...:
    while f"v5_e{n:03d}" in existing: n += 1
    clone["id"] = f"v5_e{n:03d}"; existing.add(clone["id"]); n += 1
# 写盘前 assert len({s["id"] for s in shots}) == len(shots)
# 备份改为每次带时间戳：path.with_name(f"{path.name}.{ts}.bak")
```
并给 `check_seasons.py`/新增自检加两条硬门：① brief shot id 唯一；② `episode.json` 的 shot id 集合 ⊆ brief 的 shot id 集合（或显式标注 filler），不等就 `exit 1`。


### P1-2 · `scripts/clear_stale_render_urls.py` 无 `__main__` 守卫、无 dry-run，`import` 即改生产库

```python
1  import sys, pathlib
2  sys.path.insert(0, "backend")          # cwd 相对 → 换目录跑就 ImportError 或导入别的 app
3  from app.db import SessionLocal
6  db = SessionLocal()                    # ← 模块级
8  try:
9      for ep in db.query(models.Episode).all():
15         if not path.is_file():
16             ep.render_url = None
17             if str(ep.status or "") == "ready":
18                 ep.status = "pending"    # 已就绪的集被打回 pending
20     db.commit()
```

**问题**：① 全文件没有 `if __name__ == "__main__"`——任何 `import clear_stale_render_urls`（含 pytest 收集、编辑器插件、`python -m` 之外的复用）都会立刻扫描并 commit 生产 DB；② 无 `--dry-run`、无 `--root`、无备份、无计数上限；③ `sys.path.insert(0, "backend")` 依赖 cwd；④ 判定"陈旧"用的是 `OUTPUTS_DIR / rel`，对**任何非 `/media/` 开头的 `render_url`**（例如外部 URL、`http://...`）都会算成"文件不存在"→ 直接清空。

**证据**：`db.commit()` 在模块级（第 20 行），退出码恒为 0（`print` 后自然结束），失败也只 `finally: db.close()`。

**影响**：一次误 import 就把所有"ready"集打回"pending"、把外部/相对 URL 抹掉；无法回滚（SQLite 无备份、无事务边界提示）。

**建议修法**：包成 `def main(argv)` + `--dry-run`（默认 dry-run，`--apply` 才写）、`--root` 显式传、`Path(__file__).resolve().parents[1] / "backend"`、只处理 `url.startswith("/media/")`、先 `db.rollback()` 逃生口 + 写出待改清单 JSON。

### P1-3 · `scripts/fix_loudness.py:183-185` 仅音频路径"先就地改写、后备份"→ 备份是修复后的文件

```python
108 def audio_only_fix(final: Path, max_rounds: int = 3) -> dict:
110     before = probe_loudness(final)
111     info = render.enforce_loudness(final, max_rounds=max_rounds)   # ← 就地改写 final
...
182 if not method:
183     res = audio_only_fix(final, max_rounds=max_rounds)              # final 已被覆盖
184     method = "audio_only"
185     backup = _backup(final) if res.get("ok") else ""               # ← 备份=修复后的文件
```

`_backup`（66-70）是 `copy2(final, final.stem + ".preloud.mp4")`；而 `pipeline/render.py:805-813` 的 `enforce_loudness` 是 `tmp.replace(out)`，**就地在 final 上改**。

**问题**：`--audio-only` 路径（以及 rebuild 失败后的兜底路径）产出的 `.preloud.mp4` 不是原始成片，而是修复后的副本；JSON 报告里 `"backup": "<path>"` 会让操作者以为可回滚。

**影响**：响度修坏（例如旧成片被多次 loudnorm 反复压缩）时**无原件可退**；这正是"声称有备份但备份无效"的典型。对比：rebuild 路径（171 行）的备份顺序是对的——两条路径行为不一致。

**建议修法**：`backup = _backup(final)` 提到 `audio_only_fix()` 调用之前（与 171 行一致），并让 `audio_only_fix` 只在"已备份"断言后运行；报告里区分 `backup`/`backup_valid`。

### P1-4 · `scripts/run_episode.py:76-92` 不看渲染退出码，且强制 `--force`、覆盖共享 `episode.json`

```python
76  cmd = [sys.executable, "-m", "pipeline.templates3d_hq", ... "--identity", "keyframe", "--force"]
84      code = subprocess.call(cmd, ...)
86  print(f"[render] exit={code} shots={len(shots)}")     # ← 只看不改
89  res = unique_fill.compose(tid, template, target, force=True, shots_subdir="shots", ...)
91  return {"ok": bool(res.get("ok")), ...}
```
外加 `72-73`：
```python
72  utils.write_json(Path(paths["workdir"]) / "episode.json", plan)
73  utils.write_json(OUT3D / tid / "work" / "episode.json", plan)
```
以及 `main()` 第 103 行 `return 0`（失败也返回 0）。

**问题**：`templates3d_hq` 退出码非 0（半途崩在某个镜头）时脚本继续 `compose`，用"已存在的旧镜头 + 缺镜"合成一版成片，并把 `ok=bool(res.ok)` 报成成功——`res.ok` 只代表"合成出了文件"。`--force` 写死，无法增量。`episode.json` 被无条件覆盖两次（其中一份在 `outputs/templates_3d/<tid>/work/`，是别处会读的证据），没有备份。

**影响**：单集渲染失败会产出"看起来成功"的成片（缺镜/旧镜），并污染该模板的 episode 证据；`scripts/rebuild_all_3d_5min.py` 里甚至有明文注释承认这套玩法（`# 镜头已出就算成功`）。

**建议修法**：`code != 0` 时 `raise`（或至少 `ok=False` 且不 compose）；`--force` 改为 `args.force` 透传；`episode.json` 写 `.bak` 或带 run 戳的分文件；`main` 返回 `0 if ok else 1`。

### P1-5 · `publish/upload_post.py:224` `privacy_level` 硬编码 `PUBLIC_TO_EVERYONE`，`--privacy unlisted` 只对 YouTube 生效

```python
219  has_youtube = any(platform.startswith("youtube") for platform in platforms)
220  data = {..., "privacy_level": "PUBLIC_TO_EVERYONE", "platform[]": list(platforms)}
227  if has_youtube:
229      data["privacyStatus"] = privacy if privacy in ("public","unlisted","private") else "unlisted"
```
`publish/cli.py:134` 把 `--privacy` 描述成"YouTube 可见性"，默认 `unlisted`；`backend/app/jobs.py:1750` 同源。

**问题**：`privacy_level` 是 Upload-Post 对**非 YouTube**平台（TikTok/Instagram）的可见性字段，这里无条件写死"对所有人公开"。用户以为 `unlisted` 是"先存草稿"，实际 TikTok/IG 侧会直接公开发布。

**已核实（复核 B，lead 结论）**：参数链是通的——`backend/app/jobs.py:1730-1780` 从 `meta.privacy` 或 `setting.upload_post_youtube_privacy_status` 取值（默认 `unlisted`）并以 `--privacy` 传给 CLI，`publish/cli.py:213` 也一路传到 `upload_video(privacy=...)`；**断点就在 224 行**：`privacy` 只进了 `if has_youtube` 分支（229 行），非 YouTube 平台没有任何路径能用上它。而且 **upload-post 后台的 profile 默认隐私兜不住**——payload 里显式带 `privacy_level`，provider 以显式值优先。结论：**TikTok/Instagram 永远公开，只有 YouTube 受 `--privacy` 控制**，这是真缺陷，保留 P1。

**影响**：真实外网上传时**默认公开发布**。这是**全仓唯一不可撤回的外部副作用**（视频一旦公开可被索引/转发，删帖也删不掉传播），**验收必须人工核对一次 request_id**——不能只看 `success: true`。R2 在"范围 A 未见问题"里写的是"幂等策略是对的"，它只审了重试语义，没审字段语义。

**建议修法**（映射按 lead 拍板）：
```python
PRIVACY_LEVEL = {
    "public":   "PUBLIC_TO_EVERYONE",
    "private":  "SELF_ONLY",
    "unlisted": "MUTUAL_FOLLOW_FRIENDS",
}
data["privacy_level"] = PRIVACY_LEVEL.get(privacy, "MUTUAL_FOLLOW_FRIENDS")
```
并在 `publish/cli.py` 的预检里加一条硬门：`privacy == "unlisted"` 且 `platforms` 含非 youtube 平台时**直接拒发**（进 `errors` + `return 2`），除非显式给了 `--allow-public-fallback`——不可撤的外网副作用不该静默降级。dry-run 的 payload 里回显 `{platform: 实际 privacy_level}` 映射表，让人一眼看到"这会把 TikTok 发成什么"。改动点：`upload_post.py:224` 一行 + `cli.py` 预检一处。

### P1-6 · `integrations/metaso/h3.py:129-144` 轮询一抛异常就丢 task_id；下载内容不校验却写 `ok: true`

```python
127  deadline = time.time() + args.timeout
129  while time.time() < deadline:
130      time.sleep(5)
131      rr = httpx.get(f"{base}/v2/query/video_generation/{task_id}", headers=headers, timeout=60)
132      jj = rr.json()                      # ← 无 try：一次网络抖动/非 JSON 就整体抛出
...
143  if not video_url:
144      raise SystemExit(f"超时（{args.timeout}s）未完成，task_id={task_id}")
...
149  with httpx.stream("GET", video_url, timeout=300) as rr:
150      rr.raise_for_status()
151      with open(out, "wb") as f: ...      # ← 不校验时长/编码，200 即当真
157  meta = {"ok": True, ..., "probe": probe(out)}   # probe 失败也只是 {"error": ...}
165  meta_path.write_text(...)               # ← 只有走到这里才有 meta
```
`probe()`（56-63）本身吞掉一切异常返回 `{"error": ...}`，且不检查 `returncode`。

**问题**：① 轮询阶段任何 `httpx` 异常/JSON 解析失败都直接冒到栈顶，**`meta.json` 根本不写**（写 meta 在 166 行），付费任务的 `task_id`/`video_url` 就此丢失，无法续查或另存；② 下载只做 `raise_for_status()`，CDN 返回 200 的错误页/半截 mp4 会落盘且 `ok: true`；`probe` 失败不影响 `ok`。

**影响**：云端任务的钱已花（0.09–0.15 元/秒 × 4–15s），失败后没有任何可恢复标识；坏视频会被 `pipeline/h3_comfy`/`muse_video` 当有效素材继续用。

**建议修法**：轮询体包 `try/except (httpx.HTTPError, ValueError)` → 计数 3 次后**先写 meta（含 task_id/video_url/error）再抛**；把 meta 写盘提到 `try/finally`；`ok` 判据加上 `probe` 的 `format.duration > 0` 与文件大小下限。

### P1-7 · `imagegen/cli.py:313-390` 失败也在 `finally` 覆盖 `meta.json`（`files={}`、无 `ok` 字段）

```python
313  summary["files"] = collect_files(outdir, spec_id)   # ← 只在成功路径
314  summary["ok"] = True
315  except BaseException as e:
316      summary["ok"] = False
...
377  if not args.dry_run:
378      meta_path = os.path.join(outdir, "meta.json")
379      with open(meta_path, "w", ...) as f:            # finally 里，失败也写
380          json.dump(meta, f, ...)
```
`meta` 字典（343-363）里 `"files": summary["files"]`、无 `ok`/`error` 字段。

**问题**：任一失败（ComfyUI 拒单、超时、checkpoint 缺失、PIL 打不开）都会把**上一次成功**的 `meta.json` 覆写成：`files: {}`、`created_at: 失败时刻`、`images: 旧列表`（`_merged_images` 合并）、**没有任何 ok/error 标记**。

**影响**：磁盘上的证据从"成功清单"变成"看起来成功的空清单"，且 `created_at` 被改成失败时刻；排查时无法区分"这次没生成"和"生成过但文件丢了"。实测现存 meta 形如 `files.portrait = D:\...\outputs\concepts\ali\portrait.png`、`images[0].path = ...\three_view/front.png`、`has ok field: False`。

**建议修法**：`finally` 里只在 `summary["ok"]` 或 `prev_meta` 不存在时写；失败时写 `meta.last_error.json`；`meta` 加 `ok`/`error`/`failed_tags`；`files` 用相对 `outdir` 的路径。

### P1-8 · `scripts/migrate_v50_musev_stage.py`（全文件）批量改 33 份工作流合同，无 `--dry-run`、无备份

```python
34  path.write_text(json.dumps(wf, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")   # 重排序分支
46  path.write_text(json.dumps(wf, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")   # 插入分支
```
同源兄弟 `migrate_v50_muse_stage.py:30` 有 `--dry-run`，本文件没有；两个文件都无 `.bak`。

**影响**：执行即对 `contract/v6/workflows/*.json` 做结构改写（插入 `musev`、重排），失败/写一半没有回滚；33 份合同是"用户可编辑"资产（AGENTS.md §2），被脚本覆盖后手工编辑不可追。

**建议修法**：补 `--dry-run`（默认）+ `--backup-dir`，写前 `shutil.copy2` 到 `contract/v6/workflows/.bak/<ts>/`；写后 `json.loads` 回读校验再 `os.replace`（原子替换）。

---

## P2

### P2-1 · 子进程 stderr 编码：`utf-8 + errors="replace"` 把中文报错变成 `???`（**已实测**）

**位置**：调用侧 `pipeline/v5_art.py:713`、`pipeline/templates3d_hq.py:553/700`；生产侧 `imagegen/cli.py:265-269`（只重配 `cmd_generate` 内的 stdout/stderr）。

**证据**（`docs/audit/_tools/probe_child_stderr_encoding.py` 实跑，输出见 `tmp/audit3/probe_out.txt`）：
```
utf-8 + errors=replace (现状)  | RuntimeError: ���Ĵ���checkpoint animagine-xl-4.0.safetensors �����б���
encoding=gbk   (对照)          | RuntimeError: 中文错误：checkpoint animagine-xl-4.0.safetensors 不在列表里
```
机制：Windows 上 Python 的 stdio 被重定向时默认走 locale 编码（本机 `sys.stderr.encoding == gbk`）；`imagegen/cli.py` 的 `reconfigure(encoding="utf-8")` 在 `cmd_generate()` 里，**import 期崩溃**（缺 PIL/缺 `run_h3`/语法错误）发生在它之前，于是父进程按 utf-8 解码 + `errors="replace"` → 无异常、纯乱码。

**影响**：最需要可读性的场景（子进程根本没起来）恰好只留 `???`；`V5ArtError` 的 tail 失去诊断价值。这是"GBK 兜底把损坏静默吞掉"的反向版本。

**建议修法**：`subprocess.run(..., text=True, errors="replace")` 不传 encoding（按 locale 解），或 `encoding=locale.getpreferredencoding(False)`；同时给子进程传 `env PYTHONIOENCODING=utf-8`（`run_h3.ensure_server` 已经这么做了，可复用 `env.setdefault` 模式）。

### P2-2 · `integrations/comfyui/run_h3_ref.py:67,129` `--fps` 是死参数，上报的 `duration` 是入参不是实测

```python
67  ap.add_argument("--fps", type=int, default=24)
90      length = frame_length(args.duration)      # 内部用 24fps 换算（45-48 行）
129         "duration": args.duration,             # ← 请求值冒充结果
130         "length": length, ... "fps": args.fps,
```
`h3_dynamic_probe.prepare_prompt()`（59-105）**根本没有 fps 参数**，工作流也没被改 fps。

**影响**：`pipeline/dynamic.py` 传 `--fps 25 --duration 5.49` 时，实际帧数按 24fps 算（或反之），镜头时长与预期差 ~4%；上报 JSON 的 `duration` 永远等于请求值，QC 拿它做对时会一直"通过"。

**建议修法**：删掉 `--fps` 或真正写入 `SaveVideo/VideoCombine` 节点；`duration` 用 `res["ffprobe"]["duration_sec"]` 回填，并加 `requested_duration` 字段。

### P2-3 · `imagegen/tools/rebuild_meta.py:91-107` 重建工具伪造一半字段

```python
96      "mode": "all", "style": "anime",
99      "steps": 28, "cfg": 5.5, "sampler": "euler_ancestral", "scheduler": "normal",
100     "seed": 42,
101     "size": {"portrait": [832,1216], "other": [1024,1024]},
104     "created_at": None,
```
而 `image_meta()`（38-65）其实**逐图**读到了 seed/steps/cfg/sampler/scheduler——只有 `checkpoint` 被用到（87-90）。

**影响**：这个工具的定位是"从 PNG 内嵌 prompt 还原真相"，却把非默认参数（`--seed 777 --steps 30 --cfg 4 --style realistic`）全部写成 imagegen 默认值；`created_at: None` 也会让按 ISO 解析的消费方报错。等于**用工具制造假证据**。

**建议修法**：从 `images[0]` 的 `info` 里取 steps/cfg/sampler/scheduler/seed/size；style/mode 作为 `--style/--mode` 参数传入；`created_at` 用 `--created-at` 或 `datetime.now()`；无图时直接 `return 2`。

### P2-4 · 硬编码本机绝对路径（4 处，全部无 env 兜底）

| 位置 | 代码 |
|---|---|
| `imagegen/comfy.py:33-34` | `os.environ.get("COMFYUI_ROOT", r"D:\BaiduSyncdisk\wmj_work\XJ\ComfyUI")` |
| `integrations/comfyui/workflow_to_api.py:327-328` | `os.environ.get("COMFYUI_ROOT", r"D:\...\ComfyUI")` |
| `integrations/comfyui/run_h3.py:49` / `run_h3_ref.py:31` / `tools/h3_dynamic_probe.py:22` | 同上 |
| `scripts/make_3d_cutouts.py:11`、`scripts/make_3d_showcase.py:6` | `ROOT = Path(r"D:\BaiduSyncdisk\wmj_work\XJ\AiJue")`（**无 env**） |

**影响**：前 5 处至少可覆盖；后 2 处硬编码到具体盘符+中文目录，换机/换目录即失效，且它们是"模块级执行"的脚本（见 P2-5）。

**建议修法**：`scripts/*` 统一 `Path(__file__).resolve().parents[1]`（30 个脚本已经这么写，只有这 2 个例外）。

### P2-5 · `scripts/make_3d_cutouts.py:13-28`、`make_template_showcase.py`、`make_scene_showcase.py`、`make_3d_showcase.py` 模块级执行、无 `__main__` 守卫

```python
# make_3d_cutouts.py
13  meta = {}
14  for cid in CHARS:                      # ← 模块级循环
22          im.save(dst)
27  (ROOT/"outputs"/"characters"/"cutout_3d_meta.json").write_text(...)
```
```python
# make_template_showcase.py:14,48-52
14  results = json.loads((OUT / "results.json").read_text(...))   # 无 is_file 检查
48  vf = ("...drawtext=fontfile='%s':text='%s':...") % (FONT.replace("\\","/"), label)
```

**影响**：`import` 即产出/覆盖文件（`cutout_3d_meta.json`、`docs/screenshots/48_templates_contact_sheet.png`、`outputs/template_demos/template_showcase.mp4`）；`results.json` 缺失时直接 traceback 而非友好报错；`label` 未做 ffmpeg 转义（实测：`text='100% happy'` → `Stray % near ' happy'` + 非 0 退出），`text='Kid's Show'` 这类单引号同理。

**建议修法**：全部包 `main()` + `if __name__ == "__main__"`；`label` 走 `textfile=` 或 `drawtext` 转义（`%`→`\%`、`'`→`'\\''`、`:`→`\:`）；`results.json` 缺失时打印指引并 `return 2`。

### P2-6 · `localize/translator.py` 文档声称"优先 Ollama、回退 NLLB"，代码里没有回退；空译文用原文冒充且 `ok=True`

```python
1  """多语种翻译：优先本地 Ollama qwen2.5:7b（质量好），回退 NLLB-200。"""
124     if not text or (target_lang not in CJK_KEEP and CJK_LEFT.search(text)):
125         raw = _ollama_generate(...)          # 仍是 ollama，失败直接抛
128         text = raw.splitlines()[0].strip() if raw else str(unit.get("text") or "")  # ← 原文当译文
...
192     translations = _translate_ollama(units, target_lang)
194 return {"ok": True, ...}                    # ← 永远 ok
```
另外：`LANG_CODES`（21-31）支持 9 种语言（含 fr/de/ar），`LANG_NAMES`（33-42）只有 8 种（缺 zh），`localize/terms.py` 的 `FONTS/WRAP_WIDTH/VOICES` 只覆盖 5 种 → fr/de/ar 会一路走到默认值（中文行宽/默认音色）。`prepare_source()` 的 `NAME_MAP` 只在 nllb 路径生效（158 行），默认 ollama 路径完全不调用它。

**影响**：三种"假能力"：① 没有回退链（并非降级而是报错）；② 模型返回空串时把**中文原文**写进 `translations.json` 并标 `ok: true`，下游 `pipeline/localize` 会把它当译文烧进字幕（英文片里出现中文句子且无告警）；③ fr/de/ar 的排版/音色静默取默认。

**建议修法**：`translate_units` 增加 `fallbacks`/`failed_keys` 字段并显式 `ok=False`（或至少 `partial=True`）；空译文标 `untranslated` 而不是回填原文；`provider="auto"` 才走 ollama→nllb；术语表补齐 9 语言或把 `LANG_CODES` 收敛到 5 种。

### P2-7 · `studio2d/` 整包是死代码（1838 行，零调用）

**证据**：`grep -r "studio2d"` 全仓 13 处命中，全部在 `studio2d/` 自身（docstring/`__init__`）；`tests/` 无引用；`pipeline`/`backend`/`scripts` 无 `import studio2d`。

**影响**：与 `imagegen` 产出**同一套契约**（`portrait.png/three_view/expressions/sheet.png/meta.json`）的第二套实现，但没有任何调用方——文档若声称"支持程序化 2D 生成"即为假能力；两套实现的 meta 字段还不一致（studio2d 写 `renderer/generator/palette`，imagegen 写 `model/steps/cfg`）。

同包内的小问题：`studio2d/cli.py:77` `os.path.relpath(abspath(args.spec), os.getcwd())` → meta 内容随运行目录变化，跨盘符会 `ValueError`；`studio2d/render.py:212-213` 是空 `if ...: pass` 死代码；`--size`（122 行）无校验，传 `768x1024` 会以 traceback 收场。

**建议修法**：要么删包（推荐，功能已被 imagegen 覆盖），要么在 `README` 标注 experimental 并在 `backend` 侧真正接一条调用链；`meta["spec"]` 改存 `spec` 的 repo 相对路径。

### P2-8 · 会覆盖 33 份工作流合同且无备份的另两个脚本

- `scripts/seed_v6_workflows.py:32-36`：`--force` 时 `path.write_text(...)` 全覆盖，help 里写"会覆盖手工编辑，慎用"，但**没有备份、没有 dry-run、没有 diff 预览**。
- `scripts/gen_agent_workflows.py:19-22`：`save_workflow(wf)` 覆盖 16 个内建模板，也没有备份/确认。

**影响**：AGENTS.md 明确"工作流是用户可编辑的 JSON 合同"；一条命令即可抹掉全部手工编辑。

**建议修法**：写前 `if path.exists(): copy2(path, path.with_suffix(f".{ts}.bak"))`，并打印 `changed/added/skipped` 三类；`--force` 再加 `--yes` 二次确认。

### P2-9 · `scripts/*.ps1` 全部 UTF-8 无 BOM（Windows PowerShell 5.1 下中文乱码）

**证据**：`dev.ps1` 583B/110 个非 ASCII 字节、`verify.ps1` 1023B/24、`screenshot.ps1` 667B/36、`build-frontend.ps1` 309B/42、`demo-render.ps1` 335B/39，**均 no-BOM**；内容含 `Write-Host "== 造片局 · AI 微综艺 =="` 之类中文。`README.md:35-50`、`docs/DEMO_GUIDE.md:6-7` 都用 `.\scripts\xxx.ps1` 调用（在 `powershell.exe` 5.1 下即按 cp936 解析无 BOM 脚本）。

**影响**：5.1 下中文提示变乱码；若某行中文的 GBK 误判字节对上 `"`/`$`/`` ` ``，会变成语法错误直接拒绝执行。`pwsh 7`（默认 UTF-8）不受影响，所以本机"看起来正常"。

**建议修法**：五个 `.ps1` 加 UTF-8 BOM，或首行 `#requires -version 7` + 文档统一写 `pwsh`。

### P2-10 · `imagegen` 的 meta 契约不稳定（`size` 类型漂移 + 全部绝对路径）

```python
cli.py:281     "size": args.size,                                  # summary：None 或 "1280x720"(str)
cli.py:356     "size": args.size or {"portrait": [...], "other": [...]},   # meta：str 或 dict
cli.py:313     summary["files"] = collect_files(outdir, spec_id)    # 全部 abspath
cli.py:367     "ref": os.path.abspath(args.ref)
```
**证据**：现存 `outputs/concepts/ali/meta.json` 里 `files.portrait = D:\...\portrait.png`、`images[0].path = D:\...\three_view/front.png`（反斜杠+正斜杠混用）。

**影响**：`--size` 一旦使用（`templates3d_hq` 每条都传），`meta.size` 就从 dict 变 str，按 `meta["size"]["portrait"]` 读的代码会 `TypeError`；绝对路径使产物目录不可搬移/不可归档（当前无生产消费方，但 `rebuild_meta.py`、`v5_scene_art._meta_matches` 这类"按字段比对"的代码已在附近)。

**建议修法**：`size` 恒为 `{"w":..,"h":..}` 或恒为 `[[w,h],...]`；`files`/`images[].path` 存相对 `outdir`；`ref` 存相对 repo。

### P2-11 · `integrations/comfyui/run_h3.py` 取产出不看 `save_nid`；`probe_image/probe_video` 无 timeout

```python
421  files.sort(key=lambda f: (0 if os.path.splitext(f[0])[1].lower() in (".mp4",...) else 1, f[0]))
423  filename, subfolder, typ = files[0]      # ← 按文件名排序取第一个，而不是 SaveVideo 节点的产出
```
```python
132  out = subprocess.run(cmd, capture_output=True, ...)     # probe_image：无 timeout
141  out = subprocess.run(cmd, capture_output=True, ...)     # probe_video：无 timeout
```
**影响**：工作流里有多个视频类产出（预演 + 正式）时静默取错那个；ffprobe 卡在畸形输入上会永久挂住（外层有 900s 轮询超时，但这一步在超时之外）。

**建议修法**：`_outputs_of(node_id)` 按 `meta["save_node"]` 过滤；两处 `subprocess.run(..., timeout=60)`。

### P2-12 · `imagegen/tools/download_model.py` 无 manifest、sha256 可选 → 续传可静默拼出坏权重

```python
124  start_off = os.path.getsize(out) if os.path.exists(out) else 0     # 只信文件长度，不记来源/总长
134  ranges = [(s, min(s + chunk - 1, total - 1)) for s in range(start_off, total, chunk)]
182  with open(out, "ab") as fo: ...                                      # 追加
202  if args.sha256:     # 可选；不给就只比长度
```
**影响**：换 `--url`（或 CDN 换版本）后断点续传会把两代文件拼在一起，长度恰好的坏文件会被 ComfyUI 当权重加载（随后 SDXL 出糊图，而不是报错）；`"elapsed_sec": None` 恒为 null。

**建议修法**：`<out>.manifest.json` 记 `url/sha256/total/etag`，续传前校验一致；sha256 缺失时至少校验 `ETag`+`Content-Length`；`elapsed_sec` 填实测。

---

## scripts 危险操作清单

扫描器：`docs/audit/_tools/scan_scripts_danger.py --md`（AST 级，45 个 .py）。"写什么"只列**有副作用**的项。

| 脚本 | 写什么 | 有无备份 · dry-run | 判定 |
|---|---|---|---|
| **rebuild_all_looks_5min.py** | `rmtree(outputs/templates_3d/<tid>/shots)` + `shots_<look>` + `work/episode.json` | 无备份 · 无 dry-run · 恒返回 0 | **P0 修** |
| expand_3d_shots.py | `contract/.../templates3d/<tid>_semantic.json`（新增 shot） | 备份仅首次（`.json.bak`，之后不再更新）· 有 `--dry-run`/`--reset` | **P1 修** |
| clear_stale_render_urls.py | 生产 DB `Episode.render_url/status` + commit | 无备份 · 无 dry-run · **无 `__main__` 守卫** | **P1 修** |
| fix_loudness.py | `final.mp4`（就地替换）+ `final.preloud.mp4` + `qc_report.json` + `loudness_fix_report.json` | rebuild 路径备份顺序正确；**audio-only 路径备份=修复后文件** · 有 `--fix` 开关（默认只扫描） | **P1 修** |
| run_episode.py | `work/episode.json`×2、`outputs/templates_3d/<tid>/shots/*`、成片 | 无备份 · 无 dry-run · 不看子进程退出码 · 恒返回 0 | **P1 修** |
| migrate_v50_musev_stage.py | 33 份 `contract/v6/workflows/*.json` | 无备份 · **无 dry-run** | **P1 修** |
| seed_v6_workflows.py | 同上（`--force` 全量覆盖） | 无备份 · 无 dry-run · 默认只补缺失 | P2 修 |
| gen_agent_workflows.py | 16 份 `contract/v6/workflows/*.json` | 无备份 · 无 dry-run | P2 修 |
| migrate_v50_muse_stage.py | 33 份工作流（插入 muse 阶段） | 无备份 · **有** `--dry-run` | P2（补备份） |
| rebuild_all_3d_5min.py | `work/episode.json`、`shots/`（生成）、成片、report | 无备份 · 无 dry-run · 渲染非 0 仍继续 · 恒返回 0 | P2 修 |
| rebuild_all_5min.py | 同上（V5 栈）+ report | 无备份 · 无 dry-run · 有 `False` 返回分支 | P2 修 |
| make_3d_cutouts.py | `outputs/characters/<id>/cutout_3d/front.png`、`cutout_3d_meta.json` | 无备份 · 无 dry-run · **模块级执行** · 硬编码 ROOT | P2 修 |
| make_template_showcase.py | `docs/screenshots/48_*.png`、`template_showcase.mp4`、`manifest.json` | 无备份 · 无 dry-run · **模块级执行** | P2 修 |
| make_scene_showcase.py / make_3d_showcase.py | `outputs/scene_demos|3d_showcase` 下的 mp4/concat.txt | 无备份 · 无 dry-run · 模块级执行 · drawtext 未转义 | P2 修 |
| gen_relic_template.py | `contract/v5/templates/relic_talk.json` | 无备份 · 无 dry-run · 覆盖手写模板 | P2 修 |
| verify_v45_2d_flow.py | 建/删 `custom_tpl_v45_flowcheck` 合同与 `outputs/templates_{2d,3d,v5}/<FID>` | 删除限定 FID（可接受）· 无 dry-run · **会真跑 GPU 5–8 分钟** | P3（加 `--keep`） |
| gen_3d_anime_assets.py / gen_scene_plates.py / gen_style_plates.py | 资产 png + contact sheet + meta；`os.remove(<dst>.raw.png)` 临时文件 | 临时文件删除安全 · 无 dry-run | P3 |
| render_templates_v3.py / render_templates_v5.py / render_scene_demos*.py / render_style_demo*.py / render_template_demos.py | 成片 + `results/verify.json` | 无 dry-run（重活，需人守） | P3 |
| rebuild_templates3d_progress.py / regen_v5_character_fronts.py / repair_v5_char_art_v49.py / gen_v5_*.py | progress/meta/report JSON + 资产 | progress 用 `.json.tmp` + `replace`（好）· 无 dry-run | P3 |
| 其余 9 个（check_audio/check_seasons/finalize_longform/tts_server/verify_h3_evidence/fetch_syncnet 等） | 只读成片/合同，或只写自身报告（`*_report.json`） | — | 通过 |

补充：`scripts/__pycache__` 有 43 个 `.pyc`（含 **cpython-311 + cpython-313 两套**），全部**未被 git 跟踪**（`git ls-files | grep .pyc` = 0），所以只是磁盘噪音；但 `.gitignore` 没有 `__pycache__` 之外的覆盖，建议顺手加 `.gitignore: __pycache__/ *.pyc`。

---

## 已核对无问题的点

以下都是**主动验证过的"看起来可疑但成立"**，避免下一轮重复怀疑：

1. **imagegen 命令行参数在 Windows 上安全**：全部调用方（`pipeline/v5_art.py:694`、`pipeline/templates3d_hq.py:536/691`、`backend/app/jobs.py:1030`、4 个 `scripts/gen_v5_*`）都用 **list 形式** + `cwd=REPO`，无 `shell=True`（全仓唯一 `shell=True` 在 `pipeline/muse_video.py:477`，不属本轮）；`str` 参数经 `CreateProcessW` 传递 → 中文/引号/换行/`%` 都不会被 cmd 解析，长度也远低于 32K 上限。
2. **两个主要调用方都查了 returncode + timeout**：`v5_art.py:713-730`（`timeout=600`，非 0 或 `summary.ok` 假即抛）、`templates3d_hq.py:553-558/700-705`（`timeout=660`，`returncode` + 产物存在双检）。**真正的缺口在 P2-1 的编码与 P1-7 的 meta**，不是"不检查返回码"。
3. **`imagegen.cli` 的 JSON 摘要解析不会误吞**：`print(json.dumps(...))` 是最后一行，`[warn]/[done]` 都在它之前；`v5_art` 的倒序扫 `{...}` 行能稳定命中；prompt 里的换行已被 JSON 转义。
4. **`imagegen` 的 seed 语义**：`--mode scene` 时 `extra_seed=0`（`build_plan` 第 97-99 行）→ 实际 seed == 传入 seed，与 `templates3d_hq` 的 `base_seed` 一致。
5. **`upload_post.wait_for_completion` 的重试/退出条件是对的**：`consecutive_errors` 3 次异常退出、1h deadline、`completed` 无 results 判失败、`failed` 带 message、超时返回 `request_id` 供人工核对，4xx 不带 request_id、5xx 才带（`upload_post.py:98-148, 254-271`）。
6. **`upload_post._response_error_text` 不会泄漏 key**：只读 `response.json()/text`，从不回显 headers（`151-186`）。
7. **`publish/cli.py` 的 dry-run 边界**：`--dry-run` 时不 import、不调 `upload_video`，只做本地 ffprobe 预检（`193-197`）；`UPLOAD_POST_API_KEY` 只从环境变量读、不写进 payload（`185-186`）。
8. **`integrations/metaso/h3.py` 的 key 处理**：`--key > env > backend/data/metaso.env > integrations/metaso/.env`；`backend/data/metaso.env` 与 `integrations/metaso/.env` **均在 `.gitignore`**（实测 `git ls-files` 无命中）；日志只打 `img.name` 与 `mm_file://` URL，不打 key；`backend/data/server-8090.out.log`（633KB）对 `Apikey |Bearer |sk-|METASO_API_KEY=` 的匹配数为 **0**。
9. **`integrations/comfyui/run_h3.py` 的失败语义**：`submit` 非 200 抛、`wait_prompt` 把 `status_str=error`/`completed=False` 转异常、`collect_outputs` 无产出抛、`main` 恒写 `*.meta.json` 并 `return 1`（比 imagegen 的 meta 处理更严）。
10. **`workflow_to_api.ui_to_api`**：`mode=2/4`、UI-only 节点、未安装节点会被跳过并给 warning；必填输入缺失只警告不阻断（设计上交给 ComfyUI 裁决），未见静默改数据。
11. **`verify_h3_evidence.py` 的结论是真的**：`ok = candidates and not missing and not failed and not mismatched and not duplicates`（`425-428`），ComfyUI 不可达时明确 `ok=False` + hint，`main` 返回 1；只 GET `/history`，无写操作。
12. **`check_seasons.py` / `check_audio.py` / `finalize_longform.py`**：只读合同/成片 + 写自身报告，`Path(__file__).resolve().parents[1]` 定位，退出码区分 warning；`finalize_longform` 的 `--wait-pid` 用 `tasklist` 轮询有 deadline。
13. **`tts_server.py` / `fix_loudness.py` 的入口**：subcommand 校验完整、`--fix` 默认关闭（只扫描）、`is_rendering()` 检测渲染锁后跳过（`fix_loudness.py:141-143`）。
14. **`localize/translate_cli.py`**：`--provider` 有 `choices`、`--in/--out` 必填、返回 `0 if result.ok else 1`、写盘前 `mkdir(parents=True)`。
15. **`pipeline/localize.py` 的翻译缓存键是对的**（曾被怀疑）：`payload["translations"]` 用**原始 unit key**（`localize.py:346`），下一次 `covers_all` 检查同一批 key（`304-305`）→ 命中；`u{i}` 只是批内临时键（`336`）。
16. **R9/R10 已修**（勿重复报）：`gen_relic_template.py:13`、`gen_agent_workflows.py:7` 已改 `Path(__file__).resolve().parents[1]`；`translator.prepare_source(text, target_lang)` 已对 zh/ja 跳过罗马化（`git diff` 可见）。
17. **`ffmpeg drawtext` 的 `fontfile='C:/...'` 写法成立**（实测 exit 0）：单引号足以保护盘符冒号，不是 bug；真正会炸的是文本里的 `%`/`'`（见 P2-5）。
18. **`studio2d` 无除零/越界**：唯一的除法 `render.py:104` 分母是常量半径比；`_radial`/`_frame_bust` 都有 `max(1, ...)`；1838 行里没有 `%`/整除风险（但整包是死代码，见 P2-7）。
