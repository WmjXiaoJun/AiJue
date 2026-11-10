# Muse 视频生成模型 · 怎么接（V50 操作手册）

> 你问的是「muse 那个视频生成模型」怎么接进来。这篇是**照着做就能通**的手册。
> 先明确：**MuseTalk 是口型模型（已接好）**；本文讲的是 **MuseV / MusePose / 云端 Muse** 这类「生成视频」的模型。

---

## 0. 先分清三种「Muse」

| 名字 | 干什么 | 现状 |
|---|---|---|
| **MuseTalk 1.5** | 给已有视频改口型（lip-sync） | ✅ 本机已装好并跑通（`docs/V50_MUSE_GENERATION.md`） |
| **MuseV**（TME Lyralab） | 文本 / 图片 → 人物视频 | ⬜ 未安装（本文 §2 教你怎么装） |
| **MusePose**（TME Lyralab） | 参考图 + 姿态序列 → 人物视频 | ⬜ 未安装；`dwpose` 权重本机已有（§3） |
| **云端 Muse**（任意厂商/自建） | HTTP 调用出片 | ✅ 适配器已实现并跑通（§4，用本地假接口验证过） |

平台侧的适配层已经写好：`pipeline/muse_video.py`，四类 provider 同一套签名，装好后**不用改业务代码**。

```
python -m pipeline.muse_video --check      # 看当前探测到什么
```

输出会告诉你：`musev_local / musepose_local / muse_cloud / stub` 各自是否可用，以及推荐用哪个。

---

## 1. 选哪条路（按你的显卡和用途）

| 你的情况 | 选 | 说明 |
|---|---|---|
| 有 24GB 显存（3090/4090/A10） | **MuseV 本地** | 512×768、25–49 帧，单条 1–3 分钟 |
| 只有 12GB（本机 3080 Ti） | **云端 API** 或 MuseV 降到 384×512 / 短帧 | 本地跑会 OOM；云端最省事 |
| 要做「按骨架/动作驱动同一个人」 | **MusePose 本地** | 参考图 + 姿态序列；dwpose 权重已有 |
| 只是先让流水线能跑通 | `provider=stub` | 生成纯色占位片（`stub=true`），**不能当交付** |

---

## 2. 路线 A：本地 MuseV（一次装好，长期免费）

### 2.1 拉代码（不要放进仓库大文件，按 vendor 引用）

```powershell
cd D:\BaiduSyncdisk\wmj_work\XJ\AiJue\local_models
mkdir muse -Force; cd muse
git clone https://github.com/TMElyralab/MuseV.git
```

平台约定目录（探测就认这个）：`local_models/muse/MuseV/`，权重放 `local_models/muse/MuseV/pretrained_weights/`。

### 2.2 环境（与现有 venv 隔离，避免污染 lipsync）

