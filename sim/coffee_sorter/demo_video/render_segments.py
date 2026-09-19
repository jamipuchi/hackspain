#!/usr/bin/env python3
"""Render separate review clips from approved scenes and recorded poses."""
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
ROOT = HERE.parents[2]
ASSETS = HERE.parent / "visual_assets"
REPLAY = HERE.parent / "web/replay.json"
PREVIEWS = Path("/private/tmp/coffee-demo-video-previews")
LOCK = Path("/private/tmp/hackspain-coffee-runtime.lock")
FPS = 30
SEGMENTS = {
    "01-bean-macro": {"scene": "01-bean-macro", "frames": 90, "motion": "macro-focus", "source_start": 60},
    "02-conveyor-reveal": {"scene": "02a-low-reveal-start", "frames": 90, "motion": "lateral-reveal", "source_start": 30},
    "03-overhead-inspection": {"scene": "03-overhead-inspection", "frames": 90, "motion": "locked", "source_start": 30},
    "04-blueprint-discharge": {"scene": "04-blueprint-discharge", "frames": 60, "motion": "locked", "source_start": 45},
    "04b-normal-discharge": {"scene": "04b-normal-discharge", "frames": 60, "motion": "locked", "source_start": 45},
    "07-warm-closing": {"scene": "07-warm-closing", "frames": 90, "motion": "closing-slide", "source_start": 30, "lens": 40},
    "07b-blueprint-closing": {"scene": "07b-blueprint-closing", "frames": 90, "motion": "closing-slide", "source_start": 30, "lens": 40},
    "07c-normal-closing": {"scene": "07c-normal-closing", "frames": 90, "motion": "closing-slide", "source_start": 30, "lens": 40},
}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path, data):
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(data, indent=2) + "\n")
    temporary.replace(path)


def add_render_options(parser):
    parser.add_argument("--full-hd", action="store_true", help="Render native 1920 by 1080 frames with production quality settings.")
    parser.add_argument("--samples", type=int)
    parser.add_argument("--device", choices=("CPU", "METAL"), default="CPU")
    parser.add_argument("--frame-limit", type=int, help="Render this many new frames, then stop. Resume without this option.")


def render_settings(args):
    return {
        "resolution": [1920, 1080] if args.full_hd else [640, 360],
        "samples": args.samples if args.samples is not None else (48 if args.full_hd else 12),
        "device": args.device,
        "adaptive_threshold": 0.035 if args.full_hd else 0.18,
        "crf": 16 if args.full_hd else 20,
    }


def configure_quality(scene, settings):
    import bpy

    scene.render.resolution_x, scene.render.resolution_y = settings["resolution"]
    scene.render.resolution_percentage = 100
    scene.cycles.samples = settings["samples"]
    scene.cycles.adaptive_threshold = settings["adaptive_threshold"]
    scene.cycles.use_denoising = True
    scene.cycles.device = "CPU"
    if settings["device"] == "METAL":
        preferences = bpy.context.preferences.addons["cycles"].preferences
        preferences.compute_device_type = "METAL"
        preferences.get_devices()
        if not any(device.type == "METAL" for device in preferences.devices):
            raise RuntimeError("No Metal render device is available.")
        for device in preferences.devices:
            device.use = device.type == "METAL"
        scene.cycles.device = "GPU"


def worker_options(args):
    options = ["--device", args.device]
    if args.full_hd:
        options.append("--full-hd")
    if args.samples is not None:
        options += ["--samples", str(args.samples)]
    if args.frame_limit is not None:
        options += ["--frame-limit", str(args.frame_limit)]
    return options


