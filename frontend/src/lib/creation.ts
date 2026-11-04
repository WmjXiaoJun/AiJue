// V21 · 可视化创建（新建角色 / 新建模板）API
// 契约见 docs/V21_CREATION.md §1：
//   POST /api/v5/characters {id?, label, era, persona?, voice_id?, appearance{...}, role_pool?, tags?}
//   POST /api/v5/templates  {id?, label, style, aspect?, description?, cast[], acts[], auto_brief?}
// 后端未就绪时返回 404/405；400 校验错误（detail 字符串或数组）逐条展示。
import { API_BASE, ApiError } from "./api";

export interface CharacterCreateBody {
  id?: string;
  label: string;
  era: string;
  persona?: string;
  voice_id?: string;
  role_pool?: string[];
  tags?: string[];
  /** V24：创建后同步生成 SDXL 概念图（20–60s，失败不阻塞创建） */
  auto_art?: boolean;
  /** V28：auto_art 同时生成的素材模式（["all"] = 三视图+表情+设定图） */
  auto_art_modes?: string[];
  appearance: {
    gender?: string;
    age?: number;
    hair?: string;
    hair_style?: string;
    outfit?: { type?: string; main?: string; accent?: string };
    accessories?: string[];
  };
}

/** V24：概念图生成结果 */
export interface CharacterArtResult {
  generated: boolean;
  portrait_url?: string;
  elapsed_sec?: number;
  cached?: boolean;
  error?: string;
}

export interface CharacterAssetGroup {
  front?: string;
  side?: string;
  three_quarter?: string;
  neutral?: string;
  happy?: string;
  surprised?: string;
  thinking?: string;
  [key: string]: string | undefined;
}

export interface CharacterAssets {
  portrait?: string;
  /** V30：三种立绘版本（存在才带 URL） */
  portraits?: { anime?: string | null; stylized_25d?: string | null; realistic?: string | null };
  three_view?: CharacterAssetGroup;
  expressions?: CharacterAssetGroup;
  sheet?: string;
  exists?: Record<string, boolean>;
}

export interface CharacterArtResponse {
  ok: boolean;
  character: string;
  portrait_url?: string;
  elapsed_sec?: number;
  style?: string;
  seed?: number;
  cached?: boolean;
  mode?: string;
  /** V28：本次生成的 mode 列表 */
  generated?: string[];
  assets?: CharacterAssets;
  warnings?: string[];
}

export interface CharacterCreateResponse {
  ok: boolean;
  id: string;
  item?: Record<string, unknown>;
  preview_url?: string;
  /** V24：auto_art=true 时的概念图结果 */
  art?: CharacterArtResult | null;
}

export interface TemplateCastInput {
  role: string;
  character: string;
  name?: string;
}

export interface TemplateActInput {
  scene: string;
  camera?: string;
  speaker?: string;
  text?: string;
  duration?: number;
}

export interface TemplateCreateBody {
  id?: string;
  label: string;
  style: string;
  /** V41：节目类型（2d | 3d | realistic） */
  render_type?: "2d" | "3d" | "realistic";
  /** V44：2D 风格子类型（仅 render_type=2d 有意义） */
  render_style?: "jp_anime" | "guoman" | "retro_cel" | "soft_watercolor" | "arcane_cinematic";
  aspect?: string;
  description?: string;
  cast: TemplateCastInput[];
  acts: TemplateActInput[];
  auto_brief?: boolean;
  /** V23：创建成功后立即提交 3D pipeline job */
  auto_generate?: boolean;
}

/** V23：POST /api/v5/templates 的 pipeline 字段（auto_generate=true 时返回） */
export interface TemplatePipelineResult {
  started: boolean;
  job_id?: string;
  status?: string;
  reason?: string;
  running_job_id?: string;
  error?: string;
}

export interface TemplateCreateResponse {
  ok: boolean;
  id: string;
  label?: string;
  shots?: number;
  total_duration?: number;
  audio?: number;
  brief?: boolean;
  warnings?: string[];
  pipeline?: TemplatePipelineResult | null;
}

