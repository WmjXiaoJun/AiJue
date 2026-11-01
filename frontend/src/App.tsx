import { useCallback, useEffect, useRef, useState } from "react";
import { api, errorText } from "./lib/api";
import type { Episode, Health, Project } from "./types";
import { ToastProvider, useToast } from "./components/Toast";
import { Sidebar } from "./components/Sidebar";
import { TopBar } from "./components/TopBar";
import { ProjectList } from "./components/ProjectList";
import { ProjectStudio } from "./components/studio/Studio";
import { SettingsModal } from "./components/SettingsModal";
import { TemplateModal } from "./components/TemplateModal";
import { NewProjectModal } from "./components/NewProjectModal";
import { V5Studio } from "./components/V5Studio";
import { CinemaStudio } from "./components/cinema/CinemaStudio";
import { BrandMark, IconRefresh } from "./components/icons";
import { Spinner } from "./components/ui";

export type TabKey = "script" | "assets" | "storyboard" | "generate" | "finish";

const TAB_KEYS: TabKey[] = ["script", "assets", "storyboard", "generate", "finish"];

type ViewState =
  | { mode: "projects" }
  | { mode: "workspace"; project: Project; episode: Episode }
  | { mode: "v5" }
  | { mode: "cinema" };

function parseHash(): { eid?: string; tab: TabKey; v5?: boolean; cinema?: boolean } {
  const raw = window.location.hash.replace(/^#\/?/, "");
  const parts = raw.split("/").filter(Boolean);
  if (parts[0] === "cinema") {
    return { tab: "script", cinema: true };
  }
  if (parts[0] === "v5") {
    return { tab: "script", v5: true };
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
  const [modal, setModal] = useState<null | "settings" | "templates" | "newProject">(null);
  const [taskStats, setTaskStats] = useState<{ activeJobs: number; cost: number }>({ activeJobs: 0, cost: 0 });
  const [retrying, setRetrying] = useState(false);

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
        } else if (route.v5) {
          setView({ mode: "v5" });
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
      const route = parseHash();
      if (route.cinema) {
        setView({ mode: "cinema" });
        setTab("script");
        return;
      }
      if (route.v5) {
        setView({ mode: "v5" });
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

  const openV5 = useCallback(() => {
    setView({ mode: "v5" });
    setEpisodes(null);
    setTab("script");
    setTaskStats({ activeJobs: 0, cost: 0 });
    if (window.location.hash !== "#/v5") window.location.hash = "#/v5";
  }, []);

  const openCinema = useCallback(() => {
    setView({ mode: "cinema" });
    setEpisodes(null);
    setTab("script");
    setTaskStats({ activeJobs: 0, cost: 0 });
    if (window.location.hash !== "#/cinema/overview") window.location.hash = "#/cinema/overview";
  }, []);

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
      setModal(null);
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
      <div className="boot-screen">
        <div className="boot-glow" />
        <BrandMark size={64} />
        <h1>造片局</h1>
        <p className="boot-tagline">AI 微综艺 · 制片工作台</p>
        <div className="boot-loading">
          <Spinner size={16} />
          <span>正在连接后端…</span>
        </div>
      </div>
    );
  }

  if (offline && !projects) {
    return (
      <div className="boot-screen">
        <div className="boot-glow" />
        <BrandMark size={64} />
        <h1>等待后端</h1>
        <p className="boot-tagline">造片局 · AI 微综艺 制片工作台</p>
        <div className="wait-card">
          <div className="wait-title">
            <i className="live-dot offline" />
            后端未连接
          </div>
          <p>请先启动后端（默认 http://127.0.0.1:8090），界面会自动重连，无需刷新。</p>
          <code>powershell -ExecutionPolicy Bypass -File backend\run.ps1 -Port 8090</code>
          <button type="button" className="btn btn-primary" onClick={() => void handleRetry()} disabled={retrying}>
            {retrying ? <Spinner size={14} /> : <IconRefresh size={15} />}
            立即重试
          </button>
        </div>
        <div className="boot-foot">接口 {"/api"} · 静态资源 /media</div>
      </div>
    );
  }

  if (view.mode === "cinema") {
    return <CinemaStudio onExit={goHome} />;
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
      />
    );
  }

  return (
    <div className="app-shell">
      <Sidebar
        projects={projects || []}
        activeProjectId={null}
        offline={offline}
        health={health}
        activeV5={view.mode === "v5"}
        onV5={openV5}
        onCinema={openCinema}
        onHome={goHome}
        onSelectProject={(project) => void openProject(project)}
        onTemplates={() => setModal("templates")}
        onSettings={() => setModal("settings")}
        onNewProject={() => setModal("newProject")}
      />

      <div className="app-main">
        <TopBar
          view={view}
          offline={offline}
          health={health}
          taskStats={taskStats}
          onOpenJobs={() => changeTab("generate")}
          onOpenCosts={() => changeTab("finish")}
          onSettings={() => setModal("settings")}
          onHome={goHome}
        />

        {offline && (
          <div className="offline-banner">
            <i className="live-dot offline" />
            后端连接已断开，正在自动重连…（页面保留最后一次数据）
          </div>
        )}

        <main className="app-content">
          {view.mode === "v5" ? (
            <V5Studio />
          ) : view.mode === "projects" ? (
            <ProjectList
              projects={projects || []}
              onOpenProject={(project) => void openProject(project)}
              onNewProject={() => setModal("newProject")}
            />
          ) : null}
        </main>
      </div>

      <SettingsModal open={modal === "settings"} onClose={() => setModal(null)} health={health} />
      <TemplateModal
        open={modal === "templates"}
        onClose={() => setModal(null)}
        onUse={() => setModal("newProject")}
      />
      <NewProjectModal
        open={modal === "newProject"}
        onClose={() => setModal(null)}
        onCreated={(project) => void handleProjectCreated(project)}
      />
    </div>
  );
}
