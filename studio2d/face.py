"""studio2d.face - 头部轮廓、五官（大眼/细眉/小鼻/嘴型）、发型与面部细节。"""

from __future__ import annotations

import math
import random
import zlib

from PIL import Image, ImageDraw, ImageChops

from .shapekit import (
    col, mix, lighten, darken, catmull, strand, taper, ribbon, star_points,
    mirror_loop, vgradient, ImageFont,
)

# 表情参数：eye=open/closed, wide=眼裂放大, squint=上眼睑下压, iris_dx/dy=视线
EXPR = {
    "neutral": {"eye": "open", "mouth": "smile"},
    "happy": {"eye": "closed", "mouth": "open_smile", "blush": 1.7, "brow_dy": -0.05},
    "surprised": {"eye": "open", "wide": 1.18, "squint": -0.10, "iris_scale": 0.90,
                  "pupil": 0.72, "brow_dy": -0.17, "mouth": "o", "blush": 0.8},
    "thinking": {"eye": "open", "squint": 0.26, "iris_dx": 0.075, "iris_dy": -0.012,
                 "brow_dy": -0.02, "brow_tilt": 0.10, "brow_asym": 0.09, "lift_side": 1,
                 "mouth": "flat"},
}


# ------------------------------------------------------------------ 头部基础

def head_outline(ch):
    c = ch.chibi
    w = 1.0 + 0.055 * c
    jaw = 0.64 + 0.14 * c
    chin = 0.15 + 0.06 * c
    right = [
        (0.0, -1.30), (0.34, -1.28), (0.66, -1.16), (0.88, -0.92), (0.98, -0.52),
        (w * 1.0, -0.10), (w * 0.97, 0.22), (0.86, 0.56), (jaw, 0.86),
        (jaw * 0.58, 1.06), (chin, 1.19), (0.0, 1.28),
    ]
    return mirror_loop(right)


def draw_neck(scene, ch):
    pts = [(-0.30, 1.02), (-0.33, 1.30), (-0.40, 1.58), (-0.47, 1.82), (-0.50, 1.98),
           (0.50, 1.98), (0.47, 1.82), (0.40, 1.58), (0.33, 1.30), (0.30, 1.02)]
    scene.poly(pts, fill=ch.skin, outline=ch.line, width=0.02, smooth=False)
    scene.glow(0.0, 1.40, 0.42, 0.19, ch.skin_sh, alpha=155, power=1.5)
    scene.stroke([(-0.15, 1.50), (-0.11, 1.76)], color=col(ch.skin_sh, 120), width=0.012)
    scene.stroke([(0.15, 1.50), (0.11, 1.76)], color=col(ch.skin_sh, 120), width=0.012)


def draw_ears(scene, ch):
    for side in (-1, 1):
        pts = [(side * 0.92, 0.00), (side * 1.06, -0.05), (side * 1.14, 0.10),
               (side * 1.10, 0.31), (side * 0.97, 0.40), (side * 0.92, 0.31)]
        scene.poly(pts, fill=ch.skin, outline=ch.line, width=0.02, smooth=True)
        inner = [(side * 0.99, 0.07), (side * 1.06, 0.10), (side * 1.08, 0.21), (side * 1.01, 0.29)]
        scene.stroke(inner, color=col(ch.skin_sh, 205), width=0.014, smooth=True)


def draw_face(scene, ch):
    outline = head_outline(ch)
    scene.poly(outline, fill=ch.skin, outline=ch.line, width=0.03, smooth=True, subdiv=10)
    return outline


def draw_face_paint(scene, ch, outline, par):
    mask = scene.mask(outline)
    blush = par.get("blush", 1.0)
    # 刘海投影
    layer = scene.layer()
    d = ImageDraw.Draw(layer, "RGBA")
    band = [(-1.15, -1.45), (1.15, -1.45), (1.15, -0.68), (0.55, -0.44),
            (0.0, -0.36), (-0.55, -0.44), (-1.15, -0.68)]
    d.polygon([scene.P(x, y) for x, y in catmull(band, 10, True)], fill=col(ch.skin_sh, 88))
    scene.clip(layer, mask)
    # 面中高光
    scene.glow(0.0, 0.02, 0.46, 0.62, ch.skin_hi, alpha=48, power=2.0)
    # 两颊
    for side in (-1, 1):
        scene.glow(side * 0.54, 0.57, 0.21, 0.10, ch.blush,
                   alpha=int(78 * blush), power=1.5)
    # 嘴下阴影
    scene.glow(0.0, 1.06, 0.20, 0.07, ch.skin_sh, alpha=55, power=1.8)
    # 年龄线（中年）
    if ch.age >= 40:
        for i, y in enumerate([-0.74, -0.84]):
            scene.stroke([(-0.24, y), (0.0, y - 0.02), (0.24, y)], color=col(ch.skin_sh, 95),
                         width=0.011, smooth=True)
        for side in (-1, 1):
            scene.stroke([(side * 0.20, 0.56), (side * 0.28, 0.72), (side * 0.26, 0.86)],
                         color=col(ch.skin_sh, 80), width=0.010, smooth=True)


# ------------------------------------------------------------------ 眼睛

