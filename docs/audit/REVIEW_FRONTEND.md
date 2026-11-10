# 前端代码审查 · V50 追加（#/agent / Muse / 季）

- 审查时间：2026-11-05
- 范围：`frontend/src/**` 全量静态审查 + 与 `backend/app/routers/*.py`、`pipeline/muse_video.py` 的字段对照；关键接口用 curl 实测（`/api/v6/agent/overview|series|season/*`、`/api/v6/muse/status|video/status`）。
- 方法：静态阅读 + 接口实测 + 既有 puppeteer 脚本复跑（AgentPage 2300+ 行、33 模板、季面板、Muse 双卡）。
- 结论：**P1 × 2、P2 × 5、P3 × 7，共 14 条**。无 P0（渲染异常有 ErrorBoundary 兜底，不会白屏）。
- 只读审查：本报告之外未改动任何源码。

| # | 严重度 | 位置 | 一句话 |
|---|---|---|---|
| 1 | P1 | AgentPage.tsx:371-395 / 397-413 | 工作流加载竞态：旧响应覆盖新模板 draft → 保存会把 A 的阶段写进 B 的合同 |
| 2 | P1 | AgentPage.tsx:594-599 | 季 run 返回 `job_id:null` 时把 job 置为 queued，运行态永真、按钮永久禁用 |
| 3 | P2 | AgentPage.tsx:402 | 切模板时 `setJob(null)` 丢掉进行中的 run（日志丢失 + 之后 409） |
| 4 | P2 | AgentPage.tsx:976 + lib/agent.ts:306,541 | 季 run 的「证据」三链接必然 404（目录布局 + run_id 长度） |
| 5 | P2 | AgentPage.tsx:542-564 | 快速切系列时集列表被旧响应覆盖（无请求守卫） |
| 6 | P2 | AgentPage.tsx:1483-1491 | 口型失败 item 无 out_url，仍渲染 `<a href="">` → 点击整页重载 |
| 7 | P2 | App.tsx:143-193 | 陈旧 `#/episodes/<id>` 把「episode 404」当成「后端离线」→ 永久红标 + 每 4s 重试风暴 |
| 8 | P3 | lib/agent.ts:871,943 | Muse 视频 provider 下拉/配色漏了 `replicate`/`useapi_muse` |
| 9 | P3 | lib/agent.ts:260 | 季成员 `last_run.status` 被丢弃 → 「状态」列永远空/只显示阶段数 |
| 10 | P3 | lib/agent.ts:117,127 | `stage_params` 未透传（run / season），页面无法启用 muse `apply` 回写 |
| 11 | P3 | AgentPage.tsx:239-241,947-958 | `loadAll` 并发静默丢弃 + 仍弹「已刷新」；无 try/finally 复位标志 |
| 12 | P3 | AgentPage.tsx:125-140 | 合法但非对象的 JSON（`[]`/`"x"`/`null`）被静默回退成旧 params |
| 13 | P3 | AgentPage.tsx:750-778 | Muse chip 只有 hover 打开、离开不关，浮层会一直挂着 |
| 14 | P3 | AgentPage.tsx:1530,1795 | `recent.url` 为空时 `<video src="#t=0.3">` 会去拉当前页面 |

---

## P1-1 · 工作流加载竞态会把 A 模板的阶段写进 B 模板

- **位置**：`frontend/src/components/agent/AgentPage.tsx:371-395`（`loadWorkflow`）、`:397-413`（`selectTemplate`）、`:447-477`（`saveWorkflow` 用 `selectedId` + `draft`）
- **触发**：在左栏快速连点模板 A → B（或 A 的 `GET /api/v6/agent/workflow/A` 比 B 慢），A 的响应最后到达。
- **为什么错**：`loadWorkflow` 只判断 `aliveRef`（组件是否卸载），不判断「响应回来时 id 是否仍是当前选中」。A 的响应会执行 `applyWorkflow(wf)`，把 `draft` 覆盖成 A 的阶段；此时 `selectedId` 已是 B。随后点「保存工作流」，`agentApi.saveWorkflow(selectedId /* B */, {... draft /* A 的阶段 */})` → 后端 `put_workflow` 强制 `wf["id"] = template`（`backend/app/routers/v6_agent.py:149`），于是 **B 的 `contract/v6/workflows/B.json` 被写成 A 的阶段顺序/开关/params**，且除「恢复默认」外没有回退手段（reset_workflow 只回到内建默认，用户对 B 的自定义改动丢失）。
- **最小修法**：加请求序号守卫（或改 AbortController）：

