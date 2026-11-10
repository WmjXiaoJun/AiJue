# NOTICE

## 代码许可

本仓库代码采用 **MIT License**，见 [LICENSE](LICENSE)。

## 演示素材

- `assets/` 下的角色立绘、场景底板、cutout、海报等为 **AI 生成 / 程序化生成的演示素材**，随仓库提供用于跑通 demo；不包含真人肖像。
- 素材由 SDXL / Animagine XL 等模型生成或由脚本程序化绘制；**商用前请自行确认所用基础模型的许可证与输出条款**。
- `outputs/` 与 `local_models/` 不入库（见 .gitignore）；仓库不附带模型权重、成片和第三方二进制。

## 第三方与外部服务

- **Breeze TTS 2**：研究 / 非商用许可（商用需 BreezeBlue 授权），本仓库不附带权重。
- **MuseTalk 1.5**：MIT，本仓库不附带其代码与权重。
- **FFmpeg**：LGPL/GPL，本仓库不附带二进制（`tools/node_modules/` 被忽略）。
- **MetaSo / APIMart / MiniMax / OpenAI 兼容接口**：外部服务，API Key 只存本机 `backend/data/*.env`（已忽略），仓库不含真实 Key。
- **ArcReel / OpenMontage**：本项目的流程与文档参考了它们的公开思路；未复制其代码。若后续引入代码，请同时遵循对应项目的许可证。

## 免责声明

本项目是演示 / 研究用途的 AI 视频生产工作台，不保证生成内容的版权、事实准确性或商用合规性；使用前请自行审核。
