"""台本驱动分镜（contract/SCRIPT_FORMAT.md）测试：解析、时长 clamp、force、剧集创建、镜头增删。"""

from __future__ import annotations

import uuid

import pytest

from app import script_parser


def _assets():
    return [
        {"id": "host", "name": "老周", "title": "主持人", "type": "character", "role": "host", "voice": "zh-CN-YunxiNeural"},
        {"id": "guest_a", "name": "阿栗", "title": "AI 内容创业者", "type": "character", "role": "guest", "voice": "zh-CN-XiaoxiaoNeural"},
        {"id": "guest_b", "name": "大魏", "title": "传统综艺导演", "type": "character", "role": "guest", "voice": "zh-CN-YunjianNeural"},
        {"id": "scene", "name": "演播厅", "title": "场景", "type": "scene", "role": "guest", "voice": ""},
    ]


def _new_project_episode(client, name=None):
    project = client.post(
        "/api/projects",
        json={"name": name or f"台本测试 {uuid.uuid4().hex[:6]}", "format_id": "roundtable_5min"},
    ).json()
    episode = client.get(f"/api/projects/{project['id']}/episodes").json()[0]
    return project, episode


def _add_character_assets(client, episode_id, images=None):
    images = list(images or [])
    host = client.post(
        f"/api/episodes/{episode_id}/assets",
        json={
            "role": "host",
            "type": "character",
            "name": "老周",
            "title": "主持人",
            "voice": "zh-CN-YunxiNeural",
            "accent_color": "#F5C518",
            "images": images,
        },
    ).json()
    guest = client.post(
        f"/api/episodes/{episode_id}/assets",
        json={
            "role": "guest",
            "type": "character",
            "name": "阿栗",
            "title": "AI 内容创业者",
            "voice": "zh-CN-XiaoxiaoNeural",
            "accent_color": "#5AC8FA",
            "images": images,
        },
    ).json()
    return host, guest


SAMPLE_SCRIPT = """[开场]
老周：今天聊 AI 综艺。
阿栗: 我先说结论——好不好笑才重要。
大魏：（推了推眼镜）等观众发现掌声是合成的再说。

[正片]
（冷笑）
老周：那我们做个测试。
神秘人：未知说话人也要能解析。
这一行是没有冒号的旁白。
"""


# --------------------------------------------------------------------------- #
# 解析规则（单元）
# --------------------------------------------------------------------------- #
def test_parse_dialogue_colons_and_speakers():
    shots = script_parser.build_shots("测试集", "老周：你好\n阿栗: 我也好", _assets())
    assert [shot["type"] for shot in shots] == [
        "title_card",
        "host_to_camera",
        "shot_reverse",
        "end_card",
    ]
    assert shots[1]["speaker"] == "host"
    assert shots[1]["speaker_name"] == "老周"
    assert shots[1]["line"] == "你好"
    assert shots[2]["speaker"] == "guest_a"
    assert shots[2]["speaker_name"] == "阿栗"
    assert shots[2]["line"] == "我也好"
    assert [shot["no"] for shot in shots] == [1, 2, 3, 4]


def test_parse_narration_reaction_and_unknown_speaker():
    shots = script_parser.build_shots("标题", SAMPLE_SCRIPT, _assets())
    types = [shot["type"] for shot in shots]
    assert types[0] == "title_card"
    assert types[-1] == "end_card"
    # 段落标记不单独成镜；反应镜头 visual=括号内文字、speaker 沿用上一个说话人
    # 行首括号动作拆成 reaction 镜头，再接对白（动作不进 TTS/字幕）
    reactions = [shot for shot in shots if shot["type"] == "reaction"]
    assert [shot["visual"] for shot in reactions] == ["推了推眼镜", "冷笑"]
    assert all(shot["speaker"] == "guest_b" for shot in reactions)
    assert all(shot["line"] == "" for shot in reactions)
    assert reactions[0]["subtitle"] == "（推了推眼镜）"
    assert reactions[1]["subtitle"] == "（冷笑）"
    david = [shot for shot in shots if shot["speaker"] == "guest_b" and shot["line"]]
    assert david and david[0]["line"] == "等观众发现掌声是合成的再说。"
    # 未知说话人：speaker 置空、保留原文名字
    unknown = [shot for shot in shots if shot["speaker_name"] == "神秘人"]
    assert len(unknown) == 1
    assert unknown[0]["speaker"] is None
    assert unknown[0]["line"] == "未知说话人也要能解析。"
    # 无冒号 -> 旁白 speaker=host
    narration = [shot for shot in shots if shot["line"] == "这一行是没有冒号的旁白。"]
    assert len(narration) == 1
    assert narration[0]["speaker"] == "host"
    assert narration[0]["speaker_name"] == "老周"


