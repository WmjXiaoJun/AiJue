"""studio2d.shapekit - colors / geometry / drawing primitives.

Only depends on Pillow. Every image is drawn on a 4x supersampled canvas and
downsampled with LANCZOS so lines come out smooth (anime cel look).
"""

from __future__ import annotations

import math

from PIL import Image, ImageDraw, ImageFilter, ImageChops, ImageFont

SS = 4


# ------------------------------------------------------------------ colors

def hex2rgb(value):
    if isinstance(value, (tuple, list)):
        return (float(value[0]), float(value[1]), float(value[2]))
    s = str(value).strip()
    if s.startswith('#'):
        s = s[1:]
    if len(s) == 3:
        s = s[0] * 2 + s[1] * 2 + s[2] * 2
    return (float(int(s[0:2], 16)), float(int(s[2:4], 16)), float(int(s[4:6], 16)))


def rgb2hex(c):
    r, g, b = [int(round(max(0, min(255, v)))) for v in c[:3]]
    return '#%02X%02X%02X' % (r, g, b)


def mix(a, b, t):
    a = hex2rgb(a)
    b = hex2rgb(b)
    t = max(0.0, min(1.0, t))
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t)


def lighten(c, t):
    return mix(c, (255.0, 255.0, 255.0), t)


def darken(c, t):
    return mix(c, (0.0, 0.0, 0.0), t)


def desaturate(c, t):
    g = hex2rgb(c)
    gray = 0.299 * g[0] + 0.587 * g[1] + 0.114 * g[2]
    return mix(g, (gray, gray, gray), t)


def col(c, a=255):
    r, g, b = hex2rgb(c)
    return (int(round(r)), int(round(g)), int(round(b)), int(round(a)))


# ------------------------------------------------------------------ geometry

def catmull(points, subdiv=10, closed=True):
    """Sample a Catmull-Rom spline through points."""
    pts = [(float(p[0]), float(p[1])) for p in points]
    n = len(pts)
    if n < 3:
        return pts
    if closed:
        ext = [pts[-1]] + pts + [pts[0], pts[1]]
        segs = n
    else:
        ext = [pts[0]] + pts + [pts[-1]]
        segs = n - 1
    out = []
    for i in range(segs):
        p0, p1, p2, p3 = ext[i], ext[i + 1], ext[i + 2], ext[i + 3]
        for j in range(subdiv):
            t = j / float(subdiv)
            t2 = t * t
            t3 = t2 * t
            x = 0.5 * (2 * p1[0] + (-p0[0] + p2[0]) * t
                       + (2 * p0[0] - 5 * p1[0] + 4 * p2[0] - p3[0]) * t2
                       + (-p0[0] + 3 * p1[0] - 3 * p2[0] + p3[0]) * t3)
            y = 0.5 * (2 * p1[1] + (-p0[1] + p2[1]) * t
                       + (2 * p0[1] - 5 * p1[1] + 4 * p2[1] - p3[1]) * t2
                       + (-p0[1] + 3 * p1[1] - 3 * p2[1] + p3[1]) * t3)
            out.append((x, y))
    if not closed:
        out.append(pts[-1])
    return out


def quad_curve(p0, pc, p1, n=16):
    out = []
    for i in range(n + 1):
        t = i / float(n)
        u = 1.0 - t
        out.append((u * u * p0[0] + 2 * u * t * pc[0] + t * t * p1[0],
                    u * u * p0[1] + 2 * u * t * pc[1] + t * t * p1[1]))
    return out


def strand(root_l, tip, root_r, bulge_l=0.0, bulge_r=0.0, n=18):
    """A single pointed hair strand: curved left edge, curved right edge, sharp tip."""
    def ctrl(root, bulge):
        dx = tip[0] - root[0]
        dy = tip[1] - root[1]
        L = math.hypot(dx, dy) or 1e-6
        nx, ny = -dy / L, dx / L
        mx = (root[0] + tip[0]) * 0.5
        my = (root[1] + tip[1]) * 0.5
        return (mx + nx * bulge, my + ny * bulge)

    left = quad_curve(root_l, ctrl(root_l, bulge_l), tip, n)
    right = quad_curve(root_r, ctrl(root_r, bulge_r), tip, n)[::-1]
    return left + right[1:]


def resample(path, n=24):
    pts = list(path)
    if len(pts) < 2:
        return pts * max(1, n) if pts else []
    acc = [0.0]
    for i in range(1, len(pts)):
        acc.append(acc[-1] + math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]))
    total = acc[-1] or 1e-6
    out = []
    j = 0
    for k in range(n):
        target = total * k / float(n - 1)
        while j < len(acc) - 2 and acc[j + 1] < target:
            j += 1
        seg = (acc[j + 1] - acc[j]) or 1e-6
        t = (target - acc[j]) / seg
        x = pts[j][0] + (pts[j + 1][0] - pts[j][0]) * t
        y = pts[j][1] + (pts[j + 1][1] - pts[j][1]) * t
        out.append((x, y))
    return out


