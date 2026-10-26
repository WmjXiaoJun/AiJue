// V5 渲染面板：选模板/时长/force → POST /api/v5/render → 轮询进度/日志 → 完成刷新画廊
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { V5CatalogResponse, V5Output, V5RenderJob } from "../../lib/v5";
import { v5api } from "../../lib/v5";
import { useToast } from "../Toast";
import { IconFilm, IconRefresh, IconSpark } from "../icons";
import { ProgressBar, Spinner, StatusBadge } from "../ui";

interface Props {
  catalog: V5CatalogResponse | null;
  outputs: V5Output[];
  loading: boolean;
  onRendered: () => void;
}

const TERMINAL: V5RenderJob["status"][] = ["done", "failed", "canceled"];

export function RenderPanel({ catalog, outputs, loading, onRendered }: Props) {
  const notify = useToast();
  const [template, setTemplate] = useState("tonight_show");
  const [duration, setDuration] = useState(40);
  const [force, setForce] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [job, setJob] = useState<V5RenderJob | null>(null);
  const [recent, setRecent] = useState<V5RenderJob[]>([]);
  const [error, setError] = useState("");
  const notifiedRef = useRef<string>("");

  useEffect(() => {
    if (!catalog) return;
    if (!catalog.templates.some((item) => item.id === template)) {
      const first = catalog.templates.find((item) => item.id === "tonight_show") || catalog.templates[0];
      if (first) setTemplate(first.id);
    }
  }, [catalog, template]);

  const refreshRecent = useCallback(async () => {
    try {
      const res = await v5api.renderJobs();
      setRecent(res.jobs || []);
      if (res.running) setJob((current) => current || res.running);
    } catch {
      /* 后端不可用时静默 */
    }
  }, []);

  useEffect(() => {
    void refreshRecent();
    const timer = window.setInterval(() => void refreshRecent(), 5000);
    return () => window.clearInterval(timer);
  }, [refreshRecent]);

  // 轮询当前 job
  useEffect(() => {
    if (!job || TERMINAL.includes(job.status)) return;
    let alive = true;
    const timer = window.setInterval(async () => {
      try {
        const res = await v5api.renderJob(job.id);
        if (!alive) return;
        setJob(res.job);
        if (TERMINAL.includes(res.job.status)) {
          window.clearInterval(timer);
          if (res.job.status === "done") {
            if (notifiedRef.current !== res.job.id) {
              notifiedRef.current = res.job.id;
              notify(`渲染完成：${res.job.template}（${res.job.elapsed}s）`, "success");
            }
            onRendered();
          } else if (res.job.status === "failed" && notifiedRef.current !== res.job.id) {
            notifiedRef.current = res.job.id;
            notify("渲染失败：" + (res.job.error || res.job.message), "error");
          }
          void refreshRecent();
        }
      } catch (err) {
        if (alive) setError(err instanceof Error ? err.message : String(err));
      }
    }, 1500);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, [job, notify, onRendered, refreshRecent]);

  const selectedTemplate = useMemo(
    () => (catalog?.templates || []).find((item) => item.id === template) || null,
    [catalog, template]
  );
  const rendered = useMemo(
    () => outputs.find((item) => item.id === template) || null,
    [outputs, template]
  );

  const submit = async () => {
    setSubmitting(true);
    setError("");
    try {
      const res = await v5api.render({ template, duration: Number(duration) || 40, force });
      notifiedRef.current = "";
      setJob(res.job);
      notify(`已提交渲染：${template}（job ${res.job.id}）`, "success");
      void refreshRecent();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  };

  const cancel = async () => {
    if (!job) return;
    try {
      const res = await v5api.cancelRender(job.id);
      setJob(res.job);
      notify("已请求取消渲染", "info");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const running = Boolean(job && !TERMINAL.includes(job.status));

  return (
    <section className="v5-section">
      <div className="v5-toolbar">
        <div className="v5-toolbar-main">
          <IconFilm size={16} />
          <b>渲染面板</b>
          <span className="v5-muted">
            提交 `render_v5` 后台任务（单并发）；完成后自动刷新成片画廊
          </span>
        </div>
        <div className="v5-toolbar-right">
          {loading && <Spinner size={14} />}
        </div>
      </div>

      <div className="v5-render-layout">
        <div className="v5-render-form panel">
          <div className="v5-panel-title">选择模板渲染</div>
          <label className="v5-field">
            <span className="field-label">模板</span>
            <select
              className="select select-wide"
              value={template}
              onChange={(event) => setTemplate(event.target.value)}
            >
              {(catalog?.templates || []).map((item) => (
                <option key={item.id} value={item.id}>
                  {item.label} · {item.style || "?"}
                </option>
              ))}
            </select>
          </label>
          <div className="v5-form-row">
            <label className="v5-field">
              <span className="field-label">目标时长（秒）</span>
              <input
                className="input"
                type="number"
                min={5}
                max={600}
                value={duration}
                onChange={(event) => setDuration(Number(event.target.value) || 40)}
              />
            </label>
            <label className="v5-field v5-field-check">
              <input
                type="checkbox"
                checked={force}
                onChange={(event) => setForce(event.target.checked)}
              />
              <span>强制重渲（--force）</span>
            </label>
          </div>

          {selectedTemplate && (
            <div className="v5-render-summary">
              <div className="v5-muted">
                style <b>{selectedTemplate.style}</b> · {selectedTemplate.act_count} acts ·{" "}
                {(selectedTemplate.cast || []).length} 角色
              </div>
              <div className="v5-muted">
                {rendered
                  ? `已有成片：${rendered.duration ?? "?"}s · QC ${rendered.qc_ok ? "通过" : "未通过"} · ${rendered.mtime}`
                  : "该模板还没有成片（首次渲染较慢）"}
              </div>
            </div>
          )}

          {error && <div className="v5-error">{error}</div>}
          <div className="v5-form-actions">
            <button type="button" className="btn btn-ghost" onClick={() => void refreshRecent()}>
              <IconRefresh size={14} /> 刷新任务
            </button>
            <div className="v5-spacer" />
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => void submit()}
              disabled={submitting || running || !catalog}
            >
              {submitting ? <Spinner size={13} /> : <IconSpark size={14} />}
              {running ? "渲染中…" : "开始渲染"}
            </button>
          </div>
        </div>

        <div className="v5-render-jobs panel">
          <div className="v5-panel-title">
            任务状态
            {running && <span className="v5-live-dot" />}
          </div>
          {!job && <div className="v5-muted">暂无渲染任务：提交后会在这里显示进度与日志。</div>}
          {job && (
            <>
              <div className="v5-job-head">
                <StatusBadge status={job.status} kind="job" />
                <span className="v5-muted">
                  {job.template} · job {job.id} · {job.elapsed}s
                </span>
                <div className="v5-spacer" />
                {running && (
                  <button type="button" className="btn btn-ghost btn-sm" onClick={() => void cancel()}>
                    取消
                  </button>
                )}
              </div>
              <ProgressBar
                value={job.progress}
                tone={job.status === "failed" ? "danger" : job.status === "done" ? "success" : "accent"}
                label={`${Math.round(job.progress)}%`}
              />
              <div className="v5-muted v5-job-msg">{job.message || "—"}</div>
              {job.error && <div className="v5-error">{job.error}</div>}
              <pre className="v5-log">{job.log_tail.join("\n") || "（等待日志…）"}</pre>
            </>
          )}
          {recent.length > 0 && (
            <div className="v5-job-list">
              <div className="v5-detail-label">最近任务</div>
              {recent.slice(0, 6).map((item) => (
                <button
                  key={item.id}
                  type="button"
                  className={"v5-job-row" + (job?.id === item.id ? " active" : "")}
                  onClick={() => setJob(item)}
                >
                  <span className={"v5-job-status v5-job-" + item.status} />
                  <span className="v5-ellipsis">{item.template}</span>
                  <span className="v5-muted">{Math.round(item.progress)}%</span>
                  <span className="v5-muted">{item.elapsed}s</span>
                </button>
              ))}
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
