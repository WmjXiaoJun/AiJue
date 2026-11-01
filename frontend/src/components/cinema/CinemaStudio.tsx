import { useCallback, useEffect, useMemo, useState } from "react";
import type { CinemaOverview, CinemaTab, CinemaTemplatesSummary, CinemaWorkers } from "../../lib/cinema";
import { TEMPLATE_3D_TOTAL, cinemaApi } from "../../lib/cinema";
import { useToast } from "../Toast";
import { Spinner } from "../ui";
import { IconTemplate } from "../icons";
import { CinemaOverviewPage } from "./Overview";
import { CinemaTemplates } from "./Templates";
import { CinemaShots } from "./Shots";
import { CinemaJobs } from "./Jobs";
import { CinemaAssemble } from "./Assemble";
import { CinemaFinal } from "./Final";
import "../../styles/cinema.css";

const TAB_KEYS: CinemaTab[] = ["overview", "templates", "shots", "jobs", "assemble", "final"];

const TAB_META: Record<CinemaTab, { label: string; en: string; no: string; icon: JSX.Element }> = {
  overview: { label: "总览", en: "OVERVIEW", no: "01", icon: <IconGrid /> },
  templates: { label: "模板库 · 3D", en: "TEMPLATE STUDIO", no: "02", icon: <IconTemplate size={17} /> },
  shots: { label: "镜头台", en: "SHOT WORKBENCH", no: "03", icon: <IconFrames /> },
  jobs: { label: "生成队列", en: "RENDER QUEUE", no: "04", icon: <IconPulse /> },
  assemble: { label: "剪辑台", en: "EDIT & MASTER", no: "05", icon: <IconSliders /> },
  final: { label: "成片 & QC", en: "DELIVERY", no: "06", icon: <IconPlay /> },
};

