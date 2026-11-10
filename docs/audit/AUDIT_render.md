# 渲染管线审查

第三轮（渲染管线核心）· 审查对象：V5 渲染栈（`render_v5.py` / `scene_render.py` / `motion2d.py`）与 HQ 短片栈（`templates3d_hq.py`）。
本轮定位＝**对前两轮（F1..F12 / R1..R12）逐条回归核对 + 只报新发现**，并按 Lead 给的三条实测事实做复核与影响链判定。

> 说明：`pipeline/muse_video.py` 在本轮审查期间被并发修改（617→630→691 行）。该文件结论以**函数名 + 本轮核验时的行号**为准，已在条目中标注。其余文件在审查窗口内 mtime 稳定。

---

## 概览

### 0.1 结论速览

| 级别 | 条数 | 一句话 |
|---|---|---|
| **P0** | 1 | `tts_breeze` 输出文件名只含 batch 下标 → 改一句台词重跑，会**覆盖别的台词的音频**，`voice_cache`（key＝台词原文）命中一条指向被覆盖文件的记录 → 成片出现「字幕是 A、声音是 A2」且时长/截断全错（静态读码 + 伪造 HTTP 层实证 5324B→5804B）。 |
| **P1** | 9 | HQ 栈两处缓存（T2VA/ref2va 只判体积、关键帧 `_render` 体积短路毒化）；**HQ 语义 brief 与 assemble 的 episode id 空间分裂（86–92% 镜头对不上 + 13 份文件重复 id）→ 已实证为 assemble 硬失败**；HQ 无 per-shot meta（绕开 `h3_comfy` 证据链）；`scene_plates` 全缺（V3/V4 硬失败 + 16/16 校验红）；`smooth_noise` 全尺寸缓存 ~350MB；`lipsync` 整段解码 ~1GB；`muse_video.available()` 常量返回 + `shell=True` 无 timeout；`--duration` 不是下限（7.8s vs 30s）。 |
| **P2** | 19 | 时长/字幕/边界/资源/降级类：trim 与回退路径的时长不一致、`force` 不传给 H3 缓存、关键帧失败静默换栈、`voice_key` 不含 `ref_text`、`set_character_voice` 覆盖写、BGM preset 静默兜底、`_meta_matches` 漏采样参数、`_get_layerset` 键漏 duration/seed、`evaluate_hq_qc` 只看 5 项…… |

已核对无问题的 11 组点见 §4（含两条**被我实测证伪**的怀疑：`qc.py` 的 `-show_entries` 与 `blackdetect` 计数——都不是缺陷，不要改）。

### 0.2 覆盖度（诚实声明）

| 文件 | 读法 |
|---|---|
| `render_v5.py`(1341) `templates3d_hq.py`(1013) `ass_builder.py`(246) `qc.py`(129) `qc_v5.py`(289) `mouth_sync.py`(121) `tts_engine.py`(121) `tts_breeze.py`(409) `v5_voices.py`(291) `v5_story.py`(255) `v5_audio.py`(421) `h3_v5.py`(197) `postfx.py`(48) | **全文精读** |
| `lipsync.py`(1224) `motion2d.py`(3597) `scene_render.py`(4241) `h3_comfy.py`(774) `muse_video.py`(691) `v5_scene_art.py`(754) `templates3d.py`(651) `qc_v3.py`(556) `variety_subs.py` `agents/registry.py` | **定点精读**（ffmpeg/缓存/时长/证据链相关函数全文；绘图/网络适配等无关段略过） |
| `v5_art.py`(1960) `cinema3d_assemble.py`(1437) `cinema3d.py` `cinema3d_local.py` `templates3d_pipeline.py` `templates3d_semantic.py` `templates3d_relip.py` | **扫读 + 关键函数**；`v5_art`/`cinema3d_assemble` 只读了信号最强的段落，**不宣称无缺陷** |
| `scene_render.py` 的 60+ 个 `p_*` 绘图原语（约 3000 行） | 只扫了 `smooth_noise`/字体/plate 相关调用点；**原语内部未逐条审** |

本轮的验证分三类：① 我做的**静态读码 + 纯函数/ffprobe/伪造 HTTP 层**实验（`docs/audit/_tools/v*.py`）；② Lead 在临时 `ZAOPIANJU_OUTPUTS_DIR` 里做的**真跑 assemble**（P1-3 硬失败、P2-1 的 20.65s 时长差，报告中标为"实证（Lead 真跑）"）；③ 未跑真渲染 / 未起 Breeze 服务 / 未占 GPU。

### 0.3 Lead 三条实测事实：复核结论

| Lead 的事实 | 复核结果 | 判定 |
|---|---|---|
| `*_semantic.json` 是 `briefs_for()` 唯一输入，65–93 镜 vs acts 3–6 幕，`utils.shots` 与 acts 脱钩 | **成立**。30 份 semantic 文件，shorts **63–93 镜**，对应模板 acts **4–9 幕**（无 3 幕）。`templates3d_hq.main()` 只吃 `briefs_for()`（L912-913），**完全不读 `episode.json`**；而 `t3.assemble()` 只读 `episode.json`（`templates3d.py:510`）→ 两条链在 id 上会合，见 P1-3。 | 成立，升级为 **P1-3** |
| shot id 重复（`tonight_show` 的 `v5_e001` ×9），根因 `scripts/expand_3d_shots.py` 的 `added` 每次从 0 起 | **成立**。30 份里 **13 份**有重复 id；`v5_e001` ×9 出现在 `history_mystery` / `roundtable` / `tonight_show`。根因确认：`expand_3d_shots.py:60` `added = 0`，`:67` `clone["id"] = f"v5_e{added+1:03d}"`，`:83` `added += 1`——重复跑同一份文件就撞车。影响链见 P1-3。 | 成立，并入 **P1-3** |
| `outputs/scene_plates/` 只剩 3 个 meta json、无 png，16 内建模板 `stage.plate` 全指向该目录，`test_templates_validate_strict` 16/16 红；渲染期硬失败还是回退？ | **成立且是硬失败**。`validate_dir(strict_plates=True)` → `ok=False`，16/16 模板报 `stage.plate: 资产缺失`；16 个模板的 plate 解析全部 `level=missing`。渲染期 `scenes.compose_episode` 在 `if not plate.is_file(): raise RuntimeError`（`scenes.py:380-381`）——**没有回退到程序化背景**。但 `style_v2.plate_path` 里有 3 个硬编码兜底（`late_night_desk` / `history_study` / `roundtable_studio`）：**只要这 3 张里任意一张存在，16 部片会静默共用同一张背景**（`level=base-fallback`），那才是"产出与设计不符"的路径。 | 成立，见 **P1-5** |
| （Lead 第二轮）P1-3 的失败形态到底是不是硬失败 | **已实证为硬失败**：Lead 在 `ZAOPIANJU_OUTPUTS_DIR` 指向的临时根里真跑 `t3.assemble("tonight_show")`，`utils.run(check=True)` 抛 `RuntimeError: 命令失败 (exit 4294967294)`，`final.mp4` 未产出。另得两条实测观察（跨目录耦合、回退路径时长差 20.65s），均并入 P1-3 / P2-1。 | 已实证，结论并入 **P1-3** |
| （Lead 第二轮）P2-1「回退路径未扣转场」有无实测数字 | **已实测**：同一次跑打出 `xfade_chain: 传入 total=303.000s 与期望 282.350s 不一致，按期望时长裁剪`，**Δ = 20.65s**。P2-1 由"静态推断"升级为"实测"。 | 已实证，见 **P2-1** |

---

## **P0**

