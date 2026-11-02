import { useCallback, useEffect, useMemo, useState } from "react";
import type { CinemaOverview, CinemaTab, CinemaTemplatesSummary, CinemaWorkers } from "../../lib/cinema";
import { TEMPLATE_3D_TOTAL, cinemaApi } from "../../lib/cinema";
import { useToast } from "../Toast";
import { AppShell } from "../shell/AppShell";
import { Spinner } from "../ui";
import { IconSpark, IconTemplate } from "../icons";
import { CinemaShots } from "./Shots";
import { CinemaJobs } from "./Jobs";
import { CinemaAssemble } from "./Assemble";
import "../../styles/cinema.css";

const TAB_KEYS: CinemaTab[] = ["shots", "jobs", "assemble"];

const TAB_META: Record<CinemaTab, { label: string; en: string; no: string; icon: JSX.Element }> = {
  shots: { label: "镜头台", en: "SHOT WORKBENCH", no: "01", icon: <IconFrames /> },
  jobs: { label: "生成队列", en: "RENDER QUEUE", no: "02", icon: <IconPulse /> },
  assemble: { label: "剪辑台", en: "EDIT & MASTER", no: "03", icon: <IconSliders /> },
};

function tabFromHash(): CinemaTab {
  const match = window.location.hash.match(/^#\/cinema\/?([a-z]*)/i);
  const key = (match?.[1] || "").toLowerCase() as CinemaTab;
  return TAB_KEYS.includes(key) ? key : "shots";
}

export function CinemaStudio({ onExit, onOpenSettings }: { onExit: () => void; onOpenSettings?: () => void; onOpenFinal?: () => void; onOpenLibrary?: () => void }) {
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
    // 顶栏「模板 3D x/总数」：模板库接口未就绪时静默降级（页面内另有完整错误态）
    try {
      setTplSummary((await cinemaApi.templates()).summary);
    } catch {
      /* 保持上一次统计；首次失败时用 TEMPLATE_3D_TOTAL 兜底显示 */
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
  // V17：模板总数动态（后端新增 real_* 后自动 19；接口失败时保留原 16 fallback）
  const tplTotal = tplSummary?.total || TEMPLATE_3D_TOTAL;

  const envState = useMemo(() => {
    if (!workers) return { label: "环境检测中", cls: "is-warn" as const };
    if (workers.comfyui && workers.metaso_key) return { label: "云端 / 本地就绪", cls: "is-on" as const };
    if (workers.comfyui) return { label: "本地 ComfyUI 就绪", cls: "is-on" as const };
    if (workers.metaso_key) return { label: "云端 Key 就绪", cls: "is-warn" as const };
    return { label: "生成环境离线", cls: "is-off" as const };
  }, [workers]);

  return (
    <AppShell
      active={null}
      kicker="CINEMA STUDIO · 3D 制片"
      title={overview?.project?.name || "国宝会说话 · 3D"}
      breadcrumb={[{ label: "造片局" }, { label: overview?.project?.episode_id || "ep_5610123283" }]}
      tabs={TAB_KEYS.map((key) => ({ key, no: TAB_META[key].no, label: TAB_META[key].label, en: TAB_META[key].en }))}
      activeTab={tab}
      onTabChange={(key) => navigate(key as CinemaTab)}
      chips={[
        { label: "镜头就绪", value: `${total - (shots?.missing ?? 0)}/${total}`, tone: "slate" },
        { label: "成片", value: finalDur ? formatDuration(finalDur) : "—", tone: finalDur ? "jade" : "slate" },
        { label: "进行中", value: String(activeJobs), tone: activeJobs ? "gold" : "slate" },
        { label: "模板 3D", value: `${tplSummary?.done ?? 0}/${tplTotal}`, tone: "gold" },
      ]}
      primaryAction={{ label: "剪辑成片", onClick: () => navigate("assemble"), title: "进入剪辑台合成成片" }}
      onRefresh={() => void refresh()}
      refreshing={refreshing}
      onOpenSettings={onOpenSettings || (() => { window.location.hash = "#/projects"; })}
    >
      
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
          ) : tab === "jobs" ? (
            <CinemaJobs />
          ) : tab === "assemble" ? (
            <CinemaAssemble
              onAssembled={() => {
                void load(true);
              }}
            />
          ) : (
            <CinemaShots />
          )}
    </AppShell>
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
