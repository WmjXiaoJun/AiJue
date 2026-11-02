// V18 · 角色与声音 / 选角 API（前端契约层，后端 tpl-backend 并行实现）
//   GET  /api/v5/casting?template=<tid> → 模板 cast（role/character/label/persona/voice/portrait_url）
//   GET  /api/v5/voices[?with_preview=1] → 24 preset（+ preview_url）
//   GET/PUT /api/v5/voice/{character}     → 当前音色 / 保存覆盖
//   POST /api/v5/voice/{character}/preview {text?, voice_id?} → {url, cached, engine}
// 404 时：页面走友好提示，casting 用 catalog + 角色合同兜底（不伪造声音）。
import { API_BASE, ApiError } from "./api";
import type { V5CatalogResponse, V5TemplateSummary, V5VoicePreset } from "./v5";
import { mediaSrc, v5api } from "./v5";

export interface CastingVoice {
  voice_id: string;
  label: string;
  provider: string;
  seed?: number;
}

export interface CastingMember {
  role: string;
  character: string;
  label: string;
  persona: string;
  era?: string;
  portrait_url: string;
  voice: CastingVoice;
}

export interface CastingResponse {
  ok: boolean;
  template: string;
  cast: CastingMember[];
  source?: string;
}

export interface VoicePreset extends V5VoicePreset {
  description?: string;
  preview_url?: string;
}

export interface VoiceInfoNormalized {
  character: string;
  voice_id: string;
  seed: number;
  cfg_scale: number;
  instruction: string;
  key: string;
  needs_relock: boolean;
  lockExists: boolean;
  lockCreatedAt?: string | null;
}

export interface VoicePreviewResult {
  url: string;
  cached: boolean;
  engine?: string;
  duration?: number;
}

const ROLE_LABEL: Record<string, string> = {
  host: "主持人",
  guest_a: "嘉宾 A",
  guest_b: "嘉宾 B",
  guest_c: "嘉宾 C",
  narrator: "旁白",
  expert: "专家",
  figure_a: "人物 A",
  figure_b: "人物 B",
  contestant: "选手",
  judge: "评委",
};

export function roleLabel(role: string | undefined): string {
  if (!role) return "角色";
  return ROLE_LABEL[role] || role;
}

