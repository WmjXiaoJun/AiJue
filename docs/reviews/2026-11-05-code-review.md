# AiJue 代码审查与改进规划

> 审查对象：`D:\BaiduSyncdisk\wmj_work\XJ\AiJue`（V49 · 短片线 + 季，当前工作区快照）
> 审查日期：2026-11-05
> 审查方式：**只读**。未修改任何代码、合同、配置；本报告是本次审查新增的唯一文件。
> 结论口径：静态审计（AST / 正则 / 目录扫描）+ 关键路径人工通读。量化数据来自本机脚本，复现命令见附录 A。

**关于并行改动**：审查期间工作区里另有一轮未提交的 V52 修复在推进（`docs/V52_CODE_REVIEW_FIXES.md`）。因此本报告的所有结论都以**复核时的磁盘代码**为准；其中"关键帧缓存校验 prompt 指纹"一项，在第一遍阅读后被补上，报告已据此更正（见 P0-2）。开工前请按 §5 的复核命令重新确认。

---

## 0. 一页结论

**这是一个 13 天成型的、能真实出片的 AI 视频工厂：产品完成度远高于工程完成度。**

- 业务链路已闭环：模板 → 分镜 → 配音 → 2D/H3 渲染 → 合成 → QC → 季合集。33 份工作流合同、370 份 JSON 合同、真实成片与运行证据齐备。
- 工程形态仍是一次高强度单人冲刺：没有依赖清单、没有 CI、内容改动可能被缓存静默吞掉、GPU 串行只在单进程内有效。这些不是"代码写得差"，而是**规模化之前必须补的账**。
- V52 已经清掉一批"明显是错的"的地方；剩下的基本是**结构性的账**——不修不会立刻崩，但会让每一次"加模板、改台词、重跑一集"的成本缓慢上升。

如果只做三件事（按投入产出排序）：

| 顺序 | 事项 | 一句话理由 | 预计工时 |
|---|---|---|---|
| 1 | **依赖锁定 + CI 四步门禁** | 没有可复现环境，后面所有修复都无法验证"真的修好了" | 1 人日 |
| 2 | **镜头层缓存加内容指纹** | `--reuse-shots` 默认开且只看文件大小，改台词后会整段复用旧镜头 | 0.5–1 人日 |
| 3 | **跨进程 GPU 锁 + 显式续跑** | 三类任务会互相抢 12GB 显存；崩溃后只能靠"隐性缓存复用"碰运气 | 1.5 人日 |

**不建议现在做大重构**：仓库处于高频迭代期（172 次提交、100 个未提交改动、全部文件 30 天内被动过），大重构会和内容生产互相踩踏。

### 综合评分（评审口径，1–5 分）

| 维度 | 分数 | 判断 |
|---|---:|---|
| 产品/业务完整度 | 4.5 | 链路闭环、有真实成片、有季与合集 |
| 领域抽象（分镜/风格/剪辑合同） | 4.0 | 合同化程度高，是最大资产 |
| 可维护性 | 2.5 | 20 个千行文件、五代版本并存、调用关系不可静态追踪 |
| 可靠性 | 2.5 | 缓存判据不足、失败无显式续跑、无超时/取消 |
| 可测试性 | 3.0 | 测试量与隔离做得好（491 passed），但无 CI、无环境锁 |
| 可观测性 | 2.0 | 306 处 print、无 request id、无指标 |
| 安全 | 2.5 | 无鉴权、CORS 全开、密钥明文落盘 |
| 交付工程化 | 1.5 | 无依赖清单、无 CI、无 Lint、无远程仓库 |

---

## 1. 现状基线（实测）

### 1.1 代码规模

| 目录 | 文件数 | Python 行数 | 说明 |
|---|---:|---:|---|
| `pipeline/` | 92 | 51,525 | 渲染内核 + 智能体编排 |
| `backend/` | 179 | 20,308 | FastAPI + SQLite + 任务线程 |
| `tests/` | 21 | 10,430 | 根级测试 |
| `pipeline/tests/` | 17 | 4,596 | 流水线测试（**两套测试目录并存**） |
| `scripts/` | 39 | 5,681 | 39 个一次性脚本 |
| `studio2d/` `imagegen/` `integrations/` | 80 | 3,446 | 2D 工具 / SDXL / ComfyUI |
| **合计** | **207** | **94,374** | 2,616 个函数（pipeline+backend） |

