// V49 · 智能体编排（#/agent）API 层：概览 / 工作流读写 / 运行与日志 / 季生产 / 证据链接。
// 后端：backend/app/routers/v6_agent.py（/api/v6/agent/*）+ /media/agent_runs/<run_id>/<file>
// 契约：GET  overview / workflow/{t} / runs / runs/{job} / art / series / season/{sid}/status
//       PUT  workflow/{t}   POST run / edit / season/{sid}/run
import { API_BASE, ApiError, mediaUrl } from "./api";

/* ------------------------------ 类型 ------------------------------ */

export type AgentKind = "base" | "custom";

export interface AgentRole {
  id: string;
  name: string;
  emoji?: string;
  desc?: string;
  tools?: string[];
}

/** 工作流阶段（contract/v6/workflows/<template>.json 的 stages[]） */
export interface AgentStage {
  id: string;
  agent: string;
  tool: string;
  enabled: boolean;
  params: Record<string, unknown>;
  desc: string;
  depends: string[];
  optional: boolean;
}

export interface AgentCastMember {
  role: string;
  character: string;
  name?: string;
}

export interface AgentTemplate {
  id: string;
  label: string;
  style: string;
  /** 2D 子风格（2D 模板才有） */
  render_style: string;
  /** 节目类型：2d | 3d | realistic（缺失时由 id 推导） */
  render_type: string;
  /** V49：生产栈 v5（16 内建综艺）/ shorts（custom_tpl_* 短片 HQ） */
  stack: string;
  kind: AgentKind;
  duration: number;
  cast: AgentCastMember[];
  /** 是否已有成片；概览未提供时为 null（由前端用 series/cinema 兜底） */
  ready: boolean | null;
  series: string[];
  workflow_file: string;
  stages: AgentStage[];
}

export interface AgentSeriesMember {
  template: string;
  label: string;
  ready: boolean;
  final_url: string;
  poster_url: string;
  duration: number;
  /** 最近 run 的 id（后端 last_run.run_id） */
  last_run: string;
  /** V52：最近 run 的状态 / 开始时间（后端 last_run.status|started） */
  last_run_status: string;
  last_run_started: string;
  stages: number;
  status: string;
}

export interface AgentSeries {
  id: string;
  label: string;
  description: string;
  style: string;
  cover_url: string;
  ready_count: number;
  total: number;
  total_duration: number;
  members: AgentSeriesMember[];
}

export interface AgentLogLine {
  t: string;
  stage: string;
  msg: string;
}

export interface AgentRun {
  job_id: string;
  status: string;
  logs: AgentLogLine[];
  result: Record<string, unknown> | null;
  run_dir: string;
  run_id: string;
  error: string;
  created: string;
  started: string;
  ended: string;
}

export interface AgentOverview {
  roles: AgentRole[];
  templates: AgentTemplate[];
  count: number;
  runs: string[];
  series: AgentSeries[];
}

export interface AgentWorkflowFile {
  id: string;
  name: string;
  description: string;
  stack: string;
  variables: Record<string, unknown>;
  stages: AgentStage[];
}

export interface AgentRunRequest {
  template: string;
  duration?: number;
  seed?: number;
  stages?: string[];
  goal?: string;
  dry_run?: boolean;
  force?: boolean;
  /** V52：本次 run 的阶段参数覆盖（如 {"muse":{"apply":true}}） */
  stage_params?: Record<string, unknown>;
}

export interface AgentSeasonRunRequest {
  dry_run?: boolean;
  only_missing?: boolean;
  /** V50：只跑 muse 阶段（口型），不重渲 */
  with_muse?: boolean;
  /** V52：口型写回 shots 并重新合成（后端 muse_apply → stage_params.muse.apply） */
  muse_apply?: boolean;
  /** V52：显式阶段参数覆盖（与 muse_apply 互不影响，后端会合并） */
  stage_params?: Record<string, unknown>;
  stages?: string[];
  goal?: string;
}

export interface AgentSeasonRunResponse {
  ok: boolean;
  /** 后端在「无需生产」时返回 null → 归一成空串 */
  job_id: string;
  episodes: string[];
  /** 例如「全部集数都有成片，无需生产」 */
  message: string;
  dry_run: boolean;
  with_muse: boolean;
}

export interface AgentSeasonStatus {
  series: AgentSeries | null;
  episodes: AgentSeriesMember[];
}

export interface AgentEvidence {
  label: string;
  file: string;
  href: string;
}

/* ------------------------------ 通用工具 ------------------------------ */

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
const boolOrNull = (value: unknown): boolean | null => (typeof value === "boolean" ? value : null);
const enc = encodeURIComponent;

