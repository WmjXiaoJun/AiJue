# -*- coding: utf-8 -*-
"""把带 alpha 的角色立绘合成到深色影棚背景（H3 i2v 首帧用）。

用法:
  python compose_portrait_bg.py <portrait_rgba.png> <out.png> [--size 768]
       [--bg-top 0B0D14] [--bg-bottom 232838] [--glow F5C518] [--glow-alpha 46]
"""
from __future__ import annotations
import argparse
import os
from PIL import Image, ImageDraw, ImageFilter


def hex2rgb(s: str):
    s = s.lstrip("#")
    return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))


def vertical_gradient(size, top, bottom):
    w, h = size
    img = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(img)
    for y in range(h):
        t = y / max(1, h - 1)
        c = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
        d.line([(0, y), (w, y)], fill=c)
    return img


def radial_glow(size, color, alpha=46, radius=0.62):
    w, h = size
    g = Image.radial_gradient("L").resize((w, h), Image.BICUBIC)  # 0 center -> 255 edge
    g = g.point(lambda v: int(alpha * (1 - v / 255.0)))
    layer = Image.new("RGBA", (w, h), color + (0,))
    layer.putalpha(g)
    return layer


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("portrait")
    ap.add_argument("out")
    ap.add_argument("--size", type=int, default=0, help="输出边长，0=沿用立绘尺寸")
    ap.add_argument("--bg-top", default="0B0D14")
    ap.add_argument("--bg-bottom", default="232838")
    ap.add_argument("--glow", default="F5C518")
    ap.add_argument("--glow-alpha", type=int, default=46)
    ap.add_argument("--glow-radius", type=float, default=0.62)
    args = ap.parse_args()

    fg = Image.open(args.portrait).convert("RGBA")
    if args.size and args.size != fg.width:
        fg = fg.resize((args.size, args.size), Image.LANCZOS)
    size = (fg.width, fg.height)

    bg = vertical_gradient(size, hex2rgb(args.bg_top), hex2rgb(args.bg_bottom)).convert("RGBA")
    glow = radial_glow(size, hex2rgb(args.glow), args.glow_alpha, args.glow_radius)
    bg = Image.alpha_composite(bg, glow)

    # 轻投影：把 alpha 模糊后压暗一点，让角色与背景分层
    shadow = fg.getchannel("A").filter(ImageFilter.GaussianBlur(22)).point(lambda v: int(v * 0.55))
    shadow_layer = Image.new("RGBA", size, (0, 0, 0, 0))
    shadow_layer.putalpha(shadow)
    bg = Image.alpha_composite(bg, shadow_layer)

    out = Image.alpha_composite(bg, fg).convert("RGB")
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    out.save(args.out)
    print("wrote: %s  %dx%d" % (args.out, out.width, out.height))


if __name__ == "__main__":
    main()
