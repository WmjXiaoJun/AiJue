# pipeline / 周边 第二轮审查（含回归）

- 审查人：review-pipeline（只读；未改任何源码）
- 范围 A：integrations/、imagegen/、localize/、publish/、studio2d/、scripts/
- 范围 B：render_v5.py、templates3d_hq.py、lipsync.py、mouth_sync.py、muse_video.py、deliver.py、utils.py 的本次改动
- 探针产物：`tmp/audit2/`（`verify_round2.py` / `verify_trim.py`）；第一轮探针仍在 `tmp/audit/`
- 已排除的已知项：SyncNet 权重损坏、templates_2d/templates_3d 双份成片、无鉴权、frontend dead code

复现：

```powershell
python tmp/audit2/verify_round2.py   # R2（关键帧 force/prompt_sig）、deliver -shortest
python tmp/audit2/verify_trim.py     # R1（trim 后音频/视频漂移）
```

---

## 0. 先回答你点名的问题：旧 meta 缺 `prompt_sig` 会不会每次重跑都重生成？

**不会重生成，成本是 0——但结果更糟：它会把旧图重新登记成"新 prompt 的结果"。**

`_generate_keyframe` 的新缓存判定（`templates3d_hq.py:514-527`）本身是对的：旧 meta 没有 `prompt_sig` →
`str(cached_meta.get("prompt_sig") or "") == prompt_sig` 为 False → 判定失效 → 进入生成分支。**但生成分支里的
`_render()` 仍保留着旧的体积短路**（`templates3d_hq.py:530-532`）：

```python
def _render(seed_value: int, raw_dir: pathlib.Path, dest: pathlib.Path) -> int:
    if dest.is_file() and dest.stat().st_size > 100_000:
        return dest.stat().st_size          # ← 早于 imagegen 调用，force 管不到这里
```

于是 `_render` 直接返回旧 PNG 的体积，**一次 imagegen 都没调**，随后 588-604 行把新 `prompt_sig` / `base_seed`
写进 meta。实测（`tmp/audit2/verify_round2.py` A 段，隔离 KEYFRAMES + 打桩，统计 subprocess 调用次数）：

```
returned: ...\tmp\audit2\kf\tmpl\jp_anime_animaginexl4_ip50\s001.png
subprocess calls during force-render: 0 (0 = 关键帧没有被重画)
meta now has prompt_sig: ceb19141 base_seed: 123
png bytes unchanged: 120002
```

即 `_generate_keyframe(..., force=True)` **既没重画，又把"新 prompt 已生效"写进了缓存**。之后同一 prompt 再跑就稳定命中
缓存、永远吐旧图 —— 这比"每次都重生成"贵得多（错误是静默且不可自愈的）。详见 R2。

---

## 1. 本轮结论速览

| # | 严重度 | 位置 | 一句话 |
|---|---|---|---|
| R1 | **P1** | render_v5.py:354-389 | trim 改成"缩 b-roll 时长"，但发生在音频已按旧时长建好之后 → 之后的台词整体后移、片尾被切 |
| R2 | **P1** | templates3d_hq.py:530-532 | `_render` 的体积短路让 `--force` 和 `prompt_sig` 双双失效，并把旧图写成新签名（缓存毒化） |
| R3 | **P2** | render_v5.py:964-976 | 新增的 `lip_sync` QC 门是恒真的（只判 `speech_duration > 0.2`），报告显示"口型通过"但没测任何同步 |
| R4 | **P2** | run_h3_ref.py:100-104 / h3_dynamic_probe.py:72-73 | ComfyUI input 中继按 basename 落地：跨模板 `s001.wav`/`s001.png` 互相覆盖，配合 prompt 哈希会粘滞 |
| R5 | **P3** | mouth_sync.py:81-95 | `voiced_pos` 永远查不到（只在低能量分支查，而该分支的帧不在 map 里）→ 形状仍恒取 `chars[0]` |
| R6 | **P3** | run_h3_ref.py:69 | `--timeout` 是死参数：`run_and_fetch` 内部硬编码 900s，调用方传 1200/1800 无效 |
| R7 | **P3** | lipsync.py:1156-1164 | docstring 仍称"SyncNet 可用时走 LSE"；`ok` 判据里的 `valid` 混入了大量 0.0 帧（首帧/丢脸后） |
| R8 | **P3** | utils.py:136-144 | `read_json` 的 gbk 兜底会让"UTF-8 损坏"静默变成乱码字符串（不抛错） |
| R9 | **P3** | scripts/gen_relic_template.py:13 | `REPO = Path(".").resolve()`：写入路径随 cwd 变，在 `pipeline/` 下跑会静默生成 `pipeline/contract/...` |
| R10 | **P3** | localize/translator.py:57-61 | `NAME_MAP` 无条件罗马化，`ja`/`zh` 目标也被替换成 Zhou/Ali/Dawei |
| R11 | **P3** | h3_dynamic_probe.py:85-95 | 硬编码节点 id `"90"`/`"91"` 直接写进 prompt_api：覆盖同名节点或留下孤儿 Audio 节点 |
| R12 | **P3** | run_h3.py:392-398 | 超时路径无差别 `/interrupt`，且不识别"任务已被取消/清队列"，可能打断别人的活 |

