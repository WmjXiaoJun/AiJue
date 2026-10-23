"""studio2d.body - 服装（suit / hoodie / jacket）、身体与配饰。"""

from __future__ import annotations

import math

from PIL import Image, ImageDraw

from .shapekit import (
    col, mix, lighten, darken, catmull, taper, ribbon, star_points, vgradient,
)


# ------------------------------------------------------------------ 躯干

def _bust_shape(ch):
    bw = 1.52 if ch.is_female else 1.80
    return [
        (0.40, 1.72), (0.80, 1.80), (1.22, 1.92), (bw * 0.88, 2.08), (bw, 2.34),
        (bw * 1.00, 3.60), (bw * 1.03, 5.60), (0.0, 5.60), (-bw * 1.03, 5.60),
        (-bw * 1.00, 3.60), (-bw, 2.34), (-bw * 0.88, 2.08), (-1.22, 1.92),
        (-0.80, 1.80), (-0.40, 1.72),
    ]


def _full_torso(ch):
    f = ch.is_female
    sh = 1.02 if f else 1.14
    chest = 1.08 if f else 1.18
    waist = 0.84 if f else 0.94
    hip = 0.98 if f else 1.02
    return [
        (0.36, 1.62), (0.72, 1.68), (sh * 0.86, 1.80), (sh, 1.98), (chest, 2.30),
        (chest * 0.98, 2.60), (waist, 2.92), (waist * 0.99, 3.06), (hip, 3.34),
        (hip, 3.66), (0.0, 3.68), (-hip, 3.66), (-hip, 3.34), (-waist * 0.99, 3.06),
        (-waist, 2.92), (-chest * 0.98, 2.60), (-chest, 2.30), (-sh, 1.98),
        (-sh * 0.86, 1.80), (-0.72, 1.68), (-0.36, 1.62),
    ]


def draw_body(scene, ch, mode="bust"):
    full = mode == "full"
    if full:
        _legs(scene, ch)
        torso = _full_torso(ch)
        hem = 3.62
    else:
        torso = _bust_shape(ch)
        hem = scene.units_bottom(0.15)
    scene.poly(torso, fill=ch.outfit_main, outline=ch.line, width=0.030,
               smooth=True, subdiv=10)
    kind = str(ch.outfit.get("type", "suit"))
    if kind == "hoodie":
        _hoodie(scene, ch, hem, full)
    elif kind == "jacket":
        _jacket(scene, ch, hem, full)
    else:
        _suit(scene, ch, hem, full)
    _sleeves(scene, ch, hem, full)
    _folds(scene, ch, hem, full)


# ------------------------------------------------------------------ 腿 / 鞋

def _legs(scene, ch):
    kind = str(ch.outfit.get("type", "suit"))
    pants = ch.pants
    for side in (-1, 1):
        hip = (side * 0.44, 3.46)
        knee = (side * 0.49, 5.06)
        ankle = (side * 0.46, 6.38)
        scene.poly(taper([hip, knee, ankle],
                         [(0.0, 0.74), (0.22, 0.70), (0.62, 0.52), (0.90, 0.41), (1.0, 0.38)],
                         n=24, smooth=True),
                   fill=pants, outline=ch.line, width=0.026, smooth=False)
        scene.stroke([(side * 0.52, 3.98), (side * 0.47, 5.00), (side * 0.48, 6.10)],
                     color=col(darken(pants, 0.26), 130), width=0.012, smooth=True)
        if kind == "hoodie":
            shoe = [(side * 0.14, 6.34), (side * 0.80, 6.30), (side * 0.88, 6.58),
                    (side * 0.76, 6.82), (side * 0.22, 6.84), (side * 0.08, 6.62)]
            scene.poly(shoe, fill="#F2F3F7", outline=ch.line, width=0.020, smooth=True)
            scene.poly([(side * 0.12, 6.56), (side * 0.82, 6.52), (side * 0.80, 6.72),
                        (side * 0.16, 6.74)], fill=ch.outfit_accent, smooth=False)
            scene.stroke([(side * 0.16, 6.74), (side * 0.80, 6.70)],
                         color=col("#D8DCE6", 255), width=0.020)
        else:
            shoe = [(side * 0.14, 6.34), (side * 0.78, 6.28), (side * 0.90, 6.60),
                    (side * 0.76, 6.82), (side * 0.24, 6.84), (side * 0.06, 6.64)]
            scene.poly(shoe, fill=ch.shoe, outline=ch.line, width=0.020, smooth=True)
            scene.stroke([(side * 0.18, 6.48), (side * 0.76, 6.42)],
                         color=col(lighten(ch.shoe, 0.34), 200), width=0.014)
            scene.stroke([(side * 0.20, 6.68), (side * 0.78, 6.64)],
                         color=col(darken(ch.shoe, 0.30), 220), width=0.012)
    scene.stroke([(0.0, 3.62), (0.0, 4.10)], color=col(darken(pants, 0.32), 150), width=0.014)


