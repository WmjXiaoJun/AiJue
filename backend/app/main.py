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

from . import character_files, config, jobs, models, schemas, seed
from .db import SessionLocal, get_db, init_db

logger = logging.getLogger(__name__)

VERSION = config.VERSION
SHOT_STATUSES = {"pending", "queued", "generating", "ready", "failed"}
ASSET_ROLES = {"host", "guest"}
ASSET_TYPES = {"character", "scene", "prop", "style"}
STYLE_PRESETS = {"3d_toon", "3d_cel", "2d_cel", "2d_paint", "anime", "guoman"}
DEFAULT_STYLE = "3d_toon"

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
            if job.character_id:
                character = db.get(models.Character, job.character_id)
                if character is not None and character.status == "generating":
                    character.status = "failed"
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


def _normalize_style(raw: Any) -> Dict[str, Any]:
    """把 style 规范成 {primary, variants} 对象。"""
    if isinstance(raw, str) and raw.strip():
        return {"primary": raw.strip(), "variants": [raw.strip()]}
    if isinstance(raw, dict):
        primary = str(raw.get("primary") or "").strip()
        variants = [str(item).strip() for item in (raw.get("variants") or []) if str(item).strip()]
        if not primary:
            primary = variants[0] if variants else DEFAULT_STYLE
        if primary not in variants:
            variants.insert(0, primary)
        return {"primary": primary, "variants": list(dict.fromkeys(variants))}
    return {"primary": DEFAULT_STYLE, "variants": [DEFAULT_STYLE]}


def _is_color(value: Any) -> bool:
    text = str(value or "")
    return len(text) == 7 and text.startswith("#") and all(ch in "0123456789abcdefABCDEF" for ch in text[1:])


