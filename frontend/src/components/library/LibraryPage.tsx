// V20 · 统一「模板与素材 / 成片库」（#/library/<templates|styles|scenes|final>）
// 模板：复用 cinema/Templates（embedded 模式，去页头/重复 CTA）；风格/场景：复用 v5world/Styles|Scenes；
// 成片库：19 支 3D 成片画廊（播放/报告/QC/重新合成/镜头台入口）。
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { errorText } from "../../lib/api";
import { mediaSrc } from "../../lib/v5";
import { ApiError } from "../../lib/api";
import type { CinemaTemplate } from "../../lib/cinema";
import { cinemaApi, fmtDuration, fmtSize, isRealTemplate } from "../../lib/cinema";
import { isPublishBatchActive, publishPackageBatchApi } from "../../lib/cinema";
import type { PublishPackageBatch, PublishPackageBatchItem, PublishPlatform } from "../../lib/cinema";
import { downloadUrlAsBlob } from "../../lib/download";
import { isBatchActive, isRunActive, pipelineApi, pipelineBatchApi } from "../../lib/pipeline";
import type { PipelineBatch, PipelineBatchItem } from "../../lib/pipeline";
import type { V5CatalogResponse, V5Series, V5SeriesExport } from "../../lib/v5";
import { v5api } from "../../lib/v5";
import { useToast } from "../Toast";
import { Spinner } from "../ui";
import {
  IconAlert,
  IconCheck,
  IconClose,
  IconDownload,
  IconExternal,
  IconFilm,
  IconImage,
  IconPlay,
  IconPlus,
  IconRefresh,
  IconSearch,
  IconSpark,
  IconTemplate,
  IconVideo,
} from "../icons";
import { AppShell } from "../shell/AppShell";
import { CinemaTemplates } from "../cinema/Templates";
import { CreateTemplateWizard } from "./CreateTemplateWizard";
import { PublishPackageModal } from "../publish/PublishPackageModal";
import { V5Styles } from "../v5world/Styles";
import { V5Scenes } from "../v5world/Scenes";
import "../../styles/cinema.css";
import "../../styles/v18.css";
import "../../styles/library.css";
// V5 World 复用页（风格 / 场景）的样式：v5w-* 全在这份 CSS 里，缺了就会「图片铺满 + 标签挤成一行」
import "../../styles/v5world.css";

export type LibraryTab = "templates" | "styles" | "scenes" | "final";

const TAB_META: { key: LibraryTab; no: string; label: string; en: string }[] = [
  { key: "templates", no: "01", label: "模板", en: "TEMPLATES" },
  { key: "styles", no: "02", label: "风格", en: "STYLES" },
  { key: "scenes", no: "03", label: "场景", en: "SCENES" },
];

interface Props {
  tab: LibraryTab;
  onOpenSettings: () => void;
}