```powershell
cd D:\BaiduSyncdisk\wmj_work\XJ\AiJue\local_models\muse\MuseV
python -m venv .venv
.venv\Scripts\python.exe -m pip install -U pip
.venv\Scripts\python.exe -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

> 显卡 12GB 的机器：`pip install` 后先跑最小分辨率（384×512 / 16 帧）验证不 OOM，再往上加。

### 2.3 权重

按 MuseV README 下载到 `pretrained_weights/`（`MuseV`、`MuseV_WH` 用 `text2video` 需要的那套），
以及 sd-vae、ImageBind、motion 模块等。下载完核对：

```powershell
python -m pipeline.muse_video --check
# musev_local.installed=true / weights=true 就说明目录对了
```

### 2.4 告诉平台怎么调它（一行环境变量）

平台的本地 provider 不猜你的 CLI，而是读命令模板：

```powershell
$env:MUSEV_CMD = '.venv\Scripts\python.exe scripts/inference/text2video.py --prompt "{prompt}" --num-frames 100 --height {height} --width {width} --output {out}'
```

占位符：`{prompt} {image} {pose} {duration} {seed} {width} {height} {out}`。
**按你 clone 下来的 MuseV 实际参数改这一段**（不同版本参数名会变），改完：

```powershell
python -m pipeline.muse_video --check
python -m pipeline.muse_video --prompt "底城夜雨，老秦抬头看向霓虹" --duration 4 --out tmp/muse/musev_test.mp4
```

出片即接好。产物会出现在 `tmp/muse/`（自己指定）或 `outputs/muse_video/<template>/`（走流水线时）。

---

## 3. 路线 B：本地 MusePose（动作驱动）

```powershell
cd local_models/muse
git clone https://github.com/TMElyralab/MusePose.git
# 依赖/权重按 MusePose README；dwpose 已在本机：
# local_models/lipsync_models/dwpose/dw-ll_ucoco_384.pth
$env:MUSEPOSE_CMD = '.venv\Scripts\python.exe scripts/inference.py --ref_image "{image}" --pose_dirs "{pose}" --output {out}'
python -m pipeline.muse_video --check       # musepose_local.installed=true
```

用途：一季里要「同一个角色做同一套动作」时，先用 dwpose 从参考视频提姿态序列，再 MusePose 驱动角色图。

---

## 4. 路线 C：云端 Muse（最快，已实现并验证）

### 4.1 配置（3 个环境变量）

```powershell
$env:MUSE_API_URL  = "https://你的服务/v1/video/generate"   # POST 提交
$env:MUSE_API_KEY  = "sk-..."                               # Bearer token（没有就不设）
$env:MUSE_MODEL    = "muse-v1"                              # 请求体里的 model
# 可选：任务轮询地址；不填则用 <MUSE_API_URL>/<task_id>
$env:MUSE_POLL_URL = "https://你的服务/v1/video/tasks/{id}"
```

### 4.2 平台发出的请求（照着对齐你的服务即可）

```json
{
  "model": "muse-v1",
  "prompt": "底城夜雨，老秦抬头看向霓虹",
  "duration": 4.0,
  "aspect": "16:9",
  "fps": 25,
  "seed": 20261105,
  "image_b64": "…（可选，首帧参考图 base64）",
  "pose": "…（可选，姿态序列路径/ID）"
}
```

### 4.3 平台接受的响应（任一种都可以）

```json
{ "video_url": "https://.../out.mp4" }
{ "url": "https://.../out.mp4" }
{ "status": "running", "task_id": "t1" }     // 之后轮询，直到出现 video_url
```

适配器会自动：POST 提交 → 需要就每 3s 轮询 → 下载到 `outputs/muse_video/...` → ffprobe 校验。
**这段已经用本地假接口实测通过**（POST → 轮询 → 下载 → 640×360/2s，3.04s 完成）。

---

## 5. 接进平台后怎么用（三个入口）

| 入口 | 命令 / 路径 |
|---|---|
| CLI | `python -m pipeline.muse_video --prompt "..." --duration 4 --out tmp/x.mp4` |
| API | `GET /api/v6/muse/video/status` · `POST /api/v6/muse/video {prompt, duration, aspect, provider, template, name}` · `GET /api/v6/muse/video/jobs/{id}` |
| 智能体 | 工具 `muse_video`；工作流阶段 `musev`（默认关闭，33 份合同都已插入） |
| 产物 | `outputs/muse_video/<template>/<name>.mp4` → `/media/muse_video/...` |

打开阶段（任选一）：

```powershell
python -m pipeline.agents edit --template custom_tpl_v46_cyber --op patch_workflow --json '{"enable":["musev"]}'
python -m pipeline.agents run --template custom_tpl_v46_cyber --stages musev
```

> 注意：`musev` 阶段**不依赖 shots**（不会触发重渲），它按 params 里的 prompt/时长单独出一条视频，
> 结果落在 `outputs/muse_video/`，需要进片就替换 shots 后重跑 `assemble`。

---

## 6. 验收清单（接完必跑）

1. `python -m pipeline.muse_video --check` → 目标 provider `installed/configured = true`；
2. `python -m pipeline.muse_video --prompt "测试" --duration 2 --out tmp/muse/smoke.mp4` → 有 mp4、ffprobe 有 h264+aac；
3. `GET /api/v6/muse/video/status` → `state.provider` 不是 `stub`；
4. `POST /api/v6/muse/video`（不带 `allow_stub`）→ 不是 400；job 走完 `status=ok`；
5. 页面 `#/agent` → 「Muse 视频」卡片显示 provider 与最近产物。

---

## 7. 常见故障

| 现象 | 原因 / 处理 |
|---|---|
| `provider=stub` 且 `hint` 提示未配置 | 目录或环境变量没对：本地要 `local_models/muse/MuseV`；云端要 `MUSE_API_URL` |
| 本地 provider `installed=true` 但 `weights=false` | 权重没落到 `pretrained_weights/` |
| 跑本地 OOM | 降 `{width}/{height}` 或帧数；12GB 卡建议 384×512、≤49 帧，或直接走云端 |
| 云端 401/403 | `MUSE_API_KEY` 没设或不是 Bearer 方案 |
| 云端一直 running | `MUSE_POLL_URL` 模式不对；看 `pip` 日志里的轮询地址 |
| 出片没声音 | 生成模型一般不带音轨；平台侧用 TTS + `assemble` 混音，别指望模型出声 |

