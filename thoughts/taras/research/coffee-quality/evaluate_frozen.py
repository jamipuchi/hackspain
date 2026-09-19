"""Freeze and evaluate the reserved coffee-sorter acceptance cohort."""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import urlopen


THREAD_LIMITS = {
    "OMP_NUM_THREADS": "1",
    "OPENBLAS_NUM_THREADS": "1",
    "MKL_NUM_THREADS": "1",
    "VECLIB_MAXIMUM_THREADS": "1",
}
os.environ.update(THREAD_LIMITS)

ROOT = Path(__file__).resolve().parents[4]
SIM_DIR = ROOT / "sim" / "coffee_sorter"
DEFAULT_PRESET = SIM_DIR / "configs" / "default_demo.json"
SEEDS = (111, 112, 113)
SECONDS = 6.0
TOLERANCE = 1e-9
RUN_CONTRACT = {"simulation_seconds": SECONDS, "max_wall_seconds": 300.0,
                "sequential": True, "seed_only_preset_copies": True}
ACCEPTANCE = {
    "eligible_objects_per_seed_min": 2000,
    "unresolved_objects_per_seed_max": 0,
    "capture_wilson_lower_min": 0.80,
    "good_loss_wilson_upper_max": 0.02,
    "admitted_objects_per_second_min": 500.0,
    "floating_point_tolerance": TOLERANCE,
    "cohort_spawn_window_s": [0.8, "simulation_end_minus_0.6"],
    "spills_and_unresolved_in_denominators": True,
}
sys.path.insert(0, str(SIM_DIR))

