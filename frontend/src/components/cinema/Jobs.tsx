// Cinema Studio · 生成队列
// 运行环境状态（ComfyUI / MetaSo）+ 生成/合成任务列表：进度、实时日志、取消。
// 后端契约见 docs/V7_FRONTEND_PLATFORM.md §3：GET /cinema/jobs、GET /cinema/jobs/{id}、POST /cinema/jobs/{id}/cancel。
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { errorText } from "../../lib/api";
import {
  JOB_KIND_LABEL,
  JOB_STATUS_LABEL,
  cinemaApi,
  fmtDuration,
  type CinemaJob,
  type CinemaWorkers,
} from "../../lib/cinema";
import { useToast } from "../Toast";
import { IconAlert, IconChevronDown, IconRefresh } from "../icons";
import "../../styles/cinema-final.css";

const ACTIVE_STATUS: CinemaJob["status"][] = ["queued", "running"];

function isActive(job: CinemaJob): boolean {
  return ACTIVE_STATUS.includes(job.status);
}

function jobLogs(job: CinemaJob): string[] {
  return Array.isArray(job.logs) ? job.logs : [];
}

function jobShots(job: CinemaJob): string[] {
  return Array.isArray(job.shots) ? job.shots : [];
}

function parseTime(value: string | null | undefined): number {
  if (!value) return Number.NaN;
  const ts = Date.parse(value);
  return Number.isFinite(ts) ? ts : Number.NaN;
}

function pad2(value: number): string {
  return String(value).padStart(2, "0");
}

function fmtStamp(value: string | null | undefined): string {
  if (!value) return "";
  const ts = parseTime(value);
  if (!Number.isFinite(ts)) return String(value).replace("T", " ").slice(0, 19);
  const date = new Date(ts);
  return `${pad2(date.getMonth() + 1)}-${pad2(date.getDate())} ${pad2(date.getHours())}:${pad2(date.getMinutes())}:${pad2(date.getSeconds())}`;
}

function jobElapsed(job: CinemaJob): string {
  const start = parseTime(job.started_at);
  if (!Number.isFinite(start)) return "";
  const finish = parseTime(job.finished_at);
  const stop = Number.isFinite(finish) ? finish : isActive(job) ? Date.now() : Number.NaN;
  if (!Number.isFinite(stop)) return "";
  const seconds = Math.max(0, Math.round((stop - start) / 1000));
  return fmtDuration(seconds);
}

function jobTimeText(job: CinemaJob): string {
  const start = fmtStamp(job.started_at);
  const end = fmtStamp(job.finished_at);
  const elapsed = jobElapsed(job);
  if (start && end) {
    const sameDay = start.slice(0, 5) === end.slice(0, 5);
    return `${start} → ${sameDay ? end.slice(6) : end}${elapsed ? " · " + elapsed : ""}`;
  }
  if (start) return `${start}${elapsed ? " · 耗时 " + elapsed : ""}`;
  if (job.status === "queued") return "等待调度";
  return "未记录时间";
}

function jobProgress(job: CinemaJob): number {
  const value = Number(job.progress);
  if (!Number.isFinite(value)) return job.status === "done" ? 100 : 0;
  return Math.max(0, Math.min(100, Math.round(value)));
}

function sortJobs(list: CinemaJob[]): CinemaJob[] {
  return list.slice().sort((a, b) => {
    const at = parseTime(a.started_at);
    const bt = parseTime(b.started_at);
    const aHas = Number.isFinite(at);
    const bHas = Number.isFinite(bt);
    if (aHas && bHas && at !== bt) return bt - at;
    if (aHas !== bHas) return aHas ? -1 : 1;
    return String(b.id).localeCompare(String(a.id));
  });
}

function statusChipTone(status: CinemaJob["status"]): string {
  if (status === "running") return " is-gold";
  if (status === "done") return " is-jade";
  if (status === "failed") return " is-red";
  return "";
}

