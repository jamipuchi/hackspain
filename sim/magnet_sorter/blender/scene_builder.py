"""Build the photoreal Blender scene from scene_def (runs inside Blender's Python).

Every MuJoCo body becomes an Empty; its geoms are children with the local pose from scene_def.
Per frame we only move the Empties with the body poses MuJoCo computed, so physics and render
always agree.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import bpy
import numpy as np
from mathutils import Matrix, Quaternion, Vector

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))
import scene_def as sd  # noqa: E402

BLENDER_RES = Path(bpy.app.binary_path).resolve().parents[1] / "Resources"
HDRI_DIR = next(BLENDER_RES.glob("*/datafiles/studiolights/world"), None)
MARKER_FLIP_Y = False  # set by the orientation test; MuJoCo and Blender must show the same marker


# --------------------------------------------------------------------------- materials
def _principled(mat: bpy.types.Material) -> bpy.types.ShaderNode:
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    return next(n for n in nodes if n.type == "BSDF_PRINCIPLED")


def _set(node, name: str, value) -> None:
    if name in node.inputs:
        node.inputs[name].default_value = value


def make_material(spec: sd.Material, texture_override: str | None = None) -> bpy.types.Material:
    key = f"{spec.name}:{texture_override or ''}"
    if key in bpy.data.materials:
        return bpy.data.materials[key]
    mat = bpy.data.materials.new(key)
    bsdf = _principled(mat)
    nt = mat.node_tree
    _set(bsdf, "Base Color", (*spec.base_color, 1.0))
    _set(bsdf, "Metallic", spec.metallic)
    _set(bsdf, "Roughness", spec.roughness)
    _set(bsdf, "Anisotropic", spec.anisotropic)
    if spec.transmission > 0:
        _set(bsdf, "Transmission Weight", spec.transmission)
        _set(bsdf, "IOR", 1.45)
        _set(bsdf, "Roughness", spec.roughness)
        mat.blend_method = "BLEND" if hasattr(mat, "blend_method") else None
    if spec.emission:
        _set(bsdf, "Emission Color", (*spec.emission, 1.0))
        _set(bsdf, "Emission Strength", 4.0)

    tex_file = texture_override or spec.texture
    if tex_file:
        img = bpy.data.images.load(str(sd.ASSETS / tex_file), check_existing=True)
        tex = nt.nodes.new("ShaderNodeTexImage")
        tex.image = img
        tex.interpolation = "Cubic" if "aruco" in tex_file else "Linear"
        nt.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
        if spec.procedural == "wood":
            bump = nt.nodes.new("ShaderNodeBump")
            bump.inputs["Strength"].default_value = 0.25
            bump.inputs["Distance"].default_value = 0.0006
            nt.links.new(tex.outputs["Color"], bump.inputs["Height"])
            nt.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
            _set(bsdf, "Roughness", 0.5)
        if spec.procedural == "mdf":
            # pressed-fibre bump + slight roughness variation
            noise = nt.nodes.new("ShaderNodeTexNoise")
            noise.inputs["Scale"].default_value = 900.0
            noise.inputs["Detail"].default_value = 6.0
            bump = nt.nodes.new("ShaderNodeBump")
            bump.inputs["Strength"].default_value = 0.15
            bump.inputs["Distance"].default_value = 0.0004
            nt.links.new(noise.outputs["Fac"], bump.inputs["Height"])
            nt.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
    elif spec.procedural == "rust":
        # patchy rust: dark orange-brown blotches over pitted dull steel
        noise = nt.nodes.new("ShaderNodeTexNoise")
        noise.inputs["Scale"].default_value = 900.0
        noise.inputs["Detail"].default_value = 8.0
        noise.inputs["Roughness"].default_value = 0.8
        ramp = nt.nodes.new("ShaderNodeValToRGB")
        ramp.color_ramp.elements[0].position = 0.35
        ramp.color_ramp.elements[0].color = (0.22, 0.08, 0.03, 1)
        ramp.color_ramp.elements[1].position = 0.65
        ramp.color_ramp.elements[1].color = (0.55, 0.27, 0.10, 1)
        nt.links.new(noise.outputs["Fac"], ramp.inputs["Fac"])
        nt.links.new(ramp.outputs["Color"], bsdf.inputs["Base Color"])
        _set(bsdf, "Metallic", 0.3)
        _set(bsdf, "Roughness", 0.9)
        bump = nt.nodes.new("ShaderNodeBump")
        bump.inputs["Strength"].default_value = 0.5
        bump.inputs["Distance"].default_value = 0.0003
        nt.links.new(noise.outputs["Fac"], bump.inputs["Height"])
        nt.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
    elif spec.metallic >= 0.9:
        # machined metal: micro-scratches vary roughness, tiny bump breaks up perfect reflections
        noise = nt.nodes.new("ShaderNodeTexNoise")
        noise.inputs["Scale"].default_value = 2500.0
        noise.inputs["Detail"].default_value = 4.0
        noise.inputs["Roughness"].default_value = 0.7
        ramp = nt.nodes.new("ShaderNodeValToRGB")
        lo, hi = max(0.02, spec.roughness - 0.12), min(0.95, spec.roughness + 0.12)
        ramp.color_ramp.elements[0].color = (lo, lo, lo, 1)
        ramp.color_ramp.elements[1].color = (hi, hi, hi, 1)
        nt.links.new(noise.outputs["Fac"], ramp.inputs["Fac"])
        nt.links.new(ramp.outputs["Color"], bsdf.inputs["Roughness"])
        bump = nt.nodes.new("ShaderNodeBump")
        bump.inputs["Strength"].default_value = 0.08
        bump.inputs["Distance"].default_value = 0.00005
        nt.links.new(noise.outputs["Fac"], bump.inputs["Height"])
        nt.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
        if spec.name == "steel_zinc":
            # zinc plating: faint yellow/blue iridescent tint variation
            tint = nt.nodes.new("ShaderNodeTexNoise")
            tint.inputs["Scale"].default_value = 300.0
            mix = nt.nodes.new("ShaderNodeMix")
            mix.data_type = "RGBA"
            mix.inputs["Factor"].default_value = 0.18
            mix.inputs[6].default_value = (*spec.base_color, 1.0)
            mix.inputs[7].default_value = (0.78, 0.72, 0.52, 1.0)
            nt.links.new(tint.outputs["Fac"], mix.inputs["Factor"])
            nt.links.new(mix.outputs[2], bsdf.inputs["Base Color"])
    elif spec.name.startswith("pla"):
        # FDM print: fine layer lines as anisotropic bump
        wave = nt.nodes.new("ShaderNodeTexWave")
        wave.wave_type = "BANDS"
        wave.bands_direction = "Z"
        wave.inputs["Scale"].default_value = 2600.0  # 0.2 mm layers → ~5000 per metre; softened
        bump = nt.nodes.new("ShaderNodeBump")
        bump.inputs["Strength"].default_value = 0.12
        bump.inputs["Distance"].default_value = 0.00006
        nt.links.new(wave.outputs["Fac"], bump.inputs["Height"])
        nt.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
        _set(bsdf, "Roughness", 0.55)
    return mat


# --------------------------------------------------------------------------- geometry
def _bevel(obj: bpy.types.Object, width: float, segments: int = 3) -> None:
    mod = obj.modifiers.new("bevel", "BEVEL")
    mod.width = width
    mod.segments = segments
    mod.limit_method = "ANGLE"
    mod.angle_limit = math.radians(40)
    for p in obj.data.polygons:
        p.use_smooth = False


def _smooth(obj: bpy.types.Object) -> None:
    for p in obj.data.polygons:
        p.use_smooth = True
    if hasattr(obj.data, "use_auto_smooth"):
        obj.data.use_auto_smooth = True


def _new_object(name: str, mesh: bpy.types.Mesh | None, parent: bpy.types.Object, geom: sd.Geom) -> bpy.types.Object:
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj.parent = parent
    obj.location = Vector(geom.pos)
    obj.rotation_mode = "QUATERNION"
    obj.rotation_quaternion = Quaternion(geom.quat)
    return obj


def add_box(name, geom: sd.Geom, parent, mat):
    bpy.ops.mesh.primitive_cube_add(size=2.0)
    src = bpy.context.active_object
    mesh = src.data
    bpy.data.objects.remove(src)
    obj = _new_object(name, mesh, parent, geom)
    obj.scale = Vector(geom.size)
    obj.data.materials.append(mat)
    w = min(0.0007, min(geom.size) * 0.35)
    # bevel in object space is scaled non-uniformly; approximate by applying scale first
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    obj.select_set(False)
    if w > 0.00005:
        _bevel(obj, w)
    return obj


def add_cylinder(name, geom: sd.Geom, parent, mat, vertices: int = 48):
    r, hh = geom.size[0], geom.size[1]
    bpy.ops.mesh.primitive_cylinder_add(vertices=vertices, radius=r, depth=2 * hh)
    src = bpy.context.active_object
    mesh = src.data
    bpy.data.objects.remove(src)
    obj = _new_object(name, mesh, parent, geom)
    obj.data.materials.append(mat)
    _smooth(obj)
    _bevel(obj, min(0.0006, r * 0.15, hh * 0.3), segments=3)
    return obj


def add_hexprism(name, geom: sd.Geom, parent, mat):
    r, hh = geom.size[0], geom.size[1]
    bpy.ops.mesh.primitive_cylinder_add(vertices=6, radius=r, depth=2 * hh)
    src = bpy.context.active_object
    mesh = src.data
    bpy.data.objects.remove(src)
    obj = _new_object(name, mesh, parent, geom)
    obj.rotation_quaternion = Quaternion(geom.quat) @ Quaternion((0, 0, 1), math.radians(30))
    obj.data.materials.append(mat)
    _bevel(obj, min(0.0008, r * 0.12, hh * 0.25), segments=3)  # chamfered nut edges
    return obj


def add_wire(name, geom: sd.Geom, parent, mat):
    pts = sd.wire_points(geom.points, n=max(12, 8 * len(geom.points)))
    curve = bpy.data.curves.new(name, "CURVE")
    curve.dimensions = "3D"
    spline = curve.splines.new("NURBS")
    spline.points.add(len(pts) - 1)
    for p, (x, y, z) in zip(spline.points, pts):
        p.co = (x, y, z, 1.0)
    spline.use_endpoint_u = True
    spline.order_u = 3
    curve.bevel_depth = geom.size[0]
    curve.bevel_resolution = 6
    curve.resolution_u = 6
    curve.use_fill_caps = True
    obj = bpy.data.objects.new(name, curve)
    bpy.context.scene.collection.objects.link(obj)
    obj.parent = parent
    obj.data.materials.append(mat)
    return obj


def add_marker(name, geom: sd.Geom, parent):
    bpy.ops.mesh.primitive_plane_add(size=2.0)
    src = bpy.context.active_object
    mesh = src.data
    bpy.data.objects.remove(src)
    obj = _new_object(name, mesh, parent, geom)
    obj.scale = Vector((geom.size[0], geom.size[1], 1.0))
    obj.location = Vector((geom.pos[0], geom.pos[1], geom.pos[2] + geom.size[2]))
    if MARKER_FLIP_Y:
        obj.scale.y = -geom.size[1]
    mat = make_material(sd.MATERIALS["marker"], texture_override=f"aruco_{geom.marker_id}.png")
    obj.data.materials.append(mat)
    return obj


def build_scene(bodies: list[sd.Body]) -> dict[str, bpy.types.Object]:
    empties: dict[str, bpy.types.Object] = {}
    for b in bodies:
        empty = bpy.data.objects.new(f"body:{b.name}", None)
        empty.empty_display_size = 0.01
        bpy.context.scene.collection.objects.link(empty)
        empty.rotation_mode = "QUATERNION"
        empties[b.name] = empty
        holes = {g.name: g for g in b.geoms if g.name and g.name.endswith("_hole")}
        for gi, g in enumerate(b.geoms):
            gname = f"{b.name}:{g.name or gi}"
            if g.name and g.name.endswith("_hole"):
                continue  # cutter, handled with the parent geom
            mat = make_material(sd.MATERIALS[g.material]) if g.kind != "marker" else None
            if g.kind == "box":
                obj = add_box(gname, g, empty, mat)
            elif g.kind == "cylinder":
                obj = add_cylinder(gname, g, empty, mat)
            elif g.kind == "hexprism":
                obj = add_hexprism(gname, g, empty, mat)
            elif g.kind == "wire":
                obj = add_wire(gname, g, empty, mat)
            elif g.kind == "marker":
                obj = add_marker(gname, g, empty)
            else:
                continue
            # real holes in nuts and washers via boolean
            if b.piece and g.name and (g.name.endswith("_hex") or g.name.endswith("_ring")):
                hole = holes.get(g.name.rsplit("_", 1)[0] + "_hole")
                if hole is not None:
                    bpy.ops.mesh.primitive_cylinder_add(vertices=48, radius=hole.size[0], depth=2 * hole.size[1] + 0.002)
                    cutter = bpy.context.active_object
                    cutter.name = f"{gname}_cutter"
                    cutter.parent = empty
                    cutter.location = Vector(hole.pos)
                    cutter.hide_render = True
                    cutter.hide_viewport = True
                    cutter.display_type = "WIRE"
                    mod = obj.modifiers.new("hole", "BOOLEAN")
                    mod.operation = "DIFFERENCE"
                    mod.object = cutter
                    mod.solver = "EXACT"
                    # thread hint: dark inner wall
                    obj.data.materials.append(make_material(sd.MATERIALS["steel_black"]))
    return empties


# --------------------------------------------------------------------------- world, cameras, render
def setup_world(hdri: str = "interior.exr", strength: float = 0.45) -> None:
    scene = bpy.context.scene
    world = scene.world or bpy.data.worlds.new("World")
    scene.world = world
    world.use_nodes = True
    nt = world.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputWorld")
    bg = nt.nodes.new("ShaderNodeBackground")
    bg.inputs["Strength"].default_value = strength
    if HDRI_DIR and (HDRI_DIR / hdri).exists():
        env = nt.nodes.new("ShaderNodeTexEnvironment")
        env.image = bpy.data.images.load(str(HDRI_DIR / hdri), check_existing=True)
        mapping = nt.nodes.new("ShaderNodeMapping")
        mapping.inputs["Rotation"].default_value = (0, 0, math.radians(35))
        coord = nt.nodes.new("ShaderNodeTexCoord")
        nt.links.new(coord.outputs["Generated"], mapping.inputs["Vector"])
        nt.links.new(mapping.outputs["Vector"], env.inputs["Vector"])
        nt.links.new(env.outputs["Color"], bg.inputs["Color"])
    else:
        bg.inputs["Color"].default_value = (0.6, 0.62, 0.65, 1.0)
    nt.links.new(bg.outputs["Background"], out.inputs["Surface"])

    # desk lamp: warm area light above-left, plus a cool soft fill
    key = bpy.data.lights.new("key", "AREA")
    key.energy = 16.0
    key.size = 0.35
    key.color = (1.0, 0.93, 0.85)
    ko = bpy.data.objects.new("key", key)
    scene.collection.objects.link(ko)
    ko.location = (0.05, -0.25, 0.75)
    ko.rotation_euler = (math.radians(18), math.radians(-8), math.radians(-20))
    fill = bpy.data.lights.new("fill", "AREA")
    fill.energy = 6.0
    fill.size = 0.8
    fill.color = (0.85, 0.9, 1.0)
    fo = bpy.data.objects.new("fill", fill)
    scene.collection.objects.link(fo)
    fo.location = (0.5, 0.5, 0.7)
    fo.rotation_euler = (math.radians(-30), math.radians(25), 0)

    # a desk under the board and a wall behind, so reflections have something to show
    bpy.ops.mesh.primitive_plane_add(size=3.0, location=(0.1, 0.0, -0.0121))
    desk = bpy.context.active_object
    desk.name = "desk"
    desk_mat = bpy.data.materials.new("desk")
    b = _principled(desk_mat)
    _set(b, "Base Color", (0.12, 0.11, 0.10, 1.0))
    _set(b, "Roughness", 0.35)
    desk.data.materials.append(desk_mat)
    bpy.ops.mesh.primitive_plane_add(size=4.0, location=(-0.9, 0.0, 1.2), rotation=(0, math.radians(90), 0))
    wall = bpy.context.active_object
    wall.name = "wall"
    wall_mat = bpy.data.materials.new("wall")
    b = _principled(wall_mat)
    _set(b, "Base Color", (0.82, 0.80, 0.76, 1.0))
    _set(b, "Roughness", 0.9)
    wall.data.materials.append(wall_mat)


def _camera_matrix(pos, x_axis, y_axis) -> Matrix:
    x = Vector(x_axis).normalized()
    y = Vector(y_axis).normalized()
    z = x.cross(y).normalized()
    m = Matrix.Identity(4)
    for i in range(3):
        m[i][0], m[i][1], m[i][2], m[i][3] = x[i], y[i], z[i], pos[i]
    return m


def setup_cameras() -> dict[str, bpy.types.Object]:
    cams = {}
    scene = bpy.context.scene
    # phone: looking straight down, image right = world −y, image up = world +x (same as MuJoCo xyaxes)
    pc = bpy.data.cameras.new("phone")
    pc.sensor_fit = "VERTICAL"
    pc.angle_y = math.radians(sd.PHONE_CAM["fovy_deg"])
    pc.dof.use_dof = True
    pc.dof.focus_distance = sd.PHONE_CAM["pos"][2]
    pc.dof.aperture_fstop = 2.8
    po = bpy.data.objects.new("cam_phone", pc)
    scene.collection.objects.link(po)
    po.matrix_world = _camera_matrix(sd.PHONE_CAM["pos"], (0, -1, 0), (1, 0, 0))
    cams["phone"] = po

    for cname, cam in sd.CAMERAS.items():
        c = bpy.data.cameras.new(f"cam_{cname}")
        c.sensor_fit = "VERTICAL"
        c.angle_y = math.radians(cam["fovy_deg"])
        c.dof.use_dof = True
        c.dof.aperture_fstop = 2.8
        pos_c, look_c = Vector(cam["pos"]), Vector(cam["lookat"])
        fwd_c = (look_c - pos_c).normalized()
        right_c = fwd_c.cross(Vector((0, 0, 1))).normalized()
        up_c = right_c.cross(fwd_c).normalized()
        o = bpy.data.objects.new(f"cam_{cname}", c)
        scene.collection.objects.link(o)
        o.matrix_world = _camera_matrix(pos_c, right_c, up_c)
        c.dof.focus_distance = (look_c - pos_c).length
        cams[cname] = o

    cc = bpy.data.cameras.new("cine")
    cc.sensor_fit = "VERTICAL"
    cc.angle_y = math.radians(sd.CINE_CAM["fovy_deg"])
    cc.dof.use_dof = True
    cc.dof.aperture_fstop = 4.0
    pos = Vector(sd.CINE_CAM["pos"])
    look = Vector(sd.CINE_CAM["lookat"])
    fwd = (look - pos).normalized()
    right = fwd.cross(Vector((0, 0, 1))).normalized()
    up = right.cross(fwd).normalized()
    co = bpy.data.objects.new("cam_cine", cc)
    scene.collection.objects.link(co)
    co.matrix_world = _camera_matrix(pos, right, up)
    cc.dof.focus_distance = (look - pos).length
    cams["cine"] = co
    return cams


def setup_render(samples: int = 96, engine: str = "CYCLES") -> None:
    scene = bpy.context.scene
    scene.render.engine = engine
    if engine == "CYCLES":
        prefs = bpy.context.preferences.addons["cycles"].preferences
        prefs.compute_device_type = "METAL"
        prefs.get_devices()
        for d in prefs.devices:
            d.use = d.type == "METAL"
        scene.cycles.device = "GPU"
        scene.cycles.samples = samples
        scene.cycles.use_adaptive_sampling = True
        scene.cycles.adaptive_threshold = 0.02
        scene.cycles.use_denoising = True
        scene.cycles.denoiser = "OPENIMAGEDENOISE"
        scene.cycles.max_bounces = 6
        scene.cycles.caustics_reflective = False
        scene.cycles.caustics_refractive = False
        scene.render.use_persistent_data = True
    scene.view_settings.view_transform = "AgX"
    scene.view_settings.look = "AgX - Medium High Contrast"
    scene.view_settings.exposure = 0.0
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGB"
    scene.render.film_transparent = False


def set_poses(empties: dict[str, bpy.types.Object], poses: dict[str, list[float]]) -> None:
    for name, p in poses.items():
        e = empties.get(name)
        if e is None:
            continue
        e.location = Vector(p[:3])
        e.rotation_quaternion = Quaternion(p[3:7])


def render(camera_obj: bpy.types.Object, out_path: str, width: int, height: int, samples: int | None = None) -> None:
    scene = bpy.context.scene
    scene.camera = camera_obj
    scene.render.resolution_x = width
    scene.render.resolution_y = height
    scene.render.resolution_percentage = 100
    if samples is not None and scene.render.engine == "CYCLES":
        scene.cycles.samples = samples
    scene.render.filepath = out_path
    bpy.ops.render.render(write_still=True)


def fresh_scene() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.context.scene.unit_settings.system = "METRIC"
    bpy.context.scene.unit_settings.scale_length = 1.0


def build_everything(samples: int = 96, engine: str = "CYCLES"):
    fresh_scene()
    bodies = sd.build()
    empties = build_scene(bodies)
    setup_world()
    cams = setup_cameras()
    setup_render(samples, engine)
    return empties, cams
