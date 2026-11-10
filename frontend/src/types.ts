// 造片局 · 数据模型（与 contract/API.md 对齐；所有字段按“可能缺省”处理，后端未就绪时也不崩）

export interface Project {
  id: string;
  name: string;
  format_id: string;
  aspect: string;
  status: string;
  budget: number;
  spent: number;
  created_at: string;
  cover?: string | null;
  episode_count: number;
}

export interface Episode {
  id: string;
  project_id: string;
  no: number;
  title: string;
  target_duration: number;
  status: string;
  progress: number;
  shots_total: number;
  shots_done: number;
  cost: number;
  render_url?: string | null;
  /** 台本（contract/SCRIPT_FORMAT.md），空字符串表示未填 */
  script?: string | null;
  aspect?: string | null;
  resolution?: string | null;
  fps?: number | null;
  created_at: string;
}

/** 台本 → 分镜 生成结果（后端可选返回 segments 摘要） */
export interface StoryboardSegmentSummary {
  segment_id?: string;
  name?: string;
  count?: number;
  duration?: number;
}

export interface StoryboardResult {
  created: number;
  segments?: StoryboardSegmentSummary[] | Record<string, number>;
  total_duration?: number;
}

/** 多语种出海版本（contract/LOCALIZATION.md） */
export interface LocalizationMeta {
  title?: string;
  description?: string;
  hashtags?: string[] | string;
  duration_s?: number;
  /** pipeline 产出的 meta.json 字段 */
  duration?: number;
  lang?: string;
  translator?: string | { provider?: string; model?: string } | null;
  publish?: { title?: string; description?: string; hashtags?: string[] | string } | Record<string, unknown> | null;
  [key: string]: unknown;
}

export interface Localization {
  id: string;
  episode_id: string;
  lang: string;
  /** draft | queued | rendering | ready | failed */
  status: string;
  video_url?: string | null;
  srt_url?: string | null;
  meta?: LocalizationMeta | null;
  created_at: string;
  updated_at?: string | null;
}

export interface NewShot {
  type?: string;
  line?: string;
  duration?: number;
  speaker?: string;
  speaker_name?: string;
  visual?: string;
  punchline?: string;
}

export interface Asset {
  id: string;
  episode_id: string;
  role: string; // host | guest
  type: string; // character | scene | prop | style
  name: string;
  title: string;
  persona: string;
  voice: string;
  accent_color: string;
  images?: string[] | null;
  locked: boolean;
}

export interface LowerThird {
  name?: string;
  title?: string;
}

export interface Shot {
  id: string;
  episode_id: string;
  segment_id: string;
  /** demo 种子里的别名字段（兼容后端可能透传） */
  segment?: string;
  no: number;
  type: string;
  duration: number;
  speaker?: string | null;
  speaker_name?: string;
  line?: string;
  visual?: string;
  lower_third?: LowerThird | null;
  punchline?: string;
  sfx?: string;
  mood?: string;
  status: string;
  selected_version_id?: string | null;
  /** 后端在镜头就绪后回填当前选用版本的视频地址 */
  video_url?: string | null;
  /** 动态镜头（contract/DYNAMIC_SHOTS.md）：H3 动态版本 / 首尾帧 / 生成模式 */
  h3_video_url?: string | null;
  first_frame_url?: string | null;
  last_frame_url?: string | null;
  first_frame?: string | null;
  last_frame?: string | null;
  generate_mode?: string | null;
  dynamic_ready?: boolean | null;
  title?: string;
  subtitle?: string;
}

export interface ShotVersion {
  id: string;
  shot_id: string;
  version: number;
  status: string;
  video_url?: string | null;
  thumb_url?: string | null;
  cost?: number;
  params?: Record<string, unknown> | null;
  /** 版本类型：audio_ref（口型动态）/ fl2v / i2v / dynamic / static */
  kind?: string | null;
  created_at: string;
}

