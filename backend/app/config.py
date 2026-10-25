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
METASO_ENV_PATH = DATA_DIR / "metaso.env"
PUBLISH_ENV_PATH = DATA_DIR / "publish.env"
LLM_ENV_PATH = DATA_DIR / "llm.env"
LLM_ENV_KEYS = ("LLM_PROVIDER", "LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY")


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


def metaso_key() -> str | None:
    """读取 MetaSo API Key：环境变量 METASO_API_KEY 优先，其次 backend/data/metaso.env。"""
    value = (os.environ.get("METASO_API_KEY") or "").strip()
    if value:
        return value
    path = METASO_ENV_PATH
    if not path.is_file():
        return None
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            text = line.strip()
            if not text or text.startswith("#") or "=" not in text:
                continue
            name, _, raw = text.partition("=")
            if name.strip().upper() in ("METASO_API_KEY", "KEY", "TOKEN"):
                candidate = raw.strip().strip('"').strip("'")
                if candidate:
                    return candidate
    except OSError:
        return None
    return None


def metaso_key_configured() -> bool:
    return bool(metaso_key())


def save_metaso_key(raw: str | None) -> bool:
    """把 MetaSo key 写入/清除 backend/data/metaso.env（保留其它行），返回是否已配置。"""
    value = (raw or "").strip()
    path = METASO_ENV_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    if path.is_file():
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            lines = []
    output = []
    replaced = False
    for line in lines:
        text = line.strip()
        name = text.partition("=")[0].strip().upper() if "=" in text else ""
        if name in ("METASO_API_KEY", "KEY", "TOKEN"):
            if value and not replaced:
                output.append("METASO_API_KEY=" + value)
                replaced = True
            continue
        output.append(line)
    if value and not replaced:
        output.append("METASO_API_KEY=" + value)
    payload = "\n".join(output).strip()
    try:
        if payload:
            path.write_text(payload + "\n", encoding="utf-8")
        elif path.is_file():
            path.unlink()
    except OSError:
        return bool(value)
    return bool(value)


def publish_key() -> str | None:
    """读取 Upload-Post API Key：env UPLOAD_POST_API_KEY 优先，其次 backend/data/publish.env。"""
    value = (os.environ.get("UPLOAD_POST_API_KEY") or "").strip()
    if value:
        return value
    path = PUBLISH_ENV_PATH
    if not path.is_file():
        return None
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            text = line.strip()
            if not text or text.startswith("#") or "=" not in text:
                continue
            name, _, raw = text.partition("=")
            if name.strip().upper() in ("UPLOAD_POST_API_KEY", "KEY", "TOKEN"):
                candidate = raw.strip().strip('"').strip("'")
                if candidate:
                    return candidate
    except OSError:
        return None
    return None


def publish_key_configured() -> bool:
    return bool(publish_key())


def save_publish_key(raw: str | None) -> bool:
    """把 Upload-Post key 写入/清除 backend/data/publish.env（保留其它行）。"""
    value = (raw or "").strip()
    path = PUBLISH_ENV_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    if path.is_file():
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            lines = []
    output = []
    replaced = False
    for line in lines:
        text = line.strip()
        name = text.partition("=")[0].strip().upper() if "=" in text else ""
        if name in ("UPLOAD_POST_API_KEY", "KEY", "TOKEN"):
            if value and not replaced:
                output.append("UPLOAD_POST_API_KEY=" + value)
                replaced = True
            continue
        output.append(line)
    if value and not replaced:
        output.append("UPLOAD_POST_API_KEY=" + value)
    payload = "\n".join(output).strip()
    try:
        if payload:
            path.write_text(payload + "\n", encoding="utf-8")
        elif path.is_file():
            path.unlink()
    except OSError:
        return bool(value)
    return bool(value)


def llm_file_values() -> dict:
    """读取 backend/data/llm.env 中的 LLM_*（环境变量优先级由 llm.load_config 处理）。"""
    path = LLM_ENV_PATH
    values: dict = {}
    if not path.is_file():
        return values
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            text = line.strip()
            if not text or text.startswith("#") or "=" not in text:
                continue
            name, _, raw = text.partition("=")
            name = name.strip().upper()
            if name in LLM_ENV_KEYS:
                values[name] = raw.strip().strip('"').strip("'")
    except OSError:
        return {}
    return values


def save_llm_config(updates: dict) -> None:
    """把 LLM_* 写入 backend/data/llm.env（保留其它行；空值表示删除该行）。不写日志。"""
    path = LLM_ENV_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    if path.is_file():
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            lines = []
    wanted = {str(key).upper(): str(value).strip() for key, value in updates.items() if str(key).upper() in LLM_ENV_KEYS}
    output = []
    replaced = set()
    for line in lines:
        text = line.strip()
        name = text.partition("=")[0].strip().upper() if "=" in text else ""
        if name in wanted:
            value = wanted[name]
            if value and name not in replaced:
                output.append(f"{name}={value}")
                replaced.add(name)
            continue
        output.append(line)
    for name, value in wanted.items():
        if value and name not in replaced:
            output.append(f"{name}={value}")
    payload = "\n".join(output).strip()
    try:
        if payload:
            path.write_text(payload + "\n", encoding="utf-8")
            try:
                path.chmod(0o600)
            except OSError:
                pass
        elif path.is_file():
            path.unlink()
    except OSError:
        pass


def media_url(path: str | Path) -> str:
    """把 outputs 下的文件路径转成 /media/... URL。"""
    p = Path(path)
    try:
        rel = p.resolve().relative_to(OUTPUTS_DIR.resolve())
    except (ValueError, OSError):
        rel = Path(p.name)
    return f"{MEDIA_URL_PREFIX}/{rel.as_posix()}"
