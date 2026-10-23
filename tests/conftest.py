"""pytest 全局配置：临时数据目录 + 关闭自动任务 + TestClient。"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# 必须在导入 app.* 之前设置：测试使用独立临时库与产物目录
_TMP_ROOT = Path(tempfile.mkdtemp(prefix="zaopianju-tests-"))
os.environ.setdefault("ZAOPIANJU_DATA_DIR", str(_TMP_ROOT / "data"))
os.environ.setdefault("ZAOPIANJU_OUTPUTS_DIR", str(_TMP_ROOT / "outputs"))
os.environ.setdefault("ZAOPIANJU_FRONTEND_DIST", str(_TMP_ROOT / "no-frontend-dist"))
os.environ.setdefault("ZAOPIANJU_JOBS_AUTORUN", "0")

# 让测试者知道临时目录（排障用）
print(f"[conftest] test data root: {_TMP_ROOT}")


@pytest.fixture(scope="session")
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def db_session():
    from app.db import SessionLocal

    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture()
def fresh_episode(client):
    """新建一个项目（自带第 1 集），与 demo 数据隔离。"""
    import uuid

    resp = client.post(
        "/api/projects",
        json={"name": f"pytest 项目 {uuid.uuid4().hex[:6]}", "format_id": "roundtable_5min", "aspect": "16:9"},
    )
    assert resp.status_code == 200, resp.text
    project = resp.json()
    episodes = client.get(f"/api/projects/{project['id']}/episodes").json()
    assert episodes, "新建项目应自带一集"
    return {"project": project, "episode": episodes[0]}
