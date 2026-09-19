"""Run the matched green/roasted generalization experiment and build evidence."""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import shutil
import subprocess
import sys
from dataclasses import asdict, replace
from pathlib import Path

import cv2
import matplotlib
import mujoco
import numpy as np
import sklearn

matplotlib.use("Agg")
import matplotlib.pyplot as plt

import assets
import run
from classifier import Model
from controller import SPECIALTY
from profiles import PROFILES
from run_characterization import completed_metrics
from sim import SorterSim
from vision import Inspector, draw_blobs


HERE = Path(__file__).resolve().parent
ROOT = HERE / "runs" / "generalization"
COMPARISON = ROOT / "comparison"
SHARED_SOURCES = ("controller.py", "vision.py", "sim.py", "run.py")
PROVENANCE_SOURCES = (*SHARED_SOURCES, "profiles.py", "classifier.py")
BASE_COMMIT = "511f1047e5fa1befa1790db607b2d9c4f88f6a45"
TRAINING = dict(seconds=24.0, rate=900.0, defect_boost=5.0, seed=0)
RUN = dict(seconds=4.0, rate=1000.0, seed=1, fixed_latency_ms=60.0,
           jet_force_n=0.06, policy="specialty", threshold=0.5)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def preserve_provenance(destination: Path, origin: str, command: list[str] | None = None) -> None:
    files = {
        "model_sha256": destination / "model" / f"{destination.name}.joblib",
        "report_sha256": destination / "train" / "report.json",
        "confusion_sha256": destination / "train" / "confusion.png",
    }
    actual = {key: sha256(path) for key, path in files.items()}
    path = destination / "artifact_provenance.json"
    if path.exists():
        recorded = load_json(path)
        mismatches = [key for key, value in actual.items() if recorded.get(key) != value]
        if recorded.get("training") != TRAINING:
            mismatches.append("training")
        if mismatches:
            raise RuntimeError(f"{destination.name} staged training artifacts changed: {', '.join(mismatches)}")
        return
    provenance = {"origin": origin, "training": TRAINING, **actual}
    if command is not None:
        provenance["command"] = command
    path.write_text(json.dumps(provenance, indent=2))


def validate_provenance(profile: str) -> None:
    destination = ROOT / profile
    path = destination / "artifact_provenance.json"
    if not path.exists():
        raise RuntimeError(f"{profile} staged training provenance is missing")
    recorded = load_json(path)
    files = {
        "model_sha256": destination / "model" / f"{profile}.joblib",
        "report_sha256": destination / "train" / "report.json",
        "confusion_sha256": destination / "train" / "confusion.png",
    }
    mismatches = [key for key, file in files.items()
                  if not file.exists() or recorded.get(key) != sha256(file)]
    if recorded.get("training") != TRAINING:
        mismatches.append("training")
    if mismatches:
        raise RuntimeError(f"{profile} staged training artifacts changed: {', '.join(mismatches)}")


def stage_green() -> None:
    source_model = HERE / "models" / "green_arabica.joblib"
    source_train = HERE / "runs" / "train_green_arabica"
    destination = ROOT / "green_arabica"
    model_dir = destination / "model"
    train_dir = destination / "train"
    staged = (model_dir / "green_arabica.joblib", train_dir / "report.json", train_dir / "confusion.png")
    if any(path.exists() for path in staged):
        if not all(path.exists() for path in staged):
            raise RuntimeError("green_arabica staged artifacts are incomplete")
    else:
        if not source_model.exists() or not (source_train / "report.json").exists():
            raise FileNotFoundError("green_arabica model/report are required before initial staging")
        model_dir.mkdir(parents=True, exist_ok=True)
        train_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_model, staged[0])
        for name in ("report.json", "confusion.png"):
            shutil.copy2(source_train / name, train_dir / name)
    preserve_provenance(destination, "pre-existing documented green_arabica training artifact")


