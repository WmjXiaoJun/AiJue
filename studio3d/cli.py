"""studio3d CLI - procedural original anime character generator.

Usage:
    python -m studio3d.cli create --spec <spec.json> --outdir <dir>
                                  [--styles 3d_toon,2d_cel] [--size 512]
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time

PKG = os.path.dirname(os.path.abspath(__file__))
if PKG not in sys.path:
    sys.path.insert(0, PKG)

from presets import normalize_spec, style_config, palette_for

BLENDER_DEFAULT = "C:/Program Files/Blender Foundation/Blender 5.0/blender.exe"
REPO_ROOT = os.path.dirname(PKG)
FFMPEG_DEFAULT = os.path.join(REPO_ROOT, "tools", "node_modules", "ffmpeg-static", "ffmpeg.exe")
EXPRESSIONS = ("neutral", "happy", "surprised", "thinking")
RENDER_SCRIPT = os.path.join(PKG, "blender", "render.py")
EXPRESSIONS = ("neutral", "happy", "surprised", "thinking")


def blender_bin():
    return os.environ.get("BLENDER_BIN") or BLENDER_DEFAULT


def run_blender(spec_path, outdir, style, only, base, frames, timeout=175):
    cmd = [blender_bin(), "-b", "--factory-startup", "--python-exit-code", "1",
           "--python", RENDER_SCRIPT, "--",
           "--spec", spec_path, "--outdir", outdir, "--style", style,
           "--only", only, "--base", str(base), "--frames", str(frames)]
    t0 = time.time()
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "blender timeout after %ss" % timeout,
                "elapsed": round(time.time() - t0, 2)}
    out = proc.stdout or ""
    summary = None
    for line in out.splitlines()[::-1]:
        if line.startswith("SUMMARY "):
            try:
                summary = json.loads(line[len("SUMMARY "):])
            except Exception:
                summary = None
            break
    if proc.returncode != 0 or not summary or not summary.get("ok"):
        tail = "\n".join((out.splitlines() + (proc.stderr or "").splitlines())[-25:])
        return {"ok": False, "returncode": proc.returncode,
                "error": (summary or {}).get("error", "blender failed"),
                "tail": tail, "elapsed": round(time.time() - t0, 2)}
    summary["elapsed"] = round(time.time() - t0, 2)
    return summary


def ffmpeg_bin():
    env = os.environ.get("FFMPEG_BIN")
    if env:
        return env
    if os.path.exists(FFMPEG_DEFAULT):
        return FFMPEG_DEFAULT
    return "ffmpeg"


def encode_turntable(frames_dir, out_mp4, fps=24):
    import subprocess
    cmd = [ffmpeg_bin(), "-y", "-framerate", str(fps),
           "-i", os.path.join(frames_dir, "turntable_%04d.png"),
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18",
           "-preset", "veryfast", "-movflags", "+faststart", out_mp4]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if proc.returncode != 0 or not os.path.exists(out_mp4):
        raise RuntimeError("ffmpeg failed: " + (proc.stderr or "")[-800:])
    return out_mp4


def compose_style(style_dir, spec, pal, style_name, size, only):
    import compose as composer
    files = {}

    def _compose_one(raw_name, out_rel, px, head_y, glow):
        raw = os.path.join(style_dir, "raw", raw_name)
        if not os.path.exists(raw):
            return None
        out_abs = os.path.join(style_dir, out_rel)
        os.makedirs(os.path.dirname(out_abs), exist_ok=True)
        composer.compose_on_bg(raw, out_abs, px, pal, spec["accent_color"],
                               head_y=head_y, glow=glow)
        cut_rel = os.path.join("cutout", out_rel)
        cut_abs = os.path.join(style_dir, cut_rel)
        os.makedirs(os.path.dirname(cut_abs), exist_ok=True)
        composer.copy_rgba(raw, cut_abs)
        return out_abs, cut_abs

    if "portrait" in only:
        r = _compose_one("portrait_rgba.png", "portrait.png", 768, 0.40, 0.30)
        if r:
            files["portrait"] = r[0]
            files["portrait_cutout"] = r[1]
    if "expressions" in only:
        for expr in EXPRESSIONS:
            r = _compose_one(os.path.join("expressions", expr + "_rgba.png"),
                             os.path.join("expressions", expr + ".png"),
                             size, 0.44, 0.26)
            if r:
                files["expr_" + expr] = r[0]
                files["expr_cutout_" + expr] = r[1]
    if "sheet" in only:
        views = []
        for vname, label in (("front", "FRONT 正面"), ("side", "SIDE 侧面"),
                             ("quarter", "3/4 四分之三")):
            p = os.path.join(style_dir, "raw", "sheet_" + vname + ".png")
            views.append((p, label))
        if all(os.path.exists(p) for p, _ in views):
            spec["_style"] = style_name
            out_abs = os.path.join(style_dir, "sheet.png")
            composer.compose_sheet(views, out_abs, spec, pal)
            files["sheet"] = out_abs
    if "turntable" in only:
        frames_dir = os.path.join(style_dir, "raw", "turntable")
        if os.path.isdir(frames_dir) and os.listdir(frames_dir):
            out_mp4 = os.path.join(style_dir, "turntable.mp4")
            encode_turntable(frames_dir, out_mp4, fps=24)
            files["turntable"] = out_mp4
    return files


def copy_primary(style_dir, outdir):
    copied = []
    for root, dirs, fnames in os.walk(style_dir):
        if "raw" in root.split(os.sep):
            continue
        rel = os.path.relpath(root, style_dir)
        if rel == ".":
            rel = ""
        dst_dir = os.path.join(outdir, rel) if rel else outdir
        os.makedirs(dst_dir, exist_ok=True)
        for fn in fnames:
            if fn.endswith(".json") and rel == "":
                continue
            shutil.copy2(os.path.join(root, fn), os.path.join(dst_dir, fn))
            copied.append(os.path.join(dst_dir, fn))
    return copied


def cmd_create(args):
    t_all = time.time()
    with open(args.spec, "r", encoding="utf-8") as f:
        raw_spec = json.load(f)
    spec = normalize_spec(raw_spec)
    spec["_style"] = ""
    styles = None
    if args.styles:
        styles = [s.strip() for s in args.styles.split(",") if s.strip()]
    if not styles:
        styles = list(spec["style"]["variants"]) or ["3d_toon"]
    primary = spec["style"]["primary"]
    if primary not in styles:
        primary = styles[0]
    size = int(args.size or 512)
    only = args.only or "portrait,expressions,sheet,turntable"
    only_list = [s.strip() for s in only.split(",") if s.strip()]
    outdir = os.path.abspath(args.outdir)
    os.makedirs(outdir, exist_ok=True)
    pal = palette_for(spec)
    spec_abs = os.path.abspath(args.spec)
    report = {"id": spec["id"], "styles": styles, "primary": primary,
              "styles_detail": {}, "files": {}}
    ok_all = True
    for style in styles:
        cfg = style_config(style)
        style_dir = os.path.join(outdir, "style_" + style)
        os.makedirs(style_dir, exist_ok=True)
        print("[studio3d] blender %s style=%s ..." % (spec["id"], style), flush=True)
        res = run_blender(spec_abs, style_dir, style, ",".join(only_list), size, 48)
        if not res.get("ok"):
            ok_all = False
            report["styles_detail"][style] = res
            print("[studio3d] ERROR " + json.dumps(res, ensure_ascii=False)[:2000])
            continue
        files = compose_style(style_dir, spec, pal, style, size, only_list)
        report["styles_detail"][style] = {
            "ok": True,
            "blender_sec": res.get("elapsed"),
            "renders": res.get("renders", []),
            "files": files,
        }
        if style == primary:
            copied = copy_primary(style_dir, outdir)
            report["files"] = {k: os.path.relpath(v, style_dir) for k, v in files.items()}
            report["primary_files"] = [os.path.relpath(p, outdir) for p in copied]
    # meta.json
    meta = {
        "id": spec["id"],
        "name": spec["name"],
        "title": spec.get("title", ""),
        "persona": spec.get("persona", ""),
        "voice": spec.get("voice", ""),
        "accent_color": spec["accent_color"],
        "styles": styles,
        "primary_style": primary,
        "renderer": "blender-eevee-procedural",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "elapsed_sec": round(time.time() - t_all, 2),
        "palette": pal,
        "files": report["files"],
        "styles_detail": report["styles_detail"],
    }
    with open(os.path.join(outdir, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    # summary
    all_files = []
    for k, v in report["files"].items():
        all_files.append(os.path.join(outdir, v))
    summary = {
        "ok": ok_all,
        "id": spec["id"],
        "outdir": outdir,
        "styles": styles,
        "files": all_files,
        "elapsed_sec": round(time.time() - t_all, 2),
    }
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if ok_all else 1


def main(argv=None):
    ap = argparse.ArgumentParser(prog="studio3d.cli",
                                 description="Procedural original anime character generator")
    sub = ap.add_subparsers(dest="cmd")
    c = sub.add_parser("create", help="generate a character")
    c.add_argument("--spec", required=True)
    c.add_argument("--outdir", required=True)
    c.add_argument("--styles", default=None,
                   help="comma separated styles, e.g. 3d_toon,3d_cel")
    c.add_argument("--size", type=int, default=512, help="expression render size")
    c.add_argument("--only", default="portrait,expressions,sheet,turntable",
                   help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    if args.cmd != "create":
        ap.print_help()
        return 1
    try:
        return cmd_create(args)
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.exit(main())
