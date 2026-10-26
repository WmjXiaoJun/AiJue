# -*- coding: utf-8 -*-
"""V6.1：用 RealVisXL 写实模型批量生成 24 角色高细节真人感 portrait。

产物：assets/v5/characters/<id>/portrait_real.png（不覆盖既有 anime 资产）
模型：ComfyUI/models/checkpoints/RealVisXL_V5.0_fp16.safetensors
用法：
    python scripts/gen_v5_realistic_portraits.py                 # 缺失的才生成
    python scripts/gen_v5_realistic_portraits.py --only ali,zhuge --force
    python scripts/gen_v5_realistic_portraits.py --list
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
REPORT = REPO / "tmp" / "v57_real" / "real_batch.json"
DEFAULT_CKPT = "RealVisXL_V5.0_fp16.safetensors"
REAL_EXTRA = ("photorealistic, ultra realistic portrait, real human skin texture, "
              "detailed face, detailed makeup, glossy lips, korean beauty, "
              "fashion editorial, professional studio photography, soft cinematic lighting, "
              "sharp focus, 8k, high detail, upper body, looking at viewer")
NEG_EXTRA = ("anime, cartoon, illustration, 2d, cel shading, flat colors, sketch, "
             "lowres, blurry, deformed, text, watermark, extra fingers")


def ids() -> list[str]:
    if SPECS.is_dir() and any(SPECS.glob("*.json")):
        return sorted(p.stem for p in SPECS.glob("*.json"))
    return sorted(p.name for p in ASSETS.iterdir() if p.is_dir())


def gen(cid: str, ckpt: str, seed: int, size: str) -> dict:
    spec = SPECS / f"{cid}.json"
    if not spec.is_file():
        return {"id": cid, "ok": False, "error": "missing spec"}
    outdir = ASSETS / cid
    outdir.mkdir(parents=True, exist_ok=True)
    log = outdir / "real_tmp" / "gen.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, "-m", "imagegen.cli", "generate",
           "--spec", str(spec), "--outdir", str(outdir / "real_tmp"),
           "--mode", "portrait", "--style", "realistic", "--ckpt", ckpt,
           "--seed", str(seed), "--size", size,
           "--prompt-extra", REAL_EXTRA, "--negative-extra", NEG_EXTRA,
           "--timeout", "420"]
    t0 = time.time()
    proc = subprocess.run(cmd, cwd=str(REPO), capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    log.write_text((proc.stdout or "") + "\n" + (proc.stderr or ""), encoding="utf-8")
    src = outdir / "real_tmp" / "portrait.png"
    if proc.returncode != 0 or not src.is_file():
        return {"id": cid, "ok": False, "error": (proc.stderr or "")[-300:]}
    dst = outdir / "portrait_real.png"
    dst.write_bytes(src.read_bytes())
    meta = {
        "character": cid, "model": ckpt, "seed": seed, "size": size,
        "prompt_style": "realistic", "prompt_extra": REAL_EXTRA,
        "negative_extra": NEG_EXTRA, "file": str(dst),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "seconds": round(time.time() - t0, 1),
    }
    (outdir / "real_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"id": cid, "ok": True, "file": str(dst),
            "seconds": meta["seconds"], "bytes": dst.stat().st_size}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="RealVisXL 写实角色 portrait 批量生成")
    ap.add_argument("--only", default=None, help="逗号分隔角色 id")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--seed", type=int, default=77)
    ap.add_argument("--size", default="896x1344")
    ap.add_argument("--ckpt", default=DEFAULT_CKPT)
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    all_ids = ids()
    if a.list:
        print(json.dumps([{"id": cid, "real": (ASSETS / cid / "portrait_real.png").is_file()}
                          for cid in all_ids], ensure_ascii=False, indent=1))
        return 0
    want = [x.strip() for x in (a.only or "").split(",") if x.strip()] or all_ids
    rows = []
    for i, cid in enumerate(want, 1):
        if not a.force and (ASSETS / cid / "portrait_real.png").is_file():
            rows.append({"id": cid, "ok": True, "skipped": True})
            print(f"[{i}/{len(want)}] skip {cid}", flush=True)
            continue
        try:
            r = gen(cid, a.ckpt, a.seed, a.size)
        except Exception as exc:  # noqa: BLE001
            r = {"id": cid, "ok": False, "error": str(exc)}
        rows.append(r)
        print(f"[{i}/{len(want)}] {r}", flush=True)
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(json.dumps({"rows": rows}, ensure_ascii=False, indent=2),
                          encoding="utf-8")
    print(json.dumps({"ok": sum(1 for r in rows if r.get("ok")), "total": len(rows)},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
