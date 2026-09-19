"""Run the bounded five-model comparison, retaining every case failure."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
MODELS = {
    "flash-lite": "google/gemini-2.5-flash-lite",
    "deepseek": "deepseek/deepseek-v4.1-flash",
    "glm": "z-ai/glm-5.3",
    "qwen": "qwen/qwen3.8-max-0902",
    "gemini": "google/gemini-3.8-flash",
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", required=True)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    env = {**os.environ, **{k: "1" for k in (
        "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS")}}
    for name, model in MODELS.items():
        out = HERE / "results" / name
        out.mkdir(parents=True, exist_ok=True)
        for case in ("earring", "star", "logo"):
            command = [sys.executable, str(HERE / "probe.py"), "--env-file", args.env_file,
                       "--out", str(out), "--case", case, "--model", model]
            if args.live:
                command.append("--live")
            started = time.monotonic()
            result = subprocess.run(command, text=True, capture_output=True, env=env)
            record = {"case": case, "model": model, "returncode": result.returncode,
                      "wall_seconds": time.monotonic() - started,
                      "stdout": result.stdout, "stderr": result.stderr}
            path = out / case / "execution.json"
            path.parent.mkdir(exist_ok=True)
            # Preserve the previous execution when an intentional rerun reads the cache.
            if path.exists():
                path = path.with_name(f"execution-{time.time_ns()}.json")
            path.write_text(json.dumps(record, indent=2) + "\n")
            print(json.dumps({k: record[k] for k in ("case", "model", "returncode", "wall_seconds")}), flush=True)


if __name__ == "__main__":
    main()
