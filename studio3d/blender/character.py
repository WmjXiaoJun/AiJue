"""Procedural anime character builder (Blender / bpy).

Builds a stylised, completely original character from a JSON spec:
head, layered anime eyes, brows, mouth, hair styles, body, outfit, accessories.
"""
import bpy
import bmesh
import math
from mathutils import Vector, Matrix, Euler

from lib import (
    TAU, ellipsoid, cylinder, box, tube, star, sphere_cap,
    add_outline, mesh_object, make_toon, make_flat, make_gradient,
    make_outline_mat, make_paint, link_obj, hex_lin,
)
from presets import palette_for, lighten, shade, mix


def sm(e0, e1, x):
    if e0 == e1:
        return 0.0 if x < e0 else 1.0
    t = (x - e0) / (e1 - e0)
    t = max(0.0, min(1.0, t))
    return t * t * (3.0 - 2.0 * t)


def lerp(a, b, t):
    return a + (b - a) * t


def rot_euler(rx=0.0, ry=0.0, rz=0.0):
    return Euler((rx, ry, rz)).to_matrix().to_4x4()


class Character:
    def __init__(self, spec, style_cfg, coll, root):
        self.spec = spec
        self.cfg = style_cfg
        self.coll = coll
        self.root = root
        self.pal = palette_for(spec)
        self.ap = spec["appearance"]
        self.outfit = self.ap["outfit"]
        self.mat_cache = {}
        self.outline_src = []
        self.outline_objs = []
        self.expression = "neutral"
        self.H = 1.7 * float(self.ap.get("height_ratio", 1.0))
        chibi = max(0.0, min(0.4, float(self.ap.get("chibi", 0.25))))
        self.heads = 6.2 - 4.0 * chibi
        self.R = 0.5 * (self.H / self.heads) * float(self.ap.get("head_scale", 1.1))
        self.z_h = self.H - 0.92 * self.R
        self.gender = self.ap.get("gender", "neutral")
        self.eye_size = float(self.ap.get("eye_size", 1.0))
        self.eye_z = self.z_h - 0.02 * self.R
        self.brow_z = self.z_h + 0.25 * self.R
        self.mouth_z = self.z_h - 0.46 * self.R
        self.nose_z = self.z_h - 0.27 * self.R
        self.eye_age = max(0.78, 1.0 - 0.006 * max(0.0, float(self.ap.get("age", 30)) - 25.0))
        self.shoulder_z = 0.745 * self.H
        self.outline_mat = make_outline_mat(self.pal["outline"])

    # ---------------- materials ----------------
    def M(self, key, flat=False, grad=None):
        hexcol = self.pal.get(key, key if str(key).startswith("#") else "#FF00FF")
        if grad is not None:
            ck = ("grad", hexcol, grad)
            if ck not in self.mat_cache:
                self.mat_cache[ck] = make_gradient("grad_" + hexcol.replace("#", ""),
                                                   grad[0], grad[1], "Z")
            return self.mat_cache[ck]
        kind = self.cfg["kind"]
        bands = self.cfg["bands"]
        ck = (hexcol, flat, kind, bands)
        if ck in self.mat_cache:
            return self.mat_cache[ck]
        if flat or kind == "flat":
            m = make_flat("flat_" + hexcol.replace("#", ""), hexcol)
        elif kind == "paint":
            m = make_paint("paint_" + hexcol.replace("#", ""), hexcol)
        else:
            m = make_toon("toon_" + hexcol.replace("#", "") + "_" + str(bands),
                          hexcol, bands, kind)
        self.mat_cache[ck] = m
        return m

    def reg(self, ob, factor=1.0):
        if ob is not None:
            self.outline_src.append((ob, factor))
        return ob

    # ---------------- head surface ----------------
    def head_point(self, u, a, scale=1.0):
        R = self.R
        u = max(-1.0, min(1.0, u))
        base = math.sqrt(max(0.0, 1.0 - u * u))
        jaw = 1.0 - 0.14 * sm(0.05, -0.90, u)
        rx = R * 1.00 * base * jaw
        ry = R * 0.94 * base * jaw
        x = rx * math.sin(a)
        y = -ry * math.cos(a)
        ch = max(0.0, -u)
        y -= R * 0.10 * (ch ** 1.7)
        if y < 0.0:
            b_u = sm(-0.55, -0.30, u) * (1.0 - sm(0.50, 0.68, u))
            b_x = 1.0 - sm(0.55, 0.95, abs(math.sin(a)))
            b = b_u * b_x * 0.42
            plane = -R * 0.885
            y = y * (1.0 - b) + plane * b
        z = self.z_h + 0.92 * R * u
        p = Vector((x, y, z))
        if scale != 1.0:
            c = Vector((0.0, 0.0, self.z_h))
            p = c + (p - c) * scale
        return p

    def head_front_y(self, x, z):
        u = (z - self.z_h) / (0.92 * self.R)
        if u >= 1.0 or u <= -1.0:
            return 0.0
        base = math.sqrt(max(0.0, 1.0 - u * u))
        sin_a = max(-1.0, min(1.0, x / (self.R * 0.98 * base + 1e-6)))
        a = math.asin(sin_a)
        return self.head_point(u, a).y

    def face_pt(self, x, z, out=0.006):
        y = self.head_front_y(x, z) - out * self.R
        return Vector((x, y, z))

    # ---------------- head / ears / neck ----------------
    def build_head(self):
        R = self.R
        nseg = 56
        nring = 34
        bm = bmesh.new()
        rings = []
        for i in range(nring + 1):
            u = -math.cos(math.pi * i / nring)
            if abs(u) > 0.9995:
                rings.append(None)
                continue
            ring = []
            for k in range(nseg):
                a = TAU * k / nseg - math.pi
                p = self.head_point(u, a)
                ring.append(bm.verts.new(p))
            rings.append(ring)
        top = bm.verts.new(self.head_point(1.0, 0.0))
        bot = bm.verts.new(self.head_point(-1.0, 0.0))
        for i in range(len(rings) - 1):
            r0 = rings[i]
            r1 = rings[i + 1]
            if r0 is None and r1 is None:
                continue
            if r0 is None:
                for k in range(nseg):
                    bm.faces.new((top, r1[(k + 1) % nseg], r1[k]))
            elif r1 is None:
                for k in range(nseg):
                    bm.faces.new((r0[k], r0[(k + 1) % nseg], bot))
            else:
                for k in range(nseg):
                    bm.faces.new((r0[k], r0[(k + 1) % nseg], r1[(k + 1) % nseg], r1[k]))
        bm.normal_update()
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
        head = mesh_object("Head", bm, self.M("skin"), self.coll)
        self.reg(head, 0.9)
        # ears
        for s in (-1, 1):
            p = self.head_point(-0.06, s * math.pi * 0.5, 0.99)
            rot = rot_euler(0.0, 0.0, s * 0.18)
            ear = ellipsoid("Ear_" + ("L" if s > 0 else "R"), p,
                            (0.085 * R, 0.105 * R, 0.16 * R), self.M("skin"), self.coll,
                            rot=rot)
            self.reg(ear, 0.8)
            inner = ellipsoid("EarIn_" + ("L" if s > 0 else "R"),
                              p + Vector((s * 0.02 * R, -0.035 * R, 0.0)),
                              (0.045 * R, 0.05 * R, 0.09 * R), self.M("skin_shade"), self.coll)
        # neck
        neck_top = self.z_h - 0.86 * R
        neck_bot = self.shoulder_z - 0.02
        neck = tube("Neck", [Vector((0.0, 0.045 * R, neck_bot)),
                             Vector((0.0, 0.032 * R, (neck_top + neck_bot) * 0.5)),
                             Vector((0.0, 0.018 * R, neck_top))],
                    [0.34 * R, 0.30 * R, 0.27 * R], self.M("skin_shade"), self.coll,
                    sides=26)
        self.reg(neck, 0.7)
        # nose
        nose_y = self.face_pt(0.0, self.nose_z, out=-0.012)
        nose = ellipsoid("Nose", Vector((0.0, nose_y.y + 0.005 * R, self.nose_z)),
                         (0.055 * R, 0.05 * R, 0.075 * R), self.M("skin_light"), self.coll)
        shade_nose = ellipsoid("NoseShade", Vector((0.0, nose_y.y - 0.02 * R, self.nose_z - 0.05 * R)),
                               (0.05 * R, 0.03 * R, 0.035 * R), self.M("skin_shade", flat=True),
                               self.coll)
        # cheek blush / soft warmth (all genders, stronger for female)
        female = self.gender == "female"
        blush_hex = mix(self.pal["skin"], self.pal["blush"], 0.62 if female else 0.34)
        bw = (0.145 if female else 0.115) * R
        bh = (0.085 if female else 0.062) * R
        for s in (-1, 1):
            p = self.face_pt(s * 0.52 * R, self.z_h - 0.36 * R, out=0.004)
            ellipsoid("Blush_" + str(s), p, (bw, 0.022 * R, bh),
                      self.M(blush_hex, flat=True), self.coll)
        self._build_facial_hair()

    # ---------------- eyes / brows / mouth ----------------
    def _clear_face(self):
        for ob in list(self.coll.objects):
            if ob.name.startswith("F_"):
                self.outline_src = [(o, f) for (o, f) in self.outline_src if o is not ob]
                bpy.data.objects.remove(ob, do_unlink=True)

    def _eye(self, s, params):
        R = self.R
        es = self.eye_size * self.eye_age
        ex = s * 0.415 * R
        ez = self.eye_z + params.get("eye_dz", 0.0) * R
        open_k = params.get("open", 1.0)
        surf_y = self.head_front_y(ex, ez)
        cy = surf_y + 0.075 * R
        hw = 0.285 * R * es
        hh = 0.315 * R * es * open_k
        lx = params.get("look_x", 0.0) * R
        lz = params.get("look_z", 0.0) * R
        scl = ellipsoid("F_Sclera_" + str(s), Vector((ex, cy, ez)),
                        (hw, 0.085 * R, hh), self.M("eye_white", flat=True), self.coll,
                        segments=36, rings=20)
        ellipsoid("F_Limbal_" + str(s), Vector((ex + lx, cy - 0.050 * R, ez + 0.010 * R)),
                  (0.222 * R * es, 0.040 * R, 0.335 * R * es * open_k),
                  self.M("iris_dark", flat=True), self.coll, segments=32, rings=18)
        ir = ellipsoid("F_Iris_" + str(s),
                       Vector((ex + lx, cy - 0.066 * R, ez + 0.012 * R + lz)),
                       (0.198 * R * es, 0.040 * R, 0.315 * R * es * open_k),
                       self.M("iris", grad=(self.pal["iris_light"], self.pal["iris_dark"])),
                       self.coll, segments=32, rings=18)
        ellipsoid("F_Pupil_" + str(s), Vector((ex + lx, cy - 0.086 * R, ez + 0.012 * R + lz)),
                  (0.080 * R * es, 0.030 * R, 0.130 * R * es * open_k),
                  self.M("pupil", flat=True), self.coll, segments=24, rings=14)
        ellipsoid("F_Hi1_" + str(s),
                  Vector((ex - s * 0.065 * R, cy - 0.108 * R, ez + 0.145 * R * open_k)),
                  (0.058 * R, 0.022 * R, 0.062 * R), self.M("highlight", flat=True),
                  self.coll, segments=18, rings=10)
        ellipsoid("F_Hi2_" + str(s),
                  Vector((ex + s * 0.075 * R, cy - 0.104 * R, ez - 0.10 * R * open_k)),
                  (0.026 * R, 0.016 * R, 0.028 * R), self.M("highlight", flat=True),
                  self.coll, segments=12, rings=8)
        ellipsoid("F_Lash_" + str(s), Vector((ex, cy - 0.085 * R, ez + hh * 0.80)),
                  (hw * 1.10, 0.045 * R, 0.062 * R * (0.72 + 0.28 * open_k)),
                  self.M("lash", flat=True), self.coll, segments=28, rings=12,
                  rot=rot_euler(0.0, s * 0.10, 0.0))
        fx = ex + s * hw * 1.02
        fz = ez + hh * 0.80
        pts = [Vector((fx, cy - 0.088 * R, fz)),
               Vector((fx + s * 0.055 * R, cy - 0.075 * R, fz + 0.030 * R)),
               Vector((fx + s * 0.105 * R, cy - 0.060 * R, fz + 0.068 * R))]
        tube("F_Flick_" + str(s), pts, [0.030 * R, 0.022 * R, 0.003 * R],
             self.M("lash", flat=True), self.coll, sides=8)
        low_mat = self.M("lash", flat=True) if self.gender == "female" else self.M("skin_deep", flat=True)
        ellipsoid("F_LowLash_" + str(s), Vector((ex, cy - 0.068 * R, ez - hh * 0.88)),
                  (hw * 0.55, 0.026 * R, 0.010 * R), low_mat,
                  self.coll, segments=20, rings=8)
        if params.get("closed", False):
            for ob in (scl, ir):
                ob.hide_render = True
        self._brow(s, params)

    def _lash_flick(self, s, x, y, z, hw):
        R = self.R
        pts = [Vector((x, y, z)),
               Vector((x + s * 0.06 * R, y - 0.005 * R, z + 0.035 * R)),
               Vector((x + s * 0.12 * R, y + 0.005 * R, z + 0.075 * R))]
        return tube("F_Flick_" + str(s), pts, [0.035 * R, 0.026 * R, 0.004 * R],
                    self.M("lash", flat=True), self.coll, sides=8)

    def _brow(self, s, params):
        R = self.R
        brow_style = self.ap.get("brow", "thick")
        widths = {"thick": 0.038, "soft": 0.029, "thin": 0.021}
        w = widths.get(brow_style, 0.042) * R
        lift = params.get("brow_lift", 0.0) * R
        tilt = params.get("brow_tilt", 0.0)
        base_z = self.brow_z + lift + tilt * R
        pts = []
        for i in range(5):
            t = i / 4.0
            x = s * (0.17 + 0.52 * t) * R
            arc = math.sin(math.pi * min(1.0, t * 1.15)) * 0.018 * R
            z = base_z + arc - 0.035 * R * (t ** 2) + params.get("brow_curve", 0.0) * R * math.sin(math.pi * t)
            pts.append(self.face_pt(x, z, out=0.014))
        radii = [w * 0.62, w * 0.95, w, w * 0.85, w * 0.40]
        tube("F_Brow_" + str(s), pts, radii, self.M("brow", flat=True), self.coll, sides=10, flat=0.65)
        if "scar_brow" in (self.ap.get("marks") or []) and s < 0:
            sp = [self.face_pt(s * 0.28 * R, self.brow_z + 0.30 * R, out=0.02),
                  self.face_pt(s * 0.42 * R, self.brow_z + 0.10 * R, out=0.02)]
            tube("F_Scar", sp, [0.016 * R, 0.011 * R], self.M("skin_deep", flat=True),
                 self.coll, sides=6)

    def _mouth(self, params):
        R = self.R
        style = params.get("mouth", "smile")
        y = self.head_front_y(0.0, self.mouth_z) - 0.014 * R
        if style == "line":
            pts = []
            for i in range(5):
                t = i / 4.0
                x = (t - 0.5) * 0.25 * R
                z = self.mouth_z - 0.026 * R * math.sin(math.pi * t) * params.get("smile", 1.0)
                z += 0.010 * R * (abs(t - 0.5) * 2.0) ** 1.5
                pts.append(Vector((x, y, z)))
            tube("F_Mouth", pts, [0.008 * R, 0.013 * R, 0.014 * R, 0.012 * R, 0.008 * R],
                 self.M("mouth", flat=True), self.coll, sides=8, flat=0.85)
        elif style == "open_smile":
            m = ellipsoid("F_MouthOpen", Vector((0.0, y + 0.01 * R, self.mouth_z - 0.02 * R)),
                          (0.115 * R, 0.04 * R, 0.085 * R), self.M("mouth_inner", flat=True),
                          self.coll, segments=20, rings=12)
            t = ellipsoid("F_Tongue", Vector((0.0, y - 0.012 * R, self.mouth_z - 0.065 * R)),
                          (0.075 * R, 0.025 * R, 0.04 * R), self.M("tongue", flat=True),
                          self.coll, segments=16, rings=10)
            teeth = ellipsoid("F_Teeth", Vector((0.0, y - 0.012 * R, self.mouth_z + 0.03 * R)),
                              (0.095 * R, 0.02 * R, 0.028 * R), self.M("teeth", flat=True),
                              self.coll, segments=16, rings=8)
        elif style == "o":
            ellipsoid("F_MouthO", Vector((0.0, y + 0.01 * R, self.mouth_z - 0.03 * R)),
                      (0.065 * R, 0.04 * R, 0.085 * R), self.M("mouth_inner", flat=True),
                      self.coll, segments=18, rings=12)
        elif style == "think":
            pts = [Vector((-0.11 * R, y, self.mouth_z + 0.01 * R)),
                   Vector((0.0, y - 0.01 * R, self.mouth_z + 0.03 * R)),
                   Vector((0.10 * R, y, self.mouth_z + 0.005 * R))]
            tube("F_Mouth", pts, [0.013 * R, 0.017 * R, 0.012 * R],
                 self.M("mouth", flat=True), self.coll, sides=8, flat=0.8)

    def build_face(self, expression="neutral"):
        self._clear_face()
        P = {
            "neutral": dict(open=1.0, brow_lift=0.0, mouth="line", smile=1.0),
            "happy": dict(open=0.58, brow_lift=0.05, brow_curve=0.06, mouth="open_smile", smile=1.6),
            "surprised": dict(open=1.16, brow_lift=0.11, brow_tilt=0.03, mouth="o"),
            "thinking": dict(open=0.86, brow_lift=0.02, brow_tilt=0.025, brow_curve=0.02,
                             mouth="think", look_x=0.05, look_z=0.03),
        }
        p = P.get(expression, P["neutral"])
        if self.ap.get("facial_hair") == "stubble":
            p = dict(p)
            p["mouth"] = "line"
        for s in (-1, 1):
            self._eye(s, p)
        self._mouth(p)
        self.expression = expression
        self._parent_new_objects()

    def _build_facial_hair(self):
        fh = self.ap.get("facial_hair", "none")
        R = self.R
        if fh in ("stubble", "subtle"):
            # keep stubble off: hard colour patches read badly in toon render
            pass
        elif fh == "goatee":
            mat = self.M("facial_hair", flat=True)
            self._face_patch("Goatee", -1.0, -0.72, 0.20, 1.03, mat, n=8, m=14)
            for s in (-1, 1):
                p0 = self.face_pt(s * 0.06 * R, self.mouth_z + 0.11 * R, out=0.02)
                p1 = self.face_pt(s * 0.18 * R, self.mouth_z + 0.09 * R, out=0.02)
                tube("Mustache_" + str(s), [p0, p1], [0.022 * R, 0.008 * R], mat,
                     self.coll, sides=8, flat=0.7)

    def _face_patch(self, name, u0, u1, a_lim, scale, mat, n=8, m=24):
        bm = bmesh.new()
        grid = []
        for i in range(n + 1):
            u = lerp(u0, u1, i / n)
            row = []
            for j in range(m + 1):
                a = lerp(-a_lim, a_lim, j / m)
                row.append(bm.verts.new(self.head_point(u, a, scale)))
            grid.append(row)
        for i in range(n):
            for j in range(m):
                try:
                    bm.faces.new((grid[i][j], grid[i][j + 1], grid[i + 1][j + 1], grid[i + 1][j]))
                except Exception:
                    pass
        bm.normal_update()
        return mesh_object(name, bm, mat, self.coll, True)

    # ---------------- hair ----------------
    def hairline_u(self, a):
        aa = abs(a)
        return 0.55 - 0.48 * sm(0.45, 1.55, aa) - 0.40 * sm(1.55, 2.95, aa)

    def build_hair(self):
        hs = self.ap.get("hair_style", "short_swept")
        if hs == "bald":
            self._build_bald()
            return
        self._hair_cap()
        getattr(self, "_hair_" + hs, self._hair_short_swept)()
        for m in (self.ap.get("marks") or []):
            if m == "star_clip":
                p = self.head_point(0.55, 1.55, 1.16)
                star("F_StarClip", p, 0.16 * self.R, 0.07 * self.R, 0.035 * self.R,
                     self.M("accent", flat=True), self.coll, points=5,
                     rot=rot_euler(0.3, 0.2, 0.4))

    def _hair_cap(self):
        R = self.R
        nseg = 64
        nrow = 14
        bm = bmesh.new()
        cols = []
        for k in range(nseg):
            a = TAU * k / nseg - math.pi
            u0 = self.hairline_u(a)
            col = []
            for j in range(nrow + 1):
                t = j / nrow
                u = lerp(u0, 0.985, t)
                sc = 1.035 + 0.055 * t
                col.append(bm.verts.new(self.head_point(u, a, sc)))
            cols.append(col)
        for k in range(nseg):
            c0 = cols[k]
            c1 = cols[(k + 1) % nseg]
            for j in range(nrow):
                try:
                    bm.faces.new((c0[j], c1[j], c1[j + 1], c0[j + 1]))
                except Exception:
                    pass
        pole = bm.verts.new(self.head_point(1.0, 0.0, 1.09))
        for k in range(nseg):
            c0 = cols[k]
            c1 = cols[(k + 1) % nseg]
            try:
                bm.faces.new((c0[nrow], c1[nrow], pole))
            except Exception:
                pass
        bm.normal_update()
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
        cap = mesh_object("HairCap", bm, self.M("hair"), self.coll)
        mod = cap.modifiers.new("solid", "SOLIDIFY")
        mod.thickness = 0.022 * R
        mod.offset = -1.0
        self.reg(cap, 1.0)
        try:
            cap.visible_shadow = False
        except Exception:
            pass

    def _strand(self, name, a0, u0, a1, u1, width, curl=0.0, bulge=0.13, n=6, flat=0.55,
                mat=None, tip=0.03):
        pts = []
        radii = []
        for i in range(n):
            s = i / (n - 1.0)
            a = lerp(a0, a1, s) + curl * math.sin(math.pi * s)
            u = lerp(u0, u1, s)
            sc = 1.030 + bulge * math.sin(math.pi * min(1.0, s * 1.05)) + 0.05 * s
            pts.append(self.head_point(u, a, sc))
            radii.append(width * (1.0 - 0.88 * (s ** 1.3)) + tip * self.R)
        ob = tube(name, pts, radii, mat or self.M("hair"), self.coll, sides=8, flat=flat)
        if ob is not None:
            try:
                ob.visible_shadow = False
            except Exception:
                pass
        return ob

    def _hair_short_swept(self):
        R = self.R
        bangs = [
            (-1.05, 0.52, -0.55, 0.34, 0.13, 0.05),
            (-0.72, 0.62, -0.18, 0.40, 0.14, 0.04),
            (-0.38, 0.70, 0.22, 0.44, 0.14, 0.02),
            (-0.02, 0.72, 0.60, 0.42, 0.13, 0.0),
            (0.34, 0.68, 0.95, 0.36, 0.12, -0.03),
            (0.70, 0.60, 1.25, 0.30, 0.11, -0.06),
        ]
        for i, (a0, u0, a1, u1, w, curl) in enumerate(bangs):
            self.reg(self._strand("Bang_%02d" % i, a0, u0, a1, u1, w * R, curl=curl,
                                  bulge=0.06, n=8, flat=0.45, tip=0.02), 0.9)
        volumes = [
            (-0.45, 0.92, 0.30, 0.70, 0.15, 0.0),
            (0.10, 0.92, 0.80, 0.64, 0.14, 0.0),
            (-0.95, 0.86, -0.75, 0.60, 0.13, 0.0),
            (0.62, 0.88, 1.15, 0.58, 0.13, 0.0),
            (1.35, 0.62, 1.58, 0.14, 0.14, 0.0),
            (-1.35, 0.60, -1.60, 0.12, 0.14, 0.0),
            (1.85, 0.42, 2.10, -0.30, 0.14, 0.0),
            (-1.85, 0.42, -2.10, -0.30, 0.14, 0.0),
        ]
        for i, (a0, u0, a1, u1, w, curl) in enumerate(volumes):
            self.reg(self._strand("Vol_%02d" % i, a0, u0, a1, u1, w * R, curl=curl,
                                  bulge=0.11, n=7, flat=0.48, tip=0.02), 1.0)
        for s in (-1, 1):
            self.reg(self._strand("Side_%d" % s, s * 1.52, 0.30, s * 1.55, -0.18,
                                  0.075 * R, curl=0.04, bulge=0.06, n=6, flat=0.7,
                                  tip=0.015), 0.9)

    def _hair_spiky(self):
        R = self.R
        base = [(-1.2, 0.55, -1.35, 0.95), (-0.75, 0.65, -0.95, 1.05),
                (-0.3, 0.72, -0.45, 1.15), (0.15, 0.75, 0.3, 1.18),
                (0.6, 0.68, 0.85, 1.10), (1.05, 0.55, 1.35, 1.0)]
        for i, (a0, u0, a1, u1) in enumerate(base):
            pts = [self.head_point(u0, a0, 1.04),
                   self.head_point(lerp(u0, u1, 0.6), lerp(a0, a1, 0.6), 1.18),
                   self.head_point(u1, a1, 1.34)]
            self.reg(tube("Spike_%02d" % i, pts, [0.13 * R, 0.08 * R, 0.005 * R],
                          self.M("hair"), self.coll, sides=8), 1.0)
        for i in range(7):
            a = -2.6 + 5.2 * i / 6.0
            self.reg(self._strand("SpikeBack_%02d" % i, a, 0.5, a * 1.1, 0.95,
                                  0.10 * R, bulge=0.2, flat=0.8), 0.9)

    def _hair_bob(self):
        R = self.R
        for i in range(14):
            a = -2.9 + 5.8 * i / 13.0
            if abs(a) < 0.55:
                continue
            self.reg(self._strand("Bob_%02d" % i, a, 0.35, a * 1.06, -0.62,
                                  0.13 * R, curl=0.03, bulge=0.10, flat=0.6,
                                   tip=0.02), 1.0)
        mats = self.M("hair")
        for i, (a0, u0, a1, u1, w) in enumerate([(-0.9, 0.70, -0.1, 0.12, 0.15),
                                                  (-0.4, 0.75, 0.35, 0.10, 0.15),
                                                  (0.2, 0.75, 0.9, 0.14, 0.14)]):
            self.reg(self._strand("Bang_%02d" % i, a0, u0, a1, u1, w * R), 1.0)

    def _hair_twin_tail(self):
        R = self.R
        for i, (a0, u0, a1, u1, w) in enumerate([(-0.95, 0.66, -0.15, 0.16, 0.15),
                                                  (-0.45, 0.74, 0.25, 0.10, 0.16),
                                                  (0.1, 0.76, 0.8, 0.14, 0.15)]):
            self.reg(self._strand("Bang_%02d" % i, a0, u0, a1, u1, w * R), 1.0)
        for s in (-1, 1):
            p0 = self.head_point(0.45, s * 1.75, 1.10)
            pts = [p0,
                   p0 + Vector((s * 0.30 * R, 0.05 * R, -0.30 * R)),
                   p0 + Vector((s * 0.42 * R, 0.10 * R, -0.85 * R)),
                   p0 + Vector((s * 0.36 * R, 0.06 * R, -1.45 * R)),
                   p0 + Vector((s * 0.24 * R, 0.02 * R, -1.95 * R)),
                   p0 + Vector((s * 0.12 * R, 0.0, -2.25 * R))]
            self.reg(tube("TwinTail_%d" % s, pts,
                          [0.20 * R, 0.24 * R, 0.22 * R, 0.17 * R, 0.10 * R, 0.02 * R],
                          self.M("hair"), self.coll, sides=12, flat=0.85), 1.0)
            for k in range(2):
                c = p0 + Vector((s * 0.05 * R, 0.0, -0.06 * R - k * 0.09 * R))
                cylinder("HairTie_%d_%d" % (s, k), c, 0.16 * R, 0.06 * R,
                         self.M("accent"), self.coll, segments=16,
                         rot=rot_euler(0.0, s * 0.35, 0.0))

    def _hair_ponytail(self):
        R = self.R
        for i, (a0, u0, a1, u1, w) in enumerate([(-0.9, 0.68, -0.1, 0.20, 0.15),
                                                  (-0.4, 0.75, 0.3, 0.16, 0.15),
                                                  (0.15, 0.76, 0.85, 0.20, 0.14)]):
            self.reg(self._strand("Bang_%02d" % i, a0, u0, a1, u1, w * R), 1.0)
        base = self.head_point(0.55, math.pi, 1.08)
        pts = [base, base + Vector((0.0, 0.35 * R, 0.10 * R)),
               base + Vector((0.0, 0.55 * R, -0.55 * R)),
               base + Vector((0.05 * R, 0.45 * R, -1.25 * R)),
               base + Vector((0.10 * R, 0.30 * R, -1.85 * R))]
        self.reg(tube("Ponytail", pts, [0.17 * R, 0.24 * R, 0.20 * R, 0.12 * R, 0.02 * R],
                      self.M("hair"), self.coll, sides=12, flat=0.9), 1.0)
        cylinder("PonytailTie", base + Vector((0.0, 0.12 * R, 0.03 * R)), 0.14 * R, 0.07 * R,
                 self.M("accent"), self.coll, segments=14, rot=rot_euler(1.1, 0.0, 0.0))

    def _hair_long_straight(self):
        R = self.R
        for i in range(16):
            a = -3.0 + 6.0 * i / 15.0
            self.reg(self._strand("Long_%02d" % i, a, 0.40, a * 1.02, -1.15,
                                  0.15 * R, curl=0.02, bulge=0.10, flat=0.6, tip=0.02), 1.0)
        for i, (a0, u0, a1, u1, w) in enumerate([(-0.95, 0.70, -0.1, 0.14, 0.14),
                                                  (-0.35, 0.75, 0.4, 0.10, 0.15),
                                                  (0.25, 0.75, 0.95, 0.16, 0.13)]):
            self.reg(self._strand("Bang_%02d" % i, a0, u0, a1, u1, w * R), 1.0)

    def _hair_bun(self):
        R = self.R
        for i, (a0, u0, a1, u1, w) in enumerate([(-0.9, 0.70, -0.15, 0.16, 0.14),
                                                  (-0.4, 0.76, 0.3, 0.12, 0.14),
                                                  (0.2, 0.76, 0.9, 0.16, 0.13)]):
            self.reg(self._strand("Bang_%02d" % i, a0, u0, a1, u1, w * R), 1.0)
        p = self.head_point(1.02, math.pi, 1.02)
        ellipsoid("Bun", p + Vector((0.0, 0.12 * R, 0.10 * R)),
                  (0.30 * R, 0.30 * R, 0.28 * R), self.M("hair"), self.coll,
                  segments=24, rings=16)
        cylinder("BunTie", p + Vector((0.0, 0.05 * R, 0.0)), 0.16 * R, 0.06 * R,
                 self.M("accent"), self.coll, segments=14, rot=rot_euler(1.2, 0.0, 0.0))

    def _build_bald(self):
        R = self.R
        # side hair fringe
        for s in (-1, 1):
            self.reg(self._strand("SideHair_%d" % s, s * 1.72, 0.02, s * 1.78, -0.42,
                                  0.060 * R, curl=0.03, flat=0.7), 0.9)

    # ---------------- body ----------------
    def _loft(self, name, keys, mat, sides=40, nring=26, outline=1.0, smooth=True):
        bm = bmesh.new()
        rings = []
        for i in range(nring):
            t = i / (nring - 1.0) * (len(keys) - 1)
            j = min(int(t), len(keys) - 2)
            f = t - j
            k0 = keys[j]
            k1 = keys[j + 1]
            z = lerp(k0[0], k1[0], f)
            rx = lerp(k0[1], k1[1], f)
            ry = lerp(k0[2], k1[2], f)
            cy = lerp(k0[3], k1[3], f) if len(k0) > 3 else 0.0
            ring = []
            for k in range(sides):
                a = TAU * k / sides
                ring.append(bm.verts.new((rx * math.cos(a), cy + ry * math.sin(a), z)))
            rings.append(ring)
        for i in range(nring - 1):
            for k in range(sides):
                bm.faces.new((rings[i][k], rings[i][(k + 1) % sides],
                              rings[i + 1][(k + 1) % sides], rings[i + 1][k]))
        bm.faces.new(list(reversed(rings[0])))
        bm.faces.new(rings[-1])
        bm.normal_update()
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
        ob = mesh_object(name, bm, mat, self.coll, smooth)
        if outline:
            self.reg(ob, outline)
        return ob

    def _torso_keys(self, grow=0.0, top=0.755, bot=0.465):
        R = self.R
        H = self.H
        g = grow * R
        keys = [
            (top * H, 0.60 * R + g, 0.44 * R + g, 0.0),
            (0.748 * H, 1.06 * R + g, 0.66 * R + g, 0.0),
            (0.735 * H, 1.82 * R + g, 0.88 * R + g, 0.0),
            (0.660 * H, 1.64 * R + g, 0.88 * R + g, 0.0),
            (0.590 * H, 1.40 * R + g, 0.82 * R + g, 0.0),
            (0.550 * H, 1.30 * R + g, 0.78 * R + g, 0.0),
            (0.505 * H, 1.38 * R + g, 0.86 * R + g, 0.0),
            (bot * H, 1.32 * R + g, 0.84 * R + g, 0.0),
        ]
        return keys

    def build_body(self):
        R = self.R
        H = self.H
        otype = self.outfit.get("type", "suit")
        # base torso = shirt / inner layer
        self._loft("TorsoBase", self._torso_keys(0.0, top=0.752, bot=0.468), self.M("shirt"), outline=0)
        # arms (skin or sleeve)
        self._build_arms(otype)
        # neck/chest join cover (hides torso cap rings)
        ellipsoid("NeckBase", Vector((0.0, 0.015 * R, 0.742 * H)),
                  (0.72 * R, 0.58 * R, 0.22 * R), self.M("main"), self.coll,
                  segments=28, rings=14)
        # legs / lower body
        self._build_legs(otype)
        # outfit shell
        getattr(self, "_outfit_" + otype, self._outfit_suit)()

    def _shoulder(self):
        R = self.R
        H = self.H
        sw = {"male": 1.60, "female": 1.42, "neutral": 1.50}.get(self.gender, 1.5)
        return sw * R, 0.675 * H

    def _arm_path(self):
        R = self.R
        H = self.H
        sw, sz = self._shoulder()
        p0 = Vector((sw * 0.94, 0.0, sz + 0.015 * H))
        p1 = Vector((sw * 1.10, -0.02 * R, sz - 0.10 * H))
        p2 = Vector((sw * 1.18, -0.05 * R, sz - 0.20 * H))
        p3 = Vector((sw * 1.22, -0.09 * R, sz - 0.29 * H))
        return [p0, p1, p2, p3]

    def _build_arms(self, otype):
        R = self.R
        H = self.H
        for s in (-1, 1):
            path = [Vector((p.x * s, p.y, p.z)) for p in self._arm_path()]
            mat = self.M("shirt") if otype in ("hoodie", "shirt") else self.M("main")
            radii = [0.34 * R, 0.30 * R, 0.26 * R, 0.20 * R]
            self.reg(tube("Arm_%d" % s, path, radii, mat, self.coll, sides=14), 0.9)
            # hand
            h = path[-1] + (path[-1] - path[-2]).normalized() * 0.22 * R
            hand = ellipsoid("Hand_%d" % s, h + Vector((0.0, -0.01 * R, -0.02 * R)),
                             (0.16 * R, 0.13 * R, 0.20 * R), self.M("skin"), self.coll)
            self.reg(hand, 0.8)
            # cuff
            ring = tube("Cuff_%d" % s, [path[-1], path[-1] + (path[-1] - path[-2]).normalized() * 0.05 * H],
                        [0.215 * R, 0.205 * R], self.M("shirt_shade"), self.coll, sides=14)
            if "watch" in (self.ap.get("accessories") or []) and s < 0:
                wc = path[-1] + Vector((0.0, 0.0, 0.06 * H))
                self._watch(wc, s)

    def _watch(self, c, s):
        R = self.R
        cylinder("WatchFace", c + Vector((0.0, -0.02 * R, 0.0)), 0.085 * R, 0.035 * R,
                 self.M("metal"), self.coll, segments=18, rot=rot_euler(math.pi * 0.5, 0.0, 0.0))
        tube("WatchBand", [c + Vector((s * 0.075 * R, 0.0, 0.0)),
                           c + Vector((s * 0.05 * R, -0.05 * R, 0.0)),
                           c + Vector((-s * 0.075 * R, 0.0, 0.0))],
             [0.022 * R, 0.024 * R, 0.022 * R], self.M("frame"), self.coll, sides=8, flat=0.45)

    def _build_legs(self, otype):
        R = self.R
        H = self.H
        pants = self.M("pants") if otype in ("suit", "jacket", "coat") else self.M("pants")
        for s in (-1, 1):
            hip = Vector((s * 0.62 * R, 0.0, 0.485 * H))
            knee = Vector((s * 0.56 * R, -0.02 * R, 0.265 * H))
            ankle = Vector((s * 0.52 * R, 0.0, 0.05 * H))
            path = [hip, (hip + knee) * 0.5, knee, (knee + ankle) * 0.5, ankle]
            self.reg(tube("Leg_%d" % s, path, [0.44 * R, 0.36 * R, 0.30 * R, 0.24 * R, 0.21 * R],
                          pants, self.coll, sides=14), 0.9)
            foot = ellipsoid("Shoe_%d" % s, Vector((s * 0.52 * R, -0.13 * R, 0.035 * H)),
                             (0.24 * R, 0.42 * R, 0.14 * R), self.M("shoes"), self.coll)
            self.reg(foot, 0.8)

    def _shoulder_pads(self, mat, factor=1.0):
        R = self.R
        sw, sz = self._shoulder()
        for s in (-1, 1):
            p = Vector((s * sw * 0.90, 0.0, sz + 0.008 * self.H))
            e = ellipsoid("Shoulder_%d" % s, p, (0.40 * R, 0.44 * R, 0.30 * R), mat, self.coll)
            self.reg(e, factor * 0.7)

    def _sleeve(self, mat, factor=1.0):
        R = self.R
        for s in (-1, 1):
            path = [Vector((p.x * s, p.y, p.z)) for p in self._arm_path()]
            radii = [0.36 * R, 0.33 * R, 0.30 * R, 0.255 * R]
            self.reg(tube("Sleeve_%d" % s, path, radii, mat, self.coll, sides=16), factor)

    def _collar_ring(self, z, radius, tube_r, mat):
        R = self.R
        n = 28
        pts = []
        for i in range(n + 1):
            a = TAU * i / n
            pts.append(Vector((radius * math.cos(a), radius * math.sin(a), z)))
        return tube("Collar", pts, [tube_r] * (n + 1), mat, self.coll, sides=8)

    def _front_panel(self, name, z_top, z_bot, w_top, w_bot, grow, mat, outline=0.0, steps=(8, 5)):
        R = self.R
        keys = self._torso_keys(grow, top=0.705, bot=0.465)
        # build a front patch following torso surface
        bm = bmesh.new()
        rows = []
        nz, na = steps
        for i in range(nz + 1):
            t = i / nz
            z = lerp(z_top, z_bot, t)
            # find torso radius at z by interpolating keys
            rx, ry = self._torso_radius_at(z, grow, keys)
            w = lerp(w_top, w_bot, t)
            row = []
            for j in range(na + 1):
                a = lerp(-w, w, j / na)
                row.append(bm.verts.new(Vector((rx * math.sin(a), -ry * math.cos(a), z))))
            rows.append(row)
        for i in range(nz):
            for j in range(na):
                try:
                    bm.faces.new((rows[i][j], rows[i][j + 1], rows[i + 1][j + 1], rows[i + 1][j]))
                except Exception:
                    pass
        bm.normal_update()
        return mesh_object(name, bm, mat, self.coll, True)

    def _torso_radius_at(self, z, grow, keys):
        R = self.R
        for i in range(len(keys) - 1):
            z0 = keys[i][0]
            z1 = keys[i + 1][0]
            if (z0 - z) * (z1 - z) <= 0:
                t = 0.0 if abs(z1 - z0) < 1e-9 else (z - z0) / (z1 - z0)
                rx = lerp(keys[i][1], keys[i + 1][1], t)
                ry = lerp(keys[i][2], keys[i + 1][2], t)
                return rx, ry
        return keys[-1][1], keys[-1][2]

    def _outfit_suit(self):
        R = self.R
        H = self.H
        self._loft("Jacket", self._torso_keys(0.075), self.M("main"), sides=44, nring=28)
        self._shoulder_pads(self.M("main"), 0.5)
        self._sleeve(self.M("main"))
        # shirt V panel
        self._front_panel("ShirtV", 0.716 * H, 0.598 * H, 0.26, 0.030, 0.095,
                          self.M("shirt"), steps=(10, 6))
        # tie
        self._front_panel("Tie", 0.714 * H, 0.622 * H, 0.042, 0.058, 0.130,
                          self.M("accent"), steps=(8, 3))
        # lapels
        for s in (-1, 1):
            pts = []
            for i in range(5):
                t = i / 4.0
                z = lerp(0.718 * H, 0.606 * H, t)
                w = lerp(0.36, 0.055, t)
                rx, ry = self._torso_radius_at(z, 0.115, self._torso_keys(0.115))
                pts.append(Vector((s * rx * math.sin(w), -ry * math.cos(w), z)))
            tube("Lapel_%d" % s, pts, [0.115 * R, 0.115 * R, 0.105 * R, 0.085 * R, 0.055 * R],
                 self.M("main_light"), self.coll, sides=8, flat=0.28)
        # collar wings (shirt collar under the jacket)
        for s in (-1, 1):
            c0 = Vector((s * 0.10 * R, -0.30 * R, 0.732 * H))
            c1 = Vector((s * 0.24 * R, -0.40 * R, 0.700 * H))
            c2 = Vector((s * 0.06 * R, -0.44 * R, 0.692 * H))
            tube("CollarWing_%d" % s, [c0, (c0 + c1) * 0.5, c1, c2],
                 [0.030 * R, 0.052 * R, 0.048 * R, 0.020 * R], self.M("shirt"),
                 self.coll, sides=8, flat=0.55)
        # buttons
        for i, zf in enumerate((0.628, 0.600)):
            rx, ry = self._torso_radius_at(zf * H, 0.10, self._torso_keys(0.10))
            ellipsoid("Button_%d" % i, Vector((0.0, -ry - 0.012 * R, zf * H)),
                      (0.032 * R, 0.025 * R, 0.032 * R), self.M("accent"), self.coll,
                      segments=14, rings=8)
        # pocket square
        rx, ry = self._torso_radius_at(0.665 * H, 0.10, self._torso_keys(0.10))
        box("Pocket", Vector((0.55 * R, -ry + 0.02 * R, 0.648 * H)),
            (0.14 * R, 0.03 * R, 0.02 * R), self.M("accent"), self.coll,
            rot=rot_euler(0.0, 0.0, 0.2))

    def _outfit_hoodie(self):
        R = self.R
        H = self.H
        self._loft("Hoodie", self._torso_keys(0.10), self.M("main"), sides=44, nring=28)
        self._shoulder_pads(self.M("main"))
        self._sleeve(self.M("main"))
        # hood behind neck / head
        for i in range(9):
            pass
        hood_c = Vector((0.0, 0.22 * R, 0.735 * H))
        ellipsoid("Hood", hood_c, (0.72 * R, 0.55 * R, 0.30 * R), self.M("main_dark"),
                  self.coll, segments=28, rings=16, rot=rot_euler(-0.35, 0.0, 0.0))
        # pouch
        rx, ry = self._torso_radius_at(0.575 * H, 0.10, self._torso_keys(0.10))
        box("Pouch", Vector((0.0, -ry - 0.02 * R, 0.565 * H)),
            (0.52 * R, 0.10 * R, 0.16 * R), self.M("main_light"), self.coll,
            bevel=0.03 * R)
        # drawstrings
        for s in (-1, 1):
            p0 = self.face_pt(0.0, 0.0) if False else Vector((s * 0.14 * R, -0.42 * R, 0.700 * H))
            tube("Draw_%d" % s, [p0, p0 + Vector((0.0, -0.02 * R, -0.10 * H)),
                                 p0 + Vector((s * 0.02 * R, -0.03 * R, -0.20 * H))],
                 [0.022 * R, 0.022 * R, 0.036 * R], self.M("shirt"), self.coll, sides=8,
                 flat=0.8)
        # cuffs accent ring
        for s in (-1, 1):
            path = [Vector((p.x * s, p.y, p.z)) for p in self._arm_path()]
            self.reg(tube("HoodCuff_%d" % s, [path[-1], path[-1] + Vector((0, 0, -0.04 * H))],
                          [0.27 * R, 0.26 * R], self.M("accent"), self.coll, sides=14), 0.9)
        # neck rim
        self._collar_ring(0.700 * H, 0.44 * R, 0.06 * R, self.M("main_dark"))

    def _outfit_jacket(self):
        R = self.R
        H = self.H
        self._loft("Jacket", self._torso_keys(0.08), self.M("main"), sides=44, nring=28)
        self._shoulder_pads(self.M("main"))
        self._sleeve(self.M("main"))
        self._front_panel("ShirtV", 0.702 * H, 0.560 * H, 0.30, 0.05, 0.10,
                          self.M("shirt"), steps=(12, 6))
        self._front_panel("Zipper", 0.700 * H, 0.520 * H, 0.018, 0.018, 0.135,
                          self.M("metal_dark"), steps=(12, 2))
        for s in (-1, 1):
            pts = []
            for i in range(4):
                t = i / 3.0
                z = lerp(0.706 * H, 0.560 * H, t)
                w = lerp(0.36, 0.08, t)
                rx, ry = self._torso_radius_at(z, 0.11, self._torso_keys(0.11))
                pts.append(Vector((s * rx * math.sin(w), -ry * math.cos(w), z)))
            tube("Lapel_%d" % s, pts, [0.11 * R, 0.10 * R, 0.085 * R, 0.055 * R],
                 self.M("main_light"), self.coll, sides=8, flat=0.3)
        self._collar_ring(0.706 * H, 0.41 * R, 0.06 * R, self.M("main_dark"))
        # accent cuffs + hem
        for s in (-1, 1):
            path = [Vector((p.x * s, p.y, p.z)) for p in self._arm_path()]
            self.reg(tube("JacketCuff_%d" % s, [path[-1], path[-1] + Vector((0, 0, -0.05 * H))],
                          [0.27 * R, 0.265 * R], self.M("accent"), self.coll, sides=14), 0.9)
        rx, ry = self._torso_radius_at(0.475 * H, 0.09, self._torso_keys(0.09))
        self._collar_ring(0.478 * H, rx * 0.96, 0.045 * R, self.M("accent"))

    def _outfit_shirt(self):
        R = self.R
        H = self.H
        self._loft("Shirt", self._torso_keys(0.05), self.M("shirt"), sides=40, nring=26)
        self._shoulder_pads(self.M("shirt"))
        self._sleeve(self.M("shirt"))
        self._collar_ring(0.706 * H, 0.40 * R, 0.06 * R, self.M("shirt_shade"))
        for i, zf in enumerate((0.665, 0.620, 0.575, 0.530)):
            rx, ry = self._torso_radius_at(zf * H, 0.06, self._torso_keys(0.06))
            ellipsoid("Button_%d" % i, Vector((0.0, -ry - 0.01 * R, zf * H)),
                      (0.026 * R, 0.02 * R, 0.026 * R), self.M("main_dark"), self.coll,
                      segments=12, rings=8)

    def _outfit_dress(self):
        R = self.R
        H = self.H
        self._loft("Dress", self._torso_keys(0.07), self.M("main"), sides=44, nring=26)
        self._shoulder_pads(self.M("main"))
        self._sleeve(self.M("main"))
        # skirt
        keys = [(0.505 * H, 1.20 * R, 0.80 * R, 0.0),
                (0.44 * H, 1.75 * R, 1.20 * R, 0.0),
                (0.36 * H, 2.30 * R, 1.55 * R, 0.0),
                (0.33 * H, 2.42 * R, 1.62 * R, 0.0)]
        self._loft("Skirt", keys, self.M("main_light"), sides=44, nring=14)
        self._collar_ring(0.700 * H, 0.40 * R, 0.05 * R, self.M("accent"))
        rx, ry = self._torso_radius_at(0.505 * H, 0.06, self._torso_keys(0.06))
        self._collar_ring(0.508 * H, rx * 0.98, 0.05 * R, self.M("accent"))
        # legs already skin? rebuild in skin tone
        for s in (-1, 1):
            hip = Vector((s * 0.62 * R, 0.0, 0.485 * H))
            knee = Vector((s * 0.56 * R, -0.02 * R, 0.265 * H))
            ankle = Vector((s * 0.52 * R, 0.0, 0.05 * H))
            tube("LegSkin_%d" % s, [hip, (hip + knee) * 0.5, knee, (knee + ankle) * 0.5, ankle],
                 [0.36 * R, 0.30 * R, 0.26 * R, 0.21 * R, 0.19 * R], self.M("skin"),
                 self.coll, sides=14)

    def _outfit_coat(self):
        R = self.R
        H = self.H
        keys = self._torso_keys(0.09)
        keys = keys + [(0.40 * H, 1.30 * R, 0.84 * R, 0.0),
                       (0.34 * H, 1.34 * R, 0.88 * R, 0.0)]
        self._loft("Coat", keys, self.M("main"), sides=44, nring=30)
        self._shoulder_pads(self.M("main"))
        self._sleeve(self.M("main"))
        self._front_panel("ShirtV", 0.700 * H, 0.600 * H, 0.30, 0.05, 0.11,
                          self.M("shirt"), steps=(10, 6))
        for s in (-1, 1):
            pts = []
            for i in range(5):
                t = i / 4.0
                z = lerp(0.706 * H, 0.520 * H, t)
                w = lerp(0.36, 0.06, t)
                rx, ry = self._torso_radius_at(z, 0.12, self._torso_keys(0.12))
                pts.append(Vector((s * rx * math.sin(w), -ry * math.cos(w), z)))
            tube("Lapel_%d" % s, pts, [0.12 * R, 0.11 * R, 0.10 * R, 0.085 * R, 0.06 * R],
                 self.M("main_light"), self.coll, sides=8, flat=0.3)
        self._collar_ring(0.705 * H, 0.42 * R, 0.07 * R, self.M("main_dark"))
        rx, ry = self._torso_radius_at(0.520 * H, 0.10, self._torso_keys(0.10))
        self._collar_ring(0.522 * H, rx * 0.96, 0.055 * R, self.M("accent"))

    # ---------------- accessories ----------------
    def build_accessories(self):
        acc = self.ap.get("accessories") or []
        if "glasses" in acc:
            self._glasses()
        if "headphones" in acc:
            self._headphones()
        if "earring" in acc:
            for s in (-1, 1):
                p = self.head_point(-0.25, s * math.pi * 0.5, 1.02)
                p = p + Vector((s * 0.02 * R, -0.02 * R, -0.10 * R))
                cylinder("Earring_%d" % s, p, 0.045 * self.R, 0.012 * self.R,
                         self.M("accent", flat=True), self.coll, segments=12,
                         rot=rot_euler(0.0, math.pi * 0.5, 0.0))

    def _glasses(self):
        R = self.R
        es = self.eye_size
        for s in (-1, 1):
            ex = s * 0.40 * R
            ez = self.eye_z + 0.02 * R
            y = self.head_front_y(ex, ez) - 0.10 * R
            # rim as flattened torus-ish tube circle
            pts = []
            for i in range(15):
                a = TAU * i / 14.0
                pts.append(Vector((ex + 0.285 * R * es * math.cos(a), y + 0.02 * R * math.sin(a) * 0.3,
                                   ez + 0.235 * R * es * math.sin(a))))
            tube("GlassRim_%d" % s, pts, [0.035 * R] * 15, self.M("frame"), self.coll,
                 sides=8)
            # bridge
        bridge = tube("GlassBridge", [Vector((-0.11 * R, self.head_front_y(0.0, self.eye_z) - 0.09 * R,
                                             self.eye_z + 0.02 * R)),
                                      Vector((0.11 * R, self.head_front_y(0.0, self.eye_z) - 0.09 * R,
                                              self.eye_z + 0.02 * R))],
                      [0.022 * R, 0.022 * R], self.M("frame"), self.coll, sides=8)
        # temples
        for s in (-1, 1):
            p0 = Vector((s * 0.68 * R, self.head_front_y(s * 0.68 * R, self.eye_z) - 0.08 * R,
                         self.eye_z + 0.06 * R))
            p1 = self.head_point(0.30, s * math.pi * 0.5, 1.02) + Vector((0.0, -0.02 * R, 0.06 * R))
            tube("GlassTemple_%d" % s, [p0, (p0 + p1) * 0.5, p1], [0.018 * R] * 3,
                 self.M("frame"), self.coll, sides=6)

    def _headphones(self):
        R = self.R
        # head band arc over the top
        pts = []
        for i in range(13):
            t = i / 12.0
            a = lerp(-math.pi * 0.55, math.pi * 1.55, t) if False else lerp(0.0, math.pi, t)
            ang = 0.25 + t * (math.pi - 0.5)
            pts.append(self.head_point(0.98 - 0.06 * math.sin(math.pi * t), ang, 1.10))
        # simpler: arc in x-z plane over head
        pts = []
        for i in range(13):
            t = i / 12.0
            ang = math.pi * (0.12 + 0.76 * t)
            x = math.cos(ang) * 1.045 * R
            z = self.z_h + math.sin(ang) * 0.99 * R
            pts.append(Vector((x, 0.03 * R, z)))
        tube("HeadBand", pts, [0.055 * R] * 13, self.M("accent"), self.coll, sides=10)
        for s in (-1, 1):
            p = self.head_point(0.05, s * math.pi * 0.5, 1.0)
            cup = cylinder("EarCup_%d" % s, p + Vector((s * 0.045 * R, 0.0, 0.0)),
                           0.175 * R, 0.10 * R, self.M("main_dark"), self.coll,
                           segments=20, rot=rot_euler(0.0, math.pi * 0.5, 0.0))
            cap = cylinder("EarCupCap_%d" % s, p + Vector((s * 0.10 * R, 0.0, 0.0)),
                           0.135 * R, 0.03 * R, self.M("accent"), self.coll,
                           segments=18, rot=rot_euler(0.0, math.pi * 0.5, 0.0))

    # ---------------- assembly ----------------
    def _parent_new_objects(self):
        for ob in list(self.coll.objects):
            if ob.parent is None and ob.type == "MESH":
                ob.parent = self.root

    def build(self, expression="neutral"):
        self.build_head()
        self.build_hair()
        self.build_body()
        self.build_accessories()
        self.build_face(expression)
        self._parent_new_objects()
        return self

    # ---------------- outlines ----------------
    def build_outlines(self, base_thickness):
        for ob in self.outline_objs:
            bpy.data.objects.remove(ob, do_unlink=True)
        self.outline_objs = []
        for src, factor in self.outline_src:
            if src.name not in bpy.data.objects:
                continue
            d = base_thickness * factor
            ol = add_outline(src, d, self.outline_mat, self.coll_inoutline())
            ol.parent = self.root
            self.outline_objs.append(ol)
        return self.outline_objs

    def coll_inoutline(self):
        return self.coll
