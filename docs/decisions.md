# 决策记录（2026-10-23 夜）
1. 产品名：**造片局**；品类：**AI 微综艺**；全称「造片局 · AI 微综艺」。
2. 交付形态：本夜交付**可运行 demo 平台**（前端+后端+流水线+样例成片+文档），不是生产级 SaaS。
3. 技术栈：FastAPI + SQLAlchemy + SQLite（demo）；前端 Vite + React + TypeScript，构建后由后端静态托管；无 GPU 依赖。
4. 生成策略：默认 mock provider（ffmpeg 生成示意片段），同时实现 ComfyUI(MiniMax H3) 真适配器；demo 不实际调用 H3（无凭据/需用户 ComfyUI 地址）。
5. TTS：edge-tts（zh-CN-XiaoxiaoNeural / YunxiNeural / YunjianNeural），已验证可用；字幕用 vtt/逐镜头文本。
6. 视频工具：ffmpeg/ffprobe 来自 tools/node_modules（ffmpeg-static 6.1.1），不依赖系统安装。
7. 端口：后端 8090；前端 dev 5173；构建产物由后端 / 提供。
8. 界面逻辑参考 ArcReel：项目制、资产先行、分镜/生成工作区、任务中心、费用、版本；**不复制 ArcReel 代码**（AGPL）。
9. 样例内容：虚构 AI 圆桌综艺「AI 会取代综艺导演吗？」，约 5 分钟，人物均为虚构。
10. 时间/成本：H3 768P 按 0.09 元/秒估算；mock 成本记 0 但保留费用结构。
## 2026-10-24 追加需求（用户）
- 所有角色必须是**平台自主生成的原创动漫人物**，支持 **3D 与 2D 多种风格**；
- 角色工坊要能「自己造人物」，可依托 **Blender** 程序化建模 + 卡通渲染；
- 角色资产要能直接用于资产库/分镜台/成片渲染，成片里不允许出现真人素材；
- 环境已确认：Blender 5.0.1 无头渲染可用；本机 ComfyUI（D:\BaiduSyncdisk\wmj_work\XJ\ComfyUI）内含 MiniMax H3 int8 模型与 4step turbo LoRA，可尝试真实 H3 生成（RTX 3080 Ti 12GB）。
- 交付追加：角色工坊前后端 + studio3d Blender 生成器 + 3 个演示角色 + 用角色画面重渲的样例成片 +（尽力）H3 真实动漫镜头。
