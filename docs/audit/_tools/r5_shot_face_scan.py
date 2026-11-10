# -*- coding: utf-8 -*-
"""R5：逐镜人脸体检（渲染期就能跑，CPU，不占 GPU）。

对 `outputs/templates_3d/<tid>/<shots_subdir>/*.mp4` 每镜抽 2 帧（25% / 60%）做 Haar 人脸检测：
- `no_face` 镜：两帧都检不到脸 → 大概率是「无脸/失控构图」，列 id 供定位与重渲决策；
- `multi_face` 镜：某一帧 ≥2 张脸 → 复核人物复制。

    python docs/audit/_tools/r5_shot_face_scan.py --tid tonight_show --shots shots
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "docs" / "audit" / "_tools"))

import r5_quality_check as qc  # noqa: E402

FRAMES = REPO / "tmp" / "r5_frames"


def scan_clip(clip: Path, clf, cv2, frames_dir: Path) -> dict:
    dur = float(qc.probe(clip).get("duration") or 0)
    counts = []
    paths = []
    for ratio in (0.25, 0.60):
        t = max(0.0, dur * ratio)
        png = frames_dir / f"{clip.stem}_{int(ratio * 100)}.png"
        if not qc.grab(clip, t, png):
            continue
        img = cv2.imread(str(png))
        faces, _img, _gray = qc.detect(img, clf, cv2) if clf is not None else ([], img, None)
        counts.append(len(faces))
        paths.append(str(png))
    return {"id": clip.stem, "duration": round(dur, 2), "faces": counts,
            "no_face": bool(counts) and max(counts) == 0,
            "multi_face": bool(counts) and max(counts) >= 2,
            "frames": paths}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="逐镜人脸体检（CPU）")
    ap.add_argument("--tid", default="tonight_show")
    ap.add_argument("--shots", default="shots", help="shots 目录名（渲染中在 shots/，完成后 shots_<look>/）")
    ap.add_argument("--label", default="", help="输出标签（默认同 --shots）")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args(argv)
    import cv2  # noqa: PLC0415

    clf, clf_path = qc._cascade()
    root = REPO / "outputs" / "templates_3d" / args.tid / args.shots
    clips = sorted(p for p in root.glob("*.mp4") if p.stat().st_size > 10_000)
    if args.limit:
        clips = clips[:args.limit]
    label = args.label or args.shots
    frames_dir = FRAMES / f"{args.tid}_{label}_clips"
    frames_dir.mkdir(parents=True, exist_ok=True)
    rows = [scan_clip(c, clf, cv2, frames_dir) for c in clips]
    no_face = [r for r in rows if r["no_face"]]
    multi = [r for r in rows if r["multi_face"]]
    out = {"tid": args.tid, "shots_subdir": args.shots, "clips": len(rows),
           "cascade": clf_path,
           "no_face_clips": len(no_face),
           "no_face_ratio": round(len(no_face) / max(1, len(rows)), 3),
           "no_face_ids": [r["id"] for r in no_face],
           "multi_face_clips": len(multi),
           "multi_face_ids": [r["id"] for r in multi],
           "items": rows}
    dst = FRAMES / f"{args.tid}_{label}_clips_face.json"
    dst.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: out[k] for k in ("tid", "shots_subdir", "clips", "no_face_clips",
                                          "no_face_ratio", "no_face_ids", "multi_face_ids")},
                     ensure_ascii=False, indent=1))
    print(f"json: {dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