### 1.2 代码集中度（前 12）

| 行数 | 文件 |
|---:|---|
| 4,241 | `pipeline/scene_render.py` |
| 3,597 | `pipeline/motion2d.py` |
| 3,179 | `pipeline/style_v5.py` |
| 2,733 | `backend/app/routers/cinema_templates.py` |
| 2,650 | `backend/app/main.py` |
| 2,311 | `pipeline/characters_v5.py` |
| 2,132 | `pipeline/v5_art.py` |
| 1,954 | `backend/app/jobs.py` |
| 1,648 | `pipeline/v5_primitives_modern_b.py` |
| 1,596 | `pipeline/cinema3d_assemble.py` |
| 1,474 | `backend/app/routers/v5_create.py` |
| 1,363 | `pipeline/render.py` |

> 20 个文件 > 1000 行；12 个函数 > 200 行（最长 `render.py:run_render` 331 行）。前端同理：`CharactersPage.tsx` 2,823 行、`AgentPage.tsx` 2,194 行。

### 1.3 合同与资产

| 项 | 数量 |
|---|---:|
| `contract/` JSON | 370（全部可解析，0 损坏） |
| 模板合同 | 21 内建 + 12 custom = 33 |
| 工作流合同 | 33（`contract/v6/workflows`） |
| 场景 / 角色 / 道具 | 74 / 50 / 44 |
| 风格 | 17 |
| 季合同 | 1 |
| 成片产物 | 16.08 GB / 14,306 文件 |
| agent 运行证据 | 22 个 run 目录 |

### 1.4 接口与前端

- 后端约 **150 个路由装饰器**：`/api/v5` 39、`/api/cinema` 22、`/api/v6` 18、`/api/pipeline` 8、`/media/v5` 4（新一代合计 91）；其余约 59 个是老 demo `/api/*` 与根路径路由（`/projects`、`/episodes`、`/shots` 等）。
- 前端 74 个 TS/TSX、`strict: true`、**0 处 `any`**、0 处 `@ts-ignore`；20 个手写 CSS、无 ESLint/Prettier、无前端测试。

### 1.5 质量信号（全仓 Python，复核时）

| 指标 | 数值 | 判断 |
|---|---:|---|
| `except Exception` | 约 420 处（≈4.5 处/千行） | 偏高；多数已标 `noqa: BLE001`，属**有意**的防御式写法 |
| 异常被立即吞掉（`pass`/`continue`/`return`） | **228 处** | **危险**：失败不留痕 |
| `print()` | 306 处 | 无结构化日志 |
| 引入 `logging` 的文件 | 29 / 207（14%） | 反向印证 |
| 硬编码绝对路径 | 41 处 | 多为 Windows 字体，可接受；ComfyUI 根目录写死用户路径不可接受 |
| `shell=True` | 1 处（`pipeline/muse_video.py:230`） | 可收敛 |
| `eval/exec/pickle/yaml.load` | 0 | **干净** |

### 1.6 仓库状态

- 172 次提交、单 `master` 分支、**无 remote**、100 个未提交改动；`.git` 松散对象 532 MB（打包仅 24.4 MB）。
- 无 `.github`、无 `requirements.txt` / `pyproject.toml` / lock 文件、无 ESLint。
- 文档 104 篇，版本跨度 V3 → V52；README 已补到 V52，但**同一页内数字互相矛盾**（详见 P0-3）。
- 本机实测：系统 Python（3.13 / 3.11）**均无 fastapi / sqlalchemy / PIL / numpy / pytest**，即当前机器按 README 无法直接启动后端或跑测试。

---

## 2. 做得好的地方（不要改坏）

