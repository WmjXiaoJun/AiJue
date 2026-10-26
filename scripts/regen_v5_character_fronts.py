# -*- coding: utf-8 -*-
"""V5.2 front 参考一致性批量重生成（portrait 保留，只重做 front/cutout）。

用 portrait 作 IP-Adapter 参考（weight 0.35→0.25→0.22 回退），重生成白底全身 front
→ cutout.png → 更新 meta（source=imagegen_ref）。

用法：
    python scripts/regen_v5_character_fronts.py                 # 除 3 复用外全部
    python scripts/regen_v5_character_fronts.py --only zhuge
    python scripts/regen_v5_character_fronts.py --include-reuse # 连 laozhou/ali/dawei 也重做
    python scripts/regen_v5_character_fronts.py --list
进度：tmp/v52_art/progress_fronts.json；汇总：tmp/v52_art/fronts_report.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from pipeline import v5_art  # noqa: E402

CHAR_DIR = REPO / "contract" / "v5" / "characters"
PROGRESS = REPO / "tmp" / "v52_art" / "progress_fronts.json"
REPORT = REPO / "tmp" / "v52_art" / "fronts_report.json"
REUSE_IDS = tuple(v5_art.REUSE_IDS)


def all_ids() -> list[str]:
    return sorted(p.stem for p in CHAR_DIR.glob("*.json"))


def load_progress() -> dict:
    try:
        data = json.loads(PROGRESS.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except Exception:  # noqa: BLE001
        pass
    return {"started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "total": 0, "ok": 0, "failed": 0, "skipped": 0, "items": {}}


def save_progress(progress: dict) -> None:
    progress["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    PROGRESS.parent.mkdir(parents=True, exist_ok=True)
    tmp = PROGRESS.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(progress, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(PROGRESS)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="V5.2 front 参考一致性批量重生成")
    ap.add_argument("--only", default=None, help="指定角色（逗号分隔）")
    ap.add_argument("--seed", type=int, default=43)
    ap.add_argument("--include-reuse", action="store_true",
                    help="连 laozhou/ali/dawei 复用角色也重做")
    ap.add_argument("--list", action="store_true", help="只列出现状")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    ids = all_ids()
    if not args.include_reuse:
        ids = [i for i in ids if i not in REUSE_IDS]
    if args.only:
        want = [x.strip() for x in args.only.split(",") if x.strip()]
        ids = [i for i in want if i in ids] or want
    if args.limit and args.limit > 0:
        ids = ids[: args.limit]
    if not ids:
        print(json.dumps({"ok": False, "error": "没有可重做的角色"}, ensure_ascii=False))
        return 2

    if args.list:
        rows = []
        for cid in ids:
            art = v5_art.character_art(cid)
            rows.append({"id": cid, "exists": art["exists"],
                         "portrait": art["portrait"], "size": art["size"],
                         "source": art["source"]})
        print(json.dumps({"ids": rows}, ensure_ascii=False, indent=2))
        return 0

    progress = load_progress()
    progress["total"] = len(ids)
    progress["started_at"] = progress.get("started_at") or time.strftime("%Y-%m-%dT%H:%M:%S")
    progress.setdefault("items", {})
    t_all = time.time()
    ok_n = fail_n = 0
    for idx, cid in enumerate(ids, start=1):
        t0 = time.time()
        try:
            print(f"[{idx}/{len(ids)}] {cid:16s} front-ref ...", flush=True)
            info = v5_art.generate_character_front(cid, seed=args.seed, force=True)
            meta = info.get("meta") or {}
            sec = round(time.time() - t0, 1)
            progress["items"][cid] = {
                "status": "ok", "seconds": sec, "size": info.get("size"),
                "alpha0": (meta.get("cutout_validation") or {}).get("alpha0"),
                "white_ratio": (meta.get("front_white") or {}).get("white_ratio"),
                "bg_mode": meta.get("bg_mode"), "attempts": meta.get("attempts"),
                "ip": meta.get("ip_adapter"), "seed": meta.get("seed"),
                "updated_at": time.strftime("%H:%M:%S"),
            }
            ok_n += 1
            print(f"[{idx}/{len(ids)}] {cid:16s} OK {sec}s size={info.get('size')} "
                  f"bg={meta.get('bg_mode')} alpha0="
                  f"{(meta.get('cutout_validation') or {}).get('alpha0')}", flush=True)
        except Exception as exc:  # noqa: BLE001
            sec = round(time.time() - t0, 1)
            progress["items"][cid] = {"status": "failed", "seconds": sec,
                                      "error": f"{type(exc).__name__}: {exc}"[:500],
                                      "updated_at": time.strftime("%H:%M:%S")}
            fail_n += 1
            print(f"[{idx}/{len(ids)}] {cid:16s} FAIL {sec}s {exc}", flush=True)
        progress["ok"] = ok_n
        progress["failed"] = fail_n
        save_progress(progress)

    elapsed = round(time.time() - t_all, 1)
    audit = v5_art.audit(ids)
    summary = {"ok": fail_n == 0, "total": len(ids), "ok_count": ok_n,
               "failed": fail_n, "elapsed_sec": elapsed,
               "missing_assets": audit["missing"], "progress": str(PROGRESS)}
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps({"summary": summary, "progress": progress},
                                 ensure_ascii=False, indent=2), encoding="utf-8")
    progress["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    save_progress(progress)
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if ok_n > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
