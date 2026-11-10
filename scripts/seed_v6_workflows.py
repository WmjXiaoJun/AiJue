# -*- coding: utf-8 -*-
"""V49：为所有模板补齐 contract/v6/workflows/<id>.json（按栈自动选阶段）。

用法：
    python scripts/seed_v6_workflows.py            # 只补缺失的
    python scripts/seed_v6_workflows.py --force    # 全部重建（会覆盖手工编辑，慎用）
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from pipeline import v5_catalog  # noqa: E402
from pipeline.agents.workflow import WORKFLOW_DIR, default_workflow  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="补齐 V6 智能体工作流合同")
    ap.add_argument("--force", action="store_true", help="覆盖已有工作流（默认跳过）")
    args = ap.parse_args(argv)
    cat = v5_catalog.load_catalog()
    WORKFLOW_DIR.mkdir(parents=True, exist_ok=True)
    created, skipped = [], []
    for tid, tpl in sorted((cat.get("templates") or {}).items()):
        path = WORKFLOW_DIR / f"{tid}.json"
        if path.is_file() and not args.force:
            skipped.append(tid)
            continue
        wf = default_workflow(tid, tpl)
        path.write_text(json.dumps(wf, ensure_ascii=False, indent=2), encoding="utf-8")
        created.append({"template": tid, "stack": wf.get("stack"),
                        "stages": [s["id"] for s in wf.get("stages") or []]})
    print(json.dumps({"created": created, "created_count": len(created),
                      "skipped": skipped, "skipped_count": len(skipped)},
                     ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
