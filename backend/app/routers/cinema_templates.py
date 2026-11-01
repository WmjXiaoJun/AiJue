# -*- coding: utf-8 -*-
"""V8「16 个 V5 模板 3D 化」后端 API（前缀 `/api/cinema/templates`）。

看板 / 批量操作 / 成片播放需要的全部真实文件都在这里读，不造假：
- 进度     outputs/templates_3d/progress.json（pipeline/templates3d.py 写）
- 逐镜片   outputs/templates_3d/<id>/shots/<sid>.mp4（+ <sid>.meta.json）
- 成片     outputs/templates_3d/<id>/final.mp4（ffprobe 读真实时长/大小，缓存 5s）
- 报告     outputs/templates_3d/<id>/report.json
- 海报     outputs/templates_3d/<id>/poster.jpg（不存在时从 final.mp4 抽首帧生成并缓存；
           成片还没出来时回落到旧 2D 产物 outputs/templates_v5/<id>/frames/frame_01.png 等）
- 逐镜 brief contract/v5/cinema3d/templates3d/<id>.json
- 模板元数据 contract/v5/templates/<id>.json（label 中文 / style）

端点：
  GET  /api/cinema/templates                 16 个模板总览（含 summary / jobs）
  GET  /api/cinema/templates/status          轻量轮询版（不 ffprobe、不生成海报，3s 轮询用）
  GET  /api/cinema/templates/{tid}           单模板详情（+ shots 逐镜列表，抽屉用）
  POST /api/cinema/templates/generate        body {templates: [id...]|"all", force?, dry_run?} -> {job_id}
  POST /api/cinema/templates/assemble        body {templates: [id...]|"all", force?, dry_run?} -> {job_id}

任务复用 `cinema3d.py` 的线程任务队列（同一份 `_JOBS` / `_PROCS`），因此：
  GET  /api/cinema/jobs              能看到这里提交的任务
  GET  /api/cinema/jobs/{job_id}     查询进度 / 日志
  POST /api/cinema/jobs/{job_id}/cancel  取消（会 taskkill 子进程树）
无需重复实现任务查询。

安全护栏（GPU 正被后台 `--generate` / `--assemble-watch` 占用时）：
- 检测到**外部** `python -m pipeline.templates3d --generate` 进程 -> POST generate 返回 409，
  避免两个进程同时写 progress.json / 抢 ComfyUI 队列；
- 检测到外部 `--assemble` 进程 -> POST assemble 返回 409；检测到 `--assemble-watch` 守护时，
  非 force 合并不拦截（只处理尚未剪辑的模板）并在响应里带 warning，force 合并不允许（避免同一成片互相覆盖）。
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel

from .. import config, jobs
from .cinema3d import (
    ACTIVE_STATUSES,
    JOB_KIND_LABEL,
    _JOBS,
    _JOBS_LOCK,
    _PROCS,
    _append_log,
    _failure_message,
    _finish_failed,
    _kill_tree,
    _now_text,
    _persist_job,
    _reserve_job,
    _start_process,
    _timeout_watchdog,
    _update_job,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/cinema/templates", tags=["cinema-templates"])

# --------------------------------------------------------------------------- #
# 常量
# --------------------------------------------------------------------------- #
#: pipeline/templates3d.py 的 16 个模板（顺序即看板展示顺序）
TEMPLATES: List[str] = [
    "tonight_show",
    "classics",
    "history_talk",
    "masked_singer",
    "roundtable",
    "national_treasure",
    "hot_ones",
    "mystery",
    "qipashuo",
    "shark_tank",
    "slow_life",
    "snl_sketch",
    "standup",
    "taskmaster",
    "traitors",
    "history_mystery",
]
TEMPLATE_SET = frozenset(TEMPLATES)

#: 模板级状态（progress.json 的 status 字段）
STATUS_PENDING = "pending"
STATUS_GENERATING = "generating"
STATUS_GENERATED = "generated"
STATUS_DONE = "done"
STATUS_ASSEMBLE_FAILED = "assemble_failed"
ALLOWED_STATUSES = {
    STATUS_PENDING,
    STATUS_GENERATING,
    STATUS_GENERATED,
    STATUS_DONE,
    STATUS_ASSEMBLE_FAILED,
}
#: 逐镜 clip 在 progress.json 里算「已生成」的状态
CLIP_OK_STATUSES = ("ok", "cached", "card-skip")

T3D_ROOT = config.OUTPUTS_DIR / "templates_3d"
PROGRESS_PATH = T3D_ROOT / "progress.json"
V5_ROOT = config.OUTPUTS_DIR / "templates_v5"
BRIEFS_DIR = config.CONTRACT_DIR / "v5" / "cinema3d" / "templates3d"
V5_TEMPLATE_CONTRACTS = config.CONTRACT_DIR / "v5" / "templates"

POSTER_FILENAME = "poster.jpg"
MIN_SHOT_BYTES = 100 * 1024  # 有效镜头文件最小体积（与 pipeline/templates3d.py 一致）
FINAL_CACHE_TTL = 5.0  # 成片 ffprobe 缓存秒数
DEFAULT_RESOLUTION = "768x432"

#: 任务 kind（与 cinema3d.py 共用 _JOBS；这里补上中文标签）
KIND_TEMPLATES_GENERATE = "templates_generate"
KIND_TEMPLATES_ASSEMBLE = "templates_assemble"
JOB_KIND_LABEL.setdefault(KIND_TEMPLATES_GENERATE, "16 模板 3D 生成")
JOB_KIND_LABEL.setdefault(KIND_TEMPLATES_ASSEMBLE, "16 模板 3D 合成")

#: 生成任务互斥范围：本模板生成 + 原 relic 3D 的本地/云端生成（同一块 GPU）
GENERATE_CONFLICT_KINDS = ("generate_local", "generate_cloud", KIND_TEMPLATES_GENERATE)
#: 合成任务互斥范围：本模板合成 + 原 relic 3D 合成
ASSEMBLE_CONFLICT_KINDS = ("assemble", KIND_TEMPLATES_ASSEMBLE)

GENERATE_TIMEOUT_SECONDS = 12 * 3600  # 120 镜 × 最长 40 分钟，兜底 12h
ASSEMBLE_TIMEOUT_SECONDS = 3 * 3600

#: 旧 2D 产物里可复用的海报/剧照（按优先级）
OLD_POSTER_CANDIDATES = (
    "poster.png",
    "poster.jpg",
    "cover.png",
    "cover.jpg",
    "frames/frame_01.png",
    "storyboard.png",
)


def _media_url(rel_path: str) -> str:
    text = str(rel_path).replace(os.sep, "/").lstrip("/")
    return f"{config.MEDIA_URL_PREFIX}/{text}"


def _t3d_media(tid: str, rel: str) -> str:
    return _media_url(f"templates_3d/{tid}/{rel}")


# --------------------------------------------------------------------------- #
# 容错 JSON 读缓存（progress.json / briefs 正被 pipeline 非原子写入）
# --------------------------------------------------------------------------- #
class _JsonFileCache:
    """按 (mtime_ns, size) 缓存的 JSON 读取；文件被改写/替换自动失效。

    pipeline/templates3d.py 用 `Path.write_text` 直接覆盖 progress.json，
    读这边可能读到半截 JSON：这里重试 3 次，失败返回 None（调用方回落旧值/空值）。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._keys: Dict[str, Tuple[int, int]] = {}
        self._values: Dict[str, Any] = {}

    def get(self, path: Path, attempts: int = 3) -> Any:
        try:
            stat = path.stat()
        except OSError:
            return None
        key = (stat.st_mtime_ns, stat.st_size)
        name = str(path)
        with self._lock:
            if self._keys.get(name) == key:
                return self._values.get(name)
        value: Any = None
        for attempt in range(max(1, attempts)):
            try:
                text = path.read_text(encoding="utf-8-sig")
            except OSError:
                return None
            if not text.strip():
                value = None
                break
            try:
                value = json.loads(text)
                break
            except json.JSONDecodeError:
                if attempt + 1 < attempts:
                    time.sleep(0.04)
                else:
                    value = None
        with self._lock:
            self._keys[name] = key
            self._values[name] = value
        return value


