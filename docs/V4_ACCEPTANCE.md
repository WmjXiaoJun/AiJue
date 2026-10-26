# V4 独立验收报告：Breeze TTS 2 语音 + OpenFilm 式时间线剪辑

> 验收口径：`docs/PLAN_V4_AUDIO_EDIT.md` §2.4/§5 + Lead V4 收口要求；上一版：`docs/V3_ACCEPTANCE.md`（P0/P2 16/16 已交付）。
> 生成时间：2026-10-26 13:24（由 `tmp/qa/gen_v4_doc.py` 依据真实产物生成，可重复运行）
> 验收人：qa-verifier（独立于 render-dev / h3-dev / stage-dev；**只读 outputs/与 contract/，不启动渲染**）。
> ⚠️ V4 只改语音与剪辑层；H3 证据链/黑帧/字幕/静态标记等 V3 硬门**继续适用**（QA 回归验证未回退）。

## 0. 结论摘要

| 项 | 状态 |
|---|---|
| V4 参考模板（tonight_show / history_talk 40s） | ✅ 2/2 通过 V4 验收（含 audio_pace/timeline/LUFS） |
| V4 16 模板（14×30s + 2×40s；语音驱动时长 33.16–43.79s） | ✅ 16/16 全部完成并通过终门 |
| audio_pace（atempo=1.0 / 无截断 / 语音装进镜头） | ✅ 16/16 通过 |
| timeline.json/html（五轨覆盖） | ✅ 16/16 存在且五轨完整 |
| LUFS -17~-15 / TP ≤ -1.0 dBTP | ✅ 16/16 实测 ∈ [-16.77, -15.95]（欠调 6 个已全量修复） |
| **loudness 欠调修复** | ✅ `scripts/fix_loudness.py --root outputs/templates_v3 --fix`：fixed=6 / skipped_ok=10 / failed=0；qc_report 16/16 回写 loudness check 且 ok |
| H3 证据链不回退（h3/workflow/prompt_id/seed/ref_image） | ✅ V3 原硬门继续通过（见 §4） |
| A/B 归档 + 试听样本 | ✅ |
| **16/16 V4 终门** | ✅ `python -m pytest tests/test_v4_acceptance.py -q` → **100 passed / 0 skipped** |
| 全量回归 | ✅ tests/ 427 passed；pipeline/tests 164 passed；V3 验收 90 passed（见 §8） |
| **验收结论** | ✅ **16/16 V4 可交付**（语音/剪辑增量全绿，H3/画面/字幕硬门未回退） |

## 1. 验收命令（可复跑）

```powershell
# V4 全量验收（只对达到 V4 可验收状态的模板断言，其余 skip 报告）
python -m pytest tests/test_v4_acceptance.py -q
python -m pytest tests/test_v4_acceptance.py -q -k tonight_show   # 单模板抽查

# V4 音频独立测量（LUFS / 三角色 F0 / A/B 样本）
python tmp/qa/analyze_v4_audio.py            # -> tmp/qa/v4_audio_analysis.json

# 批量自动抽查（后台，模板落盘即自动跑 pytest -k <id>）
python tmp/qa/auto_accept_v4.py --hours 5

# 本报告重生成
python tmp/qa/gen_v4_doc.py
```

## 2. V4 新增硬门（QA 独立口径）

| 硬门 | 判据 | 说明 |
|---|---|---|
| `audio_pace` | qc_report.checks.audio_pace.ok=true；每镜 atempo=1.0、truncated=false；`speech+lead(0.10)+tail(0.30) ≤ 镜头时长`；每镜 duration == `voiced.target_duration(speech)`（H3 17k+5 帧网格向上取整） | 先配音后定镜长，**彻底移除 atempo 加速** |
| voice provider | qc.stats.voice_provider=breeze、speech_driven=true；`work/voice/voice_map.json` provider=breeze，逐镜 atempo=1.0/truncated=false/配音文件存在 | V3 edge-tts 作为回退（自动降级时 QC 会显示） |
| `timeline` | timeline.json version=4、五轨 V1/A1/A2/A3/S1；V1 连续覆盖全片（±0.05s）、clip 数=plan 镜头数；A1 与配音镜一一对应且 at 对齐；A2 全片 BGM 且 duck.enabled；A3 = 转场切点数；S1 = 对话镜数；timeline.html 存在且可读 | OpenFilm 式可检查/可重渲时间线（`python -m pipeline.edit_v4 --timeline …`） |
| loudness | 成片 integrated LUFS ∈ [-17, -15]；true peak ≤ -1.0 dBTP（目标 -1.5） | edit_v4 两遍 loudnorm |
| 混音 ducking | timeline A2 duck.enabled=true + manifest.duck_bgm=true + audio_mode=v4_breeze | 感知级 ducking 效果需人耳试听（QA 只做契约级+统计证据） |
| H3 不回退 | 沿用 V3 硬门（h3=true/workflow/prompt_id 唯一/seed/ref_image/action/blackdetect/字幕/卡片静态标记） | V4 不改变 H3 生成路径 |

