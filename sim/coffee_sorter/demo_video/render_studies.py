#!/usr/bin/env python3
"""Render one or all scene studies under the shared CPU lock."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
ASSETS = HERE.parent / "visual_assets"
REPLAY = HERE.parent / "web/replay.json"
SHOTS = HERE / "study_shots.json"
LOCK = Path("/private/tmp/hackspain-coffee-runtime.lock")
DEFAULT_OUTPUT = Path("/private/tmp/coffee-demo-video-previews/basic-scene-renders-v1")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def render_still(name, output, spec):
    import bpy
    from mathutils import Vector

    sys.path.insert(0, str(ASSETS))
    from build_assets import make_assets
    from render_recording import apply_recorded_frame, create_recorded_beans
    from render_stills import add_area, configure_scene, point_at
    from scene_lighting import configure_shot
    from scene_machine import build_machine

    start = time.perf_counter()
    replay_hash = sha(REPLAY)
    payload = json.loads(REPLAY.read_bytes())
    frame = payload["frames"][spec["frame"]]
    bpy.ops.wm.read_factory_settings(use_empty=True)
    prototypes = make_assets(export_obj=False)
    library = bpy.data.collections["CoffeeBeanPrototypes"]
    library.hide_render = library.hide_viewport = True
    beans = bpy.data.collections.new("RecordedBeans")
    bpy.context.scene.collection.children.link(beans)
    objects, metadata = create_recorded_beans(payload, [frame], prototypes, beans)
    machine = build_machine(payload)
    scene = configure_scene(True, 24)
    scene.render.resolution_x, scene.render.resolution_y = 960, 540
    scene.render.resolution_percentage = 100
    scene.render.threads_mode = "FIXED"
    scene.render.threads = 8
    scene.render.use_motion_blur = False
    scene.frame_set(spec["frame"] + 1)
    direction = configure_shot(spec["rig"], spec["look"])
    position_error, rotation_error = apply_recorded_frame(frame, objects)

    camera = scene.camera
    camera.location = spec["camera"]
    point_at(camera, spec["target"])
    camera.data.lens = spec["lens"]
    camera.data.clip_start = 0.0001
    camera.data.dof.use_dof = True
    camera.data.dof.aperture_fstop = spec["fstop"]
    focus = Vector(spec["target"])
    if spec.get("focus_uids"):
        focus = sum((objects[uid].location for uid in spec["focus_uids"]), Vector()) / len(spec["focus_uids"])
    focus += Vector(spec.get("focus_offset", [0, 0, 0]))
    camera.data.dof.focus_distance = (camera.location - focus).length

    hidden = []
    if spec.get("cutaway_near_wall"):
        for index, geometry in enumerate(payload["machine"]):
            if geometry.get("material") == "chute" and geometry["pos"][1] < -0.25:
                obj = bpy.data.objects[geometry.get("name") or f"Recorded surface {index:02d}"]
                obj.hide_render = True
                hidden.append(obj.name)
    for name_to_hide in spec.get("hide_objects", []):
        bpy.data.objects[name_to_hide].hide_render = True
        hidden.append(name_to_hide)

    for light in bpy.data.collections["SceneDirection"].objects:
        if light.type == "LIGHT":
            light.data.energy *= spec.get("light_scale", 1)
    added_lights = []
    if spec.get("macro_lights"):
        for label, offset, energy, size, color in (
            ("Macro warm key", (-0.025, -0.020, 0.022), 0.012, 0.022, (1.0, 0.78, 0.50)),
            ("Macro cool rim", (0.014, 0.025, 0.026), 0.022, 0.025, (0.57, 0.76, 1.0)),
        ):
            loc = focus + Vector(offset)
            add_area(label, loc, focus, energy, size, color)
            added_lights.append({"name": label, "location": list(loc), "watts": energy, "size": size, "color": color})
    if spec.get("pair_lights"):
        for label, offset, energy, size, color in (
            ("Pair warm key", (-0.025, -0.120, 0.085), 0.60, 0.09, (1.0, 0.85, 0.66)),
            ("Pair cool rim", (0.055, 0.020, 0.075), 0.35, 0.035, (0.60, 0.80, 1.0)),
        ):
            loc = focus + Vector(offset)
            add_area(label, loc, focus, energy, size, color)
            added_lights.append({"name": label, "location": list(loc), "watts": energy, "size": size, "color": color})

    # These are sampled valve states, not per-bean contact or air-flow graphics.
    active_fires = [f for f in payload["fires"] if f[0] <= frame["t"] < f[1]]
    focused = {uid: {"metadata": metadata[uid], "world_position": list(objects[uid].location)} for uid in spec.get("focus_uids", [])}
    from bpy_extras.object_utils import world_to_camera_view
    bpy.context.view_layer.update()
    for uid, info in focused.items():
        projected = world_to_camera_view(scene, camera, objects[uid].location)
        info["image_position_normalized"] = [projected.x, 1 - projected.y]
    output.mkdir(parents=True, exist_ok=True)
    scene.render.filepath = str(output / "scene.png")
    manifest = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "git_revision": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "shot": name, "spec": spec, "direction": direction, "added_lights": added_lights,
        "replay_sha256": replay_hash, "replay_source": payload["source"],
        "source_frame_index": spec["frame"], "source_time_seconds": frame["t"],
        "source_frame": frame, "focused_objects": focused, "sampled_active_fires": active_fires,
        "active_beans": len(objects), "all_beans_retained": True,
        "max_position_error_m": position_error, "max_quaternion_component_error": rotation_error,
        "presentation_hidden_objects": hidden, "machine": machine,
        "source_sha256": {str(path.relative_to(ROOT)): sha(path) for path in [Path(__file__), SHOTS] + [ASSETS / f for f in (
            "build_assets.py", "render_recording.py", "render_stills.py", "scene_machine.py", "scene_lighting.py", "scene_direction.json") ]},
        "blender": bpy.app.version_string,
        "render": {"engine": "CYCLES", "device": "CPU", "resolution": [960, 540], "samples": 24, "threads": 8, "motion_blur": False},
        "build_seconds": round(time.perf_counter() - start, 3),
        "limitations": ["Historical recorded poses. Not the current live engine.",
                        "No per-bean pulse-contact evidence exists in this replay.",
                        "No physical intermediate poses or air flow were generated.",
                        "Any omitted wall is a presentation cutaway listed in this manifest."]
    }
    tick = time.perf_counter()
    bpy.ops.render.render(write_still=True)
    manifest["render_seconds"] = round(time.perf_counter() - tick, 3)
    assert sha(REPLAY) == replay_hash, "The recording changed during the render."
    manifest["png_sha256"] = sha(output / "scene.png")
    bpy.data.texts.new("source_manifest.json").write(json.dumps(manifest, indent=2))
    bpy.ops.wm.save_as_mainfile(filepath=str(output / "scene.blend"), compress=True)
    manifest["blend_sha256"] = sha(output / "scene.blend")
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps({"shot": name, "render_seconds": manifest["render_seconds"], "position_error": position_error}), flush=True)


def main():
    shots = json.loads(SHOTS.read_text())
    argv = sys.argv[sys.argv.index("--") + 1:] if "--blender-worker" in sys.argv else sys.argv[1:]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", choices=shots)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--blender-worker", action="store_true")
    parser.add_argument("--blender", default=os.environ.get("BLENDER", "blender"))
    args = parser.parse_args(argv)
    if args.blender_worker:
        if not args.only:
            parser.error("Select one shot for each Blender subprocess.")
        render_still(args.only, args.output_dir.resolve() / args.only, shots[args.only])
        return 0

    for name in ([args.only] if args.only else shots):
        output = args.output_dir.resolve() / name
        if (output / "scene.png").exists():
            print(f"Preserving existing render: {name}", flush=True)
            continue
        command = [args.blender, "--background", "--threads", "8", "--python-exit-code", "1",
                   "--python", str(Path(__file__).resolve()), "--", "--blender-worker", "--only", name,
                   "--output-dir", str(args.output_dir.resolve())]
        with LOCK.open("a+") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                print("The shared render slot is busy. Completed previews remain available.", flush=True)
                return 75
            output.mkdir(parents=True, exist_ok=True)
            tick = time.perf_counter()
            with (output / "blender.log").open("w") as log:
                result = subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
            elapsed = round(time.perf_counter() - tick, 3)
        if result.returncode:
            print((output / "blender.log").read_text(), flush=True)
            return result.returncode
        manifest_path = output / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["command"] = command
        manifest["process_seconds"] = elapsed
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
        print(f"Rendered {name} in {elapsed}s: {output / 'scene.png'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
