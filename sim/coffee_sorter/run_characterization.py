"""Run the bounded coffee-sorter characterization batches serially.

Examples:
  python run_characterization.py latency
  python run_characterization.py tuning

Each run writes its own stdout log beside its output directory. Runs with the
complete required artifact set are skipped, so an interrupted batch can resume.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time


HERE = Path(__file__).resolve().parent
RUNS = HERE / "runs"

LATENCY = [
    ("delay-0", ["--controller-delay-ms", "0"]),
    ("delay-20", ["--controller-delay-ms", "20"]),
    ("delay-30", ["--controller-delay-ms", "30"]),
    ("delay-40", ["--controller-delay-ms", "40"]),
    ("delay-60", ["--controller-delay-ms", "60"]),
]

TUNING = [
    ("physics-base", ["--rate", "1000", "--fixed-controller-latency-ms", "60"]),
    ("force-0.06", ["--rate", "1000", "--fixed-controller-latency-ms", "60", "--jet-force", "0.06"]),
    ("force-0.12", ["--rate", "1000", "--fixed-controller-latency-ms", "60", "--jet-force", "0.12"]),
    ("pulse-2ms", ["--rate", "1000", "--fixed-controller-latency-ms", "60", "--base-pulse-ms", "2"]),
    ("pulse-5ms", ["--rate", "1000", "--fixed-controller-latency-ms", "60", "--base-pulse-ms", "5"]),
    ("split-0.10", ["--rate", "1000", "--fixed-controller-latency-ms", "60", "--split-z-drop", "0.10"]),
    ("split-0.15", ["--rate", "1000", "--fixed-controller-latency-ms", "60", "--split-z-drop", "0.15"]),
    ("target-nozzles-1", ["--rate", "1000", "--fixed-controller-latency-ms", "60", "--target-nozzles", "1"]),
    ("target-nozzles-3", ["--rate", "1000", "--fixed-controller-latency-ms", "60", "--target-nozzles", "3"]),
    ("pool-3000-base", ["--rate", "3000", "--fixed-controller-latency-ms", "60"]),
    ("pool-3000-enlarged", ["--rate", "3000", "--fixed-controller-latency-ms", "60",
                             "--pool-ellipsoid", "1800", "--pool-half", "72",
                             "--pool-box", "30", "--pool-capsule", "30"]),
]


def completed_metrics(output, seconds):
    required = [output / "metrics.json", output / "decisions.csv", output / "inspection_evidence.json",
                *(output / f"inspection_{i}.png" for i in range(6))]
    if not all(path.stat().st_size > 0 for path in required if path.exists()) or not all(path.exists() for path in required):
        return None
    try:
        metrics = json.loads(required[0].read_text())
        evidence = json.loads(required[2].read_text())
    except (json.JSONDecodeError, OSError):
        return None
    fields = ("physical_reject_accuracy", "physical_reject_recall", "physical_reject_precision",
              "good_false_eject_rate", "denominators", "camera_blobs", "config")
    if not all(field in metrics for field in fields) or metrics.get("simulation_end_s", 0) < seconds - 1e-6:
        return None
    return metrics if len(evidence) == 6 else None


def run_batch(name, experiments):
    group = RUNS / f"{name}-sweep"
    group.mkdir(parents=True, exist_ok=True)
    manifest = []
    for slug, extra in experiments:
        output_name = f"{name}-sweep/{slug}"
        output = RUNS / output_name
        metrics_path = output / "metrics.json"
        command = [sys.executable, "-u", "run.py", "run", "--rate", "1000", "--seconds", "4",
                   "--name", output_name, *extra]
        entry = {"name": slug, "command": command, "metrics": str(metrics_path.relative_to(HERE))}
        metrics = completed_metrics(output, 4)
        if metrics:
            entry["status"] = "skipped-complete"
            manifest.append(entry)
            print(f"SKIP {slug}: metrics already exist", flush=True)
            continue
        if output.exists():
            shutil.rmtree(output)
        started = time.time()
        with (group / f"{slug}.log").open("w") as log:
            result = subprocess.run(command, cwd=HERE, stdout=log, stderr=subprocess.STDOUT)
        metrics = completed_metrics(output, 4) if result.returncode == 0 else None
        entry.update(status="completed" if metrics else "failed", returncode=result.returncode,
                     wall_seconds=time.time() - started)
        manifest.append(entry)
        (group / "manifest.json").write_text(json.dumps(manifest, indent=2))
        if not metrics:
            raise SystemExit(f"FAIL {slug}; see {group / f'{slug}.log'}")
        print(f"DONE {slug}: admitted={metrics['throughput_beans_per_s']:.1f}/s "
              f"recall={100 * metrics['physical_reject_recall']:.1f}% "
              f"false_eject={100 * metrics['good_false_eject_rate']:.1f}% "
              f"late={metrics['late_decisions']}/{metrics['reject_decisions']} "
              f"starved={metrics['pool_starved']}", flush=True)
    (group / "manifest.json").write_text(json.dumps(manifest, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("batch", choices=("latency", "tuning"))
    args = parser.parse_args()
    run_batch(args.batch, LATENCY if args.batch == "latency" else TUNING)


if __name__ == "__main__":
    main()
