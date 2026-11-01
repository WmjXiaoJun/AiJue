// V5 World · 风格墙：17 种视觉风格（色板 / 技法 / 角色处理 / 转场 / 预览图）。
// 数据：GET /api/v5/catalog（摘要）+ GET /api/v5/styles/{id}（详情）；
// 预览图优先用该风格下的 3D 电影版海报（真实 H3 画面），回退程序化风格/场景预览。
import { useCallback, useEffect, useMemo, useState } from "react";
import { mediaSrc, previewUrl, v5api } from "../../lib/v5";
import type { V5Palette, V5StyleSummary } from "../../lib/v5";
import type { V5PageProps } from "./V5World";
import { IconAlert, IconClose, IconPalette, IconRefresh, IconSearch } from "../icons";
import { Spinner } from "../ui";

interface StyleDetail {
  description?: string;
  texture?: string[];
  line?: { style?: string; width?: number };
  typography?: { title_font?: string; subtitle?: string; lower_third?: string };
  audio?: { bgm?: string; sfx?: string[]; ambience?: string };
  h3?: { style_prompt?: string };
  packaging?: Record<string, unknown>;
}

interface StylePreview {
  sources: string[];
  from3d: boolean;
} 

export function V5Styles({ catalog, templates3d, loading, error, onRefresh }: V5PageProps) {
  const [search, setSearch] = useState("");
  const [openId, setOpenId] = useState<string | null>(null);

  const templates = catalog?.templates || [];
  const templateCountByStyle = useMemo(() => {
    const map = new Map<string, number>();
    templates.forEach((template) => {
      if (!template.style) return;
      map.set(template.style, (map.get(template.style) || 0) + 1);
    });
    return map;
  }, [templates]);

  const styles = catalog?.styles || [];

  /** 风格代表画面：同风格模板的 3D 海报 → 程序化风格预览 → 代表场景×风格调色 */
  const previewFor = useCallback(
    (styleId: string, size: string): StylePreview => {
      const template = templates.find((item) => item.style === styleId) || null;
      const tpl3d = template ? templates3d[template.id] : null;
      const sources: string[] = [];
      if (tpl3d && tpl3d.final.exists) {
        sources.push(
          tpl3d.poster_url ? mediaSrc(tpl3d.poster_url) : mediaSrc(`/media/templates_3d/${template?.id}/poster.jpg`),
        );
      }
      sources.push(previewUrl("styles", styleId, undefined, size));
      const repScene = (template?.scene_route || [])[0];
      if (repScene) sources.push(previewUrl("scenes", repScene, styleId, size));
      return { sources: Array.from(new Set(sources.filter(Boolean))), from3d: sources.length > 0 && Boolean(tpl3d?.final.exists) };
    },
    [templates, templates3d],
  );

  const filtered = useMemo(() => {
    const keyword = search.trim().toLowerCase();
    if (!keyword) return styles;
    return styles.filter((style) =>
      [style.id, style.label, ...(style.tags || []), ...(style.technique || [])]
        .filter(Boolean)
        .join(" ")
        .toLowerCase()
        .includes(keyword),
    );
  }, [search, styles]);

  const stats = useMemo(() => {
    const techniques = new Set<string>();
    let covered = 0;
    styles.forEach((style) => {
      (style.technique || []).forEach((item) => techniques.add(item));
      if ((templateCountByStyle.get(style.id) || 0) > 0) covered += 1;
    });
    return { total: styles.length, techniques: techniques.size, covered, custom: styles.filter((style) => style.custom).length };
  }, [styles, templateCountByStyle]);

  const openStyle = openId ? styles.find((style) => style.id === openId) || null : null;

  return (
    <div className="cm-page v5w-page">
      <header className="cm-page-head">
        <div className="cm-head-main">
          <span className="cm-kicker">STYLES · 风格墙</span>
          <h1 className="cm-h1">风格墙</h1>
          <p className="cm-sub">
            {stats.total} 种视觉风格 · {stats.techniques} 种技法 · {stats.covered} 种被模板使用 · 点击查看色板与 H3 prompt
          </p>
        </div>
        <div className="cm-head-actions">
          <button type="button" className="cm-icon-btn" onClick={onRefresh} disabled={loading} title="刷新风格墙">
            {loading ? <Spinner size={14} /> : <IconRefresh />}
          </button>
        </div>
      </header>

      <section className="st-kpis v5w-kpis">
        <div className="st-kpi">
          <span className="st-kpi-label">Styles</span>
          <div className="st-kpi-value">
            {stats.total}
            <small>种风格</small>
          </div>
          <div className="st-kpi-hint">contract/v5/styles</div>
        </div>
        <div className="st-kpi">
          <span className="st-kpi-label">Techniques</span>
          <div className="st-kpi-value">
            {stats.techniques}
            <small>种技法</small>
          </div>
          <div className="st-kpi-hint">线稿 / 笔触 / 材质</div>
        </div>
        <div className="st-kpi">
          <span className="st-kpi-label">In Use</span>
          <div className="st-kpi-value is-jade">
            {stats.covered}
            <small>/ {stats.total}</small>
          </div>
          <div className="st-kpi-hint">被 16 模板引用的风格</div>
        </div>
        <div className="st-kpi">
          <span className="st-kpi-label">Custom</span>
          <div className="st-kpi-value">
            {stats.custom}
            <small>个</small>
          </div>
          <div className="st-kpi-hint">自定义覆盖风格</div>
        </div>
      </section>

      <div className="st-toolbar v5w-toolbar">
        <label className="st-search">
          <IconSearch size={14} />
          <input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="搜索风格 / 技法 / 标签"
            aria-label="搜索风格"
          />
          {search && (
            <button type="button" className="v5w-clear" onClick={() => setSearch("")} aria-label="清空搜索">
              <IconClose size={12} />
            </button>
          )}
        </label>
        <div className="st-toolbar-right">
          <span className="v5w-count cm-mono">
            {filtered.length} / {styles.length}
          </span>
        </div>
      </div>

      {error && !styles.length ? (
        <div className="cm-error v5w-error">
          <IconAlert size={16} />
          <span>风格墙读取失败：{error}</span>
          <button type="button" className="cm-btn cm-btn-sm" onClick={onRefresh}>
            重试
          </button>
        </div>
      ) : loading && !styles.length ? (
        <div className="cm-loading">
          <Spinner size={16} /> 正在读取风格墙…
        </div>
      ) : filtered.length === 0 ? (
        <div className="cm-empty v5w-empty">
          <IconPalette size={26} />
          <span>没有匹配的风格</span>
        </div>
      ) : (
        <div className="v5w-style-grid">
          {filtered.map((style) => (
            <StyleCard
              key={style.id}
              style={style}
              preview={previewFor(style.id, "640x360")}
              templateCount={templateCountByStyle.get(style.id) || 0}
              onOpen={() => setOpenId(style.id)}
            />
          ))}
        </div>
      )}

      {openStyle && (
        <StyleDetailModal
          style={openStyle}
          preview={previewFor(openStyle.id, "1280x720")}
          templateCount={templateCountByStyle.get(openStyle.id) || 0}
          onClose={() => setOpenId(null)}
        />
      )}
    </div>
  );
}