function tabFromHash(): CinemaTab {
  const match = window.location.hash.match(/^#\/cinema\/?([a-z]*)/i);
  const key = (match?.[1] || "").toLowerCase() as CinemaTab;
  return TAB_KEYS.includes(key) ? key : "overview";
}

export function CinemaStudio({ onExit }: { onExit: () => void }) {
  const notify = useToast();
  const [tab, setTab] = useState<CinemaTab>(() => tabFromHash());
  const [overview, setOverview] = useState<CinemaOverview | null>(null);
  const [workers, setWorkers] = useState<CinemaWorkers | null>(null);
  const [tplSummary, setTplSummary] = useState<CinemaTemplatesSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async (silent: boolean) => {
    if (!silent) setLoading(true);
    try {
      const ov = await cinemaApi.overview();
      setOverview(ov);
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "无法读取项目数据");
    } finally {
      setLoading(false);
    }
    try {
      setWorkers(await cinemaApi.workers());
    } catch {
      setWorkers(null);
    }
    // 顶栏「模板 3D x/16」：模板库接口未就绪时静默降级（页面内另有完整错误态）
    try {
      setTplSummary((await cinemaApi.templates()).summary);
    } catch {
      /* 保持上一次统计 */
    }
  }, []);

  useEffect(() => {
    void load(false);
  }, [load]);

  // tab 与 hash 双向同步（支持直接打开 /#/cinema/shots）
  useEffect(() => {
    const onHash = () => setTab(tabFromHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const navigate = useCallback((next: CinemaTab) => {
    setTab(next);
    const hash = "#/cinema/" + next;
    if (window.location.hash !== hash) window.location.hash = hash;
  }, []);

  // 轮询：有活动任务或运行环境变化时刷新
  useEffect(() => {
    const timer = window.setInterval(() => {
      void load(true);
    }, overview?.jobs?.active ? 3000 : 12000);
    return () => window.clearInterval(timer);
  }, [load, overview?.jobs?.active]);

  const refresh = useCallback(async () => {
    setRefreshing(true);
    await load(true);
    setRefreshing(false);
    notify("已刷新项目数据", "success");
  }, [load, notify]);

  const shots = overview?.shots;
  const total = shots?.total ?? 56;
  const finalDur = overview?.final?.duration ?? 0;
  const activeJobs = overview?.jobs?.active ?? 0;

  const envState = useMemo(() => {
    if (!workers) return { label: "环境检测中", cls: "is-warn" as const };
    if (workers.comfyui && workers.metaso_key) return { label: "云端 / 本地就绪", cls: "is-on" as const };
    if (workers.comfyui) return { label: "本地 ComfyUI 就绪", cls: "is-on" as const };
    if (workers.metaso_key) return { label: "云端 Key 就绪", cls: "is-warn" as const };
    return { label: "生成环境离线", cls: "is-off" as const };
  }, [workers]);

  return (
    <div className="cm-app">
      <aside className="cm-rail">
        <button type="button" className="cm-brand" onClick={() => navigate("overview")} title="Cinema Studio">
          <span className="cm-brand-seal">造</span>
          <span className="cm-brand-txt">
            <span className="cm-brand-name">造片局</span>
            <span className="cm-brand-sub">Cinema Studio</span>
          </span>
        </button>

        <div className="cm-rail-label">制片流程</div>
        <nav className="cm-nav">
          {TAB_KEYS.map((key) => (
            <button
              key={key}
              type="button"
              className={"cm-nav-item" + (tab === key ? " is-active" : "")}
              onClick={() => navigate(key)}
              title={TAB_META[key].label}
            >
              {TAB_META[key].icon}
              <span className="cm-nav-label">{TAB_META[key].label}</span>
              <span className="cm-nav-no">{TAB_META[key].no}</span>
            </button>
          ))}
        </nav>

        <div className="cm-rail-foot">
          <div className="cm-env-row" title="ComfyUI:8188 / MetaSo Key">
            <span className="cm-env-key">
              <i className={"cm-env-dot " + envState.cls} />
              <span className="cm-env-text">{envState.label}</span>
            </span>
          </div>
          <div className="cm-env-row">
            <span className="cm-env-key">
              <i className={"cm-env-dot " + (workers?.comfyui ? "is-on" : "is-off")} />
              <span className="cm-env-text">ComfyUI · {workers?.comfyui_queue ?? 0} 队</span>
            </span>
          </div>
          <button type="button" className="cm-back-link" onClick={onExit}>
            <IconBack /> <span className="cm-env-text">返回平台</span>
          </button>
        </div>
      </aside>

      <div className="cm-main">
        <header className="cm-topbar">
          <div className="cm-top-project">
            <span className="cm-kicker">Cinema Studio · 3D 制片</span>
            <div className="cm-top-title-row">
              <h1 className="cm-top-title">{overview?.project?.name || "国宝会说话 · 3D"}</h1>
              <span className="cm-top-ep">
                {overview?.project?.episode_id || "ep_5610123283"} · {overview?.template?.label || "国宝会说话 · 3D"}
              </span>
            </div>
          </div>

          <div className="cm-top-stats">
            <div className="cm-top-stat">
              <b>
                {total - (shots?.missing ?? 0)}<span style={{ color: "var(--c-mut)" }}>/{total}</span>
              </b>
              <span>镜头就绪</span>
            </div>
            <div className="cm-top-stat">
              <b>{finalDur ? formatDuration(finalDur) : "—"}</b>
              <span>成片</span>
            </div>
            <div className="cm-top-stat">
              <b style={{ color: activeJobs ? "var(--c-gold)" : "var(--c-jade)" }}>{activeJobs}</b>
              <span>进行中任务</span>
            </div>
            <div className="cm-top-stat">
              <b
                style={{
                  color:
                    tplSummary && tplSummary.done > 0 && tplSummary.done >= (tplSummary.total || TEMPLATE_3D_TOTAL)
                      ? "var(--c-jade)"
                      : "var(--c-gold-soft)",
                }}
                title="16 模板 3D 化完成数"
              >
                {tplSummary ? `${tplSummary.done}/${tplSummary.total || TEMPLATE_3D_TOTAL}` : `—/${TEMPLATE_3D_TOTAL}`}
              </b>
              <span>模板 3D</span>
            </div>
          </div>

          <div className="cm-top-actions">
            <button
              type="button"
              className={"cm-icon-btn" + (refreshing ? " is-busy" : "")}
              onClick={() => void refresh()}
              title="刷新项目数据"
            >
              {refreshing ? <Spinner size={14} /> : <IconRefresh />}
            </button>
            <button type="button" className="cm-btn cm-btn-sm" onClick={() => navigate("assemble")}>
              <IconSliders /> 剪辑成片
            </button>
          </div>
        </header>

        <main className="cm-content">
          {loading && !overview ? (
            <div className="cm-page">
              <div className="cm-loading">
                <Spinner size={16} /> 正在连接制片后端…
              </div>
            </div>
          ) : error && !overview ? (
            <div className="cm-page">
              <div className="cm-error">
                <span>后端连接失败：{error}</span>
                <button type="button" className="cm-btn cm-btn-sm" onClick={() => void load(false)}>
                  重试
                </button>
              </div>
            </div>
          ) : tab === "overview" ? (
            <CinemaOverviewPage overview={overview} onNavigate={navigate} />
          ) : tab === "templates" ? (
            <CinemaTemplates />
          ) : tab === "shots" ? (
            <CinemaShots />
          ) : tab === "jobs" ? (
            <CinemaJobs />
          ) : tab === "assemble" ? (
            <CinemaAssemble
              onAssembled={() => {
                void load(true);
              }}
            />
          ) : (
            <CinemaFinal />
          )}
        </main>
      </div>
    </div>
  );
}

function formatDuration(sec: number): string {
  const s = Math.max(0, Math.round(sec));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

/* ------------------------------ 内联图标 ------------------------------ */
const S = { width: 17, height: 17, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 1.6, strokeLinecap: "round" as const, strokeLinejoin: "round" as const };

function IconGrid() {
  return (
    <svg {...S}>
      <rect x="3.5" y="3.5" width="7" height="7" rx="1.4" />
      <rect x="13.5" y="3.5" width="7" height="7" rx="1.4" />
      <rect x="3.5" y="13.5" width="7" height="7" rx="1.4" />
      <rect x="13.5" y="13.5" width="7" height="7" rx="1.4" />
    </svg>
  );
}
function IconFrames() {
  return (
    <svg {...S}>
      <rect x="3" y="6" width="13" height="9" rx="1.6" />
      <path d="M6 18h13a1.6 1.6 0 0 0 1.6-1.6V8.4" opacity="0.65" />
      <path d="m6.4 9.4 1.8 2.2 1.9-2.9 1.9 2.9 1.6-2" />
    </svg>
  );
}
function IconPulse() {
  return (
    <svg {...S}>
      <path d="M3 12h4l2-5 3 10 2.4-7 1.6 2h5" />
    </svg>
  );
}
function IconSliders() {
  return (
    <svg {...S}>
      <path d="M4 7h9M17 7h3M4 17h3M11 17h9" />
      <circle cx="15" cy="7" r="2" />
      <circle cx="9" cy="17" r="2" />
    </svg>
  );
}
function IconPlay() {
  return (
    <svg {...S}>
      <circle cx="12" cy="12" r="8.4" />
      <path d="m10.2 9.2 5 2.8-5 2.8z" fill="currentColor" stroke="none" />
    </svg>
  );
}
function IconRefresh() {
  return (
    <svg {...S}>
      <path d="M20 12a8 8 0 1 1-2.6-5.9" />
      <path d="M20 4.5V9h-4.5" />
    </svg>
  );
}
function IconBack() {
  return (
    <svg {...S} width={14} height={14}>
      <path d="M14 6 8 12l6 6" />
    </svg>
  );
}