## 3. V4 架构落地核对

| 模块/产物 | 落地核对 |
|---|---|
| `pipeline/tts_breeze.py` | ✅ Breeze TTS 2 客户端（health/batch/Voice Design 三角色/回退）（14494 bytes） |
| `pipeline/voiced.py` | ✅ 先配音→按语音自然时长定镜（H3 17k+5 网格 / lead+tail / 长句切分）（21324 bytes） |
| `pipeline/timeline.py` | ✅ OpenFilm 式 timeline.json/html（V1/A1/A2/A3/S1 五轨）（8926 bytes） |
| `pipeline/edit_v4.py` | ✅ timeline 独立 ffmpeg 渲染器（混音/ducking/字幕/两遍 loudnorm）（10223 bytes） |
| `pipeline/qc_v3.py` | ✅ 新增 audio_pace 硬门（atempo=1.0 / 无截断 / 语音装进镜头）（22547 bytes） |
| `pipeline/render_v3.py` | ✅ 默认 V4，`--legacy-audio` 回退 V3（A/B 对照）（53549 bytes） |
| V4 默认 + legacy 回退 | ✅ render_v3 含 `--legacy-audio`；✅ audio_mode=v4_breeze |
| QC audio_pace | ✅ qc_v3 新增 audio_pace 检查 |
| V3 A/B 归档 | ✅ `outputs/v3_legacy_audio/{tonight_show,history_talk}/`（final_v3_edge.mp4 + narration_v3_edge.wav） |
| A/B 试听样本 | ✅ `tmp/v4_voice_ab/AB_round1.wav`（前 3 句 edge / 后 3 句 breeze） |

### 3.1 timeline 独立重渲验证（OpenFilm 式“可检查/可重渲”）

| 项 | 结果 |
|---|---|
| 重渲产物 | `D:\BaiduSyncdisk\wmj_work\XJ\AiJue\tmp\qa\edit_v4_check\tonight_show_rerender.mp4` |
| 规格 | 43.28s / 1280x720 / 25.0fps / audio=True |
| blackdetect | 0 |
| LUFS / TP / LRA | -15.75 / -1.47 / 11.5 |
| 结论 | ✅ 仅用 timeline.json + edit_v4 可重渲出合规成片 |

> 说明：该验证只给 `edit_v4` 喂 `timeline.json`（不重新跑 H3/TTS），验证成片可由时间线单独复原；
> 也证明人工调整 `in/out/at/gain/duck` 后可直接重渲染。

## 4. 参考模板 V4 验收结果（40s 版）

| template | 状态 | 成片 | 镜头(非卡片+卡片) | H3 | voice | atempo | truncated | audio_pace | timeline 轨 | LUFS / TP | QC |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `tonight_show` | ✅ V4 | 43.28s / 1280x720 / 25.0fps / black=0 | 6+2 | 6/6 | breeze | 1.0 | [] | ✅ | V1:8、A1:6、A2:1、A3:6、S1:6 | -15.95 / -1.47 | ✅/✅ fallback=0 |
| `history_talk` | ✅ V4 | 39.542s / 1280x720 / 25.0fps / black=0 | 5+2 | 5/5 | breeze | 1.0 | [] | ✅ | V1:7、A1:5、A2:1、A3:5、S1:5 | -16.77 / -1.72 | ✅/✅ fallback=0 |