/** 后端 detail 被 request() 序列化成字符串时，尽量还原成对象（400 的 hint/docs） */
function parseJsonish(text: string): Record<string, unknown> {
  try {
    const value = JSON.parse(text);
    return value && typeof value === "object" ? rec(value) : {};
  } catch {
    return {};
  }
}

async function request<T>(path: string, init?: RequestInit, timeoutMs = 20000): Promise<T> {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), timeoutMs);
  // 支持调用方传入自己的 signal（轮询在切模板/卸载时主动 abort），与超时共用同一个 controller
  const external = init?.signal;
  if (external) {
    if (external.aborted) controller.abort();
    else external.addEventListener("abort", () => controller.abort(), { once: true });
  }
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

/* ------------------------------ 归一化 ------------------------------ */

export function normalizeStage(raw: unknown): AgentStage {
  const item = rec(raw);
  return {
    id: str(item.id),
    agent: str(item.agent),
    tool: str(item.tool),
    enabled: bool(item.enabled, true),
    params: rec(item.params),
    desc: str(item.desc),
    depends: arr(item.depends).map((dep) => str(dep)).filter(Boolean),
    optional: bool(item.optional),
  };
}

export function normalizeTemplate(raw: unknown): AgentTemplate {
  const item = rec(raw);
  const id = str(item.id);
  const kindRaw = str(item.kind).toLowerCase();
  const kind: AgentKind = kindRaw === "custom" || kindRaw === "base" ? (kindRaw as AgentKind) : id.startsWith("custom_") ? "custom" : "base";
  return {
    id,
    label: str(item.label) || id,
    style: str(item.style),
    render_style: str(item.render_style),
    render_type: str(item.render_type),
    stack: str(item.stack),
    kind,
    duration: num(item.duration ?? item.target_duration, 0),
    cast: arr(item.cast).map((member) => {
      const c = rec(member);
      return { role: str(c.role), character: str(c.character), name: str(c.name) || undefined };
    }),
    ready: boolOrNull(item.ready),
    series: typeof item.series === "string" ? [item.series].filter(Boolean) : arr(item.series).map((sid) => str(sid)).filter(Boolean),
    workflow_file: str(item.workflow_file),
    stages: arr(item.stages).map(normalizeStage).filter((stage) => stage.id),
  };
}

/** 日志里 last_run 可能是字符串 / {run_id,status,at} / null → 统一成短字符串 */
function lastRunText(value: unknown): string {
  if (typeof value === "string") return value;
  if (value && typeof value === "object") {
    const item = rec(value);
    return str(item.run_id || item.id || item.at || item.started || item.created);
  }
  return "";
}

/** last_run 子字段（对象才有；字符串/空返回 ""） */
function lastRunField(value: unknown, key: string): string {
  if (!value || typeof value !== "object") return "";
  return str(rec(value)[key]);
}

export function normalizeSeriesMember(raw: unknown): AgentSeriesMember {
  const item = rec(raw);
  const ready = bool(item.ready, false) || Boolean(str(item.final_url));
  return {
    template: str(item.template),
    label: str(item.label) || str(item.template),
    ready,
    final_url: str(item.final_url),
    poster_url: str(item.poster_url || item.cover_url || item.poster),
    duration: num(item.duration, 0),
    last_run: lastRunText(item.last_run),
    last_run_status: lastRunField(item.last_run, "status"),
    last_run_started: lastRunField(item.last_run, "started"),
    stages: num(item.stages, 0),
    status: str(item.status),
  };
}

export function normalizeSeries(raw: unknown): AgentSeries {
  const item = rec(raw);
  const members = arr(item.members).map(normalizeSeriesMember);
  return {
    id: str(item.id),
    label: str(item.label) || str(item.id),
    description: str(item.description),
    style: str(item.style),
    cover_url: str(item.cover_url || item.cover),
    ready_count: num(item.ready_count, members.filter((member) => member.ready).length),
    total: num(item.total, members.length) || members.length,
    total_duration: num(item.total_duration, members.reduce((sum, member) => sum + member.duration, 0)),
    members,
  };
}

