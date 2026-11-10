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
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Body, Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func
from sqlalchemy.orm import Session

from . import (
    character_files,
    concept_files,
    config,
    ids,
    jobs,
    llm,
    models,
    schemas,
    script_parser,
    seed,
)
from .db import SessionLocal, get_db, init_db
from .routers import character_audition as character_audition_router
from .routers import character_dialogue as character_dialogue_router
from .routers import character_promo as character_promo_router
from .routers import character_showcase as character_showcase_router
from .routers import cinema3d as cinema3d_router
from .routers import cinema_templates as cinema_templates_router
from .routers import pipeline_ui as pipeline_ui_router
from .routers import publish_batches as publish_batches_router
from .routers import v5_catalog as v5_catalog_router
from .routers import v5_create as v5_create_router
from .routers import v5_series as v5_series_router
from .routers import v5_studio as v5_studio_router
from .routers import v6_agent as v6_agent_router
from .routers import longform as longform_router
from .routers import muse as muse_router

logger = logging.getLogger(__name__)

VERSION = config.VERSION
# 导入期"上次进程退出 -> 把 queued/running 标 failed"的开关（审查 P1-3）。
# `uvicorn --reload` 每次保存都会重新 import；不关就会把仍在跑的 GPU 任务判成 failed。
# backend/run.ps1 默认已不带 --reload，显式 -Reload 时自动置 ZAOPIANJU_JOBS_RECOVER=0。
JOBS_RECOVER = ids.env_flag("ZAOPIANJU_JOBS_RECOVER", True)

# 隐私档位映射的唯一来源在 publish/upload_post.py（纯函数，不联网）。
# 这里只做"预检/台账预览"的引用：映射规则绝不在 backend 里抄第二份。
_pub_privacy_levels = None
try:  # publish/ 在仓库根；uvicorn --app-dir backend 时 cwd 就是仓库根
    if str(config.REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(config.REPO_ROOT))
    from publish.upload_post import _privacy_levels as _pub_privacy_levels  # noqa: E402
except Exception:  # noqa: BLE001 - 只影响预检提示，不影响发布主流程
    _pub_privacy_levels = None


def _privacy_levels_preview(platforms: List[str], privacy: str) -> Dict[str, str]:
    """每个平台**实际会用**的隐私档位（只读预览；不抛"拒发"，加载失败就返回空）。"""
    if _pub_privacy_levels is None:
        return {}
    try:
        return _pub_privacy_levels(platforms, privacy, allow_public_fallback=True)
    except Exception:  # noqa: BLE001
        logger.warning("privacy_levels 预览失败（不影响发布）", exc_info=True)
        return {}


def _url_host(url: str) -> str:
    """URL 的 host（小写）；解析失败返回空串。用于判断 LLM 地址是否换了服务商。"""
    try:
        return (urlsplit(str(url or "")).hostname or "").lower()
    except ValueError:
        return ""


def _privacy_warning(privacy: str, levels: Dict[str, str]) -> str:
    """给 dry_run / 台账用的一句人话：哪些平台会落到"仅互关可见"。"""
    risky = {p: lv for p, lv in (levels or {}).items() if not p.startswith("youtube")}
    if privacy != "unlisted" or not risky:
        return ""
    parts = "、".join(f"{p}={lv}" for p, lv in risky.items())
    return f"unlisted 在 {parts}（仅互关可见）：真实发布默认拒发，需显式 allow_public_fallback"
SHOT_STATUSES = {"pending", "queued", "generating", "ready", "failed"}
SHOT_TYPES = {
    "title_card",
    "host_to_camera",
    "guest_intro",
    "shot_reverse",
    "reaction",
    "b_roll",
    "meme_card",
    "end_card",
}
ASSET_ROLES = {"host", "guest"}
ASSET_TYPES = {"character", "scene", "prop", "style"}
# 角色/资产 id 只允许小写字母开头 + 数字下划线；防止 .. \ 路径逃逸
CHAR_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,60}$")
STYLE_PRESETS = {"3d_toon", "3d_cel", "2d_cel", "2d_paint", "anime", "guoman"}
DEFAULT_STYLE = "3d_toon"
# 多语种本地化支持语言（MVP en/es，另支持 ja/ko/pt；fr/de/ar 预留）
SUPPORTED_LANGS = ["en", "es", "ja", "ko", "pt"]
# 项目画幅（PATCH /projects/{pid} 校验；与前端工作台可选项一致）
PROJECT_ASPECTS = ("16:9", "9:16", "1:1")
# 一键发布（contract/PUBLISHING.md）
PUBLISH_PLATFORMS = ["youtube", "tiktok", "instagram"]
PUBLISH_LANGS = ["zh", "en", "es", "ja", "ko", "pt"]
DEFAULT_PUBLISH_HASHTAGS = ["#造片局", "#AI微综艺", "#Zaopianju", "#AIVariety", "#AIGenerated"]
# 发布前检查：标题建议长度 / 本地化语言中文名
PUBLISH_TITLE_MAX_CHARS = 100
LOCALIZATION_LANG_LABELS = {"en": "英语", "es": "西语", "ja": "日语", "ko": "韩语", "pt": "葡语"}
# 发布前「社交账号授权」检查（Upload-Post 只读 GET；3s 超时，失败不阻断发布）
UPLOAD_POST_USERS_URL = "https://api.upload-post.com/api/uploadposts/users"
UPLOAD_POST_ACCOUNTS_TIMEOUT_SECONDS = 3.0
UPLOAD_POST_PLATFORM_LABELS = {"youtube": "YouTube", "tiktok": "TikTok", "instagram": "Instagram"}
# LLM 台本生成 JSON Schema（附带 example 供 mock provider 直接使用）
SCRIPT_JSON_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "required": ["title", "segments"],
    "example": {
        "title": "AI 会取代综艺导演吗？",
        "segments": [
            {
                "name": "开场",
                "lines": [
                    {"speaker": "老周", "text": "今天这期，我们聊一个得罪人的话题。"},
                    {"speaker": "阿栗", "text": "AI 现在能写台本、能剪片，还能自己找梗。"},
                ],
            },
            {
                "name": "正片",
                "lines": [
                    {"speaker": "大魏", "text": "机器先学会让人笑，再谈取代。"},
                    {"speaker": "老周", "text": "所以你上一次被 AI 逗笑是什么时候？"},
                ],
            },
            {
                "name": "结尾",
                "lines": [{"speaker": "老周", "text": "好，今天先聊到这儿，下期继续拆。"}],
            },
        ],
    },
    "properties": {
        "title": {"type": "string"},
        "segments": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["name", "lines"],
                "properties": {
                    "name": {"type": "string"},
                    "lines": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["speaker", "text"],
                            "properties": {
                                "speaker": {"type": "string"},
                                "text": {"type": "string"},
                            },
                        },
                    },
                },
            },
        },
    },
}

PUBLISH_LIMITS = {
    "youtube": {
        "max_duration_seconds": None,
        "title_max": 100,
        "description_max": 5000,
        "note": "长视频支持；建议先用 unlisted 审后公开",
    },
    "tiktok": {
        "max_duration_seconds": 600,
        "note": "单个视频 ≤10 分钟；需 TikTok 账号已在 upload-post 授权",
    },
    "instagram": {
        "max_duration_seconds": 900,
        "note": "Reels 建议 ≤15 分钟；竖屏 9:16 最佳，横屏会加黑边",
    },
}

# 导入即建表 + 播种（幂等），保证直接 uvicorn 启动即可用
init_db()


STALE_JOB_ERROR = "上次进程退出（服务重启），任务中断（可用 retry）"


def _recover_stale_jobs(force: bool = False) -> None:
    """服务重启后，把上一个进程遗留的 queued/running 任务标记为 failed（可 retry）。

    demo 用进程内线程执行任务，进程退出后线程即消失；不做恢复会让前端一直看到
    幽灵任务。标记 failed 并保留 error 语义，用户可点 retry 重新入队。

    开关（P1-3）：``ZAOPIANJU_JOBS_RECOVER=0|off|false|no`` 时**完全跳过**恢复。
    原因是本恢复跑在模块导入期，而 ``uvicorn --reload`` 每次保存文件都会重新 import 一次；
    那样"改一行代码 → 上一轮 GPU 任务被标 failed（子进程还在渲）"是必然路径。
    ``backend/run.ps1`` 默认不再带 ``--reload``，显式 ``-Reload`` 时会自动置位本开关。
    """
    if not force and not JOBS_RECOVER:
        logger.warning(
            "启动恢复已跳过（ZAOPIANJU_JOBS_RECOVER=0）：保留 queued/running 任务原状，"
            "不把可能仍在跑的上一次任务判成 failed"
        )
        return
    if force and not JOBS_RECOVER:
        logger.warning("启动恢复被显式调用（force=True），忽略 ZAOPIANJU_JOBS_RECOVER=0")
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
            job.error = STALE_JOB_ERROR
            job.message = "上次进程退出，任务中断"
            job.updated_at = models.utcnow()
            if job.type == "shot" and job.shot_id:
                shot = db.get(models.Shot, job.shot_id)
                if shot is not None:
                    if str((job.params or {}).get("mode") or "").strip().lower() == "dynamic":
                        if shot.dynamic_status in ("queued", "generating"):
                            shot.dynamic_status = "failed"
                    elif shot.status in ("queued", "generating"):
                        shot.status = "failed"
            if job.type == "render" and job.episode_id:
                episode = db.get(models.Episode, job.episode_id)
                if episode is not None and episode.status == "rendering":
                    episode.status = "failed"
            if job.type == "localize":
                loc_id = (job.params or {}).get("localization_id")
                localization = db.get(models.Localization, loc_id) if loc_id else None
                if localization is not None and localization.status in ("draft", "rendering"):
                    localization.status = "failed"
            if job.type == "publish":
                pub_id = (job.params or {}).get("publish_id")
                pub_record = db.get(models.PublishRecord, pub_id) if pub_id else None
                if pub_record is not None and pub_record.status in (
                    "queued",
                    "prechecking",
                    "publishing",
                ):
                    pub_record.status = "failed"
                    pub_record.error = (
                        "上次进程退出，发布任务中断；结果不确定，请先核对 request_id 再决定是否重新发布"
                    )
        db.commit()
        logger.warning("启动恢复：%d 个未完成任务已标记 failed（可重试）", len(stale))


_recover_stale_jobs()

#: 本机 demo 无鉴权：CORS 只放行本机前端端口，绝不用通配。
#: 安全背景（2026-11-08 审查）：`allow_origins=["*"]` 让任意网页都能驱动
#: 127.0.0.1:8090 的写接口（改 LLM base_url / 起渲染 / 花钱）；收紧到本机来源后，
#: 浏览器会拦掉跨源写请求。局域网访问/反向代理需要显式加：
#:   $env:ZAOPIANJU_CORS_ORIGINS="http://192.168.1.10:8090"
_DEFAULT_CORS_ORIGINS = (
    "http://127.0.0.1:8090", "http://localhost:8090",
    "http://127.0.0.1:5173", "http://localhost:5173",
)


