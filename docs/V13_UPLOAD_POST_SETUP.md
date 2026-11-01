# V13：Upload-Post 配置 + 真实发布链路打通（差社交账号授权）

> 用户提供 Upload-Post Apikey + 邮箱，要求完成「真实发布」闭环。

## 1. 配置完成

| 项 | 结果 |
|---|---|
| API Key | 已写入 `backend/data/publish.env`（`.gitignore` 已忽略，任何 GET 不返回明文） |
| profile username | `default`（通过 `GET https://api.upload-post.com/api/uploadposts/users` 用 key 查询得到；账号为新建，plan=default，limit=2 profiles） |
| enabled | `true`（`PATCH /api/settings`） |
| `GET /api/publish/config` | `configured=true, username_masked=de***, platforms=[youtube,tiktok,instagram], youtube_privacy=unlisted` |
| 发布前检查 ep_001 | render/title/localization/config/video 全 pass，`publishable=true` |

## 2. 真实链路验证

1. **dry-run**：`POST /episodes/ep_001/publish {dry_run:true}` → job done「发布预检完成」，记录 `pub_4e3199d43a` status=prechecked。
2. **真实上传尝试**：`POST /episodes/ep_001/publish {platforms:["youtube"], privacy:"unlisted"}` → job `job_a445380bf4` 失败，记录 `pub_76b1974ef5` failed，request_id `8f8aff03-...`（网络 SSL handshake timeout，后确认是上传大文件尾部超时）。
3. **直连诊断**（用真实 key，不上传成片）：
   - `GET /api/uploadposts/users` → 200，profile `default`，`social_accounts={"tiktok":""}`（空）。
   - `POST /api/upload`（2 秒测试片，platform=youtube）→ 400：
     `None of the requested platforms are valid for profile "default". Profile default has no Youtube account configured`
   - 分别探测 youtube / tiktok / instagram → **三个平台均未连接**（Profile has no <platform> account configured）。

结论：**key 有效、网络可达、发布代码/预检链路正常，唯一缺的是 Upload-Post 账号下的社交平台授权**（需用户在 upload-post.com 后台 OAuth 连接 YouTube / TikTok / Instagram，这是一次性人工操作，无法由代码代办）。

## 3. 代码改进（让阻塞原因可读 + 提前拦截）

- `publish/upload_post.py`：4xx 时解析服务端 JSON，把 `message` / `invalid_platforms` 拼进错误（如「Upload-Post 拒绝：Profile default has no Youtube account configured」），不再只有「HTTP 400」。
- 后端 preflight 新增第 6 项 `accounts`：用 key 查询 Upload-Post profiles 的 `social_accounts`；全未连接 → **fail**「Upload-Post 未连接任何社交账号（需在 upload-post.com 后台 OAuth 连接账号）」；key 未配置则跳过；网络失败降级 warn 不阻断。
- 前端成片房：`accounts` fail 时行尾显示「去连接 ↗」（跳 app.upload-post.com）；存在阻断项时发布按钮禁用并提示「请先修复发布前检查中的阻断项」。

## 4. 待用户操作（最后一步）

1. 登录 upload-post.com 后台 → 连接 **YouTube**（建议先 unlisted）/ TikTok / Instagram（OAuth 授权，各平台可能需要登录账号）。
2. 告诉我连接完成，我立即重跑真实发布（`ep_001` YouTube unlisted，或按你指定的平台/隐私/标题），并回传视频链接 + 发布记录。
3. 如需反复验证，建议先用 `unlisted`，确认成片/标题/简介无误后再改 `public`。

## 5. 证据

- `tmp/v13/finish_accounts_blocked.png`（发布前检查：账号授权 fail + 去连接入口 + 发布按钮禁用）
- `tmp/v13/api_selftest.md`（后端改动自测）
- Upload-Post 探测记录见 `tmp/v13_platform_probe.py` / `tmp/v13_upload_probe2.py`（不含 key）
