// Cinema Studio · 剪辑台
// 合成参数（环境声 / 统一影调 / 定向调色）→ POST /cinema/assemble → 轮询 job 进度与日志 → 查看成片。
import { useCallback, useEffect, useRef, useState } from "react";
import { errorText } from "../../lib/api";
import {
  JOB_STATUS_LABEL,
  cinemaApi,
  fmtDuration,
  fmtSize,
  type CinemaJob,
  type CinemaReport,
} from "../../lib/cinema";
import { useToast } from "../Toast";
import {
  IconAlert,
  IconArrowRight,
  IconCheck,
  IconClock,
  IconFilm,
  IconPalette,
  IconRefresh,
  IconSpark,
} from "../icons";
import "../../styles/cinema-final.css";

const TERMINAL_STATUS: CinemaJob["status"][] = ["done", "failed", "cancelled"];

function isJobActive(job: CinemaJob | null): boolean {
  if (!job) return false;
  return job.status === "queued" || job.status === "running";
}

function jobProgress(job: CinemaJob | null): number {
  if (!job) return 0;
  const value = Number(job.progress);
  if (!Number.isFinite(value)) return job.status === "done" ? 100 : 0;
  return Math.max(0, Math.min(100, Math.round(value)));
}

function jobLogLines(job: CinemaJob | null): string[] {
  if (!job || !Array.isArray(job.logs)) return [];
  return job.logs;
}

function parseTime(value: string | null | undefined): number {
  if (!value) return Number.NaN;
  const ts = Date.parse(value);
  return Number.isFinite(ts) ? ts : Number.NaN;
}

function pad2(value: number): string {
  return String(value).padStart(2, "0");
}

function fmtMtime(value: string | null | undefined): string {
  if (!value) return "—";
  const ts = parseTime(value);
  if (!Number.isFinite(ts)) return String(value).replace("T", " ").slice(0, 16);
  const date = new Date(ts);
  return `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())} ${pad2(date.getHours())}:${pad2(date.getMinutes())}`;
}

const PIPELINE_STEPS = [
  {
    title: "母素材检查",
    en: "MEDIA CHECK",
    desc: "校验 56 镜母素材与 episode 时间线，缺失镜头自动顺接并在报告中记录缺口。",
  },
  {
    title: "标准化 / 统一影调",
    en: "NORMALIZE & LOOK",
    desc: "逐镜 relink 与锐化校准，统一暖金高光、青绿暗部、暗角与轻颗粒，抹平云端 / 本地画质差。",
  },
  {
    title: "字幕 / BGM / 混音",
    en: "ASS · BGM · MIX",
    desc: "49 句旁白 ASS 字幕 + 章节小标，BGM ducking、环境声床与两遍 loudnorm，输出 -16 LUFS 母带。",
  },
  {
    title: "编码 QC",
    en: "ENCODE & QC",
    desc: "H.264 720p 25fps 编码，黑帧 / 响度 / 字幕覆盖 / 逐镜时长偏差写入 final_report.json。",
  },
];

interface CinemaAssembleProps {
  onAssembled?: () => void;
}

