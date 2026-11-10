# FIX_REPORT_2026-11-08 — 审查整改（安全 / 成本 / 前端 / 测试）

> 依据：2026-11-08 的全树审查（backend / pipeline / frontend 三路只读复审 + Lead 实测）。
> 范围：只改缺陷与测试可靠性；不改模板/资产数据。新增回归：`tests/test_fixes_20261108.py`（12 条）。
> 验证：**574 passed / 39 skipped / 0 failed（113.3s）**；前端 `tsc --noEmit && vite build` 通过。

## 1. 安全

### 1.1 CORS 从通配收紧到本机来源
- 文件：`backend/app/main.py`（`_cors_origins`）。
- 旧行为：`allow_origins=["*"]`，任意网页可驱动 `127.0.0.1:8090` 的写接口。
- 新行为：默认只放行 `127.0.0.1/localhost` 的 8090 与 5173；局域网/反代显式加
  `ZAOPIANJU_CORS_ORIGINS="http://192.168.x.x:8090"`。
- 验证：`tests/test_fixes_20261108.py::test_cors_rejects_foreign_origin`；实机 `OPTIONS` 外源 → 400。

### 1.2 改 LLM base_url 时清空旧 Key
- 文件：`backend/app/main.py::patch_settings`、`backend/app/llm.py::test_connection`。
- 旧链路：任意页面可 `PATCH /api/settings {llm_base_url:"https://evil"}`，再 `POST /llm/test`
  让服务端把已保存的 Key 发到新地址。
- 新行为：
  - base_url 的 host 变化且本次没给新 key → 删除 `LLM_API_KEY`（必须重填）；
  - `/llm/test` 支持临时 `base_url/model/api_key`，但传了 base_url 没给 key 时**不使用**已存 Key。
- 验证：`test_llm_base_change_clears_saved_api_key`、`test_llm_base_same_host_keeps_key`、
  `test_llm_test_override_does_not_forward_saved_key`。

### 1.3 Muse 同步接口的 template 路径穿越
- 文件：`backend/app/routers/muse.py::muse_sync`。
- 旧行为：`{clip,audio,template:"../../evil"}` → `OUT_ROOT / template` + `mkdir`，可在 outputs 外写 mp4。
- 新行为：clip 分支与视频分支统一走 `ids.check_id` + `ids.resolve_under`。
- 验证：`test_muse_sync_rejects_template_traversal`；实机请求 → 400。

## 2. 成本 / 正确性（pipeline）

### 2.1 HQ 短片：brief 总时长 vs target 前置门
- 文件：`pipeline/agents/registry.py::check_story / render_2d_hq`。
- 事实：`custom_tpl_v46_duologue` brief = 93 镜 / 307.3s，模板 `target_duration=19.14s`，
  QC 上限 28.7s；旧实现会全量开渲再判死。
- 新行为：`check_story` 记录 `brief_seconds/brief_over_target`；`render_2d_hq` 在
  `brief > 1.5×target + 2s` 时**拒绝启动 GPU**，提示对齐 target 或设 `params.limit` /
  `allow_brief_overflow=true`。
- 验证：`test_render_2d_hq_rejects_brief_overflow`。

### 2.2 智能体 voice 阶段结束停 Breeze TTS
- 文件：`pipeline/agents/registry.py::_stop_breeze / synthesize_voices`。
- 旧行为：`tts_and_pace` 拉起 Breeze（~2.3GB 显存），只有 `render_v5.run_render` 收尾会停；
  `--stages voice` 后模型常驻，紧接 HQ 易 OOM。
- 新行为：voice 阶段 `finally` 停服；`BREEZE_TTS_KEEP=1` 可保留。
- 验证：`test_stop_breeze_honors_keep_env` + 全量测试；未单独跑真 GPU 配音。