### P0-1 · `tts_breeze` 输出文件名只含 batch 下标 → 覆盖别的台词音频，`voice_cache` 指向错内容
- **位置**：`pipeline/tts_breeze.py:353-358`（文件名）+ `pipeline/render_v5.py:200-212 / 254-265 / 294-298`（按文本做 key 的缓存）
- **证据**：
  ```python
  # tts_breeze.py:353-358
  for i, nbytes in enumerate(seg_bytes):
      seg = pcm[cursor:cursor + nbytes]; cursor += nbytes
      idx = start + i
      wav_path = Path(out_dir) / f"streamb_{role}_{idx:03d}.wav"   # ← 只有 role + 批内下标
      _write_wav(wav_path, seg, sr)
  ```
  ```python
  # render_v5.py:207-212  缓存 key = 角色|声音指纹|台词
  key_prefix = f"{char_id}|{char_specs[char_id]['key']}|"
  for text in texts:
      if key_prefix + text not in cache: pending.setdefault(char_id, []).append(text)
  # render_v5.py:257-258  缓存 value 只存 path
  cache[key] = {"path": item["path"], "duration": item["duration"], ...}
  # render_v5.py:294-298  命中缓存 → 直接用该 path 的时长
  info = cache.get(f"{char_id}|{spec['key']}|{line}")
  speech = float(info.get("duration") or 0.0); src_voice = Path(info["path"])
  ```
  `pending` 的顺序取决于「哪些句子这次缺」，所以**同一句话的 `idx` 每次重跑都可能变**，而文件路径只由 `idx` 决定。
- **`voice_cache` 的 key 是什么（Lead 点名确认）**：**是「台词原文」拼接串，不是哈希、也不是下标**——
  ```python
  # render_v5.py:207   key 前缀
  key_prefix = f"{char_id}|{char_specs[char_id]['key']}|"
  # render_v5.py:209   命中判定：前缀 + 台词原文
  if key_prefix + text not in cache: pending.setdefault(char_id, []).append(text)
  # render_v5.py:257   写缓存：key 同样用 item["text"]（原文）
  key = f"{cid}|{char_specs[cid]['key']}|{item['text']}"
  # render_v5.py:294   读缓存：line 是计划里的台词原文
  info = cache.get(f"{char_id}|{spec['key']}|{line}")
  ```
  → 因此**不是**「key 也是下标」的错配双重污染：字幕文本来自计划（`shot["line"]`），**字幕是对的**；污染只发生在**声音/音频文件**这一侧——缓存里那条"文本 A"的记录，其 `path`（按 `idx` 命名）已经被"文本 A2"的合成结果覆盖。级别维持 **P0**（静默错音 + 截断，且 QC 无法察觉），不因 key 是文本而降级、也不升为双污染。
- **触发条件（固定表述）**：同一 `role` 的台词列表在重跑后**下标发生位移**，且新集合的某个 `idx` 恰好覆盖旧文件名时才触发。典型场景＝模板里 ≥2 句台词 + 改了其中 1 句 + 未设 `V5_REUSE_TTS` 且未删 `work/voice/voice_cache.json`。若改的是**最后一句且没有新增句子**（`pending` 长度 1、`idx=0` 与旧的首句重名）同样会触发——**只要旧的某句仍留在计划里就会被读到被覆盖的文件**。
- **静态读码 + 伪造 HTTP 层实证**（`docs/audit/_tools/v6_tts_filename_collision.py`：monkeypatch `httpx.post` + `server_alive`，可控 PCM + `X-Segment-Bytes`，不启动 Breeze、不联网、不碰 GPU）：
  ```
  run1（合成 A、B 两句）:  A→streamb_laozhou_000.wav(5324B)  B→streamb_laozhou_001.wav(1964B)
  run2（只改第 1 句 A→A2）: A2→streamb_laozhou_000.wav(5804B)   ← A 的音频被覆盖
  ```
- **影响**：在上述触发条件下，**仍在使用旧句的镜头会拿到新句的声音**：
  1. 声音错（字幕 A / 配音 A2），QC 的 `voice_fixed` 只比 `voice_key`，**查不出来**；
  2. 缓存里的 `duration` 是旧句的，`_build_shot_audio` 用 `atrim=0:{duration}`（`render_v5.py:375-376`）→ 新音频**被从中间截断**，末字丢失；
  3. 音画时间轴按旧时长算，长句会挤占下一镜。
- **建议修法**：文件名改成内容指纹 `f"streamb_{role}_{sha1(text)[:12]}.wav"`（或直接用 `sid`）；缓存 value 里加 `text_sha1` 并在命中时校验文件存在且 `mtime/size` 未变；顺手让 `synthesize_batch` 返回的 `duration` 与文件重新 probe 一次（现在 `probe_duration` 就是文件真实长度，但缓存里存的是旧的）。

---

## **P1**

### P1-1 · HQ 的 T2VA / ref2va 缓存只判「体积 > 200KB」，改 prompt / seed / 时长永不生效（F2 未修）
- **位置**：`pipeline/templates3d_hq.py:731-736`（T2VA）、`:804-813`（ref2va）
- **证据**：
  ```python
  # gen_t2va
  suffix = f"{STYLE_2D_CACHE}_{style_key}" if style_mode == "2d" else ""
  if look in ("realistic", "3d_anim"): suffix += f"_{look}"
  out = WORK / "clips" / f"{tid}_{shot['id']}_t2va1024{suffix}.mp4"
  if out.is_file() and out.stat().st_size > 200_000:      # ← 唯一判据
      return out
  ```
  文件名里没有 `seed`、没有 `frame_length(duration)`、没有 prompt 指纹；`gen_t2va` 收 `seed` 参数却只用于提交 workflow。
- **影响**：改语义 brief 的一句话、改 `--seed`、改镜头时长（`frame_length` 随之变）→ 仍复用旧片。**静默坏片**：画面/运镜与文本不符，且时长按旧 `duration` 生成，后面靠 `normalize_clip` 的 `tpad` 冻结末帧凑时长 → 尾部定格。
- **建议修法**：与 `_generate_keyframe` 一样写 `<out>.meta.json`，键含 `prompt_sig(crc32)/seed/frame_length/style_mode/look/style_key`；命中条件改为「meta 全等 + 文件存在」，别再用体积当判据。

### P1-2 · 关键帧 `_render` 的体积短路 → 改 prompt 后仍用旧图，并把「旧图」登记成新 `prompt_sig`（R2 未修，缓存毒化）
- **位置**：`pipeline/templates3d_hq.py:531-534`（短路）+ `:515-528`（外层只在 sig 不符时才走到这里）+ `:590-606`（写 meta）
- **证据**：
  ```python
  def _render(seed_value, raw_dir, dest):
      # force 时必须重画：否则会把旧图当成新 prompt 的结果写进 meta（缓存毒化）
      if (not force) and dest.is_file() and dest.stat().st_size > 100_000:
          return dest.stat().st_size          # ← 注释承认风险，但只处理了 force
  ...
  if (not force) and key_png.is_file() and ... and meta_path.is_file():
      params_ok = (... and str(cached_meta.get("prompt_sig") or "") == prompt_sig ...)
      if params_ok and (expected_pick is None or expected_pick == cached_chosen): return key_png
  spec_path = _imagegen_spec_for(cid, KEYFRAMES / "specs")
  ...
  size = _render(seed, ..., key_png)          # prompt_sig 变了 → 走到这里 → 却被体积短路 → 返回旧图
  ...
  meta_path.write_text(json.dumps({... "prompt_sig": prompt_sig ...}))   # ← 新签名 + 旧图
  ```
- **影响**：改语义 brief（`semantic_visual`）而**不加 `--force`** 时：关键帧是旧的、meta 却写着新签名 → **下一次跑连 sig 都对得上，永久复用旧图**。修一次只能靠删 `KEYFRAMES/` 或加 `--force`，而 `--force` 会连 90 个镜头全部重画（见 P2-4）。
- **建议修法**：`_render` 的短路条件加上「调用方声明的签名」：把 `prompt_sig`/`seed` 一起传进去，或干脆删掉这个短路（它的收益只在不 force 的重复调用，而外层已经做过缓存判定）。

