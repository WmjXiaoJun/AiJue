# -*- coding: utf-8 -*-
"""V5 现代风格 primitive 扩展（作者 A：neon_toon / film_interview / noir_rain /
blueprint_lab / hologram_stage / vhs_late_show 的 24 个场景专用）。

契约（见 pipeline/scene_render.py 文档头）：
    def primitive(img: PIL.Image.Image, spec: dict, ctx: SceneCtx) -> None

- img 是当前图层画布（RGBA，尺寸 ctx.w × ctx.h；clip 时是 clip 窗口）；
- spec 是 scene.layers[i] 原 dict（x/y/w/h/length/size/color/opacity/… 由本模块读）；
- ctx 提供坐标/调色板/确定性 rng/风格主题 helper。
颜色统一走 ``ctx.spec_color(spec, "color", fallback_role)``（palette 角色名或 hex），不写死色。

本模块在 import 时向 scene_render 注册；scene_render.EXTENSION_MODULES 已包含
``v5_primitives_modern``，无需改引擎文件。
"""
from __future__ import annotations

import math
import random

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from .scene_render import (blurred_layer, brush_line, dashed_line, draw_text,
                           glow_layer, hatch_layer, hex_rgba, mask_layer, mix,
                           register, shade, smooth_noise, soft_layer, vgradient)

# ---------------------------------------------------------------------------
# 本地小工具（不依赖引擎私有函数）
# ---------------------------------------------------------------------------


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


def _s(spec: dict, key: str, default: str) -> str:
    value = spec.get(key, default)
    return str(value) if value is not None else default


def _rng(ctx, salt: str) -> random.Random:
    return ctx.rng_from(f"{salt}|{_i(ctx.scene, 'seed', 0)}")


def _rgba(color, alpha: int):
    c = tuple(int(v) for v in color[:3])
    return (c[0], c[1], c[2], max(0, min(255, int(alpha))))


def _poly(img: Image.Image, pts, color) -> None:
    if len(pts) >= 3:
        layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ImageDraw.Draw(layer).polygon(pts, fill=color)
        img.alpha_composite(layer)


def _line(img: Image.Image, pts, color, width: float) -> None:
    if len(pts) >= 2:
        layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
        brush_line(ImageDraw.Draw(layer), pts, color, width)
        img.alpha_composite(layer)


def _rect(img: Image.Image, box, color, radius: int = 0) -> None:
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    if radius:
        d.rounded_rectangle(tuple(int(v) for v in box), radius=int(radius), fill=color)
    else:
        d.rectangle(tuple(int(v) for v in box), fill=color)
    img.alpha_composite(layer)


def _ellipse(img: Image.Image, box, color) -> None:
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).ellipse(tuple(int(v) for v in box), fill=color)
    img.alpha_composite(layer)


def _glow(img: Image.Image, ctx, blobs, color, power: float = 2.0,
          downscale: int = 6) -> None:
    img.alpha_composite(glow_layer((ctx.w, ctx.h), blobs, color,
                                   power=power, downscale=downscale))


def _screen_glint(img: Image.Image, ctx, box, color) -> None:
    """屏幕/玻璃斜向高光条。"""
    x0, y0, x1, y1 = box
    pts = [(x0 + (x1 - x0) * 0.15, y1), (x0 + (x1 - x0) * 0.42, y0),
           (x0 + (x1 - x0) * 0.56, y0), (x0 + (x1 - x0) * 0.29, y1)]
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).polygon(pts, fill=color)
    layer = layer.filter(ImageFilter.GaussianBlur(2))
    img.alpha_composite(layer)


