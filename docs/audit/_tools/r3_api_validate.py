# -*- coding: utf-8 -*-
import json, sys
from pathlib import Path
REPO = Path(r"D:\BaiduSyncdisk\wmj_work\XJ\AiJue")
sys.path.insert(0, str(REPO))
import pipeline.agents.workflow as W
from pipeline.agents.workflow import is_shorts
from pipeline import v5_catalog
from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend.app.routers import v6_agent as V

print("== 5. save_workflow 校验（隔离 WORKFLOW_DIR，不碰真合同） ==")
tmp = Path(r"C:\Users\Administrator\AppData\Local\Temp\audit_wf"); tmp.mkdir(parents=True, exist_ok=True)
for f in tmp.glob("*"): f.unlink()
orig = W.WORKFLOW_DIR; W.WORKFLOW_DIR = tmp
cases = {
  "重复 stage id": {"id": "zzz_t", "stages": [{"id": "a", "tool": "build_story", "depends": []},
                                              {"id": "a", "tool": "build_story", "depends": []}]},
  "未知 tool":     {"id": "zzz_t", "stages": [{"id": "a", "tool": "nope", "depends": []}]},
  "依赖不存在":     {"id": "zzz_t", "stages": [{"id": "a", "tool": "build_story", "depends": ["ghost"]}]},
  "空 stages":     {"id": "zzz_t", "stages": []},
  "非法 id":       {"id": "ZZZ-T", "stages": [{"id": "a", "tool": "build_story", "depends": []}]},
  "循环依赖":       {"id": "zzz_t", "stages": [{"id": "a", "tool": "build_story", "depends": ["b"]},
                                              {"id": "b", "tool": "build_story", "depends": ["a"]}]},
  "stack 写错":     {"id": "zzz_t", "stack": "v5_render",
                    "stages": [{"id": "a", "tool": "render_2d_hq", "depends": []}]},
}
for name, wf in cases.items():
    try:
        p = W.save_workflow(wf); print(f"   {name:12s} -> 已保存 {(p).name}   <== 未拦截")
    except Exception as e:
        print(f"   {name:12s} -> {type(e).__name__}: {e}")
W.WORKFLOW_DIR = orig

print("\n== 6. 模板数分布 ==")
cat = v5_catalog.load_catalog(); tpls = cat["templates"]
base = [t for t in tpls if not t.startswith("custom_")]
custom = [t for t in tpls if t.startswith("custom_")]
sh = [t for t in tpls if is_shorts(t, tpls[t])]
print("   base:", len(base), "custom_*:", len(custom), "| hq_shorts:", len(sh), "v5_render:", len(tpls)-len(sh))
print("   custom_ 里非 custom_tpl_ 前缀:", [t for t in custom if not t.startswith("custom_tpl_")])
print("   hq_shorts 里 render_style 为空的:", [t for t in sh if not (tpls[t].get("render_style"))])
print("   v5_render 里带 render_style 的:", [t for t in tpls if t not in sh and tpls[t].get("render_style")])

print("\n== 7. PUT /workflow 未知 tool → HTTP 状态 ==")
app = FastAPI(); app.include_router(V.router)
cli = TestClient(app, raise_server_exceptions=False)
tmpl = "tonight_show"
bad = {"workflow": {"version": 2, "id": tmpl, "stages": [
    {"id": "story", "agent": "story_architect", "tool": "no_such_tool", "depends": [], "enabled": True}]}}
r = cli.put(f"/api/v6/agent/workflow/{tmpl}", json=bad)
print("   status =", r.status_code, "| body =", r.text[:100])

print("\n== 8. PUT /workflow stack 与阶段不符（不写盘，仅看是否会拦） ==")
print("   读代码：put_workflow 只做 save_workflow，无 stack/模板栈一致性校验（workflow.py:160-168 只查 id/依赖/tool）")
