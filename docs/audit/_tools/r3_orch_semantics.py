# -*- coding: utf-8 -*-
import json, sys, tempfile, time
from pathlib import Path
REPO = Path(r"D:\BaiduSyncdisk\wmj_work\XJ\AiJue")
sys.path.insert(0, str(REPO))
from pipeline.agents import orchestrator as orch

class FakeCtx:
    def __init__(self, tid):
        self.template_id = tid; self.prog = None
        self.template = {}; self.force = False
        self.paths = {"out": str(REPO/"outputs"/"fake"/f"{tid}_final.mp4"),
                      "out_dir": str(REPO/"outputs"/"fake"),
                      "workdir": str(REPO/"outputs"/"fake"/"work")}
        self.run_dir = REPO/"tmp"

orch.new_context = lambda tid, **kw: FakeCtx(tid)
CALLS = []
def fake_call_tool(name, ctx, params):
    CALLS.append((name, dict(params)))
    return {"ok": True}
orch.call_tool = fake_call_tool

WF = {"version": 2, "id": "demo", "name": "demo", "stack": "v5_render", "stages": [
    {"id": "a", "agent": "r1", "tool": "ta", "depends": [], "enabled": True, "params": {"x": 1, "y": 2}},
    {"id": "b", "agent": "r2", "tool": "tb", "depends": ["a"], "enabled": True, "params": {}},
    {"id": "c", "agent": "r3", "tool": "tc", "depends": ["b"], "enabled": False, "params": {}},
    {"id": "d", "agent": "r4", "tool": "td", "depends": ["c"], "enabled": True, "params": {}},
]}
orch.load_workflow = lambda tid: json.loads(json.dumps(WF))

root = Path(tempfile.mkdtemp(prefix="audit_runs_"))

def show(tag, run):
    print(f"--- {tag}")
    print("   status =", run.status, "| run_id =", run.run_id)
    print("   stages =", [(s["id"], s["status"], (s.get("error") or "")[:60]) for s in run.stages])
    print("   tool calls =", [c[0] for c in CALLS])
    CALLS.clear()

print("== A. disabled stage in the middle of the chain (c disabled, d enabled) ==")
r = orch.run_template("demo", run_root=root); show("full run", r)
print("   summary.md 存在:", (r.run_dir/"summary.md").is_file(), "| run.json 状态:",
      json.loads((r.run_dir/"run.json").read_text(encoding="utf-8"))["status"])

print("\n== B. dry_run 是否写文件/建目录 ==")
before = set(p.name for p in root.iterdir())
r = orch.run_template("demo", dry_run=True, run_root=root); show("dry-run", r)
after = set(p.name for p in root.iterdir())
print("   新建目录:", sorted(after-before))
for nm in ("run.json","events.jsonl","summary.md","workflow.json"):
    p = r.run_dir/nm
    print(f"   {nm}: exists={p.is_file()} bytes={p.stat().st_size if p.is_file() else 0}")

print("\n== C. stage_params 合并语义（浅更新 vs 深合并） ==")
WF["stages"][0]["params"] = {"x": 1, "nested": {"k1": 1, "k2": 2}}
r = orch.run_template("demo", stages=["a"], stage_params={"a": {"nested": {"k2": 9}}}, run_root=root)
print("   传入 params:", CALLS[-1][1] if CALLS else None)
print("   （深合并应得 {'x':1,'nested':{'k1':1,'k2':9}}；浅更新会丢 k1）")

print("\n== D. stage_params 里写错阶段名 ==")
CALLS.clear()
r = orch.run_template("demo", stages=["a"], stage_params={"aa": {"x": 999}}, run_root=root)
print("   status =", r.status, "| 实际 params =", CALLS[-1][1] if CALLS else None, "（无告警）")

print("\n== E. 显式指定 disabled 阶段会执行 ==")
CALLS.clear()
r = orch.run_template("demo", stages=["c"], run_root=root); show("stages=[c]", r)

print("\n== F. run_id 同秒冲突 ==")
r1 = orch.run_template("demo", stages=["a"], run_root=root)
r2 = orch.run_template("demo", stages=["a"], run_root=root)
print("   run_id1 =", r1.run_id, "| run_id2 =", r2.run_id, "| same dir =", r1.run_dir == r2.run_dir)

print("\n== G. --start 与 --stages 同时给 ==")
CALLS.clear()
r = orch.run_template("demo", stages=["a"], start="b", run_root=root); show("stages=[a], start=b", r)
print("   注意：a 被 start 覆盖后由依赖闭包补回；若 start 更靠后则 --stages 被静默丢弃")

print("\n== H. muse 阶段 depends=[] 时 --stages muse 展开 ==")
from pipeline.agents.workflow import SHORTS_STAGES
print("   _expand(SHORTS_STAGES, ['muse']) =", [s['id'] for s in orch._expand(SHORTS_STAGES, ['muse'], None)])
print("   _expand(SHORTS_STAGES, ['qc'])   =", [s['id'] for s in orch._expand(SHORTS_STAGES, ['qc'], None)])
print("   _expand(SHORTS_STAGES, ['publish']) =", [s['id'] for s in orch._expand(SHORTS_STAGES, ['publish'], None)])
print("\nroot =", root)
