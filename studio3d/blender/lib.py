"""Procedural mesh + toon material helpers (bpy only)."""
import bpy
import bmesh
import math
from mathutils import Vector, Matrix

TAU = math.pi * 2.0
EPS = 1e-5


def srgb_to_linear(c):
    c = max(0.0, min(1.0, c))
    if c <= 0.04045:
        return c / 12.92
    return ((c + 0.055) / 1.055) ** 2.4


def hex_lin(h):
    from presets import hex_rgb
    return tuple(srgb_to_linear(c) for c in hex_rgb(h))


def new_mat(name):
    mat = bpy.data.materials.get(name)
    if mat is not None:
        bpy.data.materials.remove(mat)
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    mat.node_tree.nodes.clear()
    return mat


def make_flat(name, hexcol, strength=1.0, alpha=1.0, suffix=""):
    mat = new_mat(name + suffix)
    nt = mat.node_tree
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    em = nt.nodes.new("ShaderNodeEmission")
    col = hex_lin(hexcol)
    em.inputs["Color"].default_value = (col[0], col[1], col[2], 1.0)
    em.inputs["Strength"].default_value = strength
    if alpha < 1.0:
        tr = nt.nodes.new("ShaderNodeBsdfTransparent")
        mixn = nt.nodes.new("ShaderNodeMixShader")
        mixn.inputs[0].default_value = alpha
        nt.links.new(tr.outputs[0], mixn.inputs[1])
        nt.links.new(em.outputs[0], mixn.inputs[2])
        nt.links.new(mixn.outputs[0], out.inputs["Surface"])
        mat.blend_method = "BLEND"
        try:
            mat.surface_render_method = "BLENDED"
        except Exception:
            pass
    else:
        nt.links.new(em.outputs[0], out.inputs["Surface"])
    return mat


def make_gradient(name, hex_dark, hex_light, axis="Z", suffix=""):
    mat = new_mat(name + suffix)
    nt = mat.node_tree
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    em = nt.nodes.new("ShaderNodeEmission")
    tex = nt.nodes.new("ShaderNodeTexCoord")
    sep = nt.nodes.new("ShaderNodeSeparateXYZ")
    ramp = nt.nodes.new("ShaderNodeValToRGB")
    nt.links.new(tex.outputs["Generated"], sep.inputs[0])
    idx = {"X": 0, "Y": 1, "Z": 2}[axis]
    nt.links.new(sep.outputs[idx], ramp.inputs["Fac"])
    ramp.color_ramp.elements[0].color = (*hex_lin(hex_dark), 1)
    ramp.color_ramp.elements[1].color = (*hex_lin(hex_light), 1)
    nt.links.new(ramp.outputs["Color"], em.inputs["Color"])
    nt.links.new(em.outputs[0], out.inputs["Surface"])
    return mat


def make_screen_gradient(name, hex_dark, hex_light):
    mat = new_mat(name)
    nt = mat.node_tree
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    em = nt.nodes.new("ShaderNodeEmission")
    tex = nt.nodes.new("ShaderNodeTexCoord")
    sep = nt.nodes.new("ShaderNodeSeparateXYZ")
    ramp = nt.nodes.new("ShaderNodeValToRGB")
    nt.links.new(tex.outputs["Window"], sep.inputs[0])
    nt.links.new(sep.outputs[1], ramp.inputs["Fac"])
    ramp.color_ramp.elements[0].color = (*hex_lin(hex_dark), 1)
    ramp.color_ramp.elements[1].color = (*hex_lin(hex_light), 1)
    nt.links.new(ramp.outputs["Color"], em.inputs["Color"])
    nt.links.new(em.outputs[0], out.inputs["Surface"])
    return mat


TOON_BANDS = {
    3: [(0.0, 0.62), (0.40, 0.82), (0.78, 1.0)],
    2: [(0.0, 0.64), (0.52, 1.0)],
    4: [(0.0, 0.46), (0.30, 0.64), (0.58, 0.84), (0.82, 1.0)],
}


