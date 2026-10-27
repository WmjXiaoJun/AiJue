# -*- coding: utf-8 -*-
"""V6.2：24 角色 AAM XL（Anime Mix 2.5D 半写实）portrait 批量生成。

产物 assets/v5/characters/<id>/portrait_25d.png + portrait_25d_meta.json
（不覆盖 portrait.png / portrait_real.png）
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
ASSETS = REPO / "assets" / "v5" / "characters"
SPECS = REPO / "tmp" / "v52_art" / "specs"
REPORT = REPO / "tmp" / "v57_25d" / "aam_batch.json"
CKPT = "AAM_XL_Anime_Mix.safetensors"
EXTRA = ("2.5D semi-realistic anime, detailed face, detailed skin shading, glossy lips, "
         "detailed eyes, soft cinematic lighting, korean beauty, elegant, high detail, 8k, "
         "masterpiece, best quality")
NEG = ("photo, photorealistic, real human, flat, low detail, text, watermark, "
       "deformed, extra fingers, bad hands")


def gen(cid: str, seed: int = 77, size: str = "896x1344") -> dict:
    spec = SPECS / f"{cid}.json"
    if not spec.is_file():
        return {"id": cid, "ok": False, "error": "missing spec"}
    outdir = ASSETS / cid
    outdir.mkdir(parents=True, exist_ok=True)
    tmpdir = outdir / "aam_tmp"
    log = tmpdir / "gen.log"
    tmpdir.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, "-m", "imagegen.cli", "generate",
           "--spec", str(spec), "--outdir", str(tmpdir),
           "--mode", "portrait", "--style", "anime", "--ckpt", CKPT,
           "--seed", str(seed), "--size", size,
           "--prompt-extra", EXTRA, "--negative-extra", NEG, "--timeout", "420"]
    t0 = time.time()
    proc = subprocess.run(cmd, cwd=str(REPO), capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    log.write_text((proc.stdout or "") + "\n" + (proc.stderr or ""), encoding="utf-8")
    src = tmpdir / "portrait.png"
    if proc.returncode != 0 or not src.is_file():
        return {"id": cid, "ok": False, "error": (proc.stderr or "")[-300:]}
    dst = outdir / "portrait_25d.png"
    dst.write_bytes(src.read_bytes())
    meta = {"character": cid, "model": CKPT, "seed": seed, "size": size,
            "prompt_extra": EXTRA, "negative_extra": NEG, "file": str(dst),
            "seconds": round(time.time() - t0, 1),
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    (outdir / "portrait_25d_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"id": cid, "ok": True, "file": str(dst), "seconds": meta["seconds"]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default=None)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--seed", type=int, default=77)
    ap.add_argument("--size", default="896x1344")
    a = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    ids = sorted(p.stem for p in SPECS.glob("*.json"))
    want = [x.strip() for x in (a.only or "").split(",") if x.strip()] or ids
    rows = []
    for i, cid in enumerate(want, 1):
        dst = ASSETS / cid / "portrait_25d.png"
        if not a.force and dst.is_file():
            rows.append({"id": cid, "ok": True, "skipped": True})
            print(f"[{i}/{len(want)}] skip {cid}", flush=True)
            continue
        try:
            r = gen(cid, seed=a.seed, size=a.size)
        except Exception as exc:  # noqa: BLE001
            r = {"id": cid, "ok": False, "error": str(exc)}
        rows.append(r)
        print(f"[{i}/{len(want)}] {r}", flush=True)
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": sum(1 for r in rows if r.get("ok")), "total": len(rows)},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
