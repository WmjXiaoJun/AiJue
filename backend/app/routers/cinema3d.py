# -*- coding: utf-8 -*-
"""Cinema Studio（3D 电影制片工作台）后端 API。

前缀 `/api/cinema`，契约见 `docs/V7_FRONTEND_PLATFORM.md` §3，
响应字段严格对齐 `frontend/src/lib/cinema.ts` 的 TypeScript 类型。

数据源（全部真实文件，不造假）：
- 时间线   outputs/templates_v5/relic_talk_3d/work/episode3d.json（56 镜）
- 镜头素材 outputs/templates_v5/relic_talk_3d/shots/<sid>.mp4（+ <sid>.meta.json）
- 抽帧     outputs/templates_v5/relic_talk_3d/frames/<sid>_sheet.png
- briefs   contract/v5/cinema3d/relic_talk_3d_briefs.json（PATCH 原子回写）
- 复用映射 contract/v5/cinema3d/reuse_map.json（source=reuse 判定）
- 成片     outputs/templates_v5/relic_talk_3d/final.mp4
- 剪辑报告 outputs/templates_v5/relic_talk_3d/work_assemble/final_report.json

后台任务（线程内 subprocess，stdout 实时入日志）：
- 生成：`python -m pipeline.cinema3d_local --only ... --resolution 768x432`
        `python -m pipeline.cinema3d --only ...`（云端 MetaSo）
- 剪辑：`python -m pipeline.cinema3d_assemble [--ambience-db -20] [--unify-look/--no-unify-look] ...`
- 任务状态内存 + 落盘 outputs/cinema_jobs/<job_id>.json；同类型任务单并发（409）。
"""

from __future__ import annotations

import copy
import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel

from .. import config, jobs, models
from ..db import SessionLocal

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/cinema", tags=["cinema3d"])

# --------------------------------------------------------------------------- #
# 常量 / 路径
# --------------------------------------------------------------------------- #
PROJECT_ID = "proj_59fc69016c"
EPISODE_ID = "ep_5610123283"

REL_ROOT = Path("templates_v5") / "relic_talk_3d"
CINEMA_DIR = config.OUTPUTS_DIR / REL_ROOT
SHOTS_DIR = CINEMA_DIR / "shots"
SHEETS_DIR = CINEMA_DIR / "frames"
FRAMES_FINAL_DIR = CINEMA_DIR / "frames_final"
EPISODE3D_PATH = CINEMA_DIR / "work" / "episode3d.json"
FINAL_PATH = CINEMA_DIR / "final.mp4"
CONTACT_SHEET_PATH = CINEMA_DIR / "contact_sheet.png"
REPORT_PATH = CINEMA_DIR / "work_assemble" / "final_report.json"

BRIEFS_PATH = config.CONTRACT_DIR / "v5" / "cinema3d" / "relic_talk_3d_briefs.json"
REUSE_MAP_PATH = config.CONTRACT_DIR / "v5" / "cinema3d" / "reuse_map.json"

JOBS_DIR = config.OUTPUTS_DIR / "cinema_jobs"

TEMPLATE_META: Dict[str, Any] = {
    "id": "relic_talk_3d",
    "label": "国宝会说话 · 3D",
    "style": "museum_gold",
    "target_duration": 325.21,
}
TARGET_DURATION = float(TEMPLATE_META["target_duration"])

MIN_SHOT_BYTES = 100 * 1024  # 有效镜头文件最小体积（100KB）
MAX_JOB_LOGS = 200  # 每个任务保留的日志行数
MAX_JOBS_LISTED = 20  # GET /jobs 最多返回条数
RECENT_JOBS_IN_OVERVIEW = 5
ACTIVE_STATUSES = ("queued", "running")
ASSEMBLE_EST_SECONDS = 150.0  # 剪辑任务进度估算基准（真实约 2-3 分钟）
GENERATE_TIMEOUT_SECONDS = 4 * 3600  # 本地 H3 可能数小时，超时兜底
ASSEMBLE_TIMEOUT_SECONDS = 30 * 60

BRIEF_FIELDS = ("subject", "camera", "env", "ambience", "chars", "local_setting_key", "style")

PUBLIC_JOB_KEYS = (
    "id",
    "kind",
    "status",
    "progress",
    "stage",
    "message",
    "started_at",
    "finished_at",
    "shots",
    "logs",
    "result",
)

JOB_KIND_LABEL = {
    "generate_local": "本地 H3 生成",
    "generate_cloud": "云端 H3 生成",
    "assemble": "剪辑合成",
}


# --------------------------------------------------------------------------- #
# 通用工具
# --------------------------------------------------------------------------- #
def _media(rel: str) -> str:
    """outputs/templates_v5/relic_talk_3d 下的相对路径 -> /media URL。"""
    return f"{config.MEDIA_URL_PREFIX}/{REL_ROOT.as_posix()}/{rel}"


def _now_text() -> str:
    # ISO 8601（无时区）：前端 Date.parse 可稳定解析，Jobs 页格式化成本地时间
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _read_json(path: Path, label: str, default: Any = None) -> Any:
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return default
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail=f"{label}读取失败：{path.name}") from exc


