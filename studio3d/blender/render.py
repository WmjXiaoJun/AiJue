"""Headless Blender renderer for studio3d characters.

Called as:  blender -b --python render.py -- --spec S.json --outdir D --style 3d_toon
            [--only portrait,expressions,sheet,turntable] [--base 512] [--frames 48]

Writes raw RGBA/stills into <outdir>/raw/ and a JSON summary as the last stdout line.
"""
import bpy
import sys
import os
import json
import math
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
for _p in (HERE, PKG):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from mathutils import Vector
TAU = math.pi * 2.0
import character as charmod
import lib
from character import Character
from presets import normalize_spec, style_config


def parse_args():
    argv = sys.argv
    if "--" in argv:
        argv = argv[argv.index("--") + 1:]
    else:
        argv = []
    d = {"spec": "", "outdir": "", "style": "3d_toon", "only": "portrait",
         "base": "512", "frames": "48", "fps": "24"}
    i = 0
    while i < len(argv) - 1:
        d[argv[i].lstrip("-")] = argv[i + 1]
        i += 2
    return d


def setup_scene(cfg):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sc = bpy.context.scene
    sc.render.engine = "BLENDER_EEVEE"
    sc.render.film_transparent = True
    sc.render.image_settings.file_format = "PNG"
    sc.render.image_settings.color_mode = "RGBA"
    sc.render.image_settings.color_depth = "8"
    sc.render.resolution_percentage = 100
    sc.render.fps = int(cfg.get("fps", 24))
    sc.view_settings.view_transform = "Standard"
    sc.view_settings.look = "None"
    sc.view_settings.exposure = 0.0
    sc.view_settings.gamma = 1.0
    try:
        sc.display_settings.display_device = "sRGB"
    except Exception:
        pass
    sc.eevee.taa_render_samples = int(cfg.get("samples", 16))
    try:
        sc.eevee.use_raytracing = False
    except Exception:
        pass
    try:
        sc.eevee.use_shadows = True
    except Exception:
        pass
    world = bpy.data.worlds.new("World")
    world.use_nodes = True
    bg = world.node_tree.nodes.get("Background")
    if bg is not None:
        bg.inputs[0].default_value = (0.020, 0.024, 0.038, 1.0)
        bg.inputs[1].default_value = 1.0
    sc.world = world
    return sc


def add_light(name, kind, loc, target, energy, color, size):
    ld = bpy.data.lights.new(name, kind)
    ld.energy = energy
    ld.color = color
    if kind == "AREA":
        ld.size = size
        ld.shape = "DISK"
    ob = bpy.data.objects.new(name, ld)
    bpy.context.scene.collection.objects.link(ob)
    ob.location = Vector(loc)
    d = Vector(target) - Vector(loc)
    ob.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()
    return ob


def setup_lights(R, z_h, accent_rgb):
    H = z_h + 1.02 * R
    key = add_light("Key", "AREA", (8.0 * R, -11.0 * R, z_h + 5.0 * R),
                    (0.0, 0.0, z_h), 75.0, (1.0, 0.965, 0.92), 7.0 * R)
    fill = add_light("Fill", "AREA", (-10.0 * R, -8.0 * R, z_h + 1.5 * R),
                     (0.0, 0.0, z_h), 26.0, (0.82, 0.88, 1.0), 11.0 * R)
    rim = add_light("Rim", "AREA", (-4.0 * R, 11.0 * R, z_h + 4.5 * R),
                    (0.0, 0.0, z_h + 0.4 * R), 95.0, (0.86, 0.92, 1.0), 6.0 * R)
    acc = add_light("Accent", "AREA", (7.0 * R, 8.0 * R, z_h - 1.0 * R),
                    (0.0, 0.0, z_h), 36.0, accent_rgb, 5.0 * R)
    return [key, fill, rim, acc]


def set_lights_body(key, fill, rim, acc, R, H):
    """Reposition the 3-point rig for full-body shots."""
    tgt = Vector((0.0, 0.0, 0.55 * H))
    spec = [
        (key, (10.0 * R, -20.0 * R, 1.40 * H), 225.0, 12.0 * R),
        (fill, (-16.0 * R, -14.0 * R, 0.90 * H), 78.0, 16.0 * R),
        (rim, (-6.0 * R, 20.0 * R, 1.60 * H), 285.0, 10.0 * R),
        (acc, (10.0 * R, 14.0 * R, 0.80 * H), 108.0, 8.0 * R),
    ]
    for ob, loc, energy, size in spec:
        ob.location = Vector(loc)
        ob.data.energy = energy
        ob.data.size = size
        d = tgt - Vector(loc)
        ob.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()


def add_floor(coll):
    mat = lib.make_toon("FloorToon", "#1B2029", 3, "toon")
    fl = lib.cylinder("Floor", Vector((0.0, 0.0, -0.012)), 3.0, 0.024, mat, coll,
                      segments=64)
    return fl


def setup_camera(sc, ortho=False, ortho_scale=2.0):
    cd = bpy.data.cameras.new("Cam")
    cam = bpy.data.objects.new("Cam", cd)
    sc.collection.objects.link(cam)
    sc.camera = cam
    cd.lens = 85.0
    cd.sensor_width = 36.0
    if ortho:
        cd.type = "ORTHO"
        cd.ortho_scale = ortho_scale
    return cam


def aim(cam, loc, target):
    cam.location = Vector(loc)
    d = Vector(target) - Vector(loc)
    cam.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()


def render_to(sc, path, res_x, res_y=None):
    sc.render.resolution_x = res_x
    sc.render.resolution_y = res_y or res_x
    sc.render.filepath = path
    t0 = time.time()
    bpy.ops.render.render(write_still=True)
    return time.time() - t0


