import json
from pathlib import Path
REPO = Path(".").resolve()
for tid in ("tonight_show", "history_mystery", "custom_tpl_v46_duologue"):
    for tag, p in (("V5_OUT", REPO/"outputs/templates_v5"/tid/"work"/"episode.json"),
                   ("OUT3D ", REPO/"outputs/templates_3d"/tid/"work"/"episode.json")):
        if p.is_file():
            d = json.loads(p.read_text(encoding="utf-8"))
            ids = [str(s.get("id")) for s in (d.get("shots") or [])]
            print(f"{tid:26s} {tag} exists=True shots={len(ids):3d} first={ids[:3]} mtime_ok")
        else:
            print(f"{tid:26s} {tag} exists=False")
