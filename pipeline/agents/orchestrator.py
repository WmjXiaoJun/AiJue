# -*- coding: utf-8 -*-
"""V6 智能体编排器：按模板工作流逐阶段执行工具，记录 run 证据，支持增量重跑。

fix-round3：
- 阶段按**稳定拓扑序**执行（同层保持合同里的原顺序）；悬空 depends / 重复 id / 环 → ValueError；
- 显式 ``stages`` 默认**不补依赖**（未选中的依赖视为「外部已满足」，记 ``dep_assumed`` 事件）；
  需要旧行为时传 ``with_deps=True``（CLI: ``--with-deps``）；
- ``run_id`` 带 4 字节随机后缀 + ``mkdir(exist_ok=False)`` 重试，避免同模板同秒撞车；
- ``duration=None`` → 模板 duration → 40s（原来形参默认 40.0 让模板值永远失效）；
- 工具抛 ``SystemExit`` 也会落 failed 证据（原来只捕 Exception，证据停在 running）；
- run 状态新增 ``partial``（有**非 optional** 阶段被 skip），``to_dict`` 带 ``skipped`` 计数。
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from .. import config, utils
from .registry import StageContext, call_tool, new_context
from .workflow import load_workflow, topo_order

REPO = Path(config.REPO_ROOT)
RUN_ROOT = Path(config.OUTPUTS_ROOT) / "agent_runs"
DEFAULT_DURATION = 40.0
RUN_ID_RETRIES = 3


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

    def _count(self, status: str) -> int:
        return sum(1 for s in self.stages if s.get("status") == status)

    def to_dict(self) -> dict:
        needs_assemble = any(
            isinstance(s.get("result"), dict) and s["result"].get("needs_assemble")
            for s in self.stages)
        return {"run_id": self.run_id, "template": self.template,
                "run_dir": str(self.run_dir), "status": self.status,
                "started": self.started, "ended": self.ended,
                "goal": self.goal, "workflow": self.workflow.get("name"),
                "stages": self.stages,
                "skipped": self._count("skipped"),
                "failed": self._count("failed"),
                "needs_assemble": needs_assemble,
                "outputs": self.outputs}


def _expand(stages: list[dict], selected: list[str] | None,
            start: str | None, with_deps: bool = False) -> list[dict]:
    """按稳定拓扑序展开要执行的阶段。

    - ``with_deps=False``（默认）：显式 ``selected`` 只跑这些阶段，未选中的依赖视为外部已满足；
    - ``with_deps=True``：把依赖闭包一并补进来（旧行为，可能拉起 GPU 阶段）。
    """
    ordered = topo_order(stages)                 # 重复 id / 悬空依赖 / 环 → ValueError
    ids = [s["id"] for s in ordered]
    if start and selected:
        raise ValueError("--start 与 --stages 不能同时指定（--start 已隐含从该阶段起跑）")
    if start and start not in ids:
        raise ValueError(f"未知起始阶段: {start}；可用: {ids}")
    if selected:
        unknown = [x for x in selected if x not in ids]
        if unknown:
            raise ValueError(f"未知阶段: {unknown}；可用: {ids}")
    if start:
        selected = ids[ids.index(start):]
    if not selected:
        return ordered
    want = set(selected)
    if with_deps:
        changed = True
        while changed:
            changed = False
            for s in ordered:
                if s["id"] in want:
                    for dep in s.get("depends") or []:
                        if dep not in want:
                            want.add(dep)
                            changed = True
    return [s for s in ordered if s["id"] in want]


def _assumed_deps(todo: list[dict]) -> dict[str, list[str]]:
    """没被选中、但被选中阶段依赖的阶段 → {dep: [consumer, ...]}（视为外部已满足）。"""
    todo_ids = {s["id"] for s in todo}
    out: dict[str, list[str]] = {}
    for stage in todo:
        for dep in stage.get("depends") or []:
            if dep not in todo_ids:
                out.setdefault(dep, []).append(stage["id"])
    return out


def _check_stage_params(stage_params: dict | None, stages: list[dict]) -> None:
    if not stage_params:
        return
    known = [s["id"] for s in stages]
    unknown = sorted(str(k) for k in stage_params if str(k) not in known)
    if unknown:
        raise ValueError(f"--stage-params 未知阶段: {unknown}；可用: {known}")


def _make_run_dir(run_root: Path, template_id: str) -> tuple[str, Path]:
    """带随机后缀的 run 目录；同秒并发不会复用别人的目录。"""
    run_root.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    last_exc: Exception | None = None
    for _attempt in range(RUN_ID_RETRIES):
        run_id = f"{stamp}_{template_id}_{os.urandom(4).hex()}"
        candidate = run_root / run_id
        try:
            candidate.mkdir(parents=True, exist_ok=False)
            return run_id, candidate
        except FileExistsError as exc:   # 概率极低；重试换随机后缀
            last_exc = exc
            continue
    raise RuntimeError(f"run 目录连续 {RUN_ID_RETRIES} 次创建失败（{run_root}）: {last_exc}")


def run_template(template_id: str, *, duration: float | None = None, seed: int = 20261101,
                 force: bool = False, stages: list[str] | None = None,
                 start: str | None = None, goal: str = "",
                 dry_run: bool = False, run_root: Path | None = None,
                 stage_params: dict | None = None, with_deps: bool = False,
                 prog=None) -> AgentRun:
    """跑一条模板工作流。

    - ``duration``：None → 模板 ``duration`` → 40.0
    - ``stage_params``：{"muse": {"apply": true}} —— 只覆盖本次运行（不改合同）；未知阶段名报错
    - ``with_deps``：True 时把 ``stages`` 的依赖一并执行（默认 False，依赖只用于排序）
    """
    run_root = Path(run_root or RUN_ROOT)
    workflow = load_workflow(template_id)
    if workflow.get("degraded"):
        # 先拒绝再建目录，避免每次留下空 run 目录
        raise RuntimeError(workflow.get("degraded_reason") or "工作流合同损坏，已拒绝执行")
    stage_list = workflow.get("stages") or []
    # 1) 校验 + 排序全部前置：失败时不留任何 run 目录（不再产出 status=running 的僵尸）
    todo = _expand(stage_list, stages, start, with_deps=with_deps)
    _check_stage_params(stage_params, stage_list)

    # 2) 建上下文（未知模板在这里变成可读的 ValueError，CLI 退出码 2）
    try:
        ctx = new_context(template_id, duration=float(duration) if duration else DEFAULT_DURATION,
                          seed=seed, force=force, run_dir=None, prog=None)
    except KeyError as exc:
        raise ValueError(f"未知模板: {template_id}（不在 v5 catalog 里）") from exc
    tpl_duration = float((ctx.template or {}).get("duration") or 0.0)
    run_duration = float(duration) if duration is not None else (tpl_duration or DEFAULT_DURATION)
    ctx.duration = run_duration          # 工具读的是 ctx.duration

    # 3) run 目录：随机后缀 + 独占创建（同秒并发不再撞车）
    run_id, run_dir = _make_run_dir(run_root, template_id)
    ctx.run_dir = run_dir
    events_path = run_dir / "events.jsonl"

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
        try:
            utils.write_json(run_dir / "run.json", run.to_dict(), indent=2)
        except Exception as exc:  # noqa: BLE001 - 证据写失败不能打断渲染
            print(f"[warn] run.json 写入失败: {exc}", flush=True)

    assumed = _assumed_deps(todo)
    event("start", template=template_id, duration=run_duration, goal=goal,
          stages=[s["id"] for s in todo],
          explicit_stages=list(stages or []), with_deps=bool(with_deps),
          stage_params=stage_params or {})
    for dep, consumers in sorted(assumed.items()):
        event("dep_assumed", dep=dep, stages=consumers)
    persist()

    explicit = {str(x).strip() for x in (stages or []) if str(x).strip()}
    done: set[str] = set()
    failed = False
    for stage in todo:
        sid = stage["id"]
        deps = stage.get("depends") or []
        optional = bool(stage.get("optional", False))
        missing = [d for d in deps if d not in done and d not in assumed]
        record = {"id": sid, "agent": stage.get("agent"), "tool": stage.get("tool"),
                  "optional": optional,
                  "status": "pending", "started": "", "elapsed_sec": 0.0,
                  "result": None, "error": ""}
        params = dict(stage.get("params") or {})
        params.update((stage_params or {}).get(sid) or {})
        run.stages.append(record)
        if missing:
            # 悬空/未完成依赖 = 真失败：不能静默 skipped 让 run 报 ok
            record.update(status="failed", error=f"依赖未完成或不存在: {missing}")
            failed = True
            event("stage_fail", stage=sid, error=record["error"])
            persist()
            break
        if not stage.get("enabled", True) and sid not in explicit:
            record.update(status="skipped", error="工作流中已禁用")
            # 禁用 = "这次不跑，视为外部已满足"：加入 done 让下游继续（否则 qc/publish
            # 会报"依赖未完成"直接 failed，与 --stages 的 dep_assumed 语义矛盾）。
            done.add(sid)
            event("skip", stage=sid, reason="disabled", assume_for_dependents=True)
            persist()
            continue
        if stage.get("tool") == "generate_art" and not str(params.get("character") or "").strip():
            # 默认参数（character 为空）下 generate_art 必抛；按 skip 处理，别让一次 enable 就整条 run 失败
            record.update(status="skipped", error="generate_art 未指定 character（默认参数直接跳过）")
            done.add(sid)
            event("skip", stage=sid, reason="art_no_character", assume_for_dependents=True)
            persist()
            continue
        if dry_run:
            record.update(status="planned")
            done.add(sid)
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
            result = call_tool(stage.get("tool") or "", ctx, params)
            record.update(status="ok", result=result, elapsed_sec=round(time.time() - t0, 1))
            done.add(sid)
            event("stage_ok", stage=sid, elapsed=record["elapsed_sec"],
                  result=_short(result))
        except BaseException as exc:  # noqa: BLE001 - SystemExit/KeyboardInterrupt 也要落证据
            import traceback
            record.update(status="failed", error=f"{type(exc).__name__}: {exc}",
                          elapsed_sec=round(time.time() - t0, 1),
                          traceback=traceback.format_exc()[-3000:])
            failed = True
            event("stage_fail", stage=sid, error=str(exc))
            persist()
            if isinstance(exc, KeyboardInterrupt):   # Ctrl-C 仍然要中断，但证据已经落盘
                raise
            break
        persist()

    skipped_required = [s["id"] for s in run.stages
                        if s.get("status") == "skipped" and not s.get("optional")]
    if failed:
        run.status = "failed"
    elif dry_run:
        run.status = "planned"
    elif skipped_required:
        run.status = "partial"          # 有必需阶段被跳过：不是全绿
    else:
        run.status = "ok"
    run.ended = time.strftime("%Y-%m-%d %H:%M:%S")
    if not failed and not dry_run:
        out = Path(ctx.paths["out"])
        out_dir = Path(ctx.paths["out_dir"])
        # 短片栈的成片在 outputs/templates_3d/<tid>/（不是 render_v5 的路径）
        if (workflow.get("stack") == "hq_shorts"
                and (Path(config.OUTPUTS_ROOT) / "templates_3d" / template_id
                     / "final.mp4").is_file()):
            out = Path(config.OUTPUTS_ROOT) / "templates_3d" / template_id / "final.mp4"
            out_dir = out.parent
        run.outputs = {"final": str(out), "exists": out.is_file(),
                       "workdir": str(out_dir / "work"),
                       "out_dir": str(out_dir)}
    persist()
    # summary.md
    flags = []
    if skipped_required:
        flags.append(f"- 必需阶段被跳过: **{skipped_required}**")
    if run.status == "partial":
        flags.append("- 结论: **partial**（有必需阶段没跑，别当全绿）")
    needs_assemble = [s["id"] for s in run.stages
                      if isinstance(s.get("result"), dict) and s["result"].get("needs_assemble")]
    if needs_assemble:
        flags.append(f"- `needs_assemble`: {needs_assemble}（镜头已改但成片未重合成，需再跑 assemble）")
    lines = [f"# Agent Run {run_id}", "",
             f"- 模板: `{template_id}`", f"- 目标: {goal or '（未指定）'}",
             f"- 状态: **{run.status}**", f"- 开始: {run.started}", f"- 结束: {run.ended}"]
    lines += flags
    lines += ["", "| 阶段 | 智能体 | 工具 | 状态 | 耗时(s) |", "|---|---|---|---|---|"]
    for s in run.stages:
        lines.append(f"| {s['id']} | {s.get('agent')} | {s.get('tool')} | "
                     f"{s['status']} | {s.get('elapsed_sec')} |")
    if run.outputs:
        lines += ["", "## 产物", "", f"- 成片: `{run.outputs.get('final')}`",
                  f"- 工作目录: `{run.outputs.get('workdir')}`"]
    run_dir.joinpath("summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    event("end", status=run.status, skipped=len(skipped_required))
    return run


def _short(result, n: int = 240) -> str:
    try:
        s = json.dumps(result, ensure_ascii=False)
    except Exception:  # noqa: BLE001
        s = str(result)
    return s[:n]


def latest_runs(template: str | None = None, limit: int = 10) -> list[dict]:
    """最近的单集 run（季级 run.json 没有 run_id → 过滤掉；按 started 排序）。"""
    if not RUN_ROOT.is_dir():
        return []
    rows = []
    for p in RUN_ROOT.rglob("run.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if not isinstance(data, dict) or not data.get("run_id"):
            continue                     # season_*/run.json 是季记录，不是单集 run
        if template and data.get("template") != template:
            continue
        try:
            mtime = p.stat().st_mtime
        except OSError:
            mtime = 0.0
        rows.append((str(data.get("started") or ""), mtime, data))
    rows.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [data for _started, _mtime, data in rows[:max(0, int(limit))]]


__all__ = ["AgentRun", "run_template", "latest_runs", "RUN_ROOT"]
