# -*- coding: utf-8 -*-
"""成片质检门（参考 OpenMontage reviewer/checkpoint 的做法）。

用法:
  python -m pipeline.qc --video outputs/episodes/ep_001/final_dynamic.mp4 \
      --episode-json backend/data/jobs/job_..._episode.json --out qc_report.json
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FF = ROOT / "tools" / "node_modules" / "ffmpeg-static" / "ffmpeg.exe"
FP = ROOT / "tools" / "node_modules" / "ffprobe-static" / "bin" / "win32" / "x64" / "ffprobe.exe"


def _run(cmd: list, timeout: int = 300) -> tuple[int, str]:
    p = subprocess.run([str(x) for x in cmd], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def probe(video: Path) -> dict:
    rc, out = _run([FP, "-v", "error", "-show_entries", "format=duration,size", "-show_entries",
                    "stream=codec_type,codec_name,width,height,r_frame_rate", "-of", "json", video])
    if rc != 0:
        raise RuntimeError("ffprobe 失败: " + out[-400:])
    data = json.loads(out)
    fmt = data.get("format") or {}
    streams = data.get("streams") or []
    video_s = next((s for s in streams if s.get("codec_type") == "video"), {})
    audio_s = next((s for s in streams if s.get("codec_type") == "audio"), None)
    fps = 0.0
    if video_s.get("r_frame_rate"):
        n, _, d = video_s["r_frame_rate"].partition("/")
        fps = float(n) / float(d or 1)
    return {
        "duration": round(float(fmt.get("duration") or 0), 3),
        "size_mb": round(int(fmt.get("size") or 0) / 1048576, 2),
        "width": video_s.get("width"), "height": video_s.get("height"),
        "fps": round(fps, 2), "video_codec": video_s.get("codec_name"),
        "audio_codec": (audio_s or {}).get("codec_name"), "has_audio": bool(audio_s),
    }


def blackdetect(video: Path) -> int:
    rc, out = _run([FF, "-hide_banner", "-i", video, "-vf", "blackdetect=d=0.01:pix_th=0.10", "-an", "-f", "null", "-"])
    return len(re.findall(r"blackdetect", out))


def subtitle_count(path: Path | None) -> int:
    if not path or not path.exists():
        return 0
    text = path.read_text(encoding="utf-8", errors="replace")
    if path.suffix.lower() == ".ass":
        return len(re.findall(r"^Dialogue:", text, re.M))
    blocks = [b for b in re.split(r"\n\s*\n", text.strip()) if re.search(r"\d\d:\d\d:\d\d", b)]
    return len(blocks)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--episode-json", default=None)
    ap.add_argument("--subtitles", default=None)
    ap.add_argument("--manifest", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--target-duration", type=float, default=None)
    args = ap.parse_args(argv)
    video = Path(args.video)
    if not video.exists():
        print(json.dumps({"ok": False, "error": "video missing"}, ensure_ascii=False))
        return 1
    checks = []
    info = probe(video)

    ep = None
    if args.episode_json and Path(args.episode_json).exists():
        ep = json.loads(Path(args.episode_json).read_text(encoding="utf-8"))
    shots = (ep or {}).get("shots") or []
    expected = args.target_duration
    if expected is None and shots:
        expected = round(sum(float(s.get("duration") or 0) for s in shots), 2)
    if expected:
        delta = abs(info["duration"] - expected)
        checks.append({"name": "duration", "ok": delta <= 0.5, "detail": f"{info['duration']}s vs {expected}s (Δ{delta:.2f}s)"})
    checks.append({"name": "resolution", "ok": (info["width"], info["height"]) == (1280, 720), "detail": f"{info['width']}x{info['height']}"})
    checks.append({"name": "fps", "ok": abs(info["fps"] - 25) < 0.1, "detail": str(info["fps"])})
    checks.append({"name": "audio", "ok": info["has_audio"], "detail": str(info["audio_codec"])})
    blacks = blackdetect(video)
    checks.append({"name": "black_frames", "ok": blacks == 0, "detail": f"{blacks} blackdetect"})

    if shots:
        lines = [s for s in shots if str(s.get("line") or "").strip()]
        sub_path = Path(args.subtitles) if args.subtitles else None
        if sub_path is None:
            for cand in (video.parent / "final.ass", video.parent / "subtitles.srt",
                         video.parent / "work" / "final.ass", video.parent / "work" / "subtitles.srt"):
                if cand.exists():
                    sub_path = cand
                    break
        subs = subtitle_count(sub_path)
        if lines:
            checks.append({"name": "subtitle_coverage", "ok": subs >= max(1, int(len(lines) * 0.9)),
                           "detail": f"{subs} subtitles / {len(lines)} dialogue shots"})
        cards = [s for s in shots if str(s.get("type") or "") in ("title_card", "end_card")]
        checks.append({"name": "shot_count", "ok": len(shots) >= 2, "detail": f"{len(shots)} shots, {len(cards)} cards"})
        speakers = {str(s.get("speaker_name") or "") for s in shots if s.get("speaker_name")}
        checks.append({"name": "speakers", "ok": len(speakers) >= 1, "detail": ",".join(sorted(speakers))})

    if args.manifest and Path(args.manifest).exists():
        man = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
        checks.append({"name": "manifest", "ok": True,
                       "detail": f"dynamic={man.get('dynamic')} transition={man.get('transition')}"})

    ok = all(c["ok"] for c in checks)
    report = {"ok": ok, "video": str(video), "probe": info, "checks": checks}
    if args.out:
        Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