---

## 8. 想接别的「Muse」（换家的模型）

写 20 行就够：在 `pipeline/muse_video.py` 里加一个 `_xxx_generate(spec, out, prog)`，
在 `available()` 的 `checks` 里加探测项，在 `generate_video()` 的分发里加一个 `elif`。
签名统一：输入 `spec`（prompt/image/pose/duration/aspect/seed），输出
`{"ok": True, "provider": ..., "out": ..., "bytes": ..., "probe": ...}`，其余（API/页面/阶段/日志）全部复用。

---

## 9. 云端在哪注册、怎么用（V50.2 补充）

平台现在内置 4 个云端/本地 provider，`python -m pipeline.muse_video --check` 会全部列出来：

| provider | 是什么 | 需要什么 |
|---|---|---|
| `replicate` | **Replicate 托管的 MuseV / MuseTalk**（官方托管，按量计费） | `REPLICATE_API_TOKEN` |
| `useapi_muse` | **Meta Muse 的第三方代理**（useapi.net） | `USEAPI_TOKEN` + `USEAPI_ACCOUNT` |
| `muse_cloud` | 任意自建/厂商 HTTP 接口（RunningHub、OpenBayes、自家服务） | `MUSE_API_URL`（+ key） |
| `musev_local` / `musepose_local` | 本地开源（要 ≥20GB / 16GB 显存） | 代码+权重+`MUSEV_CMD` |

### 9.1 Replicate（推荐先试这条：不用自己装环境）

1. **注册**：<https://replicate.com/signin>（GitHub 账号一键登录）。
2. **拿 token**：右上头像 → `Account` → `API tokens` → `Create token`，形如 `r8_xxxxxxxx`。
3. **充值**：`Account` → `Billing`（按模型页的计费单位扣费，**具体价格以模型页为准**）。
4. **看模型页**（确认输入字段与价格）：<https://replicate.com/douwantech/musev>；口型是 <https://replicate.com/douwantech/musetalk>。
5. **配置并自检**：

```powershell
$env:REPLICATE_API_TOKEN = "r8_你的token"
$env:REPLICATE_MUSEV_MODEL = "douwantech/musev"      # 也可换成别的 Muse 系模型 owner/name
# 如果模型页给的是 version hash（旧式接口）才需要：
# $env:REPLICATE_MUSEV_VERSION = "<hash>"

python -m pipeline.muse_video --check        # 期望 provider = "replicate"
python -m pipeline.muse_video --prompt "底城夜雨，老秦抬头" --duration 4 --out tmp/muse/replicate.mp4
```

6. **字段对不上怎么办**：Replicate 每个模型的 input 名字不一样，用映射覆盖：

```powershell
$env:REPLICATE_MUSEV_INPUT_MAP = '{"prompt":"prompt","image":"image","duration":"num_frames"}'
```

（左边是我们的字段名：`prompt/image/pose/duration/seed/aspect`；右边填模型页 Input 里真实的键名。）

> 平台已用**本地假 Replicate** 跑通整条链路：提交 → `starting/processing` 轮询 → `succeeded` → 下载 → ffprobe 校验，6.03s 出片。

### 9.2 useapi.net（要的是「Meta 官方 Muse」那种 10 秒带声音的片子）

前提：**Meta Muse 属于订阅制产品，官方没有开放视频生成 API**（社区文档一致，见 useapi 的对比文章）。
要走这类，需要：① 能用的 Meta Muse 订阅账号；② useapi.net 的订阅 token。

1. 注册/授权：<https://useapi.net/docs/start-here/setup-muse>（按其文档绑定账号）。
2. 拿账号名：`POST https://api.useapi.net/v1/muse/accounts`（Bearer token），返回里的 account 名。
3. 配置：

```powershell
$env:USEAPI_TOKEN   = "你的 useapi token"
$env:USEAPI_ACCOUNT = "你的 muse account 名"
# 接口/轮询地址如与默认不同，可覆盖：
# $env:USEAPI_MUSE_URL  = "https://api.useapi.net/v1/muse/videos"
# $env:USEAPI_MUSE_POLL = "https://api.useapi.net/v1/muse/videos/{id}"
python -m pipeline.muse_video --check        # 期望 provider = "useapi_muse"
```

4. 适配器兼容三种返回：`{video_url}` / `{url}` / `{id,status}` 再轮询。

> 这条是第三方代理，稳定性和条款风险自己判断；平台只在出片后把 mp4 拉到本地。

