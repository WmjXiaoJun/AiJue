# -*- coding: utf-8 -*-
"""体检脚本 C：文档声称 vs 代码现实（CLI / API 路由 / 默认值 / 模板数），只读。

用法： python docs/audit/_tools/audit_docs_claims.py
输出： docs/audit/_tools/docs_claims.json + 控制台对照表

注意：会 import backend.app.main 取真实路由表；数据目录重定向到 tmp/audit/_claims_* ，
不写仓库 backend/data 与 outputs/。
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve()
REPO = next(p for p in HERE.parents if (p / "contract").is_dir() and (p / "pipeline").is_dir())
OUT = HERE.parent / "docs_claims.json"
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "backend"))

_tmp = Path(tempfile.mkdtemp(prefix="aijue-claims-"))
os.environ.setdefault("ZAOPIANJU_DATA_DIR", str(_tmp / "data"))
os.environ.setdefault("ZAOPIANJU_OUTPUTS_DIR", str(_tmp / "outputs"))
os.environ.setdefault("ZAOPIANJU_FRONTEND_DIST", str(_tmp / "no-dist"))
os.environ.setdefault("ZAOPIANJU_JOBS_AUTORUN", "0")

res: dict = {}

# ---------------------------------------------------------------- CLI
from pipeline.agents import cli as agent_cli  # noqa: E402

parser = agent_cli.build_parser()
subs = {}
for action in parser._subparsers._group_actions:  # noqa: SLF001
    for name, sub in action.choices.items():
        subs[name] = sorted({opt for a in sub._actions for opt in a.option_strings})
res["cli"] = {"subcommands": sorted(subs), "flags": subs}

# ---------------------------------------------------------------- 路由
from app.main import app  # noqa: E402

routes = set()
for r in app.routes:
    path = getattr(r, "path", None)
    for m in (getattr(r, "methods", None) or []):
        if m in ("GET", "POST", "PUT", "DELETE", "PATCH"):
            routes.add((m, path))
res["routes"] = sorted(f"{m} {p}" for m, p in routes)
res["route_counts"] = {"method_path": len(routes), "unique_path": len({p for _, p in routes})}


def has(method: str, path: str) -> bool:
    return (method, path) in routes


res["claims_api"] = {
    "PUT /api/v6/agent/workflow/{template}": has("PUT", "/api/v6/agent/workflow/{template}"),
    "GET /api/v6/agent/workflow/{template}": has("GET", "/api/v6/agent/workflow/{template}"),
    "GET /api/v6/agent/series": has("GET", "/api/v6/agent/series"),
    "POST /api/v6/agent/season/{sid}/run": has("POST", "/api/v6/agent/season/{sid}/run"),
    "GET /api/v6/agent/season/{sid}/status": has("GET", "/api/v6/agent/season/{sid}/status"),
    "POST /api/v5/series/{sid}/export": has("POST", "/api/v5/series/{sid}/export"),
    "GET /api/v6/muse/status": has("GET", "/api/v6/muse/status"),
    "POST /api/v6/muse/sync": has("POST", "/api/v6/muse/sync"),
    "GET /api/v6/muse/jobs/{job_id}": has("GET", "/api/v6/muse/jobs/{job_id}"),
}

# ---------------------------------------------------------------- 合同 / 注册表计数
from pipeline import v5_catalog  # noqa: E402
from pipeline.agents.registry import TOOLS  # noqa: E402
from pipeline.agents.roles import ROLES  # noqa: E402

cat = v5_catalog.load_catalog()
res["counts"] = {
    "catalog": cat["counts"],
    "custom": cat.get("custom_counts"),
    "roles": len(ROLES),
    "tools": len(TOOLS),
    "editorial_files": len(list((REPO / "contract/v5/editorial").glob("*.json"))),
    "workflow_files": len(list((REPO / "contract/v6/workflows").glob("*.json"))),
    "builtin_templates": len(list((REPO / "contract/v5/templates").glob("*.json"))),
    "custom_templates": len(list((REPO / "contract/v5/custom/templates").glob("*.json"))),
    "builtin_characters": len(list((REPO / "contract/v5/characters").glob("*.json"))),
    "custom_characters": len(list((REPO / "contract/v5/custom/characters").glob("*.json"))),
    "tools_list": sorted(TOOLS),
}

# ---------------------------------------------------------------- 工作流版本 / 栈
wdir = REPO / "contract/v6/workflows"
vers, stacks = Counter(), Counter()
for p in sorted(wdir.glob("*.json")):
    w = json.loads(p.read_text(encoding="utf-8-sig"))
    vers[w.get("version")] += 1
    stacks[w.get("stack")] += 1
res["workflows"] = {"versions": dict(vers), "stacks": dict(stacks)}

# ---------------------------------------------------------------- 默认值（读源码字面量）
def grep(pattern: str, files: list[str], limit: int = 6) -> list[dict]:
    out = []
    for rel in files:
        p = REPO / rel
        if not p.is_file():
            continue
        for i, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if re.search(pattern, line):
                out.append({"file": rel, "line": i, "text": line.strip()[:160]})
                if len(out) >= limit:
                    return out
    return out


res["defaults"] = {
    "max_frames_600": grep(r"max_frames:? (int =|: int =) ?600|max_frames=600", ["pipeline/lipsync.py"]),
    "muse_lock": grep(r"\.muse\.lock", ["pipeline/lipsync.py"]),
    "dry_run_season": grep(r'dry_run = _as_bool\(payload\.get\("dry_run"\)',
                           ["backend/app/routers/v6_agent.py"]),
    "only_missing": grep(r'only_missing = _as_bool', ["backend/app/routers/v6_agent.py"]),
    "reuse_shots_default": grep(r'"reuse_shots": True', ["pipeline/agents/workflow.py"]),
    "api_endpoints_claim": None,
    "season_defaults_in_code": grep(r'target_count|episode_sec',
                                    ["backend/app/routers/v6_agent.py", "scripts/check_seasons.py"]),
}

series = json.loads((REPO / "contract/v5/series/undercity_arc.json").read_text(encoding="utf-8-sig"))
res["series_season"] = series.get("season")

# ---------------------------------------------------------------- 输出物现状（对照 PART_PIPELINE）
def count_final(rel: str) -> dict:
    d = REPO / rel
    if not d.is_dir():
        return {"dir": rel, "dirs": 0, "final": 0, "exists": False}
    dirs = [p for p in d.iterdir() if p.is_dir()]
    return {"dir": rel, "dirs": len(dirs),
            "final": len(list(d.rglob("final*.mp4"))), "exists": True}


res["outputs"] = [count_final(r) for r in
                  ("outputs/templates_3d", "outputs/templates_2d", "outputs/templates_v5",
                   "outputs/templates_v3", "outputs/series")]
plates = REPO / "outputs/scene_plates"
res["scene_plates"] = {"dir": "outputs/scene_plates",
                       "png": len(list(plates.glob("*.png"))) if plates.is_dir() else 0,
                       "files": sorted(p.name for p in plates.glob('*'))[:6] if plates.is_dir() else []}

OUT.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")

print("== CLI ==")
print("  subcommands:", res["cli"]["subcommands"])
for k, v in res["cli"]["flags"].items():
    print(f"    {k}: {v}")
print("\n== API 声称 -> 现实 ==")
for k, v in res["claims_api"].items():
    print(f"  {'OK ' if v else 'MISS'} {k}")
print("  route_counts:", res["route_counts"])
print("\n== 计数 ==")
for k, v in res["counts"].items():
    if k != "tools_list":
        print(f"  {k}: {v}")
print("  tools:", len(res["counts"]["tools_list"]))
print("\n== 工作流 ==", res["workflows"])
print("\n== 默认值证据 ==")
for k, v in res["defaults"].items():
    if isinstance(v, list):
        shown = ["{}:{} {}".format(x["file"], x["line"], x["text"]) for x in v]
        print("  {}: {}".format(k, shown))
    else:
        print("  {}: {}".format(k, v))
print("\n== 季合同 season ==", json.dumps(res["series_season"], ensure_ascii=False))
print("\n== outputs 现状 ==")
for r in res["outputs"]:
    print("  ", r)
print("  scene_plates:", res["scene_plates"])
print("\nwrote", OUT)
