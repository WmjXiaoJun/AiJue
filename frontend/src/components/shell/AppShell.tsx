// V19 · 全站唯一 AppShell：240px 主轨（≤1280 图标轨 / <1024 抽屉）+ 64px 顶栏
// + 可选上下文水平 tabs + 统一内容容器（max 1600 / padding 28）。
// 只负责外壳与导航；页面内容/数据逻辑由各页面保留。
import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { api } from "../../lib/api";
import { cinemaApi } from "../../lib/cinema";
import { pipelineApi } from "../../lib/pipeline";
import {
  IconBoard,
  IconClose,
  IconFilm,
  IconGlobe,
  IconGrid,
  IconPalette,
  IconProjects,
  IconRefresh,
  IconSettings,
  IconSpark,
  IconTemplate,
  IconUsers,
  IconVideo,
} from "../icons";
import "../../styles/cinema.css";
import "../../styles/shell.css";

export type ShellNavKey = "projects" | "pipeline" | "characters" | "library" | "final" | "settings";

export interface ShellTab {
  key: string;
  label: string;
  en?: string;
  no?: string;
  disabled?: boolean;
  badge?: string;
}

export interface ShellChip {
  label: string;
  value?: string;
  tone?: "gold" | "jade" | "red" | "slate";
  title?: string;
}

export interface ShellPrimaryAction {
  label: string;
  href?: string;
  onClick?: () => void;
  disabled?: boolean;
  title?: string;
  icon?: ReactNode;
}

export interface AppShellProps {
  active: ShellNavKey | null;
  title: string;
  kicker?: string;
  breadcrumb?: { label: string; href?: string }[];
  titleExtra?: ReactNode;
  tabs?: ShellTab[];
  activeTab?: string;
  onTabChange?: (key: string) => void;
  chips?: ShellChip[];
  primaryAction?: ShellPrimaryAction;
  /** 页头附加操作（渲染在主操作左侧） */
  extraActions?: ReactNode;
  onRefresh?: () => void;
  refreshing?: boolean;
  onOpenSettings: () => void;
  children: ReactNode;
}

const NAV_ITEMS: { key: ShellNavKey; no: string; label: string; href?: string; icon: ReactNode }[] = [
  { key: "projects", no: "01", label: "项目大厅", href: "#/projects", icon: <IconProjects /> },
  { key: "pipeline", no: "02", label: "一键成片", href: "#/pipeline", icon: <IconSpark /> },
  { key: "characters", no: "03", label: "角色与声音", href: "#/characters", icon: <IconUsers /> },
  { key: "library", no: "04", label: "模板与素材", href: "#/library/templates", icon: <IconTemplate /> },
  { key: "final", no: "05", label: "成片库", href: "#/library/final", icon: <IconVideo /> },
  { key: "settings", no: "06", label: "设置", icon: <IconSettings /> },
];

interface SystemStatus {
  backend: "on" | "off" | "wait";
  version: string;
  gpu: "on" | "off" | "wait";
  queue: number;
  pipelineActive: number;
  pipelineLabel: string;
}

