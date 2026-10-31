import type { Health, Project } from "../types";
import { fmtMoney, label, PROJECT_STATUS_LABEL } from "../lib/format";
import { templateLabel } from "../lib/templates";
import { BrandMark, IconFilm, IconPlus, IconProjects, IconSettings, IconSpark, IconTemplate } from "./icons";
import { StatusBadge } from "./ui";

interface Props {
  projects: Project[];
  activeProjectId: string | null;
  offline: boolean;
  health: Health | null;
  activeV5?: boolean;
  activeCinema?: boolean;
  onV5?: () => void;
  onCinema?: () => void;
  onHome: () => void;
  onSelectProject: (project: Project) => void;
  onTemplates: () => void;
  onSettings: () => void;
  onNewProject: () => void;
}

export function Sidebar({
  projects,
  activeProjectId,
  offline,
  health,
  activeV5 = false,
  activeCinema = false,
  onV5,
  onCinema,
  onHome,
  onSelectProject,
  onTemplates,
  onSettings,
  onNewProject,
}: Props) {
  return (
    <aside className="sidebar">
      <button type="button" className="brand" onClick={onHome} title="回到项目列表">
        <BrandMark size={38} />
        <span className="brand-text">
          <span className="brand-name">造片局</span>
          <span className="brand-sub">AI 微综艺 · 制片台</span>
        </span>
      </button>

      <div className="rail-section">
        <div className="rail-head">
          <span className="rail-title">
            <IconProjects size={14} /> 项目
          </span>
          <button type="button" className="rail-add" onClick={onNewProject} title="新建项目" aria-label="新建项目">
            <IconPlus size={15} />
          </button>
        </div>
        <div className="project-rail">
          {projects.length === 0 && <div className="rail-empty">暂无项目</div>}
          {projects.map((project) => {
            const active = project.id === activeProjectId;
            return (
              <button
                key={project.id}
                type="button"
                className={"rail-item" + (active ? " active" : "")}
                onClick={() => onSelectProject(project)}
                title={project.name}
              >
                <span className="rail-item-cover" style={{ background: coverGradient(project) }}>
                  <span className="rail-item-no">{String(project.episode_count || 1).padStart(2, "0")}</span>
                </span>
                <span className="rail-item-main">
                  <span className="rail-item-name">{project.name}</span>
                  <span className="rail-item-meta">
                    {templateLabel(project.format_id).replace("横屏 ", "")} · {fmtMoney(project.spent)}
                  </span>
                </span>
                {project.status && <span className={"rail-status rail-status-" + project.status} />}
              </button>
            );
          })}
        </div>
      </div>

      <nav className="rail-nav">
        <button
          type="button"
          className={"rail-link" + (activeCinema ? " active" : "")}
          onClick={onCinema}
          title="Cinema Studio：3D 电影制片（镜头台 / 生成 / 剪辑 / QC）"
        >
          <IconFilm size={17} />
          <span>Cinema 3D</span>
          <span className="rail-link-badge">新</span>
        </button>
        <button
          type="button"
          className={"rail-link" + (activeV5 ? " active" : "")}
          onClick={onV5}
          title="V5 世界：成片画廊 / 风格墙 / 场景库 / 角色库 / 渲染"
        >
          <IconSpark size={17} />
          <span>V5 世界</span>
          <span className="rail-link-badge">V5</span>
        </button>
        <button type="button" className="rail-link" onClick={onTemplates}>
          <IconTemplate size={17} />
          <span>节目模板</span>
          <span className="rail-link-badge">1</span>
        </button>
        <button type="button" className="rail-link" onClick={onSettings}>
          <IconSettings size={17} />
          <span>设置</span>
        </button>
      </nav>

      <div className="rail-foot">
        <div className="conn">
          <i className={"live-dot " + (offline ? "offline" : "online")} />
          <span>{offline ? "等待后端" : "后端已连接"}</span>
        </div>
        <div className="rail-foot-meta">
          {health?.version ? "v" + health.version : "demo"} · 模式 {health?.mode || "mock"}
        </div>
      </div>
    </aside>
  );
}

function coverGradient(project: Project): string {
  const palette = ["#F5C518", "#5AC8FA", "#FF6B6B", "#C89B3C", "#8B7CF6", "#35C08A"];
  let hash = 0;
  for (let i = 0; i < project.id.length; i += 1) hash = (hash * 31 + project.id.charCodeAt(i)) % 997;
  const color = palette[hash % palette.length];
  return "linear-gradient(135deg, " + color + " 0%, rgba(245,197,24,0.05) 70%)";
}

export function statusText(project: Project): string {
  return label(PROJECT_STATUS_LABEL, project.status);
}