1. **合同驱动的领域抽象**：`contract/v5` 有正式 JSON Schema（7 份），`v5_catalog.py` 做引用完整性校验与 `custom/` 深合并覆盖。这是整个项目最值钱的资产，比任何渲染代码都重要。
2. **运行证据链**：每次 run 落 `run.json / events.jsonl / summary.md / workflow.json`，季 run 独立目录。出问题能复盘——很多商业流水线都缺这个。
3. **测试隔离意识**：`tests/conftest.py` 用临时目录覆盖 `ZAOPIANJU_OUTPUTS_DIR`，测试不污染真实产物；`skipif` 用得克制；491 passed 是可信的回归底子。
4. **前端类型纪律**：strict + 零 `any` + 零 `ts-ignore`，74 个文件里属于高水准。
5. **原子写与幂等意识**：多处 `tmp + os.replace`（如 `cinema3d.py:137`）、`run.json` 逐阶段持久化、季生产 `only_missing`、镜头级 `--reuse-shots`。
6. **对缓存正确性的自我修正**：`templates3d_hq` 的关键帧缓存已加入 `prompt_sig`（crc32）与 `base_seed` 校验，注释写明"改台词后不能继续吃旧关键帧"。说明作者已经意识到这个问题——只是还没打通到镜头层（P0-2）。
7. **自我审查的工程习惯**：V52 那轮"4 个只读审查员分头审 → 汇总 → 逐条修 → 回归"，本身就是这个仓库最好的机制；应固化成流程，而不是一次性运动。

---

## 3. 问题清单

> 分级：**P0** = 会导致"错了但看起来对"或直接丢工作；**P1** = 明显抬高边际成本；**P2** = 体验/债务。
> 每条：现象 → 证据 → 影响 → 建议 → 验收。行号/函数名以复核快照为准，会随迭代漂移。

### P0-1 没有依赖清单，环境不可复现

- **现象**：根目录无 `requirements.txt` / `pyproject.toml` / lock；依赖只写在 `README.md` 的一行 `pip install ...`；本机两个 Python 都不含这些包。
- **证据**：根目录清单为空；`py -0p` 与 `importlib.find_spec` 实测全为 `False`。更隐蔽的是：缺 `jsonschema` 时只打 warning 并放行（`pipeline/v5_catalog.py` 的 ImportError 分支），**环境差异会直接改变验收强度**。
- **影响**：换机/换人/半年后回看都要重新"猜"版本；无法建立可信 CI；"本机能跑"成为唯一事实来源。
- **建议**：`pyproject.toml` + `uv.lock`；把 `jsonschema` 改为**必需**；补 `scripts/bootstrap.ps1`（建 venv → 装依赖 → 构建前端 → 自检）。
- **验收**：全新目录 clone 后一条命令起服务；`python -m pipeline.v5_cli validate --strict` 返回 ok；`import jsonschema` 失败即启动失败。

### P0-2 改内容后可能被"三级缓存"静默吞掉

- **现象与证据**：从外到内有三层复用，判据强度依次递减：
  1. **镜头成品层** —— `render_2d_hq` 默认 `reuse_shots=True`（`pipeline/agents/registry.py:289`），`templates3d_hq` 只判断 `shots/<sid>.mp4` 存在且 >100KB 就 `continue`（约 822-826 行）。**改台词后连镜头都不会重渲**，日志只写一行 `ok (reuse)`。
  2. **片段层** —— `gen_t2va` 命中判据是"文件 >200KB"（`templates3d_hq.py:636-638`），文件名只含 `tid / shot_id / 风格后缀`，不含 prompt 或 seed；`gen_ref2va` 在非关键帧模式下同理（约 703-712 行）。
  3. **关键帧层** —— 已修好：`params_ok` 现在校验 `prompt_sig`（crc32）与 `base_seed`（约 505-525 行）。
