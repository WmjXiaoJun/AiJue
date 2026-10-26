# -*- coding: utf-8 -*-
"""V5.2 角色 AI 美术批量生成（24 角色，串行，ComfyUI 共享队列）。

用法：
    python scripts/gen_v5_character_art.py                # 全部 24 个（复用 3 个自动跳过）
    python scripts/gen_v5_character_art.py --only zhuge,mulan --force
    python scripts/gen_v5_character_art.py --limit 5     # 只跑前 N 个（调试）
    python scripts/gen_v5_character_art.py --list        # 只看列表/是否已有资产

产物：assets/v5/characters/<id>/{portrait.png,front.png,cutout.png,meta.json}
进度：tmp/v52_art/progress.json（每角色实时写）；汇总：tmp/v52_art/batch_report.json
失败不阻断：单角色失败记录后继续，最后打印汇总 JSON（exit 0；全部失败才 exit 1）。
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
PROGRESS = REPO / "tmp" / "v52_art" / "progress.json"
REPORT = REPO / "tmp" / "v52_art" / "batch_report.json"
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
    return {"started_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "total": 0,
            "done": 0, "failed": 0, "skipped": 0, "items": {}}


def save_progress(progress: dict) -> None:
    progress["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    PROGRESS.parent.mkdir(parents=True, exist_ok=True)
    tmp = PROGRESS.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(progress, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(PROGRESS)


def is_reuse(cid: str) -> bool:
    return cid in REUSE_IDS


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="V5.2 角色 AI 美术批量生成")
    ap.add_argument("--only", default=None, help="只生成指定角色（逗号分隔）")
    ap.add_argument("--force", action="store_true", help="已存在也重新生成")
    ap.add_argument("--seed", type=int, default=42, help="基础 seed（默认 42）")
    ap.add_argument("--style", default="anime", help="imagegen 风格（默认 anime）")
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 个")
    ap.add_argument("--list", action="store_true", help="只列出资产状态")
    args = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    ids = all_ids()
    if args.only:
        want = [x.strip() for x in args.only.split(",") if x.strip()]
        ids = [i for i in ids if i in want] or want
    if args.limit and args.limit > 0:
        ids = ids[: args.limit]
    if not ids:
        print(json.dumps({"ok": False, "error": "没有可生成的角色 id"}))
        return 2

    if args.list:
        rows = []
        for cid in ids:
            art = v5_art.character_art(cid)
            rows.append({"id": cid, "exists": art["exists"],
                         "reuse": is_reuse(cid), "size": art["size"],
                         "source": art["source"]})
        print(json.dumps({"ids": rows}, ensure_ascii=False, indent=2))
        return 0

    progress = load_progress()
    progress["total"] = len(ids)
    progress["started_at"] = progress.get("started_at") or time.strftime("%Y-%m-%dT%H:%M:%S")
    progress.setdefault("items", {})
    t_all = time.time()
    ok_count = fail_count = skip_count = 0
    for idx, cid in enumerate(ids, start=1):
        item = progress["items"].get(cid) or {}
        if is_reuse(cid):
            art = v5_art.character_art(cid)
            status = "reuse" if art["exists"] else "reuse_missing"
            progress["items"][cid] = {"status": status, "checked_at": time.strftime("%H:%M:%S"),
                                      "exists": art["exists"], "size": art["size"]}
            skip_count += 1
            print(f"[{idx}/{len(ids)}] {cid:16s} SKIP(reuse) exists={art['exists']}", flush=True)
            save_progress(progress)
            continue
        if item.get("status") == "ok" and not args.force:
            art = v5_art.character_art(cid)
            if art["exists"]:
                skip_count += 1
                print(f"[{idx}/{len(ids)}] {cid:16s} SKIP(done)", flush=True)
                continue
        t0 = time.time()
        try:
            print(f"[{idx}/{len(ids)}] {cid:16s} generating ...", flush=True)
            art = v5_art.generate_character_art(cid, seed=args.seed,
                                                style=args.style, force=args.force)
            sec = round(time.time() - t0, 1)
            meta = art.get("meta") or {}
            progress["items"][cid] = {
                "status": "ok", "seconds": sec, "size": art.get("size"),
                "face": bool(art.get("face_norm")),
                "face_score": round(float((art.get("face_norm") or {}).get("score") or 0), 3),
                "alpha0": (meta.get("cutout_validation") or {}).get("alpha0"),
                "white_ratio": (meta.get("front_white") or {}).get("white_ratio"),
                "washed": bool(((meta.get("cutout_validation") or {}).get("wash") or {}).get("removed")),
                "updated_at": time.strftime("%H:%M:%S"),
            }
            ok_count += 1
            print(f"[{idx}/{len(ids)}] {cid:16s} OK {sec}s "
                  f"size={art.get('size')} face={(art.get('face_norm') or {}).get('score')}",
                  flush=True)
        except Exception as exc:  # noqa: BLE001
            sec = round(time.time() - t0, 1)
            progress["items"][cid] = {"status": "failed", "seconds": sec,
                                      "error": f"{type(exc).__name__}: {exc}"[:500],
                                      "updated_at": time.strftime("%H:%M:%S")}
            fail_count += 1
            print(f"[{idx}/{len(ids)}] {cid:16s} FAIL {sec}s {exc}", flush=True)
        progress["done"] = ok_count
        progress["failed"] = fail_count
        progress["skipped"] = skip_count
        save_progress(progress)

    elapsed = round(time.time() - t_all, 1)
    audit = v5_art.audit(ids)
    summary = {"ok": fail_count == 0, "total": len(ids), "ok_count": ok_count,
               "failed": fail_count, "skipped": skip_count, "elapsed_sec": elapsed,
               "assets_total": audit["total"], "assets_missing": audit["missing"],
               "progress": str(PROGRESS)}
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps({"summary": summary, "progress": progress},
                                 ensure_ascii=False, indent=2), encoding="utf-8")
    progress["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    save_progress(progress)
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if ok_count > 0 or fail_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
