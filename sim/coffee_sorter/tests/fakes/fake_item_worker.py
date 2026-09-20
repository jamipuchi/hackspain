"""Fake generation and render worker driven by a scenario file.

Tests and the local demo inject failures with <item_jobs_root>/fake_scenario.json,
keyed by stage and attempt number. It never calls a provider and never starts
Blender. Outputs are tiny fixture files copied from tests/fixtures/. Production
never runs this worker, and a fake job can never reach activation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from item_jobs import (EXIT_CACHE_ENTRY_INVALID, EXIT_FAILED, EXIT_NOT_SUBMITTED, EXIT_OK,
                       EXIT_RENDER_LOCK, EXIT_UNCERTAIN)

SCENARIOS = ("ok", "fail_safe", "fail_hard", "uncertain", "lock_busy", "hang",
             "cache_entry_invalid")
PREVIEW_IMAGES = ("perspective.png", "top.png", "object.glb")
HANG_SECONDS = 60


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job-dir", type=Path, required=True)
    parser.add_argument("--stage", required=True)
    parser.add_argument("--attempt", type=int, required=True)
    parser.add_argument("--fixtures", type=Path, required=True)
    args = parser.parse_args()

    root = args.job_dir.parent.parent
    scenario = _scenario(root / "fake_scenario.json", args.stage, args.attempt,
                         _next_run(args.job_dir, args.stage))
    _record(root, args, "start", scenario)
    if scenario == "hang":
        return _hang()
    if scenario == "lock_busy":
        return _record(root, args, "end", scenario, EXIT_RENDER_LOCK)
    handler = _generation if args.stage == "generation" else _render
    return _record(root, args, "end", scenario, handler(args, scenario))


def _next_run(job_dir: Path, stage: str) -> int:
    """Count runs of one stage. A cache miss repeats an attempt number, a run never repeats."""
    path = job_dir / f"{stage}.runs"
    try:
        run = int(path.read_text().strip()) + 1
    except (OSError, ValueError):
        run = 1
    path.write_text(f"{run}\n")
    return run


def _scenario(path: Path, stage: str, attempt: int, run: int) -> str:
    """A list is keyed by run order. An object is keyed by attempt number."""
    try:
        table = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return "ok"
    entry = table.get(stage)
    if isinstance(entry, list):
        value = entry[min(run, len(entry)) - 1] if entry else "ok"
    elif isinstance(entry, dict):
        value = entry.get(str(attempt), "ok")
    else:
        value = "ok"
    return value if value in SCENARIOS else "ok"


def _generation(args, scenario: str) -> int:
    if scenario == "fail_safe":
        # A cache miss stops before submission, exactly like cached mode.
        _status(args, "not_submitted", "fake cache miss")
        return EXIT_NOT_SUBMITTED
    if scenario == "fail_hard":
        # A failure proven to precede submission. The one automatic retry applies.
        _status(args, "not_submitted", "fake generator failure before submission")
        return EXIT_FAILED
    if scenario == "uncertain":
        _status(args, "uncertain", "fake uncertain provider result")
        return EXIT_UNCERTAIN
    if scenario == "cache_entry_invalid":
        _status(args, "not_submitted", "fake cache entry verification failure")
        return EXIT_CACHE_ENTRY_INVALID
    (args.job_dir / "recipe.json").write_bytes((args.fixtures / "recipe.json").read_bytes())
    _status(args, "not_submitted", None, cache_hit=True)
    return EXIT_OK


def _render(args, scenario: str) -> int:
    if scenario in ("fail_safe", "fail_hard", "uncertain", "cache_entry_invalid"):
        return EXIT_FAILED
    previews = args.job_dir / "previews"
    previews.mkdir(parents=True, exist_ok=True)
    for name in PREVIEW_IMAGES:
        (previews / name).write_bytes((args.fixtures / "previews" / name).read_bytes())
    record = json.loads((args.fixtures / "previews" / "render.json").read_text())
    record["recipe_sha256"] = hashlib.sha256(
        (args.job_dir / "recipe.json").read_bytes()).hexdigest()
    (previews / "render.json").write_text(json.dumps(record, indent=2) + "\n")
    return EXIT_OK


def _status(args, submission: str, reason: str | None, cache_hit: bool = False) -> None:
    (args.job_dir / "provider_status.json").write_text(json.dumps({
        "provider_submission": submission, "reason": reason, "mode": "fake",
        "live_requested": False, "cache_hit": cache_hit, "evidence": None,
    }, indent=2, sort_keys=True) + "\n")


def _hang() -> int:
    # A grandchild in the same process group proves that the whole group is killed.
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    subprocess.Popen([sys.executable, "-c",
                      "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN);"
                      f" time.sleep({HANG_SECONDS})"])
    time.sleep(HANG_SECONDS)
    return EXIT_OK


def _record(root: Path, args, event: str, scenario: str, code: int = EXIT_OK) -> int:
    line = json.dumps({"event": event, "stage": args.stage, "attempt": args.attempt,
                       "scenario": scenario, "pid": os.getpid(), "at": time.time(),
                       "request_id": args.job_dir.name}) + "\n"
    with (root / "worker_runs.jsonl").open("a") as handle:
        handle.write(line)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
