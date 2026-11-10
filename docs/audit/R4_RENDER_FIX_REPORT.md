# R4 · 渲染管线与周边脚本整改（fix-render）

范围：`pipeline/{templates3d_hq,tts_breeze,qc_v5,render_v5,muse_video,lipsync,utils}.py` + `pipeline/fingerprint.py`（新增）
+ `scripts/{expand_3d_shots,rebuild_all_looks_5min,clear_stale_render_urls,fix_loudness,run_episode}.py`
+ `pipeline/agents/registry.py::muse_video`（Lead 追加授权，仅该函数）
依据：`docs/audit/AUDIT_render.md`（P0-1 / P1-1…P1-9）、`docs/audit/AUDIT_extras.md`（P0-1 / P1-1…P1-4）、`docs/audit/REVIEW_ROUND3.md`。
**未改** `pipeline/templates3d.py`（Lead 已改时间线解析），未新建 git 提交，未跑 GPU / 未起 Breeze·Muse 服务 / 未发外网。

---

## 0. 一句话结果

| 项 | 改前 | 改后 |
|---|---|---|
| 全量测试 `pytest tests pipeline/tests -q` | **13 failed / 488 passed / 57 skipped** | **505 passed / 57 skipped / 0 failed** |
| 缓存判据 | 「文件在 + 体积够大」 | **输入指纹**（`<产物>.meta.json`，SHA1 前缀 16 位） |
| TTS 文件名 | `streamb_<role>_<idx>.wav`（批内下标） | `streamb_<role>_<sha1(text)[:10]>.wav`（内容寻址） |
| `muse_video.available()` | 恒 `ok:true` + `apimart_muse` | `ok = any(configured)`；未配置 `ok:false` + `provider:"stub"` + `checks[].missing` |
| `--duration` | 只是"目标"，7.8s/30s 也能全绿 | 编码后硬门：`actual < target*0.6` → `raise`（期望/实际/差值落 `duration_gate.json`） |

---

## 1. P0-3 · TTS 文件名按下标 → 内容寻址

| 位置 | 改法 |
|---|---|
| `pipeline/tts_breeze.py:109-125` | 新增 `STREAM_PREFIX` / `text_sha1(text)`（sha1 前 10 位）/ `content_wav_path(out_dir, role, text)` / `existing_content_wav(...)` |
| `pipeline/tts_breeze.py:299-439` | `synthesize_batch` 重构为三步：① 按内容寻址分流，**已存在同名文件直接复用（不覆盖、也不提交合成）**；② 只把缺的句子按 `MAX_BATCH_TEXTS` 分批提交；③ 按原始下标回填（含批内重复句只合成一次）。结果项新增 `text_sha1 / bytes / voice_sig / reused` |
| `pipeline/tts_breeze.py:441-499` | 每条 wav 另写 `<wav>.meta.json`（`text / role / text_sha1 / voice_sig / seed / cfg_scale / bytes`）；`duration` 一律 `probe_duration` 现读文件 |
| `pipeline/render_v5.py:46-86` | 新增 `_voice_entry_ok(info, text, spec)`：**文件在 + `bytes` 未变 + `text_sha1` 一致 + `voice_key` 一致** 才算命中，否则重新合成（P0-3 的第二半："缓存 value 加 text_sha1 并在命中时校验"） |
| `pipeline/render_v5.py:344-348` | 命中缓存时按文件真实时长重算 `speech`（旧值会被 `atrim` 用来截断新音频） |

**读取方同步**：全仓 `grep streamb_` 只有 `tts_breeze.py` 生成；`convert_result_to_mix`（`tts_breeze.py:526`）与 `voiced.py:220` 都用 `Path(...).stem` 派生混音名 → 自动跟随，无需改调用点。旧文件**不删**（保留一轮便于对比）。

**自证** `python docs/audit/_tools/r4_tts_content_addressing.py`（假 HTTP 层跑真实 `synthesize`）：
```
run1: 第一句台词内容比较长 → streamb_laozhou_32a0da7314.wav (5324B)
      第二句               → streamb_laozhou_f8c7a38fb8.wav (1964B)
run2: 只改第 1 句 → 新句落到 streamb_laozhou_ab6045b09f.wav (5804B)；
      A 的文件仍是 5324B（未被覆盖）；第二句 reused=true（省一次合成）
run3: 原样重跑 → 两句 reused 全 True
旧命名（对照）: streamb_laozhou_000.wav / _001.wav —— run2 的 A2 必然写 000
```