def _eye_map(ch, side, par, x, y):
    s = ch.eye_scale
    wide = par.get("wide", 1.0)
    squint = par.get("squint", 0.0)
    yy = y * wide
    if y < 0:
        yy *= (1.0 - squint)
    return (side * (0.50 + x * s * wide), 0.140 + yy * s)


def _eye_lens(ch, side, par):
    base = [
        (-0.310, 0.045), (-0.285, -0.080), (-0.140, -0.180), (0.055, -0.205),
        (0.230, -0.140), (0.325, -0.015), (0.260, 0.100), (0.070, 0.170),
        (-0.120, 0.160), (-0.260, 0.100),
    ]
    return [_eye_map(ch, side, par, x, y) for x, y in base]


def _upper_lid(ch, side, par, extend=0.0):
    base = [(-0.315, 0.050), (-0.285, -0.085), (-0.140, -0.185),
            (0.060, -0.210), (0.230, -0.145), (0.330, -0.015)]
    pts = [_eye_map(ch, side, par, x, y) for x, y in base]
    if extend:
        x, y = _eye_map(ch, side, par, 0.330, -0.015)
        pts.append((x + side * extend * 0.85, y - extend * 0.62))
    return pts


def draw_eye(scene, ch, side, par):
    if par.get("eye") == "closed":
        _eye_closed(scene, ch, side, par)
        return
    lens_pts = _eye_lens(ch, side, par)
    xs = [p[0] for p in lens_pts]
    ys = [p[1] for p in lens_pts]
    pad_x = 0.24
    pad_y = 0.18
    x0, y0 = scene.P(min(xs) - pad_x, min(ys) - pad_y)
    x1, y1 = scene.P(max(xs) + pad_x, max(ys) + pad_y)
    ix0 = int(max(0, math.floor(x0)))
    iy0 = int(max(0, math.floor(y0)))
    ix1 = int(min(scene.W, math.ceil(x1)))
    iy1 = int(min(scene.H, math.ceil(y1)))
    lw, lh = ix1 - ix0, iy1 - iy0
    if lw < 6 or lh < 6:
        return

    def L(pts):
        return [(scene.P(px, py)[0] - ix0, scene.P(px, py)[1] - iy0) for px, py in pts]

    s = ch.eye_scale
    lens = L(catmull(lens_pts, 12, True))
    img = Image.new("RGBA", (lw, lh), (0, 0, 0, 0))
    d = ImageDraw.Draw(img, "RGBA")
    d.polygon(lens, fill=col(ch.eye_white))
    mask = Image.new("L", (lw, lh), 0)
    ImageDraw.Draw(mask).polygon(lens, fill=255)

    wide = par.get("wide", 1.0)
    icx = 0.50 * side + par.get("iris_dx", 0.0)
    icy = 0.135 + par.get("iris_dy", 0.0)
    iry = 0.198 * s * par.get("iris_scale", 1.0) * wide
    irx = iry * 0.80
    cxp, cyp = scene.P(icx, icy)
    lcx = cxp - ix0
    lcy = cyp - iy0
    rx = irx * scene.s
    ry = iry * scene.s
    box = [lcx - rx, lcy - ry, lcx + rx, lcy + ry]
    grad = vgradient((lw, lh), ch.eye_dark, ch.eye_light)
    iris = Image.new("RGBA", (lw, lh), (0, 0, 0, 0))
    emask = Image.new("L", (lw, lh), 0)
    ImageDraw.Draw(emask).ellipse(box, fill=255)
    iris.paste(grad, (0, 0), emask)
    idraw = ImageDraw.Draw(iris, "RGBA")
    idraw.ellipse(box, outline=col(ch.eye_dark), width=max(1, int(0.012 * scene.s)))
    pr = 0.072 * s * par.get("iris_scale", 1.0) * par.get("pupil", 1.0) * wide
    prx = pr * scene.s
    pry = prx * 1.20
    idraw.ellipse([lcx - prx, lcy - pry + 0.012 * scene.s, lcx + prx, lcy + pry],
                  fill=col(darken(ch.eye, 0.78)))
    idraw.arc([box[0] + 2, box[1] + 2, box[2] - 2, box[3] - 2], 32, 148,
              fill=col(lighten(ch.eye_light, 0.30), 195), width=max(1, int(0.017 * scene.s)))
    hx = lcx - 0.062 * scene.s * side
    hy = lcy - 0.080 * scene.s
    hr = 0.056 * scene.s
    idraw.ellipse([hx - hr, hy - hr * 1.05, hx + hr, hy + hr * 1.05], fill=(255, 255, 255, 246))
    h2x = lcx + 0.056 * scene.s * side
    h2y = lcy + 0.070 * scene.s
    h2 = 0.028 * scene.s
    idraw.ellipse([h2x - h2, h2y - h2, h2x + h2, h2y + h2], fill=(255, 255, 255, 215))
    h3 = 0.016 * scene.s
    idraw.ellipse([lcx + 0.030 * scene.s - h3, lcy - 0.132 * scene.s - h3,
                   lcx + 0.030 * scene.s + h3, lcy - 0.132 * scene.s + h3], fill=(255, 255, 255, 170))
    iris.putalpha(ImageChops.multiply(iris.getchannel("A"), mask))
    img.alpha_composite(iris)

    # 上眼睑投影
    sh_layer = Image.new("RGBA", (lw, lh), (0, 0, 0, 0))
    sd = ImageDraw.Draw(sh_layer, "RGBA")
    upper = L(_upper_lid(ch, side, par))
    sd.line(upper, fill=col(darken(ch.eye, 0.45), 110), width=max(1, int(0.085 * scene.s)),
            joint="curve")
    sh_layer.putalpha(ImageChops.multiply(sh_layer.getchannel("A"), mask))
    img.alpha_composite(sh_layer)

    # 睫毛（上眼睑 + 眼尾飞挑）
    lash_path = _upper_lid(ch, side, par, extend=0.16)
    prof = [(0.0, 0.018), (0.10, 0.070), (0.42, 0.072), (0.70, 0.056),
            (0.88, 0.030), (1.0, 0.002)]
    if ch.gender == "male":
        prof = [(0.0, 0.014), (0.10, 0.058), (0.45, 0.060), (0.75, 0.042),
                (0.90, 0.022), (1.0, 0.002)]
    lash = L(taper(lash_path, prof, n=26, smooth=True))
    d.polygon(lash, fill=col(ch.eye_lash))
    # 下眼睑
    low = [_eye_map(ch, side, par, x, y) for x, y in [(0.28, 0.085), (0.10, 0.155), (-0.10, 0.150)]]
    d.line(L(catmull(low, 8, False)), fill=col(ch.eye_lash, 170),
           width=max(1, int(0.016 * scene.s)), joint="curve")
    scene.paste(img, dest=(ix0, iy0))

    # 双眼皮褶皱（在局部图外重画，避免被裁）
    crease = [_eye_map(ch, side, par, x, y) for x, y in
              [(-0.20, -0.30), (-0.02, -0.35), (0.20, -0.30), (0.33, -0.20)]]
    scene.stroke(catmull(crease, 8, False), color=col(mix(ch.skin_sh, ch.line, 0.30), 150),
                 width=0.014)


