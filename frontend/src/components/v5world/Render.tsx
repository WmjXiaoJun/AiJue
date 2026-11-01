// V5 World · 渲染台：选模板 + 时长 → POST /api/v5/render（render_v5 后台任务），实时进度 / 日志 / 取消。
// 分辨率由模板合同固定（1280×720 / 25fps · 16:9），后端 /api/v5/render 只接收 template/duration/force。
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { V5RenderJob } from "../../lib/v5";
import { v5api } from "../../lib/v5";
import { fmtDuration, fmtSize } from "../../lib/cinema";
import { TEMPLATE_3D_TOTAL } from "../../lib/cinema";
import type { CinemaTemplate } from "../../lib/cinema";
import type { V5PageProps } from "./V5World";
import { IconAlert, IconCheck, IconFilm, IconPlay, IconRefresh, IconSpark } from "../icons";
import { useToast } from "../Toast";
import { Spinner } from "../ui";

const TERMINAL: V5RenderJob["status"][] = ["done", "failed", "canceled"];

const STATUS_TEXT: Record<string, string> = {
  queued: "排队中",
  running: "渲染中",
  done: "已完成",
  failed: "失败",
  canceled: "已取消",
};

const STATUS_TONE: Record<string, string> = {
  queued: "is-gold",
  running: "is-gold",
  done: "is-jade",
  failed: "is-red",
  canceled: "is-slate",
};

