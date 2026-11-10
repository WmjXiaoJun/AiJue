# -*- coding: utf-8 -*-
"""AUDIT_render 静态核验 2：HQ 语义 brief id 与 assemble 用的 episode.json id 是否对齐。"""
import json
from pathlib import Path
REPO = Path(__file__).resolve().parents[3]
V5 = REPO / "outputs" / "templates_v5"
O3D = REPO / "outputs" / "templates_3d"
SEM = REPO / "contract" / "v5" / "cinema3d" / "templates3d"

tids = ["tonight_show", "history_mystery", "custom_tpl_v46_duologue"]
for tid in tids:
    ep_p = V5 / tid / "work" / "episode.json"
    sem_p = SEM / f"{tid}_semantic.json"
    ep_ids = []
    if ep_p.is_file():
        ep = json.loads(ep_p.read_text(encoding="utf-8"))
        ep_ids = [str(s.get("id")) for s in (ep.get("shots") or [])]
    sem_ids = []
    if sem_p.is_file():
        sem_ids = [str(s.get("id")) for s in (json.loads(sem_p.read_text(encoding="utf-8")).get("shots") or [])]
    print(f"== {tid} ==")
    print(f"  episode.json exists={ep_p.is_file()} shots={len(ep_ids)} first={ep_ids[:4]}")
    print(f"  semantic     shots={len(sem_ids)} first={sem_ids[:4]}")
    if ep_ids and sem_ids:
        inter = set(ep_ids) & set(sem_ids)
        print(f"  id 交集={len(inter)}  episode 有而 semantic 无={len(set(ep_ids)-set(sem_ids))}")
    sd = O3D / tid / "shots"
    if sd.is_dir():
        files = sorted(p.name for p in sd.glob("*.mp4"))
        print(f"  outputs/templates_3d/{tid}/shots: {len(files)} mp4, first={files[:4]}")
    else:
        print(f"  outputs/templates_3d/{tid}/shots: 不存在")
