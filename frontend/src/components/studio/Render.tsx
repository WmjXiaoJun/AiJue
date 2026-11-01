// Project Studio · 生成台（#/episodes/<eid>/generate）
// 任务队列（2s 轮询）+ 费用分布：状态/类型筛选、搜索、失败重试、成片/镜头视频 lightbox、快捷跳转。
// 数据：GET /jobs?episode_id=、GET /episodes/{id}/costs、POST /jobs/{id}/retry。
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { CostReport, Job, Shot } from "../../types";
import type { StudioTabProps } from "../../lib/studio";
import {
  JOB_STATUS_TEXT,
  JOB_TYPE_TEXT,
  errorText,
  fmtClock,
  fmtDateTime,
  fmtMoney,
  isActiveJob,
  mediaUrl,
  studioApi,
  toneOfStatus,
} from "../../lib/studio";
import { SHOT_TYPE_LABEL } from "../../lib/format";
import { useToast } from "../Toast";
import { Spinner } from "../ui";
import {
  IconAlert,
  IconClose,
  IconFilm,
  IconRefresh,
  IconSearch,
  IconSpark,
  IconVideo,
} from "../icons";
import "../../styles/studio-b.css";

type RenderStatusFilter = "all" | "active" | "failed" | "done";

const STATUS_FILTERS: { key: RenderStatusFilter; label: string }[] = [
  { key: "all", label: "全部" },
  { key: "active", label: "进行中" },
  { key: "failed", label: "失败" },
  { key: "done", label: "已完成" },
];

const TONE_CLASS: Record<string, string> = {
  gold: "is-gold",
  jade: "is-jade",
  red: "is-red",
  slate: "is-slate",
};

const PAGE_SIZE = 40;

function shotNo(no: number | null | undefined): string {
  return "#" + String(Math.max(0, Math.floor(Number(no) || 0))).padStart(3, "0");
}

function statusTagClass(status: string): string {
  return "st-tag " + (TONE_CLASS[toneOfStatus(status)] || "is-slate");
}

function jobMatchesStatus(job: Job, filter: RenderStatusFilter): boolean {
  if (filter === "all") return true;
  if (filter === "active") return isActiveJob(job);
  if (filter === "failed") return job.status === "failed";
  return job.status === "done";
}

function jobProgress(job: Job): number {
  if (job.status === "done") return 100;
  const value = Number(job.progress);
  return Number.isFinite(value) ? Math.max(0, Math.min(100, value)) : 0;
}

function jobElapsed(job: Job): string {
  const start = Date.parse(job.created_at);
  if (!Number.isFinite(start)) return "—";
  const end = isActiveJob(job) ? Date.now() : job.updated_at ? Date.parse(job.updated_at) : Number.NaN;
  if (!Number.isFinite(end) || end < start) return "—";
  return fmtClock(Math.round((end - start) / 1000));
}

function jobTypeText(type: string): string {
  return JOB_TYPE_TEXT[type] || type || "任务";
}

