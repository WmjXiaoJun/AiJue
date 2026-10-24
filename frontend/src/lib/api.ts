// 极薄的 fetch 封装：相对 /api（生产同源），dev 可用 VITE_API_BASE 直连后端。
import type {
  Asset,
  Character,
  CharacterFiles,
  CharacterSpec,
  ConceptFiles,
  CostReport,
  Episode,
  Health,
  Job,
  Localization,
  NewAsset,
  PublishConfig,
  PublishRecord,
  PublishRequest,
  NewShot,
  Project,
  Settings,
  Shot,
  ShotVersion,
  StoryboardResult,
} from "../types";

const RAW_BASE = (import.meta.env.VITE_API_BASE || "").trim();
export const API_BASE = (RAW_BASE || "/api").replace(/\/+$/, "");
export const IS_DIRECT = /^https?:\/\//i.test(RAW_BASE);

/** 静态资源前缀：直连后端时同源取 origin，否则交给 dev proxy / 生产后端。 */
export function mediaUrl(url?: string | null): string {
  if (!url) return "";
  if (/^(https?:|blob:|data:)/i.test(url)) return url;
  const origin = IS_DIRECT ? RAW_BASE.replace(/\/api\/?$/i, "") : "";
  return origin + (url.startsWith("/") ? url : "/" + url);
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), 20000);
  let res: Response;
  try {
    res = await fetch(API_BASE + path, {
      headers: init?.body ? { "Content-Type": "application/json" } : undefined,
      ...init,
      signal: controller.signal,
    });
  } catch (err) {
    window.clearTimeout(timer);
    const message = err instanceof DOMException && err.name === "AbortError" ? "请求超时" : "无法连接后端";
    throw new ApiError(0, message);
  }
  window.clearTimeout(timer);
  if (!res.ok) {
    let detail = res.statusText || "请求失败";
    try {
      const data = (await res.json()) as { detail?: unknown };
      if (typeof data?.detail === "string") detail = data.detail;
      else if (data?.detail) detail = JSON.stringify(data.detail);
    } catch {
      /* 非 JSON 错误体，保留状态文本 */
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

const qs = (params: Record<string, string | undefined | null>) => {
  const search = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== "") search.set(k, v);
  });
  const text = search.toString();
  return text ? "?" + text : "";
};

