# -*- coding: utf-8 -*-
"""V5.5 姿态变体批量生成：24 角色 × {point, think}。

- 每个角色用 portrait 作 IP-Adapter 参考，生成白底全身姿态图 → 抠图：
  `assets/v5/characters/<id>/pose_<pose>/front.png`
  `assets/v5/characters/<id>/cutout_<pose>.png`
  `assets/v5/characters/<id>/pose_meta.json`（prompt/seed/prompt_id/裁剪校验）
- 不动现有 front/cutout；已存在默认 skip（可 --force 覆盖，支持断点续跑）。
- mystery_guest / robot 生成失败时允许回退 front 资产（render 侧本来就用 front）。

用法：
    python scripts/gen_v5_character_poses.py                 # 全部 24×2
    python scripts/gen_v5_character_poses.py --only zhuge --poses point
    python scripts/gen_v5_character_poses.py --list
进度：tmp/v55_poses/progress.json；汇总：tmp/v55_poses/report.json
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from pipeline import v5_art  # noqa: E402

CHAR_DIR = REPO / "contract" / "v5" / "characters"
ASSETS = REPO / "assets" / "v5" / "characters"
OUT = REPO / "tmp" / "v55_poses"
PROGRESS = OUT / "progress.json"
REPORT = OUT / "report.json"


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
            "items": {}, "ok": 0, "failed": 0, "fallback": 0}


def save_progress(progress: dict) -> None:
    progress["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    OUT.mkdir(parents=True, exist_ok=True)
    tmp = PROGRESS.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(progress, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(PROGRESS)


def make_front_fallback(cid: str, pose: str) -> dict | None:
    """黑名单/失败角色：把现有 front 资产复制成姿态资产（render 会用 front）。"""
    src_dir = ASSETS / cid
    src_front, src_cutout = src_dir / "front.png", src_dir / "cutout.png"
    if not (src_front.is_file() and src_cutout.is_file()):
        return None
    pose_dir = src_dir / f"pose_{pose}"
    pose_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src_front, pose_dir / "front.png")
    dst_cutout = src_dir / f"cutout_{pose}.png"
    shutil.copy2(src_cutout, dst_cutout)
    from PIL import Image
    with Image.open(src_cutout) as im:
        size = list(im.size)
        validation = v5_art._validate_cutout(im.convert("RGBA"))
    base_meta = {}
    try:
        base_meta = json.loads((src_dir / "meta.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        pass
    entry = {
        "id": cid, "pose": pose, "source": "front_fallback",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "files": {"front": str(pose_dir / "front.png"), "cutout": str(dst_cutout)},
        "sizes": {"cutout": size},
        "cutout_validation": validation,
        "face_norm": base_meta.get("face_norm"),
        "note": "该角色（mystery_guest/robot 或无脸）pose 变体回退 front",
    }
    meta_path = src_dir / "pose_meta.json"
    meta = {}
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        meta = {}
    meta["id"] = cid
    meta[pose] = entry
    meta["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    v5_art.clear_cache()
    return entry


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="V5.5 角色姿态变体批量生成")
    ap.add_argument("--only", default=None, help="指定角色（逗号分隔）")
    ap.add_argument("--poses", default="point,think", help="姿态列表，默认 point,think")
    ap.add_argument("--seed", type=int, default=43)
    ap.add_argument("--force", action="store_true", help="覆盖已存在资产")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    poses = [p.strip() for p in str(args.poses).split(",") if p.strip()]
    poses = [p for p in poses if p in v5_art.POSE_NAMES]
    ids = all_ids()
    if args.only:
        want = [x.strip() for x in args.only.split(",") if x.strip()]
        ids = [i for i in want if i in ids] or want
    if args.limit and args.limit > 0:
        ids = ids[: args.limit]
    if not ids or not poses:
        print(json.dumps({"ok": False, "error": "没有可生成的 id/pose"}, ensure_ascii=False))
        return 2

    if args.list:
        rows = []
        for cid in ids:
            for p in poses:
                art = v5_art.character_pose_art(cid, p)
                rows.append({"id": cid, "pose": p, "exists": art.get("exists"),
                             "cutout": art.get("cutout"),
                             "size": art.get("size"),
                             "source": art.get("source"),
                             "fallback": bool(art.get("pose_fallback_front"))})
        print(json.dumps({"rows": rows}, ensure_ascii=False, indent=2))
        return 0

    progress = load_progress()
    progress.setdefault("items", {})
    progress["total"] = len(ids) * len(poses)
    progress["started_at"] = progress.get("started_at") or time.strftime(
        "%Y-%m-%dT%H:%M:%S")
    t_all = time.time()
    ok_n = fail_n = fb_n = 0
    total = len(ids) * len(poses)
    idx = 0
    for cid in ids:
        for p in poses:
            idx += 1
            key = f"{cid}/{p}"
            item = progress["items"].get(key) or {}
            existing = (ASSETS / cid / f"cutout_{p}.png").is_file()
            if existing and not args.force:
                ok_n += 1
                item.update({"status": "ok", "skipped": True})
                progress["items"][key] = item
                print(f"[{idx}/{total}] {key:28s} SKIP（已存在）", flush=True)
                continue
            t0 = time.time()
            try:
                print(f"[{idx}/{total}] {key:28s} generating ...", flush=True)
                info = v5_art.generate_character_pose(cid, p, seed=args.seed,
                                                      force=args.force)
                meta = info.get("meta") or {}
                sec = round(time.time() - t0, 1)
                item.update({
                    "status": "ok", "seconds": sec, "size": info.get("size"),
                    "alpha0": (meta.get("cutout_validation") or {}).get("alpha0"),
                    "bg_mode": meta.get("bg_mode"), "seed": meta.get("seed"),
                    "ip": meta.get("ip_adapter"),
                    "prompt_id": meta.get("prompt_id"),
                    "updated_at": time.strftime("%H:%M:%S"),
                })
                ok_n += 1
                print(f"[{idx}/{total}] {key:28s} OK {sec}s size={info.get('size')} "
                      f"alpha0={(meta.get('cutout_validation') or {}).get('alpha0')}",
                      flush=True)
            except Exception as exc:  # noqa: BLE001
                sec = round(time.time() - t0, 1)
                fb_entry = None
                if cid in v5_art.POSE_SKIP_CHARS:
                    fb_entry = make_front_fallback(cid, p)
                if fb_entry is not None:
                    item.update({"status": "fallback", "seconds": sec,
                                 "size": (fb_entry.get("sizes") or {}).get("cutout"),
                                 "error": f"{type(exc).__name__}: {exc}"[:400],
                                 "updated_at": time.strftime("%H:%M:%S")})
                    fb_n += 1
                    print(f"[{idx}/{total}] {key:28s} FALLBACK（front 回退）{sec}s",
                          flush=True)
                else:
                    item.update({"status": "failed", "seconds": sec,
                                 "error": f"{type(exc).__name__}: {exc}"[:400],
                                 "updated_at": time.strftime("%H:%M:%S")})
                    fail_n += 1
                    print(f"[{idx}/{total}] {key:28s} FAIL {sec}s {exc}", flush=True)
            progress["items"][key] = item
            progress["ok"] = ok_n
            progress["failed"] = fail_n
            progress["fallback"] = fb_n
            save_progress(progress)

    elapsed = round(time.time() - t_all, 1)
    missing = []
    for cid in ids:
        for p in poses:
            if not (ASSETS / cid / f"cutout_{p}.png").is_file():
                missing.append(f"{cid}/{p}")
    summary = {
        "ok": not missing,
        "expected": total, "assets_ok": total - len(missing),
        "ok_count": ok_n, "fallback": fb_n, "failed": fail_n,
        "missing": missing, "elapsed_sec": elapsed, "seed": args.seed,
        "poses": poses, "ids": ids, "progress": str(PROGRESS),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps({"summary": summary, "progress": progress},
                                 ensure_ascii=False, indent=2), encoding="utf-8")
    progress["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    save_progress(progress)
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if not missing else 1


if __name__ == "__main__":
    sys.exit(main())