def test_parse_duration_clamp():
    assert script_parser.clamp_duration("嗯") == 2.5
    assert script_parser.clamp_duration("长" * 60) == 8.0
    mid = script_parser.clamp_duration("一二三四五六七八九十一二三四五六七八九十")
    assert mid == pytest.approx(round(1.2 + 20 * 0.13, 2))
    shots = script_parser.build_shots("t", "老周：嗯", _assets())
    assert shots[1]["duration"] == 2.5


def test_parse_segments_title_end_and_empty_lines():
    script = "[开场]\n老周：开场白。\n\n【正片】\n（点头）\n"
    shots = script_parser.build_shots("节目标题", script, _assets())
    assert shots[0]["title"] == "节目标题"
    assert shots[0]["duration"] == 3.5
    assert shots[-1]["subtitle"] == "下期见"
    assert shots[-1]["duration"] == 3.5
    assert shots[1]["segment_id"] == "开场"
    assert shots[2]["segment_id"] == "正片"
    # 空台本只有片头 + 片尾
    empty = script_parser.build_shots("t", "", _assets())
    assert len(empty) == 2


# --------------------------------------------------------------------------- #
# Episode.script / 新建剧集
# --------------------------------------------------------------------------- #
def test_episode_script_patch_and_get(client, fresh_episode):
    episode_id = fresh_episode["episode"]["id"]
    script = "老周：你好\n阿栗：你好"
    patched = client.patch(f"/api/episodes/{episode_id}", json={"script": script})
    assert patched.status_code == 200
    assert patched.json()["script"] == script
    detail = client.get(f"/api/episodes/{episode_id}").json()
    assert detail["script"] == script
    assert "script" in detail


def test_create_episode_increments_and_inherits(client):
    project, first = _new_project_episode(client)
    response = client.post(f"/api/projects/{project['id']}/episodes", json={})
    assert response.status_code == 200, response.text
    second = response.json()
    assert second["no"] == 2
    assert second["status"] == "draft"
    assert second["shots_total"] == 0
    assert second["script"] == ""
    assert second["aspect"] == first["aspect"]
    assert second["target_duration"] == first["target_duration"]

    third = client.post(
        f"/api/projects/{project['id']}/episodes",
        json={"title": "自定义第 3 期", "target_duration": 180},
    ).json()
    assert third["no"] == 3
    assert third["title"] == "自定义第 3 期"
    assert third["target_duration"] == 180
    episodes = client.get(f"/api/projects/{project['id']}/episodes").json()
    assert [episode["no"] for episode in episodes] == [1, 2, 3]
    assert client.post("/api/projects/not_exists/episodes", json={}).status_code == 404