/** 创建失败：携带可逐条展示的校验错误 */
export class CreationError extends Error {
  status: number;
  items: string[];

  constructor(status: number, items: string[], raw?: string) {
    super(items.length ? items.join("；") : raw || "创建失败");
    this.name = "CreationError";
    this.status = status;
    this.items = items;
  }
}

async function get<T>(path: string, timeoutMs = 30000): Promise<T> {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), timeoutMs);
  let res: Response;
  try {
    res = await fetch(API_BASE + path, { signal: controller.signal });
  } catch (err) {
    window.clearTimeout(timer);
    const message = err instanceof DOMException && err.name === "AbortError" ? "请求超时" : "无法连接后端";
    throw new CreationError(0, [message]);
  }
  window.clearTimeout(timer);
  if (!res.ok) {
    let detail: unknown = null;
    try {
      const data = (await res.json()) as { detail?: unknown };
      detail = data?.detail;
    } catch {
      /* 非 JSON */
    }
    throw new CreationError(res.status, errorItems(res.status, detail, res.statusText || "请求失败"));
  }
  return (await res.json()) as T;
}

function normalizeAssets(raw: Record<string, unknown>): CharacterAssets {
  const group = (value: unknown): CharacterAssetGroup | undefined => {
    if (!value || typeof value !== "object") return undefined;
    const rec = value as Record<string, unknown>;
    const out: CharacterAssetGroup = {};
    Object.keys(rec).forEach((key) => {
      if (typeof rec[key] === "string") out[key] = rec[key] as string;
    });
    return Object.keys(out).length ? out : undefined;
  };
  const assets: CharacterAssets = {};
  if (typeof raw.portrait === "string") assets.portrait = raw.portrait;
  if (raw.portraits && typeof raw.portraits === "object") {
    const portraits = raw.portraits as Record<string, unknown>;
    assets.portraits = {
      anime: typeof portraits.anime === "string" ? portraits.anime : null,
      stylized_25d: typeof portraits.stylized_25d === "string" ? portraits.stylized_25d : null,
      realistic: typeof portraits.realistic === "string" ? portraits.realistic : null,
    };
  }
  const three = group(raw.three_view);
  if (three) assets.three_view = three;
  const expr = group(raw.expressions);
  if (expr) assets.expressions = expr;
  if (typeof raw.sheet === "string") assets.sheet = raw.sheet;
  if (raw.exists && typeof raw.exists === "object") assets.exists = raw.exists as Record<string, boolean>;
  return assets;
}

function errorItems(status: number, detail: unknown, statusText: string): string[] {
  if (status === 404 || status === 405) {
    return ["接口尚未就绪（后端创建/概念图 API 实现中），请稍后重试"];
  }
  if (detail && typeof detail === "object" && !Array.isArray(detail)) {
    const rec = detail as Record<string, unknown>;
    if (Array.isArray(rec.errors)) return (rec.errors as unknown[]).map((item) => (typeof item === "string" ? item : JSON.stringify(item)));
    if (typeof rec.message === "string" && rec.message.trim()) return [rec.message];
    if (Array.isArray(rec.detail)) return errorItems(status, rec.detail, statusText);
  }
  if (Array.isArray(detail)) {
    return detail.map((item) => {
      if (typeof item === "string") return item;
      if (item && typeof item === "object") {
        const rec = item as Record<string, unknown>;
        const msg = typeof rec.msg === "string" ? rec.msg : typeof rec.message === "string" ? rec.message : JSON.stringify(item);
        const loc = Array.isArray(rec.loc) ? rec.loc.filter((part) => part !== "body").join(".") : "";
        return loc ? `${loc}：${msg}` : msg;
      }
      return String(item);
    });
  }
  if (typeof detail === "string" && detail.trim()) return [detail];
  if (status === 409) return ["同名 id 已存在，请更换 id 或留空自动生成"];
  return [statusText || "创建失败"];
}

