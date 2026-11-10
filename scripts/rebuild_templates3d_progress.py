# -*- coding: utf-8 -*-
"""V49：按磁盘事实重建 outputs/templates_3d/progress.json（保留已有 clip 证据）。

用法：python scripts/rebuild_templates3d_progress.py [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

OUT3D = REPO / "outputs" / "templates_3d"
PROGRESS = OUT3D / "progress.json"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="重建 templates_3d 进度文件")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    old = {}
    if PROGRESS.is_file():
        try:
            old = json.loads(PROGRESS.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            old = {}

    added, updated = [], []
    for out_dir in sorted(p for p in OUT3D.iterdir() if p.is_dir()):
        final = out_dir / "final.mp4"
        if not final.is_file() or final.stat().st_size <= 0:
            continue
        tid = out_dir.name
        shots = sorted((out_dir / "shots").glob("*.mp4")) if (out_dir / "shots").is_dir() else []
        report = {}
        if (out_dir / "report.json").is_file():
            try:
                report = json.loads((out_dir / "report.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                report = {}
        fmt = (report.get("probe") or {}).get("format") or {}
        entry = old.get(tid) or {}
        entry.update({
            "status": "done",
            "done": len(shots) or entry.get("done") or 0,
            "total": len(shots) or entry.get("total") or 0,
            "final_bytes": final.stat().st_size,
            "duration": round(float(fmt.get("duration") or 0), 3) or entry.get("duration"),
            "final": str(final),
        })
        entry.setdefault("clips", {})
        if tid in old:
            updated.append(tid)
        else:
            added.append(tid)
        old[tid] = entry

    missing = [p.name for p in OUT3D.iterdir()
               if p.is_dir() and not (p / "final.mp4").is_file()]
    payload = {k: old[k] for k in sorted(old)}
    if args.dry_run:
        print(json.dumps({"would_add": added, "would_update": len(updated),
                          "no_final": missing}, ensure_ascii=False, indent=1))
        return 0
    PROGRESS.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"recorded": len(payload), "added": added,
                      "updated": len(updated), "no_final": missing},
                     ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