> H3 证据链细节（6/6 与 5/5 镜 workflow=05、prompt_id 唯一、seed/ref_image/action 齐全）由
> `tests/test_v4_acceptance.py` 逐模板断言；成片规格 1280×720/25fps/有音轨、blackdetect=0、
> 字幕覆盖 1.0、fallbacks=[]、QC ok+deliverable 均通过。

## 5. 批量 16 模板 V4 验收表

| # | template | 状态 | 成片时长 | 计划 | H3 镜 | voice | atempo | audio_pace | timeline | LUFS | TP | QC |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | `tonight_show` | ✅ V4 | 43.28s | 43.29s | 6/6 | breeze | 1.0 | ✅ | ✅ | -15.95 | -1.47 | ✅/✅ |
| 2 | `shark_tank` | ✅ V4 | 43.08s | 43.08s | 5/5 | breeze | 1.0 | ✅ | ✅ | -16.66 | -1.47 | ✅/✅ |
| 3 | `masked_singer` | ✅ V4 | 37.4s | 37.42s | 5/5 | breeze | 1.0 | ✅ | ✅ | -16.10 | -1.41 | ✅/✅ |
| 4 | `standup` | ✅ V4 | 38.834s | 38.83s | 5/5 | breeze | 1.0 | ✅ | ✅ | -16.09 | -1.47 | ✅/✅ |
| 5 | `taskmaster` | ✅ V4 | 43.793s | 43.79s | 5/5 | breeze | 1.0 | ✅ | ✅ | -16.40 | -1.35 | ✅/✅ |
| 6 | `traitors` | ✅ V4 | 43.08s | 43.08s | 5/5 | breeze | 1.0 | ✅ | ✅ | -16.03 | -1.45 | ✅/✅ |
| 7 | `snl_sketch` | ✅ V4 | 36.709s | 36.71s | 5/5 | breeze | 1.0 | ✅ | ✅ | -16.03 | -1.50 | ✅/✅ |
| 8 | `roundtable` | ✅ V4 | 37.4s | 37.42s | 5/5 | breeze | 1.0 | ✅ | ✅ | -16.38 | -1.46 | ✅/✅ |
| 9 | `hot_ones` | ✅ V4 | 38.12s | 38.13s | 5/5 | breeze | 1.0 | ✅ | ✅ | -16.28 | -1.47 | ✅/✅ |
| 10 | `qipashuo` | ✅ V4 | 40.958s | 40.96s | 5/5 | breeze | 1.0 | ✅ | ✅ | -16.01 | -2.01 | ✅/✅ |
| 11 | `slow_life` | ✅ V4 | 33.16s | 33.17s | 5/5 | breeze | 1.0 | ✅ | ✅ | -16.00 | -1.50 | ✅/✅ |
| 12 | `history_talk` | ✅ V4 | 39.542s | 39.54s | 5/5 | breeze | 1.0 | ✅ | ✅ | -16.77 | -1.72 | ✅/✅ |
| 13 | `history_mystery` | ✅ V4 | 38.12s | 38.13s | 5/5 | breeze | 1.0 | ✅ | ✅ | -16.04 | -1.49 | ✅/✅ |
| 14 | `classics` | ✅ V4 | 36.0s | 36.0s | 5/5 | breeze | 1.0 | ✅ | ✅ | -16.30 | -1.47 | ✅/✅ |
| 15 | `national_treasure` | ✅ V4 | 35.28s | 35.29s | 5/5 | breeze | 1.0 | ✅ | ✅ | -16.17 | -1.42 | ✅/✅ |
| 16 | `mystery` | ✅ V4 | 42.36s | 42.37s | 5/5 | breeze | 1.0 | ✅ | ✅ | -16.09 | -1.50 | ✅/✅ |

- `batch_report.json`：✅ 已生成（covered_total=16，
  coverage_ok=True，failed_ids=[]，missing_ids=[]）
- 未就绪模板 无（批量进行中时该列会显示旧 V3 状态；当前 16/16 已全部为 V4 产物）。
- **LUFS 超范围模板**：无（>范围时属硬门失败，需修复后重测）；
  **qc_report 尚无 loudness check 的模板**：无（新 render/qc 代码或 `pipeline/loudness.py --fix` 后应补齐）。
