# -*- coding: utf-8 -*-
"""V18 pipeline 编排 API（`/api/pipeline/*`）+ 项目切换 3D 模板。

端点：
  POST /api/pipeline/run                启动一条 3D 模板全流程任务（单并发）
  GET  /api/pipeline/run/{job_id}       任务状态（stage/逐镜进度/日志尾/成片 URL）
  POST /api/pipeline/run/{job_id}/cancel 取消任务（杀子进程树）
  GET  /api/pipeline/runs               最近任务（默认 20 条）
  POST /api/projects/{pid}/apply-template 把项目切到 3D 模板对应的节目格式

实现：子进程 `python -m pipeline.templates3d_hq --template <tid> [--reuse-shots]`，
stdout 逐行解析阶段：
  `=== <tid>: N 镜 ...`      -> prepare
  `[i/N] <sid> ok (ref2va)`  -> generate / lipsync（原生口型）
  `=== 剪辑 ===`             -> assemble
  `完成: ...`                -> poster（进程退出后 done）
任务状态落 `outputs/pipeline_jobs/<job_id>.json`（刷新/重启后可查）。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import config, models
from ..db import get_db

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

router = APIRouter(prefix="/api", tags=["pipeline-v18"])

#: 阶段表：id -> 中文标签（顺序即 stage_index）
STAGES: List[Dict[str, str]] = [
    {"id": "prepare", "label": "准备"},
    {"id": "generate", "label": "生成镜头"},
    {"id": "lipsync", "label": "原生口型"},
    {"id": "assemble", "label": "剪辑合成"},
    {"id": "poster", "label": "海报"},
    {"id": "done", "label": "完成"},
]
_STAGE_INDEX = {item["id"]: idx for idx, item in enumerate(STAGES)}
_STAGE_LABEL = {item["id"]: item["label"] for item in STAGES}

_TEMPLATE_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,60}$")
_SHOT_PROGRESS_RE = re.compile(r"\[(\d+)/(\d+)\]")
_PREPARE_RE = re.compile(r"===\s*[a-z0-9_]+:\s*(\d+)\s*镜")
_LOG_LIMIT = 400
_TAIL_LINES = 80
_RUNS_LIMIT = 20

_SEMANTIC_DIR = config.CONTRACT_DIR / "v5" / "cinema3d" / "templates3d"
_TEMPLATE_CONTRACT_DIR = config.CONTRACT_DIR / "v5" / "templates"
_JOB_DIR = config.OUTPUTS_DIR / "pipeline_jobs"

_LOCK = threading.RLock()
_JOBS: Dict[str, Dict[str, Any]] = {}
_ACTIVE: Optional[str] = None


# --------------------------------------------------------------------------- #
# 模板 / 路径
# --------------------------------------------------------------------------- #
def _template_ids() -> List[str]:
    """19 个 3D 模板 id：优先 pipeline.templates3d.TEMPLATES，回落 contract semantic 文件。"""
    ids: List[str] = []
    try:
        from pipeline import templates3d  # noqa: PLC0415

        ids = [str(item).strip() for item in (getattr(templates3d, "TEMPLATES", None) or [])
               if str(item).strip()]
    except Exception:  # noqa: BLE001 - pipeline 不可用则只靠 contract
        ids = []
    if not ids and _SEMANTIC_DIR.is_dir():
        ids = sorted(path.name[: -len("_semantic.json")] for path in _SEMANTIC_DIR.glob("*_semantic.json"))
    return [tid for tid in ids if _TEMPLATE_ID_RE.fullmatch(tid)]


def _semantic_path(tid: str) -> Path:
    return _SEMANTIC_DIR / f"{tid}_semantic.json"


def _semantic_shot_total(tid: str) -> int:
    try:
        data = json.loads(_semantic_path(tid).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    shots = data.get("shots") if isinstance(data, dict) else None
    if not isinstance(shots, list):
        return 0
    return sum(1 for item in shots if isinstance(item, dict) and item.get("prompt"))


def _existing_shots(tid: str) -> int:
    shots_dir = config.OUTPUTS_DIR / "templates_3d" / tid / "shots"
    if not shots_dir.is_dir():
        return 0
    return sum(1 for path in shots_dir.glob("*.mp4") if path.stat().st_size > 100_000)


def _final_url(tid: str) -> Optional[str]:
    path = config.OUTPUTS_DIR / "templates_3d" / tid / "final.mp4"
    try:
        if not path.is_file() or path.stat().st_size <= 0:
            return None
        return config.media_url(path)
    except OSError:
        return None


def _template_label(tid: str) -> Optional[str]:
    try:
        data = json.loads((_TEMPLATE_CONTRACT_DIR / f"{tid}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    label = data.get("label") if isinstance(data, dict) else None
    return str(label) if label else None


# --------------------------------------------------------------------------- #
# job 存储（内存 + outputs/pipeline_jobs/*.json）
# --------------------------------------------------------------------------- #
def _now_text() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _persist(job: Dict[str, Any]) -> None:
    try:
        _JOB_DIR.mkdir(parents=True, exist_ok=True)
        target = _JOB_DIR / f"{job['id']}.json"
        tmp = target.with_name(f"{target.name}.tmp-{os.getpid()}-{threading.get_ident()}")
        tmp.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, target)
    except OSError:
        pass


def _elapsed_sec(job: Dict[str, Any]) -> float:
    started = job.get("started_ts")
    if not isinstance(started, (int, float)):
        return 0.0
    finished = job.get("finished_ts")
    end = float(finished) if isinstance(finished, (int, float)) else time.time()
    return round(max(0.0, end - float(started)), 1)


def _job_view(job: Dict[str, Any], *, tail: int = _TAIL_LINES) -> Dict[str, Any]:
    stage = str(job.get("stage") or "prepare")
    total = int(job.get("total") or 0)
    done = int(job.get("done") or 0)
    return {
        "id": job.get("id"),
        "template_id": job.get("template_id"),
        "status": job.get("status"),
        "stage": stage,
        "stage_index": int(_STAGE_INDEX.get(stage, 0)),
        "stage_label": _STAGE_LABEL.get(stage, stage),
        "progress": {"done": max(0, done), "total": max(0, total)},
        "log_tail": (job.get("log") or [])[-tail:],
        "elapsed_sec": _elapsed_sec(job),
        "final_url": _final_url(str(job.get("template_id") or "")) if job.get("status") == "done" else None,
        "error": job.get("error"),
        "reuse_shots": bool(job.get("reuse_shots")),
        "created_at": job.get("created_at"),
        "finished_at": job.get("finished_at"),
        "stages": [item["id"] for item in STAGES],
    }


def _append_log(job: Dict[str, Any], raw: str) -> None:
    """追加一行日志并解析阶段/逐镜进度（解析的是去掉 `[HH:MM:SS]` 前缀后的正文）。"""
    line = raw.rstrip("\r\n")
    if not line:
        return
    log: List[str] = job.setdefault("log", [])
    log.append(line[:800])
    if len(log) > _LOG_LIMIT:
        del log[: len(log) - _LOG_LIMIT]

    body = line
    stamp = re.match(r"^\[\d{2}:\d{2}:\d{2}\]\s*", body)
    if stamp:
        body = body[stamp.end():]
    changed = False
    shot = _SHOT_PROGRESS_RE.search(body)
    if shot:
        done, total = int(shot.group(1)), int(shot.group(2))
        if job.get("done") != done or job.get("total") != total:
            job["done"], job["total"] = done, total
            changed = True
        new_stage = "lipsync" if "ref2va" in body else "generate"
        if job.get("stage") != new_stage:
            job["stage"] = new_stage
            changed = True
    elif "=== 剪辑 ===" in body:
        if job.get("stage") != "assemble":
            job["stage"] = "assemble"
            changed = True
    elif _PREPARE_RE.search(body):
        match = _PREPARE_RE.search(body)
        if job.get("stage") != "prepare":
            job["stage"] = "prepare"
            changed = True
        if match and not job.get("total"):
            job["total"] = int(match.group(1))
            changed = True
    elif body.startswith("完成"):
        if job.get("stage") != "poster":
            job["stage"] = "poster"
            changed = True
    if changed and job.get("status") == "running":
        _persist(job)


def _run_job(job_id: str) -> None:
    global _ACTIVE
    job = _JOBS.get(job_id)
    if not job:
        return
    if job.get("cancel_requested") or job.get("status") == "canceled":
        with _LOCK:
            job["status"] = "canceled"
            job["error"] = job.get("error") or "用户取消"
            job["finished_at"] = _now_text()
            job["finished_ts"] = time.time()
            _persist(job)
            if _ACTIVE == job_id:
                _ACTIVE = None
        return
    tid = str(job.get("template_id") or "")
    cmd = [sys.executable, "-m", "pipeline.templates3d_hq", "--template", tid]
    if job.get("reuse_shots"):
        cmd.append("--reuse-shots")
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(_REPO_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=env,
            creationflags=creationflags,
        )
    except Exception as exc:  # noqa: BLE001
        with _LOCK:
            job["status"] = "failed"
            job["error"] = f"启动子进程失败: {exc}"
            job["finished_at"] = _now_text()
            job["finished_ts"] = time.time()
            _persist(job)
            if _ACTIVE == job_id:
                _ACTIVE = None
        return
    with _LOCK:
        job["pid"] = proc.pid
        if job.get("cancel_requested") or job.get("status") == "canceled":
            # 启动与取消竞态：进程刚起来就被取消 → 立刻杀掉，不进入 running
            job["status"] = "canceled"
            job["error"] = job.get("error") or "用户取消"
            job["finished_at"] = _now_text()
            job["finished_ts"] = time.time()
            _persist(job)
            if _ACTIVE == job_id:
                _ACTIVE = None
            kill_now = True
        else:
            job["status"] = "running"
            job["started_ts"] = time.time()
            job["started_at"] = _now_text()
            job["stage"] = job.get("stage") or "prepare"
            kill_now = False
            _persist(job)
    if kill_now:
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                               capture_output=True, timeout=30)
            else:
                proc.kill()
        except Exception:  # noqa: BLE001
            pass
        try:
            proc.wait(timeout=10)
        except Exception:  # noqa: BLE001
            pass
        return
    try:
        if proc.stdout is not None:
            for line in proc.stdout:
                with _LOCK:
                    _append_log(job, line)
                    if job.get("status") == "canceled":
                        continue
        code = proc.wait()
    except Exception as exc:  # noqa: BLE001 - 读流失败也要收尾
        with _LOCK:
            job["error"] = f"读取任务日志失败: {exc}"
        code = proc.poll() if proc.poll() is not None else -1
    with _LOCK:
        job["returncode"] = code
        job["finished_at"] = _now_text()
        job["finished_ts"] = time.time()
        if job.get("status") == "canceled" or job.get("cancel_requested"):
            job["status"] = "canceled"
            job["error"] = job.get("error") or "任务已取消"
        elif code == 0:
            job["status"] = "done"
            job["stage"] = "done"
            total = int(job.get("total") or 0)
            job["done"] = total
        else:
            job["status"] = "failed"
            tail = " / ".join((job.get("log") or [])[-5:])
            job["error"] = job.get("error") or f"进程退出码 {code}" + (f"：{tail[:600]}" if tail else "")
        _persist(job)
        if _ACTIVE == job_id:
            _ACTIVE = None


# --------------------------------------------------------------------------- #
# 启动时恢复（重启后把中断任务标 failed，避免永远 running）
# --------------------------------------------------------------------------- #
def _load_persisted_jobs() -> None:
    if not _JOB_DIR.is_dir():
        return
    paths = sorted(_JOB_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime)
    for path in paths[-50:]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict) or not data.get("id"):
            continue
        if data.get("status") in ("queued", "running"):
            data["status"] = "failed"
            data["error"] = "后端服务重启，任务中断（可重新发起）"
            data["finished_at"] = _now_text()
            data["finished_ts"] = time.time()
            _persist(data)
        _JOBS[str(data["id"])] = data


_load_persisted_jobs()


# --------------------------------------------------------------------------- #
# 请求模型 / 端点
# --------------------------------------------------------------------------- #
class PipelineRunBody(BaseModel):
    template_id: str
    reuse_shots: bool = False


class ApplyTemplateBody(BaseModel):
    template_id: str


@router.post("/pipeline/run", status_code=202, summary="启动 3D 模板全流程任务（单并发）")
def start_pipeline_run(payload: PipelineRunBody):
    global _ACTIVE
    tid = str(payload.template_id or "").strip()
    if not _TEMPLATE_ID_RE.fullmatch(tid):
        raise HTTPException(status_code=400, detail="template_id 必须是合法模板 id")
    if tid not in _template_ids():
        raise HTTPException(status_code=400, detail=f"未知模板: {tid}")
    if not _semantic_path(tid).is_file():
        raise HTTPException(status_code=400, detail=f"模板语义 brief 不存在: {tid}_semantic.json")
    reuse = bool(payload.reuse_shots)
    if reuse and _existing_shots(tid) <= 0:
        raise HTTPException(status_code=400,
                            detail=f"模板 {tid} 没有可复用的镜头（shots/ 为空），不能 reuse_shots")
    with _LOCK:
        if _ACTIVE:
            active = _JOBS.get(_ACTIVE) or {}
            if active.get("status") in ("queued", "running"):
                raise HTTPException(status_code=409, detail={
                    "message": "已有 pipeline 任务在运行（单并发）",
                    "job_id": _ACTIVE,
                    "template_id": active.get("template_id"),
                })
            _ACTIVE = None
        job_id = "pl_" + uuid.uuid4().hex[:12]
        job: Dict[str, Any] = {
            "id": job_id,
            "template_id": tid,
            "template_label": _template_label(tid),
            "reuse_shots": reuse,
            "status": "queued",
            "stage": "prepare",
            "total": _semantic_shot_total(tid) or _existing_shots(tid),
            "done": 0,
            "log": [],
            "error": None,
            "created_at": _now_text(),
            "created_ts": time.time(),
            "finished_at": None,
            "finished_ts": None,
        }
        _JOBS[job_id] = job
        _ACTIVE = job_id
        _persist(job)
    threading.Thread(target=_run_job, args=(job_id,), daemon=True, name=f"pipeline-{job_id}").start()
    return {
        "ok": True,
        "job_id": job_id,
        "template_id": tid,
        "template_label": _template_label(tid),
        "reuse_shots": reuse,
        "status": "queued",
        "stages": [item["id"] for item in STAGES],
        "stage_labels": dict(_STAGE_LABEL),
    }


@router.get("/pipeline/run/{job_id}", summary="pipeline 任务状态（阶段/进度/日志尾/成片）")
def get_pipeline_run(job_id: str):
    with _LOCK:
        job = _JOBS.get(str(job_id or "").strip())
        if not job:
            raise HTTPException(status_code=404, detail=f"任务不存在: {job_id}")
        return _job_view(job)


@router.post("/pipeline/run/{job_id}/cancel", summary="取消 pipeline 任务")
def cancel_pipeline_run(job_id: str):
    global _ACTIVE
    key = str(job_id or "").strip()
    with _LOCK:
        job = _JOBS.get(key)
        if not job:
            raise HTTPException(status_code=404, detail=f"任务不存在: {job_id}")
        if job.get("status") not in ("queued", "running"):
            return {"ok": True, "job_id": key, "status": job.get("status"),
                    "message": "任务已结束，无需取消"}
        job["cancel_requested"] = True
        job["status"] = "canceled"
        job["stage"] = job.get("stage") or "prepare"
        job["finished_at"] = _now_text()
        job["finished_ts"] = time.time()
        job["error"] = job.get("error") or "用户取消"
        pid = job.get("pid")
        _persist(job)
    if isinstance(pid, int) and pid > 0:
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                               capture_output=True, timeout=30)
            else:
                os.kill(pid, 15)
        except Exception:  # noqa: BLE001 - 取消失败不阻塞响应，进程收尾线程会兜底
            pass
    return {"ok": True, "job_id": key, "status": "canceled"}


@router.get("/pipeline/runs", summary="最近 pipeline 任务（默认 20 条）")
def list_pipeline_runs(limit: int = 20):
    limit = max(1, min(int(limit or 20), 50))
    with _LOCK:
        jobs = sorted(_JOBS.values(), key=lambda item: item.get("created_ts") or 0, reverse=True)
        rows = [_job_view(job, tail=20) for job in jobs[:limit]]
    return {"ok": True, "count": len(rows), "runs": rows}


@router.post("/projects/{pid}/apply-template", summary="把项目切到 3D 模板对应的节目格式")
def apply_template(pid: str, payload: ApplyTemplateBody, db: Session = Depends(get_db)):
    project = db.get(models.Project, pid)
    if project is None:
        raise HTTPException(status_code=404, detail=f"项目不存在: {pid}")
    tid = str(payload.template_id or "").strip()
    if not _TEMPLATE_ID_RE.fullmatch(tid):
        raise HTTPException(status_code=400, detail="template_id 必须是合法模板 id")
    if tid not in _template_ids():
        raise HTTPException(status_code=400, detail=f"未知模板: {tid}")

    direct = config.FORMATS_DIR / f"{tid}_5min.json"
    mapped = "direct"
    if direct.is_file():
        format_id = direct.stem
        note = "已切换到与模板同名的节目格式"
    else:
        candidates = sorted(config.FORMATS_DIR.glob("*.json"))
        if not candidates:
            raise HTTPException(status_code=400, detail="contract/formats 下没有可用的节目格式")
        format_id = candidates[0].stem
        mapped = "fallback_first"
        note = ("contract/formats 中没有该模板的同名格式，已回落到首个可用格式；"
                "项目表没有 template_id 字段，模板选择请由前端/上层保存")
    project.format_id = format_id
    db.commit()
    db.refresh(project)
    return {
        "ok": True,
        "project_id": pid,
        "format_id": format_id,
        "template_id": tid,
        "template_label": _template_label(tid),
        "mapped": mapped,
        "note": note,
    }
