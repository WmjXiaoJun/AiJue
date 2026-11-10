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
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, HTTPException

from .. import config, ids
from fastapi.responses import FileResponse

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from pipeline import v5_catalog  # noqa: E402
from pipeline.agents import art as agent_art  # noqa: E402
from pipeline.agents.ops import apply_op  # noqa: E402
from pipeline.agents.orchestrator import RUN_ROOT, latest_runs, run_template  # noqa: E402
from pipeline.agents.roles import ROLES  # noqa: E402
from pipeline.agents.workflow import (  # noqa: E402
    WORKFLOW_DIR, is_shorts, load_workflow, save_workflow)

def _remember_job(job_id: str, limit: int = 200) -> None:
    """内存 run 表按插入顺序淘汰，防止长跑服务无限增长（P2-9）。"""
    if job_id in _jobs:
        return
    while len(_jobs) >= limit:
        oldest = next(iter(_jobs))
        _jobs.pop(oldest, None)


router = APIRouter(tags=["v6-agent"])
# id 规则唯一来源：backend/app/ids.py（这里不再自己写正则 —— 审查 P1-3 的根因就是
# 本文件 ID_RE `{1,60}` 与 /media 的 `{8,40}` 两套边界并存）。保留别名方便既有调用。
ID_RE = ids.SAFE_ID
RUN_MEDIA = {"summary.md", "run.json", "events.jsonl", "template_summary.json",
             "workflow.json"}

_jobs: Dict[str, Dict[str, Any]] = {}
_job_lock = threading.Lock()
_current_job: Optional[str] = None


def _as_bool(value, default: bool = False) -> bool:
    """严格布尔：兼容 "false"/"0"/"no" 这类字符串，避免 bool("false")==True。"""
    return ids.as_bool(value, default)


def _rel(path: Any) -> str:
    """对外只给仓库相对路径（审查 P2-2：绝不回吐盘符 / 部署机用户名）。"""
    return ids.rel_to_repo(path)


def _tpl(template_id: str) -> dict:
    ident = ids.check_id(template_id, "模板 id")
    cat = v5_catalog.load_catalog()
    tpl = (cat.get("templates") or {}).get(ident)
    if not tpl:
        raise HTTPException(status_code=404, detail=f"模板不存在: {ident}")
    return tpl


MEDIA_DIRS = (Path(config.OUTPUTS_DIR) / "templates_2d",
              Path(config.OUTPUTS_DIR) / "templates_3d",
              Path(config.OUTPUTS_DIR) / "templates_v5")
SERIES_DIR = _REPO_ROOT / "contract" / "v5" / "series"


def _final_of(template_id: str) -> Optional[str]:
    """成片真实地址（多目录都可能有，取 mtime 最新的一份），没有则 None。"""
    candidates = [root / template_id / "final.mp4" for root in MEDIA_DIRS]
    candidates = [p for p in candidates if p.is_file() and p.stat().st_size > 0]
    if not candidates:
        return None
    path = max(candidates, key=lambda p: p.stat().st_mtime)
    return f"/media/{path.parent.parent.name}/{template_id}/final.mp4"


def _render_type(template_id: str, tpl: dict) -> str:
    """2d / 3d / realistic（与 /api/v5/catalog 同口径，render_style 视为 2d）。"""
    raw = str((tpl or {}).get("render_type") or "").strip().lower()
    if raw in ("2d", "3d", "realistic"):
        return raw
    if str((tpl or {}).get("render_style") or "").strip():
        return "2d"
    return "realistic" if template_id.startswith("real_") else "3d"


