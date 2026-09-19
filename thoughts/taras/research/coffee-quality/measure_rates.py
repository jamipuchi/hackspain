"""Compare fixed coffee sorter development runs at bounded feed rates."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import tempfile
import time


ROOT = Path(__file__).resolve().parents[4]
SIM_DIR = ROOT / "sim" / "coffee_sorter"
DEFAULT_PRESET = SIM_DIR / "configs" / "default_demo.json"
RATES = (10, 50, 100, 200, 250, 400, 500)
SEED = 8
SECONDS = 6.0
MAX_WALL_SECONDS = 300.0
TOLERANCE = 1e-9

sys.path.insert(0, str(SIM_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import evaluate_frozen as frozen  # noqa: E402
from engine import Engine  # noqa: E402


frozen.SIMULATION_COMMANDS += (
    "coffee-quality/measure_rates.py",
)


def write_json(path: Path, value, *, exclusive: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x" if exclusive else "w") as output:
        json.dump(value, output, indent=2, sort_keys=True, allow_nan=False)
        output.write("\n")


def write_markdown(path: Path, text: str) -> None:
    with path.open("x") as output:
        output.write(text)


def closed_cohort(report: dict) -> tuple[list[dict], dict]:
    raw = frozen.counts(report)
    end = float(report["runtime"]["simulation_seconds"]) - 0.6
    cohort = [row for row in report["objects"]
              if 0.8 <= float(row["spawn_time_s"]) <= end]
    return cohort, raw


def classification(cohort: list[dict]) -> dict:
    evaluated = []
    correct = 0
    for row in cohort:
        classes = {decision["predicted_class"] for decision in row["predictions"]}
        if len(classes) != 1:
            continue
        predicted = next(iter(classes))
        evaluated.append(row)
        correct += predicted == row["truth_class"]
    return {
        "correct": correct,
        "evaluated": len(evaluated),
        "accuracy": correct / len(evaluated) if evaluated else None,
        "coverage": len(evaluated) / len(cohort) if cohort else None,
        "association_note": (
            "Approximate associated predictions only. Objects with no associated prediction "
            "or multiple predicted classes are excluded."
        ),
    }


def metrics(report: dict) -> dict:
    cohort, raw = closed_cohort(report)
    physical_correct = (
        raw["captured_required_defects"]
        + sum(row["outcome"] == "accept" for row in cohort if not row["required_reject"])
    )
    classification_result = classification(cohort)
    return {
        "cohort": {
            "spawn_window_s": [0.8, float(report["runtime"]["simulation_seconds"]) - 0.6],
            "inclusive": True,
            "spills_in_denominators": True,
            "unresolved_in_denominators": True,
            **raw,
        },
        "capture": {
            "count": [raw["captured_required_defects"], raw["required_defects"]],
            "interval_95": frozen.wilson(raw["captured_required_defects"], raw["required_defects"]),
        },
        "good_loss": {
            "count": [raw["good_objects_lost"], raw["keep_objects"]],
            "interval_95": frozen.wilson(raw["good_objects_lost"], raw["keep_objects"]),
        },
        "physical_sorting": {
            "correct": physical_correct,
            "eligible": raw["eligible_objects"],
            "accuracy": physical_correct / raw["eligible_objects"] if raw["eligible_objects"] else None,
            "definition": "Required defects captured plus keep objects accepted, divided by eligible objects.",
        },
        "object_classification": classification_result,
        "late_decisions": sum(bool(row["late"]) for row in report["decision_evidence"]),
        "admitted_rate": float(report["runtime"]["admitted_rate"]),
        "wall_seconds": float(report["runtime"]["active_wall_seconds"]),
        "engine_rate": float(report["runtime"]["engine_rate"]),
        "max_control_compute_ms": report["timings"]["control_path"]["max_ms"],
        "max_control_compute_note": (
            "This is measured detection, inference, and control compute time. It excludes "
            "the policy 4 ms availability floor. Decision evidence does not record availability "
            "timestamps."
        ),
    }


def preset_copy(base: dict, rate: int) -> dict:
    copied = json.loads(json.dumps(base))
    copied["requested_rate"] = float(rate)
    if float(copied["requested_rate"]) != float(rate):
        raise ValueError("temporary preset requested rate differs from the scenario rate")
    changed = {key for key in base if base[key] != copied[key]}
    if changed not in (set(), {"requested_rate"}):
        raise ValueError("temporary preset changed more than requested_rate")
    return copied


def run_rate(preset_path: Path) -> tuple[dict, str | None, list[dict]]:
    engine = None
    stopped = None
    checks = []
    started = time.monotonic()
    next_check = started + 5.0
    try:
        engine = Engine(preset_path)
        while engine.sim.data.time + TOLERANCE < SECONDS:
            elapsed = time.monotonic() - started
            if elapsed >= MAX_WALL_SECONDS:
                stopped = "wall_limit"
                break
            if time.monotonic() >= next_check:
                check = frozen.activity()
                checks.append({"wall_elapsed_s": elapsed, "activity": check})
                next_check += 5.0
                if check["active"]:
                    stopped = "another_coffee_simulation_active"
                    break
            engine.step()
        return engine.report(), stopped, checks
    finally:
        if engine is not None:
            engine.close()


def provenance(report: dict, base_preset: dict, base_hash: str, per_rate_path: Path) -> dict:
    return {
        "source_sha256": report["versions"]["source_sha256"],
        "model_sha256": report["versions"]["model"],
        "policy_sha256": report["versions"]["policy"],
        "base_preset": {
            "path": str(DEFAULT_PRESET.relative_to(ROOT)),
            "sha256": base_hash,
            "value_sha256": frozen.json_hash(base_preset),
        },
        "per_rate_preset": {
            "sha256": frozen.sha256(per_rate_path),
            "value_sha256": frozen.json_hash(json.loads(per_rate_path.read_text())),
            "engine_report_sha256": report["versions"]["preset"],
        },
        "native_thread_limits": report["runtime"]["native_thread_limits"],
        "native_threadpools": report["runtime"]["native_threadpools"],
    }


def fmt(value, digits: int = 3) -> str:
    return "-" if value is None else f"{value:.{digits}f}"


def percent(value) -> str:
    return "-" if value is None else f"{value * 100:.1f}%"


def markdown(rows: list[dict]) -> str:
    lines = [
        "# Development feed-rate comparison",
        "",
        "These are development comparisons. They are not acceptance results.",
        "",
        "Classification uses approximate object associations. It excludes unseen objects and objects with ambiguous associated predicted classes.",
        "Wilson intervals show uncertainty. Small denominators produce wider intervals.",
        "",
        "| Requested rate | Admitted rate | Capture | Capture 95% | Good loss | Good loss 95% | Physical sorting | Class accuracy | Class coverage | Late decisions | Wall s | Engine rate | Max control compute ms |",
        "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in rows:
        result = row["metrics"]
        capture = result["capture"]
        loss = result["good_loss"]
        physical = result["physical_sorting"]
        classes = result["object_classification"]
        lines.append(
            "| {rate} | {admitted} | {capture_count}/{capture_total} | {capture_interval} | "
            "{loss_count}/{loss_total} | {loss_interval} | {physical} | {class_accuracy} | "
            "{class_coverage} | {late} | {wall} | {engine} | {control} |".format(
                rate=row["requested_rate"],
                admitted=fmt(result["admitted_rate"]),
                capture_count=capture["count"][0], capture_total=capture["count"][1],
                capture_interval=("-" if capture["interval_95"] is None else
                                  f"{percent(capture['interval_95'][0])} to {percent(capture['interval_95'][1])}"),
                loss_count=loss["count"][0], loss_total=loss["count"][1],
                loss_interval=("-" if loss["interval_95"] is None else
                               f"{percent(loss['interval_95'][0])} to {percent(loss['interval_95'][1])}"),
                physical=percent(physical["accuracy"]),
                class_accuracy=percent(classes["accuracy"]),
                class_coverage=percent(classes["coverage"]),
                late=result["late_decisions"], wall=fmt(result["wall_seconds"]),
                engine=fmt(result["engine_rate"]), control=fmt(result["max_control_compute_ms"]),
            )
        )
    low_counts = []
    for row in rows:
        result = row["metrics"]
        capture_total = result["capture"]["count"][1]
        loss_total = result["good_loss"]["count"][1]
        if capture_total < 100 or loss_total < 100:
            low_counts.append(
                f"- {row['requested_rate']} objects/s: capture n={capture_total}, good-loss n={loss_total}."
            )
    if low_counts:
        lines.extend(["", "Low-count uncertainty:", "", *low_counts])
    return "\n".join(lines) + "\n"


def measure(manifest_path: Path, output_dir: Path) -> int:
    manifest_path = manifest_path.resolve()
    output_dir = output_dir.resolve()
    if output_dir.exists():
        raise FileExistsError(f"development output already exists: {output_dir}")
    frozen.validate_manifest(manifest_path)
    initial_activity = frozen.activity()
    if initial_activity["active"]:
        raise RuntimeError("another coffee simulation is active before measurement starts")

    base_preset = json.loads(DEFAULT_PRESET.read_text())
    if int(base_preset["seed"]) != SEED:
        raise ValueError(f"base preset seed must remain {SEED}")
    if float(base_preset["limits"]["max_wall_seconds"]) != MAX_WALL_SECONDS:
        raise ValueError(f"base preset max_wall_seconds must remain {MAX_WALL_SECONDS}")
    base_hash = frozen.sha256(DEFAULT_PRESET)
    output_dir.mkdir(parents=True, exist_ok=False)
    summary = {
        "schema_version": 1,
        "kind": "development_feed_rate_comparison",
        "not_acceptance": True,
        "manifest": str(manifest_path),
        "manifest_sha256": frozen.sha256(manifest_path),
        "started_at_utc": frozen.now(),
        "requested_rates": list(RATES),
        "seed": SEED,
        "simulation_seconds": SECONDS,
        "max_wall_seconds_per_rate": MAX_WALL_SECONDS,
        "initial_activity": initial_activity,
        "status": "running",
        "rate_results": [],
    }
    summary_path = output_dir / "summary.json"
    write_json(summary_path, summary, exclusive=True)

    with tempfile.TemporaryDirectory(prefix="coffee-rate-comparison-") as temporary:
        for rate in RATES:
            frozen.validate_manifest(manifest_path)
            before = frozen.activity()
            if before["active"]:
                summary.update(status="incomplete", reason="another_coffee_simulation_active",
                               stopped_before_rate=rate, activity=before)
                break
            copied = preset_copy(base_preset, rate)
            preset_path = Path(temporary) / f"rate-{rate}.json"
            write_json(preset_path, copied, exclusive=True)
            report, stopped, checks = run_rate(preset_path)
            frozen.validate_manifest(manifest_path)
            result = {
                "requested_rate": rate,
                "development_comparison": True,
                "activity_before_run": before,
                "activity_checks_every_5_wall_seconds": checks,
                "stop_reason": stopped,
                "provenance": provenance(report, base_preset, base_hash, preset_path),
                "metrics": metrics(report),
                "engine_report": report,
            }
            rate_dir = output_dir / f"rate-{rate}"
            rate_dir.mkdir(exist_ok=False)
            write_json(rate_dir / "report.json", result, exclusive=True)
            summary["rate_results"].append({
                "requested_rate": rate,
                "report": str((rate_dir / "report.json").relative_to(output_dir)),
                "stop_reason": stopped,
                "simulation_seconds": report["runtime"]["simulation_seconds"],
                "metrics": result["metrics"],
            })
            if stopped or float(report["runtime"]["simulation_seconds"]) + TOLERANCE < SECONDS:
                summary.update(status="incomplete", reason=stopped or "short_simulation",
                               stopped_rate=rate)
                break
            write_json(summary_path, summary)

    if summary["status"] == "running":
        summary["status"] = "complete"
    summary["finished_at_utc"] = frozen.now()
    summary["all_rates_completed"] = len(summary["rate_results"]) == len(RATES)
    write_json(summary_path, summary)
    write_markdown(output_dir / "summary.md", markdown(summary["rate_results"]))
    print(json.dumps({"status": summary["status"], "summary": str(summary_path)}, allow_nan=False))
    return 0 if summary["status"] == "complete" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True,
                        help="existing frozen acceptance manifest to verify")
    parser.add_argument("--out", type=Path, required=True,
                        help="new development comparison output directory")
    args = parser.parse_args()
    return measure(args.manifest, args.out)


if __name__ == "__main__":
    raise SystemExit(main())
