# -*- coding: utf-8 -*-
"""V32：角色试镜视频（立绘 + 角色声音 → H3 图生视频 / 口型同步）后端。

POST /api/v5/characters/{character}/audition   创建任务（202）
GET  /api/auditions/{job_id}                   查询任务
POST /api/auditions/{job_id}/cancel            取消（杀 H3 子进程）
GET  /api/v5/characters/{character}/auditions  最近试镜

流程：queued → running(stage: tts/crop/generate/done) → done/failed/canceled；
输出 outputs/character_demos/<character>_<job_id>.mp4（/media 可访问）；
持久化 outputs/audition_jobs/<job_id>.json；重启中断任务标 failed。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel

from .. import config, jobs
from . import v5_create

router = APIRouter(tags=["character-audition"])

_REPO = Path(config.REPO_ROOT)
_ASSETS = _REPO / "assets" / "v5" / "characters"
_JOBS_DIR = Path(config.OUTPUTS_DIR) / "audition_jobs"
_DEMO_DIR = Path(config.OUTPUTS_DIR) / "character_demos"
_H3_CLI = _REPO / "integrations" / "comfyui" / "run_h3_ref.py"

_PORTRAIT_BY_STYLE = {
    "anime": "portrait.png",
    "stylized_25d": "portrait_25d.png",
    "realistic": "portrait_real.png",
}
_STYLES = tuple(_PORTRAIT_BY_STYLE)
_DEFAULT_TEXT = "各位好，我是{label}。这是用我的角色立绘生成的一段试镜。"
_ESTIMATED_SEC = 200
_JOB_ID_RE = re.compile(r"^au_[a-f0-9]{12}$")

_LOCK = threading.RLock()
_JOBS: Dict[str, Dict[str, Any]] = {}
_PROCS: Dict[str, subprocess.Popen] = {}
_ACTIVE_JOB: Optional[str] = None


# --------------------------------------------------------------------------- #
# 持久化 / 视图
# --------------------------------------------------------------------------- #
def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _persist(job: Dict[str, Any]) -> None:
    try:
        _JOBS_DIR.mkdir(parents=True, exist_ok=True)
        target = _JOBS_DIR / f"{job['id']}.json"
        tmp = target.with_name(f"{target.name}.tmp-{os.getpid()}-{threading.get_ident()}")
        tmp.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, target)
    except OSError:
        pass


def _elapsed(job: Dict[str, Any]) -> float:
    created = job.get("created_ts")
    if not isinstance(created, (int, float)):
        return 0.0
    finished = job.get("finished_ts")
    end = float(finished) if isinstance(finished, (int, float)) else time.time()
    return round(max(0.0, end - float(created)), 1)


def _set_stage(job: Dict[str, Any], stage: str, progress: Optional[int] = None) -> None:
    """更新 stage/progress，并记录完整 stage 历史（tts/crop/generate/done）。"""
    job["stage"] = stage
    if progress is not None:
        job["progress"] = int(progress)
    history = job.setdefault("stage_history", [])
    if not history or history[-1] != stage:
        history.append(stage)


def _view(job: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": job.get("id"),
        "character": job.get("character"),
        "style": job.get("style"),
        "status": job.get("status"),
        "stage": job.get("stage"),
        "stage_history": list(job.get("stage_history") or []),
        "progress": job.get("progress", 0),
        "text": job.get("text"),
        "output_url": job.get("output_url"),
        "duration_sec": job.get("duration_sec"),
        "size_bytes": job.get("size_bytes"),
        "width": job.get("width"),
        "height": job.get("height"),
        "seed": job.get("seed"),
        "portrait_file": job.get("portrait_file"),
        "tts_engine": job.get("tts_engine"),
        "error": job.get("error"),
        "warnings": job.get("warnings") or [],
        "created_at": job.get("created_at"),
        "started_at": job.get("started_at"),
        "finished_at": job.get("finished_at"),
        "elapsed_sec": _elapsed(job),
    }


# --------------------------------------------------------------------------- #
# 媒体 / 图像工具
# --------------------------------------------------------------------------- #
def _ffprobe_json(path: Path) -> Dict[str, Any]:
    try:
        proc = subprocess.run(
            [str(jobs.ffprobe_bin()), "-v", "error", "-show_streams", "-show_format",
             "-of", "json", str(path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
        )
        return json.loads(proc.stdout or "{}")
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return {}


def _media_info(path: Path) -> Dict[str, Any]:
    data = _ffprobe_json(path)
    streams = data.get("streams") or []
    video = next((row for row in streams if row.get("codec_type") == "video"), {})
    audio = next((row for row in streams if row.get("codec_type") == "audio"), None)
    fmt = data.get("format") if isinstance(data.get("format"), dict) else {}
    return {
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
        "fps": video.get("r_frame_rate"),
        "has_audio": audio is not None,
        "duration_sec": round(float(fmt.get("duration") or 0.0), 3),
        "size_bytes": int(fmt.get("size") or 0),
    }


def _image_size(path: Path) -> tuple:
    data = _ffprobe_json(path)
    stream = next((row for row in data.get("streams") or []
                   if row.get("codec_type") == "video"), {})
    return int(stream.get("width") or 0), int(stream.get("height") or 0)


def _crop_ref_16x9(source: Path, dest: Path, width: int, height: int) -> None:
    """立绘 → 16:9 参考图（V31 验证：顶部偏移 40px，人脸居中），LANCZOS 放大。"""
    src_w, src_h = _image_size(source)
    if src_w <= 0 or src_h <= 0:
        raise RuntimeError(f"无法读取立绘尺寸: {source}")
    crop_h = max(2, int(src_w * 9 / 16))
    if crop_h > src_h:
        crop_w = max(2, int(src_h * 16 / 9))
        x = max(0, (src_w - crop_w) // 2)
        y = 0
        crop_filter = f"crop={crop_w}:{src_h}:{x}:0"
    else:
        top = max(0, min(src_h - crop_h, 40))
        crop_filter = f"crop={src_w}:{crop_h}:0:{top}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [str(jobs.ffmpeg_bin()), "-y", "-hide_banner", "-loglevel", "error",
         "-i", str(source), "-vf", f"{crop_filter},scale={width}:{height}:flags=lanczos",
         "-frames:v", "1", str(dest)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
    )
    if proc.returncode != 0 or not dest.is_file() or dest.stat().st_size <= 0:
        tail = "\n".join((proc.stderr or "").strip().splitlines()[-6:])
        raise RuntimeError(f"16:9 裁图失败（returncode={proc.returncode}）：{tail[-500:]}")


# --------------------------------------------------------------------------- #
# 提示词
# --------------------------------------------------------------------------- #
_STYLE_HINT = {
    "realistic": (
        "Photorealistic live-action cinematic look, natural skin texture, realistic lighting, "
        "film still, no anime, no cartoon, no illustration"
    ),
    "anime": (
        "High-quality anime cel-shaded look, clean line art, vibrant colors, detailed face, "
        "no photorealistic real human, no live action"
    ),
    "stylized_25d": (
        "2.5D stylized CG animation look, detailed skin shading, cinematic lighting, "
        "semi-realistic anime 3D render, no flat cartoon, no live action photo"
    ),
}


def _build_prompt(character: dict, style: str, prompt_extra: str) -> str:
    label = str(character.get("label") or character.get("id") or "")
    app = character.get("appearance") if isinstance(character.get("appearance"), dict) else {}
    outfit = app.get("outfit") if isinstance(app.get("outfit"), dict) else {}
    identity = f"Subject 1 is the character shown in the reference image ({label})"
    details = []
    if app.get("gender"):
        details.append(str(app.get("gender")))
    if app.get("age"):
        details.append(f"aged about {app.get('age')}")
    if app.get("hair_style"):
        details.append(f"{str(app.get('hair_style')).replace('_', ' ')} hair")
    if outfit.get("type"):
        details.append(f"wearing {str(outfit.get('type')).replace('_', ' ')}")
    identity += (", " + ", ".join(details)) if details else ""
    identity += "; preserve the exact face, hairstyle, hair color, outfit, age and art style."
    summary = (
        "One continuous medium close-up of Subject 1 speaking naturally and directly to the camera, "
        "lips moving in sync with the reference speech audio, subtle blinking, eyebrow movement and "
        "small head nods while talking; stable identity, hairstyle, outfit and composition; "
        f"{_STYLE_HINT.get(style, _STYLE_HINT['anime'])}; 16:9 cinematic studio background; "
        "no text, no watermark."
    )
    if prompt_extra:
        summary += " " + prompt_extra
    return (
        f"subject_definitions: {identity}\n\n"
        f"summary: {summary}\n\n"
        "overall_soundscape: The reference speech audio, close-mic voice, quiet studio room tone.\n\n"
        "non_diegetic_music: N/A"
    )


# --------------------------------------------------------------------------- #
# 任务执行
# --------------------------------------------------------------------------- #
def _kill_process(proc: Optional[subprocess.Popen]) -> None:
    if proc is None or proc.poll() is not None:
        return
    try:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                       capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        try:
            proc.kill()
        except OSError:
            pass


def _run_audition(job_id: str) -> None:
    global _ACTIVE_JOB
    job = _JOBS.get(job_id)
    if not job:
        return
    with _LOCK:
        if job.get("status") == "canceled":
            job["finished_at"] = _now()
            job["finished_ts"] = time.time()
            _persist(job)
            if _ACTIVE_JOB == job_id:
                _ACTIVE_JOB = None
            return
        job.update(status="running", started_at=_now(),
                   started_ts=time.time(), error=None)
        _set_stage(job, "tts", 5)
        _persist(job)
    warnings: List[str] = job.get("warnings") or []
    proc: Optional[subprocess.Popen] = None
    try:
        character = (job.get("character_data") or {})
        style = str(job.get("style") or "anime")
        text = str(job.get("text") or "")
        width = int(job.get("width") or 1024)
        height = int(job.get("height") or 576)
        workdir = _JOBS_DIR / f"{job_id}_work"
        workdir.mkdir(parents=True, exist_ok=True)
        wav = workdir / "line.wav"

        # 1) TTS（breeze 优先 / edge 兜底；24k mono）
        tts = v5_create._synthesize_line(character, text, wav, warnings)
        duration = 0.0
        try:
            from pipeline import utils  # noqa: PLC0415

            duration = float(utils.probe_duration(wav) or 0.0)
        except Exception:  # noqa: BLE001
            duration = 0.0
        if duration <= 0:
            raise RuntimeError("TTS 音频时长为 0，无法生成试镜")
        with _LOCK:
            if job.get("status") == "canceled":
                raise _AuditionCanceled()
            job.update(tts_engine=tts.get("engine"),
                       duration_sec=round(duration, 3))
            _set_stage(job, "crop", 20)
            _persist(job)

        # 2) 立绘裁 16:9（人脸居中，顶部偏移）
        portrait = _ASSETS / str(job.get("character")) / str(job.get("portrait_file"))
        ref = workdir / "ref_16x9.png"
        _crop_ref_16x9(portrait, ref, width, height)
        with _LOCK:
            if job.get("status") == "canceled":
                raise _AuditionCanceled()
            job.update(stage="generate", progress=30)
            _set_stage(job, "generate", 30)
            _persist(job)

        # 3) H3 audio_ref（1024×576 / duration=音频+0.1）
        out = _DEMO_DIR / f"{job.get('character')}_{job_id}.mp4"
        _DEMO_DIR.mkdir(parents=True, exist_ok=True)
        prompt = _build_prompt(character, style, str(job.get("prompt_extra") or ""))
        cmd = [
            sys.executable, str(_H3_CLI), "--mode", "audio_ref",
            "--image", str(ref), "--audio", str(wav), "--out", str(out),
            "--duration", f"{duration + 0.1:.3f}",
            "--width", str(width), "--height", str(height),
            "--fps", "25", "--seed", str(job.get("seed") or 42),
            "--prompt", prompt, "--timeout", "1800",
        ]
        proc = subprocess.Popen(
            cmd, cwd=str(_REPO), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
        )
        with _LOCK:
            _PROCS[job_id] = proc
            job["h3_pid"] = proc.pid
            _persist(job)
        gen_started = time.time()
        tail: List[str] = []
        last_persist = 0.0
        while True:
            with _LOCK:
                canceled = job.get("status") == "canceled"
            if canceled:
                _kill_process(proc)
                break
            line = proc.stdout.readline() if proc.stdout is not None else ""
            if line:
                tail.append(line.strip())
                tail = tail[-40:]
                now = time.time()
                if now - last_persist > 5:
                    with _LOCK:
                        job["progress"] = min(95, 35 + int((now - gen_started) / 240 * 60))
                        _persist(job)
                    last_persist = now
                continue
            if proc.poll() is not None:
                break
            time.sleep(0.2)
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            _kill_process(proc)
        with _LOCK:
            _PROCS.pop(job_id, None)
            canceled = job.get("status") == "canceled"
        if canceled:
            try:
                out.unlink(missing_ok=True)
            except OSError:
                pass
            raise _AuditionCanceled()
        if proc.returncode != 0:
            tail_text = "\n".join(tail[-12:])
            raise RuntimeError(f"H3 生成失败（returncode={proc.returncode}）：{tail_text[-900:]}")
        if not out.is_file() or out.stat().st_size <= 0:
            raise RuntimeError("H3 未产出视频文件")

        # 4) 完成
        info = _media_info(out)
        with _LOCK:
            if job.get("status") == "canceled":
                raise _AuditionCanceled()
            stat = out.stat()
            job.update(
                status="done",
                output_url=f"/media/character_demos/{out.name}?t={int(stat.st_mtime)}",
                output_path=str(out),
                duration_sec=round(float(info.get("duration_sec") or duration), 3),
                size_bytes=int(stat.st_size),
                width=info.get("width") or width,
                height=info.get("height") or height,
                has_audio=bool(info.get("has_audio")),
                warnings=warnings,
                finished_at=_now(), finished_ts=time.time(),
            )
            _set_stage(job, "done", 100)
            _persist(job)
    except _AuditionCanceled:
        with _LOCK:
            job.update(status="canceled", error="用户取消",
                       finished_at=_now(), finished_ts=time.time(), warnings=warnings)
            _set_stage(job, "canceled")
            _persist(job)
    except Exception as exc:  # noqa: BLE001
        with _LOCK:
            job.update(status="failed", error=str(exc)[:900],
                       finished_at=_now(), finished_ts=time.time(), warnings=warnings)
            _set_stage(job, "failed")
            _persist(job)
    finally:
        with _LOCK:
            _PROCS.pop(job_id, None)
            if _ACTIVE_JOB == job_id:
                _ACTIVE_JOB = None


class _AuditionCanceled(Exception):
    pass


# --------------------------------------------------------------------------- #
# 请求模型 / 端点
# --------------------------------------------------------------------------- #
class AuditionBody(BaseModel):
    text: Optional[str] = None
    style: str = "anime"
    seed: Optional[int] = None
    prompt_extra: Optional[str] = None
    width: int = 1024
    height: int = 576


def _catalog_characters() -> Dict[str, Any]:
    from pipeline import v5_catalog  # noqa: PLC0415

    catalog = v5_catalog.load_catalog()
    return catalog.get("characters") or {}


@router.post("/api/v5/characters/{character}/audition", status_code=202,
             summary="创建角色试镜视频（立绘+角色声音→H3 口型同步）")
def create_audition(character: str, payload: AuditionBody = Body(default=AuditionBody())) -> dict:
    global _ACTIVE_JOB
    cid = str(character or "").strip().lower()
    characters = _catalog_characters()
    char = characters.get(cid)
    if not isinstance(char, dict):
        raise HTTPException(status_code=404, detail=f"角色不存在: {cid}")
    style = str(payload.style or "anime").strip().lower()
    if style not in _PORTRAIT_BY_STYLE:
        raise HTTPException(status_code=400,
                            detail=f"style 只支持 {', '.join(_STYLES)}（当前 {style!r}）")
    portrait_file = _PORTRAIT_BY_STYLE[style]
    portrait = _ASSETS / cid / portrait_file
    if not portrait.is_file() or portrait.stat().st_size <= 0:
        raise HTTPException(status_code=400,
                            detail=f"角色 {cid} 缺少 {style} 立绘 {portrait_file}；"
                                   "请先在角色详情生成该立绘版本")
    text = str(payload.text or "").strip()
    if not text:
        text = _DEFAULT_TEXT.format(label=str(char.get("label") or cid))
    if len(text) > 120:
        raise HTTPException(status_code=400, detail="台词不能超过 120 个字符")
    try:
        width = int(payload.width or 1024)
        height = int(payload.height or 576)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="width/height 必须是整数") from exc
    if not (256 <= width <= 1920 and 256 <= height <= 1920):
        raise HTTPException(status_code=400, detail="width/height 需在 256–1920")
    prompt_extra = str(payload.prompt_extra or "").strip()[:500]
    try:
        seed = int(payload.seed) if payload.seed is not None else 42
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="seed 必须是整数") from exc

    # GPU 互斥：3D pipeline 运行中 / 另一 audition 运行中 → 409
    try:
        from .pipeline_ui import active_pipeline_job  # noqa: PLC0415

        active = active_pipeline_job()
    except Exception:  # noqa: BLE001
        active = None
    if active:
        raise HTTPException(status_code=409, detail={
            "message": "3D 生成任务运行中，暂不能生成试镜视频",
            "reason": "pipeline_running",
            "job_id": active.get("id"),
        })
    with _LOCK:
        if _ACTIVE_JOB:
            current = _JOBS.get(_ACTIVE_JOB) or {}
            if current.get("status") in ("queued", "running"):
                raise HTTPException(status_code=409, detail={
                    "message": "已有试镜任务在运行（GPU 单并发）",
                    "reason": "audition_running",
                    "job_id": _ACTIVE_JOB,
                })
            _ACTIVE_JOB = None

    job_id = "au_" + uuid.uuid4().hex[:12]
    job: Dict[str, Any] = {
        "id": job_id,
        "character": cid,
        "character_data": char,
        "style": style,
        "portrait_file": portrait_file,
        "text": text,
        "prompt_extra": prompt_extra,
        "seed": seed,
        "width": width,
        "height": height,
        "status": "queued",
        "stage": "queued",
        "stage_history": ["queued"],
        "progress": 0,
        "output_url": None,
        "output_path": None,
        "duration_sec": None,
        "size_bytes": None,
        "tts_engine": None,
        "error": None,
        "warnings": [],
        "created_at": _now(),
        "created_ts": time.time(),
        "started_at": None,
        "started_ts": None,
        "finished_at": None,
        "finished_ts": None,
    }
    with _LOCK:
        _JOBS[job_id] = job
        _ACTIVE_JOB = job_id
    _persist(job)
    try:
        threading.Thread(target=_run_audition, args=(job_id,), daemon=True,
                         name=f"audition-{job_id}").start()
    except Exception as exc:  # noqa: BLE001
        with _LOCK:
            job.update(status="failed", stage="failed", error=f"启动试镜线程失败: {exc}",
                       finished_at=_now(), finished_ts=time.time(),
                       stage_history=(job.get("stage_history") or []) + ["failed"])
            if _ACTIVE_JOB == job_id:
                _ACTIVE_JOB = None
        _persist(job)
        raise HTTPException(status_code=500, detail=f"启动试镜任务失败: {exc}") from exc
    return {
        "ok": True,
        "job_id": job_id,
        "character": cid,
        "style": style,
        "status": "queued",
        "text": text,
        "estimated_sec": _ESTIMATED_SEC,
        "portrait_file": portrait_file,
    }


@router.get("/api/auditions/{job_id}", summary="角色试镜任务状态")
def get_audition(job_id: str):
    key = str(job_id or "").strip()
    with _LOCK:
        job = _JOBS.get(key)
        if not job:
            raise HTTPException(status_code=404, detail=f"试镜任务不存在: {job_id}")
        return {"ok": True, **_view(job)}


@router.post("/api/auditions/{job_id}/cancel", summary="取消试镜任务（终止 H3 子进程）")
def cancel_audition(job_id: str):
    global _ACTIVE_JOB
    key = str(job_id or "").strip()
    with _LOCK:
        job = _JOBS.get(key)
        if not job:
            raise HTTPException(status_code=404, detail=f"试镜任务不存在: {job_id}")
        if job.get("status") not in ("queued", "running"):
            return {"ok": True, "job_id": key, "status": job.get("status"),
                    "message": "任务已结束，无需取消", **_view(job)}
        job.update(status="canceled", stage="canceled", error="用户取消")
        _set_stage(job, "canceled")
        proc = _PROCS.get(key)
        _persist(job)
    _kill_process(proc)
    with _LOCK:
        if _ACTIVE_JOB == key:
            _ACTIVE_JOB = None
        return {"ok": True, "job_id": key, "status": "canceled", **_view(job)}


@router.get("/api/v5/characters/{character}/auditions", summary="角色最近试镜记录")
def list_character_auditions(character: str, limit: int = 10):
    cid = str(character or "").strip().lower()
    characters = _catalog_characters()
    if cid not in characters:
        raise HTTPException(status_code=404, detail=f"角色不存在: {cid}")
    limit = max(1, min(int(limit or 10), 50))
    with _LOCK:
        rows = sorted([job for job in _JOBS.values() if job.get("character") == cid],
                      key=lambda job: job.get("created_ts") or 0, reverse=True)[:limit]
        views = [_view(job) for job in rows]
    return {"ok": True, "character": cid, "count": len(views), "auditions": views}


def _load_persisted_jobs() -> None:
    """启动时加载；中断（queued/running）任务标 failed。"""
    if not _JOBS_DIR.is_dir():
        return
    paths = sorted(_JOBS_DIR.glob("*.json"), key=lambda path: path.stat().st_mtime)
    for path in paths[-200:]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict) or not data.get("id"):
            continue
        if data.get("status") in ("queued", "running"):
            data.update(
                status="failed", stage="failed",
                error="后端服务重启，试镜任务中断（可重新发起）",
                finished_at=_now(), finished_ts=time.time(),
            )
            _persist(data)
        _JOBS[str(data["id"])] = data


_load_persisted_jobs()
