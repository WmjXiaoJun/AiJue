# -*- coding: utf-8 -*-
"""补齐 33 个模板的封面（模板墙没封面就显得"空/难看"）。

素材优先级：
1. 该模板已渲镜头的首帧（`outputs/templates_3d/<tid>/shots/*.mp4`，挑最亮的）
2. 该模板用到的场景图（`outputs/scene_plates/<scene>*.png`）
3. 纯色品牌卡 + 模板名（最后的兜底，至少不是空白）

输出：`outputs/templates_3d/<tid>/poster.jpg`（1280×720，正面构图，不加字）
另写 `outputs/templates_3d/_posters.json` 记录每张的来源，便于追溯。

用法：python scripts/make_template_posters.py [--force] [--only tid1,tid2]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from pipeline import config, utils, v5_catalog  # noqa: E402

FFMPEG = str(config.FFMPEG_BIN)
OUT = Path(config.OUTPUTS_ROOT) / "templates_3d"
REPORT = OUT / "_posters.json"


def _luma(p: Path) -> float:
    try:
        with Image.open(p) as im:
            return float(np.asarray(im.convert("L").resize((64, 36)), dtype=float).mean())
    except Exception:  # noqa: BLE001
        return 0.0


def _from_shot(tid: str, tmp: Path) -> Path | None:
    shots = sorted((OUT / tid / "shots").glob("*.mp4"))
    if not shots:
        return None
    # 用中间那一条（开头常是片头/黑场）
    clip = shots[len(shots) // 2]
    frame = tmp / f"{tid}_poster.png"
    d = float(utils.probe_duration(clip) or 0) or 3.0
    utils.ffmpeg(["-ss", f"{d * 0.4:.2f}", "-i", str(clip), "-frames:v", "1",
                  "-y", str(frame)], check=False)
    return frame if frame.is_file() and frame.stat().st_size > 20_000 else None


def _from_plate(tid: str, template: dict) -> Path | None:
    scenes = [str((a or {}).get("scene") or "") for a in (template.get("acts") or [])]
    scenes = [s for s in scenes if s]
    plate_dir = Path(config.OUTPUTS_ROOT) / "scene_plates"
    scene_lib = Path(config.REPO_ROOT) / "assets" / "v5" / "scenes"
    for scene in scenes:
        cands = [plate_dir / f"{scene}.png", *sorted(plate_dir.glob(f"{scene}__*.png")),
                 scene_lib / f"{scene}.png", *sorted(scene_lib.glob(f"{scene}__*.png"))]
        for cand in cands:
            if cand.is_file() and cand.stat().st_size > 20_000:
                return cand
    for pattern in (f"{tid}*.png", "*.png"):
        for cand in sorted(scene_lib.glob(pattern))[:3]:
            if cand.is_file() and _luma(cand) >= 55:
                return cand
    return None


def _fallback_card(tid: str, label: str, dst: Path) -> None:
    im = Image.new("RGB", (1280, 720), (24, 26, 32))
    draw = ImageDraw.Draw(im)
    for i in range(720):                      # 轻微纵向渐变
        draw.line([(0, i), (1280, i)], fill=(24 + i // 18, 26 + i // 20, 34 + i // 16))
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/msyhbd.ttc", 96)
    except Exception:  # noqa: BLE001
        font = ImageFont.load_default()
    text = label or tid
    box = draw.textbbox((0, 0), text, font=font)
    draw.text(((1280 - (box[2] - box[0])) / 2, (720 - (box[3] - box[1])) / 2 - 20),
              text, font=font, fill=(235, 226, 200))
    im.save(dst, "JPEG", quality=88)


def build_one(tid: str, template: dict, tmp: Path, force: bool) -> dict:
    dst = OUT / tid / "poster.jpg"
    if dst.is_file() and not force and dst.stat().st_size > 40_000:
        return {"template": tid, "ok": True, "reused": True}
    dst.parent.mkdir(parents=True, exist_ok=True)
    src_kind, src = "", None
    frame = _from_shot(tid, tmp)
    if frame is not None and _luma(frame) >= 45:
        src_kind, src = "shot_frame", frame
    if src is None:
        plate = _from_plate(tid, template)
        if plate is not None:
            src_kind, src = "scene_plate", plate
    if src is not None:
        utils.ffmpeg(["-i", str(src), "-vf",
                      "scale=1280:720:force_original_aspect_ratio=increase,"
                      "crop=1280:720,eq=brightness=0.04:saturation=1.05",
                      "-frames:v", "1", "-q:v", "2", "-y", str(dst)], check=False)
    if not dst.is_file() or dst.stat().st_size < 20_000:
        src_kind = "fallback_card"
        _fallback_card(tid, str(template.get("label") or tid), dst)
    return {"template": tid, "ok": True, "source": src_kind,
            "bytes": dst.stat().st_size}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="补齐模板封面")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--only", default="")
    args = ap.parse_args(argv)
    cat = v5_catalog.load_catalog()
    templates = cat.get("templates") or {}
    ids = ([t.strip() for t in args.only.split(",") if t.strip()] if args.only
           else sorted(templates))
    tmp = Path(config.REPO_ROOT) / "tmp" / "posters"
    tmp.mkdir(parents=True, exist_ok=True)
    rows = [build_one(tid, templates.get(tid) or {}, tmp, args.force) for tid in ids]
    REPORT.write_text(json.dumps({"count": len(rows), "items": rows}, ensure_ascii=False,
                                 indent=1), encoding="utf-8")
    sources = {}
    for r in rows:
        sources[r.get("source") or "reused"] = sources.get(r.get("source") or "reused", 0) + 1
    print(json.dumps({"ok": True, "count": len(rows), "sources": sources,
                      "report": str(REPORT)}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
