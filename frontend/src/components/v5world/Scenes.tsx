// V5 World · 场景库：61 个分层绘景场景（缩略图 + style 筛选 + 搜索 + 详情节）。
// 数据：GET /api/v5/catalog（摘要）+ GET /api/v5/scenes/{id}（详情）+ AI plate / 程序化预览图回退。
import { useEffect, useMemo, useState } from "react";
import { sceneArtUrl, scenePreviewUrl, v5api } from "../../lib/v5";
import type { V5SceneSummary } from "../../lib/v5";
import type { V5PageProps } from "./V5World";
import { IconAlert, IconClose, IconGrid, IconRefresh, IconSearch } from "../icons";
import { Spinner } from "../ui";

interface SceneDetail {
  description?: string;
  layers?: { type?: string }[];
  camera_zones?: Record<string, number[]>;
  anchors?: Record<string, number[]>;
  variants?: string[];
  aspect?: number[];
}

export function V5Scenes({ catalog, loading, error, onRefresh }: V5PageProps) {
  const [search, setSearch] = useState("");
  const [styleFilter, setStyleFilter] = useState("all");
  const [tag, setTag] = useState("");
  const [openId, setOpenId] = useState<string | null>(null);

  const templates = catalog?.templates || [];
  const styles = catalog?.styles || [];
  const scenes = catalog?.scenes || [];

  /** 某风格下模板引用到的场景集合（v5 模板的 scene_route） */
  const scenesByStyle = useMemo(() => {
    const map = new Map<string, Set<string>>();
    templates.forEach((template) => {
      if (!template.style) return;
      const set = map.get(template.style) || new Set<string>();
      (template.scene_route || []).forEach((scene) => set.add(scene));
      map.set(template.style, set);
    });
    return map;
  }, [templates]);

  const usedScenes = useMemo(() => {
    const set = new Set<string>();
    templates.forEach((template) => (template.scene_route || []).forEach((scene) => set.add(scene)));
    return set;
  }, [templates]);

  const topTags = useMemo(() => {
    const counts = new Map<string, number>();
    scenes.forEach((scene) => (scene.tags || []).forEach((item) => counts.set(item, (counts.get(item) || 0) + 1)));
    return [...counts.entries()].sort((a, b) => b[1] - a[1]).slice(0, 12).map(([name]) => name);
  }, [scenes]);

  const styleSceneSet = styleFilter !== "all" ? scenesByStyle.get(styleFilter) || new Set<string>() : null;

  const filtered = useMemo(() => {
    const keyword = search.trim().toLowerCase();
    return scenes.filter((scene) => {
      if (styleSceneSet && !styleSceneSet.has(scene.id)) return false;
      if (tag && !(scene.tags || []).includes(tag)) return false;
      if (!keyword) return true;
      return [scene.id, scene.label, ...(scene.tags || []), ...(scene.layer_types || []), ...(scene.camera_zones || [])]
        .filter(Boolean)
        .join(" ")
        .toLowerCase()
        .includes(keyword);
    });
  }, [scenes, search, styleSceneSet, tag]);

  const stats = useMemo(() => {
    const layers = scenes.reduce((sum, scene) => sum + (scene.layer_count || 0), 0);
    const withAiPlate = scenes.filter((scene) => scene.custom).length;
    return { total: scenes.length, layers, used: usedScenes.size, custom: withAiPlate };
  }, [scenes, usedScenes]);

  const previewStyle = styleFilter !== "all" ? styleFilter : undefined;
  const openScene = openId ? scenes.find((scene) => scene.id === openId) || null : null;

  return (
    <div className="cm-page v5w-page">
      <header className="cm-page-head">
        <div className="cm-head-main">
          <span className="cm-kicker">SCENES · 场景库</span>
          <h1 className="cm-h1">场景库</h1>
          <p className="cm-sub">
            {stats.total} 个分层绘景场景 · {stats.layers} 个图层 · {stats.used} 个被模板引用 · 支持按风格调色预览
          </p>
        </div>
        <div className="cm-head-actions">
          <button type="button" className="cm-icon-btn" onClick={onRefresh} disabled={loading} title="刷新场景库">
            {loading ? <Spinner size={14} /> : <IconRefresh />}
          </button>
        </div>
      </header>

      <section className="st-kpis v5w-kpis">
        <div className="st-kpi">
          <span className="st-kpi-label">Scenes</span>
          <div className="st-kpi-value">
            {stats.total}
            <small>个场景</small>
          </div>
          <div className="st-kpi-hint">contract/v5/scenes</div>
        </div>
        <div className="st-kpi">
          <span className="st-kpi-label">In Use</span>
          <div className="st-kpi-value is-jade">
            {stats.used}
            <small>/ {stats.total}</small>
          </div>
          <div className="st-kpi-hint">被模板引用</div>
        </div>
        <div className="st-kpi">
          <span className="st-kpi-label">Layers</span>
          <div className="st-kpi-value">
            {stats.layers}
            <small>层</small>
          </div>
          <div className="st-kpi-hint">全部场景图层合计</div>
        </div>
        <div className="st-kpi">
          <span className="st-kpi-label">Filtered</span>
          <div className="st-kpi-value is-gold">
            {filtered.length}
            <small>个命中</small>
          </div>
          <div className="st-kpi-hint">
            {styleFilter === "all" ? "全部风格" : "按风格 scene_route 过滤"}
          </div>
        </div>
      </section>

      <div className="st-toolbar v5w-toolbar">
        <label className="st-search">
          <IconSearch size={14} />
          <input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="搜索场景 / 图层 / 机位 / 标签"
            aria-label="搜索场景"
          />
          {search && (
            <button type="button" className="v5w-clear" onClick={() => setSearch("")} aria-label="清空搜索">
              <IconClose size={12} />
            </button>
          )}
        </label>
        <select
          className="cm-select v5w-select"
          value={styleFilter}
          onChange={(event) => setStyleFilter(event.target.value)}
          aria-label="风格筛选"
        >
          <option value="all">全部风格（{styles.length}）</option>
          {styles.map((style) => (
            <option key={style.id} value={style.id}>
              {style.label} · {scenesByStyle.get(style.id)?.size || 0} 场景
            </option>
          ))}
        </select>
        <div className="st-toolbar-right">
          <span className="v5w-count cm-mono">
            {filtered.length} / {scenes.length}
          </span>
        </div>
      </div>

      <div className="v5w-chip-row">
        <button type="button" className={"v5w-filter-chip" + (tag === "" ? " is-active" : "")} onClick={() => setTag("")}>
          全部标签
        </button>
        {topTags.map((item) => (
          <button
            key={item}
            type="button"
            className={"v5w-filter-chip" + (tag === item ? " is-active" : "")}
            onClick={() => setTag(tag === item ? "" : item)}
          >
            {item}
          </button>
        ))}
      </div>

      {error && !scenes.length ? (
        <div className="cm-error v5w-error">
          <IconAlert size={16} />
          <span>场景库读取失败：{error}</span>
          <button type="button" className="cm-btn cm-btn-sm" onClick={onRefresh}>
            重试
          </button>
        </div>
      ) : loading && !scenes.length ? (
        <div className="cm-loading">
          <Spinner size={16} /> 正在读取场景库…
        </div>
      ) : filtered.length === 0 ? (
        <div className="cm-empty v5w-empty">
          <IconGrid size={26} />
          <span>没有匹配的场景</span>
          <span className="cm-subtle">换个关键词 / 标签，或把风格筛选切回「全部风格」</span>
        </div>
      ) : (
        <div className="v5w-scene-grid">
          {filtered.map((scene) => (
            <SceneCard key={scene.id} scene={scene} styleId={previewStyle} onOpen={() => setOpenId(scene.id)} />
          ))}
        </div>
      )}

      {openScene && <SceneDetailModal scene={openScene} styleId={previewStyle} onClose={() => setOpenId(null)} />}
    </div>
  );
}

