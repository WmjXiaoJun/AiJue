# V27：发布包附带竖版 9:16（TikTok / Instagram 适配）

> 当前发布包只有 1280×720 16:9 母版，TikTok/IG 需要 9:16。本轮在发布包里自动生成竖版：**模糊背景填充（blur-pad）**，完整保留 16:9 画面内容（上下模糊留白），并附竖版封面。

## 1. 后端（发布包扩展）
### POST /api/cinema/templates/{tid}/publish-package 增加 body 字段
`{platform?: ..., force?: bool, vertical?: bool}`（默认：platform 为 tiktok/instagram 时 vertical=true；其它平台 false）
- `vertical=true` 时在 zip 内额外生成：
  - `video/final_vertical.mp4`（**1080×1920，30/25fps 沿用源帧率，H.264+AAC**）
    - ffmpeg 滤镜：背景 = 原片 scale 填满 1080×1920 + `gblur=sigma=30`；前景 = 原片 scale=1080:608；overlay 居中；输出 yuv420p
  - `cover/cover_vertical.jpg`（poster 同法生成 1080×1920 竖版封面）
  - metadata 增加 `vertical: {file:"video/final_vertical.mp4", resolution:"1080x1920", mode:"blur_pad"}`
  - README 追加竖版说明（TikTok/IG 可直接上传竖版）
- 也支持 `vertical_mode`（可选 `blur_pad` 默认 / `crop` 中心裁切）——先实现 blur_pad，crop 作为可选
- 包文件名区分：`<tid>_<platform>_v.zip`？保持不变但内容多两个文件；force 可重建
- 校验：源 final 存在；竖版生成失败 → warning，不阻塞主包（仍返回 16:9 包）
- 自测：对 custom_tpl_v23_shop 以 platform=tiktok&vertical=true 构建 → zip 含 final_vertical.mp4（ffprobe 1080x1920 + 音轨）与 cover_vertical.jpg（1080x1920），metadata.vertical 正确；vertical=false 不含；force 重建；写 tmp/v27/backend_selftest.md

## 2. 前端（发布弹层）
- 弹层平台选择下方增加「**附带竖版 9:16（TikTok/IG）**」checkbox：平台选 TikTok/Instagram 时默认勾选，切回其它平台自动取消（可手动改）
- 请求带 `vertical: true`；文件清单显示 `video/final_vertical.mp4`、`cover/cover_vertical.jpg`（带「竖版」badge + 尺寸提示）
- 平台提醒文案更新：勾选竖版后「可直接上传 TikTok/IG；竖版为模糊背景填充，完整保留画面」
- 「重新打包」按当前 checkbox 重新请求（force）
- 截图 tmp/v27/ui/：勾选竖版、文件清单含竖版、竖版预览提示、取消竖版对比

## 3. 验收
1. API：tiktok+vertical 包内竖版视频 1080×1920 有音轨、竖版封面 1080×1920，metadata 正确；其它平台不含
2. UI：勾选后导出包文件清单出现竖版两个文件；下载解压可播放
3. `tsc` 0 / build 通过 / console 无 JS error；截图 tmp/v27/
4. docs/V27 + commit

## 4. 分工
- **tpl-backend**：vertical 参数 + ffmpeg 竖版视频/封面 + metadata/README + 自测
- **tpl-frontend**：弹层 checkbox/请求参数/文件清单竖版展示/文案 + 截图
- **Lead**：真实导出检查（ffprobe 竖版/封面）、截图、docs、commit


## 5. 交付结果（2026-11-03 20:00）

### 后端（23/23 自测 PASS，V26 回归 26/26 PASS）
- `cinema_templates.py`：`_build_publish_package(tid, platform, force, vertical, vertical_mode)`；`_publish_build_vertical`（ffmpeg blur-pad：bg 填满 1080×1920 + gblur sigma=30，fg 1080×608 居中 overlay，yuv420p/H.264/AAC/25fps；竖版封面同法）
- `PublishPackageBody` 增加 `vertical?`（tiktok/instagram 默认 true）与 `vertical_mode`（当前仅 blur_pad，crop→400）
- zip 新增 `video/final_vertical.mp4`、`cover/cover_vertical.jpg`；`metadata.json` 增 `vertical{file,resolution,mode,width,height,fps,duration_sec,size_bytes,cover_file}`；README 增竖版说明
- 缓存按 vertical 开关/模式校验，不匹配自动重建（同一 zip 名）；竖版失败仅 warning 不阻塞 16:9 主包
- `/media` 的 `.zip` 固定 `Content-Type: application/zip`（修 Windows 默认 x-zip-compressed，V26 验收项）
- 实测（custom_tpl_v23_shop tiktok+vertical+force）：构建 **3.7s**，zip **9,080,387B / 9 文件**；竖版视频 ffprobe **1080×1920 / H.264 / 25fps / AAC / 15.08s**；竖版封面 **1080×1920**；metadata.vertical 正确；vertical=false → 7 文件无竖版
- 8090 PID **25552**，health ok

### 前端
- 发布包弹层新增「附带竖版 9:16（TikTok/IG）」checkbox：TikTok/Instagram 默认勾选（标「平台默认」），切回通用/YouTube 自动取消（可手动改）
- 勾选后显示竖版预览提示；请求带 `vertical:true, vertical_mode:"blur_pad"`；loading 显示「（含竖版 9:16）」
- 文件清单：竖版文件带绿色「竖版」badge + 1080×1920 + 高亮行；summary 显示「竖版 已附带/未附带」
- 平台提醒更新；重新打包/重试按当前勾选 force；复制元数据含 vertical 信息
- 实测：TikTok 真实导出 9 文件（竖版视频 + 封面），切 YouTube 自动取消，取消勾选重打包回到 7 文件；console 无 JS error

### Lead 独立验收
- E2E（`tmp/v27/e2e_vertical.py`）：TikTok vertical 包 → zip 9 文件；竖版 ffprobe 1080×1920 + audio + 15.08s；封面 1080×1920；metadata.vertical 完整
- 竖版抽帧 `tmp/v27/vertical_frame.jpg`：完整 16:9 画面居中 + 上下模糊背景，字幕保留
- UI（`tmp/v27/lead/01_tiktok_vertical_modal.png`）：生产构建点「发布包」→ 切 TikTok → 清单含 `video/final_vertical.mp4` 与竖版 badge
- 截图：`tmp/v27/ui/`（4 张）、`tmp/v27/lead/`（1 张）

### 使用
1. 成片库 →「发布包」→ 选 TikTok / Instagram（竖版默认勾选）→ 下载 zip
2. zip 内 `video/final_vertical.mp4` 直接传 TikTok/IG；`cover/cover_vertical.jpg` 作竖版封面；16:9 母版仍在
3. 平台默认：TikTok/IG 带竖版；YouTube/通用不带（可手动勾选）

### 遗留
- vertical_mode=crop 未实现（当前 blur_pad）；竖版前景固定 1080×608 居中
- 同平台切换竖版会覆盖同一 zip（需要两版请分别下载）
- 竖版包约 9MB（原 3.6MB），发布包保留最近 5 个
