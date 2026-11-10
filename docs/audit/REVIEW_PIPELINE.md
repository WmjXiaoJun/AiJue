# pipeline/ 代码审查（不含 agents 子包）

- 审查人：review-pipeline（只读审查，未修改任何源码/数据）
- 范围：render_v5 / scene_render / motion2d / editing / timeline / edit_v4 / ass_builder / style_v5 / v5_* / templates3d* / stage / h3_comfy / h3_v5 / dynamic / cinema3d* / qc / qc_v3 / qc_v5 / lipsync / mouth_sync / muse_video / deliver / v5_catalog / v5_cli / utils / config / tts* / capcut / localize
- 方法：静态阅读 + 4 个只读探针脚本（产物全部写在 `tmp/audit/`，不触碰 outputs/contract）
- 已排除的已知项：SyncNet 权重损坏、templates_2d/templates_3d 双份成片、无鉴权、frontend dead code v5world/*、README 旧章节

复现命令：

```powershell
python tmp/audit/verify_lipsync.py    # F1 的 AST 证据 + F3/F4/F5
python tmp/audit/verify_deliver.py    # F11 的 ffmpeg 行为
python tmp/audit/verify_outputs.py    # F11 的存量产物证据（只读 ffprobe）
```

---

## 结论速览

| # | 严重度 | 位置 | 一句话 |
|---|---|---|---|
| F1 | **P0** | render_v5.py:1028 | `style` 缩进错进 `if BREEZE_TTS_KEEP` 分支，设了该环境变量整条 V5 渲染必炸 |
| F2 | **P1** | templates3d_hq.py:629 / 703 / 506 | T2VA/ref2va/关键帧缓存只用 (模板,镜号,风格) 做 key，改 prompt/seed/时长永不生效，`force` 也清不掉 |
| F3 | **P2** | lipsync.py:857-862 / 871-875 | 跨进程锁只凭 mtime>stale 就抢**活着的**持有者的锁；释放时无条件 unlink 别人的锁 |
| F4 | **P2** | lipsync.py:1108-1120 | 无脸/帧数不足时 `ok=True` + `corr_best=None` + `motion_mean=NaN`（非法 JSON），QC 会被骗过 |
| F5 | **P2** | lipsync.py:1145-1148 | 从没跑过 SyncNet，但只要权重"可读"就把 `provider` 标成 `syncnet`，且没有 LSE-C/LSE-D 字段 |
| F6 | **P2** | render_v5.py:285-287 | `min(MAX_SHOT, …)` 被下一行原样撤销 → 长台词镜头不封顶，转而删掉别的镜头 |
| F7 | **P2** | render_v5.py:949-960 + qc_v5.py:249 | qc_v5 的口型硬门从没被喂过 `lip_sync`，Muse 同步分永远进不了 QC |
| F8 | **P2** | render_v5.py:775-782 / 830-840 | video_raw / narration 拷贝失败只 warning，final_encode 静默吃上一轮的旧文件 |
| F9 | **P2** | lipsync.py:1034 + backend/app/routers/muse.py:145 | 同步度指标没有 `start` 参数：分段镜头拿整条音频对齐，start>0 时分数无意义 |
| F10 | **P3** | render_v5.py:698-703 | `except TypeError` 把 motion2d 内部的真 TypeError 当成"旧版不支持 mouth_strength"，静默二次渲染且口型强度变了 |
| F11 | **P3** | deliver.py:69-73 / 101 | `-c:a copy` 无 `-shortest`：音频可长于视频（存量产物已见 +0.207s）；`--hd` 参数是死的 |
| F12 | **P3** | mouth_sync.py:53-54 / 87 | `lead` 形参从未使用；口型形状用 `chars[i % len]` 与台词位置脱钩 |

---

## F1 · P0 · `BREEZE_TTS_KEEP=1` 时整条 V5 渲染 UnboundLocalError

**位置**：`pipeline/render_v5.py:1020-1028`（赋值）、`render_v5.py:1036`（首次使用）、`render_v5.py:1047`（致命使用）

```python
1020  if str(os.environ.get("BREEZE_TTS_KEEP", "")).lower() not in ("1", "true", "yes"):
1021      try:
1022          from . import tts_breeze
1023          tts_breeze.stop_server()
...
1028          style = _style(catalog, str(plan["episode"].get("style") or template.get("style") or ""))   # ← 缩进在 if 内
1029  # V5.4 剪辑计划
1030  try:
1031      from . import editorial
...
1036      edit_plan = editorial.build_edit_plan(template, plan, style, ...)   # 在 try 内，被吞
1047  prog(20, f"逐镜 2D 动画渲染（{len(plan['shots'])} 镜，风格 {style.get('label')}）")   # 不在 try 内 → 炸
```

**触发条件**：`BREEZE_TTS_KEEP=1`（或 `true`/`yes`）跑 `python -m pipeline.cli render_v5 …` / `render_v5.run_render`。这是仓库里唯一"保留 TTS 服务"的开关（render_v3.py:997 同款语义），日常想省一次 Breeze 冷启动就会踩。

**为什么错**：`style` 在 `run_render` 里只在这一处赋值，且被缩进到了 `if` 块内。设了开关 → 1036 的 `NameError` 被 1043 的 `except Exception` 吞掉（日志只写 "V5.4 剪辑计划失败"），继续跑到 1047 → `UnboundLocalError` → `main()` 的兜底 `except` 返回 `{"ok": false}`。**结果：TTS 已经跑完（最贵的一步），成片一行没渲。** 这 4 个空格纯粹是缩进事故。

**验证**（AST，见 `tmp/audit/verify_lipsync.py` D 段）：

```
line 1028: style = ... guards=['If']        # 唯一的绑定，位于 If 内
first loads of style at lines: [1036, 1047, 1048, 1051, 1065, 1070]
```

**最小修法**：把 1028 移出 `if`（顶格，与 1029 的注释同级）：

```diff
-        style = _style(catalog, str(plan["episode"].get("style") or template.get("style") or ""))
+    style = _style(catalog, str(plan["episode"].get("style") or template.get("style") or ""))
```

---

## F2 · P1 · HQ 短片缓存 key 不含 prompt/seed/时长，`force` 也救不回来

**位置**：`pipeline/templates3d_hq.py:629`（T2VA）、`:703`（ref2va）、`:506-517`（关键帧）；`pipeline/agents/registry.py:279`

```python
# gen_t2va (629)
suffix = f"{STYLE_2D_CACHE}_{style_key}" if style_mode == "2d" else ""
out = WORK / "clips" / f"{tid}_{shot['id']}_t2va1024{suffix}.mp4"
if out.is_file() and out.stat().st_size > 200_000:
    return out                      # ← 文件在就返回，不看 prompt / seed / duration / frame_length
```

```python
# _generate_keyframe 的缓存判定 (506-517)：只比 ip_strength / ip_end / ckpt / candidates_count
params_ok = (abs(float(cached_meta.get("ip_strength") or -1) - effective_ip) < 0.001
             and abs(float(cached_meta.get("ip_end") or -1) - effective_end) < 0.001
             and str(cached_meta.get("ckpt") or "") == str(style["ckpt"])
             and int(cached_meta.get("candidates_count") or 1) == n_cand)
# prompt_extra / negative_extra / seed 变了也命中缓存
```

```python
# registry.py:279 —— 默认就带 --reuse-shots，只有 ctx.force 才去掉
if params.get("reuse_shots", True) and not ctx.force:
    argv += ["--reuse-shots"]
```

**触发条件**：改 `contract/v5/cinema3d/templates3d/<tid>_semantic.json` 的 `prompt`/`semantic_visual`/`duration`，或改 episode 里对应镜头时长，然后重跑 shots 阶段（默认或 `force=True`）。

**为什么错**：`main()` 里没有 `--force` 这个开关，`ctx.force` 只影响 `--reuse-shots`（跳过整镜）：
- 无台词 B-roll 镜：去掉 `--reuse-shots` 后仍走到 `gen_t2va` → `st_size > 200_000` 命中 → **一帧都没重画**；
- 2D 有台词镜：`_generate_keyframe` 命中旧关键帧（prompt 不在 key 里）→ `gen_ref2va` 强制 unlink 重跑 H3，但**首帧还是旧的** → 花 6-10 分钟 GPU 换来和上一版一样的画面；
- 改 duration 更直接：`frame_length()` 的输入变了，缓存照样命中，成片靠 `editing` 的 `tpad` 冻末帧补齐 → 镜头尾部画面冻结，流程却 `ok`。

**最小修法**：把内容指纹塞进缓存判定（不改调用方）：

```diff
+def _clip_key(prompt, seed, style_mode, style_key, duration, length):
+    return utils.sha1_text(prompt, seed, style_mode, style_key, duration, length)
 # gen_t2va
-    if out.is_file() and out.stat().st_size > 200_000:
+    key = _clip_key(prompt, seed, style_mode, style_key, duration, length)
+    keyf = out.with_suffix(".key")
+    if keyf.is_file() and keyf.read_text(encoding="utf-8") == key \
+            and out.is_file() and out.stat().st_size > 200_000:
         return out
+    keyf.write_text(key, encoding="utf-8")
```
关键帧同理把 `prompt_extra`/`negative_extra`/`seed` 加进 `meta` 并在 `params_ok` 里比较；`registry.render_2d_hq` 增加 `--force` 透传（清 `tmp/v16/clips/*_<sid>_*`）。

---

## F3 · P2 · Muse 跨进程锁会抢活人的锁，释放时又会删掉别人的锁

**位置**：`pipeline/lipsync.py:832-868`（`_acquire_lock`）、`:871-875`（`_release_lock`）

```python
845  except FileExistsError:
846      owner = _lock_owner()
848      age = time.time() - _LOCK_PATH.stat().st_mtime
851      if owner and not _pid_alive(owner):
853          _LOCK_PATH.unlink()      # 死主回收：正确
857      if age > stale:              # ← 没有 "owner 还活着" 的前置条件
859          _LOCK_PATH.unlink()      # 活着的持有者也会被抢
871  def _release_lock() -> None:
873      _LOCK_PATH.unlink()          # 不校验 owner 是不是自己
```

**触发条件**：单个 Muse 任务持锁超过 `stale=900s`（锁文件 mtime 只在创建时写一次，全程不刷新）。最典型是 `--device cpu` 回退（`_Models.__init__` 无 CUDA 时回落 cpu，600 帧 CPU 推理远超 15 分钟），或某一镜特别长。

**为什么错**：锁的全部意义是"Muse 独占 GPU"。A 跑过 900s 后 B 进来直接 unlink 抢锁开跑 → 两个进程同时 load UNet/VAE → 显存 OOM 或双双掉到 CPU；更糟的是 A 结束时 `_release_lock` 会删掉 **B** 的锁，于是 C 也能进来。这是"锁存在但互斥不成立"，比没有锁更难查。

**验证**（`tmp/audit/verify_lipsync.py` C 段，用"当前进程 PID + 1000s 前的 mtime"模拟活着的持有者）：

```
live pid = 52800 alive? True owner in file: 52800
_acquire_lock RETURNED (stole the live owner's lock) after 0.00s
```

**最小修法**：stale 回收只对"读不出 owner"的僵尸锁生效，并刷新心跳：

```diff
-            if age > stale:
+            if not owner and age > stale:      # 只有无主锁才按年龄回收
                 _LOCK_PATH.unlink(); continue
+            if age > stale:                    # 有主但太久：只告警，不抢
+                LOG.warning("Muse 锁已持有 %.0fs（owner=%s），疑似卡死", age, owner)
```
再加 `_release_lock(owner=os.getpid())`：只有 `_lock_owner()==os.getpid()` 才 unlink；长任务里每 60s `os.utime(_LOCK_PATH)` 刷心跳。

---

## F4 · P2 · 无脸时 `mouth_audio_sync` 仍返回 `ok=True`，并写出非法 JSON（NaN）

**位置**：`pipeline/lipsync.py:1108-1120`

```python
1107  motion_arr = np.asarray(motion, dtype=float)
1108  return {
1109      "ok": True,                     # ← 无论有没有检测到脸
1111      "corr_best": round(best["corr"], 4) if best["corr"] is not None else None,
1117      "motion_mean": round(float(np.nanmean(motion_arr)) if n else 0.0, 3),   # 全 NaN → NaN
```

**触发条件**：镜头里 YuNet 一帧都没检到脸（抠背影 / 大全景 / 侧逆光 / 强风格化），或有效帧 <8 让 `_pearson` 直接返回 None。Muse 产物里"脸被 H3 画糊"是最常见情况。

**为什么错**：调用方全靠 `ok` 判断成败（`agents/registry.py:405`、`backend/app/routers/muse.py:148`），于是"根本没测出任何东西"被记成"同步分已计算"，`corr_best=None` 在下游被当成"没超阈值所以不拦"。同时 `motion_mean` 是 NaN：`json.dumps` 原样输出 `NaN`，Python `json.loads` 收得回，**JS / orjson / allow_nan=False 全部报错**（`utils.write_json` 有 `_finite` 兜底，但 `lipsync` CLI 的 `--json` 直出 stdout，父进程按行解析）。

**验证**（`tmp/audit/verify_lipsync.py` A 段：640x360 testsrc2 无脸片 + 440Hz 音）：

```
{"ok": true, ..., "corr_best": null, "lag_frames": 0, "frames": 75, "face_ratio": 0.0, "motion_mean": NaN, ...}
JSON contains bare NaN -> True
strict json parse FAILS: non-strict NaN
RuntimeWarning: Mean of empty slice   (lipsync.py:1117)
```

**最小修法**（3 行）：

```diff
-    return {
-        "ok": True,
+    if detected < 4 or not np.isfinite(motion_arr).any():
+        return {"ok": False, "method": "mouth_motion_vs_audio_rms",
+                "reason": f"未检测到可用人脸（face_ratio={detected / max(n, 1):.2f}）",
+                "frames": n, "face_ratio": round(detected / max(n, 1), 3)}
+    return {
+        "ok": True,
...
-        "motion_mean": round(float(np.nanmean(motion_arr)) if n else 0.0, 3),
+        "motion_mean": round(float(np.nanmean(motion_arr)), 3) if np.isfinite(motion_arr).any() else 0.0,
```

---

## F5 · P2 · `measure_sync` 把口型代理指标冒充 SyncNet

**位置**：`pipeline/lipsync.py:1140-1149`

```python
1145  syncnet = _syncnet_status()
1146  result = mouth_audio_sync(clip_mp4, audio_wav)      # ← 无论 syncnet 是否 ready，都只算代理指标
1147  result["syncnet"] = syncnet
1148  result["provider"] = "syncnet" if syncnet.get("ready") else "mouth_audio_corr"
```

**为什么错**：`provider` 描述的是"算出来的数是谁给的"，而不是"权重文件在不在"。一旦 `syncnet_ready=True`（权重换成好文件就会），任何按 `provider=="syncnet"` 分流的下游（报告、看板、未来的 QC 门）都会把 `corr_best`（±3 帧的嘴动/RMS 相关）当成 LSE-C/LSE-D 用，而返回值里根本没有 LSE 字段——取不到就是 None，静默降级。`device` 形参也从未被使用，说明这条分支没写完。

**验证**（`tmp/audit/verify_lipsync.py` B 段，monkeypatch `_syncnet_status` 返回 ready）：

```
{'ok': True, 'provider': 'syncnet', 'corr_best': None, 'lag_frames': 0, 'face_ratio': 0.0}
has LSE fields -> []
provider claims syncnet -> True
```

**最小修法**：`provider` 只反映实际实现，权重可用单独放一个字段：

```diff
-    result["provider"] = "syncnet" if syncnet.get("ready") else "mouth_audio_corr"
+    result["provider"] = "mouth_audio_corr"   # SyncNet LSE 分支尚未实现
+    result["syncnet_ready"] = bool(syncnet.get("ready"))
```

---

## F6 · P2 · `MAX_SHOT` 是死代码：长台词镜头不封顶，代价是别的镜头被删

**位置**：`pipeline/render_v5.py:285-287`

```python
285  dur = min(MAX_SHOT, max(MIN_SHOT, round(speech + LEAD_SEC + TAIL_SEC, 2)))
286  if dur < speech + LEAD_SEC + TAIL_SEC - 0.01:
287      dur = round(speech + LEAD_SEC + TAIL_SEC, 2)    # ← 把上面那个 min(MAX_SHOT,…) 原地撤销
```

**触发条件**：任意一条台词 TTS 时长 > `MAX_SHOT - LEAD - TAIL` = 11.55s（长段独白、语速慢的 voice preset、TTS 异常拖长）。

**为什么错**：286 的判定在 `dur` 被 `MAX_SHOT` 夹住时恒为真 → 立刻还回 `speech+0.45`。所以 `MAX_SHOT=12.0` 对长台词完全无效，镜头能到 20s+。真正受伤的是 `_trim_to_target`（`:349`）：它为了压回目标时长会**整镜删掉**其它 b-roll，于是"一句超长台词"表现为"成片少了 2 个镜头"，而 `episode.json` 里那镜还在 → 计划和成片不一致。286 的本意应是"MIN_SHOT 夹取后补回"，而不是无条件补回。

**最小修法**：

```diff
-        dur = min(MAX_SHOT, max(MIN_SHOT, round(speech + LEAD_SEC + TAIL_SEC, 2)))
-        if dur < speech + LEAD_SEC + TAIL_SEC - 0.01:
-            dur = round(speech + LEAD_SEC + TAIL_SEC, 2)
+        need = round(speech + LEAD_SEC + TAIL_SEC, 2)
+        dur = max(MIN_SHOT, min(MAX_SHOT, need))
+        if need > MAX_SHOT:
+            LOG.warning("镜头 %s 台词 %.2fs 超 MAX_SHOT，语音尾部将被截断", shot["id"], speech)
+            shot["speech_truncated"] = round(need - MAX_SHOT, 2)
```

---

## F7 · P2 · qc_v5 的"口型硬门"永远不会执行

**位置**：`pipeline/qc_v5.py:249-253` 需要 `qc_extra["lip_sync"]`；`pipeline/render_v5.py:951-955` 只喂了 `style` + `fallbacks`

```python
# render_v5.run_qc
951  qc_extra = {"style": {...}, "fallbacks": []}
953  report = qc_v5.evaluate(video=paths["out"], plan=plan, qc_extra=qc_extra, ...)
```
```python
# qc_v5.evaluate
249  if extra.get("lip_sync"):
251      ok = bool(ls.get("ok", True))
```

**为什么错**：qc_v5 的模块 docstring 把 `lip_sync` 列为 V5 QC 的新增项，但 V5 渲染链路里没有任何一处把 `lipsync.measure_sync()` 的结果交给 `run_qc`，`checks` 里因此**永远没有 `lip_sync` 这一项**——`qc_report.json` 看起来全绿，其实嘴型这一项根本没测。典型"状态 ok 但产物可能错"。

**最小修法**（`run_render` 里 assemble 之后、`run_qc` 之前）：

```diff
+    lip = None
+    try:
+        from . import lipsync
+        scores = [lipsync.measure_sync(Path(paths["shots_dir"]) / f"{s['id']}.mp4", s.get("audio"))
+                  for s in plan["shots"] if s.get("line")]
+        vals = [x["corr_best"] for x in scores if x.get("ok") and x.get("corr_best") is not None]
+        lip = {"ok": bool(vals) and min(vals) >= 0.15,
+               "detail": f"corr_min={min(vals) if vals else None}", "samples": len(vals)}
+    except Exception as exc:
+        lip = {"ok": False, "detail": f"口型测量失败: {exc}"}
     report = qc_v5.evaluate(video=paths["out"], plan=plan,
-                            qc_extra=qc_extra, subtitles=...)
+                            qc_extra={**qc_extra, "lip_sync": lip}, subtitles=...)
```

---

## F8 · P2 · 拷贝失败只 warning，final_encode 静默使用上一轮的旧文件

**位置**：`pipeline/render_v5.py:775-782`（video_raw）、`:830-840`（narration）

```python
775  try:
777      target = Path(paths["workdir"]) / "video_raw.mp4"
778      if Path(video_raw).resolve() != target.resolve():
779          _shutil.copy2(video_raw, target)
781  except Exception as exc:
782      LOG.warning("video_raw 同步失败: %s", exc)      # ← 继续往下走
```

**为什么错**：`render_mod.final_encode` 的输入是**约定文件名** `workdir/video_raw.mp4` 和 `workdir/audio/narration.wav`（见 `render.py:934/943`）。这两处拷贝失败（磁盘满、目标被播放器占用、权限）时只用 warning 记录，然后照常编码——用**上一次渲染残留**的画面/旁白出片，probe/QC 全过，只有人眼看才发现"这一版根本没换画面"。而 `assemble` 773-774 的注释本来就写明这是历史踩过的坑，兜底却仍是"继续"。

**最小修法**：

```diff
     except Exception as exc:
-        LOG.warning("video_raw 同步失败: %s", exc)
+        raise RenderV5Error(f"video_raw 同步失败（拒绝用旧文件编码）: {exc}") from exc
```

---

## F9 · P2 · 同步度指标没有时间轴起点，Muse 的分段任务打分是错的

**位置**：`pipeline/lipsync.py:1034`（`mouth_audio_sync(clip, audio_wav=None)`）、`:1094`（`env = _audio_env(audio_wav, fps, n)` 永远从 0 开始）；调用方 `backend/app/routers/muse.py:145-149`

```python
# muse.py
145  res = lipsync.sync_clip(item["clip"], item["audio"], out, start=start, duration=duration)
149  sync = lipsync.measure_sync(out, item["audio"])     # ← 没有把 start 传下去
```
```python
# lipsync.py
1094 env = _audio_env(audio_wav, fps, n)                  # 从 wav 的 0s 开始切包络
1095 corr_zero = _pearson(motion[1:], env[: len(motion) - 1])
```

**触发条件**：`POST /api/v6/muse/sync` 带 `start`/`duration`（接口签名有，工作流参数也能传），即只对某一镜的中段做口型。

**为什么错**：`sync_clip` 的输出是从 `start` 起的片段，但 `mouth_audio_sync` 把这条片段和**整条 TTS 音频的开头**对齐。start=2.0s 时嘴动序列和语音包络整体错开 2s，`corr_best` 会掉到 0 附近甚至为负——**口型明明是对的，分数却是错的**，且错得没有边界（start 越大越离谱）。

**最小修法**：

```diff
-def _audio_env(audio_wav, fps: float, n_frames: int) -> list:
+def _audio_env(audio_wav, fps: float, n_frames: int, start: float = 0.0) -> list:
-        a = int(i * 16000.0 / fps)
-        b = int((i + 1) * 16000.0 / fps)
+        base = float(start) * 16000.0
+        a = int(base + i * 16000.0 / fps)
+        b = int(base + (i + 1) * 16000.0 / fps)
```
`mouth_audio_sync` / `measure_sync` 同步加 `start` 参数，由 `muse.py` 传入与 `sync_clip` 相同的 `start`。

---

## F10 · P3 · `except TypeError` 会把 motion2d 内部的真 TypeError 误判成"旧版 API"

**位置**：`pipeline/render_v5.py:694-703`

```python
694  res = motion2d.render_shot(layers, chars_spec, cam, dur, FPS, out_mp4, style,
696                            width=FINAL_W, height=FINAL_H, seed=(i + 1) * 31,
697                            workdir=paths["workdir"], mouth_strength=0.85)
698  except TypeError:
699      # 旧版 motion2d 不支持 mouth_strength
700      res = motion2d.render_shot(...)      # 不带 mouth_strength，整镜重新渲一遍
```

**为什么错**：TypeError 的判定只看类型，不看来源。motion2d 内部任何 `int(None)`、`float("x")`、`len(None)`（都来自数据，如自定义角色缺字段）都会被当成"签名不兼容"，于是**同一镜渲两遍**，第二遍用默认 `MOUTH_DEFAULT_STRENGTH`(0.55) 而不是 0.85 → 口型幅度悄悄变小，日志一行都看不出来；真正的 TypeError 也被永久掩埋。

**最小修法**：

```diff
-    try:
-        res = motion2d.render_shot(..., mouth_strength=0.85)
-    except TypeError:
-        res = motion2d.render_shot(...)
+    import inspect
+    kwargs = dict(width=FINAL_W, height=FINAL_H, seed=(i + 1) * 31, workdir=paths["workdir"])
+    if "mouth_strength" in inspect.signature(motion2d.render_shot).parameters:
+        kwargs["mouth_strength"] = 0.85
+    res = motion2d.render_shot(layers, chars_spec, cam, dur, FPS, out_mp4, style, **kwargs)
```

---

## F11 · P3 · `deliver` 的 `-c:a copy` 让音频活得比视频长；`--hd` 是死参数

**位置**：`pipeline/deliver.py:69-73`、`:101`

```python
63  if vertical:
64      vf = "crop=ih*9/16:ih,scale=1080:1920:flags=lanczos"
69  subprocess.run([FFMPEG, "-y", ..., "-vf", vf, "-c:v", "libx264", "-crf", "19",
72                  "-c:a", "copy", "-movflags", "+faststart", str(out)], ...)
101     res = deliver(tid, hd=True, vertical=args.vertical, force=args.force)   # args.hd 从未参与
```

**触发条件**：源 `final.mp4` 的音轨比视频轨长（`enforce_loudness` 重写音轨、或上游 xfade 少算几帧都会这样）。

**为什么错**：`-c:a copy` 只能整条复制，没有 `-shortest` 就不会按视频裁音频，`_probe(out)` 回报的是**容器**时长（= 音频时长）→ "交付规格"文件比画面多出一段尾巴，`results[].duration` 也随之偏大。`--hd` 名存实亡（`hd` 恒为 True，720p 分支不可达）。

**验证**（`tmp/audit/verify_deliver.py`，1280x720 / 视频 6.000s + 音频 6.400s 的源）：

```
--- vertical: rc=0  video: 1080 x 1920  video_dur: 6.000  audio_dur: 6.400  format_dur: 6.400
--- hd:       rc=0  video: 1920 x 1080  video_dur: 6.000  audio_dur: 6.400  format_dur: 6.400
```

存量产物也有实例（`tmp/audit/verify_outputs.py`，44 个 final 里 1 个 a-v > 0.12s）：

```
outputs\templates_3d\custom_tpl_v22_radio\final.mp4        v= 23.440 a= 23.647 fmt= 23.648 a-v=+0.207
outputs\templates_3d\custom_tpl_v46_cyber\final_9x16.mp4   v= 14.120 a= 14.141 fmt= 14.141 a-v=+0.021
outputs\templates_3d\custom_tpl_v46_robot\final.mp4        v= 13.680 a= 13.745 fmt= 13.746 a-v=+0.065
```

**最小修法**：

```diff
-                    "-pix_fmt", "yuv420p", "-c:a", "copy",
+                    "-pix_fmt", "yuv420p", "-c:a", "copy", "-shortest",
...
-            res = deliver(tid, hd=True, vertical=args.vertical, force=args.force)
+            res = deliver(tid, hd=bool(args.hd or not args.vertical), vertical=args.vertical,
+                          force=args.force)
```

---

## F12 · P3 · `mouth_sync` 的 `lead` 形参是空的，字符形状与台词位置脱钩

**位置**：`pipeline/mouth_sync.py:53-54`、`:87`

```python
53  def envelope_timeline(audio_path, fps=25.0, text="", lead=0.10) -> list[dict]:
61      frame = 1.0 / fps                                    # lead 从未被引用
87          viseme = _shape_for(chars[i % len(chars)] if chars else "a", i)
```

**为什么错**：①`lead` 出现在公开签名里，任何按 `lead=0.10` 对齐"人声起点"的调用方都会拿到"没偏移"的时间轴，且完全看不出来（`render_v5:642` 没传 `lead`，现在无害，但这正是下一个人最容易踩的坑）；②口型形状按**帧号对字符数取模**选择：4 秒 100 帧 + 10 字台词 → 同一句被循环 10 遍，嘴形和"说到第几个字"无关，与模块 docstring 声称的"用台词字符的形状序列做形状选择"不符。

**最小修法**：

```diff
+    offset = max(0, int(round(float(lead) * fps)))       # 真用上 lead
+    for i in range(max(0, n_frames - offset)):
-    for i in range(n_frames):
...
-            viseme = _shape_for(chars[i % len(chars)] if chars else "a", i)
+            ci = min(len(chars) - 1, int(i / max(1, n_frames) * len(chars))) if chars else 0
+            viseme = _shape_for(chars[ci] if chars else "a", i)
```

---

## 静默 except / 静默回退清单（按危害排序）

| 位置 | 吞掉什么 | 产物后果 | 状态是否仍 ok |
|---|---|---|---|
| render_v5.py:775-782 | video_raw 拷贝失败 | **成片用上一轮的旧画面** | 是（QC 全过）—F8 |
| render_v5.py:830-840 | narration 拷贝失败 | 成片用上一轮旧旁白 | 是 —F8 |
| render_v5.py:1043-1045 | editorial 计划失败 | 无景别/无 per-cut 转场，剪辑设计丢失 | 是 |
| render_v5.py:644-652 | 音频口型失败 → 文本 viseme | 嘴形与声音不同步（"嘴没对上"的原始症状） | 是 |
| render_v5.py:698-703 | 真 TypeError 被当签名不兼容 | 口型强度 0.85→0.55，白渲一遍 | 是 —F10 |
| render_v5.py:850-856 | 调色/包装失败 | 成片没风格化（QC 的 style 检查只看 style id，不看像素） | 是 |
| templates3d_hq.py:836-837 | 关键帧失败 → 回退 T2VA | 有台词镜**没有口型**，只有一行"回退 T2VA" | 是 —F2 同族 |
| templates3d_hq.py:761-762 | 卡片生成失败 | 成片缺片头/片尾卡 | 是 |
| templates3d_hq.py:384-391 | cv2 缺失/读图失败 → score=0 | 抽卡全按 #0 选，选卡图看起来"有分" | 是 |
| lipsync.py:867-868 | 锁目录不可写 | 直接无锁跑（GPU 并发失控） | 是 —F3 同族 |
| lipsync.py:1109-1120 | 无脸/帧不足 | `ok=True` + 空指标 | 是 —F4 |
| qc_v5.py:50-51 | blackdetect 失败 | 返回 -1 → 门**失败**（唯一站得住的一处） | 否（正确） |
| cinema3d_assemble.py:356-358 | 边界解析失败 → 简单模式 | 转场时长重新量化，帧级对齐与 episode 脱钩 | 是 |
| h3_comfy.py:455-459 | ffprobe 失败 | 返回 {} → 调用方按 0 处理 | 视调用方 |
| localize.py:68 / render.py:59 | 只按体积判"文件有效" | 截断文件被当成品复用 | 是 |

---

## 未见问题（已逐一读过，未发现值得一提的缺陷）

- **editing.py**：`_normalize_boundaries` 的夹取（`min(dur, 0.8, d/2, d/2)`）、`acc_dur` 递减、`_post_norm` 重新统一 timebase、`mix_audio_edit` 的 `adelay/atrim/apad/amix duration=longest` 组合都自洽；`expect` 与实际产物比对（±2 帧）也做了，是本仓最扎实的模块之一。唯一注意点是 `_shot_duration` 对 ≤0.05s 的镜头回退 5.0（与 render_v5 的 `or 0` 口径不同），正常数据走不到。
- **timeline.py / edit_v4.py**：帧↔秒、ASS 时间戳（`H:MM:SS.cc`）、`in/out/at` 语义（video=时长、subtitle=绝对结束）与 qc_v5 的解析一致。
- **ass_builder.py**：`ass_time`、`ass_color`（#RRGGBB→&H00BBGGRR 字节序正确）、换行/转义无越界。
- **v5_catalog.py**：`deep_merge`（dict 递归 + list 整体替换）、builtin→custom 两轮加载顺序、`load_schema` 的单复数回退（schema 目录实际就是 `style/scene/character/prop/template/pack.schema.json`）、`_SCHEMA_CACHE` 按 mtime 失效、timeline 语义校验的 at/out 口径都对。实跑 `validate_catalog()`：33 模板 / 74 场景 / 26 角色，`ok=True, errors=0, warnings=0`。
- **motion2d.py**：`mouth_plan_at` 的斜坡/闭口插帧、`_char_placement` 的仿射（`spr_w/spr_h` 均 ≥8，无除零）、`render_shot` 的 duration/fps 前置校验（`duration<=0`、`fps>120` 直接抛）都稳。
- **qc_v5.py / qc_v3.py**：`blackdetect` 失败→-1→判失败（保守正确）、duration 容差 0.8s、字幕覆盖按 `_edit_start` 窗口对齐——除 F7 的入口缺失外未见问题。
- **h3_comfy.py**：`_snap_frames_up`（17k+5 向上取整）、`_to_h3_audio` 的 mtime 新鲜度判定、`_parse_last_json` 自尾向首扫描都对。
- **v5_catalog + v5_cli**：`validate_item` 的 partial 覆盖先 `deep_merge` 到基线再校验，避免"只写 voice 就报缺 style"的误报，设计正确。
