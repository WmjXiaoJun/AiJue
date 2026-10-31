// Cinema Studio（3D 电影制片）类型与 API client。
// 后端：backend/app/routers/cinema3d.py（前缀 /api/cinema）；契约见 docs/V7_FRONTEND_PLATFORM.md §3。
import { API_BASE, ApiError, mediaUrl } from "./api";

export type CinemaTab = "overview" | "shots" | "jobs" | "assemble" | "final";

export type ShotSource = "cloud" | "local" | "reuse" | "missing";
export type JobKind = "generate_local" | "generate_cloud" | "assemble";
export type JobStatus = "queued" | "running" | "done" | "failed" | "cancelled";

export interface CinemaBrief {
  subject: string;
  camera?: string;
  env?: string;
  ambience?: string;
  chars?: string[];
  local_setting_key?: string;
  style?: string;
}

export interface CinemaShot {
  id: string;
  index: number;
  duration: number;
  line: string;
  kind: "broll" | "dialogue" | "card";
  source: ShotSource;
  valid: boolean;
  url: string;
  sheet_url: string;
  scene?: string;
  edit_start: number;
  meta: Record<string, unknown>;
  /** 复用镜头的母素材 id（后端 /shots 提供） */
  reuse_src?: string | null;
  brief: CinemaBrief;
}

export interface CinemaJob {
  id: string;
  kind: JobKind;
  status: JobStatus;
  progress: number;
  message: string;
  started_at?: string | null;
  finished_at?: string | null;
  shots?: string[];
  logs: string[];
  result?: Record<string, unknown> | null;
}

export interface CinemaProject {
  id: string;
  name: string;
  episode_id: string;
  render_url?: string | null;
  status?: string;
}

export interface CinemaFinal {
  exists: boolean;
  duration: number;
  size: number;
  mtime?: string | null;
  url: string;
}

export interface CinemaOverview {
  project: CinemaProject;
  template: { id: string; label: string; style: string; target_duration: number };
  shots: { total: number; cloud: number; local: number; reuse: number; missing: number };
  final: CinemaFinal;
  jobs: { active: number; recent: CinemaJob[] };
}

export interface CinemaEpisode {
  total_duration: number;
  render_url?: string | null;
}

export interface CinemaShotsResponse {
  episode: CinemaEpisode;
  shots: CinemaShot[];
}

export interface CinemaQc {
  duration?: number;
  duration_error?: number;
  width?: number;
  height?: number;
  fps?: number;
  blackdetect?: number;
  loudness?: { integrated_lufs?: number; true_peak_dbtp?: number; lra?: number; input_i?: number };
  subtitles?: number;
}

export interface CinemaReport {
  exists: boolean;
  final?: CinemaFinal;
  report?: Record<string, unknown> | null;
  qc?: CinemaQc | null;
  frames?: string[];
  contact_sheet_url?: string;
}

export interface CinemaWorkers {
  comfyui: boolean;
  comfyui_queue: number;
  metaso_key: boolean;
}

export interface GenerateRequest {
  provider: "local" | "metaso";
  shots: string[];
  force?: boolean;
  resolution?: string;
}

export interface AssembleRequest {
  ambience_db?: number;
  unify_look?: boolean;
  shot_grade?: boolean;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), 30000);
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
      /* 非 JSON 错误体 */
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const cinemaApi = {
  overview: (): Promise<CinemaOverview> => request("/cinema/overview"),
  shots: (): Promise<CinemaShotsResponse> => request("/cinema/shots"),
  patchShot: (sid: string, patch: Partial<CinemaBrief>): Promise<{ brief: CinemaBrief }> =>
    request(`/cinema/shots/${encodeURIComponent(sid)}`, { method: "PATCH", body: JSON.stringify(patch) }),
  generate: (body: GenerateRequest): Promise<{ job_id: string }> =>
    request("/cinema/generate", { method: "POST", body: JSON.stringify(body) }),
  assemble: (body: AssembleRequest): Promise<{ job_id: string }> =>
    request("/cinema/assemble", { method: "POST", body: JSON.stringify(body) }),
  jobs: (): Promise<{ jobs: CinemaJob[] }> => request("/cinema/jobs"),
  job: (id: string): Promise<CinemaJob> => request(`/cinema/jobs/${encodeURIComponent(id)}`),
  cancelJob: (id: string): Promise<CinemaJob> =>
    request(`/cinema/jobs/${encodeURIComponent(id)}/cancel`, { method: "POST" }),
  report: (): Promise<CinemaReport> => request("/cinema/report"),
  workers: (): Promise<CinemaWorkers> => request("/cinema/workers"),
};

// ---------------- 展示辅助 ----------------
export const SOURCE_LABEL: Record<ShotSource, string> = {
  cloud: "云端 H3",
  local: "本地 H3",
  reuse: "素材复用",
  missing: "缺失",
};

export const SOURCE_HINT: Record<ShotSource, string> = {
  cloud: "MetaSo 云端 MiniMax-H3 生成（1344×768 电影级 3D）",
  local: "本地 ComfyUI MiniMax-H3 生成（768×432，4step turbo）",
  reuse: "从已审母素材剪出的纪录片式 B-roll",
  missing: "还没有对应素材，剪辑时会缺失/顺接",
};

export const JOB_KIND_LABEL: Record<JobKind, string> = {
  generate_local: "本地 H3 生成",
  generate_cloud: "云端 H3 生成",
  assemble: "剪辑合成",
};

export const JOB_STATUS_LABEL: Record<JobStatus, string> = {
  queued: "排队中",
  running: "运行中",
  done: "已完成",
  failed: "失败",
  cancelled: "已取消",
};

export function fmtDuration(sec: number | null | undefined): string {
  const s = Math.max(0, Math.round(Number(sec) || 0));
  const m = Math.floor(s / 60);
  const r = s % 60;
  return `${m}:${String(r).padStart(2, "0")}`;
}

export function fmtClock(sec: number | null | undefined): string {
  return fmtDuration(sec) + " / 5:25";
}

export function fmtSize(bytes: number | null | undefined): string {
  const b = Number(bytes) || 0;
  if (!b) return "—";
  if (b >= 1e9) return (b / 1e9).toFixed(2) + " GB";
  if (b >= 1e6) return (b / 1e6).toFixed(1) + " MB";
  return (b / 1e3).toFixed(0) + " KB";
}

export function shotMediaUrl(url: string): string {
  return mediaUrl(url);
}

export function fmtTimecode(sec: number): string {
  const s = Math.max(0, sec || 0);
  const m = Math.floor(s / 60);
  const r = Math.floor(s % 60);
  const f = Math.floor((s - Math.floor(s)) * 25);
  return `${String(m).padStart(2, "0")}:${String(r).padStart(2, "0")}:${String(f).padStart(2, "0")}`;
}