async function post<T>(path: string, body: unknown, timeoutMs = 240000): Promise<T> {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), timeoutMs);
  let res: Response;
  try {
    res = await fetch(API_BASE + path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: controller.signal,
    });
  } catch (err) {
    window.clearTimeout(timer);
    const message = err instanceof DOMException && err.name === "AbortError" ? "请求超时（创建包含 TTS 合成，可重试）" : "无法连接后端";
    throw new CreationError(0, [message]);
  }
  window.clearTimeout(timer);
  if (!res.ok) {
    let detail: unknown = null;
    try {
      const data = (await res.json()) as { detail?: unknown };
      detail = data?.detail;
    } catch {
      /* 非 JSON */
    }
    throw new CreationError(res.status, errorItems(res.status, detail, res.statusText || "创建失败"));
  }
  return (await res.json()) as T;
}

export const creationApi = {
  createCharacter: (body: CharacterCreateBody): Promise<CharacterCreateResponse> =>
    post("/v5/characters", body, 300000),
  createTemplate: (body: TemplateCreateBody): Promise<TemplateCreateResponse> =>
    post("/v5/templates", body, 300000),
  /** V24：生成/重新生成角色概念图（SDXL，20–60s，串行 GPU） */
  generateCharacterArt: (
    character: string,
    body: {
      mode?: "portrait";
      modes?: string[];
      style?: string;
      prompt_extra?: string;
      seed?: number;
      force?: boolean;
    } = {},
  ): Promise<CharacterArtResponse> =>
    post(`/v5/characters/${encodeURIComponent(character)}/art`, { mode: "portrait", force: true, ...body }, 600000),
  /** V28：扫描角色已生成素材（三视图/表情/设定图/立绘） */
  getCharacterAssets: async (character: string): Promise<CharacterAssets> => {
    try {
      const res = await get(`/v5/characters/${encodeURIComponent(character)}/assets`);
      const rec = (res || {}) as Record<string, unknown>;
      const assets = (rec.assets || rec) as Record<string, unknown>;
      return normalizeAssets(assets);
    } catch (err) {
      throw err;
    }
  },
};

/** 400 错误是否为「后端未就绪」 */
export function isCreationNotReady(err: unknown): boolean {
  return err instanceof CreationError && (err.status === 404 || err.status === 405);
}

export function creationErrorMessage(err: unknown): string[] {
  if (err instanceof CreationError) return err.items;
  if (err instanceof ApiError) return [err.message];
  if (err instanceof Error) return [err.message];
  return [String(err)];
}

/** 自动生成符合 ^[a-z][a-z0-9_]{1,60}$ 的 id */
export function makeCustomId(prefix: "custom" | "custom_tpl"): string {
  const hex = Array.from({ length: 8 }, () => Math.floor(Math.random() * 16).toString(16)).join("");
  return `${prefix}_${hex}`;
}

/* ------------------------------ V32：角色试镜视频 ------------------------------ */
export type AuditionStatus = "queued" | "running" | "done" | "failed" | "canceled";
export type AuditionStyle = "anime" | "stylized_25d" | "realistic";
export type AuditionBackground = "studio" | "radio" | "cafe" | "street" | "office" | "ancient_hall" | "futuristic";
export type AuditionCamera = "medium" | "closeup" | "half_body";

export interface AuditionJob {
  id: string;
  character: string;
  style: AuditionStyle | string;
  status: AuditionStatus;
  stage?: string;
  progress?: number;
  text?: string;
  output_url?: string | null;
  /** V33：无字幕版 / SRT / 多句 */
  plain_output_url?: string | null;
  srt_url?: string | null;
  subtitles_burned?: boolean;
  lines?: string[];
  segments?: { text: string; start: number; end: number }[];
  total_lines?: number;
  current_line?: number;
  background?: AuditionBackground | string;
  background_label?: string;
  camera?: AuditionCamera | string;
  camera_label?: string;
  duration_sec?: number;
  size_bytes?: number;
  error?: string | null;
  estimated_sec?: number;
  elapsed_sec?: number;
  created_at?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
}

