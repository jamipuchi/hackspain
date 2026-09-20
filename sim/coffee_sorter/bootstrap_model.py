"""Train or validate the isolated green-coffee model used by the live demo."""
from __future__ import annotations

import os

for _name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[_name] = "1"

import argparse
import hashlib
import json
import math
import platform
import time
from collections import Counter
from dataclasses import asdict
from pathlib import Path

import cv2
import joblib
import mujoco
import numpy as np
import scipy
import sklearn
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import confusion_matrix

from assets import ASSETS, FAMILIES, N_VARIANTS
from classifier import MODELS, Model
from profiles import PROFILES
from scene import Layout
from sim import SorterSim
from vision import FEATURES, Inspector


HERE = Path(__file__).resolve().parent
SOURCE_FILES = ("assets.py", "bootstrap_model.py", "classifier.py", "profiles.py", "scene.py", "sim.py", "vision.py", "requirements.txt")
TRAIN_SEED = 7
HOLDOUT_SEED = 9
CAPTURE_EVERY = 2
POOL = dict(n_ellipsoid=400, n_half=64, n_box=32, n_capsule=32)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def consumed_assets() -> dict[str, str]:
    names = ["half_bean.obj", *[f"tex_{family}_{index}.png" for family in FAMILIES for index in range(N_VARIANTS)]]
    missing = [name for name in names if not (ASSETS / name).is_file()]
    if missing:
        raise RuntimeError(
            f"Missing committed simulator assets: {', '.join(missing)}. "
            "Restore them with `python sim/coffee_sorter/assets.py` before bootstrapping."
        )
    return {name: sha256(ASSETS / name) for name in names}


def provenance(config: dict) -> dict:
    return {
        "config": config,
        "source": {name: sha256(HERE / name) for name in SOURCE_FILES},
        "assets": consumed_assets(),
        "runtime": {
            # The plain version, never sys.version: its build date is a timestamp, and a
            # hashed or bundled manifest carries no timestamp.
            "python": platform.python_version(),
            "implementation": platform.python_implementation(),
            "platform": platform.platform(),
            "packages": {
                "numpy": np.__version__,
                "scikit_learn": sklearn.__version__,
                "mujoco": mujoco.__version__,
                "opencv": cv2.__version__,
                "joblib": joblib.__version__,
                "scipy": scipy.__version__,
            },
        },
    }


def collect_partition(seed: int, seconds: float, rate: float, defect_boost: float,
                      layout: Layout | None = None, capture_every: int = CAPTURE_EVERY,
                      profile=None):
    profile = profile or PROFILES["green_arabica"]
    sim = SorterSim(profile, layout or Layout(**POOL), rate=rate, seed=seed, defect_boost=defect_boost)
    inspector = Inspector(sim)
    rows: list[tuple[int, int, str]] = []
    features: list[np.ndarray] = []
    labels: list[str] = []
    try:
        for step in range(int(seconds / sim.dt)):
            sim.step()
            if step % capture_every:
                continue
            frame, exposure_t = inspector.capture()
            blobs = inspector.detect(frame, exposure_t)
            for index, members in enumerate(inspector.component_members(blobs)):
                uids = np.unique(members)
                if blobs.partial[index] or len(uids) != 1:
                    continue
                uid = int(uids[0])
                bean = sim.bean_by_uid.get(uid)
                if bean is None:
                    continue
                rows.append((seed, uid, bean.cls))
                features.append(blobs.X[index].copy())
                labels.append(bean.cls)
    finally:
        inspector.close()
    return np.asarray(features, dtype=float), np.asarray(labels, dtype=object), rows


def require_classes(name: str, labels: np.ndarray, classes: list[str]) -> None:
    missing = [label for label in classes if label not in set(labels)]
    if missing:
        raise RuntimeError(
            f"{name} collection missed classes: {', '.join(missing)}. "
            "Increase --seconds before using this model."
        )