- **影响**：改了台词/视觉描述/种子后，用户点运行、系统报 `ok`、成片却没变。不报错、不留痕，是最难排查的一类问题，还会污染整季一致性判断。第一层最致命，因为它是**默认开启**的。
- **建议**：三层统一用内容指纹，逐层向上传递：
  - 关键帧：已有 `prompt_sig`，补齐 `style_key` 与 `ip_end`（已校验）即可；
  - 片段：文件名或 sidecar `.meta.json` 记 `clip_key = sha1(prompt + seed + style + duration)`，命中必须比对；
  - 镜头层：`shots/<sid>.meta.json` 记 `spec_hash = sha1(分镜 JSON + 风格 + 关键帧 sig)`；`reuse_shots` 改为"有 hash 且相等才复用"，无 hash 视为未命中；
  - 提供 `--refresh-cache`（只失效缓存）与 `--force`（全量重渲）两个语义分离的开关。
- **验收**：改一句台词 → 该镜 `spec_hash`、clip 文件名、最终 mp4 mtime 三者都变；什么都不改 → 二次运行 `reused=true` 且 0 次重算；`ok (reuse)` 日志里能打出被复用的 hash 前缀。

### P0-3 没有 CI 门禁，绿灯不可持续；文档口径自相矛盾

- **现象**：无 `.github`、无 lint 配置。V52 记录 `pytest tests` 491 passed，但 `README.md` 写"21 文件 343 用例 + 17 文件 185 用例"（合计 528），同页另一处又写"491 passed"；还写"142 个 API 端点"（实测约 150）、"16 内建 + 17 custom"（合同目录实际 21 + 12，总数 33 一致）。
- **影响**：没有自动回归，"绿灯"只在某人手动跑过的那一刻成立；同一份 README 给出三套数字，会让"当前状态"不可信——这比代码问题更影响接手成本。
- **建议**：
  1. CI 四步：`ruff check` → `pytest tests pipeline/tests` → `v5_cli validate --strict` → workflow schema 校验；
  2. 数字单一事实源：README 的端点/测试/模板计数由 `scripts/repo_stats.py --write-readme` 生成，不手写；
  3. 两套测试目录明确分工（`tests/` 面向 API 与契约，`pipeline/tests/` 面向渲染算子）。
- **验收**：推送即跑、5 分钟内出结果；`repo_stats.py --check` 在 CI 里比对 README 与实际一致。

### P1-4 失败后没有显式续跑入口，进度不可见；重启即判死

- **现象**：任务用 `threading.Thread(daemon=True)` 跑（`backend/app/jobs.py` 的 `dispatch`），进程退出线程即死；启动时 `_recover_stale_jobs()` 把队列里所有 `queued/running` 一律标 `failed`（`backend/app/main.py`，注释自承"进程退出后线程即消失"）。重试端点（`main.py` 的 `/jobs/{jid}/retry`）是**整单重跑**；agent 侧无 `resume`。
- **补充事实**：已完成镜头其实会被 `reuse_shots` 隐式跳过，所以并非"全废"；但**用户看不到"从第几镜继续"**，也不确定是重跑还是复用——这正是它危险的地方：进度信息只存在于被吞掉的日志里。
- **影响**：崩溃后要人工判断"能不能直接重跑"，季生产时尤其容易整季重来一遍；单集 6–10 分钟 GPU 的等待成本被重复支付。
- **建议**：
  1. run 目录落 `state.json`，每镜完成原子写 `status / spec_hash / output / elapsed`；
  2. 新增 `python -m pipeline.agents resume --run-id ...`，只补 `pending/failed`；
  3. `jobs` 表加 `attempt / heartbeat_at / cancel_requested`，重启把 `running` 且心跳新鲜的重新入队（而非一律 failed）；
  4. 前端运行面板显示"3/7 镜完成 · 已复用 2"。
- **验收**：第 3 镜时 `kill` 进程 → 重启 → `resume` 只重跑第 3 镜；`run.json` 里 `resumed_from` 与每镜 `reused` 可查。

### P1-5 GPU 串行不跨进程、也不跨模块

