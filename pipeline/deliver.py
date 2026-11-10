# -*- coding: utf-8 -*-
"""V50 · 交付规格导出：单集成片 → 1080P 横版 / 1080×1920 竖版。

用法：
    python -m pipeline.deliver --template custom_tpl_v46_cyber --hd
    python -m pipeline.deliver --template custom_tpl_v46_cyber --vertical
    python -m pipeline.deliver --all --hd          # 全部有 final 的模板

产物（与原片同目录，不覆盖 final.mp4）：
    outputs/templates_3d/<tid>/final_1080p.mp4
    outputs/templates_3d/<tid>/final_9x16.mp4
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ROOTS = (REPO / "outputs" / "templates_3d", REPO / "outputs" / "templates_2d")
FFMPEG = REPO / "tools" / "node_modules" / "ffmpeg-static" / "ffmpeg.exe"
FFPROBE = REPO / "tools" / "node_modules" / "ffprobe-static" / "bin" / "win32" / "x64" / "ffprobe.exe"


def _probe(path: Path) -> dict:
    try:
        raw = subprocess.run([str(FFPROBE), "-v", "error", "-select_streams", "v:0",
                              "-show_entries", "stream=width,height",
                              "-show_entries", "format=duration,size",
                              "-of", "json", str(path)],
                             capture_output=True, text=True, timeout=60).stdout
        data = json.loads(raw or "{}")
        stream = (data.get("streams") or [{}])[0]
        fmt = data.get("format") or {}
        return {"width": stream.get("width"), "height": stream.get("height"),
                "duration": round(float(fmt.get("duration") or 0), 3),
                "bytes": int(fmt.get("size") or 0)}
    except Exception:  # noqa: BLE001
        return {}


def source_of(template_id: str) -> Path | None:
    for root in ROOTS:
        candidate = root / template_id / "final.mp4"
        if candidate.is_file() and candidate.stat().st_size > 0:
            return candidate
    return None


def deliver(template_id: str, *, hd: bool = True, vertical: bool = False,
            force: bool = False) -> dict:
    src = source_of(template_id)
    if src is None:
        return {"ok": False, "template": template_id, "error": "没有 final.mp4"}
    out_name = ("final_9x16.mp4" if vertical
                else ("final_1080p.mp4" if hd else "final_720p.mp4"))
    out = src.parent / out_name
    if out.is_file() and not force:
        return {"ok": True, "template": template_id, "out": str(out), "cached": True,
                **(_probe(out))}
    if vertical:
        vf = "crop=ih*9/16:ih,scale=1080:1920:flags=lanczos"
    elif hd:
        vf = "scale=1920:1080:flags=lanczos"
    else:
        vf = "scale=1280:720:flags=lanczos"
    t0 = time.time()
    base = [str(FFMPEG), "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(src), "-vf", vf,
            "-c:v", "libx264", "-crf", "19", "-preset", "medium",
            "-pix_fmt", "yuv420p"]
    tail = ["-shortest", "-movflags", "+faststart", str(out)]
    try:
        subprocess.run(base + ["-c:a", "copy"] + tail, check=True, timeout=900)
    except subprocess.CalledProcessError:
        # 音轨编码不被 mp4 接受（pcm/wma…）时重编码为 aac
        subprocess.run(base + ["-c:a", "aac", "-b:a", "192k"] + tail,
                       check=True, timeout=900)
    probe = _probe(out)
    return {"ok": True, "template": template_id, "out": str(out), "cached": False,
            "elapsed_sec": round(time.time() - t0, 1),
            "source": str(src), **probe}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="交付规格导出（1080P / 竖版）")
    ap.add_argument("--template", default=None)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--hd", action="store_true", help="1920×1080（默认）")
    ap.add_argument("--sd", action="store_true", help="1280×720（不放大）")
    ap.add_argument("--vertical", action="store_true", help="1080×1920 中心裁切")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args(argv)
    targets = []
    if args.all:
        for root in ROOTS:
            if root.is_dir():
                targets += [p.name for p in sorted(root.iterdir())
                            if (p / "final.mp4").is_file()]
    elif args.template:
        targets = [args.template]
    else:
        ap.error("--template 或 --all 必填")
    results = []
    for tid in targets:
        try:
            res = deliver(tid, hd=not args.sd, vertical=args.vertical, force=args.force)
        except Exception as exc:  # noqa: BLE001
            res = {"ok": False, "template": tid, "error": str(exc)[:300]}
        results.append(res)
        print(f"[{tid}] {'ok' if res.get('ok') else 'fail'} "
              f"{res.get('width')}x{res.get('height')} {res.get('duration')}s", flush=True)
    ok = sum(1 for r in results if r.get("ok"))
    print(json.dumps({"ok": ok == len(results), "done": ok, "total": len(results),
                      "results": results}, ensure_ascii=False))
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
