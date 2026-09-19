"""Render available recipes sequentially while holding the shared runtime lock."""

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

HERE = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="*")
    parser.add_argument("--case", default="*")
    args = parser.parse_args()
    renderer = HERE / "render_recipe.py"
    renderer_hash = hashlib.sha256(renderer.read_bytes()).hexdigest()
    for recipe in sorted((HERE / "results").glob(f"{args.model}/{args.case}/recipe.json")):
        out = recipe.parent / "render"
        if (out / "render.json").exists():
            previous = json.loads((out / "render.json").read_text())
            if previous.get("renderer_sha256") != renderer_hash:
                raise SystemExit(f"Renderer changed. Preserve prior render before repeating: {out}")
            if previous.get("recipe_sha256") != hashlib.sha256(recipe.read_bytes()).hexdigest():
                raise SystemExit(f"Recipe changed. Preserve prior render before repeating: {out}")
            print(f"Cached render: {recipe.parent}", flush=True)
            continue
        with open("/private/tmp/hackspain-coffee-runtime.lock", "a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                print("Runtime lock is occupied. No Blender process started.", flush=True)
                raise SystemExit(75)
            out.mkdir(exist_ok=True)
            env = {**os.environ, **{key: "1" for key in (
                "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS")}}
            command = ["/opt/homebrew/bin/blender", "--background", "--threads", "1",
                       "--python-exit-code", "1", "--python", str(renderer), "--",
                       "--recipe", str(recipe), "--out", str(out)]
            started = time.monotonic()
            load = os.getloadavg()
            print(f"Rendering: {recipe.parent}", flush=True)
            with (out / "blender.log").open("w") as log:
                result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, env=env, timeout=600)
            record = {"returncode": result.returncode, "subprocess_wall_seconds": time.monotonic() - started,
                      "host_load_before": load, "host_load_after": os.getloadavg(),
                      "command": command, "thread_environment": {key: env[key] for key in (
                          "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS")},
                      "exclusive_lock": "/private/tmp/hackspain-coffee-runtime.lock"}
            (out / "execution.json").write_text(json.dumps(record, indent=2) + "\n")
            print(json.dumps({"case": str(recipe.parent.relative_to(HERE)),
                              "returncode": result.returncode, "wall_s": record["subprocess_wall_seconds"]}), flush=True)
            if result.returncode:
                raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
