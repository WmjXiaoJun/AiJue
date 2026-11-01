// V5 成片画廊：16 模板卡（封面/风格/时长/QC/播放/时间线/QC 报告）
// V8 起「优先 3D 版」：同时读取 /api/cinema/templates，有 3D 成片的模板自动替换海报/播放/时长，
// 加金色 3D 徽章与「3D 电影版」标识，并保留「切换 2D 原版」入口（不丢旧数据）。
// /api/v5/outputs 契约不变；模板库接口失败时静默回退到旧 2D 数据。
import { useCallback, useEffect, useMemo, useState } from "react";
import type { V5Output } from "../../lib/v5";
import { mediaSrc } from "../../lib/v5";
import type { CinemaTemplate, CinemaTemplatesSummary } from "../../lib/cinema";
import { cinemaApi } from "../../lib/cinema";
import { IconClose, IconRefresh, IconSpark, IconVideo } from "../icons";
import { EmptyState, Spinner } from "../ui";
import { PlayOverlay, QcBadge, SmartImage, StyleBadge, VideoModal } from "./shared";
import "../../styles/cinema-templates.css";

interface Props {
  outputs: V5Output[];
  loading: boolean;
  includeCustom: boolean;
  expected: number;
  onToggleCustom: (value: boolean) => void;
  onRefresh: () => void;
}

function is3dReady(template: CinemaTemplate | null | undefined): boolean {
  return Boolean(template && template.final && template.final.exists && template.final.url);
}

interface Playing3D {
  output: V5Output;
  template: CinemaTemplate;
}

