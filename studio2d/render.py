"""studio2d.render - 组装 portrait / 表情 / 立绘 / 设定图。"""

from __future__ import annotations

import math

from PIL import Image, ImageDraw

from .shapekit import (
    Scene, col, mix, lighten, darken, vgradient, radial_mask, star_points, load_font,
    catmull,
)
from . import face as F
from . import body as B

EXPR_NAMES = ["neutral", "happy", "surprised", "thinking"]
EXPR_LABELS = {
    "neutral": "NEUTRAL / 中性",
    "happy": "HAPPY / 开心",
    "surprised": "SURPRISED / 惊讶",
    "thinking": "THINKING / 思考",
}
HAIR_LABELS = {
    "short_swept": "短发 · 侧扫", "spiky": "刺猬短发", "bob": "波波头",
    "twin_tail": "双马尾", "ponytail": "马尾", "long_straight": "长直发",
    "bald": "光头", "bun": "丸子头",
}
OUTFIT_LABELS = {
    "suit": "西装套装", "hoodie": "连帽卫衣", "jacket": "机车夹克",
    "shirt": "衬衫", "dress": "连衣裙", "coat": "大衣",
}
ACC_LABELS = {
    "glasses": "眼镜", "watch": "腕表", "headphones": "耳机",
    "earring": "耳饰", "none": "无",
}
MARK_LABELS = {
    "warm_smile": "温和笑容", "star_clip": "星星发夹", "scar_brow": "眉骨疤",
}


# ------------------------------------------------------------------ 组装

def _frame_bust(ch, size, head_ratio=0.1349, top_ratio=0.055):
    s = size * head_ratio * ch.head_scale
    ox = size * 0.5
    oy = size * top_ratio + 1.30 * s
    return ox, oy, s


def _head_parts(scene, ch, expr):
    par = F.EXPR.get(expr, F.EXPR["neutral"])
    F.draw_ears(scene, ch)
    outline = F.draw_face(scene, ch)
    F.draw_face_paint(scene, ch, outline, par)
    F.draw_brows(scene, ch, par)
    for side in (-1, 1):
        F.draw_eye(scene, ch, side, par)
    F.draw_nose(scene, ch)
    F.draw_mouth(scene, ch, par.get("mouth", "smile"))
    F.draw_facial_hair(scene, ch)
    F.draw_marks(scene, ch)
    F.draw_hair_front(scene, ch)


def _rim_light(scene, ch):
    col_rim = col(lighten(ch.accent, 0.60), 62)
    path = [(-0.96, 0.10), (-1.08, -0.32), (-1.02, -0.84), (-0.74, -1.22),
            (-0.40, -1.39), (-0.04, -1.46)]
    scene.stroke(catmull(path, 12, False), color=col_rim, width=0.014)
    shoulder = [(-1.16, 2.04), (-1.48, 2.18), (-1.66, 2.44)]
    scene.stroke(catmull(shoulder, 10, False), color=col(lighten(ch.accent, 0.60), 34),
                 width=0.011)


def draw_bust(scene, ch, expr="neutral"):
    F.draw_hair_back(scene, ch)
    F.draw_neck(scene, ch)
    B.draw_body(scene, ch, mode="bust")
    _head_parts(scene, ch, expr)
    B.draw_accessories(scene, ch, mode="bust")
    _rim_light(scene, ch)


def draw_full(scene, ch, expr="neutral"):
    F.draw_hair_back(scene, ch)
    F.draw_neck(scene, ch)
    B.draw_body(scene, ch, mode="full")
    _head_parts(scene, ch, expr)
    B.draw_accessories(scene, ch, mode="full")
    _rim_light(scene, ch)


# ------------------------------------------------------------------ 背景

def _radial(size, cx, cy, rx, ry, power=1.5):
    w, h = int(size[0]), int(size[1])
    sw, sh = 160, 160
    m = Image.new("L", (sw, sh), 0)
    p = m.load()
    for yy in range(sh):
        uy = (yy + 0.5) / sh
        for xx in range(sw):
            ux = (xx + 0.5) / sw
            d = math.hypot((ux - cx) / rx, (uy - cy) / ry)
            v = max(0.0, 1.0 - d)
            p[xx, yy] = int(round(255 * (v ** power)))
    return m.resize((w, h), Image.BICUBIC)


