// V54 · 5 分钟长版（Longform）API 客户端
// 后端契约（Lead 提供，接口按此写死；就绪前 GET 404 → UI 显示「未接入」）：
//   POST /api/v5/longform/run    {template?: string, all?: boolean, duration?: number}
//   GET  /api/v5/longform/status → { ok, running?, jobs?/items? }
// 产物沿用 /media/templates_*（与成片库同源）。
import { API_BASE, ApiError } from "./api";

/** 5 分钟长版的目标时长（秒） */
export const LONGFORM_DURATION = 300;

export interface LongformJob {
  id: string;
  template: string;
  status: string;
  /** 0–100 */
  progress: number;
  message: string;
  duration: number;
  final_url: string;
  started_at: string;
  finished_at: string;
}

export interface LongformState {
  running: LongformJob | null;
  jobs: LongformJob[];
}

export interface LongformRunRequest {
  template?: string;
  all?: boolean;
  duration?: number;
}

export interface LongformRunResponse {
  ok: boolean;
  job_id: string;
  message: string;
}

const rec = (value: unknown): Record<string, unknown> =>
  value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
const arr = (value: unknown): unknown[] => (Array.isArray(value) ? value : []);
const str = (value: unknown, fallback = ""): string =>
  typeof value === "string" ? value : value === null || value === undefined ? fallback : String(value);
const num = (value: unknown, fallback = 0): number => {
  const n = Number(value);
  return Number.isFinite(n) ? n : fallback;
};
const bool = (value: unknown, fallback = false): boolean => (typeof value === "boolean" ? value : fallback);
const enc = encodeURIComponent;

async function request<T>(path: string, init?: RequestInit, timeoutMs = 20000): Promise<T> {
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
      /* 非 JSON 错误体 */
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

/** outputs/xxx → /media/xxx（后端返回的 out 是磁盘路径） */
function mediaUrlFromPath(path: string): string {
  const norm = path.replace(/\\/g, "/");
  const idx = norm.indexOf("/outputs/");
  if (idx < 0) return "";
  return "/media/" + norm.slice(idx + "/outputs/".length);
}

/** 归一化（后端字段未最终定稿，尽量兼容 running / jobs / items / done-total 等写法） */
export function normalizeLongformJob(raw: unknown): LongformJob {
  const item = rec(raw);
  const logs = arr(item.log_tail).map((line) => str(line));
  const progressRaw = rec(item.progress);
  const done = num(progressRaw.done ?? item.done, 0);
  const total = num(progressRaw.total ?? item.total, 0);
  const percent = item.percent ?? item.pct;
  const progress =
    percent !== undefined && percent !== null
      ? Math.max(0, Math.min(100, num(percent)))
      : total > 0
        ? Math.max(0, Math.min(100, Math.round((done / total) * 100)))
        : Math.max(0, Math.min(100, num(item.progress)));
  return {
    id: str(item.job_id || item.id),
    template: str(item.template || item.template_id || item.mode),
    status: str(item.status, "queued").toLowerCase(),
    progress,
    message: str(item.message || item.stage || item.error) || (logs.length ? logs[logs.length - 1] : ""),
    duration: num(item.duration ?? item.target_duration, 0),
    final_url: str(item.final_url || item.url) || mediaUrlFromPath(str(item.out)),
    started_at: str(item.started_at || item.started || item.created),
    finished_at: str(item.finished_at || item.ended),
  };
}

export function normalizeLongformState(raw: unknown): LongformState {
  const item = rec(raw);
  // 形态 A：{ running, jobs } / { items }
  if (item.running || Array.isArray(item.jobs)) {
    const jobsRaw = arr(item.jobs).length ? arr(item.jobs) : arr(item.items);
    const jobs = jobsRaw.map(normalizeLongformJob);
    const runningRaw = item.running ?? item.current ?? null;
    const running = runningRaw && typeof runningRaw === "object" ? normalizeLongformJob(runningRaw) : null;
    return { running, jobs };
  }
  // 形态 B（当前后端）：单个 job 对象 {job_id,status,templates[],items[],log_tail[],duration,mode}
  if (item.job_id || item.status) {
    const job = normalizeLongformJob(item);
    const templates = arr(item.templates).map((t) => str(t));
    const rowsRaw = arr(item.items);
    const progress = templates.length
      ? Math.max(0, Math.min(100, Math.round((rowsRaw.length / templates.length) * 100)))
      : job.status === "ok"
        ? 100
        : 0;
    const running = isLongformActive(job) ? { ...job, progress: Math.max(2, progress) } : null;
    const rows: LongformJob[] = rowsRaw.length
      ? rowsRaw.map((entry) => {
          const row = rec(entry);
          const ok = bool(row.ok, true);
          const template = str(row.template);
          return {
            id: job.id + ":" + template,
            template,
            status: ok ? "ok" : "failed",
            progress: ok ? 100 : 0,
            message: ok ? "" : str(row.error || row.message),
            duration: num(row.duration ?? row.target, 0),
            final_url: mediaUrlFromPath(str(row.out)),
            started_at: job.started_at,
            finished_at: job.finished_at,
          };
        })
      : [{ ...job, progress: job.status === "ok" ? 100 : progress }];
    return { running, jobs: rows };
  }
  return { running: null, jobs: [] };
}

export function isLongformActive(job: LongformJob | null | undefined): boolean {
  const status = String(job?.status || "").toLowerCase();
  return status === "queued" || status === "running" || status === "starting";
}

export const longformApi = {
  status: async (): Promise<LongformState> => normalizeLongformState(await request("/v5/longform/status")),

  run: async (body: LongformRunRequest): Promise<LongformRunResponse> => {
    const raw = await request<unknown>(
      "/v5/longform/run",
      { method: "POST", body: JSON.stringify({ ...body, duration: body.duration || LONGFORM_DURATION }) },
      60000,
    );
    const item = rec(raw);
    return { ok: bool(item.ok, true), job_id: str(item.job_id), message: str(item.message) };
  },
};

/** 404/405/501 → 长版接口不可用（接口已上线；仍 404 多为资源不存在或反代未转发） */
export function isLongformNotReady(err: unknown): boolean {
  return err instanceof ApiError && (err.status === 404 || err.status === 405 || err.status === 501);
}
