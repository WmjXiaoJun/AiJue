import { useMemo, useState } from "react";
import { api, errorText } from "../../lib/api";
import type { Job, Shot } from "../../types";
import {
  JOB_STATUS_LABEL,
  JOB_TYPE_LABEL,
  SHOT_TYPE_LABEL,
  fmtMoney,
  fmtShotDuration,
  fmtTime,
} from "../../lib/format";
import { useToast } from "../Toast";
import type { TabBaseProps } from "./types";
import { Avatar, EmptyState, ProgressBar, SectionHead, Spinner, StatusBadge } from "../ui";
import { ShotVersions } from "../ShotVersions";
import {
  IconAlert,
  IconChevronRight,
  IconClock,
  IconFilm,
  IconJobs,
  IconRefresh,
  IconSpark,
  IconVideo,
} from "../icons";

interface Props extends TabBaseProps {
  jobs: Job[];
}

const STATUS_FILTERS = [
  { key: "all", label: "全部" },
  { key: "active", label: "进行中" },
  { key: "failed", label: "失败" },
  { key: "done", label: "已完成" },
];

function jobStatusActive(job: Job): boolean {
  return job.status === "queued" || job.status === "running";
}

export function Generation({ episode, assets, shots, jobs, onChanged, onOpenShot }: Props) {
  const notify = useToast();
  const [statusFilter, setStatusFilter] = useState("all");
  const [typeFilter, setTypeFilter] = useState("all");
  const [selectedShotId, setSelectedShotId] = useState<string | null>(null);
  const [retrying, setRetrying] = useState<string | null>(null);

  const shotMap = useMemo(() => new Map(shots.map((shot) => [shot.id, shot])), [shots]);
  const assetMap = useMemo(() => new Map(assets.map((asset) => [asset.id, asset])), [assets]);

  const filtered = useMemo(() => {
    const list = jobs.filter((job) => {
      if (typeFilter !== "all" && job.type !== typeFilter) return false;
      if (statusFilter === "active") return jobStatusActive(job);
      if (statusFilter === "failed") return job.status === "failed";
      if (statusFilter === "done") return job.status === "done";
      return true;
    });
    return list.slice().sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)));
  }, [jobs, statusFilter, typeFilter]);

  const selectedShot = selectedShotId ? shotMap.get(selectedShotId) || null : null;
  const selectedAsset = selectedShot?.speaker ? assetMap.get(selectedShot.speaker) : undefined;
  const activeCount = jobs.filter(jobStatusActive).length;
  const failedCount = jobs.filter((job) => job.status === "failed").length;
  const totalCost = jobs.reduce((sum, job) => sum + (job.cost || 0), 0);

  const handleRetry = async (job: Job) => {
    setRetrying(job.id);
    try {
      await api.retryJob(job.id);
      notify("已重新提交任务 " + job.id, "success");
      onChanged();
    } catch (err) {
      notify(errorText(err), "error");
    } finally {
      setRetrying(null);
    }
  };

  const renderJobTitle = (job: Job): string => {
    if (job.type === "render") return "成片渲染 · 第 " + String(episode.no || 1).padStart(3, "0") + " 期";
    const shot = job.shot_id ? shotMap.get(job.shot_id) : undefined;
    if (shot) {
      return "#" + String(shot.no).padStart(3, "0") + " · " + (SHOT_TYPE_LABEL[shot.type] || shot.type) + " · " + (shot.speaker_name || "画外");
    }
    return JOB_TYPE_LABEL[job.type] || job.type;
  };

  const readyShots = shots.filter((shot) => shot.status === "ready").slice(0, 8);

  return (
    <div className="generation-view">
      <div className="gen-main">
        <div className="gen-head">
          <SectionHead
            title="生成台"
            en="GENERATION"
            extra={
              <div className="gen-stats">
                <span>
                  <i className="live-dot online" /> {activeCount} 进行中
                </span>
                <span>{failedCount} 失败</span>
                <span>任务费用 {fmtMoney(totalCost)}</span>
              </div>
            }
          />
          <div className="gen-filters">
            <div className="filter-tabs">
              {STATUS_FILTERS.map((item) => (
                <button
                  key={item.key}
                  type="button"
                  className={"filter-tab" + (statusFilter === item.key ? " active" : "")}
                  onClick={() => setStatusFilter(item.key)}
                >
                  {item.label}
                </button>
              ))}
            </div>
            <select className="select" value={typeFilter} onChange={(event) => setTypeFilter(event.target.value)}>
              <option value="all">全部任务类型</option>
              <option value="shot">镜头生成</option>
              <option value="render">成片渲染</option>
            </select>
            <button type="button" className="btn btn-ghost btn-sm" onClick={onChanged} title="刷新任务">
              <IconRefresh size={14} /> 刷新
            </button>
          </div>
        </div>

        {filtered.length === 0 ? (
          <EmptyState
            icon={<IconJobs size={26} />}
            title="暂无任务"
            hint="到分镜台选择镜头批量生成，或单独生成某个镜头，任务会实时出现在这里。"
          />
        ) : (
          <div className="job-list">
            {filtered.map((job) => {
              const shot = job.shot_id ? shotMap.get(job.shot_id) : undefined;
              const asset = shot?.speaker ? assetMap.get(shot.speaker) : undefined;
              const selectable = !!shot;
              const isSelected = !!shot && shot.id === selectedShotId;
              return (
                <article
                  key={job.id}
                  className={"job-row" + (selectable ? " selectable" : "") + (isSelected ? " is-selected" : "")}
                  onClick={() => selectable && setSelectedShotId(shot!.id)}
                >
                  <span className={"job-icon job-icon-" + job.type}>
                    {job.type === "render" ? <IconFilm size={17} /> : <IconSpark size={17} />}
                  </span>
                  <div className="job-main">
                    <div className="job-title-row">
                      <span className="job-title">{renderJobTitle(job)}</span>
                      <StatusBadge status={job.status} kind="job" />
                      {job.status === "running" && <span className="job-live">RUNNING</span>}
                    </div>
                    <div className="job-sub">
                      <span>{JOB_TYPE_LABEL[job.type] || job.type}</span>
                      <i />
                      <span>{fmtTime(job.created_at)}</span>
                      <i />
                      <span>{fmtMoney(job.cost)}</span>
                      {shot && (
                        <>
                          <i />
                          <span>{fmtShotDuration(shot.duration)} · {shot.line || shot.visual || "画面镜头"}</span>
                        </>
                      )}
                    </div>
                    {jobStatusActive(job) && (
                      <ProgressBar value={job.progress || 0} label={Math.round(job.progress || 0) + "%"} />
                    )}
                    {job.error && job.status === "failed" && (
                      <div className="job-error">
                        <IconAlert size={13} /> {job.error}
                      </div>
                    )}
                    {job.message && job.status !== "failed" && <div className="job-message">{job.message}</div>}
                  </div>
                  <div className="job-actions">
                    {job.status === "failed" && (
                      <button
                        type="button"
                        className="btn btn-outline btn-sm"
                        disabled={retrying === job.id}
                        onClick={(event) => {
                          event.stopPropagation();
                          void handleRetry(job);
                        }}
                      >
                        {retrying === job.id ? <Spinner size={13} /> : <IconRefresh size={13} />} 重试
                      </button>
                    )}
                    {selectable && (
                      <span className="job-enter">
                        版本 <IconChevronRight size={14} />
                      </span>
                    )}
                  </div>
                </article>
              );
            })}
          </div>
        )}
      </div>

      <aside className="gen-side panel">
        {selectedShot ? (
          <div className="gen-side-inner">
            <div className="gen-side-head">
              <div>
                <span className="gen-side-kicker">镜头版本</span>
                <h3>
                  #{String(selectedShot.no).padStart(3, "0")} · {SHOT_TYPE_LABEL[selectedShot.type] || selectedShot.type}
                </h3>
              </div>
              <StatusBadge status={selectedShot.status} />
            </div>
            <div className="gen-side-shot">
              {selectedShot.speaker_name && (
                <span className="speaker-mini">
                  <Avatar name={selectedShot.speaker_name} color={selectedAsset?.accent_color} size={22} />
                  {selectedShot.speaker_name}
                </span>
              )}
              <p>{selectedShot.line || selectedShot.visual || "—"}</p>
              <div className="gen-side-shot-meta">
                <span><IconClock size={12} /> {fmtShotDuration(selectedShot.duration)}</span>
                <button type="button" className="link-btn" onClick={() => onOpenShot(selectedShot)}>
                  详情
                </button>
              </div>
            </div>
            <ShotVersions shot={selectedShot} onChanged={onChanged} />
          </div>
        ) : (
          <div className="gen-side-inner">
            <div className="gen-side-head">
              <div>
                <span className="gen-side-kicker">VERSIONS</span>
                <h3>镜头版本</h3>
              </div>
            </div>
            <p className="gen-side-hint">点击左侧任务行，查看该镜头的所有生成版本，并可选用任意一版。</p>
            {readyShots.length > 0 ? (
              <div className="ready-shots">
                <h4>最近就绪镜头</h4>
                {readyShots.map((shot) => (
                  <button key={shot.id} type="button" className="ready-shot" onClick={() => setSelectedShotId(shot.id)}>
                    <span className="ready-shot-no">#{String(shot.no).padStart(3, "0")}</span>
                    <span className="ready-shot-type">{SHOT_TYPE_LABEL[shot.type] || shot.type}</span>
                    <span className="ready-shot-line">{shot.line || shot.visual || "—"}</span>
                    <IconVideo size={14} />
                  </button>
                ))}
              </div>
            ) : (
              <EmptyState
                icon={<IconVideo size={22} />}
                title="还没有就绪镜头"
                hint="先在分镜台提交生成任务，完成后这里会列出可查看的镜头。"
              />
            )}
          </div>
        )}
      </aside>
    </div>
  );
}
