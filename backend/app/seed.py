"""空库播种：从 contract/demo_episode.json 建 demo 项目/集/资产/镜头。

幂等：库里已有项目时直接跳过；python -m app.seed --force 可强制重建 demo 数据。
"""

from __future__ import annotations

import argparse
import json
import logging
from typing import Any, Dict

from . import character_files, config
from .db import SessionLocal
from .models import (
    Asset,
    Character,
    Episode,
    Job,
    Project,
    Shot,
    ShotVersion,
    Setting,
)

logger = logging.getLogger(__name__)

DEMO_PROJECT_ID = "proj_zaopianju_demo"
DEMO_EPISODE_ID = "ep_001"

DEFAULT_VOICE_MAP = {
    "host": "zh-CN-YunxiNeural",
    "guest_a": "zh-CN-XiaoxiaoNeural",
    "guest_b": "zh-CN-YunjianNeural",
}


def ensure_defaults(db) -> Setting:
    """确保设置行存在并返回。"""
    setting = db.get(Setting, 1)
    if setting is None:
        setting = Setting(
            id=1,
            generation_mode="mock",
            comfyui_url="http://127.0.0.1:8188",
            workflow_template="h3_roundtable.json",
            resolution="1280x720",
            fps=25,
            tts_provider="edge-tts",
            voice_map=dict(DEFAULT_VOICE_MAP),
            h3_price_per_second=0.09,
            h3_provider="local",
            metaso_base_url="https://metaso.cn/api/minimax",
            metaso_resolution="768P",
            metaso_duration=5,
            localize_langs=["en", "es"],
            localize_translator="ollama",
            localize_voices={},
            upload_post_username="",
            upload_post_enabled=False,
            upload_post_platforms=["youtube", "tiktok", "instagram"],
            upload_post_youtube_privacy_status="unlisted",
            upload_post_youtube_made_for_kids=False,
            publish_brand_title="造片局 · AI微综艺",
        )
        db.add(setting)
        db.commit()
        db.refresh(setting)
    else:
        changed = False
        if not setting.voice_map:
            setting.voice_map = dict(DEFAULT_VOICE_MAP)
            changed = True
        defaults = {
            "h3_provider": "local",
            "metaso_base_url": "https://metaso.cn/api/minimax",
            "metaso_resolution": "768P",
            "metaso_duration": 5,
            "localize_langs": ["en", "es"],
            "localize_translator": "ollama",
            "localize_voices": {},
            "upload_post_username": "",
            "upload_post_enabled": False,
            "upload_post_platforms": ["youtube", "tiktok", "instagram"],
            "upload_post_youtube_privacy_status": "unlisted",
            "upload_post_youtube_made_for_kids": False,
            "publish_brand_title": "造片局 · AI微综艺",
        }
        for field, value in defaults.items():
            current = getattr(setting, field)
            if current is None or (isinstance(current, str) and not current.strip()):
                setattr(setting, field, value)
                changed = True
        if changed:
            db.commit()
    return setting


def get_settings(db) -> Setting:
    return ensure_defaults(db)


def load_demo_episode() -> Dict[str, Any]:
    return json.loads(config.DEMO_EPISODE_PATH.read_text(encoding="utf-8"))


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def seed_if_empty(db) -> bool:
    """空库时播种 demo 数据；返回是否执行了播种。"""
    if db.query(Project).count() > 0:
        return False
    return seed_demo(db)


def seed_demo(db) -> bool:
    """写入 demo 项目（若已存在则跳过）。"""
    if db.get(Project, DEMO_PROJECT_ID) is not None:
        return False
    try:
        data = load_demo_episode()
    except FileNotFoundError:
        logger.warning("contract/demo_episode.json 不存在，跳过 demo 播种: %s", config.DEMO_EPISODE_PATH)
        return False
    except json.JSONDecodeError:
        logger.exception("contract/demo_episode.json 解析失败，跳过 demo 播种")
        return False

    proj_data = data.get("project") or {}
    ep_data = data.get("episode") or {}
    project = Project(
        id=proj_data.get("id") or DEMO_PROJECT_ID,
        name=proj_data.get("name") or "造片局 · 样片",
        format_id=proj_data.get("format_id") or "roundtable_5min",
        aspect=proj_data.get("aspect") or "16:9",
        status="active",
        budget=5000.0,
        spent=0.0,
        cover=None,
    )
    db.add(project)
    db.flush()

    episode = Episode(
        id=ep_data.get("id") or DEMO_EPISODE_ID,
        project_id=project.id,
        no=int(ep_data.get("no") or 1),
        title=ep_data.get("title") or project.name,
        target_duration=int(ep_data.get("target_duration") or 296),
        status=ep_data.get("status") or "draft",
        progress=0.0,
        shots_total=0,
        shots_done=0,
        cost=0.0,
        render_url=None,
        aspect=ep_data.get("aspect") or project.aspect,
        resolution=ep_data.get("resolution") or "1280x720",
        fps=int(ep_data.get("fps") or 25),
        packaging=ep_data.get("packaging") or {},
    )
    db.add(episode)
    db.flush()

    for item in data.get("assets") or []:
        db.add(
            Asset(
                id=str(item.get("id")),
                episode_id=episode.id,
                role=item.get("role") or "guest",
                type=item.get("type") or "character",
                name=item.get("name") or "",
                title=item.get("title") or "",
                persona=item.get("persona") or "",
                voice=item.get("voice") or "",
                accent_color=item.get("accent_color") or "#888888",
                images=list(item.get("images") or []),
                locked=bool(item.get("locked", False)),
            )
        )

    shots = data.get("shots") or []
    for index, item in enumerate(shots, start=1):
        db.add(
            Shot(
                id=str(item.get("id") or f"s{index:03d}"),
                episode_id=episode.id,
                segment_id=str(item.get("segment") or ""),
                no=index,
                type=item.get("type") or "host_to_camera",
                duration=_as_float(item.get("duration"), 5.0),
                speaker=item.get("speaker"),
                speaker_name=item.get("speaker_name") or "",
                line=item.get("line") or "",
                visual=item.get("visual") or "",
                lower_third=item.get("lower_third"),
                punchline=item.get("punchline") or "",
                sfx=item.get("sfx") or "",
                mood=item.get("mood") or "",
                status=item.get("status") or "pending",
                selected_version_id=None,
                title=item.get("title"),
                subtitle=item.get("subtitle"),
            )
        )

    episode.shots_total = len(shots)
    episode.shots_done = 0
    episode.progress = 0.0
    project.spent = 0.0
    db.commit()
    logger.info("已播种 demo：1 项目 / 1 集 / %s 资产 / %s 镜头", len(data.get("assets") or []), len(shots))
    return True