def make_toon(name, hexcol, bands=3, kind="toon", suffix=""):
    mat = new_mat(name + suffix)
    nt = mat.node_tree
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    em = nt.nodes.new("ShaderNodeEmission")
    diff = nt.nodes.new("ShaderNodeBsdfDiffuse")
    diff.inputs["Color"].default_value = (1, 1, 1, 1)
    s2rgb = nt.nodes.new("ShaderNodeShaderToRGB")
    ramp = nt.nodes.new("ShaderNodeValToRGB")
    mixn = nt.nodes.new("ShaderNodeMixRGB")
    mixn.blend_type = "MULTIPLY"
    mixn.inputs["Fac"].default_value = 1.0
    ramp.color_ramp.interpolation = "CONSTANT"
    stops = TOON_BANDS.get(bands, TOON_BANDS[3])
    els = ramp.color_ramp.elements
    while len(els) > len(stops):
        els.remove(els[-1])
    while len(els) < len(stops):
        els.new(0.5)
    for i, (pos, val) in enumerate(stops):
        els[i].position = pos
        els[i].color = (val, val, val, 1.0)
    nt.links.new(diff.outputs[0], s2rgb.inputs[0])
    nt.links.new(s2rgb.outputs[0], ramp.inputs["Fac"])
    col = hex_lin(hexcol)
    mixn.inputs["Color1"].default_value = (col[0], col[1], col[2], 1)
    nt.links.new(ramp.outputs["Color"], mixn.inputs["Color2"])
    nt.links.new(mixn.outputs["Color"], em.inputs["Color"])
    if kind == "cel":
        rim = nt.nodes.new("ShaderNodeLayerWeight")
        rim.inputs["Blend"].default_value = 0.35
        rimramp = nt.nodes.new("ShaderNodeValToRGB")
        rimramp.color_ramp.interpolation = "CONSTANT"
        rimramp.color_ramp.elements[0].position = 0.0
        rimramp.color_ramp.elements[0].color = (1, 1, 1, 1)
        rimramp.color_ramp.elements[1].position = 0.62
        rimramp.color_ramp.elements[1].color = (1.0, 1.0, 1.0, 1)
        nt.links.new(rim.outputs["Facing"], rimramp.inputs["Fac"])
        mix2 = nt.nodes.new("ShaderNodeMixRGB")
        mix2.blend_type = "MULTIPLY"
        mix2.inputs["Fac"].default_value = 0.16
        nt.links.new(mixn.outputs["Color"], mix2.inputs["Color1"])
        mix2.inputs["Color2"].default_value = (1.25, 1.22, 1.30, 1)
        nt.links.new(mix2.outputs["Color"], em.inputs["Color"])
    nt.links.new(em.outputs[0], out.inputs["Surface"])
    return mat


def make_paint(name, hexcol, suffix=""):
    mat = new_mat(name + suffix)
    nt = mat.node_tree
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    em = nt.nodes.new("ShaderNodeEmission")
    tex = nt.nodes.new("ShaderNodeTexCoord")
    sep = nt.nodes.new("ShaderNodeSeparateXYZ")
    ramp = nt.nodes.new("ShaderNodeValToRGB")
    nt.links.new(tex.outputs["Window"], sep.inputs[0])
    nt.links.new(sep.outputs[1], ramp.inputs["Fac"])
    shade_col = hex_lin(lighten_hex(hexcol, 0.82))
    lit_col = hex_lin(lighten_hex(hexcol, 1.12))
    ramp.color_ramp.elements[0].color = (*shade_col, 1)
    ramp.color_ramp.elements[1].color = (*lit_col, 1)
    nt.links.new(ramp.outputs["Color"], em.inputs["Color"])
    nt.links.new(em.outputs[0], out.inputs["Surface"])
    return mat


def lighten_hex(h, f):
    from presets import lighten
    return lighten(h, f)


