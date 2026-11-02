import { useCallback, useEffect, useRef, useState } from "react";
import { api, errorText } from "./lib/api";
import type { Episode, Health, Project } from "./types";
import { ToastProvider, useToast } from "./components/Toast";
import { ProjectStudio } from "./components/studio/Studio";
import { PlatformHome } from "./components/platform/PlatformHome";
import { CinemaStudio } from "./components/cinema/CinemaStudio";
import { PipelineConsole } from "./components/pipeline/PipelineConsole";
import { CharactersPage } from "./components/characters/CharactersPage";
import { LibraryPage, type LibraryTab } from "./components/library/LibraryPage";
import { BrandMark, IconRefresh } from "./components/icons";
import { Spinner } from "./components/ui";

export type TabKey = "script" | "assets" | "storyboard" | "generate" | "finish";

const TAB_KEYS: TabKey[] = ["script", "assets", "storyboard", "generate", "finish"];

type ViewState =
  | { mode: "projects" }
  | { mode: "workspace"; project: Project; episode: Episode }
  | { mode: "cinema" }
  | { mode: "pipeline" }
  | { mode: "characters" }
  | { mode: "library"; tab: LibraryTab };

const LIBRARY_TABS: LibraryTab[] = ["templates", "styles", "scenes", "final"];

