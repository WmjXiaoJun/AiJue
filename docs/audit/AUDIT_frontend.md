# 前端审查（第三轮 · 前两轮回归核对 + 增量新发现）

## 概览

- 审查时间：2026-11-06（本轮）
- 范围：`frontend/src` 全量（**56 个 `.ts/.tsx`，约 3.2 万行**；另有 22 个 `.css` 不在本轮范围），重点文件全覆盖：`App.tsx`、`components/agent/AgentPage.tsx`、`components/characters/CharactersPage.tsx`、`components/studio/*`、`components/library/LibraryPage.tsx`、`lib/templates.ts`、`lib/agent.ts`、`lib/creation.ts`、`lib/cinema.ts`、`lib/v5.ts`、`components/cinema/*`、`components/SettingsModal.tsx`、`vite.config.ts`、`tsconfig.json`、`package.json`
- 方法：`npx tsc --noEmit` 实跑 + 后端路由全量枚举（155 条装饰器 / 136 条去重路径）+ 前端 138 个 fetch 调用点自动抽取 + 实测后端（`127.0.0.1:8090` 在线，`/api/health` = 200 `{"status":"ok","version":"1.0.0","mode":"comfyui"}`）+ 前两轮报告逐条回归
- **只读**：本轮未改动 `frontend/` 下任何文件（含 `types.ts` / `SettingsModal.tsx` 的 WIP），基线干净；工具脚本只落在 `docs/audit/_tools/`
- 结论：**P0 × 0、P1 × 1、P2 × 4、P3 × 7（新发现 12 条）**；前两轮 14 条中 **13 条已修、1 条降级为残留**；V54 的 8 个断点中 **4 条已修、4 条仍在**
- 复核回填：Q1（P1-1）**保持 P1**，标注「代码级推断，未浏览器复现」，判据为**一致性缺陷**；Q2（P2-3）确认为**纯前端缺口**（合同 17 份齐全、`POST /projects {relic_talk_5min}` = 200）；另按 Lead 实测新增 **P3-7**（后端 `format_id` 零校验）

| 维度 | 本轮实测结论 |
|---|---|
| 类型检查 | `tsc --noEmit` **通过（exit 0，零输出，5.9 s）**；全仓库 `any` **0 处** |
| 接口契约 | 前端 115 条唯一 method+path **全部命中后端**（含 `generate-dynamic`）→ 前轮「零缺口」结论**成立** |
| Lead 假设 1（`/api/v6/agent/templates`、`/api/templates` = 404） | **属实但无影响**：两条 404 实测成立，但前端**从未调用**这两个路径（grep 0 命中）；模板数据来自 `/api/v6/agent/overview`（实测 33 条） |
| Lead 假设 2（`/api/settings/metaso-test` 被 SPA 挂载遮蔽 → 必然 404/405） | **不成立**：端点定义在 `main.py:2617`，SPA 挂载在 `main.py:2691`（**路由先注册**）；实测 `POST /api/settings/metaso-test` = **200** 并真实返回模型列表。且前端 `SettingsModal` 的 Metaso 按钮走的是 `PATCH /api/settings`，从不调这个端点 |
| Lead 复核 Q1（P1-1 定级） | **保持 P1**：判据是「同仓已有 `wfSeqRef`/`seasonSeqRef`/`inFlightRef` 范式，最外层导航漏了」＝**一致性缺陷**；连点是主路径操作，偶发也值得修。**未做浏览器复现**，已在标题与正文标注 |
| Lead 复核 Q2（P2-3 定级） | **纯前端缺口，P2 成立**：`contract/formats/` 17 份齐全、`POST /api/projects {"format_id":"relic_talk_5min"}` = **200**；且 **formats(`*_5min`) 与 templates(裸 id) 是两套命名空间**，别把本条与成片库缺 `relic_talk`（= B2）混为一谈 |
| Lead 复核新增（P3-7） | 后端 `POST /projects {"format_id":"no_such_format"}` 也是 **200** → `format_id` **零校验**；当前 UI 的 16 项白名单只是**掩盖**了这个洞，**不要**在前端补「只允许这 17 个」的兜底 |
| 端口 | `vite.config.ts` 代理 `127.0.0.1:8090` = 后端 `backend/run.ps1` 默认 `-Port 8090` = 实际在跑的服务，**一致**；`3080` 是 DSH Web GUI，与本应用无关 |

---

## 构建与类型检查结果（命令 + 输出）

```
PS> cd frontend; npx tsc --noEmit
EXIT=0  elapsed=5.9s
stdout lines: 0
```

- `frontend/package.json:9` → `"build": "tsc --noEmit && vite build"`，故 `tsc` 阶段即 `npm run build` 的类型门；**类型门通过**。
- 未执行 `vite build`：会重写 `frontend/dist`，与「基线要干净」冲突。现有 `frontend/dist/index.html` 与 `dist/assets/index-DOKOLhy0.js` 时间为 `2026-11-06 23:27:11`，晚于最新的 `src` 文件（`styles/settings.css` 23:27:02），**dist 不陈旧**。
- `tsconfig.json`：`strict: true`、`noEmit: true`、`noUnusedLocals/Parameters: false`、`noFallthroughCasesInSwitch: true`、`types: ["vite/client"]`、`include: ["src","vite.config.ts"]`。**`noUncheckedIndexedAccess` 未开**，这是本仓库唯一真实的类型盲区：`arr[0].field` 在 tsc 眼里永远安全。本轮把所有 `[0].`/`[i].` 下标访问逐处人工核对（`AgentPage.tsx:458/641`、`Final.tsx:440`、`CreateTemplateWizard.tsx:102/108`、`Assets.tsx:311/1100`、`CharactersPage.tsx:737-742/1003`），**均有长度守卫或同源同长数组**，未发现越界。
- `vite.config.ts`：`server.port 5173 / strictPort`、`preview.port 4173`，两者 proxy 都指向 `http://127.0.0.1:8090`（`/api`、`/media`），`changeOrigin: true`。`build.chunkSizeWarningLimit: 1400`（当前单 chunk 约 710 kB，阈值宽松但未触发）。

### 本次核对的两个「404」

| 请求 | 实测 | 前端是否调用 | 判定 |
|---|---|---|---|
| `GET /api/v6/agent/templates` | **404** | 否（`grep "agent/templates"` = 0） | 无害；模板由 `GET /api/v6/agent/overview` 一次返回（实测 `templates=33`） |
| `GET /api/templates` | **404** | 否（`grep '"/templates"'` = 0） | 无害；前端只用 `POST /api/v5/templates`（建模板）与 `GET /api/cinema/templates` |
| `POST /api/settings/metaso-test` | **200** `{"ok":true,"models":["MiniMax-H3",…]}` | 否（`grep metaso-test` = 0，只存在于后端） | Lead 假设**不成立**；顺带说明：该端点是**无前端入口的死端点** |
| `POST /api/episodes/__nope__/shots/generate-dynamic` | **404 `{"detail":"集不存在: __nope__"}`** | 是（`lib/api.ts:135`） | **路由存在**（错的是 eid），不是缺口 |

> 附带的平台行为（不是前端缺陷，但解释了为什么前端到处写 `status === 404 \|\| 405`）：未匹配的 `/api/*` 会落到 `app.mount("/")` 的 SPA 静态托管 —— GET 因 `SPAStaticFiles` 的 `startswith("api")` 保护返回 **404**，POST/PATCH/DELETE 由 Starlette StaticFiles 返回 **405**。实测 `POST /api/v6/agent/templates` 与 `POST /api/v6/agent/nonexistent-route` 都是 `405 Method Not Allowed`。所以「打错的 POST 路径」表现为 405 而非 404，前端的双码判断恰好兜住。

---

## 前两轮回归核对表

### A. `docs/audit/REVIEW_FRONTEND.md`（14 条）

| # | 原级别 | 标题 | 现状 | 现有代码证据 |
|---|---|---|---|---|
| 1 | P1 | 工作流加载竞态：A 的阶段写进 B 合同 | ✅ **已修** | `AgentPage.tsx:409` `const seq = ++wfSeqRef.current`；`:414/:417` `if (!aliveRef.current \|\| seq !== wfSeqRef.current) return`；`:427` finally 同条件复位；`:393` 记录 `draftForRef`，`:491-497` 保存前校验「draft 归属 ≠ selectedId 即阻止」双保险 |
| 2 | P1 | 季 run 返回 `job_id:null` → 运行态永真、按钮永久禁用 | ✅ **已修** | `AgentPage.tsx:676-684`：`if (!res.job_id) { setSeasonNote(message); notify(message,"info"); void refreshSeason(seasonSid); return; }`，不再 `setJob(queued)`；`agent.ts:146-155` `AgentSeasonRunResponse.message` 已补 |
| 3 | P2 | 切模板 `setJob(null)` 丢掉进行中的 run | ✅ **已修** | `AgentPage.tsx:438-442` 注释「V52：进行中的 run 不随模板切换丢弃」+ `if (!isAgentActive(job)) { setJob(null); setJobTemplate(""); }` |
| 4 | P2 | 季 run 的「证据」三链接必然 404 | ⚠️ **降级为残留** | 后端已补齐：`v6_agent.py:423-434` 在 `season_root` 写 `run.json / summary.md / events.jsonl`，实测 `GET /media/agent_runs/season_undercity_arc_20261106_232820/summary.md` = **200**。**残留**：`v6_agent.py:548` 的 `^[a-z0-9_]{8,40}$` 会拒掉 >40 字符的 run_id（见新发现 P2-4） |
| 5 | P2 | 快速切系列时集列表被旧响应覆盖 | ✅ **已修** | `AgentPage.tsx:617` `const seq = ++seasonSeqRef.current`；`:620/:626` 双守卫 `seq !== seasonSeqRef.current` 即丢弃 |
| 6 | P2 | 口型失败 item 无 `out_url` 仍渲染 `<a href="">` | ✅ **已修** | `AgentPage.tsx:1664` `{item.out_url ? (<a href={mediaUrl(item.out_url)} …>) : (<span …>)}`；`:1679` `{!item.out_url && item.error && <span className="ag-muse-fail-text">}` |
| 7 | P2 | 陈旧 `#/episodes/<id>` 被当成「后端离线」 | ✅ **已修** | `App.tsx:187-201`：episode 单独 `try/catch`，`ApiError.status === 404` → `notify("剧集不存在或已被删除：…")` + `history.replaceState("#/projects")`，不再进入外层 `catch { setOffline(true) }` |
| 8 | P3 | Muse 视频 provider 下拉缺 `replicate`/`useapi_muse` | ✅ **已修** | `agent.ts:914-922` 已含 7 项（auto/muse_cloud/replicate/useapi_muse/musev_local/musepose_local/stub）；`agent.ts:986-990` `museVideoTone` 把 `muse_cloud\|replicate\|useapi_muse` 归 `gold` |
| 9 | P3 | 季成员 `last_run.status` 被丢弃 | ✅ **已修** | `agent.ts:67-68` 新增 `last_run_status/last_run_started`；`:292-293` `lastRunField(...)` 填充；UI `AgentPage.tsx:2272-2277` 用 `episode.last_run_status \|\| episode.status` 渲染状态 chip + `title="开始于 …"` |
| 10 | P3 | `stage_params` 未透传 | ✅ **已修** | `agent.ts:130` `stage_params?`；`:141` 季侧同名字段；`AgentPage.tsx:567-580` 校验 JSON 对象 → `:592` 发送 `stage_params`；`:673` 季侧发送 `muse_apply` |
| 11 | P3 | `loadAll` 并发静默丢弃 + 无 try/finally，仍弹「已刷新」 | ✅ **已修** | `AgentPage.tsx:269-339`：签名改为 `Promise<boolean>`，`:270` 早退返回 `false`，`:273/:332` `try {…} finally { inFlightRef.current = false; …}`；`:1074-1083` `refresh` 按返回的 `ok` 决定 toast 文案 |
| 12 | P3 | 非对象 JSON（`[]`/`null`/标量）静默回退 | ✅ **已修** | `AgentPage.tsx:135-144` `paramsInvalid` 明确拒绝 `!value \|\| typeof !== "object" \|\| Array.isArray`；`:498-502` 保存前拦截并提示；`:146-157` `stageParamsError` 同规则 |
| 13 | P3 | Muse chip hover 打开后不随鼠标离开关闭 | ✅ **已修** | `AgentPage.tsx:855-859` `scheduleCloseMuseChip`（180 ms 延时 + `museChipPinned` 时不关）；`:2349` chip `onMouseLeave={onHoverClose}`、`:2365` 浮层 `onMouseLeave={onPopClose}`；`:875` 卸载清 timer |
| 14 | P3 | `recent.url` 为空时 `<video src="#t=0.3">` | ✅ **已修** | `AgentPage.tsx:1718-1722` 与 `:1987-1991` 均为 `{src ? <video src={src + "#t=0.3"} …/> : <span className="ag-muse-video is-empty" aria-hidden/>}` |