def render(name, args):
    import bpy
    from mathutils import Vector

    sys.path.insert(0, str(ASSETS))
    from render_recording import apply_recorded_frame, create_recorded_beans
    from render_stills import point_at

    spec = SEGMENTS[name]
    base = args.base_dir / spec["scene"]
    output = args.output_dir / name
    output.mkdir(parents=True, exist_ok=True)
    frames_dir = output / "frames"
    frames_dir.mkdir(exist_ok=True)
    source_manifest = json.loads((base / "manifest.json").read_text())
    assert sha(base / "scene.blend") == source_manifest["blend_sha256"]
    assert sha(REPLAY) == source_manifest["replay_sha256"]
    payload = json.loads(REPLAY.read_bytes())
    source_indices = [spec["source_start"] + (0 if spec["motion"] == "macro-focus" else i) for i in range(spec["frames"])]
    source_frames = [payload["frames"][i] for i in source_indices]
    assert payload["config"]["fps"] == FPS
    settings = render_settings(args)
    contract = {
        "segment": spec, "fps": FPS, **settings,
        "base_blend_sha256": source_manifest["blend_sha256"], "replay_sha256": sha(REPLAY),
        "renderer_sha256": sha(Path(__file__)),
        "importer_sha256": sha(ASSETS / "render_recording.py"),
    }
    manifest_path = output / "manifest.json"
    record = {
        "name": name, "contract": contract, "status": "rendering", "frames": [],
        "git_revision": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "source_scene_manifest": source_manifest, "source_indices": source_indices,
        "limitations": [
            "The source is a historical 30 Hz recording, not the current engine.",
            "Dynamic clips use one actual recorded pose sample per output frame, without interpolated physics.",
            "The macro intentionally holds one recorded instant while its camera and focus move.",
            "Motion blur is disabled. Short pulse contacts cannot be resolved from this recording.",
            "The ultra-slow-motion air-jet clip requires a separate high-rate recording.",
        ],
    }
    if manifest_path.exists():
        record = json.loads(manifest_path.read_text())
        if record["contract"] != contract:
            raise RuntimeError("The existing render has different inputs. Use a new output directory.")
    else:
        write_json(manifest_path, record)
    bpy.ops.wm.open_mainfile(filepath=str(base / "scene.blend"))
    scene = bpy.context.scene
    configure_quality(scene, settings)
    scene.render.threads_mode = "FIXED"
    scene.render.threads = 8
    scene.render.fps = FPS
    scene.render.use_motion_blur = False
    scene.render.use_persistent_data = True
    scene.frame_start, scene.frame_end = 1, spec["frames"]
    beans = bpy.data.collections["RecordedBeans"]
    for obj in tuple(beans.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    prototypes = {kind: bpy.data.objects[f"bean_{kind}"] for kind in ("good", "black", "insect", "broken")}
    objects, _ = create_recorded_beans(payload, source_frames, prototypes, beans)
    camera = scene.camera
    camera.data.lens = spec.get("lens", camera.data.lens)
    shot = source_manifest["spec"]
    start = Vector(shot["camera"])
    target = Vector(shot["target"])
    end = start.copy()
    end_target = target.copy()
    if spec["motion"] == "lateral-reveal":
        end_shot = json.loads((args.base_dir / "02b-low-reveal-end/manifest.json").read_text())["spec"]
        end, end_target = Vector(end_shot["camera"]), Vector(end_shot["target"])
    elif spec["motion"] == "closing-slide":
        end += Vector((-0.23, -0.15, 0))
    elif spec["motion"] == "macro-focus":
        start += Vector((-0.0015, 0.001, 0))
        end += Vector((0.0015, -0.001, 0))
        camera.data.dof.aperture_fstop = 8

    completed = {f["frame"]: f for f in record["frames"]}
    rendered = 0
    for index, frame in enumerate(source_frames):
        number = index + 1
        path = frames_dir / f"frame_{number:04d}.png"
        if number in completed:
            if not path.exists() or sha(path) != completed[number]["png_sha256"]:
                raise RuntimeError(f"The recorded output frame is missing or changed: {path}")
            continue
        if path.exists():
            raise RuntimeError(f"An unverified frame exists: {path}. Use a new output directory.")
        scene.frame_set(number)
        position_error, rotation_error = apply_recorded_frame(frame, objects)
        progress = index / (spec["frames"] - 1)
        eased = progress * progress * (3 - 2 * progress)
        camera.location = start.lerp(end, eased)
        current_target = target.lerp(end_target, eased)
        point_at(camera, current_target)
        focus = current_target
        if spec["motion"] == "macro-focus":
            focus = objects[1783].location + Vector((-0.003, -0.003, 0.002)).lerp(Vector((0, 0.001, 0.0035)), eased)
        camera.data.dof.focus_distance = (camera.location - focus).length
        scene.render.filepath = str(path)
        tick = time.perf_counter()
        bpy.ops.render.render(write_still=True)
        record["frames"].append({
            "frame": number, "source_index": source_indices[index], "source_time": frame["t"],
            "active_beans": len(frame["beans"]) // 9,
            "pose_rows_sha256": hashlib.sha256(json.dumps(frame["beans"], separators=(",", ":")).encode()).hexdigest(),
            "camera": list(camera.location), "target": list(current_target),
            "focus_distance": camera.data.dof.focus_distance, "lens_mm": camera.data.lens,
            "max_position_error_m": position_error, "max_quaternion_component_error": rotation_error,
            "render_seconds": round(time.perf_counter() - tick, 3), "png_sha256": sha(path),
        })
        write_json(manifest_path, record)
        print(f"FRAME {name} {number}/{spec['frames']}", flush=True)
        rendered += 1
        if args.frame_limit is not None and rendered >= args.frame_limit:
            break
    assert sha(REPLAY) == contract["replay_sha256"]
    record["status"] = "frames-complete" if len(record["frames"]) == spec["frames"] else "rendering"
    write_json(manifest_path, record)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", choices=SEGMENTS)
    parser.add_argument("--base-dir", type=Path, default=PREVIEWS / "basic-scene-renders-v3")
    parser.add_argument("--output-dir", type=Path, default=PREVIEWS / "segments-v2")
    add_render_options(parser)
    parser.add_argument("--blender", default="blender")
    parser.add_argument("--worker", action="store_true")
    argv = sys.argv[sys.argv.index("--") + 1:] if "--worker" in sys.argv else sys.argv[1:]
    args = parser.parse_args(argv)
    args.base_dir, args.output_dir = args.base_dir.resolve(), args.output_dir.resolve()
    if render_settings(args)["samples"] < 1:
        parser.error("Samples must be positive.")
    if args.frame_limit is not None and args.frame_limit < 1:
        parser.error("The frame limit must be positive.")
    if args.worker:
        if not args.only:
            parser.error("Select one segment for each Blender process.")
        render(args.only, args)
        return 0
    for name in ([args.only] if args.only else SEGMENTS):
        output = args.output_dir / name
        manifest_path = output / "manifest.json"
        video = output / "preview.mp4"
        if video.exists():
            manifest = json.loads(manifest_path.read_text())
            if manifest.get("video", {}).get("sha256") != sha(video):
                raise RuntimeError(f"The existing video is unverified: {video}")
            if any(manifest["contract"].get(key) != render_settings(args)[key] for key in ("resolution", "samples")):
                raise RuntimeError("The existing video has different quality settings. Select a new output directory.")
            print(f"Preserving completed video: {name}", flush=True)
            continue
        output.mkdir(parents=True, exist_ok=True)
        command = [args.blender, "--background", "--threads", "8", "--python-exit-code", "1", "--python", str(Path(__file__).resolve()),
                   "--", "--worker", "--only", name, "--base-dir", str(args.base_dir), "--output-dir", str(args.output_dir), *worker_options(args)]
        with LOCK.open("a+") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                print("The shared render slot is busy. Completed frames remain available.", flush=True)
                return 75
            tick = time.perf_counter()
            with (output / "blender.log").open("a") as log:
                result = subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
            elapsed = round(time.perf_counter() - tick, 3)
        if result.returncode:
            print(f"Blender failed. Read {output / 'blender.log'}", flush=True)
            return result.returncode
        manifest = json.loads(manifest_path.read_text())
        manifest["process_seconds"] = manifest.get("process_seconds", 0) + elapsed
        manifest["blender_command"] = command
        write_json(manifest_path, manifest)
        if manifest["status"] != "frames-complete":
            print(f"FRAMES_READY {name} {len(manifest['frames'])}/{SEGMENTS[name]['frames']}", flush=True)
            continue
        encode = ["ffmpeg", "-n", "-threads", "2", "-framerate", str(FPS), "-i", str(output / "frames/frame_%04d.png"),
                  "-c:v", "libx264", "-threads", "2", "-preset", "medium", "-crf", str(render_settings(args)["crf"]), "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(video)]
        with (output / "ffmpeg.log").open("w") as log:
            subprocess.run(encode, stdout=log, stderr=subprocess.STDOUT, check=True)
        probe = json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0", "-show_entries",
            "stream=codec_name,width,height,r_frame_rate,nb_read_frames:format=duration", "-of", "json", str(video)], text=True))
        stream = probe["streams"][0]
        assert (stream["width"], stream["height"], stream["r_frame_rate"], int(stream["nb_read_frames"])) == (*render_settings(args)["resolution"], "30/1", SEGMENTS[name]["frames"])
        manifest["video"] = {"sha256": sha(video), "probe": probe, "encode_command": encode}
        manifest["status"] = "complete"
        write_json(manifest_path, manifest)
        print(f"VIDEO_READY {name} {video}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
