# R5 状态（2026-11-07 01:05）

## 已完成

| 项 | 结果 | 证据 |
|---|---|---|
| **UI 重设计** | 浅色纸面默认 + 深色跟随系统；左侧 5 组导航（生产/资产/成片/调试/设置）；首页 = 3 张「今天做什么」+ 33 模板宫格；成片库/管线/智能体同一套皮 | `npx tsc --noEmit` EXIT=0、`npx vite build` EXIT=0；16 张截图 `tmp/ui_r5/`（4 页 × 2 尺寸 × 浅/深，16/16 无横向溢出） |
| **视频模型优先级** | `muse > comfyui_local > other`，chosen=Muse（MuseTalk 可用）；ComfyUI 0.42.0 在线兜底；Muse 桶只认 Muse 家族 | `GET /api/v6/video/provider` → 200；`pipeline/video_provider.py` |
| **DeepSeek 剧本** | 修掉「剧本目录读错、LLM 台本从未进渲染」的 bug；`test_connection` ok（969ms）；生成 52 句/5 段/1147 字 5 分钟台本 | `outputs/llm_scripts/tonight_show_5min_v2.json`；`tests/test_r5_provider_and_script.py` 8 条 |
| **内容密度根因 + 修复** | 模板 act 自带 `text` → 每幕只 1 句 → 5 分钟片只有 9 句台词；新增 `prefer_script` / `script_mode:"script"`，52 句铺成 52 个台词镜（285s） | `tests/test_r5_script_driven.py` 5 条；旧行为/新行为对比见 `tmp/spread_test.txt` |
| **开源库统计** | 直接依赖 / 工具链 / 模型 / 只借鉴不抄代码（ArcReel AGPL 明确不复制）+ 合规注意 + "没用到的库" | `docs/OSS_STACK.md` |
| **三风格 5 分钟样片（classics，旧链路）** | 2d 85.1MB / 3d 31.3MB / realistic 92.7MB，均 300.0s·1280×720·25fps·aac | `outputs/templates_3d/classics/final_300s_*.mp4` |
| **质量抽检工具** | `docs/audit/_tools/r5_quality_check.py`（无脸率/多人脸/复制疑似）；classics：3d **50% 无脸 FAIL**，2d/realistic 17.2% pass | 报告：fix-render 消息 |
| **40s 冒烟（新链路）** | `final_40s_2d_anim.mp4`（6.4MB，40.0s，字幕 21 条包装事件），修掉 `--limit` 下 assemble 误判的 bug | `outputs/templates_3d/tonight_show/final_40s_2d_anim.mp4` |

测试：**525 passed / 57 skipped / 0 failed**。

## 进行中

- **tonight_show 2D 300s**：`--only tonight_show --looks 2d_anim --target 300`，约 2.4 分钟/镜 × 74 镜 ≈ 1.5h；完成后按顺序跑 3d_anim、realistic。
- 三条成片共用当前 brief（**只有 7–9 句真台词**），用于验证渲染/包装/QC 链路。

## 下一步（待拍板）

1. **重做 brief → 重渲三版**：用 52 句台本 + `script_mode:"script"` 重新生成 `contract/v5/cinema3d/templates3d/tonight_show_semantic.json`，再跑三观感 → 才是「真正的 5 分钟微综艺」（52 条字幕、无空镜撑时长）。
2. **3D 观感**：classics 实测 50% 帧无人脸（多数镜头是纯 T2VA 无角色锚点）。要么给 T2VA 加 face-anchor 后缀 + 负面词重渲受影响镜头，要么本轮先用 2D + 真实交付、3D 单独立项。
3. **classics 是否按新链路重渲**，保证与 tonight_show 同一口径。

## 已知未覆盖（下轮清）

`@media`/`@keyframes` 内颜色、内联 style 色、遮罩渐变叠层、局部 `--tone`、楷体标题、<1366 时智能体右栏折行、成片卡按钮未收纳、CSS 体积 426kB（gzip 66.8）。
