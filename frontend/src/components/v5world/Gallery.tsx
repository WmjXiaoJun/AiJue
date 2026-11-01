// V5 World · 成片画廊：16 模板成片，3D 电影版优先（海报/播放/时长用 templates_3d），可切回 2D 原版。
// 数据：GET /api/v5/outputs（2D 成片）+ GET /api/cinema/templates（3D 成片状态，shell 已加载）。
import { useEffect, useMemo, useState } from "react";
import type { V5Output } from "../../lib/v5";
import { mediaSrc } from "../../lib/v5";
import type { CinemaTemplate } from "../../lib/cinema";
import { fmtDuration, fmtSize } from "../../lib/cinema";
import type { V5PageProps } from "./V5World";
import { IconAlert, IconCheck, IconClose, IconDownload, IconExternal, IconFilm, IconPlay, IconRefresh } from "../icons";
import { Spinner } from "../ui";

type GalleryMode = "templates" | "all";

interface FilmItem {
  output: V5Output;
  template: CinemaTemplate | null;
  has3d: boolean;
}

interface PlayingItem {
  output: V5Output;
  template: CinemaTemplate | null;
  use3d: boolean;
}

function is3dReady(template: CinemaTemplate | null | undefined): boolean {
  return Boolean(template && template.final.exists && template.final.url);
}

function pad2(n: number): string {
  return String(n).padStart(2, "0");
}

