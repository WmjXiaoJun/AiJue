# 一键发布（Upload-Post）操作手册

平台把「生成好的成片」通过 [Upload-Post](https://upload-post.com) 一键发布到 **YouTube / TikTok / Instagram**。
技术契约见 `contract/PUBLISHING.md`；本文是面向使用者/运维的配置、预检、发布与排查手册。

> 安全提示：API Key 只保存在后端 `backend/data/publish.env`（或环境变量 `UPLOAD_POST_API_KEY`），
> 任何 GET 接口都不会返回明文；任务子进程通过环境变量接收 key，不写进 job JSON / 命令行 / 日志。

---

## 1. 注册与连接账号（一次性）

1. 打开 <https://upload-post.com> 注册账号。
2. 在后台 **Connected Accounts / 平台连接** 中依次授权：
   - YouTube（选择要发布的频道）
   - TikTok（需 TikTok 账号授权；单个视频 ≤10 分钟）
   - Instagram（Reels；建议竖屏 9:16）
3. 在 API / Profile 页面复制：
   - **API Key**（upload-post 的 Apikey）
   - **profile username**（发布时的 `user` 字段，不是登录邮箱）

## 2. 在后端填写配置

两种方式（二选一，API Key 相同）：

- 前端「设置 / 一键发布」面板（推荐）
- 直接调用接口：

```powershell
# 填 key（只写 backend/data/publish.env，不落库、不回显）
curl -X PATCH http://127.0.0.1:8090/api/settings ^
  -H "Content-Type: application/json" ^
  -d "{"upload_post_api_key":"<你的 API Key>","upload_post_username":"<profile username>","upload_post_enabled":true}"

# 查看是否已配置（不会返回 key 明文）
curl http://127.0.0.1:8090/api/publish/config
```

也可以手工创建 `backend/data/publish.env`：

```text
UPLOAD_POST_API_KEY=你的key
```

**优先级**：环境变量 `UPLOAD_POST_API_KEY` > `backend/data/publish.env`。
`upload_post_username / enabled / platforms / privacy / made_for_kids / publish_brand_title` 存在 SQLite `settings` 表。

### 配置项一览

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `upload_post_api_key` | 空 | secret，写入 `backend/data/publish.env`；PATCH 传空串可清除；GET 仅返回 `upload_post_api_key_configured` |
| `upload_post_username` | 空 | Upload-Post profile 用户名 |
| `upload_post_enabled` | false | 关闭后不允许真实发布（预检仍可用） |
| `upload_post_platforms` | youtube,tiktok,instagram | 默认平台，可被发布请求覆盖 |
| `upload_post_youtube_privacy_status` | unlisted | `public / unlisted / private`；建议先 unlisted 审后公开 |
| `upload_post_youtube_made_for_kids` | false | YouTube 受众声明 |
| `publish_brand_title` | 造片局 · AI微综艺 | 品牌标题/描述默认值 |

`GET /api/publish/config` 返回：`configured / username_masked / enabled / platforms / youtube_privacy / limits / brand`。
其中 `configured = 有 key + username + enabled=true`。

## 3. 预检（dry_run，不联网）

发布前先预检：检查视频是否存在、时长、大小、分辨率、平台限制、配置是否齐全；**不会发送任何网络请求**。

前端点「预检」，或：

```powershell
curl -X POST http://127.0.0.1:8090/api/episodes/ep_e1015494fa/publish ^
  -H "Content-Type: application/json" ^
  -d "{"platforms":["youtube","tiktok","instagram"],"lang":"zh","dry_run":true}"
```

返回 `Job(type=publish)`；完成后 `GET /api/episodes/{eid}/publishes` 的记录：
- `status=prechecked`
- `meta.warnings`：横屏 Instagram 提示、未配置提示等
- `meta.duration / size_mb`：预检数据

预检同样支持指定 `lang`（en/es/ja/ko/pt）与 `version`（final/dynamic）。

## 4. 正式发布

1. 确认 `GET /api/publish/config` 的 `configured=true`。
2. 选择语言版本与平台：

```powershell
curl -X POST http://127.0.0.1:8090/api/episodes/ep_e1015494fa/publish ^
  -H "Content-Type: application/json" ^
  -d "{"platforms":["youtube","tiktok"],"lang":"en","title":"Will AI Replace Variety Directors?","hashtags":["#AI","#Zaopianju"],"privacy":"unlisted","version":"final"}"
```

请求体字段：

| 字段 | 说明 |
| --- | --- |
| `platforms` | 可选 `youtube/tiktok/instagram`，缺省用 settings |
| `lang` | `zh`（默认）或 `en/es/ja/ko/pt`；非 zh 用 `outputs/episodes/{eid}/localized/{lang}/final.mp4` |
| `version` | `final`（默认）或 `dynamic`（优先 `final_dynamic.mp4`） |
| `title` / `description` / `hashtags` / `privacy` | 可覆盖默认发布元数据 |
| `dry_run` | true 只预检 |

**元数据优先级**：
1. 请求体显式传入；
2. `outputs/episodes/{eid}/localized/{lang}/meta.json` 的 `publish.title / publish.description / publish.hashtags`；
3. 默认：`episode.title` + 品牌文案「造片局 · AI微综艺」+ 标签 `#造片局 #AI微综艺 #Zaopianju #AIVariety #AIGenerated`。

**视频选择**：
- `lang=zh` → `outputs/episodes/{eid}/final.mp4`（`version=dynamic` 优先 `final_dynamic.mp4`）
- `lang!=zh` → `outputs/episodes/{eid}/localized/{lang}/final.mp4`

**任务与记录**：
- 创建 `Job(type=publish)`；成功后 `PublishRecord.status=published`，回填 `request_id`、
  `meta.results`（各平台结果）、`meta.duration/size_mb`，`Job.output_url` 为视频 URL。
- 失败：`status=failed`，`error` 可读；**不自动重试**（避免重复发布）。
- 同一集同参数（平台/语言/版本/dry_run）已有排队/运行任务时复用，防重复点击。
- 发布记录：`GET /api/episodes/{eid}/publishes`（倒序）；单条：`GET /api/publishes/{id}`。

## 5. 平台限制

| 平台 | 限制 | 预检行为 |
| --- | --- | --- |
| YouTube | 长视频支持；标题 ≤100、描述 ≤5000；建议 unlisted 先审后公开 | 标题过长给 warning |
| TikTok | 单个视频 **≤10 分钟**；需账号已授权 | 超时长为 error，阻断发布 |
| Instagram Reels | 建议 ≤15 分钟；竖屏 9:16 最佳 | 横屏给 warning（会加黑边） |

其他：上传文件 >500MB 预检报错；视频文件不存在/为空直接报错。

## 6. 合规

- AI 生成内容需标注：发布到 YouTube 时后端自动传 `containsSyntheticMedia=true`；
  默认文案含 `#AIGenerated`，可按需修改。
- 请自行确认平台条款、音乐/字体/素材版权。
- 品牌账号统一使用「造片局 · AI微综艺」。

## 7. 发布 CLI（后端任务实际调用）

```powershell
# 预检（不联网，不要求 key）
python -m publish.cli --video outputs/episodes/ep_e1015494fa/final.mp4 ^
  --title "AI 做的综艺，观众看得出来吗？" --platforms youtube,tiktok,instagram ^
  --username myprofile --dry-run

# 正式发布（key 从环境变量读取）
$env:UPLOAD_POST_API_KEY="<key>"
python -m publish.cli --video outputs/episodes/ep_e1015494fa/localized/en/final.mp4 ^
  --title "Will AI Replace Variety Directors?" --platforms youtube ^
  --username myprofile --privacy unlisted --hashtags "#AI #Zaopianju"
```

CLI 末行固定输出 JSON：
`{ok, dry_run, video, duration, size_mb, platforms, configured, warnings, errors, request_id?, results?, error?}`。
退出码：0 成功/预检通过；1 上传失败；2 预检不通过。

## 8. 排查

| 现象 | 原因 / 处理 |
| --- | --- |
| 发布接口 400「未配置或未启用」 | 填 `upload_post_api_key` + `upload_post_username`，并设 `upload_post_enabled=true`；`GET /api/publish/config` 确认 `configured=true` |
| 400「待发布视频不存在」 | 先渲染该集（或该语言本地化版本）；检查 `outputs/episodes/{eid}/...` |
| 预检 error「TikTok 单视频上限 10 分钟」 | 剪短到 ≤10 分钟，或本次去掉 TikTok |
| warning「横屏加黑边」 | 正常提示；竖屏版本观感更好 |
| 发布失败且 error 含 `unconfirmed` / `check request_id` | 网络超时/5xx 时远端可能已收到视频；**不要自动重试**，先到 upload-post 后台用 request_id 核对结果，确认未发布再重新发起 |
| 发布失败 401/403 | API Key 无效或未复制完整；重新 PATCH key（GET 不会显示明文） |
| 发布失败「账号未授权」 | 到 upload-post.com 重新连接对应平台账号 |
| 任务失败但页面显示旧记录 | 刷新 `GET /api/episodes/{eid}/publishes` 查看最新 `request_id/error` |
| 服务重启导致发布中断 | 记录会标记 failed 并提示结果不确定；先核对 request_id 再决定是否重新发布 |

## 9. 相关文件

- 契约：`contract/PUBLISHING.md`
- 后端：`backend/app/main.py`（/api/publish/*）、`backend/app/jobs.py`（publish 任务）
- 发布包：`publish/upload_post.py`（HTTP 客户端）、`publish/cli.py`（子进程 CLI）
- 数据表：`publish_records`（SQLite，启动自动建表）
- 测试：`tests/test_publishing.py`