def _series_members() -> Dict[str, str]:
    """template -> 所属系列 id（用于概览标记）。"""
    out: Dict[str, str] = {}
    if SERIES_DIR.is_dir():
        for path in sorted(SERIES_DIR.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            for item in data.get("members") or []:
                tid = str(item.get("template") or "").strip()
                if tid:
                    out[tid] = str(data.get("id") or path.stem)
    return out


def _stage_rows(wf: dict) -> list:
    return [{"id": s.get("id"), "agent": s.get("agent"), "tool": s.get("tool"),
             "depends": s.get("depends") or [], "enabled": s.get("enabled", True),
             "optional": s.get("optional", False), "desc": s.get("desc") or "",
             "params": s.get("params") or {}} for s in wf.get("stages") or []]


@router.get("/api/v6/agent/overview")
def overview() -> dict:
    cat = v5_catalog.load_catalog()
    series_of = _series_members()
    templates = []
    for tid, tpl in sorted((cat.get("templates") or {}).items()):
        wf = load_workflow(tid, tpl)
        templates.append({
            "id": tid, "label": tpl.get("label"), "style": tpl.get("style"),
            "render_type": _render_type(tid, tpl),
            "render_style": tpl.get("render_style"),
            "kind": "custom" if tid.startswith("custom_") else "base",
            "stack": wf.get("stack") or ("hq_shorts" if is_shorts(tid, tpl) else "v5_render"),
            "duration": tpl.get("target_duration") or tpl.get("duration"),
            "cast": tpl.get("cast") or [],
            "ready": bool(_final_of(tid)),
            "series": series_of.get(tid),
            "stages": _stage_rows(wf),
            # 仓库相对路径（P2-2）：给浏览器绝对路径等于泄漏盘符/用户名，且给路径穿越画地图
            "workflow_file": _rel(WORKFLOW_DIR / f"{tid}.json"),
        })
    return {"ok": True, "roles": list(ROLES.values()), "templates": templates,
            "count": len(templates),
            "series": _series_list(),
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
    return {"ok": True, "template": template, "path": _rel(path),
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
            # payload 在 start_run 里已经转换/校验过；这里再取一次兜底（只给内部调用方用）
            str(payload["template"]), duration=float(payload.get("duration") or 40),
            seed=int(payload.get("seed") or 20261101),
            force=_as_bool(payload.get("force")), stages=payload.get("stages"),
            start=payload.get("start"), goal=str(payload.get("goal") or ""),
            dry_run=_as_bool(payload.get("dry_run")),
            stage_params=payload.get("stage_params"), prog=prog)
        with _job_lock:
            job = _jobs[job_id]
            job["status"] = run.status
            job["ended"] = time.strftime("%Y-%m-%d %H:%M:%S")
            job["result"] = run.to_dict()
            job["run_dir"] = _rel(run.run_dir)
    except BaseException as exc:  # noqa: BLE001 - SystemExit 也要落 failed
        with _job_lock:
            job = _jobs[job_id]
            job["status"] = "failed"
            job["error"] = f"{type(exc).__name__}: {exc}"
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
    sp = payload.get("stage_params")
    if isinstance(sp, str) and sp.strip():
        try:
            payload["stage_params"] = json.loads(sp)
        except ValueError:
            raise HTTPException(status_code=400, detail="stage_params 必须是 JSON 对象")
    # P1-2：非法 duration/seed 曾在**提交后**才在线程里炸 → 前端先拿到 200 + job_id，
    # 紧接着 job 变 failed（错误体是 Python 内部异常串）。现在提交前校验，直接 422。
    try:
        payload["duration"] = float(payload.get("duration") or 40)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=422, detail=f"duration 必须是数字（收到 {payload.get('duration')!r}）"
        ) from exc
    try:
        payload["seed"] = int(payload.get("seed") or 20261101)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=422, detail=f"seed 必须是整数（收到 {payload.get('seed')!r}）"
        ) from exc
    # P2-7：bool("false") == True —— 请求体里的字符串布尔必须走严格转换
    payload["force"] = _as_bool(payload.get("force"))
    payload["dry_run"] = _as_bool(payload.get("dry_run"))
    with _job_lock:
        running = [j for j in _jobs.values()
                   if j.get("status") in ("queued", "running")]
        if running:
            raise HTTPException(status_code=409,
                                detail=f"已有 run 在跑: {running[0]['job_id']}")
        job_id = "job_" + uuid.uuid4().hex[:10]
        _remember_job(job_id)
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


# --------------------------------------------------------------------------- #
# V49 季（Season）：把一系列短片串成可生产的「一季」
# --------------------------------------------------------------------------- #
def _poster_of(template_id: str) -> Optional[str]:
    candidates = [root / template_id / name for root in MEDIA_DIRS
                  for name in ("poster.jpg", "poster.png")
                  if (root / template_id / name).is_file()]
    if not candidates:
        return None
    path = max(candidates, key=lambda p: p.stat().st_mtime)  # 与成片口径一致：取最新
    return f"/media/{path.parent.parent.name}/{template_id}/{path.name}"


def _series_paths() -> list:
    return sorted(SERIES_DIR.glob("*.json")) if SERIES_DIR.is_dir() else []


