// Project Studio · 成片房（#/episodes/<eid>/finish）
// 成片播放 + 一键渲染（waitJob 进度）+ 多语种出海 + 导出剪映草稿 + 海外发布（平台/隐私）+ 发布记录。
// 数据：GET /episodes/{id}/localizations|publishes|costs、GET /publish/config、GET /jobs?episode_id；
//       POST /episodes/{id}/render|export|localize|publish、POST /jobs/{id}/retry。
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { CostReport, Job, Localization, PublishConfig, PublishRecord } from "../../types";
import type { PublishPreflight, StudioTabProps } from "../../lib/studio";
import {
  EPISODE_STATUS_TEXT,
  JOB_STATUS_TEXT,
  JOB_TYPE_TEXT,
  errorText,
  fmtClock,
  fmtDateTime,
  fmtMoney,
  isActiveJob,
  mediaUrl,
  studioApi,
  toneOfStatus,
  waitJob,
} from "../../lib/studio";
import { useToast } from "../Toast";
import { Spinner } from "../ui";
import {
  IconAlert,
  IconCheck,
  IconCopy,
  IconDownload,
  IconExternal,
  IconFilm,
  IconGlobe,
  IconRefresh,
  IconSend,
  IconSpark,
} from "../icons";
import "../../styles/studio-b.css";

const LANGS = [
  { code: "en", label: "英语", native: "English" },
  { code: "es", label: "西语", native: "Español" },
  { code: "ja", label: "日语", native: "日本語" },
  { code: "ko", label: "韩语", native: "한국어" },
  { code: "pt", label: "葡语", native: "Português" },
];

const LANG_TEXT: Record<string, string> = {
  zh: "中文原版",
  en: "英语",
  es: "西语",
  ja: "日语",
  ko: "韩语",
  pt: "葡语",
};

const PLATFORMS = [
  { id: "youtube", label: "YouTube", hint: "长视频 · 标题 ≤100" },
  { id: "tiktok", label: "TikTok", hint: "单条 ≤10 分钟" },
  { id: "instagram", label: "Instagram", hint: "Reels 建议 ≤15 分钟" },
];

const PRIVACY_OPTIONS = [
  { value: "unlisted", label: "unlisted · 不公开（先审后公开）" },
  { value: "public", label: "public · 公开" },
  { value: "private", label: "private · 仅自己可见" },
];

const LOC_STATUS_TEXT: Record<string, string> = {
  draft: "草稿",
  queued: "排队中",
  running: "生成中",
  rendering: "生成中",
  ready: "已就绪",
  failed: "生成失败",
};

const PUBLISH_STATUS_TEXT: Record<string, string> = {
  queued: "排队中",
  running: "发布中",
  pending: "处理中",
  processing: "处理中",
  done: "已完成",
  completed: "已发布",
  published: "已发布",
  failed: "失败",
  dry_run: "预检",
};

const TONE_CLASS: Record<string, string> = {
  gold: "is-gold",
  jade: "is-jade",
  red: "is-red",
  slate: "is-slate",
};

function statusTagClass(status: string): string {
  return "st-tag " + (TONE_CLASS[toneOfStatus(status)] || "is-slate");
}

function platformLabel(id: string): string {
  return PLATFORMS.find((item) => item.id === id)?.label || id;
}

function langLabel(code: string | null | undefined): string {
  const key = String(code || "").toLowerCase();
  return LANG_TEXT[key] || (key ? key.toUpperCase() : "—");
}

function secondsText(value: unknown): string {
  const seconds = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(seconds) || seconds <= 0) return "—";
  return fmtClock(Math.round(seconds));
}

async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}

