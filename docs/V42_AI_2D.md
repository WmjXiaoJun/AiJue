# V42：2D 动画改为 AI 生成（修复 2D 效果）

> 用户反馈「2d的效果不行」：V5 快剪 2D（render_v5 纸片/抽象背景/PPT 感）观感差。
> 方案：2D 类型改用 **H3 AI 视频 + 2D 动漫提示词**（cel-shaded/flat colors/line art），保持原模板分幕、TTS、字幕、运镜；生成耗时与 3D 同档（约 15–40 分钟）。V5 快剪保留为内部 legacy，不再作为节目类型使用。

## 1. 生成管线（pipeline/templates3d_hq.py，本轮允许修改）
- 新增 `--style-mode {3d,2d}`（默认 3d）
- 2D 模式改造提示词：
  - T2VA shot prompt：替换/前置 2D 动漫描述——`2D anime, cel-shaded, flat colors, clean line art, hand-drawn animation, anime screenshot`；去掉 3d toon / 3D animated 等词
  - ref2va motion prompt：`2D anime art style` 替代 3D；quality 句改为 `2D anime film quality, clean line art, flat cel-shaded colors`
  - 负向/风格描述不变的部分保持
- 其余流程不变（TTS 用 templates_v5 work 音频、口型、剪辑、海报）

## 2. 后端分派（backend）
- 模板 `render_type=2d` → 不再用 custom_2d_runner，改为 **hq 2D 模式**：
  - 内置模板：`python -m pipeline.templates3d_hq --template <tid> --style-mode 2d`
  - custom：`custom_hq_runner.py <tid> --style-mode 2d`（wrapper 透传参数）
- 成功后把 `outputs/templates_3d/<tid>/final.mp4` + `poster.jpg` 复制/移动到 `outputs/templates_2d/<tid>/`（保留 dir 兼容；list/status 仍显示 2D 类型 + final）
- 耗时/阶段提示更新：2D 也是 prepare→generate→lipsync→assemble→poster，约 15–40 分钟
- 保留 custom_2d_runner 代码但不再分派（legacy，可后续删除）

## 3. 前端文案
- 新建模板向导：2D 动画提示改为「AI 生成 2D 动漫画面（cel-shading），约 15–40 分钟」；3D/写实不变
- 模板库/一键成片：2D 类型不再显示「约 1–3 分钟」，统一 15–40 分钟；第④步提示对应

## 4. 验收
1. 生成一个 4 幕 2D 模板（真实 H3 2D 模式，约 15–20 分钟）→ 抽帧确认画面为 2D 动漫（线稿/平涂/非纸片），字幕/口型正常
2. 3D/写实模板不受影响（dry-run 命令仍是 3d 模式）
3. 页面类型选择/耗时提示更新；2D 成片可在模板库播放
4. `tsc 0 / build / console` 无 error；docs/V42 + commit

## 5. 分工
- **tpl-backend**：hq style-mode 2d + backend 分派/产物复制 + 真实 2D 样本生成与抽帧
- **tpl-frontend**：2D 文案/耗时/提示更新 + 截图
- **Lead**：审查 2D 样片画质（不符合则调提示词再迭代）、端到端验证、docs、commit


## 6. 交付结果（2026-11-04 15:36）

### 实现
- `pipeline/templates3d_hq.py`：新增 `--style-mode {3d,2d}`
  - 2D：T2VA prompt 前置 `2D anime, cel-shaded, flat colors, clean line art, hand-drawn animation, anime screenshot`；替换 `3d toon` / `3D animated` / 3D 质感词
  - ref2va motion prompt：`2D anime art style` + `2D anime film quality, clean line art, flat cel-shaded colors`
  - 2D 缓存文件加 `_2d` 后缀，避免复用 3D 镜头；新增 `_ensure_card_shots` 为 custom 2D 自动补片头/片尾卡
- backend：`pipeline_ui` / `cinema_templates` 的 2D 分派改为 hq `--style-mode 2d`（内置直接、custom 走 custom_hq_runner 透传）；不再分派 `custom_2d_runner`（保留 legacy）；成功后 final/poster 复制到 `outputs/templates_2d/<tid>/`；2D 阶段恢复 prepare→generate→lipsync→assemble→poster→done
- 前端：2D 类型说明/耗时统一为「AI 生成 2D 动漫画面（cel-shading / 平涂），约 15–40 分钟」，第④步 2D 提示同步；卡片徽标保持 2D

### 真实 2D 样本（custom_tpl_v41_2d_demo，4 幕）
- 首轮 4 镜 H3 生成 **710.5s（约 11m50s）**（T2VA + 2 个 ref2va）；补片头/片尾卡后剪辑 9.3s
- 成片：**3,784,141B / 13.49s / 1280×720 / 25fps / AAC**，poster 70,268B
- 抽帧：`tmp/v42/frames/frame_01~06.png`
- 画面对比：`tmp/v42/2d_old_vs_new.jpg`（左旧 V5 纸片感；右新 AI 2D 动漫：霓虹演播厅/老周中近景/甜妹咖啡馆）
- 看片：`/media/templates_2d/custom_tpl_v41_2d_demo/final.mp4`
- 页面验证：模板库筛选 2D → 播放 AI 2D 成片；一键成片 2D 模板显示「约 15–40 分钟」；console 无 error

### 3D/写实回归
- `tonight_show`（3d）与 `real_street`（realistic）dry-run 命令仍为 `pipeline.templates3d_hq --style-mode 3d`，未被 2D 分派影响

### 说明 / 遗留
- 2D 现为 3D 档耗时（4 镜约 12 分钟；完整 6–8 镜约 15–25 分钟）；不再提供 1–3 分钟快剪
- 画面为 H3 生成的 2D 动漫（线稿/平涂/cel-shading），部分镜头人脸细节仍可继续迭代提示词（如更强 key visual/lineart 约束、指定日漫/国漫风格）
- 旧 V41 V5 快剪产物已被 hq 2D 成片覆盖；custom_2d_runner 不再被调用