# ------------------------------------------------------------------ 三套服装

def _suit(scene, ch, hem, full):
    shirt = ch.outfit_shirt
    main = ch.outfit_main
    # 衬衫 V
    scene.poly([(0.28, 1.58), (0.02, 2.68), (-0.28, 1.58)],
               fill=shirt, outline=ch.line, width=0.018, smooth=False)
    # 领子
    for side in (-1, 1):
        collar = [(side * 0.04, 1.54), (side * 0.18, 1.48), (side * 0.32, 1.60),
                  (side * 0.18, 1.76), (side * 0.06, 1.64)]
        scene.poly(collar, fill=shirt, outline=ch.line, width=0.020, smooth=True)
        scene.stroke([(side * 0.12, 1.58), (side * 0.34, 1.82)],
                     color=col(ch.shirt_sh, 220), width=0.012, smooth=True)
    # 翻领
    for side in (-1, 1):
        lapel = [(side * 0.26, 1.58), (side * 0.86, 1.84), (side * 0.58, 2.03),
                 (side * 0.17, 2.70), (side * 0.02, 2.64)]
        scene.poly(lapel, fill=ch.outfit_sh, outline=ch.line, width=0.022, smooth=True)
        scene.stroke([(side * 0.13, 1.66), (side * 0.15, 2.08), (side * 0.05, 2.58)],
                     color=col(lighten(ch.outfit_main, 0.16), 150), width=0.009, smooth=True)
    # 领带
    tie = ch.outfit_accent
    scene.poly([(0.0, 1.92), (0.115, 2.02), (0.0, 2.17), (-0.115, 2.02)],
               fill=tie, outline=darken(tie, 0.40), width=0.015, smooth=True)
    scene.poly([(0.0, 2.15), (0.135, 2.30), (0.068, 3.02), (0.0, 3.12),
                (-0.068, 3.02), (-0.135, 2.30)],
               fill=tie, outline=darken(tie, 0.40), width=0.015, smooth=True)
    scene.stroke([(0.045, 2.42), (0.02, 3.00)], color=col(lighten(tie, 0.42), 210),
                 width=0.030, smooth=True)
    # 门襟与扣子
    scene.stroke([(0.06, 3.02), (0.10, min(hem, 5.30))],
                 color=col(ch.outfit_sh, 230), width=0.018, smooth=True)
    for by in (3.24, 3.86):
        if by < min(hem, 5.30) - 0.08:
            scene.ellipse(0.15, by, 0.048, 0.048, fill=col(darken(main, 0.42), 255),
                          outline=col(ch.line, 255), width=0.012)
            scene.ellipse(0.135, by - 0.015, 0.016, 0.016,
                          fill=col(lighten(main, 0.35), 220))
    # 口袋巾
    scene.poly([(-0.78, 2.52), (-0.58, 2.48), (-0.66, 2.66)],
               fill=tie, outline=darken(tie, 0.40), width=0.013, smooth=False)
    # 肩线
    for side in (-1, 1):
        scene.stroke([(side * 0.44, 1.92), (side * 1.10, 2.04), (side * 1.62, 2.18)],
                     color=col(ch.outfit_sh, 200), width=0.012, smooth=True)