**合计：13 条已修，1 条（P2-4）由「必然 404」降级为「长 sid 边界下 404」。无回归。**

### B. `docs/audit/UI_LOGIC_V54.md`（B1–B8 断点）

| # | 断点 | 现状 | 证据 |
|---|---|---|---|
| B1 | 成片无法删除 | ✅ **已修（前端开关已开）** | `lib/v5.ts:425` `FINAL_DELETE_ENABLED = true`、`:426` `FINAL_DELETE_PATH = "/v5/outputs/{template}"`；后端 `DELETE /api/v5/outputs/{template}` 已存在（实测 `DELETE /api/v5/outputs/__nope__` = **400**「模板名非法」，路由存在而非 404）。✗ 但 `v5.ts:416-424` 的注释仍写着「后端目前没有删除成片的接口…所以默认关闭」——注释与代码相反（见 P3-3） |
| B2 | 成片库只列 30/33 | ❌ **未修** | 数据源仍是 `cinemaApi.templates()`（`LibraryPage.tsx:105`）；实测 `GET /api/cinema/templates` = **30** 条、`summary.total=30`；而 `GET /api/v5/outputs?include_custom=true` = **33** 条 |
| B3 | 成片卡 `0:00` / 尺寸 `—` | ❌ **未修** | 同上，`final.duration/size` 来源未变；`steps.tsx:261` 的「时长未记录」降级仍在 |
| B4 | 长版接口曾 404 | ✅ **已修** | 实测 `GET /api/v5/longform/status` 与 `POST /api/v5/longform/run` 均在（`longform.py:111/76`）；`LongformPanel.tsx:147` 的 404 自检降级是保底，不再是主路径 |
| B5 | 工作台 tab 数 | ✅ **无问题** | `App.tsx:16-18` `TabKey = "script"\|"assets"\|"storyboard"\|"generate"\|"finish"`（5 个） |
| B6 | 陈旧剧集深链 | ✅ **已修** | 同 REVIEW #7（`App.tsx:187-201`） |
| B7 | 角色页试镜/宣传/对白 404 兜底 | ❌ **未修（且文案过期）** | 兜底仍在 `CharactersPage.tsx:517/564/709/756/810/953/1013/1068/1086/1272`，但对应后端**全部已实现**（`character_audition.py`、`character_promo.py`、`character_dialogue.py`、`character_showcase.py`、`v5_catalog.py:528`）→ 用户若真撞上 404（例如拼错 id）会看到「后端 V32 实现中」的错误归因 |
| B8 | 长版批量会真占 GPU、无二次确认 | ❌ **未修** | `LongformPanel.tsx:80-100` `start("all")` 直接 `longformApi.run({all:true, duration:300})`；对比 `Storyboard.tsx:228` 的批量动态**有** `window.confirm` |

### C. `docs/audit/PART_FRONTEND.md` §7.3「路由层隐患」勘误（3 条）

| 原结论 | 复核 | 证据 |
|---|---|---|
| ① `GET /api/v5/voice/{character}` 在 `v5_studio.py` 与 `v5_catalog.py` **重复注册** | ❌ **表述有误** | `v5_catalog.py` 并无 `GET /voice/{character}`，只有 `GET /voices`(426)、`POST /voice/{c}/lock`(503)、`POST /voice/{c}/preview`(528) 与通配 `GET /{kind}/{item_id}`(187)。真正的重复不存在 |
| ② `GET /{kind}/{item_id}` 先于 `/voice/{character}` 注册，靠 include 顺序兜住 | ✅ **属实（潜在）** | `main.py` include 顺序：`v5_studio`（约 2578）早于 `v5_catalog`（约 2582）；`/api/v5/voice/guanyu` 命中 `v5_studio.py:538`。3 段的 `/voice/{c}/preview` 不会被 2 段通配命中。属顺序依赖隐患，当前安全 |
| ③ `cinemaApi.templates()` 依赖 Starlette 307 尾斜杠重定向 | ❌ **不成立** | `cinema_templates.py:1825-1826` 对同一函数**同时注册** `@router.get("")` 与 `@router.get("/")` → `/api/cinema/templates` 直接命中，实测 **200 无重定向** |

### D. `PART_FRONTEND.md` §8「过期文案」复核

**仍全部过期、一条未清理**（`grep "尚未就绪（后端"` 命中约 21 处，加上「接口未就绪」共 45 处文案）。见新发现 P3-1。

---

## 接口契约对照表（前端调用 / 后端是否存在 / 结论）

**方法**：Python 全量枚举后端装饰器（155 条，含**跨行装饰器** —— 上一轮我自己的第一版提取器漏了 `main.py:1462-1465` 的 `@router.post(\n "/episodes/{eid}/shots/generate-dynamic"`，修正后此路径**存在**），再把前端 138 个 `fetch/request/get/post` 调用点的**第一参数按字符串拼接重建**，`${…}`/变量 → `{}`，与后端路径模板对齐比对。

**结果：115 条唯一 method+path，全部命中；真实缺口 0 条。** 全量机读表：`docs/audit/_tools/contract_table.txt`（138 行调用点）、`docs/audit/_tools/backend_routes.json`（155 条后端路由）。

下表中 5 行为**提取器产物**（非缺口），已标注：

| # | 前端调用（method path） | 前端位置 | 后端 | 结论 |
|---|---|---|---|---|
| 1 | GET `/health` | `lib/api.ts:92` | `main.py:504` | 命中 |
| 2 | GET `/projects` / POST `/projects` | `lib/api.ts:94,97` | `main.py:517,522` | 命中 |
| 3 | GET `/projects/{pid}` / PATCH（死代码） | `lib/api.ts:95`、`studio/Templates3D.tsx:110` | `main.py:558,563` | 命中 |
| 4 | GET/POST `/projects/{pid}/episodes` | `lib/api.ts:98,100` | `main.py:621,638` | 命中 |
| 5 | GET/PATCH `/episodes/{eid}` | `lib/api.ts:102,104` | `main.py:711,719` | 命中 |
| 6 | GET/POST `/episodes/{eid}/assets` | `lib/api.ts:106,108` | `main.py:732,743` | 命中 |
| 7 | PATCH `/assets/{aid}` | `lib/api.ts:110` | `main.py:760` | 命中 |
| 8 | GET/POST `/episodes/{eid}/shots` | `lib/api.ts:112,114` | `main.py:1338,1362` | 命中 |
| 9 | DELETE/PATCH `/shots/{sid}` | `lib/api.ts:115,117` | `main.py:1423,1349` | 命中 |
| 10 | POST `/shots/{sid}/generate`（含 `{mode:"dynamic"}` 回退） | `lib/api.ts:119,123` | `main.py:1444` | 命中（`:1452-1457` 接受 `mode=dynamic`） |
| 11 | **POST `/episodes/{eid}/shots/generate-dynamic`** | `lib/api.ts:135` | **`main.py:1462`（跨行装饰器）** | **命中**；实测 404 = 「集不存在」 |
| 12 | GET `/shots/{sid}/versions`、POST `…/{vid}/select` | `lib/api.ts:140,142` | `main.py:1507,1518` | 命中 |
| 13 | POST `/episodes/{eid}/storyboard/generate` | `lib/api.ts:145` | `main.py:1537` | 命中 |
| 14 | POST `/episodes/{eid}/render` | `lib/api.ts:149` | `main.py:1564` | 命中 |
| 15 | GET `/jobs` / `/jobs/{jid}` / POST `/jobs/{jid}/retry` | `lib/api.ts:152-154` | `main.py:1579,1596,1601` | 命中 |
| 16 | GET `/episodes/{eid}/costs` | `lib/api.ts:156` | `main.py:2381` | 命中 |
| 17 | GET/POST `/projects/{pid}/characters` | `lib/api.ts:159,164,170` | `main.py:825,842` | 命中 |
| 18 | GET/PATCH `/characters/{cid}` | `lib/api.ts:160,181` | `main.py:905,912` | 命中 |
| 19 | POST `/characters/{cid}/generate`、GET `…/files`、GET `…/concepts`、POST `…/concept/generate`、POST `…/portrait/from-concept`、POST `…/h3-animate` | `lib/api.ts:183,187,190,192,196,216` | `main.py:951,972,1160,1127,1175,1223` | 命中 |
| 20 | POST `/episodes/{eid}/export`、`/localize`、GET `/localizations`、GET `/localizations/{lid}` | `lib/api.ts:238,242,251,252` | `main.py:1263,1284,1319,1330` | 命中 |
| 21 | GET `/publish/config`、POST `/episodes/{eid}/publish`、GET `…/publishes`、GET `/publishes/{id}` | `lib/api.ts:255,257,258,259` | `main.py:1960,2226,2362,2373` | 命中 |
| 22 | GET `/episodes/{eid}/publish/preflight`（裸 fetch + `res.ok` 检查） | `lib/studio.ts:261` | `main.py:2199` | 命中 |
| 23 | GET `/llm/config` / `/llm/providers` / POST `/llm/test` | `lib/api.ts:262,263,265` | `main.py:1720,1734,1763` | 命中 |
| 24 | POST `/projects/{pid}/generate-script`（60 s 超时 + 内部 job 轮询） | `lib/api.ts:268` | `main.py:1776` | 命中 |
| 25 | GET/PATCH `/settings` | `lib/api.ts:297,299` | `main.py:2406,2411` | 命中 |
| 26 | GET `/cinema/overview` / `shots` / `report` / `workers` | `lib/cinema.ts:351,352,363,364` | `cinema3d.py:888,917,1189,1214` | 命中 |
| 27 | PATCH `/cinema/shots/{sid}`、POST `/cinema/generate`、`/cinema/assemble` | `lib/cinema.ts:354,356,358` | `cinema3d.py:969,1008,1066` | 命中 |
| 28 | GET `/cinema/jobs`、`/cinema/jobs/{id}`、POST `…/{id}/cancel` | `lib/cinema.ts:359,360,362` | `cinema3d.py:1103,1117,1123` | 命中 |
| 29 | GET `/cinema/templates`（`?look=`）| `lib/cinema.ts:368` | `cinema_templates.py:1825`（`""` 与 `"/"` 双注册） | 命中（**无需 307**） |
| 30 | GET `/cinema/templates/{id}`、POST `…/generate`、`…/assemble`、GET/POST `…/{tid}/publish-package` | `lib/cinema.ts:372,375,378,384` | `cinema_templates.py:2857,1912,2014,2820,2796` | 命中 |
| 31 | POST `/cinema/publish-packages/batch`、GET `…/batch/{id}`、POST `…/cancel`、GET `…/batches` | `lib/cinema.ts:778,784,786,788` | `publish_batches.py:217,300,319,309` | 命中 |
| 32 | POST `/v5/characters`、POST `/v5/templates`、POST `/v5/characters/{c}/art`、GET `/v5/characters/{c}/assets` | `lib/creation.ts:265,267,280,284` | `v5_create.py:813,1187,963,1012` | 命中 |
| 33 | POST `/v5/characters/{c}/audition`、GET `/auditions/{id}`、POST `…/cancel`、GET `…/{c}/auditions` | `lib/creation.ts:406,408,410,412` | `character_audition.py:625,832,842,864` | 命中 |
| 34 | POST `/v5/characters/{c}/promo`、GET `/promos/{id}`、POST `…/cancel`、GET `…/{c}/promos` | `lib/creation.ts:513,514,516,518` | `character_promo.py:551,742,752,774` | 命中 |
| 35 | POST `/v5/characters/dialogue`、GET `/v5/dialogues`、GET `/v5/dialogues/{id}`、POST `…/cancel` | `lib/creation.ts:676,681,677,679` | `character_dialogue.py:563,822,790,800` | 命中 |
| 36 | POST/GET `/v5/character-showcase/split-packages`、`/publish-package` | `lib/creation.ts:768,770,784,787` | `character_showcase.py:634,640,378,392` | 命中 |
| 37 | GET `/v5/longform/status`、POST `/v5/longform/run` | `lib/longform.ts:169,173` | `longform.py:111,76` | 命中 |
| 38 | POST `/v5/outputs` 型删除 `DELETE /v5/outputs/{template}` | `lib/v5.ts:426,437` | `v5_studio.py:304` | 命中（实测 400 = 模板名非法） |
| 39 | GET `/v5/outputs` / `/v5/catalog` / GET·PUT `/v5/{kind}/{item_id}` | `lib/v5.ts:443,444,447,450` | `v5_studio.py:265`、`v5_catalog.py:81,187,208` | 命中 |
| 40 | GET `/v5/series`、POST `/v5/series/{sid}/export` | `lib/v5.ts:455,457` | `v5_series.py:104,204` | 命中 |
| 41 | GET `/v5/voices`、`/v5/casting`、GET·PUT `/v5/voice/{c}`、POST `…/preview`、`…/lock` | `lib/v5.ts:462,464,466`、`lib/voice.ts:187,190,196,200,208` | `v5_catalog.py:426,449,538,559,528,503` | 命中 |
| 42 | POST·GET `/v5/render`、GET·DELETE `/v5/render/{job}` | `lib/v5.ts:483,487,488,490` | `v5_studio.py:716,784,796,804` | 命中 |
| 43 | GET `/v5/editorial/{t}`、`/v5/storyboard/{t}` | `lib/v5.ts:492,494` | `v5_studio.py:412,432` | 命中 |
| 44 | GET `/v5/preview/scene/{id}`、`/v5/preview/character/{id}`（图片 URL，走 `<img src>`） | `lib/v5.ts:519,526` | `v5_catalog.py:161`（`/preview/{kind}/{item_id}`） | 命中（通配段） |
| 45 | POST `/pipeline/run`、GET `…/{id}`、POST `…/cancel`、GET `/pipeline/runs`、POST `/pipeline/batch`、GET `…/{id}`、POST `…/cancel`、GET `/pipeline/batches` | `lib/pipeline.ts:109,114,116,118,215,220,222,224` | `pipeline_ui.py:698,704,713,744,1053,1171,1190,1180` | 命中 |
| 46 | GET `/v6/agent/overview`、`/v6/agent/workflow/{t}`、PUT 同路径、POST `/v6/agent/run`、GET `/runs`、`/runs/{id}`、POST `/edit`、GET `/art`、`/series`、POST `/season/{sid}/run`、GET `/season/{sid}/status` | `lib/agent.ts:374,377,381,395,400,403,414,416,421,439,431` | `v6_agent.py:124,150,156,210,243,252,518,531,350,457,510` | 命中（`/v5/series` 回退亦命中） |
| 47 | GET `/v6/muse/status`、POST `/v6/muse/sync`、GET `/v6/muse/jobs/{id}`、GET `/v6/muse/video/status`、POST `/v6/muse/video`、GET `/v6/muse/video/jobs/{id}` | `lib/agent.ts:455,458,463,466,471,485` | `muse.py:125,199,248,277,326,362` | 命中 |
| 48 | `/media/**`（`mediaUrl/mediaSrc` 前缀，生成 `<img>/<video>/<a href>`） | `lib/api.ts:35`、`lib/v5.ts:532-554` | `main.py:2644` `/media` 静态挂载 + `v5_studio.py:460-485` + `v6_agent.py:546` | 静态，命中 |

