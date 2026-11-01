// V11 Project Studio · 06 3D 模板（3D TEMPLATE）
// 项目节目模板 ↔ 16 模板 3D 样片打通，全部真实 API：
// - GET  /api/cinema/templates                16 模板总表 + summary + jobs
// - GET  /api/cinema/templates/{tid}          当前模板详情（逐镜 / 成片 / QC）
// - GET  /media/templates_3d/{tid}/report.json 成片 probe（分辨率 / 帧率 / 转场 / BGM）
// - POST /api/cinema/templates/generate       重新生成该模板 3D（本地 GPU）
// - PATCH /api/projects/{pid} {format_id}     切换项目模板（后端由 tpl-backend 并行开发，404/405 优雅提示）
// 设计与共享元件见 styles/studio.css；本页专属样式在 styles/studio-3d.css（st-tpl3d-*）。
import { useCallback, useEffect, useMemo, useState } from "react";
import type { Project } from "../../types";
import type { StudioTabProps } from "../../lib/studio";
import { TPL3D_FORMAT_SUFFIX, TPL3D_LIBRARY_HREF, errorText, fmtClock, fmtDateTime, mediaUrl } from "../../lib/studio";
import { API_BASE, ApiError } from "../../lib/api";
import {
  TEMPLATE_3D_SEEDS,
  TEMPLATE_STATUS_LABEL,
  cinemaApi,
  fmtSize,
  templateClipPercent,
  templateStatusChip,
  type CinemaTemplate,
  type CinemaTemplateDetail,
  type CinemaTemplatesSummary,
} from "../../lib/cinema";
import { useToast } from "../Toast";
import { Spinner } from "../ui";
import { IconAlert, IconClose, IconExternal, IconFilm, IconPlay, IconRefresh, IconSpark } from "../icons";
import "../../styles/studio-3d.css";

const FORMAT_SUFFIX = TPL3D_FORMAT_SUFFIX;
const LIBRARY_HREF = TPL3D_LIBRARY_HREF;

/** 3D 模板 id → 项目 format_id（roundtable → roundtable_5min） */
function formatIdForTemplate(tid: string): string {
  return tid + FORMAT_SUFFIX;
}

/** 项目 format_id → 3D 模板 id；未匹配（如 relic_talk_5min）返回 null */
function templateIdFromFormat(formatId: string | null | undefined, known: Set<string>): string | null {
  const raw = String(formatId || "").trim();
  if (!raw) return null;
  const candidates: string[] = [raw];
  if (raw.endsWith(FORMAT_SUFFIX)) candidates.push(raw.slice(0, -FORMAT_SUFFIX.length));
  for (const candidate of candidates) {
    if (known.has(candidate)) return candidate;
  }
  return null;
}

function statusTagClass(status: CinemaTemplate["status"]): string {
  return templateStatusChip(status) || "is-slate";
}

function statusText(status: CinemaTemplate["status"]): string {
  return TEMPLATE_STATUS_LABEL[status] || status;
}

/** report.json probe 解析（成片分辨率 / 帧率 / 转场 / BGM） */
interface TemplateProbe {
  width: number;
  height: number;
  fps: number;
  duration: number;
  size: number;
  bgm: boolean;
  transitions: number;
}

function parseReportProbe(raw: unknown): TemplateProbe | null {
  if (!raw || typeof raw !== "object") return null;
  const rec = raw as Record<string, unknown>;
  const probe = rec.probe && typeof rec.probe === "object" ? (rec.probe as Record<string, unknown>) : {};
  const streams = Array.isArray(probe.streams) ? probe.streams : [];
  let video: Record<string, unknown> | null = null;
  for (const item of streams) {
    if (item && typeof item === "object" && (item as Record<string, unknown>).codec_type === "video") {
      video = item as Record<string, unknown>;
      break;
    }
  }
  const format = probe.format && typeof probe.format === "object" ? (probe.format as Record<string, unknown>) : {};
  const assemble = rec.assemble && typeof rec.assemble === "object" ? (rec.assemble as Record<string, unknown>) : {};
  const num = (value: unknown): number => {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : 0;
  };
  const fpsRaw = String(video?.r_frame_rate || "");
  const [fpsNum, fpsDen] = fpsRaw.split("/").map((part) => Number(part));
  const fps = fpsDen ? fpsNum / fpsDen : fpsNum || 0;
  const width = num(video?.width);
  const height = num(video?.height);
  const duration = num(format.duration);
  if (!width && !height && !duration) return null;
  return {
    width,
    height,
    fps: Number.isFinite(fps) ? Math.round(fps * 100) / 100 : 0,
    duration,
    size: num(format.size),
    bgm: Boolean(assemble.bgm),
    transitions: Array.isArray(assemble.boundaries) ? assemble.boundaries.length : 0,
  };
}

