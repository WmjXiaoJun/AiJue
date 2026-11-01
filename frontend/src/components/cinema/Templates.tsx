// Cinema Studio · 模板库 · 3D（#/cinema/templates）
// 16 个 V5 模板重做成 30–45s 3D 电影感样片：总表 / 逐模板镜头进度 / 成片播放 / 生成与合成提交。
// 数据流：GET /api/cinema/templates（3s 轮询，有活动任务时 2s；页面隐藏时暂停）
//         GET /api/cinema/templates/{id}（抽屉里看逐镜状态）
//         POST /api/cinema/templates/generate|assemble → {job_id}
// 后端端点未就绪（404）时：优雅报错 + 重试，并用 16 个模板的 id/label 作占位顺序（不伪造进度）。
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { errorText, mediaUrl } from "../../lib/api";
import {
  TEMPLATE_3D_SEEDS,
  TEMPLATE_3D_TOTAL,
  TEMPLATE_STATUS_LABEL,
  cinemaApi,
  fmtDuration,
  fmtSize,
  templateClipPercent,
  templateStatusDot,
  type CinemaTemplate,
  type CinemaTemplateDetail,
  type CinemaTemplateSeed,
  type CinemaTemplatesResponse,
} from "../../lib/cinema";
import { useToast } from "../Toast";
import { Spinner } from "../ui";
import {
  IconAlert,
  IconChevronRight,
  IconClose,
  IconFilm,
  IconPlay,
  IconRefresh,
  IconSpark,
} from "../icons";
import "../../styles/cinema-templates.css";

const POLL_IDLE_MS = 3000;
const POLL_BUSY_MS = 2000;

interface CardItem {
  seed: CinemaTemplateSeed;
  template: CinemaTemplate | null;
}

function pad2(value: number): string {
  return String(value).padStart(2, "0");
}

function clockText(date: Date | null): string {
  if (!date) return "";
  return `${pad2(date.getHours())}:${pad2(date.getMinutes())}:${pad2(date.getSeconds())}`;
}

/** 有全部镜头（或已有成片）才允许合成 */
function canAssemble(template: CinemaTemplate | null): boolean {
  if (!template) return false;
  if (template.final.exists && template.final.url) return true;
  return template.clips.total > 0 && template.clips.done >= template.clips.total;
}

function mergeCards(data: CinemaTemplatesResponse | null): CardItem[] {
  const byId = new Map<string, CinemaTemplate>();
  for (const template of data?.templates ?? []) byId.set(template.id, template);
  const items: CardItem[] = [];
  const used = new Set<string>();
  for (const seed of TEMPLATE_3D_SEEDS) {
    items.push({ seed, template: byId.get(seed.id) ?? null });
    used.add(seed.id);
  }
  // 后端若返回 16 个之外的模板（未来新增），追加在队列尾部，不隐藏数据
  for (const template of data?.templates ?? []) {
    if (used.has(template.id)) continue;
    items.push({
      seed: { id: template.id, label: template.label || template.id, en: template.id.replace(/_/g, " ").toUpperCase(), style: template.style },
      template,
    });
  }
  return items;
}

