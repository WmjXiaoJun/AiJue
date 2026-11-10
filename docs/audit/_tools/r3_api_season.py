# -*- coding: utf-8 -*-
import json, sys, time
from pathlib import Path
REPO = Path(r"D:\BaiduSyncdisk\wmj_work\XJ\AiJue")
sys.path.insert(0, str(REPO))
from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend.app.routers import v6_agent as V

app = FastAPI(); app.include_router(V.router)
cli = TestClient(app)

print("== A. GET /api/v6/agent/series (real) ==")
r = cli.get("/api/v6/agent/series"); d = r.json()
s = d["series"][0]
print("   status", r.status_code, "| count", d["count"], "| id", s["id"], "| members", len(s["members"]),
      "| ready_count", s["ready_count"], "| warnings", s["season_warnings"])
print("   member[0]:", {k: s["members"][0][k] for k in ("template","label","stack","ready","stages","last_run")})

print("\n== B. season sid 路径穿越（Windows 反斜杠） ==")
for sid in ("..%5C..%5Ccontract%5Cv6%5Cart%5Cpose_policy", "..%2F..%2Fcontract%2Fv6%2Fart%2Fpose_policy"):
    r = cli.get(f"/api/v6/agent/season/{sid}/status")
    print(f"   sid={sid[:46]:46s} -> {r.status_code} {str(r.json())[:120]}")

print("\n== C. /media/agent_runs run_id 长度闸门 ==")
for rid in ("20261106_232732_demo", "20261105_160602_custom_tpl_v46_cyber",
            "20261105_112758_custom_tpl_v46_duologue", "20261105_112758_custom_tpl_v45_soft_watercolor"):
    r = cli.get(f"/media/agent_runs/{rid}/run.json")
    print(f"   {rid:45s} len={len(rid):3d} -> {r.status_code}")

print("\n== D. 打桩 run_template 后测季生产语义 ==")
REC = []
class FakeRun:
    def __init__(self, tid, dry_run):
        self.run_id = "run_" + tid[-8:]; self.run_dir = REPO/"outputs"/"agent_runs"/self.run_id
        self.status = "planned" if dry_run else "ok"
        self.stages = [{"id": "muse", "status": "skipped"}, {"id": "qc", "status": "ok"}]
    def to_dict(self): return {"run_id": self.run_id, "status": self.status}
def fake_run_template(tid, **kw):
    REC.append({"template": tid, **{k: v for k, v in kw.items() if k in
               ("dry_run","stages","stage_params","force","run_root","duration","goal")}})
    return FakeRun(tid, kw.get("dry_run"))
V.run_template = fake_run_template

def wait_job(job_id):
    for _ in range(200):
        j = cli.get(f"/api/v6/agent/runs/{job_id}").json()
        if j.get("status") not in ("queued","running"): return j
        time.sleep(0.05)
    return j

REC.clear()
r = cli.post("/api/v6/agent/season/undercity_arc/run", json={})
print("   D1 默认 payload ->", r.status_code, {k: r.json().get(k) for k in ("job_id","episodes","dry_run","with_muse")})
j = wait_job(r.json()["job_id"]); print("      job.status =", j.get("status"), "| season_run =", (j.get("result") or {}).get("season_run"))
print("      run_template 收到:", [{k: v for k, v in x.items() if k in ("template","dry_run","stages","stage_params")} for x in REC])
print("      季目录内容:", sorted(p.name for p in Path(j["run_dir"]).iterdir()))

REC.clear()
r = cli.post("/api/v6/agent/season/undercity_arc/run", json={"with_muse": True})
print("\n   D2 with_muse=true ->", r.status_code, {k: r.json().get(k) for k in ("episodes","dry_run","with_muse")})
j = wait_job(r.json()["job_id"])
print("      job.status =", j.get("status"))
print("      run_template 收到:", [{k: v for k, v in x.items() if k in ("template","dry_run","stages","stage_params")} for x in REC])

REC.clear()
r = cli.post("/api/v6/agent/season/undercity_arc/run", json={"with_muse": True, "muse_apply": True, "stages": "story,shots"})
print("\n   D3 with_muse + muse_apply + stages=story,shots ->", r.status_code, {k: r.json().get(k) for k in ("episodes","dry_run","stages")})
j = wait_job(r.json()["job_id"])
print("      用户 stages 被覆盖后的实际调用:", [{k: v for k, v in x.items() if k in ("template","stages","stage_params")} for x in REC])

REC.clear()
r = cli.post("/api/v6/agent/season/undercity_arc/run", json={"dry_run": False, "only_missing": False, "stages": "story"})
print("\n   D4 dry_run=false only_missing=false stages=story ->", r.status_code, r.json().get("episodes"))
j = wait_job(r.json()["job_id"]); print("      job.status =", j.get("status"), "| episodes 记录:", [(e.get("template"), e.get("status")) for e in (j.get("result") or {}).get("episodes", [])])

REC.clear()
r = cli.post("/api/v6/agent/season/undercity_arc/run", json={"muse_apply": True, "dry_run": True})
print("\n   D5 只给 muse_apply=true（without with_muse）->", r.status_code, r.json().get("stages"), r.json().get("dry_run"))
j = wait_job(r.json()["job_id"])
print("      stage_params =", [x.get("stage_params") for x in REC][:1], "| stages=", [x.get("stages") for x in REC][:1])

REC.clear()
r = cli.post("/api/v6/agent/season/undercity_arc/run", json={"dry_run": True, "stages": "stro"})
print("\n   D6 stages 写错 ->", r.status_code, {k: r.json().get(k) for k in ("job_id","episodes")})
j = wait_job(r.json()["job_id"]); print("      job.status =", j.get("status"), "| error/结果:", str((j.get("result") or j.get("error")))[:160])

print("\n== E. 单并发 409 ==")
V._jobs.clear()
V._jobs["x"] = {"job_id": "x", "status": "running", "logs": [], "created": ""}
r = cli.post("/api/v6/agent/season/undercity_arc/run", json={"dry_run": True})
print("   季 run ->", r.status_code, r.json().get("detail"))
r = cli.post("/api/v6/agent/run", json={"template": "tonight_show", "dry_run": True})
print("   单 run ->", r.status_code, r.json().get("detail"))
V._jobs.clear()
print("\n== F. GET /overview ==")
r = cli.get("/api/v6/agent/overview"); d = r.json()
print("   status", r.status_code, "| count", d["count"], "| runs", d["runs"][:3])
print("   样例:", {k: d["templates"][0][k] for k in ("id","kind","stack","render_type","ready","series")})
