# -*- coding: utf-8 -*-
"""task-44：11 个 3d_anime 模板的 3D 风格场景 demo（静态合成，无 H3）。

按 contract/style_map.json 选 3d_anime 模板；用 3D plate + cutout_3d + --style 3d_anime，
输出 outputs/scene_demos_3d/{template}.mp4（30-45s）+ frames/ + verify.json + overview_3d.png。
5 个 2d_anime 模板保持现有 outputs/scene_demos/ 2D 版不动。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
import render_template_demos as rtd  # noqa: E402
from pipeline import config, shot_grammar, utils  # noqa: E402

FF = str(config.FFMPEG_BIN)
FP = str(config.FFPROBE_BIN)
FORMATS = ROOT / "contract" / "formats"
STYLE_MAP = ROOT / "contract" / "style_map.json"
OUT = ROOT / "outputs" / "scene_demos_3d"
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
        shots = shots[:-1] + [pad_shot] + [end] if shots[-1] is end else shots + [pad_shot, end]
        data["shots"] = shots
        data["total_duration"] = round(sum(float(s["duration"]) for s in shots), 2)
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
    # 4x3 contact sheet
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
    sm = json.loads(STYLE_MAP.read_text(encoding="utf-8"))
    templates = [k for k, v in (sm.get("templates") or {}).items() if v == "3d_anime"]
    OUT.mkdir(parents=True, exist_ok=True)
    results = []
    for fid in templates:
        fmt_path = FORMATS / f"{fid}.json"
        if not fmt_path.is_file():
            results.append({"id": fid, "ok": False, "error": "format missing"})
            continue
        key = fid[:-5] if fid.endswith("_5min") else fid
        fmt = json.loads(fmt_path.read_text(encoding="utf-8"))
        data = build_episode(fmt)
        demo_dir = OUT / key
        demo_dir.mkdir(parents=True, exist_ok=True)
        ep_json = demo_dir / "episode.json"
        ep_json.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        out_mp4 = (OUT / f"{key}.mp4").resolve()
        cmd = [sys.executable, "-m", "pipeline.cli", "render",
               "--episode", str(ep_json), "--workdir", str(demo_dir / "work"),
               "--out", str(out_mp4), "--scenes", "--scene-template", key,
               "--style", "3d_anime",
               "--transitions", "xfade", "--transition-style", "fade",
               "--transition-duration", "0.25"]
        print("[3d-demo] render", key, flush=True)
        proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                              encoding="utf-8", errors="replace")
        ok = proc.returncode == 0 and out_mp4.is_file()
        tail = (proc.stdout or proc.stderr or "")[-300:]
        print("[3d-demo]", key, "ok" if ok else "FAIL", tail.replace("\n", " ")[:180], flush=True)
        if not ok:
            results.append({"id": key, "ok": False, "error": tail})
            continue
        info = probe(out_mp4)
        height = [s.get("height") for s in info.get("streams", []) if s.get("codec_name") == "h264"]
        manifest = json.loads((demo_dir / "work" / "manifest.json").read_text(encoding="utf-8"))
        scenes = manifest.get("scenes") or {}
        metas = []
        for mp in sorted((demo_dir / "work" / "scenes" / "meta").glob("*.json")):
            m = json.loads(mp.read_text(encoding="utf-8"))
            if m.get("character"):
                metas.append(m)
        frames = extract_frames(out_mp4, data["shots"], demo_dir / "frames")
        result = {
            "id": key, "format": fid, "ok": True,
            "duration": round(float(info["format"]["duration"]), 2),
            "size_bytes": int(info["format"]["size"]),
            "width": ([s.get("width") for s in info.get("streams", []) if s.get("codec_name") == "h264"] or [None])[0],
            "height": height[0] if height else None,
            "shots": len(data["shots"]),
            "blackdetect": blackdetect(out_mp4),
            "style": (manifest.get("style") or {}).get("name"),
            "plate": scenes.get("plate"), "plate_style": scenes.get("plate_style"),
            "plate_fallback": scenes.get("plate_fallback"),
            "cutout_fallback_count": sum(1 for m in metas if m.get("cutout_fallback")),
            "cutout_3d_count": sum(1 for m in metas if m.get("cutout_style") == "3d_anime"),
            "ass_events": manifest.get("ass_events"),
            "frames": frames,
            "video": str(out_mp4),
        }
        (demo_dir / "verify.json").write_text(json.dumps(result, ensure_ascii=False, indent=2),
                                             encoding="utf-8")
        results.append(result)
    # 11 模板抽帧总览（每模板第一张非卡片帧）
    picks = []
    for r in results:
        if not r.get("ok"):
            continue
        for p in r["frames"]["frames"]:
            if "title_card" not in p and "end_card" not in p:
                picks.append(p)
                break
    while len(picks) < 12 and picks:
        picks.append(picks[-1])
    if picks:
        args = []
        for p in picks[:12]:
            args += ["-i", p]
        layout = "|".join(f"{(i % 4) * 480}_{(i // 4) * 270}" for i in range(12))
        fc = "".join(f"[{i}:v]scale=480:270[v{i}];" for i in range(12))
        fc += "".join(f"[v{i}]" for i in range(12)) + f"xstack=inputs=12:layout={layout}[o]"
        subprocess.run([FF, "-y", "-hide_banner", "-loglevel", "error", *args,
                        "-filter_complex", fc, "-map", "[o]",
                        str(OUT / "overview_3d.png")], check=False)
    (OUT / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2),
                                      encoding="utf-8")
    print(json.dumps({"ok": all(r.get("ok") for r in results), "count": len(results),
                      "results": [{k: r.get(k) for k in ("id", "duration", "blackdetect",
                                                         "style", "plate_style",
                                                         "plate_fallback",
                                                         "cutout_fallback_count",
                                                         "cutout_3d_count")}
                                  for r in results]}, ensure_ascii=False, indent=2))
    return 0 if all(r.get("ok") for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