def _eye_closed(scene, ch, side, par):
    s = ch.eye_scale
    path = [_eye_map(ch, side, par, x, y) for x, y in
            [(-0.30, 0.02), (-0.12, -0.09), (0.10, -0.13), (0.30, -0.03), (0.44, -0.13)]]
    prof = [(0.0, 0.014), (0.18, 0.058), (0.55, 0.062), (0.85, 0.038), (1.0, 0.002)]
    scene.poly(taper(path, prof, n=26, smooth=True), fill=ch.eye_lash, smooth=False)
    low = [_eye_map(ch, side, par, x, y) for x, y in
           [(0.24, 0.09), (0.02, 0.155), (-0.18, 0.135)]]
    scene.stroke(catmull(low, 8, False), color=col(ch.eye_lash, 150), width=0.013)
    for k in (0.16, 0.30):
        p = _eye_map(ch, side, par, k, -0.10)
        scene.stroke([p, (p[0] + side * 0.01, p[1] - 0.09)], color=col(ch.eye_lash, 200),
                     width=0.012)


def draw_brows(scene, ch, par):
    thick = ch.brow == "thick"
    for side in (-1, 1):
        dy = par.get("brow_dy", 0.0)
        tilt = par.get("brow_tilt", 0.0)
        asym = par.get("brow_asym", 0.0)
        if asym and par.get("lift_side") == side:
            dy -= asym
        s = ch.eye_scale
        inner = (side * (0.50 - 0.31 * s), -0.158 + dy + tilt * 0.55)
        mid = (side * (0.50 - 0.02 * s), -0.218 + dy - (0.03 if not thick else 0.0))
        outer = (side * (0.50 + 0.35 * s), -0.120 + dy - tilt * 0.55)
        if thick:
            prof = [(0.0, 0.012), (0.13, 0.100), (0.50, 0.082), (0.85, 0.042), (1.0, 0.002)]
        else:
            prof = [(0.0, 0.008), (0.16, 0.062), (0.52, 0.048), (0.86, 0.026), (1.0, 0.001)]
        scene.poly(taper([inner, mid, outer], prof, n=22, smooth=True), fill=ch.brow_color, smooth=False)


# ------------------------------------------------------------------ 鼻 / 嘴

def draw_nose(scene, ch):
    scene.poly([(0.008, 0.598), (0.096, 0.606), (0.048, 0.682)],
               fill=col(ch.skin_sh, 250), smooth=False)
    scene.stroke([(0.018, 0.688), (0.062, 0.704)], color=col(ch.skin_deep, 190), width=0.014)
    scene.glow(0.045, 0.570, 0.030, 0.030, ch.skin_hi, alpha=120, power=1.5)