def _cors_origins() -> List[str]:
    raw = os.environ.get("ZAOPIANJU_CORS_ORIGINS", "").strip()
    if raw:
        return [item.strip() for item in raw.split(",") if item.strip()]
    return list(_DEFAULT_CORS_ORIGINS)


app = FastAPI(title=config.APP_NAME, version=VERSION)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),  # 只允许本机前端；见上方安全说明
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
    """懒同步：磁盘产物（概念图 / SDXL cutout / H3）-> Character URL/status，并保持 Asset 一致。"""
    changed = character_files.apply(character)
    changed = concept_files.apply(character) or changed
    changed = _sync_character_asset(db, character) or changed
    if changed and commit:
        db.commit()
    return changed


def available_formats() -> List[str]:
    """contract/formats/*.json 里真实存在的节目模板 id（升序，供 422 提示可用值）。"""
    if not config.FORMATS_DIR.is_dir():
        return []
    return sorted(path.stem for path in config.FORMATS_DIR.glob("*.json"))


def _load_format(format_id: str) -> Dict[str, Any]:
    """读取 contract/formats/<format_id>.json。

    id 先过统一校验（`backend/app/ids.py`），不再直接 `FORMATS_DIR / f"{id}.json"`：
    审查 P1-1 实测 `format_id="..\\\\..\\\\contract\\\\demo_episode"` 能读到 formats 之外的任意 json。
    - id 形态非法（含 `..`/`\\`/绝对路径）→ 400（这是调用方参数错误，不是"模板不存在"）
    - id 形态合法但文件不存在 → `{}`（保持既有"未知模板"语义，由调用方决定 400/422）
    """
    ident = ids.check_id(format_id, "format_id")
    path = ids.resolve_under(config.FORMATS_DIR, f"{ident}.json", "format_id")
    if path.is_file():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            logger.exception("格式文件解析失败: %s", path)
    return {}


def _require_format(format_id: str) -> Dict[str, Any]:
    """API 边界校验：format_id 必须是 contract/formats/*.json 里真实存在的一份。

    审查 P2 实测：`POST /api/projects {"format_id":"no_such_format"}` 曾直接 **200**，
    这个 id 还会写进 projects.format_id 长期驻留（前端 16 项下拉恰好掩盖了它）。
    现在不合法 → **422**，detail 里带上可用模板列表，前端能直接把选项摆出来。
    """
    choices = available_formats()
    hint = " / ".join(choices) or "（contract/formats 下没有可用模板）"
    try:
        ident = ids.check_id(format_id, "format_id")
    except HTTPException as exc:
        raise HTTPException(status_code=422, detail=f"{exc.detail}；可用模板: {hint}") from exc
    spec = _load_format(ident)
    if not spec:
        raise HTTPException(status_code=422, detail=f"未知节目模板: {ident}；可用模板: {hint}")
    return spec


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
# 台本 -> 分镜（contract/SCRIPT_FORMAT.md）
# --------------------------------------------------------------------------- #
def _episode_character_assets(db: Session, episode: models.Episode):
    return (
        db.query(models.Asset)
        .filter(
            models.Asset.episode_id == episode.id,
            models.Asset.type == "character",
        )
        .order_by(models.Asset.created_at.asc())
        .all()
    )


def _episode_characters(db: Session, episode: models.Episode):
    return (
        db.query(models.Character)
        .filter(models.Character.project_id == episode.project_id)
        .order_by(models.Character.created_at.asc())
        .all()
    )


def _build_script_shots(db: Session, episode: models.Episode, script: str) -> int:
    """按台本解析生成镜头（不 commit；调用方负责统计/提交）。"""
    assets = _episode_character_assets(db, episode)
    characters = _episode_characters(db, episode)
    payloads = script_parser.build_shots(episode.title, script, assets, characters)
    for payload in payloads:
        db.add(models.Shot(id=models.new_id("s"), episode_id=episode.id, **payload))
    db.flush()
    return len(payloads)


def _delete_all_shots(db: Session, episode_id: str) -> int:
    shots = db.query(models.Shot).filter(models.Shot.episode_id == episode_id).all()
    for shot in shots:
        db.delete(shot)
    db.flush()
    return len(shots)


def _reindex_shots(db: Session, episode_id: str) -> None:
    """按当前顺序重排 no 为 1..N（删除/追加后调用）。"""
    shots = (
        db.query(models.Shot)
        .filter(models.Shot.episode_id == episode_id)
        .order_by(models.Shot.no.asc(), models.Shot.id.asc())
        .all()
    )
    for index, shot in enumerate(shots, start=1):
        if shot.no != index:
            shot.no = index
    db.flush()


def _refresh_episode_shot_stats(db: Session, episode: models.Episode) -> None:
    jobs._refresh_episode_stats(db, episode)


def _copy_episode_assets(
    db: Session, source_episode: models.Episode, target_episode: models.Episode
) -> int:
    """把源集资产复制到目标集（角色 name/title/persona/voice/images 等一并复制）。"""
    source_assets = (
        db.query(models.Asset)
        .filter(models.Asset.episode_id == source_episode.id)
        .order_by(models.Asset.created_at.asc())
        .all()
    )
    for asset in source_assets:
        db.add(
            models.Asset(
                id=models.new_id("asset"),
                episode_id=target_episode.id,
                role=asset.role,
                type=asset.type,
                name=asset.name,
                title=asset.title,
                persona=asset.persona,
                voice=asset.voice,
                accent_color=asset.accent_color,
                images=list(asset.images or []),
                locked=asset.locked,
            )
        )
    return len(source_assets)


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
    fmt = _require_format(payload.format_id)
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


@router.patch("/projects/{pid}", response_model=schemas.ProjectOut)
def patch_project(
    pid: str,
    payload: schemas.ProjectUpdate,
    db: Session = Depends(get_db),
):
    """更新项目名称 / 节目模板 / 画幅（工作台「3D 模板」页切换模板用）。

    只接受 name / format_id / aspect 且至少传一个；format_id 必须是
    contract/formats/*.json 里真实存在的节目模板（否则 400，避免项目指向不存在的模板）。
    """
    project = _get_or_404(db, models.Project, pid, "项目")
    changes = {
        key: value
        for key, value in payload.model_dump(exclude_unset=True).items()
        if value is not None
    }
    if not changes:
        raise HTTPException(
            status_code=400,
            detail="没有可更新的字段（name / format_id / aspect 至少传一个）",
        )

    # 先全部校验、再统一赋值，避免部分字段非法时污染 session
    new_name: Optional[str] = None
    new_format_id: Optional[str] = None
    new_aspect: Optional[str] = None
    if "name" in changes:
        new_name = str(changes["name"]).strip()
        if not new_name:
            raise HTTPException(status_code=400, detail="项目名称不能为空")
        if len(new_name) > 200:
            raise HTTPException(
                status_code=400,
                detail=f"项目名称过长：{len(new_name)} 字（最多 200 字）",
            )
    if "format_id" in changes:
        raw_format_id = str(changes["format_id"]).strip()
        choices = available_formats()
        hint = " / ".join(choices) or "（contract/formats 下没有可用模板）"
        try:
            new_format_id = ids.check_id(raw_format_id, "format_id")
            spec = _load_format(new_format_id)
        except HTTPException:  # 非法 id（含 ..\ 穿越）与"不存在"统一按未知模板处理
            spec = {}
        if not spec:
            raise HTTPException(
                status_code=400, detail=f"未知节目模板: {raw_format_id}；可用模板: {hint}"
            )
    if "aspect" in changes:
        new_aspect = str(changes["aspect"]).strip()
        if new_aspect not in PROJECT_ASPECTS:
            raise HTTPException(
                status_code=400,
                detail=f"不支持的画幅: {new_aspect}；可选 {' / '.join(PROJECT_ASPECTS)}",
            )

    if new_name is not None:
        project.name = new_name
    if new_format_id is not None:
        project.format_id = new_format_id
    if new_aspect is not None:
        project.aspect = new_aspect
    db.commit()
    return project


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


@router.post("/projects/{pid}/episodes", response_model=schemas.EpisodeOut)
def create_episode(
    pid: str,
    payload: Optional[schemas.EpisodeCreate] = None,
    db: Session = Depends(get_db),
):
    """创建下一集（no 自增，draft，默认继承项目画幅/时长）。"""
    project = _get_or_404(db, models.Project, pid, "项目")
    setting = seed.get_settings(db)
    fmt = _load_format(project.format_id)
    max_no = (
        db.query(func.max(models.Episode.no))
        .filter(models.Episode.project_id == pid)
        .scalar()
        or 0
    )
    no = int(max_no) + 1
    last = (
        db.query(models.Episode)
        .filter(models.Episode.project_id == pid)
        .order_by(models.Episode.no.desc())
        .first()
    )
    target_duration = (
        int(payload.target_duration)
        if payload is not None and payload.target_duration
        else int(
            (last.target_duration if last is not None else None)
            or fmt.get("target_duration")
            or 296
        )
    )
    title = (
        payload.title.strip()
        if payload is not None and payload.title and payload.title.strip()
        else f"{project.name} · 第 {no} 期"
    )
    no_copy_assets = bool(payload.no_copy_assets) if payload is not None else False
    copy_from = payload.copy_assets_from if payload is not None else None
    source_episode: Optional[models.Episode] = None
    if not no_copy_assets:
        if copy_from:
            source_episode = _get_or_404(db, models.Episode, copy_from, "源集")
            if source_episode.project_id != pid:
                raise HTTPException(status_code=400, detail="copy_assets_from 必须是同一项目下的集")
        else:
            source_episode = last

    episode = models.Episode(
        id=models.new_id("ep"),
        project_id=pid,
        no=no,
        title=title,
        target_duration=target_duration,
        status="draft",
        progress=0.0,
        shots_total=0,
        shots_done=0,
        cost=0.0,
        aspect=project.aspect,
        resolution=(last.resolution if last is not None else setting.resolution) or "1280x720",
        fps=int((last.fps if last is not None else setting.fps) or 25),
        packaging=fmt.get("packaging") or {},
        script="",
    )
    db.add(episode)
    db.flush()
    if source_episode is not None:
        _copy_episode_assets(db, source_episode, episode)
    db.commit()
    return episode


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
    if not CHAR_ID_RE.match(character_id):
        raise HTTPException(status_code=400, detail=f"非法角色 id: {character_id}")
    if ".." in character_id or "/" in character_id or "\\" in character_id:
        raise HTTPException(status_code=400, detail=f"非法角色 id: {character_id}")
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
    """角色生成：统一走 SDXL 概念图（portrait/three_view/expressions），不再触发外部 3D 生成。"""
    character = _get_or_404(db, models.Character, cid, "角色")
    character.status = "generating"
    db.commit()
    job, _created = jobs.enqueue_concept_job(
        db,
        character,
        modes=["portrait", "three_view", "expressions"],
        style=None,
        prompt_extra=None,
        seed=None,
    )
    return job