/** PATCH /api/projects/{pid}（后端未上线时抛 ApiError 404/405，页面优雅提示） */
async function patchProjectFormat(pid: string, formatId: string): Promise<Project> {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), 20000);
  try {
    const res = await fetch(API_BASE + "/projects/" + encodeURIComponent(pid), {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ format_id: formatId }),
      signal: controller.signal,
    });
    if (!res.ok) {
      let detail = res.statusText || "请求失败";
      try {
        const data = (await res.json()) as { detail?: unknown };
        if (typeof data?.detail === "string") detail = data.detail;
        else if (data?.detail) detail = JSON.stringify(data.detail);
      } catch {
        /* 非 JSON 错误体，保留状态文本 */
      }
      throw new ApiError(res.status, detail);
    }
    return (await res.json()) as Project;
  } catch (err) {
    if (err instanceof ApiError) throw err;
    const message = err instanceof DOMException && err.name === "AbortError" ? "请求超时" : "无法连接后端";
    throw new ApiError(0, message);
  } finally {
    window.clearTimeout(timer);
  }
}

interface TemplateCard {
  id: string;
  label: string;
  style: string;
  en: string;
  /** null = 接口未就绪时的种子占位（只用于结构，不伪造任何进度） */
  template: CinemaTemplate | null;
}

interface DetailErrorState {
  message: string;
  status: number;
}

const SHOT_KIND_TEXT: Record<string, string> = {
  broll: "B-roll",
  dialogue: "对白",
  card: "卡",
  voiceover: "旁白",
  action: "动作",
  reaction: "反应",
};

interface Templates3DProps extends StudioTabProps {
  /** 切换节目模板成功后，同步 shell 顶栏的 format_id chip */
  onProjectFormatChange?: (formatId: string) => void;
}

