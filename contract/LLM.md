# LLM 内容生成契约（本地 / API Key）

## 配置
| 项 | env | 说明 |
|---|---|---|
| Provider | LLM_PROVIDER | ollama / openai_compatible / mock |
| Base URL | LLM_BASE_URL | ollama 默认 http://127.0.0.1:11434；OpenAI 兼容填 https://api.xxx/v1 |
| Model | LLM_MODEL | 本地默认 qwen2.5:7b；API 按服务商 |
| API Key | LLM_API_KEY | 仅 API 模式；写入 backend/data/llm.env，响应只回 has_api_key/掩码 |

## 端点
- `GET /api/llm/config` → `{provider, base_url, model, has_api_key, api_key_masked}`；
- `PATCH /api/settings` 接受 `llm_provider/llm_base_url/llm_model/llm_api_key`（空 key 不覆盖）；
- `POST /api/llm/test` → `{ok, latency_ms, model, error?}`；
- `POST /api/projects/{pid}/generate-script` body `{topic, template_id?, duration, tone}` → `{script, segments:[{name,duration,lines:[{speaker,text}]}], provider, model}`；

## Provider 行为
- ollama：`POST {base_url}/api/chat`，`stream=false`，`format=json`（需要时）；
- openai_compatible：`POST {base_url}/chat/completions`，`Authorization: Bearer <key>`，`response_format=json_object`；
- mock：固定台本，供测试/无模型环境；
- 失败：超时 60s，重试 1 次，仍失败返回 502 + 可读错误；key 不进日志/响应。

## 台本 JSON schema（LLM 输出）
~~~json
{
  "title": "string",
  "segments": [
    {"name": "主持人开场", "duration": 40, "lines": [{"speaker": "老周", "text": "..."}]}
  ]
}
~~~
- 校验：segments 非空、每条 line 有 speaker/text、duration 合计与请求时长误差 ≤30%；失败重试一次。

## 安全
- Key 只存 backend/data/llm.env（尽量 0600）；
- 列表/配置接口永不返回明文 key；日志过滤 Authorization/LLM_API_KEY；
- 前端输入框 type=password，保存后显示掩码。
