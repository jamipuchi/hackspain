"""Run and summarize matched physical sensor-realism experiments.

Quickstart:
  python run_sensor_realism.py all
  python run_sensor_realism.py run --scenario exposure-stress-500us-1000
  python run_sensor_realism.py summarize
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import asdict
import csv
import hashlib
import json
from pathlib import Path
import platform
import shutil
import sys
import time

import cv2
import matplotlib
import mujoco
import numpy as np

matplotlib.use("Agg")
import matplotlib.pyplot as plt

import assets
import run
from run_characterization import completed_metrics
from sensor_realism import (
    PhysicalConfig, SensorConfig, SensorInspector, SensorSorterSim, diagnostic_metrics,
)


HERE = Path(__file__).resolve().parent
ROOT = HERE / "runs" / "sensor-realism"
CONFIG_PATH = HERE / "configs" / "sensor_realism_scenarios.json"
MODEL_PATH = HERE / "runs" / "generalization" / "green_arabica" / "model" / "green_arabica.joblib"
SOURCE_FILES = ("sensor_realism.py", "run_sensor_realism.py", "run.py", "controller.py",
                "sim.py", "vision.py", "evidence.py", "scene.py", "profiles.py",
                "classifier.py", "assets.py", "render.py", "run_characterization.py")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_hashes() -> dict:
    return {name: sha256(HERE / name) for name in SOURCE_FILES}


LOADED_SOURCE_HASHES = source_hashes()
LOADED_CONFIG_SHA256 = sha256(CONFIG_PATH)


def require_loaded_inputs() -> None:
    if source_hashes() != LOADED_SOURCE_HASHES or sha256(CONFIG_PATH) != LOADED_CONFIG_SHA256:
        raise RuntimeError("source or config changed since process start; restart with the current files")


def environment_versions() -> dict:
    return {"python": platform.python_version(), "mujoco": mujoco.__version__,
            "opencv": cv2.__version__, "numpy": np.__version__}


def artifact_paths(output: Path) -> list[Path]:
    return [output / "decisions.csv", output / "inspection_evidence.json", output / "feed_manifest.json",
            *(output / f"inspection_{i}.png" for i in range(6))]


def artifact_hashes(output: Path) -> dict:
    return {path.name: sha256(path) for path in artifact_paths(output)}


def stable_sha256(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def artifacts_are_valid(output: Path, metrics: dict) -> bool:
    try:
        completion = json.loads((output / "completion.json").read_text())
        if not isinstance(completion, dict) or completion.get("metrics_sha256") != sha256(output / "metrics.json"):
            return False
        for path in artifact_paths(output):
            if not path.is_file() or not path.stat().st_size:
                return False
        with (output / "decisions.csv").open(newline="") as stream:
            reader = csv.DictReader(stream)
            rows = list(reader)
            if reader.fieldnames is None or not {"tid", "reject"}.issubset(reader.fieldnames):
                return False
        evidence = json.loads((output / "inspection_evidence.json").read_text())
        manifest = json.loads((output / "feed_manifest.json").read_text())
        if not isinstance(manifest, dict) or not isinstance(manifest.get("items"), list):
            return False
        if not isinstance(evidence, list) or len(evidence) != 6 or not all(isinstance(item, dict) for item in evidence):
            return False
        if {item.get("image") for item in evidence} != {f"inspection_{i}.png" for i in range(6)}:
            return False
        if any(cv2.imread(str(output / f"inspection_{i}.png"), cv2.IMREAD_UNCHANGED) is None for i in range(6)):
            return False
        saved = metrics.get("sensor_realism", {}).get("artifact_sha256")
        return isinstance(saved, dict) and saved == artifact_hashes(output)
    except (csv.Error, json.JSONDecodeError, OSError, TypeError):
        return False


def require_finite_numbers(value, name="config") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            require_finite_numbers(item, f"{name}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            require_finite_numbers(item, f"{name}[{index}]")
    elif isinstance(value, (int, float)) and not isinstance(value, bool) and not np.isfinite(value):
        raise ValueError(f"{name} must be finite")


def load_config() -> dict:
    config = json.loads(CONFIG_PATH.read_text())
    require_finite_numbers(config)
    if config.get("schema_version") != 1:
        raise ValueError("unsupported sensor-realism config schema")
    shared = config["shared"]
    if shared["profile"] != "green_arabica" or shared["seconds"] < 4.0:
        raise ValueError("experiment requires frozen green classifier and at least four seconds")
    if shared.get("camera_cadence_hz") != 250.0:
        raise ValueError("stock camera cadence is fixed at 250 Hz")
    slugs = [item["slug"] for item in config["scenarios"]]
    if len(slugs) != len(set(slugs)):
        raise ValueError("scenario slugs must be unique")
    for item in config["scenarios"]:
        SensorConfig(**item.get("sensor", {})).validate()
        PhysicalConfig(**item.get("physical", {})).validate()
    return config


def scenario_args(shared: dict, scenario: dict) -> argparse.Namespace:
    return argparse.Namespace(
        profile=shared["profile"], rate=scenario["requested_rate"], seed=shared["seed"],
        seconds=shared["seconds"], policy=shared["policy"], threshold=shared["threshold"],
        no_anomaly=False, name=scenario["slug"], controller_delay_ms=0.0,
        fixed_controller_latency_ms=shared["fixed_controller_latency_ms"],
        jet_force=shared["jet_force_n"], base_pulse_ms=3.0, target_nozzles=None,
        split_z_drop=run.Layout.split_z_drop, nozzles=run.Layout.n_nozzles,
        pool_ellipsoid=shared["pool_ellipsoid"], pool_half=shared["pool_half"],
        pool_box=shared["pool_box"], pool_capsule=shared["pool_capsule"], video=False,
    )


@contextmanager
def local_experiment(sensor_config: SensorConfig, physical_config: PhysicalConfig):
    """Inject local variants into the stock runner without changing protected sources."""
    holder = {}
    original = run.SorterSim, run.Inspector, run.Controller, run.RUNS, run.MODELS

    def make_sim(*args, **kwargs):
        holder["sim"] = SensorSorterSim(*args, physical_config=physical_config, **kwargs)
        return holder["sim"]

    def make_inspector(sim):
        holder["inspector"] = SensorInspector(sim, sensor_config)
        return holder["inspector"]

    class CapturingController(original[2]):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            holder["controller"] = self

    run.SorterSim, run.Inspector, run.Controller, run.RUNS, run.MODELS = (
        make_sim, make_inspector, CapturingController, ROOT, MODEL_PATH.parent)
    try:
        yield holder
    finally:
        run.SorterSim, run.Inspector, run.Controller, run.RUNS, run.MODELS = original


def expected_identity(config: dict, scenario: dict) -> dict:
    return {
        "config_sha256": sha256(CONFIG_PATH),
        "scenario": scenario,
        "shared": config["shared"],
        "classifier_sha256": sha256(MODEL_PATH),
        "source_sha256": source_hashes(),
        "environment": environment_versions(),
    }


def complete_scenario(config: dict, scenario: dict) -> dict | None:
    output = ROOT / scenario["slug"]
    metrics = completed_metrics(output, config["shared"]["seconds"])
    if metrics is None:
        return None
    if not artifacts_are_valid(output, metrics):
        raise RuntimeError(f"completed scenario {scenario['slug']} has invalid or modified artifacts")
    identity = metrics.get("sensor_realism", {}).get("identity")
    if identity != expected_identity(config, scenario):
        raise RuntimeError(f"completed scenario {scenario['slug']} has different config, source, or model")
    return metrics


def archive_for_rerun(output: Path) -> None:
    archive = ROOT / "_reruns" / f"{output.name}-{time.time_ns()}"
    archive.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(output), str(archive))


def run_scenario(config: dict, scenario: dict, rerun=False) -> dict:
    require_loaded_inputs()
    try:
        existing = complete_scenario(config, scenario)
    except RuntimeError:
        if not rerun:
            raise
        existing = None
    if existing is not None and not rerun:
        print(f"SKIP {scenario['slug']}: complete matching evidence exists", flush=True)
        return existing
    output = ROOT / scenario["slug"]
    if output.exists():
        if not rerun:
            raise RuntimeError(f"incomplete output needs inspection before rerun: {output}")
        archive_for_rerun(output)

    identity_before = expected_identity(config, scenario)
    sensor_config = SensorConfig(**scenario.get("sensor", {}))
    physical_config = PhysicalConfig(**scenario.get("physical", {}))
    print(f"RUN {scenario['slug']}: {scenario['label']}", flush=True)
    with local_experiment(sensor_config, physical_config) as holder:
        metrics = run.cmd_run(scenario_args(config["shared"], scenario), return_metrics=True)
    if expected_identity(config, scenario) != identity_before:
        raise RuntimeError("config, behavioral source, or frozen green classifier changed during experiment")

    diagnostics = diagnostic_metrics(metrics, holder["controller"], holder["inspector"], holder["sim"])
    feed_manifest = holder["sim"].feed_manifest()
    (output / "feed_manifest.json").write_text(json.dumps(feed_manifest, indent=2))
    cohort = holder["sim"].eligible_feed_records(metrics["denominators"]["cohort_start_s"],
                                                   metrics["denominators"]["cohort_end_s"])
    cohort_classes = {}
    for item in cohort:
        cohort_classes[item["cls"]] = cohort_classes.get(item["cls"], 0) + 1
    rerun = f"{sys.executable} -u run_sensor_realism.py run --scenario {scenario['slug']} --rerun"
    metrics["sensor_realism"] = {
        "identity": identity_before,
        "sensor_config": asdict(sensor_config),
        "physical_config": asdict(physical_config),
        "blur_formula": "3 m/s * exposure_seconds * 4000 px/m",
        "blur_pixels": sensor_config.blur_pixels,
        "blur_kernel_rows": sensor_config.blur_kernel_rows,
        "camera_width_px": 2080,
        "exposure_vs_latency": "Hardware exposure is an image-formation assumption, separate from the existing 4 ms pipeline latency floor. The controller retains a 60 ms minimum total latency.",
        "diagnostics": diagnostics,
        "feed_manifest_sha256": sha256(output / "feed_manifest.json"),
        "feed": {"eligible_product_identity_sha256": stable_sha256(cohort),
                 "eligible_product_cohort": cohort_classes, "eligible_product_count": len(cohort)},
        "artifact_sha256": artifact_hashes(output),
        "source_sha256": identity_before["source_sha256"],
        "config_source": str(CONFIG_PATH.relative_to(HERE)),
        "exact_rerun_command": rerun,
        "limits": [
            "Synthetic single runs do not establish hardware performance or real-time throughput.",
            "Final physical outcomes alone do not isolate vision errors from tracking, timing, contact, or jet effects.",
            "Exposure, noise, illumination gradient, jitter waveform, and crowded-feed geometry are assumptions until measured.",
            "The item-keyed feed stream preserves product identity across placement retries; crowded admission can still change the eligible cohort.",
        ],
    }
    (output / "metrics.json").write_text(json.dumps(metrics, indent=2, default=float))
    (output / "completion.json").write_text(json.dumps({
        "metrics_sha256": sha256(output / "metrics.json"),
    }, indent=2))
    print(f"DONE {scenario['slug']}: effective={metrics['throughput_beans_per_s']:.1f}/s "
          f"accuracy={100 * metrics['physical_reject_accuracy']:.1f}% "
          f"recall={100 * metrics['physical_reject_recall']:.1f}% "
          f"good-false-eject={100 * metrics['good_false_eject_rate']:.1f}%", flush=True)
    return metrics


def summary_row(scenario: dict, metrics: dict) -> dict:
    d = metrics["denominators"]
    diag = metrics["sensor_realism"]["diagnostics"]
    return {
        "slug": scenario["slug"], "label": scenario["label"],
        "comparison": scenario["comparison"], "requested_rate_beans_per_s": scenario["requested_rate"],
        "effective_rate_beans_per_s": metrics["throughput_beans_per_s"],
        "eligible_beans": d["eligible_beans"], "resolved_beans": d["resolved_beans"],
        "defects_to_remove": d["defects_to_remove"], "keep_beans": d["keep_beans"],
        "rejected_beans": d["rejected_beans"],
        "physical_accuracy": metrics["physical_reject_accuracy"],
        "physical_defect_recall": metrics["physical_reject_recall"],
        "physical_good_false_eject_rate": metrics["good_false_eject_rate"],
        "physical_intervals_95": metrics["intervals_95"],
        "feed": metrics["sensor_realism"]["feed"],
        "vision": diag["vision"], "camera_decision_coverage": diag["camera_decision_coverage"],
        "late_miss_own_hit_funnel": diag["late_miss_own_hit_funnel"],
        "merged_cohorts": diag["merged_cohorts"], "runtime": diag["runtime"],
        "physical": diag["physical"],
    }


def plot_summary(rows: list[dict]) -> None:
    labels = [row["slug"].replace("-1000", "").replace("-3000", "") for row in rows]
    x = np.arange(len(rows))
    fig, axes = plt.subplots(2, 2, figsize=(17, 10), constrained_layout=True)
    axes[0, 0].bar(x, [100 * row["physical_accuracy"] for row in rows])
    axes[0, 0].set_ylabel("physical accuracy (%)")
    axes[0, 1].bar(x, [100 * row["physical_defect_recall"] for row in rows], color="C2")
    axes[0, 1].set_ylabel("physical defect recall (%)")
    axes[1, 0].bar(x, [100 * row["physical_good_false_eject_rate"] for row in rows], color="C3")
    axes[1, 0].set_ylabel("good false ejects / keep beans (%)")
    axes[1, 1].bar(x, [100 * (row["merged_cohorts"]["ever_merged_occupancy"] or 0) for row in rows], color="C4")
    axes[1, 1].set_ylabel("eligible beans ever merged (%)")
    for ax in axes.flat:
        ax.set_xticks(x, labels, rotation=50, ha="right", fontsize=8)
        ax.grid(axis="y", alpha=0.25)
    fig.suptitle("Sensor realism: matched single physical runs (ASSUMED degradations)")
    fig.savefig(ROOT / "summary.png", dpi=150)
    plt.close(fig)


def camera_contact_sheet(scenarios: list[dict]) -> None:
    width, tile_h, columns = 620, 150, 2
    rows = (len(scenarios) + columns - 1) // columns
    canvas = np.full((rows * (tile_h + 34), columns * width, 3), 242, np.uint8)
    for index, scenario in enumerate(scenarios):
        image = cv2.imread(str(ROOT / scenario["slug"] / "inspection_2.png"))
        if image is None:
            raise RuntimeError(f"missing camera evidence for {scenario['slug']}")
        scale = min(width / image.shape[1], tile_h / image.shape[0])
        resized = cv2.resize(image, (round(image.shape[1] * scale), round(image.shape[0] * scale)),
                             interpolation=cv2.INTER_AREA)
        row, column = divmod(index, columns)
        x0, y0 = column * width, row * (tile_h + 34)
        canvas[y0:y0 + resized.shape[0], x0:x0 + resized.shape[1]] = resized
        cv2.putText(canvas, scenario["slug"], (x0 + 6, y0 + tile_h + 23),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.48, (25, 25, 25), 1, cv2.LINE_AA)
    if not cv2.imwrite(str(ROOT / "camera_contact_sheet.png"), canvas):
        raise RuntimeError("could not write camera contact sheet")


def write_report(config: dict, rows: list[dict]) -> None:
    lines = [
        "# Sensor realism results", "",
        "Quickstart (from `sim/coffee_sorter`):", "",
        f"    {sys.executable} -u run_sensor_realism.py all", "",
        "These are matched synthetic physical runs with the frozen green classifier. All degradations labeled ASSUMED remain unverified on hardware; there is no real-time claim.", "",
        "| scenario | effective /s | accuracy | defect recall (defects) | good false eject (keep) | ever merged | CPU overruns |", "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| {row['slug']} | {row['effective_rate_beans_per_s']:.1f} | {100 * row['physical_accuracy']:.1f}% | "
            f"{100 * row['physical_defect_recall']:.1f}% ({row['defects_to_remove']}) | "
            f"{100 * row['physical_good_false_eject_rate']:.1f}% ({row['keep_beans']}) | "
            f"{100 * (row['merged_cohorts']['ever_merged_occupancy'] or 0):.1f}% | "
            f"{row['runtime']['detector_controller_cpu_overruns']}/{row['runtime']['detector_controller_frames']} |"
        )
    lines += [
        "", "Exposure is distinct from latency: assumed 100 us nominal and 500 us stress exposure only form the image (blur = `3 * seconds * 4000` pixels). The unchanged controller retains its existing 4 ms pipeline floor and every run uses a 60 ms minimum total latency and 0.06 N jets.",
        "", "The late/miss/own-hit funnel and merged cohorts are in `summary.json` and each scenario's `metrics.json`. They are diagnostics, not an isolated causal attribution from final outcomes.",
        "", f"Config SHA-256: `{sha256(CONFIG_PATH)}`", f"Classifier SHA-256: `{sha256(MODEL_PATH)}`",
        "", "Exact per-scenario rerun commands are in `rerun_commands.json`.", "",
    ]
    (ROOT / "REPORT.md").write_text("\n".join(lines))


def summarize(config: dict) -> None:
    require_loaded_inputs()
    metrics = []
    for scenario in config["scenarios"]:
        item = complete_scenario(config, scenario)
        if item is None:
            raise RuntimeError(f"scenario is not complete: {scenario['slug']}")
        metrics.append(item)
    rows = [summary_row(scenario, result) for scenario, result in zip(config["scenarios"], metrics)]
    cohort_checks = {}
    for comparison in {scenario["comparison"] for scenario in config["scenarios"]}:
        checked = [row for row in rows if row["comparison"] == comparison]
        identities = {row["feed"]["eligible_product_identity_sha256"] for row in checked}
        if checked[0]["requested_rate_beans_per_s"] == 1000.0:
            if len(identities) != 1:
                raise RuntimeError(f"one-factor comparison {comparison} has unmatched realized product cohorts")
            cohort_checks[comparison] = {"status": "asserted-matched-realized-product-cohort",
                                         "identity": identities.pop()}
        else:
            cohort_checks[comparison] = {
                "status": ("matched-realized-product-cohort" if len(identities) == 1 else
                           "not-causally-paired: crowded/high-feed admission changed eligible cohort"),
                "identities": sorted(identities),
            }
    summary = {
        "method": "1000/s one-factor comparisons assert identical realized product cohorts from item-keyed feed manifests; 3000/s crowded cases report effective admission and do not claim causal pairing if eligibility differs.",
        "assumptions": config["assumptions"], "rows": rows,
        "cohort_checks": cohort_checks,
        "config_sha256": sha256(CONFIG_PATH), "classifier_sha256": sha256(MODEL_PATH),
        "source_sha256": source_hashes(),
        "limits": [
            "One physical run per scenario; intervals are binomial sampling intervals, not between-run uncertainty.",
            "Synthetic transform runtime is not camera hardware time and is excluded from detector/controller CPU.",
            "Effective admitted rate is reported because crowded spawning may not sustain the requested feed.",
            "Final outcomes cannot by themselves identify whether vision, tracking, timing, contacts, or jets caused an error.",
        ],
    }
    ROOT.mkdir(parents=True, exist_ok=True)
    (ROOT / "summary.json").write_text(json.dumps(summary, indent=2))
    commands = {scenario["slug"]: metrics[index]["sensor_realism"]["exact_rerun_command"]
                for index, scenario in enumerate(config["scenarios"])}
    commands["full_suite"] = f"{sys.executable} -u run_sensor_realism.py all"
    (ROOT / "rerun_commands.json").write_text(json.dumps(commands, indent=2))
    plot_summary(rows)
    camera_contact_sheet(config["scenarios"])
    write_report(config, rows)
    print("wrote", ROOT / "summary.json", ROOT / "summary.png", ROOT / "REPORT.md")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("run", "summarize", "all"), nargs="?", default="all")
    parser.add_argument("--scenario", help="scenario slug (run command only)")
    parser.add_argument("--rerun", action="store_true", help="archive an existing scenario before rerunning it")
    args = parser.parse_args()
    config = load_config()
    assets.build()
    ROOT.mkdir(parents=True, exist_ok=True)
    if args.command in ("run", "all"):
        selected = config["scenarios"]
        if args.scenario:
            selected = [scenario for scenario in selected if scenario["slug"] == args.scenario]
            if not selected:
                raise SystemExit(f"unknown scenario: {args.scenario}")
        for scenario in selected:
            run_scenario(config, scenario, rerun=args.rerun)
    if args.command in ("summarize", "all") and not args.scenario:
        summarize(config)


if __name__ == "__main__":
    main()