def test_create_episode_copies_assets_by_default(client):
    project, first = _new_project_episode(client)
    _add_character_assets(client, first["id"], images=["/media/characters/laozhou/portrait_from_concept.png"])
    second = client.post(f"/api/projects/{project['id']}/episodes", json={}).json()
    first_assets = client.get(f"/api/episodes/{first['id']}/assets").json()
    second_assets = client.get(f"/api/episodes/{second['id']}/assets").json()
    assert len(first_assets) == 2
    assert len(second_assets) == 2
    assert {asset["name"] for asset in second_assets} == {"老周", "阿栗"}
    assert {asset["voice"] for asset in second_assets} == {
        "zh-CN-YunxiNeural",
        "zh-CN-XiaoxiaoNeural",
    }
    assert all(asset["episode_id"] == second["id"] for asset in second_assets)
    assert {asset["images"][0] for asset in second_assets} == {
        "/media/characters/laozhou/portrait_from_concept.png"
    }
    assert {asset["id"] for asset in first_assets}.isdisjoint(
        {asset["id"] for asset in second_assets}
    )
    # 源集不受影响
    assert len(client.get(f"/api/episodes/{first['id']}/assets").json()) == 2


def test_create_episode_no_copy_assets(client):
    project, first = _new_project_episode(client)
    _add_character_assets(client, first["id"])
    third = client.post(
        f"/api/projects/{project['id']}/episodes",
        json={"no_copy_assets": True},
    ).json()
    assert client.get(f"/api/episodes/{third['id']}/assets").json() == []


def test_create_episode_copy_assets_from_specified(client):
    project, first = _new_project_episode(client)
    _add_character_assets(client, first["id"])
    second = client.post(f"/api/projects/{project['id']}/episodes", json={}).json()
    # 再给 first 加一个额外资产，验证 copy_assets_from 指定源集而非默认最新集
    client.post(
        f"/api/episodes/{first['id']}/assets",
        json={"role": "guest", "type": "character", "name": "大魏", "voice": "zh-CN-YunjianNeural"},
    )
    third = client.post(
        f"/api/projects/{project['id']}/episodes",
        json={"copy_assets_from": first["id"]},
    ).json()
    third_assets = client.get(f"/api/episodes/{third['id']}/assets").json()
    assert {asset["name"] for asset in third_assets} == {"老周", "阿栗", "大魏"}
    assert len(client.get(f"/api/episodes/{second['id']}/assets").json()) == 2


def test_create_episode_copy_assets_validation(client):
    project, first = _new_project_episode(client)
    assert (
        client.post(
            f"/api/projects/{project['id']}/episodes",
            json={"copy_assets_from": "not_exists"},
        ).status_code
        == 404
    )
    other_project, other_first = _new_project_episode(client)
    assert (
        client.post(
            f"/api/projects/{project['id']}/episodes",
            json={"copy_assets_from": other_first["id"]},
        ).status_code
        == 400
    )


def test_copied_assets_match_script_speakers(client):
    project, first = _new_project_episode(client)
    first_host, first_guest = _add_character_assets(client, first["id"])
    second = client.post(f"/api/projects/{project['id']}/episodes", json={}).json()
    second_assets = client.get(f"/api/episodes/{second['id']}/assets").json()
    by_name = {asset["name"]: asset["id"] for asset in second_assets}
    client.patch(
        f"/api/episodes/{second['id']}",
        json={"script": "老周：第一句。\n阿栗：第二句。"},
    )
    created = client.post(
        f"/api/episodes/{second['id']}/storyboard/generate", json={"force": True}
    ).json()["created"]
    assert created == 4
    shots = client.get(f"/api/episodes/{second['id']}/shots").json()
    dialogue = [shot for shot in shots if shot["line"]]
    assert [shot["speaker"] for shot in dialogue] == [by_name["老周"], by_name["阿栗"]]
    assert dialogue[0]["speaker"] != first_host["id"]
    assert dialogue[1]["speaker"] != first_guest["id"]


def test_migration_script_column(db_session):
    from sqlalchemy import inspect

    from app import models
    from app.db import engine, migrate_columns

    columns = {column["name"] for column in inspect(engine).get_columns("episodes")}
    assert "script" in columns
    episode = db_session.get(models.Episode, "ep_001")
    assert episode is not None
    assert (episode.script or "") == ""
    migrate_columns()  # 幂等，可重复执行


