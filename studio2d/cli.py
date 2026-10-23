"""studio2d.cli - 命令行入口。

    python -m studio2d.cli create --spec contract/characters/laozhou.json --outdir outputs/characters/laozhou/2d

产物: portrait.png  portrait_bg.png  expressions/*.png  sheet.png  meta.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

from . import RENDERER, __version__
from .spec import load_character, build_character
from .render import (
    EXPR_NAMES, render_portrait, render_expression, render_sheet,
)


def _save(img, path):
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    img.save(path)


def _rel(outdir, name):
    return name.replace(os.sep, "/")


def cmd_create(args):
    t0 = time.time()
    ch = load_character(args.spec, args.style)
    outdir = os.path.abspath(args.outdir)
    os.makedirs(outdir, exist_ok=True)
    size = int(args.size)
    files = {}

    portrait = render_portrait(ch, size=size, background=False)
    _save(portrait, os.path.join(outdir, "portrait.png"))
    files["portrait"] = "portrait.png"

    portrait_bg = render_portrait(ch, size=size, background=True)
    _save(portrait_bg, os.path.join(outdir, "portrait_bg.png"))
    files["portrait_bg"] = "portrait_bg.png"

    expressions = {}
    for name in EXPR_NAMES:
        img = render_expression(ch, name, size=512)
        rel = "expressions/" + name + ".png"
        _save(img, os.path.join(outdir, "expressions", name + ".png"))
        expressions[name] = rel
    files["expressions"] = expressions

    if not args.no_sheet:
        sheet = render_sheet(ch)
        _save(sheet, os.path.join(outdir, "sheet.png"))
        files["sheet"] = "sheet.png"

    elapsed = round(time.time() - t0, 2)
    meta = {
        "id": ch.id,
        "name": ch.name,
        "title": ch.title,
        "persona": ch.persona,
        "style": ch.style,
        "style_label": ch.style_label,
        "renderer": RENDERER,
        "generator": "studio2d " + __version__,
        "palette": ch.palette,
        "files": files,
        "sizes": {"portrait": [size, size], "expressions": [512, 512], "sheet": [1600, 900]},
        "elapsed_sec": elapsed,
        "spec": os.path.relpath(os.path.abspath(args.spec), os.getcwd()).replace(os.sep, "/"),
    }
    with open(os.path.join(outdir, "meta.json"), "w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=2)

    flat = []
    for key in ("portrait", "portrait_bg", "sheet"):
        if key in files:
            flat.append(_rel(outdir, files[key]))
    for name in EXPR_NAMES:
        flat.append(_rel(outdir, expressions[name]))
    summary = {
        "ok": True,
        "id": ch.id,
        "name": ch.name,
        "style": ch.style,
        "renderer": RENDERER,
        "outdir": outdir.replace(os.sep, "/"),
        "files": flat,
        "elapsed_sec": elapsed,
    }
    print(json.dumps(summary, ensure_ascii=False))
    return 0


def cmd_validate(args):
    ch = load_character(args.spec, args.style)
    print(json.dumps({
        "ok": True, "id": ch.id, "name": ch.name, "style": ch.style,
        "hair_style": ch.hair_style, "outfit": ch.outfit.get("type"),
        "accessories": ch.accessories, "marks": ch.marks,
        "palette": ch.palette,
    }, ensure_ascii=False))
    return 0


def build_parser():
    ap = argparse.ArgumentParser(prog="studio2d.cli", description="离线程序化 2D 动漫角色生成器（Pillow）")
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("create", help="生成 portrait/表情/sheet/meta")
    p.add_argument("--spec", required=True, help="角色 spec JSON")
    p.add_argument("--outdir", required=True, help="产物目录")
    p.add_argument("--style", default="auto",
                   help="auto | 2d_cel | anime_cel | guoman（默认从 spec 推断）")
    p.add_argument("--size", default=768, help="portrait 尺寸（默认 768）")
    p.add_argument("--no-sheet", action="store_true", help="跳过设定图")
    p.set_defaults(func=cmd_create)

    v = sub.add_parser("validate", help="只解析 spec 并打印派生参数")
    v.add_argument("--spec", required=True)
    v.add_argument("--style", default="auto")
    v.set_defaults(func=cmd_validate)
    return ap


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "cmd", None):
        parser.print_help()
        return 2
    try:
        return args.func(args)
    except Exception as exc:
        sys.stderr.write("studio2d error: " + str(exc) + chr(10))
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