### P1-3 · HQ 语义 brief 与 assemble 的 `episode.json` id 空间分裂 + 13 份 brief 有重复 id（Lead 事实 1+2 的合并影响链）
- **位置**：`templates3d_hq.py:912-913`（只吃 brief）、`:918-980`（按 `shot["id"]` 落 `shots/<sid>.mp4`）、`templates3d.py:510`（assemble 只吃 `episode.json`）、`:507-541`；根因 `scripts/expand_3d_shots.py:60,67,83`
- **证据（`docs/audit/_tools/v1_semantic_ids.py` / `v2_id_alignment.py` / `v3_episode_vs_brief.py`）**：
  ```
  semantic files: 30   含重复 shot id 的: 13   shots 63–93   acts 4–9
  worst: history_mystery 的 v5_e001 ×9（roundtable / tonight_show 同为 ×9，custom_tpl_v46_duologue 为 ×4）

  tonight_show   : episode.json 78 镜 / semantic 76 镜 / **67 镜在 brief 里不存在** / 磁盘 0 个 clip
  history_mystery: 78 / 63 / 68 / 0
  custom_tpl_v46_duologue: episode 6 镜 / semantic 90 镜 / 缺 0 / 磁盘 0
  19/30 模板的 episode 镜头有 86–92% 在 brief 里找不到；这些模板被 assemble 索要的「从未生成的镜头」共 1313 个
  11 个 custom_tpl_* 反向失配：brief 81–93 镜，episode 只有 4–6 镜（缺 0）
  ```
  **基线声明（重要，避免按错文件复核）**：以上"78 镜 / 1313 镜 / 86–92%"全部取自 `outputs/templates_v5/<tid>/work/episode.json`——**即 `t3.load_episode` 实际读的那一份**。`outputs/templates_3d/<tid>/work/episode.json` 下存在**同名陈旧文件**，镜数完全不同（`v7_two_episode_files.py` 实测：`tonight_show` V5_OUT=78 / OUT3D=**11**；`history_mystery` 78 / **10**；`custom_tpl_v46_duologue` 6 / 6）。assemble **不读** OUT3D 那份，但它会误导人去改错文件（下条"跨目录耦合"）。
- **实证（Lead 第二轮在临时 `ZAOPIANJU_OUTPUTS_DIR` 真跑 `t3.assemble("tonight_show")`，真实 outputs 零写入）**：
  ```
  V5.4 editing 不可用，回退单转场: 剪辑片段不存在: ...\templates_3d\tonight_show\shots\v5_card_title.mp4
  RuntimeError: 命令失败 (exit 4294967294): …ffmpeg.exe … -i ...\shots\v5_card_title.mp4 -i ...\shots\v5_001.mp4 …
  final.mp4 产出: False
  ```
  → 第 2 跳判定**成立**：`utils.run(check=True)` 抛 `RuntimeError` 硬失败，没有静默出坏片。附带两条实测观察：
  - **跨目录耦合（id 分裂的另一面）**：输入时间线读 `V5_OUT/<tid>/work/episode.json`（`templates3d.py:352-356`，缺文件走 `SystemExit`），镜头却从 `OUT3D/<tid>/shots/` 取（`:432`、`:513`、`:516`）。所以**缺哪个目录，报的是完全不同的错**：只复制了 `templates_3d`（缺 `templates_v5`）→ `SystemExit: 模板 tonight_show 缺少 work/episode.json`（看起来像时间线丢了）；只复制了 `templates_v5`（缺 `templates_3d/.../shots`）→ ffmpeg `Error opening input file`（看起来像镜头坏了）。**两个栈的产物目录被硬绑在一起，但没有任何一致性校验**。
  - **回退路径的时长差被实测出来**：同一次跑打出 `xfade_chain: 传入 total=303.000s 与期望 282.350s 不一致，按期望时长裁剪` → 见 P2-1。
- **影响链**（三跳）：
  1. **落盘覆盖**：`gen_t2va`/`gen_ref2va`/`_generate_keyframe` 都以 `shot_id` 命名（`WORK/clips/{tid}_{sid}_*`、`KEYFRAMES/<tid>/<style>/<sid>.png`）。同 id 出现 9 次 = 同一路径被写 9 次，**前 8 次生成的结果被丢弃**（每次 H3 1–6 分钟 GPU）。成片里那条镜头用的是最后一次的 prompt/seed。
  2. **assemble 硬失败（已实证）**：`t3.assemble` 按 `episode.json`（V5_OUT，78 镜）的 id 去 `OUT3D/<tid>/shots/` 找 clip，缺 67 个 → `editing.xfade_chain` 先 `FileNotFoundError`（被 `render_v5.assemble:833` 吞成 warning 后回退）→ 回退路径 `concat_video` / `concat_video_xfade` 把这些路径**原样塞给 ffmpeg**（`render.py:442-451`、`558-559`）→ ffmpeg `No such file` → `utils.ffmpeg(check=True)` 抛 `RuntimeError: 命令失败 (exit 4294967294)` → 整条 assemble 失败、`final.mp4` 不产出。**是硬失败，不是静默坏片**；但错误信息只是 ffmpeg 的 `Error opening input file`，**看不出根因是 id 失配**，更看不出另一端还有一份 11 镜的陈旧 `episode.json`。
  3. **QC 抓不到**：`evaluate_hq_qc` 只查「成片存在 / 海报 / 时长 ≥ target×0.6 / 有音轨 / 宽 ≥1280」（`agents/registry.py:341-365`）。镜头层错误（缺镜、重复镜、旧镜）它一个都看不见。反过来说：11 个 custom 模板**只有 4–6 镜进成片、80+ 镜白烧 GPU**，QC 依然全绿。
- **建议修法**：① `expand_3d_shots.py` 的 id 用 `crc32(prompt)+序号` 或「最大已用 id + 1」而不是 `added+1`，并把 `added` 从现有文件里的最大序号续号；② `templates3d_hq.main()` 落盘前先校验 `len(set(ids)) == len(ids)`，重复直接 fail；③ 要么让 brief 由 `episode.json` 派生（单一事实源），要么让 `assemble` 只遍历「brief 里有且文件存在」的镜头并把 `episode.json` 的重叠部分对齐；④ `evaluate_hq_qc` 加一条「成片镜头数 == episode 镜头数」或「每镜 clip 存在」的硬门。

### P1-4 · HQ 的 H3/T2VA 产物没有任何 per-shot 证据（meta），完全绕开 `h3_comfy` 的严格证据链
- **位置**：`templates3d_hq.py:980`（只 `shutil.copy2(final_src, out_shots/<sid>.mp4)`，全程无 meta 写入；`gen_ref2va:825-833` 只判 `returncode`，连 stdout 都不解析）；对比 `h3_comfy.py:613-690`（强制 `prompt_id`、`meta_ok`、`assert_unique_prompt_ids`）
- **证据**：`Select-String templates3d_hq.py -Pattern "meta.json|write_json|copy2"` → 只有 4 处 `copy2`（卡片/归档/关键帧/成片），**没有任何 `shots/<sid>.meta.json`**。而 `h3_comfy` 只被 `render_v3.py` 使用（V3 老栈）。
- **影响**：
  - 短片栈（V49 主线）产出的 clip **无法追溯** prompt/seed/workflow/prompt_id → 出问题不可能复现，也无法判断「这条镜头是不是复用了上周的旧片」；
  - `qc_v3` 的 `h3_coverage` / `prompt_id` 唯一性 / `workflow` 硬门（`qc_v3.py:344-375`）在 HQ 栈上**根本无从执行**，HQ 只能落到 4 项弱门（P1-3 第 3 跳）；
  - 与 `h3_comfy` 的严格度形成明显倒挂：主线栈反而最松。
