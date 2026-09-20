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
can drive it with a fake engine. The real closed-loop run and the final loader
proof both load the ACTUAL immutable `candidate.preset.json`, which carries
`"model_path": "candidate.joblib"` with `"model_path_root": "preset"`, so the
engine reads the model that sits beside it. No temporary preset and no absolute
path exist, and the hashed bytes never change.

The Engine and `live.load_preset` both resolve their profile by name, and both
must see the CANDIDATE labels. `bound_candidate_profile` therefore binds the
candidate catalog for the WHOLE validation scope and restores the previous entry
afterwards, even when something raises.

Nothing here calls a provider, reads a preview, or reads a beauty render.
"""
from __future__ import annotations

import os

for _name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[_name] = "1"

import argparse
import contextlib
import fcntl
import json
import math
import sys
import time
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping

import numpy as np

from bootstrap_model import (
    HOLDOUT_SEED,
    MIN_LABEL_OBSERVATIONS,
    MIN_LABEL_UNIQUE_OBJECTS,
    TRAIN_SEED,
    collect_covered,
    DURATION_FREEZE,
    STARTING_SECONDS,
    collection_rounds,
    fit_model,
    label_coverage,
    missing_references,
    provenance,
    require_classes,
    require_finite,
    sha256,
    short_labels,
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
# Demo-mode-only codes: root accepted these as review-worthy, not release-blocking, for a
# demo. No threshold changes, and gate_failures itself never changes.
QUALITY_WARNING_CODES = ("anomaly_fraction", "keep_outcome_accept_fraction",
                         "keep_outcome_resolved", "holdout_accuracy")


def write_atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")
    os.replace(temporary, path)


def write_progress(out: Path, phase: str, fraction: float) -> None:
    """The queue publishes this file, so every phase writes it atomically."""
    write_atomic_json(out / "progress.json", {"phase": phase, "fraction": fraction})


def coverage(rows: list[tuple[int, int, str]]) -> tuple[dict[str, int], dict[str, int]]:
    """Observations and unique objects per label, from the shared bootstrap helper."""
    return label_coverage(rows)


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

    `engine` is injected. It needs `step()`, `sim.dt`, and the retained `_object_records`
    (each carrying `object_id`, `truth_class`, `outcome`, `resolved_time_s`, `decisions`,
    `activated_rejection_tracks`, and `jet_hits`). A collector can remove a resolved body
    from `sim.bean_of` before a scan of the live bean map would see it, so outcomes are
    read from the retained records instead, never from `sim.bean_of` and never by draining
    a sim event or outcome queue a second time. A pulse was commanded when any retained
    decision for that object is both a reject and scheduled, and activated when its
    retained activated-tracks set is non-empty; neither is derived from a prediction or a
    physical outcome. The returned outcomes and pulses stay in separate blocks: a commanded
    pulse is never an outcome, and this run never touches a classifier number.
    """
    outcomes, seen = Counter(), set()
    commanded = activated = jet_hits = 0
    steps, ran = int(max_sim_seconds / engine.sim.dt), 0
    for _ in range(steps):
        engine.step()
        ran += 1
        for record in list(engine._object_records.values()):
            object_id = record["object_id"]
            if (record["truth_class"] != label or record["outcome"] is None
                    or object_id in seen):
                continue
            seen.add(object_id)
            outcomes[record["outcome"]] += 1
            commanded += int(any(decision["reject"] and decision["scheduled"]
                                 for decision in record["decisions"]))
            activated += int(bool(record["activated_rejection_tracks"]))
            jet_hits += int(record["jet_hits"])
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
        "pulses": dict(commanded=commanded, activated=activated, jet_hits=jet_hits),
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
    # The bundled preset must load through the service loader, or nothing can activate it.
    # A missing proof is a failure: absence of evidence is never evidence.
    if (validation.get("preset_compatibility") or {}).get("loaded") is not True:
        failures.append("preset_incompatible")
    classifier = validation["classifier"]
    unique = classifier["holdout_unique_objects"]
    if any(unique.get(label, 0) < MIN_HOLDOUT_UNIQUE for label in validation["labels"]):
        failures.append("holdout_coverage")
    if classifier["holdout_accuracy"] < MIN_ACCURACY:
        failures.append("holdout_accuracy")
    if classifier["new_label_recall"] < MIN_RECALL:
        failures.append("new_label_recall")
    # Every trained label owns an anomaly reference, because live policy can keep any of
    # them. A label that stays short after the bounded rounds blocks the model.
    reference = validation.get("reference_coverage") or {}
    if (short_labels(validation["labels"], reference.get("train_observations") or {},
                     reference.get("train_unique_objects") or {})
            or missing_references(reference.get("reference_labels") or [],
                                  validation["labels"])):
        failures.append("insufficient_label_coverage")
    # Keep or Reject is a policy, not physical truth. Activation always adds the new label
    # as Keep, so the anomaly path and the physical outcomes gate EVERY candidate, whatever
    # its truth.defect and truth.severity say. Label confidence alone proves nothing.
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