**提取器产物（非缺口，5 行）**：`/cinema/templates{}`（`cinema.ts:368` 的三元拼接）、`/jobs{}`（`api.ts:152` 的 `qs()`）、`/v5/outputs{}`（`v5.ts:443` 的 `qs()`）—— 三者的真实路径均已在上表命中；`/v5/preview/scene/{}`、`/v5/preview/character/{}` —— 由后端通配 `{kind}/{item_id}` 命中（上表 44）。

---

## P0 / P1 / P2 / P3 逐条（本轮新发现）

> P0 = 0 条（无白屏/无数据破坏；渲染异常有 `components/ErrorBoundary.tsx` 兜底）。以下每条按「位置 / 问题 / 证据 / 影响 / 建议修法」展开。

### P1-1 · `App.tsx` 全局导航无请求守卫：连点项目/剧集，**先点的赢**（旧响应覆盖新视图）（代码级推断，未浏览器复现）

- **位置**：`frontend/src/App.tsx:312-331`（`openProject`）、`:239-254`（`openEpisodeById`）、`:334-350`（`switchEpisode`）；触发入口 `:588`（`onOpenProject={(project) => void openProject(project)}`）与 `:257-310`（`hashchange → openEpisodeById`）
- **问题**：三个导航函数都是「先 `await` 拉 `/projects/{id}/episodes` 或 `/episodes/{id}`，再 `setView/setEpisodes/setTab` + 结尾改写 `window.location.hash`」。**没有任何请求序号或 AbortController 守卫**，也**没有 in-flight 互斥**。
- **判据（为什么算缺陷而不是洁癖）**：这是一条**一致性缺陷** —— 同一条「hash 变更 → 拉数据 → 覆盖视图」的路径，仓库里**已有成熟范式且到处在用**，唯独最外层漏了：

  | 位置 | 守卫 | 作用 |
  |---|---|---|
  | `AgentPage.tsx:409` + `:414/:417/:427` | `wfSeqRef` 序号 | 丢弃过期的 workflow 响应（原 P1-1 修复） |
  | `AgentPage.tsx:617` + `:620/:626` | `seasonSeqRef` 序号 | 丢弃过期的季状态响应（原 P2-5 修复） |
  | `LibraryPage.tsx:96,102` | `inFlightRef` 互斥 | 同一时刻只跑一轮目录加载 |
  | `studio/Render.tsx:113`、`Storyboard.tsx:116`、`Finishing.tsx:233`、`cinema/Templates.tsx:145`、`cinema/Jobs.tsx:119`、`PipelineConsole.tsx:124`、`V5World.tsx:94` | `inFlightRef` / `*Busy` | 同上 |
  | **`App.tsx:239-254 / 312-331 / 334-350`** | **无** | ← **唯一缺口**，且是**所有页面共用的最外层入口** |

  「连点两个项目/两期剧集」是主路径操作（项目大厅卡片、工作台剧集切换器都是一点即发），不是边角交互；慢后端（SQLite 冷查询、期数多的项目）下必然可触发。**即使偶发也值得修**，因为失败表现是"导航到错的项目且 URL 被一起改写"，用户无法靠刷新自愈。
- **复现状态**：**未做浏览器复现**（本轮无 CDP/浏览器会话，未做 Slow 3G 连点实证）。判据为代码级：`Promise` 无序号比较、`setView` 无条件执行、`window.location.hash` 在 `await` 之后无条件改写。修复成本约 10 行（见下），建议按 P1 排期而非降级。
- **证据**：

```ts
// App.tsx:312-331
const openProject = useCallback(async (project: Project) => {
  try {
    const episodes = await api.episodes(project.id);       // ← A 的请求
    if (!episodes.length) { … return; }
    const episode = episodes.find((item) => item.no === 1) || episodes[0];
    setEpisodes(episodes);
    setView({ mode: "workspace", project, episode });       // ← 谁后返回谁生效
    setTab("script");
    setTaskStats({ activeJobs: 0, cost: episode.cost || 0 });
    window.location.hash = hashFor(episode.id, "script");    // ← 连 hash 都被改回 A
  } catch (err) { notify(errorText(err), "error"); }
}, [notify]);
```

- **触发**：项目大厅连点 A → B（A 的 `/projects/A/episodes` 慢于 B 返回，例如 A 有 12 期、B 只有 1 期，或后端 SQLite 首次冷查询）。剧集切换器连点 `第1期 → 第2期`（`switchEpisode`）同理会停在 `.1`。
- **影响**：用户点的是 B，最终停在 **A** 的工作台，且 URL 被改写为 `#/episodes/A/script` —— 界面与意图不符、且因为 hash 被同步改写，浏览器「后退」也回不到 B；`setEpisodes`/`taskStats` 与 `view` 来自同一批旧数据，暂无不一致，但**导航语义错误**。此缺陷在 V52 的 404 修复（`App.tsx:187-201`）之后成为 `boot()` 路径与 `hashchange` 路径的主竞态。
- **建议修法**（与 `AgentPage` 同构）：

```ts
const navSeqRef = useRef(0);
const openProject = useCallback(async (project: Project) => {
  const seq = ++navSeqRef.current;
  try {
    const episodes = await api.episodes(project.id);
    if (seq !== navSeqRef.current) return;            // 过期响应丢弃
    …
  } catch (err) { if (seq === navSeqRef.current) notify(errorText(err), "error"); }
}, [notify]);
```
`openEpisodeById` / `switchEpisode` 共用同一个 `navSeqRef`（它们是同一个「当前视图」的所有者）。

---

### P2-1 · `LibraryPage` 刷新按钮会**永久卡在 refreshing**（`inFlightRef` 早退不复位）+ 闭包旧值使 toast 守卫失效

- **位置**：`frontend/src/components/library/LibraryPage.tsx:101-118`（`load`）、`:205-209`（`refresh`）、`:337`（`refreshing={refreshing}` 驱动 AppShell 按钮态）
- **问题**：`refresh()` 先 `setRefreshing(true)`，再 `await load(true)`；而 `load()` 在并发时**直接 `return`**（`inFlightRef.current === true`），既不执行 `setLoading(false)` 也不执行 `setRefreshing(false)`。**唯一复位 `refreshing` 的地方是 `load()` 的尾部（:117）**，所以早退路径下按钮永远保持 loading/disabled。
- **证据**：

```ts
// LibraryPage.tsx:101-118
const load = useCallback(async (silent: boolean) => {
  if (inFlightRef.current) return;          // ← 早退：不复位任何 loading 标志
  inFlightRef.current = true;
  if (!silent) setLoading(true);
  const [catRes, tplRes] = await Promise.allSettled([…]);
  …
  inFlightRef.current = false;
  setLoading(false);
  setRefreshing(false);                     // ← 只有走到这里才复位
}, []);

// LibraryPage.tsx:205-209
const refresh = useCallback(async () => {
  setRefreshing(true);
  await load(true);                         // ← 若 load 早退，这里什么都不发生
  if (refreshing === false) notify("模板与素材数据已刷新", "success");  // ← 闭包旧值，恒为 false
}, [load, notify, refreshing]);
```

- **触发**：首屏 `useEffect → load(false)`（:120-122）还在跑时点顶栏刷新；或点「观感」切换（`switchLook → load(false)`，:125-134）后立刻点刷新。
- **影响**：① 顶栏刷新按钮永久 spinner/禁用，直到下一次 `load()` 成功（只能靠再切一次观感或重进页面解锁）；② `refreshing === false` 用的是本次渲染的旧值，恒成立 → 连点两次会弹两次「已刷新」，实际上第二次根本没刷新（静默丢弃，用户以为数据已更新）。
- **建议修法**：`load` 早退时返回 `false` 并**不**由 `refresh` 自行复位；或把 `refreshing` 的复位搬进 `refresh` 的 `finally`：

```ts
const refresh = useCallback(async () => {
  setRefreshing(true);
  try { if (await load(true)) notify("模板与素材数据已刷新", "success"); }
  finally { setRefreshing(false); }          // 不再依赖 load 的尾部
}, [load, notify]);
```
（`load` 需按 `AgentPage.loadAll` 的写法返回 `boolean`，`:269` 已是该范式。）

---

### P2-2 · `AgentPage` 三条轮询：无 in-flight 互斥、无 `document.hidden` 守卫 → 重叠请求 + 后台标签页持续打点

- **位置**：`AgentPage.tsx:717-747`（run 状态，**1500 ms**）、`:900-929`（Muse 口型，2000 ms）、`:1021-1049`（Muse 视频，2000 ms）
- **问题**：三处都是 `window.setInterval(async () => { const next = await agentApi.…; … }, 1500|2000)`：
  1. **无**「上一轮未回来就跳过」的互斥（对比 `LibraryPage.load` 的 `inFlightRef`、`cinema/Jobs` 的 `jobsBusy`），后端慢时请求会叠加：请求超时 20 s（`agent.ts:193` 默认 `timeoutMs = 20000`）÷ 1.5 s ≈ **最多 13 个并发**同域请求；
  2. **无** `document.hidden / visibilityState` 判断 —— 同仓库 `cinema/Templates.tsx:174-179`、`cinema/Jobs.tsx:164-170`、`studio/Render.tsx:153-159`、`studio/Storyboard.tsx:148-154`、`studio/Finishing.tsx:268-282` 都做了，`AgentPage` 是唯一遗漏的重点页面（`grep document.hidden|visibilityState` in AgentPage = **0**）。