_JSON_CACHE = _JsonFileCache()


def _load_progress() -> Dict[str, Any]:
    data = _JSON_CACHE.get(PROGRESS_PATH, attempts=4)
    return data if isinstance(data, dict) else {}


def _template_contract(tid: str) -> Dict[str, Any]:
    data = _JSON_CACHE.get(V5_TEMPLATE_CONTRACTS / f"{tid}.json")
    return data if isinstance(data, dict) else {}


def _template_label(tid: str) -> str:
    label = str(_template_contract(tid).get("label") or "").strip()
    return label or tid


def _template_style(tid: str) -> Optional[str]:
    style = _template_contract(tid).get("style")
    return str(style) if style else None


def _load_briefs(tid: str) -> Dict[str, Any]:
    data = _JSON_CACHE.get(BRIEFS_DIR / f"{tid}.json")
    return data if isinstance(data, dict) else {}


def _expected_clip_total(tid: str) -> int:
    """期望生成镜头数（不含 card）：优先 briefs，回落旧 v5 episode.json。"""
    briefs = _load_briefs(tid)
    shots = briefs.get("shots")
    if isinstance(shots, list):
        total = sum(1 for item in shots if isinstance(item, dict) and item.get("prompt"))
        if total:
            return total
    episode = _JSON_CACHE.get(V5_ROOT / tid / "work" / "episode.json")
    if isinstance(episode, dict) and isinstance(episode.get("shots"), list):
        return sum(
            1
            for item in episode["shots"]
            if isinstance(item, dict) and str(item.get("type") or "").lower() != "card"
        )
    return 0


# --------------------------------------------------------------------------- #
# 模板状态（progress.json -> 规范状态 / 百分比 / clips）
# --------------------------------------------------------------------------- #
def _normalize_status(raw: Any) -> str:
    text = str(raw or "").strip().lower()
    return text if text in ALLOWED_STATUSES else STATUS_PENDING


def _clip_counts(tid: str, state: Dict[str, Any]) -> Tuple[int, int]:
    clips = state.get("clips") if isinstance(state.get("clips"), dict) else {}
    raw_done = state.get("done")
    done = int(raw_done) if isinstance(raw_done, (int, float)) else sum(
        1
        for item in clips.values()
        if isinstance(item, dict) and str(item.get("status") or "") in CLIP_OK_STATUSES
    )
    raw_total = state.get("total")
    total = int(raw_total) if isinstance(raw_total, (int, float)) else 0
    if total <= 0:
        total = _expected_clip_total(tid)
    done = max(0, done)
    total = max(done, total)
    return done, total


def _progress_percent(status: str, done: int, total: int) -> float:
    if status == STATUS_DONE:
        return 100.0
    if total <= 0:
        return 100.0 if status in (STATUS_GENERATED, STATUS_ASSEMBLE_FAILED) else 0.0
    pct = done * 100.0 / total
    return round(max(0.0, min(100.0, pct)), 1)


def _template_state(tid: str, progress: Dict[str, Any]) -> Dict[str, Any]:
    raw = progress.get(tid) if isinstance(progress, dict) else None
    state = raw if isinstance(raw, dict) else {}
    status = _normalize_status(state.get("status"))
    done, total = _clip_counts(tid, state)
    # 兜底：progress 里没登记但逐镜片已经在盘上（例如写盘失败/历史产物）
    if status == STATUS_PENDING and done <= 0:
        shots_dir = T3D_ROOT / tid / "shots"
        if shots_dir.is_dir():
            existing = sum(
                1
                for path in shots_dir.glob("*.mp4")
                if path.stat().st_size >= MIN_SHOT_BYTES
            )
            if existing > 0:
                done = min(existing, total or existing)
                if total <= 0:
                    total = existing
                status = STATUS_GENERATING
    if status == STATUS_PENDING and done > 0:
        status = STATUS_GENERATING
    updated = state.get("updated")
    error = state.get("error")
    return {
        "status": status,
        "progress": _progress_percent(status, done, total),
        "clips": {"done": done, "total": total},
        "updated": str(updated) if updated else None,
        "error": str(error)[:800] if error else None,
    }