function normalizeRun(raw: unknown): AgentRun {
  const item = rec(raw);
  const result = item.result && typeof item.result === "object" ? rec(item.result) : null;
  const runDir = str(item.run_dir);
  const runIdFromDir = runDir ? runDir.replace(/\\/g, "/").split("/").filter(Boolean).pop() || "" : "";
  return {
    job_id: str(item.job_id || item.id),
    status: str(item.status, "queued").toLowerCase(),
    logs: arr(item.logs).map((line) => {
      const l = rec(line);
      return { t: str(l.t || l.ts), stage: str(l.stage), msg: str(l.msg || l.message) };
    }),
    result,
    run_dir: runDir,
    run_id: str(result?.run_id) || runIdFromDir,
    error: str(item.error),
    created: str(item.created),
    started: str(item.started),
    ended: str(item.ended),
  };
}

export function normalizeOverview(raw: unknown): AgentOverview {
  const item = rec(raw);
  const templates = arr(item.templates).map(normalizeTemplate).filter((template) => template.id);
  return {
    roles: arr(item.roles).map((role) => {
      const r = rec(role);
      return {
        id: str(r.id),
        name: str(r.name) || str(r.id),
        emoji: str(r.emoji) || undefined,
        desc: str(r.desc),
        tools: arr(r.tools).map((tool) => str(tool)),
      };
    }),
    templates,
    count: num(item.count, templates.length),
    runs: arr(item.runs).map((runId) => str(runId)),
    series: arr(item.series).map(normalizeSeries),
  };
}

export function normalizeWorkflow(raw: unknown): AgentWorkflowFile {
  const item = rec(raw);
  const workflow = rec(item.workflow ?? raw);
  return {
    id: str(workflow.id || item.template),
    name: str(workflow.name) || str(workflow.id),
    description: str(workflow.description),
    stack: str(workflow.stack),
    variables: rec(workflow.variables),
    stages: arr(workflow.stages).map(normalizeStage).filter((stage) => stage.id),
  };
}

/* ------------------------------ API ------------------------------ */