def _series_entry(sid: str) -> dict:
    # P1-1：不再 `SERIES_DIR / f"{sid}.json"`（`\\` 在 Windows 上是分隔符，
    # `..%5C..%5C..%5Ccontract%5Cdemo_episode` 会逃出 series 目录读任意 json）。
    # 双重闸门：check_id 白名单 + resolve_under containment，越界一律 400。
    ident = ids.check_id(sid, "系列 id")
    path = ids.resolve_under(SERIES_DIR, f"{ident}.json", "系列 id")
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"系列不存在: {ident}")
    data = json.loads(path.read_text(encoding="utf-8"))
    cat = v5_catalog.load_catalog()
    templates = cat.get("templates") or {}
    members = []
    for order, item in enumerate(data.get("members") or [], 1):
        tid = str(item.get("template") or "").strip()
        tpl = templates.get(tid) or {}
        runs = latest_runs(tid, 1)
        wf = load_workflow(tid, tpl)
        members.append({
            "order": order,
            "template": tid,
            "label": str(item.get("label") or tpl.get("label") or tid),
            "render_style": tpl.get("render_style"),
            "duration": tpl.get("target_duration") or tpl.get("duration"),
            "ready": bool(_final_of(tid)),
            "final_url": _final_of(tid),
            "poster_url": _poster_of(tid),
            "stages": len(wf.get("stages") or []),
            "stack": wf.get("stack") or ("hq_shorts" if is_shorts(tid, tpl) else "v5_render"),
            "last_run": ({"run_id": runs[0].get("run_id"), "status": runs[0].get("status"),
                          "started": runs[0].get("started")} if runs else None),
        })
    total = sum(float(m["duration"] or 0) for m in members) or None
    season = data.get("season") or {}
    rules = season.get("rules") or {}
    warnings: list = []
    if rules.get("same_cast"):
        casts = []
        for m in members:
            tpl_cast = templates.get(m["template"]) or {}
            casts.append({str(e.get("character")) for e in (tpl_cast.get("cast") or [])
                          if isinstance(e, dict)})
        if casts and not set.intersection(*casts):
            warnings.append("season.rules.same_cast=true，但各集 cast 没有交集")
    if rules.get("same_style"):
        styles = {(templates.get(m["template"]) or {}).get("render_style") for m in members}
        if len({s for s in styles if s}) > 1:
            warnings.append(f"season.rules.same_style=true，但各集 render_style 不同: {sorted(styles)}")
    target = int(season.get("target_count") or len(members) or 0)
    if target and len(members) != target:
        warnings.append(f"target_count={target} 与实际集数 {len(members)} 不一致")
    return {
        "id": data.get("id") or path.stem,
        "label": data.get("label") or path.stem,
        "description": data.get("description") or "",
        "style": data.get("style") or "",
        "cover_url": _poster_of(str(data.get("cover") or "")),
        "season": data.get("season"),
        "season_warnings": warnings,
        "members": members,
        "ready_count": sum(1 for m in members if m["ready"]),
        "total": len(members),
        "total_duration": round(total, 2) if total else None,
        "run_dir": _rel(RUN_ROOT),
    }


def _series_list() -> list:
    out = []
    for path in _series_paths():
        try:
            out.append(_series_entry(path.stem))
        except (OSError, ValueError, HTTPException):
            continue
    return out


@router.get("/api/v6/agent/series")
def agent_series() -> dict:
    items = _series_list()
    return {"ok": True, "count": len(items), "series": items}