@router.get("/characters/{cid}/files", response_model=schemas.CharacterFilesOut)
def get_character_files(cid: str, db: Session = Depends(get_db)):
    character = _get_or_404(db, models.Character, cid, "角色")
    _sync_character(db, character)
    data = concept_files.scan(character.id)
    return {
        "portrait": character.portrait_url,
        "sheet": character.concept_sheet_url,
        "turntable": None,
        "expressions": dict(character.concept_expressions or character.expressions or {}),
        "meta": data.get("meta") or {},
    }


# --------------------------------------------------------------------------- #
# AI 概念图 / H3 动画（contract/CONCEPT.md）
# --------------------------------------------------------------------------- #
def _resolve_output_file(raw: Any) -> Optional[Path]:
    """把 /media/... URL 或仓库相对/绝对路径解析为 outputs 内存在的文件（限制在 outputs）。

    containment 统一走 `backend/app/ids.py::resolve_under`（与三处合同 json 入口、`/media`
    证据路由同一套判定），不再各写一段 `relative_to`；越界一律按"不是产物文件"返回 None，
    保持本函数既有的 Optional 契约（调用方据此 400/回退），只记 warning 便于排障。
    """
    text = str(raw or "").strip()
    if not text or text.lower().startswith(("http://", "https://", "data:")):
        return None
    prefix = config.MEDIA_URL_PREFIX + "/"
    rel = text[len(prefix):] if text.startswith(prefix) else text
    try:
        resolved = ids.resolve_under(config.OUTPUTS_DIR, rel, "产物路径")
    except HTTPException:
        logger.warning("产物路径越出 outputs，已忽略: %r", text)
        return None
    return resolved if resolved.is_file() else None


def _character_default_image(character: models.Character) -> Optional[Path]:
    """H3 动画默认输入图：优先概念图立绘，其次本地立绘（SDXL cutout / 概念图回填）。"""
    for url in (character.concept_portrait_url, character.portrait_url):
        path = _resolve_output_file(url)
        if path is not None:
            return path
    return None


def _build_export_manifest(
    db: Session, episode: models.Episode, export_type: str = "capcut"
) -> Dict[str, Any]:
    """按 contract/EXPORT.md 生成导出清单（视频/音频/字幕/画布参数）。

    视频优先用分镜片段铺时间线（与 pipeline.capcut build-manifest 行为一致，剪映里可逐镜编辑）：
    workdir/clips/{sid}.mp4 -> outputs/episodes/{eid}/shots/{sid}.mp4 -> 单段 final.mp4。
    音频优先 workdir/audio/narration.wav（render 产出的旁白混音）；
    字幕来自镜头 line/subtitle，按镜头时长累计时间轴。
    """
    shots = (
        db.query(models.Shot)
        .filter(models.Shot.episode_id == episode.id)
        .order_by(models.Shot.no.asc())
        .all()
    )
    workdir = config.OUTPUTS_DIR / "episodes" / episode.id
    clips_dir = workdir / "clips"
    shots_dir = workdir / "shots"
    final_path = workdir / "final.mp4"

    cursor = 0.0
    subtitles: List[Dict[str, Any]] = []
    for shot in shots:
        duration = float(shot.duration or 0.0)
        # 字幕只收对白/反应（不把花字、片头片尾等包装文本当字幕）
        text = str(shot.line or "").strip()
        if not text and shot.type == "reaction":
            text = str(shot.subtitle or "").strip()
        if text:
            subtitles.append(
                {
                    "start": round(cursor, 3),
                    "end": round(cursor + duration, 3),
                    "text": text,
                }
            )
        cursor += duration
    shots_total = cursor

    use_shot_timeline = bool(shots) and all(
        (clips_dir / f"{shot.id}.mp4").is_file()
        or (shots_dir / f"{shot.id}.mp4").is_file()
        for shot in shots
    )
    video: List[Dict[str, Any]] = []
    if use_shot_timeline:
        start = 0.0
        for shot in shots:
            duration = float(shot.duration or 0.0)
            clip = clips_dir / f"{shot.id}.mp4"
            if clip.is_file():
                rel_path = f"outputs/episodes/{episode.id}/clips/{shot.id}.mp4"
            else:
                clip = shots_dir / f"{shot.id}.mp4"
                rel_path = f"outputs/episodes/{episode.id}/shots/{shot.id}.mp4"
            video.append(
                {
                    "path": rel_path,
                    "start": round(start, 3),
                    "duration": round(duration, 3),
                    "type": "shot",
                }
            )
            start += duration
        total = start
    else:
        total = jobs.probe_video_duration(final_path) or float(
            episode.target_duration or shots_total or 0.0
        )
        if final_path.is_file():
            video.append(
                {
                    "path": f"outputs/episodes/{episode.id}/final.mp4",
                    "start": 0,
                    "duration": round(float(total or 0.0), 3),
                    "type": "main",
                }
            )

    audio: List[Dict[str, Any]] = []
    narration = workdir / "audio" / "narration.wav"
    if narration.is_file():
        audio.append(
            {
                "path": f"outputs/episodes/{episode.id}/audio/narration.wav",
                "start": 0,
                "duration": round(float(total or 0.0), 3),
                "volume": 1.0,
            }
        )

    resolution = str(episode.resolution or "1280x720").lower()
    try:
        width, height = (int(part) for part in resolution.split("x", 1))
    except (TypeError, ValueError):
        width, height = 1280, 720
    name = f"造片局_EP{int(episode.no or 1):03d}"
    return {
        "name": name,
        "width": width,
        "height": height,
        "fps": int(episode.fps or 25),
        "duration": round(float(total or 0.0), 3),
        "video": video,
        "audio": audio,
        "subtitles": subtitles,
    }


@router.post("/characters/{cid}/concept/generate", response_model=schemas.JobOut)
def generate_character_concept(
    cid: str,
    payload: Optional[schemas.ConceptGenerateRequest] = None,
    db: Session = Depends(get_db),
):
    character = _get_or_404(db, models.Character, cid, "角色")
    modes = [str(mode) for mode in (payload.modes if payload else []) if str(mode)]
    if not modes:
        modes = ["portrait", "three_view"]
    invalid_modes = [mode for mode in modes if mode not in jobs.CONCEPT_MODES]
    if invalid_modes:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的模式: {invalid_modes}；可选 {list(jobs.CONCEPT_MODES)}",
        )
    style = payload.style if payload else None
    if style and style not in jobs.CONCEPT_STYLES:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的 style: {style}；可选 {list(jobs.CONCEPT_STYLES)}",
        )
    job, _created = jobs.enqueue_concept_job(
        db,
        character,
        modes=modes,
        style=style,
        prompt_extra=payload.prompt_extra if payload else None,
        seed=payload.seed if payload else None,
    )
    return job


@router.get("/characters/{cid}/concepts", response_model=schemas.ConceptFilesOut)
def get_character_concepts(cid: str, db: Session = Depends(get_db)):
    character = _get_or_404(db, models.Character, cid, "角色")
    _sync_character(db, character)
    data = concept_files.scan(character.id)
    return {
        "portrait": character.concept_portrait_url,
        "three_view": dict(character.concept_three_view or data.get("three_view") or {}),
        "expressions": dict(character.concept_expressions or data.get("expressions") or {}),
        "sheet": character.concept_sheet_url,
        "meta": data.get("meta") or {},
        "consistent": data.get("consistent") or {"three_view": {}, "expressions": {}, "meta": {}},
    }


@router.post("/characters/{cid}/portrait/from-concept", response_model=schemas.CharacterOut)
def set_portrait_from_concept(
    cid: str,
    payload: schemas.PortraitFromConceptRequest,
    db: Session = Depends(get_db),
):
    character = _get_or_404(db, models.Character, cid, "角色")
    source = _resolve_output_file(payload.path)
    if source is None:
        # 兼容前端简写：相对概念图目录的路径（如 "portrait.png"、
        # "consistent/three_view/front.png"），也接受 outputs/concepts/{cid}/ 相对路径。
        compact = str(payload.path or "").strip()
        if compact and not compact.lower().startswith(("http://", "https://", "data:")):
            candidate = concept_files.concept_dir(character.id) / compact
            source = _resolve_output_file(str(candidate))
            if source is None:
                source = _resolve_output_file(str(config.OUTPUTS_DIR / compact))
    if source is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "path 必须是 outputs 下存在的图片：/media/... URL、"
                "outputs/... 相对路径，或相对概念图目录的文件名（如 portrait.png）"
            ),
        )
    if source.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp"):
        raise HTTPException(status_code=400, detail="仅支持 png/jpg/webp 图片")
    base = character_files.character_dir(character.id)
    base.mkdir(parents=True, exist_ok=True)
    dest = base / "portrait_from_concept.png"
    shutil.copy2(source, dest)
    # 同步 cutout（若概念图旁有 cutout 版本）
    for candidate in (
        source.parent / "cutout" / source.name,
        source.parent.parent / "cutout" / source.name,
    ):
        if candidate.is_file():
            cutout_dir = base / "cutout"
            cutout_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(candidate, cutout_dir / "portrait.png")
            break
    character.portrait_url = config.media_url(dest)
    character.status = "ready"
    _sync_character_asset(db, character)
    db.commit()
    return character


@router.post("/characters/{cid}/h3-animate", response_model=schemas.JobOut)
def h3_animate_character(
    cid: str,
    payload: Optional[schemas.H3AnimateRequest] = None,
    db: Session = Depends(get_db),
):
    character = _get_or_404(db, models.Character, cid, "角色")
    _sync_character(db, character)
    if payload is not None and payload.image:
        resolved = _resolve_output_file(payload.image)
        if resolved is None:
            raise HTTPException(
                status_code=400,
                detail="image 必须是 outputs 下存在的图片（也接受 /media/... URL）",
            )
        image_path = resolved
    else:
        image_path = _character_default_image(character)
        if image_path is None:
            raise HTTPException(
                status_code=400,
                detail="角色还没有可用立绘/概念图，请先生成概念图或用 image 指定",
            )
    prompt = payload.prompt if payload is not None else None
    job, _created = jobs.enqueue_h3_job(db, character, str(image_path), prompt)
    return job


