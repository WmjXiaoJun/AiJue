# -*- coding: utf-8 -*-
"""V54：把成片扩到「满 N 分钟」的长版（短片栈用；V5 栈请直接用 render_v5 --duration）。

做法：片头卡 + 成片按 3 种画面变体循环 + 片尾卡 → 精确裁到 target 秒。
变体只做无损观感变化（轻微推近/降饱和），不重渲镜头，所以单条 1-2 分钟。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from . import config, utils

REPO = config.REPO_ROOT
OUT_ROOT = config.OUTPUTS_ROOT
FONT = "C:/Windows/Fonts/msyhbd.ttc"


def _template(tid: str) -> dict:
    for directory in (REPO / "contract/v5/custom/templates", REPO / "contract/v5/templates"):
        path = directory / f"{tid}.json"
        if path.is_file():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
    return {}


def _assemble_shorts(tid: str) -> Path | None:
    """HQ 短片没有成片但有镜头时，先用 templates3d.assemble 重合成（不跑 GPU）。"""
    out_dir = OUT_ROOT / "templates_3d" / tid
    shots = list((out_dir / "shots").glob("*.mp4")) if (out_dir / "shots").is_dir() else []
    if not shots:
        return None
    from . import templates3d as t3  # noqa: PLC0415

    builtin = REPO / "contract/v5/templates" / f"{tid}.json"
    custom = REPO / "contract/v5/custom/templates" / f"{tid}.json"

    def _load(_tid: str) -> dict:
        for path in (custom, builtin):
            if path.is_file():
                return json.loads(path.read_text(encoding="utf-8"))
        raise RuntimeError(f"缺少模板合同: {_tid}")

    orig = t3.load_template
    t3.load_template = _load
    try:
        t3.assemble(tid)
    finally:
        t3.load_template = orig
    candidate = out_dir / "final.mp4"
    return candidate if candidate.is_file() else None


def _final_of(tid: str) -> Path | None:
    for directory in (OUT_ROOT / "templates_3d", OUT_ROOT / "templates_v5"):
        candidate = directory / tid / "final.mp4"
        if candidate.is_file() and candidate.stat().st_size > 0:
            return candidate
    assembled = _assemble_shorts(tid)
    if assembled is not None:
        return assembled
    candidate = OUT_ROOT / "templates_2d" / tid / "final.mp4"
    return candidate if candidate.is_file() and candidate.stat().st_size > 0 else None


def _poster_of(tid: str) -> Path | None:
    for directory in (OUT_ROOT / "templates_3d", OUT_ROOT / "templates_v5"):
        for name in ("poster.jpg", "poster.png", "cover.jpg"):
            candidate = directory / tid / name
            if candidate.is_file():
                return candidate
    return None


def _card(text: str, poster: Path | None, seconds: float, out: Path,
          width: int = 1280, height: int = 720) -> Path:
    """片头/片尾卡：静态图 + 大字（无图则纯色）。"""
    escaped = str(text).replace(":", r"\:").replace("'", "")
    draw = ""
    if escaped.strip():
        draw = (f"drawtext=fontfile='{FONT}':text='{escaped}':fontcolor=white:fontsize=64:"
                f"x=(w-text_w)/2:y=(h-text_h)/2:box=1:boxcolor=black@0.35:boxborderw=28")
    if poster and poster.is_file():
        args = ["-loop", "1", "-i", str(poster)]
        vf = (f"scale={width}:{height}:force_original_aspect_ratio=increase,"
              f"crop={width}:{height},eq=brightness=-0.22," +
              (f"{draw}," if draw else "") + "format=yuv420p")
    else:
        args = ["-f", "lavfi", "-i", f"color=c=0x101418:s={width}x{height}:r=25"]
        vf = (f"{draw}," if draw else "") + "format=yuv420p"
    utils.ffmpeg(args + ["-t", f"{seconds:.3f}", "-vf", vf,
                          "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                          "-pix_fmt", "yuv420p", "-an", "-y", str(out)])
    return out


def _variant(src: Path, out: Path, mode: int) -> Path:
    """同一成片的三种观感变体（循环时不完全重复）。"""
    base = ("scale=1280:720:force_original_aspect_ratio=decrease,"
            "pad=1280:720:(ow-iw)/2:(oh-ih)/2,setsar=1")
    if mode == 0:
        vf = base
    elif mode == 1:
        vf = base + ",eq=saturation=1.08"
    else:
        vf = base + ",eq=saturation=0.94:brightness=-0.02"
    utils.ffmpeg(["-i", str(src), "-vf", vf, "-r", "25",
                  "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                  "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
                  "-y", str(out)])
    return out


def build(tid: str, target: float = 300.0, force: bool = False) -> dict:
    """生成 <tid>/final_5min.mp4（精确 target 秒）。返回证据 dict。"""
    started = time.time()
    src = _final_of(tid)
    if src is None:
        return {"ok": False, "template": tid, "error": "没有成片（先跑渲染）"}
    target = max(30.0, float(target))
    out_dir = src.parent
    final = out_dir / f"final_{int(target)}s.mp4"
    if final.is_file() and not force and final.stat().st_mtime >= src.stat().st_mtime:
        probe = utils.probe_video(final) if hasattr(utils, "probe_video") else {}
        return {"ok": True, "cached": True, "template": tid, "out": str(final),
                "duration": (probe or {}).get("duration") or utils.probe_duration(final)}
    work = utils.ensure_dir(OUT_ROOT / "longform" / tid)
    tpl = _template(tid)
    title = str(tpl.get("label") or tpl.get("packaging", {}).get("title") or tid)
    poster = _poster_of(tid)
    intro = _card(f"{title} · 5 分钟完整版", poster, 4.0, work / "card_in.mp4")
    outro = _card("本集完 · 下集见", poster, 3.0, work / "card_out.mp4")
    src_dur = float(utils.probe_duration(src) or 0)
    if src_dur <= 0.1:
        return {"ok": False, "template": tid, "error": f"成片时长异常: {src_dur}"}
    variants = [_variant(src, work / f"v{i}.mp4", i) for i in range(3)]
    body = target - 7.0
    loops = max(1, int(body // src_dur) + 1)
    lines = [f"file '{intro.as_posix()}'"]
    for i in range(loops):
        lines.append(f"file '{variants[i % len(variants)].as_posix()}'")
    lines.append(f"file '{outro.as_posix()}'")
    concat_file = work / "concat.txt"
    concat_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    utils.ffmpeg(["-f", "concat", "-safe", "0", "-i", str(concat_file),
                  "-t", f"{target:.3f}",
                  "-vf", "fps=25,format=yuv420p",
                  "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                  "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
                  "-movflags", "+faststart", "-y", str(final)])
    actual = float(utils.probe_duration(final) or 0)
    evidence = {"ok": actual >= target - 0.5, "template": tid, "target": target,
                "duration": round(actual, 3), "loops": loops, "source": str(src),
                "out": str(final), "elapsed_sec": round(time.time() - started, 1),
                "variants": len(variants)}
    utils.write_json(out_dir / f"longform_{int(target)}s.json", evidence)
    return evidence


def main(argv=None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="把成片扩到满 N 分钟")
    ap.add_argument("--template", required=True)
    ap.add_argument("--duration", type=float, default=300.0)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args(argv)
    print(json.dumps(build(args.template, args.duration, args.force),
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())