"""contract/API.md 端点集成测试（FastAPI TestClient + 临时 SQLite）。"""

from __future__ import annotations

import uuid

import pytest


# --------------------------------------------------------------------------- #
# 基础 / 项目
# --------------------------------------------------------------------------- #
def test_health(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["version"]
    assert data["mode"] in ("mock", "comfyui")


def test_root_placeholder_without_frontend_dist(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "造片局" in response.text


def test_projects_seeded(client):
    response = client.get("/api/projects")
    assert response.status_code == 200
    projects = response.json()
    demo = [project for project in projects if project["id"] == "proj_zaopianju_demo"]
    assert demo, projects
    project = demo[0]
    for key in (
        "id",
        "name",
        "format_id",
        "aspect",
        "status",
        "budget",
        "spent",
        "created_at",
        "cover",
        "episode_count",
    ):
        assert key in project
    assert project["format_id"] == "roundtable_5min"
    assert project["episode_count"] == 1


def test_project_detail_and_404(client):
    response = client.get("/api/projects/proj_zaopianju_demo")
    assert response.status_code == 200
    assert response.json()["id"] == "proj_zaopianju_demo"
    missing = client.get("/api/projects/not_exists")
    assert missing.status_code == 404
    assert "detail" in missing.json()


def test_create_project_with_default_episode(client):
    name = f"新节目-{uuid.uuid4().hex[:6]}"
    response = client.post(
        "/api/projects",
        json={"name": name, "format_id": "roundtable_5min", "aspect": "16:9"},
    )
    assert response.status_code == 200, response.text
    project = response.json()
    assert project["id"].startswith("proj_")
    assert project["name"] == name
    assert project["episode_count"] == 1

    episodes_response = client.get(f"/api/projects/{project['id']}/episodes")
    assert episodes_response.status_code == 200
    episodes = episodes_response.json()
    assert len(episodes) == 1
    episode = episodes[0]
    assert episode["project_id"] == project["id"]
    assert episode["target_duration"] > 0
    detail = client.get(f"/api/episodes/{episode['id']}")
    assert detail.status_code == 200
    assert detail.json()["id"] == episode["id"]


def test_demo_episode_seeded(client):
    response = client.get("/api/episodes/ep_001")
    assert response.status_code == 200
    data = response.json()
    assert data["project_id"] == "proj_zaopianju_demo"
    assert data["target_duration"] == 296
    assert data["shots_total"] == 52
    assert data["shots_done"] == 0
    assert data["status"] == "draft"


def test_patch_episode(client, fresh_episode):
    episode_id = fresh_episode["episode"]["id"]
    response = client.patch(
        f"/api/episodes/{episode_id}",
        json={"title": "改过的标题", "target_duration": 300},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["title"] == "改过的标题"
    assert data["target_duration"] == 300
    assert client.get(f"/api/episodes/{episode_id}").json()["title"] == "改过的标题"
    # demo 集不应受影响
    assert client.get("/api/episodes/ep_001").json()["title"] == "AI 会取代综艺导演吗？"


def test_patch_episode_404(client):
    assert client.patch("/api/episodes/not_exists", json={"title": "x"}).status_code == 404


# --------------------------------------------------------------------------- #
# 资产
# --------------------------------------------------------------------------- #
def test_assets_seeded(client):
    response = client.get("/api/episodes/ep_001/assets")
    assert response.status_code == 200
    assets = response.json()
    assert len(assets) == 5
    by_id = {asset["id"]: asset for asset in assets}
    assert set(by_id) == {"host", "guest_a", "guest_b", "scene_studio", "scene_tech"}
    assert by_id["host"]["role"] == "host"
    assert by_id["host"]["voice"] == "zh-CN-YunxiNeural"
    assert by_id["guest_b"]["name"] == "大魏"
    assert by_id["scene_studio"]["type"] == "scene"
    assert by_id["host"]["locked"] is False
    assert isinstance(by_id["host"]["images"], list)


def test_create_and_patch_asset(client, fresh_episode):
    episode_id = fresh_episode["episode"]["id"]
    payload = {
        "role": "guest",
        "type": "character",
        "name": "测试嘉宾",
        "title": "测试",
        "persona": "测试人设",
        "voice": "zh-CN-XiaoxiaoNeural",
        "accent_color": "#123456",
        "images": [],
        "locked": False,
    }
    response = client.post(f"/api/episodes/{episode_id}/assets", json=payload)
    assert response.status_code == 200, response.text
    asset = response.json()
    assert asset["id"].startswith("asset_")
    assert asset["episode_id"] == episode_id
    assert asset["name"] == "测试嘉宾"

    patched = client.patch(
        f"/api/assets/{asset['id']}",
        json={"locked": True, "voice": "zh-CN-YunjianNeural", "persona": "新人设"},
    )
    assert patched.status_code == 200
    data = patched.json()
    assert data["locked"] is True
    assert data["voice"] == "zh-CN-YunjianNeural"
    assert data["persona"] == "新人设"
    assert len(client.get(f"/api/episodes/{episode_id}/assets").json()) == 1


def test_create_asset_validation(client, fresh_episode):
    episode_id = fresh_episode["episode"]["id"]
    bad_role = client.post(
        f"/api/episodes/{episode_id}/assets",
        json={"role": "naruto", "type": "character", "name": "x"},
    )
    assert bad_role.status_code == 400
    bad_type = client.post(
        f"/api/episodes/{episode_id}/assets",
        json={"role": "guest", "type": "weird", "name": "x"},
    )
    assert bad_type.status_code == 400
    assert client.patch("/api/assets/not_exists", json={"locked": True}).status_code == 404


# --------------------------------------------------------------------------- #
# 镜头
# --------------------------------------------------------------------------- #
def test_shots_seeded_52(client):
    response = client.get("/api/episodes/ep_001/shots")
    assert response.status_code == 200
    shots = response.json()
    assert len(shots) == 52
    assert [shot["id"] for shot in shots[:3]] == ["s001", "s002", "s003"]
    assert shots[-1]["id"] == "s052"
    assert {shot["segment_id"] for shot in shots} == {
        "open",
        "cold_open",
        "intro",
        "topic1",
        "topic2",
        "closing",
    }
    types = {shot["type"] for shot in shots}
    assert {
        "title_card",
        "host_to_camera",
        "shot_reverse",
        "reaction",
        "b_roll",
        "meme_card",
        "end_card",
    } <= types
    first = shots[0]
    for key in (
        "id",
        "episode_id",
        "segment_id",
        "no",
        "type",
        "duration",
        "speaker",
        "speaker_name",
        "line",
        "visual",
        "lower_third",
        "punchline",
        "sfx",
        "mood",
        "status",
        "selected_version_id",
    ):
        assert key in first
    assert first["no"] == 1
    assert shots[5]["lower_third"] == {"name": "阿栗", "title": "AI 内容创业者"}


def test_patch_shot_and_restore(client):
    shots = client.get("/api/episodes/ep_001/shots").json()
    original = next(shot for shot in shots if shot["id"] == "s002")
    try:
        response = client.patch(
            "/api/shots/s002",
            json={
                "line": "改过的台词",
                "duration": 6.5,
                "punchline": "加个花字",
                "visual": "新画面",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["line"] == "改过的台词"
        assert data["duration"] == 6.5
        assert data["punchline"] == "加个花字"
        assert data["visual"] == "新画面"
    finally:
        restored = client.patch(
            "/api/shots/s002",
            json={
                "line": original["line"],
                "duration": original["duration"],
                "punchline": original["punchline"],
                "visual": original["visual"],
            },
        )
        assert restored.status_code == 200
    assert client.patch("/api/shots/not_exists", json={"line": "x"}).status_code == 404
    assert client.patch("/api/shots/s002", json={"status": "weird"}).status_code == 400


def test_storyboard_generate_on_empty_episode(client, fresh_episode):
    episode_id = fresh_episode["episode"]["id"]
    assert client.get(f"/api/episodes/{episode_id}/shots").json() == []
    response = client.post(f"/api/episodes/{episode_id}/storyboard/generate")
    assert response.status_code == 200
    created = response.json()["created"]
    assert created >= 1
    shots = client.get(f"/api/episodes/{episode_id}/shots").json()
    assert len(shots) == created
    assert client.get(f"/api/episodes/{episode_id}").json()["shots_total"] == created
    assert client.post(f"/api/episodes/{episode_id}/storyboard/generate").json()["created"] == 0
    assert len(client.get(f"/api/episodes/{episode_id}/shots").json()) == created


def test_storyboard_generate_demo_noop(client):
    assert client.post("/api/episodes/ep_001/storyboard/generate").json()["created"] == 0
    assert len(client.get("/api/episodes/ep_001/shots").json()) == 52


# --------------------------------------------------------------------------- #
# 任务 / 版本
# --------------------------------------------------------------------------- #
def test_generate_shot_enqueues_job(client, fresh_episode):
    episode_id = fresh_episode["episode"]["id"]
    client.post(f"/api/episodes/{episode_id}/storyboard/generate")
    shot = client.get(f"/api/episodes/{episode_id}/shots").json()[0]
    response = client.post(f"/api/shots/{shot['id']}/generate", json={"params": {"seed": 7}})
    assert response.status_code == 200, response.text
    job = response.json()
    assert job["type"] == "shot"
    assert job["status"] == "queued"
    assert job["episode_id"] == episode_id
    assert job["shot_id"] == shot["id"]
    assert job["progress"] == 0.0

    detail = client.get(f"/api/jobs/{job['id']}")
    assert detail.status_code == 200
    assert detail.json()["id"] == job["id"]

    listed = client.get(
        "/api/jobs", params={"episode_id": episode_id, "type": "shot", "status": "queued"}
    ).json()
    assert any(item["id"] == job["id"] for item in listed)

    retried = client.post(f"/api/jobs/{job['id']}/retry")
    assert retried.status_code == 200
    assert retried.json()["status"] == "queued"
    assert client.get(f"/api/episodes/{episode_id}/shots").json()[0]["status"] == "queued"


def test_generate_shot_404(client):
    assert client.post("/api/shots/not_exists/generate", json={}).status_code == 404
    assert client.get("/api/jobs/not_exists").status_code == 404
    assert client.post("/api/jobs/not_exists/retry").status_code == 404


def test_versions_list_and_select(client, fresh_episode, db_session):
    from app import models

    episode_id = fresh_episode["episode"]["id"]
    client.post(f"/api/episodes/{episode_id}/storyboard/generate")
    shots = client.get(f"/api/episodes/{episode_id}/shots").json()
    shot = shots[0]
    version = models.ShotVersion(
        id=models.new_id("sv"),
        shot_id=shot["id"],
        version=1,
        status="ready",
        video_url=f"/media/episodes/{episode_id}/shots/{shot['id']}.mp4",
        thumb_url=None,
        cost=0.0,
        params={"provider": "mock"},
    )
    db_session.add(version)
    db_session.commit()

    versions = client.get(f"/api/shots/{shot['id']}/versions").json()
    assert [item["id"] for item in versions] == [version.id]
    assert versions[0]["video_url"].startswith("/media/")

    selected = client.post(f"/api/shots/{shot['id']}/versions/{version.id}/select")
    assert selected.status_code == 200
    data = selected.json()
    assert data["selected_version_id"] == version.id
    assert data["status"] == "ready"
    assert data["video_url"] == version.video_url

    other_shot = shots[1]
    wrong = client.post(f"/api/shots/{other_shot['id']}/versions/{version.id}/select")
    assert wrong.status_code == 404


def test_render_enqueues_job(client):
    response = client.post("/api/episodes/ep_001/render", json={})
    assert response.status_code == 200
    job = response.json()
    assert job["type"] == "render"
    assert job["status"] == "queued"
    assert job["episode_id"] == "ep_001"
    listed = client.get("/api/jobs", params={"episode_id": "ep_001", "type": "render"}).json()
    assert any(item["id"] == job["id"] for item in listed)


def test_render_404(client):
    assert client.post("/api/episodes/not_exists/render", json={}).status_code == 404


def test_generate_and_render_accept_empty_body(client, fresh_episode):
    """前端/用户可能不带 body 直接 POST，接口应兼容。"""
    episode_id = fresh_episode["episode"]["id"]
    client.post(f"/api/episodes/{episode_id}/storyboard/generate")
    shot = client.get(f"/api/episodes/{episode_id}/shots").json()[0]
    generated = client.post(f"/api/shots/{shot['id']}/generate")
    assert generated.status_code == 200, generated.text
    assert generated.json()["type"] == "shot"
    rendered = client.post(f"/api/episodes/{episode_id}/render")
    assert rendered.status_code == 200, rendered.text
    assert rendered.json()["type"] == "render"


# 注：/api/events 是可选 SSE 端点；Starlette TestClient 会缓冲流式响应，
# 放进来会把 300s 的 SSE 生命期拉进测试，因此改由联调脚本对活服务验证（见 backend/README.md）。


# --------------------------------------------------------------------------- #
# 费用 / 设置 / 静态 / 播种幂等
# --------------------------------------------------------------------------- #
def test_costs_structure(client):
    response = client.get("/api/episodes/ep_001/costs")
    assert response.status_code == 200
    data = response.json()
    assert data["currency"] == "CNY"
    assert isinstance(data["total"], (int, float))
    assert isinstance(data["by_type"], dict)
    assert isinstance(data["items"], list)
    assert client.get("/api/episodes/not_exists/costs").status_code == 404


def test_settings_get_and_patch(client):
    original = client.get("/api/settings").json()
    assert set(original) == {
        "generation_mode",
        "comfyui_url",
        "workflow_template",
        "resolution",
        "fps",
        "tts_provider",
        "voice_map",
        "h3_price_per_second",
        "h3_provider",
        "metaso_base_url",
        "metaso_resolution",
        "metaso_duration",
        "metaso_key_configured",
    }
    try:
        response = client.patch(
            "/api/settings",
            json={"generation_mode": "comfyui", "h3_price_per_second": 0.12, "fps": 30},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["generation_mode"] == "comfyui"
        assert data["h3_price_per_second"] == 0.12
        assert data["fps"] == 30
        assert client.get("/api/health").json()["mode"] == "comfyui"
        bad = client.patch("/api/settings", json={"generation_mode": "weird"})
        assert bad.status_code == 400
    finally:
        client.patch(
            "/api/settings",
            json={
                "generation_mode": original["generation_mode"],
                "h3_price_per_second": original["h3_price_per_second"],
                "fps": original["fps"],
            },
        )
    assert client.get("/api/settings").json()["generation_mode"] == original["generation_mode"]


def test_media_static_files(client):
    from app import config

    target = config.OUTPUTS_DIR / "pytest-media" / "hello.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("media-ok", encoding="utf-8")
    response = client.get("/media/pytest-media/hello.txt")
    assert response.status_code == 200
    assert response.text == "media-ok"
    assert client.get("/media/pytest-media/not-exists.txt").status_code == 404


def test_episode_stats_are_recomputed_lazily(client, db_session, fresh_episode):
    """并发任务可能把旧快照写进 shots_done/progress；读接口必须按真实 COUNT 重算。"""
    from app import models

    episode_id = fresh_episode["episode"]["id"]
    project_id = fresh_episode["project"]["id"]
    client.post(f"/api/episodes/{episode_id}/storyboard/generate")
    shots = client.get(f"/api/episodes/{episode_id}/shots").json()
    assert len(shots) >= 2

    # 模拟旧快照
    episode = db_session.get(models.Episode, episode_id)
    episode.shots_total = 999
    episode.shots_done = 1
    episode.progress = 2.0
    db_session.commit()

    # 标记两个镜头已有选中版本（计数只看 selected_version_id）
    for shot in shots[:2]:
        row = db_session.get(models.Shot, shot["id"])
        row.selected_version_id = "fake_version_" + shot["id"]
    db_session.commit()

    detail = client.get(f"/api/episodes/{episode_id}").json()
    assert detail["shots_total"] == len(shots)
    assert detail["shots_done"] == 2
    assert detail["progress"] == round(2 * 100.0 / len(shots), 1)

    listed = client.get(f"/api/projects/{project_id}/episodes").json()
    assert listed[0]["shots_done"] == 2
    assert listed[0]["shots_total"] == len(shots)

    # 成片已存在时 progress 固定 100
    episode = db_session.get(models.Episode, episode_id)
    episode.render_url = "/media/episodes/fake/final.mp4"
    episode.shots_done = 1
    db_session.commit()
    refreshed = client.get(f"/api/episodes/{episode_id}").json()
    assert refreshed["progress"] == 100
    assert refreshed["shots_done"] == 2


def test_startup_recovery_marks_stale_jobs(client, fresh_episode):
    """模拟服务重启：遗留 queued/running 任务应被标记 failed 且可 retry。"""
    from app.main import _recover_stale_jobs

    episode_id = fresh_episode["episode"]["id"]
    client.post(f"/api/episodes/{episode_id}/storyboard/generate")
    shot = client.get(f"/api/episodes/{episode_id}/shots").json()[0]
    job = client.post(f"/api/shots/{shot['id']}/generate", json={}).json()

    _recover_stale_jobs()

    recovered = client.get(f"/api/jobs/{job['id']}").json()
    assert recovered["status"] == "failed"
    assert "重启" in recovered["error"]
    assert client.get(f"/api/episodes/{episode_id}/shots").json()[0]["status"] == "failed"

    retried = client.post(f"/api/jobs/{job['id']}/retry").json()
    assert retried["status"] == "queued"


def test_seed_is_idempotent(db_session):
    from app import models, seed

    projects_before = db_session.query(models.Project).count()
    assert seed.seed_if_empty(db_session) is False
    assert seed.seed_demo(db_session) is False
    assert db_session.query(models.Project).count() == projects_before
    assert db_session.query(models.Shot).filter(models.Shot.episode_id == "ep_001").count() == 52
    assert db_session.query(models.Asset).filter(models.Asset.episode_id == "ep_001").count() == 5
