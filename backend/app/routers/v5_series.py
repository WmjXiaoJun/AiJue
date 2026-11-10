# -*- coding: utf-8 -*-
"""V48.1：系列导出合集——片头卡 + 各集串联成一个 mp4。"""
from __future__ import annotations

import hashlib
import json
import pathlib
import shutil
import subprocess
import threading
import time
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException

from .. import config, ids

router = APIRouter(prefix="/api/v5", tags=["v5-series"])

REPO = pathlib.Path(__file__).resolve().parents[3]
SERIES_DIR = REPO / "contract/v5/series"
TEMPLATE_DIRS = (REPO / "contract/v5/custom/templates", REPO / "contract/v5/templates")
OUT_ROOT = pathlib.Path(config.OUTPUTS_DIR)
OUT_3D = OUT_ROOT / "templates_3d"
OUT_2D = OUT_ROOT / "templates_2d"
OUT_SERIES = OUT_ROOT / "series"
FFMPEG = REPO / "tools/node_modules/ffmpeg-static/ffmpeg.exe"
FFPROBE = REPO / "tools/node_modules/ffprobe-static/bin/win32/x64/ffprobe.exe"
FONT = pathlib.Path("C:/Windows/Fonts/msyhbd.ttc")


def _template(tid: str) -> Dict[str, Any]:
    for directory in TEMPLATE_DIRS:
        path = directory / f"{tid}.json"
        if path.is_file():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return {}
    return {}


def _newest(tid: str, name: str) -> pathlib.Path | None:
    """templates_2d 与 templates_3d 可能各有一份成片；取 mtime 最新的那份。"""
    candidates = [root / tid / name for root in (OUT_2D, OUT_3D)
                  if (root / tid / name).is_file()]
    return max(candidates, key=lambda p: p.stat().st_mtime) if candidates else None


def _media(tid: str, name: str) -> str | None:
    path = _newest(tid, name)
    if path is None:
        return None
    return f"/media/{path.parent.parent.name}/{tid}/{name}"


def _member(item: Dict[str, Any]) -> Dict[str, Any]:
    tid = str(item.get("template") or "").strip()
    contract = _template(tid)
    duration = contract.get("target_duration") or contract.get("duration")
    return {
        "template": tid,
        "label": str(item.get("label") or contract.get("label") or tid),
        "render_style": contract.get("render_style"),
        "duration": float(duration) if isinstance(duration, (int, float)) else None,
        "poster_url": _media(tid, "poster.jpg"),
        "final_url": _media(tid, "final.mp4"),
        "ready": bool(_media(tid, "final.mp4")),
    }


def _load(sid: str) -> Dict[str, Any]:
    # P1-1：不再 `SERIES_DIR / f"{sid}.json"`。`\\` 在 Windows 上是合法分隔符，
    # `..%5C..%5C..%5Ccontract%5Cdemo_episode` 曾能读出 series 目录之外的任意 json。
    # 先用 ids.check_id 白名单挡构造，再用 ids.resolve_under 做 containment 兜底。
    ident = ids.check_id(sid, "系列 id")
    path = ids.resolve_under(SERIES_DIR, f"{ident}.json", "系列 id")
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"系列不存在: {ident}")
    data = json.loads(path.read_text(encoding="utf-8"))
    cover = str(data.get("cover") or "")
    members = [_member(item) for item in (data.get("members") or [])]
    total = sum(m["duration"] or 0.0 for m in members) or None
    return {
        "id": data.get("id") or path.stem,
        "label": data.get("label") or path.stem,
        "description": data.get("description") or "",
        "style": data.get("style") or "",
        "cover": cover,
        "cover_url": _media(cover, "poster.jpg"),
        "members": members,
        "ready_count": sum(1 for m in members if m["ready"]),
        "total_duration": total,
    }


def _series_list() -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    if SERIES_DIR.is_dir():
        for path in sorted(SERIES_DIR.glob("*.json")):
            try:
                items.append(_load(path.stem))
            except (OSError, ValueError, HTTPException):
                continue
    return items


