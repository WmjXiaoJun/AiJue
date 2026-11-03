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