/* ------------------------------ 场景卡 ------------------------------ */
function SceneCard({ scene, styleId, onOpen }: { scene: V5SceneSummary; styleId?: string; onOpen: () => void }) {
  const [failed, setFailed] = useState(false);
  const thumb = sceneArtUrl(scene.id);
  const visual = failed ? scenePreviewUrl(scene.id, styleId, "640x360") : thumb;
  return (
    <button type="button" className="v5w-scene-card" onClick={onOpen} title={`${scene.label} · ${scene.id}`}>
      <div className="v5w-scene-thumb">
        <img src={visual} alt={scene.label} loading="lazy" onError={() => setFailed(true)} />
        <span className="v5w-scene-mask" />
        <span className="v5w-scene-title">
          <b>{scene.label}</b>
          <em className="cm-mono">{scene.id}</em>
        </span>
        <span className="v5w-scene-layers cm-mono">{scene.layer_count || 0} 层</span>
      </div>
      <div className="v5w-scene-body">
        <div className="v5w-scene-meta cm-mono">
          <span>{(scene.camera_zones || []).length} 机位</span>
          <span>{(scene.aspect || [16, 9]).join(":")}</span>
          <span>{(scene.variants || []).length} 变体</span>
          {scene.custom && <span className="v5w-tag is-jade">custom</span>}
        </div>
        <div className="v5w-scene-tags">
          {(scene.tags || []).slice(0, 4).map((item) => (
            <span key={item} className="v5w-tag">
              {item}
            </span>
          ))}
        </div>
      </div>
    </button>
  );
}

