# -*- coding: utf-8 -*-
"""fix-round3 编排层验证（打桩：不跑 GPU、不写 outputs/）。

覆盖：拓扑排序 / 环与悬空依赖 / 显式 stages 不补依赖 / --with-deps / --start 冲突 /
duration 解析 / run_id 唯一 / stage_params 未知键 / art 默认参数 skip / SystemExit 落 failed /
partial 状态 / dep_assumed 事件 / latest_runs 过滤季记录。

用法：python docs/audit/_tools/fix4_verify_agents.py
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

from pipeline.agents import orchestrator as orch          # noqa: E402
from pipeline.agents.workflow import (DEFAULT_STAGES, SHORTS_STAGES,  # noqa: E402
                                      save_workflow, topo_order)

PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"   [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


class FakeCtx:
    def __init__(self, tid: str):
        self.template_id = tid
        self.duration = 0.0
        self.force = False
        self.prog = None
        self.template = {"label": tid, "duration": 60.0}
        self.run_dir = REPO / "tmp"
        self.paths = {"out": str(REPO / "tmp" / "fake" / f"{tid}.mp4"),
                      "out_dir": str(REPO / "tmp" / "fake"),
                      "workdir": str(REPO / "tmp" / "fake" / "work")}
        self.logs = []

    def log(self, msg):
        self.logs.append(str(msg))


CALLS: list[str] = []
TOOL_RAISE: Exception | None = None


def install(wf: dict) -> None:
    """把 run_template 的外部依赖全换成桩。"""
    orch.load_workflow = lambda tid: json.loads(json.dumps(wf))
    orch.new_context = lambda tid, **kw: FakeCtx(tid)

    def _call(name, ctx, params):
        CALLS.append(name)
        if TOOL_RAISE is not None:
            raise TOOL_RAISE
        return {"ok": True}
    orch.call_tool = _call


def wf_of(stages):
    return {"version": 2, "id": "demo", "name": "demo", "stack": "v5_render",
            "stages": json.loads(json.dumps(stages))}


def main() -> int:
    print("== 1. 稳定拓扑排序（同层保持原顺序） ==")
    jumbled = [
        {"id": "shots", "tool": "render_shots", "depends": ["story"], "enabled": True, "params": {}},
        {"id": "story", "tool": "build_story", "depends": [], "enabled": True, "params": {}},
        {"id": "voice", "tool": "synthesize_voices", "depends": ["story"], "enabled": True, "params": {}},
    ]
    order = [s["id"] for s in topo_order(jumbled)]
    check("调换顺序后按依赖执行（story 先于 shots/voice，同层保持 shots→voice）",
          order == ["story", "shots", "voice"], str(order))
    real = [s["id"] for s in topo_order(DEFAULT_STAGES)]
    check("12 阶段 V5 工作流顺序不变", real == [s["id"] for s in DEFAULT_STAGES], str(real))

    print("\n== 2. 悬空依赖 / 环 → 可读 ValueError ==")
    try:
        topo_order([{"id": "a", "depends": ["ghost"]}])
        check("悬空依赖报错", False)
    except ValueError as exc:
        check("悬空依赖报错", "依赖不存在的阶段" in str(exc), str(exc))
    try:
        topo_order([{"id": "a", "depends": ["b"]}, {"id": "b", "depends": ["a"]}])
        check("环检测", False)
    except ValueError as exc:
        check("环检测", "环" in str(exc), str(exc))

    print("\n== 3. 显式 stages 默认不补依赖 / --with-deps 恢复 ==")
    only_pub = [s["id"] for s in orch._expand(SHORTS_STAGES, ["publish"], None)]
    check("--stages publish 只跑 publish（不再拉起 GPU shots）", only_pub == ["publish"], str(only_pub))
    with_deps = [s["id"] for s in orch._expand(SHORTS_STAGES, ["publish"], None, with_deps=True)]
    check("--with-deps 恢复依赖闭包",
          with_deps == ["story", "shots", "qc", "publish"], str(with_deps))
    only_muse = [s["id"] for s in orch._expand(SHORTS_STAGES, ["muse"], None)]
    check("--stages muse 仍是指定阶段（depends 只用于排序）", only_muse == ["muse"], str(only_muse))

    print("\n== 4. --start 与 --stages 同时给 → 报错；未知阶段报错 ==")
    try:
        orch._expand(SHORTS_STAGES, ["qc"], "shots")
        check("start+stages 冲突报错", False)
    except ValueError as exc:
        check("start+stages 冲突报错", "不能同时指定" in str(exc), str(exc))
    try:
        orch._expand(SHORTS_STAGES, ["shotz"], None)
        check("未知阶段报错", False)
    except ValueError as exc:
        check("未知阶段报错", "未知阶段" in str(exc), str(exc))

    print("\n== 5. duration：None → 模板 duration → 40 ==")
    root = Path(tempfile.mkdtemp(prefix="fix4_dur_"))
    install(wf_of([{"id": "a", "tool": "ta", "depends": [], "enabled": True, "params": {}}]))
    run = orch.run_template("demo", duration=None, run_root=root)
    ev = [json.loads(x) for x in (run.run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    check("模板 duration=60 生效（原来恒 40）", ev[0]["duration"] == 60.0, str(ev[0]["duration"]))
    run2 = orch.run_template("demo", duration=25.0, run_root=root)
    ev2 = [json.loads(x) for x in (run2.run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    check("显式 --duration 优先", ev2[0]["duration"] == 25.0, str(ev2[0]["duration"]))

    print("\n== 6. run_id 唯一（同秒两次 run 不撞车） ==")
    a = orch.run_template("demo", run_root=root)
    b = orch.run_template("demo", run_root=root)
    check("同秒两次 run_id 不同", a.run_id != b.run_id, f"{a.run_id} vs {b.run_id}")
    check("两个 run 目录都在", a.run_dir.is_dir() and b.run_dir.is_dir())
    check("run_id 带 8 位随机后缀",
          len(a.run_id.rsplit("_", 1)[-1]) == 8 and a.run_id.rsplit("_", 1)[-1] != "demo")

    print("\n== 7. stage_params 未知阶段 → ValueError，且不留 run 目录 ==")
    before = set(p.name for p in root.iterdir())
    try:
        orch.run_template("demo", stage_params={"shotz": {"x": 1}}, run_root=root)
        check("未知 stage_params 报错", False)
    except ValueError as exc:
        check("未知 stage_params 报错", "未知阶段" in str(exc), str(exc))
    check("报错后没有新建 run 目录", set(p.name for p in root.iterdir()) == before)

    print("\n== 8. art 默认参数 skip；必需阶段被 skip → partial ==")
    CALLS.clear()
    art_wf = wf_of([
        {"id": "story", "tool": "build_story", "depends": [], "enabled": True,
         "optional": False, "params": {}},
        {"id": "art", "tool": "generate_art", "depends": ["story"], "enabled": True,
         "optional": True, "params": {"character": "", "poses": []}},
    ])
    install(art_wf)
    run3 = orch.run_template("demo", run_root=root)
    art_rec = [s for s in run3.stages if s["id"] == "art"][0]
    check("art 空 character → skipped（不再 ValueError）",
          art_rec["status"] == "skipped", art_rec["error"])
    check("art 是 optional → run 仍 ok", run3.status == "ok", run3.status)

    storyboard_wf = wf_of([
        {"id": "story", "tool": "build_story", "depends": [], "enabled": True, "optional": False, "params": {}},
        {"id": "sb", "tool": "build_storyboard", "depends": ["story"], "enabled": False,
         "optional": False, "params": {}},
    ])
    install(storyboard_wf)
    run4 = orch.run_template("demo", run_root=root)
    check("必需阶段被 skip → status=partial", run4.status == "partial", run4.status)
    check("to_dict 带 skipped 计数", run4.to_dict()["skipped"] == 1, str(run4.to_dict()["skipped"]))
    check("summary.md 标出 partial", "partial" in (run4.run_dir / "summary.md").read_text(encoding="utf-8"))

    print("\n== 9. dep_assumed 事件（显式 stages 的依赖被视作已满足） ==")
    install(wf_of(SHORTS_STAGES))
    run5 = orch.run_template("demo", stages=["publish"], run_root=root)
    ev5 = [json.loads(x) for x in (run5.run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    assumed = {e["dep"]: e["stages"] for e in ev5 if e["kind"] == "dep_assumed"}
    check("记 dep_assumed 事件（publish 的直接依赖 qc 视为外部已满足）",
          assumed.get("qc") == ["publish"], str(assumed))
    check("只跑了 publish 一个工具", CALLS[-1:] == ["write_shorts_report"], str(CALLS[-3:]))
    check("run 状态 ok（依赖视为外部已满足）", run5.status == "ok", run5.status)

    print("\n== 10. 工具抛 SystemExit → failed 证据落盘（不再停在 running） ==")
    global TOOL_RAISE
    TOOL_RAISE = SystemExit(2)
    install(wf_of([{"id": "a", "tool": "ta", "depends": [], "enabled": True,
                    "optional": False, "params": {}}]))
    run6 = orch.run_template("demo", run_root=root)
    TOOL_RAISE = None
    rj = json.loads((run6.run_dir / "run.json").read_text(encoding="utf-8"))
    check("run.status=failed", rj["status"] == "failed", rj["status"])
    check("run.json 记录 SystemExit", "SystemExit" in str(rj["stages"][0]["error"]), rj["stages"][0]["error"])
    check("summary.md 已写", (run6.run_dir / "summary.md").is_file())

    print("\n== 11. latest_runs：过滤季记录、按 started 排序 ==")
    far = Path(tempfile.mkdtemp(prefix="fix4_latest_"))
    orch.RUN_ROOT = far          # 隔离，不碰真实 outputs
    for name, started, run_id in (("20260101_000000_x", "2026-01-01 00:00:00", "r1"),
                                  ("20260102_000000_x", "2026-01-02 00:00:00", "r2")):
        d = far / name
        d.mkdir(parents=True)
        (d / "run.json").write_text(json.dumps(
            {"run_id": run_id, "template": "demo", "status": "ok", "started": started}),
            encoding="utf-8")
    season = far / "season_demo_20260103_000000"
    season.mkdir(parents=True)
    (season / "run.json").write_text(json.dumps(
        {"season": "demo", "status": "ok"}), encoding="utf-8")     # 没有 run_id
    rows = orch.latest_runs(None, 10)
    check("季记录被过滤（run_id 为空的不进列表）",
          [r["run_id"] for r in rows] == ["r2", "r1"], str([r.get("run_id") for r in rows]))

    print("\n== 12. save_workflow：环 / stack 值域 → ValueError ==")
    tmpdir = Path(tempfile.mkdtemp(prefix="fix4_wf_"))
    import pipeline.agents.workflow as W
    old_dir = W.WORKFLOW_DIR
    W.WORKFLOW_DIR = tmpdir
    try:
        for name, wf in {
            "环": {"id": "zzz_a", "stack": "v5_render", "stages": [
                {"id": "a", "tool": "build_story", "depends": ["b"]},
                {"id": "b", "tool": "build_story", "depends": ["a"]}]},
            "stack 非法": {"id": "zzz_a", "stack": "v9_whatever", "stages": [
                {"id": "a", "tool": "build_story", "depends": []}]},
        }.items():
            try:
                save_workflow(wf)
                check(f"save_workflow 拦 {name}", False)
            except ValueError as exc:
                check(f"save_workflow 拦 {name}", True, str(exc))
        ok = W.save_workflow({"id": "zzz_a", "stack": "hq_shorts", "stages": [
            {"id": "a", "tool": "render_2d_hq", "depends": []}]})
        check("合法 workflow 能保存", ok.is_file())
        check("写盘带尾换行", ok.read_bytes().endswith(b"\n"))
    finally:
        W.WORKFLOW_DIR = old_dir

    print(f"\n== 结果：PASS {len(PASS)} / FAIL {len(FAIL)} ==")
    if FAIL:
        print("FAILED:", FAIL)
    shutil.rmtree(root, ignore_errors=True)
    shutil.rmtree(far, ignore_errors=True)
    shutil.rmtree(tmpdir, ignore_errors=True)
    return 0 if not FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())
