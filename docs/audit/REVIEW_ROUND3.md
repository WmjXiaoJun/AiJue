# REVIEW_ROUND3 — AiJue 第三轮全量审查（功能 + 代码现状）

- **审查时间**：2026-11-06 23:20–23:45（Asia/Shanghai）
- **基线**：工作树有 30+ 未提交 WIP（`git status`），`backend/app/main.py` 在审查期间被外部改写两次（mtime 23:27:55 → 23:28:59），`pipeline/muse_video.py` 亦被并发改写（617→630→691 行）。本报告所有行号以 23:45 的磁盘版本为准。
- **方法**：6 路并行分域审查（backend / agents+合同 / render / frontend / 数据+测试+文档 / 周边包+脚本）+ Lead 亲自跑的可执行探针。**全部只读**：未改 `backend/`、`pipeline/`、`contract/`、`frontend/src/`、`outputs/` 的任何文件。
- **产物**：本文件 = 汇总；六份分报告在 `docs/audit/AUDIT_*.md`；复现脚本在 `docs/audit/_tools/`；Lead 的探针在 `tmp/audit_*.py` + `tmp/lead_findings.md`。

---

## 0. 结论速览

1. **代码质量整体不差，坏在"最后一公里"**：`pytest tests` 311 绿、前端 `tsc --noEmit` 0 错、前端 115 条 API 调用对后端 **0 缺口**、33 份工作流 dry-run **33/33 全是 planned**、370 个合同 JSON **全部合法**。真正的缺陷几乎都集中在**产出可信度**上——渲染缓存假命中、QC 弱门、skipped 记成功、静态托管吞异常。
2. **今天这套系统跑不出一部片**：21/21 内建模板的场景底板（plate）全丢，V5 栈在 `scenes.py:380` **硬失败**；HQ 短片的镜头 id 空间与时间线 id 空间**分裂**，`assemble` 必然抛 `RuntimeError`（已实证）；`outputs/` 里 31 个 `templates_3d` 目录 **0 个 final.mp4**。属于**数据/资产断链**，不是代码写错。
3. **最危险的 4 类"静默错误"**：① 改台词重跑会**覆盖别人的配音**（`tts_breeze` 文件名按下标）；② 缓存只判"文件存在/体积"，**改 prompt/seed 照样复用旧片**；③ 阶段被跳过仍记 `ok`、QC 5 项弱门 fail-open；④ 发布到 TikTok/Instagram **永远公开**（`privacy_level` 硬编码），这是全仓唯一不可撤回的外部副作用。
4. **前两轮 91 条已完成 61 条**（+11 部分修、8 未修、1 修出反向、10 超范围/降级）。本轮新增 **128 条**（去重后约 105），其中 **4 条 P0 / 21 条 P1**。
5. **最值得先做的三件事**（合计 <1 天，能把"跑不出片"变成"能跑出片"）：修 `expand_3d_shots.py` 的 id 生成 → 重生成 `outputs/scene_plates/` 底板 → 统一 `episode.json` 与 brief 的 id 空间并加一致性断言。

---

## 1. 功能可用性矩阵（"所有功能现在到底能不能用"）

