# -*- coding: utf-8 -*-
"""用 Pillow 把「立绘 + 三视图 + 表情 + 名字/配色条」拼成角色设定图 sheet.png（1600x900）。

布局：
  ┌──────────────────────────────────────────────────────────────┐
  │ ▌名字  称号                                        brand 条  │
  │ ┌────────┐   三视图: [front] [side] [three_quarter]          │
  │ │portrait│                                                   │
  │ │ 620px  │   表情: [neutral] [happy] [surprised] [thinking]   │
  │ └────────┘   配色: [accent][main][shirt][skin][hair][eye]     │
  │ persona / tags 文本                                           │
  └──────────────────────────────────────────────────────────────┘
"""
from __future__ import annotations

import os

from PIL import Image, ImageDraw, ImageFont

CANVAS = (1600, 900)
BG_TOP = (11, 13, 20)
BG_BOTTOM = (35, 40, 56)
TEXT = (240, 240, 245)
TEXT_MUTED = (160, 165, 180)
ACCENT_DEFAULT = (245, 197, 24)

_FONT_CANDIDATES = [
    ("C:/Windows/Fonts/msyhbd.ttc", 0),
    ("C:/Windows/Fonts/msyh.ttc", 0),
    ("C:/Windows/Fonts/simhei.ttf", 0),
    ("C:/Windows/Fonts/arialbd.ttf", 0),
]


def load_font(size: int, bold: bool = False):
    cands = list(_FONT_CANDIDATES)
    if bold:
        cands = [c for c in cands if "bd" in c[0]] + [c for c in cands if "bd" not in c[0]]
    for path, index in cands:
        try:
            if os.path.isfile(path):
                return ImageFont.truetype(path, size, index=index)
        except Exception:
            continue
    try:
        return ImageFont.load_default(size)
    except Exception:
        return ImageFont.load_default()


def hex2rgb(s, default=(245, 197, 24)):
    try:
        s = str(s).lstrip("#")
        if len(s) == 3:
            s = "".join(c * 2 for c in s)
        return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))
    except Exception:
        return default


def _gradient(size, top, bottom):
    w, h = size
    img = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(img)
    for y in range(h):
        t = y / max(1, h - 1)
        d.line([(0, y), (w, y)],
               fill=tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)))
    return img


