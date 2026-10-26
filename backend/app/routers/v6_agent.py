# -*- coding: utf-8 -*-
"""V6 Agent API：工作流读取/保存、智能体运行、修改操作、运行记录、美术资产。

路由（挂在 /api 之外，独立前缀）：
- GET  /api/v6/agent/overview                   角色/模板/工作流概览
- GET  /api/v6/agent/workflow/{template}        读取工作流 JSON
- PUT  /api/v6/agent/workflow/{template}        保存工作流 JSON（智能体编辑）
- POST /api/v6/agent/run                       启动后台智能体 run（单并发）
- GET  /api/v6/agent/runs                      最近 run（内存 job + 磁盘 run 目录）
- GET  /api/v6/agent/runs/{job_id}             单 job 状态/日志/结果
- POST /api/v6/agent/edit                      智能体修改（set_voice/set_pose/patch_*）
- GET  /api/v6/agent/art                       角色资产状态 + 姿态策略
- POST /api/v6/agent/pose                      姿态资产启停
- GET  /media/agent_runs/{run_id}/{file}       run 证据文件（summary.md/run.json/events.jsonl）
"""
from __future__ import annotations

import json
import re
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import FileResponse

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from pipeline import v5_catalog  # noqa: E402
from pipeline.agents import art as agent_art  # noqa: E402
from pipeline.agents.ops import apply_op  # noqa: E402
from pipeline.agents.orchestrator import RUN_ROOT, latest_runs, run_template  # noqa: E402
from pipeline.agents.roles import ROLES  # noqa: E402
from pipeline.agents.workflow import WORKFLOW_DIR, load_workflow, save_workflow  # noqa: E402

router = APIRouter(tags=["v6-agent"])
ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,60}$")
RUN_MEDIA = {"summary.md", "run.json", "events.jsonl", "template_summary.json",
             "workflow.json"}

_jobs: Dict[str, Dict[str, Any]] = {}
_job_lock = threading.Lock()
_current_job: Optional[str] = None


def _tpl(template_id: str) -> dict:
    if not ID_RE.match(template_id or ""):
        raise HTTPException(status_code=400, detail="非法模板 id")
    cat = v5_catalog.load_catalog()
    tpl = (cat.get("templates") or {}).get(template_id)
    if not tpl:
        raise HTTPException(status_code=404, detail=f"模板不存在: {template_id}")
    return tpl


@router.get("/api/v6/agent/overview")
def overview() -> dict:
    cat = v5_catalog.load_catalog()
    templates = []
    for tid, tpl in sorted((cat.get("templates") or {}).items()):
        if tid.startswith("custom_"):
            continue
        wf = load_workflow(tid, tpl)
        templates.append({
            "id": tid, "label": tpl.get("label"), "style": tpl.get("style"),
            "cast": tpl.get("cast") or [],
            "stages": [{"id": s.get("id"), "agent": s.get("agent"),
                        "tool": s.get("tool"), "enabled": s.get("enabled", True),
                        "params": s.get("params") or {}} for s in wf.get("stages") or []],
            "workflow_file": str(WORKFLOW_DIR / f"{tid}.json"),
        })
    return {"ok": True, "roles": list(ROLES.values()), "templates": templates,
            "count": len(templates),
            "runs": [r.get("run_id") for r in latest_runs(None, 8)]}


@router.get("/api/v6/agent/workflow/{template}")
def get_workflow(template: str) -> dict:
    _tpl(template)
    return {"ok": True, "template": template, "workflow": load_workflow(template)}


@router.put("/api/v6/agent/workflow/{template}")
def put_workflow(template: str, body: dict = Body(...)) -> dict:
    _tpl(template)
    wf = body.get("workflow") or body
    if not isinstance(wf, dict) or not wf.get("stages"):
        raise HTTPException(status_code=400, detail="workflow.stages 必须是非空数组")
    wf["id"] = template
    path = save_workflow(wf)
    return {"ok": True, "template": template, "path": str(path),
            "stages": [s.get("id") for s in wf.get("stages") or []]}