- **现象**：`backend/app/jobs.py` 的 `GPU_LOCK` 是 `threading.Lock()`；全仓文件锁只有两处：Muse（`pipeline/lipsync.py` 的 `_acquire_lock` / `.muse.lock`）与 v3 单模板（`pipeline/render_v3.py` 的 `_acquire_lock`）。图像生成、H3、`templates3d_hq` 都没有跨进程互斥。而实际有三条并发路径：Web 任务线程、agent run 线程、CLI/季生产（可能 `python -m` 子进程）。
- **影响**：12GB 显存下，UI 生成 + agent 跑 H3 + CLI 跑关键帧同时发生 → OOM / ComfyUI 队列卡死；失败表现像"模型崩了"，实际是资源争抢。
- **建议**：抽一个 `pipeline/gpu_lock.py`（复用 Muse 的 PID 回收逻辑），按资源分锁 `gpu.sdxl` / `gpu.h3` / `gpu.muse`；所有 GPU 入口统一 `with gpu_lock("h3"):`；暴露 `GET /api/v6/agent/gpu` 显示持有者与排队人数。
- **验收**：并发触发两条 GPU 任务，第二条日志出现 `waiting for gpu.h3 (holder=job_x)`，总时长 = 串行之和，无 OOM。

### P1-6 编排无超时 / 重试 / 取消；run_id 秒级冲突

- **现象**：`orchestrator.run_template` 的阶段循环里，任一阶段异常即 `break`，后续阶段全部不执行，无重试、无 timeout；`v6_agent` 只有启动，无 cancel。`run_id` 由 `time.strftime("%Y%m%d_%H%M%S") + "_" + template` 生成，同一秒两次运行会落到同一目录（`mkdir(exist_ok=True)` 不报错）。
- **影响**：一次网络抖动（edge-tts）或一次显存不足，整轮白跑；UI"停止"后子进程还在跑；并发两次 run 互相覆写证据。
- **建议**：stage 合同加 `timeout_sec / retries / retry_backoff / continue_on_error`；重试仍失败才 `break`，后续标 `blocked`；新增 `POST /api/v6/agent/runs/{id}/cancel`；`run_id` 加 4 位随机后缀并独占创建。
- **验收**：注入一次 TTS 抛错 → 自动重试 2 次后失败，`run.json` 可见 3 次 attempt；取消 3 秒内进程消失；同秒并发两次 run 得到两个目录。

### P1-7 双栈 + 五代版本并存，调用关系不可静态追踪

- **现象**：渲染入口至少 6 个（`render.py` / `render_v3.py` / `render_v5.py` / `scene_render.py` / `motion2d.py` / `templates3d_hq.py`）；风格 3 套、QC 3 套。
- **更麻烦的是调用方式有 5 种**：正常 import、`_require("scene_render")` 字符串导入（`render_v5.py`）、`importlib.import_module` 动态扩展（`scene_render.py` 的 `EXTENSION_MODULES`）、`python -m` 子进程（`cinema3d.py`）、环境变量 `V5_PRIMITIVE_MODULES` 覆盖。
- **影响**：静态扫描"无引用"的模块里**大部分其实是活的**（见附录 B）。清理/重构无法靠工具判断，只能靠人肉记忆。
- **建议**：建**唯一渲染入口注册表**（`pipeline/agents/registry.py` 已有工具注册表，可扩成渲染器注册表）；旧代文件头加 `DEPRECATED` 并在首次调用时 `logger.warning`；脚本扫 AST 生成 `docs/ARCHITECTURE_GRAPH.md`；旧代移入 `pipeline/legacy/`，CI 禁止新代码 import。
- **验收**：`python -m pipeline.registry --graph` 列出全部入口与调用方。

### P1-8 巨型文件与单体入口

- **现象**：见 1.2；`main.py` 2,650 行里仍混着老 demo 路由，`cinema_templates.py` 2,733 行，`jobs.py` 1,954 行。
- **影响**：review 不可能、合并必冲突、改一处要读 3000 行上下文。
- **建议**：按资源拆 `main.py`（老 `/api` 下沉 `routers/legacy_demo.py` 并标 deprecated）；`cinema_templates` 按 `templates / shots / render / publish` 四段拆；渲染大文件先抽纯函数（`style_v5` 的 finish/technique 最容易切）。
- **验收**：新增文件 ≤ 800 行；`main.py` ≤ 300 行，只做装配。

