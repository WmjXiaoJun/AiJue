// Project Studio（V9 项目工作台）类型 / 常量 / API 包装。
// 设计文档：docs/V9_PROJECT_STUDIO.md；后端沿用现有 /api 端点。
import { API_BASE, api, errorText, mediaUrl } from "./api";
import type {
  Asset,
  Character,
  ConceptFiles,
  CostReport,
  Episode,
  Job,
  Localization,
  Project,
  PublishConfig,
  PublishRecord,
  Shot,
  ShotVersion,
} from "../types";

export type StudioTabKey = "script" | "assets" | "storyboard" | "generate" | "finish" | "templates3d";

export const STUDIO_TABS: { key: StudioTabKey; no: string; label: string; en: string; desc: string }[] = [
  { key: "script", no: "01", label: "编剧间", en: "SCRIPT", desc: "台本 / AI 生成 / 分镜脚本" },
  { key: "assets", no: "02", label: "资产库", en: "ASSETS", desc: "角色 / 场景 / 声线 / 锁定" },
  { key: "storyboard", no: "03", label: "分镜台", en: "STORYBOARD", desc: "镜头 / 台词 / 视觉 / 版本" },
  { key: "generate", no: "04", label: "生成台", en: "RENDER", desc: "任务队列 / 进度 / 费用" },
  { key: "finish", no: "05", label: "成片房", en: "FINISHING", desc: "成片 / 渲染 / 出海 / 发布" },
  { key: "templates3d", no: "06", label: "3D 模板", en: "3D TEMPLATE", desc: "项目模板 / 16 模板 3D / 切换" },
];

/** 项目 format_id 与 16 个 3D 模板 id 的换算后缀（national_treasure_5min ↔ national_treasure） */
export const TPL3D_FORMAT_SUFFIX = "_5min";

/** Cinema Studio「16 模板 3D 制作进度」入口 */
export const TPL3D_LIBRARY_HREF = "#/cinema/templates";

export interface StudioTabProps {
  project: Project;
  episode: Episode;
  /** 更新 episode（保存台本、刷新镜头数、渲染完成等场景） */
  onEpisode: (episode: Episode) => void;
  /** 切到下一个 tab（如「生成分镜 → 分镜台」「分镜 → 生成台」） */
  onNavigate: (tab: StudioTabKey) => void;
}

// ---------------- 状态标签 / 色调 ----------------
export const EPISODE_STATUS_TEXT: Record<string, string> = {
  draft: "草稿",
  ready: "已就绪",
  rendering: "渲染中",
  generating: "生成中",
  done: "已完成",
  failed: "失败",
};

export const JOB_STATUS_TEXT: Record<string, string> = {
  queued: "排队中",
  running: "进行中",
  done: "已完成",
  failed: "失败",
  canceled: "已取消",
  cancelled: "已取消",
};

export const JOB_TYPE_TEXT: Record<string, string> = {
  shot: "镜头生成",
  render: "整片渲染",
  dynamic: "动态镜头",
  character: "角色生成",
  concept: "概念图",
  h3: "H3 动画",
  localize: "多语种",
  publish: "发布",
  export: "导出",
};

export const SHOT_STATUS_TEXT: Record<string, string> = {
  pending: "待生成",
  queued: "排队中",
  rendering: "生成中",
  running: "生成中",
  done: "已完成",
  failed: "失败",
  preview: "预览",
  static: "静态",
};

export const ASSET_ROLE_TEXT: Record<string, string> = {
  host: "主持",
  guest: "嘉宾",
  narrator: "旁白",
  scene: "场景",
  prop: "道具",
  style: "风格",
};

export function toneOfStatus(status?: string | null): "gold" | "jade" | "red" | "slate" {
  const s = String(status || "").toLowerCase();
  if (["done", "ready", "success", "succeeded"].includes(s)) return "jade";
  if (["running", "rendering", "generating", "queued", "preview"].includes(s)) return "gold";
  if (["failed", "error", "canceled", "cancelled"].includes(s)) return "red";
  return "slate";
}