def require_finite(name: str, values: np.ndarray) -> None:
    if not np.isfinite(values).all():
        raise RuntimeError(f"{name} contains non-finite values. Check simulator output before using this model.")


def unique_object_counts(rows: list[tuple[int, int, str]]) -> dict[str, int]:
    return dict(Counter(label for _, _, label in set(rows)))


def fit_model(profile, X_train: np.ndarray, y_train: np.ndarray, X_holdout: np.ndarray, y_holdout: np.ndarray, meta: dict) -> tuple[Model, dict]:
    classes = profile.names
    class_id = {name: index for index, name in enumerate(classes)}
    y_train_id = np.asarray([class_id[name] for name in y_train])
    y_holdout_id = np.asarray([class_id[name] for name in y_holdout])
    clf = HistGradientBoostingClassifier(
        max_iter=300,
        learning_rate=0.08,
        max_leaf_nodes=31,
        l2_regularization=0.05,
        early_stopping=False,
        random_state=TRAIN_SEED,
    )
    started = time.perf_counter()
    clf.fit(X_train, y_train_id)
    fit_seconds = time.perf_counter() - started
    good = X_train[y_train == "good"]
    if len(good) < 2:
        raise RuntimeError("Training collection needs at least two good observations for anomaly covariance.")
    scale = good.std(0) + 1e-6
    normalized_good = (good - good.mean(0)) / scale
    covariance = np.cov(normalized_good.T) + 0.05 * np.eye(normalized_good.shape[1])
    inverse_covariance = np.linalg.inv(covariance)
    distances = np.sqrt(np.einsum("ij,jk,ik->i", normalized_good, inverse_covariance, normalized_good))
    threshold = float(np.percentile(distances, 99.7))
    require_finite("Train-only anomaly statistics", np.concatenate((good.mean(0), scale, covariance.ravel(), inverse_covariance.ravel(), distances)))
    if not math.isfinite(threshold):
        raise RuntimeError("Train-only anomaly threshold is non-finite. Check simulator output before using this model.")
    model = Model(classes, clf, good.mean(0), inverse_covariance, threshold, scale, meta)

    started = time.perf_counter()
    probabilities, anomaly = model.predict(X_holdout)
    predict_seconds = time.perf_counter() - started
    predicted = np.argmax(probabilities, axis=1)
    is_defect = np.asarray([profile.by_name(name).defect for name in classes])
    truth_defect = is_defect[y_holdout_id]
    predicted_defect = is_defect[predicted]
    report = {
        "fit_seconds": fit_seconds,
        "classifier_holdout_observation_accuracy": float((predicted == y_holdout_id).mean()),
        "classifier_holdout_observation_defect_recall": float((truth_defect & predicted_defect).sum() / max(truth_defect.sum(), 1)),
        "classifier_holdout_observation_good_false_defect": float((~truth_defect & predicted_defect).sum() / max((~truth_defect).sum(), 1)),
        "holdout_measurement": "observation-weighted holdout of separate seed-9 objects",
        "holdout_classification_scope": "argmax classifier output excludes anomaly and is not controller or physical quality",
        "holdout_predict_ms_per_40": predict_seconds / max(len(X_holdout), 1) * 40_000,
        "anomaly_threshold": threshold,
        "holdout_anomaly_p99": float(np.percentile(anomaly, 99)),
        "confusion": confusion_matrix(y_holdout_id, predicted, labels=range(len(classes))).tolist(),
    }
    return model, report


def artifact_paths(output: Path) -> tuple[Path, Path]:
    return output.with_suffix(".manifest.json"), output.with_suffix(".report.json")