function normalizeAudition(raw: unknown): AuditionJob {
  const rec = (raw && typeof raw === "object" ? raw : {}) as Record<string, unknown>;
  return {
    id: String(rec.id ?? rec.job_id ?? ""),
    character: String(rec.character ?? ""),
    style: String(rec.style ?? "anime"),
    status: (String(rec.status ?? "queued") as AuditionStatus) || "queued",
    stage: typeof rec.stage === "string" ? rec.stage : undefined,
    progress: Number(rec.progress) || 0,
    text: typeof rec.text === "string" ? rec.text : undefined,
    output_url: typeof rec.output_url === "string" ? rec.output_url : null,
    plain_output_url: typeof rec.plain_output_url === "string" ? rec.plain_output_url : null,
    srt_url: typeof rec.srt_url === "string" ? rec.srt_url : null,
    subtitles_burned: Boolean(rec.subtitles_burned),
    lines: Array.isArray(rec.lines) ? rec.lines.map((line) => String(line)) : undefined,
    segments: Array.isArray(rec.segments)
      ? rec.segments.map((seg) => ({
          text: String((seg as Record<string, unknown>)?.text ?? ""),
          start: Number((seg as Record<string, unknown>)?.start) || 0,
          end: Number((seg as Record<string, unknown>)?.end) || 0,
        }))
      : undefined,
    total_lines: Number(rec.total_lines) || 0,
    current_line: Number(rec.current_line) || 0,
    background: typeof rec.background === "string" ? rec.background : undefined,
    background_label: typeof rec.background_label === "string" ? rec.background_label : undefined,
    camera: typeof rec.camera === "string" ? rec.camera : undefined,
    camera_label: typeof rec.camera_label === "string" ? rec.camera_label : undefined,
    duration_sec: Number(rec.duration_sec) || 0,
    size_bytes: Number(rec.size_bytes) || 0,
    error: typeof rec.error === "string" ? rec.error : null,
    estimated_sec: Number(rec.estimated_sec) || 0,
    elapsed_sec: Number(rec.elapsed_sec) || 0,
    created_at: typeof rec.created_at === "string" ? rec.created_at : null,
    started_at: typeof rec.started_at === "string" ? rec.started_at : null,
    finished_at: typeof rec.finished_at === "string" ? rec.finished_at : null,
  };
}

export function isAuditionActive(job: AuditionJob | null | undefined): boolean {
  return Boolean(job && (job.status === "queued" || job.status === "running"));
}

export const auditionApi = {
  start: async (
    character: string,
    body: {
      text?: string;
      lines?: string[];
      subtitles?: boolean;
      background?: AuditionBackground;
      camera?: AuditionCamera;
      style?: AuditionStyle;
      seed?: number;
      prompt_extra?: string;
    },
  ): Promise<AuditionJob> =>
    normalizeAudition(await post(`/v5/characters/${encodeURIComponent(character)}/audition`, body, 60000)),
  get: async (jobId: string): Promise<AuditionJob> =>
    normalizeAudition(await get(`/auditions/${encodeURIComponent(jobId)}`)),
  cancel: async (jobId: string): Promise<AuditionJob> =>
    normalizeAudition(await post(`/auditions/${encodeURIComponent(jobId)}/cancel`, {}, 30000)),
  list: async (character: string, limit = 10): Promise<AuditionJob[]> => {
    const res = (await get(`/v5/characters/${encodeURIComponent(character)}/auditions?limit=${limit}`)) as
      | { jobs?: unknown[]; auditions?: unknown[]; items?: unknown[] }
      | unknown[];
    const list = Array.isArray(res) ? res : res.jobs || res.auditions || res.items || [];
    return list.map(normalizeAudition);
  },
};

/* ------------------------------ V35：角色宣传片 ------------------------------ */
export type PromoStatus = "queued" | "running" | "done" | "failed" | "canceled";

export interface PromoSceneInput {
  lines: string[];
  background?: AuditionBackground;
  camera?: AuditionCamera;
  subtitles?: boolean;
}

export interface PromoJobShot {
  index?: number;
  status?: string;
  stage?: string;
  background?: string;
  background_label?: string;
  camera?: string;
  camera_label?: string;
  lines?: string[];
  duration_sec?: number;
}