from controller import Policy  # noqa: E402
from engine import Engine, SOURCE_FILES  # noqa: E402
from assets import ASSETS, FAMILIES, N_VARIANTS  # noqa: E402


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def json_hash(value) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def write_json(path: Path, value, *, exclusive: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x" if exclusive else "w") as output:
        json.dump(value, output, indent=2, sort_keys=True, allow_nan=False)
        output.write("\n")


def policy_value(preset: dict) -> dict:
    config = preset["policy"]
    value = asdict(Policy(
        name=config["name"], reject_severities=tuple(config["reject_severities"]),
        threshold=float(config["threshold"]), anomaly=bool(config["anomaly"]),
        base_pulse=float(config["base_pulse_s"]), ref_mass=float(config["ref_mass_kg"]),
        max_pulse=float(config["max_pulse_s"]), lead=float(config["lead_s"]),
        latency_floor=float(config["latency_floor_s"]),
        induced_delay=float(config["induced_delay_s"]),
        fixed_latency=config["fixed_latency_s"], target_nozzles=config["target_nozzles"],
    ))
    return json.loads(json.dumps(value))


def frozen_values() -> dict:
    preset = json.loads(DEFAULT_PRESET.read_text())
    model_path = Path(preset["model_path"])
    model_path = model_path if model_path.is_absolute() else SIM_DIR / model_path
    policy = policy_value(preset)
    source_paths = {name: SIM_DIR / name for name in (*SOURCE_FILES, "assets.py", "bootstrap_model.py", "requirements.txt")}
    source_paths["evaluate_frozen.py"] = Path(__file__).resolve()
    return {
        "source_sha256": {name: sha256(path) for name, path in source_paths.items()},
        "assets_sha256": {name: sha256(ASSETS / name) for name in
                          ["half_bean.obj", *[f"tex_{family}_{index}.png"
                           for family in FAMILIES for index in range(N_VARIANTS)]]},
        "model": {"path": str(model_path.relative_to(ROOT)), "sha256": sha256(model_path),
                  "bytes": model_path.stat().st_size},
        "preset": {"path": str(DEFAULT_PRESET.relative_to(ROOT)),
                   "sha256": sha256(DEFAULT_PRESET), "value_sha256": json_hash(preset)},
        "policy": {"sha256": json_hash(policy), "value": policy},
        "native_thread_limits": {name: os.environ.get(name) for name in THREAD_LIMITS},
    }


def freeze(path: Path) -> None:
    if path.exists():
        raise FileExistsError(f"freeze manifest already exists: {path}")
    preset = json.loads(DEFAULT_PRESET.read_text())
    if float(preset["limits"]["max_wall_seconds"]) != 300.0:
        raise ValueError("default preset max_wall_seconds must remain 300")
    if float(preset["requested_rate"]) != 500.0:
        raise ValueError("requested rate must remain 500 objects/s")
    if abs(float(preset["layout"]["timestep"]) * preset["camera_every_steps"] - 0.004) > 1e-12:
        raise ValueError("inspection frequency must remain 250 Hz")
    manifest = {
        "schema_version": 1,
        "created_at_utc": now(),
        "expected_seeds": list(SEEDS),
        "run": RUN_CONTRACT,
        "acceptance": ACCEPTANCE,
        "frozen": frozen_values(),
    }
    write_json(path.resolve(), manifest, exclusive=True)
    print(json.dumps({"status": "frozen", "manifest": str(path.resolve())}))


def validate_manifest(path: Path) -> dict:
    manifest = json.loads(path.read_text())
    if manifest.get("schema_version") != 1:
        raise ValueError("unsupported freeze manifest schema")
    if manifest.get("expected_seeds") != list(SEEDS):
        raise ValueError("freeze manifest has unexpected seeds")
    if manifest.get("run") != RUN_CONTRACT or manifest.get("acceptance") != ACCEPTANCE:
        raise ValueError("freeze manifest run contract changed")
    if manifest.get("frozen") != frozen_values():
        raise ValueError("source, model, preset, policy, or native-thread freeze changed")
    return manifest


SIMULATION_COMMANDS = (
    "coffee_sorter/engine.py", "coffee_sorter/run.py", "coffee_sorter/classifier.py",
    "coffee_sorter/bootstrap_model.py", "coffee_sorter/run_characterization.py",
    "coffee_sorter/run_sensor_realism.py", "coffee_sorter/run_generalization.py",
    "coffee_sorter/export_replay.py", "coffee-quality/measure_motion.py",
    "coffee-quality/measure_pulses.py", "coffee-quality/evaluate_frozen.py",
)


def activity() -> dict:
    health = {"reachable": False, "status": None}
    try:
        with urlopen("http://127.0.0.1:8890/health", timeout=0.5) as response:
            health = {"reachable": True, **json.loads(response.read())}
    except HTTPError as exc:
        try:
            health = {"reachable": True, "http_status": exc.code, **json.loads(exc.read())}
        except (json.JSONDecodeError, OSError):
            health = {"reachable": True, "http_status": exc.code, "status": None}
    except (URLError, TimeoutError, json.JSONDecodeError, OSError):
        pass

    result = subprocess.run(["ps", "-axo", "pid=,command="], check=True,
                            capture_output=True, text=True)
    processes = []
    for line in result.stdout.splitlines():
        fields = line.strip().split(None, 1)
        if len(fields) != 2 or not fields[0].isdigit() or int(fields[0]) == os.getpid():
            continue
        executable = Path(fields[1].split()[0]).name.lower()
        if executable.startswith("python") and any(token in fields[1] for token in SIMULATION_COMMANDS):
            processes.append({"pid": int(fields[0]), "command": fields[1]})
    return {"health": health, "processes": processes,
            "active": health.get("status") in ("starting", "running") or bool(processes)}


def wilson(successes: int, total: int) -> list[float] | None:
    if total == 0:
        return None
    z = 1.96
    proportion = successes / total
    scale = 1 + z * z / total
    centre = (proportion + z * z / (2 * total)) / scale
    half = z * math.sqrt(proportion * (1 - proportion) / total
                         + z * z / (4 * total * total)) / scale
    return [max(0.0, centre - half), min(1.0, centre + half)]


def counts(report: dict) -> dict:
    quality = report["quality"]
    end = float(report["runtime"]["simulation_seconds"]) - 0.6
    if abs(float(quality["cohort_start_s"]) - 0.8) > TOLERANCE:
        raise ValueError("cohort start changed")
    if abs(float(quality["cohort_end_s"]) - end) > TOLERANCE:
        raise ValueError("cohort end changed")
    if quality["spills_in_denominators"] is not True or quality["unresolved_in_denominators"] is not True:
        raise ValueError("quality denominators changed")
    for row in report["objects"]:
        expected = 0.8 <= float(row["spawn_time_s"]) <= end
        if bool(row["in_cohort"]) != expected:
            raise ValueError("closed cohort membership changed")
    cohort = [row for row in report["objects"] if row["in_cohort"]]
    required = [row for row in cohort if row["required_reject"]]
    keep = [row for row in cohort if not row["required_reject"]]
    raw = {
        "eligible_objects": len(cohort), "required_defects": len(required),
        "keep_objects": len(keep),
        "captured_required_defects": sum(row["outcome"] == "reject" for row in required),
        "good_objects_lost": sum(row["outcome"] in ("reject", "spilled") for row in keep),
        "unresolved_objects": sum(row["outcome"] is None for row in cohort),
    }
    for key, value in raw.items():
        if int(quality[key]) != value:
            raise ValueError(f"reported {key} does not match object evidence")
    return raw


def assess(seed: int, report: dict) -> dict:
    raw = counts(report)
    capture = wilson(raw["captured_required_defects"], raw["required_defects"])
    good_loss = wilson(raw["good_objects_lost"], raw["keep_objects"])
    admitted = float(report["runtime"]["admitted_rate"])
    checks = {
        "eligible": raw["eligible_objects"] >= 2000,
        "zero_unresolved": raw["unresolved_objects"] == 0,
        "capture_lower": capture is not None and capture[0] >= 0.80,
        "good_loss_upper": good_loss is not None and good_loss[1] <= 0.02,
        "admitted_rate": admitted + TOLERANCE >= 500.0,
    }
    return {"seed": seed, "raw_counts": raw, "capture_interval_95": capture,
            "good_loss_interval_95": good_loss, "admitted_rate": admitted,
            "checks": checks, "pass": all(checks.values())}


def pooled(results: list[dict]) -> dict:
    keys = ("eligible_objects", "required_defects", "keep_objects",
            "captured_required_defects", "good_objects_lost", "unresolved_objects")
    raw = {key: sum(row["raw_counts"][key] for row in results) for key in keys}
    capture = wilson(raw["captured_required_defects"], raw["required_defects"])
    good_loss = wilson(raw["good_objects_lost"], raw["keep_objects"])
    return {"raw_counts": raw, "capture_interval_95": capture,
            "good_loss_interval_95": good_loss,
            "quality_bounds_pass": (capture is not None and capture[0] >= 0.80
                                    and good_loss is not None and good_loss[1] <= 0.02)}


def run_seed(preset_path: Path, wall_limit: float) -> tuple[dict, str | None, dict | None]:
    engine = None
    stopped = None
    competing = None
    started = time.monotonic()
    next_check = started + 5.0
    try:
        engine = Engine(preset_path)
        while engine.sim.data.time + TOLERANCE < SECONDS:
            if time.monotonic() - started >= wall_limit:
                stopped = "wall_limit"
                break
            if time.monotonic() >= next_check:
                competing = activity()
                next_check = time.monotonic() + 5.0
                if competing["active"]:
                    stopped = "another_coffee_simulation_active"
                    break
            engine.step()
        return engine.report(), stopped, competing
    finally:
        if engine is not None:
            engine.close()


def evaluate(manifest_path: Path, output_dir: Path) -> int:
    manifest_path = manifest_path.resolve()
    output_dir = output_dir.resolve()
    validate_manifest(manifest_path)
    exposure_path = manifest_path.with_name(manifest_path.name + ".exposed.json")
    if output_dir.exists():
        raise FileExistsError(f"acceptance output already exists: {output_dir}")
    if exposure_path.exists():
        raise FileExistsError(f"reserved seeds are already exposed: {exposure_path}")
    output_dir.mkdir(parents=True, exist_ok=False)
    exposure = {"started_at_utc": now(), "manifest_sha256": sha256(manifest_path),
                "seeds_exposed": list(SEEDS), "output": str(output_dir)}
    write_json(exposure_path, exposure, exclusive=True)
    summary = {"schema_version": 1, "status": "running", "manifest": str(manifest_path),
               "manifest_sha256": exposure["manifest_sha256"], "seeds_exposed": list(SEEDS),
               "started_at_utc": exposure["started_at_utc"], "seed_results": []}
    summary_path = output_dir / "summary.json"
    write_json(summary_path, summary)

    check = activity()
    summary["initial_activity"] = check
    if check["active"]:
        summary.update(status="incomplete", reason="another_coffee_simulation_active",
                       finished_at_utc=now(), pooled=pooled([]))
        write_json(summary_path, summary)
        print(json.dumps({"status": summary["status"], "summary": str(summary_path)}))
        return 1

    preset = json.loads(DEFAULT_PRESET.read_text())
    wall_limit = float(preset["limits"]["max_wall_seconds"])
    with tempfile.TemporaryDirectory(prefix="coffee-acceptance-") as temporary:
        for seed in SEEDS:
            validate_manifest(manifest_path)
            check = activity()
            if check["active"]:
                summary.update(status="incomplete", reason="another_coffee_simulation_active",
                               stopped_before_seed=seed, activity=check)
                break
            seeded = json.loads(json.dumps(preset))
            seeded["seed"] = seed
            if {key for key in preset if preset[key] != seeded[key]} != {"seed"}:
                raise ValueError("temporary preset changed more than seed")
            preset_path = Path(temporary) / f"seed-{seed}.json"
            write_json(preset_path, seeded)
            report, stopped, competing = run_seed(preset_path, wall_limit)
            write_json(output_dir / f"seed-{seed}-report.json", report, exclusive=True)
            validate_manifest(manifest_path)
            if stopped or float(report["runtime"]["simulation_seconds"]) + TOLERANCE < SECONDS:
                summary.update(status="incomplete", reason=stopped or "short_simulation",
                               stopped_seed=seed, activity=competing)
                break
            result = assess(seed, report)
            summary["seed_results"].append(result)
            summary["pooled"] = pooled(summary["seed_results"])
            write_json(summary_path, summary)

    if summary["status"] == "running":
        summary["status"] = "pass" if all(row["pass"] for row in summary["seed_results"]) else "fail"
    summary["pooled"] = pooled(summary["seed_results"])
    summary["finished_at_utc"] = now()
    summary["all_seeds_completed"] = len(summary["seed_results"]) == len(SEEDS)
    write_json(summary_path, summary)
    print(json.dumps({"status": summary["status"], "summary": str(summary_path)}))
    return 0 if summary["status"] == "pass" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--freeze", type=Path, metavar="MANIFEST")
    action.add_argument("--evaluate", type=Path, metavar="MANIFEST")
    parser.add_argument("--out", type=Path, help="new acceptance output directory")
    args = parser.parse_args()
    if args.freeze:
        if args.out:
            parser.error("--out applies only to --evaluate")
        freeze(args.freeze)
        return 0
    if not args.out:
        parser.error("--evaluate requires --out")
    return evaluate(args.evaluate, args.out)


if __name__ == "__main__":
    raise SystemExit(main())
