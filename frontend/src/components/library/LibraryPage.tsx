// V20 · 统一「模板与素材 / 成片库」（#/library/<templates|styles|scenes|final>）
// 模板：复用 cinema/Templates（embedded 模式，去页头/重复 CTA）；风格/场景：复用 v5world/Styles|Scenes；
// 成片库：19 支 3D 成片画廊（播放/报告/QC/重新合成/镜头台入口）。
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { errorText } from "../../lib/api";
import { mediaSrc } from "../../lib/v5";
import { ApiError } from "../../lib/api";
import type { CinemaTemplate } from "../../lib/cinema";
import { cinemaApi, fmtDuration, fmtSize, isRealTemplate } from "../../lib/cinema";
import { isBatchActive, isRunActive, pipelineApi, pipelineBatchApi } from "../../lib/pipeline";
import type { PipelineBatch, PipelineBatchItem } from "../../lib/pipeline";
import type { V5CatalogResponse } from "../../lib/v5";
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
import { V5Styles } from "../v5world/Styles";
import { V5Scenes } from "../v5world/Scenes";
import "../../styles/cinema.css";
import "../../styles/v18.css";
import "../../styles/library.css";

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

  useEffect(() => {
    if (!playing) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setPlaying(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [playing]);

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
      </div>

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
              <article key={template.id} className={"v20-final-card" + (ready ? " is-ready" : "") + (isRealTemplate(template.id) ? " is-real" : "")}>
                <button type="button" className="v20-final-poster" onClick={() => ready && setPlaying(template)} disabled={!ready} title={ready ? "播放 3D 成片" : "尚无成片"}>
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
                    <button type="button" className="cm-btn cm-btn-sm cm-btn-ghost" onClick={() => void reassemble(template)} disabled={assembling === template.id}>
                      {assembling === template.id ? <Spinner size={12} /> : <IconSpark size={12} />} 重新合成
                    </button>
                    <a className="cm-btn cm-btn-sm cm-btn-ghost" href="#/cinema/shots" title="进入 3D 制作台 · 镜头台">
                      <IconFilm size={12} /> 镜头台
                    </a>
                    <a className="cm-btn cm-btn-sm cm-btn-ghost" href={videoUrl} target="_blank" rel="noreferrer" title="打开原片">
                      <IconDownload size={12} />
                    </a>
                  </div>
                </div>
              </article>
            );
          })}
        </div>
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
