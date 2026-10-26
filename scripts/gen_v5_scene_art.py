#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V5.2 场景 AI 美术批量生成（SDXL Animagine，ComfyUI 8188 共享队列串行）。

用法：
    python scripts/gen_v5_scene_art.py --dry-run                       # 只打印每场景 prompt/来源
    python scripts/gen_v5_scene_art.py --only pilot --sheet tmp/v52_scene_pilot/sheet.png
    python scripts/gen_v5_scene_art.py --only ancient_map,battlefield
    python scripts/gen_v5_scene_art.py --seed 20261101                  # 全量（61 场景）
    python scripts/gen_v5_scene_art.py --force --only ancient_map      # 重生成

产物：
    assets/v5/scenes/<scene_id>.png + <scene_id>.json（prompt/seed/prompt_id/gen_sec）
    tmp/v52_art/scene_progress.json（进度，失败不阻断）

只提交 ComfyUI 队列、不重启/不改 ComfyUI；与 render-dev 角色图共享队列自动串行。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from PIL import Image, ImageDraw  # noqa: E402

from pipeline import utils  # noqa: E402
from pipeline import v5_scene_art as art  # noqa: E402

PROGRESS_PATH = REPO / "tmp" / "v52_art" / "scene_progress.json"


def parse_size(text: str) -> tuple[int, int]:
    a, _, b = str(text).lower().partition("x")
    return int(a), int(b)


def parse_ids(text: str) -> list[str]:
    if not text:
        return []
    if str(text).strip().lower() == "pilot":
        return list(art.PILOT_SCENES)
    if str(text).strip().lower() == "history":
        return list(art.HISTORY_SCENES)
    return [t.strip() for t in str(text).split(",") if t.strip()]


