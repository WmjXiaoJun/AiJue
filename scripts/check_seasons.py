# -*- coding: utf-8 -*-
"""V52：季合同自检（cast / 风格 / 集数 / 成片就绪）。

用法：python scripts/check_seasons.py [--json]
退出码：0 无问题；1 有 warning
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


def check() -> list:
    cat = v5_catalog.load_catalog()
    templates = cat.get("templates") or {}
    series_dir = REPO / "contract" / "v5" / "series"
    rows = []
    for path in sorted(series_dir.glob("*.json")) if series_dir.is_dir() else []:
        data = json.loads(path.read_text(encoding="utf-8"))
        season = data.get("season") or {}
        rules = season.get("rules") or {}
        members = data.get("members") or []
        warnings = []
        missing = [m.get("template") for m in members if m.get("template") not in templates]
        if missing:
            warnings.append(f"成员模板不存在: {missing}")
        if rules.get("same_cast"):
            casts = []
            for m in members:
                tpl = templates.get(m.get("template")) or {}
                casts.append({str(e.get("character")) for e in (tpl.get("cast") or [])
                              if isinstance(e, dict)})
            if casts and not set.intersection(*casts):
                warnings.append("same_cast=true 但各集 cast 无交集")
        if rules.get("same_style"):
            styles = [(templates.get(m.get("template")) or {}).get("render_style")
                      for m in members]
            if len(set(styles)) > 1:
                warnings.append(f"same_style=true 但风格不一致（含缺失）: {sorted(set(styles), key=str)}")
        if rules.get("same_world"):
            styles = [(templates.get(m.get("template")) or {}).get("render_style")
                      for m in members]
            if len(set(styles)) > 1:
                warnings.append(f"same_world=true 但 render_style 不同: {sorted(set(styles), key=str)}")
        sec = rules.get("episode_sec")
        if isinstance(sec, (list, tuple)) and len(sec) == 2:
            low, high = float(sec[0]), float(sec[1])
            for m in members:
                dur = (templates.get(m.get("template")) or {}).get("target_duration")
                if dur is not None and not (low <= float(dur) <= high):
                    warnings.append(f"{m.get('template')} 目标时长 {dur}s 不在 {low:g}-{high:g}s")
        acts = rules.get("acts")
        if isinstance(acts, (list, tuple)) and len(acts) == 2:
            low, high = int(acts[0]), int(acts[1])
            for m in members:
                tpl = templates.get(m.get("template")) or {}
                n = len(tpl.get("acts") or []) or tpl.get("act_count")
                if n is not None and not (low <= int(n) <= high):
                    warnings.append(f"{m.get('template')} 幕数 {n} 不在 {low}-{high}")
        target = int(season.get("target_count") or 0)
        if target and target != len(members):
            warnings.append(f"target_count={target} != 集数 {len(members)}")
        rows.append({"series": data.get("id") or path.stem, "members": len(members),
                     "warnings": warnings})
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="季合同自检")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    rows = check()
    bad = [r for r in rows if r["warnings"]]
    if args.json:
        print(json.dumps({"series": rows, "ok": not bad}, ensure_ascii=False, indent=2))
    else:
        for row in rows:
            flag = "OK " if not row["warnings"] else "WARN"
            print("[{}] {} · {} 集".format(flag, row["series"], row["members"]))
            for warning in row["warnings"]:
                print(f"       - {warning}")
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())