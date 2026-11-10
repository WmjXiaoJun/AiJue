# -*- coding: utf-8 -*-
"""V69：给资产库角色生成「3D 动画 / 真实」两套头像，供锁脸关键帧使用。

原 portrait.png 是 2D 动漫立绘；直接做 IP-Adapter 会把 3D/真实 观感拉回动漫。
这里用原立绘做弱参考（0.35~0.45）+ 目标风格提示，产出同角色的 3D/真实版头像。
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

from pipeline import config, utils  # noqa: E402

CHARS = REPO / "assets" / "v5" / "characters"
SPECS = REPO / "tmp" / "v52_art" / "specs"

STYLE_PROMPT = {
    "3d": ("3D animated feature film character, Pixar-style CG rendering, stylized "
           "but realistic materials, subsurface skin, soft rim light, neutral background"),
    "realistic": ("photorealistic portrait of a real person, natural skin texture, "
                  "85mm lens, soft studio light, neutral background"),
}
NEG = {
    "3d": "anime, cel shading, flat colors, line art, 2d, photorealistic human",
    "realistic": "anime, cartoon, cel shading, 3d render, illustration, painting",
}


def build_spec(cid: str) -> Path:
    contract = json.loads((REPO / "contract/v5/characters" / f"{cid}.json").read_text(encoding="utf-8"))
    SPECS.mkdir(parents=True, exist_ok=True)
    spec = {
        "id": cid,
        "name": str(contract.get("label") or contract.get("name") or cid),
        "persona": str(contract.get("persona") or ""),
        "appearance": contract.get("appearance") or {},
        "style": {"primary": "realistic", "variants": ["realistic"]},
        "extra_tags": ["cinematic"],
    }
    path = SPECS / f"{cid}_look.json"
    path.write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def make(cid: str, look: str, force: bool = False) -> dict:
    src = CHARS / cid / "portrait.png"
    dst = CHARS / cid / f"portrait_{look}.png"
    if not src.is_file():
        return {"character": cid, "ok": False, "error": "缺少 portrait.png"}
    if dst.is_file() and not force:
        return {"character": cid, "ok": True, "cached": True, "out": str(dst)}
    spec = build_spec(cid)
    outdir = utils.ensure_dir(CHARS / cid / f"_look_{look}")
    cmd = [sys.executable, "-m", "imagegen.cli", "generate",
           "--spec", str(spec), "--outdir", str(outdir), "--mode", "portrait",
           "--style", "realistic", "--size", "832x1216",
           "--prompt-extra", STYLE_PROMPT[look],
           "--negative-extra", NEG[look],
           "--ref", str(src), "--ip-adapter-strength", "0.42",
           "--ip-adapter-end-at", "0.8", "--seed", "20261106",
           "--timeout", "600"]
    proc = subprocess.run(cmd, cwd=str(REPO), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=660)
    candidates = sorted(outdir.glob("*.png"))
    if proc.returncode != 0 or not candidates:
        tail = "\\n".join(((proc.stdout or "") + (proc.stderr or "")).strip().splitlines()[-6:])
        return {"character": cid, "ok": False, "error": tail[-400:]}
    utils.ffmpeg(["-i", str(candidates[-1]), "-y", str(dst)])
    return {"character": cid, "ok": True, "out": str(dst)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--characters", default="")
    ap.add_argument("--looks", default="3d,realistic")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args(argv)
    ids = ([c.strip() for c in args.characters.split(",") if c.strip()] if args.characters
           else sorted(p.name for p in CHARS.iterdir() if (p / "portrait.png").is_file()))
    rows = []
    for cid in ids:
        for look in [x.strip() for x in args.looks.split(",") if x.strip()]:
            rows.append(make(cid, look, force=args.force))
    print(json.dumps(rows, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
