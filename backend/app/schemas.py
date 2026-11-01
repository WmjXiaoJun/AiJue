"""Pydantic 请求/响应模型（JSON snake_case）。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


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


class ProjectUpdate(BaseModel):
    """PATCH /projects/{pid}：workspace「3D 模板」页切换节目模板用。"""

    name: Optional[str] = None
    format_id: Optional[str] = None
    aspect: Optional[str] = None


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
    script: str = ""


class EpisodeCreate(BaseModel):
    title: Optional[str] = None
    target_duration: Optional[int] = Field(default=None, ge=1)
    # 资产继承：默认从项目最新一集复制；可指定源集或关闭
    copy_assets_from: Optional[str] = None
    no_copy_assets: bool = False


class EpisodeUpdate(BaseModel):
    title: Optional[str] = None
    target_duration: Optional[int] = Field(default=None, ge=1)
    script: Optional[str] = None


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
    # 全动态分镜（contract/DYNAMIC_SHOTS.md）
    first_frame_url: Optional[str] = None
    last_frame_url: Optional[str] = None
    h3_video_url: Optional[str] = None
    dynamic_mode: Optional[str] = None
    dynamic_status: str = "none"


class ShotUpdate(BaseModel):
    line: Optional[str] = None
    duration: Optional[float] = Field(default=None, ge=0.5, le=60)
    status: Optional[str] = None
    visual: Optional[str] = None
    punchline: Optional[str] = None
    # 首尾帧来源（可选；/media/... URL 或 outputs 下路径）
    first_frame_url: Optional[str] = None
    last_frame_url: Optional[str] = None


class ShotGenerateRequest(BaseModel):
    # mode 缺省=静态 gen-shot；mode="dynamic" 走全动态镜头任务
    mode: Optional[str] = None
    params: Dict[str, Any] = Field(default_factory=dict)


class DynamicGenerateRequest(BaseModel):
    shot_ids: Optional[List[str]] = None
    all: bool = True


class ShotVersionOut(ORMModel):
    id: str
    shot_id: str
    version: int
    status: str
    kind: str = "clip"
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
    # H3 provider（local 默认 / metaso 云端测试）
    h3_provider: str = "local"
    metaso_base_url: str = "https://metaso.cn/api/minimax"
    metaso_resolution: str = "768P"
    metaso_duration: int = 5
    metaso_key_configured: bool = False  # 只读，不回传明文 key
    # 多语种本地化（contract/LOCALIZATION.md）
    localize_langs: List[str] = Field(default_factory=lambda: ["en", "es"])
    localize_translator: str = "ollama"  # ollama 默认；nllb 兜底；mock 测试
    localize_voices: Dict[str, Any] = Field(default_factory=dict)
    # 一键发布 Upload-Post（contract/PUBLISHING.md）
    upload_post_username: str = ""
    upload_post_enabled: bool = False
    upload_post_platforms: List[str] = Field(
        default_factory=lambda: ["youtube", "tiktok", "instagram"]
    )
    upload_post_youtube_privacy_status: str = "unlisted"
    upload_post_youtube_made_for_kids: bool = False
    publish_brand_title: str = "造片局 · AI微综艺"
    upload_post_api_key_configured: bool = False  # 只读，不回传明文 key
    # LLM 内容生成（backend/data/llm.env）
    llm_provider: str = "ollama"
    llm_base_url: str = "http://127.0.0.1:11434"
    llm_model: str = "qwen2.5:7b"
    llm_api_key_configured: bool = False


class SettingsUpdate(BaseModel):
    generation_mode: Optional[str] = None
    comfyui_url: Optional[str] = None
    workflow_template: Optional[str] = None
    resolution: Optional[str] = None
    fps: Optional[int] = Field(default=None, ge=1, le=120)
    tts_provider: Optional[str] = None
    voice_map: Optional[Dict[str, str]] = None
    h3_price_per_second: Optional[float] = Field(default=None, ge=0)
    h3_provider: Optional[str] = None
    metaso_base_url: Optional[str] = None
    metaso_resolution: Optional[str] = None
    metaso_duration: Optional[int] = Field(default=None, ge=4, le=15)
    # 写入 backend/data/metaso.env（不落 DB、不回传）；空字符串表示清除
    metaso_key: Optional[str] = None
    # 多语种本地化
    localize_langs: Optional[List[str]] = None
    localize_translator: Optional[str] = None
    localize_voices: Optional[Dict[str, Any]] = None
    # 一键发布 Upload-Post（api_key 写 backend/data/publish.env，不落 DB/不回传）
    upload_post_api_key: Optional[str] = None
    upload_post_username: Optional[str] = None
    upload_post_enabled: Optional[bool] = None
    upload_post_platforms: Optional[List[str]] = None
    upload_post_youtube_privacy_status: Optional[str] = None
    upload_post_youtube_made_for_kids: Optional[bool] = None
    publish_brand_title: Optional[str] = None
    # LLM 内容生成（写 backend/data/llm.env，api_key 不落 DB/不回传）
    llm_provider: Optional[str] = None
    llm_base_url: Optional[str] = None
    llm_model: Optional[str] = None
    llm_api_key: Optional[str] = None


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
    # AI 概念图 / H3 动画（扩展字段，前端可选用）
    concept_portrait_url: Optional[str] = None
    concept_sheet_url: Optional[str] = None
    concept_three_view: Dict[str, str] = Field(default_factory=dict)
    concept_expressions: Dict[str, str] = Field(default_factory=dict)
    h3_video_url: Optional[str] = None
    h3_portrait_url: Optional[str] = None
    h3_metaso_url: Optional[str] = None

    @field_validator("concept_three_view", "concept_expressions", mode="before")
    @classmethod
    def _none_to_empty_dict(cls, value):
        return value or {}


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


# ---------------- concept / imagegen / h3 / export ----------------
class ConceptGenerateRequest(BaseModel):
    modes: List[str] = Field(default_factory=lambda: ["portrait", "three_view"])
    style: Optional[str] = None
    prompt_extra: Optional[str] = None
    seed: Optional[int] = None


class ConceptFilesOut(BaseModel):
    portrait: Optional[str] = None
    three_view: Dict[str, str] = Field(default_factory=dict)
    expressions: Dict[str, str] = Field(default_factory=dict)
    sheet: Optional[str] = None
    meta: Dict[str, Any] = Field(default_factory=dict)
    # IP-Adapter 一致性产物（contract/CONSISTENCY.md）：{three_view, expressions, meta}
    consistent: Dict[str, Any] = Field(default_factory=dict)


class PortraitFromConceptRequest(BaseModel):
    path: str


class H3AnimateRequest(BaseModel):
    image: Optional[str] = None
    prompt: Optional[str] = None


class EpisodeExportRequest(BaseModel):
    type: str = "capcut"
    # 可选导出名（默认 造片局_EPxxx）；测试/演示可用独立 name 避免覆盖 canonical 草稿
    name: Optional[str] = None


# ---------------- LLM 内容生成 ----------------
class LLMConfigOut(BaseModel):
    provider: str
    base_url: str
    model: str
    has_api_key: bool = False
    api_key_masked: str = ""
    timeout_seconds: float = 120.0
    supported_providers: List[str] = Field(default_factory=list)


class LLMTestRequest(BaseModel):
    provider: Optional[str] = None


class LLMTestResult(BaseModel):
    ok: bool
    provider: str
    model: str
    latency_ms: int = 0
    reply: Optional[str] = None
    error: Optional[str] = None


class ScriptGenerateRequest(BaseModel):
    topic: str = Field(min_length=1, max_length=500)
    template_id: Optional[str] = None
    duration: int = Field(default=60, ge=15, le=600)
    tone: Optional[str] = Field(default="轻松幽默", max_length=100)


class ScriptLineOut(BaseModel):
    speaker: str
    text: str


class ScriptSegmentOut(BaseModel):
    name: str
    lines: List[ScriptLineOut] = Field(default_factory=list)


class ScriptGenerateResult(BaseModel):
    title: str
    script: str
    segments: List[ScriptSegmentOut] = Field(default_factory=list)
    topic: str
    provider: str
    model: str
    duration: int


# ---------------- 一键发布（contract/PUBLISHING.md） ----------------
class PublishRecordOut(ORMModel):
    id: str
    episode_id: str
    platforms: List[str] = Field(default_factory=list)
    lang: str = "zh"
    video_url: Optional[str] = None
    title: str = ""
    status: str
    request_id: Optional[str] = None
    meta: Dict[str, Any] = Field(default_factory=dict)
    error: Optional[str] = None
    created_at: datetime

    @field_validator("meta", mode="before")
    @classmethod
    def _meta_none_to_dict(cls, value):
        return value or {}

    @field_validator("platforms", mode="before")
    @classmethod
    def _platforms_none_to_list(cls, value):
        return value or []


class PublishRequest(BaseModel):
    platforms: Optional[List[str]] = None
    lang: Optional[str] = None  # zh/en/es/ja/ko/pt
    title: Optional[str] = None
    description: Optional[str] = None
    hashtags: Optional[List[str]] = None
    privacy: Optional[str] = None  # public|unlisted|private（YouTube）
    dry_run: bool = False
    version: Optional[str] = None  # final|dynamic


class PublishConfigOut(BaseModel):
    configured: bool = False
    username_masked: str = ""
    enabled: bool = False
    platforms: List[str] = Field(default_factory=list)
    youtube_privacy: str = "unlisted"
    limits: Dict[str, Any] = Field(default_factory=dict)
    brand: Dict[str, Any] = Field(default_factory=dict)


class PublishPreflightCheck(BaseModel):
    id: str
    label: str
    status: str  # pass | warn | fail
    detail: str = ""


class PublishPreflightConfig(BaseModel):
    configured: bool = False
    enabled: bool = False
    platforms: List[str] = Field(default_factory=list)
    username_masked: Optional[str] = None


class PublishPreflightOut(BaseModel):
    """GET /episodes/{eid}/publish/preflight：发布前检查（不真的发 HTTP/不写库）。"""

    episode_id: str
    ok: bool
    publishable: bool
    checks: List[PublishPreflightCheck] = Field(default_factory=list)
    config: PublishPreflightConfig
    warnings: List[str] = Field(default_factory=list)


# ---------------- 多语种本地化（contract/LOCALIZATION.md） ----------------
class LocalizationOut(ORMModel):
    id: str
    episode_id: str
    lang: str
    status: str
    video_url: Optional[str] = None
    srt_url: Optional[str] = None
    meta: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime

    @field_validator("meta", mode="before")
    @classmethod
    def _meta_none_to_dict(cls, value):
        return value or {}


class LocalizeRequest(BaseModel):
    # 不传时使用 settings.localize_langs（默认 ["en","es"]）
    langs: Optional[List[str]] = None


class HealthOut(BaseModel):
    status: str = "ok"
    version: str
    mode: str


class StoryboardResult(BaseModel):
    created: int


class StoryboardGenerateRequest(BaseModel):
    force: bool = False


class ShotCreateRequest(BaseModel):
    type: Optional[str] = None
    line: Optional[str] = None
    duration: Optional[float] = Field(default=None, ge=0.5, le=60)
    speaker_name: Optional[str] = None
    visual: Optional[str] = None


class ShotDeleteResult(BaseModel):
    ok: bool = True
    deleted: str
    shots_total: int


class CostItem(BaseModel):
    label: str
    amount: float


class CostsOut(BaseModel):
    currency: str = "CNY"
    total: float
    by_type: Dict[str, float] = Field(default_factory=dict)
    items: List[CostItem] = Field(default_factory=list)
