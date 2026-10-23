"""角色产物扫描：outputs/characters/{cid}/ 下的 portrait/sheet/turntable/expressions/meta。

角色画面由 studio3d Blender CLI 生成，后端只负责：
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
    """扫描产物目录，返回 {portrait,sheet,turntable,expressions,meta}（None 表示缺失）。"""
    base = character_dir(character_id)
    result: Dict[str, Any] = {
        "portrait": None,
        "sheet": None,
        "turntable": None,
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

    portrait/sheet/turntable 齐全时把 status 置为 ready。
    """
    data = scan(character.id)
    changed = False
    for key, field in FIELD_NAMES.items():
        url = data.get(key)
        if url and getattr(character, field) != url:
            setattr(character, field, url)
            changed = True
    expressions = data.get("expressions") or {}
    if expressions and dict(character.expressions or {}) != expressions:
        character.expressions = dict(expressions)
        changed = True
    # 状态口径：有 portrait 即视为「立绘已就绪」；sheet/turntable 可缺失（前端显示占位）。
    # 生成中的角色保持 generating，等任务结束后由 run_character_job 显式置 ready。
    if data.get("portrait") and character.status in ("pending", "failed"):
        character.status = "ready"
        changed = True
    return changed
