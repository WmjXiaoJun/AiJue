# REVIEW_AGENTS_ROUND2 — 修复回归 + 合同/测试质量

- 范围 A：pipeline/agents 修复后的回归；范围 B：合同与测试质量。
- 只读审查，未改源码/合同；复现脚本在 tmp/audit_agents/（r2_*.py）。
- registry.py 在我审查期间又被改过一次（mtime 17:03:59，回滚由 os.replace 改成 shutil.copy2），本报告按当前磁盘版本。
- 12 条，按严重度排序；已验证修好的项列在文末「回归结论」。

---

## P1

### 1. 输出根目录三处口径不一：设了 ZAOPIANJU_OUTPUTS_DIR 就「写这边读那边」，好片 QC 5/5 全红
- **位置**：pipeline/agents/registry.py:198-204（_outputs_root，env 优先）、:319-331（render_2d_hq 读 report）、:336-358（evaluate_hq_qc 读成片）、:418/:531（muse 产物）；对照 pipeline/templates3d.py:30-31（OUT3D 写死 repo/outputs）、pipeline/agents/orchestrator.py:165-168（run.outputs 写死 repo/outputs）
- **触发**：环境里存在 ZAOPIANJU_OUTPUTS_DIR（tests/conftest.py:20 默认就设；多实例部署 / 隔离输出也设）。
- **为什么错**：本轮把 registry 侧改成 env 优先（与后端 app/config 同口径），但 templates3d / templates3d_hq 仍写 repo/outputs，orchestrator 也仍写死 repo/outputs。于是同一个 run 里：
  - render_2d_hq 把 hq 的产物写到 repo/outputs/templates_3d/<tid>，却去 <env>/templates_3d/<tid>/report.json 读 duration/bytes → 全是 None；
  - evaluate_hq_qc 读 <env> 下不存在的成片 → final_exists / poster / duration / audio / resolution 5 项全红，对一部真实存在的好片报 QC 失败，publish 被卡死；
  - orchestrator 记录的 run.outputs.final 又是第三个路径。
  这是把 REVIEW_BACKEND 第 12 条的同款问题复制进了 agents 层；而新测试恰好 monkeypatch 掉了 _outputs_root（第 9 条），所以测不出来。
- **证据**：
~~~bash
python tmp/audit_agents/r2_regress.py    # 段 E
# 有 env: ...\tmp\...\alt_out | templates3d.OUT3D: ...\outputs\templates_3d | 一致? False
# _shorts_dir: ...\alt_out\templates_3d\custom_tpl_v46_duologue
# 真实成片存在: True
# evaluate_hq_qc -> RuntimeError 短片 QC 失败: ['final_exists','poster','duration','audio','resolution']
~~~
- **最小修法**：只留一个真相源。要么让 templates3d / templates3d_hq 也用 `_outputs_root()`（OUT3D = _outputs_root()/"templates_3d"，两个常量各改一行），要么 registry 全改回 t3.OUT3D / t3.V5_OUT，并把 orchestrator:166 的硬编码换成 registry._shorts_dir(ctx)。再加一条断言 `_outputs_root()/"templates_3d" == templates3d.OUT3D`。

### 2. patch_workflow / set_stage_param 在 degraded 合同上静默覆盖用户文件，并把 degraded:true 写进合同 → 模板永久拒绝执行
- **位置**：pipeline/agents/ops.py:115-133（patch_workflow：load_workflow → 打补丁 → save_workflow）、:140-141（set_stage_param 同路径）；pipeline/agents/workflow.py:138-142（degraded 默认工作流）
- **触发**：某模板的工作流 JSON 被手改坏（多逗号 / stages 被清空），随后任何一次 edit --op patch_workflow、edit --op set_stage_param，或 API apply_op 的这两个 op。
- **为什么错**：load_workflow 现在对坏文件返回「默认 stages + degraded:true」（本意是让 run 拒绝），但 patch_workflow 不看 degraded，直接在这份默认上打补丁再 save_workflow → 用户原文件被 12 个默认阶段覆盖（原内容彻底丢失），同时 degraded / degraded_reason 被持久化进合同；之后每次 run_template 都在 workflow.py:80 拒绝执行，模板等于被砖化，唯一出路是 reset_workflow 或手删文件。实测：
~~~bash
python tmp/audit_agents/r2_regress.py    # 段 B2
# 原文件仍在前 20 字符: {"id":"broken","stag
# 写回后 degraded 字段: True | 阶段数: 12
# run_template 再跑: RuntimeError broken.json 解析失败...
~~~
- **最小修法**：patch_workflow 开头 `wf = load_workflow(template); if wf.get("degraded"): raise RuntimeError(wf["degraded_reason"])`；save_workflow 里 pop 掉 degraded / degraded_reason 兜底；顺便把坏文件备份成 <tid>.json.broken，别让用户失去原文。

