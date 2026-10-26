# -*- coding: utf-8 -*-
"""V5 现代风格 B 的扩展 primitive（6 风格专用绘制原语）。

风格：silk_arena（绢本设色）/ oil_salon（油画）/ baroque_candle（巴洛克烛光）/
      candy_collage（糖果拼贴）/ clay_diorama（黏土定格）/ chalk_theatre（粉笔剧场）。

契约：docs/V5_INTERFACES.md §1 + scene_render.SceneCtx
    def primitive(img: Image.Image, spec: dict, ctx: SceneCtx) -> None
    - img 是 RGBA 图层（ctx.w × ctx.h），就地绘制；
    - 坐标/尺寸一律 0-1 归一化：ctx.X/Y/PX/PY/S；
    - 颜色只从 style["palette"] 取：ctx.color(ref, fallback, alpha) / ctx.spec_color(spec, ...)；
    - 随机数用 ctx.rng_from(salt)，保证同 seed 可复现；
    - layer 归属由 scene JSON 的 "layer" 字段决定（本模块 JSON 已显式写）。

被 pipeline/scene_render.py 的 _load_extensions() 自动 import；import 时 register(name, fn)。
"""
from __future__ import annotations

import math
import random
from typing import Sequence

from PIL import Image, ImageChops, ImageDraw, ImageFilter

from .scene_render import (  # 引擎公开 helper（docs 契约）
    V5Error,
    brush_line,
    dashed_line,
    draw_text,
    glow_layer,
    hatch_layer,
    mask_layer,
    mix,
    register,
    shade,
    smooth_noise,
    soft_layer,
    vgradient,
)

TAU = math.pi * 2.0


# ===========================================================================
# 本地小工具
# ===========================================================================
def _f(spec: dict, key: str, default: float) -> float:
    try:
        return float(spec.get(key, default))
    except (TypeError, ValueError):
        return float(default)


def _i(spec: dict, key: str, default: int) -> int:
    try:
        return int(spec.get(key, default))
    except (TypeError, ValueError):
        return int(default)


def _rng(ctx, salt: str) -> random.Random:
    return ctx.rng_from(salt)


def _rgba(color, alpha: int = 255) -> tuple[int, int, int, int]:
    """任意颜色 + 额外 alpha（颜色本身 alpha 参与相乘）。"""
    if color is None:
        return (0, 0, 0, int(alpha))
    r, g, b = int(color[0]), int(color[1]), int(color[2])
    a = int(color[3]) if len(color) > 3 else 255
    return (r, g, b, max(0, min(255, int(a * alpha / 255.0))))


def _sh(color, factor: float) -> tuple[int, int, int, int]:
    return shade(color, factor)


def _roles(ctx, spec, key: str = "colors") -> list:
    """spec.colors（角色名/hex 列表）→ RGBA 列表；缺省用 accent/fg/highlight。"""
    raw = spec.get(key)
    if not isinstance(raw, (list, tuple)) or not raw:
        raw = ["accent", "accent:1", "highlight", "fg"]
    out = []
    for ref in raw:
        try:
            out.append(ctx.color(ref, fallback="accent"))
        except V5Error:
            continue
    return out or [ctx.color(None, fallback="accent")]


def _poly(draw, pts, fill, outline=None, width: int = 1) -> None:
    pts = [(float(x), float(y)) for x, y in pts]
    if len(pts) < 3:
        return
    draw.polygon(pts, fill=fill)
    if outline is not None and width > 0:
        draw.line(pts + [pts[0]], fill=outline, width=width, joint="curve")


def _rot(x: float, y: float, cx: float, cy: float, angle: float):
    ca, sa = math.cos(angle), math.sin(angle)
    dx, dy = x - cx, y - cy
    return (cx + dx * ca - dy * sa, cy + dx * sa + dy * ca)


def _rect_pts(x0, y0, x1, y1, angle=0.0):
    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    return [_rot(x, y, cx, cy, angle) for x, y in
            ((x0, y0), (x1, y0), (x1, y1), (x0, y1))]


def _blob(img, cx, cy, rx, ry, color, blur=0.0, alpha=255):
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer, "RGBA")
    d.ellipse((cx - rx, cy - ry, cx + rx, cy + ry), fill=_rgba(color, alpha))
    if blur:
        layer = layer.filter(ImageFilter.GaussianBlur(float(blur)))
    img.alpha_composite(layer)


def _outline_ellipse(draw, cx, cy, rx, ry, color, width=2):
    draw.ellipse((cx - rx, cy - ry, cx + rx, cy + ry),
                 outline=_rgba(color), width=max(1, int(width)))


def _sketch_line(draw, pts, color, width=2, passes=2, rng=None, jitter=1.2):
    """手绘/粉笔线：多遍抖动描边。"""
    pts = [(float(x), float(y)) for x, y in pts]
    for _ in range(max(1, int(passes))):
        jp = [(x + rng.uniform(-jitter, jitter), y + rng.uniform(-jitter, jitter))
              for x, y in pts] if rng is not None else pts
        brush_line(draw, jp, _rgba(color), width, taper=True)


def _tex_dots(img, color, rng, count=500, alpha=40, rmax=2.0):
    """撒点纹理（纸纹/粉笔灰/烛灰）。"""
    d = ImageDraw.Draw(img, "RGBA")
    w, h = img.size
    for _ in range(max(1, int(count))):
        x, y = rng.uniform(0, w), rng.uniform(0, h)
        r = rng.uniform(0.5, rmax)
        a = int(alpha * rng.uniform(0.3, 1.0))
        d.ellipse((x - r, y - r, x + r, y + r), fill=_rgba(color, a))


def _arrow_band(draw, x0, x1, y, color, thickness=3, steps=18, amp=4.0, rng=None):
    """一条略带起伏的粗线（拼贴条/缎带）。"""
    rng = rng or random.Random(1)
    phase = rng.uniform(0, TAU)
    pts = [(x0 + (x1 - x0) * t / steps,
            y + amp * math.sin(phase + t * 0.6)) for t in range(steps + 1)]
    brush_line(draw, pts, _rgba(color), thickness, taper=False)


