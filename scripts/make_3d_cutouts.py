# -*- coding: utf-8 -*-
"""3D 动漫立绘去白底 → cutout_3d（复用 2D cutout 的泛洪去白底逻辑）。"""
from __future__ import annotations
import json, sys
from pathlib import Path
from PIL import Image, ImageFilter
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from make_2d_cutouts import remove_white  # noqa: E402

ROOT = Path(r"D:\BaiduSyncdisk\wmj_work\XJ\AiJue")
CHARS = ["laozhou", "ali", "dawei"]
meta = {}
for cid in CHARS:
    src = ROOT / "outputs" / "concepts" / cid / "three_view_3d" / "front.png"
    if not src.exists():
        print("skip", cid); continue
    outdir = ROOT / "outputs" / "characters" / cid / "cutout_3d"
    outdir.mkdir(parents=True, exist_ok=True)
    dst = outdir / "front.png"
    im = remove_white(Image.open(src))
    im.save(dst)
    a = np.array(im)
    meta[cid] = {"src": str(src), "out": str(dst), "size": im.size,
                 "alpha_ratio": round(float((a[:, :, 3] > 0).mean()), 3)}
    print(cid, im.size, meta[cid]["alpha_ratio"])
(ROOT / "outputs" / "characters" / "cutout_3d_meta.json").write_text(
    json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
print("done")