def make_outline_mat(darkhex="#101218", backface=True, suffix=""):
    mat = new_mat("outline_" + darkhex.replace("#", "") + suffix)
    nt = mat.node_tree
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    em = nt.nodes.new("ShaderNodeEmission")
    col = hex_lin(darkhex)
    em.inputs["Color"].default_value = (col[0], col[1], col[2], 1)
    nt.links.new(em.outputs[0], out.inputs["Surface"])
    mat.use_backface_culling = backface
    return mat


def link_obj(ob, coll):
    (coll or bpy.context.scene.collection).objects.link(ob)
    return ob


def mesh_object(name, bm, mat, coll, smooth=True):
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new(name, me)
    link_obj(ob, coll)
    if mat is not None:
        me.materials.append(mat)
    if smooth:
        for p in me.polygons:
            p.use_smooth = True
    return ob


def ellipsoid(name, center, radii, mat, coll, segments=24, rings=16, rot=None, smooth=True):
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=segments, v_segments=rings, radius=1.0)
    R = Matrix.Identity(4)
    if rot is not None:
        R = rot.to_matrix().to_4x4() if hasattr(rot, "to_matrix") else rot
    M = Matrix.Translation(Vector(center)) @ R @ Matrix.Diagonal(Vector(radii).to_4d())
    bmesh.ops.transform(bm, matrix=M, verts=bm.verts[:])
    return mesh_object(name, bm, mat, coll, smooth)


def cylinder(name, center, radius, depth, mat, coll, segments=24, rot=None, r2=None):
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=False, segments=segments,
                          radius1=radius, radius2=(radius if r2 is None else r2), depth=depth)
    R = Matrix.Identity(4)
    if rot is not None:
        R = rot.to_matrix().to_4x4() if hasattr(rot, "to_matrix") else rot
    bmesh.ops.transform(bm, matrix=Matrix.Translation(Vector(center)) @ R, verts=bm.verts[:])
    return mesh_object(name, bm, mat, coll, True)


def box(name, center, size, mat, coll, rot=None, bevel=0.0, seg=2):
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=Vector(size), verts=bm.verts[:])
    if bevel > 0:
        try:
            bmesh.ops.bevel(bm, geom=bm.edges[:] + bm.verts[:], offset=bevel, segments=seg,
                            profile=0.5, affect="EDGES", clamp_overlap=True)
        except Exception:
            pass
    R = Matrix.Identity(4)
    if rot is not None:
        R = rot.to_matrix().to_4x4() if hasattr(rot, "to_matrix") else rot
    bmesh.ops.transform(bm, matrix=Matrix.Translation(Vector(center)) @ R, verts=bm.verts[:])
    return mesh_object(name, bm, mat, coll, bevel > 0)


def tube(name, pts, radii, mat, coll, sides=10, flat=1.0, smooth=True, twist=0.0):
    pts = [Vector(p) for p in pts]
    n = len(pts)
    if n < 2:
        return None
    tans = []
    for i in range(n):
        if i == 0:
            t = pts[1] - pts[0]
        elif i == n - 1:
            t = pts[-1] - pts[-2]
        else:
            t = pts[i + 1] - pts[i - 1]
        if t.length < EPS:
            t = Vector((0, 0, 1))
        tans.append(t.normalized())
    up = Vector((0, 0, 1))
    if abs(tans[0].dot(up)) > 0.92:
        up = Vector((1, 0, 0))
    nrm = (up - tans[0] * up.dot(tans[0])).normalized()
    frames = []
    for i in range(n):
        if i > 0:
            q = tans[i - 1].rotation_difference(tans[i])
            nrm = q @ nrm
            nrm = (nrm - tans[i] * nrm.dot(tans[i])).normalized()
        binorm = tans[i].cross(nrm).normalized()
        frames.append((nrm, binorm))
    bm = bmesh.new()
    rings = []
    for i in range(n):
        nn, bb = frames[i]
        r = radii[i]
        w = r * flat if flat != 1.0 else r
        ring = []
        for k in range(sides):
            a = TAU * k / sides + twist
            v = bm.verts.new(pts[i] + nn * (math.cos(a) * r) + bb * (math.sin(a) * w))
            ring.append(v)
        rings.append(ring)
    for i in range(n - 1):
        for k in range(sides):
            a = rings[i][k]
            b = rings[i][(k + 1) % sides]
            c = rings[i + 1][(k + 1) % sides]
            d = rings[i + 1][k]
            bm.faces.new((a, b, c, d))
    bm.faces.new(list(reversed(rings[0])))
    bm.faces.new(rings[-1])
    bm.normal_update()
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    return mesh_object(name, bm, mat, coll, smooth)