export function V5Render({
  catalog,
  templates3d,
  presetTemplate,
  loading,
  onRendered,
}: V5PageProps & { onRendered: () => void }) {
  const notify = useToast();

  const templates = useMemo(() => {
    const all = catalog?.templates || [];
    const show = all.filter((item) => !item.custom && !item.id.startsWith("relic_talk"));
    const others = all.filter((item) => !show.includes(item));
    return [...show, ...others];
  }, [catalog]);

  const [template, setTemplate] = useState("tonight_show");
  const [duration, setDuration] = useState(40);
  const [force, setForce] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [job, setJob] = useState<V5RenderJob | null>(null);
  const [recent, setRecent] = useState<V5RenderJob[]>([]);
  const [error, setError] = useState("");
  const [recentError, setRecentError] = useState("");

  const logRef = useRef<HTMLPreElement | null>(null);
  const notifiedRef = useRef<string>("");
  const aliveRef = useRef(true);
  const initializedRef = useRef(false);

  useEffect(() => {
    aliveRef.current = true;
    return () => {
      aliveRef.current = false;
    };
  }, []);

  const selected = useMemo(() => templates.find((item) => item.id === template) || null, [template, templates]);
  const tpl3d: CinemaTemplate | null = templates3d[template] || null;
  const total3d = Object.keys(templates3d).length;
  const readyCount = Object.values(templates3d).filter((item) => item.final.exists).length;

  // 默认模板 + 目标时长（首次加载时对齐模板 target_duration）
  useEffect(() => {
    if (initializedRef.current || !templates.length) return;
    initializedRef.current = true;
    const preferred = templates.find((item) => item.id === template) || templates[0];
    setTemplate(preferred.id);
    setDuration(preferred.target_duration || 40);
  }, [template, templates]);

  // 模板库「用此模板渲染」→ 预选模板 + 时长
  useEffect(() => {
    if (!presetTemplate) return;
    if (!catalog || templates.some((item) => item.id === presetTemplate)) {
      setTemplate(presetTemplate);
      const target = templates.find((item) => item.id === presetTemplate);
      if (target?.target_duration) setDuration(target.target_duration);
    }
  }, [catalog, presetTemplate, templates]);

  // 切换模板时带出目标时长（用户可再改）
  const handleTemplateChange = (next: string) => {
    setTemplate(next);
    const target = templates.find((item) => item.id === next);
    if (target?.target_duration) setDuration(target.target_duration);
  };

  const refreshRecent = useCallback(async () => {
    try {
      const res = await v5api.renderJobs();
      if (!aliveRef.current) return;
      setRecent(res.jobs || []);
      setRecentError("");
      if (res.running) setJob((current) => current || res.running);
    } catch (err) {
      if (aliveRef.current) setRecentError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  useEffect(() => {
    void refreshRecent();
    const timer = window.setInterval(() => void refreshRecent(), 5000);
    return () => window.clearInterval(timer);
  }, [refreshRecent]);

  // 当前 job 轮询（1.5s）
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
            notify("渲染失败：" + (res.job.error || res.job.message || "未知原因"), "error");
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

  // 日志自动滚到底
  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [job?.log_tail.length, job?.id]);

  const running = Boolean(job && !TERMINAL.includes(job.status));

  const submit = async () => {
    if (submitting || running) return;
    const seconds = Number(duration);
    if (!Number.isFinite(seconds) || seconds < 5 || seconds > 600) {
      notify("时长需在 5–600 秒之间", "error");
      return;
    }
    const confirmed = window.confirm(
      `将渲染「${selected?.label || template}」（${seconds}s）。\n\nrender_v5 会跑完整流水线（镜头/配音/剪辑，约 1–3 分钟），继续？`,
    );
    if (!confirmed) return;
    setSubmitting(true);
    setError("");
    try {
      const res = await v5api.render({ template, duration: seconds, force });
      notifiedRef.current = "";
      setJob(res.job);
      notify(`已提交渲染：${template}（job ${res.job.id}）`, "success");
      void refreshRecent();
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setError(message);
      notify("提交渲染失败：" + message, "error");
    } finally {
      if (aliveRef.current) setSubmitting(false);
    }
  };

  const cancel = async () => {
    if (!job) return;
    try {
      const res = await v5api.cancelRender(job.id);
      setJob(res.job);
      notify("已请求取消渲染", "info");
      void refreshRecent();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const jobProgress = job ? (job.status === "done" ? 100 : Math.max(0, Math.min(100, Number(job.progress) || 0))) : 0;

  return (
    <div className="cm-page v5w-page">
      <header className="cm-page-head">
        <div className="cm-head-main">
          <span className="cm-kicker">RENDER · 渲染台</span>
          <h1 className="cm-h1">渲染台</h1>
          <p className="cm-sub">
            选模板 + 目标时长 → 提交 render_v5 后台任务（单并发）· {templates.length} 个模板可选 · 3D 电影版已就绪{" "}
            {readyCount}/{total3d || TEMPLATE_3D_TOTAL}
          </p>
        </div>
        <div className="cm-head-actions">
          <button type="button" className="cm-icon-btn" onClick={() => void refreshRecent()} title="刷新渲染任务">
            {submitting ? <Spinner size={14} /> : <IconRefresh />}
          </button>
        </div>
      </header>

      <section className="st-kpis v5w-kpis">
        <div className="st-kpi">
          <span className="st-kpi-label">Templates</span>
          <div className="st-kpi-value">
            {templates.length}
            <small>个可选</small>
          </div>
          <div className="st-kpi-hint">catalog.templates</div>
        </div>
        <div className="st-kpi">
          <span className="st-kpi-label">3D Ready</span>
          <div className="st-kpi-value is-jade">
            {readyCount}
            <small>/ {total3d || TEMPLATE_3D_TOTAL}</small>
          </div>
          <div className="st-kpi-hint">3D 电影版已就绪</div>
        </div>
        <div className="st-kpi">
          <span className="st-kpi-label">Job</span>
          <div className="st-kpi-value is-gold">
            {running ? Math.round(jobProgress) : recent.length}
            <small>{running ? "%" : "个历史"}</small>
          </div>
          <div className="st-kpi-hint">{running ? "渲染进行中 · 1.5s 轮询" : "队列空闲"}</div>
        </div>
        <div className="st-kpi">
          <span className="st-kpi-label">Spec</span>
          <div className="st-kpi-value">
            1280<small>×720 · 25fps</small>
          </div>
          <div className="st-kpi-hint">渲染规格由模板合同固定</div>
        </div>
      </section>

      <div className="v5w-render-grid">
        <section className="st-panel v5w-render-form">
          <div className="st-panel-head">
            <h3 className="st-panel-title">新建渲染</h3>
            <p className="st-panel-sub">POST /api/v5/render · 后台单并发任务</p>
            <div className="st-panel-right">
              {running ? <span className="st-tag is-gold">渲染中</span> : <span className="st-tag is-slate">空闲</span>}
            </div>
          </div>
          <div className="st-panel-body">
            <label className="st-field">
              <span className="st-label">模板</span>
              <select className="cm-select" value={template} onChange={(event) => handleTemplateChange(event.target.value)}>
                {templates.map((item) => {
                  const threeD = templates3d[item.id];
                  const done3d = Boolean(threeD && threeD.final.exists);
                  return (
                    <option key={item.id} value={item.id}>
                      {item.label} · {item.style} · {item.target_duration}s{done3d ? " · 3D ✓" : ""}
                    </option>
                  );
                })}
              </select>
            </label>

            <div className="v5w-form-row">
              <label className="st-field">
                <span className="st-label">目标时长（秒）</span>
                <input
                  className="cm-input"
                  type="number"
                  min={5}
                  max={600}
                  step={1}
                  value={duration}
                  onChange={(event) => setDuration(Number(event.target.value) || 0)}
                />
              </label>
              <label className="st-field">
                <span className="st-label">输出规格</span>
                <input className="cm-input" value="1280×720 · 25fps · 16:9" readOnly title="由模板合同固定，接口不接收自定义宽高" />
              </label>
            </div>

            <label className="v5w-check">
              <input type="checkbox" checked={force} onChange={(event) => setForce(event.target.checked)} />
              <span>强制重渲（--force，忽略已有缓存）</span>
            </label>

            {selected && (
              <div className="v5w-render-summary">
                <div className="v5w-render-summary-row">
                  <span>幕结构</span>
                  <b className="cm-mono">{selected.act_count || 0} 幕 · {(selected.cast || []).length} 角色</b>
                </div>
                <div className="v5w-render-summary-row">
                  <span>场景路线</span>
                  <b className="cm-mono">{(selected.scene_route || []).length} 个节点</b>
                </div>
                <div className="v5w-render-summary-row">
                  <span>3D 电影版</span>
                  <b className="cm-mono">
                    {tpl3d?.final.exists
                      ? `${fmtDuration(tpl3d.final.duration)} · ${fmtSize(tpl3d.final.size)} · 已就绪`
                      : "暂无 3D 版"}
                  </b>
                </div>
              </div>
            )}

            {error && (
              <div className="cm-error v5w-error">
                <IconAlert size={14} />
                <span>{error}</span>
              </div>
            )}

            <div className="v5w-render-actions">
              <span className="st-hint">
                时长越接近模板目标时长，节奏越稳；渲染完成后自动刷新成片画廊。
              </span>
              <div className="v5w-render-actions-right">
                {running && (
                  <button type="button" className="cm-btn cm-btn-sm" onClick={() => void cancel()}>
                    取消渲染
                  </button>
                )}
                <button
                  type="button"
                  className="cm-btn cm-btn-primary cm-btn-sm"
                  onClick={() => void submit()}
                  disabled={submitting || running || loading || !templates.length}
                >
                  {submitting ? <Spinner size={13} /> : <IconSpark size={13} />}
                  {running ? "渲染中…" : "开始渲染"}
                </button>
              </div>
            </div>
          </div>
        </section>

        <div className="v5w-render-side">
          <section className="st-panel v5w-render-job">
            <div className="st-panel-head">
              <h3 className="st-panel-title">任务状态</h3>
              <p className="st-panel-sub">{job ? `job ${job.id}` : "提交后在这里显示进度与日志"}</p>
              <div className="st-panel-right">
                {running && <span className="v5w-live-dot" title="渲染中" />}
                {job && (
                  <span className={"st-tag " + (STATUS_TONE[job.status] || "is-slate")}>
                    {STATUS_TEXT[job.status] || job.status}
                  </span>
                )}
              </div>
            </div>
            <div className="st-panel-body">
              {!job ? (
                <div className="cm-empty v5w-empty is-compact">
                  <IconFilm size={26} />
                  <span>暂无渲染任务</span>
                  <span className="cm-subtle">选择模板后点「开始渲染」，任务会实时出现在这里。</span>
                </div>
              ) : (
                <>
                  <div className="v5w-job-head">
                    <span className="cm-mono v5w-job-id">{job.id}</span>
                    <span className="v5w-muted cm-mono">{job.template}</span>
                    <span className="v5w-muted cm-mono">{job.elapsed}s</span>
                    <span className="v5w-job-spacer" />
                    <span className="v5w-muted cm-mono">{job.duration}s 目标</span>
                  </div>
                  <div className={"cm-progress" + (running ? " is-running" : "")}>
                    <i style={{ width: `${Math.max(jobProgress, job.status === "failed" ? 2 : 0)}%` }} />
                  </div>
                  <div className="v5w-job-meta">
                    <span className="cm-mono">{Math.round(jobProgress)}%</span>
                    <span className="v5w-job-msg">{job.message || "—"}</span>
                  </div>
                  {job.error && (
                    <div className="cm-error v5w-error">
                      <IconAlert size={14} />
                      <span>{job.error}</span>
                    </div>
                  )}
                  <pre className="v5w-log" ref={logRef}>
                    {(job.log_tail || []).join("\n") || "（等待日志…）"}
                  </pre>
                </>
              )}
            </div>
          </section>

          <section className="st-panel v5w-render-recent">
            <div className="st-panel-head">
              <h3 className="st-panel-title">最近任务</h3>
              <p className="st-panel-sub">GET /api/v5/render · 每 5s 刷新</p>
              <div className="st-panel-right">
                <button type="button" className="cm-btn cm-btn-ghost cm-btn-sm" onClick={() => void refreshRecent()}>
                  <IconRefresh size={12} /> 刷新
                </button>
              </div>
            </div>
            <div className="st-panel-body is-flush">
              {recentError ? (
                <div className="cm-error v5w-error">
                  <IconAlert size={14} />
                  <span>任务列表读取失败：{recentError}</span>
                </div>
              ) : recent.length === 0 ? (
                <div className="cm-empty v5w-empty is-compact">
                  <span>还没有渲染记录</span>
                </div>
              ) : (
                <div className="v5w-job-list">
                  {recent.slice(0, 8).map((item) => (
                    <button
                      key={item.id}
                      type="button"
                      className={"v5w-job-row" + (job?.id === item.id ? " is-active" : "")}
                      onClick={() => setJob(item)}
                    >
                      <i className={"v5w-job-dot is-" + item.status} />
                      <span className="v5w-job-row-template">{item.template}</span>
                      <span className="cm-mono v5w-muted">{item.duration}s</span>
                      <span className="cm-mono v5w-muted">{Math.round(item.progress || 0)}%</span>
                      <span className="cm-mono v5w-muted">{item.elapsed}s</span>
                      <span className={"st-tag " + (STATUS_TONE[item.status] || "is-slate")}>
                        {STATUS_TEXT[item.status] || item.status}
                      </span>
                    </button>
                  ))}
                </div>
              )}
            </div>
          </section>

          {tpl3d?.final.exists && (
            <section className="st-panel v5w-render-3d">
              <div className="st-panel-head">
                <h3 className="st-panel-title">当前模板 · 3D 电影版</h3>
                <div className="st-panel-right">
                  <span className="st-tag is-jade">
                    <IconCheck size={11} /> 已就绪
                  </span>
                </div>
              </div>
              <div className="st-panel-body">
                <a
                  className="cm-btn cm-btn-sm"
                  href={
                    tpl3d.final.url
                      ? tpl3d.final.url
                      : `/media/templates_3d/${selected?.id}/final.mp4`
                  }
                  target="_blank"
                  rel="noreferrer"
                >
                  <IconPlay size={12} /> 播放 {fmtDuration(tpl3d.final.duration)} 3D 版
                </a>
                <span className="st-hint">3D 版由本地 ComfyUI H3 生成，与 2D 渲染共享时间线字幕。</span>
              </div>
            </section>
          )}
        </div>
      </div>
    </div>
  );
}
