"""Render a validated object recipe with Blender.

Usage:
  blender --background --threads 1 --python render_recipe.py -- --recipe RECIPE --out OUTPUT_DIR
"""

import argparse
import hashlib
import json
import math
import sys
import time
from pathlib import Path

import bpy
from mathutils import Vector

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
from probe import validate_recipe

SEED = 20260919


def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--recipe", required=True)
    parser.add_argument("--out", required=True)
    return parser.parse_args(argv)


def srgb_to_linear(value):
    return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4


def material_for(part):
    material = bpy.data.materials.new(f"Material_{part['name']}")
    material.use_nodes = True
    shader = material.node_tree.nodes.get("Principled BSDF")
    color = [srgb_to_linear(component) for component in part["color"]]
    shader.inputs["Base Color"].default_value = (*color, 1.0)
    shader.inputs["Metallic"].default_value = float(part["metallic"])
    shader.inputs["Roughness"].default_value = float(part["roughness"])
    return material


def apply_scale(obj):
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    obj.select_set(False)


def smooth(obj):
    for polygon in obj.data.polygons:
        polygon.use_smooth = True


def smooth_cylinder_sides(obj):
    for polygon in obj.data.polygons:
        polygon.use_smooth = abs(polygon.normal.z) < 0.5


def apply_bevel(obj, size):
    width = min(0.4, min(size) * 0.08)
    if width <= 0:
        return
    modifier = obj.modifiers.new("Small edge bevel", "BEVEL")
    modifier.width = width
    modifier.segments = 2
    modifier.limit_method = "ANGLE"
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.modifier_apply(modifier=modifier.name)
    obj.select_set(False)


def polygon_mesh(part):
    size_x, size_y, size_z = part["size_mm"]
    points = [(point[0] * size_x, point[1] * size_y) for point in part["outline"]]
    signed_area = sum(
        points[index][0] * points[(index + 1) % len(points)][1]
        - points[(index + 1) % len(points)][0] * points[index][1]
        for index in range(len(points))
    )
    if signed_area < 0:
        points.reverse()
    half_z = size_z / 2
    vertices = [(x, y, -half_z) for x, y in points] + [(x, y, half_z) for x, y in points]
    count = len(points)
    faces = [tuple(range(count - 1, -1, -1)), tuple(range(count, count * 2))]
    faces.extend((index, (index + 1) % count, (index + 1) % count + count, index + count) for index in range(count))
    mesh = bpy.data.meshes.new(part["name"])
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(part["name"], mesh)
    bpy.context.collection.objects.link(obj)
    return obj


def center_mesh(obj):
    corners = [Vector(corner) for corner in obj.bound_box]
    center = (Vector((min(corner.x for corner in corners), min(corner.y for corner in corners), min(corner.z for corner in corners)))
              + Vector((max(corner.x for corner in corners), max(corner.y for corner in corners), max(corner.z for corner in corners)))) / 2
    for vertex in obj.data.vertices:
        vertex.co -= center


def text_mesh(part):
    bpy.ops.object.text_add()
    obj = bpy.context.active_object
    obj.name = part["name"]
    obj.data.body = part["text"]
    obj.data.align_x = "CENTER"
    obj.data.align_y = "CENTER"
    obj.data.extrude = part["size_mm"][2]
    bpy.context.view_layer.update()
    bpy.ops.object.convert(target="MESH")
    obj = bpy.context.active_object
    dimensions = obj.dimensions.copy()
    obj.scale = tuple(target / current for target, current in zip(part["size_mm"], dimensions))
    apply_scale(obj)
    center_mesh(obj)
    return obj


