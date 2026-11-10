# -*- coding: utf-8 -*-
"""V36-A：双人对戏（正反打多镜 + 片头卡 + 字幕 + 海报/SRT）。

POST /api/v5/characters/dialogue
GET  /api/v5/dialogues/{dialogue_id}
POST /api/v5/dialogues/{dialogue_id}/cancel
GET  /api/v5/dialogues?limit=5

每行一镜：说话角色立绘 + 同背景/机位 + 该角色音色 TTS + 字幕烧录；拼成 1024×576/24fps/AAC。
"""
from __future__ import annotations

import json
import os
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
from . import character_audition as audition
from . import character_promo as promo_mod

router = APIRouter(tags=["character-dialogue"])

_REPO = Path(config.REPO_ROOT)
_DIALOGUE_DIR = Path(config.OUTPUTS_DIR) / "dialogue_jobs"
_DEMO_DIR = Path(config.OUTPUTS_DIR) / "character_demos"
_H3_CLI = _REPO / "integrations" / "comfyui" / "run_h3_ref.py"
_PORTRAIT_BY_STYLE = audition._PORTRAIT_BY_STYLE
_STYLES = audition._STYLES
_MAX_LINES = 6
_MAX_LINE_CHARS = 60
_MAX_TOTAL_AUDIO_SECONDS = 24.0
_TITLE_CHARS = 40
_TITLE_CARD_SECONDS = 2.2
_WIDTH = 1024
_HEIGHT = 576
_FPS = 24

_LOCK = threading.RLock()
_DIALOGUES: Dict[str, Dict[str, Any]] = {}
_PROCS: Dict[str, subprocess.Popen] = {}
_ACTIVE_DIALOGUE: Optional[str] = None