def sphere_cap(name, center, radii, mat, coll, u_low=-1.0, u_high=1.0, segments=32, rings=14):
    """Partial ellipsoid shell (no thickness), u in [-1,1]."""
    bm = bmesh.new()
    verts = []
    for i in range(rings + 1):
        u = u_low + (u_high - u_low) * i / rings
        r = math.sqrt(max(0.0, 1.0 - u * u))
        ring = []
        for k in range(segments):
            a = TAU * k / segments
            p = Vector((radii[0] * r * math.cos(a), radii[1] * r * math.sin(a), radii[2] * u))
            ring.append(bm.verts.new(center + p))
        verts.append(ring)
    for i in range(rings):
        for k in range(segments):
            a = verts[i][k]
            b = verts[i][(k + 1) % segments]
            c = verts[i + 1][(k + 1) % segments]
            d = verts[i + 1][k]
            try:
                bm.faces.new((a, b, c, d))
            except Exception:
                pass
    bm.normal_update()
    return mesh_object(name, bm, mat, coll, True)


def star(name, center, r_out, r_in, depth, mat, coll, points=5, rot=None):
    bm = bmesh.new()
    front = []
    back = []
    for i in range(points * 2):
        a = math.pi * 2 * i / (points * 2) + math.pi / 2
        r = r_out if i % 2 == 0 else r_in
        x = math.cos(a) * r
        z = math.sin(a) * r
        front.append(bm.verts.new((x, -depth * 0.5, z)))
        back.append(bm.verts.new((x, depth * 0.5, z)))
    bm.faces.new(list(reversed(front)))
    bm.faces.new(back)
    n = points * 2
    for i in range(n):
        a = front[i]
        b = front[(i + 1) % n]
        c = back[(i + 1) % n]
        d = back[i]
        bm.faces.new((a, b, c, d))
    bm.normal_update()
    norm = bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    R = Matrix.Identity(4)
    if rot is not None:
        R = rot.to_matrix().to_4x4() if hasattr(rot, "to_matrix") else rot
    bmesh.ops.transform(bm, matrix=Matrix.Translation(Vector(center)) @ R, verts=bm.verts[:])
    return mesh_object(name, bm, mat, coll, False)


def add_outline(src, thickness, mat, coll, name=None):
    me = src.data.copy()
    bm = bmesh.new()
    bm.from_mesh(me)
    bm.normal_update()
    for v in bm.verts:
        v.co = v.co + v.normal * thickness
    bmesh.ops.reverse_faces(bm, faces=bm.faces[:])
    bm.to_mesh(me)
    bm.free()
    me.materials.clear()
    me.materials.append(mat)
    ob = bpy.data.objects.new(name or (src.name + "_OL"), me)
    link_obj(ob, coll)
    for p in me.polygons:
        p.use_smooth = True
    try:
        ob.visible_shadow = False
    except Exception:
        pass
    return ob


def join_objects(objs, name, coll):
    if not objs:
        return None
    bpy.ops.object.select_all(action="DESELECT")
    for o in objs:
        o.select_set(True)
    bpy.context.view_layer.objects.active = objs[0]
    bpy.ops.object.join()
    ob = bpy.context.view_layer.objects.active
    ob.name = name
    return ob
