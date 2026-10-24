"""Stylised 3D TV-studio scene: set + three toon characters + camera moves.

Rendered headless with Blender 5.0 / EEVEE from studio3d CLI:
    blender -b --python studio3d/blender/scene.py -- --outdir <dir> [--shots ...]

Outputs PNG frame sequences into <outdir>/frames/<shot>/ .
"""
import bpy
import bmesh
import math
import os
import sys
import json
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
for _p in (HERE, PKG):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from mathutils import Vector, Euler, Matrix
import lib
import character as charmod
from character import Character
from presets import normalize_spec, style_config, palette_for, hex_rgb, lighten, shade, mix

TAU = math.pi * 2.0
FONT_PATH = "C:/Windows/Fonts/msyhbd.ttc"

STUDIO = {
    "bg": "#0A0C12",
    "floor": "#141824",
    "floor_light": "#1D2434",
    "stage": "#1A2030",
    "table": "#151A26",
    "desk": "#1C2333",
    "metal": "#39414F",
    "chair": "#232A38",
    "screen_low": "#101828",
    "screen_high": "#070A12",
    "accent": "#F5C518",
    "accent_cool": "#5AC8FA",
    "warm_light": "#FFD9A0",
    "cool_light": "#9EC7FF",
}


def sm(e0, e1, x):
    t = (x - e0) / (e1 - e0) if e1 != e0 else 0.0
    t = max(0.0, min(1.0, t))
    return t * t * (3.0 - 2.0 * t)


def look_at(ob, target):
    d = Vector(target) - ob.location
    ob.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()


def new_mat(name, hexcol, kind="toon", bands=3):
    return lib.make_toon("set_" + name + "_" + hexcol.replace("#", ""), hexcol, bands, kind)


def flat_mat(name, hexcol, strength=1.0, alpha=1.0):
    return lib.make_flat("set_" + name, hexcol, strength=strength, alpha=alpha)


