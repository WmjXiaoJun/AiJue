// V6 智能体面板：工作流编排/运行/修改/运行记录/美术资产（/api/v6/agent/*）
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  agentapi,
  errorText,
  type AgentArt,
  type AgentEditResult,
  type AgentJob,
  type AgentOverview,
  type AgentStage,
  type AgentTemplate,
  type AgentWorkflow,
} from "../../lib/agent";
import { mediaUrl } from "../../lib/api";
import { useToast } from "../Toast";
import { Spinner } from "../ui";

const OP_OPTIONS = [
  { value: "set_voice", label: "改声音（set_voice）" },
  { value: "set_pose", label: "改姿态（set_pose）" },
  { value: "patch_editorial", label: "改剪辑档案（patch_editorial）" },
  { value: "patch_template", label: "改模板（patch_template）" },
  { value: "patch_workflow", label: "改工作流（patch_workflow）" },
];

const OP_HELP: Record<string, string> = {
  set_voice: '{"character":"ali","value":"legacy_v4_ali"}',
  set_pose: '{"character":"zhuge","pose":"point","enabled":false}',
  patch_editorial: '{"transition_policy":{"hard_cut_ratio":0.45}}',
  patch_template: '{"style":"ink_chronicle"}',
  patch_workflow: '{"enable":["h3"],"params":{"h3":{"hero_shots":2}}}',
};