def _write_json_atomic(path: Path, data: Any) -> None:
    """原子写 JSON（同目录临时文件 + os.replace）。

    briefs 合同原文件是无 BOM、CRLF 换行、无尾换行的 `json.dumps(indent=1)` 产出；
    这里保持同样的换行/缩进风格，避免一次 PATCH 把整份合同 diff 成全部行。
    """
    try:
        original = path.read_bytes()
    except OSError:
        original = b""
    text = json.dumps(data, ensure_ascii=False, indent=1)
    crlf = b"\r\n" in original and original.count(b"\r\n") == original.count(b"\n")
    if crlf:
        text = text.replace("\n", "\r\n")
    payload = text.encode("utf-8")
    if original.endswith(b"\n"):
        payload += b"\r\n" if crlf else b"\n"
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}-{threading.get_ident()}")
    try:
        tmp.write_bytes(payload)
        os.replace(tmp, path)
    except OSError as exc:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise HTTPException(status_code=500, detail="写入镜头 brief 合同失败（请检查文件权限）") from exc


# --------------------------------------------------------------------------- #
# 项目 / 时间线 / briefs / 复用映射
# --------------------------------------------------------------------------- #
def _project_info() -> Dict[str, Any]:
    """项目卡：从 DB 读项目名/集/成片地址；DB 不可用时回落常量（页面不白屏）。"""
    info: Dict[str, Any] = {
        "id": PROJECT_ID,
        "name": "国宝会说话 · 3D",
        "episode_id": EPISODE_ID,
        "render_url": _media("final.mp4"),
        "status": "ready",
    }
    try:
        with SessionLocal() as db:
            project = db.get(models.Project, PROJECT_ID)
            episode = db.get(models.Episode, EPISODE_ID)
            if project is not None:
                if project.name:
                    info["name"] = str(project.name)
                if project.status:
                    info["status"] = str(project.status)
            if episode is not None:
                info["episode_id"] = str(episode.id)
                if episode.render_url:
                    info["render_url"] = str(episode.render_url)
                if episode.status:
                    info["status"] = str(episode.status)
    except Exception as exc:  # noqa: BLE001 - 读 DB 失败不阻塞制片页
        logger.warning("读取 Cinema 项目信息失败，回落常量：%s", exc)
    return info


def _load_episode() -> Dict[str, Any]:
    data = _read_json(EPISODE3D_PATH, "3D 时间线")
    if not isinstance(data, dict) or not isinstance(data.get("shots"), list):
        raise HTTPException(
            status_code=500,
            detail="3D 时间线缺失或格式不正确：outputs/templates_v5/relic_talk_3d/work/episode3d.json",
        )
    return data


def _episode_total_duration(episode: Dict[str, Any]) -> float:
    meta = episode.get("meta") or {}
    cinema3d = meta.get("cinema3d") or {}
    raw = cinema3d.get("total_duration")
    if isinstance(raw, (int, float)) and raw > 0:
        return round(float(raw), 2)
    total = sum(float((shot or {}).get("duration") or 0.0) for shot in episode.get("shots") or [])
    return round(total, 2)


_BRIEFS_LOCK = threading.RLock()  # 保护 briefs JSON 的读-改-写（PATCH 并发安全）
# briefs 读缓存：键 = (mtime_ns, size)。PATCH 写入后显式失效 + 换成新内容，
# 文件被外部改动（如管线重写 briefs）也会因 mtime 变化自动失效 —— 永不返回旧 prompt。
_BRIEFS_CACHE: Dict[str, Any] = {"key": None, "data": None}


def _briefs_stat_key() -> Optional[Tuple[int, int]]:
    try:
        st = BRIEFS_PATH.stat()
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


def _invalidate_briefs_cache() -> None:
    """PATCH 成功后调用：清空 briefs 缓存（读接口下一次一定拿到新值）。"""
    with _BRIEFS_LOCK:
        _BRIEFS_CACHE["key"] = None
        _BRIEFS_CACHE["data"] = None


def _load_briefs() -> Dict[str, Any]:
    """读 briefs 合同（按 mtime+size 校验的进程内缓存，返回只读视图）。"""
    key = _briefs_stat_key()
    with _BRIEFS_LOCK:
        cached = _BRIEFS_CACHE.get("data")
        if cached is not None and _BRIEFS_CACHE.get("key") == key:
            return cached
    data = _read_json(BRIEFS_PATH, "镜头 brief 合同")
    if not isinstance(data, dict) or not isinstance(data.get("shots"), list):
        raise HTTPException(
            status_code=500,
            detail="镜头 brief 合同缺失或格式不正确：contract/v5/cinema3d/relic_talk_3d_briefs.json",
        )
    with _BRIEFS_LOCK:
        _BRIEFS_CACHE["key"] = key
        _BRIEFS_CACHE["data"] = data
    return data