| 功能域 | 结论 | 卡在哪（证据） |
|---|---|---|
| 建项目 / 台本 / 角色 | ✅ 可用 | 26 角色资产、24 音色、场景/风格引用 **0 缺失**（data-tests A）；`POST /api/projects` 正常 |
| └ `format_id` 校验 | ❌ 缺 | `POST /api/projects {"format_id":"no_such_format"}` → **200**，照样建项目（Lead 实测）；前端 16 项白名单恰好掩盖了这个洞 |
| 场景底板 plate | ❌ 全丢 | `outputs/scene_plates/` 只剩 `meta.json`/`meta_3d.json`/`meta_styles.json`，**0 张 png**；21/21 模板解析出的 plate 全部 MISSING（`tmp/audit_plate_resolve2.py`） |
| **V5 渲染栈**（21 内建模板） | ❌ 不可用 | `scenes.compose_episode` 在 plate 缺失时 **raise**（`scenes.py:380-381`），不是降级；`outputs/templates_v5` 0/34 有 final |
| **HQ 短片栈**（12 custom / 11 短片） | ❌ 不可用 | brief 89 镜 vs 时间线 4–6 镜，两套 id 空间；`assemble` 读 `templates_v5/<tid>/work/episode.json` 却从 `templates_3d/<tid>/shots/` 取片 → 硬失败（Lead 实证 `RuntimeError (exit 4294967294)`） |
| TTS / 配音 | ⚠️ 能用但会错 | `tts_breeze.py:357` 输出名 `streamb_<role>_<idx>.wav` 只含批次下标 → 改一句台词重跑就覆盖别的台词音频；`voice_cache` 的 key 是**原文串**，所以字幕对、声音错 |
| 剪辑 / 字幕 / 包装 | ⚠️ 能用有回退 | 实测 `V5.4 editing 不可用，回退单转场`；`xfade_chain` 报 `total=303.000s vs 期望 282.350s`（Δ=20.65s）仍继续裁剪 |
| H3（图生视频） | ⛔ 阶段默认关 | 33 份工作流里 `h3.enabled=false`；`outputs/h3` 仅 16 个文件，无成片 |
| Muse 口型（MuseTalk） | ⚠️ 能用但空跑 | `/api/v6/muse/status` 200、musetalk installed=true；但 `muse.depends=[]` + skipped 记 ok → `--stages muse` **100% 空跑却全绿** |
| Muse 视频（MuseV/MusePose/云） | ❌ 假可用 | `muse_video.available()` **恒返回 `ok:true` + `order[0]`**（清空所有配置仍成立）；`_local_generate` 用 `shell=True` + `str.format` 拼命令，无 timeout |
| QC | ❌ 弱门 | `evaluate_hq_qc` 只有 5 项（存在/时长/海报/音轨/分辨率），`lip_sync` fail-open；时长门只有下界 `0.6×target`，14.12s 能过 8.47s 的目标；HQ 不写 `qc_report.json` |
| 发布 | ⚠️ 能用但危险 | `privacy_level` 硬编码 `PUBLIC_TO_EVERYONE`（TikTok/IG 永远公开）；5 条发布记录 4 条卡在 `prechecked`、`video_url` 全指向不存在的文件 |
| 系列 / 季 | ⚠️ API 齐、规则空转 | `/api/v6/agent/series` 200；但编排层**完全不校验** `season.rules`（只有 `check_seasons.py` 私下校验，双份逻辑）；`with_muse` 会静默把 dry_run 翻成真跑 |
| 前端 | ✅ 类型健康 / ⚠️ 一处竞态 | `tsc --noEmit` **EXIT=0**（5.9s，全仓 `any`=0、`dangerouslySetInnerHTML`=0）；115 条调用 0 缺口；`App.tsx` 全局导航无请求守卫（连点"先点的赢"，未浏览器复现） |
| 测试 | ❌ 两套都红 | `pytest tests` = **4 failed / 311 passed / 57 skipped**；`pytest pipeline/tests` = **16 failed / 170 passed**；仓库根直接 `pytest` 会递归 `tmp/`(527 py) + `local_models/`(4345) 实际挂死 |
| 周边脚本 | ❌ 有破坏性 | `rebuild_all_looks_5min.py:69-82` `rmtree` 生产镜头目录后 rename、无备份；`clear_stale_render_urls.py` import 即改生产库；`fix_loudness.py` audio-only 路径先改后备份 |

---

## 2. P0（4 条，任何一条都足以让交付不可信）

### P0-1 · 21/21 内建模板的场景底板全丢 → V5 栈全员硬失败，且只补一张就会串景
- **位置**：消费端 `pipeline/scenes.py:380-381`（`raise RuntimeError("缺少场景 plate")`）、`pipeline/style_v2.py:108-115`、`pipeline/stage.py:49`；数据端 `outputs/scene_plates/`（只剩 3 个 meta json）
- **证据**（Lead 实跑 `tmp/audit_plate_resolve2.py`，走真实 `scene_map → profile → plate_path` 链）：
  ```
  模板 21；plate 命中 0；缺失 21
    classics      MISSING expected=history_study.png     level=missing fallback=True
    tonight_show  MISSING expected=late_night_desk.png   level=missing fallback=True
    ...（21/21 全 MISSING）
  scene_plates 目录内容: meta.json 14077 / meta_3d.json 12631 / meta_styles.json 25360
  ```
