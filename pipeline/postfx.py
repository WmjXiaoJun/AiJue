# -*- coding: utf-8 -*-
"""V67：成片后期特效（暗角/胶片颗粒/色散/漏光），用于 3D/2D/真实三种观感统一质感。"""
from __future__ import annotations

import json
from pathlib import Path

from . import utils


def apply(src: Path, out: Path, *, strength: str = "medium",
          light_leak: bool = True, work: Path | None = None) -> Path:
    """在成片上叠加：暗角 + 胶片颗粒 + 轻微色散 + 漏光（可选）。"""
    work = utils.ensure_dir(work or (src.parent / "_postfx"))
    level = {"soft": 1.0, "medium": 1.6, "strong": 2.4}.get(strength, 1.6)
    vf = [f"vignette=PI/{4.2 / level:.2f}",
          f"noise=alls={int(4 * level)}:allf=t+u",
          f"rgbashift=rh={int(1 * level)}:bh=-{int(1 * level)}:rv={int(-0.5 * level)}:bv={int(0.5 * level)}"]
    args = ["-i", str(src)]
    graph = None
    if light_leak:
        # 斜向暖色漏光：gradients 生成 -> screen 混合
        leak = work / "leak.png"
        try:
            utils.ffmpeg(["-f", "lavfi", "-i",
                          "gradients=s=1280x720:c0=0x000000:c1=0x7a3b12:x0=0:y0=0:x1=1280:y1=720:"
                          "nb_colors=2:type=linear:d=1:r=25",
                          "-frames:v", "1", "-y", str(leak)])
        except Exception:  # noqa: BLE001
            leak = None
        if leak and leak.is_file():
            args += ["-loop", "1", "-i", str(leak)]
            graph = ("[0:v]" + ",".join(vf) + "[base];"
                     "[base][1:v]blend=all_mode=screen:all_opacity=0.18[v]")
        else:
            graph = None
    if graph:
        args += ["-filter_complex", graph, "-map", "[v]", "-map", "0:a?",
                 "-shortest", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                 "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-y", str(out)]
    else:
        args += ["-vf", ",".join(vf), "-c:v", "libx264", "-preset", "veryfast",
                 "-crf", "20", "-c:a", "aac", "-b:a", "192k",
                 "-movflags", "+faststart", "-y", str(out)]
    utils.ffmpeg(args)
    if out.is_file() and out.stat().st_size > 0:
        out.replace(src)
    return src