def _hoodie(scene, ch, hem, full):
    main = ch.outfit_main
    # 帽兜（颈后）
    hood = [(0.66, 2.14), (0.62, 1.66), (0.46, 1.40), (0.20, 1.28), (0.0, 1.24),
            (-0.20, 1.28), (-0.46, 1.40), (-0.62, 1.66), (-0.66, 2.14),
            (-0.38, 2.08), (-0.24, 1.88), (-0.17, 1.70), (0.0, 1.64),
            (0.17, 1.70), (0.24, 1.88), (0.38, 2.08)]
    scene.poly(hood, fill=ch.outfit_sh, outline=ch.line, width=0.024, smooth=True)
    scene.stroke([(-0.30, 1.96), (0.0, 2.02), (0.30, 1.96)],
                 color=col(darken(main, 0.34), 180), width=0.012, smooth=True)
    # 白色内搭领口
    scene.poly([(-0.23, 1.57), (-0.02, 1.62), (0.23, 1.57), (0.17, 1.71),
                (0.02, 1.74), (-0.17, 1.71)],
               fill=ch.shirt, outline=ch.line, width=0.014, smooth=True)
    scene.stroke([(-0.16, 1.62), (0.0, 1.67), (0.16, 1.62)],
                 color=col(ch.shirt_sh, 220), width=0.010, smooth=True)
    # 抽绳
    for side in (-1, 1):
        path = [(side * 0.10, 1.94), (side * 0.15, 2.34), (side * 0.10, 2.82)]
        scene.stroke(catmull(path, 8, False), color=col(lighten(ch.outfit_accent, 0.12), 255),
                     width=0.026)
        scene.rrect(side * 0.10 - 0.040, 2.82, side * 0.10 + 0.040, 2.95, 0.018,
                    fill=ch.outfit_accent, outline=ch.line, width=0.010)
    # 前兜
    scene.stroke(catmull([(-0.70, 3.16), (-0.36, 3.00), (0.36, 3.00), (0.70, 3.16)], 10, False),
                 color=col(darken(main, 0.30), 255), width=0.024)
    scene.stroke(catmull([(-0.62, 3.12), (-0.32, 3.03), (0.32, 3.03), (0.62, 3.12)], 10, False),
                 color=col(lighten(main, 0.12), 130), width=0.011)
    # 胸前小星标
    if "star_clip" in ch.marks:
        pts = star_points(0.56, 2.56, 0.125, 0.055)
        scene.poly(pts, fill=ch.outfit_accent, outline=ch.line, width=0.014, smooth=False)
    # 下摆
    if full:
        scene.stroke([(-1.02, 3.56), (0.0, 3.60), (1.02, 3.56)],
                     color=col(darken(main, 0.34), 230), width=0.018, smooth=True)


def _jacket(scene, ch, hem, full):
    main = ch.outfit_main
    hb = min(hem, 5.30)
    # 内搭衬衫面板
    scene.poly([(-0.42, 1.58), (0.42, 1.58), (0.48, hb), (-0.48, hb)],
               fill=ch.shirt, outline=ch.line, width=0.018, smooth=False)
    scene.stroke([(-0.46, 2.2), (-0.48, hb - 0.1)], color=col(darken(ch.shirt, 0.18), 170),
                 width=0.010)
    # 拉链
    scene.stroke([(0.0, 1.62), (0.0, hb)], color=col(ch.outfit_accent, 255), width=0.022)
    yy = 1.78
    while yy < hb - 0.1:
        scene.ellipse(0.0, yy, 0.016, 0.016, fill=col(lighten(ch.outfit_accent, 0.5), 235))
        yy += 0.30
    # 立领
    for side in (-1, 1):
        collar = [(side * 0.24, 1.58), (side * 0.58, 1.42), (side * 0.76, 1.62),
                  (side * 0.44, 1.84), (side * 0.27, 1.72)]
        scene.poly(collar, fill=ch.outfit_sh, outline=ch.line, width=0.020, smooth=True)
        scene.stroke([(side * 0.56, 1.50), (side * 0.70, 1.66), (side * 0.46, 1.80)],
                     color=col(ch.outfit_accent, 255), width=0.015, smooth=True)
    # 肩部横条
    for side in (-1, 1):
        scene.stroke([(side * 0.60, 1.96), (side * 1.28, 2.06), (side * 1.62, 2.20)],
                     color=col(ch.outfit_accent, 235), width=0.042, smooth=True)
    # 斜插袋
    for side in (-1, 1):
        scene.stroke([(side * 0.86, 2.66), (side * 0.66, 2.60)],
                     color=col(lighten(ch.shirt, 0.30), 190), width=0.014)
    # 下摆
    if full:
        scene.stroke([(-1.04, 3.54), (0.0, 3.58), (1.04, 3.54)],
                     color=col(darken(main, 0.38), 235), width=0.018, smooth=True)


# ------------------------------------------------------------------ 袖 / 臂 / 衣褶

def _sleeves(scene, ch, hem, full):
    kind = str(ch.outfit.get("type", "suit"))
    if full:
        _arms(scene, ch, kind)
        return
    for side in (-1, 1):
        scene.stroke(catmull([(side * 1.06, 2.24), (side * 1.34, 2.46),
                              (side * 1.50, 3.20), (side * 1.54, min(hem, 5.20))], 10, False),
                     color=col(ch.line, 95), width=0.018)


