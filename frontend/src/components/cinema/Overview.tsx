import { useEffect, useState } from "react";
import type { CinemaOverview, CinemaReport, CinemaTab } from "../../lib/cinema";
import { cinemaApi, fmtDuration, fmtSize, JOB_KIND_LABEL, JOB_STATUS_LABEL } from "../../lib/cinema";
import { mediaUrl } from "../../lib/api";
import { Spinner } from "../ui";

const HERO_FALLBACK = "/media/templates_v5/relic_talk_3d/frames_final/frame_07.png";

export function CinemaOverviewPage({
  overview,
  onNavigate,
}: {
  overview: CinemaOverview | null;
  onNavigate: (tab: CinemaTab) => void;
}) {
  const [report, setReport] = useState<CinemaReport | null>(null);

  useEffect(() => {
    let alive = true;
    cinemaApi
      .report()
      .then((r) => alive && setReport(r))
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, []);

  if (!overview) {
    return (
      <div className="cm-page">
        <div className="cm-loading">
          <Spinner size={16} /> 正在读取项目…
        </div>
      </div>
    );
  }

  const shotStats = overview.shots;
  const total = shotStats.total || 56;
  const final = overview.final;
  const qc = report?.qc;
  const heroImg = report?.frames?.length ? report.frames[Math.min(6, report.frames.length - 1)] : HERO_FALLBACK;

  const segments = [
    { key: "cloud", label: "云端 H3", value: shotStats.cloud, cls: "is-cloud" },
    { key: "local", label: "本地 H3", value: shotStats.local, cls: "is-local" },
    { key: "reuse", label: "素材复用", value: shotStats.reuse, cls: "is-reuse" },
    { key: "missing", label: "缺失", value: shotStats.missing, cls: "is-missing" },
  ];

  const steps = [
    {
      name: "素材盘点",
      desc: "56 镜时间线 / 每镜时长 / 台词 / 3D brief",
      value: `${total - (shotStats.missing || 0)} / ${total} 镜就绪`,
      done: (shotStats.missing || 0) === 0,
    },
    {
      name: "3D 镜头生成",
      desc: "云端 MetaSo MiniMax-H3 + 本地 ComfyUI H3",
      value: `云端 ${shotStats.cloud} · 本地 ${shotStats.local} · 复用 ${shotStats.reuse}`,
      done: (shotStats.missing || 0) === 0,
    },
    {
      name: "电影化剪辑",
      desc: "统一影调 / 字幕 / BGM / 环境声 / 转场",
      value: final.exists ? `${fmtDuration(final.duration)} · 25fps · 1280×720` : "尚未生成",
      done: final.exists,
    },
    {
      name: "QC 质检",
      desc: "黑帧 / 响度 / 字幕覆盖 / 时长偏差",
      value: qc ? `黑帧 ${qc.blackdetect ?? 0} 段 · ${qc.loudness?.integrated_lufs ?? "—"} LUFS` : "等待报告",
      done: !!qc,
    },
    {
      name: "交付与绑定",
      desc: "项目成片页 / 下载 / 发布材料",
      value: overview.project.render_url ? "已绑定项目成片页" : "待绑定",
      done: !!overview.project.render_url,
    },
  ];

  return (
    <div className="cm-page">
      <section className="cm-hero">
        <div className="cm-hero-copy">
          <span className="cm-hero-kicker">Now Screening · 文物纪录短片</span>
          <h2 className="cm-hero-title">
            国宝<em>会说话</em>
          </h2>
          <p className="cm-hero-desc">
            三件国宝，五千年间。越王勾践剑、千里江山图、曾侯乙编钟 —— 全部镜头由 MiniMax-H3
            生成 3D 电影画面，旁白与固定音色 TTS 驱动，本页可直接检片、改 prompt、重生成与重剪。
          </p>
          <div className="cm-hero-actions">
            <button type="button" className="cm-btn cm-btn-primary" onClick={() => onNavigate("final")}>
              <IconPlaySmall /> 播放成片 {final.exists ? fmtDuration(final.duration) : ""}
            </button>
            <button type="button" className="cm-btn" onClick={() => onNavigate("shots")}>
              进入镜头台
            </button>
            <button type="button" className="cm-btn cm-btn-ghost" onClick={() => onNavigate("assemble")}>
              重新剪辑
            </button>
          </div>
        </div>
        <div className="cm-hero-media">
          <img src={mediaUrl(heroImg)} alt="《国宝会说话》成片画面" />
          <button type="button" className="cm-hero-play" onClick={() => onNavigate("final")}>
            <i>▶</i> PLAY {final.exists ? fmtDuration(final.duration) : "5:25"}
          </button>
        </div>
      </section>

      <section className="cm-stat-strip">
        <div className="cm-stat-cell">
          <span className="cm-stat-label">Shots Ready</span>
          <div className="cm-stat-value">
            {total - (shotStats.missing || 0)}
            <small>/ {total} 镜</small>
          </div>
        </div>
        <div className="cm-stat-cell">
          <span className="cm-stat-label">Cloud H3</span>
          <div className="cm-stat-value is-gold">
            {shotStats.cloud}
            <small>云端电影镜</small>
          </div>
        </div>
        <div className="cm-stat-cell">
          <span className="cm-stat-label">Local H3</span>
          <div className="cm-stat-value is-jade">
            {shotStats.local}
            <small>本地补镜</small>
          </div>
        </div>
        <div className="cm-stat-cell">
          <span className="cm-stat-label">B-roll Reuse</span>
          <div className="cm-stat-value">
            {shotStats.reuse}
            <small>素材复用</small>
          </div>
        </div>
        <div className="cm-stat-cell">
          <span className="cm-stat-label">Master</span>
          <div className="cm-stat-value is-gold">
            {final.exists ? fmtDuration(final.duration) : "—"}
            <small>{final.exists ? fmtSize(final.size) : "未生成"}</small>
          </div>
        </div>
      </section>

      <section className="cm-ov-grid" style={{ marginBottom: 18 }}>
        <div className="cm-card cm-card-pad">
          <div className="cm-section-head">
            <span className="cm-kicker">Production Pipeline</span>
            <h3 className="cm-section-title">制片流水线</h3>
            <span className="cm-head-right cm-chip is-jade">5 / 5 完成</span>
          </div>
          <div className="cm-pipe">
            {steps.map((step, i) => (
              <div key={step.name} className={"cm-pipe-step" + (step.done ? " is-done" : "")}>
                <span className="cm-pipe-no">{String(i + 1).padStart(2, "0")}</span>
                <span className="cm-pipe-main">
                  <div className="cm-pipe-name">{step.name}</div>
                  <div className="cm-pipe-desc">{step.desc}</div>
                </span>
                <span className="cm-pipe-val">
                  <i className={"cm-status-dot " + (step.done ? "is-done" : "is-running")} />{" "}
                  <span style={{ marginLeft: 7 }}>{step.value}</span>
                </span>
              </div>
            ))}
          </div>
        </div>

        <div className="cm-card cm-card-pad">
          <div className="cm-section-head">
            <span className="cm-kicker">Shot Composition</span>
            <h3 className="cm-section-title">镜头构成</h3>
            <button type="button" className="cm-btn cm-btn-ghost cm-btn-sm cm-head-right" onClick={() => onNavigate("shots")}>
              查看镜头台 →
            </button>
          </div>
          <div className="cm-comp-bar">
            {segments.map((seg) =>
              seg.value > 0 ? (
                <i key={seg.key} className={seg.cls} style={{ width: `${(seg.value / total) * 100}%` }} />
              ) : null,
            )}
          </div>
          <div className="cm-legend">
            {segments.map((seg) => (
              <div key={seg.key} className="cm-legend-item">
                <span className={"cm-legend-dot " + seg.cls} style={{ background: legendColor(seg.key) }} />
                {seg.label}
                <b>{seg.value}</b>
              </div>
            ))}
          </div>
          <hr className="cm-hair" style={{ margin: "18px 0 14px" }} />
          <div className="cm-qt-links">
            <button type="button" className="cm-qt-link" onClick={() => onNavigate("assemble")}>
              合成参数 · 环境声 / 统一影调 / 定向调色 <b>剪辑台 →</b>
            </button>
            <button type="button" className="cm-qt-link" onClick={() => onNavigate("final")}>
              QC 报告 · 黑帧 / 响度 / 字幕覆盖 / 抽帧 <b>成片 →</b>
            </button>
            <button type="button" className="cm-qt-link" onClick={() => onNavigate("jobs")}>
              生成任务 · 本地 H3 队列 / 实时日志 <b>队列 →</b>
            </button>
          </div>
        </div>
      </section>

      <section className="cm-ov-grid">
        <div className="cm-card cm-card-pad">
          <div className="cm-section-head">
            <span className="cm-kicker">Recent Jobs</span>
            <h3 className="cm-section-title">最近任务</h3>
            <button type="button" className="cm-btn cm-btn-ghost cm-btn-sm cm-head-right" onClick={() => onNavigate("jobs")}>
              全部任务 →
            </button>
          </div>
          {overview.jobs.recent?.length ? (
            overview.jobs.recent.slice(0, 5).map((job) => (
              <div key={job.id} className="cm-job-row">
                <i className={"cm-status-dot " + jobDot(job.status)} />
                <span className="cm-job-kind">{JOB_KIND_LABEL[job.kind] || job.kind}</span>
                <span className="cm-chip is-mono">{JOB_STATUS_LABEL[job.status] || job.status}</span>
                <span className="cm-job-meta" style={{ marginLeft: "auto" }}>
                  {job.shots?.length ? `${job.shots.length} 镜 · ` : ""}
                  {job.started_at || ""}
                </span>
                <div className={"cm-progress" + (job.status === "running" ? " is-running" : "")}>
                  <i style={{ width: `${Math.max(2, job.progress || 0)}%` }} />
                </div>
              </div>
            ))
          ) : (
            <div className="cm-empty" style={{ marginTop: 8 }}>
              还没有任务记录：去镜头台选镜生成，或在剪辑台重新合成。
            </div>
          )}
        </div>

        <div className="cm-card cm-card-pad">
          <div className="cm-section-head">
            <span className="cm-kicker">Quick Actions</span>
            <h3 className="cm-section-title">快捷操作</h3>
          </div>
          <div className="cm-quick-grid" style={{ gridTemplateColumns: "1fr 1fr 1fr" }}>
            <button type="button" className="cm-quick" onClick={() => onNavigate("shots")}>
              <span className="cm-quick-icon">
                <IconFramesSmall />
              </span>
              <span className="cm-quick-name">重生成镜头</span>
              <span className="cm-quick-desc">选镜 → 本地 H3 / 云端 H3</span>
              <span className="cm-quick-arrow">→</span>
            </button>
            <button type="button" className="cm-quick" onClick={() => onNavigate("assemble")}>
              <span className="cm-quick-icon">
                <IconSlidersSmall />
              </span>
              <span className="cm-quick-name">剪辑成片</span>
              <span className="cm-quick-desc">统一影调 / 字幕 / BGM</span>
              <span className="cm-quick-arrow">→</span>
            </button>
            <button type="button" className="cm-quick" onClick={() => onNavigate("final")}>
              <span className="cm-quick-icon">
                <IconPlaySmall />
              </span>
              <span className="cm-quick-name">检片 & QC</span>
              <span className="cm-quick-desc">播放 / 指标 / 下载</span>
              <span className="cm-quick-arrow">→</span>
            </button>
          </div>

          <hr className="cm-hair" style={{ margin: "16px 0 10px" }} />
          <div className="cm-qt-links">
            <a className="cm-qt-link" href={"#/episodes/" + (overview.project.episode_id || "") + "/finish"}>
              打开项目成片页（造片局 · 国宝会说话）<b>项目 →</b>
            </a>
            <a className="cm-qt-link" href={"#/projects"}>
              返回项目列表 / 其它模板 <b>平台 →</b>
            </a>
          </div>
        </div>
      </section>

      <p className="cm-subtle" style={{ marginTop: 18, fontSize: 11, letterSpacing: "0.04em" }}>
        数据源：outputs/templates_v5/relic_talk_3d · contract/v5/cinema3d/relic_talk_3d_briefs.json ·
        最终报告 work_assemble/final_report.json
      </p>
    </div>
  );
}

function legendColor(key: string): string {
  switch (key) {
    case "cloud":
      return "linear-gradient(180deg,#e5c66d,#c9a227)";
    case "local":
      return "linear-gradient(180deg,#6fb39c,#3f7365)";
    case "reuse":
      return "linear-gradient(180deg,#75889f,#46536b)";
    default:
      return "#c2543f";
  }
}

function jobDot(status: string): string {
  switch (status) {
    case "running":
      return "is-running";
    case "done":
      return "is-done";
    case "failed":
    case "cancelled":
      return "is-failed";
    default:
      return "";
  }
}

/* 小图标 */
const S = { width: 15, height: 15, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 1.7, strokeLinecap: "round" as const, strokeLinejoin: "round" as const };
function IconPlaySmall() {
  return (
    <svg {...S}>
      <circle cx="12" cy="12" r="8.4" />
      <path d="m10.2 9.2 5 2.8-5 2.8z" fill="currentColor" stroke="none" />
    </svg>
  );
}
function IconFramesSmall() {
  return (
    <svg {...S}>
      <rect x="3" y="6" width="13" height="9" rx="1.6" />
      <path d="M6 18h13a1.6 1.6 0 0 0 1.6-1.6V8.4" opacity="0.6" />
    </svg>
  );
}
function IconSlidersSmall() {
  return (
    <svg {...S}>
      <path d="M4 7h9M17 7h3M4 17h3M11 17h9" />
      <circle cx="15" cy="7" r="2" />
      <circle cx="9" cy="17" r="2" />
    </svg>
  );
}