def profile_width(profile, t):
    if t <= profile[0][0]:
        return profile[0][1]
    for i in range(1, len(profile)):
        if t <= profile[i][0]:
            t0, w0 = profile[i - 1]
            t1, w1 = profile[i]
            if t1 - t0 <= 1e-9:
                return w1
            return w0 + (w1 - w0) * (t - t0) / (t1 - t0)
    return profile[-1][1]


def taper(path, profile, n=30, smooth=True, subdiv=12):
    """Variable width brush stroke along a smooth path -> closed polygon."""
    if smooth:
        path = catmull(path, subdiv, False)
    pts = resample(path, n)
    out_l = []
    out_r = []
    for i, p in enumerate(pts):
        if i == 0:
            a, b = pts[0], pts[1]
        elif i == n - 1:
            a, b = pts[n - 2], pts[n - 1]
        else:
            a, b = pts[i - 1], pts[i + 1]
        dx = b[0] - a[0]
        dy = b[1] - a[1]
        L = math.hypot(dx, dy) or 1e-6
        nx, ny = -dy / L, dx / L
        w = profile_width(profile, i / float(max(1, n - 1))) * 0.5
        out_l.append((p[0] + nx * w, p[1] + ny * w))
        out_r.append((p[0] - nx * w, p[1] - ny * w))
    return out_l + out_r[::-1]


def ribbon(path, widths, smooth=False, subdiv=10):
    if smooth:
        path = catmull(path, subdiv, False)
        n = len(path)
        ws = []
        for i in range(n):
            t = i / float(max(1, n - 1))
            k = t * (len(widths) - 1)
            i0 = int(math.floor(k))
            i1 = min(len(widths) - 1, i0 + 1)
            ws.append(widths[i0] + (widths[i1] - widths[i0]) * (k - i0))
        widths = ws
    n = min(len(path), len(widths))
    left = []
    right = []
    for i in range(n):
        p = path[i]
        w = widths[i] * 0.5
        if i == 0:
            a, b = path[0], path[1]
        elif i == n - 1:
            a, b = path[n - 2], path[n - 1]
        else:
            a, b = path[i - 1], path[i + 1]
        dx = b[0] - a[0]
        dy = b[1] - a[1]
        L = math.hypot(dx, dy) or 1e-6
        nx, ny = -dy / L, dx / L
        left.append((p[0] + nx * w, p[1] + ny * w))
        right.append((p[0] - nx * w, p[1] - ny * w))
    return left + right[::-1]


def mirror_loop(right_half):
    """right_half starts on the symmetry axis (top) and ends on it (bottom)."""
    full = list(right_half)
    for x, y in reversed(right_half[1:-1]):
        full.append((-x, y))
    return full


def star_points(cx, cy, r_out, r_in, n=5, rot=-math.pi / 2):
    pts = []
    for i in range(n * 2):
        r = r_out if i % 2 == 0 else r_in
        a = rot + i * math.pi / n
        pts.append((cx + math.cos(a) * r, cy + math.sin(a) * r))
    return pts


# ------------------------------------------------------------------ gradients

def vgradient(size, c0, c1):
    w, h = max(1, int(size[0])), max(1, int(size[1]))
    strip = Image.new("RGB", (1, h))
    p = strip.load()
    a = hex2rgb(c0)
    b = hex2rgb(c1)
    for y in range(h):
        t = y / float(max(1, h - 1))
        p[0, y] = (int(round(a[0] + (b[0] - a[0]) * t)),
                   int(round(a[1] + (b[1] - a[1]) * t)),
                   int(round(a[2] + (b[2] - a[2]) * t)))
    return strip.resize((w, h), Image.BILINEAR)


def radial_mask(size, power=1.6):
    w, h = max(1, int(size[0])), max(1, int(size[1]))
    sw = max(8, min(128, w))
    sh = max(8, min(128, h))
    m = Image.new("L", (sw, sh), 0)
    p = m.load()
    for yy in range(sh):
        dy = (yy + 0.5) / sh * 2.0 - 1.0
        for xx in range(sw):
            dx = (xx + 0.5) / sw * 2.0 - 1.0
            v = max(0.0, 1.0 - math.hypot(dx, dy))
            p[xx, yy] = int(round(255 * (v ** power)))
    return m.resize((w, h), Image.BICUBIC)


# ------------------------------------------------------------------ fonts

_FONT_CACHE = {}