- **建议修法**：`templates3d_hq` 改为调用 `h3_comfy.generate(...)`（它已经会写 meta、校验 prompt_id），或至少让 `gen_ref2va` 解析 CLI 的 JSON 结果行并写 `shots/<sid>.meta.json`；`evaluate_hq_qc` 增加「每个 body 镜有 meta 且 prompt_id 唯一」。

### P1-5 · `outputs/scene_plates/` 全缺 png：V3/V4 渲染期**硬失败**，16/16 严格校验红；而 `plate_path` 的 3 个硬编码兜底会在「只补一张」时静默串景（Lead 事实 3）
- **位置**：`pipeline/scenes.py:380-381`（`raise RuntimeError`）、`pipeline/style_v2.py::plate_path`（三级候选 + 3 个硬编码 alt）、`contract/templates_v3/*.json` 的 `stage.plate`
- **证据（`docs/audit/_tools/v4_scene_plates.py`）**：
  ```
  validate_dir ok = False  模板数 = 16  失败 = 16
  失败样例 classics: ["stage.plate: 资产缺失 outputs/scene_plates/archive_room__ink.png"]
  classics  plate=archive_room__ink       level=missing fallback=True exists=False
  ...
  解析不到的模板数: 16 / 16
  ```
  ```python
  # scenes.py:374-381
  plate, plate_style, plate_fallback = style_v2.plate_path(profile, str(scene.get("plate") or ""))
  ...
  if not plate.is_file():
      raise RuntimeError(f"缺少场景 plate: {scene.get('plate')}")
  ```
- **影响**：
  1. 走 V3/V4 栈（`render.py:1094` → `scenes.compose_episode`）的渲染**必然**在这一步抛错——不是"回退成程序化背景"，是直接失败。当前 V5（`scene_render`）与 HQ 栈**不读** `outputs/scene_plates`（V5 用 `assets/v5/scenes/<sid>.png`），所以这条只打击老栈 + 验收测试；
  2. **真正的静默风险**：`style_v2.plate_path` 在候选全 miss 后会依次试 `late_night_desk.png` / `history_study.png` / `roundtable_studio.png`。目前三张都没有 → 硬失败（行为正确）。**但一旦有人只补其中一张**，16 部片会一起变成同一张背景（`level="base-fallback"`，`fallback=True`），`plate_fallback` 只写进 meta，没人会读 → 「与设计不符」的坏片。
- **建议修法**：要么把 16 个 plate 补回来；要么把 `stage.plate` 改成可选并让 `compose_episode` 在没有 plate 时走 `scene_render` 程序化背景（而不是 raise）；同时给 `plate_path` 的硬编码 alt 加 `LOG.warning` 并在 QC 里把 `plate_fallback=True` 当硬门。

### P1-6 · `scene_render.smooth_noise` 的缓存存的是**全尺寸 float32**，64 项 ≈ 350MB 常驻
- **位置**：`pipeline/scene_render.py:520`（`_NOISE_CACHE`）、`:528-556`（key 含 seed → 每镜都新 key）
- **证据**：
  ```python
  total = np.zeros((h, w), dtype=np.float32)      # 528 前：直接按目标尺寸分配
  ...
  arr = total.astype(np.float32)                  # 553：全尺寸
  if len(_NOISE_CACHE) > 64: _NOISE_CACHE.pop(next(iter(_NOISE_CACHE)))
  _NOISE_CACHE[key] = arr                         # 556：全尺寸入缓存
  ```
  key = `(w, h, scale, seed, octaves, persistence, normalize)`；`render_scene_layers` 每镜的每层都是独立 `layer_seed`（`scene_render.py:1081-1084`），所以一部长片会产生**几十上百个不同 key**。
  单条 1600×900×4B = **5.49 MiB**；上限 64 条 ≈ **352 MiB**（float32 峰值，且不随镜头数回落）。
- **影响**：V5 长片（40s+ / 60+ 镜 × 3 层）渲染过程中内存随镜头数爬到 350MB 后稳定，叠加 layerset 缓存（`motion2d._PREP_CACHE`，8 项 × 3 层 × ~2.7MB）与 PIL 大图，低配机器/并行集会明显吃紧。文档注释写的是「小分辨率生成 + 双线性放大，毫秒级」，**实际缓存的是放大后的结果**，与注释意图相反。
- **建议修法**：缓存 `(gh, gw)` 的小图（几 KB）而不是放大结果；或按 `(w,h,scale,seed)` 用 `lru_cache(maxsize=8)`。

### P1-7 · `lipsync` 把整段视频解码进内存（不 downscale，`max_frames` 只是上限不是截断）
- **位置**：`pipeline/lipsync.py:511-525`（整段读）、`:743`（`_sync_in_process` 先整段读再按 start/duration 切片）、`:1057-1059`（`mouth_audio_sync` 先整段读再截 600 帧）、`:759`（`_write_video` 又持有一份 `out_frames`）
- **证据**：
  ```python
  def _ffmpeg_frames_in(clip: str) -> list:
      cap = cv2.VideoCapture(str(clip));  frames = []
      while True:
          ok, fr = cap.read()
          if not ok: break
          frames.append(fr)          # ← 全片、原始分辨率、BGR
      cap.release(); return frames, float(fps)
  ```
  ```python
  frames, vfps = _ffmpeg_frames_in(clip)                        # 743 全片
  if start or duration: frames = frames[s:e]                     # 744-747 之后才切
  ...
  enc = _write_video(res["out_frames"], fps, audio_seg, out)     # 759 输出帧再一份
  ```
  `max_frames` 只在 `_sync_clip_impl:933-935` 做「超过就报错」，不截断。
- **影响**：600 帧 @1024×576×3B = **1,061,683,200 B ≈ 0.99 GiB（1061 MB）**（输入）+ 同量级（输出）+ MuseTalk 的 crop/coords → Muse 阶段在 12GB 卡机器上内存/显存双紧，容易 MemoryError 或触发系统换页（比 OOM 更糟：整机卡死）。
- **建议修法**：用 `cv2.VideoCapture` + `cap.set(CAP_PROP_POS_FRAMES, s)` 做**有界窗口**读；或写成生成器，逐帧检测+贴回再写 ffmpeg stdin（`_write_video` 已是管道模式，容易改）；`mouth_audio_sync` 的 `max_frames` 应该在读取循环里 break。

### P1-8 · `muse_video.available()` 永远返回 `order[0]` 且 `ok=True`；`_local_generate` 用 `shell=True + str.format` 且无 timeout
- **位置**：`pipeline/muse_video.py:118-141`（可用性）、`:457-477`（本地命令）
- **证据**（`docs/audit/_tools/v5c_available_constant.py`，把所有 provider 的配置清空后调用）：
  ```
  {"all_configs_emptied": true, "ok": true, "provider": "apimart_muse",
   "configured_flags": {..., "apimart_muse": false, "metaso_h3": false, "minimax_cloud": false, "stub": true}}
  ```
  ```python
  order = ["apimart_muse", "metaso_h3", "minimax_cloud", "musev_local", ..., "stub"]
  for name in order:
      if name.endswith("_local"): ...
      elif name in ("replicate", "useapi_muse"):
          if item["configured"]: provider = name; break
      elif name == "muse_cloud":
          if item["configured"]: provider = name; break
      else:                        # ← apimart_muse / metaso_h3 / minimax_cloud / stub 全落这里
          provider = name; break   # ← 第一次循环就命中 apimart_muse，无条件
  ```
  后果：① `ok` 恒 True（调用方/UI 的"Muse 已就绪"是假的）；② `musev_local` 就算装好也**永远不会被选中**；③ `stub` 分支不可达（写死的兜底死代码）；④ 真正调用时才报 `未配置 APIMART_API_KEY` 或 HTTP 401/429（本轮实测到一次真实 `HTTP 429 insufficient_quota`）。
  ```python
  # 457-477
  cmd = cmd_tpl.format(**mapping)              # mapping 含 prompt/image/out（用户数据）
  proc = subprocess.run(cmd, shell=True, ...)  # ← shell + 无 timeout + 无引号保证
  ```