class _DialogueCanceled(Exception):
    pass


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _persist(dialogue: Dict[str, Any]) -> None:
    try:
        _DIALOGUE_DIR.mkdir(parents=True, exist_ok=True)
        target = _DIALOGUE_DIR / f"{dialogue['id']}.json"
        tmp = target.with_name(f"{target.name}.tmp-{os.getpid()}-{threading.get_ident()}")
        tmp.write_text(json.dumps(dialogue, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, target)
    except OSError:
        pass


def _elapsed(dialogue: Dict[str, Any]) -> float:
    created = dialogue.get("created_ts")
    if not isinstance(created, (int, float)):
        return 0.0
    finished = dialogue.get("finished_ts")
    end = float(finished) if isinstance(finished, (int, float)) else time.time()
    return round(max(0.0, end - float(created)), 1)


def _set_stage(dialogue: Dict[str, Any], stage: str, progress: Optional[int] = None) -> None:
    dialogue["stage"] = stage
    if progress is not None:
        dialogue["progress"] = int(progress)
    history = dialogue.setdefault("stage_history", [])
    if not history or history[-1] != stage:
        history.append(stage)


def _line_view(line: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "index": line.get("index"),
        "speaker": line.get("speaker"),
        "character": line.get("character"),
        "label": line.get("label"),
        "text": line.get("text"),
        "background": line.get("background"),
        "background_label": line.get("background_label"),
        "camera": line.get("camera"),
        "camera_label": line.get("camera_label"),
        "status": line.get("status"),
        "segments": line.get("segments") or [],
        "duration_sec": line.get("duration_sec"),
        "output_url": line.get("output_url"),
        "error": line.get("error"),
    }


def _view(dialogue: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": dialogue.get("id"),
        "job_id": dialogue.get("id"),
        "character_a": dialogue.get("character_a"),
        "character_b": dialogue.get("character_b"),
        "character_c": dialogue.get("character_c"),
        "character_a_label": dialogue.get("character_a_label"),
        "character_b_label": dialogue.get("character_b_label"),
        "character_c_label": dialogue.get("character_c_label"),
        "characters": {
            key: {
                "id": dialogue.get(f"character_{key}"),
                "label": dialogue.get(f"character_{key}_label"),
            }
            for key in ("a", "b", "c") if dialogue.get(f"character_{key}")
        },
        "style": dialogue.get("style"),
        "background": dialogue.get("background"),
        "background_label": dialogue.get("background_label"),
        "camera": dialogue.get("camera"),
        "camera_label": dialogue.get("camera_label"),
        "scenes": dialogue.get("scenes") or {
            key: {
                "background": dialogue.get("background"),
                "background_label": dialogue.get("background_label"),
                "camera": dialogue.get("camera"),
                "camera_label": dialogue.get("camera_label"),
            } for key in ("a", "b", "c") if dialogue.get(f"character_{key}")
        },
        "title": dialogue.get("title"),
        "status": dialogue.get("status"),
        "stage": dialogue.get("stage"),
        "stage_history": list(dialogue.get("stage_history") or []),
        "progress": dialogue.get("progress", 0),
        "current_line": dialogue.get("current_line", -1),
        "total_lines": dialogue.get("total_lines", len(dialogue.get("lines") or [])),
        "lines": [_line_view(line) for line in dialogue.get("lines") or []],
        "output_url": dialogue.get("output_url"),
        "poster_url": dialogue.get("poster_url"),
        "srt_url": dialogue.get("srt_url"),
        "duration_sec": dialogue.get("duration_sec"),
        "size_bytes": dialogue.get("size_bytes"),
        "width": dialogue.get("width"),
        "height": dialogue.get("height"),
        "seed": dialogue.get("seed"),
        "error": dialogue.get("error"),
        "warnings": dialogue.get("warnings") or [],
        "created_at": dialogue.get("created_at"),
        "started_at": dialogue.get("started_at"),
        "finished_at": dialogue.get("finished_at"),
        "elapsed_sec": _elapsed(dialogue),
    }


def active_dialogue_job() -> Optional[Dict[str, Any]]:
    with _LOCK:
        if not _ACTIVE_DIALOGUE:
            return None
        dialogue = _DIALOGUES.get(_ACTIVE_DIALOGUE) or {}
        if dialogue.get("status") in ("queued", "running"):
            return _view(dialogue)
    return None


# --------------------------------------------------------------------------- #
# 子进程 / 工具
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


def _canceled(dialogue_id: str) -> bool:
    with _LOCK:
        dialogue = _DIALOGUES.get(dialogue_id) or {}
        return dialogue.get("status") == "canceled"


def _run_ffmpeg(dialogue_id: str, cmd: List[str], timeout: float = 900.0) -> None:
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                            text=True, encoding="utf-8", errors="replace")
    with _LOCK:
        _PROCS[dialogue_id] = proc
    started = time.time()
    try:
        while True:
            if _canceled(dialogue_id):
                _kill_process(proc)
                raise _DialogueCanceled()
            if proc.poll() is not None:
                break
            if time.time() - started > timeout:
                _kill_process(proc)
                raise RuntimeError(f"ffmpeg 超时（{timeout:.0f}s）")
            time.sleep(0.2)
        stderr = proc.communicate()[1] or ""
        if proc.returncode != 0:
            tail = "\n".join(stderr.strip().splitlines()[-8:])
            raise RuntimeError(f"ffmpeg 失败（returncode={proc.returncode}）：{tail[-500:]}")
    finally:
        with _LOCK:
            _PROCS.pop(dialogue_id, None)


