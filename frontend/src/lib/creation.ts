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
  appearance: {
    gender?: string;
    age?: number;
    hair?: string;
    hair_style?: string;
    outfit?: { type?: string; main?: string; accent?: string };
    accessories?: string[];
  };
}

export interface CharacterCreateResponse {
  ok: boolean;
  id: string;
  item?: Record<string, unknown>;
  preview_url?: string;
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

function errorItems(status: number, detail: unknown, statusText: string): string[] {
  if (status === 404 || status === 405) {
    return ["创建接口尚未就绪（后端 V21 创建 API 实现中）；当前操作未写入任何数据，请稍后重试"];
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
    post("/v5/characters", body),
  createTemplate: (body: TemplateCreateBody): Promise<TemplateCreateResponse> =>
    post("/v5/templates", body),
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
