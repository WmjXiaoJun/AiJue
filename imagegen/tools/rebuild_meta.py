# -*- coding: utf-8 -*-
"""从产物 PNG 的 ComfyUI 内嵌 prompt 元数据重建 meta.json。

ComfyUI 的 SaveImage 会把「提交给 /prompt 的完整 API prompt」写进 PNG 文本块，
所以即使只跑了某个 mode、或 meta.json 被增量覆盖，也能从图片本身还原：
positive / negative / seed / size / checkpoint / steps / cfg / sampler / scheduler。

用法:
  python imagegen/tools/rebuild_meta.py --spec contract/characters/laozhou.json --outdir outputs/concepts/laozhou
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, REPO_ROOT)

from imagegen.cli import EXPRESSIONS, VIEWS, collect_files  # noqa: E402
from imagegen.prompt import build_prompt, load_spec  # noqa: E402


def expected_images(outdir):
    items = [("portrait", "portrait.png")]
    for v in VIEWS:
        items.append((v, os.path.join("three_view", v + ".png")))
    for e in EXPRESSIONS:
        items.append((e, os.path.join("expressions", e + ".png")))
    items.append(("scene", "scene.png"))
    return items


def image_meta(path):
    with Image.open(path) as im:
        w, h = im.size
        raw = im.info.get("prompt")
    data = {}
    if raw:
        try:
            data = json.loads(raw)
        except Exception:
            data = {}
    pos = neg = seed = ckpt = None
    steps = cfg = sampler = scheduler = None
    try:
        pos = data["2"]["inputs"]["text"]
        neg = data["3"]["inputs"]["text"]
        seed = data["5"]["inputs"]["seed"]
        steps = data["5"]["inputs"]["steps"]
        cfg = data["5"]["inputs"]["cfg"]
        sampler = data["5"]["inputs"]["sampler_name"]
        scheduler = data["5"]["inputs"]["scheduler"]
        ckpt = data["1"]["inputs"]["ckpt_name"]
    except (KeyError, TypeError):
        pass
    return {
        "width": w, "height": h, "prompt": pos, "negative": neg, "seed": seed,
        "checkpoint": ckpt, "steps": steps, "cfg": cfg,
        "sampler": sampler, "scheduler": scheduler,
    }


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", required=True)
    ap.add_argument("--outdir", required=True)
    args = ap.parse_args(argv)
    spec = load_spec(args.spec)
    outdir = os.path.abspath(args.outdir)

    images = []
    for tag, rel in expected_images(outdir):
        path = os.path.join(outdir, rel)
        if not os.path.isfile(path):
            continue
        info = image_meta(path)
        rec = {"tag": tag, "path": path, **{k: v for k, v in info.items() if k != "checkpoint"}}
        images.append(rec)
        print("[meta] %-14s %dx%d seed=%s" % (tag, info["width"], info["height"], info["seed"]))

    base = build_prompt(spec, style="anime")
    ckpt = next((im.get("checkpoint") for im in [image_meta(os.path.join(outdir, r))
                                                 for _, r in expected_images(outdir)
                                                 if os.path.isfile(os.path.join(outdir, r))]),
                "animagine-xl-4.0.safetensors")
    meta = {
        "id": spec["id"],
        "name": spec.get("name"),
        "title": spec.get("title"),
        "persona": spec.get("persona"),
        "mode": "all",
        "style": "anime",
        "model": ckpt or "animagine-xl-4.0.safetensors",
        "steps": 28, "cfg": 5.5, "sampler": "euler_ancestral", "scheduler": "normal",
        "seed": 42,
        "size": {"portrait": [832, 1216], "other": [1024, 1024]},
        "negative": base["negative"],
        "prompt": {rec["tag"]: rec.get("prompt") for rec in images},
        "created_at": None,
        "files": collect_files(outdir, spec["id"]),
        "images": images,
    }
    path = os.path.join(outdir, "meta.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print(json.dumps({"ok": True, "meta": path, "images": len(images)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
