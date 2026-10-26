# -*- coding: utf-8 -*-
"""V6 智能体工作台 CLI。

用法：
  python -m pipeline.agents list
  python -m pipeline.agents inspect --template history_talk
  python -m pipeline.agents run --template history_talk --duration 40 --goal "..."
  python -m pipeline.agents run --template history_talk --stages shots,assemble,qc
  python -m pipeline.agents edit --template classics --op set_voice --char guanyu --value legacy_v4_laozhou
  python -m pipeline.agents edit --template classics --op patch_editorial --json '{"pacing":{"climax":0.6}}'
  python -m pipeline.agents edit --template classics --op patch_workflow --json '{"enable":["h3"]}'
  python -m pipeline.agents status --template history_talk
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .. import config, v5_catalog
from .ops import apply_op
from .orchestrator import latest_runs, run_template
from .roles import ROLES
from .workflow import WORKFLOW_DIR, load_workflow, save_workflow


def _print(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def cmd_list(args) -> int:
    cat = v5_catalog.load_catalog()
    rows = []
    for tid, tpl in sorted(cat.get("templates", {}).items()):
        if tid.startswith("custom_"):
            continue
        wf = load_workflow(tid, tpl)
        rows.append({"template": tid, "label": tpl.get("label"),
                     "style": tpl.get("style"),
                     "stages": [s["id"] for s in wf.get("stages") or []
                                if s.get("enabled", True)],
                     "workflow_file": str(WORKFLOW_DIR / f"{tid}.json"),
                     "exists": (WORKFLOW_DIR / f"{tid}.json").is_file()})
    _print({"roles": {k: v["name"] for k, v in ROLES.items()},
            "templates": rows, "total": len(rows)})
    return 0


def cmd_inspect(args) -> int:
    cat = v5_catalog.load_catalog()
    tpl = cat["templates"].get(args.template) or {}
    wf = load_workflow(args.template, tpl)
    _print({"template": args.template, "label": tpl.get("label"),
            "workflow": wf,
            "roles": {s.get("agent"): ROLES.get(str(s.get("agent")), {}).get("name")
                      for s in wf.get("stages") or []}})
    return 0


def cmd_run(args) -> int:
    stages = [x.strip() for x in (args.stages or "").split(",") if x.strip()] or None
    run = run_template(args.template, duration=args.duration, seed=args.seed,
                       force=args.force, stages=stages, start=args.start,
                       goal=args.goal or "", dry_run=args.dry_run,
                       prog=lambda sid, msg: print(f"[{sid}] {msg}", flush=True))
    _print(run.to_dict())
    return 0 if run.status in ("ok", "planned") else 1


def cmd_edit(args) -> int:
    payload = {}
    if getattr(args, "json_file", None):
        payload = json.loads(Path(args.json_file).read_text(encoding="utf-8-sig"))
    elif args.json:
        payload = json.loads(args.json)
    if args.value is not None:
        payload = args.value
    res = apply_op(args.template, args.op, character=args.char, value=payload,
                   json=payload, stage=args.stage)
    _print(res)
    return 0


def cmd_status(args) -> int:
    _print({"template": args.template, "runs": latest_runs(args.template, args.limit)})
    return 0


def cmd_workflow(args) -> int:
    cat = v5_catalog.load_catalog()
    tpl = cat["templates"].get(args.template) or {}
    wf = load_workflow(args.template, tpl)
    path = save_workflow(wf)
    _print({"ok": True, "template": args.template, "path": str(path),
            "stages": [s["id"] for s in wf.get("stages") or []]})
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="pipeline.agents", description="AiJue V6 智能体工作台")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("list", help="列出模板/工作流/角色")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("inspect", help="查看模板工作流")
    p.add_argument("--template", required=True)
    p.set_defaults(func=cmd_inspect)

    p = sub.add_parser("run", help="按工作流执行（支持增量）")
    p.add_argument("--template", required=True)
    p.add_argument("--duration", type=float, default=40.0)
    p.add_argument("--seed", type=int, default=20261101)
    p.add_argument("--stages", default=None, help="逗号分隔阶段（自动补依赖）")
    p.add_argument("--start", default=None, help="从某阶段开始")
    p.add_argument("--goal", default=None, help="本次编排目标（写入 run 证据）")
    p.add_argument("--force", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("edit", help="智能体修改（声音/剪辑/模板/工作流）")
    p.add_argument("--template", required=True)
    p.add_argument("--op", required=True,
                   choices=["set_voice", "patch_editorial", "patch_template",
                            "patch_workflow", "set_stage_param", "reset_workflow"])
    p.add_argument("--char", default=None)
    p.add_argument("--stage", default=None)
    p.add_argument("--value", default=None)
    p.add_argument("--json", default=None)
    p.add_argument("--json-file", default=None,
                   help="从 JSON 文件读取修改内容（推荐，避免 shell 引号问题）")
    p.set_defaults(func=cmd_edit)

    p = sub.add_parser("status", help="查看最近 run")
    p.add_argument("--template", default=None)
    p.add_argument("--limit", type=int, default=10)
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("workflow", help="导出模板工作流到 contract/v6/workflows")
    p.add_argument("--template", required=True)
    p.set_defaults(func=cmd_workflow)
    return ap


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