export function isActiveJob(job: Job | null | undefined): boolean {
  const s = String(job?.status || "").toLowerCase();
  return s === "queued" || s === "running" || s === "rendering" || s === "generating";
}

export function jobTitle(job: Job): string {
  const type = JOB_TYPE_TEXT[job.type] || job.type || "任务";
  if (job.shot_id) return `${type} · ${job.shot_id}`;
  return type;
}

// ---------------- 格式化 ----------------
export function fmtClock(sec: number | null | undefined): string {
  const s = Math.max(0, Math.round(Number(sec) || 0));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

export function fmtMoney(n: number | null | undefined): string {
  const v = Number(n) || 0;
  return "¥" + v.toFixed(2);
}

export function fmtDateTime(iso?: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso).slice(0, 19).replace("T", " ");
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getMonth() + 1}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export function relativeTime(iso?: string | null): string {
  if (!iso) return "";
  const d = new Date(iso).getTime();
  if (Number.isNaN(d)) return "";
  const diff = Math.max(0, Date.now() - d) / 1000;
  if (diff < 60) return `${Math.round(diff)} 秒前`;
  if (diff < 3600) return `${Math.round(diff / 60)} 分钟前`;
  if (diff < 86400) return `${Math.round(diff / 3600)} 小时前`;
  return `${Math.round(diff / 86400)} 天前`;
}

export function countWords(text: string): number {
  return (text || "").replace(/\s+/g, "").length;
}

/** 台本段落切分（按空行/【】标题切） */
export function scriptParagraphs(script: string): { title: string; body: string }[] {
  const raw = (script || "").trim();
  if (!raw) return [];
  const blocks = raw.split(/\n{2,}/).map((b) => b.trim()).filter(Boolean);
  return blocks.map((b) => {
    const m = b.match(/^【([^】]+)】\s*([\s\S]*)$/);
    if (m) return { title: m[1], body: m[2].trim() };
    const line = b.split("\n")[0];
    return { title: line.length <= 24 ? line : "", body: line.length <= 24 ? b.split("\n").slice(1).join("\n").trim() || line : b };
  });
}

// ---------------- 轮询辅助 ----------------
/** 轮询单个任务直到结束（done/failed/canceled）；onTick 返回 false 可提前停止 */
export async function waitJob(
  jobId: string,
  onTick?: (job: Job) => void,
  opts: { intervalMs?: number; timeoutMs?: number } = {},
): Promise<Job> {
  const interval = opts.intervalMs ?? 1500;
  const deadline = Date.now() + (opts.timeoutMs ?? 15 * 60 * 1000);
  let last: Job | null = null;
  while (Date.now() < deadline) {
    await new Promise((r) => window.setTimeout(r, interval));
    try {
      last = await api.job(jobId);
    } catch (err) {
      if (err) continue;
    }
    const current = last;
    if (!current) continue;
    onTick?.(current);
    if (!isActiveJob(current)) return current;
  }
  return last || ({} as Job);
}

export interface PublishPreflightCheck {
  id: string;
  label: string;
  status: "pass" | "warn" | "fail" | string;
  detail?: string;
}

export interface PublishPreflight {
  episode_id: string;
  ok: boolean;
  publishable: boolean;
  checks: PublishPreflightCheck[];
  config?: PublishConfig;
  warnings?: string[];
}