export const agentApi = {
  overview: async (): Promise<AgentOverview> => normalizeOverview(await request("/v6/agent/overview")),

  workflow: async (template: string): Promise<AgentWorkflowFile> =>
    normalizeWorkflow(await request("/v6/agent/workflow/" + enc(template))),

  saveWorkflow: async (template: string, workflow: Record<string, unknown>): Promise<{ ok: boolean; template: string; path: string; stages: string[] }> => {
    const raw = await request<unknown>(
      "/v6/agent/workflow/" + enc(template),
      { method: "PUT", body: JSON.stringify({ workflow }) },
      30000,
    );
    const item = rec(raw);
    return {
      ok: bool(item.ok, true),
      template: str(item.template),
      path: str(item.path),
      stages: arr(item.stages).map((s) => str(s)),
    };
  },

  run: async (body: AgentRunRequest): Promise<{ job_id: string }> => {
    const raw = await request<unknown>("/v6/agent/run", { method: "POST", body: JSON.stringify(body) }, 30000);
    return { job_id: str(rec(raw).job_id) };
  },

  runStatus: async (jobId: string, signal?: AbortSignal): Promise<AgentRun> =>
    normalizeRun(await request("/v6/agent/runs/" + enc(jobId), signal ? { signal } : undefined, 15000)),

  runs: async (): Promise<{ jobs: AgentRun[]; diskRuns: AgentRun[] }> => {
    const item = rec(await request<unknown>("/v6/agent/runs"));
    return {
      jobs: arr(item.jobs).map(normalizeRun),
      diskRuns: arr(item.disk_runs).map((run) => {
        const r = rec(run);
        return normalizeRun({ ...r, job_id: r.run_id, status: r.status });
      }),
    };
  },

  edit: async (body: { template: string; op: string; character?: string; value?: unknown; json?: unknown; stage?: string }): Promise<Record<string, unknown>> =>
    rec(await request<unknown>("/v6/agent/edit", { method: "POST", body: JSON.stringify(body) }, 60000)),

  art: async (): Promise<Record<string, unknown>> => rec(await request<unknown>("/v6/agent/art")),

  /** 季列表：优先 /v6/agent/series；后端 M3 未就绪（404）时回退 /v5/series（同一份系列合同） */
  series: async (): Promise<{ series: AgentSeries[]; source: "v6" | "v5" }> => {
    try {
      const raw = await request<unknown>("/v6/agent/series");
      return { series: arr(rec(raw).series).map(normalizeSeries), source: "v6" };
    } catch (err) {
      if (!isAgentNotReady(err)) throw err;
      const raw = await request<unknown>("/v5/series");
      return { series: arr(rec(raw).series).map(normalizeSeries), source: "v5" };
    }
  },

  seasonStatus: async (sid: string): Promise<AgentSeasonStatus> => {
    const raw = await request<unknown>("/v6/agent/season/" + enc(sid) + "/status");
    const item = rec(raw);
    const series = item.series && typeof item.series === "object" ? normalizeSeries(item.series) : null;
    return { series, episodes: arr(item.episodes).map(normalizeSeriesMember) };
  },

  seasonRun: async (sid: string, body: AgentSeasonRunRequest): Promise<AgentSeasonRunResponse> => {
    const raw = await request<unknown>(
      "/v6/agent/season/" + enc(sid) + "/run",
      { method: "POST", body: JSON.stringify(body) },
      30000,
    );
    const item = rec(raw);
    return {
      ok: bool(item.ok, true),
      job_id: str(item.job_id),
      episodes: arr(item.episodes).map((e) => str(e)),
      message: str(item.message),
      dry_run: bool(item.dry_run, false),
      with_muse: bool(item.with_muse, false),
    };
  },

  /* ---------------------- V49：Muse 口型（MuseTalk） ---------------------- */
  museStatus: async (): Promise<MuseStatus> => normalizeMuseStatus(await request("/v6/muse/status")),

  museSync: async (body: MuseSyncRequest): Promise<{ job_id: string }> => {
    const raw = await request<unknown>("/v6/muse/sync", { method: "POST", body: JSON.stringify(body) }, 30000);
    return { job_id: str(rec(raw).job_id) };
  },

  museJob: async (jobId: string, signal?: AbortSignal): Promise<MuseJob> =>
    normalizeMuseJob(await request("/v6/muse/jobs/" + enc(jobId), signal ? { signal } : undefined, 15000)),

  /* ---------------------- V50：Muse 视频生成 ---------------------- */
  museVideoStatus: async (): Promise<MuseVideoStatus> => normalizeMuseVideoStatus(await request("/v6/muse/video/status")),

  /** provider=stub 且未显式 allow_stub → 后端 400；这里把 detail.hint/docs 提成可读 message */
  museVideo: async (body: MuseVideoRequest): Promise<MuseVideoCreate> => {
    try {
      const raw = await request<unknown>("/v6/muse/video", { method: "POST", body: JSON.stringify(body) }, 120000);
      const item = rec(raw);
      return { job_id: str(item.job_id), provider: str(item.provider), out: str(item.out) };
    } catch (err) {
      if (err instanceof ApiError && err.status === 400) {
        const detail = parseJsonish(err.message);
        const hint = str(detail.hint) || str(rec(detail.state).hint) || "provider=stub 需要显式 allow_stub=true";
        const docs = str(detail.docs);
        throw new ApiError(400, docs ? hint + "（接法：" + docs + "）" : hint);
      }
      throw err;
    }
  },

  museVideoJob: async (jobId: string, signal?: AbortSignal): Promise<MuseVideoJob> =>
    normalizeMuseVideoJob(await request("/v6/muse/video/jobs/" + enc(jobId), signal ? { signal } : undefined, 20000)),

  /* ---------------------- R8：模板级云端 Muse（每镜按角色立绘） ---------------------- */
  /** 起一个整集任务；后端已有任务时抛 ApiError(409) */
  museTemplate: async (body: MuseTemplateRequest): Promise<{ job_id: string }> => {
    const raw = await request<unknown>("/v6/muse/video/template", { method: "POST", body: JSON.stringify(body) }, 30000);
    return { job_id: str(rec(raw).job_id) };
  },

  museTemplateJob: async (jobId: string, signal?: AbortSignal): Promise<MuseTemplateJob> =>
    normalizeMuseTemplateJob(
      await request("/v6/muse/video/jobs/" + enc(jobId), signal ? { signal } : undefined, 20000),
    ),
};

/* ------------------------------ 派生工具 ------------------------------ */

/** 任务是否还在跑（queued/running）；run 结束态是 ok / planned / failed */
export function isAgentActive(run: AgentRun | null | undefined): boolean {
  if (!run) return false;
  return run.status === "queued" || run.status === "running";
}

/** 404/405/501 → 接口不可用（季接口已上线；仍 404 多为系列/资源 id 不存在） */
export function isAgentNotReady(err: unknown): boolean {
  return err instanceof ApiError && (err.status === 404 || err.status === 405 || err.status === 501);
}

export const AGENT_STATUS_LABEL: Record<string, string> = {
  queued: "排队中",
  running: "运行中",
  ok: "已完成",
  done: "已完成",
  planned: "计划完成",
  failed: "失败",
  pending: "未开始",
  skipped: "已跳过",
};

export function agentStatusLabel(status: string): string {
  return AGENT_STATUS_LABEL[String(status || "").toLowerCase()] || status || "—";
}

