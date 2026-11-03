# -*- coding: utf-8 -*-
"""V35：角色宣传片（多场景/多机位 + 片头卡/场景卡/字幕/海报）。

POST /api/v5/characters/{character}/promo
GET  /api/promos/{promo_id}
POST /api/promos/{promo_id}/cancel
GET  /api/v5/characters/{character}/promos?limit=5

每镜复用 audition 内部流程（TTS+拼接+segments+SRT+H3 audio_ref+字幕烧录），
再拼接片头卡（2.2s）/ 每镜场景卡（1.0s）/ 各镜正片，输出 1024×576 24fps AAC 宣传片。
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
from . import character_audition as audition

router = APIRouter(tags=["character-promo"])

_REPO = Path(config.REPO_ROOT)
_PROMO_DIR = Path(config.OUTPUTS_DIR) / "promo_jobs"
_DEMO_DIR = Path(config.OUTPUTS_DIR) / "character_demos"
_H3_CLI = _REPO / "integrations" / "comfyui" / "run_h3_ref.py"

_PORTRAIT_BY_STYLE = audition._PORTRAIT_BY_STYLE
_STYLES = audition._STYLES
_MAX_SHOTS = 3
_MAX_LINES_PER_SHOT = 2
_MAX_LINE_CHARS = 60
_MAX_SHOT_AUDIO_SECONDS = 8.0
_TITLE_CHARS = 40
_TITLE_CARD_SECONDS = 2.2
_SCENE_CARD_SECONDS = 1.0
_WIDTH = 1024
_HEIGHT = 576
_FPS = 24

_LOCK = threading.RLock()
_PROMOS: Dict[str, Dict[str, Any]] = {}
_PROCS: Dict[str, subprocess.Popen] = {}
_ACTIVE_PROMO: Optional[str] = None


class _PromoCanceled(Exception):
    pass


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _persist(promo: Dict[str, Any]) -> None:
    try:
        _PROMO_DIR.mkdir(parents=True, exist_ok=True)
        target = _PROMO_DIR / f"{promo['id']}.json"
        tmp = target.with_name(f"{target.name}.tmp-{os.getpid()}-{threading.get_ident()}")
        tmp.write_text(json.dumps(promo, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, target)
    except OSError:
        pass


def _elapsed(promo: Dict[str, Any]) -> float:
    created = promo.get("created_ts")
    if not isinstance(created, (int, float)):
        return 0.0
    finished = promo.get("finished_ts")
    end = float(finished) if isinstance(finished, (int, float)) else time.time()
    return round(max(0.0, end - float(created)), 1)


def _set_stage(promo: Dict[str, Any], stage: str, progress: Optional[int] = None) -> None:
    promo["stage"] = stage
    if progress is not None:
        promo["progress"] = int(progress)
    history = promo.setdefault("stage_history", [])
    if not history or history[-1] != stage:
        history.append(stage)


def _shot_view(shot: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "index": shot.get("index"),
        "lines": shot.get("lines") or [],
        "background": shot.get("background"),
        "background_label": shot.get("background_label"),
        "camera": shot.get("camera"),
        "camera_label": shot.get("camera_label"),
        "subtitles": bool(shot.get("subtitles", True)),
        "status": shot.get("status"),
        "segments": shot.get("segments") or [],
        "duration_sec": shot.get("duration_sec"),
        "output_url": shot.get("output_url"),
        "error": shot.get("error"),
    }


def _view(promo: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": promo.get("id"),
        "job_id": promo.get("id"),
        "character": promo.get("character"),
        "style": promo.get("style"),
        "title": promo.get("title"),
        "status": promo.get("status"),
        "stage": promo.get("stage"),
        "stage_history": list(promo.get("stage_history") or []),
        "progress": promo.get("progress", 0),
        "current_shot": promo.get("current_shot", -1),
        "total_shots": promo.get("total_shots", len(promo.get("shots") or [])),
        "shots": [_shot_view(shot) for shot in promo.get("shots") or []],
        "output_url": promo.get("output_url"),
        "poster_url": promo.get("poster_url"),
        "srt_url": promo.get("srt_url"),
        "duration_sec": promo.get("duration_sec"),
        "size_bytes": promo.get("size_bytes"),
        "width": promo.get("width"),
        "height": promo.get("height"),
        "seed": promo.get("seed"),
        "error": promo.get("error"),
        "warnings": promo.get("warnings") or [],
        "created_at": promo.get("created_at"),
        "started_at": promo.get("started_at"),
        "finished_at": promo.get("finished_at"),
        "elapsed_sec": _elapsed(promo),
    }


def active_promo_job() -> Optional[Dict[str, Any]]:
    """供 audition/其他 router 判断宣传片是否运行。"""
    with _LOCK:
        if not _ACTIVE_PROMO:
            return None
        promo = _PROMOS.get(_ACTIVE_PROMO) or {}
        if promo.get("status") in ("queued", "running"):
            return _view(promo)
    return None


# --------------------------------------------------------------------------- #
# 子进程 / 取消
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


def _proc_canceled(promo_id: str) -> bool:
    with _LOCK:
        promo = _PROMOS.get(promo_id) or {}
        return promo.get("status") == "canceled"


def _run_ffmpeg(promo_id: str, cmd: List[str], timeout: float = 900.0) -> None:
    """跑 ffmpeg 子进程（注册以便取消时 taskkill）。"""
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                            text=True, encoding="utf-8", errors="replace")
    with _LOCK:
        _PROCS[promo_id] = proc
    started = time.time()
    try:
        while True:
            if _proc_canceled(promo_id):
                _kill_process(proc)
                raise _PromoCanceled()
            if proc.poll() is not None:
                break
            if time.time() - started > timeout:
                _kill_process(proc)
                raise RuntimeError(f"ffmpeg 超时（{timeout:.0f}s）")
            time.sleep(0.2)
        stderr = (proc.communicate()[1] or "")
        if proc.returncode != 0:
            tail = "\n".join(stderr.strip().splitlines()[-8:])
            raise RuntimeError(f"ffmpeg 失败（returncode={proc.returncode}）：{tail[-500:]}")
    finally:
        with _LOCK:
            _PROCS.pop(promo_id, None)


# --------------------------------------------------------------------------- #
# 卡片 / 海报
# --------------------------------------------------------------------------- #
def _font(size: int):
    from PIL import ImageFont  # noqa: PLC0415

    for name in ("msyh.ttc", "msyhbd.ttc", "simhei.ttf", "simsun.ttc"):
        path = Path("C:/Windows/Fonts") / name
        if path.is_file():
            try:
                return ImageFont.truetype(str(path), size)
            except OSError:
                continue
    return ImageFont.load_default()


def _draw_center(draw, text: str, y: int, font, fill, width: int = _WIDTH) -> None:
    try:
        box = draw.textbbox((0, 0), text, font=font)
        text_width = box[2] - box[0]
    except Exception:  # noqa: BLE001
        text_width = len(text) * font.size
    draw.text(((width - text_width) // 2, y), text, font=font, fill=fill)


def _title_card(promo: Dict[str, Any], path: Path) -> None:
    from PIL import Image, ImageDraw  # noqa: PLC0415

    image = Image.new("RGB", (_WIDTH, _HEIGHT), (13, 16, 22))
    draw = ImageDraw.Draw(image)
    # 装饰线 + 顶部微光
    for y in range(0, 180):
        shade = 13 + int(10 * (1 - y / 180))
        draw.line([(0, y), (_WIDTH, y)], fill=(shade, shade + 2, shade + 6))
    draw.rectangle([72, 330, 952, 336], fill=(245, 197, 24))
    _draw_center(draw, "角色宣传片", 90, _font(28), (150, 158, 170))
    _draw_center(draw, str(promo.get("label") or promo.get("character") or ""), 170,
                 _font(72), (245, 245, 248))
    title = str(promo.get("title") or "")
    if title:
        _draw_center(draw, title, 265, _font(34), (245, 197, 24))
    _draw_center(draw, "CHARACTER PROMO", 420, _font(22), (110, 120, 135))
    image.save(path, "PNG")


def _scene_card(shot: Dict[str, Any], path: Path) -> None:
    from PIL import Image, ImageDraw  # noqa: PLC0415

    image = Image.new("RGB", (_WIDTH, _HEIGHT), (16, 20, 27))
    draw = ImageDraw.Draw(image)
    draw.rectangle([0, 0, 14, _HEIGHT], fill=(245, 197, 24))
    _draw_center(draw, f"SCENE {int(shot.get('index') or 0) + 1:02d}", 150,
                 _font(26), (130, 140, 155))
    _draw_center(draw, f"{shot.get('background_label') or ''} · {shot.get('camera_label') or ''}",
                 220, _font(52), (245, 245, 248))
    _draw_center(draw, "背景 · 机位", 310, _font(26), (150, 160, 175))
    image.save(path, "PNG")


def _card_video(png: Path, duration: float, out: Path, promo_id: str) -> None:
    _run_ffmpeg(promo_id, [
        str(jobs.ffmpeg_bin()), "-y", "-hide_banner", "-loglevel", "error",
        "-loop", "1", "-t", f"{duration:.3f}", "-i", str(png),
        "-f", "lavfi", "-t", f"{duration:.3f}", "-i", "anullsrc=r=48000:cl=stereo",
        "-shortest", "-r", str(_FPS), "-s", f"{_WIDTH}x{_HEIGHT}", "-pix_fmt", "yuv420p",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart", str(out),
    ], timeout=120)


def _normalize_shot(src: Path, out: Path, promo_id: str) -> None:
    _run_ffmpeg(promo_id, [
        str(jobs.ffmpeg_bin()), "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(src),
        "-vf", f"fps={_FPS},scale={_WIDTH}:{_HEIGHT}:flags=lanczos,format=yuv420p",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart", str(out),
    ], timeout=300)


# --------------------------------------------------------------------------- #
# 单镜生成（复用 audition 内部流程）
# --------------------------------------------------------------------------- #
def _run_shot_h3(promo: Dict[str, Any], shot: Dict[str, Any], workdir: Path,
                 ref: Path, audio: Path, duration: float, warnings: List[str]) -> Path:
    promo_id = str(promo.get("id"))
    prompt = audition._build_prompt(
        promo.get("character_data") or {}, str(promo.get("style") or "realistic"), "",
        background=str(shot.get("background") or "studio"),
        camera=str(shot.get("camera") or "medium"),
    )
    out = workdir / "shot.mp4"
    cmd = [
        sys.executable, str(_H3_CLI), "--mode", "audio_ref",
        "--image", str(ref), "--audio", str(audio), "--out", str(out),
        "--duration", f"{duration + 0.1:.3f}",
        "--width", str(_WIDTH), "--height", str(_HEIGHT),
        "--fps", "25", "--seed", str(promo.get("seed") or 42),
        "--prompt", prompt, "--timeout", "1800",
    ]
    proc = subprocess.Popen(cmd, cwd=str(_REPO), stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                            errors="replace", bufsize=1)
    with _LOCK:
        _PROCS[promo_id] = proc
    tail: List[str] = []
    try:
        while True:
            if _proc_canceled(promo_id):
                _kill_process(proc)
                raise _PromoCanceled()
            line = proc.stdout.readline() if proc.stdout is not None else ""
            if line:
                tail.append(line.strip())
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
            _PROCS.pop(promo_id, None)
    if _proc_canceled(promo_id):
        raise _PromoCanceled()
    if proc.returncode != 0:
        tail_text = "\n".join(tail[-10:])
        raise RuntimeError(
            f"H3 生成失败（returncode={proc.returncode}）：{tail_text[-800:]}")
    if not out.is_file() or out.stat().st_size <= 0:
        raise RuntimeError("H3 未产出视频文件")
    return out


def _build_assemble(promo: Dict[str, Any], workdir: Path, warnings: List[str]) -> None:
    """片头卡 + 每镜场景卡 + 各镜正片 → 最终宣传片 + poster + 合并 SRT。"""
    promo_id = str(promo["id"])
    shots = promo.get("shots") or []
    segments: List[Path] = []
    title_png = workdir / "title.png"
    _title_card(promo, title_png)
    title_mp4 = workdir / "title.mp4"
    _card_video(title_png, _TITLE_CARD_SECONDS, title_mp4, promo_id)
    segments.append(title_mp4)

    merged_segments: List[Dict[str, Any]] = []
    offset = _TITLE_CARD_SECONDS
    for shot in shots:
        scene_png = workdir / f"scene_{shot['index']}.png"
        _scene_card(shot, scene_png)
        scene_mp4 = workdir / f"scene_{shot['index']}.mp4"
        _card_video(scene_png, _SCENE_CARD_SECONDS, scene_mp4, promo_id)
        segments.append(scene_mp4)
        offset += _SCENE_CARD_SECONDS

        raw = Path(str(shot.get("output_path") or ""))
        if not raw.is_file():
            raise RuntimeError(f"第 {shot['index'] + 1} 镜视频不存在")
        normalized = workdir / f"shot_{shot['index']}_norm.mp4"
        _normalize_shot(raw, normalized, promo_id)
        segments.append(normalized)
        shot_duration = audition._media_info(normalized).get("duration_sec") or 0.0
        for seg in shot.get("segments") or []:
            merged_segments.append({
                "index": len(merged_segments),
                "text": seg["text"],
                "start": round(offset + float(seg["start"]), 3),
                "end": round(offset + float(seg["end"]), 3),
            })
        offset += float(shot_duration)

    concat_file = workdir / "concat.txt"
    concat_file.write_text(
        "\n".join(f"file '{str(path.resolve()).replace(chr(92), '/').replace(chr(39), chr(39) + chr(92) + chr(39) + chr(39))}'"
                  for path in segments) + "\n",
        encoding="utf-8",
    )
    out = _DEMO_DIR / f"{promo.get('character')}_promo_{promo_id}.mp4"
    _DEMO_DIR.mkdir(parents=True, exist_ok=True)
    _run_ffmpeg(promo_id, [
        str(jobs.ffmpeg_bin()), "-y", "-hide_banner", "-loglevel", "error",
        "-f", "concat", "-safe", "0", "-i", str(concat_file),
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart", str(out),
    ], timeout=600)

    srt_path = workdir / "promo.srt"
    audition._write_srt(merged_segments, srt_path)
    poster = workdir / "poster.jpg"
    _run_ffmpeg(promo_id, [
        str(jobs.ffmpeg_bin()), "-y", "-hide_banner", "-loglevel", "error",
        "-ss", "1.0", "-i", str(out), "-frames:v", "1", "-q:v", "3", str(poster),
    ], timeout=120)
    info = audition._media_info(out)
    with _LOCK:
        promo.update(
            output_path=str(out),
            output_url=f"/media/character_demos/{out.name}?t={int(out.stat().st_mtime)}",
            poster_url=f"/media/promo_jobs/{workdir.name}/{poster.name}"
                       f"?t={int(poster.stat().st_mtime)}",
            srt_url=f"/media/promo_jobs/{workdir.name}/{srt_path.name}"
                    f"?t={int(srt_path.stat().st_mtime)}",
            duration_sec=info.get("duration_sec"),
            size_bytes=int(out.stat().st_size),
            width=info.get("width") or _WIDTH,
            height=info.get("height") or _HEIGHT,
            merged_segments=merged_segments,
        )
        _persist(promo)


# --------------------------------------------------------------------------- #
# 任务线程
# --------------------------------------------------------------------------- #
def _run_promo(promo_id: str) -> None:
    global _ACTIVE_PROMO
    promo = _PROMOS.get(promo_id)
    if not promo:
        return
    warnings: List[str] = promo.get("warnings") or []
    try:
        with _LOCK:
            if promo.get("status") == "canceled":
                raise _PromoCanceled()
            promo.update(status="running", started_at=_now(), started_ts=time.time(),
                         error=None)
            _set_stage(promo, "shot_1_tts", 2)
            promo["current_shot"] = 0
            _persist(promo)
        character = promo.get("character_data") or {}
        style = str(promo.get("style") or "realistic")
        shots = promo.get("shots") or []
        workdir = _PROMO_DIR / f"{promo_id}_work"
        workdir.mkdir(parents=True, exist_ok=True)
        portrait = (_REPO / "assets" / "v5" / "characters" / str(promo.get("character"))
                    / str(promo.get("portrait_file")))
        for shot in shots:
            index = int(shot.get("index") or 0)
            shot_dir = workdir / f"shot_{index}"
            shot_dir.mkdir(parents=True, exist_ok=True)
            with _LOCK:
                if promo.get("status") == "canceled":
                    raise _PromoCanceled()
                promo["current_shot"] = index
                shot["status"] = "running"
                _set_stage(promo, f"shot_{index + 1}_tts",
                           2 + int(index / max(1, len(shots)) * 85))
                _persist(promo)
            # 1) TTS + segments + SRT
            audio, segments, duration, _engines = audition._synthesize_lines(
                character, list(shot.get("lines") or []), shot_dir, warnings)
            if duration > _MAX_SHOT_AUDIO_SECONDS:
                raise RuntimeError(
                    f"第 {index + 1} 镜 TTS 总时长 {duration:.2f}s 超过 {_MAX_SHOT_AUDIO_SECONDS:.0f}s")
            srt_path = shot_dir / "subtitles.srt"
            audition._write_srt(segments, srt_path)
            # 2) 立绘 16:9 参考图
            ref = shot_dir / "ref_16x9.png"
            audition._crop_ref_16x9(portrait, ref, _WIDTH, _HEIGHT)
            # 3) H3 audio_ref
            with _LOCK:
                if promo.get("status") == "canceled":
                    raise _PromoCanceled()
                _set_stage(promo, f"shot_{index + 1}_generate",
                           8 + int(index / max(1, len(shots)) * 85))
                _persist(promo)
            raw = _run_shot_h3(promo, shot, shot_dir, ref, audio, duration, warnings)
            # 4) 字幕烧录
            final_shot = raw
            if bool(shot.get("subtitles", True)):
                subtitled = shot_dir / "shot_sub.mp4"
                if audition._burn_subtitles(raw, subtitled, shot_dir, warnings):
                    final_shot = subtitled
            info = audition._media_info(final_shot)
            with _LOCK:
                shot.update(
                    status="done",
                    segments=segments,
                    duration_sec=info.get("duration_sec"),
                    output_path=str(final_shot),
                    output_url=None,
                    tts_engine=",".join(sorted(set(_engines))),
                )
                _persist(promo)
        # 5) 拼接
        with _LOCK:
            if promo.get("status") == "canceled":
                raise _PromoCanceled()
            _set_stage(promo, "assemble", 90)
            promo["current_shot"] = -1
            _persist(promo)
        _build_assemble(promo, workdir, warnings)
        with _LOCK:
            if promo.get("status") == "canceled":
                raise _PromoCanceled()
            promo.update(status="done", warnings=warnings,
                         finished_at=_now(), finished_ts=time.time())
            _set_stage(promo, "done", 100)
            _persist(promo)
    except _PromoCanceled:
        with _LOCK:
            promo.update(status="canceled", error="用户取消",
                         finished_at=_now(), finished_ts=time.time(), warnings=warnings)
            _set_stage(promo, "canceled")
            for shot in promo.get("shots") or []:
                if shot.get("status") in ("queued", "running"):
                    shot["status"] = "canceled"
            _persist(promo)
    except Exception as exc:  # noqa: BLE001
        with _LOCK:
            promo.update(status="failed", error=str(exc)[:900],
                         finished_at=_now(), finished_ts=time.time(), warnings=warnings)
            _set_stage(promo, "failed")
            for shot in promo.get("shots") or []:
                if shot.get("status") in ("queued", "running"):
                    shot["status"] = "failed"
            _persist(promo)
    finally:
        with _LOCK:
            _PROCS.pop(promo_id, None)
            if _ACTIVE_PROMO == promo_id:
                _ACTIVE_PROMO = None


# --------------------------------------------------------------------------- #
# 请求模型 / 端点
# --------------------------------------------------------------------------- #
class PromoShotBody(BaseModel):
    lines: Optional[List[str]] = None
    text: Optional[str] = None
    background: str = "studio"
    camera: str = "medium"
    subtitles: bool = True


class PromoBody(BaseModel):
    style: str = "realistic"
    title: Optional[str] = None
    seed: Optional[int] = None
    shots: List[PromoShotBody]


@router.post("/api/v5/characters/{character}/promo", status_code=202,
             summary="创建角色宣传片（多场景/机位，片头卡+场景卡+字幕）")
def create_promo(character: str, payload: PromoBody = Body(default=PromoBody(shots=[]))) -> dict:
    global _ACTIVE_PROMO
    cid = str(character or "").strip().lower()
    characters = audition._catalog_characters()
    char = characters.get(cid)
    if not isinstance(char, dict):
        raise HTTPException(status_code=404, detail=f"角色不存在: {cid}")
    style = str(payload.style or "realistic").strip().lower()
    if style not in _PORTRAIT_BY_STYLE:
        raise HTTPException(status_code=400,
                            detail=f"style 只支持 {', '.join(_STYLES)}（当前 {style!r}）")
    portrait_file = _PORTRAIT_BY_STYLE[style]
    portrait = (_REPO / "assets" / "v5" / "characters" / cid / portrait_file)
    if not portrait.is_file() or portrait.stat().st_size <= 0:
        raise HTTPException(status_code=400,
                            detail=f"角色 {cid} 缺少 {style} 立绘 {portrait_file}；"
                                   "请先在角色详情生成该立绘版本")
    raw_shots = payload.shots or []
    if not (1 <= len(raw_shots) <= _MAX_SHOTS):
        raise HTTPException(status_code=400, detail=f"shots 需要 1–{_MAX_SHOTS} 镜")
    title = str(payload.title or "").strip()
    if not title:
        title = f"{str(char.get('label') or cid)} · 角色宣传片"
    if len(title) > _TITLE_CHARS:
        raise HTTPException(status_code=400, detail=f"title 不能超过 {_TITLE_CHARS} 字")
    try:
        seed = int(payload.seed) if payload.seed is not None else 42
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="seed 必须是整数") from exc
    shots: List[Dict[str, Any]] = []
    for index, raw in enumerate(raw_shots):
        lines: List[str] = []
        if getattr(raw, "lines", None):
            source = list(raw.lines)
        elif str(getattr(raw, "text", "") or "").strip():
            source = [str(raw.text)]
        else:
            source = [str(audition._DEFAULT_TEXT.format(label=str(char.get("label") or cid)))]
        for line_index, item in enumerate(source):
            line = str(item or "").strip()
            if not line:
                continue
            if len(line) > _MAX_LINE_CHARS:
                raise HTTPException(status_code=400,
                                    detail=f"第 {index + 1} 镜第 {line_index + 1} 行超过 {_MAX_LINE_CHARS} 字")
            lines.append(line)
        if not lines:
            lines = [str(audition._DEFAULT_TEXT.format(label=str(char.get("label") or cid)))]
        if len(lines) > _MAX_LINES_PER_SHOT:
            raise HTTPException(status_code=400,
                                detail=f"第 {index + 1} 镜最多 {_MAX_LINES_PER_SHOT} 行")
        estimated = audition._estimate_audio_seconds(lines)
        if estimated > _MAX_SHOT_AUDIO_SECONDS:
            raise HTTPException(status_code=400,
                                detail=f"第 {index + 1} 镜台词太长（估算 {estimated:.1f}s > 8s）")
        background = str(raw.background or "studio").strip().lower()
        if background not in audition._BACKGROUNDS:
            raise HTTPException(status_code=400,
                                detail=f"第 {index + 1} 镜 background 非法: {background!r}")
        camera = str(raw.camera or "medium").strip().lower()
        if camera not in audition._CAMERAS:
            raise HTTPException(status_code=400,
                                detail=f"第 {index + 1} 镜 camera 非法: {camera!r}")
        shots.append({
            "index": index,
            "lines": lines,
            "background": background,
            "background_label": audition._BACKGROUNDS[background][0],
            "camera": camera,
            "camera_label": audition._CAMERAS[camera][0],
            "subtitles": bool(raw.subtitles),
            "status": "queued",
            "segments": [],
        })

    # GPU / 任务互斥
    try:
        from .pipeline_ui import active_pipeline_job  # noqa: PLC0415

        active_pipeline = active_pipeline_job()
    except Exception:  # noqa: BLE001
        active_pipeline = None
    if active_pipeline:
        raise HTTPException(status_code=409, detail={
            "message": "3D 生成任务运行中，暂不能生成宣传片",
            "reason": "pipeline_running", "job_id": active_pipeline.get("id"),
        })
    # V36：与双人对戏互斥
    try:
        from . import character_dialogue  # noqa: PLC0415

        active_dialogue = character_dialogue.active_dialogue_job()
    except Exception:  # noqa: BLE001
        active_dialogue = None
    if active_dialogue:
        raise HTTPException(status_code=409, detail={
            "message": "双人对戏任务运行中，暂不能生成宣传片",
            "reason": "dialogue_running", "job_id": active_dialogue.get("id"),
        })
    with _LOCK:
        audition_active = None
        if audition._ACTIVE_JOB:
            job = audition._JOBS.get(audition._ACTIVE_JOB) or {}
            if job.get("status") in ("queued", "running"):
                audition_active = audition._ACTIVE_JOB
        if audition_active:
            raise HTTPException(status_code=409, detail={
                "message": "试镜任务运行中，暂不能生成宣传片",
                "reason": "audition_running", "job_id": audition_active,
            })
        if _ACTIVE_PROMO:
            current = _PROMOS.get(_ACTIVE_PROMO) or {}
            if current.get("status") in ("queued", "running"):
                raise HTTPException(status_code=409, detail={
                    "message": "已有宣传片任务在运行（GPU 单并发）",
                    "reason": "promo_running", "job_id": _ACTIVE_PROMO,
                })

    promo_id = "pr_" + uuid.uuid4().hex[:12]
    promo: Dict[str, Any] = {
        "id": promo_id,
        "character": cid,
        "character_data": char,
        "style": style,
        "title": title,
        "label": str(char.get("label") or cid),
        "seed": seed,
        "portrait_file": portrait_file,
        "shots": shots,
        "total_shots": len(shots),
        "status": "queued",
        "stage": "queued",
        "stage_history": ["queued"],
        "progress": 0,
        "current_shot": -1,
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
        _PROMOS[promo_id] = promo
        _ACTIVE_PROMO = promo_id
    _persist(promo)
    try:
        threading.Thread(target=_run_promo, args=(promo_id,), daemon=True,
                         name=f"promo-{promo_id}").start()
    except Exception as exc:  # noqa: BLE001
        with _LOCK:
            promo.update(status="failed", error=f"启动宣传片线程失败: {exc}",
                         finished_at=_now(), finished_ts=time.time())
            _set_stage(promo, "failed")
            if _ACTIVE_PROMO == promo_id:
                _ACTIVE_PROMO = None
        _persist(promo)
        raise HTTPException(status_code=500, detail=f"启动宣传片任务失败: {exc}") from exc
    return {
        "ok": True,
        "promo_id": promo_id,
        "job_id": promo_id,
        "character": cid,
        "style": style,
        "title": title,
        "total_shots": len(shots),
        "shots_count": len(shots),
        "status": "queued",
        "estimated_sec": len(shots) * 180 + 30,
    }


@router.get("/api/promos/{promo_id}", summary="角色宣传片任务状态")
def get_promo(promo_id: str):
    key = str(promo_id or "").strip()
    with _LOCK:
        promo = _PROMOS.get(key)
        if not promo:
            raise HTTPException(status_code=404, detail=f"宣传片任务不存在: {promo_id}")
        return {"ok": True, **_view(promo)}


@router.post("/api/promos/{promo_id}/cancel", summary="取消宣传片任务（终止当前 H3/ffmpeg）")
def cancel_promo(promo_id: str):
    global _ACTIVE_PROMO
    key = str(promo_id or "").strip()
    with _LOCK:
        promo = _PROMOS.get(key)
        if not promo:
            raise HTTPException(status_code=404, detail=f"宣传片任务不存在: {promo_id}")
        if promo.get("status") not in ("queued", "running"):
            return {"ok": True, "promo_id": key, "status": promo.get("status"),
                    "message": "任务已结束，无需取消", **_view(promo)}
        promo.update(status="canceled", stage="canceled", error="用户取消")
        _set_stage(promo, "canceled")
        proc = _PROCS.get(key)
        _persist(promo)
    _kill_process(proc)
    with _LOCK:
        if _ACTIVE_PROMO == key:
            _ACTIVE_PROMO = None
        return {"ok": True, "promo_id": key, "status": "canceled", **_view(promo)}


@router.get("/api/v5/characters/{character}/promos", summary="角色最近宣传片")
def list_character_promos(character: str, limit: int = 5):
    cid = str(character or "").strip().lower()
    if cid not in audition._catalog_characters():
        raise HTTPException(status_code=404, detail=f"角色不存在: {cid}")
    limit = max(1, min(int(limit or 5), 20))
    with _LOCK:
        rows = sorted([promo for promo in _PROMOS.values() if promo.get("character") == cid],
                      key=lambda promo: promo.get("created_ts") or 0, reverse=True)[:limit]
        views = [_view(promo) for promo in rows]
    return {"ok": True, "character": cid, "count": len(views), "promos": views}


def _load_persisted_promos() -> None:
    if not _PROMO_DIR.is_dir():
        return
    paths = sorted(_PROMO_DIR.glob("*.json"), key=lambda path: path.stat().st_mtime)
    for path in paths[-200:]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict) or not data.get("id"):
            continue
        if data.get("status") in ("queued", "running"):
            data.update(status="failed", stage="failed",
                        error="后端服务重启，宣传片任务中断（可重新发起）",
                        finished_at=_now(), finished_ts=time.time())
            for shot in data.get("shots") or []:
                if shot.get("status") in ("queued", "running"):
                    shot["status"] = "failed"
            _persist(data)
        _PROMOS[str(data["id"])] = data


_load_persisted_promos()