// ---------------- API 包装（沿用 /api） ----------------
export const studioApi = {
  project: (pid: string): Promise<Project> => api.project(pid),
  episode: (eid: string): Promise<Episode> => api.episode(eid),
  episodes: (pid: string): Promise<Episode[]> => api.episodes(pid),
  patchEpisode: (eid: string, body: { title?: string; target_duration?: number; script?: string }): Promise<Episode> =>
    api.patchEpisode(eid, body),
  generateScript: api.generateScript,
  generateStoryboard: (eid: string, force = false) => api.generateStoryboard(eid, force),

  shots: (eid: string): Promise<Shot[]> => api.shots(eid),
  patchShot: (sid: string, body: { line?: string; duration?: number; status?: string; visual?: string; punchline?: string }): Promise<Shot> =>
    api.patchShot(sid, body),
  createShot: api.createShot,
  deleteShot: (sid: string): Promise<unknown> => api.deleteShot(sid),
  generateShot: (sid: string, params?: Record<string, unknown>): Promise<Job> => api.generateShot(sid, params),
  generateDynamicShot: (sid: string): Promise<Job> => api.generateDynamicShot(sid),
  generateAllDynamic: (eid: string) => api.generateAllDynamic(eid),
  versions: (sid: string): Promise<ShotVersion[]> => api.versions(sid),
  selectVersion: (sid: string, vid: string): Promise<Shot> => api.selectVersion(sid, vid),

  assets: (eid: string): Promise<Asset[]> => api.assets(eid),
  createAsset: api.createAsset,
  patchAsset: (aid: string, body: { locked?: boolean; voice?: string; persona?: string }): Promise<Asset> =>
    api.patchAsset(aid, body),

  characters: (pid: string): Promise<Character[]> => api.characters(pid),
  character: (cid: string): Promise<Character> => api.character(cid),
  createCharacter: api.createCharacter,
  /** 更新角色资料（name/title/persona/voice/accent_color）；后端会同步资产 */
  patchCharacter: (
    cid: string,
    body: Partial<Pick<Character, "name" | "title" | "persona" | "voice" | "accent_color">>,
  ): Promise<Character> => api.patchCharacter(cid, body),
  generateCharacter: (cid: string, styles?: string[]): Promise<Job> => api.generateCharacter(cid, styles),
  characterFiles: (cid: string) => api.characterFiles(cid),
  characterConcepts: (cid: string): Promise<ConceptFiles> => api.characterConcepts(cid),
  generateConcept: (cid: string, body: { modes?: string[]; style?: string; prompt_extra?: string; seed?: number }): Promise<Job> =>
    api.generateConcept(cid, body),
  setPortraitFromConcept: (cid: string, path: string, fallbackPath?: string) => api.setPortraitFromConcept(cid, path, fallbackPath),
  /** 角色立绘 → H3 动画（本地 ComfyUI 或云端 MetaSo；昂贵，UI 需二次确认） */
  h3Animate: (
    cid: string,
    body: { image?: string; prompt?: string; provider?: string } = {},
    fallbackImage?: string,
  ): Promise<Job> => api.h3Animate(cid, body, fallbackImage),

  jobs: (params: { episode_id?: string; type?: string; status?: string } = {}): Promise<Job[]> => api.jobs(params),
  job: (jid: string): Promise<Job> => api.job(jid),
  retryJob: (jid: string): Promise<Job> => api.retryJob(jid),
  costs: (eid: string): Promise<CostReport> => api.costs(eid),
  render: (eid: string): Promise<Job> => api.render(eid),
  exportEpisode: (eid: string, type = "capcut"): Promise<Job> => api.exportEpisode(eid, type),
  localizeEpisode: (eid: string, langs: string[]): Promise<Job[]> => api.localizeEpisode(eid, langs),
  localizations: (eid: string): Promise<Localization[]> => api.localizations(eid),
  publishConfig: (): Promise<PublishConfig> => api.publishConfig(),
  publishEpisode: api.publishEpisode,
  publishes: (eid: string): Promise<PublishRecord[]> => api.publishes(eid),
  publishPreflight: async (eid: string): Promise<PublishPreflight> => {
    const res = await fetch(API_BASE + "/episodes/" + eid + "/publish/preflight");
    const data = (await res.json().catch(() => ({}))) as { detail?: unknown } & PublishPreflight;
    if (!res.ok) {
      const detail = typeof data.detail === "string" ? data.detail : "发布前检查失败";
      throw new Error(detail);
    }
    return data;
  },
  settings: api.settings,
};

export { errorText, mediaUrl };
export type { Asset, Character, ConceptFiles, CostReport, Episode, Job, Localization, Project, PublishConfig, PublishRecord, Shot, ShotVersion };
