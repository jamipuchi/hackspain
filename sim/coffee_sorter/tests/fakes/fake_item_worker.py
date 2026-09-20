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
from item_jobs import (EXIT_CACHE_ENTRY_INVALID, EXIT_RESPONSE_RECEIVED, EXIT_FAILED,
                       EXIT_NOT_SUBMITTED, EXIT_OK, EXIT_RENDER_LOCK, EXIT_UNCERTAIN)

SCENARIOS = ("ok", "fail_safe", "fail_hard", "uncertain", "lock_busy", "hang",
             "cache_entry_invalid", "answer_unsaved", "answer_unsaved_without_status",
             "hard_gate")
PREVIEW_IMAGES = ("perspective.png", "top.png", "object.glb")
HANG_SECONDS = 60
# The recorded star render bounds, so the fake render reports measured-looking numbers.
STAR_BOUNDS_MM = (16.94960594177246, 16.120851516723635, 2.0)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job-dir", type=Path, required=True)
    parser.add_argument("--stage", required=True)
    parser.add_argument("--attempt", type=int, required=True)
    parser.add_argument("--fixtures", type=Path, required=True)
    # Only the trainer command ever carries this flag. Any other stage would refuse it.
    parser.add_argument("--quality-gate", choices=("strict", "demo"), default=None)
    args = parser.parse_args()
    if args.quality_gate is not None and args.stage != "training":
        parser.error("--quality-gate belongs to the trainer only")

    root = args.job_dir.parent.parent
    scenario = _scenario(root / "fake_scenario.json", args.stage, args.attempt,
                         _next_run(args.job_dir, args.stage))
    _record(root, args, "start", scenario)
    if scenario == "hang":
        return _hang()
    if scenario == "lock_busy":
        return _record(root, args, "end", scenario, EXIT_RENDER_LOCK)
    handlers = {"generation": _generation, "render": _render,
                "physics_proposal": _physics_proposal, "physics": _physics,
                "training": _training}
    return _record(root, args, "end", scenario, handlers[args.stage](args, scenario))


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
    # A real render describes the recipe it rendered and reports the measured bounds, so
    # this fake completes the same record. The bounds are the recorded star values.
    recipe_bytes = (args.job_dir / "recipe.json").read_bytes()
    record["recipe_sha256"] = hashlib.sha256(recipe_bytes).hexdigest()
    record["recipe_name"] = json.loads(recipe_bytes).get("name")
    record.setdefault("bounding_dimensions_mm", list(STAR_BOUNDS_MM))
    record.setdefault("mesh_counts", {"objects": 1, "vertices": 140, "polygons": 132})
    (previews / "render.json").write_text(json.dumps(record, indent=2) + "\n")
    return EXIT_OK


def _physics_proposal(args, scenario: str) -> int:
    """The provider rules of generation, applied to the physics description."""
    if scenario == "fail_safe":
        _status(args, "not_submitted", "fake physics cache miss")
        return EXIT_NOT_SUBMITTED
    if scenario == "fail_hard":
        _status(args, "not_submitted", "fake physics failure before submission")
        return EXIT_FAILED
    if scenario == "uncertain":
        _status(args, "uncertain", "fake uncertain provider result")
        return EXIT_UNCERTAIN
    if scenario == "cache_entry_invalid":
        _status(args, "not_submitted", "fake physics cache verification failure")
        return EXIT_CACHE_ENTRY_INVALID
    if scenario == "answer_unsaved":
        _status(args, "completed", "fake answer confirmed, the cache write failed")
        return EXIT_RESPONSE_RECEIVED
    if scenario == "answer_unsaved_without_status":
        # The status write itself is what failed, so the exit code is the only evidence.
        return EXIT_RESPONSE_RECEIVED
    # Build the draft the real adapter builds, from THIS job's own artifacts, so the uri
    # stays relative to the job asset root and every hash binds to the rendered GLB.
    from object_definitions import build_object_definition

    definition = build_object_definition(
        description="A small five-point star token.",
        recipe_path=args.job_dir / "recipe.json",
        render_metadata_path=args.job_dir / "previews" / "render.json",
        glb_path=args.job_dir / "previews" / "object.glb",
        visual_uri="previews/object.glb",
        object_key="star_token",
        physics_proposal={
            "shape": "box",
            "dimensions_m": [0.016, 0.012, 0.002],
            "density_kg_m3": 1200.0,
            "material_assumption": "Assumed density of a light decorative token.",
            "limitations": "Density and contact geometry are unmeasured proxy estimates.",
        },
        sorting_proposal={"class_name": "star_token", "defect": False, "severity": "none",
                          "proposed_action": "keep"},
    )
    (args.job_dir / "definition.json").write_text(json.dumps(definition, indent=2) + "\n")
    (args.job_dir / "physics.json").write_text(json.dumps({
        "physics_source": "fake", "physics_measurement_status": "unmeasured_proxy_estimate",
    }, indent=2, sort_keys=True) + "\n")
    _status(args, "not_submitted", None, cache_hit=True)
    return EXIT_OK