---

## P2

### 3. 悬空 depends / 未知工具能存能跑：被跳过的阶段不改变 run 状态，run 照样 ok
- **位置**：pipeline/agents/workflow.py:146-157（save_workflow 只校验 id）；orchestrator.py:114-122（missing → skipped）、:159（status 只看 failed）
- **触发**：PUT /api/v6/agent/workflow/{tid} 写入 depends:["nope"]，或 edit --op patch_workflow 写入未知 tool / 重复 stage id；之后 run。
- **为什么错**：save 阶段不校验「stage id 唯一 / depends 必须存在 / tool 必须在 TOOLS」；run 阶段缺依赖只记 skipped，failed 不变 → status 仍是 planned/ok。于是「一个阶段都没跑，但报告成功」。_expand 的校验只覆盖命令行传入的 stages/start，覆盖不到合同里的 depends。
- **证据**：
~~~bash
python tmp/audit_agents/r2_contracts.py
# status: planned | stages: [('story','planned'), ('shots','skipped'), ('qc','skipped')]
# save_workflow 接受重复 id / 悬空依赖 / 未知工具 -> 写盘成功: dup_test.json | 段数: 2
~~~
- **最小修法**：save_workflow 里加 _validate(workflow)：stage id 唯一、depends ⊂ ids、tool in TOOLS（惰性 import 避免循环）；run_template 里把 missing 依赖算作 failed=True（或至少 status="partial" 并在 to_dict 里露出 skipped 计数）。

### 4. muse 的原件 / 缓存不随 shots 重渲失效：重渲后的新镜头会被「旧镜头的口型版」覆盖
- **位置**：pipeline/agents/registry.py:412-424（targets 取 _orig、out 复用）、:462-470（backup 只在不存在时写）
- **触发**：任一镜头走过一次 muse apply → 之后重渲该镜头（render_2d_hq --force / 改 prompt）→ 再跑 muse（带不带 --force 都一样）。
- **为什么错**：两处缓存都没有失效键：
  1) outputs/muse/<tid>/<sid>_muse.mp4 只要存在且 ctx.force=False 就复用 → 那是上一版镜头的口型结果；
  2) 即使 ctx.force=True 重算，源是 _orig/<sid>.mp4（第 414-417 行），而 _orig 只在不存在时写、永不随重渲更新 → 算出来仍是「旧镜头的口型版」。
  实测第二次 apply 把已重渲成 RENDER-V2 的镜头写回成 MUSE-OF-RENDER-V1，_orig 还停在 V1。
- **证据**：
~~~bash
python tmp/audit_agents/r2_stale.py
# 1) 首次 apply -> shot: MUSE-OF-RENDER-V1 | _orig: RENDER-V1
# 2) 重渲后 apply -> shot: MUSE-OF-RENDER-V1   （新镜头被旧结果覆盖）
~~~
- **最小修法**：缓存键带上镜头内容指纹：`tag = sha1(clip.read_bytes())[:8]`，备份与输出都用 <sid>_<tag>.mp4；或 render_shots / render_2d_hq 重渲时顺手删 shots/_orig/<sid>.mp4 与 outputs/muse/<tid>/<sid>_muse.mp4。

### 5. muse 的 QC 刷新失败被吞成成功：成片已不过 QC，run 仍然 ok
- **位置**：pipeline/agents/registry.py:512-517
- **触发**：muse apply + 重合成后，evaluate_hq_qc 判失败（时长掉了 / 海报没了 / 分辨率不对）。
- **为什么错**：except Exception 只打日志「QC 刷新失败（保留旧证据）」，然后正常返回 dict → orchestrator 记 status=ok、run.status=ok；`--stages muse`（季 with_muse 走的就是这条）不跑 publish，季面板只看 run.status → 一部已经 QC 不过的成片被当成成功交付。注意 evaluate_hq_qc 抛错前已把 qc_shorts.json 写成 ok=false，于是证据文件是红、run 是绿，两者矛盾。
- **最小修法**：QC 失败时 raise RuntimeError（让 run 落 failed），或至少把 qc_ok:False 塞进返回值，并让 orchestrator 把「result 里 qc_ok / ok 为 False」也算 failed。