/* ------------------------------ 风格卡 ------------------------------ */
function StyleCard({
  style,
  preview,
  templateCount,
  onOpen,
}: {
  style: V5StyleSummary;
  preview: StylePreview;
  templateCount: number;
  onOpen: () => void;
}) {
  const colors = flattenPalette(style.palette, 8);
  return (
    <button type="button" className="v5w-style-card" onClick={onOpen} title={`${style.label} · ${style.id}`}>
      <div className="v5w-style-preview">
        <StyleImage sources={preview.sources} alt={style.label} />
        <span className="v5w-style-preview-mask" />
        <span className="v5w-style-name">
          <b>{style.label}</b>
          <em className="cm-mono">{style.id}</em>
        </span>
        <span className="v5w-style-tpl-count cm-mono">{templateCount} 模板</span>
        {preview.from3d && <span className="v5w-style-3d cm-mono">3D 样片</span>}
      </div>
      <div className="v5w-style-body">
        <div className="v5w-palette">
          {colors.length ? (
            colors.map((color) => <i key={color} style={{ background: color }} title={color} />)
          ) : (
            <span className="v5w-muted">无色板数据</span>
          )}
        </div>
        <div className="v5w-style-tags">
          {(style.technique || []).slice(0, 3).map((item) => (
            <span key={item} className="v5w-tag">
              {item}
            </span>
          ))}
          {style.character_treatment && <span className="v5w-tag is-gold">{style.character_treatment}</span>}
        </div>
        <div className="v5w-style-foot">
          <span className="cm-mono v5w-muted">转场 {style.transition || "—"}</span>
          {style.custom && <span className="v5w-tag is-jade">custom</span>}
        </div>
      </div>
    </button>
  );
}

