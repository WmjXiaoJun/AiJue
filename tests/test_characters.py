"""角色工坊 API 测试：CRUD / 资产联动 / files / 生成任务（假 studio3d CLI）。"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

FLAT_CHARACTER = {
    "name": "测试角色",
    "title": "测试嘉宾",
    "persona": "用来联调角色工坊",
    "voice": "zh-CN-XiaoxiaoNeural",
    "accent_color": "#7BD389",
    "style": {"primary": "2d_cel", "variants": ["2d_cel", "3d_toon"]},
    "appearance": {
        "gender": "female",
        "age": 22,
        "hair_style": "bob",
        "outfit": {"type": "hoodie"},
    },
}


def _create_project(client):
    project = client.post(
        "/api/projects",
        json={"name": f"角色测试 {uuid.uuid4().hex[:6]}", "format_id": "roundtable_5min"},
    ).json()
    episode = client.get(f"/api/projects/{project['id']}/episodes").json()[0]
    return project, episode


def _create_character(client, project_id, payload=None):
    response = client.post(
        f"/api/projects/{project_id}/characters", json=payload or dict(FLAT_CHARACTER)
    )
    assert response.status_code == 200, response.text
    return response.json()


def _asset_of(client, episode_id, asset_id):
    assets = client.get(f"/api/episodes/{episode_id}/assets").json()
    return next(asset for asset in assets if asset["id"] == asset_id)


# --------------------------------------------------------------------------- #
# 种子角色 / 列表
# --------------------------------------------------------------------------- #
def test_seeded_characters(client):
    response = client.get("/api/projects/proj_zaopianju_demo/characters")
    assert response.status_code == 200
    characters = response.json()
    assert len(characters) == 3
    by_id = {character["id"]: character for character in characters}
    assert set(by_id) == {"laozhou", "ali", "dawei"}
    assert by_id["laozhou"]["name"] == "老周"
    assert by_id["laozhou"]["title"] == "主持人"
    assert by_id["laozhou"]["asset_id"] == "host"
    assert by_id["ali"]["asset_id"] == "guest_a"
    assert by_id["dawei"]["asset_id"] == "guest_b"
    assert by_id["laozhou"]["style"] == {"primary": "3d_toon", "variants": ["3d_toon", "2d_cel"]}
    assert by_id["ali"]["style"]["primary"] == "2d_cel"
    assert by_id["laozhou"]["status"] == "pending"

    character = by_id["laozhou"]
    for key in (
        "id",
        "project_id",
        "asset_id",
        "name",
        "title",
        "persona",
        "voice",
        "accent_color",
        "style",
        "spec",
        "portrait_url",
        "sheet_url",
        "turntable_url",
        "expressions",
        "status",
        "created_at",
    ):
        assert key in character
    assert character["spec"]["appearance"]["gender"] == "male"
    assert character["spec"]["appearance"]["hair_style"] == "short_swept"

    # 资产联动：images 指向 portrait，且 name/persona 与角色一致
    host = _asset_of(client, "ep_001", "host")
    assert host["type"] == "character"
    assert host["name"] == "老周"
    assert host["voice"] == "zh-CN-YunxiNeural"
    assert host["images"] == ["/media/characters/laozhou/portrait.png"]


def test_list_characters_project_404(client):
    assert client.get("/api/projects/not_exists/characters").status_code == 404


# --------------------------------------------------------------------------- #
# 创建 / 详情
# --------------------------------------------------------------------------- #
def test_create_flat_character_and_asset(client):
    project, episode = _create_project(client)
    character = _create_character(client, project["id"])
    assert character["id"].startswith("char_")
    assert character["project_id"] == project["id"]
    assert character["name"] == "测试角色"
    assert character["title"] == "测试嘉宾"
    assert character["accent_color"] == "#7BD389"
    assert character["style"] == {"primary": "2d_cel", "variants": ["2d_cel", "3d_toon"]}
    assert character["spec"]["appearance"]["hair_style"] == "bob"
    assert character["spec"]["id"] == character["id"]
    assert character["status"] == "pending"
    assert character["asset_id"]

    detail = client.get(f"/api/characters/{character['id']}")
    assert detail.status_code == 200
    assert detail.json()["id"] == character["id"]

    asset = _asset_of(client, episode["id"], character["asset_id"])
    assert asset["type"] == "character"
    assert asset["name"] == "测试角色"
    assert asset["role"] == "guest"
    assert asset["persona"] == FLAT_CHARACTER["persona"]
    assert asset["images"] == [f"/media/characters/{character['id']}/portrait.png"]


def test_create_character_with_spec_wrapper(client):
    project, _episode = _create_project(client)
    character_id = f"wrapped_{uuid.uuid4().hex[:6]}"
    response = client.post(
        f"/api/projects/{project['id']}/characters",
        json={
            "spec": {
                "id": character_id,
                "name": "包裹角色",
                "title": "测试",
                "style": {"primary": "anime"},
                "appearance": {"gender": "male", "age": 30},
            }
        },
    )
    assert response.status_code == 200, response.text
    character = response.json()
    assert character["id"] == character_id
    assert character["name"] == "包裹角色"
    assert character["style"] == {"primary": "anime", "variants": ["anime"]}


def test_create_character_validation(client):
    project, _episode = _create_project(client)
    url = f"/api/projects/{project['id']}/characters"
    assert client.post(url, json={}).status_code == 400
    assert client.post(url, json={"name": "x", "style": {"primary": "weird"}}).status_code == 400
    assert client.post(url, json={"name": "x", "accent_color": "red"}).status_code == 400

    spec = {"spec": {"id": f"dup_{uuid.uuid4().hex[:6]}", "name": "重复角色"}}
    assert client.post(url, json=spec).status_code == 200
    assert client.post(url, json=spec).status_code == 409


def test_get_character_404(client):
    assert client.get("/api/characters/not_exists").status_code == 404


# --------------------------------------------------------------------------- #
# PATCH
# --------------------------------------------------------------------------- #
def test_patch_character_syncs_asset(client):
    project, episode = _create_project(client)
    character = _create_character(client, project["id"])
    response = client.patch(
        f"/api/characters/{character['id']}",
        json={
            "name": "新名字",
            "title": "新头衔",
            "persona": "新人设",
            "voice": "zh-CN-YunjianNeural",
            "accent_color": "#112233",
        },
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["name"] == "新名字"
    assert data["persona"] == "新人设"
    assert data["accent_color"] == "#112233"
    asset = _asset_of(client, episode["id"], character["asset_id"])
    assert asset["name"] == "新名字"
    assert asset["persona"] == "新人设"
    assert asset["voice"] == "zh-CN-YunjianNeural"
    assert asset["accent_color"] == "#112233"

    # spec patch 派生 style/title
    patched = client.patch(
        f"/api/characters/{character['id']}",
        json={"spec": {"style": {"primary": "3d_cel", "variants": ["3d_cel"]}, "title": "spec 头衔"}},
    )
    assert patched.status_code == 200
    assert patched.json()["style"]["primary"] == "3d_cel"
    assert patched.json()["title"] == "spec 头衔"

    assert (
        client.patch(f"/api/characters/{character['id']}", json={"accent_color": "red"}).status_code
        == 400
    )
    assert client.patch("/api/characters/not_exists", json={"name": "x"}).status_code == 404


# --------------------------------------------------------------------------- #
# files（懒同步磁盘产物）
# --------------------------------------------------------------------------- #
def test_character_files_empty(client):
    response = client.get("/api/characters/laozhou/files")
    assert response.status_code == 200
    assert response.json() == {
        "portrait": None,
        "sheet": None,
        "turntable": None,
        "expressions": {},
        "meta": {},
    }


def test_character_files_lazy_sync_from_disk(client):
    from app import config

    project, episode = _create_project(client)
    character = _create_character(client, project["id"])
    base = config.OUTPUTS_DIR / "characters" / character["id"]
    (base / "expressions").mkdir(parents=True, exist_ok=True)
    (base / "portrait.png").write_bytes(b"png")
    (base / "sheet.png").write_bytes(b"png")
    (base / "turntable.mp4").write_bytes(b"mp4")
    for name in ("neutral", "happy", "surprised", "thinking"):
        (base / "expressions" / f"{name}.png").write_bytes(b"png")
    (base / "meta.json").write_text(
        json.dumps({"id": character["id"], "renderer": "blender"}), encoding="utf-8"
    )

    detail = client.get(f"/api/characters/{character['id']}").json()
    assert detail["status"] == "ready"
    assert detail["portrait_url"] == f"/media/characters/{character['id']}/portrait.png"
    assert detail["sheet_url"].endswith("/sheet.png")
    assert detail["turntable_url"].endswith("/turntable.mp4")
    assert set(detail["expressions"]) == {"neutral", "happy", "surprised", "thinking"}

    files = client.get(f"/api/characters/{character['id']}/files").json()
    assert files["portrait"] == detail["portrait_url"]
    assert set(files["expressions"]) == {"neutral", "happy", "surprised", "thinking"}
    assert files["meta"]["renderer"] == "blender"

    asset = _asset_of(client, episode["id"], character["asset_id"])
    assert asset["images"] == [detail["portrait_url"]]


def test_character_files_portrait_only_is_ready(client):
    """只有 portrait + 表情、缺 sheet/turntable 时也应视为 ready（前端占位缺失项）。"""
    from app import config

    project, _episode = _create_project(client)
    character = _create_character(client, project["id"])
    base = config.OUTPUTS_DIR / "characters" / character["id"]
    (base / "expressions").mkdir(parents=True, exist_ok=True)
    (base / "portrait.png").write_bytes(b"png")
    for name in ("neutral", "happy", "surprised", "thinking"):
        (base / "expressions" / f"{name}.png").write_bytes(b"png")

    detail = client.get(f"/api/characters/{character['id']}").json()
    assert detail["status"] == "ready"
    assert detail["portrait_url"] == f"/media/characters/{character['id']}/portrait.png"
    assert detail["sheet_url"] is None
    assert detail["turntable_url"] is None
    assert set(detail["expressions"]) == {"neutral", "happy", "surprised", "thinking"}

    files = client.get(f"/api/characters/{character['id']}/files").json()
    assert files["portrait"] == detail["portrait_url"]
    assert files["sheet"] is None
    assert files["turntable"] is None
    assert len(files["expressions"]) == 4


# --------------------------------------------------------------------------- #
# 生成任务
# --------------------------------------------------------------------------- #
def test_generate_character_enqueues_job(client):
    project, _episode = _create_project(client)
    character = _create_character(client, project["id"])
    response = client.post(
        f"/api/characters/{character['id']}/generate", json={"styles": ["2d_cel"]}
    )
    assert response.status_code == 200, response.text
    job = response.json()
    assert job["type"] == "character"
    assert job["status"] == "queued"
    assert job["character_id"] == character["id"]
    assert job["episode_id"]
    assert client.get(f"/api/characters/{character['id']}").json()["status"] == "generating"
    listed = client.get("/api/jobs", params={"type": "character", "status": "queued"}).json()
    assert any(item["id"] == job["id"] for item in listed)
    # 同一角色重复触发复用活跃任务
    again = client.post(f"/api/characters/{character['id']}/generate", json={}).json()
    assert again["id"] == job["id"]
    # 非法 style / 不存在角色
    other = _create_character(client, project["id"], {"name": "另一个角色"})
    assert (
        client.post(f"/api/characters/{other['id']}/generate", json={"styles": ["bad"]}).status_code
        == 400
    )
    assert client.post("/api/characters/not_exists/generate", json={}).status_code == 404


def _fake_studio_cli(character_id, calls, *, ok=True, stderr="", message=""):
    def fake_run_cli(cmd, cwd=None, on_line=None):
        calls.append(list(cmd))
        if not ok:
            return 1, [], stderr
        outdir = Path(cmd[cmd.index("--outdir") + 1])
        (outdir / "expressions").mkdir(parents=True, exist_ok=True)
        (outdir / "portrait.png").write_bytes(b"portrait-png")
        (outdir / "sheet.png").write_bytes(b"sheet-png")
        (outdir / "turntable.mp4").write_bytes(b"turntable-mp4")
        for name in ("neutral", "happy", "surprised", "thinking"):
            (outdir / "expressions" / f"{name}.png").write_bytes(b"expression-png")
        (outdir / "meta.json").write_text(
            json.dumps({"id": character_id, "renderer": "blender", "styles": ["2d_cel"]}),
            encoding="utf-8",
        )
        line = json.dumps(
            {"ok": True, "outdir": str(outdir), "files": ["portrait.png"], "elapsed_sec": 1.0},
            ensure_ascii=False,
        )
        return 0, [line], message

    return fake_run_cli


def test_character_generation_success_with_fake_cli(client, monkeypatch):
    from app import jobs

    project, episode = _create_project(client)
    character = _create_character(client, project["id"])
    job = client.post(
        f"/api/characters/{character['id']}/generate", json={"styles": ["2d_cel"]}
    ).json()
    calls = []
    monkeypatch.setattr(jobs, "run_cli", _fake_studio_cli(character["id"], calls))
    jobs.run_job(job["id"])

    done = client.get(f"/api/jobs/{job['id']}").json()
    assert done["status"] == "done", done
    assert done["progress"] == 100
    assert done["character_id"] == character["id"]
    expected_portrait = f"/media/characters/{character['id']}/portrait.png"
    assert done["output_url"] == expected_portrait

    detail = client.get(f"/api/characters/{character['id']}").json()
    assert detail["status"] == "ready"
    assert detail["portrait_url"] == expected_portrait
    assert detail["sheet_url"].endswith("/sheet.png")
    assert detail["turntable_url"].endswith("/turntable.mp4")
    assert set(detail["expressions"]) == {"neutral", "happy", "surprised", "thinking"}

    files = client.get(f"/api/characters/{character['id']}/files").json()
    assert files["meta"]["renderer"] == "blender"
    assert client.get(expected_portrait).status_code == 200

    asset = _asset_of(client, episode["id"], character["asset_id"])
    assert asset["images"] == [expected_portrait]

    # 命令形状：python -m studio3d.cli create --spec ... --outdir ... --styles 2d_cel
    assert calls[0][1:4] == ["-m", "studio3d.cli", "create"]
    assert "--spec" in calls[0] and "--outdir" in calls[0]
    assert calls[0][calls[0].index("--styles") + 1] == "2d_cel"


def test_character_generation_failure_with_fake_cli(client, monkeypatch):
    from app import jobs

    project, _episode = _create_project(client)
    character = _create_character(client, project["id"])
    job = client.post(f"/api/characters/{character['id']}/generate", json={}).json()

    def fake(cmd, cwd=None, on_line=None):
        return 1, [], "Blender not found: C:/Program Files/Blender Foundation"

    monkeypatch.setattr(jobs, "run_cli", fake)
    jobs.run_job(job["id"])

    done = client.get(f"/api/jobs/{job['id']}").json()
    assert done["status"] == "failed"
    assert done["error"] and "studio3d create" in done["error"]
    assert "Blender not found" in done["error"]
    detail = client.get(f"/api/characters/{character['id']}").json()
    assert detail["status"] == "failed"
    assert detail["portrait_url"] is None
    assert client.get(f"/api/characters/{character['id']}/files").json()["portrait"] is None


def test_character_generation_launch_exception(client, monkeypatch):
    from app import jobs

    project, _episode = _create_project(client)
    character = _create_character(client, project["id"])
    job = client.post(f"/api/characters/{character['id']}/generate", json={}).json()

    def fake(cmd, cwd=None, on_line=None):
        raise FileNotFoundError("python 不可用")

    monkeypatch.setattr(jobs, "run_cli", fake)
    jobs.run_job(job["id"])

    done = client.get(f"/api/jobs/{job['id']}").json()
    assert done["status"] == "failed"
    assert done["error"] and "studio3d 启动失败" in done["error"]
    assert client.get(f"/api/characters/{character['id']}").json()["status"] == "failed"


def test_seed_characters_idempotent(db_session):
    from app import models, seed

    before = db_session.query(models.Character).count()
    assert seed.seed_characters(db_session) == 0
    assert db_session.query(models.Character).count() == before
    assert before >= 3