export const api = {
  health: () => request<Health>("/health"),

  projects: () => request<Project[]>("/projects"),
  project: (pid: string) => request<Project>("/projects/" + pid),
  createProject: (body: { name: string; format_id: string; aspect?: string }) =>
    request<Project>("/projects", { method: "POST", body: JSON.stringify(body) }),
  episodes: (pid: string) => request<Episode[]>("/projects/" + pid + "/episodes"),
  createEpisode: (pid: string, body: { title?: string; target_duration?: number } = {}) =>
    request<Episode>("/projects/" + pid + "/episodes", { method: "POST", body: JSON.stringify(body) }),

  episode: (eid: string) => request<Episode>("/episodes/" + eid),
  patchEpisode: (eid: string, body: { title?: string; target_duration?: number; script?: string }) =>
    request<Episode>("/episodes/" + eid, { method: "PATCH", body: JSON.stringify(body) }),

  assets: (eid: string) => request<Asset[]>("/episodes/" + eid + "/assets"),
  createAsset: (eid: string, body: NewAsset) =>
    request<Asset>("/episodes/" + eid + "/assets", { method: "POST", body: JSON.stringify(body) }),
  patchAsset: (aid: string, body: { locked?: boolean; voice?: string; persona?: string }) =>
    request<Asset>("/assets/" + aid, { method: "PATCH", body: JSON.stringify(body) }),

  shots: (eid: string) => request<Shot[]>("/episodes/" + eid + "/shots"),
  createShot: (eid: string, body: NewShot) =>
    request<Shot>("/episodes/" + eid + "/shots", { method: "POST", body: JSON.stringify(body) }),
  deleteShot: (sid: string) => request<{ ok?: boolean } | void>("/shots/" + sid, { method: "DELETE" }),
  patchShot: (sid: string, body: { line?: string; duration?: number; status?: string; visual?: string; punchline?: string }) =>
    request<Shot>("/shots/" + sid, { method: "PATCH", body: JSON.stringify(body) }),
  generateShot: (sid: string, params?: Record<string, unknown>) =>
    request<Job>("/shots/" + sid + "/generate", { method: "POST", body: JSON.stringify({ params: params ?? {} }) }),
  /** 生成单个镜头的 H3 动态版本：{mode:"dynamic"}；后端仅接受 params 时自动回退 */
  generateDynamicShot: async (sid: string): Promise<Job> => {
    const post = (payload: Record<string, unknown>) =>
      request<Job>("/shots/" + sid + "/generate", { method: "POST", body: JSON.stringify(payload) });
    try {
      return await post({ mode: "dynamic", params: { mode: "dynamic" } });
    } catch (err) {
      if (err instanceof ApiError && (err.status === 422 || err.status === 400)) {
        return post({ params: { mode: "dynamic" } });
      }
      throw err;
    }
  },
  /** 批量生成全部镜头动态版本 */
  generateAllDynamic: async (eid: string): Promise<{ created?: number; jobs?: Job[]; count?: number }> => {
    return request<{ created?: number; jobs?: Job[]; count?: number }>(
      "/episodes/" + eid + "/shots/generate-dynamic",
      { method: "POST", body: "{}" }
    );
  },
  versions: (sid: string) => request<ShotVersion[]>("/shots/" + sid + "/versions"),
  selectVersion: (sid: string, vid: string) =>
    request<Shot>("/shots/" + sid + "/versions/" + vid + "/select", { method: "POST", body: "{}" }),

  generateStoryboard: (eid: string, force = false) =>
    request<StoryboardResult>("/episodes/" + eid + "/storyboard/generate", {
      method: "POST",
      body: JSON.stringify({ force }),
    }),
  render: (eid: string) => request<Job>("/episodes/" + eid + "/render", { method: "POST", body: "{}" }),

  jobs: (params: { episode_id?: string; type?: string; status?: string } = {}) =>
    request<Job[]>("/jobs" + qs(params)),
  job: (jid: string) => request<Job>("/jobs/" + jid),
  retryJob: (jid: string) => request<Job>("/jobs/" + jid + "/retry", { method: "POST", body: "{}" }),

  costs: (eid: string) => request<CostReport>("/episodes/" + eid + "/costs"),

  // ---- 角色工坊（contract/CHARACTER.md） ----
  characters: (pid: string) => request<Character[]>("/projects/" + pid + "/characters"),
  character: (cid: string) => request<Character>("/characters/" + cid),
  /** 契约要求扁平 spec；后端亦兼容 {spec:{...}}，422/400 时自动回退 */
  createCharacter: async (pid: string, spec: CharacterSpec): Promise<Character> => {
    try {
      return await request<Character>("/projects/" + pid + "/characters", {
        method: "POST",
        body: JSON.stringify(spec),
      });
    } catch (err) {
      if (err instanceof ApiError && (err.status === 400 || err.status === 422)) {
        return request<Character>("/projects/" + pid + "/characters", {
          method: "POST",
          body: JSON.stringify({ spec }),
        });
      }
      throw err;
    }
  },
  patchCharacter: (
    cid: string,
    body: Partial<Pick<Character, "name" | "persona" | "voice" | "accent_color">> & { spec?: CharacterSpec }
  ) => request<Character>("/characters/" + cid, { method: "PATCH", body: JSON.stringify(body) }),
  generateCharacter: (cid: string, styles?: string[]) =>
    request<Job>("/characters/" + cid + "/generate", {
      method: "POST",
      body: JSON.stringify({ styles: styles ?? [] }),
    }),
  characterFiles: (cid: string) => request<CharacterFiles>("/characters/" + cid + "/files"),

  // ---- 概念图 / H3 动画 / 导出（contract/CONCEPT.md、contract/EXPORT.md） ----
  characterConcepts: (cid: string) => request<ConceptFiles>("/characters/" + cid + "/concepts"),
  generateConcept: (cid: string, body: { modes?: string[]; style?: string; prompt_extra?: string; seed?: number }) =>
    request<Job>("/characters/" + cid + "/concept/generate", { method: "POST", body: JSON.stringify(body) }),
  /** 优先 outputs 相对路径；400/422 时回退为 /media URL，兼容不同后端实现 */
  setPortraitFromConcept: async (cid: string, path: string, fallbackPath?: string): Promise<Character> => {
    try {
      return await request<Character>("/characters/" + cid + "/portrait/from-concept", {
        method: "POST",
        body: JSON.stringify({ path }),
      });
    } catch (err) {
      if (fallbackPath && err instanceof ApiError && (err.status === 400 || err.status === 422)) {
        return request<Character>("/characters/" + cid + "/portrait/from-concept", {
          method: "POST",
          body: JSON.stringify({ path: fallbackPath }),
        });
      }
      throw err;
    }
  },
  h3Animate: async (
    cid: string,
    body: { image?: string; prompt?: string; provider?: string } = {},
    fallbackImage?: string
  ): Promise<Job> => {
    const post = (payload: Record<string, unknown>) =>
      request<Job>("/characters/" + cid + "/h3-animate", { method: "POST", body: JSON.stringify(payload) });
    try {
      return await post(body);
    } catch (err) {
      const retryable = err instanceof ApiError && (err.status === 400 || err.status === 422);
      if (!retryable) throw err;
      // 1) 后端未支持 body 覆盖 provider → 去掉 provider 重试（仅用 settings）
      if (body.provider) {
        const { provider: _omit, ...rest } = body;
        try {
          return await post(rest);
        } catch (inner) {
          if (!(inner instanceof ApiError) || !fallbackImage || !rest.image) throw inner;
          return post({ ...rest, image: fallbackImage });
        }
      }
      // 2) 路径格式不兼容 → 换 /media URL 重试
      if (fallbackImage && body.image) return post({ ...body, image: fallbackImage });
      throw err;
    }
  },
  exportEpisode: (eid: string, type = "capcut") =>
    request<Job>("/episodes/" + eid + "/export", { method: "POST", body: JSON.stringify({ type }) }),

  /** 生成多语种出海版本：每语言一个 Job（兼容返回 Job[] / {jobs:[...]} / 单个 Job） */
  localizeEpisode: async (eid: string, langs: string[]): Promise<Job[]> => {
    const res = await request<Job | Job[] | { jobs?: Job[]; created?: number }>(
      "/episodes/" + eid + "/localize",
      { method: "POST", body: JSON.stringify({ langs }) }
    );
    if (Array.isArray(res)) return res;
    if (res && Array.isArray((res as { jobs?: Job[] }).jobs)) return (res as { jobs: Job[] }).jobs;
    if (res && typeof (res as Job).id === "string") return [res as Job];
    return [];
  },
  localizations: (eid: string) => request<Localization[]>("/episodes/" + eid + "/localizations"),
  localization: (lid: string) => request<Localization>("/localizations/" + lid),

  /** 海外发布（contract/PUBLISHING.md） */
  publishConfig: () => request<PublishConfig>("/publish/config"),
  publishEpisode: (eid: string, body: PublishRequest) =>
    request<Job>("/episodes/" + eid + "/publish", { method: "POST", body: JSON.stringify(body) }),
  publishes: (eid: string) => request<PublishRecord[]>("/episodes/" + eid + "/publishes"),
  publish: (id: string) => request<PublishRecord>("/publishes/" + id),

  settings: () => request<Settings>("/settings"),
  patchSettings: (body: Partial<Settings>) =>
    request<Settings>("/settings", { method: "PATCH", body: JSON.stringify(body) }),
};

export function errorText(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return String(err);
}