def train_roasted() -> None:
    destination = ROOT / "roasted"
    train_dir = destination / "train"
    model_path = destination / "model" / "roasted.joblib"
    command = [sys.executable, "-u", "run.py", "train", "--profile", "roasted",
               "--seconds", str(TRAINING["seconds"]), "--rate", str(TRAINING["rate"]),
               "--boost", str(TRAINING["defect_boost"]), "--seed", str(TRAINING["seed"])]
    if model_path.exists() and (train_dir / "report.json").exists():
        preserve_provenance(destination, "stock run.py train CLI", command)
        print("roasted training artifacts already complete; skipping")
        return
    print("running stock training CLI:", " ".join(command), flush=True)
    subprocess.run(command, cwd=HERE, check=True)
    source_model = HERE / "models" / "roasted.joblib"
    source_train = HERE / "runs" / "train_roasted"
    if not source_model.exists() or not (source_train / "report.json").exists():
        raise RuntimeError("stock training CLI did not produce its documented artifacts")
    model_path.parent.mkdir(parents=True, exist_ok=True)
    train_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_model, model_path)
    for name in ("report.json", "confusion.png"):
        shutil.copy2(source_train / name, train_dir / name)
    preserve_provenance(destination, "stock run.py train CLI", command)
    print("saved", model_path)


def run_args(profile: str) -> argparse.Namespace:
    return argparse.Namespace(
        profile=profile, rate=RUN["rate"], seed=RUN["seed"], seconds=RUN["seconds"],
        policy=RUN["policy"], threshold=RUN["threshold"], no_anomaly=False,
        name=f"{profile}/run", controller_delay_ms=0.0,
        fixed_controller_latency_ms=RUN["fixed_latency_ms"],
        jet_force=RUN["jet_force_n"], base_pulse_ms=3.0, target_nozzles=None,
        split_z_drop=run.Layout.split_z_drop, nozzles=run.Layout.n_nozzles,
        pool_ellipsoid=run.Layout.n_ellipsoid, pool_half=run.Layout.n_half,
        pool_box=run.Layout.n_box, pool_capsule=run.Layout.n_capsule, video=False,
    )


def expected_run_config() -> dict:
    args = run_args("green_arabica")
    layout = run.Layout(n_nozzles=args.nozzles, split_z_drop=args.split_z_drop,
                        n_ellipsoid=args.pool_ellipsoid, n_half=args.pool_half,
                        n_box=args.pool_box, n_capsule=args.pool_capsule)
    policy = replace(SPECIALTY, threshold=args.threshold, anomaly=not args.no_anomaly,
                     base_pulse=args.base_pulse_ms / 1000,
                     induced_delay=args.controller_delay_ms / 1000,
                     fixed_latency=args.fixed_controller_latency_ms / 1000,
                     target_nozzles=args.target_nozzles)
    config = {"layout": asdict(layout), "policy": asdict(policy),
              "jet_force_n": args.jet_force, "requested_rate_beans_per_s": args.rate}
    return json.loads(json.dumps(config))


def validate_metrics_payload(profile: str, metrics: dict, model_hash: str) -> None:
    checks = {
        "profile": metrics.get("profile") == profile,
        "classifier": metrics.get("classifier_sha256") == model_hash,
        "seed": metrics.get("seed") == RUN["seed"],
        "rate": metrics.get("rate") == RUN["rate"],
        "seconds": metrics.get("seconds") == RUN["seconds"],
        "policy": metrics.get("policy") == RUN["policy"],
        "threshold": metrics.get("threshold") == RUN["threshold"],
        "config": metrics.get("config") == expected_run_config(),
    }
    failed = [name for name, valid in checks.items() if not valid]
    if failed:
        raise RuntimeError(f"{profile} complete run does not match experiment: {', '.join(failed)}")


def validated_metrics(profile: str) -> dict | None:
    validate_provenance(profile)
    output = ROOT / profile / "run"
    metrics = completed_metrics(output, RUN["seconds"])
    if metrics is None:
        return None
    expected_model = sha256(ROOT / profile / "model" / f"{profile}.joblib")
    validate_metrics_payload(profile, metrics, expected_model)
    return metrics