---

## R1 · P1 · trim 缩时长后音频还是旧的：后续台词整体后移、片尾被切

**位置**：`pipeline/render_v5.py:354-389`（新 `_trim_to_target`）、配合 `:306-320`（音频在建）

```python
306  total += dur
307  # 每镜音轨（digest 含 voice_key：改声音后强制重混）
308  out_wav = audio_dir / f"{shot['id']}.wav"
309  digest = utils.sha1_text("v5-audio", shot["id"], dur, ...)
313  if not force and out_wav.is_file() and m.get("hash") == digest:
314      shot["audio"] = str(out_wav); continue
316  _build_shot_audio(out_wav, src_voice, dur, workdir)     # ← 按【未 trim 的 dur】建 wav
...
354  def _trim_to_target(plan, target):
363      for s in shots:
364          if s.get("type") == "broll" and float(s.get("duration") or 0) > 1.2:
366              cut = min(float(s["duration"]) - 1.2, max(0.2, over))
368              s["duration"] = round(float(s["duration"]) - cut, 2)   # ← 只改时长，不重建 wav
```

**触发条件**：`sum(shot.duration) > max_total`（target ≤60 时 `max_total = max(target, 45)`）。V5 是语音驱动时长，
实际总长经常冲到 45s+，只要目标 40s 就必然进这个分支（且 `len(shots) > 3` 恒成立）。

**为什么错**：`shot["duration"]` 是视频/字幕的唯一真源（`render_shot_clips` 用它渲、`assemble` 用它算
`_edit_start` 和字幕窗口），但音轨文件已经按旧时长落盘。没有 J/L cut 时（V5 常态）narration 走
`render_mod.concat_audio`（`render_v5.py:841-842`）：**纯拼接 + 末尾裁到 total**。于是

- 被缩的那一镜，其静音 wav 比画面长 Δ；
- Δ 之后的所有台词在音轨里整体后移 Δ（字幕/画面不动）→ 逐镜越到后面越不同步；
- 音轨总长比成片长 Δsum，末尾 Δsum 秒被 `-t` 直接切掉 → 最后一句容易被削头去尾。

实测（`tmp/audit2/verify_trim.py`，6 镜 / 目标 40s）：

```
before trim: total = 48.2
trimmed(deleted)=[]  after trim: sum(shot)=44.8s  sum(wav)=48.2s  drift=+3.400s
episode.total_duration after trim = 48.199999999999996 (应为 44.8 )
  s001  dur= 8.60  wav=12.00        ← 缩了 3.4s，wav 还是 12s
  s002  dur=12.00  wav=12.00
  s003  dur=12.00  wav=12.00
fallback concat_audio() dur = 44.8 | 视频目标 = 44.8
```

附带第二个小问题：`384 if trimmed:` 只在"删镜"时更新 `episode.total_duration`；纯缩时长时不更新（实测仍是 48.2），
于是 `episode.json` / `manifest.expected_duration` 与成片不符（`assemble` 自己会重算，所以只是证据链脏，不影响成片）。

