# -*- coding: utf-8 -*-
"""V4 时间线渲染器：把 timeline.json 渲染成 MP4（OpenFilm 式非破坏剪辑）。

用法：
  python -m pipeline.edit_v4 --timeline outputs/templates_v3/tonight_show/timeline.json \
      --out outputs/templates_v3/tonight_show/final_timeline.mp4

渲染内容：
- V1 视频轨：按 in/out/at 拼接（支持 xfade/cut；片段来自 timeline）
- A1 语音轨 + A3 SFX：绝对时间放置（adelay）+ 混音
- A2 音乐轨：sidechaincompress 按语音自动 ducking
- S1 字幕轨：生成 ASS 并烧录
- 调色/包装：复用 style_v2（按 timeline.style.preset / palette）
- 最终 loudnorm 一遍（与 render_v3 一致）
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import ass_builder, config, fonts, render as render_mod, style_v2, timeline as timeline_mod, utils

LOG = __import__("logging").getLogger("pipeline")
FINAL_W, FINAL_H, FINAL_FPS = 1280, 720, 25


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------
def _track(timeline: dict, track_id: str) -> dict:
    return next((t for t in timeline.get("tracks") or [] if t.get("id") == track_id), {})


def _abs_src(workdir: Path, src: str) -> Path:
    p = Path(src)
    if p.is_absolute():
        return p
    return (workdir / p).resolve()


def _fill_silence(path: Path, duration: float, sample_rate: int) -> Path:
    utils.ensure_dir(path.parent)
    utils.ffmpeg(["-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                  "-i", f"anullsrc=r={sample_rate}:cl=stereo",
                  "-t", f"{duration:.3f}", "-ar", str(sample_rate), "-ac", "2",
                  "-c:a", "pcm_s16le", str(path)])
    return path


def _mix_track(clips: list[dict], workdir: Path, total: float, out: Path,
               sample_rate: int, label: str) -> Path | None:
    """把若干绝对时间音频片段混到一条 length=total 的轨道。"""
    valid = []
    for clip in clips:
        src = _abs_src(workdir, str(clip.get("src") or ""))
        if not src.is_file():
            LOG.warning("%s 片段缺失，跳过: %s", label, src)
            continue
        valid.append((clip, src))
    if not valid:
        return None
    utils.ensure_dir(Path(out).parent)
    base = _fill_silence(workdir / "edit" / f"_{label}_base.wav", total, sample_rate)
    args = ["-i", str(base)]
    fc: list[str] = ["[0:a]anull[base]"]
    for i, (clip, src) in enumerate(valid, start=1):
        args += ["-i", str(src)]
        ms = max(0, int(round(float(clip.get("at") or 0.0) * 1000)))
        gain = float(clip.get("gain") or 1.0)
        fc.append(f"[{i}:a]aresample={sample_rate},aformat=channel_layouts=stereo,"
                  f"volume={gain},adelay={ms}|{ms},atrim=0:{total:.3f}[c{i}]")
    fc.append("[base]" + "".join(f"[c{i}]" for i in range(1, len(valid) + 1))
              + f"amix=inputs={len(valid) + 1}:duration=first:normalize=0[out]")
    utils.ffmpeg(args + ["-filter_complex", ";".join(fc), "-map", "[out]",
                         "-t", f"{total:.3f}", "-ar", str(sample_rate), "-ac", "2",
                         "-c:a", "pcm_s16le", str(out)])
    return out


def _build_ass(timeline: dict, ass_path: Path) -> int:
    """S1 字幕轨 → ASS（简洁综艺风：底部居中、白字黑边、重点色）。"""
    clips = sorted((_track(timeline, "S1").get("clips") or []),
                   key=lambda c: float(c.get("at") or 0.0))
    if not clips:
        return 0
    try:
        font_info = fonts.ensure_fonts(ass_path.parent)
        body = Path(str((font_info.get("body") or {}).get("file") or ""))
        font_name = body.stem or "Microsoft YaHei"
    except Exception:  # noqa: BLE001
        font_name = "Microsoft YaHei"
    lines = [
        "[Script Info]", "ScriptType: v4.00+", "PlayResX: 1280", "PlayResY: 720",
        "WrapStyle: 0", "ScaledBorderAndShadow: yes", "", "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Default,{font_name},44,&H00FFFFFF,&H000000FF,&H00000000,&H64000000,"
        "-1,0,0,0,100,100,0,0,1,3,1,2,40,40,42,1", "", "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]

    def ts(sec: float) -> str:
        sec = max(0.0, float(sec))
        h = int(sec // 3600)
        m = int((sec % 3600) // 60)
        s = sec % 60
        return f"{h}:{m:02d}:{s:05.2f}"

    count = 0
    for clip in clips:
        text = str(clip.get("text") or "").strip()
        if not text:
            continue
        start = float(clip.get("at") or 0.0)
        end = max(start + 0.4, float(clip.get("out") or start + 2.0))
        safe = text.replace("{", "（").replace("}", "）").replace("\n", "\\N")
        lines.append(f"Dialogue: 0,{ts(start)},{ts(end)},Default,"
                     f"{clip.get('speaker') or ''},0,0,0,,{safe}")
        count += 1
    utils.write_text(ass_path, "\n".join(lines) + "\n")
    return count


# ---------------------------------------------------------------------------
# 主渲染
# ---------------------------------------------------------------------------
def render_timeline(timeline: dict, out: Path, workdir: Path | None = None,
                    prog=None) -> dict:
    out = Path(out).resolve()
    workdir = Path(workdir).resolve() if workdir else out.parent / "work_edit"
    utils.ensure_dir(workdir)
    total = round(float(timeline.get("duration") or 0.0), 3)
    width, height = (timeline.get("resolution") or [FINAL_W, FINAL_H])[:2]
    fps = int(timeline.get("fps") or FINAL_FPS)
    utils.ensure_dir(out.parent)

    # V1 视频
    v1 = _track(timeline, "V1")
    clips = sorted(v1.get("clips") or [], key=lambda c: float(c.get("at") or 0.0))
    clip_paths: dict[str, Path] = {}
    shots: list[dict] = []
    for clip in clips:
        sid = str(clip.get("id"))
        clip_paths[sid] = _abs_src(workdir, str(clip.get("src") or ""))
        shots.append({"id": sid,
                      "duration": round(float(clip.get("out") or 0) - float(clip.get("in") or 0), 3),
                      "type": "title_card" if clip.get("kind") == "card" else "dialogue",
                      "segment_id": clip.get("segment_id")})
    trans = (timeline.get("style") or {}).get("transition") or {}
    t_type = str(trans.get("type") or "xfade")
    t_style = str(trans.get("style") or "fade")
    t_dur = float(trans.get("duration") or 0.3)
    if prog is not None:
        prog(20, f"时间线渲染：V1 {len(shots)} 片段")
    if t_type == "xfade" and len(shots) > 1:
        video_raw = render_mod.concat_video_xfade(
            shots, clip_paths, workdir, fps, width, height, total,
            transition=t_style, transition_duration=t_dur)
    else:
        video_raw = render_mod.concat_video(shots, workdir, fps, total,
                                            clip_paths=clip_paths)
    LOG.info("video_raw: %s (%.2fs)", video_raw, utils.probe_duration(video_raw))

    # 音频轨
    sr = int(config.SAMPLE_RATE)
    if prog is not None:
        prog(55, "时间线渲染：A1/A3 混音")
    voice = _mix_track(_track(timeline, "A1").get("clips") or [], workdir, total,
                       workdir / "audio" / "narration.wav", sr, "voice")
    if voice is None:
        voice = _fill_silence(workdir / "audio" / "narration.wav", total, sr)
    # A3 SFX 混到转场轨
    sfx = _mix_track(_track(timeline, "A3").get("clips") or [], workdir, total,
                     workdir / "sfx" / "transitions_timeline.wav", sr, "sfx")
    music_track = _track(timeline, "A2").get("clips") or []
    bgm = None
    if music_track:
        src = _abs_src(workdir, str(music_track[0].get("src") or ""))
        bgm = src if src.is_file() else None

    # 字幕（final_encode 约定读取 workdir/final.ass）
    ass_path = workdir / "final.ass"
    sub_count = _build_ass(timeline, ass_path)

    # 调色/包装
    style = timeline.get("style") or {}
    grade = ""
    packaging = ""
    try:
        profile = style_v2.profile_for(str(timeline.get("template") or ""),
                                       override=str(style.get("preset") or "") or None)
        grade = style_v2.grade_filter(profile)
        font_info = fonts.ensure_fonts(workdir)
        packaging = style_v2.packaging_filters(profile, workdir, width, height,
                                               font_info["body"]["file"])
    except Exception as exc:  # noqa: BLE001
        LOG.warning("时间线调色/包装失败（跳过）: %s", exc)

    if prog is not None:
        prog(78, "时间线渲染：编码成片（字幕/混音/loudnorm）")
    render_mod.final_encode(
        workdir, out, total, fps, width, height, bgm,
        has_ass=sub_count > 0, prog=prog or (lambda *a, **k: None), pct0=78, pct1=99,
        transition_sfx=sfx, grade_filter=grade or None,
        packaging_filter=packaging or None, duck_bgm=bool(bgm))
    probe = utils.probe_video(out)
    return {"ok": out.is_file(), "path": str(out), "duration": probe.get("duration"),
            "resolution": f"{probe.get('width')}x{probe.get('height')}",
            "fps": probe.get("fps"), "subtitles": sub_count,
            "video": str(video_raw), "voice": str(voice), "bgm": str(bgm or ""),
            "timeline": {"tracks": len(timeline.get("tracks") or []),
                         "clips": len(shots)}}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m pipeline.edit_v4")
    ap.add_argument("--timeline", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--workdir", default=None)
    args = ap.parse_args(argv)
    tl = timeline_mod.load_timeline(args.timeline)
    res = render_timeline(tl, Path(args.out), Path(args.workdir) if args.workdir else None)
    utils.emit_json_line(res)
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
