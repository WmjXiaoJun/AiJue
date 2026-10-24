"""全动态分镜（contract/DYNAMIC_SHOTS.md）测试：字段迁移/PATCH 首尾帧/单镜 dynamic/失败/批量。"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest


def _new_episode_with_shots(client, count=6):
    project = client.post(
        "/api/projects",
        json={"name": f"动态测试 {uuid.uuid4().hex[:6]}", "format_id": "roundtable_5min"},
    ).json()
    episode = client.get(f"/api/projects/{project['id']}/episodes").json()[0]
    client.post(f"/api/episodes/{episode['id']}/storyboard/generate", json={})
    shots = client.get(f"/api/episodes/{episode['id']}/shots").json()
    assert len(shots) >= count
    return project, episode, shots


def _job_params(db_session, job_id):
    from app import models

    db_session.expire_all()
    job = db_session.get(models.Job, job_id)
    return dict(job.params or {})


def _fake_dynamic(calls, *, fail=False, error_text="dynamic model missing"):
    def fake_run_cli(cmd, cwd=None, on_line=None, timeout=None):
        calls.append(
            {
                "cmd": list(cmd),
                "timeout": timeout,
                "spec_path": cmd[cmd.index("--spec") + 1],
                "out_path": cmd[cmd.index("--out") + 1],
            }
        )
        if fail:
            return 1, [], error_text
        out_path = Path(cmd[cmd.index("--out") + 1])
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b"fake-dynamic-mp4")
        spec = json.loads(Path(cmd[cmd.index("--spec") + 1]).read_text(encoding="utf-8"))
        return (
            0,
            [
                json.dumps(
                    {
                        "ok": True,
                        "path": str(out_path),
                        "duration": spec["shot"]["duration"],
                        "provider": "mock-dynamic",
                        "dynamic_mode": "audio_ref",
                    }
                )
            ],
            "",
        )

    return fake_run_cli


# --------------------------------------------------------------------------- #
# 字段 / 迁移 / PATCH
# --------------------------------------------------------------------------- #
def test_dynamic_shot_columns_migration():
    from sqlalchemy import inspect

    from app.db import engine

    shot_columns = {column["name"] for column in inspect(engine).get_columns("shots")}
    assert {
        "first_frame_url",
        "last_frame_url",
        "h3_video_url",
        "dynamic_mode",
        "dynamic_status",
    } <= shot_columns
    version_columns = {
        column["name"] for column in inspect(engine).get_columns("shot_versions")
    }
    assert "kind" in version_columns


def test_shots_return_dynamic_fields_and_patch_first_last_frame(client):
    _project, _episode, shots = _new_episode_with_shots(client)
    shot = shots[0]
    assert shot["dynamic_status"] == "none"
    assert shot["dynamic_mode"] is None
    assert shot["h3_video_url"] is None
    assert shot["first_frame_url"] is None
    assert shot["last_frame_url"] is None

    patched = client.patch(
        f"/api/shots/{shot['id']}",
        json={
            "first_frame_url": "/media/episodes/ep_x/frames/first.png",
            "last_frame_url": "/media/episodes/ep_x/frames/last.png",
            "line": "首尾帧来源测试",
        },
    )
    assert patched.status_code == 200, patched.text
    data = patched.json()
    assert data["first_frame_url"] == "/media/episodes/ep_x/frames/first.png"
    assert data["last_frame_url"] == "/media/episodes/ep_x/frames/last.png"
    assert data["line"] == "首尾帧来源测试"
    fresh = client.get(f"/api/episodes/{shot['episode_id']}/shots").json()[0]
    assert fresh["first_frame_url"].endswith("first.png")
    assert client.patch("/api/shots/not_exists", json={"line": "x"}).status_code == 404


# --------------------------------------------------------------------------- #
# 单镜 dynamic
# --------------------------------------------------------------------------- #
def test_generate_dynamic_shot_success_and_reuse(client, db_session, monkeypatch):
    from app import jobs

    _project, episode, shots = _new_episode_with_shots(client)
    target = shots[1]
    client.patch(
        f"/api/shots/{target['id']}",
        json={
            "first_frame_url": "/media/first.png",
            "last_frame_url": "/media/last.png",
        },
    )
    first = client.post(
        f"/api/shots/{target['id']}/generate", json={"mode": "dynamic"}
    )
    assert first.status_code == 200, first.text
    job = first.json()
    assert job["type"] == "shot"
    assert job["status"] == "queued"
    assert _job_params(db_session, job["id"]).get("mode") == "dynamic"
    shot_now = client.get(f"/api/episodes/{episode['id']}/shots").json()[1]
    assert shot_now["dynamic_status"] == "queued"
    # 同镜头动态任务复用，不重复入队
    again = client.post(
        f"/api/shots/{target['id']}/generate", json={"mode": "dynamic"}
    ).json()
    assert again["id"] == job["id"]

    calls = []
    monkeypatch.setattr(jobs, "run_cli", _fake_dynamic(calls))
    jobs.run_job(job["id"])

    done = client.get(f"/api/jobs/{job['id']}").json()
    assert done["status"] == "done", done
    expected_url = f"/media/episodes/{episode['id']}/shots/{target['id']}_dynamic.mp4"
    assert done["output_url"] == expected_url
    shot_after = client.get(f"/api/episodes/{episode['id']}/shots").json()[1]
    assert shot_after["dynamic_status"] == "ready"
    assert shot_after["dynamic_mode"] == "audio_ref"
    assert shot_after["h3_video_url"] == expected_url
    assert shot_after["first_frame_url"] == "/media/first.png"
    assert shot_after["last_frame_url"] == "/media/last.png"
    # 静态版本不受影响（selected_version_id 不变）
    assert shot_after["selected_version_id"] is None
    versions = client.get(f"/api/shots/{target['id']}/versions").json()
    dynamic_versions = [item for item in versions if item["kind"] == "dynamic"]
    assert len(dynamic_versions) == 1
    assert dynamic_versions[0]["video_url"] == expected_url
    assert client.get(expected_url).status_code == 200

    call = calls[0]
    cmd = call["cmd"]
    assert cmd[1:3] == ["-m", "pipeline.dynamic"]
    assert call["timeout"] == 1800
    spec = json.loads(Path(call["spec_path"]).read_text(encoding="utf-8"))
    assert spec["mode"] == "dynamic"
    assert spec["shot"]["id"] == target["id"]
    assert spec["dynamic"]["first_frame_url"] == "/media/first.png"
    assert spec["dynamic"]["last_frame_url"] == "/media/last.png"


def test_generate_dynamic_shot_failure(client, monkeypatch):
    from app import jobs

    _project, episode, shots = _new_episode_with_shots(client)
    target = shots[2]
    job = client.post(
        f"/api/shots/{target['id']}/generate", json={"mode": "dynamic"}
    ).json()
    calls = []
    monkeypatch.setattr(
        jobs, "run_cli", _fake_dynamic(calls, fail=True, error_text="H3 dynamic OOM")
    )
    jobs.run_job(job["id"])

    done = client.get(f"/api/jobs/{job['id']}").json()
    assert done["status"] == "failed"
    assert done["error"] and "pipeline.dynamic" in done["error"]
    assert "H3 dynamic OOM" in done["error"]
    shot_after = client.get(f"/api/episodes/{episode['id']}/shots").json()[2]
    assert shot_after["dynamic_status"] == "failed"
    assert shot_after["h3_video_url"] is None
    assert client.get(f"/api/shots/{target['id']}/versions").json() == []


def test_generate_shot_mode_validation_and_static_backward_compat(client, db_session):
    _project, _episode, shots = _new_episode_with_shots(client)
    target = shots[0]
    assert (
        client.post(
            f"/api/shots/{target['id']}/generate", json={"mode": "weird"}
        ).status_code
        == 400
    )
    static_job = client.post(f"/api/shots/{target['id']}/generate", json={}).json()
    assert static_job["type"] == "shot"
    assert "mode" not in _job_params(db_session, static_job["id"])
    # 同镜头已有静态活跃任务 -> 复用
    reused = client.post(f"/api/shots/{target['id']}/generate", json={}).json()
    assert reused["id"] == static_job["id"]
    # 另一个镜头可带 params 入队
    params_job = client.post(
        f"/api/shots/{shots[1]['id']}/generate", json={"params": {"seed": 1}}
    ).json()
    assert _job_params(db_session, params_job["id"]).get("seed") == 1


# --------------------------------------------------------------------------- #
# 批量 dynamic
# --------------------------------------------------------------------------- #
def test_batch_generate_dynamic_all_and_subset(client, db_session):
    _project, episode, shots = _new_episode_with_shots(client)
    jobs_all = client.post(
        f"/api/episodes/{episode['id']}/shots/generate-dynamic", json={"all": True}
    ).json()
    assert len(jobs_all) == len(shots)
    assert all(job["type"] == "shot" for job in jobs_all)
    assert all(
        _job_params(db_session, job["id"]).get("mode") == "dynamic"
        for job in jobs_all
    )
    current = client.get(f"/api/episodes/{episode['id']}/shots").json()
    assert all(item["dynamic_status"] == "queued" for item in current)
    # 再次批量 -> 复用现有动态任务
    again = client.post(
        f"/api/episodes/{episode['id']}/shots/generate-dynamic", json={"all": True}
    ).json()
    assert [job["id"] for job in again] == [job["id"] for job in jobs_all]

    subset = client.post(
        f"/api/episodes/{episode['id']}/shots/generate-dynamic",
        json={"shot_ids": [shots[0]["id"], shots[1]["id"], shots[0]["id"]]},
    ).json()
    assert [job["shot_id"] for job in subset] == [shots[0]["id"], shots[1]["id"]]

    assert (
        client.post(
            f"/api/episodes/{episode['id']}/shots/generate-dynamic",
            json={"all": False},
        ).status_code
        == 400
    )
    assert (
        client.post(
            f"/api/episodes/{episode['id']}/shots/generate-dynamic",
            json={"shot_ids": ["s_not_exists"]},
        ).status_code
        == 400
    )
    assert (
        client.post(
            "/api/episodes/not_exists/shots/generate-dynamic", json={"all": True}
        ).status_code
        == 404
    )