### 9.3 自建/国内 GPU 云（RunningHub、OpenBayes）

- **RunningHub**：注册 <https://www.runninghub.cn> → 充值 → 用平台现成的 MuseV 工作流 → 拿 `apiKey` + `workflowId`；
  然后把它们的 openapi 地址填进通用 provider：

```powershell
$env:MUSE_API_URL = "https://www.runninghub.cn/task/openapi/create"   # 以你后台文档为准
$env:MUSE_API_KEY = "你的 apiKey"
```

- **OpenBayes**：注册 <https://openbayes.com> → 按官方 MuseV 教程起一个 GPU 实例（教程：`openbayes.com/console/public/tutorials/uVaH9i3DYxr`）→ 把实例暴露的 HTTP 地址填 `MUSE_API_URL`。

这两条都是「你有一台云 GPU 在跑 MuseV」，平台的通用 `muse_cloud` provider 直接对接，不需要改代码。

### 9.4 三条路怎么选

| | Replicate | useapi.net（Meta Muse） | 自建云 GPU |
|---|---|---|---|
| 要不要自己装环境 | 不要 | 不要 | 要（或买现成工作流） |
| 注册难度 | 最低（GitHub 登录 + 充值） | 中（要 Muse 订阅 + useapi 订阅） | 中 |
| 成本 | 按量，模型页标价 | 订阅制 | 按 GPU 小时 |
| 能不能指定画幅/时长 | 看模型 input | 10 秒档为主 | 自己控制 |
| 平台支持 | ✅ `provider=replicate` | ✅ `provider=useapi_muse` | ✅ `provider=muse_cloud` |

### 9.5 配好之后在平台上怎么用

```powershell
# 1) 自检
python -m pipeline.muse_video --check

# 2) 出一条
python -m pipeline.muse_video --prompt "..." --duration 4 --aspect 9:16 --out tmp/muse/a.mp4

# 3) 走 API（后台任务）
Invoke-WebRequest -Method Post -ContentType application/json '
  -Body '{"prompt":"底城夜雨","duration":4,"aspect":"16:9","template":"custom_tpl_v46_cyber"}' '
  http://127.0.0.1:8090/api/v6/muse/video -UseBasicParsing

# 4) 页面：http://127.0.0.1:8090/#/agent → 右栏「Muse 视频」→ 填 prompt → 生成
```

> 配置改完要**重启后端**（环境变量在进程启动时读取）。

---

## 11. MiniMax 海螺（Hailuo / H3）在线接入（V72）

本地 ComfyUI 跑的其实就是 **MiniMax H3 权重**（`minimax_h3_fl2va_pruned_int8`）。
在线版只是把同一类模型放到 MiniMax 云上跑，优点是免显存、速度快，缺点是按量计费、需要 Key。

### 注册与取 Key
1. 打开 https://platform.minimaxi.com ，注册并完成实名认证；
2. 控制台 →「接口密钥 / API Keys」→ 新建，复制 `sk-...`；
3. 需要开通「视频生成（Hailuo）」权限，并确认账户有余额。

### 配置（PowerShell）
```powershell
$env:MINIMAX_API_KEY="sk-..."          # 必填
$env:MINIMAX_MODEL="MiniMax-Hailuo-02" # 可选，默认这个
$env:MINIMAX_BASE="https://api.minimaxi.com"
$env:MINIMAX_GROUP_ID="..."            # 可选，部分账号需要
```

### 自检与单条试跑
```powershell
python -m pipeline.muse_video --check
python -m pipeline.muse_video --provider minimax_cloud --prompt "赛博深夜演播室，主持人说话" `
       --duration 6 --resolution 1080P --out tmp/muse/minimax_test.mp4
```

### 实现位置
- `pipeline/muse_video.py`：`minimax_config()` / `_minimax_generate()`（创建任务 → 轮询 → 下载），
  `available()` 里 `minimax_cloud` 排在最前（有 Key 时优先）；
- 后端 `GET /api/v6/muse/video/status` 会把 `minimax_cloud` 一并返回；
- 生成接口：`POST /api/v6/muse/video {provider:"minimax_cloud", prompt, duration, resolution}`。

### 与成片链路的衔接（待接）
当前 `scripts/run_episode.py` 的镜头仍走本地 H3（ComfyUI）。要把镜头切到在线 MiniMax，
需要给 hq 增加 `--video-provider minimax`：把每个 shot 的 prompt 提交到 MiniMax，拿回 mp4 落到
`shots/<sid>.mp4`，后续锁脸/字幕/合成不变。这部分我可以按需接。
