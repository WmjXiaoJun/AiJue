"""角色产物扫描：概念图（SDXL）与 H3 帧回填 Character URL。

角色画面全部由 imagegen（SDXL 概念图）、本地 cutout 资产或 H3 动画生成；后端只负责：
- 把磁盘上已有的概念立绘 / 2D cutout / H3 产物回填到 Character URL/status；
- 提供 /api/characters/{cid}/files 所需的文件清单（/media/... 相对 URL）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from . import concept_files, config


def character_dir(character_id: str) -> Path:
    return config.OUTPUTS_DIR / "characters" / character_id


def expected_portrait_url(character_id: str) -> str:
    """角色立绘的占位 URL（尚未生成时用于 Asset.images 关联）。"""
    return config.media_url(character_dir(character_id) / "portrait.png")


def scan(character_id: str) -> Dict[str, Any]:
    """扫描角色产物目录 + 概念图目录，返回立绘/表情/H3 等 URL（None 表示缺失）。"""
    base = character_dir(character_id)
    concept = concept_files.scan(character_id)
    result: Dict[str, Any] = {
        "portrait": None,
        "portrait_from_concept": None,
        "cutout_2d": None,
        "h3_portrait": None,
        "h3_video": None,
        "h3_metaso_video": None,
        "expressions": {},
        "concept_portrait": concept.get("portrait"),
        "concept_sheet": concept.get("sheet"),
        "concept_expressions": concept.get("expressions") or {},
        "concept_meta": concept.get("meta") or {},
    }

    def _existing(path: Path) -> bool:
        try:
            return path.is_file() and path.stat().st_size > 0
        except OSError:
            return False

    from_concept = base / "portrait_from_concept.png"
    if _existing(from_concept):
        result["portrait_from_concept"] = config.media_url(from_concept)
    cutout_2d = base / "cutout_2d" / "front.png"
    if _existing(cutout_2d):
        result["cutout_2d"] = config.media_url(cutout_2d)

    # 立绘优先：概念图回填的本地立绘 > SDXL 2D cutout > 概念图 portrait
    result["portrait"] = (
        result["portrait_from_concept"]
        or result["cutout_2d"]
        or result["concept_portrait"]
    )

    h3_portrait = base / "h3_portrait.png"
    if _existing(h3_portrait):
        result["h3_portrait"] = config.media_url(h3_portrait)
    else:
        h3_mid = config.OUTPUTS_DIR / "h3" / f"{character_id}_h3_fmid.png"
        if _existing(h3_mid):
            result["h3_portrait"] = config.media_url(h3_mid)
    h3_video = config.OUTPUTS_DIR / "h3" / f"{character_id}_h3.mp4"
    if _existing(h3_video):
        result["h3_video"] = config.media_url(h3_video)
    h3_metaso = config.OUTPUTS_DIR / "h3" / f"{character_id}_h3_metaso.mp4"
    if _existing(h3_metaso):
        result["h3_metaso_video"] = config.media_url(h3_metaso)

    result["expressions"] = dict(result["concept_expressions"])
    return result


def apply(character) -> bool:
    """把概念图 / SDXL cutout / H3 产物回填到 Character，返回是否有变化。

    状态口径：有立绘（概念图回填立绘、SDXL 2D cutout 或概念图 portrait）即 status=ready。
    """
    data = scan(character.id)
    changed = False

    portrait_url = data.get("portrait")
    if portrait_url and character.portrait_url != portrait_url:
        character.portrait_url = portrait_url
        changed = True

    expressions = data.get("concept_expressions") or {}
    if expressions and dict(character.expressions or {}) != expressions:
        character.expressions = dict(expressions)
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

    if portrait_url and character.status in ("pending", "failed"):
        character.status = "ready"
        changed = True
    return changed
