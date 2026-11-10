import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api, errorText } from "./lib/api";
import type { Episode, Health, Project } from "./types";
import { ToastProvider, useToast } from "./components/Toast";
import { ProjectStudio } from "./components/studio/Studio";
import { PlatformHome } from "./components/platform/PlatformHome";
import { CinemaStudio } from "./components/cinema/CinemaStudio";
import { PipelineConsole } from "./components/pipeline/PipelineConsole";
import { CharactersPage } from "./components/characters/CharactersPage";
import { LibraryPage, type LibraryTab } from "./components/library/LibraryPage";
import { TemplateWall } from "./components/library/TemplateWall";
import type { CinemaLook } from "./lib/cinema";
import { AgentPage, type AgentTab } from "./components/agent/AgentPage";
import { BrandMark, IconRefresh } from "./components/icons";
import { Spinner } from "./components/ui";

export type TabKey = "script" | "assets" | "storyboard" | "generate" | "finish";

const TAB_KEYS: TabKey[] = ["script", "assets", "storyboard", "generate", "finish"];

type ViewState =
  | { mode: "projects" }
  | { mode: "workspace"; project: Project; episode: Episode }
  | { mode: "cinema" }
  | { mode: "pipeline" }
  | { mode: "agent"; tab: AgentTab }
  | { mode: "characters" }
  | { mode: "library"; tab: LibraryTab; look?: CinemaLook };

const LIBRARY_TABS: LibraryTab[] = ["templates", "styles", "scenes", "final"];
/** V63：成片库观感（写进 URL 的 ?look=） */
const LIBRARY_LOOKS: CinemaLook[] = ["3d_anim", "2d_anim", "realistic"];
/** V49：智能体编排的两个子视图（#/agent、#/agent/season） */
const AGENT_TABS: AgentTab[] = ["workflow", "season"];

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
    // V49：智能体入口上线前的旧 URL
    "/v5/agent": "#/agent",
    "/v6/agent": "#/agent",
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