export interface PromoJob {
  id: string;
  character: string;
  style: AuditionStyle | string;
  title?: string;
  status: PromoStatus;
  stage?: string;
  progress?: number;
  scene_count?: number;
  current_shot?: number;
  total_shots?: number;
  shots?: PromoJobShot[];
  output_url?: string | null;
  poster_url?: string | null;
  srt_url?: string | null;
  duration_sec?: number;
  size_bytes?: number;
  error?: string | null;
  estimated_sec?: number;
  elapsed_sec?: number;
  created_at?: string | null;
}

function normalizePromoJob(raw: unknown): PromoJob {
  const rec = (raw && typeof raw === "object" ? raw : {}) as Record<string, unknown>;
  const shotsRaw = Array.isArray(rec.shots) ? rec.shots : [];
  return {
    id: String(rec.id ?? rec.job_id ?? ""),
    character: String(rec.character ?? ""),
    style: String(rec.style ?? "realistic"),
    title: typeof rec.title === "string" ? rec.title : undefined,
    status: (String(rec.status ?? "queued") as PromoStatus) || "queued",
    stage: typeof rec.stage === "string" ? rec.stage : undefined,
    progress: Number(rec.progress) || 0,
    scene_count: Number(rec.scene_count) || shotsRaw.length,
    current_shot: Number(rec.current_shot) || 0,
    total_shots: Number(rec.total_shots) || 0,
    shots: shotsRaw.map((item) => {
      const shot = (item && typeof item === "object" ? item : {}) as Record<string, unknown>;
      return {
        index: Number(shot.index) || undefined,
        status: typeof shot.status === "string" ? shot.status : undefined,
        stage: typeof shot.stage === "string" ? shot.stage : undefined,
        background: typeof shot.background === "string" ? shot.background : undefined,
        background_label: typeof shot.background_label === "string" ? shot.background_label : undefined,
        camera: typeof shot.camera === "string" ? shot.camera : undefined,
        camera_label: typeof shot.camera_label === "string" ? shot.camera_label : undefined,
        lines: Array.isArray(shot.lines) ? shot.lines.map((line) => String(line)) : undefined,
        duration_sec: Number(shot.duration_sec) || 0,
      };
    }),
    output_url: typeof rec.output_url === "string" ? rec.output_url : null,
    poster_url: typeof rec.poster_url === "string" ? rec.poster_url : null,
    srt_url: typeof rec.srt_url === "string" ? rec.srt_url : null,
    duration_sec: Number(rec.duration_sec) || 0,
    size_bytes: Number(rec.size_bytes) || 0,
    error: typeof rec.error === "string" ? rec.error : null,
    estimated_sec: Number(rec.estimated_sec) || 0,
    elapsed_sec: Number(rec.elapsed_sec) || 0,
    created_at: typeof rec.created_at === "string" ? rec.created_at : null,
  };
}

export function isPromoActive(job: PromoJob | null | undefined): boolean {
  return Boolean(job && (job.status === "queued" || job.status === "running"));
}

export const promoApi = {
  start: async (
    character: string,
    body: { style?: AuditionStyle; title?: string; seed?: number; shots: PromoSceneInput[] },
  ): Promise<PromoJob> => normalizePromoJob(await post(`/v5/characters/${encodeURIComponent(character)}/promo`, body, 60000)),
  get: async (jobId: string): Promise<PromoJob> => normalizePromoJob(await get(`/promos/${encodeURIComponent(jobId)}`)),
  cancel: async (jobId: string): Promise<PromoJob> =>
    normalizePromoJob(await post(`/promos/${encodeURIComponent(jobId)}/cancel`, {}, 30000)),
  list: async (character: string, limit = 5): Promise<PromoJob[]> => {
    const res = (await get(`/v5/characters/${encodeURIComponent(character)}/promos?limit=${limit}`)) as
      | { promos?: unknown[]; jobs?: unknown[]; items?: unknown[] }
      | unknown[];
    const list = Array.isArray(res) ? res : res.promos || res.jobs || res.items || [];
    return list.map(normalizePromoJob);
  },
};