export function CinemaTemplates() {
  const notify = useToast();

  const [data, setData] = useState<CinemaTemplatesResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");
  const [syncedAt, setSyncedAt] = useState<Date | null>(null);
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [playing, setPlaying] = useState<CinemaTemplate | null>(null);
  const [detailId, setDetailId] = useState<string | null>(null);

  const aliveRef = useRef(true);
  const inFlightRef = useRef(false);

  useEffect(() => {
    aliveRef.current = true;
    return () => {
      aliveRef.current = false;
    };
  }, []);

  const load = useCallback(async (silent: boolean) => {
    if (inFlightRef.current) return;
    inFlightRef.current = true;
    if (!silent) setLoading(true);
    try {
      const res = await cinemaApi.templates();
      if (!aliveRef.current) return;
      setData(res);
      setError("");
      setSyncedAt(new Date());
    } catch (err) {
      if (!aliveRef.current) return;
      setError(errorText(err));
    } finally {
      inFlightRef.current = false;
      if (aliveRef.current) {
        setLoading(false);
        setRefreshing(false);
      }
    }
  }, []);

  useEffect(() => {
    void load(false);
  }, [load]);

  const activeJobs = data?.jobs?.active ?? 0;
  const pollingBusy = activeJobs > 0 || busyAction !== null;

  // 3s 轮询（有活动任务 2s）；页面不可见时跳过，回到前台立刻补一次
  useEffect(() => {
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") void load(true);
    }, pollingBusy ? POLL_BUSY_MS : POLL_IDLE_MS);
    return () => window.clearInterval(timer);
  }, [load, pollingBusy]);

  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState === "visible") void load(true);
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, [load]);

  const cards = useMemo(() => mergeCards(data), [data]);
  const summary = data?.summary ?? null;
  const totalTemplates = summary?.total || TEMPLATE_3D_TOTAL;
  const clipsDone = summary?.clips_done ?? 0;
  const clipsTotal = summary?.clips_total ?? 0;
  const overallPct = clipsTotal > 0 ? Math.round((clipsDone / clipsTotal) * 100) : 0;
  const generatingCount = summary?.generating ?? 0;
  const doneCount = summary?.done ?? 0;
  const generatedCount = summary?.generated ?? cards.filter((item) => item.template?.status === "generated").length;
  const pendingCount =
    summary?.pending ?? Math.max(0, totalTemplates - doneCount - generatingCount - generatedCount);
  const failedCount =
    summary?.failed ?? cards.filter((item) => item.template?.status === "assemble_failed").length;
  const placeholderOnly = !data || data.templates.length === 0;

  const refresh = useCallback(async () => {
    setRefreshing(true);
    try {
      await load(true);
    } finally {
      if (aliveRef.current) setRefreshing(false);
    }
    if (aliveRef.current) notify("模板 3D 进度已刷新", "success");
  }, [load, notify]);

  /** 有 running 任务时禁止重复提交（给 toast 而不是静默） */
  const blockedByJobs = useCallback((): boolean => {
    if ((data?.jobs?.active ?? 0) > 0) {
      notify("已有进行中的生成任务（GPU 串行），等当前任务结束后再提交", "info");
      return true;
    }
    return false;
  }, [data, notify]);

  const submitGenerate = useCallback(
    async (ids: string[] | "all") => {
      if (busyAction) return;
      if (blockedByJobs()) return;
      const names =
        ids === "all"
          ? `全部 ${cards.length} 个模板`
          : ids.map((id) => cards.find((item) => item.seed.id === id)?.template?.label || cards.find((item) => item.seed.id === id)?.seed.label || id).join("、");
      const force = ids !== "all" && cards.find((item) => item.seed.id === ids[0])?.template?.status === "done";
      const busyNote =
        generatingCount > 0 ? `\n当前还有 ${generatingCount} 个模板在后台生成（同一块 GPU 串行），新任务可能排队或冲突。` : "";
      const tip = force
        ? `将强制重渲「${names}」的全部镜头（本地 ComfyUI H3，GPU 串行，约 20–40 分钟）。继续？`
        : `将把「${names}」排入 3D 生成队列（本地 ComfyUI H3，GPU 串行；已缓存镜头自动跳过）。继续？${busyNote}`;
      if (!window.confirm(tip)) return;
      const key = ids === "all" ? "generate:all" : `generate:${ids[0]}`;
      setBusyAction(key);
      try {
        const res = await cinemaApi.generateTemplates({ templates: ids, force: Boolean(force) });
        notify(`已提交 3D 生成任务${res?.job_id ? " · " + String(res.job_id).slice(0, 8) : ""}`, "success");
        await load(true);
      } catch (err) {
        notify("提交生成失败：" + errorText(err), "error");
      } finally {
        if (aliveRef.current) setBusyAction(null);
      }
    },
    [blockedByJobs, busyAction, cards, generatingCount, load, notify],
  );

  const submitAssemble = useCallback(
    async (ids: string[] | "all") => {
      if (busyAction) return;
      const names =
        ids === "all"
          ? "全部已就绪的模板"
          : ids.map((id) => cards.find((item) => item.seed.id === id)?.template?.label || id).join("、");
      if (!window.confirm(`将把「${names}」排入 3D 剪辑合成（ffmpeg 统一影调 / 字幕 / 响度）。继续？`)) return;
      const key = ids === "all" ? "assemble:all" : `assemble:${ids[0]}`;
      setBusyAction(key);
      try {
        const res = await cinemaApi.assembleTemplates({ templates: ids });
        notify(`已提交合成任务${res?.job_id ? " · " + String(res.job_id).slice(0, 8) : ""}`, "success");
        await load(true);
      } catch (err) {
        notify("提交合成失败：" + errorText(err), "error");
      } finally {
        if (aliveRef.current) setBusyAction(null);
      }
    },
    [busyAction, cards, load, notify],
  );

  const openCard = useCallback((item: CardItem) => {
    if (item.template?.final?.exists && item.template.final.url) {
      setPlaying(item.template);
      return;
    }
    setDetailId(item.seed.id);
  }, []);

  const genBlocked = activeJobs > 0 || busyAction !== null;

  return (
    <div className="cm-page cm-tpl">
      <header className="cm-page-head">
        <div className="cm-head-main">
          <div className="cm-kicker">TEMPLATE STUDIO · 3D</div>
          <h1 className="cm-h1">模板库 · 3D</h1>
          <p className="cm-sub">
            {TEMPLATE_3D_TOTAL} 个模板 · 后台生成中 {generatingCount}/{totalTemplates} · 共{" "}
            {clipsTotal > 0 ? clipsTotal : "—"} 镜
            <span className="cm-tpl-live" title={syncedAt ? `最近同步 ${clockText(syncedAt)}` : "等待首次同步"}>
              <i className={"cm-status-dot " + (pollingBusy ? "is-running" : "is-done")} />
              {pollingBusy ? "2s" : "3s"} 自动刷新
            </span>
          </p>
        </div>
        <div className="cm-head-actions cm-tpl-head-actions">
          <button
            type="button"
            className={"cm-btn cm-btn-primary cm-btn-sm" + (genBlocked ? " is-dim" : "")}
            onClick={() => void submitGenerate("all")}
            aria-disabled={genBlocked}
            title={genBlocked ? "已有进行中的生成任务" : "为全部 16 个模板排入 3D 生成队列"}
          >
            {busyAction === "generate:all" ? <Spinner size={13} /> : <IconSpark size={14} />} 全部生成
          </button>
          <button
            type="button"
            className={"cm-btn cm-btn-sm" + (busyAction ? " is-dim" : "")}
            onClick={() => void submitAssemble("all")}
            aria-disabled={busyAction !== null}
            title="把已生成全部镜头的模板排入剪辑合成"
          >
            {busyAction === "assemble:all" ? <Spinner size={13} /> : <IconFilm size={14} />} 全部合成
          </button>
          <button
            type="button"
            className={"cm-icon-btn" + (refreshing ? " is-busy" : "")}
            onClick={() => void refresh()}
            title="刷新模板 3D 进度"
          >
            {refreshing ? <Spinner size={14} /> : <IconRefresh />}
          </button>
        </div>
      </header>

      {error && (
        <div className="cm-error cm-tpl-errorbar" role="alert">
          <IconAlert size={16} />
          <span>
            {data ? "刷新模板库失败：" : "模板库接口尚未就绪："}
            {error}
          </span>
          <button type="button" className="cm-btn cm-btn-sm" onClick={() => void load(false)} disabled={loading}>
            {loading ? <Spinner size={13} /> : null} 重试
          </button>
        </div>
      )}

      <section className="cm-tpl-summary">
        <div className="cm-tpl-summary-main">
          <div className="cm-tpl-summary-top">
            <span className="cm-kicker">Production Progress</span>
            <b className="cm-tpl-pct">
              {overallPct}
              <small>%</small>
            </b>
          </div>
          <div className={"cm-progress cm-tpl-summary-bar" + (generatingCount > 0 ? " is-running" : "")}>
            <i style={{ width: `${Math.max(overallPct, clipsTotal > 0 ? 2 : 0)}%` }} />
          </div>
          <div className="cm-tpl-summary-nums">
            <span>
              镜头 <b>{clipsDone}</b> / {clipsTotal > 0 ? clipsTotal : "—"}
            </span>
            <span>
              模板 <b>{doneCount}</b> / {totalTemplates}
            </span>
            <span>3D 成片 {doneCount > 0 ? `${doneCount} 部` : "—"}</span>
          </div>
        </div>
        <div className="cm-tpl-chips">
          <span className="cm-chip is-jade">
            <i className="cm-status-dot is-done" /> 已完成 {doneCount}
          </span>
          <span className="cm-chip is-gold">
            <i className="cm-status-dot is-running" /> 生成中 {generatingCount}
          </span>
          {generatedCount > 0 && (
            <span className="cm-chip is-jade">
              <i className="cm-status-dot is-done" /> 待合成 {generatedCount}
            </span>
          )}
          <span className="cm-chip">
            <i className="cm-status-dot" /> 待生成 {pendingCount}
          </span>
          <span className="cm-chip is-red">
            <i className="cm-status-dot is-failed" /> 失败 {failedCount}
          </span>
        </div>
      </section>

      {placeholderOnly && (
        <div className="cm-tpl-notice">
          <IconAlert size={15} />
          <span>
            后端 <b className="cm-mono">/api/cinema/templates</b> 暂未返回数据：
            下面按 16 个模板的固定顺序占位，进度留空，接口连通后自动填充。
            模板 3D 生成由后台 GPU 串行执行（本地 ComfyUI H3，约 3–4 小时跑完 16 个模板）。
          </span>
        </div>
      )}

      {loading && !data && (
        <div className="cm-loading">
          <Spinner size={15} /> 正在读取模板 3D 进度…
        </div>
      )}

      {data && data.templates.length === 0 && (
        <div className="cm-empty cm-tpl-empty">
          <span>模板库还没有任何模板数据</span>
          <span className="cm-subtle" style={{ fontSize: 11.5 }}>
            后台跑 `python -m pipeline.templates3d --generate --templates all` 后这里会出现 16 张模板卡
          </span>
        </div>
      )}

      <div className="cm-tpl-grid">
        {cards.map((item) => (
          <TemplateCard
            key={item.seed.id}
            item={item}
            genBlocked={genBlocked}
            busyAction={busyAction}
            onPlay={(template) => setPlaying(template)}
            onGenerate={(template) => void submitGenerate([template.id])}
            onAssemble={(template) => void submitAssemble([template.id])}
            onDetail={(id) => setDetailId(id)}
            onOpen={(target) => openCard(target)}
          />
        ))}
      </div>

      <p className="cm-subtle" style={{ marginTop: 18, fontSize: 11, letterSpacing: "0.04em" }}>
        数据源：outputs/templates_3d/progress.json · contract/v5/cinema3d/templates3d/ · 成片
        outputs/templates_3d/&lt;id&gt;/final.mp4（1280×720 / 25fps）
      </p>

      {playing && <TemplatePlayer template={playing} onClose={() => setPlaying(null)} />}
      {detailId && (
        <TemplateDrawer
          seedId={detailId}
          onClose={() => setDetailId(null)}
          onGenerate={(id) => void submitGenerate([id])}
          onAssemble={(id) => void submitAssemble([id])}
          onPlay={(template) => {
            setDetailId(null);
            setPlaying(template);
          }}
        />
      )}
    </div>
  );
}

