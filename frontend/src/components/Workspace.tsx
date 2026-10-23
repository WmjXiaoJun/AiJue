import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api, errorText } from "../lib/api";
import type { Asset, Character, Episode, Job, Project, Shot } from "../types";
import { fmtDuration, fmtMoney, fmtPercent, isActiveJob, label, EPISODE_STATUS_LABEL } from "../lib/format";
import { templateLabel } from "../lib/templates";
import { usePolling } from "../hooks/usePolling";
import { useToast } from "./Toast";
import { Screenwriting } from "./tabs/Screenwriting";
import { AssetLibrary } from "./tabs/AssetLibrary";
import { Storyboard } from "./tabs/Storyboard";
import { Generation } from "./tabs/Generation";
import { Finishing } from "./tabs/Finishing";
import { ShotDrawer } from "./ShotDrawer";
import {
  IconBoard,
  IconChevronLeft,
  IconFilm,
  IconScript,
  IconSpark,
  IconUsers,
} from "./icons";
import { Notice, ProgressBar, Spinner, StatusBadge } from "./ui";
import type { TabKey } from "../App";
import type { TabBaseProps } from "./tabs/types";

const TABS: { key: TabKey; label: string; en: string; icon: (props: { size?: number }) => JSX.Element }[] = [
  { key: "script", label: "编剧间", en: "Script", icon: IconScript },
  { key: "assets", label: "资产库", en: "Assets", icon: IconUsers },
  { key: "storyboard", label: "分镜台", en: "Storyboard", icon: IconBoard },
  { key: "generate", label: "生成台", en: "Render", icon: IconSpark },
  { key: "finish", label: "成片房", en: "Finishing", icon: IconFilm },
];

interface Props {
  project: Project;
  episode: Episode;
  tab: TabKey;
  offline: boolean;
  onTabChange: (tab: TabKey) => void;
  onBack: () => void;
  onStats: (stats: { activeJobs: number; cost: number }) => void;
}