@router.get("/series", summary="系列列表（同世界模板串成可连续播放的剧集）")
def list_series() -> Dict[str, Any]:
    items = _series_list()
    return {"ok": True, "count": len(items), "series": items}


@router.get("/series/{sid}", summary="系列详情")
def series_detail(sid: str) -> Dict[str, Any]:
    return {"ok": True, "series": _load(sid)}


def _title_card(series: Dict[str, Any], out: pathlib.Path) -> pathlib.Path:
    """用封面海报压暗后叠系列名/简介，生成 2s 片头卡。"""
    card = out / "title.jpg"
    cover = pathlib.Path(config.OUTPUTS_DIR) / (series["cover_url"] or "").replace("/media/", "", 1)
    try:
        from PIL import Image, ImageDraw, ImageFont
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Pillow 不可用: {exc}") from exc
    if cover.is_file():
        with Image.open(cover) as im:
            base = im.convert("RGB").resize((1280, 720))
    else:
        base = Image.new("RGB", (1280, 720), (14, 16, 22))
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 150))
    base = Image.alpha_composite(base.convert("RGBA"), overlay).convert("RGB")
    draw = ImageDraw.Draw(base)
    try:
        font_big = ImageFont.truetype(str(FONT), 58)
        font_small = ImageFont.truetype(str(FONT), 26)
        font_tag = ImageFont.truetype(str(FONT), 20)
    except OSError:
        font_big = font_small = font_tag = ImageFont.load_default()
    draw.text((72, 250), series["label"], font=font_big, fill=(245, 242, 232))
    draw.text((74, 330), series.get("description") or "", font=font_small, fill=(198, 190, 172))
    tag = f"{series['style']} · {len(series['members'])} 集 · 造片局"
    draw.text((74, 388), tag, font=font_tag, fill=(201, 162, 39))
    base.save(card, quality=92)
    return card


def _sha1(path: pathlib.Path, chunk: int = 1 << 20) -> str:
    digest = hashlib.sha1()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(chunk), b""):
            digest.update(block)
    return digest.hexdigest()


def _ffmpeg_version() -> str:
    try:
        raw = subprocess.run([str(FFMPEG), "-version"], capture_output=True,
                             text=True, timeout=30).stdout
        return (raw.splitlines() or [""])[0][:80]
    except (OSError, subprocess.SubprocessError):
        return ""


def _render_card(image: pathlib.Path | None, out: pathlib.Path, lines: list[tuple]) -> pathlib.Path:
    """1280×720 卡：底图压暗 + 多行文字（字号/颜色由 lines 指定）。"""
    from PIL import Image, ImageDraw, ImageFont

    if image is not None and image.is_file():
        with Image.open(image) as im:
            base = im.convert("RGB").resize((1280, 720))
    else:
        base = Image.new("RGB", (1280, 720), (14, 16, 22))
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 150))
    base = Image.alpha_composite(base.convert("RGBA"), overlay).convert("RGB")
    draw = ImageDraw.Draw(base)
    for text, size, xy, color in lines:
        try:
            font = ImageFont.truetype(str(FONT), size)
        except OSError:
            font = ImageFont.load_default()
        draw.text(xy, str(text), font=font, fill=color)
    base.save(out, quality=92)
    return out


def _episode_card(series: Dict[str, Any], member: Dict[str, Any], order: int,
                  out_dir: pathlib.Path) -> pathlib.Path:
    """分集卡：序号 + 集标题（用该集海报做底，没有则用封面）。"""
    poster = None
    if member.get("poster_url"):
        candidate = pathlib.Path(config.OUTPUTS_DIR) / str(member["poster_url"]).replace("/media/", "", 1)
        poster = candidate if candidate.is_file() else None
    if poster is None and series.get("cover_url"):
        candidate = pathlib.Path(config.OUTPUTS_DIR) / str(series["cover_url"]).replace("/media/", "", 1)
        poster = candidate if candidate.is_file() else None
    return _render_card(poster, out_dir / f"ep_card_{order:02d}.jpg", [
        (f"第 {order} 部", 26, (72, 268), (201, 162, 39)),
        (member.get("label") or member["template"], 52, (72, 306), (245, 242, 232)),
        (f"{series['label']} · 造片局", 20, (74, 386), (198, 190, 172)),
    ])