export function StudioFinishing({ episode, onEpisode, onNavigate }: StudioTabProps) {
  const notify = useToast();

  const [jobs, setJobs] = useState<Job[]>([]);
  const [localizations, setLocalizations] = useState<Localization[] | null>(null);
  const [locError, setLocError] = useState("");
  const [publishes, setPublishes] = useState<PublishRecord[] | null>(null);
  const [recordsError, setRecordsError] = useState("");
  const [config, setConfig] = useState<PublishConfig | null>(null);
  const [preflight, setPreflight] = useState<PublishPreflight | null>(null);
  const [preflightBusy, setPreflightBusy] = useState(false);
  const [preflightError, setPreflightError] = useState("");
  const [configError, setConfigError] = useState("");
  const [costs, setCosts] = useState<CostReport | null>(null);

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [refreshing, setRefreshing] = useState(false);

  const [duration, setDuration] = useState(0);
  const [renderBusy, setRenderBusy] = useState(false);
  const [renderProgress, setRenderProgress] = useState<number | null>(null);

  const [selectedLangs, setSelectedLangs] = useState<string[]>(["en"]);
  const [locBusy, setLocBusy] = useState(false);
  const [locProgress, setLocProgress] = useState<number | null>(null);

  const [exportBusy, setExportBusy] = useState(false);

  const [platforms, setPlatforms] = useState<string[]>(["youtube"]);
  const [privacy, setPrivacy] = useState("unlisted");
  const [publishTitle, setPublishTitle] = useState("");
  const [publishBusy, setPublishBusy] = useState(false);
  const [publishProgress, setPublishProgress] = useState<number | null>(null);
  const [publishError, setPublishError] = useState("");

  const aliveRef = useRef(true);
  const inFlightRef = useRef(false);
  const prevActiveRef = useRef(0);
  const progressMapRef = useRef<Record<string, number>>({});
  const platformsInitRef = useRef(false);

  useEffect(() => {
    aliveRef.current = true;
    return () => {
      aliveRef.current = false;
    };
  }, []);

  useEffect(() => {
    setPublishTitle(episode.title || "");
  }, [episode.id, episode.title]);

  const loadJobsOnly = useCallback(async () => {
    try {
      const list = await studioApi.jobs({ episode_id: episode.id });
      if (aliveRef.current) setJobs(list);
      return list;
    } catch {
      return null;
    }
  }, [episode.id]);

  const loadLocalizations = useCallback(async () => {
    try {
      const list = await studioApi.localizations(episode.id);
      if (!aliveRef.current) return;
      setLocalizations(list);
      setLocError("");
    } catch (err) {
      if (!aliveRef.current) return;
      setLocalizations((prev) => prev ?? []);
      setLocError(errorText(err));
    }
  }, [episode.id]);

  const loadRecords = useCallback(async () => {
    try {
      const list = await studioApi.publishes(episode.id);
      if (!aliveRef.current) return;
      setPublishes(list);
      setRecordsError("");
    } catch (err) {
      if (!aliveRef.current) return;
      setPublishes((prev) => prev ?? []);
      setRecordsError(errorText(err));
    }
  }, [episode.id]);

  const loadConfig = useCallback(async () => {
    try {
      const data = await studioApi.publishConfig();
      if (!aliveRef.current) return;
      setConfig(data);
      setConfigError("");
      if (!platformsInitRef.current && Array.isArray(data.platforms) && data.platforms.length > 0) {
        platformsInitRef.current = true;
        const available = data.platforms.filter((id) => PLATFORMS.some((item) => item.id === id));
        if (available.length > 0) setPlatforms(available);
      }
      if (data.youtube_privacy) setPrivacy(data.youtube_privacy);
    } catch (err) {
      if (!aliveRef.current) return;
      setConfig(null);
      setConfigError(errorText(err));
    }
  }, []);

  const loadAll = useCallback(
    async (silent: boolean) => {
      if (inFlightRef.current) return;
      inFlightRef.current = true;
      if (!silent) setLoading(true);
      try {
        const [jobList, cost] = await Promise.all([
          studioApi.jobs({ episode_id: episode.id }),
          studioApi.costs(episode.id).catch(() => null),
        ]);
        if (!aliveRef.current) return;
        setJobs(jobList);
        if (cost) setCosts(cost);
        setError("");
      } catch (err) {
        if (!aliveRef.current) return;
        setError(errorText(err));
      } finally {
        inFlightRef.current = false;
        if (aliveRef.current) setLoading(false);
      }
      await Promise.all([loadLocalizations(), loadRecords(), loadConfig()]);
    },
    [episode.id, loadConfig, loadLocalizations, loadRecords],
  );

  useEffect(() => {
    setLocalizations(null);
    setPublishes(null);
    setJobs([]);
    void loadAll(false);
  }, [loadAll]);

  const activeJobs = useMemo(() => jobs.filter(isActiveJob), [jobs]);
  const activeCount = activeJobs.length;

  // 有活动任务时 2s 轮询；任务全部结束后补刷一次多语种/发布记录
  useEffect(() => {
    if (!activeCount) {
      if (prevActiveRef.current > 0) {
        prevActiveRef.current = 0;
        void loadLocalizations();
        void loadRecords();
      }
      return;
    }
    prevActiveRef.current = activeCount;
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") void loadJobsOnly();
    }, 2000);
    return () => window.clearInterval(timer);
  }, [activeCount, loadJobsOnly, loadLocalizations, loadRecords]);

  const refresh = useCallback(async () => {
    setRefreshing(true);
    try {
      const ep = await studioApi.episode(episode.id);
      if (aliveRef.current) {
        onEpisode(ep);
        await loadAll(true);
      }
      notify("成片房数据已刷新", "success");
    } catch (err) {
      notify("刷新失败：" + errorText(err), "error");
    } finally {
      if (aliveRef.current) setRefreshing(false);
    }
  }, [episode.id, loadAll, notify, onEpisode]);

  const latestRender = useMemo(
    () =>
      jobs
        .filter((job) => job.type === "render")
        .slice()
        .sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)))[0] || null,
    [jobs],
  );
  const latestExport = useMemo(
    () =>
      jobs
        .filter((job) => job.type === "export")
        .slice()
        .sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)))[0] || null,
    [jobs],
  );
  const latestPublish = useMemo(
    () =>
      jobs
        .filter((job) => job.type === "publish")
        .slice()
        .sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)))[0] || null,
    [jobs],
  );
  const activeLocalizeJobs = useMemo(
    () => jobs.filter((job) => job.type === "localize" && isActiveJob(job)),
    [jobs],
  );

  const finalUrl = useMemo(() => {
    if (episode.render_url) return mediaUrl(episode.render_url);
    const doneRender = jobs.find((job) => job.type === "render" && job.status === "done" && job.output_url);
    return doneRender?.output_url ? mediaUrl(doneRender.output_url) : "";
  }, [episode.render_url, jobs]);

  const renderActive = Boolean(latestRender && isActiveJob(latestRender));
  const exportActive = Boolean(latestExport && isActiveJob(latestExport));
  const publishActive = Boolean(latestPublish && isActiveJob(latestPublish));
  const loadPreflight = useCallback(async () => {
    setPreflightBusy(true);
    try {
      setPreflight(await studioApi.publishPreflight(episode.id));
      setPreflightError("");
    } catch (err) {
      setPreflightError(errorText(err));
    } finally {
      setPreflightBusy(false);
    }
  }, [episode.id]);

  useEffect(() => {
    void loadPreflight();
  }, [loadPreflight]);

  const configured = Boolean(config?.configured && config?.enabled !== false);

  const readyLocs = useMemo(
    () => (localizations || []).filter((item) => item.status === "ready"),
    [localizations],
  );
  const readyLangSet = useMemo(() => new Set(readyLocs.map((item) => item.lang)), [readyLocs]);
  const costTotal = costs ? Number(costs.total) || 0 : Number(episode.cost) || 0;
  const activeLocalizeProgress = activeLocalizeJobs.length
    ? Math.round(
        activeLocalizeJobs.reduce((sum, job) => sum + (Number(job.progress) || 0), 0) / activeLocalizeJobs.length,
      )
    : 0;

  /* ---------------- 渲染 ---------------- */
  const handleRender = async () => {
    if (renderBusy || renderActive) return;
    const confirmed = window.confirm(
      `将把本集全部就绪镜头渲染为成片（${episode.shots_done || episode.shots_total || "全部"} 镜 → final.mp4，约 1–3 分钟）。继续？`,
    );
    if (!confirmed) return;
    setRenderBusy(true);
    setRenderProgress(0);
    try {
      const created = await studioApi.render(episode.id);
      notify("成片渲染任务已提交", "success");
      if (created?.id) {
        const done = await waitJob(created.id, (tick) => {
          if (aliveRef.current) setRenderProgress(Math.max(0, Math.min(100, Number(tick.progress) || 0)));
        });
        if (!aliveRef.current) return;
        if (done.status === "done") {
          setRenderProgress(100);
          const ep = await studioApi.episode(episode.id);
          if (aliveRef.current) {
            onEpisode(ep);
            notify("成片渲染完成，已刷新成片地址", "success");
          }
        } else if (done.status === "failed" || done.status === "canceled") {
          notify("成片渲染失败：" + (done.error || done.message || "未知原因"), "error");
        }
      }
      await loadJobsOnly();
    } catch (err) {
      notify("渲染提交失败：" + errorText(err), "error");
    } finally {
      if (aliveRef.current) {
        setRenderBusy(false);
        setRenderProgress(null);
      }
    }
  };

  /* ---------------- 多语种 ---------------- */
  const toggleLang = (code: string) => {
    setSelectedLangs((prev) => (prev.includes(code) ? prev.filter((item) => item !== code) : [...prev, code]));
  };

  const handleLocalize = async () => {
    if (locBusy) return;
    if (selectedLangs.length === 0) {
      notify("请至少选择一种语言", "info");
      return;
    }
    const names = selectedLangs.map((code) => langLabel(code)).join(" / ");
    const confirmed = window.confirm(
      `将为「${names}」生成多语种版本（复用画面，替换配音与字幕，约 1–2 分钟/语言，会产生费用）。继续？`,
    );
    if (!confirmed) return;
    setLocBusy(true);
    setLocProgress(0);
    progressMapRef.current = {};
    try {
      const created = await studioApi.localizeEpisode(episode.id, selectedLangs);
      if (!created.length) {
        notify("多语种任务已提交，可在生成台查看进度", "success");
        await loadJobsOnly();
        return;
      }
      created.forEach((job) => {
        if (job?.id) progressMapRef.current[job.id] = 0;
      });
      notify(`已提交 ${created.length} 个多语种任务`, "success");
      const results = await Promise.all(
        created.map((job) =>
          job?.id
            ? waitJob(job.id, (tick) => {
                if (!aliveRef.current) return;
                progressMapRef.current[job.id] = Math.max(0, Math.min(100, Number(tick.progress) || 0));
                const values = Object.values(progressMapRef.current);
                const total = values.reduce((sum, value) => sum + value, 0);
                setLocProgress(values.length ? Math.round(total / values.length) : 0);
              })
            : Promise.resolve(null),
        ),
      );
      if (!aliveRef.current) return;
      const ok = results.filter((job) => job && job.status === "done").length;
      const failed = results.filter((job) => job && (job.status === "failed" || job.status === "canceled")).length;
      setLocProgress(100);
      if (failed > 0) notify(`多语种完成 ${ok} 个，失败 ${failed} 个`, "error");
      else notify(`多语种版本已生成（${ok} 个）`, "success");
      await loadLocalizations();
      await loadJobsOnly();
    } catch (err) {
      notify("多语种提交失败：" + errorText(err), "error");
    } finally {
      if (aliveRef.current) {
        setLocBusy(false);
        setLocProgress(null);
      }
    }
  };

  /* ---------------- 导出 ---------------- */
  const handleExport = async () => {
    if (exportBusy || exportActive) return;
    setExportBusy(true);
    try {
      const job = await studioApi.exportEpisode(episode.id, "capcut");
      notify("剪映草稿导出任务已提交", "success");
      if (job?.id) {
        const done = await waitJob(job.id, () => undefined);
        if (done.status === "done") notify("剪映草稿已导出", "success");
        else if (done.status === "failed" || done.status === "canceled")
          notify("导出失败：" + (done.error || done.message || "未知原因"), "error");
      }
      await loadJobsOnly();
    } catch (err) {
      notify("导出提交失败：" + errorText(err), "error");
    } finally {
      if (aliveRef.current) setExportBusy(false);
    }
  };

  /* ---------------- 发布 ---------------- */
  const togglePlatform = (id: string) => {
    setPlatforms((prev) => (prev.includes(id) ? prev.filter((item) => item !== id) : [...prev, id]));
  };

  const handlePublish = async () => {
    if (publishBusy || publishActive) return;
    if (platforms.length === 0) {
      notify("请至少选择一个发布平台", "info");
      return;
    }
    const confirmed = window.confirm(
      `将把本集成片真实上传到 ${platforms.map(platformLabel).join(" / ")}（可见性 ${privacy}）。\n\n` +
        "请确认已获得平台账号授权并同意平台条款后再继续。",
    );
    if (!confirmed) return;
    setPublishBusy(true);
    setPublishProgress(0);
    setPublishError("");
    try {
      const job = await studioApi.publishEpisode(episode.id, {
        platforms,
        privacy,
        title: publishTitle.trim() || undefined,
      });
      notify("发布任务已提交", "success");
      if (job?.id) {
        const done = await waitJob(job.id, (tick) => {
          if (aliveRef.current) setPublishProgress(Math.max(0, Math.min(100, Number(tick.progress) || 0)));
        });
        if (!aliveRef.current) return;
        if (done.status === "done") {
          setPublishProgress(100);
          notify("发布完成", "success");
        } else if (done.status === "failed" || done.status === "canceled") {
          const message = done.error || done.message || "未知原因";
          setPublishError(message);
          notify("发布失败：" + message, "error");
        }
      }
      await Promise.all([loadRecords(), loadJobsOnly()]);
    } catch (err) {
      const message = errorText(err);
      setPublishError(message);
      notify("发布失败：" + message, "error");
    } finally {
      if (aliveRef.current) {
        setPublishBusy(false);
        setPublishProgress(null);
      }
    }
  };

  const copyPath = async (text: string, label: string) => {
    const ok = await copyText(text);
    notify(ok ? label + "已复制" : "复制失败，请手动选择", ok ? "success" : "error");
  };

  const renderDisplayProgress =
    renderProgress !== null
      ? renderProgress
      : latestRender
        ? Math.round(Number(latestRender.progress) || 0)
        : 0;
  const exportPath = latestExport?.status === "done" ? latestExport.output_url || latestExport.message || "" : "";

  return (
    <div className="st-page st-finish">
      <header className="st-page-head">
        <div className="st-head-main">
          <span className="st-kicker">FINISHING · 成片房</span>
          <h1 className="st-h1">成片房</h1>
          <p className="st-sub">
            {EPISODE_STATUS_TEXT[episode.status] || episode.status} · 目标 {fmtClock(episode.target_duration)} · 成片{" "}
            {duration > 0 ? fmtClock(duration) : "—"} · 多语版本 {readyLocs.length} · 发布记录{" "}
            {publishes ? publishes.length : "—"}
            {" "}
            <span className="st-board-ep cm-mono">{episode.id}</span>
          </p>
        </div>
        <div className="st-head-actions">
          <button
            type="button"
            className="cm-icon-btn"
            onClick={() => void refresh()}
            disabled={refreshing || loading}
            title="刷新成片房数据"
          >
            {refreshing || loading ? <Spinner size={13} /> : <IconRefresh />}
          </button>
          <button type="button" className="cm-btn cm-btn-sm" onClick={() => onNavigate("generate")}>
            <IconSpark size={14} /> 去生成台
          </button>
        </div>
      </header>

      {error && !loading && (
        <div className="st-error">
          <IconAlert size={16} />
          <span>成片房数据读取失败：{error}</span>
          <button type="button" className="cm-btn cm-btn-sm" onClick={() => void refresh()}>
            重试
          </button>
        </div>
      )}

      <div className="st-split">
        <div className="st-finish-main">
          {/* 成片母版 */}
          <section className="st-panel st-finish-player-panel">
            <div className="st-panel-head">
              <h3 className="st-panel-title">成片母版</h3>
              <p className="st-panel-sub">
                {episode.resolution || "1280x720"} · {episode.fps || 25}fps · {episode.aspect || "16:9"} · 目标{" "}
                {fmtClock(episode.target_duration)}
              </p>
              <div className="st-panel-right">
                <span className={statusTagClass(episode.status)}>
                  {EPISODE_STATUS_TEXT[episode.status] || episode.status}
                </span>
              </div>
            </div>
            <div className="st-panel-body">
              {finalUrl ? (
                <>
                  <div className="st-player st-finish-player">
                    <video
                      src={finalUrl}
                      controls
                      playsInline
                      preload="metadata"
                      onLoadedMetadata={(event) => setDuration(Number(event.currentTarget.duration) || 0)}
                    />
                  </div>
                  <div className="st-finish-meta">
                    <span>
                      成片时长 <b className="cm-mono">{duration > 0 ? fmtClock(duration) : "—"}</b>
                    </span>
                    <span>
                      目标 <b className="cm-mono">{fmtClock(episode.target_duration)}</b>
                    </span>
                    <span>
                      镜头 <b className="cm-mono">{episode.shots_done}/{episode.shots_total || "—"}</b>
                    </span>
                    <span className="st-finish-meta-path cm-mono" title={episode.render_url || ""}>
                      {episode.render_url || "—"}
                    </span>
                    <div className="st-finish-meta-actions">
                      <a className="cm-btn cm-btn-ghost cm-btn-sm" href={finalUrl} target="_blank" rel="noreferrer">
                        <IconExternal size={13} /> 新窗口
                      </a>
                      <a className="cm-btn cm-btn-ghost cm-btn-sm" href={finalUrl} download>
                        <IconDownload size={13} /> 下载
                      </a>
                    </div>
                  </div>
                </>
              ) : (
                <div className="st-finish-no-final">
                  <IconFilm size={34} />
                  <b>尚未渲染成片</b>
                  <span>把已就绪镜头合成为 final.mp4，完成后这里会自动出现播放器；渲染进度可在生成台追踪。</span>
                  <button
                    type="button"
                    className="cm-btn cm-btn-primary cm-btn-sm"
                    onClick={() => void handleRender()}
                    disabled={renderBusy || renderActive}
                  >
                    {renderBusy || renderActive ? <Spinner size={13} /> : <IconFilm size={13} />} 渲染成片
                  </button>
                </div>
              )}
            </div>
          </section>

          {/* 渲染成片 */}
          <section className="st-panel st-finish-render-panel">
            <div className="st-panel-head">
              <h3 className="st-panel-title">渲染成片</h3>
              <p className="st-panel-sub">串联 {episode.shots_total || "全部"} 镜 · 合成音轨 / 字幕 / 花字 / 片头片尾</p>
              <div className="st-panel-right">
                <button
                  type="button"
                  className="cm-btn cm-btn-primary cm-btn-sm"
                  onClick={() => void handleRender()}
                  disabled={renderBusy || renderActive}
                >
                  {renderBusy || renderActive ? <Spinner size={13} /> : <IconFilm size={13} />}
                  {renderBusy || renderActive ? "渲染中…" : finalUrl ? "重新渲染" : "渲染成片"}
                </button>
              </div>
            </div>
            <div className="st-panel-body">
              {latestRender ? (
                <div className="st-finish-job">
                  <div className="st-finish-job-head">
                    <span className={statusTagClass(latestRender.status)}>
                      {(isActiveJob(latestRender) && <i className="st-board-dot" />) || null}
                      {JOB_STATUS_TEXT[latestRender.status] || latestRender.status}
                    </span>
                    <span className="cm-mono">{latestRender.id}</span>
                    <span className="st-hint">
                      {JOB_TYPE_TEXT[latestRender.type] || latestRender.type} ·{" "}
                      {fmtDateTime(latestRender.updated_at || latestRender.created_at)}
                    </span>
                  </div>
                  <div className="st-finish-job-progress">
                    <div className={"cm-progress" + (isActiveJob(latestRender) ? " is-running" : "")}>
                      <i
                        style={{
                          width: `${Math.max(
                            isActiveJob(latestRender) ? renderDisplayProgress : Number(latestRender.progress) || 0,
                            latestRender.status === "failed" ? 2 : 0,
                          )}%`,
                        }}
                      />
                    </div>
                    <span className="cm-mono">
                      {Math.round(
                        isActiveJob(latestRender) ? renderDisplayProgress : Number(latestRender.progress) || 0,
                      )}
                      %
                    </span>
                  </div>
                  {latestRender.message && <p className="st-finish-job-msg">{latestRender.message}</p>}
                  {latestRender.status === "failed" && latestRender.error && (
                    <div className="st-error st-finish-job-error">
                      <IconAlert size={14} />
                      <span>{latestRender.error}</span>
                    </div>
                  )}
                </div>
              ) : (
                <p className="st-hint">还没有渲染任务：点右上角「渲染成片」，任务会提交到生成台并实时显示进度。</p>
              )}
            </div>
          </section>

          {/* 多语种 */}
          <section className="st-panel st-finish-loc-panel">
            <div className="st-panel-head">
              <h3 className="st-panel-title">多语种出海</h3>
              <p className="st-panel-sub">复用画面 · 替换配音与字幕（EN / ES / JA / KO / PT）</p>
              <div className="st-panel-right">
                <span className="st-tag is-slate cm-mono">
                  {readyLocs.length} / {LANGS.length} 已就绪
                </span>
              </div>
            </div>
            <div className="st-panel-body">
              {locError && (
                <div className="st-error st-finish-loc-error">
                  <IconAlert size={14} />
                  <span>多语种列表读取失败：{locError}</span>
                  <button type="button" className="cm-btn cm-btn-sm" onClick={() => void loadLocalizations()}>
                    重试
                  </button>
                </div>
              )}

              <div className="st-finish-langs">
                {LANGS.map((lang) => {
                  const active = selectedLangs.includes(lang.code);
                  const ready = readyLangSet.has(lang.code);
                  return (
                    <button
                      key={lang.code}
                      type="button"
                      className={"st-finish-lang" + (active ? " is-active" : "") + (ready ? " is-ready" : "")}
                      onClick={() => toggleLang(lang.code)}
                      disabled={locBusy}
                      aria-pressed={active}
                    >
                      <b>{lang.label}</b>
                      <em>{lang.native}</em>
                      {ready ? <span className="st-finish-lang-tag">已就绪</span> : <span className="st-finish-lang-tag">未生成</span>}
                    </button>
                  );
                })}
              </div>

              <div className="st-finish-loc-actions">
                <span className="st-hint">已选 {selectedLangs.length} 种语言 · 建议先渲染中文成片</span>
                <button
                  type="button"
                  className="cm-btn cm-btn-primary cm-btn-sm"
                  onClick={() => void handleLocalize()}
                  disabled={locBusy || selectedLangs.length === 0}
                >
                  {locBusy ? <Spinner size={13} /> : <IconGlobe size={13} />} 生成多语种版本
                </button>
              </div>

              {(locBusy || activeLocalizeJobs.length > 0) && (
                <div className="st-finish-job">
                  <div className="st-finish-job-head">
                    <span className="st-tag is-gold">
                      <i className="st-board-dot" /> 多语种生成中
                    </span>
                    <span className="st-hint">
                      {activeLocalizeJobs.length > 0 ? `${activeLocalizeJobs.length} 个任务` : `${selectedLangs.length} 个语言`}
                      {" · 2s 轮询"}
                    </span>
                  </div>
                  <div className="st-finish-job-progress">
                    <div className="cm-progress is-running">
                      <i style={{ width: `${Math.max(locProgress ?? activeLocalizeProgress, 2)}%` }} />
                    </div>
                    <span className="cm-mono">{Math.round(locProgress ?? activeLocalizeProgress)}%</span>
                  </div>
                </div>
              )}

              {localizations === null ? (
                <div className="st-loading">
                  <Spinner size={14} /> 正在读取多语种版本…
                </div>
              ) : localizations.length === 0 ? (
                <div className="st-empty st-finish-loc-empty">
                  <IconGlobe size={26} />
                  <b>还没有多语种版本</b>
                  <span>先渲染中文成片，再勾选目标语言生成配音 + 字幕版本。</span>
                </div>
              ) : (
                <div className="st-finish-loc-list">
                  {localizations.map((item) => {
                    const metaDuration = item.meta?.duration ?? item.meta?.duration_s;
                    return (
                      <article key={item.id} className="st-finish-loc-card">
                        <div className="st-finish-loc-head">
                          <span className="st-finish-loc-lang">
                            <b>{langLabel(item.lang)}</b>
                            <em className="cm-mono">{String(item.lang || "").toUpperCase()}</em>
                          </span>
                          <span className={statusTagClass(item.status)}>
                            {LOC_STATUS_TEXT[item.status] || item.status}
                          </span>
                        </div>
                        <div className="st-finish-loc-meta">
                          <span className="cm-mono">{secondsText(metaDuration)}</span>
                          <span>{fmtDateTime(item.created_at)}</span>
                          <span className="cm-mono">{item.id}</span>
                        </div>
                        {item.video_url ? (
                          <div className="st-finish-loc-actions-row">
                            <a className="cm-btn cm-btn-ghost cm-btn-sm" href={mediaUrl(item.video_url)} target="_blank" rel="noreferrer">
                              <IconExternal size={12} /> 播放
                            </a>
                            <a className="cm-btn cm-btn-ghost cm-btn-sm" href={mediaUrl(item.video_url)} download>
                              <IconDownload size={12} /> mp4
                            </a>
                            {item.srt_url && (
                              <a className="cm-btn cm-btn-ghost cm-btn-sm" href={mediaUrl(item.srt_url)} download>
                                <IconDownload size={12} /> srt
                              </a>
                            )}
                          </div>
                        ) : (
                          <p className="st-hint">该语言暂无视频产物（{LOC_STATUS_TEXT[item.status] || item.status}）</p>
                        )}
                      </article>
                    );
                  })}
                </div>
              )}
            </div>
          </section>
        </div>

        {/* 右侧：KPI + 导出与发布 */}
        <aside className="st-split-side st-finish-side">
          <section className="st-panel st-finish-kpi-panel">
            <div className="st-panel-head">
              <h3 className="st-panel-title">本集成片</h3>
              <p className="st-panel-sub">状态 / 时长 / 版本 / 记录 / 费用</p>
            </div>
            <div className="st-panel-body is-flush">
              <div className="st-kpis st-finish-kpis">
                <div className="st-kpi">
                  <span className="st-kpi-label">Status</span>
                  <div className="st-kpi-value st-finish-kpi-status">
                    <span className={statusTagClass(episode.status)}>
                      {EPISODE_STATUS_TEXT[episode.status] || episode.status}
                    </span>
                  </div>
                  <div className="st-kpi-hint">{episode.shots_done}/{episode.shots_total || "—"} 镜就绪</div>
                </div>
                <div className="st-kpi">
                  <span className="st-kpi-label">Master</span>
                  <div className="st-kpi-value">{duration > 0 ? fmtClock(duration) : "—"}</div>
                  <div className="st-kpi-hint">{finalUrl ? (duration > 0 ? "成片已就绪" : "成片已就绪 · 时长待读") : "尚未渲染"}</div>
                </div>
                <div className="st-kpi">
                  <span className="st-kpi-label">Localized</span>
                  <div className={"st-kpi-value" + (readyLocs.length > 0 ? " is-jade" : "")}>
                    {readyLocs.length}
                    <small>/ {LANGS.length}</small>
                  </div>
                  <div className="st-kpi-hint">多语种版本</div>
                </div>
                <div className="st-kpi">
                  <span className="st-kpi-label">Publishes</span>
                  <div className="st-kpi-value">{publishes ? publishes.length : "—"}</div>
                  <div className="st-kpi-hint">发布记录</div>
                </div>
                <div className="st-kpi">
                  <span className="st-kpi-label">Cost</span>
                  <div className="st-kpi-value is-gold">
                    {fmtMoney(costTotal)}
                    <small>{costs?.currency || "CNY"}</small>
                  </div>
                  <div className="st-kpi-hint">本集累计费用</div>
                </div>
              </div>
            </div>
          </section>

          <section className="st-panel st-finish-publish-panel">
            <div className="st-panel-head">
              <h3 className="st-panel-title">导出与发布</h3>
              <p className="st-panel-sub">剪映草稿 · 海外平台一键分发</p>
            </div>
            <div className="st-panel-body">
              <div className="st-finish-block">
                <div className="st-finish-block-head">
                  <span className="st-label">交付导出</span>
                  <button
                    type="button"
                    className="cm-btn cm-btn-sm"
                    onClick={() => void handleExport()}
                    disabled={exportBusy || exportActive}
                  >
                    {exportBusy || exportActive ? <Spinner size={12} /> : <IconDownload size={12} />}
                    {exportActive ? "导出中…" : "导出剪映草稿"}
                  </button>
                </div>
                <p className="st-hint">生成剪映专业版可导入的草稿（时间线 / 素材 / subtitles.srt / README）。</p>
                {latestExport && (
                  <div className="st-finish-export">
                    <div className="st-finish-job-head">
                      <span className={statusTagClass(latestExport.status)}>
                        {JOB_STATUS_TEXT[latestExport.status] || latestExport.status}
                      </span>
                      <span className="st-hint">{fmtDateTime(latestExport.updated_at || latestExport.created_at)}</span>
                    </div>
                    {(exportActive || exportBusy) && (
                      <div className="st-finish-job-progress">
                        <div className="cm-progress is-running">
                          <i style={{ width: `${Math.max(Number(latestExport.progress) || 0, 2)}%` }} />
                        </div>
                        <span className="cm-mono">{Math.round(Number(latestExport.progress) || 0)}%</span>
                      </div>
                    )}
                    {latestExport.status === "failed" && latestExport.error && (
                      <div className="st-error st-finish-job-error">
                        <IconAlert size={13} />
                        <span>{latestExport.error}</span>
                      </div>
                    )}
                    {exportPath && (
                      <div className="st-finish-export-path">
                        <code className="cm-mono" title={exportPath}>
                          {exportPath}
                        </code>
                        <button type="button" className="cm-btn cm-btn-ghost cm-btn-sm" onClick={() => void copyPath(exportPath, "导出路径")}>
                          <IconCopy size={12} /> 复制
                        </button>
                      </div>
                    )}
                  </div>
                )}
              </div>

              <hr className="st-finish-hair" />

              <div className="st-finish-block">
                <div className="st-finish-block-head">
                  <span className="st-label">海外发布</span>
                  <span className={"st-tag " + (configured ? "is-jade" : "is-gold")}>
                    {configured ? "Upload-Post 已连接" : "Upload-Post 未配置"}
                  </span>
                </div>
                <div className="st-pf">
                  <div className="st-pf-head">
                    <span className="st-pf-title">发布前检查</span>
                    {preflight && (
                      <span className={"st-tag " + (preflight.publishable ? "is-jade" : "is-gold")}>
                        {preflight.publishable ? "可以发布" : preflight.ok ? "配置未就绪" : "存在阻断项"}
                      </span>
                    )}
                    <button
                      type="button"
                      className="cm-btn cm-btn-ghost cm-btn-sm st-pf-btn"
                      onClick={() => void loadPreflight()}
                      disabled={preflightBusy}
                    >
                      {preflightBusy ? <Spinner size={12} /> : <IconRefresh size={12} />}
                      重新检查
                    </button>
                  </div>
                  {preflightError && <p className="st-hint is-error">检查失败：{preflightError}</p>}
                  {preflight && (
                    <ul className="st-pf-list">
                      {preflight.checks.map((item) => (
                        <li key={item.id} className={"st-pf-item is-" + item.status}>
                          <i className="st-pf-dot" />
                          <span className="st-pf-label">{item.label}</span>
                          <span className="st-pf-detail">{item.detail || "—"}</span>
                        </li>
                      ))}
                    </ul>
                  )}
                  {preflight && preflight.warnings && preflight.warnings.length > 0 && (
                    <p className="st-hint st-pf-warn">{preflight.warnings.join("；")}</p>
                  )}
                </div>
                {configError && <p className="st-hint is-error">发布配置读取失败：{configError}</p>}
                {config && !configured && (
                  <p className="st-hint">
                    发布按钮仍可提交：后端会返回可读错误（未配置 API Key / 平台未授权）。完成后可到设置里配置 Upload-Post。
                  </p>
                )}
                {config?.username_masked && <p className="st-hint">账号 @{config.username_masked}</p>}

                <div className="st-finish-platforms">
                  {PLATFORMS.map((platform) => {
                    const active = platforms.includes(platform.id);
                    return (
                      <button
                        key={platform.id}
                        type="button"
                        className={"st-finish-platform" + (active ? " is-active" : "")}
                        onClick={() => togglePlatform(platform.id)}
                        disabled={publishBusy || publishActive}
                        aria-pressed={active}
                      >
                        <span className="st-finish-platform-check">{active ? <IconCheck size={11} /> : null}</span>
                        <span className="st-finish-platform-main">
                          <b>{platform.label}</b>
                          <em>{platform.hint}</em>
                        </span>
                      </button>
                    );
                  })}
                </div>

                <div className="st-finish-publish-fields">
                  <label className="st-field">
                    <span className="st-label">YouTube 可见性</span>
                    <select className="cm-select" value={privacy} onChange={(event) => setPrivacy(event.target.value)}>
                      {PRIVACY_OPTIONS.map((option) => (
                        <option key={option.value} value={option.value}>
                          {option.label}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="st-field">
                    <span className="st-label">标题（{publishTitle.length}/100）</span>
                    <input
                      className="cm-input"
                      value={publishTitle}
                      maxLength={100}
                      onChange={(event) => setPublishTitle(event.target.value)}
                      placeholder="发布标题"
                    />
                  </label>
                </div>

                <div className="st-finish-publish-actions">
                  <span className="st-hint">
                    {finalUrl ? "以当前成片 final.mp4 发布" : "尚无成片，发布可能失败（先渲染）"}
                  </span>
                  <button
                    type="button"
                    className="cm-btn cm-btn-primary cm-btn-sm"
                    onClick={() => void handlePublish()}
                    disabled={publishBusy || publishActive || platforms.length === 0}
                  >
                    {publishBusy || publishActive ? <Spinner size={13} /> : <IconSend size={13} />}
                    {publishActive ? "发布中…" : "发布"}
                  </button>
                </div>

                {(publishProgress !== null || publishActive) && (
                  <div className="st-finish-job-progress">
                    <div className="cm-progress is-running">
                      <i style={{ width: `${Math.max(publishProgress ?? (publishActive ? Number(latestPublish?.progress) || 0 : 0), 2)}%` }} />
                    </div>
                    <span className="cm-mono">
                      {Math.round(publishProgress ?? (publishActive ? Number(latestPublish?.progress) || 0 : 0))}%
                    </span>
                  </div>
                )}

                {publishError && (
                  <div className="st-error st-finish-job-error">
                    <IconAlert size={13} />
                    <span>{publishError}</span>
                  </div>
                )}
              </div>

              <hr className="st-finish-hair" />

              <div className="st-finish-block">
                <div className="st-finish-block-head">
                  <span className="st-label">发布记录</span>
                  <button type="button" className="cm-btn cm-btn-ghost cm-btn-sm" onClick={() => void loadRecords()}>
                    <IconRefresh size={12} /> 刷新
                  </button>
                </div>
                {recordsError && <p className="st-hint is-error">发布记录读取失败：{recordsError}</p>}
                {publishes === null ? (
                  <div className="st-loading">
                    <Spinner size={13} /> 正在读取发布记录…
                  </div>
                ) : publishes.length === 0 ? (
                  <p className="st-hint">还没有发布记录：选好平台后点「发布」，记录会显示在这里。</p>
                ) : (
                  <div className="st-finish-records">
                    {publishes.map((record) => (
                      <article key={record.id} className="st-finish-record">
                        <div className="st-finish-record-head">
                          <span className="st-finish-record-platforms">
                            {record.platforms.map((platform) => (
                              <span key={platform} className="st-tag is-slate">
                                {platformLabel(platform)}
                              </span>
                            ))}
                          </span>
                          <span className={statusTagClass(record.status)}>
                            {PUBLISH_STATUS_TEXT[record.status] || record.status}
                          </span>
                        </div>
                        <div className="st-finish-record-title">{record.title || "—"}</div>
                        <div className="st-finish-record-meta">
                          <span>{langLabel(record.lang || "zh")}</span>
                          <span>{fmtDateTime(record.created_at)}</span>
                          {record.request_id && <span className="cm-mono">{record.request_id}</span>}
                        </div>
                        {record.error && (
                          <div className="st-error st-finish-job-error">
                            <IconAlert size={12} />
                            <span>{record.error}</span>
                          </div>
                        )}
                      </article>
                    ))}
                  </div>
                )}
              </div>
            </div>
          </section>
        </aside>
      </div>
    </div>
  );
}