export function Workspace({ project, episode: initialEpisode, tab, offline, onTabChange, onBack, onStats }: Props) {
  const notify = useToast();
  const [episode, setEpisode] = useState<Episode>(initialEpisode);
  const [shots, setShots] = useState<Shot[] | null>(null);
  const [assets, setAssets] = useState<Asset[] | null>(null);
  const [characters, setCharacters] = useState<Character[] | null>(null);
  const [charactersSupported, setCharactersSupported] = useState(true);
  const [charactersError, setCharactersError] = useState<string | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [lastSync, setLastSync] = useState<number | null>(null);
  const [selectedShotId, setSelectedShotId] = useState<string | null>(null);

  const eid = initialEpisode.id;
  const statsRef = useRef(onStats);
  statsRef.current = onStats;

  const applyEpisode = useCallback((next: Episode) => {
    setEpisode(next);
  }, []);

  const refresh = useCallback(async () => {
    const [epRes, jobsRes, shotsRes] = await Promise.allSettled([
      api.episode(eid),
      api.jobs({ episode_id: eid }),
      api.shots(eid),
    ]);
    let nextEpisode: Episode | null = null;
    let nextJobs: Job[] | null = null;
    if (epRes.status === "fulfilled") {
      nextEpisode = epRes.value;
      applyEpisode(epRes.value);
    }
    if (jobsRes.status === "fulfilled") {
      nextJobs = jobsRes.value;
      setJobs(jobsRes.value);
    }
    if (shotsRes.status === "fulfilled") {
      setShots(shotsRes.value);
    }
    if (nextEpisode) {
      statsRef.current({
        activeJobs: (nextJobs || []).filter(isActiveJob).length,
        cost: nextEpisode.cost || 0,
      });
    }
    if (epRes.status === "fulfilled" || jobsRes.status === "fulfilled" || shotsRes.status === "fulfilled") {
      setLastSync(Date.now());
    }
  }, [eid, applyEpisode]);

  const refreshRef = useRef(refresh);
  refreshRef.current = refresh;

  /** 角色工坊：真实接口；404/405 视为「后端未就绪」但不阻塞其它模块 */
  const refreshCharacters = useCallback(async (): Promise<Character[] | null> => {
    try {
      const list = await api.characters(project.id);
      setCharacters(list);
      setCharactersSupported(true);
      setCharactersError(null);
      return list;
    } catch (err) {
      setCharactersError(errorText(err));
      const status = err instanceof ApiError ? err.status : 0;
      setCharacters((prev) => prev ?? []);
      setCharactersSupported(!(status === 404 || status === 405));
      return null;
    }
  }, [project.id]);

  const refreshAssets = useCallback(async () => {
    try {
      setAssets(await api.assets(eid));
    } catch {
      /* 保留已有资产列表 */
    }
  }, [eid]);

  const loadInitial = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    const [epRes, shotsRes, assetsRes, jobsRes, charRes] = await Promise.allSettled([
      api.episode(eid),
      api.shots(eid),
      api.assets(eid),
      api.jobs({ episode_id: eid }),
      api.characters(project.id),
    ]);
    if (epRes.status === "rejected") {
      setLoadError(errorText(epRes.reason));
    } else {
      setEpisode(epRes.value);
    }
    setShots(shotsRes.status === "fulfilled" ? shotsRes.value : []);
    setAssets(assetsRes.status === "fulfilled" ? assetsRes.value : []);
    setJobs(jobsRes.status === "fulfilled" ? jobsRes.value : []);
    if (charRes.status === "fulfilled") {
      setCharacters(charRes.value);
      setCharactersSupported(true);
      setCharactersError(null);
    } else {
      const reason = charRes.reason;
      const status = reason instanceof ApiError ? reason.status : 0;
      setCharacters([]);
      setCharactersError(errorText(reason));
      setCharactersSupported(!(status === 404 || status === 405));
    }
    setLoading(false);
    setLastSync(Date.now());
    if (epRes.status === "fulfilled" && jobsRes.status === "fulfilled") {
      statsRef.current({
        activeJobs: jobsRes.value.filter(isActiveJob).length,
        cost: epRes.value.cost || 0,
      });
    }
  }, [eid, project.id]);

  useEffect(() => {
    void loadInitial();
  }, [loadInitial]);

  useEffect(() => {
    const previous = document.title;
    document.title = "造片局 · 第 " + String(episode.no || 1).padStart(3, "0") + " 期 · " + episode.title;
    return () => {
      document.title = previous;
    };
  }, [episode.no, episode.title]);

  // 2s 轮询 episode + jobs + shots
  usePolling(refresh, 2000, !offline);

  const handleChanged = useCallback(() => {
    void refreshRef.current();
  }, []);

  const handleAssetUpdated = useCallback((updated: Asset) => {
    setAssets((list) => (list || []).map((asset) => (asset.id === updated.id ? updated : asset)));
    void refreshRef.current();
  }, []);

  const selectedShot = (shots || []).find((shot) => shot.id === selectedShotId) || null;
  const openShot = useCallback((shot: Shot) => setSelectedShotId(shot.id), []);
  const closeShot = useCallback(() => setSelectedShotId(null), []);

  const shotsReady = (shots || []).filter((shot) => shot.status === "ready").length;
  const totalDuration = (shots || []).reduce((sum, shot) => sum + (shot.duration || 0), 0);
  const activeJobs = jobs.filter(isActiveJob).length;
  const episodeProgress =
    typeof episode.progress === "number" && episode.progress > 1
      ? episode.progress
      : (episode.progress || 0) * 100;
  const jobByShot = new Map<string, Job>();
  jobs.forEach((job) => {
    if (job.shot_id && !jobByShot.has(job.shot_id)) jobByShot.set(job.shot_id, job);
  });

  const baseProps: TabBaseProps = {
    project,
    episode,
    assets: assets || [],
    shots: shots || [],
    onChanged: handleChanged,
    onOpenShot: openShot,
  };

  return (
    <div className="page page-workspace">
      <div className="ws-head">
        <div className="ws-head-main">
          <button type="button" className="back-link" onClick={onBack}>
            <IconChevronLeft size={15} />
            项目
          </button>
          <div className="ws-title-row">
            <h2 className="ws-title">{episode.title}</h2>
            <StatusBadge status={episode.status} kind="episode" />
            <span className="ws-format">{templateLabel(project.format_id)}</span>
          </div>
          <div className="ws-meta">
            <span>第 {String(episode.no || 1).padStart(3, "0")} 期</span>
            <i />
            <span>目标时长 {fmtDuration(episode.target_duration)}</span>
            <i />
            <span>画幅 {project.aspect || "16:9"}</span>
            <i />
            <span>镜头 {shots ? shots.length : episode.shots_total || 0}</span>
            <i />
            <span>台词时长 {fmtDuration(totalDuration)}</span>
            <i />
            <span>费用 {fmtMoney(episode.cost)}</span>
          </div>
        </div>
        <div className="ws-progress">
          <div className="ws-progress-top">
            <span className="ws-progress-label">镜头就绪</span>
            <b className="ws-progress-num">
              {shotsReady}
              <span> / {shots ? shots.length : episode.shots_total || 0}</span>
            </b>
          </div>
          <ProgressBar
            value={shots && shots.length ? (shotsReady / shots.length) * 100 : episodeProgress}
            tone={episode.status === "failed" ? "danger" : "accent"}
            label={fmtPercent(shots && shots.length ? (shotsReady / shots.length) * 100 : episodeProgress)}
          />
          <div className="ws-progress-foot">
            <span>{label(EPISODE_STATUS_LABEL, episode.status)}</span>
            <span>{activeJobs > 0 ? activeJobs + " 个任务运行中" : "无进行中任务"}</span>
          </div>
        </div>
      </div>

      <div className="ws-tabs">
        <div className="tab-list" role="tablist" aria-label="制作流程">
          {TABS.map((item, index) => {
            const Icon = item.icon;
            const active = tab === item.key;
            return (
              <button
                key={item.key}
                type="button"
                role="tab"
                aria-selected={active}
                className={"tab" + (active ? " active" : "")}
                onClick={() => onTabChange(item.key)}
              >
                <span className="tab-index">0{index + 1}</span>
                <Icon size={16} />
                <span className="tab-label">{item.label}</span>
                <span className="tab-en">{item.en}</span>
              </button>
            );
          })}
        </div>
        <div className="tab-side">
          <span className="tab-side-item">
            <i className={"live-dot " + (offline ? "offline" : "online")} />
            {lastSync ? "刚刚同步" : "同步中"}
          </span>
          <span className="tab-side-item">2s 轮询</span>
        </div>
      </div>

      <div className="ws-content">
        {loadError && (
          <Notice tone="danger">
            加载剧集数据失败：{loadError}
            <button type="button" className="link-btn" onClick={() => void loadInitial()}>
              重试
            </button>
          </Notice>
        )}

        {loading && !shots ? (
          <div className="skeleton-wrap">
            <div className="skeleton-row" />
            <div className="skeleton-row" />
            <div className="skeleton-row skeleton-tall" />
          </div>
        ) : (
          <>
            {tab === "script" && <Screenwriting {...baseProps} />}
            {tab === "assets" && (
              <AssetLibrary
                {...baseProps}
                onAssetUpdated={handleAssetUpdated}
                characters={characters}
                charactersSupported={charactersSupported}
                charactersError={charactersError}
                onRefreshCharacters={refreshCharacters}
                onRefreshAssets={refreshAssets}
              />
            )}
            {tab === "storyboard" && <Storyboard {...baseProps} jobByShot={jobByShot} characters={characters || []} />}
            {tab === "generate" && <Generation {...baseProps} jobs={jobs} />}
            {tab === "finish" && <Finishing {...baseProps} jobs={jobs} characters={characters || []} />}
          </>
        )}
      </div>

      <ShotDrawer
        shot={selectedShot}
        assets={assets || []}
        onClose={closeShot}
        onChanged={handleChanged}
      />
    </div>
  );
}