- **影响**：`available()` 的语义被废掉（所有 `if not avail["ok"]` 的闸门都不会触发）；本地 provider 路径上，含空格/引号/`&`/`|`/中文标点的 prompt 或 Windows 路径会被 cmd.exe 二次解释（用户模板里加不加引号决定成败），且**没有 timeout**——MuseV 推理卡死会永久挂住 agent 阶段。
- **建议修法**：`available()` 里给 `apimart_muse`/`metaso_h3`/`minimax_cloud`/`stub` 各写显式分支并检查 `configured`；`stub` 单独处理；`_local_generate` 改成 argv 列表（用 `shlex.split(tpl)` 再对每个 token 做占位替换）+ `timeout`。
  > 注：该文件正在被并发修改，本条基于本轮 691 行版本核验。

### P1-9 · `--duration` 对短模板不是下限：合同 30s 的模板实际只出 7.8s
- **位置**：`pipeline/v5_story.py:147,204`（`total` 只求和，`passes=1` 未用）、`render_v5.py:337-339`（只有 `target >= 120` 才补片）、`:412-418`（`_trim_to_target` 只缩不扩）
- **证据**（`docs/audit/_tools/v1_semantic_ids.py` 同批 `tmp_verify_plan.py` 的产物，已并入 v1 输出）：
  ```
  real_street:       plan=26.0s  target=35s  ratio=0.74
  custom_holo_talk:  plan=7.8s   target=30s  ratio=0.26
  ```
  其余 31 个模板 ≥0.75。
- **影响**：`python -m pipeline.agents run --template custom_holo_talk --duration 30` 会安静地交出一支 **7.8s** 的片子；`episode.total_duration` 被写成 7.8 → `qc_v5` 的时长门拿 7.8 比 7.8 → **QC 全绿**。用户拿到的是"合规模板但只有 1/4 时长"的片子。
- **建议修法**：`build_plan` 里按 `target` 对 acts 做一次配比填充（`passes` 字段已经预留），或在 `tts_and_pace` 里把补片阈值从 `>= 120` 改成「`total < target*0.9` 且模板声明了 target」，并对短模板复用 `unique_fill`（它已经在 `_append_fillers` 里用于长版）。

---

## **P2**

