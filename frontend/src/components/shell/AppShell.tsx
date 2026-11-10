// R6 · 应用外壳（大幅减法版）：左侧只有 4 项（模板 / 生产 / 成片 / 设置）。
// 路由与页面能力都保留，只是不再从导航暴露；顶栏只留页面名 + 一个主按钮 + 设置。
import { useCallback, useEffect, useState, type ReactNode } from "react";
import {
  IconBoard,
  IconChevronLeft,
  IconChevronRight,
  IconFilm,
  IconJobs,
  IconProjects,
  IconScript,
  IconSettings,
  IconTemplate,
  IconUsers,
} from "../icons";
import "../../styles/cinema.css";
import "../../styles/shell.css";

export type ShellNavKey =
  | "projects"
  | "pipeline"
  | "agent"
  | "characters"
  | "library"
  | "final"
  | "settings"
  | "cinema";

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
  /** 页面级指标（减法后默认不展示，保留兼容） */
  chips?: ShellChip[];
  primaryAction?: ShellPrimaryAction;
  extraActions?: ReactNode;
  chipsExtra?: ReactNode;
  onRefresh?: () => void;
  refreshing?: boolean;
  onOpenSettings: () => void;
  children: ReactNode;
}

interface NavItem {
  key: string;
  no: string;
  label: string;
  href?: string;
  icon: ReactNode;
  match: ShellNavKey[];
  /** 02–06 需要「当前这一集」，没有就禁用并提示 */
  needEpisode?: boolean;
  onClick?: "settings";
}

/** 一级导航：项目大厅 + 五步创作 + 模板 + 设置（其余页面留在路由里，不进导航） */
const NAV_ITEMS: NavItem[] = [
  { key: "projects", no: "01", label: "项目大厅", href: "#/projects", icon: <IconProjects />, match: ["projects"] },
  { key: "script", no: "02", label: "剧本间", icon: <IconScript />, match: [], needEpisode: true },
  { key: "assets", no: "03", label: "资产库", icon: <IconUsers />, match: [], needEpisode: true },
  { key: "storyboard", no: "04", label: "分镜台", icon: <IconBoard />, match: [], needEpisode: true },
  { key: "generate", no: "05", label: "生成台", icon: <IconJobs />, match: [], needEpisode: true },
  { key: "finish", no: "06", label: "成片房", icon: <IconFilm />, match: [], needEpisode: true },
  { key: "templates", no: "07", label: "模板", href: "#/library/templates", icon: <IconTemplate />, match: ["library"] },
];

const NAV_SETTINGS: NavItem = {
  key: "settings",
  no: "08",
  label: "设置",
  icon: <IconSettings />,
  match: ["settings"],
  onClick: "settings",
};

/** 当前这一集（App 打开工作台时写入），用于 02–06 的直达链接 */
function readEpisode(): string {
  try {
    return localStorage.getItem("zpj_current_episode") || "";
  } catch {
    return "";
  }
}

function episodeHref(key: string): string {
  const eid = readEpisode();
  return eid ? `#/episodes/${encodeURIComponent(eid)}/${key}` : "";
}

const STORAGE_KEY = "zpj_shell_open_v1";

