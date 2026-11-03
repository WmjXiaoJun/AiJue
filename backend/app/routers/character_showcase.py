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


def _readme(metadata: Dict[str, Any], rows: List[Dict[str, str]]) -> str:
    chars = "、".join(f"{row['label']}（{row['character']}）" for row in rows) or "—"
    lines = [
        f"试镜合集发布包：{metadata['title']}",
        "",
        "一、包内文件",
        "  video/character_audition_reel.mp4   合集视频（字幕已烧录）",
        "  cover/cover.jpg                     封面帧",
        "  metadata.json                       标题/时长/分辨率/角色清单",
        "  subtitles/<character>.srt           各角色试镜 SRT",
        "  README.txt                          本说明",
        "",
        "二、上传步骤",
        "  1. 登录目标平台创作中心，选择上传视频；",
        "  2. 上传 video/character_audition_reel.mp4（已含烧录中文字幕）；",
        "  3. 使用 cover/cover.jpg 作为封面；",
        "  4. 标题/简介/标签参考 metadata.json；",
        "  5. 如需平台字幕，可上传 subtitles/ 下对应角色 SRT。",
        "",
        "三、说明",
        f"  时长：{metadata.get('duration_sec')}s；分辨率：{metadata.get('resolution')}；"
        f"fps：{metadata.get('fps')}",
        f"  角色：{chars}",
        "  字幕已烧录在视频内；SRT 仅作备用/二次编辑。",
        "",
    ]
    return "\n".join(lines)


def _build_package(title: str, force: bool) -> Dict[str, Any]:
    if not _REEL.is_file() or _REEL.stat().st_size <= 0:
        raise HTTPException(status_code=400,
                            detail=f"试镜合集视频不存在: {_REEL}（请先生成 10 角色试镜合集）")
    _PUBLISH_DIR.mkdir(parents=True, exist_ok=True)
    target = _PUBLISH_DIR / f"{_SLUG}.zip"
    with _BUILD_LOCK:
        if target.is_file() and not force:
            cached = _read_zip_metadata(target)
            if cached and str(cached.get("title") or "") == title:
                return _package_view(target, cached=cached, cached_flag=True)
        info = _media_info(_REEL)
        rows = _audition_srts()
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
        work = Path(tempfile.mkdtemp(prefix=".showcase-", dir=str(_DEMO_DIR)))
        try:
            cover = work / "cover.jpg"
            if not _make_cover(_REEL, float(info.get("duration_sec") or 0), cover):
                raise RuntimeError("封面抽帧失败")
            metadata_bytes = json.dumps(metadata, ensure_ascii=False, indent=2).encode("utf-8")
            readme_bytes = _readme(metadata, rows).encode("utf-8")
            tmp_zip = target.with_name(f".{target.name}.tmp-{os.getpid()}-{threading.get_ident()}")
            files: List[Dict[str, Any]] = []
            with zipfile.ZipFile(tmp_zip, "w", compression=zipfile.ZIP_DEFLATED,
                                 compresslevel=6) as zf:
                zf.write(_REEL, arcname=f"video/{_REEL.name}")
                files.append({"name": f"video/{_REEL.name}", "size": _REEL.stat().st_size})
                zf.write(cover, arcname="cover/cover.jpg")
                files.append({"name": "cover/cover.jpg", "size": cover.stat().st_size})
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
        view = _package_view(target, cached=metadata, cached_flag=False)
        view["files"] = files
        return view


def _read_zip_metadata(path: Path) -> Optional[Dict[str, Any]]:
    try:
        with zipfile.ZipFile(path) as zf:
            if "metadata.json" not in zf.namelist():
                return None
            data = json.loads(zf.read("metadata.json").decode("utf-8"))
            return data if isinstance(data, dict) else None
    except (OSError, ValueError, zipfile.BadZipFile, UnicodeDecodeError):
        return None


def _package_view(path: Path, *, cached: Dict[str, Any], cached_flag: bool) -> Dict[str, Any]:
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
    return _build_package(title, bool(payload.force))


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
                "generated_at": metadata.get("generated_at"),
            })
    return {"ok": True, "count": len(rows), "packages": rows}
