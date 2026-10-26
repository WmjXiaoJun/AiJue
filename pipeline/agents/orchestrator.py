# -*- coding: utf-8 -*-
"""V6 智能体编排器：按模板工作流逐阶段执行工具，记录 run 证据，支持增量重跑。"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from .. import config, utils
from .registry import StageContext, call_tool, new_context
from .workflow import load_workflow

REPO = Path(config.REPO_ROOT)
RUN_ROOT = REPO / "outputs" / "agent_runs"


@dataclass
class AgentRun:
    run_id: str
    template: str
    run_dir: Path
    workflow: dict
    stages: list = field(default_factory=list)
    status: str = "running"
    started: str = ""
    ended: str = ""
    goal: str = ""
    outputs: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"run_id": self.run_id, "template": self.template,
                "run_dir": str(self.run_dir), "status": self.status,
                "started": self.started, "ended": self.ended,
                "goal": self.goal, "workflow": self.workflow.get("name"),
                "stages": self.stages, "outputs": self.outputs}


def _expand(stages: list[dict], selected: list[str] | None,
            start: str | None) -> list[dict]:
    ids = [s["id"] for s in stages]
    if start and start in ids:
        selected = ids[ids.index(start):]
    if not selected:
        return stages
    want = set(selected)
    # 自动补依赖
    changed = True
    while changed:
        changed = False
        for s in stages:
            if s["id"] in want:
                for dep in s.get("depends") or []:
                    if dep not in want:
                        want.add(dep)
                        changed = True
    return [s for s in stages if s["id"] in want]


def run_template(template_id: str, *, duration: float = 40.0, seed: int = 20261101,
                 force: bool = False, stages: list[str] | None = None,
                 start: str | None = None, goal: str = "",
                 dry_run: bool = False, run_root: Path | None = None,
                 prog=None) -> AgentRun:
    run_root = Path(run_root or RUN_ROOT)
    run_root.mkdir(parents=True, exist_ok=True)
    run_id = time.strftime("%Y%m%d_%H%M%S") + f"_{template_id}"
    run_dir = run_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    events_path = run_dir / "events.jsonl"
    workflow = load_workflow(template_id)
    ctx = new_context(template_id, duration=duration, seed=seed, force=force,
                      run_dir=run_dir, prog=None)
    if ctx.template.get("duration"):
        duration = float(duration or ctx.template["duration"])
    run = AgentRun(run_id=run_id, template=template_id, run_dir=run_dir,
                   workflow=workflow, started=time.strftime("%Y-%m-%d %H:%M:%S"),
                   goal=goal)
    run_dir.joinpath("workflow.json").write_text(
        json.dumps(workflow, ensure_ascii=False, indent=2), encoding="utf-8")

    def event(kind: str, **extra) -> None:
        rec = {"ts": time.strftime("%H:%M:%S"), "kind": kind, **extra}
        with events_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def persist() -> None:
        run_dir.joinpath("run.json").write_text(
            json.dumps(run.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    event("start", template=template_id, duration=duration, goal=goal,
          stages=[s["id"] for s in (workflow.get("stages") or [])])
    persist()
    todo = _expand(workflow.get("stages") or [], stages, start)
    done: set[str] = set()
    failed = False
    for stage in todo:
        sid = stage["id"]
        deps = stage.get("depends") or []
        missing = [d for d in deps if d not in done]
        record = {"id": sid, "agent": stage.get("agent"), "tool": stage.get("tool"),
                  "status": "pending", "started": "", "elapsed_sec": 0.0,
                  "result": None, "error": ""}
        run.stages.append(record)
        if missing:
            record.update(status="skipped", error=f"依赖未完成: {missing}")
            event("skip", stage=sid, reason=record["error"])
            continue
        if not stage.get("enabled", True):
            record.update(status="skipped", error="工作流中已禁用")
            event("skip", stage=sid, reason="disabled")
            continue
        if dry_run:
            record.update(status="planned")
            event("plan", stage=sid, tool=stage.get("tool"))
            continue
        record["started"] = time.strftime("%H:%M:%S")
        t0 = time.time()
        event("stage_start", stage=sid, agent=stage.get("agent"),
              tool=stage.get("tool"))
        try:
            def stage_prog(msg: str, _sid=sid) -> None:
                event("progress", stage=_sid, msg=str(msg))
                if callable(prog):
                    prog(_sid, msg)
            ctx.prog = stage_prog
            result = call_tool(stage.get("tool") or "", ctx, dict(stage.get("params") or {}))
            record.update(status="ok", result=result, elapsed_sec=round(time.time() - t0, 1))
            done.add(sid)
            event("stage_ok", stage=sid, elapsed=record["elapsed_sec"],
                  result=_short(result))
        except Exception as exc:  # noqa: BLE001
            import traceback
            record.update(status="failed", error=str(exc),
                          elapsed_sec=round(time.time() - t0, 1),
                          traceback=traceback.format_exc()[-3000:])
            failed = True
            event("stage_fail", stage=sid, error=str(exc))
            persist()
            break
        persist()
    run.status = "failed" if failed else ("planned" if dry_run else "ok")
    run.ended = time.strftime("%Y-%m-%d %H:%M:%S")
    if not failed and not dry_run:
        out = Path(ctx.paths["out"])
        run.outputs = {"final": str(out), "exists": out.is_file(),
                       "workdir": str(ctx.paths["workdir"]),
                       "out_dir": str(ctx.paths["out_dir"])}
    persist()
    # summary.md
    lines = [f"# Agent Run {run_id}", "",
             f"- 模板: `{template_id}`", f"- 目标: {goal or '（未指定）'}",
             f"- 状态: **{run.status}**", f"- 开始: {run.started}", f"- 结束: {run.ended}",
             "", "| 阶段 | 智能体 | 工具 | 状态 | 耗时(s) |", "|---|---|---|---|---|"]
    for s in run.stages:
        lines.append(f"| {s['id']} | {s.get('agent')} | {s.get('tool')} | "
                     f"{s['status']} | {s.get('elapsed_sec')} |")
    if run.outputs:
        lines += ["", "## 产物", "", f"- 成片: `{run.outputs.get('final')}`",
                  f"- 工作目录: `{run.outputs.get('workdir')}`"]
    run_dir.joinpath("summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    event("end", status=run.status)
    return run


def _short(result, n: int = 240) -> str:
    try:
        s = json.dumps(result, ensure_ascii=False)
    except Exception:  # noqa: BLE001
        s = str(result)
    return s[:n]


def latest_runs(template: str | None = None, limit: int = 10) -> list[dict]:
    if not RUN_ROOT.is_dir():
        return []
    runs = []
    for p in sorted(RUN_ROOT.glob("*/run.json"), reverse=True):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if template and data.get("template") != template:
            continue
        runs.append(data)
        if len(runs) >= limit:
            break
    return runs


__all__ = ["AgentRun", "run_template", "latest_runs", "RUN_ROOT"]