def load_font(size_px, bold=False):
    key = (int(size_px), bool(bold))
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]
    cands = []
    if bold:
        cands += ["C:/Windows/Fonts/msyhbd.ttc", "C:/Windows/Fonts/msyhbd.ttf",
                  "C:/Windows/Fonts/simhei.ttf"]
    cands += ["C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/msyh.ttf",
              "C:/Windows/Fonts/simhei.ttf", "C:/Windows/Fonts/simsun.ttc"]
    font = None
    for path in cands:
        try:
            font = ImageFont.truetype(path, int(size_px))
            break
        except Exception:
            continue
    if font is None:
        font = ImageFont.load_default()
    _FONT_CACHE[key] = font
    return font


# ------------------------------------------------------------------ scene

class Scene:
    """Supersampled RGBA canvas with a head-unit -> pixel transform.

    注意：Pillow 在 RGBA 画布上直接绘制半透明色是「替换像素」而不是混合，
    因此所有带 alpha 的图元都会先画进局部图层再 alpha_composite，保证正确混合。
    """

    def __init__(self, width, height, ox, oy, scale, ss=SS, background=None):
        self.w = int(width)
        self.h = int(height)
        self.ss = int(ss)
        self.W = self.w * self.ss
        self.H = self.h * self.ss
        self.ox = float(ox) * self.ss
        self.oy = float(oy) * self.ss
        self.s = float(scale) * self.ss
        if background is None:
            self.img = Image.new("RGBA", (self.W, self.H), (0, 0, 0, 0))
        else:
            bg = background.convert("RGBA")
            if bg.size != (self.W, self.H):
                bg = bg.resize((self.W, self.H), Image.LANCZOS)
            self.img = bg
        self.draw = ImageDraw.Draw(self.img, "RGBA")

    # -- transforms
    def P(self, x, y):
        return (self.ox + float(x) * self.s, self.oy + float(y) * self.s)

    def PT(self, pts):
        return [self.P(x, y) for x, y in pts]

    def U(self, v):
        return float(v) * self.s

    def W_(self, v):
        return max(1, int(round(float(v) * self.s)))

    def units_bottom(self, extra=0.0):
        return (self.H - self.oy) / self.s + extra

    # -- color
    @staticmethod
    def _c(c):
        if c is None:
            return None
        if isinstance(c, str):
            return col(c)
        if len(c) == 3:
            return col(c)
        return (int(c[0]), int(c[1]), int(c[2]), int(max(0, min(255, c[3]))))

    @staticmethod
    def _alpha(c):
        return c is not None and len(c) == 4 and c[3] < 255

    def _paint(self, bbox, fn):
        x0 = max(0, int(math.floor(bbox[0])))
        y0 = max(0, int(math.floor(bbox[1])))
        x1 = min(self.W, int(math.ceil(bbox[2])) + 1)
        y1 = min(self.H, int(math.ceil(bbox[3])) + 1)
        w, h = x1 - x0, y1 - y0
        if w <= 0 or h <= 0:
            return
        layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer, "RGBA")
        fn(d, x0, y0)
        self.img.alpha_composite(layer, dest=(x0, y0))

    # -- primitives (all coordinates in head units)
    def poly(self, pts, fill=None, outline=None, width=0.02, smooth=True, subdiv=10):
        p = catmull(pts, subdiv, True) if (smooth and len(pts) >= 3) else list(pts)
        P = self.PT(p)
        if len(P) < 3:
            return
        f = self._c(fill)
        o = self._c(outline)
        w = self.W_(width) if o is not None else 0
        if not (self._alpha(f) or self._alpha(o)):
            if f is not None:
                self.draw.polygon(P, fill=f)
            if o is not None and w > 0:
                self.draw.line(P + [P[0]], fill=o, width=w, joint="curve")
            return
        xs = [q[0] for q in P]
        ys = [q[1] for q in P]
        pad = w + 3
        bbox = (min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad)

        def fn(d, dx, dy):
            LP = [(q[0] - dx, q[1] - dy) for q in P]
            if f is not None:
                d.polygon(LP, fill=f)
            if o is not None and w > 0:
                d.line(LP + [LP[0]], fill=o, width=w, joint="curve")

        self._paint(bbox, fn)

    def stroke(self, pts, color, width=0.02, smooth=False, closed=False, subdiv=10):
        p = catmull(pts, subdiv, closed) if (smooth and len(pts) >= 3) else list(pts)
        P = self.PT(p)
        if len(P) < 2:
            return
        c = self._c(color)
        w = self.W_(width)
        if closed:
            P = P + [P[0]]
        if not self._alpha(c):
            self.draw.line(P, fill=c, width=w, joint="curve")
            return
        xs = [q[0] for q in P]
        ys = [q[1] for q in P]
        pad = w + 3
        bbox = (min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad)

        def fn(d, dx, dy):
            d.line([(q[0] - dx, q[1] - dy) for q in P], fill=c, width=w, joint="curve")

        self._paint(bbox, fn)

    def ellipse(self, cx, cy, rx, ry, fill=None, outline=None, width=0.02):
        x0, y0 = self.P(cx - rx, cy - ry)
        x1, y1 = self.P(cx + rx, cy + ry)
        f = self._c(fill)
        o = self._c(outline)
        w = self.W_(width) if o is not None else 0
        if not (self._alpha(f) or self._alpha(o)):
            self.draw.ellipse([x0, y0, x1, y1], fill=f, outline=o, width=w)
            return

        def fn(d, dx, dy):
            d.ellipse([x0 - dx, y0 - dy, x1 - dx, y1 - dy], fill=f, outline=o, width=w)

        pad = max(4, w + 3)
        self._paint((x0 - pad, y0 - pad, x1 + pad, y1 + pad), fn)

    def rrect(self, x0, y0, x1, y1, r, fill=None, outline=None, width=0.02):
        p0 = self.P(x0, y0)
        p1 = self.P(x1, y1)
        f = self._c(fill)
        o = self._c(outline)
        w = self.W_(width) if o is not None else 0
        box = [p0[0], p0[1], p1[0], p1[1]]
        radius = self.U(r)
        if not (self._alpha(f) or self._alpha(o)):
            self.draw.rounded_rectangle(box, radius=radius, fill=f, outline=o, width=w)
            return

        def fn(d, dx, dy):
            d.rounded_rectangle([box[0] - dx, box[1] - dy, box[2] - dx, box[3] - dy],
                                radius=radius, fill=f, outline=o, width=w)

        pad = max(4, w + 3)
        self._paint((box[0] - pad, box[1] - pad, box[2] + pad, box[3] + pad), fn)

    def glow(self, cx, cy, rx, ry, color, alpha=90, power=1.8, pad=0.35):
        x0 = int(max(0, math.floor(self.P(cx - rx - pad, cy - ry - pad)[0])))
        y0 = int(max(0, math.floor(self.P(cx - rx - pad, cy - ry - pad)[1])))
        x1 = int(min(self.W, math.ceil(self.P(cx + rx + pad, cy + ry + pad)[0])))
        y1 = int(min(self.H, math.ceil(self.P(cx + rx + pad, cy + ry + pad)[1])))
        w, h = x1 - x0, y1 - y0
        if w < 2 or h < 2:
            return
        m = radial_mask((w, h), power=power)
        a = m.point(lambda v: int(v * alpha / 255.0))
        tint = Image.new("RGBA", (w, h), col(color, 255))
        tint.putalpha(a)
        self.img.alpha_composite(tint, dest=(x0, y0))

    def layer(self):
        return Image.new("RGBA", (self.W, self.H), (0, 0, 0, 0))

    def paste(self, layer, dest=None):
        if dest is None:
            self.img.alpha_composite(layer)
        else:
            self.img.alpha_composite(layer, dest=(int(dest[0]), int(dest[1])))

    def mask(self, pts, smooth=True, subdiv=10, invert=False):
        m = Image.new("L", (self.W, self.H), 255 if invert else 0)
        p = catmull(pts, subdiv, True) if smooth else list(pts)
        ImageDraw.Draw(m).polygon(self.PT(p), fill=0 if invert else 255)
        return m

    def clip(self, layer, mask):
        layer.putalpha(ImageChops.multiply(layer.getchannel("A"), mask))
        self.img.alpha_composite(layer)

    # -- text (coordinates in final pixels)
    def text(self, x, y, s, size, fill, bold=False, anchor="la"):
        font = load_font(size * self.ss, bold=bold)
        c = self._c(fill)
        if c is not None and len(c) == 4 and c[3] < 255:
            probe = self.draw.textbbox((0, 0), s, font=font, anchor=anchor)
            pad = 8
            bbox = (x * self.ss - abs(probe[0]) - pad, y * self.ss - abs(probe[1]) - pad,
                    x * self.ss + abs(probe[2]) + pad, y * self.ss + abs(probe[3]) + pad)

            def fn(d, dx, dy):
                d.text((x * self.ss - dx, y * self.ss - dy), s, font=font, fill=c,
                       anchor=anchor)

            self._paint(bbox, fn)
            return
        self.draw.text((x * self.ss, y * self.ss), s, font=font, fill=c, anchor=anchor)

    def text_width(self, s, size, bold=False):
        font = load_font(size * self.ss, bold=bold)
        return font.getlength(s) / float(self.ss)

    def finish(self):
        return self.img.resize((self.w, self.h), Image.LANCZOS)