- **qc ⇄ 独立测量一致性**：16/16 `checks.loudness` 与 QA 独立测量 LUFS/TP 差 ≤0.6dB（实测差 0.00dB；终门 `test_loudness` 强制断言）。

## 6. A/B 数据（语音/剪辑对照）

### A/B 音色数据（Breeze TTS 2 vs V3 edge-tts）

| 角色 | 音色（Voice Design） | 报告参考基频 | QA 独立 YIN 估计（tonight_show 生产配音） | A/B breeze 样本 | A/B edge 样本 |
|---|---|---|---|---|---|
| laozhou | 深沉磁性男声（host） | 82 Hz | 116.3 Hz | 128.0 Hz | 155.1 Hz |
| ali | 明亮清脆女声 | 164 Hz | 266.4 Hz | 279.7 Hz | 213.8 Hz |
| dawei | 沉稳沙哑男声 | 104 Hz | 92.0 Hz | 130.0 Hz | 99.1 Hz |

> 说明：报告参考基频为 Lead/V4 报告值；QA 用 YIN（自相关差分 + 累积均值归一化）做独立复核，两种方法在 TTS 语调起伏/谐波结构上会有系统差异（YIN 对部分句子会拾取谐波或半频），**绝对音高以人耳试听为准**，但 QA 复核的相对关系成立：ali 基频显著高于 laozhou/dawei。

### atempo / 截断 / 响度（V3 vs V4）

| 指标 | V3（edge-tts） | V4（Breeze TTS 2 + 语音驱动） |
|---|---|---|
| atempo | 1.0–1.36×（长句加速塞进固定 5.17s，见 PLAN_V4/V4_REPORT） | **1.0**（qc.stats.atempo_max=1.0，voice_map 逐镜 atempo=1.0，QA 已核） |
| 截断 | 旧版 s001 约 1.3s 被截断 | **0**（qc.stats.truncated_shots=[]，逐镜 truncated=false） |
| 镜头时长 | 模板固定 5.17s/镜 | 语音自然时长 → H3 17k+5 网格向上取整（+0.10 lead / +0.30 tail） |
| 成片 LUFS（tonight_show） | -16.12 | -15.95（TP -1.47 dBTP） |
| 成片 LUFS（history_talk） | -16.07 | -16.77（TP -1.72 dBTP） |

### 试听样本

- `tmp/v4_voice_ab/AB_round1.wav`（31.47s；前 3 句 edge-tts、后 3 句 Breeze，同文本）；自动切出 17 个语音段
- 分角色样本：`tmp/v4_voice_ab/{breeze,edge}_{laozhou,ali,dawei}_*.wav`
- V3 原片/旁白：`outputs/v3_legacy_audio/{tonight_show,history_talk}/final_v3_edge.mp4` + `narration_v3_edge.wav`

## 7. 遗留问题与风险

