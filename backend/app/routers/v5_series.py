# -*- coding: utf-8 -*-
"""V48：系列（series）只读接口——把同世界的多个模板串成可连续播放的一条线。"""
from __future__ import annotations

import json
import pathlib
from typing import Any, Dict, List

from fastapi import APIRouter

router = APIRouter(prefix="/api/v5", tags=["v5-series"])

REPO = pathlib.Path(__file__).resolve().parents[3]
SERIES_DIR = REPO / "contract/v5/series"
TEMPLATE_DIRS = (REPO / "contract/v5/custom/templates", REPO / "contract/v5/templates")
OUT_3D = REPO / "outputs/templates_3d"
OUT_2D = REPO / "outputs/templates_2d"


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
    poster = _media(tid, "poster.jpg")
    final = _media(tid, "final.mp4")
    return {
        "template": tid,
        "label": str(item.get("label") or contract.get("label") or tid),
        "render_style": contract.get("render_style"),
        "duration": float(duration) if isinstance(duration, (int, float)) else None,
        "poster_url": poster,
        "final_url": final,
        "ready": bool(final),
    }


def _series(path: pathlib.Path) -> Dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    members = [_member(item) for item in (data.get("members") or [])]
    cover = str(data.get("cover") or (members[0]["template"] if members else ""))
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


@router.get("/series", summary="系列列表（同世界模板串成可连续播放的剧集）")
def list_series() -> Dict[str, Any]:
    items: List[Dict[str, Any]] = []
    if SERIES_DIR.is_dir():
        for path in sorted(SERIES_DIR.glob("*.json")):
            try:
                items.append(_series(path))
            except (OSError, ValueError):
                continue
    return {"ok": True, "count": len(items), "series": items}
