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
- 角色工坊要能「自己造人物」，可依托 **AI 3D** 程序化建模 + 卡通渲染；
- 角色资产要能直接用于资产库/分镜台/成片渲染，成片里不允许出现真人素材；
- 环境已确认：AI 3D 5.0.1 无头渲染可用；本机 ComfyUI（D:\BaiduSyncdisk\wmj_work\XJ\ComfyUI）内含 MiniMax H3 int8 模型与 4step turbo LoRA，可尝试真实 H3 生成（RTX 3080 Ti 12GB）。
- 交付追加：角色工坊前后端 + ai_character AI 3D 生成器 + 3 个演示角色 + 用角色画面重渲的样例成片 +（尽力）H3 真实动漫镜头。
## 2026-10-24 角色 2.1（一致性）
- SDXL 概念图三视图/表情用 IP-Adapter（plus_sdxl_vit-h + CLIP-ViT-H）条件化，解决换脸问题。
- H3 动画改为概念图驱动（覆盖 outputs/h3/{id}_h3.mp4），成片优先用概念图/H3 新素材。
- 模型下载走 imagegen/tools/download_model.py 多连接；ComfyUI 单 worker 串行。
## 2026-10-24 H3 双 Provider（用户指示）
- 用户提供 MetaSo 云端 MiniMax H3 API（测试用），要求更快的 H3 用它，其余仍本地生成。
- 结论：默认 provider=local（本机 ComfyUI，生产成片）；新增 provider=metaso（测试/快速迭代），产物单独写 outputs/h3/metaso/ 或 {id}_h3_metaso.mp4，不覆盖本地 H3。
- 实测：MetaSo i2v 5s/768P 单条约 23-25s（vs 本地 116-223s），约 6-10 倍快；0.09 元/秒（5s 约 0.45 元）。
- Key 仅存 backend/data/metaso.env（gitignore），仓库只保留 .env.example。
- API 协议：POST /v1/files/upload → mm_file://；POST /v2/video_generation {model,content,resolution,duration,ratio?}；GET /v2/query/video_generation/{task_id}。
