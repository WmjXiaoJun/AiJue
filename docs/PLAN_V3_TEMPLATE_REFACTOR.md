# 规划：模板 v3 重构 —— 让每个模板真正“进入场景”，全部镜头走 H3/ComfyUI

## 0. 先回答两个问题
### H3 经过 ComfyUI 了吗？
- **动态镜头：是。** integrations/comfyui/run_h3_ref.py 会把 H3 任务提交给本机 ComfyUI（127.0.0.1:8188），使用工作流 04_i2v_fl2v_4step_sla.json（首尾帧）与 05_ref2va_4step_sla.json（音驱动口型）；ep_001/002 动态版、shotplan demo、tonight_show_3d、全 H3《历史夜话》都是这样生成的，ComfyUI 有完整 prompt/history 记录。
- **但最近这批 16 个 style_v2 / llm_demos 没有走 H3**：它们是静态合成（SDXL 场景 plate + 2D 角色 cutout + ffmpeg 调色/包装/转场），只有 H3 demo 和动态版才经过 ComfyUI。所以看到的“平面感、都像一个模板”主要来自静态 demo 链路。

### 为什么现在感觉“都是一个模板、任务没进场景”？
1. 模板只定义了 segment 名称/时长/镜头 type 循环，没有舞台空间、角色站位、道具、动作、走位、镜头调度；
2. 所有模板共用同一套 host 居中 + 嘉宾左右的构图和样例台词；差异只在场景 plate 和调色；
3. LLM 台本只输出「谁说什么」，没有 [动作]/[走位]/[道具] 舞台提示，分镜自然落不到场景里；
4. 成片默认静态合成，H3 只在少数 demo 打开；H3 prompt 也缺少“在场景里的动作/调度”描述。

## 1. 重构目标
- 模板 = 节目形态：舞台/布景/角色定位/道具/动作库/镜头语言/包装/声音，不再只是“皮肤”；
- 每一镜都进场景：角色有站位、有动作、和道具/环境发生关系；镜头有调度（推拉/横移/环绕/过肩/空镜）；
- 默认 H3 生成：每镜走 ComfyUI H3（首帧=场景合成帧，prompt 含舞台调度），静态合成只作为 previs/兜底；
- 16 个模板形态不同：cast 角色不同、舞台不同、动作库不同、镜头语言不同，LLM 内容按模板生成。

## 2. Template v3 数据结构
contract/templates_v3/{id}.json：
```json
{
  "id": "tonight_show",
  "label": "美式深夜秀",
  "style": "cinematic_3d",
  "stage": {
    "type": "3d_set|2d_plate|h3_scene",
    "plate": "late_night_desk__cinematic",
    "layout": {"desk": [0.30,0.62], "sofa": [0.68,0.58], "window": [0.50,0.20], "ground_y": 0.86},
    "props": ["cue_cards", "coffee_mug", "monitor", "lamp"],
    "camera_zones": ["front", "desk_swap", "sofa_ots", "window_wide"]
  },
  "cast": [
    {"role":"host","character":"laozhou","block":"desk_center","props":["cue_cards"]},
    {"role":"guest_a","character":"ali","block":"sofa_right","props":["coffee_mug"]},
    {"role":"guest_b","character":"dawei","block":"sofa_left","props":["script"]}
  ],
  "segments": [
    {"id":"cold_open","name":"开场独白","duration":35,
     "beats":["host 从窗边走向 desk","host 拿起 cue cards","看向 guest 席"],
     "shots":["wide 舞台 establishing","medium host 走位","close cue cards"]},
    {"id":"guest_intro","duration":55,"beats":["guest 入座","host 递话筒"]},
    {"id":"roundtable","duration":80,"beats":["三人对谈","道具互动","反应镜头"]},
    {"id":"closing","duration":30,"beats":["host 收束","起身","灯暗"]}
  ],
  "camera_language": {"default":"slow dolly-in","wide":"truck left","reaction":"static"},
  "packaging": {"lower_third":"zaopianju_yellow","transition":"fade","bgm":"talk_light"},
  "h3": {"workflow":"05_ref2va_4step_sla","duration":[4,8],"fps":25}
}
```
- beats 是叙事节拍，shots 是镜头意图；生成时由 storyboard 把 beat 展开成镜头（含 action/走位/prop）；
- stage.layout 给合成器/首帧构图用；cast.block 给角色站位/遮挡/景别；
- camera_language 与 h3 指定镜头如何动、用哪个 ComfyUI workflow；
- 所有模板必须填 stage/layout/cast/props/beats，缺一不可（校验器强制）。

