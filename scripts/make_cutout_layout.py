# -*- coding: utf-8 -*-
"""把角色资产整理成渲染代码约定的目录布局（纯复制/派生，不用 AI）。

现状：`assets/v5/characters/<cid>/` 里有 `cutout.png`（RGBA 全身透明）与
`cutout_25d.png`（RGBA 半 3D），但 `pipeline/stage.py` 与 `pipeline/style*.py`
按 `<cid>/cutout_2d/front.png`、`<cid>/cutout_3d/front.png` 找资产 —— 目录不存在，
于是 26 个角色全部"无 cutout"，所有镜头都不叠角色。

本脚本只做**布局派生**（不重画、不抠图）：
    cutout.png      -> cutout_2d/front.png      （2D 干净线稿）
    cutout_25d.png  -> cutout_3d/front.png      （半 3D / 3D 风）
缺 cutout_25d.png 时用 cutout.png 兜底，并在 manifest 里标注 derived_from。

幂等：目标文件已存在且大小一致就跳过；`--force` 覆盖。
provenance 写 `assets/v5/characters/cutout_layout.json`，避免被当成新产出的 AI 素材。

用法：
    python scripts/make_cutout_layout.py
    python scripts/make_cutout_layout.py --force
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from pipeline import config  # noqa: E402

CHARS = Path(config.CHARS_ROOT)
LAYOUT = {
    "cutout_2d/front.png": ("cutout.png",),
    "cutout_3d/front.png": ("cutout_25d.png", "cutout.png"),
}


def _digest(path: Path) -> str:
    h = hashlib.sha1()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="派生角色 cutout 目录布局")
    ap.add_argument("--force", action="store_true", help="覆盖已存在的 front.png")
    args = ap.parse_args(argv)

    manifest_path = CHARS / "cutout_layout.json"
    manifest: dict = {}
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            manifest = {}

    made, skipped, missing = [], [], []
    for cdir in sorted(p for p in CHARS.iterdir() if p.is_dir()):
        cid = cdir.name
        for rel, sources in LAYOUT.items():
            dst = cdir / rel
            src = next((cdir / s for s in sources if (cdir / s).is_file()), None)
            if src is None:
                missing.append(f"{cid}:{rel}")
                continue
            if dst.is_file() and not args.force and dst.stat().st_size == src.stat().st_size:
                skipped.append(f"{cid}:{rel}")
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            manifest[f"{cid}/{rel}"] = {
                "derived_from": str(src.relative_to(CHARS)).replace("\\", "/"),
                "sha1_16": _digest(dst),
                "bytes": dst.stat().st_size,
                "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "note": "布局派生（非新绘），源文件是资产库既有 RGBA cutout",
            }
            made.append(f"{cid}:{rel}")

    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"ok": True, "chars_root": str(CHARS), "made": len(made),
                      "skipped": len(skipped), "missing": missing[:10],
                      "detail_made": made[:12]}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
