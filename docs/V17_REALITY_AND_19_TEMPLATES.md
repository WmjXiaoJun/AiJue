# V17：19 模板体系（16 高清重做 + 3 写实新模板）— 执行中

> 用户：可以，全量跑；注意内容和场景；UI 操作逻辑；再加几个真实的人物场景模版。
> 本文件记录执行状态与已完成部分；全部完成后会补最终数据表/截图。

## 1. 已完成（数据/平台层）

### 3 个写实新模板（内容层，studio-a 产出）
| 模板 | 设定 | 角色 | 镜数 / 时长 |
|---|---|---|---|
| `real_street` 城市夜访 | 夜间城市街头采访（霓虹街/地铁口/便利店） | 沈舟(主持 38) + 陈野(27) + 李静(34) | 7 镜 / 35.2s |
| `real_cafe` 咖啡对谈 | 白天咖啡馆（窗边木桌/吧台/沙发） | 沈舟 + 周岚(32) + 高远(45) | 7 镜 / 33.8s |
| `real_kitchen` 深夜食堂 | 夜晚家庭厨房（灶台/餐桌/暖灯） | 沈舟 + 老赵(48) + 阿凯(26) | 7 镜 / 33.6s |

- `contract/v5/templates/real_*.json`、7 个 `contract/v5/characters/real_*.json`、9 个 `contract/v5/scenes/*.json`（schema 全过）
- `outputs/templates_v5/real_*/work/episode.json` + 每镜 TTS 24k mono wav（对白）与静音（b-roll）
- 语义 brief：`contract/v5/cinema3d/templates3d/real_*_semantic.json`（台词 → 英文画面描述，含景别/运镜）
- 内容文档：`docs/V17_REAL_TEMPLATES_CONTENT.md`

### 平台（19 模板）
- 后端 `cinema_templates.py`：模板 id 动态发现（pipeline/contract/桥接），19 模板 list/status/detail/generate/assemble 全支持；pending 新模板容错、海报/label/style 回退
- 前端：模板库/首页/V5 世界/工作台 3D tab 数量全动态（19、写实 3 档筛选、写实徽章、pending 占位与抽屉、操作状态机）
- 验证：`tsc` 0 error、`vite build` 通过；截图 `tmp/v17/integration/templates_19.png`、`home_19.png`

### 3D 流水线准备
- `pipeline/templates3d.py`：TEMPLATES 扩到 19；STYLE3D 加 3 个写实 look（photorealistic cinematic live-action）与场景描述；`character_desc(realistic=True)`
- `templates3d_semantic.py`：写实 look 支持（画质词、角色描述去卡通化）
- `templates3d_hq.py`：1024×576 T2VA + 自定义运镜提示的 ref2va（解决「没运镜/没远景」）
- `templates3d_hq_batch.py`：15 个待重做模板的批处理（美式深夜秀样片已完成）
- `templates3d_new_templates.py`：写实模板生成（语义 brief → T2VA 1024 + ref2va → 剪辑 + 海报 + progress）
- **写实模板 assemble 冒烟测试通过**（占位镜头）：final 33.844s、字幕 ass 与 BGM 生成正常

## 2. 正在跑（GPU 串行）

| 任务 | 状态 | ETA |
|---|---|---|
| 16 模板高清全量（15 个待跑） | 🟡 05:26 开始 classics | ~17:00–19:00 |
| 3 个写实模板 3D 生成 | ⏳ 队列已挂，等全量批结束自动开始 | 完成后 +2.5h |

## 3. 后续（全部完成后）

1. 校验 19 支 final（时长/分辨率/音轨/海报）+ 抽帧
2. 更新模板库截图（19 全 done）
3. 数据表 + 对比图写入本文档
4. commit，向用户汇报


## 4. 全量进度（滚动更新）

| 模板 | 状态 | 耗时 | 成片 |
|---|---|---|---|
| classics | ✅ done | 36.4 min | 42.25s / 18.1MB / 1024 源 |
| history_talk | ✅ done | 35.4 min | 36.13s / 13.1MB / 1024 源 |
| roundtable | ✅ done | 44.4 min | 41.78s / 15.1MB |
| masked_singer | ✅ done | 27.2 min | 41.70s / 19.9MB |
| national_treasure | ✅ done | 26.9 min | 41.06s / 15.4MB |
| hot_ones | ✅ done | 32.3 min | 38.58s / 14.0MB |
| mystery | ✅ done | 31.5 min | 34.82s / 12.2MB |
| qipashuo | 🟡 跑中 | - | - |
| mystery 起其余 9 个 | ⏳ 排队 | - | - |

- 单模板实测 ~35–40 分钟（1024×576 T2VA + 7~9 个对话镜 ref2va + 剪辑海报）
- 按实测推算 15 个全量 ≈ 9–10 小时 → 预计 **16:00–17:30** 完成；随后补跑 masked_singer + 3 个写实模板 ≈ 2–2.5 小时
- 对比样张：	mp/v17/compare/cmp_classics.png（上=旧 MuseTalk 768，下=HQ 1024）


## 5. 性能问题与修复（2026-11-02 上午）

- 现象：1024×576 生成时单镜从 ~4 分钟恶化到 10–21 分钟，ComfyUI GPU CUDA 无占用、卡在 VideoVAE decode（日志停在 "VideoVAE prepared for dynamic VRAM loading … Staged"）。
- 根因：12GB 显存下 20GB H3 模型 + 5GB VideoVAE 动态加载，默认 smart-memory 让权重长期驻留显存，VAE decode 阶段显存不足触发 WDDM 分页抖动。
- 修复：ComfyUI 启动参数改为 `--disable-smart-memory --reserve-vram 0.5`（每 prompt 结束把权重卸载回内存、保留 0.5GB 显存余量）。实测 1024×576/124 帧 T2VA 从 5–21 分钟降到 **114 秒**，随后单镜稳定 ~4–6 分钟。
- 附带：清理两个重复启动的批处理进程；`masked_singer` 因重启中断进入补跑队列（post-batch 编排器负责补跑失败模板 + 生成 3 个写实模板）。