export function CinemaJobs() {
  const notify = useToast();

  const [jobs, setJobs] = useState<CinemaJob[]>([]);
  const [loading, setLoading] = useState(true);
  const [jobsError, setJobsError] = useState("");

  const [workers, setWorkers] = useState<CinemaWorkers | null>(null);
  const [workersError, setWorkersError] = useState("");
  const [workersLoading, setWorkersLoading] = useState(true);

  const [expanded, setExpanded] = useState<string[]>([]);
  const [confirming, setConfirming] = useState<string | null>(null);
  const [cancelling, setCancelling] = useState<string | null>(null);

  const jobsBusy = useRef(false);
  const workersBusy = useRef(false);
  const logRefs = useRef<Record<string, HTMLPreElement | null>>({});
  const stickToBottom = useRef<Record<string, boolean>>({});

  const loadJobs = useCallback(async (silent: boolean) => {
    if (jobsBusy.current) return;
    jobsBusy.current = true;
    try {
      const res = await cinemaApi.jobs();
      setJobs(sortJobs(res.jobs || []));
      setJobsError("");
    } catch (err) {
      setJobsError(errorText(err));
    } finally {
      jobsBusy.current = false;
      if (!silent) setLoading(false);
    }
  }, []);

  const loadWorkers = useCallback(async () => {
    if (workersBusy.current) return;
    workersBusy.current = true;
    setWorkersLoading(true);
    try {
      const res = await cinemaApi.workers();
      setWorkers(res);
      setWorkersError("");
    } catch (err) {
      setWorkersError(errorText(err));
    } finally {
      workersBusy.current = false;
      setWorkersLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadJobs(false);
  }, [loadJobs]);

  useEffect(() => {
    void loadWorkers();
    const timer = window.setInterval(() => {
      if (!document.hidden) void loadWorkers();
    }, 10000);
    return () => window.clearInterval(timer);
  }, [loadWorkers]);

  const activeCount = useMemo(() => jobs.filter(isActive).length, [jobs]);

  // 有活跃任务 1.5s 轮询，否则 8s 轮询（页面不可见时跳过）。
  useEffect(() => {
    const delay = activeCount > 0 ? 1500 : 8000;
    const timer = window.setInterval(() => {
      if (!document.hidden) void loadJobs(true);
    }, delay);
    return () => window.clearInterval(timer);
  }, [activeCount, loadJobs]);

  // 展开的任务自动滚到日志底部（用户手动上翻时暂停跟随）。
  useEffect(() => {
    expanded.forEach((id) => {
      const el = logRefs.current[id];
      if (!el) return;
      if (stickToBottom.current[id] === false) return;
      el.scrollTop = el.scrollHeight;
    });
  }, [jobs, expanded]);

  useEffect(() => {
    if (!confirming) return;
    const timer = window.setTimeout(() => setConfirming(null), 4000);
    return () => window.clearTimeout(timer);
  }, [confirming]);

  const refreshAll = useCallback(() => {
    void loadJobs(false);
    void loadWorkers();
  }, [loadJobs, loadWorkers]);

  const toggleExpanded = (id: string) => {
    setConfirming(null);
    setExpanded((list) => (list.includes(id) ? list.filter((item) => item !== id) : [...list, id]));
  };

  const runCancel = useCallback(
    async (job: CinemaJob) => {
      setCancelling(job.id);
      try {
        const updated = await cinemaApi.cancelJob(job.id);
        setJobs((list) => sortJobs(list.map((item) => (item.id === updated.id ? updated : item))));
        notify(`已请求取消：${JOB_KIND_LABEL[job.kind] || job.kind}`, "info");
      } catch (err) {
        notify("取消失败：" + errorText(err), "error");
      } finally {
        setCancelling(null);
        void loadJobs(true);
      }
    },
    [loadJobs, notify]
  );

  const handleCancelClick = (job: CinemaJob) => {
    if (cancelling) return;
    if (confirming !== job.id) {
      setConfirming(job.id);
      return;
    }
    setConfirming(null);
    void runCancel(job);
  };

  const comfyOnline = workers ? workers.comfyui : null;
  const metasoReady = workers ? workers.metaso_key : null;
  const comfyQueue = workers && typeof workers.comfyui_queue === "number" ? workers.comfyui_queue : null;

  return (
    <div className="cm-page cm-jobs">
      <header className="cm-page-head cm-jobs-head">
        <div className="cm-head-main">
          <div className="cm-kicker">RENDER QUEUE</div>
          <h1 className="cm-h1">生成队列</h1>
          <p className="cm-sub">
            {activeCount > 0 ? `${activeCount} 个活跃任务` : "当前没有活跃任务"} · 共 {jobs.length} 条任务记录 ·{" "}
            {activeCount > 0 ? "1.5 秒" : "8 秒"}自动刷新
          </p>
        </div>
        <div className="cm-head-actions">
          <button
            type="button"
            className="cm-btn cm-jobs-refresh"
            onClick={refreshAll}
            disabled={loading || workersLoading}
          >
            <IconRefresh size={15} className={loading || workersLoading ? "is-spin" : undefined} />
            刷新
          </button>
        </div>
      </header>

      <section className="cm-card cm-card-pad cm-jobs-env">
        <div className="cm-jobs-env-head">
          <div>
            <h2 className="cm-card-title">运行环境</h2>
            <p className="cm-card-sub">ComfyUI 本地渲染与 MetaSo 云端密钥状态 · 每 10 秒自动检测</p>
          </div>
          <span
            className={
              "cm-chip cm-jobs-env-chip" +
              (workers
                ? comfyOnline && metasoReady
                  ? " is-jade"
                  : comfyOnline || metasoReady
                    ? " is-gold"
                    : " is-red"
                : "")
            }
          >
            <i
              className={
                "cm-status-dot" +
                (workers
                  ? comfyOnline && metasoReady
                    ? " is-done"
                    : comfyOnline || metasoReady
                      ? " is-running"
                      : " is-failed"
                  : " is-running")
              }
              aria-hidden="true"
            />
            {workers
              ? comfyOnline && metasoReady
                ? "双环境就绪"
                : comfyOnline || metasoReady
                  ? "单环境就绪"
                  : "环境离线"
              : "检测中"}
          </span>
        </div>

        {workersError ? (
          <div className="cm-jobs-env-error" role="alert">
            <IconAlert size={14} />
            <span>无法读取运行环境：{workersError}</span>
            <button type="button" className="cm-btn cm-btn-ghost" onClick={() => void loadWorkers()}>
              重试
            </button>
          </div>
        ) : (
          <div className="cm-jobs-env-grid">
            <div className="cm-jobs-env-item">
              <i
                className={
                  "cm-env-dot " +
                  (comfyOnline === null ? "is-warn" : comfyOnline ? "is-on" : "is-off")
                }
                aria-hidden="true"
              />
              <div className="cm-jobs-env-body">
                <div className="cm-jobs-env-name">
                  ComfyUI
                  <span className="cm-jobs-env-en">LOCAL H3</span>
                </div>
                <div className="cm-jobs-env-state">
                  {comfyOnline === null ? "检测中…" : comfyOnline ? "在线" : "离线"}
                  {comfyOnline && comfyQueue !== null ? ` · 队列 ${comfyQueue}` : ""}
                </div>
              </div>
              <div className="cm-jobs-env-meta cm-mono">
                {comfyOnline && comfyQueue !== null ? `${comfyQueue} job` : "—"}
              </div>
            </div>

            <div className="cm-jobs-env-item">
              <i
                className={
                  "cm-env-dot " +
                  (metasoReady === null ? "is-warn" : metasoReady ? "is-on" : "is-off")
                }
                aria-hidden="true"
              />
              <div className="cm-jobs-env-body">
                <div className="cm-jobs-env-name">
                  MetaSo Key
                  <span className="cm-jobs-env-en">CLOUD H3</span>
                </div>
                <div className="cm-jobs-env-state">
                  {metasoReady === null ? "检测中…" : metasoReady ? "已配置" : "未配置"}
                </div>
              </div>
              <div className="cm-jobs-env-meta cm-mono">{metasoReady === null ? "—" : metasoReady ? "API" : "缺密钥"}</div>
            </div>
          </div>
        )}

        {!workersError && (comfyOnline === false || metasoReady === false) && (
          <div className="cm-jobs-env-hints">
            {comfyOnline === false && (
              <div className="cm-jobs-env-hint">
                <IconAlert size={13} />
                本地生成需先启动 ComfyUI（127.0.0.1:8188），否则镜头台只能排队等待。
              </div>
            )}
            {metasoReady === false && (
              <div className="cm-jobs-env-hint">
                <IconAlert size={13} />
                云端生成需在「设置」中配置 MetaSo Key；当前只能使用本地 ComfyUI。
              </div>
            )}
          </div>
        )}
      </section>

      {jobsError && (
        <div className="cm-error cm-jobs-error" role="alert">
          <IconAlert size={15} />
          <span>无法读取任务列表：{jobsError}</span>
          <button type="button" className="cm-btn cm-btn-ghost" onClick={() => void loadJobs(false)}>
            重试
          </button>
        </div>
      )}

      {loading ? (
        <div className="cm-loading cm-jobs-loading">正在连接生成队列…</div>
      ) : jobs.length === 0 ? (
        <div className="cm-empty cm-jobs-empty">
          <div className="cm-jobs-empty-title">暂无任务：去镜头台选镜生成，或在剪辑台合成</div>
          <div className="cm-jobs-empty-actions">
            <a className="cm-btn cm-btn-ghost" href="#/cinema/shots">
              去镜头台
            </a>
            <a className="cm-btn cm-btn-ghost" href="#/cinema/assemble">
              去剪辑台
            </a>
          </div>
        </div>
      ) : (
        <div className="cm-jobs-list">
          {jobs.map((job) => {
            const open = expanded.includes(job.id);
            const progress = jobProgress(job);
            const shots = jobShots(job);
            const logs = jobLogs(job);
            const active = isActive(job);
            return (
              <article
                key={job.id}
                className={
                  "cm-card cm-card-pad cm-jobs-item is-" +
                  job.status +
                  (open ? " is-expanded" : "") +
                  (active ? " is-active" : "")
                }
                role="button"
                tabIndex={0}
                aria-expanded={open}
                onClick={() => toggleExpanded(job.id)}
                onKeyDown={(event) => {
                  if (event.target !== event.currentTarget) return;
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    toggleExpanded(job.id);
                  }
                }}
              >
                <div className="cm-jobs-row">
                  <span className="cm-jobs-kind">{JOB_KIND_LABEL[job.kind] || job.kind}</span>
                  <span className={"cm-chip cm-jobs-pill" + statusChipTone(job.status)}>
                    {(job.status === "running" || job.status === "queued") && (
                      <i
                        className={"cm-status-dot" + (job.status === "running" ? " is-running" : "")}
                        aria-hidden="true"
                      />
                    )}
                    {JOB_STATUS_LABEL[job.status] || job.status}
                  </span>
                  <span className="cm-jobs-spacer" />
                  <span className="cm-jobs-time cm-mono">{jobTimeText(job)}</span>
                  {active && (
                    <button
                      type="button"
                      className={
                        "cm-btn cm-btn-ghost cm-btn-sm cm-jobs-cancel" +
                        (confirming === job.id ? " is-confirming" : "")
                      }
                      onClick={(event) => {
                        event.stopPropagation();
                        handleCancelClick(job);
                      }}
                      onKeyDown={(event) => event.stopPropagation()}
                      disabled={cancelling === job.id}
                    >
                      {cancelling === job.id
                        ? "取消中…"
                        : confirming === job.id
                          ? "确认取消？"
                          : "取消"}
                    </button>
                  )}
                  <span className={"cm-jobs-chevron" + (open ? " is-open" : "")} aria-hidden="true">
                    <IconChevronDown size={15} />
                  </span>
                </div>

                {job.message && <div className="cm-jobs-msg">{job.message}</div>}

                <div className="cm-jobs-progress">
                  <div className={"cm-progress cm-jobs-progress-bar" + (job.status === "running" ? " is-running" : "")}>
                    <i style={{ width: progress + "%" }} />
                  </div>
                  <span className="cm-jobs-bar-pct cm-mono">{progress}%</span>
                </div>

                {shots.length > 0 && (
                  <div className="cm-jobs-chips">
                    <span className="cm-jobs-chips-label">镜头</span>
                    {shots.slice(0, 8).map((sid) => (
                      <span key={sid} className="cm-chip cm-jobs-shot cm-mono">
                        {sid}
                      </span>
                    ))}
                    {shots.length > 8 && (
                      <span className="cm-jobs-shot-more cm-mono">+{shots.length - 8}</span>
                    )}
                  </div>
                )}

                {open && (
                  <div className="cm-jobs-logs" onClick={(event) => event.stopPropagation()}>
                    <div className="cm-jobs-logs-head">
                      <span>运行日志</span>
                      <span className="cm-mono cm-jobs-logs-count">{logs.length} 行</span>
                    </div>
                    <pre
                      className="cm-scroll cm-mono cm-jobs-log"
                      ref={(el) => {
                        logRefs.current[job.id] = el;
                      }}
                      onScroll={(event) => {
                        const el = event.currentTarget;
                        stickToBottom.current[job.id] =
                          el.scrollHeight - el.scrollTop - el.clientHeight < 48;
                      }}
                    >
                      {logs.length > 0 ? logs.join("\n") : "暂无日志输出…"}
                    </pre>
                  </div>
                )}
              </article>
            );
          })}
        </div>
      )}
    </div>
  );
}
