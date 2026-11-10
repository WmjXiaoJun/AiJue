# -*- coding: utf-8 -*-
"""R5：三观感成片的质量抽检（无脸/人物复制/字幕与包装覆盖率）。

用法：
    python docs/audit/_tools/r5_quality_check.py --video outputs/templates_3d/classics/final_300s_3d_anim.mp4 \
        --tid classics --look 3d_anim [--ass <final.ass>] [--episode <episode.json>] [--step 10]

产出：
- `tmp/r5_frames/<tid>_<look>_<sec>.png` 抽帧
- `tmp/r5_frames/<tid>_<look>_qc.json` 指标
- stdout 一段 JSON（可直接贴报告）

指标口径：
1. no_face_ratio = 检测不到人脸的帧 / 抽帧数（>0.20 判该 look 不合格）
2. dup_suspect   = 同一帧里 ≥2 张「尺寸接近 + 水平分开 + 灰度相关性高」的人脸（人物复制）
3. 字幕/包装     = ASS 的 Dialogue 条数（按 style 分：Main/Punch/Name/Tag/Title3D）

不用 GPU、不联网；只在 tmp/ 下写抽帧。
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
OUT_FRAMES = REPO / "tmp" / "r5_frames"
FFMPEG = REPO / "tools" / "node_modules" / "ffmpeg-static" / "ffmpeg.exe"
FFPROBE = REPO / "tools" / "node_modules" / "ffprobe-static" / "bin" / "win32" / "x64" / "ffprobe.exe"


def probe(path: Path) -> dict:
    raw = subprocess.run([str(FFPROBE), "-v", "error", "-show_entries",
                          "format=duration,size,bit_rate", "-show_entries",
                          "stream=codec_type,codec_name,width,height,r_frame_rate,sample_rate,channels",
                          "-of", "json", str(path)], capture_output=True, text=True,
                         encoding="utf-8", errors="replace").stdout
    try:
        data = json.loads(raw or "{}")
    except ValueError:
        return {}
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), {})
    audio = next((s for s in streams if s.get("codec_type") == "audio"), {})
    fmt = data.get("format") or {}
    num, _, den = str(video.get("r_frame_rate") or "0/1").partition("/")
    try:
        fps = round(float(num) / float(den or 1), 3)
    except (ValueError, ZeroDivisionError):
        fps = 0.0
    return {"duration": round(float(fmt.get("duration") or 0), 3),
            "bytes": int(fmt.get("size") or 0),
            "bitrate_kbps": round(float(fmt.get("bit_rate") or 0) / 1000, 1),
            "width": video.get("width"), "height": video.get("height"),
            "fps": fps, "vcodec": video.get("codec_name"),
            "has_audio": bool(audio), "acodec": audio.get("codec_name"),
            "sample_rate": audio.get("sample_rate"), "channels": audio.get("channels")}


def grab(video: Path, t: float, out: Path) -> bool:
    out.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run([str(FFMPEG), "-y", "-hide_banner", "-loglevel", "error",
                           "-ss", f"{t:.3f}", "-i", str(video), "-frames:v", "1",
                           "-q:v", "2", str(out)], capture_output=True, text=True)
    return out.is_file() and out.stat().st_size > 5_000


def _cascade():
    import cv2  # noqa: PLC0415

    cands = []
    local = REPO / "local_models"
    if local.is_dir():
        cands += [p for p in local.rglob("haarcascade_frontalface_default.xml")]
        cands += [p for p in local.rglob("*yunet*.onnx")][:1]
    cands.append(Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml")
    for path in cands:
        try:
            if str(path).endswith(".xml") and Path(path).is_file():
                clf = cv2.CascadeClassifier(str(path))
                if not clf.empty():
                    return clf, str(path)
        except Exception:  # noqa: BLE001
            continue
    return None, ""


def _iou_like(a, b) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    dx = abs((ax + aw / 2) - (bx + bw / 2)) / max(1.0, (aw + bw) / 2)
    dy = abs((ay + ah / 2) - (by + bh / 2)) / max(1.0, (ah + bh) / 2)
    return max(dx, dy)


def detect(img, clf, cv2) -> list[tuple]:
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    gray = cv2.equalizeHist(gray)
    h, w = gray.shape[:2]
    faces = clf.detectMultiScale(gray, scaleFactor=1.08, minNeighbors=5,
                                 minSize=(int(h * 0.05), int(h * 0.05)))
    out = []
    for (x, y, fw, fh) in faces:
        out.append((int(x), int(y), int(fw), int(fh)))
    return out, img, gray


def _same_face(crop_a, crop_b, cv2) -> float:
    if crop_a is None or crop_b is None or crop_a.size == 0 or crop_b.size == 0:
        return 0.0
    a = cv2.resize(crop_a, (64, 64))
    b = cv2.resize(crop_b, (64, 64))
    a = cv2.equalizeHist(a).astype("float32")
    b = cv2.equalizeHist(b).astype("float32")
    a -= a.mean()
    b -= b.mean()
    den = (float((a * a).sum()) ** 0.5) * (float((b * b).sum()) ** 0.5)
    return float((a * b).sum() / den) if den else 0.0


def qc_video(video: Path, tid: str, look: str, step: float = 10.0) -> dict:
    import cv2  # noqa: PLC0415

    info = probe(video)
    dur = float(info.get("duration") or 0.0)
    clf, clf_path = _cascade()
    now = 0.0
    frames: list[dict] = []
    paths: list[str] = []
    guard = 0
    while now < max(1.0, dur) and guard < 500:
        guard += 1
        out = OUT_FRAMES / f"{tid}_{look}_{int(round(now)):04d}.png"
        if grab(video, now, out):
            img = cv2.imread(str(out))
            faces, _img, gray = detect(img, clf, cv2) if clf is not None else ([], img, None)
            dup = []
            for i in range(len(faces)):
                for j in range(i + 1, len(faces)):
                    a, b = faces[i], faces[j]
                    ratio = max(a[3], b[3]) / max(1.0, min(a[3], b[3]))
                    if ratio > 1.35:
                        continue
                    if _iou_like(a, b) < 0.85:
                        continue
                    ca = gray[a[1]:a[1] + a[3], a[0]:a[0] + a[2]] if gray is not None else None
                    cb = gray[b[1]:b[1] + b[3], b[0]:b[0] + b[2]] if gray is not None else None
                    corr = _same_face(ca, cb, cv2)
                    if corr >= 0.55:
                        dup.append({"a": a, "b": b, "corr": round(corr, 3)})
            frames.append({"t": round(now, 2), "faces": len(faces), "boxes": faces,
                           "dup": dup, "path": str(out)})
            paths.append(str(out))
        now += float(step)
    picked = [(f["t"], f["path"]) for f in frames]
    no_face = [f for f in frames if f["faces"] == 0]
    multi = [f for f in frames if f["faces"] >= 2]
    dup = [f for f in frames if f["dup"]]
    result = {
        "tid": tid, "look": look, "video": str(video), **info,
        "step_sec": step, "frames": len(frames),
        "face_cascade": clf_path,
        "no_face_frames": len(no_face),
        "no_face_ratio": round(len(no_face) / max(1, len(frames)), 3),
        "no_face_times": [f["t"] for f in no_face],
        "multi_face_frames": len(multi), "multi_face_times": [f["t"] for f in multi],
        "dup_suspect_frames": len(dup),
        "dup_suspect": [{"t": f["t"], "faces": f["faces"], "dup": f["dup"],
                         "path": f["path"]} for f in dup],
        "no_face_fail": bool(len(frames) and len(no_face) / len(frames) > 0.20),
        "frame_paths": paths,
        "samples": picked[:3],
    }
    return result


_STYLE_RE = re.compile(r"^Dialogue:\s*\d+,[^,]*,[^,]*,([A-Za-z0-9_]+),")


def ass_stats(ass_path: Path) -> dict:
    if not ass_path.is_file():
        return {"ass": str(ass_path), "exists": False}
    text = ass_path.read_text(encoding="utf-8", errors="replace")
    styles: dict[str, int] = {}
    total = 0
    texts: list[str] = []
    for line in text.splitlines():
        if not line.startswith("Dialogue:"):
            continue
        total += 1
        m = _STYLE_RE.match(line)
        key = m.group(1) if m else "?"
        styles[key] = styles.get(key, 0) + 1
        body = line.split(",", 9)[-1] if line.count(",") >= 9 else ""
        body = re.sub(r"\{[^}]*\}", "", body).strip()
        if body:
            texts.append(body)
    return {"ass": str(ass_path), "exists": True, "dialogue_events": total,
            "by_style": styles, "sample_texts": texts[:8]}


def episode_stats(ep_path: Path) -> dict:
    if not ep_path.is_file():
        return {"episode": str(ep_path), "exists": False}
    try:
        data = json.loads(ep_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"episode": str(ep_path), "exists": False}
    shots = [s for s in (data.get("shots") or []) if isinstance(s, dict)]
    dialogue = [s for s in shots if str(s.get("line") or "").strip()]
    return {"episode": str(ep_path), "exists": True, "shots": len(shots),
            "dialogue_shots": len(dialogue),
            "rendered": sum(1 for s in shots if s.get("rendered")),
            "source": (data.get("meta") or {}).get("source"),
            "look": (data.get("meta") or {}).get("look")}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="三观感成片质量抽检")
    ap.add_argument("--video", required=True)
    ap.add_argument("--tid", required=True)
    ap.add_argument("--look", required=True)
    ap.add_argument("--step", type=float, default=10.0)
    ap.add_argument("--ass", default="")
    ap.add_argument("--episode", default="")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)
    video = Path(args.video)
    if not video.is_file():
        raise SystemExit(f"成片不存在: {video}")
    res = qc_video(video, args.tid, args.look, step=args.step)
    ass = Path(args.ass) if args.ass else (REPO / "outputs" / "fillers" / args.tid /
                                           "final.ass")
    ep = Path(args.episode) if args.episode else (
        REPO / "outputs" / "templates_3d" / args.tid / "work" /
        f"episode_{args.look}.json")
    res["ass"] = ass_stats(ass)
    res["episode"] = episode_stats(ep)
    res["coverage"] = {
        "main_subs": (res["ass"].get("by_style") or {}).get("Main", 0),
        "dialogue_shots": res["episode"].get("dialogue_shots"),
        "punch": (res["ass"].get("by_style") or {}).get("Punch", 0),
        "name_tags": (res["ass"].get("by_style") or {}).get("Name", 0),
        "scene_tags": (res["ass"].get("by_style") or {}).get("Tag", 0),
        "title3d": (res["ass"].get("by_style") or {}).get("Title3D", 0),
    }
    out = OUT_FRAMES / f"{args.tid}_{args.look}_qc.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    brief = {k: res[k] for k in ("tid", "look", "duration", "width", "height", "fps",
                                 "has_audio", "bytes", "bitrate_kbps", "frames",
                                 "no_face_frames", "no_face_ratio", "no_face_fail",
                                 "multi_face_frames", "dup_suspect_frames")}
    brief["coverage"] = res["coverage"]
    brief["qc_json"] = str(out)
    print(json.dumps(brief, ensure_ascii=False, indent=1))
    if not args.quiet:
        print("no_face_times:", res["no_face_times"], file=sys.stderr)
        if res["dup_suspect"]:
            print("dup_suspect:", json.dumps(
                [{"t": d["t"], "faces": d["faces"], "path": d["path"]}
                 for d in res["dup_suspect"]], ensure_ascii=False), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
