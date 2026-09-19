#!/usr/bin/env python3
"""Render the three approved demo preview stills with shared CPU locking."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[3]
RENDERER = ROOT / "sim/coffee_sorter/visual_assets/render_scene.py"
LOCK_PATH = Path("/private/tmp/hackspain-coffee-runtime.lock")
DEFAULT_OUTPUT = Path("/private/tmp/coffee-demo-video-previews")

SHOTS = {
    "machine-overview": {"shot": "hero", "look": "warm-roastery", "frame": 60},
    "inspection-close-up": {"shot": "inspection", "look": "noir-rim", "frame": 60},
    "discharge-air-jet": {"shot": "discharge", "look": "blueprint", "frame": 60},
}


def command_for(blender: str, output: Path, shot: dict[str, object]) -> list[str]:
    return [
        blender,
        "--background",
        "--threads",
        "8",
        "--python-exit-code",
        "1",
        "--python",
        str(RENDERER),
        "--",
        "--shot",
        str(shot["shot"]),
        "--look",
        str(shot["look"]),
        "--frame",
        str(shot["frame"]),
        "--preview",
        "--threads",
        "8",
        "--output-dir",
        str(output),
    ]


def render_one(blender: str, output: Path, shot: dict[str, object]) -> dict[str, object]:
    command = command_for(blender, output, shot)
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOCK_PATH.open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError(f"The render slot is busy: {LOCK_PATH}") from None

        started = time.perf_counter()
        result = subprocess.run(
            command,
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
        elapsed = round(time.perf_counter() - started, 3)
        fcntl.flock(lock, fcntl.LOCK_UN)

    output.mkdir(parents=True, exist_ok=True)
    (output / "blender.log").write_text(result.stdout)
    print(result.stdout, end="")
    if result.returncode:
        raise subprocess.CalledProcessError(result.returncode, command)
    return {
        "command": shlex.join(command),
        "elapsed_seconds": elapsed,
        "output_dir": str(output),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--blender", default=os.environ.get("BLENDER", "blender"))
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--only", choices=tuple(SHOTS))
    args = parser.parse_args()

    names = [args.only] if args.only else list(SHOTS)
    run = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "git_revision": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "lock_path": str(LOCK_PATH),
        "preview": True,
        "threads": 8,
        "shots": {},
    }

    try:
        for name in names:
            run["shots"][name] = render_one(
                args.blender,
                args.output_dir.resolve() / name,
                SHOTS[name],
            )
    except RuntimeError as error:
        print(str(error), file=sys.stderr)
        return 75

    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "run-manifest.json").write_text(json.dumps(run, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