export function AppShell({
  active,
  title,
  kicker,
  breadcrumb,
  titleExtra,
  tabs,
  activeTab,
  onTabChange,
  chips,
  primaryAction,
  extraActions,
  onRefresh,
  refreshing,
  onOpenSettings,
  children,
}: AppShellProps) {
  const [status, setStatus] = useState<SystemStatus>({
    backend: "wait",
    version: "",
    gpu: "wait",
    queue: 0,
    pipelineActive: 0,
    pipelineLabel: "空闲",
  });
  const [drawerOpen, setDrawerOpen] = useState(false);

  const loadStatus = useCallback(async () => {
    const next: SystemStatus = { backend: "wait", version: "", gpu: "wait", queue: 0, pipelineActive: 0, pipelineLabel: "空闲" };
    try {
      const health = await api.health();
      next.backend = "on";
      next.version = health.version || "";
    } catch {
      next.backend = "off";
    }
    try {
      const workers = await cinemaApi.workers();
      next.gpu = workers.comfyui ? "on" : "off";
      next.queue = workers.comfyui_queue || 0;
    } catch {
      next.gpu = "wait";
    }
    try {
      const runs = await pipelineApi.runs();
      const activeRuns = runs.filter((run) => run.status === "queued" || run.status === "running");
      next.pipelineActive = activeRuns.length;
      next.pipelineLabel = activeRuns.length
        ? `运行中 ${activeRuns.length}`
        : runs.some((run) => run.status === "queued" || run.status === "running")
          ? "运行中"
          : "空闲";
    } catch {
      next.pipelineLabel = "—";
    }
    setStatus(next);
  }, []);

  useEffect(() => {
    void loadStatus();
    const timer = window.setInterval(() => void loadStatus(), 12000);
    return () => window.clearInterval(timer);
  }, [loadStatus]);

  useEffect(() => {
    if (!drawerOpen) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setDrawerOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [drawerOpen]);

  const systemChips = useMemo<ShellChip[]>(() => {
    return [
      {
        label: "后端",
        value: status.backend === "on" ? "已连接" : status.backend === "off" ? "离线" : "检测中",
        tone: status.backend === "on" ? "jade" : status.backend === "off" ? "red" : "slate",
        title: status.version ? `后端 v${status.version}` : "后端健康检查 /api/health",
      },
      {
        label: "GPU",
        value: status.gpu === "on" ? `ComfyUI ${status.queue} 队` : status.gpu === "off" ? "离线" : "检测中",
        tone: status.gpu === "on" ? "gold" : status.gpu === "off" ? "red" : "slate",
        title: "ComfyUI 生成环境 / 队列",
      },
      {
        label: "任务",
        value: status.pipelineActive ? `pipeline ${status.pipelineLabel}` : "pipeline 空闲",
        tone: status.pipelineActive ? "gold" : "slate",
        title: "pipeline 单并发任务状态",
      },
    ];
  }, [status]);

  const navItems = (
    <nav className="shell-nav" aria-label="全局导航">
      {NAV_ITEMS.map((item) =>
        item.key === "settings" ? (
          <button
            key={item.key}
            type="button"
            className="shell-nav-item"
            onClick={() => {
              setDrawerOpen(false);
              onOpenSettings();
            }}
            title={item.label}
          >
            <span className="shell-nav-icon">{item.icon}</span>
            <span className="shell-nav-label">{item.label}</span>
            <span className="shell-nav-no">{item.no}</span>
          </button>
        ) : (
          <a
            key={item.key}
            className={"shell-nav-item" + (active === item.key ? " is-active" : "")}
            href={item.href}
            onClick={() => setDrawerOpen(false)}
            aria-current={active === item.key ? "page" : undefined}
            title={item.label}
          >
            <span className="shell-nav-icon">{item.icon}</span>
            <span className="shell-nav-label">{item.label}</span>
            <span className="shell-nav-no">{item.no}</span>
          </a>
        ),
      )}
    </nav>
  );

  const railFoot = (
    <div className="shell-rail-foot">
      <div className="shell-rail-status" title="后端健康检查 /api/health">
        <i className={"shell-dot is-" + (status.backend === "on" ? "jade" : status.backend === "off" ? "red" : "slate")} />
        <span>后端</span>
        <b>{status.backend === "on" ? "已连接" : status.backend === "off" ? "离线" : "检测中"}</b>
      </div>
      <div className="shell-rail-status" title="ComfyUI 生成环境 / 队列">
        <i className={"shell-dot is-" + (status.gpu === "on" ? "gold" : status.gpu === "off" ? "red" : "slate")} />
        <span>GPU</span>
        <b>{status.gpu === "on" ? `ComfyUI ${status.queue} 队` : status.gpu === "off" ? "离线" : "检测中"}</b>
      </div>
      <div className="shell-rail-status" title="pipeline 单并发任务">
        <i className={"shell-dot is-" + (status.pipelineActive ? "gold" : "jade")} />
        <span>任务</span>
        <b>{status.pipelineLabel}</b>
      </div>
      <div className="shell-rail-version">V19 · 造片局工作台</div>
    </div>
  );

  return (
    <div className="cm-app shell-app">
      <aside className="shell-rail">
        <a className="shell-brand" href="#/projects" title="造片局 · 项目大厅">
          <span className="shell-brand-seal">造</span>
          <span className="shell-brand-text">
            <b>造片局</b>
            <em>ZAOPIANJU</em>
          </span>
        </a>
        {navItems}
        {railFoot}
      </aside>

      {drawerOpen && (
        <div className="shell-drawer-mask" onMouseDown={() => setDrawerOpen(false)} role="presentation">
          <aside className="shell-drawer" onMouseDown={(event) => event.stopPropagation()} role="dialog" aria-modal="true" aria-label="全局导航">
            <div className="shell-drawer-head">
              <span className="shell-brand-seal">造</span>
              <b>造片局</b>
              <button type="button" className="shell-drawer-close" onClick={() => setDrawerOpen(false)} aria-label="关闭导航">
                <IconClose size={16} />
              </button>
            </div>
            {navItems}
            {railFoot}
          </aside>
        </div>
      )}

      <div className="shell-main">
        <header className="shell-topbar">
          <button type="button" className="shell-menu-btn" onClick={() => setDrawerOpen(true)} aria-label="打开导航">
            <span />
            <span />
            <span />
          </button>
          <div className="shell-topbar-main">
            <div className="shell-crumbs">
              {kicker && <span className="shell-kicker">{kicker}</span>}
              {(breadcrumb || []).map((crumb, index) => (
                <span key={`${crumb.label}-${index}`} className="shell-crumb">
                  {index > 0 && <i>/</i>}
                  {crumb.href ? <a href={crumb.href}>{crumb.label}</a> : <b>{crumb.label}</b>}
                </span>
              ))}
            </div>
            <div className="shell-title-row">
              <h1 className="shell-title">{title}</h1>
              {titleExtra}
            </div>
          </div>
          <div className="shell-topbar-right">
            <div className="shell-chips">
              {systemChips.map((chip) => (
                <span key={chip.label} className={"shell-chip is-" + (chip.tone || "slate")} title={chip.title}>
                  <em>{chip.label}</em>
                  {chip.value}
                </span>
              ))}
              {(chips || []).map((chip) => (
                <span key={chip.label} className={"shell-chip is-" + (chip.tone || "slate")} title={chip.title}>
                  <em>{chip.label}</em>
                  {chip.value}
                </span>
              ))}
            </div>
            {onRefresh && (
              <button type="button" className={"shell-icon-btn" + (refreshing ? " is-busy" : "")} onClick={onRefresh} title="刷新数据">
                <IconRefresh size={15} />
              </button>
            )}
            {extraActions && <div className="shell-extra-actions">{extraActions}</div>}
            {primaryAction && (
              primaryAction.href ? (
                <a className="cm-btn cm-btn-primary shell-cta" href={primaryAction.href} title={primaryAction.title}>
                  {primaryAction.icon} {primaryAction.label}
                </a>
              ) : (
                <button
                  type="button"
                  className="cm-btn cm-btn-primary shell-cta"
                  onClick={primaryAction.onClick}
                  disabled={primaryAction.disabled}
                  title={primaryAction.title}
                >
                  {primaryAction.icon} {primaryAction.label}
                </button>
              )
            )}
          </div>
        </header>

        {tabs && tabs.length > 0 && (
          <div className="shell-tabs" role="tablist" aria-label="模块导航">
            {tabs.map((tab) => (
              <button
                key={tab.key}
                type="button"
                role="tab"
                aria-selected={activeTab === tab.key}
                className={"shell-tab" + (activeTab === tab.key ? " is-active" : "")}
                onClick={() => onTabChange?.(tab.key)}
                disabled={tab.disabled}
                title={tab.en ? `${tab.label} · ${tab.en}` : tab.label}
              >
                {tab.no && <span className="shell-tab-no">{tab.no}</span>}
                <span className="shell-tab-label">{tab.label}</span>
                {tab.en && <span className="shell-tab-en">{tab.en}</span>}
                {tab.badge && <span className="shell-tab-badge">{tab.badge}</span>}
              </button>
            ))}
          </div>
        )}

        <main className="shell-content">
          <div className="shell-content-inner">{children}</div>
        </main>
      </div>
    </div>
  );
}