def split_quality_warnings(failures: list[str]) -> dict[str, Any]:
    """Split one `gate_failures` list for demo mode. `gate_failures` itself never changes.

    The `QUALITY_WARNING_CODES` move out of `failures` into `quality_warnings`, in the
    order `gate_failures` returned them. Every other code stays hard. `review_status`
    follows only from whether a warning exists.
    """
    warnings = [code for code in failures if code in QUALITY_WARNING_CODES]
    hard = [code for code in failures if code not in QUALITY_WARNING_CODES]
    return {"failures": hard, "quality_warnings": warnings,
            "review_status": "needs_review" if warnings else "clean"}


def resolve_policy(profile, new_label: str, preset: Mapping[str, Any],
                   policy_path: Path | None) -> tuple[list[str], str | None, str]:
    """The reject classes the closed-loop run applies. The new label is never rejected.

    The queue writes the baseline policy file from the training baseline. Without it the
    default derives from the preset severities over the SURVIVORS only. Returns
    (applied_reject_classes, baseline_policy_version, policy_source).
    """
    labels = set(profile.names)
    if policy_path is None:
        reject_severities = set(preset["policy"]["reject_severities"])
        derived = [item.name for item in profile.classes
                   if item.name != new_label and item.defect
                   and item.severity in reject_severities]
        return sorted(derived), None, "derived_default"
    value = json.loads(Path(policy_path).read_text())
    reject_classes = value.get("reject_classes") if isinstance(value, Mapping) else None
    if not isinstance(reject_classes, list) or any(not isinstance(name, str) for name in reject_classes):
        raise ValueError("policy reject_classes must be a list of candidate catalog labels")
    unknown = sorted(set(reject_classes) - labels)
    if unknown:
        raise ValueError(f"policy names labels outside the candidate catalog: {', '.join(unknown)}")
    if new_label in reject_classes:
        raise ValueError(f"the candidate always activates as Keep and can never be rejected: {new_label}")
    version = value.get("policy_version")
    if version is not None and not isinstance(version, str):
        raise ValueError("policy_version must be text or absent")
    return sorted(set(reject_classes)), version, "baseline_file"


def acquire_lock(path: Path):
    """Take the shared exclusive runtime lock without blocking. None means another run holds it."""
    handle = open(path, "a")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        return None
    return handle


@contextlib.contextmanager
def bound_candidate_profile(profile):
    """Bind the candidate catalog as the authoritative profile for this child process.

    The Engine and `live.load_preset` both resolve their profile by name from PROFILES,
    and both must see the CANDIDATE labels: the new type first, the victim gone. Every
    validation step that reads a profile therefore runs inside this one scope, and the
    previous entry is restored even when the body raises.
    """
    import profiles

    key, missing = profile.name, object()
    previous = profiles.PROFILES.get(key, missing)
    profiles.PROFILES[key] = profile
    try:
        yield profile
    finally:
        if previous is missing:
            profiles.PROFILES.pop(key, None)
        else:
            profiles.PROFILES[key] = previous


def preset_compatibility(preset_path: Path) -> dict[str, Any]:
    """Prove the bundled preset loads through the service loader itself.

    `live.load_preset` is the code the service runs, so it is the only honest proof. It is
    imported here, at this one call site, because importing it pulls aiohttp into this
    child. A refusal is recorded as evidence, never raised as a crash.
    """
    from live import load_preset

    try:
        load_preset(preset_path)
    except Exception as error:
        return {"loaded": False, "reason": f"{type(error).__name__}: {error}",
                "loader": "live.load_preset"}
    return {"loaded": True, "reason": None, "loader": "live.load_preset"}