def draw_mouth(scene, ch, kind):
    lip_line = col(ch.mouth_in, 240)
    if kind == "smile":
        w = 0.17 if "warm_smile" in ch.marks else 0.145
        pts = [(-w, 0.940), (0.0, 0.995), (w, 0.940)]
        scene.stroke(catmull(pts, 12, False), color=lip_line, width=0.030)
        scene.stroke([(-0.05, 1.027), (0.0, 1.033), (0.05, 1.027)],
                     color=col(lighten(ch.lip, 0.30), 175), width=0.016)
    elif kind == "open_smile":
        upper = [(-0.210, 0.910), (-0.100, 0.893), (0.0, 0.897),
                 (0.100, 0.893), (0.210, 0.910)]
        lower = [(0.155, 1.040), (0.0, 1.120), (-0.155, 1.040)]
        loop = catmull(upper + lower, 12, True)
        scene.poly(loop, fill=ch.mouth_in, outline=ch.line, width=0.016, smooth=False)
        scene.ellipse(0.0, 1.065, 0.105, 0.045, fill=ch.tongue)
        scene.poly([(-0.170, 0.917), (0.170, 0.917), (0.140, 0.973), (-0.140, 0.973)],
                   fill=ch.teeth, smooth=False)
        scene.stroke(catmull([(-0.24, 0.900), (-0.215, 0.930)], 6, False),
                     color=col(ch.skin_sh, 150), width=0.012)
        scene.stroke(catmull([(0.24, 0.900), (0.215, 0.930)], 6, False),
                     color=col(ch.skin_sh, 150), width=0.012)
    elif kind == "o":
        scene.ellipse(0.0, 0.985, 0.072, 0.100, fill=ch.mouth_in,
                      outline=ch.line, width=0.016)
        scene.ellipse(0.0, 1.030, 0.050, 0.040, fill=ch.tongue)
    elif kind == "flat":
        scene.stroke(catmull([(-0.150, 0.973), (0.0, 0.960), (0.140, 0.980)], 10, False),
                     color=lip_line, width=0.026)
        scene.stroke([(-0.165, 0.960), (-0.150, 0.973)], color=col(ch.skin_sh, 160), width=0.011)
    elif kind == "small":
        scene.stroke(catmull([(-0.085, 0.960), (0.0, 0.977), (0.085, 0.960)], 8, False),
                     color=lip_line, width=0.022)


# ------------------------------------------------------------------ 胡须 / 标记

def draw_facial_hair(scene, ch):
    fh = ch.facial_hair
    if fh in ("", "none", None):
        return
    if fh == "stubble":
        rnd = random.Random(zlib.crc32(ch.id.encode("utf-8")) & 0x7FFFFFFF)
        base = mix(ch.hair, ch.skin_sh, 0.30)
        scene.glow(-0.34, 1.03, 0.26, 0.11, ch.skin_sh, alpha=95, power=1.9)
        scene.glow(0.34, 1.03, 0.26, 0.11, ch.skin_sh, alpha=95, power=1.9)
        scene.glow(0.0, 1.16, 0.24, 0.10, ch.skin_sh, alpha=85, power=1.9)
        layer = scene.layer()
        ld = ImageDraw.Draw(layer, "RGBA")

        def dot(x, y, r, a):
            p = scene.P(x, y)
            rr = r * scene.s
            ld.ellipse([p[0] - rr, p[1] - rr * 0.8, p[0] + rr, p[1] + rr * 0.8],
                       fill=col(base, a))

        for _ in range(560):
            t = rnd.random()
            x = -0.56 + 1.12 * t
            y = 0.90 + 0.30 * (abs(x) / 0.56) ** 1.55 + 0.10 * rnd.random()
            if y > 1.205:
                continue
            if abs(x) < 0.215 and 0.88 < y < 1.075:
                continue
            dot(x, y, 0.0032 + 0.0042 * rnd.random(), 78 + int(60 * rnd.random()))
        for _ in range(150):
            x = -0.235 + 0.47 * rnd.random()
            y = 0.820 + 0.055 * rnd.random()
            dot(x, y, 0.0030 + 0.003 * rnd.random(), 70)
        for _ in range(170):
            x = -0.20 + 0.40 * rnd.random()
            y = 1.055 + 0.13 * rnd.random()
            dot(x, y, 0.0030 + 0.004 * rnd.random(), 72)
        scene.clip(layer, scene.mask(head_outline(ch)))
    elif fh == "goatee":
        beard = mix(ch.hair, (150.0, 150.0, 154.0), 0.36) if ch.age >= 48 else ch.hair
        scene.poly([(0.0, 1.020), (0.205, 1.085), (0.180, 1.225), (0.0, 1.280),
                    (-0.180, 1.225), (-0.205, 1.085)], fill=beard, outline=ch.line,
                   width=0.014, smooth=True)
        for side in (-1, 1):
            path = [(side * 0.020, 0.868), (side * 0.120, 0.842),
                    (side * 0.220, 0.858), (side * 0.262, 0.902)]
            scene.poly(taper(path, [(0.0, 0.030), (0.2, 0.090), (0.7, 0.070), (1.0, 0.0)], n=20),
                       fill=beard, smooth=False)
        scene.stroke([(0.0, 1.045), (0.0, 1.000)], color=beard, width=0.030)


def draw_marks(scene, ch):
    for m in ch.marks:
        if m == "warm_smile":
            for side in (-1, 1):
                scene.stroke([(side * 0.205, 0.913), (side * 0.245, 0.940),
                              (side * 0.232, 0.973)],
                             color=col(ch.skin_deep, 170), width=0.012, smooth=True)
        elif m == "scar_brow":
            scene.stroke([(-0.640, -0.250), (-0.505, -0.282)],
                         color=col(lighten(ch.skin, 0.55), 225), width=0.017)
            scene.stroke([(-0.575, -0.312), (-0.552, -0.194)],
                         color=col(lighten(ch.skin, 0.55), 205), width=0.013)


