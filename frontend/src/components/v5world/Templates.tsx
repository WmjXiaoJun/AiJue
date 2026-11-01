// V5 World · 模板库：16 个 AI 微综艺模板（风格 / 时长 / 画幅 / 幕结构 / 卡司），
// 可播放 3D 电影版、查看逐幕详情、一键进入渲染台。
import { useEffect, useMemo, useState } from "react";
import type { V5TemplateSummary, V5Output } from "../../lib/v5";
import { mediaSrc, previewUrl, v5api } from "../../lib/v5";
import type { CinemaTemplate } from "../../lib/cinema";
import { fmtDuration } from "../../lib/cinema";
import type { V5PageProps } from "./V5World";
import { IconAlert, IconClose, IconFilm, IconPlay, IconRefresh, IconSearch, IconSpark } from "../icons";
import { Spinner } from "../ui";

const HIDDEN_TEMPLATE_IDS = new Set(["relic_talk", "relic_talk_3d", "custom_holo_talk"]);

interface TemplateDetail {
  aspect?: number[];
  description?: string;
  acts?: { id?: string; name?: string; scene?: string; camera?: string; duration?: number; line?: string; text?: string; characters?: string[]; broll?: boolean }[];
  packaging?: Record<string, unknown>;
}

export function V5Templates({ catalog, outputs, templates3d, loading, error, onRefresh, onRenderTemplate }: V5PageProps) {
  const [search, setSearch] = useState("");
  const [styleFilter, setStyleFilter] = useState("all");
  const [detailId, setDetailId] = useState<string | null>(null);

  const styleLabel = useMemo(() => {
    const map = new Map<string, string>();
    (catalog?.styles || []).forEach((style) => map.set(style.id, style.label));
    return map;
  }, [catalog]);

  const outputMap = useMemo(() => new Map(outputs.map((item) => [item.id, item])), [outputs]);

  const templates = useMemo<V5TemplateSummary[]>(() => {
    const all = catalog?.templates || [];
    const show = all.filter((item) => !HIDDEN_TEMPLATE_IDS.has(item.id) && !item.custom);
    return show.length ? show : all.filter((item) => !item.custom);
  }, [catalog]);

  const filtered = useMemo(() => {
    const keyword = search.trim().toLowerCase();
    return templates.filter((item) => {
      if (styleFilter !== "all" && item.style !== styleFilter) return false;
      if (!keyword) return true;
      const text = [item.id, item.label, item.style, ...(item.tags || []), ...(item.cast || []).map((cast) => cast.character)]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();
      return text.includes(keyword);
    });
  }, [search, styleFilter, templates]);

  const stats = useMemo(() => {
    const ready3d = templates.filter((item) => {
      const tpl = templates3d[item.id];
      return Boolean(tpl && tpl.final.exists);
    }).length;
    const totalActs = templates.reduce((sum, item) => sum + (item.act_count || 0), 0);
    const avgDuration =
      templates.length > 0
        ? templates.reduce((sum, item) => sum + (Number(item.target_duration) || 0), 0) / templates.length
        : 0;
    return { total: templates.length, ready3d, totalActs, avgDuration };
  }, [templates, templates3d]);

  return (
    <div className="cm-page v5w-page">
      <header className="cm-page-head">
        <div className="cm-head-main">
          <span className="cm-kicker">TEMPLATES · 模板库</span>
          <h1 className="cm-h1">模板库</h1>
          <p className="cm-sub">
            {stats.total} 个 AI 微综艺模板 · 3D 电影版 {stats.ready3d}/{stats.total} · 共 {stats.totalActs} 幕 · 平均{" "}
            {stats.avgDuration.toFixed(0)}s
          </p>
        </div>
        <div className="cm-head-actions">
          <button type="button" className="cm-icon-btn" onClick={onRefresh} disabled={loading} title="刷新模板库">
            {loading ? <Spinner size={14} /> : <IconRefresh />}
          </button>
        </div>
      </header>

      <section className="st-kpis v5w-kpis">
        <div className="st-kpi">
          <span className="st-kpi-label">Templates</span>
          <div className="st-kpi-value">
            {stats.total}
            <small>个模板</small>
          </div>
          <div className="st-kpi-hint">contract/v5/templates</div>
        </div>
        <div className="st-kpi">
          <span className="st-kpi-label">3D Ready</span>
          <div className="st-kpi-value is-gold">
            {stats.ready3d}
            <small>/ {stats.total}</small>
          </div>
          <div className="st-kpi-hint">30–45s 3D 电影版</div>
        </div>
        <div className="st-kpi">
          <span className="st-kpi-label">Acts</span>
          <div className="st-kpi-value">
            {stats.totalActs}
            <small>幕</small>
          </div>
          <div className="st-kpi-hint">固定节奏 + 逐幕场景</div>
        </div>
        <div className="st-kpi">
          <span className="st-kpi-label">Avg Length</span>
          <div className="st-kpi-value">
            {stats.avgDuration.toFixed(0)}
            <small>s</small>
          </div>
          <div className="st-kpi-hint">目标时长均值</div>
        </div>
      </section>

      <div className="st-toolbar v5w-toolbar">
        <label className="st-search">
          <IconSearch size={14} />
          <input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="搜索模板 / 风格 / 卡司"
            aria-label="搜索模板"
          />
          {search && (
            <button type="button" className="v5w-clear" onClick={() => setSearch("")} aria-label="清空搜索">
              <IconClose size={12} />
            </button>
          )}
        </label>
        <select className="cm-select v5w-select" value={styleFilter} onChange={(event) => setStyleFilter(event.target.value)} aria-label="风格筛选">
          <option value="all">全部风格（{catalog?.styles.length || 0}）</option>
          {(catalog?.styles || []).map((style) => (
            <option key={style.id} value={style.id}>
              {style.label} · {style.id}
            </option>
          ))}
        </select>
        <div className="st-toolbar-right">
          <span className="v5w-count cm-mono">
            {filtered.length} / {templates.length}
          </span>
        </div>
      </div>

      {error && !templates.length ? (
        <div className="cm-error v5w-error">
          <IconAlert size={16} />
          <span>模板库读取失败：{error}</span>
          <button type="button" className="cm-btn cm-btn-sm" onClick={onRefresh}>
            重试
          </button>
        </div>
      ) : loading && !templates.length ? (
        <div className="cm-loading">
          <Spinner size={16} /> 正在读取 V5 模板…
        </div>
      ) : filtered.length === 0 ? (
        <div className="cm-empty v5w-empty">
          <IconFilm size={26} />
          <span>没有匹配的模板</span>
          <span className="cm-subtle">换个关键词，或把风格筛选切回「全部风格」</span>
        </div>
      ) : (
        <div className="v5w-tpl-grid">
          {filtered.map((template) => {
            const tpl3d = templates3d[template.id] || null;
            const output = outputMap.get(template.id) || null;
            const ready3d = Boolean(tpl3d && tpl3d.final.exists);
            const poster3d = tpl3d?.poster_url ? mediaSrc(tpl3d.poster_url) : mediaSrc(`/media/templates_3d/${template.id}/poster.jpg`);
            const poster2d = output?.poster_url ? mediaSrc(output.poster_url) : "";
            const preview = previewUrl("templates", template.id, undefined, "640x360");
            const sources = ready3d ? [poster3d, poster2d, preview] : [poster2d, poster3d, preview];
            return (
              <article key={template.id} className={"v5w-tpl-card" + (ready3d ? " is-3d" : "")}>
                <div className="v5w-tpl-poster">
                  <PosterImage sources={sources} alt={template.label} className="v5w-tpl-img" fallback={template.label.slice(0, 2)} />
                  {ready3d && <span className="v5w-badge3d">3D</span>}
                  <span className="v5w-tpl-scene-route" title={(template.scene_route || []).join(" → ")}>
                    {(template.scene_route || [])
                      .slice(0, 4)
                      .map((scene) => sceneLabel(scene))
                      .join(" · ")}
                  </span>
                </div>
                <div className="v5w-tpl-body">
                  <div className="v5w-tpl-title">
                    <b>{template.label}</b>
                    <span className="cm-mono v5w-muted">{template.id}</span>
                  </div>
                  <div className="v5w-tpl-meta">
                    <span className="v5w-style-chip">{styleLabel.get(template.style || "") || template.style || "—"}</span>
                    <span className="cm-mono">{fmtDuration(template.target_duration || 0)}</span>
                    <span className="cm-mono">16:9</span>
                    <span className="cm-mono">{template.act_count || 0} 幕</span>
                    <span className="cm-mono">{(template.cast || []).length} 角色</span>
                  </div>
                  <div className="v5w-tpl-cast">
                    {(template.cast || []).slice(0, 3).map((cast) => (
                      <span key={cast.role + cast.character} className="v5w-cast-chip">
                        <em>{roleLabel(cast.role)}</em>
                        {cast.character}
                      </span>
                    ))}
                    {(template.cast || []).length > 3 && <span className="v5w-muted">+{(template.cast || []).length - 3}</span>}
                  </div>
                  <div className="v5w-tpl-actions">
                    <button
                      type="button"
                      className="cm-btn cm-btn-sm"
                      onClick={() => setDetailId(template.id)}
                      title="查看逐幕详情"
                    >
                      详情
                    </button>
                    <button
                      type="button"
                      className="cm-btn cm-btn-sm"
                      onClick={() => onRenderTemplate?.(template.id)}
                      title="带此模板进入渲染台"
                    >
                      <IconSpark size={12} /> 渲染
                    </button>
                    {ready3d && (
                      <a
                        className="cm-btn cm-btn-primary cm-btn-sm"
                        href={mediaSrc(tpl3d?.final.url || `/media/templates_3d/${template.id}/final.mp4`)}
                        target="_blank"
                        rel="noreferrer"
                        title="播放 3D 电影版"
                      >
                        <IconPlay size={12} /> 3D 版
                      </a>
                    )}
                  </div>
                </div>
              </article>
            );
          })}
        </div>
      )}

      {detailId && <TemplateDetailModal id={detailId} templates={templates} templates3d={templates3d} onClose={() => setDetailId(null)} onRender={() => {
        onRenderTemplate?.(detailId);
        setDetailId(null);
      }} />}
    </div>
  );
}