**最小修法**（二选一，都不要让"已建好的音频"和"时长"脱钩）：

```diff
 # 方案 A（推荐）：先把时长定稿，再建音频 —— 在 tts_and_pace 里把 trim 拆成两步
     plan["episode"]["total_duration"] = round(total, 3)
+    # 1) 只算时长（dry-run），确定每镜最终 duration
+    _trim_to_target(plan, target, apply_audio=False)
+    # 2) 按最终 duration 建/校验音频（digest 天然失效 → 自动重混）
     ...
 # 方案 B：trim 后对被动过的镜头重建 wav
    if shrunk:
+       for s in shots:
+           if s.get("audio") and float(s.get("duration") or 0) < _wav_dur(s):
+               _build_shot_audio(Path(s["audio"]), _voice_of(s), float(s["duration"]), workdir)
```

并修正 `if trimmed:` → 无论是缩还是删，都重算 `plan["episode"]["total_duration"]`。

---

## R2 · P1 · `--force` 与 `prompt_sig` 被 `_render` 的体积短路废掉，并把旧图登记成新签名

**位置**：`pipeline/templates3d_hq.py:530-532`（短路）、`:514-527`（新缓存判定）、`:588-604`（meta 落盘）

**触发条件**：关键帧 PNG 已存在（>100KB）+ 任意一次需要重画的场景：改 `prompt`/`semantic_visual`、改 `--ip-strength`、
换 `--keyframe-candidates`、或显式传 `--force`。

**为什么错**：两层判定错位——外层（514）已经承认"缓存失效，需要重画"，内层（531）又用"文件存在"把生成拦住。
后果两极：

1. `--force` 对关键帧**完全无效**（实测 0 次 imagegen 调用）。第六轮想靠 `--force` 重抽卡/换 seed 是做不到的；
2. `prompt_sig` 被**毒化**：旧图 + 新签名落进 meta（实测 `prompt_sig=ceb19141`），下一次同 prompt 直接命中缓存。
   下游 `gen_ref2va` 拿到的是"新 prompt 名义下"的旧首帧 → H3 用错误的脸/构图演全套，日志一行看不出来。

另外 `_render` 对候选图（`candidates/{shot}_c{k}.png`）是同一套短路，所以 `candidates>1` 抽卡的"重抽"也不可能。

**最小修法**：把 `force` 一路传进 `_render`，且只在"缓存命中"时才允许复用：

```diff
-    def _render(seed_value: int, raw_dir: pathlib.Path, dest: pathlib.Path) -> int:
-        if dest.is_file() and dest.stat().st_size > 100_000:
+    def _render(seed_value: int, raw_dir: pathlib.Path, dest: pathlib.Path,
+                force: bool = False) -> int:
+        if (not force) and dest.is_file() and dest.stat().st_size > 100_000:
             return dest.stat().st_size
...
-        size = _render(seed, KEYFRAMES / tid / f"{style_key}_{ckpt_tag}" / f"raw_{shot_id}", key_png)
+        size = _render(seed, ..., key_png, force=force)
...
-            size = _render(seed_k, raw_dir_k, cand_png)
+            size = _render(seed_k, raw_dir_k, cand_png, force=force)
```

再加一条保险：`prompt_sig` 写盘前校验 PNG 的 mtime 晚于本次开始时间，否则视为"未生成"直接报错。
（顺带：`gen_t2va`/`gen_ref2va` 的 200KB 短路仍未接 `force`，与第一轮 F2 同源，建议一并清理。）

---

## R3 · P2 · 新增的 `lip_sync` QC 门是恒真的

**位置**：`pipeline/render_v5.py:964-976`

```python
966  dialogue = [s for s in plan.get("shots") or [] if str(s.get("line") or "").strip()]
967  no_speech = [str(s.get("id")) for s in dialogue
968               if float(s.get("speech_duration") or 0) <= 0.2]
969  lip_ok = not no_speech
973  "lip_sync": {"ok": lip_ok, "provider": "viseme", ...}
```