def create_part(part):
    size_x, size_y, size_z = part["size_mm"]
    kind = part["kind"]
    if kind == "ring":
        tube = part["tube_mm"]
        major = max(tube, (max(size_x, size_y) / 2) - tube)
        bpy.ops.mesh.primitive_torus_add(major_radius=major, minor_radius=tube, major_segments=64, minor_segments=16)
        obj = bpy.context.active_object
        outer_diameter = 2 * (major + tube)
        obj.scale = (size_x / outer_diameter, size_y / outer_diameter, 1)
        apply_scale(obj)
        smooth(obj)
    elif kind == "ellipsoid":
        bpy.ops.mesh.primitive_uv_sphere_add(segments=48, ring_count=24)
        obj = bpy.context.active_object
        obj.scale = (size_x / 2, size_y / 2, size_z / 2)
        apply_scale(obj)
        smooth(obj)
    elif kind == "box":
        bpy.ops.mesh.primitive_cube_add()
        obj = bpy.context.active_object
        obj.scale = (size_x / 2, size_y / 2, size_z / 2)
        apply_scale(obj)
        apply_bevel(obj, part["size_mm"])
    elif kind == "cylinder":
        bpy.ops.mesh.primitive_cylinder_add(vertices=64, radius=1, depth=2)
        obj = bpy.context.active_object
        obj.scale = (size_x / 2, size_y / 2, size_z / 2)
        apply_scale(obj)
        smooth_cylinder_sides(obj)
    elif kind == "polygon":
        obj = polygon_mesh(part)
        apply_bevel(obj, part["size_mm"])
    else:
        obj = text_mesh(part)
        apply_bevel(obj, part["size_mm"])

    obj.name = part["name"]
    obj.data.materials.append(material_for(part))
    obj.location = part["position_mm"]
    obj.rotation_euler = [math.radians(angle) for angle in part["rotation_deg"]]
    return obj


def world_bounds(objects):
    points = [obj.matrix_world @ Vector(corner) for obj in objects for corner in obj.bound_box]
    minimum = Vector(tuple(min(point[index] for point in points) for index in range(3)))
    maximum = Vector(tuple(max(point[index] for point in points) for index in range(3)))
    return minimum, maximum


def aim(obj, target):
    obj.rotation_euler = (Vector(target) - obj.location).to_track_quat("-Z", "Y").to_euler()


def add_area_light(name, location, target, energy, size):
    bpy.ops.object.light_add(type="AREA", location=location)
    light = bpy.context.active_object
    light.name = name
    light.data.energy = energy
    light.data.shape = "DISK"
    light.data.size = size
    aim(light, target)
    return light


def setup_studio(bounds):
    minimum, maximum = bounds
    dimensions = maximum - minimum
    span = max(dimensions.x, dimensions.y, dimensions.z, 1.0)
    center = (minimum + maximum) / 2

    floor_material = bpy.data.materials.new("Cream studio floor")
    floor_material.diffuse_color = (0.82, 0.76, 0.64, 1.0)
    bpy.ops.mesh.primitive_plane_add(size=span * 12, location=(center.x, center.y, -0.001))
    floor = bpy.context.active_object
    floor.name = "Studio floor"
    floor.data.materials.append(floor_material)

    bpy.ops.object.camera_add(location=(center.x + span * 1.35, center.y - span * 1.35, maximum.z + span * 1.1))
    camera = bpy.context.active_object
    camera.name = "Perspective camera"
    camera.data.type = "ORTHO"
    camera.data.ortho_scale = span * 1.7
    aim(camera, center)

    bpy.ops.object.camera_add(location=(center.x, center.y, maximum.z + span * 2.2))
    top_camera = bpy.context.active_object
    top_camera.name = "Top camera"
    top_camera.data.type = "ORTHO"
    top_camera.data.ortho_scale = max(dimensions.x, dimensions.y, 1.0) * 1.35
    aim(top_camera, center)

    add_area_light("Key", (center.x + span * 1.2, center.y - span * 1.0, maximum.z + span * 1.6), center, 40 * span ** 2, span * 1.4)
    add_area_light("Fill", (center.x - span * 1.1, center.y - span * 0.7, maximum.z + span * 0.8), center, 15 * span ** 2, span * 1.8)
    add_area_light("Rim", (center.x, center.y + span * 1.25, maximum.z + span * 1.5), center, 30 * span ** 2, span)
    return camera, top_camera