def _summary(states: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    counts = Counter(item["status"] for item in states)
    total = len(states)
    done = counts.get(STATUS_DONE, 0)
    generating = counts.get(STATUS_GENERATING, 0)
    generated = counts.get(STATUS_GENERATED, 0)
    assemble_failed = counts.get(STATUS_ASSEMBLE_FAILED, 0)
    pending = total - done - generating - generated - assemble_failed
    return {
        "total": total,
        "done": done,
        "generating": generating,
        "generated": generated,
        "assemble_failed": assemble_failed,
        "pending": max(0, pending),
        "clips_done": sum(int(item["clips"]["done"]) for item in states),
        "clips_total": sum(int(item["clips"]["total"]) for item in states),
    }


# --------------------------------------------------------------------------- #
# 成片信息（ffprobe 缓存 5s，列表接口并行探测）
# --------------------------------------------------------------------------- #
_FINAL_CACHE: Dict[str, Tuple[Tuple[int, int], float, Dict[str, Any]]] = {}
_FINAL_LOCK = threading.Lock()


def _final_path(tid: str) -> Path:
    return T3D_ROOT / tid / "final.mp4"


def _final_info(tid: str) -> Dict[str, Any]:
    path = _final_path(tid)
    url = _t3d_media(tid, "final.mp4")
    try:
        stat = path.stat()
    except OSError:
        return {"exists": False, "duration": 0.0, "size": 0, "mtime": None, "url": url}
    key = (stat.st_mtime_ns, stat.st_size)
    now = time.monotonic()
    with _FINAL_LOCK:
        hit = _FINAL_CACHE.get(tid)
        if hit is not None and hit[0] == key and (now - hit[1]) < FINAL_CACHE_TTL:
            return dict(hit[2])
    duration = float(jobs.probe_video_duration(path) or 0.0)
    value = {
        "exists": bool(stat.st_size > 0),
        "duration": round(duration, 2),
        "size": int(stat.st_size),
        "mtime": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(stat.st_mtime)),
        "url": url,
    }
    with _FINAL_LOCK:
        _FINAL_CACHE[tid] = (key, now, value)
    return dict(value)