**为什么错**：`speech_duration` 由 `tts_and_pace` 从 TTS 缓存时长写入，任何有台词的镜头都 ≈ TTS 时长（远大于 0.2s），
只有"TTS 没合成"时才会为 0——而那种情况 283 行已经 `raise RenderV5Error("配音缺失")` 了。所以这一项**永远是 True**，
`qc_report.json` 里却会多出一条"口型通过"，读报告的人会以为嘴型被校验过。实际驱动嘴型的是
`mouth_sync.envelope_timeline`，而它算出来的时间轴从没被复核过（哪怕 `coverage_ratio` 已经现成）。

**最小修法**（门要落在"能证伪"的量上）：

```diff
+    from . import mouth_sync
+    bad = []
+    for s in dialogue:
+        wav = Path(paths["workdir"]) / "audio" / f"{s['id']}.wav"
+        try:
+            tl = mouth_sync.envelope_timeline(wav, FPS, str(s.get("line") or ""))
+        except Exception as exc:
+            bad.append({"shot": s["id"], "error": str(exc)[:80]}); continue
+        cov = mouth_sync.coverage_ratio(tl, LEAD_SEC, LEAD_SEC + float(s["speech_duration"] or 0))
+        if cov < 0.25:                      # 说话区间里"张嘴"帧不到 25% → 口型几乎不动
+            bad.append({"shot": s["id"], "coverage": round(cov, 3)})
     qc_extra = {..., "lip_sync": {"ok": not bad, "provider": "envelope",
-                                "detail": ...}}
+                                "detail": f"{len(dialogue)-len(bad)}/{len(dialogue)} 镜有口型运动",
+                                "bad": bad}}
```

---

## R4 · P2 · ComfyUI 中继按 basename 落盘：跨模板串音、串首帧

**位置**：`integrations/comfyui/run_h3_ref.py:100-104`、`integrations/comfyui/tools/h3_dynamic_probe.py:70-73`（`--image` 同理）

```python
100  relay_dir = Path(COMFY) / "input" / "zaopianju_dynamic"
102  dst = relay_dir / Path(wav).name          # ← 只有 basename：s001.wav
103  shutil.copyfile(wav, dst)
104  audio_rel = "zaopianju_dynamic/%s" % Path(wav).name
```
```python
 72  img_name = os.path.basename(ref_image)    # ← s001.png
 73  shutil.copyfile(ref_image, os.path.join(relay, img_name))
```

**触发条件**：任意两个模板同时/交错跑 H3（季生产 + 手动补渲、两个 API 任务、CLI 与 agent 并行）。所有模板的镜头 id
都是 `s001/s002/…`，中继名完全相同。

**为什么错**：ComfyUI 的 `input/` 是**全局命名空间**，执行时才读文件。A 任务已提交、B 任务把 `s001.wav` 覆盖成另一条台词，
A 执行时读到 B 的音频 → 生成的口型对的是别人的话，而两边都"成功"。首帧更严重：A 的 `s001.png` 被 B 的立绘覆盖，
出来的镜头是**另一个角色**。叠加 ComfyUI 按 prompt JSON 哈希缓存执行的特性（prompt 里只写相对路径名），
同 id、同 duration、同 seed 的两个模板会直接命中上一模板的缓存产物，重跑也不会变。

**最小修法**：中继名带上源路径短哈希（幂等，不破坏缓存复用）：

```diff
+import hashlib
+def _relay_name(p) -> str:
+    h = hashlib.sha1(str(Path(p).resolve()).encode("utf-8")).hexdigest()[:8]
+    return f"{h}_{Path(p).name}"
-    dst = relay_dir / Path(wav).name
+    dst = relay_dir / _relay_name(wav)
-    audio_rel = "zaopianju_dynamic/%s" % Path(wav).name
+    audio_rel = "zaopianju_dynamic/%s" % dst.name
```
`prepare_prompt` 里的 `img_name` 同样处理。

---

## R5 · P3 · `voiced_pos` 永远命中不到，字符映射还是"恒取第一个字"

**位置**：`pipeline/mouth_sync.py:81-95`