/** 阶段着色（日志按 stage 上色 / 卡片左侧色条） */
const STAGE_TONE_MAP: Record<string, string> = {
  story: "slate",
  check_story: "slate",
  voice: "jade",
  art: "violet",
  editorial: "cyan",
  shots: "gold",
  h3: "orange",
  assemble: "jade",
  storyboard: "cyan",
  qc: "red",
  publish: "gold",
  muse: "violet",
  // V50 · Muse 视频任务日志的阶段（后端 cloud / local / stub）
  cloud: "cyan",
  local: "jade",
  stub: "orange",
  // 工作流阶段：musev（MuseV/MusePose 视频）与 muse（MuseTalk 口型）
  musev: "cyan",
};
const STAGE_TONES = ["slate", "jade", "gold", "cyan", "violet", "orange", "red"];

export function stageTone(stageId: string): string {
  const key = String(stageId || "").toLowerCase();
  if (STAGE_TONE_MAP[key]) return STAGE_TONE_MAP[key];
  let hash = 0;
  for (let i = 0; i < key.length; i += 1) hash = (hash * 31 + key.charCodeAt(i)) % 9973;
  return STAGE_TONES[hash % STAGE_TONES.length];
}

/** 阶段中文名（阶段 id → 中文；未知 id 原样返回） */
export const STAGE_LABEL: Record<string, string> = {
  story: "剧本",
  check_story: "剧本核对",
  voice: "声音",
  art: "美术资产",
  editorial: "剪辑档案",
  shots: "镜头渲染",
  h3: "H3 hero 镜",
  assemble: "成片合成",
  storyboard: "分镜表",
  qc: "质检",
  publish: "发布",
  muse: "Muse 口型",
  musev: "Muse 视频",
};

export function stageLabel(stageId: string): string {
  return STAGE_LABEL[String(stageId || "").toLowerCase()] || stageId;
}

/** 运行证据（/media/agent_runs/<run_id>/<file>） */
export const AGENT_EVIDENCE_FILES: { label: string; file: string }[] = [
  { label: "summary.md", file: "summary.md" },
  { label: "run.json", file: "run.json" },
  { label: "events.jsonl", file: "events.jsonl" },
];

export function agentEvidence(run: AgentRun | null | undefined): AgentEvidence[] {
  const runId = run?.run_id || "";
  if (!runId) return [];
  return AGENT_EVIDENCE_FILES.map((item) => ({
    label: item.label,
    file: item.file,
    href: mediaUrl("/media/agent_runs/" + enc(runId) + "/" + item.file),
  }));
}

/** result.stages 汇总（完成/跳过/失败） */
export function summarizeRun(run: AgentRun | null | undefined): { done: number; skipped: number; failed: number; total: number } {
  const stages = arr(run?.result?.stages).map(rec);
  let done = 0;
  let skipped = 0;
  let failed = 0;
  stages.forEach((stage) => {
    const status = str(stage.status).toLowerCase();
    if (status === "done" || status === "ok") done += 1;
    else if (status === "skipped") skipped += 1;
    else if (status === "failed") failed += 1;
  });
  return { done, skipped, failed, total: stages.length };
}

/* ------------------------------ edit ops（M2：四个 op 页面可用） ------------------------------ */

export interface AgentEditOp {
  id: string;
  label: string;
  needsCharacter: boolean;
  field: "value" | "json";
  hint: string;
  placeholder: string;
}

export const AGENT_EDIT_OPS: AgentEditOp[] = [
  {
    id: "set_voice",
    label: "换音色 · set_voice",
    needsCharacter: true,
    field: "value",
    hint: "character + value（contract/v5/voices.json 里的 preset id）",
    placeholder: "legacy_v4_laozhou",
  },
  {
    id: "patch_editorial",
    label: "改剪辑档案 · patch_editorial",
    needsCharacter: false,
    field: "json",
    hint: "JSON 深合并进 contract/v5/editorial/<template>.json",
    placeholder: '{"pacing":{"climax":0.6}}',
  },
  {
    id: "patch_template",
    label: "改模板 · patch_template",
    needsCharacter: false,
    field: "json",
    hint: "JSON 深合并进 contract/v5/templates/<template>.json",
    placeholder: '{"style":"ink_chronicle"}',
  },
  {
    id: "patch_workflow",
    label: "改工作流 · patch_workflow",
    needsCharacter: false,
    field: "json",
    hint: '支持 {"enable":["h3"]} / {"params":{...}} / {"stages":[...]}',
    placeholder: '{"enable":["h3"]}',
  },
];
/* ------------------------------ V49 · Muse 口型（MuseTalk 本机推理） ------------------------------ */
// 后端：GET /api/v6/muse/status · POST /api/v6/muse/sync · GET /api/v6/muse/jobs/{job_id}
// 产物：/media/muse/...（recent[].url / result.items[].out_url）

