// V18 · 制片控制台（#/pipeline）API：pipeline job 编排 + 轮询 + 取消。
// 后端契约（docs/V18_UI_PIPELINE_AND_VOICE.md §C.4）：
//   POST /api/pipeline/run {template_id, reuse_shots?} → {job_id, template_id, stages}
//   GET  /api/pipeline/run/{job_id} → {status, stage, stage_index, progress:{done,total}, log_tail[], elapsed_sec, final_url, error}
//   POST /api/pipeline/run/{job_id}/cancel
//   GET  /api/pipeline/runs → 最近任务
import { API_BASE, ApiError } from "./api";

export type PipelineStatus = "queued" | "running" | "done" | "failed" | "canceled";

export type PipelineStage =
  | "prepare"
  | "generate"
  | "lipsync"
  | "assemble"
  | "poster"
  | "done"
  | "canceled"
  | "failed"
  | string;

export interface PipelineRun {
  id: string;
  template_id: string;
  status: PipelineStatus;
  stage: PipelineStage;
  stage_index: number;
  progress: { done: number; total: number };
  log_tail: string[];
  elapsed_sec: number;
  final_url: string;
  error: string;
}

export interface PipelineRunCreateResponse {
  job_id: string;
  template_id: string;
  stages?: string[];
}

export const PIPELINE_STAGE_LABEL: Record<string, string> = {
  prepare: "准备素材",
  generate: "生成 3D 镜头",
  lipsync: "口型 / TTS",
  assemble: "剪辑合成",
  poster: "生成海报",
  done: "完成",
  canceled: "已取消",
  failed: "失败",
};

async function request<T>(path: string, init?: RequestInit, timeoutMs = 30000): Promise<T> {
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

function normalizeRun(raw: unknown): PipelineRun {
  const rec = (raw && typeof raw === "object" ? raw : {}) as Record<string, unknown>;
  const progress = (rec.progress && typeof rec.progress === "object" ? rec.progress : {}) as Record<string, unknown>;
  const rawLog = rec.log_tail;
  const log = Array.isArray(rawLog) ? rawLog.map((line) => String(line)) : typeof rawLog === "string" ? rawLog.split("\n") : [];
  const status = String(rec.status || "queued").toLowerCase();
  const statuses: PipelineStatus[] = ["queued", "running", "done", "failed", "canceled"];
  return {
    id: String(rec.id || rec.job_id || ""),
    template_id: String(rec.template_id || rec.template || ""),
    status: (statuses as string[]).includes(status) ? (status as PipelineStatus) : "running",
    stage: String(rec.stage || ""),
    stage_index: Number(rec.stage_index) || 0,
    progress: {
      done: Math.max(0, Number(progress.done) || 0),
      total: Math.max(0, Number(progress.total) || 0),
    },
    log_tail: log.slice(-120),
    elapsed_sec: Math.max(0, Number(rec.elapsed_sec) || 0),
    final_url: String(rec.final_url || ""),
    error: String(rec.error || ""),
  };
}

export const pipelineApi = {
  run: (templateId: string, reuseShots: boolean): Promise<PipelineRunCreateResponse> =>
    request("/pipeline/run", {
      method: "POST",
      body: JSON.stringify({ template_id: templateId, reuse_shots: Boolean(reuseShots) }),
    }),
  runStatus: async (jobId: string): Promise<PipelineRun> =>
    normalizeRun(await request(`/pipeline/run/${encodeURIComponent(jobId)}`, undefined, 15000)),
  cancel: (jobId: string): Promise<unknown> =>
    request(`/pipeline/run/${encodeURIComponent(jobId)}/cancel`, { method: "POST" }),
  runs: async (): Promise<PipelineRun[]> => {
    const res = (await request<unknown>("/pipeline/runs")) as Record<string, unknown> | unknown[];
    const list = Array.isArray(res) ? res : Array.isArray((res as Record<string, unknown>)?.runs) ? ((res as Record<string, unknown>).runs as unknown[]) : [];
    return list.map((item) => normalizeRun(item));
  },
};

/** 任务是否还在跑（queued/running） */
export function isRunActive(run: PipelineRun | null | undefined): boolean {
  return Boolean(run && (run.status === "queued" || run.status === "running"));
}

/** 404/405 → 后端接口尚未就绪（友好提示用） */
export function isPipelineNotReady(err: unknown): boolean {
  return err instanceof ApiError && (err.status === 404 || err.status === 405 || err.status === 501);
}

export function pipelineRunPercent(run: PipelineRun | null | undefined): number {
  if (!run) return 0;
  if (run.status === "done") return 100;
  const { done, total } = run.progress;
  if (total > 0) return Math.max(0, Math.min(100, Math.round((done / total) * 100)));
  if (run.stage === "assemble") return 85;
  if (run.stage === "poster") return 95;
  if (run.stage === "prepare") return 3;
  return 0;
}