| # | 位置 | 问题 | 证据 / 影响 | 建议 |
|---|---|---|---|---|
| P2-1 | `render_v5.py:837-851, 863-920` | **回退路径的时长不一致（已实测）**：`xfade_chain` 失败时 `transition_loss`/`_render_total` 从未写入，`render_total = total`（未扣转场）；而回退的 `concat_video_xfade` 会按 `TRANSITION_DUR=0.35` 缩短 0.35×(n-1)。`final_encode` 用 `-t render_total -shortest` → 视频先结束，**尾镜音频被截**；同时 `timings` 走的是无 `_edit_start` 的游标 → 字幕整体偏移 0.35×(n-1)。 | **Lead 实跑 `t3.assemble("tonight_show")` 实测**：`xfade_chain: 传入 total=303.000s 与期望 282.350s 不一致，按期望时长裁剪` → **两套时长差 20.65s**（78 镜 × 0.35 ≈ 26.95s，扣除部分 cut 边界后与 20.65s 同量级）。即：传给编码器的时长与实际拼出的时长差 20.65s，靠 `-shortest` 兜住，尾段音频被吃。`qc_v5` 的时长门（±0.8s）会失败，属于"响但迟"：片子已编码完才红。 | 在 `except` 分支里同样写 `_render_total = total - loss`（loss 用回退转场算），或直接 `raise` 让上层重跑而不是悄悄换拼法；`xfade_chain` 那句 `LOG.warning` 应升级为 raise（它已经在告诉你时长对不上）。 |
| P2-2 | `render_v5.py:657-678` | `render_shot_clips(..., force=force)` 的 `force`**形参从未被使用**；`_prerendered` 的填充镜只要文件在就无条件复用（`if shot.get("_prerendered") and out_mp4.is_file(): continue`）。 | 改风格/改 `BGM`/改时长后重跑，b-roll 填充镜仍是上一轮的画面与时长；`force=True` 也救不回来。 | 用 `shot_meta.json` 里的 signature（style_id/duration/seed）建补充键；`force` 时跳过 `_prerendered` 复用。 |
| P2-3 | `render_v5.py:299-305` | 长台词镜**不封顶**（`MAX_SHOT` 只剩告警），超出的量靠 `_trim_to_target` 删别的镜头补。 | 一句 20s 的台词会把整片的 b-roll 全删光（F6 的"代价转移"仍在）。 | 超 `MAX_SHOT` 时按语速上限告警并在 `episode` 里标记 `over_max`，QC 里当软门；或对超长句做 TTS 加速（`TTS_MAX_ATEMPO` 已有）。 |
| P2-4 | `templates3d_hq.py:897-900, 970, 976` | `--force` 的帮助写"忽略所有缓存（关键帧/镜头）"，但 `force` **没有传给** `gen_t2va`/`gen_ref2va`（两者根本没这个参数）。 | 用户按提示加 `--force` 想重渲，关键帧重画了、**H3 镜头仍是旧的** → 新旧混排。 | 给 `gen_t2va`/`gen_ref2va` 加 `force` 并删缓存文件；或把帮助文案改成"仅关键帧"。 |
| P2-5 | `templates3d_hq.py:947-968` | 关键帧/锁脸失败时 `except` → **静默回退 T2VA** 后继续，整片 ok。 | 一部本想做「2D 关键帧锁脸」的片子里，失败的那几镜变成 3D/T2VA 观感，`log` 里只有一行"回退 T2VA"，产物一致性无人校验。 | 记 `fallbacks[]` 并让 `evaluate_hq_qc` 在 `fallbacks` 非空时红（`qc_v5` 已有这个字段范式）。 |
| P2-6 | `templates3d_hq.py:709-718, 815-819` | `_ensure_silence` 与首帧抽取都是 `check=False` 且不校验产物（只在大循环里 `if not first.is_file()`）。 | 静音参考生成失败 → 路径传下去，H3 报错信息失真；首帧抽取失败但旧文件存在 → 用上一轮的帧做 ref2va（串镜）。 | 两处都改 `check=True` + 校验尺寸/时长；抽帧用独立临时名再 `replace`。 |
| P2-7 | `templates3d_hq.py:1047-1071`（`_conform_duration`）、`render_v5.py:962-965` | 长版对齐只在 `target ≥ 120` 生效，且返回值来自 `probe_duration`；若 probe 失败返回 `0.0` → `episode.total_duration=0`。 | 成片时长被写成 0 → 后续 `timeline`/报告/QС 全按 0 算。 | `_conform_duration` 在 `actual<=0` 时返回原值并 `LOG.warning`；`total_duration` 用 max(old, new)。 |
| P2-8 | `scene_render.py:520-557` + `motion2d.py:915-919` | `_get_layerset` 的缓存键 = `id(layers)+camspec+style_sig+size`，**不含 `duration`/`seed`**；而 `prepare_layers` 的 overscan 是用 `camera_state(..., duration, seed)` 采样 25 帧算出来的。 | 同 `layers` 对象 + 同 camspec 但 duration/seed 变了 → 命中旧 layerset → overscan 按旧轨迹算 → 推近/摇摆镜**露黑边**。目前 `render_shot_clips` 每镜新建 layers 对象，命中率低，但 `render_still`/storyboard 复用同一 layers 时会踩。 | 把 `duration`/`seed` 加进 `camspec` 或缓存键。 |
| P2-9 | `v5_voices.py:80-88` | `voice_key` 不含 `ref_text`（参考音频的文本）。 | 改 `ref_text` 后克隆音色会变，但 `voice_key` 不变 → `voice_cache`（P0-1 的那套）不失效 → 继续用旧音色，QC 的 `voice_fixed` 也认为没变。 | `voice_key` 里加 `ref_text` 的 hash。 |
| P2-10 | `v5_voices.py:278-290` | `set_character_voice` 的 docstring 说"深合并"，实际是**整体覆盖写** `{"id":..,"voice":updates}`，不读已有 custom 文件。 | `voice set --seed 123` 会把之前设置的 `instruction` 一起清掉。 | 读已有文件 → `_deep_merge`（同文件里已有该函数）。 |
| P2-11 | `v5_audio.py:384-401` | `_resolve_preset` 对**未知 preset 静默回退 synthwave**：`resolved` 永远落在 `PRESETS` 里，所以 `if fn is None` 是死分支，`LOG.info("preset 未知…")` 永不执行。 | 风格 JSON 里写错 `audio.bgm` → 全片配乐变成 synthwave，无任何日志/QC 痕迹。 | `_resolve_preset` 未知时 `LOG.warning` 并把 `resolved` 写进 BGM meta。 |
| P2-12 | `v5_audio.py:402-418` + `render.py:839` | BGM 固定 60s（`DEFAULT_SECONDS`），靠 `-stream_loop -1` 循环；`mono[:int(seconds*SR)]` 在包络中途硬切。 | 长片每 60s 循环点有一次波形跳变（咔哒声）；`_pad/_pluck` 的尾音被切断。非致命但可听。 | 生成时给首尾各 0.5s 交叉淡化，或按 `total+5s` 生成再裁。 |
| P2-13 | `v5_scene_art.py:620-631` vs `:736-739` | `_meta_matches` 只比 `prompt/negative/seed/size/model`，**不比 `steps/cfg/sampler/scheduler`**（而 meta 里明明记了这 4 个）。 | 调 `steps`/`cfg`/`sampler`（`prompts.json` 或 CLI）→ 仍复用旧 plate，画面与参数不符。 | 把 4 个参数加进比较。 |
| P2-14 | `qc_v5.py:60, 70-74, 112-117` | `style_fingerprint` 的抽帧目录是 `tmp/v5_qc/_fp_{video.stem}`，而所有模板的成片都叫 `final.mp4` → 全部共用 `_fp_final`；`finally` 里 `tmp.glob("*.png").unlink()` 会删到别的进程正在读的帧。 | 季生产并行/两个 run 同时 QC → 抽帧互相删 → 指纹直方图缺帧（`frames=6` 但实际样本 <6），`edge_density` 失真。 | 目录加 `os.getpid()` 或 `template_id`。 |
| P2-15 | `agents/registry.py:341-365`（`evaluate_hq_qc`） | HQ 硬门只有 5 项：存在/海报/`duration ≥ target*0.6`/音轨/宽 ≥1280。**不查镜头数、不查 meta、不查黑帧、不查字幕覆盖**。 | `target*0.6` 的容差意味着成片只要 60% 时长就过——与 P1-9（时长不足）叠加时，一支 7.8s/30s 的片也能过。 | 补：镜头数一致性、per-shot meta、`fallbacks` 为空、黑帧检测（`qc_v5.blackdetect_count` 可直接复用）。 |
| P2-16 | `render_v5.py:1151-1178` + `qc_v5.py:259-263` | `lip_sync` 门**fail-open**：`_mouth_sync` import 失败、或 `audio_path` 不存在时 `continue`，`per_shot` 为空 → `lip_ok = not no_speech and not low_cov` → True。 | 音频全缺（或 wav 不是 s16le 导致 `_build_shot_audio` 之外的路径产出别的格式）时，报告显示"口型覆盖达标"。 | 有对白镜但 `per_shot` 为空 → 直接 `ok=False`。 |
| P2-17 | `render_v5.py:1162` | `run_qc` 里 `_mouth_sync.envelope_timeline(...)` **没有 try**（而渲染期同一调用有 try）。 | `_read_wav_mono` 只吃 `pcm_s16le`，遇到非 16bit/非 RIFF wav 会抛 `MouthSyncError` → **成片已生成却因 QC 抛错而整条 run 失败**。 | 与渲染期一致：try + 记 `fallbacks`，或失败即判 `lip_ok=False` 而不是异常。 |
| P2-18 | `ass_builder.py:158-160, 147, 230-232` | ① `lower_third` 名字条 `lt_end = lt_start + max(2.4, ...)` 可**超出本镜尾部**（短镜 1.0s 时挂 2.4s）→ 与下一镜字幕同屏；② `build_ass` 只在 `(w,h)!=(1280,720)` 时改 `PlayResX/Y`，而事件里的 `\pos(640,655)/\pos(46,30)` 是**硬编码 1280×720 坐标**。 | 短镜人名条串场；非 720p 调用（当前 V5 固定 1280×720，V4 `render.py` 传入的也是 720p）时字幕整体错位——属潜伏缺陷。 | `lt_end = min(end-0.05, ...)`；坐标按 `width/1280` 缩放。 |
| P2-19 | `templates3d_relip.py:97, 99, 107` | ① `--width 768 --height 432` 生成的镜头**直接覆盖** `shots/<sid>.mp4`（HQ 其他镜头是 1024×576）→ 成片里这一镜被放大 1.33× 变软；② `abs(hash(tid+sid))` 做种子（`PYTHONHASHSEED` 随机，每次重跑口型都不同）；③ `shutil.copy2(out, clip)` **无 `_orig` 备份**。 | relip 一遍后 HQ 镜头被不可逆降质覆盖，AGENTS 里宣传的"原件保留在 `_orig/`"在这条路径上不存在。 | 生成尺寸对齐 1024×576；seed 用 `crc32`（`templates3d_hq.py:937` 已有正确写法）；覆盖前 `to_orig()`。 |

---

## 静态验证明细

所有脚本在 `docs/audit/_tools/`（本轮新增，未改动 `pipeline/`、`contract/`、`outputs/`）。

### 1) 语义 brief 的镜头数 / 重复 id / acts 脱钩 — `v1_semantic_ids.py`
```
$ python docs/audit/_tools/v1_semantic_ids.py
template                           shots uniq dupids acts dup_extra
classics                              66   66      0    9         0
custom_tpl_v46_duologue               93   90      1    6         4 <<<
history_mystery                       71   63      1    9         9 <<<
tonight_show                          76   68      1    9         9 <<<
roundtable                            71   63      1    9         9 <<<
...
semantic files: 30  files with duplicate shot ids: 13
worst: history_mystery shot id v5_e001 x9
shots range: 63 - 93        acts range: 4 - 9
```
（完整 30 行见脚本输出；`<<<` 为含重复 id 的文件。）

### 2) brief id 与 assemble 用的 episode.json 对齐 — `v2_id_alignment.py`
```
== tonight_show ==
  episode.json exists=True shots=78 first=['v5_card_title', 'v5_001', 'v5_002', 'v5_003']
  semantic     shots=76  first=['v5_card_title', 'v5_001', 'v5_002', 'v5_003']
  id 交集=11  episode 有而 semantic 无=67
  outputs/templates_3d/tonight_show/shots: 0 mp4
== custom_tpl_v46_duologue ==
  episode.json shots=6   semantic shots=93   交集=6
  outputs/templates_3d/custom_tpl_v46_duologue/shots: 0 mp4
```

### 3) 失配率（30 个模板全量，基线＝`outputs/templates_v5/*/work/episode.json`） — `v3_episode_vs_brief.py`
```
模板数: 30   会被 assemble 索要但 HQ 从未生成的镜头总数: 1313
至少有 1 个镜头无法对号入座的模板数: 19
（19 个内建综艺模板 miss 率 86–92%；11 个 custom_tpl_* miss 率 0%，但 brief 81–93 镜 / episode 4–6 镜）
```
> **基线口径**：脚本读的是 `REPO/outputs/templates_v5/<tid>/work/episode.json`（`t3.load_episode` 的真正输入）。**不是** `outputs/templates_3d/<tid>/work/episode.json`——后者是另一份陈旧文件，见下条。

