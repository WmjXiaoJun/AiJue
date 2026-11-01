import { useCallback, useEffect, useMemo, useState } from "react";
import type { Episode, Job, Project } from "../../types";
import { api, mediaUrl } from "../../lib/api";
import { cinemaApi } from "../../lib/cinema";
import type { CinemaTemplate, CinemaTemplatesResponse } from "../../lib/cinema";
import { TEMPLATES, templateLabel } from "../../lib/templates";
import { EPISODE_STATUS_TEXT, JOB_TYPE_TEXT, fmtDateTime, relativeTime, isActiveJob } from "../../lib/studio";
import { useToast } from "../Toast";
import { Spinner } from "../ui";
import { BrandMark } from "../icons";
import { SettingsModal } from "../SettingsModal";
import "../../styles/cinema.css";
import "../../styles/platform.css";

interface Props {
  projects: Project[];
  offline: boolean;
  onOpenProject: (project: Project) => void;
  onOpenCinema: () => void;
  onOpenV5: () => void;
  onProjectCreated: (project: Project) => void;
  onRefreshProjects: () => void | Promise<void>;
}

type EpisodeMap = Record<string, Episode[]>;

function fmtClock(sec: number | null | undefined): string {
  const s = Math.max(0, Math.round(Number(sec) || 0));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

/** 项目格式 id → 3D 模板 id（roundtable_5min → roundtable） */
function templateIdOf(formatId?: string | null): string {
  return String(formatId || "").replace(/_5min$/i, "");
}

function posterOf(project: Project): string {
  return `/media/templates_3d/${templateIdOf(project.format_id)}/poster.jpg`;
}

function latestEpisode(list: Episode[] | undefined): Episode | null {
  if (!list || !list.length) return null;
  return [...list].sort((a, b) => {
    const ta = new Date(a.created_at || 0).getTime();
    const tb = new Date(b.created_at || 0).getTime();
    if (ta !== tb) return tb - ta;
    return (b.no || 0) - (a.no || 0);
  })[0];
}

function jobLabel(job: Job): string {
  const type = JOB_TYPE_TEXT[job.type] || job.type || "任务";
  const target = job.shot_id || (job.params as Record<string, unknown> | null)?.["shot"] || "";
  return target ? `${type} · ${String(target)}` : type;
}

const JOB_STATUS: Record<string, string> = {
  queued: "排队中",
  running: "进行中",
  done: "已完成",
  failed: "失败",
  canceled: "已取消",
  cancelled: "已取消",
  rendering: "进行中",
  generating: "进行中",
};

export function PlatformHome({
  projects,
  offline,
  onOpenProject,
  onOpenCinema,
  onOpenV5,
  onProjectCreated,
  onRefreshProjects,
}: Props) {
  const notify = useToast();
  const [episodeMap, setEpisodeMap] = useState<EpisodeMap>({});
  const [tpl, setTpl] = useState<CinemaTemplatesResponse | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [newOpen, setNewOpen] = useState(false);

  const load = useCallback(async (silent = false) => {
    if (!silent) setLoading(true);
    const list = projects || [];
    const epResults = await Promise.allSettled(list.map((p) => api.episodes(p.id)));
    const map: EpisodeMap = {};
    epResults.forEach((res, index) => {
      if (res.status === "fulfilled") map[list[index].id] = res.value;
    });
    setEpisodeMap(map);
    try {
      setTpl(await cinemaApi.templates());
    } catch {
      setTpl(null);
    }
    try {
      const all = await api.jobs({});
      setJobs([...all].sort((a, b) => new Date(b.created_at || 0).getTime() - new Date(a.created_at || 0).getTime()));
    } catch {
      setJobs([]);
    }
    setLoading(false);
  }, [projects]);

  useEffect(() => {
    void load(false);
  }, [load]);

  const refresh = useCallback(async () => {
    setRefreshing(true);
    try {
      await onRefreshProjects();
      await load(true);
    } finally {
      setRefreshing(false);
    }
  }, [onRefreshProjects, load]);

  const allEpisodes = useMemo(
    () => Object.values(episodeMap).flat(),
    [episodeMap],
  );
  const finishedCount = useMemo(() => allEpisodes.filter((ep) => ep.render_url).length, [allEpisodes]);

  const heroPick = useMemo(() => {
    const withEp = projects
      .map((p) => ({ project: p, episode: latestEpisode(episodeMap[p.id]) }))
      .filter((item) => item.episode) as { project: Project; episode: Episode }[];
    if (!withEp.length) return null;
    return withEp.sort((a, b) => {
      const ta = new Date(a.episode.created_at || 0).getTime();
      const tb = new Date(b.episode.created_at || 0).getTime();
      return tb - ta;
    })[0];
  }, [projects, episodeMap]);

  const tplSummary = tpl?.summary;
  const tplDone = tplSummary?.done ?? 0;
  const tplTotal = tplSummary?.total ?? 16;
  const activeJobs = jobs.filter(isActiveJob).length;

  return (
    <div className="cm-app pl-app">
      <aside className="cm-rail">
        <div className="cm-brand">
          <div className="cm-brand-seal">
            <BrandMark size={30} />
          </div>
          <div className="cm-brand-txt">
            <span className="cm-brand-name">造片局</span>
            <span className="cm-brand-sub">CINEMA STUDIO</span>
          </div>
        </div>
        <div className="cm-rail-label">制片流程</div>
        <nav className="cm-nav">
          <button type="button" className="cm-nav-item is-active">
            <IconGrid />
            <span className="pl-nav-label">项目大厅</span>
            <span className="cm-nav-no">01</span>
          </button>
          <button type="button" className="cm-nav-item" onClick={onOpenCinema}>
            <IconFilm />
            <span className="pl-nav-label">Cinema 3D</span>
            <span className="cm-nav-no">02</span>
          </button>
          <button type="button" className="cm-nav-item" onClick={onOpenV5}>
            <IconWorld />
            <span className="pl-nav-label">V5 世界</span>
            <span className="cm-nav-no">03</span>
          </button>
          <button type="button" className="cm-nav-item" onClick={() => setSettingsOpen(true)}>
            <IconGear />
            <span className="pl-nav-label">设置</span>
            <span className="cm-nav-no">04</span>
          </button>
        </nav>
        <div className="cm-rail-foot">
          <div className={`pl-env ${offline ? "is-off" : ""}`}>
            <i className="cm-status-dot" />
            {offline ? "后端未连接" : "后端已连接"}
          </div>
          <div className="pl-env-sub">v1.0.0 · 模式 comfyui</div>
        </div>
      </aside>

      <div className="cm-main">
        <header className="cm-topbar">
          <div className="pl-top-left">
            <span className="cm-kicker">PRODUCTION BOARD · 造片局</span>
            <h1 className="pl-h1">项目大厅</h1>
          </div>
          <div className="pl-top-stats">
            <div className="pl-top-stat">
              <b>{projects.length}</b>
              <span>项目</span>
            </div>
            <div className="pl-top-stat">
              <b>{allEpisodes.length}</b>
              <span>期数</span>
            </div>
            <div className="pl-top-stat">
              <b className="is-jade">{finishedCount}</b>
              <span>成片</span>
            </div>
            <div className="pl-top-stat">
              <b className="is-gold">{tplDone}/{tplTotal}</b>
              <span>3D 模板</span>
            </div>
            {activeJobs > 0 && (
              <div className="pl-top-stat">
                <b className="is-gold">{activeJobs}</b>
                <span>进行中任务</span>
              </div>
            )}
          </div>
          <div className="pl-top-actions">
            <button type="button" className="cm-icon-btn" title="刷新" onClick={() => void refresh()} disabled={refreshing}>
              {refreshing ? <Spinner size={13} /> : <IconRefresh />}
            </button>
            <button type="button" className="cm-btn cm-btn-primary" onClick={() => setNewOpen(true)}>
              <IconPlus />
              新建项目
            </button>
          </div>
        </header>

        <main className="pl-content">
          {loading ? (
            <div className="cm-loading">
              <Spinner size={15} /> 正在读取项目数据…
            </div>
          ) : (
            <>
              {heroPick && (
                <section className="pl-hero">
                  <div className="pl-hero-body">
                    <span className="cm-kicker">CONTINUE PRODUCTION · 继续制作</span>
                    <h2 className="pl-hero-title">{heroPick.project.name}</h2>
                    <div className="pl-hero-ep">
                      <span className="pl-ep-no">第 {heroPick.episode.no} 期</span>
                      <span className="pl-hero-ep-title">{heroPick.episode.title}</span>
                    </div>
                    <div className="pl-chips">
                      <span className="cm-chip is-jade">{heroPick.episode.render_url ? "已出成片" : "制作中"}</span>
                      <span className="cm-chip is-slate cm-mono">目标 {fmtClock(heroPick.episode.target_duration)}</span>
                      <span className="cm-chip is-slate cm-mono">{heroPick.episode.shots_done}/{heroPick.episode.shots_total || "—"} 镜</span>
                      <span className="cm-chip is-slate cm-mono">{templateLabel(heroPick.project.format_id)}</span>
                    </div>
                    <div className="pl-hero-actions">
                      <button type="button" className="cm-btn cm-btn-primary" onClick={() => onOpenProject(heroPick.project)}>
                        进入工作台 →
                      </button>
                      {heroPick.episode.render_url && (
                        <a className="cm-btn cm-btn-ghost" href={`#/episodes/${heroPick.episode.id}/finish`}>
                          看完成片
                        </a>
                      )}
                      <button
                        type="button"
                        className="cm-btn cm-btn-ghost"
                        onClick={() => void createEpisodeFor(heroPick.project.id)}
                      >
                        <IconPlus /> 新建一期
                      </button>
                    </div>
                  </div>
                  <div className="pl-hero-media">
                    <img src={posterOf(heroPick.project)} alt={heroPick.project.name} onError={(e) => { (e.currentTarget as HTMLImageElement).style.display = "none"; }} />
                    <div className="pl-hero-glow" />
                    <div className="pl-hero-frame" />
                    <div className="pl-hero-badge">
                      <span className="cm-mono">{templateIdOf(heroPick.project.format_id).toUpperCase()}</span>
                      <b>3D</b>
                    </div>
                  </div>
                </section>
              )}

              <section className="pl-section">
                <div className="pl-sec-head">
                  <h2 className="pl-h2">我的项目</h2>
                  <span className="pl-sec-sub">{projects.length} 个项目 · {allEpisodes.length} 期</span>
                  <button type="button" className="cm-btn cm-btn-ghost cm-btn-sm pl-sec-action" onClick={() => setNewOpen(true)}>
                    <IconPlus /> 新建项目
                  </button>
                </div>
                <div className="pl-projects">
                  {projects.map((project) => {
                    const eps = episodeMap[project.id] || [];
                    const latest = latestEpisode(eps);
                    return (
                      <article key={project.id} className="pl-project" onClick={() => onOpenProject(project)}>
                        <div className="pl-project-cover">
                          <img src={posterOf(project)} alt="" onError={(e) => { (e.currentTarget as HTMLImageElement).style.display = "none"; }} />
                          <div className="pl-cover-shade" />
                          <span className="pl-cover-format">{templateLabel(project.format_id)}</span>
                          <span className="pl-cover-no cm-mono">EP.{String(project.episode_count || 1).padStart(3, "0")}</span>
                        </div>
                        <div className="pl-project-body">
                          <h3 className="pl-project-name">{project.name}</h3>
                          <div className="pl-project-meta">
                            <span>{eps.length} 期</span>
                            <span className="pl-dot" />
                            <span>{eps.filter((ep) => ep.render_url).length} 支成片</span>
                            <span className="pl-dot" />
                            <span className="cm-mono">¥{Number(project.spent || 0).toFixed(2)}</span>
                          </div>
                          <div className="pl-project-latest">
                            {latest ? (
                              <>
                                <i className={`pl-mini-dot ${latest.render_url ? "is-jade" : "is-gold"}`} />
                                <span className="pl-latest-text">
                                  第 {latest.no} 期 · {latest.title || "未命名"}
                                </span>
                                <span className="pl-latest-status">{latest.render_url ? "已出片" : EPISODE_STATUS_TEXT[latest.status] || latest.status}</span>
                              </>
                            ) : (
                              <span className="pl-latest-text">尚未创建期数</span>
                            )}
                          </div>
                          <div className="pl-project-actions" onClick={(e) => e.stopPropagation()}>
                            <button type="button" className="cm-btn cm-btn-sm cm-btn-ghost" onClick={() => void createEpisodeFor(project.id)}>
                              <IconPlus /> 新建期
                            </button>
                            <button type="button" className="cm-btn cm-btn-sm cm-btn-primary" onClick={() => onOpenProject(project)}>
                              进入工作台 →
                            </button>
                          </div>
                        </div>
                      </article>
                    );
                  })}
                  <button type="button" className="pl-project pl-project-new" onClick={() => setNewOpen(true)}>
                    <IconPlus />
                    <b>新建项目</b>
                    <span>从 16 个节目模板开始</span>
                  </button>
                </div>
              </section>

              <div className="pl-two-col">
                <section className="pl-panel">
                  <div className="pl-sec-head">
                    <h2 className="pl-h2">16 模板 · 3D 影库</h2>
                    <span className="pl-sec-sub">已完成 {tplDone}/{tplTotal}</span>
                    <a className="cm-btn cm-btn-ghost cm-btn-sm pl-sec-action" href="#/cinema/templates">
                      全部 {tplTotal} 支 →
                    </a>
                  </div>
                  <div className="pl-tpl-grid">
                    {(tpl?.templates || []).slice(0, 6).map((item: CinemaTemplate) => (
                      <a key={item.id} className="pl-tpl" href={`#/cinema/templates`} title={item.label}>
                        <div className="pl-tpl-thumb">
                          {item.poster_url ? (
                            <img src={mediaUrl(item.poster_url)} alt={item.label} />
                          ) : (
                            <span className="pl-tpl-letter">{String(item.label || "?").slice(0, 1)}</span>
                          )}
                          <span className={`pl-tpl-badge ${item.status === "done" ? "is-done" : ""}`}>
                            {item.status === "done" ? "3D" : `${item.clips?.done ?? 0}/${item.clips?.total ?? 0}`}
                          </span>
                        </div>
                        <div className="pl-tpl-name">{item.label}</div>
                        <div className="pl-tpl-sub cm-mono">{item.id}</div>
                      </a>
                    ))}
                  </div>
                </section>

                <section className="pl-panel">
                  <div className="pl-sec-head">
                    <h2 className="pl-h2">最近动态</h2>
                    <span className="pl-sec-sub">{jobs.length} 条任务记录</span>
                    <a className="cm-btn cm-btn-ghost cm-btn-sm pl-sec-action" href="#/cinema/jobs">
                      生成队列 →
                    </a>
                  </div>
                  <div className="pl-jobs">
                    {jobs.slice(0, 7).map((job) => (
                      <div key={job.id} className="pl-job">
                        <i className={`pl-mini-dot ${job.status === "done" ? "is-jade" : job.status === "failed" ? "is-red" : "is-gold"}`} />
                        <div className="pl-job-main">
                          <span className="pl-job-title">{jobLabel(job)}</span>
                          <span className="pl-job-sub cm-mono">{job.id} · {fmtDateTime(job.created_at)} · {relativeTime(job.created_at)}</span>
                        </div>
                        <span className={`cm-chip ${job.status === "done" ? "is-jade" : job.status === "failed" ? "is-red" : "is-gold"}`}>
                          {JOB_STATUS[job.status] || job.status}
                        </span>
                      </div>
                    ))}
                    {!jobs.length && <div className="cm-empty">还没有任务记录</div>}
                  </div>
                </section>
              </div>
            </>
          )}
        </main>
      </div>

      <SettingsModal open={settingsOpen} onClose={() => setSettingsOpen(false)} health={{ status: offline ? "offline" : "ok" }} />
      <NewProjectPanel
        open={newOpen}
        onClose={() => setNewOpen(false)}
        onCreated={(project) => {
          setNewOpen(false);
          onProjectCreated(project);
          notify(`项目「${project.name}」已创建`, "success");
        }}
      />
    </div>
  );

  async function createEpisodeFor(pid: string) {
    try {
      const ep = await api.createEpisode(pid, {});
      await load(true);
      notify(`已新建第 ${ep.no} 期：${ep.title}`, "success");
    } catch (err) {
      notify("新建期失败：" + (err instanceof Error ? err.message : String(err)), "error");
    }
  }
}

/* ---------------- 新建项目（新设计弹层） ---------------- */
function NewProjectPanel({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: (project: Project) => void;
}) {
  const notify = useToast();
  const [name, setName] = useState("");
  const [formatId, setFormatId] = useState(TEMPLATES[0]?.id || "roundtable_5min");
  const [aspect, setAspect] = useState(TEMPLATES[0]?.aspect || "16:9");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (open) {
      setBusy(false);
      setName("");
      setFormatId(TEMPLATES[0]?.id || "roundtable_5min");
      setAspect(TEMPLATES[0]?.aspect || "16:9");
    }
  }, [open]);

  const template = TEMPLATES.find((item) => item.id === formatId) || TEMPLATES[0];

  if (!open) return null;

  const submit = async () => {
    setBusy(true);
    try {
      const project = await api.createProject({
        name: name.trim() || "造片局 · 新节目",
        format_id: formatId,
        aspect,
      });
      onCreated(project);
    } catch (err) {
      notify("创建失败：" + (err instanceof Error ? err.message : String(err)), "error");
      setBusy(false);
    }
  };

  return (
    <div className="pl-modal-mask" onClick={onClose}>
      <div className="pl-modal" onClick={(e) => e.stopPropagation()} role="dialog" aria-modal="true">
        <div className="pl-modal-head">
          <div>
            <span className="cm-kicker">NEW PROJECT · 新节目</span>
            <h3 className="pl-modal-title">从一个模板开始</h3>
          </div>
          <button type="button" className="cm-icon-btn" onClick={onClose} aria-label="关闭">
            <IconClose />
          </button>
        </div>
        <div className="pl-modal-body">
          <label className="pl-field">
            <span className="pl-label">项目名称</span>
            <input
              className="pl-input"
              value={name}
              placeholder="例如：造片局 · 新节目"
              onChange={(e) => setName(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") void submit(); }}
              autoFocus
            />
          </label>
          <div className="pl-label">节目模板 · {TEMPLATES.length} 个</div>
          <div className="pl-tpl-picker">
            {TEMPLATES.map((item) => (
              <button
                key={item.id}
                type="button"
                className={"pl-tpl-option" + (item.id === formatId ? " is-active" : "")}
                onClick={() => { setFormatId(item.id); setAspect(item.aspect); }}
              >
                <span className="pl-tpl-option-name">{item.name}</span>
                <span className="pl-tpl-option-sub cm-mono">{item.id} · {item.aspect} · {fmtClock(item.target_duration)}</span>
                <span className="pl-tpl-option-dot" style={{ background: item.accent }} />
              </button>
            ))}
          </div>
          <div className="pl-modal-hint">
            当前选择：<b>{template?.name}</b> · {template?.aspect} · 目标 {fmtClock(template?.target_duration)} · 创建后自动初始化剧集 / 资产 / 分镜。
          </div>
        </div>
        <div className="pl-modal-foot">
          <button type="button" className="cm-btn cm-btn-ghost" onClick={onClose} disabled={busy}>
            取消
          </button>
          <button type="button" className="cm-btn cm-btn-primary" onClick={() => void submit()} disabled={busy}>
            {busy ? <Spinner size={13} /> : <IconPlus />}
            创建项目
          </button>
        </div>
      </div>
    </div>
  );
}

/* ---------------- 图标 ---------------- */
const S = {
  width: 16,
  height: 16,
  viewBox: "0 0 24 24",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.6,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
};
function IconGrid() {
  return (
    <svg {...S}>
      <rect x="3.5" y="3.5" width="7" height="7" rx="1.2" />
      <rect x="13.5" y="3.5" width="7" height="7" rx="1.2" />
      <rect x="3.5" y="13.5" width="7" height="7" rx="1.2" />
      <rect x="13.5" y="13.5" width="7" height="7" rx="1.2" />
    </svg>
  );
}
function IconFilm() {
  return (
    <svg {...S}>
      <rect x="3" y="5" width="18" height="14" rx="2" />
      <path d="M7 5v14M17 5v14M3 9h4M3 15h4M17 9h4M17 15h4" />
    </svg>
  );
}
function IconWorld() {
  return (
    <svg {...S}>
      <circle cx="12" cy="12" r="8.5" />
      <path d="M3.5 12h17M12 3.5c2.4 2.2 3.6 5.1 3.6 8.5s-1.2 6.3-3.6 8.5c-2.4-2.2-3.6-5.1-3.6-8.5s1.2-6.3 3.6-8.5Z" />
    </svg>
  );
}
function IconGear() {
  return (
    <svg {...S}>
      <circle cx="12" cy="12" r="3" />
      <path d="M19 12a7 7 0 0 0-.1-1.2l2-1.6-2-3.4-2.4 1a7 7 0 0 0-2-1.2L14 3h-4l-.5 2.6a7 7 0 0 0-2 1.2l-2.4-1-2 3.4 2 1.6A7 7 0 0 0 5 12c0 .4 0 .8.1 1.2l-2 1.6 2 3.4 2.4-1a7 7 0 0 0 2 1.2L10 21h4l.5-2.6a7 7 0 0 0 2-1.2l2.4 1 2-3.4-2-1.6c.1-.4.1-.8.1-1.2Z" />
    </svg>
  );
}
function IconRefresh() {
  return (
    <svg {...S} width={15} height={15}>
      <path d="M20 12a8 8 0 1 1-2.6-5.9" />
      <path d="M20 4.5V9h-4.5" />
    </svg>
  );
}
function IconPlus() {
  return (
    <svg {...S} width={13} height={13}>
      <path d="M12 5v14M5 12h14" />
    </svg>
  );
}
function IconClose() {
  return (
    <svg {...S} width={14} height={14}>
      <path d="M6 6l12 12M18 6 6 18" />
    </svg>
  );
}
