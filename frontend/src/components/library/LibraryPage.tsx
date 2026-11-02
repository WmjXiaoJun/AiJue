// V20 · 统一「模板与素材 / 成片库」（#/library/<templates|styles|scenes|final>）
// 模板：复用 cinema/Templates（embedded 模式，去页头/重复 CTA）；风格/场景：复用 v5world/Styles|Scenes；
// 成片库：19 支 3D 成片画廊（播放/报告/QC/重新合成/镜头台入口）。
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { errorText } from "../../lib/api";
import { mediaSrc } from "../../lib/v5";
import type { CinemaTemplate } from "../../lib/cinema";
import { cinemaApi, fmtDuration, fmtSize, isRealTemplate } from "../../lib/cinema";
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
  const [createdTemplate, setCreatedTemplate] = useState<string | null>(null);
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

  const refresh = useCallback(async () => {
    setRefreshing(true);
    await load(true);
    if (refreshing === false) notify("模板与素材数据已刷新", "success");
  }, [load, notify, refreshing]);

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

      {tab === "templates" && createdTemplate && (
        <div className="v21-created-banner">
          <IconCheck size={14} />
          <span>
            模板 <b className="cm-mono">{createdTemplate}</b> 已创建（pending）· 可去「一键成片」生成 3D
          </span>
          <a className="cm-btn cm-btn-primary cm-btn-sm" href="#/pipeline">
            <IconSpark size={12} /> 去一键成片
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
      {tab === "final" && <FinalLibrary templates={templates} loading={loading} onRefresh={() => void refresh()} />}

      {wizardOpen && (
        <CreateTemplateWizard
          open={wizardOpen}
          catalog={catalog}
          existingTemplates={catalog?.templates || []}
          onClose={() => setWizardOpen(false)}
          onCreated={(id) => {
            setWizardOpen(false);
            setTemplateVersion((prev) => prev + 1);
            setHighlightTemplate(id);
            setCreatedTemplate(id);
            notify("模板已创建；可去一键成片生成 3D", "success");
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
}: {
  templates: CinemaTemplate[];
  loading: boolean;
  onRefresh: () => void;
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