```python
81  voiced = [i for i in range(n_frames) if float(smooth[i]) >= 0.30]
83  voiced_pos = {idx: pos for pos, idx in enumerate(voiced)}
...
91  elif level < 0.30:                     # ← 只有低能量帧进这里
93      pos = voiced_pos.get(i, 0)         # ← 而 i 不在 voiced 里（它 <0.30）→ 恒为 0
94      ci = min(len(chars) - 1, int(pos * len(chars) / voiced_count)) if chars else 0
95      viseme = _shape_for(chars[ci] if chars else "a", i)
```

**为什么错**：查表和取值在同一条件下互斥——`voiced_pos` 的 key 全是 `smooth>=0.30` 的帧，而查表发生在 `smooth<0.30` 的帧上，
`pos` 恒为 0 → `ci` 恒为 0 → 形状永远来自 `chars[0]`（只靠 `_shape_for(char, i)` 里的 `i` 抖动，与"说到第几个字"无关）。
模块 docstring 承诺的"按台词位置取字"依然没实现。

**最小修法**（预先算好每帧的"发声进度"，不要查表）：

```diff
-    voiced_pos = {idx: pos for pos, idx in enumerate(voiced)}
+    progress = [0.0] * n_frames
+    seen = 0
+    for i in range(n_frames):
+        progress[i] = seen / voiced_count
+        if i in voiced_index_set:      # set(voiced)
+            seen += 1
...
-            pos = voiced_pos.get(i, 0)
-            ci = min(len(chars) - 1, int(pos * len(chars) / voiced_count)) if chars else 0
+            ci = min(len(chars) - 1, int(progress[i] * len(chars))) if chars else 0
```

---

## R6 · P3 · `run_h3_ref.py` 的 `--timeout` 是死参数（真实预算 900s）

**位置**：`integrations/comfyui/run_h3_ref.py:69` 声明；`tools/h3_dynamic_probe.py:108-111` 硬编码

```python
# run_h3_ref.py
 69  ap.add_argument("--timeout", type=int, default=1800)
...
126  res = run_and_fetch(Path(args.out).stem, api, out)     # ← args.timeout 没传
# h3_dynamic_probe.py
110  pid = submit(SERVER, prompt_api, log)
111  rec = wait_prompt(SERVER, pid, 900, log)                # ← 常量
```

**为什么错**：`pipeline/templates3d_hq.py:721` 明确传 `--timeout 1200`，`run_h3_ref.py` 的 help 写"总秒数"，但真正的上限
永远是 900s。H3 在显存被占/首帧 1024×576 需要重绘时会超过 15 分钟 → 任务在调用方以为还有 5 分钟时被 `/interrupt` 打断，
表现为"莫名其妙的 TimeoutError"。

**最小修法**：`run_and_fetch(tag, api, out, timeout=900)` 增参并透传 `wait_prompt(SERVER, pid, timeout, log)`，
`run_h3_ref.main` 里传 `args.timeout`。

---

## R7 · P3 · `measure_sync` 的文档与 `ok` 判据仍会误导

**位置**：`pipeline/lipsync.py:1154-1164`（docstring/字段）、`:1114-1119`（ok）

```python
1156  """口型同步度：SyncNet 权重可用时走 LSE 打分，否则退回嘴动-语音相关代理指标。"""
1163  # 注意：SyncNet 的 LSE-C/LSE-D 推理尚未接入；权重可读也不能冒充 syncnet 分数
1164  result["provider"] = "mouth_audio_corr"
1114  valid = int(np.isfinite(np.asarray(motion, dtype=float)).sum())
1119  ok = bool(valid >= 8 and detected > 0)
```

**为什么错**：①`provider` 已经诚实了，但 docstring 第一句还在承诺 LSE，读代码的人会以为 `syncnet.ready` 时会有 LSE-C/LSE-D；
②`valid` 统计的是"有限值"的帧，而 `motion[i]` 在**首帧**和**每次丢脸后重锁的第一帧**都会 append `0.0`（有限值），
所以"75 帧里只有 1 帧检到脸"也可能 `valid≈74`、`detected=1` → `ok=True`，随后 `corr_best` 是拿一条几乎全 0 的曲线算出来的。
建议 `valid` 改成"参与计算的帧里 motion 的方差 > 阈值"，或直接要求 `face_ratio >= 0.3`。

