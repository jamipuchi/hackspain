"""Render one item job recipe through the shared render suite.

Locking stays in one layer. render_suite.py takes the shared runtime lock and
exits with the lock-busy code when it is busy. This script never takes that lock
and holds no lock path of its own: it receives one and passes it down. It stages
the job recipe into the layout that render_suite.py globs, propagates the
lock-busy code unchanged, and copies the validated preview files into
<job-dir>/previews/.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from item_jobs import EXIT_FAILED, EXIT_OK, PREVIEW_FILES

MODEL_FOLDER = "item"
CASE_FOLDER = "job"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--render-suite", type=Path, required=True)
    parser.add_argument("--job-dir", type=Path, required=True)
    parser.add_argument("--runtime-lock", type=Path, required=True)
    parser.add_argument("--blender", default="blender")
    args = parser.parse_args()

    recipe = args.job_dir / "recipe.json"
    if not recipe.is_file():
        sys.stderr.write("the job has no recipe to render\n")
        return EXIT_FAILED
    work = args.job_dir / "render_work"
    case = work / MODEL_FOLDER / CASE_FOLDER
    try:
        case.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(recipe, case / "recipe.json")
        result = subprocess.run([sys.executable, str(args.render_suite),
                                 "--results-root", str(work), "--model", MODEL_FOLDER,
                                 "--case", CASE_FOLDER, "--blender", args.blender,
                                 "--lock", str(args.runtime_lock)])
        if result.returncode:
            # The lock-busy code keeps its meaning: no attempt was spent.
            return result.returncode

        rendered = case / "render"
        previews = args.job_dir / "previews"
        previews.mkdir(parents=True, exist_ok=True)
        for name in PREVIEW_FILES:
            source = rendered / name
            if not source.is_file():
                sys.stderr.write(f"the render produced no {name}\n")
                return EXIT_FAILED
            shutil.copyfile(source, previews / name)
        return EXIT_OK
    finally:
        # Staging never survives an attempt, including the lock-busy path.
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