/* ------------------------------ V36：双人对戏 + 试镜合集发布包 ------------------------------ */
export type DialogueSpeaker = "a" | "b" | "c";

export interface DialogueLineInput {
  speaker: DialogueSpeaker;
  text: string;
}

export interface DialogueScene {
  background?: string;
  background_label?: string;
  camera?: string;
  camera_label?: string;
}

export interface DialogueJobLine extends DialogueLineInput {
  index?: number;
  start?: number;
  end?: number;
  duration_sec?: number;
  background?: string;
  background_label?: string;
  camera?: string;
  camera_label?: string;
  character?: string;
}

export interface DialogueJob {
  id: string;
  character_a: string;
  character_b: string;
  a_label?: string;
  b_label?: string;
  style: AuditionStyle | string;
  background?: string;
  background_label?: string;
  camera?: string;
  camera_label?: string;
  title?: string;
  status: "queued" | "running" | "done" | "failed" | "canceled";
  stage?: string;
  progress?: number;
  current_line?: number;
  total_lines?: number;
  lines?: DialogueJobLine[];
  scenes?: { a?: DialogueScene; b?: DialogueScene; c?: DialogueScene };
  characters?: { a?: string; b?: string; c?: string };
  character_c?: string;
  c_label?: string;
  output_url?: string | null;
  poster_url?: string | null;
  srt_url?: string | null;
  duration_sec?: number;
  size_bytes?: number;
  error?: string | null;
  elapsed_sec?: number;
  created_at?: string | null;
}

function normalizeDialogueJob(raw: unknown): DialogueJob {
  const rec = (raw && typeof raw === "object" ? raw : {}) as Record<string, unknown>;
  const linesRaw = Array.isArray(rec.lines) ? rec.lines : [];
  return {
    id: String(rec.id ?? rec.job_id ?? ""),
    character_a: String(rec.character_a ?? ""),
    character_b: String(rec.character_b ?? ""),
    a_label: typeof rec.a_label === "string" ? rec.a_label : undefined,
    b_label: typeof rec.b_label === "string" ? rec.b_label : undefined,
    style: String(rec.style ?? "realistic"),
    background: typeof rec.background === "string" ? rec.background : undefined,
    background_label: typeof rec.background_label === "string" ? rec.background_label : undefined,
    camera: typeof rec.camera === "string" ? rec.camera : undefined,
    camera_label: typeof rec.camera_label === "string" ? rec.camera_label : undefined,
    character_c: typeof rec.character_c === "string" ? rec.character_c : undefined,
    c_label: typeof rec.c_label === "string" ? rec.c_label : undefined,
    characters: (() => {
      const value = rec.characters;
      if (!value || typeof value !== "object") return undefined;
      const chars = value as Record<string, unknown>;
      return {
        a: typeof chars.a === "string" ? chars.a : undefined,
        b: typeof chars.b === "string" ? chars.b : undefined,
        c: typeof chars.c === "string" ? chars.c : undefined,
      };
    })(),
    title: typeof rec.title === "string" ? rec.title : undefined,
    status: (String(rec.status ?? "queued") as DialogueJob["status"]) || "queued",
    stage: typeof rec.stage === "string" ? rec.stage : undefined,
    progress: Number(rec.progress) || 0,
    current_line: Number(rec.current_line) || 0,
    total_lines: Number(rec.total_lines) || linesRaw.length,
    lines: linesRaw.map((item) => {
      const line = (item && typeof item === "object" ? item : {}) as Record<string, unknown>;
      return {
        speaker: ((value): DialogueSpeaker => (value === "b" ? "b" : value === "c" ? "c" : "a"))(String(line.speaker ?? "a")),
        text: String(line.text ?? ""),
        index: Number(line.index) || undefined,
        start: Number(line.start) || 0,
        end: Number(line.end) || 0,
        duration_sec: Number(line.duration_sec) || 0,
        background: typeof line.background === "string" ? line.background : undefined,
        background_label: typeof line.background_label === "string" ? line.background_label : undefined,
        camera: typeof line.camera === "string" ? line.camera : undefined,
        camera_label: typeof line.camera_label === "string" ? line.camera_label : undefined,
        character: typeof line.character === "string" ? line.character : undefined,
      };
    }),
    scenes: (() => {
      const rawScenes = (rec.scenes && typeof rec.scenes === "object" ? rec.scenes : null) as Record<string, unknown> | null;
      if (!rawScenes) return undefined;
      const parse = (value: unknown): DialogueScene | undefined => {
        if (!value || typeof value !== "object") return undefined;
        const scene = value as Record<string, unknown>;
        return {
          background: typeof scene.background === "string" ? scene.background : undefined,
          background_label: typeof scene.background_label === "string" ? scene.background_label : undefined,
          camera: typeof scene.camera === "string" ? scene.camera : undefined,
          camera_label: typeof scene.camera_label === "string" ? scene.camera_label : undefined,
        };
      };
      return { a: parse(rawScenes.a), b: parse(rawScenes.b), c: parse(rawScenes.c) };
    })(),
    output_url: typeof rec.output_url === "string" ? rec.output_url : null,
    poster_url: typeof rec.poster_url === "string" ? rec.poster_url : null,
    srt_url: typeof rec.srt_url === "string" ? rec.srt_url : null,
    duration_sec: Number(rec.duration_sec) || 0,
    size_bytes: Number(rec.size_bytes) || 0,
    error: typeof rec.error === "string" ? rec.error : null,
    elapsed_sec: Number(rec.elapsed_sec) || 0,
    created_at: typeof rec.created_at === "string" ? rec.created_at : null,
  };
}

