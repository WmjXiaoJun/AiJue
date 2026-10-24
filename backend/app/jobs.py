"""后台任务：shot 生成 / render 渲染。

- 任务落库（Job 表），进程内守护线程执行，demo 不依赖 Redis/Celery；
- shot 任务调用 pipeline CLI gen-shot，render 任务调用 pipeline CLI render；
- render CLI 的 stdout 中 "PROGRESS <0-100> <message>" 行会实时写回 Job/Episode 进度；
- CLI 缺失或失败时任务置 failed 并记录 error，线程内异常全部兜住，绝不拖垮 API 进程。
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from sqlalchemy import func

from . import character_files, concept_files, config, models, seed
from .db import SessionLocal

logger = logging.getLogger(__name__)

ACTIVE_STATUSES = ("queued", "running")
_ERR_LIMIT = 2000
# ComfyUI 串行锁：concept（SDXL）与 h3（MiniMax H3）共用本机显卡，必须排队执行
GPU_LOCK = threading.Lock()
H3_TIMEOUT_SECONDS = 20 * 60  # 本地 H3 长任务超时（契约要求 >= 15 分钟）
METASO_TIMEOUT_SECONDS = 300  # MetaSo 云端测试 provider 超时（实测单条 ~23-25s）
LOCALIZE_TIMEOUT_SECONDS = 1800  # 多语种本地化（翻译 + TTS + 混音 + 编码）


def utcnow():
    return models.utcnow()


# --------------------------------------------------------------------------- #
# 外部命令执行
# --------------------------------------------------------------------------- #
def run_cli(
    cmd: List[str],
    cwd: Optional[str | Path] = None,
    on_line: Optional[Callable[[str], None]] = None,
    timeout: Optional[float] = None,
) -> Tuple[int, List[str], str]:
    """执行 CLI，实时回调 stdout 行。

    返回 (returncode, stdout_lines, stderr_text)。不会抛出 CLI 的业务失败，
    只有启动失败（如解释器不存在）才抛异常，由 run_job 兜底置 failed。
    timeout（秒）到时会终止子进程，用于 ComfyUI H3 等长任务。
    """
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUNBUFFERED", "1")
    stdout_lines: List[str] = []
    stderr_lines: List[str] = []

    def _pump(stream, sink: List[str], callback: Optional[Callable[[str], None]] = None) -> None:
        try:
            for raw in iter(stream.readline, ""):
                line = raw.rstrip("\r\n")
                sink.append(line)
                if callback is not None:
                    try:
                        callback(line)
                    except Exception:  # 回调失败不能影响子进程读取
                        logger.exception("任务进度回调失败")
        finally:
            try:
                stream.close()
            except Exception:
                pass

    proc = subprocess.Popen(  # noqa: S603 - demo 固定命令
        cmd,
        cwd=str(cwd or config.REPO_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    readers: List[threading.Thread] = []
    if proc.stdout is not None:
        reader = threading.Thread(target=_pump, args=(proc.stdout, stdout_lines, on_line), daemon=True)
        reader.start()
        readers.append(reader)
    if proc.stderr is not None:
        reader = threading.Thread(target=_pump, args=(proc.stderr, stderr_lines), daemon=True)
        reader.start()
        readers.append(reader)
    timed_out = False
    try:
        returncode = proc.wait(timeout=timeout) if timeout else proc.wait()
    except subprocess.TimeoutExpired:
        timed_out = True
        returncode = -9
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass
        logger.warning("命令超时 %ss，已终止: %s", timeout, cmd[:4])
    for reader in readers:
        reader.join(timeout=5)
    stderr_text = "\n".join(stderr_lines)
    if timed_out:
        stderr_text = (stderr_text + "\n[timeout] 命令超过 %.0fs，已终止" % (timeout or 0)).strip()
    return returncode, stdout_lines, stderr_text


def _last_json(lines: List[str]) -> Dict[str, Any]:
    for line in reversed(lines or []):
        text = (line or "").strip()
        if text.startswith("{") and text.endswith("}"):
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                continue
    return {}


def _parse_progress(line: str) -> Optional[Tuple[float, str]]:
    text = (line or "").strip()
    if not text.upper().startswith("PROGRESS"):
        return None
    parts = text.split(None, 2)
    if len(parts) < 2:
        return None
    try:
        value = float(parts[1])
    except (TypeError, ValueError):
        return None
    message = parts[2].strip() if len(parts) > 2 else ""
    return max(0.0, min(100.0, value)), message


def _format_error(stage: str, returncode: int, stderr: str, stdout_lines: List[str]) -> str:
    parts = [f"{stage} 失败（exit code {returncode}）"]
    stderr_text = (stderr or "").strip()
    if stderr_text:
        parts.append("stderr: " + stderr_text[-_ERR_LIMIT:])
    else:
        tail = " | ".join([line for line in (stdout_lines or []) if line.strip()][-5:])
        if tail:
            parts.append("stdout: " + tail[-_ERR_LIMIT:])
    return "；".join(parts)[: _ERR_LIMIT + 300]


# --------------------------------------------------------------------------- #
# 通用更新
# --------------------------------------------------------------------------- #
def _update(db, job: models.Job, **fields: Any) -> None:
    for key, value in fields.items():
        setattr(job, key, value)
    job.updated_at = utcnow()


def _fail(
    db,
    job: models.Job,
    error: str,
    shot: Optional[models.Shot] = None,
    episode: Optional[models.Episode] = None,
    character: Optional[models.Character] = None,
    localization: Optional[models.Localization] = None,
) -> None:
    message = (error or "任务失败").strip() or "任务失败"
    _update(db, job, status="failed", error=message, message=message[:200])
    if shot is not None:
        shot.status = "failed"
    if episode is not None:
        episode.status = "failed"
    if character is not None:
        character.status = "failed"
    if localization is not None:
        localization.status = "failed"
        meta = dict(localization.meta or {})
        meta["error"] = message
        localization.meta = meta
    db.commit()
    logger.warning("任务 %s 失败: %s", job.id, message[:300])


def _refresh_episode_stats(db, episode: Optional[models.Episode]) -> None:
    """重算集的镜头数与费用，并同步项目 spent。"""
    if episode is None:
        return
    db.flush()
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
    episode.shots_total = int(total)
    episode.shots_done = int(done)
    if episode.render_url:
        episode.progress = 100.0
    elif episode.shots_total:
        episode.progress = round(episode.shots_done * 100.0 / episode.shots_total, 1)
    total_cost = (
        db.query(func.coalesce(func.sum(models.ShotVersion.cost), 0.0))
        .join(models.Shot, models.Shot.id == models.ShotVersion.shot_id)
        .filter(models.Shot.episode_id == episode.id)
        .scalar()
    )
    episode.cost = round(float(total_cost or 0.0), 4)
    db.flush()
    project = db.get(models.Project, episode.project_id)
    if project is not None:
        project.spent = round(
            float(
                db.query(func.coalesce(func.sum(models.Episode.cost), 0.0))
                .filter(models.Episode.project_id == project.id)
                .scalar()
                or 0.0
            ),
            4,
        )


# --------------------------------------------------------------------------- #
# 任务调度
# --------------------------------------------------------------------------- #
def dispatch(job_id: str) -> None:
    """自动执行开关打开时，用守护线程跑任务。"""
    if not config.JOBS_AUTORUN:
        logger.info("JOBS_AUTORUN 已关闭，任务保持排队: %s", job_id)
        return
    thread = threading.Thread(target=run_job, args=(job_id,), daemon=True, name=f"job-{job_id}")
    thread.start()


def run_job(job_id: str) -> None:
    """任务入口：加载任务并执行，任何异常都兜底置 failed。"""
    try:
        with SessionLocal() as db:
            job = db.get(models.Job, job_id)
            if job is None:
                logger.warning("任务不存在: %s", job_id)
                return
            if job.status in ("done", "failed", "canceled"):
                return
            if job.type == "shot":
                run_shot_job(db, job)
            elif job.type == "render":
                run_render_job(db, job)
            elif job.type == "character":
                run_character_job(db, job)
            elif job.type == "concept":
                run_concept_job(db, job)
            elif job.type == "h3":
                run_h3_job(db, job)
            elif job.type == "export":
                run_export_job(db, job)
            elif job.type == "localize":
                run_localize_job(db, job)
            else:
                _fail(db, job, f"未知任务类型: {job.type}")
    except Exception as exc:  # noqa: BLE001 - 后台线程必须兜底
        logger.exception("任务 %s 执行异常", job_id)
        _mark_failed_isolated(job_id, exc)


def _mark_failed_isolated(job_id: str, exc: Exception) -> None:
    try:
        with SessionLocal() as db:
            job = db.get(models.Job, job_id)
            if job is not None:
                _update(db, job, status="failed", error=str(exc), message=str(exc)[:200])
                if job.type == "shot" and job.shot_id:
                    shot = db.get(models.Shot, job.shot_id)
                    if shot is not None:
                        shot.status = "failed"
                if job.type == "render" and job.episode_id:
                    episode = db.get(models.Episode, job.episode_id)
                    if episode is not None:
                        episode.status = "failed"
                if job.type == "character" and job.character_id:
                    character = db.get(models.Character, job.character_id)
                    if character is not None:
                        character.status = "failed"
                if job.type == "localize":
                    loc_id = (job.params or {}).get("localization_id")
                    loc = db.get(models.Localization, loc_id) if loc_id else None
                    if loc is not None:
                        loc.status = "failed"
                db.commit()
    except Exception:  # noqa: BLE001
        logger.exception("任务失败状态写回失败: %s", job_id)


# --------------------------------------------------------------------------- #
# shot 任务
# --------------------------------------------------------------------------- #
def _shot_payload(shot: models.Shot) -> Dict[str, Any]:
    return {
        "id": shot.id,
        "episode_id": shot.episode_id,
        "segment_id": shot.segment_id,
        "no": shot.no,
        "type": shot.type,
        "duration": shot.duration,
        "speaker": shot.speaker,
        "speaker_name": shot.speaker_name,
        "line": shot.line,
        "visual": shot.visual,
        "lower_third": shot.lower_third,
        "punchline": shot.punchline,
        "sfx": shot.sfx,
        "mood": shot.mood,
        "status": shot.status,
        "selected_version_id": shot.selected_version_id,
        "title": shot.title,
        "subtitle": shot.subtitle,
    }


def _asset_payload(asset: models.Asset) -> Dict[str, Any]:
    return {
        "id": asset.id,
        "episode_id": asset.episode_id,
        "role": asset.role,
        "type": asset.type,
        "name": asset.name,
        "title": asset.title,
        "persona": asset.persona,
        "voice": asset.voice,
        "accent_color": asset.accent_color,
        "images": list(asset.images or []),
        "locked": asset.locked,
    }


def _resolve_workflow_template(template: str) -> Any:
    """解析 settings.workflow_template，转成可 JSON 序列化的对象/JSON 文本。

    - JSON 文本 -> 解析成对象（pipeline 也接受对象）
    - 文件路径（相对仓库根 / contract / data / backend/app/workflows）-> 读取 JSON
    - 其它字符串 -> 原样返回，由 pipeline 报明确错误
    """
    text = str(template or "").strip()
    if not text:
        return text
    if text.startswith("{") or text.startswith("["):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text
    candidates = [
        Path(text),
        config.REPO_ROOT / text,
        config.CONTRACT_DIR / text,
        config.DATA_DIR / text,
        Path(__file__).resolve().parent / "workflows" / text,
    ]
    for candidate in candidates:
        try:
            if candidate.is_file():
                return json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return text
    return text


def _settings_payload(setting) -> Dict[str, Any]:
    """comfyui 模式需要的设置（pipeline CLI 无数据库，由 spec 传入）。"""
    return {
        "generation_mode": setting.generation_mode,
        "comfyui_url": setting.comfyui_url,
        "workflow_template": _resolve_workflow_template(setting.workflow_template),
        "resolution": setting.resolution,
        "fps": setting.fps,
        "tts_provider": setting.tts_provider,
        "voice_map": dict(setting.voice_map or {}),
        "h3_price_per_second": setting.h3_price_per_second,
    }


def _build_shot_spec(
    db,
    shot: models.Shot,
    episode: models.Episode,
    mode: str,
    params: Dict[str, Any],
    setting,
) -> Dict[str, Any]:
    assets = [_asset_payload(asset) for asset in db.query(models.Asset).filter(models.Asset.episode_id == episode.id).all()]
    return {
        "shot": _shot_payload(shot),
        "assets": assets,
        "episode": {
            "id": episode.id,
            "aspect": episode.aspect,
            "resolution": episode.resolution,
            "fps": episode.fps,
            "packaging": episode.packaging or {},
        },
        "mode": mode,
        "params": params,
        # 契约 shot_spec 之外补 settings：pipeline CLI 需要 comfyui_url/workflow_template，
        # 而它不连数据库，因此由后端把 /api/settings 带进 spec；mock 模式 pipeline 会忽略。
        "settings": _settings_payload(setting),
    }


def run_shot_job(db, job: models.Job) -> None:
    shot = db.get(models.Shot, job.shot_id) if job.shot_id else None
    if shot is None:
        _fail(db, job, "镜头不存在，无法生成")
        return
    episode = db.get(models.Episode, shot.episode_id)
    if episode is None:
        _fail(db, job, "镜头所属集不存在")
        return

    setting = seed.get_settings(db)
    mode = (setting.generation_mode or "mock").strip().lower()
    if mode not in ("mock", "comfyui"):
        mode = "mock"
    params = dict(job.params or {})

    _update(db, job, status="running", progress=3, message=f"准备镜头 {shot.id}")
    shot.status = "generating"
    db.commit()

    spec = _build_shot_spec(db, shot, episode, mode, params, setting)
    config.JOBS_TMP_DIR.mkdir(parents=True, exist_ok=True)
    spec_path = config.JOBS_TMP_DIR / f"{job.id}.json"
    spec_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")

    out_path = config.OUTPUTS_DIR / "episodes" / episode.id / "shots" / f"{shot.id}.mp4"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable,
        "-m",
        "pipeline.cli",
        "gen-shot",
        "--spec",
        str(spec_path),
        "--out",
        str(out_path),
    ]
    if mode == "mock":
        cmd.append("--mock")

    _update(db, job, progress=10, message="调用 pipeline gen-shot")
    db.commit()

    try:
        returncode, stdout_lines, stderr_text = run_cli(cmd)
    except Exception as exc:  # noqa: BLE001 - CLI 无法启动
        _fail(db, job, f"gen-shot 启动失败: {exc}", shot=shot)
        return

    if returncode != 0:
        _fail(db, job, _format_error("gen-shot", returncode, stderr_text, stdout_lines), shot=shot)
        return

    result = _last_json(stdout_lines)
    actual_path = out_path
    if not actual_path.exists():
        reported = result.get("path")
        if reported:
            candidate = Path(str(reported))
            if not candidate.is_absolute():
                candidate = config.REPO_ROOT / candidate
            if candidate.exists():
                actual_path = candidate
    if not actual_path.exists():
        _fail(db, job, f"gen-shot 未产出视频文件: {out_path}", shot=shot)
        return

    duration = float(result.get("duration") or shot.duration or 0.0)
    provider = str(result.get("provider") or mode)
    if mode == "mock":
        cost = 0.0
    else:
        cost = round(float(setting.h3_price_per_second or 0.0) * duration, 4)

    latest = (
        db.query(func.max(models.ShotVersion.version))
        .filter(models.ShotVersion.shot_id == shot.id)
        .scalar()
        or 0
    )
    version = models.ShotVersion(
        id=models.new_id("sv"),
        shot_id=shot.id,
        version=int(latest) + 1,
        status="ready",
        video_url=config.media_url(actual_path),
        thumb_url=None,
        cost=cost,
        params={"provider": provider, "mode": mode, "duration": duration, "params": params},
    )
    db.add(version)
    db.flush()

    shot.selected_version_id = version.id
    shot.status = "ready"
    _update(
        db,
        job,
        status="done",
        progress=100,
        message=f"镜头 {shot.id} 生成完成（{provider}）",
        output_url=version.video_url,
        cost=cost,
        error=None,
    )
    db.commit()
    _refresh_episode_stats(db, episode)
    db.commit()
    logger.info("shot 任务完成: job=%s shot=%s cost=%s", job.id, shot.id, cost)


# --------------------------------------------------------------------------- #
# render 任务
# --------------------------------------------------------------------------- #
def _serialize_episode(db, episode: models.Episode, job: models.Job) -> Path:
    """把 DB 里的实际集序列化成 pipeline render 需要的 episode JSON。

    结构对齐 contract/demo_episode.json（episode/assets/shots/segments），
    保证任意集（如 ep_002）渲染时不会误用 demo 的 52 镜头。
    """
    assets = (
        db.query(models.Asset)
        .filter(models.Asset.episode_id == episode.id)
        .order_by(models.Asset.created_at.asc())
        .all()
    )
    shots = (
        db.query(models.Shot)
        .filter(models.Shot.episode_id == episode.id)
        .order_by(models.Shot.no.asc())
        .all()
    )
    shot_payload = []
    for shot in shots:
        shot_payload.append(
            {
                "id": shot.id,
                "episode_id": shot.episode_id,
                "segment": shot.segment_id,
                "segment_id": shot.segment_id,
                "no": shot.no,
                "type": shot.type,
                "duration": shot.duration,
                "speaker": shot.speaker,
                "speaker_name": shot.speaker_name,
                "line": shot.line,
                "visual": shot.visual,
                "lower_third": shot.lower_third,
                "punchline": shot.punchline,
                "sfx": shot.sfx,
                "mood": shot.mood,
                "title": shot.title,
                "subtitle": shot.subtitle,
            }
        )
    project = db.get(models.Project, episode.project_id)
    data: Dict[str, Any] = {
        "episode": {
            "id": episode.id,
            "no": episode.no,
            "title": episode.title,
            "target_duration": episode.target_duration,
            "aspect": episode.aspect,
            "resolution": episode.resolution,
            "fps": episode.fps,
            "status": episode.status,
            "packaging": episode.packaging or {},
        },
        "assets": [
            {
                "id": asset.id,
                "episode_id": asset.episode_id,
                "role": asset.role,
                "type": asset.type,
                "name": asset.name,
                "title": asset.title,
                "persona": asset.persona,
                "voice": asset.voice,
                "accent_color": asset.accent_color,
                "images": list(asset.images or []),
                "locked": asset.locked,
            }
            for asset in assets
        ],
        "shots": shot_payload,
        "total_duration": round(sum(float(shot.duration or 0.0) for shot in shots), 3),
    }
    if project is not None:
        data["project"] = {
            "id": project.id,
            "name": project.name,
            "format_id": project.format_id,
            "aspect": project.aspect,
        }
        format_path = config.FORMATS_DIR / f"{project.format_id}.json"
        if format_path.is_file():
            try:
                fmt = json.loads(format_path.read_text(encoding="utf-8"))
                data["segments"] = fmt.get("segments") or []
            except (OSError, json.JSONDecodeError):
                logger.warning("读取格式模板失败: %s", format_path)

    config.JOBS_TMP_DIR.mkdir(parents=True, exist_ok=True)
    episode_path = config.JOBS_TMP_DIR / f"{job.id}_episode.json"
    episode_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return episode_path


def run_render_job(db, job: models.Job) -> None:
    episode = db.get(models.Episode, job.episode_id) if job.episode_id else None
    if episode is None:
        _fail(db, job, "成片所属集不存在")
        return

    workdir = config.OUTPUTS_DIR / "episodes" / episode.id
    workdir.mkdir(parents=True, exist_ok=True)
    out_path = workdir / "final.mp4"

    rel_workdir = Path("outputs") / "episodes" / episode.id
    rel_out = rel_workdir / "final.mp4"

    _update(db, job, status="running", progress=1, message="启动渲染")
    episode.status = "rendering"
    episode.progress = max(float(episode.progress or 0.0), 1.0)
    db.commit()

    episode_spec_path = _serialize_episode(db, episode, job)
    cmd = [
        sys.executable,
        "-m",
        "pipeline.cli",
        "render",
        "--episode",
        str(episode_spec_path),
        "--workdir",
        str(rel_workdir).replace("\\", "/"),
        "--out",
        str(rel_out).replace("\\", "/"),
    ]

    state = {"progress": 1.0}

    def on_line(line: str) -> None:
        parsed = _parse_progress(line)
        if not parsed:
            return
        value, message = parsed
        if value < state["progress"] + 1.0 and value < 100.0:
            return
        state["progress"] = value
        job.progress = value
        if message:
            job.message = message[:200]
        job.updated_at = utcnow()
        episode.progress = value
        db.commit()

    try:
        returncode, stdout_lines, stderr_text = run_cli(cmd, on_line=on_line)
    except Exception as exc:  # noqa: BLE001 - CLI 无法启动
        _fail(db, job, f"render 启动失败: {exc}", episode=episode)
        return

    if returncode != 0:
        _fail(db, job, _format_error("render", returncode, stderr_text, stdout_lines), episode=episode)
        return

    if not out_path.exists():
        result = _last_json(stdout_lines)
        reported = result.get("path")
        if reported:
            candidate = Path(str(reported))
            if not candidate.is_absolute():
                candidate = config.REPO_ROOT / candidate
            if candidate.exists():
                out_path = candidate
    if not out_path.exists():
        _fail(db, job, f"render 未产出成片: {out_path}", episode=episode)
        return

    result = _last_json(stdout_lines)
    output_url = config.media_url(out_path)
    episode.render_url = output_url
    episode.status = "ready"
    _update(
        db,
        job,
        status="done",
        progress=100,
        message="成片渲染完成",
        output_url=output_url,
        cost=0.0,
        error=None,
    )
    db.commit()
    _refresh_episode_stats(db, episode)
    episode.progress = 100.0
    db.commit()
    logger.info("render 任务完成: job=%s episode=%s path=%s", job.id, episode.id, out_path)


# --------------------------------------------------------------------------- #
# character 任务（角色工坊：调用 studio3d Blender CLI）
# --------------------------------------------------------------------------- #
def _character_styles(character: models.Character, styles: Optional[List[str]] = None) -> List[str]:
    chosen = [str(item).strip() for item in (styles or []) if str(item).strip()]
    if chosen:
        return list(dict.fromkeys(chosen))
    style = character.style or {}
    if isinstance(style, str):
        style = {"primary": style}
    if isinstance(style, dict):
        variants = [str(item) for item in (style.get("variants") or []) if item]
        primary = style.get("primary")
        if primary and str(primary) not in variants:
            variants.insert(0, str(primary))
        if variants:
            return list(dict.fromkeys(variants))
    return ["3d_toon"]


def enqueue_character_job(db, character: models.Character, styles: Optional[List[str]] = None):
    """入队角色生成任务；同一角色已有排队/运行任务时复用。返回 (job, created)。"""
    active = (
        db.query(models.Job)
        .filter(
            models.Job.type == "character",
            models.Job.character_id == character.id,
            models.Job.status.in_(ACTIVE_STATUSES),
        )
        .order_by(models.Job.created_at.desc())
        .first()
    )
    if active is not None:
        return active, False
    chosen_styles = _character_styles(character, styles)
    episode = (
        db.query(models.Episode)
        .filter(models.Episode.project_id == character.project_id)
        .order_by(models.Episode.no.asc())
        .first()
    )
    job = models.Job(
        id=models.new_id("job"),
        type="character",
        status="queued",
        progress=0.0,
        message="角色生成排队中",
        episode_id=episode.id if episode is not None else None,
        shot_id=None,
        character_id=character.id,
        params={"styles": chosen_styles},
    )
    db.add(job)
    character.status = "generating"
    db.commit()
    dispatch(job.id)
    return job, True


def run_character_job(db, job: models.Job) -> None:
    character = db.get(models.Character, job.character_id) if job.character_id else None
    if character is None:
        _fail(db, job, "角色不存在，无法生成")
        return

    styles = _character_styles(character, (job.params or {}).get("styles"))
    _update(db, job, status="running", progress=3, message=f"准备生成角色 {character.id}")
    character.status = "generating"
    db.commit()

    outdir = character_files.character_dir(character.id)
    outdir.mkdir(parents=True, exist_ok=True)
    spec = dict(character.spec or {})
    spec.setdefault("id", character.id)
    spec.setdefault("name", character.name)
    spec.setdefault("title", character.title)
    spec.setdefault("persona", character.persona)
    spec.setdefault("voice", character.voice)
    spec.setdefault("accent_color", character.accent_color)
    if not spec.get("style"):
        spec["style"] = character.style or {"primary": "3d_toon", "variants": ["3d_toon"]}
    config.JOBS_TMP_DIR.mkdir(parents=True, exist_ok=True)
    spec_path = config.JOBS_TMP_DIR / f"{job.id}.json"
    spec_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")

    cmd = [
        sys.executable,
        "-m",
        "studio3d.cli",
        "create",
        "--spec",
        str(spec_path),
        "--outdir",
        str(outdir),
        "--styles",
        ",".join(styles),
    ]
    _update(db, job, progress=10, message="调用 studio3d Blender 生成角色")
    db.commit()

    try:
        returncode, stdout_lines, stderr_text = run_cli(cmd)
    except Exception as exc:  # noqa: BLE001 - CLI 无法启动
        _fail(db, job, f"studio3d 启动失败: {exc}", character=character)
        return

    if returncode != 0:
        _fail(
            db,
            job,
            _format_error("studio3d create", returncode, stderr_text, stdout_lines),
            character=character,
        )
        return

    result = _last_json(stdout_lines)
    if result.get("ok") is False:
        _fail(
            db,
            job,
            f"studio3d 生成失败: {result.get('error') or result}",
            character=character,
        )
        return

    data = character_files.scan(character.id)
    if not data.get("portrait"):
        _fail(
            db,
            job,
            f"studio3d 未产出 portrait.png（outdir={outdir}）",
            character=character,
        )
        return

    character.portrait_url = data["portrait"]
    character.sheet_url = data["sheet"]
    character.turntable_url = data["turntable"]
    character.expressions = data["expressions"]
    character.status = "ready"
    asset = db.get(models.Asset, character.asset_id) if character.asset_id else None
    if asset is not None:
        asset.images = [data["portrait"]]
    _update(
        db,
        job,
        status="done",
        progress=100,
        message=f"角色 {character.id} 生成完成",
        output_url=data["portrait"],
        cost=0.0,
        error=None,
    )
    db.commit()
    logger.info("character 任务完成: job=%s character=%s", job.id, character.id)


# --------------------------------------------------------------------------- #
# concept 任务（AI 概念图：调用 imagegen CLI，ComfyUI/SDXL）
# --------------------------------------------------------------------------- #
CONCEPT_MODES = ("portrait", "three_view", "expressions", "scene", "sheet", "all")
CONCEPT_STYLES = ("anime", "illustrious", "guoman")


def _character_spec_payload(character: models.Character) -> Dict[str, Any]:
    """把 Character 还原成 contract/CHARACTER.md 的 spec JSON（供 CLI 使用）。"""
    spec = dict(character.spec or {})
    spec.setdefault("id", character.id)
    spec.setdefault("name", character.name)
    spec.setdefault("title", character.title)
    spec.setdefault("persona", character.persona)
    spec.setdefault("voice", character.voice)
    spec.setdefault("accent_color", character.accent_color)
    if not spec.get("style"):
        spec["style"] = character.style or {"primary": "3d_toon", "variants": ["3d_toon"]}
    return spec


def enqueue_concept_job(
    db,
    character: models.Character,
    modes: Optional[List[str]] = None,
    style: Optional[str] = None,
    prompt_extra: Optional[str] = None,
    seed: Optional[int] = None,
):
    """入队概念图任务；同一角色已有活跃概念图任务时复用。返回 (job, created)。"""
    active = (
        db.query(models.Job)
        .filter(
            models.Job.type == "concept",
            models.Job.character_id == character.id,
            models.Job.status.in_(ACTIVE_STATUSES),
        )
        .order_by(models.Job.created_at.desc())
        .first()
    )
    if active is not None:
        return active, False
    chosen_modes = [str(mode) for mode in (modes or []) if str(mode)] or ["portrait", "three_view"]
    episode = (
        db.query(models.Episode)
        .filter(models.Episode.project_id == character.project_id)
        .order_by(models.Episode.no.asc())
        .first()
    )
    job = models.Job(
        id=models.new_id("job"),
        type="concept",
        status="queued",
        progress=0.0,
        message="概念图生成排队中",
        episode_id=episode.id if episode is not None else None,
        character_id=character.id,
        params={
            "modes": chosen_modes,
            "style": style,
            "prompt_extra": prompt_extra,
            "seed": seed,
        },
    )
    db.add(job)
    db.commit()
    dispatch(job.id)
    return job, True


def run_concept_job(db, job: models.Job) -> None:
    character = db.get(models.Character, job.character_id) if job.character_id else None
    if character is None:
        _fail(db, job, "角色不存在，无法生成概念图")
        return
    params = dict(job.params or {})
    modes = [str(mode) for mode in (params.get("modes") or ["portrait", "three_view"]) if str(mode)]
    style = params.get("style")
    prompt_extra = params.get("prompt_extra")
    seed = params.get("seed")

    _update(db, job, status="running", progress=2, message=f"准备生成概念图（{','.join(modes)}）")
    db.commit()

    outdir = concept_files.concept_dir(character.id)
    outdir.mkdir(parents=True, exist_ok=True)
    config.JOBS_TMP_DIR.mkdir(parents=True, exist_ok=True)
    spec_path = config.JOBS_TMP_DIR / f"{job.id}_concept_spec.json"
    spec_path.write_text(
        json.dumps(_character_spec_payload(character), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    total = len(modes)
    _update(db, job, progress=4, message="等待 ComfyUI 空闲")
    db.commit()
    with GPU_LOCK:
        for index, mode in enumerate(modes):
            cmd = [
                sys.executable,
                "-m",
                "imagegen.cli",
                "generate",
                "--spec",
                str(spec_path),
                "--outdir",
                str(outdir),
                "--mode",
                mode,
            ]
            if style:
                cmd += ["--style", str(style)]
            if prompt_extra:
                cmd += ["--prompt-extra", str(prompt_extra)]
            if seed is not None:
                cmd += ["--seed", str(seed)]
            _update(
                db,
                job,
                progress=round(5 + index * 85.0 / max(1, total), 1),
                message=f"imagegen 生成 {mode}（{index + 1}/{total}）",
            )
            db.commit()
            try:
                returncode, stdout_lines, stderr_text = run_cli(cmd, timeout=900)
            except Exception as exc:  # noqa: BLE001 - CLI 无法启动
                _fail(db, job, f"imagegen 启动失败: {exc}")
                return
            if returncode != 0:
                _fail(
                    db,
                    job,
                    _format_error(
                        f"imagegen generate --mode {mode}", returncode, stderr_text, stdout_lines
                    ),
                )
                return

    data = concept_files.scan(character.id)
    if not data.get("portrait"):
        _fail(db, job, f"imagegen 未产出概念图 portrait.png（outdir={outdir}）")
        return
    character.concept_portrait_url = data["portrait"]
    character.concept_sheet_url = data["sheet"]
    character.concept_three_view = data["three_view"]
    character.concept_expressions = data["expressions"]
    _update(
        db,
        job,
        status="done",
        progress=100,
        message=f"概念图生成完成（{','.join(modes)}）",
        output_url=data["portrait"],
        cost=0.0,
        error=None,
    )
    db.commit()
    logger.info("concept 任务完成: job=%s character=%s", job.id, character.id)


# --------------------------------------------------------------------------- #
# h3 任务（概念图/立绘 -> MiniMax H3 动画，ComfyUI 串行 + 长超时）
# --------------------------------------------------------------------------- #
def _tool_path(env_name: str, default_rel: str) -> Path:
    override = os.environ.get(env_name)
    if override:
        return Path(override)
    return config.REPO_ROOT / default_rel


def ffmpeg_bin() -> Path:
    return _tool_path("FFMPEG_BIN", "tools/node_modules/ffmpeg-static/ffmpeg.exe")


def ffprobe_bin() -> Path:
    return _tool_path("FFPROBE_BIN", "tools/node_modules/ffprobe-static/bin/win32/x64/ffprobe.exe")


def probe_video_duration(path: str | Path) -> Optional[float]:
    try:
        proc = subprocess.run(
            [
                str(ffprobe_bin()),
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=nw=1:nk=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return float((proc.stdout or "").strip())
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def extract_video_frame(video_path: str | Path, out_path: str | Path, position: float = 0.5) -> bool:
    """用 ffmpeg 抽取视频中段帧（H3 动画 -> h3_portrait.png）。失败返回 False。"""
    duration = probe_video_duration(video_path) or 0.0
    target = Path(out_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        str(ffmpeg_bin()),
        "-y",
        "-ss",
        f"{max(0.0, duration * position):.3f}",
        "-i",
        str(video_path),
        "-frames:v",
        "1",
        str(target),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0 and target.is_file() and target.stat().st_size > 0


def enqueue_h3_job(
    db,
    character: models.Character,
    image_path: Optional[str] = None,
    prompt: Optional[str] = None,
):
    """入队 H3 动画任务；同一角色已有活跃 H3 任务时复用。返回 (job, created)。"""
    active = (
        db.query(models.Job)
        .filter(
            models.Job.type == "h3",
            models.Job.character_id == character.id,
            models.Job.status.in_(ACTIVE_STATUSES),
        )
        .order_by(models.Job.created_at.desc())
        .first()
    )
    if active is not None:
        return active, False
    episode = (
        db.query(models.Episode)
        .filter(models.Episode.project_id == character.project_id)
        .order_by(models.Episode.no.asc())
        .first()
    )
    setting = seed.get_settings(db)
    provider = str(setting.h3_provider or "local").strip().lower()
    if provider not in ("local", "metaso"):
        provider = "local"
    job = models.Job(
        id=models.new_id("job"),
        type="h3",
        status="queued",
        progress=0.0,
        message=f"H3 动画排队中（{provider}）",
        episode_id=episode.id if episode is not None else None,
        character_id=character.id,
        params={
            "image": str(image_path) if image_path else None,
            "prompt": prompt,
            "provider": provider,
        },
    )
    db.add(job)
    db.commit()
    dispatch(job.id)
    return job, True


def run_h3_job(db, job: models.Job) -> None:
    character = db.get(models.Character, job.character_id) if job.character_id else None
    if character is None:
        _fail(db, job, "角色不存在，无法生成 H3 动画")
        return
    params = dict(job.params or {})
    image_path = params.get("image")
    if not image_path or not Path(str(image_path)).is_file():
        _fail(db, job, "H3 动画缺少可用输入图片（请先生成概念图/立绘，或用 image 指定）")
        return
    prompt = str(params.get("prompt") or "").strip() or (
        f"{character.name}，原创动漫角色，轻微呼吸与眨眼，镜头缓慢推近，高质量动画"
    )
    provider = str(params.get("provider") or "local").strip().lower()
    if provider == "metaso":
        run_metaso_h3_job(db, job, character, str(image_path), prompt)
        return
    out_path = config.OUTPUTS_DIR / "h3" / f"{character.id}_h3.mp4"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    _update(db, job, status="running", progress=2, message="等待 ComfyUI 空闲（H3 串行）")
    db.commit()
    with GPU_LOCK:
        _update(db, job, progress=5, message="H3 视频生成中（ComfyUI）")
        db.commit()
        cmd = [
            sys.executable,
            "integrations/comfyui/run_h3.py",
            "--image",
            str(image_path),
            "--prompt",
            prompt,
            "--out",
            str(out_path),
            "--width",
            "768",
            "--height",
            "768",
            "--length",
            "124",
        ]
        try:
            returncode, stdout_lines, stderr_text = run_cli(cmd, timeout=H3_TIMEOUT_SECONDS)
        except Exception as exc:  # noqa: BLE001 - CLI 无法启动
            _fail(db, job, f"run_h3 启动失败: {exc}")
            return

    if returncode != 0:
        _fail(db, job, _format_error("run_h3", returncode, stderr_text, stdout_lines))
        return
    if not out_path.is_file() or out_path.stat().st_size == 0:
        _fail(db, job, f"run_h3 未产出视频: {out_path}")
        return

    video_url = config.media_url(out_path)
    character.h3_video_url = video_url
    portrait_path = character_files.character_dir(character.id) / "h3_portrait.png"
    try:
        if extract_video_frame(out_path, portrait_path):
            character.h3_portrait_url = config.media_url(portrait_path)
    except Exception:  # noqa: BLE001 - 帧提取失败不影响视频结果
        logger.exception("H3 中段帧提取失败: %s", out_path)

    _update(
        db,
        job,
        status="done",
        progress=100,
        message="H3 动画生成完成",
        output_url=video_url,
        cost=0.0,
        error=None,
    )
    db.commit()
    logger.info("h3 任务完成: job=%s character=%s", job.id, character.id)


def run_metaso_h3_job(
    db,
    job: models.Job,
    character: models.Character,
    image_path: str,
    prompt: str,
) -> None:
    """MetaSo 云端 H3（测试 provider）：输出 outputs/h3/{cid}_h3_metaso.mp4，不覆盖本地版本。"""
    setting = seed.get_settings(db)
    resolution = str(setting.metaso_resolution or "768P").strip() or "768P"
    if resolution not in ("768P", "2K"):
        resolution = "768P"
    try:
        duration = int(setting.metaso_duration or 5)
    except (TypeError, ValueError):
        duration = 5
    duration = max(4, min(15, duration))
    base_url = str(setting.metaso_base_url or "").strip() or "https://metaso.cn/api/minimax"

    out_path = config.OUTPUTS_DIR / "h3" / f"{character.id}_h3_metaso.mp4"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        "-m",
        "integrations.metaso.h3",
        "--image",
        image_path,
        "--prompt",
        prompt,
        "--out",
        str(out_path),
        "--resolution",
        resolution,
        "--duration",
        str(duration),
        "--base-url",
        base_url,
    ]
    _update(db, job, status="running", progress=3, message="MetaSo 云端 H3 生成中")
    db.commit()
    try:
        returncode, stdout_lines, stderr_text = run_cli(cmd, timeout=METASO_TIMEOUT_SECONDS)
    except Exception as exc:  # noqa: BLE001 - CLI 无法启动
        _fail(db, job, f"integrations.metaso.h3 启动失败: {exc}")
        return
    if returncode != 0:
        _fail(
            db,
            job,
            _format_error("integrations.metaso.h3", returncode, stderr_text, stdout_lines),
        )
        return
    if not out_path.is_file() or out_path.stat().st_size == 0:
        _fail(db, job, f"MetaSo 未产出视频: {out_path}")
        return

    url = config.media_url(out_path)
    character.h3_metaso_url = url
    result = _last_json(stdout_lines) or {}
    cost = 0.0
    try:
        cost = round(
            float(
                result.get("cost")
                or result.get("estimated_cost")
                or result.get("est_cost_cny")
                or 0.0
            ),
            4,
        )
    except (TypeError, ValueError):
        cost = 0.0
    _update(
        db,
        job,
        status="done",
        progress=100,
        message="H3 云端动画生成完成（MetaSo）",
        output_url=url,
        cost=cost,
        error=None,
    )
    db.commit()
    logger.info("metaso h3 任务完成: job=%s character=%s url=%s", job.id, character.id, url)


# --------------------------------------------------------------------------- #
# localize 任务（多语种本地化：调用 pipeline.localize）
# --------------------------------------------------------------------------- #
def enqueue_localize_job(db, episode: models.Episode, lang: str):
    """入队单语言本地化任务；同一集同语言已有活跃任务时复用。返回 (job, created)。"""
    lang = str(lang or "").strip().lower()
    if not lang:
        raise ValueError("lang 不能为空")
    active = None
    for candidate in (
        db.query(models.Job)
        .filter(
            models.Job.type == "localize",
            models.Job.episode_id == episode.id,
            models.Job.status.in_(ACTIVE_STATUSES),
        )
        .order_by(models.Job.created_at.desc())
        .all()
    ):
        if str((candidate.params or {}).get("lang") or "").strip().lower() == lang:
            active = candidate
            break
    localization = (
        db.query(models.Localization)
        .filter(
            models.Localization.episode_id == episode.id,
            models.Localization.lang == lang,
        )
        .first()
    )
    if localization is None:
        localization = models.Localization(
            id=models.new_id("loc"),
            episode_id=episode.id,
            lang=lang,
            status="draft",
            meta={},
        )
        db.add(localization)
        db.flush()
    else:
        localization.status = "draft"
    if active is not None:
        return active, False
    job = models.Job(
        id=models.new_id("job"),
        type="localize",
        status="queued",
        progress=0.0,
        message=f"本地化排队中（{lang}）",
        episode_id=episode.id,
        params={"lang": lang, "localization_id": localization.id},
    )
    db.add(job)
    db.commit()
    dispatch(job.id)
    return job, True


def run_localize_job(db, job: models.Job) -> None:
    episode = db.get(models.Episode, job.episode_id) if job.episode_id else None
    params = dict(job.params or {})
    lang = str(params.get("lang") or "").strip().lower()
    localization = (
        db.get(models.Localization, params.get("localization_id"))
        if params.get("localization_id")
        else None
    )
    if localization is None and episode is not None and lang:
        localization = (
            db.query(models.Localization)
            .filter(
                models.Localization.episode_id == episode.id,
                models.Localization.lang == lang,
            )
            .first()
        )
    if episode is None or localization is None or not lang:
        _fail(db, job, "本地化任务缺少集/语言/版本记录")
        return

    setting = seed.get_settings(db)
    # 默认 Ollama qwen2.5:7b；NLLB 兜底；mock 仅测试
    translator = str(setting.localize_translator or "ollama").strip().lower()
    if translator not in ("ollama", "nllb", "mock"):
        translator = "ollama"

    outdir = config.OUTPUTS_DIR / "episodes" / episode.id / "localized" / lang
    outdir.mkdir(parents=True, exist_ok=True)
    episode_json = _serialize_episode(db, episode, job)

    _update(db, job, status="running", progress=3, message=f"本地化处理中（{lang}）")
    localization.status = "rendering"
    db.commit()

    cmd = [
        sys.executable,
        "-m",
        "pipeline.localize",
        "--episode-json",
        str(episode_json),
        "--lang",
        lang,
        "--outdir",
        str(outdir),
        "--translator",
        translator,
    ]
    voices = setting.localize_voices or {}
    if isinstance(voices, dict) and voices:
        config.JOBS_TMP_DIR.mkdir(parents=True, exist_ok=True)
        voices_path = config.JOBS_TMP_DIR / f"{job.id}_localize_voices.json"
        voices_path.write_text(
            json.dumps(voices, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        cmd += ["--voices-json", str(voices_path)]
    try:
        returncode, stdout_lines, stderr_text = run_cli(cmd, timeout=LOCALIZE_TIMEOUT_SECONDS)
    except Exception as exc:  # noqa: BLE001 - CLI 无法启动
        _fail(db, job, f"pipeline.localize 启动失败: {exc}", localization=localization)
        return
    if returncode != 0:
        _fail(
            db,
            job,
            _format_error("pipeline.localize", returncode, stderr_text, stdout_lines),
            localization=localization,
        )
        return

    final_path = outdir / "final.mp4"
    if not final_path.is_file() or final_path.stat().st_size == 0:
        _fail(
            db,
            job,
            f"pipeline.localize 未产出 final.mp4（outdir={outdir}）",
            localization=localization,
        )
        return
    meta: Dict[str, Any] = {}
    meta_path = outdir / "meta.json"
    if meta_path.is_file():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            meta = {}
    srt_path = outdir / "subtitles.srt"
    video_url = config.media_url(final_path)
    localization.status = "ready"
    localization.video_url = video_url
    localization.srt_url = config.media_url(srt_path) if srt_path.is_file() else None
    localization.meta = meta if isinstance(meta, dict) else {}
    _update(
        db,
        job,
        status="done",
        progress=100,
        message=f"本地化完成（{lang}）",
        output_url=video_url,
        cost=0.0,
        error=None,
    )
    db.commit()
    logger.info("localize 任务完成: job=%s episode=%s lang=%s", job.id, episode.id, lang)


# --------------------------------------------------------------------------- #
# export 任务（剪映草稿导出：调用 pipeline.capcut）
# --------------------------------------------------------------------------- #
def enqueue_export_job(
    db,
    episode: models.Episode,
    manifest: Dict[str, Any],
    export_type: str = "capcut",
):
    """入队导出任务；同一集已有活跃导出任务时复用。返回 (job, created)。"""
    active = (
        db.query(models.Job)
        .filter(
            models.Job.type == "export",
            models.Job.episode_id == episode.id,
            models.Job.status.in_(ACTIVE_STATUSES),
        )
        .order_by(models.Job.created_at.desc())
        .first()
    )
    if active is not None:
        return active, False
    name = str(manifest.get("name") or f"造片局_EP{int(episode.no or 1):03d}")
    job = models.Job(
        id=models.new_id("job"),
        type="export",
        status="queued",
        progress=0.0,
        message="导出排队中",
        episode_id=episode.id,
        character_id=None,
        params={
            "manifest": manifest,
            "name": name,
            "export_type": export_type,
        },
    )
    db.add(job)
    db.commit()
    dispatch(job.id)
    return job, True


def run_export_job(db, job: models.Job) -> None:
    params = dict(job.params or {})
    manifest = dict(params.get("manifest") or {})
    name = str(params.get("name") or manifest.get("name") or "造片局_EP001")
    if not manifest:
        _fail(db, job, "导出清单为空，无法导出")
        return
    _update(db, job, status="running", progress=5, message=f"准备导出剪映草稿 {name}")
    db.commit()

    config.JOBS_TMP_DIR.mkdir(parents=True, exist_ok=True)
    manifest_path = config.JOBS_TMP_DIR / f"{job.id}_export_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    outdir = config.OUTPUTS_DIR / "exports"
    outdir.mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable,
        "-m",
        "pipeline.capcut",
        "export",
        "--manifest",
        str(manifest_path),
        "--outdir",
        str(outdir),
        "--name",
        name,
        "--copy-media",
    ]
    _update(db, job, progress=10, message="调用 pipeline.capcut export")
    db.commit()
    try:
        returncode, stdout_lines, stderr_text = run_cli(cmd, timeout=600)
    except Exception as exc:  # noqa: BLE001 - CLI 无法启动
        _fail(db, job, f"pipeline.capcut 启动失败: {exc}")
        return
    if returncode != 0:
        _fail(
            db,
            job,
            _format_error("pipeline.capcut export", returncode, stderr_text, stdout_lines),
        )
        return

    result = _last_json(stdout_lines)
    expected_dir = outdir / name
    draft_path: Optional[Path] = None
    for candidate in (result.get("draft_dir"), result.get("path"), result.get("outdir"), expected_dir):
        if not candidate:
            continue
        path = Path(str(candidate))
        if not path.is_absolute():
            path = config.REPO_ROOT / path
        if path.is_dir():
            draft_path = path
            break
    if draft_path is None:
        _fail(db, job, f"export 未产出剪映草稿目录（期望 {expected_dir}）")
        return
    try:
        output_url = draft_path.resolve().relative_to(config.REPO_ROOT.resolve()).as_posix()
    except ValueError:
        output_url = str(draft_path)
    _update(
        db,
        job,
        status="done",
        progress=100,
        message=f"剪映草稿已导出：{output_url}",
        output_url=output_url,
        cost=0.0,
        error=None,
    )
    db.commit()
    logger.info("export 任务完成: job=%s path=%s", job.id, output_url)


# --------------------------------------------------------------------------- #
# 入队 / 重试
# --------------------------------------------------------------------------- #
def enqueue_shot_job(db, shot: models.Shot, params: Optional[Dict[str, Any]] = None):
    """入队 shot 任务；同一镜头已有排队/运行任务时复用。返回 (job, created)。"""
    active = (
        db.query(models.Job)
        .filter(
            models.Job.shot_id == shot.id,
            models.Job.type == "shot",
            models.Job.status.in_(ACTIVE_STATUSES),
        )
        .order_by(models.Job.created_at.desc())
        .first()
    )
    if active is not None:
        return active, False

    job = models.Job(
        id=models.new_id("job"),
        type="shot",
        status="queued",
        progress=0.0,
        message="排队中",
        episode_id=shot.episode_id,
        shot_id=shot.id,
        params=params or {},
    )
    db.add(job)
    shot.status = "queued"
    db.commit()
    dispatch(job.id)
    return job, True


def enqueue_render_job(db, episode: models.Episode, params: Optional[Dict[str, Any]] = None):
    """入队 render 任务；同一集已有排队/运行任务时复用。返回 (job, created)。"""
    active = (
        db.query(models.Job)
        .filter(
            models.Job.episode_id == episode.id,
            models.Job.type == "render",
            models.Job.status.in_(ACTIVE_STATUSES),
        )
        .order_by(models.Job.created_at.desc())
        .first()
    )
    if active is not None:
        return active, False

    job = models.Job(
        id=models.new_id("job"),
        type="render",
        status="queued",
        progress=0.0,
        message="渲染排队中",
        episode_id=episode.id,
        shot_id=None,
        params=params or {},
    )
    db.add(job)
    episode.status = "rendering"
    db.commit()
    dispatch(job.id)
    return job, True


def retry_job(db, job: models.Job):
    """重置失败/完成的任务并重新入队。返回 (job, created)。"""
    if job.status == "running":
        return job, False
    job.status = "queued"
    job.progress = 0.0
    job.message = "重试排队中"
    job.error = None
    job.updated_at = utcnow()
    if job.type == "shot" and job.shot_id:
        shot = db.get(models.Shot, job.shot_id)
        if shot is not None:
            shot.status = "queued"
    if job.type == "render" and job.episode_id:
        episode = db.get(models.Episode, job.episode_id)
        if episode is not None:
            episode.status = "rendering"
    if job.type == "character" and job.character_id:
        character = db.get(models.Character, job.character_id)
        if character is not None:
            character.status = "generating"
    if job.type == "localize":
        loc_id = (job.params or {}).get("localization_id")
        loc = db.get(models.Localization, loc_id) if loc_id else None
        if loc is not None:
            loc.status = "draft"
    db.commit()
    dispatch(job.id)
    return job, True