_EXPORT_LOCK = threading.Lock()


@router.post("/series/{sid}/export", summary="导出系列合集（片头卡 + 分集卡 + 串片 + manifest；支持竖版）")
def export_series(sid: str, force: bool = False, vertical: bool = False,
                  hd: bool = False) -> Dict[str, Any]:
    """同一时间只允许一次导出（固定 concat/manifest 路径，并发会互踩）。"""
    if not _EXPORT_LOCK.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="合集导出进行中，请稍后再试")
    try:
        return _export_series_impl(sid, force=force, vertical=vertical, hd=hd)
    finally:
        _EXPORT_LOCK.release()


def _export_series_impl(sid: str, force: bool = False, vertical: bool = False,
                        hd: bool = False) -> Dict[str, Any]:
    series = _load(sid)
    missing = [m["template"] for m in series["members"] if not m["ready"]]
    if missing:
        # P2-1：detail 统一成字符串（前端按 `err.message` 直接显示；对象会变 [object Object]）。
        # 缺的是哪几集仍然带出来，便于台账核对。
        raise HTTPException(
            status_code=400,
            detail=f"有 {len(missing)} 集还没有成片，无法导出合集: {' / '.join(missing)}",
        )
    out_dir = OUT_SERIES / sid
    out_dir.mkdir(parents=True, exist_ok=True)
    tag = "series_9x16" if vertical else ("series_1080p" if hd else "series")
    final = out_dir / f"{tag}.mp4"
    # 每个变体独立 manifest：横版 manifest.json / 1080p manifest_1080p.json / 竖版 manifest_vertical.json
    manifest_name = ("manifest_vertical.json" if vertical
                     else ("manifest_1080p.json" if hd else "manifest.json"))
    manifest_path = out_dir / manifest_name
    media_url = f"/media/series/{sid}/{tag}.mp4"
    manifest_url = f"/media/series/{sid}/{manifest_path.name}"
    if final.is_file() and manifest_path.is_file() and not force:
        cached = json.loads(manifest_path.read_text(encoding="utf-8"))
        # 缓存必须比对每集成片 sha1：单集重渲（例如 Muse 进片）后合集要自动重建
        cached_eps = {e.get("template"): e for e in cached.get("episodes") or []}
        fresh = True
        for member in series["members"]:
            clip = _newest(member["template"], "final.mp4")
            entry = cached_eps.get(member["template"]) or {}
            if clip is None:
                fresh = False
                break
            stat = clip.stat()
            # 快路径：size+mtime 一致就不用读整文件算 sha1
            if entry.get("bytes") == stat.st_size and entry.get("mtime_ns") == stat.st_mtime_ns:
                continue
            if entry.get("sha1") != _sha1(clip):
                fresh = False
                break
        expected_variant = ("vertical_9x16" if vertical
                            else ("horizontal_1080p" if hd else "horizontal_16x9"))
        if fresh and cached.get("variant") == expected_variant:
            return {"ok": True, "cached": True, "vertical": vertical, "url": media_url,
                    "manifest_url": manifest_url, "duration": cached.get("duration"),
                    "sha1": cached.get("sha1")}
        force = True  # 集数变了 → 视作强制重建
    started = time.time()
    card = _title_card(series, out_dir)
    card_clip = out_dir / "title_card.mp4"
    subprocess.run([str(FFMPEG), "-y", "-hide_banner", "-loglevel", "error",
                    "-loop", "1", "-i", str(card), "-t", "2.2", "-r", "25",
                    "-vf", "scale=1280:720,format=yuv420p",
                    "-c:v", "libx264", "-crf", "20", "-pix_fmt", "yuv420p", str(card_clip)],
                   check=True, timeout=120)
    segments: list[Dict[str, Any]] = [{"kind": "title", "path": card_clip, "duration": 2.2}]
    episodes: list[Dict[str, Any]] = []
    for order, member in enumerate(series["members"], 1):
        clip = _newest(member["template"], "final.mp4")
        if clip is None:
            raise HTTPException(status_code=400, detail=f"缺少成片: {member['template']}")
        ep_card = _episode_card(series, member, order, out_dir)
        ep_card_clip = out_dir / f"ep_card_{order:02d}.mp4"
        subprocess.run([str(FFMPEG), "-y", "-hide_banner", "-loglevel", "error",
                        "-loop", "1", "-i", str(ep_card), "-t", "1.2", "-r", "25",
                        "-vf", "scale=1280:720,format=yuv420p",
                        "-c:v", "libx264", "-crf", "20", "-pix_fmt", "yuv420p",
                        str(ep_card_clip)], check=True, timeout=120)
        segments.append({"kind": "episode_card", "path": ep_card_clip, "duration": 1.2,
                         "order": order, "template": member["template"]})
        segments.append({"kind": "episode", "path": clip, "order": order,
                         "template": member["template"],
                         "duration": float(member["duration"] or 0)})
        stat = clip.stat()
        episodes.append({"index": order, "order": order, "template": member["template"],
                         "label": member["label"], "duration": member["duration"],
                         "sha1": _sha1(clip), "bytes": stat.st_size,
                         "mtime_ns": stat.st_mtime_ns})
    list_file = out_dir / ("concat_9x16.txt" if vertical else "concat.txt")
    lines = [f"file '{seg['path'].as_posix()}'" for seg in segments]
    list_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    cmd = [str(FFMPEG), "-y", "-hide_banner", "-loglevel", "error",
           "-f", "concat", "-safe", "0", "-i", str(list_file)]
    if vertical:
        cmd += ["-vf", "crop=ih*9/16:ih,scale=1080:1920:flags=lanczos",
                "-c:v", "libx264", "-crf", "21", "-preset", "veryfast",
                "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k"]
    elif hd:
        cmd += ["-vf", "scale=1920:1080:flags=lanczos",
                "-c:v", "libx264", "-crf", "19", "-preset", "medium",
                "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k"]
    else:
        cmd += ["-c:v", "libx264", "-crf", "20", "-preset", "veryfast",
                "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k"]
    cmd += ["-movflags", "+faststart", str(final)]
    subprocess.run(cmd, check=True, timeout=900)
    duration = round(sum(float(seg.get("duration") or 0) for seg in segments), 3)
    probe = subprocess.run([str(FFPROBE), "-v", "error", "-select_streams", "v:0",
                            "-show_entries", "stream=width,height",
                            "-show_entries", "format=duration", "-of", "json", str(final)],
                           capture_output=True, text=True, timeout=120).stdout
    try:
        info = json.loads(probe or "{}")
        stream = (info.get("streams") or [{}])[0]
        out_w, out_h = stream.get("width"), stream.get("height")
        out_duration = float((info.get("format") or {}).get("duration") or duration)
    except (ValueError, TypeError):
        out_w = out_h = None
        out_duration = duration
    manifest = {
        "id": sid, "label": series["label"],
        "variant": ("vertical_9x16" if vertical
                    else ("horizontal_1080p" if hd else "horizontal_16x9")),
        "vertical": vertical, "hd": hd,
        "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "duration": round(out_duration, 3), "bytes": final.stat().st_size,
        "sha1": _sha1(final),
        "resolution": [out_w, out_h],
        "build_sec": round(time.time() - started, 1),
        "ffmpeg": _ffmpeg_version(),
        "cover": series.get("cover"),
        "season": series.get("season"),
        "episodes": episodes,
        "segments": [{"kind": seg["kind"], "duration": seg["duration"],
                      **({"order": seg["order"], "template": seg["template"]}
                         if seg.get("order") else {})} for seg in segments],
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    if series.get("cover_url"):
        cover = pathlib.Path(config.OUTPUTS_DIR) / series["cover_url"].replace("/media/", "", 1)
        if cover.is_file():
            shutil.copy2(cover, out_dir / "poster.jpg")
    return {"ok": True, "cached": False, "vertical": vertical, "url": media_url,
            "manifest_url": manifest_url, **manifest}