- **证据**：

```ts
// AgentPage.tsx:717-747（节选）
useEffect(() => {
  if (!pollTarget || (pollStatus !== "queued" && pollStatus !== "running")) return;
  let alive = true;
  const timer = window.setInterval(async () => {
    try {
      const next = await agentApi.runStatus(pollTarget);   // 20s 超时，1500ms 一次 → 可叠加
      if (!alive || !aliveRef.current) return;
      setPollError(""); setJob(next);
      …
    } catch (err) { … }
  }, 1500);
  return () => { alive = false; window.clearInterval(timer); };
}, [pollTarget, pollStatus, jobScope, seasonSid, notify, refreshSeason, loadAll]);
```
- **影响**：① 后端忙时前端每秒级叠加请求，且**旧响应可能后到**覆盖 `job`（状态回退一帧：日志条数抖动、进度回跳）；② 标签页切到后台后仍以 1.5 s 打点，长时间挂机会产生大量无意义请求（run 可能跑 6–10 分钟 GPU，期间用户常切走）；③ 与 `pollStatus` 的终态判断配合下会**额外多打一轮**（`isAgentActive(next)` 为 false 时不会立即 clearInterval，只依赖 effect 依赖变化重跑）。
- **建议修法**：抽一个 `useInterval(fn, delay, enabled)`：内部 `busyRef` 互斥 + `if (document.hidden) return;`；终态时 `window.clearInterval(timer)` 或直接 `setJob` 后由 effect 卸载。run 轮询建议 2 s、Muse 2 s（当前 1.5 s 无必要）。

---

### P2-3 · `lib/templates.ts` 与 `contract/formats` 漂移：**缺 `relic_talk_5min`** → 新建项目少一档、文案数字错（**纯前端缺口，Lead 已实测确认**）

- **位置**：`frontend/src/lib/templates.ts:20-1462`（`TEMPLATES` 共 **16** 条）、消费方 `components/platform/PlatformHome.tsx:283`（文案「从 16 个节目模板开始」）、`:394-407`（新建项目表单初值）、`:450-452`（模板选择列表）
- **问题**：文件头注释写「与 `contract/formats/*.json` 对齐」，但 `contract/formats/` 有 **17** 份格式合同，前端少了 `relic_talk_5min`（国宝会说话 · 5 分钟，6 段 / 306 s）。**合同与后端都是齐的**（Lead 实测 `POST /api/projects {"format_id":"relic_talk_5min"}` = **200**，项目创建成功且 `id/format_id` 正确回填），缺的只有 `lib/templates.ts` 这一项 + 文案。其余 16 条的 `segments` 数、`target_duration`、`aspect` 与合同**逐字段一致**。
- **证据**（`python docs/audit/_tools/tpl_diff.py` 实测输出）：

```
id                          FE segs  BE segs   FE dur   BE dur   FE asp   BE asp
classics_5min                     5        5    311.0      311     16:9     16:9
…
relic_talk_5min                   -        6        -      306        -     16:9  <== MISSING IN FRONTEND
roundtable_5min                   6        6    296.0      296     16:9     16:9
…
```
`PlatformHome.tsx:407`：`const template = TEMPLATES.find((item) => item.id === formatId) || TEMPLATES[0];` —— 下拉数据源即该常量数组。

- **⚠️ 两套命名空间不是同一件事（易误读，务必区分）**：

  | 命名空间 | 值域 | 来源 | 谁在用 |
  |---|---|---|---|
  | **formats**（`*_5min`） | 17 份：`roundtable_5min`、`relic_talk_5min`、`classics_5min` … | `contract/formats/*.json` | 大厅「新建项目」的 `format_id`（`PlatformHome` 下拉 → `POST /api/projects`） |
  | **templates**（裸 id） | 33 个：`relic_talk`、`relic_talk_3d`、`custom_tpl_v46_*` … | `contract/v5/templates/`、`contract/v6/workflows/` | 智能体页 / 管线 / 成片库（`/api/v6/agent/overview`、`/api/cinema/templates`） |

  所以：**本 P2-3 缺的是 formats 命名空间里的 `relic_talk_5min`**；而成片库/智能体页里的 `relic_talk`、`relic_talk_3d` 属于 templates 命名空间，它们"看不到"是 `UI_LOGIC_V54` 的 **B2**（`/api/cinema/templates` 只回 30/33），**与本条无关**。两条不要混为一谈。
- **影响**：① 大厅「新建项目」的模板下拉**没有「国宝会说话」**，`format_id="relic_talk_5min"` 在 UI 上不可选（后端完全支持）；② 文案「从 16 个节目模板开始」永久错误（应为 17）；③ 若有人通过 URL/接口建了该 format 的项目，`templateLabel("relic_talk_5min")`（`templates.ts:1464-1467`）会原样回退成裸 id，卡片显示 `relic_talk_5min` 而不是「国宝会说话 · 5 分钟」。
- **建议修法**：补第 17 条（照 `contract/formats/relic_talk_5min.json` 的 6 段 / 306 s 生成）+ 改正文案；长期把该常量改成**由 `GET /api/v5/catalog`（或 `contract/formats` 经 `/api/v5/{kind}/{id}`）驱动**，静态数组只留 fallback，避免再次漂移。
  **但不要顺手在前端加「只允许这 17 个」的白名单兜底** —— 见 P3-7，那会把一个后端校验洞藏得更深。

---

### P2-4 · 季 run 证据链接：`run_id` 超过 40 字符时被后端媒体网关正则拒绝（残留自原 P2-4）

- **位置**：`frontend/src/components/agent/AgentPage.tsx:1102`（`const evidence = useMemo(() => agentEvidence(job), [job])`，**未按 `jobScope` 区分**）、`frontend/src/lib/agent.ts:576-584`（拼 `/media/agent_runs/<run_id>/<file>`）、后端 `backend/app/routers/v6_agent.py:546-553`
- **现状**：后端已在季根目录写全三种证据文件（`v6_agent.py:423-434`），实测 `GET /media/agent_runs/season_undercity_arc_20261106_232820/summary.md` = **200**，故原「必然 404」不成立。**但**媒体路由的入参校验是 `^[a-z0-9_]{8,40}$`，而季 run_dir 命名是 `season_<sid>_<YYYYmmdd_HHMMSS>`（`v6_agent.py:373`）= `7 + len(sid) + 1 + 15` 字符 → **`len(sid) > 17` 即必然 404**，与文件是否存在无关。
- **证据**：

```python
# backend/app/routers/v6_agent.py:546-553
@router.get("/media/agent_runs/{run_id}/{file}")
def agent_run_media(run_id: str, file: str) -> FileResponse:
    if not re.match(r"^[a-z0-9_]{8,40}$", run_id) or file not in RUN_MEDIA:
        raise HTTPException(status_code=404, detail="not found")   # ← 长 run_id 直接拒
```
```
实测：season_undercity_arc (sid 13) → run_id 36 → summary.md 200 ✅
      season_custom_tpl_v46_duologue_20261106_232820 (46 字符) → 404 ❌
阈值：7 + len(sid) + 1 + 15 ≤ 40  ⇒  len(sid) ≤ 17
当前合同 contract/v5/series/ 只有 undercity_arc（13），故今天不触发。
```
- **影响**：新增任何一个 id 长度 ≥18 的系列（例如 `custom_tpl_v46_duologue`、`v46_undercity_arc_2`），季生产跑完后「证据」三链接（summary.md / run.json / events.jsonl）**全部 404**，且面板看不出是「接口正则拒了」还是「文件没生成」。另：`v6_agent.py:45` 的 `ID_RE = ^[a-z][a-z0-9_]{1,60}$` 与媒体路由的 `{8,40}` **两套边界不一致**，属于后端自身缺陷（前端无法自愈）。
- **建议修法**（前端侧可先做防御）：`const evidence = useMemo(() => (jobScope === "season" ? [] : agentEvidence(job)), [job, jobScope])`，季模式改渲染后端已返回的 `result.summary_url / run_url / events_url`（`v6_agent.py:443-445` 已经给了绝对 `/media/...` 路径，前端 `normalizeRun` 目前把 `result` 原样保留，可直接用）。后端侧建议把正则放宽到 `{8,80}` 并与 `ID_RE` 统一。

---

### P3-1 · 约 45 处「接口尚未就绪（后端 Vxx 实现中）」文案全部过期（承 `PART_FRONTEND` §8，仍未清理）

- **位置**（抽样，均为**后端已实现**的接口）：`CharactersPage.tsx:517/564`（V32 试镜 → `character_audition.py` 已实现）、`:709/756`（V35 宣传片 → `character_promo.py`）、`:953/1013`（V36 双人 → `character_dialogue.py`）、`:810/1068/1086`（V38/V36 切条包 → `character_showcase.py`）、`:1272`（V18 试听 → `v5_catalog.py:528`）、`LibraryPage.tsx:250-251`（V25 批量 → `pipeline_ui.py:1053`）、`:880-881`（V29 批量导出 → `publish_batches.py:217`）、`PipelineConsole.tsx:206/395`（V18 pipeline → `pipeline_ui.py:698`）、`steps.tsx:378/419`、`Templates3D.tsx:340`（`PATCH /api/projects/{id}` 已存在 `main.py:563`）、`PublishPackageModal.tsx:47`（V26 → `cinema_templates.py:2796`）、`CreateCharacterDrawer.tsx:172` + `CreateTemplateWizard.tsx:223`（V21 → `v5_create.py:813/1187`）、`SettingsModal.tsx:383`（`/api/llm/test` 存在 `main.py:1763`）。
- **影响**：功能可用但 UI 归因错误。真正撞 404（id 写错、后端没起、代理断）时用户看到「后端 V32 实现中」，会误判为「该功能没做」，从而放弃重试/报错路径。
- **建议修法**：统一改为不承诺版本与实现状态的措辞（「接口不可用（404），请确认后端已启动后重试」），并把「Vxx 实现中」这类历史信息移进注释或删除。

### P3-2 · `Settings` 类型缺 4 个后端字段 → 设置页无入口（能力空转）

- **位置**：`frontend/src/types.ts:286-325`（`Settings`）
- **证据**：后端 `backend/app/schemas.py:196-229` 的 `SettingsOut` 还包含 `localize_langs`、`localize_translator`、`localize_voices`（`:212-214`）与 `publish_brand_title`（`:223`），`SettingsUpdate` 也接受（`:248-250,258`）。前端类型里**一个都没有**，`SettingsModal.save()`（`:283-311`）也不会发送 → 多语种翻译器/语言列表/发布品牌标题在 UI 上不可编辑，只能改 `backend/data/*.env` 或直接 PATCH。
- **影响**：`PublishRecord`/成片标题会一直用后端默认 `造片局 · AI微综艺`；多语种语言集只能是后端默认 `["en","es"]`。
- **建议修法**：补类型 + 在「多语种」「发布」分栏各加一个控件；`save()` 里按需带上（这几个字段后端全有默认值，漏传不会 422）。

### P3-3 · `lib/v5.ts` 删成片：注释与代码相反，501 分支成死代码

- **位置**：`frontend/src/lib/v5.ts:416-426`（注释）、`:425`（`FINAL_DELETE_ENABLED = true`）、`:433-439`（`deleteFinalOutput` 的 `if (!FINAL_DELETE_ENABLED) throw new V5ApiError(501, …)`）
- **证据**：注释仍写「后端目前**没有**删除成片的接口（现有 DELETE 仅 …），所以默认关闭」，但下一行就是 `= true`，且后端 `DELETE /api/v5/outputs/{template}` 确实存在（实测 400 = 模板名非法）。
- **影响**：后续维护者按注释理解会误判；`:434-436` 的 501 分支永不执行（`V5ApiError(501)` 的 UI 文案「后端尚未提供…」也就永远出不来）。属纯维护性缺陷。
- **建议修法**：删掉过期注释段落，保留「后端路径已上线」一句；`FINAL_DELETE_ENABLED` 若确定长期为 true 可去掉开关与 501 分支。

### P3-4 · `App.tsx` 把所有 boot 异常归为「后端离线」