### 6. 季自检脚本假绿：same_style 过滤 None、episode_sec / acts / same_world 完全不校验
- **位置**：scripts/check_seasons.py:43-50
- **触发**：季合同里混入 V5 模板（render_style=None），或改坏集数 / 时长 / 幕数。
- **为什么错**：
  - styles 取 render_style 后 `{s for s in styles if s}` —— None 被过滤掉，一个 arcane_cinematic + 两个 None 的混栈季声明 same_style:true 会静默通过；
  - 合同里 rules.episode_sec（13–25s）、rules.acts（4–6）、season.same_world 一个都没查；acts / 时长只能靠人工（我手工核过：当前 14.12 / 13.68 / 19.14s、4 / 4 / 6 幕，全部合规）；
  - 全脚本只覆盖 4 类问题（成员缺失 / same_cast 交集 / same_style / target_count），而退出码就是它的全部输出。
- **最小修法**：same_style 改成「None 也算不一致」；补 episode_sec / acts 区间校验（按成员合同 acts 求和 / 计数）；same_world 要么校验要么从合同删掉。

### 7. tests/test_v53_agents.py 往真实 contract/v6/workflows/ 里写坏合同
- **位置**：tests/test_v53_agents.py:39-50（path = workflow.WORKFLOW_DIR / "zzz_broken_test.json" 直接写仓库目录）
- **触发**：跑测试；尤其 pytest-xdist 并行、只读检出、或测试进程被硬杀。
- **为什么错**：文件头声称「不写仓库数据」，实际写的就是合同目录：
  - 与 seed_v6_workflows / 页面工作流列表并发时会被枚举到（GET workflow 会拿到一个 degraded 的假模板）；
  - 进程被硬杀时残留 zzz_broken_test.json，之后任何 list 都把它当模板展示；
  - 只读 checkout / CI 只读挂载时测试直接 error（不是 skip）。
- **最小修法**：`monkeypatch.setattr(workflow, "WORKFLOW_DIR", tmp_path)`（一行），断言不变。

### 8. 两条测试是恒真断言，季规则回归拦不住
- **位置**：tests/test_v53_agents.py:124（assert proc.returncode in (0, 1)）、:113-115（只断言 season_warnings 是 list）
- **触发**：季合同被改坏（same_cast 又变 true 且 cast 无交集、成员模板被删）。
- **为什么错**：check_seasons.py 的 main 只会返回 0/1，in (0,1) 永真 —— 脚本报 warning 时测试依然绿；season_warnings 只断言类型，而当前数据一条 warning 都没有，所以这两条对「季合同是否自洽」零约束。
- **最小修法**：`assert proc.returncode == 0, proc.stdout`（当前数据就是 0）；再补一条构造坏季的用例（tmp 目录 + monkeypatch series 目录）断言 warnings 非空。

---

## P3

### 9. test_evaluate_hq_qc_requires_final 把 _outputs_root 换掉，恰好掩盖了第 1 条
- **位置**：tests/test_v53_agents.py:77-81
- **触发**：任何对短片 QC / publish 的测试。
- **为什么错**：用例 monkeypatch registry._outputs_root → tmp_path，测试里永远「一致」，而生产 / CI 下 _outputs_root() 与 templates3d.OUT3D 的分裂（第 1 条）完全不可见，给了一个假的安全感。
- **最小修法**：保留该用例，另加 `assert registry._outputs_root() / "templates_3d" == templates3d.OUT3D`；或在 conftest 统一设 env 并让 templates3d 也读它。

### 10. degraded 拒绝会留下空 run 目录；latest_runs 的 stat 没有 try
- **位置**：orchestrator.py:73-77（先建 run_dir 再 load_workflow）、:79-81（拒绝）、:203（p.stat().st_mtime）
- **触发**：合同坏掉时反复 run；或 outputs/agent_runs 被清理脚本删文件的同时刷新 run 列表。
- **为什么错**：拒绝发生在 run_dir 建好之后 → 每次拒绝留一个 outputs/agent_runs/<ts>_<tid>/ 空目录（实测无 run.json），越积越多；latest_runs 的排序 lambda 里 p.stat() 一旦 FileNotFoundError 就整体 500（/api/v6/agent/runs 直接挂）。
- **最小修法**：把 load_workflow / degraded 检查挪到 run_dir.mkdir 之前；排序 key 改成「存在才 stat」并 try/except 跳过。

