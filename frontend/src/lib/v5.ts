// V5 Studio 前端 API 层：类型 + fetch 封装 + 媒体/预览 URL（相对 /api，dev 走 Vite proxy）
import { API_BASE, IS_DIRECT, mediaUrl } from "./api";

// ---------------------------------------------------------------------------
// 类型（对齐 backend/app/routers/v5_studio.py 与 v5_catalog.py）
// ---------------------------------------------------------------------------
export interface V5Output {
  id: string;
  label: string;
  style: string;
  style_label: string;
  style_tags: string[];
  duration: number | null;
  resolution: string | null;
  fps: number | null;
  qc_ok: boolean | null;
  qc_checks: number;
  qc_total: number;
  shots: number;
  scenes: string[];
  characters: string[];
  final_url: string | null;
  timeline_url: string | null;
  qc_url: string | null;
  style_report_url: string | null;
  poster_url: string | null;
  mtime: string;
}

export interface V5OutputsResponse {
  ok: boolean;
  root: string;
  count: number;
  expected: number;
  outputs: V5Output[];
  missing: string[];
  generated_at: string;
}

export interface V5Palette {
  [key: string]: string | string[];
}

export interface V5StyleSummary {
  id: string;
  label: string;
  tags?: string[];
  technique?: string[];
  character_treatment?: string;
  transition?: string;
  palette?: V5Palette;
  custom?: boolean;
}

export interface V5SceneSummary {
  id: string;
  label: string;
  tags?: string[];
  layer_count?: number;
  layer_types?: string[];
  camera_zones?: string[];
  aspect?: number[];
  variants?: string[];
  custom?: boolean;
}

export interface V5CharacterSummary {
  id: string;
  label: string;
  role_pool?: string[];
  gender?: string;
  age?: number;
  hat?: string | null;
  voice_provider?: string;
  has_image?: boolean;
  tags?: string[];
  custom?: boolean;
}

export interface V5CastMember {
  role: string;
  character: string;
}

export interface V5TemplateSummary {
  id: string;
  label: string;
  tags?: string[];
  style?: string;
  target_duration?: number;
  cast?: V5CastMember[];
  act_count?: number;
  act_ids?: string[];
  scene_route?: string[];
  custom?: boolean;
}

export interface V5CatalogResponse {
  ok: boolean;
  root: string;
  kinds: string[];
  counts: Record<string, number>;
  styles: V5StyleSummary[];
  scenes: V5SceneSummary[];
  characters: V5CharacterSummary[];
  templates: V5TemplateSummary[];
  props?: { id: string; label: string; kind?: string; custom?: boolean }[];
  packs?: { id: string; label: string; custom?: boolean }[];
}

export interface V5VoicePreset {
  voice_id: string;
  label?: string;
  seed?: number;
  cfg_scale?: number;
  tags?: string[];
}

export interface V5VoicesResponse {
  ok: boolean;
  count: number;
  presets: V5VoicePreset[];
}

export interface V5VoiceLock {
  character?: string;
  key?: string;
  wav?: string;
  exists?: boolean;
  text?: string;
  seed?: number;
  voice_id?: string;
  created_at?: string;
}

export interface V5VoiceInfo {
  ok: boolean;
  character: string;
  voice_id: string;
  seed: number;
  cfg_scale: number;
  instruction?: string | null;
  key: string;
  ref_text?: string;
  lock: V5VoiceLock;
  needs_relock: boolean;
  stale_locks?: string[];
}

export interface V5VoiceUpdateBody {
  voice_id?: string;
  seed?: number;
  cfg_scale?: number;
  instruction?: string;
}

export interface V5VoiceUpdateResponse {
  ok: boolean;
  character: string;
  written: string;
  voice: { voice_id: string; seed: number; cfg_scale: number; instruction?: string | null; key: string };
  previous_key: string;
  needs_relock: boolean;
  lock: V5VoiceLock;
  hint?: string;
}

export type V5RenderStatus = "queued" | "running" | "done" | "failed" | "canceled";

