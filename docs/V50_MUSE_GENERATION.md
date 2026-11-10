# V50 · Muse（MuseTalk 1.5）生成能力接入

> 需求（用户 2026-11-05）：**具备接入 Muse 的能力来生成**。
> 结论：MuseTalk 1.5 已在本机**真实跑通**并接成产品能力——API / 智能体阶段 / 页面三个入口共用同一条推理链路。

---

## 1. 现在能生成什么

| 能力 | 入口 | 产物 |
|---|---|---|
| **口型生成（MuseTalk 1.5）** | `POST /api/v6/muse/sync`、`python -m pipeline.agents run --stages muse`、页面「Muse 口型修正」 | `outputs/muse/<template>/<shot>_muse.mp4`（1024×576 / 24fps / h264+aac） |
| 回写成片 | `muse` 阶段 `params.apply=true`（默认关） | 备份原镜头到 `shots/_orig/` → 覆盖 `shots/<sid>.mp4` → 调 `templates3d.assemble` 重新合成 |
| 可用性自检 | `GET /api/v6/muse/status`、`python -c "from pipeline import lipsync; print(lipsync.available())"` | 代码/权重/venv/ffmpeg/GPU 逐项布尔 + reason |

---

## 2. 本机环境（已就绪，不是占位）

| 项 | 值 |
|---|---|
| 代码 | `local_models/lipsync/MuseTalk`（vendor 引用，含 musetalk/、scripts/） |
| CUDA 探测 | `GET /api/v6/muse/status` → `available.cuda = {available:true, device:"NVIDIA GeForce RTX 3080 Ti"}`（首次 ~2s，之后缓存） |
| 权重 | `local_models/lipsync_models/`：`musetalkV15/unet.pth` 3.24GB（字节数校验 3,400,074,924）、`sd-vae` 319MB、`whisper` 144MB、`syncnet` 845MB、`yunet` 0.2MB、`dwpose` 388MB |
| 运行环境 | `local_models/lipsync-venv`：**torch 2.9.1+cu128，CUDA 可用，RTX 3080 Ti（12GB）** |
| 转发机制 | 主环境（无 torch）调 `lipsync.sync_clip()` 自动 subprocess 转发到 venv；`LIPSYNC_FORWARD_DEPTH` 防进程风暴（历史事故见 `docs/LIPSYNC_FEASIBILITY.md`） |

---

## 3. 实测（真实生成，非 mock）

输入：`custom_tpl_v46_duologue` 第 2 幕对白镜（`shots/v5_002.mp4` 1024×576/24fps/124 帧 + `work/audio/v5_002.wav`）

| 指标 | 值 |
|---|---|
| 耗时 | **21.02s**（含模型加载 8.86s；纯推理 ≈12s） |
| 输出 | 79 帧 / **3.292s** / 1024×576 / 24fps / h264 + aac |
| 速度 | 56.16 ms/帧（3.76 fps 吞吐） |
| 显存 | peak 6845 MB / 12GB |
| 人脸 | 124/124 帧检出（YuNet 22ms/帧），0 帧跳过贴回 |
| 阶段 | VAE preprocess 2.25s → UNet 2.06s → paste 0.13s → encode 0.36s |
| 前后对比 | `tmp/v5_lipsync/runtime/v5_002_dump/0000_before.png` / `0000_after.png` |
| 接口链路 | `POST /api/v6/muse/sync` → job `ok` → `/media/muse/custom_tpl_v46_duologue/v5_002_muse.mp4` **200 / 1.6MB** |

---

## 4. 接口

```
GET  /api/v6/muse/status
  -> { ok, available:{ok, musetalk, syncnet, deps:{venv_python,musetalk_code,ffmpeg,unet,unet_config,sd_vae,whisper,yunet}, torch_in_env, reason},
       recent:[{template,shot,url,bytes,mtime}], jobs:[...], running }

POST /api/v6/muse/sync
  body { template?, limit?, clip?, audio?, start?, duration? }
  -> { ok, job_id, template, shots:[sid...], provider:"musetalk" }
  - 只给 template：自动挑「对白镜头 + 音频」对（优先 episode.json 里 type=dialogue / 有 line 的镜头）
  - 给 clip + audio：单条处理（相对仓库根路径）
  - limit 1–8；同一时间只允许 1 个 Muse 任务（409）

GET  /api/v6/muse/jobs/{job_id}
  -> { ok, status:"running|ok|failed", logs:[{t,msg}], result:{items:[{shot,out_url,elapsed}], ok_count, failed_count, out_dir} }
```