---

## 2. 渲染缓存假命中 → 统一输入指纹

### 2.1 新增 `pipeline/fingerprint.py`
- `signature(*parts, **fields)`：稳定序列化（排序 key、浮点 4 位量化）→ SHA1 前 16 位；
- `meta_path_for(asset)`：`clips/a.mp4 → clips/a.meta.json`（**与 `h3_comfy.meta_path_for` 同一约定**，两栈证据链同名同位置）；
- `meta_matches(asset, sig)`：**产物存在 + meta 指纹一致**；体积只用于排除空文件（`MIN_ASSET_BYTES`）；
- `write_meta()` 原子写（走 `utils.write_json` 的 `.tmp→replace`）；
- 三个"参数集"构造器（给别的模块直接复用，避免各自漏项）：
  `keyframe_signature(... steps/cfg/sampler/scheduler ...)`、`scene_plate_signature(...)`、`layerset_signature(... duration, seed)`。

### 2.2 `pipeline/templates3d_hq.py`
| 位置 | 改法 |
|---|---|
| `:460-509` | `_cache_hit(asset, sig)`：指纹一致 + `meta.bytes == 实际体积`（发现"产物被截断/改写但 meta 还在"）；未命中原因记进 `LOG_HIT_MISS` 并写进运行报告 |
| `:526-700` `_generate_keyframe` | 缓存判定改成 `keyframe_signature(...)`：**prompt + 负面词 + seed + ckpt + cfg + steps + sampler + scheduler + IP 强度/结束/权重类型 + 抽卡数 + 选卡 + 尺寸**；`_render` 的体积短路改为**每张候选图各自的指纹**；`_render` 成功后**立即**写该图 meta；`_generate_keyframe` 末尾的 meta 只在真渲之后写（"新签名 + 旧图"不再可能出现） |
| `:801-860` `gen_t2va` | 命中判据 = `sha1(prompt+seed+width+height+length+duration+mode+look+style_id+broll+tid+shot)`；新增 `force` 参数（`--force` 现在真的能重渲 H3 镜头，P2-4）；渲完写 `<out>.meta.json`（`prompt_id / workflow / seed / length / duration / gen_sec / prompt`） |
| `:891-956` `gen_ref2va` | 同上，指纹额外含**首帧内容签名 + 音频内容签名**；首帧抽帧改「临时名 → `os.replace`」并在 `returncode != 0` 时报错（不再静默用旧帧）；解析 CLI 最后一行 JSON 拿到真实 `prompt_id / workflow / gen_sec` 写入 meta |
| `:1121-1195` `main()` | 每渲一镜写 `shots/<sid>.meta.json`（**字段名与 `h3_comfy` 证据链同名**：`h3/ok/mode/workflow/workflow_file/prompt_id/seed/duration/length/width/height/fps/ref_image/audio/shot_id/shot_index/prompt/gen_sec/path`，另加 `template/style_mode/style_2d/look/source/clip_fingerprint/fallback`）；累加 `fallbacks[]`（关键帧/锁脸回退不再只有一行日志）；写 `outputs/templates_3d/<tid>/work/hq_run.json`（含 `meta_ok / missing_prompt_id / duplicate_ids / cache_miss / items`） |

**自证** `python docs/audit/_tools/r4_cache_fingerprint_proof.py`（假 ComfyUI + 假文件，只写 `tmp/`）：
```
A. gen_t2va: 同输入 submits=1（命中）；改 prompt → submits=2（未命中）；
   对照旧判据 size>200_000 = True（同一份文件改 prompt 也"命中" = 假命中）
B. 关键帧: 同指纹命中 True；改 prompt False；只改 steps/cfg/sampler/scheduler False；
   产物被截断 False（旧判据 size>100_000 = True → 假命中/毒化）
C. per-shot meta: shots/v5_test01.meta.json → h3_comfy.load_meta 命中、meta_ok=True、
   assert_unique_prompt_ids → ['pid-r4-proof']（两栈证据链真正对齐）
D. 静态断言: _generate_keyframe 命中分支 return 之前没有任何 meta 写入；
   `st_size > 100_000` 短路已不存在
```

