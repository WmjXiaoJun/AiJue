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
  created_at: string;
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