### 3b) 两份同名 episode.json（Lead 提示的"第一次读到 11 镜"来源） — `v7_two_episode_files.py`
```
tonight_show               V5_OUT shots= 78 first=['v5_card_title','v5_001','v5_002']
tonight_show               OUT3D  shots= 11 first=['v5_card_title','v5_001','v5_002']
history_mystery            V5_OUT shots= 78      OUT3D shots= 10
custom_tpl_v46_duologue    V5_OUT shots=  6      OUT3D shots=  6
```
`t3.load_episode` 只读 V5_OUT（`templates3d.py:352-356`）；OUT3D 那份**没有任何代码读**，但名字一模一样 → 人手改错文件的概率很高（这就是 P1-3「跨目录耦合」的第三个坑）。

### 4) `scene_plates` 缺失的影响 — `v4_scene_plates.py`
```
validate_dir ok = False  模板数 = 16  失败 = 16
失败样例 classics: ["stage.plate: 资产缺失 outputs/scene_plates/archive_room__ink.png"]
... 16 行全是 level=missing fallback=True exists=False
解析不到的模板数: 16 / 16
```

### 5) `muse_video.available()` 是常量 — `v5c_available_constant.py`
```
$ python docs/audit/_tools/v5c_available_constant.py
{"all_configs_emptied": true, "ok": true, "provider": "apimart_muse",
 "configured_flags": {"musev_local": false, "musepose_local": false, "muse_cloud": false,
  "replicate": false, "useapi_muse": false, "apimart_muse": false, "metaso_h3": false,
  "minimax_cloud": false, "stub": true}}
```
（`v5_muse_provider.py` 是同一结论在 630 行版本上的复现，含一次真实 `HTTP 429 insufficient_quota`：`ok=True` 的 provider 实际不可用。）

### 6) `tts_breeze` 文件名覆盖 → 缓存指向错音频 — `v6_tts_filename_collision.py`
```
== run1：合成 A、B 两句 ==
[run1] {"第一句台词内容比较长": {"file": "streamb_laozhou_000.wav", "bytes": 5324, "duration": 0.11},
        "第二句": {"file": "streamb_laozhou_001.wav", "bytes": 1964, "duration": 0.04}}
== run2：只改第 1 句（A→A2） ==
[run2] {"改过的第一句台词比较长": {"file": "streamb_laozhou_000.wav", "bytes": 5804, "duration": 0.12}}
```
（静态读码 + 伪造 `httpx.post` 返回可控 PCM + `X-Segment-Bytes`；**未启动 Breeze 服务、不联网、不碰 GPU**。Lead 已确认接受此级证据，不再要求真机跑 Breeze。）

### 7) `qc.py` 的两条怀疑 → **证伪（不是缺陷）**
```
$ python - (import pipeline.qc; qc.probe / qc.blackdetect on outputs/fillers/classics/fill_001.mp4)
utils.probe_video -> {"width":1280,"height":720,"fps":25.0,"duration":4.35,"codec":"h264","has_audio":true}
qc.probe          -> {"duration":4.35,...,"audio_codec":"aac"}          # 两次 -show_entries 是合并的，不是覆盖
qc.blackdetect(非黑片) -> 0                                              # 不会把过滤器名计成黑段
```
详细输出与 8) 的锚点转储脚本：`v0_anchor_dump.py`（打印本报告引用的每一行原文，便于行号复核）。

### 8) `--duration` 不是下限
```
real_street: plan=26.0s target=35s ratio=0.74
custom_holo_talk: plan=7.8s target=30s ratio=0.26
```

---

## 前两轮回归核对表

判定口径：**已修** = 本轮代码里能找到明确修复；**未修** = 现象仍在；**部分** = 主症状修了但残留；**范围外** = 该文件不在本轮范围/未复核。

| 编号 | 前轮结论 | 本轮判定 | 现有代码证据 |
|---|---|---|---|
| **F1** P0 `BREEZE_TTS_KEEP=1` → UnboundLocalError | 已修 | `style` 已提到函数作用域：`render_v5.py:1247` 是 `if str(os.environ.get("BREEZE_TTS_KEEP"…))` 块的起始，`:1255` `style = _style(...)` 在块外（缩进 4）。拿掉环境变量后仍能拿到 `style`。 |
| **F2** P1 HQ 缓存 key 缺 prompt/seed/时长 | **部分/未修** | 关键帧已补（`templates3d_hq.py:515-528` 比对 `prompt_sig/base_seed/ip_strength/ip_end/ckpt/candidates_count`）；**T2VA 与 ref2va 仍是 `st_size > 200_000` 单一判据**（`:735`、`:812`）→ 见 P1-1。 |
| **F3** P2 Muse 锁抢活人 | 已修（残留） | `lipsync.py:853` 只在 `owner 且 not _pid_alive(owner)` 时抢占；`:875-882` `_release_lock` 只释放自己的。**残留**：`:860` `age > stale`(6h) 仍会抢活锁；`:877` 锁文件不可读（owner=0）时也会 unlink（`os.open` 与 `os.write` 之间的窗口）。 |
| **F4** P2 无脸仍 `ok=True` + NaN | 已修 | `lipsync.py:1122` `ok = bool(valid >= 8 and detected > 0)`；`:1118` 只把 `motion > 0.05` 计入 `valid`；`:1119-1121` `motion_mean` 用 `np.isfinite(...).any()` 兜底为 `None`（不再写 NaN）。 |
| **F5** P2 假称 syncnet | 已修 | `lipsync.py:1167` `result["provider"] = "mouth_audio_corr"`；`:1166` 注释明确"权重可读也不能冒充 syncnet 分数"；`measure_sync` docstring 同步改写。 |
| **F6** P2 `MAX_SHOT` 死代码 | 已修（改设计） | `render_v5.py:300-305` 不再 `min(MAX_SHOT, …)`，改为记 `shot["over_max_sec"]` + 告警；`MAX_SHOT` 仅用于这条告警。**代价**仍在（见 P2-3）。 |
| **F7** P2 qc_v5 口型门没被喂 `lip_sync` | 已修（fail-open） | `render_v5.py:1151-1178` 用 `mouth_sync.coverage_ratio` 造 `qc_extra["lip_sync"]`；`qc_v5.py:259-263` 消费它。**fail-open** 见 P2-16。 |
| **F8** P2 video_raw/narration 拷贝失败只 warning | 已修 | `render_v5.py:861-863`、`:934-935` 均 `raise RenderV5Error("…拒绝用旧素材出片")`。 |
| **F9** P2 同步指标没有 start | 已修（pipeline 侧） | `lipsync.py:1157-1164` `measure_sync(..., start=0.0)` → `mouth_audio_sync(start=start)`；`:1001/1019` `_audio_env` 按 `start` 偏移。**调用方**（`backend/app/routers/muse.py`）未复核。 |
| **F10** P3 `except TypeError` 误判 | 已修 | `render_v5.py:780` 改为 `inspect.signature(motion2d.render_shot).parameters` 探测，不再靠异常分类。 |
| **F11** P3 `deliver -c:a copy` / `--hd` 死参数 | 范围外 | `pipeline/deliver.py` 不在本轮清单，未复核（前轮给出的行号 69-73/101）。 |
| **F12** P3 `mouth_sync` 的 `lead` 形参是空的 | 已修 | `mouth_sync.py:80` `lead_frames = …`、`:89` `if i < lead_frames … viseme = "closed"`。字符形状也改为跟随 `voiced_seen`（`:93`）。 |
| **R1** P1 trim 后音频不重算 | 已修 | `render_v5.py:344-354`：trim 标记 `_trimmed_duration` 的镜头按新时长**重建音轨**（`_build_shot_audio(audio_path, None, dur)`）并写 `hash:"trimmed"`，之后 `concat_audio` 的顺序/长度与视频一致。 |
| **R2** P1 `_render` 体积短路让 `--force`/`prompt_sig` 双双失效 | **未修** | `templates3d_hq.py:531-534` 原样存在，`force` 能绕、**prompt 变更绕不过**，且外面 `:590-606` 会写入新 `prompt_sig` → 毒化。见 P1-2。 |
| **R3** P2 `lip_sync` QC 门恒真 | 已修（改判据） | 不再是"只判 `speech_duration > 0.2`"，改为 viseme `coverage_ratio ≥ 0.05`；恒真变为 fail-open（P2-16）。 |
| **R4** P2 ComfyUI 中继按 basename 落盘 | 范围外（有关联新证据） | `integrations/comfyui/*` 未复核；但 P1-4 指出 HQ 栈连 meta 都不写，问题层级更靠上。 |
| **R5** P3 `voiced_pos` 永远查不到 | 已修 | `mouth_sync.py:81-99`：`voiced_count/voiced_seen` 统计 `smooth>=0.30` 的帧，低能量分支按 `voiced_seen*len(chars)/voiced_count` 取字，字符随进度前进（不再恒取 `chars[0]`）。 |
| **R6** P3 `run_h3_ref --timeout` 死参数 | 范围外（有新增证据） | `h3_v5.py:830` 仍传 `--timeout 1200`，且 `:82-86` 的 `subprocess.run` **本身没有 timeout** → 即使 CLI 支持，父进程也不设上限（见 P2 相邻条 + P1-8 同类问题）。 |
| **R7** P3 `measure_sync` docstring / `ok` 判据 | 已修 | 同 F4/F5：docstring 改成"代理指标"，`valid` 排除 0.0 占位帧。 |
| **R8** P3 `read_json` 的 gbk 兜底 | **未修** | `utils.py:144-149` 原样：`("utf-8-sig","utf-8","gbk")` 三级兜底，gbk 成功时只 `LOG.warning`；损坏的 UTF-8 会被读成乱码字符串而不报错（渲染侧表现为 prompt/台词乱码而不是崩溃）。 |
| **R9** P3 脚本用 cwd 当仓库根 | 范围外 | `scripts/` 不在本轮清单。 |
| **R10** P3 `NAME_MAP` 对 ja/zh 也罗马化 | 范围外 | `localize/translator.py` 未复核。 |
| **R11** P3 H3 工作流硬编码节点 id `"90"/"91"` | 范围外 | `integrations/comfyui/*` 未复核。 |
| **R12** P3 超时无差别 `/interrupt` | 范围外 | 同上。 |