# ------------------------------------------------------------------ 发型

def _cap_points(ch):
    k = 1.05 if ch.is_female else 1.0
    v = 1.03 if ch.is_female else 1.0
    outer = [
        (1.12, 0.30), (1.20, -0.16), (1.15, -0.78), (0.96, -1.14), (0.64, -1.35),
        (0.28, -1.45), (0.0, -1.48), (-0.28, -1.45), (-0.64, -1.35), (-0.96, -1.14),
        (-1.15, -0.78), (-1.20, -0.16), (-1.12, 0.30),
    ]
    inner = [(-1.00, 0.05), (-0.74, -0.40), (-0.36, -0.60), (0.0, -0.66),
             (0.36, -0.60), (0.74, -0.40), (1.00, 0.05)]
    pts = [(x * k, y * v) for x, y in outer] + [(x * k, y * v) for x, y in inner]
    return pts, 13


def draw_cap(scene, ch):
    pts, n_outer = _cap_points(ch)
    scene.poly(pts, fill=ch.hair, smooth=True, subdiv=10)
    scene.stroke(pts[:n_outer], color=ch.line, width=0.030, smooth=True)
    # 靠近发际线的暗部
    scene.poly([(-0.98, 0.00), (-0.72, -0.44), (-0.34, -0.62), (0.0, -0.68),
                (0.34, -0.62), (0.72, -0.44), (0.98, 0.00), (0.86, 0.02),
                (0.50, -0.44), (0.0, -0.56), (-0.50, -0.44), (-0.86, 0.02)],
               fill=col(ch.hair_sh, 200), smooth=True, subdiv=8)


def _draw_strands(scene, ch, defs):
    for root_l, tip, root_r, bl, br in defs:
        poly = strand(root_l, tip, root_r, bl, br)
        scene.poly(poly, fill=ch.hair, outline=ch.line, width=0.022, smooth=False)


def _bangs_short_swept(ch):
    return [
        ((-0.98, -0.74), (-1.10, 0.18), (-0.74, -0.68), -0.06, 0.06),
        ((-0.72, -0.82), (-0.58, -0.24), (-0.38, -0.76), -0.04, 0.05),
        ((-0.44, -0.84), (-0.30, -0.17), (-0.06, -0.80), -0.04, 0.05),
        ((-0.12, -0.84), (0.08, -0.15), (0.28, -0.80), -0.04, 0.05),
        ((0.22, -0.82), (0.42, -0.21), (0.54, -0.78), -0.04, 0.05),
        ((0.52, -0.80), (0.72, -0.25), (0.82, -0.74), -0.03, 0.05),
        ((0.78, -0.74), (1.08, 0.14), (0.94, -0.78), 0.04, 0.06),
    ]


def _bangs_twin_tail(ch):
    return [
        ((-0.12, -0.80), (-0.90, 0.16), (-0.54, -0.74), -0.10, 0.07),
        ((-0.18, -0.82), (-0.42, 0.40), (-0.30, -0.78), -0.06, 0.06),
        ((0.18, -0.82), (0.42, 0.40), (0.30, -0.78), -0.05, 0.06),
        ((0.12, -0.80), (0.92, 0.14), (0.54, -0.74), 0.07, 0.10),
        ((-0.98, -0.58), (-1.18, 0.58), (-0.86, -0.52), -0.06, 0.06),
        ((0.86, -0.52), (1.20, 0.58), (0.98, -0.58), 0.06, 0.06),
        ((-0.05, -0.86), (0.03, 0.02), (0.09, -0.84), -0.02, 0.02),
        ((0.04, -0.88), (0.16, -0.10), (0.14, -0.84), 0.01, 0.02),
    ]


def _bangs_bob(ch):
    return [
        ((-0.98, -0.60), (-1.02, 0.34), (-0.72, -0.56), -0.03, 0.04),
        ((-0.72, -0.68), (-0.58, 0.10), (-0.30, -0.66), -0.03, 0.04),
        ((-0.34, -0.68), (-0.20, 0.14), (0.06, -0.66), -0.03, 0.04),
        ((0.02, -0.66), (0.20, 0.12), (0.36, -0.68), -0.03, 0.04),
        ((0.32, -0.66), (0.56, 0.08), (0.74, -0.68), -0.03, 0.04),
        ((0.74, -0.60), (1.02, 0.30), (0.98, -0.64), 0.04, 0.05),
    ]