export interface V5RenderJob {
  id: string;
  template: string;
  duration: number;
  force: boolean;
  status: V5RenderStatus;
  progress: number;
  message: string;
  created_at?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  elapsed: number;
  returncode?: number | null;
  error?: string | null;
  result?: Record<string, unknown> | null;
  log_tail: string[];
  out_dir?: string;
}

export interface V5RenderListResponse {
  ok: boolean;
  running: V5RenderJob | null;
  jobs: V5RenderJob[];
  count: number;
}

// ---------------------------------------------------------------------------
// fetch 封装（渲染/试听可能较慢，默认 90s；提交渲染 30s）
// ---------------------------------------------------------------------------
export class V5ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = "V5ApiError";
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit, timeoutMs = 90000): Promise<T> {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), timeoutMs);
  let res: Response;
  try {
    res = await fetch(API_BASE + path, {
      headers: init?.body ? { "Content-Type": "application/json" } : undefined,
      ...init,
      signal: controller.signal,
    });
  } catch (err) {
    window.clearTimeout(timer);
    const timeout = err instanceof DOMException && err.name === "AbortError";
    throw new V5ApiError(0, timeout ? "请求超时（渲染/试听可能较慢，请稍后重试）" : "无法连接后端");
  }
  window.clearTimeout(timer);
  if (!res.ok) {
    let detail = res.statusText || "请求失败";
    try {
      const data = (await res.json()) as { detail?: unknown };
      if (typeof data?.detail === "string") detail = data.detail;
      else if (data?.detail) detail = JSON.stringify(data.detail);
    } catch {
      /* 非 JSON 错误体 */
    }
    throw new V5ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

function qs(params: Record<string, string | number | boolean | undefined | null>): string {
  const search = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== "") search.set(key, String(value));
  });
  const text = search.toString();
  return text ? "?" + text : "";
}

const enc = encodeURIComponent;

export interface V5ItemResponse {
  ok: boolean;
  kind: string;
  id: string;
  item: Record<string, unknown>;
  custom?: boolean;
  source?: string;
  base?: string;
  custom_path?: string;
}

export interface V5PutResponse {
  ok: boolean;
  kind: string;
  id: string;
  path?: string;
  dry_run?: boolean;
  validated?: boolean;
  write_mode?: string;
  warnings?: unknown[];
}