def _season_worker(job_id: str, sid: str, payload: dict) -> None:
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
                if len(j["logs"]) > 900:
                    j["logs"] = j["logs"][-900:]

    season_root = RUN_ROOT / f"season_{sid}_{time.strftime('%Y%m%d_%H%M%S')}"
    season_root.mkdir(parents=True, exist_ok=True)
    events_path = season_root / "events.jsonl"

    def season_event(kind: str, **extra) -> None:
        rec = {"ts": time.strftime("%H:%M:%S"), "kind": kind, **extra}
        try:
            with events_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except OSError:
            pass

    season_event("season_start", season=sid, episodes=payload["episodes"])
    # 同一套严格布尔：季 payload 直接来自请求体，字符串 "false" 不能当 True
    force = _as_bool(payload.get("force"))
    dry_run = _as_bool(payload.get("dry_run"))
    with_muse = _as_bool(payload.get("with_muse"))
    results = []
    try:
        for tid in payload["episodes"]:
            prog(tid, f"开始本集：{tid}")
            try:
                run = run_template(
                    tid, duration=float(payload.get("duration") or 40),
                    seed=int(payload.get("seed") or 20261101),
                    force=force,
                    stages=payload.get("stages"),
                    goal=str(payload.get("goal") or f"{sid} 季生产"),
                    dry_run=dry_run,
                    stage_params=payload.get("stage_params"),
                    run_root=season_root,
                    prog=lambda stage, msg, _tid=tid: prog(_tid, f"[{stage}] {msg}"))
                results.append({"template": tid, "status": run.status,
                                "run_id": run.run_id, "run_dir": _rel(run.run_dir),
                                "stages": [{"id": s["id"], "status": s["status"]}
                                           for s in run.stages]})
                prog(tid, f"完成：{run.status}")
            except BaseException as exc:  # noqa: BLE001 - SystemExit 也要记失败
                results.append({"template": tid, "status": "failed",
                                "error": f"{type(exc).__name__}: {exc}"})
                prog(tid, f"失败：{exc}")
        failed = [r for r in results if r.get("status") == "failed"]
        # dry-run 只是计划，不能报 ok（否则“计划完成”会被当成“已出片”）
        status = "failed" if failed else ("planned" if dry_run else "ok")
        season_event("season_end", status=status, ok=len(results) - len(failed),
                     failed=[r.get("template") for r in failed])
        season_record = {"season": sid, "status": status,
                         "started": _jobs[job_id].get("started"),
                         "ended": time.strftime("%Y-%m-%d %H:%M:%S"),
                         "dry_run": dry_run,
                         "with_muse": with_muse,
                         "stages": payload.get("stages"),
                         "stage_params": payload.get("stage_params"),
                         "episodes": results}
        (season_root / "run.json").write_text(
            json.dumps(season_record, ensure_ascii=False, indent=2), encoding="utf-8")
        lines = [f"# Season Run {season_root.name}", "",
                 f"- 系列: {sid}", f"- 状态: **{status}**",
                 f"- 集数: {len(results)}（成功 {len(results) - len(failed)}）",
                 f"- dry_run: {dry_run}",
                 "", "| 集 | 状态 | run_id | 阶段 |", "|---|---|---|---|"]
        for item in results:
            stages_txt = ",".join(f"{s['id']}:{s['status']}" for s in item.get("stages") or [])
            lines.append(f"| {item.get('template')} | {item.get('status')} | "
                         f"{item.get('run_id') or item.get('error', '-')} | {stages_txt or '-'} |")
        (season_root / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        with _job_lock:
            job = _jobs[job_id]
            job["status"] = status
            job["ended"] = time.strftime("%Y-%m-%d %H:%M:%S")
            job["run_dir"] = _rel(season_root)
            job["result"] = {"season": sid, "episodes": results,
                             "dry_run": dry_run,
                             "season_run": season_root.name,
                             "summary_url": f"/media/agent_runs/{season_root.name}/summary.md",
                             "run_url": f"/media/agent_runs/{season_root.name}/run.json",
                             "events_url": f"/media/agent_runs/{season_root.name}/events.jsonl"}
    except BaseException as exc:  # noqa: BLE001 - SystemExit 也要落 failed
        with _job_lock:
            job = _jobs[job_id]
            job["status"] = "failed"
            job["error"] = f"{type(exc).__name__}: {exc}"
            job["ended"] = time.strftime("%Y-%m-%d %H:%M:%S")
    finally:
        with _job_lock:
            _current_job = None


@router.post("/api/v6/agent/season/{sid}/run")
def season_run(sid: str, payload: dict = Body(default=None)) -> dict:
    payload = dict(payload or {})
    series = _series_entry(sid)
    only_missing = _as_bool(payload.get("only_missing"), True)
    with_muse = _as_bool(payload.get("with_muse"))
    # P1-6 修复：季生产默认**永远** dry-run（安全）。旧代码把 `not with_muse` 当默认值，
    # 于是"只勾一个口型"就把整季翻成真跑（GPU 真烧、成片真覆盖）。要真跑必须显式
    # `dry_run: false`，与单集 run 的口径一致。
    dry_run = _as_bool(payload.get("dry_run"), True)
    warnings: list[str] = []
    if with_muse and not dry_run:
        warnings.append("with_muse + dry_run=false：会真跑 GPU 并把口型结果回写 shots/（原件在 shots/_orig/）")
    stages = payload.get("stages")
    if isinstance(stages, str):
        stages = [s.strip() for s in stages.split(",") if s.strip()]
    stage_params = payload.get("stage_params") or {}
    if isinstance(stage_params, str) and stage_params.strip():
        try:
            stage_params = json.loads(stage_params)
        except ValueError:
            raise HTTPException(status_code=400, detail="stage_params 必须是 JSON 对象")
    if _as_bool(payload.get("muse_apply")):
        stage_params = {**stage_params, "muse": {"apply": True, "reassemble": True}}
    # P1-2：季生产同样在**提交前**校验数字参数，别让非法值在线程里炸成一个死 job
    try:
        duration = float(payload.get("duration") or 40)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=422, detail=f"duration 必须是数字（收到 {payload.get('duration')!r}）"
        ) from exc
    try:
        seed = int(payload.get("seed") or 20261101)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=422, detail=f"seed 必须是整数（收到 {payload.get('seed')!r}）"
        ) from exc
    if with_muse:
        # 季批量口型：默认只跑 muse；用户显式给了 stages 就尊重它，只把 muse 补进去
        # （旧代码直接把 stages 覆盖成 ["muse"]，用户的 {stages:[...]} 被静默丢弃）。
        stages = list(stages) if stages else ["muse"]
        if "muse" not in stages:
            stages.append("muse")
        only_missing = False
        episodes = [m["template"] for m in series["members"] if m["template"]]
    else:
        episodes = [m["template"] for m in series["members"]
                    if (m["template"] and (not only_missing or not m["ready"]))]
    if not episodes:
        return {"ok": True, "job_id": None, "episodes": [],
                "message": "全部集数都有成片，无需生产"}
    with _job_lock:
        running = [j for j in _jobs.values()
                   if j.get("status") in ("queued", "running")]
        if running:
            raise HTTPException(status_code=409,
                                detail=f"已有 run 在跑: {running[0]['job_id']}")
        job_id = "season_" + uuid.uuid4().hex[:10]
        _jobs[job_id] = {
            "job_id": job_id, "status": "queued", "logs": [],
            "created": time.strftime("%Y-%m-%d %H:%M:%S"),
            "payload": {"season": sid, "episodes": episodes, "dry_run": dry_run,
                        "stages": stages, "only_missing": only_missing,
                        "with_muse": with_muse},
        }
    task = dict(payload)
    task.update({"episodes": episodes, "dry_run": dry_run, "stages": stages,
                 "stage_params": stage_params, "duration": duration, "seed": seed,
                 "force": _as_bool(payload.get("force")),
                 "with_muse": with_muse})
    threading.Thread(target=_season_worker, args=(job_id, sid, task),
                     daemon=True).start()
    return {"ok": True, "job_id": job_id, "episodes": episodes,
            "dry_run": dry_run, "with_muse": with_muse,
            "stages": stages, "only_missing": only_missing,
            "warnings": warnings}