### P1-9 工作流只校验 id，不校验内容

- **现象**：`save_workflow` 已校验 id 白名单（V52 修复），但**不校验** stage id 唯一、`tool` 是否在注册表、`depends` 是否成环、`params` 类型；`PUT /api/v6/agent/workflow/{template}` 仍只要求 `stages` 非空。
- **影响**：工作流写坏后，错误以 KeyError / 无限等待的形式在运行时出现，证据链里只看到"stage failed"。
- **建议**：workflow schema（stages 唯一、tool 枚举、depends DAG 无环、params 白名单）；保存前校验，失败 422 + 字段路径；CI 全量 33 份。
- **验收**：写入环形依赖 / 未知 tool → 立即 422 并指出字段。

### P1-10 可观测性弱

- **现象**：306 处 `print`、仅 29/207 文件用 logging；228 处异常被吞；无 request id、无 stage 耗时聚合、无失败率；`latest_runs()` 每次全盘 `rglob` 扫 `run.json`。
- **影响**：线上问题只能翻 `events.jsonl`；"为什么这集慢了"答不出来；季生产失败率无法统计。
- **建议**：统一 `logging` + JSON formatter（`run_id/template/stage/shot/duration_ms/exit_code`）；中间件注入 `X-Request-ID` 透传到子进程 env；`run.json` 汇总 `gpu_seconds`；用 `outputs/agent_runs/index.json` 替代全盘递归。
- **验收**：任取一次失败 run，凭 `run_id` 一条命令拉出"哪一镜、哪个模型、耗时、退出码"。

### P2-11 无鉴权、CORS 全开、密钥明文

- **现象**：`main.py` 的 CORS 配置为 `allow_origins=["*"]`，注释写明"demo 无鉴权"；所有写接口无凭证校验；MetaSo/Upload-Post key 存 `backend/data/*.env` 明文，`chmod(0o600)` 在 Windows 上是空操作。
- **影响**：服务一旦 bind 到 `0.0.0.0` 或经内网访问，任何人都能触发 GPU 任务、改配置、拉走成片；密钥随同步盘/备份外泄。
- **建议**：默认 `127.0.0.1`；绑定非本机时强制 `X-Api-Token`；CORS 改显式来源；密钥改 Windows DPAPI 或 `icacls` 收紧，并加日志脱敏断言。
- **验收**：非本机访问写接口返回 401；日志中永不出现明文 key。

### P2-12 数据与文档债

- **现象**：`outputs/` 16.08 GB / 14,306 文件、22 个 run 目录无保留策略；`localize/models` 2.3 GB；104 篇文档跨 V3–V52；`.git` 532 MB 松散对象；100 个未提交改动；无 remote。
- **影响**：磁盘持续膨胀；"哪份是当前方案"要靠猜；无远程 = 单点数据风险。
- **建议**：产物保留策略（每次仅留 `run.json/events/summary/poster`）+ `scripts/gc_outputs.py --days 30 --dry-run`；`docs/INDEX.md` 分"现行/已归档"；`git gc --aggressive`、大图转 LFS；**尽快加 remote 备份**。
- **验收**：`outputs` 月增长可预测；`docs/INDEX.md` 一屏说清当前方案。

---

## 4. 路线图（合计约 8–10 人日）

> 1 人日 ≈ 6 小时有效工作；每阶段结束都应保持"能出片"。

### Sprint 0 · 工程基线（1 人日）

| # | 任务 | 验收 |
|---|---|---|
| 0.1 | `pyproject.toml` + 锁文件 + `scripts/bootstrap.ps1` | 新机一条命令起服务 |
| 0.2 | Ruff（`E,F,B,BLE`）+ pre-commit | 全仓 0 error（存量白名单） |
| 0.3 | CI 四步（lint / pytest / contract validate / workflow validate） | 推送即跑，5 分钟出结果 |
| 0.4 | `scripts/repo_stats.py` 生成 README 数字 | CI 校验 README 与实际一致 |