def _bangs_spiky(ch):
    return [
        ((-0.92, -0.72), (-1.12, -0.10), (-0.66, -0.64), -0.04, 0.05),
        ((-0.66, -0.74), (-0.86, 0.10), (-0.30, -0.70), -0.05, 0.06),
        ((-0.28, -0.74), (-0.44, 0.24), (0.06, -0.70), -0.05, 0.05),
        ((0.08, -0.72), (0.30, 0.22), (0.46, -0.70), -0.04, 0.05),
        ((0.48, -0.70), (0.78, 0.10), (0.86, -0.70), 0.04, 0.05),
        ((0.86, -0.66), (1.10, -0.02), (0.94, -0.72), 0.04, 0.05),
        ((-0.60, -1.30), (-0.92, -1.78), (-0.32, -1.38), 0.03, 0.04),
        ((-0.10, -1.40), (-0.16, -1.90), (0.24, -1.42), 0.02, 0.03),
        ((0.40, -1.34), (0.76, -1.72), (0.66, -1.28), 0.03, 0.04),
    ]


def _bangs_long_straight(ch):
    return [
        ((-0.12, -0.82), (-0.78, 0.14), (-0.50, -0.76), -0.08, 0.06),
        ((-0.16, -0.84), (-0.30, 0.44), (-0.24, -0.80), -0.04, 0.04),
        ((0.16, -0.84), (0.30, 0.44), (0.24, -0.80), -0.04, 0.04),
        ((0.12, -0.82), (0.80, 0.12), (0.50, -0.76), 0.06, 0.08),
        ((-1.00, -0.60), (-1.16, 0.86), (-0.86, -0.52), -0.03, 0.04),
        ((0.86, -0.52), (1.18, 0.86), (1.00, -0.60), 0.04, 0.04),
    ]


def _bangs_ponytail(ch):
    return [
        ((-0.92, -0.70), (-1.06, 0.16), (-0.66, -0.64), -0.05, 0.05),
        ((-0.62, -0.72), (-0.80, 0.14), (-0.28, -0.68), -0.05, 0.06),
        ((-0.28, -0.72), (-0.38, 0.30), (0.10, -0.68), -0.05, 0.05),
        ((0.12, -0.70), (0.24, 0.26), (0.50, -0.68), -0.04, 0.05),
        ((0.52, -0.68), (0.78, 0.10), (0.86, -0.70), 0.04, 0.05),
        ((0.86, -0.64), (1.06, 0.18), (0.94, -0.72), 0.04, 0.05),
    ]


def _bangs_bun(ch):
    return [
        ((-0.10, -0.84), (-0.72, 0.12), (-0.46, -0.78), -0.07, 0.05),
        ((-0.14, -0.86), (-0.24, 0.34), (-0.18, -0.82), -0.04, 0.04),
        ((0.14, -0.86), (0.24, 0.34), (0.18, -0.82), -0.04, 0.04),
        ((0.10, -0.84), (0.72, 0.10), (0.46, -0.78), 0.05, 0.07),
        ((-0.98, -0.58), (-1.10, 0.52), (-0.84, -0.52), -0.04, 0.05),
        ((0.84, -0.52), (1.12, 0.52), (0.98, -0.58), 0.04, 0.05),
    ]


def _hair_highlight(scene, ch):
    path1 = [(-0.74, -0.86), (-0.44, -1.06), (-0.06, -1.15), (0.32, -1.10), (0.62, -0.92)]
    scene.poly(taper(path1, [(0.0, 0.003), (0.16, 0.052), (0.52, 0.044),
                             (0.84, 0.026), (1.0, 0.003)], n=30),
               fill=col(mix(ch.hair_hi, ch.hair, 0.25), 170), smooth=False)
    path2 = [(-0.56, -1.20), (-0.22, -1.31), (0.14, -1.28), (0.44, -1.16)]
    scene.poly(taper(path2, [(0.0, 0.002), (0.22, 0.024), (0.62, 0.021), (1.0, 0.002)], n=26),
               fill=col(mix(ch.hair_hi2, ch.hair, 0.30), 140), smooth=False)