export interface Job {
  id: string;
  type: string; // shot | render
  status: string; // queued | running | done | failed | canceled
  progress: number;
  message?: string | null;
  episode_id?: string | null;
  shot_id?: string | null;
  character_id?: string | null;
  output_url?: string | null;
  cost?: number;
  error?: string | null;
  params?: Record<string, unknown> | null;
  created_at: string;
  updated_at?: string;
}

export interface CostItem {
  label: string;
  amount: number;
}

export interface CostReport {
  currency: string;
  total: number;
  by_type: Record<string, number>;
  items: CostItem[];
}

/** 角色工坊（contract/CHARACTER.md） */
export type CharacterStyleKey = string;

export interface CharacterStyle {
  primary: CharacterStyleKey;
  variants?: CharacterStyleKey[];
}

export interface CharacterOutfit {
  type?: string;
  main?: string;
  accent?: string;
  shirt?: string;
}

export interface CharacterAppearance {
  gender?: string;
  age?: number;
  height_ratio?: number;
  head_scale?: number;
  chibi?: number;
  skin?: string;
  hair_color?: string;
  hair_style?: string;
  eye_color?: string;
  eye_size?: number;
  brow?: string;
  facial_hair?: string;
  outfit?: CharacterOutfit;
  accessories?: string[];
  marks?: string[];
}

export interface CharacterSpec {
  id?: string;
  name: string;
  title: string;
  persona: string;
  voice: string;
  accent_color: string;
  style: CharacterStyle;
  appearance: CharacterAppearance;
}

export interface Character {
  id: string;
  project_id: string;
  asset_id?: string | null;
  name: string;
  title: string;
  persona: string;
  voice: string;
  accent_color: string;
  style?: CharacterStyle | string | null;
  spec?: CharacterSpec | null;
  portrait_url?: string | null;
  sheet_url?: string | null;
  turntable_url?: string | null;
  expressions?: Record<string, string> | null;
  /** 后端 CharacterOut 回填：概念图与 H3 产物（可选） */
  concept_portrait_url?: string | null;
  concept_sheet_url?: string | null;
  concept_three_view?: Record<string, string> | null;
  concept_expressions?: Record<string, string> | null;
  h3_video_url?: string | null;
  h3_portrait_url?: string | null;
  /** MetaSo 云端测试产物（测试用，不替代本地生产视频） */
  h3_metaso_url?: string | null;
  status: string;
  created_at: string;
}


/** 概念图（contract/CONCEPT.md） */
export interface ConceptConsistent {
  three_view?: Record<string, string> | string[] | null;
  expressions?: Record<string, string> | null;
  meta?: Record<string, unknown> | null;
}

export interface ConceptFiles {
  portrait?: string | null;
  three_view?: Record<string, string> | string[] | null;
  expressions?: Record<string, string> | null;
  sheet?: string | null;
  meta?: Record<string, unknown> | null;
  /** IP-Adapter 一致性产物（contract/CONSISTENCY.md）；后端就绪后返回 */
  consistent?: ConceptConsistent | null;
}

export interface CharacterFiles {
  portrait?: string | null;
  sheet?: string | null;
  turntable?: string | null;
  expressions?: Record<string, string> | null;
  meta?: Record<string, unknown> | null;
}

export interface Health {
  status: string;
  version?: string;
  mode?: string;
}