### Sprint 1 · 可靠性（2.5–3 人日）

| # | 任务 | 验收 |
|---|---|---|
| 1.1 | 三层缓存内容指纹（镜头 / 片段 / 关键帧）+ `--refresh-cache` | 改台词必重算；不改则 0 重算 |
| 1.2 | `state.json` + `resume` + 前端进度 | kill 后只补未完成镜头 |
| 1.3 | `pipeline/gpu_lock.py` + 排队可见 | 并发任务串行且可观测 |
| 1.4 | stage timeout / retries / cancel；`run_id` 唯一化 | 注入失败自动重试；取消 3 秒生效 |
| 1.5 | `jobs` 加 `attempt/heartbeat`，重启重入队 | 重启后 running 任务自动续跑 |

### Sprint 2 · 架构收敛（3–4 人日）

| # | 任务 | 验收 |
|---|---|---|
| 2.1 | 渲染注册表 + 调用图脚本 + `docs/ARCHITECTURE_GRAPH.md` | 入口可枚举、调用方可查 |
| 2.2 | `main.py` 拆成 router 装配层；老 `/api` 下沉 legacy | `main.py` ≤ 300 行 |
| 2.3 | legacy 代标记 + `pipeline/legacy/` 归档 | CI 禁止新代码 import legacy |
| 2.4 | workflow schema（DAG 环检测 / tool 枚举 / params 白名单） | 非法工作流 422 |

### Sprint 3 · 交付质量（2 人日，可与内容生产并行）

| # | 任务 | 验收 |
|---|---|---|
| 3.1 | 结构化日志 + `X-Request-ID` + run 索引 | 单次失败一条命令定位 |
| 3.2 | 安全收口：默认 localhost、写接口 token、CORS 白名单、密钥 ACL | 非本机写接口 401 |
| 3.3 | 前端 ESLint + Playwright 冒烟（`#/agent`、`#/library/final`、季面板） | 冒烟 3 条全绿 |
| 3.4 | 产物 GC + docs 索引 + 仓库瘦身 | `--dry-run` 报告可读；`.git` < 100 MB |

> 具体施工步骤、改动点与代码片段见配套文档：`docs/OPTIMIZATION_PLAN_2026-11-05.md`。

---

## 5. 开工前的复核命令

仓库正在被并行修改，动手前先确认这些结论仍然成立：

```powershell
# 1) 关键帧是否仍校验 prompt_sig / base_seed
Select-String -LiteralPath pipeline\templates3d_hq.py -Pattern "prompt_sig|base_seed" -Context 2,2

# 2) 镜头层是否仍只按文件大小复用
Select-String -LiteralPath pipeline\templates3d_hq.py -Pattern "reuse_shots|ok \(reuse\)" -Context 3,3
Select-String -LiteralPath pipeline\agents\registry.py -Pattern "reuse_shots" -Context 2,2

# 3) 任务恢复是否仍一刀切 failed
Select-String -LiteralPath backend\app\main.py -Pattern "_recover_stale_jobs" -Context 0,15

# 4) 编排是否仍异常即 break
Select-String -LiteralPath pipeline\agents\orchestrator.py -Pattern "break" -Context 3,3
```

## 6. 建议的门禁与度量

**CI 必过（每次提交）**

```powershell
ruff check .
python -m pytest tests pipeline/tests -q
python -m pipeline.v5_cli validate --strict
python scripts/check_workflows.py        # 33 份：schema + DAG 无环 + tool 存在
python scripts/repo_stats.py --check     # README 数字与代码一致
```

**每季/每周看板（从 run 证据聚合，不新增埋点）**

| 指标 | 口径 |
|---|---|
| 单集成片耗时 | `run.json` stages elapsed（P50 / P90） |
| 缓存命中率 | `reused` 镜头数 / 总镜头数 |
| 缓存误命中 | 改内容后仍 `reused` 的次数（应为 0） |
| 阶段失败率 | 每 stage `failed` / 运行次数 |
| GPU 排队时长 | 锁等待 / 总时长 |
| 交付合格率 | `evaluate_qc` / `evaluate_hq_qc` 通过率 |