# --------------------------------------------------------------------------- #
# 成片导出（contract/EXPORT.md：剪映草稿）
# --------------------------------------------------------------------------- #
def _sanitize_export_name(raw: Any) -> Optional[str]:
    """导出名清洗：去掉 Windows 非法文件字符，避免目录穿越/覆盖意外路径。"""
    text = str(raw or "").strip()
    if not text:
        return None
    cleaned = re.sub(r'[\\/:*?"<>|]+', "_", text).strip(" .")
    return cleaned[:64] or None


@router.post("/episodes/{eid}/export", response_model=schemas.JobOut)
def export_episode(
    eid: str,
    payload: Optional[schemas.EpisodeExportRequest] = None,
    db: Session = Depends(get_db),
):
    episode = _get_or_404(db, models.Episode, eid, "集")
    export_type = (payload.type if payload is not None else None) or "capcut"
    if export_type != "capcut":
        raise HTTPException(status_code=400, detail="目前仅支持 type=capcut")
    manifest = _build_export_manifest(db, episode, export_type)
    export_name = _sanitize_export_name(payload.name) if payload is not None else None
    if export_name:
        manifest["name"] = export_name
    job, _created = jobs.enqueue_export_job(db, episode, manifest, export_type)
    return job


# --------------------------------------------------------------------------- #
# 多语种本地化（contract/LOCALIZATION.md）
# --------------------------------------------------------------------------- #
@router.post("/episodes/{eid}/localize", response_model=List[schemas.JobOut])
def localize_episode(
    eid: str,
    payload: Optional[schemas.LocalizeRequest] = None,
    db: Session = Depends(get_db),
):
    """按语言入队本地化任务；langs 缺省用 settings.localize_langs。"""
    episode = _get_or_404(db, models.Episode, eid, "集")
    setting = seed.get_settings(db)
    raw_langs = (
        payload.langs
        if payload is not None and payload.langs is not None
        else (setting.localize_langs or ["en", "es"])
    )
    langs: List[str] = []
    for item in raw_langs or []:
        lang = str(item).strip().lower()
        if not lang:
            continue
        if lang not in SUPPORTED_LANGS:
            raise HTTPException(
                status_code=400,
                detail=f"不支持的语言: {lang}；可选 {SUPPORTED_LANGS}",
            )
        if lang not in langs:
            langs.append(lang)
    if not langs:
        raise HTTPException(status_code=400, detail="至少需要一个语言（如 en/es）")
    jobs_out: List[models.Job] = []
    for lang in langs:
        job, _created = jobs.enqueue_localize_job(db, episode, lang)
        jobs_out.append(job)
    return jobs_out


@router.get("/episodes/{eid}/localizations", response_model=List[schemas.LocalizationOut])
def list_episode_localizations(eid: str, db: Session = Depends(get_db)):
    _get_or_404(db, models.Episode, eid, "集")
    return (
        db.query(models.Localization)
        .filter(models.Localization.episode_id == eid)
        .order_by(models.Localization.created_at.asc())
        .all()
    )


@router.get("/localizations/{lid}", response_model=schemas.LocalizationOut)
def get_localization(lid: str, db: Session = Depends(get_db)):
    return _get_or_404(db, models.Localization, lid, "本地化版本")


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


@router.post("/episodes/{eid}/shots", response_model=schemas.ShotOut)
def append_shot(
    eid: str,
    payload: Optional[schemas.ShotCreateRequest] = None,
    db: Session = Depends(get_db),
):
    """追加一个镜头（no 顺延，必要时重排）。"""
    episode = _get_or_404(db, models.Episode, eid, "集")
    data = payload.model_dump(exclude_unset=True) if payload is not None else {}
    shot_type = str(data.get("type") or "host_to_camera")
    if shot_type not in SHOT_TYPES:
        raise HTTPException(status_code=400, detail=f"type 只能是 {sorted(SHOT_TYPES)}")
    line = str(data.get("line") or "")
    visual = str(data.get("visual") or "")
    speaker_name = str(data.get("speaker_name") or "").strip()
    duration = data.get("duration")
    if duration is None:
        duration = script_parser.clamp_duration(line or visual)
    duration = max(0.5, min(60.0, float(duration)))
    speaker_id = None
    display_name = speaker_name
    if speaker_name:
        speaker_id, display_name = script_parser.match_speaker(
            speaker_name,
            _episode_character_assets(db, episode),
            _episode_characters(db, episode),
        )
    max_no = (
        db.query(func.max(models.Shot.no))
        .filter(models.Shot.episode_id == eid)
        .scalar()
        or 0
    )
    shot = models.Shot(
        id=models.new_id("s"),
        episode_id=eid,
        segment_id="custom",
        no=int(max_no) + 1,
        type=shot_type,
        duration=round(float(duration), 2),
        speaker=speaker_id,
        speaker_name=display_name or speaker_name,
        line=line,
        visual=visual,
        lower_third=None,
        punchline="",
        sfx="",
        mood="",
        status="pending",
        selected_version_id=None,
        title=None,
        subtitle=None,
    )
    db.add(shot)
    db.flush()
    _reindex_shots(db, eid)
    _refresh_episode_shot_stats(db, episode)
    db.commit()
    return shot


@router.delete("/shots/{sid}", response_model=schemas.ShotDeleteResult)
def delete_shot(sid: str, db: Session = Depends(get_db)):
    """删除镜头并重排剩余镜头 no。"""
    shot = _get_or_404(db, models.Shot, sid, "镜头")
    episode_id = shot.episode_id
    db.delete(shot)
    db.flush()
    _reindex_shots(db, episode_id)
    episode = db.get(models.Episode, episode_id)
    if episode is not None:
        _refresh_episode_shot_stats(db, episode)
    db.commit()
    total = (
        db.query(func.count(models.Shot.id))
        .filter(models.Shot.episode_id == episode_id)
        .scalar()
        or 0
    )
    return {"ok": True, "deleted": sid, "shots_total": int(total)}


@router.post("/shots/{sid}/generate", response_model=schemas.JobOut)
def generate_shot(
    sid: str,
    payload: Optional[schemas.ShotGenerateRequest] = None,
    db: Session = Depends(get_db),
):
    shot = _get_or_404(db, models.Shot, sid, "镜头")
    params = dict(payload.params) if payload is not None else {}
    mode = str(payload.mode or "").strip().lower() if payload is not None else ""
    if mode not in ("", "static", "dynamic"):
        raise HTTPException(status_code=400, detail="mode 只能是 static 或 dynamic")
    if mode == "dynamic":
        job, _created = jobs.enqueue_dynamic_shot_job(db, shot, params)
        return job
    job, _created = jobs.enqueue_shot_job(db, shot, params)
    return job


@router.post(
    "/episodes/{eid}/shots/generate-dynamic", response_model=List[schemas.JobOut]
)
def generate_dynamic_shots(
    eid: str,
    payload: Optional[schemas.DynamicGenerateRequest] = None,
    db: Session = Depends(get_db),
):
    """批量生成动态版：shot_ids 指定镜头，否则 all=true 生成全部分镜。"""
    _get_or_404(db, models.Episode, eid, "集")
    shots = (
        db.query(models.Shot)
        .filter(models.Shot.episode_id == eid)
        .order_by(models.Shot.no.asc())
        .all()
    )
    if not shots:
        raise HTTPException(status_code=400, detail="该集还没有镜头，请先生成分镜")
    if payload is not None and payload.shot_ids:
        by_id = {shot.id: shot for shot in shots}
        missing = [sid for sid in payload.shot_ids if sid not in by_id]
        if missing:
            raise HTTPException(
                status_code=400, detail=f"镜头不存在或不属于该集: {missing}"
            )
        selected = []
        seen = set()
        for sid in payload.shot_ids:
            if sid in seen:
                continue
            seen.add(sid)
            selected.append(by_id[sid])
    else:
        if payload is not None and not payload.all:
            raise HTTPException(
                status_code=400, detail="shot_ids 为空时 all 必须为 true"
            )
        selected = shots
    jobs_out: List[models.Job] = []
    for shot in selected:
        job, _created = jobs.enqueue_dynamic_shot_job(db, shot, {})
        jobs_out.append(job)
    return jobs_out


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
def generate_storyboard(
    eid: str,
    payload: Optional[schemas.StoryboardGenerateRequest] = None,
    db: Session = Depends(get_db),
):
    """台本 -> 分镜；script 为空时回退旧模板骨架。force=true 时重建。"""
    episode = _get_or_404(db, models.Episode, eid, "集")
    force = bool(payload.force) if payload is not None else False
    existing = db.query(models.Shot).filter(models.Shot.episode_id == eid).count()
    if existing and not force:
        return {"created": 0}
    if existing and force:
        _delete_all_shots(db, eid)
    script = str(episode.script or "")
    if script.strip():
        created = _build_script_shots(db, episode, script)
    else:
        created = _create_skeleton_shots(db, episode)
    episode.shots_done = 0
    episode.shots_total = created
    episode.progress = 0.0
    _refresh_episode_shot_stats(db, episode)
    db.commit()
    return {"created": created}


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
    if job.type == "publish":
        raise HTTPException(
            status_code=400,
            detail="发布任务失败不自动重试（避免重复发布）；请先核对 request_id，再重新发起发布",
        )
    job, created = jobs.retry_job(db, job)
    if not created:
        # 审查 P2：这里曾把 jobs.retry_job 的 (job, False) 丢掉 → running 任务点"重试"
        # 既不报错也没动作（200 + 状态不变）。现在明确 409，前端能显示"正在运行，不能重试"。
        raise HTTPException(
            status_code=409,
            detail=f"任务正在运行，不能重试（job={job.id}，status={job.status}）；请等它结束或在任务面板取消",
        )
    return job


# --------------------------------------------------------------------------- #
# LLM 内容生成（本地 Ollama / OpenAI 兼容 API / mock）
# --------------------------------------------------------------------------- #
def _mask_secret(secret: str) -> str:
    text = (secret or "").strip()
    if not text:
        return ""
    if len(text) <= 8:
        return "*" * len(text)
    return text[:4] + "***" + text[-4:]


