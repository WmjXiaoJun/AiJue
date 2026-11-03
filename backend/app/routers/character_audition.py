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
# V33：多句台词 + 字幕
_MAX_LINES = 6
_MAX_LINE_CHARS = 80
_MAX_AUDIO_SECONDS = 12.0
_LINE_GAP_SECONDS = 0.35
# V34：背景 / 机位
_BACKGROUNDS = {
    "studio": ("棚拍", "in a dark professional recording studio with soft key light"),
    "radio": ("深夜电台",
              "in a warm late-night radio studio with a vintage microphone and practical lamps softly out of focus"),
    "cafe": ("咖啡馆", "in a cozy cafe by a window with warm afternoon light and a softly blurred background"),
    "street": ("夜景街头", "on a neon-lit city street at night with bokeh lights and shallow depth of field"),
    "office": ("办公室", "in a modern office with the city skyline visible through the window"),
    "ancient_hall": ("古风大殿",
                     "in an ancient Chinese hall with wooden pillars, red curtains and warm lantern light"),
    "futuristic": ("未来演播厅",
                   "in a futuristic control room with blue holographic screens and cinematic studio lighting"),
}
_CAMERAS = {
    "medium": ("中景", "medium close-up, chest-up framing"),
    "closeup": ("近景", "tight close-up on the face, shallow depth of field"),
    "half_body": ("半身", "half-body shot, waist-up framing, more of the outfit visible"),
}

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
        "lines": job.get("lines") or [],
        "background": job.get("background"),
        "background_label": job.get("background_label"),
        "camera": job.get("camera"),
        "camera_label": job.get("camera_label"),
        "segments": job.get("segments") or [],
        "subtitles": bool(job.get("subtitles", True)),
        "srt_url": job.get("srt_url"),
        "subtitles_burned": bool(job.get("subtitles_burned")),
        "output_url": job.get("output_url"),
        "plain_output_url": job.get("plain_output_url"),
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


def _build_prompt(character: dict, style: str, prompt_extra: str,
                  background: str = "studio", camera: str = "medium") -> str:
    background_label, background_phrase = _BACKGROUNDS.get(
        str(background or "studio").strip().lower(), _BACKGROUNDS["studio"])
    camera_label, camera_phrase = _CAMERAS.get(
        str(camera or "medium").strip().lower(), _CAMERAS["medium"])
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
        f"One continuous {camera_phrase} of Subject 1 speaking naturally and directly to the camera, "
        "lips moving in sync with the reference speech audio, subtle blinking, eyebrow movement and "
        "small head nods while talking; stable identity, hairstyle, outfit and composition; "
        f"{_STYLE_HINT.get(style, _STYLE_HINT['anime'])}; "
        f"{background_phrase}; 16:9 cinematic composition, no text, no watermark."
    )
    if prompt_extra:
        summary += " " + prompt_extra
    return (
        f"subject_definitions: {identity}\n\n"
        f"summary: {summary}\n\n"
        "overall_soundscape: The reference speech audio, close-mic voice, quiet room tone.\n\n"
        "non_diegetic_music: N/A"
    )


# --------------------------------------------------------------------------- #
# V33：多句 TTS / SRT / 烧录
# --------------------------------------------------------------------------- #
class _AuditionTooLong(RuntimeError):
    pass


def _probe_duration(path: Path) -> float:
    try:
        from pipeline import utils  # noqa: PLC0415

        return round(float(utils.probe_duration(path) or 0.0), 3)
    except Exception:  # noqa: BLE001
        return 0.0


def _estimate_audio_seconds(lines: List[str]) -> float:
    """粗略估算 TTS 时长（中文约 4 字/秒）+ 句间 0.35s（用于创建时 400 预检）。"""
    chars = sum(len(str(line)) for line in lines)
    return round(chars / 4.0 + _LINE_GAP_SECONDS * max(0, len(lines) - 1), 2)


