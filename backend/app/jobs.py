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

from . import config, models, seed
from .db import SessionLocal

logger = logging.getLogger(__name__)

ACTIVE_STATUSES = ("queued", "running")
_ERR_LIMIT = 2000


def utcnow():
    return models.utcnow()


# --------------------------------------------------------------------------- #
# 外部命令执行
# --------------------------------------------------------------------------- #
def run_cli(
    cmd: List[str],
    cwd: Optional[str | Path] = None,
    on_line: Optional[Callable[[str], None]] = None,
) -> Tuple[int, List[str], str]:
    """执行 pipeline CLI，实时回调 stdout 行。

    返回 (returncode, stdout_lines, stderr_text)。不会抛出 CLI 的业务失败，
    只有启动失败（如解释器不存在）才抛异常，由 run_job 兜底置 failed。
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
    returncode = proc.wait()
    for reader in readers:
        reader.join(timeout=5)
    return returncode, stdout_lines, "\n".join(stderr_lines)


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
) -> None:
    message = (error or "任务失败").strip() or "任务失败"
    _update(db, job, status="failed", error=message, message=message[:200])
    if shot is not None:
        shot.status = "failed"
    if episode is not None:
        episode.status = "failed"
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
                if job.shot_id:
                    shot = db.get(models.Shot, job.shot_id)
                    if shot is not None:
                        shot.status = "failed"
                if job.episode_id:
                    episode = db.get(models.Episode, job.episode_id)
                    if episode is not None:
                        episode.status = "failed"
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
def _episode_arg(episode: models.Episode) -> str:
    """render CLI 使用 contract/demo_episode.json（demo 固定输入）。"""
    try:
        return str(config.DEMO_EPISODE_PATH.relative_to(config.REPO_ROOT)).replace("\\", "/")
    except ValueError:
        return str(config.DEMO_EPISODE_PATH).replace("\\", "/")


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

    cmd = [
        sys.executable,
        "-m",
        "pipeline.cli",
        "render",
        "--episode",
        _episode_arg(episode),
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
    db.commit()
    dispatch(job.id)
    return job, True