- **影响**：V5 栈（21 个内建模板）**一个都跑不起来**；`tests/test_v3_acceptance.py::test_templates_validate_strict` 16/16 红由此而来。
- **陷阱**：`style_v2.plate_path` 里有 3 个硬编码 alt 名（`late_night_desk` / `history_study` / `roundtable_studio`）。**只补其中一张**会让原本该用专属底板的模板全部静默共用同一张背景 —— 修的时候必须一次性补齐 14 张专属名。
- **修法**：`python scripts/gen_scene_plates.py`（需 SDXL/ComfyUI）重生成；先跑 `python -c` 版解析器确认 21/21 命中；把 `fallback=True` 从"静默"改成"QC 报警"。

### P0-2 · 两栈镜头 id 空间分裂 + brief 内 id 重复 → HQ 短片 assemble 必失败、GPU 白烧
- **位置**：`pipeline/templates3d.py:352-356`（时间线读 `V5_OUT/<tid>/work/episode.json`）、`:432/:513/:516`（镜头取 `OUT3D/<tid>/shots/`）；`pipeline/templates3d_hq.py:913/919/931/937/980`；根因脚本 `scripts/expand_3d_shots.py:60/67/83-92`
- **证据**（Lead 实跑 `tmp/audit_dup_impact.py`）：
  ```
  tonight_show:            episode 78 镜（唯一 id 78，303.00s）；brief 76 镜
                           episode 有而 brief 无 = 67（fill_000…）
                           brief 有而 episode 无 = 65（v5_e001…）
  custom_tpl_v46_duologue: episode 6 镜（19.14s）；brief 93 镜 → 87 镜渲染后永不入片
  templates3d_hq.py:919/931/980  out_shots/f"{shot['id']}.mp4"   → 同 id 写同一文件
  templates3d_hq.py:937          seed = crc32(tid + shot["id"])  → 同 id 同 seed
  ```
  重复 id（13/30 份 brief，`tonight_show`/`roundtable`/`history_mystery` 各 9×`v5_e001`）根因：`clone["id"] = f"v5_e{added+1:03d}"` 每次运行从 001 重排，且 `.bak` 只建一次、永远 append（不幂等）。HEAD 版本 0 重复 → 是本机跑脚本造成的。
- **影响**：① `assemble` 拿 78 个 id 去 `shots/` 里找，HQ 从来没渲过这些 id → **硬失败**（Lead 实测：`V5.4 editing 不可用，回退单转场: 剪辑片段不存在 …v5_card_title.mp4` → `RuntimeError: 命令失败 (exit 4294967294)` → `final.mp4 产出: False`）；② 短片栈反向：89 镜只服务 4–6 镜的成片，**≥90% GPU 白烧**（单集 6–10 分钟）。
- **修法**：① `expand_3d_shots.py` 改 `id = f"v5_x{max_existing+1:03d}"` 并在写盘前 assert 唯一；② 让 episode 与 brief **共用一份 id 源**（brief 由 episode 派生时回填 `shot_id`，而不是各行其是）；③ `assemble` 前加断言 `set(episode ids) == set(shots/*.mp4)`，不一致直接报可读错误而不是喂给 ffmpeg。

### P0-3 · 配音文件名只含批次下标 → 改一句台词重跑会覆盖别的台词音频
- **位置**：`pipeline/tts_breeze.py:357`（`streamb_{role}_{idx:03d}.wav`）、消费端 `pipeline/render_v5.py:207/209/257/294`（`voice_cache` key = `f"{cid}|{spec['key']}|{句子原文}"`）
- **证据**：静态读码 + 伪造 HTTP 层实证（`streamb_laozhou_000.wav` 5324B → 5804B）。
- **影响**：字幕取 `shot["line"]` 所以**字幕是对的**，但"句子 A"那条记录的 `path` 被"句子 A2"的合成结果覆盖 → 声音错、`atrim` 还会按旧时长截断；QC 的 `voice_fixed` 只看 `voice_key`，**查不出来**。
- **修法**：文件名改 `streamb_{role}_{sha1(text)[:8]}.wav`（与 cache key 同源）；旧文件保留一轮再清。