def _tint(img, mask, color, strength):
    a = mask.point(lambda v: int(v * strength / 255.0))
    tint = Image.new("RGBA", img.size, col(color, 255))
    tint.putalpha(a)
    img.alpha_composite(tint)


def make_studio_bg(size, ch):
    if ch.warm:
        c0, c1 = (31, 21, 19), (10, 8, 10)
    else:
        c0, c1 = (19, 23, 35), (6, 8, 13)
    img = vgradient((size, size), c0, c1).convert("RGBA")
    _tint(img, _radial((size, size), 0.50, 0.28, 0.60, 0.52, 1.7), ch.accent, 44)
    _tint(img, _radial((size, size), 0.50, 0.64, 0.58, 0.46, 2.0),
          lighten(ch.accent, 0.45), 20)
    _tint(img, _radial((size, size), 0.50, 0.06, 0.44, 0.26, 1.7), (255, 255, 255), 26)
    _tint(img, _radial((size, size), 0.50, 1.08, 0.62, 0.15, 1.8),
          lighten(ch.accent, 0.55), 22)
    vig = _radial((size, size), 0.50, 0.46, 0.82, 0.84, 1.2)
    inv = vig.point(lambda v: int((255 - v) * 0.58))
    dark = Image.new("RGBA", (size, size), (0, 0, 0, 255))
    dark.putalpha(inv)
    img.alpha_composite(dark)
    return img


# ------------------------------------------------------------------ 输出

def render_portrait(ch, size=768, background=False, expr="neutral"):
    ox, oy, s = _frame_bust(ch, size)
    bg = make_studio_bg(size, ch) if background else None
    scene = Scene(size, size, ox, oy, s, background=bg)
    draw_bust(scene, ch, expr)
    return scene.finish()


def render_expression(ch, expr, size=512):
    s = size * 0.258 * ch.head_scale
    ox = size * 0.5
    oy = size * 0.050 + 1.30 * s
    scene = Scene(size, size, ox, oy, s)
    draw_bust(scene, ch, expr)
    return scene.finish()


def render_fullbody(ch, height=720, expr="neutral"):
    s = height / 8.60
    w = int(round(3.95 * s))
    ox = w * 0.5
    oy = 1.52 * s
    scene = Scene(w, height, ox, oy, s)
    draw_full(scene, ch, expr)
    return scene.finish()


# ------------------------------------------------------------------ 设定图

def _sheet_bg(ch, W, H):
    if ch.warm:
        c0, c1 = (30, 22, 20), (10, 9, 11)
    else:
        c0, c1 = (19, 23, 35), (7, 9, 14)
    img = vgradient((W, H), c0, c1).convert("RGBA")
    _tint(img, _radial((W, H), 0.16, 0.10, 0.55, 0.75, 1.5), ch.accent, 28)
    _tint(img, _radial((W, H), 0.92, 0.92, 0.50, 0.55, 1.6),
          lighten(ch.accent, 0.30), 12)
    vig = _radial((W, H), 0.50, 0.45, 0.95, 0.98, 1.15)
    inv = vig.point(lambda v: int((255 - v) * 0.60))
    dark = Image.new("RGBA", (W, H), (0, 0, 0, 255))
    dark.putalpha(inv)
    img.alpha_composite(dark)
    return img


def _wrap_cn(scene, text, size, maxw, bold=False):
    text = str(text).replace(chr(10), " ")
    lines = []
    cur = ""
    for ch2 in text:
        if scene.text_width(cur + ch2, size, bold) > maxw and cur:
            lines.append(cur)
            cur = ch2
        else:
            cur += ch2
    if cur:
        lines.append(cur)
    return lines