export function StudioTemplates3D({ project, episode, onProjectFormatChange }: Templates3DProps) {
  const notify = useToast();

  const [templates, setTemplates] = useState<CinemaTemplate[]>([]);
  const [summary, setSummary] = useState<CinemaTemplatesSummary | null>(null);
  const [jobsActive, setJobsActive] = useState(0);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [refreshToken, setRefreshToken] = useState(0);

  const [formatId, setFormatId] = useState(project.format_id || "");
  const [switchingId, setSwitchingId] = useState<string | null>(null);
  const [generatingId, setGeneratingId] = useState<string | null>(null);

  const [detail, setDetail] = useState<CinemaTemplateDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<DetailErrorState | null>(null);
  const [probe, setProbe] = useState<TemplateProbe | null>(null);

  const [previewId, setPreviewId] = useState<string | null>(null);

  // 外部（App 侧）project.format_id 变化时同步
  useEffect(() => {
    if (project.format_id) setFormatId(project.format_id);
  }, [project.format_id]);

  const loadTemplates = useCallback(async (mode: "initial" | "refresh") => {
    if (mode === "initial") setLoading(true);
    else setRefreshing(true);
    setError(null);
    try {
      const res = await cinemaApi.templates();
      setTemplates(res.templates);
      setSummary(res.summary);
      setJobsActive(res.jobs.active);
    } catch (err) {
      setError(errorText(err));
    } finally {
      if (mode === "initial") setLoading(false);
      else setRefreshing(false);
      setRefreshToken((token) => token + 1);
    }
  }, []);

  useEffect(() => {
    void loadTemplates("initial");
  }, [loadTemplates]);

  const refreshAll = useCallback(async () => {
    await loadTemplates("refresh");
  }, [loadTemplates]);

  const knownIds = useMemo(
    () =>
      new Set(
        templates.length ? templates.map((item) => item.id) : TEMPLATE_3D_SEEDS.map((seed) => seed.id),
      ),
    [templates],
  );
  const currentTemplateId = useMemo(() => templateIdFromFormat(formatId, knownIds), [formatId, knownIds]);
  const currentTemplate = useMemo(
    () => templates.find((item) => item.id === currentTemplateId) || null,
    [templates, currentTemplateId],
  );

  const cards = useMemo<TemplateCard[]>(() => {
    const seedMap = new Map(TEMPLATE_3D_SEEDS.map((seed) => [seed.id, seed]));
    if (templates.length) {
      return templates.map((item) => ({
        id: item.id,
        label: item.label || seedMap.get(item.id)?.label || item.id,
        style: item.style || seedMap.get(item.id)?.style || "",
        en: seedMap.get(item.id)?.en || item.id.toUpperCase(),
        template: item,
      }));
    }
    return TEMPLATE_3D_SEEDS.map((seed) => ({
      id: seed.id,
      label: seed.label,
      style: seed.style,
      en: seed.en,
      template: null,
    }));
  }, [templates]);

  const currentCard = useMemo(
    () => cards.find((card) => card.id === currentTemplateId) || null,
    [cards, currentTemplateId],
  );
  const previewCard = useMemo(() => cards.find((card) => card.id === previewId) || null, [cards, previewId]);

  // 当前模板详情（逐镜 / 成片 / QC）
  useEffect(() => {
    if (!currentTemplateId) {
      setDetail(null);
      setDetailError(null);
      setDetailLoading(false);
      return;
    }
    let alive = true;
    setDetailLoading(true);
    setDetailError(null);
    cinemaApi
      .templateDetail(currentTemplateId)
      .then((data) => {
        if (alive) setDetail(data);
      })
      .catch((err) => {
        if (!alive) return;
        setDetail(null);
        setDetailError({ message: errorText(err), status: err instanceof ApiError ? err.status : 0 });
      })
      .finally(() => {
        if (alive) setDetailLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [currentTemplateId, refreshToken]);

  // 当前模板的 report.json probe（分辨率 / 帧率 / 转场 / BGM）
  const reportUrl = currentTemplate?.report_url || "";
  useEffect(() => {
    if (!reportUrl) {
      setProbe(null);
      return;
    }
    let alive = true;
    fetch(mediaUrl(reportUrl))
      .then((res) => (res.ok ? res.json() : Promise.reject(new Error("HTTP " + res.status))))
      .then((data) => {
        if (alive) setProbe(parseReportProbe(data));
      })
      .catch(() => {
        if (alive) setProbe(null);
      });
    return () => {
      alive = false;
    };
  }, [reportUrl, refreshToken]);

  // Esc 关闭预览
  useEffect(() => {
    if (!previewId) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setPreviewId(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [previewId]);

  // ---- 切换项目模板（PATCH /projects/{pid}） ----
  const switchTemplate = useCallback(
    async (card: TemplateCard) => {
      if (card.id === currentTemplateId) return;
      const nextFormat = formatIdForTemplate(card.id);
      const confirmed = window.confirm(
        "切换项目节目模板？\n\n" +
          `项目：${project.name}\n` +
          `format_id：${formatId || "—"} → ${nextFormat}\n` +
          `3D 模板：${card.label}（${card.id}）\n\n` +
          "只改项目的节目模板，不会重建已写的台本 / 分镜 / 资产；如需按新模板重做，请到对应 tab 重新生成。",
      );
      if (!confirmed) return;
      setSwitchingId(card.id);
      try {
        const updated = await patchProjectFormat(project.id, nextFormat);
        const applied = updated.format_id || nextFormat;
        setFormatId(applied);
        onProjectFormatChange?.(applied);
        notify(`项目模板已切换为「${card.label}」（${applied}）`, "success");
      } catch (err) {
        const status = err instanceof ApiError ? err.status : 0;
        if (status === 404 || status === 405) {
          notify("切换接口未就绪：后端 PATCH /api/projects/{id} 尚未上线（tpl-backend 开发中），请稍后重试", "error");
        } else {
          notify("切换项目模板失败：" + errorText(err), "error");
        }
      } finally {
        setSwitchingId(null);
      }
    },
    [currentTemplateId, formatId, notify, onProjectFormatChange, project.id, project.name],
  );

  // ---- 重新生成该模板 3D（POST /cinema/templates/generate，本地 GPU） ----
  const regenerateTemplate = useCallback(
    async (card: TemplateCard) => {
      const template = card.template;
      if (!template) {
        notify("模板库接口未就绪，暂不能提交 3D 生成任务", "error");
        return;
      }
      const confirmed = window.confirm(
        `提交「${card.label}」的 3D 生成任务？\n\n` +
          `本地 ComfyUI MiniMax-H3：约 ${template.clips.total || 8} 镜 × 54–110s，会占用本地 GPU；` +
          "若已有 3D 任务在跑会返回 409。\n" +
          (template.final.exists ? "当前已有 3D 成片，force 重生成会覆盖该模板的镜头与成片。\n" : "") +
          "\n确认提交？",
      );
      if (!confirmed) return;
      setGeneratingId(card.id);
      try {
        const res = await cinemaApi.generateTemplates({ templates: [card.id], force: true });
        notify(`已提交「${card.label}」3D 生成任务（job ${res.job_id}），进度见 Cinema 模板库`, "success");
      } catch (err) {
        const status = err instanceof ApiError ? err.status : 0;
        if (status === 409) {
          notify("已有 3D 生成 / 合成任务在运行（GPU 串行）：" + errorText(err), "error");
        } else if (status === 404 || status === 405) {
          notify("3D 生成接口未就绪，先跳 Cinema 模板库查看进度", "info");
          window.location.hash = LIBRARY_HREF.replace(/^#/, "");
        } else {
          notify("提交 3D 生成失败：" + errorText(err), "error");
        }
      } finally {
        setGeneratingId(null);
      }
    },
    [notify],
  );

  // ---- 派生数据 ----
  const loaded = templates.length > 0;
  const totalTemplates = summary?.total ?? (loaded ? templates.length : TEMPLATE_3D_SEEDS.length);
  const doneCount = loaded ? summary?.done ?? templates.filter((item) => item.status === "done").length : null;
  const clipsDone = loaded ? summary?.clips_done ?? 0 : null;
  const clipsTotal = loaded ? summary?.clips_total ?? 0 : null;
  const currentFinal = currentTemplate?.final || null;
  const currentProgress = currentTemplate ? templateClipPercent(currentTemplate) : 0;
  const currentTag = currentTemplate ? statusTagClass(currentTemplate.status) : "is-slate";

  const qcCell = (label: string, value: string, hint: string) => (
    <div className="st-tpl3d-qc-item">
      <span className="st-kpi-label">{label}</span>
      <b className="cm-mono">{value}</b>
      <em>{hint}</em>
    </div>
  );

  const shotKindChips = useMemo(() => {
    if (!detail?.shots?.length) return [];
    const counts = new Map<string, number>();
    detail.shots.forEach((shot) => {
      const key = shot.kind || "shot";
      counts.set(key, (counts.get(key) || 0) + 1);
    });
    return Array.from(counts.entries()).map(([kind, count]) => ({
      kind,
      count,
      label: SHOT_KIND_TEXT[kind] || kind,
    }));
  }, [detail]);

  return (
    <div className="st-page">
      <header className="st-page-head">
        <div className="st-head-main">
          <span className="st-kicker">TEMPLATE 3D · 模板</span>
          <h2 className="st-h1">3D 模板</h2>
          <p className="st-sub">
            第 {episode.no} 期 · 项目模板 <span className="cm-mono">{formatId || "—"}</span>
            {" → "}
            3D 模板 <span className="cm-mono">{currentTemplateId || "未匹配"}</span>
            {doneCount !== null ? (
              <>
                {" "}
                · 已完成{" "}
                <span className="cm-mono">
                  {doneCount}/{totalTemplates}
                </span>
              </>
            ) : null}
          </p>
        </div>
        <div className="st-head-actions">
          <button type="button" className="cm-btn" onClick={() => void refreshAll()} disabled={refreshing || loading}>
            {refreshing ? <Spinner size={13} /> : <IconRefresh size={14} />}
            {refreshing ? "刷新中…" : "刷新"}
          </button>
          <a className="cm-btn cm-btn-primary" href={LIBRARY_HREF} title="打开 Cinema Studio 的 16 模板 3D 制作进度页">
            <IconExternal size={14} />
            Cinema 模板库
          </a>
        </div>
      </header>

      <div className="st-kpis">
        <div className="st-kpi">
          <span className="st-kpi-label">项目模板</span>
          <div className="st-kpi-value st-tpl3d-kpi-name">{currentTemplate?.label || currentCard?.label || "—"}</div>
          <div className="st-kpi-hint cm-mono">{formatId || "—"}</div>
        </div>
        <div className="st-kpi">
          <span className="st-kpi-label">3D 模板完成</span>
          <div className="st-kpi-value">
            {doneCount === null ? "—" : doneCount}
            <small>/ {totalTemplates}</small>
          </div>
          <div className="st-kpi-hint">16 支 3D 电影感样片</div>
        </div>
        <div className="st-kpi">
          <span className="st-kpi-label">3D 镜头</span>
          <div className="st-kpi-value">
            {clipsDone === null ? "—" : clipsDone}
            <small>/ {clipsTotal === null ? "—" : clipsTotal}</small>
          </div>
          <div className="st-kpi-hint">本地 ComfyUI H3 逐镜生成</div>
        </div>
        <div className="st-kpi">
          <span className="st-kpi-label">当前模板成片</span>
          <div className="st-kpi-value">{currentFinal?.exists ? fmtClock(currentFinal.duration) : "—"}</div>
          <div className="st-kpi-hint">
            {currentFinal?.exists ? fmtSize(currentFinal.size) : "尚无 3D 成片"}
          </div>
        </div>
        <div className="st-kpi">
          <span className="st-kpi-label">进行中任务</span>
          <div className="st-kpi-value">{loaded ? jobsActive : "—"}</div>
          <div className="st-kpi-hint">3D 生成 / 合成（GPU 串行）</div>
        </div>
      </div>

      {error && (
        <div className="st-error st-tpl3d-error">
          <IconAlert size={15} />
          <span>
            模板库接口失败：{error}
            {loaded ? "" : "（下方为 16 模板占位结构，3D 进度不可用）"}
          </span>
          <button type="button" className="cm-btn cm-btn-sm" onClick={() => void refreshAll()}>
            重试
          </button>
        </div>
      )}

      {loading && !loaded ? (
        <section className="st-panel">
          <div className="st-panel-body">
            <div className="st-loading">
              <Spinner size={14} /> 读取 16 模板 3D 数据…
            </div>
          </div>
        </section>
      ) : (
        <>
          {/* ---------------- 1. 项目当前模板大卡（海报 / 信息 / 成片播放） ---------------- */}
          <section className="st-panel">
            <div className="st-panel-head">
              <div>
                <h3 className="st-panel-title">项目当前模板</h3>
                <p className="st-panel-sub">
                  PROJECT TEMPLATE · <span className="cm-mono">{formatId || "—"}</span>
                  {currentTemplateId ? (
                    <>
                      {" "}
                      → <span className="cm-mono">{currentTemplateId}</span>
                    </>
                  ) : null}
                </p>
              </div>
              <div className="st-panel-right">
                {currentTemplate && <span className={"st-tag " + currentTag}>{statusText(currentTemplate.status)}</span>}
                <span className="cm-chip is-mono">{episode.aspect || project.aspect || "16:9"}</span>
              </div>
            </div>
            <div className="st-panel-body">
              {!currentTemplateId || !currentCard ? (
                <div className="st-empty">
                  <span>
                    当前项目模板（<span className="cm-mono">{formatId || "—"}</span>）没有对应的 3D 模板
                  </span>
                  <span className="st-hint">
                    16 个 3D 模板对应 format_id = &lt;模板 id&gt;{FORMAT_SUFFIX}；可在下方网格把项目切到有 3D 样片的模板。
                  </span>
                  <a className="cm-btn cm-btn-sm" href={LIBRARY_HREF}>
                    去模板库
                  </a>
                </div>
              ) : (
                <>
                  <div className="st-tpl3d-hero-grid">
                    <button
                      type="button"
                      className="st-tpl3d-hero-poster"
                      onClick={() => setPreviewId(currentCard.id)}
                      disabled={!currentFinal?.exists}
                      title={currentFinal?.exists ? "播放 3D 成片" : "该模板尚无 3D 成片"}
                    >
                      {currentTemplate?.poster_url ? (
                        <img src={mediaUrl(currentTemplate.poster_url)} alt={currentCard.label} />
                      ) : (
                        <span className="st-tpl3d-hero-fallback">
                          <IconFilm size={30} />
                          <b>{currentCard.label}</b>
                        </span>
                      )}
                      <span className="st-tpl3d-hero-badges">
                        <span className={"st-tag " + currentTag}>
                          <i className="cm-status-dot" style={{ background: "currentColor" }} />
                          {currentTemplate ? statusText(currentTemplate.status) : "接口未就绪"}
                        </span>
                        {currentFinal?.exists && (
                          <span className="st-tag is-slate cm-mono">{fmtClock(currentFinal.duration)}</span>
                        )}
                      </span>
                      {currentFinal?.exists && (
                        <span className="st-tpl3d-play-badge">
                          <IconPlay size={18} />
                        </span>
                      )}
                    </button>

                    <div className="st-tpl3d-hero-info">
                      <span className="st-kicker">3D 电影感样片</span>
                      <h4 className="st-tpl3d-hero-title">{currentCard.label}</h4>
                      <div className="st-tpl3d-hero-meta">
                        <span className="st-tag is-slate cm-mono">模板 {currentCard.id}</span>
                        <span className="st-tag is-slate cm-mono">format {formatId}</span>
                        <span className="st-tag is-slate cm-mono">
                          镜头 {currentTemplate ? `${currentTemplate.clips.done}/${currentTemplate.clips.total}` : "—"}
                        </span>
                      </div>
                      <p className="st-hint st-tpl3d-hero-look">
                        3D 风格 <span className="cm-mono">{currentTemplate?.style || currentCard.style || "—"}</span>
                        {currentFinal?.mtime ? (
                          <>
                            {" "}
                            · 成片更新 <span className="cm-mono">{fmtDateTime(currentFinal.mtime)}</span>
                          </>
                        ) : null}
                      </p>
                      <div className="st-tpl3d-hero-stats">
                        <div className="st-tpl3d-hero-stat">
                          <span>镜头完成</span>
                          <b>{currentTemplate ? `${currentTemplate.clips.done} / ${currentTemplate.clips.total}` : "—"}</b>
                        </div>
                        <div className="st-tpl3d-hero-stat">
                          <span>成片时长</span>
                          <b>{currentFinal?.exists ? fmtClock(currentFinal.duration) : "—"}</b>
                        </div>
                        <div className="st-tpl3d-hero-stat">
                          <span>成片大小</span>
                          <b>{currentFinal?.exists ? fmtSize(currentFinal.size) : "—"}</b>
                        </div>
                        <div className="st-tpl3d-hero-stat">
                          <span>画幅 / 帧率</span>
                          <b>{probe ? `${probe.width}×${probe.height} · ${probe.fps}fps` : "—"}</b>
                        </div>
                      </div>
                      <div className="st-tpl3d-hero-actions">
                        <button
                          type="button"
                          className="cm-btn cm-btn-primary cm-btn-sm"
                          onClick={() => setPreviewId(currentCard.id)}
                          disabled={!currentFinal?.exists}
                        >
                          <IconPlay size={14} /> 播放 3D 成片
                        </button>
                        <button
                          type="button"
                          className="cm-btn cm-btn-sm"
                          onClick={() => void regenerateTemplate(currentCard)}
                          disabled={generatingId !== null || !currentTemplate}
                        >
                          {generatingId === currentCard.id ? <Spinner size={12} /> : <IconSpark size={13} />}
                          {currentFinal?.exists ? "重新生成 3D" : "生成该模板 3D"}
                        </button>
                        <a className="cm-btn cm-btn-sm cm-btn-ghost" href={LIBRARY_HREF}>
                          <IconExternal size={13} /> 打开 Cinema 模板库
                        </a>
                      </div>
                    </div>
                  </div>

                  {/* 成片播放器 */}
                  <div className="st-tpl3d-player">
                    {currentFinal?.exists ? (
                      <>
                        <div className="st-player">
                          <video
                            controls
                            preload="metadata"
                            poster={currentTemplate?.poster_url ? mediaUrl(currentTemplate.poster_url) : undefined}
                            src={mediaUrl(currentFinal.url)}
                          />
                        </div>
                        <div className="st-tpl3d-player-meta">
                          <span className="st-tag is-jade">3D 成片</span>
                          <span className="st-tag is-slate cm-mono">{fmtClock(currentFinal.duration)}</span>
                          <span className="st-tag is-slate cm-mono">{fmtSize(currentFinal.size)}</span>
                          <span className="st-tag is-slate cm-mono">更新 {fmtDateTime(currentFinal.mtime)}</span>
                          {probe ? (
                            <span className="st-tag is-slate cm-mono">
                              {probe.width}×{probe.height} · {probe.fps}fps
                            </span>
                          ) : null}
                        </div>
                      </>
                    ) : (
                      <div className="st-empty">
                        <span>该模板尚无 3D 成片</span>
                        <span className="st-hint">可以去 Cinema 模板库生成，或先在下方切换到其它已有 3D 成片的模板。</span>
                        <a className="cm-btn cm-btn-sm" href={LIBRARY_HREF}>
                          去模板库
                        </a>
                      </div>
                    )}
                  </div>
                </>
              )}
            </div>
          </section>

          {/* ---------------- 2. 3D QC / 成片信息 ---------------- */}
          <section className="st-panel">
            <div className="st-panel-head">
              <div>
                <h3 className="st-panel-title">3D QC · 成片信息</h3>
                <p className="st-panel-sub cm-mono">
                  GET /api/cinema/templates/{currentTemplateId || "{tid}"} · 以产物为准
                </p>
              </div>
              <div className="st-panel-right">
                {detailLoading && <Spinner size={13} />}
                <span className="cm-chip is-mono">{currentTemplateId || "—"}</span>
              </div>
            </div>
            <div className="st-panel-body">
              {!currentTemplateId ? (
                <div className="st-empty">
                  <span>当前项目模板没有对应的 3D 模板</span>
                  <span className="st-hint">在下方 16 模板网格里切换到有 3D 样片的模板后再查看 QC。</span>
                  <a className="cm-btn cm-btn-sm" href={LIBRARY_HREF}>
                    去模板库
                  </a>
                </div>
              ) : detailLoading && !detail ? (
                <div className="st-loading">
                  <Spinner size={14} /> 读取 3D 详情…
                </div>
              ) : detailError ? (
                detailError.status === 404 ? (
                  <div className="st-empty">
                    <span>该模板尚无 3D 成片</span>
                    <span className="st-hint">{detailError.message}</span>
                    <a className="cm-btn cm-btn-sm" href={LIBRARY_HREF}>
                      去模板库
                    </a>
                  </div>
                ) : (
                  <div className="st-error">
                    <IconAlert size={15} />
                    <span>3D 详情读取失败：{detailError.message}</span>
                    <button type="button" className="cm-btn cm-btn-sm" onClick={() => void refreshAll()}>
                      重试
                    </button>
                  </div>
                )
              ) : detail ? (
                <>
                  <div className="st-tpl3d-qc">
                    {qcCell("镜头 / 完成", `${detail.clips.done} / ${detail.clips.total}`, `完成率 ${templateClipPercent(detail)}%`)}
                    {qcCell(
                      "成片时长",
                      detail.final.exists ? fmtClock(detail.final.duration) : "—",
                      detail.final.exists ? `更新 ${fmtDateTime(detail.final.mtime)}` : "尚无 3D 成片",
                    )}
                    {qcCell("文件大小", detail.final.exists ? fmtSize(detail.final.size) : "—", detail.final.url || "—")}
                    {qcCell("模板状态", statusText(detail.status), detail.error || "以 final.mp4 / report.json 产物为准")}
                    {qcCell(
                      "成片画幅",
                      probe ? `${probe.width}×${probe.height}` : "—",
                      probe ? `${probe.fps}fps · probe 实测` : "report.json 未就绪",
                    )}
                    {qcCell(
                      "后期",
                      probe ? `${probe.transitions} 个转场` : "—",
                      probe ? (probe.bgm ? "BGM 混音 · 字幕包装" : "无 BGM") : "—",
                    )}
                  </div>

                  {!detail.final.exists && (
                    <div className="st-tpl3d-progress">
                      <div className={"cm-progress" + (detail.status === "generating" ? " is-running" : "")}>
                        <i style={{ width: templateClipPercent(detail) + "%" }} />
                      </div>
                      <span className="st-hint">
                        逐镜生成 {detail.clips.done}/{detail.clips.total}（{templateClipPercent(detail)}%）
                        {detail.status === "assemble_failed" ? " · 合成失败，需重新合成" : ""}
                      </span>
                    </div>
                  )}

                  {shotKindChips.length > 0 && (
                    <div className="st-tpl3d-shot-chips">
                      <span className="st-hint">逐镜构成：</span>
                      {shotKindChips.map((chip) => (
                        <span key={chip.kind} className="cm-chip is-mono">
                          {chip.label} × {chip.count}
                        </span>
                      ))}
                      <span className="cm-chip is-mono">共 {detail.shots.length} 项</span>
                    </div>
                  )}

                  {detail.error && (
                    <div className="st-error st-tpl3d-error">
                      <IconAlert size={15} />
                      <span>模板错误：{detail.error}</span>
                    </div>
                  )}
                </>
              ) : (
                <div className="st-empty">
                  <span>暂无 3D 详情</span>
                  <span className="st-hint">可以刷新重试，或去 Cinema 模板库查看。</span>
                </div>
              )}
            </div>
          </section>

          {/* ---------------- 3. 16 模板切换网格 ---------------- */}
          <section className="st-panel">
            <div className="st-panel-head">
              <div>
                <h3 className="st-panel-title">16 模板 · 3D 成片</h3>
                <p className="st-panel-sub">点击海报预览 3D 样片 · 切换为本项目模板 · 重新生成 3D</p>
              </div>
              <div className="st-panel-right">
                {loaded && summary && (
                  <>
                    <span className="cm-chip is-jade">
                      完成 {summary.done}/{summary.total}
                    </span>
                    <span className="cm-chip is-mono">
                      镜头 {summary.clips_done}/{summary.clips_total}
                    </span>
                  </>
                )}
                {jobsActive > 0 && <span className="cm-chip is-gold">进行中 {jobsActive}</span>}
              </div>
            </div>
            <div className="st-panel-body">
              <div className="st-tpl3d-grid">
                {cards.map((card) => {
                  const template = card.template;
                  const isCurrent = card.id === currentTemplateId;
                  const final = template?.final;
                  const progress = template ? templateClipPercent(template) : 0;
                  return (
                    <article key={card.id} className={"st-tpl3d-card" + (isCurrent ? " is-current" : "")}>
                      <button
                        type="button"
                        className="st-tpl3d-poster"
                        onClick={() => setPreviewId(card.id)}
                        disabled={!final?.exists}
                        title={final?.exists ? "播放 3D 成片" : "该模板尚无 3D 成片"}
                      >
                        {template?.poster_url ? (
                          <img src={mediaUrl(template.poster_url)} alt={card.label} loading="lazy" />
                        ) : (
                          <span className="st-tpl3d-fallback">
                            <IconFilm size={20} />
                            <b>{card.label}</b>
                          </span>
                        )}
                        <span className={"st-tpl3d-status st-tag " + (template ? statusTagClass(template.status) : "is-slate")}>
                          {template ? statusText(template.status) : "接口未就绪"}
                        </span>
                        {isCurrent && <span className="st-tpl3d-current-badge">当前</span>}
                        {final?.exists && (
                          <span className="st-tpl3d-play-badge">
                            <IconPlay size={14} />
                          </span>
                        )}
                      </button>
                      <div className="st-tpl3d-card-body">
                        <div className="st-tpl3d-card-name">{card.label}</div>
                        <div className="st-tpl3d-card-sub cm-mono">
                          {card.en} · {formatIdForTemplate(card.id)}
                        </div>
                        <div className="st-tpl3d-card-meta">
                          <span className="cm-mono">{final?.exists ? fmtClock(final.duration) : "—"}</span>
                          <span className="cm-mono">{final?.exists ? fmtSize(final.size) : "—"}</span>
                          <span className="cm-mono">{template ? `${template.clips.done}/${template.clips.total} 镜` : "—"}</span>
                        </div>
                        {template && !final?.exists && progress > 0 && (
                          <div className="st-tpl3d-card-progress">
                            <div className={"cm-progress" + (template.status === "generating" ? " is-running" : "")}>
                              <i style={{ width: progress + "%" }} />
                            </div>
                            <span className="st-hint">{progress}%</span>
                          </div>
                        )}
                      </div>
                      <div className="st-tpl3d-card-actions">
                        <button
                          type="button"
                          className="cm-btn cm-btn-sm cm-btn-primary"
                          onClick={() => void switchTemplate(card)}
                          disabled={isCurrent || switchingId !== null}
                          title={isCurrent ? "已是本项目模板" : "PATCH /api/projects/{id} 切换 format_id"}
                        >
                          {switchingId === card.id ? <Spinner size={12} /> : null}
                          {isCurrent ? "当前模板" : "切换为本项目模板"}
                        </button>
                        <button
                          type="button"
                          className="cm-btn cm-btn-sm"
                          onClick={() => void regenerateTemplate(card)}
                          disabled={generatingId !== null || !template}
                          title={template ? "提交 3D 生成任务（本地 GPU，串行）" : "模板库接口未就绪"}
                        >
                          {generatingId === card.id ? <Spinner size={12} /> : <IconSpark size={12} />}
                          {final?.exists ? "重新生成 3D" : "生成该模板 3D"}
                        </button>
                      </div>
                    </article>
                  );
                })}
              </div>
            </div>
          </section>

          {/* ---------------- 4. 3D 制作说明 / 画幅对照 ---------------- */}
          <section className="st-panel">
            <div className="st-panel-head">
              <div>
                <h3 className="st-panel-title">3D 制作说明</h3>
                <p className="st-panel-sub">本地 H3 · 模板与项目画幅对照</p>
              </div>
            </div>
            <div className="st-panel-body">
              <div className="st-tpl3d-note-grid">
                <div className="st-tpl3d-note">
                  <span className="st-kpi-label">生成管线</span>
                  <b>本地 ComfyUI MiniMax-H3</b>
                  <em>4step turbo · 768×432 逐镜生成，render_v5 合成 1280×720 / 25fps 成片</em>
                </div>
                <div className="st-tpl3d-note">
                  <span className="st-kpi-label">规模</span>
                  <b>
                    {clipsTotal === null ? "—" : clipsTotal} 镜 × {totalTemplates} 模板
                  </b>
                  <em>16 支 31–42s 演示片，含字幕 / BGM / 风格包装 / 统一 loudnorm</em>
                </div>
                <div className="st-tpl3d-note">
                  <span className="st-kpi-label">当前状态</span>
                  <b>
                    {doneCount === null ? "—" : doneCount} / {totalTemplates} 已完成
                  </b>
                  <em>{jobsActive > 0 ? `进行中任务 ${jobsActive}（GPU 串行）` : "当前无 3D 生成 / 合成任务"}</em>
                </div>
              </div>

              <table className="st-table st-tpl3d-compare">
                <thead>
                  <tr>
                    <th>对照项</th>
                    <th>项目（本期）</th>
                    <th>3D 模板样片</th>
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    <td>节目模板</td>
                    <td className="cm-mono">{formatId || "—"}</td>
                    <td>
                      {currentTemplate ? (
                        <>
                          {currentTemplate.label} <span className="cm-mono">({currentTemplate.id})</span>
                        </>
                      ) : (
                        "—"
                      )}
                    </td>
                  </tr>
                  <tr>
                    <td>画幅</td>
                    <td className="cm-mono">{episode.aspect || project.aspect || "—"}</td>
                    <td className="cm-mono">{probe ? `${probe.width}×${probe.height}` : "—"}</td>
                  </tr>
                  <tr>
                    <td>分辨率 / 帧率</td>
                    <td className="cm-mono">
                      {episode.resolution || "—"} · {episode.fps || 25}fps
                    </td>
                    <td className="cm-mono">{probe ? `${probe.width}×${probe.height} · ${probe.fps}fps` : "—"}</td>
                  </tr>
                  <tr>
                    <td>时长</td>
                    <td>
                      目标 <span className="cm-mono">{fmtClock(episode.target_duration)}</span>
                    </td>
                    <td className="cm-mono">{currentFinal?.exists ? fmtClock(currentFinal.duration) : "—"}</td>
                  </tr>
                </tbody>
              </table>

              <ul className="st-tpl3d-rules">
                <li>3D 模板样片是 16 个节目模板统一重制的 31–42s 演示片，不是当期成片；当期成片请在「成片房」渲染。</li>
                <li>
                  切换项目模板只改 <span className="cm-mono">format_id</span>，不会自动重建已写的台本 / 分镜 / 资产。
                </li>
                <li>重新生成占用本地 GPU（16 模板全量约 120 镜、2–2.5 小时），同一时间只允许一个 3D 生成 / 合成任务。</li>
              </ul>
            </div>
          </section>
        </>
      )}

      {/* ---------------- 3D 样片预览 ---------------- */}
      {previewCard && (
        <div className="st-tpl3d-preview" onClick={() => setPreviewId(null)} role="dialog" aria-modal="true">
          <div className="st-tpl3d-preview-body" onClick={(event) => event.stopPropagation()}>
            <div className="st-tpl3d-preview-head">
              <div>
                <span className="st-kicker">3D 模板样片</span>
                <h3 className="st-tpl3d-preview-title">{previewCard.label}</h3>
                <div className="st-drawer-sub cm-mono">
                  {previewCard.id} · {formatIdForTemplate(previewCard.id)}
                </div>
              </div>
              <button type="button" className="st-drawer-close" onClick={() => setPreviewId(null)} aria-label="关闭">
                <IconClose size={15} />
              </button>
            </div>
            <div className="st-tpl3d-preview-stage">
              {previewCard.template?.final.exists ? (
                <video
                  controls
                  autoPlay
                  playsInline
                  poster={previewCard.template.poster_url ? mediaUrl(previewCard.template.poster_url) : undefined}
                  src={mediaUrl(previewCard.template.final.url)}
                />
              ) : (
                <div className="st-empty">
                  <span>该模板尚无 3D 成片</span>
                  <span className="st-hint">去 Cinema 模板库生成后再来预览。</span>
                </div>
              )}
            </div>
            <div className="st-tpl3d-preview-actions">
              {previewCard.template && (
                <span className={"st-tag " + statusTagClass(previewCard.template.status)}>
                  {statusText(previewCard.template.status)}
                </span>
              )}
              <span className="cm-chip is-mono">
                {previewCard.template ? `${previewCard.template.clips.done}/${previewCard.template.clips.total} 镜` : "—"}
              </span>
              <span className="cm-chip is-mono">
                {previewCard.template?.final.exists ? fmtClock(previewCard.template.final.duration) : "—"}
              </span>
              <span className="cm-chip is-mono">
                {previewCard.template?.final.exists ? fmtSize(previewCard.template.final.size) : "—"}
              </span>
              <div className="st-tpl3d-preview-buttons">
                {previewCard.id !== currentTemplateId && (
                  <button
                    type="button"
                    className="cm-btn cm-btn-primary cm-btn-sm"
                    onClick={() => {
                      setPreviewId(null);
                      void switchTemplate(previewCard);
                    }}
                    disabled={switchingId !== null}
                  >
                    切换为本项目模板
                  </button>
                )}
                <a className="cm-btn cm-btn-sm" href={LIBRARY_HREF}>
                  <IconExternal size={13} /> 打开模板库
                </a>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