def build_contact_sheet(items: list[dict], out_png: Path, *,
                        cols: int = 3, tile=(640, 360)) -> bool:
    """把已生成的 plate 拼 contact sheet（带 scene_id 标签）。"""
    ok_items = [it for it in items if it.get("path") and Path(it["path"]).is_file()]
    if not ok_items:
        return False
    rows = (len(ok_items) + cols - 1) // cols
    label_h, head = 22, 34
    w = cols * tile[0]
    h = head + rows * (tile[1] + label_h)
    sheet = Image.new("RGB", (w, h), "#101010")
    d = ImageDraw.Draw(sheet)
    d.text((10, 8), "V5.2 scene plates (SDXL Animagine)", fill=(240, 240, 240))
    for i, item in enumerate(ok_items):
        r, c = divmod(i, cols)
        x, y = c * tile[0], head + r * (tile[1] + label_h)
        try:
            sheet.paste(Image.open(item["path"]).convert("RGB").resize(tile, Image.BILINEAR),
                        (x, y))
        except Exception:  # noqa: BLE001
            continue
        d.text((x + 6, y + tile[1] + 3),
               f"{item['scene']}  seed={item.get('seed')}", fill=(205, 215, 235))
    utils.ensure_dir(out_png.parent)
    sheet.save(out_png)
    return True


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="V5.2 场景 plate 批量生成（SDXL）")
    ap.add_argument("--only", default="", help="只生成指定场景：id1,id2 / pilot / history")
    ap.add_argument("--seed", type=int, default=art.DEFAULT_SEED)
    ap.add_argument("--force", action="store_true", help="强制重生成（忽略已有）")
    ap.add_argument("--size", default=f"{art.DEFAULT_SIZE[0]}x{art.DEFAULT_SIZE[1]}")
    ap.add_argument("--outdir", default=str(art.ASSETS_DIR))
    ap.add_argument("--server", default=art.DEFAULT_SERVER)
    ap.add_argument("--model", default=art.DEFAULT_MODEL)
    ap.add_argument("--timeout", type=float, default=art.DEFAULT_TIMEOUT)
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 个（调试）")
    ap.add_argument("--sheet", default="", help="额外输出 contact sheet PNG 路径")
    ap.add_argument("--dry-run", action="store_true", help="只打印 prompt，不提交 ComfyUI")
    args = ap.parse_args(argv)

    scene_ids = art.list_scene_ids()
    art_prompts = art.load_art_prompts()
    # 场景库与 prompts.json 取并集：prompts.json 含 custom 场景（如 custom_holo_gallery），
    # 即使没有 contract/v5/scenes/<id>.json 只要能拿到完整 prompt 也可生成（61 个）。
    scene_ids = sorted(set(scene_ids) | set(art_prompts))
    if not scene_ids:
        print(f"[error] 没有找到场景 JSON: {art.SCENES_DIR}")
        return 2
    wanted = parse_ids(args.only)
    if wanted:
        missing = [s for s in wanted if s not in scene_ids]
        if missing:
            print(f"[warn] 场景不存在（已跳过）: {missing}")
        scene_ids = [s for s in wanted if s in scene_ids]
    if args.limit:
        scene_ids = scene_ids[: int(args.limit)]
    size = parse_size(args.size)
    prompts = art_prompts
    print(f"[plan] 场景 {len(scene_ids)} 个 / seed={args.seed} / size={size[0]}x{size[1]} "
          f"/ out={args.outdir} / prompts.json={'YES' if prompts else 'no(fallback)'}")

    progress = {
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "updated_at": "",
        "seed": args.seed,
        "size": list(size),
        "outdir": str(Path(args.outdir)),
        "total": len(scene_ids),
        "done": 0, "failed": 0, "reused": 0,
        "items": {},
        "failures": [],
        "elapsed_sec": 0.0,
    }
    utils.write_json(PROGRESS_PATH, progress)

    started = time.time()
    results: list[dict] = []
    for i, scene_id in enumerate(scene_ids, 1):
        try:
            scene = art.load_scene(scene_id)
        except art.V5SceneArtError:
            scene = {}
            if scene_id not in prompts:
                print(f"[warn] {scene_id}: 无场景 JSON 也无 prompts.json 条目，跳过")
                continue
        built = art.build_scene_prompt(scene_id, scene=scene, prompts=prompts)
        if args.dry_run:
            print(f"[dry-run] {i:02d}/{len(scene_ids)} {scene_id} "
                  f"source={built['source']}")
            print(f"   POS: {built['positive'][:300]}")
            print(f"   NEG: {built['negative'][:200]}")
            results.append({"scene": scene_id, "ok": True, "dry_run": True,
                            "prompt": built["positive"], "source": built["source"]})
            continue
        t0 = time.time()
        try:
            info = art.generate_scene_plate(
                scene_id, seed=args.seed, size=size, force=args.force,
                out_dir=args.outdir, server=args.server, model=args.model,
                timeout=args.timeout, scene=scene, prompts=prompts)
            gen_sec = round(time.time() - t0, 1)
            status = "reused" if info.get("reused") else "ok"
            results.append({"scene": scene_id, "ok": True, "status": status,
                            "path": info.get("path"), "seed": info.get("seed"),
                            "gen_sec": info.get("gen_sec"), "prompt_id": info.get("prompt_id"),
                            "prompt_source": info.get("prompt_source"),
                            "elapsed_sec": gen_sec})
            if info.get("reused"):
                progress["reused"] += 1
            else:
                progress["done"] += 1
            print(f"[{i:02d}/{len(scene_ids)}] {status:6s} {scene_id} "
                  f"{float(info.get('gen_sec') or 0):5.1f}s seed={info.get('seed')}")
        except Exception as exc:  # noqa: BLE001 - 失败不阻断
            error = f"{type(exc).__name__}: {exc}"
            progress["failed"] += 1
            progress["failures"].append({"scene": scene_id, "error": error[:600]})
            results.append({"scene": scene_id, "ok": False, "error": error[:600]})
            print(f"[{i:02d}/{len(scene_ids)}] FAIL   {scene_id}: {error[:240]}")
        finally:
            progress["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            progress["elapsed_sec"] = round(time.time() - started, 1)
            progress["items"][scene_id] = {
                "ok": results[-1].get("ok") if results else False,
                "status": results[-1].get("status") if results else "",
                "path": results[-1].get("path") if results else "",
                "gen_sec": results[-1].get("gen_sec") if results else None,
                "error": results[-1].get("error") if results else "",
            }
            utils.write_json(PROGRESS_PATH, progress)

    # 资产就绪校验
    ready = [s for s in scene_ids if art.scene_plate_ok(s, out_dir=args.outdir)]
    sheet_path = ""
    if args.sheet and not args.dry_run:
        if build_contact_sheet(results, Path(args.sheet)):
            sheet_path = str(Path(args.sheet))
            print(f"[sheet] {sheet_path}")
    elapsed = round(time.time() - started, 1)
    summary = {
        "ok": progress["failed"] == 0 and len(ready) == len(scene_ids),
        "scenes": len(scene_ids),
        "ready": len(ready),
        "generated": progress["done"],
        "reused": progress["reused"],
        "failed": progress["failed"],
        "failures": progress["failures"],
        "elapsed_sec": elapsed,
        "avg_sec": round(elapsed / max(1, progress["done"]), 1) if progress["done"] else 0.0,
        "outdir": str(Path(args.outdir)),
        "seed": args.seed,
        "size": list(size),
        "sheet": sheet_path,
        "progress": str(PROGRESS_PATH),
        "dry_run": bool(args.dry_run),
        "results": results,
    }
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if summary["ok"] or args.dry_run else 1


if __name__ == "__main__":
    raise SystemExit(main())