CHARACTER_ASSET_IDS = {
    "老周": "host",
    "阿栗": "guest_a",
    "大魏": "guest_b",
}


def seed_characters(db) -> int:
    """为 demo 项目播种 contract/characters/*.json（幂等，关联 host/guest_a/guest_b 资产）。"""
    project = db.get(Project, DEMO_PROJECT_ID)
    folder = config.CONTRACT_DIR / "characters"
    if project is None or not folder.is_dir():
        return 0
    episode = (
        db.query(Episode)
        .filter(Episode.project_id == project.id)
        .order_by(Episode.no.asc())
        .first()
    )
    assets = {}
    if episode is not None:
        for asset in db.query(Asset).filter(Asset.episode_id == episode.id).all():
            assets[asset.id] = asset

    created = 0
    for path in sorted(folder.glob("*.json")):
        try:
            spec = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.exception("角色 spec 读取失败: %s", path)
            continue
        character_id = str(spec.get("id") or path.stem)
        if db.get(Character, character_id) is not None:
            continue
        name = str(spec.get("name") or character_id)
        target_asset_id = CHARACTER_ASSET_IDS.get(name)
        asset = assets.get(target_asset_id) if target_asset_id else None
        if asset is None:
            asset = next(
                (
                    item
                    for item in assets.values()
                    if item.type == "character" and item.name == name
                ),
                None,
            )
        style = spec.get("style")
        if isinstance(style, str):
            style = {"primary": style, "variants": [style]}
        if not isinstance(style, dict) or not style:
            style = {"primary": "3d_toon", "variants": ["3d_toon"]}
        expected_portrait = character_files.expected_portrait_url(character_id)
        if asset is not None:
            asset.type = "character"
            if not asset.images:
                # 契约要求 Asset.images 指向角色 portrait（生成后即真实可用）
                asset.images = [expected_portrait]
        db.add(
            Character(
                id=character_id,
                project_id=project.id,
                asset_id=asset.id if asset is not None else None,
                name=name,
                title=str(spec.get("title") or ""),
                persona=str(spec.get("persona") or ""),
                voice=str(spec.get("voice") or ""),
                accent_color=str(spec.get("accent_color") or "#888888"),
                style=style,
                spec=spec,
                portrait_url=None,
                sheet_url=None,
                expressions={},
                status="pending",
            )
        )
        created += 1
        logger.info("播种角色: %s (%s)", character_id, name)
    if created:
        db.commit()
    # 若磁盘上已有概念图 / SDXL cutout / H3 产物，回填 URL/status
    changed = False
    for character in db.query(Character).filter(Character.project_id == project.id).all():
        changed = character_files.apply(character) or changed
    if changed:
        db.commit()
    return created


def purge_demo(db) -> int:
    """删除 demo 项目及其集/资产/镜头/版本/任务，返回删除的集数。"""
    episode_ids = [
        row.id for row in db.query(Episode).filter(Episode.project_id == DEMO_PROJECT_ID).all()
    ]
    if episode_ids:
        db.query(Job).filter(Job.episode_id.in_(episode_ids)).delete(synchronize_session=False)
        shot_ids = db.query(Shot.id).filter(Shot.episode_id.in_(episode_ids))
        db.query(ShotVersion).filter(ShotVersion.shot_id.in_(shot_ids)).delete(synchronize_session=False)
    project = db.get(Project, DEMO_PROJECT_ID)
    if project is not None:
        db.delete(project)
    db.commit()
    return len(episode_ids)


def main() -> None:
    parser = argparse.ArgumentParser(description="造片局 demo 数据播种")
    parser.add_argument("--force", action="store_true", help="先删除 demo 数据再重新播种")
    args = parser.parse_args()

    from .db import init_db

    init_db()
    with SessionLocal() as db:
        removed = purge_demo(db) if args.force else 0
        seeded = seed_demo(db)
        setting = ensure_defaults(db)
        counts = {
            "projects": db.query(Project).count(),
            "episodes": db.query(Episode).count(),
            "assets": db.query(Asset).count(),
            "shots": db.query(Shot).count(),
            "characters": db.query(Character).count(),
            "mode": setting.generation_mode,
        }
    print(json.dumps({"seeded": seeded, "removed_episodes": removed, **counts}, ensure_ascii=False))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
