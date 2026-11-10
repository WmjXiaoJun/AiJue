# -*- coding: utf-8 -*-
"""V50：给已有工作流合同补 `muse` 阶段（MuseTalk 口型，默认关闭）。

- 已有 muse 阶段 → 跳过
- hq_shorts：插在 qc 之前；v5_render：插在 assemble 之前
- 保持其余阶段与用户编辑不变

用法：python scripts/migrate_v50_muse_stage.py [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from pipeline.agents.workflow import (  # noqa: E402
    DEFAULT_STAGES, SHORTS_STAGES, WORKFLOW_DIR, is_shorts)

MUSE_SHORTS = next(s for s in SHORTS_STAGES if s["id"] == "muse")
MUSE_BASE = next(s for s in DEFAULT_STAGES if s["id"] == "muse")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="补 muse 阶段到工作流合同")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    changed, skipped = [], []
    for path in sorted(WORKFLOW_DIR.glob("*.json")):
        try:
            wf = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        stages = wf.get("stages") or []
        if any(s.get("id") == "muse" for s in stages):
            skipped.append(path.stem)
            continue
        shorts = wf.get("stack") == "hq_shorts" or is_shorts(path.stem, {})
        stage = dict(MUSE_SHORTS if shorts else MUSE_BASE)
        anchor = "qc" if shorts else "assemble"
        idx = next((i for i, s in enumerate(stages) if s.get("id") == anchor), len(stages))
        stages.insert(idx, stage)
        wf["stages"] = stages
        wf.setdefault("stack", "hq_shorts" if shorts else "v5_render")
        if not args.dry_run:
            path.write_text(json.dumps(wf, ensure_ascii=False, indent=2), encoding="utf-8")
        changed.append(path.stem)
    print(json.dumps({"changed": changed, "changed_count": len(changed),
                      "skipped": len(skipped)}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
