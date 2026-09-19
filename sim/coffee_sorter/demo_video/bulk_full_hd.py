#!/usr/bin/env python3
"""Render and upload all approved Full HD clips or a selected subset.

When: Run the approved batch on the isolated HackSpain render worker.
Env: Blender 5.2.2, ffmpeg, ffprobe, agent-fs, and a private agent-fs JSON file.
Example: python3 sim/coffee_sorter/demo_video/bulk_full_hd.py --credentials /run/secrets/agent-fs.json
Local subset: --device METAL --only 07c-normal-closing --only 07b-blueprint-closing --source-bundles
Selected subsets do not create the final nine-clip ZIP.
Output: One PASS/FAIL line and a /tmp log path. --json emits a compact result.
Generated/maintained via the script-builder skill.
"""
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
import zipfile

from render_segments import PREVIEWS, ROOT, SEGMENTS, write_json

NAMES = list(SEGMENTS)
NAMES.insert(5, "05-ultra-slowmo")
PREFIX = "qa/hackspain/2026-09-19-coffee-demo/"
CONTEXT = ["--org", "9d0f4b46-6113-49f7-8e8c-d315a64bd59d", "--drive", "ad84339c-9d70-462a-84cf-b58aba031ac5"]


def digest(data):
    return hashlib.sha256(data).hexdigest()


