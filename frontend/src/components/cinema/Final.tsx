// Cinema Studio · 成片 & QC
// 播放 final.mp4，展示 final_report.json 的 QC 硬门、抽帧证据与联系表，支持下载 / 跳到项目成片页 / 手动刷新。
import { useCallback, useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import { errorText } from "../../lib/api";
import {
  cinemaApi,
  fmtDuration,
  fmtSize,
  fmtTimecode,
  shotMediaUrl,
  type CinemaReport,
} from "../../lib/cinema";
import { useToast } from "../Toast";
import {
  IconAlert,
  IconCheck,
  IconClose,
  IconDownload,
  IconExternal,
  IconFilm,
  IconImage,
  IconRefresh,
  IconVideo,
} from "../icons";
import "../../styles/cinema-final.css";

interface RawFrameItem {
  index?: number;
  time?: number;
  shot?: string;
  path?: string;
}

interface RawReport {
  generated_at?: string;
  args?: {
    width?: number;
    height?: number;
    fps?: number;
    ambience_db?: number;
    unify_look?: boolean;
    shot_grade?: boolean;
    episode_total_duration?: number;
  };
  media?: {
    duration?: number;
    width?: number;
    height?: number;
    fps?: number;
    codec?: string;
    has_audio?: boolean;
    audio_codec?: string;
    size_bytes?: number;
  };
  blackdetect?: { ok?: boolean; count?: number; min_duration?: number; pix_th?: number };
  loudness?: { integrated_lufs?: number; true_peak_dbtp?: number; lra?: number };
  subtitles?: {
    narration_events?: number;
    narration_lines?: number;
    coverage_ratio?: number;
    total_events?: number;
    title_events?: number;
  };
  frames?: { count?: number; items?: RawFrameItem[] };
  timeline?: { shots_included?: number; shots_total?: number };
  warnings?: string[];
}

interface Preview {
  src: string;
  caption: string;
}

interface QcMetric {
  key: string;
  label: string;
  en: string;
  value: string;
  unit?: string;
  sub: string;
  ok: boolean | null;
}

function num(value: unknown): number | null {
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (typeof value === "string" && value.trim() !== "") {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function asRecord(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== "object") return null;
  return value as Record<string, unknown>;
}

function pad2(value: number): string {
  return String(value).padStart(2, "0");
}

function parseTime(value: string | null | undefined): number {
  if (!value) return Number.NaN;
  const ts = Date.parse(value);
  return Number.isFinite(ts) ? ts : Number.NaN;
}

function fmtMtime(value: string | null | undefined): string {
  if (!value) return "—";
  const ts = parseTime(value);
  if (!Number.isFinite(ts)) return String(value).replace("T", " ").slice(0, 16);
  const date = new Date(ts);
  return `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())} ${pad2(date.getHours())}:${pad2(date.getMinutes())}`;
}

function fmtLufs(value: number | null): string {
  return value === null ? "—" : value.toFixed(2);
}

export function CinemaFinal() {
  const notify = useToast();

  const [report, setReport] = useState<CinemaReport | null>(null);
  const [reportError, setReportError] = useState("");
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [episodeId, setEpisodeId] = useState<string | null>(null);
  const [preview, setPreview] = useState<Preview | null>(null);

  const loadReport = useCallback(async () => {
    setLoading(true);
    try {
      const res = await cinemaApi.report();
      setReport(res);
      setReportError("");
    } catch (err) {
      setReportError(errorText(err));
    } finally {
      setLoading(false);
    }
  }, []);

  const loadOverview = useCallback(async () => {
    try {
      const overview = await cinemaApi.overview();
      const id = overview.project?.episode_id;
      setEpisodeId(typeof id === "string" && id ? id : null);
    } catch {
      /* 项目信息不可用时不阻塞成片页：播放 / QC / 下载仍可用 */
    }
  }, []);

  useEffect(() => {
    void loadReport();
    void loadOverview();
  }, [loadReport, loadOverview]);

  useEffect(() => {
    if (!preview) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setPreview(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [preview]);

  const refreshAll = useCallback(async () => {
    setRefreshing(true);
    await Promise.all([loadReport(), loadOverview()]);
    setRefreshing(false);
    notify("成片报告已刷新", "info");
  }, [loadOverview, loadReport, notify]);

  const qc = report?.qc ?? null;
  const raw = useMemo<RawReport | null>(() => {
    const value = report?.report;
    if (!value || typeof value !== "object") return null;
    return value as unknown as RawReport;
  }, [report]);

  const final = report?.final ?? null;
  const media = raw?.media ?? null;
  const args = raw?.args ?? null;
  const loudness = qc?.loudness ?? raw?.loudness ?? null;
  const rawSubs = raw?.subtitles ?? null;

  const duration = num(qc?.duration) ?? num(media?.duration) ?? num(final?.duration);
  const durationError = num(qc?.duration_error);
  const width = num(qc?.width) ?? num(media?.width);
  const height = num(qc?.height) ?? num(media?.height);
  const fps = num(qc?.fps) ?? num(media?.fps);
  const expectedWidth = num(args?.width) ?? 1280;
  const expectedHeight = num(args?.height) ?? 720;
  const expectedFps = num(args?.fps) ?? 25;

  const blackQc: unknown = qc?.blackdetect;
  let blackCount = typeof blackQc === "number" ? blackQc : num(asRecord(blackQc)?.count);
  if (blackCount === null) blackCount = num(raw?.blackdetect?.count);

  const integratedLufs = num(loudness?.integrated_lufs);
  const truePeak = num(loudness?.true_peak_dbtp);
  const lra = num(loudness?.lra);

  const subtitleEvents = num(qc?.subtitles) ?? num(rawSubs?.total_events);
  const narrationEvents = num(rawSubs?.narration_events);
  const narrationLines = num(rawSubs?.narration_lines);
  const coverage = num(rawSubs?.coverage_ratio);

  const frames = report?.frames;
  const frameUrls = useMemo(
    () =>
      Array.isArray(frames)
        ? frames.filter((item): item is string => typeof item === "string" && item.trim().length > 0)
        : [],
    [frames]
  );
  const sheetUrl = report?.contact_sheet_url ? shotMediaUrl(report.contact_sheet_url) : "";
  const videoSrc = final?.url ? shotMediaUrl(final.url) : "";

  const frameCards = useMemo(() => {
    const items = raw?.frames?.items;
    const meta = Array.isArray(items) && items.length === frameUrls.length ? items : null;
    return frameUrls.map((url, index) => {
      const item = meta?.[index];
      const shot = item?.shot ? String(item.shot) : "";
      const at = num(item?.time);
      const caption = [`抽帧 ${pad2(index + 1)}`, shot, at !== null ? fmtTimecode(at) : ""]
        .filter(Boolean)
        .join(" · ");
      return { url, src: shotMediaUrl(url), caption };
    });
  }, [frameUrls, raw]);

  const metrics: QcMetric[] = useMemo(() => {
    const durationOk =
      duration !== null && (durationError === null || Math.abs(durationError) <= 0.2);
    const resolutionOk = width !== null && height !== null && width === expectedWidth && height === expectedHeight;
    const fpsOk = fps !== null && Math.abs(fps - expectedFps) < 0.01;
    const blackOk = blackCount === 0;
    const loudnessOk =
      integratedLufs !== null &&
      integratedLufs >= -18 &&
      integratedLufs <= -14 &&
      (truePeak === null || truePeak <= -1);
    const subtitlesOk =
      coverage !== null
        ? coverage >= 0.999
        : narrationEvents !== null && narrationLines !== null
          ? narrationEvents >= narrationLines
          : false;

    const durationSub =
      duration !== null
        ? `${duration.toFixed(2)}s · 时间线偏差 ${
            durationError !== null
              ? (durationError > 0 ? "+" : "") + durationError.toFixed(2) + "s"
              : "—"
          }`
        : "报告未提供时长";

    const resolutionSub = media?.codec
      ? `${String(media.codec).toUpperCase()} · ${media.has_audio ? "AAC 立体声" : "无音轨"} · 规格 ${expectedWidth}×${expectedHeight}`
      : `规格 ${expectedWidth}×${expectedHeight} / 16:9`;

    const blackThreshold = raw?.blackdetect;
    const thresholdText =
      blackThreshold && num(blackThreshold.min_duration) !== null
        ? `阈值 ${num(blackThreshold.min_duration)}s`
        : "阈值 0.1s";

    const subtitleSub =
      narrationEvents !== null && narrationLines !== null
        ? `旁白 ${narrationEvents}/${narrationLines} 句 · 覆盖 ${
            coverage !== null ? Math.round(coverage * 100) + "%" : "—"
          }`
        : subtitleEvents !== null
          ? "报告未提供旁白覆盖率"
          : "报告未提供字幕事件";

    return [
      {
        key: "duration",
        label: "时长",
        en: "DURATION",
        value: duration !== null ? fmtDuration(duration) : "—",
        sub: durationSub,
        ok: durationOk,
      },
      {
        key: "resolution",
        label: "分辨率",
        en: "RESOLUTION",
        value: width !== null && height !== null ? `${width}×${height}` : "—",
        sub: resolutionSub,
        ok: resolutionOk,
      },
      {
        key: "fps",
        label: "帧率",
        en: "FRAME RATE",
        value: fps !== null ? String(Math.round(fps * 100) / 100) : "—",
        unit: "fps",
        sub: `交付规格 ${expectedFps} fps · 逐帧对齐 _edit_start`,
        ok: fpsOk,
      },
      {
        key: "black",
        label: "黑帧",
        en: "BLACK FRAMES",
        value: blackCount !== null ? `${blackCount} 段` : "—",
        sub:
          blackCount === 0
            ? `blackdetect 通过 · ${thresholdText} · 片尾淡出不触发`
            : blackCount !== null
              ? "检测到全黑段，回看片尾淡出 / 空镜"
              : "报告未提供黑帧检测",
        ok: blackOk,
      },
      {
        key: "loudness",
        label: "响度",
        en: "LOUDNESS",
        value: fmtLufs(integratedLufs),
        unit: "LUFS",
        sub: `true peak ${truePeak !== null ? truePeak.toFixed(2) + " dBTP" : "—"} · LRA ${
          lra !== null ? lra.toFixed(1) : "—"
        } · 目标 -16 LUFS`,
        ok: loudnessOk,
      },
      {
        key: "subtitles",
        label: "字幕覆盖",
        en: "SUBTITLES",
        value: subtitleEvents !== null ? String(subtitleEvents) : "—",
        unit: "事件",
        sub: subtitleSub,
        ok: subtitlesOk,
      },
    ];
  }, [
    blackCount,
    coverage,
    duration,
    durationError,
    expectedFps,
    expectedHeight,
    expectedWidth,
    fps,
    height,
    integratedLufs,
    lra,
    media,
    narrationEvents,
    narrationLines,
    raw,
    subtitleEvents,
    truePeak,
    width,
  ]);

  const okCount = metrics.filter((metric) => metric.ok === true).length;
  const hasQc = qc !== null || raw !== null;
  const hasContent = Boolean(final || hasQc);
  const generatedAt = raw?.generated_at ? String(raw.generated_at).replace("T", " ").slice(0, 19) : "";

  return (
    <div className="cm-page cm-final">
      <header className="cm-page-head cm-final-head">
        <div className="cm-head-main">
          <div className="cm-kicker">DELIVERY</div>
          <h1 className="cm-h1">成片 &amp; QC</h1>
          <p className="cm-sub">
            成片母版播放 · QC 硬门核对 · 抽帧证据与交付下载
            {hasContent ? ` · ${okCount}/${metrics.length} 项通过` : ""}
          </p>
        </div>
      </header>

      {loading && !report && <div className="cm-loading cm-final-loading">正在读取成片报告…</div>}

      {reportError && (
        <div className="cm-error cm-final-error" role="alert">
          <IconAlert size={16} />
          <span>无法读取成片报告：{reportError}</span>
          <button type="button" className="cm-btn cm-btn-ghost" onClick={() => void refreshAll()} disabled={refreshing}>
            <IconRefresh size={14} className={refreshing ? "is-spin" : undefined} />
            重试
          </button>
        </div>
      )}

      {!loading && !reportError && report && !hasContent && (
        <div className="cm-empty cm-final-empty">
          <span className="cm-final-empty-icon" aria-hidden="true">
            <IconFilm size={30} />
          </span>
          <div className="cm-final-empty-title">尚未生成成片</div>
          <p className="cm-final-empty-hint">
            完成一次剪辑台合成后，这里会播放最新成片并展示 QC 指标与抽帧证据。
          </p>
          <a className="cm-btn" href="#/cinema/assemble">
            去剪辑台合成
          </a>
        </div>
      )}

      {hasContent && report && (
        <>
          <section className="cm-card cm-card-pad cm-final-screen">
            <div className="cm-final-screen-head">
              <div className="cm-final-screen-title">
                <IconFilm size={16} />
                成片母版
                <span className="cm-final-screen-en">FINAL MASTER</span>
              </div>
              <div className="cm-final-screen-chips">
                {duration !== null && (
                  <span className="cm-chip is-mono">{fmtDuration(duration)}</span>
                )}
                {width !== null && height !== null && (
                  <span className="cm-chip is-mono">
                    {width}×{height}
                  </span>
                )}
                {fps !== null && <span className="cm-chip is-mono">{fps} fps</span>}
                {final?.size ? <span className="cm-chip is-mono">{fmtSize(final.size)}</span> : null}
                {final?.mtime && (
                  <span className="cm-chip is-mono cm-final-screen-mtime">{fmtMtime(final.mtime)}</span>
                )}
              </div>
            </div>

            <div className={"cm-video-frame cm-final-video-frame" + (videoSrc ? "" : " is-empty")}>
              {videoSrc ? (
                <video
                  controls
                  preload="metadata"
                  src={videoSrc}
                  poster={frameCards.length > 0 ? frameCards[0].src : undefined}
                >
                  你的浏览器不支持 HTML5 视频播放。
                </video>
              ) : (
                <div className="cm-final-video-empty">
                  <IconVideo size={30} />
                  <div className="cm-final-video-empty-title">尚未生成成片文件</div>
                  <p>执行一次剪辑台合成后，这里会播放最新母版。</p>
                </div>
              )}
            </div>
          </section>

          <div className="cm-section-head">
            <h2 className="cm-section-title">QC 硬门</h2>
            <div className="cm-head-right">
              <span className="cm-final-section-note">
                {okCount}/{metrics.length} 项通过 · 数据来自 final_report.json
              </span>
            </div>
          </div>

          <div className="cm-final-qc-grid">
            {metrics.map((metric) => {
              const tone =
                metric.ok === true ? "is-ok" : metric.ok === false ? "is-warn" : "is-unknown";
              return (
                <div key={metric.key} className={"cm-card cm-card-pad cm-final-qc " + tone}>
                  <div className="cm-final-qc-top">
                    <span className="cm-final-qc-label">
                      {metric.label}
                      <small>{metric.en}</small>
                    </span>
                    <span className="cm-final-qc-icon" aria-hidden="true">
                      {metric.ok === true ? <IconCheck size={13} /> : <IconAlert size={13} />}
                    </span>
                  </div>
                  <div className="cm-final-qc-value cm-mono">
                    {metric.value}
                    {metric.unit && <em>{metric.unit}</em>}
                  </div>
                  <div className="cm-final-qc-sub">{metric.sub}</div>
                </div>
              );
            })}
          </div>

          <div className="cm-section-head">
            <h2 className="cm-section-title">抽帧证据</h2>
            <div className="cm-head-right">
              <span className="cm-final-section-note">
                {frameCards.length > 0 ? `${frameCards.length} 帧抽帧` : "暂无抽帧"}
                {sheetUrl ? " · 联系表 4×2" : ""} · 点击图片放大
              </span>
            </div>
          </div>

          {frameCards.length === 0 && !sheetUrl ? (
            <div className="cm-empty cm-final-frames-empty">
              <span className="cm-final-empty-icon" aria-hidden="true">
                <IconImage size={26} />
              </span>
              <div className="cm-final-empty-title">尚未生成抽帧</div>
              <p className="cm-final-empty-hint">合成完成后会自动抽取 8 帧并生成联系表。</p>
            </div>
          ) : (
            <div className="cm-final-evidence">
              {frameCards.length > 0 && (
                <div className="cm-final-frames">
                  {frameCards.map((card) => (
                    <button
                      key={card.url}
                      type="button"
                      className="cm-final-frame"
                      onClick={() => setPreview({ src: card.src, caption: card.caption })}
                      title={card.caption}
                    >
                      <img src={card.src} alt={card.caption} loading="lazy" />
                      <span className="cm-final-frame-cap cm-mono">{card.caption}</span>
                    </button>
                  ))}
                </div>
              )}
              {sheetUrl && (
                <button
                  type="button"
                  className="cm-final-sheet"
                  onClick={() => setPreview({ src: sheetUrl, caption: "联系表 · CONTACT SHEET" })}
                  title="联系表 · 点击放大"
                >
                  <img src={sheetUrl} alt="成片联系表" loading="lazy" />
                  <span className="cm-final-sheet-cap cm-mono">CONTACT SHEET · 点击放大</span>
                </button>
              )}
            </div>
          )}

          <div className="cm-final-actions">
            <div className="cm-final-actions-main">
              {videoSrc ? (
                <a className="cm-btn cm-btn-primary cm-final-download" href={videoSrc} download="relic_talk_3d_final.mp4">
                  <IconDownload size={15} />
                  下载成片
                </a>
              ) : (
                <span className="cm-btn cm-final-disabled" aria-disabled="true">
                  <IconDownload size={15} />
                  下载成片
                </span>
              )}

              {episodeId ? (
                <a
                  className="cm-btn cm-btn-ghost"
                  href={"#/episodes/" + encodeURIComponent(episodeId) + "/finish"}
                >
                  <IconExternal size={15} />
                  打开项目成片页
                </a>
              ) : (
                <span
                  className="cm-btn cm-final-disabled"
                  aria-disabled="true"
                  title="暂时无法获取项目信息"
                >
                  <IconExternal size={15} />
                  打开项目成片页
                </span>
              )}

              <button
                type="button"
                className="cm-btn cm-btn-ghost"
                onClick={() => void refreshAll()}
                disabled={refreshing}
              >
                <IconRefresh size={15} className={refreshing ? "is-spin" : undefined} />
                刷新报告
              </button>
            </div>

            <div className="cm-final-actions-meta cm-mono">
              {generatedAt ? `报告 ${generatedAt}` : reportError ? "报告不可用" : "—"}
            </div>
          </div>
        </>
      )}

      {preview &&
        createPortal(
          <div
            className="cm-final-lightbox"
            role="dialog"
            aria-modal="true"
            aria-label={preview.caption || "抽帧预览"}
            onClick={() => setPreview(null)}
          >
            <div className="cm-final-lightbox-inner" onClick={(event) => event.stopPropagation()}>
              <button
                type="button"
                className="cm-btn cm-btn-ghost cm-final-lightbox-close"
                onClick={() => setPreview(null)}
                aria-label="关闭预览"
              >
                <IconClose size={16} />
              </button>
              <img src={preview.src} alt={preview.caption || "抽帧预览"} />
              {preview.caption && <div className="cm-final-lightbox-cap cm-mono">{preview.caption}</div>}
            </div>
          </div>,
          document.body
        )}
    </div>
  );
}
