# REVIEW_AGENTS — pipeline/agents + contract + scripts + tests 审查报告

- 审查范围：pipeline/agents/**、contract/**（v6/workflows、v5/templates、v5/series）、scripts/**（契约/编排相关）、tests/**
- 只读审查，未改动任何源码或合同数据。复现脚本都在 tmp/audit_agents/（跑完可删）。
- 已排除已知四项（SyncNet 权重、templates_2d/3d 双份成片、无鉴权、frontend v5world 死代码、README 旧章节）。
- 共 15 条，按严重度排序；证据命令都可直接重跑。

---

## P1

### 1. render_2d_hq / muse_lipsync 把 templates3d.load_template 永久替换成「忽略 tid」的闭包
- **位置**：pipeline/agents/registry.py:259-265（render_2d_hq）、pipeline/agents/registry.py:437-443（muse_lipsync 重组分）
- **触发**：进程里跑过任意一次 render_2d_hq 或 muse_lipsync(apply=true)，之后任何代码再调 templates3d.load_template(other_tid)。
- **为什么错**：_load_template(_tid) 完全忽略入参，只用闭包里算好的 builtin/custom（当前模板）。t3.load_template = _load_template 是模块级全局，且没有恢复（同一函数的 hq.log 有 try/finally 恢复，load_template 没有）。同进程后续的：
  - pipeline/templates3d.py:477,509（generate / assemble 内部就是全局查 load_template）
  - pipeline/templates3d_pipeline.py:67
  - backend/app/routers/v5_create.py:1066,1115-1116（建模板 / semantic brief，和智能体 run 同一个 FastAPI 进程）

  全部拿到「上一次渲染的那个模板」的合同 → 风格 / cast / acts 全串台，且不报错。
- **证据**：
~~~bash
python tmp/audit_agents/test_registry.py
# t3.load_template 被替换: True
# 用 tid=tonight_show 调 load_template 拿到: custom_tpl_v46_duologue / V46 2D·装配夜话
~~~
- **最小修法**：render_2d_hq / muse_lipsync 里 saved = t3.load_template，放进现有 try/finally 恢复；同时 _load_template 尊重 _tid：先 contract/v5/templates/<tid>.json，不存在再 contract/v5/custom/templates/<tid>.json（几行）。

### 2. check_story 抛 SystemExit 穿透所有 except Exception：run 证据停摆 + 后台任务永久 409
- **位置**：pipeline/agents/registry.py:201-205（_semantic_brief 里 raise SystemExit）；pipeline/agents/orchestrator.py:139（except Exception）；backend/app/routers/v6_agent.py:186、:361，以及 :213 / :413 的 running 检查
- **触发**：任一短片模板缺 contract/v5/cinema3d/templates3d/<tid>_semantic.json（新增 custom 模板最常见的漏配），跑 story 阶段。
- **为什么错**：SystemExit 继承 BaseException，不是 Exception。run_template 的 except Exception 抓不到 → 不写 status=failed、不 persist()、不写 summary.md、不发 end 事件，run.json 永远停在 "running"；CLI 直接带原始异常退出。后端两条 worker（单集 _run_worker、季 _season_worker）同样只 except Exception：线程直接死，finally 只清 _current_job，job["status"] 留在 "running" → 之后每次 POST /run 与 /season/{sid}/run 都命中 running 检查返回 409，直到重启进程。
- **证据**：
~~~bash
python tmp/audit_agents/test_registry.py   # 第二段
# 抛出 SystemExit(非 Exception): 缺少语义 brief: ...custom_holo_talk_semantic.json
# isinstance(SystemExit(), Exception) = False
~~~
- **最小修法**：registry._semantic_brief 改成 raise RuntimeError；orchestrator 与两个 worker 用 except BaseException（或 except (Exception, SystemExit)），并在 handler 里把 job 状态落到 failed。

### 3. --stages 写错一个字母 → 0 阶段、status: ok、退出码 0
- **位置**：pipeline/agents/orchestrator.py:39-57（_expand）、:97-101、:149
- **触发**：run --stages shotz（拼错）、--stages story,asemble；--start 写错同样被静默忽略（:42 的 if start and start in ids 没有 else）。
- **为什么错**：want 建好后只做「补依赖」，从不校验 selected 是否都命中；返回 [] → todo 空 → 循环不执行 → failed=False → run.status="ok"，cmd_run 返回 0，run.outputs.exists=False。API 同样把 ok 写进 job；季生产会把这一集计入成功。
- **证据**：
~~~bash
python tmp/audit_agents/test_orch.py   # 段 A
# dry_run=False status: ok | stages: [] | outputs.exists: False
~~~
- **最小修法**：_expand 开头 unknown = [x for x in (selected or []) if x not in ids]; if unknown: raise ValueError(未知阶段 ...)；start not in ids 同样报错。

### 4. muse 阶段在 V5 栈模板上指向另一条产线：静默 skipped 或改写 outputs/templates_3d 的成片
- **位置**：pipeline/agents/registry.py:368-371（_dialogue_pairs）→ pipeline/lipsync.py:163 OUT_ROOTS=(templates_3d, templates_2d)；registry.py:386、432-447（回写 + t3.assemble）
- **触发**：V5 栈模板上启用 muse（AGENTS.md §5 的 --stage-params '{"muse":{"apply":true}}'）。V5 的镜头在 outputs/templates_v5/<tid>/shots，muse 只认 templates_3d / templates_2d。
- **为什么错**：两条错误路径都静默：
  1. outputs/templates_3d/<tid> 存在（tonight_show、history_talk 等 3D 看板都有，work/audio 齐）→ muse 拿 3D 看板的镜头做口型，apply 直接覆盖 templates_3d/<tid>/shots/*.mp4，再 t3.assemble 重出 templates_3d/<tid>/final.mp4。V5 交付片一条没动，3D 看板被污染。
  2. 无 3D 目录（如 relic_talk）→ pairs 空 → 返回 {"skipped": True}，orchestrator 记成 status: ok 并计入 run.status="ok"，等于「口型已做」的假成功。
- **证据**：
~~~bash
python tmp/audit_agents/test_latest.py
# tonight_show -> 2  clip: outputs\templates_3d\tonight_show\shots\v5_002.mp4 ...
# relic_talk  -> 0
~~~
- **最小修法**：muse_lipsync 开头按 load_workflow(...)["stack"] 分流：V5 栈默认 raise RuntimeError（或把 outputs/templates_v5/<tid>/shots 加进 OUT_ROOTS，并把重组改回 render_v5.assemble）；skipped 结果在 orchestrator 里记 status="skipped" 而不是 ok。

---

## P2

### 5. muse_lipsync 的 apply 没有回滚、也不是原子写：assemble 失败后镜头已被永久替换
- **位置**：pipeline/agents/registry.py:417-431（备份 + 覆盖循环）、:432-447（t3.assemble）
- **触发**：apply=true 时 t3.assemble 抛错（缺 episode.json / ffmpeg 失败 / 被别的进程占着），或第 N 个 clip 写入中途 IO 失败。
- **为什么错**：clip.write_bytes(out.read_bytes()) 原地截断重写；_orig/ 只写不读——全仓库没有从 _orig 恢复的代码路径。异常一抛，shots/ 里 N 个镜头已被换成 muse 版、成片还是旧的，run 记 failed，只能手工恢复。另外 applied>0 且 reassemble=false 时同样「镜头已换、成片没重出」。
- **最小修法**：先 tmp = clip.with_suffix(".muse.tmp"); tmp.write_bytes(out.read_bytes()); os.replace(tmp, clip)；把循环 + assemble 包进 try，except 里按已 apply 列表从 _orig/ 逐个 shutil.copy2 回滚后 re-raise。

### 6. run.json 走裸 json.dumps：Path/NaN 会写坏证据文件，且写入点在 try 之外
- **位置**：pipeline/agents/orchestrator.py:89-91（persist）、:134-136（record["result"] = result）、:148（persist() 在 try 外）
- **触发**：任一工具返回值出现 Path 或 NaN/Infinity；或任何工具返回后 persist 本身失败。
- **为什么错**：json.dumps 默认 allow_nan=True → 写出 NaN/Infinity 字面量（Python 能读回，JS JSON.parse 直接抛，页面 run 证据会崩）；遇到 Path 直接 TypeError。更糟的是 persist() 在 try/except 之外——序列化一失败，整个 run 从 run_template 抛出，既没有 failed 记录也没有 summary.md。仓库其它写盘都走 utils.write_json（pipeline/utils.py:147-165，_finite + allow_nan=False），只有这里绕过了。
- **证据**：
~~~bash
python tmp/audit_agents/test_orch.py   # 段 H
# TypeError: Object of type WindowsPath is not JSON serializable
~~~
  已逐条核对现有工具返回值：render_v5.assemble / tts_and_pace / h3_v5.render_hybrid / lipsync.sync_clip 全是 str/float，当前不会触发，属边界。
- **最小修法**：persist() 改成 utils.write_json(run_dir/"run.json", utils._finite(run.to_dict()))，并把 persist() 挪进 try、自身再包一层 try/except 记 event("persist_fail")。

### 7. 工作流 JSON 坏了 → 静默回退默认工作流，并按默认栈跑完整条 run
- **位置**：pipeline/agents/workflow.py:124-135
- **触发**：手改 contract/v6/workflows/<tid>.json 时多一个逗号 / 存成非 UTF-8 / stages 被清空。
- **为什么错**：except Exception: pass 后直接 return default_workflow(...)，没有任何日志。run.json 里 workflow 字段还写着默认名，看似正常；实际跑的是内建默认阶段（V5 模板含 voice/editorial/assemble，短片是另一套），用户以为自己改的依赖 / 参数生效了。data.get("stages") 为空数组时同样静默。
- **证据**：
~~~bash
python tmp/audit_agents/test_orch.py   # 段 F：坏 JSON 入参
# 返回 stack= v5_render | 阶段数= 12 | name= broken 工作流
~~~
- **最小修法**：except 里 LOG.warning 并把 "degraded": True, "degraded_reason": str(exc) 塞进返回 dict；run_template 遇到 degraded 直接 raise。

### 8. default_workflow 浅拷贝：params / depends 与模块常量是同一个对象
- **位置**：pipeline/agents/workflow.py:117（[dict(stage) for stage in stages]）
- **触发**：任何对返回值里 stages[i]["params"]（或 depends 列表）的原地修改——例如 API 拿到默认工作流后补参数、脚本批量改 params。
- **为什么错**：dict(stage) 只复制第一层，params 字典和 depends 列表仍指向 DEFAULT_STAGES / SHORTS_STAGES。改一次，进程内后续所有模板的默认工作流都被污染（load_workflow 缺文件时也返回这份被污染的数据）。
- **证据**：
~~~bash
python tmp/audit_agents/test_orch.py   # 段 E
# 第二条工作流 art.params: {'character': 'POLLUTED', 'poses': []}
# DEFAULT_STAGES 也被改
~~~
- **最小修法**：改成 copy.deepcopy(stages)（import copy 一行）。

### 9. edit --op patch_template / set_voice 只认内建目录：11 个短片模板 + 2 个 custom 角色一律 FileNotFoundError
- **位置**：pipeline/agents/ops.py:89（CONTRACT/"templates"/<template>.json）、ops.py:42-43（CONTRACT/"characters"/<character>.json）
- **触发**：python -m pipeline.agents edit --template custom_tpl_v46_duologue --op patch_template --json '{...}'；或对 custom_old_inventor / custom_street_kid 执行 --op set_voice。AGENTS.md §3 把这两个 op 当通用能力宣传。
- **为什么错**：custom 模板在 contract/v5/custom/templates/，custom 角色在 contract/v5/custom/characters/（v5_catalog 合并读，这里没走 catalog）。报错文案还是「模板不存在」，把「路径没找对」说成「数据不存在」。registry.render_2d_hq:256-263 自己做了 builtin→custom 回退，两处规则不一致。
- **证据**：
~~~bash
python tmp/audit_agents/test_ops.py
# patch_template custom_tpl_v46_duologue -> FileNotFoundError 模板不存在: ...\contract\v5\templates\...
# set_voice custom_old_inventor -> FileNotFoundError 角色不存在: ...\contract\v5\characters\...
~~~
- **最小修法**：抽一个 _resolve(kind, tid)：先 contract/v5/{kind}/，不存在再 contract/v5/custom/{kind}/；patch_template / patch_editorial / set_voice 共用。

### 10. set_voice 忽略 template，改的是全局角色音色，却只回报该模板的受影响阶段
- **位置**：pipeline/agents/ops.py:46-64（template 参数只用于拼 rerun 文案）
- **触发**：edit --template classics --op set_voice --char laozhou --value ...。
- **为什么错**：写的是 contract/v5/characters/laozhou.json，该角色被 tonight_show / roundtable / custom 系列共用 → 所有模板的音色一起变；返回的 affected_stages 只列本模板，rerun 也只重跑本模板 → 其它已交付成片与合同声明的音色不一致（AGENTS.md §4「每角色固定音色、跨集一致」被破坏），且没人知道要重跑哪些。
- **最小修法**：要么按模板写覆盖（custom 模板 JSON 里存 voice 覆盖），要么在返回值里加 affected_templates（用到该角色的所有模板）并把 rerun 改成全量。

### 11. 季合同声明 same_cast: true，三集 cast 交集为空；且没有任何代码校验季规则
- **位置**：contract/v5/series/undercity_arc.json:31-37（rules.acts / same_cast / same_style）vs :7-20（members）
- **触发**：按季生产跑 undercity_arc。
- **为什么错**：第 1 集 cast = laozhou / ali / tianmei，第 2、3 集 = custom_old_inventor / custom_street_kid，公共角色 0 个。same_cast: true 与实际数据直接冲突，「三部曲同一个底城世界」实际是「1 集 + 另外两集互为续集」。全仓库 grep same_cast 只有合同和文档，无任何校验（check_story 只看单集、季 worker 不看 rules）——这个不一致永远发现不了。
- **证据**：
~~~bash
python tmp/audit_agents/check_series.py
# common cast: []
# union-diff: ['ali','custom_old_inventor','custom_street_kid','laozhou','tianmei']
~~~
- **最小修法**：check_story 增补季级校验（或 _series_entry 里加 validate_season）：same_cast 为真时断言 members 的 cast 字符集交集非空且 >= 最小人数，不满足就报错 / 在 GET /series 里标 warnings。数据侧把第 1 集改成同一对角色。

### 12. latest_runs 按路径字典序而不是 run 时间排序：season_ 前缀永远压过更新的单集 run
- **位置**：pipeline/agents/orchestrator.py:192（sorted(RUN_ROOT.rglob("run.json"), reverse=True)）
- **触发**：存在季 run 之后，GET /api/v6/agent/runs（latest_runs(None, 8/10)）与 run --template X status。
- **为什么错**：排序键是整个路径字符串：.../season_undercity_arc_20261105_131453/... 大于 .../20261105_131633_...，所以所有季内 run 排在所有单集 run 之前，与真实时间无关；季内又按模板名字典序而不是 run 顺序。结果：limit 名额被旧季 run 吃掉，最新的单集 run 根本不显示（当前 29 份 run.json：12 份季内 + 17 份单集，取 10 条全是季内）。
- **证据**：
~~~bash
python tmp/audit_agents/test_sort.py
# total: 29；sorted(reverse=True) 前 6 条全部是 season_... 下的
~~~
- **最小修法**：读完数据按 data.get("started")（或 run.json 的 mtime）排序后再切片。

### 13. muse 阶段 depends 为空：只跑 muse（季批量 with_muse 就走这条）会重写成片却不重跑 qc/publish，QC 证据变旧
- **位置**：pipeline/agents/workflow.py:73-76（SHORTS muse depends: []）、:39-42（DEFAULT muse 同）；33 份合同里也都是 "depends": []；backend/app/routers/v6_agent.py:401-405（with_muse → stages=["muse"]）
- **触发**：POST /season/{sid}/run {with_muse:true, muse_apply:true}，或 run --stages muse --stage-params '{"muse":{"apply":true}}'。
- **为什么错**：_expand(["muse"]) 只返回 muse（无依赖可补），muse 内部覆盖镜头 + t3.assemble 重出 final.mp4，但 qc_shorts.json / template_summary.json 仍是上一轮的；季面板 status 只看 run.status（ok）→ 已经换了成片却拿旧 QC 当证据。qc 只依赖 shots，永远感知不到 muse 的改动。
- **最小修法**：muse 的 depends 改成 ["shots"]，qc 的 depends 改成 ["shots","muse"]（或 muse reassemble 分支后自评 evaluate_hq_qc 并写回）。

---

## P3

### 14. render_2d_hq 把 duration / bytes 以字符串写进 run 证据
- **位置**：pipeline/agents/registry.py:306-311（probe = report["probe"]["format"] → {"duration": "19.120000", "size": "6255489"}）
- **触发**：每次短片 shots 阶段。
- **为什么错**：report.json 里 ffprobe 的字符串被原样透传，run.json 里 result.duration 是 "19.120000" 而不是 float，任何对证据做数值比较 / 求和的消费方（季汇总、页面排序、以后的校验）都会 TypeError 或静默字符串比较。
- **证据**：python tmp/audit_agents/test_registry.py 第一段 result 行；outputs/templates_3d/custom_tpl_v46_duologue/report.json 的 probe.format 类型即 str。
- **最小修法**："duration": float(probe.get("duration") or 0), "bytes": int(probe.get("size") or 0)。

### 15. save_workflow 不校验 id：可写出 WORKFLOW_DIR 之外；也完全不校验 stages / 依赖 / 工具名
- **位置**：pipeline/agents/workflow.py:138-147
- **触发**：edit --template ../evil --op patch_workflow --json '{...}'（CLI --template 无白名单）；PUT /api/v6/agent/workflow/{template} 虽把 wf["id"] 强制成 URL 参数，但 stages 内容照单全收。
- **为什么错**：_path(template_id) 直接拼路径 → contract/v6/workflows/../evil.json 落到 contract/v6/ 之外；写入是 tmp.replace(p)，目标不存在也能建。另外 stages 里重复 id、依赖不存在的阶段、工具名拼错都能存进去，配合条目 3 表现为「跑完 ok 但其实没跑」。
- **证据**：
~~~bash
python tmp/audit_agents/test_orch.py   # 段 G
# 写到: ...\tmp\audit_agents\wf\sub\..\escape_check.json | 在 target 外: True
~~~
- **最小修法**：if not re.fullmatch("[A-Za-z0-9_]+", template_id): raise ValueError；save_workflow 里加 _validate(workflow)：stage id 唯一、depends 必须存在于同文件、tool in TOOLS（惰性 import 避免循环）。

---

## 未见问题的模块（明确结论）

- **contract/v6/workflows ↔ contract/v5 模板一一对应**：33/33，无孤儿、无缺失。21 份 v5/templates + 12 份 custom/templates（含 custom_holo_talk）全部有工作流文件；stack 与 is_shorts() 判定 100% 一致（11 hq_shorts + 22 v5_render）。
- **阶段依赖自洽**：33 份合同内无重复 stage id、无依赖不存在阶段、无「依赖排在后面」、无「enabled 阶段依赖 disabled 阶段」、无缺 tool 的阶段。_expand 的依赖展开实测正确（--stages publish → story,voice,editorial,shots,assemble,qc,publish）。
- **check_story 对 11 个短片模板全部通过**：acts / scene / line-角色 / 语义 brief prompt 数量与分幕一一对应，sum(acts.duration) == target_duration（误差 0），成片时长与 target 差 <= 0.06s。
- **其它工具返回值可 JSON 序列化**：render_v5.assemble（全 str/float）、tts_and_pace、h3_v5.render_hybrid、lipsync.sync_clip（Path 都 str 过）、write_report / write_shorts_report 均无 Path；evaluate_hq_qc 用 utils.probe_video（0 兜底，无 NaN）→ 条目 6 是边界而非现存 bug。
- **scripts/seed_v6_workflows.py**：幂等（33 份已存在时 created_count=0、零写盘）；migrate_v50_muse_stage.py 幂等（有 muse 即跳过）；migrate_v50_musev_stage.py 幂等但无 if __name__ == "__main__"（import 即改 33 份合同，建议补上）。
- **tests/ 没有「随数据增长必挂」的基线**：test_v5_acceptance.py:65 / test_v5_2_acceptance.py:69 / test_v5_4_acceptance.py:58 都是固定 16 集白名单 + len(READY)<16 就 skip 的守卫，加模板不会挂；偏脆的只有 test_v45_style_polish.py:213（硬断言合集 3 集、时长 > 40s，加第 4 集会挂）与 :186（duologue 必须 6 幕）。
- **真正的缺口**：tests/ 里没有任何一条测试触达 pipeline/agents/*（grep agents / v6/workflows 零命中）——条目 1/2/3/5/8/15 全无回归网。建议至少补：_expand 未知阶段抛错、load_workflow 坏 JSON 抛错、check_story 缺 brief 抛 RuntimeError、save_workflow 非法 id 抛错、muse apply 失败回滚（tmp 目录 + 假 t3.assemble）。