1. **F0 测量方法差异**：报告参考值 82/164/104 Hz 与 QA YIN 独立估计存在系统差（语调/谐波），绝对音高以人耳试听为准；相对区分（ali 显著高于两位男声）已复核成立。
2. **ducking 感知验证**：timeline/manifest/渲染链路已确认启用（sidechaincompress），实际听感强度需 Lead/用户试听确认。
3. **批量期间 V3 验收套件可能瞬时失败**：`pytest tests/test_v3_acceptance.py` 在模板正被 V4 覆盖的瞬间可能命中半新半旧文件（如 episode 已新、final 仍旧）；批量完成后需重跑全量回归。
4. **时长口径**：V4 为语音驱动时长（`trim_to_target` 控制在目标 ±15%），验收按 `work/episode.json` 实际 plan 时长核对 ±0.6s，不套用固定 30-45s。
5. **渲染成本**：H3 单镜变长（最长约 15.08s），GPU 串行时间高于 V3；批量 14 模板约 3-4h。
6. **TTS 许可**：Breeze TTS 2 权重与自托管输出为 research / non-commercial，本仓库 demo 用途；商用需 BreezeBlue 授权。
7. **TTS 回退**：若 Breeze 服务不可用自动回退 edge-tts，此时 audio_pace 的 provider 会变成 edge、speech_driven 标记需复查；验收硬门要求 provider=breeze。
8. **批量早期 loudnorm 欠调（已全量修复）**：批量前段产物有 9 个模板欠调（classics -18.15 / hot_ones -17.53 / mystery -17.13 / national_treasure -17.28 / roundtable / shark_tank / snl_sketch / standup / taskmaster），根因是旧 `final_encode` 用单遍 loudnorm（PLAN 要求两遍）。已闭环：render-dev 改为两遍 + 编码后闭环校正并给 qc_v3 加 `loudness` check；Lead 两轮 `scripts/fix_loudness.py --root outputs/templates_v3 --fix`（首轮修 classics/hot_ones/mystery，末轮 fixed=6 / skipped_ok=10 / failed=0）。**最终 16/16 实测 ∈ [-16.77, -15.95]，TP ≤ -1.35 dBTP，qc_report 16/16 loudness check ok，QA 独立测量与 qc 差 ≤0.6dB。**
9. **生产渲染尚未消费 timeline（双实现漂移风险）**：`render_v3` 的 V4 路径未调用 `edit_v4.render_timeline`（全仓 grep 仅 edit_v4 自身引用），生产成片仍走旧 assembly + `final_encode(duck_bgm)`，timeline.json/html 目前是**描述性产物**；QA 手工只用 timeline 重渲验证渲染器可用（tonight_show `tmp/qa/edit_v4_check/` 43.28s/1280×720/25fps/black=0/**-15.75 LUFS** ✅），但 timeline A2 `gain=0.22` 未被 final_encode 消费（生产用 `config.BGM_VOLUME=0.075`）。若要求“编辑 timeline 后重渲=生产成片”，需 wiring 或在文档注明为描述性时间线。
10. **5min 版未按 V4 重渲**：`outputs/templates_v3/tonight_show_5min/` 仍是 V3-era 产物（无 timeline/audio_pace/loudness check），**不纳入 V4 16/16 终门**；如需 V4 5min 版需重跑（GPU）。归档目录 `tonight_show_40s_v1` 同为 V3 版本归档，不参与 V4 终门。
11. **响度修复备份**：16 个模板目录各保留 `final.preloud.mp4`（合计 171.4MB）用于回滚；**不影响 QA 目录白名单测试**（`tests/test_v3_acceptance.py::test_unexpected_output_dirs` 只扫目录/预期 id，备份是文件；全量 V3 验收 90 passed 已覆盖），终验通过后由 render-dev 清理。

## 8. 最终回归记录（批量完成后更新）

- `python -m pytest tests/test_v4_acceptance.py -q`：100 passed / 0 skipped（16/16 模板 × 硬门 + 批级终门）
- `python -m pytest tests/test_v3_acceptance.py -q`：90 passed / 0 skipped（V4 产物对 V3 硬门回归）
- `python -m pytest tests -q`：427 passed / 0 skipped
- `python -m pytest pipeline/tests -q`：164 passed

## 9. 证据路径索引

| 证据 | 路径 |
|---|---|
| V4 成片 | `outputs/templates_v3/{id}/final.mp4` |
| V4 时间线 | `outputs/templates_v3/{id}/timeline.json`、`timeline.html` |
| 语音映射/证据 | `outputs/templates_v3/{id}/work/voice/voice_map.json`、`work/voices/streamb_*.wav` |
| QC 报告（含 audio_pace） | `outputs/templates_v3/{id}/qc_report.json` |
| H3 证据链 | `outputs/templates_v3/{id}/h3_meta.json` |
| 批量报告 | `outputs/templates_v3/batch_report.json` |
| V3 A/B 归档 | `outputs/v3_legacy_audio/{tonight_show,history_talk}/` |
| A/B 试听样本 | `tmp/v4_voice_ab/AB_round1.wav`、`tmp/v4_voice_ab/{breeze,edge}_{角色}_*.wav` |
| QA 音频测量 | `tmp/qa/v4_audio_analysis.json`（LUFS / F0 / A/B 分段） |
| 批量自动抽查 | `tmp/qa/auto_accept_v4.log`、`tmp/qa/accepted_v4.json` |
| 验收测试 | `tests/test_v4_acceptance.py` |
| V3 基线报告 | `docs/V3_ACCEPTANCE.md` |