/* ------------------------------ 模板卡 ------------------------------ */
function TemplateCard({
  item,
  genBlocked,
  busyAction,
  onPlay,
  onGenerate,
  onAssemble,
  onDetail,
  onOpen,
}: {
  item: CardItem;
  genBlocked: boolean;
  busyAction: string | null;
  onPlay: (template: CinemaTemplate) => void;
  onGenerate: (template: CinemaTemplate) => void;
  onAssemble: (template: CinemaTemplate) => void;
  onDetail: (id: string) => void;
  onOpen: (item: CardItem) => void;
}) {
  const { seed, template } = item;
  const [failedPoster, setFailedPoster] = useState("");
  const label = template?.label || seed.label;
  const style = template?.style || seed.style;
  const status = template?.status ?? null;
  const clips = template?.clips ?? { done: 0, total: 0 };
  const final = template?.final ?? null;
  const hasFinal = Boolean(final?.exists && final.url);
  const pct = templateClipPercent(template);
  const statusLabel = status ? TEMPLATE_STATUS_LABEL[status] : "接口未就绪";
  const cardClass =
    "cm-tpl-card" + (status ? " is-" + status : " is-unknown") + (hasFinal ? " is-3d-final" : "");
  const generateKey = template ? `generate:${template.id}` : "";
  const assembleKey = template ? `assemble:${template.id}` : "";
  const generateLabel = template?.status === "done" ? "重生成" : "生成";
  const canGenerate = Boolean(template) && busyAction !== generateKey;

  return (
    <article className={cardClass}>
      <button
        type="button"
        className={"cm-tpl-poster" + (status === "generating" ? " is-generating" : "")}
        onClick={() => onOpen(item)}
        title={hasFinal ? "播放 3D 成片" : "查看模板镜头详情"}
      >
        {template?.poster_url && template.poster_url !== failedPoster ? (
          <img
            className="cm-tpl-poster-img"
            src={mediaUrl(template.poster_url)}
            alt={label}
            loading="lazy"
            onError={() => setFailedPoster(template?.poster_url ?? "")}
          />
        ) : (
          <span className="cm-tpl-poster-fallback">
            <i>{label.slice(0, 1)}</i>
            <em>{seed.en}</em>
          </span>
        )}
        <span className={"cm-tpl-pill " + (status ? "is-" + status : "")}>
          <i className={"cm-status-dot " + (status ? templateStatusDot(status) : "")} />
          {statusLabel}
        </span>
        {hasFinal && <span className="cm-tpl-badge3d">3D</span>}
        {status === "generating" && <span className="cm-tpl-sweep" />}
        {hasFinal && (
          <span className="cm-tpl-poster-play">
            <IconPlay size={18} />
          </span>
        )}
      </button>

      <div className="cm-tpl-body">
        <div className="cm-tpl-title">
          <b title={label}>{label}</b>
          <span className="cm-tpl-id cm-mono">{seed.id}</span>
        </div>

        <div className="cm-tpl-tags">
          <span className="cm-tpl-style" title={style}>
            {style}
          </span>
          <span className="cm-tpl-en">{seed.en}</span>
        </div>

        <div className="cm-tpl-clips">
          <span className="cm-tpl-clips-num">
            {clips.done}
            <small>/{clips.total > 0 ? clips.total : "—"}</small>
          </span>
          <span className="cm-tpl-clips-label">镜</span>
          <div
            className={"cm-progress cm-tpl-bar" + (status === "generating" ? " is-running" : "")}
            title={`${pct}%`}
          >
            <i style={{ width: `${Math.max(pct, clips.total > 0 || hasFinal ? 2 : 0)}%` }} />
          </div>
        </div>

        <div className={"cm-tpl-final" + (hasFinal ? "" : " is-empty")}>
          {hasFinal && final ? (
            <>
              <span className="cm-tpl-final-main">{fmtDuration(final.duration)}</span>
              <span className="cm-tpl-final-sub">{fmtSize(final.size)}</span>
            </>
          ) : (
            <span className="cm-tpl-final-main">—</span>
          )}
          <span className="cm-tpl-final-label">{hasFinal ? "3D 成片" : "未合成"}</span>
        </div>

        {template?.error && (
          <p className="cm-tpl-card-error" title={template.error}>
            {template.error}
          </p>
        )}

        <div className="cm-tpl-actions">
          <button
            type="button"
            className="cm-btn cm-btn-sm cm-btn-play"
            onClick={() => template && onPlay(template)}
            disabled={!hasFinal || !template}
            title={hasFinal ? "播放 3D 成片" : "还没有 3D 成片"}
          >
            <IconPlay size={11} /> 播放
          </button>
          <button
            type="button"
            className={"cm-btn cm-btn-sm cm-btn-ghost" + (genBlocked ? " is-dim" : "")}
            onClick={() => template && onGenerate(template)}
            aria-disabled={!canGenerate || genBlocked}
            title={genBlocked ? "已有进行中的生成任务" : `提交「${label}」3D 生成`}
          >
            {busyAction === generateKey ? <Spinner size={11} /> : null}
            {generateLabel}
          </button>
          <button
            type="button"
            className="cm-btn cm-btn-sm cm-btn-ghost"
            onClick={() => template && onAssemble(template)}
            disabled={!canAssemble(template) || busyAction === assembleKey}
            title={canAssemble(template) ? "合成 3D 成片" : "全部镜头就绪后才可合成"}
          >
            合成
          </button>
          <button
            type="button"
            className="cm-btn cm-btn-sm cm-btn-ghost cm-tpl-more"
            onClick={() => onDetail(seed.id)}
            aria-label={`查看 ${label} 的镜头详情`}
            title="镜头详情"
          >
            <IconChevronRight size={14} />
          </button>
        </div>
      </div>
    </article>
  );
}

