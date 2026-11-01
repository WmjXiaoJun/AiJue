# V14：全站 UI 审计 + 视频生成逻辑审计 + 角色工坊 v2 + 旧成片清理

> 用户反馈：UI 是否有冲突；生成的视频逻辑是否正确；最早成片不要了；角色改进；页面操作要一套完整的；声音与口型对不上；画面与文字不匹配。
> 本步：审计 + 修复 + 试点验证。**待用户确认后再做 16 模板全量重生成（约 3.5–4h GPU）**。

## 1. UI 一致性审计与修复

审计覆盖：项目大厅 / 工作台 6 tab / Cinema 6 页 / V5 世界 6 页 + 设置、新建项目、Toast、Boot、ErrorBoundary，1680/1280/860。

**发现的冲突**
| 级别 | 问题 | 状态 |
|---|---|---|
| P1 | 设置弹层仍是旧「AI 微综艺」蓝黄霓虹风（独立旧 token/层级 z-index 60） | ✅ 重做为 `cm-set-*` 三区分区（生成与计价 / 海外发布 / AI 模型），业务逻辑原样保留，ESC 关闭 |
| P1 | Toast 用旧配色、z-index=90 与新弹层同层会被盖 | ✅ 重做 `cm-toast`（左色条/z-index 220），逻辑不变 |
| P1 | BootScreen / ErrorBoundary 仍是旧设计 | ✅ 重做 `cm-boot-*` / `cm-crash-*` |
| P1 | 旧 CSS（layout/views/v5.css 共 115KB）已无页面使用但仍全量加载，且 `main.tsx` 先 import App 再加旧 CSS → 同权重后置覆盖新设计 | ✅ 删除三文件 + 重排 import 顺序；CSS 313KB → 250KB |
| P1 | 旧组件定义与新设计 12 处类名重叠（`.is-error/.is-empty/.is-selected/.is-dirty/.is-locked/.is-ready/.is-ok/.is-missing/.is-cloud/.is-local/.is-compact/.v5-film-card`）+ 旧文件 body/button/a 全局规则 | ✅ 旧文件已删，仅 `base/components.css` 保留给 Spinner/基础 reset |
| P2 | `body` 未锁滚动 → 全屏外壳出现双滚动条/滚动穿透 | ✅ `html,body,#root{height:100%}; body{overflow:hidden}` |
| P2 | 旧 2D 成片入口（V5 画廊「切换 2D 原版」/「全部成片(18)」/ KPI「2D 原版 16 项硬门」） | ✅ V5 画廊 3D-only（只播 `/media/templates_3d/*`、KPI 16/16 3D、120/120 镜头、232MB；缺 3D 显示待生成占位，不回退 2D） |
| P2 | index.html 旧品牌「造片局 · AI 微综艺」 | ✅ 改「造片局 · AI 视频制片 / 3D 电影制片工作台」 |
| P3 | `components.css` 仍有重复定义、旧不可达组件文件（v5/*、V5Studio、旧 Sidebar/TopBar 等）仍在仓库 | 保留（无引用/无可见冲突），建议 V15 清理 |

证据：`tmp/v14/UI_AUDIT.md`、`tmp/v14/ui_before/`（86 张）、`tmp/v14/ui_after/`（77 张）、`tmp/v14/ui_after/02_settings_modal.png`、`tmp/v14/studio_flow.png`。

## 2. 视频生成逻辑审计（结论：两处真问题）

详细报告：`tmp/v14/PIPELINE_AUDIT.md`（含抽帧/人脸/数据 JSON）。

1. **口型对不上（3D 模板链路）**：画面用 H3 **纯文生视频 T2VA**（workflow `04` 无音频输入，prompt 还写死 "No dialogue, no human voices"），配音是另一条旧 TTS 音轨，后期只做混音；H3 联生成的音轨被丢弃 → 嘴部动作与声音**没有因果关系**。2D 链路反而有真口型机制（`mouth_sync.envelope_timeline` 用 TTS 音频能量驱动 cutout 嘴型），但 2D 老片已下线/归档。
2. **画面与文字不匹配（3D 模板链路）**：`pipeline/templates3d.py::build_briefs` 只用「line 是否为空」选一条**模板级通用动作**，台词语义从未进 prompt —— 抽样 120/120 镜 prompt 无 CJK、无台词语义、每镜写死 "No dialogue"，且无角色参考图（同模板 6 连镜人物/服装全变），字幕却烧真实台词。
3. **时间轴无系统性错位**：3D 重合成实测音轨逐镜对齐 `_edit_start`（相关 0.88–0.99）；旧 2D V5 成片曾有 narration 比画面超前 ~2s 的历史 bug（旧代码已修，相关老片已归档）。

**能力早已就绪**：`pipeline/lipsync.py`（MuseTalk 1.5，`available=true`，本地 6.85GB 模型、48–52ms/帧稳态）从未被调用；H3 `05_ref2va` + `ref_audios`（音频驱动）也可用，仅旧 dynamic 链用过。

## 3. 已实施试点（今晚实跑，可直接对比看）

### A. 语义画面（解决「画面与文字不匹配」）
新增 `pipeline/templates3d_semantic.py`：台词 → LLM（DeepSeek）转英文电影画面描述 → 组合 3D prompt 重新生成。
- 今晚 `tonight_show` 9 镜全部重生成，示例：`「别怕 AI，先怕自己写的稿子像 AI」→ 主持人举起会变成机器面具的纸稿`；`「凌晨三点还能给我写十个开场白」→ 后台机器人连夜打印台词`；`「观众半夜打开电视…是有人陪」→ 雨夜客厅独坐观众 + 屏幕暖光`。
- 成片：`outputs/templates_3d/tonight_show/final.mp4` 41.61s / 17.7MB；帧序列见 `tmp/v14/semantic_tonight_sheet.png`。

### B. 真口型同步（解决「声音与口型对不上」）
- 在语义版基础上，对 7 个对话镜跑 MuseTalk：**44 / 30 / 27 / 31 / 25 / 27 / 23 秒**（模型加载后 ~25–45s/镜），全部 `ok`（人脸检测通过）。
- 重新剪辑后的成片即当前 `tonight_show` 3D 成片；对比片（无口型版）另存 `outputs/templates_3d/tonight_show/_compare_semantic_only.mp4`（对应 `/media/templates_3d/tonight_show/_compare_semantic_only.mp4`）。
- 备选 H3 ref2va（音频驱动原生口型）实测：8.2s 镜 **224s**、1024×576、构图会随动画漂移 → 更慢且可控性差，**推荐 MuseTalk 后处理**。

## 4. 最早成片已清出（用户要求）

- 把 17 个旧 **2D** 成片移到 `outputs/_archive_v5_2d_finals/`（16 模板 + relic_talk；含 `manifest.json`，未删除，可随时找回）。
- V5 世界前端全部 3D-only：不再有「切换 2D 原版」、不再显示 18 部成片；`/media/templates_v5/<id>/final.mp4` 已无任何前端引用。
- 保留：`relic_talk_3d` 3D 成片、各期 `outputs/episodes/<eid>/final.mp4`（工作台成片房/发布用），以及 2D 链路的音频/工作目录（3D 模板剪辑复用其 TTS）。

## 5. 角色工坊 v2（已升级）

`studio/Assets.tsx` 重写角色区（+1325 行）：
- 分区筛选「全部 / 已锁定 / 有概念图 / 待生成」+ 搜索 + 排序；角色卡新增 2D/3D badge、概念完成度 3/3、声线、锁定、上次生成、hover 快捷操作。
- 角色工作台抽屉 4 tab：**概念图**（立绘/三视图/表情/联系表 + lightbox + 设为头像）、**生成**（模式多选 + 风格 + 附加 prompt + job 进度）、**H3 动画**（已有视频播放 / 重新生成，价格与耗时确认）、**角色资料**（name/title/persona/voice/accent 编辑 + spec 摘要）。
- 新建角色 → 直接打开抽屉并落在「生成」tab；空项目/空态/错误态/重试齐全。
- 证据：`tmp/v14/characters/`（17 张）。

## 6. 「页面操作是一整套」——新增制片流水线导航

Studio 底部新增固定流水线条：`01 编剧间 → 02 资产库 → 03 分镜台 → 04 生成台 → 05 成片房 → 06 3D 模板`，已完成的步骤显示绿色、当前步骤金色，右侧「← 上一步 / 下一步：xxx →」，最后一步给「去成片画廊 →」。这样用户在任何一页都知道自己在整条流程的哪一步、下一步去哪。

## 7. 16 模板全量重做方案（待确认）

| 方案 | 内容 | GPU 时间 | 效果 |
|---|---|---|---|
| **推荐：语义 + MuseTalk 全量** | 16 模板 × 平均 ~7.5 镜语义重生成（~2.5–3h）+ 97 个对话镜 MuseTalk（~40–60min） | **≈ 3.5–4h**（一次夜间跑完） | 画面贴台词 + 对话镜头口型跟音频 |
| 只做语义 | 同上去掉 MuseTalk | ≈ 2.5–3h | 画面贴台词，口型仍不同步 |
| 先做 4 个重点模板 | 选 tonight_show/classics/masked_singer/roundtable | ≈ 50–60min | 先看效果再决定其余 |
| 角色一致性增强（长期） | 锁定角色立绘 + 参考图条件（ref2va/IPAdapter）+ 固定 seed | +2–4 天开发 | 同模板跨镜人物一致 |

命令（Lead 已封装）：
```powershell
# 单模板：语义重生成 + 每镜口型 + 剪辑
python -m pipeline.templates3d_semantic --template tonight_show
# 仅对已有镜头补口型并重新剪辑
python -m pipeline.templates3d_semantic --template tonight_show --lipsync-only
```

## 8. 遗留 & 建议

1. 等用户确认是否全量重跑（建议今晚一次跑完，我明天早上交付 16 支新成片 + 对比）。
2. 角色一致性目前是「同一角色描述 + 概念图」，跨镜仍会换脸；要彻底解决需参考图条件（见方案表 C）。
3. 老 2D 链路代码与旧 V5 面板仍在仓库（V5 世界已全 3D-only），可后续整体删除；`components.css` 重复样式 V15 清理。
4. Upload-Post 等用户连接社交账号后再回来做（真实发布）。


## 9. 16 模板全量重做：已完成（2026-11-02 02:04）

> 用户确认「全量跑」。tonight_show 作为试点先完成（21:38），其余 15 个模板用三阶段流水线（`pipeline/templates3d_pipeline.py`：A 全部生成 → B 统一口型 → C 剪辑+海报）跑完。

**结果 16/16 全部重建：语义画面 + MuseTalk 口型（97 个对话镜）+ 重新剪辑 + 新海报**

| 模板 | 时长 | 大小 | 口型镜 | 模板 | 时长 | 大小 | 口型镜 |
|---|---|---|---|---|---|---|---|
| tonight_show | 41.61s | 16.9MB | 7 | classics | 42.25s | 19.1MB | 7 |
| history_talk | 36.13s | 11.8MB | 5 | masked_singer | 41.70s | 20.6MB | 6 |
| roundtable | 41.78s | 13.7MB | 6 | national_treasure | 41.06s | 13.0MB | 6 |
| hot_ones | 38.58s | 12.9MB | 6 | mystery | 34.82s | 9.7MB | 6 |
| qipashuo | 35.72s | 12.4MB | 6 | shark_tank | 31.51s | 14.1MB | 5 |
| slow_life | 35.74s | 13.0MB | 6 | snl_sketch | 38.53s | 12.1MB | 6 |
| standup | 41.31s | 14.0MB | 6 | taskmaster | 32.66s | 13.0MB | 6 |
| traitors | 37.92s | 14.7MB | 6 | history_mystery | 40.83s | 13.8MB | 7 |

全部 **1280×720 / 25fps / H.264+AAC**，时长与旧版一致；合计 224.8MB、97 个对话镜完成口型同步。

- 校验脚本：`tmp/v14/verify_final.py` → 16/16 ok（时长/分辨率/音轨/海报/口型镜数）；16 帧总览 `tmp/v14/16_templates_v14_montage.png`；旧 vs 新对比 `tmp/v14/before_after_v14.png`（旧版备份在 `outputs/_archive_v14_before_semantic/`）。
- 模板库页面：`tmp/v14/templates_v14_done.png`（16/16 已完成、海报全部更新）。
- 执行插曲：ComfyUI 偶发在模型动态加载时长时间卡住（5–15 分钟无 compute），已重启 ComfyUI（UTF-8 环境变量避免日志 emoji 崩溃）并改用三阶段流水线，避免每模板重复卸载/重载模型；总耗时约 4.5 小时（含两轮卡顿和重启）。
- 语义重生成数据：`contract/v5/cinema3d/templates3d/<id>_semantic.json`（含每镜台词 → 英文画面描述映射）。

### 后续可继续（未做）
1. 角色一致性长期方案（参考图条件/IPAdapter，2–4 天开发）。
2. 旧 2D 链路代码/旧 V5 面板/`components.css` 冗余清理（V15）。
3. Upload-Post 真实发布（等用户连接社交账号）。