---

## 5. 智能体编排里的 Muse

- 工具：`muse_lipsync`（video_director）。参数 `limit`（默认 2）、`apply`（回写 shots，默认 false）、`reassemble`（apply 后重新合成，默认 true）。
- 阶段：两条栈的 33 份工作流都已插入 `muse` 阶段（**默认 enabled=false**，可选）：
  - `hq_shorts`：story → art? → shots → **muse?** → qc → publish
  - `v5_render`：story → voice → art? → editorial → shots → **muse?** → assemble → storyboard → qc → publish（启用 muse 时记得同时跑 assemble）
- 命令行：

```powershell
# 只跑 Muse（逐镜，产物在 outputs/muse/<tid>/）
python -m pipeline.agents run --template custom_tpl_v46_duologue --stages muse

# 回写成片：muse + assemble（apply 由工作流 params 控制）
python -m pipeline.agents run --template custom_tpl_v46_duologue --stages muse,assemble
```

---

## 6. 页面

`#/agent` 顶部 chips 增加「Muse 口型」状态灯；右栏「Muse 口型修正」卡片：provider / CUDA / venv 状态、最近生成片段（可直接播放）、「给本集对白镜跑口型」按钮（limit 默认 2）+ 复用日志面板轮询 `/jobs/{id}`。不可用时按钮禁用并显示 `reason`。

截图：`tmp/ui/v49/agent_muse.png`（真实状态 + 弹层）、`tmp/ui/v49/agent_muse_job.png`（任务完成态）。

---

## 7. 限制与注意

| 项 | 说明 |
|---|---|
| 串行 | 必须一次一个 clip（GPU 12GB）。**跨进程独占锁**：`pipeline.lipsync` 在 `tmp/v5_lipsync/.muse.lock` 上抢锁（父进程持锁，子进程 `LIPSYNC_CHILD=1` 跳过），CLI / API / agent 同时只允许 1 个任务；锁残留 >40 分钟自动回收 |
| 帧数上限 | `max_frames=600`（≈24s@25fps），防止误跑长批量 |
| 时长对齐 | 输出取视频/音频较短者（本例 124 帧输入 → 79 帧输出，音频 3.292s） |
| `apply` 风险 | 会覆盖 `shots/<sid>.mp4`；原件自动备份到 `shots/_orig/`，可回滚 |
| 2D 绘画风格 | YuNet 在赛博/日漫脸上检出 124/124；极端风格化（几何/剪影）可能检不到，届时返回 `face_found=false` |
| SyncNet 打分 | `measure_sync()` 仍依赖 xformers，未启用（见 LIPSYNC_FEASIBILITY §遗留）——当前用前后帧 + 人工抽检验收 |

---

## 8. 下一步（backlog）

1. **SyncNet 同步分**（装 xformers 后可给每个产物出 LSE-C/LSE-D 分数，进 QC 硬门）。
2. **季批量口型**：季生产时对全部对白镜跑 muse（3 集 × 4 镜 ≈ 4 分钟 GPU）。
3. **MuseV / MusePose**：`dwpose/dw-ll_ucoco_384.pth` 已在本地，可接「参考姿态驱动角色」，作为 keyframe 之外的第二条人物动画路径。
4. **Muse 结果进最终成片**：把 `apply=true` 设为短片栈默认（需先验证重合成后 QC 与时长）。

---

## 9. V50.1 追加（同日）

### 9.1 同步分（口型是否对得上）：先给可用的客观指标