**代码健康红线（新增代码适用，存量递减）**

- 单文件 ≤ 800 行；单函数 ≤ 120 行；
- 新增 `except Exception` 必须带日志或明确返回值语义（禁止裸 `pass`）；
- 新模块必须在注册表登记，否则 CI 报"不可达模块"。

---

## 7. 明确不建议做的事

1. **不要现在重写渲染内核**。`scene_render` / `motion2d` 是 7,800 行成熟算子，重写收益低、风险极高；只做"切分"和"加壳"。
2. **不要合并两条栈**。V5 渲染栈与 HQ 短片栈服务不同产物形态，合并会同时拖慢两条线；该统一的是**输入协议（ShotSpec）**，不是渲染器。
3. **不要为一两个人日的问题上微服务/消息队列**。SQLite + 文件锁足够当前吞吐；先做"可恢复 + 可观测"。
4. **不要批量删除"看起来没引用"的模块**。本次扫描已证实动态导入与子进程会让静态分析大面积误判（`v5_primitives_modern_b.py` 是活的）。清理走注册表 + 运行时告警，观察一周再删。
5. **不要在无 CI 的情况下继续加模板**。模板是资产，但每个新模板都复制一份操控参数；先补 schema 与门禁，扩张才低成本。

---

## 附录 A · 证据与复现命令

本次审查使用的只读脚本（临时目录，不在仓库内）：

| 脚本 | 作用 |
|---|---|
| `%TEMP%\aijue_scan.py` | 目录规模、最大文件、总行数 |
| `%TEMP%\aijue_audit.py` | 语法/异常/日志/合同/硬编码路径/测试清单 |
| `%TEMP%\aijue_refs.py` | 模块引用计数（含 `_require` / `import_module` 字符串） |
| `%TEMP%\aijue_routes.py` | 路由分代统计 |
| `%TEMP%\aijue_age.py` | 文件最后改动时间分布（全部 ≤30 天，佐证"高频迭代期"） |
| `%TEMP%\aijue_tests.py` | 两套测试目录的文件/行数 |

## 附录 B · 待人工确认的清理候选

以下模块静态扫描"无 import 引用"，但**可能是 CLI 或动态加载**，删除前需逐个确认（本次未做任何删除）：

| 模块 | 行数 | 可能用途 | 确认方式 |
|---|---:|---|---|
| `pipeline/cinema3d_assemble.py` | 1,596 | `python -m` 剪辑器（`cinema3d.py` 调用） | **确认在用** |
| `pipeline/v5_primitives_modern{,_b}.py` | 2,563 | `EXTENSION_MODULES` 动态扩展 | **确认在用** |
| `pipeline/templates3d_pipeline.py` | 171 | 批处理 CLI？ | `git log -S` + 全仓字符串搜索 |
| `pipeline/templates3d_relip.py` | 162 | 重口型 CLI？ | 同上 |
| `pipeline/templates3d_new_templates.py` | 140 | 新模板批跑？ | 同上 |
| `pipeline/templates3d_hq_batch.py` | 67 | 批量入口？ | 同上 |
| `pipeline/deliver.py` | 120 | 交付脚本（README V51 提到 `python -m pipeline.deliver`） | **确认在用** |

> 结论：**当前不建议删除任何模块**，应先建注册表与调用图，再按图清理。

---

## 附录 C · 本次审查的边界

- 未运行应用、未跑 GPU 任务、**未实跑 pytest**（本机缺依赖，见 P0-1）。文中"491 passed"引自 `docs/V52_CODE_REVIEW_FIXES.md`，属项目自述，非本次实测。
- 未审阅 `outputs/` 内 16 GB 产物内容；未审阅 `local_models/` 与 `tmp/refs/` 第三方代码。
- 未做业务/内容质量评价（风格是否好看、剧本是否合理不在范围）。
- 审查期间仓库有并行改动，P0-2 的关键帧部分已据此更正；行号与函数名可能在提交后漂移。
