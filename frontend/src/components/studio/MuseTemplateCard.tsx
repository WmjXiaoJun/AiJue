// R8 · 生成台「云端 Muse 生成 · 每镜按角色」卡片。
// 每段 10 秒，参考图 = 该句说话人的立绘，段间用上一段最后一帧接首帧。
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiError } from "../../lib/api";
import type { Episode, Project } from "../../types";
import {
  agentApi,
  isMuseTemplateActive,
  museFinalUrl,
  runningJobIdFrom409,
  type MuseTemplateJob,
} from "../../lib/agent";
import { useToast } from "../Toast";
import { Spinner } from "../ui";
import { IconAlert, IconPlay, IconRefresh } from "../icons";

const SEGMENT_SECONDS = 10;

export function MuseTemplateCard({ project, episode }: { project: Project; episode: Episode }) {
  const notify = useToast();
  /** 生产模板 id：项目格式去掉 _5min（tonight_show_5min → tonight_show） */
  const template = useMemo(() => String(project.format_id || "").replace(/_5min$/i, "").trim(), [project.format_id]);

  const [segments, setSegments] = useState(30);
  const [chain, setChain] = useState(true);
  const [job, setJob] = useState<MuseTemplateJob | null>(null);
  const [starting, setStarting] = useState(false);
  const [notice, setNotice] = useState("");
  const [logsOpen, setLogsOpen] = useState(false);

  const aliveRef = useRef(true);
  useEffect(() => {
    aliveRef.current = true;
    return () => {
      aliveRef.current = false;
    };
  }, []);

  const active = isMuseTemplateActive(job);
  const jobId = job?.job_id || "";

  /** 5s 轮询（页面隐藏跳过；卸载/换任务时 abort） */
  useEffect(() => {
    if (!jobId || !active) return;
    let alive = true;
    let busy = false;
    const controller = new AbortController();
    const tick = async () => {
      if (busy || document.hidden) return;
      busy = true;
      try {
        const next = await agentApi.museTemplateJob(jobId, controller.signal);
        if (!alive || !aliveRef.current || controller.signal.aborted) return;
        setJob(next);
        if (next.status === "done") notify("Muse 成片已生成", "success");
        if (next.status === "failed") notify("Muse 生成失败", "error");
      } catch (err) {
        if (!alive || !aliveRef.current || controller.signal.aborted) return;
        if (err instanceof ApiError && err.status === 404) {
          setNotice("任务已不存在（后端重启或已清理）");
          setJob((prev) => (prev ? { ...prev, status: "failed" } : prev));
        }
      } finally {
        busy = false;
      }
    };
    const timer = window.setInterval(() => void tick(), 5000);
    return () => {
      alive = false;
      controller.abort();
      window.clearInterval(timer);
    };
  }, [jobId, active, notify]);

  const start = useCallback(
    async (force: boolean) => {
      if (!template) {
        setNotice("当前项目没有节目格式，先回项目大厅选一个模板");
        return;
      }
      const count = Math.max(1, Math.min(120, Math.round(Number(segments) || 30)));
      setStarting(true);
      setNotice("");
      try {
        const res = await agentApi.museTemplate({
          template,
          segments: count,
          chain,
          force,
          assemble: true,
        });
        setJob({
          job_id: res.job_id,
          status: "running",
          stage: "已提交，等待后端开始…",
          logs: [],
          ok_count: 0,
          failed: [],
          final: "",
          error: "",
          template,
        });
        notify(`已开始云端 Muse：${template} · ${count} 段`, "success");
      } catch (err) {
        if (err instanceof ApiError && err.status === 409) {
          // 已有任务在跑：当提示处理，并尝试接管那个任务继续看进度
          const running = runningJobIdFrom409(err.message);
          setNotice("已有 Muse 视频任务在跑" + (running ? `（${running}）` : "") + "，已切到它的进度");
          if (running) {
            setJob({
              job_id: running,
              status: "running",
              stage: "接管已有任务…",
              logs: [],
              ok_count: 0,
              failed: [],
              final: "",
              error: "",
              template,
            });
          }
        } else {
          setNotice("启动失败：" + (err instanceof Error ? err.message : String(err)));
        }
      } finally {
        if (aliveRef.current) setStarting(false);
      }
    },
    [chain, notify, segments, template],
  );

  const finalUrl = museFinalUrl(job?.final || "");
  const done = job?.status === "done";
  const failed = job?.status === "failed";
  const tailLogs = (job?.logs || []).slice(-3);
  const lastStage = job?.stage || job?.logs?.[(job?.logs?.length || 0) - 1] || "";
  const disabled = !template || starting || active;

  return (
    <section className="ui-card muse-gen">
      <div className="ui-card-head">
        <h3 className="ui-card-title">
          云端 Muse 生成 · 每镜按角色
          {template ? <span className="ui-card-sub">{template}</span> : <span className="ui-card-sub is-err">未选择模板</span>}
        </h3>
        {active && (
          <span className="ui-chip is-warn">
            <Spinner size={11} /> 生成中
          </span>
        )}
        {done && <span className="ui-chip is-ok">已完成</span>}
        {failed && <span className="ui-chip is-err">失败</span>}
      </div>

      <p className="muse-gen-desc">
        每段 {SEGMENT_SECONDS} 秒，参考图 = 该句说话人的立绘，段间用上一段最后一帧接首帧。
      </p>

      <div className="muse-gen-controls">
        <label className="muse-gen-field">
          <span>段数</span>
          <input
            type="number"
            min={1}
            max={120}
            value={segments}
            onChange={(event) => setSegments(Number(event.target.value) || 0)}
            disabled={active || starting}
          />
          <em>≈ {Math.max(1, Math.round(Number(segments) || 0)) * SEGMENT_SECONDS} 秒</em>
        </label>

        <label className="muse-gen-switch">
          <input type="checkbox" checked={chain} onChange={(event) => setChain(event.target.checked)} disabled={active || starting} />
          <span>首尾帧衔接</span>
        </label>

        <button type="button" className="ui-btn is-primary" onClick={() => void start(false)} disabled={disabled}>
          {starting ? <Spinner size={12} /> : <IconPlay size={13} />}
          {active ? "生成中…" : "开始生成"}
        </button>

        {!active && jobId && (
          <button type="button" className="ui-btn" onClick={() => void start(true)} disabled={starting}>
            <IconRefresh size={13} /> 重新生成
          </button>
        )}

        {template && (
          <span className="muse-gen-hint">30 段 ≈ 300 秒</span>
        )}
      </div>

      {!template && (
        <p className="muse-gen-notice">
          <IconAlert size={13} /> 当前项目没有节目格式：先在「项目大厅」新建/进入一个带模板的项目，这里才能生成。
        </p>
      )}

      {notice && (
        <p className={"muse-gen-notice" + (failed ? " is-err" : "")}>
          <IconAlert size={13} /> {notice}
        </p>
      )}

      {(active || done || failed) && (
        <div className="muse-gen-status">
          <span className={"muse-gen-dot is-" + (done ? "ok" : failed ? "err" : "run")} />
          <span className="muse-gen-stage" title={lastStage}>
            {lastStage || (active ? "排队中…" : done ? "已完成" : "失败")}
          </span>
          <span className="muse-gen-count">
            {job?.ok_count ?? 0}/{Math.max(1, Number(segments) || 0)}
          </span>
          {tailLogs.length > 0 && (
            <button type="button" className="muse-gen-logs-toggle" onClick={() => setLogsOpen((prev) => !prev)}>
              {logsOpen ? "收起日志" : "看日志"}
            </button>
          )}
        </div>
      )}

      {logsOpen && tailLogs.length > 0 && (
        <pre className="muse-gen-logs">{(job?.logs || []).slice(-8).join("\n")}</pre>
      )}

      {failed && (
        <div className="muse-gen-fail">
          {job?.error && <p>{job.error}</p>}
          {(job?.failed || []).slice(0, 3).map((item) => (
            <p key={item}>{item}</p>
          ))}
        </div>
      )}

      {done && (
        <div className="muse-gen-done">
          {finalUrl ? (
            <a className="ui-btn is-primary" href={finalUrl} target="_blank" rel="noreferrer">
              <IconPlay size={13} /> 播放成片
            </a>
          ) : (
            <span className="ui-btn is-disabled">成片路径未返回</span>
          )}
          <button type="button" className="ui-btn" onClick={() => void start(true)} disabled={starting}>
            <IconRefresh size={13} /> 重新生成
          </button>
          <span className="muse-gen-hint">第 {episode.no || 1} 期 · {template}</span>
        </div>
      )}
    </section>
  );
}
