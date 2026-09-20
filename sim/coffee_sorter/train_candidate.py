"""Train and validate one candidate model for a candidate object catalog.

This runs as a child process. It reads a candidate catalog root and a reviewed
preset, collects two independent partitions, fits one model, and writes the
candidate artifacts next to a strict `validation.json`.

The gate is strict and unchanged: no anomaly logic and no threshold moves here.
Four kinds of evidence stay separate in `validation.json` and none is derived
from another: classifier metrics, anomaly decision numbers, commanded valve
pulses, and physical outcomes (`keep_outcome`). The `keep_outcome` rule is a
minimum engineering release gate, not a statistical accuracy claim. Every tested
seed and its resolved count is recorded, including failed runs.

`keep_outcome` takes an already constructed engine-like object, so a unit test
can drive it with a fake engine. `main` builds the real `Engine` from a TEMPORARY
preset that carries an ABSOLUTE `model_path`, because `engine.py` resolves a
relative `model_path` against the source directory and this module never edits
it. That temporary preset exists for one in-process run. It is never hashed,
never bundled, and it is deleted afterwards. The bundled `candidate.preset.json`
keeps the relative `"model_path": "candidate.joblib"` instead.

Nothing here calls a provider, reads a preview, or reads a beauty render.
"""
from __future__ import annotations

import os

for _name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[_name] = "1"

import argparse
import fcntl
import json
import math
import platform
import sys
import tempfile
import time
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping

import numpy as np

from bootstrap_model import (
    HOLDOUT_SEED,
    TRAIN_SEED,
    collect_partition,
    fit_model,
    provenance,
    require_classes,
    require_finite,
    sha256,
    unique_object_counts,
)
from object_catalog import CatalogError, load_catalog, require_label_order
from profiles import profile_from_catalog
from scene import Layout
from vision import FEATURES

RUNTIME_LOCK = Path("/private/tmp/hackspain-coffee-runtime.lock")
LOCK_BUSY_EXIT = 75
DEFECT_BOOST = 5
MIN_HOLDOUT_UNIQUE = 10
MIN_ACCURACY = 0.90
MIN_RECALL = 0.90
MAX_KEEP_ANOMALY_FRACTION = 0.05
MIN_KEEP_RESOLVED = 30
MIN_KEEP_ACCEPT_FRACTION = 0.95
KEEP_OUTCOME_SIM_SECONDS = 20.0
PHASES = ("collect_train", "collect_holdout", "fit", "validate")


def write_atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")
    os.replace(temporary, path)


def write_progress(out: Path, phase: str, fraction: float) -> None:
    """The queue publishes this file, so every phase writes it atomically."""
    write_atomic_json(out / "progress.json", {"phase": phase, "fraction": fraction})


def coverage(rows: list[tuple[int, int, str]]) -> tuple[dict[str, int], dict[str, int]]:
    """Observations and unique objects per label. They are separate numbers.

    One unique object is one (seed, uid) pair within one collection run.
    """
    observations = dict(Counter(label for _, _, label in rows))
    return observations, unique_object_counts(rows)


def holdout_metrics(classes: list[str], y_true: np.ndarray, probabilities: np.ndarray,
                    new_label: str) -> dict[str, float]:
    """Classifier evidence only. It excludes the anomaly path and every physical outcome."""
    if len(y_true) == 0:
        return {"holdout_accuracy": 0.0, "new_label_recall": 0.0, "observations": 0}
    predicted = np.asarray([classes[index] for index in np.argmax(probabilities, axis=1)], dtype=object)
    truth = np.asarray(y_true, dtype=object)
    selected = truth == new_label
    return {
        "holdout_accuracy": float((predicted == truth).mean()),
        "new_label_recall": float((predicted[selected] == new_label).mean()) if selected.any() else 0.0,
        "observations": int(len(truth)),
    }


def anomaly_evidence(scores: np.ndarray, threshold: float, selected: np.ndarray) -> dict[str, Any]:
    """Anomaly decision numbers for one label. No threshold changes in this phase."""
    values = np.asarray(scores, dtype=float)[np.asarray(selected, dtype=bool)]
    if len(values) == 0:
        return {"fraction_above_threshold": 1.0, "median": None,
                "threshold": float(threshold), "observations": 0}
    return {
        "fraction_above_threshold": float((values > threshold).mean()),
        "median": float(np.median(values)),
        "threshold": float(threshold),
        "observations": int(len(values)),
    }


