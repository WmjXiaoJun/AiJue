# 一键发布（海外平台）v1

## 背景 / 参考
参考 MoneyPrinterTurbo（harry0703/MoneyPrinterTurbo）的做法：它通过 **Upload-Post（upload-post.com）** 第三方服务完成「生成后一键跨平台发布」到 TikTok / Instagram / YouTube Shorts。
本平台同样采用 Upload-Post 作为统一发布网关：用户在 upload-post.com 注册账号、连接自己的 TikTok/Instagram/YouTube 账号，拿到 API Key 与 username 即可一键发布。

## Upload-Post API（实测参考实现）
- Base: https://api.upload-post.com
- 上传: POST /api/upload（multipart/form-data）
  - headers: Authorization: Apikey <UPLOAD_POST_API_KEY>
  - fields: video(文件), user=<username>, request_id=<uuid>, title, privacy_level, platform[]=tiktok|instagram|youtube
  - YouTube 额外: selfDeclaredMadeForKids=true|false, privacyStatus=public|unlisted|private, youtube_title(≤100), youtube_description, tags[]=…, containsSyntheticMedia=true
  - 返回: {success:true, request_id} 或 {success:true, results:{platform:{success,...}}}; 大文件可能转后台, 需轮询
- 状态: GET /api/uploadposts/status?request_id=…（Authorization 同上）；status: completed|failed|pending|queued|processing
- 失败/超时/网络异常时上传结果可能不确定：返回 request_id 并要求人工确认，不得自动重试（避免重复发布）。

## 配置（Settings / backend/data/publish.env，均可）
- upload_post_api_key（secret，GET 只返回是否已配置）
- upload_post_username（upload-post 的 profile 用户名）
- upload_post_enabled（bool）
- upload_post_platforms（默认 ["youtube","tiktok","instagram"]）
- upload_post_youtube_privacy_status（默认 "unlisted"，安全）
- upload_post_youtube_made_for_kids（默认 false）
- 品牌默认：publish_brand_title = "造片局 · AI微综艺"；发布文案默认包含 #造片局 #AI微综艺 #Zaopianju #AIVariety

## 后端 API
- GET /api/publish/config → {configured, username_masked, enabled, platforms, youtube_privacy, limits}
- PATCH /api/settings 支持 upload_post_*（api_key 传空串可清除；GET 永不返回明文）
- POST /api/episodes/{eid}/publish
  body: {platforms?:string[], lang?:"zh"|"en"|"es"|"ja"|"ko"|"pt", title?, description?, hashtags?:string[], privacy?:string, dry_run?:boolean, version?:"final"|"dynamic"}
  - video 选择：lang 非 zh → outputs/episodes/{eid}/localized/{lang}/final.mp4；zh/未给 → outputs/episodes/{eid}/final.mp4（version=dynamic 时优先 final_dynamic.mp4）
  - 元数据默认：localized/{lang}/meta.json 的 publish.title/description/hashtags；否者 episode.title + 品牌文案
  - 创建 Job(type=publish)，调用 publish CLI；dry_run=true 只做预检（文件/时长/平台限制/配置），不联网
  - Job done 记录 request_id/平台结果/错误；失败可读；不自动重试
- GET /api/episodes/{eid}/publishes → 发布记录列表；GET /api/publishes/{id}
- 数据表 PublishRecord：id/episode_id/platforms/lang/video_url/title/status/request_id/meta/error/created_at

## CLI（后端任务调用）
~~~powershell
python -m publish.cli --video <mp4> --title <title> --platforms youtube,tiktok,instagram --username <user> --dry-run
python -m publish.cli --video <mp4> --title <title> --platforms youtube --username <user>
~~~
- key 通过环境变量 UPLOAD_POST_API_KEY 传入（不要写进 job json/日志）；dry-run 不要求 key，但缺少配置时报告需要配置
- 末行输出 JSON：{ok, dry_run, video, duration, size_mb, platforms, configured, request_id?, results?, error?}

## 前端
- 成片房「一键发布（海外）」卡片：品牌「造片局 · AI微综艺」；平台多选（YouTube/TikTok/Instagram，显示时长/尺寸限制）；语言版本（中文/EN/ES/JA/KO/PT）；标题/描述/Hashtags（预填发布元数据，可编辑）；YouTube 可见性；「预检」「发布」按钮。
- 未配置时显示引导：去 upload-post.com 注册 → 连接 YouTube/TikTok/Instagram → 复制 API Key + username → 设置里填入；不伪造成功。
- 发布记录列表：平台/语言/状态/request_id/错误/时间。

## 平台限制（预检提示）
- YouTube：长视频支持；建议 privacy=unlisted 先审后公开；标题≤100；描述≤5000。
- TikTok：单个视频 ≤10 分钟（本平台 5-10 分钟可发）；需 TikTok 账号在 upload-post 授权。
- Instagram Reels：建议 ≤15 分钟（当前 IG 上限），竖屏最佳；横屏 16:9 会加黑边，预检提示。

## 合规
- AI 生成内容需标注：YouTube 传 containsSyntheticMedia=true；发布文案加 #AIGenerated。
- 用户需自行确认平台条款、音乐/字体/素材版权；品牌账号统一为「造片局 · AI微综艺」。

## 测试
- 单元测试 mock HTTP：上传成功即时返回、后台任务轮询到 completed、平台失败、网络异常不重试、dry-run、配置掩码、缺配置报错。
- 后端 API：enqueue/成功回填/失败状态/列表/设置校验；不进行真实上传（无凭据）。