def _physics(args, scenario: str) -> int:
    """Write the route verdict the real validator would write."""
    verdict = "physics_unsupported" if scenario in ("fail_safe", "fail_hard") else "accept"
    reason = "route_not_accepted" if verdict != "accept" else None
    if scenario == "uncertain":
        return EXIT_FAILED
    out = args.job_dir / "physics"
    out.mkdir(parents=True, exist_ok=True)
    (out / "result.json").write_text(json.dumps({
        "verdict": verdict, "reason": reason, "detail": None, "seed": 8,
        "trials": [], "load": None, "estimate_basis": "unmeasured_proxy_estimate",
        "glb_checked": True,
    }, indent=2, sort_keys=True) + "\n")
    return EXIT_OK


def _training(args, scenario: str) -> int:
    """Write the trainer verdict.

    `fail_safe` writes a COMPLETE record that FAILS the gate, so the queue reports a
    candidate validation failure. `fail_hard` and `uncertain` exit non-zero, so the queue
    reports a training failure. Every written record carries the full evidence a real
    trainer writes: a partial record is a different defect that the queue refuses itself.
    """
    if scenario in ("uncertain", "fail_hard"):
        return EXIT_FAILED
    out = args.job_dir / "training" / "out"
    out.mkdir(parents=True, exist_ok=True)
    # `fail_safe` misses a QUALITY code and `hard_gate` misses a HARD one. The demo gate
    # moves only the quality code to the warnings, exactly as the real trainer does.
    failures = {"fail_safe": ["anomaly_fraction"], "hard_gate": ["new_label_recall"]}.get(
        scenario, [])
    demo = {}
    if args.quality_gate == "demo":
        warnings = [code for code in failures if code == "anomaly_fraction"]
        failures = [code for code in failures if code not in warnings]
        demo = {"quality_gate": "demo", "quality_warnings": warnings,
                "review_status": "needs_review" if warnings else None}
    passed = not failures
    (out / "validation.json").write_text(json.dumps({
        **demo,
        "passed": passed,
        "failures": failures,
        "labels": ["star_token", "good"],
        "new_label": "star_token",
        "label_order_ok": True,
        "classifier": {"holdout_accuracy": 0.97, "new_label_recall": 1.0},
        "anomaly": {"fraction_above_threshold": 1.0 if scenario == "fail_safe" else 0.0,
                    "threshold": 14.339},
        "policy": {"applied_reject_classes": [], "new_label_policy": "keep",
                   "policy_source": "baseline_file"},
        "pulses": {"runs": [{"seed": 8, "commanded": 0}]},
        "keep_outcome": {"runs": [{"seed": 8, "resolved": 34, "accepted": 34,
                                   "accept_fraction": 1.0}]},
        "preset_compatibility": {"loaded": True, "reason": None},
    }, indent=2, sort_keys=True) + "\n")
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
                       "quality_gate": args.quality_gate,
                       "scenario": scenario, "pid": os.getpid(), "at": time.time(),
                       "request_id": args.job_dir.name}) + "\n"
    with (root / "worker_runs.jsonl").open("a") as handle:
        handle.write(line)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