def build_engine(preset_path: Path):
    """Build the real Engine from the immutable candidate preset itself.

    The candidate preset carries `"model_path": "candidate.joblib"` with
    `"model_path_root": "preset"`, so the engine loads the model beside it. No temporary
    preset and no absolute path exist any more, and the hashed bytes never change.
    """
    from engine import Engine

    return Engine(Path(preset_path))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--catalog-root", type=Path, required=True)
    parser.add_argument("--preset", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--seconds", type=float, default=STARTING_SECONDS)
    parser.add_argument("--runtime-lock", type=Path, default=RUNTIME_LOCK)
    parser.add_argument("--policy", type=Path,
                        help='training baseline policy: {"reject_classes": [...], "policy_version": "..."}')
    parser.add_argument("--quality-gate", choices=("strict", "demo"), default="strict",
                        help="strict blocks on every code (default). demo moves "
                             "QUALITY_WARNING_CODES to quality_warnings and keeps the rest hard.")
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
    # Refuse a wrong policy before the lock and before any heavy work.
    try:
        policy = resolve_policy(profile, new_label, preset, args.policy)
    except (OSError, ValueError) as error:
        parser.error(f"--policy is invalid: {error}")

    handle = acquire_lock(args.runtime_lock)
    if handle is None:
        print(f"runtime lock is occupied: {args.runtime_lock}. No training started.", file=sys.stderr)
        return LOCK_BUSY_EXIT
    try:
        return train(args, preset, preset_path, layout, rate, capture_every, catalog, profile,
                     new_label, policy)
    finally:
        fcntl.flock(handle, fcntl.LOCK_UN)
        handle.close()


def train(args, preset, preset_path: Path, layout: Layout, rate: float, capture_every: int,
          catalog, profile, new_label: str, policy: tuple[list[str], str | None, str]) -> int:
    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    config = {
        "profile": profile.name,
        # The candidate is bound to the catalog it was trained for, exactly like the
        # canonical model, so an activation can never pair a model with another revision.
        "catalog_revision": catalog["catalog_revision"],
        "train_seed": TRAIN_SEED,
        "holdout_seed": HOLDOUT_SEED,
        "seconds_per_partition": args.seconds,
        "starting_seconds": STARTING_SECONDS,
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

    rounds = collection_rounds(args.seconds)
    write_progress(out, "collect_train", 0.0)
    X_train, y_train, train_rows, train_history = collect_covered(
        TRAIN_SEED, rate, DEFECT_BOOST, layout, capture_every, profile, rounds)
    write_progress(out, "collect_holdout", 0.35)
    X_holdout, y_holdout, holdout_rows, holdout_history = collect_covered(
        HOLDOUT_SEED, rate, DEFECT_BOOST, layout, capture_every, profile, rounds)
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
            "train": {"seed": TRAIN_SEED, "seconds": train_history[-1]["seconds"],
                      "rows": [list(row) for row in train_rows],
                      "observations": train_observations,
                      "unique_object_counts": train_unique,
                      "collection_rounds": train_history},
            "holdout": {"seed": HOLDOUT_SEED, "seconds": holdout_history[-1]["seconds"],
                        "rows": [list(row) for row in holdout_rows],
                        "observations": holdout_observations,
                        "unique_object_counts": holdout_unique,
                        "collection_rounds": holdout_history},
        },
    }
    manifest_path = out / "candidate.manifest.json"
    write_atomic_json(manifest_path, manifest)
    applied_reject_classes, baseline_version, policy_source = policy
    # The bundled preset keeps a relative model path. It carries no absolute path. Its
    # startup policy must also name only candidate labels. In particular, the victim is
    # absent from the candidate catalog, while the new label always starts as Keep.
    candidate_preset = {
        **dict(preset),
        "model_path": "candidate.joblib",
        "model_path_root": "preset",
        "policy": {
            **dict(preset["policy"]),
            "initial_reject_classes": list(applied_reject_classes),
        },
    }
    preset_out = out / "candidate.preset.json"
    write_atomic_json(preset_out, candidate_preset)

    write_progress(out, "validate", 0.85)
    # The anomaly gate uses the reference set the APPLIED policy activates: every model
    # label the policy keeps. That always includes the new label, because activation adds
    # it as Keep. There is only one policy here. The saved artifact stays
    # policy-independent, and the engine binds its own set at load.
    kept_labels = [name for name in model.classes if name not in set(applied_reject_classes)]
    active_labels = model.set_anomaly_reference(kept_labels)
    probabilities, anomaly = model.predict(X_holdout)
    metrics = holdout_metrics(profile.names, y_holdout, probabilities, new_label)
    truth = profile.by_name(new_label)
    validation = {
        "labels": profile.names,
        "new_label": new_label,
        # Recorded, never rewritten: physical truth is independent of the Keep policy.
        "new_label_truth": {"defect": bool(truth.defect), "severity": truth.severity},
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
            "kept_labels": kept_labels,
            "anomaly_reference_labels": active_labels,
            "scope": "anomaly decision numbers only; the threshold is the unchanged good threshold",
        },
        "reference_coverage": {
            "train_observations": train_observations,
            "train_unique_objects": train_unique,
            "reference_labels": sorted(model.references()),
            "collection_rounds": {"train": train_history, "holdout": holdout_history},
            "scope": "train-partition coverage behind each anomaly reference; an engineering gate, not a statistical guarantee",
        },
        "pulses": None,
        "keep_outcome": None,
        "policy": {
            "applied_reject_classes": applied_reject_classes,
            "baseline_policy_version": baseline_version,
            "engine_policy_version": None,
            "policy_source": policy_source,
            "new_label_policy": "keep",
            "scope": "the policy the closed-loop run applied; it is not a truth value and never rewrites one",
        },
        "artifact_sha256": manifest["artifact_sha256"],
        "manifest_sha256": sha256(manifest_path),
        "preset_sha256": sha256(preset_out),
        "source": expected["source"],
        "catalog_revision": catalog["catalog_revision"],
        # The freeze note carries a date, and a bundled manifest must carry none, so it
        # lives in the external report only.
        "duration_freeze": DURATION_FREEZE,
        "gate": {
            "min_holdout_unique_objects": MIN_HOLDOUT_UNIQUE,
            "min_holdout_accuracy": MIN_ACCURACY,
            "min_new_label_recall": MIN_RECALL,
            "max_keep_anomaly_fraction": MAX_KEEP_ANOMALY_FRACTION,
            "min_keep_resolved": MIN_KEEP_RESOLVED,
            "min_keep_accept_fraction": MIN_KEEP_ACCEPT_FRACTION,
            "min_label_observations": MIN_LABEL_OBSERVATIONS,
            "min_label_unique_objects": MIN_LABEL_UNIQUE_OBJECTS,
        },
    }
    # Every candidate activates as Keep, so every candidate runs the closed-loop gate.
    # Both the closed-loop run and the loader proof read the profile by name, so both run
    # inside ONE binding of the candidate catalog.
    with bound_candidate_profile(profile):
        validation["policy"]["engine_policy_version"] = record_keep_outcome(
            validation, preset, preset_out, new_label, applied_reject_classes)
        validation["preset_compatibility"] = preset_compatibility(preset_out)
    validation["failures"] = gate_failures(validation)
    if args.quality_gate == "demo":
        # Strict output stays byte-identical to today, so this label lives only here.
        validation["classifier"]["new_label_recall_basis"] = (
            "simulator heuristic, not calibrated confidence")
        validation.update(split_quality_warnings(validation["failures"]))
        validation["quality_gate"] = "demo"
    validation["passed"] = not validation["failures"]
    write_atomic_json(out / "validation.json", validation)

    report.update({
        "model": str(artifact),
        "artifact_sha256": manifest["artifact_sha256"],
        "training_counts": dict(Counter(y_train)),
        "holdout_counts": dict(Counter(y_holdout)),
        "training_observations": train_observations,
        "training_unique_object_counts": train_unique,
        "holdout_observations": holdout_observations,
        "holdout_unique_object_counts": holdout_unique,
        "collection_rounds": {"train": train_history, "holdout": holdout_history},
        "candidate_feed_prior": profile.classes[0].prior,
        "wall_seconds": time.perf_counter() - started,
    })
    write_atomic_json(out / "candidate.report.json", report)
    write_progress(out, "validate", 1.0)
    # A completed validation is not a crash, so a failed gate still exits 0.
    print(json.dumps({"status": "trained", "passed": validation["passed"],
                      "failures": validation["failures"]}, sort_keys=True))
    return 0


