"""造片局后端配置（demo）。

路径全部基于仓库根目录解析；可用环境变量覆盖（测试隔离也靠它）：
- ZAOPIANJU_DATA_DIR       数据目录，默认 backend/data
- ZAOPIANJU_DB             SQLite 文件，默认 <data>/zaopianju.db
- ZAOPIANJU_OUTPUTS_DIR    生成产物目录，默认 <repo>/outputs
- ZAOPIANJU_FRONTEND_DIST  前端构建产物目录，默认 <repo>/frontend/dist
- ZAOPIANJU_JOBS_AUTORUN   0/false 时任务只入队不自动执行（测试用）
"""

from __future__ import annotations

import os
from pathlib import Path

VERSION = "1.0.0"
APP_NAME = "造片局 · AI 微综艺 API"

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_DIR = REPO_ROOT / "contract"
DEMO_EPISODE_PATH = CONTRACT_DIR / "demo_episode.json"
FORMATS_DIR = CONTRACT_DIR / "formats"


def _env_path(name: str, default: Path) -> Path:
    raw = os.environ.get(name)
    if not raw:
        return default
    p = Path(raw)
    return p if p.is_absolute() else (REPO_ROOT / p)


DATA_DIR = _env_path("ZAOPIANJU_DATA_DIR", REPO_ROOT / "backend" / "data")
DB_PATH = _env_path("ZAOPIANJU_DB", DATA_DIR / "zaopianju.db")
OUTPUTS_DIR = _env_path("ZAOPIANJU_OUTPUTS_DIR", REPO_ROOT / "outputs")
FRONTEND_DIST = _env_path("ZAOPIANJU_FRONTEND_DIST", REPO_ROOT / "frontend" / "dist")
JOBS_TMP_DIR = DATA_DIR / "jobs"

JOBS_AUTORUN = os.environ.get("ZAOPIANJU_JOBS_AUTORUN", "1").strip().lower() not in {
    "0",
    "false",
    "no",
    "off",
}

API_PREFIX = "/api"
MEDIA_URL_PREFIX = "/media"


def ensure_dirs() -> None:
    """确保运行所需目录存在。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    JOBS_TMP_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUTS_DIR / "episodes").mkdir(parents=True, exist_ok=True)
    (OUTPUTS_DIR / "characters").mkdir(parents=True, exist_ok=True)
    (OUTPUTS_DIR / "concepts").mkdir(parents=True, exist_ok=True)
    (OUTPUTS_DIR / "exports").mkdir(parents=True, exist_ok=True)
    (OUTPUTS_DIR / "h3").mkdir(parents=True, exist_ok=True)


def media_url(path: str | Path) -> str:
    """把 outputs 下的文件路径转成 /media/... URL。"""
    p = Path(path)
    try:
        rel = p.resolve().relative_to(OUTPUTS_DIR.resolve())
    except (ValueError, OSError):
        rel = Path(p.name)
    return f"{MEDIA_URL_PREFIX}/{rel.as_posix()}"