def _arms(scene, ch, kind):
    fabric = ch.outfit_main
    for side in (-1, 1):
        sh = (side * 1.02, 1.90)
        el = (side * 1.30, 2.98)
        wr = (side * 1.24, 4.12)
        scene.poly(taper([sh, el, wr],
                         [(0.0, 0.58), (0.16, 0.60), (0.52, 0.48), (0.86, 0.40), (1.0, 0.36)],
                         n=26, smooth=True),
                   fill=fabric, outline=ch.line, width=0.026, smooth=False)
        # 袖口
        cuff_fill = ch.outfit_hi if kind == "suit" else ch.outfit_accent
        scene.rrect(side * 1.24 - 0.195, 4.00, side * 1.24 + 0.195, 4.20, 0.05,
                    fill=cuff_fill, outline=ch.line, width=0.012)
        # 袖子 accent 条
        if kind == "jacket":
            scene.stroke([(side * 1.16, 2.30), (side * 1.24, 3.20), (side * 1.18, 3.90)],
                         color=col(ch.outfit_accent, 235), width=0.030, smooth=True)
        # 手
        scene.poly(taper([wr, (side * 1.24, 4.58)],
                         [(0.0, 0.36), (0.5, 0.36), (1.0, 0.24)], n=12, smooth=True),
                   fill=ch.skin, outline=ch.line, width=0.020, smooth=False)
        scene.stroke([(side * 1.20, 4.30), (side * 1.28, 4.32)],
                     color=col(ch.skin_sh, 170), width=0.010)


def _folds(scene, ch, hem, full):
    kind = str(ch.outfit.get("type", "suit"))
    if kind == "suit":
        return
    limit = min(hem, 5.20)
    for i in range(2):
        y = 3.46 + i * 0.62
        if y > limit - 0.34:
            break
        for side in (-1, 1):
            scene.stroke([(side * (0.62 + 0.10 * i), y),
                          (side * (0.94 + 0.06 * i), y + 0.22)],
                         color=col(ch.outfit_sh, 120), width=0.013)


# ------------------------------------------------------------------ 配饰

def draw_accessories(scene, ch, mode="bust"):
    acc = ch.accessories or []
    if "glasses" in acc:
        _glasses(scene, ch)
    if "headphones" in acc:
        _headphones(scene, ch)
    if "watch" in acc and mode == "full":
        _watch(scene, ch)


def _glasses(scene, ch):
    frame = mix((142.0, 150.0, 164.0), ch.accent, 0.16)
    lens = col(mix(ch.eye_white, ch.accent, 0.06), 32)
    for side in (-1, 1):
        xa = side * 0.13
        xb = side * 0.90
        x0, x1 = min(xa, xb), max(xa, xb)
        scene.rrect(x0, -0.335, x1, 0.310, 0.15, fill=lens,
                    outline=frame, width=0.022)
        scene.stroke([(x0 + 0.09, -0.22), (x0 + 0.25, 0.01)],
                     color=(255, 255, 255, 80), width=0.024)
    scene.stroke([(-0.13, -0.035), (0.0, -0.06), (0.13, -0.035)],
                 color=frame, width=0.020, smooth=True)
    for side in (-1, 1):
        scene.stroke([(side * 0.90, -0.17), (side * 1.08, -0.13), (side * 1.14, 0.04)],
                     color=frame, width=0.018, smooth=True)


def _headphones(scene, ch):
    cup = mix(ch.outfit_main, (20.0, 24.0, 36.0), 0.55)
    ac = ch.outfit_accent
    band = [(-1.08, 0.14), (-1.02, -0.78), (-0.64, -1.26), (0.0, -1.44),
            (0.64, -1.26), (1.02, -0.78), (1.08, 0.14)]
    scene.stroke(catmull(band, 14, False), color=col(cup, 255), width=0.100)
    scene.stroke(catmull(band, 14, False), color=col(ac, 255), width=0.020)
    for side in (-1, 1):
        x = side * 1.12
        scene.rrect(x - 0.16, -0.10, x + 0.16, 0.34, 0.10,
                    fill=col(cup, 255), outline=ch.line, width=0.020)
        scene.rrect(x - 0.10, -0.02, x + 0.10, 0.26, 0.07, fill=col(ac, 255))
        scene.rrect(x - 0.062, 0.03, x + 0.062, 0.20, 0.05,
                    fill=col(lighten(cup, 0.20), 255))


def _watch(scene, ch):
    x, y = 1.20, 4.06
    scene.stroke([(x, y - 0.30), (x, y + 0.28)],
                 color=col(darken(ch.outfit_main, 0.42), 255), width=0.070)
    scene.ellipse(x, y, 0.115, 0.115, fill=col("#D9DEE8", 255),
                  outline=ch.line, width=0.016)
    scene.ellipse(x, y, 0.078, 0.078, fill=col(ch.outfit_accent, 255))
    scene.stroke([(x, y - 0.05), (x, y + 0.02)], color=col("#20242B", 255), width=0.011)
    scene.stroke([(x, y), (x + 0.036, y)], color=col("#20242B", 255), width=0.011)
