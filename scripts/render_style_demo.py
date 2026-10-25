# -*- coding: utf-8 -*-
"""task-43：tonight_show 3D 风格 H3 demo（8 个 H3 镜头 + 2D/3D 对比抽帧）。

流程：
  1) 由 contract/formats/tonight_show_5min.json 生成 8 个 H3 镜头 + 片头/片尾 episode；
  2) pipeline render --scenes --style 3d_anime --dynamic：
     场景合成（plate_3d + cutout_3d 优先）→ 以合成整帧为 H3 参考图 + style prompt → H3 8 条；
  3) 拼接/字幕/BGM 输出 outputs/h3_style_demos/tonight_show_3d.mp4；
  4) 抽帧 + 2D（history_talk h3_scene_demo.mp4）对比 + verify.json。
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
OUT = ROOT / "outputs" / "h3_style_demos"
DEMO_DIR = OUT / "tonight_show_3d"
VIDEO = OUT / "tonight_show_3d.mp4"
TWO_D = ROOT / "outputs" / "history_full" / "h3_scene_demo.mp4"


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


def psnr(f1: Path, f2: Path) -> float | None:
    p = subprocess.run([FF, "-hide_banner", "-i", str(f1), "-i", str(f2),
                        "-lavfi", "psnr", "-f", "null", "NUL"],
                       capture_output=True, text=True, errors="replace")
    m = re.search(r"average:([0-9.]+)", p.stderr or "")
    return float(m.group(1)) if m else None


def build_episode() -> dict:
    fmt = json.loads((ROOT / "contract" / "formats" / "tonight_show_5min.json")
                     .read_text(encoding="utf-8"))
    data = rtd.build_episode(fmt)
    title = next(s for s in data["shots"] if s["type"] == "title_card")
    end = next(s for s in data["shots"] if s["type"] == "end_card")
    dyn = [s for s in data["shots"] if s["type"] not in ("title_card", "end_card")]
    # 8 个 H3 镜头：去掉一个 host 镜头，保留 host/guest/reaction 混合
    drop = [s for s in dyn if s["id"] == "demo_s010"] or [s for s in dyn if s["type"] == "host_to_camera"][-1:]
    drop_ids = {s["id"] for s in drop}
    chosen = [s for s in dyn if s["id"] not in drop_ids][:8]
    order = {s["id"]: i for i, s in enumerate(data["shots"])}
    chosen.sort(key=lambda s: order.get(s["id"], 0))
    for s in (title, end):
        s["line"] = ""
        s["subtitle"] = ""
        s["punchline"] = ""
    data["shots"] = [title] + chosen + [end]
    data["episode"]["title"] = "造片局 · 美式深夜秀 · 3D 动漫版"
    data["total_duration"] = round(sum(float(s["duration"]) for s in data["shots"]), 2)
    return data


def run_render(data: dict) -> int:
    DEMO_DIR.mkdir(parents=True, exist_ok=True)
    ep_json = DEMO_DIR / "episode.json"
    ep_json.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    cmd = [sys.executable, "-m", "pipeline.cli", "render",
           "--episode", str(ep_json),
           "--workdir", str(DEMO_DIR / "work"),
           "--out", str(VIDEO),
           "--scenes", "--scene-template", "tonight_show",
           "--style", "3d_anime",
           "--dynamic", "--dynamic-fallback-static",
           "--transitions", "xfade", "--transition-style", "fade",
           "--transition-duration", "0.25"]
    print("[style-demo] render tonight_show_3d (8 H3 shots)", flush=True)
    proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    tail = (proc.stdout or proc.stderr or "")[-800:]
    print("[style-demo] exit", proc.returncode, tail.replace("\n", " ")[:500], flush=True)
    if proc.returncode != 0:
        raise SystemExit(proc.returncode)
    return proc.returncode


def extract_frames(data: dict) -> dict:
    frames = DEMO_DIR / "frames"
    frames.mkdir(parents=True, exist_ok=True)
    t = 0.0
    paths = []
    for i, shot in enumerate(data["shots"]):
        dur = float(shot.get("duration") or 3.0)
        sec = t + min(1.2, dur * 0.5)
        p = frames / f"shot_{i+1:02d}_{shot.get('type')}.png"
        subprocess.run([FF, "-y", "-hide_banner", "-loglevel", "error", "-ss",
                        f"{sec:.2f}", "-i", str(VIDEO), "-frames:v", "1", str(p)],
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
                        str(frames / "scene_contact_sheet.png")], check=False)
    return {"frames": [str(p) for p in paths],
            "sheet": str(frames / "scene_contact_sheet.png")}


def compare_2d_3d(frames_info: dict) -> str:
    """2D（history_talk h3_scene_demo）vs 3D（tonight_show_3d）对比图。"""
    out = OUT / "compare_2d_3d.png"
    f2 = OUT / "frames" / "_2d_compare.png"
    f3 = OUT / "frames" / "_3d_compare.png"
    subprocess.run([FF, "-y", "-hide_banner", "-loglevel", "error", "-ss", "12.0",
                    "-i", str(TWO_D), "-frames:v", "1", str(f2)], check=False)
    # 3D：取第 2 个镜头（host 中景，避免片头卡）
    src = None
    for p in frames_info.get("frames") or []:
        if "host_to_camera" in p:
            src = p
            break
    if src is None and frames_info.get("frames"):
        src = frames_info["frames"][0]
    if src:
        subprocess.run([FF, "-y", "-hide_banner", "-loglevel", "error",
                        "-i", src, str(f3)], check=False)
    if not (f2.is_file() and f3.is_file()):
        return ""
    font = ROOT / "outputs" / "episodes" / "ep_e1015494fa" / "fonts" / "Deng.ttf"
    subprocess.run([FF, "-y", "-hide_banner", "-loglevel", "error", "-i", str(f2),
                    "-i", str(f3), "-filter_complex",
                    "[0:v]scale=640:360,drawtext=text='history_talk 2D H3':fontfile='" + str(font).replace("\\", "/") + "':fontsize=24:fontcolor=white:box=1:boxcolor=black@0.6:x=14:y=14[a];"
                    "[1:v]scale=640:360,drawtext=text='tonight_show 3D H3':fontfile='" + str(font).replace("\\", "/") + "':fontsize=24:fontcolor=white:box=1:boxcolor=black@0.6:x=14:y=14[b];"
                    "[a][b]hstack[o]", "-map", "[o]", str(out)], check=False)
    return str(out)


def main() -> int:
    data = build_episode()
    # 省时：若显式传 --reuse 且已有成片，跳过 H3 重跑
    reuse = "--reuse" in sys.argv and VIDEO.is_file()
    if not reuse:
        run_render(data)
    info = probe(VIDEO)
    manifest = json.loads((DEMO_DIR / "work" / "manifest.json").read_text(encoding="utf-8"))
    frames_info = extract_frames(data)
    compare = compare_2d_3d(frames_info)
    # H3 meta
    metas = []
    for mp in sorted((DEMO_DIR / "work" / "dynamic" / "meta").glob("*.json")):
        m = json.loads(mp.read_text(encoding="utf-8"))
        if m.get("mode") in ("audio_ref", "fl2v"):
            metas.append({"sid": m.get("id") or mp.stem, "mode": m["mode"],
                          "style": m.get("style"),
                          "ref_strategy": m.get("ref_strategy"),
                          "prompt_prefix": (m.get("style_prefix") or "")[:120],
                          "prompt": (m.get("prompt") or "")[:400],
                          "prompt_file": m.get("prompt_file")})
    scenes = manifest.get("scenes") or {}
    scene_metas = []
    for mp in sorted((DEMO_DIR / "work" / "scenes" / "meta").glob("*.json")):
        m = json.loads(mp.read_text(encoding="utf-8"))
        if m.get("character"):
            scene_metas.append({"id": m.get("id"), "style": m.get("style"),
                                "plate": m.get("plate"), "cutout_style": m.get("cutout_style"),
                                "cutout_fallback": m.get("cutout_fallback")})
    result = {
        "ok": True,
        "video": str(VIDEO),
        "duration": round(float(info["format"]["duration"]), 2),
        "size_bytes": int(info["format"]["size"]),
        "resolution": [s.get("width") for s in info.get("streams", []) if s.get("codec_name") == "h264"][:1],
        "blackdetect": blackdetect(VIDEO),
        "manifest_style": manifest.get("style"),
        "manifest_scenes": scenes,
        "dynamic_count": len(metas),
        "dynamic": metas,
        "scene_meta_sample": scene_metas[:4],
        "cutout_fallback_count": sum(1 for m in scene_metas if m.get("cutout_fallback")),
        "plate_fallback": scenes.get("plate_fallback"),
        "frames": frames_info,
        "compare_2d_3d": compare,
    }
    (OUT / "verify.json").write_text(json.dumps(result, ensure_ascii=False, indent=2),
                                     encoding="utf-8")
    print(json.dumps({k: result[k] for k in ("video", "duration", "size_bytes", "blackdetect",
                                            "manifest_style", "manifest_scenes", "dynamic_count",
                                            "cutout_fallback_count", "plate_fallback",
                                            "frames", "compare_2d_3d")},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
