# -*- coding: utf-8 -*-
"""AUDIT_backend_api 的可复现探针（只读；不改 backend/、不写 contract/、不跑 GPU）。

用法（仓库根）：
    python docs/audit/_tools/api_audit_probe.py            # 全部
    python docs/audit/_tools/api_audit_probe.py routes     # 只跑路由枚举
    python docs/audit/_tools/api_audit_probe.py http       # 只跑 TestClient 探针
    python docs/audit/_tools/api_audit_probe.py paths      # 只跑路径拼接探针

环境：ZAOPIANJU_JOBS_AUTORUN=0（不自动起任务线程/GPU）；DB 与 outputs 不重定向，
本脚本不写库、不落产物（唯一副作用：POST /api/settings/metaso-test 会带已保存的
key 外联 metaso.cn 一次，若不想外联请跳过 http 组或用 --no-net）。
"""
from __future__ import annotations

import collections
import os
import sys
from pathlib import Path

os.environ.setdefault("ZAOPIANJU_JOBS_AUTORUN", "0")

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

NO_NET = "--no-net" in sys.argv


def group_routes() -> None:
    """1) 枚举 mounted 路由，找 method+path 重复 / 遮蔽风险。"""
    import backend.app.main as m

    rows = []
    for r in m.app.routes:
        for meth in (getattr(r, "methods", None) or []):
            rows.append((meth, getattr(r, "path", ""), getattr(r, "endpoint", None)))
    print(f"[routes] app.routes={len(m.app.routes)}  method+path 条目={len(rows)}")
    counter = collections.Counter((x[0], x[1]) for x in rows)
    dups = {k: v for k, v in counter.items() if v > 1}
    print("[routes] 重复 method+path:", dups or "无")
    # 注册顺序：Mount("/") 之后注册的路由不可达
    order = []
    for idx, r in enumerate(m.app.routes):
        order.append((idx, type(r).__name__, getattr(r, "path", None)))
    mounts = [o for o in order if o[1] == "Mount"]
    print("[routes] Mount 注册位置:", mounts)
    if mounts:
        first_root = [o for o in mounts if o[2] == ""]
        if first_root:
            idx = first_root[0][0]
            after = [o for o in order if o[0] > idx and o[1] == "APIRoute"]
            print(f"[routes] 挂在 '/' 之后注册的 APIRoute 数={len(after)} ->",
                  [o[2] for o in after] or "无（无遮蔽）")


def http_probes() -> None:
    """2) TestClient 探针：WIP 端点可达性 / 非数字参数 / 反斜杠穿越。"""
    from fastapi.testclient import TestClient

    import backend.app.main as m

    c = TestClient(m.app, raise_server_exceptions=False)

    print("\n[http] 健康检查:", c.get("/api/health").status_code)

    print("[http] WIP 端点 POST /api/settings/metaso-test（挂载顺序回归）")
    if NO_NET:
        print("       跳过（--no-net）")
    else:
        r = c.post("/api/settings/metaso-test", json={})
        print(f"       -> {r.status_code} {r.text[:100]}")

    print("[http] POST /api/v5/longform/run duration='abc'（期望 400，实测 500 即为缺陷）")
    r = c.post("/api/v5/longform/run", json={"duration": "abc"})
    print(f"       -> {r.status_code} {r.text[:100]}")

    print("[http] POST /api/v6/agent/run duration='abc'（只校验 template，duration 进线程才炸）")
    r = c.post("/api/v6/agent/run", json={"template": "tonight_show", "duration": "abc"})
    print(f"       -> {r.status_code} {r.text[:100]}")

    print("[http] 绝对路径泄漏 GET /api/v6/agent/overview")
    r = c.get("/api/v6/agent/overview")
    if r.status_code == 200:
        tpl = (r.json().get("templates") or [{}])[0]
        print("       workflow_file =", tpl.get("workflow_file"))

    print("[http] Windows 反斜杠穿越 GET /api/v6/agent/season/<..\\..\\..\\contract\\demo_episode>/status")
    sid = "..\\..\\..\\contract\\demo_episode"
    r = c.get("/api/v6/agent/season/" + sid.replace("\\", "%5C") + "/status")
    print(f"       -> {r.status_code} {r.text[:160]}")


def path_probes() -> None:
    """3) 路径拼接探针：format_id / series id 未校验时能逃出契约目录。"""
    print("\n[paths] contract/formats/{format_id}.json 拼接（format_id 无正则校验）")
    formats = (REPO / "contract" / "formats").resolve()
    for bad in (r"..\..\contract\demo_episode",
                r"..\..\contract\v5\series\undercity_arc"):
        p = formats / (bad + ".json")
        print(f"       {bad!r} -> exists={p.resolve().is_file()} {p.resolve()}")

    print("[paths] contract/v5/series/{sid}.json 拼接（sid 无正则校验）")
    series = (REPO / "contract" / "v5" / "series").resolve()
    p = series / "..\\..\\..\\contract\\demo_episode.json"
    print(f"       -> exists={p.resolve().is_file()} {p.resolve()}")


def main() -> None:
    groups = [a for a in sys.argv[1:] if not a.startswith("--")] or ["routes", "paths", "http"]
    if "routes" in groups:
        group_routes()
    if "paths" in groups:
        path_probes()
    if "http" in groups:
        http_probes()


if __name__ == "__main__":
    main()