export interface MuseDep {
  key: string;
  value: string;
  ok: boolean;
}

export interface MuseAvailable {
  ok: boolean;
  musetalk: boolean;
  syncnet: boolean;
  /** 本机 torch 是否装进 lipsync venv（后端 available.torch_in_env） */
  torchInEnv: boolean;
  /** 默认 provider（后端 available.provider_default，通常 musetalk） */
  providerDefault: string;
  venv: string;
  deps: MuseDep[];
  reason: string;
}

export interface MuseRecent {
  template: string;
  shot: string;
  url: string;
  bytes: number;
  duration: number;
}

export interface MuseJobBrief {
  job_id: string;
  status: string;
  template: string;
  shot: string;
  out_url: string;
  elapsed: number;
}

/** V50：口型同步分（嘴动 ↔ 语音包络相关；syncnet 权重不全时 provider=mouth_audio_corr） */
export interface MuseSyncScore {
  ok: boolean;
  provider: string;
  corr_best: number;
  lag_frames: number;
  face_ratio: number;
}

export interface MuseJobItem {
  shot: string;
  out_url: string;
  elapsed: number;
  sync: MuseSyncScore | null;
  /** V52：失败镜头的原因（后端失败项只有 {shot,error,elapsed}，无 out_url） */
  error: string;
}

export interface MuseJob {
  job_id: string;
  status: string;
  logs: { t: string; msg: string }[];
  items: MuseJobItem[];
  error: string;
}

export interface MuseStatus {
  available: MuseAvailable;
  jobs: MuseJobBrief[];
  recent: MuseRecent[];
}

export interface MuseSyncRequest {
  template?: string;
  shot?: string;
  clip?: string;
  audio?: string;
  start?: number;
  duration?: number;
  limit?: number;
}

function normalizeDepValue(value: unknown): { text: string; ok: boolean } {
  if (typeof value === "string") {
    const text = value.trim();
    const ok = Boolean(text) && !/^(false|missing|none|no|0|-)$/i.test(text);
    return { text: text || "—", ok };
  }
  if (typeof value === "boolean") return { text: value ? "ok" : "缺失", ok: value };
  if (typeof value === "number") return { text: Number.isInteger(value) ? String(value) : value.toFixed(2), ok: value > 0 };
  if (value && typeof value === "object") {
    const item = rec(value);
    const okValue = item.ok ?? item.available ?? Object.keys(item).length > 0;
    const text = str(item.version || item.path || item.value || item.label) || JSON.stringify(value);
    return { text, ok: bool(okValue, true) };
  }
  return { text: "—", ok: false };
}

export function normalizeMuseStatus(raw: unknown): MuseStatus {
  const item = rec(raw);
  const av = rec(item.available);
  const providers = rec(av.providers);
  const depMap = rec(av.deps);
  const deps: MuseDep[] = Object.keys(depMap).map((key) => {
    const { text, ok } = normalizeDepValue(depMap[key]);
    return { key, value: text, ok };
  });
  const venv = str(av.venv || av.venv_python || depMap.venv || depMap.venv_path);
  return {
    available: {
      ok: bool(av.ok, false),
      musetalk: bool(providers.musetalk ?? av.musetalk, false),
      syncnet: bool(av.syncnet, false),
      torchInEnv: bool(av.torch_in_env, false),
      providerDefault: str(av.provider_default || "musetalk"),
      venv,
      deps,
      reason: str(av.reason),
    },
    jobs: arr(item.jobs).map((job) => {
      const j = rec(job);
      return {
        job_id: str(j.job_id || j.id),
        status: str(j.status),
        template: str(j.template),
        shot: str(j.shot) || str(arr(j.shots)[0]),
        out_url: str(j.out_url),
        elapsed: num(j.elapsed, 0),
      };
    }),
    recent: arr(item.recent).map((entry) => {
      const r = rec(entry);
      return {
        template: str(r.template),
        shot: str(r.shot),
        url: str(r.url || r.out_url),
        bytes: num(r.bytes, 0),
        duration: num(r.duration, 0),
      };
    }),
  };
}