export function isDialogueActive(job: DialogueJob | null | undefined): boolean {
  return Boolean(job && (job.status === "queued" || job.status === "running"));
}

export const dialogueApi = {
  start: async (body: {
    character_a: string;
    character_b: string;
    style?: AuditionStyle;
    background?: AuditionBackground;
    camera?: AuditionCamera;
    title?: string;
    scene_a?: { background?: AuditionBackground; camera?: AuditionCamera };
    scene_b?: { background?: AuditionBackground; camera?: AuditionCamera };
    character_c?: string;
    scene_c?: { background?: AuditionBackground; camera?: AuditionCamera };
    lines: DialogueLineInput[];
  }): Promise<DialogueJob> => normalizeDialogueJob(await post("/v5/characters/dialogue", body, 60000)),
  get: async (jobId: string): Promise<DialogueJob> => normalizeDialogueJob(await get(`/v5/dialogues/${encodeURIComponent(jobId)}`)),
  cancel: async (jobId: string): Promise<DialogueJob> =>
    normalizeDialogueJob(await post(`/v5/dialogues/${encodeURIComponent(jobId)}/cancel`, {}, 30000)),
  list: async (limit = 5): Promise<DialogueJob[]> => {
    const res = (await get(`/v5/dialogues?limit=${limit}`)) as { dialogues?: unknown[]; jobs?: unknown[]; items?: unknown[] } | unknown[];
    const list = Array.isArray(res) ? res : res.dialogues || res.jobs || res.items || [];
    return list.map(normalizeDialogueJob);
  },
};

export interface ShowcasePackageFile {
  name: string;
  size_bytes?: number;
  size?: number;
}

export interface ShowcasePackage {
  ok: boolean;
  package_url?: string | null;
  size_bytes?: number;
  files: ShowcasePackageFile[];
  cached?: boolean;
  title?: string;
  vertical?: { file?: string; resolution?: string; mode?: string } | null;
  created_at?: string | null;
  error?: string | null;
}