def source_parts(directory, members, limit=40 * 1024 * 1024):
    """Keep each native-frame archive below the agent-fs request limit."""
    groups, group, size = [], [], 0
    for member in members:
        member_size = (directory / member).stat().st_size + 1024
        if member_size > limit:
            raise RuntimeError(f"A source member exceeds the archive limit: {member}")
        if group and size + member_size > limit:
            groups.append(group)
            group, size = [], 0
        group.append(member)
        size += member_size
    if group:
        groups.append(group)
    parts = []
    for number, group in enumerate(groups, 1):
        archive = directory / f"native-frames-{number:03d}.zip"
        if not archive.exists():
            temporary = archive.with_suffix(".zip.tmp")
            with zipfile.ZipFile(temporary, "w", zipfile.ZIP_STORED) as bundle:
                for member in group:
                    bundle.write(directory / member, member)
            temporary.replace(archive)
        with zipfile.ZipFile(archive) as bundle:
            if bundle.testzip() is not None or sorted(bundle.namelist()) != sorted(group):
                raise RuntimeError("Native archive integrity verification failed.")
            for member in group:
                if bundle.read(member) != (directory / member).read_bytes():
                    raise RuntimeError(f"Native archive contents differ: {member}")
        if archive.stat().st_size > limit:
            raise RuntimeError("The native archive exceeds the upload limit.")
        parts.append((archive, group))
    return parts


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--credentials", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=PREVIEWS / "segments-1080p")
    parser.add_argument("--device", choices=("CPU", "METAL"), default="CPU")
    parser.add_argument("--only", choices=NAMES, action="append", help="Render selected clips in this order. Repeat for each clip.")
    parser.add_argument("--source-bundles", action="store_true", help="Upload verified native frames and metadata for another render worker.")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    names = args.only or NAMES
    if len(names) != len(set(names)):
        parser.error("Select each clip only once.")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    log_path = Path("/tmp") / ("bulk-full-hd-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + ".log")
    state_path = args.output_dir / "batch.json"
    state = {"status": "running", "completed": [], "current": None, "log": str(log_path)}
    credentials = json.loads(args.credentials.read_text())
    env = {**os.environ, **{key: credentials[key] for key in ("AGENT_FS_API_URL", "AGENT_FS_API_KEY")}}
    secret = env["AGENT_FS_API_KEY"]

    with (args.output_dir / "batch.lock").open("a+") as batch_lock, log_path.open("a", buffering=1) as log:
        def note(message):
            message = str(message).replace(secret, "[redacted]")
            log.write(message + "\n")
            if args.verbose:
                print(message, flush=True)

        def run(command, upload=False):
            result = subprocess.run(command, cwd=ROOT, env=env if upload else None, capture_output=True)
            if result.returncode:
                note(result.stderr.decode(errors="replace"))
                raise RuntimeError(f"{command[0]} failed with exit {result.returncode}.")
            return result.stdout

        def upload(path, remote):
            data = path.read_bytes()
            expected = digest(data)
            listed = json.loads(run(["agent-fs", *CONTEXT, "ls", remote.rsplit("/", 1)[0] + "/", "--json"], True))
            if isinstance(listed, str):
                listed = json.loads(listed)
            exists = any(entry["name"] == remote.rsplit("/", 1)[1] for entry in listed["entries"])
            if exists:
                downloaded = run(["agent-fs", *CONTEXT, "download", remote], True)
                if digest(downloaded) == expected:
                    note(f"Preserved verified upload: {remote}")
                    return {"path": remote, "sha256": expected, "bytes": len(data)}
                raise RuntimeError(f"The remote file has different bytes: {remote}")
            response = json.loads(run(["agent-fs", *CONTEXT, "write", remote, "--file", str(path), "-m", "Verified native Full HD coffee clip", "--json"], True))
            if isinstance(response, str):
                response = json.loads(response)
            if response["contentHash"] != expected or response["size"] != len(data):
                raise RuntimeError(f"Upload verification failed: {remote}")
            if run(["agent-fs", *CONTEXT, "download", remote], True) != data:
                raise RuntimeError(f"Downloaded bytes differ: {remote}")
            return {"path": remote, "sha256": expected, "bytes": len(data)}

        try:
            fcntl.flock(batch_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            message = "Another batch holds the output lock. Its state remains unchanged."
            note(message)
            output = {"status": "fail", "log": str(log_path), "summary": message}
            print(json.dumps(output) if args.json else f"FAIL: {message} (log: {log_path})")
            return 1

        try:
            for name in names:
                state["current"] = name
                write_json(state_path, state)
                slowmo = name == "05-ultra-slowmo"
                directory = args.output_dir / name
                command = [sys.executable, str(Path(__file__).with_name("render_slowmo.py" if slowmo else "render_segments.py")),
                           "--full-hd", "--device", args.device, "--output-dir", str(directory if slowmo else args.output_dir)]
                if not slowmo:
                    command += ["--only", name]
                note(run(command).decode())
                manifest = json.loads((directory / "manifest.json").read_text())
                expected_frames = 120 if slowmo else SEGMENTS[name]["frames"]
                if manifest["status"] != "complete" or len(manifest["frames"]) != expected_frames:
                    raise RuntimeError(f"Incomplete clip: {name}")
                for frame in manifest["frames"]:
                    data = (directory / "frames" / f"frame_{frame['frame']:04d}.png").read_bytes()
                    if digest(data) != frame["png_sha256"] or tuple(int.from_bytes(data[n:n + 4], "big") for n in (16, 20)) != (1920, 1080):
                        raise RuntimeError(f"Native frame verification failed: {name}")
                video = directory / "preview.mp4"
                probe = json.loads(run(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0", "-show_entries",
                                       "stream=width,height,r_frame_rate,nb_read_frames", "-of", "json", str(video)]))["streams"][0]
                if (probe["width"], probe["height"], probe["r_frame_rate"], int(probe["nb_read_frames"])) != (1920, 1080, "30/1", expected_frames):
                    raise RuntimeError(f"Video verification failed: {name}")
                if digest(video.read_bytes()) != manifest["video"]["sha256"]:
                    raise RuntimeError(f"Video hash verification failed: {name}")
                receipt = upload(video, PREFIX + f"full-hd/{name}-1080p.mp4")
                upload(directory / "manifest.json", PREFIX + f"full-hd/manifests/{name}.json")
                if args.source_bundles:
                    members = ["manifest.json", "preview.mp4", *[
                        f"frames/frame_{frame['frame']:04d}.png" for frame in manifest["frames"]]]
                    source_receipts = []
                    for archive, group in source_parts(directory, members):
                        source_receipts.append({**upload(archive, PREFIX + f"full-hd/sources/{name}/{archive.name}"), "members": group})
                    ready = directory / "source-ready.json"
                    expected_ready = {"schema_version": 1, "name": name, "manifest_sha256": digest((directory / "manifest.json").read_bytes()),
                                      "video_sha256": receipt["sha256"], "parts": source_receipts}
                    if ready.exists() and json.loads(ready.read_text()) != expected_ready:
                        raise RuntimeError(f"The existing source index has different contents: {name}")
                    write_json(ready, expected_ready)
                    receipt["source_bundle"] = upload(ready, PREFIX + f"full-hd/sources/{name}/ready.json")
                state["completed"].append({"name": name, **receipt})
                write_json(state_path, state)
                note(f"Verified and uploaded: {name}")

            if args.only:
                state["status"], state["current"] = "complete", None
                write_json(state_path, state)
                message = f"{len(names)} selected Full HD clips are verified in Swarm. The final ZIP remains with the main worker."
                output = {"status": "pass", "log": str(log_path), "summary": message}
                print(json.dumps(output) if args.json else f"PASS: {message} (log: {log_path})")
                return 0

            archive = PREVIEWS / "coffee-demo-clips-1080p.zip"
            if not archive.exists():
                temporary = archive.with_suffix(".zip.tmp")
                with zipfile.ZipFile(temporary, "w", zipfile.ZIP_STORED) as bundle:
                    for name in NAMES:
                        bundle.write(args.output_dir / name / "preview.mp4", f"{name}-1080p.mp4")
                temporary.replace(archive)
            with zipfile.ZipFile(archive) as bundle:
                if bundle.testzip() is not None or set(bundle.namelist()) != {f"{name}-1080p.mp4" for name in NAMES}:
                    raise RuntimeError("ZIP integrity verification failed.")
                for name in NAMES:
                    if bundle.read(f"{name}-1080p.mp4") != (args.output_dir / name / "preview.mp4").read_bytes():
                        raise RuntimeError("ZIP contents differ from the verified clips.")
            state["archive"] = upload(archive, PREFIX + archive.name)
            state["status"], state["current"] = "complete", None
            write_json(state_path, state)
            result, message, code = "pass", "Nine Full HD clips and the ZIP are verified in Swarm.", 0
        except Exception as error:
            message = str(error).replace(secret, "[redacted]")
            state.update(status="failed", error=message)
            write_json(state_path, state)
            note(message)
            result, code = "fail", 1
        output = {"status": result, "log": str(log_path), "summary": message}
        print(json.dumps(output) if args.json else f"{result.upper()}: {message} (log: {log_path})")
        return code


if __name__ == "__main__":
    raise SystemExit(main())
