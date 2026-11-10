# -*- coding: utf-8 -*-
"""AUDIT_render 静态核验 1：HQ 语义 brief 的镜头数 / 重复 id / 与模板 acts 的脱钩。"""
import json, sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
SEM = REPO / "contract" / "v5" / "cinema3d" / "templates3d"

rows = []
for p in sorted(SEM.glob("*_semantic.json")):
    tid = p.name.replace("_semantic.json", "")
    data = json.loads(p.read_text(encoding="utf-8"))
    shots = data.get("shots") or []
    ids = [str(s.get("id")) for s in shots]
    dup = {k: v for k, v in Counter(ids).items() if v > 1}
    tpl_path = None
    for cand in (REPO / "contract" / "v5" / "templates" / f"{tid}.json",
                 REPO / "contract" / "v5" / "custom" / "templates" / f"{tid}.json"):
        if cand.is_file():
            tpl_path = cand
            break
    acts = 0
    if tpl_path:
        tpl = json.loads(tpl_path.read_text(encoding="utf-8"))
        acts = len([a for a in (tpl.get("acts") or []) if isinstance(a, dict)])
    rows.append((tid, len(shots), len(set(ids)), dup, acts, sum(dup.values())))

print(f"{'template':34s} shots uniq dupids acts dup_extra")
for tid, n, u, dup, acts, extra in rows:
    flag = " <<<" if dup else ""
    print(f"{tid:34s} {n:5d} {u:4d} {len(dup):6d} {acts:4d} {extra:9d}{flag}")
print()
total_files = len(rows)
with_dup = [r[0] for r in rows if r[3]]
print("semantic files:", total_files, " files with duplicate shot ids:", len(with_dup))
worst = max(rows, key=lambda r: (max(r[3].values()) if r[3] else 0))
if worst[3]:
    k, v = max(worst[3].items(), key=lambda kv: kv[1])
    print(f"worst: {worst[0]} shot id {k} x{v}")
print("shots range:", min(r[1] for r in rows), "-", max(r[1] for r in rows))
print("acts range:", min(r[4] for r in rows), "-", max(r[4] for r in rows))
if worst[3]:
    print("\n-- 示例：重复 id 明细（取 worst 文件前 5 个重复 id）--")
    for k, v in list(worst[3].items())[:5]:
        print(f"   {k} x{v}")
