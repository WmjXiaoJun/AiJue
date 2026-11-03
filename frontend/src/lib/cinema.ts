// Cinema Studio（3D 电影制片）类型与 API client。
// 后端：backend/app/routers/cinema3d.py（前缀 /api/cinema）；契约见 docs/V7_FRONTEND_PLATFORM.md §3。
import { API_BASE, ApiError, mediaUrl } from "./api";

export type CinemaTab = "shots" | "jobs" | "assemble";

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

// ---------------- 模板库 · 3D（16 个 V5 模板重做成 30–45s 3D 电影感样片） ----------------
// 契约：GET /cinema/templates、GET /cinema/templates/{id}、
//       POST /cinema/templates/generate、POST /cinema/templates/assemble。
export type CinemaTemplateStatus = "pending" | "generating" | "generated" | "done" | "assemble_failed";

export interface CinemaTemplateClips {
  done: number;
  total: number;
}

export interface CinemaTemplateFinal {
  exists: boolean;
  url: string;
  duration: number;
  size: number;
  mtime?: string | null;
}

export interface CinemaTemplate {
  id: string;
  label: string;
  style: string;
  status: CinemaTemplateStatus;
  /** 0–100（后端按镜头数/时长估算；缺失时前端按 clips 兜底） */
  progress: number;
  clips: CinemaTemplateClips;
  final: CinemaTemplateFinal;
  poster_url: string;
  report_url: string;
  error: string;
}

export interface CinemaTemplatesSummary {
  total: number;
  done: number;
  generating: number;
  /** 镜头已全部生成、等待剪辑合成 */
  generated: number;
  pending: number;
  failed: number;
  clips_done: number;
  clips_total: number;
}

export interface CinemaTemplatesResponse {
  templates: CinemaTemplate[];
  summary: CinemaTemplatesSummary;
  jobs: { active: number };
}

export interface CinemaTemplateShot {
  id: string;
  kind: string;
  scene: string;
  duration: number;
  status: string;
  url: string;
  valid: boolean;
}

export interface CinemaTemplateDetail extends CinemaTemplate {
  shots: CinemaTemplateShot[];
}

export interface CinemaTemplatesGenerateRequest {
  templates: string[] | "all";
  force?: boolean;
}

export interface CinemaTemplatesAssembleRequest {
  templates: string[] | "all";
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

/** V26：发布包文件 */
export interface PublishPackageFile {
  name: string;
  size: number;
}

/** V26：发布包导出结果（POST/GET /api/cinema/templates/{tid}/publish-package） */
export interface PublishPackageResponse {
  ok: boolean;
  template_id: string;
  package_url: string;
  size_bytes: number;
  files: PublishPackageFile[];
  cached?: boolean;
  platform?: string;
  /** 部分后端实现会在响应里带 metadata（用于「复制元数据」） */
  metadata?: Record<string, unknown>;
  /** V27：竖版产物信息（vertical=true 时返回） */
  vertical?: { file?: string; cover?: string; resolution?: string; mode?: string } | null;
  warnings?: string[];
}

export type PublishPlatform = "generic" | "youtube" | "tiktok" | "instagram";

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
  /** 16 模板 3D 制作总表（后端 404 时由调用方降级处理；此处已做字段归一化） */
  templates: async (): Promise<CinemaTemplatesResponse> =>
    normalizeTemplatesResponse(await request<unknown>("/cinema/templates")),
  /** 单模板详情：总表字段 + shots[]（逐镜 3D 生成状态） */
  templateDetail: async (id: string): Promise<CinemaTemplateDetail> =>
    normalizeTemplateDetail(await request<unknown>(`/cinema/templates/${encodeURIComponent(id)}`)),
  /** 排入 3D 生成队列；templates 传 "all" 或模板 id 数组 */
  generateTemplates: (body: CinemaTemplatesGenerateRequest): Promise<{ job_id: string }> =>
    request("/cinema/templates/generate", { method: "POST", body: JSON.stringify(body) }),
  /** 排入 3D 合成队列（ffmpeg 剪辑成片）；templates 传 "all" 或模板 id 数组 */
  assembleTemplates: (body: CinemaTemplatesAssembleRequest): Promise<{ job_id: string }> =>
    request("/cinema/templates/assemble", { method: "POST", body: JSON.stringify(body) }),
  /** V26：构建/复用发布包 zip（视频/封面/字幕/元数据/README） */
  publishPackage: (
    templateId: string,
    body: { platform?: PublishPlatform; force?: boolean; vertical?: boolean; vertical_mode?: "blur_pad" | "crop" } = {},
  ): Promise<PublishPackageResponse> =>
    request(`/cinema/templates/${encodeURIComponent(templateId)}/publish-package`, {
      method: "POST",
      body: JSON.stringify({ platform: "generic", force: false, vertical: false, ...body }),
    }),
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

// ---------------- 模板库 · 3D 归一化与展示辅助 ----------------
// 后端契约先行、实现可能滞后：这里对字段做宽容归一化（缺省/别名/字符串数字），
// 保证 404、字段缺失、字段改名时前端不崩，也不伪造任何进度。
type LooseRecord = Record<string, unknown>;

function looseRecord(value: unknown): LooseRecord | null {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as LooseRecord) : null;
}

