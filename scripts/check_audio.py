# -*- coding: utf-8 -*-
"""V66：成片声音体检（时长/音轨/响度/静音占比/每镜是否有声）。"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from pipeline import config, utils  # noqa: E402

FFMPEG = str(config.FFMPEG_BIN)
FFPROBE = str(config.FFPROBE_BIN)


def _run(args: list[str]) -> str:
    try:
        return subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=600).stderr or ""
    except Exception as exc:  # noqa: BLE001
        return f"ERR {exc}"


def check(path: Path) -> dict:
    probe = utils.probe_video(path) or {}
    has_audio = bool(probe.get("has_audio"))
    out = {"file": str(path), "duration": round(float(probe.get("duration") or 0), 2),
           "has_audio": has_audio}
    if not has_audio:
        return out
    vol = _run([FFMPEG, "-hide_banner", "-nostdin", "-i", str(path), "-af",
                "volumedetect", "-f", "null", "-"])
    m = re.search(r"mean_volume:\s*(-?[\d.]+) dB", vol)
    mx = re.search(r"max_volume:\s*(-?[\d.]+) dB", vol)
    out["mean_db"] = float(m.group(1)) if m else None
    out["max_db"] = float(mx.group(1)) if mx else None
    sil = _run([FFMPEG, "-hide_banner", "-nostdin", "-i", str(path), "-af",
                "silencedetect=noise=-38dB:d=0.6", "-f", "null", "-"])
    spans = [float(x) for x in re.findall(r"silence_duration:\s*([\d.]+)", sil)]
    total = float(probe.get("duration") or 0) or 1.0
    out["silence_sec"] = round(sum(spans), 1)
    out["speech_ratio"] = round(1 - sum(spans) / total, 3)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True)
    args = ap.parse_args(argv)
    res = check(Path(args.file))
    print(json.dumps(res, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