def _final_info_many(tids: Sequence[str]) -> Dict[str, Dict[str, Any]]:
    """并行 ffprobe（首次 16 个成片约 0.3-0.8s；缓存命中则纯 stat）。"""
    if not tids:
        return {}
    workers = max(1, min(8, len(tids)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        values = list(pool.map(_final_info, tids))
    return dict(zip(tids, values))


# --------------------------------------------------------------------------- #
# 海报：poster.jpg（3D 成片抽帧）> 旧 2D 产物（frame_01.png 等）
# --------------------------------------------------------------------------- #
_POSTER_LOCK = threading.RLock()
_POSTER_QUEUE: List[str] = []
_POSTER_WORKER: Optional[threading.Thread] = None


def _poster_path(tid: str) -> Path:
    return T3D_ROOT / tid / POSTER_FILENAME


def _poster_exists(tid: str) -> bool:
    path = _poster_path(tid)
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def _old_poster_url(tid: str) -> Optional[str]:
    base = V5_ROOT / tid
    for rel in OLD_POSTER_CANDIDATES:
        path = base / rel
        try:
            if path.is_file() and path.stat().st_size > 0:
                return _media_url(f"templates_v5/{tid}/{rel}")
        except OSError:
            continue
    return None


def _generate_poster(tid: str) -> bool:
    """用 ffmpeg 从 final.mp4 抽一帧做海报（同目录临时文件 + os.replace 原子落盘）。"""
    final = _final_path(tid)
    if not final.is_file():
        return False
    target = _poster_path(tid)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False
    tmp = target.with_name(f"poster.tmp-{os.getpid()}-{threading.get_ident()}.jpg")
    cmd = [
        str(jobs.ffmpeg_bin()),
        "-y",
        "-ss",
        "0.8",
        "-i",
        str(final),
        "-frames:v",
        "1",
        "-q:v",
        "3",
        str(tmp),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("生成模板海报失败（%s）：%s", tid, exc)
        return False
    try:
        ok = proc.returncode == 0 and tmp.is_file() and tmp.stat().st_size > 0
    except OSError:
        ok = False
    if not ok:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return False
    try:
        os.replace(tmp, target)
    except OSError as exc:
        logger.warning("写入模板海报失败（%s）：%s", tid, exc)
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return False
    return True


def _poster_worker() -> None:
    global _POSTER_WORKER
    while True:
        with _POSTER_LOCK:
            if not _POSTER_QUEUE:
                _POSTER_WORKER = None
                return
            tid = _POSTER_QUEUE.pop(0)
        try:
            if _poster_exists(tid) or not _final_path(tid).is_file():
                continue
            _generate_poster(tid)
        except Exception:  # noqa: BLE001 - 后台线程必须兜底
            logger.exception("后台生成模板海报异常：%s", tid)


def _enqueue_poster(tid: str) -> None:
    global _POSTER_WORKER
    with _POSTER_LOCK:
        if tid in _POSTER_QUEUE or _poster_exists(tid):
            return
        _POSTER_QUEUE.append(tid)
        if _POSTER_WORKER is not None and _POSTER_WORKER.is_alive():
            return
        _POSTER_WORKER = threading.Thread(
            target=_poster_worker, daemon=True, name="cinema-templates-poster"
        )
        _POSTER_WORKER.start()


def _poster_url(tid: str, final_exists: bool, allow_generate: bool) -> Optional[str]:
    """海报 URL：优先 3D poster.jpg；成片已出但海报未生成时抽帧（列表异步/详情同步）；
    成片还没出来时回落到旧 2D 产物，避免卡片空图。"""
    if _poster_exists(tid):
        return _t3d_media(tid, POSTER_FILENAME)
    if final_exists:
        if allow_generate:
            if _generate_poster(tid):
                return _t3d_media(tid, POSTER_FILENAME)
        else:
            _enqueue_poster(tid)
    return _old_poster_url(tid)


def _report_url(tid: str) -> Optional[str]:
    path = T3D_ROOT / tid / "report.json"
    try:
        if path.is_file() and path.stat().st_size > 0:
            return _t3d_media(tid, "report.json")
    except OSError:
        pass
    return None


# --------------------------------------------------------------------------- #
# 单模板视图
# --------------------------------------------------------------------------- #
def _template_view(
    tid: str,
    progress: Dict[str, Any],
    final_info: Optional[Dict[str, Any]] = None,
    allow_poster_generate: bool = False,
) -> Dict[str, Any]:
    state = _template_state(tid, progress)
    final = final_info if final_info is not None else _final_info(tid)
    return {
        "id": tid,
        "label": _template_label(tid),
        "style": _template_style(tid),
        "status": state["status"],
        "progress": state["progress"],
        "clips": state["clips"],
        "final": final,
        "poster_url": _poster_url(tid, bool(final.get("exists")), allow_poster_generate),
        "report_url": _report_url(tid),
        "error": state["error"],
        "updated": state["updated"],
    }


def _jobs_info() -> Dict[str, Any]:
    with _JOBS_LOCK:
        active_generate = sum(
            1
            for job in _JOBS.values()
            if job.get("kind") == KIND_TEMPLATES_GENERATE and job.get("status") in ACTIVE_STATUSES
        )
        active_assemble = sum(
            1
            for job in _JOBS.values()
            if job.get("kind") == KIND_TEMPLATES_ASSEMBLE and job.get("status") in ACTIVE_STATUSES
        )
    return {
        "active": active_generate + active_assemble,
        "generate": active_generate,
        "assemble": active_assemble,
    }


# --------------------------------------------------------------------------- #
# 轻量轮询：progress + 文件存在性，不 ffprobe（3s 轮询专用）
# --------------------------------------------------------------------------- #
def _status_rows(progress: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for tid in TEMPLATES:
        state = _template_state(tid, progress)
        try:
            final_exists = _final_path(tid).is_file()
        except OSError:
            final_exists = False
        rows.append(
            {
                "id": tid,
                "label": _template_label(tid),
                "status": state["status"],
                "progress": state["progress"],
                "clips": state["clips"],
                "final_exists": final_exists,
                "updated": state["updated"],
            }
        )
    return rows


# --------------------------------------------------------------------------- #
# 逐镜详情（briefs + progress.clips + shots 目录）
# --------------------------------------------------------------------------- #
_SHOT_PROBE_CACHE: Dict[str, Tuple[Tuple[int, int], bool, float]] = {}
_SHOT_PROBE_LOCK = threading.Lock()


def _probe_shot_file(path: Path) -> Tuple[bool, float]:
    try:
        stat = path.stat()
    except OSError:
        return (False, 0.0)
    if stat.st_size < MIN_SHOT_BYTES:
        return (False, 0.0)
    key = (stat.st_mtime_ns, stat.st_size)
    name = str(path)
    with _SHOT_PROBE_LOCK:
        hit = _SHOT_PROBE_CACHE.get(name)
    if hit is not None and hit[0] == key:
        return (hit[1], hit[2])
    duration = float(jobs.probe_video_duration(path) or 0.0)
    valid = duration > 0.0
    with _SHOT_PROBE_LOCK:
        _SHOT_PROBE_CACHE[name] = (key, valid, duration)
    return (valid, duration)


def _probe_shots(paths: Dict[str, Path]) -> Dict[str, Tuple[bool, float]]:
    if not paths:
        return {}
    workers = max(1, min(8, len(paths)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        values = list(pool.map(_probe_shot_file, paths.values()))
    return dict(zip(paths.keys(), values))


def _shot_meta_view(tid: str, sid: str, clip: Dict[str, Any]) -> Dict[str, Any]:
    raw = _JSON_CACHE.get(T3D_ROOT / tid / "shots" / f"{sid}.meta.json")
    meta: Dict[str, Any] = {}
    if isinstance(raw, dict):
        # prompt 很长且前端抽屉不展示，剔除以减小响应体积
        meta = {key: value for key, value in raw.items() if key != "prompt"}
    for key in ("status", "seconds", "prompt_id", "error"):
        if key in clip:
            meta.setdefault(key, clip[key])
    return meta


def _shot_status(kind: str, clip: Dict[str, Any], valid: bool) -> str:
    if kind == "card":
        return "card"  # 片头/片尾卡片沿用旧 v5 卡片镜，不参与 3D 生成
    clip_status = str(clip.get("status") or "").strip().lower()
    if clip_status in CLIP_OK_STATUSES:
        return "done"
    if clip_status == "failed":
        return "failed"
    if valid:
        return "done"
    return "pending"


def _shots_detail(tid: str, progress: Dict[str, Any]) -> List[Dict[str, Any]]:
    briefs = _load_briefs(tid)
    brief_shots = briefs.get("shots") if isinstance(briefs.get("shots"), list) else []
    episode = _JSON_CACHE.get(V5_ROOT / tid / "work" / "episode.json")
    episode_index: Dict[str, Dict[str, Any]] = {}
    if isinstance(episode, dict) and isinstance(episode.get("shots"), list):
        for item in episode["shots"]:
            if isinstance(item, dict) and item.get("id") is not None:
                episode_index[str(item["id"])] = item
    source: List[Dict[str, Any]] = []
    if brief_shots:
        source = [item for item in brief_shots if isinstance(item, dict)]
    else:
        source = list(episode_index.values())

    raw = progress.get(tid) if isinstance(progress, dict) else None
    state = raw if isinstance(raw, dict) else {}
    clip_map = state.get("clips") if isinstance(state.get("clips"), dict) else {}

    shots_dir = T3D_ROOT / tid / "shots"
    probe_paths: Dict[str, Path] = {}
    for item in source:
        sid = str(item.get("id") or "")
        if sid:
            probe_paths[sid] = shots_dir / f"{sid}.mp4"
    probes = _probe_shots(probe_paths)

    shots: List[Dict[str, Any]] = []
    for item in source:
        sid = str(item.get("id") or "").strip()
        if not sid:
            continue
        # briefs 里的 card 镜没有 duration/scene，回落到旧 v5 episode.json
        fallback = episode_index.get(sid) or {}
        raw_kind = str(item.get("kind") or "").strip().lower()
        if not raw_kind:
            raw_type = str(item.get("type") or fallback.get("type") or "").lower()
            raw_kind = "card" if raw_type == "card" else ("broll" if raw_type == "broll" else "dialogue")
        clip = clip_map.get(sid)
        clip = clip if isinstance(clip, dict) else {}
        valid, probed = probes.get(sid, (False, 0.0))
        status = _shot_status(raw_kind, clip, valid)
        planned = item.get("duration")
        if planned is None:
            planned = fallback.get("duration")
        try:
            planned_duration = round(float(planned), 3) if planned is not None else 0.0
        except (TypeError, ValueError):
            planned_duration = 0.0
        scene = item.get("scene") or fallback.get("scene")
        shots.append(
            {
                "id": sid,
                "kind": raw_kind,
                "scene": str(scene) if scene else None,
                "duration": planned_duration,
                "status": status,
                "url": _t3d_media(tid, f"shots/{sid}.mp4"),
                "valid": bool(valid),
                "seconds": round(float(probed), 2) if probed else None,
                "prompt_id": clip.get("prompt_id"),
                "error": str(clip.get("error"))[:400] if clip.get("error") else None,
                "meta": _shot_meta_view(tid, sid, clip),
            }
        )
    return shots


# --------------------------------------------------------------------------- #
# 后台进程探测（避免和正在跑的 pipeline.templates3d 抢 GPU / 抢 progress.json）
# --------------------------------------------------------------------------- #
_PIPE_SCAN_CACHE: Dict[str, Any] = {"ts": 0.0, "modes": None, "ok": False}
_PIPE_SCAN_LOCK = threading.Lock()
_PIPE_SCAN_TTL = 10.0

_POWER_SHELL_SCAN = (
    "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
    "Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress"
)


def _own_job_pids() -> set:
    with _JOBS_LOCK:
        procs = list(_PROCS.values())
    pids = set()
    for proc in procs:
        if proc is None:
            continue
        try:
            if proc.poll() is None:
                pids.add(int(proc.pid))
        except (OSError, ValueError):
            continue
    return pids


def _scan_pipeline_modes() -> Tuple[Dict[str, List[int]], bool]:
    """扫描系统 python 进程里的 pipeline.templates3d 模式（10s 缓存）。

    返回 (modes, ok)：ok=False 表示扫描失败（调用方回落到 progress.json 判断）。
    """
    now = time.monotonic()
    with _PIPE_SCAN_LOCK:
        cached = _PIPE_SCAN_CACHE.get("modes")
        if cached is not None and (now - float(_PIPE_SCAN_CACHE.get("ts") or 0.0)) < _PIPE_SCAN_TTL:
            return cached, bool(_PIPE_SCAN_CACHE.get("ok"))

    modes: Dict[str, List[int]] = {"generate": [], "assemble": [], "watch": []}
    ok = False
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", _POWER_SHELL_SCAN],
            capture_output=True,
            text=True,
            timeout=20,
        )
        if proc.returncode == 0:
            ok = True
            raw = (proc.stdout or "").strip()
            data = json.loads(raw) if raw else []
            if isinstance(data, dict):
                data = [data]
            for item in data if isinstance(data, list) else []:
                if not isinstance(item, dict):
                    continue
                command = str(item.get("CommandLine") or "")
                if "pipeline.templates3d" not in command:
                    continue
                try:
                    pid = int(item.get("ProcessId") or 0)
                except (TypeError, ValueError):
                    continue
                if pid <= 0:
                    continue
                # `--assemble-watch` 也包含子串 "--assemble"，先判 watch
                if "--generate" in command:
                    modes["generate"].append(pid)
                if "--assemble-watch" in command:
                    modes["watch"].append(pid)
                elif "--assemble" in command:
                    modes["assemble"].append(pid)
    except (OSError, subprocess.SubprocessError, ValueError):
        logger.warning("扫描 pipeline.templates3d 后台进程失败，回落 progress.json 判断")

    with _PIPE_SCAN_LOCK:
        _PIPE_SCAN_CACHE["ts"] = now
        _PIPE_SCAN_CACHE["modes"] = modes
        _PIPE_SCAN_CACHE["ok"] = ok
    return modes, ok


def _external_pipeline_modes() -> Tuple[Dict[str, List[int]], bool]:
    modes, ok = _scan_pipeline_modes()
    own = _own_job_pids()
    if own:
        modes = {key: [pid for pid in pids if pid not in own] for key, pids in modes.items()}
    return modes, ok


def _progress_has_fresh_generating(max_age_seconds: float = 300.0) -> bool:
    progress = _load_progress()
    generating = any(
        isinstance(state, dict) and state.get("status") == STATUS_GENERATING
        for state in progress.values()
    )
    if not generating:
        return False
    try:
        age = time.time() - PROGRESS_PATH.stat().st_mtime
    except OSError:
        return False
    return age <= max_age_seconds


def _external_generation_pid() -> Optional[int]:
    """外部 `--generate` 进程 PID；-1 表示扫描失败但进度显示正在生成。"""
    modes, ok = _external_pipeline_modes()
    if ok:
        pids = modes.get("generate") or []
        return int(pids[0]) if pids else None
    return -1 if _progress_has_fresh_generating() else None


def _external_watcher_pids() -> List[int]:
    modes, ok = _external_pipeline_modes()
    if not ok:
        return []
    return list(modes.get("watch") or [])


def _external_assemble_pids() -> List[int]:
    modes, ok = _external_pipeline_modes()
    if not ok:
        return []
    return list(modes.get("assemble") or [])


# --------------------------------------------------------------------------- #
# 请求体 / 参数校验
# --------------------------------------------------------------------------- #
class TemplateGenerateRequest(BaseModel):
    """POST /generate。

    templates: 模板 id 数组 或 "all"（也接受逗号分隔字符串，容错前端）
    force:     true 时给 pipeline 加 --force（重生成/重剪辑已有产物）
    resolution: 可选，默认 768x432（与后台生成任务一致）
    dry_run:   可选，只建任务并立即完成（不启动子进程/不占 GPU），用于前端预览将执行的命令
    """

    templates: Optional[Union[List[str], str]] = "all"
    force: bool = False
    resolution: Optional[str] = None
    dry_run: bool = False


class TemplateAssembleRequest(BaseModel):
    """POST /assemble。"""

    templates: Optional[Union[List[str], str]] = "all"
    force: bool = False
    dry_run: bool = False


def _bad_request(detail: str) -> HTTPException:
    return HTTPException(status_code=400, detail=detail)


def _parse_templates(raw: Any, field: str = "templates") -> List[str]:
    if raw is None:
        raw = "all"
    requested: List[str]
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            raise _bad_request(f"{field} 不能为空：传模板 id 数组或 \"all\"")
        if text.lower() == "all":
            return list(TEMPLATES)
        requested = [item.strip() for item in text.split(",") if item.strip()]
    elif isinstance(raw, (list, tuple)):
        requested = [str(item).strip() for item in raw if str(item).strip()]
    else:
        raise _bad_request(f"{field} 必须是模板 id 数组或 \"all\"")
    if not requested:
        raise _bad_request(f"{field} 不能为空：至少选择一个模板，或传 \"all\"")
    unknown = [item for item in requested if item not in TEMPLATE_SET]
    if unknown:
        raise _bad_request(
            f"模板不存在：{', '.join(unknown[:8])}；可选模板：{', '.join(TEMPLATES)}"
        )
    return list(dict.fromkeys(requested))


def _parse_resolution(raw: Optional[str]) -> str:
    text = str(raw or DEFAULT_RESOLUTION).strip().lower()
    if not re.fullmatch(r"\d{2,5}x\d{2,5}", text):
        raise _bad_request("resolution 格式应为 宽x高（如 768x432）")
    return text


def _generate_cmd(tids: Sequence[str], force: bool, resolution: str) -> List[str]:
    cmd = [
        sys.executable,
        "-m",
        "pipeline.templates3d",
        "--generate",
        "--templates",
        ",".join(tids),
        "--resolution",
        resolution,
    ]
    if force:
        cmd.append("--force")
    return cmd


def _assemble_cmd(tids: Sequence[str], force: bool) -> List[str]:
    cmd = [
        sys.executable,
        "-m",
        "pipeline.templates3d",
        "--assemble",
        "--templates",
        ",".join(tids),
    ]
    if force:
        cmd.append("--force")
    return cmd


# --------------------------------------------------------------------------- #
# 任务执行：复刻 cinema3d._run_job，但用 templates3d 的日志格式解析进度
# --------------------------------------------------------------------------- #
_GEN_START_RE = re.compile(r"^===\s*([a-z0-9_]+)\s*[:：]\s*(\d+)\s*镜\s*===")
_GEN_DONE_RE = re.compile(r"^===\s*([a-z0-9_]+)\s*完成\s*(\d+)\s*/\s*(\d+)")
_GEN_CLIP_RE = re.compile(r"^\[([a-z0-9_]+)\s+(\d+)\s*/\s*(\d+)\]")
_ASM_START_RE = re.compile(r"^---\s*剪辑\s+([a-z0-9_]+)\s*---")
_ASM_DONE_RE = re.compile(r"^---\s*([a-z0-9_]+)\s*剪辑完成")
_ASM_FAIL_RE = re.compile(r"^---\s*([a-z0-9_]+)\s*剪辑失败\s*[:：]?\s*(.*)$")


class _TemplateJobTracker:
    """把 pipeline.templates3d 的 stdout 翻译成任务进度 / 终态结果。"""

    def __init__(self, mode: str, tids: Sequence[str]) -> None:
        self.mode = mode  # "generate" | "assemble"
        self.tids = list(tids)
        self.expected: Dict[str, int] = {tid: _expected_clip_total(tid) for tid in self.tids}
        self.template_total: Dict[str, int] = {
            tid: self.expected.get(tid, 0) for tid in self.tids
        }
        self.done_by_tid: Dict[str, int] = {tid: 0 for tid in self.tids}
        self.assembled: List[str] = []
        self.assemble_failed: List[str] = []
        self.assemble_errors: Dict[str, str] = {}

    # -- 生成 -------------------------------------------------------------
    def _generate_update(self, line: str) -> Optional[Dict[str, Any]]:
        match = _GEN_DONE_RE.match(line)
        if match:
            tid = match.group(1)
            if tid in self.done_by_tid:
                done = int(match.group(2))
                total = int(match.group(3))
                self.done_by_tid[tid] = done
                self.template_total[tid] = max(self.template_total.get(tid, 0), total)
            return self._generate_progress(f"模板 {tid} 生成完成")
        match = _GEN_CLIP_RE.match(line)
        if match:
            tid = match.group(1)
            index, total = int(match.group(2)), int(match.group(3))
            if tid in self.done_by_tid:
                self.done_by_tid[tid] = max(self.done_by_tid[tid], index)
                self.template_total[tid] = max(self.template_total.get(tid, 0), total)
            return self._generate_progress(f"{tid} 逐镜生成中 {index}/{total}")
        match = _GEN_START_RE.match(line)
        if match:
            tid = match.group(1)
            total = int(match.group(2))
            if tid in self.done_by_tid:
                self.template_total[tid] = max(self.template_total.get(tid, 0), total)
            return self._generate_progress(f"开始生成模板 {tid}（{total} 镜）")
        if "达到本次时间预算" in line:
            return {"message": line[:200]}
        return None

    def _generate_progress(self, message: str) -> Dict[str, Any]:
        total = sum(self.template_total.values())
        done = sum(self.done_by_tid.values())
        percent = (done * 100.0 / total) if total > 0 else 1.0
        return {"progress": round(min(99.0, max(1.0, percent)), 1), "message": message[:200]}

    # -- 合成 -------------------------------------------------------------
    def _assemble_update(self, line: str) -> Optional[Dict[str, Any]]:
        match = _ASM_FAIL_RE.match(line)
        if match:
            tid = match.group(1)
            if tid not in self.assemble_failed:
                self.assemble_failed.append(tid)
            detail = (match.group(2) or "").strip()
            if detail.endswith("---"):  # pipeline 输出形如 `--- <tid> 剪辑失败: <exc> ---`
                detail = detail[:-3].strip()
            self.assemble_errors[tid] = detail[:400]
            return self._assemble_progress(f"模板 {tid} 剪辑失败")
        match = _ASM_DONE_RE.match(line)
        if match:
            tid = match.group(1)
            if tid not in self.assembled:
                self.assembled.append(tid)
            return self._assemble_progress(f"模板 {tid} 剪辑完成")
        match = _ASM_START_RE.match(line)
        if match:
            return self._assemble_progress(f"剪辑模板 {match.group(1)} 中…")
        return None

    def _assemble_progress(self, message: str) -> Dict[str, Any]:
        total = max(1, len(self.tids))
        finished = len(self.assembled) + len(self.assemble_failed)
        percent = (finished * 100.0 / total) if total else 1.0
        return {"progress": round(min(99.0, max(1.0, percent)), 1), "message": message[:200]}

    # -- 入口 -------------------------------------------------------------
    def on_line(self, line: str) -> Optional[Dict[str, Any]]:
        if self.mode == "assemble":
            return self._assemble_update(line)
        return self._generate_update(line)

    # -- 终态 -------------------------------------------------------------
    def generate_result(self) -> Dict[str, Any]:
        progress = _load_progress()
        templates_status: Dict[str, str] = {}
        clips_done = 0
        clips_total = 0
        failed_clips: List[str] = []
        for tid in self.tids:
            state = progress.get(tid) if isinstance(progress, dict) else None
            state = state if isinstance(state, dict) else {}
            done, total = _clip_counts(tid, state)
            status = _normalize_status(state.get("status"))
            templates_status[tid] = status
            clips_done += done
            clips_total += total
            clips = state.get("clips") if isinstance(state.get("clips"), dict) else {}
            for sid, clip in clips.items():
                if isinstance(clip, dict) and str(clip.get("status") or "") == "failed":
                    failed_clips.append(f"{tid}/{sid}")
        return {
            "mode": "generate",
            "templates": list(self.tids),
            "clips_done": clips_done,
            "clips_total": clips_total,
            "templates_status": templates_status,
            "failed_clips": failed_clips[:40],
        }

    def assemble_result(self) -> Dict[str, Any]:
        return {
            "mode": "assemble",
            "templates": list(self.tids),
            "assembled": list(self.assembled),
            "failed": list(self.assemble_failed),
            "errors": dict(self.assemble_errors),
        }


def _generate_done_message(result: Dict[str, Any]) -> str:
    tids = result.get("templates") or []
    clips_done = int(result.get("clips_done") or 0)
    clips_total = int(result.get("clips_total") or 0)
    statuses = result.get("templates_status") or {}
    finished = sum(1 for value in statuses.values() if value in (STATUS_GENERATED, STATUS_DONE))
    message = f"3D 生成完成：{clips_done}/{clips_total} 镜，{finished}/{len(tids)} 模板"
    failed = result.get("failed_clips") or []
    if failed:
        message += f"；失败 {len(failed)} 镜（{', '.join(str(item) for item in failed[:3])}）"
    return message


def _assemble_done_message(result: Dict[str, Any]) -> str:
    assembled = result.get("assembled") or []
    failed = result.get("failed") or []
    if assembled:
        message = f"3D 合成完成：成功 {len(assembled)} 个模板"
        if failed:
            message += f"，失败 {len(failed)} 个（{', '.join(str(item) for item in failed[:3])}）"
        return message
    if failed:
        return f"3D 合成失败：{len(failed)} 个模板（{', '.join(str(item) for item in failed[:3])}）"
    return "3D 合成任务结束：没有可剪辑的模板（需先完成逐镜生成，或等 assemble-watch 自动处理）"


def _finish_template_job(job_id: str, message: str, result: Dict[str, Any]) -> None:
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if job is None or job.get("status") == "cancelled":
            return
        job.update(
            status="done",
            progress=100.0,
            finished_at=_now_text(),
            message=message[:300],
            result=result,
        )
        snapshot = dict(job)
    _persist_job(snapshot)


def _run_template_job(job_id: str) -> None:
    """任务线程：起子进程 -> 逐行入日志/解析进度 -> 终态回写（复用 cinema3d 的任务状态函数）。"""
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if job is None:
            return
        cmd = list(job.get("_cmd") or [])
        mode = str(job.get("_mode") or "")
        timeout = job.get("_timeout")
        tids = list(job.get("_templates") or [])
    if not cmd:
        _finish_failed(job_id, "内部错误：任务缺少可执行命令")
        return
    tracker = _TemplateJobTracker(mode, tids)
    label = JOB_KIND_LABEL.get(KIND_TEMPLATES_GENERATE if mode == "generate" else KIND_TEMPLATES_ASSEMBLE, "任务")

    proc: Optional[subprocess.Popen] = None
    try:
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
                message=f"{label}运行中",
            )
            start_snapshot = dict(current)
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
                name=f"cinema-templates-watchdog-{job_id}",
            ).start()
        if proc.stdout is not None:
            for raw in proc.stdout:
                line = str(raw).rstrip("\r\n")
                if not line.strip():
                    continue
                _append_log(job_id, line)
                with _JOBS_LOCK:
                    current = _JOBS.get(job_id)
                    if current is None or current.get("status") != "running":
                        continue
                update = tracker.on_line(line)
                if update:
                    _update_job(job_id, **update)
        proc.wait()
        returncode = proc.returncode
    except Exception as exc:  # noqa: BLE001 - 任务线程必须兜底
        logger.exception("templates3d 任务执行异常：%s", job_id)
        _finish_failed(job_id, f"任务执行异常：{exc}")
        return
    finally:
        with _JOBS_LOCK:
            _PROCS.pop(job_id, None)
        if proc is not None and proc.poll() is None:
            _kill_tree(proc)

    with _JOBS_LOCK:
        current = _JOBS.get(job_id)
        if current is None:
            return
        status = current.get("status")
        logs = list(current.get("logs") or [])
        timed_out = bool(current.get("_timed_out"))
    if status == "cancelled":
        return
    if timed_out:
        _finish_failed(job_id, f"任务超时（>{int(float(timeout or 0))}s），已终止")
        return

    if mode == "assemble":
        result = tracker.assemble_result()
        message = _assemble_done_message(result)
    else:
        result = tracker.generate_result()
        message = _generate_done_message(result)
    if returncode == 0:
        _finish_template_job(job_id, message, result)
    else:
        fail_mode = "assemble" if mode == "assemble" else "generate"
        _finish_failed(job_id, _failure_message(int(returncode or -1), logs, fail_mode))


def _spawn_template_job(job_id: str) -> None:
    threading.Thread(
        target=_run_template_job,
        args=(job_id,),
        daemon=True,
        name=f"cinema-templates-job-{job_id}",
    ).start()


# --------------------------------------------------------------------------- #
# GET /api/cinema/templates
# --------------------------------------------------------------------------- #
@router.get("")
@router.get("/")
def list_templates(response: Response) -> Dict[str, Any]:
    response.headers["Cache-Control"] = "no-store, max-age=0"
    progress = _load_progress()
    finals = _final_info_many(TEMPLATES)
    views = [
        _template_view(tid, progress, finals.get(tid), allow_poster_generate=False)
        for tid in TEMPLATES
    ]
    return {
        "templates": views,
        "summary": _summary(views),
        "jobs": _jobs_info(),
    }


# --------------------------------------------------------------------------- #
# GET /api/cinema/templates/status（轻量轮询，注册在 /{tid} 之前）
# --------------------------------------------------------------------------- #
@router.get("/status")
def get_templates_status(response: Response) -> Dict[str, Any]:
    response.headers["Cache-Control"] = "no-store, max-age=0"
    progress = _load_progress()
    rows = _status_rows(progress)
    return {
        "templates": rows,
        "summary": _summary(rows),
        "jobs": _jobs_info(),
    }


# --------------------------------------------------------------------------- #
# POST /api/cinema/templates/generate
# --------------------------------------------------------------------------- #
def _conflict_message_external_generate(pid: int) -> str:
    if pid > 0:
        return (
            f"已有后台 3D 生成进程在运行（PID {pid}），GPU 正在串行生成，请等它完成后再触发；"
            "可先用 GET /api/cinema/templates 或 GET /api/cinema/templates/status 查看进度"
        )
    return (
        "progress.json 显示正在生成（后台生成的进程未能扫描到），请等当前生成结束后再触发；"
        "可先用 GET /api/cinema/templates/status 查看进度"
    )


def _reserve_or_409(
    conflict_kinds: Sequence[str],
    kind: str,
    tids: Sequence[str],
    **fields: Any,
) -> Dict[str, Any]:
    try:
        return _reserve_job(tuple(conflict_kinds), kind, [], **fields)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - 队列异常不能变成 500 静默
        raise HTTPException(status_code=500, detail=f"任务入队失败：{exc}") from exc


@router.post("/generate")
def start_generate(payload: TemplateGenerateRequest) -> Dict[str, Any]:
    tids = _parse_templates(payload.templates)
    resolution = _parse_resolution(payload.resolution)
    force = bool(payload.force)
    dry_run = bool(payload.dry_run)

    if not dry_run:  # dry-run 不启动子进程，只预览命令，不受后台 GPU 任务影响
        external_pid = _external_generation_pid()
        if external_pid is not None:
            raise HTTPException(
                status_code=409, detail=_conflict_message_external_generate(external_pid)
            )

    cmd = _generate_cmd(tids, force, resolution)
    job = _reserve_or_409(
        GENERATE_CONFLICT_KINDS,
        KIND_TEMPLATES_GENERATE,
        tids,
        _cmd=cmd,
        _mode="generate",
        _timeout=GENERATE_TIMEOUT_SECONDS,
        _templates=list(tids),
        message=f"16 模板 3D 生成排队中（{len(tids)} 个模板）",
        templates=list(tids),
        force=force,
        resolution=resolution,
        dry_run=dry_run,
        result={"mode": "generate", "templates": list(tids), "force": force, "dry_run": dry_run},
    )
    if dry_run:
        _update_job(
            job["id"],
            status="done",
            progress=100.0,
            finished_at=_now_text(),
            message="dry-run：未执行（命令见 logs/result）",
            result={
                "mode": "generate",
                "dry_run": True,
                "templates": list(tids),
                "force": force,
                "command": cmd,
            },
        )
        _append_log(job["id"], "[dry-run] " + " ".join(cmd))
    else:
        _spawn_template_job(job["id"])
    logger.info("templates3d 生成任务入队：%s %s", job["id"], ",".join(tids))
    return {"job_id": job["id"], "templates": list(tids), "dry_run": bool(payload.dry_run)}


# --------------------------------------------------------------------------- #
# POST /api/cinema/templates/assemble
# --------------------------------------------------------------------------- #
@router.post("/assemble")
def start_assemble(payload: TemplateAssembleRequest) -> Dict[str, Any]:
    tids = _parse_templates(payload.templates)
    force = bool(payload.force)
    dry_run = bool(payload.dry_run)

    warning = None
    if not dry_run:  # dry-run 不启动子进程，只预览命令
        watcher_pids = _external_watcher_pids()
        external_assemble = _external_assemble_pids()
        if external_assemble:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"已有后台 3D 合成进程在运行（PID {external_assemble[0]}），"
                    "请等它结束后再触发，避免同一个 final.mp4 被并发覆盖"
                ),
            )
        if force and watcher_pids:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"后台 assemble-watch 守护正在运行（PID {watcher_pids[0]}），"
                    "force 合成会和守护抢同一个成片；请等守护结束（16 模板全部 done 后自动退出）再 force 合成"
                ),
            )
        if watcher_pids:
            warning = (
                f"后台 assemble-watch 守护正在运行（PID {watcher_pids[0]}），会自动剪辑已生成完的模板；"
                "本任务只会处理尚未剪辑的模板（非 force）"
            )

    cmd = _assemble_cmd(tids, force)
    job = _reserve_or_409(
        ASSEMBLE_CONFLICT_KINDS,
        KIND_TEMPLATES_ASSEMBLE,
        tids,
        _cmd=cmd,
        _mode="assemble",
        _timeout=ASSEMBLE_TIMEOUT_SECONDS,
        _templates=list(tids),
        message=f"16 模板 3D 合成排队中（{len(tids)} 个模板）",
        templates=list(tids),
        force=force,
        dry_run=dry_run,
        result={"mode": "assemble", "templates": list(tids), "force": force, "dry_run": dry_run},
    )
    if dry_run:
        _update_job(
            job["id"],
            status="done",
            progress=100.0,
            finished_at=_now_text(),
            message="dry-run：未执行（命令见 logs/result）",
            result={
                "mode": "assemble",
                "dry_run": True,
                "templates": list(tids),
                "force": force,
                "command": cmd,
            },
        )
        _append_log(job["id"], "[dry-run] " + " ".join(cmd))
    else:
        _spawn_template_job(job["id"])
    logger.info("templates3d 合成任务入队：%s %s", job["id"], ",".join(tids))
    response: Dict[str, Any] = {"job_id": job["id"], "templates": list(tids), "dry_run": bool(payload.dry_run)}
    if warning:
        response["warning"] = warning
    return response


# --------------------------------------------------------------------------- #
# GET /api/cinema/templates/{tid}（必须最后注册，避免吃掉 /status 等固定路径）
# --------------------------------------------------------------------------- #
@router.get("/{tid}")
def get_template(tid: str, response: Response) -> Dict[str, Any]:
    response.headers["Cache-Control"] = "no-store, max-age=0"
    key = str(tid or "").strip()
    if key not in TEMPLATE_SET:
        raise HTTPException(
            status_code=404,
            detail=f"模板不存在：{tid}；可选模板：{', '.join(TEMPLATES)}",
        )
    progress = _load_progress()
    view = _template_view(key, progress, allow_poster_generate=True)
    view["shots"] = _shots_detail(key, progress)
    view["look"] = _load_briefs(key).get("look")
    return view
