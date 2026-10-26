# V5.6 报告：口型同步修复 + 角色细节升级（pilot）+ H3 生成

> 用户反馈：①「嘴形完全没对上」；②「角色要像参考角色档案那样有细节（2.5D 真人感/高细节）」，然后给 H3 生成。

## 1. 口型同步（已修，16 部重渲）

**根因**：原 `characters_v5.viseme_timeline` 仅按台词字符权重把口型**均匀铺满镜头时长**，与 TTS 实际语速/停顿无关 → 嘴形与声音必然错位。

**修复**：新增 `pipeline/mouth_sync.py`：
- 读取每镜 TTS wav，按 25fps 计算短时 RMS 能量包络（去噪底/95 分位归一 + 3 帧平滑）；
- 有声音才张嘴：能量 <0.10 closed、<0.30 小口型（按台词字符哈希选 a/o/e/i/u/m）、<0.60 o/e、≥0.60 a；
- 与音频严格同时间轴（shot wav 含 LEAD 0.1s），静音自动闭嘴；
- `render_v5` 优先用 `mouth_sync.envelope_timeline(shot.audio, FPS, line)`，失败回退旧文本 viseme。
- 实测（history_talk v5_002 10.13s）：张嘴占比 **56.3%**，111 个口型事件，closed 11 段（对应停顿）。

**H3 路线的口型**：H3 `audio_ref` 工作流本身就是"参考图 + 音频 → 视频"，生成的嘴形与输入音频真实同步（pilot：`tmp/v56_h3/ali_detailed_h3.mp4`，抽帧可见开/闭口变化）；后续 hero 镜用 H3 可得到电影级真对口型。

## 2. 角色细节升级（pilot）

- 仅有的 SDXL checkpoint 是 **Animagine XL 4.0（动漫模型）**，无法直接生成参考图那种"2.5D 真人感写真级"细节。
- 已用高细节 prompt（detailed skin/lips/makeup/studio light/8k）+ IP-Adapter 保身份，为 3 个角色（ali/tianmei/laozhou）生成**高细节版 portrait + 全身立绘**：
  - 对比图 `tmp/v56_real/old_vs_new.png`（上排旧版、下排高细节版）——细节/光影/皮肤质感明显提升，但仍属"精细动漫"而非真人写真。
  - 文件：`tmp/v56_real/assets/<id>/{portrait.png, three_view/front.png}` + `pilot_report.json`。
- **达到参考图级别的两条路径**：
  1. 下载真实感 SDXL checkpoint（RealVisXL / Juggernaut XL 约 6.5GB）→ 用同 IP-Adapter 流程重生成 24 角色 → 细节接近参考写真；
  2. 用 H3 当"细节放大器"：以当前高细节立绘为参考生成视频（H3 输出更电影化/立体），再抽帧做角色资产。
- 3D/H3 生成验证：`tmp/v56_h3/ali_detailed_h3.mp4`（1024×576/5.17s/含音频，H3 audio_ref，2m02s GPU）抽帧见开闭口/头部动作。

## 3. 下一步（需确认）

| 选项 | 内容 | 成本 |
|---|---|---|
| A | 下载 RealVisXL/Juggernaut（~6.5GB）→ 重生成 24 角色高细节画像 + 全身 → H3 hero 镜 | 下载 10-30 分钟 + GPU 20-30 分钟 |
| B | 用现有高细节立绘 + H3 生成角色镜头（24 角色各 1 条 5s 介绍/对白） | GPU ~24×2 分钟 ≈ 50 分钟 |
| C | 先只做 3 个 pilot 角色的 H3 成片（验证画质/口型/一致性） | ~6 分钟 |