**最小修法**：

```diff
-    """口型同步度：SyncNet 权重可用时走 LSE 打分，否则退回嘴动-语音相关代理指标。"""
+    """口型同步度：当前实现只给「嘴动 ↔ 语音包络」代理指标（SyncNet LSE 未接入）。
+    provider 恒为 mouth_audio_corr；syncnet.ready 只表示权重文件可读。"""
-    ok = bool(valid >= 8 and detected > 0)
+    ok = bool(valid >= 8 and detected >= 4 and detected / max(n, 1) >= 0.30)
```

---

## R8 · P3 · `read_json` 的 gbk 兜底把"损坏"静默变成"乱码"

**位置**：`pipeline/utils.py:136-144`

```python
138  for enc in ("utf-8-sig", "utf-8", "gbk"):
139      try:
140          with open(path, "r", encoding=enc) as fh:
141              return json.load(fh)          # ← gbk 几乎能解码任意字节
142      except (OSError, json.JSONDecodeError, UnicodeDecodeError):
143          continue
144  return default
```

**触发条件**：任何一个 UTF-8 JSON 被截断在**多字节字符中间**（中断的写盘、同步盘冲突副本、手工编辑没存完整）。
UTF-8 解码抛 `UnicodeDecodeError` → 落到 `gbk` → GBK 对任意 0x80-0xFF 字节基本都能解 → 若截断不在语法关键位置，
`json.load` 会成功 → 返回**乱码字符串**（角色名/台词）而不是报错。第一轮已指出"静默吞错"的一般性问题，
这里是具体化：gbk 这一档是唯一会把"解码失败"变成"成功但错"的路径。

**最小修法**：gbk 只在文件里**没有**合法 UTF-8 且调用方显式要求时启用，或至少记录一条 warning：

```diff
-    for enc in ("utf-8-sig", "utf-8", "gbk"):
+    for enc in ("utf-8-sig", "utf-8", "gbk"):
         try:
             with open(path, "r", encoding=enc) as fh:
-                return json.load(fh)
+                data = json.load(fh)
+            if enc == "gbk":
+                LOG.warning("read_json: %s 不是合法 UTF-8，已按 GBK 解码（可能有乱码）", path)
+            return data
         except (OSError, json.JSONDecodeError, UnicodeDecodeError):
             continue
+    LOG.warning("read_json: 无法解析 %s，返回默认值 %r", path, default)
     return default
```

---

## R9 · P3 · 两个脚本用 cwd 当仓库根

**位置**：`scripts/gen_relic_template.py:13`、`scripts/gen_agent_workflows.py:7`

```python
REPO = Path(".").resolve()
OUT = REPO / "contract" / "v5" / "templates" / "relic_talk.json"
```

**为什么错**：其余 30 个脚本都用 `Path(__file__).resolve().parents[1]`。在 `pipeline/` 或任意子目录下执行
`python ../scripts/gen_relic_template.py`：不报错，但会在**当前目录**下新建 `contract/v5/templates/relic_talk.json`
（一份没人读的假契约），真契约不变 → "改了数据却没生效"。`gen_agent_workflows.py` 因为 `WORKFLOW_DIR` 来自包内绝对路径，
只影响 `sys.path`（会 import 失败，属于显性错误），但仍应统一。

**最小修法**：

```diff
-REPO = Path(".").resolve()
+REPO = Path(__file__).resolve().parents[1]
```
（`gen_agent_workflows.py` 同改；顺手把生成结果过一遍 `v5_catalog.validate_item` 再落盘。）

---

## R10 · P3 · `NAME_MAP` 对 `ja/zh` 目标也生效

**位置**：`localize/translator.py:57-61`（`prepare_source`）、`:44-45`（`CJK_KEEP`）

```python
44  CJK_LEFT = re.compile(r"[\u4e00-\u9fff]")
45  CJK_KEEP = {"ja", "zh"}
48  NAME_MAP = {"老周": "Zhou", "阿栗": "Ali", "大魏": "Dawei"}
57  def prepare_source(text: str) -> str:
58      out = text or ""
59      for zh, ro in NAME_MAP.items():
60          out = out.replace(zh, ro)
```

