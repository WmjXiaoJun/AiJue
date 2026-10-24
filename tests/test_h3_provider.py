"""H3 provider（local / MetaSo 云端）测试：settings 字段、key 掩码、provider 分派、失败可读。"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest


@pytest.fixture()
def restore_h3_settings(client):
    original = client.get("/api/settings").json()
    yield
    client.patch(
        "/api/settings",
        json={
            "h3_provider": original["h3_provider"],
            "metaso_base_url": original["metaso_base_url"],
            "metaso_resolution": original["metaso_resolution"],
            "metaso_duration": original["metaso_duration"],
        },
    )


def _new_character(client):
    project = client.post(
        "/api/projects",
        json={"name": f"h3prov {uuid.uuid4().hex[:6]}", "format_id": "roundtable_5min"},
    ).json()
    episode = client.get(f"/api/projects/{project['id']}/episodes").json()[0]
    character = client.post(
        f"/api/projects/{project['id']}/characters",
        json={
            "name": "H3 角色",
            "title": "测试",
            "style": {"primary": "anime", "variants": ["anime"]},
            "appearance": {"gender": "male", "age": 30, "hair_style": "short_swept"},
        },
    ).json()
    return project, episode, character


def _give_portrait(character_id):
    from app import config

    base = config.OUTPUTS_DIR / "characters" / character_id
    base.mkdir(parents=True, exist_ok=True)
    (base / "portrait.png").write_bytes(b"portrait-png")


def _fake_strategy_cli(calls, *, fail=False, error_text="", cost=None):
    def fake_run_cli(cmd, cwd=None, on_line=None, timeout=None):
        calls.append({"cmd": list(cmd), "timeout": timeout})
        if fail:
            return 1, [], error_text
        out = Path(cmd[cmd.index("--out") + 1])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"fake-h3-mp4")
        payload = {"ok": True, "path": str(out)}
        if cost is not None:
            payload["est_cost_cny"] = cost
        return 0, [json.dumps(payload)], ""

    return fake_run_cli


def _fake_extract(video_path, out_path, position=0.5):
    target = Path(out_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"frame")
    return True


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #
def test_settings_h3_provider_fields(client):
    data = client.get("/api/settings").json()
    assert data["h3_provider"] in ("local", "metaso")
    assert data["metaso_base_url"].startswith("https://")
    assert data["metaso_resolution"] in ("768P", "2K")
    assert 4 <= data["metaso_duration"] <= 15
    assert isinstance(data["metaso_key_configured"], bool)
    # 只读字段不回传明文 key
    assert "metaso_key" not in data
    assert "METASO_API_KEY" not in json.dumps(data)


def test_settings_patch_provider_and_metaso_fields(client, restore_h3_settings):
    response = client.patch(
        "/api/settings",
        json={
            "h3_provider": "metaso",
            "metaso_base_url": "https://metaso.cn/api/minimax",
            "metaso_resolution": "768P",
            "metaso_duration": 5,
        },
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["h3_provider"] == "metaso"
    assert data["metaso_base_url"] == "https://metaso.cn/api/minimax"
    assert data["metaso_resolution"] == "768P"
    assert data["metaso_duration"] == 5

    assert client.patch("/api/settings", json={"h3_provider": "cloud"}).status_code == 400
    assert client.patch("/api/settings", json={"metaso_resolution": "4K"}).status_code == 400
    assert client.patch("/api/settings", json={"metaso_base_url": "ftp://x"}).status_code == 400
    assert client.patch("/api/settings", json={"metaso_duration": 99}).status_code == 422


def test_metaso_key_masked_written_and_cleared(client):
    from app import config

    env_path = Path(config.METASO_ENV_PATH)
    if env_path.is_file():
        env_path.unlink()

    patched = client.patch("/api/settings", json={"metaso_key": "sk-secret-test-123"})
    assert patched.status_code == 200, patched.text
    body = patched.json()
    assert body["metaso_key_configured"] is True
    assert "sk-secret-test-123" not in json.dumps(body)
    assert env_path.is_file()
    assert "sk-secret-test-123" in env_path.read_text(encoding="utf-8")

    fetched = client.get("/api/settings").json()
    assert fetched["metaso_key_configured"] is True
    assert "sk-secret-test-123" not in json.dumps(fetched)

    cleared = client.patch("/api/settings", json={"metaso_key": ""})
    assert cleared.status_code == 200
    assert cleared.json()["metaso_key_configured"] is False
    assert not env_path.is_file()


# --------------------------------------------------------------------------- #
# h3-animate provider 分派
# --------------------------------------------------------------------------- #
def test_h3_animate_local_provider(client, monkeypatch, restore_h3_settings):
    from app import jobs

    client.patch("/api/settings", json={"h3_provider": "local"})
    _project, _episode, character = _new_character(client)
    _give_portrait(character["id"])
    job = client.post(f"/api/characters/{character['id']}/h3-animate", json={"prompt": "本地测试"}).json()
    assert job["type"] == "h3"

    calls = []
    monkeypatch.setattr(jobs, "run_cli", _fake_strategy_cli(calls))
    monkeypatch.setattr(jobs, "extract_video_frame", _fake_extract)
    jobs.run_job(job["id"])

    done = client.get(f"/api/jobs/{job['id']}").json()
    assert done["status"] == "done", done
    expected = f"/media/h3/{character['id']}_h3.mp4"
    assert done["output_url"] == expected
    detail = client.get(f"/api/characters/{character['id']}").json()
    assert detail["h3_video_url"] == expected
    assert detail["h3_metaso_url"] is None
    cmd = calls[0]["cmd"]
    assert cmd[1] == "integrations/comfyui/run_h3.py"
    assert cmd[cmd.index("--prompt") + 1] == "本地测试"
    assert calls[0]["timeout"] is not None and calls[0]["timeout"] >= 900


def test_h3_animate_metaso_provider_does_not_overwrite_local(client, monkeypatch, restore_h3_settings):
    from app import config, jobs

    client.patch(
        "/api/settings",
        json={
            "h3_provider": "metaso",
            "metaso_base_url": "https://metaso.cn/api/minimax",
            "metaso_resolution": "768P",
            "metaso_duration": 5,
        },
    )
    _project, _episode, character = _new_character(client)
    _give_portrait(character["id"])
    local_file = config.OUTPUTS_DIR / "h3" / f"{character['id']}_h3.mp4"
    local_file.parent.mkdir(parents=True, exist_ok=True)
    local_file.write_bytes(b"local-canonical-h3")

    job = client.post(
        f"/api/characters/{character['id']}/h3-animate",
        json={"prompt": "云端测试：角色说话微动作"},
    ).json()
    calls = []
    monkeypatch.setattr(jobs, "run_cli", _fake_strategy_cli(calls, cost=0.45))
    jobs.run_job(job["id"])

    done = client.get(f"/api/jobs/{job['id']}").json()
    assert done["status"] == "done", done
    expected = f"/media/h3/{character['id']}_h3_metaso.mp4"
    assert done["output_url"] == expected
    assert done["cost"] == pytest.approx(0.45)
    detail = client.get(f"/api/characters/{character['id']}").json()
    assert detail["h3_metaso_url"] == expected
    # 本地 H3 文件未被覆盖
    assert local_file.read_bytes() == b"local-canonical-h3"

    cmd = calls[0]["cmd"]
    assert cmd[1:3] == ["-m", "integrations.metaso.h3"]
    assert cmd[cmd.index("--resolution") + 1] == "768P"
    assert cmd[cmd.index("--duration") + 1] == "5"
    assert cmd[cmd.index("--base-url") + 1] == "https://metaso.cn/api/minimax"
    assert cmd[cmd.index("--prompt") + 1] == "云端测试：角色说话微动作"
    assert calls[0]["timeout"] == 300


def test_h3_animate_metaso_failure_readable(client, monkeypatch, restore_h3_settings):
    from app import jobs

    client.patch("/api/settings", json={"h3_provider": "metaso"})
    _project, _episode, character = _new_character(client)
    _give_portrait(character["id"])
    job = client.post(f"/api/characters/{character['id']}/h3-animate", json={}).json()
    calls = []
    monkeypatch.setattr(
        jobs,
        "run_cli",
        _fake_strategy_cli(calls, fail=True, error_text="MetaSo API 401 Unauthorized"),
    )
    jobs.run_job(job["id"])

    done = client.get(f"/api/jobs/{job['id']}").json()
    assert done["status"] == "failed"
    assert done["error"] and "integrations.metaso.h3" in done["error"]
    assert "MetaSo API 401 Unauthorized" in done["error"]
    detail = client.get(f"/api/characters/{character['id']}").json()
    assert detail["h3_metaso_url"] is None


def test_h3_animate_local_failure_readable(client, monkeypatch, restore_h3_settings):
    from app import jobs

    client.patch("/api/settings", json={"h3_provider": "local"})
    _project, _episode, character = _new_character(client)
    _give_portrait(character["id"])
    job = client.post(f"/api/characters/{character['id']}/h3-animate", json={}).json()
    monkeypatch.setattr(
        jobs,
        "run_cli",
        _fake_strategy_cli([], fail=True, error_text="ComfyUI connection refused"),
    )
    jobs.run_job(job["id"])
    done = client.get(f"/api/jobs/{job['id']}").json()
    assert done["status"] == "failed"
    assert done["error"] and "run_h3" in done["error"]
    assert "ComfyUI connection refused" in done["error"]
