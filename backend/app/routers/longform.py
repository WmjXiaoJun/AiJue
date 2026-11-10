# -*- coding: utf-8 -*-
"""V54：5 分钟长版批量生成 API（前端 `/api/v5/longform/*` 用）。

- POST /api/v5/longform/run   {template|"all", duration, force}
- GET  /api/v5/longform/status
- GET  /api/v5/longform/report

实际执行交给 scripts/rebuild_all_5min.py（子进程），服务端只做任务状态与日志。
"""
from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, HTTPException

from pipeline import utils, v5_catalog  # noqa: E402

from .. import config, ids  # noqa: E402
from pipeline.agents.workflow import is_shorts  # noqa: E402

REPO = Path(__file__).resolve().parents[3]
router = APIRouter(prefix="/api/v5/longform", tags=["v5-longform"])

RUN_DIR = Path(config.OUTPUTS_DIR) / "longform"
REPORT = RUN_DIR / "report_5min.json"
# id 规则唯一来源：backend/app/ids.py（不再本地再写一份正则 —— 多套边界就是 P1-3 的根因）
ID_RE = ids.SAFE_ID

_lock = threading.Lock()
_jobs: Dict[str, Dict[str, Any]] = {}
_current: Optional[str] = None


def _status_view(job: Dict[str, Any]) -> Dict[str, Any]:
    out = {k: job.get(k) for k in
           ("job_id", "status", "created", "ended", "mode", "templates", "duration")}
    out["log_tail"] = (job.get("log") or [])[-40:]
    out["items"] = job.get("items") or []
    return out


def _worker(job_id: str, args: List[str]) -> None:
    job = _jobs[job_id]
    cmd = [sys.executable, str(REPO / "scripts" / "rebuild_all_5min.py"), *args]
    try:
        proc = subprocess.Popen(cmd, cwd=str(REPO), stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True,
                                encoding="utf-8", errors="replace", bufsize=1)
        for line in proc.stdout or []:
            job["log"].append(line.rstrip())
        code = proc.wait()
        job["status"] = "ok" if code == 0 else "failed"
        job["exit"] = code
    except BaseException as exc:  # noqa: BLE001
        job["status"] = "failed"
        job["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        job["ended"] = time.strftime("%Y-%m-%d %H:%M:%S")
        try:
            if REPORT.is_file():
                job["items"] = json.loads(REPORT.read_text(encoding="utf-8")).get("items") or []
        except (OSError, ValueError):
            pass
        with _lock:
            global _current
            if _current == job_id:
                _current = None


@router.post("/run", summary="生成 5 分钟长版（单模板或全部）")
def run_longform(payload: dict = Body(default=None)) -> Dict[str, Any]:
    global _current
    payload = dict(payload or {})
    # P1-2：`duration="abc"` 曾在请求线程里抛 ValueError -> FastAPI 500（不是 4xx）。
    # 现在类型错 → 422 + 明确 detail；范围错（<30s）仍是 400，两者的语义要分清。
    try:
        duration = float(payload.get("duration") or 300.0)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=422, detail=f"duration 必须是数字（收到 {payload.get('duration')!r}）"
        ) from exc
    if duration < 30:
        raise HTTPException(status_code=400, detail="duration 至少 30 秒")
    target = str(payload.get("template") or "all").strip()
    # P2-7：`force="false"` 曾被 bool() 判成 True
    force = ids.as_bool(payload.get("force"))
    if target != "all" and not ID_RE.match(target):
        raise HTTPException(status_code=400, detail=f"非法 template: {target}")
    if target != "all":
        catalog = v5_catalog.load_catalog()
        if target not in (catalog.get("templates") or {}):
            raise HTTPException(status_code=404, detail=f"模板不存在: {target}")
    with _lock:
        if _current:
            raise HTTPException(status_code=409, detail=f"已有长版任务在跑: {_current}")
        job_id = "lf_" + uuid.uuid4().hex[:10]
        args: List[str] = []
        if target != "all":
            args += ["--only", target]
        if force:
            args.append("--force")
        _jobs[job_id] = {"job_id": job_id, "status": "queued", "log": [],
                         "created": time.strftime("%Y-%m-%d %H:%M:%S"),
                         "ended": None, "mode": target,
                         "templates": ([target] if target != "all" else "all"),
                         "duration": duration, "items": []}
        _current = job_id
    threading.Thread(target=_worker, args=(job_id, args), daemon=True,
                     name=f"longform-{job_id}").start()
    return {"ok": True, "job_id": job_id, "mode": target, "duration": duration}


@router.get("/status", summary="长版任务状态")
def longform_status(job_id: str = "") -> Dict[str, Any]:
    with _lock:
        if job_id:
            job = _jobs.get(job_id)
            if job is None:
                raise HTTPException(status_code=404, detail=f"未知任务: {job_id}")
            return _status_view(job)
        if _current and _current in _jobs:
            return _status_view(_jobs[_current])
        if not _jobs:
            return {"status": "idle", "items": _report_items()}
        latest = sorted(_jobs.values(), key=lambda j: j.get("created") or "")[-1]
        view = _status_view(latest)
        if not view.get("items"):
            view["items"] = _report_items()
        return view


def _report_items() -> List[Dict[str, Any]]:
    try:
        data = json.loads(REPORT.read_text(encoding="utf-8"))
        return data.get("items") or []
    except (OSError, ValueError):
        return []


@router.get("/report", summary="5 分钟重制报告")
def longform_report() -> Dict[str, Any]:
    if not REPORT.is_file():
        return {"target": 300.0, "items": []}
    try:
        return json.loads(REPORT.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=500, detail=f"报告损坏: {exc}")