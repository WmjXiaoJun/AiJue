# -*- coding: utf-8 -*-
"""V48.1：系列导出合集——片头卡 + 各集串联成一个 mp4。"""
from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import time
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/v5", tags=["v5-series"])

REPO = pathlib.Path(__file__).resolve().parents[3]
SERIES_DIR = REPO / "contract/v5/series"
TEMPLATE_DIRS = (REPO / "contract/v5/custom/templates", REPO / "contract/v5/templates")
OUT_3D = REPO / "outputs/templates_3d"
OUT_2D = REPO / "outputs/templates_2d"
OUT_SERIES = REPO / "outputs/series"
FFMPEG = REPO / "tools/node_modules/ffmpeg-static/ffmpeg.exe"
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


def _media(tid: str, name: str) -> str | None:
    for root in (OUT_2D, OUT_3D):
        if (root / tid / name).is_file():
            return f"/media/{root.name}/{tid}/{name}"
    return None


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
    path = SERIES_DIR / f"{sid}.json"
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"系列不存在: {sid}")
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
    cover = REPO / "outputs" / (series["cover_url"] or "").replace("/media/", "", 1)
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


@router.post("/series/{sid}/export", summary="导出系列合集（片头卡 + 全部集数串成 mp4）")
def export_series(sid: str, force: bool = False) -> Dict[str, Any]:
    series = _load(sid)
    missing = [m["template"] for m in series["members"] if not m["ready"]]
    if missing:
        raise HTTPException(status_code=400, detail={"message": "有集数还没有成片", "missing": missing})
    out_dir = OUT_SERIES / sid
    out_dir.mkdir(parents=True, exist_ok=True)
    final = out_dir / "series.mp4"
    manifest_path = out_dir / "manifest.json"
    if final.is_file() and manifest_path.is_file() and not force:
        return {"ok": True, "cached": True, "url": f"/media/series/{sid}/series.mp4",
                "manifest_url": f"/media/series/{sid}/manifest.json",
                "duration": json.loads(manifest_path.read_text(encoding="utf-8")).get("duration")}
    card = _title_card(series, out_dir)
    card_clip = out_dir / "title_card.mp4"
    subprocess.run([str(FFMPEG), "-y", "-hide_banner", "-loglevel", "error",
                    "-loop", "1", "-i", str(card), "-t", "2.2", "-r", "25",
                    "-vf", "scale=1280:720,format=yuv420p",
                    "-c:v", "libx264", "-crf", "20", "-pix_fmt", "yuv420p", str(card_clip)],
                   check=True, timeout=120)
    list_file = out_dir / "concat.txt"
    lines = [f"file '{card_clip.as_posix()}'"]
    for member in series["members"]:
        for root in (OUT_2D, OUT_3D):
            clip = root / member["template"] / "final.mp4"
            if clip.is_file():
                lines.append(f"file '{clip.as_posix()}'")
                break
    list_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    started = time.time()
    subprocess.run([str(FFMPEG), "-y", "-hide_banner", "-loglevel", "error",
                    "-f", "concat", "-safe", "0", "-i", str(list_file),
                    "-c:v", "libx264", "-crf", "20", "-preset", "veryfast",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k",
                    "-movflags", "+faststart", str(final)],
                   check=True, timeout=900)
    duration = round(2.2 + sum(m["duration"] or 0.0 for m in series["members"]), 3)
    manifest = {
        "id": sid, "label": series["label"], "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "duration": duration, "bytes": final.stat().st_size,
        "build_sec": round(time.time() - started, 1),
        "episodes": [{"index": i + 1, "template": m["template"], "label": m["label"],
                      "duration": m["duration"]} for i, m in enumerate(series["members"])],
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    if series.get("cover_url"):
        cover = REPO / "outputs" / series["cover_url"].replace("/media/", "", 1)
        if cover.is_file():
            shutil.copy2(cover, out_dir / "poster.jpg")
    return {"ok": True, "cached": False, "url": f"/media/series/{sid}/series.mp4",
            "manifest_url": f"/media/series/{sid}/manifest.json", **manifest}
