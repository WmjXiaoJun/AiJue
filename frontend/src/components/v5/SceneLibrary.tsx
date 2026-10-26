// V5 场景库：61 场景网格（AI plate → 程序化预览回退）+ 搜索/标签过滤 + 详情
import { useEffect, useMemo, useState } from "react";
import type { V5CatalogResponse, V5SceneSummary } from "../../lib/v5";
import { mediaSrc, sceneArtUrl, scenePreviewUrl, v5api } from "../../lib/v5";
import { IconClose, IconGrid, IconSearch } from "../icons";
import { EmptyState, Spinner } from "../ui";
import { SmartImage, Tag } from "./shared";

interface Props {
  catalog: V5CatalogResponse | null;
  loading: boolean;
}

type SceneDetail = {
  description?: string;
  layers?: { type?: string }[];
  camera_zones?: Record<string, number[]>;
  anchors?: Record<string, number[]>;
  variants?: string[];
  aspect?: number[];
  seed?: number;
};

export function SceneLibrary({ catalog, loading }: Props) {
  const [search, setSearch] = useState("");
  const [tag, setTag] = useState("");
  const [styleId, setStyleId] = useState("");
  const [selected, setSelected] = useState<V5SceneSummary | null>(null);
  const [detail, setDetail] = useState<SceneDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  useEffect(() => {
    if (!catalog || styleId) return;
    const preferred = catalog.styles.find((style) => style.id === "neon_toon") || catalog.styles[0];
    if (preferred) setStyleId(preferred.id);
  }, [catalog, styleId]);

  useEffect(() => {
    if (!selected) return;
    let alive = true;
    setDetailLoading(true);
    setDetail(null);
    v5api
      .item("scenes", selected.id)
      .then((res) => {
        if (alive) setDetail((res.item || {}) as SceneDetail);
      })
      .catch(() => undefined)
      .finally(() => {
        if (alive) setDetailLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [selected]);

  const tags = useMemo(() => {
    if (!catalog) return [];
    const counts = new Map<string, number>();
    catalog.scenes.forEach((scene) => {
      (scene.tags || []).forEach((item) => counts.set(item, (counts.get(item) || 0) + 1));
    });
    return [...counts.entries()].sort((a, b) => b[1] - a[1]).slice(0, 10).map(([item]) => item);
  }, [catalog]);

  const filtered = useMemo(() => {
    if (!catalog) return [];
    const keyword = search.trim().toLowerCase();
    return catalog.scenes.filter((scene) => {
      if (tag && !(scene.tags || []).includes(tag)) return false;
      if (!keyword) return true;
      const text = [scene.id, scene.label, ...(scene.tags || [])].join(" ").toLowerCase();
      return text.includes(keyword);
    });
  }, [catalog, search, tag]);

  if (!catalog && loading) {
    return (
      <div className="v5-loading">
        <Spinner size={18} /> 正在读取场景库…
      </div>
    );
  }
  if (!catalog) return <EmptyState title="场景库不可用" hint="检查后端 /api/v5/catalog" />;

  return (
    <section className="v5-section">
      <div className="v5-toolbar">
        <div className="v5-toolbar-main">
          <IconGrid size={16} />
          <b>场景库</b>
          <span className="v5-muted">
            {filtered.length} / {catalog.scenes.length} 个分层绘景场景
          </span>
        </div>
        <div className="v5-toolbar-right v5-wrap">
          <label className="v5-input-icon">
            <IconSearch size={14} />
            <input
              className="input input-sm"
              placeholder="搜索场景 id / 名称 / 标签"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
            />
          </label>
          <select
            className="select select-sm"
            value={styleId}
            onChange={(event) => setStyleId(event.target.value)}
            title="按风格调色预览"
          >
            {catalog.styles.map((style) => (
              <option key={style.id} value={style.id}>
                风格：{style.label}
              </option>
            ))}
          </select>
        </div>
      </div>

      <div className="v5-chip-row v5-filter-row">
        <button
          type="button"
          className={"v5-filter-chip" + (tag === "" ? " active" : "")}
          onClick={() => setTag("")}
        >
          全部
        </button>
        {tags.map((item) => (
          <button
            key={item}
            type="button"
            className={"v5-filter-chip" + (tag === item ? " active" : "")}
            onClick={() => setTag(tag === item ? "" : item)}
          >
            {item}
          </button>
        ))}
      </div>

      <div className="v5-scene-grid">
        {filtered.map((scene) => (
          <button
            key={scene.id}
            type="button"
            className="v5-scene-card"
            onClick={() => setSelected(scene)}
          >
            <SmartImage
              className="v5-scene-thumb"
              sources={[sceneArtUrl(scene.id), scenePreviewUrl(scene.id, styleId, "640x360")]}
              alt={scene.label}
              fallback={<span className="v5-muted">无缩略图</span>}
            />
            <div className="v5-scene-body">
              <div className="v5-scene-title">
                <b>{scene.label}</b>
                {scene.custom && <span className="v5-custom-flag">custom</span>}
              </div>
              <div className="v5-muted v5-scene-meta">
                {scene.layer_count || 0} 层 · {(scene.camera_zones || []).length} 机位 ·{" "}
                {(scene.aspect || [16, 9]).join(":")}
              </div>
              <div className="v5-chip-row">
                {(scene.tags || []).slice(0, 3).map((item) => (
                  <Tag key={item}>{item}</Tag>
                ))}
              </div>
            </div>
          </button>
        ))}
      </div>
      {filtered.length === 0 && <EmptyState title="没有匹配场景" hint="换个关键词或标签试试" />}

      {selected && (
        <div className="v5-overlay" onMouseDown={() => setSelected(null)}>
          <div className="v5-modal v5-modal-lg" onMouseDown={(event) => event.stopPropagation()}>
            <div className="v5-modal-head">
              <div>
                <b>{selected.label}</b>
                <span className="v5-muted">
                  {selected.id} · {(selected.aspect || [16, 9]).join(":")} ·{" "}
                  {selected.layer_count || 0} 层
                </span>
              </div>
              <button type="button" className="icon-btn" onClick={() => setSelected(null)} aria-label="关闭">
                <IconClose size={16} />
              </button>
            </div>
            <div className="v5-modal-body v5-scene-detail">
              <SmartImage
                className="v5-scene-preview"
                sources={[
                  sceneArtUrl(selected.id),
                  scenePreviewUrl(selected.id, styleId, "1280x720"),
                ]}
                alt={selected.label}
                fallback={<span className="v5-muted">预览不可用</span>}
              />
              <div className="v5-scene-detail-side">
                {detailLoading && <Spinner size={14} />}
                <div className="v5-detail-block">
                  <span className="v5-detail-label">描述</span>
                  <p className="v5-muted">
                    {detail?.description || "（该场景 JSON 没有 description）"}
                  </p>
                </div>
                <div className="v5-detail-block">
                  <span className="v5-detail-label">图层 layers</span>
                  <div className="v5-chip-row">
                    {(detail?.layers || []).map((layer, index) => (
                      <Tag key={`${layer.type}-${index}`}>{layer.type || "?"}</Tag>
                    ))}
                  </div>
                </div>
                <div className="v5-detail-block">
                  <span className="v5-detail-label">机位 camera_zones</span>
                  <div className="v5-chip-row">
                    {Object.keys(detail?.camera_zones || selected.camera_zones || {})
                      .length === 0 && <span className="v5-muted">—</span>}
                    {Object.keys(detail?.camera_zones || {})
                      .map((zone) => ({ zone }))
                      .concat(
                        Object.keys(detail?.camera_zones || {}).length
                          ? []
                          : (selected.camera_zones || []).map((zone) => ({ zone }))
                      )
                      .map(({ zone }) => (
                        <Tag key={zone}>{zone}</Tag>
                      ))}
                  </div>
                </div>
                <div className="v5-detail-block">
                  <span className="v5-detail-label">角色锚点 anchors</span>
                  <div className="v5-muted">
                    {Object.entries(detail?.anchors || {})
                      .map(([role, xy]) => `${role} (${xy.map((v) => v.toFixed(2)).join(", ")})`)
                      .join(" · ") || "—"}
                  </div>
                </div>
                <div className="v5-chip-row">
                  {(selected.tags || []).map((item) => (
                    <Tag key={item}>{item}</Tag>
                  ))}
                </div>
                {mediaSrc(sceneArtUrl(selected.id)) && (
                  <a
                    className="v5-link-like"
                    href={sceneArtUrl(selected.id)}
                    target="_blank"
                    rel="noreferrer"
                  >
                    AI plate 原图
                  </a>
                )}
              </div>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
