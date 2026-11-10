"""LLM provider 抽象（contract 规划：本地 Ollama + OpenAI 兼容 API + mock）。

- complete(prompt, json_schema=None, temperature=0.7, system=None)
- provider: ollama（/api/chat）、openai_compatible（/v1/chat/completions，Bearer key）、mock
- 配置：backend/data/llm.env（LLM_PROVIDER / LLM_BASE_URL / LLM_MODEL / LLM_API_KEY），
  环境变量优先；API Key 只存本地文件/环境变量，不进日志/响应。
- 超时与重试：网络超时/连接失败/5xx 自动重试一次（0.6s 退避）；4xx（鉴权/参数）不重试，错误信息可读。
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger(__name__)

DEFAULT_OLLAMA_BASE_URL = "http://127.0.0.1:11434"
DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "qwen2.5:7b"
SUPPORTED_PROVIDERS = ("ollama", "openai_compatible", "mock")
DEFAULT_TIMEOUT_SECONDS = 120.0
CONNECT_TIMEOUT_SECONDS = 8.0
RETRY_BACKOFF_SECONDS = 0.6


class LLMError(RuntimeError):
    """provider 调用失败（错误信息可直接展示给用户）。"""

    def __init__(self, message: str, *, provider: str = "", status_code: Optional[int] = None):
        super().__init__(message)
        self.provider = provider
        self.status_code = status_code


@dataclass
class LLMConfig:
    provider: str = "ollama"
    base_url: str = DEFAULT_OLLAMA_BASE_URL
    model: str = DEFAULT_MODEL
    api_key: str = ""
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def load_config() -> LLMConfig:
    """读取 LLM 配置：环境变量 LLM_* 优先，其次 backend/data/llm.env，最后默认值。"""
    from . import config as app_config

    file_values = app_config.llm_file_values()
    provider = (_env("LLM_PROVIDER") or file_values.get("LLM_PROVIDER") or "ollama").lower()
    if provider not in SUPPORTED_PROVIDERS:
        provider = "ollama"
    default_base = DEFAULT_OPENAI_BASE_URL if provider == "openai_compatible" else DEFAULT_OLLAMA_BASE_URL
    base_url = (
        _env("LLM_BASE_URL")
        or file_values.get("LLM_BASE_URL")
        or default_base
    ).rstrip("/")
    model = _env("LLM_MODEL") or file_values.get("LLM_MODEL") or DEFAULT_MODEL
    api_key = _env("LLM_API_KEY") or file_values.get("LLM_API_KEY") or ""
    return LLMConfig(
        provider=provider,
        base_url=base_url,
        model=model,
        api_key=api_key,
    )


def _strip_examples(value: Any) -> Any:
    """给真实模型的 schema 不包含 example，避免模型直接照抄示例。"""
    if isinstance(value, dict):
        return {key: _strip_examples(item) for key, item in value.items() if key != "example"}
    if isinstance(value, list):
        return [_strip_examples(item) for item in value]
    return value


def _chat_messages(prompt: str, system: Optional[str], json_schema: Optional[Dict[str, Any]]) -> list:
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    content = prompt
    if json_schema is not None:
        content = (
            prompt
            + "\n\n只输出一个合法 JSON 对象，不要 Markdown 代码块、不要额外解释。JSON Schema：\n"
            + json.dumps(_strip_examples(json_schema), ensure_ascii=False)
        )
    messages.append({"role": "user", "content": content})
    return messages


def _request_json(method: str, url: str, *, headers: Dict[str, str], payload: Dict[str, Any], provider: str) -> Dict[str, Any]:
    """带一次重试的 HTTP JSON 请求（5xx/超时/连接异常重试）。"""
    last_error: Optional[Exception] = None
    for attempt in range(2):
        try:
            response = httpx.request(
                method,
                url,
                headers=headers,
                json=payload,
                timeout=httpx.Timeout(
                    DEFAULT_TIMEOUT_SECONDS,
                    connect=CONNECT_TIMEOUT_SECONDS,
                ),
            )
            if response.status_code >= 500:
                last_error = LLMError(
                    f"{provider} 服务暂不可用（HTTP {response.status_code}）",
                    provider=provider,
                    status_code=response.status_code,
                )
            elif response.status_code >= 400:
                text = (response.text or "").strip()[:400]
                raise LLMError(
                    f"{provider} 调用失败（HTTP {response.status_code}）：{text}",
                    provider=provider,
                    status_code=response.status_code,
                )
            else:
                try:
                    data = response.json()
                except ValueError as exc:
                    raise LLMError(f"{provider} 返回了非 JSON 响应", provider=provider) from exc
                if not isinstance(data, dict):
                    raise LLMError(f"{provider} 返回格式异常", provider=provider)
                return data
        except LLMError:
            raise
        except httpx.TimeoutException as exc:
            last_error = LLMError(f"{provider} 请求超时（>{DEFAULT_TIMEOUT_SECONDS:.0f}s）", provider=provider)
        except httpx.HTTPError as exc:
            last_error = LLMError(f"{provider} 网络错误：{exc}", provider=provider)
        if attempt == 0 and last_error is not None:
            logger.warning("LLM 请求失败，重试一次: %s", last_error)
            time.sleep(RETRY_BACKOFF_SECONDS)
    assert last_error is not None
    raise last_error


def _complete_ollama(prompt: str, json_schema: Optional[Dict[str, Any]], temperature: float, system: Optional[str], cfg: LLMConfig) -> str:
    payload: Dict[str, Any] = {
        "model": cfg.model,
        "messages": _chat_messages(prompt, system, json_schema),
        "stream": False,
        "options": {"temperature": max(0.0, min(2.0, float(temperature)))},
    }
    if json_schema is not None:
        payload["format"] = "json"
    data = _request_json(
        "POST",
        f"{cfg.base_url}/api/chat",
        headers={"Content-Type": "application/json"},
        payload=payload,
        provider="ollama",
    )
    content = ((data.get("message") or {}).get("content")) or ""
    if not isinstance(content, str) or not content.strip():
        raise LLMError("ollama 返回内容为空", provider="ollama")
    return content.strip()


def _complete_openai(prompt: str, json_schema: Optional[Dict[str, Any]], temperature: float, system: Optional[str], cfg: LLMConfig) -> str:
    if not cfg.api_key:
        raise LLMError("openai_compatible 需要配置 LLM_API_KEY", provider="openai_compatible")
    payload: Dict[str, Any] = {
        "model": cfg.model,
        "messages": _chat_messages(prompt, system, json_schema),
        "temperature": max(0.0, min(2.0, float(temperature))),
        "stream": False,
    }
    if json_schema is not None:
        payload["response_format"] = {"type": "json_object"}
    data = _request_json(
        "POST",
        f"{cfg.base_url}/chat/completions",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {cfg.api_key}",
        },
        payload=payload,
        provider="openai_compatible",
    )
    choices = data.get("choices") or []
    if not choices:
        raise LLMError("openai_compatible 返回缺少 choices", provider="openai_compatible")
    content = ((choices[0].get("message") or {}).get("content")) or ""
    if not isinstance(content, str) or not content.strip():
        raise LLMError("openai_compatible 返回内容为空", provider="openai_compatible")
    return content.strip()


def _mock_json_for_schema(schema: Dict[str, Any]) -> Any:
    if "example" in schema:
        return schema["example"]
    schema_type = schema.get("type")
    if schema_type == "object" or "properties" in schema:
        properties = schema.get("properties") or {}
        required = schema.get("required") or list(properties.keys())
        return {
            key: _mock_json_for_schema(properties.get(key) or {})
            for key in required
            if key in properties
        }
    if schema_type == "array":
        return [_mock_json_for_schema(schema.get("items") or {})]
    if schema_type in ("number", "integer"):
        return 0
    if schema_type == "boolean":
        return False
    return "mock"


def _complete_mock(prompt: str, json_schema: Optional[Dict[str, Any]], temperature: float, system: Optional[str]) -> str:
    if json_schema is not None:
        return json.dumps(_mock_json_for_schema(json_schema), ensure_ascii=False)
    return "mock LLM 回复：" + prompt.strip()[:200]


def get_provider(provider: Optional[str] = None):
    cfg = load_config()
    if provider:
        cfg.provider = provider
    return cfg


def complete(
    prompt: str,
    json_schema: Optional[Dict[str, Any]] = None,
    temperature: float = 0.7,
    system: Optional[str] = None,
    provider: Optional[str] = None,
    cfg_override: Optional[LLMConfig] = None,
) -> str:
    """调用当前配置的 LLM，返回文本（json_schema 时调用方负责解析/校验）。

    ``cfg_override`` 仅供 `test_connection` 这类"用临时配置试一次"的调用方使用；
    常规调用不传，始终以 `load_config()` 为准。
    """
    cfg = cfg_override or load_config()
    if provider:
        cfg.provider = str(provider).strip().lower()
    if not (prompt or "").strip():
        raise LLMError("prompt 不能为空")
    if cfg.provider == "mock":
        return _complete_mock(prompt, json_schema, temperature, system)
    if cfg.provider == "ollama":
        return _complete_ollama(prompt, json_schema, temperature, system, cfg)
    if cfg.provider == "openai_compatible":
        return _complete_openai(prompt, json_schema, temperature, system, cfg)
    raise LLMError(f"不支持的 LLM provider: {cfg.provider}", provider=cfg.provider)


def test_connection(provider: Optional[str] = None,
                    base_url: Optional[str] = None,
                    model: Optional[str] = None,
                    api_key: Optional[str] = None) -> Dict[str, Any]:
    """连接测试：发一条最小 prompt，返回 ok/provider/model/latency/error。

    覆盖值只作用于本次测试，不写配置文件。安全约束：调用方指定 ``base_url`` 却没给
    ``api_key`` 时，**不带**已保存的 Key（避免"改地址 → 测试 → Key 外泄"）。
    """
    cfg = load_config()
    if provider:
        cfg.provider = str(provider).strip().lower()
    if base_url:
        cfg.base_url = str(base_url).strip().rstrip("/")
        if api_key is None:
            cfg.api_key = ""
    if model:
        cfg.model = str(model).strip()
    if api_key is not None:
        cfg.api_key = str(api_key).strip()
    started = time.monotonic()
    try:
        text = complete("请只回复：OK", temperature=0.0, cfg_override=cfg)
        return {
            "ok": True,
            "provider": cfg.provider,
            "model": cfg.model,
            "latency_ms": int((time.monotonic() - started) * 1000),
            "reply": text[:100],
            "error": None,
        }
    except LLMError as exc:
        return {
            "ok": False,
            "provider": cfg.provider,
            "model": cfg.model,
            "latency_ms": int((time.monotonic() - started) * 1000),
            "reply": None,
            "error": str(exc),
        }