function looseNumber(value: unknown, fallback = 0): number {
  const num = typeof value === "number" ? value : typeof value === "string" && value.trim() !== "" ? Number(value) : Number.NaN;
  return Number.isFinite(num) ? num : fallback;
}

function looseString(value: unknown, fallback = ""): string {
  return typeof value === "string" && value.trim() !== "" ? value : fallback;
}

const TEMPLATE_STATUSES: CinemaTemplateStatus[] = ["pending", "generating", "generated", "done", "assemble_failed"];

const TEMPLATE_STATUS_ALIASES: Record<string, CinemaTemplateStatus> = {
  queued: "pending",
  waiting: "pending",
  idle: "pending",
  todo: "pending",
  empty: "pending",
  new: "pending",
  running: "generating",
  rendering: "generating",
  in_progress: "generating",
  ready: "generated",
  clips_ready: "generated",
  ok: "generated",
  complete: "done",
  completed: "done",
  success: "done",
  assembled: "done",
  failed: "assemble_failed",
  error: "assemble_failed",
};

export function normalizeTemplateStatus(raw: unknown): CinemaTemplateStatus {
  const key = typeof raw === "string" ? raw.trim().toLowerCase() : "";
  if ((TEMPLATE_STATUSES as string[]).includes(key)) return key as CinemaTemplateStatus;
  return TEMPLATE_STATUS_ALIASES[key] ?? "pending";
}

function normalizeTemplateFinal(raw: unknown): CinemaTemplateFinal {
  const rec = looseRecord(raw) ?? {};
  const url = looseString(rec.url) || looseString(rec.final_url) || looseString(rec.path);
  return {
    exists: typeof rec.exists === "boolean" ? rec.exists : Boolean(url),
    url,
    duration: Math.max(0, looseNumber(rec.duration ?? rec.duration_sec, 0)),
    size: Math.max(0, looseNumber(rec.size ?? rec.size_bytes, 0)),
    mtime: looseString(rec.mtime) || null,
  };
}