```ts
const wfSeq = useRef(0);
const loadWorkflow = useCallback(async (id: string) => {
  const seq = ++wfSeq.current;
  setWfLoading(true);
  setWfError("");
  try {
    const wf = await agentApi.workflow(id);
    if (!aliveRef.current || seq !== wfSeq.current) return; // 丢弃过期响应
    applyWorkflow(wf);
  } catch (err) {
    if (!aliveRef.current || seq !== wfSeq.current) return;
    /* …原兜底逻辑… */
  } finally {
    if (aliveRef.current && seq === wfSeq.current) setWfLoading(false);
  }
}, [applyWorkflow]);
```

---

## P1-2 · 季 run 返回 `job_id:null` 时把 job 置为 queued，按钮永久禁用

- **位置**：`frontend/src/components/agent/AgentPage.tsx:594-599`；契约侧 `backend/app/routers/v6_agent.py:407-409`
- **触发**：对「全部集数都有成片」的季点「整季生产（only_missing）」，或勾选 with_muse 但 members 为空。
- **为什么错**：后端此时返回 `{"ok": true, "job_id": null, "episodes": [], "message": "全部集数都有成片，无需生产"}`；前端 `setJob({ ...EMPTY_RUN, job_id: res.job_id /* "" */, status: "queued" })`：
  1. `runActive = isAgentActive(job)` → `pollTarget = ""`，轮询 effect 直接 return，永不进入终态；
  2. RunLog 永远显示「排队中」，且 `job.run_id` 为空 → 无证据块；
  3. 「季计划预览 / 整季生产 / 顶部主 CTA / 单集运行」全部带 `disabled={... || runActive}`，**页面进入死锁，只能刷新**；
  4. toast 还会提示「已启动整季生产（only_missing）：0 集排队」（误导）。
- **最小修法**：

```ts
const res = await agentApi.seasonRun(...);
if (!res.job_id || !res.episodes.length) {
  notify(res.message || "没有需要生产的集", "info");
  return;
}
setJob({ ...EMPTY_RUN, job_id: res.job_id, status: "queued" });
```
（`AgentSeasonRunResponse` 里补 `message?: string`。）

---

## P2-3 · 切模板时 `setJob(null)` 丢掉进行中的 run

- **位置**：`frontend/src/components/agent/AgentPage.tsx:397-413`（`:402 setJob(null)`）
- **触发**：启动一次单集 run（未 dry-run 时几分钟），在跑的过程中点左栏另一个模板。
- **为什么错**：`job` 被清空 → 轮询 effect 因 `pollTarget` 变空而停止，前端再也拿不到这次 run 的日志/结果；后端任务仍在跑且**单并发**（`v6_agent.py:212-216` 409），用户再次点「运行」只会得到 `409 已有 run 在跑`，而界面上没有任何入口能看回那个 job（除非记住 job_id 手拼 URL）。
- **最小修法**：切换前拦一下，或切换时保留 job：

```ts
if (isAgentActive(job)) {
  notify("有 run 在跑，等结束再切模板（或先记下 job_id）", "info");
  return;
}
```

---

## P2-4 · 季 run 的「证据」三链接必然 404