def record_keep_outcome(validation: dict[str, Any], preset, preset_path: Path,
                        new_label: str, reject_classes: list[str]) -> str | None:
    """Run one seeded closed-loop run under the intended live policy.

    The run loads the immutable candidate preset, applies the survivor reject classes
    before its first step, and never rejects the new label, because activation always adds
    it as Keep. Returns the engine policy version. Outcomes and pulses stay separate.
    """
    seed = int(preset["seed"])
    # The caller holds `bound_candidate_profile`, so the Engine already resolves the
    # candidate labels. This function never binds a second time.
    engine = build_engine(preset_path)
    try:
        # The intended live policy applies before the first step, never after it.
        ack = engine.set_reject_classes(list(reject_classes))
        run = keep_outcome(engine, new_label, seed=seed)
    finally:
        engine.close()
    validation["keep_outcome"] = {
        "required": True,
        "runs": [{"seed": run["seed"], "sim_seconds": run["sim_seconds"], **run["outcomes"]}],
        "scope": ("physical outcomes only; a minimum engineering release gate, "
                  "not a statistical accuracy claim"),
    }
    validation["pulses"] = {
        "runs": [{"seed": run["seed"], **run["pulses"]}],
        "scope": ("object-level counts from retained engine records: objects with a scheduled "
                  "reject decision, objects with an activated rejection track, and jet hits; "
                  "they never enter the gate and are never an outcome"),
    }
    return ack.get("policy_version") if isinstance(ack, Mapping) else None


if __name__ == "__main__":
    sys.exit(main())