### 2.3 仍不在我写权限内的两条（同一根因，给 owner 的一行补丁）
| 位置 | 现状 | 建议补丁 |
|---|---|---|
| `pipeline/v5_scene_art.py:620-631` `_meta_matches` | 只比 `prompt/negative/seed/size/model`，漏 **steps/cfg/sampler/scheduler** | 签名加 4 个可选参数并逐个比对（`if value is not None and str(meta.get(key)) != str(value): return False`），由 `generate_scene_plate` 传入（它在作用域里有）；或直接用 `fingerprint.scene_plate_signature(...)` 写/比指纹 |
| `pipeline/motion2d.py:914-919` `_get_layerset` | `key = id(layers)`，`duration/seed` 只进了 camspec 之外的比较、**没进键** | `key = (id(layers), round(float(duration), 3), int(seed))` 一行；或 `fingerprint.layerset_signature(...)` |

---

## 3. QC 弱门

| 位置 | 改法 |
|---|---|
| `pipeline/qc_v5.py:130-171` 新增 `_lip_sync_check` | 口型门不再 fail-open：有对白镜但 `per_shot` 为空 → `ok=False, reason="no_data"`；部分镜取数失败 → `partial_data`；无对白镜 → `not_applicable` 通过。字段：`reason/provider/shots/no_speech/low_coverage/per_shot/failures` |
| `pipeline/qc_v5.py:296-312` | `checks["lip_sync"]` **恒存在**；新增 `checks["duration_gate"]`（把期望/实际/下限/差值/比例写进报告） |
| `pipeline/render_v5.py:1216-1262` `run_qc` | `mouth_sync` 导入失败 / `audio` 缺失 / `envelope_timeline` 抛错 **全部 try + 记 `failures[]`**（P2-17：成片已生成却因 QC 抛错而整条 run 失败）；`reason` 归一到 `no_speech/no_data/partial_data/low_coverage`；`lip_ok` 必须有真实 `per_shot` 才为真 |
| `pipeline/render_v5.py:1195-1213` `_duration_gate` + `:1388-1402` | 时长硬门：`MIN_DURATION_RATIO = 0.6`（可用 `V5_MIN_DURATION_RATIO` 覆盖）；`actual < target*0.6` → 写 `duration_gate.json` + `emit_json_line` + `raise RenderV5Error`；通过时 `LOG.info` 期望/实际/下限 |

> 说明：`custom_holo_talk --duration 30` 现在会**失败**（7.8s < 18s 下限）——这是本条的验收行为。根因（`v5_story.build_plan` 不做 target 配比填充）不在我的写权限内。

---

## 4. muse / 假能力

| 位置 | 改法 |
|---|---|
| `pipeline/muse_video.py:71-190` `available()` | 每个 provider 显式分支：`configured` 由必需 env/文件决定（`MUSEV_CMD`、权重目录、`APIMART_API_KEY`、`REPLICATE_API_TOKEN`、`USEAPI_TOKEN+USEAPI_ACCOUNT`、`MUSE_API_URL`、`MINIMAX_API_KEY`、`METASO_*`，**env 文件也算**）；`missing[]` 列出缺什么；`ok = any(configured)`；`configured_providers[]`；`stub` 单独标 `configured:false, available:true`；无配置时 `provider` 回落 `"stub"`（让调用方走 skipped 分支）但 `ok=false` |
| `pipeline/muse_video.py:487-560` `_local_generate` | `shlex.split(posix=False)`（保留 Windows 反斜杠）→ 去成对引号 → 逐 token 替换占位符（**不经过 `str.format`，其它 `{...}` 原样保留不崩**）→ 列表参数 `subprocess.run(argv, timeout=...)`（`MUSEV_TIMEOUT`/`MUSEPOSE_TIMEOUT`，默认 3600），`TimeoutExpired/OSError` 转可读 `RuntimeError`；日志用 `shlex.quote` 回显。**模板语法不变**（`MUSEV_CMD` 仍是 `{prompt}` 占位） |
| `pipeline/agents/registry.py:561-620` `muse_video`（Lead 追加授权） | ① `available().ok is False` 且没显式 provider → `{"skipped": True, "reason": "未配置任何 Muse provider（<按 checks 列缺项>）"}`；② 只剩 stub 且没 `allow_stub` → skip（原逻辑）；③ **没有 prompt/image/pose → skip**（旧实现会拿模板 label 当提示词去调付费接口；`contract/v6/workflows/*.json` 的 `musev` 阶段 params 都是空串） |