def _paste_shadow(sc, cx, cy, rx, ry, alpha=120):
    w = max(4, int(rx * 2 * sc.ss))
    h = max(4, int(ry * 2 * sc.ss))
    m = _radial((w, h), 0.5, 0.5, 0.5, 0.5, 1.8)
    a = m.point(lambda v: int(v * alpha / 255.0))
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 255))
    layer.putalpha(a)
    sc.img.alpha_composite(layer, dest=(int((cx - rx) * sc.ss), int((cy - ry) * sc.ss)))


def _paste_img(sc, img, x, y):
    big = img.convert("RGBA")
    if big.width != int(big.width):
        pass
    target = (int(round(big.width * sc.ss)), int(round(big.height * sc.ss)))
    if big.size != target:
        big = big.resize(target, Image.LANCZOS)
    sc.img.alpha_composite(big, dest=(int(round(x * sc.ss)), int(round(y * sc.ss))))


def render_sheet(ch, size=(1600, 900)):
    W, H = size
    bg = _sheet_bg(ch, W, H)
    sc = Scene(W, H, 0, 0, 1.0, ss=2, background=bg)
    accent = ch.accent
    muted = "#98A1B4"
    white = "#F5F7FB"
    dim = "#5C6476"
    kind = str(ch.outfit.get("type", "suit"))

    # 背景装饰
    for i in range(10):
        y0 = 600 + i * 36
        sc.stroke([(-30, y0), (260, y0 + 210)], color=col(accent, 14), width=2)
    for i in range(4):
        x0 = 1030 + i * 26
        sc.stroke([(x0, 120), (x0 + 90, 890)], color=col(white, 10), width=1)

    # 顶部
    sc.text(44, 28, "造片局 · AI 微综艺", 30, accent, bold=True)
    sc.text(46, 68, "CHARACTER SHEET / 角色设定图 · 2D", 13, muted)
    sc.text(W - 44, 32, "ORIGINAL ANIME CHARACTER", 15, white, anchor="ra")
    sc.text(W - 44, 56, "PILLOW 程序化生成 · 离线可复现", 12, muted, anchor="ra")
    badge_text = "2D CEL" if ch.style == "2d_cel" else "GUOMAN"
    bw = sc.text_width(badge_text, 15, True) + 32
    sc.rrect(W - 44 - bw, 74, W - 44, 108, 11, fill=accent)
    sc.text(W - 44 - bw / 2.0, 91, badge_text, 15, "#141821", bold=True, anchor="mm")
    sc.stroke([(40, 120), (W - 40, 120)], color=col(accent, 130), width=1.4)

    # ---- 左：立绘
    px0, px1 = 40, 610
    sc.rrect(px0, 138, px1, 858, 18, fill=(255, 255, 255, 9),
             outline=col(accent, 62), width=1.4)
    sc.text(px0 + 22, 152, "立绘 / FULL BODY", 14, accent, bold=True)
    body_img = render_fullbody(ch, height=666)
    bx = px0 + (px1 - px0) / 2.0 - body_img.width / 2.0
    by = 846 - body_img.height
    _paste_shadow(sc, px0 + (px1 - px0) / 2.0, 842, 170, 26, alpha=130)
    _paste_img(sc, body_img, bx, by)
    sc.text(px0 + 22, 812, "HEAD : BODY = 1 : 2.9", 12, dim)
    sc.text(px1 - 22, 812, "RENDER 4X SSAA", 12, dim, anchor="ra")

    # ---- 中：表情
    mx0, mx1 = 632, 1092
    sc.rrect(mx0, 138, mx1, 858, 18, fill=(255, 255, 255, 9),
             outline=col(accent, 62), width=1.4)
    sc.text(mx0 + 22, 152, "表情 / EXPRESSIONS", 14, accent, bold=True)
    card_w, card_h = 198, 198
    spots = [(mx0 + 26, 190), (mx0 + 250, 190), (mx0 + 26, 448), (mx0 + 250, 448)]
    for (cx, cy), name in zip(spots, EXPR_NAMES):
        sc.rrect(cx, cy, cx + card_w, cy + card_h, 14, fill=(20, 26, 38, 255),
                 outline=col(accent, 72), width=1.4)
        thumb = render_expression(ch, name, size=512)
        inner = 180
        thumb = thumb.resize((inner, inner), Image.LANCZOS)
        _paste_img(sc, thumb, cx + (card_w - inner) / 2.0, cy + (card_h - inner) / 2.0)
        sc.text(cx + card_w / 2.0, cy + card_h + 22, EXPR_LABELS[name], 13, muted,
                anchor="ma")
    sc.stroke([(mx0 + 24, 418), (mx1 - 24, 418)], color=col(white, 22), width=1.2)

    # ---- 右：信息
    rx0, rx1 = 1114, 1560
    sc.rrect(rx0, 138, rx1, 858, 18, fill=(255, 255, 255, 9),
             outline=col(accent, 62), width=1.4)
    sc.text(rx0 + 24, 168, ch.name, 62, white, bold=True)
    sc.text(rx0 + 26, 242, ch.id.upper(), 14, accent)
    tw = sc.text_width(ch.title, 16, True) + 30
    sc.rrect(rx0 + 24, 268, rx0 + 24 + tw, 302, 10, fill=accent)
    sc.text(rx0 + 24 + tw / 2.0, 285, ch.title, 16, "#141821", bold=True, anchor="mm")
    yy = 322
    for ln in _wrap_cn(sc, ch.persona, 15, 386):
        sc.text(rx0 + 24, yy, ln, 15, muted)
        yy += 24
    # 信息表
    hair_label = HAIR_LABELS.get(ch.hair_style, ch.hair_style)
    outfit_label = OUTFIT_LABELS.get(kind, kind)
    accs = [ACC_LABELS.get(a, a) for a in (ch.accessories or []) if a != "none"]
    marks = [MARK_LABELS.get(m, m) for m in (ch.marks or [])]
    rows = [
        ("GENDER / AGE", ("FEMALE" if ch.is_female else "MALE") + " · " + str(ch.age)),
        ("STYLE / 风格", ch.style_label.split(" / ")[0]),
        ("HAIR / 发型", hair_label),
        ("OUTFIT / 服装", outfit_label),
        ("ACCESSORY / 配饰", " / ".join(accs) if accs else "无"),
        ("MARK / 标记", " / ".join(marks) if marks else "无"),
    ]
    ty = 396
    for label, value in rows:
        sc.text(rx0 + 24, ty, label, 11, dim)
        sc.text(rx0 + 168, ty - 1, value, 14, white)
        sc.stroke([(rx0 + 24, ty + 24), (rx1 - 24, ty + 24)], color=col(white, 16), width=1.0)
        ty += 34
    # 配色条
    sc.text(rx0 + 24, 630, "PALETTE / 配色", 12, accent, bold=True)
    pal = [
        ("ACCENT", ch.palette["accent"]),
        ("SKIN", ch.palette["skin"]),
        ("HAIR", ch.palette["hair"]),
        ("OUTFIT", ch.palette["outfit_main"]),
        ("SHIRT", ch.palette["outfit_shirt"]),
    ]
    sw = 60
    gap = 16
    for i, (nm, hx) in enumerate(pal):
        x = rx0 + 24 + i * (sw + gap)
        sc.rrect(x, 654, x + sw, 654 + sw, 9, fill=hx,
                 outline=col(white, 60), width=1.2)
        sc.text(x, 722, hx, 9, muted)
        sc.text(x, 735, nm, 9, dim)
    # 底部
    sc.stroke([(rx0 + 24, 772), (rx1 - 24, 772)], color=col(white, 22), width=1.2)
    sc.text(rx0 + 24, 786, "RENDERER", 11, dim)
    sc.text(rx0 + 24, 802, "studio2d · pillow · " + ch.style_label.split(" / ")[0], 13, white)
    sc.text(rx0 + 24, 826, "程序化绘制 · 非真人素材 · 可重复", 11, muted)

    # 底部条
    sc.rrect(40, 872, W - 40, 876, 2, fill=accent)
    sc.text(44, 882, "造片局 · AI 微综艺 / CHARACTER STUDIO · 2D STYLE", 10, dim)
    sc.text(W - 44, 882, "no photography / no external model / pure code", 10, dim, anchor="ra")
    return sc.finish()
