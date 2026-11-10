# -*- coding: utf-8 -*-
"""第三轮审查探针（只读 + 打桩，不跑 GPU、不改 contract/）。

用法: python docs/audit/_tools/r3_probe.py
覆盖:
  1) _expand 顺序依赖（无拓扑排序）
  2) duration 覆盖死代码
  3) 工具抛 SystemExit 时 run 证据停摆
  4) art.generate_art 默认参数即失败
  5) orchestrator 硬编码 repo/outputs vs config.OUTPUTS_ROOT
  6) write_shorts_report 对「成片不存在」仍报 qc_ok
  7) evaluate_hq_qc 时长阈值（0.6x 且无上限）
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
os.chdir(REPO)

from pipeline.agents import orchestrator as orch          # noqa: E402
from pipeline.agents import registry as reg              # noqa: E402
from pipeline.agents.workflow import SHORTS_STAGES       # noqa: E402
from pipeline import config as pcfg                      # noqa: E402


class FakeCtx:
    def __init__(self, tid):
        self.template_id = tid
        self.prog = None
        self.template = {}
        self.force = False
        self.duration = None
        self.paths = {"out": str(REPO / "outputs" / "fake" / f"{tid}_final.mp4"),
                      "out_dir": str(REPO / "outputs" / "fake"),
                      "workdir": str(REPO / "outputs" / "fake" / "work")}
        self.run_dir = REPO / "tmp"


CALLS: list = []


def _install(wf: dict, tool_fn=None):
    orch.new_context = lambda tid, **kw: _ctx_holder.setdefault(tid, FakeCtx(tid))
    orch.load_workflow = lambda tid: json.loads(json.dumps(wf))

    def _call(name, ctx, params):
        CALLS.append((name, dict(params)))
        if tool_fn:
            return tool_fn(name, ctx, params)
        return {"ok": True}
    orch.call_tool = _call


_ctx_holder: dict = {}


def show(tag, run):
    print(f"   [{tag}] status={run.status} run_id={run.run_id}")
    print(f"        stages={[(s['id'], s['status'], (s.get('error') or '')[:46]) for s in run.stages]}")


def case1_order():
    print("\n== 1. _expand 不做拓扑排序：UI 调换顺序后依赖未完成 → failed ==")
    wf = {"version": 2, "id": "orddemo", "stack": "v5_render", "stages": [
        # 故意把 shots 放到 story 前面（页面工作流编辑器允许拖顺序）
        {"id": "shots", "tool": "render_shots", "depends": ["story"], "enabled": True, "params": {}},
        {"id": "story", "tool": "build_story", "depends": [], "enabled": True, "params": {}},
    ]}
    CALLS.clear()
    _install(wf)
    r = orch.run_template("orddemo", run_root=Path(tempfile.mkdtemp(prefix="r3a_")))
    show("reorder", r)
    print("        tool 实际调用:", [c[0] for c in CALLS], "（render_shots 一次都没跑，先撞依赖检查）")
    print("        _expand 结果:", [s["id"] for s in orch._expand(wf["stages"], None, None)])


def case2_duration():
    print("\n== 2. duration 覆盖是死代码（orchestrator.py:85-86） ==")
    wf = {"version": 2, "id": "durdemo", "stack": "v5_render", "stages": [
        {"id": "a", "tool": "ta", "depends": [], "enabled": True, "params": {}}]}
    _ctx_holder.clear()
    _install(wf)
    seen = {}
    ctx = FakeCtx("durdemo")
    ctx.template = {"duration": 60.0}          # 模板自带 60s

    def _nc(tid, **kw):
        seen["duration"] = kw.get("duration")
        ctx.duration = kw.get("duration")
        return ctx
    orch.new_context = _nc
    run = orch.run_template("durdemo", duration=40.0, run_root=Path(tempfile.mkdtemp(prefix="r3b_")))
    ev = [json.loads(l) for l in (run.run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    print(f"   模板 duration=60, CLI --duration=40 -> new_context(duration={seen['duration']})"
          f" | 事件记录 duration={ev[0]['duration']}  （模板 60 永不生效）")
    run2 = orch.run_template("durdemo", duration=0.0, run_root=Path(tempfile.mkdtemp(prefix="r3c_")))
    ev2 = [json.loads(l) for l in (run2.run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    print(f"   --duration=0 -> new_context(duration={seen['duration']})"
          f" | 事件记录 duration={ev2[0]['duration']}  （记录写 60，工具拿 0 → 证据与执行不一致）")


def case3_systemexit():
    print("\n== 3. 工具抛 SystemExit：orchestrator 只 except Exception → 证据停摆 ==")
    wf = {"version": 2, "id": "sedemo", "stack": "v5_render", "stages": [
        {"id": "a", "tool": "ta", "depends": [], "enabled": True, "params": {}},
        {"id": "b", "tool": "tb", "depends": ["a"], "enabled": True, "params": {}}]}
    _ctx_holder.clear()
    _install(wf, tool_fn=lambda n, c, p: (_ for _ in ()).throw(SystemExit(3)) if n == "ta" else {"ok": True})
    root = Path(tempfile.mkdtemp(prefix="r3d_"))
    try:
        orch.run_template("sedemo", run_root=root)
        print("   未抛异常")
    except BaseException as exc:
        d = next(root.iterdir())
        runj = json.loads((d / "run.json").read_text(encoding="utf-8"))
        print(f"   run_template 抛出 {type(exc).__name__}({exc})")
        print(f"   run.json.status = {runj['status']} | summary.md 存在 = {(d/'summary.md').is_file()}"
              f" | 阶段记录 = {runj['stages']}")


def case4_generate_art():
    print("\n== 4. 启用 art 阶段（默认 params.character='') → generate_art 直接 ValueError ==")
    try:
        reg.TOOLS["generate_art"]["fn"] = None
        reg.call_tool("generate_art", FakeCtx("tonight_show"), {})
    except Exception as exc:
        print(f"   {type(exc).__name__}: {exc}")


def case5_outputs_root():
    print("\n== 5. 输出根口径（R2#1 残项） ==")
    os.environ["ZAOPIANJU_OUTPUTS_DIR"] = str(REPO / "tmp" / "alt_out")
    import importlib
    importlib.reload(pcfg)
    from pipeline.agents import registry as reg2
    importlib.reload(reg2)
    import pipeline.templates3d as t3
    print(f"   config.OUTPUTS_ROOT = {pcfg.OUTPUTS_ROOT}")
    print(f"   registry._outputs_root() = {reg2._outputs_root()}")
    print(f"   templates3d.OUT3D        = {t3.OUT3D}")
    print(f"   orchestrator 里的硬编码  = {REPO / 'outputs' / 'templates_3d' / '<tid>' / 'final.mp4'}"
          f"  (orchestrator.py:169-172)")
    print(f"   一致? {reg2._outputs_root() / 'templates_3d' == t3.OUT3D}")
    os.environ.pop("ZAOPIANJU_OUTPUTS_DIR", None)
    importlib.reload(pcfg)
    importlib.reload(reg2)


def case6_stale_qc():
    print("\n== 6. write_shorts_report：成片不在了但 qc_shorts.json 说 ok ==")
    tid = "custom_tpl_v46_cyber"
    out = pcfg.OUTPUTS_ROOT / "templates_3d" / tid
    final = out / "final.mp4"
    qc = json.loads((out / "qc_shorts.json").read_text(encoding="utf-8"))
    ctx = FakeCtx(tid)
    ctx.run_dir = Path(tempfile.mkdtemp(prefix="r3f_"))
    ctx.template = {"label": "赛博 2D"}
    res = reg.write_shorts_report(ctx, {})
    print(f"   磁盘: final.mp4 存在={final.is_file()} | qc_shorts.json ok={qc.get('ok')}")
    print(f"   报告: qc_ok={res['qc_ok']} duration={res['duration']} resolution={res['resolution']}")
    print("   → 证据文件是绿的，成片是没的")


def case7_qc_tolerance():
    print("\n== 7. evaluate_hq_qc 时长阈值 ==")
    src = (REPO / "pipeline" / "agents" / "registry.py").read_text(encoding="utf-8")
    line = [l for l in src.splitlines() if "target * 0.6" in l][0]
    print("   代码:", line.strip())
    for target in (14.12, 19.14, 23.45):
        print(f"   target={target}s -> 只要 >= {target * 0.6:.2f}s 就过；无上限（40s 也过）")


if __name__ == "__main__":
    case1_order()
    case2_duration()
    case3_systemexit()
    case4_generate_art()
    case5_outputs_root()
    case6_stale_qc()
    case7_qc_tolerance()
