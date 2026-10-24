"""概念图 / H3 动画 / 剪映导出 API 测试（假 imagegen / run_h3 / pipeline.capcut CLI）。"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest


def _new_project_episode(client):
    project = client.post(
        "/api/projects",
        json={"name": f"概念测试 {uuid.uuid4().hex[:6]}", "format_id": "roundtable_5min"},
    ).json()
    episode = client.get(f"/api/projects/{project['id']}/episodes").json()[0]
    return project, episode


def _new_character(client):
    project, episode = _new_project_episode(client)
    character = client.post(
        f"/api/projects/{project['id']}/characters",
        json={
            "name": "概念角色",
            "title": "测试嘉宾",
            "persona": "测试概念图",
            "voice": "zh-CN-XiaoxiaoNeural",
            "accent_color": "#66CCFF",
            "style": {"primary": "anime", "variants": ["anime"]},
            "appearance": {
                "gender": "female",
                "age": 22,
                "hair_style": "bob",
                "hair_color": "#6B4A8F",
                "eye_color": "#3A6FE0",
                "outfit": {"type": "hoodie"},
            },
        },
    ).json()
    return project, episode, character


def _write_concept_outputs(character_id):
    from app import config

    base = config.OUTPUTS_DIR / "concepts" / character_id
    (base / "three_view").mkdir(parents=True, exist_ok=True)
    (base / "expressions").mkdir(parents=True, exist_ok=True)
    (base / "portrait.png").write_bytes(b"concept-portrait")
    (base / "sheet.png").write_bytes(b"concept-sheet")
    for name in ("front", "side", "three_quarter"):
        (base / "three_view" / f"{name}.png").write_bytes(b"view-" + name.encode())
    for name in ("neutral", "happy", "surprised", "thinking"):
        (base / "expressions" / f"{name}.png").write_bytes(b"expr-" + name.encode())
    (base / "meta.json").write_text(
        json.dumps({"id": character_id, "mode": "portrait", "model": "animagine-xl-4.0"}),
        encoding="utf-8",
    )
    return base


# --------------------------------------------------------------------------- #
# concept/generate + concepts 懒扫描
# --------------------------------------------------------------------------- #
def test_concept_generate_enqueues_job(client):
    _project, _episode, character = _new_character(client)
    response = client.post(
        f"/api/characters/{character['id']}/concept/generate",
        json={
            "modes": ["portrait", "three_view"],
            "style": "anime",
            "prompt_extra": "bonus tag",
            "seed": 123,
        },
    )
    assert response.status_code == 200, response.text
    job = response.json()
    assert job["type"] == "concept"
    assert job["status"] == "queued"
    assert job["character_id"] == character["id"]
    assert job["episode_id"]
    listed = client.get("/api/jobs", params={"type": "concept", "status": "queued"}).json()
    assert any(item["id"] == job["id"] for item in listed)
    again = client.post(f"/api/characters/{character['id']}/concept/generate", json={}).json()
    assert again["id"] == job["id"]


def test_concept_generate_validation(client):
    _project, _episode, character = _new_character(client)
    url = f"/api/characters/{character['id']}/concept/generate"
    assert client.post(url, json={"modes": ["bad_mode"]}).status_code == 400
    assert client.post(url, json={"style": "bad_style"}).status_code == 400
    assert client.post("/api/characters/not_exists/concept/generate", json={}).status_code == 404


def test_concept_lazy_scan(client):
    from app import config

    _project, _episode, character = _new_character(client)
    base = _write_concept_outputs(character["id"])

    files = client.get(f"/api/characters/{character['id']}/concepts")
    assert files.status_code == 200
    data = files.json()
    assert data["portrait"] == f"/media/concepts/{character['id']}/portrait.png"
    assert data["sheet"] == f"/media/concepts/{character['id']}/sheet.png"
    assert set(data["three_view"]) == {"front", "side", "three_quarter"}
    assert set(data["expressions"]) == {"neutral", "happy", "surprised", "thinking"}
    assert data["meta"]["model"] == "animagine-xl-4.0"

    detail = client.get(f"/api/characters/{character['id']}").json()
    assert detail["concept_portrait_url"] == data["portrait"]
    assert detail["concept_sheet_url"] == data["sheet"]
    assert len(detail["concept_three_view"]) == 3

    media = client.get(f"/media/concepts/{character['id']}/portrait.png")
    assert media.status_code == 200
    assert media.content == b"concept-portrait"
    assert base.is_dir()


def test_character_concepts_consistent_scan(client):
    """contract/CONSISTENCY.md：consistent/three_view、consistent/expressions 懒扫描。"""
    _project, _episode, character = _new_character(client)
    base = _write_concept_outputs(character["id"])
    consistent = base / "consistent"
    (consistent / "three_view").mkdir(parents=True, exist_ok=True)
    (consistent / "expressions").mkdir(parents=True, exist_ok=True)
    for name in ("front", "side", "three_quarter"):
        (consistent / "three_view" / f"{name}.png").write_bytes(b"consistent-view")
    for name in ("neutral", "happy", "surprised", "thinking"):
        (consistent / "expressions" / f"{name}.png").write_bytes(b"consistent-expr")
    (consistent / "meta.json").write_text(
        json.dumps({"mode": "three_view", "ip_adapter_strength": 0.65, "ref": "portrait.png"}),
        encoding="utf-8",
    )

    data = client.get(f"/api/characters/{character['id']}/concepts").json()
    assert set(data["consistent"]["three_view"]) == {"front", "side", "three_quarter"}
    assert set(data["consistent"]["expressions"]) == {"neutral", "happy", "surprised", "thinking"}
    assert data["consistent"]["meta"]["ip_adapter_strength"] == 0.65
    assert data["consistent"]["three_view"]["front"] == (
        f"/media/concepts/{character['id']}/consistent/three_view/front.png"
    )
    # 概念图常规字段仍然齐全
    assert data["portrait"] == f"/media/concepts/{character['id']}/portrait.png"

    # 未生成 consistent 的角色返回空结构而不是报错
    empty = client.get("/api/characters/laozhou/concepts").json()["consistent"]
    assert empty == {"three_view": {}, "expressions": {}, "meta": {}}


def _fake_imagegen(calls, fail_mode=None):
    def fake_run_cli(cmd, cwd=None, on_line=None, timeout=None):
        calls.append(list(cmd))
        mode = cmd[cmd.index("--mode") + 1]
        if fail_mode == mode:
            return 1, [], "SDXL OOM"
        outdir = Path(cmd[cmd.index("--outdir") + 1])
        if mode == "portrait":
            outdir.mkdir(parents=True, exist_ok=True)
            (outdir / "portrait.png").write_bytes(b"concept-portrait")
            (outdir / "expressions").mkdir(exist_ok=True)
            for name in ("neutral", "happy", "surprised", "thinking"):
                (outdir / "expressions" / f"{name}.png").write_bytes(b"png")
            (outdir / "meta.json").write_text(
                json.dumps({"id": "x", "mode": mode, "model": "animagine-xl-4.0"}),
                encoding="utf-8",
            )
        elif mode == "three_view":
            (outdir / "three_view").mkdir(parents=True, exist_ok=True)
            for name in ("front", "side", "three_quarter"):
                (outdir / "three_view" / f"{name}.png").write_bytes(b"png")
            (outdir / "sheet.png").write_bytes(b"sheet")
        return 0, [json.dumps({"ok": True, "mode": mode})], ""

    return fake_run_cli


def test_concept_generation_success_with_fake_cli(client, monkeypatch):
    from app import jobs

    _project, _episode, character = _new_character(client)
    job = client.post(
        f"/api/characters/{character['id']}/concept/generate",
        json={"modes": ["portrait", "three_view"], "style": "anime", "prompt_extra": "bonus tag", "seed": 7},
    ).json()
    calls = []
    monkeypatch.setattr(jobs, "run_cli", _fake_imagegen(calls))
    jobs.run_job(job["id"])

    done = client.get(f"/api/jobs/{job['id']}").json()
    assert done["status"] == "done", done
    assert done["progress"] == 100
    assert done["output_url"] == f"/media/concepts/{character['id']}/portrait.png"

    detail = client.get(f"/api/characters/{character['id']}").json()
    assert detail["concept_portrait_url"] == done["output_url"]
    assert detail["concept_sheet_url"] == f"/media/concepts/{character['id']}/sheet.png"
    assert set(detail["concept_three_view"]) == {"front", "side", "three_quarter"}
    assert set(detail["concept_expressions"]) == {"neutral", "happy", "surprised", "thinking"}

    assert len(calls) == 2
    assert calls[0][calls[0].index("--mode") + 1] == "portrait"
    assert calls[1][calls[1].index("--mode") + 1] == "three_view"
    assert calls[0][calls[0].index("--style") + 1] == "anime"
    assert calls[0][calls[0].index("--prompt-extra") + 1] == "bonus tag"
    assert calls[0][calls[0].index("--seed") + 1] == "7"
    assert calls[0][1:4] == ["-m", "imagegen.cli", "generate"]
    assert "--spec" in calls[0]


def test_concept_generation_failure_with_fake_cli(client, monkeypatch):
    from app import jobs

    _project, _episode, character = _new_character(client)
    job = client.post(
        f"/api/characters/{character['id']}/concept/generate",
        json={"modes": ["portrait"]},
    ).json()
    monkeypatch.setattr(jobs, "run_cli", _fake_imagegen([], fail_mode="portrait"))
    jobs.run_job(job["id"])

    done = client.get(f"/api/jobs/{job['id']}").json()
    assert done["status"] == "failed"
    assert done["error"] and "imagegen generate --mode portrait" in done["error"]
    assert "SDXL OOM" in done["error"]
    detail = client.get(f"/api/characters/{character['id']}").json()
    assert detail["concept_portrait_url"] is None
    assert client.get(f"/api/characters/{character['id']}/concepts").json()["portrait"] is None


# --------------------------------------------------------------------------- #
# portrait/from-concept
# --------------------------------------------------------------------------- #
def test_portrait_from_concept(client):
    _project, _episode, character = _new_character(client)
    base = _write_concept_outputs(character["id"])
    cutout = base / "cutout"
    cutout.mkdir(parents=True, exist_ok=True)
    (cutout / "portrait.png").write_bytes(b"cutout-portrait")

    response = client.post(
        f"/api/characters/{character['id']}/portrait/from-concept",
        json={"path": f"/media/concepts/{character['id']}/portrait.png"},
    )
    assert response.status_code == 200, response.text
    data = response.json()
    expected = f"/media/characters/{character['id']}/portrait_from_concept.png"
    assert data["portrait_url"] == expected
    assert data["status"] == "ready"

    from app import config

    copied = config.OUTPUTS_DIR / "characters" / character["id"] / "portrait_from_concept.png"
    assert copied.is_file()
    assert copied.read_bytes() == b"concept-portrait"
    cutout_copied = config.OUTPUTS_DIR / "characters" / character["id"] / "cutout" / "portrait.png"
    assert cutout_copied.is_file()
    assert cutout_copied.read_bytes() == b"cutout-portrait"

    # 角色卡与资产同步；GET 懒同步不会把角色卡改回 Blender portrait
    (config.OUTPUTS_DIR / "characters" / character["id"] / "portrait.png").write_bytes(b"blender")
    detail = client.get(f"/api/characters/{character['id']}").json()
    assert detail["portrait_url"] == expected
    assets = client.get(f"/api/episodes/{_episode['id']}/assets").json()
    asset = next(item for item in assets if item["id"] == character["asset_id"])
    assert asset["images"] == [expected]

    media = client.get(expected)
    assert media.status_code == 200

    bad = client.post(
        f"/api/characters/{character['id']}/portrait/from-concept",
        json={"path": "/media/concepts/not_exists/portrait.png"},
    )
    assert bad.status_code == 400
    assert client.post("/api/characters/not_exists/portrait/from-concept", json={"path": "x"}).status_code == 404


# --------------------------------------------------------------------------- #
# h3-animate
# --------------------------------------------------------------------------- #
def _fake_h3(calls, timeouts, fail=False):
    def fake_run_cli(cmd, cwd=None, on_line=None, timeout=None):
        calls.append(list(cmd))
        timeouts.append(timeout)
        if fail:
            return 1, [], "ComfyUI connection refused"
        out = Path(cmd[cmd.index("--out") + 1])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"fake-h3-mp4")
        return 0, [json.dumps({"ok": True, "path": str(out)})], ""

    return fake_run_cli


def test_h3_animate_requires_image(client):
    _project, _episode, character = _new_character(client)
    response = client.post(f"/api/characters/{character['id']}/h3-animate", json={})
    assert response.status_code == 400
    assert "立绘" in response.json()["detail"]
    assert client.post("/api/characters/not_exists/h3-animate", json={}).status_code == 404


def test_h3_animate_success_with_fake_cli(client, monkeypatch):
    from app import config, jobs

    _project, _episode, character = _new_character(client)
    base = config.OUTPUTS_DIR / "characters" / character["id"]
    base.mkdir(parents=True, exist_ok=True)
    (base / "portrait.png").write_bytes(b"portrait-png")

    response = client.post(
        f"/api/characters/{character['id']}/h3-animate",
        json={"prompt": "轻微点头"},
    )
    assert response.status_code == 200, response.text
    job = response.json()
    assert job["type"] == "h3"
    assert job["status"] == "queued"
    assert job["character_id"] == character["id"]
    # 活跃任务复用（完成后可再次入队新任务）
    again = client.post(f"/api/characters/{character['id']}/h3-animate", json={}).json()
    assert again["id"] == job["id"]

    calls, timeouts = [], []
    monkeypatch.setattr(jobs, "run_cli", _fake_h3(calls, timeouts))

    def fake_extract(video_path, out_path, position=0.5):
        target = Path(out_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"h3-frame")
        return True

    monkeypatch.setattr(jobs, "extract_video_frame", fake_extract)
    jobs.run_job(job["id"])

    done = client.get(f"/api/jobs/{job['id']}").json()
    assert done["status"] == "done", done
    expected_video = f"/media/h3/{character['id']}_h3.mp4"
    assert done["output_url"] == expected_video
    detail = client.get(f"/api/characters/{character['id']}").json()
    assert detail["h3_video_url"] == expected_video
    assert detail["h3_portrait_url"] == f"/media/characters/{character['id']}/h3_portrait.png"
    assert client.get(expected_video).status_code == 200

    cmd = calls[0]
    assert cmd[1] == "integrations/comfyui/run_h3.py"
    assert cmd[cmd.index("--width") + 1] == "768"
    assert cmd[cmd.index("--height") + 1] == "768"
    assert cmd[cmd.index("--length") + 1] == "124"
    assert cmd[cmd.index("--prompt") + 1] == "轻微点头"
    assert Path(cmd[cmd.index("--image") + 1]).is_file()
    assert timeouts[0] is not None and timeouts[0] >= 900

    # 任务完成后允许再次入队
    fresh = client.post(f"/api/characters/{character['id']}/h3-animate", json={}).json()
    assert fresh["id"] != job["id"]
    assert fresh["type"] == "h3"


def test_h3_animate_failure(client, monkeypatch):
    from app import config, jobs

    _project, episode, character = _new_character(client)
    base = config.OUTPUTS_DIR / "characters" / character["id"]
    base.mkdir(parents=True, exist_ok=True)
    (base / "portrait.png").write_bytes(b"portrait-png")
    job = client.post(f"/api/characters/{character['id']}/h3-animate", json={}).json()
    calls, timeouts = [], []
    monkeypatch.setattr(jobs, "run_cli", _fake_h3(calls, timeouts, fail=True))
    jobs.run_job(job["id"])

    done = client.get(f"/api/jobs/{job['id']}").json()
    assert done["status"] == "failed"
    assert done["error"] and "run_h3 失败" in done["error"]
    assert "ComfyUI connection refused" in done["error"]
    detail = client.get(f"/api/characters/{character['id']}").json()
    assert detail["h3_video_url"] is None
    # 失败不应误伤 episode 状态
    assert client.get(f"/api/episodes/{episode['id']}").json()["status"] == "draft"


# --------------------------------------------------------------------------- #
# export（剪映草稿）
# --------------------------------------------------------------------------- #
def test_export_enqueues_job(client, fresh_episode):
    episode = fresh_episode["episode"]
    response = client.post(f"/api/episodes/{episode['id']}/export", json={"type": "capcut"})
    assert response.status_code == 200, response.text
    job = response.json()
    assert job["type"] == "export"
    assert job["status"] == "queued"
    assert job["episode_id"] == episode["id"]
    again = client.post(f"/api/episodes/{episode['id']}/export", json={}).json()
    assert again["id"] == job["id"]
    assert client.post(f"/api/episodes/{episode['id']}/export", json={"type": "premiere"}).status_code == 400
    assert client.post("/api/episodes/not_exists/export", json={}).status_code == 404


def test_export_custom_name_with_fake_cli(client, monkeypatch, fresh_episode):
    from app import jobs

    episode = fresh_episode["episode"]
    job = client.post(
        f"/api/episodes/{episode['id']}/export",
        json={"type": "capcut", "name": "造片局_EP001_apicheck"},
    ).json()
    calls, captured = [], {}
    monkeypatch.setattr(jobs, "run_cli", _fake_capcut(calls, captured))
    jobs.run_job(job["id"])
    done = client.get(f"/api/jobs/{job['id']}").json()
    assert done["status"] == "done", done
    assert calls[0][calls[0].index("--name") + 1] == "造片局_EP001_apicheck"
    assert captured["manifest"]["name"] == "造片局_EP001_apicheck"
    assert "造片局_EP001_apicheck" in done["output_url"]


def _fake_capcut(calls, captured, fail=False):
    def fake_run_cli(cmd, cwd=None, on_line=None, timeout=None):
        calls.append(list(cmd))
        if fail:
            return 1, [], "capcut exporter crashed"
        name = cmd[cmd.index("--name") + 1]
        outdir = Path(cmd[cmd.index("--outdir") + 1])
        draft = outdir / name
        draft.mkdir(parents=True, exist_ok=True)
        (draft / "draft_content.json").write_text(
            json.dumps({"canvas_config": {"width": 1280, "height": 720}, "duration": 299000000}),
            encoding="utf-8",
        )
        (draft / "draft_meta_info.json").write_text("{}", encoding="utf-8")
        (draft / "subtitles.srt").write_text(
            "1\n00:00:00,000 --> 00:00:01,000\n测试字幕\n", encoding="utf-8"
        )
        manifest_path = Path(cmd[cmd.index("--manifest") + 1])
        captured["manifest"] = json.loads(manifest_path.read_text(encoding="utf-8"))
        captured["draft"] = draft
        return 0, [json.dumps({"ok": True, "draft_dir": str(draft)})], ""

    return fake_run_cli


def test_export_capcut_success_with_fake_cli(client, monkeypatch, fresh_episode):
    from app import jobs

    episode = fresh_episode["episode"]
    job = client.post(f"/api/episodes/{episode['id']}/export", json={"type": "capcut"}).json()
    calls, captured = [], {}
    monkeypatch.setattr(jobs, "run_cli", _fake_capcut(calls, captured))
    jobs.run_job(job["id"])

    done = client.get(f"/api/jobs/{job['id']}").json()
    assert done["status"] == "done", done
    assert "造片局_EP001" in done["output_url"]
    assert captured["draft"].is_dir()
    assert (captured["draft"] / "draft_content.json").is_file()

    manifest = captured["manifest"]
    assert manifest["name"] == "造片局_EP001"
    assert manifest["width"] == 1280 and manifest["height"] == 720
    assert manifest["fps"] == 25
    assert manifest["duration"] > 0
    assert isinstance(manifest["subtitles"], list)

    cmd = calls[0]
    assert cmd[1:4] == ["-m", "pipeline.capcut", "export"]
    assert cmd[cmd.index("--name") + 1] == "造片局_EP001"
    assert "--copy-media" in cmd
    assert "--manifest" in cmd and "--outdir" in cmd


def test_export_failure(client, monkeypatch, fresh_episode):
    from app import jobs

    episode = fresh_episode["episode"]
    job = client.post(f"/api/episodes/{episode['id']}/export", json={}).json()
    calls, captured = [], {}
    monkeypatch.setattr(jobs, "run_cli", _fake_capcut(calls, captured, fail=True))
    jobs.run_job(job["id"])

    done = client.get(f"/api/jobs/{job['id']}").json()
    assert done["status"] == "failed"
    assert done["error"] and "pipeline.capcut export" in done["error"]
    assert "capcut exporter crashed" in done["error"]
    assert client.get(f"/api/episodes/{episode['id']}").json()["status"] == "draft"


def test_export_manifest_prefers_shot_clips(client, db_session, fresh_episode):
    """有分镜片段时 manifest 用逐镜时间线 + 旁白音频（与 build-manifest 一致）。"""
    from app import config, models
    from app.main import _build_export_manifest

    episode_id = fresh_episode["episode"]["id"]
    client.post(f"/api/episodes/{episode_id}/storyboard/generate")
    shots = client.get(f"/api/episodes/{episode_id}/shots").json()
    workdir = config.OUTPUTS_DIR / "episodes" / episode_id
    (workdir / "clips").mkdir(parents=True, exist_ok=True)
    (workdir / "audio").mkdir(parents=True, exist_ok=True)
    for shot in shots:
        (workdir / "clips" / f"{shot['id']}.mp4").write_bytes(b"clip")
    (workdir / "audio" / "narration.wav").write_bytes(b"wav")
    client.patch(f"/api/shots/{shots[0]['id']}", json={"line": "开头台词"})

    episode = db_session.get(models.Episode, episode_id)
    manifest = _build_export_manifest(db_session, episode, "capcut")
    assert len(manifest["video"]) == len(shots)
    assert manifest["video"][0]["type"] == "shot"
    assert manifest["video"][0]["path"].endswith(f"clips/{shots[0]['id']}.mp4")
    assert manifest["video"][0]["start"] == 0.0
    expected_duration = round(sum(float(shot["duration"]) for shot in shots), 3)
    assert manifest["duration"] == pytest.approx(expected_duration)
    assert manifest["audio"], manifest
    assert manifest["audio"][0]["path"].endswith("audio/narration.wav")
    assert manifest["subtitles"] and manifest["subtitles"][0]["text"] == "开头台词"


def test_export_manifest_includes_canvas_and_subtitles(client, db_session, fresh_episode):
    from app import models
    from app.main import _build_export_manifest

    episode_id = fresh_episode["episode"]["id"]
    client.post(f"/api/episodes/{episode_id}/storyboard/generate")
    shots = client.get(f"/api/episodes/{episode_id}/shots").json()
    client.patch(f"/api/shots/{shots[0]['id']}", json={"line": "第一句台词"})
    episode = db_session.get(models.Episode, episode_id)
    manifest = _build_export_manifest(db_session, episode, "capcut")
    assert manifest["name"] == "造片局_EP001"
    assert manifest["width"] == 1280 and manifest["height"] == 720
    assert manifest["fps"] == 25
    assert manifest["duration"] > 0
    assert manifest["subtitles"], manifest
    assert manifest["subtitles"][0]["text"] == "第一句台词"
    assert manifest["subtitles"][0]["start"] == 0.0
    assert manifest["subtitles"][0]["end"] > 0