- **位置**：`frontend/src/components/agent/AgentPage.tsx:976`（`evidence = agentEvidence(job)`）→ `frontend/src/lib/agent.ts:541-550`（`agentEvidence`）与 `lib/agent.ts:306`（`run_id: result.run_id || basename(run_dir)`）
- **触发**：季生产/季计划任务完成后（`jobScope === "season"`）。
- **为什么错**：季 run 的 `run_dir` 形如 `outputs/agent_runs/season_<sid>_<ts>/`，里面只有**每集子目录**（实测 `season_undercity_arc_20261105_131453/` 下只有 3 个 `<ts>_<template>` 目录，无 `summary.md/run.json/events.jsonl`）；而 `agentEvidence` 拼的是 `/media/agent_runs/<basename>/summary.md` → 文件不存在。另外 `backend/app/routers/v6_agent.py:470` 的 `^[a-z0-9_]{8,40}$` 限制下，sid 较长的季（`season_` + sid + 15 位时间戳）会超过 40 字符，直接被 404。
- **最小修法**：季模式不渲染证据块（`jobScope === "season" ? [] : agentEvidence(job)`），或按 `result.episodes[].run_id` 列出每集证据。

---

## P2-5 · 快速切系列时集列表被旧响应覆盖

- **位置**：`frontend/src/components/agent/AgentPage.tsx:542-564`（`refreshSeason`）
- **触发**：在左栏连点系列 A → B（或 B 的 `GET /season/{sid}/status` 慢于 A 返回）。
- **为什么错**：与 P1-1 同类，缺请求守卫；A 的响应最后到达会把 `seasonEpisodes` 覆盖成 A 的集，而选中的是 B —— 面板标题是 B、集列表是 A，`seasonUpdated` 也被刷新，肉眼无法分辨。后续点「整季生产」用的仍是 B（`startSeason` 用 `seasonSid`），只是显示错位。
- **最小修法**：`if (sid !== seasonSidRef.current) return;`（`seasonSidRef` 随 state 同步）或加自增序号。

---

## P2-6 · 口型失败 item 无 out_url，仍渲染 `<a href="">`

- **位置**：`frontend/src/components/agent/AgentPage.tsx:1483-1491`
- **触发**：Muse 口型任务里某个镜头失败（`backend/app/routers/muse.py:159-161` 只写 `{shot, error, elapsed}`，无 `out_url`）。
- **为什么错**：`normalizeMuseJob` 归一成 `out_url: ""`，UI 仍渲染 `<a href="" target="_blank">` —— 点击会打开/跳转到当前页面（hash 路由被重置、整页重载），正在看的日志、选中的模板全部丢失；同时把「失败」项伪装成可下载。
- **最小修法**：`item.out_url ? <a …/> : <span className="cm-btn cm-btn-sm" aria-disabled>失败</span>`（`error` 放进 title）。

---

## P2-7 · 陈旧 `#/episodes/<id>` 被当成「后端离线」

- **位置**：`frontend/src/App.tsx:143-193`（`boot` 的 catch 只有 `setOffline(true)`）、`:199-206`（离线重试 4s）
- **触发**：打开一个已删除/不存在的剧集深链（`#/episodes/<gone>/script`），或 project/episodes 接口报错。
- **为什么错**：`boot()` 里 health 与 projects 都成功，随后 `api.episode(route.eid)` 抛 404 被同一个 catch 捕获 → `offline = true`。因为 `projects` 已有值，界面落到项目大厅，但顶部「后端」chip 变红，且 `offline` 每 4s 触发一次 `boot(true)` 再次访问同一个 404，形成**永久重试 + 误导性状态**（后端其实健康）。hashchange 路径（`openEpisodeById`）有兜底，boot 路径没有。
- **最小修法**：把 episode 拉取单独 try/catch，失败时 `window.history.replaceState(null, "", "#/projects")`（或 `setView({mode:"projects"})`），不要置 `offline`。

---

## P3-8 · Muse 视频 provider 下拉/配色与后端 checks 不同步

- **位置**：`frontend/src/lib/agent.ts:871-877`（`MUSE_VIDEO_PROVIDERS` 仅 auto/muse_cloud/musev_local/musepose_local/stub）、`:943-947`（`museVideoTone`）；后端 `pipeline/muse_video.py:60-85` 现返回 **6** 个 checks（新增 `replicate`、`useapi_muse`），推荐顺序 `musev_local → musepose_local → replicate → useapi_muse → muse_cloud → stub`。
- **触发**：部署里只有 Replicate/useapi 凭证（`state.provider = replicate`）。
- **为什么错**：下拉里没有这两个选项 → 只能保持 auto，无法显式切换/回退；`museVideoTone` 对未知 provider 返回 `"jade"`（本地色），把云端 provider 标成绿色。
- **最小修法**：选项由 `state.checks` 的 key 生成（`[...Object.keys(checks), "stub"]`），tone 用 `checks[provider]` 的 `configured/installed` 判定。