def keep_outcome(engine, label: str, *, seed: int, min_resolved: int = MIN_KEEP_RESOLVED,
                 max_sim_seconds: float = KEEP_OUTCOME_SIM_SECONDS) -> dict[str, Any]:
    """Physical outcomes of one seeded closed-loop run, with the controller and the jets on.

    `engine` is injected. It needs `step()`, `sim.dt`, and `sim.bean_of` values that
    carry `uid`, `cls`, `outcome`, `targeted`, `fired_target`, and `jet_hits`. The
    returned outcomes and pulses stay in separate blocks: a commanded pulse is never
    an outcome, and this run never touches a classifier number.
    """
    outcomes, pulses, seen = Counter(), Counter(), set()
    steps, ran = int(max_sim_seconds / engine.sim.dt), 0
    for _ in range(steps):
        engine.step()
        ran += 1
        for bean in list(engine.sim.bean_of.values()):
            if bean.cls != label or bean.outcome is None or bean.uid in seen:
                continue
            seen.add(bean.uid)
            outcomes[bean.outcome] += 1
            pulses["commanded"] += int(bool(bean.targeted))
            pulses["activated"] += int(bool(bean.fired_target))
            pulses["jet_hits"] += int(bean.jet_hits)
        if len(seen) >= min_resolved:
            break
    resolved = len(seen)
    return {
        "seed": seed,
        "label": label,
        "sim_seconds": ran * engine.sim.dt,
        "outcomes": {
            "resolved": resolved,
            "accepted": outcomes["accept"],
            "rejected": outcomes["reject"],
            "spilled": outcomes["spilled"],
            "accept_fraction": outcomes["accept"] / resolved if resolved else 0.0,
        },
        "pulses": dict(commanded=pulses["commanded"], activated=pulses["activated"],
                       jet_hits=pulses["jet_hits"]),
    }


def label_order_ok(catalog, model_classes) -> bool:
    """A model is compatible only when its labels equal the newest-first catalog order."""
    try:
        require_label_order(catalog, list(model_classes))
    except CatalogError:
        return False
    return True


def gate_failures(validation: Mapping[str, Any]) -> list[str]:
    """The strict gate over assembled evidence. It is never weakened to force an activation."""
    failures = []
    if not validation["label_order_ok"]:
        failures.append("label_order")
    classifier = validation["classifier"]
    unique = classifier["holdout_unique_objects"]
    if any(unique.get(label, 0) < MIN_HOLDOUT_UNIQUE for label in validation["labels"]):
        failures.append("holdout_coverage")
    if classifier["holdout_accuracy"] < MIN_ACCURACY:
        failures.append("holdout_accuracy")
    if classifier["new_label_recall"] < MIN_RECALL:
        failures.append("new_label_recall")
    if validation["new_label_is_keep"]:
        # Label confidence alone never proves compatibility: the anomaly path and the
        # physical outcomes gate a Keep type as well.
        if validation["anomaly"]["fraction_above_threshold"] > MAX_KEEP_ANOMALY_FRACTION:
            failures.append("anomaly_fraction")
        runs = (validation["keep_outcome"] or {}).get("runs") or []
        if not runs:
            failures.append("keep_outcome_missing")
        else:
            latest = runs[-1]
            if latest["resolved"] < MIN_KEEP_RESOLVED:
                failures.append("keep_outcome_resolved")
            if latest["accept_fraction"] < MIN_KEEP_ACCEPT_FRACTION:
                failures.append("keep_outcome_accept_fraction")
    return failures


def acquire_lock(path: Path):
    """Take the shared exclusive runtime lock without blocking. None means another run holds it."""
    handle = open(path, "a")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        return None
    return handle