### P0-4 · 交付面为零：所有成片消失 + 一个脚本会继续删
- **证据**（Lead + data-tests 交叉实测）：
  ```
  outputs/templates_3d : 31 个目录，final.mp4 = 0
  outputs/templates_2d :  9 个目录，final.mp4 = 0
  outputs/templates_v3 : 18 个目录，final.mp4 = 0
  outputs/templates_v5 : 34 个目录，final.mp4 = 0
  outputs/series       : 只剩 concat.txt / concat_9x16.txt / manifest*.json，series.mp4 与 title_card.mp4 都不在
  outputs/scene_plates : 0 张 png（只剩 3 个 meta json）
  → 两套测试 20 条红里，17 条根因就是这些资产/产物缺失
  ```
- **成因之一**：`scripts/rebuild_all_looks_5min.py:69-82` 先 `rmtree(outputs/templates_3d/<tid>/shots)` 再 `live.rename(target)`，**删掉全栈共用的生产镜头目录且无备份**，渲染失败不恢复；跑完 `--looks 2d_anim` 后 `shots/` 里躺的是 2D 镜头，后续 `--reuse-shots` 会把 2D 当 3D 复用。
- **修法**：脚本改为 `shots_<look>/` 分目录 + 成功后原子切换 + 失败回滚；补片按 `outputs/templates_3d/<tid>/final_300s_*.mp4`（classics 已存在 89MB/31MB/97MB 三份，需产品确认是否为当前交付主线）决定范围。

---

## 3. P1（21 条，按域）

### 后端 / API（3）
1. **`season/{sid}` 路径穿越**：`v6_agent.py:278-282`、`v5_series.py:72-76`、`main.py:352-357`（`{id}.json` 直接拼）。实测 `GET /api/v6/agent/season/..%5C..%5C..%5Ccontract%5Cdemo_episode/status` → **200 回吐文件内容**；Windows 反斜杠绕过 `..` 过滤。（agents 侧独立复现 `..%5C..%5Cv6%5Cart%5Cpose_policy` → 200）
2. **`--reload` 下保存一次文件就把正在跑的 GPU 任务判成 failed**：`backend/run.ps1:25` 默认 `--reload`；`main.py:206` 导入期 `_recover_stale_jobs()` 把 `queued/running` 全标 failed（"服务重启，任务中断"）。仓库是活跃开发状态，一次保存即触发。
3. **非数字参数**：`longform.py:80` `duration="abc"` → **500**；`v6_agent.py:187-188` → 200 起 job 后立刻 failed（应 422）。

### 编排 / 合同（7）
4. **`--stages publish` 自动重跑 GPU 阶段**：`orchestrator.py:39-63` 依赖闭包；`--stages qc` 实测展开成全链 —— 与 AGENTS.md「只跑必要阶段」相反。
5. **同秒 run_id 撞车**：`orchestrator.py:79-81` `%Y%m%d_%H%M%S + "_" + tid` + `mkdir(exist_ok=True)` + append events。本轮 **23:30:32 抓到真实现场**（`outputs/agent_runs/20261106_233032_tonight_show/` 两次 `start`、`run.json` 被覆盖、`summary.md` 是上一次的）。修法：`os.urandom(4).hex()` 后缀 + `exist_ok=False` 重试。
6. **`with_muse` 静默翻真跑**：季 API 把 `dry_run` 默认从 true 改成 `not with_muse`，并丢弃用户传的 `stages`/`only_missing`。
7. **`muse.depends=[]` + skipped 记 ok**：`--stages muse` / 季 `with_muse` **100% 空跑且全绿**（R1#13 残留）。
8. **`/media` 正则 `{8,40}`**：46 字符 run_id 的证据永久 404；`ID_RE {1,60}` 与媒体路由 `{8,40}` 两套边界不一致。
9. **短片验收门三处口径不符**：`evaluate_hq_qc` 写 `qc_shorts.json`、PLAN 要 `qc_report.json`；PLAN 写 `templates_2d`（实际 `templates_3d`，`templates_2d` 只有 1 个文件）；时长门只有 `0.6×target` 下界无上限。
10. **`_expand` 不拓扑排序**：按 JSON 顺序执行，UI 一调换阶段顺序就"依赖未完成"failed。

