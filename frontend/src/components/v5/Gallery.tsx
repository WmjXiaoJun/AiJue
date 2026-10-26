// V5 成片画廊：16 模板卡（封面/风格/时长/QC/播放/时间线/QC 报告）
import { useCallback, useState } from "react";
import type { V5Output } from "../../lib/v5";
import { mediaSrc } from "../../lib/v5";
import { IconRefresh, IconSpark, IconVideo } from "../icons";
import { EmptyState, Spinner } from "../ui";
import { PlayOverlay, QcBadge, SmartImage, StyleBadge, VideoModal } from "./shared";

interface Props {
  outputs: V5Output[];
  loading: boolean;
  includeCustom: boolean;
  expected: number;
  onToggleCustom: (value: boolean) => void;
  onRefresh: () => void;
}

export function Gallery({ outputs, loading, includeCustom, expected, onToggleCustom, onRefresh }: Props) {
  const [playing, setPlaying] = useState<V5Output | null>(null);

  const openExternal = useCallback((url: string | null) => {
    if (!url) return;
    window.open(mediaSrc(url), "_blank", "noopener,noreferrer");
  }, []);

  return (
    <section className="v5-section">
      <div className="v5-toolbar">
        <div className="v5-toolbar-main">
          <IconVideo size={16} />
          <b>成片画廊</b>
          <span className="v5-muted">
            已生成 {outputs.length} / {expected} 部 · 点击封面播放成片
          </span>
        </div>
        <div className="v5-toolbar-right">
          <label className="v5-check">
            <input
              type="checkbox"
              checked={includeCustom}
              onChange={(event) => onToggleCustom(event.target.checked)}
            />
            含自定义示例
          </label>
          <button type="button" className="btn btn-ghost" onClick={onRefresh} disabled={loading}>
            {loading ? <Spinner size={14} /> : <IconRefresh size={14} />} 刷新
          </button>
        </div>
      </div>

      {!loading && outputs.length === 0 && (
        <EmptyState
          icon={<IconVideo size={26} />}
          title="还没有 V5 成片"
          hint="先到「渲染」页签提交一次 render_v5；产物写在 outputs/templates_v5/<template>/"
        />
      )}

      <div className="v5-gallery-grid">
        {outputs.map((output) => (
          <article key={output.id} className="v5-film-card">
            <button
              type="button"
              className="v5-film-cover"
              onClick={() => setPlaying(output)}
              title={output.final_url ? "播放成片" : "暂无 final.mp4"}
            >
              <SmartImage
                className="v5-film-poster"
                sources={[output.poster_url]}
                alt={output.label}
                fallback={<span className="v5-film-placeholder">{output.label.slice(0, 4)}</span>}
              />
              {output.final_url && <PlayOverlay />}
              <span className="v5-film-style">
                <StyleBadge label={output.style_label || output.style} />
              </span>
            </button>
            <div className="v5-film-body">
              <div className="v5-film-title">
                <b>{output.label}</b>
                <span className="v5-muted">{output.id}</span>
              </div>
              <div className="v5-film-meta">
                <span>{output.duration ? `${output.duration}s` : "—"}</span>
                <span>{output.resolution || "—"}</span>
                <span>{output.fps ? `${output.fps}fps` : "—"}</span>
                <span>{output.shots} 镜</span>
              </div>
              <div className="v5-film-row">
                <QcBadge ok={output.qc_ok} checks={output.qc_checks} total={output.qc_total} />
                <span className="v5-muted v5-ellipsis" title={output.scenes.join(" / ")}>
                  {output.scenes.slice(0, 3).join(" · ") || "—"}
                </span>
              </div>
              <div className="v5-film-actions">
                <button
                  type="button"
                  className="btn btn-primary btn-sm"
                  disabled={!output.final_url}
                  onClick={() => setPlaying(output)}
                >
                  <IconSpark size={13} /> 播放
                </button>
                <button
                  type="button"
                  className="btn btn-ghost btn-sm"
                  disabled={!output.timeline_url}
                  onClick={() => openExternal(output.timeline_url)}
                >
                  时间线
                </button>
                <button
                  type="button"
                  className="btn btn-ghost btn-sm"
                  disabled={!output.qc_url}
                  onClick={() => openExternal(output.qc_url)}
                >
                  QC
                </button>
              </div>
            </div>
          </article>
        ))}
      </div>

      <VideoModal
        output={playing}
        onClose={() => setPlaying(null)}
        onOpenTimeline={(output) => openExternal(output.timeline_url)}
        onOpenQc={(output) => openExternal(output.qc_url)}
      />
    </section>
  );
}
