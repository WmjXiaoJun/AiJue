# V43：2D 进阶——角色动漫关键帧 + H3 动画（提升脸/画质）

> V42 已把 2D 改为 H3 AI + 2D 动漫提示词，但用户认为「还是差点」：对话镜头脸/线条仍有 AI 痕迹（T2VA 直接生成首帧导致角色脸崩、细节糊）。
> 本版升级：**对话镜头先由 SDXL 动漫模型生成高质量「角色 + 场景」关键帧（IP-Adapter 锁脸），再把该关键帧交给 H3 做口型/微动**；B-roll 镜头保持 T2VA 但加强 anime key visual 提示词。

## 1. 生成流程（pipeline/templates3d_hq.py，2D 模式分支）
### 对话镜头（有台词）
1. 取角色合同（template cast role→character）与角色 anime 立绘 `assets/v5/characters/<id>/portrait.png`
2. 用 imagegen 生成关键帧：
   `python -m imagegen.cli generate --spec <spec> --mode scene --style anime --size 1280x720 --ref <portrait> --ip-adapter-strength 0.75 --prompt-extra "<semantic_visual>; anime key visual, cel-shaded, clean lineart, detailed eyes, official anime art, no text" --outdir <work>/keyframes/<shot>`
   - spec 可由合同外观构造（复用 V24 `_character_art_spec` 思路或角色 spec 缓存）
   - 产物 `scene.png` → 转 1024×576（保持 16:9）
3. ref2va：用该关键帧 + 该镜 TTS 音频 + 2D anime motion prompt（role/服装保持、口型同步）
4. 失败回退：keyframe 生成失败 → 原 T2VA 流程
### B-roll 镜头
- 保持 T2VA，但 prompt 加强：`anime key visual, Japanese TV anime screenshot, clean lineart, flat cel-shaded colors, detailed background, no 3D, no cgi`

### 缓存
- 关键帧缓存 `<work>/keyframes/<shot_id>.png`；2D ref2va 缓存 `_2d` 后缀不变

## 2. 验收
- 同一 4 幕 2D 模板（custom_tpl_v41_2d_demo）重新生成（约 15–20 分钟）
- 抽帧对比 V42：角色脸部/线稿/背景细节应明显更好；字幕/口型正常
- 3D/写实 dry-run 不受影响
- 报告：耗时、关键帧路径、成片 ffprobe、frame_01~06

## 3. 分工
- **tpl-backend**：hq 2D 对话镜头关键帧 + IP-Adapter + ref2va 改造 + B-roll 提示词强化 + 样本
- **Lead**：审片（不行继续迭代：更强 key visual / 国漫风格 / 不同 checkpoint）、端到端、docs、commit
