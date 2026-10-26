import type { Episode, Health, Project } from "../types";
import { fmtMoney } from "../lib/format";
import { IconChevronRight, IconJobs, IconSettings, IconWallet } from "./icons";
import { StatusBadge } from "./ui";

type ViewState =
  | { mode: "projects" }
  | { mode: "workspace"; project: Project; episode: Episode }
  | { mode: "v5" };

interface Props {
  view: ViewState;
  offline: boolean;
  health: Health | null;
  taskStats: { activeJobs: number; cost: number };
  onOpenJobs: () => void;
  onOpenCosts: () => void;
  onSettings: () => void;
  onHome: () => void;
}

export function TopBar({ view, offline, health, taskStats, onOpenJobs, onOpenCosts, onSettings, onHome }: Props) {
  const episode = view.mode === "workspace" ? view.episode : null;
  const project = view.mode === "workspace" ? view.project : null;

  return (
    <header className="topbar">
      <div className="topbar-left">
        {episode && project ? (
          <>
            <button type="button" className="crumb-link" onClick={onHome}>
              {project.name}
            </button>
            <IconChevronRight size={14} className="crumb-sep" />
            <div className="topbar-episode">
              <span className="topbar-ep-no">第 {String(episode.no || 1).padStart(3, "0")} 期</span>
              <h1>{episode.title}</h1>
            </div>
            <StatusBadge status={episode.status} kind="episode" />
          </>
        ) : view.mode === "v5" ? (
          <div className="topbar-episode">
            <span className="topbar-ep-no">V5 · 可变风格世界</span>
            <h1>V5 世界</h1>
          </div>
        ) : (
          <div className="topbar-episode">
            <span className="topbar-ep-no">造片局 · AI 微综艺</span>
            <h1>项目总览</h1>
          </div>
        )}
      </div>

      <div className="topbar-right">
        <div className={"sync-pill" + (offline ? " is-offline" : "")} title="每 2 秒轮询 episode 与 jobs">
          <i className={"live-dot " + (offline ? "offline" : "online")} />
          <span>{offline ? "重连中" : "实时同步"}</span>
        </div>

        <button
          type="button"
          className="topbar-btn"
          onClick={onOpenJobs}
          disabled={!episode}
          title={episode ? "任务中心 · 生成台" : "进入剧集后可用"}
        >
          <IconJobs size={16} />
          <span>任务中心</span>
          {taskStats.activeJobs > 0 && <span className="topbar-badge">{taskStats.activeJobs}</span>}
        </button>

        <button
          type="button"
          className="topbar-btn"
          onClick={onOpenCosts}
          disabled={!episode}
          title={episode ? "查看费用汇总" : "进入剧集后可用"}
        >
          <IconWallet size={16} />
          <span>费用</span>
          <b className="topbar-money">{fmtMoney(taskStats.cost)}</b>
        </button>

        <button type="button" className="icon-btn topbar-settings" onClick={onSettings} title="设置" aria-label="设置">
          <IconSettings size={18} />
        </button>
      </div>
    </header>
  );
}
