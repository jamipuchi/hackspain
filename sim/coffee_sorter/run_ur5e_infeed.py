"""CLI for the bounded UR5e infeed picking evidence run."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import time
from pathlib import Path

from ur5e_infeed import _verify_source_identity, run_scenario, scenarios, write_summary


def _reserve_output(output: Path, rerun: bool) -> Path | None:
    output = output.resolve()
    archive: Path | None = None
    if output.exists() and any(output.iterdir()):
        if not rerun:
            raise FileExistsError(
                f"refusing nonempty output {output}; pass --rerun to archive it"
            )
        cache = Path(
            os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))
        ) / "coffee-sorter" / "ur5e-infeed-archives"
        cache.mkdir(parents=True, exist_ok=True)
        archive = cache / f"{output.name}-{time.time_ns()}"
        output.rename(archive)
    elif output.exists():
        output.rmdir()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.mkdir(exist_ok=False)
    return archive


def _write_completion(output: Path, scenario_names: list[str]) -> None:
    artifacts = {
        path.relative_to(output).as_posix(): {
            "bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        for path in sorted(output.rglob("*"))
        if path.is_file() and path.name != "completion.json"
    }
    completion = {
        "complete": True,
        "scenarios": scenario_names,
        "source_files_sha256": _verify_source_identity(),
        "artifacts": artifacts,
    }
    temporary = output / ".completion.json.tmp"
    temporary.write_text(
        json.dumps(completion, indent=2, sort_keys=True, allow_nan=False) + "\n"
    )
    temporary.replace(output / "completion.json")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--menagerie-dir", type=Path, required=True, help="pinned mujoco_menagerie checkout")
    parser.add_argument("--output", type=Path, default=Path("runs/ur5e-infeed"))
    parser.add_argument("--scenario", choices=("all", *scenarios()), default="all")
    parser.add_argument("--no-render", action="store_true")
    parser.add_argument("--rerun", action="store_true", help="archive an existing suite before running fresh")
    parser.add_argument("--tests-log", type=Path, help="completed test log to bind into the suite")
    args = parser.parse_args()
    if args.tests_log is not None and not args.tests_log.is_file():
        parser.error(f"tests log does not exist: {args.tests_log}")
    try:
        archive = _reserve_output(args.output, args.rerun)
    except (FileExistsError, OSError) as error:
        parser.error(str(error))
    if archive is not None:
        print(f"archived previous suite: {archive}")
    selected = scenarios().values() if args.scenario == "all" else (scenarios()[args.scenario],)
    results = [run_scenario(scenario, args.menagerie_dir / "universal_robots_ur5e", args.output / scenario.name, render=not args.no_render) for scenario in selected]
    if args.scenario == "all":
        write_summary(args.output, results)
    if args.tests_log is not None:
        shutil.copyfile(args.tests_log, args.output / "tests.log")
    _write_completion(args.output, [result["scenario"] for result in results])
    for result in results:
        print(result["scenario"], result["outcomes"], f"success={result['large_debris_success_rate']:.1%}")


if __name__ == "__main__":
    main()