def reusable(output: Path, manifest_path: Path, expected: dict, classes: list[str]) -> bool:
    if not output.is_file() or not manifest_path.is_file():
        return False
    try:
        manifest = json.loads(manifest_path.read_text())
        if manifest.get("provenance") != expected or manifest.get("artifact_sha256") != sha256(output):
            return False
        return Model.load(output).classes == classes
    except Exception:
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=4.0, help="simulation seconds for each independent partition")
    parser.add_argument("--output", type=Path, default=MODELS / "live_green_arabica.joblib")
    parser.add_argument("--preset", type=Path, default=HERE / "configs" / "default_demo.json")
    args = parser.parse_args()
    if not math.isfinite(args.seconds) or args.seconds <= 0:
        parser.error("--seconds must be finite and positive")

    preset_path = args.preset.resolve()
    preset = json.loads(preset_path.read_text())
    if preset.get("profile") != "green_arabica":
        parser.error("--preset must use the green_arabica profile")
    layout = Layout(**preset["layout"])
    rate = float(preset["requested_rate"])
    capture_every = preset["camera_every_steps"]
    if not math.isfinite(rate) or rate <= 0:
        parser.error("preset requested_rate must be finite and positive")
    if isinstance(capture_every, bool) or not isinstance(capture_every, int) or capture_every <= 0:
        parser.error("preset camera_every_steps must be a positive integer")

    config = {
        "profile": "green_arabica",
        "train_seed": TRAIN_SEED,
        "holdout_seed": HOLDOUT_SEED,
        "seconds_per_partition": args.seconds,
        "rate": rate,
        "defect_boost": 5,
        "capture_every": capture_every,
        "physical_preset": {
            "name": preset.get("name"),
            "version": preset.get("version"),
            "sha256": sha256(preset_path),
            "layout": asdict(layout),
            "requested_rate": rate,
            "camera_every_steps": capture_every,
            "capture_hz": 1.0 / (layout.timestep * capture_every),
        },
    }
    expected = provenance(config)
    output = args.output.resolve()
    manifest_path, report_path = artifact_paths(output)
    profile = PROFILES["green_arabica"]
    if reusable(output, manifest_path, expected, profile.names):
        print(json.dumps({"status": "reused", "model": str(output), "sha256": sha256(output)}))
        return

    started = time.perf_counter()
    X_train, y_train, train_rows = collect_partition(
        TRAIN_SEED, args.seconds, rate, 5, layout, capture_every
    )
    X_holdout, y_holdout, holdout_rows = collect_partition(
        HOLDOUT_SEED, args.seconds, rate, 5, layout, capture_every
    )
    require_finite("Training features", X_train)
    require_finite("Holdout features", X_holdout)
    require_classes("training", y_train, profile.names)
    require_classes("holdout", y_holdout, profile.names)
    meta = {
        "profile": profile.name,
        "classes": profile.names,
        "features": FEATURES,
        "provenance": expected,
        "training_rows": len(train_rows),
        "holdout_rows": len(holdout_rows),
    }
    model, report = fit_model(profile, X_train, y_train, X_holdout, y_holdout, meta)
    output.parent.mkdir(parents=True, exist_ok=True)
    model.save(output)
    manifest = {
        "version": 1,
        "provenance": expected,
        "artifact_sha256": sha256(output),
        "features": FEATURES,
        "partitions": {
            "train": {"seed": TRAIN_SEED, "rows": [list(row) for row in train_rows],
                      "unique_object_counts": unique_object_counts(train_rows)},
            "holdout": {"seed": HOLDOUT_SEED, "rows": [list(row) for row in holdout_rows],
                        "unique_object_counts": unique_object_counts(holdout_rows)},
        },
    }
    report.update({
        "model": str(output),
        "artifact_sha256": manifest["artifact_sha256"],
        "training_counts": dict(Counter(y_train)),
        "holdout_counts": dict(Counter(y_holdout)),
        "training_unique_object_counts": unique_object_counts(train_rows),
        "holdout_unique_object_counts": unique_object_counts(holdout_rows),
        "wall_seconds": time.perf_counter() - started,
    })
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True))
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True))
    print(json.dumps({"status": "trained", "model": str(output), **report}, sort_keys=True))


if __name__ == "__main__":
    main()
