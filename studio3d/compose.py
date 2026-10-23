"""PIL composition: dark studio backdrops, portrait/expression framing, sheets."""
import os
import math
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter
from presets import hex_rgb


FONT_BOLD = "C:/Windows/Fonts/msyhbd.ttc"
FONT_REG = "C:/Windows/Fonts/msyh.ttc"


def c255(hx):
    return tuple(int(round(v * 255.0)) for v in hex_rgb(hx))


def _font(size, bold=True):
    path = FONT_BOLD if bold else FONT_REG
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default()


def studio_background(size, pal, accent, head_y=0.40, glow=0.30):
    if isinstance(size, int):
        size = (size, size)
    w, h = size
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    cx = w * 0.5
    cy = h * head_y
    r = np.sqrt(((xx - cx) / w) ** 2 + ((yy - cy) / h) ** 2)
    t = np.clip(r / 0.66, 0.0, 1.0)[..., None]
    c1 = np.array(hex_rgb(pal["bg_mid"]), dtype=np.float32)
    c2 = np.array(hex_rgb(pal["bg_dark"]), dtype=np.float32)
    arr = c1 * (1.0 - t) + c2 * t
    # accent glow behind the character
    g = np.exp(-(((xx - cx) / (w * 0.46)) ** 2 + ((yy - cy) / (h * 0.46)) ** 2))
    acc = np.array(hex_rgb(accent), dtype=np.float32)
    arr = arr + g[..., None] * acc * glow
    # vignette
    vig = 1.0 - 0.38 * np.clip((r - 0.58) / 0.55, 0.0, 1.0)
    arr = arr * vig[..., None]
    # subtle warm floor bounce
    floor = np.clip((yy / h - 0.72) / 0.3, 0.0, 1.0)[..., None]
    arr = arr + floor * np.array([0.05, 0.045, 0.06], dtype=np.float32)
    arr = np.clip(arr * 255.0, 0, 255).astype(np.uint8)
    return Image.fromarray(arr, "RGB").convert("RGBA")


def compose_on_bg(rgba_path, out_path, size, pal, accent, head_y=0.40, glow=0.30):
    bg = studio_background(size, pal, accent, head_y=head_y, glow=glow)
    fg = Image.open(rgba_path).convert("RGBA")
    if isinstance(size, int):
        size = (size, size)
    if fg.size != size:
        fg = fg.resize(size, Image.LANCZOS)
    bg.alpha_composite(fg)
    bg.convert("RGB").save(out_path, quality=95)
    return out_path


def copy_rgba(src, dst):
    im = Image.open(src).convert("RGBA")
    im.save(dst)
    return dst


def _wrap_cjk(text, font, max_w):
    draw = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    lines = []
    cur = ""
    for ch in str(text):
        if ch == "\n":
            lines.append(cur)
            cur = ""
            continue
        test = cur + ch
        if draw.textlength(test, font=font) > max_w and cur:
            lines.append(cur)
            cur = ch
        else:
            cur = test
    if cur:
        lines.append(cur)
    return lines


def compose_sheet(views, out_path, spec, pal, width=1600, height=900):
    """views: list of (rgba_path, label) for front / side / quarter."""
    W, H = width, height
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    t = np.clip((xx / W) * 0.7 + (yy / H) * 0.3, 0.0, 1.0)[..., None]
    c1 = np.array(hex_rgb(pal["bg_mid"]), dtype=np.float32)
    c2 = np.array(hex_rgb(pal["bg_dark"]), dtype=np.float32)
    arr = c1 * (1 - t) + c2 * t
    acc = np.array(hex_rgb(spec["accent_color"]), dtype=np.float32)
    g = np.exp(-(((xx - W * 0.26) / (W * 0.55)) ** 2 + ((yy - H * 0.45) / (H * 0.6)) ** 2))
    arr = arr + g[..., None] * acc * 0.15
    img = Image.fromarray(np.clip(arr * 255, 0, 255).astype(np.uint8), "RGB")
    draw = ImageDraw.Draw(img)
    panel_w = 370
    gap = 14
    top = 36
    x0 = 50
    panel_h = 736
    for i, (path, label) in enumerate(views):
        px = x0 + i * (panel_w + gap)
        draw.rounded_rectangle([px, top, px + panel_w, top + panel_h], radius=16,
                               fill=c255(pal["bg_dark"]),
                               outline=c255(pal["accent"]), width=1)
        if not os.path.exists(path):
            continue
        fg = Image.open(path).convert("RGBA")
        fh = 648
        scale = fh / fg.height
        fg = fg.resize((max(1, int(fg.width * scale)), fh), Image.LANCZOS)
        if fg.width > panel_w - 16:
            cut = (fg.width - (panel_w - 16)) // 2
            fg = fg.crop((cut, 0, cut + panel_w - 16, fh))
        fg_x = px + (panel_w - fg.width) // 2
        img.paste(fg, (fg_x, top + 14), fg)
        lab_font = _font(26, bold=True)
        lw = draw.textlength(label, font=lab_font)
        draw.text((px + (panel_w - lw) / 2, top + panel_h - 44), label,
                  font=lab_font, fill=(255, 255, 255))
    info_x = x0 + 3 * (panel_w + gap) + 26
    info_w = W - info_x - 44
    f_name = _font(78, bold=True)
    draw.text((info_x, 96), spec["name"], font=f_name, fill=(255, 255, 255))
    f_title = _font(30)
    draw.text((info_x, 196), spec.get("title", ""), font=f_title,
              fill=c255(pal["accent_light"]))
    draw.rounded_rectangle([info_x, 246, info_x + min(220, info_w), 258], radius=6,
                           fill=c255(spec["accent_color"]))
    f_small = _font(22)
    y = 286
    for line in _wrap_cjk(spec.get("persona", ""), f_small, info_w):
        draw.text((info_x, y), line, font=f_small, fill=(206, 212, 228))
        y += 34
    y = max(y + 22, 402)
    draw.text((info_x, y), "PALETTE", font=_font(20, bold=True), fill=(148, 158, 184))
    y += 40
    swatches = [("ACCENT", spec["accent_color"]), ("HAIR", pal["hair"]),
                ("SKIN", pal["skin"]), ("MAIN", pal["main"]), ("SHIRT", pal["shirt"])]
    sw = 92
    for i, (lab, hx) in enumerate(swatches):
        sx = info_x + (i % 3) * (sw + 12)
        sy = y + (i // 3) * 112
        draw.rounded_rectangle([sx, sy, sx + sw, sy + 62], radius=9,
                               fill=c255(hx), outline=(255, 255, 255), width=1)
        draw.text((sx, sy + 68), lab, font=_font(15), fill=(160, 168, 190))
        draw.text((sx, sy + 88), hx.upper(), font=_font(15), fill=(118, 126, 148))
    draw.text((x0, H - 62), "造片局 · AI 微综艺  /  CHARACTER SHEET", font=_font(24),
              fill=(255, 255, 255))
    style_label = "STYLE  " + str(spec.get("_style", "")).upper()
    sw2 = draw.textlength(style_label, font=_font(22, bold=True))
    draw.text((W - 60 - sw2, H - 62), style_label, font=_font(22, bold=True),
              fill=c255(spec["accent_color"]))
    img.save(out_path, quality=95)
    return out_path