### 渲染（7）
11. **HQ T2VA/ref2va 缓存只判体积**（`templates3d_hq.py:735/812`）→ prompt/seed 变了照样复用旧片（F2 未修）。
12. **关键帧 `_render` 体积短路**（`:531-534`）→ 把旧图登记成新 `prompt_sig`，缓存毒化（R2 未修）。
13. **HQ 不写 per-shot meta**（`:980` 只有 `copy2`）→ 完全绕开 `h3_comfy` 的严格证据链，事后无法复核"这镜是不是这么渲的"。
14. **`muse_video.available()` 恒 `ok:true`**（清空所有配置仍 `provider=apimart_muse`）+ `_local_generate` 用 `shell=True` + `str.format` + 无 timeout → 假能力 + 命令注入/空格路径断裂。
15. **`--duration` 不是下限**：`custom_holo_talk` 30s→7.8s、`real_street` 35s→26s，且 QC 拿 7.8 比 7.8 自证全绿。
16. **内存**：`scene_render.smooth_noise` 缓存存全尺寸 float32（64 项 ≈350MB）；`lipsync` 整段解码（600 帧 1024×576 ≈1GB）。
17. **跨目录耦合**：`assemble` 时间线读 `templates_v5`、镜头取 `templates_3d`，两栈产物目录硬绑；`OUT3D/<tid>/work/episode.json` 那份同名文件**无任何代码读**却极易被误改（`tonight_show` 78 镜 vs 11 镜）。

### 周边 / 发布（2）
18. **`upload_post.py:224` `privacy_level="PUBLIC_TO_EVERYONE"` 硬编码**：`--privacy unlisted` 只对 YouTube 生效，TikTok/Instagram **永远公开**；payload 显式带该字段，profile 默认值不兜。映射建议：`public→PUBLIC_TO_EVERYONE`、`private→SELF_ONLY`、`unlisted→MUTUAL_FOLLOW_FRIENDS`；`unlisted` + 非 youtube 时预检直接拒发。
19. **`rebuild_all_looks_5min.py:69-82`**：`rmtree` 生产镜头目录 + rename，无备份、失败不恢复（P0-4 的成因）。

### 前端 / 测试（2）
20. **`App.tsx` 全局导航无请求守卫**：连点两个项目/剧集"先点的赢"，旧响应覆盖新视图并把 hash 一起改回；同仓 10 个页面都有 `wfSeqRef`/`seasonSeqRef`/`inFlightRef`，唯独最外层没有（代码级推断，未浏览器复现）。
21. **测试基础设施**：无 `pytest.ini`/`pyproject`/`setup.cfg` → 无 `testpaths`/`norecursedirs`，仓库根 `pytest` 递归 4763 个外部 `test_*.py`；`--timeout` 未装插件直接报错。修法一行：
    ```ini
    [pytest]
    testpaths = tests pipeline/tests
    norecursedirs = tmp local_models outputs frontend tools .git
    ```

---

## 4. P2 摘要（22 条，详见分报告）