- **位置**：`frontend/src/App.tsx:203-204`（`catch { setOffline(true); }`）、`:217-223`（`offline` 时每 4 s 全量重跑 `boot(true)`）
- **证据**：`boot()` 里 `api.health()` / `api.projects()` / `api.episodes()` / `api.project()` 的**任何**异常（500、JSON 解析失败、`api.episode` 的 500）都会落到同一个 `catch` → 整屏「等待后端」+ 4 s 重试风暴；只有 episode 的 404 被单独拆出去了（`:187-201`）。而 `request()` 对 500 抛的是 `ApiError(500, detail)`（`api.ts:67-77`），信息被丢弃。
- **影响**：后端 500（例如 DB 文件被锁、`projects` 查询报错）表现为「后端未连接」，用户按提示重启后端无效；且 4 s 重试会持续放大后端错误日志。
- **建议修法**：`catch (err)` 里区分 `err instanceof ApiError && err.status > 0` → 顶部错误条显示真实 `status + detail`，只有 `status === 0`（连接失败/超时）才置 `offline` 并启用重连定时器。

### P3-5 · 硬编码地址/端口散落在 UI 文案

- **位置**：`App.tsx:511-512`（「默认 http://127.0.0.1:8090」+ `backend\run.ps1 -Port 8090` 命令）、`CreateTemplateWizard.tsx:292`（「请确认后端 8090 已启动」）、`cinema/Jobs.tsx:354`（「ComfyUI（127.0.0.1:8188）」）、`SettingsModal.tsx:65/78/111/528/839/843`（`11434` Ollama、`8188` ComfyUI 占位与提示）
- **影响**：`vite.config.ts` 端口与后端默认一致（8090）时无碍；一旦换端口（`run.ps1 -Port 9000` 或反代），这些文案会指错方向。`SettingsModal` 的 11434/8188 属合理默认值，可保留。
- **建议修法**：`App.tsx` 的离线提示改为读 `import.meta.env.VITE_API_BASE || "/api"` 展示实际代理目标（或干脆不写端口，只写「见 README/run.ps1」）。

### P3-6 · `mediaUrl` 对绝对 URL 直通 + 三处 `href` 后端字段原样落地

- **位置**：`lib/api.ts:35-40`（`if (/^(https?:|blob:|data:)/i.test(url)) return url;`）、`components/pipeline/LongformPanel.tsx:225`（`href={job.final_url}`，**不经 `mediaUrl`**）、`lib/longform.ts:113`（`final_url: str(item.final_url || item.url) || mediaUrlFromPath(...)`，优先用后端原值）、`components/shell/AppShell.tsx:325`（`href={primaryAction.href}`）
- **问题**：`mediaUrl` 只做「加 origin」，不校验协议；`longform` 的 `final_url` 直接取后端 `final_url`/`url` 字符串。若后端（或将来引入的用户可写字段，如自定义模板的产物路径）返回 `javascript:` 开头的值，`<a href>` 点击即执行脚本；`<img src="javascript:…">` 不执行但会污染网络面板。
- **影响**：当前后端只回 `/media/...` 与磁盘路径，风险低；属**纵深防御缺失**，一旦产物路径来自用户输入（自定义模板 id / 上传文件名）即成存储型 XSS 面。
- **建议修法**：`mediaUrl` 增加协议白名单（`https?:|blob:|data:image/|^/(?!/)`），其余返回 `""`；`LongformPanel:225` 改用 `mediaUrl(job.final_url)`；`AppShell` 的 `primaryAction.href` 限制为 `#/` 前缀。

---

### P3-7 · 后端 `format_id` **零校验**（`no_such_format` 也返回 200）；前端**不要**补白名单兜底（Lead 实测，后端条目）

- **位置**：`frontend/src/lib/api.ts:96-97`（`createProject: (body) => request<Project>("/projects", { method:"POST", body: JSON.stringify(body) })`）、调用方 `components/platform/PlatformHome.tsx:394-407,450-452`（下拉值域 = `TEMPLATES` 16 项）
- **实测（Lead）**：`POST /api/projects {"format_id":"relic_talk_5min"}` → **200**（可建）；`POST /api/projects {"format_id":"no_such_format"}` → **200**（**同样建出项目**）。即后端对 `format_id` **完全不校验**，任意字符串都能落库。
- **问题**：当前 UI 的 16 项白名单（`TEMPLATES`）**恰好**把用户限制在合法值域内 —— 这是"前端白名单掩盖后端校验洞"：洞真实存在，只是从 UI 打不进去。修 P2-3 把 `relic_talk_5min` 加进前端后，UI 仍然安全（多一项合法值），**但洞不会被暴露，也就不会被修**。
- **影响**：① 任何直接打 API 的客户端（脚本、CLI、`POST /projects` 手搓、未来的移动端）都能建出 `format_id` 不存在的项目，后续 `pipeline`/`assemble` 阶段会以「模板不存在」崩溃或静默产出空片；② 前端一旦为了"更稳"再加一层「只允许这 17 个」的校验，会把洞藏得更深（后端永远收不到非法值 → 永远没人修），且每加一份合同就要同步改前端，正是 P2-3 那次漂移的成因。
- **建议修法**（**分工明确**）：
  - **后端**（主修）：`POST /api/projects` 校验 `format_id ∈ contract/formats/*.json`，非法返回 **400/422 + 可用值列表**（并在 `GET /api/projects/formats` 或 `/api/v5/catalog` 暴露值域）。Lead 已记入总报告后端条目。
  - **前端**（本轮只做这两件）：① 补 `relic_talk_5min`（P2-3）；② **不要**新增值域白名单/正则兜底 —— 下拉本来就只给合法值，真正的防线应在后端。前端该做的只是把后端的 4xx detail 展示出来，这一条**已经具备**：`PlatformHome.tsx:420-422` 的 `catch` 就是 `notify("创建失败：" + err.message, "error")`，而 `ApiError.message` 已带后端 `detail`（`api.ts:67-77`），所以后端一旦补上校验，UI 会**自动**把「format_id 非法：可用值 …」显示出来，前端无需改动。

---

## 已核对无问题的点

| 项 | 结论与证据 |
|---|---|
| **`any` 滥用** | 全仓库 `grep -w any` = **0 处**；`as unknown as` 仅 3 处（`api.ts`×2、`Final.tsx`×1）且都在 JSON 兜底解析里；非空断言 `x!.y` = **0 处**。`tsc --noEmit` 在 `strict: true` 下零错误 |
| **`dangerouslySetInnerHTML` / `innerHTML` / `eval` / `new Function` / `document.write`** | 全部 **0 命中** |
| **`res.ok` 覆盖** | **12 个含 fetch 的文件、14 处 `fetch` 调用**逐个核对：14 处全部检查状态码（`Templates3D.tsx:294` 写成 `res.ok ? res.json() : Promise.reject(...)`；`PublishPackageModal.tsx:164`、`download.ts:9`、`studio.ts:263`、`api.ts:67` 均检查）。**无「不检查状态码」的 fetch** |
| **`JSON.parse` 无 try** | 全仓 **6 处**（`lib/agent.ts:186`、`AgentPage.tsx:128/139/151/570/772`）**全部**包在 `try` 内；`createTemplateWizard`/`voice`/`cinema` 等无裸 `JSON.parse` |
| **定时器清理** | 全仓 **27 处 `setInterval`**（`clearInterval` 29 处）逐处核对：**没有任何文件出现「有 setInterval 无 clearInterval」**，多数还带 `alive`/`aliveRef` 守卫，无「切页后仍在跑」的定时器。**22 处 `setTimeout`**：`Toast.tsx:26`（自清理）、`AgentPage.tsx:858`（`museChipTimerRef` + `:875` 卸载清理）、`cinema/Shots.tsx:134`（`timerRef` + `:91` 清理）、`Templates.tsx:235`、`cinema/Jobs.tsx:184`、`CharactersPage.tsx:1338`、`PublishPackageModal.tsx:174/191`、`download.ts:26` 均正常。注：`PublishPackageModal.tsx:174` 的 `URL.revokeObjectURL` 4 s 后才执行，不随卸载提前释放（可忽略） |
| **`useEffect` 依赖** | 全量扫描 **156 处**，**0 处**缺依赖数组；未发现「依赖对象字面量/内联函数导致死循环」的写法（`LoadAll`/`load`/`refresh` 全部 `useCallback` 且依赖稳定） |
| **解构嵌套字段崩溃** | `lib/agent.ts`、`lib/v5.ts`、`lib/cinema.ts`、`lib/creation.ts`、`lib/longform.ts`、`lib/pipeline.ts` 的 `normalize*` 全部走 `rec/arr/str/num/bool` 六件套（`agent.ts:170-181`），null/类型错都退化成默认值；`CharactersPage.tsx:737-742`（`promoScenes[index]`）与 `:1003`（`duoLInes[index]`）经核对是**同源同长数组**的 index（`promoScenes.map` / `duoLInes.map` 派生），不会越界；`AgentPage.tsx:460` 的 `templates[0].id` 有 `:458 if (!templates.length) return` 守卫；`:642` 的 `seriesList[0].id` 有 `:641` 守卫；`Final.tsx:440` 的 `frameCards[0].src` 有 `length > 0` 守卫 |
| **数组/对象混用** | 所有列表渲染前都有 `?? []` / `arr()` 归一（`const list = shots ?? []` 等）；`normalizeOverview`/`normalizeSeries` 等对 `series: string[] \| object[]` 两种形态都兼容 |
| **string/number 混用比较** | `String(item.status \|\| "").toLowerCase()` 统一归一后才比较（`Studio.tsx:88`、`AgentPage.tsx` 的 `agentStatusLabel`） |
| **竞态（关键路径）** | `AgentPage.loadWorkflow`（`:409` `wfSeqRef`）、`refreshSeason`（`:617` `seasonSeqRef`）、`LibraryPage.load`（`:96` `inFlightRef`）、`studio/*` 的 `inFlightRef`（`Render.tsx:113`、`Storyboard.tsx:116`、`Finishing.tsx:233`、`cinema/Templates.tsx:145`、`cinema/Jobs.tsx:119`、`PipelineConsole.tsx:124`、`V5World.tsx:94`）**全部有守卫**。唯一缺守卫的是 `App.tsx` 的页面导航（新发现 P1-1） |
| **轮询终止条件** | 每条轮询都有明确的「非活动即 return / 终态即停」入口判断（`if (!activeCount) return`、`if (!isAgentActive(job)) return`、`if (!batch?.id \|\| !isBatchActive(batch)) return` …），未发现「永不终止」的轮询 |
| **页面卸载中止 fetch** | `lib/*` 的 `request()` 都建 `AbortController` + `setTimeout(abort, timeoutMs)`，并在 `clearTimeout` 前 `try/catch` 归一成 `ApiError(0)`；**未**接组件卸载信号（React 18 下不会告警、不会崩），属可接受取舍 |
| **`encodeURIComponent`** | 所有动态段都编码：`api.ts` 用 `"/projects/" + pid`（pid 为后端生成的 uuid，无特殊字符风险），`v5.ts`/`cinema.ts`/`pipeline.ts`/`voice.ts`/`creation.ts` 全部 `enc(...)`/`encodeURIComponent(...)`；query 用 `URLSearchParams`（`api.ts:82-89`、`v5.ts:389`）。**唯一未编码**：`v5.ts:532-554` 的媒体文件名（`"/media/v5/characters/" + enc(characterId) + "/" + file`，`file` 未编码）—— 若产物文件名含空格/中文，`<img>` 会 404（低概率，`file` 来自固定的 `portrait.png/front.png/...`） |
| **空 catch / 吞错** | 空 `catch`（仅注释）8 处逐处确认：`App.tsx:94/166/276`（sessionStorage/路由降级）、`LibraryPage.tsx:154/193/825`（可选接口探测）、`SettingsModal.tsx:223/234`（LLM 配置降级），**均非业务主链路**且有注释；`api.ts:73`、`Final.tsx` 的非 JSON 错误体兜底同理 |
| **构建产物** | `frontend/dist` 存在且比所有 `src` 新（23:27:11 vs 23:27:02）；`vite.config.ts` 的 `outDir: dist`、`assetsDir: assets` 与后端 `SPAStaticFiles(directory=FRONTEND_DIST)`（`main.py:2691-2695`）一致 |
| **孤儿组件** | `PART_FRONTEND` §5 点名的 8 个孤儿组件（`v5world/*`、`studio/Templates3D.tsx` 等）仍未被路由引用，属历史死代码；`Templates3D.tsx:110` 的 `patchProjectFormat` 只在孤儿页可达 → 「给项目换模板」在主链路仍不可达（`POST /api/projects/{pid}/apply-template` 零引用） |