def principled_mat(name, hexcol, roughness=0.25, metallic=0.0):
    mat = lib.new_mat("set_" + name)
    nt = mat.node_tree
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    col = lib.hex_lin(hexcol)
    bsdf.inputs["Base Color"].default_value = (col[0], col[1], col[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    nt.links.new(bsdf.outputs[0], out.inputs["Surface"])
    return mat


def ring(name, center, radius, tube_r, hexcol, strength=1.0, n=96, axis="Z"):
    """Emission tube circle (used instead of solid cylinders for glowing rims)."""
    cx, cy, cz = center
    pts = []
    for i in range(n + 1):
        a = TAU * i / n
        pts.append(Vector((cx + radius * math.cos(a), cy + radius * math.sin(a), cz)))
    return lib.tube(name, pts, [tube_r] * (n + 1), flat_mat(name, hexcol, strength),
                    bpy.context.scene.collection, sides=8)


# ---------------------------------------------------------------- scene setup
def setup_scene(args):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sc = bpy.context.scene
    sc.render.engine = "BLENDER_EEVEE"
    sc.render.film_transparent = False
    sc.render.image_settings.file_format = "PNG"
    sc.render.image_settings.color_mode = "RGB"
    sc.render.image_settings.color_depth = "8"
    sc.render.fps = int(args.get("fps", 25))
    sc.render.resolution_percentage = 100
    sc.view_settings.view_transform = "Standard"
    sc.view_settings.look = "None"
    sc.view_settings.exposure = 0.0
    sc.view_settings.gamma = 1.0
    sc.eevee.taa_render_samples = int(args.get("samples", 20))
    try:
        sc.eevee.use_raytracing = True
        sc.eevee.ray_tracing_method = "SCREEN"
        sc.eevee.ray_tracing_options.resolution_scale = "2"
        sc.eevee.ray_tracing_options.use_denoise = True
    except Exception as e:
        print("raytracing setup skipped:", e)
    world = bpy.data.worlds.new("World")
    world.use_nodes = True
    bg = world.node_tree.nodes.get("Background")
    if bg is not None:
        bg.inputs[0].default_value = (0.010, 0.012, 0.020, 1.0)
        bg.inputs[1].default_value = 1.0
    sc.world = world
    return sc


def coll_new(sc, name):
    c = bpy.data.collections.new(name)
    sc.collection.children.link(c)
    return c


# ---------------------------------------------------------------- set pieces
def build_floor(coll, pal):
    # reflective studio floor (EEVEE screen-space raytraced reflections)
    bpy.ops.mesh.primitive_plane_add(size=1.0)
    fl = bpy.context.active_object
    fl.name = "Floor"
    for c in list(fl.users_collection):
        c.objects.unlink(fl)
    coll.objects.link(fl)
    fl.scale = (14.0, 11.0, 1.0)
    fl.location = (0.0, 3.4, 0.0)
    fl.data.materials.append(principled_mat("floor", STUDIO["floor"], roughness=0.30,
                                            metallic=0.18))
    # stage platform + glowing rims (rings, not solid discs)
    lib.cylinder("Stage", Vector((0.0, 2.2, 0.07)), 6.2, 0.14,
                 new_mat("stage", STUDIO["stage"]), coll, segments=80)
    lib.tube("StageRim", [Vector((6.2 * math.cos(TAU * i / 96.0),
                                  2.2 + 6.2 * math.sin(TAU * i / 96.0), 0.148))
                          for i in range(97)],
             [0.028] * 97, flat_mat("stagerim", STUDIO["accent"], 1.5), coll, sides=8)
    # inner floor ring accents for depth
    for rr, st in ((3.85, 0.9), (4.15, 0.45)):
        lib.tube("FloorRing_%.2f" % rr,
                 [Vector((rr * math.cos(TAU * i / 96.0), 2.2 + rr * math.sin(TAU * i / 96.0), 0.012))
                  for i in range(97)],
                 [0.014] * 97, flat_mat("floorring_%.2f" % rr, STUDIO["accent_cool"], st),
                 coll, sides=6)
    return fl


def build_screen(coll, pal, brand_text="造片局 · AI 微综艺"):
    cx, cy = 0.0, 12.0
    rad, h0, h1 = 8.6, 0.30, 3.55
    n = 40
    bm = bmesh.new()
    rows = []
    for i in range(n + 1):
        a = -0.92 + 1.84 * i / n
        x = cx + rad * math.sin(a)
        y = cy - rad * math.cos(a)
        rows.append([bm.verts.new((x, y, h0)), bm.verts.new((x, y, h1))])
    for i in range(n):
        a, b = rows[i], rows[i + 1]
        bm.faces.new((a[0], b[0], b[1], a[1]))
    bm.normal_update()
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    scr = lib.mesh_object("BGScreen", bm,
                          lib.make_gradient("set_screen", STUDIO["screen_low"],
                                            STUDIO["screen_high"], "Z"), coll)
    # glowing top edge
    pts = []
    for i in range(n + 1):
        a = -0.92 + 1.84 * i / n
        pts.append(Vector((cx + rad * math.sin(a), cy - rad * math.cos(a), h1)))
    lib.tube("ScreenEdge", pts, [0.035] * len(pts), flat_mat("screenedge", STUDIO["accent"], 1.6),
             coll, sides=8)
    # animated screen sweep (adds life to the LED wall)
    sweep_mesh = lib.box("ScreenSweep", Vector((0.0, 0.0, 0.0)), (1.05, 0.015, 0.020),
                         flat_mat("screensweep", STUDIO["accent"], 1.6), coll)
    sweep_mesh.location = Vector((0.0, cy - rad - 0.09, 1.62))
    sweep_mesh.keyframe_insert(data_path="rotation_euler", frame=1)
    sweep_mesh.rotation_euler = (0.0, math.pi * 0.55, 0.0)
    sweep_mesh.keyframe_insert(data_path="rotation_euler", frame=170)
    # host backdrop panel (behind the host desk, on the screen wall)
    lib.box("HostBackdrop", Vector((-3.65, 4.18, 1.75)), (3.0, 0.02, 2.5),
            new_mat("hostbackdrop", mix(STUDIO["screen_high"], STUDIO["accent_cool"], 0.07)),
            coll)
    for bx in (-5.10, -2.20):
        lib.box("HostBar_%.1f" % bx, Vector((bx, 4.16, 1.75)), (0.05, 0.03, 2.5),
                flat_mat("hostbar_%.1f" % bx, STUDIO["accent_cool"], 0.9), coll)
    for bz in (1.02, 2.48):
        lib.box("HostBarH_%.2f" % bz, Vector((-3.65, 4.15, bz)), (2.6, 0.02, 0.025),
                flat_mat("hostbarh_%.2f" % bz, STUDIO["accent"], 0.55), coll)
    try:
        curve2 = bpy.data.curves.new("HostText", "FONT")
        curve2.body = "TALK SHOW"
        curve2.size = 0.20
        curve2.align_x = "CENTER"
        try:
            curve2.font = bpy.data.fonts.load(FONT_PATH)
        except Exception:
            pass
        ht = bpy.data.objects.new("HostText", curve2)
        coll.objects.link(ht)
        ht.location = (-3.65, 4.14, 2.72)
        ht.rotation_euler = (math.pi * 0.5, 0.0, 0.0)
        ht.data.materials.append(flat_mat("hosttext", "#DFE9F5", 0.85))
    except Exception as e:
        print("host text skipped:", e)
    # screen content: glowing base strip + logo ring + brand text (flat billboards)
    lib.box("ScreenBase", Vector((cx, cy - rad - 0.06, 0.50)),
            (7.4, 0.02, 0.09), flat_mat("screenbase", STUDIO["accent"], 1.3), coll)
    for k, (rr, zz, st) in enumerate(((0.72, 1.62, 1.2), (0.92, 1.62, 0.55))):
        pts = []
        for i in range(97):
            a = TAU * i / 96.0
            pts.append(Vector((cx + rr * math.cos(a), cy - rad - 0.06,
                               zz + rr * math.sin(a))))
        lib.tube("ScreenRing_%d" % k, pts, [0.018] * 97,
                 flat_mat("ring_%d" % k, STUDIO["accent"], st), coll, sides=6)
    for k, zz in enumerate((0.88, 3.26)):
        lib.box("ScreenBar_%d" % k, Vector((cx, cy - rad - 0.06, zz)),
                (7.9, 0.02, 0.030),
                flat_mat("screenbar_%d" % k, STUDIO["accent_cool"], 0.85), coll)
    try:
        curve = bpy.data.curves.new("BrandText", "FONT")
        curve.body = brand_text
        curve.size = 0.40
        curve.align_x = "CENTER"
        curve.extrude = 0.012
        try:
            curve.font = bpy.data.fonts.load(FONT_PATH)
        except Exception:
            pass
        tob = bpy.data.objects.new("BrandText", curve)
        coll.objects.link(tob)
        tob.location = (cx, cy - rad - 0.10, 2.74)
        tob.rotation_euler = (math.pi * 0.5, 0.0, 0.0)
        tob.data.materials.append(flat_mat("brandtext", "#FFF8E2", 1.6))
    except Exception as e:
        print("brand text skipped:", e)
    return scr


def build_table(coll, pal, center=(1.9, 1.5), radius=1.55, top=0.72):
    cx, cy = center
    top_ob = lib.cylinder("TableTop", Vector((cx, cy, top)), radius, 0.09,
                          new_mat("table", STUDIO["table"]), coll, segments=64)
    # skirt hides the characters' lower bodies so they read as seated
    skirt = lib.cylinder("TableSkirt", Vector((cx, cy, top * 0.48)), radius - 0.02,
                         top * 0.94, new_mat("tableskirt", STUDIO["stage"]), coll,
                         segments=64)
    lib.tube("TableSkirtRim", [Vector((cx + (radius - 0.005) * math.cos(TAU * i / 72.0),
                                       cy + (radius - 0.005) * math.sin(TAU * i / 72.0),
                                       top * 0.06)) for i in range(73)],
             [0.012] * 73, flat_mat("tableskirt_rim", STUDIO["accent"], 0.7), coll, sides=6)
    lib.tube("TableRim", [Vector((cx + (radius + 0.01) * math.cos(TAU * i / 72.0),
                                  cy + (radius + 0.01) * math.sin(TAU * i / 72.0),
                                  top + 0.052)) for i in range(73)],
             [0.016] * 73, flat_mat("tablerim", STUDIO["accent"], 1.5), coll, sides=6)
    base = lib.cylinder("TableBase", Vector((cx, cy, top * 0.5)), 0.34, top,
                        new_mat("tablebase", STUDIO["metal"]), coll, segments=28)
    foot = lib.cylinder("TableFoot", Vector((cx, cy, 0.16)), 0.95, 0.06,
                        new_mat("tablefoot", STUDIO["stage"]), coll, segments=48)
    # table props: cups / mic / cards
    cup = lib.cylinder("Cup1", Vector((cx - 0.55, cy - 0.55, top + 0.10)), 0.075, 0.16,
                       flat_mat("cup", "#E9E4D8", 1.0), coll, segments=20)
    cup2 = lib.cylinder("Cup2", Vector((cx + 0.62, cy - 0.30, top + 0.09)), 0.065, 0.14,
                        flat_mat("cup2", "#D8DDE8", 1.0), coll, segments=20)
    # name cards
    for i, xo in enumerate((-0.95, 0.10, 0.95)):
        card = lib.box("Card_%d" % i, Vector((cx + xo, cy + 0.15, top + 0.06)),
                       (0.26, 0.02, 0.12), flat_mat("card", "#F6F2E6", 1.0), coll,
                       rot=Euler((0.0, 0.0, (i - 1) * 0.25)).to_matrix().to_4x4(),
                       bevel=0.008)
        lib.box("CardTxt_%d" % i, Vector((cx + xo, cy + 0.138, top + 0.055)),
                (0.18, 0.004, 0.012), flat_mat("cardtxt", STUDIO["desk"], 1.0), coll)
    return top_ob


def build_desk(coll, pal, center=(-3.5, 1.85), width=2.2, depth=0.8, height=0.88):
    cx, cy = center
    body = lib.box("DeskBody", Vector((cx, cy, height * 0.5)),
                   (width, depth, height), new_mat("desk", STUDIO["desk"]), coll,
                   bevel=0.05)
    front = lib.box("DeskFront", Vector((cx, cy - depth * 0.5 - 0.02, height * 0.5)),
                    (width * 0.96, 0.04, height * 0.92),
                    flat_mat("deskfront", STUDIO["desk"], 1.0), coll)
    strip = lib.box("DeskStrip", Vector((cx, cy - depth * 0.5 - 0.05, height * 0.62)),
                    (width * 0.8, 0.02, 0.05), flat_mat("deskstrip", STUDIO["accent"], 1.4),
                    coll)
    top = lib.box("DeskTop", Vector((cx, cy, height + 0.02)),
                  (width + 0.1, depth + 0.06, 0.06), new_mat("desktop", STUDIO["metal"]),
                  coll, bevel=0.02)
    # logo block on the front
    try:
        curve = bpy.data.curves.new("DeskLogo", "FONT")
        curve.body = "造片局"
        curve.size = 0.20
        curve.align_x = "CENTER"
        curve.extrude = 0.01
        try:
            curve.font = bpy.data.fonts.load(FONT_PATH)
        except Exception:
            pass
        tob = bpy.data.objects.new("DeskLogo", curve)
        coll.objects.link(tob)
        tob.location = (cx, cy - depth * 0.5 - 0.07, height * 0.42)
        tob.rotation_euler = (math.pi * 0.5, 0.0, 0.0)
        tob.data.materials.append(flat_mat("desklogo", "#FFF6DC", 1.4))
    except Exception as e:
        print("desk logo skipped:", e)
    return top


def build_chairs(coll, seats):
    for i, (x, y, z, h) in enumerate(seats):
        seat = lib.cylinder("ChairSeat_%d" % i, Vector((x, y, z)), 0.40, 0.09,
                            new_mat("chair", STUDIO["chair"]), coll, segments=28)
        back = lib.box("ChairBack_%d" % i, Vector((x, y + 0.42, z + 0.44)),
                       (0.72, 0.08, 0.78), new_mat("chairback", STUDIO["chair"]), coll,
                       bevel=0.04)
        post = lib.cylinder("ChairPost_%d" % i, Vector((x, y, z - 0.24)), 0.07, 0.48,
                            new_mat("chairpost", STUDIO["metal"]), coll, segments=16)


def build_truss(coll, pal):
    mat = new_mat("metal", STUDIO["metal"])
    for i, y in enumerate((0.9, 5.2)):
        lib.tube("Truss_%d" % i, [Vector((-7.2, y, 3.62)), Vector((7.2, y, 3.62))],
                 [0.075, 0.075], mat, coll, sides=10)
        for x in (-7.0, 7.0):
            lib.tube("TrussPost_%d_%d" % (i, int(x)), [Vector((x, y, 0.6)), Vector((x, y, 3.62))],
                     [0.075, 0.075], mat, coll, sides=10)
    for i, y in enumerate((0.9, 5.2)):
        lib.tube("TrussCross_%d" % i, [Vector((-7.2, y, 4.15)), Vector((-7.2, y - 0.0, 4.15))],
                 [0.075, 0.075], mat, coll, sides=6)
    # hanging fixtures with emissive front + point light
    fix_positions = [(-4.2, 1.1, 3.32), (-0.6, 1.1, 3.32), (3.0, 1.1, 3.32),
                     (-2.6, 4.9, 3.32), (2.2, 4.9, 3.32), (5.3, 4.9, 3.32)]
    for i, (x, y, z) in enumerate(fix_positions):
        lib.cylinder("FixBody_%d" % i, Vector((x, y, z)), 0.15, 0.34, mat, coll,
                     segments=16, rot=Euler((0.0, 0.0, 0.0)).to_matrix().to_4x4())
        lib.cylinder("FixLens_%d" % i, Vector((x, y, z - 0.19)), 0.13, 0.03,
                     flat_mat("fixlens", STUDIO["warm_light"], 3.0), coll, segments=16)
        lib.tube("FixDrop_%d" % i, [Vector((x, y, z + 0.17)), Vector((x, y, 3.62))],
                 [0.02, 0.02], mat, coll, sides=6)
    # vertical accent light bars on the sides
    for i, x in enumerate((-7.6, 7.6)):
        lib.box("SideBar_%d" % i, Vector((x, 1.6, 1.85)), (0.08, 0.08, 2.6),
                flat_mat("sidebar_%d" % i, STUDIO["accent_cool"], 1.3), coll)


def build_foreground(coll, pal):
    """Foreground monitor + mic stands give parallax depth in the wide shot."""
    x, y = -5.0, -0.9
    stand = lib.cylinder("FGStand", Vector((x, y, 0.45)), 0.05, 0.9,
                         new_mat("fgmetal", STUDIO["metal"]), coll, segments=12)
    frame = lib.box("FGFrame", Vector((x, y, 0.98)), (0.66, 0.06, 0.40),
                    new_mat("fgframe", STUDIO["desk"]), coll, bevel=0.02)
    lib.box("FGScr", Vector((x, y - 0.04, 0.98)), (0.58, 0.01, 0.33),
            flat_mat("fgscr", STUDIO["accent_cool"], 0.7), coll)
    # mic stands near the host desk
    for i, (mx, my) in enumerate(((-4.75, 0.95), (-2.05, 0.62))):
        lib.cylinder("MicBase_%d" % i, Vector((mx, my, 0.05)), 0.22, 0.06,
                     new_mat("micbase", STUDIO["metal"]), coll, segments=18)
        lib.tube("MicPole_%d" % i, [Vector((mx, my, 0.08)), Vector((mx, my, 1.28))],
                 [0.022, 0.022], new_mat("micpole", STUDIO["metal"]), coll, sides=8)
        lib.ellipsoid("MicHead_%d" % i, Vector((mx + 0.03, my - 0.02, 1.32)),
                      (0.055, 0.055, 0.085), flat_mat("michead", "#2A2E36", 1.0), coll,
                      segments=16, rings=10)


def build_reflections(coll, pal):
    # stage top gets a subtly glossy material so the floor reflects the neon + characters
    lib.cylinder("StageGlass", Vector((0.0, 2.2, 0.152)), 6.16, 0.006,
                 principled_mat("stageglass", "#0E131C", roughness=0.24, metallic=0.22),
                 coll, segments=96)


def add_lights(coll, pal, sc):
    def area(name, loc, target, energy, color, size, shape="DISK", shadow=True):
        ld = bpy.data.lights.new(name, "AREA")
        ld.energy = energy
        ld.color = color
        ld.size = size
        ld.shape = shape
        try:
            ld.use_shadow = shadow
        except Exception:
            pass
        ob = bpy.data.objects.new(name, ld)
        coll.objects.link(ob)
        ob.location = Vector(loc)
        look_at(ob, target)
        return ob
    key = area("KeyLight", (-5.2, 0.2, 4.0), (-1.0, 2.2, 1.1), 720.0,
               (1.0, 0.94, 0.84), 6.0, "DISK", True)
    fill = area("FillLight", (6.4, -1.2, 2.6), (1.4, 2.4, 1.1), 380.0,
                (0.62, 0.76, 1.0), 7.0, "DISK", False)
    rim = area("RimLight", (2.4, 7.2, 3.4), (0.6, 2.0, 1.4), 900.0,
               (0.55, 0.72, 1.0), 5.0, "DISK", True)
    accent = area("AccentLight", (-6.4, 3.4, 1.9), (-2.4, 2.0, 1.1), 320.0,
                  lib.hex_lin(STUDIO["accent"]), 3.0, "DISK", False)
    area("HostRim", (-5.0, 4.4, 2.3), (-3.4, 2.0, 1.45), 260.0,
         (0.62, 0.80, 1.0), 1.6, "DISK", False)
    # warm practicals from the fixtures (no shadows: cheap)
    for i, (x, y) in enumerate(((-0.6, 1.1), (3.0, 1.1), (2.2, 4.9))):
        ld = bpy.data.lights.new("Prac_%d" % i, "POINT")
        ld.energy = 90.0
        ld.color = (1.0, 0.86, 0.62)
        ld.shadow_soft_size = 0.35
        try:
            ld.use_shadow = False
        except Exception:
            pass
        ob = bpy.data.objects.new("Prac_%d" % i, ld)
        coll.objects.link(ob)
        ob.location = Vector((x, y, 3.55))
    return key


def build_walls(coll, pal):
    # vertical neon accents on the back wall (frame the stage)
    for x in (-6.4, 6.4, -4.6):
        lib.box("WallNeon_%.1f" % x, Vector((x, 9.2, 1.9)), (0.07, 0.07, 3.4),
                flat_mat("wallneon_%.1f" % x, STUDIO["accent_cool"], 1.1), coll,
                bevel=0.0)
    # dark side walls + back wall for a closed studio feel
    for i, (loc, size, rotz) in enumerate((
            ((0.0, 12.4, 2.4), (24.0, 0.2, 5.2), 0.0),
            ((-11.2, 3.0, 2.4), (0.2, 20.0, 5.2), 0.0),
            ((11.2, 3.0, 2.4), (0.2, 20.0, 5.2), 0.0))):
        ob = lib.box("Wall_%d" % i, Vector(loc), size,
                     new_mat("wall_%d" % i, STUDIO["bg"]), coll, bevel=0.0)


# ---------------------------------------------------------------- characters
def build_characters(scene_coll, specs, styles, gap_root):
    roots = []
    chars = []
    for i, (cid, spec, style_name) in enumerate(zip(specs["ids"], specs["specs"], styles)):
        cfg = style_config(style_name)
        rc = coll_new(bpy.context.scene, "Char_" + cid)
        root = bpy.data.objects.new("Root_" + cid, None)
        rc.objects.link(root)
        ch = Character(spec, cfg, rc, root)
        ch.build("neutral")
        ch.build_outlines(0.0042 * cfg["outline"])
        chars.append((cid, ch, root))
    return chars


def place_characters(chars, pal, slots):
    """slots: list of dict(x,y,z,rot) matching chars order."""
    for (cid, ch, root), slot in zip(chars, slots):
        root.location = Vector((slot["x"], slot["y"], slot["z"]))
        root.rotation_euler[2] = slot.get("rot", 0.0)


# ---------------------------------------------------------------- render
def make_camera(sc):
    cd = bpy.data.cameras.new("SceneCam")
    cd.lens = 42.0
    cd.sensor_width = 36.0
    cam = bpy.data.objects.new("SceneCam", cd)
    sc.collection.objects.link(cam)
    sc.camera = cam
    return cam


def shot_wide(p, t):
    """lateral dolly across the stage, slight rise."""
    loc = Vector((-2.9 + 5.6 * t, -5.7 + 0.7 * t, 1.30 + 0.28 * t))
    look = Vector((-2.00 + 4.00 * t, 2.7, 1.16 + 0.14 * t))
    return loc, look, 40.0


def shot_host(p, t):
    """slow push-in on laozhou behind the host desk."""
    loc = Vector((-1.40 - 1.60 * t, -3.20 + 2.65 * t, 1.60 - 0.10 * t))
    look = Vector((-3.15 - 0.15 * t, 2.10, 1.36 - 0.005 * t))
    return loc, look, 50.0 + 16.0 * t


def shot_stage_pan(p, t):
    """pan / crane across the round table, changing angle."""
    ang = math.radians(-24.0 + 44.0 * t)
    rad = 7.4 - 0.6 * t
    loc = Vector((rad * math.sin(ang) + 1.5, -rad * math.cos(ang) + 2.0, 1.45 + 0.95 * t))
    look = Vector((1.1, 2.4, 1.18 + 0.16 * t))
    return loc, look, 38.0


SHOTS = {
    "3d_studio_wide": dict(fn=shot_wide, seconds=5.0),
    "3d_studio_host": dict(fn=shot_host, seconds=5.0),
    "3d_stage_pan": dict(fn=shot_stage_pan, seconds=6.0),
}
SHOT_ALIASES = {"wide": "3d_studio_wide", "host": "3d_studio_host",
                "stage_pan": "3d_stage_pan", "stage": "3d_stage_pan"}


def resolve_shot(name):
    name = SHOT_ALIASES.get(name, name)
    if name not in SHOTS:
        raise KeyError("unknown shot: %s (available: %s)" % (name, ",".join(SHOTS)))
    return name


def aim_cam(cam, loc, target, lens):
    cam.location = Vector(loc)
    cam.data.lens = lens
    look_at(cam, target)


def render_shots(sc, cam, args, outdir, shots):
    fps = int(args.get("fps", 25))
    res_x, res_y = [int(v) for v in str(args.get("size", "1280x720")).lower().split("x")]
    sc.render.resolution_x = res_x
    sc.render.resolution_y = res_y
    results = []
    for name in shots:
        cfg = SHOTS[name]
        nframes = int(round(cfg["seconds"] * fps))
        fdir = os.path.join(outdir, "frames", name)
        os.makedirs(fdir, exist_ok=True)
        t0 = time.time()
        p = Vector((0.0, 0.0, 0.0))
        for f in range(nframes):
            t = f / max(1, nframes - 1.0)
            p, look, lens = cfg["fn"](p, t)
            aim_cam(cam, p, look, lens)
            sc.frame_set(f + 1)
            sc.render.filepath = os.path.join(fdir, "frame_%04d.png" % (f + 1))
            bpy.ops.render.render(write_still=True)
        secs = time.time() - t0
        results.append({"shot": name, "frames": nframes, "seconds": round(secs, 2),
                        "avg_frame_sec": round(secs / nframes, 3),
                        "dir": fdir})
        print("SHOT_DONE %s frames=%d secs=%.1f" % (name, nframes, secs), flush=True)
    return results


def main():
    t0 = time.time()
    argv = sys.argv
    argv = argv[argv.index("--") + 1:] if "--" in argv else []
    args = {}
    i = 0
    while i < len(argv) - 1:
        args[argv[i].lstrip("-")] = argv[i + 1]
        i += 2
    outdir = args.get("outdir") or os.path.join(PKG, "..", "outputs", "3d_scenes")
    outdir = os.path.abspath(outdir)
    os.makedirs(outdir, exist_ok=True)
    char_dir = args.get("chardir") or os.path.join(os.path.dirname(PKG), "contract", "characters")
    ids = [s.strip() for s in args.get("characters", "laozhou,ali,dawei").split(",") if s.strip()]
    styles = [s.strip() for s in args.get("styles", "3d_toon,3d_toon,3d_cel").split(",")]
    specs = {"ids": [], "specs": []}
    for cid in ids:
        with open(os.path.join(char_dir, cid + ".json"), "r", encoding="utf-8") as f:
            specs["ids"].append(cid)
            specs["specs"].append(normalize_spec(json.load(f)))
    shots = [s.strip() for s in args.get("shots", "3d_studio_wide,3d_studio_host,3d_stage_pan").split(",")
             if s.strip()]
    still_only = args.get("still") is not None

    sc = setup_scene(args)
    set_coll = coll_new(sc, "StudioSet")
    build_floor(set_coll, None)
    build_walls(set_coll, None)
    build_screen(set_coll, None)
    build_table(set_coll, None)
    build_desk(set_coll, None)
    build_truss(set_coll, None)
    build_reflections(set_coll, None)
    build_foreground(set_coll, None)
    build_chairs(set_coll, [(-3.4, 2.05, -0.30, 1.7), (2.80, 2.95, -0.30, 1.6),
                            (0.80, 2.95, -0.30, 1.6)])
    add_lights(set_coll, None, sc)
    chars = build_characters(set_coll, specs, styles, None)
    place_characters(chars, None, [
        {"x": -3.4, "y": 2.05, "z": -0.10, "rot": -0.10},
        {"x": 2.80, "y": 2.95, "z": -0.18, "rot": 0.16},
        {"x": 0.80, "y": 2.95, "z": -0.18, "rot": -0.18},
    ])
    cam = make_camera(sc)

    if still_only:
        sh = resolve_shot(args.get("still", "wide"))
        p = Vector((0.0, 0.0, 0.0))
        t = float(args.get("t", "0.5"))
        p, look, lens = SHOTS[sh]["fn"](p, t)
        aim_cam(cam, p, look, lens)
        res = args.get("size", "1280x720").lower().split("x")
        sc.render.resolution_x, sc.render.resolution_y = int(res[0]), int(res[1])
        out = os.path.join(outdir, "previews", "still_" + sh + ".png")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        sc.render.filepath = out
        t1 = time.time()
        bpy.ops.render.render(write_still=True)
        print("STILL " + out + " secs=%.2f" % (time.time() - t1))
    else:
        results = render_shots(sc, cam, args, outdir, shots)
        print("SCENE_RESULT " + json.dumps({"ok": True, "outdir": outdir, "fps": int(args.get("fps", 25)),
                                            "size": args.get("size", "1280x720"), "shots": results,
                                            "render_sec": round(time.time() - t0, 2)}))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        traceback.print_exc()
        print("SCENE_RESULT " + json.dumps({"ok": False, "error": str(e)}))
        sys.exit(1)