def draw_hair_back(scene, ch):
    style = ch.hair_style
    if style == "twin_tail":
        for side in (-1, 1):
            path = [(side * 0.95, -0.56), (side * 1.34, 0.02), (side * 1.50, 0.96),
                    (side * 1.38, 1.86), (side * 1.16, 2.56)]
            widths = [0.44, 0.58, 0.54, 0.38, 0.10]
            scene.poly(ribbon(path, widths, smooth=True), fill=ch.hair,
                       outline=ch.line, width=0.026, smooth=False)
            inner = [(side * 1.02, -0.42), (side * 1.28, 0.10), (side * 1.38, 0.90),
                     (side * 1.28, 1.66)]
            scene.poly(ribbon(inner, [0.12, 0.20, 0.18, 0.10], smooth=True),
                       fill=col(ch.hair_sh, 200), smooth=False)
            scene.ellipse(side * 1.02, -0.30, 0.20, 0.115, fill=ch.outfit_accent,
                          outline=ch.line, width=0.016)
            scene.ellipse(side * 1.02, -0.30, 0.11, 0.06, fill=darken(ch.outfit_accent, 0.25))
    elif style == "long_straight":
        mass = [(1.06, -0.92), (1.42, 0.16), (1.52, 1.46), (1.46, 2.72),
                (1.24, 3.58), (0.72, 3.72), (0.0, 3.74), (-0.72, 3.72),
                (-1.24, 3.58), (-1.46, 2.72), (-1.52, 1.46), (-1.42, 0.16), (-1.06, -0.92)]
        scene.poly(mass, fill=ch.hair, outline=ch.line, width=0.028, smooth=True, subdiv=10)
        for side in (-1, 1):
            scene.poly([(side * 0.92, -0.72), (side * 1.22, 0.30), (side * 1.30, 1.60),
                        (side * 1.20, 2.70), (side * 1.30, 1.60), (side * 1.06, 0.30)][::-1],
                       fill=col(ch.hair_sh, 110), smooth=True)
    elif style == "bob":
        mass = [(1.08, -0.62), (1.30, 0.16), (1.28, 0.86), (1.04, 1.20),
                (0.52, 1.32), (0.0, 1.34), (-0.52, 1.32), (-1.04, 1.20),
                (-1.28, 0.86), (-1.30, 0.16), (-1.08, -0.62)]
        scene.poly(mass, fill=ch.hair, outline=ch.line, width=0.028, smooth=True, subdiv=10)
    elif style == "ponytail":
        path = [(0.42, -1.18), (1.10, -0.90), (1.62, -0.10), (1.74, 1.10),
                (1.56, 2.20), (1.34, 2.90)]
        widths = [0.34, 0.44, 0.46, 0.40, 0.26, 0.08]
        scene.poly(ribbon(path, widths, smooth=True), fill=ch.hair,
                   outline=ch.line, width=0.026, smooth=False)
    elif style == "bun":
        scene.ellipse(0.42, -1.46, 0.36, 0.34, fill=ch.hair, outline=ch.line, width=0.026)
        scene.ellipse(0.42, -1.46, 0.22, 0.20, fill=col(ch.hair_sh, 180))
        scene.ellipse(0.42, -1.10, 0.20, 0.10, fill=ch.outfit_accent,
                      outline=ch.line, width=0.014)


_FRINGE_LOCKS = {
    "side": [
        ((-0.98, -0.78), (-1.12, -0.10), (-0.68, -0.80), 0.04, 0.10),
        ((-0.74, -0.84), (-0.58, -0.22), (-0.38, -0.82), 0.03, 0.09),
        ((-0.46, -0.86), (-0.19, -0.13), (-0.06, -0.84), 0.03, 0.09),
        ((-0.18, -0.86), (0.16, -0.19), (0.32, -0.84), 0.03, 0.09),
        ((0.22, -0.84), (0.55, -0.12), (0.58, -0.82), 0.03, 0.09),
        ((0.56, -0.82), (0.94, -0.06), (0.86, -0.86), 0.04, 0.10),
    ],
    "center": [
        ((-0.96, -0.60), (-1.16, 0.16), (-0.80, -0.62), 0.04, 0.10),
        ((-0.82, -0.80), (-0.60, -0.20), (-0.42, -0.84), 0.03, 0.09),
        ((-0.48, -0.86), (-0.26, -0.14), (-0.10, -0.86), 0.03, 0.09),
        ((-0.16, -0.88), (-0.02, -0.30), (0.10, -0.88), 0.02, 0.06),
        ((0.14, -0.88), (0.26, -0.16), (0.46, -0.86), 0.03, 0.09),
        ((0.46, -0.86), (0.66, -0.22), (0.84, -0.80), 0.03, 0.09),
        ((0.82, -0.60), (1.20, 0.16), (0.98, -0.62), 0.04, 0.10),
    ],
    "bob": [
        ((-1.00, -0.58), (-1.06, 0.30), (-0.82, -0.60), 0.03, 0.07),
        ((-0.86, -0.78), (-0.66, -0.10), (-0.44, -0.82), 0.02, 0.06),
        ((-0.48, -0.84), (-0.26, -0.06), (-0.06, -0.84), 0.02, 0.06),
        ((-0.08, -0.84), (0.16, -0.08), (0.34, -0.84), 0.02, 0.06),
        ((0.36, -0.82), (0.60, -0.10), (0.80, -0.80), 0.02, 0.06),
        ((0.84, -0.60), (1.08, 0.28), (1.00, -0.58), 0.03, 0.07),
    ],
    "spiky": [
        ((-0.94, -0.76), (-1.16, -0.08), (-0.66, -0.80), 0.04, 0.10),
        ((-0.78, -0.82), (-0.62, -0.06), (-0.34, -0.82), 0.03, 0.09),
        ((-0.46, -0.84), (-0.24, 0.04), (-0.02, -0.82), 0.03, 0.09),
        ((-0.10, -0.84), (0.16, -0.04), (0.36, -0.82), 0.03, 0.09),
        ((0.28, -0.82), (0.58, 0.02), (0.64, -0.80), 0.03, 0.09),
        ((0.60, -0.80), (0.96, -0.10), (0.88, -0.84), 0.04, 0.10),
    ],
}


def _fringe_band(scene, ch):
    band = [(-1.18, -0.22), (-1.24, -0.62), (-1.04, -1.02), (-0.42, -0.98),
            (0.42, -0.98), (1.04, -1.02), (1.24, -0.62), (1.18, -0.22),
            (1.04, -0.46), (0.66, -0.55), (0.12, -0.58), (-0.48, -0.55), (-0.98, -0.44)]
    scene.poly(band, fill=ch.hair, smooth=True, subdiv=10)
