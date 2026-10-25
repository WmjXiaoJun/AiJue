"""LLM 内容生成测试：provider 抽象 / 配置掩码 / 台本生成 / 错误分支。"""

from __future__ import annotations

import json
import uuid

import httpx
import pytest

from app import llm


def _new_project(client):
    return client.post(
        "/api/projects",
        json={"name": f"LLM 测试 {uuid.uuid4().hex[:6]}", "format_id": "roundtable_5min"},
    ).json()


def _mock_env(monkeypatch, provider="mock"):
    monkeypatch.setenv("LLM_PROVIDER", provider)
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)


# --------------------------------------------------------------------------- #
# Provider 抽象
# --------------------------------------------------------------------------- #
def test_mock_provider_text_and_json(monkeypatch):
    _mock_env(monkeypatch)
    text = llm.complete("你好")
    assert text.startswith("mock LLM 回复")
    schema = {
        "type": "object",
        "required": ["title", "segments"],
        "example": {"title": "t", "segments": [{"name": "开场", "lines": [{"speaker": "老周", "text": "hi"}]}]},
        "properties": {},
    }
    raw = llm.complete("写台本", json_schema=schema)
    data = json.loads(raw)
    assert data["title"] == "t"
    assert data["segments"][0]["lines"][0]["speaker"] == "老周"


def test_ollama_provider_request(monkeypatch):
    calls = []

    def fake_request(method, url, headers=None, json=None, timeout=None):
        calls.append({"method": method, "url": url, "payload": json})
        return httpx.Response(
            200,
            json={"message": {"content": "你好世界"}},
            request=httpx.Request(method, url),
        )

    monkeypatch.setattr(llm.httpx, "request", fake_request)
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("LLM_BASE_URL", "http://ollama.test:11434")
    monkeypatch.setenv("LLM_MODEL", "qwen2.5:7b")
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    schema = {"type": "object", "properties": {}, "example": {"title": "不要照抄"}}
    assert llm.complete("hi", json_schema=schema, temperature=0.2) == "你好世界"
    prompt_text = calls[0]["payload"]["messages"][-1]["content"]
    assert "不要照抄" not in prompt_text
    assert calls[0]["url"] == "http://ollama.test:11434/api/chat"
    assert calls[0]["payload"]["model"] == "qwen2.5:7b"
    assert calls[0]["payload"]["stream"] is False
    assert calls[0]["payload"]["format"] == "json"
    assert calls[0]["payload"]["options"]["temperature"] == 0.2


def test_openai_provider_request(monkeypatch):
    calls = []

    def fake_request(method, url, headers=None, json=None, timeout=None):
        calls.append({"url": url, "headers": headers, "payload": json})
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "OpenAI 回复"}}]},
            request=httpx.Request(method, url),
        )

    monkeypatch.setattr(llm.httpx, "request", fake_request)
    monkeypatch.setenv("LLM_PROVIDER", "openai_compatible")
    monkeypatch.setenv("LLM_BASE_URL", "https://api.example.com/v1")
    monkeypatch.setenv("LLM_MODEL", "deepseek-chat")
    monkeypatch.setenv("LLM_API_KEY", "sk-secret")
    schema = {"type": "object", "properties": {}}
    assert llm.complete("hi", json_schema=schema) == "OpenAI 回复"
    assert calls[0]["url"] == "https://api.example.com/v1/chat/completions"
    assert calls[0]["headers"]["Authorization"] == "Bearer sk-secret"
    assert calls[0]["payload"]["response_format"] == {"type": "json_object"}


