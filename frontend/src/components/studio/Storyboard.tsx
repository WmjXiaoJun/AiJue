// Project Studio · 分镜台（#/episodes/<eid>/storyboard）
// 52 镜表格（搜索/状态筛选/排序）+ 镜头抽屉（改台词/时长/视觉/花字、单镜生成、动态生成、版本选择、删除）。
// 数据：GET /episodes/{id}/shots、GET /jobs?episode_id、GET /shots/{sid}/versions；
//       PATCH /shots/{sid}、POST /shots/{sid}/generate|generate-dynamic、POST /shots/{sid}/versions/{vid}/select、
//       DELETE /shots/{sid}、POST /episodes/{id}/storyboard/generate、POST /episodes/{id}/shots/generate-dynamic。
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Episode, Job, Shot, ShotVersion } from "../../types";
import type { StudioTabProps } from "../../lib/studio";
import {
  SHOT_STATUS_TEXT,
  errorText,
  fmtClock,
  isActiveJob,
  mediaUrl,
  studioApi,
  toneOfStatus,
  waitJob,
} from "../../lib/studio";
import { SHOT_TYPE_LABEL, VERSION_STATUS_LABEL, fmtTime, segmentName, versionKindLabel } from "../../lib/format";
import { useToast } from "../Toast";
import { Spinner } from "../ui";
import {
  IconAlert,
  IconCheck,
  IconClose,
  IconDownload,
  IconEdit,
  IconPlay,
  IconRefresh,
  IconSearch,
  IconSpark,
  IconTrash,
  IconVideo,
} from "../icons";
import "../../styles/studio-b.css";

type BoardFilter = "all" | "pending" | "active" | "done" | "failed";
type BoardSort = "no" | "duration";

const FILTERS: { key: BoardFilter; label: string }[] = [
  { key: "all", label: "全部" },
  { key: "pending", label: "待生成" },
  { key: "active", label: "生成中" },
  { key: "done", label: "已完成" },
  { key: "failed", label: "失败" },
];

const STATUS_TEXT: Record<string, string> = {
  ...SHOT_STATUS_TEXT,
  ready: "已完成",
  done: "已完成",
  preview: "预览",
  static: "静态",
};

const TONE_CLASS: Record<string, string> = {
  gold: "is-gold",
  jade: "is-jade",
  red: "is-red",
  slate: "is-slate",
};

function shotNo(no: number | null | undefined): string {
  return "#" + String(Math.max(0, Math.floor(Number(no) || 0))).padStart(3, "0");
}

function activeStatus(status: string): boolean {
  const s = String(status || "").toLowerCase();
  return s === "queued" || s === "generating" || s === "rendering" || s === "running";
}

function bucketOf(shot: Shot): Exclude<BoardFilter, "all"> {
  const s = String(shot.status || "").toLowerCase();
  if (s === "failed" || s === "error") return "failed";
  if (activeStatus(s)) return "active";
  if (s === "ready" || s === "done" || s === "success") return "done";
  return "pending";
}

function statusTagClass(status: string): string {
  return "st-tag " + (TONE_CLASS[toneOfStatus(status)] || "is-slate");
}

function durationText(seconds: number | null | undefined): string {
  const value = Number(seconds) || 0;
  return value > 0 ? value.toFixed(1) + "s" : "—";
}