| 域 | 条目 |
|---|---|
| 后端 | `HTTPException.detail` 30 处是对象/其余字符串（前端显示 `[object Object]`）· 绝对路径外泄（`workflow_file`/`run_dir`/`out`）· `/api/events` SSE 在 async 生成器里跑同步 SQLAlchemy，异常吞成 `{}` · `jobs.run_cli` 超时只 `proc.kill()`（Windows 不杀孙进程，同仓 4 处已用 `taskkill /T`）· `jobs.retry_job` 对 running 静默 return 但 API 报 200 · `bool()` 而非 `_as_bool()`（`"false"` 判 True，`v6_agent.py:189/191/541`）· GPU 互斥仍 check-then-act · `_jobs`/日志无上限 · `video_fallback` 按文件名启发式反而把正常本地化版本全判回退（`main.py:2315-2317`，前端 0 消费 → 数据口径问题） |
| 编排 | 僵尸 run（`_expand` 失败留 `status:running`）· 未知模板 KeyError traceback · `save_workflow` 不查环/不查 stack · `PUT workflow` 校验异常 500 · `stage_params` 浅合并 + 未知阶段名静默 · `--start` 覆盖 `--stages` · duration 死代码（`duration or template.duration` 恒取形参 40.0）· `SystemExit` 仍穿透 orchestrator · `art` 默认参数即 ValueError · `write_shorts_report` 对不存在成片报 `qc_ok:true` · 季 `ready` 只看文件不看 QC · `set_voice` 不认 custom 角色 |
| 渲染 | 回退路径未扣转场（实测 Δ=20.65s，`LOG.warning` 应升级为 raise）· `force` 形参未使用 · `--force` 不传给 H3 缓存 · 关键帧失败静默换 T2VA · `_get_layerset` 键漏 duration+seed · `voice_key` 漏 `ref_text` · BGM preset 静默兜底 · `_meta_matches` 漏采样参数 · `lip_sync` fail-open · `ass_builder` 人名条越镜尾 + `\pos` 硬编码 · `templates3d_relip` 768×432 覆盖无备份 |
| 前端 | `LibraryPage` 刷新永久卡 refreshing · `AgentPage` 三条轮询无互斥、无 `document.hidden`（可叠 13 个并发）· `lib/templates.ts` 缺 `relic_talk_5min`（纯前端缺口，后端 200 可建）· 季证据链接 `run_id>40` 边界 404 |
| 周边 | 子进程 stderr 用 utf-8 解 cp936 → 中文 traceback 变 `???`（已实测）· `run_h3_ref.py --fps` 死参数 + 上报入参而非实测时长 · `rebuild_meta.py` 伪造元数据 · 5 处硬编码绝对路径 · 4 个脚本模块级执行 + `drawtext text='%s'` 未转义（含 `%`/`'` 直接失败）· `localize` 假回退 + 空译文当 `ok:true` · `studio2d` 整包 **1838 行死代码** · `seed_v6_workflows --force` 无备份 · 5 个 `.ps1` UTF-8 无 BOM（PS 5.1 中文乱码） |
| 数据 | 30 个 `*_semantic.json.bak` 未跟踪挂在 `contract/` 里 · `scene_map.json`/`scene_descriptions.json` 是 v3 遗留却被当 v5 校验依据（33/33 假阳性）· `roles.py` 挂着不存在的工具名（`run_workflow`/`inspect_workflow`） |

---

## 5. 五个跨域根因（比单条缺陷更值得修）

1. **id 有三套，各写各的**：template `acts[]` / `templates_v5` 的 `episode.json` / `templates3d` 的 `*_semantic.json`。三处都能"定义镜头"，`assemble` 用第二套，HQ 渲染用第三套，`expand_3d_shots.py` 在第三套里造 id 且不查重。→ 建议：**episode 是唯一真相源**，brief 只存 `shot_id → prompt` 的映射，`assemble` 前断言 id 集合相等。
2. **缓存判据只看"文件在不在、够不够大"**：`_render`/`gen_t2va`/`_meta_matches`/`voice_cache`/关键帧抽卡全是这套。→ 建议：**产出一律带输入指纹**（prompt+seed+size+steps+cfg+ref 的 hash 写进 meta），指纹不符即视为未命中；`--force` 必须是"绕过指纹"而不只是"重跑"。
3. **"ok / success" 语义太宽**：skipped 记 ok、dry_run 记 ok、`available()` 恒 true、QC 5 项 fail-open、`write_shorts_report` 对不存在的成片报绿。→ 建议：run 状态拆成 `ok / partial / failed / planned`，`skipped` 一律 `partial`，报告里必须列出"哪些阶段没跑"。
4. **路径与边界校验各写一套**：`ID_RE {1,60}` vs 媒体 `{8,40}`；`{id}.json` 直接拼 vs `/media` 有 resolve 白名单；`format_id` 零校验；`plate_path` 3 个硬编码 alt。→ 建议：一个 `ids.py`（唯一 id 规则 + 保留字）+ 一个 `paths.resolve_under(root, rel)`，所有入口复用。
5. **文档与代码各自演化**：模板数 16+17→21+12、工具 13→18、角色 50→26、用例 343/185→372/186、"33 份 v2"→16 v1+17 v2、V5 栈 17→22、45 处"后端 Vxx 实现中"过期文案。→ 建议：把可数事实（模板数/路由数/工具名）做成**自动生成的 `docs/STATUS.md`**，CI 里 diff 不为空就 fail。

---

## 6. 前两轮回归总账（91 条）