export function StudioRender({ episode, onNavigate }: StudioTabProps) {
  const notify = useToast();

  const [jobs, setJobs] = useState<Job[] | null>(null);
  const [costs, setCosts] = useState<CostReport | null>(null);
  const [costsError, setCostsError] = useState("");
  const [shots, setShots] = useState<Shot[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [refreshing, setRefreshing] = useState(false);
  const [statusFilter, setStatusFilter] = useState<RenderStatusFilter>("all");
  const [typeFilter, setTypeFilter] = useState("all");
  const [search, setSearch] = useState("");
  const [visible, setVisible] = useState(PAGE_SIZE);
  const [retrying, setRetrying] = useState<string | null>(null);
  const [lightbox, setLightbox] = useState<Job | null>(null);

  const aliveRef = useRef(true);
  const inFlightRef = useRef(false);

  useEffect(() => {
    aliveRef.current = true;
    return () => {
      aliveRef.current = false;
    };
  }, []);

  const load = useCallback(
    async (silent: boolean) => {
      if (inFlightRef.current) return;
      inFlightRef.current = true;
      if (!silent) setLoading(true);
      try {
        const [jobList, cost, shotList] = await Promise.all([
          studioApi.jobs({ episode_id: episode.id }),
          studioApi.costs(episode.id).catch((err) => {
            if (aliveRef.current) setCostsError(errorText(err));
            return null;
          }),
          studioApi.shots(episode.id).catch(() => [] as Shot[]),
        ]);
        if (!aliveRef.current) return;
        setJobs(jobList);
        setShots(shotList);
        if (cost) {
          setCosts(cost);
          setCostsError("");
        }
        setError("");
      } catch (err) {
        if (!aliveRef.current) return;
        setError(errorText(err));
      } finally {
        inFlightRef.current = false;
        if (aliveRef.current) setLoading(false);
      }
    },
    [episode.id],
  );

  useEffect(() => {
    setJobs(null);
    void load(false);
  }, [load]);

  const jobList = jobs ?? [];
  const activeCount = useMemo(() => jobList.filter(isActiveJob).length, [jobList]);

  // 有活动任务时 2s 轮询（队列/进度/费用实时更新）
  useEffect(() => {
    if (!activeCount) return;
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") void load(true);
    }, 2000);
    return () => window.clearInterval(timer);
  }, [activeCount, load]);

  const refresh = useCallback(async () => {
    setRefreshing(true);
    try {
      await load(true);
      notify("已刷新任务与费用", "success");
    } finally {
      if (aliveRef.current) setRefreshing(false);
    }
  }, [load, notify]);

  const shotMap = useMemo(() => new Map(shots.map((shot) => [shot.id, shot])), [shots]);

  const doneCount = jobList.filter((job) => job.status === "done").length;
  const failedCount = jobList.filter((job) => job.status === "failed").length;
  const costsTotal = costs ? Number(costs.total) || 0 : jobList.reduce((sum, job) => sum + (Number(job.cost) || 0), 0);
  const types = useMemo(() => Array.from(new Set(jobList.map((job) => job.type).filter(Boolean))).sort(), [jobList]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    const list = jobList.filter((job) => {
      if (!jobMatchesStatus(job, statusFilter)) return false;
      if (typeFilter !== "all" && job.type !== typeFilter) return false;
      if (!q) return true;
      const shot = job.shot_id ? shotMap.get(job.shot_id) : undefined;
      const haystack = [
        job.id,
        job.type,
        jobTypeText(job.type),
        job.status,
        JOB_STATUS_TEXT[job.status] || "",
        job.message,
        job.error,
        job.shot_id,
        job.character_id,
        job.output_url,
        shot ? shotNo(shot.no) : "",
        shot ? SHOT_TYPE_LABEL[shot.type] || shot.type : "",
        shot?.line,
        shot?.speaker_name,
      ]
        .filter((part): part is string => typeof part === "string" && part.length > 0)
        .join(" ")
        .toLowerCase();
      return haystack.includes(q);
    });
    return list.slice().sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)));
  }, [jobList, search, shotMap, statusFilter, typeFilter]);

  useEffect(() => {
    setVisible(PAGE_SIZE);
  }, [search, statusFilter, typeFilter]);

  const shown = filtered.slice(0, visible);

  const byType = useMemo(() => {
    const entries = Object.entries(costs?.by_type || {}).map(([key, value]) => [key, Number(value) || 0] as const);
    return entries.sort((a, b) => b[1] - a[1]);
  }, [costs]);

  const costMax = Math.max(1, ...byType.map(([, amount]) => amount));
  const costItems = useMemo(() => {
    const items = (costs?.items || []).slice();
    return items.sort((a, b) => (Number(b.amount) || 0) - (Number(a.amount) || 0)).slice(0, 8);
  }, [costs]);
  const costItemMax = Math.max(1, ...costItems.map((item) => Number(item.amount) || 0));

  const handleRetry = useCallback(
    async (job: Job) => {
      if (retrying) return;
      setRetrying(job.id);
      try {
        await studioApi.retryJob(job.id);
        notify(`已重新提交任务 ${job.id}`, "success");
        await load(true);
      } catch (err) {
        notify("重试失败：" + errorText(err), "error");
      } finally {
        if (aliveRef.current) setRetrying(null);
      }
    },
    [load, notify, retrying],
  );

  useEffect(() => {
    if (!lightbox) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setLightbox(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [lightbox]);

  const jobTitle = (job: Job): { main: string; sub: string } => {
    const typeText = jobTypeText(job.type);
    if (job.type === "render") {
      return { main: `成片渲染 · 第 ${episode.no || 1} 期`, sub: typeText };
    }
    if (job.shot_id) {
      const shot = shotMap.get(job.shot_id);
      if (shot) {
        const speaker = shot.speaker_name || (shot.line ? "有台词" : "画外");
        return {
          main: `${shotNo(shot.no)} · ${SHOT_TYPE_LABEL[shot.type] || shot.type || "镜头"}`,
          sub: `${speaker} · ${typeText}`,
        };
      }
      return { main: job.shot_id, sub: typeText };
    }
    if (job.character_id) return { main: `角色 ${job.character_id}`, sub: typeText };
    return { main: typeText, sub: job.id };
  };

  return (
    <div className="st-page st-render">
      <header className="st-page-head">
        <div className="st-head-main">
          <span className="st-kicker">RENDER · 生成台</span>
          <h1 className="st-h1">生成台</h1>
          <p className="st-sub">
            {jobList.length > 0 ? (
              <>
                共 {jobList.length} 个任务 · 进行中 {activeCount} · 失败 {failedCount} · 累计费用 {fmtMoney(costsTotal)}
              </>
            ) : (
              <>镜头生成 / 整片渲染 / 多语种 / 导出与发布任务统一在这里排队与追踪</>
            )}
            {" "}
            <span className="st-board-ep cm-mono">{episode.id}</span>
          </p>
        </div>
        <div className="st-head-actions">
          <button type="button" className="cm-icon-btn" onClick={() => void refresh()} disabled={refreshing || loading} title="刷新任务">
            {refreshing || loading ? <Spinner size={13} /> : <IconRefresh />}
          </button>
          <button type="button" className="cm-btn cm-btn-sm" onClick={() => onNavigate("storyboard")}>
            <IconSpark size={14} /> 去分镜台
          </button>
          <button type="button" className="cm-btn cm-btn-sm" onClick={() => onNavigate("finish")}>
            <IconFilm size={14} /> 去成片房
          </button>
        </div>
      </header>

      <section className="st-kpis">
        <div className="st-kpi">
          <span className="st-kpi-label">Jobs</span>
          <div className="st-kpi-value">
            {jobList.length}
            <small>个任务</small>
          </div>
          <div className="st-kpi-hint">{doneCount} 个已完成</div>
        </div>
        <div className="st-kpi">
          <span className="st-kpi-label">Running</span>
          <div className={"st-kpi-value" + (activeCount > 0 ? " is-gold" : "")}>
            {activeCount}
            <small>进行中</small>
          </div>
          <div className="st-kpi-hint">{activeCount > 0 ? "2s 自动轮询" : "队列空闲"}</div>
        </div>
        <div className="st-kpi">
          <span className="st-kpi-label">Failed</span>
          <div className={"st-kpi-value" + (failedCount > 0 ? " is-red" : " is-jade")}>
            {failedCount}
            <small>失败</small>
          </div>
          <div className="st-kpi-hint">{failedCount > 0 ? "可单条重试" : "无失败任务"}</div>
        </div>
        <div className="st-kpi">
          <span className="st-kpi-label">Cost</span>
          <div className="st-kpi-value is-gold">
            {fmtMoney(costsTotal)}
            <small>{costs?.currency || "CNY"}</small>
          </div>
          <div className="st-kpi-hint">本集累计费用</div>
        </div>
      </section>

      {error && !jobs && (
        <div className="st-error">
          <IconAlert size={16} />
          <span>任务读取失败：{error}</span>
          <button type="button" className="cm-btn cm-btn-sm" onClick={() => void load(false)}>
            重试
          </button>
        </div>
      )}

      <section className="st-panel st-render-cost">
        <div className="st-panel-head">
          <h3 className="st-panel-title">费用分布</h3>
          <p className="st-panel-sub">
            {costs ? `按类型汇总 · ${costs.currency || "CNY"} · 共 ${(costs.items || []).length} 项明细` : "费用数据读取中…"}
          </p>
          <div className="st-panel-right">
            <span className="st-tag is-gold cm-mono">{fmtMoney(costsTotal)}</span>
          </div>
        </div>
        <div className="st-panel-body">
          {costsError && (
            <div className="st-error st-render-cost-error">
              <IconAlert size={14} />
              <span>费用接口不可用：{costsError}</span>
            </div>
          )}
          <div className="st-render-cost-grid">
            <div className="st-render-cost-types">
              <span className="st-label">By Type</span>
              {byType.length === 0 ? (
                <div className="st-empty st-render-cost-empty">
                  <span>暂无费用明细（后端 mock 计费或任务尚未产生费用）</span>
                </div>
              ) : (
                byType.map(([key, amount]) => (
                  <div key={key} className="st-render-cost-row">
                    <span className="st-render-cost-label">{jobTypeText(key)}</span>
                    <div className="st-render-cost-bar">
                      <i style={{ width: `${(amount / costMax) * 100}%` }} />
                    </div>
                    <b className="cm-mono">{fmtMoney(amount)}</b>
                  </div>
                ))
              )}
            </div>
            <div className="st-render-cost-items">
              <span className="st-label">Top 明细</span>
              {costItems.length === 0 ? (
                <div className="st-hint">暂无费用条目</div>
              ) : (
                costItems.map((item, index) => (
                  <div key={item.label + index} className="st-render-cost-item">
                    <span className="st-render-cost-item-label" title={item.label}>
                      {item.label}
                    </span>
                    <div className="st-render-cost-bar is-thin">
                      <i style={{ width: `${((Number(item.amount) || 0) / costItemMax) * 100}%` }} />
                    </div>
                    <b className="cm-mono">{fmtMoney(item.amount)}</b>
                  </div>
                ))
              )}
              {(costs?.items || []).length > costItems.length && (
                <span className="st-hint">共 {(costs?.items || []).length} 项，仅展示前 {costItems.length} 项</span>
              )}
            </div>
          </div>
        </div>
      </section>

      <div className="st-toolbar st-render-toolbar">
        <label className="st-search">
          <IconSearch size={14} />
          <input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="搜索任务 / 镜号 / 消息"
            aria-label="搜索任务"
          />
          {search && (
            <button type="button" className="st-board-clear" onClick={() => setSearch("")} aria-label="清空搜索">
              <IconClose size={12} />
            </button>
          )}
        </label>
        <div className="st-seg" role="tablist" aria-label="状态筛选">
          {STATUS_FILTERS.map((item) => (
            <button
              key={item.key}
              type="button"
              className={statusFilter === item.key ? "is-active" : ""}
              onClick={() => setStatusFilter(item.key)}
            >
              {item.label}
            </button>
          ))}
        </div>
        <select
          className="cm-select st-render-select"
          value={typeFilter}
          onChange={(event) => setTypeFilter(event.target.value)}
          aria-label="任务类型"
        >
          <option value="all">全部类型（{types.length}）</option>
          {types.map((type) => (
            <option key={type} value={type}>
              {jobTypeText(type)}
            </option>
          ))}
        </select>
        <div className="st-toolbar-right">
          <span className="st-board-count cm-mono">
            {filtered.length} / {jobList.length}
          </span>
        </div>
      </div>

      <section className="st-panel st-render-jobs">
        <div className="st-panel-head">
          <h3 className="st-panel-title">任务队列</h3>
          <p className="st-panel-sub">
            {activeCount > 0 ? "有任务进行中 · 2s 自动刷新" : "队列空闲"} · 失败任务可重试，完成的成片/镜头可直接播放
          </p>
          <div className="st-panel-right">
            <span className={`st-tag ${activeCount > 0 ? "is-gold" : "is-slate"}`}>
              {activeCount > 0 ? activeCount + " 进行中" : "空闲"}
            </span>
          </div>
        </div>
        <div className="st-panel-body is-flush">
          {loading && !jobs ? (
            <div className="st-loading">
              <Spinner size={15} /> 正在读取任务…
            </div>
          ) : jobList.length === 0 ? (
            <div className="st-empty st-render-empty">
              <IconSpark size={30} />
              <b>该期还没有生成任务</b>
              <span>去分镜台提交单镜生成 / 动态生成，或到成片房渲染整片，任务会实时出现在这里。</span>
              <div className="st-board-empty-actions">
                <button type="button" className="cm-btn cm-btn-sm" onClick={() => onNavigate("storyboard")}>
                  去分镜台
                </button>
                <button type="button" className="cm-btn cm-btn-sm" onClick={() => onNavigate("finish")}>
                  去成片房
                </button>
              </div>
            </div>
          ) : filtered.length === 0 ? (
            <div className="st-empty st-render-empty">
              <IconSearch size={26} />
              <b>没有符合条件的任务</b>
              <span>换个关键词，或把筛选切回「全部」。</span>
            </div>
          ) : (
            <>
              <div className="st-render-table-wrap">
                <table className="st-table st-render-table">
                  <thead>
                    <tr>
                      <th>任务</th>
                      <th style={{ width: 96 }}>类型</th>
                      <th style={{ width: 88 }}>状态</th>
                      <th style={{ width: 132 }}>进度</th>
                      <th>消息 / 错误</th>
                      <th style={{ width: 118 }}>时间 · 耗时</th>
                      <th style={{ width: 76 }}>费用</th>
                      <th style={{ width: 84 }}>操作</th>
                    </tr>
                  </thead>
                  <tbody>
                    {shown.map((job) => {
                      const title = jobTitle(job);
                      const progress = jobProgress(job);
                      const active = isActiveJob(job);
                      const message = job.status === "failed" ? job.error || job.message || "" : job.message || "";
                      return (
                        <tr key={job.id} className={active ? "is-active" : ""}>
                          <td className="st-cell-main">
                            <div className="st-render-job-title">
                              <b>{title.main}</b>
                              <span className="st-render-job-id cm-mono">{job.id}</span>
                            </div>
                            <span className="st-render-job-sub">{title.sub}</span>
                          </td>
                          <td>
                            <span className="st-render-type">{jobTypeText(job.type)}</span>
                          </td>
                          <td>
                            <span className={statusTagClass(job.status)}>
                              {active && <i className="st-board-dot" />}
                              {JOB_STATUS_TEXT[job.status] || job.status}
                            </span>
                          </td>
                          <td>
                            <div className="st-render-progress">
                              <div className={"cm-progress" + (active ? " is-running" : "")}>
                                <i style={{ width: `${Math.max(progress, job.status === "failed" ? 2 : 0)}%` }} />
                              </div>
                              <span className="cm-mono">{Math.round(progress)}%</span>
                            </div>
                          </td>
                          <td>
                            {message ? (
                              <span
                                className={"st-render-msg" + (job.status === "failed" ? " is-error" : "")}
                                title={message}
                              >
                                {message}
                              </span>
                            ) : (
                              <span className="st-hint">—</span>
                            )}
                          </td>
                          <td className="cm-mono st-render-time">
                            <span>{fmtDateTime(job.created_at)}</span>
                            <span className="st-hint">{jobElapsed(job)}</span>
                          </td>
                          <td className="cm-mono st-render-cost-cell">{fmtMoney(job.cost)}</td>
                          <td className="st-cell-actions">
                            {job.status === "failed" ? (
                              <button
                                type="button"
                                className="cm-btn cm-btn-sm"
                                onClick={() => void handleRetry(job)}
                                disabled={retrying === job.id}
                                title="重新提交该任务"
                              >
                                {retrying === job.id ? <Spinner size={12} /> : <IconRefresh size={12} />} 重试
                              </button>
                            ) : job.status === "done" && job.output_url ? (
                              <button
                                type="button"
                                className="cm-btn cm-btn-ghost cm-btn-sm"
                                onClick={() => setLightbox(job)}
                                title="播放产物"
                              >
                                <IconVideo size={12} /> 播放
                              </button>
                            ) : (
                              <span className="st-hint">—</span>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              <div className="st-render-foot">
                <span className="cm-mono">
                  显示 {shown.length} / {filtered.length} 条
                </span>
                {filtered.length > visible ? (
                  <button type="button" className="cm-btn cm-btn-sm" onClick={() => setVisible((prev) => prev + PAGE_SIZE)}>
                    加载更多（剩余 {filtered.length - visible}）
                  </button>
                ) : (
                  <span className="st-hint">已全部加载</span>
                )}
              </div>
            </>
          )}
        </div>
      </section>

      {lightbox && (
        <div className="st-lightbox st-render-lightbox" onClick={() => setLightbox(null)}>
          <div className="st-render-lightbox-inner" onClick={(event) => event.stopPropagation()}>
            <div className="st-render-lightbox-head">
              <div>
                <b>{jobTitle(lightbox).main}</b>
                <span className="st-render-lightbox-sub cm-mono">
                  {lightbox.id} · {jobTypeText(lightbox.type)} · {fmtDateTime(lightbox.updated_at || lightbox.created_at)}
                </span>
              </div>
              <button type="button" className="st-drawer-close" onClick={() => setLightbox(null)} aria-label="关闭">
                <IconClose size={16} />
              </button>
            </div>
            <video
              className="st-render-lightbox-video"
              src={mediaUrl(lightbox.output_url || "")}
              controls
              autoPlay
              playsInline
              preload="metadata"
            />
            <div className="st-render-lightbox-foot">
              <a className="cm-btn cm-btn-sm" href={mediaUrl(lightbox.output_url || "")} target="_blank" rel="noreferrer">
                新窗口打开
              </a>
              <span className="st-hint">{lightbox.output_url}</span>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
