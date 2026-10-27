# -*- coding: utf-8 -*-
"""V6.2：24 角色 2.5D 半写实（非真人照片）portrait 批量生成。

流水线：RealVis 写实 portrait（结构/细节基底） --Animagine img2img--> 2.5D 半写实插画。
产物：assets/v5/characters/<id>/portrait_25d.png + portrait_25d_meta.json
（保留 portrait_real / portrait 原资产，不覆盖）

用法：
    python scripts/gen_v5_stylized_portraits.py
    python scripts/gen_v5_stylized_portraits.py --only ali,zhuge --force
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from imagegen import comfy  # noqa: E402
from imagegen.prompt import build_prompt, load_spec  # noqa: E402
from imagegen.workflow import (DEFAULT_CFG, DEFAULT_SAMPLER, DEFAULT_SCHEDULER,  # noqa: E402
                               DEFAULT_STEPS)

ASSETS = REPO / "assets" / "v5" / "characters"
SPECS = REPO / "tmp" / "v52_art" / "specs"
COMFY_ROOT = comfy.DEFAULT_COMFY_ROOT
ANIME = "animagine-xl-4.0.safetensors"
EXTRA = ("2.5D anime CG illustration, semi-realistic anime style, highly detailed face, "
         "detailed skin shading, glossy lips, detailed eyes, elegant fashion, "
         "soft cinematic lighting, depth of field, masterpiece, 8k")


def _copy(src: Path, name: str) -> str:
    dst = Path(COMFY_ROOT) / "input" / "zaopianju_25d" / name
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(Path(src).read_bytes())
    return f"zaopianju_25d/{name}"


def run_wf(wf: dict, out_png: Path) -> dict:
    t0 = time.time()
    pid = comfy.submit(comfy.DEFAULT_SERVER, wf, log=lambda *a, **k: None)
    rec = comfy.wait_prompt(comfy.DEFAULT_SERVER, pid, 900, lambda *a, **k: None)
    item = comfy.fetch_images(rec)[0]
    saved = comfy.save_image(comfy.DEFAULT_SERVER, COMFY_ROOT, item, str(out_png),
                             log=lambda *a, **k: None)
    return {"out": saved, "seconds": round(time.time() - t0, 1), "prompt_id": pid}


def img2img_anime(pos: str, neg: str, init: Path, ref: Path, out_png: Path,
                  seed: int = 77, denoise: float = 0.52, ipa: float = 0.5) -> dict:
    init_name = _copy(init, f"{out_png.stem}_init.png")
    ref_name = _copy(ref, f"{out_png.stem}_ref.png")
    wf = {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": ANIME}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": pos, "clip": ["1", 1]}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"text": neg, "clip": ["1", 1]}},
        "4": {"class_type": "LoadImage", "inputs": {"image": init_name}},
        "11": {"class_type": "VAEEncode", "inputs": {"pixels": ["4", 0], "vae": ["1", 2]}},
        "8": {"class_type": "IPAdapterUnifiedLoader",
              "inputs": {"model": ["1", 0], "preset": "PLUS (high strength)"}},
        "10": {"class_type": "LoadImage", "inputs": {"image": ref_name}},
        "9": {"class_type": "IPAdapterAdvanced",
              "inputs": {"model": ["8", 0], "ipadapter": ["8", 1], "image": ["10", 0],
                         "weight": float(ipa), "weight_type": "style transfer",
                         "start_at": 0.0, "end_at": 0.8,
                         "combine_embeds": "concat", "embeds_scaling": "V only"}},
        "5": {"class_type": "KSampler",
              "inputs": {"model": ["9", 0], "positive": ["2", 0], "negative": ["3", 0],
                         "latent_image": ["11", 0], "seed": int(seed),
                         "steps": DEFAULT_STEPS, "cfg": DEFAULT_CFG,
                         "sampler_name": DEFAULT_SAMPLER, "scheduler": DEFAULT_SCHEDULER,
                         "denoise": float(denoise)}},
        "6": {"class_type": "VAEDecode", "inputs": {"samples": ["5", 0], "vae": ["1", 2]}},
        "7": {"class_type": "SaveImage", "inputs": {"images": ["6", 0],
                                                    "filename_prefix": "zaopianju_25d/g"}},
    }
    return run_wf(wf, out_png)


def gen(cid: str, seed: int = 77, denoise: float = 0.52, ipa: float = 0.5) -> dict:
    spec_path = SPECS / f"{cid}.json"
    cdir = ASSETS / cid
    real = cdir / "portrait_real.png"
    anime = cdir / "portrait.png"
    if not spec_path.is_file() or not real.is_file() or not anime.is_file():
        return {"id": cid, "ok": False, "error": "missing spec/portrait_real/portrait"}
    spec = load_spec(str(spec_path))
    built = build_prompt(spec, style="stylized_25d", composition="portrait",
                         prompt_extra=EXTRA)
    dst = cdir / "portrait_25d.png"
    r = img2img_anime(built["positive"], built["negative"], real, anime, dst,
                      seed=seed, denoise=denoise, ipa=ipa)
    meta = {"character": cid, "pipeline": "RealVis portrait -> Animagine img2img",
            "model": ANIME, "seed": seed, "denoise": denoise, "ipa": ipa,
            "prompt": built["positive"], "negative": built["negative"],
            "file": str(dst), "seconds": r["seconds"],
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    (cdir / "portrait_25d_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"id": cid, "ok": True, "file": str(dst), "seconds": r["seconds"]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default=None)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--seed", type=int, default=77)
    ap.add_argument("--denoise", type=float, default=0.52)
    ap.add_argument("--ipa", type=float, default=0.5)
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    ids = sorted(p.stem for p in SPECS.glob("*.json"))
    if a.list:
        print(json.dumps([{"id": cid, "25d": (ASSETS / cid / "portrait_25d.png").is_file()}
                          for cid in ids], ensure_ascii=False, indent=1))
        return 0
    want = [x.strip() for x in (a.only or "").split(",") if x.strip()] or ids
    rows = []
    for i, cid in enumerate(want, 1):
        if not a.force and (ASSETS / cid / "portrait_25d.png").is_file():
            rows.append({"id": cid, "ok": True, "skipped": True})
            print(f"[{i}/{len(want)}] skip {cid}", flush=True)
            continue
        try:
            r = gen(cid, seed=a.seed, denoise=a.denoise, ipa=a.ipa)
        except Exception as exc:  # noqa: BLE001
            r = {"id": cid, "ok": False, "error": str(exc)}
        rows.append(r)
        print(f"[{i}/{len(want)}] {r}", flush=True)
        (REPO / "tmp" / "v57_25d" / "batch.json").write_text(
            json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": sum(1 for r in rows if r.get("ok")), "total": len(rows)},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