export function LibraryPage({ tab, onOpenSettings }: Props) {
  const notify = useToast();
  const [catalog, setCatalog] = useState<V5CatalogResponse | null>(null);
  const [templates3d, setTemplates3d] = useState<Record<string, CinemaTemplate>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [refreshing, setRefreshing] = useState(false);
  const [wizardOpen, setWizardOpen] = useState(false);
  const [templateVersion, setTemplateVersion] = useState(0);
  const [highlightTemplate, setHighlightTemplate] = useState<string | null>(null);
  const [createdTemplate, setCreatedTemplate] = useState<{ id: string; started: boolean; reason?: string } | null>(null);
  const [runningTemplateIds, setRunningTemplateIds] = useState<Set<string>>(new Set());
  // V25：批量选择 / 批量队列
  const [batchMode, setBatchMode] = useState(false);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [batch, setBatch] = useState<PipelineBatch | null>(null);
  const [batchLoading, setBatchLoading] = useState(false);
  const [batchSubmitting, setBatchSubmitting] = useState(false);
  const [batchError, setBatchError] = useState("");
  const [confirmCancel, setConfirmCancel] = useState(false);
  const inFlightRef = useRef(false);

  const load = useCallback(async (silent: boolean) => {
    if (inFlightRef.current) return;
    inFlightRef.current = true;
    if (!silent) setLoading(true);
    const [catRes, tplRes] = await Promise.allSettled([v5api.catalog(), cinemaApi.templates()]);
    const errors: string[] = [];
    if (catRes.status === "fulfilled") setCatalog(catRes.value);
    else errors.push("V5 目录：" + errorText(catRes.reason));
    if (tplRes.status === "fulfilled") {
      const map: Record<string, CinemaTemplate> = {};
      for (const item of tplRes.value.templates) map[item.id] = item;
      setTemplates3d(map);
    } else errors.push("3D 模板：" + errorText(tplRes.reason));
    setError(errors.join("；"));
    inFlightRef.current = false;
    setLoading(false);
    setRefreshing(false);
  }, []);

  useEffect(() => {
    void load(false);
  }, [load]);

  // V25：进入页面时恢复正在运行的 batch（刷新/切页后继续看进度）
  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const list = await pipelineBatchApi.list(5);
        if (!alive) return;
        const active = list.find((item) => isBatchActive(item));
        if (active) setBatch(active);
      } catch {
        /* 接口未就绪时静默 */
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  // V25：batch 队列 3s 轮询（终态自动停止）
  useEffect(() => {
    if (!batch?.id || !isBatchActive(batch)) return;
    let alive = true;
    const poll = async () => {
      try {
        const next = await pipelineBatchApi.get(batch.id);
        if (!alive) return;
        setBatch(next);
        setBatchError("");
      } catch (err) {
        if (!alive) return;
        setBatchError(err instanceof ApiError ? err.message : "批量进度读取失败");
      }
    };
    const timer = window.setInterval(() => void poll(), 3000);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, [batch?.id, batch?.status]);

  // V23：生成中 badge —— /api/pipeline/runs 每 10s 轮询，匹配模板 running/queued job
  useEffect(() => {
    let alive = true;
    const poll = async () => {
      try {
        const runs = await pipelineApi.runs();
        if (!alive) return;
        setRunningTemplateIds(new Set(runs.filter((run) => isRunActive(run)).map((run) => run.template_id)));
      } catch {
        /* 轮询失败静默；不影响页面 */
      }
    };
    void poll();
    const timer = window.setInterval(() => void poll(), 10000);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, []);

  const refresh = useCallback(async () => {
    setRefreshing(true);
    await load(true);
    if (refreshing === false) notify("模板与素材数据已刷新", "success");
  }, [load, notify, refreshing]);

  /* ------------------------------ V25：批量选择 / 队列 ------------------------------ */
  const toggleBatchMode = useCallback(() => {
    setBatchMode((prev) => {
      if (prev) setSelectedIds(new Set());
      return !prev;
    });
  }, []);

  const toggleSelect = useCallback((templateId: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(templateId)) next.delete(templateId);
      else next.add(templateId);
      return next;
    });
  }, []);

  const clearSelection = useCallback(() => setSelectedIds(new Set()), []);

  const submitBatch = useCallback(
    async (reuseShots: boolean) => {
      const ids = Array.from(selectedIds);
      if (!ids.length) {
        notify("请先选择至少 1 个模板", "info");
        return;
      }
      setBatchSubmitting(true);
      setBatchError("");
      try {
        const started = await pipelineBatchApi.start(ids, reuseShots);
        setBatch(started);
        setConfirmCancel(false);
        notify(`已提交批量${reuseShots ? "合成" : "生成"} · ${ids.length} 个模板排队中`, "success");
        setBatchMode(false);
        setSelectedIds(new Set());
      } catch (err) {
        const status = err instanceof ApiError ? err.status : 0;
        const message = err instanceof Error ? err.message : String(err);
        if (status === 404 || status === 405) {
          setBatchError("批量接口尚未就绪（后端 V25 实现中），请稍后重试");
          notify("批量接口尚未就绪（后端 V25 实现中）", "info");
        } else if (status === 409) {
          setBatchError("已有任务运行中（单发或批量队列），请等待完成或取消后再试");
          notify("已有任务运行中，请等待完成或取消后再批量提交", "error");
        } else {
          setBatchError("批量提交失败：" + message);
          notify("批量提交失败：" + message, "error");
        }
      } finally {
        setBatchSubmitting(false);
      }
    },
    [notify, selectedIds],
  );

  const cancelBatch = useCallback(async () => {
    if (!batch?.id) return;
    setBatchLoading(true);
    try {
      const next = await pipelineBatchApi.cancel(batch.id);
      setBatch(next);
      setConfirmCancel(false);
      notify("已请求取消整批（进行中的任务会终止，未开始的标记为已取消）", "info");
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setBatchError("取消批量失败：" + message);
      notify("取消批量失败：" + message, "error");
    } finally {
      setBatchLoading(false);
    }
  }, [batch?.id, notify]);

  /** V25：batch 队列状态 → 模板卡片 badge */
  const batchStatusMap = useMemo(() => {
    const map = new Map<string, { status: string; done?: number; total?: number }>();
    batch?.items.forEach((item) => {
      if (item.status === "queued" || item.status === "running") {
        map.set(item.template_id, { status: item.status, done: item.progress?.done, total: item.progress?.total });
      }
    });
    return map;
  }, [batch]);

  const templates = useMemo(() => Object.values(templates3d), [templates3d]);
  const doneCount = templates.filter((item) => item.final.exists).length;
  const realCount = templates.filter((item) => isRealTemplate(item.id)).length;

  const active = tab === "final" ? "final" : "library";
  const title = tab === "final" ? "成片库" : "模板与素材";
  const kicker = tab === "final" ? "FINAL LIBRARY · 3D 成片" : "TEMPLATE & ASSET LIBRARY";
  const tabMeta = TAB_META.find((item) => item.key === tab);

  return (
    <AppShell
      active={active}
      kicker={kicker}
      title={title}
      breadcrumb={[{ label: "造片局" }, { label: tab === "final" ? "成片库" : tabMeta?.label || "模板与素材" }]}
      tabs={tab === "final" ? undefined : TAB_META.map((item) => ({ key: item.key, no: item.no, label: item.label, en: item.en }))}
      activeTab={tab}
      onTabChange={(key) => {
        window.location.hash = "#/library/" + key;
      }}
      chips={
        tab === "final"
          ? [
              { label: "3D 成片", value: `${doneCount}/${templates.length || 19}`, tone: "gold" },
              { label: "写实", value: String(realCount), tone: "slate" },
              { label: "时长", value: fmtDuration(templates.reduce((sum, item) => sum + (item.final.duration || 0), 0)), tone: "slate" },
            ]
          : [
              { label: "3D 模板", value: `${doneCount}/${templates.length || 19}`, tone: "gold" },
              { label: "风格", value: String(catalog?.styles.length ?? "—"), tone: "slate" },
              { label: "场景", value: String(catalog?.scenes.length ?? "—"), tone: "slate" },
            ]
      }
      primaryAction={
        tab === "templates"
          ? { label: "新建模板", onClick: () => setWizardOpen(true), icon: <IconPlus size={14} />, title: "创建自定义模板（4 步向导，含 TTS 分镜）" }
          : { label: "一键成片", href: "#/pipeline", icon: <IconSpark size={14} />, title: "进入一键成片控制台" }
      }
      onRefresh={() => void refresh()}
      refreshing={refreshing}
      onOpenSettings={onOpenSettings}
    >
      {error && (
        <div className="v18-error" style={{ marginBottom: 14 }}>
          <IconAlert size={15} />
          <div className="v18-error-main">
            <b>部分数据读取失败</b>
            <p className="v18-error-text">{error}</p>
          </div>
          <div className="v18-error-actions">
            <button type="button" className="cm-btn cm-btn-sm" onClick={() => void load(false)}>
              <IconRefresh size={12} /> 重试
            </button>
          </div>
        </div>
      )}

      {tab === "templates" && (
        <div className="v25-batch-toolbar">
          <button
            type="button"
            className={"cm-btn cm-btn-sm" + (batchMode ? " cm-btn-primary" : "")}
            onClick={toggleBatchMode}
            title="多选模板后批量排队生成/合成"
          >
            <IconCheck size={12} /> {batchMode ? "退出批量选择" : "批量选择"}
          </button>
          {batchMode && <span className="v25-batch-hint">点选模板卡片勾选；底部批量条可提交队列</span>}
          {batch?.id && isBatchActive(batch) && (
            <button type="button" className="cm-btn cm-btn-sm" onClick={() => setBatch(batch)} title="查看当前批量队列">
              <IconFilm size={12} /> 批量队列进行中（{batch.summary?.done ?? 0}/{batch.total}）
            </button>
          )}
          {batchError && <span className="v25-batch-error">{batchError}</span>}
        </div>
      )}
      {tab === "templates" && createdTemplate && (
        <div className={"v21-created-banner" + (createdTemplate.started ? " is-running" : "")}>
          <IconCheck size={14} />
          <span>
            模板 <b className="cm-mono">{createdTemplate.id}</b> 已创建
            {createdTemplate.started ? " · 3D 生成已开始（后台任务，进度在「一键成片」）" : "（pending）"}
            {!createdTemplate.started && createdTemplate.reason === "busy" ? " · 已有任务运行，可稍后生成" : ""}
          </span>
          <a
            className="cm-btn cm-btn-primary cm-btn-sm"
            href={"#/pipeline?template=" + encodeURIComponent(createdTemplate.id)}
            onClick={() => {
              try {
                sessionStorage.setItem("v23_pipeline_template", createdTemplate.id);
              } catch {
                /* ignore */
              }
            }}
          >
            <IconSpark size={12} /> {createdTemplate.started ? "查看生成进度 →" : "去一键成片"}
          </a>
          <button type="button" className="cm-btn cm-btn-sm" onClick={() => setCreatedTemplate(null)}>
            知道了
          </button>
        </div>
      )}
      {tab === "templates" && (
        <CinemaTemplates
          key={templateVersion}
          embedded
          highlightId={highlightTemplate || undefined}
          runningTemplateIds={runningTemplateIds}
          batchMode={batchMode}
          selectedIds={selectedIds}
          onToggleSelect={toggleSelect}
          batchStatus={batchStatusMap}
          onOpenShots={() => (window.location.hash = "#/cinema/shots")}
        />
      )}
      {tab === "styles" && (
        <V5Styles
          catalog={catalog}
          templates3d={templates3d}
          loading={loading}
          error={error}
          onRefresh={() => void refresh()}
          onNavigate={() => undefined}
          onRenderTemplate={() => (window.location.hash = "#/pipeline")}
        />
      )}
      {tab === "scenes" && (
        <V5Scenes
          catalog={catalog}
          templates3d={templates3d}
          loading={loading}
          error={error}
          onRefresh={() => void refresh()}
          onNavigate={() => undefined}
          onRenderTemplate={() => (window.location.hash = "#/pipeline")}
        />
      )}
      {tab === "final" && <FinalLibrary templates={templates} loading={loading} onRefresh={() => void refresh()} batchStatus={batchStatusMap} />}

      {tab === "templates" && batchMode && (
        <div className="v25-batchbar" role="region" aria-label="批量操作">
          <div className="v25-batchbar-info">
            <b>已选 {selectedIds.size} 个</b>
            <span>预计约 {Math.max(1, selectedIds.size * 2)}–{Math.max(1, selectedIds.size * 30)} 分钟（合成≈{Math.max(1, selectedIds.size * 2)} 分钟 / 生成≈{Math.max(1, selectedIds.size * 30)} 分钟）</span>
          </div>
          <div className="v25-batchbar-actions">
            <button
              type="button"
              className="cm-btn cm-btn-primary cm-btn-sm"
              disabled={batchSubmitting || !selectedIds.size}
              onClick={() => void submitBatch(false)}
              title="逐个模板走完整 3D 生成（串行排队；耗时较长）"
            >
              {batchSubmitting ? <Spinner size={12} /> : <IconSpark size={12} />} 批量生成
            </button>
            <button
              type="button"
              className="cm-btn cm-btn-sm"
              disabled={batchSubmitting || !selectedIds.size}
              onClick={() => void submitBatch(true)}
              title="复用已有镜头只跑剪辑合成（快，不重新生成镜头）"
            >
              <IconFilm size={12} /> 批量合成（复用已有镜头）
            </button>
            <button type="button" className="cm-btn cm-btn-sm cm-btn-ghost" onClick={clearSelection} disabled={!selectedIds.size}>
              取消选择
            </button>
          </div>
        </div>
      )}

      {batch?.id && (
        <div className="v25-batch-mask" onMouseDown={() => !isBatchActive(batch) && setBatch(null)} role="presentation">
          <aside className="v25-batch-drawer" onMouseDown={(event) => event.stopPropagation()} role="dialog" aria-modal="true" aria-label="批量队列">
            <header className="v25-batch-head">
              <div>
                <span className="v21-drawer-kicker">BATCH PIPELINE · 批量队列</span>
                <h3 className="v21-drawer-title">
                  {batch.status === "running" ? "批量进行中" : batch.status === "queued" ? "批量排队中" : batch.status === "done" ? "批量完成" : batch.status === "canceled" ? "批量已取消" : "批量失败"}
                </h3>
                <p className="v21-drawer-sub">
                  {batch.id} · {batch.total} 个模板 · {batch.reuse_shots ? "复用已有镜头合成" : "完整 3D 生成"} · 每 3s 刷新
                </p>
              </div>
              <button type="button" className="v21-close" onClick={() => setBatch(null)} aria-label="收起队列">
                <IconClose size={16} />
              </button>
            </header>
            <div className="v25-batch-summary">
              <span className="v18-tag is-jade">完成 {batch.summary?.done ?? 0}</span>
              <span className="v18-tag is-gold">进行 {batch.summary?.running ?? 0}</span>
              <span className="v18-tag">排队 {batch.summary?.queued ?? 0}</span>
              <span className="v18-tag is-red">失败 {batch.summary?.failed ?? 0}</span>
              {batch.elapsed_sec ? <span className="v18-tag">耗时 {Math.round(batch.elapsed_sec)}s</span> : null}
            </div>
            <div className="v25-batch-body">
              {batch.items.map((item, index) => (
                <BatchItemRow key={item.template_id + index} item={item} index={index} active={index === batch.current_index} />
              ))}
              {batchError && (
                <div className="v21-error" style={{ marginTop: 10 }}>
                  <b>队列状态读取失败</b>
                  <span>{batchError}</span>
                </div>
              )}
            </div>
            <footer className="v25-batch-foot">
              {isBatchActive(batch) ? (
                confirmCancel ? (
                  <>
                    <span className="v21-foot-note">确认取消整批？进行中的任务会终止，未开始的标记为已取消。</span>
                    <button type="button" className="cm-btn cm-btn-primary cm-btn-sm" onClick={() => void cancelBatch()} disabled={batchLoading}>
                      {batchLoading ? <Spinner size={12} /> : null} 确认取消整批
                    </button>
                    <button type="button" className="cm-btn cm-btn-sm" onClick={() => setConfirmCancel(false)} disabled={batchLoading}>
                      继续等待
                    </button>
                  </>
                ) : (
                  <>
                    <span className="v21-foot-note">批量队列串行执行；可离开页面，再次进入会恢复队列进度。</span>
                    <button type="button" className="cm-btn cm-btn-sm cm-btn-ghost" onClick={() => setConfirmCancel(true)}>
                      取消整批
                    </button>
                  </>
                )
              ) : (
                <>
                  <span className="v21-foot-note">
                    {batch.summary?.failed ? `批量结束：完成 ${batch.summary?.done ?? 0} / 失败 ${batch.summary.failed}，可到成片库查看已完成成片。` : "批量结束，可去成片库查看/播放成片。"}
                  </span>
                  <a className="cm-btn cm-btn-primary cm-btn-sm" href="#/library/final">
                    <IconVideo size={12} /> 去成片库
                  </a>
                  <button type="button" className="cm-btn cm-btn-sm" onClick={() => setBatch(null)}>
                    关闭
                  </button>
                </>
              )}
            </footer>
          </aside>
        </div>
      )}

      {wizardOpen && (
        <CreateTemplateWizard
          open={wizardOpen}
          catalog={catalog}
          existingTemplates={catalog?.templates || []}
          onClose={() => setWizardOpen(false)}
          onCreated={(id, res) => {
            setWizardOpen(false);
            setTemplateVersion((prev) => prev + 1);
            setHighlightTemplate(id);
            const started = Boolean(res?.pipeline?.started);
            setCreatedTemplate({ id, started, reason: res?.pipeline?.reason });
            if (started) {
              setRunningTemplateIds((prev) => new Set(prev).add(id));
            }
            void load(true);
          }}
        />
      )}
    </AppShell>
  );
}