def _brief_index(briefs: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    index: Dict[str, Dict[str, Any]] = {}
    for item in briefs.get("shots") or []:
        if isinstance(item, dict) and item.get("id") is not None:
            index[str(item["id"])] = item
    return index


def _brief_view(raw: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """只暴露前端 CinemaBrief 认识的字段（subject 必填，其余可选）。"""
    view: Dict[str, Any] = {}
    source = raw or {}
    for key in BRIEF_FIELDS:
        value = source.get(key)
        if value is None:
            continue
        if key == "chars" and not isinstance(value, list):
            value = [str(value)]
        view[key] = value
    view.setdefault("subject", "")
    return view


def _load_reuse_index() -> Dict[str, Dict[str, Any]]:
    data = _read_json(REUSE_MAP_PATH, "复用映射", default=[])
    index: Dict[str, Dict[str, Any]] = {}
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and item.get("id") is not None:
                index[str(item["id"])] = item
    return index


def _shot_meta(sid: str) -> Dict[str, Any]:
    path = SHOTS_DIR / f"{sid}.meta.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


# --------------------------------------------------------------------------- #
# 镜头有效性（ffprobe 缓存 + 并行探测）
# --------------------------------------------------------------------------- #
_VALID_CACHE: Dict[str, Tuple[Tuple[int, int], bool, float]] = {}
_VALID_LOCK = threading.Lock()


def _stat_key(path: Path) -> Optional[Tuple[int, int]]:
    try:
        st = path.stat()
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


def _cached_validity(sid: str, path: Path) -> Optional[Tuple[bool, float]]:
    key = _stat_key(path)
    if key is None:
        return (False, 0.0)
    with _VALID_LOCK:
        hit = _VALID_CACHE.get(sid)
    if hit is not None and hit[0] == key:
        return (hit[1], hit[2])
    return None


def _probe_one(sid: str, path: Path, nominal: float, key: Tuple[int, int]) -> Tuple[bool, float]:
    size = key[1]
    valid = size >= MIN_SHOT_BYTES
    duration = 0.0
    if valid:
        duration = float(jobs.probe_video_duration(path) or 0.0)
        valid = duration > 0.0 and duration >= max(0.0, nominal - 0.1)
    with _VALID_LOCK:
        _VALID_CACHE[sid] = (key, valid, duration)
    return valid, duration


def _validity_many(shots: List[Dict[str, Any]]) -> Dict[str, Tuple[bool, float]]:
    """批量判定镜头文件有效性：缓存命中直接返回，未命中 ffprobe 并行探测（首次约 0.5s）。"""
    result: Dict[str, Tuple[bool, float]] = {}
    todo: List[Tuple[str, Path, float, Tuple[int, int]]] = []
    for shot in shots:
        sid = str(shot.get("id") or "")
        if not sid:
            continue
        path = SHOTS_DIR / f"{sid}.mp4"
        cached = _cached_validity(sid, path)
        if cached is not None:
            result[sid] = cached
            continue
        key = _stat_key(path)
        if key is None:
            result[sid] = (False, 0.0)
            continue
        todo.append((sid, path, float(shot.get("duration") or 0.0), key))
    if todo:
        workers = max(1, min(8, len(todo)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for sid, value in pool.map(lambda item: (item[0], _probe_one(item[0], item[1], item[2], item[3])), todo):
                result[sid] = value
    return result


def _quick_valid(sid: str, path: Path) -> bool:
    """轻量有效性（只 stat，不 ffprobe）：/overview 保页面秒开；已探测过的走缓存。"""
    cached = _cached_validity(sid, path)
    if cached is not None:
        return cached[0]
    key = _stat_key(path)
    return bool(key is not None and key[1] >= MIN_SHOT_BYTES)


def _classify_source(sid: str, meta: Dict[str, Any], valid: bool, reuse_index: Dict[str, Dict[str, Any]]) -> str:
    """source 判定优先级（契约）：reuse_map -> meta.provider(metaso) -> meta.provider(local) -> missing -> reuse。"""
    if sid in reuse_index:
        return "reuse"
    provider = str((meta or {}).get("provider") or "").lower()
    if "metaso" in provider:
        return "cloud"
    if "local" in provider:
        return "local"
    if not valid:
        return "missing"
    return "reuse"


def _count_sources(episode_shots: List[Dict[str, Any]]) -> Dict[str, int]:
    reuse_index = _load_reuse_index()
    counts = {"total": len(episode_shots), "cloud": 0, "local": 0, "reuse": 0, "missing": 0}
    for shot in episode_shots:
        sid = str(shot.get("id") or "")
        if not sid:
            continue
        if sid in reuse_index:
            counts["reuse"] += 1
            continue
        meta = _shot_meta(sid)
        provider = str(meta.get("provider") or "").lower()
        if "metaso" in provider:
            counts["cloud"] += 1
            continue
        if "local" in provider:
            counts["local"] += 1
            continue
        valid = _quick_valid(sid, SHOTS_DIR / f"{sid}.mp4")
        counts["reuse" if valid else "missing"] += 1
    return counts


def _sheet_url(sid: str, reuse_index: Dict[str, Dict[str, Any]]) -> str:
    """抽帧缩略图：优先本镜 sheet；复用镜没有自己的 sheet 时退到母素材 sheet（更好看也准确）。"""
    own = SHEETS_DIR / f"{sid}_sheet.png"
    if own.is_file():
        return _media(f"frames/{sid}_sheet.png")
    source = str((reuse_index.get(sid) or {}).get("src") or "").strip()
    if source and (SHEETS_DIR / f"{source}_sheet.png").is_file():
        return _media(f"frames/{source}_sheet.png")
    return _media(f"frames/{sid}_sheet.png")


# --------------------------------------------------------------------------- #
# 成片信息
# --------------------------------------------------------------------------- #
_FINAL_CACHE: Dict[str, Any] = {"key": None, "value": None}
_FINAL_LOCK = threading.Lock()


def _final_info() -> Dict[str, Any]:
    url = _media("final.mp4")
    try:
        st = FINAL_PATH.stat()
    except OSError:
        return {"exists": False, "duration": 0.0, "size": 0, "mtime": None, "url": url}
    key = (st.st_mtime_ns, st.st_size)
    with _FINAL_LOCK:
        cached = _FINAL_CACHE.get("value")
        if cached is not None and _FINAL_CACHE.get("key") == key:
            return dict(cached)
    duration = float(jobs.probe_video_duration(FINAL_PATH) or 0.0)
    value = {
        "exists": True,
        "duration": round(duration, 2),
        "size": int(st.st_size),
        "mtime": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%dT%H:%M:%S"),
        "url": url,
    }
    with _FINAL_LOCK:
        _FINAL_CACHE["key"] = key
        _FINAL_CACHE["value"] = value
    return dict(value)


# --------------------------------------------------------------------------- #
# 后台任务：内存队列 + 落盘 + 子进程管理
# --------------------------------------------------------------------------- #
_JOBS: Dict[str, Dict[str, Any]] = {}
_PROCS: Dict[str, subprocess.Popen] = {}
_JOBS_LOCK = threading.RLock()
_LAST_PERSIST: Dict[str, float] = {}


def _job_file(job_id: str) -> Path:
    return JOBS_DIR / f"{job_id}.json"


def _persist_job(job: Dict[str, Any]) -> None:
    snapshot = {key: value for key, value in job.items() if not str(key).startswith("_")}
    try:
        JOBS_DIR.mkdir(parents=True, exist_ok=True)
        target = _job_file(str(job["id"]))
        tmp = target.with_name(f"{target.name}.{os.getpid()}-{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps(snapshot, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, target)
    except OSError:
        logger.warning("任务状态落盘失败：%s", job.get("id"))


def _snapshot(job: Dict[str, Any]) -> Dict[str, Any]:
    return dict(job)


def _update_job(job_id: str, **fields: Any) -> Optional[Dict[str, Any]]:
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if job is None:
            return None
        job.update(fields)
        snapshot = _snapshot(job)
    _persist_job(snapshot)
    return snapshot


def _append_log(job_id: str, line: str) -> None:
    text = str(line).rstrip("\r\n")
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if job is None:
            return
        logs: List[str] = job.setdefault("logs", [])
        logs.append(text[:2000])
        if len(logs) > MAX_JOB_LOGS:
            del logs[: len(logs) - MAX_JOB_LOGS]
        snapshot = _snapshot(job)
        snapshot["logs"] = list(logs)
    # 日志刷盘节流：0.4s 一次，避免高频输出拖慢 API；状态变更仍会立即落盘
    now = time.monotonic()
    if now - _LAST_PERSIST.get(job_id, 0.0) >= 0.4:
        _LAST_PERSIST[job_id] = now
        _persist_job(snapshot)


def _public_job(job: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {key: job.get(key) for key in PUBLIC_JOB_KEYS}
    out["logs"] = list(out.get("logs") or [])
    progress = float(out.get("progress") or 0.0)
    if job.get("status") == "running" and job.get("kind") == "assemble":
        started = job.get("_started_ts")
        if started:
            estimate = (time.time() - float(started)) / ASSEMBLE_EST_SECONDS * 100.0
            progress = max(progress, min(99.0, estimate))
    out["progress"] = round(max(0.0, min(100.0, progress)), 1)
    return out


def _sorted_public_jobs(limit: int = MAX_JOBS_LISTED) -> List[Dict[str, Any]]:
    with _JOBS_LOCK:
        snapshot = [dict(job) for job in _JOBS.values()]
    snapshot.sort(key=lambda job: float(job.get("_created_ts") or 0.0), reverse=True)
    return [_public_job(job) for job in snapshot[:limit]]


def _active_job_count() -> int:
    with _JOBS_LOCK:
        return sum(1 for job in _JOBS.values() if job.get("status") in ACTIVE_STATUSES)


def _reserve_job(
    conflict_kinds: Tuple[str, ...],
    kind: str,
    shots: List[str],
    **fields: Any,
) -> Dict[str, Any]:
    """单并发预留：同类型已有 queued/running 任务时返回 409（检查 + 插入在同一把锁内）。"""
    job_id = "cjob_" + uuid.uuid4().hex[:10]
    with _JOBS_LOCK:
        for existing in _JOBS.values():
            if existing.get("kind") in conflict_kinds and existing.get("status") in ACTIVE_STATUSES:
                label = JOB_KIND_LABEL.get(str(existing.get("kind")), "生成")
                raise HTTPException(
                    status_code=409,
                    detail=f"已有{label}任务在运行（{existing.get('id')}），请等待完成或先取消该任务",
                )
        job: Dict[str, Any] = {
            "id": job_id,
            "kind": kind,
            "status": "queued",
            "progress": 0.0,
            "message": str(fields.pop("message", "排队中")),
            "started_at": None,
            "finished_at": None,
            "shots": list(shots),
            "logs": [],
            "result": None,
            "_created_ts": time.time(),
            "_started_ts": None,
        }
        job.update(fields)
        _JOBS[job_id] = job
        snapshot = _snapshot(job)
    _persist_job(snapshot)
    return snapshot


def _start_process(cmd: List[str]) -> subprocess.Popen:
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUNBUFFERED", "1")
    try:
        return subprocess.Popen(  # noqa: S603 - 固定 python -m pipeline 命令
            cmd,
            cwd=str(config.REPO_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
        )
    except OSError as exc:
        raise RuntimeError(f"无法启动 Python 子进程：{exc}") from exc


def _kill_tree(proc: Optional[subprocess.Popen]) -> None:
    if proc is None or proc.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
                timeout=15,
                check=False,
            )
        else:
            proc.kill()
    except Exception:  # noqa: BLE001 - 终止失败不抛给调用方
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass


def _last_json(lines: List[str]) -> Dict[str, Any]:
    for line in reversed(lines or []):
        text = (line or "").strip()
        if text.startswith("{") and text.endswith("}"):
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict):
                return data
    return {}


_PROGRESS_RE = re.compile(r"^\s*\[(\d+)/(\d+)\]\s*(.*)$")
_ENCODE_RE = re.compile(r"编码\s+(\d+(?:\.\d+)?)\s*%")


def _handle_log_line(job_id: str, raw: str, mode: str) -> None:
    line = str(raw).rstrip("\r\n")
    if not line.strip():
        return
    _append_log(job_id, line)
    with _JOBS_LOCK:
        current = _JOBS.get(job_id)
        if current is None or current.get("status") != "running":
            return  # 已取消/已结束：迟到日志只留档，不再覆盖进度与消息
    match = _PROGRESS_RE.match(line)
    if match:
        done_count = int(match.group(1))
        total = int(match.group(2))
        pct = (done_count / total * 100.0) if total > 0 else 0.0
        detail = match.group(3).strip()
        _update_job(
            job_id,
            progress=round(min(99.0, pct), 1),
            message=(detail or f"已完成 {done_count}/{total} 镜")[:200],
        )
        return
    encode = _ENCODE_RE.search(line)
    if encode:
        _update_job(
            job_id,
            progress=round(min(99.0, float(encode.group(1))), 1),
            message=line[:200],
        )
        return
    if mode == "assemble" and line.startswith("[cinema3d]"):
        _update_job(job_id, message=line[:200])


def _done_message(mode: str, result: Dict[str, Any]) -> str:
    if mode == "assemble":
        out = str(result.get("out") or result.get("report") or "")
        name = Path(out).name if out else ""
        return f"剪辑合成完成{('：' + name) if name else ''}"
    ok_count = result.get("ok")
    failed = result.get("failed") or []
    if isinstance(ok_count, (int, float)):
        message = f"镜头生成完成：成功 {int(ok_count)} 镜"
        if isinstance(failed, list) and failed:
            message += f"，失败 {len(failed)} 镜（{', '.join(str(x) for x in failed[:5])}）"
        return message
    return "任务完成"


def _failure_message(returncode: int, logs: List[str], mode: str) -> str:
    tail_lines = [line for line in (logs or []) if line.strip()][-4:]
    tail = " | ".join(tail_lines)[-600:]
    lowered = tail.lower()
    if "402" in tail or "余额" in tail or "insufficient" in lowered or "balance" in lowered:
        return "云端 MetaSo 余额不足（402），请改用本地 H3 生成，或充值后重试"
    label = "剪辑合成" if mode == "assemble" else "镜头生成"
    return f"{label}失败（exit code {returncode}）：{tail or '无输出'}"


def _finish_failed(job_id: str, message: str) -> None:
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if job is None:
            return
        if job.get("status") == "cancelled":
            return
        job.update(
            status="failed",
            finished_at=_now_text(),
            message=message[:300],
            result={**(job.get("result") or {}), "error": message[:2000]},
        )
        snapshot = _snapshot(job)
    _persist_job(snapshot)


def _finish_done(job_id: str, mode: str, result: Dict[str, Any]) -> None:
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if job is None or job.get("status") == "cancelled":
            return
        job.update(
            status="done",
            progress=100.0,
            finished_at=_now_text(),
            message=_done_message(mode, result),
            result=result,
        )
        snapshot = _snapshot(job)
    _persist_job(snapshot)


def _timeout_watchdog(job_id: str, proc: subprocess.Popen, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return
        time.sleep(1.0)
    if proc.poll() is not None:
        return
    _update_job(job_id, _timed_out=True, message=f"任务超时（>{int(timeout)}s），正在终止")
    _append_log(job_id, f"[timeout] 任务超过 {int(timeout)}s，已终止子进程")
    _kill_tree(proc)


def _run_job(job_id: str) -> None:
    """任务线程：起子进程 -> 逐行入日志/解析进度 -> 终态回写。"""
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if job is None:
            return
        cmd = list(job.get("_cmd") or [])
        mode = str(job.get("_mode") or "generate")
        timeout = job.get("_timeout")
    if not cmd:
        _finish_failed(job_id, "内部错误：任务缺少可执行命令")
        return

    proc: Optional[subprocess.Popen] = None
    try:
        # 原子地 queued -> running；若用户在任务启动前已取消，直接收工不启动子进程
        with _JOBS_LOCK:
            current = _JOBS.get(job_id)
            if current is None:
                return
            if current.get("status") == "cancelled":
                return
            current.update(
                status="running",
                started_at=_now_text(),
                _started_ts=time.time(),
                progress=1.0,
                message="任务启动",
            )
            start_snapshot = _snapshot(current)
        _persist_job(start_snapshot)
        _append_log(job_id, "$ " + " ".join(cmd))
        proc = _start_process(cmd)
        with _JOBS_LOCK:
            _PROCS[job_id] = proc
            cancelled_before_register = (_JOBS.get(job_id) or {}).get("status") == "cancelled"
        if cancelled_before_register:
            _kill_tree(proc)
            return
        if timeout:
            threading.Thread(
                target=_timeout_watchdog,
                args=(job_id, proc, float(timeout)),
                daemon=True,
                name=f"cinema-watchdog-{job_id}",
            ).start()
        if proc.stdout is not None:
            for raw in proc.stdout:
                _handle_log_line(job_id, raw, mode)
        proc.wait()
        returncode = proc.returncode
    except Exception as exc:  # noqa: BLE001 - 任务线程必须兜底
        logger.exception("cinema 任务执行异常：%s", job_id)
        _finish_failed(job_id, f"任务执行异常：{exc}")
        return
    finally:
        with _JOBS_LOCK:
            _PROCS.pop(job_id, None)
        if proc is not None and proc.poll() is None:
            _kill_tree(proc)

    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if job is None:
            return
        status = job.get("status")
        logs = list(job.get("logs") or [])
        timed_out = bool(job.get("_timed_out"))
    if status == "cancelled":
        return
    if timed_out:
        _finish_failed(job_id, f"任务超时（>{int(float(timeout or 0))}s），已终止")
        return
    result = _last_json(logs)
    if returncode == 0:
        _finish_done(job_id, mode, result)
    else:
        _finish_failed(job_id, _failure_message(int(returncode or -1), logs, mode))


def _spawn_job(job_id: str) -> None:
    thread = threading.Thread(
        target=_run_job,
        args=(job_id,),
        daemon=True,
        name=f"cinema-job-{job_id}",
    )
    thread.start()


def _load_jobs_from_disk() -> None:
    """启动时恢复历史任务（供 GET /jobs 排查）；未完成的标记 failed（进程已退出）。"""
    try:
        JOBS_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        return
    for path in sorted(JOBS_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict) or not data.get("id"):
            continue
        data.setdefault("logs", [])
        data.setdefault("result", None)
        data.setdefault("shots", [])
        try:
            created = path.stat().st_mtime
        except OSError:
            created = 0.0
        data["_created_ts"] = created
        if data.get("status") in ACTIVE_STATUSES:
            data["status"] = "failed"
            data["message"] = "服务重启，任务中断"
            data["finished_at"] = _now_text()
            _persist_job({key: value for key, value in data.items() if not str(key).startswith("_")})
        with _JOBS_LOCK:
            _JOBS[str(data["id"])] = data


_load_jobs_from_disk()


# --------------------------------------------------------------------------- #
# 请求体模型
# --------------------------------------------------------------------------- #
class CinemaShotPatch(BaseModel):
    """PATCH /shots/{sid}：只允许改 brief 的可编辑字段。"""

    subject: Optional[str] = None
    camera: Optional[str] = None
    ambience: Optional[str] = None
    env: Optional[str] = None
    chars: Optional[List[str]] = None
    # 容错：前端抽屉若一并带上这两个字段也接受
    style: Optional[str] = None
    local_setting_key: Optional[str] = None


class CinemaGenerateRequest(BaseModel):
    provider: str
    shots: List[str] = []
    force: bool = False
    resolution: Optional[str] = None


class CinemaAssembleRequest(BaseModel):
    ambience_db: Optional[float] = None
    unify_look: Optional[bool] = None
    shot_grade: Optional[bool] = None


# --------------------------------------------------------------------------- #
# GET /overview
# --------------------------------------------------------------------------- #
@router.get("/overview")
def get_overview(response: Response) -> Dict[str, Any]:
    response.headers["Cache-Control"] = "no-store, max-age=0"
    episode = _load_episode()
    episode_shots = [shot for shot in episode.get("shots") or [] if isinstance(shot, dict)]
    return {
        "project": _project_info(),
        "template": dict(TEMPLATE_META),
        "shots": _count_sources(episode_shots),
        "final": _final_info(),
        "jobs": {
            "active": _active_job_count(),
            "recent": _sorted_public_jobs(RECENT_JOBS_IN_OVERVIEW),
        },
    }


# --------------------------------------------------------------------------- #
# GET /shots
# --------------------------------------------------------------------------- #
def _kind_of(shot: Dict[str, Any]) -> str:
    raw = str(shot.get("type") or "").strip().lower()
    if raw == "card":
        return "card"
    if raw == "broll":
        return "broll"
    return "dialogue"


@router.get("/shots")
def get_shots(response: Response) -> Dict[str, Any]:
    # 禁止浏览器/代理缓存：PATCH brief 后刷新页面必须看到最新 prompt
    response.headers["Cache-Control"] = "no-store, max-age=0"
    episode = _load_episode()
    briefs_index = _brief_index(_load_briefs())
    reuse_index = _load_reuse_index()
    episode_shots = [shot for shot in episode.get("shots") or [] if isinstance(shot, dict)]
    validity = _validity_many(episode_shots)

    shots: List[Dict[str, Any]] = []
    for position, shot in enumerate(episode_shots):
        sid = str(shot.get("id") or "")
        if not sid:
            continue
        meta = _shot_meta(sid)
        valid, _probed = validity.get(sid, (False, 0.0))
        scene = shot.get("scene")
        edit_start = shot.get("_edit_start")
        reuse_entry = reuse_index.get(sid) or {}
        reuse_src = str(reuse_entry.get("src")) if reuse_entry.get("src") else None
        shots.append(
            {
                "id": sid,
                "index": int(shot.get("no") or (position + 1)),
                "duration": round(float(shot.get("duration") or 0.0), 3),
                "line": str(shot.get("line") or ""),
                "kind": _kind_of(shot),
                "source": _classify_source(sid, meta, valid, reuse_index),
                "valid": bool(valid),
                "url": _media(f"shots/{sid}.mp4"),
                "sheet_url": _sheet_url(sid, reuse_index),
                "scene": str(scene) if scene else None,
                "edit_start": round(float(edit_start or 0.0), 3),
                "meta": meta,
                # 复用镜的母素材 id（来自 reuse_map.json，如 v5_054 -> v5_013）；非复用镜为 null
                "reuse_src": reuse_src,
                "brief": _brief_view(briefs_index.get(sid)),
            }
        )
    return {
        "episode": {
            "total_duration": _episode_total_duration(episode),
            "render_url": _media("final.mp4"),
        },
        "shots": shots,
    }


# --------------------------------------------------------------------------- #
# PATCH /shots/{sid}
# --------------------------------------------------------------------------- #
@router.patch("/shots/{sid}")
def patch_shot(sid: str, payload: CinemaShotPatch) -> Dict[str, Any]:
    patch = {
        key: value
        for key, value in payload.model_dump(exclude_unset=True).items()
        if value is not None
    }
    if not patch:
        raise HTTPException(status_code=400, detail="没有可更新的字段（subject/camera/ambience/env/chars）")
    if "chars" in patch:
        patch["chars"] = [str(item) for item in patch["chars"]]

    with _BRIEFS_LOCK:  # 同一时刻只允许一个 PATCH 写 briefs，避免并发覆盖
        # 深拷贝缓存内容再改，避免污染其它读请求正在使用的缓存对象
        briefs = copy.deepcopy(_load_briefs())
        target = None
        for item in briefs.get("shots") or []:
            if isinstance(item, dict) and str(item.get("id")) == sid:
                target = item
                break
        if target is None:
            raise HTTPException(status_code=404, detail=f"镜头 brief 不存在：{sid}")
        target.update(patch)
        try:
            _write_json_atomic(BRIEFS_PATH, briefs)
        finally:
            # 无论写入是否抛错都先失效：下一次 GET /shots 必定重新读盘，不会看到旧 prompt
            _invalidate_briefs_cache()
        # 把刚写入的新内容直接放回缓存（键 = 新文件 mtime+size），读接口立刻可见
        _BRIEFS_CACHE["key"] = _briefs_stat_key()
        _BRIEFS_CACHE["data"] = briefs
        view = _brief_view(target)
    logger.info("PATCH /api/cinema/shots/%s 已写回 briefs：%s", sid, ", ".join(sorted(patch)))
    return {"brief": view}


# --------------------------------------------------------------------------- #
# POST /generate
# --------------------------------------------------------------------------- #
@router.post("/generate")
def start_generate(payload: CinemaGenerateRequest) -> Dict[str, str]:
    provider = str(payload.provider or "").strip().lower()
    if provider not in ("local", "metaso"):
        raise HTTPException(status_code=400, detail="provider 只能是 local（本地 H3）或 metaso（云端 H3）")

    requested = [str(item).strip() for item in payload.shots or [] if str(item).strip()]
    shot_ids = list(dict.fromkeys(requested))
    if not shot_ids:
        raise HTTPException(status_code=400, detail="至少选择一个镜头（shots 不能为空）")

    episode = _load_episode()
    known = {str(shot.get("id")) for shot in episode.get("shots") or [] if isinstance(shot, dict)}
    unknown = [sid for sid in shot_ids if sid not in known]
    if unknown:
        raise HTTPException(status_code=400, detail=f"镜头不存在：{', '.join(unknown[:8])}")

    resolution = str(payload.resolution or "768x432").strip().lower()
    if not re.fullmatch(r"\d{2,5}x\d{2,5}", resolution):
        raise HTTPException(status_code=400, detail="resolution 格式应为 宽x高（如 768x432）")

    if provider == "local":
        kind = "generate_local"
        cmd = [
            sys.executable,
            "-m",
            "pipeline.cinema3d_local",
            "--only",
            ",".join(shot_ids),
            "--resolution",
            resolution,
        ]
        label = "本地 H3"
    else:
        kind = "generate_cloud"
        cmd = [sys.executable, "-m", "pipeline.cinema3d", "--only", ",".join(shot_ids)]
        label = "云端 MetaSo"
    if payload.force:
        cmd.append("--force")

    job = _reserve_job(
        ("generate_local", "generate_cloud"),
        kind,
        shot_ids,
        _cmd=cmd,
        _mode="generate",
        _timeout=GENERATE_TIMEOUT_SECONDS,
        message=f"{label}生成排队中（{len(shot_ids)} 镜）",
        provider=provider,
        resolution=resolution if provider == "local" else None,
    )
    _spawn_job(job["id"])
    return {"job_id": job["id"]}


# --------------------------------------------------------------------------- #
# POST /assemble
# --------------------------------------------------------------------------- #
@router.post("/assemble")
def start_assemble(payload: CinemaAssembleRequest) -> Dict[str, str]:
    cmd = [sys.executable, "-m", "pipeline.cinema3d_assemble"]
    if payload.ambience_db is not None:
        try:
            ambience_db = float(payload.ambience_db)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="ambience_db 必须是数字（dB，建议 -20）") from exc
        if not (-60.0 <= ambience_db <= 0.0):
            raise HTTPException(status_code=400, detail="ambience_db 应在 -60 ~ 0 之间（dB，默认 -20）")
        cmd += ["--ambience-db", f"{ambience_db:g}"]
    if payload.unify_look is not None:
        cmd.append("--unify-look" if payload.unify_look else "--no-unify-look")
    if payload.shot_grade is not None:
        cmd.append("--shot-grade" if payload.shot_grade else "--no-shot-grade")

    job = _reserve_job(
        ("assemble",),
        "assemble",
        [],
        _cmd=cmd,
        _mode="assemble",
        _timeout=ASSEMBLE_TIMEOUT_SECONDS,
        message="剪辑合成排队中",
        params={
            "ambience_db": payload.ambience_db,
            "unify_look": payload.unify_look,
            "shot_grade": payload.shot_grade,
        },
    )
    _spawn_job(job["id"])
    return {"job_id": job["id"]}


# --------------------------------------------------------------------------- #
# GET /jobs  /jobs/{id}  POST /jobs/{id}/cancel
# --------------------------------------------------------------------------- #
@router.get("/jobs")
def list_jobs(response: Response) -> Dict[str, Any]:
    response.headers["Cache-Control"] = "no-store, max-age=0"
    return {"jobs": _sorted_public_jobs(MAX_JOBS_LISTED)}


def _find_job(job_id: str) -> Dict[str, Any]:
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"任务不存在：{job_id}")
        return dict(job)


@router.get("/jobs/{job_id}")
def get_job(job_id: str, response: Response) -> Dict[str, Any]:
    response.headers["Cache-Control"] = "no-store, max-age=0"
    return _public_job(_find_job(job_id))


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> Dict[str, Any]:
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"任务不存在：{job_id}")
        if job.get("status") not in ACTIVE_STATUSES:
            return _public_job(job)  # 已结束：幂等返回当前状态
        job.update(
            status="cancelled",
            finished_at=_now_text(),
            message="任务已取消",
            result={**(job.get("result") or {}), "cancelled": True},
        )
        snapshot = _snapshot(job)
        proc = _PROCS.get(job_id)
    _persist_job(snapshot)
    if proc is not None:
        _kill_tree(proc)
    logger.info("cinema 任务已取消：%s", job_id)
    return _public_job(snapshot)


# --------------------------------------------------------------------------- #
# GET /report
# --------------------------------------------------------------------------- #
def _build_qc(report: Dict[str, Any]) -> Dict[str, Any]:
    media = report.get("media") or {}
    loudness = report.get("loudness") or {}
    blackdetect = report.get("blackdetect") or {}
    subtitles = report.get("subtitles") or {}
    args = report.get("args") or {}

    duration: Optional[float] = None
    raw_duration = media.get("duration")
    if isinstance(raw_duration, (int, float)):
        duration = round(float(raw_duration), 2)
    expected = args.get("episode_total_duration")
    if not isinstance(expected, (int, float)):
        expected = TARGET_DURATION
    duration_error = None
    if duration is not None:
        duration_error = round(abs(duration - float(expected)), 2)

    def _number(raw: Any) -> Optional[float]:
        return round(float(raw), 2) if isinstance(raw, (int, float)) else None

    black_count = blackdetect.get("count")
    total_events = subtitles.get("total_events")
    return {
        "duration": duration,
        "duration_error": duration_error,
        "width": media.get("width"),
        "height": media.get("height"),
        "fps": media.get("fps"),
        "blackdetect": int(black_count) if isinstance(black_count, (int, float)) else None,
        "loudness": {
            "integrated_lufs": _number(loudness.get("integrated_lufs")),
            "true_peak_dbtp": _number(loudness.get("true_peak_dbtp")),
            "lra": _number(loudness.get("lra")),
        },
        # CinemaQc.subtitles = 字幕事件数（Final 页按「N 事件」展示；覆盖率从 report.subtitles 原样读）
        "subtitles": int(total_events) if isinstance(total_events, (int, float)) else None,
    }


@router.get("/report")
def get_report(response: Response) -> Dict[str, Any]:
    response.headers["Cache-Control"] = "no-store, max-age=0"
    report = _read_json(REPORT_PATH, "剪辑报告", default=None)
    report_data = report if isinstance(report, dict) else None
    frames: List[str] = []
    if FRAMES_FINAL_DIR.is_dir():
        for path in sorted(FRAMES_FINAL_DIR.glob("*.png"))[:8]:
            frames.append(_media(f"frames_final/{path.name}"))
    return {
        "exists": report_data is not None,
        "final": _final_info(),
        "report": report_data,
        "qc": _build_qc(report_data) if report_data is not None else None,
        "frames": frames,
        "contact_sheet_url": _media("contact_sheet.png"),
    }


# --------------------------------------------------------------------------- #
# GET /workers
# --------------------------------------------------------------------------- #
COMFYUI_QUEUE_URL = "http://127.0.0.1:8188/queue"


@router.get("/workers")
def get_workers(response: Response) -> Dict[str, Any]:
    response.headers["Cache-Control"] = "no-store, max-age=0"
    comfyui = False
    comfyui_queue = 0
    try:
        with urllib.request.urlopen(COMFYUI_QUEUE_URL, timeout=2.0) as response:
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
        if isinstance(payload, dict):
            comfyui = True
            comfyui_queue = len(payload.get("queue_running") or []) + len(
                payload.get("queue_pending") or []
            )
    except (urllib.error.URLError, OSError, ValueError):
        comfyui = False
        comfyui_queue = 0
    return {
        "comfyui": comfyui,
        "comfyui_queue": comfyui_queue,
        # 只暴露「是否配置」，绝不返回 key 本身
        "metaso_key": bool(config.metaso_key_configured()),
    }
