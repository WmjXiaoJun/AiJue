# MetaSo MiniMax H3 云端 Provider（测试用）

> 定位：**仅用于测试/快速迭代**。生产成片仍由本机 ComfyUI + MiniMax H3 int8 生成（outputs/h3/{id}_h3.mp4 保持不变）。

## 为什么用它
- 本机 H3（12GB 显存、ComfyUI 单 worker）单条 5s 镜头约 **116–223s**；
- MetaSo 云端 API 同模型同规格（MiniMax-H3），实测 **单条 5s/768P 约 23–25s**（上传<1s + 创建 1–2s + 生成 ~21s + 下载 <1s），约快 **6–10 倍**；
- 价格：768P **0.09 元/秒**（5s ≈ 0.45 元），2K 0.15 元/秒。

## 协议（与 meta-sota/ComfyUI-MiniMaxH3-API 一致）
- base_url：`https://metaso.cn/api/minimax`
- `POST /v1/files/upload`：multipart（purpose=video_generation_input, file）→ `mm_file://{file_id}`
- `POST /v2/video_generation`：`{model:"MiniMax-H3", content:[{type:"text",text},{type:"image_url",image_url:{url},role:"first_frame"}], resolution:"768P|2K", duration:5, ratio?}`
- `GET /v2/query/video_generation/{task_id}` → `{task:{status, content:{url}}}`

## CLI
```powershell
python -m integrations.metaso.h3 --image outputs/concepts/laozhou/portrait.png `
  --prompt "anime style, cel shaded, the character speaks to camera..." `
  --out outputs/h3/metaso/laozhou_metaso.mp4 --resolution 768P --duration 5
```
- Key 读取顺序：`--key` > 环境变量 `METASO_API_KEY` > `backend/data/metaso.env` > `integrations/metaso/.env`（均已 gitignore）
- 输出末行 JSON（含 timeline / 预估费用 / ffprobe）；同时写 `<out>.meta.json`
- 图生视频会先 upload 到 MetaSo 拿 mm_file://，端口/比例随图片（832×1216 → 输出 768×1120）

## 实测（2026-10-24）
| 任务 | 结果 |
|---|---|
| t2v 5s 768P 16:9 | 16.2s 完成，2.7MB |
| i2v 5s 768P（ali 概念图） | 23.5s 完成，4.4MB，768×1120 |
| 3 角色 i2v | laozhou 24.6s / ali 23.5s / dawei 24.3s，均 768×1120 / 5.175s / h264+aac |
| 预估成本 | 0.45 元/条（768P×5s） |

## 安全
- Key 只保存在本地 `backend/data/metaso.env` 或环境变量，**不要提交 git**；仓库只包含 `.env.example` 占位。
- 测试产物写 `outputs/h3/metaso/`，不覆盖本地 H3 文件。

## 后续（后端/前端接入）
- Settings：`h3_provider: local|metaso`（默认 local）、`metaso_base_url`、`metaso_key_configured`；
- POST /characters/{cid}/h3-animate 按 provider 选择：local → run_h3.py；metaso → `python -m integrations.metaso.h3 ... --out outputs/h3/{cid}_h3_metaso.mp4`（不覆盖本地版本）；
- 前端设置面板提供 provider 切换与「测试用」标识。
