"""ComfyUI 适配器单元测试（httpx.MockTransport，不真连服务器）。"""

from __future__ import annotations

import json

import httpx
import pytest

from app.comfyui import (
    ComfyUIError,
    ComfyUIVideoClient,
    build_workflow,
    load_workflow_template,
    pick_video_file,
)


def test_build_workflow_substitutes_types():
    template = {
        "1": {
            "class_type": "MiniMaxH3",
            "inputs": {
                "prompt": "{{prompt}}",
                "width": "{{width}}",
                "seed": "{{seed}}",
                "keep": "static",
                "mix": "prefix-{{width}}-suffix",
            },
        },
        "2": ["{{prompt}}", 1],
    }
    result = build_workflow(template, {"prompt": "AI 综艺", "width": 1280, "seed": 42})
    inputs = result["1"]["inputs"]
    assert inputs["prompt"] == "AI 综艺"
    assert inputs["width"] == 1280
    assert inputs["seed"] == 42
    assert inputs["keep"] == "static"
    assert inputs["mix"] == "prefix-1280-suffix"
    assert result["2"] == ["AI 综艺", 1]


def test_load_workflow_template(tmp_path):
    inline = load_workflow_template('{"a": {"inputs": {"p": "{{prompt}}"}}}')
    assert inline["a"]["inputs"]["p"] == "{{prompt}}"
    path = tmp_path / "wf.json"
    path.write_text(json.dumps({"b": 1}), encoding="utf-8")
    assert load_workflow_template(str(path)) == {"b": 1}
    assert load_workflow_template(path) == {"b": 1}
    with pytest.raises(ComfyUIError):
        load_workflow_template("definitely_missing_workflow.json")


def test_pick_video_file_prefers_video():
    outputs = {
        "12": {
            "images": [{"filename": "frame.png", "type": "output"}],
            "gifs": [{"filename": "out.mp4", "subfolder": "v", "type": "output"}],
        }
    }
    assert pick_video_file(outputs) == ("out.mp4", "v", "output")
    with pytest.raises(ComfyUIError):
        pick_video_file({})


def _mock_client(handler, timeout=2.0, poll_interval=0.01):
    return ComfyUIVideoClient(
        "http://comfy.local",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        poll_interval=poll_interval,
        timeout=timeout,
    )


def test_generate_video_submit_poll_download():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, dict(request.url.params)))
        if request.url.path == "/prompt":
            assert request.method == "POST"
            payload = json.loads(request.content)
            assert payload["prompt"]["1"]["class_type"] == "FakeH3"
            assert payload["client_id"]
            return httpx.Response(200, json={"prompt_id": "pid-1"})
        if request.url.path == "/history/pid-1":
            return httpx.Response(
                200,
                json={
                    "pid-1": {
                        "status": {"status_str": "success", "completed": True},
                        "outputs": {
                            "7": {
                                "videos": [
                                    {"filename": "shot.mp4", "subfolder": "clips", "type": "output"}
                                ]
                            }
                        },
                    }
                },
            )
        if request.url.path == "/view":
            assert request.url.params["filename"] == "shot.mp4"
            assert request.url.params["subfolder"] == "clips"
            assert request.url.params["type"] == "output"
            return httpx.Response(200, content=b"FAKE-VIDEO-BYTES")
        return httpx.Response(404)

    client = _mock_client(handler)
    workflow = {"1": {"class_type": "FakeH3", "inputs": {"prompt": "{{prompt}}"}}}
    data = client.generate_video(workflow, {"prompt": "测试镜头"})
    assert data == b"FAKE-VIDEO-BYTES"
    assert seen[0][1] == "/prompt"
    assert any(item[1] == "/history/pid-1" for item in seen)
    assert seen[-1][1] == "/view"


def test_generate_video_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    client = _mock_client(handler)
    with pytest.raises(httpx.HTTPStatusError):
        client.submit_prompt({"x": 1})


def test_generate_video_submit_error_payload():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"error": {"type": "invalid_prompt"}})

    client = _mock_client(handler)
    with pytest.raises(ComfyUIError):
        client.submit_prompt({"x": 1})


def test_generate_video_timeout():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/prompt":
            return httpx.Response(200, json={"prompt_id": "pid-timeout"})
        return httpx.Response(200, json={})

    client = _mock_client(handler, timeout=0.05, poll_interval=0.01)
    with pytest.raises(ComfyUIError):
        client.generate_video({"x": 1}, {})


def test_generate_video_execution_error():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/prompt":
            return httpx.Response(200, json={"prompt_id": "pid-err"})
        return httpx.Response(
            200,
            json={"pid-err": {"status": {"status_str": "error", "messages": ["CUDA OOM"]}}},
        )

    client = _mock_client(handler)
    with pytest.raises(ComfyUIError):
        client.generate_video({"x": 1}, {})
