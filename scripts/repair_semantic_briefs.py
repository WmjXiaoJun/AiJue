# -*- coding: utf-8 -*-
"""修复 semantic brief 的重复 shot id，并给每镜标注 act_index（幂等）。

背景：`scripts/expand_3d_shots.py` 用 `v5_e{added+1:03d}` 命名新镜，`added`
每次运行从 0 起算 → 重复跑就撞车（工作区 13/30 份 brief 出现 v5_e001 重复 2–9 次）。
`templates3d_hq` 用 `shots/<id>.mp4` 落盘、`crc32(tid+id)` 派生 seed → 同 id 互相覆盖。

本脚本：
1. 重复 id 改成 `<prefix>_x{next:03d}`（不复用已有编号，保证全局唯一）；
2. 每镜写 `act_index`：brief 前缀与模板 acts 一一对应（已核实 scene 完全对齐），
   多出来的镜头 `act_index = -1`（扩写镜）；
3. 备份为 `<name>.json.bak`（覆盖式保存最近一次原文）；
4. 幂等：没有重复 id 且已有 act_index 时不动文件。

用法：
    python scripts/repair_semantic_briefs.py --dry-run
    python scripts/repair_semantic_briefs.py
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BRIEFS = REPO / "contract" / "v5" / "cinema3d" / "templates3d"
CUSTOM = REPO / "contract" / "v5" / "custom" / "templates"


def _acts_for(tid: str) -> list[dict]:
    p = CUSTOM / f"{tid}.json"
    if not p.is_file():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []
    return [a for a in (data.get("acts") or []) if isinstance(a, dict)]


def _next_x_index(ids: list[str]) -> int:
    nums = [int(m.group(1)) for i in ids if (m := re.match(r"^v5_x(\d{3,})$", str(i)))]
    return (max(nums) + 1) if nums else 1


def repair(path: Path, dry: bool, rename_dupes: bool) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    shots = data.get("shots") or []
    tid = path.name.replace("_semantic.json", "")
    acts = _acts_for(tid)
    ids = [str(s.get("id") or "") for s in shots]
    dupes = {k: v for k, v in Counter(ids).items() if v > 1 and k}
    changed = False
    if rename_dupes and dupes:
        seen: set[str] = set()
        next_x = _next_x_index(ids)
        for shot in shots:
            sid = str(shot.get("id") or "")
            if sid and sid not in seen:
                seen.add(sid)
                continue
            new_id = f"v5_x{next_x:03d}"
            next_x += 1
            while new_id in seen or new_id in ids:
                new_id = f"v5_x{next_x:03d}"
                next_x += 1
            shot["id"] = new_id
            seen.add(new_id)
            changed = True
    # 每镜 act_index
    for idx, shot in enumerate(shots):
        want = idx if idx < len(acts) else -1
        if shot.get("act_index") != want:
            shot["act_index"] = want
            changed = True
    ids_after = [str(s.get("id") or "") for s in shots]
    dupes_after = {k: v for k, v in Counter(ids_after).items() if v > 1 and k}
    assert not dupes_after, f"{path.name}: 修复后仍有重复 id {dupes_after}"
    if changed and not dry:
        shutil.copy2(path, path.with_suffix(".json.bak"))
        data["shots"] = shots
        path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"template": tid, "shots": len(shots), "acts": len(acts),
            "dupes_before": sum(dupes.values()) - len(dupes) if dupes else 0,
            "changed": changed}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="修复 semantic brief 重复 id + 标注 act_index")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--dir", default=str(BRIEFS))
    ap.add_argument("--no-rename", action="store_true", help="只标 act_index，不改 id")
    args = ap.parse_args(argv)
    paths = sorted(Path(args.dir).glob("*_semantic.json"))
    rows = []
    for p in paths:
        try:
            rows.append(repair(p, args.dry_run, rename_dupes=not args.no_rename))
        except AssertionError as exc:
            print(f"[FAIL] {exc}")
            return 1
    changed = [r for r in rows if r["changed"]]
    print(json.dumps({"ok": True, "dry_run": args.dry_run, "briefs": len(rows),
                      "changed": len(changed),
                      "dupes_fixed": sum(r["dupes_before"] for r in rows),
                      "detail": changed[:12]}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
