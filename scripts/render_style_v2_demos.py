# -*- coding: utf-8 -*-
"""task-48：16 模板差异化风格 demo（style_v2：plate/cutout 变体 + palette 调色 + 转场 + 包装）。

输出 outputs/style_demos_v2/{template}.mp4（30-45s）+ frames/ + verify.json + overview_16.png。
优先使用 task-47 的 {scene}__{preset}.png；缺失自动回退 *_3d/base 并记录 fallback。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
import render_template_demos as rtd  # noqa: E402
from pipeline import config, shot_grammar, style_v2  # noqa: E402

FF = str(config.FFMPEG_BIN)
FP = str(config.FFPROBE_BIN)
FORMATS = ROOT / "contract" / "formats"
OUT = ROOT / "outputs" / "style_demos_v2"
TARGET_MIN = 30.0


def probe(path: Path) -> dict:
    p = subprocess.run([FP, "-v", "error", "-show_entries",
                        "format=duration,size:stream=codec_name,width,height,nb_frames",
                        "-of", "json", str(path)], capture_output=True, text=True)
    return json.loads(p.stdout or "{}")


def blackdetect(path: Path) -> list:
    p = subprocess.run([FF, "-hide_banner", "-i", str(path), "-vf",
                        "blackdetect=d=0.01:pix_th=0.10", "-an", "-f", "null", "NUL"],
                       capture_output=True, text=True, errors="replace")
    return [x for x in p.stderr.splitlines() if "blackdetect" in x]


def frame_stats(png: Path) -> dict:
    p = subprocess.run([FF, "-hide_banner", "-i", str(png), "-vf",
                        "signalstats,metadata=print:key=lavfi.signalstats.YAVG",
                        "-frames:v", "1", "-f", "null", "NUL"],
                       capture_output=True, text=True, errors="replace")
    m = re.search(r"YAVG=([0-9.]+)", p.stderr or "")
    p2 = subprocess.run([FF, "-hide_banner", "-i", str(png), "-vf",
                         "signalstats,metadata=print",
                         "-frames:v", "1", "-f", "null", "NUL"],
                        capture_output=True, text=True, errors="replace")
    def _g(k):
        mm = re.search(k + r"=([0-9.]+)", p2.stderr or "")
        return float(mm.group(1)) if mm else None
    return {"YAVG": float(m.group(1)) if m else None,
            "UAVG": _g("UAVG"), "VAVG": _g("VAVG")}


def build_episode(fmt: dict) -> dict:
    data = rtd.build_episode(fmt)
    shots = data["shots"]
    end = next((s for s in shots if s["type"] == "end_card"), None)
    total = sum(float(s["duration"]) for s in shots)
    if total < TARGET_MIN and end is not None:
        pad = round(max(3.0, TARGET_MIN + 1.0 - total), 2)
        no = max(int(s.get("no") or 0) for s in shots) + 1
        pad_shot = {
            "id": "demo_pad", "episode_id": data["episode"].get("id"),
            "segment_id": "closing", "no": no, "type": "b_roll",
            "duration": pad, "speaker": None, "speaker_name": "",
            "line": "", "visual": "空镜 · 收束", "lower_third": None,
            "punchline": "", "sfx": "", "mood": "", "status": "pending",
            "selected_version_id": None, "title": "", "subtitle": "",
            "shot_plan": shot_grammar.plan_for_type("b_roll", variant=no % 3),
        }
        data["shots"] = (shots[:-1] + [pad_shot] + [end]
                         if shots and shots[-1] is end else shots + [pad_shot, end])
        data["total_duration"] = round(sum(float(s["duration"]) for s in data["shots"]), 2)
    for s in data["shots"]:
        if s.get("type") in ("title_card", "end_card", "meme_card"):
            s["line"] = ""
            s["subtitle"] = ""
            s["punchline"] = ""
    return data


def extract_frames(video: Path, shots: list, frames_dir: Path) -> dict:
    frames_dir.mkdir(parents=True, exist_ok=True)
    t = 0.0
    paths = []
    for i, shot in enumerate(shots):
        dur = float(shot.get("duration") or 3.0)
        sec = t + min(1.2, dur * 0.5)
        p = frames_dir / f"shot_{i+1:02d}_{shot.get('type')}.png"
        subprocess.run([FF, "-y", "-hide_banner", "-loglevel", "error", "-ss",
                        f"{sec:.2f}", "-i", str(video), "-frames:v", "1", str(p)],
                       check=False)
        if p.is_file():
            paths.append(p)
        t += dur
    imgs = paths[:12]
    while len(imgs) < 12 and imgs:
        imgs.append(imgs[-1])
    if imgs:
        args = []
        for p in imgs:
            args += ["-i", str(p)]
        layout = "|".join(f"{(i % 4) * 480}_{(i // 4) * 270}" for i in range(12))
        fc = "".join(f"[{i}:v]scale=480:270[v{i}];" for i in range(12))
        fc += "".join(f"[v{i}]" for i in range(12)) + f"xstack=inputs=12:layout={layout}[o]"
        subprocess.run([FF, "-y", "-hide_banner", "-loglevel", "error", *args,
                        "-filter_complex", fc, "-map", "[o]",
                        str(frames_dir / "contact_sheet.png")], check=False)
    return {"frames": [str(p) for p in paths],
            "sheet": str(frames_dir / "contact_sheet.png")}


def main() -> int:
    only = sys.argv[1] if len(sys.argv) > 1 else None
    presets = style_v2.load_presets()
    templates = list((presets.get("templates") or {}).keys())
    OUT.mkdir(parents=True, exist_ok=True)
    results = []
    for fid in templates:
        key = fid[:-5] if fid.endswith("_5min") else fid
        if only and only not in (fid, key):
            continue
        fmt_path = FORMATS / f"{fid}.json"
        if not fmt_path.is_file():
            results.append({"id": key, "ok": False, "error": "format missing"})
            continue
        fmt = json.loads(fmt_path.read_text(encoding="utf-8"))
        data = build_episode(fmt)
        profile = style_v2.profile_for(fid)
        trans_mode, trans_style = style_v2.transition_args(profile)
        demo_dir = OUT / key
        demo_dir.mkdir(parents=True, exist_ok=True)
        ep_json = demo_dir / "episode.json"
        ep_json.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        out_mp4 = (OUT / f"{key}.mp4").resolve()
        cmd = [sys.executable, "-m", "pipeline.cli", "render",
               "--episode", str(ep_json), "--workdir", str(demo_dir / "work"),
               "--out", str(out_mp4), "--scenes", "--scene-template", key,
               "--style-profile", "auto",
               "--transitions", trans_mode]
        if trans_mode == "xfade":
            cmd += ["--transition-style", trans_style, "--transition-duration", "0.25"]
        print("[style-v2]", key, profile.get("preset"), profile.get("palette"), flush=True)
        proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                              encoding="utf-8", errors="replace")
        ok = proc.returncode == 0 and out_mp4.is_file()
        tail = (proc.stdout or proc.stderr or "")[-400:]
        print("[style-v2]", key, "ok" if ok else "FAIL", tail.replace("\n", " ")[:200], flush=True)
        if not ok:
            results.append({"id": key, "ok": False, "error": tail})
            continue
        info = probe(out_mp4)
        streams = info.get("streams") or []
        v = next((s for s in streams if s.get("codec_name") == "h264"), {})
        manifest = json.loads((demo_dir / "work" / "manifest.json").read_text(encoding="utf-8"))
        scenes = manifest.get("scenes") or {}
        metas = []
        for mp in sorted((demo_dir / "work" / "scenes" / "meta").glob("*.json")):
            m = json.loads(mp.read_text(encoding="utf-8"))
            if m.get("character"):
                metas.append(m)
        frames = extract_frames(out_mp4, data["shots"], demo_dir / "frames")
        sample = frames["frames"][1] if len(frames["frames"]) > 1 else frames["frames"][0]
        result = {
            "id": key, "format": fid, "ok": True,
            "preset": profile.get("preset"), "palette": profile.get("palette"),
            "character_style": profile.get("character_style"),
            "transition": profile.get("transition"),
            "transition_applied": {"mode": trans_mode, "style": trans_style},
            "duration": round(float(info["format"]["duration"]), 2),
            "size_bytes": int(info["format"]["size"]),
            "width": v.get("width"), "height": v.get("height"),
            "frames": v.get("nb_frames"),
            "shots": len(data["shots"]),
            "blackdetect": blackdetect(out_mp4),
            "style_profile": manifest.get("style_profile"),
            "plate": scenes.get("plate"), "plate_style": scenes.get("plate_style"),
            "plate_fallback": scenes.get("plate_fallback"),
            "expected_plate": scenes.get("expected_plate"),
            "cutout_fallback_count": sum(1 for m in metas if m.get("cutout_fallback")),
            "cutout_style_counts": {s: sum(1 for m in metas if m.get("cutout_style") == s)
                                    for s in {m.get("cutout_style") for m in metas}},
            "ass_events": manifest.get("ass_events"),
            "frame_stats": frame_stats(Path(sample)) if sample else {},
            "frames_info": frames,
            "video": str(out_mp4),
        }
        (demo_dir / "verify.json").write_text(json.dumps(result, ensure_ascii=False, indent=2),
                                             encoding="utf-8")
        results.append(result)
    # overview 4x4（每模板一张代表帧 + template/preset 标签）
    picks = []
    for r in results:
        if not r.get("ok"):
            continue
        fps = r["frames_info"]["frames"]
        sel = next((p for p in fps if "title_card" not in p and "end_card" not in p), fps[0])
        picks.append((sel, r["id"], r["preset"]))
    while len(picks) < 16 and picks:
        picks.append(picks[-1])
    if picks:
        text_dir = OUT / "text"
        text_dir.mkdir(parents=True, exist_ok=True)
        args = []
        fc_parts = []
        for i, (p, tid, preset) in enumerate(picks[:16]):
            label = f"{tid} · {preset}"
            label_file = text_dir / f"ov_{i}.txt"
            label_file.write_text(label, encoding="utf-8")
            args += ["-i", p]
        font = None
        for cand in (ROOT / "outputs" / "episodes" / "ep_e1015494fa" / "fonts" / "Deng.ttf",
                     ROOT / "outputs" / "history_full" / "work_h3demo" / "fonts" / "Deng.ttf"):
            if cand.is_file():
                font = os.path.relpath(cand, ROOT).replace("\\", "/")
                break
        cells = []
        for i in range(16):
            parts = [f"scale=480:270"]
            if font:
                label_file = os.path.relpath(text_dir / f"ov_{i}.txt", ROOT).replace("\\", "/")
                parts.append(f"drawtext=textfile='{label_file}':fontfile='{font}':"
                             f"fontsize=20:fontcolor=white:box=1:boxcolor=black@0.55:"
                             f"boxborderw=6:x=10:y=10")
            fc_parts.append(f"[{i}:v]" + ",".join(parts) + f"[v{i}]")
            cells.append(f"[v{i}]")
        layout = "|".join(f"{(i % 4) * 480}_{(i // 4) * 270}" for i in range(16))
        fc = ";".join(fc_parts) + ";" + "".join(cells) + \
             f"xstack=inputs=16:layout={layout}[o]"
        subprocess.run([FF, "-y", "-hide_banner", "-loglevel", "error", *args,
                        "-filter_complex", fc, "-map", "[o]",
                        str(OUT / "overview_16.png")], check=False)
    (OUT / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2),
                                      encoding="utf-8")
    print(json.dumps({"ok": all(r.get("ok") for r in results),
                      "count": len(results),
                      "results": [{k: r.get(k) for k in ("id", "preset", "palette",
                                                         "transition", "duration",
                                                         "blackdetect", "plate_style",
                                                         "plate_fallback",
                                                         "cutout_fallback_count",
                                                         "frame_stats")}
                                  for r in results]}, ensure_ascii=False, indent=2))
    return 0 if all(r.get("ok") for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