export function CinemaAssemble({ onAssembled }: CinemaAssembleProps) {
  const notify = useToast();

  const [ambienceDb, setAmbienceDb] = useState(-20);
  const [unifyLook, setUnifyLook] = useState(true);
  const [shotGrade, setShotGrade] = useState(true);

  const [report, setReport] = useState<CinemaReport | null>(null);
  const [reportError, setReportError] = useState("");
  const [reportLoading, setReportLoading] = useState(true);

  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState("");
  const [pollError, setPollError] = useState("");
  const [jobId, setJobId] = useState<string | null>(null);
  const [job, setJob] = useState<CinemaJob | null>(null);
  const [adopting, setAdopting] = useState(true);

  const notifiedRef = useRef("");
  const onAssembledRef = useRef(onAssembled);

  useEffect(() => {
    onAssembledRef.current = onAssembled;
  }, [onAssembled]);

  const loadReport = useCallback(async () => {
    setReportLoading(true);
    try {
      const res = await cinemaApi.report();
      setReport(res);
      setReportError("");
    } catch (err) {
      setReportError(errorText(err));
    } finally {
      setReportLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadReport();
  }, [loadReport]);

  // 进页面时接管已在运行的合成任务。
  useEffect(() => {
    let alive = true;
    const adopt = async () => {
      try {
        const res = await cinemaApi.jobs();
        if (!alive) return;
        const running = (res.jobs || []).find(
          (item) => item.kind === "assemble" && (item.status === "queued" || item.status === "running")
        );
        if (running) {
          setJobId(running.id);
          setJob(running);
        }
      } catch {
        /* 任务列表不可用时保持手动模式，不影响参数操作 */
      } finally {
        if (alive) setAdopting(false);
      }
    };
    void adopt();
    return () => {
      alive = false;
    };
  }, []);

  const handleTerminal = useCallback(
    (finished: CinemaJob) => {
      if (finished.status === "done") {
        if (notifiedRef.current !== finished.id) {
          notifiedRef.current = finished.id;
          notify("合成完成：成片与 QC 报告已更新", "success");
        }
        void loadReport();
        onAssembledRef.current?.();
      } else if (finished.status === "failed") {
        if (notifiedRef.current !== finished.id) {
          notifiedRef.current = finished.id;
          notify("合成失败：" + (finished.message || "请查看运行日志"), "error");
        }
        void loadReport();
      } else if (finished.status === "cancelled") {
        if (notifiedRef.current !== finished.id) {
          notifiedRef.current = finished.id;
          notify("合成任务已取消", "info");
        }
      }
    },
    [loadReport, notify]
  );

  // 轮询当前合成任务（1.5s）；终态时停止并触发回调。
  useEffect(() => {
    if (!jobId) return;
    let alive = true;
    let timer = 0;
    const tick = async () => {
      try {
        const next = await cinemaApi.job(jobId);
        if (!alive) return;
        setJob(next);
        setPollError("");
        if (TERMINAL_STATUS.includes(next.status)) {
          window.clearInterval(timer);
          handleTerminal(next);
        }
      } catch (err) {
        if (alive) setPollError(errorText(err));
      }
    };
    void tick();
    timer = window.setInterval(() => {
      if (!document.hidden) void tick();
    }, 1500);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, [jobId, handleTerminal]);

  const submit = useCallback(async () => {
    setSubmitting(true);
    setSubmitError("");
    setPollError("");
    try {
      const res = await cinemaApi.assemble({
        ambience_db: ambienceDb,
        unify_look: unifyLook,
        shot_grade: shotGrade,
      });
      notifiedRef.current = "";
      setJob(null);
      setJobId(res.job_id);
      notify("已提交合成任务，正在渲染母版…", "success");
    } catch (err) {
      const message = errorText(err);
      setSubmitError(message);
      notify("提交合成失败：" + message, "error");
    } finally {
      setSubmitting(false);
    }
  }, [ambienceDb, notify, shotGrade, unifyLook]);

  const goFinal = useCallback(() => {
    onAssembledRef.current?.();
    if (!window.location.hash.includes("/cinema/final")) {
      window.location.hash = "#/cinema/final";
    }
  }, []);

  const active = isJobActive(job);
  const done = job?.status === "done";
  const failed = job?.status === "failed";
  // 提交成功但首个轮询尚未返回时，仍视为合成中，避免按钮重新可点造成重复提交。
  const waiting = Boolean(jobId) && !job;
  const busy = submitting || waiting || active;
  const progress = jobProgress(job);
  const logs = jobLogLines(job);
  const reportFinal = report?.final ?? null;

  const buttonLabel = submitting || waiting
    ? "提交中…"
    : active
      ? "合成中…"
      : done
        ? "再次合成"
        : failed
          ? "重试合成"
          : "开始合成";

  return (
    <div className="cm-page cm-asm">
      <header className="cm-page-head cm-asm-head">
        <div className="cm-head-main">
          <div className="cm-kicker">EDIT &amp; MASTER</div>
          <h1 className="cm-h1">剪辑台</h1>
          <p className="cm-sub">56 镜 · 5:25 · 统一影调 / 字幕 / BGM / 环境声</p>
        </div>
      </header>

      <div className="cm-asm-grid">
        <section className="cm-card cm-card-pad cm-asm-panel cm-asm-params">
          <div className="cm-asm-panel-head">
            <h2 className="cm-card-title">合成参数</h2>
            <span className="cm-kicker cm-asm-panel-kicker">ASSEMBLY PARAMETERS</span>
          </div>
          <div className="cm-hair" aria-hidden="true" />

          <div className="cm-asm-field">
            <div className="cm-asm-field-row">
              <span className="cm-asm-label">
                环境声
                <small>AMBIENCE</small>
              </span>
              <span className="cm-asm-value cm-mono">{ambienceDb} dB</span>
            </div>
            <input
              type="range"
              className="cm-asm-range"
              min={-40}
              max={-6}
              step={1}
              value={ambienceDb}
              disabled={busy}
              onChange={(event) => setAmbienceDb(Number(event.target.value))}
              aria-label="环境声混入音量（dB）"
            />
            <div className="cm-asm-scale cm-mono" aria-hidden="true">
              <span>-40 dB 静音</span>
              <span>-20 dB 默认</span>
              <span>-6 dB 最响</span>
            </div>
            <p className="cm-asm-hint">每镜 H3 原声环境床的混入音量，压在旁白之下；-20 dB 为交付基准。</p>
          </div>

          <div className="cm-hair" aria-hidden="true" />

          <div className="cm-asm-toggles">
            <div className="cm-asm-toggle">
              <span className="cm-asm-toggle-icon" aria-hidden="true">
                <IconPalette size={15} />
              </span>
              <div className="cm-asm-toggle-text">
                <span className="cm-asm-toggle-name">
                  统一影调
                  <span className="cm-asm-toggle-en">UNIFY LOOK</span>
                </span>
                <span className="cm-asm-toggle-hint">
                  暖金高光 / 青绿暗部 + 暗角 + 轻颗粒，抹平云端与本地 H3 的画质差。
                </span>
              </div>
              <button
                type="button"
                role="switch"
                aria-checked={unifyLook}
                aria-label="统一影调"
                className={"cm-asm-switch" + (unifyLook ? " is-on" : "")}
                disabled={busy}
                onClick={() => setUnifyLook((value) => !value)}
              >
                <span />
              </button>
            </div>

            <div className="cm-asm-toggle">
              <span className="cm-asm-toggle-icon" aria-hidden="true">
                <IconSpark size={15} />
              </span>
              <div className="cm-asm-toggle-text">
                <span className="cm-asm-toggle-name">
                  定向调色
                  <span className="cm-asm-toggle-en">SHOT GRADE</span>
                </span>
                <span className="cm-asm-toggle-hint">
                  QA warn 镜头单独压暗 / 降饱和 / 跳过白墙开场，不影响全片统一基线。
                </span>
              </div>
              <button
                type="button"
                role="switch"
                aria-checked={shotGrade}
                aria-label="定向调色"
                className={"cm-asm-switch" + (shotGrade ? " is-on" : "")}
                disabled={busy}
                onClick={() => setShotGrade((value) => !value)}
              >
                <span />
              </button>
            </div>
          </div>

          <div className="cm-hair" aria-hidden="true" />

          <div className="cm-asm-submit-area">
            <button
              type="button"
              className="cm-btn cm-btn-primary cm-asm-submit"
              onClick={() => void submit()}
              disabled={busy}
            >
              <IconFilm size={16} />
              {buttonLabel}
            </button>
            <p className="cm-asm-submit-note">预计 2–3 分钟（含缓存）；完成后自动刷新成片与 QC 报告。</p>
          </div>

          {submitError && (
            <div className="cm-error cm-asm-error" role="alert">
              <IconAlert size={15} />
              <span>提交失败：{submitError}</span>
            </div>
          )}
          {pollError && (
            <div className="cm-error cm-asm-error" role="alert">
              <IconAlert size={15} />
              <span>任务轮询异常：{pollError}</span>
            </div>
          )}

          {adopting && !job && (
            <div className="cm-loading cm-asm-adopting">正在检查运行中的合成任务…</div>
          )}

          {job && (
            <div
              className={
                "cm-asm-run " +
                (done ? "is-done" : failed ? "is-failed" : active ? "is-active" : "is-idle")
              }
            >
              <div className="cm-asm-run-head">
                <span className="cm-asm-run-state">
                  {done ? (
                    <>
                      <IconCheck size={15} /> 合成完成
                    </>
                  ) : failed ? (
                    <>
                      <IconAlert size={15} /> 合成失败
                    </>
                  ) : active ? (
                    <>
                      <i className="cm-status-dot is-running" aria-hidden="true" />
                      合成中 · {JOB_STATUS_LABEL[job.status] || job.status}
                    </>
                  ) : (
                    <>
                      <IconClock size={15} /> {JOB_STATUS_LABEL[job.status] || job.status}
                    </>
                  )}
                </span>
                <span className="cm-asm-run-pct cm-mono">{progress}%</span>
              </div>

              <div className={"cm-progress cm-asm-bar" + (active ? " is-running" : "")} aria-hidden="true">
                <i style={{ width: progress + "%" }} />
              </div>

              {job.message && <p className="cm-asm-run-msg">{job.message}</p>}

              {logs.length > 0 && (
                <pre className="cm-scroll cm-mono cm-asm-log">{logs.slice(-60).join("\n")}</pre>
              )}

              {(done || failed) && (
                <div className="cm-asm-run-actions">
                  {done && (
                    <button type="button" className="cm-btn cm-btn-primary" onClick={goFinal}>
                      查看成片 <IconArrowRight size={15} />
                    </button>
                  )}
                  {failed && (
                    <button
                      type="button"
                      className="cm-btn cm-btn-primary"
                      onClick={() => void submit()}
                      disabled={submitting}
                    >
                      重试 <IconRefresh size={14} />
                    </button>
                  )}
                </div>
              )}
            </div>
          )}
        </section>

        <section className="cm-card cm-card-pad cm-asm-panel cm-asm-pipeline">
          <div className="cm-asm-panel-head">
            <h2 className="cm-card-title">成片清单</h2>
            <span className="cm-kicker cm-asm-panel-kicker">ASSEMBLY PIPELINE</span>
          </div>
          <div className="cm-hair" aria-hidden="true" />

          <div className="cm-pipe cm-asm-pipe">
            {PIPELINE_STEPS.map((step, index) => (
              <div key={step.en} className="cm-pipe-step">
                <span className="cm-pipe-no cm-mono">{pad2(index + 1)}</span>
                <div className="cm-pipe-main">
                  <div className="cm-pipe-name">
                    {step.title}
                    <small className="cm-asm-step-en">{step.en}</small>
                  </div>
                  <div className="cm-pipe-desc">{step.desc}</div>
                </div>
              </div>
            ))}
          </div>

          <div className="cm-hair" aria-hidden="true" />

          <div className="cm-asm-final">
            <div className="cm-asm-final-head">
              <span>当前成片</span>
              <button
                type="button"
                className="cm-btn cm-btn-ghost cm-btn-sm cm-asm-final-refresh"
                onClick={() => void loadReport()}
                disabled={reportLoading}
              >
                <IconRefresh size={13} className={reportLoading ? "is-spin" : undefined} />
                刷新
              </button>
            </div>

            {reportError ? (
              <div className="cm-asm-final-empty" role="alert">
                报告暂不可用：{reportError}
              </div>
            ) : reportFinal ? (
              <div className="cm-asm-final-grid">
                <div>
                  <span className="cm-asm-final-k">时长</span>
                  <span className="cm-asm-final-v cm-mono">{fmtDuration(reportFinal.duration)}</span>
                </div>
                <div>
                  <span className="cm-asm-final-k">大小</span>
                  <span className="cm-asm-final-v cm-mono">{fmtSize(reportFinal.size)}</span>
                </div>
                <div>
                  <span className="cm-asm-final-k">更新</span>
                  <span className="cm-asm-final-v cm-mono">{fmtMtime(reportFinal.mtime)}</span>
                </div>
              </div>
            ) : (
              <div className="cm-asm-final-empty">尚未生成成片：完成一次合成后可在此查看最新文件信息。</div>
            )}
          </div>
        </section>
      </div>
    </div>
  );
}
