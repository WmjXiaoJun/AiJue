# -*- coding: utf-8 -*-
"""V49 M1 补资产：给缺 front/cutout/face_norm 的角色补齐 AI 美术资产（不重生成图片）。

背景（M1 验收红线③④）：
- `caocao`：新 portrait 已入库，但 `meta.json` 少了 `face_norm`（口型 overlay 依赖）；
- `custom_old_inventor` / `custom_street_kid`：只有 portrait + `three_view/*`，
  根目录缺 `front.png` / `cutout.png` / `face_norm`。

做法（全部走仓库现有算法，不新造口径）：
1. `front.png` 直接取 imagegen 已出的白底全身图 `three_view/front.png`
   （与 `v5_art.generate_character_art` 的 "three_view → front" 完全同源，不重渲、不回滚）；
2. `cutout.png` = `v5_art.remove_white(front)`（白底不达标时退 `remove_background_robust`），
   再用 `v5_art._validate_cutout()` 卡 alpha0/四角/bbox，不达标直接失败、不写坏资产；
3. `face_norm` = `v5_art._norm_geom(v5_art.face_geometry(cutout, "cutout"))`
   （`_art_face` 的口径就是 cutout 归一化几何）；`face_portrait_norm` 另按 portrait 记一份；
4. meta.json 只做增量合并（保留 seed/model/images/created_at 等既有生成证据）。

用法：
    python scripts/repair_v5_char_art_v49.py --dry-run       # 只报告要做什么
    python scripts/repair_v5_char_art_v49.py                 # 默认修 3 个目标角色
    python scripts/repair_v5_char_art_v49.py --only caocao
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

from PIL import Image  # noqa: E402

from pipeline import v5_art  # noqa: E402

TARGETS = ("caocao", "custom_old_inventor", "custom_street_kid")


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _face(cutout_png: Path) -> dict:
    im = Image.open(cutout_png).convert("RGBA")
    return v5_art._norm_geom(v5_art.face_geometry(im, "cutout"))


def _portrait_face(portrait_png: Path) -> dict | None:
    if not portrait_png.is_file():
        return None
    try:
        im = Image.open(portrait_png)
        return v5_art._norm_geom(v5_art.face_geometry(im, "portrait"))
    except Exception:  # noqa: BLE001
        return None


def repair(cid: str, *, dry_run: bool = False, log=print) -> dict:
    base = v5_art.ASSETS_DIR / cid
    meta_path = base / "meta.json"
    portrait = base / "portrait.png"
    front = base / "front.png"
    cutout_path = base / "cutout.png"
    three_front = base / "three_view" / "front.png"
    row = {"id": cid, "actions": [], "problems": []}
    if not base.is_dir():
        row["problems"].append(f"资产目录缺失 {base}")
        return row

    # ---- 1) front.png：复用 imagegen 的白底全身图 ----
    if not front.is_file():
        if not three_front.is_file():
            row["problems"].append("缺 front.png 且无 three_view/front.png 可复用")
            return row
        img = Image.open(three_front)
        white = v5_art._white_report(img)
        row["front_white"] = {"ok": white["ok"],
                              "white_ratio": white.get("white_ratio")}
        row["actions"].append(f"front.png <- three_view/front.png {img.size} "
                              f"white_ok={white['ok']}")
        if not dry_run:
            shutil.copyfile(three_front, front)

    # ---- 2) cutout.png：remove_white + 质量校验 ----
    if not cutout_path.is_file():
        src = Image.open(front if front.is_file() else three_front)
        white = v5_art._white_report(src)
        if white["ok"]:
            cut, wash = v5_art.remove_white(src, with_report=True)
            mode = "white"
        else:
            cut, wash = v5_art.remove_background_robust(src)
            mode = "adaptive"
        validation = v5_art._validate_cutout(cut)
        validation["wash"] = wash
        validation["mode"] = mode
        row["cutout_validation"] = validation
        if not validation["ok"]:
            row["problems"].append(f"cutout 质量校验不达标：{validation}")
            return row
        row["actions"].append(f"cutout.png <- {mode} cutout {cut.size} "
                              f"alpha0={validation['alpha0']}")
        if not dry_run:
            cut.save(cutout_path)

    # ---- 3) face_norm / face_portrait_norm ----
    meta = _read_json(meta_path)
    row["meta_before"] = {"source": meta.get("source"),
                          "seed": meta.get("seed"),
                          "images": len(meta.get("images") or []),
                          "face_norm": bool(meta.get("face_norm"))}
    if not meta.get("face_norm"):
        if dry_run:
            row["actions"].append("face_norm <- cutout 归一化脸几何（dry-run 未计算）")
        else:
            norm = _face(cutout_path)
            meta["face_norm"] = norm
            row["actions"].append(f"face_norm <- cutout "
                                  f"score={norm.get('score')} est={norm.get('estimated')}")
    if not meta.get("face_portrait_norm"):
        if dry_run:
            row["actions"].append("face_portrait_norm <- portrait（dry-run 未计算）")
        else:
            pnorm = _portrait_face(portrait)
            if pnorm:
                meta["face_portrait_norm"] = pnorm
                row["actions"].append(
                    f"face_portrait_norm <- portrait score={pnorm.get('score')} "
                    f"est={pnorm.get('estimated')}")
    if not dry_run:
        meta.setdefault("files", {})
        if isinstance(meta["files"], dict):
            if front.is_file():
                meta["files"]["front"] = str(front)
            if cutout_path.is_file():
                meta["files"]["cutout"] = str(cutout_path)
        meta.setdefault("sizes", {})
        if isinstance(meta["sizes"], dict) and cutout_path.is_file():
            with Image.open(cutout_path) as im:
                meta["sizes"]["cutout"] = list(im.size)
        meta["face_norm_updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        meta["face_norm_source"] = "pipeline/v5_art._norm_geom(face_geometry(cutout))"
        _write_json(meta_path, meta)
        v5_art.clear_cache()
    row["meta_after"] = {"source": meta.get("source"),
                         "seed": meta.get("seed"),
                         "images": len(meta.get("images") or []),
                         "face_norm": bool(meta.get("face_norm"))}
    return row


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="V49 M1 角色美术资产补齐")
    ap.add_argument("--only", default=None, help="逗号分隔的 id（默认 3 个目标角色）")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    ids = ([x.strip() for x in args.only.split(",") if x.strip()]
           if args.only else list(TARGETS))
    rows = [repair(cid, dry_run=args.dry_run) for cid in ids]
    print(json.dumps({"dry_run": args.dry_run, "rows": rows},
                     ensure_ascii=False, indent=2))
    return 0 if all(not r["problems"] for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