**自证** `python docs/audit/_tools/r4_muse_available.py`：
```
1) 本机真实配置: ok=true, provider=apimart_muse, configured=[apimart_muse, metaso_h3]（key 在 backend/data/*.env）
2) 清空 env + env 文件: ok=false, provider=stub, configured=[]，checks 里 missing 逐条列出
3) 模板 → argv: ".venv\Scripts\python.exe" | scripts/inference/text2video.py | --prompt | 他说: 你好 & 再见 | 100% 完成 | ...  （&/|/%/空格/反斜杠都不被 shell 解析）
4) 未配置时 registry.muse_video → skipped=True, reason 含各 provider 缺项
5) 已配置但 params={} → skipped=True（不发请求）
```

---

## 5. 脚本破坏性

### 5.1 `scripts/expand_3d_shots.py`
- `:61-73` `next_id_index()`：`max(已有 v5_xNNN)+1`；新 id `v5_x{next:03d}`（旧 `v5_e{added+1}` 每次从 001 重排 → 13/30 份 brief 重复 id）；
- **幂等**：`len(shots) >= target` → `skipped`，不写盘（重复跑不再产生新镜头）；
- 写盘前三道断言（`:123-130`）：新 id 自身唯一 / 不与已有 id 冲突 / **写盘后重复集合必须等于写盘前**（本脚本绝不制造新的重复）；
- `.bak` 每次覆盖写（保留最近一次原文）；
- 新增 `--strict-ids`（已有重复 id 时判失败，供数据修复前当门用）；失败行返回 `ok:false` → `main` 返回 1。

### 5.2 `scripts/rebuild_all_looks_5min.py`
- **永不删生产目录**：渲染前 `shots/` → `shots_bak_<stamp>/`（`os.replace`，保留）；HQ 渲进干净的 `shots/`；成功后 `shots/` → `shots_<look>/`（旧 `shots_<look>` → `shots_<look>_bak_<stamp>`），再把 `shots_bak_<stamp>/` **换回** `shots/`（生产目录回到跑之前的样子）；
- 失败自动回滚：半成品留 `shots_failed_<stamp>/`，`shots/` 复原；`templates3d_hq` 退出码非 0 或没产出镜头都算失败（不再"有镜头就 compose"）；
- `--dry-run` 打印 plan（`live/target/live_backup/old_target_backup/failed_dir`）；`--force` 透传（不再无条件 `--force`）；
- episode 分文件：`work/episode_<look>.json` + `work/looks.json` 索引（`active_look`、每 look 的 `shots_subdir/episode/final`），`work/episode.json` 仍写（兼容旧读者）；
- `main` 单模板失败不拖垮整批，最后返回 1。

### 5.3 `scripts/clear_stale_render_urls.py`（重写；`__main__` 守卫在 `:128`）
- `main(argv)` + `if __name__ == "__main__"`：**import 无副作用**（实测 `exec_module` 不改库）；
- **默认 dry-run**，写库必须 `--apply`；待改清单先写 `tmp/clear_stale_render_urls.json`；
- `sys.path` 用 `Path(__file__).resolve().parents[1] / "backend"`（与 cwd 无关）；
- 只处理 `/media/...` 本地 URL；外部 `http(s)/相对` 一律跳过并计数 `external_skipped`；
- `collect()` 只读、`apply_changes(ids)` 才 `commit`，异常 `rollback()`；`--limit` 限条数。

### 5.4 `scripts/fix_loudness.py`
- `_backup(final) -> (path, created_now)`；**audio-only 路径先备份再 `audio_only_fix`**（与 rebuild 路径一致）；
- 复用旧 `.preloud.mp4` 时核一次音量：`backup_lufs` vs `before.lufs`（≤0.5 才算真原件）→ `backup_matches_before`；
- 报告新增 `backup_created / backup_lufs / backup_matches_before / backup_valid`。