def run_pair() -> None:
    for profile in ("green_arabica", "roasted"):
        output = ROOT / profile / "run" / "metrics.json"
        if validated_metrics(profile):
            print(profile, "closed-loop artifacts already complete; skipping")
            continue
        if output.parent.exists():
            raise RuntimeError(f"incomplete run directory requires inspection: {output.parent}")
        run.MODELS = ROOT / profile / "model"
        run.RUNS = ROOT
        run.cmd_run(run_args(profile))


def load_json(path: Path) -> dict:
    return json.loads(path.read_text())


def metric_row(metrics: dict) -> dict:
    per_class = metrics["per_class"]
    defects_rejected = sum(row["rejected"] for row in per_class.values() if row["defect"])
    keep_rejected = sum(row["rejected"] for row in per_class.values() if not row["defect"])
    spilled = sum(row["spilled"] for row in per_class.values())
    denominators = metrics["denominators"]
    return {
        "eligible_beans": denominators["eligible_beans"],
        "resolved_beans": denominators["resolved_beans"],
        "defects_to_remove": denominators["defects_to_remove"],
        "keep_beans": denominators["keep_beans"],
        "rejected_beans": denominators["rejected_beans"],
        "physical_accuracy": metrics["physical_reject_accuracy"],
        "physical_reject_recall": metrics["physical_reject_recall"],
        "physical_reject_precision": metrics["physical_reject_precision"],
        "good_false_eject_rate": metrics["good_false_eject_rate"],
        "spilled_rate": metrics["spilled_rate"],
        "late_reject_rate": metrics["late_decisions"] / max(metrics["reject_decisions"], 1),
        "throughput_beans_per_s": metrics["throughput_beans_per_s"],
        "pool_starved": metrics["pool_starved"],
        "wall_seconds": metrics["wall_seconds"],
        "measured_compute_ms": metrics["measured_compute_ms"],
        "latency_ms": metrics["latency_ms"],
        "counts": {
            "defects_rejected": defects_rejected,
            "keep_rejected": keep_rejected,
            "spilled": spilled,
            "late_reject_decisions": metrics["late_decisions"],
            "reject_decisions": metrics["reject_decisions"],
        },
        "intervals_95": metrics["intervals_95"],
        "classifier_sha256": metrics["classifier_sha256"],
        "source_sha256": metrics["source_sha256"],
    }


def confusion_figure(reports: dict[str, dict]) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(17, 7), constrained_layout=True)
    for ax, (profile, report) in zip(axes, reports.items()):
        matrix = np.asarray(report["confusion"])
        normalized = matrix / np.maximum(matrix.sum(axis=1, keepdims=True), 1)
        ax.imshow(normalized, cmap="Blues", vmin=0, vmax=1)
        labels = report["classes"]
        ax.set_xticks(range(len(labels)), labels, rotation=45, ha="right")
        ax.set_yticks(range(len(labels)), labels)
        for row in range(len(labels)):
            for column in range(len(labels)):
                ax.text(column, row, str(matrix[row, column]), ha="center", va="center", fontsize=7,
                        color="white" if normalized[row, column] > 0.5 else "black")
        ax.set_title(f"{profile}: {report['accuracy']:.2%} ({report['n_test']:,} held-out blobs)")
        ax.set_xlabel("Predicted class")
        ax.set_ylabel("True class")
    fig.suptitle("Matched synthetic training: held-out blob confusion", fontsize=16)
    fig.text(0.5, 0.005, "Blob-level random holdout: repeated views of one bean may appear in train and test.",
             ha="center", fontsize=10)
    fig.savefig(COMPARISON / "confusion_matrices.png", dpi=150)
    plt.close(fig)