function normalizeShowcasePackage(raw: unknown): ShowcasePackage {
  const rec = (raw && typeof raw === "object" ? raw : {}) as Record<string, unknown>;
  const files = Array.isArray(rec.files) ? rec.files : [];
  return {
    ok: rec.ok !== false,
    package_url: typeof rec.package_url === "string" ? rec.package_url : null,
    size_bytes: Number(rec.size_bytes) || 0,
    files: files.map((item) => {
      const file = (item && typeof item === "object" ? item : {}) as Record<string, unknown>;
      return {
        name: String(file.name ?? ""),
        size_bytes: Number(file.size_bytes ?? file.size) || 0,
      };
    }),
    cached: Boolean(rec.cached),
    title: typeof rec.title === "string" ? rec.title : undefined,
    vertical: (() => {
      const value = rec.vertical;
      if (!value || typeof value !== "object") return null;
      const vertical = value as Record<string, unknown>;
      return {
        file: typeof vertical.file === "string" ? vertical.file : undefined,
        resolution: typeof vertical.resolution === "string" ? vertical.resolution : undefined,
        mode: typeof vertical.mode === "string" ? vertical.mode : undefined,
      };
    })(),
    created_at: typeof rec.created_at === "string" ? rec.created_at : null,
    error: typeof rec.error === "string" ? rec.error : null,
  };
}

export interface SplitPackage {
  ok: boolean;
  package_url?: string | null;
  size_bytes?: number;
  files: ShowcasePackageFile[];
  characters: string[];
  cached?: boolean;
  created_at?: string | null;
  error?: string | null;
}

function normalizeSplitPackage(raw: unknown): SplitPackage {
  const rec = (raw && typeof raw === "object" ? raw : {}) as Record<string, unknown>;
  const files = Array.isArray(rec.files) ? rec.files : [];
  const characters = Array.isArray(rec.characters) ? rec.characters.map((item) => String(item)) : [];
  return {
    ok: rec.ok !== false,
    package_url: typeof rec.package_url === "string" ? rec.package_url : null,
    size_bytes: Number(rec.size_bytes) || 0,
    files: files.map((item) => {
      const file = (item && typeof item === "object" ? item : {}) as Record<string, unknown>;
      return { name: String(file.name ?? file.character ?? ""), size_bytes: Number(file.size_bytes ?? file.size) || 0 };
    }),
    characters,
    cached: Boolean(rec.cached),
    created_at: typeof rec.created_at === "string" ? rec.created_at : null,
    error: typeof rec.error === "string" ? rec.error : null,
  };
}

export const splitPackageApi = {
  build: async (body: { force?: boolean } = {}): Promise<SplitPackage> =>
    normalizeSplitPackage(await post("/v5/character-showcase/split-packages", { force: false, ...body }, 300000)),
  list: async (): Promise<SplitPackage[]> => {
    const res = (await get("/v5/character-showcase/split-packages")) as
      | { packages?: unknown[]; items?: unknown[]; package_url?: string; files?: unknown[] }
      | unknown[];
    if (Array.isArray(res)) return res.map(normalizeSplitPackage);
    if (res && Array.isArray(res.packages)) return res.packages.map(normalizeSplitPackage);
    if (res && Array.isArray(res.items)) return res.items.map(normalizeSplitPackage);
    if (res && (res.package_url || Array.isArray(res.files))) return [normalizeSplitPackage(res)];
    return [];
  },
};

export const showcaseApi = {
  build: async (body: { title?: string; force?: boolean; vertical?: boolean; vertical_mode?: "blur_pad" | "crop" } = {}): Promise<ShowcasePackage> =>
    normalizeShowcasePackage(
      await post("/v5/character-showcase/publish-package", { vertical: false, vertical_mode: "blur_pad", ...body }, 300000),
    ),
  list: async (): Promise<ShowcasePackage[]> => {
    const res = (await get("/v5/character-showcase/publish-package")) as
      | { packages?: unknown[]; items?: unknown[]; package_url?: string; files?: unknown[] }
      | unknown[];
    if (Array.isArray(res)) return res.map(normalizeShowcasePackage);
    if (res && Array.isArray(res.packages)) return res.packages.map(normalizeShowcasePackage);
    if (res && Array.isArray(res.items)) return res.items.map(normalizeShowcasePackage);
    if (res && (res.package_url || Array.isArray(res.files))) return [normalizeShowcasePackage(res)];
    return [];
  },
};
