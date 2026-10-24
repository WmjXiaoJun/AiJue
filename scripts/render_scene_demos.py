# -*- coding: utf-8 -*-
"""生成场景升级 demo（2D 绘景+角色分层+视差；tonight_show 混 3D 片段）。

用法：
  python scripts/render_scene_demos.py [history_talk|tonight_show|roundtable|...]
输出：
  outputs/scene_demos/{history_talk,tonight_show}.mp4
  outputs/template_demos_scene/{roundtable,taskmaster,qipashuo,mystery}.mp4
  .../frames/ 抽帧与 contact sheet，verify.json
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
import render_template_demos as rtd  # noqa: E402
from pipeline import config, utils  # noqa: E402

FF = str(config.FFMPEG_BIN)
FP = str(config.FFPROBE_BIN)
FORMATS = ROOT / "contract" / "formats"
SCENE_DEMOS = ROOT / "outputs" / "scene_demos"
TEMPLATE_DEMOS = ROOT / "outputs" / "template_demos_scene"


def _template_key(fmt_id: str) -> str:
    fid = str(fmt_id or "")
    return fid[:-5] if fid.endswith("_5min") else fid


TARGETS = [(_p.stem, _template_key(_p.stem), SCENE_DEMOS)
           for _p in sorted(FORMATS.glob("*.json"))]


def probe(path: Path) -> dict:
    out = subprocess.run([FP, "-v", "error", "-show_entries",
                          "format=duration,size:stream=codec_name,width,height,nb_frames",
                          "-of", "json", str(path)], capture_output=True, text=True)
    return json.loads(out.stdout or "{}")


def blackdetect(path: Path) -> list:
    p = subprocess.run([FF, "-hide_banner", "-i", str(path), "-vf",
                        "blackdetect=d=0.01:pix_th=0.10", "-an", "-f", "null", "NUL"],
                       capture_output=True, text=True, errors="replace")
    return [x for x in p.stderr.splitlines() if "blackdetect" in x]


def psnr(f1: Path, f2: Path) -> float | None:
    p = subprocess.run([FF, "-hide_banner", "-i", str(f1), "-i", str(f2),
                        "-lavfi", "psnr", "-f", "null", "NUL"],
                       capture_output=True, text=True, errors="replace")
    m = re.search(r"average:([0-9.]+)", p.stderr or "")
    return float(m.group(1)) if m else None


def frames_for_video(video: Path, shots: list, out_dir: Path, sheet: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    t = 0.0
    paths = []
    for i, shot in enumerate(shots):
        dur = float(shot.get("duration") or 3.0)
        sec = t + min(1.2, dur * 0.5)
        p = out_dir / f"shot_{i+1:02d}_{shot.get('type')}.png"
        subprocess.run([FF, "-y", "-hide_banner", "-loglevel", "error", "-ss",
                        f"{sec:.2f}", "-i", str(video), "-frames:v", "1", str(p)],
                       check=False)
        if p.is_file():
            paths.append(p)
        t += dur
    # 相机运动：取第一个非卡片镜头 0.3s 与 2/3 处帧做 psnr（推镜时差异明显）
    motion = None
    probe_shots = [s for s in shots if s.get("type") not in ("title_card", "end_card", "meme_card")]
    if probe_shots:
        start = 0.0
        for s in shots:
            if s is probe_shots[0]:
                break
            start += float(s.get("duration") or 0.0)
        f1 = out_dir / "_m1.png"
        f2 = out_dir / "_m2.png"
        d0 = float(probe_shots[0].get("duration") or 3.0)
        subprocess.run([FF, "-y", "-hide_banner", "-loglevel", "error", "-ss",
                        f"{start + 0.3:.2f}", "-i", str(video), "-frames:v", "1", str(f1)],
                       check=False)
        subprocess.run([FF, "-y", "-hide_banner", "-loglevel", "error", "-ss",
                        f"{start + max(1.0, d0 * 0.7):.2f}", "-i", str(video),
                        "-frames:v", "1", str(f2)], check=False)
        motion = psnr(f1, f2)
        f1.unlink(missing_ok=True)
        f2.unlink(missing_ok=True)
    # 4x3 contact sheet（不足 12 格用最后一帧补齐）
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
                        "-filter_complex", fc, "-map", "[o]", str(sheet)], check=False)
    return {"frames": [str(p) for p in paths], "sheet": str(sheet),
            "camera_motion_psnr": motion}


def main() -> int:
    only = sys.argv[1] if len(sys.argv) > 1 else None
    results = []
    for fmt_id, template_key, out_root in TARGETS:
        if only and only not in (fmt_id, template_key):
            continue
        fmt_path = FORMATS / f"{fmt_id}.json"
        if not fmt_path.is_file():
            results.append({"id": fmt_id, "ok": False, "error": "format missing"})
            continue
        fmt = json.loads(fmt_path.read_text(encoding="utf-8"))
        data = rtd.build_episode(fmt)
        for shot in data["shots"]:
            if shot.get("type") in ("title_card", "end_card", "meme_card"):
                shot["line"] = ""
                shot["subtitle"] = ""
                shot["punchline"] = ""
        demo_dir = out_root / template_key
        demo_dir.mkdir(parents=True, exist_ok=True)
        ep_json = demo_dir / "episode.json"
        ep_json.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        work = demo_dir / "work"
        out_mp4 = (out_root / f"{template_key}.mp4").resolve()
        cmd = [sys.executable, "-m", "pipeline.cli", "render",
               "--episode", str(ep_json), "--workdir", str(work),
               "--out", str(out_mp4), "--scenes",
               "--scene-template", template_key,
               "--transitions", "xfade", "--transition-style", "fade",
               "--transition-duration", "0.25"]
        print("[scene-demo] render", template_key, flush=True)
        proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                              encoding="utf-8", errors="replace")
        ok = proc.returncode == 0 and out_mp4.is_file()
        tail = (proc.stdout or proc.stderr or "")[-400:]
        print("[scene-demo]", template_key, "ok" if ok else "FAIL",
              tail.replace("\n", " ")[:240], flush=True)
        if not ok:
            results.append({"id": template_key, "ok": False, "error": tail})
            continue
        info = probe(out_mp4)
        v = next((s for s in info.get("streams", []) if s.get("codec_name") == "h264"), {})
        frames_dir = demo_dir / "frames"
        frames = frames_for_video(out_mp4, data["shots"], frames_dir,
                                  frames_dir / "scene_contact_sheet.png")
        manifest = json.loads((work / "manifest.json").read_text(encoding="utf-8"))
        result = {
            "id": template_key, "ok": True, "format": fmt_id,
            "duration": round(float(info["format"]["duration"]), 2),
            "size_bytes": int(info["format"]["size"]),
            "width": v.get("width"), "height": v.get("height"),
            "frames": v.get("nb_frames"),
            "shots": len(data["shots"]),
            "blackdetect": blackdetect(out_mp4),
            "manifest_scenes": manifest.get("scenes"),
            "ass_events": manifest.get("ass_events"),
            "camera_motion_psnr": frames.get("camera_motion_psnr"),
            "frames": frames,
            "video": str(out_mp4),
        }
        (demo_dir / "verify.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        results.append(result)
    (ROOT / "outputs" / "scene_demos_results.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": all(r.get("ok") for r in results),
                      "count": len(results), "results": results},
                     ensure_ascii=False, indent=2)[:4000])
    return 0 if all(r.get("ok") for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