def _extract_json_object(text: str) -> Dict[str, Any]:
    raw = (text or "").strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        raw = "\n".join(lines).strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("输出中没有 JSON 对象")
    try:
        data = json.loads(raw[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON 解析失败：{exc.msg}") from exc
    if not isinstance(data, dict):
        raise ValueError("JSON 顶层必须是对象")
    return data


def _validate_script_payload(data: Dict[str, Any], topic: str, duration: int) -> Dict[str, Any]:
    title = str(data.get("title") or topic or "未命名台本").strip() or topic
    segments_raw = data.get("segments")
    if not isinstance(segments_raw, list) or not segments_raw:
        raise ValueError("segments 必须是非空数组")
    if len(segments_raw) > 12:
        raise ValueError("segments 段数过多（最多 12 段）")
    segments: List[Dict[str, Any]] = []
    for index, segment in enumerate(segments_raw):
        if not isinstance(segment, dict):
            raise ValueError(f"第 {index + 1} 段不是对象")
        name = str(segment.get("name") or "").strip()
        lines_raw = segment.get("lines")
        if not isinstance(lines_raw, list) or not lines_raw:
            raise ValueError(f"第 {index + 1} 段 lines 必须是非空数组")
        lines: List[Dict[str, str]] = []
        for line in lines_raw:
            if not isinstance(line, dict):
                raise ValueError(f"第 {index + 1} 段存在非法台词")
            speaker = str(line.get("speaker") or "").strip()
            text = str(line.get("text") or "").strip()
            if not speaker or not text:
                raise ValueError(f"第 {index + 1} 段存在空 speaker/text")
            lines.append({"speaker": speaker, "text": text})
        segments.append({"name": name or f"第{index + 1}段", "lines": lines})
    total_lines = sum(len(segment["lines"]) for segment in segments)
    if total_lines < 3:
        raise ValueError("台词总数过少（至少 3 句）")
    return {"title": title, "segments": segments}


def _script_text_from_segments(segments: List[Dict[str, Any]]) -> str:
    blocks: List[str] = []
    for segment in segments:
        block = [f"[{segment['name']}]"]
        block.extend(
            f"{line['speaker']}：{line['text']}" for line in segment.get("lines") or []
        )
        blocks.append("\n".join(block))
    return "\n\n".join(blocks) + "\n"


def _build_script_prompt(
    topic: str,
    duration: int,
    tone: str,
    template_spec: Dict[str, Any],
) -> str:
    template_name = str(
        template_spec.get("name") or template_spec.get("title") or ""
    ).strip()
    template_desc = str(
        template_spec.get("description") or template_spec.get("summary") or ""
    ).strip()
    lines = [
        "你是一名中文综艺编剧，为「造片局 · AI微综艺」写一期可直接录制的台本。",
        f"话题：{topic}",
        f"目标时长：约 {duration} 秒（约 {max(6, duration // 4)} 句台词）。",
        f"语气：{tone or '轻松幽默'}。",
        "固定角色：老周（主持人，稳重吐槽）、阿栗（AI 内容创业者，脑洞大）、大魏（传统综艺导演，冷幽默）。",
        "结构：必须有「开场」「正片」「结尾」三段；开场抛话题，正片有观点碰撞和至少一次括号动作反应，结尾收束。",
        "台词要口语化、有梗、可被 TTS 直接朗读；不要舞台说明以外的旁白。",
    ]
    if template_name:
        lines.append(f"参考节目模板：{template_name}。")
    if template_desc:
        lines.append(f"模板说明：{template_desc[:400]}")
    lines.append(
        "输出 JSON：{\"title\":\"节目标题\",\"segments\":[{\"name\":\"开场\",\"lines\":[{\"speaker\":\"老周\",\"text\":\"台词\"}]}]}。"
    )
    return "\n".join(lines)


@router.get("/llm/config", response_model=schemas.LLMConfigOut)
def llm_config():
    cfg = llm.load_config()
    return {
        "provider": cfg.provider,
        "base_url": cfg.base_url,
        "model": cfg.model,
        "has_api_key": bool(cfg.api_key),
        "api_key_masked": _mask_secret(cfg.api_key),
        "timeout_seconds": cfg.timeout_seconds,
        "supported_providers": list(llm.SUPPORTED_PROVIDERS),
    }


@router.get("/llm/providers")
def llm_providers():
    return {
        "providers": [
            {
                "id": "ollama",
                "label": "本地 Ollama",
                "default_base_url": "http://127.0.0.1:11434",
                "requires_api_key": False,
                "default_model": "qwen2.5:7b",
            },
            {
                "id": "openai_compatible",
                "label": "OpenAI 兼容 API（DeepSeek/OpenAI 等）",
                "default_base_url": "https://api.openai.com/v1",
                "requires_api_key": True,
                "default_model": "gpt-4o-mini",
            },
            {
                "id": "mock",
                "label": "Mock（测试用，不联网）",
                "default_base_url": "",
                "requires_api_key": False,
                "default_model": "mock",
            },
        ]
    }


@router.post("/llm/test", response_model=schemas.LLMTestResult)
def llm_test(payload: Optional[schemas.LLMTestRequest] = None):
    payload = payload or schemas.LLMTestRequest()
    provider = payload.provider
    if provider is not None:
        provider = str(provider).strip().lower()
        if provider not in llm.SUPPORTED_PROVIDERS:
            raise HTTPException(
                status_code=400,
                detail=f"不支持的 LLM provider: {provider}；可选 {list(llm.SUPPORTED_PROVIDERS)}",
            )
    base_url = str(payload.base_url or "").strip()
    if base_url and not base_url.lower().startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="base_url 必须是 http(s) URL")
    model = str(payload.model or "").strip()
    api_key = str(payload.api_key or "").strip()
    # 覆盖值只用于本次测试，不落库；传了 base_url 又没传 key 时 test_connection 不会复用旧 Key。
    return llm.test_connection(provider, base_url=base_url or None,
                               model=model or None, api_key=api_key or None)


@router.post("/projects/{pid}/generate-script", response_model=schemas.ScriptGenerateResult)
def generate_script(
    pid: str,
    payload: schemas.ScriptGenerateRequest,
    db: Session = Depends(get_db),
):
    project = _get_or_404(db, models.Project, pid, "项目")
    template_spec: Dict[str, Any] = {}
    if payload.template_id:
        template_spec = _load_format(str(payload.template_id))
        if not template_spec:
            raise HTTPException(
                status_code=400, detail=f"模板不存在: {payload.template_id}"
            )
    topic = payload.topic.strip()
    if not topic:
        raise HTTPException(status_code=400, detail="topic 不能为空")
    prompt = _build_script_prompt(topic, payload.duration, payload.tone or "轻松幽默", template_spec)
    last_error = ""
    for attempt in range(2):
        attempt_prompt = prompt
        if attempt == 1:
            attempt_prompt = (
                prompt
                + "\n\n上一次输出不符合要求："
                + last_error
                + "\n请重新生成完整 JSON（不要 Markdown 代码块）。"
            )
        try:
            raw = llm.complete(
                attempt_prompt,
                json_schema=SCRIPT_JSON_SCHEMA,
                temperature=0.8 if attempt == 0 else 0.4,
            )
        except llm.LLMError as exc:
            raise HTTPException(status_code=502, detail=f"LLM 调用失败：{exc}") from exc
        try:
            data = _extract_json_object(raw)
            validated = _validate_script_payload(data, topic, payload.duration)
        except ValueError as exc:
            last_error = str(exc)[:300]
            continue
        script_text = _script_text_from_segments(validated["segments"])
        cfg = llm.load_config()
        return {
            "title": validated["title"],
            "script": script_text,
            "segments": validated["segments"],
            "topic": topic,
            "provider": cfg.provider,
            "model": cfg.model,
            "duration": payload.duration,
        }
    raise HTTPException(
        status_code=502,
        detail=f"LLM 输出不符合 JSON 结构（已重试一次）：{last_error or '未知原因'}",
    )


# --------------------------------------------------------------------------- #
# 一键发布（contract/PUBLISHING.md）
# --------------------------------------------------------------------------- #
def _publish_video_candidates(episode: models.Episode, lang: str, version: str) -> List[Path]:
    base = config.OUTPUTS_DIR / "episodes" / episode.id
    candidates: List[Path] = []
    if lang and lang != "zh":
        candidates.append(base / "localized" / lang / "final.mp4")
    if version == "dynamic":
        candidates.append(base / "final_dynamic.mp4")
    candidates.append(base / "final.mp4")
    deduped: List[Path] = []
    for candidate in candidates:
        if candidate not in deduped:
            deduped.append(candidate)
    return deduped


def _outputs_relative(path: Path) -> str:
    """outputs 下文件的相对路径（跨目录发布/检查时用于展示，失败回退文件名）。"""
    try:
        return path.resolve().relative_to(config.OUTPUTS_DIR.resolve()).as_posix()
    except (ValueError, OSError):
        return path.name


def resolve_episode_video(eid: str, render_url: Optional[str]) -> Optional[Path]:
    """解析一集要发布的成片文件（返回 outputs 下真实存在的文件；找不到返回 None）。

    取片优先级（与 PRD 对齐）：
    1. `render_url` 以 `/media/` 开头 → 映射 `outputs/<render_url 去掉 /media/>`，
       文件存在且 >0 字节则用它（覆盖 3D/relic 等产物不在 outputs/episodes/<eid>/ 的情况）；
    2. 回退 `outputs/episodes/<eid>/final.mp4`；
    3. 都不可用 → None（由调用方决定 400 / preflight fail）。
    只做本地 Path 判断，不发 HTTP；用 resolve 后相对 outputs 校验，防路径穿越。
    """
    outputs_root = Path(config.OUTPUTS_DIR)
    media_prefix = f"{config.MEDIA_URL_PREFIX}/"
    raw = str(render_url or "").strip()
    if raw.startswith(media_prefix):
        relative = raw[len(media_prefix):]
        candidate = (outputs_root / relative).resolve()
        try:
            candidate.relative_to(outputs_root.resolve())
            inside_outputs = True
        except (ValueError, OSError):
            inside_outputs = False
        if inside_outputs:
            try:
                if candidate.is_file() and candidate.stat().st_size > 0:
                    return candidate
            except OSError:
                pass
    fallback = outputs_root / "episodes" / str(eid) / "final.mp4"
    try:
        if fallback.is_file() and fallback.stat().st_size > 0:
            return fallback
    except OSError:
        pass
    return None


def _localized_publish_meta(episode: models.Episode, lang: str) -> Dict[str, Any]:
    if not lang or lang == "zh":
        return {}
    path = config.OUTPUTS_DIR / "episodes" / episode.id / "localized" / lang / "meta.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    publish = data.get("publish")
    if isinstance(publish, dict):
        return publish
    return data


def _normalize_hashtags(raw: Any) -> List[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        parts = [part.strip() for part in raw.replace(",", " ").split() if part.strip()]
    else:
        parts = [str(part).strip() for part in raw if str(part).strip()]
    tags: List[str] = []
    for tag in parts:
        if not tag.startswith("#"):
            tag = "#" + tag
        if tag not in tags:
            tags.append(tag)
    return tags


def _mask_username(username: str) -> str:
    text = (username or "").strip()
    if not text:
        return ""
    if len(text) <= 2:
        return "*" * len(text)
    return text[:2] + "***"


def _publish_config_values(db: Session) -> Dict[str, Any]:
    """发布配置读取（`GET /publish/config` 与发布前检查共用，不回传明文 key）。"""
    setting = seed.get_settings(db)
    api_key = config.publish_key()
    username = setting.upload_post_username or ""
    configured = bool(api_key and username and setting.upload_post_enabled)
    return {
        "configured": configured,
        "username_masked": _mask_username(username),
        "enabled": bool(setting.upload_post_enabled),
        "platforms": list(setting.upload_post_platforms or PUBLISH_PLATFORMS),
        "youtube_privacy": setting.upload_post_youtube_privacy_status or "unlisted",
        "limits": PUBLISH_LIMITS,
        "brand": {
            "title": setting.publish_brand_title or "造片局 · AI微综艺",
            "hashtags": DEFAULT_PUBLISH_HASHTAGS,
        },
    }


@router.get("/publish/config", response_model=schemas.PublishConfigOut)
def publish_config(db: Session = Depends(get_db)):
    return _publish_config_values(db)


def _format_mmss(seconds: float) -> str:
    total = int(round(max(0.0, float(seconds or 0.0))))
    minutes, secs = divmod(total, 60)
    return f"{minutes}:{secs:02d}"


def _account_value_present(value: Any) -> bool:
    """Upload-Post social_accounts 里某平台的值是否表示「已连接」。

    实测未连接平台会返回空字符串占位（如 `{"tiktok": ""}`），且上传时报
    "Profile default has no ... account configured"；因此空字符串/空容器视为未连接，
    真正连接后通常返回账号名/对象/非空值。
    """
    if value is None or value is False:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict, tuple, set)):
        return bool(value)
    return True


