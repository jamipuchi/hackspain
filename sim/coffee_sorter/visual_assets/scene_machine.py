"""Build the recorded coffee-sorter geometry with presentation-only detail.

``build_machine(payload)`` owns the ``CinematicMachine`` collection.  It does
not create cameras, lighting, beans, render configuration, or output files.
"""
from __future__ import annotations

import bpy
from mathutils import Quaternion


COLLECTION_NAME = "CinematicMachine"
QUARTER_TURN_X = Quaternion((0.70710678, 0.70710678, 0.0, 0.0))
QUARTER_TURN_Y = Quaternion((0.70710678, 0.0, 0.70710678, 0.0))


def _machine_collection():
    collection = bpy.data.collections.get(COLLECTION_NAME)
    if collection is None:
        collection = bpy.data.collections.new(COLLECTION_NAME)
        bpy.context.scene.collection.children.link(collection)
    elif collection not in tuple(bpy.context.scene.collection.children):
        bpy.context.scene.collection.children.link(collection)
    for obj in tuple(collection.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    return collection


def _move_to_collection(obj, collection):
    for owner in tuple(obj.users_collection):
        owner.objects.unlink(obj)
    collection.objects.link(obj)
    return obj


def _assign_material(obj, material):
    obj.data.materials.clear()
    obj.data.materials.append(material)


def _box(collection, name, location, dimensions, material, bevel=0.0):
    bpy.ops.mesh.primitive_cube_add(size=2.0, location=location)
    obj = bpy.context.object
    obj.name = name
    obj.dimensions = dimensions
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    _assign_material(obj, material)
    _move_to_collection(obj, collection)
    if bevel:
        modifier = obj.modifiers.new("Edge radii", "BEVEL")
        modifier.width = bevel
        modifier.segments = 2
    return obj


def _cylinder(collection, name, location, radius, depth, material, quaternion=None, bevel=0.0):
    bpy.ops.mesh.primitive_cylinder_add(vertices=24, radius=radius, depth=depth, location=location)
    obj = bpy.context.object
    obj.name = name
    _assign_material(obj, material)
    _move_to_collection(obj, collection)
    if quaternion is not None:
        obj.rotation_mode = "QUATERNION"
        obj.rotation_quaternion = quaternion
    for polygon in obj.data.polygons:
        polygon.use_smooth = True
    if bevel:
        modifier = obj.modifiers.new("Machined edges", "BEVEL")
        modifier.width = bevel
        modifier.segments = 2
    return obj


def _copy_mesh(collection, source, name, location, quaternion=None):
    obj = source.copy()
    obj.name = name
    obj.data = source.data
    collection.objects.link(obj)
    obj.location = location
    if quaternion is not None:
        obj.rotation_mode = "QUATERNION"
        obj.rotation_quaternion = quaternion
    return obj


def _material(name, color, roughness, metallic=0.0, brushed=False):
    material = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    material.diffuse_color = (*color, 1.0)
    material.use_nodes = True
    nodes = material.node_tree.nodes
    nodes.clear()
    output = nodes.new("ShaderNodeOutputMaterial")
    shader = nodes.new("ShaderNodeBsdfPrincipled")
    shader.inputs["Base Color"].default_value = (*color, 1.0)
    shader.inputs["Metallic"].default_value = metallic
    shader.inputs["Roughness"].default_value = roughness
    if "Specular IOR Level" in shader.inputs:
        shader.inputs["Specular IOR Level"].default_value = 0.32
    material.node_tree.links.new(shader.outputs["BSDF"], output.inputs["Surface"])
    if not brushed:
        return material

    coordinates = nodes.new("ShaderNodeTexCoord")
    fine_noise = nodes.new("ShaderNodeTexNoise")
    scratches = nodes.new("ShaderNodeTexWave")
    bump = nodes.new("ShaderNodeBump")
    roughness_range = nodes.new("ShaderNodeMapRange")
    fine_noise.inputs["Scale"].default_value = 115.0
    fine_noise.inputs["Detail"].default_value = 2.0
    fine_noise.inputs["Roughness"].default_value = 0.42
    scratches.wave_type = "BANDS"
    scratches.bands_direction = "X"
    scratches.inputs["Scale"].default_value = 360.0
    scratches.inputs["Distortion"].default_value = 2.0
    scratches.inputs["Detail"].default_value = 2.0
    bump.inputs["Strength"].default_value = 0.10
    bump.inputs["Distance"].default_value = 0.000018
    roughness_range.inputs["From Min"].default_value = 0.0
    roughness_range.inputs["From Max"].default_value = 1.0
    roughness_range.inputs["To Min"].default_value = max(0.05, roughness - 0.08)
    roughness_range.inputs["To Max"].default_value = min(0.95, roughness + 0.06)
    links = material.node_tree.links
    links.new(coordinates.outputs["Generated"], fine_noise.inputs["Vector"])
    links.new(coordinates.outputs["Generated"], scratches.inputs["Vector"])
    links.new(scratches.outputs["Color"], bump.inputs["Height"])
    links.new(bump.outputs["Normal"], shader.inputs["Normal"])
    links.new(fine_noise.outputs["Fac"], roughness_range.inputs["Value"])
    links.new(roughness_range.outputs["Result"], shader.inputs["Roughness"])
    return material


def _materials():
    return {
        "stainless": _material("Sorter stainless", (0.34, 0.37, 0.39), 0.28, 0.86, True),
        "graphite": _material("Sorter graphite", (0.028, 0.034, 0.042), 0.39, 0.18),
        "rubber": _material("Sorter belt rubber", (0.018, 0.026, 0.030), 0.68, 0.02),
        "machined": _material("Machined roller steel", (0.22, 0.25, 0.27), 0.20, 0.92, True),
        "chute": _material("Sorter chute", (0.17, 0.20, 0.22), 0.32, 0.78, True),
        "floor": _material("Sorter studio floor", (0.035, 0.031, 0.027), 0.74),
        "accept": _material("Accept path", (0.09, 0.34, 0.16), 0.42, 0.10),
        "reject": _material("Reject path", (0.42, 0.08, 0.045), 0.42, 0.10),
        "control": _material("Control box", (0.055, 0.065, 0.075), 0.33, 0.22),
        "conduit": _material("Conduit rubber", (0.018, 0.022, 0.026), 0.61, 0.04),
        "diffuser": _material("Inspection diffuser", (0.86, 0.84, 0.71), 0.25, 0.06),
    }


def _recorded_material(geometry, materials):
    name = geometry.get("name")
    if name == "bin_accept":
        return materials["accept"]
    if name == "bin_reject":
        return materials["reject"]
    return {
        "belt": materials["rubber"],
        "frame": materials["graphite"],
        "steel": materials["stainless"],
        "floor": materials["floor"],
        "chute": materials["chute"],
    }.get(geometry.get("material"), materials["diffuser"])


def _add_recorded_surfaces(payload, collection, materials):
    count = 0
    for index, geometry in enumerate(payload["machine"]):
        name = geometry.get("name") or f"Recorded surface {index:02d}"
        material = _recorded_material(geometry, materials)
        if geometry["type"] == 6:
            obj = _box(collection, name, geometry["pos"], [2.0 * value for value in geometry["size"]], material)
        elif geometry["type"] == 5:
            obj = _cylinder(collection, name, geometry["pos"], geometry["size"][0], 2.0 * geometry["size"][1], material)
        else:
            raise ValueError(f"Unsupported recorded machine primitive: {geometry['type']}")
        obj.rotation_mode = "QUATERNION"
        obj.rotation_quaternion = Quaternion(geometry["quat"])
        obj["recorded_machine_surface"] = True
        count += 1
    return count


def _add_fasteners(collection, materials):
    positions = []
    for x in (-1.00, -0.76, -0.50, -0.25):
        positions.extend(((x, 0.260, 0.615), (x, -0.260, 0.615)))
    master = _cylinder(collection, "Rail fastener 00", positions[0], 0.0058, 0.004, materials["machined"], QUARTER_TURN_X, 0.0007)
    for index, position in enumerate(positions[1:], 1):
        _copy_mesh(collection, master, f"Rail fastener {index:02d}", position, QUARTER_TURN_X)
    return len(positions)


def _add_nozzle_manifold(collection, layout, materials):
    belt_z = layout["belt_z"]
    belt_w = layout["belt_w"]
    n_nozzles = layout["n_nozzles"]
    x = layout["ej_x"]
    nozzle_z = belt_z + layout["ej_z_offset"]
    _cylinder(collection, "Air manifold", (x, 0.0, belt_z + 0.091), 0.012, belt_w + 0.080, materials["machined"], QUARTER_TURN_X, 0.001)
    _cylinder(collection, "Air manifold left cap", (x, -belt_w / 2 - 0.045, belt_z + 0.091), 0.017, 0.010, materials["stainless"], QUARTER_TURN_X, 0.001)
    _cylinder(collection, "Air manifold right cap", (x, belt_w / 2 + 0.045, belt_z + 0.091), 0.017, 0.010, materials["stainless"], QUARTER_TURN_X, 0.001)
    first_y = -belt_w / 2 + belt_w / n_nozzles * 0.5
    stem = _cylinder(collection, "Nozzle stem 00", (x, first_y, belt_z + 0.076), 0.0028, 0.026, materials["machined"], None, 0.0004)
    nozzle = _cylinder(collection, "Nozzle 00", (x, first_y, nozzle_z), 0.0042, 0.012, materials["stainless"], None, 0.0005)
    for index in range(1, n_nozzles):
        y = -belt_w / 2 + belt_w / n_nozzles * (index + 0.5)
        _copy_mesh(collection, stem, f"Nozzle stem {index:02d}", (x, y, belt_z + 0.076))
        _copy_mesh(collection, nozzle, f"Nozzle {index:02d}", (x, y, nozzle_z))
    return n_nozzles


def _add_presentation_details(collection, materials):
    detail_count = 0
    for y, side in ((0.315, "right"), (-0.315, "left")):
        _box(collection, f"Lower frame rail {side}", (-0.55, y, 0.255), (0.94, 0.030, 0.045), materials["graphite"], 0.004)
        _cylinder(collection, f"Lower cross tube {side}", (-0.55, y, 0.135), 0.014, 0.92, materials["graphite"], QUARTER_TURN_Y, 0.001)
        detail_count += 2
    for x in (-1.0, -0.1):
        for y, side in ((0.30, "right"), (-0.30, "left")):
            _cylinder(collection, f"Leveling foot {x:.1f} {side}", (x, y, 0.030), 0.046, 0.060, materials["graphite"], None, 0.003)
            _cylinder(collection, f"Foot collar {x:.1f} {side}", (x, y, 0.065), 0.025, 0.012, materials["machined"], None, 0.001)
            detail_count += 2

    _cylinder(collection, "Drive motor", (-1.14, 0.355, 0.495), 0.073, 0.145, materials["graphite"], QUARTER_TURN_X, 0.003)
    _cylinder(collection, "Motor end bell", (-1.14, 0.435, 0.495), 0.059, 0.018, materials["machined"], QUARTER_TURN_X, 0.002)
    _box(collection, "Motor mounting plate", (-1.10, 0.326, 0.425), (0.17, 0.038, 0.12), materials["graphite"], 0.003)
    _box(collection, "Control enclosure", (-0.53, -0.355, 0.435), (0.19, 0.090, 0.205), materials["control"], 0.006)
    _box(collection, "Control enclosure face", (-0.53, -0.405, 0.445), (0.135, 0.006, 0.120), materials["graphite"], 0.001)
    _box(collection, "Control enclosure top seam", (-0.53, -0.407, 0.508), (0.155, 0.004, 0.004), materials["machined"], 0.0005)
    _box(collection, "Control enclosure lower seam", (-0.53, -0.407, 0.382), (0.155, 0.004, 0.004), materials["machined"], 0.0005)
    _cylinder(collection, "Emergency control guard", (-0.49, -0.410, 0.475), 0.015, 0.010, materials["reject"], QUARTER_TURN_X, 0.001)
    _cylinder(collection, "Motor-side conduit", (-0.82, 0.362, 0.410), 0.010, 0.56, materials["conduit"], QUARTER_TURN_Y)
    _cylinder(collection, "Control-side conduit", (-0.39, -0.412, 0.365), 0.009, 0.30, materials["conduit"], QUARTER_TURN_Y)
    detail_count += 10

    for x, name in ((-0.24, "upstream"), (0.00, "downstream")):
        for y, side in ((0.345, "right"), (-0.345, "left")):
            _box(collection, f"Camera gantry post {name} {side}", (x, y, 0.815), (0.032, 0.032, 0.56), materials["graphite"], 0.003)
            detail_count += 1
    _box(collection, "Camera gantry bridge", (-0.12, 0.0, 1.105), (0.28, 0.72, 0.055), materials["graphite"], 0.006)
    _box(collection, "Camera shroud", (-0.12, 0.0, 1.062), (0.078, 0.190, 0.040), materials["control"], 0.004)
    for x, name in ((-0.17, "one"), (-0.07, "two")):
        _box(collection, f"Inspection light heatsink {name}", (x, 0.0, 0.875), (0.026, 0.565, 0.024), materials["machined"], 0.002)
        detail_count += 1
    detail_count += 2

    for x, name in ((-1.10, "feed"), (0.0, "drive")):
        for y, side in ((0.274, "right"), (-0.274, "left")):
            _cylinder(collection, f"Roller end cap {name} {side}", (x, y, 0.570), 0.040, 0.020, materials["machined"], QUARTER_TURN_X, 0.001)
            _cylinder(collection, f"Roller collar {name} {side}", (x, y * 1.035, 0.570), 0.024, 0.026, materials["stainless"], QUARTER_TURN_X, 0.001)
            detail_count += 2

    for y, side in ((0.293, "right"), (-0.293, "left")):
        _box(collection, f"Splitter side trim {side}", (0.49, y, 0.465), (0.31, 0.012, 0.032), materials["stainless"], 0.002)
        _box(collection, f"Discharge support {side}", (0.58, y, 0.205), (0.030, 0.030, 0.40), materials["graphite"], 0.003)
        _cylinder(collection, f"Discharge foot {side}", (0.58, y, 0.022), 0.037, 0.044, materials["graphite"], None, 0.002)
        _box(collection, f"Discharge lower tie {side}", (0.23, y, 0.15), (0.70, 0.030, 0.035), materials["graphite"], 0.003)
        _box(collection, f"Reject tray bracket {side}", (0.40, y * 0.86, 0.165), (0.23, 0.070, 0.022), materials["stainless"], 0.002)
        _box(collection, f"Accept tray bracket {side}", (0.65, y * 0.86, 0.282), (0.25, 0.070, 0.025), materials["stainless"], 0.002)
        _box(collection, f"Feed support {side}", (-1.15, y, 0.615), (0.025, 0.025, 0.19), materials["graphite"], 0.002)
        _box(collection, f"Feed lower bracket {side}", (-1.10, y, 0.53), (0.18, 0.050, 0.025), materials["graphite"], 0.002)
        _box(collection, f"Feed upper bracket {side}", (-1.13, y, 0.71), (0.18, 0.055, 0.025), materials["stainless"], 0.002)
        _box(collection, f"Manifold support {side}", (0.10, y * 1.10, 0.625), (0.025, 0.025, 0.145), materials["graphite"], 0.002)
        _box(collection, f"Manifold mounting arm {side}", (0.025, y * 1.10, 0.56), (0.18, 0.032, 0.026), materials["graphite"], 0.002)
        _box(collection, f"Manifold clamp {side}", (0.10, y * 1.06, 0.691), (0.038, 0.045, 0.032), materials["stainless"], 0.002)
        detail_count += 12
    _box(collection, "Accept tray underside", (0.64, 0.0, 0.292), (0.23, 0.52, 0.010), materials["graphite"], 0.002)
    _box(collection, "Reject tray underside", (0.40, 0.0, 0.172), (0.23, 0.52, 0.010), materials["graphite"], 0.002)
    detail_count += 2
    return detail_count


def build_machine(payload):
    """Create the complete coffee-sorter scene from an exported replay payload."""
    if "machine" not in payload or "layout" not in payload:
        raise ValueError("Replay payload must contain machine and layout sections")
    collection = _machine_collection()
    materials = _materials()
    recorded_count = _add_recorded_surfaces(payload, collection, materials)
    # Extend the studio floor without changing or overlapping the recorded slab.
    floor = next((g for g in payload["machine"] if g.get("name") == "floor"), None)
    if floor is not None:
        x, y, z = floor["pos"]
        hx, hy, hz = floor["size"]
        extent = max(200.0, hx + 1.0, hy + 1.0)
        for sign, side in ((-1, "near"), (1, "far")):
            _box(collection, f"Studio floor {side}", (x, y + sign * (extent + hy) / 2, z),
                 (2 * extent, extent - hy, 2 * hz), materials["floor"])
        for sign, side in ((-1, "left"), (1, "right")):
            _box(collection, f"Studio floor {side}", (x + sign * (extent + hx) / 2, y, z),
                 (extent - hx, 2 * hy, 2 * hz), materials["floor"])
    nozzle_count = _add_nozzle_manifold(collection, payload["layout"], materials)
    fastener_count = _add_fasteners(collection, materials)
    presentation_count = _add_presentation_details(collection, materials)
    return {
        "counts": {
            "recorded_surfaces": recorded_count,
            "nozzles": nozzle_count,
            "fasteners": fastener_count,
            "presentation_objects": presentation_count,
            "total_objects": len(collection.objects),
        },
        "notes": (
            "Recorded payload primitives retain their original dimensions, positions, rotations, and primitive types. "
            "Presentation hardware is limited to the exterior, overhead gantry, and discharge edges. "
            "No cameras, lights, world settings, beans, render settings, or output files are created."
        ),
    }