export function normalizeMuseJob(raw: unknown): MuseJob {
  const item = rec(raw);
  const result = rec(item.result);
  const itemsSource = arr(result.items).length ? arr(result.items) : arr(item.items);
  return {
    job_id: str(item.job_id || item.id),
    status: str(item.status, "queued").toLowerCase(),
    logs: arr(item.logs).map((line) => {
      const l = rec(line);
      return { t: str(l.t || l.ts), msg: str(l.msg || l.message) };
    }),
    items: itemsSource.map((entry) => {
      const it = rec(entry);
      const syncRaw = it.sync && typeof it.sync === "object" ? rec(it.sync) : null;
      // 无 corr_best 视为「没有分数」，UI 不显示同步行
      const sync =
        syncRaw && syncRaw.corr_best !== null && syncRaw.corr_best !== undefined
          ? {
              ok: bool(syncRaw.ok, true),
              provider: str(syncRaw.provider),
              corr_best: num(syncRaw.corr_best, 0),
              lag_frames: num(syncRaw.lag_frames, 0),
              face_ratio: num(syncRaw.face_ratio, 0),
            }
          : null;
      return {
        shot: str(it.shot),
        out_url: str(it.out_url || it.url),
        elapsed: num(it.elapsed, 0),
        sync,
        error: str(it.error),
      };
    }),
    error: str(item.error),
  };
}

/** Muse 任务是否还在跑（queued/running/starting） */
export function isMuseActive(job: MuseJob | { status?: string } | null | undefined): boolean {
  const status = String(job?.status || "").toLowerCase();
  return status === "queued" || status === "running" || status === "starting";
}

/** 同步分着色：>0.25 绿（同向）/ 0.1–0.25 金（弱）/ <0.1 灰（≈无关） */
export function syncTone(corr: number): "jade" | "gold" | "slate" {
  if (!Number.isFinite(corr)) return "slate";
  if (corr > 0.25) return "jade";
  if (corr >= 0.1) return "gold";
  return "slate";
}
/* ------------------------------ V50 · Muse 视频生成 ------------------------------ */
// 后端：GET /api/v6/muse/video/status · POST /api/v6/muse/video · GET /api/v6/muse/video/jobs/{id}
// provider：muse_cloud（云）/ musev_local（本地）/ musepose_local（本地姿态）/ stub（占位，仅接线自测）

export type MuseVideoProviderId = string;

export type MuseVideoChecks = Record<string, Record<string, unknown>>;

export interface MuseVideoState {
  ok: boolean;
  provider: MuseVideoProviderId;
  checks: MuseVideoChecks;
  docs: string;
  hint: string;
  root: string;
}

export interface MuseVideoRecent {
  name: string;
  url: string;
  bytes: number;
  mtime: string;
}

export interface MuseVideoJobBrief {
  job_id: string;
  status: string;
  provider: string;
  out_url: string;
  elapsed: number;
}

export interface MuseVideoJobResult {
  provider: string;
  bytes: number;
  elapsed_sec: number;
  out: string;
  out_url: string;
  stub: boolean;
  probe: string;
}

export interface MuseVideoJob {
  job_id: string;
  status: string;
  logs: { t: string; stage: string; msg: string }[];
  result: MuseVideoJobResult | null;
  error: string;
}

export interface MuseVideoStatus {
  state: MuseVideoState;
  recent: MuseVideoRecent[];
  jobs: MuseVideoJobBrief[];
  running: string;
}

export interface MuseVideoCreate {
  job_id: string;
  provider: string;
  out: string;
}

export interface MuseVideoRequest {
  prompt: string;
  duration?: number;
  aspect?: string;
  provider?: MuseVideoProviderId;
  template?: string;
  name?: string;
  allow_stub?: boolean;
}

/** 可选 provider（auto = 跟随后端默认 state.provider） */
export const MUSE_VIDEO_PROVIDERS: { id: string; label: string }[] = [
  { id: "", label: "auto（后端默认）" },
  { id: "muse_cloud", label: "muse_cloud · 通用云接口" },
  { id: "replicate", label: "replicate · MuseV 托管" },
  { id: "useapi_muse", label: "useapi_muse · Meta Muse" },
  { id: "musev_local", label: "musev_local · 本地" },
  { id: "musepose_local", label: "musepose_local · 本地姿态" },
  { id: "stub", label: "stub · 占位（不可交付）" },
];