export function StudioStoryboard({ episode, onEpisode, onNavigate }: StudioTabProps) {
  const notify = useToast();

  const [shots, setShots] = useState<Shot[] | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [refreshing, setRefreshing] = useState(false);
  const [busy, setBusy] = useState<null | "storyboard" | "dynamic">(null);
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState<BoardFilter>("all");
  const [sort, setSort] = useState<BoardSort>("no");
  const [openId, setOpenId] = useState<string | null>(null);
  const [rowBusy, setRowBusy] = useState<string | null>(null);

  const aliveRef = useRef(true);
  const inFlightRef = useRef(false);

  useEffect(() => {
    aliveRef.current = true;
    return () => {
      aliveRef.current = false;
    };
  }, []);

  const load = useCallback(
    async (silent: boolean) => {
      if (inFlightRef.current) return;
      inFlightRef.current = true;
      if (!silent) setLoading(true);
      try {
        const [list, jobList] = await Promise.all([
          studioApi.shots(episode.id),
          studioApi.jobs({ episode_id: episode.id }).catch(() => [] as Job[]),
        ]);
        if (!aliveRef.current) return;
        setShots(list);
        setJobs(jobList);
        setError("");
      } catch (err) {
        if (!aliveRef.current) return;
        setError(errorText(err));
      } finally {
        inFlightRef.current = false;
        if (aliveRef.current) setLoading(false);
      }
    },
    [episode.id],
  );

  useEffect(() => {
    setShots(null);
    setOpenId(null);
    void load(false);
  }, [load]);

  const activeJobs = useMemo(() => jobs.filter(isActiveJob), [jobs]);

  // 有生成任务在跑时静默轮询（状态/版本会自己变化）
  useEffect(() => {
    if (!activeJobs.length) return;
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") void load(true);
    }, 2500);
    return () => window.clearInterval(timer);
  }, [activeJobs.length, load]);

  const refresh = useCallback(async () => {
    setRefreshing(true);
    try {
      await load(true);
      notify("已重新加载镜头", "success");
    } finally {
      if (aliveRef.current) setRefreshing(false);
    }
  }, [load, notify]);

  const list = shots ?? [];
  const total = list.length;
  const doneCount = useMemo(() => list.filter((shot) => bucketOf(shot) === "done").length, [list]);
  const totalDuration = useMemo(() => list.reduce((sum, shot) => sum + (Number(shot.duration) || 0), 0), [list]);
  const avgDuration = total > 0 ? totalDuration / total : 0;
  const progressPct = total > 0 ? Math.round((doneCount / total) * 100) : 0;

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    const result = list.filter((shot) => {
      if (filter !== "all" && bucketOf(shot) !== filter) return false;
      if (!q) return true;
      const haystack = [
        shotNo(shot.no),
        String(shot.no ?? ""),
        shot.id,
        shot.type,
        SHOT_TYPE_LABEL[shot.type] || "",
        shot.line,
        shot.visual,
        shot.punchline,
        shot.speaker_name,
        segmentName(shot.segment_id || shot.segment),
      ]
        .filter((part): part is string => typeof part === "string" && part.length > 0)
        .join(" ")
        .toLowerCase();
      return haystack.includes(q);
    });
    return result.slice().sort((a, b) => {
      if (sort === "duration") return (Number(b.duration) || 0) - (Number(a.duration) || 0);
      return (Number(a.no) || 0) - (Number(b.no) || 0);
    });
  }, [filter, list, search, sort]);

  const selected = useMemo(() => (openId ? list.find((shot) => shot.id === openId) || null : null), [list, openId]);

  const handleGenerateStoryboard = useCallback(async () => {
    if (busy) return;
    const confirmed = window.confirm(
      `将按当前台本重新生成全部分镜：覆盖现有 ${total > 0 ? total + " 个镜头" : "镜头列表"}（含已生成版本与选用状态）。继续？`,
    );
    if (!confirmed) return;
    setBusy("storyboard");
    try {
      const result = await studioApi.generateStoryboard(episode.id, true);
      const [ep, nextShots] = await Promise.all([studioApi.episode(episode.id), studioApi.shots(episode.id)]);
      if (!aliveRef.current) return;
      onEpisode(ep);
      setShots(nextShots);
      setOpenId(null);
      const created = result && typeof result.created === "number" ? ` · 新建 ${result.created} 镜` : "";
      notify(`分镜已重新生成（共 ${nextShots.length} 镜${created}）`, "success");
    } catch (err) {
      notify("生成分镜失败：" + errorText(err), "error");
    } finally {
      if (aliveRef.current) setBusy(null);
    }
  }, [busy, episode.id, notify, onEpisode, total]);

  const handleBatchDynamic = useCallback(async () => {
    if (busy) return;
    const confirmed = window.confirm(
      "将为全部镜头提交「动态生成」（H3 逐镜排队，耗时较长，会产生费用）。提交后跳到生成台查看进度。继续？",
    );
    if (!confirmed) return;
    setBusy("dynamic");
    try {
      const result = await studioApi.generateAllDynamic(episode.id);
      const count = Array.isArray(result) ? result.length : null;
      notify(`已提交批量动态生成任务${count !== null ? " · " + count + " 个" : ""}，去生成台查看进度`, "success");
      onNavigate("generate");
    } catch (err) {
      notify("批量动态生成失败：" + errorText(err), "error");
    } finally {
      if (aliveRef.current) setBusy(null);
    }
  }, [busy, episode.id, notify, onNavigate]);

  const handleRowGenerate = useCallback(
    async (shot: Shot) => {
      if (rowBusy) return;
      setRowBusy(shot.id);
      try {
        await studioApi.generateShot(shot.id);
        notify(`已提交 ${shotNo(shot.no)} 的生成任务`, "success");
        void load(true);
      } catch (err) {
        notify("提交生成失败：" + errorText(err), "error");
      } finally {
        if (aliveRef.current) setRowBusy(null);
      }
    },
    [load, notify, rowBusy],
  );

  const handlePatched = useCallback((next: Shot) => {
    setShots((prev) => (prev ? prev.map((shot) => (shot.id === next.id ? { ...shot, ...next } : shot)) : prev));
  }, []);

  const handleDeleted = useCallback(
    (id: string) => {
      setOpenId(null);
      setShots((prev) => (prev ? prev.filter((shot) => shot.id !== id) : prev));
      void studioApi
        .episode(episode.id)
        .then((ep) => aliveRef.current && onEpisode(ep))
        .catch(() => undefined);
      void load(true);
    },
    [episode.id, load, onEpisode],
  );

  const filteredDuration = filtered.reduce((sum, shot) => sum + (Number(shot.duration) || 0), 0);

  return (
    <div className="st-page st-board">
      <header className="st-page-head">
        <div className="st-head-main">
          <span className="st-kicker">STORYBOARD · 分镜台</span>
          <h1 className="st-h1">分镜台</h1>
          <p className="st-sub">
            {total > 0 ? (
              <>
                {total} 镜 · 总时长 {fmtClock(totalDuration)} · 已完成 {doneCount}/{total} · 平均镜长{" "}
                {(avgDuration || 0).toFixed(1)}s
              </>
            ) : (
              <>按台本拆分镜头、逐镜生成与选版；先保存台本再生成分镜</>
            )}
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
            title="重新加载镜头"
          >
            {refreshing || loading ? <Spinner size={13} /> : <IconRefresh />}
          </button>
          <button
            type="button"
            className="cm-btn cm-btn-sm"
            onClick={() => void handleGenerateStoryboard()}
            disabled={busy !== null}
          >
            {busy === "storyboard" ? <Spinner size={13} /> : <IconSpark size={14} />} 生成分镜
          </button>
          <button
            type="button"
            className="cm-btn cm-btn-sm"
            onClick={() => void handleBatchDynamic()}
            disabled={busy !== null}
          >
            {busy === "dynamic" ? <Spinner size={13} /> : <IconVideo size={14} />} 批量动态生成
          </button>
        </div>
      </header>

      <section className="st-kpis">
        <div className="st-kpi">
          <span className="st-kpi-label">Shots</span>
          <div className="st-kpi-value">
            {total}
            <small>镜</small>
          </div>
          <div className="st-kpi-hint">{episode.shots_total ? `期数记录 ${episode.shots_total} 镜` : "全部分镜"}</div>
        </div>
        <div className="st-kpi">
          <span className="st-kpi-label">Ready</span>
          <div className={"st-kpi-value" + (total > 0 && doneCount >= total ? " is-jade" : "")}>
            {doneCount}
            <small>/ {total || "—"}</small>
          </div>
          <div className="st-kpi-hint">{progressPct}% 已就绪</div>
        </div>
        <div className="st-kpi">
          <span className="st-kpi-label">Duration</span>
          <div className="st-kpi-value">
            {fmtClock(totalDuration)}
            <small>目标 {fmtClock(episode.target_duration)}</small>
          </div>
          <div className="st-kpi-hint">全部镜头时长合计</div>
        </div>
        <div className="st-kpi">
          <span className="st-kpi-label">Avg Shot</span>
          <div className="st-kpi-value">
            {(avgDuration || 0).toFixed(1)}
            <small>s</small>
          </div>
          <div className="st-kpi-hint">单镜平均时长</div>
        </div>
        <div className="st-kpi">
          <span className="st-kpi-label">Generating</span>
          <div className={"st-kpi-value" + (activeJobs.length > 0 ? " is-gold" : "")}>
            {activeJobs.length}
            <small>任务</small>
          </div>
          <div className="st-kpi-hint">{activeJobs.length > 0 ? "生成中 · 2.5s 自动刷新" : "当前空闲"}</div>
        </div>
      </section>

      <div className="st-board-progress">
        <div className="st-board-progress-head">
          <span className="cm-mono">
            {doneCount}/{total || "—"} 镜已完成
          </span>
          <b className="cm-mono">{progressPct}%</b>
        </div>
        <div className={"cm-progress" + (activeJobs.length > 0 ? " is-running" : "")}>
          <i style={{ width: `${Math.max(progressPct, total > 0 ? 2 : 0)}%` }} />
        </div>
      </div>

      <div className="st-toolbar">
        <label className="st-search">
          <IconSearch size={14} />
          <input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="搜索镜号 / 台词 / 视觉 / 说话人"
            aria-label="搜索镜头"
          />
          {search && (
            <button type="button" className="st-board-clear" onClick={() => setSearch("")} aria-label="清空搜索">
              <IconClose size={12} />
            </button>
          )}
        </label>
        <div className="st-seg" role="tablist" aria-label="状态筛选">
          {FILTERS.map((item) => (
            <button
              key={item.key}
              type="button"
              className={filter === item.key ? "is-active" : ""}
              onClick={() => setFilter(item.key)}
            >
              {item.label}
            </button>
          ))}
        </div>
        <div className="st-seg" role="tablist" aria-label="排序">
          <button type="button" className={sort === "no" ? "is-active" : ""} onClick={() => setSort("no")}>
            镜号
          </button>
          <button type="button" className={sort === "duration" ? "is-active" : ""} onClick={() => setSort("duration")}>
            时长
          </button>
        </div>
        <div className="st-toolbar-right">
          <span className="st-board-count cm-mono">
            {filtered.length} / {total || 0}
          </span>
        </div>
      </div>

      {loading && !shots ? (
        <div className="st-loading">
          <Spinner size={15} /> 正在读取镜头…
        </div>
      ) : error && !shots ? (
        <div className="st-error">
          <IconAlert size={16} />
          <span>镜头读取失败：{error}</span>
          <button type="button" className="cm-btn cm-btn-sm" onClick={() => void load(false)}>
            重试
          </button>
        </div>
      ) : total === 0 ? (
        <div className="st-panel">
          <div className="st-panel-body">
            <div className="st-empty st-board-empty">
              <IconVideo size={30} />
              <b>该期还没有分镜</b>
              <span>先在「编剧间」保存台本，然后点右上角「生成分镜」按台本拆分镜头。</span>
              <div className="st-board-empty-actions">
                <button type="button" className="cm-btn cm-btn-sm" onClick={() => onNavigate("script")}>
                  去编剧间
                </button>
                <button
                  type="button"
                  className="cm-btn cm-btn-primary cm-btn-sm"
                  onClick={() => void handleGenerateStoryboard()}
                  disabled={busy !== null}
                >
                  {busy === "storyboard" ? <Spinner size={13} /> : <IconSpark size={13} />} 生成分镜
                </button>
              </div>
            </div>
          </div>
        </div>
      ) : filtered.length === 0 ? (
        <div className="st-panel">
          <div className="st-panel-body">
            <div className="st-empty st-board-empty">
              <IconSearch size={26} />
              <b>没有符合条件的镜头</b>
              <span>换个关键词，或把状态筛选切回「全部」。</span>
            </div>
          </div>
        </div>
      ) : (
        <div className="st-panel">
          <div className="st-panel-head">
            <h3 className="st-panel-title">镜头表</h3>
            <p className="st-panel-sub">点击行打开镜头抽屉 · 可改台词/时长/视觉、生成、选版、删除</p>
            <div className="st-panel-right">
              <span className="st-tag is-slate cm-mono">{filtered.length} 行</span>
            </div>
          </div>
          <div className="st-panel-body is-flush">
            <div className="st-board-table-wrap">
              <table className="st-table st-board-table">
                <thead>
                  <tr>
                    <th style={{ width: 74 }}>镜号</th>
                    <th style={{ width: 108 }}>类型</th>
                    <th>台词 / 视觉</th>
                    <th style={{ width: 86 }}>时长</th>
                    <th style={{ width: 96 }}>状态</th>
                    <th className="st-board-col-ops" style={{ width: 138 }}>
                      操作
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {filtered.map((shot) => {
                    const bucket = bucketOf(shot);
                    const active = bucket === "active";
                    return (
                      <tr
                        key={shot.id}
                        className={openId === shot.id ? "is-selected" : ""}
                        onClick={() => setOpenId(shot.id)}
                      >
                        <td className="st-cell-main cm-mono">{shotNo(shot.no)}</td>
                        <td>
                          <span className="st-board-type">{SHOT_TYPE_LABEL[shot.type] || shot.type || "—"}</span>
                        </td>
                        <td>
                          <div className="st-board-line">
                            <span className={"st-board-line-main" + (shot.line ? "" : " is-visual")}>
                              {shot.line || shot.visual || "—"}
                            </span>
                            <span className="st-board-line-meta">
                              {shot.speaker_name || "画外"}
                              {shot.punchline ? " · 花字 " + shot.punchline : ""}
                              {segmentName(shot.segment_id || shot.segment)
                                ? " · " + segmentName(shot.segment_id || shot.segment)
                                : ""}
                            </span>
                          </div>
                        </td>
                        <td className="cm-mono">{durationText(shot.duration)}</td>
                        <td>
                          <span className={statusTagClass(shot.status)}>
                            {active && <i className="st-board-dot" />}
                            {STATUS_TEXT[shot.status] || shot.status || "待生成"}
                          </span>
                        </td>
                        <td className="st-cell-actions st-board-col-ops">
                          <div className="st-board-row-actions">
                            <button
                              type="button"
                              className="cm-btn cm-btn-ghost cm-btn-sm"
                              onClick={(event) => {
                                event.stopPropagation();
                                void handleRowGenerate(shot);
                              }}
                              disabled={rowBusy === shot.id || active}
                              title={active ? "生成中" : "提交单镜生成"}
                            >
                              {rowBusy === shot.id ? <Spinner size={12} /> : <IconSpark size={12} />} 生成
                            </button>
                            <button
                              type="button"
                              className="st-board-open"
                              onClick={(event) => {
                                event.stopPropagation();
                                setOpenId(shot.id);
                              }}
                              title="镜头详情"
                              aria-label={"打开 " + shotNo(shot.no)}
                            >
                              <IconEdit size={13} />
                            </button>
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <div className="st-board-foot">
              <span className="cm-mono">
                共 {filtered.length} 镜 · {fmtClock(filteredDuration)}
              </span>
              <span className="st-hint">数据源 GET /api/episodes/{episode.id}/shots</span>
            </div>
          </div>
        </div>
      )}

      {selected && (
        <ShotPanel
          shot={selected}
          episode={episode}
          onClose={() => setOpenId(null)}
          onPatched={handlePatched}
          onDeleted={handleDeleted}
          onRefresh={async () => {
            await load(true);
            try {
              const ep = await studioApi.episode(episode.id);
              if (aliveRef.current) onEpisode(ep);
            } catch {
              /* 静默 */
            }
          }}
        />
      )}
    </div>
  );
}

/* ------------------------------ 镜头抽屉 ------------------------------ */
function ShotPanel({
  shot,
  episode,
  onClose,
  onPatched,
  onDeleted,
  onRefresh,
}: {
  shot: Shot;
  episode: Episode;
  onClose: () => void;
  onPatched: (shot: Shot) => void;
  onDeleted: (id: string) => void;
  onRefresh: () => Promise<void>;
}) {
  const notify = useToast();

  const [line, setLine] = useState(shot.line || "");
  const [duration, setDuration] = useState(String(shot.duration ?? ""));
  const [visual, setVisual] = useState(shot.visual || "");
  const [punchline, setPunchline] = useState(shot.punchline || "");
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [busyJob, setBusyJob] = useState<null | "generate" | "dynamic">(null);
  const [jobProgress, setJobProgress] = useState<number | null>(null);
  const [jobMessage, setJobMessage] = useState("");
  const [jobError, setJobError] = useState("");

  const [versions, setVersions] = useState<ShotVersion[] | null>(null);
  const [versionsError, setVersionsError] = useState("");
  const [previewUrl, setPreviewUrl] = useState<string>("");
  const [selecting, setSelecting] = useState<string | null>(null);

  const aliveRef = useRef(true);

  useEffect(() => {
    aliveRef.current = true;
    return () => {
      aliveRef.current = false;
    };
  }, []);

  // 切换镜头时重置表单
  useEffect(() => {
    setLine(shot.line || "");
    setDuration(String(shot.duration ?? ""));
    setVisual(shot.visual || "");
    setPunchline(shot.punchline || "");
    setJobError("");
  }, [shot.id]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const loadVersions = useCallback(async () => {
    try {
      const list = await studioApi.versions(shot.id);
      if (!aliveRef.current) return;
      setVersions(list);
      setVersionsError("");
      const preferred = list.find((item) => item.id === shot.selected_version_id) || list[0];
      setPreviewUrl(preferred?.video_url ? mediaUrl(preferred.video_url) : "");
    } catch (err) {
      if (!aliveRef.current) return;
      setVersions([]);
      setVersionsError(errorText(err));
    }
  }, [shot.id, shot.selected_version_id]);

  useEffect(() => {
    setVersions(null);
    setVersionsError("");
    setPreviewUrl(
      shot.video_url ? mediaUrl(shot.video_url) : shot.h3_video_url ? mediaUrl(shot.h3_video_url) : "",
    );
    void loadVersions();
  }, [loadVersions, shot.h3_video_url, shot.video_url]);

  const dirty =
    (shot.line || "") !== line ||
    String(shot.duration ?? "") !== (duration === "" ? "" : String(Number(duration))) ||
    (shot.visual || "") !== visual ||
    (shot.punchline || "") !== punchline;

  const handleSave = async () => {
    const seconds = Number(duration);
    if (!Number.isFinite(seconds) || seconds <= 0) {
      notify("时长需为正数（秒）", "error");
      return;
    }
    setSaving(true);
    try {
      const next = await studioApi.patchShot(shot.id, {
        line,
        duration: Math.round(seconds * 100) / 100,
        visual,
        punchline,
      });
      if (!aliveRef.current) return;
      onPatched(next);
      setDuration(String(next.duration ?? seconds));
      notify(`${shotNo(shot.no)} 已保存`, "success");
    } catch (err) {
      notify("保存失败：" + errorText(err), "error");
    } finally {
      if (aliveRef.current) setSaving(false);
    }
  };

  const runJob = async (kind: "generate" | "dynamic", submit: () => Promise<Job>) => {
    if (busyJob) return;
    setBusyJob(kind);
    setJobProgress(0);
    setJobMessage("");
    setJobError("");
    try {
      const created = await submit();
      if (!created?.id) {
        notify("任务已提交，请在生成台查看进度", "success");
        await onRefresh();
        return;
      }
      const done = await waitJob(created.id, (tick) => {
        if (!aliveRef.current) return;
        setJobProgress(Math.max(0, Math.min(100, Number(tick.progress) || 0)));
        setJobMessage(tick.message || "");
      });
      if (!aliveRef.current) return;
      if (done.status === "done") {
        setJobProgress(100);
        notify(`${shotNo(shot.no)} ${kind === "generate" ? "镜头生成" : "动态生成"}完成`, "success");
        await onRefresh();
        await loadVersions();
      } else if (done.status === "failed" || done.status === "canceled") {
        const message = done.error || done.message || "任务失败";
        setJobError(message);
        notify(`${kind === "generate" ? "镜头生成" : "动态生成"}失败：${message}`, "error");
      } else {
        notify("任务状态未知，请到生成台确认", "info");
      }
    } catch (err) {
      const message = errorText(err);
      setJobError(message);
      notify("提交任务失败：" + message, "error");
    } finally {
      if (aliveRef.current) {
        setBusyJob(null);
        setJobProgress(null);
      }
    }
  };

  const handleGenerate = () => runJob("generate", () => studioApi.generateShot(shot.id));
  const handleDynamic = () => runJob("dynamic", () => studioApi.generateDynamicShot(shot.id));

  const handleSelectVersion = async (version: ShotVersion) => {
    setSelecting(version.id);
    try {
      const next = await studioApi.selectVersion(shot.id, version.id);
      if (!aliveRef.current) return;
      onPatched(next);
      notify(`已选用 V${version.version} 作为该镜头成片`, "success");
      await loadVersions();
    } catch (err) {
      notify("选版失败：" + errorText(err), "error");
    } finally {
      if (aliveRef.current) setSelecting(null);
    }
  };

  const handleDelete = async () => {
    const confirmed = window.confirm(
      `删除 ${shotNo(shot.no)}（${SHOT_TYPE_LABEL[shot.type] || shot.type}）？该操作会同时移除其生成版本，不可撤销。`,
    );
    if (!confirmed) return;
    setDeleting(true);
    try {
      await studioApi.deleteShot(shot.id);
      if (!aliveRef.current) return;
      notify(`${shotNo(shot.no)} 已删除`, "success");
      onDeleted(shot.id);
    } catch (err) {
      notify("删除失败：" + errorText(err), "error");
      if (aliveRef.current) setDeleting(false);
    }
  };

  const running = busyJob !== null;

  return (
    <>
      <div className="st-drawer-mask" onClick={onClose} />
      <aside className="st-drawer st-board-drawer" role="dialog" aria-modal="true" aria-label={"镜头 " + shotNo(shot.no)}>
        <header className="st-drawer-head">
          <div className="st-board-drawer-title">
            <h3 className="st-drawer-title">
              {shotNo(shot.no)} · {SHOT_TYPE_LABEL[shot.type] || shot.type || "镜头"}
            </h3>
            <span className="st-drawer-sub">
              {shot.id} · {segmentName(shot.segment_id || shot.segment) || "—"} · {durationText(shot.duration)} ·{" "}
              {episode.id}
            </span>
          </div>
          <span className={statusTagClass(shot.status)}>{STATUS_TEXT[shot.status] || shot.status || "待生成"}</span>
          <button type="button" className="st-drawer-close" onClick={onClose} aria-label="关闭">
            <IconClose size={16} />
          </button>
        </header>

        <div className="st-drawer-body">
          <section className="st-board-section">
            <div className="st-board-section-head">
              <span className="st-label">预览</span>
              {(shot.video_url || shot.h3_video_url) && (
                <a
                  className="st-board-link"
                  href={mediaUrl(shot.video_url || shot.h3_video_url || "")}
                  target="_blank"
                  rel="noreferrer"
                >
                  <IconDownload size={12} /> 打开原片
                </a>
              )}
            </div>
            {previewUrl ? (
              <video className="st-board-video" src={previewUrl} controls playsInline preload="metadata" key={previewUrl} />
            ) : (
              <div className="st-board-video-empty">
                <IconVideo size={22} />
                <span>该镜头暂无视频（生成后可预览；动态版本走 H3）</span>
              </div>
            )}
          </section>

          <section className="st-board-section">
            <div className="st-board-section-head">
              <span className="st-label">编辑镜头</span>
              <span className="st-hint">{dirty ? "有未保存修改" : "已同步"}</span>
            </div>
            <label className="st-field">
              <span className="st-label">台词</span>
              <textarea
                className="cm-textarea"
                rows={3}
                value={line}
                onChange={(event) => setLine(event.target.value)}
                placeholder="说话人台词（无台词留空）"
              />
            </label>
            <div className="st-board-field-row">
              <label className="st-field">
                <span className="st-label">时长（秒）</span>
                <input
                  className="cm-input"
                  type="number"
                  min={0.5}
                  step={0.5}
                  value={duration}
                  onChange={(event) => setDuration(event.target.value)}
                />
              </label>
              <label className="st-field">
                <span className="st-label">花字</span>
                <input
                  className="cm-input"
                  value={punchline}
                  onChange={(event) => setPunchline(event.target.value)}
                  placeholder="可留空"
                />
              </label>
            </div>
            <label className="st-field">
              <span className="st-label">画面描述</span>
              <textarea
                className="cm-textarea"
                rows={3}
                value={visual}
                onChange={(event) => setVisual(event.target.value)}
                placeholder="景别 / 构图 / 画面动作"
              />
            </label>
            <div className="st-board-inline">
              <button
                type="button"
                className="cm-btn cm-btn-primary cm-btn-sm"
                onClick={() => void handleSave()}
                disabled={saving || !dirty}
              >
                {saving ? <Spinner size={13} /> : <IconCheck size={13} />} 保存修改
              </button>
              <span className="st-hint">PATCH /api/shots/{shot.id}</span>
            </div>
          </section>

          <section className="st-board-section">
            <div className="st-board-section-head">
              <span className="st-label">生成</span>
              {running && <span className="st-tag is-gold">任务进行中</span>}
            </div>
            <div className="st-board-inline">
              <button type="button" className="cm-btn cm-btn-sm" onClick={() => void handleGenerate()} disabled={running}>
                {busyJob === "generate" ? <Spinner size={13} /> : <IconSpark size={13} />} 生成镜头
              </button>
              <button type="button" className="cm-btn cm-btn-sm" onClick={() => void handleDynamic()} disabled={running}>
                {busyJob === "dynamic" ? <Spinner size={13} /> : <IconVideo size={13} />} 动态生成
              </button>
              {running && <span className="st-hint">可切到生成台看实时日志</span>}
            </div>
            {jobProgress !== null && (
              <div className="st-board-job">
                <div className={"cm-progress" + (running ? " is-running" : "")}>
                  <i style={{ width: `${Math.max(jobProgress, 2)}%` }} />
                </div>
                <div className="st-board-job-meta">
                  <span className="cm-mono">{Math.round(jobProgress)}%</span>
                  <span className="st-board-job-msg">{jobMessage || "任务进行中…"}</span>
                </div>
              </div>
            )}
            {jobError && (
              <div className="st-error st-board-job-error">
                <IconAlert size={14} />
                <span>{jobError}</span>
              </div>
            )}
          </section>

          <section className="st-board-section">
            <div className="st-board-section-head">
              <span className="st-label">版本{versions ? " · " + versions.length : ""}</span>
              <button type="button" className="st-board-link" onClick={() => void loadVersions()}>
                <IconRefresh size={12} /> 刷新
              </button>
            </div>
            {versionsError && (
              <div className="st-error">
                <IconAlert size={14} />
                <span>版本读取失败：{versionsError}</span>
                <button type="button" className="cm-btn cm-btn-sm" onClick={() => void loadVersions()}>
                  重试
                </button>
              </div>
            )}
            {versions === null ? (
              <div className="st-loading">
                <Spinner size={14} /> 正在读取版本…
              </div>
            ) : versions.length === 0 ? (
              <div className="st-empty st-board-versions-empty">
                <IconPlay size={20} />
                <span>还没有生成版本：点上面的「生成镜头」或「动态生成」</span>
              </div>
            ) : (
              <div className="st-board-versions">
                {versions.map((version) => {
                  const isSelected = version.id === shot.selected_version_id;
                  return (
                    <article key={version.id} className={"st-board-version" + (isSelected ? " is-selected" : "")}>
                      <div className="st-board-version-main">
                        <span className="st-board-version-no cm-mono">V{version.version}</span>
                        <span className="st-tag is-slate">{versionKindLabel(version)}</span>
                        <span className={statusTagClass(version.status)}>
                          {VERSION_STATUS_LABEL[version.status] || version.status || "可用"}
                        </span>
                        {isSelected && <span className="st-tag is-jade">已选用</span>}
                        <span className="st-board-version-time cm-mono">{fmtTime(version.created_at)}</span>
                      </div>
                      <div className="st-board-version-actions">
                        {version.video_url && (
                          <button
                            type="button"
                            className="cm-btn cm-btn-ghost cm-btn-sm"
                            onClick={() => setPreviewUrl(mediaUrl(version.video_url || ""))}
                          >
                            <IconPlay size={12} /> 预览
                          </button>
                        )}
                        <button
                          type="button"
                          className="cm-btn cm-btn-ghost cm-btn-sm"
                          onClick={() => void handleSelectVersion(version)}
                          disabled={isSelected || selecting === version.id}
                        >
                          {selecting === version.id ? <Spinner size={12} /> : <IconCheck size={12} />}
                          {isSelected ? "已选用" : "选用"}
                        </button>
                      </div>
                    </article>
                  );
                })}
              </div>
            )}
          </section>

          <section className="st-board-section st-board-danger">
            <div className="st-board-section-head">
              <span className="st-label">危险区</span>
            </div>
            <button type="button" className="st-board-delete" onClick={() => void handleDelete()} disabled={deleting}>
              {deleting ? <Spinner size={13} /> : <IconTrash size={13} />} 删除该镜头
            </button>
            <p className="st-hint">会一并删除镜头记录与全部生成版本（含已选用的成片），不可恢复。</p>
          </section>
        </div>
      </aside>
    </>
  );
}