def build_engine(preset: Mapping[str, Any], model_path: Path, directory: Path):
    """Build the real Engine from a temporary preset that carries an absolute model path.

    `engine.py` resolves a relative `model_path` against the source directory and this
    module never edits it. The temporary preset is never hashed and never bundled.
    """
    from engine import Engine

    temporary = directory / "keep-outcome.preset.json"
    temporary.write_text(json.dumps({**dict(preset), "model_path": str(model_path)}))
    return Engine(temporary)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--catalog-root", type=Path, required=True)
    parser.add_argument("--preset", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--seconds", type=float, default=4.0)
    parser.add_argument("--runtime-lock", type=Path, default=RUNTIME_LOCK)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not math.isfinite(args.seconds) or args.seconds <= 0:
        parser.error("--seconds must be finite and positive")

    preset_path = args.preset.resolve()
    preset = json.loads(preset_path.read_text())
    layout = Layout(**preset["layout"])
    rate = float(preset["requested_rate"])
    capture_every = preset["camera_every_steps"]
    if not math.isfinite(rate) or rate <= 0:
        parser.error("preset requested_rate must be finite and positive")
    if isinstance(capture_every, bool) or not isinstance(capture_every, int) or capture_every <= 0:
        parser.error("preset camera_every_steps must be a positive integer")

    catalog = load_catalog(args.catalog_root)
    profile = profile_from_catalog(catalog)
    if profile.name != preset.get("profile"):
        parser.error("the candidate catalog profile does not match the preset profile")
    new_label = profile.names[0]  # newest first: the candidate leads the catalog order

    handle = acquire_lock(args.runtime_lock)
    if handle is None:
        print(f"runtime lock is occupied: {args.runtime_lock}. No training started.", file=sys.stderr)
        return LOCK_BUSY_EXIT
    try:
        return train(args, preset, preset_path, layout, rate, capture_every, catalog, profile, new_label)
    finally:
        fcntl.flock(handle, fcntl.LOCK_UN)
        handle.close()


def train(args, preset, preset_path: Path, layout: Layout, rate: float, capture_every: int,
          catalog, profile, new_label: str) -> int:
    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    config = {
        "profile": profile.name,
        "train_seed": TRAIN_SEED,
        "holdout_seed": HOLDOUT_SEED,
        "seconds_per_partition": args.seconds,
        "rate": rate,
        "defect_boost": DEFECT_BOOST,
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
    # `sys.version` carries an interpreter build date. A hashed or bundled file carries
    # no timestamp, so the manifest and the model meta share this plain version instead.
    expected["runtime"]["python"] = platform.python_version()

    write_progress(out, "collect_train", 0.0)
    X_train, y_train, train_rows = collect_partition(
        TRAIN_SEED, args.seconds, rate, DEFECT_BOOST, layout, capture_every, profile)
    write_progress(out, "collect_holdout", 0.35)
    X_holdout, y_holdout, holdout_rows = collect_partition(
        HOLDOUT_SEED, args.seconds, rate, DEFECT_BOOST, layout, capture_every, profile)
    require_finite("Training features", X_train)
    require_finite("Holdout features", X_holdout)
    require_classes("training", y_train, profile.names)
    require_classes("holdout", y_holdout, profile.names)

    write_progress(out, "fit", 0.7)
    meta = {
        "profile": profile.name,
        "classes": profile.names,
        "features": FEATURES,
        "provenance": expected,
        "training_rows": len(train_rows),
        "holdout_rows": len(holdout_rows),
    }
    model, report = fit_model(profile, X_train, y_train, X_holdout, y_holdout, meta)
    artifact = out / "candidate.joblib"
    model.save(artifact)
    train_observations, train_unique = coverage(train_rows)
    holdout_observations, holdout_unique = coverage(holdout_rows)
    manifest = {
        "version": 1,
        "provenance": expected,
        "artifact_sha256": sha256(artifact),
        "features": FEATURES,
        "partitions": {
            "train": {"seed": TRAIN_SEED, "rows": [list(row) for row in train_rows],
                      "unique_object_counts": train_unique},
            "holdout": {"seed": HOLDOUT_SEED, "rows": [list(row) for row in holdout_rows],
                        "unique_object_counts": holdout_unique},
        },
    }
    manifest_path = out / "candidate.manifest.json"
    write_atomic_json(manifest_path, manifest)
    # The bundled preset keeps a relative model path. It carries no absolute path.
    candidate_preset = {**dict(preset), "model_path": "candidate.joblib", "model_path_root": "preset"}
    preset_out = out / "candidate.preset.json"
    write_atomic_json(preset_out, candidate_preset)

    write_progress(out, "validate", 0.85)
    probabilities, anomaly = model.predict(X_holdout)
    metrics = holdout_metrics(profile.names, y_holdout, probabilities, new_label)
    is_keep = not profile.by_name(new_label).defect
    validation = {
        "labels": profile.names,
        "new_label": new_label,
        "new_label_is_keep": is_keep,
        "label_order_ok": label_order_ok(catalog, model.classes),
        "classifier": {
            "holdout_accuracy": metrics["holdout_accuracy"],
            "new_label_recall": metrics["new_label_recall"],
            "holdout_observations": holdout_observations,
            "holdout_unique_objects": holdout_unique,
            "train_observations": train_observations,
            "train_unique_objects": train_unique,
            "scope": "argmax classifier output; it excludes the anomaly path and every physical outcome",
        },
        "anomaly": {
            "label": new_label,
            **anomaly_evidence(anomaly, model.anomaly_thresh,
                               np.asarray(y_holdout, dtype=object) == new_label),
            "scope": "anomaly decision numbers only; the threshold and the reference are unchanged",
        },
        "pulses": None,
        "keep_outcome": None,
        "artifact_sha256": manifest["artifact_sha256"],
        "manifest_sha256": sha256(manifest_path),
        "preset_sha256": sha256(preset_out),
        "source": expected["source"],
        "catalog_revision": catalog["catalog_revision"],
        "gate": {
            "min_holdout_unique_objects": MIN_HOLDOUT_UNIQUE,
            "min_holdout_accuracy": MIN_ACCURACY,
            "min_new_label_recall": MIN_RECALL,
            "max_keep_anomaly_fraction": MAX_KEEP_ANOMALY_FRACTION,
            "min_keep_resolved": MIN_KEEP_RESOLVED,
            "min_keep_accept_fraction": MIN_KEEP_ACCEPT_FRACTION,
        },
    }
    if is_keep:
        record_keep_outcome(validation, preset, artifact, profile, new_label)
    validation["failures"] = gate_failures(validation)
    validation["passed"] = not validation["failures"]
    write_atomic_json(out / "validation.json", validation)

    report.update({
        "model": str(artifact),
        "artifact_sha256": manifest["artifact_sha256"],
        "training_counts": dict(Counter(y_train)),
        "holdout_counts": dict(Counter(y_holdout)),
        "training_unique_object_counts": train_unique,
        "holdout_unique_object_counts": holdout_unique,
        "candidate_feed_prior": profile.classes[0].prior,
        "wall_seconds": time.perf_counter() - started,
    })
    write_atomic_json(out / "candidate.report.json", report)
    write_progress(out, "validate", 1.0)
    # A completed validation is not a crash, so a failed gate still exits 0.
    print(json.dumps({"status": "trained", "passed": validation["passed"],
                      "failures": validation["failures"]}, sort_keys=True))
    return 0


def record_keep_outcome(validation: dict[str, Any], preset, artifact: Path, profile,
                        new_label: str) -> None:
    """Run one seeded closed-loop run and record its two evidence kinds separately."""
    import profiles

    # The Engine resolves its profile by name from PROFILES. The candidate binds a
    # distinct key, so a built-in entry is never overwritten, and the key goes away
    # again when the run ends. Only the temporary preset names that key.
    key = f"__candidate__{profile.name}"
    profiles.PROFILES[key] = profile
    seed = int(preset["seed"])
    try:
        with tempfile.TemporaryDirectory() as directory:
            engine = build_engine({**dict(preset), "profile": key}, artifact.resolve(),
                                  Path(directory))
            try:
                run = keep_outcome(engine, new_label, seed=seed)
            finally:
                engine.close()
    finally:
        profiles.PROFILES.pop(key, None)
    validation["keep_outcome"] = {
        "required": True,
        "runs": [{"seed": run["seed"], "sim_seconds": run["sim_seconds"], **run["outcomes"]}],
        "scope": ("physical outcomes only; a minimum engineering release gate, "
                  "not a statistical accuracy claim"),
    }
    validation["pulses"] = {
        "runs": [{"seed": run["seed"], **run["pulses"]}],
        "scope": "commanded valve pulses only; they never enter the gate and are never an outcome",
    }


if __name__ == "__main__":
    sys.exit(main())
