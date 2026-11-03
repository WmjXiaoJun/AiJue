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