def configure_scene():
    scene = bpy.context.scene
    scene.unit_settings.system = "METRIC"
    scene.unit_settings.scale_length = 0.001
    scene.render.engine = "CYCLES"
    scene.render.threads_mode = "FIXED"
    scene.render.threads = 1
    scene.cycles.device = "CPU"
    scene.cycles.samples = 24
    scene.cycles.use_denoising = True
    scene.cycles.seed = SEED
    scene.render.resolution_x = 768
    scene.render.resolution_y = 768
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.film_transparent = False
    scene.world.color = (0.055, 0.045, 0.035)
    scene.world.use_nodes = True
    scene.world.node_tree.nodes["Background"].inputs["Color"].default_value = (0.055, 0.045, 0.035, 1)
    scene.world.node_tree.nodes["Background"].inputs["Strength"].default_value = 0.25
    return scene


def render(scene, camera, output_path):
    scene.camera = camera
    scene.render.filepath = str(output_path)
    start = time.perf_counter()
    bpy.ops.render.render(write_still=True)
    return time.perf_counter() - start


def export_object_files(objects, output_dir):
    view_layer = bpy.context.view_layer
    for obj in view_layer.objects:
        obj.select_set(False)
    for obj in objects:
        obj.select_set(True)
    view_layer.objects.active = objects[0]
    # Blender's glTF exporter does not apply scene.unit_settings.scale_length.
    transforms = [(obj, obj.location.copy(), obj.scale.copy()) for obj in objects]
    for obj, _, _ in transforms:
        obj.location *= 0.001
        obj.scale *= 0.001
    try:
        bpy.ops.export_scene.gltf(filepath=str(output_dir / "object.glb"), export_format="GLB", use_selection=True)
    finally:
        for obj, location, scale in transforms:
            obj.location = location
            obj.scale = scale

    for obj in list(bpy.context.scene.objects):
        if obj not in objects:
            bpy.data.objects.remove(obj, do_unlink=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(output_dir / "object.blend"))


def bounds_record(obj):
    minimum, maximum = world_bounds([obj])
    dimensions = maximum - minimum
    return {
        "name": obj.name,
        "minimum_mm": list(minimum),
        "maximum_mm": list(maximum),
        "dimensions_mm": list(dimensions),
    }


def main():
    args = parse_args()
    output_dir = Path(args.out)
    output_dir.mkdir(parents=True, exist_ok=True)
    recipe_path = Path(args.recipe)
    recipe_bytes = recipe_path.read_bytes()
    recipe = json.loads(recipe_bytes)
    validate_recipe(recipe)

    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    scene = configure_scene()
    objects = [create_part(part) for part in recipe["parts"]]
    bpy.context.view_layer.update()

    minimum, _ = world_bounds(objects)
    for obj in objects:
        obj.location.z -= minimum.z
    bpy.context.view_layer.update()
    bounds = world_bounds(objects)
    camera, top_camera = setup_studio(bounds)
    perspective_seconds = render(scene, camera, output_dir / "perspective.png")
    top_seconds = render(scene, top_camera, output_dir / "top.png")

    minimum, maximum = world_bounds(objects)
    dimensions = maximum - minimum
    mesh_counts = {
        "objects": len(objects),
        "vertices": sum(len(obj.data.vertices) for obj in objects),
        "polygons": sum(len(obj.data.polygons) for obj in objects),
    }
    part_bounds = [bounds_record(obj) for obj in objects]
    export_object_files(objects, output_dir)
    metadata = {
        "recipe_name": recipe["name"],
        "engine": scene.render.engine,
        "samples": scene.cycles.samples,
        "blender_version": bpy.app.version_string,
        "blender_build_hash": bpy.app.build_hash.decode("ascii"),
        "seed": SEED,
        "recipe_sha256": hashlib.sha256(recipe_bytes).hexdigest(),
        "renderer_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "scene_threads": {"mode": scene.render.threads_mode, "threads": scene.render.threads},
        "mesh_counts": mesh_counts,
        "bounding_dimensions_mm": [dimensions.x, dimensions.y, dimensions.z],
        "part_bounds_mm": part_bounds,
        "glb_units": "meters",
        "glb_scale_from_recipe_mm": 0.001,
        "render_timings_seconds": {"perspective": perspective_seconds, "top": top_seconds, "total": perspective_seconds + top_seconds},
    }
    with open(output_dir / "render.json", "w", encoding="utf-8") as handle:
        json.dump(metadata, handle, indent=2)
        handle.write("\n")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"render_recipe failed: {error}", file=sys.stderr)
        raise
