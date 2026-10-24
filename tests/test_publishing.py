"""一键发布（contract/PUBLISHING.md）测试：CLI 预检/上传 mock、API enqueue/回填/失败/设置。"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import httpx
import pytest


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _episode_with_final(client, name="发布测试"):
    project = client.post(
        "/api/projects",
        json={"name": f"{name} {uuid.uuid4().hex[:6]}", "format_id": "roundtable_5min"},
    ).json()
    episode = client.get(f"/api/projects/{project['id']}/episodes").json()[0]
    return project, episode


def _write_video(episode_id, relative="final.mp4", data=b"fake-mp4-data"):
    from app import config

    path = config.OUTPUTS_DIR / "episodes" / episode_id / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _configure_upload_post(client, *, username="demo-profile", key="test-key-123"):
    response = client.patch(
        "/api/settings",
        json={
            "upload_post_api_key": key,
            "upload_post_username": username,
            "upload_post_enabled": True,
            "upload_post_platforms": ["youtube", "tiktok", "instagram"],
            "upload_post_youtube_privacy_status": "unlisted",
        },
    )
    assert response.status_code == 200, response.text
    return response


def _reset_upload_post(client):
    client.patch(
        "/api/settings",
        json={
            "upload_post_api_key": "",
            "upload_post_username": "",
            "upload_post_enabled": False,
            "upload_post_platforms": ["youtube", "tiktok", "instagram"],
            "upload_post_youtube_privacy_status": "unlisted",
            "upload_post_youtube_made_for_kids": False,
            "publish_brand_title": "造片局 · AI微综艺",
        },
    )


def _job_params(db_session, job_id):
    from app import models

    db_session.expire_all()
    job = db_session.get(models.Job, job_id)
    return dict(job.params or {})


def _last_json(text):
    for line in reversed((text or "").strip().splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            return json.loads(line)
    raise AssertionError(f"no JSON line: {text!r}")


# --------------------------------------------------------------------------- #
# CLI 预检 / 上传（mock HTTP）
# --------------------------------------------------------------------------- #
def test_cli_dry_run_no_network(monkeypatch, capsys):
    import publish.cli as cli

    monkeypatch.setattr(
        cli, "probe_media", lambda path: {"duration": 75.95, "width": 1280, "height": 720}
    )

    def boom(*args, **kwargs):
        raise AssertionError("dry-run 不应发网络请求")

    monkeypatch.setattr(cli, "upload_video", boom, raising=False)
    from publish import upload_post

    monkeypatch.setattr(upload_post, "_post_upload", boom)
    video = Path(__file__).resolve().parent / "_publish_dryrun.mp4"
    video.write_bytes(b"x")
    try:
        code = cli.main(
            [
                "--video",
                str(video),
                "--title",
                "AI 综艺",
                "--platforms",
                "youtube,tiktok,instagram",
                "--username",
                "demo",
                "--dry-run",
            ]
        )
    finally:
        video.unlink(missing_ok=True)
    result = _last_json(capsys.readouterr().out)
    assert code == 0
    assert result["ok"] is True
    assert result["dry_run"] is True
    assert result["configured"] is False
    assert any("未配置" in item for item in result["warnings"])
    assert any("Instagram" in item for item in result["warnings"])


def test_cli_dry_run_tiktok_duration_limit(monkeypatch, capsys):
    import publish.cli as cli

    monkeypatch.setattr(
        cli, "probe_media", lambda path: {"duration": 700.0, "width": 1280, "height": 720}
    )
    video = Path(__file__).resolve().parent / "_publish_long.mp4"
    video.write_bytes(b"x")
    try:
        code = cli.main(
            [
                "--video",
                str(video),
                "--title",
                "long",
                "--platforms",
                "tiktok",
                "--dry-run",
            ]
        )
    finally:
        video.unlink(missing_ok=True)
    result = _last_json(capsys.readouterr().out)
    assert code == 2
    assert result["ok"] is False
    assert any("10 分钟" in item for item in result["errors"])


def test_cli_missing_video(monkeypatch, capsys):
    import publish.cli as cli

    code = cli.main(["--video", "not_exists.mp4", "--dry-run"])
    result = _last_json(capsys.readouterr().out)
    assert code == 2
    assert result["ok"] is False
    assert "不存在" in result["error"]


class _FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            request = httpx.Request("POST", "https://api.upload-post.com/api/upload")
            raise httpx.HTTPStatusError("http error", request=request, response=self)

    def json(self):
        return self._payload


def _video_file(tmp_path):
    path = tmp_path / "final.mp4"
    path.write_bytes(b"fake-video-bytes")
    return path


def test_cli_upload_success_immediate(monkeypatch, capsys, tmp_path):
    import publish.cli as cli
    from publish import upload_post

    monkeypatch.setattr(
        cli, "probe_media", lambda path: {"duration": 12.5, "width": 1280, "height": 720}
    )
    calls = []

    def fake_post(url, headers, data, files, timeout):
        calls.append({"url": url, "headers": headers, "data": data})
        return _FakeResponse(
            200,
            {"success": True, "request_id": "req_1", "results": {"youtube": {"success": True}}},
        )

    monkeypatch.setattr(upload_post, "_post_upload", fake_post)
    monkeypatch.setenv("UPLOAD_POST_API_KEY", "secret-key")
    code = cli.main(
        [
            "--video",
            str(_video_file(tmp_path)),
            "--title",
            "标题",
            "--description",
            "desc",
            "--hashtags",
            "#AI #综艺",
            "--platforms",
            "youtube",
            "--username",
            "demo",
        ]
    )
    result = _last_json(capsys.readouterr().out)
    assert code == 0
    assert result["ok"] is True
    assert result["request_id"] == "req_1"
    assert result["configured"] is True
    assert calls and calls[0]["data"]["platform[]"] == ["youtube"]
    assert calls[0]["headers"]["Authorization"] == "Apikey secret-key"
    assert calls[0]["data"]["containsSyntheticMedia"] == "true"
    assert "secret-key" not in json.dumps(result)


def test_cli_upload_background_poll(monkeypatch, capsys, tmp_path):
    import publish.cli as cli
    from publish import upload_post

    monkeypatch.setattr(
        cli, "probe_media", lambda path: {"duration": 12.5, "width": 1280, "height": 720}
    )
    monkeypatch.setenv("UPLOAD_POST_API_KEY", "secret-key")
    monkeypatch.setattr(upload_post, "STATUS_POLL_INTERVAL_SECONDS", 0.0)
    monkeypatch.setattr(
        upload_post,
        "_post_upload",
        lambda *args, **kwargs: _FakeResponse(
            200, {"success": True, "request_id": "req_bg"}
        ),
    )
    statuses = [
        {"status": "pending"},
        {"status": "completed", "results": {"youtube": {"success": True}}},
    ]

    def fake_status(request_id, api_key, timeout=30):
        assert request_id == "req_bg"
        return statuses.pop(0)

    monkeypatch.setattr(upload_post, "check_status", fake_status)
    code = cli.main(
        [
            "--video",
            str(_video_file(tmp_path)),
            "--title",
            "标题",
            "--platforms",
            "youtube",
            "--username",
            "demo",
        ]
    )
    result = _last_json(capsys.readouterr().out)
    assert code == 0
    assert result["ok"] is True
    assert result["request_id"] == "req_bg"
    assert result["results"]["youtube"]["success"] is True


def test_cli_upload_platform_failure(monkeypatch, capsys, tmp_path):
    import publish.cli as cli
    from publish import upload_post

    monkeypatch.setattr(
        cli, "probe_media", lambda path: {"duration": 12.5, "width": 1280, "height": 720}
    )
    monkeypatch.setenv("UPLOAD_POST_API_KEY", "secret-key")
    monkeypatch.setattr(
        upload_post,
        "_post_upload",
        lambda *args, **kwargs: _FakeResponse(
            200,
            {
                "success": True,
                "request_id": "req_fail",
                "results": {"youtube": {"success": False, "error": "quota"}},
            },
        ),
    )
    code = cli.main(
        [
            "--video",
            str(_video_file(tmp_path)),
            "--title",
            "t",
            "--platforms",
            "youtube",
            "--username",
            "demo",
        ]
    )
    result = _last_json(capsys.readouterr().out)
    assert code == 1
    assert result["ok"] is False
    assert "youtube" in result["error"]


def test_cli_network_unconfirmed(monkeypatch, capsys, tmp_path):
    import publish.cli as cli
    from publish import upload_post

    monkeypatch.setattr(
        cli, "probe_media", lambda path: {"duration": 12.5, "width": 1280, "height": 720}
    )
    monkeypatch.setenv("UPLOAD_POST_API_KEY", "secret-key")

    def timeout(*args, **kwargs):
        raise httpx.TimeoutException("timed out")

    monkeypatch.setattr(upload_post, "_post_upload", timeout)
    code = cli.main(
        [
            "--video",
            str(_video_file(tmp_path)),
            "--title",
            "t",
            "--platforms",
            "youtube",
            "--username",
            "demo",
        ]
    )
    result = _last_json(capsys.readouterr().out)
    assert code == 1
    assert result["ok"] is False
    assert result["request_id"]
    assert "unconfirmed" in result["error"]


# --------------------------------------------------------------------------- #
# Settings / config
# --------------------------------------------------------------------------- #
def test_publish_settings_and_config_masking(client):
    original = client.get("/api/settings").json()
    try:
        _configure_upload_post(client)
        settings = client.get("/api/settings").json()
        assert settings["upload_post_api_key_configured"] is True
        assert settings["upload_post_username"] == "demo-profile"
        assert "test-key-123" not in json.dumps(settings)
        config_data = client.get("/api/publish/config").json()
        assert config_data["configured"] is True
        assert config_data["enabled"] is True
        assert config_data["username_masked"] == "de***"
        assert config_data["limits"]["tiktok"]["max_duration_seconds"] == 600
        assert "test-key-123" not in json.dumps(config_data)
        assert config_data["brand"]["title"] == "造片局 · AI微综艺"
        assert client.patch(
            "/api/settings", json={"upload_post_platforms": ["bilibili"]}
        ).status_code == 400
        assert client.patch(
            "/api/settings", json={"upload_post_youtube_privacy_status": "secret"}
        ).status_code == 400
    finally:
        _reset_upload_post(client)
        client.patch(
            "/api/settings",
            json={
                "upload_post_username": original["upload_post_username"],
                "upload_post_enabled": original["upload_post_enabled"],
                "upload_post_platforms": original["upload_post_platforms"],
                "upload_post_youtube_privacy_status": original[
                    "upload_post_youtube_privacy_status"
                ],
                "upload_post_youtube_made_for_kids": original[
                    "upload_post_youtube_made_for_kids"
                ],
                "publish_brand_title": original["publish_brand_title"],
            },
        )
    config_data = client.get("/api/publish/config").json()
    assert config_data["configured"] is False


# --------------------------------------------------------------------------- #
# API enqueue / job 成功 / 失败
# --------------------------------------------------------------------------- #
def test_publish_dry_run_job(client, db_session, monkeypatch):
    from app import jobs

    _project, episode = _episode_with_final(client)
    _write_video(episode["id"])
    response = client.post(
        f"/api/episodes/{episode['id']}/publish",
        json={"dry_run": True, "platforms": ["youtube", "tiktok"]},
    )
    assert response.status_code == 200, response.text
    job = response.json()
    assert job["type"] == "publish"
    assert job["status"] == "queued"
    params = _job_params(db_session, job["id"])
    assert params["dry_run"] is True
    records = client.get(f"/api/episodes/{episode['id']}/publishes").json()
    assert len(records) == 1
    assert records[0]["status"] == "queued"
    assert records[0]["platforms"] == ["youtube", "tiktok"]

    calls = []

    def fake_run_cli(cmd, cwd=None, on_line=None, timeout=None, env_overrides=None):
        calls.append({"cmd": cmd, "env": env_overrides, "timeout": timeout})
        return 0, [
            json.dumps(
                {
                    "ok": True,
                    "dry_run": True,
                    "configured": False,
                    "warnings": ["未配置 Upload-Post"],
                    "errors": [],
                    "duration": 75.95,
                    "size_mb": 9.9,
                }
            )
        ], ""

    monkeypatch.setattr(jobs, "run_cli", fake_run_cli)
    jobs.run_job(job["id"])
    done = client.get(f"/api/jobs/{job['id']}").json()
    assert done["status"] == "done"
    assert "预检" in done["message"]
    record = client.get(f"/api/episodes/{episode['id']}/publishes").json()[0]
    assert record["status"] == "prechecked"
    assert record["meta"]["dry_run"] is True
    assert "--dry-run" in calls[0]["cmd"]
    assert calls[0]["env"] is None


def test_publish_success_with_localized_metadata(client, db_session, monkeypatch):
    from app import config, jobs

    _project, episode = _episode_with_final(client)
    _write_video(episode["id"], "localized/en/final.mp4")
    meta_path = config.OUTPUTS_DIR / "episodes" / episode["id"] / "localized" / "en" / "meta.json"
    meta_path.write_text(
        json.dumps(
            {
                "publish": {
                    "title": "Will AI Replace Variety Directors?",
                    "description": "English description",
                    "hashtags": ["#AI", "#Variety"],
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.delenv("UPLOAD_POST_API_KEY", raising=False)
    _configure_upload_post(client)
    try:
        response = client.post(
            f"/api/episodes/{episode['id']}/publish",
            json={"lang": "en", "platforms": ["youtube"]},
        )
        assert response.status_code == 200, response.text
        job = response.json()
        record = client.get(f"/api/episodes/{episode['id']}/publishes").json()[0]
        assert record["title"] == "Will AI Replace Variety Directors?"
        assert record["video_url"].endswith("/localized/en/final.mp4")
        calls = []

        def fake_run_cli(cmd, cwd=None, on_line=None, timeout=None, env_overrides=None):
            calls.append({"cmd": cmd, "env": env_overrides})
            return 0, [
                json.dumps(
                    {
                        "ok": True,
                        "request_id": "req_abc",
                        "results": {"youtube": {"success": True}},
                        "duration": 75.95,
                        "size_mb": 9.9,
                    }
                )
            ], ""

        monkeypatch.setattr(jobs, "run_cli", fake_run_cli)
        jobs.run_job(job["id"])
        done = client.get(f"/api/jobs/{job['id']}").json()
        assert done["status"] == "done", done
        record = client.get(f"/api/publishes/{record['id']}").json()
        assert record["status"] == "published"
        assert record["request_id"] == "req_abc"
        assert record["meta"]["results"]["youtube"]["success"] is True
        cmd = calls[0]["cmd"]
        assert cmd[1:3] == ["-m", "publish.cli"]
        assert "--dry-run" not in cmd
        assert "Will AI Replace Variety Directors?" in cmd
        assert calls[0]["env"] == {"UPLOAD_POST_API_KEY": "test-key-123"}
        assert "test-key-123" not in " ".join(cmd)
        assert "--hashtags" in cmd
        hashtags_arg = cmd[cmd.index("--hashtags") + 1]
        assert "#AI" in hashtags_arg and "#Variety" in hashtags_arg
    finally:
        _reset_upload_post(client)


def test_publish_failure_and_no_retry(client, monkeypatch):
    from app import jobs

    monkeypatch.delenv("UPLOAD_POST_API_KEY", raising=False)
    _project, episode = _episode_with_final(client)
    _write_video(episode["id"])
    _configure_upload_post(client)
    try:
        job = client.post(
            f"/api/episodes/{episode['id']}/publish",
            json={"platforms": ["youtube"]},
        ).json()

        def fake_run_cli(cmd, cwd=None, on_line=None, timeout=None, env_overrides=None):
            return 1, [
                json.dumps(
                    {
                        "ok": False,
                        "request_id": "req_uncertain",
                        "error": "network error; upload outcome is unconfirmed",
                    }
                )
            ], ""

        monkeypatch.setattr(jobs, "run_cli", fake_run_cli)
        jobs.run_job(job["id"])
        done = client.get(f"/api/jobs/{job['id']}").json()
        assert done["status"] == "failed"
        assert "unconfirmed" in done["error"]
        record = client.get(f"/api/episodes/{episode['id']}/publishes").json()[0]
        assert record["status"] == "failed"
        assert record["request_id"] == "req_uncertain"
        assert "unconfirmed" in record["error"]
        retry = client.post(f"/api/jobs/{job['id']}/retry")
        assert retry.status_code == 400
        assert "不自动重试" in retry.json()["detail"]
    finally:
        _reset_upload_post(client)


def test_publish_requires_config_for_real_but_dry_run_ok(client):
    monkeypatchless = client
    _reset_upload_post(client)
    _project, episode = _episode_with_final(client)
    _write_video(episode["id"])
    real = client.post(
        f"/api/episodes/{episode['id']}/publish", json={"platforms": ["youtube"]}
    )
    assert real.status_code == 400
    assert "未配置" in real.json()["detail"]
    dry = client.post(
        f"/api/episodes/{episode['id']}/publish",
        json={"platforms": ["youtube"], "dry_run": True},
    )
    assert dry.status_code == 200


def test_publish_video_selection_and_validation(client):
    _project, episode = _episode_with_final(client)
    final_path = _write_video(episode["id"])
    dynamic_path = _write_video(episode["id"], "final_dynamic.mp4")
    dry = client.post(
        f"/api/episodes/{episode['id']}/publish",
        json={"platforms": ["youtube"], "dry_run": True, "version": "dynamic"},
    )
    assert dry.status_code == 200
    record = client.get(f"/api/episodes/{episode['id']}/publishes").json()[0]
    assert record["video_url"].endswith("/final_dynamic.mp4")
    dynamic_path.unlink()
    second = client.post(
        f"/api/episodes/{episode['id']}/publish",
        json={"platforms": ["youtube"], "dry_run": True, "version": "dynamic"},
    )
    assert second.status_code == 200
    assert len(client.get(f"/api/episodes/{episode['id']}/publishes").json()) == 2
    final_path.unlink()
    assert client.post(
        f"/api/episodes/{episode['id']}/publish",
        json={"platforms": ["youtube"], "dry_run": True},
    ).status_code == 400
    assert client.post(
        f"/api/episodes/{episode['id']}/publish",
        json={"platforms": ["bilibili"], "dry_run": True},
    ).status_code == 400
    assert client.post(
        f"/api/episodes/{episode['id']}/publish",
        json={"platforms": ["youtube"], "lang": "fr", "dry_run": True},
    ).status_code == 400
    assert client.post(
        f"/api/episodes/{episode['id']}/publish",
        json={"platforms": ["youtube"], "version": "weird", "dry_run": True},
    ).status_code == 400
    assert client.post(
        f"/api/episodes/{episode['id']}/publish",
        json={"platforms": ["youtube"], "privacy": "secret", "dry_run": True},
    ).status_code == 400
    assert client.post(
        "/api/episodes/not_exists/publish", json={"platforms": ["youtube"]}
    ).status_code == 404


def test_publish_default_brand_metadata(client):
    _project, episode = _episode_with_final(client)
    _write_video(episode["id"])
    assert client.post(
        f"/api/episodes/{episode['id']}/publish",
        json={"platforms": ["youtube"], "dry_run": True},
    ).status_code == 200
    record = client.get(f"/api/episodes/{episode['id']}/publishes").json()[0]
    assert record["title"] == episode["title"]
    assert "造片局" in record["meta"]["description"]
    assert "#造片局" in record["meta"]["hashtags"]
    assert "#AIVariety" in record["meta"]["hashtags"]
    detail = client.get(f"/api/publishes/{record['id']}")
    assert detail.status_code == 200
    assert client.get("/api/publishes/not_exists").status_code == 404