def camera_panel(profile_name: str) -> np.ndarray:
    profile = PROFILES[profile_name]
    sim = SorterSim(profile, rate=0, seed=17)
    beans = [sim.spawn(spec) for spec in profile.classes]
    if any(bean is None for bean in beans):
        raise RuntimeError(f"could not stage every {profile_name} class")
    for y, bean in zip(np.linspace(-0.225, 0.225, len(beans)), beans):
        qa, va = sim.body_qpos[bean.body], sim.body_qvel[bean.body]
        sim.data.qpos[qa:qa + 3] = [sim.L.cam_x, y, sim.L.belt_z + bean.axes[2] + 0.001]
        sim.data.qvel[va:va + 6] = 0
    mujoco.mj_forward(sim.model, sim.data)
    inspector = Inspector(sim)
    frame, timestamp = inspector.capture()
    blobs = inspector.detect(frame, timestamp)
    members = inspector.component_members(blobs)
    uid_to_class = {bean.uid: bean.cls for bean in beans}
    truth = [uid_to_class[int(group[0])] if len(group) == 1 else None for group in members]
    known_indices = [index for index, name in enumerate(truth) if name is not None]
    if {truth[index] for index in known_indices} != set(profile.names) or len(known_indices) != len(profile.names):
        raise RuntimeError(f"camera strip did not isolate all {profile_name} classes: {truth}")
    model = Model.load(ROOT / profile_name / "model" / f"{profile_name}.joblib")
    probabilities, _ = model.predict(blobs.X)
    predicted = [model.classes[int(row.argmax())] for row in probabilities]
    labels = [f"{actual}>{guess}" if actual is not None else "" for actual, guess in zip(truth, predicted)]
    strip = draw_blobs(frame, blobs, labels)

    width, tile_width, tile_height = frame.shape[1], 400, 145
    rows = (len(blobs.bbox) + 4) // 5
    panel = np.full((255 + rows * tile_height, width, 3), 246, np.uint8)
    cv2.putText(panel, f"{profile_name} | truth > prediction", (18, 32),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (25, 25, 25), 2, cv2.LINE_AA)
    panel[50:50 + frame.shape[0]] = strip
    order = sorted(known_indices, key=lambda i: profile.names.index(truth[i]))
    for tile, index in enumerate(order):
        x, y, w, h = map(int, blobs.bbox[index])
        crop = frame[max(0, y - 6):min(frame.shape[0], y + h + 6),
                     max(0, x - 6):min(frame.shape[1], x + w + 6)]
        x0, y0 = (tile % 5) * tile_width + 10, 250 + (tile // 5) * tile_height
        scale = min(110 / crop.shape[1], 90 / crop.shape[0])
        resized = cv2.resize(crop, (max(1, round(crop.shape[1] * scale)),
                                    max(1, round(crop.shape[0] * scale))), interpolation=cv2.INTER_AREA)
        crop_y = y0 + (90 - resized.shape[0]) // 2
        crop_x = x0 + (110 - resized.shape[1]) // 2
        panel[crop_y:crop_y + resized.shape[0], crop_x:crop_x + resized.shape[1]] = resized
        confidence = probabilities[index].max()
        cv2.putText(panel, truth[index], (x0 + 120, y0 + 34), cv2.FONT_HERSHEY_SIMPLEX,
                    0.55, (25, 25, 25), 1, cv2.LINE_AA)
        cv2.putText(panel, f"pred {predicted[index]} {confidence:.2f}", (x0 + 120, y0 + 62),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (45, 45, 45), 1, cv2.LINE_AA)
    inspector.close()
    return panel


def contact_sheet() -> None:
    panels = [camera_panel(profile) for profile in ("green_arabica", "roasted")]
    width = max(panel.shape[1] for panel in panels)
    output = np.full((sum(panel.shape[0] for panel in panels) + 28, width, 3), 230, np.uint8)
    y = 0
    for panel in panels:
        output[y:y + panel.shape[0], :panel.shape[1]] = panel
        y += panel.shape[0] + 28
    if not cv2.imwrite(str(COMPARISON / "camera_strip_contact_sheet.png"),
                       cv2.cvtColor(output, cv2.COLOR_RGB2BGR)):
        raise RuntimeError("could not write camera contact sheet")


def commit_sha(path: Path, commit: str = BASE_COMMIT) -> str:
    data = subprocess.check_output(["git", "show", f"{commit}:sim/coffee_sorter/{path.name}"], cwd=HERE)
    return hashlib.sha256(data).hexdigest()


def summarize() -> None:
    COMPARISON.mkdir(parents=True, exist_ok=True)
    profiles = ("green_arabica", "roasted")
    reports = {profile: load_json(ROOT / profile / "train" / "report.json") for profile in profiles}
    raw_metrics = {profile: validated_metrics(profile) for profile in profiles}
    if any(metrics is None for metrics in raw_metrics.values()):
        raise RuntimeError("both complete matched closed-loop runs are required before summarizing")
    summary = {
        "method": "same controller, vision, physics, policy, seed, feed rate, duration, and actuator settings",
        "training": TRAINING,
        "closed_loop": RUN,
        "profiles": {profile: {
            "training": {
                key: reports[profile][key] for key in
                ("n_train", "n_test", "accuracy", "defect_recall", "good_false_reject", "per_class")
            },
            "closed_loop": metric_row(raw_metrics[profile]),
        } for profile in profiles},
    }
    (COMPARISON / "metrics.json").write_text(json.dumps(summary, indent=2))
    confusion_figure(reports)
    contact_sheet()

    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=HERE, text=True).strip()
    methodology = {
        "commit": commit,
        "protected_source_reference_commit": BASE_COMMIT,
        "training": TRAINING,
        "closed_loop": RUN,
        "run_order": list(profiles),
        "green_training_source": "existing artifact from the same documented 24 s / 900 s^-1 / boost 5 recipe",
        "camera_contact_sheet": "real MuJoCo inspection-camera frames with one staged, isolated instance per profile class; ground-truth labels are evaluation-only",
        "holdout_limit": "blob-level random split can place repeated views of a bean in both train and test",
        "timing_limit": "single sequential run per profile on a shared host; fixed latency is a simulated minimum and measured compute can exceed it",
        "versions": {
            "python": platform.python_version(), "mujoco": mujoco.__version__,
            "numpy": np.__version__, "opencv": cv2.__version__, "scikit_learn": sklearn.__version__,
        },
    }
    (COMPARISON / "methodology.json").write_text(json.dumps(methodology, indent=2))

    current = {name: sha256(HERE / name) for name in PROVENANCE_SOURCES}
    reference = {name: commit_sha(HERE / name) for name in PROVENANCE_SOURCES}
    integrity = {
        "current": current,
        "reference_commit": BASE_COMMIT,
        "reference": reference,
        "matches_reference": {name: current[name] == reference[name] for name in PROVENANCE_SOURCES},
        "run_metric_hashes": {profile: {name: raw_metrics[profile]["source_sha256"][name]
                                          for name in SHARED_SOURCES} for profile in profiles},
    }
    integrity["protected_sources_identical"] = all(
        integrity["matches_reference"][name] for name in SHARED_SOURCES)
    integrity["all_identical"] = integrity["protected_sources_identical"] and all(
        all(hashes[name] == current[name] for name in SHARED_SOURCES)
        for hashes in integrity["run_metric_hashes"].values())
    (COMPARISON / "shared_source_integrity.json").write_text(json.dumps(integrity, indent=2))
    if not integrity["all_identical"]:
        raise RuntimeError("shared controller/vision/simulation/runner source changed")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("train", "run", "summarize", "all"), default="all", nargs="?")
    args = parser.parse_args()
    assets.build()
    stage_green()
    if args.stage in ("train", "all"):
        train_roasted()
    if args.stage in ("run", "all"):
        run_pair()
    if args.stage in ("summarize", "all"):
        summarize()


if __name__ == "__main__":
    main()
