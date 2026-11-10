# -*- coding: utf-8 -*-
"""体检脚本 A2：cinema3d/templates3d/*_semantic.json 与模板 acts 的对齐 + shot id 重复。

只读：不改任何合同/源码。输出 JSON + 控制台表。
用法： python docs/audit/_tools/audit_semantic_shots.py
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
BRIEFS = REPO / "contract" / "v5" / "cinema3d" / "templates3d"
TPL_DIRS = [REPO / "contract" / "v5" / "templates", REPO / "contract" / "v5" / "custom" / "templates"]
OUT = Path(__file__).resolve().parent / "semantic_shots.json"


def load(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {"_error": f"{type(exc).__name__}: {exc}"}


tpls: dict[str, dict] = {}
for d in TPL_DIRS:
    for p in sorted(d.glob("*.json")):
        tpls.setdefault(p.stem, load(p))

rows = []
for p in sorted(BRIEFS.glob("*_semantic.json")):
    tid = p.stem[: -len("_semantic")]
    brief = load(p)
    shots = [s for s in (brief.get("shots") or []) if isinstance(s, dict)]
    ids = [str(s.get("id") or "") for s in shots]
    dup = {k: v for k, v in Counter(ids).items() if v > 1}
    ids_norm = [i for i in ids if i]
    dup_norm = {k: v for k, v in Counter(ids_norm).items() if v > 1}
    bdur = round(sum(float(s.get("duration") or 0) for s in shots), 2)
    tpl = tpls.get(tid) or {}
    acts = [a for a in (tpl.get("acts") or []) if isinstance(a, dict)]
    rows.append({
        "template": tid,
        "brief": p.name,
        "render_style": str(tpl.get("render_style") or ""),
        "acts": len(acts),
        "brief_shots": len(shots),
        "shots_with_prompt": sum(1 for s in shots if s.get("prompt")),
        "acts_x_brief_ratio": round(len(shots) / len(acts), 2) if acts else None,
        "brief_duration": bdur,
        "target_duration": float(tpl.get("target_duration") or 0),
        "dur_ratio": round(bdur / float(tpl.get("target_duration") or 1), 2)
        if tpl.get("target_duration") else None,
        "unique_ids": len(set(ids_norm)),
        "dup_id_count": len(dup_norm),
        "dup_ids": dup_norm,
        "id_patterns": sorted({re.sub(r"\d+", "#", i) for i in ids_norm}),
        "error": brief.get("_error"),
    })

OUT.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")

print(f"{'template':32} {'acts':>4} {'shots':>6} {'ratio':>6} {'dupdur':>8} {'target':>7} "
      f"{'uniq':>5} {'dupids':>7}")
for r in rows:
    print(f"{r['template']:32} {r['acts']:4} {r['brief_shots']:6} "
          f"{str(r['acts_x_brief_ratio']):>6} {r['brief_duration']:8} {r['target_duration']:7} "
          f"{r['unique_ids']:5} {r['dup_id_count']:7}")
print("\n== 有重复 id 的 brief ==")
for r in rows:
    if r["dup_id_count"]:
        detail = ", ".join(f"{k}×{v}" for k, v in sorted(r["dup_ids"].items())[:8])
        print(f"  {r['template']}: {r['dup_id_count']} 个重复 id（{detail}）")
print("\n== id 命名族 ==")
for r in rows:
    if len(r["id_patterns"]) > 1:
        print(f"  {r['template']}: {r['id_patterns']}")
print("\nwrote", OUT)