export interface Settings {
  generation_mode: string;
  comfyui_url?: string;
  workflow_template?: string;
  resolution: string;
  fps: number;
  tts_provider: string;
  voice_map?: Record<string, string> | null;
  h3_price_per_second: number;
  /** H3 生成方式：local（本地 ComfyUI，生产）/ metaso（MetaSo 云端，测试） */
  h3_provider?: string;
  /** MetaSo 云端 API Key 是否已配置（只读） */
  metaso_key_configured?: boolean;
  /** 只写：PATCH { metaso_key } 保存新 Key，空串表示清除（GET 永不回传明文） */
  metaso_key?: string;
  /** MetaSo 视频接口基址（只读） */
  metaso_base_url?: string;
  /** MetaSo 分辨率（只读，如 768P） */
  metaso_resolution?: string;
  /** MetaSo 单段时长（秒，只读） */
  metaso_duration?: number;
  /** 海外发布（Upload-Post） */
  upload_post_username?: string;
  upload_post_enabled?: boolean;
  upload_post_platforms?: string[];
  upload_post_youtube_privacy_status?: string;
  upload_post_youtube_made_for_kids?: boolean;
  /** Upload-Post API Key 是否已配置（只读，GET 不回传明文） */
  upload_post_api_key_configured?: boolean;
  /** 只写：PATCH 时传空串表示清除，GET 永不返回 */
  upload_post_api_key?: string;
  /** AI 模型（LLM）contract/docs/PLAN_V2_STYLE_LLM.md */
  llm_provider?: string;
  llm_base_url?: string;
  llm_model?: string;
  /** 只读：LLM API Key 是否已配置（不回传明文） */
  llm_api_key_configured?: boolean;
  /** 只写：PATCH 时传空串表示清除 */
  llm_api_key?: string;
  /** 多语种本地化（后端 schemas.SettingsOut.localize_*；当前 UI 未提供编辑入口，仅在读到时透传） */
  localize_langs?: string[];
  localize_translator?: string;
  localize_voices?: Record<string, unknown>;
  /** 发布品牌标题（后端 schemas.SettingsOut.publish_brand_title；当前 UI 未提供编辑入口） */
  publish_brand_title?: string;
}

/** LLM 配置（GET /api/llm/config） */
export interface LLMConfig {
  provider?: string;
  llm_provider?: string;
  base_url?: string;
  llm_base_url?: string;
  model?: string;
  llm_model?: string;
  /** 后端实际字段：是否已配置 API Key（不回传明文） */
  has_api_key?: boolean;
  api_key_configured?: boolean;
  api_key_masked?: string | null;
  timeout_seconds?: number;
  supported_providers?: string[];
  providers?: LLMProviderInfo[];
  [key: string]: unknown;
}

export interface LLMProviderInfo {
  id: string;
  label?: string;
  name?: string;
  requires_key?: boolean;
  default_base_url?: string;
  default_model?: string;
  hint?: string;
  [key: string]: unknown;
}

export interface LLMTestResult {
  ok: boolean;
  provider?: string;
  model?: string;
  latency_ms?: number;
  elapsed_sec?: number;
  sample?: string;
  reply?: string;
  error?: string | null;
  [key: string]: unknown;
}

export interface GenerateScriptRequest {
  topic: string;
  template_id?: string;
  duration?: number;
  tone?: string;
}

export interface GenerateScriptResult {
  script: string;
  title?: string;
  segments?: unknown;
  provider?: string;
  model?: string;
  elapsed_sec?: number;
  warning?: string | null;
  [key: string]: unknown;
}

/** 一键发布（海外）contract/PUBLISHING.md */
export interface PublishConfig {
  configured: boolean;
  username_masked?: string | null;
  enabled: boolean;
  platforms: string[];
  youtube_privacy?: string;
  limits?: Record<string, unknown>;
}

export interface PublishRecord {
  id: string;
  episode_id: string;
  platforms: string[];
  lang?: string | null;
  video_url?: string | null;
  title?: string;
  status: string;
  request_id?: string | null;
  meta?: Record<string, unknown> | null;
  error?: string | null;
  created_at: string;
  updated_at?: string | null;
}

export interface PublishRequest {
  platforms?: string[];
  lang?: string;
  title?: string;
  description?: string;
  hashtags?: string[];
  privacy?: string;
  dry_run?: boolean;
  version?: "final" | "dynamic";
}

export interface NewAsset {
  role: string;
  type: string;
  name: string;
  title?: string;
  persona?: string;
  voice?: string;
  accent_color?: string;
}
