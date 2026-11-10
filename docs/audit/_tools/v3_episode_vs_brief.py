# -*- coding: utf-8 -*-
"""AUDIT_render 静态核验 3：episode.json（assemble 的镜头表）vs semantic brief（HQ 实际生成）失配率。"""
import json
from pathlib import Path
REPO = Path(__file__).resolve().parents[3]
V5 = REPO / "outputs" / "templates_v5"
O3D = REPO / "outputs" / "templates_3d"
SEM = REPO / "contract" / "v5" / "cinema3d" / "templates3d"

print(f"{'template':32s} ep  sem  miss  miss%  clips_on_disk")
tot_missing = 0
rows = []
for sem_p in sorted(SEM.glob("*_semantic.json")):
    tid = sem_p.name.replace("_semantic.json", "")
    ep_p = V5 / tid / "work" / "episode.json"
    if not ep_p.is_file():
        continue
    ep_ids = [str(s.get("id")) for s in (json.loads(ep_p.read_text(encoding="utf-8")).get("shots") or [])]
    sem_ids = {str(s.get("id")) for s in (json.loads(sem_p.read_text(encoding="utf-8")).get("shots") or [])}
    miss = [i for i in ep_ids if i not in sem_ids]
    disk = len(list((O3D / tid / "shots").glob("*.mp4"))) if (O3D / tid / "shots").is_dir() else 0
    rows.append((tid, len(ep_ids), len(sem_ids), len(miss), 100.0 * len(miss) / max(1, len(ep_ids)), disk))
    tot_missing += len(miss)
for tid, ne, ns, nm, pct, disk in rows:
    print(f"{tid:32s} {ne:3d} {ns:4d} {nm:5d} {pct:5.0f}%  {disk}")
print()
print("模板数:", len(rows), " 会被 assemble 索要但 HQ 从未生成的镜头总数:", tot_missing)
print("至少有 1 个镜头无法对号入座的模板数:", sum(1 for r in rows if r[3] > 0))
