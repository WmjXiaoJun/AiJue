# -*- coding: utf-8 -*-
"""V4 时间线（OpenFilm 式声明式剪辑）。

把一集成片表达为 tracks / clips / 媒体片段（in/out/at/gain）的 JSON，落
``outputs/templates_v3/{id}/timeline.json``，并生成只读预览 ``timeline.html``。

设计参考 OpenFilm（https://github.com/openfilm/openfilm）：film.html 的 <section> 轨道、
媒体片段 ``#t=in,out``、``at`` 起点 + 多轨音频。V4 用 JSON 表达同一模型，由
``pipeline/edit_v4.py`` 的 ffmpeg 渲染器执行（非破坏、可人工编辑后重渲）。

轨道：
- V1 video：镜头片段（in/out/at/transition）
- A1 voice：每镜对白（src=audio/{sid}.wav，天然含 LEAD 偏移），绝对 at
- A2 music：BGM（duck 参数由 edit_v4 用 sidechaincompress 实现）
- A3 sfx：转场 whoosh / ding
- S1 subtitle：台词字幕块（绝对 at/out）
"""
from __future__ import annotations

import json
import html
from pathlib import Path

from . import utils

TIMELINE_VERSION = 4
FPS = 25


def _shot_src(paths: dict, sid: str) -> str:
    return str((Path(paths["shots_dir"]) / f"{sid}.mp4").resolve())


def build_timeline(plan: dict, template: dict, paths: dict, *, profile: dict | None = None,
                   mode: str = "comfy", transition: str = "xfade",
                   transition_style: str = "fade",
                   transition_duration: float = 0.3) -> dict:
    """从 render_v3 的 plan + 产物路径构建 timeline 字典。"""
    shots = plan.get("shots") or []
    workdir = Path(paths["workdir"])
    total = round(sum(float(s.get("duration") or 0.0) for s in shots), 3)
    template_id = str(template.get("id") or (plan.get("episode") or {}).get("id") or "")
    video_clips, voice_clips, subtitle_clips = [], [], []
    cursor = 0.0
    cut_points: list[float] = []
    for shot in shots:
        sid = str(shot.get("id"))
        dur = round(float(shot.get("duration") or 0.0), 3)
        start = round(cursor, 3)
        is_card = bool(shot.get("static_card")) or str(shot.get("type") or "") in (
            "title_card", "end_card", "meme_card")
        video_clips.append({
            "id": sid,
            "src": _shot_src(paths, sid),
            "in": 0.0, "out": dur, "at": start,
            "kind": "card" if is_card else "h3",
            "role": shot.get("role"),
            "segment_id": shot.get("segment_id"),
            "transition": {"type": "cut" if is_card else transition,
                           "style": transition_style,
                           "duration": 0.0 if is_card else transition_duration},
        })
        line = str(shot.get("line") or "").strip()
        voice = shot.get("voice") if isinstance(shot.get("voice"), dict) else {}
        if line and voice.get("provider") not in (None, "", "missing"):
            voice_clips.append({
                "id": f"v_{sid}",
                "src": str((workdir / "audio" / f"{sid}.wav").resolve()),
                "at": start, "out": dur,
                "speech_duration": shot.get("speech_duration"),
                "lead_sec": float((plan.get("meta") or {}).get("lead_sec") or 0.10),
                "provider": voice.get("provider"), "voice": voice.get("voice"),
                "seed": voice.get("seed"), "atempo": 1.0,
                "character": voice.get("character"),
                "text": line,
            })
            subtitle_clips.append({
                "id": f"s_{sid}", "text": line,
                "speaker": shot.get("speaker_name") or shot.get("role"),
                "at": round(start + float((plan.get("meta") or {}).get("lead_sec") or 0.10), 3),
                "out": round(min(start + dur - 0.05,
                                 start + float(shot.get("speech_duration") or dur) + 0.35), 3),
            })
        if not is_card and cursor > 0:
            cut_points.append(start)
        cursor += dur
    bgm = workdir / "bgm" / "soft_pad.wav"
    timeline = {
        "version": TIMELINE_VERSION,
        "template": template_id,
        "label": str(template.get("label") or ""),
        "mode": mode,
        "fps": FPS,
        "resolution": [int((plan.get("episode") or {}).get("width") or 1280),
                       int((plan.get("episode") or {}).get("height") or 720)],
        "duration": total,
        "style": {
            "preset": (profile or {}).get("preset"),
            "palette": (profile or {}).get("palette"),
            "grade_filter": (profile or {}).get("grade_filter") or "",
            "transition": {"type": transition, "style": transition_style,
                           "duration": transition_duration},
        },
        "tracks": [
            {"id": "V1", "kind": "video", "clips": video_clips},
            {"id": "A1", "kind": "voice", "clips": voice_clips},
            {"id": "A2", "kind": "music", "clips": [{
                "id": "bgm", "src": str(bgm.resolve()), "at": 0.0, "out": total,
                "gain": 0.22, "duck": {"enabled": True, "threshold": 0.035,
                                       "ratio": 6, "attack": 15, "release": 350},
                "fade_in": 2.0, "fade_out": 4.0,
            }] if bgm.is_file() else []},
            {"id": "A3", "kind": "sfx", "clips": [
                {"id": f"whoosh_{i}", "src": str((workdir / "sfx" / "whoosh.wav").resolve()),
                 "at": round(t, 3), "gain": 0.85}
                for i, t in enumerate(cut_points)
                if (workdir / "sfx" / "whoosh.wav").is_file()]},
            {"id": "S1", "kind": "subtitle", "clips": subtitle_clips},
        ],
        "meta": {
            "speech_driven": bool((plan.get("meta") or {}).get("speech_driven")),
            "voice_provider": (plan.get("meta") or {}).get("voice_provider"),
            "lead_sec": (plan.get("meta") or {}).get("lead_sec"),
            "tail_sec": (plan.get("meta") or {}).get("tail_sec"),
            "h3_mode_policy": "audio_ref",
            "cards": sum(1 for c in video_clips if c["kind"] == "card"),
            "h3_shots": sum(1 for c in video_clips if c["kind"] == "h3"),
            "voice_clips": len(voice_clips),
        },
    }
    return timeline


