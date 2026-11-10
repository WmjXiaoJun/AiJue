// Cinema Studio · 镜头台（#/cinema/shots）：56 镜网格 / 搜索 / 来源筛选 / 批量重生成 / 单镜抽屉。
// 数据流：GET /api/cinema/shots → 本地筛选；PATCH brief 就地更新；POST /generate 后 5s 刷新。
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import "../../styles/cinema-shots.css";
import {
  SOURCE_HINT,
  SOURCE_LABEL,
  cinemaApi,
  fmtDuration,
  shotMediaUrl,
  type CinemaBrief,
  type CinemaShot,
  type CinemaShotsResponse,
  type ShotSource,
} from "../../lib/cinema";
import { errorText } from "../../lib/api";
import { useToast } from "../Toast";
import { Spinner } from "../ui";
import {
  IconAlert,
  IconCheck,
  IconClose,
  IconFilm,
  IconPlay,
  IconRefresh,
  IconSearch,
  IconSpark,
} from "../icons";
import { ShotDrawer } from "./ShotDrawer";

type SourceFilter = "all" | ShotSource;
type BatchProvider = "local" | "metaso";

const SOURCE_ORDER: ShotSource[] = ["cloud", "local", "reuse", "missing"];
const REFRESH_DELAY = 5000;

function padIndex(index: number): string {
  return String(Math.max(0, Math.floor(Number(index) || 0))).padStart(3, "0");
}

/** 卡片 prompt 摘要：优先 3D brief 的主体 + 运镜，缺省回落到台词/场景。 */
function promptSummary(shot: CinemaShot): string {
  const parts = [shot.brief?.subject, shot.brief?.camera].filter(
    (part): part is string => typeof part === "string" && part.trim().length > 0
  );
  return parts.length > 0 ? parts.join(" · ") : shot.line || "暂无 3D brief";
}

/** 搜索索引：镜号 / 台词 / 场景 / brief 全字段。 */
function searchIndex(shot: CinemaShot): string {
  return [
    "#" + padIndex(shot.index),
    shot.id,
    shot.line,
    shot.scene,
    shot.kind,
    shot.brief?.subject,
    shot.brief?.camera,
    shot.brief?.env,
    shot.brief?.ambience,
  ]
    .filter((part): part is string => typeof part === "string")
    .join(" ")
    .toLowerCase();
}