---

## 附：方法与可复现命令

```bash
# 1) 类型检查（本轮必跑项）
cd frontend && npx tsc --noEmit          # -> exit 0, 零输出, 5.9s

# 2) 后端路由全量枚举（含跨行装饰器）→ docs/audit/_tools/backend_routes.json (155 条)
python docs/audit/_tools/routes2.py

# 3) 前端 138 个调用点抽取 + 逐条比对 → docs/audit/_tools/contract_table.txt
python docs/audit/_tools/final_contract.py

# 4) 去重成 115 条路径表 → docs/audit/_tools/dedup_table.txt
python docs/audit/_tools/dedup_table.py

# 5) templates.ts 与 contract/formats 逐字段比对（发现 relic_talk_5min 缺失）
python docs/audit/_tools/tpl_diff.py

# 6) useEffect 依赖 / 定时器扫描（156 处，0 处缺依赖）
python docs/audit/_tools/scan_effects.py

# 7) 实测（后端已在 8090 运行）
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8090/api/health                      # 200
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8090/api/v6/agent/templates         # 404（前端不调用）
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8090/api/templates                  # 404（前端不调用）
curl -s -X POST -H "Content-Type: application/json" -d '{}' \
     http://127.0.0.1:8090/api/settings/metaso-test                                            # 200（未被 SPA 遮蔽）
curl -s -X POST -H "Content-Type: application/json" -d '{"all":true}' \
     http://127.0.0.1:8090/api/episodes/__nope__/shots/generate-dynamic                        # 404 {"detail":"集不存在"}
curl -s http://127.0.0.1:8090/api/cinema/templates | jq '.templates|length'                     # 30
curl -s http://127.0.0.1:8090/api/v6/agent/overview | jq '.templates|length'                    # 33
curl -s "http://127.0.0.1:8090/api/v5/outputs?include_custom=true" | jq '.outputs|length'        # 33
curl -s -o /dev/null -w "%{http_code}\n" \
  http://127.0.0.1:8090/media/agent_runs/season_undercity_arc_20261106_232820/summary.md         # 200
```

**未覆盖**：未跑浏览器级交互（不引入 puppeteer 依赖）；未跑 `vite build`（避免改写 `dist`）；P1-1 / P2-1 / P2-2 为代码级推断（未构造真实慢后端做时序复现），但触发条件与现有守卫缺失位置均已定位到行。

---

## 整改记录（本轮已改，Lead 验收用）

写权限：`frontend/src/**`。未改 `frontend/dist`（`vite build` 会重写，但 `frontend/dist/` 在 `.gitignore` 内，不产生 git diff）、未改 `package.json` / `backend/**` / `pipeline/**` / `tests/**`、未加依赖、未新建提交。

| # | 原级别 | 改法 | 位置（改后行号） | 状态 |
|---|---|---|---|---|
| 1 | P1 | 导航请求序号守卫：`await` 之后 `if (seq !== navSeqRef.current) return`，hash 写回移到守卫之后；三个导航函数（`openEpisodeById` / `openProject` / `switchEpisode`）共用同一 `navSeqRef` | `App.tsx:145`（ref）、`:265/271/277`、`:341/344/356`、`:367/371/378` | ✅ 已修 |
| 2 | P2 | `load()` 改 `Promise<boolean>`，并发标志与 `loading/refreshing` 统一搬进 `finally`；`refresh()` 用返回值决定 toast，复位放自己的 `finally`，删掉 `refreshing` 闭包依赖 | `LibraryPage.tsx:105-125`（load）、`:213-221`（refresh） | ✅ 已修 |
| 3 | P2 | 三条轮询统一：`busy` in-flight 互斥 + `document.hidden` 跳过 + `AbortController`，cleanup 里 `abort()`；abort 后的 catch 直接 return（不报错）。`lib/agent.ts` 的 `request()` 支持外部 `signal`（与 20s 超时共用 controller），`runStatus/museJob/museVideoJob` 增加可选 `signal` 参数 | `AgentPage.tsx:721-753`、`:912-943`、`:1041-1071`；`lib/agent.ts:196-201`、`:405`、`:468`、`:491` | ✅ 已修 |
| 4 | P2 | 补齐第 17 条格式：`relic_talk_5min`（6 段 / 306s / 16:9，与 `contract/formats/relic_talk_5min.json` 逐字段一致，脚本复核 0 DRIFT）；文案读 `TEMPLATES.length` 自动变 17，未加值域白名单 | `lib/templates.ts:1462-1556`（新条目，`"id": "relic_talk_5min"` 在 `:1463`） | ✅ 已修 |
| 5 | P3 | 删掉「后端没有接口」的过期注释与 501 死代码，改为说明后端 `DELETE /api/v5/outputs/{template}` 已上线；`FINAL_DELETE_ENABLED` 保留为 UI 总开关；连带清理 LibraryPage 三处「后端尚未提供删除接口」文案 | `lib/v5.ts:416-433`；`LibraryPage.tsx:785-787`、`:1518`、`:1536-1543` | ✅ 已修 |
| 6 | P3 | 过期文案：**54 条规则 / 61 处**中性化（`实现中` / `未挂载` / `尚未就绪` / `尚未上线` / `Vxx 实现中` 全部清零，`grep` 复核 = **0**）。逐条核对过后端路由**全部已实现**，故无「保留 404」项；剩余 26 处命中为**真实运行状态**（Muse provider 不可用 / 发布配置未就绪 / report.json 未生成）或**描述性注释**，按语义保留 | 17 个文件：`AgentPage`、`CharactersPage`、`LibraryPage`、`PipelineConsole`、`steps`、`LongformPanel`(文案)、`Templates3D`、`cinema/Templates`、`cinema/Shots`、`PublishPackageModal`、`Assets`、`VoicePicker`、`CreateCharacterDrawer`、`CreateTemplateWizard`、`SettingsModal`、`lib/creation.ts`、`lib/pipeline.ts`/`lib/longform.ts`(注释) | ✅ 已修 |
| 7 | P3 | boot 归因：仅 `ApiError.status === 0`（网络失败/超时）置 `offline` 并自动重连；其他 HTTP 错误展示真实 `err.message`（新增整屏「后端返回错误」态，含重试按钮），重连改为**最多 3 次**（每 4s，self-clear），手动重试把计数归零 | `App.tsx:132`（state）、`:164`、`:211-220`（catch）、`:233-246`（退避/上限）、`:497`（手动重试归零）、`:533-551`（错误屏）、`:568`（离线文案改「自动重连 3 次」） | ✅ 已修 |
| 8 | P3 | `Settings` 补 4 个可选字段，只加类型不改 UI | `types.ts:325-330` | ✅ 已修 |
| — | P2 | 季 run 证据链接 `run_id > 40` 字符 404 | **未改**：根因在后端 `v6_agent.py:548` 的 `^[a-z0-9_]{8,40}$`（与 `:45` 的 `{1,60}` 不一致），属后端写权限外；前端侧规避方案（季模式改用后端已返回的 `result.summary_url/run_url/events_url`）已写入 P2-4，本轮未擅自改语义 | ⏸ 待后端 |
| — | P3 | 硬编码端口文案（`App.tsx` 8090 / `CreateTemplateWizard` 8090 / `cinema/Jobs` 8188） | **未改**：不在本轮必改清单，且与 `vite.config.ts` 代理一致时无害；避免扩大 diff | ⏸ 未改 |
| — | P3 | `mediaUrl` 协议白名单 + 裸 `href` | **未改**：`mediaUrl` 语义被 6 个 lib 共用，单改需一起回归；不在必改清单 | ⏸ 未改 |
| — | P3 | 后端 `format_id` 零校验（P3-7） | **未改前端**：按 Lead 指示**不加**值域白名单，仅记录；后端补校验后 UI 会自动显示 detail（`PlatformHome.tsx:420-422`） | ⏸ 待后端 |

**验收（本轮末尾实跑）**

```
> cd frontend; npx tsc --noEmit
TSC_EXIT=0            # 零输出

> cd frontend; npx vite build
vite v5.4.11 building for production...
✓ 95 modules transformed.
dist/index.html                   0.61 kB │ gzip:   0.44 kB
dist/assets/index-CCPSEmP4.css  353.18 kB │ gzip:  55.94 kB
dist/assets/index-DWCp0DBn.js   718.73 kB │ gzip: 213.41 kB
✓ built in 1.20s
BUILD_EXIT=0

> dist 抽查
bundle 含 "relic_talk_5min"；bundle 内 "实现中" 出现次数 = 0
```

**回归自查**：`grep -c "实现中|未挂载|尚未就绪|尚未上线" frontend/src = 0`；`python docs/audit/_tools/tpl_diff.py` 显示 17/17 条格式与合同一致、0 DRIFT；文件行尾（CRLF/LF）逐个核对，混排 0（`App.tsx`/`templates.ts`/`v5.ts` 保持 CRLF，其余保持 LF）。

> 说明：工作区存在**本轮之前**的未提交 WIP（`AgentPage.tsx`、`lib/agent.ts`、`LongformPanel.tsx`、`lib/longform.ts`、`styles/agent.css|v54.css|v62.css` 为 `??` 未跟踪；`icons.tsx`、`AppShell.tsx`、`lib/cinema.ts`、`settings.css` 等有非本轮的改动）。因此 `git diff --stat` 的数字不等于本轮改动量，本轮的改动以上表行号为准。

---

## R5 UI 重设计（简洁明朗 · 浅色纸面）

目标：**功能一个不少，只解决"看起来乱"**。33 模板 / 15 条路由 / 全部后端能力保留；不引入任何依赖；`App.tsx` 的 hash 路由与 boot 逻辑未改动（只在外壳/页面层换皮）。

### R5-1 改了哪些文件

| 文件 | 类型 | 说明 |
|---|---|---|
| `frontend/src/styles/design.css` | **新增**（4226 行：手写 1192 + 生成 3035） | 唯一新样式入口，`main.tsx` **最后**引入；token、旧变量映射、旧硬编码色压缩、新外壳/首页/UI 组件样式 |
| `frontend/src/components/ui/PageHeader.tsx` | **新增** | 页头（标题/副标题/meta/actions） |
| `frontend/src/components/ui/Card.tsx` | **新增** | 卡片（title/sub/actions/flush/quiet） |
| `frontend/src/components/ui/Stat.tsx` | **新增** | 指标 + `StatRow` |
| `frontend/src/main.tsx` | 改 2 行 | 末尾 `import "./styles/design.css"`（保证覆盖历史 CSS） |
| `frontend/src/components/shell/AppShell.tsx` | **重写**（371 → 520 行） | 68px 图标轨 ⇄ 208px 文本栏（可折叠、localStorage 记忆）；5 组导航；顶栏收敛为「位置 + 页面指标 + 刷新 + 一个主按钮 + 设置」；props 全兼容（8 个使用方未改调用） |
| `frontend/src/components/platform/PlatformHome.tsx` | **首页重写**（正文 200 行 → 220 行） | 3 个「今天做什么」大卡 + 33 模板宫格（搜索/筛选/状态）+ 最近成片 4 条（有片才显示）；`NewProjectPanel` / `SettingsModal` / 数据加载原样保留 |
| `frontend/src/components/agent/AgentPage.tsx` | 加 30 行 + 文案 | 顶部 `PageHeader` + 4 个 `Stat`；Muse 卡的接口路径文案移入提示 |
| `frontend/src/components/library/LibraryPage.tsx` | 加 26 行 | 顶部 `PageHeader` + 3 个 `Stat`（已出片/已选/批量状态） |
| `frontend/src/components/pipeline/LongformPanel.tsx` | 文案 2 处 | 去掉正文里的 `LONGFORM · 300s · /api/v5/...` |

> 注意：`components/ui.tsx`（单文件）与新增目录 `components/ui/` 并存不会冲突——`from "../ui"` 仍解析到 `ui.tsx`，新页面用显式子路径 `../ui/PageHeader`。

### R5-2 token 表（`design.css` 第 1 节）