export function normalizeMuseVideoStatus(raw: unknown): MuseVideoStatus {
  const item = rec(raw);
  const state = rec(item.state);
  const checksRaw = rec(state.checks);
  const checks: MuseVideoChecks = {};
  Object.keys(checksRaw).forEach((key) => {
    checks[key] = rec(checksRaw[key]);
  });
  return {
    state: {
      ok: bool(state.ok, false),
      provider: str(state.provider),
      checks,
      docs: str(state.docs),
      hint: str(state.hint),
      root: str(state.root),
    },
    recent: arr(item.recent).map((entry) => {
      const r = rec(entry);
      return { name: str(r.name), url: str(r.url || r.out_url), bytes: num(r.bytes, 0), mtime: str(r.mtime) };
    }),
    jobs: arr(item.jobs).map((entry) => {
      const j = rec(entry);
      return {
        job_id: str(j.job_id || j.id),
        status: str(j.status),
        provider: str(j.provider),
        out_url: str(j.out_url),
        elapsed: num(j.elapsed, 0),
      };
    }),
    running: str(item.running),
  };
}

export function normalizeMuseVideoJob(raw: unknown): MuseVideoJob {
  const item = rec(raw);
  const resultRaw = rec(item.result);
  const hasResult = Object.keys(resultRaw).length > 0;
  return {
    job_id: str(item.job_id || item.id),
    status: str(item.status, "queued").toLowerCase(),
    logs: arr(item.logs).map((line) => {
      const l = rec(line);
      return { t: str(l.t || l.ts), stage: str(l.stage), msg: str(l.msg || l.message) };
    }),
    result: hasResult
      ? {
          provider: str(resultRaw.provider),
          bytes: num(resultRaw.bytes, 0),
          elapsed_sec: num(resultRaw.elapsed_sec, 0),
          out: str(resultRaw.out),
          out_url: str(resultRaw.out_url || resultRaw.url),
          stub: bool(resultRaw.stub, false),
          probe: str(resultRaw.probe),
        }
      : null,
    error: str(item.error),
  };
}

/** provider 徽标：stub / 未接入 → 灰；云 → 金；本地 → 玉 */
export function museVideoTone(provider: string, ok: boolean): "jade" | "gold" | "slate" {
  if (!ok || !provider || provider === "stub") return "slate";
  if (provider === "muse_cloud" || provider === "replicate" || provider === "useapi_muse") return "gold";
  return "jade";
}

/* ------------------------- R8：模板级云端 Muse（每镜按角色） ------------------------- */

export interface MuseTemplateRequest {
  template: string;
  /** 段数（每段 10s） */
  segments?: number;
  limit?: number;
  /** 首尾帧衔接：用上一段最后一帧当下一段首帧 */
  chain?: boolean;
  force?: boolean;
  assemble?: boolean;
  dry_run?: boolean;
}

export interface MuseTemplateJob {
  job_id: string;
  /** running | done | failed */
  status: string;
  /** 当前阶段文案（后端 prog 回调写入） */
  stage: string;
  /** 逐行日志（"[角色] 消息"） */
  logs: string[];
  ok_count: number;
  failed: string[];
  /** 成片路径（outputs/... 或 /media/...） */
  final: string;
  error: string;
  template: string;
}

export function normalizeMuseTemplateJob(raw: unknown): MuseTemplateJob {
  const item = rec(raw);
  return {
    job_id: str(item.job_id || item.id),
    status: str(item.status, "queued").toLowerCase(),
    stage: str(item.stage),
    logs: arr(item.logs).map((line) => (typeof line === "string" ? line : str(rec(line).msg))),
    ok_count: num(item.ok_count, 0),
    failed: arr(item.failed).map((line) => str(line)),
    final: str(item.final),
    error: str(item.error),
    template: str(item.template),
  };
}

/** 任务是否还在跑 */
export function isMuseTemplateActive(job: MuseTemplateJob | null): boolean {
  if (!job) return false;
  return job.status === "running" || job.status === "queued";
}

/** 从 409 的 detail 里抠出已在跑的 job_id（后端格式：已有 Muse 视频任务在跑: muset_xxx） */
export function runningJobIdFrom409(message: string): string {
  const hit = /muset_[a-z0-9]+/i.exec(String(message || ""));
  return hit ? hit[0] : "";
}

/** 成片路径 → 可播放 URL（outputs/... → /media/...） */
export function museFinalUrl(raw: string): string {
  const text = String(raw || "").replace(/\\/g, "/");
  if (!text) return "";
  if (/^https?:/i.test(text)) return text;
  if (text.startsWith("/media/")) return mediaUrl(text);
  const cut = text.indexOf("/outputs/");
  if (cut >= 0) return mediaUrl("/media/" + text.slice(cut + "/outputs/".length));
  if (text.startsWith("outputs/")) return mediaUrl("/media/" + text.slice("outputs/".length));
  return mediaUrl(text);
}
