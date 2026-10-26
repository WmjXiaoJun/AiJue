// V5 风格墙：16 风格卡（技法/色板/角色处理/标签），点击看代表场景预览
import { useEffect, useState } from "react";
import type { V5CatalogResponse, V5StyleSummary } from "../../lib/v5";
import { mediaSrc, previewUrl, representativeScene, v5api } from "../../lib/v5";
import { IconClose, IconPalette, IconSpark } from "../icons";
import { EmptyState, Spinner } from "../ui";
import { Palette, SmartImage, Tag } from "./shared";

interface Props {
  catalog: V5CatalogResponse | null;
  loading: boolean;
}

type StyleDetail = {
  technique?: string[];
  audio?: { bgm?: string; sfx?: string | string[]; ambience?: string };
  typography?: Record<string, unknown>;
  h3?: { style_prompt?: string };
  packaging?: Record<string, unknown>;
  line?: Record<string, unknown>;
  texture?: string[];
};

export function StyleWall({ catalog, loading }: Props) {
  const [selected, setSelected] = useState<V5StyleSummary | null>(null);
  const [detail, setDetail] = useState<StyleDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  useEffect(() => {
    if (!selected) return;
    let alive = true;
    setDetailLoading(true);
    setDetail(null);
    v5api
      .item("styles", selected.id)
      .then((res) => {
        if (alive) setDetail((res.item || {}) as StyleDetail);
      })
      .catch(() => undefined)
      .finally(() => {
        if (alive) setDetailLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [selected]);

  if (!catalog && loading) {
    return (
      <div className="v5-loading">
        <Spinner size={18} /> 正在读取风格库…
      </div>
    );
  }
  if (!catalog) return <EmptyState title="风格库不可用" hint="检查后端 /api/v5/catalog" />;

  const scene = selected ? representativeScene(catalog, selected.id) : null;
  const previewSrc = selected
    ? [
        scene ? previewUrl("scenes", scene, selected.id, "1024x576") : null,
        scene ? null : null,
      ]
    : [];

  return (
    <section className="v5-section">
      <div className="v5-toolbar">
        <div className="v5-toolbar-main">
          <IconPalette size={16} />
          <b>风格墙</b>
          <span className="v5-muted">{catalog.styles.length} 种视觉风格 · 点击查看技法/色板/代表场景</span>
        </div>
      </div>

      <div className="v5-style-grid">
        {catalog.styles.map((style) => (
          <button
            key={style.id}
            type="button"
            className="v5-style-card"
            onClick={() => setSelected(style)}
          >
            <div className="v5-style-card-top">
              <span className="v5-style-name">{style.label}</span>
              {style.custom && <span className="v5-custom-flag">custom</span>}
            </div>
            <Palette palette={style.palette} size={20} />
            <div className="v5-chip-row">
              {(style.technique || []).slice(0, 3).map((item) => (
                <Tag key={item}>{item}</Tag>
              ))}
              {style.character_treatment && <Tag>{style.character_treatment}</Tag>}
            </div>
            <div className="v5-style-foot">
              <span className="v5-muted">{style.transition || "—"}</span>
              <span className="v5-link-like">
                <IconSpark size={12} /> 场景预览
              </span>
            </div>
          </button>
        ))}
      </div>

      {selected && (
        <div className="v5-overlay" onMouseDown={() => setSelected(null)}>
          <div className="v5-modal" onMouseDown={(event) => event.stopPropagation()}>
            <div className="v5-modal-head">
              <div>
                <b>{selected.label}</b>
                <span className="v5-muted">{selected.id}</span>
              </div>
              <button type="button" className="icon-btn" onClick={() => setSelected(null)} aria-label="关闭">
                <IconClose size={16} />
              </button>
            </div>
            <div className="v5-modal-body v5-style-detail">
              <div className="v5-style-detail-left">
                <Palette palette={selected.palette} size={26} />
                <div className="v5-detail-block">
                  <span className="v5-detail-label">技法 technique</span>
                  <div className="v5-chip-row">
                    {(selected.technique || []).map((item) => (
                      <Tag key={item}>{item}</Tag>
                    ))}
                  </div>
                </div>
                <div className="v5-detail-block">
                  <span className="v5-detail-label">角色处理 / 转场</span>
                  <div className="v5-muted">
                    {selected.character_treatment || "—"} · {selected.transition || "—"}
                  </div>
                </div>
                {detailLoading && <Spinner size={14} />}
                {detail && (
                  <>
                    <div className="v5-detail-block">
                      <span className="v5-detail-label">纹理 / 线条</span>
                      <div className="v5-muted">
                        {(detail.texture || []).join(" / ") || "—"} ·{" "}
                        {String(detail.line?.style || "—")} × {String(detail.line?.width ?? "—")}
                      </div>
                    </div>
                    <div className="v5-detail-block">
                      <span className="v5-detail-label">声音 audio</span>
                      <div className="v5-muted">
                        BGM {detail.audio?.bgm || "—"} · SFX{" "}
                        {Array.isArray(detail.audio?.sfx)
                          ? detail.audio?.sfx.join(", ")
                          : detail.audio?.sfx || "—"}
                      </div>
                    </div>
                    <div className="v5-detail-block">
                      <span className="v5-detail-label">H3 风格 prompt</span>
                      <p className="v5-prompt">{detail.h3?.style_prompt || "—"}</p>
                    </div>
                  </>
                )}
                <div className="v5-chip-row">
                  {(selected.tags || []).map((tag) => (
                    <Tag key={tag}>{tag}</Tag>
                  ))}
                </div>
              </div>
              <div className="v5-style-detail-right">
                <span className="v5-detail-label">
                  代表场景{scene ? ` · ${scene}` : "（未找到）"}
                </span>
                {scene ? (
                  <SmartImage
                    className="v5-style-preview"
                    sources={[...previewSrc, mediaSrc(`/media/v5/scenes/${scene}.png`)]}
                    alt={selected.label}
                    fallback={<span className="v5-muted">场景预览不可用</span>}
                  />
                ) : (
                  <div className="v5-img-fallback">该风格暂无代表场景</div>
                )}
              </div>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