/* ------------------------------ 播放弹层 ------------------------------ */
function TemplatePlayer({ template, onClose }: { template: CinemaTemplate; onClose: () => void }) {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const final = template.final;
  return createPortal(
    <div className="cm-tpl-modal" onMouseDown={onClose} role="dialog" aria-modal="true" aria-label={`${template.label} 3D 成片`}>
      <div className="cm-tpl-modal-inner" onMouseDown={(event) => event.stopPropagation()}>
        <header className="cm-tpl-modal-head">
          <div style={{ minWidth: 0 }}>
            <span className="cm-tpl-modal-kicker">3D 成片 · {template.style || "3D"}</span>
            <h3 className="cm-tpl-modal-title">
              {template.label}
              <small>{template.id}</small>
            </h3>
          </div>
          <button type="button" className="cm-tpl-modal-close" onClick={onClose} aria-label="关闭">
            <IconClose size={17} />
          </button>
        </header>
        <video
          className="cm-tpl-video"
          src={mediaUrl(final.url)}
          controls
          autoPlay
          playsInline
          preload="metadata"
        />
        <footer className="cm-tpl-modal-foot">
          <span className="cm-tpl-modal-meta">
            {fmtDuration(final.duration)} · {fmtSize(final.size)}
          </span>
          <span>
            镜头 {template.clips.done}/{template.clips.total || "—"} · 3D 重制
          </span>
          <span className="cm-tpl-modal-spacer" />
          {template.report_url && (
            <a className="cm-btn cm-btn-sm" href={mediaUrl(template.report_url)} target="_blank" rel="noreferrer">
              制作报告
            </a>
          )}
          <a className="cm-btn cm-btn-sm" href={mediaUrl(final.url)} target="_blank" rel="noreferrer">
            打开原片
          </a>
        </footer>
      </div>
    </div>,
    document.body,
  );
}