# --------------------------------------------------------------------------- #
# storyboard/generate（台本解析 + force 语义）
# --------------------------------------------------------------------------- #
def test_storyboard_from_script_and_force(client):
    project, episode = _new_project_episode(client)
    _add_character_assets(client, episode["id"])
    client.patch(f"/api/episodes/{episode['id']}", json={"script": SAMPLE_SCRIPT})
    first = client.post(f"/api/episodes/{episode['id']}/storyboard/generate", json={}).json()
    created = first["created"]
    assert created == 10  # 8 台本镜头（含行首动作拆出的 reaction）+ 片头 + 片尾
    shots = client.get(f"/api/episodes/{episode['id']}/shots").json()
    assert len(shots) == created
    assert [shot["no"] for shot in shots] == list(range(1, created + 1))
    assert shots[0]["type"] == "title_card"
    assert shots[0]["title"] == episode["title"]
    assert shots[-1]["type"] == "end_card"
    guest_lines = [shot for shot in shots if shot["speaker"] == "guest_a" or shot["speaker_name"] == "阿栗"]
    assert guest_lines, shots
    assert client.get(f"/api/episodes/{episode['id']}").json()["shots_total"] == created
    assert sum(shot["duration"] for shot in shots) > 0

    # 已有镜头且未 force -> created 0
    again = client.post(f"/api/episodes/{episode['id']}/storyboard/generate", json={}).json()
    assert again["created"] == 0
    # force=true -> 重建（id 变化、数量一致）
    old_ids = {shot["id"] for shot in shots}
    forced = client.post(
        f"/api/episodes/{episode['id']}/storyboard/generate", json={"force": True}
    ).json()
    assert forced["created"] == created
    new_shots = client.get(f"/api/episodes/{episode['id']}/shots").json()
    assert len(new_shots) == created
    assert not old_ids.intersection({shot["id"] for shot in new_shots})


def test_storyboard_empty_script_falls_back_to_skeleton(client, fresh_episode):
    episode = fresh_episode["episode"]
    created = client.post(f"/api/episodes/{episode['id']}/storyboard/generate", json={}).json()["created"]
    assert created >= 1
    shots = client.get(f"/api/episodes/{episode['id']}/shots").json()
    types = {shot["type"] for shot in shots}
    assert "title_card" in types and "end_card" in types
    assert client.get(f"/api/episodes/{episode['id']}").json()["shots_total"] == created


# --------------------------------------------------------------------------- #
# 镜头追加 / 删除 / 重排
# --------------------------------------------------------------------------- #
def test_append_and_delete_shots_reindex(client, fresh_episode):
    episode = fresh_episode["episode"]
    host_asset, _guest_asset = _add_character_assets(client, episode["id"])
    client.post(f"/api/episodes/{episode['id']}/storyboard/generate", json={})
    before = client.get(f"/api/episodes/{episode['id']}/shots").json()
    count = len(before)

    appended = client.post(
        f"/api/episodes/{episode['id']}/shots",
        json={"type": "host_to_camera", "line": "追加的台词", "speaker_name": "老周", "visual": "追加剧照"},
    )
    assert appended.status_code == 200, appended.text
    shot = appended.json()
    assert shot["no"] == count + 1
    assert shot["line"] == "追加的台词"
    assert shot["speaker"] == host_asset["id"]
    assert shot["duration"] == pytest.approx(script_parser.clamp_duration("追加的台词"))
    assert client.get(f"/api/episodes/{episode['id']}").json()["shots_total"] == count + 1

    extra = client.post(
        f"/api/episodes/{episode['id']}/shots",
        json={"type": "reaction", "visual": "（点头）"},
    ).json()
    assert extra["no"] == count + 2

    # 删除中间镜头 -> 剩余 no 连续
    deleted = client.delete(f"/api/shots/{shot['id']}")
    assert deleted.status_code == 200
    assert deleted.json()["ok"] is True
    assert deleted.json()["shots_total"] == count + 1
    remaining = client.get(f"/api/episodes/{episode['id']}/shots").json()
    assert len(remaining) == count + 1
    assert [item["no"] for item in remaining] == list(range(1, count + 2))
    assert shot["id"] not in {item["id"] for item in remaining}
    assert client.get(f"/api/episodes/{episode['id']}").json()["shots_total"] == count + 1

    assert client.delete("/api/shots/not_exists").status_code == 404