export function V5Gallery({ outputs, templates3d, loading, error, onRefresh }: V5PageProps) {
  const [mode, setMode] = useState<GalleryMode>("templates");
  const [force2d, setForce2d] = useState<Set<string>>(() => new Set());
  const [playing, setPlaying] = useState<PlayingItem | null>(null);

  const templateIds = useMemo(() => Object.keys(templates3d), [templates3d]);
  const canonical = useMemo(() => new Set(templateIds), [templateIds]);

  const items = useMemo<FilmItem[]>(() => {
    const byId = new Map(outputs.map((item) => [item.id, item]));
    const list: FilmItem[] = [];
    // 1) 16 模板：按 3D 模板顺序（后端稳定顺序）排列，缺成片也占位
    for (const id of templateIds) {
      const output = byId.get(id);
      if (!output) continue;
      list.push({ output, template: templates3d[id] || null, has3d: is3dReady(templates3d[id]) });
      byId.delete(id);
    }
    // 2) 3D 接口异常时：全部成片按 2D 顺序铺开（不回退假数据）
    const rest = Array.from(byId.values()).sort((a, b) => String(a.id).localeCompare(String(b.id)));
    for (const output of rest) {
      const template = templates3d[output.id] || null;
      list.push({ output, template, has3d: is3dReady(template) });
    }
    if (mode === "templates" && canonical.size > 0) {
      return list.filter((item) => canonical.has(item.output.id));
    }
    return list;
  }, [canonical, mode, outputs, templateIds, templates3d]);

  const stats = useMemo(() => {
    const with3d = items.filter((item) => item.has3d).length;
    const qcPass = items.filter((item) => item.output.qc_ok).length;
    const duration3d = items.reduce(
      (sum, item) =>
        sum + (item.has3d ? Number(item.template?.final.duration) || 0 : Number(item.output.duration) || 0),
      0,
    );
    return { total: items.length, with3d, qcPass, duration: duration3d };
  }, [items]);

  const openPlayer = (item: FilmItem) => {
    const use3d = item.has3d && !force2d.has(item.output.id);
    setPlaying({ output: item.output, template: item.template, use3d });
  };
  const toggleMode = (id: string) => {
    setForce2d((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const playable = (item: FilmItem): string => {
    if (item.has3d && !force2d.has(item.output.id)) {
      return item.template?.final.url || `/media/templates_3d/${item.output.id}/final.mp4`;
    }
    // 2D 原版：优先 Lead 指定的 outputs/templates_v5 产物路径（v5api 的 /media/v5 前缀可能尚未挂载）
    return `/media/templates_v5/${item.output.id}/final.mp4`;
  };

  return (
    <div className="cm-page v5w-page">
      <header className="cm-page-head">
        <div className="cm-head-main">
          <span className="cm-kicker">GALLERY · 成片画廊</span>
          <h1 className="cm-h1">成片画廊</h1>
          <p className="cm-sub">
            {stats.total} 支成片 · 3D 电影版 {stats.with3d} 支 · QC 通过 {stats.qcPass}/{stats.total} · 点击封面播放
          </p>
        </div>
        <div className="cm-head-actions">
          <a className="v5w-cinema-entry" href="#/cinema/templates" title="进入 Cinema Studio · 模板库 · 3D">
            <IconFilm size={14} /> Cinema 模板库
            <b>→</b>
          </a>
          <button type="button" className="cm-icon-btn" onClick={onRefresh} disabled={loading} title="刷新成片 / 3D 状态">
            {loading ? <Spinner size={14} /> : <IconRefresh />}
          </button>
        </div>
      </header>

      <section className="st-kpis v5w-kpis">
        <div className="st-kpi">
          <span className="st-kpi-label">Films</span>
          <div className="st-kpi-value">
            {stats.total}
            <small>支成片</small>
          </div>
          <div className="st-kpi-hint">templates_v5 扫描结果</div>
        </div>
        <div className="st-kpi">
          <span className="st-kpi-label">3D Edition</span>
          <div className={"st-kpi-value" + (stats.with3d > 0 ? " is-gold" : "")}>
            {stats.with3d}
            <small>/ {stats.total}</small>
          </div>
          <div className="st-kpi-hint">3D 电影版已就绪</div>
        </div>
        <div className="st-kpi">
          <span className="st-kpi-label">QC Pass</span>
          <div className={"st-kpi-value" + (stats.qcPass === stats.total && stats.total > 0 ? " is-jade" : "")}>
            {stats.qcPass}
            <small>/ {stats.total}</small>
          </div>
          <div className="st-kpi-hint">2D 原版 16 项硬门</div>
        </div>
        <div className="st-kpi">
          <span className="st-kpi-label">Duration</span>
          <div className="st-kpi-value">
            {fmtDuration(stats.duration)}
            <small>合计</small>
          </div>
          <div className="st-kpi-hint">当前展示版本时长合计</div>
        </div>
      </section>

      <div className="st-toolbar v5w-toolbar">
        <div className="st-seg" role="tablist" aria-label="成片范围">
          <button type="button" className={mode === "templates" ? "is-active" : ""} onClick={() => setMode("templates")}>
            16 模板成片
          </button>
          <button type="button" className={mode === "all" ? "is-active" : ""} onClick={() => setMode("all")}>
            全部成片（{outputs.length}）
          </button>
        </div>
        <div className="st-toolbar-right">
          <span className="v5w-note cm-mono">
            3D 版：本地 ComfyUI H3 · 1280×720 / 25fps · 30–45s
          </span>
        </div>
      </div>

      {error && !outputs.length ? (
        <div className="cm-error v5w-error">
          <IconAlert size={16} />
          <span>成片列表读取失败：{error}</span>
          <button type="button" className="cm-btn cm-btn-sm" onClick={onRefresh}>
            重试
          </button>
        </div>
      ) : loading && !outputs.length ? (
        <div className="cm-loading">
          <Spinner size={16} /> 正在读取成片画廊…
        </div>
      ) : items.length === 0 ? (
        <div className="cm-empty v5w-empty">
          <IconFilm size={28} />
          <span>还没有成片：先到「渲染台」提交一次 render_v5</span>
          <button type="button" className="cm-btn cm-btn-sm" onClick={() => window.location.hash = "#/v5/render"}>
            去渲染台
          </button>
        </div>
      ) : (
        <div className="v5w-film-grid">
          {items.map((item) => (
            <FilmCard
              key={item.output.id}
              item={item}
              forced2d={force2d.has(item.output.id)}
              onPlay={() => openPlayer(item)}
              onToggleMode={() => toggleMode(item.output.id)}
              playableUrl={playable(item)}
            />
          ))}
        </div>
      )}

      {playing && <PlayerModal playing={playing} onClose={() => setPlaying(null)} onSwitch2d={() => {
        const id = playing.output.id;
        toggleMode(id);
        setPlaying({ output: playing.output, template: playing.template, use3d: false });
      }} />}
    </div>
  );
}

/* ------------------------------ 成片卡 ------------------------------ */
function FilmCard({
  item,
  forced2d,
  onPlay,
  onToggleMode,
  playableUrl,
}: {
  item: FilmItem;
  forced2d: boolean;
  onPlay: () => void;
  onToggleMode: () => void;
  playableUrl: string;
}) {
  const { output, template, has3d } = item;
  const use3d = has3d && !forced2d;
  const poster3d = template?.poster_url
    ? mediaSrc(template.poster_url)
    : mediaSrc(`/media/templates_3d/${output.id}/poster.jpg`);
  // 2D 原版海报：outputs/templates_v5 产物优先（v5api 的 /media/v5 前缀可能尚未挂载）
  const poster2d = [
    `/media/templates_v5/${output.id}/contact_sheet.png`,
    output.poster_url ? mediaSrc(output.poster_url) : "",
    `/media/templates_v5/${output.id}/frames/frame_01.png`,
  ].filter(Boolean);
  const sources = use3d ? [poster3d, ...poster2d] : [...poster2d, poster3d];
  const duration = use3d
    ? template?.final.duration
      ? fmtDuration(template.final.duration)
      : "—"
    : output.duration
      ? fmtDuration(output.duration)
      : "—";
  const resolution = use3d ? "1280×720" : output.resolution || "—";
  const size = use3d && template?.final.size ? fmtSize(template.final.size) : output.shots ? `${output.shots} 镜` : "—";

  return (
    <article className={"v5w-film-card" + (use3d ? " is-3d" : "")}>
      <button type="button" className="v5w-film-cover" onClick={onPlay} title={use3d ? "播放 3D 电影版" : "播放 2D 原版"}>
        <PosterImage sources={sources} alt={output.label} className="v5w-film-poster" fallback={output.label.slice(0, 4)} />
        {playableUrl && (
          <span className="v5w-play">
            <IconPlay size={20} />
          </span>
        )}
        {use3d && <span className="v5w-badge3d">3D</span>}
        <span className="v5w-film-style">{output.style_label || output.style || "—"}</span>
      </button>

      <div className="v5w-film-body">
        <div className="v5w-film-title">
          <b>{output.label}</b>
          <span className="cm-mono v5w-muted">{output.id}</span>
        </div>
        <div className="v5w-film-meta">
          <span className="cm-mono">{duration}</span>
          <span className="cm-mono">{resolution}</span>
          <span className="cm-mono">{size}</span>
          <span className="cm-mono">{output.fps ? output.fps + "fps" : "—"}</span>
        </div>
        <div className="v5w-film-row">
          <span className={"st-tag " + (output.qc_ok ? "is-jade" : "is-red")}>
            {output.qc_ok ? "QC 通过" : "QC 未通过"}
            {typeof output.qc_checks === "number" && output.qc_total ? ` · ${output.qc_checks}/${output.qc_total}` : ""}
          </span>
          {use3d ? (
            <span className="v5w-chip3d">
              <i /> 3D 电影版
            </span>
          ) : has3d ? (
            <span className="v5w-chip">2D 原版</span>
          ) : (
            <span className="v5w-chip is-muted">2D · 无 3D 版</span>
          )}
        </div>
        <div className="v5w-film-actions">
          <button type="button" className="cm-btn cm-btn-primary cm-btn-sm" onClick={onPlay} disabled={!playableUrl}>
            <IconPlay size={12} /> 播放
          </button>
          {output.timeline_url && (
            <a
              className="cm-btn cm-btn-ghost cm-btn-sm"
              href={`/media/templates_v5/${output.id}/timeline.html`}
              target="_blank"
              rel="noreferrer"
            >
              <IconExternal size={12} /> 时间线
            </a>
          )}
          {output.qc_url && (
            <a
              className="cm-btn cm-btn-ghost cm-btn-sm"
              href={`/media/templates_v5/${output.id}/qc_report.json`}
              target="_blank"
              rel="noreferrer"
            >
              QC
            </a>
          )}
        </div>
        {has3d && (
          <button type="button" className="v5w-mode-toggle" onClick={onToggleMode}>
            {use3d ? "切换 2D 原版" : "切换 3D 电影版"}
          </button>
        )}
      </div>
    </article>
  );
}

/* ------------------------------ 播放弹层 ------------------------------ */
function PlayerModal({
  playing,
  onClose,
  onSwitch2d,
}: {
  playing: PlayingItem;
  onClose: () => void;
  onSwitch2d: () => void;
}) {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const { output, template, use3d } = playing;
  // 3D 版：cinema API 的 final.url（本地 ComfyUI H3 产物）；2D 原版：outputs/templates_v5 产物
  const sources = use3d
    ? [
        template?.final.url ? mediaSrc(template.final.url) : "",
        `/media/templates_3d/${output.id}/final.mp4`,
      ]
    : [
        `/media/templates_v5/${output.id}/final.mp4`,
        output.final_url ? mediaSrc(output.final_url) : "",
      ];
  const srcList = Array.from(new Set(sources.filter(Boolean)));
  const src = srcList[0] || "";

  return (
    <div className="v5w-modal-mask" onMouseDown={onClose} role="dialog" aria-modal="true" aria-label={output.label}>
      <div className="v5w-modal" onMouseDown={(event) => event.stopPropagation()}>
        <header className="v5w-modal-head">
          <div className="v5w-modal-head-main">
            <span className="v5w-modal-kicker">{use3d ? "3D 电影版 · COMFYUI H3" : "2D 原版 · RENDER_V5"}</span>
            <h3 className="v5w-modal-title">
              {output.label}
              <small className="cm-mono">{output.id}</small>
            </h3>
          </div>
          <button type="button" className="v5w-modal-close" onClick={onClose} aria-label="关闭">
            <IconClose size={17} />
          </button>
        </header>
        <video className="v5w-video" controls autoPlay playsInline preload="metadata" key={srcList.join("|")}>
          {srcList.map((item) => (
            <source key={item} src={item} type="video/mp4" />
          ))}
        </video>
        <footer className="v5w-modal-foot">
          <span className="cm-mono">
            {use3d
              ? `${fmtDuration(template?.final.duration || 0)} · ${fmtSize(template?.final.size || 0)} · 1280×720 / 25fps`
              : `${fmtDuration(output.duration || 0)} · ${output.resolution || "—"} · ${output.fps || "?"}fps`}
          </span>
          <span className="v5w-modal-spacer" />
          {use3d && (
            <button type="button" className="cm-btn cm-btn-sm" onClick={onSwitch2d}>
              <IconCheck size={13} /> 切换 2D 原版
            </button>
          )}
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
