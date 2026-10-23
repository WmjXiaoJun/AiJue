"""造片局 · AI 微综艺 demo 后端入口。

启动：
    python -m uvicorn app.main:app --port 8090 --app-dir backend

- 启动前建表；空库时从 contract/demo_episode.json 播种 demo 数据；
- /api 下实现 contract/API.md 全部端点；
- /media 静态挂载仓库 outputs/；frontend/dist 存在时 / 挂载前端并做 SPA fallback。
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func
from sqlalchemy.orm import Session

from . import config, jobs, models, schemas, seed
from .db import SessionLocal, get_db, init_db

logger = logging.getLogger(__name__)

VERSION = config.VERSION
SHOT_STATUSES = {"pending", "queued", "generating", "ready", "failed"}
ASSET_ROLES = {"host", "guest"}
ASSET_TYPES = {"character", "scene", "prop", "style"}

# 导入即建表 + 播种（幂等），保证直接 uvicorn 启动即可用
init_db()


def _recover_stale_jobs() -> None:
    """服务重启后，把上一个进程遗留的 queued/running 任务标记为 failed（可 retry）。

    demo 用进程内线程执行任务，进程退出后线程即消失；不做恢复会让前端一直看到
    幽灵任务。标记 failed 并保留 error 语义，用户可点 retry 重新入队。
    """
    with SessionLocal() as db:
        stale = (
            db.query(models.Job)
            .filter(models.Job.status.in_(("queued", "running")))
            .all()
        )
        if not stale:
            return
        for job in stale:
            job.status = "failed"
            job.error = "服务重启，任务中断，可点重试"
            job.message = "服务重启，任务中断"
            job.updated_at = models.utcnow()
            if job.shot_id:
                shot = db.get(models.Shot, job.shot_id)
                if shot is not None and shot.status in ("queued", "generating"):
                    shot.status = "failed"
            if job.episode_id:
                episode = db.get(models.Episode, job.episode_id)
                if episode is not None and episode.status == "rendering":
                    episode.status = "failed"
        db.commit()
        logger.warning("启动恢复：%d 个未完成任务已标记 failed（可重试）", len(stale))


_recover_stale_jobs()

app = FastAPI(title=config.APP_NAME, version=VERSION)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # demo 无鉴权，允许前端 5173 直连
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

router = APIRouter(prefix=config.API_PREFIX)


# --------------------------------------------------------------------------- #
# 工具
# --------------------------------------------------------------------------- #
def _get_or_404(db: Session, model: Any, ident: str, label: str):
    obj = db.get(model, ident)
    if obj is None:
        raise HTTPException(status_code=404, detail=f"{label}不存在: {ident}")
    return obj


def _sync_episode_counts(db: Session, episode: models.Episode) -> bool:
    """按数据库真实状态懒重算集的 shots_total/shots_done/progress。

    并发 shot 任务可能各自基于旧快照写聚合列，写侧重算不保证最终一致；
    读接口每次以 COUNT 真实重算并持久化，保证前端看到的数字永远是对的。
    """
    total = (
        db.query(func.count(models.Shot.id))
        .filter(models.Shot.episode_id == episode.id)
        .scalar()
        or 0
    )
    done = (
        db.query(func.count(models.Shot.id))
        .filter(
            models.Shot.episode_id == episode.id,
            models.Shot.selected_version_id.isnot(None),
        )
        .scalar()
        or 0
    )
    total, done = int(total), int(done)
    if episode.render_url:
        progress = 100.0
    elif total:
        progress = round(done * 100.0 / total, 1)
    else:
        progress = 0.0
    changed = (
        episode.shots_total != total
        or episode.shots_done != done
        or float(episode.progress or 0.0) != progress
    )
    episode.shots_total = total
    episode.shots_done = done
    episode.progress = progress
    return changed


def _load_format(format_id: str) -> Dict[str, Any]:
    path = config.FORMATS_DIR / f"{format_id}.json"
    if path.is_file():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            logger.exception("格式文件解析失败: %s", path)
    return {}


def _create_skeleton_shots(db: Session, episode: models.Episode) -> int:
    """按格式定义给空集生成骨架分镜（demo 集已有 52 个镜头，不会走到这里）。"""
    format_id = "roundtable_5min"
    if episode.project is not None:
        format_id = episode.project.format_id or format_id
    spec = _load_format(format_id)
    segments = spec.get("segments") or []
    if not segments:
        try:
            segments = seed.load_demo_episode().get("segments") or []
        except Exception:  # noqa: BLE001 - 无样例时生成空分镜
            segments = []
    created = 0
    for segment in segments:
        pattern = segment.get("pattern") or []
        shot_type = pattern[0] if pattern else "host_to_camera"
        if segment.get("id") == "open":
            shot_type = "title_card"
        elif segment.get("id") == "closing":
            shot_type = "end_card"
        created += 1
        db.add(
            models.Shot(
                id=models.new_id("s"),
                episode_id=episode.id,
                segment_id=str(segment.get("id") or ""),
                no=created,
                type=shot_type,
                duration=float(segment.get("duration") or 5.0),
                speaker=None,
                speaker_name="",
                line="",
                visual=str(segment.get("goal") or segment.get("name") or ""),
                lower_third=None,
                punchline="",
                sfx="",
                mood="",
                status="pending",
                selected_version_id=None,
                title=episode.title if shot_type == "title_card" else None,
                subtitle=None,
            )
        )
    episode.shots_total = created
    episode.shots_done = 0
    db.commit()
    return created


# --------------------------------------------------------------------------- #
# 基础
# --------------------------------------------------------------------------- #
@router.get("/health", response_model=schemas.HealthOut)
def health(db: Session = Depends(get_db)):
    mode = "mock"
    try:
        mode = seed.get_settings(db).generation_mode or "mock"
    except Exception:  # noqa: BLE001 - 健康检查永远可用
        logger.exception("读取设置失败，mode 回落 mock")
    return {"status": "ok", "version": VERSION, "mode": mode}


# --------------------------------------------------------------------------- #
# 项目 / 集
# --------------------------------------------------------------------------- #
@router.get("/projects", response_model=List[schemas.ProjectOut])
def list_projects(db: Session = Depends(get_db)):
    return db.query(models.Project).order_by(models.Project.created_at.asc()).all()


@router.post("/projects", response_model=schemas.ProjectOut)
def create_project(payload: schemas.ProjectCreate, db: Session = Depends(get_db)):
    setting = seed.get_settings(db)
    fmt = _load_format(payload.format_id)
    project = models.Project(
        id=models.new_id("proj"),
        name=payload.name.strip(),
        format_id=payload.format_id,
        aspect=payload.aspect or "16:9",
        status="draft",
        budget=5000.0,
        spent=0.0,
        cover=None,
    )
    episode = models.Episode(
        id=models.new_id("ep"),
        no=1,
        title=f"{project.name} · 第 1 期",
        target_duration=int(fmt.get("target_duration") or 296),
        status="draft",
        progress=0.0,
        shots_total=0,
        shots_done=0,
        cost=0.0,
        aspect=project.aspect,
        resolution=setting.resolution or "1280x720",
        fps=int(setting.fps or 25),
        packaging=fmt.get("packaging") or {},
    )
    episode.project = project
    db.add(project)
    db.add(episode)
    db.commit()
    return project


@router.get("/projects/{pid}", response_model=schemas.ProjectOut)
def get_project(pid: str, db: Session = Depends(get_db)):
    return _get_or_404(db, models.Project, pid, "项目")


@router.get("/projects/{pid}/episodes", response_model=List[schemas.EpisodeOut])
def list_project_episodes(pid: str, db: Session = Depends(get_db)):
    _get_or_404(db, models.Project, pid, "项目")
    episodes = (
        db.query(models.Episode)
        .filter(models.Episode.project_id == pid)
        .order_by(models.Episode.no.asc())
        .all()
    )
    changed = False
    for episode in episodes:
        changed = _sync_episode_counts(db, episode) or changed
    if changed:
        db.commit()
    return episodes


@router.get("/episodes/{eid}", response_model=schemas.EpisodeOut)
def get_episode(eid: str, db: Session = Depends(get_db)):
    episode = _get_or_404(db, models.Episode, eid, "集")
    if _sync_episode_counts(db, episode):
        db.commit()
    return episode


@router.patch("/episodes/{eid}", response_model=schemas.EpisodeOut)
def patch_episode(eid: str, payload: schemas.EpisodeUpdate, db: Session = Depends(get_db)):
    episode = _get_or_404(db, models.Episode, eid, "集")
    for key, value in payload.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(episode, key, value)
    db.commit()
    return episode


# --------------------------------------------------------------------------- #
# 资产
# --------------------------------------------------------------------------- #
@router.get("/episodes/{eid}/assets", response_model=List[schemas.AssetOut])
def list_assets(eid: str, db: Session = Depends(get_db)):
    _get_or_404(db, models.Episode, eid, "集")
    return (
        db.query(models.Asset)
        .filter(models.Asset.episode_id == eid)
        .order_by(models.Asset.created_at.asc())
        .all()
    )


@router.post("/episodes/{eid}/assets", response_model=schemas.AssetOut)
def create_asset(eid: str, payload: schemas.AssetCreate, db: Session = Depends(get_db)):
    _get_or_404(db, models.Episode, eid, "集")
    if payload.role not in ASSET_ROLES:
        raise HTTPException(status_code=400, detail=f"role 只能是 {sorted(ASSET_ROLES)}")
    if payload.type not in ASSET_TYPES:
        raise HTTPException(status_code=400, detail=f"type 只能是 {sorted(ASSET_TYPES)}")
    asset_id = payload.id or models.new_id("asset")
    if db.get(models.Asset, asset_id) is not None:
        raise HTTPException(status_code=409, detail=f"资产 id 已存在: {asset_id}")
    data = payload.model_dump(exclude={"id"})
    asset = models.Asset(id=asset_id, episode_id=eid, **data)
    db.add(asset)
    db.commit()
    return asset


@router.patch("/assets/{aid}", response_model=schemas.AssetOut)
def patch_asset(aid: str, payload: schemas.AssetUpdate, db: Session = Depends(get_db)):
    asset = _get_or_404(db, models.Asset, aid, "资产")
    for key, value in payload.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(asset, key, value)
    db.commit()
    return asset


# --------------------------------------------------------------------------- #
# 镜头 / 版本
# --------------------------------------------------------------------------- #
@router.get("/episodes/{eid}/shots", response_model=List[schemas.ShotOut])
def list_shots(eid: str, db: Session = Depends(get_db)):
    _get_or_404(db, models.Episode, eid, "集")
    return (
        db.query(models.Shot)
        .filter(models.Shot.episode_id == eid)
        .order_by(models.Shot.no.asc())
        .all()
    )


@router.patch("/shots/{sid}", response_model=schemas.ShotOut)
def patch_shot(sid: str, payload: schemas.ShotUpdate, db: Session = Depends(get_db)):
    shot = _get_or_404(db, models.Shot, sid, "镜头")
    changes = payload.model_dump(exclude_unset=True)
    if "status" in changes and changes["status"] not in SHOT_STATUSES:
        raise HTTPException(status_code=400, detail=f"status 只能是 {sorted(SHOT_STATUSES)}")
    for key, value in changes.items():
        if value is not None:
            setattr(shot, key, value)
    db.commit()
    return shot


@router.post("/shots/{sid}/generate", response_model=schemas.JobOut)
def generate_shot(
    sid: str,
    payload: Optional[schemas.ShotGenerateRequest] = None,
    db: Session = Depends(get_db),
):
    shot = _get_or_404(db, models.Shot, sid, "镜头")
    params = payload.params if payload else {}
    job, _created = jobs.enqueue_shot_job(db, shot, params)
    return job


@router.get("/shots/{sid}/versions", response_model=List[schemas.ShotVersionOut])
def list_shot_versions(sid: str, db: Session = Depends(get_db)):
    _get_or_404(db, models.Shot, sid, "镜头")
    return (
        db.query(models.ShotVersion)
        .filter(models.ShotVersion.shot_id == sid)
        .order_by(models.ShotVersion.version.asc())
        .all()
    )


@router.post("/shots/{sid}/versions/{vid}/select", response_model=schemas.ShotOut)
def select_shot_version(sid: str, vid: str, db: Session = Depends(get_db)):
    shot = _get_or_404(db, models.Shot, sid, "镜头")
    version = db.get(models.ShotVersion, vid)
    if version is None or version.shot_id != sid:
        raise HTTPException(status_code=404, detail=f"镜头版本不存在: {vid}")
    shot.selected_version_id = version.id
    if version.status == "ready":
        shot.status = "ready"
    db.commit()
    episode = db.get(models.Episode, shot.episode_id)
    jobs._refresh_episode_stats(db, episode)
    db.commit()
    return shot


# --------------------------------------------------------------------------- #
# 分镜 / 渲染
# --------------------------------------------------------------------------- #
@router.post("/episodes/{eid}/storyboard/generate", response_model=schemas.StoryboardResult)
def generate_storyboard(eid: str, db: Session = Depends(get_db)):
    episode = _get_or_404(db, models.Episode, eid, "集")
    existing = db.query(models.Shot).filter(models.Shot.episode_id == eid).count()
    if existing:
        return {"created": 0}
    return {"created": _create_skeleton_shots(db, episode)}


@router.post("/episodes/{eid}/render", response_model=schemas.JobOut)
def render_episode(
    eid: str,
    payload: Optional[schemas.RenderRequest] = None,
    db: Session = Depends(get_db),
):
    episode = _get_or_404(db, models.Episode, eid, "集")
    params = payload.params if payload else {}
    job, _created = jobs.enqueue_render_job(db, episode, params)
    return job


# --------------------------------------------------------------------------- #
# 任务
# --------------------------------------------------------------------------- #
@router.get("/jobs", response_model=List[schemas.JobOut])
def list_jobs(
    episode_id: Optional[str] = None,
    type: Optional[str] = None,
    status: Optional[str] = None,
    db: Session = Depends(get_db),
):
    query = db.query(models.Job)
    if episode_id:
        query = query.filter(models.Job.episode_id == episode_id)
    if type:
        query = query.filter(models.Job.type == type)
    if status:
        query = query.filter(models.Job.status == status)
    return query.order_by(models.Job.created_at.desc()).limit(200).all()


@router.get("/jobs/{jid}", response_model=schemas.JobOut)
def get_job(jid: str, db: Session = Depends(get_db)):
    return _get_or_404(db, models.Job, jid, "任务")


@router.post("/jobs/{jid}/retry", response_model=schemas.JobOut)
def retry_job(jid: str, db: Session = Depends(get_db)):
    job = _get_or_404(db, models.Job, jid, "任务")
    job, _created = jobs.retry_job(db, job)
    return job


# --------------------------------------------------------------------------- #
# 费用 / 设置
# --------------------------------------------------------------------------- #
@router.get("/episodes/{eid}/costs", response_model=schemas.CostsOut)
def episode_costs(eid: str, db: Session = Depends(get_db)):
    _get_or_404(db, models.Episode, eid, "集")
    rows = (
        db.query(models.Job)
        .filter(models.Job.episode_id == eid, models.Job.status == "done")
        .order_by(models.Job.created_at.asc())
        .all()
    )
    items: List[Dict[str, Any]] = []
    by_type: Dict[str, float] = {}
    for row in rows:
        amount = round(float(row.cost or 0.0), 4)
        if row.type == "shot":
            label = f"{row.shot_id or '镜头'} · 镜头生成"
        elif row.type == "render":
            label = "成片渲染"
        else:
            label = row.type
        items.append({"label": label, "amount": amount})
        by_type[row.type] = round(by_type.get(row.type, 0.0) + amount, 4)
    total = round(sum(by_type.values()), 4)
    return {"currency": "CNY", "total": total, "by_type": by_type, "items": items}


@router.get("/settings", response_model=schemas.SettingsOut)
def get_settings(db: Session = Depends(get_db)):
    return seed.get_settings(db)


@router.patch("/settings", response_model=schemas.SettingsOut)
def patch_settings(payload: schemas.SettingsUpdate, db: Session = Depends(get_db)):
    setting = seed.get_settings(db)
    changes = payload.model_dump(exclude_unset=True)
    if changes.get("generation_mode") is not None and changes["generation_mode"] not in ("mock", "comfyui"):
        raise HTTPException(status_code=400, detail="generation_mode 只能是 mock 或 comfyui")
    for key, value in changes.items():
        if value is not None:
            setattr(setting, key, value)
    db.commit()
    db.refresh(setting)
    return setting


# --------------------------------------------------------------------------- #
# SSE（可选；前端默认轮询）
# --------------------------------------------------------------------------- #
@router.get("/events")
async def events(episode_id: Optional[str] = None):
    async def event_stream():
        yield "retry: 2000\n\n"
        for _ in range(150):
            payload_json = "{}"
            try:
                with SessionLocal() as db:
                    query = db.query(models.Job)
                    if episode_id:
                        query = query.filter(models.Job.episode_id == episode_id)
                    rows = query.order_by(models.Job.created_at.desc()).limit(20).all()
                    payload_json = json.dumps(
                        {
                            "jobs": [
                                schemas.JobOut.model_validate(row).model_dump(mode="json")
                                for row in rows
                            ]
                        },
                        ensure_ascii=False,
                    )
            except Exception:  # noqa: BLE001 - SSE 断流不 crash
                logger.exception("SSE 查询失败")
            yield f"data: {payload_json}\n\n"
            await asyncio.sleep(2)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# --------------------------------------------------------------------------- #
# 静态资源 & 前端 SPA
# --------------------------------------------------------------------------- #
app.include_router(router)

config.OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
app.mount(
    config.MEDIA_URL_PREFIX,
    StaticFiles(directory=str(config.OUTPUTS_DIR)),
    name="media",
)

PLACEHOLDER_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <title>造片局 · AI 微综艺 API</title>
  <style>
    body { background: #0f1115; color: #e8e8ea; font-family: system-ui, sans-serif; padding: 48px; }
    a { color: #f5c518; }
    code { background: #1b1e26; padding: 2px 6px; border-radius: 4px; }
  </style>
</head>
<body>
  <h1>造片局 · AI 微综艺</h1>
  <p>后端 API 已就绪（前端构建产物 frontend/dist 尚未找到，构建后前端将由本服务托管）。</p>
  <ul>
    <li><a href="/docs">OpenAPI 文档 /docs</a></li>
    <li><a href="/api/health">GET /api/health</a></li>
    <li><a href="/api/projects">GET /api/projects</a></li>
  </ul>
</body>
</html>"""


class SPAStaticFiles(StaticFiles):
    """静态托管前端构建产物；未命中的路径回落 index.html（SPA fallback）。"""

    async def get_response(self, path: str, scope):  # noqa: ANN001
        from starlette.exceptions import HTTPException as StarletteHTTPException

        try:
            response = await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code == 404 and not str(path).startswith("api"):
                return await super().get_response("index.html", scope)
            raise
        if response.status_code == 404 and not str(path).startswith("api"):
            return await super().get_response("index.html", scope)
        return response


if config.FRONTEND_DIST.is_dir() and (config.FRONTEND_DIST / "index.html").is_file():
    app.mount(
        "/",
        SPAStaticFiles(directory=str(config.FRONTEND_DIST), html=True),
        name="frontend",
    )
else:

    @app.get("/", include_in_schema=False)
    def index_placeholder():
        return HTMLResponse(PLACEHOLDER_HTML)
