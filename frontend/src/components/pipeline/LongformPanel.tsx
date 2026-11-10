// V54 · 5 分钟长版面板（管线页）：单模板 300s + 全部模板批量 + 进度/日志位
// 后端：POST /api/v5/longform/run {template|all, duration:300} · GET /api/v5/longform/status
import { useCallback, useEffect, useRef, useState } from "react";
import { errorText } from "../../lib/api";
import {
  LONGFORM_DURATION,
  isLongformActive,
  isLongformNotReady,
  longformApi,
  type LongformState,
} from "../../lib/longform";
import { useToast } from "../Toast";
import { Spinner } from "../ui";
import { IconAlert, IconClock, IconInfo, IconPlay, IconRefresh } from "../icons";
import "../../styles/v54.css";

export function LongformPanel({
  selectedId,
  selectedLabel,
  total,
}: {
  selectedId: string | null;
  selectedLabel?: string;
  total: number;
}) {
  const notify = useToast();
  const aliveRef = useRef(true);
  const [state, setState] = useState<LongformState | null>(null);
  const [error, setError] = useState("");
  const [notReady, setNotReady] = useState(false);
  const [busy, setBusy] = useState<"" | "one" | "all">("");
  const [lastStarted, setLastStarted] = useState("");

  useEffect(() => {
    aliveRef.current = true;
    return () => {
      aliveRef.current = false;
    };
  }, []);

  const refresh = useCallback(async () => {
    try {
      const next = await longformApi.status();
      if (!aliveRef.current) return;
      setState(next);
      setError("");
      setNotReady(false);
    } catch (err) {
      if (!aliveRef.current) return;
      if (isLongformNotReady(err)) {
        setNotReady(true);
        setError("");
      } else {
        setError("长版状态读取失败：" + errorText(err));
      }
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const running = state?.running || null;
  const active = isLongformActive(running);

  // 运行中 3s 轮询
  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(() => void refresh(), 3000);
    return () => window.clearInterval(timer);
  }, [active, refresh]);

  // 未接入时每 30s 自检一次（后端上线后自动点亮，无需刷新页面）
  useEffect(() => {
    if (!notReady) return;
    const timer = window.setInterval(() => void refresh(), 30000);
    return () => window.clearInterval(timer);
  }, [notReady, refresh]);

  const start = useCallback(
    async (mode: "one" | "all") => {
      if (mode === "one" && !selectedId) {
        notify("先在模板列表里选一个模板", "info");
        return;
      }
      if (active) {
        notify("已有长版任务在跑：" + (running?.template || running?.id || ""), "info");
        return;
      }
      setBusy(mode);
      setError("");
      try {
        const res = await longformApi.run(
          mode === "one" ? { template: selectedId || undefined, duration: LONGFORM_DURATION } : { all: true, duration: LONGFORM_DURATION },
        );
        if (!aliveRef.current) return;
        setLastStarted(new Date().toLocaleTimeString("zh-CN", { hour12: false }));
        notify(
          mode === "one"
            ? "已启动 5 分钟长版：" + (selectedLabel || selectedId) + " · " + LONGFORM_DURATION + "s"
            : "已启动全部模板 5 分钟长版（" + total + " 个）",
          "success",
        );
        if (res.job_id) void refresh();
      } catch (err) {
        if (!aliveRef.current) return;
        if (isLongformNotReady(err)) {
          setNotReady(true);
          notify("长版接口返回 404：POST /api/v5/longform/run", "error");
        } else {
          setError("启动失败：" + errorText(err));
        }
      } finally {
        if (aliveRef.current) setBusy("");
      }
    },
    [active, notify, refresh, running?.id, running?.template, selectedId, selectedLabel, total],
  );

  const jobs = state?.jobs || [];
  const statusText = notReady ? "未接入" : active ? "运行中" : error ? "异常" : "空闲";

  return (
    <section className="v18-panel v54-longform">
      <header className="v18-panel-head">
        <div className="v18-panel-head-main">
          <h3 className="v18-panel-title">
            <IconClock size={15} /> 5 分钟长版
          </h3>
          <p className="v18-panel-sub">
            长版 · {LONGFORM_DURATION}s · {running ? "运行中" : "可启动"}
            {lastStarted ? " · 最近启动 " + lastStarted : ""}
          </p>
        </div>
        <div className="v18-panel-actions">
          <span className={"ag-status " + (notReady ? "is-pending" : active ? "is-running" : error ? "is-failed" : "is-ok")}>
            <i className={"v54-dot" + (active ? " is-live" : "")} /> {statusText}
          </span>
        </div>
      </header>

      <div className="v18-panel-body">
        {notReady && (
          <p className="v18-note is-warn">
            <IconAlert size={13} />
            <span>
              长版接口暂不可用：按钮先禁用；面板每 30s 自检，恢复后自动点亮。
              契约：<code>POST /api/v5/longform/run</code> {"{template|all, duration:300}"} · <code>GET /api/v5/longform/status</code>
            </span>
          </p>
        )}
        {error && (
          <div className="v18-error ag-inline-error">
            <IconAlert size={14} />
            <div className="v18-error-main">
              <p className="v18-error-text">{error}</p>
            </div>
            <div className="v18-error-actions">
              <button type="button" className="cm-btn cm-btn-sm" onClick={() => void refresh()}>
                <IconRefresh size={12} /> 重试
              </button>
            </div>
          </div>
        )}

        <div className="v54-longform-actions">
          <button
            type="button"
            className="cm-btn cm-btn-sm cm-btn-primary"
            onClick={() => void start("one")}
            disabled={!selectedId || active || busy !== "" || notReady}
            title={
              notReady
                ? "接口返回 404：GET /api/v5/longform/status"
                : selectedId
                  ? "POST /api/v5/longform/run {template:" + selectedId + ", duration:" + LONGFORM_DURATION + "}"
                  : "先选模板"
            }
          >
            {busy === "one" ? <Spinner size={12} /> : <IconPlay size={12} />} 本模板 {LONGFORM_DURATION}s
            {selectedLabel ? " · " + selectedLabel : ""}
          </button>
          <button
            type="button"
            className="cm-btn cm-btn-sm"
            onClick={() => void start("all")}
            disabled={active || busy !== "" || notReady}
            title={notReady ? "长版接口返回 404" : "POST /api/v5/longform/run {all:true, duration:" + LONGFORM_DURATION + "}"}
          >
            {busy === "all" ? <Spinner size={12} /> : <IconRefresh size={12} />} 全部模板 5 分钟（{total} 个）
          </button>
          <button type="button" className="cm-btn cm-btn-sm cm-btn-ghost" onClick={() => void refresh()} title="重新读取长版状态">
            <IconRefresh size={12} /> 刷新状态
          </button>
        </div>

        {active && running && (
          <div className="v54-longform-progress">
            <div className="v54-longform-progress-head">
              <b>{running.template || running.id || "—"}</b>
              <span className="cm-mono">{running.status}</span>
              <em>{running.progress}%</em>
            </div>
            <div className="v54-bar">
              <i style={{ width: Math.max(2, Math.min(100, running.progress)) + "%" }} />
            </div>
            {running.message && <span className="v54-longform-msg">{running.message}</span>}
          </div>
        )}

        {jobs.length > 0 && (
          <div className="v54-longform-jobs">
            <div className="v54-longform-jobs-head">
              <span>最近任务</span>
              <em>{jobs.length}</em>
            </div>
            {jobs.slice(0, 4).map((job) => (
              <div key={job.id + job.template} className={"v54-job is-" + (job.status || "queued")}>
                <b className="cm-mono">{job.template || job.id || "—"}</b>
                <span className={"v18-tag " + (job.status === "ok" || job.status === "done" ? "is-jade" : job.status === "failed" ? "is-red" : "is-gold")}>
                  {job.status}
                </span>
                <em className="cm-mono">{job.progress}%</em>
                {job.final_url && (
                  <a className="v54-job-link" href={job.final_url} target="_blank" rel="noreferrer">
                    成片
                  </a>
                )}
              </div>
            ))}
          </div>
        )}

        {!notReady && !active && !jobs.length && !error && (
          <p className="v18-note">
            <IconInfo size={13} />
            <span>长版 = 把该模板的镜头扩到 {LONGFORM_DURATION}s（5 分钟）后重新合成；单条/整批都走同一队列。</span>
          </p>
        )}
      </div>
    </section>
  );
}