/* ------------------------------ 成片库 ------------------------------ */
function FinalLibrary({
  templates,
  loading,
  onRefresh,
  batchStatus,
}: {
  templates: CinemaTemplate[];
  loading: boolean;
  onRefresh: () => void;
  batchStatus?: Map<string, { status: string; done?: number; total?: number }>;
}) {
  const notify = useToast();
  const [filter, setFilter] = useState<"all" | "classic" | "real">("all");
  const [query, setQuery] = useState("");
  const [playing, setPlaying] = useState<CinemaTemplate | null>(null);
  const [assembling, setAssembling] = useState<string | null>(null);
  const [publishTemplate, setPublishTemplate] = useState<CinemaTemplate | null>(null);
  // V29：批量导出发布包
  const [exportMode, setExportMode] = useState(false);
  const [exportIds, setExportIds] = useState<Set<string>>(new Set());
  const [exportPlatform, setExportPlatform] = useState<PublishPlatform>("generic");
  const [exportVertical, setExportVertical] = useState(false);
  const [exportVerticalMode, setExportVerticalMode] = useState<"blur_pad" | "crop">("blur_pad");
  const [pubBatch, setPubBatch] = useState<PublishPackageBatch | null>(null);
  const [pubSubmitting, setPubSubmitting] = useState(false);
  const [pubError, setPubError] = useState("");
  const [pubConfirmCancel, setPubConfirmCancel] = useState(false);
  const [pubCanceling, setPubCanceling] = useState(false);
  const [downloadingAll, setDownloadingAll] = useState(false);
  const [downloadAllIndex, setDownloadAllIndex] = useState(0);
  const [series, setSeries] = useState<V5Series[]>([]);
  const [seriesPlaying, setSeriesPlaying] = useState<{ series: V5Series; index: number } | null>(null);
  const [seriesBusy, setSeriesBusy] = useState<string | null>(null);
  const [seriesFiles, setSeriesFiles] = useState<Record<string, V5SeriesExport>>({});

  useEffect(() => {
    if (!playing) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setPlaying(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [playing]);

  useEffect(() => {
    let alive = true;
    void v5api
      .series()
      .then((res) => { if (alive) setSeries(res.series || []); })
      .catch(() => { if (alive) setSeries([]); });
    return () => { alive = false; };
  }, [templates.length]);

  const exportSeries = useCallback(
    async (item: V5Series) => {
      setSeriesBusy(item.id);
      try {
        const res = await v5api.exportSeries(item.id);
        setSeriesFiles((prev) => ({ ...prev, [item.id]: res }));
        notify(
          res.cached
            ? `合集已存在：${item.label}（${fmtDuration(res.duration || 0)}）`
            : `合集已生成：${item.label}（${fmtDuration(res.duration || 0)} · ${fmtSize(res.bytes || 0)}）`,
          "success",
        );
      } catch (err) {
        notify(`导出合集失败：${err instanceof Error ? err.message : String(err)}`, "error");
      } finally {
        setSeriesBusy(null);
      }
    },
    [notify],
  );

  useEffect(() => {
    if (!seriesPlaying) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setSeriesPlaying(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [seriesPlaying]);

  const list = useMemo(() => {
    const keyword = query.trim().toLowerCase();
    return templates.filter((item) => {
      if (filter === "real" && !isRealTemplate(item.id)) return false;
      if (filter === "classic" && isRealTemplate(item.id)) return false;
      if (!keyword) return true;
      return [item.id, item.label, item.style].filter(Boolean).join(" ").toLowerCase().includes(keyword);
    });
  }, [filter, query, templates]);

  const realCount = templates.filter((item) => isRealTemplate(item.id)).length;

  const reassemble = useCallback(
    async (template: CinemaTemplate) => {
      if (!window.confirm(`重新合成「${template.label}」的 3D 成片？（复用已有镜头跑剪辑合成）`)) return;
      setAssembling(template.id);
      try {
        const res = await cinemaApi.assembleTemplates({ templates: [template.id] });
        notify(`已提交合成任务${res?.job_id ? " · " + String(res.job_id).slice(0, 8) : ""}`, "success");
        onRefresh();
      } catch (err) {
        notify("提交合成失败：" + errorText(err), "error");
      } finally {
        setAssembling(null);
      }
    },
    [notify, onRefresh],
  );

  const toggleExportMode = useCallback(() => {
    setExportMode((prev) => {
      if (prev) setExportIds(new Set());
      return !prev;
    });
  }, []);

  const toggleExportSelect = useCallback((templateId: string) => {
    setExportIds((prev) => {
      const next = new Set(prev);
      if (next.has(templateId)) next.delete(templateId);
      else next.add(templateId);
      return next;
    });
  }, []);

  const changeExportPlatform = useCallback((platform: PublishPlatform) => {
    setExportPlatform(platform);
    const verticalDefault = platform === "tiktok" || platform === "instagram";
    setExportVertical(verticalDefault);
    setExportVerticalMode("blur_pad");
  }, []);

  // 进入成片库时恢复运行中的批量导出
  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const list = await publishPackageBatchApi.list(5);
        if (!alive) return;
        const active = list.find((item) => isPublishBatchActive(item));
        if (active) setPubBatch(active);
      } catch {
        /* 接口未就绪时静默 */
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  // 3s 轮询
  useEffect(() => {
    if (!pubBatch?.id || !isPublishBatchActive(pubBatch)) return;
    let alive = true;
    const poll = async () => {
      try {
        const next = await publishPackageBatchApi.get(pubBatch.id);
        if (!alive) return;
        setPubBatch(next);
        setPubError("");
      } catch (err) {
        if (!alive) return;
        setPubError(err instanceof Error ? err.message : "批量导出进度读取失败");
      }
    };
    const timer = window.setInterval(() => void poll(), 3000);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, [pubBatch?.id, pubBatch?.status]);

  const submitExport = useCallback(async () => {
    const ids = Array.from(exportIds);
    if (!ids.length) {
      notify("请先选择至少 1 支成片", "info");
      return;
    }
    setPubSubmitting(true);
    setPubError("");
    try {
      const batch = await publishPackageBatchApi.start({
        template_ids: ids,
        platform: exportPlatform,
        vertical: exportVertical,
        vertical_mode: exportVerticalMode,
        force: false,
      });
      setPubBatch(batch);
      setPubConfirmCancel(false);
      setExportMode(false);
      setExportIds(new Set());
      notify(`已提交批量导出 · ${ids.length} 支成片排队中`, "success");
    } catch (err) {
      const status = err instanceof ApiError ? err.status : 0;
      const message = err instanceof Error ? err.message : String(err);
      if (status === 404 || status === 405) {
        setPubError("批量导出接口尚未就绪（后端 V29 实现中），请稍后重试");
        notify("批量导出接口尚未就绪（后端 V29 实现中）", "info");
      } else {
        setPubError("批量导出失败：" + message);
        notify("批量导出失败：" + message, "error");
      }
    } finally {
      setPubSubmitting(false);
    }
  }, [exportIds, exportPlatform, exportVertical, exportVerticalMode, notify]);

  const cancelExport = useCallback(async () => {
    if (!pubBatch?.id) return;
    setPubCanceling(true);
    try {
      const next = await publishPackageBatchApi.cancel(pubBatch.id);
      setPubBatch(next);
      setPubConfirmCancel(false);
      notify("已请求取消批量导出（当前项完成后停止）", "info");
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setPubError("取消失败：" + message);
      notify("取消失败：" + message, "error");
    } finally {
      setPubCanceling(false);
    }
  }, [notify, pubBatch?.id]);

  const downloadOne = useCallback(
    async (item: PublishPackageBatchItem) => {
      if (!item.package_url) return;
      try {
        const res = await downloadUrlAsBlob(mediaSrc(item.package_url), `${item.template_id}_publish.zip`);
        notify(`已开始下载：${res.filename}（${fmtSize(res.size)}）`, "success");
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        setPubError("下载失败：" + message);
        notify("下载失败：" + message, "error");
      }
    },
    [notify],
  );

  const downloadAllDone = useCallback(async () => {
    const items = (pubBatch?.items || []).filter((item) => item.status === "done" && item.package_url);
    if (!items.length) {
      notify("没有可下载的发布包", "info");
      return;
    }
    setDownloadingAll(true);
    setDownloadAllIndex(0);
    let ok = 0;
    for (let i = 0; i < items.length; i += 1) {
      setDownloadAllIndex(i + 1);
      try {
        await downloadUrlAsBlob(mediaSrc(items[i].package_url as string), `${items[i].template_id}_publish.zip`);
        ok += 1;
      } catch (err) {
        notify(`「${items[i].label || items[i].template_id}」下载失败：${err instanceof Error ? err.message : String(err)}`, "error");
      }
      await new Promise((resolve) => window.setTimeout(resolve, 700));
    }
    setDownloadingAll(false);
    notify(`全部下载完成：${ok}/${items.length} 个发布包`, ok === items.length ? "success" : "info");
  }, [notify, pubBatch?.items]);

  const exportDoneItems = (pubBatch?.items || []).filter((item) => item.status === "done" && item.package_url);
  const exportSummary = pubBatch?.summary || {};

  return (
    <div className="v20-final">
      <div className="v20-final-toolbar">
        <label className="st-search">
          <IconSearch size={14} />
          <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索成片 / 模板" aria-label="搜索成片" />
          {query && (
            <button type="button" className="v5w-clear" onClick={() => setQuery("")} aria-label="清空搜索">
              <IconClose size={12} />
            </button>
          )}
        </label>
        <div className="tpl-seg" role="group" aria-label="成片筛选">
          {([
            { id: "all" as const, label: "全部", count: templates.length },
            { id: "classic" as const, label: "原 16 模板", count: templates.length - realCount },
            { id: "real" as const, label: "写实新模板", count: realCount },
          ]).map((item) => (
            <button
              key={item.id}
              type="button"
              className={filter === item.id ? "is-active" : ""}
              onClick={() => setFilter(item.id)}
              aria-pressed={filter === item.id}
            >
              {item.label}
              <span className="tpl-seg-count">{item.count}</span>
            </button>
          ))}
        </div>
        <span className="v18-filter-count">
          {list.length} / {templates.length} 支
        </span>
        <button
          type="button"
          className={"cm-btn cm-btn-sm" + (exportMode ? " cm-btn-primary" : "")}
          onClick={toggleExportMode}
          title="多选成片批量导出发布包"
        >
          <IconDownload size={12} /> {exportMode ? "退出批量导出" : "批量导出"}
        </button>
        {exportMode && <span className="v29-export-hint">勾选成片后使用底部批量条统一设置平台/竖版</span>}
        {pubError && !pubBatch && <span className="v25-batch-error">{pubError}</span>}
      </div>

      {series.length > 0 && (
        <div className="v20-series">
          <div className="v20-series-head">
            <b>系列</b>
            <span>{series.length} 条 · 同世界观连续播放</span>
          </div>
          <div className="v20-series-grid">
            {series.map((item) => (
              <article key={item.id} className="v20-series-card">
                <button
                  type="button"
                  className="v20-series-cover"
                  onClick={() => setSeriesPlaying({ series: item, index: 0 })}
                  title={item.label}
                >
                  {item.cover_url ? <img src={mediaSrc(item.cover_url)} alt={item.label} loading="lazy" /> : <span className="v20-final-fallback">{item.label.slice(0, 1)}</span>}
                  <span className="v20-series-count">{item.members.length} 集</span>
                  <span className="v20-final-play"><IconPlay size={18} /></span>
                </button>
                <div className="v20-series-body">
                  <b>{item.label}</b>
                  <span className="v20-series-desc">{item.description}</span>
                  <div className="v20-series-meta">
                    <span>{item.style}</span>
                    <span>{item.ready_count}/{item.members.length} 可用</span>
                    {item.total_duration ? <span>{fmtDuration(item.total_duration)}</span> : null}
                  </div>
                  <div className="v20-series-actions">
                    <button
                      type="button"
                      className="cm-btn cm-btn-primary cm-btn-sm"
                      onClick={() => setSeriesPlaying({ series: item, index: 0 })}
                      disabled={!item.ready_count}
                    >
                      <IconPlay size={12} /> 连续播放
                    </button>
                    <button
                      type="button"
                      className="cm-btn cm-btn-sm cm-btn-ghost"
                      onClick={() => setSeriesPlaying({ series: item, index: Math.max(0, item.members.findIndex((m) => m.ready)) })}
                    >
                      从第一部开始
                    </button>
                    <button
                      type="button"
                      className="cm-btn cm-btn-sm cm-btn-ghost"
                      onClick={() => void exportSeries(item)}
                      disabled={seriesBusy === item.id || !item.ready_count}
                      title="片头卡 + 全部集数串成一个 mp4"
                    >
                      <IconDownload size={12} /> {seriesBusy === item.id ? "合成中…" : "导出合集"}
                    </button>
                    {seriesFiles[item.id]?.url ? (
                      <a className="cm-btn cm-btn-sm v20-series-download" href={mediaSrc(seriesFiles[item.id].url)} download>
                        <IconDownload size={12} /> 下载合集 {fmtDuration(seriesFiles[item.id].duration || 0)}
                      </a>
                    ) : null}
                  </div>
                </div>
              </article>
            ))}
          </div>
        </div>
      )}

      {loading && !templates.length ? (
        <div className="v18-empty">
          <Spinner size={16} />
          <span>正在读取 19 支 3D 成片…</span>
        </div>
      ) : !list.length ? (
        <div className="v18-empty">
          <IconVideo size={26} />
          <b>没有匹配的成片</b>
          <span>换个关键词，或切回「全部」；成片由「一键成片 / 模板卡」生成。</span>
        </div>
      ) : (
        <div className="v20-final-grid">
          {list.map((template) => {
            const ready = Boolean(template.final.exists && template.final.url);
            const videoUrl = mediaSrc(template.final.url || `/media/templates_3d/${template.id}/final.mp4`);
            const qcUrl = mediaSrc(`/media/templates_3d/${template.id}/qc_report.json`);
            return (
              <article
                key={template.id}
                className={
                  "v20-final-card" +
                  (ready ? " is-ready" : "") +
                  (isRealTemplate(template.id) ? " is-real" : "") +
                  (exportMode ? " is-batch-mode" : "") +
                  (exportIds.has(template.id) ? " is-selected" : "")
                }
              >
                {exportMode && (
                  <label className="v25-card-check" onClick={(event) => event.stopPropagation()} title={exportIds.has(template.id) ? "取消选择" : "选择该成片"}>
                    <input
                      type="checkbox"
                      checked={exportIds.has(template.id)}
                      onChange={() => toggleExportSelect(template.id)}
                      aria-label={"选择成片 " + template.id}
                    />
                  </label>
                )}
                <button
                  type="button"
                  className="v20-final-poster"
                  onClick={() => (exportMode ? toggleExportSelect(template.id) : ready && setPlaying(template))}
                  disabled={!exportMode && !ready}
                  title={exportMode ? "点击选择/取消该成片" : ready ? "播放 3D 成片" : "尚无成片"}
                >
                  {template.poster_url ? (
                    <img src={mediaSrc(template.poster_url)} alt={template.label} loading="lazy" />
                  ) : (
                    <span className="v20-final-fallback">{template.label.slice(0, 1)}</span>
                  )}
                  {ready ? (
                    <span className="v20-final-badge"><IconPlay size={13} /> 3D</span>
                  ) : (
                    <span className="v20-final-badge is-pending">待生成</span>
                  )}
                  {isRealTemplate(template.id) && <span className="tpl-real-badge v20-final-real">写实</span>}
                  {batchStatus?.get(template.id) && (
                    <span className={"v20-final-batch is-" + batchStatus.get(template.id)?.status}>
                      {batchStatus.get(template.id)?.status === "queued"
                        ? "排队中"
                        : `生成中 ${batchStatus.get(template.id)?.done ?? 0}/${batchStatus.get(template.id)?.total ?? "—"}`}
                    </span>
                  )}
                  {ready && <span className="v20-final-play"><IconPlay size={18} /></span>}
                </button>
                <div className="v20-final-body">
                  <div className="v20-final-title">
                    <b>{template.label}</b>
                    <span className="cm-mono">{template.id}</span>
                  </div>
                  <div className="v20-final-meta">
                    <span>{fmtDuration(template.final.duration || 0)}</span>
                    <span>{fmtSize(template.final.size)}</span>
                    <span>镜头 {template.clips.done}/{template.clips.total || "—"}</span>
                    <span className="v18-tag is-gold">{template.status === "done" ? "已完成" : template.status}</span>
                    <a className="v20-final-source cm-mono" href={videoUrl} target="_blank" rel="noreferrer" title="打开原片">
                      <IconDownload size={11} /> 原片
                    </a>
                  </div>
                  <div className="v20-final-actions">
                    <button type="button" className="cm-btn cm-btn-primary cm-btn-sm" disabled={!ready} onClick={() => setPlaying(template)}>
                      <IconPlay size={12} /> 播放
                    </button>
                    {template.report_url && (
                      <a className="cm-btn cm-btn-sm" href={mediaSrc(template.report_url)} target="_blank" rel="noreferrer">
                        <IconExternal size={12} /> 报告
                      </a>
                    )}
                    <a className="cm-btn cm-btn-sm" href={qcUrl} target="_blank" rel="noreferrer" title="QC 报告 JSON（黑帧/响度/字幕覆盖/时长偏差）">
                      <IconImage size={12} /> QC
                    </a>
                    <button
                      type="button"
                      className="cm-btn cm-btn-sm is-publish"
                      onClick={() => setPublishTemplate(template)}
                      title="导出发布包（成片 + 封面 + 字幕 + 元数据 + README 的 zip）"
                    >
                      <IconDownload size={12} /> 发布包
                    </button>
                    <button type="button" className="cm-btn cm-btn-sm cm-btn-ghost" onClick={() => void reassemble(template)} disabled={assembling === template.id}>
                      {assembling === template.id ? <Spinner size={12} /> : <IconSpark size={12} />} 重新合成
                    </button>
                    <a className="cm-btn cm-btn-sm cm-btn-ghost" href="#/cinema/shots" title="进入 3D 制作台 · 镜头台">
                      <IconFilm size={12} /> 镜头台
                    </a>
                  </div>
                </div>
              </article>
            );
          })}
        </div>
      )}

      {exportMode && (
        <div className="v29-batchbar" role="region" aria-label="批量导出发布包">
          <div className="v29-batchbar-info">
            <b>已选 {exportIds.size} 支成片</b>
            <span>统一设置平台 / 竖版 / 模式后批量构建发布包</span>
          </div>
          <select
            className="v21-select v29-batchbar-select"
            value={exportPlatform}
            onChange={(event) => changeExportPlatform(event.target.value as PublishPlatform)}
            aria-label="目标平台"
            disabled={pubSubmitting}
          >
            <option value="generic">本地/通用</option>
            <option value="youtube">YouTube</option>
            <option value="tiktok">TikTok</option>
            <option value="instagram">Instagram</option>
          </select>
          <label className={"v29-batchbar-toggle" + (exportVertical ? " is-on" : "")}>
            <input
              type="checkbox"
              checked={exportVertical}
              onChange={(event) => setExportVertical(event.target.checked)}
              disabled={pubSubmitting}
            />
            竖版 9:16
          </label>
          <div className="v29-batchbar-modes" role="group" aria-label="竖版模式">
            <button
              type="button"
              className={"v28-vmode-btn" + (exportVerticalMode === "blur_pad" ? " is-active" : "")}
              onClick={() => setExportVerticalMode("blur_pad")}
              disabled={!exportVertical || pubSubmitting}
              title="模糊背景填充（完整保留 16:9 画面）"
            >
              模糊填充
            </button>
            <button
              type="button"
              className={"v28-vmode-btn" + (exportVerticalMode === "crop" ? " is-active" : "")}
              onClick={() => setExportVerticalMode("crop")}
              disabled={!exportVertical || pubSubmitting}
              title="中心裁切 9:16（主体居中素材）"
            >
              中心裁切
            </button>
          </div>
          <div className="v29-batchbar-actions">
            <button type="button" className="cm-btn cm-btn-primary cm-btn-sm" disabled={pubSubmitting || !exportIds.size} onClick={() => void submitExport()}>
              {pubSubmitting ? <Spinner size={12} /> : <IconDownload size={12} />} 批量导出发布包
            </button>
            <button
              type="button"
              className="cm-btn cm-btn-sm cm-btn-ghost"
              disabled={!exportIds.size || pubSubmitting}
              onClick={() => setExportIds(new Set())}
            >
              取消选择
            </button>
          </div>
        </div>
      )}

      {pubBatch && (
        <div className="v25-batch-mask" onMouseDown={() => !isPublishBatchActive(pubBatch) && setPubBatch(null)} role="presentation">
          <aside className="v25-batch-drawer v29-pub-drawer" onMouseDown={(event) => event.stopPropagation()} role="dialog" aria-modal="true" aria-label="批量导出发布包">
            <header className="v25-batch-head">
              <div>
                <span className="v21-drawer-kicker">BATCH PUBLISH · 批量发布包</span>
                <h3 className="v21-drawer-title">
                  {pubBatch.status === "running" || pubBatch.status === "queued"
                    ? "批量导出中"
                    : pubBatch.status === "done"
                      ? "批量导出完成"
                      : pubBatch.status === "partial_failed"
                        ? "批量导出部分失败"
                        : pubBatch.status === "canceled"
                          ? "批量导出已取消"
                          : "批量导出失败"}
                </h3>
                <p className="v21-drawer-sub">
                  {pubBatch.id} · {pubBatch.total} 支成片 · {pubBatch.platform || exportPlatform}
                  {pubBatch.vertical ? ` · 竖版 ${pubBatch.vertical_mode || exportVerticalMode}` : " · 16:9"} · 每 3s 刷新
                </p>
              </div>
              <button type="button" className="v21-close" onClick={() => setPubBatch(null)} aria-label="收起批量导出">
                <IconClose size={16} />
              </button>
            </header>
            <div className="v25-batch-summary">
              <span className="v18-tag is-jade">完成 {exportSummary.done ?? exportDoneItems.length}</span>
              <span className="v18-tag is-gold">构建中 {exportSummary.building ?? 0}</span>
              <span className="v18-tag">排队 {exportSummary.queued ?? 0}</span>
              <span className="v18-tag is-red">失败 {exportSummary.failed ?? 0}</span>
              {pubBatch.elapsed_sec ? <span className="v18-tag">耗时 {Math.round(pubBatch.elapsed_sec)}s</span> : null}
            </div>
            <div className="v25-batch-body">
              {pubBatch.items.map((item, index) => {
                const statusText =
                  item.status === "queued"
                    ? "排队中"
                    : item.status === "building"
                      ? "构建中"
                      : item.status === "done"
                        ? "已完成"
                        : item.status === "failed"
                          ? "失败"
                          : "已取消";
                return (
                  <div key={item.template_id + index} className={"v29-pub-item is-" + item.status + (index === pubBatch.current_index ? " is-active" : "")}>
                    <span className="v25-batch-no">{String(index + 1).padStart(2, "0")}</span>
                    <div className="v29-pub-item-main">
                      <div className="v29-pub-item-title">
                        <b>{item.label || item.template_id}</b>
                        <span className="cm-mono">{item.template_id}</span>
                      </div>
                      <div className="v29-pub-item-meta">
                        <span className={"v18-tag" + (item.status === "done" ? " is-jade" : item.status === "failed" ? " is-red" : item.status === "building" ? " is-gold" : "")}>
                          {statusText}
                        </span>
                        {item.status === "done" && (
                          <>
                            <span className="cm-mono">{fmtSize(item.size_bytes || 0)}</span>
                            <span>{item.files_count || 0} 文件</span>
                            <button type="button" className="cm-btn cm-btn-sm" onClick={() => void downloadOne(item)}>
                              <IconDownload size={11} /> 下载
                            </button>
                          </>
                        )}
                        {item.status === "building" && <span className="cm-mono">打包中…</span>}
                        {item.error && <span className="v29-pub-item-err">{item.error}</span>}
                      </div>
                    </div>
                  </div>
                );
              })}
              {pubError && (
                <div className="v21-error" style={{ marginTop: 10 }}>
                  <b>批量导出状态读取失败</b>
                  <span>{pubError}</span>
                </div>
              )}
            </div>
            <footer className="v25-batch-foot">
              {isPublishBatchActive(pubBatch) ? (
                pubConfirmCancel ? (
                  <>
                    <span className="v21-foot-note">确认取消整批？当前项完成后停止，未开始项标记为已取消。</span>
                    <button type="button" className="cm-btn cm-btn-primary cm-btn-sm" onClick={() => void cancelExport()} disabled={pubCanceling}>
                      {pubCanceling ? <Spinner size={12} /> : null} 确认取消整批
                    </button>
                    <button type="button" className="cm-btn cm-btn-sm" onClick={() => setPubConfirmCancel(false)} disabled={pubCanceling}>
                      继续等待
                    </button>
                  </>
                ) : (
                  <>
                    <button type="button" className="cm-btn cm-btn-sm" onClick={() => void downloadAllDone()} disabled={downloadingAll || !exportDoneItems.length}>
                      {downloadingAll ? <Spinner size={12} /> : <IconDownload size={12} />} 全部下载{downloadingAll ? `（${downloadAllIndex}/${exportDoneItems.length}）` : ""}
                    </button>
                    <span className="v21-foot-note">批量导出串行构建；可离开页面，再次进入会恢复队列进度。</span>
                    <button type="button" className="cm-btn cm-btn-sm cm-btn-ghost" onClick={() => setPubConfirmCancel(true)}>
                      取消整批
                    </button>
                  </>
                )
              ) : (
                <>
                  <span className="v21-foot-note">
                    {pubBatch.status === "partial_failed" || (exportSummary.failed ?? 0) > 0
                      ? `批量结束：完成 ${exportDoneItems.length} / 失败 ${exportSummary.failed ?? pubBatch.items.filter((item) => item.status === "failed").length}，可下载已完成发布包。`
                      : "批量结束，可下载全部发布包或关闭。"}
                  </span>
                  <button type="button" className="cm-btn cm-btn-primary cm-btn-sm" onClick={() => void downloadAllDone()} disabled={downloadingAll || !exportDoneItems.length}>
                    {downloadingAll ? <Spinner size={12} /> : <IconDownload size={12} />} 全部下载{downloadingAll ? `（${downloadAllIndex}/${exportDoneItems.length}）` : ""}
                  </button>
                  <button type="button" className="cm-btn cm-btn-sm" onClick={() => setPubBatch(null)}>
                    关闭
                  </button>
                </>
              )}
            </footer>
          </aside>
        </div>
      )}

      {publishTemplate && (
        <PublishPackageModal
          open
          templateId={publishTemplate.id}
          templateLabel={publishTemplate.label}
          onClose={() => setPublishTemplate(null)}
        />
      )}

      {playing && (
        <div className="v20-modal-mask" onMouseDown={() => setPlaying(null)} role="dialog" aria-modal="true" aria-label={playing.label}>
          <div className="v20-modal" onMouseDown={(event) => event.stopPropagation()}>
            <header className="v20-modal-head">
              <div>
                <span className="v20-modal-kicker">FINAL · 3D 成片 · {playing.id}</span>
                <h3 className="v20-modal-title">{playing.label}</h3>
              </div>
              <button type="button" className="v18-voice-close" onClick={() => setPlaying(null)} aria-label="关闭">
                <IconClose size={16} />
              </button>
            </header>
            <video
              className="v20-video"
              src={mediaSrc(playing.final.url || `/media/templates_3d/${playing.id}/final.mp4`)}
              controls
              autoPlay
              playsInline
              preload="metadata"
            />
            <footer className="v20-modal-foot">
              <span>{fmtDuration(playing.final.duration)} · {fmtSize(playing.final.size)} · 1280×720 / 25fps</span>
              <span style={{ marginLeft: "auto" }}>镜头 {playing.clips.done}/{playing.clips.total || "—"}</span>
            </footer>
          </div>
        </div>
      )}

      {seriesPlaying && (() => {
        const item = seriesPlaying.series;
        const index = Math.min(seriesPlaying.index, item.members.length - 1);
        const current = item.members[index];
        const nextIndex = item.members.findIndex((member, i) => i > index && member.ready);
        return (
          <div className="v20-modal-mask" onMouseDown={() => setSeriesPlaying(null)} role="dialog" aria-modal="true" aria-label={item.label}>
            <div className="v20-modal v20-series-modal" onMouseDown={(event) => event.stopPropagation()}>
              <header className="v20-modal-head">
                <div>
                  <span className="v20-modal-kicker">SERIES · {item.label} · {index + 1}/{item.members.length}</span>
                  <h3 className="v20-modal-title">{current?.label || item.label}</h3>
                </div>
                <button type="button" className="v18-voice-close" onClick={() => setSeriesPlaying(null)} aria-label="关闭">
                  <IconClose size={16} />
                </button>
              </header>
              {current?.final_url ? (
                <video
                  key={current.template}
                  className="v20-video"
                  src={mediaSrc(current.final_url)}
                  controls
                  autoPlay
                  playsInline
                  preload="metadata"
                  onEnded={() => {
                    if (nextIndex >= 0) setSeriesPlaying({ series: item, index: nextIndex });
                  }}
                />
              ) : (
                <div className="v18-empty"><span>这一集还没生成成片</span></div>
              )}
              <footer className="v20-modal-foot v20-series-foot">
                <div className="v20-series-queue">
                  {item.members.map((member, i) => (
                    <button
                      key={member.template}
                      type="button"
                      className={"v20-series-item" + (i === index ? " is-active" : "")}
                      onClick={() => setSeriesPlaying({ series: item, index: i })}
                      disabled={!member.ready}
                    >
                      <span className="v20-series-index">{i + 1}</span>
                      <span>{member.label}</span>
                      {member.duration ? <em>{fmtDuration(member.duration)}</em> : null}
                    </button>
                  ))}
                </div>
              </footer>
            </div>
          </div>
        );
      })()}
    </div>
  );
}


/* ------------------------------ V25：批量队列 item 行 ------------------------------ */
function BatchItemRow({ item, index, active }: { item: PipelineBatchItem; index: number; active: boolean }) {
  const pct = item.progress && item.progress.total > 0 ? Math.min(100, Math.round((item.progress.done / item.progress.total) * 100)) : item.status === "done" ? 100 : 0;
  const statusText =
    item.status === "queued"
      ? "排队中"
      : item.status === "running"
        ? "生成中"
        : item.status === "done"
          ? "已完成"
          : item.status === "failed"
            ? "失败"
            : item.status === "canceled"
              ? "已取消"
              : "已跳过";
  return (
    <div className={"v25-batch-item is-" + item.status + (active ? " is-active" : "")}>
      <span className="v25-batch-no">{String(index + 1).padStart(2, "0")}</span>
      <div className="v25-batch-item-main">
        <div className="v25-batch-item-title">
          <b>{item.label || item.template_id}</b>
          <span className="cm-mono">{item.template_id}</span>
        </div>
        <div className="v25-batch-item-meta">
          <span className={"v18-tag" + (item.status === "done" ? " is-jade" : item.status === "failed" ? " is-red" : item.status === "running" ? " is-gold" : "")}>{statusText}</span>
          {item.job_id && <span className="cm-mono">job {item.job_id.slice(0, 10)}</span>}
          {item.status === "running" && (item.stage_label || item.stage) && <span>{item.stage_label || item.stage}</span>}
          {item.status === "running" && item.progress && item.progress.total > 0 && (
            <span className="cm-mono">{item.progress.done}/{item.progress.total} 镜</span>
          )}
          {item.status === "running" && <span className="v25-batch-pct">{pct}%</span>}
          {item.status === "done" && item.final_url && (
            <a className="cm-btn cm-btn-sm cm-btn-ghost" href="#/library/final">
              <IconVideo size={11} /> 成片
            </a>
          )}
          {item.error && <span className="v25-batch-item-err">{item.error}</span>}
        </div>
        {item.status === "running" && <div className="cm-progress v25-batch-bar"><i style={{ width: Math.max(pct, 3) + "%" }} /></div>}
      </div>
    </div>
  );
}