- 新增 `pipeline.lipsync.mouth_audio_sync()`：YuNet 逐帧取嘴部 ROI → 相邻帧运动量 + 音频 RMS 包络 → ±3 帧内取最佳 Pearson 相关。
- 实测（v5_002_muse.mp4）：`corr_best=0.2116`、`lag_frames=-1`、`face_ratio=1.0`、79 帧，秒级完成。
- `measure_sync()` 现在返回该指标；Muse 口型任务的每条结果带 `sync:{corr_best,lag_frames,face_ratio,provider}`。
- **SyncNet 真分数未启用**：`local_models/lipsync_models/syncnet/latentsync_syncnet.pt` 只有 886,297,621 字节且 zip 中央目录缺失（下载中断），`available().syncnet_ready=false` + 具体 reason。补齐办法见 `docs/MUSE_VIDEO_INTEGRATION.md` §7 与 `LIPSYNC_FEASIBILITY.md`。

### 9.2 Muse 视频生成（MuseV / MusePose / 云端）

- 适配层：`pipeline/muse_video.py`，四类 provider（`musev_local` / `musepose_local` / `muse_cloud` / `stub`），统一 `generate_video(spec)`。
- CLI：`python -m pipeline.muse_video --check`，`--prompt ... --out ...`。
- API：`GET /api/v6/muse/video/status`、`POST /api/v6/muse/video`、`GET /api/v6/muse/video/jobs/{id}`；产物 `outputs/muse_video/<template>/<name>.mp4`。
- 智能体：工具 `muse_video` + 工作流阶段 `musev`（33 份合同都已插入，默认关）。
- **云端分支已实测**：本地假接口 POST → 轮询 → 下载 → ffprobe 640×360/2s，3.04s；API 走通 `musev_65cd… → ok → /media/muse_video/...`。
- **本地分支**：目录 `local_models/muse/MuseV(..MusePose)` 未安装，`available()` 如实返回 `installed=false` 并给 `hint`；装法见 `docs/MUSE_VIDEO_INTEGRATION.md` §2/§3。

### 9.3 季批量口型 + 1080P 合集

- `POST /api/v6/agent/season/{sid}/run` 新增 `with_muse=true`：对**全部集数**只跑 `muse` 阶段（不重渲），实测 3 集全部 `planned`，阶段列表只有 `muse`。
- 系列导出新增 `hd=true`：`POST /api/v5/series/{sid}/export?force=true&hd=true` → **1920×1080 / 52.8s / 38.1MB / variant=horizontal_1080p**，`/media/series/<sid>/series_1080p.mp4` 200。

### 9.4 版本表

| 能力 | 入口 | 状态 |
|---|---|---|
| MuseTalk 口型 | API / CLI / 页面卡片 / `muse` 阶段 | ✅ 真跑 |
| 同步分（代理指标） | `measure_sync` / 任务结果 `sync` | ✅ 真跑 |
| SyncNet LSE 分 | — | ⛔ 权重损坏，需重下 |
| MuseV / MusePose | `musev_local` / `musepose_local` | ⬜ 未安装（手册已写） |
| 云端 Muse | `muse_cloud` | ✅ 适配器+API 实测 |
| 季批量口型 | `season/run {with_muse}` | ✅ dry-run 实测 |
| 1080P 合集 | `series/export?hd=true` | ✅ 实测 |

### 9.5 页面（agent-ui 交付）

- `#/agent` 右栏顺序：单集运行 → **Muse 口型修正** → **Muse 视频** → 智能体编辑。
- 「Muse 口型修正」每条结果新增「同步 x.xx（lag n 帧）」：>0.25 绿 / 0.1–0.25 金 / <0.1 灰。
- 「Muse 视频」卡片：provider chip（muse_cloud 金 / 本地 玉 / stub 灰）、4 项自检明细、prompt + 时长(2/4/6s) + 画幅(16:9/9:16/1:1) + provider 下拉、2s 轮询日志、成片播放、recent 缩略；未接入时显示 hint + 「查看接法」并禁用按钮；stub 需二次确认（`allow_stub`）。
- 季面板新增「同时跑口型（Muse）」勾选 → `with_muse:true`（提示：只跑口型、不重渲）。
- 中栏出现 `07 Muse 视频（musev）` / `08 Muse 口型（muse）` 两个可选阶段（默认关）。
- 截图：`tmp/ui/v49/agent_muse_video.png`、`agent_muse_sync.png`、`agent_muse_video_stub.png`、`agent_season_muse.png`。