| 类别 | token | 浅色（默认） | 深色（`prefers-color-scheme: dark`） |
|---|---|---|---|
| 面 | `--bg` / `--surface` / `--surface-2` / `--surface-3` | `#f6f7f9` / `#ffffff` / `#f1f3f6` / `#e8ecf1` | `#0d0f13` / `#14171d` / `#1a1e26` / `#222733` |
| 字 | `--text` / `--text-dim` / `--text-mute` | `#14181f` / `#5a6472` / `#8a93a1` | `#e9edf5` / `#a8b2c4` / `#78839a` |
| 线 | `--line` / `--line-strong` | `#e3e7ed` / `#ccd4de` | `#232a36` / `#313a4a` |
| 主色 | `--accent` / `--accent-2` / `--accent-ink` / `--accent-soft` | `#b45309` / `#d97706` / `#fff` / 10% | `#e8b33a` / `#f5c518` / `#1a1408` / 14% |
| 语义 | `--ok` / `--warn` / `--err` / `--info` | `#15803d` / `#b45309` / `#b91c1c` / `#1d4ed8` | `#38c98b` / `#f0b429` / `#ff6060` / `#5b9dff` |
| 间距 | `--s-1..--s-6` | 4 / 8 / 12 / 16 / 24 / 32 | 同 |
| 圆角 | `--r-8` / `--r-12` / `--r-16` | 8 / 12 / 16 | 同 |
| 字号 | `--f-12..--f-28` | 12 / 13 / 14 / 16 / 20 / 28 | 同 |
| 阴影 | `--shadow-1` | `0 1px 2px rgba(16,24,40,.04), 0 2px 8px rgba(16,24,40,.06)` | `0 1px 2px rgba(0,0,0,.4), 0 8px 24px rgba(0,0,0,.28)` |

浅色下品牌金改为**琥珀**（`#b45309`），因为 `#f5c518` 在白底上对比度不足；深色下仍是金。

### R5-3 旧变量 → token 映射表（唯一一份，两种主题共用）

声明在 `design.css` 第 2 节，`:root { --旧: var(--新) }`，因此旧组件只要用 `var(--panel)` 就自动跟随主题：

| 旧变量（来源） | 映射 |
|---|---|
| `--bg-0/-1/-2`、`--panel/-2/-3`（base.css） | `--bg`、`--surface/--surface-2/--surface-3` |
| `--line`、`--line-soft`、`--line-strong` | `--line`、`--line`、`--line-strong` |
| `--text`、`--text-dim`、`--mute`、`--faint` | `--text`、`--text-dim`、`--text-mute`、`--text-mute` |
| `--accent`、`--accent-2`、`--accent-ink` | 同名 token |
| `--green/--red/--blue/--cyan/--purple/--orange` | `--ok/--err/--info/--info/--accent-2/--warn` |
| `--radius`、`--radius-sm`、`--radius-lg`、`--shadow-panel`、`--shadow-pop` | `--r-12`、`--r-8`、`--r-16`、`--shadow-1`、`--shadow-1` |
| `--c-bg/-bg-2/-panel/-panel-2/-panel-3/-line/-line-strong/-text/-sub/-mut`（cinema.css、settings.css） | `--bg`、`--bg`、`--surface`、`--surface-2`、`--surface-3`、`--line`、`--line-strong`、`--text`、`--text-dim`、`--text-mute` |
| `--c-gold/-gold-soft/-gold-dim/-jade/-jade-dim/-red/-red-dim/-slate` | `--accent`、`--accent-2`、`--accent-soft`、`--ok`、`--accent-soft`、`--err`、`--accent-soft`、`--text-mute` |
| `--c-radius/-radius-sm/-shadow` | `--r-12`、`--r-8`、`--shadow-1` |

**旧硬编码色值**不是手改 1.4 万行，而是脚本压缩：`docs/audit/_tools/gen_design_legacy.py` 解析 22 个历史 CSS 的**顶层规则**，把 `color/background*/border*/box-shadow` 里的字面量按亮度/色相分类压到 token：

- 近白/浅灰字 → `--text` / `--text-dim` / `--text-mute`
- 金色字/面 → `--accent`（低透明度用 `color-mix(...)`）
- 深色面（`#0b0e13` 等，含 `rgba(19,22,29,.9)`）→ `--bg` / `--surface`
- 白色叠加（`rgba(255,255,255,.02)`）→ `--surface-2`
- **渐变面**（`linear-gradient(...)`）→ 取最实的一档压成平色（这就是 `.ag-rail`、`.shell-rail`、`.pl-hero` 从深色渐变变成浅色卡面的原因）；全低透明度的遮罩渐变保留
- **渐变文字**（`background-clip:text`）→ 清掉渐变，改 `--accent` 实色
- `text-shadow` → `none`，`box-shadow` → `--shadow-1`

产出：**883 条规则 / 1099 条声明**（`design.css` 中 `===== AUTO =====` 与 `===== END AUTO =====` 之间，可重复运行、尾部手工修正块会被保留）。

### R5-4 外壳与首页

- 导航 5 组（不删任何入口）：**生产**（微综艺 `#/projects`、短片 `#/agent`、长版 `#/pipeline`）· **资产**（角色、场景、风格、模板）· **成片**（成片库、发布包）· **调试**（管线、Cinema、智能体）· **设置**。
- 68px 图标轨 / 208px 文本栏：`≧1366` 默认展开，<1366 默认收起，手动切换写入 `localStorage["zpj_shell_open_v1"]`；<900px 变浮层抽屉（Esc / 遮罩关闭）。
- 顶栏：位置（面包屑）+ 页面标题 + 页面指标 chip +（刷新、主按钮、设置）；页头不再有 hero 横幅/渐变噪点（`.pl-hero-glow`、`body` 的两处金色 radial-gradient 已 `background-image: none`）。
- 首页：`PageHeader` + 3 大卡（微综艺一集 / 5 分钟长版验证 / 从 33 模板新建）+ 33 模板宫格（搜索 label+id、筛 全部/综艺/短片、状态「有样片/制作中」、栈与时长）+ 最近成片 4 条（无片则整块不渲染）。数据用 `/api/v6/agent/overview`（33）+ `/api/cinema/templates`（状态）。

### R5-5 验收（自证）

```
> cd frontend; npx tsc --noEmit
EXIT=0（零输出）

> cd frontend; npx vite build
vite v5.4.11 building for production...
✓ 95 modules transformed.
dist/index.html                   0.61 kB │ gzip:   0.44 kB
dist/assets/index-BwNdCl_B.css  426.41 kB │ gzip:  66.83 kB     （重设计前 353.18 kB）
dist/assets/index-CW_FKM1b.js   719.27 kB │ gzip: 214.57 kB
✓ built in 964ms
EXIT=0
```

截图（`tmp/ui_r5/`，本机 Chrome + `puppeteer-core`，脚本 `tmp/ui/shot-r5.cjs`，`emulateMediaFeatures` 分别模拟浅/深色）：

```
home-1600-light.png  home-1600-dark.png  home-1280-light.png  home-1280-dark.png
agent-1600-*.png     library-1600-*.png  pipeline-1600-*.png  … 1280 同
共 16 张（4 页 × 2 尺寸 × 2 主题），另有早期 home-1600.png 存档
```

横向溢出检测（同一脚本内 `documentElement.scrollWidth > clientWidth`，并列出 `right > viewport` 的元素）：

```
16/16 → overflow=false，wide=[]（无越界元素）
浅色 body 背景 rgb(246,247,249) = --bg；深色 rgb(13,15,19) = --bg
```

复现：`cd tmp/ui && node shot-r5.cjs`（需 8090 已起；脚本只读，不改仓库）。

### R5-6 仍未被 token 覆盖的清单（下一轮清）

1. **`@media` / `@supports` 内部的颜色规则**：脚本只处理顶层规则，响应式断点里改色的少数规则仍是字面量。
2. **`@keyframes` 里的颜色**（骨架屏 shimmer、脉冲点等）未改写。
3. **内联 `style={{ color/background }}`**：`AgentPage` 的 tone 色、`cinema/Shots` hover 色、`Shots.tsx` 的 `background: promoScenes[...]` 等，脚本扫不到。
4. **深色墨字**（luma < 0.42 的 `color:`）保留原值：多为金底按钮上的墨色（`#171203`），主题切换时对比度仍成立。
5. **遮罩/渐变叠层**（`.pl-cover-shade`、`.cm-hero::after`、`.home-final-cap` 等）刻意保留渐变。
6. **局部 `--tone`**（`agent.css:200/415` 的 `--tone: #6f7d92`）仍是字面量。
7. **旧页面字体**：`cinema.css`/`platform.css` 的 `--font-display`（楷体）仍用于少数旧标题；新组件（`ui-*`）统一无衬线。
8. **`AgentPage` 三栏在 <1366 时右栏落到折叠线以下**：`agent.css` 的栅格断点未改（内容不缺失，只是要滚）。
9. **成片卡同级按钮仍有 7 个**：已压到 28px/12px + 6px gap（噪声降低），但没做「更多」收纳菜单。
10. **CSS 体积**：design.css 426 kB（gzip 66.8 kB）；生成块可再按选择器频次裁剪。

### R5-7 与上一轮修复的关系

上一轮 8 条整改**全部保留**：`navSeqRef` 守卫、`boot` 只把 `status===0` 判离线、`LibraryPage.load` finally 复位、`AgentPage` 三条轮询的 in-flight/`document.hidden`/`AbortController`、`relic_talk_5min` 第 17 条模板、`lib/v5.ts` 删除成片注释与 501 死代码、`types.ts` 4 个 Settings 字段、61 处过期文案中性化——本轮未回退任何一条（`grep "实现中|未挂载|尚未就绪"` 仍为 0）。

---

## R6 大幅减法 · 第一步（导航 4 项 + 模板墙首页）

用户反馈：「太难看了也太难操作了，没必要的东西你就删除了」。方向从"设计系统"改为**减法**：只留「选模板 → 生成 → 看片 → 设置」。

### R6-1 导航：13 个入口 → 4 项

| 保留 | 目标 | 高亮映射 |
|---|---|---|
| 模板 | `#/library/templates` | `library` / `projects`（首页也算模板） |
| 生产 | `#/agent` | `agent` |
| 成片 | `#/library/final` | `final` |
| 设置 | 弹层 | `settings` |

**从 UI 删除**（路由全部保留，hash 仍可直达）：微综艺、短片、长版、角色、场景、风格、模板库/发布包的独立入口、管线、Cinema、智能体，以及「生产 / 资产 / 成片 / 调试」四个分组标题、底部「后端·已连接 / 生成环境·就绪 / 任务·空闲」状态行、品牌副标题、状态轮询（`loadStatus` 12s×3 接口）。
`AppShell` 从 520 行收到 **~300 行**，props 仍向后兼容（8 个页面的调用未改）。

### R6-2 首页：模板墙

- 顶栏：`造片局` + 右侧唯一主按钮 `用当前模板生成`（+ 设置图标）。
- 正文：搜索框 + 「全部 / 综艺 / 短片」+ `N 个` + **33 张模板卡**（封面 / 名字 / 时长·类型 / `用这个生成`）。点卡片或按钮 = 写入 `localStorage["zpj_current_template"]` 并跳到 `#/agent`（生产）。
- **删掉**：3 张「今天做什么」大卡、4 个统计（项目/期数/成片/模板）、系列卡（底城往事）、最近成片区、`PageHeader` 副标题与指标、`我的项目` 网格、最近动态流、新建项目弹层入口。
- 数据只要两个接口：`/api/v6/agent/overview`（33）+ `/api/cinema/templates`（封面/状态）。
- `PlatformHome.tsx` 从 627 行收到 **~240 行**。

### R6-3 视觉硬约束落地（`design.css` 第 12 节）

- `body / .sh-app / .sh-main / .sh-content`：`background-image: none`（无噪点、无径向光斑）。
- `.sh-top`：纯 `--bg` 底、**无 backdrop-blur**、1px 下边线。
- `.sh-rail`：`--bg` 底、无阴影、1px 右边线。
- 所有卡片（`.ui-card / .wall-card / .pl-panel / .v18-panel / .cm-panel / .ag-col-head / .ag-rail`）：**`box-shadow: none` + `border-radius: 8px`**。
- 正文 14px / 标题 20px / 卡面 1px 边线：沿用 R5 token（`--f-14`、`--f-20`、`--line`）。

### R6-4 验收

```
> cd frontend; npx tsc --noEmit        → EXIT=0（零输出）
> cd frontend; npx vite build
✓ 95 modules transformed.
dist/assets/index-LhlNAdwL.css  429.24 kB │ gzip: 67.47 kB
dist/assets/index-lFnQV9zu.js   694.54 kB │ gzip: 207.97 kB
✓ built in 1.01s                      → EXIT=0
```