export function CinemaShots() {
  const notify = useToast();

  const [data, setData] = useState<CinemaShotsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<SourceFilter>("all");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [openId, setOpenId] = useState<string | null>(null);
  const [batchBusy, setBatchBusy] = useState<BatchProvider | null>(null);
  const [refreshHint, setRefreshHint] = useState(false);

  const aliveRef = useRef(true);
  const timerRef = useRef<number | null>(null);

  const clearTimer = useCallback(() => {
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  useEffect(() => {
    aliveRef.current = true;
    return () => {
      aliveRef.current = false;
      clearTimer();
    };
  }, [clearTimer]);

  const load = useCallback(async (mode: "initial" | "refresh") => {
    if (mode === "refresh") setRefreshing(true);
    else setLoading(true);
    setError(null);
    try {
      const res = await cinemaApi.shots();
      if (!aliveRef.current) return;
      setData(res);
      setRefreshHint(false);
      // 清理已不存在的选中项
      const ids = new Set(res.shots.map((shot) => shot.id));
      setSelected((prev) => {
        const next = new Set([...prev].filter((id) => ids.has(id)));
        return next.size === prev.size ? prev : next;
      });
      setOpenId((prev) => (prev && ids.has(prev) ? prev : null));
    } catch (err) {
      if (!aliveRef.current) return;
      setError(errorText(err));
    } finally {
      if (aliveRef.current) {
        setLoading(false);
        setRefreshing(false);
      }
    }
  }, []);

  useEffect(() => {
    void load("initial");
  }, [load]);

  /** 生成任务提交后：5 秒后静默刷新一次镜头列表 */
  const scheduleRefresh = useCallback(() => {
    clearTimer();
    setRefreshHint(true);
    timerRef.current = window.setTimeout(() => {
      timerRef.current = null;
      if (aliveRef.current) void load("refresh");
    }, REFRESH_DELAY);
  }, [clearTimer, load]);

  const shots = data?.shots ?? [];

  const ordered = useMemo(
    () => [...shots].sort((a, b) => (Number(a.index) || 0) - (Number(b.index) || 0)),
    [shots]
  );

  const counts = useMemo(() => {
    const next: Record<ShotSource, number> = { cloud: 0, local: 0, reuse: 0, missing: 0 };
    for (const shot of shots) {
      if (shot.source === "cloud") next.cloud += 1;
      else if (shot.source === "local") next.local += 1;
      else if (shot.source === "reuse") next.reuse += 1;
      else next.missing += 1;
    }
    return next;
  }, [shots]);

  const filtered = useMemo(() => {
    const tokens = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
    return ordered.filter((shot) => {
      if (filter !== "all" && shot.source !== filter) return false;
      if (tokens.length === 0) return true;
      const hay = searchIndex(shot);
      return tokens.every((token) => hay.includes(token));
    });
  }, [ordered, filter, query]);

  const selectedShots = useMemo(() => ordered.filter((shot) => selected.has(shot.id)), [ordered, selected]);

  const openShot = useMemo(() => (openId ? shots.find((shot) => shot.id === openId) ?? null : null), [shots, openId]);

  const toggleSelect = useCallback((shotId: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(shotId)) next.delete(shotId);
      else next.add(shotId);
      return next;
    });
  }, []);

  const selectAllFiltered = useCallback(() => {
    setSelected(new Set(filtered.map((shot) => shot.id)));
  }, [filtered]);

  const clearSelected = useCallback(() => setSelected(new Set()), []);

  const handleBatchGenerate = useCallback(
    async (provider: BatchProvider) => {
      const ids = selectedShots.map((shot) => shot.id);
      if (ids.length === 0 || batchBusy) return;
      const label = provider === "local" ? "本地 H3" : "云端 H3";
      const confirmed = window.confirm(
        `确认用${label}重生成选中的 ${ids.length} 个镜头？\n将强制覆盖已有素材，提交后可在「生成队列」查看进度。`
      );
      if (!confirmed) return;
      setBatchBusy(provider);
      try {
        const res = await cinemaApi.generate({ provider, shots: ids, force: true });
        notify(
          `已提交 ${ids.length} 个镜头的${label}任务${res.job_id ? `（任务 ${res.job_id}）` : ""}，到「生成队列」查看进度`,
          "success"
        );
        setSelected(new Set());
        scheduleRefresh();
      } catch (err) {
        notify(`${label}批量生成提交失败：${errorText(err)}`, "error");
      } finally {
        setBatchBusy(null);
      }
    },
    [selectedShots, batchBusy, notify, scheduleRefresh]
  );

  const handleSaved = useCallback((shotId: string, brief: CinemaBrief) => {
    setData((prev) =>
      prev
        ? {
            ...prev,
            shots: prev.shots.map((shot) => (shot.id === shotId ? { ...shot, brief } : shot)),
          }
        : prev
    );
  }, []);

  const resetFilters = useCallback(() => {
    setQuery("");
    setFilter("all");
  }, []);

  const total = data ? shots.length : null;
  const hasInitialError = !!error && !data;
  const hasInlineError = !!error && !!data;

  return (
    <div className="cm-page cm-shots">
      <header className="cm-page-head cm-shots-head">
        <div className="cm-head-main cm-shots-head-main">
          <div className="cm-kicker">SHOT WORKBENCH</div>
          <h1 className="cm-h1">镜头台</h1>
          <p className="cm-sub cm-shots-sub">
            <span className="cm-shots-sub-item">
              <b className="cm-mono">{total ?? "—"}</b> 镜
            </span>
            <span className="cm-shots-sub-item">
              总时长 <b className="cm-mono">{data ? fmtDuration(data.episode.total_duration) : "—"}</b>
            </span>
            <span className="cm-shots-sub-div" aria-hidden="true" />
            <span className="cm-shots-sub-item" title={SOURCE_HINT.cloud}>
              <i className="cm-shots-dot is-cloud" />
              云端 <b className="cm-mono">{total === null ? "—" : counts.cloud}</b>
            </span>
            <span className="cm-shots-sub-item" title={SOURCE_HINT.local}>
              <i className="cm-shots-dot is-local" />
              本地 <b className="cm-mono">{total === null ? "—" : counts.local}</b>
            </span>
            <span className="cm-shots-sub-item" title={SOURCE_HINT.reuse}>
              <i className="cm-shots-dot is-reuse" />
              素材复用 <b className="cm-mono">{total === null ? "—" : counts.reuse}</b>
            </span>
            <span className="cm-shots-sub-item" title={SOURCE_HINT.missing}>
              <i className="cm-shots-dot is-missing" />
              缺失 <b className="cm-mono">{total === null ? "—" : counts.missing}</b>
            </span>
          </p>
        </div>
        <div className="cm-head-actions cm-shots-head-actions">
          <button
            type="button"
            className="cm-btn cm-shots-btn"
            onClick={() => void load("refresh")}
            disabled={loading || refreshing}
            title="重新读取镜头清单"
          >
            {refreshing ? <Spinner size={14} /> : <IconRefresh size={15} />}
            刷新
          </button>
        </div>
      </header>

      <div className="cm-shots-toolbar">
        <div className="cm-shots-filter-row">
          <div className="cm-shots-search">
            <IconSearch size={15} />
            <input
              type="search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="搜索镜号 / 台词 / 场景 / prompt"
              aria-label="搜索镜头"
              spellCheck={false}
            />
            {query && (
              <button type="button" className="cm-shots-search-clear" onClick={() => setQuery("")} aria-label="清空搜索">
                <IconClose size={12} />
              </button>
            )}
          </div>

          <div className="cm-shots-chips" role="group" aria-label="来源筛选">
            <button
              type="button"
              className="cm-chip cm-shots-chip"
              aria-pressed={filter === "all"}
              onClick={() => setFilter("all")}
            >
              全部 <span className="cm-mono">{total ?? "—"}</span>
            </button>
            {SOURCE_ORDER.map((source) => (
              <button
                type="button"
                key={source}
                className="cm-chip cm-shots-chip"
                aria-pressed={filter === source}
                onClick={() => setFilter(source)}
                title={SOURCE_HINT[source]}
              >
                <i className={`cm-shots-dot is-${source}`} />
                {SOURCE_LABEL[source]} <span className="cm-mono">{total === null ? "—" : counts[source]}</span>
              </button>
            ))}
          </div>
        </div>

        <div className={"cm-shots-batch" + (selectedShots.length > 0 ? " is-active" : "")}>
          <div className="cm-shots-batch-info">
            <span className="cm-shots-selcount">
              {selectedShots.length > 0 ? `已选 ${selectedShots.length} 镜` : "未选中镜头"}
            </span>
            <span className="cm-shots-batch-hint">勾选卡片后可批量重生成，提交后到「生成队列」查看进度</span>
            <button
              type="button"
              className="cm-shots-link"
              onClick={selectAllFiltered}
              disabled={filtered.length === 0}
            >
              全选结果（{filtered.length}）
            </button>
            <button
              type="button"
              className="cm-shots-link"
              onClick={clearSelected}
              disabled={selectedShots.length === 0}
            >
              清空
            </button>
          </div>
          <div className="cm-shots-batch-actions">
            <button
              type="button"
              className="cm-btn cm-shots-btn"
              onClick={() => void handleBatchGenerate("local")}
              disabled={selectedShots.length === 0 || !!batchBusy}
            >
              {batchBusy === "local" ? <Spinner size={14} /> : <IconFilm size={15} />}
              本地 H3 重生成（{selectedShots.length}）
            </button>
            <button
              type="button"
              className="cm-btn cm-btn-primary cm-shots-btn"
              onClick={() => void handleBatchGenerate("metaso")}
              disabled={selectedShots.length === 0 || !!batchBusy}
            >
              {batchBusy === "metaso" ? <Spinner size={14} /> : <IconSpark size={15} />}
              云端 H3 重生成（{selectedShots.length}）
            </button>
          </div>
        </div>
      </div>

      {refreshHint && (
        <p className="cm-shots-refresh-hint">
          生成任务已提交 · 5 秒后自动刷新镜头列表，进度请到「生成队列」查看
        </p>
      )}

      {hasInitialError && (
        <div className="cm-error cm-shots-error">
          <IconAlert size={18} />
          <div className="cm-shots-error-main">
            <strong>镜头清单加载失败</strong>
            <p>{error}</p>
            <p>后端 /api/cinema/shots 暂不可用；可重试，或稍后刷新。</p>
          </div>
          <button type="button" className="cm-btn cm-shots-btn" onClick={() => void load("initial")}>
            <IconRefresh size={15} />
            重试
          </button>
        </div>
      )}

      {hasInlineError && (
        <div className="cm-error cm-shots-error is-inline">
          <IconAlert size={16} />
          <div className="cm-shots-error-main">
            <p>刷新失败：{error}（仍显示上次读取的镜头数据）</p>
          </div>
          <button type="button" className="cm-btn cm-shots-btn" onClick={() => void load("refresh")}>
            重试
          </button>
        </div>
      )}

      {loading && !data ? (
        <div className="cm-shots-loading">
          <div className="cm-loading">
            <Spinner size={18} />
            <span>正在读取镜头清单…</span>
          </div>
          <div className="cm-shots-skeletons" aria-hidden="true">
            {Array.from({ length: 10 }, (_, index) => (
              <div className="cm-shots-skel" key={index}>
                <div className="cm-shots-skel-thumb" />
                <div className="cm-shots-skel-body">
                  <div className="cm-shots-skel-line is-short" />
                  <div className="cm-shots-skel-line" />
                  <div className="cm-shots-skel-line is-mid" />
                </div>
              </div>
            ))}
          </div>
        </div>
      ) : null}

      {!hasInitialError && data && filtered.length === 0 ? (
        <div className="cm-empty cm-shots-empty">
          <div className="cm-shots-empty-title">没有匹配的镜头</div>
          <div className="cm-shots-empty-hint">
            {query ? `没有找到与「${query}」相关的镜头` : "当前来源筛选下没有镜头"}
          </div>
          <div className="cm-shots-empty-actions">
            <button type="button" className="cm-btn cm-shots-btn" onClick={resetFilters}>
              清除筛选
            </button>
          </div>
        </div>
      ) : null}

      {filtered.length > 0 && (
        <>
          <div className="cm-shots-grid">
            {filtered.map((shot) => (
              <ShotCard
                key={shot.id}
                shot={shot}
                selected={selected.has(shot.id)}
                onToggle={() => toggleSelect(shot.id)}
                onOpen={() => setOpenId(shot.id)}
              />
            ))}
          </div>
          <p className="cm-shots-foot">
            <span>
              显示 <span className="cm-mono">{filtered.length}</span> / {total ?? filtered.length} 镜
            </span>
            <span>点击卡片查看详情并编辑 brief · 勾选卡片可批量重生成</span>
          </p>
        </>
      )}

      {openShot && (
        <ShotDrawer
          key={openShot.id}
          shot={openShot}
          onClose={() => setOpenId(null)}
          onSaved={handleSaved}
          onRefresh={scheduleRefresh}
        />
      )}
    </div>
  );
}