**回归小结**：24 条中 **已修 15**、**部分/未修 3**（F2、R2、R8）、**范围外 6**（F11、R4、R6、R9–R12）。两个 P0/P1 级现象（F2、R2）**在同一模块同一函数里原地未动**，且本轮又在其上叠加了新证据（P1-1、P1-2 与 P1-3 的落盘覆盖是同一个根因家族：**用"文件名/体积"代替"内容指纹"做缓存与落盘判定**）。

---

## 已核对无问题的点

1. **`utils.ffmpeg` 的公共约定是对的**：`pipeline/utils.py:57` 统一注入 `-hide_banner -nostdin -loglevel error -y`，`:47-51` `check=True` 抛错并带 stderr 尾部 → 绝大多数 `utils.ffmpeg(...)` 调用点不存在"缺 `-y`/缺 `-nostdin`/不看 returncode"的问题。直接 `subprocess.run` 的少数几处（`templates3d_hq.py:560/715/818/999/1004`、`h3_v5.py:91`、`lipsync.py:533/559/1010`）都显式带了 `-y`。
2. **Windows 盘符/中文/特殊字符进 filter 的经典坑已被规避**：全仓 `drawtext` 一律走 `textfile='…'` + `cwd=workdir` + 相对路径（`style_v5.py:2160/2175/2185`、`style_v2.py:257`、`scenes.py:151-153`、`mock_shot.py:101/137`），`fonts.py` 也把字体复制到 `workdir/fonts` 再用 `fontsdir=fonts`（`render.py:910`）→ 没有 `text='…'` 内联用户文本的调用点（唯一例外 `longform.py:85`，范围外）。
3. **`qc.py` 的两个怀疑都不成立**（见验证明细 7）：`-show_entries` 出现两次是**合并**语义（duration/size 与 stream 都在结果里）；`blackdetect` 计数用 `re.findall("blackdetect")` 在非黑片上返回 **0**，不会把过滤器名算成黑段。
4. **`h3_comfy.generate` 的证据链是严谨的**：缺 CLI → 直接 raise（`:578-581`）；`prompt_id` 必须存在（`:616-620`）；`meta_ok` 不过就 raise（`:682-683`）；`assert_unique_prompt_ids` 防重复；`normalize_clip` 用 `tpad=clone + trim` 保证精确时长（`:720-725`）。**问题在于 HQ 主线栈没用它**（P1-4）。
5. **`qc_v3.evaluate` 的硬门是真的**：`h3_coverage`（`:344-353`）、`prompt_id` 唯一性（`:373-374`）、`workflow/seed` 可追溯（`:360-375`）、黑帧（`:326-334`）、字幕覆盖（`:336-342`）都会 fail 而不是"记一笔"。
6. **`motion2d._VideoWriter` 的编码参数与失败处理正确**：`-nostdin -y`、stdin 直通、`close()` 检查 `code != 0` 抛 `V5Error` 并带 stderr 尾部（`:3237-3250`）；`render_shot` 对 `duration<=0`/`fps<=0`/`fps>120` 都显式 raise（`:3314-3321`）+ 角色上限 12（`:3341`）。
7. **`render_v5` 的"拒绝用旧素材出片"是到位的**（F8）：`video_raw` 与 `narration` 同步失败都 raise，不再静默吃上一轮文件。
8. **`tts_breeze` 的服务生命周期**：`start_server` 会先 `free_comfy_gpu()` 再起（`:161-166`），`stop_server` 按命令行匹配（`:208-227`），`server_status` 报 `model_present`。渲染完主动停服释放显存（`render_v5.py:1247-1253`）。
9. **`v5_scene_art.generate_scene_plate` 没有静默兜底**：ComfyUI 不返回图 → `raise V5SceneArtError`（`:709-712`），写盘走 `tmp_png.replace(png)` 原子替换（`:715-717`），meta 记 `prompt_id/gen_sec`。只漏了采样参数比较（P2-13）。
10. **`mouth_sync` 的边界安全**：空音频 → `n_frames=1`、`floor/peak=0`、`span=1e-4` → 全 closed，`coverage_ratio=0` → 对白镜会判 `lip_ok=False`（正确的失败方向），不会除零。
11. **`postfx.apply` 的输入输出分离**：`unique_fill.py:370-371` 传 `out=work/postfx.mp4`（≠ `src`），`out.replace(src)` 不会造成 ffmpeg 读写同一文件。

---

## 附：建议的处理顺序

1. **P0-1**（一句台词污染另一句的配音，已实证）——改动最小、收益最大。
2. **P1-1 / P1-2 / P1-3**（HQ 三个缓存/落盘判定）——同一根因家族，建议一次性引入「内容指纹 + per-shot meta」。
3. **P1-4 / P2-15**（证据链与 QC 门）——让上面三处的问题以后能被 QC 拦住。
4. **P1-5**（plate 缺失）——决定是补资产还是让老栈可降级，二选一必须显式。
5. **P1-6 / P1-7**（内存）与 **P1-8**（Muse 可用性闸门）。
6. **P1-9 / P2-1 / P2-7**（时长合同）——用户在意的"片长不对"。