| 域 | 前两轮条数 | 已修 | 部分修 | 未修 | 修出反向 | 超范围/降级 |
|---|---|---|---|---|---|---|
| backend（R1 13 + R2 13） | 26 | 16 | 6 | 3 | 1 | 0 |
| agents + 合同（R1 12 + R2 12，含 P3） | 27 | 17 | 5 | 2 | 0 | 3 |
| pipeline（F1–F12 + R1–R12） | 24 | 15 | 0 | 3 | 0 | 6 |
| frontend（REVIEW_FRONTEND 14） | 14 | 13 | 0 | 0 | 0 | 1（降级） |
| **合计** | **91** | **61** | **11** | **8** | **1** | **10** |

- **仍未修的老账（8 条，全部在本轮 P1/P2 里复现）**：发布静默回退（R1-P1-2）、无 job 取消端点（R1-P2-7 后半）、`pipeline` 层仍写死 `repo/outputs`（R2-P2-7；`orchestrator.py:169-172` 仍拼死路径）、`muse.depends=[]`（R1#13）、`set_voice` rerun 只给一条（R2#11）、HQ T2VA 体积判缓存（F2）、关键帧体积短路（R2）、`read_json` gbk 兜底（R8）。
- **唯一"修出反向"**：R2-P1-4 的 `video_fallback` 从"回退时打标"改成"按文件名判定"，结果 `localized/<lang>/final.mp4` 恒被判成回退（本轮 backend P2-10）。
- **前两轮的正面结论仍需保留**：合同 33/33 一一对应、`save_workflow` 校验已生效、`muse` 的原子 `os.replace` + 回滚、`_expand` 未知名报错、`degraded` 拒绝执行、前端 13/14 条守卫已补。

---

## 7. 修复路线（按"能不能出片"排序）

### R0 · 半天，恢复可出片（做这三件就够）
1. `scripts/gen_scene_plates.py` 重生成 14 张专属底板 + 验证 `21/21 plate 命中`（验收：`python tmp/audit_plate_resolve2.py` 全 OK）。
2. 修 `expand_3d_shots.py` 的 id 生成（`max_existing+1` + 唯一性 assert），删掉 30 个 `.bak`，重跑一次让 brief 与 episode id 对齐（验收：`python tmp/audit_dup_impact.py` 里 `episode 有而 brief 无` = 0）。
3. 加 `pytest.ini` 的 `testpaths`/`norecursedirs`（验收：仓库根 `pytest -q` 11 秒内出结果）。

### R1 · 1 天，堵静默错误
4. `tts_breeze` 文件名改 `sha1(text)[:8]`；旧音频保留一轮。
5. 缓存判据统一加输入指纹（T2VA / 关键帧 / `_meta_matches`）。
6. `upload_post` 的 `privacy_level` 映射 + `unlisted` 非 youtube 预检拒发。
7. `assemble` 前加 id 集合断言；把 `xfade_chain` 的时长不一致 `LOG.warning` 改成 `raise`。
8. 三个路径穿越入口统一走 `resolve_under`；`format_id` 加值域校验（400 + 可用值）。
9. `evaluate_hq_qc` 双写 `qc_report.json`、时长门补上限、`lip_sync` 去掉 fail-open。

### R2 · 2–3 天，结构性问题
10. episode 作为唯一真相源，brief 退化为 `shot_id → prompt`；`OUT3D/<tid>/work/episode.json` 那份幽灵文件删掉或标废弃。
11. run 状态机加 `partial`；`skipped` 不再算 ok；`muse.depends` 改成 `["shots"]`，`apply` 后强制重跑 assemble。
12. 季规则进编排层（与 `check_seasons.py` 合成一份）。
13. `run_id` 加随机后缀 + `exist_ok=False`；`/media` 与 `ID_RE` 边界统一。
14. `rebuild_all_looks_5min` 分目录 + 原子切换；`clear_stale_render_urls` 加 `__main__` 守卫 + dry-run 默认。

### R3 · 持续
15. `docs/STATUS.md` 自动生成（模板/路由/工具计数），CI diff 即 fail。
16. 清理 `studio2d`（1838 行死代码）、45 处过期"实现中"文案、5 个 `.ps1` 加 BOM。

---

## 8. 本轮未验证 / 已纠正的误报（诚实标注）