export const v5api = {
  outputs: (includeCustom = false) =>
    request<V5OutputsResponse>("/v5/outputs" + qs({ include_custom: includeCustom ? "true" : undefined })),
  catalog: () => request<V5CatalogResponse>("/v5/catalog"),
  /** 读单个对象（内建 + custom 合并后的完整 JSON） */
  item: (kind: string, id: string) =>
    request<V5ItemResponse>("/v5/" + enc(kind) + "/" + enc(id)),
  /** 保存 custom 覆盖（dryRun=true 只校验不落盘；校验失败抛 V5ApiError，message 含 errors） */
  putItem: (kind: string, id: string, item: Record<string, unknown>, dryRun = false) =>
    request<V5PutResponse>(
      "/v5/" + enc(kind) + "/" + enc(id) + (dryRun ? "?dry_run=true" : ""),
      { method: "PUT", body: JSON.stringify({ item, dry_run: dryRun }) },
      60000
    ),
  voices: () => request<V5VoicesResponse>("/v5/voices"),
  characterVoice: (character: string) =>
    request<V5VoiceInfo>("/v5/voice/" + enc(character)),
  putVoice: (character: string, body: V5VoiceUpdateBody) =>
    request<V5VoiceUpdateResponse>("/v5/voice/" + enc(character), {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  previewVoice: (character: string, text?: string) =>
    request<{ ok: boolean; character: string; out?: string; duration?: number; locked?: boolean }>(
      "/v5/voice/" + enc(character) + "/preview",
      { method: "POST", body: JSON.stringify(text ? { text } : {}) },
      240000
    ),
  lockVoice: (character: string, force = true) =>
    request<{ ok: boolean; character: string; voice_id: string; key: string; lock: V5VoiceLock }>(
      "/v5/voice/" + enc(character) + "/lock",
      { method: "POST", body: JSON.stringify({ force }) },
      600000
    ),
  render: (body: { template: string; duration: number; force: boolean }) =>
    request<{ ok: boolean; job: V5RenderJob; cmd: string[] }>("/v5/render", {
      method: "POST",
      body: JSON.stringify(body),
    }, 30000),
  renderJobs: () => request<V5RenderListResponse>("/v5/render"),
  renderJob: (jobId: string) => request<{ ok: boolean; job: V5RenderJob }>("/v5/render/" + enc(jobId)),
  cancelRender: (jobId: string) =>
    request<{ ok: boolean; job: V5RenderJob }>("/v5/render/" + enc(jobId), { method: "DELETE" }),
};

// ---------------------------------------------------------------------------
// URL helpers
// ---------------------------------------------------------------------------
/** /api 下的接口 URL（dev 相对路径走 Vite proxy；直连模式用绝对地址）。 */
export function apiUrl(path: string): string {
  return API_BASE + (path.startsWith("/") ? path : "/" + path);
}

/** /media 静态资源（自动处理直连后端的 origin）。 */
export function mediaSrc(url?: string | null): string {
  return mediaUrl(url);
}

/** 场景预览（程序化渲染；size 越小越快） */
export function scenePreviewUrl(sceneId: string, styleId?: string, size = "640x360"): string {
  const params = new URLSearchParams({ size });
  if (styleId) params.set("style", styleId);
  return apiUrl("/v5/preview/scene/" + enc(sceneId) + "?" + params.toString());
}

/** 角色预览（程序化渲染，作为 AI 立绘缺失时的回退） */
export function characterPreviewUrl(characterId: string, styleId?: string): string {
  const params = new URLSearchParams({ size: "640x960" });
  if (styleId) params.set("style", styleId);
  return apiUrl("/v5/preview/character/" + enc(characterId) + "?" + params.toString());
}

/** 角色 AI 立绘（assets/v5/characters/<id>/portrait.png；未生成时 404） */
export function characterArtUrl(characterId: string, file = "portrait.png"): string {
  return mediaSrc("/media/v5/characters/" + enc(characterId) + "/" + file);
}

/** 场景 AI plate（assets/v5/scenes/<id>.png；未生成时 404） */
export function sceneArtUrl(sceneId: string): string {
  return mediaSrc("/media/v5/scenes/" + enc(sceneId) + ".png");
}

/** 角色试听 wav（后端 tmp/v5_voice/<id>.wav，带时间戳避免缓存） */
export function voiceWavUrl(characterId: string): string {
  return mediaSrc("/media/v5/voice/" + enc(characterId) + ".wav") + "?t=" + Date.now();
}

/** 风格墙代表场景：优先找使用该 style 的模板的第一个 act.scene */
export function representativeScene(
  catalog: V5CatalogResponse | null,
  styleId: string
): string | null {
  if (!catalog) return null;
  const template = catalog.templates.find(
    (item) => item.style === styleId && (item.scene_route || []).length > 0
  );
  if (template && template.scene_route && template.scene_route[0]) return template.scene_route[0];
  const tagged = catalog.scenes.find((scene) => (scene.tags || []).includes(styleId));
  return tagged ? tagged.id : null;
}

/** 角色预览用的默认风格：按 era/tags 选（历史→水墨，现代→霓虹），否则第一个 */
export function defaultStyleForCharacter(
  catalog: V5CatalogResponse | null,
  character: V5CharacterSummary | null
): string | undefined {
  if (!catalog) return undefined;
  const ids = new Set(catalog.styles.map((style) => style.id));
  const history = character?.tags?.includes("history") || character?.id === "custom_lin";
  if (history && ids.has("ink_chronicle")) return "ink_chronicle";
  if (ids.has("neon_toon")) return "neon_toon";
  return catalog.styles[0]?.id;
}

/** 预览 URL 通用（风格/场景/角色/模板都可走 /api/v5/preview） */
export function previewUrl(
  kind: "styles" | "scenes" | "characters" | "templates",
  id: string,
  styleId?: string,
  size = "1024x576"
): string {
  const params = new URLSearchParams({ size });
  if (styleId) params.set("style", styleId);
  return apiUrl("/v5/preview/" + kind + "/" + enc(id) + "?" + params.toString());
}

export const IS_DIRECT_API = IS_DIRECT;
