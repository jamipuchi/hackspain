"""Build and render the first coffee demo segment inside Blender."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import time

import bpy
from mathutils import Vector


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
VISUAL_ASSETS = HERE.parent / "visual_assets"
REPLAY = HERE.parent / "web/replay.json"
sys.path.insert(0, str(VISUAL_ASSETS))

from build_assets import make_assets
from render_recording import apply_recorded_frame, create_recorded_beans, sha
from render_stills import configure_scene
from scene_lighting import configure_shot
from scene_machine import build_machine


SOURCE_FRAME = 60
FPS = 24
DURATION_SECONDS = 4
FRAME_COUNT = FPS * DURATION_SECONDS
CAMERA_TRAVEL_METRES = 0.22


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--threads", type=int, default=8)
    parser.add_argument("--samples", type=int, default=12)
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else [])
    if not 1 <= args.threads <= 8:
        parser.error("Threads must be between one and eight.")
    if args.samples < 1:
        parser.error("Samples must be positive.")

    started = time.perf_counter()
    output = args.output_dir.resolve()
    frames_dir = output / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    payload = json.loads(REPLAY.read_bytes())
    frame = payload["frames"][SOURCE_FRAME]
    replay_hash = sha(REPLAY)

    bpy.ops.wm.read_factory_settings(use_empty=True)
    prototypes = make_assets(export_obj=False)
    library = bpy.data.collections["CoffeeBeanPrototypes"]
    library.hide_render = True
    library.hide_viewport = True
    beans = bpy.data.collections.new("RecordedBeans")
    bpy.context.scene.collection.children.link(beans)
    objects, metadata = create_recorded_beans(payload, [frame], prototypes, beans)
    machine = build_machine(payload)

    scene = configure_scene(True, args.samples)
    scene.render.resolution_x = 640
    scene.render.resolution_y = 360
    scene.render.resolution_percentage = 100
    scene.render.threads_mode = "FIXED"
    scene.render.threads = args.threads
    scene.render.use_motion_blur = False
    scene.render.image_settings.file_format = "PNG"
    scene.render.filepath = str(frames_dir / "frame_")
    scene.render.fps = FPS
    scene.frame_start = 1
    scene.frame_end = FRAME_COUNT
    scene.frame_step = 1

    direction = configure_shot("hero", "warm-roastery")
    position_error, rotation_error = apply_recorded_frame(frame, objects)
    camera = scene.camera
    start_location = camera.location.copy()
    forward = camera.matrix_world.to_quaternion() @ Vector((0.0, 0.0, -1.0))
    end_location = start_location + forward.normalized() * CAMERA_TRAVEL_METRES

    camera.location = start_location
    camera.keyframe_insert(data_path="location", frame=1)
    camera.location = end_location
    camera.keyframe_insert(data_path="location", frame=FRAME_COUNT)
    for curve in camera.animation_data.action.fcurves:
        for point in curve.keyframe_points:
            point.interpolation = "BEZIER"
            point.handle_left_type = "AUTO_CLAMPED"
            point.handle_right_type = "AUTO_CLAMPED"
    scene.frame_set(1)

    record = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "git_revision": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "source_sha256": {
            name: sha(path)
            for name, path in {
                "intro_segment_scene.py": Path(__file__),
                "render_scene.py": VISUAL_ASSETS / "render_scene.py",
                "scene_machine.py": VISUAL_ASSETS / "scene_machine.py",
                "scene_lighting.py": VISUAL_ASSETS / "scene_lighting.py",
                "scene_direction.json": VISUAL_ASSETS / "scene_direction.json",
                "render_recording.py": VISUAL_ASSETS / "render_recording.py",
                "render_stills.py": VISUAL_ASSETS / "render_stills.py",
                "build_assets.py": VISUAL_ASSETS / "build_assets.py",
            }.items()
        },
        "segment": "01-machine-overview",
        "shot": "hero",
        "look": "warm-roastery",
        "source_frame_index": SOURCE_FRAME,
        "source_time_seconds": frame["t"],
        "replay_path": str(REPLAY.resolve()),
        "replay_sha256": replay_hash,
        "source": payload["source"],
        "config": payload["config"],
        "schema": payload["schema"],
        "active_beans": len(objects),
        "beans": [metadata[uid] for uid in sorted(objects)],
        "source_frame": frame,
        "recorded_machine": payload["machine"],
        "machine": machine,
        "max_position_error_m": position_error,
        "max_quaternion_component_error": rotation_error,
        "camera": {
            "start_location": list(start_location),
            "end_location": list(end_location),
            "travel_metres": CAMERA_TRAVEL_METRES,
            "rotation_is_fixed": True,
            "interpolation": "BEZIER_AUTO_CLAMPED",
        },
        "direction": direction,
        "render": {
            "engine": scene.render.engine,
            "device": scene.cycles.device,
            "samples": scene.cycles.samples,
            "threads": scene.render.threads,
            "resolution": [scene.render.resolution_x, scene.render.resolution_y],
            "fps": FPS,
            "duration_seconds": DURATION_SECONDS,
            "frame_count": FRAME_COUNT,
            "motion_blur": False,
        },
        "limitations": [
            "The beans use one old recorded pose for the complete segment.",
            "The segment shows camera motion only.",
            "The segment does not show the current live engine.",
            "The segment establishes no sorting or runtime result.",
        ],
    }
    record["build_seconds"] = round(time.perf_counter() - started, 3)

    render_started = time.perf_counter()
    bpy.ops.render.render(animation=True)
    record["render_seconds"] = round(time.perf_counter() - render_started, 3)
    frame_paths = sorted(frames_dir.glob("frame_*.png"))
    if len(frame_paths) != FRAME_COUNT:
        raise RuntimeError(f"Expected {FRAME_COUNT} frames. Found {len(frame_paths)}.")
    record["frames"] = [
        {"name": path.name, "sha256": sha(path)} for path in frame_paths
    ]
    if sha(REPLAY) != replay_hash:
        raise RuntimeError("The replay changed during the render.")

    record["total_blender_seconds"] = round(time.perf_counter() - started, 3)
    bpy.data.texts.new("source_manifest.json").write(json.dumps(record, indent=2) + "\n")
    bpy.ops.wm.save_as_mainfile(filepath=str(output / "segment.blend"), compress=True)
    record["blend_sha256"] = sha(output / "segment.blend")
    (output / "manifest.json").write_text(json.dumps(record, indent=2) + "\n")
    print(
        json.dumps(
            {
                "segment": record["segment"],
                "frame_count": FRAME_COUNT,
                "render_seconds": record["render_seconds"],
                "max_position_error_m": position_error,
                "max_quaternion_component_error": rotation_error,
            },
            indent=2,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
