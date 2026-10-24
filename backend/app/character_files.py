"""角色产物扫描：outputs/characters/{cid}/ 下的立绘/设定/表情/转面/概念立绘/H3 帧。

角色画面由 studio3d Blender CLI、imagegen 概念图或 H3 动画生成，后端只负责：
- 约定产物路径并扫描回填 Character URL；
- 提供 /api/characters/{cid}/files 所需的文件清单（/media/characters/... 相对 URL）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from . import config

EXPRESSION_NAMES = ("neutral", "happy", "surprised", "thinking")
FILE_NAMES = {
    "portrait": "portrait.png",
    "sheet": "sheet.png",
    "turntable": "turntable.mp4",
}
FIELD_NAMES = {
    "portrait": "portrait_url",
    "sheet": "sheet_url",
    "turntable": "turntable_url",
}


def character_dir(character_id: str) -> Path:
    return config.OUTPUTS_DIR / "characters" / character_id


def expected_portrait_url(character_id: str) -> str:
    """角色的固定半身像 URL（即使尚未生成，也用于 Asset.images 关联）。"""
    return config.media_url(character_dir(character_id) / FILE_NAMES["portrait"])


def scan(character_id: str) -> Dict[str, Any]:
    """扫描角色产物目录，返回所有已知产物 URL（None 表示缺失）。"""
    base = character_dir(character_id)
    result: Dict[str, Any] = {
        "portrait": None,
        "portrait_from_concept": None,
        "sheet": None,
        "turntable": None,
        "h3_portrait": None,
        "h3_video": None,
        "h3_metaso_video": None,
        "expressions": {},
        "meta": {},
    }
    for key, filename in FILE_NAMES.items():
        path = base / filename
        try:
            if path.is_file() and path.stat().st_size > 0:
                result[key] = config.media_url(path)
        except OSError:
            continue
    try:
        from_concept = base / "portrait_from_concept.png"
        if from_concept.is_file() and from_concept.stat().st_size > 0:
            result["portrait_from_concept"] = config.media_url(from_concept)
    except OSError:
        pass
    try:
        h3_portrait = base / "h3_portrait.png"
        if h3_portrait.is_file() and h3_portrait.stat().st_size > 0:
            result["h3_portrait"] = config.media_url(h3_portrait)
        else:
            # run_h3.py 侧车产物：outputs/h3/{cid}_h3_fmid.png（中段帧）
            h3_mid = config.OUTPUTS_DIR / "h3" / f"{character_id}_h3_fmid.png"
            if h3_mid.is_file() and h3_mid.stat().st_size > 0:
                result["h3_portrait"] = config.media_url(h3_mid)
        h3_video = config.OUTPUTS_DIR / "h3" / f"{character_id}_h3.mp4"
        if h3_video.is_file() and h3_video.stat().st_size > 0:
            result["h3_video"] = config.media_url(h3_video)
        h3_metaso = config.OUTPUTS_DIR / "h3" / f"{character_id}_h3_metaso.mp4"
        if h3_metaso.is_file() and h3_metaso.stat().st_size > 0:
            result["h3_metaso_video"] = config.media_url(h3_metaso)
    except OSError:
        pass
    for name in EXPRESSION_NAMES:
        path = base / "expressions" / f"{name}.png"
        try:
            if path.is_file() and path.stat().st_size > 0:
                result["expressions"][name] = config.media_url(path)
        except OSError:
            continue
    meta_path = base / "meta.json"
    if meta_path.is_file():
        try:
            result["meta"] = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            result["meta"] = {}
    return result


def apply(character) -> bool:
    """把磁盘上已存在的产物 URL 回填到 Character，返回是否有变化。

    状态口径：有立绘（Blender portrait 或概念图指定的 portrait_from_concept）即 status=ready；
    sheet/turntable 可缺失（前端显示占位），生成中的角色保持生成状态，由任务结束显式置位。
    """
    data = scan(character.id)
    changed = False

    portrait_url = data.get("portrait_from_concept") or data.get("portrait")
    if portrait_url and character.portrait_url != portrait_url:
        character.portrait_url = portrait_url
        changed = True
    for key, field in (("sheet", "sheet_url"), ("turntable", "turntable_url")):
        url = data.get(key)
        if url and getattr(character, field) != url:
            setattr(character, field, url)
            changed = True
    for key, field in (
        ("h3_portrait", "h3_portrait_url"),
        ("h3_video", "h3_video_url"),
        ("h3_metaso_video", "h3_metaso_url"),
    ):
        url = data.get(key)
        if url and getattr(character, field) != url:
            setattr(character, field, url)
            changed = True

    expressions = data.get("expressions") or {}
    if expressions and dict(character.expressions or {}) != expressions:
        character.expressions = dict(expressions)
        changed = True
    if portrait_url and character.status in ("pending", "failed"):
        character.status = "ready"
        changed = True
    return changed