def _run_worker(job_id: str, payload: dict) -> None:
    global _current_job
    with _job_lock:
        job = _jobs[job_id]
        job["status"] = "running"
        job["started"] = time.strftime("%Y-%m-%d %H:%M:%S")
        _current_job = job_id

    def prog(stage: str, msg: str) -> None:
        with _job_lock:
            j = _jobs.get(job_id)
            if j is not None:
                j["logs"].append({"t": time.strftime("%H:%M:%S"), "stage": stage,
                                  "msg": str(msg)[:500]})
                if len(j["logs"]) > 800:
                    j["logs"] = j["logs"][-800:]

    try:
        run = run_template(
            str(payload["template"]), duration=float(payload.get("duration") or 40),
            seed=int(payload.get("seed") or 20261101),
            force=bool(payload.get("force")), stages=payload.get("stages"),
            start=payload.get("start"), goal=str(payload.get("goal") or ""),
            dry_run=bool(payload.get("dry_run")), prog=prog)
        with _job_lock:
            job = _jobs[job_id]
            job["status"] = run.status
            job["ended"] = time.strftime("%Y-%m-%d %H:%M:%S")
            job["result"] = run.to_dict()
            job["run_dir"] = str(run.run_dir)
    except Exception as exc:  # noqa: BLE001
        with _job_lock:
            job = _jobs[job_id]
            job["status"] = "failed"
            job["error"] = str(exc)
            job["ended"] = time.strftime("%Y-%m-%d %H:%M:%S")
    finally:
        with _job_lock:
            _current_job = None


@router.post("/api/v6/agent/run")
def start_run(payload: dict = Body(...)) -> dict:
    template = str(payload.get("template") or "")
    _tpl(template)
    stages = payload.get("stages")
    if isinstance(stages, str):
        stages = [s.strip() for s in stages.split(",") if s.strip()]
    payload = dict(payload)
    payload["stages"] = stages
    with _job_lock:
        running = [j for j in _jobs.values() if j.get("status") == "running"]
        if running:
            raise HTTPException(status_code=409,
                                detail=f"已有 run 在跑: {running[0]['job_id']}")
        job_id = "job_" + uuid.uuid4().hex[:10]
        _jobs[job_id] = {
            "job_id": job_id, "status": "queued", "logs": [],
            "created": time.strftime("%Y-%m-%d %H:%M:%S"),
            "payload": {k: payload.get(k) for k in
                        ("template", "duration", "stages", "goal", "dry_run", "force")},
        }
    th = threading.Thread(target=_run_worker, args=(job_id, payload), daemon=True)
    th.start()
    return {"ok": True, "job_id": job_id}


@router.get("/api/v6/agent/runs")
def list_runs() -> dict:
    with _job_lock:
        jobs = sorted(_jobs.values(), key=lambda j: j.get("created") or "", reverse=True)[:20]
    disk = latest_runs(None, 10)
    return {"ok": True, "jobs": [{k: v for k, v in j.items() if k != "logs"} for j in jobs],
            "disk_runs": disk}


@router.get("/api/v6/agent/runs/{job_id}")
def get_run(job_id: str) -> dict:
    with _job_lock:
        job = _jobs.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="job 不存在")
        return {"ok": True, **job}


@router.post("/api/v6/agent/edit")
def agent_edit(payload: dict = Body(...)) -> dict:
    template = str(payload.get("template") or "")
    _tpl(template)
    op = str(payload.get("op") or "")
    try:
        return apply_op(template, op, character=payload.get("character"),
                        pose=payload.get("pose"), value=payload.get("value"),
                        json=payload.get("json"), stage=payload.get("stage"))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/api/v6/agent/art")
def agent_art_list() -> dict:
    return agent_art.list_art(None, {})


@router.post("/api/v6/agent/pose")
def agent_pose(payload: dict = Body(...)) -> dict:
    try:
        return agent_art.set_pose(None, {"character": payload.get("character"),
                                         "pose": payload.get("pose"),
                                         "enabled": bool(payload.get("enabled", True))})
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/media/agent_runs/{run_id}/{file}")
def agent_run_media(run_id: str, file: str) -> FileResponse:
    if not re.match(r"^[a-z0-9_]{8,40}$", run_id) or file not in RUN_MEDIA:
        raise HTTPException(status_code=404, detail="not found")
    path = (RUN_ROOT / run_id / file).resolve()
    root = RUN_ROOT.resolve()
    if not str(path).startswith(str(root)) or not path.is_file():
        raise HTTPException(status_code=404, detail="not found")
    return FileResponse(path)
