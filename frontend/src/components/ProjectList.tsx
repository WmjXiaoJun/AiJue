import { useMemo, type CSSProperties } from "react";
import type { Project } from "../types";
import {
  PROJECT_STATUS_LABEL,
  fmtDateTime,
  fmtMoney,
  label,
} from "../lib/format";
import { templateLabel } from "../lib/templates";
import { IconArrowRight, IconFilm, IconPlus } from "./icons";
import { ProgressBar, StatusBadge } from "./ui";

interface Props {
  projects: Project[];
  onOpenProject: (project: Project) => void;
  onNewProject: () => void;
}

export function ProjectList({ projects, onOpenProject, onNewProject }: Props) {
  const stats = useMemo(() => {
    const episodes = projects.reduce((sum, item) => sum + (item.episode_count || 0), 0);
    const spent = projects.reduce((sum, item) => sum + (item.spent || 0), 0);
    const active = projects.filter((item) => item.status === "active" || item.status === "generating").length;
    return { episodes, spent, active };
  }, [projects]);

  return (
    <div className="page page-projects">
      <div className="page-head">
        <div>
          <div className="page-eyebrow">PRODUCTION · 项目制生产</div>
          <h2 className="page-title">项目</h2>
          <p className="page-sub">
            从台本到成片，一档 AI 微综艺的完整制片流水线：编剧间 → 资产库 → 分镜台 → 生成台 → 成片房。
          </p>
        </div>
        <button type="button" className="btn btn-primary" onClick={onNewProject}>
          <IconPlus size={16} />
          新建项目
        </button>
      </div>

      <div className="stat-strip">
        <div className="stat-cell">
          <span className="stat-label">项目</span>
          <b className="stat-value">{projects.length}</b>
        </div>
        <div className="stat-cell">
          <span className="stat-label">剧集</span>
          <b className="stat-value">{stats.episodes}</b>
        </div>
        <div className="stat-cell">
          <span className="stat-label">制作中</span>
          <b className="stat-value">{stats.active}</b>
        </div>
        <div className="stat-cell">
          <span className="stat-label">累计费用</span>
          <b className="stat-value stat-money">{fmtMoney(stats.spent)}</b>
        </div>
        <div className="stat-cell stat-cell-note">
          <span className="stat-label">生成模式</span>
          <b className="stat-value">Mock · 离线可跑</b>
        </div>
      </div>

      {projects.length === 0 ? (
        <div className="project-empty">
          <IconFilm size={30} />
          <h3>还没有项目</h3>
          <p>创建一个项目，选择「横屏 AI 圆桌 · 5 分钟」模式即可开始。</p>
          <button type="button" className="btn btn-primary" onClick={onNewProject}>
            <IconPlus size={16} /> 新建项目
          </button>
        </div>
      ) : (
        <div className="project-grid">
          {projects.map((project, index) => (
            <ProjectCard
              key={project.id}
              project={project}
              index={index}
              onOpen={() => onOpenProject(project)}
            />
          ))}
        </div>
      )}
    </div>
  );
}

const COVER_COLORS = ["#F5C518", "#5AC8FA", "#FF6B6B", "#C89B3C", "#8B7CF6", "#35C08A"];

function ProjectCard({ project, index, onOpen }: { project: Project; index: number; onOpen: () => void }) {
  const accent = COVER_COLORS[index % COVER_COLORS.length];
  const budget = project.budget || 0;
  const spent = project.spent || 0;
  const usedPct = budget > 0 ? Math.min(100, Math.round((spent / budget) * 100)) : 0;
  const epNo = String(project.episode_count || 1).padStart(3, "0");

  return (
    <button type="button" className="project-card" onClick={onOpen} style={{ "--accent": accent } as CSSProperties}>
      <div className="project-cover" style={{ background: `linear-gradient(135deg, ${accent}2e 0%, #0c0f14 58%), radial-gradient(circle at 82% 18%, ${accent}40, transparent 45%)` }}>
        <div className="cover-grid" />
        <div className="cover-ring" />
        <span className="cover-no">EP.{epNo}</span>
        <div className="cover-top">
          <span className="cover-format">{templateLabel(project.format_id)}</span>
          <StatusBadge status={project.status} kind="episode" labelText={label(PROJECT_STATUS_LABEL, project.status)} />
        </div>
        <div className="cover-bottom">
          <div className="cover-name">{project.name}</div>
          <div className="cover-meta">
            <span>{project.aspect || "16:9"}</span>
            <i />
            <span>{project.episode_count || 0} 集</span>
            <i />
            <span>创建于 {fmtDateTime(project.created_at).slice(0, 10)}</span>
          </div>
        </div>
      </div>

      <div className="project-body">
        <div className="project-row">
          <span className="project-label">预算消耗</span>
          <span className="project-cost">
            <b>{fmtMoney(spent)}</b>
            <em>/ {fmtMoney(budget)}</em>
          </span>
        </div>
        <ProgressBar value={usedPct} tone={usedPct > 85 ? "danger" : "accent"} size="sm" />
        <div className="project-foot">
          <span className="project-tip">{usedPct}% 已用</span>
          <span className="project-enter">
            进入工作台 <IconArrowRight size={15} />
          </span>
        </div>
      </div>
    </button>
  );
}