def _upload_post_connected_platforms(
    api_key: str, platforms: List[str], username: str = ""
) -> List[str]:
    """GET Upload-Post `/api/uploadposts/users`，返回所选平台中已连接（有账号）的平台列表。

    只读请求、3s 超时；网络/HTTP/JSON 异常原样抛给调用方（preflight 会转 warn，绝不 500）。
    """
    selected = [str(item).strip().lower() for item in (platforms or []) if str(item).strip()]
    if not selected:
        return []
    response = httpx.get(
        UPLOAD_POST_USERS_URL,
        headers={"Authorization": f"Apikey {api_key}"},
        timeout=UPLOAD_POST_ACCOUNTS_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    try:
        payload = response.json()
    except ValueError as exc:
        raise ValueError("Upload-Post users 返回非 JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("Upload-Post users 返回格式异常")
    profiles = payload.get("profiles")
    if not isinstance(profiles, list):
        profiles = []
    wanted_username = str(username or "").strip().lower()
    matched = [
        profile
        for profile in profiles
        if isinstance(profile, dict)
        and wanted_username
        and str(profile.get("username") or "").strip().lower() == wanted_username
    ]
    account_maps: List[Dict[str, Any]] = []
    for profile in matched or profiles:
        accounts = profile.get("social_accounts") if isinstance(profile, dict) else None
        if isinstance(accounts, dict):
            account_maps.append(accounts)
    connected: List[str] = []
    for accounts in account_maps:
        for platform, account in accounts.items():
            name = str(platform or "").strip().lower()
            if name not in selected or name in connected:
                continue
            if _account_value_present(account):
                connected.append(name)
    return connected


def _publish_preflight_checks(
    episode: models.Episode, db: Session
) -> Tuple[List[Dict[str, Any]], List[str], Dict[str, Any]]:
    """发布前检查（纯读：DB 查询 + 文件 stat/ffprobe + Upload-Post 账号只读 GET，不写库/不发布）。"""
    checks: List[Dict[str, Any]] = []
    warnings: List[str] = []

    def add(
        check_id: str,
        label: str,
        status: str,
        detail: str = "",
        warning: Optional[str] = None,
    ) -> None:
        checks.append({"id": check_id, "label": label, "status": status, "detail": detail})
        if warning:
            warnings.append(warning)

    # 1) 成片就绪：render_url 缺失 -> fail；有 url 但文件解析不到 -> warn（video 检查会 fail）
    render_url = str(episode.render_url or "").strip()
    render_path = _resolve_output_file(render_url) if render_url else None
    render_duration = 0.0
    if render_path is not None:
        render_duration = float(jobs.probe_video_duration(render_path) or 0.0)
    if not render_url:
        add("render", "成片就绪", "fail", "尚未渲染成片（render_url 为空）", "尚未渲染成片，请先在「生成」页渲染")
    elif render_path is None:
        add(
            "render",
            "成片就绪",
            "warn",
            f"render_url 指向的文件不存在：{render_url}",
            "render_url 指向的成片文件不存在",
        )
    else:
        duration_text = _format_mmss(render_duration) if render_duration > 0 else "时长未知"
        add("render", "成片就绪", "pass", f"{duration_text} {render_url}")

    # 2) 标题：1–100 字 pass，超长 warn，空 fail
    title = str(episode.title or "").strip()
    if not title:
        add("title", "标题", "fail", "标题为空", "标题为空，请先填写节目标题")
    elif len(title) > PUBLISH_TITLE_MAX_CHARS:
        add(
            "title",
            "标题",
            "warn",
            f"{len(title)} 字（建议 ≤{PUBLISH_TITLE_MAX_CHARS} 字）：{title[:60]}",
            f"标题 {len(title)} 字，超过建议的 {PUBLISH_TITLE_MAX_CHARS} 字",
        )
    else:
        add("title", "标题", "pass", title)

    # 3) 多语种版本：0 个 -> warn
    loc_rows = (
        db.query(models.Localization)
        .filter(models.Localization.episode_id == episode.id)
        .all()
    )
    # 只有真正出片的版本才算：status=ready 且 video_url 非空（只有 draft 记录不算）
    ready_rows = [row for row in loc_rows
                  if str(getattr(row, "status", "") or "").lower() == "ready"
                  and str(getattr(row, "video_url", "") or "").strip()]
    langs = sorted(
        {
            str(row.lang or "").strip().lower()
            for row in ready_rows
            if str(row.lang or "").strip()
        }
    )
    if not langs:
        add("localization", "多语种版本", "warn", "暂无多语种版本（仅中文）", "暂无多语种版本，可先发布中文版")
    else:
        labels = [LOCALIZATION_LANG_LABELS.get(lang, lang) for lang in langs]
        add("localization", "多语种版本", "pass", f"{'、'.join(labels)}（{len(langs)} 个版本）")

    # 4) 发布配置（复用 /publish/config 读取逻辑）
    publish_cfg = _publish_config_values(db)
    if not publish_cfg["configured"]:
        add(
            "config",
            "发布配置",
            "fail",
            "Upload-Post 未配置（需要 API Key + username）",
            "发布前需要配置 Upload-Post Key 与账号",
        )
    elif not publish_cfg["enabled"]:
        add(
            "config",
            "发布配置",
            "fail",
            f"已配置 {publish_cfg['username_masked'] or '账号'}，但发布开关已关闭",
            "Upload-Post 发布开关已关闭",
        )
    else:
        add(
            "config",
            "发布配置",
            "pass",
            f"已配置 {publish_cfg['username_masked']} · {'/'.join(publish_cfg['platforms'])}",
        )

    # 5) 视频可访问：与 POST /episodes/{eid}/publish 统一走 resolve_episode_video()
    #    （render_url 优先，其次集目录 final.mp4），只 Path 判断存在性与大小，不真的发 HTTP。
    video_path = resolve_episode_video(episode.id, episode.render_url)
    if video_path is None:
        expected = _publish_video_candidates(episode, "zh", "final")[0]
        detail = f"成片文件不存在：{_outputs_relative(expected)}"
        if render_url:
            detail += f"（render_url={render_url} 也未解析到 outputs 下有效文件）"
        add("video", "视频可访问", "fail", detail, "成片文件不可访问（render_url / 集目录成片均不存在）")
    else:
        try:
            size = int(video_path.stat().st_size)
        except OSError:
            size = 0
        relative_path = _outputs_relative(video_path)
        if size <= 0:
            add("video", "视频可访问", "fail", f"成片文件为空：{relative_path}", "成片文件为空")
        else:
            add(
                "video",
                "视频可访问",
                "pass",
                f"{size / 1024 / 1024:.1f}MB {relative_path}",
            )

    # 6) 社交账号授权：key 已配置时才查（GET /api/uploadposts/users，3s 超时，只读）；
    #    全都没连接 -> fail（提示去 upload-post 后台 OAuth 连接账号）；查询失败 -> warn 不阻断。
    publish_api_key = config.publish_key()
    if publish_api_key:
        selected_platforms = list(publish_cfg["platforms"] or PUBLISH_PLATFORMS)
        try:
            connected = _upload_post_connected_platforms(
                publish_api_key,
                selected_platforms,
                username=seed.get_settings(db).upload_post_username or "",
            )
        except Exception as exc:  # noqa: BLE001 - 账号状态查询失败绝不能阻断 preflight
            add(
                "accounts",
                "社交账号授权",
                "warn",
                f"账号状态查询失败，不阻断发布（{type(exc).__name__}）",
                "账号状态查询失败，不阻断发布",
            )
            connected = None
        if connected is not None:
            if connected:
                labels = [UPLOAD_POST_PLATFORM_LABELS.get(item, item) for item in connected]
                add("accounts", "社交账号授权", "pass", f"已连接：{'、'.join(labels)}")
            else:
                add(
                    "accounts",
                    "社交账号授权",
                    "fail",
                    "Upload-Post 未连接任何社交账号（需在 upload-post.com 后台 OAuth 连接 YouTube/TikTok/Instagram）",
                    "Upload-Post 未连接任何社交账号（需在 upload-post.com 后台 OAuth 连接账号）",
                )

    # 7) 可见性映射（P1-18）：把"每个平台实际会用哪个隐私档"提前摊给用户看。
    #    unlisted 在 TikTok/Instagram 只能落到 MUTUAL_FOLLOW_FRIENDS（仅互关可见），
    #    既不是"公开"也不是"不公开"；真发布时默认拒发，dry_run 只警告不拦。
    selected = list(publish_cfg["platforms"] or PUBLISH_PLATFORMS)
    privacy = str(publish_cfg.get("youtube_privacy") or "unlisted").strip().lower()
    if privacy not in ("public", "unlisted", "private"):
        privacy = "unlisted"
    levels = _privacy_levels_preview(selected, privacy)
    if levels:
        warning = _privacy_warning(privacy, levels)
        add(
            "privacy",
            "可见性映射",
            "warn" if warning else "pass",
            " · ".join(f"{p}={lv}" for p, lv in levels.items()),
            warning or None,
        )

    return checks, warnings, publish_cfg


@router.get("/episodes/{eid}/publish/preflight", response_model=schemas.PublishPreflightOut)
def publish_preflight(eid: str, db: Session = Depends(get_db)):
    """发布前检查：成片/标题/多语种/发布配置/视频可访问，全部纯读。

    - ok：没有 fail 项；
    - publishable：ok 且发布配置已配置且开启；
    - 视频检查与 POST /publish 取片逻辑一致（集目录成片），避免"检查通过但发布 400"。
    """
    episode = _get_or_404(db, models.Episode, eid, "集")
    checks, warnings, publish_cfg = _publish_preflight_checks(episode, db)
    ok = all(check["status"] != "fail" for check in checks)
    publishable = bool(ok and publish_cfg["configured"] and publish_cfg["enabled"])
    return {
        "episode_id": episode.id,
        "ok": ok,
        "publishable": publishable,
        "checks": checks,
        "config": {
            "configured": bool(publish_cfg["configured"]),
            "enabled": bool(publish_cfg["enabled"]),
            "platforms": list(publish_cfg["platforms"]),
            "username_masked": publish_cfg["username_masked"] or None,
        },
        "warnings": warnings,
    }


@router.post("/episodes/{eid}/publish", response_model=schemas.JobOut)
def publish_episode(
    eid: str,
    payload: Optional[schemas.PublishRequest] = None,
    allow_public_fallback: bool = False,
    db: Session = Depends(get_db),
):
    """发起一次发布（入队 job，真正的上传在后台线程 + publish.cli 子进程里）。

    ``allow_public_fallback``（query 参数，默认 False）：审查 P1-18 —— `privacy=unlisted`
    而平台含 TikTok/Instagram 时，Upload-Post 只有 `MUTUAL_FOLLOW_FRIENDS` 这种"仅互关可见"
    的档位，与"不公开"并不等价；此前的实现把 `privacy_level` 硬编码成 `PUBLIC_TO_EVERYONE`，
    等于 **TikTok/IG 永远公开**（全仓唯一不可撤回的外部副作用）。现在默认**拒发**，只有显式
    传 true 才允许映射到"仅互关可见"并继续发布。
    """
    episode = _get_or_404(db, models.Episode, eid, "集")
    setting = seed.get_settings(db)
    body = payload or schemas.PublishRequest()

    platforms: List[str] = []
    raw_platforms = body.platforms if body.platforms is not None else (setting.upload_post_platforms or PUBLISH_PLATFORMS)
    for item in raw_platforms or []:
        platform = str(item).strip().lower()
        if not platform:
            continue
        if platform not in PUBLISH_PLATFORMS:
            raise HTTPException(
                status_code=400,
                detail=f"不支持的平台: {platform}；可选 {PUBLISH_PLATFORMS}",
            )
        if platform not in platforms:
            platforms.append(platform)
    if not platforms:
        raise HTTPException(status_code=400, detail="至少选择一个发布平台")

    lang = str(body.lang or "zh").strip().lower()
    if lang not in PUBLISH_LANGS:
        raise HTTPException(status_code=400, detail=f"不支持的语言: {lang}；可选 {PUBLISH_LANGS}")
    version = str(body.version or "final").strip().lower()
    if version not in ("final", "dynamic"):
        raise HTTPException(status_code=400, detail="version 只能是 final 或 dynamic")
    privacy = str(body.privacy or setting.upload_post_youtube_privacy_status or "unlisted").strip().lower()
    if privacy not in ("public", "unlisted", "private"):
        raise HTTPException(status_code=400, detail="privacy 只能是 public / unlisted / private")
    # P1-18：unlisted 在 TikTok/Instagram 上没有等价档位（Upload-Post 只有
    # MUTUAL_FOLLOW_FRIENDS「仅互关可见」）。默认拒发，别让"不公开"变成"永远公开"。
    # dry_run 只是预检、一个字节都不上传，因此放行（否则连"先看看会发成什么"都做不到）；
    # 真上传时 upload_post._privacy_levels 还有同一道闸门（防其它调用方绕过）。
    public_risky = [p for p in platforms if not p.startswith("youtube")]
    if privacy == "unlisted" and public_risky and not allow_public_fallback and not body.dry_run:
        raise HTTPException(
            status_code=400,
            detail=(
                f"privacy=unlisted 无法安全映射到 {public_risky}："
                "TikTok/Instagram 最接近的档位是 MUTUAL_FOLLOW_FRIENDS（仅互关可见），不是「不公开」。"
                "请改用 privacy=private（SELF_ONLY），或只发 youtube，"
                "或确认可接受该可见范围后加 ?allow_public_fallback=true 重发"
                "（dry_run=true 的预检不受此限制）"
            ),
        )

    candidates = _publish_video_candidates(episode, lang, version)
    if lang == "zh" and version == "final":
        # 默认发布（中文终版）：统一走 resolve_episode_video —— render_url 优先，
        # 覆盖 3D/relic 等成片不在 outputs/episodes/<eid>/ 的情况。
        video_path = resolve_episode_video(episode.id, episode.render_url)
    else:
        # 多语种/动态版先按语言/版本取片；取不到回退统一解析（保持既有 API 行为），
        # 但必须在记录里标记 video_fallback，避免记录写着 lang=en 实际是中文母版还看不出来。
        video_path = next((candidate for candidate in candidates if candidate.is_file()), None)
        if video_path is None:
            video_path = resolve_episode_video(episode.id, episode.render_url)
            video_fallback = True
    if video_path is None:
        detail = (
            "待发布视频不存在（请先渲染"
            + ("动态版" if version == "dynamic" else "")
            + f"）：{_outputs_relative(candidates[0])}"
        )
        if episode.render_url:
            detail += f"（render_url={episode.render_url} 也未解析到 outputs 下有效文件）"
        raise HTTPException(status_code=400, detail=detail)

    api_key = config.publish_key()
    configured = bool(api_key and setting.upload_post_username and setting.upload_post_enabled)
    if not body.dry_run and not configured:
        raise HTTPException(
            status_code=400,
            detail="Upload-Post 未配置或未启用：需要 API Key + username，且 upload_post_enabled=true",
        )

    publish_meta = _localized_publish_meta(episode, lang)
    title = (
        str(body.title).strip()
        if body.title and str(body.title).strip()
        else str(publish_meta.get("title") or episode.title or setting.publish_brand_title)
    )
    description = (
        str(body.description).strip()
        if body.description is not None and str(body.description).strip()
        else str(
            publish_meta.get("description")
            or f"{setting.publish_brand_title}｜第 {episode.no} 期：{episode.title}（AI 生成内容）"
        )
    )
    if body.hashtags is not None:
        hashtags = _normalize_hashtags(body.hashtags)
        if not hashtags:
            hashtags = _normalize_hashtags(DEFAULT_PUBLISH_HASHTAGS)
    else:
        hashtags = _normalize_hashtags(
            publish_meta.get("hashtags") or DEFAULT_PUBLISH_HASHTAGS
        )

    # 真判断：多语种/动态版最终落到母版 final.mp4 才算 fallback（candidates 里本来也含 final.mp4）
    fallback_flag = bool((lang != "zh" or version != "final")
                         and Path(video_path).name == "final.mp4")
    # 台账回显（P1-18）：每个平台实际会用哪个隐私档 + 一句人话警告。
    # 落进 record.meta（GET /episodes/{eid}/publishes 可见），dry_run 时再写进 job.message，
    # 让 200 响应当场就能看到"这次预检会发成什么"，不必等发布完成。
    privacy_levels = _privacy_levels_preview(platforms, privacy)
    privacy_warning = _privacy_warning(privacy, privacy_levels)
    record = models.PublishRecord(
        id=models.new_id("pub"),
        episode_id=episode.id,
        platforms=platforms,
        lang=lang,
        video_url=config.media_url(video_path),
        title=title,
        status="queued",
        request_id=None,
        meta={
            "description": description,
            "hashtags": hashtags,
            "privacy": privacy,
            "privacy_levels": privacy_levels,
            "privacy_warnings": [privacy_warning] if privacy_warning else [],
            "allow_public_fallback": bool(allow_public_fallback),
            "dry_run": bool(body.dry_run),
            "version": version,
            "video_fallback": fallback_flag,
            "video_rel": str(video_path.relative_to(config.OUTPUTS_DIR).as_posix()),
        },
        error=None,
    )
    db.add(record)
    db.flush()
    job, _created = jobs.enqueue_publish_job(
        db,
        episode,
        record,
        {
            "publish_id": record.id,
            "dry_run": bool(body.dry_run),
            "platforms": platforms,
            "lang": lang,
            "version": version,
            "allow_public_fallback": bool(allow_public_fallback),
            "video_rel": str(video_path.relative_to(config.OUTPUTS_DIR).as_posix()),
        },
    )
    if not _created:
        # 命中去重（已有同参数发布任务在跑）：保留这条记录做审计，
        # 但标成 duplicate，不能永远 queued（幽灵记录）。
        record.status = "duplicate"
        record.error = "与进行中的同参数发布任务合并"
    if body.dry_run:
        # dry_run 不产生任何外网副作用，因此上面不拦；但必须把"会发成什么"当场回显：
        # JobOut 没有 meta 字段，能当场看到的就是 message（台账再看 /publishes 的 record.meta）。
        preview = " · ".join(f"{p}={lv}" for p, lv in (privacy_levels or {}).items())
        job.message = ("预检：" + (preview or "privacy 映射未知")
                       + (f"｜{privacy_warning}" if privacy_warning else ""))
        job.updated_at = models.utcnow()
    db.commit()
    return job


@router.get("/episodes/{eid}/publishes", response_model=List[schemas.PublishRecordOut])
def list_episode_publishes(eid: str, db: Session = Depends(get_db)):
    _get_or_404(db, models.Episode, eid, "集")
    return (
        db.query(models.PublishRecord)
        .filter(models.PublishRecord.episode_id == eid)
        .order_by(models.PublishRecord.created_at.desc())
        .all()
    )


@router.get("/publishes/{pid}", response_model=schemas.PublishRecordOut)
def get_publish_record(pid: str, db: Session = Depends(get_db)):
    return _get_or_404(db, models.PublishRecord, pid, "发布记录")


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
    # metaso_key / upload_post_api_key 不落 DB、不回传：分别写入 env 文件
    metaso_key = changes.pop("metaso_key", None)
    upload_post_api_key = changes.pop("upload_post_api_key", None)
    # LLM 配置写 backend/data/llm.env（api_key 不落 DB、不回传）
    llm_updates: Dict[str, str] = {}
    llm_provider = changes.pop("llm_provider", None)
    if llm_provider is not None:
        provider = str(llm_provider).strip().lower()
        if provider not in llm.SUPPORTED_PROVIDERS:
            raise HTTPException(
                status_code=400,
                detail=f"llm_provider 只能是 {list(llm.SUPPORTED_PROVIDERS)}",
            )
        llm_updates["LLM_PROVIDER"] = provider
    llm_base_url = changes.pop("llm_base_url", None)
    if llm_base_url is not None:
        base = str(llm_base_url).strip()
        if base and not base.lower().startswith(("http://", "https://")):
            raise HTTPException(status_code=400, detail="llm_base_url 必须是 http(s) URL")
        llm_updates["LLM_BASE_URL"] = base
    llm_model = changes.pop("llm_model", None)
    if llm_model is not None:
        model = str(llm_model).strip()
        if not model:
            raise HTTPException(status_code=400, detail="llm_model 不能为空")
        llm_updates["LLM_MODEL"] = model
    llm_api_key = changes.pop("llm_api_key", None)
    if llm_api_key is not None:
        llm_updates["LLM_API_KEY"] = str(llm_api_key).strip()
    if (llm_base_url is not None and llm_api_key is None
            and str(llm_updates.get("LLM_BASE_URL") or "").strip()):
        old_base = llm.load_config().base_url
        new_base = str(llm_updates.get("LLM_BASE_URL") or "").strip()
        if _url_host(new_base) != _url_host(old_base):
            # 换地址/换服务商时必须重新填 Key：否则旧 Key 会被发到调用方指定的新地址
            # （审查 2026-11-08：CORS 通配 + 可改 base_url 的 Key 外泄链）。
            llm_updates["LLM_API_KEY"] = ""
    if changes.get("generation_mode") is not None and changes["generation_mode"] not in ("mock", "comfyui"):
        raise HTTPException(status_code=400, detail="generation_mode 只能是 mock 或 comfyui")
    provider = changes.get("h3_provider")
    if provider is not None and provider not in ("local", "metaso"):
        raise HTTPException(status_code=400, detail="h3_provider 只能是 local 或 metaso（metaso 为测试 provider）")
    resolution = changes.get("metaso_resolution")
    if resolution is not None and resolution not in ("768P", "2K"):
        raise HTTPException(status_code=400, detail="metaso_resolution 只能是 768P 或 2K")
    duration = changes.get("metaso_duration")
    if duration is not None and not (4 <= int(duration) <= 15):
        raise HTTPException(status_code=400, detail="metaso_duration 必须在 4-15 秒之间")
    base_url = changes.get("metaso_base_url")
    if base_url is not None and str(base_url).strip() and not str(base_url).lower().startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="metaso_base_url 必须是 http(s) URL")
    # 多语种本地化设置
    if changes.get("localize_langs") is not None:
        normalized_langs: List[str] = []
        for item in changes["localize_langs"] or []:
            lang = str(item).strip().lower()
            if not lang:
                continue
            if lang not in SUPPORTED_LANGS:
                raise HTTPException(
                    status_code=400,
                    detail=f"不支持的语言: {lang}；可选 {SUPPORTED_LANGS}",
                )
            if lang not in normalized_langs:
                normalized_langs.append(lang)
        if not normalized_langs:
            raise HTTPException(status_code=400, detail="localize_langs 不能为空")
        changes["localize_langs"] = normalized_langs
    translator = changes.get("localize_translator")
    if translator is not None:
        translator = str(translator).strip().lower()
        if translator not in ("ollama", "nllb", "mock"):
            raise HTTPException(
                status_code=400,
                detail="localize_translator 只能是 ollama / nllb / mock（ollama 默认，nllb 兜底）",
            )
        changes["localize_translator"] = translator
    # 一键发布 Upload-Post
    if changes.get("upload_post_platforms") is not None:
        publish_platforms: List[str] = []
        for item in changes["upload_post_platforms"] or []:
            platform = str(item).strip().lower()
            if not platform:
                continue
            if platform not in PUBLISH_PLATFORMS:
                raise HTTPException(
                    status_code=400,
                    detail=f"不支持的平台: {platform}；可选 {PUBLISH_PLATFORMS}",
                )
            if platform not in publish_platforms:
                publish_platforms.append(platform)
        if not publish_platforms:
            raise HTTPException(status_code=400, detail="upload_post_platforms 不能为空")
        changes["upload_post_platforms"] = publish_platforms
    youtube_privacy = changes.get("upload_post_youtube_privacy_status")
    if youtube_privacy is not None:
        youtube_privacy = str(youtube_privacy).strip().lower()
        if youtube_privacy not in ("public", "unlisted", "private"):
            raise HTTPException(
                status_code=400,
                detail="upload_post_youtube_privacy_status 只能是 public / unlisted / private",
            )
        changes["upload_post_youtube_privacy_status"] = youtube_privacy
    if changes.get("upload_post_username") is not None:
        changes["upload_post_username"] = str(changes["upload_post_username"]).strip()
    if changes.get("publish_brand_title") is not None:
        brand_title = str(changes["publish_brand_title"]).strip()
        if not brand_title:
            raise HTTPException(status_code=400, detail="publish_brand_title 不能为空")
        changes["publish_brand_title"] = brand_title
    for key, value in changes.items():
        if value is not None:
            setattr(setting, key, value)
    if metaso_key is not None:
        config.save_metaso_key(str(metaso_key))
    if upload_post_api_key is not None:
        config.save_publish_key(str(upload_post_api_key))
    if llm_updates:
        config.save_llm_config(llm_updates)
    db.commit()
    db.refresh(setting)
    return setting


# --------------------------------------------------------------------------- #
# SSE（可选；前端默认轮询）
# --------------------------------------------------------------------------- #
def _jobs_snapshot(episode_id: Optional[str]) -> Dict[str, Any]:
    """SSE 用的一次快照（同步 DB 调用，必须放到线程里跑，别堵事件循环）。"""
    with SessionLocal() as db:
        query = db.query(models.Job)
        if episode_id:
            query = query.filter(models.Job.episode_id == episode_id)
        rows = query.order_by(models.Job.created_at.desc()).limit(20).all()
        return {
            "jobs": [
                schemas.JobOut.model_validate(row).model_dump(mode="json")
                for row in rows
            ]
        }


@router.get("/events")
async def events(request: Request, episode_id: Optional[str] = None):
    """任务状态 SSE（前端默认轮询，此端点可选）。

    审查 P2-3：原实现把同步 SQLAlchemy 直接跑在 async 生成器里 —— 每 2s 一次 SQLite 查询
    会卡住整个事件循环（所有并发请求一起排队）；DB 一旦出错只 `logger.exception` 然后照旧
    `yield data: {}`，客户端无法区分"没有任务"和"后端挂了"。

    现在：① 查询丢到 `asyncio.to_thread`；② 异常时发一条 `event: error` 后结束（不再吞成 `{}`）；
    ③ 用 `request.is_disconnected()` 提前结束，最长仍保留 5 分钟上限。
    """

    async def event_stream():
        yield "retry: 2000\n\n"
        for _ in range(150):
            if await request.is_disconnected():
                return
            try:
                payload = await asyncio.to_thread(_jobs_snapshot, episode_id)
                payload_json = json.dumps(payload, ensure_ascii=False)
            except Exception as exc:  # noqa: BLE001 - 出错必须让客户端看得见
                logger.exception("SSE 查询失败")
                error = json.dumps(
                    {"error": "任务状态查询失败", "detail": f"{type(exc).__name__}: {exc}"},
                    ensure_ascii=False,
                )
                yield f"event: error\ndata: {error}\n\n"
                return
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
# V36 双人对戏 / 试镜合集发布包（必须在 V5 catalog 通用路由之前注册，避免 /api/v5/{kind}/{id} 抢占）
app.include_router(character_dialogue_router.router)
app.include_router(character_showcase_router.router)
# V5 契约 API（/api/v5/*，独立前缀；不改变既有 V3/V4 接口）
app.include_router(v5_studio_router.router)
# V48 系列（同世界模板串成剧集，只读）
app.include_router(v5_series_router.router)
app.include_router(longform_router.router)
app.include_router(v5_catalog_router.router)
# V6 Agent API（/api/v6/agent/* + /media/agent_runs/*）
app.include_router(v6_agent_router.router)
# V50 Muse（MuseTalk 1.5）口型生成（/api/v6/muse/*）
app.include_router(muse_router.router)
# R5：视频模型优先级（GET /api/v6/video/provider → Muse → 本地 ComfyUI → 其它云端）
app.include_router(muse_router.video_router)
# V7 Cinema Studio API（/api/cinema/*，3D 电影制片工作台）
app.include_router(cinema3d_router.router)
# V8 16 模板 3D 化 API（/api/cinema/templates/*，复用 cinema3d 的任务队列）
app.include_router(cinema_templates_router.router)
# V29 批量导出发布包（/api/cinema/publish-packages/*）
app.include_router(publish_batches_router.router)
# V32 角色试镜视频（/api/v5/characters/{id}/audition + /api/auditions/*）
app.include_router(character_audition_router.router)
# V35 角色宣传片（/api/v5/characters/{id}/promo + /api/promos/*）
app.include_router(character_promo_router.router)
# V18 全流程编排 API（/api/pipeline/* + 项目切换 3D 模板）
app.include_router(pipeline_ui_router.router)
# V21 可视化创建 API（/api/v5/characters、/api/v5/templates 新建/删除）
app.include_router(v5_create_router.router)

config.OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)