---

## P3-9 · 季成员 `last_run.status` 被丢弃

- **位置**：`frontend/src/lib/agent.ts:260-280`（`normalizeSeriesMember` → `last_run: lastRunText(...)` 只取 run_id）；后端 `v6_agent.py:286-287` 返回 `last_run: {run_id, status, started}`。
- **触发**：季面板「最近 run / 状态」列。
- **为什么错**：`lastRunText` 把对象压成 `run_id` 字符串，`status` 丢失；`AgentSeriesMember.status` 读的是顶层 `status`（后端没有该字段），所以「状态」列只能退化成阶段数标签，无法看出上次 run 是 ok 还是 failed。
- **最小修法**：`normalizeSeriesMember` 增加 `last_run_status`/`last_run_started`，季集列表用其渲染状态 chip。

---

## P3-10 · `stage_params` 未透传（run / season）

- **位置**：`frontend/src/lib/agent.ts:117-125`（`AgentRunRequest`）、`:127-133`（`AgentSeasonRunRequest`）；`AgentPage.tsx:502-539`（`startRun` body）、`:590-597`（`startSeason` body）；后端 `v6_agent.py:206-211`（run）与 `:391-402`（season，含 `muse_apply` → `stage_params.muse={apply,reassemble}`）。
- **触发**：想在页面上一键「口型跑完写回 shots 并重新合成」。
- **为什么错**：前端从不发送 `stage_params`，页面也没有对应开关；后端能力（muse apply / 任意阶段临时参数）在 UI 上不可达，只能走 CLI。属于契约已支持但前端未接。
- **最小修法**：`AgentRunRequest`/`AgentSeasonRunRequest` 增加 `stage_params?: Record<string, unknown>`；「Muse 口型修正」卡片加「写回成片并重新合成（muse.apply）」勾选，季面板同源透传 `muse_apply`。

---

## P3-11 · `loadAll` 并发静默丢弃 + 仍弹「已刷新」

- **位置**：`frontend/src/components/agent/AgentPage.tsx:239-241`（`if (inFlightRef.current) return;`）、`:947-958`（`refresh` 无条件 `notify("智能体数据已刷新")`）
- **触发**：首屏还在加载（或 run 结束触发的静默刷新）时点顶部刷新按钮。
- **为什么错**：`loadAll(true)` 因 `inFlightRef` 早退 → 数据没更新，但 `refresh` 继续往下走并弹成功 toast；另外 `loadAll` 没有 `try/finally`，中途抛异常会让 `inFlightRef` **永久为 true**（后续所有刷新失效）。
- **最小修法**：`loadAll` 返回 `boolean`（是否真的执行），`refresh` 据此决定 toast；整体包 `try/finally` 复位 `inFlightRef`。

---

## P3-12 · 非对象 JSON 被静默回退

- **位置**：`frontend/src/components/agent/AgentPage.tsx:125-140`（`parseParams` / `paramsInvalid`）
- **触发**：阶段 params 输入 `[]`、`"x"`、`null`、`123` 后保存。
- **为什么错**：`paramsInvalid` 只判断「能否 JSON.parse」，`parseParams` 对非对象返回 `fallback`（旧 params）——保存成功但参数没变，用户以为生效；面板显示输入值，与实际合同不一致。
- **最小修法**：把对象校验并入 `paramsInvalid`（`!value || typeof value !== "object" || Array.isArray(value)` → 标红阻止保存）。

---

## P3-13 · Muse chip 悬浮打开后不随鼠标离开关闭

- **位置**：`frontend/src/components/agent/AgentPage.tsx:750-756`（`openMuseChip` 绑定 `onMouseEnter`）、`:760-778`（只监听外部 mousedown / Esc / scroll）
- **触发**：鼠标扫过顶部 chip。
- **为什么错**：进入即打开，离开不关 → 浮层常驻，遮住下方运行面板；要关只能点空白或滚动。
- **最小修法**：chip（和 portal 浮层）加 `onMouseLeave` + 150ms 延时关闭，点击仍可固定。