### 2.3 HQ 镜头复用加指纹、原子落盘
- 文件：`pipeline/templates3d_hq.py`。
- 旧行为：只判 `>100KB` 即复用；`shutil.copy2` 非原子，截断文件会被下次当有效镜头。
- 新行为：
  - 复用需 `brief_prompt == 当前 prompt`（旧 meta 用「镜头不旧于 brief」兜底）且
    seed/style_mode/style_2d/look 一致；
  - 落盘先写 `.tmp` → `ffprobe` 时长 >0.1s → `os.replace`；
  - 每镜 meta 新增 `brief_prompt`。
- 验证：全量测试（含 `pipeline/tests` 185 条）。

### 2.4 禁用阶段不再阻断下游
- 文件：`pipeline/agents/orchestrator.py`。
- 旧行为：disabled 阶段记 skipped 但不进 `done`，下游报「依赖未完成」直接 failed。
- 新行为：disabled（以及 `generate_art` 无 character 的跳过）= 外部已满足，加入 `done`；
  run 仍有 `skipped` 计数，非 optional 时状态为 `partial`。
- 验证：`test_disabled_stage_does_not_block_dependents`；33/33 工作流 dry-run planned。

### 2.5 Muse 全失败不覆盖权威时间线
- 文件：`pipeline/muse_shots.py`。
- 旧行为：无论成败都写 `work/episode.json`（`timeline_source=muse_cloud`），0 镜也能顶掉可用时间线。
- 新行为：全成 → 写 `episode.json`；部分成 → `episode.partial.json`；全败 → 不写；
  不全成时 `--assemble` 明确 skipped。
- 验证：`test_muse_render_all_failed_does_not_touch_authoritative_timeline`、
  `test_muse_partial_episode_filename`。

## 3. 前端

| 问题 | 文件 | 修法 |
|---|---|---|
| 批量动态返回类型错（后端返回数组，前端按对象取） | `frontend/src/lib/api.ts`、`Storyboard.tsx` | 类型改 `Job[]`，count 取 `result.length` |
| 生成台本 60s 客户端超时 < 后端 120s×2 | `api.ts` | 超时提到 300s |
| 切剧集失败后下拉框永久禁用 | `Studio.tsx` | `Promise.finally` 复位 `switching` |
| 15s 心跳 × 20s 超时导致旧响应覆盖新状态 | `App.tsx` | in-flight 守卫 + 连续 2 次失败才判离线 |

验证：`npm run build`（tsc 0 error，vite 1.1s）。

## 4. 测试可靠性

- `pipeline/tests/test_v3_render.py::test_storyboard_30s`：缺 `outputs/llm_scripts` 时 skip。
- `tests/test_v4_acceptance.py::test_ab_round_sample`：缺 `tmp/v4_voice_ab/AB_round1.wav` 时 skip。
- `tests/test_v5_2_acceptance.py::test_frontend_api_live`：`timeline.html` 改为动态取已渲染产物；
  目录覆盖改为 `count + len(missing) >= 21`，不再要求 21 部都已出片（真渲染需 GPU）。
- `tests/test_v5_acceptance.py::test_subtitle_quality`：接受现行主字幕样式 `Main`
  （原先只认名字含 `V/SUB` 的样式）。
- 新增 `tests/test_fixes_20261108.py`（12 条）：CORS / Key / 路径穿越 / 禁用阶段 /
  brief 门 / Muse 时间线。

## 5. 行为变化（部署须知）

1. **CORS 默认只允许本机**。局域网/反代需设 `ZAOPIANJU_CORS_ORIGINS`。
2. **换 LLM base_url 会清空已存 Key**（同 host 换路径不清），需重新填写。
3. **brief 远超 target 的短片会拒绝开渲**；要么对齐 `target_duration`，要么在 shots 阶段
   `params.limit` 或 `allow_brief_overflow=true`。
4. **禁用阶段视为外部已满足**；下游可能因缺少产物在运行时失败，但不会再在编排层报依赖错。

## 6. 复跑

```powershell
python -m pytest -q                      # 574 passed / 39 skipped
npm run build --prefix frontend           # tsc + vite
python -m pytest tests/test_fixes_20261108.py -q
```