def _first_episode(db: Session, project: models.Project) -> models.Episode:
    """项目下第一集（角色资产挂到该集；新项目创建时已自动带第 1 集）。"""
    episode = (
        db.query(models.Episode)
        .filter(models.Episode.project_id == project.id)
        .order_by(models.Episode.no.asc())
        .first()
    )
    if episode is not None:
        return episode
    setting = seed.get_settings(db)
    fmt = _load_format(project.format_id)
    episode = models.Episode(
        id=models.new_id("ep"),
        project_id=project.id,
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
    db.add(episode)
    db.commit()
    return episode


def _sync_character_asset(db: Session, character: models.Character) -> bool:
    """角色字段/半身像同步到关联 Asset（供资产库/分镜台引用）。"""
    if not character.asset_id:
        return False
    asset = db.get(models.Asset, character.asset_id)
    if asset is None:
        return False
    changed = False
    for field in ("name", "title", "persona", "voice", "accent_color"):
        value = getattr(character, field)
        if getattr(asset, field) != value:
            setattr(asset, field, value)
            changed = True
    expected = character.portrait_url or character_files.expected_portrait_url(character.id)
    if list(asset.images or []) != [expected]:
        asset.images = [expected]
        changed = True
    return changed


def _sync_character(db: Session, character: models.Character, commit: bool = True) -> bool:
    """懒同步：磁盘产物 -> Character URL/status，并保持 Asset 一致。"""
    changed = character_files.apply(character)
    changed = _sync_character_asset(db, character) or changed
    if changed and commit:
        db.commit()
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
# 角色工坊（contract/CHARACTER.md）
# --------------------------------------------------------------------------- #
def _character_spec_from_payload(payload: schemas.CharacterCreate):
    """兼容 {spec:{...}} 包裹与扁平对象；返回 (spec, provided_id)。"""
    spec = dict(payload.spec) if isinstance(payload.spec, dict) else {}
    flat: Dict[str, Any] = {}
    for field in ("name", "title", "persona", "voice", "accent_color", "style", "appearance", "role"):
        value = getattr(payload, field, None)
        if value is not None:
            flat[field] = value
    for key, value in (payload.model_extra or {}).items():
        if key not in flat:
            flat[key] = value
    provided_id = None
    if spec:
        provided_id = spec.get("id")
        spec.update(flat)
        return spec, provided_id
    if flat:
        return flat, None
    return None, None


def _character_fields_from_spec(spec: Dict[str, Any], character_id: str) -> Dict[str, Any]:
    name = str(spec.get("name") or character_id).strip() or character_id
    title = str(spec.get("title") or "")
    persona = str(spec.get("persona") or "")
    voice = str(spec.get("voice") or "")
    accent_color = str(spec.get("accent_color") or "#888888")
    if not _is_color(accent_color):
        raise HTTPException(status_code=400, detail="accent_color 必须是 #RRGGBB 格式")
    style = _normalize_style(spec.get("style"))
    if style["primary"] not in STYLE_PRESETS:
        raise HTTPException(
            status_code=400,
            detail=f"style.primary 必须是 {sorted(STYLE_PRESETS)} 之一",
        )
    spec["id"] = character_id
    spec["name"] = name
    spec.setdefault("title", title)
    spec.setdefault("persona", persona)
    spec.setdefault("voice", voice)
    spec.setdefault("accent_color", accent_color)
    spec["style"] = style
    return {
        "name": name,
        "title": title,
        "persona": persona,
        "voice": voice,
        "accent_color": accent_color,
        "style": style,
    }


@router.get("/projects/{pid}/characters", response_model=List[schemas.CharacterOut])
def list_characters(pid: str, db: Session = Depends(get_db)):
    _get_or_404(db, models.Project, pid, "项目")
    characters = (
        db.query(models.Character)
        .filter(models.Character.project_id == pid)
        .order_by(models.Character.created_at.asc())
        .all()
    )
    changed = False
    for character in characters:
        changed = _sync_character(db, character, commit=False) or changed
    if changed:
        db.commit()
    return characters


@router.post("/projects/{pid}/characters", response_model=schemas.CharacterOut)
def create_character(
    pid: str,
    payload: schemas.CharacterCreate,
    db: Session = Depends(get_db),
):
    project = _get_or_404(db, models.Project, pid, "项目")
    spec, provided_id = _character_spec_from_payload(payload)
    if not spec:
        raise HTTPException(
            status_code=400,
            detail="缺少角色 spec：请传扁平对象或 {spec:{...}}",
        )
    character_id = str(provided_id or "").strip() or models.new_id("char")
    if db.get(models.Character, character_id) is not None:
        raise HTTPException(status_code=409, detail=f"角色 id 已存在: {character_id}")

    fields = _character_fields_from_spec(spec, character_id)
    episode = _first_episode(db, project)
    role = spec.get("role") if spec.get("role") in ASSET_ROLES else "guest"
    portrait_url = character_files.expected_portrait_url(character_id)
    asset = models.Asset(
        id=models.new_id("asset"),
        episode_id=episode.id,
        role=role,
        type="character",
        name=fields["name"],
        title=fields["title"],
        persona=fields["persona"],
        voice=fields["voice"],
        accent_color=fields["accent_color"],
        images=[portrait_url],
        locked=False,
    )
    db.add(asset)
    db.flush()

    character = models.Character(
        id=character_id,
        project_id=project.id,
        asset_id=asset.id,
        name=fields["name"],
        title=fields["title"],
        persona=fields["persona"],
        voice=fields["voice"],
        accent_color=fields["accent_color"],
        style=fields["style"],
        spec=spec,
        portrait_url=None,
        sheet_url=None,
        turntable_url=None,
        expressions={},
        status="pending",
    )
    db.add(character)
    db.commit()
    _sync_character(db, character)
    return character


@router.get("/characters/{cid}", response_model=schemas.CharacterOut)
def get_character(cid: str, db: Session = Depends(get_db)):
    character = _get_or_404(db, models.Character, cid, "角色")
    _sync_character(db, character)
    return character


@router.patch("/characters/{cid}", response_model=schemas.CharacterOut)
def patch_character(
    cid: str,
    payload: schemas.CharacterPatch,
    db: Session = Depends(get_db),
):
    character = _get_or_404(db, models.Character, cid, "角色")
    changes = payload.model_dump(exclude_unset=True)
    spec_update = changes.pop("spec", None)

    if spec_update is not None:
        merged = dict(character.spec or {})
        merged.update(spec_update)
        merged["id"] = character.id
        character.spec = merged
        derived_style = _normalize_style(merged.get("style"))
        if derived_style["primary"] not in STYLE_PRESETS:
            raise HTTPException(
                status_code=400,
                detail=f"style.primary 必须是 {sorted(STYLE_PRESETS)} 之一",
            )
        character.style = derived_style
        for field in ("name", "title", "persona", "voice", "accent_color"):
            if changes.get(field) is None and merged.get(field) is not None:
                setattr(character, field, merged[field])

    for field in ("name", "title", "persona", "voice", "accent_color"):
        value = changes.get(field)
        if value is None:
            continue
        if field == "accent_color" and not _is_color(value):
            raise HTTPException(status_code=400, detail="accent_color 必须是 #RRGGBB 格式")
        setattr(character, field, str(value))

    _sync_character_asset(db, character)
    db.commit()
    return character


@router.post("/characters/{cid}/generate", response_model=schemas.JobOut)
def generate_character(
    cid: str,
    payload: Optional[schemas.CharacterGenerateRequest] = None,
    db: Session = Depends(get_db),
):
    character = _get_or_404(db, models.Character, cid, "角色")
    styles = payload.styles if payload else []
    invalid = [style for style in styles if style not in STYLE_PRESETS]
    if invalid:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的 style: {invalid}；可选 {sorted(STYLE_PRESETS)}",
        )
    job, _created = jobs.enqueue_character_job(db, character, styles)
    return job


@router.get("/characters/{cid}/files", response_model=schemas.CharacterFilesOut)
def get_character_files(cid: str, db: Session = Depends(get_db)):
    character = _get_or_404(db, models.Character, cid, "角色")
    _sync_character(db, character)
    data = character_files.scan(character.id)
    return {
        "portrait": character.portrait_url,
        "sheet": character.sheet_url,
        "turntable": character.turntable_url,
        "expressions": dict(character.expressions or {}),
        "meta": data.get("meta") or {},
    }


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