---

## P3-14 · `recent.url` 为空时 `<video src="#t=0.3">`

- **位置**：`frontend/src/components/agent/AgentPage.tsx:1530`（口型 recent）、`:1795`（Muse 视频 recent）
- **触发**：`_media_url` 返回 `""`（产物路径不在 `outputs/` 下，`backend/app/routers/muse.py:78-83` 的 `ValueError` 分支）。
- **为什么错**：`src={"" + "#t=0.3"}` 被浏览器解析成「当前文档 + hash」，视频元素会以 HTML 页面为视频源请求一次，控制台出现 1 条 media 解码错误，缩略图空白。
- **最小修法**：`{src ? <video …/> : <span className="ag-muse-video" />}`。

---

## 未见问题（已逐项核对）

**轮询泄漏（Lead 重点）**：全部定时器都有 `clearInterval` + `alive` 守卫，页面卸载即停 —— agent runs `AgentPage.tsx:630-660`、Muse 口型 `:783-808`、Muse 视频 `:900-932`、AppShell `:154-160`、LibraryPage `:138`、`:158`、`:690`、CharactersPage `:504`、`:696`、`:926`、cinema/Jobs `:155`、`:166`、cinema/Templates `:175`、`:901`、studio/Studio `:95-101`、v5world/Render `:112-116`、`:119-140`、studio/Finishing `:278`、studio/Storyboard `:150`、PipelineConsole `:312-343`。**未发现切换页面后仍在跑的 2s 定时器。**

**JSON.parse 无 try**：仅 4 处（`lib/agent.ts:171`、`AgentPage.tsx:128/138/683`），全部包在 try 内。

**崩溃点**：`normalize*` 全部走 `rec/arr/str/num`（null/undefined 安全）；`seriesList[0]` 有长度守卫（`AgentPage.tsx:566-575`）；`AGENT_EDIT_OPS[0]` 是常量；`museVideoResult.out_url`、`job.result` 都有判空；`RunLog` 在 `job === null` 时全部条件渲染。渲染异常由 `components/ErrorBoundary.tsx:12-41` 兜底（不会白屏）。

**key 重复**：模板 `key={id}`、季 `key={template+index}`、Muse recent `key={name+url}`、证据链接 `key={file}` 均稳定；日志列表用 index（append-only，无重排）。

**单集 run 证据链接**：`run_dir` 末段 `<ts>_<template>`（≤40 字符），且 orchestrator 把 `run.json/events.jsonl/summary.md` 写在 run 根目录（`pipeline/agents/orchestrator.py:88-89,153`）→ `/media/agent_runs/<id>/…` 正确（stub 实测链接形态一致）。仅季 run 有问题（见 P2-4）。

**字段一致性（已实测）**：`overview`（render_type/kind/stack/ready/series[字符串]/stages.depends）与 `normalizeTemplate` 一致；`muse/status`（available.ok/providers/deps/venv_python/torch_in_env/reason + recent.url）一致；`muse/video/status`（state.provider/checks/docs/hint + recent.name/url/bytes/mtime）一致；`muse/video/jobs/{id}`（logs[].stage + result.provider/bytes/elapsed_sec/probe/out_url/stub）一致；`agent/season/{sid}/status`（episodes[].last_run 对象、stages 计数、final_url/poster_url）一致；`with_muse` 请求体与后端一致（后端会强制 `stages=["muse"]`、`only_missing=false`，前端 toast 已如实描述）。

**路由 / 旧 URL**：`#/v5/agent`、`#/v6/agent` → `#/agent` 实测生效；`#/agent/season` 深链、前进后退正常；`parseHash` 对未知子路径回退 workflow。

**未覆盖**：未跑 pytest/vitest；未对 `v5world/*` 历史页面逐个跑交互（按约定不报死代码数量）；P1-1 / P1-2 为代码级推断，未构造真实竞态去写合同文件。