async function request<T>(path: string, init?: RequestInit, timeoutMs = 60000): Promise<T> {
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
      /* 非 JSON */
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

function rec(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" ? (value as Record<string, unknown>) : {};
}

function str(value: unknown, fallback = ""): string {
  return typeof value === "string" && value.trim() !== "" ? value : fallback;
}

function num(value: unknown, fallback = 0): number {
  const n = Number(value);
  return Number.isFinite(n) ? n : fallback;
}

export function normalizePreset(raw: unknown): VoicePreset {
  const r = rec(raw);
  const tags = Array.isArray(r.tags) ? r.tags.map((tag) => String(tag)) : [];
  return {
    voice_id: str(r.voice_id) || str(r.id),
    label: str(r.label) || str(r.name) || str(r.voice_id) || str(r.id) || "—",
    seed: r.seed === undefined ? undefined : num(r.seed),
    cfg_scale: r.cfg_scale === undefined ? undefined : num(r.cfg_scale),
    tags,
    description: str(r.description) || str(r.instruction) || str(r.reason),
    preview_url: str(r.preview_url),
  };
}

export function normalizeCasting(raw: unknown, templateId: string): CastingResponse {
  const r = rec(raw);
  const listRaw = Array.isArray(r.cast) ? r.cast : Array.isArray(raw) ? raw : [];
  const cast = listRaw.map((item): CastingMember => {
    const m = rec(item);
    const voice = rec(m.voice);
    const portrait = str(m.portrait_url) || str(rec(m.art).portrait);
    return {
      role: str(m.role),
      character: str(m.character) || str(m.id),
      label: str(m.label) || str(m.name) || str(m.character) || "—",
      persona: str(m.persona) || str(m.description),
      era: str(m.era) || undefined,
      portrait_url: portrait ? mediaSrc(portrait.startsWith("/") ? portrait : "/" + portrait) : "",
      voice: {
        voice_id: str(voice.voice_id) || str(m.voice_id),
        label: str(voice.label) || str(voice.voice_id) || str(m.voice_id) || "—",
        provider: str(voice.provider) || "breeze",
        seed: voice.seed === undefined ? undefined : num(voice.seed),
      },
    };
  });
  return { ok: r.ok !== false, template: str(r.template) || templateId, cast, source: str(r.source, "api") };
}

export function normalizeVoiceInfo(raw: unknown, character: string): VoiceInfoNormalized {
  const r = rec(raw);
  const lock = rec(r.lock);
  return {
    character: str(r.character) || character,
    voice_id: str(r.voice_id),
    seed: num(r.seed),
    cfg_scale: num(r.cfg_scale),
    instruction: str(r.instruction),
    key: str(r.key),
    needs_relock: Boolean(r.needs_relock),
    lockExists: Boolean(lock.exists),
    lockCreatedAt: str(lock.created_at) || null,
  };
}

export function normalizePreview(raw: unknown, character: string): VoicePreviewResult {
  const r = rec(raw);
  const rawUrl = str(r.url) || str(r.out) || str(r.preview_url);
  const fallback = `/media/v5/voice/${encodeURIComponent(character)}.wav?t=${Date.now()}`;
  return {
    url: mediaSrc(rawUrl || fallback),
    cached: Boolean(r.cached),
    engine: str(r.engine) || undefined,
    duration: r.duration === undefined ? undefined : num(r.duration),
  };
}

export const voiceApi = {
  /** 模板 cast（后端就绪后返回 persona/portrait；404 时调用方走 catalog 兜底） */
  casting: async (templateId: string): Promise<CastingResponse> =>
    normalizeCasting(await request(`/v5/casting?template=${encodeURIComponent(templateId)}`), templateId),

  voices: async (withPreview = true): Promise<VoicePreset[]> => {
    const res = rec(await request(`/v5/voices${withPreview ? "?with_preview=1" : ""}`));
    const list = Array.isArray(res.presets) ? res.presets : Array.isArray(res.voices) ? res.voices : [];
    return list.map((item) => normalizePreset(item)).filter((item) => item.voice_id);
  },

  voiceInfo: async (character: string): Promise<VoiceInfoNormalized> =>
    normalizeVoiceInfo(await request(`/v5/voice/${encodeURIComponent(character)}`), character),

  /** 保存音色覆盖（PUT /v5/voice/{character}，写 custom 角色文件） */
  setVoice: (character: string, body: { voice_id: string; seed?: number; cfg_scale?: number }) =>
    request(`/v5/voice/${encodeURIComponent(character)}`, { method: "PUT", body: JSON.stringify(body) }),

  /** 生成/复用试听 wav；voice_id 为可选扩展（后端支持时按指定音色试听） */
  preview: async (character: string, opts: { text?: string; voiceId?: string } = {}): Promise<VoicePreviewResult> => {
    const body: Record<string, unknown> = {};
    if (opts.text) body.text = opts.text;
    if (opts.voiceId) body.voice_id = opts.voiceId;
    return normalizePreview(
      await request(`/v5/voice/${encodeURIComponent(character)}/preview`, {
        method: "POST",
        body: JSON.stringify(body),
      }, 120000),
      character,
    );
  },
};

/** 后端 casting 未就绪时的兜底：catalog 模板 cast + 角色合同 + 当前 voice 覆盖。 */
export async function fallbackCasting(
  templateId: string,
  catalog: V5CatalogResponse | null,
  presets: VoicePreset[],
): Promise<CastingResponse> {
  const template: V5TemplateSummary | undefined = (catalog?.templates || []).find((item) => item.id === templateId);
  const cast = template?.cast || [];
  const presetLabel = new Map(presets.map((preset) => [preset.voice_id, preset.label || preset.voice_id]));
  const members: CastingMember[] = [];
  for (const member of cast) {
    const characterId = String(member.character || "");
    const summary = (catalog?.characters || []).find((item) => item.id === characterId);
    let persona = "";
    let era = "";
    let portraitUrl = "";
    let voiceId = "";
    let provider = "breeze";
    try {
      const itemRes = rec(await v5api.item("characters", characterId));
      const item = rec(itemRes.item);
      persona = str(item.persona);
      era = str(item.era);
      voiceId = str(rec(item.voice).voice_id);
      provider = str(rec(item.voice).provider, "breeze");
      const art = str(rec(item.art).portrait);
      if (art) portraitUrl = mediaSrc(art.startsWith("/") ? art : "/" + art);
    } catch {
      /* 角色合同读取失败：保留目录摘要 */
    }
    try {
      const info = await voiceApi.voiceInfo(characterId);
      if (info.voice_id) voiceId = info.voice_id;
    } catch {
      /* 声音信息缺失：留空 */
    }
    members.push({
      role: String(member.role || ""),
      character: characterId,
      label: summary?.label || characterId,
      persona,
      era: era || undefined,
      portrait_url: portraitUrl,
      voice: {
        voice_id: voiceId,
        label: presetLabel.get(voiceId) || voiceId || "—",
        provider,
      },
    });
  }
  return { ok: true, template: templateId, cast: members, source: "fallback" };
}

/** 角色页用：读取单个角色的完整合同（persona/era/voice/appearance/art）。 */
export interface CharacterContract {
  id: string;
  label: string;
  era: string;
  persona: string;
  voiceId: string;
  voiceProvider: string;
  tags: string[];
  rolePool: string[];
  appearance: Record<string, unknown>;
  art: { portrait?: string; front?: string; cutout?: string };
  source: string;
}

export async function fetchCharacterContract(
  characterId: string,
  catalog: V5CatalogResponse | null,
): Promise<CharacterContract> {
  const summary = (catalog?.characters || []).find((item) => item.id === characterId);
  try {
    const res = rec(await v5api.item("characters", characterId));
    const item = rec(res.item);
    const art = rec(item.art);
    return {
      id: characterId,
      label: str(item.label) || summary?.label || characterId,
      era: str(item.era),
      persona: str(item.persona),
      voiceId: str(rec(item.voice).voice_id),
      voiceProvider: str(rec(item.voice).provider, "breeze"),
      tags: Array.isArray(item.tags) ? item.tags.map((tag) => String(tag)) : summary?.tags || [],
      rolePool: Array.isArray(item.role_pool)
        ? item.role_pool.map((role) => String(role))
        : summary?.role_pool || [],
      appearance: rec(item.appearance),
      art: {
        portrait: str(art.portrait) || undefined,
        front: str(art.front) || undefined,
        cutout: str(art.cutout) || undefined,
      },
      source: str(res.source) || "contract",
    };
  } catch {
    return {
      id: characterId,
      label: summary?.label || characterId,
      era: "",
      persona: "",
      voiceId: "",
      voiceProvider: summary?.voice_provider || "breeze",
      tags: summary?.tags || [],
      rolePool: summary?.role_pool || [],
      appearance: {},
      art: {},
      source: "catalog",
    };
  }
}
