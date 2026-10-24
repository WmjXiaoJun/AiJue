# -*- coding: utf-8 -*-
"""从 SDXL 全身白底立绘制作 2D 动漫全身透明 cutout（泛洪去白底 + 边缘羽化）。"""
from __future__ import annotations
import json, os, sys
from collections import deque
from pathlib import Path
from PIL import Image, ImageFilter
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
CHARS = ["laozhou", "ali", "dawei"]
VIEWS = ["front", "three_quarter"]
WHITE = 238
TOL = 18

def remove_white(im: Image.Image) -> Image.Image:
    im = im.convert("RGBA")
    a = np.array(im).astype(np.int16)
    h, w = a.shape[:2]
    rgb = a[:, :, :3]
    # 近白像素（亮度高、饱和度低）
    mx = rgb.max(axis=2); mn = rgb.min(axis=2)
    whiteish = (mn >= WHITE - TOL) & ((mx - mn) <= 12)
    # 从四边泛洪，只删除与边界连通的白色，避免误删白色衣服
    seen = np.zeros((h, w), dtype=bool)
    q = deque()
    for x in range(w):
        for y in (0, h - 1):
            if whiteish[y, x] and not seen[y, x]:
                seen[y, x] = True; q.append((y, x))
    for y in range(h):
        for x in (0, w - 1):
            if whiteish[y, x] and not seen[y, x]:
                seen[y, x] = True; q.append((y, x))
    while q:
        y, x = q.popleft()
        for dy, dx in ((1,0),(-1,0),(0,1),(0,-1)):
            ny, nx = y + dy, x + dx
            if 0 <= ny < h and 0 <= nx < w and whiteish[ny, nx] and not seen[ny, nx]:
                seen[ny, nx] = True; q.append((ny, nx))
    alpha = np.where(seen, 0, 255).astype(np.uint8)
    aout = np.dstack([rgb.astype(np.uint8), alpha])
    out = Image.fromarray(aout, "RGBA")
    # 羽化 alpha 边缘 1px
    al = out.getchannel("A").filter(ImageFilter.GaussianBlur(0.8))
    out.putalpha(al)
    bbox = out.getbbox()
    if bbox:
        pad = 8
        box = (max(0, bbox[0]-pad), max(0, bbox[1]-pad), min(w, bbox[2]+pad), min(h, bbox[3]+pad))
        out = out.crop(box)
    return out

def main():
    meta = {}
    for cid in CHARS:
        for v in VIEWS:
            src = ROOT / "outputs" / "concepts" / cid / "three_view" / (v + ".png")
            if not src.exists():
                print("skip missing", src); continue
            outdir = ROOT / "outputs" / "characters" / cid / "cutout_2d"
            outdir.mkdir(parents=True, exist_ok=True)
            dst = outdir / (v + ".png")
            im = remove_white(Image.open(src))
            im.save(dst)
            meta.setdefault(cid, {})[v] = {"src": str(src), "out": str(dst), "size": im.size,
                                           "alpha_ratio": round(float((np.array(im)[:,:,3] > 0).mean()), 3)}
            print(cid, v, im.size, meta[cid][v]["alpha_ratio"])
    out = ROOT / "outputs" / "characters" / "cutout_2d_meta.json"
    out.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print("meta", out)

if __name__ == "__main__":
    main()
