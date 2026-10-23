"""后台任务单元测试：注入假 pipeline CLI，覆盖 shot/render 成功、失败与计费。

不依赖 pipeline/ 是否就绪，不真正启动 ffmpeg，也不会真连 ComfyUI。
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest


def _new_episode_with_shots(client):
    project = client.post(
        "/api/projects",
        json={"name": f"任务测试 {uuid.uuid4().hex[:6]}", "format_id": "roundtable_5min"},
    ).json()
    episode = client.get(f"/api/projects/{project['id']}/episodes").json()[0]
    client.post(f"/api/episodes/{episode['id']}/storyboard/generate")
    shots = client.get(f"/api/episodes/{episode['id']}/shots").json()
    assert shots, "骨架分镜生成失败"
    return episode, shots


def _shot_of(client, episode_id, shot_id):
    shots = client.get(f"/api/episodes/{episode_id}/shots").json()
    return next(shot for shot in shots if shot["id"] == shot_id)


def _fake_shot_cli(calls, duration=5.0):
    def fake_run_cli(cmd, cwd=None, on_line=None):
        calls.append(list(cmd))
        out_path = Path(cmd[cmd.index("--out") + 1])
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b"fake-mp4-bytes")
        line = json.dumps(
            {"ok": True, "path": str(out_path), "duration": duration, "provider": "mock"},
            ensure_ascii=False,
        )
        if on_line is not None:
            on_line(line)
        return 0, [line], ""

    return fake_run_cli


def test_shot_job_success_comfyui_cost(client, monkeypatch):
    from app import jobs

    episode, shots = _new_episode_with_shots(client)
    shot = shots[0]
    settings = client.get("/api/settings").json()
    price = settings["h3_price_per_second"]
    client.patch("/api/settings", json={"generation_mode": "comfyui"})
    try:
        queued = client.post(f"/api/shots/{shot['id']}/generate", json={}).json()
        calls = []
        monkeypatch.setattr(jobs, "run_cli", _fake_shot_cli(calls))
        jobs.run_job(queued["id"])

        done = client.get(f"/api/jobs/{queued['id']}").json()
        assert done["status"] == "done", done
        assert done["progress"] == 100
        assert done["error"] in (None, "")
        expected_url = f"/media/episodes/{episode['id']}/shots/{shot['id']}.mp4"
        assert done["output_url"] == expected_url
        # 计费按 CLI 返回的实际生成时长（假 CLI 固定返回 5.0s）
        expected_cost = round(price * 5.0, 4)
        assert done["cost"] == pytest.approx(expected_cost)

        versions = client.get(f"/api/shots/{shot['id']}/versions").json()
        assert len(versions) == 1
        assert versions[0]["cost"] == pytest.approx(expected_cost)
        assert versions[0]["video_url"] == expected_url
        assert versions[0]["params"]["provider"] == "mock"
        assert versions[0]["params"]["duration"] == 5.0

        updated = _shot_of(client, episode["id"], shot["id"])
        assert updated["status"] == "ready"
        assert updated["selected_version_id"] == versions[0]["id"]
        assert updated["video_url"] == expected_url

        # 命令形状：python -m pipeline.cli gen-shot --spec ... --out ...（comfyui 模式不带 --mock）
        assert calls[0][1:4] == ["-m", "pipeline.cli", "gen-shot"]
        assert "--spec" in calls[0] and "--out" in calls[0]
        assert "--mock" not in calls[0]

        costs = client.get(f"/api/episodes/{episode['id']}/costs").json()
        assert costs["total"] == pytest.approx(expected_cost)
        assert costs["by_type"]["shot"] == pytest.approx(expected_cost)
        assert costs["items"]

        episode_after = client.get(f"/api/episodes/{episode['id']}").json()
        assert episode_after["shots_done"] == 1
        assert episode_after["cost"] == pytest.approx(expected_cost)
        project = client.get(f"/api/projects/{episode['project_id']}").json()
        assert project["spent"] == pytest.approx(expected_cost)
    finally:
        client.patch("/api/settings", json={"generation_mode": settings["generation_mode"]})


def test_shot_job_success_mock_cost_zero_and_mock_flag(client, monkeypatch):
    from app import jobs

    episode, shots = _new_episode_with_shots(client)
    shot = shots[0]
    client.patch("/api/settings", json={"generation_mode": "mock"})
    queued = client.post(f"/api/shots/{shot['id']}/generate", json={}).json()
    calls = []
    monkeypatch.setattr(jobs, "run_cli", _fake_shot_cli(calls))
    jobs.run_job(queued["id"])

    done = client.get(f"/api/jobs/{queued['id']}").json()
    assert done["status"] == "done"
    assert done["cost"] == 0.0
    assert "--mock" in calls[0]
    versions = client.get(f"/api/shots/{shot['id']}/versions").json()
    assert versions[0]["cost"] == 0.0
    assert _shot_of(client, episode["id"], shot["id"])["status"] == "ready"


def test_shot_job_active_reused(client):
    episode, shots = _new_episode_with_shots(client)
    shot = shots[0]
    first = client.post(f"/api/shots/{shot['id']}/generate", json={}).json()
    second = client.post(f"/api/shots/{shot['id']}/generate", json={}).json()
    assert first["id"] == second["id"]
    active = client.get(
        "/api/jobs", params={"shot_id": shot["id"], "type": "shot", "status": "queued"}
    )
    # 注意：contract 的 /jobs 只承诺 episode_id/type/status 过滤，这里只校验类型/状态
    assert first["type"] == "shot" and first["status"] == "queued"


def test_shot_job_cli_failure_marks_failed(client, monkeypatch):
    from app import jobs

    episode, shots = _new_episode_with_shots(client)
    shot = shots[1]
    queued = client.post(f"/api/shots/{shot['id']}/generate", json={}).json()

    def fake_run_cli(cmd, cwd=None, on_line=None):
        return 1, ["begin"], "ModuleNotFoundError: No module named 'pipeline'"

    monkeypatch.setattr(jobs, "run_cli", fake_run_cli)
    jobs.run_job(queued["id"])

    done = client.get(f"/api/jobs/{queued['id']}").json()
    assert done["status"] == "failed"
    assert done["error"] and "pipeline" in done["error"]
    assert "exit code 1" in done["error"]
    assert _shot_of(client, episode["id"], shot["id"])["status"] == "failed"
    assert client.get(f"/api/shots/{shot['id']}/versions").json() == []


def test_shot_job_launch_exception_marks_failed(client, monkeypatch):
    from app import jobs

    episode, shots = _new_episode_with_shots(client)
    shot = shots[2]
    queued = client.post(f"/api/shots/{shot['id']}/generate", json={}).json()

    def fake_run_cli(cmd, cwd=None, on_line=None):
        raise FileNotFoundError("python 不可用")

    monkeypatch.setattr(jobs, "run_cli", fake_run_cli)
    jobs.run_job(queued["id"])

    done = client.get(f"/api/jobs/{queued['id']}").json()
    assert done["status"] == "failed"
    assert done["error"] and "启动失败" in done["error"]


def test_render_job_success(client, monkeypatch):
    from app import config, jobs

    episode = client.get("/api/episodes/ep_001").json()
    queued = client.post(f"/api/episodes/{episode['id']}/render", json={}).json()
    assert queued["type"] == "render"

    def fake_run_cli(cmd, cwd=None, on_line=None):
        assert cmd[1:4] == ["-m", "pipeline.cli", "render"]
        assert "--episode" in cmd and "--workdir" in cmd and "--out" in cmd
        rel_out = Path(cmd[cmd.index("--out") + 1])
        assert rel_out.parts[0] == "outputs"
        out_path = config.OUTPUTS_DIR.joinpath(*rel_out.parts[1:])
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b"fake-final-mp4")
        lines = ["PROGRESS 10 检查镜头片段", "PROGRESS 55 逐镜合成", "PROGRESS 100 输出成片"]
        for line in lines:
            if on_line is not None:
                on_line(line)
        result = json.dumps(
            {"ok": True, "path": str(out_path), "duration": 296.0, "shots": 52, "size_bytes": 1234},
            ensure_ascii=False,
        )
        return 0, lines + [result], ""

    monkeypatch.setattr(jobs, "run_cli", fake_run_cli)
    jobs.run_job(queued["id"])

    done = client.get(f"/api/jobs/{queued['id']}").json()
    assert done["status"] == "done", done
    assert done["progress"] == 100
    assert done["output_url"] == "/media/episodes/ep_001/final.mp4"
    assert done["message"] == "成片渲染完成"

    episode_after = client.get("/api/episodes/ep_001").json()
    assert episode_after["status"] == "ready"
    assert episode_after["progress"] == 100
    assert episode_after["render_url"] == "/media/episodes/ep_001/final.mp4"

    media = client.get("/media/episodes/ep_001/final.mp4")
    assert media.status_code == 200
    assert media.content == b"fake-final-mp4"


def test_render_job_failure(client, monkeypatch, fresh_episode):
    from app import jobs

    episode = fresh_episode["episode"]
    queued = client.post(f"/api/episodes/{episode['id']}/render", json={}).json()

    def fake_run_cli(cmd, cwd=None, on_line=None):
        if on_line is not None:
            on_line("PROGRESS 30 合成中")
        return 2, [], "ffmpeg not found"

    monkeypatch.setattr(jobs, "run_cli", fake_run_cli)
    jobs.run_job(queued["id"])

    done = client.get(f"/api/jobs/{queued['id']}").json()
    assert done["status"] == "failed"
    assert "render 失败" in done["error"]
    assert "ffmpeg not found" in done["error"]
    assert client.get(f"/api/episodes/{episode['id']}").json()["status"] == "failed"


def test_build_shot_spec_includes_settings(db_session):
    from app import jobs, models, seed

    episode = db_session.get(models.Episode, "ep_001")
    shot = db_session.get(models.Shot, "s001")
    setting = seed.get_settings(db_session)
    spec = jobs._build_shot_spec(db_session, shot, episode, "comfyui", {"seed": 1}, setting)
    assert spec["mode"] == "comfyui"
    assert spec["shot"]["id"] == "s001"
    assert len(spec["assets"]) == 5
    assert spec["episode"]["id"] == "ep_001"
    assert spec["params"] == {"seed": 1}
    payload = spec["settings"]
    assert payload["comfyui_url"] == setting.comfyui_url
    assert payload["generation_mode"] == setting.generation_mode
    assert payload["resolution"] == setting.resolution
    assert isinstance(payload["workflow_template"], (dict, str))


def test_resolve_workflow_template():
    from app.jobs import _resolve_workflow_template

    inline = '{"a": {"inputs": {"text": "x"}}}'
    assert _resolve_workflow_template(inline) == {"a": {"inputs": {"text": "x"}}}
    # backend/app/workflows 下自带示例模板
    bundled = _resolve_workflow_template("h3_roundtable.json")
    assert isinstance(bundled, dict)
    assert _resolve_workflow_template("missing_workflow_xyz.json") == "missing_workflow_xyz.json"


def test_parse_progress():
    from app.jobs import _parse_progress

    assert _parse_progress("PROGRESS 42 合成第 10 镜") == (42.0, "合成第 10 镜")
    assert _parse_progress("PROGRESS 120 done") == (100.0, "done")
    assert _parse_progress("progress 5 lower") == (5.0, "lower")  # 大小写不敏感
    assert _parse_progress("hello") is None
    assert _parse_progress("PROGRESS abc") is None