# ===========================================================================
# 1. 绢本设色（silk_arena）
# ===========================================================================
@register("silk_weave")
def p_silk_weave(img: Image.Image, spec: dict, ctx) -> None:
    """绢面经纬织纹：细横纹 + 淡竖纹 + 随机丝结。"""
    w, h = ctx.w, ctx.h
    color = ctx.spec_color(spec, "color", "silk")
    rng = _rng(ctx, "silk_weave")
    d = ImageDraw.Draw(img, "RGBA")
    step = max(3, int(round(ctx.S(_f(spec, "spacing", 0.012)))))
    alpha = int(_f(spec, "alpha", 34))
    for y in range(0, h, step):
        d.line([(0, y), (w, y + rng.uniform(-1, 1))],
               fill=_rgba(color, alpha), width=1)
    for x in range(0, w, max(4, step * 2)):
        d.line([(x, 0), (x + rng.uniform(-1, 1), h)],
               fill=_rgba(color, max(8, alpha // 2)), width=1)
    _tex_dots(img, color, rng, count=max(200, w * h // 9000), alpha=max(6, alpha),
              rmax=1.6)


@register("gongbi_mountains")
def p_gongbi_mountains(img: Image.Image, spec: dict, ctx) -> None:
    """工笔远山：多层淡色山影 + 细线勾边（石青/赭石）+ 山脚雾带。"""
    w, h = ctx.w, ctx.h
    base_color = ctx.spec_color(spec, "color", "accent")
    count = max(1, _i(spec, "count", 3))
    opacity = max(0.1, min(1.0, _f(spec, "opacity", 0.6)))
    rng = _rng(ctx, "gongbi_mountains")
    draw = ImageDraw.Draw(img, "RGBA")
    paper = ctx.color("paper", fallback="bg")
    for layer in range(count):
        depth = (layer + 1) / count
        y_base = ctx.Y(0.46 + 0.13 * depth)
        amp = ctx.S(0.07 + 0.06 * depth)
        color = mix(base_color, paper, max(0.0, 0.62 - 0.42 * depth))
        pts = [(0.0, float(h))]
        peaks = 3 + layer * 2
        for k in range(peaks + 1):
            x = w * k / peaks
            y = y_base - amp * (0.35 + 0.65 * rng.random())
            pts.append((x, y))
        pts.append((float(w), float(h)))
        _poly(draw, pts, _rgba(color, int(170 * opacity)))
        edge = [(x, y) for x, y in pts[1:-1]]
        brush_line(draw, edge, _rgba(_sh(base_color, 0.62), int(235 * opacity)),
                   1.8 + depth * 1.2, taper=True)
        for _ in range(int(10 + 14 * depth)):
            x = rng.uniform(0, w)
            top_y = y_base - amp * rng.uniform(0.1, 0.9)
            draw.line([(x, top_y), (x + rng.uniform(-8, 8), top_y + rng.uniform(8, 26))],
                      fill=_rgba(_sh(base_color, 0.8), int(70 * opacity)), width=1)
    # 山脚雾带（把远山压进纸面）
    haze_y = ctx.Y(0.56)
    img.alpha_composite(glow_layer(
        (w, h), [(w * 0.5, haze_y, w * 0.65, ctx.S(0.06), 0.55)],
        paper, power=1.6, downscale=8))


@register("silk_ground_wash")
def p_silk_ground_wash(img: Image.Image, spec: dict, ctx) -> None:
    """绢本地面淡彩：底部水平色带 + 细横线，给人物一个台面。"""
    w, h = ctx.w, ctx.h
    color = ctx.spec_color(spec, "color", "azurite")
    paper = ctx.color("paper", fallback="bg")
    y = _f(spec, "y", 0.66)
    d = ImageDraw.Draw(img, "RGBA")
    top = ctx.Y(y)
    for i in range(3):
        band_top = top + (h - top) * (i / 3.0)
        band_bot = top + (h - top) * ((i + 1) / 3.0)
        tone = mix(color, paper, 0.55 + 0.15 * i)
        d.rectangle((0, band_top, w, band_bot), fill=_rgba(tone, 55 + 12 * i))
    for i in range(9):
        yy = top + (h - top) * (i + 0.5) / 9
        d.line([(0, yy), (w, yy)], fill=_rgba(_sh(color, 1.1), 60), width=1)
    d.line([(0, top), (w, top)], fill=_rgba(_sh(color, 0.55), 160), width=2)


@register("gongbi_clouds")
def p_gongbi_clouds(img: Image.Image, spec: dict, ctx) -> None:
    """工笔祥云：成组云头卷曲线 + 淡淡填色。"""
    w, h = ctx.w, ctx.h
    color = ctx.spec_color(spec, "color", "highlight")
    count = max(1, _i(spec, "count", 4))
    rng = _rng(ctx, "gongbi_clouds")
    draw = ImageDraw.Draw(img, "RGBA")
    for i in range(count):
        cx = ctx.X(0.1 + 0.8 * rng.random())
        cy = ctx.Y(_f(spec, "y", 0.30) + rng.uniform(-0.06, 0.10))
        r = ctx.S(0.035 + rng.uniform(0.0, 0.03))
        fill = _rgba(mix(color, ctx.color("paper", fallback="bg"), 0.55), 150)
        for k in range(3):
            rx = r * (1.0 + 0.45 * k)
            ry = r * (0.5 + 0.12 * k)
            draw.ellipse((cx - rx, cy - ry, cx + rx, cy + ry), fill=fill)
        # 云头卷线
        for k in range(4):
            ang = k * math.pi / 2 + rng.uniform(-0.2, 0.2)
            spiral = [(cx + r * 1.15 * math.cos(ang + t * 0.55),
                       cy + r * 0.62 * math.sin(ang + t * 0.55))
                      for t in (0.2, 0.7, 1.2, 1.7)]
            brush_line(draw, spiral, _rgba(color, 200), 1.4, taper=True)


@register("silk_banner")
def p_silk_banner(img: Image.Image, spec: dict, ctx) -> None:
    """绢本竖幡：幡面 + 描金内框 + 幡头横杆 + 下端流苏；count=2 时左右对称。"""
    color = ctx.spec_color(spec, "color", "accent")
    accent = ctx.color(spec.get("accent") or "accent:1", fallback="accent")
    x0, y0, x1, y1 = ctx.box(spec, defaults=(0.1, 0.16, 0.1, 0.6))
    count = max(1, min(2, _i(spec, "count", 1)))
    w, h = max(4.0, x1 - x0), max(6.0, y1 - y0)
    d = ImageDraw.Draw(img, "RGBA")
    xs = [x0]
    if count >= 2:
        xs.append(ctx.X(1.0 - _f(spec, "x", 0.1) - _f(spec, "w", 0.1)))
    for bx0 in xs:
        bx1 = bx0 + w
        _poly(d, [(bx0, y0), (bx1, y0), (bx1, y1), (bx0, y1)],
              _rgba(color, 225), outline=_rgba(_sh(color, 0.7)), width=2)
        inset = min(w, h) * 0.09
        d.rectangle((bx0 + inset, y0 + inset, bx1 - inset, y1 - inset),
                    outline=_rgba(accent, 210), width=max(1, int(ctx.S(0.004))))
        for k in range(5):
            cy = y0 + inset + (y1 - y0 - 2 * inset) * (k + 0.5) / 5.0
            d.ellipse((bx0 + w * 0.38, cy - w * 0.06,
                       bx0 + w * 0.62, cy + w * 0.06),
                      outline=_rgba(accent, 150), width=1)
        rod_h = max(3.0, h * 0.035)
        d.rounded_rectangle((bx0 - w * 0.12, y0 - rod_h, bx1 + w * 0.12, y0),
                            radius=rod_h * 0.5, fill=_rgba(accent, 240))
        for k in range(4):
            bx = bx0 + w * (0.15 + 0.23 * k)
            d.line([(bx, y1), (bx, y1 + h * 0.06)], fill=_rgba(accent, 210),
                   width=max(1, int(ctx.S(0.003))))
            d.ellipse((bx - 2, y1 + h * 0.055, bx + 2, y1 + h * 0.075),
                      fill=_rgba(accent, 230))


@register("silk_panels")
def p_silk_panels(img: Image.Image, spec: dict, ctx) -> None:
    """绢面屏风：count 块竖长绢面 + 木框 + 中缝阴影（双屏对垒）。"""
    color = ctx.spec_color(spec, "color", "paper")
    accent = ctx.color(spec.get("accent") or "shadow", fallback="shadow")
    count = max(1, min(3, _i(spec, "count", 2)))
    gap = max(2.0, _f(spec, "gap", 0.018) * ctx.scene_w)
    x0, y0, x1, y1 = ctx.box(spec, defaults=(0.16, 0.1, 0.68, 0.8))
    rng = _rng(ctx, "silk_panels")
    d = ImageDraw.Draw(img, "RGBA")
    total_w = max(8.0, x1 - x0)
    panel_w = (total_w - gap * (count - 1)) / count
    for k in range(count):
        px0 = x0 + k * (panel_w + gap)
        px1 = px0 + panel_w
        # 投影
        d.rectangle((px0 + 4, y0 + 6, px1 + 6, y1 + 8), fill=_rgba(accent, 70))
        # 绢面
        d.rectangle((px0, y0, px1, y1), fill=_rgba(color, 252))
        # 木框
        fw = max(2, int(panel_w * 0.018))
        d.rectangle((px0, y0, px1, y1), outline=_rgba(accent, 240), width=fw)
        d.rectangle((px0 + fw, y0 + fw, px1 - fw, y1 - fw),
                    outline=_rgba(_sh(accent, 1.3), 150), width=1)
        # 绢面内框压边（避免斜纹造成摩尔纹，纹理交给 apply_finish）
        d.rectangle((px0 + fw * 2, y0 + fw * 2, px1 - fw * 2, y1 - fw * 2),
                    outline=_rgba(_sh(color, 0.9), 40), width=1)
    # 中缝阴影
    for k in range(count - 1):
        gx = x0 + (k + 1) * panel_w + k * gap + gap / 2
        d.line([(gx, y0), (gx, y1)], fill=_rgba(accent, 60),
               width=max(2, int(gap * 0.35)))


@register("ink_circle_stage")
def p_ink_circle_stage(img: Image.Image, spec: dict, ctx) -> None:
    """墨圈舞台：淡墨圆台 + 多层墨环（飞白边） + 周围墨点飞溅。"""
    w, h = ctx.w, ctx.h
    color = ctx.spec_color(spec, "color", "ink")
    cx_ = ctx.X(_f(spec, "x", 0.5))
    cy_ = ctx.Y(_f(spec, "y", 0.8))
    r = ctx.S(_f(spec, "r", 0.3))
    ry = r * _f(spec, "flatten", 0.34)
    opacity = max(0.05, min(1.0, _f(spec, "opacity", 0.34)))
    rng = _rng(ctx, "ink_circle")
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer, "RGBA")
    d.ellipse((cx_ - r, cy_ - ry, cx_ + r, cy_ + ry),
              fill=_rgba(color, int(120 * opacity)))
    for k in range(4):
        rr = r * (1.0 - 0.16 * k)
        rry = ry * (1.0 - 0.16 * k)
        for _ in range(3):
            jx = rng.uniform(-r * 0.012, r * 0.012)
            jy = rng.uniform(-ry * 0.03, ry * 0.03)
            d.ellipse((cx_ - rr + jx, cy_ - rry + jy, cx_ + rr + jx, cy_ + rry + jy),
                      outline=_rgba(color, int((200 - 30 * k) * opacity)),
                      width=max(2, int(ctx.S(0.0035) * (1.2 - 0.1 * k))))
    for _ in range(5):
        a0 = rng.uniform(0, 360)
        d.arc((cx_ - r * 0.8, cy_ - ry * 0.8, cx_ + r * 0.8, cy_ + ry * 0.8),
              a0, a0 + rng.uniform(30, 90),
              fill=_rgba(color, int(120 * opacity)), width=max(1, int(ctx.S(0.002))))
    for _ in range(30):
        ang = rng.uniform(0, TAU)
        px = cx_ + r * rng.uniform(0.95, 1.12) * math.cos(ang)
        py = cy_ + ry * rng.uniform(0.95, 1.15) * math.sin(ang)
        rr = rng.uniform(1.0, 5.0)
        d.ellipse((px - rr, py - rr, px + rr, py + rr),
                  fill=_rgba(color, int(110 * opacity)))
    img.alpha_composite(layer.filter(ImageFilter.GaussianBlur(max(0.4, ctx.S(0.0012)))))

@register("ink_crowd")
def p_ink_crowd(img: Image.Image, spec: dict, ctx) -> None:
    """墨色观众剪影：rows 排、per_row 人，头肩轮廓 + 肩线连接。"""
    w, h = ctx.w, ctx.h
    color = ctx.spec_color(spec, "color", "fg")
    rows = max(1, _i(spec, "rows", 2))
    per_row = max(2, _i(spec, "per_row", 9))
    y_start = _f(spec, "y_start", 0.88)
    opacity = max(0.05, min(1.0, _f(spec, "opacity", 0.28)))
    rng = _rng(ctx, "ink_crowd")
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer, "RGBA")
    for row in range(rows):
        y = ctx.Y(y_start - 0.075 * (rows - 1 - row))
        scale = ctx.S(0.075 + 0.012 * row)
        for k in range(per_row):
            x = ctx.X((k + 0.5) / per_row + rng.uniform(-0.012, 0.012))
            head_r = scale * 0.34
            shoulder_w = scale * 0.52
            body_h = scale * 0.72
            a = int(255 * opacity * rng.uniform(0.75, 1.0))
            d.ellipse((x - head_r, y - body_h - head_r * 2,
                       x + head_r, y - body_h), fill=_rgba(color, a))
            d.pieslice((x - shoulder_w, y - body_h * 1.05,
                        x + shoulder_w, y + body_h * 0.6),
                       180, 360, fill=_rgba(color, a))
    img.alpha_composite(layer.filter(ImageFilter.GaussianBlur(max(0.6, ctx.S(0.0025)))))


@register("scroll_rods")
def p_scroll_rods(img: Image.Image, spec: dict, ctx) -> None:
    """卷轴天杆/地杆 + 两端轴头（绢本场景收边）。"""
    w, h = ctx.w, ctx.h
    color = ctx.spec_color(spec, "color", "shadow")
    accent = ctx.color(spec.get("accent") or "accent:1", fallback="accent")
    d = ImageDraw.Draw(img, "RGBA")
    rod_h = max(3.0, ctx.S(0.014))
    margin = ctx.S(0.012)
    for y in (margin, h - margin - rod_h):
        d.rounded_rectangle((margin, y, w - margin, y + rod_h),
                            radius=rod_h * 0.5, fill=_rgba(color, 235))
        for x in (margin, w - margin):
            r = rod_h * 1.15
            d.ellipse((x - r, y + rod_h / 2 - r, x + r, y + rod_h / 2 + r),
                      fill=_rgba(accent, 235))
    # 卷边阴影
    shadow = max(2.0, rod_h * 0.9)
    d.rectangle((0, margin + rod_h, w, margin + rod_h + shadow),
                fill=_rgba(color, 40))
    d.rectangle((0, h - margin - rod_h - shadow, w, h - margin - rod_h),
                fill=_rgba(color, 40))


@register("flying_white")
def p_flying_white(img: Image.Image, spec: dict, ctx) -> None:
    """飞白：一道锥形枯笔横扫，用蒙版打孔模拟枯笔断续（分屏中缝/气势线）。"""
    w, h = ctx.w, ctx.h
    color = ctx.spec_color(spec, "color", "fg")
    opacity = max(0.05, min(1.0, _f(spec, "opacity", 0.5)))
    angle = math.radians(_f(spec, "angle", 0.0))
    length = _f(spec, "length", 1.0) * math.hypot(w, h)
    cx_ = ctx.X(_f(spec, "x", 0.5))
    cy_ = ctx.Y(_f(spec, "y", 0.5))
    half = ctx.S(_f(spec, "thickness", 0.03))
    rng = _rng(ctx, "flying_white")
    dx, dy = math.cos(angle), math.sin(angle)
    nx, ny = -dy, dx
    mask = Image.new("L", (w, h), 0)
    md = ImageDraw.Draw(mask)
    steps = 48
    left, right = [], []
    for i in range(steps + 1):
        t = i / steps
        along = -0.5 + t
        taper = math.sin(math.pi * t) ** 0.55
        cxp = cx_ + dx * length * along
        cyp = cy_ + dy * length * along
        left.append((cxp + nx * half * taper, cyp + ny * half * taper))
        right.append((cxp - nx * half * taper, cyp - ny * half * taper))
    md.polygon(left + list(reversed(right)), fill=int(230 * opacity))
    # 枯笔打孔：沿笔锋随机缝隙
    for _ in range(90):
        t = rng.uniform(0.02, 0.98)
        cxp = cx_ + dx * length * (t - 0.5)
        cyp = cy_ + dy * length * (t - 0.5)
        taper = math.sin(math.pi * t) ** 0.55
        md.line([(cxp + nx * half * taper, cyp + ny * half * taper),
                 (cxp - nx * half * taper, cyp - ny * half * taper)],
                fill=0, width=max(1, int(ctx.S(rng.uniform(0.001, 0.004)))))
    # 笔尖缺墨
    for i in range(10):
        t = i / 10.0
        cxp = cx_ + dx * length * (0.5 - t * 0.16)
        cyp = cy_ + dy * length * (0.5 - t * 0.16)
        rr = ctx.S(0.004)
        md.ellipse((cxp - rr, cyp - rr, cxp + rr, cyp + rr), fill=0)
    img.alpha_composite(mask_layer((w, h), mask, color))

@register("oil_strokes")
def p_oil_strokes(img: Image.Image, spec: dict, ctx) -> None:
    """厚涂笔触：短粗笔触 + 高光边，营造画布肌理（可铺底或叠前景）。"""
    w, h = ctx.w, ctx.h
    colors = _roles(ctx, spec)
    count = max(8, _i(spec, "count", 80))
    opacity = max(0.05, min(1.0, _f(spec, "opacity", 0.3)))
    rng = _rng(ctx, "oil_strokes")
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer, "RGBA")
    for _ in range(count):
        x, y = rng.uniform(0, w), rng.uniform(0, h)
        length = rng.uniform(0.02, 0.09) * min(w, h)
        ang = rng.uniform(-0.5, 0.5) + (math.pi / 2 if rng.random() < 0.35 else 0)
        dx, dy = math.cos(ang) * length / 2, math.sin(ang) * length / 2
        base = colors[rng.randrange(len(colors))]
        thick = max(2, int(rng.uniform(0.004, 0.014) * min(w, h)))
        a = int(70 * opacity * rng.uniform(0.35, 1.0))
        d.line([(x - dx, y - dy), (x + dx, y + dy)], fill=_rgba(base, a), width=thick)
        hi = shade(base, 1.5)
        d.line([(x - dx * 0.8 + 1, y - dy * 0.8 - 1), (x + dx * 0.8 + 1, y + dy * 0.8 - 1)],
               fill=_rgba(hi, int(a * 0.55)), width=max(1, thick // 3))
        d.line([(x - dx * 0.9, y + thick * 0.35), (x + dx * 0.9, y + thick * 0.35)],
               fill=_rgba(_sh(base, 0.55), int(a * 0.5)), width=max(1, thick // 2))
    img.alpha_composite(layer)


@register("drapery")
def p_drapery(img: Image.Image, spec: dict, ctx) -> None:
    """天鹅绒帷幔：竖向褶皱、明暗交替、顶部垂幔与流苏。"""
    w, h = ctx.w, ctx.h
    color = ctx.spec_color(spec, "color", "accent")
    accent = ctx.color(spec.get("accent") or "accent:1", fallback="accent")
    x0, y0, x1, y1 = ctx.box(spec, defaults=(0.0, 0.0, 0.18, 0.98))
    folds = max(2, _i(spec, "folds", 5))
    rng = _rng(ctx, "drapery")
    d = ImageDraw.Draw(img, "RGBA")
    width = max(4.0, x1 - x0)
    # 主体
    d.rectangle((x0, y0, x1, y1), fill=_rgba(color, 245))
    # 顶部垂幔（波状）
    swag = [((x0 + width * t / 10), y0 + math.sin(t * math.pi) * (y1 - y0) * 0.035)
            for t in range(11)]
    brush_line(d, swag, _rgba(accent, 220), max(2, ctx.S(0.004)))
    # 竖向褶皱
    for k in range(folds):
        fx = x0 + width * (k + 0.5) / folds
        wav = [(fx + math.sin(t * 0.9 + k) * width * 0.02,
                y0 + (y1 - y0) * t / 12) for t in range(13)]
        light = shade(color, 1.45)
        dark = shade(color, 0.6)
        brush_line(d, wav, _rgba(light, 150), max(2, ctx.S(0.007)), taper=True)
        brush_line(d, [(x + ctx.S(0.012), y) for x, y in wav],
                   _rgba(dark, 140), max(2, ctx.S(0.009)), taper=True)
    # 底部流苏
    tip_y = y1 - (y1 - y0) * 0.02
    for k in range(folds * 3):
        fx = x0 + width * (k + 0.5) / (folds * 3)
        d.line([(fx, tip_y), (fx, tip_y + ctx.S(0.022))],
               fill=_rgba(accent, 200), width=max(1, int(ctx.S(0.002))))


@register("candelabra")
def p_candelabra(img: Image.Image, spec: dict, ctx) -> None:
    """烛台：金属座 + 蜡烛 + 火苗 + 径向暖光。"""
    w, h = ctx.w, ctx.h
    color = ctx.spec_color(spec, "color", "accent")
    candle = ctx.color(spec.get("candle") or "highlight", fallback="highlight")
    cx_ = ctx.X(_f(spec, "x", 0.5))
    cy_ = ctx.Y(_f(spec, "y", 0.3))
    scale = ctx.S(_f(spec, "scale", 0.25))
    count = max(1, min(9, _i(spec, "candles", 5)))
    glow = max(0.0, min(1.0, _f(spec, "glow", 0.5)))
    d = ImageDraw.Draw(img, "RGBA")
    # 座杆与底盘
    d.line([(cx_, cy_), (cx_, cy_ + scale * 1.25)], fill=_rgba(shade(color, 0.55), 240),
           width=max(2, int(scale * 0.07)))
    d.ellipse((cx_ - scale * 0.28, cy_ + scale * 1.18,
               cx_ + scale * 0.28, cy_ + scale * 1.32),
              fill=_rgba(shade(color, 0.65), 240))
    arm_w = scale * 0.55
    d.line([(cx_ - arm_w, cy_ + scale * 0.25), (cx_ + arm_w, cy_ + scale * 0.25)],
           fill=_rgba(color, 240), width=max(2, int(scale * 0.055)))
    flames = []
    for k in range(count):
        t = (k + 0.5) / count
        bx = cx_ - arm_w + arm_w * 2 * t
        by = cy_ + scale * 0.25 - abs(t - 0.5) * scale * 0.35
        ch = scale * (0.34 + 0.16 * math.sin(math.pi * t))
        d.line([(bx, by), (bx, by - ch)], fill=_rgba(candle, 250),
               width=max(2, int(scale * 0.045)))
        # 火苗
        fy = by - ch - scale * 0.03
        d.polygon([(bx, fy - scale * 0.10), (bx - scale * 0.035, fy + scale * 0.02),
                   (bx + scale * 0.035, fy + scale * 0.02)],
                  fill=_rgba(ctx.color("highlight", fallback="highlight"), 240))
        flames.append((bx, fy, scale * 0.22, scale * 0.32, 0.7 + 0.3 * glow))
    if glow > 0:
        halo = glow_layer((w, h), flames,
                          ctx.color("highlight", fallback="accent"),
                          power=2.2, downscale=6, blur=2)
        img.alpha_composite(halo)


@register("oval_roundtable")
def p_oval_roundtable(img: Image.Image, spec: dict, ctx) -> None:
    """透视椭圆圆桌：桌面 + 桌沿厚度 + 桌布垂边 + 高光。"""
    w, h = ctx.w, ctx.h
    color = ctx.spec_color(spec, "color", "accent")
    accent = ctx.color(spec.get("accent") or "accent:1", fallback="accent")
    cx_ = ctx.X(_f(spec, "cx", 0.5))
    cy_ = ctx.Y(_f(spec, "cy", 0.8))
    rx = ctx.S(_f(spec, "rx", 0.32))
    ry = ctx.S(_f(spec, "ry", 0.11))
    cloth = bool(spec.get("cloth", True))
    d = ImageDraw.Draw(img, "RGBA")
    # 桌沿厚度
    d.ellipse((cx_ - rx, cy_ - ry, cx_ + rx, cy_ + ry),
              fill=_rgba(_sh(color, 0.8), 245))
    d.ellipse((cx_ - rx, cy_ - ry * 1.45, cx_ + rx, cy_ + ry * 0.55),
              fill=_rgba(shade(color, 1.12), 245))
    # 桌面高光 + 描金桌沿（无桌布时作为石桌轮廓光）
    d.arc((cx_ - rx * 0.86, cy_ - ry * 1.3, cx_ + rx * 0.86, cy_ + ry * 0.4),
          195, 345, fill=_rgba(shade(color, 1.5), 200),
          width=max(2, int(ctx.S(0.005))))
    d.arc((cx_ - rx, cy_ - ry * 1.45, cx_ + rx, cy_ + ry * 0.55),
          185, 355, fill=_rgba(accent, 200), width=max(2, int(ctx.S(0.006))))
    d.arc((cx_ - rx * 0.98, cy_ - ry * 0.35, cx_ + rx * 0.98, cy_ + ry * 1.2),
          10, 170, fill=_rgba(_sh(color, 0.55), 210), width=max(2, int(ctx.S(0.006))))
    if cloth:
        drape = [(cx_ - rx * (1 - 0.5 * math.sin(t * math.pi / 6)), cy_ + ry * 0.6)
                 for t in range(7)]
        pts = drape + [(cx_ + rx, cy_ + ry * 1.35),
                       (cx_ - rx, cy_ + ry * 1.35)]
        _poly(d, pts, _rgba(accent, 200),
              outline=_rgba(_sh(accent, 0.7), 220), width=2)
        for k in range(4):
            fx = cx_ - rx + rx * 2 * (k + 0.5) / 4
            d.line([(fx, cy_ + ry * 0.7), (fx - rx * 0.04, cy_ + ry * 1.32)],
                   fill=_rgba(shade(accent, 0.72), 160), width=2)
    # 桌上烛光斑
    img.alpha_composite(glow_layer(
        (w, h), [(cx_, cy_ - ry * 0.4, rx * 0.6, ry * 0.9, 0.35)],
        ctx.color("highlight", fallback="accent"), power=2.4, downscale=8))


@register("gold_frame")
def p_gold_frame(img: Image.Image, spec: dict, ctx) -> None:
    """鎏金画框：金边 + 内衬 + 四角涡卷纹。"""
    w, h = ctx.w, ctx.h
    color = ctx.spec_color(spec, "color", "accent")
    accent = ctx.color(spec.get("accent") or "accent:1", fallback="accent")
    border = max(2.0, _f(spec, "width", 0.03) * min(w, h))
    d = ImageDraw.Draw(img, "RGBA")
    outer = [(0, 0), (w, 0), (w, h), (0, h)]
    inner = [(border, border), (w - border, border),
             (w - border, h - border), (border, h - border)]
    # 外框（金色描边 + 明暗）
    for i in range(int(border)):
        t = i / max(1.0, border)
        col = mix(_sh(color, 0.65), shade(color, 1.35), 0.5 + 0.5 * math.sin(t * math.pi))
        d.rectangle((i, i, w - i, h - i), outline=_rgba(col, 235), width=1)
    d.rectangle((border * 0.45, border * 0.45, w - border * 0.45, h - border * 0.45),
                outline=_rgba(accent, 190), width=max(1, int(border * 0.12)))
    if bool(spec.get("ornate", True)):
        r = border * 1.6
        for cx_, cy_ in ((border, border), (w - border, border),
                         (border, h - border), (w - border, h - border)):
            for k in range(3):
                rr = r * (1.0 - 0.24 * k)
                d.arc((cx_ - rr, cy_ - rr, cx_ + rr, cy_ + rr),
                      0, 360, fill=_rgba(accent, 170), width=max(1, int(border * 0.10)))
            d.ellipse((cx_ - r * 0.22, cy_ - r * 0.22,
                       cx_ + r * 0.22, cy_ + r * 0.22),
                      fill=_rgba(shade(color, 1.4), 200))
    # 内阴影
    d.rectangle((border, border, w - border, h - border),
                outline=_rgba(_sh(color, 0.45), 170), width=max(2, int(border * 0.2)))


# ===========================================================================
# 3. 巴洛克烛光（baroque_candle）
# ===========================================================================
@register("gothic_arch")
def p_gothic_arch(img: Image.Image, spec: dict, ctx) -> None:
    """哥特尖拱：柱身 + 双弧尖拱 + 内凹阴影 + 金色边光，count 个横向排布。"""
    w, h = ctx.w, ctx.h
    color = ctx.spec_color(spec, "color", "stone")
    accent = ctx.color(spec.get("accent") or "accent", fallback="accent")
    count = max(1, min(8, _i(spec, "count", 4)))
    opacity = max(0.1, min(1.0, _f(spec, "opacity", 0.9)))
    y_bottom = _f(spec, "y_bottom", 0.98)
    d = ImageDraw.Draw(img, "RGBA")
    bot = ctx.Y(y_bottom)
    for k in range(count):
        t = (k + 0.5) / count
        cx_ = ctx.X(0.06 + 0.88 * t)
        half = ctx.S(0.075 + 0.02 * math.sin(math.pi * t))
        spring = bot - ctx.S(0.34 + 0.10 * math.sin(math.pi * t))
        # 双圆心尖拱（two-centred pointed arch）
        radius = half * 1.55
        cxl = cx_ + half * 0.55
        cxr = cx_ - half * 0.55
        a_end = math.acos((cx_ - cxl) / radius)
        apex = spring - radius * math.sin(a_end)
        rise = spring - apex
        arc_l = []
        arc_r = []
        for i in range(14):
            a = math.pi - (math.pi - a_end) * i / 13.0
            arc_l.append((cxl + radius * math.cos(a), spring - radius * math.sin(a)))
            arc_r.append((cxr - radius * math.cos(a), spring - radius * math.sin(a)))
        shape = [(cx_ - half, bot)] + arc_l + list(reversed(arc_r)) + [(cx_ + half, bot)]
        # 拱洞内凹（更暗）
        _poly(d, shape, _rgba(_sh(color, 0.45), int(200 * opacity)))
        # 内层小拱（纵深）
        inner = [(cx_ - half * 0.7, bot)] + \
                [(cx_ - half * 0.7 * math.cos(u), spring + rise * 0.18 - rise * 0.82 * math.sin(u))
                 for u in [i / 10.0 * math.pi / 2 for i in range(11)]] + \
                [(cx_ + half * 0.7 * math.cos(u), spring + rise * 0.18 - rise * 0.82 * math.sin(u))
                 for u in [i / 10.0 * math.pi / 2 for i in range(11)]] + [(cx_ + half * 0.7, bot)]
        _poly(d, inner, _rgba(_sh(color, 0.28), int(170 * opacity)))
        # 柱身
        col_w = max(3.0, half * 0.34)
        for side in (-1, 1):
            x = cx_ + side * half
            d.rectangle((x - col_w / 2, spring, x + col_w / 2, bot),
                        fill=_rgba(shade(color, 1.1), int(250 * opacity)))
            d.line([(x - col_w / 2, spring), (x - col_w / 2, bot)],
                   fill=_rgba(shade(color, 1.45), int(200 * opacity)), width=2)
            d.line([(x + col_w / 2, spring), (x + col_w / 2, bot)],
                   fill=_rgba(_sh(color, 0.45), int(220 * opacity)), width=2)
            # 柱头
            d.rectangle((x - col_w * 0.9, spring - col_w * 0.5,
                         x + col_w * 0.9, spring),
                        fill=_rgba(shade(color, 1.25), int(240 * opacity)))
        # 拱边缘光
        brush_line(d, arc_l, _rgba(shade(color, 1.35), int(150 * opacity)), 3)
        brush_line(d, arc_r, _rgba(shade(color, 1.35), int(150 * opacity)), 3)
        brush_line(d, arc_l, _rgba(accent, int(95 * opacity)), 1.6)
        brush_line(d, arc_r, _rgba(accent, int(95 * opacity)), 1.6)
    # 顶部阴影
    img.alpha_composite(glow_layer(
        (w, h), [(ctx.X(0.5), ctx.Y(0.0), w * 0.6, h * 0.28, 0.32)],
        ctx.color("shadow", fallback="fg"), power=2.4, downscale=8))


@register("stone_floor")
def p_stone_floor(img: Image.Image, spec: dict, ctx) -> None:
    """石板地面：向消失点收敛的砖缝 + 交错明暗 + 烛光反光。"""
    w, h = ctx.w, ctx.h
    color = ctx.spec_color(spec, "color", "stone")
    accent = ctx.color(spec.get("accent") or "shadow", fallback="shadow")
    y_top = _f(spec, "y", 0.62)
    rows = max(2, min(10, _i(spec, "rows", 6)))
    cols = max(3, min(14, _i(spec, "cols", 8)))
    rng = _rng(ctx, "stone_floor")
    d = ImageDraw.Draw(img, "RGBA")
    top = ctx.Y(y_top)
    bottom = float(h)
    if bottom - top < 8:
        return
    vx = ctx.X(0.5)
    # 横缝（近大远小）
    ys = [top + (bottom - top) * (i / rows) ** 1.45 for i in range(rows + 1)]
    prev = top
    for i, y in enumerate(ys):
        # 石板填充（明暗交错）
        if i == 0:
            continue
        for c in range(cols):
            x0 = ctx.X((c + 0.0) / cols)
            x1 = ctx.X((c + 1.0) / cols)
            # 透视：越远越向中心收
            tt = (y - top) / max(1.0, bottom - top)
            x0 = vx + (x0 - vx) * (0.35 + 0.65 * tt)
            x1 = vx + (x1 - vx) * (0.35 + 0.65 * tt)
            tone = shade(color, 0.82 + 0.3 * rng.random())
            d.polygon([(x0, prev), (x1, prev), (x1, y), (x0, y)],
                      fill=_rgba(tone, 235 if (i + c) % 2 else 210))
        prev = y
    # 缝线
    for i, y in enumerate(ys):
        tt = (y - top) / max(1.0, bottom - top)
        d.line([(0, y), (w, y)], fill=_rgba(accent, int(120 + 90 * tt)),
               width=max(1, int(1 + 3 * tt)))
    for c in range(cols + 1):
        x = ctx.X(c / cols)
        d.line([(vx + (x - vx) * 0.35, top), (x, bottom)],
               fill=_rgba(accent, 90), width=1)
    # 反光
    img.alpha_composite(glow_layer(
        (w, h), [(ctx.X(0.5), top + (bottom - top) * 0.25,
                  w * 0.4, (bottom - top) * 0.35, 0.18)],
        ctx.color("candle", fallback="accent"), power=2.5, downscale=8))


@register("chandelier_baroque")
def p_chandelier_baroque(img: Image.Image, spec: dict, ctx) -> None:
    """巴洛克吊灯：链条 + 环形灯架 + 蜡烛群 + 暖光。"""
    w, h = ctx.w, ctx.h
    color = ctx.spec_color(spec, "color", "accent")
    candle = ctx.color(spec.get("candle") or "highlight", fallback="highlight")
    cx_ = ctx.X(_f(spec, "x", 0.5))
    cy_ = ctx.Y(_f(spec, "y", 0.14))
    scale = ctx.S(_f(spec, "scale", 0.4))
    count = max(3, min(14, _i(spec, "candles", 9)))
    glow = max(0.0, min(1.0, _f(spec, "glow", 0.55)))
    d = ImageDraw.Draw(img, "RGBA")
    # 链条
    d.line([(cx_, 0), (cx_, cy_)], fill=_rgba(shade(color, 0.5), 220),
           width=max(2, int(scale * 0.03)))
    for k in range(int(cy_ / max(4, scale * 0.06))):
        yy = k * scale * 0.06
        d.ellipse((cx_ - scale * 0.02, yy - scale * 0.012,
                   cx_ + scale * 0.02, yy + scale * 0.012),
                  outline=_rgba(color, 180), width=1)
    # 灯圈
    ring_y = cy_ + scale * 0.28
    d.ellipse((cx_ - scale * 0.55, ring_y - scale * 0.16,
               cx_ + scale * 0.55, ring_y + scale * 0.16),
              outline=_rgba(color, 245), width=max(2, int(scale * 0.035)))
    flames = []
    for k in range(count):
        ang = math.pi * (0.15 + 0.7 * k / max(1, count - 1))
        bx = cx_ + scale * 0.52 * math.cos(ang + math.pi)
        by = ring_y
        ch = scale * 0.16
        d.line([(bx, by), (bx, by - ch)], fill=_rgba(candle, 250),
               width=max(2, int(scale * 0.02)))
        d.polygon([(bx, by - ch - scale * 0.055),
                   (bx - scale * 0.018, by - ch + scale * 0.01),
                   (bx + scale * 0.018, by - ch + scale * 0.01)],
                  fill=_rgba(ctx.color("highlight", fallback="highlight"), 245))
        flames.append((bx, by - ch, scale * 0.09, scale * 0.15, 0.8))
    # 垂饰
    for k in range(count):
        t = (k + 0.5) / count
        bx = cx_ - scale * 0.52 + scale * 1.04 * t
        d.line([(bx, ring_y), (bx, ring_y + scale * 0.16)],
               fill=_rgba(color, 200), width=max(1, int(scale * 0.012)))
        d.ellipse((bx - scale * 0.02, ring_y + scale * 0.15,
                   bx + scale * 0.02, ring_y + scale * 0.20),
                  fill=_rgba(shade(color, 1.2), 220))
    if glow > 0:
        img.alpha_composite(glow_layer(
            (w, h), [(f[0], f[1], f[2] * 3.2, f[3] * 3.0, f[4] * glow) for f in flames],
            ctx.color("candle", fallback="highlight"),
            power=2.0, downscale=6, blur=2))


@register("crypt_vault")
def p_crypt_vault(img: Image.Image, spec: dict, ctx) -> None:
    """地窖穹顶与石棺：交叉拱肋 + 石柱 + 前景石棺剪影。"""
    w, h = ctx.w, ctx.h
    color = ctx.spec_color(spec, "color", "stone")
    accent = ctx.color(spec.get("accent") or "shadow", fallback="shadow")
    count = max(1, min(5, _i(spec, "count", 3)))
    opacity = max(0.1, min(1.0, _f(spec, "opacity", 0.95)))
    d = ImageDraw.Draw(img, "RGBA")
    # 左右石柱
    for side in (-1, 1):
        x = ctx.X(0.5 + side * 0.42)
        d.rectangle((x - ctx.S(0.05), ctx.Y(0.05), x + ctx.S(0.05), ctx.Y(0.98)),
                    fill=_rgba(shade(color, 0.65), int(240 * opacity)))
        d.line([(x - ctx.S(0.02), ctx.Y(0.05)), (x - ctx.S(0.02), ctx.Y(0.98))],
               fill=_rgba(shade(color, 1.25), int(200 * opacity)), width=3)
    # 交叉拱肋
    for k in range(count):
        t = (k + 0.5) / count
        apex = (ctx.X(0.5 + (t - 0.5) * 0.5), ctx.Y(0.02 + 0.04 * (k % 2)))
        left = (ctx.X(0.05 + 0.1 * t), ctx.Y(0.55))
        right = (ctx.X(0.95 - 0.1 * t), ctx.Y(0.55))
        for a, b in ((left, apex), (apex, right)):
            pts = []
            for i in range(9):
                tt = i / 8.0
                x = a[0] + (b[0] - a[0]) * tt
                y = a[1] + (b[1] - a[1]) * tt - math.sin(math.pi * tt) * ctx.S(0.03)
                pts.append((x, y))
            brush_line(d, pts, _rgba(shade(color, 1.15), int(230 * opacity)), 4, taper=True)
    # 前景石棺
    base_y = ctx.Y(0.9)
    for k in range(2):
        cx_ = ctx.X(0.32 + 0.36 * k)
        bw, bh = ctx.S(0.2), ctx.S(0.11)
        d.rounded_rectangle((cx_ - bw / 2, base_y - bh, cx_ + bw / 2, base_y),
                            radius=bh * 0.25, fill=_rgba(shade(color, 0.55), 250))
        d.rounded_rectangle((cx_ - bw * 0.56, base_y - bh * 1.18,
                             cx_ + bw * 0.56, base_y - bh * 0.86),
                            radius=bh * 0.2, fill=_rgba(shade(color, 0.8), 250))
        d.line([(cx_ - bw * 0.5, base_y - bh * 0.98),
                (cx_ + bw * 0.5, base_y - bh * 0.98)],
               fill=_rgba(accent, 150), width=2)
    # 拱顶阴影
    img.alpha_composite(glow_layer(
        (w, h), [(ctx.X(0.5), ctx.Y(0.0), w * 0.6, h * 0.3, 0.35)],
        ctx.color("shadow", fallback="fg"), power=2.6, downscale=8))


# ===========================================================================
# 4. 糖果拼贴（candy_collage）
# ===========================================================================
@register("collage_panels")
def p_collage_panels(img: Image.Image, spec: dict, ctx) -> None:
    """撕纸拼贴底板：多层旋转色块 + 胶带 + 网点。"""
    w, h = ctx.w, ctx.h
    colors = _roles(ctx, spec)
    count = max(2, _i(spec, "count", 6))
    rng = _rng(ctx, "collage_panels")
    d = ImageDraw.Draw(img, "RGBA")
    for _ in range(count):
        cw = rng.uniform(0.16, 0.42) * w
        ch = rng.uniform(0.12, 0.36) * h
        cx_ = rng.uniform(0.1, 0.9) * w
        cy_ = rng.uniform(0.12, 0.88) * h
        ang = rng.uniform(-0.22, 0.22)
        base = colors[rng.randrange(len(colors))]
        tone = shade(base, rng.uniform(0.9, 1.12))
        # 撕边多边形
        pts = []
        edges = 9
        for i in range(edges):
            t = i / edges
            ax = cx_ - cw / 2 + cw * t
            pts.append(_rot(ax, cy_ - ch / 2 + rng.uniform(-4, 4), cx_, cy_, ang))
        for i in range(edges):
            t = i / edges
            ay = cy_ - ch / 2 + ch * t
            pts.append(_rot(cx_ + cw / 2 + rng.uniform(-4, 4), ay, cx_, cy_, ang))
        for i in range(edges):
            t = i / edges
            ax = cx_ + cw / 2 - cw * t
            pts.append(_rot(ax, cy_ + ch / 2 + rng.uniform(-4, 4), cx_, cy_, ang))
        for i in range(edges):
            t = i / edges
            ay = cy_ + ch / 2 - ch * t
            pts.append(_rot(cx_ - cw / 2 + rng.uniform(-4, 4), ay, cx_, cy_, ang))
        _poly(d, pts, _rgba(tone, 235),
              outline=_rgba(shade(base, 0.6), 200), width=max(1, int(ctx.S(0.0035))))
        # 半调网点
        if rng.random() < 0.55:
            for gy in range(4):
                for gx in range(6):
                    px = cx_ + (gx - 2.5) * cw / 7
                    py = cy_ + (gy - 1.5) * ch / 5
                    rr = min(cw, ch) * 0.012
                    d.ellipse((px - rr, py - rr, px + rr, py + rr),
                              fill=_rgba(shade(base, 0.5), 90))
        # 胶带
        if rng.random() < 0.6:
            tw, th = cw * 0.5, max(4.0, h * 0.018)
            tx = cx_ + rng.uniform(-cw * 0.3, cw * 0.3)
            ty = cy_ - ch / 2 - th * 0.5
            tape = _rect_pts(tx - tw / 2, ty - th / 2, tx + tw / 2, ty + th / 2,
                             ang + math.pi / 2)
            _poly(d, tape, _rgba(ctx.color("highlight", fallback="bg"), 110))
    _tex_dots(img, ctx.color("fg", fallback="fg"), rng, count=260, alpha=26, rmax=1.2)


@register("confetti_rain")
def p_confetti_rain(img: Image.Image, spec: dict, ctx) -> None:
    """彩带纸屑：彩色小纸片（矩形/三角/圆弧），随机旋转与大小。"""
    w, h = ctx.w, ctx.h
    colors = _roles(ctx, spec)
    count = max(10, _i(spec, "count", 80))
    rng = _rng(ctx, "confetti")
    d = ImageDraw.Draw(img, "RGBA")
    for _ in range(count):
        x = rng.uniform(0, w)
        y = rng.uniform(0, h * 1.05)
        size = rng.uniform(0.006, 0.02) * min(w, h)
        color = colors[rng.randrange(len(colors))]
        ang = rng.uniform(0, TAU)
        kind = rng.randrange(3)
        a = int(rng.uniform(150, 255))
        if kind == 0:
            pts = _rect_pts(x - size * 0.5, y - size * 0.25,
                            x + size * 0.5, y + size * 0.25, ang)
            _poly(d, pts, _rgba(color, a))
        elif kind == 1:
            pts = [(x + size * 0.6 * math.cos(ang + k * TAU / 3),
                    y + size * 0.6 * math.sin(ang + k * TAU / 3)) for k in range(3)]
            _poly(d, pts, _rgba(color, a))
        else:
            d.arc((x - size, y - size * 0.4, x + size, y + size * 0.4),
                  ang * 57.3, ang * 57.3 + rng.uniform(90, 240),
                  fill=_rgba(color, a), width=max(2, int(size * 0.35)))


@register("sticker_burst")
def p_sticker_burst(img: Image.Image, spec: dict, ctx) -> None:
    """星爆贴纸：多角星 + 粗描边 + 内圈色块 + 小笑脸。"""
    w, h = ctx.w, ctx.h
    colors = _roles(ctx, spec)
    count = max(1, _i(spec, "count", 4))
    rng = _rng(ctx, "sticker_burst")
    base_x = _f(spec, "x", 0.5)
    base_y = _f(spec, "y", 0.3)
    for i in range(count):
        cx_ = ctx.X(base_x + rng.uniform(-0.3, 0.3))
        cy_ = ctx.Y(base_y + rng.uniform(-0.18, 0.18))
        r = ctx.S(rng.uniform(0.05, 0.1))
        spikes = rng.choice((10, 12, 14))
        color = colors[rng.randrange(len(colors))]
        pts = []
        for k in range(spikes * 2):
            rr = r * (1.0 if k % 2 == 0 else 0.72)
            ang = k * math.pi / spikes + rng.uniform(-0.03, 0.03)
            pts.append((cx_ + rr * math.cos(ang), cy_ + rr * math.sin(ang)))
        _poly(draw := ImageDraw.Draw(img, "RGBA"), pts, _rgba(color, 245),
              outline=_rgba(ctx.color("fg", fallback="fg"), 255),
              width=max(2, int(r * 0.09)))
        # 内圈
        rr = r * 0.45
        d = draw
        d.ellipse((cx_ - rr, cy_ - rr, cx_ + rr, cy_ + rr),
                  fill=_rgba(ctx.color("highlight", fallback="highlight"), 235))
        # 简易笑脸
        er = rr * 0.16
        for side in (-1, 1):
            d.ellipse((cx_ + side * rr * 0.42 - er, cy_ - rr * 0.18 - er,
                       cx_ + side * rr * 0.42 + er, cy_ - rr * 0.18 + er),
                      fill=_rgba(ctx.color("fg", fallback="fg"), 235))
        d.arc((cx_ - rr * 0.5, cy_ - rr * 0.05, cx_ + rr * 0.5, cy_ + rr * 0.62),
              20, 160, fill=_rgba(ctx.color("fg", fallback="fg"), 235),
              width=max(2, int(rr * 0.14)))


@register("leaderboard_rows")
def p_leaderboard_rows(img: Image.Image, spec: dict, ctx) -> None:
    """排行榜：圆角行条 + 名次徽章 + 进度条 + 虚线分隔。"""
    w, h = ctx.w, ctx.h
    color = ctx.spec_color(spec, "color", "fg")
    accent = ctx.color(spec.get("accent") or "accent", fallback="accent")
    rows = max(2, min(8, _i(spec, "rows", 5)))
    rng = _rng(ctx, "leaderboard")
    d = ImageDraw.Draw(img, "RGBA")
    top = ctx.Y(_f(spec, "y", 0.18))
    bottom = ctx.Y(0.9)
    row_h = (bottom - top) / rows
    for r in range(rows):
        y0 = top + r * row_h + row_h * 0.12
        y1 = y0 + row_h * 0.72
        x0 = ctx.X(0.16)
        x1 = ctx.X(0.84)
        tone = shade(color, 0.9 + 0.12 * rng.random())
        d.rounded_rectangle((x0, y0, x1, y1), radius=row_h * 0.3,
                            fill=_rgba(tone, 60),
                            outline=_rgba(color, 210),
                            width=max(2, int(row_h * 0.06)))
        # 名次徽章
        br = row_h * 0.28
        bcx, bcy = x0 + row_h * 0.42, (y0 + y1) / 2
        d.ellipse((bcx - br, bcy - br, bcx + br, bcy + br),
                  fill=_rgba(accent if r == 0 else shade(accent, 0.85), 240))
        draw_text(d, (bcx, bcy), str(r + 1), int(br * 1.15),
                  _rgba(ctx.color("highlight", fallback="bg"), 255), anchor="mm")
        # 进度条
        bar_x0 = x0 + row_h * 0.9
        bar_x1 = x1 - row_h * 0.4
        bar_y = (y0 + y1) / 2
        frac = 0.92 - 0.16 * r - rng.uniform(0, 0.05)
        d.line([(bar_x0, bar_y), (bar_x1, bar_y)],
               fill=_rgba(color, 70), width=max(3, int(row_h * 0.14)))
        d.line([(bar_x0, bar_y), (bar_x0 + (bar_x1 - bar_x0) * frac, bar_y)],
               fill=_rgba(accent, 235), width=max(3, int(row_h * 0.14)))
        for k in range(3):
            tx = bar_x1 + row_h * 0.18 + k * row_h * 0.16
            d.ellipse((tx - 2, bar_y - 2, tx + 2, bar_y + 2),
                      fill=_rgba(color, 160))
    d.line([(ctx.X(0.16), bottom + row_h * 0.06), (ctx.X(0.84), bottom + row_h * 0.06)],
           fill=_rgba(ctx.color("fg", fallback="fg"), 120), width=2)


@register("prop_icons")
def p_prop_icons(img: Image.Image, spec: dict, ctx) -> None:
    """拼贴道具图标：奖杯/按钮/卡牌/铃铛/星星/骰子，散布在台面上。"""
    w, h = ctx.w, ctx.h
    colors = _roles(ctx, spec)
    count = max(2, min(10, _i(spec, "count", 6)))
    rng = _rng(ctx, "prop_icons")
    cx0 = _f(spec, "x", 0.5)
    cy0 = _f(spec, "y", 0.72)
    d = ImageDraw.Draw(img, "RGBA")
    kinds = ["trophy", "button", "cards", "bell", "star", "die"]
    for i in range(count):
        kind = kinds[i % len(kinds)]
        cx_ = ctx.X(cx0 + (i - (count - 1) / 2.0) * 0.12 + rng.uniform(-0.02, 0.02))
        cy_ = ctx.Y(cy0 + rng.uniform(-0.04, 0.04))
        s = ctx.S(rng.uniform(0.035, 0.055))
        color = colors[i % len(colors)]
        dark = _rgba(shade(color, 0.55), 245)
        if kind == "trophy":
            d.polygon([(cx_ - s * 0.5, cy_ - s), (cx_ + s * 0.5, cy_ - s),
                       (cx_ + s * 0.3, cy_ + s * 0.2), (cx_ - s * 0.3, cy_ + s * 0.2)],
                      fill=_rgba(color, 245), outline=dark)
            d.rectangle((cx_ - s * 0.12, cy_ + s * 0.2, cx_ + s * 0.12, cy_ + s * 0.55),
                        fill=_rgba(color, 245), outline=dark)
            d.ellipse((cx_ - s * 0.42, cy_ + s * 0.5, cx_ + s * 0.42, cy_ + s * 0.76),
                      fill=dark)
        elif kind == "button":
            d.ellipse((cx_ - s, cy_ - s, cx_ + s, cy_ + s),
                      fill=_rgba(color, 245), outline=dark, width=3)
            d.ellipse((cx_ - s * 0.55, cy_ - s * 0.55, cx_ + s * 0.55, cy_ + s * 0.55),
                      fill=_rgba(shade(color, 1.3), 230))
        elif kind == "cards":
            for k in range(3):
                pts = _rect_pts(cx_ - s + k * s * 0.28, cy_ - s * 1.2 + k * s * 0.12,
                                cx_ + s * 0.35 + k * s * 0.28, cy_ + s * 1.0 + k * s * 0.12,
                                -0.12 + k * 0.1)
                _poly(d, pts, _rgba(shade(color, 1.05), 245), outline=dark, width=2)
        elif kind == "bell":
            d.pieslice((cx_ - s, cy_ - s * 1.3, cx_ + s, cy_ + s * 0.7),
                       180, 360, fill=_rgba(color, 245))
            d.rectangle((cx_ - s, cy_ - s * 0.3, cx_ + s, cy_ + s * 0.35),
                        fill=_rgba(color, 245))
            d.ellipse((cx_ - s * 0.22, cy_ + s * 0.35, cx_ + s * 0.22, cy_ + s * 0.75),
                      fill=dark)
        elif kind == "star":
            pts = [(cx_ + s * (1.0 if k % 2 == 0 else 0.45) * math.cos(-math.pi / 2 + k * math.pi / 5),
                    cy_ + s * (1.0 if k % 2 == 0 else 0.45) * math.sin(-math.pi / 2 + k * math.pi / 5))
                   for k in range(10)]
            _poly(d, pts, _rgba(color, 245), outline=dark, width=2)
        else:  # die
            d.rounded_rectangle((cx_ - s, cy_ - s, cx_ + s, cy_ + s),
                                radius=s * 0.25, fill=_rgba(color, 245), outline=dark, width=2)
            for k in range(3):
                px = cx_ + (-s * 0.4 if k == 0 else (0 if k == 1 else s * 0.4))
                py = cy_ + (-s * 0.4 if k == 0 else (0 if k == 1 else s * 0.4))
                d.ellipse((px - s * 0.12, py - s * 0.12, px + s * 0.12, py + s * 0.12),
                          fill=dark)


# ===========================================================================
# 5. 黏土定格（clay_diorama）
# ===========================================================================
def _clay_blob(d, cx, cy, rx, ry, color, rng, outline=True):
    """黏土块：圆角不规则椭圆 + 高光 + 底部阴影。"""
    pts = []
    n = 14
    for k in range(n):
        ang = k * TAU / n
        rr = 1.0 + rng.uniform(-0.06, 0.06)
        pts.append((cx + rx * rr * math.cos(ang), cy + ry * rr * math.sin(ang)))
    _poly(d, pts, _rgba(color, 250))
    if outline:
        brush_line(d, pts, _rgba(shade(color, 0.62), 220), 2)
    hi = shade(color, 1.35)
    d.arc((cx - rx * 0.75, cy - ry * 0.95, cx + rx * 0.75, cy + ry * 0.15),
          200, 330, fill=_rgba(hi, 170), width=max(2, int(min(rx, ry) * 0.18)))
    d.arc((cx - rx * 0.8, cy - ry * 0.2, cx + rx * 0.8, cy + ry * 0.95),
          20, 160, fill=_rgba(shade(color, 0.5), 130), width=max(2, int(min(rx, ry) * 0.12)))


@register("clay_objects")
def p_clay_objects(img: Image.Image, spec: dict, ctx) -> None:
    """黏土道具组：kind ∈ tiles/pots/utensils/fence/watering_can/bench/street_lamp/
    plinth/figures/chimney。"""
    w, h = ctx.w, ctx.h
    kind = str(spec.get("kind") or "pots")
    color = ctx.spec_color(spec, "color", "clay")
    accent = ctx.color(spec.get("accent") or "accent:1", fallback="accent")
    count = max(1, min(14, _i(spec, "count", 5)))
    rng = _rng(ctx, f"clay_{kind}")
    scale = ctx.S(_f(spec, "scale", 1.0))
    d = ImageDraw.Draw(img, "RGBA")
    if kind == "tiles":
        y0 = ctx.Y(_f(spec, "y", 0.2))
        tile_h = ctx.S(_f(spec, "h", 0.4))
        cols = max(4, count)
        for r in range(3):
            for c in range(cols):
                x = ctx.X((c + 0.5) / cols)
                y = y0 + tile_h * (r + 0.5) / 3
                tw, th = scale * 0.14, tile_h / 3 * 0.82
                tone = shade(color, 0.92 + 0.12 * rng.random())
                d.rounded_rectangle((x - tw / 2, y - th / 2, x + tw / 2, y + th / 2),
                                    radius=th * 0.3, fill=_rgba(tone, 230),
                                    outline=_rgba(shade(color, 0.7), 180), width=2)
        return
    base_x = _f(spec, "x", 0.5)
    base_y = _f(spec, "y", 0.8)
    if kind == "fence":
        y0 = ctx.Y(base_y)
        span = 0.8
        d.line([(ctx.X(0.1), y0 - scale * 0.1), (ctx.X(0.9), y0 - scale * 0.1)],
               fill=_rgba(shade(color, 0.75), 220), width=max(2, int(scale * 0.02)))
        d.line([(ctx.X(0.1), y0), (ctx.X(0.9), y0)],
               fill=_rgba(shade(color, 0.75), 220), width=max(2, int(scale * 0.02)))
        for k in range(max(3, count)):
            x = ctx.X(0.1 + span * k / max(1, count - 1))
            d.rounded_rectangle((x - scale * 0.018, y0 - scale * 0.22,
                                 x + scale * 0.018, y0 + scale * 0.02),
                                radius=scale * 0.018, fill=_rgba(color, 245),
                                outline=_rgba(shade(color, 0.65), 200), width=2)
        return
    if kind == "plinth":
        cx_ = ctx.X(base_x)
        cy_ = ctx.Y(base_y)
        rx, ry = scale * 0.42, scale * 0.12
        d.ellipse((cx_ - rx, cy_ - ry, cx_ + rx, cy_ + ry),
                  fill=_rgba(shade(color, 0.8), 250))
        d.rectangle((cx_ - rx, cy_ - ry * 0.2, cx_ + rx, cy_ + ry * 0.6),
                    fill=_rgba(color, 250))
        d.ellipse((cx_ - rx, cy_ + ry * 0.2, cx_ + rx, cy_ + ry * 1.1),
                  fill=_rgba(shade(color, 0.85), 250))
        return
    if kind == "street_lamp":
        for k in range(count):
            x = ctx.X(0.2 if count == 1 else 0.18 + 0.64 * k / max(1, count - 1))
            y = ctx.Y(base_y)
            post_h = scale * 0.9
            d.line([(x, y), (x, y - post_h)], fill=_rgba(shade(color, 0.7), 245),
                   width=max(3, int(scale * 0.03)))
            d.ellipse((x - scale * 0.09, y - post_h - scale * 0.16,
                       x + scale * 0.09, y - post_h + scale * 0.02),
                      fill=_rgba(accent, 235), outline=_rgba(shade(color, 0.6), 220), width=2)
            img.alpha_composite(glow_layer(
                (w, h), [(x, y - post_h, scale * 0.16, scale * 0.16, 0.5)],
                ctx.color("highlight", fallback="accent"), power=2.2, downscale=8))
        return
    if kind == "figures":
        for k in range(count):
            cx_ = ctx.X(base_x + (k - (count - 1) / 2.0) * 0.11)
            cy_ = ctx.Y(base_y)
            body_r = scale * 0.05
            _clay_blob(d, cx_, cy_ - body_r * 1.15, body_r, body_r * 1.4, color, rng)
            head_r = body_r * 0.72
            d.ellipse((cx_ - head_r, cy_ - body_r * 3.4 - head_r,
                       cx_ + head_r, cy_ - body_r * 3.4 + head_r),
                      fill=_rgba(shade(color, 1.15), 250),
                      outline=_rgba(shade(color, 0.6), 220), width=2)
        return
    # 默认：成组罐子/餐具/长椅/浇水壶
    if kind == "bench":
        x0 = ctx.X(base_x) - scale * 0.3
        y0 = ctx.Y(base_y)
        d.rounded_rectangle((x0, y0 - scale * 0.16, x0 + scale * 0.6, y0 - scale * 0.08),
                            radius=scale * 0.03, fill=_rgba(color, 245),
                            outline=_rgba(shade(color, 0.65), 220), width=2)
        for leg in (x0 + scale * 0.05, x0 + scale * 0.5):
            d.rectangle((leg, y0 - scale * 0.1, leg + scale * 0.04, y0),
                        fill=_rgba(shade(color, 0.7), 245))
        return
    if kind == "watering_can":
        cx_, cy_ = ctx.X(base_x), ctx.Y(base_y)
        _clay_blob(d, cx_, cy_, scale * 0.14, scale * 0.12, accent, rng)
        d.line([(cx_ + scale * 0.1, cy_ - scale * 0.02),
                (cx_ + scale * 0.26, cy_ - scale * 0.14)],
               fill=_rgba(shade(accent, 0.75), 240), width=max(3, int(scale * 0.03)))
        d.arc((cx_ - scale * 0.12, cy_ - scale * 0.24,
               cx_ + scale * 0.12, cy_ - scale * 0.04), 200, 350,
              fill=_rgba(shade(accent, 0.7), 235), width=max(2, int(scale * 0.02)))
        return
    if kind == "utensils":
        for k in range(count):
            x = ctx.X(base_x + (k - (count - 1) / 2.0) * 0.09)
            y = ctx.Y(base_y)
            d.line([(x, y), (x, y - scale * 0.28)], fill=_rgba(shade(color, 0.8), 240),
                   width=max(3, int(scale * 0.018)))
            d.ellipse((x - scale * 0.035, y - scale * 0.34,
                       x + scale * 0.035, y - scale * 0.24),
                      fill=_rgba(accent, 235))
        return
    for k in range(count):
        cx_ = ctx.X(base_x + (k - (count - 1) / 2.0) * 0.11)
        cy_ = ctx.Y(base_y + rng.uniform(-0.02, 0.02))
        rx = scale * rng.uniform(0.05, 0.075)
        ry = scale * rng.uniform(0.055, 0.085)
        _clay_blob(d, cx_, cy_, rx, ry, shade(color, 0.92 + 0.18 * rng.random()), rng)
        # 罐口
        d.ellipse((cx_ - rx * 0.5, cy_ - ry * 0.98, cx_ + rx * 0.5, cy_ - ry * 0.7),
                  fill=_rgba(shade(color, 1.25), 240))


@register("clay_plants")
def p_clay_plants(img: Image.Image, spec: dict, ctx) -> None:
    """黏土植物/花：茎 + 叶团 + 花头，成排散布。"""
    w, h = ctx.w, ctx.h
    color = ctx.spec_color(spec, "color", "accent")
    accent = ctx.color(spec.get("accent") or "accent:1", fallback="accent")
    count = max(1, min(16, _i(spec, "count", 6)))
    y = _f(spec, "y", 0.85)
    scale = ctx.S(_f(spec, "scale", 1.0))
    rng = _rng(ctx, "clay_plants")
    d = ImageDraw.Draw(img, "RGBA")
    for k in range(count):
        x = ctx.X(0.1 + 0.8 * (k + 0.5) / count + rng.uniform(-0.02, 0.02))
        base_y = ctx.Y(y + rng.uniform(-0.02, 0.02))
        height = scale * rng.uniform(0.1, 0.2)
        d.line([(x, base_y), (x + rng.uniform(-4, 4), base_y - height)],
               fill=_rgba(shade(color, 0.75), 240), width=max(2, int(scale * 0.014)))
        leaf_r = height * rng.uniform(0.22, 0.34)
        for side in (-1, 1):
            lx = x + side * leaf_r * 0.8
            ly = base_y - height * rng.uniform(0.45, 0.8)
            _clay_blob(d, lx, ly, leaf_r, leaf_r * 0.72,
                       shade(color, 0.9 + 0.2 * rng.random()), rng, outline=False)
        if rng.random() < 0.6:
            d.ellipse((x - leaf_r * 0.6, base_y - height - leaf_r * 0.6,
                       x + leaf_r * 0.6, base_y - height + leaf_r * 0.6),
                      fill=_rgba(accent, 245),
                      outline=_rgba(shade(accent, 0.6), 200), width=2)
            d.ellipse((x - leaf_r * 0.2, base_y - height - leaf_r * 0.2,
                       x + leaf_r * 0.2, base_y - height + leaf_r * 0.2),
                      fill=_rgba(shade(accent, 1.3), 245))


@register("clay_houses")
def p_clay_houses(img: Image.Image, spec: dict, ctx) -> None:
    """黏土小屋：圆角墙体 + 三角屋顶 + 门窗 + 烟囱，成排。"""
    w, h = ctx.w, ctx.h
    color = ctx.spec_color(spec, "color", "clay")
    accent = ctx.color(spec.get("accent") or "accent:1", fallback="accent")
    count = max(1, min(6, _i(spec, "count", 3)))
    y = _f(spec, "y", 0.72)
    scale = ctx.S(_f(spec, "scale", 1.0))
    rng = _rng(ctx, "clay_houses")
    d = ImageDraw.Draw(img, "RGBA")
    for k in range(count):
        cx_ = ctx.X(0.12 + 0.76 * (k + 0.5) / count + rng.uniform(-0.02, 0.02))
        base_y = ctx.Y(y + rng.uniform(-0.03, 0.02))
        house_w = scale * rng.uniform(0.18, 0.28)
        house_h = scale * rng.uniform(0.14, 0.22)
        tone = shade(color, 0.9 + 0.18 * rng.random())
        d.rounded_rectangle((cx_ - house_w / 2, base_y - house_h,
                             cx_ + house_w / 2, base_y),
                            radius=house_w * 0.06, fill=_rgba(tone, 250),
                            outline=_rgba(shade(color, 0.62), 210), width=2)
        # 屋顶
        roof = [(cx_ - house_w * 0.58, base_y - house_h),
                (cx_, base_y - house_h - house_h * 0.62),
                (cx_ + house_w * 0.58, base_y - house_h)]
        _poly(d, roof, _rgba(accent, 250),
              outline=_rgba(shade(accent, 0.6), 210), width=2)
        # 门与窗
        d.rounded_rectangle((cx_ - house_w * 0.1, base_y - house_h * 0.42,
                             cx_ + house_w * 0.1, base_y),
                            radius=house_w * 0.02, fill=_rgba(shade(color, 0.55), 245))
        for side in (-1, 1):
            wx = cx_ + side * house_w * 0.28
            wy = base_y - house_h * 0.62
            d.rounded_rectangle((wx - house_w * 0.08, wy - house_h * 0.1,
                                 wx + house_w * 0.08, wy + house_h * 0.1),
                                radius=house_w * 0.02,
                                fill=_rgba(ctx.color("highlight", fallback="butter"), 240))
        # 烟囱
        if rng.random() < 0.6:
            chx = cx_ + house_w * 0.3
            d.rectangle((chx - house_w * 0.04, base_y - house_h - house_h * 0.55,
                         chx + house_w * 0.04, base_y - house_h - house_h * 0.25),
                        fill=_rgba(shade(accent, 0.75), 245))


@register("tilt_shift")
def p_tilt_shift(img: Image.Image, spec: dict, ctx) -> None:
    """移轴微缩：上下模糊带 + 中间清晰（直接把当前图层整体做景深）。"""
    w, h = img.size
    focus = _f(spec, "focus_y", 0.62)
    strength = max(0.0, min(1.0, _f(spec, "strength", 0.5)))
    if strength <= 0.01 or w < 8 or h < 8:
        return
    blur_radius = max(1.0, strength * min(w, h) * 0.018)
    blurred = img.filter(ImageFilter.GaussianBlur(blur_radius))
    # 用渐变 L 蒙版合成：清晰区在 focus 附近
    mask = Image.new("L", (w, h), 0)
    md = ImageDraw.Draw(mask)
    fy = int(focus * h)
    band = int(h * (0.10 + 0.12 * (1.0 - strength)))
    md.rectangle((0, max(0, fy - band), w, min(h, fy + band)), fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(max(2.0, h * 0.06)))
    img.paste(blurred, (0, 0), mask.point(lambda v: int(v * 0.85)))


# ===========================================================================
# 6. 粉笔素描剧场（chalk_theatre）
# ===========================================================================
@register("blackboard")
def p_blackboard(img: Image.Image, spec: dict, ctx) -> None:
    """黑板：板面底色 + 擦拭雾痕 + 木框 + 粉笔灰。"""
    w, h = ctx.w, ctx.h
    color = ctx.spec_color(spec, "color", "paper")
    try:
        frame = ctx.color(spec.get("frame") or "wood", fallback="accent")
    except V5Error:  # 自定义场景可能写错 palette 角色：退回 accent
        frame = ctx.color("accent", fallback="fg")
    strength = max(0.0, min(1.0, _f(spec, "strength", 0.4)))
    rng = _rng(ctx, "blackboard")
    # 板面（略不均匀）
    img.paste(_rgba(color, 255), (0, 0, w, h))
    import numpy as np
    noise_arr = smooth_noise(max(8, w // 12), max(8, h // 12), scale=0.35,
                             seed=ctx.seed + 3, octaves=3)
    noise = Image.fromarray((noise_arr * 255.0).astype("uint8"), "L")
    noise = noise.resize((w, h), Image.BILINEAR).filter(ImageFilter.GaussianBlur(w * 0.004))
    img.alpha_composite(mask_layer((w, h), noise,
                                   ctx.color("highlight", fallback="highlight"),
                                   alpha=int(16 + 20 * strength)))
    # 擦拭弧痕
    for _ in range(6):
        cx_ = rng.uniform(0.1, 0.9) * w
        cy_ = rng.uniform(0.1, 0.9) * h
        rx, ry = rng.uniform(0.1, 0.3) * w, rng.uniform(0.05, 0.16) * h
        d = ImageDraw.Draw(img, "RGBA")
        d.arc((cx_ - rx, cy_ - ry, cx_ + rx, cy_ + ry),
              rng.uniform(0, 360), rng.uniform(0, 360),
              fill=_rgba(ctx.color("highlight", fallback="highlight"), 14),
              width=max(2, int(h * 0.012)))
    # 木框
    fw = max(3.0, ctx.S(0.016))
    d = ImageDraw.Draw(img, "RGBA")
    d.rectangle((0, 0, w - 1, h - 1), outline=_rgba(frame, 255), width=int(fw))
    for i in range(2):
        d.rectangle((fw * (0.4 + 0.25 * i), fw * (0.4 + 0.25 * i),
                     w - fw * (0.4 + 0.25 * i), h - fw * (0.4 + 0.25 * i)),
                    outline=_rgba(shade(frame, 1.25 if i == 0 else 0.65), 180),
                    width=max(1, int(fw * 0.2)))
    # 粉笔灰
    _tex_dots(img, ctx.color("highlight", fallback="highlight"), rng,
              count=max(200, w * h // 7000), alpha=22, rmax=1.8)


@register("chalk_hatching")
def p_chalk_hatching(img: Image.Image, spec: dict, ctx) -> None:
    """粉笔排线：成组平行短线（可限定 x/y/w/h 区域）。"""
    w, h = ctx.w, ctx.h
    color = ctx.spec_color(spec, "color", "fg")
    opacity = max(0.02, min(1.0, _f(spec, "opacity", 0.15)))
    count = max(4, _i(spec, "count", 26))
    rng = _rng(ctx, "chalk_hatch")
    d = ImageDraw.Draw(img, "RGBA")
    region = ctx.box(spec, defaults=(0.0, _f(spec, "y", 0.0), 1.0, _f(spec, "h", 1.0)))
    x0, y0, x1, y1 = region
    if x1 - x0 < 4 or y1 - y0 < 4:
        return
    base_ang = math.radians(_f(spec, "angle", 32.0) + rng.uniform(-8, 8))
    for _ in range(count):
        cx_ = rng.uniform(x0, x1)
        cy_ = rng.uniform(y0, y1)
        length = rng.uniform(0.03, 0.12) * min(w, h)
        ang = base_ang + rng.uniform(-0.25, 0.25)
        pts = [(cx_ - math.cos(ang) * length / 2, cy_ - math.sin(ang) * length / 2),
               (cx_ + math.cos(ang) * length / 2, cy_ + math.sin(ang) * length / 2)]
        a = int(200 * opacity * rng.uniform(0.4, 1.0))
        for pass_i in range(2):
            jp = [(x + rng.uniform(-1.2, 1.2), y + rng.uniform(-1.2, 1.2))
                  for x, y in pts]
            d.line(jp, fill=_rgba(color, a), width=max(1, int(rng.uniform(1, 2.6))))


@register("chalk_curtains")
def p_chalk_curtains(img: Image.Image, spec: dict, ctx) -> None:
    """粉笔红幕：两侧手绘幕布 + 顶部垂幔 + 粉笔排线阴影。"""
    w, h = ctx.w, ctx.h
    color = ctx.spec_color(spec, "color", "accent")
    accent = ctx.color(spec.get("accent") or "accent:1", fallback="highlight")
    opacity = max(0.1, min(1.0, _f(spec, "opacity", 0.95)))
    rng = _rng(ctx, "chalk_curtains")
    d = ImageDraw.Draw(img, "RGBA")
    curtain_w = ctx.X(0.2)
    # 顶幔
    swag_y = ctx.Y(0.0)
    pts = [(0, 0)]
    for k in range(9):
        pts.append((w * k / 8, swag_y + ctx.S(0.03) * math.sin(k * math.pi / 8 * 2)))
    pts += [(w, 0)]
    _poly(d, pts, _rgba(color, int(235 * opacity)))
    brush_line(d, pts[1:-1], _rgba(accent, 220), max(2, int(ctx.S(0.006))))
    # 两侧幕布
    for side in (0, 1):
        edge = []
        for k in range(11):
            t = k / 10.0
            x = curtain_w * (1.0 - 0.35 * math.sin(t * math.pi * 1.2))
            if side:
                x = w - x
            edge.append((x, h * t))
        pts = ([(0, 0)] + edge + [(0, h)]) if not side else ([(w, 0)] + edge + [(w, h)])
        _poly(d, pts, _rgba(color, int(245 * opacity)))
        brush_line(d, edge, _rgba(shade(color, 0.6), 230), max(2, int(ctx.S(0.008))))
        # 褶皱
        for k in range(4):
            fx = (curtain_w * (0.15 + 0.18 * k)) * (1 if not side else -1) + (0 if not side else w)
            pts2 = [(fx + math.sin(t * 3.1 + k) * curtain_w * 0.05, h * t)
                    for t in [i / 12 for i in range(13)]]
            brush_line(d, pts2, _rgba(shade(color, 1.35), 130),
                       max(2, int(ctx.S(0.005))), taper=True)
        # 粉笔排线
        for _ in range(18):
            cx_ = rng.uniform(0, curtain_w * 0.9) + (0 if not side else w - curtain_w * 0.9)
            cy_ = rng.uniform(0, h)
            length = rng.uniform(0.02, 0.06) * min(w, h)
            d.line([(cx_, cy_), (cx_ + length * 0.5, cy_ + length * 0.9)],
                   fill=_rgba(accent, int(70 * opacity)), width=max(1, int(ctx.S(0.002))))


@register("chalk_objects")
def p_chalk_objects(img: Image.Image, spec: dict, ctx) -> None:
    """粉笔画道具：kind ∈ microphone/chairs/cityscape/street_lamp/camera/mirror/
    clothes_rack/costume/trunk。"""
    w, h = ctx.w, ctx.h
    color = ctx.spec_color(spec, "color", "fg")
    rng = _rng(ctx, f"chalk_{spec.get('kind')}")
    d = ImageDraw.Draw(img, "RGBA")
    kind = str(spec.get("kind") or "microphone")
    cx0 = _f(spec, "x", 0.5)
    cy0 = _f(spec, "y", 0.78)
    scale = ctx.S(_f(spec, "scale", 1.0))
    lw = max(2, int(ctx.S(0.0035)))

    def _sk(pts, close=False, passes=2):
        if close:
            pts = list(pts) + [pts[0]]
        _sketch_line(d, pts, color, lw, passes=passes, rng=rng, jitter=1.4)

    if kind == "microphone":
        x, y = ctx.X(cx0), ctx.Y(cy0)
        _sk([(x, y), (x, y - scale * 0.34)])
        _sk([(x - scale * 0.12, y), (x + scale * 0.12, y)])
        mx, my = x, y - scale * 0.42
        _sk([(mx - scale * 0.05, my + scale * 0.03),
             (mx - scale * 0.07, my - scale * 0.05),
             (mx, my - scale * 0.1),
             (mx + scale * 0.07, my - scale * 0.05),
             (mx + scale * 0.05, my + scale * 0.03)], close=True)
        for k in range(3):
            _sk([(mx - scale * 0.05, my - scale * 0.01 + k * scale * 0.018),
                 (mx + scale * 0.05, my - scale * 0.01 + k * scale * 0.018)], passes=1)
        return
    if kind == "chairs":
        count = max(1, min(6, _i(spec, "count", 3)))
        for k in range(count):
            x = ctx.X(cx0 + (k - (count - 1) / 2.0) * 0.16)
            y = ctx.Y(cy0)
            _sk([(x - scale * 0.07, y), (x - scale * 0.07, y - scale * 0.12)])
            _sk([(x - scale * 0.07, y - scale * 0.12), (x - scale * 0.12, y - scale * 0.3)])
            _sk([(x + scale * 0.07, y), (x + scale * 0.07, y - scale * 0.12)])
            _sk([(x + scale * 0.07, y - scale * 0.12), (x + scale * 0.12, y - scale * 0.3)])
            _sk([(x - scale * 0.08, y - scale * 0.12), (x + scale * 0.08, y - scale * 0.12)])
            _sk([(x - scale * 0.08, y - scale * 0.3), (x + scale * 0.08, y - scale * 0.3)])
        return
    if kind == "cityscape":
        base_y = ctx.Y(0.68)
        rng2 = _rng(ctx, "chalk_city")
        x = ctx.X(0.04)
        while x < w * 0.96:
            bw = rng2.uniform(0.04, 0.1) * w
            bh = rng2.uniform(0.1, 0.32) * h
            _sk([(x, base_y), (x, base_y - bh), (x + bw, base_y - bh), (x + bw, base_y)],
                passes=1)
            for k in range(int(bh / (h * 0.035)) + 1):
                wy = base_y - bh + h * 0.02 + k * h * 0.032
                _sk([(x + bw * 0.2, wy), (x + bw * 0.35, wy)], passes=1)
                _sk([(x + bw * 0.6, wy), (x + bw * 0.78, wy)], passes=1)
            x += bw * 1.15
        _sk([(0, base_y), (w, base_y)], passes=1)
        return
    if kind == "street_lamp":
        count = max(1, min(4, _i(spec, "count", 1)))
        for k in range(count):
            x = ctx.X(0.2 if count == 1 else 0.2 + 0.6 * k / max(1, count - 1))
            y = ctx.Y(cy0)
            _sk([(x, y), (x, y - scale * 0.9)])
            _sk([(x - scale * 0.09, y - scale * 0.9), (x, y - scale * 0.9),
                 (x + scale * 0.09, y - scale * 0.9), (x + scale * 0.06, y - scale * 0.78),
                 (x - scale * 0.06, y - scale * 0.78)], close=True)
        return
    if kind == "camera":
        x, y = ctx.X(cx0), ctx.Y(cy0)
        _sk([(x - scale * 0.12, y - scale * 0.1), (x + scale * 0.12, y - scale * 0.1),
             (x + scale * 0.12, y + scale * 0.06), (x - scale * 0.12, y + scale * 0.06)],
            close=True)
        _sk([(x - scale * 0.06, y - scale * 0.1), (x - scale * 0.06, y - scale * 0.16),
             (x + scale * 0.06, y - scale * 0.16), (x + scale * 0.06, y - scale * 0.1)])
        d.ellipse((x + scale * 0.04, y - scale * 0.07, x + scale * 0.13, y + scale * 0.02),
                  outline=_rgba(color, 220), width=lw)
        _sk([(x - scale * 0.1, y + scale * 0.06), (x - scale * 0.2, y + scale * 0.34)])
        _sk([(x + scale * 0.1, y + scale * 0.06), (x + scale * 0.2, y + scale * 0.34)])
        _sk([(x, y + scale * 0.06), (x, y + scale * 0.34)])
        return
    if kind == "mirror":
        x, y = ctx.X(cx0), ctx.Y(cy0)
        rx, ry = scale * 0.13, scale * 0.19
        d.ellipse((x - rx, y - ry, x + rx, y + ry), outline=_rgba(color, 230), width=lw)
        d.ellipse((x - rx * 0.86, y - ry * 0.86, x + rx * 0.86, y + ry * 0.86),
                  outline=_rgba(color, 180), width=max(1, lw - 1))
        _sk([(x - rx * 0.5, y + ry * 0.9), (x - rx * 0.7, y + ry * 1.5)])
        _sk([(x + rx * 0.5, y + ry * 0.9), (x + rx * 0.7, y + ry * 1.5)])
        return
    if kind == "clothes_rack":
        x, y = ctx.X(cx0), ctx.Y(cy0)
        _sk([(x - scale * 0.22, y - scale * 0.4), (x + scale * 0.22, y - scale * 0.4)])
        for side in (-1, 1):
            _sk([(x + side * scale * 0.2, y - scale * 0.4),
                 (x + side * scale * 0.2, y + scale * 0.1)])
        for k in range(5):
            hx = x - scale * 0.16 + k * scale * 0.08
            _sk([(hx, y - scale * 0.4), (hx, y - scale * 0.33)], passes=1)
            _sk([(hx - scale * 0.035, y - scale * 0.33), (hx, y - scale * 0.28),
                 (hx + scale * 0.035, y - scale * 0.33)], passes=1)
        return
    if kind == "costume":
        x, y = ctx.X(cx0), ctx.Y(cy0)
        _sk([(x, y - scale * 0.4), (x, y - scale * 0.28)])
        _sk([(x - scale * 0.12, y - scale * 0.24), (x - scale * 0.2, y + scale * 0.12),
             (x + scale * 0.2, y + scale * 0.12), (x + scale * 0.12, y - scale * 0.24),
             (x, y - scale * 0.3)], close=True)
        _sk([(x - scale * 0.02, y + scale * 0.12), (x - scale * 0.06, y + scale * 0.3)])
        _sk([(x + scale * 0.02, y + scale * 0.12), (x + scale * 0.06, y + scale * 0.3)])
        return
    # trunk
    x, y = ctx.X(cx0), ctx.Y(cy0)
    _sk([(x - scale * 0.16, y), (x - scale * 0.16, y - scale * 0.16),
         (x + scale * 0.16, y - scale * 0.16), (x + scale * 0.16, y)], close=True)
    _sk([(x - scale * 0.16, y - scale * 0.11), (x + scale * 0.16, y - scale * 0.11)])
    _sk([(x - scale * 0.1, y - scale * 0.16), (x - scale * 0.1, y - scale * 0.26),
         (x + scale * 0.1, y - scale * 0.26), (x + scale * 0.1, y - scale * 0.16)])


@register("chalk_audience")
def p_chalk_audience(img: Image.Image, spec: dict, ctx) -> None:
    """粉笔观众：成排头肩线稿 + 随机掌声短线。"""
    w, h = ctx.w, ctx.h
    color = ctx.spec_color(spec, "color", "fg")
    rows = max(1, min(5, _i(spec, "rows", 2)))
    per_row = max(3, min(14, _i(spec, "per_row", 9)))
    y_start = _f(spec, "y_start", 0.9)
    opacity = max(0.1, min(1.0, _f(spec, "opacity", 0.8)))
    rng = _rng(ctx, "chalk_audience")
    d = ImageDraw.Draw(img, "RGBA")
    lw = max(2, int(ctx.S(0.003)))

    def _line(pts, passes=1):
        _sketch_line(d, pts, _rgba(color, int(230 * opacity)), lw, passes=passes,
                     rng=rng, jitter=1.6)

    for row in range(rows):
        y = ctx.Y(y_start - 0.085 * (rows - 1 - row))
        scale = ctx.S(0.085 + 0.01 * row)
        for k in range(per_row):
            x = ctx.X((k + 0.5) / per_row + rng.uniform(-0.01, 0.01))
            hr = scale * 0.17
            _line([(x - hr, y - scale * 0.42), (x - hr, y - scale * 0.62),
                   (x, y - scale * 0.72), (x + hr, y - scale * 0.62),
                   (x + hr, y - scale * 0.42)], passes=1)
            _line([(x - scale * 0.31, y), (x - scale * 0.2, y - scale * 0.32),
                   (x, y - scale * 0.4), (x + scale * 0.2, y - scale * 0.32),
                   (x + scale * 0.31, y)], passes=1)
            # 掌声记号
            if rng.random() < 0.35:
                for side in (-1, 1):
                    hx = x + side * scale * 0.34
                    hy = y - scale * 0.3
                    for a in (-0.5, 0.0, 0.5):
                        _line([(hx, hy), (hx + math.cos(a) * scale * 0.1,
                                          hy + math.sin(a) * scale * 0.1 - scale * 0.02)],
                              passes=1)


# ===========================================================================
# 自检
# ===========================================================================
PRIMITIVES = [
    "silk_weave", "gongbi_mountains", "gongbi_clouds", "silk_banner",
    "silk_panels", "ink_circle_stage", "ink_crowd", "scroll_rods",
    "flying_white",
    "silk_ground_wash",
    "oil_strokes", "drapery", "candelabra", "oval_roundtable", "gold_frame",
    "gothic_arch", "stone_floor", "chandelier_baroque", "crypt_vault",
    "collage_panels", "confetti_rain", "sticker_burst", "leaderboard_rows",
    "prop_icons", "clay_objects", "clay_plants", "clay_houses", "tilt_shift",
    "blackboard", "chalk_hatching", "chalk_curtains", "chalk_objects",
    "chalk_audience",
]

if __name__ == "__main__":  # pragma: no cover
    from . import scene_render
    print("modern_b primitives:", len(PRIMITIVES))
    for name in PRIMITIVES:
        if name not in scene_render.list_primitives():
            print("MISSING", name)
