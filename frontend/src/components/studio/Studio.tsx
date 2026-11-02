import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Episode, Job, Project, Shot } from "../../types";
import type { StudioTabKey } from "../../lib/studio";
import { STUDIO_TABS, EPISODE_STATUS_TEXT, fmtClock, fmtMoney, isActiveJob, studioApi, toneOfStatus } from "../../lib/studio";
import { useToast } from "../Toast";
import { Spinner } from "../ui";
import { StudioScripts } from "./Scripts";
import { StudioAssets } from "./Assets";
import { StudioStoryboard } from "./Storyboard";
import { StudioRender } from "./Render";
import { StudioFinishing } from "./Finishing";
import { StudioTemplates3D } from "./Templates3D";
import "../../styles/studio.css";

interface Props {
  project: Project;
  episode: Episode;
  episodes: Episode[];
  tab: StudioTabKey;
  /** method 语法保持与 App 的 TabKey 回调双向兼容（V11 新增 templates3d 由 Studio 内部接管） */
  onTabChange(tab: StudioTabKey): void;
  onBack: () => void;
  onEpisodeUpdate: (episode: Episode) => void;
  onSwitchEpisode: (eid: string) => void;
}

export function ProjectStudio({
  project,
  episode: initialEpisode,
  episodes,
  tab,
  onTabChange,
  onBack,
  onEpisodeUpdate,
  onSwitchEpisode,
}: Props) {
  const notify = useToast();
  const [episode, setEpisode] = useState<Episode>(initialEpisode);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [shots, setShots] = useState<Shot[]>([]);
  const [costTotal, setCostTotal] = useState<number>(Number(initialEpisode.cost) || 0);
  const [refreshing, setRefreshing] = useState(false);
  const [switching, setSwitching] = useState(false);
  /** 3D 模板页切换节目模板后，同步顶栏 format_id chip（App 侧 project 暂不重取） */
  const [projectFormatOverride, setProjectFormatOverride] = useState<string | null>(null);
  const episodeIdRef = useRef(initialEpisode.id);

  const activeTab: StudioTabKey = tab;
  const displayFormatId = projectFormatOverride || project.format_id;

  useEffect(() => {
    if (episodeIdRef.current !== initialEpisode.id) {
      episodeIdRef.current = initialEpisode.id;
      setEpisode(initialEpisode);
      setSwitching(false);
    } else {
      setEpisode(initialEpisode);
    }
  }, [initialEpisode]);

  const applyEpisode = useCallback(
    (next: Episode) => {
      setEpisode(next);
      onEpisodeUpdate(next);
    },
    [onEpisodeUpdate],
  );

  const loadOverview = useCallback(async () => {
    const [jobsRes, shotsRes, costRes] = await Promise.allSettled([
      studioApi.jobs({ episode_id: episode.id }),
      studioApi.shots(episode.id),
      studioApi.costs(episode.id),
    ]);
    if (jobsRes.status === "fulfilled") setJobs(jobsRes.value);
    if (shotsRes.status === "fulfilled") setShots(shotsRes.value);
    if (costRes.status === "fulfilled") setCostTotal(Number(costRes.value.total) || 0);
  }, [episode.id]);

  useEffect(() => {
    void loadOverview();
  }, [loadOverview]);

  const shotsReady = useMemo(
    () => shots.filter((item) => ["ready", "done", "rendering", "generated"].includes(String(item.status || "").toLowerCase())).length,
    [shots],
  );
  const shotsTotal = shots.length || episode.shots_total || 0;

  const activeJobs = useMemo(() => jobs.filter(isActiveJob).length, [jobs]);

  useEffect(() => {
    if (!activeJobs) return;
    const timer = window.setInterval(() => {
      void loadOverview();
    }, 2500);
    return () => window.clearInterval(timer);
  }, [activeJobs, loadOverview]);

  const refresh = useCallback(async () => {
    setRefreshing(true);
    try {
      const [ep, list, shotList, costs] = await Promise.all([
        studioApi.episode(episode.id),
        studioApi.jobs({ episode_id: episode.id }),
        studioApi.shots(episode.id),
        studioApi.costs(episode.id),
      ]);
      applyEpisode(ep);
      setJobs(list);
      setShots(shotList);
      setCostTotal(Number(costs.total) || 0);
      notify("已刷新期数与任务", "success");
    } catch (err) {
      notify("刷新失败：" + (err instanceof Error ? err.message : String(err)), "error");
    } finally {
      setRefreshing(false);
    }
  }, [applyEpisode, episode.id, notify]);

  const handleTabChange = useCallback((next: StudioTabKey) => onTabChange(next), [onTabChange]);

  const tabMeta = STUDIO_TABS.find((t) => t.key === activeTab) || STUDIO_TABS[0];

  const tabProps = {
    project: { ...project, format_id: displayFormatId },
    episode,
    onEpisode: applyEpisode,
    onNavigate: handleTabChange,
  };

  return (
    <div className="cm-app st-app">
      <div className="st-main">
        <header className="st-topbar">
          <button type="button" className="st-back" onClick={onBack} title="返回项目列表">
            <IconChevronLeft /> 平台
          </button>

          <div className="st-head-main">
            <div className="st-breadcrumb">
              <span>{project.name}</span>
              <span>/</span>
              <b>第 {episode.no} 期</b>
              <span>/</span>
              <span>{tabMeta.en}</span>
            </div>
            <div className="st-ep-row">
              <h1 className="st-ep-title">{episode.title || project.name + " · 第 " + episode.no + " 期"}</h1>
              {episodes.length > 1 && (
                <select
                  className="st-ep-select"
                  value={episode.id}
                  disabled={switching}
                  onChange={(e) => {
                    setSwitching(true);
                    onSwitchEpisode(e.target.value);
                  }}
                  title="切换期数"
                >
                  {episodes.map((ep) => (
                    <option key={ep.id} value={ep.id}>
                      第 {ep.no} 期 · {ep.title || ep.id}
                    </option>
                  ))}
                </select>
              )}
            </div>
            <div className="st-top-meta">
              <span className={"st-tag " + (toneOfStatus(episode.status) === "jade" ? "is-jade" : toneOfStatus(episode.status) === "gold" ? "is-gold" : toneOfStatus(episode.status) === "red" ? "is-red" : "is-slate")}>
                <Dot />
                {EPISODE_STATUS_TEXT[episode.status] || episode.status}
              </span>
              <span className="st-tag is-slate cm-mono">目标 {fmtClock(episode.target_duration)}</span>
              {episode.resolution && <span className="st-tag is-slate cm-mono">{episode.resolution}</span>}
              {episode.aspect && <span className="st-tag is-slate cm-mono">{episode.aspect}</span>}
              <span className="st-tag is-slate cm-mono">{displayFormatId}</span>
            </div>
          </div>

          <div className="st-top-stats">
            <div className="st-stat">
              <b className={shotsTotal > 0 && shotsReady >= shotsTotal ? "is-jade" : ""}>{shotsTotal}</b>
              <span>镜头</span>
            </div>
            <div className="st-stat-divider" />
            <div className="st-stat">
              <b>{fmtClock(episode.target_duration)}</b>
              <span>目标时长</span>
            </div>
            <div className="st-stat-divider" />
            <div className="st-stat">
              <b className="is-gold">{fmtMoney(costTotal)}</b>
              <span>已花费</span>
            </div>
            <div className="st-stat-divider" />
            <div className="st-stat">
              <b className={activeJobs ? "is-gold" : "is-jade"}>{activeJobs}</b>
              <span>进行中任务</span>
            </div>
          </div>

          <div className="st-top-actions">
            <button type="button" className="cm-icon-btn" onClick={() => void refresh()} title="刷新型数/任务">
              {refreshing ? <Spinner size={13} /> : <IconRefresh />}
            </button>
          </div>
        </header>

        <nav className="st-tabbar">
          {STUDIO_TABS.map((item) => (
            <button
              key={item.key}
              type="button"
              className={"st-tab" + (activeTab === item.key ? " is-active" : "")}
              onClick={() => handleTabChange(item.key)}
              title={item.desc}
            >
              <span className="st-tab-no">{item.no}</span>
              <span className="st-tab-label">{item.label}</span>
              <span className="st-tab-en">{item.en}</span>
            </button>
          ))}
          <div className="st-tabbar-right">
            <a className="st-tab-link" href="#/pipeline" title="一键成片 · 制片控制台">
              一键成片 ↗
            </a>
            <a className="st-tab-link" href="#/cinema/templates" title="模板的 3D 样片与制作进度">
              Cinema 3D ↗
            </a>
            <button type="button" className="st-tab-link" onClick={onBack}>
              返回平台
            </button>
          </div>
        </nav>


        <main className="st-content">
          {activeTab === "script" ? (
            <StudioScripts {...tabProps} />
          ) : activeTab === "assets" ? (
            <StudioAssets {...tabProps} />
          ) : activeTab === "storyboard" ? (
            <StudioStoryboard {...tabProps} />
          ) : activeTab === "generate" ? (
            <StudioRender {...tabProps} />
          ) : activeTab === "templates3d" ? (
            <StudioTemplates3D {...tabProps} onProjectFormatChange={setProjectFormatOverride} />
          ) : (
            <StudioFinishing {...tabProps} />
          )}
        </main>

        <footer className="st-flow">
          <span className="st-flow-label">制片流水线</span>
          <div className="st-flow-steps">
            {STUDIO_TABS.map((item, index) => {
              const currentIndex = STUDIO_TABS.findIndex((t) => t.key === activeTab);
              const state = index < currentIndex ? "is-done" : index === currentIndex ? "is-current" : "";
              return (
                <button
                  key={item.key}
                  type="button"
                  className={"st-flow-step " + state}
                  onClick={() => handleTabChange(item.key)}
                  title={item.desc}
                >
                  <span className="st-flow-no">{item.no}</span>
                  <span className="st-flow-name">{item.label}</span>
                </button>
              );
            })}
          </div>
          <div className="st-flow-actions">
            {(() => {
              const currentIndex = STUDIO_TABS.findIndex((t) => t.key === activeTab);
              const prev = currentIndex > 0 ? STUDIO_TABS[currentIndex - 1] : null;
              const next = currentIndex < STUDIO_TABS.length - 1 ? STUDIO_TABS[currentIndex + 1] : null;
              return (
                <>
                  {prev && (
                    <button type="button" className="cm-btn cm-btn-ghost cm-btn-sm" onClick={() => handleTabChange(prev.key)}>
                      ← {prev.label}
                    </button>
                  )}
                  {next ? (
                    <button type="button" className="cm-btn cm-btn-primary cm-btn-sm" onClick={() => handleTabChange(next.key)}>
                      下一步：{next.label} →
                    </button>
                  ) : (
                    <a className="cm-btn cm-btn-ghost cm-btn-sm" href="#/v5/gallery">
                      去成片画廊 →
                    </a>
                  )}
                </>
              );
            })()}
          </div>
        </footer>
      </div>
    </div>
  );
}

/* 内联图标 */
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
function IconChevronLeft() {
  return (
    <svg {...S} width={14} height={14}>
      <path d="M14 6 8 12l6 6" />
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
function Dot() {
  return <i style={{ width: 6, height: 6, borderRadius: 99, background: "currentColor", boxShadow: "0 0 8px currentColor" }} />;
}