/* ------------------------------ 风格详情 ------------------------------ */
function StyleDetailModal({
  style,
  preview,
  templateCount,
  onClose,
}: {
  style: V5StyleSummary;
  preview: StylePreview;
  templateCount: number;
  onClose: () => void;
}) {
  const [detail, setDetail] = useState<StyleDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError("");
    v5api
      .item("styles", style.id)
      .then((res) => {
        if (alive) setDetail((res.item || {}) as StyleDetail);
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
  }, [style.id]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const colors = flattenPalette(style.palette, 12);

  return (
    <div className="v5w-modal-mask" onMouseDown={onClose} role="dialog" aria-modal="true">
      <div className="v5w-modal is-wide" onMouseDown={(event) => event.stopPropagation()}>
        <header className="v5w-modal-head">
          <div className="v5w-modal-head-main">
            <span className="v5w-modal-kicker">STYLE · {style.id}</span>
            <h3 className="v5w-modal-title">
              {style.label}
              <small className="cm-mono">{templateCount} 个模板在用</small>
            </h3>
          </div>
          <button type="button" className="v5w-modal-close" onClick={onClose} aria-label="关闭">
            <IconClose size={17} />
          </button>
        </header>
        <div className="v5w-modal-body">
          <div className="v5w-style-detail-grid">
            <div className="v5w-style-detail-preview">
              <StyleImage sources={preview.sources} alt={style.label} />
              {preview.from3d && <span className="v5w-style-3d cm-mono">3D 样片</span>}
            </div>
            <div className="v5w-style-detail-side">
              <p className="v5w-detail-desc">{detail?.description || "该风格没有描述"}</p>
              <div className="v5w-palette is-large">
                {colors.length ? colors.map((color) => <i key={color} style={{ background: color }} title={color} />) : <span className="v5w-muted">—</span>}
              </div>
              {loading ? (
                <div className="cm-loading">
                  <Spinner size={14} /> 读取风格合同…
                </div>
              ) : error ? (
                <div className="cm-error">
                  <IconAlert size={14} />
                  <span>详情读取失败：{error}</span>
                </div>
              ) : (
                <>
                  <div className="v5w-detail-block">
                    <span className="v5w-detail-label">技法 / 材质</span>
                    <div className="v5w-style-tags">
                      {(style.technique || []).map((item) => (
                        <span key={item} className="v5w-tag">
                          {item}
                        </span>
                      ))}
                      {(detail?.texture || []).map((item) => (
                        <span key={item} className="v5w-tag is-gold">
                          {item}
                        </span>
                      ))}
                    </div>
                  </div>
                  <div className="v5w-detail-block">
                    <span className="v5w-detail-label">线条 / 角色处理 / 转场</span>
                    <p className="v5w-detail-text">
                      {detail?.line?.style || "—"} · {detail?.line?.width ?? "—"}px ·{" "}
                      {style.character_treatment || "—"} · {style.transition || "—"}
                    </p>
                  </div>
                  <div className="v5w-detail-block">
                    <span className="v5w-detail-label">字体 / 声音</span>
                    <p className="v5w-detail-text">
                      {detail?.typography?.title_font || "—"} / {detail?.typography?.subtitle || "—"} · BGM{" "}
                      {detail?.audio?.bgm || "—"} · {detail?.audio?.ambience || "—"}
                    </p>
                  </div>
                  {detail?.h3?.style_prompt && (
                    <div className="v5w-detail-block">
                      <span className="v5w-detail-label">H3 风格 prompt</span>
                      <p className="v5w-prompt">{detail.h3.style_prompt}</p>
                    </div>
                  )}
                  <div className="v5w-style-tags">
                    {(style.tags || []).map((tag) => (
                      <span key={tag} className="v5w-tag is-muted">
                        #{tag}
                      </span>
                    ))}
                  </div>
                </>
              )}
            </div>
          </div>
        </div>
        <footer className="v5w-modal-foot">
          <span className="cm-mono">{style.palette ? Object.keys(style.palette).length : 0} 个色板键</span>
          <span className="v5w-modal-spacer" />
          <a
            className="cm-btn cm-btn-sm"
            href={preview.sources[0] || previewUrl("styles", style.id, undefined, "1920x1080")}
            target="_blank"
            rel="noreferrer"
          >
            打开大图预览
          </a>
        </footer>
      </div>
    </div>
  );
}

/* ------------------------------ 风格代表画面（多级回退） ------------------------------ */
function StyleImage({ sources, alt }: { sources: string[]; alt: string }) {
  const list = sources.filter(Boolean);
  const key = list.join("|");
  const [index, setIndex] = useState(0);
  useEffect(() => setIndex(0), [key]);
  if (!list.length || index >= list.length) {
    return <span className="v5w-style-fallback">{alt.slice(0, 2)}</span>;
  }
  return <img src={list[index]} alt={alt} loading="lazy" onError={() => setIndex((prev) => prev + 1)} />;
}

/* ------------------------------ 工具 ------------------------------ */
function flattenPalette(palette: V5Palette | undefined, max = 8): string[] {
  if (!palette) return [];
  const order = ["bg", "fg", "accent", "paper", "shadow", "highlight", "velvet", "stone", "candle"];
  const colors: string[] = [];
  const keys = [...order.filter((key) => key in palette), ...Object.keys(palette)];
  for (const key of keys) {
    const value = palette[key];
    const items = Array.isArray(value) ? value : [value];
    for (const item of items) {
      if (typeof item === "string" && /^#[0-9a-fA-F]{3,8}$/.test(item) && !colors.includes(item)) {
        colors.push(item);
      }
      if (colors.length >= max) return colors;
    }
  }
  return colors;
}