/** 单模板归一化；fallback 用于后端只返回部分字段时补 id/label/style */
export function normalizeCinemaTemplate(
  raw: unknown,
  fallback?: { id?: string; label?: string; style?: string },
): CinemaTemplate {
  const rec = looseRecord(raw) ?? {};
  const clipsRaw = looseRecord(rec.clips);
  const clipsDone = Math.max(0, looseNumber(clipsRaw?.done ?? rec.clips_done ?? rec.done, 0));
  const clipsTotal = Math.max(0, looseNumber(clipsRaw?.total ?? rec.clips_total ?? rec.total ?? rec.shots_total, 0));
  const final = normalizeTemplateFinal(rec.final);
  const status = normalizeTemplateStatus(rec.status);
  const rawProgress = looseNumber(rec.progress, Number.NaN);
  const progress = Math.max(
    0,
    Math.min(
      100,
      Math.round(
        Number.isFinite(rawProgress)
          ? rawProgress
          : clipsTotal > 0
            ? (clipsDone / clipsTotal) * 100
            : status === "done"
              ? 100
              : 0,
      ),
    ),
  );
  return {
    id: looseString(rec.id) || fallback?.id || "",
    label: looseString(rec.label) || fallback?.label || "",
    style: looseString(rec.style) || fallback?.style || "",
    status,
    progress,
    clips: { done: clipsDone, total: clipsTotal },
    final,
    poster_url: looseString(rec.poster_url) || looseString(rec.poster) || "",
    report_url: looseString(rec.report_url) || looseString(rec.report) || "",
    error: looseString(rec.error) || "",
  };
}

export function summarizeTemplates(templates: CinemaTemplate[]): CinemaTemplatesSummary {
  const done = templates.filter((item) => item.status === "done").length;
  const generating = templates.filter((item) => item.status === "generating").length;
  const generated = templates.filter((item) => item.status === "generated").length;
  const failed = templates.filter((item) => item.status === "assemble_failed").length;
  return {
    total: templates.length,
    done,
    generating,
    generated,
    failed,
    pending: Math.max(0, templates.length - done - generating - generated - failed),
    clips_done: templates.reduce((sum, item) => sum + item.clips.done, 0),
    clips_total: templates.reduce((sum, item) => sum + item.clips.total, 0),
  };
}

export function normalizeTemplatesResponse(raw: unknown): CinemaTemplatesResponse {
  const rec = looseRecord(raw);
  let listRaw: unknown = rec?.templates ?? raw;
  if (!Array.isArray(listRaw)) {
    const map = looseRecord(listRaw);
    listRaw = map
      ? Object.entries(map).map(([id, item]) => ({ id, ...(looseRecord(item) ?? {}) }))
      : [];
  }
  const templates = (listRaw as unknown[]).map((item) => normalizeCinemaTemplate(item));
  const summaryRaw = looseRecord(rec?.summary) ?? {};
  const computed = summarizeTemplates(templates);
  const jobsRaw = looseRecord(rec?.jobs) ?? {};
  return {
    templates,
    summary: {
      total: Math.max(0, looseNumber(summaryRaw.total, templates.length || TEMPLATE_3D_SEEDS.length)),
      done: Math.max(0, looseNumber(summaryRaw.done, computed.done)),
      generating: Math.max(0, looseNumber(summaryRaw.generating, computed.generating)),
      generated: Math.max(0, looseNumber(summaryRaw.generated, computed.generated)),
      pending: Math.max(0, looseNumber(summaryRaw.pending, computed.pending)),
      failed: Math.max(0, looseNumber(summaryRaw.assemble_failed ?? summaryRaw.failed, computed.failed)),
      clips_done: Math.max(0, looseNumber(summaryRaw.clips_done, computed.clips_done)),
      clips_total: Math.max(0, looseNumber(summaryRaw.clips_total, computed.clips_total)),
    },
    jobs: { active: Math.max(0, looseNumber(jobsRaw.active, 0)) },
  };
}

export function normalizeTemplateDetail(raw: unknown): CinemaTemplateDetail {
  const rec = looseRecord(raw) ?? {};
  const template = normalizeCinemaTemplate(rec);
  const shotsRaw = Array.isArray(rec.shots) ? rec.shots : [];
  const shots = shotsRaw.map((item): CinemaTemplateShot => {
    const shot = looseRecord(item) ?? {};
    const url = looseString(shot.url) || looseString(shot.video_url);
    const valid = typeof shot.valid === "boolean" ? shot.valid : Boolean(url);
    return {
      id: looseString(shot.id),
      kind: looseString(shot.kind),
      scene: looseString(shot.scene),
      duration: Math.max(0, looseNumber(shot.duration, 0)),
      status: looseString(shot.status) || (valid ? "done" : "pending"),
      url,
      valid,
    };
  });
  return { ...template, shots };
}