export function Gallery({ outputs, loading, includeCustom, expected, onToggleCustom, onRefresh }: Props) {
  const [playing, setPlaying] = useState<V5Output | null>(null);
  const [playing3d, setPlaying3d] = useState<Playing3D | null>(null);
  const [threeD, setThreeD] = useState<Record<string, CinemaTemplate>>({});
  const [tplSummary, setTplSummary] = useState<CinemaTemplatesSummary | null>(null);
  const [tplError, setTplError] = useState("");
  const [tplLoading, setTplLoading] = useState(true);
  /** 用户选择「看 2D 原版」的模板 id */
  const [force2d, setForce2d] = useState<Set<string>>(() => new Set());

  const loadTemplates = useCallback(async () => {
    try {
      const res = await cinemaApi.templates();
      const map: Record<string, CinemaTemplate> = {};
      for (const template of res.templates) map[template.id] = template;
      setThreeD(map);
      setTplSummary(res.summary);
      setTplError("");
    } catch (err) {
      // 静默回退：模板库接口未就绪时画廊保持旧 2D 行为
      setTplError(err instanceof Error ? err.message : "模板库接口不可用");
    } finally {
      setTplLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadTemplates();
  }, [loadTemplates]);

  const openExternal = useCallback((url: string | null) => {
    if (!url) return;
    window.open(mediaSrc(url), "_blank", "noopener,noreferrer");
  }, []);

  const handleRefresh = useCallback(() => {
    onRefresh();
    void loadTemplates();
  }, [loadTemplates, onRefresh]);

  const toggle2d = useCallback((id: string) => {
    setForce2d((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const progressText = useMemo(() => {
    if (tplLoading) return "读取 3D 进度…";
    if (tplError) return "3D 进度接口暂不可用";
    if (!tplSummary) return "16 模板";
    return `已完成 ${tplSummary.done}/${tplSummary.total || 16} · 镜头 ${tplSummary.clips_done}/${
      tplSummary.clips_total || "—"
    }`;
  }, [tplError, tplLoading, tplSummary]);

  const threeDCount = useMemo(() => Object.values(threeD).filter((template) => is3dReady(template)).length, [threeD]);

  return (
    <section className="v5-section">
      <div className="v5-toolbar">
        <div className="v5-toolbar-main">
          <IconVideo size={16} />
          <b>成片画廊</b>
          <span className="v5-muted">
            已生成 {outputs.length} / {expected} 部 · 点击封面播放成片
            {threeDCount > 0 ? ` · ${threeDCount} 部已有 3D 电影版` : ""}
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
          <button type="button" className="btn btn-ghost" onClick={handleRefresh} disabled={loading}>
            {loading ? <Spinner size={14} /> : <IconRefresh size={14} />} 刷新
          </button>
        </div>
      </div>

      <a className="v5-3d-entry" href="#/cinema/templates" title="打开 Cinema Studio · 模板库 · 3D">
        <span className="v5-3d-entry-dot" />
        <span>16 模板 3D 制作进度</span>
        <small>{progressText}</small>
        <b>查看 →</b>
      </a>

      {!loading && outputs.length === 0 && (
        <EmptyState
          icon={<IconVideo size={26} />}
          title="还没有 V5 成片"
          hint="先到「渲染」页签提交一次 render_v5；产物写在 outputs/templates_v5/<template>/"
        />
      )}

      <div className="v5-gallery-grid">
        {outputs.map((output) => {
          const template = threeD[output.id];
          const ready3d = is3dReady(template);
          const active3d = ready3d && !force2d.has(output.id) && template ? template : null;
          const use3d = active3d !== null;
          const playingUrl = active3d ? active3d.final.url : output.final_url;
          const durationText = active3d
            ? active3d.final.duration > 0
              ? `${Math.round(active3d.final.duration)}s`
              : output.duration
                ? `${output.duration}s`
                : "—"
            : output.duration
              ? `${output.duration}s`
              : "—";
          const posterSources = active3d
            ? [active3d.poster_url, output.poster_url]
            : [output.poster_url];
          const cardClass = "v5-film-card" + (use3d ? " is-3d" : "");
          const openPlayer = () => {
            if (active3d) setPlaying3d({ output, template: active3d });
            else setPlaying(output);
          };

          return (
            <article key={output.id} className={cardClass}>
              <button
                type="button"
                className="v5-film-cover"
                onClick={openPlayer}
                title={use3d ? "播放 3D 电影版" : output.final_url ? "播放成片" : "暂无 final.mp4"}
              >
                <SmartImage
                  className="v5-film-poster"
                  sources={posterSources}
                  alt={output.label}
                  fallback={<span className="v5-film-placeholder">{output.label.slice(0, 4)}</span>}
                />
                {playingUrl && <PlayOverlay />}
                {use3d && <span className="v5-3d-badge">3D</span>}
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
                  <span>{durationText}</span>
                  <span>{output.resolution || "—"}</span>
                  <span>{output.fps ? `${output.fps}fps` : "—"}</span>
                  <span>{output.shots} 镜</span>
                </div>
                <div className="v5-film-row">
                  <QcBadge ok={output.qc_ok} checks={output.qc_checks} total={output.qc_total} />
                  {use3d ? (
                    <span className="v5-3d-chip">
                      <i />
                      3D 电影版
                    </span>
                  ) : ready3d ? (
                    <span className="v5-3d-chip is-progress">2D 原版</span>
                  ) : template && template.status === "generating" ? (
                    <span className="v5-3d-chip is-progress">
                      <i />
                      3D 制作中 {template.clips.done}/{template.clips.total || "—"}
                    </span>
                  ) : null}
                  <span className="v5-muted v5-ellipsis" title={output.scenes.join(" / ")}>
                    {output.scenes.slice(0, 3).join(" · ") || "—"}
                  </span>
                </div>
                <div className="v5-film-actions">
                  <button
                    type="button"
                    className="btn btn-primary btn-sm"
                    disabled={!playingUrl}
                    onClick={openPlayer}
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
                {ready3d && (
                  <div className="v5-film-row">
                    <button type="button" className="v5-3d-toggle" onClick={() => toggle2d(output.id)}>
                      {use3d ? "切换 2D 原版" : "切换 3D 电影版"}
                    </button>
                    <span className="v5-muted v5-ellipsis">
                      {use3d ? "3D 版：本地 H3 重制 1280×720" : "2D 原版：V5 程序化渲染"}
                    </span>
                  </div>
                )}
              </div>
            </article>
          );
        })}
      </div>

      <VideoModal
        output={playing}
        onClose={() => setPlaying(null)}
        onOpenTimeline={(output) => openExternal(output.timeline_url)}
        onOpenQc={(output) => openExternal(output.qc_url)}
      />

      {playing3d && (
        <Video3DModal
          item={playing3d}
          onClose={() => setPlaying3d(null)}
          onSwitch2d={() => {
            toggle2d(playing3d.output.id);
            setPlaying3d(null);
            setPlaying(playing3d.output);
          }}
        />
      )}
    </section>
  );
}

/** 3D 成片播放弹层（复用 v5.css 的 overlay / video-modal 视觉；ESC & 遮罩关闭） */
function Video3DModal({
  item,
  onClose,
  onSwitch2d,
}: {
  item: Playing3D;
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

  const { output, template } = item;
  return (
    <div className="v5-overlay" onMouseDown={onClose}>
      <div className="v5-video-modal" onMouseDown={(event) => event.stopPropagation()}>
        <div className="v5-video-head">
          <div>
            <b>{output.label}</b>
            <span className="v5-3d-modal-sub">
              3D 电影版 · {template.style || output.style_label} ·{" "}
              {template.final.duration > 0 ? `${Math.round(template.final.duration)}s` : "—"} ·{" "}
              {template.clips.done}/{template.clips.total || "—"} 镜
            </span>
          </div>
          <button type="button" className="icon-btn" onClick={onClose} aria-label="关闭">
            <IconClose size={16} />
          </button>
        </div>
        <video
          className="v5-video"
          controls
          autoPlay
          playsInline
          preload="metadata"
          src={mediaSrc(template.final.url)}
        />
        <div className="v5-video-actions">
          <span className="v5-muted">
            本地 ComfyUI H3 重制 · 1280×720 / 25fps · 逐镜 3D
          </span>
          <div className="v5-spacer" />
          <button type="button" className="btn btn-ghost" onClick={onSwitch2d}>
            切换 2D 原版
          </button>
          <a className="btn btn-ghost" href={mediaSrc(template.final.url)} target="_blank" rel="noreferrer">
            打开原片
          </a>
        </div>
      </div>
    </div>
  );
}