export function AgentPanel() {
  const notify = useToast();
  const [overview, setOverview] = useState<AgentOverview | null>(null);
  const [sel, setSel] = useState<string>("");
  const [wf, setWf] = useState<AgentWorkflow | null>(null);
  const [dirty, setDirty] = useState(false);
  const [duration, setDuration] = useState(40);
  const [goal, setGoal] = useState("智能体编排：按当前工作流出片");
  const [stageFilter, setStageFilter] = useState("");
  const [jobId, setJobId] = useState("");
  const [job, setJob] = useState<AgentJob | null>(null);
  const [runs, setRuns] = useState<{ jobs: AgentJob[]; disk_runs: Record<string, unknown>[] }>({
    jobs: [],
    disk_runs: [],
  });
  const [art, setArt] = useState<AgentArt | null>(null);
  const [editOp, setEditOp] = useState("set_voice");
  const [editChar, setEditChar] = useState("");
  const [editPose, setEditPose] = useState("point");
  const [editJson, setEditJson] = useState(OP_HELP.patch_editorial);
  const [editResult, setEditResult] = useState<AgentEditResult | null>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const logRef = useRef<HTMLDivElement | null>(null);

  const loadOverview = useCallback(async () => {
    try {
      const res = await agentapi.overview();
      setOverview(res);
      if (!sel && res.templates.length) setSel(res.templates[0].id);
    } catch (err) {
      setError(errorText(err));
    }
  }, [sel]);

  const loadWorkflow = useCallback(async (template: string) => {
    if (!template) return;
    try {
      const res = await agentapi.workflow(template);
      setWf(res.workflow);
      setDirty(false);
    } catch (err) {
      setError(errorText(err));
    }
  }, []);

  const loadRuns = useCallback(async () => {
    try {
      setRuns(await agentapi.runs());
    } catch (err) {
      setError(errorText(err));
    }
  }, []);

  const loadArt = useCallback(async () => {
    try {
      setArt(await agentapi.art());
    } catch (err) {
      setError(errorText(err));
    }
  }, []);

  useEffect(() => {
    void loadOverview();
    void loadRuns();
    void loadArt();
  }, [loadOverview, loadRuns, loadArt]);

  useEffect(() => {
    if (sel) void loadWorkflow(sel);
  }, [sel, loadWorkflow]);

  useEffect(() => {
    if (!jobId) return;
    let stopped = false;
    const tick = async () => {
      try {
        const res = await agentapi.job(jobId);
        if (stopped) return;
        setJob(res);
        if (["ok", "failed", "planned", "error"].includes(res.status)) {
          void loadRuns();
          notify(res.status === "ok" ? "智能体 run 完成" : `run ${res.status}`, res.status === "ok" ? "success" : "error");
          stopped = true;
          window.clearInterval(timer);
        }
      } catch {
        /* 轮询失败忽略 */
      }
    };
    void tick();
    const timer = window.setInterval(() => void tick(), 1200);
    return () => {
      stopped = true;
      window.clearInterval(timer);
    };
  }, [jobId, loadRuns, notify]);

  useEffect(() => {
    if (job?.logs?.length && logRef.current) {
      logRef.current.scrollTop = logRef.current.scrollHeight;
    }
  }, [job?.logs]);

  const template: AgentTemplate | undefined = useMemo(
    () => overview?.templates.find((t) => t.id === sel),
    [overview, sel]
  );

  const updateStage = (index: number, patch: Partial<AgentStage>) => {
    if (!wf) return;
    const stages = wf.stages.map((s, i) => (i === index ? { ...s, ...patch } : s));
    setWf({ ...wf, stages });
    setDirty(true);
  };

  const saveWorkflow = async () => {
    if (!wf || !sel) return;
    setBusy("save");
    try {
      const res = await agentapi.saveWorkflow(sel, wf);
      setDirty(false);
      notify(`工作流已保存：${res.path}`, "success");
      await loadOverview();
    } catch (err) {
      notify(errorText(err), "error");
    } finally {
      setBusy("");
    }
  };

  const startRun = async (dryRun: boolean) => {
    if (!sel) return;
    setBusy(dryRun ? "dry" : "run");
    setJob(null);
    try {
      const stages = stageFilter.trim()
        ? stageFilter.split(",").map((s) => s.trim()).filter(Boolean)
        : null;
      const res = await agentapi.run({
        template: sel,
        duration,
        goal,
        stages,
        dry_run: dryRun,
      });
      setJobId(res.job_id);
      notify(`已启动 ${dryRun ? "试运行" : "智能体运行"}：${res.job_id}`, "success");
      await loadRuns();
    } catch (err) {
      notify(errorText(err), "error");
    } finally {
      setBusy("");
    }
  };

  const applyEdit = async () => {
    if (!sel) return;
    setBusy("edit");
    setError("");
    try {
      let json: Record<string, unknown> | null = null;
      if (editOp === "set_voice") {
        json = { character: editChar, value: (document.getElementById("agent-voice") as HTMLInputElement)?.value || "" };
      } else if (editOp === "set_pose") {
        json = { character: editChar, pose: editPose, enabled: (document.getElementById("agent-pose-on") as HTMLInputElement)?.checked ?? true };
      } else {
        json = JSON.parse(editJson || "{}");
      }
      const res = await agentapi.edit({ template: sel, op: editOp, json, ...json });
      setEditResult(res);
      notify("修改已应用", "success");
      await Promise.all([loadWorkflow(sel), loadArt(), loadOverview()]);
    } catch (err) {
      setEditResult(null);
      setError(errorText(err));
      notify(errorText(err), "error");
    } finally {
      setBusy("");
    }
  };

  const rerunAffected = async () => {
    if (!editResult?.affected_stages?.length || !sel) return;
    setStageFilter(editResult.affected_stages.join(","));
    notify(`已填入受影响阶段：${editResult.affected_stages.join(",")}，点「运行」执行`, "success");
  };

  const togglePose = async (character: string, pose: string, enabled: boolean) => {
    try {
      await agentapi.pose(character, pose, enabled);
      await loadArt();
    } catch (err) {
      notify(errorText(err), "error");
    }
  };

  if (!overview) {
    return (
      <div className="v5-loading">
        <Spinner /> 加载智能体工作台…
      </div>
    );
  }

  return (
    <div className="agent-shell">
      <div className="agent-hero">
        <div>
          <div className="v5-head-kicker">V6 · AGENT ORCHESTRATION</div>
          <h2 className="agent-title">智能体编排台</h2>
          <p className="v5-muted">
            {overview.roles.length} 个角色智能体 · {overview.count} 份模板工作流 · 声音/剪辑/视频/工作流全部可由智能体驱动
          </p>
        </div>
        <div className="agent-role-chips">
          {overview.roles.map((r) => (
            <span className="agent-chip" key={r.id} title={r.desc || ""}>
              {r.emoji || "🤖"} {r.name}
            </span>
          ))}
        </div>
      </div>
      {error ? <div className="v5-banner v5-banner-error">{error}</div> : null}

      <div className="agent-grid">
        <section className="agent-card">
          <header className="agent-card-head">
            <h3>模板工作流</h3>
            <span className="v5-muted">{dirty ? "有未保存修改" : wf ? `${wf.stages.length} 阶段` : ""}</span>
          </header>
          <div className="agent-row">
            <select
              className="agent-input"
              value={sel}
              onChange={(e) => setSel(e.target.value)}
            >
              {overview.templates.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.label || t.id}（{t.style || "-"}）
                </option>
              ))}
            </select>
            <button className="v5-btn" onClick={() => void loadWorkflow(sel)}>重载</button>
            <button className="v5-btn primary" disabled={busy === "save" || !wf} onClick={() => void saveWorkflow()}>
              保存工作流
            </button>
          </div>
          {template?.cast?.length ? (
            <div className="v5-muted">
              cast：
              {template.cast
                .map((c) =>
                  typeof c === "string"
                    ? c
                    : String(
                        (c as Record<string, unknown>).name ||
                          (c as Record<string, unknown>).character ||
                          (c as Record<string, unknown>).id ||
                          ""
                      )
                )
                .filter(Boolean)
                .join(" / ")}
            </div>
          ) : null}
          <div className="agent-stages">
            {wf?.stages.map((stage, index) => (
              <div className={"agent-stage" + (stage.enabled ? "" : " disabled")} key={stage.id}>
                <label className="agent-stage-toggle">
                  <input
                    type="checkbox"
                    checked={stage.enabled !== false}
                    onChange={(e) => updateStage(index, { enabled: e.target.checked })}
                  />
                </label>
                <div className="agent-stage-main">
                  <b>{stage.id}</b>
                  <span className="agent-tag">{stage.agent || "-"}</span>
                  <span className="v5-muted">{stage.tool}</span>
                  {stage.desc ? <span className="v5-muted agent-desc">{stage.desc}</span> : null}
                </div>
                {stage.params && Object.keys(stage.params).length > 0 ? (
                  <code className="agent-params">{JSON.stringify(stage.params)}</code>
                ) : null}
              </div>
            ))}
          </div>
        </section>

        <section className="agent-card">
          <header className="agent-card-head">
            <h3>运行智能体</h3>
            <span className="v5-muted">单并发 / 增量</span>
          </header>
          <div className="agent-field">
            <label>目标</label>
            <input className="agent-input" value={goal} onChange={(e) => setGoal(e.target.value)} />
          </div>
          <div className="agent-row">
            <div className="agent-field small">
              <label>时长(s)</label>
              <input
                className="agent-input"
                type="number"
                min={10}
                max={90}
                value={duration}
                onChange={(e) => setDuration(Number(e.target.value) || 40)}
              />
            </div>
            <div className="agent-field">
              <label>阶段（逗号分隔，可选）</label>
              <input
                className="agent-input"
                placeholder="如 editorial,shots,assemble,qc"
                value={stageFilter}
                onChange={(e) => setStageFilter(e.target.value)}
              />
            </div>
          </div>
          <div className="agent-row">
            <button className="v5-btn" disabled={!!busy} onClick={() => void startRun(true)}>
              试运行（dry-run）
            </button>
            <button className="v5-btn primary" disabled={!!busy} onClick={() => void startRun(false)}>
              开始运行
            </button>
            {jobId ? <span className="v5-muted">job: {jobId}</span> : null}
          </div>
          <div className="agent-status">
            状态：<b>{job?.status || "未启动"}</b>
            {job?.run_dir ? <span className="v5-muted"> · {job.run_dir}</span> : null}
          </div>
          {job?.result?.stages?.length ? (
            <div className="agent-stage-results">
              {job.result.stages.map((s) => (
                <span className={"agent-chip " + (s.status === "ok" ? "ok" : s.status === "failed" ? "bad" : "")} key={s.id}>
                  {s.id}:{s.status}{s.elapsed_sec != null ? ` ${s.elapsed_sec}s` : ""}
                </span>
              ))}
            </div>
          ) : null}
          <div className="agent-logs" ref={logRef}>
            {(job?.logs || []).map((line, i) => (
              <div key={i}>
                <span className="v5-muted">[{line.t}]</span> <b>{line.stage}</b> {line.msg}
              </div>
            ))}
            {!job?.logs?.length ? <div className="v5-muted">运行日志会在这里实时滚动…</div> : null}
          </div>
          {job?.result?.run_id ? (
            <a
              className="v5-link-like"
              href={agentapi.runFileUrl(job.result.run_id, "summary.md")}
              target="_blank"
              rel="noreferrer"
            >
              查看 run 证据 summary.md
            </a>
          ) : null}
        </section>

        <section className="agent-card">
          <header className="agent-card-head">
            <h3>智能体修改</h3>
            <span className="v5-muted">改数据 → 增量重跑</span>
          </header>
          <div className="agent-row">
            <div className="agent-field">
              <label>操作</label>
              <select
                className="agent-input"
                value={editOp}
                onChange={(e) => {
                  setEditOp(e.target.value);
                  setEditJson(OP_HELP[e.target.value] || "{}");
                }}
              >
                {OP_OPTIONS.map((op) => (
                  <option key={op.value} value={op.value}>{op.label}</option>
                ))}
              </select>
            </div>
            <div className="agent-field">
              <label>角色</label>
              <select className="agent-input" value={editChar} onChange={(e) => setEditChar(e.target.value)}>
                <option value="">选择角色…</option>
                {(art?.characters || []).map((c) => (
                  <option key={c.character} value={c.character}>{c.character}</option>
                ))}
              </select>
            </div>
          </div>
          {editOp === "set_voice" ? (
            <div className="agent-field">
              <label>voice preset</label>
              <input id="agent-voice" className="agent-input" placeholder="legacy_v4_ali / strategist_qinglang …" />
            </div>
          ) : null}
          {editOp === "set_pose" ? (
            <div className="agent-row">
              <div className="agent-field">
                <label>姿态</label>
                <select className="agent-input" value={editPose} onChange={(e) => setEditPose(e.target.value)}>
                  <option value="point">point（指点）</option>
                  <option value="think">think（沉思）</option>
                </select>
              </div>
              <label className="agent-check"><input id="agent-pose-on" type="checkbox" defaultChecked /> 启用</label>
            </div>
          ) : null}
          {editOp !== "set_voice" ? (
            <div className="agent-field">
              <label>JSON（{editOp}）</label>
              <textarea
                className="agent-textarea"
                value={editJson}
                onChange={(e) => setEditJson(e.target.value)}
                rows={5}
              />
            </div>
          ) : null}
          <div className="agent-row">
            <button className="v5-btn primary" disabled={busy === "edit"} onClick={() => void applyEdit()}>
              应用修改
            </button>
            {editResult?.affected_stages?.length ? (
              <button className="v5-btn" onClick={() => void rerunAffected()}>填入受影响阶段</button>
            ) : null}
          </div>
          {editResult ? (
            <div className="agent-edit-result">
              <div>
                受影响阶段：<b>{editResult.affected_stages?.join(", ") || "-"}</b>
              </div>
              <code className="agent-params">{editResult.rerun || ""}</code>
            </div>
          ) : null}
        </section>

        <section className="agent-card">
          <header className="agent-card-head">
            <h3>美术资产</h3>
            <span className="v5-muted">{art?.count || 0} 角色</span>
          </header>
          <div className="agent-art-grid">
            {(art?.characters || []).map((c) => (
              <div className="agent-art-item" key={c.character}>
                <img src={mediaUrl(`/media/v5/characters/${c.character}/portrait.png`)} alt={c.character} loading="lazy" />
                <div className="agent-art-name">{c.character}</div>
                <div className="agent-art-poses">
                  <label>
                    <input
                      type="checkbox"
                      checked={c.point && !(art?.policy?.characters?.[c.character]?.disabled || []).includes("point")}
                      onChange={(e) => void togglePose(c.character, "point", e.target.checked)}
                    />
                    point
                  </label>
                  <label>
                    <input
                      type="checkbox"
                      checked={c.think && !(art?.policy?.characters?.[c.character]?.disabled || []).includes("think")}
                      onChange={(e) => void togglePose(c.character, "think", e.target.checked)}
                    />
                    think
                  </label>
                </div>
              </div>
            ))}
          </div>
        </section>

        <section className="agent-card agent-runs">
          <header className="agent-card-head">
            <h3>运行记录</h3>
            <button className="v5-btn" onClick={() => void loadRuns()}>刷新</button>
          </header>
          <div className="agent-run-list">
            {runs.jobs.map((j) => (
              <div className="agent-run-row" key={j.job_id}>
                <span className={"agent-chip " + (j.status === "ok" ? "ok" : j.status === "failed" ? "bad" : "")}>
                  {j.status}
                </span>
                <code>{j.job_id}</code>
                <span className="v5-muted">{j.created}</span>
                <button className="v5-link-like" onClick={() => { setJobId(j.job_id); setJob(null); }}>
                  查看
                </button>
              </div>
            ))}
            {!runs.jobs.length ? <div className="v5-muted">本次进程暂无 run；磁盘 run 见下方。</div> : null}
            {runs.disk_runs.map((r) => {
              const runId = String((r as Record<string, unknown>).run_id || "");
              return (
                <div className="agent-run-row" key={runId}>
                  <span className="agent-chip">disk</span>
                  <code>{runId}</code>
                  <span className="v5-muted">{String((r as Record<string, unknown>).status || "")}</span>
                  <a className="v5-link-like" href={agentapi.runFileUrl(runId, "summary.md")} target="_blank" rel="noreferrer">
                    summary.md
                  </a>
                </div>
              );
            })}
          </div>
        </section>
      </div>
      {busy ? <div className="v5-muted"><Spinner /> {busy}…</div> : null}
    </div>
  );
}