function parseHash(): { eid?: string; tab: TabKey; cinema?: boolean; pipeline?: boolean; characters?: boolean; library?: LibraryTab; look?: CinemaLook; agent?: AgentTab; template?: string } {
  const rawHash = window.location.hash.replace(/^#\/?/, "");
  const queryAt = rawHash.indexOf("?");
  const raw = queryAt >= 0 ? rawHash.slice(0, queryAt) : rawHash;
  const query = new URLSearchParams(queryAt >= 0 ? rawHash.slice(queryAt + 1) : "");
  const parts = raw.split("/").filter(Boolean);
  if (parts[0] === "cinema") {
    return { tab: "script", cinema: true };
  }
  if (parts[0] === "library") {
    const tab = (LIBRARY_TABS as string[]).includes(parts[1] || "") ? (parts[1] as LibraryTab) : "templates";
    // V63：观感可写 ?look=3d_anim，也兼容 #/library/final/look/3d_anim
    const rawLook = (query.get("look") || (parts[2] === "look" ? parts[3] || "" : "")).trim().toLowerCase();
    const look = (LIBRARY_LOOKS as string[]).includes(rawLook) ? (rawLook as CinemaLook) : undefined;
    return { tab: "script", library: tab, look };
  }
  if (parts[0] === "pipeline") {
    // V23：创建即生成后跳转 #/pipeline?template=<id>（或 sessionStorage 兜底），选中模板并定位第④步
    let stored: string | null = null;
    try {
      stored = sessionStorage.getItem("v23_pipeline_template");
    } catch {
      stored = null;
    }
    return { tab: "script", pipeline: true, template: query.get("template") || stored || undefined };
  }
  if (parts[0] === "characters") {
    return { tab: "script", characters: true };
  }
  if (parts[0] === "agent") {
    const sub = (AGENT_TABS as string[]).includes(parts[1] || "") ? (parts[1] as AgentTab) : "workflow";
    return { tab: "script", agent: sub };
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
  /** 后端在线但返回了 HTTP 错误（≠ 离线）：展示真实 detail，不再触发自动重连 */
  const [bootError, setBootError] = useState("");
  const [view, setView] = useState<ViewState>({ mode: "projects" });
  const [episodes, setEpisodes] = useState<Episode[] | null>(null);
  const [tab, setTab] = useState<TabKey>("script");
  const [taskStats, setTaskStats] = useState<{ activeJobs: number; cost: number }>({ activeJobs: 0, cost: 0 });
  const [retrying, setRetrying] = useState(false);
  const [settingsSignal, setSettingsSignal] = useState(0);
  const [pipelineInitialTemplate, setPipelineInitialTemplate] = useState<string | null>(null);

  const viewRef = useRef(view);
  viewRef.current = view;
  // 心跳防重入 + 连续失败计数：15s 间隔 < 20s 请求超时，旧实现两个请求会重叠，
  // 先失败的旧响应可能把刚恢复的在线状态又打回离线。
  const hbBusyRef = useRef(false);
  const hbFailRef = useRef(0);
  // R7：把「当前这一集」写进 localStorage，供一级导航 02–06 直达
  useEffect(() => {
    if (view.mode !== "workspace") return;
    try {
      localStorage.setItem("zpj_current_episode", view.episode.id);
    } catch {
      /* ignore */
    }
  }, [view]);
  const lastSyncRef = useRef<number | null>(null);
  /** 导航请求序号：连点项目/剧集时丢弃过期响应（与 AgentPage 的 wfSeqRef、LibraryPage 的 inFlightRef 同范式） */
  const navSeqRef = useRef(0);
  /** 离线自动重连次数：最多 3 次（4s/8s/16s），成功或手动重试后归零 */
  const autoRetryRef = useRef(0);

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
        setBootError("");
        lastSyncRef.current = Date.now();
        const route = parseHash();
        if (route.cinema) {
          setView({ mode: "cinema" });
        } else if (route.pipeline && route.template) {
          setPipelineInitialTemplate(route.template);
          try {
            sessionStorage.removeItem("v23_pipeline_template");
          } catch {
            /* ignore */
          }
          if (window.location.hash.includes("?")) window.history.replaceState(null, "", "#/pipeline");
          setView({ mode: "pipeline" });
          setEpisodes(null);
          setTab("script");
          setTaskStats({ activeJobs: 0, cost: 0 });
        } else if (route.library) {
          setView({ mode: "library", tab: route.library, look: route.look });
        } else if (route.pipeline) {
          setView({ mode: "pipeline" });
        } else if (route.agent) {
          setView({ mode: "agent", tab: route.agent });
          setEpisodes(null);
          setTab("script");
          setTaskStats({ activeJobs: 0, cost: 0 });
        } else if (route.characters) {
          setView({ mode: "characters" });
        } else if (route.eid) {
          // V52：剧集 404 ≠ 后端离线 —— 只有 health/projects 失败才置 offline
          try {
            const episode = await api.episode(route.eid);
            const project = list.find((item) => item.id === episode.project_id) || (await api.project(episode.project_id));
            setEpisodes(await api.episodes(project.id));
            setView({ mode: "workspace", project, episode });
            setTab(route.tab);
          } catch (err) {
            if (err instanceof ApiError && err.status === 404) {
              notify("剧集不存在或已被删除：" + route.eid, "error");
              window.history.replaceState(null, "", "#/projects");
              setView({ mode: "projects" });
            } else {
              throw err;
            }
          }
        }
      } catch (err) {
        // 只有「连不上后端」（ApiError.status === 0：网络失败/超时）才算离线并触发自动重连；
        // HTTP 错误（500/422…）说明后端在线，展示真实 detail 并停止重试风暴。
        if (err instanceof ApiError && err.status === 0) {
          setOffline(true);
        } else {
          setOffline(false);
          setBootError(errorText(err));
          notify("后端返回错误：" + errorText(err), "error");
        }
      } finally {
        setBooting(false);
      }
    },
    [loadProjects, notify]
  );

  useEffect(() => {
    void boot(false);
  }, [boot]);

  // 后端重连：每 4s 一次，最多 3 次后自动停止（避免后端不可用时的无限重试风暴）；
  // 手动「立即重试」会把计数归零，再给 3 次机会。
  useEffect(() => {
    if (!offline) {
      autoRetryRef.current = 0;
      return;
    }
    const timer = window.setInterval(() => {
      if (autoRetryRef.current >= 3) {
        window.clearInterval(timer);
        return;
      }
      autoRetryRef.current += 1;
      void boot(true);
    }, 4000);
    return () => window.clearInterval(timer);
  }, [offline, boot]);

  // 在线状态心跳（顶部“实时同步”指示）
  useEffect(() => {
    if (offline) return;
    let alive = true;
    const tick = async () => {
      if (hbBusyRef.current) return;
      hbBusyRef.current = true;
      try {
        const next = await api.health();
        if (!alive) return;
        hbFailRef.current = 0;
        setHealth(next);
        setOffline(false);
      } catch {
        if (!alive) return;
        hbFailRef.current += 1;
        // 单次超时可能只是后端在跑长任务；连续 2 次失败才判离线。
        if (hbFailRef.current >= 2) setOffline(true);
      } finally {
        hbBusyRef.current = false;
      }
    };
    void tick();
    const timer = window.setInterval(() => void tick(), 15000);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, [offline]);

  const openEpisodeById = useCallback(
    async (eid: string, nextTab: TabKey) => {
      const seq = ++navSeqRef.current;
      try {
        const episode = await api.episode(eid);
        const project =
          projects?.find((item) => item.id === episode.project_id) || (await api.project(episode.project_id));
        const list = await api.episodes(project.id);
        if (seq !== navSeqRef.current) return; // 期间又点了别的项目/剧集 → 丢弃本次响应
        setEpisodes(list);
        setView({ mode: "workspace", project, episode });
        setTab(nextTab);
        setTaskStats({ activeJobs: 0, cost: episode.cost || 0 });
      } catch {
        if (seq === navSeqRef.current) window.location.hash = hashFor();
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
        setView({ mode: "library", tab: route.library, look: route.look });
        setTab("script");
        return;
      }
      if (route.pipeline) {
        if (route.template) {
          setPipelineInitialTemplate(route.template);
          try {
            sessionStorage.removeItem("v23_pipeline_template");
          } catch {
            /* ignore */
          }
          if (window.location.hash.includes("?")) window.history.replaceState(null, "", "#/pipeline");
        }
        setView({ mode: "pipeline" });
        setTab("script");
        return;
      }
      if (route.agent) {
        setView({ mode: "agent", tab: route.agent });
        setTab("script");
        setTaskStats({ activeJobs: 0, cost: 0 });
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
      const seq = ++navSeqRef.current;
      try {
        const episodes = await api.episodes(project.id);
        if (seq !== navSeqRef.current) return; // 期间又点了别的项目 → 丢弃本次响应
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
        if (seq === navSeqRef.current) notify(errorText(err), "error");
      }
    },
    [notify]
  );

  /** 切换剧集（同一项目内） */
  const switchEpisode = useCallback(
    async (eid: string) => {
      const current = viewRef.current;
      if (current.mode !== "workspace" || current.episode.id === eid) return;
      const seq = ++navSeqRef.current;
      try {
        const episode = await api.episode(eid);
        const list = await api.episodes(current.project.id);
        if (seq !== navSeqRef.current) return; // 期间又切了别的剧集 → 丢弃本次响应
        setEpisodes(list);
        setView({ mode: "workspace", project: current.project, episode });
        setTab("script");
        setTaskStats({ activeJobs: 0, cost: episode.cost || 0 });
        window.location.hash = hashFor(eid, "script");
      } catch (err) {
        if (seq === navSeqRef.current) notify("切换剧集失败：" + errorText(err), "error");
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

  /** V63：成片库观感写入 URL（replaceState 不产生历史记录；前进/后退由 hashchange 恢复） */
  const changeLibraryLook = useCallback((next: "" | CinemaLook) => {
    const current = viewRef.current;
    if (current.mode !== "library") return;
    setView({ mode: "library", tab: current.tab, look: next || undefined });
    const hash = "#/library/" + current.tab + (next ? "?look=" + next : "");
    if (window.location.hash !== hash) window.history.replaceState(null, "", hash);
  }, []);

  const openPipeline = useCallback(() => {
    setView({ mode: "pipeline" });
    setEpisodes(null);
    setTab("script");
    setTaskStats({ activeJobs: 0, cost: 0 });
    if (window.location.hash !== "#/pipeline") window.location.hash = "#/pipeline";
  }, []);

  /** V49：进入智能体编排（workflow | season），并同步 hash 以支持前进/后退 */
  const openAgent = useCallback((next: AgentTab = "workflow") => {
    setView({ mode: "agent", tab: next });
    setEpisodes(null);
    setTab("script");
    setTaskStats({ activeJobs: 0, cost: 0 });
    const hash = next === "season" ? "#/agent/season" : "#/agent";
    if (window.location.hash !== hash) window.location.hash = hash;
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
    autoRetryRef.current = 0; // 手动重试重新计数
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

  // 后端在线但接口报错（500/422…）：显示真实 detail，不做自动重连
  if (bootError && !projects && !offline) {
    return (
      <div className="cm-boot">
        <div className="cm-boot-glow" />
        <BrandMark size={64} />
        <h1>后端返回错误</h1>
        <p className="cm-boot-tagline">造片局 · AI VIDEO STUDIO</p>
        <div className="cm-boot-card">
          <div className="cm-boot-card-title">
            <i />
            接口错误（后端在线）
          </div>
          <p>{bootError}</p>
          <code>后端已连通，但 /api/health 或 /api/projects 返回了错误；修好后点「重试」即可，不会再自动轰炸。</code>
          <button type="button" className="cm-btn cm-btn-primary cm-btn-sm" onClick={() => void handleRetry()} disabled={retrying}>
            {retrying ? <Spinner size={14} /> : <IconRefresh size={15} />}
            重试
          </button>
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
          <p>请先启动后端（默认 http://127.0.0.1:8090）。界面会自动重连 3 次（每 4s），仍失败请点「立即重试」。</p>
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
        initialTemplateId={pipelineInitialTemplate || undefined}
        onExit={goHome}
        onOpenCharacters={openCharacters}
        onOpenCinema={openCinema}
        onOpenLibrary={() => openLibrary("templates")}
        onOpenSettings={openSettings}
      />
    );
  }

  if (view.mode === "agent") {
    return <AgentPage tab={view.tab} onTabChange={openAgent} onOpenSettings={openSettings} />;
  }

  if (view.mode === "library" && view.tab === "templates") {
    return (
      <TemplateWall
        onProjectCreated={(project) => void handleProjectCreated(project)}
        onOpenSettings={openSettings}
      />
    );
  }

  if (view.mode === "library") {
    return (
      <LibraryPage
        tab={view.tab}
        look={view.look}
        onLookChange={changeLibraryLook}
        onOpenSettings={openSettings}
      />
    );
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