_SIDE_LOCKS = {
    "short_swept": [
        ((-1.00, -0.76), (-1.12, 0.20), (-0.76, -0.68), 0.05, 0.11),
        ((0.80, -0.76), (1.10, 0.16), (0.96, -0.78), 0.05, 0.10),
    ],
    "twin_tail": [
        ((-0.98, -0.58), (-1.20, 0.58), (-0.86, -0.52), 0.04, 0.10),
        ((0.86, -0.52), (1.22, 0.58), (0.98, -0.58), 0.04, 0.10),
    ],
    "bob": [
        ((-0.98, -0.60), (-1.04, 0.34), (-0.74, -0.56), 0.04, 0.08),
        ((0.74, -0.56), (1.04, 0.30), (0.98, -0.62), 0.04, 0.08),
    ],
    "long_straight": [
        ((-1.00, -0.60), (-1.16, 0.88), (-0.86, -0.52), 0.03, 0.08),
        ((0.86, -0.52), (1.18, 0.88), (1.00, -0.60), 0.03, 0.08),
    ],
    "ponytail": [
        ((-1.00, -0.74), (-1.08, 0.18), (-0.76, -0.66), 0.05, 0.10),
        ((0.80, -0.74), (1.06, 0.16), (0.94, -0.76), 0.05, 0.10),
    ],
    "bun": [
        ((-0.98, -0.58), (-1.10, 0.52), (-0.84, -0.52), 0.04, 0.09),
        ((0.84, -0.52), (1.12, 0.52), (0.98, -0.58), 0.04, 0.09),
    ],
    "spiky": [
        ((-0.94, -0.72), (-1.14, -0.06), (-0.68, -0.64), 0.04, 0.10),
        ((0.88, -0.66), (1.10, 0.04), (0.96, -0.72), 0.04, 0.10),
        ((-0.60, -1.30), (-0.92, -1.78), (-0.32, -1.38), 0.03, 0.06),
        ((-0.10, -1.40), (-0.16, -1.90), (0.24, -1.42), 0.02, 0.05),
        ((0.40, -1.34), (0.76, -1.72), (0.66, -1.28), 0.03, 0.06),
    ],
}


def _fringe(scene, ch, style):
    if style == "bob":
        kind = "bob"
    elif style in ("twin_tail", "long_straight", "bun"):
        kind = "center"
    elif style == "spiky":
        kind = "spiky"
    else:
        kind = "side"
    _fringe_band(scene, ch)
    under = [(-1.08, -0.30), (-0.86, -0.34), (-0.58, -0.30), (-0.30, -0.36),
             (-0.02, -0.30), (0.26, -0.36), (0.54, -0.30), (0.82, -0.34),
             (1.06, -0.30), (0.98, -0.60), (0.40, -0.66), (-0.30, -0.66), (-0.92, -0.58)]
    scene.poly(under, fill=ch.hair, smooth=True, subdiv=8)
    locks = _FRINGE_LOCKS.get(kind, _FRINGE_LOCKS["side"])
    deep = mix(ch.hair, ch.hair_sh, 0.22)
    for i, (root_l, tip, root_r, bl, br) in enumerate(locks):
        tone = ch.hair if i % 2 == 0 else deep
        scene.poly(strand(root_l, tip, root_r, bl, br), fill=tone, smooth=False)
        if i % 2 == 1:
            scene.stroke([(root_l[0] * 0.92, -0.66), (tip[0] * 0.96, tip[1] + 0.05)],
                         color=col(ch.line, 95), width=0.008, smooth=True)


def _side_locks(scene, ch, style):
    for root_l, tip, root_r, bl, br in _SIDE_LOCKS.get(style, _SIDE_LOCKS["short_swept"]):
        scene.poly(strand(root_l, tip, root_r, bl, br), fill=ch.hair,
                   outline=ch.line, width=0.022, smooth=False)


def draw_hair_front(scene, ch):
    style = ch.hair_style
    if style == "bald":
        scene.glow(0.0, -0.92, 0.66, 0.30, ch.skin_hi, alpha=110, power=1.7)
        scene.glow(-0.18, -1.08, 0.24, 0.12, lighten(ch.skin, 0.45), alpha=130, power=1.4)
        side_hair = mix(ch.hair, (156.0, 158.0, 164.0), 0.30) if ch.age >= 48 else ch.hair
        for side in (-1, 1):
            pts = [(side * 0.96, -0.18), (side * 1.09, -0.06), (side * 1.09, 0.12),
                   (side * 0.99, 0.30), (side * 0.94, 0.22), (side * 1.01, 0.08),
                   (side * 0.98, -0.06), (side * 0.94, -0.14)]
            scene.poly(pts, fill=side_hair, outline=ch.line, width=0.016, smooth=True)
        return
    draw_cap(scene, ch)
    _fringe(scene, ch, style)
    _side_locks(scene, ch, style)
    _hair_highlight(scene, ch)
    if "star_clip" in ch.marks:
        pts = star_points(0.80, -0.94, 0.135, 0.058)
        scene.poly(pts, fill=ch.outfit_accent, outline=ch.line, width=0.016, smooth=False)
        scene.ellipse(0.76, -0.99, 0.022, 0.022, fill=col(lighten(ch.outfit_accent, 0.75), 220))