**为什么错**：罗马化是为英/西/葡等拉丁语种准备的（避免音译漂移），但 `prepare_source` 不看目标语种。
日语版会得到 "Zhou が言いました"，中文重写版会把"老周"变成 "Zhou"——而 `CJK_KEEP` 明明已经把 ja/zh 划成"保留汉字"。

**最小修法**：

```diff
-def prepare_source(text: str) -> str:
+def prepare_source(text: str, target_lang: str = "") -> str:
+    if target_lang in CJK_KEEP:
+        return text or ""
    out = text or ""
```
调用处把 `target_lang` 传进来。

---

## R11 · P3 · H3 工作流里硬编码节点 id `"90"`/`"91"`

**位置**：`integrations/comfyui/tools/h3_dynamic_probe.py:85-96`

```python
 85  audio_nid = "90"
 86  prompt_api[audio_nid] = {"class_type": "LoadAudio", "inputs": {"audio": audio_rel}}
 87  h3["inputs"]["ref_audios.ref_audio_0"] = [audio_nid, 0]
...
 94  last_nid = "91"
 95  prompt_api[last_nid] = {"class_type": "LoadImage", "inputs": {"image": last_rel}}
```

**为什么错**：`ui_to_api` 产出的 id 是 ComfyUI 的字符串数字（"1".."57"…）。写死 "90"/"91" 目前恰好不冲突，
但只要模板里节点数增长到 90+，或有人复制粘贴带上这两个 id，就会**静默覆盖**既有节点（被覆盖的节点可能是采样器/解码器），
表现为"出图但构图/参考图不对"。反之若模板自带 LoadAudio，则会多出一个孤儿节点（无害但污染图）。

**最小修法**：生成一个保证不冲突的 id：

```diff
-            audio_nid = "90"
+            audio_nid = str(max([int(k) for k in prompt_api if str(k).isdigit()] or [0]) + 1)
```
`last_nid` 用 `audio_nid` 递增，避免两次挑到同一个号。

---

## R12 · P3 · `wait_prompt` 超时会无差别 `/interrupt`，且不认"已取消"

**位置**：`integrations/comfyui/run_h3.py:361-402`

```python
392  if elapsed > timeout:
393      log("[poll] 超时 %s，尝试 /interrupt" % fmt_sec(elapsed))
395          http_post_json(base + "/interrupt", {}, timeout=10)
398      raise TimeoutError(...)
```

**为什么错**：①`/interrupt` 打断的是**当前正在跑的那个任务**，不一定是我们这个 `prompt_id`（我们的可能还在 pending，或者早被
别人清了队列）——超时就无脑 interrupt，等于顺手杀掉别人的渲染。②如果任务被用户在 UI 上取消/删队列，
`/history/<pid>` 永远不会出现该 id，循环只能干等到 900s 才报"超时"，日志里全是 `state=unknown`，排查时看不到真实原因。

**最小修法**：

```diff
-        if elapsed > timeout:
+        if state == "unknown" and elapsed > 120:
+            q = http_json(base + "/queue", timeout=15) or {}
+            if prompt_id not in [it[1] for it in (q.get("queue_running") or [])
+                                 + (q.get("queue_pending") or [])]:
+                raise RuntimeError(f"任务已不在队列（被取消/清空）: {prompt_id}")
+        if elapsed > timeout:
             log("[poll] 超时 %s，尝试 /interrupt" % fmt_sec(elapsed))
-            try:
-                http_post_json(base + "/interrupt", {}, timeout=10)
-            except Exception:
-                pass
+            if prompt_id in running:          # 只有确实在跑的是我们才打断
+                try:
+                    http_post_json(base + "/interrupt", {}, timeout=10)
+                except Exception:
+                    pass
```

---

## 2. 上一轮 12 条的回归核对

