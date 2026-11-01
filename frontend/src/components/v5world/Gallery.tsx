// V5 World · 成片画廊（3D-only）
// 旧 16 支 2D 模板成片已归档到 outputs/_archive_v5_2d_finals/，「最早的视频成片」不再展示；
// 本页唯一数据源：GET /api/cinema/templates（16 模板 3D 电影版），只播放 /media/templates_3d/<id>/final.mp4。
import { useEffect, useMemo, useState } from "react";
import { mediaSrc } from "../../lib/v5";
import type { CinemaTemplate } from "../../lib/cinema";
import { fmtDuration, fmtSize } from "../../lib/cinema";
import type { V5PageProps } from "./V5World";
import { IconAlert, IconClose, IconDownload, IconExternal, IconFilm, IconPlay, IconRefresh, IconSearch } from "../icons";
import { Spinner } from "../ui";

function is3dReady(template: CinemaTemplate | null | undefined): boolean {
  return Boolean(template && template.final.exists && template.final.url);
}

function posterSources(template: CinemaTemplate): string[] {
  return [
    template.poster_url ? mediaSrc(template.poster_url) : "",
    `/media/templates_3d/${template.id}/poster.jpg`,
  ].filter(Boolean);
}

function finalSources(template: CinemaTemplate): string[] {
  return [
    template.final?.url ? mediaSrc(template.final.url) : "",
    `/media/templates_3d/${template.id}/final.mp4`,
  ].filter(Boolean);
}