function readStoredOpen(): boolean {
  // 窄屏默认收起抽屉：否则 236px 侧栏会盖住内容（截图/手机首屏都会只剩半页）。
  if (typeof window !== "undefined" && window.innerWidth <= 900) return false;
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw === "1") return true;
    if (raw === "0") return false;
  } catch {
    /* ignore */
  }
  return true;
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
  primaryAction,
  extraActions,
  chipsExtra,
  onOpenSettings,
  children,
}: AppShellProps) {
  const [open, setOpen] = useState<boolean>(() => readStoredOpen());

  const toggleOpen = useCallback(() => {
    setOpen((prev) => {
      const next = !prev;
      try {
        localStorage.setItem(STORAGE_KEY, next ? "1" : "0");
      } catch {
        /* ignore */
      }
      return next;
    });
  }, []);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const isActive = (item: NavItem) => Boolean(active && item.match.includes(active));

  const renderItem = (item: NavItem) => {
    const cls = "sh-nav-item" + (isActive(item) ? " is-active" : "");
    const label = (
      <>
        <span className="sh-nav-icon">{item.icon}</span>
        <span className="sh-nav-no">{item.no}</span>
        <span className="sh-nav-label">{item.label}</span>
      </>
    );
    if (item.onClick === "settings") {
      return (
        <button key={item.key} type="button" className={cls} onClick={onOpenSettings} title={item.label}>
          {label}
        </button>
      );
    }
    const href = item.needEpisode ? episodeHref(item.key) : item.href;
    if (!href) {
      return (
        <span key={item.key} className="sh-nav-item is-disabled" title="先在项目大厅选一个项目/一期">
          {label}
        </span>
      );
    }
    return (
      <a
        key={item.key}
        className={cls}
        href={href}
        title={item.label}
        aria-current={isActive(item) ? "page" : undefined}
      >
        {label}
      </a>
    );
  };

  return (
    <div className={"sh-app" + (open ? " is-open" : "")}>
      <div className="sh-scrim" onClick={() => setOpen(false)} role="presentation" />

      <aside className="sh-rail sh-rail-lite" aria-label="主导航">
        <a className="sh-brand" href="#/projects" title="造片局">
          <span className="sh-brand-seal">造</span>
          <span className="sh-brand-text">
            <b>造片局</b>
            <em>AI 漫剧工坊</em>
          </span>
        </a>
        <nav className="sh-groups">
          {NAV_ITEMS.map(renderItem)}
          {renderItem(NAV_SETTINGS)}
        </nav>
        <div className="sh-rail-foot">
          <button type="button" className="sh-collapse-btn" onClick={toggleOpen} title={open ? "收起" : "展开"}>
            {open ? <IconChevronLeft size={14} /> : <IconChevronRight size={14} />}
            <span className="sh-nav-label">收起</span>
          </button>
        </div>
      </aside>

      <div className="sh-main">
        <header className="sh-top">
          <button type="button" className="sh-ico-btn sh-menu-btn" onClick={toggleOpen} aria-label="展开或收起导航">
            <IconTemplate size={15} />
          </button>
          <div className="sh-top-main">
            {(kicker || (breadcrumb && breadcrumb.length > 0)) && (
              <div className="sh-crumbs">
                {kicker && <span>{kicker}</span>}
                {(breadcrumb || []).map((crumb, index) => (
                  <span key={`${crumb.label}-${index}`}>
                    {(index > 0 || kicker) && <i>/</i>}
                    {crumb.href ? <a href={crumb.href}>{crumb.label}</a> : <b>{crumb.label}</b>}
                  </span>
                ))}
              </div>
            )}
            <h1 className="sh-title">{title}</h1>
            {(chipsExtra || titleExtra) && (
              <div className="sh-title-meta">
                {chipsExtra}
                {titleExtra}
              </div>
            )}
          </div>
          <div className="sh-top-actions">
            {extraActions}
            {primaryAction &&
              (primaryAction.href ? (
                <a className="ui-btn is-primary" href={primaryAction.href} title={primaryAction.title}>
                  {primaryAction.icon}
                  {primaryAction.label}
                </a>
              ) : (
                <button
                  type="button"
                  className="ui-btn is-primary"
                  onClick={primaryAction.onClick}
                  disabled={primaryAction.disabled}
                  title={primaryAction.title}
                >
                  {primaryAction.icon}
                  {primaryAction.label}
                </button>
              ))}
            <button type="button" className="sh-ico-btn" onClick={onOpenSettings} title="设置" aria-label="设置">
              <IconSettings size={15} />
            </button>
          </div>
        </header>

        {tabs && tabs.length > 0 && (
          <div className="sh-tabs" role="tablist" aria-label="模块导航">
            {tabs.map((tab) => (
              <button
                key={tab.key}
                type="button"
                role="tab"
                aria-selected={activeTab === tab.key}
                className={"sh-tab" + (activeTab === tab.key ? " is-active" : "")}
                onClick={() => onTabChange?.(tab.key)}
                disabled={tab.disabled}
              >
                {tab.label}
                {tab.badge && <span className="sh-tab-badge">{tab.badge}</span>}
              </button>
            ))}
          </div>
        )}

        <main className="sh-content">
          <div className="sh-content-inner">{children}</div>
        </main>
      </div>
    </div>
  );
}