# ---------------------------------------------------------------------------
# 1) 霓虹灯管 / 玻璃幕墙
# ---------------------------------------------------------------------------
def neon_tube(img: Image.Image, spec: dict, ctx) -> None:
    """霓虹灯管：沿 x,y 起点的水平或竖向灯管，带外发光与高亮芯。"""
    color = ctx.spec_color(spec, "color", "accent")
    length = ctx.S(_f(spec, "length", 0.24))
    thick = ctx.S(_f(spec, "thickness", 0.012))
    vertical = bool(spec.get("vertical")) or abs(_f(spec, "angle", 0.0) % 180.0 - 90.0) < 30
    x = ctx.X(_f(spec, "x", 0.5))
    y = ctx.Y(_f(spec, "y", 0.5))
    if vertical:
        p0, p1 = (x, y), (x, y + length)
    else:
        p0, p1 = (x, y), (x + length, y)
    # 外发光（两层：宽而弱 + 窄而强）
    _glow(img, ctx, [(p0[0], p0[1], thick * 7, thick * 7, 0.5),
                     (p1[0], p1[1], thick * 7, thick * 7, 0.5)],
          color[:3] + (255,), power=2.2, downscale=8)
    # 灯管本体（白芯 + 彩色外圈）
    tube = Image.new("RGBA", (ctx.w, ctx.h), (0, 0, 0, 0))
    d = ImageDraw.Draw(tube)
    w = max(2, int(round(thick)))
    rgba = color[:3] + (255,)
    d.line([p0, p1], fill=rgba, width=w)
    r = w / 2.0
    for p in (p0, p1):
        d.ellipse((p[0] - r, p[1] - r, p[0] + r, p[1] + r), fill=rgba)
    core = mix(color, (255, 255, 255, 255), 0.72)
    d.line([p0, p1], fill=core[:3] + (230,), width=max(1, w // 3))
    img.alpha_composite(tube)
    # 挂点
    if spec.get("mounts", False):
        for t in (0.08, 0.92):
            cx = p0[0] + (p1[0] - p0[0]) * t
            cy = p0[1] + (p1[1] - p0[1]) * t
            _line(img, [(cx - thick, cy), (cx + thick, cy)],
                  ctx.color("fg:1", alpha=180), 2)


def glass_wall(img: Image.Image, spec: dict, ctx) -> None:
    """玻璃幕墙：半透明玻璃 + 竖明框 + 城市反光条。"""
    box = ctx.box(spec, defaults=(0.0, 0.35, 1.0, 0.6))
    x0, y0, x1, y1 = [int(round(v)) for v in box]
    if x1 < x0:
        x0, x1 = x1, x0
    if y1 < y0:
        y0, y1 = y1, y0
    glass = ctx.spec_color(spec, "color", "glass")
    _rect(img, (x0, y0, x1, y1), _rgba(mix(glass, (255, 255, 255, 255), 0.14), 58))
    # 幕墙上的城市反光（斜向亮带）
    _screen_glint(img, ctx, (x0, y0, x1, y1),
                  _rgba(mix(glass, (255, 255, 255, 255), 0.4), 42))
    _screen_glint(img, ctx, (x0 + (x1 - x0) * 0.2, y0, x1, y1),
                  _rgba(mix(glass, (255, 255, 255, 255), 0.55), 30))
    # 明框：竖 4-6 根 + 横 1-2 根（metal/fg）
    metal = ctx.color("fg:1", alpha=150)
    cols = max(3, _i(spec, "columns", 5))
    for k in range(cols + 1):
        cx = x0 + (x1 - x0) * k / cols
        _line(img, [(cx, y0), (cx, y1)], metal, max(2, ctx.S(0.004)))
    rows = max(1, _i(spec, "rows", 2))
    for k in range(rows + 1):
        cy = y0 + (y1 - y0) * k / rows
        _line(img, [(x0, cy), (x1, cy)], metal, max(2, ctx.S(0.003)))
    # 斜向城市反光
    for k in range(3):
        ry = y0 + (y1 - y0) * (0.22 + k * 0.26)
        _line(img, [(x0 + (x1 - x0) * 0.05, ry),
                    (x0 + (x1 - x0) * 0.9, ry - (y1 - y0) * 0.12)],
              ctx.color("highlight", alpha=44), max(2, ctx.S(0.006)))


# ---------------------------------------------------------------------------
# 2) 家具/道具（现代演播厅/访谈/舞台）
# ---------------------------------------------------------------------------
def studio_desk(img: Image.Image, spec: dict, ctx) -> None:
    """主持台：台面 + 前板 + 桌面小道具（麦/杯/卡片），卡通厚描边。"""
    x = ctx.X(_f(spec, "x", 0.32))
    y = ctx.Y(_f(spec, "y", 0.82))
    w = ctx.S(_f(spec, "width", 0.26))
    h = ctx.S(_f(spec, "height", 0.17))
    color = ctx.spec_color(spec, "color", "accent:2")
    outline = ctx.color("shadow", alpha=180)
    top_h = max(4.0, h * 0.16)
    # 前板
    _poly(img, [(x - w * 0.46, y - h * 0.55), (x + w * 0.46, y - h * 0.55),
                (x + w * 0.52, y), (x - w * 0.52, y)], _rgba(color, 235))
    _poly(img, [(x - w * 0.46, y - h * 0.55), (x + w * 0.46, y - h * 0.55),
                (x + w * 0.44, y - h * 0.55 + top_h), (x - w * 0.44, y - h * 0.55 + top_h)],
          _rgba(mix(color, (255, 255, 255, 255), 0.22), 240))
    # 下沿
    _line(img, [(x - w * 0.52, y), (x + w * 0.52, y)], outline, max(3, ctx.S(0.006)))
    # 桌面道具：麦克风
    mx, my = x - w * 0.2, y - h * 0.55
    _line(img, [(mx, my), (mx, my - h * 0.42)], ctx.color("fg:0", alpha=230),
          max(3, ctx.S(0.007)))
    _ellipse(img, (mx - ctx.S(0.012), my - h * 0.42 - ctx.S(0.012),
                   mx + ctx.S(0.012), my - h * 0.42 + ctx.S(0.012)),
             ctx.color("fg:0", alpha=235))
    # 卡片/杯子
    _rect(img, (x + w * 0.08, my - ctx.S(0.03), x + w * 0.24, my - ctx.S(0.012)),
          ctx.color("accent:0", alpha=225), radius=int(ctx.S(0.004)))
    _rect(img, (x + w * 0.28, my - ctx.S(0.05), x + w * 0.4, my),
          ctx.color("highlight", alpha=220), radius=int(ctx.S(0.006)))
    _glow(img, ctx, [(mx, my - h * 0.45, ctx.S(0.05), ctx.S(0.05), 0.35)],
          ctx.color("highlight", alpha=255), power=2.0)


def sofa_set(img: Image.Image, spec: dict, ctx) -> None:
    """沙发组：靠背 + 两个座垫 + 扶手 + 侧桌。"""
    x = ctx.X(_f(spec, "x", 0.5))
    y = ctx.Y(_f(spec, "y", 0.84))
    w = ctx.S(_f(spec, "width", 0.34))
    h = ctx.S(_f(spec, "height", 0.2))
    color = ctx.spec_color(spec, "color", "accent:1")
    dark = _rgba(shade(color, 0.62), 240)
    light = _rgba(mix(color, (255, 255, 255, 255), 0.2), 235)
    # 靠背
    _rect(img, (x - w * 0.5, y - h, x + w * 0.5, y - h * 0.42), dark,
          radius=int(h * 0.16))
    # 座垫
    _rect(img, (x - w * 0.46, y - h * 0.5, x - w * 0.02, y - h * 0.06), light,
          radius=int(h * 0.12))
    _rect(img, (x + w * 0.02, y - h * 0.5, x + w * 0.46, y - h * 0.06), light,
          radius=int(h * 0.12))
    # 扶手
    _rect(img, (x - w * 0.54, y - h * 0.6, x - w * 0.44, y - h * 0.06), dark,
          radius=int(h * 0.1))
    _rect(img, (x + w * 0.44, y - h * 0.6, x + w * 0.54, y - h * 0.06), dark,
          radius=int(h * 0.1))
    # 底部阴影 + 腿
    _glow(img, ctx, [(x, y + ctx.S(0.01), w * 0.62, h * 0.16, 0.35)],
          ctx.color("shadow", alpha=255), power=1.6)
    for sx in (-0.42, 0.42):
        _line(img, [(x + w * sx, y - h * 0.08), (x + w * sx, y + h * 0.03)],
              ctx.color("shadow", alpha=220), max(3, ctx.S(0.007)))
    _line(img, [(x - w * 0.5, y - h * 0.42), (x + w * 0.5, y - h * 0.42)],
          ctx.color("shadow", alpha=120), max(2, ctx.S(0.004)))


def kitchen_counter(img: Image.Image, spec: dict, ctx) -> None:
    """厨房操作台：台面 + 柜门 + 水槽/锅具剪影。"""
    x = ctx.X(_f(spec, "x", 0.5))
    y = ctx.Y(_f(spec, "y", 0.82))
    w = ctx.S(_f(spec, "width", 0.6))
    color = ctx.spec_color(spec, "color", "accent:1")
    top = _rgba(mix(color, (255, 255, 255, 255), 0.35), 245)
    body = _rgba(shade(color, 0.7), 240)
    _rect(img, (x - w * 0.5, y - ctx.S(0.03), x + w * 0.5, y + ctx.S(0.12)), body)
    _rect(img, (x - w * 0.52, y - ctx.S(0.04), x + w * 0.52, y - ctx.S(0.015)),
          top, radius=int(ctx.S(0.004)))
    for k in range(3):
        dx = x - w * 0.32 + k * w * 0.32
        _rect(img, (dx - w * 0.13, y + ctx.S(0.02), dx + w * 0.13, y + ctx.S(0.1)),
              _rgba(shade(color, 0.58), 220), radius=int(ctx.S(0.004)))
        _line(img, [(dx - w * 0.05, y + ctx.S(0.055)), (dx + w * 0.05, y + ctx.S(0.055))],
              ctx.color("highlight", alpha=150), 3)
    # 锅/壶剪影
    _ellipse(img, (x - w * 0.36, y - ctx.S(0.09), x - w * 0.16, y - ctx.S(0.03)),
             ctx.color("shadow", alpha=230))
    _line(img, [(x - w * 0.3, y - ctx.S(0.12)), (x - w * 0.24, y - ctx.S(0.1))],
          ctx.color("highlight", alpha=80), 3)


def dressing_table(img: Image.Image, spec: dict, ctx) -> None:
    """化妆台：台面 + 镜框 + 四周灯泡 + 桌面小物。"""
    x = ctx.X(_f(spec, "x", 0.42))
    y = ctx.Y(_f(spec, "y", 0.84))
    w = ctx.S(_f(spec, "width", 0.34))
    h = ctx.S(_f(spec, "height", 0.16))
    color = ctx.spec_color(spec, "color", "accent:2")
    # 台面
    _rect(img, (x - w * 0.5, y - h, x + w * 0.5, y - h * 0.7),
          _rgba(mix(color, (255, 255, 255, 255), 0.3), 245))
    _rect(img, (x - w * 0.52, y - h * 0.72, x + w * 0.52, y), _rgba(shade(color, 0.66), 240))
    # 镜子
    mw, mh = w * 0.62, h * 3.0
    _rect(img, (x - mw * 0.5, y - h - mh, x + mw * 0.5, y - h - ctx.S(0.01)),
          _rgba(shade(color, 0.45), 235), radius=int(ctx.S(0.01)))
    mirror = Image.new("RGBA", (ctx.w, ctx.h), (0, 0, 0, 0))
    md = ImageDraw.Draw(mirror)
    md.rectangle((x - mw * 0.45, y - h - mh + ctx.S(0.01),
                  x + mw * 0.45, y - h - ctx.S(0.02)),
                 fill=_rgba(mix(ctx.color("highlight"), ctx.color("shadow"), 0.55), 190))
    img.alpha_composite(mirror)
    _screen_glint(img, ctx, (x - mw * 0.4, y - h - mh + ctx.S(0.02),
                             x + mw * 0.4, y - h - ctx.S(0.04)),
                  ctx.color("highlight", alpha=90))
    _rect(img, (x - mw * 0.5, y - h - mh, x + mw * 0.5, y - h - ctx.S(0.01)),
          (0, 0, 0, 0), radius=int(ctx.S(0.01)))
    _line(img, [(x - mw * 0.5, y - h - mh), (x + mw * 0.5, y - h - mh),
                (x + mw * 0.5, y - h - ctx.S(0.01)),
                (x - mw * 0.5, y - h - ctx.S(0.01)),
                (x - mw * 0.5, y - h - mh)],
          ctx.color("accent:0", alpha=200), max(2, int(ctx.S(0.005))))
    # 镜周灯泡
    bulbs = 7
    for k in range(bulbs):
        t = k / (bulbs - 1)
        bx = x - mw * 0.56 + mw * 1.12 * t
        by = y - h - mh - ctx.S(0.012)
        _ellipse(img, (bx - ctx.S(0.008), by - ctx.S(0.008),
                       bx + ctx.S(0.008), by + ctx.S(0.008)),
                 ctx.color("highlight", alpha=245))
        _glow(img, ctx, [(bx, by, ctx.S(0.03), ctx.S(0.03), 0.4)],
              ctx.color("accent:2", alpha=255), power=1.8)


def curtains(img: Image.Image, spec: dict, ctx) -> None:
    """侧幕/窗帘：竖向褶皱 + 顶幔。"""
    side = _s(spec, "side", "both")
    color = ctx.spec_color(spec, "color", "accent:1")
    h = ctx.h
    w = ctx.S(_f(spec, "width", 0.16))
    folds = max(4, _i(spec, "folds", 6))
    sides = [-1, 1] if side == "both" else ([1] if side == "right" else [-1])
    for sign in sides:
        x0 = 0 if sign < 0 else ctx.w - w
        panel = Image.new("RGBA", (ctx.w, ctx.h), (0, 0, 0, 0))
        d = ImageDraw.Draw(panel)
        d.rectangle((x0, 0, x0 + w, h), fill=_rgba(color, 220))
        for k in range(folds):
            fx = x0 + w * (k + 0.5) / folds
            fold_w = w / folds * 0.42
            shade_c = shade(color, 0.7 if k % 2 else 1.18)
            d.rectangle((fx - fold_w / 2, 0, fx + fold_w / 2, h),
                        fill=_rgba(shade_c, 120))
        img.alpha_composite(panel.filter(ImageFilter.GaussianBlur(0.6)))
        # 顶部幔
        _rect(img, (x0 - ctx.S(0.01), -ctx.S(0.01), x0 + w + ctx.S(0.01), ctx.S(0.1)),
              _rgba(shade(color, 0.6), 240), radius=int(ctx.S(0.02)))


# ---------------------------------------------------------------------------
# 3) 光效/材质（窗光/百叶/台灯/路灯/尘埃/扫描线）
# ---------------------------------------------------------------------------
def window_light(img: Image.Image, spec: dict, ctx) -> None:
    """窗光：窗口亮斑 + 斜射光束（暖光/胶片常用）。"""
    color = ctx.spec_color(spec, "color", "highlight")
    cx = ctx.X(_f(spec, "x", 0.5))
    cy = ctx.Y(_f(spec, "y", 0.4))
    size = ctx.S(_f(spec, "size", 0.5))
    pw = size * 0.42
    ph = size * 0.58

    def painter(draw: ImageDraw.ImageDraw, sw: int, sh: int) -> None:
        sx, sy = sw / float(max(1, ctx.w)), sh / float(max(1, ctx.h))
        draw.ellipse(((cx - pw) * sx, (cy - ph) * sx, (cx + pw) * sx, (cy + ph) * sx),
                     fill=color[:3] + (120,))
        # 斜射光束
        for k in range(3):
            off = (k - 1) * pw * 0.45
            draw.polygon([((cx + off) * sx, (cy - ph) * sx),
                          ((cx + off + pw * 0.5) * sx, (cy - ph) * sx),
                          ((cx + off + pw * 1.4) * sx, (cy + ph * 1.8) * sx),
                          ((cx + off + pw * 0.5) * sx, (cy + ph * 1.8) * sx)],
                         fill=color[:3] + (30,))

    img.alpha_composite(soft_layer((ctx.w, ctx.h), painter, blur=10, downscale=6))
    _glow(img, ctx, [(cx, cy, pw * 1.1, ph * 1.1, 0.55)], color[:3] + (255,),
          power=2.0, downscale=8)
    if spec.get("frame", True):
        frame = ctx.color("fg:1", alpha=120)
        _line(img, [(cx - pw, cy - ph), (cx + pw, cy - ph), (cx + pw, cy + ph),
                    (cx - pw, cy + ph), (cx - pw, cy - ph)], frame,
              max(2, ctx.S(0.003)))


def blinds(img: Image.Image, spec: dict, ctx) -> None:
    """百叶窗：横向叶片 + 透光缝 + 墙面条纹投影。"""
    box = ctx.box(spec, defaults=(0.55, 0.12, 0.4, 0.5))
    x0, y0, x1, y1 = [int(round(v)) for v in box]
    if x1 <= x0 or y1 <= y0:
        return
    slats = max(6, _i(spec, "slats", 14))
    color = ctx.spec_color(spec, "color", "highlight")
    dark = _rgba(ctx.color("shadow"), 220)
    layer = Image.new("RGBA", (ctx.w, ctx.h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.rectangle((x0, y0, x1, y1), fill=dark)
    step = max(2.0, (y1 - y0) / slats)
    for k in range(slats):
        sy = y0 + step * k
        d.rectangle((x0, sy + step * 0.18, x1, sy + step * 0.18 + max(1.0, step * 0.16)),
                    fill=_rgba(color, 150))
    img.alpha_composite(layer)
    # 窗框
    _line(img, [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)],
          ctx.color("fg:1", alpha=200), max(2, ctx.S(0.005)))
    # 墙上的斜影条
    for k in range(slats // 2):
        ty = y1 + (k + 1) * step * 1.4
        if ty > ctx.h:
            break
        _line(img, [(x0 - (x1 - x0) * 0.3, ty), (x1 + (x1 - x0) * 0.2, ty - step)],
              ctx.color("shadow", alpha=110), max(2, ctx.S(0.006)))


def street_lamp(img: Image.Image, spec: dict, ctx) -> None:
    """路灯：灯杆 + 灯头 + 地面光池。"""
    color = ctx.spec_color(spec, "color", "accent")
    x = ctx.X(_f(spec, "x", 0.2))
    y = ctx.Y(_f(spec, "y", 0.5))
    h = ctx.S(_f(spec, "height", 0.5))
    pole = ctx.color("fg:1", alpha=90)
    _line(img, [(x, y), (x, y - h)], pole, max(3, ctx.S(0.008)))
    _line(img, [(x, y - h), (x + ctx.S(0.05), y - h)], pole, max(3, ctx.S(0.007)))
    lx, ly = x + ctx.S(0.05), y - h
    _ellipse(img, (lx - ctx.S(0.016), ly - ctx.S(0.012),
                   lx + ctx.S(0.016), ly + ctx.S(0.012)),
             ctx.color("highlight", alpha=245))
    _glow(img, ctx, [(lx, ly, ctx.S(0.09), ctx.S(0.06), 0.85)],
          color[:3] + (255,), power=2.0, downscale=6)
    # 地面光池
    _glow(img, ctx, [(lx, y + ctx.S(0.02), ctx.S(0.22), ctx.S(0.05), 0.5)],
          color[:3] + (255,), power=1.6, downscale=8)


def desk_lamp(img: Image.Image, spec: dict, ctx) -> None:
    """桌面台灯：底座 + 灯罩 + 锥形光圈。"""
    color = ctx.spec_color(spec, "color", "accent")
    x = ctx.X(_f(spec, "x", 0.8))
    y = ctx.Y(_f(spec, "y", 0.7))
    s = ctx.S(_f(spec, "size", 0.12))
    metal = ctx.color("fg:1", alpha=210)
    _ellipse(img, (x - s * 0.35, y - s * 0.06, x + s * 0.35, y + s * 0.06),
             _rgba(ctx.color("shadow"), 230))
    _line(img, [(x, y - s * 0.05), (x, y - s * 0.75)], metal, max(2, ctx.S(0.006)))
    _poly(img, [(x - s * 0.3, y - s * 0.75), (x + s * 0.3, y - s * 0.75),
                (x + s * 0.2, y - s * 1.0), (x - s * 0.2, y - s * 1.0)],
          _rgba(mix(color, (255, 255, 255, 255), 0.35), 235))
    _glow(img, ctx, [(x, y - s * 0.82, s * 0.6, s * 0.5, 0.8)],
          color[:3] + (255,), power=2.2, downscale=8)

    def painter(draw: ImageDraw.ImageDraw, sw: int, sh: int) -> None:
        sx, sy = sw / float(max(1, ctx.w)), sh / float(max(1, ctx.h))
        draw.polygon([((x - s * 0.18) * sx, (y - s * 0.95) * sy),
                      ((x + s * 0.18) * sx, (y - s * 0.95) * sy),
                      ((x + s * 0.6) * sx, (y + s * 0.2) * sy),
                      ((x - s * 0.6) * sx, (y + s * 0.2) * sy)],
                     fill=color[:3] + (34,))

    img.alpha_composite(soft_layer((ctx.w, ctx.h), painter, blur=8, downscale=6))


def bokeh(img: Image.Image, spec: dict, ctx) -> None:
    """背景/前景散景光斑（胶片/夜场虚焦）。"""
    color = ctx.spec_color(spec, "color", "highlight")
    rng = _rng(ctx, "bokeh")
    density = max(0.05, _f(spec, "density", 0.25))
    count = int(26 * density * 10) + 6
    layer = Image.new("RGBA", (ctx.w, ctx.h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    for _ in range(count):
        px = rng.uniform(0.0, 1.0) * ctx.w
        py = rng.uniform(0.0, 0.9) * ctx.h
        r = ctx.S(rng.uniform(0.006, 0.035) * (0.6 + density))
        tint = mix(color, (255, 255, 255, 255), rng.uniform(0.0, 0.5))
        d.ellipse((px - r, py - r, px + r, py + r),
                  fill=_rgba(tint, int(rng.uniform(18, 70))))
    img.alpha_composite(layer.filter(ImageFilter.GaussianBlur(3.0)))
    for _ in range(4):
        px = rng.uniform(0.05, 0.95) * ctx.w
        py = rng.uniform(0.05, 0.85) * ctx.h
        r = ctx.S(rng.uniform(0.02, 0.05))
        _glow(img, ctx, [(px, py, r, r, 0.22)],
              mix(color, (255, 255, 255, 255), 0.3)[:3] + (255,),
              power=1.8, downscale=8)


def dust(img: Image.Image, spec: dict, ctx) -> None:
    """空气中漂浮的尘埃/光尘（胶片/暖光场景）。"""
    color = ctx.spec_color(spec, "color", "highlight")
    rng = _rng(ctx, "dust")
    count = max(8, _i(spec, "count", 60))
    layer = Image.new("RGBA", (ctx.w, ctx.h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    for _ in range(count):
        px = rng.uniform(0.0, 1.0) * ctx.w
        py = rng.uniform(0.0, 0.85) * ctx.h
        r = ctx.S(rng.uniform(0.0012, 0.004))
        a = int(rng.uniform(40, 150))
        d.ellipse((px - r, py - r, px + r, py + r), fill=color[:3] + (a,))
    img.alpha_composite(layer.filter(ImageFilter.GaussianBlur(0.7)))


def scanline(img: Image.Image, spec: dict, ctx) -> None:
    """扫描线：横向细线（VHS 用）。"""
    color = ctx.spec_color(spec, "color", "shadow")
    step = max(2, _i(spec, "step", 4))
    alpha = int(255 * _f(spec, "strength", 0.16))
    layer = Image.new("RGBA", (ctx.w, ctx.h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    for y in range(0, ctx.h, step):
        d.line([(0, y), (ctx.w, y)], fill=color[:3] + (alpha,), width=1)
    img.alpha_composite(layer)


# ---------------------------------------------------------------------------
# 4) 蓝图/白板/数据（blueprint_lab）
# ---------------------------------------------------------------------------
def whiteboard(img: Image.Image, spec: dict, ctx) -> None:
    """白板：白色板面 + 金属边框 + 马克笔草稿线。"""
    box = ctx.box(spec, defaults=(0.2, 0.1, 0.6, 0.6))
    x0, y0, x1, y1 = [int(round(v)) for v in box]
    if x1 <= x0 or y1 <= y0:
        return
    board = ctx.spec_color(spec, "color", "paper")
    _rect(img, (x0, y0, x1, y1), _rgba(mix(board, (255, 255, 255, 255), 0.55), 250))
    _glow(img, ctx, [((x0 + x1) / 2, (y0 + y1) / 2, (x1 - x0) * 0.55,
                      (y1 - y0) * 0.55, 0.25)],
          ctx.color("highlight", alpha=255), power=2.0, downscale=8)
    _line(img, [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)],
          ctx.color("fg:1", alpha=200), max(2, ctx.S(0.005)))
    # 草稿线：几条流程曲线 + 小圆点
    rng = _rng(ctx, "whiteboard")
    ink = ctx.color("accent:0", alpha=150)
    for k in range(3):
        base_y = y0 + (y1 - y0) * (0.26 + k * 0.24)
        pts = [(x0 + (x1 - x0) * (0.08 + t * 0.22),
                base_y + math.sin(t * 6 + k) * (y1 - y0) * 0.04)
               for t in range(6)]
        _line(img, pts, ink, max(2, ctx.S(0.004)))
        r = ctx.S(0.008)
        _ellipse(img, (pts[-1][0] - r, pts[-1][1] - r, pts[-1][0] + r, pts[-1][1] + r),
                 ctx.color("accent:1", alpha=220))
    # 板托
    _rect(img, (x0 + (x1 - x0) * 0.3, y1, x1 - (x1 - x0) * 0.3, y1 + ctx.S(0.02)),
          _rgba(ctx.color("fg:1"), 220))


def sticky_notes(img: Image.Image, spec: dict, ctx) -> None:
    """便利贴墙：小方块网格、轻微旋转与图钉。"""
    cx = ctx.X(_f(spec, "x", 0.6))
    cy = ctx.Y(_f(spec, "y", 0.3))
    size = ctx.S(_f(spec, "size", 0.2))
    color = ctx.spec_color(spec, "color", "accent:1")
    rng = _rng(ctx, "notes")
    cols, rows = 3, 2
    for r in range(rows):
        for c in range(cols):
            nx = cx + (c - (cols - 1) / 2.0) * size * 1.1 + rng.uniform(-2, 2)
            ny = cy + (r - (rows - 1) / 2.0) * size * 1.0 + rng.uniform(-2, 2)
            half = size * 0.4
            tint = mix(color, (255, 255, 255, 255), rng.uniform(0.0, 0.35))
            note = Image.new("RGBA", (ctx.w, ctx.h), (0, 0, 0, 0))
            ImageDraw.Draw(note).rectangle(
                (nx - half, ny - half * 0.85, nx + half, ny + half * 0.85),
                fill=_rgba(tint, 235))
            rotated = note.rotate(rng.uniform(-6, 6), center=(nx, ny),
                                  resample=Image.BILINEAR)
            img.alpha_composite(rotated)
            _line(img, [(nx - half * 0.5, ny - half * 0.2), (nx + half * 0.5, ny - half * 0.2)],
                  ctx.color("fg:1", alpha=110), max(1, int(ctx.S(0.002))))
            _ellipse(img, (nx - ctx.S(0.005), ny - half * 0.85 - ctx.S(0.005),
                           nx + ctx.S(0.005), ny - half * 0.85 + ctx.S(0.005)),
                     ctx.color("accent:0", alpha=240))


def diagram_nodes(img: Image.Image, spec: dict, ctx) -> None:
    """工程节点图：节点 + 连线 + 标签条（蓝图/数据讲解）。"""
    color = ctx.spec_color(spec, "color", "accent:0")
    cx = ctx.X(_f(spec, "x", 0.5))
    cy = ctx.Y(_f(spec, "y", 0.42))
    size = ctx.S(_f(spec, "size", 0.5))
    rng = _rng(ctx, "diagram")
    n = max(4, _i(spec, "nodes", 7))
    pts = []
    for k in range(n):
        ang = -math.pi * 0.85 + k * (math.pi * 1.7 / max(1, n - 1))
        rad = size * (0.32 + 0.16 * rng.random())
        px = cx + math.cos(ang) * rad * 1.5
        py = cy + math.sin(ang) * rad * 0.8
        pts.append((px, py))
    line_col = ctx.color("fg:1", alpha=150)
    # 用本地安全虚线（不用 scene_render.dashed_line：浮点相位在特定尺寸可能停滞）
    def _dash(p0, p1, color, width):
        dx, dy = p1[0] - p0[0], p1[1] - p0[1]
        seg = math.hypot(dx, dy)
        if seg <= 1e-6:
            return
        dash = max(4.0, ctx.S(0.02))
        gap = max(3.0, ctx.S(0.01))
        period = dash + gap
        pos = 0.0
        guard = 0
        while pos < seg and guard < 4000:
            guard += 1
            run = min(dash, seg - pos)
            t0, t1 = pos / seg, (pos + run) / seg
            _line(img, [(p0[0] + dx * t0, p0[1] + dy * t0),
                        (p0[0] + dx * t1, p0[1] + dy * t1)], color, width)
            pos += period
    for k in range(1, n):
        _dash(pts[k - 1], pts[k], line_col, max(1, int(ctx.S(0.003))))
    for k, (px, py) in enumerate(pts):
        r = ctx.S(0.022 if k == 0 else 0.015)
        _ellipse(img, (px - r, py - r, px + r, py + r), _rgba(color, 210))
        _line(img, [(px - r, py), (px + r, py)], ctx.color("highlight", alpha=170),
              max(1, int(ctx.S(0.002))))
        _glow(img, ctx, [(px, py, r * 2.4, r * 2.4, 0.25)], color[:3] + (255,),
              power=2.0, downscale=8)
        # 标签条
        _rect(img, (px + r * 1.2, py - r * 0.5, px + r * 3.6, py + r * 0.5),
              ctx.color("fg:1", alpha=70), radius=int(r * 0.4))


def monitor(img: Image.Image, spec: dict, ctx) -> None:
    """监视器/数据屏：深色屏 + 发光数据行 + 反光。"""
    x = ctx.X(_f(spec, "x", 0.8))
    y = ctx.Y(_f(spec, "y", 0.62))
    w = ctx.S(_f(spec, "width", 0.2))
    h = w * 0.58
    color = ctx.spec_color(spec, "color", "accent:1")
    _rect(img, (x - w * 0.5, y - h, x + w * 0.5, y), _rgba(ctx.color("shadow"), 240),
          radius=int(ctx.S(0.008)))
    _rect(img, (x - w * 0.46, y - h * 0.95, x + w * 0.46, y - h * 0.06),
          _rgba(shade(color, 0.35), 230), radius=int(ctx.S(0.006)))
    rng = _rng(ctx, "monitor")
    for k in range(6):
        ly = y - h * 0.85 + k * h * 0.14
        lw = w * rng.uniform(0.2, 0.82)
        _line(img, [(x - w * 0.42, ly), (x - w * 0.42 + lw, ly)],
              _rgba(color, 150), max(1, int(ctx.S(0.004))))
    _glow(img, ctx, [(x, y - h * 0.5, w * 0.75, h * 0.8, 0.35)],
          color[:3] + (255,), power=2.0, downscale=8)
    _screen_glint(img, ctx, (x - w * 0.46, y - h * 0.95, x + w * 0.46, y - h * 0.06),
                  ctx.color("highlight", alpha=34))
    # 支架
    _line(img, [(x, y), (x, y + ctx.S(0.02))], ctx.color("fg:1", alpha=200),
          max(2, int(ctx.S(0.006))))


# ---------------------------------------------------------------------------
# 5) 全息/舞台（hologram_stage）
# ---------------------------------------------------------------------------
def mirror_shards(img: Image.Image, spec: dict, ctx) -> None:
    """镜面碎片：随机多边形反光 + 亮边。"""
    color = ctx.spec_color(spec, "color", "highlight")
    rng = _rng(ctx, "shards")
    count = max(4, _i(spec, "count", 18))
    layer = Image.new("RGBA", (ctx.w, ctx.h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    for _ in range(count):
        cx = rng.uniform(0.05, 0.95) * ctx.w
        cy = rng.uniform(0.05, 0.9) * ctx.h
        sz = ctx.S(rng.uniform(0.02, 0.09))
        n = rng.randint(3, 5)
        pts = []
        for k in range(n):
            ang = rng.uniform(0, math.tau)
            rad = sz * rng.uniform(0.4, 1.0)
            pts.append((cx + math.cos(ang) * rad, cy + math.sin(ang) * rad))
        fill = _rgba(mix(color, (255, 255, 255, 255), rng.uniform(0.0, 0.5)),
                     int(rng.uniform(40, 120)))
        d.polygon(pts, fill=fill, outline=color[:3] + (170,))
    img.alpha_composite(layer.filter(ImageFilter.GaussianBlur(0.4)))
    _glow(img, ctx, [(ctx.w * 0.5, ctx.h * 0.5, ctx.w * 0.5, ctx.h * 0.4, 0.2)],
          color[:3] + (255,), power=2.0, downscale=10)


def audience_sea(img: Image.Image, spec: dict, ctx) -> None:
    """观众星海：无数手机/应援灯点 + 少量 bokeh。"""
    color = ctx.spec_color(spec, "color", "accent:1")
    rng = _rng(ctx, "audience")
    density = max(0.1, _f(spec, "density", 0.7))
    count = int(160 * density) + 20
    layer = Image.new("RGBA", (ctx.w, ctx.h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    for _ in range(count):
        px = rng.uniform(0.0, 1.0) * ctx.w
        py = rng.uniform(0.5, 1.0) * ctx.h
        r = ctx.S(rng.uniform(0.0015, 0.006))
        tint = mix(color, (255, 255, 255, 255), rng.uniform(0.0, 0.55))
        d.ellipse((px - r, py - r, px + r, py + r),
                  fill=_rgba(tint, int(rng.uniform(90, 220))))
    img.alpha_composite(layer.filter(ImageFilter.GaussianBlur(0.8)))
    # 前景 bokeh
    for _ in range(10):
        px = rng.uniform(0.0, 1.0) * ctx.w
        py = rng.uniform(0.45, 0.95) * ctx.h
        r = ctx.S(rng.uniform(0.012, 0.035))
        _glow(img, ctx, [(px, py, r, r, 0.35)],
              mix(color, (255, 255, 255, 255), 0.4)[:3] + (255,),
              power=1.8, downscale=8)


def light_tunnel(img: Image.Image, spec: dict, ctx) -> None:
    """光隧道：向心环形光带 + 中心辉光。"""
    color = ctx.spec_color(spec, "color", "accent:1")
    cx = ctx.X(_f(spec, "x", 0.5))
    cy = ctx.Y(_f(spec, "y", 0.5))
    rings = max(5, _i(spec, "rings", 12))
    layer = Image.new("RGBA", (ctx.w, ctx.h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    for k in range(rings):
        t = k / float(rings - 1)
        rx = ctx.S(0.62) * (1.0 - t) ** 1.7 + ctx.S(0.02)
        ry = rx * 0.62
        a = int(150 * (1.0 - t) ** 1.4)
        tint = mix(color, (255, 255, 255, 255), 0.25 + 0.4 * t)
        d.ellipse((cx - rx, cy - ry, cx + rx, cy + ry),
                  outline=_rgba(tint, a), width=max(1, int(ctx.S(0.004))))
    img.alpha_composite(layer.filter(ImageFilter.GaussianBlur(1.0)))
    _glow(img, ctx, [(cx, cy, ctx.S(0.1), ctx.S(0.1), 0.9),
                     (cx, cy, ctx.S(0.3), ctx.S(0.24), 0.35)],
          color[:3] + (255,), power=2.2, downscale=8)


def stage_floor(img: Image.Image, spec: dict, ctx) -> None:
    """舞台地面/湿地面：地平线、透视地砖/反光条与台沿高光。"""
    color = ctx.spec_color(spec, "color", "paper")
    horizon = _f(spec, "horizon", 0.72)
    hy = max(1.0, min(ctx.h - 1.0, ctx.Y(horizon)))
    floor_h = max(2, int(ctx.h - hy))
    mix_c = mix(color, ctx.color("highlight"), 0.16)
    grad = vgradient(ctx.w, floor_h, _rgba(mix_c, 230), _rgba(shade(color, 0.32), 248))
    img.alpha_composite(grad, dest=(0, int(hy)))
    # 地平线反光带（舞台灯/城市光）
    img.alpha_composite(glow_layer(
        (ctx.w, ctx.h), [(ctx.w * 0.5, hy, ctx.w * 0.62, ctx.S(0.045), 0.65)],
        ctx.color("highlight"), power=1.7, downscale=8))
    # 透视地缝
    line_col = ctx.color("highlight", alpha=64)
    cx = ctx.w * 0.5
    for k in range(-8, 9):
        x_end = cx + k * ctx.w * 0.11
        _line(img, [(cx, hy), (x_end, ctx.h)], line_col, max(1, int(ctx.S(0.0022))))
    y = hy + (ctx.h - hy) * 0.10
    step = max(6.0, (ctx.h - hy) * 0.055)
    while y < ctx.h:
        _line(img, [(0, y), (ctx.w, y)],
              ctx.color("highlight",
                        alpha=max(18, int(70 * (1 - (y - hy) / max(1.0, ctx.h - hy))))),
              max(1, int(ctx.S(0.0018))))
        y += step
        step *= 1.22
    # 台沿高光 + 灯位倒影竖条
    _line(img, [(0, hy), (ctx.w, hy)], ctx.color("highlight", alpha=150),
          max(2, int(ctx.S(0.006))))
    rng = _rng(ctx, "floor-reflect")
    for _ in range(6):
        rx = rng.uniform(0.06, 0.94) * ctx.w
        rh = (ctx.h - hy) * rng.uniform(0.18, 0.55)
        _line(img, [(rx, hy + 2), (rx, min(ctx.h - 1, hy + rh))],
              ctx.color("highlight", alpha=int(rng.uniform(14, 40))),
              max(2, int(ctx.S(0.008))))


def comedy_stage(img: Image.Image, spec: dict, ctx) -> None:
    """喜剧俱乐部舞台：砖拱门 + 侧幕 + 麦克风架 + 台面。"""
    color = ctx.spec_color(spec, "color", "accent:2")
    x = ctx.X(_f(spec, "x", 0.5))
    y = ctx.Y(_f(spec, "y", 0.84))
    w = ctx.S(_f(spec, "width", 0.5))
    h = ctx.S(_f(spec, "height", 0.34))
    # 砖拱
    arch = Image.new("RGBA", (ctx.w, ctx.h), (0, 0, 0, 0))
    d = ImageDraw.Draw(arch)
    d.arc((x - w * 0.5, y - h, x + w * 0.5, y + h * 0.2), 180, 360,
          fill=_rgba(shade(color, 0.75), 240), width=max(4, int(ctx.S(0.02))))
    d.arc((x - w * 0.44, y - h * 0.94, x + w * 0.44, y + h * 0.16), 180, 360,
          fill=_rgba(mix(color, (255, 255, 255, 255), 0.18), 200),
          width=max(2, int(ctx.S(0.008))))
    img.alpha_composite(arch)
    # 台面
    _rect(img, (x - w * 0.52, y, x + w * 0.52, y + ctx.S(0.05)),
          _rgba(shade(color, 0.5), 245), radius=int(ctx.S(0.006)))
    _line(img, [(x - w * 0.5, y), (x + w * 0.5, y)],
          ctx.color("accent:2", alpha=200), max(2, int(ctx.S(0.006))))
    # 麦克风架
    mx, my = x + w * 0.06, y
    _line(img, [(mx, my), (mx, my - h * 0.62)], ctx.color("fg:0", alpha=235),
          max(3, int(ctx.S(0.008))))
    _ellipse(img, (mx - ctx.S(0.014), my - h * 0.62 - ctx.S(0.014),
                   mx + ctx.S(0.014), my - h * 0.62 + ctx.S(0.014)),
             ctx.color("fg:0", alpha=240))
    _glow(img, ctx, [(mx, my - h * 0.62, ctx.S(0.06), ctx.S(0.05), 0.3)],
          ctx.color("highlight", alpha=255), power=2.0)
    # 侧幕
    for sign in (-1, 1):
        sx = x + sign * w * 0.62
        _poly(img, [(sx, y - h * 0.9), (sx + sign * ctx.S(0.06), y - h * 0.9),
                    (sx + sign * ctx.S(0.1), y + ctx.S(0.04)),
                    (sx, y + ctx.S(0.04))],
              _rgba(shade(ctx.color("accent:0"), 0.8), 200))


def film_strip(img: Image.Image, spec: dict, ctx) -> None:
    """胶片条：横贯画面的胶片带 + 齿孔 + 分格窗。"""
    color = ctx.spec_color(spec, "color", "fg:0")
    cy = ctx.Y(_f(spec, "y", 0.5))
    h = ctx.S(_f(spec, "height", 0.26))
    y0, y1 = cy - h / 2, cy + h / 2
    # 胶片带底色（暖黑）
    band = Image.new("RGBA", (ctx.w, ctx.h), (0, 0, 0, 0))
    d = ImageDraw.Draw(band)
    d.rectangle((0, y0, ctx.w, y1), fill=_rgba(shade(color, 0.55), 235))
    # 画面窗（每格略亮的暖色块 + 暗边）
    frame_w = ctx.S(0.20)
    gap = ctx.S(0.018)
    x = gap
    rng = _rng(ctx, "filmframes")
    while x + frame_w < ctx.w:
        win = (x, y0 + h * 0.2, x + frame_w, y1 - h * 0.2)
        tint = mix(ctx.color("paper"), ctx.color("highlight"),
                   rng.uniform(0.05, 0.35))
        d.rectangle(win, fill=_rgba(tint, 200))
        d.rectangle(win, outline=_rgba(shade(color, 0.3), 220),
                    width=max(1, int(ctx.S(0.003))))
        # 窗内剪影（竖向虚焦亮斑）
        for _ in range(3):
            sx = rng.uniform(win[0] + 4, win[2] - 4)
            sw = max(2.0, (win[2] - win[0]) * rng.uniform(0.04, 0.12))
            d.rectangle((sx, win[1] + 3, sx + sw, win[3] - 3),
                        fill=_rgba(shade(tint, 0.8), int(rng.uniform(30, 70))))
        x += frame_w + gap
    # 齿孔（纸张亮色，两排）
    hole_w, hole_h = ctx.S(0.021), ctx.S(0.024)
    step = hole_w * 2.0
    hx = hole_w * 0.8
    while hx < ctx.w:
        for hy in (y0 + hole_h * 0.6, y1 - hole_h * 0.6):
            d.rounded_rectangle((hx - hole_w * 0.5, hy - hole_h * 0.5,
                                 hx + hole_w * 0.5, hy + hole_h * 0.5),
                                radius=int(hole_w * 0.25),
                                fill=_rgba(ctx.color("paper"), 225))
        hx += step
    # 分格线
    gx = 0.0
    while gx < ctx.w:
        d.line([(gx, y0), (gx, y1)], fill=_rgba(shade(color, 0.3), 200),
               width=max(1, int(ctx.S(0.002))))
        gx += frame_w + gap
    img.alpha_composite(band)
    # 边缘高光
    _line(img, [(0, y0), (ctx.w, y0)], ctx.color("highlight", alpha=80),
          max(1, int(ctx.S(0.002))))
    _line(img, [(0, y1), (ctx.w, y1)], ctx.color("highlight", alpha=80),
          max(1, int(ctx.S(0.002))))


def vhs_osd(img: Image.Image, spec: dict, ctx) -> None:
    """VHS OSD：屏幕字（PLAY/REC/时间码）+ 磁带噪点条。"""
    color = ctx.spec_color(spec, "color", "accent:1")
    x = ctx.X(_f(spec, "x", 0.16))
    y = ctx.Y(_f(spec, "y", 0.12))
    size = max(10, int(ctx.S(_f(spec, "size", 0.05))))
    text = _s(spec, "text", "PLAY")
    draw = ImageDraw.Draw(img)
    ok = draw_text(draw, (int(x), int(y)), text, size,
                   color[:3] + (235,), anchor="lm", display=True)
    if not ok:  # 无字体：用几何条代替
        _rect(img, (x, y - size * 0.4, x + size * 3.2, y + size * 0.4),
              _rgba(color, 190))
    stamp = _s(spec, "stamp", "SP 0:00:12")
    draw_text(draw, (int(ctx.X(0.94)), int(ctx.Y(0.92))), stamp,
              max(9, int(size * 0.7)), ctx.color("fg:0", alpha=200),
              anchor="rm", display=False)
    # REC 红点
    rx, ry = ctx.X(_f(spec, "rec_x", 0.08)), ctx.Y(_f(spec, "rec_y", 0.13))
    _ellipse(img, (rx - size * 0.16, ry - size * 0.16,
                   rx + size * 0.16, ry + size * 0.16),
             ctx.color("accent:0", alpha=245))
    # 磁带噪点条
    rng = _rng(ctx, "osd")
    ny = rng.uniform(0.05, 0.95)
    for _ in range(40):
        nx = rng.uniform(0.0, 1.0) * ctx.w
        nw = ctx.S(rng.uniform(0.001, 0.02))
        _line(img, [(nx, ny * ctx.h), (nx + nw, ny * ctx.h)],
              ctx.color("highlight", alpha=int(rng.uniform(30, 110))), 1)


# ---------------------------------------------------------------------------
# 注册（import 时执行；scene_render._load_extensions 会加载本模块）
# ---------------------------------------------------------------------------
_MODERN_PRIMITIVES = {
    "neon_tube": neon_tube,
    "glass_wall": glass_wall,
    "studio_desk": studio_desk,
    "sofa_set": sofa_set,
    "kitchen_counter": kitchen_counter,
    "dressing_table": dressing_table,
    "curtains": curtains,
    "window_light": window_light,
    "blinds": blinds,
    "street_lamp": street_lamp,
    "desk_lamp": desk_lamp,
    "dust": dust,
    "bokeh": bokeh,
    "scanline": scanline,
    "whiteboard": whiteboard,
    "sticky_notes": sticky_notes,
    "diagram_nodes": diagram_nodes,
    "monitor": monitor,
    "mirror_shards": mirror_shards,
    "audience_sea": audience_sea,
    "light_tunnel": light_tunnel,
    "stage_floor": stage_floor,
    "comedy_stage": comedy_stage,
    "film_strip": film_strip,
    "vhs_osd": vhs_osd,
}

for _name, _fn in _MODERN_PRIMITIVES.items():
    register(_name, _fn)