@router.get("/api/v6/agent/season/{sid}/status")
def season_status(sid: str) -> dict:
    series = _series_entry(sid)
    episodes = []
    for m in series["members"]:
        episodes.append({**m, "missing": not m["ready"]})
    return {"ok": True, "series": series, "episodes": episodes}

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
                                         # P2-7：enabled=false("false") 曾被 bool() 判成 True
                                         "enabled": _as_bool(payload.get("enabled", True), True)})
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/media/agent_runs/{run_id}/{file}")
def agent_run_media(run_id: str, file: str) -> FileResponse:
    """run 证据文件（summary.md / run.json / events.jsonl ...）。

    P1-3：run_id 闸门与 `ids.SAFE_ID` **同源**（`backend/app/ids.py::RUN_ID_RE`，
    允许 1-80 字符、允许前导数字、允许下划线）。原实现写的是 `^[a-z0-9_]{8,40}$`，
    于是 46 字符 run_id（`20261106_233032_custom_tpl_v45_soft_watercolor`）的证据**永久 404**。
    containment 与三处合同 json 入口共用 `ids.resolve_under`。
    """
    ident = ids.check_run_id(run_id, "run_id")
    if file not in RUN_MEDIA:
        raise HTTPException(status_code=404, detail=f"不支持的证据文件: {file}")
    path = ids.resolve_under(RUN_ROOT, f"{ident}/{file}", "run 证据文件")
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"证据文件不存在: {ident}/{file}")
    return FileResponse(path)
