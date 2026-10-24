"""SQLAlchemy ORM 模型（与 contract/API.md 通用对象一致）。"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from .db import Base


def utcnow() -> datetime:
    """naive UTC 时间（SQLite 存储简单、输出 ISO8601）。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


class Project(Base):
    __tablename__ = "projects"

    id = Column(String(64), primary_key=True)
    name = Column(String(200), nullable=False)
    format_id = Column(String(64), nullable=False, default="roundtable_5min")
    aspect = Column(String(16), nullable=False, default="16:9")
    status = Column(String(32), nullable=False, default="draft")
    budget = Column(Float, nullable=False, default=0.0)
    spent = Column(Float, nullable=False, default=0.0)
    cover = Column(String(300), nullable=True)
    created_at = Column(DateTime, nullable=False, default=utcnow)

    episodes = relationship(
        "Episode",
        back_populates="project",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="Episode.no",
    )
    characters = relationship(
        "Character",
        back_populates="project",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="Character.created_at",
    )

    @property
    def episode_count(self) -> int:
        return len(self.episodes)


class Episode(Base):
    __tablename__ = "episodes"

    id = Column(String(64), primary_key=True)
    project_id = Column(String(64), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    no = Column(Integer, nullable=False, default=1)
    title = Column(String(300), nullable=False, default="")
    target_duration = Column(Integer, nullable=False, default=296)
    status = Column(String(32), nullable=False, default="draft")
    progress = Column(Float, nullable=False, default=0.0)
    shots_total = Column(Integer, nullable=False, default=0)
    shots_done = Column(Integer, nullable=False, default=0)
    cost = Column(Float, nullable=False, default=0.0)
    render_url = Column(String(400), nullable=True)
    created_at = Column(DateTime, nullable=False, default=utcnow)
    aspect = Column(String(16), nullable=False, default="16:9")
    resolution = Column(String(32), nullable=False, default="1280x720")
    fps = Column(Integer, nullable=False, default=25)
    packaging = Column(JSON, nullable=True)
    # 用户台本（contract/SCRIPT_FORMAT.md，台本驱动分镜）
    script = Column(Text, nullable=False, default="")

    project = relationship("Project", back_populates="episodes")
    assets = relationship(
        "Asset",
        back_populates="episode",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    shots = relationship(
        "Shot",
        back_populates="episode",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="Shot.no",
    )
    localizations = relationship(
        "Localization",
        back_populates="episode",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="Localization.created_at",
    )

    @property
    def shots_url(self) -> str:
        return f"/api/episodes/{self.id}/shots"


class Asset(Base):
    __tablename__ = "assets"

    id = Column(String(64), primary_key=True)
    episode_id = Column(String(64), ForeignKey("episodes.id", ondelete="CASCADE"), nullable=False)
    role = Column(String(16), nullable=False, default="guest")  # host|guest
    type = Column(String(16), nullable=False, default="character")  # character|scene|prop|style
    name = Column(String(120), nullable=False, default="")
    title = Column(String(120), nullable=False, default="")
    persona = Column(Text, nullable=False, default="")
    voice = Column(String(64), nullable=False, default="")
    accent_color = Column(String(16), nullable=False, default="#888888")
    images = Column(JSON, nullable=False, default=list)
    locked = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, nullable=False, default=utcnow)

    episode = relationship("Episode", back_populates="assets")


class Shot(Base):
    __tablename__ = "shots"

    id = Column(String(64), primary_key=True)
    episode_id = Column(String(64), ForeignKey("episodes.id", ondelete="CASCADE"), nullable=False)
    segment_id = Column(String(64), nullable=False, default="")
    no = Column(Integer, nullable=False, default=1)
    type = Column(String(32), nullable=False, default="host_to_camera")
    duration = Column(Float, nullable=False, default=5.0)
    speaker = Column(String(64), nullable=True)
    speaker_name = Column(String(120), nullable=False, default="")
    line = Column(Text, nullable=False, default="")
    visual = Column(Text, nullable=False, default="")
    lower_third = Column(JSON, nullable=True)
    punchline = Column(Text, nullable=False, default="")
    sfx = Column(String(64), nullable=False, default="")
    mood = Column(String(64), nullable=False, default="")
    status = Column(String(32), nullable=False, default="pending")  # pending|queued|generating|ready|failed
    selected_version_id = Column(String(64), nullable=True)
    title = Column(String(300), nullable=True)
    subtitle = Column(String(400), nullable=True)

    episode = relationship("Episode", back_populates="shots")
    versions = relationship(
        "ShotVersion",
        back_populates="shot",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="ShotVersion.version",
    )

    @property
    def video_url(self) -> str | None:
        for version in self.versions:
            if version.id == self.selected_version_id:
                return version.video_url
        return None


class ShotVersion(Base):
    __tablename__ = "shot_versions"

    id = Column(String(64), primary_key=True)
    shot_id = Column(String(64), ForeignKey("shots.id", ondelete="CASCADE"), nullable=False)
    version = Column(Integer, nullable=False, default=1)
    status = Column(String(32), nullable=False, default="ready")
    video_url = Column(String(400), nullable=True)
    thumb_url = Column(String(400), nullable=True)
    cost = Column(Float, nullable=False, default=0.0)
    params = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, nullable=False, default=utcnow)

    shot = relationship("Shot", back_populates="versions")