### 5.5 `scripts/run_episode.py`
- `code != 0` → **不 compose**，返回 `ok:false / stage:"render" / exit`（`main` 返回 1）；渲染成功但 `shots/` 为空同样失败；
- `--force` 由参数决定（默认增量），不再写死；
- `episode.json` 写前备份 `.bak`（两份路径都备），返回 `episodes[]` 便于追溯；
- `main` 返回 `0 if ok else 1`。

---

## 6. `utils.read_json` 编码兜底（低优先项，已做）
`pipeline/utils.py:138-186`：`utf-8-sig → utf-8 →（**仅 <8KB**）gbk`（打 `LOG.warning`）；gbk 也失败 → `LOG.warning` + **返回 default**（不再对每个编码继续"猜"）；≥8KB 的损坏文件不再尝试 gbk。
自证 `python docs/audit/_tools/r4_read_json_encoding.py`：小 gbk 文件读出中文（有 warning）；20KB gbk 文件返回 DEFAULT；损坏/非法字节返回 DEFAULT；UTF-8 BOM 正常。

---

## 7. 测试账（前后）
```
改前（本会话基线）: 13 failed, 488 passed, 57 skipped   （110s）
  失败集中在 scenes/plate/cutout（P0-1 底板缺失）+ lipsync 契约 + muse_video skipped + frontend live
最终:              505 passed, 57 skipped, 0 failed       （92s）
  其中我修掉的两条（Lead 点名的 P1-3）：
    pipeline/tests/test_lipsync_unit.py::test_measure_sync_returns_contract_dict → `measure_sync` 现在永远返回 {ok,reason,syncnet,provider,device}
    tests/test_v53_agents.py::test_muse_video_skips_without_provider → 工具入口未配置/空选题均 skipped（不抛）
未放宽任何既有断言；未删除任何测试。
```

---

## 8. 残留风险 / 需要别人确认
1. **TTS 文件名只含文本**（按要求）：同一 role 改**音色**时文件名不变 → 由 `<wav>.meta.json` 的 `voice_sig` + `render_v5._voice_entry_ok` 判失效；但若用户回滚音色且旧缓存条目仍在，可能复用到新音色的文件。彻底隔离需要把 `voice_sig` 放进文件名（会偏离 Lead 指定的命名）。
2. **本地 Muse 模板不再支持 shell 语义**（管道/重定向/`&&`）。占位符语法、引号加分号用法不变；需要 shell 语义请在模板里指向一个 `.bat/.ps1`。
3. **时长门会硬失败**短模板（`custom_holo_talk` 30s→7.8s、`real_street` 35s→26s）。根治要改 `v5_story.build_plan`（不在我的写权限）。
4. `_ensure_card_shots` 生成的片头/片尾卡仍不写 per-shot meta（`h3_comfy` 会把无 meta/`h3:false` 跳过，属于预期）。
5. `pipeline/agents/registry.py` 我只改了 `muse_video` + 新增 `_muse_missing`；该文件正被别人并行修改，合并时请以最新内容为准。
6. `_ensure_card_shots` 现在优先用 `t3.resolve_timeline(tid)` 取时间线（Lead 的 V49 改动），拿不到才退回 `t3.load_episode` —— 避免把卡片补到 assemble 不会读的那条时间线上。

---

## 9. 顺手修掉的两个"必现"缺陷（不在原清单，但同文件同链路）
1. **`templates3d_hq.py` 缺 `from pipeline import utils`**：工作树里 `_identity_keyframe` / `_ensure_silence` / `_render_meta` 都在用 `utils.ensure_dir`，而模块头部只有 `local` / `t3` 两个 import → `--identity keyframe` 每一镜都会 `NameError`，被 `main` 的 `except Exception` 吞成「锁脸失败，回退 T2VA」。已补 import（`templates3d_hq.py:33`）。
2. **`gen_ref2va` 抽首帧静默失败**：`subprocess.run(..., check=False)` 且不校验产物 → 抽帧失败时用上一轮的旧帧做 ref2va（串镜）。现在写临时名 → 校验 `returncode` 与产物 → `os.replace`；失败直接 `raise`。