/* ------------------------------ 逐幕详情弹层 ------------------------------ */
function TemplateDetailModal({
  id,
  templates,
  templates3d,
  onClose,
  onRender,
}: {
  id: string;
  templates: V5TemplateSummary[];
  templates3d: Record<string, CinemaTemplate>;
  onClose: () => void;
  onRender: () => void;
}) {
  const [detail, setDetail] = useState<TemplateDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const summary = templates.find((item) => item.id === id) || null;
  const tpl3d = templates3d[id] || null;

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError("");
    v5api
      .item("templates", id)
      .then((res) => {
        if (alive) setDetail((res.item || {}) as TemplateDetail);
      })
      .catch((err) => {
        if (alive) setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [id]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const acts = detail?.acts || [];
  const actRows: NonNullable<TemplateDetail["acts"]> = acts.length
    ? acts
    : (summary?.act_ids || []).map((actId) => ({ id: actId }));
  const aspect = detail?.aspect ? `${detail.aspect[0]}:${detail.aspect[1]}` : "16:9";

  return (
    <div className="v5w-modal-mask" onMouseDown={onClose} role="dialog" aria-modal="true">
      <div className="v5w-modal is-wide" onMouseDown={(event) => event.stopPropagation()}>
        <header className="v5w-modal-head">
          <div className="v5w-modal-head-main">
            <span className="v5w-modal-kicker">TEMPLATE · {summary?.style || "—"} · {aspect}</span>
            <h3 className="v5w-modal-title">
              {summary?.label || id}
              <small className="cm-mono">{id}</small>
            </h3>
          </div>
          <button type="button" className="v5w-modal-close" onClick={onClose} aria-label="关闭">
            <IconClose size={17} />
          </button>
        </header>
        <div className="v5w-modal-body">
          {loading ? (
            <div className="cm-loading">
              <Spinner size={15} /> 正在读取模板合同…
            </div>
          ) : error ? (
            <div className="cm-error">
              <IconAlert size={15} />
              <span>模板详情读取失败：{error}</span>
            </div>
          ) : (
            <>
              <p className="v5w-detail-desc">{detail?.description || "该模板没有描述"}</p>
              <div className="v5w-detail-stats">
                <span>
                  目标时长 <b className="cm-mono">{fmtDuration(summary?.target_duration || 0)}</b>
                </span>
                <span>
                  画幅 <b className="cm-mono">{aspect}</b>
                </span>
                <span>
                  幕数 <b className="cm-mono">{acts.length || summary?.act_count || 0}</b>
                </span>
                <span>
                  卡司 <b className="cm-mono">{(summary?.cast || []).length}</b>
                </span>
                {tpl3d && (
                  <span>
                    3D 版 <b className="cm-mono">{tpl3d.final.exists ? fmtDuration(tpl3d.final.duration) : "—"}</b>
                  </span>
                )}
              </div>
              <div className="v5w-detail-section-title">幕结构 · {acts.length || summary?.act_count || 0} 幕</div>
              <div className="v5w-act-list">
                {actRows.map((act, index) => (
                  <div key={act.id || index} className="v5w-act-row">
                    <span className="cm-mono v5w-act-no">{String(index + 1).padStart(2, "0")}</span>
                    <span className="v5w-act-name">{act.name || act.id || "—"}</span>
                    <span className="v5w-act-scene cm-mono">{sceneLabel(act.scene || (summary?.scene_route || [])[index] || "")}</span>
                    <span className="v5w-act-camera cm-mono">{act.camera || "—"}</span>
                    <span className="cm-mono v5w-act-dur">{act.duration ? `${Number(act.duration).toFixed(1)}s` : "—"}</span>
                    <span className="v5w-act-text">{act.text || (act.broll ? "B-roll" : "—")}</span>
                  </div>
                ))}
              </div>
            </>
          )}
        </div>
        <footer className="v5w-modal-foot">
          <span className="cm-mono">{summary?.scene_route?.length || 0} 个场景节点</span>
          <span className="v5w-modal-spacer" />
          <button type="button" className="cm-btn cm-btn-sm" onClick={onRender}>
            <IconSpark size={12} /> 用此模板渲染
          </button>
          {tpl3d?.final.exists && (
            <a
              className="cm-btn cm-btn-primary cm-btn-sm"
              href={mediaSrc(tpl3d.final.url)}
              target="_blank"
              rel="noreferrer"
            >
              <IconPlay size={12} /> 播放 3D 版
            </a>
          )}
        </footer>
      </div>
    </div>
  );
}

/* ------------------------------ 工具 ------------------------------ */
function sceneLabel(id: string): string {
  if (!id) return "—";
  return id.replace(/_/g, " ");
}

function roleLabel(role: string): string {
  const map: Record<string, string> = {
    host: "主持",
    guest_a: "嘉宾A",
    guest_b: "嘉宾B",
    narrator: "旁白",
    figure_a: "人物A",
    figure_b: "人物B",
  };
  return map[role] || role;
}

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

export { PosterImage as V5TemplatesPoster };