def test_append_shot_validation(client, fresh_episode):
    episode = fresh_episode["episode"]
    bad_type = client.post(f"/api/episodes/{episode['id']}/shots", json={"type": "weird"})
    assert bad_type.status_code == 400
    bad_duration = client.post(
        f"/api/episodes/{episode['id']}/shots", json={"duration": 0.1}
    )
    assert bad_duration.status_code == 422
    assert client.post("/api/episodes/not_exists/shots", json={}).status_code == 404


def test_render_serializes_actual_episode(client, monkeypatch, fresh_episode):
    """render 任务必须用实际集的 episode JSON（不是 demo 的 52 镜头）。"""
    import json
    from pathlib import Path

    from app import config, jobs

    episode = fresh_episode["episode"]
    _add_character_assets(client, episode["id"])
    client.patch(f"/api/episodes/{episode['id']}", json={"script": "老周：第一句台本。\n阿栗：第二句台本。"})
    client.post(f"/api/episodes/{episode['id']}/storyboard/generate", json={})
    shots = client.get(f"/api/episodes/{episode['id']}/shots").json()
    job = client.post(f"/api/episodes/{episode['id']}/render", json={}).json()

    captured = {}

    def fake_run_cli(cmd, cwd=None, on_line=None, timeout=None):
        episode_path = Path(cmd[cmd.index("--episode") + 1])
        captured["episode"] = json.loads(episode_path.read_text(encoding="utf-8"))
        rel_out = Path(cmd[cmd.index("--out") + 1])
        out = config.OUTPUTS_DIR.joinpath(*rel_out.parts[1:])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"fake-final")
        return 0, [json.dumps({"ok": True, "path": str(out)})], ""

    monkeypatch.setattr(jobs, "run_cli", fake_run_cli)
    jobs.run_job(job["id"])

    done = client.get(f"/api/jobs/{job['id']}").json()
    assert done["status"] == "done", done
    data = captured["episode"]
    assert data["episode"]["id"] == episode["id"]
    assert data["episode"]["title"] == episode["title"]
    assert len(data["shots"]) == len(shots) == 4
    assert data["shots"][0]["type"] == "title_card"
    assert data["shots"][0]["title"] == episode["title"]
    voices = {asset["voice"] for asset in data["assets"]}
    assert "zh-CN-YunxiNeural" in voices and "zh-CN-XiaoxiaoNeural" in voices
    assert data["total_duration"] == pytest.approx(sum(shot["duration"] for shot in shots), abs=0.01)
    assert done["output_url"] == f"/media/episodes/{episode['id']}/final.mp4"


def test_force_delete_shots_with_versions(client, db_session, fresh_episode):
    from app import models

    episode = fresh_episode["episode"]
    client.post(f"/api/episodes/{episode['id']}/storyboard/generate", json={})
    shot = client.get(f"/api/episodes/{episode['id']}/shots").json()[0]
    db_session.add(
        models.ShotVersion(
            id=models.new_id("sv"),
            shot_id=shot["id"],
            version=1,
            status="ready",
            video_url="/media/x.mp4",
            cost=0.0,
            params={},
        )
    )
    db_session.commit()
    jobs_before = db_session.query(models.ShotVersion).filter_by(shot_id=shot["id"]).count()
    assert jobs_before == 1

    client.patch(f"/api/episodes/{episode['id']}", json={"script": "老周：重建"})
    rebuilt = client.post(
        f"/api/episodes/{episode['id']}/storyboard/generate", json={"force": True}
    ).json()
    assert rebuilt["created"] == 3  # 片头 + 1 句 + 片尾
    db_session.expire_all()
    assert db_session.query(models.ShotVersion).filter_by(shot_id=shot["id"]).count() == 0