def write_timeline(timeline: dict, out_dir: Path | str) -> dict:
    out_dir = Path(out_dir)
    utils.ensure_dir(out_dir)
    json_path = out_dir / "timeline.json"
    utils.write_json(json_path, timeline)
    html_path = out_dir / "timeline.html"
    html_path.write_text(render_html(timeline), encoding="utf-8")
    return {"json": str(json_path), "html": str(html_path)}


def load_timeline(path: Path | str) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "tracks" not in data:
        raise ValueError(f"timeline 非法: {path}")
    return data


def timeline_shots(timeline: dict) -> list[dict]:
    """从 V1 轨道还原 render.py 兼容的 shots 列表（供复用 concat/xfade/QC）。"""
    video = next((t for t in timeline.get("tracks") or [] if t.get("id") == "V1"), {})
    clips = sorted(video.get("clips") or [], key=lambda c: float(c.get("at") or 0.0))
    shots: list[dict] = []
    for clip in clips:
        shots.append({
            "id": clip.get("id"),
            "duration": round(float(clip.get("out") or 0.0) - float(clip.get("in") or 0.0), 3),
            "type": "title_card" if clip.get("kind") == "card" else "dialogue",
            "segment_id": clip.get("segment_id"),
            "voice": {},
        })
    return shots


def render_html(timeline: dict) -> str:
    rows = []
    for track in timeline.get("tracks") or []:
        rows.append(f"<h2>{html.escape(str(track.get('id')))} · "
                    f"{html.escape(str(track.get('kind')))}</h2><table>"
                    "<tr><th>id</th><th>at</th><th>out</th><th>src / text</th></tr>")
        for clip in track.get("clips") or []:
            src = clip.get("text") or clip.get("src") or ""
            rows.append(
                "<tr><td>{}</td><td>{}</td><td>{}</td><td><code>{}</code></td></tr>".format(
                    html.escape(str(clip.get("id"))),
                    html.escape(str(clip.get("at"))),
                    html.escape(str(clip.get("out"))),
                    html.escape(str(src))[:180]))
        rows.append("</table>")
    return ("<!doctype html><html lang='zh'><meta charset='utf-8'>"
            f"<title>Timeline · {html.escape(str(timeline.get('template')))}</title>"
            "<style>body{font:14px/1.5 system-ui;background:#12141a;color:#e8e8ee;"
            "padding:24px}table{border-collapse:collapse;margin-bottom:24px;width:100%}"
            "td,th{border:1px solid #2c3040;padding:4px 8px;text-align:left}"
            "h2{color:#f5c518;margin:20px 0 6px}code{color:#9fd4ff}</style>"
            f"<h1>Timeline v{timeline.get('version')} · "
            f"{html.escape(str(timeline.get('template')))} · {timeline.get('duration')}s</h1>"
            + "".join(rows) + "</html>")