def _title_card(dialogue: Dict[str, Any], path: Path) -> None:
    from PIL import Image, ImageDraw  # noqa: PLC0415

    image = Image.new("RGB", (_WIDTH, _HEIGHT), (13, 16, 22))
    draw = ImageDraw.Draw(image)
    draw.rectangle([72, 330, 952, 336], fill=(245, 197, 24))
    label_a = str(dialogue.get("character_a_label") or dialogue.get("character_a") or "")
    label_b = str(dialogue.get("character_b_label") or dialogue.get("character_b") or "")
    labels = [label_a, label_b]
    if dialogue.get("character_c"):
        labels.append(str(dialogue.get("character_c_label") or dialogue.get("character_c") or ""))

    def center(text: str, y: int, size: int, fill) -> None:
        font = promo_mod._font(size)
        try:
            box = draw.textbbox((0, 0), text, font=font)
            width = box[2] - box[0]
        except Exception:  # noqa: BLE001
            width = len(text) * size
        draw.text((( _WIDTH - width) // 2, y), text, font=font, fill=fill)

    center("三人对戏" if len(labels) >= 3 else "双人对戏", 90, 28, (150, 158, 170))
    center("  ×  ".join(labels), 165, 62 if len(labels) == 2 else 52, (245, 245, 248))
    center(str(dialogue.get("title") or ""), 265, 32, (245, 197, 24))
    center("DIALOGUE SCENE", 420, 22, (110, 120, 135))
    image.save(path, "PNG")


def _card_video(png: Path, duration: float, out: Path, dialogue_id: str) -> None:
    _run_ffmpeg(dialogue_id, [
        str(jobs.ffmpeg_bin()), "-y", "-hide_banner", "-loglevel", "error",
        "-loop", "1", "-t", f"{duration:.3f}", "-i", str(png),
        "-f", "lavfi", "-t", f"{duration:.3f}", "-i", "anullsrc=r=48000:cl=stereo",
        "-shortest", "-r", str(_FPS), "-s", f"{_WIDTH}x{_HEIGHT}", "-pix_fmt", "yuv420p",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart", str(out),
    ], timeout=120)


def _normalize(src: Path, out: Path, dialogue_id: str) -> None:
    _run_ffmpeg(dialogue_id, [
        str(jobs.ffmpeg_bin()), "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(src),
        "-vf", f"fps={_FPS},scale={_WIDTH}:{_HEIGHT}:flags=lanczos,format=yuv420p",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart", str(out),
    ], timeout=300)


# --------------------------------------------------------------------------- #
# 单行生成
# --------------------------------------------------------------------------- #
def _run_line_h3(dialogue: Dict[str, Any], line: Dict[str, Any], workdir: Path,
                 ref: Path, audio: Path, duration: float) -> Path:
    dialogue_id = str(dialogue["id"])
    character = ((dialogue.get("character_data") or dialogue.get("characters") or {})
                 .get(str(line.get("character"))) or {})
    prompt = audition._build_prompt(
        character, str(dialogue.get("style") or "realistic"), "",
        background=str(line.get("background") or dialogue.get("background") or "cafe"),
        camera=str(line.get("camera") or dialogue.get("camera") or "medium"),
    )
    out = workdir / "line.mp4"
    proc = subprocess.Popen([
        sys.executable, str(_H3_CLI), "--mode", "audio_ref",
        "--image", str(ref), "--audio", str(audio), "--out", str(out),
        "--duration", f"{duration + 0.1:.3f}",
        "--width", str(_WIDTH), "--height", str(_HEIGHT),
        "--fps", "25", "--seed", str(dialogue.get("seed") or 42),
        "--prompt", prompt, "--timeout", "1800",
    ], cwd=str(_REPO), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", bufsize=1)
    with _LOCK:
        _PROCS[dialogue_id] = proc
    tail: List[str] = []
    try:
        while True:
            if _canceled(dialogue_id):
                _kill_process(proc)
                raise _DialogueCanceled()
            chunk = proc.stdout.readline() if proc.stdout is not None else ""
            if chunk:
                tail.append(chunk.strip())
                tail = tail[-40:]
                continue
            if proc.poll() is not None:
                break
            time.sleep(0.2)
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            _kill_process(proc)
    finally:
        with _LOCK:
            _PROCS.pop(dialogue_id, None)
    if _canceled(dialogue_id):
        raise _DialogueCanceled()
    if proc.returncode != 0:
        tail_text = "\n".join(tail[-10:])
        raise RuntimeError(f"H3 生成失败（returncode={proc.returncode}）：{tail_text[-700:]}")
    if not out.is_file() or out.stat().st_size <= 0:
        raise RuntimeError("H3 未产出视频文件")
    return out


# --------------------------------------------------------------------------- #
# 拼接
# --------------------------------------------------------------------------- #
def _assemble(dialogue: Dict[str, Any], workdir: Path, warnings: List[str]) -> None:
    dialogue_id = str(dialogue["id"])
    title_png = workdir / "title.png"
    _title_card(dialogue, title_png)
    title_mp4 = workdir / "title.mp4"
    _card_video(title_png, _TITLE_CARD_SECONDS, title_mp4, dialogue_id)
    segments_files = [title_mp4]
    merged_segments: List[Dict[str, Any]] = []
    offset = _TITLE_CARD_SECONDS
    for line in dialogue.get("lines") or []:
        raw = Path(str(line.get("output_path") or ""))
        if not raw.is_file():
            raise RuntimeError(f"第 {line['index'] + 1} 行视频不存在")
        norm = workdir / f"line_{line['index']}_norm.mp4"
        _normalize(raw, norm, dialogue_id)
        segments_files.append(norm)
        duration = float(audition._media_info(norm).get("duration_sec") or 0.0)
        for seg in line.get("segments") or []:
            merged_segments.append({
                "index": len(merged_segments),
                "text": seg["text"],
                "start": round(offset + float(seg["start"]), 3),
                "end": round(offset + float(seg["end"]), 3),
            })
        offset += duration
    concat_file = workdir / "concat.txt"
    concat_file.write_text("\n".join(
        f"file '{str(path.resolve()).replace(chr(92), '/')}'" for path in segments_files
    ) + "\n", encoding="utf-8")
    out = _DEMO_DIR / f"dialogue_{dialogue_id}.mp4"
    _DEMO_DIR.mkdir(parents=True, exist_ok=True)
    _run_ffmpeg(dialogue_id, [
        str(jobs.ffmpeg_bin()), "-y", "-hide_banner", "-loglevel", "error",
        "-f", "concat", "-safe", "0", "-i", str(concat_file),
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart", str(out),
    ], timeout=600)
    srt_path = workdir / "dialogue.srt"
    audition._write_srt(merged_segments, srt_path)
    poster = workdir / "poster.jpg"
    _run_ffmpeg(dialogue_id, [
        str(jobs.ffmpeg_bin()), "-y", "-hide_banner", "-loglevel", "error",
        "-ss", "1.0", "-i", str(out), "-frames:v", "1", "-q:v", "3", str(poster),
    ], timeout=120)
    info = audition._media_info(out)
    with _LOCK:
        dialogue.update(
            output_path=str(out),
            output_url=f"/media/character_demos/{out.name}?t={int(out.stat().st_mtime)}",
            poster_url=f"/media/dialogue_jobs/{workdir.name}/{poster.name}"
                       f"?t={int(poster.stat().st_mtime)}",
            srt_url=f"/media/dialogue_jobs/{workdir.name}/{srt_path.name}"
                    f"?t={int(srt_path.stat().st_mtime)}",
            duration_sec=info.get("duration_sec"),
            size_bytes=int(out.stat().st_size),
            width=info.get("width") or _WIDTH,
            height=info.get("height") or _HEIGHT,
            merged_segments=merged_segments,
            warnings=warnings,
        )
        _persist(dialogue)


# --------------------------------------------------------------------------- #
# 任务线程
# --------------------------------------------------------------------------- #
def _run_dialogue(dialogue_id: str) -> None:
    global _ACTIVE_DIALOGUE
    dialogue = _DIALOGUES.get(dialogue_id)
    if not dialogue:
        return
    warnings: List[str] = dialogue.get("warnings") or []
    try:
        with _LOCK:
            if dialogue.get("status") == "canceled":
                raise _DialogueCanceled()
            dialogue.update(status="running", started_at=_now(), started_ts=time.time(),
                            error=None)
            _set_stage(dialogue, "line_1_tts", 3)
            dialogue["current_line"] = 0
            _persist(dialogue)
        style = str(dialogue.get("style") or "realistic")
        lines = dialogue.get("lines") or []
        characters = dialogue.get("character_data") or dialogue.get("characters") or {}
        workdir = _DIALOGUE_DIR / f"{dialogue_id}_work"
        workdir.mkdir(parents=True, exist_ok=True)
        for line in lines:
            index = int(line.get("index") or 0)
            shot_dir = workdir / f"line_{index}"
            shot_dir.mkdir(parents=True, exist_ok=True)
            with _LOCK:
                if dialogue.get("status") == "canceled":
                    raise _DialogueCanceled()
                dialogue["current_line"] = index
                line["status"] = "running"
                _set_stage(dialogue, f"line_{index + 1}_tts",
                           3 + int(index / max(1, len(lines)) * 88))
                _persist(dialogue)
            character = characters.get(str(line.get("character"))) or {}
            speaker_audio, segments, duration, _engines = audition._synthesize_lines(
                character, [str(line.get("text") or "")], shot_dir, warnings)
            srt_path = shot_dir / "subtitles.srt"
            audition._write_srt(segments, srt_path)
            portrait_file = _PORTRAIT_BY_STYLE.get(style, "portrait.png")
            portrait = (_REPO / "assets" / "v5" / "characters"
                        / str(line.get("character")) / portrait_file)
            ref = shot_dir / "ref_16x9.png"
            audition._crop_ref_16x9(portrait, ref, _WIDTH, _HEIGHT)
            with _LOCK:
                if dialogue.get("status") == "canceled":
                    raise _DialogueCanceled()
                _set_stage(dialogue, f"line_{index + 1}_generate",
                           10 + int(index / max(1, len(lines)) * 88))
                _persist(dialogue)
            raw = _run_line_h3(dialogue, line, shot_dir, ref, speaker_audio, duration)
            final = raw
            if dialogue.get("subtitles_burned", True):
                subtitled = shot_dir / "line_sub.mp4"
                if audition._burn_subtitles(raw, subtitled, shot_dir, warnings):
                    final = subtitled
            info = audition._media_info(final)
            with _LOCK:
                line.update(status="done", segments=segments,
                            duration_sec=info.get("duration_sec"),
                            output_path=str(final))
                _persist(dialogue)
        with _LOCK:
            if dialogue.get("status") == "canceled":
                raise _DialogueCanceled()
            _set_stage(dialogue, "assemble", 92)
            dialogue["current_line"] = -1
            _persist(dialogue)
        _assemble(dialogue, workdir, warnings)
        with _LOCK:
            if dialogue.get("status") == "canceled":
                raise _DialogueCanceled()
            dialogue.update(status="done", warnings=warnings,
                            finished_at=_now(), finished_ts=time.time(),
                            poster_url=dialogue.get("poster_url"),
                            srt_url=dialogue.get("srt_url"))
            _set_stage(dialogue, "done", 100)
            _persist(dialogue)
    except _DialogueCanceled:
        with _LOCK:
            dialogue.update(status="canceled", error="用户取消",
                            finished_at=_now(), finished_ts=time.time(), warnings=warnings)
            _set_stage(dialogue, "canceled")
            for line in dialogue.get("lines") or []:
                if line.get("status") in ("queued", "running"):
                    line["status"] = "canceled"
            _persist(dialogue)
    except Exception as exc:  # noqa: BLE001
        with _LOCK:
            dialogue.update(status="failed", error=str(exc)[:900],
                            finished_at=_now(), finished_ts=time.time(), warnings=warnings)
            _set_stage(dialogue, "failed")
            for line in dialogue.get("lines") or []:
                if line.get("status") in ("queued", "running"):
                    line["status"] = "failed"
            _persist(dialogue)
    finally:
        with _LOCK:
            _PROCS.pop(dialogue_id, None)
            if _ACTIVE_DIALOGUE == dialogue_id:
                _ACTIVE_DIALOGUE = None


# --------------------------------------------------------------------------- #
# 请求模型 / 端点
# --------------------------------------------------------------------------- #
class DialogueLineBody(BaseModel):
    speaker: str
    text: str


class DialogueSceneBody(BaseModel):
    background: Optional[str] = None
    camera: Optional[str] = None


class DialogueBody(BaseModel):
    character_a: str
    character_b: str
    character_c: Optional[str] = None
    style: str = "realistic"
    background: str = "cafe"
    camera: str = "medium"
    scene_a: Optional[DialogueSceneBody] = None
    scene_b: Optional[DialogueSceneBody] = None
    scene_c: Optional[DialogueSceneBody] = None
    title: Optional[str] = None
    seed: Optional[int] = None
    lines: List[DialogueLineBody]


def _mutex_check() -> None:
    try:
        from .pipeline_ui import active_pipeline_job  # noqa: PLC0415

        active_pipeline = active_pipeline_job()
    except Exception:  # noqa: BLE001
        active_pipeline = None
    if active_pipeline:
        raise HTTPException(status_code=409, detail={
            "message": "3D 生成任务运行中，暂不能生成双人对戏",
            "reason": "pipeline_running", "job_id": active_pipeline.get("id"),
        })
    with _LOCK:
        if audition._ACTIVE_JOB:
            job = audition._JOBS.get(audition._ACTIVE_JOB) or {}
            if job.get("status") in ("queued", "running"):
                raise HTTPException(status_code=409, detail={
                    "message": "试镜任务运行中，暂不能生成双人对戏",
                    "reason": "audition_running", "job_id": audition._ACTIVE_JOB,
                })
        promo = promo_mod.active_promo_job()
        if promo:
            raise HTTPException(status_code=409, detail={
                "message": "宣传片任务运行中，暂不能生成双人对戏",
                "reason": "promo_running", "job_id": promo.get("id"),
            })
        if _ACTIVE_DIALOGUE:
            current = _DIALOGUES.get(_ACTIVE_DIALOGUE) or {}
            if current.get("status") in ("queued", "running"):
                raise HTTPException(status_code=409, detail={
                    "message": "已有双人对戏任务在运行（GPU 单并发）",
                    "reason": "dialogue_running", "job_id": _ACTIVE_DIALOGUE,
                })


@router.post("/api/v5/characters/dialogue", status_code=202,
             summary="创建双人对戏（正反打多镜 + 字幕）")
def create_dialogue(payload: DialogueBody = Body(default=DialogueBody(
        character_a="", character_b="", lines=[]))) -> dict:
    global _ACTIVE_DIALOGUE
    cid_a = str(payload.character_a or "").strip().lower()
    cid_b = str(payload.character_b or "").strip().lower()
    cid_c = str(payload.character_c or "").strip().lower() or None
    characters = audition._catalog_characters()
    char_a = characters.get(cid_a)
    char_b = characters.get(cid_b)
    char_c = characters.get(cid_c) if cid_c else None
    missing = [cid for cid, char in ((cid_a, char_a), (cid_b, char_b),
                                     (cid_c, char_c)) if cid and not isinstance(char, dict)]
    if missing:
        raise HTTPException(status_code=404, detail=f"角色不存在: {', '.join(missing)}")
    if not isinstance(char_a, dict) or not isinstance(char_b, dict):
        raise HTTPException(status_code=404, detail="角色 A/B 不能为空")
    ids = [cid_a, cid_b] + ([cid_c] if cid_c else [])
    if len(set(ids)) != len(ids):
        raise HTTPException(status_code=400, detail="角色 A/B/C 不能相同")
    if payload.scene_c is not None and not cid_c:
        raise HTTPException(status_code=400, detail="scene_c 需要同时提供 character_c")
    style = str(payload.style or "realistic").strip().lower()
    if style not in _PORTRAIT_BY_STYLE:
        raise HTTPException(status_code=400,
                            detail=f"style 只支持 {', '.join(_STYLES)}（当前 {style!r}）")
    portrait_file = _PORTRAIT_BY_STYLE[style]
    for cid in ids:
        portrait = _REPO / "assets" / "v5" / "characters" / cid / portrait_file
        if not portrait.is_file() or portrait.stat().st_size <= 0:
            raise HTTPException(status_code=400,
                                detail=f"角色 {cid} 缺少 {style} 立绘 {portrait_file}")
    background = str(payload.background or "cafe").strip().lower()
    if background not in audition._BACKGROUNDS:
        raise HTTPException(status_code=400,
                            detail=f"background 只支持 {', '.join(audition._BACKGROUNDS)}"
                                   f"（当前 {background!r}）")
    camera = str(payload.camera or "medium").strip().lower()
    if camera not in audition._CAMERAS:
        raise HTTPException(status_code=400,
                            detail=f"camera 只支持 {', '.join(audition._CAMERAS)}"
                                   f"（当前 {camera!r}）")

    def resolve_scene(scene: Optional[DialogueSceneBody], name: str) -> Dict[str, Any]:
        bg = str((scene.background if scene and scene.background else background)).strip().lower()
        if bg not in audition._BACKGROUNDS:
            raise HTTPException(status_code=400,
                                detail=f"{name}.background 只支持 "
                                       f"{', '.join(audition._BACKGROUNDS)}（当前 {bg!r}）")
        cam = str((scene.camera if scene and scene.camera else camera)).strip().lower()
        if cam not in audition._CAMERAS:
            raise HTTPException(status_code=400,
                                detail=f"{name}.camera 只支持 "
                                       f"{', '.join(audition._CAMERAS)}（当前 {cam!r}）")
        return {
            "background": bg,
            "background_label": audition._BACKGROUNDS[bg][0],
            "camera": cam,
            "camera_label": audition._CAMERAS[cam][0],
        }

    scenes = {
        "a": resolve_scene(payload.scene_a, "scene_a"),
        "b": resolve_scene(payload.scene_b, "scene_b"),
    }
    if cid_c:
        scenes["c"] = resolve_scene(payload.scene_c, "scene_c")
    raw_lines = payload.lines or []
    if not (2 <= len(raw_lines) <= _MAX_LINES):
        raise HTTPException(status_code=400, detail=f"lines 需要 2–{_MAX_LINES} 行")
    lines: List[Dict[str, Any]] = []
    texts: List[str] = []
    speaker_char = {"a": cid_a, "b": cid_b}
    speaker_data = {"a": char_a, "b": char_b}
    if cid_c:
        speaker_char["c"] = cid_c
        speaker_data["c"] = char_c
    for index, raw in enumerate(raw_lines):
        speaker = str(raw.speaker or "").strip().lower()
        if speaker not in ("a", "b", "c"):
            raise HTTPException(status_code=400,
                                detail=f"第 {index + 1} 行 speaker 必须是 a/b/c（当前 {speaker!r}）")
        if speaker == "c" and not cid_c:
            raise HTTPException(status_code=400,
                                detail=f"第 {index + 1} 行 speaker=c，但未提供 character_c")
        text = str(raw.text or "").strip()
        if not text:
            raise HTTPException(status_code=400, detail=f"第 {index + 1} 行台词不能为空")
        if len(text) > _MAX_LINE_CHARS:
            raise HTTPException(status_code=400,
                                detail=f"第 {index + 1} 行超过 {_MAX_LINE_CHARS} 字")
        cid = speaker_char[speaker]
        char = speaker_data[speaker]
        scene = scenes[speaker]
        lines.append({
            "index": index, "speaker": speaker, "character": cid,
            "label": str(char.get("label") or cid), "text": text,
            "background": scene["background"],
            "background_label": scene["background_label"],
            "camera": scene["camera"],
            "camera_label": scene["camera_label"],
            "status": "queued", "segments": [],
        })
        texts.append(text)
    estimated = audition._estimate_audio_seconds(texts)
    if estimated > _MAX_TOTAL_AUDIO_SECONDS:
        raise HTTPException(status_code=400,
                            detail=f"台词总时长估算 {estimated:.1f}s 超过 "
                                   f"{_MAX_TOTAL_AUDIO_SECONDS:.0f}s，请精简")
    title = str(payload.title or "").strip()
    if not title:
        labels = [str(char_a.get("label") or cid_a), str(char_b.get("label") or cid_b)]
        if cid_c:
            labels.append(str(char_c.get("label") or cid_c))
            title = " × ".join(labels)
        else:
            title = f"{labels[0]} & {labels[1]} · 双人对戏"
    if len(title) > _TITLE_CHARS:
        raise HTTPException(status_code=400, detail=f"title 不能超过 {_TITLE_CHARS} 字")
    try:
        seed = int(payload.seed) if payload.seed is not None else 42
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="seed 必须是整数") from exc

    _mutex_check()

    dialogue_id = "dlg_" + uuid.uuid4().hex[:12]
    dialogue: Dict[str, Any] = {
        "id": dialogue_id,
        "character_a": cid_a, "character_b": cid_b,
        "character_a_label": str(char_a.get("label") or cid_a),
        "character_b_label": str(char_b.get("label") or cid_b),
        "character_c": cid_c,
        "character_c_label": str(char_c.get("label") or cid_c) if cid_c else None,
        "characters": {
            "a": {"id": cid_a, "label": str(char_a.get("label") or cid_a)},
            "b": {"id": cid_b, "label": str(char_b.get("label") or cid_b)},
            **({"c": {"id": cid_c,
                      "label": str(char_c.get("label") or cid_c)}} if cid_c else {}),
        },
        "character_data": {
            cid_a: char_a, cid_b: char_b,
            **({cid_c: char_c} if cid_c else {}),
        },
        "style": style,
        "portrait_file": portrait_file,
        "background": background,
        "background_label": audition._BACKGROUNDS[background][0],
        "camera": camera,
        "camera_label": audition._CAMERAS[camera][0],
        "scenes": scenes,
        "title": title,
        "seed": seed,
        "lines": lines,
        "total_lines": len(lines),
        "subtitles_burned": True,
        "status": "queued",
        "stage": "queued",
        "stage_history": ["queued"],
        "progress": 0,
        "current_line": -1,
        "output_url": None,
        "output_path": None,
        "poster_url": None,
        "srt_url": None,
        "duration_sec": None,
        "size_bytes": None,
        "width": _WIDTH,
        "height": _HEIGHT,
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
        running = _DIALOGUES.get(_ACTIVE_DIALOGUE) or {}
        if str(running.get("status")) in ("queued", "running"):
            raise HTTPException(status_code=409, detail=f"已有对谈任务在跑: {_ACTIVE_DIALOGUE}")
        _DIALOGUES[dialogue_id] = dialogue
        _ACTIVE_DIALOGUE = dialogue_id
        if len(_DIALOGUES) > 50:
            for old_id in sorted(_DIALOGUES, key=lambda k: _DIALOGUES[k].get("created_ts") or 0)[:-50]:
                if old_id != _ACTIVE_DIALOGUE:
                    _DIALOGUES.pop(old_id, None)
    _persist(dialogue)
    try:
        threading.Thread(target=_run_dialogue, args=(dialogue_id,), daemon=True,
                         name=f"dialogue-{dialogue_id}").start()
    except Exception as exc:  # noqa: BLE001
        with _LOCK:
            dialogue.update(status="failed", error=f"启动双人对戏线程失败: {exc}",
                            finished_at=_now(), finished_ts=time.time())
            _set_stage(dialogue, "failed")
            if _ACTIVE_DIALOGUE == dialogue_id:
                _ACTIVE_DIALOGUE = None
        _persist(dialogue)
        raise HTTPException(status_code=500, detail=f"启动双人对戏任务失败: {exc}") from exc
    return {
        "ok": True,
        "dialogue_id": dialogue_id,
        "job_id": dialogue_id,
        "character_a": cid_a,
        "character_b": cid_b,
        "character_c": cid_c,
        "characters": {
            "a": {"id": cid_a, "label": str(char_a.get("label") or cid_a)},
            "b": {"id": cid_b, "label": str(char_b.get("label") or cid_b)},
            **({"c": {"id": cid_c,
                      "label": str(char_c.get("label") or cid_c)}} if cid_c else {}),
        },
        "style": style,
        "background": background,
        "camera": camera,
        "scenes": scenes,
        "title": title,
        "total_lines": len(lines),
        "status": "queued",
        "estimated_sec": len(lines) * 120 + 30,
        "characters_count": len(ids),
    }


@router.get("/api/v5/dialogues/{dialogue_id}", summary="双人对戏任务状态")
def get_dialogue(dialogue_id: str):
    key = str(dialogue_id or "").strip()
    with _LOCK:
        dialogue = _DIALOGUES.get(key)
        if not dialogue:
            raise HTTPException(status_code=404, detail=f"双人对戏任务不存在: {dialogue_id}")
        return {"ok": True, **_view(dialogue)}


@router.post("/api/v5/dialogues/{dialogue_id}/cancel", summary="取消双人对戏任务")
def cancel_dialogue(dialogue_id: str):
    global _ACTIVE_DIALOGUE
    key = str(dialogue_id or "").strip()
    with _LOCK:
        dialogue = _DIALOGUES.get(key)
        if not dialogue:
            raise HTTPException(status_code=404, detail=f"双人对戏任务不存在: {dialogue_id}")
        if dialogue.get("status") not in ("queued", "running"):
            return {"ok": True, "dialogue_id": key, "status": dialogue.get("status"),
                    "message": "任务已结束，无需取消", **_view(dialogue)}
        dialogue.update(status="canceled", stage="canceled", error="用户取消")
        _set_stage(dialogue, "canceled")
        proc = _PROCS.get(key)
        _persist(dialogue)
    _kill_process(proc)
    with _LOCK:
        if _ACTIVE_DIALOGUE == key:
            _ACTIVE_DIALOGUE = None
        return {"ok": True, "dialogue_id": key, "status": "canceled", **_view(dialogue)}


@router.get("/api/v5/dialogues", summary="最近双人对戏（默认 5 条）")
def list_dialogues(limit: int = 5):
    limit = max(1, min(int(limit or 5), 20))
    with _LOCK:
        rows = sorted(_DIALOGUES.values(), key=lambda item: item.get("created_ts") or 0,
                      reverse=True)[:limit]
        views = [_view(dialogue) for dialogue in rows]
    return {"ok": True, "count": len(views), "dialogues": views}


def _load_persisted_dialogues() -> None:
    if not _DIALOGUE_DIR.is_dir():
        return
    paths = sorted(_DIALOGUE_DIR.glob("*.json"), key=lambda path: path.stat().st_mtime)
    for path in paths[-200:]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict) or not data.get("id"):
            continue
        if data.get("status") in ("queued", "running"):
            data.update(status="failed", stage="failed",
                        error="后端服务重启，双人对戏任务中断（可重新发起）",
                        finished_at=_now(), finished_ts=time.time())
            for line in data.get("lines") or []:
                if line.get("status") in ("queued", "running"):
                    line["status"] = "failed"
            _persist(data)
        _DIALOGUES[str(data["id"])] = data


_load_persisted_dialogues()