def main():
    t_start = time.time()
    args = parse_args()
    spec_path = args["spec"]
    outdir = args["outdir"]
    style_name = args["style"]
    only = [s.strip() for s in args["only"].split(",") if s.strip()]
    base = int(args["base"])
    frames = int(args["frames"])
    with open(spec_path, "r", encoding="utf-8") as f:
        spec = normalize_spec(json.load(f))
    cfg = style_config(style_name)
    rawdir = os.path.join(outdir, "raw")
    os.makedirs(rawdir, exist_ok=True)
    os.makedirs(os.path.join(rawdir, "expressions"), exist_ok=True)

    sc = setup_scene(cfg)
    coll = bpy.data.collections.new("Character")
    sc.collection.children.link(coll)
    root = bpy.data.objects.new("Root", None)
    sc.collection.objects.link(root)

    ch = Character(spec, cfg, coll, root)
    try:
        ch.build("neutral")
    except Exception as e:
        import traceback
        traceback.print_exc()
        print("BUILD_ERROR: " + str(e))
        raise
    key, fill, rim, acc = setup_lights(ch.R, ch.z_h, lib.hex_lin(spec["accent_color"]))
    cam = setup_camera(sc)
    R = ch.R
    z_h = ch.z_h
    tan_half = 18.0 / 85.0
    renders = []
    is3d = not cfg["ortho"]

    ortho = bool(cfg["ortho"])
    if "portrait" in only:
        frame_h = 3.30 * R
        zc = z_h - 0.40 * R
        if ortho:
            cam.data.type = "ORTHO"
            cam.data.ortho_scale = frame_h
            ch.build_outlines(0.0032 * cfg["outline"])
            root.rotation_euler[2] = 0.0
            aim(cam, (0.0, -3.0, zc), (0.0, 0.0, zc))
        else:
            ch.build_outlines(0.0020 * cfg["outline"])
            root.rotation_euler[2] = 0.0
            dist = (frame_h * 0.5) / tan_half
            aim(cam, (0.0, -dist, zc), (0.0, 0.0, zc))
        path = os.path.join(rawdir, "portrait_rgba.png")
        secs = render_to(sc, path, 768)
        renders.append({"name": "portrait", "path": path, "secs": round(secs, 2)})

    if "expressions" in only:
        frame_h = 2.75 * R
        zc = z_h - 0.04 * R
        if ortho:
            cam.data.type = "ORTHO"
            cam.data.ortho_scale = frame_h
            ch.build_outlines(0.0028 * cfg["outline"])
            root.rotation_euler[2] = 0.0
            aim(cam, (0.0, -3.0, zc), (0.0, 0.0, zc))
        else:
            ch.build_outlines(0.0016 * cfg["outline"])
            root.rotation_euler[2] = 0.0
            dist = (frame_h * 0.5) / tan_half
            aim(cam, (0.0, -dist, zc + 0.02 * R), (0.0, 0.0, zc))
        for expr in ("neutral", "happy", "surprised", "thinking"):
            ch.build_face(expr)
            path = os.path.join(rawdir, "expressions", expr + "_rgba.png")
            secs = render_to(sc, path, base)
            renders.append({"name": "expr_" + expr, "path": path, "secs": round(secs, 2)})
        ch.build_face("neutral")

    if "sheet" in only:
        ch.build_outlines(0.0040 * cfg["outline"])
        set_lights_body(key, fill, rim, acc, R, ch.H)
        cam.data.type = "ORTHO"
        cam.data.ortho_scale = ch.H * 1.13
        zc_body = ch.H * 0.525
        aim(cam, (0.0, -3.2, zc_body), (0.0, 0.0, zc_body))
        for vname, rot in (("front", 0.0), ("side", math.pi * 0.5),
                           ("quarter", 0.62)):
            root.rotation_euler[2] = rot
            path = os.path.join(rawdir, "sheet_" + vname + ".png")
            secs = render_to(sc, path, 440, 820)
            renders.append({"name": "sheet_" + vname, "path": path,
                            "secs": round(secs, 2)})
        root.rotation_euler[2] = 0.0

    if "turntable" in only:
        ch.build_outlines(0.0046 * cfg["outline"])
        set_lights_body(key, fill, rim, acc, R, ch.H)
        sc.render.film_transparent = False
        add_floor(sc.collection)
        cam.data.type = "PERSP"
        cam.data.lens = 70.0
        frame_h = ch.H * 1.16
        zc_body = ch.H * 0.545
        dist = (frame_h * 0.5) / (18.0 / 70.0)
        aim(cam, (0.0, -dist, zc_body + 0.02), (0.0, 0.0, zc_body))
        os.makedirs(os.path.join(rawdir, "turntable"), exist_ok=True)
        nfr = int(args.get("frames", "48"))
        for f in range(nfr):
            root.rotation_euler[2] = TAU * f / nfr
            path = os.path.join(rawdir, "turntable", "turntable_%04d.png" % (f + 1))
            secs = render_to(sc, path, 720)
            renders.append({"name": "turn_%04d" % (f + 1), "path": path,
                            "secs": round(secs, 2)})
        root.rotation_euler[2] = 0.0

    print("RENDERS " + json.dumps(renders))
    print("BLENDER_SEC " + str(round(time.time() - t_start, 2)))
    print("SUMMARY " + json.dumps({"ok": True, "style": style_name, "outdir": outdir,
                                   "renders": renders,
                                   "elapsed": round(time.time() - t_start, 2)}))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        traceback.print_exc()
        print("SUMMARY " + json.dumps({"ok": False, "error": str(e)}))
        sys.exit(1)