export function V5Gallery({ catalog, templates3d, loading, error, onRefresh }: V5PageProps) {
  const [playing, setPlaying] = useState<CinemaTemplate | null>(null);
  const [query, setQuery] = useState("");

  const styleLabel = useMemo(() => {
    const map = new Map<string, string>();
    (catalog?.styles || []).forEach((style) => map.set(style.id, style.label));
    return map;
  }, [catalog]);

  const templates = useMemo(() => Object.values(templates3d), [templates3d]);

  const filtered = useMemo(() => {
    const keyword = query.trim().toLowerCase();
    if (!keyword) return templates;
    return templates.filter((template) =>
      [template.id, template.label, template.style, styleLabel.get(template.style)]
        .filter(Boolean)
        .join(" ")
        .toLowerCase()
        .includes(keyword),
    );
  }, [query, styleLabel, templates]);

  const stats = useMemo(() => {
    const ready = templates.filter((template) => is3dReady(template));
    const duration = templates.reduce((sum, template) => sum + (Number(template.final.duration) || 0), 0);
    const size = templates.reduce((sum, template) => sum + (Number(template.final.size) || 0), 0);
    const clipsDone = templates.reduce((sum, template) => sum + (template.clips.done || 0), 0);
    const clipsTotal = templates.reduce((sum, template) => sum + (template.clips.total || 0), 0);
    return { total: templates.length, ready: ready.length, duration, size, clipsDone, clipsTotal };
  }, [templates]);

  useEffect(() => {
    if (!playing) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setPlaying(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [playing]);

  return (
    <div className="cm-page v5w-page">
      <header className="cm-page-head">
        <div className="cm-head-main">
          <span className="cm-kicker">GALLERY · 3D 成片画廊</span>
          <h1 className="cm-h1">成片画廊</h1>
          <p className="cm-sub">
            {stats.total} 支模板的 3D 电影版 · 已就绪 {stats.ready}/{stats.total} · 本地 ComfyUI H3 全量重制 · 点击封面播放
          </p>
        </div>
        <div className="cm-head-actions">
          <a className="v5w-cinema-entry" href="#/cinema/templates" title="进入 Cinema Studio · 模板库 · 3D">
            <IconFilm size={14} /> Cinema 模板库
            <b>→</b>
          </a>
          <button type="button" className="cm-icon-btn" onClick={onRefresh} disabled={loading} title="刷新 3D 成片状态">
            {loading ? <Spinner size={14} /> : <IconRefresh />}
          </button>
        </div>
      </header>

      <section className="st-kpis v5w-kpis">
        <div className="st-kpi">
          <span className="st-kpi-label">3D Films</span>
          <div className="st-kpi-value is-gold">
            {stats.ready}
            <small>/ {stats.total || 16}</small>
          </div>
          <div className="st-kpi-hint">3D 电影版已就绪</div>
        </div>
        <div className="st-kpi">
          <span className="st-kpi-label">Duration</span>
          <div className="st-kpi-value">
            {fmtDuration(stats.duration)}
            <small>合计</small>
          </div>
          <div className="st-kpi-hint">16 支 3D 成片总时长</div>
        </div>
        <div className="st-kpi">
          <span className="st-kpi-label">Volume</span>
          <div className="st-kpi-value">
            {fmtSize(stats.size)}
            <small>合计</small>
          </div>
          <div className="st-kpi-hint">final.mp4 体积合计</div>
        </div>
        <div className="st-kpi">
          <span className="st-kpi-label">3D Clips</span>
          <div className={"st-kpi-value" + (stats.clipsTotal > 0 && stats.clipsDone >= stats.clipsTotal ? " is-jade" : "")}>
            {stats.clipsDone}
            <small>/ {stats.clipsTotal || "—"}</small>
          </div>
          <div className="st-kpi-hint">H3 镜头全量重制</div>
        </div>
      </section>

      <div className="st-toolbar v5w-toolbar">
        <label className="st-search">
          <IconSearch size={14} />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="搜索模板 / 风格"
            aria-label="搜索 3D 成片"
          />
          {query && (
            <button type="button" className="v5w-clear" onClick={() => setQuery("")} aria-label="清空搜索">
              <IconClose size={12} />
            </button>
          )}
        </label>
        <div className="st-toolbar-right">
          <span className="v5w-note cm-mono">3D 版：本地 ComfyUI H3 · 1280×720 / 25fps · 30–45s</span>
          <span className="v5w-count cm-mono">
            {filtered.length} / {stats.total}
          </span>
        </div>
      </div>

      {error && templates.length === 0 ? (
        <div className="cm-error v5w-error">
          <IconAlert size={16} />
          <span>3D 成片状态读取失败：{error}</span>
          <button type="button" className="cm-btn cm-btn-sm" onClick={onRefresh}>
            重试
          </button>
        </div>
      ) : loading && templates.length === 0 ? (
        <div className="cm-loading">
          <Spinner size={16} /> 正在读取 3D 成片…
        </div>
      ) : templates.length === 0 ? (
        <div className="cm-empty v5w-empty">
          <IconFilm size={28} />
          <span>暂无 3D 成片数据：以 GET /api/cinema/templates 为唯一数据源</span>
          <a className="cm-btn cm-btn-sm" href="#/cinema/templates">
            去 Cinema 模板库
          </a>
        </div>
      ) : filtered.length === 0 ? (
        <div className="cm-empty v5w-empty">
          <IconSearch size={26} />
          <span>没有匹配的模板</span>
        </div>
      ) : (
        <div className="v5w-film-grid">
          {filtered.map((template) => (
            <FilmCard
              key={template.id}
              template={template}
              styleLabel={styleLabel.get(template.style) || template.style || "—"}
              onPlay={() => is3dReady(template) && setPlaying(template)}
            />
          ))}
        </div>
      )}

      {playing && <PlayerModal template={playing} onClose={() => setPlaying(null)} />}
    </div>
  );
}

/* ------------------------------ 3D 成片卡 ------------------------------ */
function FilmCard({
  template,
  styleLabel,
  onPlay,
}: {
  template: CinemaTemplate;
  styleLabel: string;
  onPlay: () => void;
}) {
  const ready = is3dReady(template);
  const sources = posterSources(template);
  return (
    <article className={"v5w-film-card" + (ready ? " is-3d" : " is-pending")}>
      <button
        type="button"
        className="v5w-film-cover"
        onClick={onPlay}
        disabled={!ready}
        title={ready ? "播放 3D 电影版" : "3D 版尚未生成"}
      >
        <PosterImage sources={sources} alt={template.label} className="v5w-film-poster" fallback={template.label.slice(0, 4)} />
        {ready && (
          <span className="v5w-play">
            <IconPlay size={20} />
          </span>
        )}
        {ready ? <span className="v5w-badge3d">3D</span> : <span className="v5w-chip is-pending">待生成</span>}
        <span className="v5w-film-style">{styleLabel}</span>
      </button>

      <div className="v5w-film-body">
        <div className="v5w-film-title">
          <b>{template.label}</b>
          <span className="cm-mono v5w-muted">{template.id}</span>
        </div>
        <div className="v5w-film-meta">
          <span className="cm-mono">{ready ? fmtDuration(template.final.duration) : "—"}</span>
          <span className="cm-mono">1280×720</span>
          <span className="cm-mono">{ready ? fmtSize(template.final.size) : "—"}</span>
          <span className="cm-mono">25fps</span>
        </div>
        <div className="v5w-film-row">
          <span className={"st-tag " + (ready ? "is-jade" : "is-slate")}>
            {ready ? "3D 电影版" : "3D 待生成"}
          </span>
          <span className="v5w-chip is-muted">
            镜头 {template.clips.done}/{template.clips.total || "—"}
          </span>
        </div>
        <div className="v5w-film-actions">
          <button type="button" className="cm-btn cm-btn-primary cm-btn-sm" onClick={onPlay} disabled={!ready}>
            <IconPlay size={12} /> 播放 3D 版
          </button>
          {template.report_url && (
            <a className="cm-btn cm-btn-ghost cm-btn-sm" href={mediaSrc(template.report_url)} target="_blank" rel="noreferrer">
              <IconExternal size={12} /> 制作报告
            </a>
          )}
          <a className="cm-btn cm-btn-ghost cm-btn-sm" href="#/cinema/templates" title="在 Cinema 模板库查看该模板">
            <IconFilm size={12} /> 模板库
          </a>
        </div>
      </div>
    </article>
  );
}

/* ------------------------------ 3D 播放弹层 ------------------------------ */
function PlayerModal({ template, onClose }: { template: CinemaTemplate; onClose: () => void }) {
  const sources = finalSources(template);
  const src = sources[0] || "";
  return (
    <div className="v5w-modal-mask" onMouseDown={onClose} role="dialog" aria-modal="true" aria-label={template.label}>
      <div className="v5w-modal" onMouseDown={(event) => event.stopPropagation()}>
        <header className="v5w-modal-head">
          <div className="v5w-modal-head-main">
            <span className="v5w-modal-kicker">3D 电影版 · COMFYUI H3</span>
            <h3 className="v5w-modal-title">
              {template.label}
              <small className="cm-mono">{template.id}</small>
            </h3>
          </div>
          <button type="button" className="v5w-modal-close" onClick={onClose} aria-label="关闭">
            <IconClose size={17} />
          </button>
        </header>
        <video className="v5w-video" controls autoPlay playsInline preload="metadata" key={sources.join("|")}>
          {sources.map((item) => (
            <source key={item} src={item} type="video/mp4" />
          ))}
        </video>
        <footer className="v5w-modal-foot">
          <span className="cm-mono">
            {fmtDuration(template.final.duration)} · {fmtSize(template.final.size)} · 1280×720 / 25fps · 镜头{" "}
            {template.clips.done}/{template.clips.total || "—"}
          </span>
          <span className="v5w-modal-spacer" />
          {src && (
            <a className="cm-btn cm-btn-sm" href={src} target="_blank" rel="noreferrer">
              <IconDownload size={13} /> 打开原片
            </a>
          )}
        </footer>
      </div>
    </div>
  );
}

/* ------------------------------ 海报多级回退 ------------------------------ */
function PosterImage({
  sources,
  alt,
  className,
  fallback,
}: {
  sources: string[];
  alt: string;
  className?: string;
  fallback: string;
}) {
  const list = sources.filter(Boolean);
  const key = list.join("|");
  const [index, setIndex] = useState(0);
  useEffect(() => setIndex(0), [key]);
  if (!list.length || index >= list.length) {
    return <span className="v5w-poster-fallback">{fallback}</span>;
  }
  return <img className={className} src={list[index]} alt={alt} loading="lazy" onError={() => setIndex((prev) => prev + 1)} />;
}