### 11. set_voice 依旧是全局改：laozhou 影响 29 个模板，rerun 只给一个
- **位置**：ops.py:46-71
- **触发**：edit --template classics --op set_voice --char laozhou --value ...
- **为什么错**：写入的仍是 contract/v5/characters/laozhou.json（全局资产），新增的 scope:"global" 与 affected_templates 只是让问题可见，但 rerun 还是 --template classics；照提示重跑，另外 28 个模板的成片音色已变而无人重渲（实测 laozhou 出现在 29 个模板的 cast 里）。
- **最小修法**：rerun 按 affected_templates 逐条生成（或给一条全量命令），payload 带 requires_rerun_count；真要 per-template 语义就写模板级覆盖。

### 12. migrate_v50_musev_stage.py 仍无 __main__ 守卫，且插入位置与 SHORTS_STAGES 相反
- **位置**：scripts/migrate_v50_musev_stage.py:17-33
- **触发**：任何 import scripts.migrate_v50_musev_stage（测试收集、工具扫描、pydoc）。
- **为什么错**：模块顶层直接遍历并 path.write_text 33 份工作流合同 —— import 即改仓库数据（round1 已提过，仍未修）；且它把 musev 插到 muse 之前（第 26-29 行），与 workflow.SHORTS_STAGES 的 muse→musev 顺序相反，--start muse / --start musev 的切片结果因文件而异。
- **最小修法**：包进 def main() + if __name__ == "__main__"；插入锚点改成「muse 之后」保持同序。

---

## 回归结论（已验证修好的）

- _expand：未知 stage / 未知 start 现在都抛 ValueError（r2_regress 段 A 实测）。
- load_workflow + run_template：坏 JSON → degraded + 拒绝执行（段 B 实测），不再静默按默认栈跑。
- default_workflow：deepcopy，DEFAULT_STAGES[2].params 不再被污染（段 D 实测）。
- save_workflow：id 白名单生效，../escape、Bad-Id 均被拒（段 C 实测）。
- render_2d_hq：t3.load_template 在 finally 恢复，且 _load_template 尊重 tid（读码 + 越栈行为确认）。
- muse_lipsync：V5 栈直接 skipped、不再触碰 templates_3d；apply 走 tmp + os.replace（原子）；写失败 / 合成失败都用 shutil.copy2 回滚且保留 _orig（r2_muse3 逐次 trace 确认只有一次 replace、回滚后 _orig 仍在）；合成后调 evaluate_hq_qc 刷新证据。
- ops.patch_template：custom 目录回退生效，写入落到 contract/v5/custom/templates/ 且保留原字段（r2_contracts2 段 3）。
- 合同：33/33 一一对应，stack 判定全对（22 v5_render + 11 hq_shorts），无重复 stage id / 悬空依赖 / enabled 依赖 disabled，合同中无残留 degraded 键。
- 季合同：same_cast 已改 false，与三集 cast 实际（1 集 laozhou/ali/tianmei，2-3 集 custom_old_inventor/custom_street_kid）一致；same_style true 与三集 arcane_cinematic 一致；episode_sec / acts 人工核对全合规。
- 脚本幂等：seed_v6_workflows（33 份存在即 0 写）、migrate_v50_muse_stage（有 muse 即跳过，且有 __main__ 守卫）、check_seasons（只读）。

## 最小测试清单（建议补，按性价比排序）

1. _expand：未知 stage / 未知 start 抛 ValueError（dry_run 即可，不跑工具）。
2. save_workflow：非法 id 抛错；重复 stage id / 悬空 depends / 未知 tool 抛错（配合第 3 条落地校验）。
3. run_template degraded 拒绝：monkeypatch WORKFLOW_DIR 到 tmp（不写真实合同），断言 raises 且不留空 run 目录。
4. default_workflow 深拷贝：改一个返回值不影响第二次调用。
5. patch_workflow 遇 degraded 抛错（第 2 条回归）。
6. muse apply 用假 lipsync（sync_clip / measure_sync / available 全 mock，tmp 目录）：① assemble 失败后镜头字节还原且 _orig 在 ② 成功时镜头=新、_orig=旧 ③ 重渲后再跑不覆盖新镜头（第 4 条回归）。骨架可直接抄 tmp/audit_agents/r2_muse2.py。
7. 输出口径：_outputs_root()/"templates_3d" == templates3d.OUT3D（第 1 条回归）。
8. 季自检：坏季 fixture 断言 warnings 非空；check_seasons.py 断言 returncode == 0。