export interface CinemaTemplateSeed {
  id: string;
  label: string;
  en: string;
  style: string;
}

/** 原 16 个 V5 模板的稳定顺序（与 pipeline/templates3d.py 的 TEMPLATES 一致）。
 *  只作为「接口未就绪/缺项」时的占位顺序与 label 兜底；后端新增模板（如 real_*）由
 *  normalizeTemplatesResponse 动态返回、页面按 summary.total / templates.length 渲染，
 *  这里不预置新增模板、也不伪造任何进度数据。 */
export const TEMPLATE_3D_SEEDS: CinemaTemplateSeed[] = [
  { id: "tonight_show", label: "美式深夜秀", en: "TONIGHT SHOW", style: "neon_toon" },
  { id: "classics", label: "典籍", en: "CLASSICS", style: "woodblock_classic" },
  { id: "history_talk", label: "历史夜话", en: "HISTORY TALK", style: "ink_chronicle" },
  { id: "masked_singer", label: "蒙面歌手", en: "MASKED SINGER", style: "hologram_stage" },
  { id: "roundtable", label: "圆桌派", en: "ROUNDTABLE", style: "oil_salon" },
  { id: "national_treasure", label: "国宝", en: "NATIONAL TREASURE", style: "museum_gold" },
  { id: "hot_ones", label: "热辣对话", en: "HOT ONES", style: "film_interview" },
  { id: "mystery", label: "悬疑现场", en: "MYSTERY", style: "noir_rain" },
  { id: "qipashuo", label: "奇葩说", en: "QIPA SHUO", style: "silk_arena" },
  { id: "shark_tank", label: "创投路演", en: "SHARK TANK", style: "blueprint_lab" },
  { id: "slow_life", label: "慢生活", en: "SLOW LIFE", style: "clay_diorama" },
  { id: "snl_sketch", label: "周六夜现场", en: "SNL SKETCH", style: "chalk_theatre" },
  { id: "standup", label: "脱口秀", en: "STAND-UP", style: "vhs_late_show" },
  { id: "taskmaster", label: "任务大师", en: "TASKMASTER", style: "candy_collage" },
  { id: "traitors", label: "叛徒圆桌", en: "TRAITORS", style: "baroque_candle" },
  { id: "history_mystery", label: "历史悬案", en: "HISTORY MYSTERY", style: "parchment_case" },
];

export const TEMPLATE_3D_TOTAL = TEMPLATE_3D_SEEDS.length;

/** V17 新增「写实人物场景」模板（real_street / real_cafe / real_kitchen …）识别：
 *  以 id 前缀 real_ 为准，页面据此展示「写实」徽章与分类筛选。 */
export function isRealTemplate(id: string | null | undefined): boolean {
  return /^real_/i.test(String(id || "").trim());
}

export const TEMPLATE_STATUS_LABEL: Record<CinemaTemplateStatus, string> = {
  pending: "待生成",
  generating: "生成中",
  generated: "镜头就绪",
  done: "已完成",
  assemble_failed: "合成失败",
};

/** 状态点样式（复用 cinema.css 的 .cm-status-dot） */
export function templateStatusDot(status: CinemaTemplateStatus): string {
  if (status === "generating") return "is-running";
  if (status === "done" || status === "generated") return "is-done";
  if (status === "assemble_failed") return "is-failed";
  return "";
}

/** 状态胶囊样式（复用 cinema.css 的 .cm-chip 变体） */
export function templateStatusChip(status: CinemaTemplateStatus): string {
  if (status === "generating") return "is-gold";
  if (status === "done" || status === "generated") return "is-jade";
  if (status === "assemble_failed") return "is-red";
  return "";
}

export function templateClipPercent(template: CinemaTemplate | null | undefined): number {
  if (!template) return 0;
  if (template.final.exists) return 100;
  if (template.clips.total > 0) return Math.round((template.clips.done / template.clips.total) * 100);
  return Math.max(0, Math.min(100, Math.round(template.progress)));
}

