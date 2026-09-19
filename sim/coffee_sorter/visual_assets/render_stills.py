#!/usr/bin/env python3
"""Render deterministic studio stills of the realistic coffee-bean assets.

Run with Blender, for example:
  blender --background --python render_stills.py -- --shot good --preview
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import random
import sys
import time

import bpy
from mathutils import Quaternion, Vector


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from build_assets import make_assets  # noqa: E402


SEED = 230948
SHOT_NAMES = ("good", "defects", "conveyor")


class Layout:
    """Render subset of the authoritative dimensions in ../scene.py, in metres."""

    belt_len = 1.1
    belt_w = 0.50
    belt_z = 0.60
    cam_x = -0.12


def material(name, color, roughness=0.5, metallic=0.0):
    mat = bpy.data.materials.new(name)
    mat.diffuse_color = (*color, 1.0)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    bsdf.inputs["Base Color"].default_value = (*color, 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat


def cube(name, location, dimensions, mat, bevel=0.0):
    bpy.ops.mesh.primitive_cube_add(location=location)
    obj = bpy.context.object
    obj.name = name
    obj.dimensions = dimensions
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    obj.data.materials.append(mat)
    if bevel:
        modifier = obj.modifiers.new("Soft edges", "BEVEL")
        modifier.width = bevel
        modifier.segments = 3
    return obj


def point_at(obj, target):
    obj.rotation_euler = (Vector(target) - obj.location).to_track_quat("-Z", "Y").to_euler()


def add_area(name, location, target, energy, size, color):
    data = bpy.data.lights.new(name, "AREA")
    data.energy = energy
    data.shape = "DISK"
    data.size = size
    data.color = color
    obj = bpy.data.objects.new(name, data)
    bpy.context.scene.collection.objects.link(obj)
    obj.location = location
    point_at(obj, target)
    return obj


def add_camera(location, target, lens, fstop, focus_distance):
    data = bpy.data.cameras.new("Camera")
    data.lens = lens
    data.sensor_width = 36
    data.clip_start = 0.0001
    data.clip_end = 20.0
    data.dof.use_dof = False
    data.dof.aperture_fstop = fstop
    data.dof.focus_distance = focus_distance
    obj = bpy.data.objects.new("Camera", data)
    bpy.context.scene.collection.objects.link(obj)
    obj.location = location
    point_at(obj, target)
    bpy.context.scene.camera = obj


def rest_on(obj, surface_z):
    bpy.context.view_layer.update()
    bottom = min((obj.matrix_world @ vertex.co).z for vertex in obj.data.vertices)
    obj.location.z += surface_z - bottom + 0.00005


def face_axis_at(obj, local_axis, point):
    view_direction = (Vector(point) - obj.location).normalized()
    obj.rotation_mode = "QUATERNION"
    obj.rotation_quaternion = Vector(local_axis).rotation_difference(view_direction)


def add_bean(prototypes, collection, kind, name, location, rotation, surface_z):
    obj = prototypes[kind].copy()
    obj.name = name
    obj.data = prototypes[kind].data
    collection.objects.link(obj)
    obj.hide_render = False
    obj.hide_viewport = False
    obj.location = location
    obj.rotation_euler = tuple(math.radians(angle) for angle in rotation)
    # Exact simulator dimensions are carried by the prototypes. Never scale clones.
    obj.scale = (1.0, 1.0, 1.0)
    rest_on(obj, surface_z)
    return obj


def studio_surface():
    surface = material("Warm stone", (0.145, 0.132, 0.115), 0.72)
    cube("Studio surface", (0, 0, -0.005), (0.28, 0.24, 0.010), surface, 0.003)


def stage_good(prototypes, beans):
    studio_surface()
    poses = [
        ((-0.012, 0.007, 0.0031), (5, -7, -28)),
        ((0.000, -0.006, 0.0031), (-4, 8, 18)),
        ((0.013, 0.006, 0.0031), (3, -5, -8)),
    ]
    for index, (location, rotation) in enumerate(poses):
        add_bean(prototypes, beans, "good", f"good_{index:02d}", location, rotation, 0.0)
    target = (0.001, 0.001, 0.002)
    camera = (0.040, -0.055, 0.076)
    add_camera(camera, target, 75, 22, (Vector(camera) - Vector(target)).length)
    add_area("Key softbox", (-0.040, -0.030, 0.050), target, 0.035, 0.035, (1.0, 0.84, 0.67))
    add_area("Fill card", (0.070, -0.010, 0.045), target, 0.019, 0.07, (0.73, 0.83, 1.0))
    add_area("Rim", (0.015, 0.075, 0.065), target, 0.031, 0.06, (1.0, 0.72, 0.48))


def stage_defects(prototypes, beans):
    studio_surface()
    camera = (0.046, -0.065, 0.085)
    # The bore-bearing +Z faces stay aimed upward; the broken bean's -Z fracture
    # cap is rolled toward the camera from the front row.
    poses = [
        ("good", (-0.015, 0.007, 0.0031), (4, -5, -18)),
        ("black", (-0.005, -0.006, 0.0031), (-5, 6, 25)),
        ("insect", (0.008, 0.006, 0.0031), (7, -9, -8)),
        ("broken", (0.019, -0.006, 0.0042), (58, 10, 65)),
    ]
    for index, (kind, location, rotation) in enumerate(poses):
        obj = add_bean(prototypes, beans, kind, f"{kind}_{index:02d}", location, rotation, 0.0)
        if kind == "broken":
            face_axis_at(obj, (0, 0, -1), camera)
            obj.rotation_quaternion @= Quaternion((0, 1, 0), math.radians(38))
            rest_on(obj, 0.0)
    target = (0.002, 0.001, 0.002)
    add_camera(camera, target, 76, 26, (Vector(camera) - Vector(target)).length)
    add_area("Key softbox", (-0.040, -0.035, 0.055), target, 0.040, 0.04, (1.0, 0.82, 0.64))
    add_area("Bore fill", (0.040, -0.035, 0.070), target, 0.022, 0.06, (0.72, 0.84, 1.0))
    add_area("Fracture rim", (0.055, 0.060, 0.070), target, 0.034, 0.06, (1.0, 0.68, 0.43))


def conveyor_parts(layout):
    belt = material("Belt blue", (0.020, 0.085, 0.31), 0.64)
    frame = material("Frame black", (0.025, 0.030, 0.038), 0.38, 0.20)
    steel = material("Brushed steel", (0.31, 0.34, 0.38), 0.27, 0.82)
    white = material("Inspection diffuser", (0.92, 0.88, 0.73), 0.25)
    x_mid = -layout.belt_len / 2
    cube("belt_top", (x_mid, 0, layout.belt_z - 0.004),
         (layout.belt_len, layout.belt_w, 0.008), belt, 0.002)
    cube("belt_frame", (x_mid, 0, layout.belt_z - 0.050),
         (layout.belt_len, layout.belt_w + 0.060, 0.080), frame, 0.004)
    for side in (-1, 1):
        cube(f"belt_rail_{side:+d}",
             (x_mid, side * (layout.belt_w / 2 + 0.004), layout.belt_z + 0.012),
             (layout.belt_len, 0.008, 0.024), steel, 0.002)
    cube("inspection_camera_housing", (layout.cam_x, 0, layout.belt_z + 0.420),
         (0.100, layout.belt_w + 0.120, 0.060), frame, 0.006)
    for offset in (-0.050, 0.050):
        cube(f"inspection_line_{offset:+.3f}",
             (layout.cam_x + offset, 0, layout.belt_z + 0.250),
             (0.012, layout.belt_w + 0.040, 0.012), white, 0.002)


def stage_conveyor(prototypes, beans):
    layout = Layout()
    conveyor_parts(layout)
    camera = (layout.cam_x + 0.105, -0.36, layout.belt_z + 0.145)
    rng = random.Random(SEED)
    kinds = ["good", "good", "black", "good", "insect", "good", "broken", "good",
             "good", "insect", "good", "black", "good", "good"]
    for index, kind in enumerate(kinds):
        row, column = divmod(index, 5)
        location = (layout.cam_x - 0.050 + column * 0.024 + rng.uniform(-0.003, 0.003),
                    -0.206 + row * 0.034 + rng.uniform(-0.003, 0.003),
                    layout.belt_z + (0.0040 if kind == "broken" else 0.0031))
        rotation = (rng.uniform(-7, 7), rng.uniform(-7, 7), rng.uniform(-80, 80))
        if kind == "broken":
            rotation = (52, 8, 35)
        obj = add_bean(prototypes, beans, kind, f"belt_{kind}_{index:02d}",
                       location, rotation, layout.belt_z)
        if kind == "broken":
            face_axis_at(obj, (0, 0, -1), camera)
            obj.rotation_quaternion @= Quaternion((0, 1, 0), math.radians(38))
            rest_on(obj, layout.belt_z)
    target = (layout.cam_x, -0.17, layout.belt_z + 0.002)
    add_camera(camera, target, 65, 18, (Vector(camera) - Vector(target)).length)
    add_area("Inspection key", (layout.cam_x - 0.08, -0.12, layout.belt_z + 0.26),
             target, 0.57, 0.22, (0.95, 0.90, 0.78))
    add_area("Inspection fill", (layout.cam_x + 0.12, 0.06, layout.belt_z + 0.20),
             target, 0.24, 0.16, (0.68, 0.82, 1.0))
    add_area("Edge light", (layout.cam_x - 0.02, 0.20, layout.belt_z + 0.14),
             target, 0.33, 0.12, (1.0, 0.66, 0.38))


def configure_scene(preview, samples):
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = samples
    scene.cycles.seed = SEED
    scene.cycles.use_denoising = True
    scene.cycles.max_bounces = 5
    scene.cycles.diffuse_bounces = 3
    scene.cycles.glossy_bounces = 3
    scene.cycles.transmission_bounces = 2
    scene.cycles.volume_bounces = 0
    scene.cycles.use_adaptive_sampling = True
    scene.cycles.adaptive_threshold = 0.18 if preview else 0.035
    scene.render.resolution_x, scene.render.resolution_y = ((640, 480) if preview else (1400, 1000))
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGB"
    scene.render.film_transparent = False
    scene.render.threads_mode = "FIXED"
    scene.render.threads = min(16, os.cpu_count() or 1)
    scene.render.image_settings.color_depth = "8"
    if scene.world is None:
        scene.world = bpy.data.worlds.new("Studio World")
    scene.world.color = (0.008, 0.010, 0.015)
    scene.render.image_settings.compression = 35
    scene.render.fps = 24
    scene.view_settings.look = "AgX - Medium High Contrast"
    scene.view_settings.exposure = 0.8
    if preview and scene.camera is not None:
        scene.camera.data.dof.use_dof = False
    return scene


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shot", choices=SHOT_NAMES, required=True)
    parser.add_argument("--preview", action="store_true")
    parser.add_argument("--output-dir", type=Path, default=HERE / "renders")
    parser.add_argument("--samples", type=int)
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    args = parser.parse_args(argv)
    samples = args.samples if args.samples is not None else (16 if args.preview else 96)
    if samples < 1:
        parser.error("--samples must be positive")

    bpy.ops.wm.read_factory_settings(use_empty=True)
    prototypes = make_assets(export_obj=False)
    prototype_collection = bpy.data.collections["CoffeeBeanPrototypes"]
    beans = bpy.data.collections.new("StagedBeans")
    bpy.context.scene.collection.children.link(beans)

    {"good": stage_good, "defects": stage_defects, "conveyor": stage_conveyor}[args.shot](prototypes, beans)
    prototype_collection.hide_render = True
    prototype_collection.hide_viewport = True
    scene = configure_scene(args.preview, samples)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    suffix = "_preview" if args.preview else ""
    image_path = (args.output_dir / f"{args.shot}{suffix}.png").resolve()
    blend_path = (args.output_dir / f"{args.shot}{suffix}.blend").resolve()
    record_path = (args.output_dir / f"{args.shot}{suffix}.json").resolve()
    scene.render.filepath = str(image_path)
    bpy.ops.wm.save_as_mainfile(filepath=str(blend_path), compress=True)

    started_at = datetime.now(timezone.utc)
    started = time.perf_counter()
    bpy.ops.render.render(write_still=True)
    elapsed = time.perf_counter() - started
    record = {
        "shot": args.shot,
        "preview": args.preview,
        "image": str(image_path),
        "blend": str(blend_path),
        "wall_clock_started_utc": started_at.isoformat(),
        "render_elapsed_seconds": round(elapsed, 3),
        "threads": scene.render.threads,
        "resolution": [scene.render.resolution_x, scene.render.resolution_y],
        "samples": samples,
        "blender_version": bpy.app.version_string,
        "render_engine": scene.render.engine,
        "seed": SEED,
    }
    record_path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(record, sort_keys=True))


if __name__ == "__main__":
    main()