三张 1600×1000 截图（`tmp/ui/shot-r6.cjs`，浅色）：

| 文件 | 路由 | 断言 |
|---|---|---|
| `tmp/ui_r5/r6-home-1600.png` | `#/projects`（默认页） | `scrollWidth == clientWidth`；导航文本 = `["模板","生产","成片","设置"]` |
| `tmp/ui_r5/r6-produce-1600.png` | `#/agent` | 同上 |
| `tmp/ui_r5/r6-films-1600.png` | `#/library/final` | 同上 |

> 生产页 / 成片页的**内容减法**是下一步（本轮只换导航与首页）；两页仍是 R5 的内容密度。

### R6-5 下一步（待办）

1. 生产页：只留「模板名 + 生成按钮 / 一行阶段进度 / 播放」。删阶段参数编辑、工作流 JSON、run 历史、季生产、Muse 卡、技术路径、stat。
2. 成片页：只留「封面 + 名字 + 时长 + 播放」（点开全屏）。删批量导出/删除/QC/报告/发布包/镜头台。
3. 首页封面：30/33 有 3D 海报，其余 3 个（`relic_talk` 系）走字母占位——若要统一，需后端补 `poster_url`。

---

## R7 回到项目制五步创作（一级导航 8 项）

方向纠正：R6 的「4 项 + 模板墙首页」**砍过头**。产品主线是 **项目 → 五步创作**，不是"选模板点一下"。本轮把被砍掉的页面**放回导航与工作台 tab 流**，模板墙降级为独立页面。

### R7-1 一级导航（8 项，项目大厅为首页）

| # | 导航项 | 路由 | 说明 |
|---|---|---|---|
| 01 | 项目大厅 | `#/projects` | 续做（hero）+ 我的项目卡 + 最近任务 + 新建项目 |
| 02 | 剧本间 | `#/episodes/{eid}/script` | 台本编辑器 + AI 生成台本 + 生成分镜 |
| 03 | 资产库 | `…/assets` | 角色（固定音色 + 立绘）/ 场景 |
| 04 | 分镜台 | `…/storyboard` | 镜头表（景别/运镜/台词/时长）|
| 05 | 生成台 | `…/generate` | 任务队列（2s 轮询）|
| 06 | 成片房 | `…/finish` | 播放 / 渲染 / 多语种 / 发布 |
| 07 | 模板 | `#/library/templates` | 33 张模板卡 = 快速起一集 |
| 08 | 设置 | 弹层 | — |

- **02–06 是"当前这一集"的页内 tab**：`App` 在进入工作台时把 `episode.id` 写进 `localStorage["zpj_current_episode"]`（一处 `useEffect`，不改任何导航语义）；导航据此拼 `#/episodes/<eid>/<tab>`，**没有当前集时这 5 项显示为禁用**（`opacity .4` + 提示「先在项目大厅选一个项目/一期」）。
- 工作台本身仍保留 5 个 tab（编剧间 / 资产库 / 分镜台 / 生成台 / 成片房），与一级导航一一对应，形成「左侧进、上边切」两条等价路径。
- **不进导航但保留路由**：一键成片 `#/pipeline`、角色与声音 `#/characters`、3D 镜头台/队列/剪辑台 `#/cinema/*`、风格 `#/library/styles`、场景 `#/library/scenes`、成片库 `#/library/final`、智能体 `#/agent`（R6 的 9 个调试入口继续只留路由）。

### R7-2 页面接线

| 文件 | 改动 |
|---|---|
| `components/platform/PlatformHome.tsx` | **恢复为「项目大厅」**（从 `git HEAD` 取回 567 行原版）：hero 续做 / 我的项目（含「新建期」「进入工作台」）/ 最近任务 / 新建项目弹层 / 设置弹层；删掉重复的「模板 3D 影库」面板与对应 `cinemaApi.templates()` 请求（模板已有独立页） |
| `components/library/TemplateWall.tsx` | **新增**：R6 的 33 张封面墙搬到这里（搜索 + 全部/综艺/短片）；按钮改为 **「用这个起一集」** → `POST /api/projects`（同名节目格式 `{id}_5min`，短片模板回落默认格式）→ `onProjectCreated` 直接进工作台第 1 期 |
| `App.tsx` | `#/library/templates` 走 `TemplateWall`（其余 library tab 仍走 `LibraryPage`）；新增「记住当前集」effect |
| `components/shell/AppShell.tsx` | 导航改 8 项 + 编号 + 禁用态；其余（折叠、抽屉、tabs、主按钮）沿用 R6 |
| `styles/design.css` | 新增 §13（`.sh-nav-no` / 禁用态）+ 修正封面压暗层（生成块曾把 `.pl-cover-shade` 压成浅面导致封面发白） |

### R7-3 保留的 R6 减法

- 视觉仍为「浅色、1px 边线、8px 圆角、无阴影堆叠、无渐变」。
- 9 个调试入口不进导航（路由可达）。
- 上一轮修的导航守卫 / 轮询 / 刷新（`navSeqRef`、`agentStatus` 轮询、`LibraryPage.load` finally）零改动。

### R7-4 验收

```
> cd frontend; npx tsc --noEmit     → EXIT=0（零输出）
> cd frontend; npx vite build
✓ 95 modules transformed.
dist/assets/index-BIHIOxtz.css  429.56 kB │ gzip: 67.57 kB
dist/assets/index-yIAKRe9M.js   719.28 kB │ gzip: 214.48 kB
✓ built in 1.15s                     → EXIT=0
```

六张 1600×1000 截图（`tmp/ui/shot-r7.cjs`，浅色；脚本内从 `/api/projects` 取首个项目与首期 `eid`）：

| 文件 | 路由 | 断言 |
|---|---|---|
| `tmp/ui_r7/01-projects-1600.png` | `#/projects` | 无横向滚动；导航 = 8 项 |
| `tmp/ui_r7/02-script-1600.png` | `#/episodes/ep_001/script` | 同上；工作台 tabs = 5 项 |
| `tmp/ui_r7/03-assets-1600.png` | `…/assets` | 同上 |
| `tmp/ui_r7/04-storyboard-1600.png` | `…/storyboard` | 同上 |
| `tmp/ui_r7/05-generate-1600.png` | `…/generate` | 同上 |
| `tmp/ui_r7/06-finish-1600.png` | `…/finish` | 同上 |

导航文本实测：`["01项目大厅","02剧本间","03资产库","04分镜台","05生成台","06成片房","07模板","08设置"]`；`scrollWidth == clientWidth`（1600）六页全通过。

### R7-5 下一步

1. 五步页面的**视觉收敛**（本轮只接线）：剧本间页头的 `SCRIPT ROOM · 台本` 双语 kicker、资产库/分镜台/生成台的重复统计条可以再压。
2. `#/library/templates` 里 3 个无海报模板仍是字母占位（需后端补 `poster_url`）。
3. 「用这个起一集」对 `custom_tpl_*` 会回落 `roundtable_5min` 格式（没有对应的 `*_5min` 合同）；如需短片也有独立格式，需后端补 format 合同。

---

## R8 生成台「云端 Muse 生成 · 每镜按角色」

后端新接口（已重启，实测 200）：`POST /api/v6/muse/video/template` + `GET /api/v6/muse/video/jobs/{job_id}`。本页只做一件事：让用户点着出片。

### R8-1 改动文件

| 文件 | 改动 |
|---|---|
| `frontend/src/lib/agent.ts` | 新增 `museTemplate()`（POST，30s 超时）、`museTemplateJob()`（GET，20s，支持 `AbortSignal`）；新增类型 `MuseTemplateRequest` / `MuseTemplateJob`、归一化 `normalizeMuseTemplateJob`、`isMuseTemplateActive`、`runningJobIdFrom409`、`museFinalUrl` |
| `frontend/src/components/studio/MuseTemplateCard.tsx` | **新增**卡片组件（唯一 UI 改动） |
| `frontend/src/components/studio/Render.tsx` | 3 行接线：import、`{ project, episode, onNavigate }` 解构、卡片挂在页头与 KPI 之间 |
| `frontend/src/styles/design.css` | 新增 §14（`.muse-gen-*`，沿用浅色 token / 1px 线 / 8px 圆角） |

其它页面零改动；`App.tsx` 路由与上一轮的导航/轮询/刷新守卫均未触碰。

### R8-2 卡片行为

- 标题：**云端 Muse 生成 · 每镜按角色**（右侧灰字显示当前模板 id，如 `classics`）。
- 说明：「每段 10 秒，参考图 = 该句说话人的立绘，段间用上一段最后一帧接首帧。」
- 控件三个：**段数**（默认 30，1–120，下方 `≈ 300 秒`）、**首尾帧衔接**（开关，默认开）、**开始生成**。
- 模板 id 来源：`project.format_id.replace(/_5min$/i, "")`（`classics_5min → classics`），与后端 `_template(tid)` 的取值一致。
- 点击 → `POST {template, segments, chain, force:false, assemble:true}` → 拿 `job_id` 后**每 5s** 轮询：
  - **in-flight 互斥** + `document.hidden` 跳过 + `AbortController.abort()`（卸载/换任务即停），与 AgentPage 那三处同范式；
  - 状态点（运行=琥珀脉冲 / 完成=绿 / 失败=红）+ `stage` 一行（超长省略，title 全文）+ `ok_count/段数`；
  - 日志默认折叠，点「看日志」展开最后 8 行（小号灰字等宽）。
- `done` → **播放成片**（`final` 走 `museFinalUrl()`：`outputs/templates_3d/<tid>/final.mp4` → `/media/templates_3d/<tid>/final.mp4`；也兼容后端直接给 `/media/...` 或绝对路径）+ **重新生成**（`force:true`）。
- `failed` → 显示 `error` + `failed[]` 前 3 条。
- **409（已有任务在跑）当提示**：黄色一行「已有 Muse 视频任务在跑（muset_xxx），已切到它的进度」，并从 detail 里正则抠出 `muset_xxx` **自动接管该任务的轮询**，不报错、不弹红。
- 轮询遇 404（后端重启/清理）→ 提示「任务已不存在」并落 failed，不会永久打点。

### R8-3 禁用逻辑（截图实测）

```ts
const template = project.format_id.replace(/_5min$/i, "").trim();
const disabled = !template || starting || active;   // ← 无当前项目（无格式）/ 启动中 / 已有任务在跑
```

- 无 `format_id` 时：按钮禁用 + 标题右侧红字「未选择模板」+ 一段提示「当前项目没有节目格式：先在项目大厅新建/进入一个带模板的项目」。
- 截图实测（demo 项目 `format_id=classics_5min`）：`primaryDisabled=false`、`segments=30`、`chain=true`、`hint=30 段 ≈ 300 秒`、`template=classics`。
- 生成中：按钮文案变「生成中…」并禁用，输入框与开关同时禁用。

### R8-4 验收

```
后端探测
GET  /api/v6/video/provider                → 200 {"ok":true,"chosen":"muse",...}
POST /api/v6/muse/video/template (dry_run) → 200 {"ok":true,"dry_run":true,"template":"national_treasure","plan":[...]}

前端
> cd frontend; npx tsc --noEmit           → EXIT=0（零输出）
> cd frontend; npx vite build
✓ 95 modules transformed.
dist/assets/index-BIgDM6wH.css  432.16 kB │ gzip: 68.07 kB
dist/assets/index-BKvyaZDd.js   725.18 kB │ gzip: 217.40 kB
✓ built in 1.06s                            → EXIT=0
```

截图：`tmp/ui_r8/05-generate-muse-1600.png`（1600×1000，浅色，`#/episodes/ep_001/generate`），同脚本断言 `card=true`、`scrollWidth==clientWidth`。
脚本：`tmp/ui/shot-r8.cjs`（先从 `/api/projects` 取首个项目 + 首期 `eid` + `format_id`，再截图并读取卡片 DOM 状态）。

### R8-5 未做 / 注意

1. **没有放「试跑（dry_run）」按钮**：按需求只要三个控件；`agentApi.museTemplate({dry_run:true})` 已可用，后续要加一行按钮即可（返回 `plan[]`）。
2. 后端的 `logs` 是**字符串数组**（`"[角色] 消息"`），不是对象数组，归一化已按字符串处理。
3 `ok_count/段数` 的分母用的是**输入框里的段数**（后端未回填最终段数时也能看出进度）。
4. 该卡片与页面原有的 job 队列互不影响：Muse 任务走 `/api/v6/muse/video/jobs/*`，不写入 `/api/jobs`。
