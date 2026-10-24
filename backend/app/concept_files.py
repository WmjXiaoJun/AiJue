"""概念图产物扫描：outputs/concepts/{cid}/（imagegen / SDXL 输出）。

产物约定（contract/CONCEPT.md）：
  portrait.png
  three_view/{front,side,three_quarter}.png
  expressions/{neutral,happy,surprised,thinking}.png
  sheet.png
  meta.json

IP-Adapter 一致性产物（contract/CONSISTENCY.md，task-16）：
  consistent/three_view/{front,side,three_quarter}.png
  consistent/expressions/{neutral,happy,surprised,thinking}.png
  consistent/meta.json
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from . import config

THREE_VIEW_NAMES = ("front", "side", "three_quarter")
EXPRESSION_NAMES = ("neutral", "happy", "surprised", "thinking")
FILE_NAMES = {"portrait": "portrait.png", "sheet": "sheet.png"}
FIELD_NAMES = {"portrait": "concept_portrait_url", "sheet": "concept_sheet_url"}


def concept_dir(character_id: str) -> Path:
    return config.OUTPUTS_DIR / "concepts" / character_id


def _read_meta(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _scan_view_set(base: Path) -> Dict[str, Any]:
    """扫描一组一致性产物目录（three_view / expressions / meta）。"""
    data: Dict[str, Any] = {"three_view": {}, "expressions": {}, "meta": {}}
    for name in THREE_VIEW_NAMES:
        path = base / "three_view" / f"{name}.png"
        try:
            if path.is_file() and path.stat().st_size > 0:
                data["three_view"][name] = config.media_url(path)
        except OSError:
            continue
    for name in EXPRESSION_NAMES:
        path = base / "expressions" / f"{name}.png"
        try:
            if path.is_file() and path.stat().st_size > 0:
                data["expressions"][name] = config.media_url(path)
        except OSError:
            continue
    data["meta"] = _read_meta(base / "meta.json")
    return data


def scan(character_id: str) -> Dict[str, Any]:
    """扫描概念图目录，返回 {portrait,sheet,three_view,expressions,consistent,meta}。"""
    base = concept_dir(character_id)
    result: Dict[str, Any] = {
        "portrait": None,
        "sheet": None,
        "three_view": {},
        "expressions": {},
        "consistent": {"three_view": {}, "expressions": {}, "meta": {}},
        "meta": {},
    }
    for key, filename in FILE_NAMES.items():
        path = base / filename
        try:
            if path.is_file() and path.stat().st_size > 0:
                result[key] = config.media_url(path)
        except OSError:
            continue
    regular = _scan_view_set(base)
    result["three_view"] = regular["three_view"]
    result["expressions"] = regular["expressions"]
    result["meta"] = _read_meta(base / "meta.json")
    result["consistent"] = _scan_view_set(base / "consistent")
    return result


def apply(character) -> bool:
    """把磁盘上已有的概念图 URL 回填 Character（概念图不改变角色主 status）。"""
    data = scan(character.id)
    changed = False
    for key, field in FIELD_NAMES.items():
        url = data.get(key)
        if url and getattr(character, field) != url:
            setattr(character, field, url)
            changed = True
    three_view = data.get("three_view") or {}
    if three_view and dict(character.concept_three_view or {}) != three_view:
        character.concept_three_view = dict(three_view)
        changed = True
    expressions = data.get("expressions") or {}
    if expressions and dict(character.concept_expressions or {}) != expressions:
        character.concept_expressions = dict(expressions)
        changed = True
    return changed
