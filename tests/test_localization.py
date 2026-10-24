"""多语种本地化（contract/LOCALIZATION.md）测试：enqueue/成功回填/失败/列表/settings。"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest


def _new_episode_with_shots(client):
    project = client.post(
        "/api/projects",
        json={"name": f"本地化测试 {uuid.uuid4().hex[:6]}", "format_id": "roundtable_5min"},
    ).json()
    episode = client.get(f"/api/projects/{project['id']}/episodes").json()[0]
    client.post(f"/api/episodes/{episode['id']}/storyboard/generate", json={})
    shots = client.get(f"/api/episodes/{episode['id']}/shots").json()
    return project, episode, shots


def _fake_localize(calls, *, fail=False, error_text="translation failed"):
    def fake_run_cli(cmd, cwd=None, on_line=None, timeout=None):
        calls.append(
            {
                "cmd": list(cmd),
                "timeout": timeout,
                "episode_path": cmd[cmd.index("--episode-json") + 1],
            }
        )
        if fail:
            return 1, [], error_text
        outdir = Path(cmd[cmd.index("--outdir") + 1])
        outdir.mkdir(parents=True, exist_ok=True)
        lang = cmd[cmd.index("--lang") + 1]
        (outdir / "final.mp4").write_bytes(b"localized-mp4")
        (outdir / "subtitles.srt").write_text(
            "1\n00:00:00,000 --> 00:00:01,000\nHello world\n", encoding="utf-8"
        )
        (outdir / "translations.json").write_text("{}", encoding="utf-8")
        (outdir / "meta.json").write_text(
            json.dumps(
                {
                    "lang": lang,
                    "duration": 75.95,
                    "title": "Will AI Replace Variety Directors?",
                    "description": "demo",
                    "hashtags": ["#AI", "#variety"],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return 0, [json.dumps({"ok": True, "outdir": str(outdir)})], ""

    return fake_run_cli


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #
def test_localize_settings_fields_and_patch(client):
    original = client.get("/api/settings").json()
    assert isinstance(original["localize_langs"], list) and original["localize_langs"]
    assert original["localize_translator"] in ("ollama", "nllb", "mock")
    assert isinstance(original["localize_voices"], dict)
    try:
        patched = client.patch(
            "/api/settings",
            json={
                "localize_langs": ["en", "ja", "EN"],
                "localize_translator": "mock",
                "localize_voices": {"en": {"host": "en-US-GuyNeural"}},
            },
        )
        assert patched.status_code == 200, patched.text
        data = patched.json()
        assert data["localize_langs"] == ["en", "ja"]
        assert data["localize_translator"] == "mock"
        assert data["localize_voices"]["en"]["host"] == "en-US-GuyNeural"
        assert client.patch("/api/settings", json={"localize_langs": ["xx"]}).status_code == 400
        assert client.patch("/api/settings", json={"localize_langs": []}).status_code == 400
        assert (
            client.patch("/api/settings", json={"localize_translator": "google"}).status_code
            == 400
        )
    finally:
        client.patch(
            "/api/settings",
            json={
                "localize_langs": original["localize_langs"],
                "localize_translator": original["localize_translator"],
                "localize_voices": original["localize_voices"],
            },
        )


def test_localization_table_migration():
    from sqlalchemy import inspect

    from app.db import engine

    columns = {column["name"] for column in inspect(engine).get_columns("localizations")}
    assert {
        "id",
        "episode_id",
        "lang",
        "status",
        "video_url",
        "srt_url",
        "meta_json",
        "created_at",
    } <= columns


# --------------------------------------------------------------------------- #
# enqueue / 列表
# --------------------------------------------------------------------------- #
def test_localize_enqueues_jobs_and_lists(client):
    _project, episode, _shots = _new_episode_with_shots(client)
    response = client.post(
        f"/api/episodes/{episode['id']}/localize", json={"langs": ["en", "es"]}
    )
    assert response.status_code == 200, response.text
    jobs = response.json()
    assert len(jobs) == 2
    assert {job["type"] for job in jobs} == {"localize"}
    assert {job["status"] for job in jobs} == {"queued"}
    assert all(job["episode_id"] == episode["id"] for job in jobs)

    listed = client.get(f"/api/episodes/{episode['id']}/localizations").json()
    assert {item["lang"] for item in listed} == {"en", "es"}
    assert {item["status"] for item in listed} == {"draft"}
    detail = client.get(f"/api/localizations/{listed[0]['id']}")
    assert detail.status_code == 200
    assert detail.json()["episode_id"] == episode["id"]
    assert client.get("/api/localizations/not_exists").status_code == 404

    # 去重 + 非法语言 + 未知集
    dedup = client.post(
        f"/api/episodes/{episode['id']}/localize", json={"langs": ["en", "EN", "en"]}
    ).json()
    assert len(dedup) == 1
    assert client.post(
        f"/api/episodes/{episode['id']}/localize", json={"langs": ["xx"]}
    ).status_code == 400
    assert client.post("/api/episodes/not_exists/localize", json={}).status_code == 404


def test_localize_uses_settings_default_langs(client):
    original = client.get("/api/settings").json()
    _project, episode, _shots = _new_episode_with_shots(client)
    try:
        client.patch("/api/settings", json={"localize_langs": ["ja"]})
        jobs = client.post(f"/api/episodes/{episode['id']}/localize", json={}).json()
        assert len(jobs) == 1
        localizations = client.get(f"/api/episodes/{episode['id']}/localizations").json()
        assert {item["lang"] for item in localizations} == {"ja"}
        # 同语言活跃任务复用
        again = client.post(
            f"/api/episodes/{episode['id']}/localize", json={"langs": ["ja"]}
        ).json()
        assert again[0]["id"] == jobs[0]["id"]
    finally:
        client.patch("/api/settings", json={"localize_langs": original["localize_langs"]})


# --------------------------------------------------------------------------- #
# 任务成功 / 失败
# --------------------------------------------------------------------------- #
def test_localize_job_success_writes_back(client, monkeypatch):
    from app import jobs

    _project, episode, shots = _new_episode_with_shots(client)
    job = client.post(
        f"/api/episodes/{episode['id']}/localize", json={"langs": ["en"]}
    ).json()[0]
    calls = []
    monkeypatch.setattr(jobs, "run_cli", _fake_localize(calls))
    jobs.run_job(job["id"])

    done = client.get(f"/api/jobs/{job['id']}").json()
    assert done["status"] == "done", done
    expected_video = f"/media/episodes/{episode['id']}/localized/en/final.mp4"
    assert done["output_url"] == expected_video
    localizations = client.get(f"/api/episodes/{episode['id']}/localizations").json()
    assert len(localizations) == 1
    localization = client.get(f"/api/localizations/{localizations[0]['id']}").json()
    assert localization["status"] == "ready"
    assert localization["video_url"] == expected_video
    assert localization["srt_url"] == f"/media/episodes/{episode['id']}/localized/en/subtitles.srt"
    assert localization["meta"]["lang"] == "en"
    assert localization["meta"]["title"].startswith("Will AI")
    assert client.get(expected_video).status_code == 200
    assert client.get(localization["srt_url"]).status_code == 200

    call = calls[0]
    cmd = call["cmd"]
    assert cmd[1:3] == ["-m", "pipeline.localize"]
    assert cmd[cmd.index("--lang") + 1] == "en"
    assert cmd[cmd.index("--translator") + 1] in ("ollama", "nllb", "mock")
    assert call["timeout"] == 1800
    episode_json = json.loads(Path(call["episode_path"]).read_text(encoding="utf-8"))
    assert episode_json["episode"]["id"] == episode["id"]
    assert len(episode_json["shots"]) == len(shots)


def test_localize_default_translator_is_ollama(client):
    original = client.get("/api/settings").json()
    try:
        client.patch("/api/settings", json={"localize_translator": "ollama"})
        assert client.get("/api/settings").json()["localize_translator"] == "ollama"
        assert (
            client.patch("/api/settings", json={"localize_translator": "google"}).status_code
            == 400
        )
        assert client.patch("/api/settings", json={"localize_translator": "nllb"}).status_code == 200
        assert client.patch("/api/settings", json={"localize_translator": "mock"}).status_code == 200
    finally:
        client.patch(
            "/api/settings", json={"localize_translator": original["localize_translator"]}
        )


def test_localize_voices_json_passed_to_cli(client, monkeypatch):
    from app import jobs

    original = client.get("/api/settings").json()
    _project, episode, _shots = _new_episode_with_shots(client)
    voices = {"en": {"host": "en-US-GuyNeural", "guest_a": "en-US-AriaNeural"}}
    try:
        client.patch("/api/settings", json={"localize_voices": voices})
        job = client.post(
            f"/api/episodes/{episode['id']}/localize", json={"langs": ["en"]}
        ).json()[0]
        calls = []
        monkeypatch.setattr(jobs, "run_cli", _fake_localize(calls))
        jobs.run_job(job["id"])
        cmd = calls[0]["cmd"]
        assert "--voices-json" in cmd
        voices_path = Path(cmd[cmd.index("--voices-json") + 1])
        assert json.loads(voices_path.read_text(encoding="utf-8")) == voices
        done = client.get(f"/api/jobs/{job['id']}").json()
        assert done["status"] == "done", done
    finally:
        client.patch(
            "/api/settings",
            json={
                "localize_voices": original["localize_voices"],
                "localize_translator": original["localize_translator"],
            },
        )


def test_localize_voices_empty_no_flag(client, monkeypatch):
    from app import jobs

    original = client.get("/api/settings").json()
    _project, episode, _shots = _new_episode_with_shots(client)
    try:
        client.patch("/api/settings", json={"localize_voices": {}})
        job = client.post(
            f"/api/episodes/{episode['id']}/localize", json={"langs": ["es"]}
        ).json()[0]
        calls = []
        monkeypatch.setattr(jobs, "run_cli", _fake_localize(calls))
        jobs.run_job(job["id"])
        assert "--voices-json" not in calls[0]["cmd"]
    finally:
        client.patch("/api/settings", json={"localize_voices": original["localize_voices"]})


def test_localize_job_failure_marks_failed(client, monkeypatch):
    from app import jobs

    _project, episode, _shots = _new_episode_with_shots(client)
    job = client.post(
        f"/api/episodes/{episode['id']}/localize", json={"langs": ["es"]}
    ).json()[0]
    calls = []
    monkeypatch.setattr(
        jobs, "run_cli", _fake_localize(calls, fail=True, error_text="NLLB model missing")
    )
    jobs.run_job(job["id"])

    done = client.get(f"/api/jobs/{job['id']}").json()
    assert done["status"] == "failed"
    assert done["error"] and "pipeline.localize" in done["error"]
    assert "NLLB model missing" in done["error"]
    localizations = client.get(f"/api/episodes/{episode['id']}/localizations").json()
    assert len(localizations) == 1
    assert localizations[0]["status"] == "failed"
    assert "NLLB model missing" in localizations[0]["meta"]["error"]
    assert localizations[0]["video_url"] is None