/* ------------------------------ 详情抽屉（逐镜状态） ------------------------------ */
function TemplateDrawer({
  seedId,
  onClose,
  onGenerate,
  onAssemble,
  onPlay,
}: {
  seedId: string;
  onClose: () => void;
  onGenerate: (id: string) => void;
  onAssemble: (id: string) => void;
  onPlay: (template: CinemaTemplate) => void;
}) {
  const [detail, setDetail] = useState<CinemaTemplateDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const seed = TEMPLATE_3D_SEEDS.find((item) => item.id === seedId);
  const aliveRef = useRef(true);

  useEffect(() => {
    aliveRef.current = true;
    return () => {
      aliveRef.current = false;
    };
  }, []);

  const load = useCallback(
    async (silent: boolean) => {
      if (!silent) setLoading(true);
      try {
        const res = await cinemaApi.templateDetail(seedId);
        if (!aliveRef.current) return;
        setDetail(res);
        setError("");
      } catch (err) {
        if (!aliveRef.current) return;
        setError(errorText(err));
      } finally {
        if (aliveRef.current) setLoading(false);
      }
    },
    [seedId],
  );

  useEffect(() => {
    void load(false);
  }, [load]);

  // 抽屉打开时 3s 刷新逐镜状态（页面可见才刷）
  useEffect(() => {
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") void load(true);
    }, 3000);
    return () => window.clearInterval(timer);
  }, [load]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const label = detail?.label || seed?.label || seedId;
  const clips = detail?.clips ?? { done: 0, total: 0 };
  const pct = templateClipPercent(detail);
  const hasFinal = Boolean(detail?.final?.exists && detail.final.url);
  const shots = detail?.shots ?? [];

  return createPortal(
    <div className="cm-tpl-drawer" onMouseDown={onClose} role="dialog" aria-modal="true" aria-label={`${label} 镜头详情`}>
      <aside className="cm-tpl-drawer-inner" onMouseDown={(event) => event.stopPropagation()}>
        <header className="cm-tpl-drawer-head">
          <div className="cm-tpl-drawer-head-main">
            <span className="cm-tpl-drawer-kicker">Template · 3D Shots</span>
            <h3 className="cm-tpl-drawer-title">{label}</h3>
            <p className="cm-tpl-drawer-sub">
              {seedId} · {detail?.style || seed?.style || "—"}
              {detail ? ` · ${TEMPLATE_STATUS_LABEL[detail.status]}` : ""}
            </p>
          </div>
          <button type="button" className="cm-tpl-drawer-close" onClick={onClose} aria-label="关闭">
            <IconClose size={17} />
          </button>
        </header>

        <div className="cm-tpl-drawer-body">
          {error && (
            <div className="cm-tpl-detail-empty">
              <p style={{ margin: "0 0 10px" }}>模板详情读取失败：{error}</p>
              <button type="button" className="cm-btn cm-btn-sm" onClick={() => void load(false)}>
                重试
              </button>
            </div>
          )}

          {!error && loading && !detail && <div className="cm-tpl-detail-empty">正在读取模板详情…</div>}

          {detail && (
            <>
              <div className="cm-tpl-detail-stats">
                <div className="cm-tpl-detail-cell">
                  <span>Clips</span>
                  <b>
                    {clips.done}
                    <small>/{clips.total || "—"}</small>
                  </b>
                </div>
                <div className="cm-tpl-detail-cell">
                  <span>Master</span>
                  <b>{hasFinal ? fmtDuration(detail.final.duration) : "—"}</b>
                </div>
                <div className="cm-tpl-detail-cell">
                  <span>Size</span>
                  <b style={{ fontSize: 12 }}>{hasFinal ? fmtSize(detail.final.size) : "—"}</b>
                </div>
              </div>

              <div style={{ height: 4, borderRadius: 999, background: "rgba(255,255,255,0.07)", overflow: "hidden" }}>
                <div
                  style={{
                    width: `${Math.max(pct, clips.total > 0 || hasFinal ? 2 : 0)}%`,
                    height: "100%",
                    borderRadius: 999,
                    background: "linear-gradient(90deg, #8f6f12, #e5c66d)",
                    boxShadow: "0 0 12px rgba(201,162,39,0.5)",
                  }}
                />
              </div>

              {detail.error && <div className="cm-tpl-detail-empty" style={{ marginTop: 12 }}>{detail.error}</div>}

              <div className="cm-tpl-detail-section">Shots · {shots.length || clips.total || 0} 镜</div>
              {shots.length > 0 ? (
                <div className="cm-tpl-shots">
                  {shots.map((shot) => (
                    <div key={shot.id} className="cm-tpl-shot-row">
                      <i
                        className={
                          "cm-status-dot " +
                          (shot.valid ? "is-done" : shot.status === "failed" ? "is-failed" : shot.status === "generating" ? "is-running" : "")
                        }
                      />
                      <span className="cm-tpl-shot-id">{shot.id}</span>
                      {shot.kind && <span className="cm-tpl-shot-kind">{shot.kind}</span>}
                      <span className="cm-tpl-shot-scene">{shot.scene || "—"}</span>
                      <span className="cm-tpl-shot-dur">{shot.duration ? `${Math.round(shot.duration)}s` : "—"}</span>
                      {shot.url ? (
                        <a className="cm-tpl-shot-link" href={mediaUrl(shot.url)} target="_blank" rel="noreferrer">
                          看
                        </a>
                      ) : (
                        <span className="cm-tpl-shot-link" style={{ opacity: 0.3 }}>
                          —
                        </span>
                      )}
                    </div>
                  ))}
                </div>
              ) : (
                <div className="cm-tpl-detail-empty">后端暂未返回逐镜记录（{detail.error || "检查 /api/cinema/templates/{id}"}）</div>
              )}
            </>
          )}
        </div>

        <footer className="cm-tpl-drawer-foot">
          <button
            type="button"
            className="cm-btn cm-btn-sm cm-btn-primary"
            onClick={() => onGenerate(seedId)}
            disabled={!detail}
          >
            <IconSpark size={13} /> {detail?.status === "done" ? "重生成" : "生成"}
          </button>
          <button
            type="button"
            className="cm-btn cm-btn-sm"
            onClick={() => onAssemble(seedId)}
            disabled={!canAssemble(detail)}
          >
            <IconFilm size={13} /> 合成
          </button>
          <span className="cm-tpl-drawer-foot-spacer" />
          {hasFinal && detail && (
            <button type="button" className="cm-btn cm-btn-sm" onClick={() => onPlay(detail)}>
              <IconPlay size={13} /> 播放 3D 成片
            </button>
          )}
        </footer>
      </aside>
    </div>,
    document.body,
  );
}
