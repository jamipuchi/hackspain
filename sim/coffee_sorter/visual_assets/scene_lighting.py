#!/usr/bin/env python3
"""Camera, world and studio-light presets for the full coffee sorter scene.

Every number lives in scene_direction.json. This module only applies it:

    import scene_lighting
    settings = scene_lighting.configure_shot("hero", "noir-rim")

configure_shot creates the active camera, the world, the studio lights, the colour
management and the look's material override in the current Blender scene. It returns
the applied settings as a JSON-serializable dict. It never deletes meshes, moves beans,
edits geometry, renders, or saves files. A second call replaces only the camera and
lights that an earlier call created.

Print the resolved settings without rendering:
  blender --background scene.blend --python scene_lighting.py -- --shot hero --look noir-rim
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import sys

import bpy
from mathutils import Vector


HERE = Path(__file__).resolve().parent
DIRECTION_PATH = HERE / "scene_direction.json"
COLLECTION_NAME = "SceneDirection"
WORLD_NAME = "SceneDirection world"
OVERRIDE_NAME = "SceneDirection override"
LINESET_NAME = "SceneDirection outlines"


def load_direction(path=DIRECTION_PATH):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def available(path=DIRECTION_PATH):
    direction = load_direction(path)
    return {"shots": list(direction["shots"]), "looks": list(direction["looks"])}


def _collection(scene):
    collection = bpy.data.collections.get(COLLECTION_NAME)
    if collection is None:
        collection = bpy.data.collections.new(COLLECTION_NAME)
    if collection.name not in scene.collection.children:
        scene.collection.children.link(collection)
    return collection


def _clear_owned(collection):
    """Remove only the cameras and lights that an earlier configure_shot call created."""
    for obj in list(collection.objects):
        if obj.type not in {"CAMERA", "LIGHT"}:
            continue
        data = obj.data
        bpy.data.objects.remove(obj, do_unlink=True)
        if data.users == 0:
            (bpy.data.cameras if isinstance(data, bpy.types.Camera) else bpy.data.lights).remove(data)


def _aim(obj, target):
    obj.rotation_mode = "XYZ"
    obj.rotation_euler = (Vector(target) - obj.location).to_track_quat("-Z", "Y").to_euler()


def _add_camera(scene, collection, spec):
    data = bpy.data.cameras.new("SceneDirection camera")
    data.sensor_fit = "HORIZONTAL"  # the horizontal framing survives an aspect change
    data.sensor_width = spec["sensor_width_mm"]
    data.lens = spec["lens_mm"]
    data.clip_start = spec["clip_start_m"]
    data.clip_end = spec["clip_end_m"]
    data.dof.use_dof = bool(spec["dof"])
    data.dof.aperture_fstop = spec["fstop"]
    data.dof.focus_distance = spec["focus_distance_m"]
    obj = bpy.data.objects.new("SceneDirection camera", data)
    collection.objects.link(obj)
    obj.location = spec["location"]
    _aim(obj, spec["target"])
    scene.camera = obj
    return obj


def _add_light(collection, spec):
    data = bpy.data.lights.new(spec["name"], "AREA")
    data.energy = spec["energy_w"]
    data.color = spec["color"]
    data.shape = spec["shape"]
    if spec["shape"] == "RECTANGLE":
        data.size, data.size_y = spec["size_m"]
    else:
        data.size = spec["size_m"]
    data.spread = math.radians(spec.get("spread_deg", 180))
    obj = bpy.data.objects.new(spec["name"], data)
    collection.objects.link(obj)
    obj.location = spec["location"]
    _aim(obj, spec["target"])
    # A fill with glossy off adds light without a second highlight on every surface.
    obj.visible_glossy = bool(spec.get("glossy", True))
    return obj


def _set_world(scene, spec):
    world = bpy.data.worlds.get(WORLD_NAME) or bpy.data.worlds.new(WORLD_NAME)
    world.color = spec["color"]
    if world.node_tree is None:
        world.use_nodes = True
    nodes, links = world.node_tree.nodes, world.node_tree.links
    nodes.clear()
    lighting = nodes.new("ShaderNodeBackground")
    lighting.inputs["Color"].default_value = (*spec["color"], 1.0)
    lighting.inputs["Strength"].default_value = spec["strength"]
    surface = lighting.outputs["Background"]
    if "camera_color" in spec:
        # The camera sees camera_color as the backdrop. Only color x strength lights the scene.
        backdrop = nodes.new("ShaderNodeBackground")
        backdrop.inputs["Color"].default_value = (*spec["camera_color"], 1.0)
        mix = nodes.new("ShaderNodeMixShader")
        links.new(nodes.new("ShaderNodeLightPath").outputs["Is Camera Ray"], mix.inputs["Fac"])
        links.new(surface, mix.inputs[1])
        links.new(backdrop.outputs["Background"], mix.inputs[2])
        surface = mix.outputs["Shader"]
    links.new(surface, nodes.new("ShaderNodeOutputWorld").inputs["Surface"])
    scene.world = world


def _set_color_management(scene, spec, exposure_offset):
    view = scene.view_settings
    view.view_transform = spec["view_transform"]
    applied_look = None
    # Look names differ between Blender builds. Report the accepted name.
    for candidate in (spec["look"], spec["look"].replace("AgX - ", ""), "None"):
        try:
            view.look = candidate
            applied_look = candidate
            break
        except TypeError:
            continue
    view.exposure = spec["exposure"] + exposure_offset
    view.gamma = spec["gamma"]
    return {"view_transform": view.view_transform, "look": applied_look,
            "exposure": round(view.exposure, 4), "gamma": view.gamma}


def _new_override_material():
    old = bpy.data.materials.get(OVERRIDE_NAME)
    if old:
        bpy.data.materials.remove(old)
    mat = bpy.data.materials.new(OVERRIDE_NAME)
    if mat.node_tree is None:
        mat.use_nodes = True
    mat.node_tree.nodes.clear()
    return mat


def _matte_override(spec):
    mat = _new_override_material()
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    shader = nodes.new("ShaderNodeBsdfPrincipled")
    shader.inputs["Base Color"].default_value = (*spec["color"], 1.0)
    shader.inputs["Roughness"].default_value = spec["roughness"]
    specular = shader.inputs.get("Specular IOR Level") or shader.inputs.get("Specular")
    if specular:
        specular.default_value = spec["specular"]
    links.new(shader.outputs["BSDF"], nodes.new("ShaderNodeOutputMaterial").inputs["Surface"])
    mat.diffuse_color = (*spec["color"], 1.0)
    return mat


def _map_range(nodes, links, source, low, high):
    node = nodes.new("ShaderNodeMapRange")
    node.clamp = True
    node.inputs["From Min"].default_value = low
    node.inputs["From Max"].default_value = high
    links.new(source, node.inputs["Value"])
    return node.outputs["Result"]


def _blueprint_override(spec):
    """One blue diffuse material. Bright lines come from the shader, not from Freestyle:
    a Bevel-normal test marks panel edges, a facing test marks silhouettes."""
    mat = _new_override_material()
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    diffuse = nodes.new("ShaderNodeBsdfDiffuse")
    diffuse.inputs["Color"].default_value = (*spec["color"], 1.0)
    emission = nodes.new("ShaderNodeEmission")
    emission.inputs["Color"].default_value = (*spec["line_color"], 1.0)
    emission.inputs["Strength"].default_value = spec["line_strength"]

    bevel = nodes.new("ShaderNodeBevel")
    bevel.samples = spec["edge_samples"]
    bevel.inputs["Radius"].default_value = spec["edge_radius_m"]
    geometry = nodes.new("ShaderNodeNewGeometry")
    dot = nodes.new("ShaderNodeVectorMath")
    dot.operation = "DOT_PRODUCT"
    links.new(bevel.outputs["Normal"], dot.inputs[0])
    links.new(geometry.outputs["Normal"], dot.inputs[1])
    bend = nodes.new("ShaderNodeMath")  # 0 on a flat face, grows where the bevelled normal turns
    bend.operation = "SUBTRACT"
    bend.inputs[0].default_value = 1.0
    links.new(dot.outputs["Value"], bend.inputs[1])
    edge = _map_range(nodes, links, bend.outputs["Value"], *spec["edge_range"])

    facing = nodes.new("ShaderNodeLayerWeight")
    facing.inputs["Blend"].default_value = 0.5
    rim = _map_range(nodes, links, facing.outputs["Facing"], *spec["rim_range"])
    rim_gain = nodes.new("ShaderNodeMath")
    rim_gain.operation = "MULTIPLY"
    rim_gain.inputs[1].default_value = spec["rim_gain"]
    links.new(rim, rim_gain.inputs[0])

    lines = nodes.new("ShaderNodeMath")
    lines.operation = "MAXIMUM"
    links.new(edge, lines.inputs[0])
    links.new(rim_gain.outputs["Value"], lines.inputs[1])
    mix = nodes.new("ShaderNodeMixShader")
    position = nodes.new("ShaderNodeSeparateXYZ")
    links.new(geometry.outputs["Position"], position.inputs["Vector"])
    above_floor = nodes.new("ShaderNodeMath")
    above_floor.operation = "GREATER_THAN"
    above_floor.inputs[1].default_value = spec["line_min_z_m"]
    links.new(position.outputs["Z"], above_floor.inputs[0])
    masked_lines = nodes.new("ShaderNodeMath")
    masked_lines.operation = "MULTIPLY"
    links.new(lines.outputs["Value"], masked_lines.inputs[0])
    links.new(above_floor.outputs["Value"], masked_lines.inputs[1])
    links.new(masked_lines.outputs["Value"], mix.inputs["Fac"])
    links.new(diffuse.outputs["BSDF"], mix.inputs[1])
    links.new(emission.outputs["Emission"], mix.inputs[2])
    links.new(mix.outputs["Shader"], nodes.new("ShaderNodeOutputMaterial").inputs["Surface"])
    mat.diffuse_color = (*spec["color"], 1.0)
    return mat


def _set_freestyle(scene, spec):
    enabled = bool(spec and spec.get("enabled"))
    scene.render.use_freestyle = enabled
    for view_layer in scene.view_layers:
        view_layer.use_freestyle = enabled
        if not enabled:
            continue
        settings = view_layer.freestyle_settings
        settings.crease_angle = math.radians(spec["crease_angle_deg"])
        lineset = settings.linesets.get(LINESET_NAME) or settings.linesets.new(LINESET_NAME)
        lineset.select_silhouette = lineset.select_border = lineset.select_crease = True
        lineset.linestyle.color = spec["color"]
        lineset.linestyle.thickness = spec["thickness_px"]
    if enabled:
        scene.render.line_thickness_mode = "ABSOLUTE"
        scene.render.line_thickness = spec["thickness_px"]
    return enabled


def configure_shot(shot_name, look_name):
    direction = load_direction()
    if shot_name not in direction["shots"]:
        raise ValueError(f"Unknown shot {shot_name!r}. Choose from {', '.join(direction['shots'])}.")
    if look_name not in direction["looks"]:
        raise ValueError(f"Unknown look {look_name!r}. Choose from {', '.join(direction['looks'])}.")
    shot, look = direction["shots"][shot_name], direction["looks"][look_name]
    rig = look["rigs"][shot["rig"]]

    scene = bpy.context.scene
    collection = _collection(scene)
    _clear_owned(collection)
    camera = _add_camera(scene, collection, shot["camera"])
    for light in rig["lights"]:
        _add_light(collection, light)
    _set_world(scene, look["world"])
    color_management = _set_color_management(scene, look["color_management"], rig.get("exposure_offset", 0.0))

    override = look.get("material_override")
    if override and override["type"] == "blueprint":
        # Line width and silhouette limits depend on the shot scale and camera angle.
        override = {**override, **shot["blueprint"]}
    if override is None:
        material = None
        stale = bpy.data.materials.get(OVERRIDE_NAME)
        if stale:
            bpy.data.materials.remove(stale)
    elif override["type"] == "matte":
        material = _matte_override(override)
    elif override["type"] == "blueprint":
        material = _blueprint_override(override)
    else:
        raise ValueError(f"unknown material_override type {override['type']!r}")
    for view_layer in scene.view_layers:
        view_layer.material_override = material
    freestyle = _set_freestyle(scene, look.get("freestyle"))

    return {
        "shot": shot_name,
        "look": look_name,
        "direction_sha256": hashlib.sha256(DIRECTION_PATH.read_bytes()).hexdigest(),
        "blender_version": bpy.app.version_string,
        "camera": {**shot["camera"], "object": camera.name,
                   "rotation_euler_rad": [round(angle, 6) for angle in camera.rotation_euler],
                   "sensor_fit": camera.data.sensor_fit},
        "aspect": shot["aspect"],
        "world": look["world"],
        "color_management": color_management,
        "rig": shot["rig"],
        "lights": rig["lights"],
        "material_override": {**override, "material": material.name} if override else None,
        # An override also replaces glass and emissive materials. See SCENE_DIRECTION.md.
        "override_replaces_transparent_materials": override is not None,
        "freestyle": freestyle,
        "render_hints": direction["render_hints"],
    }


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--shot", required=True)
    parser.add_argument("--look", required=True)
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else [])
    print(json.dumps(configure_shot(args.shot, args.look), indent=2))


if __name__ == "__main__":
    main()