interface ShotCardProps {
  shot: CinemaShot;
  selected: boolean;
  onToggle: () => void;
  onOpen: () => void;
}

function ShotCard({ shot, selected, onToggle, onOpen }: ShotCardProps) {
  const [hovered, setHovered] = useState(false);
  const [armed, setArmed] = useState(false);
  const [sheetFailed, setSheetFailed] = useState(false);
  const videoRef = useRef<HTMLVideoElement | null>(null);

  const pad = padIndex(shot.index);
  const media = shot.url ? shotMediaUrl(shot.url) : "";
  const sheet = shot.sheet_url ? shotMediaUrl(shot.sheet_url) : "";
  const line = shot.line?.trim();
  // 只有 ffprobe 校验通过的镜头才有可播放的独立 mp4（复用/缺失镜的 url 会 404）
  const hasPreview = shot.valid && media.length > 0;
  const showSheet = sheet.length > 0 && !sheetFailed;

  useEffect(() => {
    setSheetFailed(false);
  }, [sheet]);

  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    if (hovered) {
      try {
        video.currentTime = 0;
      } catch {
        /* 元数据未就绪时设置 currentTime 可能抛错，忽略 */
      }
      void video.play().catch(() => undefined);
    } else {
      video.pause();
    }
  }, [hovered, armed]);

  useEffect(() => {
    return () => {
      videoRef.current?.pause();
    };
  }, []);

  const onMouseEnter = () => {
    setHovered(true);
    setArmed(true);
  };

  return (
    <article
      className={"cm-shots-card" + (selected ? " is-selected" : "")}
      role="button"
      tabIndex={0}
      aria-label={`镜头 #${pad} 详情`}
      aria-pressed={selected}
      onClick={onOpen}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onOpen();
        }
      }}
      onMouseEnter={onMouseEnter}
      onMouseLeave={() => setHovered(false)}
      title={SOURCE_HINT[shot.source]}
    >
      <div className="cm-shots-thumb">
        {showSheet ? (
          <img
            src={sheet}
            alt={`镜头 #${pad} 缩略图`}
            loading="lazy"
            onError={() => setSheetFailed(true)}
          />
        ) : (
          <div className="cm-shots-thumb-fallback">
            <IconFilm size={20} />
            <span>{shot.source === "missing" ? "待生成" : "暂无缩略图"}</span>
          </div>
        )}
        {armed && hasPreview ? (
          <video
            ref={videoRef}
            className={"cm-shots-thumb-video" + (hovered ? " is-on" : "")}
            src={media}
            muted
            loop
            playsInline
            preload="none"
            aria-hidden="true"
          />
        ) : null}
        {hasPreview && (
          <span className="cm-shots-play" aria-hidden="true">
            <IconPlay size={14} />
          </span>
        )}
        {shot.scene && <span className="cm-shots-thumb-scene">{shot.scene}</span>}
        <label
          className="cm-shots-check"
          onClick={(event) => event.stopPropagation()}
          onKeyDown={(event) => event.stopPropagation()}
        >
          <input
            type="checkbox"
            checked={selected}
            onChange={onToggle}
            aria-label={`选择镜头 #${pad}`}
          />
        </label>
      </div>

      <div className="cm-shots-body">
        <div className="cm-shots-card-meta">
          <span className="cm-shots-index cm-mono">#{pad}</span>
          <span className="cm-shots-source">
            <i className={`cm-shots-dot is-${shot.source}`} />
            {SOURCE_LABEL[shot.source]}
          </span>
          <span className="cm-shots-duration cm-mono">{fmtDuration(shot.duration)}</span>
        </div>
        <p className={"cm-shots-line" + (line ? "" : " is-empty")}>{line || "（无台词）"}</p>
        <p className={"cm-shots-prompt" + (shot.brief?.subject ? "" : " is-empty")}>{promptSummary(shot)}</p>
      </div>
    </article>
  );
}
