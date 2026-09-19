"""Build reusable, metre-scale coffee bean meshes in Blender.

Run with Blender, for example::

    blender --background --python build_assets.py -- --output-dir visual_assets/generated

``make_assets`` is also the render-script interface. It returns four prototype
objects at the simulator origin; callers should duplicate them before hiding the
``CoffeeBeanPrototypes`` collection.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys

import bpy


COLLECTION_NAME = "CoffeeBeanPrototypes"
REGULAR_SEMI_AXES = (0.0049, 0.00355, 0.00255)
BROKEN_SEMI_AXES = (0.0050, 0.0036, 0.0026)
BROKEN_SOURCE_DIMENSIONS = (0.0100, 0.007183224, 0.002567526)


def _material(name, dark, light, roughness=0.82, bump=0.000055):
    material = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    material.use_nodes = True
    nodes = material.node_tree.nodes
    nodes.clear()
    output = nodes.new("ShaderNodeOutputMaterial")
    shader = nodes.new("ShaderNodeBsdfPrincipled")
    texture = nodes.new("ShaderNodeTexNoise")
    micro = nodes.new("ShaderNodeTexNoise")
    striations = nodes.new("ShaderNodeTexWave")
    micro_mix = nodes.new("ShaderNodeMixRGB")
    tint = nodes.new("ShaderNodeMixRGB")
    object_info = nodes.new("ShaderNodeObjectInfo")
    random_value = nodes.new("ShaderNodeMapRange")
    ramp = nodes.new("ShaderNodeValToRGB")
    bump_node = nodes.new("ShaderNodeBump")
    coordinates = nodes.new("ShaderNodeTexCoord")
    texture.inputs["Scale"].default_value = 7.0
    texture.inputs["Detail"].default_value = 3.2
    texture.inputs["Roughness"].default_value = 0.68
    micro.inputs["Scale"].default_value = 145.0
    micro.inputs["Detail"].default_value = 2.0
    micro.inputs["Roughness"].default_value = 0.55
    striations.wave_type = "BANDS"
    striations.bands_direction = "Y"
    striations.inputs["Scale"].default_value = 42.0
    striations.inputs["Distortion"].default_value = 3.0
    striations.inputs["Detail"].default_value = 2.0
    micro_mix.blend_type = "MULTIPLY"
    micro_mix.inputs["Fac"].default_value = 0.22
    tint.blend_type = "MULTIPLY"
    tint.inputs["Fac"].default_value = 1.0
    random_value.inputs["From Min"].default_value = 0.0
    random_value.inputs["From Max"].default_value = 1.0
    random_value.inputs["To Min"].default_value = 0.97
    random_value.inputs["To Max"].default_value = 1.03
    ramp.color_ramp.elements[0].color = (*dark, 1.0)
    ramp.color_ramp.elements[1].color = (*light, 1.0)
    shader.inputs["Roughness"].default_value = roughness
    shader.inputs["Specular IOR Level"].default_value = 0.24
    bump_node.inputs["Strength"].default_value = 0.38
    bump_node.inputs["Distance"].default_value = bump
    links = material.node_tree.links
    links.new(coordinates.outputs["Generated"], texture.inputs["Vector"])
    links.new(coordinates.outputs["Generated"], micro.inputs["Vector"])
    links.new(coordinates.outputs["Generated"], striations.inputs["Vector"])
    links.new(texture.outputs["Fac"], ramp.inputs["Fac"])
    links.new(micro.outputs["Fac"], micro_mix.inputs[1])
    links.new(striations.outputs["Color"], micro_mix.inputs[2])
    links.new(micro_mix.outputs["Color"], bump_node.inputs["Height"])
    links.new(object_info.outputs["Random"], random_value.inputs["Value"])
    links.new(ramp.outputs["Color"], tint.inputs[1])
    links.new(random_value.outputs["Result"], tint.inputs[2])
    links.new(tint.outputs["Color"], shader.inputs["Base Color"])
    links.new(bump_node.outputs["Normal"], shader.inputs["Normal"])
    links.new(shader.outputs["BSDF"], output.inputs["Surface"])
    return material


def _flat_material(name, color, roughness=0.9):
    material = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    material.diffuse_color = (*color, 1.0)
    material.use_nodes = True
    shader = material.node_tree.nodes.get("Principled BSDF")
    shader.inputs["Base Color"].default_value = (*color, 1.0)
    shader.inputs["Roughness"].default_value = roughness
    shader.inputs["Specular IOR Level"].default_value = 0.18
    return material


def _surface_vertices(axes, rings=64, segments=96):
    ax, ay, az = axes
    # The pole lies in the groove; dent it with its neighbours to avoid a spike.
    vertices = [(0.0, 0.0, az - 0.00082)]
    for ring in range(1, rings):
        theta = math.pi * ring / rings
        sin_theta = math.sin(theta)
        for segment in range(segments):
            phi = math.tau * segment / segments
            x = ax * sin_theta * math.cos(phi)
            y = ay * sin_theta * math.sin(phi)
            z = az * math.cos(theta)
            # A coffee seed is subtly lopsided rather than a perfect ellipsoid.
            y *= 1.0 + 0.075 * math.sin(math.pi * x / ax) + 0.018 * math.sin(3.0 * phi)
            z *= 1.0 - 0.018 * x / ax
            # Fine real geometry breaks the manufactured-smooth silhouette. Peak displacement is 20 um.
            wrinkle = 0.000020 * (0.62 * math.sin(23.0 * phi + 5.0 * theta)
                                  + 0.38 * math.sin(41.0 * phi - 3.0 * theta))
            nx, ny, nz = x / (ax * ax), y / (ay * ay), z / (az * az)
            normal_length = math.sqrt(nx * nx + ny * ny + nz * nz)
            x += wrinkle * nx / normal_length
            y += wrinkle * ny / normal_length
            z += wrinkle * nz / normal_length
            if z > -0.00015:
                centre = 0.00010 * math.sin(1.4 * math.pi * x / ax)
                across = (y - centre) / (0.14 * ay)
                along = max(0.0, 1.0 - (abs(x) / (0.90 * ax)) ** 6)
                height = max(0.0, min(1.0, (z / az + 0.08) / 1.08))
                z -= 0.00082 * math.exp(-(across * across)) * along * height
            vertices.append((x, y, z))
    vertices.append((0.0, 0.0, -az))
    return vertices


def _surface_faces(rings=64, segments=96):
    faces = []
    top = 0
    bottom = 1 + (rings - 1) * segments
    for segment in range(segments):
        following = (segment + 1) % segments
        faces.append((top, 1 + segment, 1 + following))
    for ring in range(rings - 2):
        start = 1 + ring * segments
        following_start = start + segments
        for segment in range(segments):
            following = (segment + 1) % segments
            faces.append((start + segment, following_start + segment,
                          following_start + following, start + following))
    start = 1 + (rings - 2) * segments
    for segment in range(segments):
        following = (segment + 1) % segments
        faces.append((start + segment, bottom, start + following))
    return faces


def _fit_bounds(mesh, dimensions, preserve_z_min=False):
    mins = [min(vertex.co[axis] for vertex in mesh.vertices) for axis in range(3)]
    maxs = [max(vertex.co[axis] for vertex in mesh.vertices) for axis in range(3)]
    scales = [dimensions[axis] / (maxs[axis] - mins[axis]) for axis in range(3)]
    centres = [(mins[axis] + maxs[axis]) * 0.5 for axis in range(3)]
    for vertex in mesh.vertices:
        vertex.co.x = (vertex.co.x - centres[0]) * scales[0]
        vertex.co.y = (vertex.co.y - centres[1]) * scales[1]
        if preserve_z_min:
            vertex.co.z = (vertex.co.z - mins[2]) * scales[2]
        else:
            vertex.co.z = (vertex.co.z - centres[2]) * scales[2]


def _new_object(name, vertices, faces, collection):
    mesh = bpy.data.meshes.new(f"{name}_mesh")
    mesh.from_pydata(vertices, (), faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    collection.objects.link(obj)
    for polygon in mesh.polygons:
        polygon.use_smooth = True
    return obj


def _assign_crease(obj, body_material, crease_material):
    # Shade the sculpted furrow continuously instead of colouring whole polygons.
    name = body_material.name + "_creased"
    material = bpy.data.materials.get(name)
    if material is None:
        material = body_material.copy()
        material.name = name
        nodes, links = material.node_tree.nodes, material.node_tree.links
        shader = next(node for node in nodes if node.type == "BSDF_PRINCIPLED")
        body_color = shader.inputs["Base Color"].links[0].from_socket
        coords = nodes.new("ShaderNodeTexCoord")
        xyz = nodes.new("ShaderNodeSeparateXYZ")
        links.new(coords.outputs["Object"], xyz.inputs["Vector"])

        def math_node(operation, source, value):
            node = nodes.new("ShaderNodeMath")
            node.operation = operation
            links.new(source, node.inputs[0])
            node.inputs[1].default_value = value
            return node.outputs[0]

        wave = math_node("MULTIPLY", xyz.outputs["X"], 1.4 * math.pi / REGULAR_SEMI_AXES[0])
        wave = math_node("SINE", wave, 0.0)
        wave = math_node("MULTIPLY", wave, 0.00010)
        offset = nodes.new("ShaderNodeMath")
        offset.operation = "SUBTRACT"
        links.new(xyz.outputs["Y"], offset.inputs[0])
        links.new(wave, offset.inputs[1])
        distance = math_node("ABSOLUTE", offset.outputs[0], 0.0)
        seam = nodes.new("ShaderNodeMapRange")
        seam.interpolation_type = "SMOOTHERSTEP"
        seam.inputs["From Min"].default_value = 0.00003
        seam.inputs["From Max"].default_value = 0.00017
        seam.inputs["To Min"].default_value = 1.0
        seam.inputs["To Max"].default_value = 0.0
        links.new(distance, seam.inputs["Value"])
        top = math_node("GREATER_THAN", xyz.outputs["Z"], 0.0004)
        mask = nodes.new("ShaderNodeMath")
        mask.operation = "MULTIPLY"
        links.new(seam.outputs["Result"], mask.inputs[0])
        links.new(top, mask.inputs[1])
        mix = nodes.new("ShaderNodeMixRGB")
        links.new(mask.outputs[0], mix.inputs[0])
        links.new(body_color, mix.inputs[1])
        mix.inputs[2].default_value = crease_material.diffuse_color
        links.new(mix.outputs[0], shader.inputs["Base Color"])
    obj.data.materials.append(material)


def _regular_bean(name, collection, body_material, crease_material):
    obj = _new_object(name, _surface_vertices(REGULAR_SEMI_AXES), _surface_faces(), collection)
    _fit_bounds(obj.data, tuple(2.0 * axis for axis in REGULAR_SEMI_AXES))
    _assign_crease(obj, body_material, crease_material)
    obj["simulator_semi_axes_m"] = REGULAR_SEMI_AXES
    obj["simulator_origin"] = "body centre"
    return obj


def _apply_bore(bean, location, radius, depth, tilt):
    segments = 32
    vertices = []
    for ring, z in enumerate((-depth / 2, -depth * 0.12, depth / 2)):
        for segment in range(segments):
            angle = math.tau * segment / segments
            irregularity = 1.0 + 0.075 * math.sin(5.0 * angle + 0.7 * ring) \
                            + 0.035 * math.sin(9.0 * angle - 0.4 * ring)
            vertices.append((radius * irregularity * math.cos(angle),
                             radius * irregularity * math.sin(angle), z))
    faces = []
    for ring in range(2):
        start = ring * segments
        next_start = start + segments
        for segment in range(segments):
            following = (segment + 1) % segments
            faces.append((start + segment, next_start + segment,
                          next_start + following, start + following))
    faces.append(tuple(reversed(range(segments))))
    faces.append(tuple(2 * segments + segment for segment in range(segments)))
    mesh = bpy.data.meshes.new("insect_bore_cutter_mesh")
    mesh.from_pydata(vertices, (), faces)
    mesh.update()
    cutter = bpy.data.objects.new("insect_bore_cutter", mesh)
    bpy.context.scene.collection.objects.link(cutter)
    cutter.location = location
    cutter.rotation_euler = tilt
    bpy.context.view_layer.objects.active = bean
    bean.select_set(True)
    cutter.select_set(False)
    modifier = bean.modifiers.new("physical insect bore", "BOOLEAN")
    modifier.operation = "DIFFERENCE"
    modifier.solver = "EXACT"
    modifier.object = cutter
    bpy.ops.object.modifier_apply(modifier=modifier.name)
    bpy.data.objects.remove(cutter, do_unlink=True)


def _broken_bean(collection, body_material, fracture_material):
    ax, ay, az = BROKEN_SEMI_AXES
    segments = 72
    levels = 28
    vertices = [(0.0, 0.0, az - 0.00065)]
    for level in range(1, levels + 1):
        theta = 0.5 * math.pi * level / levels
        for segment in range(segments):
            phi = math.tau * segment / segments
            edge_noise = 1.0 - (level / levels) ** 7 * (
                0.075 + 0.048 * math.sin(5.0 * phi) + 0.030 * math.sin(11.0 * phi + 0.4)
            )
            x = ax * math.sin(theta) * math.cos(phi) * edge_noise
            y = ay * math.sin(theta) * math.sin(phi) * edge_noise
            y *= 1.0 + 0.035 * math.sin(math.pi * x / ax)
            z = az * math.cos(theta) * (1.0 - 0.018 * x / ax)
            wrinkle = 0.000019 * (0.65 * math.sin(19.0 * phi + 4.0 * theta)
                                  + 0.35 * math.sin(37.0 * phi - 5.0 * theta))
            nx, ny, nz = x / (ax * ax), y / (ay * ay), z / (az * az)
            normal_length = math.sqrt(nx * nx + ny * ny + nz * nz)
            x += wrinkle * nx / normal_length
            y += wrinkle * ny / normal_length
            z += wrinkle * nz / normal_length
            if z > 0.00008:
                crease_y = 0.00008 * math.sin(1.3 * math.pi * x / ax)
                across = (y - crease_y) / (0.14 * ay)
                along = max(0.0, 1.0 - (abs(x) / (0.90 * ax)) ** 6)
                z -= 0.00065 * math.exp(-(across * across)) * along * min(1.0, z / az)
            vertices.append((x, y, max(0.0, z)))
    cap_rings = []
    for ring, radius in enumerate((0.82, 0.64, 0.46, 0.29, 0.12)):
        start = len(vertices)
        cap_rings.append(start)
        angular_offset = (ring % 2) * math.pi / segments
        for segment in range(segments):
            phi = math.tau * segment / segments + angular_offset
            radial = radius * (1.0 + 0.022 * math.sin(7.0 * phi + 0.8 * ring)
                               + 0.012 * math.sin(13.0 * phi - 0.5 * ring))
            z = 0.000055 + 0.000075 * (1.0 - radius) \
                + 0.000045 * math.sin(7.0 * phi + 1.1 * ring) \
                + 0.000026 * math.sin(13.0 * phi - 0.7 * ring)
            vertices.append((ax * radial * math.cos(phi), ay * radial * math.sin(phi), z))
    centre = len(vertices)
    vertices.append((0.000035, -0.000025, 0.000145))

    faces = []
    for segment in range(segments):
        faces.append((0, 1 + segment, 1 + (segment + 1) % segments))
    for level in range(levels - 1):
        start = 1 + level * segments
        following_start = start + segments
        for segment in range(segments):
            following = (segment + 1) % segments
            faces.append((start + segment, following_start + segment,
                          following_start + following, start + following))
    boundary_start = 1 + (levels - 1) * segments
    cap_face_start = len(faces)
    rings = [boundary_start, *cap_rings]
    for ring, (outer, inner) in enumerate(zip(rings, rings[1:])):
        for segment in range(segments):
            following = (segment + 1) % segments
            if (segment + ring) % 2:
                faces.append((outer + segment, inner + segment, outer + following))
                faces.append((outer + following, inner + segment, inner + following))
            else:
                faces.append((outer + segment, inner + segment, inner + following))
                faces.append((outer + segment, inner + following, outer + following))
    inner = cap_rings[-1]
    for segment in range(segments):
        faces.append((inner + segment, centre, inner + (segment + 1) % segments))

    obj = _new_object("bean_broken", vertices, faces, collection)
    _fit_bounds(obj.data, BROKEN_SOURCE_DIMENSIONS, True)
    obj.data.materials.append(body_material)
    obj.data.materials.append(fracture_material)
    for polygon in obj.data.polygons[cap_face_start:]:
        polygon.material_index = 1
        polygon.use_smooth = False
    obj["simulator_semi_axes_m"] = BROKEN_SEMI_AXES
    obj["simulator_scale_variants"] = (0.9, 1.0, 1.1)
    obj["source_obj_dimensions_m"] = BROKEN_SOURCE_DIMENSIONS
    obj["simulator_origin"] = "fracture plane at z=0; body extends into +z"
    return obj


def _replace_collection():
    old = bpy.data.collections.get(COLLECTION_NAME)
    if old:
        for obj in list(old.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        bpy.data.collections.remove(old)
    collection = bpy.data.collections.new(COLLECTION_NAME)
    bpy.context.scene.collection.children.link(collection)
    return collection


def _bounds(obj):
    coordinates = [obj.matrix_world @ vertex.co for vertex in obj.data.vertices]
    mins = [min(point[axis] for point in coordinates) for axis in range(3)]
    maxs = [max(point[axis] for point in coordinates) for axis in range(3)]
    return {
        "min_m": [round(value, 9) for value in mins],
        "max_m": [round(value, 9) for value in maxs],
        "dimensions_m": [round(maxs[axis] - mins[axis], 9) for axis in range(3)],
    }


def _export_obj(obj, path):
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.wm.obj_export(
        filepath=str(path),
        export_selected_objects=True,
        export_materials=False,
        apply_modifiers=True,
        forward_axis="Y",
        up_axis="Z",
    )


def _write_outputs(prototypes, output_dir, export_obj):
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "units": "metres",
        "collection": COLLECTION_NAME,
        "regular_simulator_semi_axes_m": REGULAR_SEMI_AXES,
        "broken_simulator_nominal_scale_m": BROKEN_SEMI_AXES,
        "broken_source_obj_dimensions_m": BROKEN_SOURCE_DIMENSIONS,
        "broken_simulator_scale_variants": (0.9, 1.0, 1.1),
        "objects": {kind: {"name": obj.name, "origin": obj["simulator_origin"], **_bounds(obj)}
                    for kind, obj in prototypes.items()},
    }
    (output_dir / "dimensions.json").write_text(json.dumps(manifest, indent=2) + "\n")
    if export_obj:
        for kind, obj in prototypes.items():
            _export_obj(obj, output_dir / f"bean_{kind}.obj")


def make_assets(output_dir=None, export_obj=True):
    """Create four reusable prototype Objects and optionally export a manifest/OBJs."""
    collection = _replace_collection()
    green = _material("coffee_green", (0.22, 0.245, 0.145), (0.42, 0.45, 0.30))
    black = _material("coffee_black", (0.004, 0.003, 0.002), (0.035, 0.023, 0.015), bump=0.000075)
    crease = _flat_material("coffee_silver_skin", (0.12, 0.095, 0.055), 0.96)
    bore = _flat_material("coffee_insect_bore", (0.032, 0.018, 0.008), 0.93)
    fracture = _material("coffee_fracture", (0.28, 0.20, 0.08), (0.57, 0.43, 0.19), 0.92, 0.000035)

    good = _regular_bean("bean_good", collection, green, crease)
    black_bean = _regular_bean("bean_black", collection, black, crease)
    insect = _regular_bean("bean_insect", collection, green, crease)
    insect.data.materials.append(bore)
    bores = [
        ((-0.00145, 0.00072, 0.00255), 0.00043, 0.0022,
         (math.radians(7), math.radians(-10), 0.0)),
        ((0.00115, -0.00052, 0.00250), 0.00036, 0.0019,
         (math.radians(-9), math.radians(8), 0.0)),
    ]
    for location, radius, depth, tilt in bores:
        _apply_bore(insect, location, radius, depth, tilt)
    for polygon in insect.data.polygons:
        if polygon.center.z < 0.00065:
            continue
        if any((polygon.center.x - location[0]) ** 2 + (polygon.center.y - location[1]) ** 2
               < (1.35 * radius) ** 2 for location, radius, _, _ in bores):
            polygon.material_index = 1
    insect["physical_bores"] = 2
    broken = _broken_bean(collection, green, fracture)
    prototypes = {"good": good, "black": black_bean, "insect": insect, "broken": broken}
    for obj in prototypes.values():
        obj.location = (0.0, 0.0, 0.0)
        obj.rotation_euler = (0.0, 0.0, 0.0)
        obj.scale = (1.0, 1.0, 1.0)
    if output_dir is not None:
        _write_outputs(prototypes, Path(output_dir), export_obj)
    return prototypes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).with_name("generated"))
    parser.add_argument("--no-obj", action="store_true")
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else [])
    bpy.ops.wm.read_factory_settings(use_empty=True)
    prototypes = make_assets(args.output_dir, not args.no_obj)
    bpy.ops.wm.save_as_mainfile(filepath=str(args.output_dir / "coffee_bean_library.blend"), compress=True)
    print(json.dumps({"objects": list(prototypes), "output_dir": str(args.output_dir),
                      "blend": str(args.output_dir / "coffee_bean_library.blend")}))


if __name__ == "__main__":
    main()