| 上轮 | 位置 | 状态 |
|---|---|---|
| F1 `style` 缩进 → UnboundLocalError | render_v5.py:1052 | **已修**（移出 if，通过） |
| F2 缓存不含 prompt/seed/时长 | templates3d_hq.py:514-527 | 部分修：关键帧加了 `prompt_sig+base_seed`，但被 R2 短路废掉；`gen_t2va/gen_ref2va` 仍无 key |
| F3 锁抢活人 | lipsync.py:851-864, 873-876 | **已修**（死主优先、stale 提到 6h、release 校验 owner）；残余：Windows `_pid_alive` 跨用户可能误判"死" |
| F4 无脸 ok=True / NaN | lipsync.py:1114-1132 | **已修**（`ok` 加 `detected>0`，`motion_mean` 为 None）；见 R7 的两点残余 |
| F5 provider 冒充 syncnet | lipsync.py:1164 | **已修**（恒为 mouth_audio_corr）；docstring 残留 → R7 |
| F6 `MAX_SHOT` 死代码 | render_v5.py:286-292 | **已改语义**（不再截断，记 `over_max_sec`+warning）；正确，但把压力转移给了 trim → R1 |
| F7 `lip_sync` 门缺失 | render_v5.py:964-976 | 已接上，但恒真 → R3 |
| F8 video_raw/narration 静默用旧文件 | render_v5.py:789-797, 847-855 | **已修**（改 raise） |
| F9 同步指标无 start | lipsync.py:999-1023,1154-1161 + muse.py:164 + registry.py:443 | **已修**（两侧调用点都传了 `start`） |
| F10 `except TypeError` | render_v5.py:710-715 | **已修**（改 `inspect.signature`） |
| F11 deliver `-shortest` | deliver.py:75-81 | **已修**（实测 video 5.000 / audio 4.992 / fmt 5.000）；另加 aac 兜底与 `--sd` |
| F12 mouth_sync `lead`/字符映射 | mouth_sync.py:80, 93-95 | `lead` 已生效（前 N 帧闭口）；字符映射仍不成立 → R5 |

---

## 3. 范围 A 未见问题（已读，未发现值得报的缺陷）

- **integrations/comfyui/run_h3.py**：`submit/wait_prompt/collect_outputs` 的错误分支都把 `status_str=error` 转成异常，
  超时/失败不会伪装成功；`probe_video`/`fmt_sec` 无越界。除 R12 的 interrupt 策略外无问题。
- **integrations/comfyui/workflow_to_api.py**：`ui_to_api` 的 widget↔input 映射与 `validate_workflow` 只做提示不阻断，
  设计上是"让 ComfyUI 自己报错"，可接受；未见静默改数据的逻辑。
- **integrations/metaso/h3.py**（153 行）：纯 HTTP 适配器，超时/状态码都显式判断。
- **imagegen/cli.py**：`--mode scene` 时 `extra_seed=0` → 实际 seed == 传入 seed（与 templates3d_hq 的 `base_seed` 一致）；
  `--size` 强制 8 的倍数；`prefix = {subfolder}/{spec_id}_{tag}` 只作为 ComfyUI 文件名，产物按 `rec` 取，不会串图。
- **imagegen/sheet.py / prompt.py / workflow.py**：拼图与提示词拼装无除零/越界（`max(1, ...)` 都在位）。
- **localize/cli + terms.py**：术语表查找与 CLI 参数校验正常（除 R10）。
- **publish/cli.py + upload_post.py**：**幂等策略是对的**——"结果不确定（超时/5xx）返回 `request_id` 供人工确认，不自动重试"，
  4xx 明确拒绝不带 `request_id`；平台/话题去重、`MAX_UPLOAD_MB`/TikTok 时长上限都做了前置校验。未见问题。
- **studio2d/**：只有一处除法（`shapekit.py:328`，分母是常量缩放），未见除零/越界；`render.py` 的帧循环有 max(1,…) 保护。
- **scripts/**：除 R9 两处 cwd 依赖外，其余 30 个脚本都用 `Path(__file__).resolve().parents[1]`；`fix_loudness.py` 的
  幂等做得比较扎实（有渲染锁检测、`.preloud` 备份、重建到 `final.rebuild.mp4` 再替换）。