class MediaStaticFiles(StaticFiles):
    """V26：/media 下的发布包 .zip 统一 Content-Type: application/zip。

    Windows 的 mimetypes 会把 .zip 映射成 application/x-zip-compressed，前端按
    application/zip 判断时不一致，这里显式覆盖。
    """

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        if isinstance(response, FileResponse) and str(path).lower().endswith(".zip"):
            response.media_type = "application/zip"
            response.headers["content-type"] = "application/zip"
        return response


@app.post("/api/settings/metaso-test", summary="测试 Metaso H3 Key（/v1/models）")
def metaso_key_test(payload: dict = Body(default=None)) -> dict:
    """用给定 key（或已保存的）请求 Metaso 的模型列表，验证 key 与网络。"""
    import urllib.error
    import urllib.request

    key = str((payload or {}).get("metaso_key") or "").strip() or (config.metaso_key() or "")
    if not key:
        return {"ok": False, "error": "未配置 Metaso Key"}
    base = "https://metaso.cn/api/openai"
    req = urllib.request.Request(f"{base}/v1/models",
                                 headers={"Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8") or "{}")
        models = [str(m.get("id")) for m in (data.get("data") or []) if m.get("id")]
        return {"ok": True, "models": models, "base": base}
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read().decode("utf-8", "replace")[:300]
        except Exception:  # noqa: BLE001
            detail = ""
        return {"ok": False, "status": exc.code, "error": detail or str(exc)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


app.mount(
    config.MEDIA_URL_PREFIX,
    MediaStaticFiles(directory=str(config.OUTPUTS_DIR)),
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
