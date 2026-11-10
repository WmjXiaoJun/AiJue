# -*- coding: utf-8 -*-
import json, sys
from pathlib import Path
REPO = Path(r"D:\BaiduSyncdisk\wmj_work\XJ\AiJue")
sys.path.insert(0, str(REPO))
from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend.app.routers import v6_agent as V
from pipeline import v5_catalog
from pipeline.agents.workflow import is_shorts, save_workflow, load_workflow

app = FastAPI(); app.include_router(V.router); cli = TestClient(app)

print("== 1. run_id 长度 vs /media 闸门 (^[a-z0-9_]{8,40}$) ==")
cat = v5_catalog.load_catalog(); tpls = cat['templates']
bad = []
for tid in sorted(tpls):
    rid = "20261105_112758_" + tid          # time.strftime + '_' + template_id
    ok = 8 <= len(rid) <= 40
    if not ok: bad.append((tid, len(rid)))
print("   run_id 超 40 的模板（其证据在 /media 上永久 404）:", bad)

print("\n== 2. season sid 更深穿越（补 3 层 ..） ==")
for sid in ("..%5C..%5Cv6%5Cart%5Cpose_policy", "..%5C..%5C..%5CAGENTS", "undercity_arc"):
    r = cli.get(f"/api/v6/agent/season/{sid}/status")
    body = r.text[:150].replace("\n", " ")
    print(f"   {sid[:38]:38s} -> {r.status_code} {body}")

print("\n== 3. set_stage_param 指向不存在阶段 ==")
tpl = "tonight_show"
wf_before = load_workflow(tpl)
try:
    r = cli.post("/api/v6/agent/edit", json={"template": tpl, "op": "set_stage_param",
                                            "stage": "shotz", "json": {"x": 1}})
    print("   status", r.status_code, "->", str(r.json())[:160])
except Exception as e:
    print("   EXC", type(e).__name__, e)
wf_after = load_workflow(tpl)
print("   合同是否被改写:", wf_before != wf_after)
print("   git diff 是否为空:", end=" ")
import subprocess
print(subprocess.run(["git","diff","--stat","--","contract/v6/workflows/tonight_show.json"],
                     capture_output=True, text=True).stdout.strip() or "空（未改写）")
# 副作用自愈：set_stage_param 会无条件 save_workflow，丢掉文件尾换行（见报告 P3-7）。
# 这里把尾换行补回去，保证重复跑本脚本不会把 contract/ 弄脏。
_p = Path(r"D:\BaiduSyncdisk\wmj_work\XJ\AiJue\contract\v6\workflows\tonight_show.json")
_b = _p.read_bytes()
if not _b.endswith(b"\n"):
    _p.write_bytes(_b + b"\n")
    print("   [自愈] 已补回尾换行（合同语义未变）")

print("\n== 4. PUT /workflow 传未知 tool（校验失败应 400，实测？） ==")
bad_wf = {"workflow": {"version": 2, "id": tpl, "stages": [
    {"id": "story", "agent": "story_architect", "tool": "no_such_tool", "depends": [], "enabled": True}]}}
app2 = FastAPI(); app2.include_router(V.router)
cli2 = TestClient(app2, raise_server_exceptions=False)
r = cli2.put(f"/api/v6/agent/workflow/{tpl}", json=bad_wf)
print("   status =", r.status_code, "| body =", r.text[:120])
print("   合同 git diff:", subprocess.run(["git","diff","--stat","--","contract/v6/workflows/tonight_show.json"],
      capture_output=True, text=True).stdout.strip() or "空（未改写）")

print("\n== 5. save_workflow 校验能力（隔离目录打桩） ==")
import pipeline.agents.workflow as W
tmp = Path(r"C:\Users\Administrator\AppData\Local\Temp\audit_wf"); tmp.mkdir(parents=True, exist_ok=True)
orig = W.WORKFLOW_DIR; W.WORKFLOW_DIR = tmp
cases = {
  "重复 stage id": {"id": "zzz_t", "stages": [{"id": "a", "tool": "build_story", "depends": []},
                                              {"id": "a", "tool": "build_story", "depends": []}]},
  "未知 tool": {"id": "zzz_t", "stages": [{"id": "a", "tool": "nope", "depends": []}]},
  "依赖不存在": {"id": "zzz_t", "stages": [{"id": "a", "tool": "build_story", "depends": ["ghost"]}]},
  "空 stages": {"id": "zzz_t", "stages": []},
  "非法 id": {"id": "ZZZ-T", "stages": [{"id": "a", "tool": "build_story", "depends": []}]},
  "循环依赖": {"id": "zzz_t", "stages": [{"id": "a", "tool": "build_story", "depends": ["b"]},
                                        {"id": "b", "tool": "build_story", "depends": ["a"]}]},
}
for name, wf in cases.items():
    try:
        p = W.save_workflow(wf); print(f"   {name:12s} -> 已保存 {p.name}  <== 未拦截")
    except Exception as e:
        print(f"   {name:12s} -> {type(e).__name__}: {e}")
W.WORKFLOW_DIR = orig

print("\n== 6. 模板数分布（对照 AGENTS.md 的 16+17） ==")
kind = {"base": 0, "custom": 0}
for tid in tpls: kind["custom" if tid.startswith("custom_") else "base"] += 1
sh = [t for t in tpls if is_shorts(t, tpls[t])]
print("   base(非 custom_ 前缀):", kind["base"], "| custom_*:", kind["custom"],
      "| hq_shorts:", len(sh), "| v5_render:", len(tpls)-len(sh))
