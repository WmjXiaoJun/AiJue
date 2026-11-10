# -*- coding: utf-8 -*-
"""2026-11-08 审查整改的回归测试（安全 + 缓存/成本 + 前端合同）。

覆盖：
- CORS 只放行本机来源；
- 改 LLM base_url 时清空旧 Key；/llm/test 的 base_url 覆盖不复用旧 Key；
- /api/v6/muse/sync 的 template 路径穿越；
- 禁用工作流阶段不再阻断下游；
- HQ brief 时长显式超 target 时拒绝全量开渲；
- Muse 部分成功只写 episode.partial.json。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture()
def llm_env_guard():
    """LLM 配置是进程级文件：测试结束必须还原，避免污染 test_llm.py 的"无 key"用例。"""
    from app import config

    path = config.LLM_ENV_PATH
    before = path.read_bytes() if path.is_file() else None
    try:
        yield
    finally:
        if before is None:
            path.unlink(missing_ok=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(before)


def test_cors_rejects_foreign_origin(client):
    r = client.options(
        "/api/settings",
        headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "PATCH",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert r.status_code == 400
    assert r.headers.get("access-control-allow-origin") != "https://evil.example"


def test_cors_allows_local_origin(client):
    r = client.options(
        "/api/settings",
        headers={
            "Origin": "http://127.0.0.1:8090",
            "Access-Control-Request-Method": "PATCH",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") == "http://127.0.0.1:8090"


def test_llm_base_change_clears_saved_api_key(client, monkeypatch, llm_env_guard):
    from app import config

    for name in ("LLM_PROVIDER", "LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    config.save_llm_config({
        "LLM_PROVIDER": "openai_compatible",
        "LLM_BASE_URL": "https://old.example/v1",
        "LLM_MODEL": "m",
        "LLM_API_KEY": "sk-old-should-not-travel",
    })
    r = client.patch("/api/settings", json={"llm_base_url": "https://new.example/v1"})
    assert r.status_code == 200, r.text
    values = config.llm_file_values()
    assert values.get("LLM_BASE_URL") == "https://new.example/v1"
    assert values.get("LLM_API_KEY", "") == ""


def test_llm_base_same_host_keeps_key(client, monkeypatch, llm_env_guard):
    from app import config

    for name in ("LLM_PROVIDER", "LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    config.save_llm_config({
        "LLM_PROVIDER": "openai_compatible",
        "LLM_BASE_URL": "https://api.example/v1",
        "LLM_MODEL": "m",
        "LLM_API_KEY": "sk-keep",
    })
    r = client.patch("/api/settings", json={"llm_base_url": "https://api.example/v2"})
    assert r.status_code == 200, r.text
    assert config.llm_file_values().get("LLM_API_KEY") == "sk-keep"


def test_llm_test_honors_provider_override():
    from app import llm

    # 旧实现 test_connection 改了局部 cfg.provider，但 complete() 内部重新 load_config()
    # 丢掉了覆盖 —— 这里锁定"provider=mock 真的会走 mock"。
    res = llm.test_connection("mock")
    assert res["ok"] is True
    assert res["provider"] == "mock"


def test_llm_test_override_does_not_forward_saved_key(monkeypatch, llm_env_guard):
    from app import config, llm

    for name in ("LLM_PROVIDER", "LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    config.save_llm_config({
        "LLM_PROVIDER": "openai_compatible",
        "LLM_BASE_URL": "https://old.example/v1",
        "LLM_MODEL": "m",
        "LLM_API_KEY": "sk-saved-secret",
    })
    seen: list[dict] = []

    class _Resp:
        status_code = 200
        text = ""

        def json(self):
            return {"choices": [{"message": {"content": "OK"}}]}

    def fake_request(method, url, *, headers=None, json=None, timeout=None):  # noqa: A002
        seen.append({"url": url, "headers": dict(headers or {})})
        return _Resp()

    monkeypatch.setattr(llm.httpx, "request", fake_request)
    # 只改 base_url、不给 key：不能把已保存的 Key 发到新地址
    res = llm.test_connection("openai_compatible", base_url="https://evil.example/v1")
    assert res["ok"] is False
    assert not seen, "不应向新地址发起带旧 Key 的请求"
    # 显式给新 key：用新 key，且请求里不出现旧 key
    res = llm.test_connection("openai_compatible", base_url="https://evil.example/v1",
                              api_key="sk-new")
    assert res["ok"] is True
    assert seen and "evil.example" in seen[-1]["url"]
    assert "sk-saved-secret" not in json.dumps(seen[-1]["headers"])


def test_stop_breeze_honors_keep_env(monkeypatch):
    from pipeline import tts_breeze
    from pipeline.agents import registry

    calls: list[str] = []
    monkeypatch.setattr(tts_breeze, "stop_server", lambda: (calls.append("stop"), True)[1])
    monkeypatch.delenv("BREEZE_TTS_KEEP", raising=False)
    assert registry._stop_breeze() is True
    assert calls == ["stop"]
    calls.clear()
    monkeypatch.setenv("BREEZE_TTS_KEEP", "1")
    assert registry._stop_breeze() is False
    assert calls == []


def test_muse_sync_rejects_template_traversal(client):
    from app import config

    out = Path(config.OUTPUTS_DIR)
    out.mkdir(parents=True, exist_ok=True)
    clip = out / "traversal_clip.mp4"
    audio = out / "traversal_audio.wav"
    clip.write_bytes(b"x" * 2048)
    audio.write_bytes(b"x" * 2048)
    r = client.post("/api/v6/muse/sync", json={
        "clip": str(clip), "audio": str(audio), "template": "../../evil",
    })
    assert r.status_code == 400, r.text
    assert "template" in str(r.json().get("detail", ""))


def test_disabled_stage_does_not_block_dependents(tmp_path, monkeypatch):
    from pipeline.agents import workflow
    from pipeline.agents.orchestrator import run_template

    monkeypatch.setattr(workflow, "WORKFLOW_DIR", tmp_path)
    wf = {
        "version": 2,
        "id": "custom_tpl_v46_duologue",
        "name": "disabled-stage test",
        "stack": "hq_shorts",
        "stages": [
            {"id": "story", "agent": "story_architect", "tool": "check_story",
             "depends": [], "enabled": False, "optional": False, "params": {}},
            {"id": "qc", "agent": "qa", "tool": "evaluate_hq_qc",
             "depends": ["story"], "enabled": True, "optional": False, "params": {}},
        ],
    }
    (tmp_path / "custom_tpl_v46_duologue.json").write_text(
        json.dumps(wf, ensure_ascii=False), encoding="utf-8")
    run = run_template("custom_tpl_v46_duologue", dry_run=True,
                       run_root=tmp_path / "runs")
    statuses = {s["id"]: s["status"] for s in run.stages}
    assert statuses == {"story": "skipped", "qc": "planned"}, statuses
    assert run.status == "planned"


def test_render_2d_hq_rejects_brief_overflow(tmp_path):
    from types import SimpleNamespace

    from pipeline import v5_catalog
    from pipeline.agents import registry

    tid = "custom_tpl_v46_duologue"
    catalog = v5_catalog.load_catalog()
    ctx = SimpleNamespace(template_id=tid, template=catalog["templates"][tid],
                          duration=19.14, force=False, paths={}, log=lambda _m: None)
    with pytest.raises(RuntimeError, match="远超模板 target"):
        registry.render_2d_hq(ctx, {})


def test_muse_render_all_failed_does_not_touch_authoritative_timeline(tmp_path, monkeypatch):
    from pipeline import muse_shots

    monkeypatch.setattr(muse_shots, "OUT", tmp_path)
    segs = [{"id": "seg_001", "scene": "late_night_studio", "roles": [],
             "speaker": "", "line": "", "duration": 10}]
    monkeypatch.setattr(muse_shots, "plan_segments", lambda *a, **k: segs)
    monkeypatch.setattr(muse_shots, "generate_one",
                        lambda *a, **k: {"id": "seg_001", "ok": False, "error": "boom"})
    res = muse_shots.render("tonight_show", segments=1, assemble=True)
    assert res["ok"] is False and res["episode"] is None
    assert res["assemble"].get("skipped")
    assert not (tmp_path / "templates_3d" / "tonight_show" / "work" / "episode.json").exists()
    assert not (tmp_path / "templates_3d" / "tonight_show" / "work" / "episode.partial.json").exists()


def test_muse_partial_episode_filename(tmp_path, monkeypatch):
    from pipeline import muse_shots

    monkeypatch.setattr(muse_shots, "OUT", tmp_path)
    segs = [{"id": "seg_001", "scene": "late_night_studio", "roles": [], "line": ""}]
    clips = {"seg_001": {"ok": True, "duration": 10.0}}
    path = muse_shots.write_episode("tonight_show", segs, clips,
                                    filename="episode.partial.json")
    assert path.name == "episode.partial.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["meta"]["timeline_source"] == "muse_cloud"
    assert len(data["shots"]) == 1