/* ------------------------------ 场景详情 ------------------------------ */
function SceneDetailModal({ scene, styleId, onClose }: { scene: V5SceneSummary; styleId?: string; onClose: () => void }) {
  const [detail, setDetail] = useState<SceneDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [plateFailed, setPlateFailed] = useState(false);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError("");
    v5api
      .item("scenes", scene.id)
      .then((res) => {
        if (alive) setDetail((res.item || {}) as SceneDetail);
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
  }, [scene.id]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const previewSrc = plateFailed ? scenePreviewUrl(scene.id, styleId, "1280x720") : sceneArtUrl(scene.id);
  const aspect = (detail?.aspect || scene.aspect || [16, 9]).join(":");
  const zones = Object.keys(detail?.camera_zones || {});
  const anchors = Object.entries(detail?.anchors || {});
  const layers = (detail?.layers || []).map((layer) => layer.type || "?");

  return (
    <div className="v5w-modal-mask" onMouseDown={onClose} role="dialog" aria-modal="true">
      <div className="v5w-modal is-wide" onMouseDown={(event) => event.stopPropagation()}>
        <header className="v5w-modal-head">
          <div className="v5w-modal-head-main">
            <span className="v5w-modal-kicker">SCENE · {scene.id} · {aspect}</span>
            <h3 className="v5w-modal-title">
              {scene.label}
              <small className="cm-mono">{scene.layer_count || 0} 图层 · {zones.length || (scene.camera_zones || []).length} 机位</small>
            </h3>
          </div>
          <button type="button" className="v5w-modal-close" onClick={onClose} aria-label="关闭">
            <IconClose size={17} />
          </button>
        </header>
        <div className="v5w-modal-body">
          <div className="v5w-scene-detail-grid">
            <div className="v5w-scene-detail-preview">
              <img src={previewSrc} alt={scene.label} onError={() => setPlateFailed(true)} />
            </div>
            <div className="v5w-scene-detail-side">
              {loading ? (
                <div className="cm-loading">
                  <Spinner size={14} /> 读取场景合同…
                </div>
              ) : error ? (
                <div className="cm-error">
                  <IconAlert size={14} />
                  <span>详情读取失败：{error}</span>
                </div>
              ) : (
                <>
                  <p className="v5w-detail-desc">{detail?.description || "该场景 JSON 没有 description（程序化分层绘景）"}</p>
                  <div className="v5w-detail-block">
                    <span className="v5w-detail-label">图层 layers</span>
                    <div className="v5w-scene-tags">
                      {(layers.length ? layers : scene.layer_types || []).map((layer, index) => (
                        <span key={layer + "-" + index} className="v5w-tag">
                          {layer}
                        </span>
                      ))}
                    </div>
                  </div>
                  <div className="v5w-detail-block">
                    <span className="v5w-detail-label">机位 camera_zones</span>
                    <div className="v5w-scene-tags">
                      {(zones.length ? zones : scene.camera_zones || []).map((zone, index) => (
                        <span key={zone + "-" + index} className="v5w-tag is-gold">
                          {zone}
                        </span>
                      ))}
                    </div>
                  </div>
                  <div className="v5w-detail-block">
                    <span className="v5w-detail-label">角色锚点 anchors</span>
                    <p className="v5w-detail-text cm-mono">
                      {anchors.length
                        ? anchors.map(([role, xy]) => `${role} (${xy.map((v) => Number(v).toFixed(2)).join(", ")})`).join(" · ")
                        : "—"}
                    </p>
                  </div>
                  <div className="v5w-detail-block">
                    <span className="v5w-detail-label">变体 variants</span>
                    <div className="v5w-scene-tags">
                      {(detail?.variants || scene.variants || []).map((item, index) => (
                        <span key={item + "-" + index} className="v5w-tag is-muted">
                          {item}
                        </span>
                      ))}
                      {!(detail?.variants || scene.variants || []).length && <span className="v5w-muted">—</span>}
                    </div>
                  </div>
                  <div className="v5w-scene-tags">
                    {(scene.tags || []).map((item) => (
                      <span key={item} className="v5w-tag is-muted">
                        #{item}
                      </span>
                    ))}
                  </div>
                </>
              )}
            </div>
          </div>
        </div>
        <footer className="v5w-modal-foot">
          <span className="cm-mono">/{scenePreviewUrl(scene.id, styleId, "1280x720").split("?")[0]}</span>
          <span className="v5w-modal-spacer" />
          <a
            className="cm-btn cm-btn-sm"
            href={scenePreviewUrl(scene.id, styleId, "1920x1080")}
            target="_blank"
            rel="noreferrer"
          >
            打开大图预览
          </a>
          <a className="cm-btn cm-btn-sm" href={sceneArtUrl(scene.id)} target="_blank" rel="noreferrer">
            AI plate 原图
          </a>
        </footer>
      </div>
    </div>
  );
}