## 3. 代码重构（新会话执行）
| 模块 | 职责 |
|---|---|
| contract/templates_v3/*.json | 16 个模板 v3 定义（舞台/角色位/道具/beat/镜头语言/H3 配置） |
| pipeline/template_v3.py | 加载 + 校验（必须字段、cast/style 合法、timeline 总长、shot 覆盖） |
| pipeline/stage.py | 场景几何：plate/3D 资产、ground_y、角色 bbox、站位/遮挡/前景层、相机 zone |
| pipeline/action_library.py | 动作库：walk_in / pick_prop / sip / point / react / turn / sit / handoff，映射到 H3 prompt |
| pipeline/storyboard_v3.py | LLM 台本（含 [动作]/[走位]/[道具]）→ beats → shots → ShotPlan + H3 prompt + 首帧合成参数 |
| pipeline/h3_comfy.py | ComfyUI 客户端：提交 04/05 workflow、轮询 history、下载视频、记录 prompt_id/workflow/seed；每镜必须留下 comfy 证据 |
| pipeline/render_v3.py | 场景首帧合成 → H3 每镜 → 剪辑/转场/包装/混音 → QC；静态合成仅 previs/兜底 |
| backend | 模板 v3 API/校验；项目创建用 v3 模板；LLM 台本支持动作提示 |
| frontend | 模板选择展示 stage/cast/动作差异；分镜台显示每镜 action/comfy 状态 |

## 4. H3/ComfyUI 验收（关键）
- 每镜 meta 必须含：workflow、prompt_id、seed、duration、ref_image、action；
- 成片 QC 检查：所有非卡片镜头 h3=true、ComfyUI prompt 可追溯、身份/场景一致；
- 不允许“静态 cutout 冒充成片”；静态只允许作为 previs 或 H3 失败兜底并在 QC 标记；
- ComfyUI 地址/工作流：http://127.0.0.1:8188，04_i2v_fl2v_4step_sla.json、05_ref2va_4step_sla.json；
- H3 输入首帧 = stage 合成帧（角色站位 + 道具 + 场景），prompt 写清 action/走位/镜头；输出 1280×704 或 768²，按模板适配。

## 5. 16 模板重构矩阵（示例，需全部重写）
| 模板 | 舞台 | cast/站位 | 动作库/镜头语言 |
|---|---|---|---|
| tonight_show | 3D 深夜秀办公桌+沙发+窗 | host desk；guest 沙发左右 | 走位入场、cue card、喝咖啡、推拉+过肩 |
| taskmaster | 3D 彩色任务舞台+奖杯台 | host 台侧；guest 任务区 | 抢道具、看计时器、举牌、手持跟拍 |
| masked_singer | 3D 霓虹舞台+幕布 | host 侧台；歌手舞台中央 | 揭面、灯光扫、环绕、升降 |
| roundtable | 3D 圆桌演播厅 | 三人各占圆桌 120° | 转身、递话筒、俯拍环绕 |
| snl_sketch | 3D 剧院舞台+客厅布景 | 角色进出布景 | 开门、摔道具、走位、固定机位 |
| slow_life | 3D 厨房/院子 | 两人分站灶台/餐桌 | 切菜、端菜、坐下、手持近景 |
| history_talk | 2D 水墨书房 | host 书案；两位历史人物左右席 | 展卷、点烛、指地图、缓慢推轨 |
| qipashuo | 2D 国风辩论台 | 正反方分列 | 起身、拍案、摇扇、快速切 |
| national_treasure | 2D 金彩展厅 | host 展柜旁；嘉宾对面 | 开柜、举灯、俯身查看、环绕展品 |
| mystery | 3D 黑色电影案发现场 | 侦探/嫌疑人分站 | 掀布、照手电、踱步、百叶窗影子 |
| 其余 6 个 | 各自舞台 | 各自 cast/站位 | 各自动作库 |

## 6. 迁移阶段（新会话按此执行）
| 阶段 | 内容 | 验收 | 估算 |
|---|---|---|---|
| P0 | 冻结旧模板；落盘 templates_v3 schema + 16 模板 JSON + 校验器 | 16/16 校验通过（stage/cast/beats 必填） | 1 天 |
| P1 | stage 首帧合成 + H3 ComfyUI 每镜生成 + render_v3；先做 tonight_show / history_talk | 2 个模板各 30-45s 全 H3 走 ComfyUI，meta 可追溯 | 2-3 天 |
| P2 | 补齐 16 模板；LLM 台本带动作提示；批量生成 30-45s 全 H3 demo | 16/16 全 H3 且画面/内容/舞台不同；QC 通过 | 5-7 天（GPU 串行） |
| P3 | 重点模板完整 5 分钟；包装/调色/音频分层/多语；发布链路 | 2 个 5 分钟级成片 + QC | 3-4 天 |

## 7. 新会话执行清单（Runbook）
1. 环境：ComfyUI 8188 启动、H3 工作流文件存在、Ollama 11434 或 DeepSeek key（llm.env）；后端 8090；
2. 先跑校验：python -m pipeline.template_v3 --validate contract/templates_v3；
3. 资产：每个模板 1 套场景 plate/3D 首帧 + 角色 cutout；缺则先补（SDXL/H3，不用 Blender）；
4. 单模板跑通：python -m pipeline.cli render_v3 --template tonight_show --duration 45 --h3 comfy；检查每镜 comfy prompt_id；
5. 批量：16 模板全 H3 demo（GPU 串行，估算每模板 8-12 镜 × 2-4 分钟 ≈ 30-50 分钟/模板）；
6. QC 门：每镜 h3=true、身份一致、场景连续、blackdetect=0、时长/字幕/包装；
7. 输出规范：outputs/templates_v3/{id}/final.mp4、shots/、h3_meta.json、qc_report.json；
8. 注意：H3 尺寸需 32 倍数（1280×704/768²）；fl2v 首尾帧要同尺寸；长任务放后台；磁盘监控。

## 8. 风险与对策
- H3 慢（2-4 分钟/镜）：按模板优先级排队，后台串行；静态 previs 先给导演看构图；
- H3 身份漂移：每镜 reference 用同角色 cutout/概念图 + 5-aspect 身份锚点；关键镜头 fl2v 首尾帧控制；
- 场景不一致：首帧来自同一 stage plate/合成器，H3 prompt 锁背景；wide 镜头单独首帧；
- 时长/成本：模板先 30-45s demo，验收后再做 5 分钟；
- 旧静态链路保留为 fallback，但成片默认必须 H3（QC 强制）。

## 9. 结论
- 这轮先交付“重构蓝图 + v3 schema + 2 个参考模板 + runbook”；
- 新会话直接按 P0→P2 执行，不再沿用旧的「统一 host 居中 + 静态合成」模板。
