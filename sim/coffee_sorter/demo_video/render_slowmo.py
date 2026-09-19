#!/usr/bin/env python3
"""Render the verified 500 Hz capture without pose interpolation."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from render_segments import ASSETS, FPS, LOCK, PREVIEWS, ROOT, sha, write_json

CAPTURE = PREVIEWS / "high-rate-capture"
REPLAY = CAPTURE / "fresh-current-main/replay.json"
VALIDATION = CAPTURE / "fresh-current-main-reproduction/validation.json"
AUDIT = CAPTURE / "independent-audit.json"
PAIR = (1261, 1256)
OUTPUT_FRAMES = 120


def inputs():
    validation = json.loads(VALIDATION.read_text())
    audit = json.loads(AUDIT.read_text())
    payload = json.loads(REPLAY.read_text())
    assert audit["status"] == "passed_for_render_handoff"
    assert audit["artifacts"]["primary"]["replay"]["sha256"] == sha(REPLAY)
    assert audit["comparison"]["maximumPositionDeltaUnits"] == audit["comparison"]["maximumQuaternionDeltaUnits"] == 0
    assert audit["comparison"]["physicalContactsMatch"] is True
    assert validation["passed"] is True
    assert sha(REPLAY) == validation["sourceReplaySha256"]
    comparison = validation["sourcePoseComparison"]
    assert comparison["maximumPositionDeltaUnits"] == comparison["maximumQuaternionDeltaUnits"] == 0
    assert not comparison["mismatches"]
    assert payload["config"]["fps"] == 500
    interval = validation["usefulInterval"]
    frames = [(i, f) for i, f in enumerate(payload["frames"]) if interval["start"] <= f["t"] < interval["endExclusive"]]
    assert len(frames) == interval["samples"] == 143
    assert all(abs(b[1]["t"] - a[1]["t"] - 0.002) < 1e-9 for a, b in zip(frames, frames[1:]))
    metadata = {b[0]: b for b in payload["beans"]}
    assert metadata[1261][6] == "reject" and metadata[1256][6] == "accept"
    contacts = [c for c in payload["contacts"] if c["objectId"] in PAIR]
    assert len(contacts) == 2 and all(c["objectId"] == 1261 and c["associatedTarget"] for c in contacts)
    for _, frame in frames:
        assert set(PAIR) <= set(frame["beans"][::9])
    # The last capture frames place the good bean behind the splitter.
    # End after both recorded outcomes, while the two paths remain visible.
    frames = frames[:OUTPUT_FRAMES]
    assert frames[-1][1]["t"] > max(metadata[uid][7] for uid in PAIR)
    return payload, validation, frames, contacts


def render(args):
    import bpy
    from mathutils import Vector
    from bpy_extras.object_utils import world_to_camera_view

    sys.path.insert(0, str(ASSETS))
    from build_assets import make_assets
    from render_recording import apply_recorded_frame, create_recorded_beans
    from render_stills import add_area, configure_scene, point_at
    from scene_lighting import configure_shot
    from scene_machine import build_machine

    payload, validation, frames, contacts = inputs()
    contract = {
        "segment": {"frames": len(frames), "source_start": frames[0][0], "source_end": frames[-1][0]},
        "resolution": [640, 360], "fps": FPS, "source_hz": 500, "slowdown": 500 / FPS,
        "samples": args.samples, "study": args.study,
        "replay_sha256": sha(REPLAY), "validation_sha256": sha(VALIDATION), "independent_audit_sha256": sha(AUDIT),
        "renderer_sha256": sha(Path(__file__)),
        "asset_sources": {name: sha(ASSETS / name) for name in (
            "build_assets.py", "render_recording.py", "render_stills.py", "scene_machine.py", "scene_lighting.py", "scene_direction.json")},
    }
    output = args.output_dir
    output.mkdir(parents=True, exist_ok=True)
    (output / "frames").mkdir(exist_ok=True)
    manifest_path = output / "manifest.json"
    record = {
        "name": "05-ultra-slowmo", "status": "rendering", "contract": contract, "frames": [], "visibility_checks": [],
        "source": payload["source"], "source_path": str(REPLAY), "validation_path": str(VALIDATION),
        "independent_audit_path": str(AUDIT), "independent_audit": json.loads(AUDIT.read_text()),
        "validation": validation, "contacts": contacts,
        "pulses": [p for p in payload["pulses"] if p["pulseId"] in {c["pulseId"] for c in contacts}],
        "pair": [b for b in payload["beans"] if b[0] in PAIR],
        "git_revision": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "limitations": [
            "This is one development-seed simulation, not a hardware recording or performance benchmark.",
            "The capture uses 1000 objects per second. The separate live preview uses 500 objects per second.",
            "Every output frame uses one recorded physics-step pose. No pose interpolation or motion blur is applied.",
            "All recorded beans remain present. Camera tracking and wall cutaways are presentation changes.",
            "Air force contact is verified, but air flow is not rendered or simulated as a fluid.",
            "The camera retreats to retain both diverging paths. No bean positions or geometry are changed.",
            "The clip ends after both recorded outcomes, before the splitter obscures the good bean in later capture frames.",
        ],
    }
    if manifest_path.exists():
        record = json.loads(manifest_path.read_text())
        if record["contract"] != contract:
            raise RuntimeError("The output has different inputs. Select a new output directory.")
    else:
        write_json(manifest_path, record)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    prototypes = make_assets(export_obj=False)
    bpy.data.collections["CoffeeBeanPrototypes"].hide_render = True
    bpy.data.collections["CoffeeBeanPrototypes"].hide_viewport = True
    beans = bpy.data.collections.new("RecordedBeans")
    bpy.context.scene.collection.children.link(beans)
    objects, _ = create_recorded_beans(payload, [f for _, f in frames], prototypes, beans)
    record["machine"] = build_machine(payload)
    scene = configure_scene(True, args.samples)
    scene.render.resolution_x, scene.render.resolution_y = 640, 360
    scene.render.resolution_percentage = 100
    scene.render.threads_mode = "FIXED"
    scene.render.threads = 8
    scene.render.fps = FPS
    scene.render.use_motion_blur = False
    scene.render.use_persistent_data = True
    configure_shot("discharge", "noir-rim")
    camera = scene.camera
    camera.data.lens = 55
    camera.data.clip_start = 0.0001
    camera.data.dof.use_dof = True
    camera.data.dof.aperture_fstop = 11

    hidden = []
    for index, geometry in enumerate(payload["machine"]):
        if geometry.get("material") == "chute":
            hidden.append(geometry.get("name") or f"Recorded surface {index:02d}")
    hidden += ["Splitter side trim left", "Splitter side trim right", "Manifold clamp right", "Manifold support right"]
    for name in hidden:
        bpy.data.objects[name].hide_render = True
    record["presentation_hidden_objects"] = hidden
    for label, color, watts, size in (
        ("Pair warm key", (1, 0.85, 0.66), 0.6, 0.09),
        ("Pair cool rim", (0.60, 0.80, 1), 0.35, 0.035),
    ):
        add_area(label, (0, 0, 1), (0, 0, 0), watts, size, color)

    completed = {f["frame"]: f for f in record["frames"]}
    selected = {1, 20, 76, 77, 80, 100, len(frames)} if args.study else set(range(1, len(frames) + 1))
    for number, (source_index, frame) in enumerate(frames, 1):
        path = output / "frames" / f"frame_{number:04d}.png"
        if number in completed:
            assert path.exists() and sha(path) == completed[number]["png_sha256"]
            continue
        if path.exists():
            raise RuntimeError(f"An unverified output frame exists: {path}")
        scene.frame_set(number)
        position_error, rotation_error = apply_recorded_frame(frame, objects)
        bad, good = (objects[uid].location.copy() for uid in PAIR)
        target = (bad + good) / 2
        separation = abs(good.z - bad.z)
        # Lower the camera around the manifold, then rise after the pulse.
        # Smooth ramps retain foreground clearance during approach and exit.
        def smooth_ramp(start, end):
            value = max(0.0, min(1.0, (frame["t"] - start) / (end - start)))
            return value * value * (3 - 2 * value)

        manifold_window = smooth_ramp(1.60, 1.62) * (1 - smooth_ramp(1.65, 1.68))
        height = 0.15 - 0.115 * manifold_window
        # The positive-Y side places this pair close to the camera.
        camera.location = (min(target.x - 0.08, 0.20), target.y + 0.14 + 2.1 * separation, max(target.z + height, 0.535))
        point_at(camera, target)
        camera.data.dof.focus_distance = (camera.location - target).length
        for name, offset in (
            ("Pair warm key", (-0.025, 0.120, 0.085)),
            ("Pair cool rim", (0.055, -0.020, 0.075)),
        ):
            light = bpy.data.objects[name]
            light.location = target + Vector(offset)
            point_at(light, target)
        bpy.context.view_layer.update()
        projections = {}
        for uid in PAIR:
            projected = world_to_camera_view(scene, camera, objects[uid].location)
            assert 0.04 < projected.x < 0.96 and 0.04 < projected.y < 0.96 and projected.z > 0
            ray = objects[uid].location - camera.location
            origin = camera.location.copy()
            hit_object = None
            for _ in range(24):
                distance = ray.length + 0.01 - (origin - camera.location).length
                if distance <= 0:
                    break
                hit, location, _, _, candidate, _ = scene.ray_cast(
                    bpy.context.evaluated_depsgraph_get(), origin, ray.normalized(), distance=distance)
                if not hit:
                    break
                if not candidate.hide_render:
                    hit_object = candidate
                    break
                origin = location + ray.normalized() * 0.00001
            projections[uid] = {"world_position": list(objects[uid].location), "image_position": [projected.x, 1 - projected.y],
                                "center_ray_first_object": hit_object.name if hit_object else None}
        record["visibility_checks"].append({"frame": number, "source_time": frame["t"], "pair": projections})
        if number not in selected:
            continue
        scene.render.filepath = str(path)
        tick = time.perf_counter()
        bpy.ops.render.render(write_still=True)
        record["frames"].append({
            "frame": number, "source_index": source_index, "source_time": frame["t"],
            "active_beans": len(frame["beans"]) // 9, "all_beans_retained": True,
            "pose_rows_sha256": hashlib.sha256(json.dumps(frame["beans"], separators=(",", ":")).encode()).hexdigest(),
            "camera": list(camera.location), "target": list(target), "focus_distance": camera.data.dof.focus_distance,
            "pair": projections, "lens_mm": camera.data.lens,
            "max_position_error_m": position_error, "max_quaternion_component_error": rotation_error,
            "render_seconds": round(time.perf_counter() - tick, 3), "png_sha256": sha(path),
        })
        write_json(manifest_path, record)
        print(f"FRAME slowmo {number}/{len(frames)} source={frame['t']}", flush=True)
    assert sha(REPLAY) == contract["replay_sha256"] and sha(VALIDATION) == contract["validation_sha256"]
    assert sha(AUDIT) == contract["independent_audit_sha256"]
    record["status"] = "study-complete" if args.study else "frames-complete"
    write_json(manifest_path, record)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=PREVIEWS / "segments-v2/05-ultra-slowmo-v2")
    parser.add_argument("--samples", type=int, default=12)
    parser.add_argument("--study", action="store_true")
    parser.add_argument("--worker", action="store_true")
    argv = sys.argv[sys.argv.index("--") + 1:] if "--worker" in sys.argv else sys.argv[1:]
    args = parser.parse_args(argv)
    args.output_dir = args.output_dir.resolve()
    if args.samples < 1:
        parser.error("Samples must be positive.")
    if args.worker:
        render(args)
        return 0
    inputs()
    output = args.output_dir
    output.mkdir(parents=True, exist_ok=True)
    video = output / "preview.mp4"
    manifest_path = output / "manifest.json"
    if video.exists():
        assert sha(video) == json.loads(manifest_path.read_text())["video"]["sha256"]
        print(f"Preserving completed video: {video}")
        return 0
    command = ["blender", "--background", "--threads", "8", "--python-exit-code", "1", "--python", str(Path(__file__).resolve()),
               "--", "--worker", "--output-dir", str(output), "--samples", str(args.samples)]
    if args.study:
        command.append("--study")
    with LOCK.open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("The shared render slot is busy.")
            return 75
        tick = time.perf_counter()
        with (output / "blender.log").open("a") as log:
            result = subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
        elapsed = round(time.perf_counter() - tick, 3)
    if result.returncode:
        print(f"Blender failed. Read {output / 'blender.log'}")
        return result.returncode
    manifest = json.loads(manifest_path.read_text())
    manifest["process_seconds"] = elapsed
    manifest["blender_command"] = command
    write_json(manifest_path, manifest)
    if args.study:
        print(f"STUDY_READY {output}")
        return 0
    encode = ["ffmpeg", "-n", "-threads", "2", "-framerate", str(FPS), "-i", str(output / "frames/frame_%04d.png"),
              "-c:v", "libx264", "-threads", "2", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(video)]
    with (output / "ffmpeg.log").open("w") as log:
        subprocess.run(encode, stdout=log, stderr=subprocess.STDOUT, check=True)
    probe = json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0", "-show_entries",
        "stream=codec_name,width,height,r_frame_rate,nb_read_frames:format=duration", "-of", "json", str(video)], text=True))
    stream = probe["streams"][0]
    assert (stream["width"], stream["height"], stream["r_frame_rate"], int(stream["nb_read_frames"])) == (640, 360, "30/1", OUTPUT_FRAMES)
    manifest["status"] = "complete"
    manifest["video"] = {"sha256": sha(video), "probe": probe, "encode_command": encode}
    write_json(manifest_path, manifest)
    print(f"VIDEO_READY {video}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