def _fit_image(path, box_w, box_h):
    """等比缩放并居中放到 box_w x box_h 的透明画布上。"""
    im = Image.open(path).convert("RGBA")
    scale = min(box_w / im.width, box_h / im.height)
    w, h = max(1, int(im.width * scale)), max(1, int(im.height * scale))
    im = im.resize((w, h), Image.LANCZOS)
    canvas = Image.new("RGBA", (box_w, box_h), (0, 0, 0, 0))
    canvas.paste(im, ((box_w - w) // 2, (box_h - h) // 2), im)
    return canvas


def _placeholder(box_w, box_h, text):
    canvas = Image.new("RGBA", (box_w, box_h), (0, 0, 0, 0))
    d = ImageDraw.Draw(canvas)
    d.rounded_rectangle([0, 0, box_w - 1, box_h - 1], radius=10,
                        outline=(80, 85, 100, 255), width=2)
    d.text((box_w // 2, box_h // 2), text, font=load_font(18), fill=TEXT_MUTED, anchor="mm")
    return canvas


def _card(canvas, path, x, y, box_w, box_h, label=None, accent=ACCENT_DEFAULT):
    """在 canvas 上贴一张图（带细边框 + 可选下方标签）。"""
    if path and os.path.isfile(path):
        img = _fit_image(path, box_w, box_h)
    else:
        img = _placeholder(box_w, box_h, "missing")
    d = ImageDraw.Draw(canvas)
    d.rounded_rectangle([x - 2, y - 2, x + box_w + 2, y + box_h + 2], radius=12,
                        outline=accent + (110,), width=2)
    canvas.alpha_composite(img, (x, y))
    if label:
        d.text((x + box_w // 2, y + box_h + 22), label,
               font=load_font(20), fill=TEXT_MUTED, anchor="mm")


def _wrap(draw, text, font, max_width):
    lines, cur = [], ""
    for ch in text:
        if draw.textlength(cur + ch, font=font) <= max_width:
            cur += ch
        else:
            if cur:
                lines.append(cur)
            cur = ch
    if cur:
        lines.append(cur)
    return lines


def compose_sheet(spec: dict, outdir: str, out_path: str = None,
                  seed: int = 42, style: str = "anime",
                  model: str = "animagine-xl-4.0.safetensors") -> str:
    """spec + outdir 里的图 -> sheet.png；返回输出路径。"""
    out_path = out_path or os.path.join(outdir, "sheet.png")
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    accent = hex2rgb(spec.get("accent_color"))
    app = spec.get("appearance") or {}
    outfit = app.get("outfit") or {}

    base = _gradient(CANVAS, BG_TOP, BG_BOTTOM).convert("RGBA")
    canvas = Image.new("RGBA", CANVAS, (0, 0, 0, 0))
    canvas.alpha_composite(base)
    d = ImageDraw.Draw(canvas)

    # 顶部 accent 条 + 名字/称号
    d.rectangle([0, 0, 12, CANVAS[1]], fill=accent + (255,))
    d.rectangle([48, 30, 56, 108], fill=accent + (255,))
    d.text((76, 26), str(spec.get("name") or spec.get("id")),
           font=load_font(48, bold=True), fill=TEXT)
    d.text((78, 84), "%s  ·  %s" % (spec.get("title") or "", spec.get("id") or ""),
           font=load_font(24), fill=accent)
    d.text((CANVAS[0] - 48, 40), "造片局 · AI 微综艺", font=load_font(22, bold=True),
           fill=TEXT_MUTED, anchor="rt")
    d.text((CANVAS[0] - 48, 74), "imagegen  concept sheet", font=load_font(18),
           fill=TEXT_MUTED, anchor="rt")

    # 左：立绘（高度让出底部配色条空间，label 不会压到色卡）
    portrait = os.path.join(outdir, "portrait.png")
    _card(canvas, portrait, 48, 130, 400, 580, label="portrait", accent=accent)

    # 右上：三视图
    rx = 500
    d.text((rx, 126), "三视图  three view", font=load_font(22, bold=True), fill=TEXT)
    tw, th = 280, 280
    for i, tag in enumerate(["front", "side", "three_quarter"]):
        p = os.path.join(outdir, "three_view", tag + ".png")
        _card(canvas, p, rx + i * (tw + 16), 158, tw, th,
              label=tag.replace("_", " "), accent=accent)

    # 右下：表情
    ey = 470
    d.text((rx, ey), "表情  expressions", font=load_font(22, bold=True), fill=TEXT)
    ew, eh = 208, 208
    for i, tag in enumerate(["neutral", "happy", "surprised", "thinking"]):
        p = os.path.join(outdir, "expressions", tag + ".png")
        _card(canvas, p, rx + i * (ew + 14), ey + 32, ew, eh, label=tag, accent=accent)

    # 底部：配色条
    py = 770
    d.text((48, py), "配色 palette", font=load_font(20, bold=True), fill=TEXT_MUTED)
    swatches = [
        ("accent", spec.get("accent_color")),
        ("main", outfit.get("main")),
        ("shirt", outfit.get("shirt")),
        ("skin", app.get("skin")),
        ("hair", app.get("hair_color")),
        ("eye", app.get("eye_color")),
    ]
    sx = 200
    for name, value in swatches:
        if not value:
            continue
        rgb = hex2rgb(value)
        d.rounded_rectangle([sx, py - 8, sx + 76, py + 34], radius=6, fill=rgb + (255,))
        d.text((sx + 38, py + 44), name, font=load_font(14), fill=TEXT_MUTED, anchor="mm")
        d.text((sx + 38, py + 62), str(value), font=load_font(13), fill=(110, 115, 130), anchor="mm")
        sx += 108

    # 底部：persona / 生成参数
    persona = str(spec.get("persona") or "")
    font = load_font(19)
    lines = _wrap(d, "人设：" + persona, font, 1500)[:2]
    ty = 846
    for line in lines:
        d.text((48, ty), line, font=font, fill=TEXT_MUTED)
        ty += 26
    d.text((CANVAS[0] - 48, 872),
           "%s · style=%s · seed=%s" % (model, style, seed),
           font=load_font(16), fill=(110, 115, 130), anchor="rt")

    canvas.convert("RGB").save(out_path, quality=95)
    return out_path
