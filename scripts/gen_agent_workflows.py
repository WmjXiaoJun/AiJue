# -*- coding: utf-8 -*-
"""生成 16 个模板的 V6 智能体工作流 JSON（contract/v6/workflows/<id>.json）。"""
import json
import sys
from pathlib import Path

REPO = Path(".").resolve()
sys.path.insert(0, str(REPO))
from pipeline import v5_catalog  # noqa: E402
from pipeline.agents.workflow import WORKFLOW_DIR, default_workflow, save_workflow  # noqa: E402


def main() -> int:
    cat = v5_catalog.load_catalog()
    n = 0
    for tid, tpl in sorted(cat.get("templates", {}).items()):
        if tid.startswith("custom_"):
            continue
        wf = default_workflow(tid, tpl)
        save_workflow(wf)
        n += 1
        print(f"workflow {tid} -> {WORKFLOW_DIR / (tid + '.json')}")
    print(json.dumps({"ok": True, "count": n}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