- **未验证 3 件**：① `App.tsx` 导航竞态（需浏览器 + Slow 3G，本会话无 CDP）；② 真 Breeze TTS 覆盖（需起服务与权重，只用伪造 HTTP 层 + 静态读码证明）；③ 任何真渲染/GPU 路径（本轮**一次 GPU 都没跑**，所有渲染类结论来自静态链 + 数据核对 + 无 GPU 的 `assemble` 实测）。
- **已纠正的误报**（我或队友初判后被推翻）：
  1. `/api/settings/metaso-test` 被 SPA 遮蔽 → **不成立**。那是 23:27:55 那版（路由在 `mount("/")` 之后、且 `Body` 未导入会让整站 import 崩，是过程中的瞬时 P0）；23:28:59 版已把 `Body` 加进 `:22`、路由移到 `:2617`、挂载在 `:2691`，实测 `POST → 200` 并回真实模型列表。**残留问题只是顺序脆弱**（P2）。
  2. 工作流 35 份 → **实为 33 份**（平铺与递归都是 33）；33 = 22 个 `v5_render` + 11 个 `hq_shorts`，`custom_holo_talk` 走 V5 栈。
  3. `/api/v6/agent/templates`、`/api/templates` 404 → **属实但无害**：前端 grep 两串均 0 命中，模板走 `/api/v6/agent/overview`（实测 `templates=33`）；115 条前端调用对后端 **0 缺口**。
  4. 前端"过期的 `StudioModal` 关闭态"等 3 条 PART_FRONTEND 勘误 → 其中"依赖 307 尾斜杠重定向"**不成立**（`cinema_templates.py:1825-1826` 对 `""` 和 `"/"` 双注册）。
  5. `test_muse_video_skips_without_provider` 挂在 `registry.py:541` → **测试夹具过期**（假 `_Ctx` 无 `seed`，真 `StageContext` 有），不是产品 bug；`test_frontend_api_live` 的 404 是成片缺失（P0-4），不是路由问题。

---

## 9. 分报告索引与复现

| 分报告 | 覆盖 | 新发现 |
|---|---|---|
| [AUDIT_backend_api.md](AUDIT_backend_api.md) | `backend/app`（main/routers/jobs/db/schemas） | 3 P1 + 10 P2；含前两轮 26 条回归表 |
| [AUDIT_agents_contract.md](AUDIT_agents_contract.md) | `pipeline/agents` + `contract/v6` + 季 | 8 P1 + 17 P2 + 7 P3；前两轮 27 条回归表 |
| [AUDIT_render.md](AUDIT_render.md) | `templates3d_hq` / `render_v5` / `scene_render` / `motion2d` / TTS / Muse | 1 P0 + 9 P1 + 19 P2；F1–F12/R1–R12 回归表 |
| [AUDIT_frontend.md](AUDIT_frontend.md) | `frontend/src` | 1 P1 + 4 P2 + 7 P3；115 条接口对照表 |
| [AUDIT_data_tests_docs.md](AUDIT_data_tests_docs.md) | 合同/资产、测试、文档漂移 | 2 P0 + 4 P1 + 11 P2 |
| [AUDIT_extras.md](AUDIT_extras.md) | `integrations` / `imagegen` / `studio2d` / `publish` / `localize` / `scripts` | 1 P0 + 8 P1 + 12 P2；45 脚本危险面扫描 |

**Lead 亲验探针（`tmp/`，可重跑）**：
`audit_plate_resolve2.py`（21/21 plate 缺失）· `audit_dup_impact.py`（id 空间分裂）· `audit_assemble_failmode2.py`（assemble 硬失败）· `audit_semantic_ids.py`（`v5_e001`×9）· `audit_dryrun_all_workflows.py`（33/33 planned）· `audit_smoke_api.py`（161 路由/0 重复）· `audit_db_media2.py`（发布记录全指向缺文件）· `audit_fe_relic.py`（`format_id` 零校验）· `run_pytest_utf8.py`（UTF-8 落盘跑 pytest）。

**已知会留下痕迹的副作用**：`docs/audit/_tools/` 与 `tmp/` 为本轮新增（未跟踪）；`contract/v5/cinema3d/templates3d/*.json.bak` 共 30 个是本轮之前 `expand_3d_shots.py` 留下的，建议删除或加 `.gitignore`；`outputs/agent_runs/` 里有 2 个僵尸 run 目录按证据保留。
