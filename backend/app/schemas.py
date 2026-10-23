"""Pydantic 请求/响应模型（JSON snake_case）。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---------------- Project / Episode ----------------
class ProjectOut(ORMModel):
    id: str
    name: str
    format_id: str
    aspect: str
    status: str
    budget: float
    spent: float
    created_at: datetime
    cover: Optional[str] = None
    episode_count: int = 0


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    format_id: str = "roundtable_5min"
    aspect: str = "16:9"


class EpisodeOut(ORMModel):
    id: str
    project_id: str
    no: int
    title: str
    target_duration: int
    status: str
    progress: float
    shots_total: int
    shots_done: int
    cost: float
    render_url: Optional[str] = None
    created_at: datetime
    aspect: str = "16:9"
    resolution: str = "1280x720"
    fps: int = 25


class EpisodeUpdate(BaseModel):
    title: Optional[str] = None
    target_duration: Optional[int] = Field(default=None, ge=1)


# ---------------- Asset ----------------
class AssetOut(ORMModel):
    id: str
    episode_id: str
    role: str
    type: str
    name: str
    title: str
    persona: str
    voice: str
    accent_color: str
    images: List[str] = Field(default_factory=list)
    locked: bool = False


class AssetCreate(BaseModel):
    id: Optional[str] = None
    role: str = "guest"
    type: str = "character"
    name: str = ""
    title: str = ""
    persona: str = ""
    voice: str = ""
    accent_color: str = "#888888"
    images: List[str] = Field(default_factory=list)
    locked: bool = False


class AssetUpdate(BaseModel):
    locked: Optional[bool] = None
    voice: Optional[str] = None
    persona: Optional[str] = None


# ---------------- Shot / Version ----------------
class ShotOut(ORMModel):
    id: str
    episode_id: str
    segment_id: str
    no: int
    type: str
    duration: float
    speaker: Optional[str] = None
    speaker_name: str
    line: str
    visual: str
    lower_third: Optional[Dict[str, Any]] = None
    punchline: str
    sfx: str
    mood: str
    status: str
    selected_version_id: Optional[str] = None
    title: Optional[str] = None
    subtitle: Optional[str] = None
    video_url: Optional[str] = None


class ShotUpdate(BaseModel):
    line: Optional[str] = None
    duration: Optional[float] = Field(default=None, ge=0.5, le=60)
    status: Optional[str] = None
    visual: Optional[str] = None
    punchline: Optional[str] = None


class ShotGenerateRequest(BaseModel):
    params: Dict[str, Any] = Field(default_factory=dict)


class ShotVersionOut(ORMModel):
    id: str
    shot_id: str
    version: int
    status: str
    video_url: Optional[str] = None
    thumb_url: Optional[str] = None
    cost: float
    params: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


# ---------------- Job ----------------
class JobOut(ORMModel):
    id: str
    type: str
    status: str
    progress: float
    message: str
    episode_id: Optional[str] = None
    shot_id: Optional[str] = None
    character_id: Optional[str] = None
    output_url: Optional[str] = None
    cost: float
    error: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class RenderRequest(BaseModel):
    params: Dict[str, Any] = Field(default_factory=dict)


# ---------------- Settings / misc ----------------
class SettingsOut(ORMModel):
    generation_mode: str
    comfyui_url: str
    workflow_template: str
    resolution: str
    fps: int
    tts_provider: str
    voice_map: Dict[str, str] = Field(default_factory=dict)
    h3_price_per_second: float


class SettingsUpdate(BaseModel):
    generation_mode: Optional[str] = None
    comfyui_url: Optional[str] = None
    workflow_template: Optional[str] = None
    resolution: Optional[str] = None
    fps: Optional[int] = Field(default=None, ge=1, le=120)
    tts_provider: Optional[str] = None
    voice_map: Optional[Dict[str, str]] = None
    h3_price_per_second: Optional[float] = Field(default=None, ge=0)


# ---------------- Character（角色工坊） ----------------
class CharacterOut(ORMModel):
    id: str
    project_id: str
    asset_id: Optional[str] = None
    name: str
    title: str
    persona: str
    voice: str
    accent_color: str
    style: Dict[str, Any] = Field(default_factory=dict)
    spec: Dict[str, Any] = Field(default_factory=dict)
    portrait_url: Optional[str] = None
    sheet_url: Optional[str] = None
    turntable_url: Optional[str] = None
    expressions: Dict[str, str] = Field(default_factory=dict)
    status: str
    created_at: datetime


class CharacterCreate(BaseModel):
    """创建角色：兼容扁平对象与 {spec:{...}} 包裹（id 由后端生成）。"""

    model_config = ConfigDict(extra="allow")

    spec: Optional[Dict[str, Any]] = None
    name: Optional[str] = None
    title: Optional[str] = None
    persona: Optional[str] = None
    voice: Optional[str] = None
    accent_color: Optional[str] = None
    style: Optional[Any] = None
    appearance: Optional[Dict[str, Any]] = None
    role: Optional[str] = None


class CharacterPatch(BaseModel):
    name: Optional[str] = None
    title: Optional[str] = None
    persona: Optional[str] = None
    voice: Optional[str] = None
    accent_color: Optional[str] = None
    spec: Optional[Dict[str, Any]] = None


class CharacterGenerateRequest(BaseModel):
    styles: List[str] = Field(default_factory=list)


class CharacterFilesOut(BaseModel):
    portrait: Optional[str] = None
    sheet: Optional[str] = None
    turntable: Optional[str] = None
    expressions: Dict[str, str] = Field(default_factory=dict)
    meta: Dict[str, Any] = Field(default_factory=dict)


class HealthOut(BaseModel):
    status: str = "ok"
    version: str
    mode: str


class StoryboardResult(BaseModel):
    created: int


class CostItem(BaseModel):
    label: str
    amount: float


class CostsOut(BaseModel):
    currency: str = "CNY"
    total: float
    by_type: Dict[str, float] = Field(default_factory=dict)
    items: List[CostItem] = Field(default_factory=list)