def test_provider_retry_on_timeout(monkeypatch):
    attempts = []

    def fake_request(*args, **kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            raise httpx.TimeoutException("timeout")
        return httpx.Response(
            200,
            json={"message": {"content": "ok"}},
            request=httpx.Request("POST", "http://x/api/chat"),
        )

    monkeypatch.setattr(llm.httpx, "request", fake_request)
    monkeypatch.setattr(llm.time, "sleep", lambda seconds: None)
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("LLM_BASE_URL", "http://x")
    assert llm.complete("hi") == "ok"
    assert len(attempts) == 2


def test_provider_auth_error_no_retry(monkeypatch):
    attempts = []

    def fake_request(*args, **kwargs):
        attempts.append(1)
        return httpx.Response(
            401,
            text="invalid api key",
            request=httpx.Request("POST", "http://x/chat/completions"),
        )

    monkeypatch.setattr(llm.httpx, "request", fake_request)
    monkeypatch.setenv("LLM_PROVIDER", "openai_compatible")
    monkeypatch.setenv("LLM_BASE_URL", "http://x/v1")
    monkeypatch.setenv("LLM_API_KEY", "bad-key")
    with pytest.raises(llm.LLMError) as excinfo:
        llm.complete("hi")
    assert "HTTP 401" in str(excinfo.value)
    assert len(attempts) == 1


def test_openai_missing_key(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai_compatible")
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    with pytest.raises(llm.LLMError) as excinfo:
        llm.complete("hi")
    assert "LLM_API_KEY" in str(excinfo.value)


def test_test_connection_mock(monkeypatch):
    _mock_env(monkeypatch)
    result = llm.test_connection()
    assert result["ok"] is True
    assert result["provider"] == "mock"
    assert result["error"] is None


# --------------------------------------------------------------------------- #
# API 配置 / 掩码
# --------------------------------------------------------------------------- #
def test_llm_config_and_settings_masking(client):
    from app import config

    baseline = client.get("/api/llm/config").json()
    assert baseline["provider"] in llm.SUPPORTED_PROVIDERS
    assert "api_key" not in baseline
    try:
        patched = client.patch(
            "/api/settings",
            json={
                "llm_provider": "mock",
                "llm_base_url": "http://llm.test:9999/v1",
                "llm_model": "test-model",
                "llm_api_key": "sk-test-secret-1234",
            },
        )
        assert patched.status_code == 200, patched.text
        settings = patched.json()
        assert settings["llm_provider"] == "mock"
        assert settings["llm_base_url"] == "http://llm.test:9999/v1"
        assert settings["llm_model"] == "test-model"
        assert settings["llm_api_key_configured"] is True
        assert "sk-test-secret-1234" not in json.dumps(settings)
        config_data = client.get("/api/llm/config").json()
        assert config_data["has_api_key"] is True
        assert config_data["api_key_masked"] == "sk-t***1234"
        assert "sk-test-secret-1234" not in json.dumps(config_data)
        assert config.LLM_ENV_PATH.is_file()
        content = config.LLM_ENV_PATH.read_text(encoding="utf-8")
        assert "LLM_PROVIDER=mock" in content
        assert "sk-test-secret-1234" in content  # 只落本地 env 文件
        assert client.patch("/api/settings", json={"llm_provider": "nope"}).status_code == 400
        assert client.patch(
            "/api/settings", json={"llm_base_url": "ftp://bad"}
        ).status_code == 400
        assert client.patch("/api/settings", json={"llm_model": "   "}).status_code == 400
    finally:
        client.patch(
            "/api/settings",
            json={
                "llm_provider": baseline["provider"],
                "llm_base_url": baseline["base_url"],
                "llm_model": baseline["model"],
                "llm_api_key": "",
            },
        )
        config.LLM_ENV_PATH.unlink(missing_ok=True)
    after = client.get("/api/llm/config").json()
    assert after["has_api_key"] is False


def test_llm_test_endpoint(client, monkeypatch):
    _mock_env(monkeypatch)
    response = client.post("/api/llm/test", json={})
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["provider"] == "mock"
    assert "api_key" not in response.text.lower()
    assert client.post("/api/llm/test", json={"provider": "bad"}).status_code == 400


def test_llm_config_env_priority(client, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("LLM_BASE_URL", "http://env-llm:1234")
    monkeypatch.setenv("LLM_MODEL", "env-model")
    data = client.get("/api/llm/config").json()
    assert data["provider"] == "mock"
    assert data["base_url"] == "http://env-llm:1234"
    assert data["model"] == "env-model"


# --------------------------------------------------------------------------- #
# 台本生成
# --------------------------------------------------------------------------- #
def test_generate_script_with_mock_provider(client, monkeypatch):
    _mock_env(monkeypatch)
    project = _new_project(client)
    response = client.post(
        f"/api/projects/{project['id']}/generate-script",
        json={"topic": "AI 会取代综艺导演吗", "duration": 60, "tone": "犀利"},
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["provider"] == "mock"
    assert data["title"]
    assert "[开场]" in data["script"]
    assert "[正片]" in data["script"]
    assert "[结尾]" in data["script"]
    assert len(data["segments"]) == 3
    assert all(segment["lines"] for segment in data["segments"])
    assert "：" in data["script"]


def test_generate_script_template_validation(client, monkeypatch):
    _mock_env(monkeypatch)
    project = _new_project(client)
    ok = client.post(
        f"/api/projects/{project['id']}/generate-script",
        json={"topic": "测试", "template_id": "roundtable_5min"},
    )
    assert ok.status_code == 200
    bad = client.post(
        f"/api/projects/{project['id']}/generate-script",
        json={"topic": "测试", "template_id": "not_exists"},
    )
    assert bad.status_code == 400


def test_generate_script_retry_then_success(client, monkeypatch):
    project = _new_project(client)
    calls = []
    good = {
        "title": "重试成功",
        "segments": [
            {"name": "开场", "lines": [{"speaker": "老周", "text": "第一句"}]},
            {"name": "正片", "lines": [{"speaker": "阿栗", "text": "第二句"}]},
            {"name": "结尾", "lines": [{"speaker": "大魏", "text": "第三句"}]},
        ],
    }

    def fake_complete(prompt, json_schema=None, temperature=0.7, system=None, provider=None):
        calls.append(temperature)
        if len(calls) == 1:
            return "这不是 JSON"
        return json.dumps(good, ensure_ascii=False)

    monkeypatch.setattr(llm, "complete", fake_complete)
    response = client.post(
        f"/api/projects/{project['id']}/generate-script", json={"topic": "重试"}
    )
    assert response.status_code == 200, response.text
    assert len(calls) == 2
    assert calls[0] == 0.8
    assert calls[1] == 0.4


def test_generate_script_invalid_twice_502(client, monkeypatch):
    project = _new_project(client)
    calls = []

    def fake_complete(*args, **kwargs):
        calls.append(1)
        return "not json"

    monkeypatch.setattr(llm, "complete", fake_complete)
    response = client.post(
        f"/api/projects/{project['id']}/generate-script", json={"topic": "失败"}
    )
    assert response.status_code == 502
    assert "已重试" in response.json()["detail"]
    assert len(calls) == 2


def test_generate_script_llm_error_502(client, monkeypatch):
    project = _new_project(client)

    def fake_complete(*args, **kwargs):
        raise llm.LLMError("ollama 请求超时", provider="ollama")

    monkeypatch.setattr(llm, "complete", fake_complete)
    response = client.post(
        f"/api/projects/{project['id']}/generate-script", json={"topic": "失败"}
    )
    assert response.status_code == 502
    assert "LLM 调用失败" in response.json()["detail"]
    assert client.post(
        "/api/projects/not_exists/generate-script", json={"topic": "x"}
    ).status_code == 404


def test_generate_script_validation_topic(client):
    project = _new_project(client)
    assert client.post(
        f"/api/projects/{project['id']}/generate-script", json={"topic": ""}
    ).status_code == 422
    assert client.post(
        f"/api/projects/{project['id']}/generate-script",
        json={"topic": "x", "duration": 5},
    ).status_code == 422
