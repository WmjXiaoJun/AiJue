# -*- coding: utf-8 -*-
"""V36-B：试镜合集发布包（source 固定 character_audition_reel.mp4）。

POST /api/v5/character-showcase/publish-package {title?, force?}
GET  /api/v5/character-showcase/publish-package

zip：video/character_audition_reel.mp4 + cover/cover.jpg + metadata.json + README.txt
    + subtitles/<character>.srt（扫描 outputs/audition_jobs 最新各角色 SRT）
输出 outputs/character_demos/publish/character_audition_reel.zip。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.parse
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel

from .. import config, jobs

router = APIRouter(tags=["character-showcase"])

_REPO = Path(config.REPO_ROOT)
_DEMO_DIR = Path(config.OUTPUTS_DIR) / "character_demos"
_REEL = _DEMO_DIR / "character_audition_reel.mp4"
_PUBLISH_DIR = _DEMO_DIR / "publish"
_AUDITION_DIR = Path(config.OUTPUTS_DIR) / "audition_jobs"
_SLUG = "character_audition_reel"
_DEFAULT_TITLE = "角色试镜合集"
_BUILD_LOCK = threading.Lock()


class ShowcaseBody(BaseModel):
    title: Optional[str] = None
    force: bool = False
    vertical: bool = False
    vertical_mode: str = "blur_pad"


def _catalog() -> Dict[str, Any]:
    try:
        from pipeline import v5_catalog  # noqa: PLC0415

        return v5_catalog.load_catalog().get("characters") or {}
    except Exception:  # noqa: BLE001
        return {}


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _media_info(path: Path) -> Dict[str, Any]:
    try:
        proc = subprocess.run(
            [str(jobs.ffprobe_bin()), "-v", "error", "-show_streams", "-show_format",
             "-of", "json", str(path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
        )
        data = json.loads(proc.stdout or "{}")
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return {}
    streams = data.get("streams") or []
    video = next((row for row in streams if row.get("codec_type") == "video"), {})
    fmt = data.get("format") if isinstance(data.get("format"), dict) else {}
    return {
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
        "fps": video.get("r_frame_rate"),
        "duration_sec": round(float(fmt.get("duration") or 0.0), 2),
        "size_bytes": int(fmt.get("size") or 0),
    }


def _audition_srts() -> List[Dict[str, str]]:
    """扫描 audition_jobs：每个角色取最新 done 任务的 SRT 文件（存在才带）。"""
    catalog = _catalog()
    latest: Dict[str, tuple] = {}
    if not _AUDITION_DIR.is_dir():
        return []
    for path in _AUDITION_DIR.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if data.get("status") != "done":
            continue
        srt_url = str(data.get("srt_url") or "")
        if not srt_url:
            continue
        rel = urllib.parse.unquote(srt_url.split("?")[0])
        if rel.startswith("/media/"):
            rel = rel[len("/media/"):]
        srt_path = Path(config.OUTPUTS_DIR) / rel
        if not srt_path.is_file() or srt_path.stat().st_size <= 0:
            continue
        cid = str(data.get("character") or "")
        if not cid:
            continue
        created = data.get("created_ts") or 0
        if cid not in latest or created > latest[cid][0]:
            latest[cid] = (created, srt_path, data)
    rows = []
    for cid, (_created, srt_path, data) in latest.items():
        label = str((catalog.get(cid) or {}).get("label") or cid)
        rows.append({"character": cid, "label": label,
                     "srt": srt_path, "job_id": str(data.get("id") or "")})
    rows.sort(key=lambda row: row["character"])
    return rows


def _make_cover(reel: Path, duration: float, dest: Path) -> bool:
    try:
        proc = subprocess.run(
            [str(jobs.ffmpeg_bin()), "-y", "-hide_banner", "-loglevel", "error",
             "-ss", f"{max(0.5, duration * 0.25):.2f}", "-i", str(reel),
             "-frames:v", "1", "-q:v", "3", str(dest)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0 and dest.is_file() and dest.stat().st_size > 0


def _make_vertical(reel: Path, duration: float, dest: Path, mode: str) -> bool:
    """生成 1080×1920 竖版：blur_pad（模糊填充）或 crop（中心裁切）。"""
    ffmpeg = str(jobs.ffmpeg_bin())
    if mode == "crop":
        video_filter = ("scale=1080:1920:force_original_aspect_ratio=increase,"
                        "crop=1080:1920,format=yuv420p")
        cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(reel),
               "-vf", video_filter, "-map", "0:v:0", "-map", "0:a?",
               "-c:v", "libx264", "-preset", "medium", "-crf", "20",
               "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2",
               "-movflags", "+faststart", str(dest)]
    else:
        filter_complex = (
            "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920,gblur=sigma=30[bg];"
            "[0:v]scale=1080:1920:force_original_aspect_ratio=decrease[fg];"
            "[bg][fg]overlay=(W-w)/2:(H-h)/2,format=yuv420p[outv]"
        )
        cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(reel),
               "-filter_complex", filter_complex, "-map", "[outv]", "-map", "0:a?",
               "-c:v", "libx264", "-preset", "medium", "-crf", "20",
               "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2",
               "-movflags", "+faststart", str(dest)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=600)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0 and dest.is_file() and dest.stat().st_size > 0


def _readme(metadata: Dict[str, Any], rows: List[Dict[str, str]],
            vertical: Optional[Dict[str, Any]] = None) -> str:
    chars = "、".join(f"{row['label']}（{row['character']}）" for row in rows) or "—"
    lines = [
        f"试镜合集发布包：{metadata['title']}",
        "",
        "一、包内文件",
        "  video/character_audition_reel.mp4   合集视频（16:9，字幕已烧录）",
        "  cover/cover.jpg                     16:9 封面帧",
        "  metadata.json                       标题/时长/分辨率/角色清单",
        "  subtitles/<character>.srt           各角色试镜 SRT",
        "  README.txt                          本说明",
    ]
    if vertical:
        lines.extend([
            "  video/character_audition_reel_vertical.mp4  竖版 1080×1920（TikTok/IG）",
            "  cover/cover_vertical.jpg                    竖版封面 1080×1920",
        ])
    lines.extend([
        "",
        "二、上传步骤",
        "  1. 登录目标平台创作中心，选择上传视频；",
        "  2. 上传 video/character_audition_reel.mp4（16:9，已含烧录中文字幕）；",
        "  3. 使用 cover/cover.jpg 作为封面；",
        "  4. 标题/简介/标签参考 metadata.json；",
        "  5. 如需平台字幕，可上传 subtitles/ 下对应角色 SRT。",
    ])
    if vertical:
        lines.extend([
            "",
            "三、竖版 9:16 说明",
            f"  模式：{vertical.get('mode')}（blur_pad=模糊填充；crop=中心裁切）；"
            f"分辨率：{vertical.get('resolution')}",
            "  竖版视频为同源内容转制，字幕已烧录；发布到 TikTok/IG/抖音时优先上传",
            "  video/character_audition_reel_vertical.mp4 并使用 cover/cover_vertical.jpg。",
        ])
    lines.extend([
        "",
        "四、说明",
        f"  时长：{metadata.get('duration_sec')}s；分辨率：{metadata.get('resolution')}；"
        f"fps：{metadata.get('fps')}",
        f"  角色：{chars}",
        "  字幕已烧录在视频内；SRT 仅作备用/二次编辑。",
        "",
    ])
    return "\n".join(lines)


def _build_package(title: str, force: bool, vertical: bool = False,
                   vertical_mode: str = "blur_pad") -> Dict[str, Any]:
    if not _REEL.is_file() or _REEL.stat().st_size <= 0:
        raise HTTPException(status_code=400,
                            detail=f"试镜合集视频不存在: {_REEL}（请先生成 10 角色试镜合集）")
    _PUBLISH_DIR.mkdir(parents=True, exist_ok=True)
    target = _PUBLISH_DIR / f"{_SLUG}.zip"
    with _BUILD_LOCK:
        if target.is_file() and not force:
            cached = _read_zip_metadata(target)
            if cached and _cache_matches(cached, title, vertical, vertical_mode):
                return _package_view(target, cached=cached, cached_flag=True)
        info = _media_info(_REEL)
        rows = _audition_srts()
        work = Path(tempfile.mkdtemp(prefix=".showcase-", dir=str(_DEMO_DIR)))
        try:
            cover = work / "cover.jpg"
            if not _make_cover(_REEL, float(info.get("duration_sec") or 0), cover):
                raise RuntimeError("封面抽帧失败")
            vertical_info: Optional[Dict[str, Any]] = None
            vertical_video: Optional[Path] = None
            vertical_cover: Optional[Path] = None
            warnings: List[str] = []
            if vertical:
                try:
                    vertical_video = work / "character_audition_reel_vertical.mp4"
                    if not _make_vertical(_REEL, float(info.get("duration_sec") or 0),
                                          vertical_video, vertical_mode):
                        raise RuntimeError("ffmpeg 竖版转制失败")
                    vertical_cover = work / "cover_vertical.jpg"
                    if not _make_cover(vertical_video, float(info.get("duration_sec") or 0),
                                       vertical_cover):
                        raise RuntimeError("竖版封面抽帧失败")
                    vertical_info = {
                        "file": "video/character_audition_reel_vertical.mp4",
                        "resolution": "1080x1920",
                        "mode": vertical_mode,
                    }
                except Exception as exc:  # noqa: BLE001 - 竖版失败不阻塞 16:9
                    warnings.append(f"竖版生成失败，已跳过（16:9 包正常）：{exc}")
                    vertical_info = None
                    vertical_video = None
                    vertical_cover = None
            metadata = {
                "title": title,
                "description": "造片局 AI 角色试镜合集：多位角色的写实/动漫立绘 + 角色音色 + H3 图生视频口型同步，带中文字幕。",
                "tags": ["角色试镜", "AI视频", "character audition", "showcase", "AI微综艺"],
                "duration_sec": info.get("duration_sec"),
                "resolution": f"{info.get('width')}x{info.get('height')}",
                "width": info.get("width"),
                "height": info.get("height"),
                "fps": info.get("fps"),
                "size_bytes": info.get("size_bytes"),
                "characters": [{"id": row["character"], "label": row["label"]} for row in rows],
                "srt_count": len(rows),
                "source": str(_REEL),
                "generated_at": _now_iso(),
                "subtitles_burned": True,
            }
            if vertical_info:
                metadata["vertical"] = vertical_info
            metadata_bytes = json.dumps(metadata, ensure_ascii=False, indent=2).encode("utf-8")
            readme_bytes = _readme(metadata, rows, vertical_info).encode("utf-8")
            tmp_zip = target.with_name(f".{target.name}.tmp-{os.getpid()}-{threading.get_ident()}")
            files: List[Dict[str, Any]] = []
            with zipfile.ZipFile(tmp_zip, "w", compression=zipfile.ZIP_DEFLATED,
                                 compresslevel=6) as zf:
                zf.write(_REEL, arcname=f"video/{_REEL.name}")
                files.append({"name": f"video/{_REEL.name}", "size": _REEL.stat().st_size})
                zf.write(cover, arcname="cover/cover.jpg")
                files.append({"name": "cover/cover.jpg", "size": cover.stat().st_size})
                if vertical_info and vertical_video and vertical_cover:
                    zf.write(vertical_video, arcname=vertical_info["file"])
                    files.append({"name": vertical_info["file"],
                                  "size": vertical_video.stat().st_size})
                    zf.write(vertical_cover, arcname="cover/cover_vertical.jpg")
                    files.append({"name": "cover/cover_vertical.jpg",
                                  "size": vertical_cover.stat().st_size})
                zf.writestr("metadata.json", metadata_bytes)
                files.append({"name": "metadata.json", "size": len(metadata_bytes)})
                zf.writestr("README.txt", readme_bytes)
                files.append({"name": "README.txt", "size": len(readme_bytes)})
                for row in rows:
                    arcname = f"subtitles/{row['character']}.srt"
                    zf.write(row["srt"], arcname=arcname)
                    files.append({"name": arcname, "size": row["srt"].stat().st_size})
            os.replace(tmp_zip, target)
        finally:
            shutil.rmtree(work, ignore_errors=True)
        view = _package_view(target, cached=metadata, cached_flag=False, warnings=warnings)
        view["files"] = files
        return view


def _cache_matches(cached: Dict[str, Any], title: str, vertical: bool,
                   vertical_mode: str) -> bool:
    if str(cached.get("title") or "") != title:
        return False
    cached_vertical = cached.get("vertical")
    if not vertical:
        return not cached_vertical
    if not isinstance(cached_vertical, dict):
        return False
    return str(cached_vertical.get("mode") or "") == vertical_mode


def _read_zip_metadata(path: Path) -> Optional[Dict[str, Any]]:
    try:
        with zipfile.ZipFile(path) as zf:
            if "metadata.json" not in zf.namelist():
                return None
            data = json.loads(zf.read("metadata.json").decode("utf-8"))
            return data if isinstance(data, dict) else None
    except (OSError, ValueError, zipfile.BadZipFile, UnicodeDecodeError):
        return None


def _package_view(path: Path, *, cached: Dict[str, Any], cached_flag: bool,
                  warnings: Optional[List[str]] = None) -> Dict[str, Any]:
    files = []
    try:
        with zipfile.ZipFile(path) as zf:
            for entry in zf.infolist():
                if not entry.is_dir():
                    files.append({"name": entry.filename, "size": int(entry.file_size)})
    except (OSError, zipfile.BadZipFile):
        pass
    stat = path.stat()
    rows = cached.get("characters") or []
    return {
        "ok": True,
        "package_url": f"/media/character_demos/publish/{path.name}?t={int(stat.st_mtime)}",
        "filename": path.name,
        "size_bytes": int(stat.st_size),
        "files": files,
        "cached": cached_flag,
        "title": cached.get("title"),
        "duration_sec": cached.get("duration_sec"),
        "resolution": cached.get("resolution"),
        "characters": rows,
        "srt_count": cached.get("srt_count"),
        "vertical": bool(cached.get("vertical")),
        "vertical_info": cached.get("vertical"),
        "vertical_mode": (cached.get("vertical") or {}).get("mode") if isinstance(cached.get("vertical"), dict) else None,
        "warnings": warnings or [],
        "video_url": f"/media/character_demos/{_REEL.name}?t={int(_REEL.stat().st_mtime)}",
        "generated_at": cached.get("generated_at"),
        "path": str(path),
    }


@router.post("/api/v5/character-showcase/publish-package",
             summary="构建试镜合集发布包 zip（视频/封面/元数据/README/各角色 SRT）")
def build_showcase_package(payload: ShowcaseBody = Body(default=ShowcaseBody())) -> Dict[str, Any]:
    title = str(payload.title or _DEFAULT_TITLE).strip() or _DEFAULT_TITLE
    if len(title) > 80:
        raise HTTPException(status_code=400, detail="title 不能超过 80 字")
    vertical_mode = str(payload.vertical_mode or "blur_pad").strip().lower()
    if vertical_mode not in ("blur_pad", "crop"):
        raise HTTPException(status_code=400,
                            detail="vertical_mode 只支持 blur_pad/crop"
                                   f"（当前 {vertical_mode!r}）")
    return _build_package(title, bool(payload.force), bool(payload.vertical), vertical_mode)


@router.get("/api/v5/character-showcase/publish-package",
            summary="列出试镜合集发布包")
def list_showcase_packages() -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    if _PUBLISH_DIR.is_dir():
        for path in sorted(_PUBLISH_DIR.glob("*.zip"),
                           key=lambda item: item.stat().st_mtime, reverse=True):
            metadata = _read_zip_metadata(path) or {}
            stat = path.stat()
            rows.append({
                "filename": path.name,
                "package_url": f"/media/character_demos/publish/{path.name}"
                               f"?t={int(stat.st_mtime)}",
                "size_bytes": int(stat.st_size),
                "title": metadata.get("title"),
                "duration_sec": metadata.get("duration_sec"),
                "resolution": metadata.get("resolution"),
                "srt_count": metadata.get("srt_count"),
                "vertical": metadata.get("vertical"),
                "vertical_mode": (metadata.get("vertical") or {}).get("mode")
                                 if isinstance(metadata.get("vertical"), dict) else None,
                "generated_at": metadata.get("generated_at"),
            })
    return {"ok": True, "count": len(rows), "packages": rows}