/** V20：旧 URL → 新 IA 的重定向表（含 V5 下线、Cinema 瘦身、工作台去 06 tab） */
function legacyRedirectHash(): string | null {
  const raw = window.location.hash.replace(/^#\/?/, "");
  const parts = raw.split("/").filter(Boolean);
  const path = "/" + parts.join("/");
  const map: Record<string, string> = {
    "/v5/gallery": "#/library/final",
    "/v5/templates": "#/library/templates",
    "/v5/styles": "#/library/styles",
    "/v5/scenes": "#/library/scenes",
    "/v5/characters": "#/characters",
    "/v5/render": "#/pipeline",
    "/cinema/overview": "#/projects",
    "/cinema/templates": "#/library/templates",
    "/cinema/final": "#/library/final",
    "/cinema": "#/cinema/shots",
  };
  if (map[path]) return map[path];
  if (parts[0] === "v5") return "#/library/final";
  if (parts[0] === "cinema" && parts[1] && !["shots", "jobs", "assemble"].includes(parts[1])) {
    return parts[1] === "templates" ? "#/library/templates" : parts[1] === "final" ? "#/library/final" : "#/projects";
  }
  if (parts[0] === "episodes" && parts[1] && parts[2] === "templates3d") {
    return "#/episodes/" + encodeURIComponent(decodeURIComponent(parts[1])) + "/script";
  }
  return null;
}

function applyLegacyRedirect(): boolean {
  const target = legacyRedirectHash();
  if (!target) return false;
  window.location.replace(window.location.pathname + target);
  return true;
}

function parseHash(): { eid?: string; tab: TabKey; cinema?: boolean; pipeline?: boolean; characters?: boolean; library?: LibraryTab } {
  const raw = window.location.hash.replace(/^#\/?/, "");
  const parts = raw.split("/").filter(Boolean);
  if (parts[0] === "cinema") {
    return { tab: "script", cinema: true };
  }
  if (parts[0] === "library") {
    const tab = (LIBRARY_TABS as string[]).includes(parts[1] || "") ? (parts[1] as LibraryTab) : "templates";
    return { tab: "script", library: tab };
  }
  if (parts[0] === "pipeline") {
    return { tab: "script", pipeline: true };
  }
  if (parts[0] === "characters") {
    return { tab: "script", characters: true };
  }
  if (parts[0] === "episodes" && parts[1]) {
    const tab = TAB_KEYS.includes(parts[2] as TabKey) ? (parts[2] as TabKey) : "script";
    return { eid: decodeURIComponent(parts[1]), tab };
  }
  return { tab: "script" };
}

function hashFor(eid?: string, tab?: TabKey): string {
  return eid ? "#/episodes/" + encodeURIComponent(eid) + "/" + (tab || "script") : "#/projects";
}

export default function App() {
  return (
    <ToastProvider>
      <Shell />
    </ToastProvider>
  );
}

function Shell() {
  const notify = useToast();
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [offline, setOffline] = useState(false);
  const [booting, setBooting] = useState(true);
  const [view, setView] = useState<ViewState>({ mode: "projects" });
  const [episodes, setEpisodes] = useState<Episode[] | null>(null);
  const [tab, setTab] = useState<TabKey>("script");
  const [taskStats, setTaskStats] = useState<{ activeJobs: number; cost: number }>({ activeJobs: 0, cost: 0 });
  const [retrying, setRetrying] = useState(false);
  const [settingsSignal, setSettingsSignal] = useState(0);

  const viewRef = useRef(view);
  viewRef.current = view;
  const lastSyncRef = useRef<number | null>(null);

  const loadProjects = useCallback(async () => {
    const list = await api.projects();
    setProjects(list);
    return list;
  }, []);

  const boot = useCallback(
    async (silent: boolean) => {
      if (applyLegacyRedirect()) return;
      if (!silent) setBooting(true);
      try {
        const h = await api.health();
        setHealth(h);
        const list = await loadProjects();
        setOffline(false);
        lastSyncRef.current = Date.now();
        const route = parseHash();
        if (route.cinema) {
          setView({ mode: "cinema" });
        } else if (route.library) {
          setView({ mode: "library", tab: route.library });
        } else if (route.pipeline) {
          setView({ mode: "pipeline" });
        } else if (route.characters) {
          setView({ mode: "characters" });
        } else if (route.eid) {
          const episode = await api.episode(route.eid);
          const project = list.find((item) => item.id === episode.project_id) || (await api.project(episode.project_id));
          setEpisodes(await api.episodes(project.id));
          setView({ mode: "workspace", project, episode });
          setTab(route.tab);
        }
      } catch {
        setOffline(true);
      } finally {
        setBooting(false);
      }
    },
    [loadProjects]
  );

  useEffect(() => {
    void boot(false);
  }, [boot]);

  // 后端重连：每 4s 重试一次健康检查与项目列表
  useEffect(() => {
    if (!offline) return;
    const timer = window.setInterval(() => {
      void boot(true);
    }, 4000);
    return () => window.clearInterval(timer);
  }, [offline, boot]);

  // 在线状态心跳（顶部“实时同步”指示）
  useEffect(() => {
    if (offline) return;
    const timer = window.setInterval(async () => {
      try {
        setHealth(await api.health());
        setOffline(false);
      } catch {
        setOffline(true);
      }
    }, 15000);
    return () => window.clearInterval(timer);
  }, [offline]);

  const openEpisodeById = useCallback(
    async (eid: string, nextTab: TabKey) => {
      try {
        const episode = await api.episode(eid);
        const project =
          projects?.find((item) => item.id === episode.project_id) || (await api.project(episode.project_id));
        setEpisodes(await api.episodes(project.id));
        setView({ mode: "workspace", project, episode });
        setTab(nextTab);
        setTaskStats({ activeJobs: 0, cost: episode.cost || 0 });
      } catch {
        window.location.hash = hashFor();
      }
    },
    [projects]
  );

  // 支持浏览器前进/后退 + 直接带 hash 打开
  useEffect(() => {
    const onHash = () => {
      if (applyLegacyRedirect()) return;
      const route = parseHash();
      if (route.cinema) {
        setView({ mode: "cinema" });
        setTab("script");
        return;
      }
      if (route.library) {
        setView({ mode: "library", tab: route.library });
        setTab("script");
        return;
      }
      if (route.pipeline) {
        setView({ mode: "pipeline" });
        setTab("script");
        return;
      }
      if (route.characters) {
        setView({ mode: "characters" });
        setTab("script");
        return;
      }
      if (!route.eid) {
        setView({ mode: "projects" });
        setTab("script");
        return;
      }
      const current = viewRef.current;
      if (current.mode === "workspace" && current.episode.id === route.eid) {
        setTab(route.tab);
        return;
      }
      void openEpisodeById(route.eid, route.tab);
    };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, [openEpisodeById]);

  const openProject = useCallback(
    async (project: Project) => {
      try {
        const episodes = await api.episodes(project.id);
        if (!episodes.length) {
          notify("项目「" + project.name + "」还没有剧集，等待后端初始化", "error");
          return;
        }
        const episode = episodes.find((item) => item.no === 1) || episodes[0];
        setEpisodes(episodes);
        setView({ mode: "workspace", project, episode });
        setTab("script");
        setTaskStats({ activeJobs: 0, cost: episode.cost || 0 });
        window.location.hash = hashFor(episode.id, "script");
      } catch (err) {
        notify(errorText(err), "error");
      }
    },
    [notify]
  );

  /** 切换剧集（同一项目内） */
  const switchEpisode = useCallback(
    async (eid: string) => {
      const current = viewRef.current;
      if (current.mode !== "workspace" || current.episode.id === eid) return;
      try {
        const episode = await api.episode(eid);
        setEpisodes(await api.episodes(current.project.id));
        setView({ mode: "workspace", project: current.project, episode });
        setTab("script");
        setTaskStats({ activeJobs: 0, cost: episode.cost || 0 });
        window.location.hash = hashFor(eid, "script");
      } catch (err) {
        notify("切换剧集失败：" + errorText(err), "error");
      }
    },
    [notify]
  );

  /** 新建剧集并切换过去 */
  const createEpisode = useCallback(
    async (body: { title?: string; target_duration?: number }): Promise<Episode | null> => {
      const current = viewRef.current;
      if (current.mode !== "workspace") return null;
      try {
        const episode = await api.createEpisode(current.project.id, body);
        setEpisodes(await api.episodes(current.project.id));
        setView({ mode: "workspace", project: current.project, episode });
        setTab("script");
        setTaskStats({ activeJobs: 0, cost: episode.cost || 0 });
        window.location.hash = hashFor(episode.id, "script");
        notify("已创建第 " + String(episode.no || 1).padStart(3, "0") + " 期，可开始写台本", "success");
        return episode;
      } catch (err) {
        notify("新建剧集失败：" + errorText(err), "error");
        return null;
      }
    },
    [notify]
  );

  const changeTab = useCallback((next: TabKey) => {
    setTab(next);
    const current = viewRef.current;
    if (current.mode === "workspace") {
      const hash = hashFor(current.episode.id, next);
      if (window.location.hash !== hash) window.location.hash = hash;
    }
  }, []);

  const goHome = useCallback(() => {
    setView({ mode: "projects" });
    setEpisodes(null);
    setTab("script");
    setTaskStats({ activeJobs: 0, cost: 0 });
    if (window.location.hash !== "#/projects") window.location.hash = "#/projects";
    void loadProjects().catch(() => undefined);
  }, [loadProjects]);

  const openCinema = useCallback(() => {
    setView({ mode: "cinema" });
    setEpisodes(null);
    setTab("script");
    setTaskStats({ activeJobs: 0, cost: 0 });
    if (window.location.hash !== "#/cinema/shots") window.location.hash = "#/cinema/shots";
  }, []);

  const openLibrary = useCallback((tab: LibraryTab = "templates") => {
    setView({ mode: "library", tab });
    setEpisodes(null);
    setTab("script");
    setTaskStats({ activeJobs: 0, cost: 0 });
    const hash = "#/library/" + tab;
    if (window.location.hash !== hash) window.location.hash = hash;
  }, []);

  const openPipeline = useCallback(() => {
    setView({ mode: "pipeline" });
    setEpisodes(null);
    setTab("script");
    setTaskStats({ activeJobs: 0, cost: 0 });
    if (window.location.hash !== "#/pipeline") window.location.hash = "#/pipeline";
  }, []);

  const openCharacters = useCallback(() => {
    setView({ mode: "characters" });
    setEpisodes(null);
    setTab("script");
    setTaskStats({ activeJobs: 0, cost: 0 });
    if (window.location.hash !== "#/characters") window.location.hash = "#/characters";
  }, []);

  /** 从任意页面打开「设置」：回到项目大厅并让 PlatformHome 打开设置弹层 */
  const openSettings = useCallback(() => {
    setSettingsSignal((prev) => prev + 1);
    setView({ mode: "projects" });
    setEpisodes(null);
    setTab("script");
    if (window.location.hash !== "#/projects") window.location.hash = "#/projects";
    void loadProjects().catch(() => undefined);
  }, [loadProjects]);

  const applyEpisodeUpdate = useCallback((next: Episode) => {
    setView((prev) => (prev.mode === "workspace" ? { ...prev, episode: next } : prev));
    setEpisodes((prev) => (prev ? prev.map((item) => (item.id === next.id ? next : item)) : prev));
  }, []);

  const handleSynced = useCallback((stats: { activeJobs: number; cost: number }) => {
    lastSyncRef.current = Date.now();
    setTaskStats(stats);
  }, []);

  const handleRetry = useCallback(async () => {
    setRetrying(true);
    await boot(false);
    setRetrying(false);
  }, [boot]);

  const handleProjectCreated = useCallback(
    async (project: Project) => {
      notify("项目「" + project.name + "」已创建", "success");
      try {
        await loadProjects();
        const episodes = await api.episodes(project.id);
        if (episodes.length) await openProject(project);
      } catch {
        /* 项目已创建，剧集初始化失败不阻塞 */
      }
    },
    [loadProjects, notify, openProject]
  );

  if (booting) {
    return (
      <div className="cm-boot">
        <div className="cm-boot-glow" />
        <BrandMark size={64} />
        <h1>造片局</h1>
        <p className="cm-boot-tagline">AI VIDEO STUDIO · 制片工作台</p>
        <div className="cm-boot-loading">
          <Spinner size={16} />
          <span>正在连接后端…</span>
        </div>
      </div>
    );
  }

  if (offline && !projects) {
    return (
      <div className="cm-boot">
        <div className="cm-boot-glow" />
        <BrandMark size={64} />
        <h1>等待后端</h1>
        <p className="cm-boot-tagline">造片局 · AI VIDEO STUDIO</p>
        <div className="cm-boot-card">
          <div className="cm-boot-card-title">
            <i />
            后端未连接
          </div>
          <p>请先启动后端（默认 http://127.0.0.1:8090），界面会自动重连，无需刷新。</p>
          <code>powershell -ExecutionPolicy Bypass -File backend\run.ps1 -Port 8090</code>
          <button type="button" className="cm-btn cm-btn-primary cm-btn-sm" onClick={() => void handleRetry()} disabled={retrying}>
            {retrying ? <Spinner size={14} /> : <IconRefresh size={15} />}
            立即重试
          </button>
        </div>
        <div className="cm-boot-foot">接口 {"/api"} · 静态资源 /media</div>
      </div>
    );
  }

  if (view.mode === "cinema") {
    return <CinemaStudio onExit={goHome} onOpenSettings={openSettings} />;
  }

  if (view.mode === "pipeline") {
    return (
      <PipelineConsole
        onExit={goHome}
        onOpenCharacters={openCharacters}
        onOpenCinema={openCinema}
        onOpenLibrary={() => openLibrary("templates")}
        onOpenSettings={openSettings}
      />
    );
  }

  if (view.mode === "library") {
    return <LibraryPage tab={view.tab} onOpenSettings={openSettings} />;
  }

  if (view.mode === "characters") {
    return (
      <CharactersPage
        onExit={goHome}
        onOpenPipeline={openPipeline}
        onOpenCinema={openCinema}
        onOpenSettings={openSettings}
      />
    );
  }

  if (view.mode === "workspace") {
    return (
      <ProjectStudio
        key={view.episode.id}
        project={view.project}
        episode={view.episode}
        episodes={episodes || [view.episode]}
        tab={tab}
        onTabChange={changeTab}
        onBack={goHome}
        onEpisodeUpdate={applyEpisodeUpdate}
        onSwitchEpisode={switchEpisode}
        onOpenSettings={openSettings}
      />
    );
  }

  if (view.mode === "projects") {
    return (
      <PlatformHome
        projects={projects || []}
        offline={offline}
        onOpenProject={(project) => void openProject(project)}
        onOpenCinema={openCinema}
        onOpenLibrary={() => openLibrary("templates")}
        onOpenFinal={() => openLibrary("final")}
        onProjectCreated={(project) => void handleProjectCreated(project)}
        onRefreshProjects={() => void loadProjects()}
        openSettingsSignal={settingsSignal}
      />
    );
  }

  return null;
}