def _srt_time(seconds: float) -> str:
    total_ms = max(0, int(round(float(seconds) * 1000)))
    hours, rest = divmod(total_ms, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    secs, millis = divmod(rest, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def _write_srt(segments: List[Dict[str, Any]], path: Path) -> None:
    blocks = []
    for index, segment in enumerate(segments, 1):
        blocks.append(
            f"{index}\n{_srt_time(segment['start'])} --> {_srt_time(segment['end'])}\n"
            f"{segment['text']}\n"
        )
    path.write_text("\n".join(blocks), encoding="utf-8")


def _synthesize_lines(character: dict, lines: List[str], workdir: Path,
                      warnings: List[str]) -> tuple:
    """逐句 TTS（同角色音色）→ 句间 0.35s 静音 → 拼接 24k mono wav；返回 (wav, segments, total)。"""
    parts: List[Path] = []
    segments: List[Dict[str, Any]] = []
    engines: List[str] = []
    cursor = 0.0
    for index, line in enumerate(lines):
        line_wav = workdir / f"line_{index:02d}.wav"
        tts = v5_create._synthesize_line(character, line, line_wav, warnings)
        engines.append(str(tts.get("engine") or "unknown"))
        duration = _probe_duration(line_wav)
        if duration <= 0:
            raise RuntimeError(f"第 {index + 1} 句 TTS 音频时长为 0")
        start = cursor
        end = round(start + duration, 3)
        segments.append({"index": index, "text": line,
                         "start": round(start, 3), "end": end})
        parts.append(line_wav)
        cursor = end
        if index < len(lines) - 1:
            cursor += _LINE_GAP_SECONDS
    total = round(cursor, 3)
    if total > _MAX_AUDIO_SECONDS:
        raise _AuditionTooLong(
            f"台词总时长 {total:.2f}s 超过 {_MAX_AUDIO_SECONDS:.0f}s，请精简台词")
    gap_wav = workdir / "gap.wav"
    subprocess.run(
        [str(jobs.ffmpeg_bin()), "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
         "-t", f"{_LINE_GAP_SECONDS:.3f}", "-c:a", "pcm_s16le", str(gap_wav)],
        capture_output=True, timeout=60, check=True,
    )
    entries: List[Path] = []
    for index, part in enumerate(parts):
        if index:
            entries.append(gap_wav)
        entries.append(part)
    concat_file = workdir / "concat.txt"
    lines_text = []
    for entry in entries:
        escaped = str(entry.resolve()).replace("\\", "/").replace("'", "'\\''")
        lines_text.append(f"file '{escaped}'")
    concat_file.write_text("\n".join(lines_text) + "\n", encoding="utf-8")
    audio = workdir / "audio.wav"
    proc = subprocess.run(
        [str(jobs.ffmpeg_bin()), "-y", "-hide_banner", "-loglevel", "error",
         "-f", "concat", "-safe", "0", "-i", str(concat_file),
         "-ar", "24000", "-ac", "1", "-c:a", "pcm_s16le", str(audio)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
    )
    if proc.returncode != 0 or not audio.is_file() or audio.stat().st_size <= 0:
        tail = "\n".join((proc.stderr or "").strip().splitlines()[-6:])
        raise RuntimeError(f"多句 TTS 拼接失败（returncode={proc.returncode}）：{tail[-400:]}")
    probed = _probe_duration(audio)
    if probed > 0:
        total = probed
    return audio, segments, total, engines


def _burn_subtitles(plain: Path, subtitled: Path, workdir: Path,
                    warnings: List[str]) -> bool:
    """用 ffmpeg subtitles 滤镜烧录 SRT（白字黑边、底部居中、MarginV 24）。"""
    style = ("FontName=Microsoft YaHei,FontSize=20,PrimaryColour=&H00FFFFFF,"
             "OutlineColour=&H00000000,BorderStyle=1,Outline=2,Shadow=0,"
             "Alignment=2,MarginV=24")
    vf = f"subtitles=subtitles.srt:force_style='{style}'"
    try:
        proc = subprocess.run(
            [str(jobs.ffmpeg_bin()), "-y", "-hide_banner", "-loglevel", "error",
             "-i", str(plain.resolve()), "-vf", vf, "-c:v", "libx264", "-preset", "veryfast",
             "-crf", "20", "-c:a", "copy", "-movflags", "+faststart", str(subtitled.resolve())],
            cwd=str(workdir), capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=600,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        warnings.append(f"字幕烧录失败: {type(exc).__name__}: {exc}")
        return False
    if proc.returncode != 0 or not subtitled.is_file() or subtitled.stat().st_size <= 0:
        tail = "\n".join((proc.stderr or "").strip().splitlines()[-8:])
        warnings.append(f"字幕烧录失败（returncode={proc.returncode}）：{tail[-500:]}")
        return False
    return True


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
        raw_lines = job.get("lines")
        if isinstance(raw_lines, list) and raw_lines:
            lines = [str(line).strip() for line in raw_lines if str(line).strip()]
        else:
            lines = [str(job.get("text") or "")]
        lines = lines or [str(job.get("text") or "")]
        width = int(job.get("width") or 1024)
        height = int(job.get("height") or 576)
        workdir = _JOBS_DIR / f"{job_id}_work"
        workdir.mkdir(parents=True, exist_ok=True)

        # 1) 逐句 TTS（breeze 优先 / edge 兜底；24k mono；句间 0.35s）→ segments + SRT
        audio, segments, duration, engines = _synthesize_lines(
            character, lines, workdir, warnings)
        srt_path = workdir / "subtitles.srt"
        _write_srt(segments, srt_path)
        with _LOCK:
            if job.get("status") == "canceled":
                raise _AuditionCanceled()
            job.update(
                lines=lines,
                segments=segments,
                tts_engine=",".join(sorted(set(engines))),
                duration_sec=round(duration, 3),
                srt_url=f"/media/audition_jobs/{workdir.name}/{srt_path.name}"
                        f"?t={int(srt_path.stat().st_mtime)}",
            )
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
        prompt = _build_prompt(
            character, style, str(job.get("prompt_extra") or ""),
            background=str(job.get("background") or "studio"),
            camera=str(job.get("camera") or "medium"),
        )
        cmd = [
            sys.executable, str(_H3_CLI), "--mode", "audio_ref",
            "--image", str(ref), "--audio", str(audio), "--out", str(out),
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

        # 4) 完成：可选烧录字幕（白字黑边/底部居中/MarginV 24），失败仅 warning
        plain_info = _media_info(out)
        plain_stat = out.stat()
        plain_url = f"/media/character_demos/{out.name}?t={int(plain_stat.st_mtime)}"
        final_path = out
        final_info = plain_info
        subtitles_burned = False
        if bool(job.get("subtitles", True)):
            subtitled = _DEMO_DIR / f"{job.get('character')}_{job_id}_sub.mp4"
            if _burn_subtitles(out, subtitled, workdir, warnings):
                final_path = subtitled
                final_info = _media_info(subtitled)
                subtitles_burned = True
        with _LOCK:
            if job.get("status") == "canceled":
                raise _AuditionCanceled()
            stat = final_path.stat()
            job.update(
                status="done",
                subtitles_burned=subtitles_burned,
                plain_output_url=plain_url,
                plain_output_path=str(out),
                output_url=f"/media/character_demos/{final_path.name}?t={int(stat.st_mtime)}",
                output_path=str(final_path),
                duration_sec=round(float(final_info.get("duration_sec") or duration), 3),
                size_bytes=int(stat.st_size),
                width=final_info.get("width") or width,
                height=final_info.get("height") or height,
                has_audio=bool(final_info.get("has_audio")),
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
    lines: Optional[List[str]] = None
    subtitles: bool = True
    style: str = "anime"
    background: str = "studio"
    camera: str = "medium"
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
    lines: List[str] = []
    if payload.lines is not None:
        if not isinstance(payload.lines, (list, tuple)):
            raise HTTPException(status_code=400, detail="lines 必须是字符串数组")
        if len(payload.lines) > _MAX_LINES:
            raise HTTPException(status_code=400, detail=f"lines 最多 {_MAX_LINES} 行")
        for index, raw_line in enumerate(payload.lines):
            line = str(raw_line or "").strip()
            if not line:
                continue
            if len(line) > _MAX_LINE_CHARS:
                raise HTTPException(
                    status_code=400,
                    detail=f"第 {index + 1} 行超过 {_MAX_LINE_CHARS} 字，请精简",
                )
            lines.append(line)
    if not lines:
        if len(text) > _MAX_LINE_CHARS:
            raise HTTPException(status_code=400, detail="台词不能超过 80 个字符")
        lines = [text]
    elif len(lines) > _MAX_LINES:
        raise HTTPException(status_code=400, detail=f"lines 最多 {_MAX_LINES} 行")
    estimated = _estimate_audio_seconds(lines)
    if estimated > _MAX_AUDIO_SECONDS:
        raise HTTPException(status_code=400,
                            detail="台词过长，请精简到 12 秒内（当前估算 "
                                   f"{estimated:.1f}s）")
    subtitles = bool(payload.subtitles)
    background = str(payload.background or "studio").strip().lower()
    if background not in _BACKGROUNDS:
        raise HTTPException(status_code=400,
                            detail=f"background 只支持 {', '.join(_BACKGROUNDS)}"
                                   f"（当前 {background!r}）")
    camera = str(payload.camera or "medium").strip().lower()
    if camera not in _CAMERAS:
        raise HTTPException(status_code=400,
                            detail=f"camera 只支持 {', '.join(_CAMERAS)}"
                                   f"（当前 {camera!r}）")
    background_label = _BACKGROUNDS[background][0]
    camera_label = _CAMERAS[camera][0]
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
    # V35：与角色宣传片互斥（promo 运行时 audition 409）
    try:
        from . import character_promo  # noqa: PLC0415

        active_promo = character_promo.active_promo_job()
    except Exception:  # noqa: BLE001
        active_promo = None
    if active_promo:
        raise HTTPException(status_code=409, detail={
            "message": "宣传片任务运行中，暂不能生成试镜视频",
            "reason": "promo_running",
            "job_id": active_promo.get("id"),
        })
    # V36：与双人对戏互斥
    try:
        from . import character_dialogue  # noqa: PLC0415

        active_dialogue = character_dialogue.active_dialogue_job()
    except Exception:  # noqa: BLE001
        active_dialogue = None
    if active_dialogue:
        raise HTTPException(status_code=409, detail={
            "message": "双人对戏任务运行中，暂不能生成试镜视频",
            "reason": "dialogue_running",
            "job_id": active_dialogue.get("id"),
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
        "lines": lines,
        "background": background,
        "background_label": background_label,
        "camera": camera,
        "camera_label": camera_label,
        "segments": [],
        "subtitles": subtitles,
        "subtitles_burned": False,
        "srt_url": None,
        "plain_output_url": None,
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
        "lines": lines,
        "background": background,
        "background_label": background_label,
        "camera": camera,
        "camera_label": camera_label,
        "subtitles": subtitles,
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