class Job(Base):
    __tablename__ = "jobs"

    id = Column(String(64), primary_key=True)
    type = Column(String(16), nullable=False)  # shot|render
    status = Column(String(16), nullable=False, default="queued")  # queued|running|done|failed|canceled
    progress = Column(Float, nullable=False, default=0.0)
    message = Column(String(500), nullable=False, default="")
    episode_id = Column(String(64), nullable=True)
    shot_id = Column(String(64), nullable=True)
    character_id = Column(String(64), nullable=True)
    output_url = Column(String(400), nullable=True)
    cost = Column(Float, nullable=False, default=0.0)
    error = Column(Text, nullable=True)
    params = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, nullable=False, default=utcnow)
    updated_at = Column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)


class Localization(Base):
    """多语种本地化版本（contract/LOCALIZATION.md）。"""

    __tablename__ = "localizations"
    __table_args__ = (
        UniqueConstraint("episode_id", "lang", name="uq_localization_episode_lang"),
    )

    id = Column(String(64), primary_key=True)
    episode_id = Column(
        String(64), ForeignKey("episodes.id", ondelete="CASCADE"), nullable=False
    )
    lang = Column(String(8), nullable=False)
    status = Column(String(16), nullable=False, default="draft")  # draft/queued/rendering/ready/failed
    video_url = Column(String(400), nullable=True)
    srt_url = Column(String(400), nullable=True)
    # 契约字段名 meta（DB 列名 meta_json）
    meta = Column("meta_json", JSON, nullable=False, default=dict)
    created_at = Column(DateTime, nullable=False, default=utcnow)

    episode = relationship("Episode", back_populates="localizations")


class Character(Base):
    """原创动漫角色（角色工坊产物，字段与 contract/CHARACTER.md 对齐）。"""

    __tablename__ = "characters"

    id = Column(String(64), primary_key=True)
    project_id = Column(
        String(64), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    asset_id = Column(
        String(64), ForeignKey("assets.id", ondelete="SET NULL"), nullable=True
    )
    name = Column(String(120), nullable=False, default="")
    title = Column(String(120), nullable=False, default="")
    persona = Column(Text, nullable=False, default="")
    voice = Column(String(64), nullable=False, default="")
    accent_color = Column(String(16), nullable=False, default="#888888")
    style = Column(JSON, nullable=False, default=dict)
    # 契约字段名 spec（DB 列名为 spec_json）
    spec = Column("spec_json", JSON, nullable=False, default=dict)
    portrait_url = Column(String(400), nullable=True)
    sheet_url = Column(String(400), nullable=True)
    turntable_url = Column(String(400), nullable=True)
    # 契约字段名 expressions（DB 列名为 expressions_json）
    expressions = Column("expressions_json", JSON, nullable=False, default=dict)
    status = Column(String(16), nullable=False, default="pending")
    created_at = Column(DateTime, nullable=False, default=utcnow)

    # AI 概念图（imagegen/SDXL，outputs/concepts/{cid}/）
    concept_portrait_url = Column(String(400), nullable=True)
    concept_sheet_url = Column(String(400), nullable=True)
    concept_three_view = Column("concept_three_view", JSON, nullable=False, default=dict)
    concept_expressions = Column("concept_expressions", JSON, nullable=False, default=dict)
    # H3 动画（integrations/comfyui/run_h3.py，outputs/h3/{cid}_h3.mp4）
    h3_video_url = Column(String(400), nullable=True)
    h3_portrait_url = Column(String(400), nullable=True)
    # MetaSo 云端 H3（测试 provider，outputs/h3/{cid}_h3_metaso.mp4）
    h3_metaso_url = Column(String(400), nullable=True)

    project = relationship("Project", back_populates="characters")


class Setting(Base):
    __tablename__ = "settings"

    id = Column(Integer, primary_key=True, default=1)
    generation_mode = Column(String(16), nullable=False, default="mock")
    comfyui_url = Column(String(300), nullable=False, default="http://127.0.0.1:8188")
    workflow_template = Column(Text, nullable=False, default="h3_roundtable.json")
    resolution = Column(String(32), nullable=False, default="1280x720")
    fps = Column(Integer, nullable=False, default=25)
    tts_provider = Column(String(32), nullable=False, default="edge-tts")
    voice_map = Column(JSON, nullable=False, default=dict)
    h3_price_per_second = Column(Float, nullable=False, default=0.09)
    # H3 云端 provider（测试用 MetaSo；默认 local 走本机 ComfyUI）
    h3_provider = Column(String(16), nullable=False, default="local")
    metaso_base_url = Column(String(300), nullable=False, default="https://metaso.cn/api/minimax")
    metaso_resolution = Column(String(16), nullable=False, default="768P")
    metaso_duration = Column(Integer, nullable=False, default=5)
    # 多语种本地化（contract/LOCALIZATION.md）
    localize_langs = Column(JSON, nullable=False, default=lambda: ["en", "es"])
    localize_translator = Column(String(16), nullable=False, default="ollama")  # ollama|nllb|mock
    localize_voices = Column(JSON, nullable=False, default=dict)
    updated_at = Column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)

    @property
    def metaso_key_configured(self) -> bool:
        """只读：是否已配置 MetaSo key（不回传明文）。"""
        from . import config

        return config.metaso_key_configured()
