#!/usr/bin/env python3
"""Render and encode the first coffee demo segment for review."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[3]
BLENDER_SCRIPT = Path(__file__).resolve().with_name("intro_segment_scene.py")
LOCK_PATH = Path("/private/tmp/hackspain-coffee-runtime.lock")
DEFAULT_OUTPUT = Path(
    "/private/tmp/coffee-demo-video-previews/segments/01-machine-overview-v1"
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run(command: list[str], *, cwd: Path) -> tuple[subprocess.CompletedProcess[str], float]:
    started = time.perf_counter()
    result = subprocess.run(
        command,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    return result, round(time.perf_counter() - started, 3)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--blender", default=os.environ.get("BLENDER", "blender"))
    parser.add_argument("--ffmpeg", default=os.environ.get("FFMPEG", "ffmpeg"))
    parser.add_argument("--ffprobe", default=os.environ.get("FFPROBE", "ffprobe"))
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    output = args.output_dir.resolve()
    frames = output / "frames"
    if frames.exists() and any(frames.glob("frame_*.png")):
        print("The output directory contains rendered frames. Use a new directory.", file=sys.stderr)
        return 2
    output.mkdir(parents=True, exist_ok=True)

    blender_command = [
        args.blender,
        "--background",
        "--threads",
        "8",
        "--python-exit-code",
        "1",
        "--python",
        str(BLENDER_SCRIPT),
        "--",
        "--threads",
        "8",
        "--samples",
        "12",
        "--output-dir",
        str(output),
    ]
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOCK_PATH.open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(f"The render slot is busy: {LOCK_PATH}", file=sys.stderr)
            return 75
        blender_result, blender_seconds = run(blender_command, cwd=ROOT)
        fcntl.flock(lock, fcntl.LOCK_UN)

    (output / "blender.log").write_text(blender_result.stdout)
    print(blender_result.stdout, end="")
    if blender_result.returncode:
        print("Blender did not complete the segment.", file=sys.stderr)
        return blender_result.returncode

    video = output / "01-machine-overview-preview.mp4"
    ffmpeg_command = [
        args.ffmpeg,
        "-y",
        "-framerate",
        "24",
        "-i",
        str(frames / "frame_%04d.png"),
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "20",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(video),
    ]
    ffmpeg_result, encode_seconds = run(ffmpeg_command, cwd=ROOT)
    (output / "ffmpeg.log").write_text(ffmpeg_result.stdout)
    if ffmpeg_result.returncode:
        print(ffmpeg_result.stdout, file=sys.stderr)
        print("FFmpeg did not encode the segment.", file=sys.stderr)
        return ffmpeg_result.returncode

    ffprobe_command = [
        args.ffprobe,
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=codec_name,width,height,r_frame_rate,nb_frames:format=duration,size",
        "-of",
        "json",
        str(video),
    ]
    probe_result, _ = run(ffprobe_command, cwd=ROOT)
    if probe_result.returncode:
        print(probe_result.stdout, file=sys.stderr)
        print("FFprobe did not verify the segment.", file=sys.stderr)
        return probe_result.returncode

    manifest_path = output / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["driver"] = {
        "blender_command": shlex.join(blender_command),
        "blender_process_seconds": blender_seconds,
        "ffmpeg_command": shlex.join(ffmpeg_command),
        "encode_seconds": encode_seconds,
    }
    manifest["video"] = {
        "path": str(video),
        "sha256": sha256(video),
        "probe": json.loads(probe_result.stdout),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps({"video": str(video), "sha256": manifest["video"]["sha256"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
