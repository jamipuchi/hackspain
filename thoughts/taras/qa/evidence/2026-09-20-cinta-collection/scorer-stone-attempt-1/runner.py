#!/usr/bin/env python3
"""Run bounded pooled collection diagnostics without changing application source."""
from __future__ import annotations

import argparse
from collections import Counter
import copy
import fcntl
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import traceback

for _thread_variable in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
    os.environ[_thread_variable] = "1"

import numpy as np


LOCK_PATH = Path("/private/tmp/hackspain-coffee-runtime.lock")
BASELINE_DIR = Path(__file__).resolve().parent / "2026-09-20-cinta-stone-mechanics"
EXPECTED_MODEL_SHA256 = "f5b26f26aed62db18fbef23b4df712b96b2946b1ae2fce699e1a2e85a20844a3"
EXPECTED_PRESET_SHA256 = "5fc5033ea62ceb4f640b4e642990d014d6de09ce706c20501eecd6c30d5c428b"
EXPECTED_FORCE_N = 0.06
EXPECTED_LEAD_S = 0.0015
EXPECTED_POOL_SIZE = 488
EXPECTED_DT = 0.001
EXPECTED_SEEDS = (7, 8, 9, 42)
EXPECTED_CLASSES = (
    "good", "faded", "black", "sour", "insect",
    "broken", "shell", "husk", "stone", "stick",
)
OBJECT_SECONDS = 2.0
CONTINUATION_SECONDS = 1.5
SOURCE_HASH_FILES = (
    "sim.py", "scene.py", "engine.py", "rolling_scores.py", "controller.py", "profiles.py",
)


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def shared_settling_seconds() -> float:
    from rolling_scores import SETTLING_SECONDS
    return float(SETTLING_SECONDS)


def source_hashes(coffee: Path) -> dict[str, str]:
    return {name: file_hash(coffee / name) for name in SOURCE_HASH_FILES} | {
        "runner": file_hash(Path(__file__).resolve()),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--preset", type=Path, required=True)
    parser.add_argument("--mode", choices=("stone", "classes"), required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--observe-support", action="store_true")
    return parser.parse_args()


def json_write(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def validate_inputs(repo: Path, model_path: Path, preset_path: Path) -> tuple[dict, Path]:
    if not model_path.is_file():
        raise FileNotFoundError(f"model is missing: {model_path}")
    if not preset_path.is_file():
        raise FileNotFoundError(f"preset is missing: {preset_path}")
    coffee = repo / "sim" / "coffee_sorter"
    if not coffee.is_dir():
        raise FileNotFoundError(f"coffee sorter source is missing: {coffee}")
    if file_hash(model_path) != EXPECTED_MODEL_SHA256:
        raise ValueError("model SHA256 does not match the approved model")
    if file_hash(preset_path) != EXPECTED_PRESET_SHA256:
        raise ValueError("preset SHA256 does not match the approved preset")
    source_preset = json.loads(preset_path.read_text())
    if source_preset.get("profile") != "green_arabica":
        raise ValueError("preset must use the green_arabica profile")
    if float(source_preset["jet_force_n"]) != EXPECTED_FORCE_N:
        raise ValueError("preset jet_force_n must remain 0.06 N")
    if float(source_preset["policy"]["lead_s"]) != EXPECTED_LEAD_S:
        raise ValueError("preset policy lead_s must remain 0.0015 seconds")
    pool_size = sum(
        int(source_preset["layout"][name])
        for name in ("n_ellipsoid", "n_half", "n_box", "n_capsule")
    )
    if pool_size != EXPECTED_POOL_SIZE:
        raise ValueError(f"preset pool size must remain 488, got {pool_size}")
    return source_preset, coffee


def state(sim, data, body: int) -> dict:
    qa, va = sim.body_qpos[body], sim.body_qvel[body]
    return {
        "time_s": float(data.time),
        "pose": [float(v) for v in data.qpos[qa:qa + 7]],
        "velocity": [float(v) for v in data.qvel[va:va + 6]],
    }


def positive_contacts(sim, data, body: int, time_s: float | None = None) -> list[dict]:
    import mujoco

    contacts = []
    for index, contact in enumerate(data.contact[:data.ncon]):
        first, second = int(contact.geom1), int(contact.geom2)
        if first in sim.collection_geom:
            label, collision = sim.collection_geom[first], second
            named_geom = first
        elif second in sim.collection_geom:
            label, collision = sim.collection_geom[second], first
            named_geom = second
        elif first == sim.ground_geom:
            label, collision, named_geom = "floor", second, first
        elif second == sim.ground_geom:
            label, collision, named_geom = "floor", first, second
        else:
            continue
        if sim.collision_body.get(collision) != body:
            continue
        force = np.zeros(6)
        mujoco.mj_contactForce(sim.model, data, index, force)
        if force[0] <= 0.0:
            continue
        contacts.append({
            "surface": label,
            "geom_name": mujoco.mj_id2name(
                sim.model, mujoco.mjtObj.mjOBJ_GEOM, named_geom,
            ),
            "normal_force_n": float(force[0]),
            "distance_m": float(contact.dist),
            "solve_time_s": float(data.time if time_s is None else time_s),
            "observed_after_step_s": float(data.time),
        })
    return contacts


def observe_support(sim, body: int, contacts: list[dict]) -> dict:
    import mujoco

    named = [contact for contact in contacts if contact["surface"] in ("accept", "reject")]
    if not named:
        return {"observed": False, "reason": "no_positive_named_bin_contact_at_park"}

    failures = []
    if sim.rate != 0:
        failures.append("feed_rate_not_zero")
    if sim.n_active() != 1:
        failures.append(f"active_objects={sim.n_active()}")
    if not all(fire.t_off <= sim.data.time for fire in sim.fires):
        failures.append("valve_pulses_not_expired")
    if np.count_nonzero(sim.data.xfrc_applied) != 0:
        failures.append("xfrc_applied_not_zero")
    if np.count_nonzero(sim.data.qfrc_applied) != 0:
        failures.append("qfrc_applied_not_zero")

    initial_checks = {
        "feed_rate": sim.rate,
        "active_objects": sim.n_active(),
        "all_valve_pulses_expired": not any(fire.t_off > sim.data.time for fire in sim.fires),
        "xfrc_applied_all_zero": bool(np.count_nonzero(sim.data.xfrc_applied) == 0),
        "qfrc_applied_all_zero": bool(np.count_nonzero(sim.data.qfrc_applied) == 0),
    }
    if failures:
        return {
            "observed": True,
            "first_named_bin_contact": named[0],
            "initial_checks": initial_checks,
            "continuation_seconds": CONTINUATION_SECONDS,
            "continuation": [],
            "continuation_contacts": [],
            "continuation_final": None,
            "preservation": None,
            "final_100ms": None,
            "assertion_failures": failures,
        }

    saved_qpos = sim.data.qpos.copy()
    saved_qvel = sim.data.qvel.copy()
    saved_warmstart = sim.data.qacc_warmstart.copy()
    saved_xfrc = sim.data.xfrc_applied.copy()
    saved_qfrc = sim.data.qfrc_applied.copy()
    saved_time = float(sim.data.time)
    probe = mujoco.MjData(sim.model)
    mujoco.mj_copyData(probe, sim.model, sim.data)
    continuation = []
    continuation_contacts = []
    roller_updates = True
    for _ in range(round(CONTINUATION_SECONDS / sim.dt)):
        before = state(sim, probe, body)
        continuation.append(before)
        probe.xfrc_applied[:] = 0
        probe.qpos[sim.belt_qpos] = 0.0
        probe.qvel[sim.belt_qvel] = sim.L.belt_speed
        angle = sim.L.belt_speed / 0.03 * sim.dt
        head_before = float(probe.qpos[sim.roller_head])
        tail_before = float(probe.qpos[sim.roller_tail])
        probe.qpos[sim.roller_head] += angle
        probe.qpos[sim.roller_tail] += angle
        mujoco.mj_step(sim.model, probe)
        roller_updates &= abs(float(probe.qpos[sim.roller_head]) - head_before - angle) < 1e-9
        roller_updates &= abs(float(probe.qpos[sim.roller_tail]) - tail_before - angle) < 1e-9
        continuation_contacts.extend(positive_contacts(sim, probe, body, before["time_s"]))
    final = state(sim, probe, body)
    if not roller_updates:
        failures.append("conveyor_or_roller_update_mismatch")
    if not np.array_equal(saved_qpos, sim.data.qpos):
        failures.append("original_qpos_changed")
    if not np.array_equal(saved_qvel, sim.data.qvel):
        failures.append("original_qvel_changed")
    if not np.array_equal(saved_warmstart, sim.data.qacc_warmstart):
        failures.append("original_warmstart_changed")
    if saved_time != float(sim.data.time):
        failures.append("original_time_changed")
    if not np.array_equal(saved_xfrc, sim.data.xfrc_applied):
        failures.append("original_xfrc_applied_changed")
    if not np.array_equal(saved_qfrc, sim.data.qfrc_applied):
        failures.append("original_qfrc_applied_changed")

    final_t = final["time_s"]
    tail = [sample for sample in continuation if sample["time_s"] >= final_t - 0.1]
    final_position = np.asarray(final["pose"][:3])
    displacement = max(
        (float(np.linalg.norm(np.asarray(sample["pose"][:3]) - final_position))
         for sample in tail), default=None,
    )
    terminal_speed = float(np.linalg.norm(np.asarray(final["velocity"][:3])))
    bins = {}
    for label in ("accept", "reject"):
        supported_times = {
            contact["solve_time_s"] for contact in continuation_contacts
            if contact["surface"] == label and contact["solve_time_s"] >= final_t - 0.1
            and contact["normal_force_n"] > 1e-9
        }
        bins[label] = {
            "support_fraction": len(supported_times) / len(tail) if tail else 0.0,
            "stable": bool(
                tail and len(supported_times) / len(tail) >= 0.9
                and terminal_speed <= 0.01 and displacement <= 0.001
            ),
        }
    support = {
        "observed": True,
        "first_named_bin_contact": named[0],
        "initial_checks": {
            "feed_rate": sim.rate,
            "active_objects": sim.n_active(),
            "all_valve_pulses_expired": not any(fire.t_off > sim.data.time for fire in sim.fires),
            "xfrc_applied_all_zero": bool(np.count_nonzero(sim.data.xfrc_applied) == 0),
            "qfrc_applied_all_zero": bool(np.count_nonzero(sim.data.qfrc_applied) == 0),
        },
        "continuation_seconds": CONTINUATION_SECONDS,
        "continuation": continuation,
        "continuation_contacts": continuation_contacts,
        "continuation_final": final,
        "preservation": {
            "qpos_unchanged": "original_qpos_changed" not in failures,
            "qvel_unchanged": "original_qvel_changed" not in failures,
            "warmstart_unchanged": "original_warmstart_changed" not in failures,
            "time_unchanged": "original_time_changed" not in failures,
            "xfrc_applied_unchanged": "original_xfrc_applied_changed" not in failures,
            "qfrc_applied_unchanged": "original_qfrc_applied_changed" not in failures,
            "conveyor_and_rollers_updated_each_step": roller_updates,
        },
        "final_100ms": {
            "support": bins,
            "terminal_speed_m_s": terminal_speed,
            "maximum_displacement_m": displacement,
            "tail_steps": len(tail),
        },
        "assertion_failures": failures,
    }
    return support


def install_observers(sim, capture: dict, observe_support_enabled: bool) -> None:
    original_fire = sim.fire
    original_park = sim._park

    def observed_fire(nozzle, t_on, duration, force=0.09, uid=None):
        fire = original_fire(nozzle, t_on, duration, force, uid=uid)
        if fire is not None:
            capture["fires"].append({
                "track_id": None if uid is None else int(uid),
                "nozzle": int(fire.nozzle),
                "t_on": float(fire.t_on),
                "t_off": float(fire.t_off),
                "force_n": float(fire.force),
            })
        return fire

    def observed_park(body: int):
        bean = sim.bean_of.get(body)
        support_error = None
        if bean is not None:
            qa, va = sim.body_qpos[body], sim.body_qvel[body]
            contacts = positive_contacts(sim, sim.data, body, bean.resolved_t)
            capture["parks"].append({
                "object_id": int(bean.uid),
                "body_id": int(body),
                "sim_time_s": float(sim.data.time),
                "outcome": bean.outcome,
                "contacts": contacts,
                "final_pose_before_park": [float(v) for v in sim.data.qpos[qa:qa + 7]],
                "final_velocity_before_park": [float(v) for v in sim.data.qvel[va:va + 6]],
            })
            if observe_support_enabled:
                try:
                    support = observe_support(sim, body, contacts)
                    capture.setdefault("support", {})[bean.uid] = support
                    if support.get("assertion_failures"):
                        support_error = AssertionError(
                            "support checks failed: " + ", ".join(support["assertion_failures"])
                        )
                except Exception as exc:
                    capture.setdefault("support", {})[bean.uid] = {
                        "observed": True,
                        "assertion_error": str(exc),
                    }
                    support_error = exc
        result = original_park(body)
        if support_error is not None:
            raise support_error
        return result

    sim.fire = observed_fire
    sim._park = observed_park


def body_sample(sim, bean) -> dict:
    qa, va = sim.body_qpos[bean.body], sim.body_qvel[bean.body]
    return {
        "body_id": int(bean.body),
        "mass_kg": float(sim.model.body_mass[bean.body]),
        "axes_m": [float(v) for v in bean.axes],
        "initial_pose": [float(v) for v in sim.data.qpos[qa:qa + 7]],
        "initial_velocity": [float(v) for v in sim.data.qvel[va:va + 6]],
    }


def decision_for(engine, object_id: int):
    decision = engine._decision_by_uid.get(object_id)
    if decision is not None:
        return decision
    for candidate in reversed(engine._decision_evidence):
        if object_id in candidate.get("object_ids", ()):
            return engine._decision_by_track.get(candidate["track_id"])
    return None


def decision_facts(engine, object_id: int, spawn_time_s: float) -> dict:
    decision = decision_for(engine, object_id)
    if decision is None:
        return {
            "observed": False,
            "predicted_class": None,
            "probabilities": None,
            "anomaly_score": None,
            "anomaly_threshold": float(engine.model.anomaly_thresh),
            "reject_probability": None,
            "reject": False,
            "scheduled": False,
            "late": False,
            "pulse_s": None,
            "relative_t_decided_s": None,
            "relative_t_available_s": None,
            "relative_t_fire_s": None,
            "track_id": None,
            "nozzles": [],
        }
    probabilities = {
        name: float(probability)
        for name, probability in zip(engine.model.classes, decision.probs)
    }
    reject_probability = float(decision.probs[engine.controller.reject_mask].sum())
    return {
        "observed": True,
        "predicted_class": decision.cls,
        "probabilities": probabilities,
        "anomaly_score": float(decision.anomaly),
        "anomaly_threshold": float(engine.model.anomaly_thresh),
        "reject_probability": reject_probability,
        "class_threshold_reject": bool(reject_probability >= engine.policy.threshold),
        "anomaly_reject": bool(
            engine.policy.anomaly and decision.anomaly > engine.model.anomaly_thresh
        ),
        "reject": bool(decision.reject),
        "scheduled": bool(decision.scheduled),
        "late": bool(decision.late),
        "pulse_s": float(decision.pulse),
        "relative_t_decided_s": float(decision.t_decided - spawn_time_s),
        "relative_t_available_s": float(decision.t_available - spawn_time_s),
        "relative_t_fire_s": float(decision.t_fire - spawn_time_s),
        "track_id": int(decision.tid),
        "nozzles": [int(nozzle) for nozzle in decision.nozzles],
        "observation_count": int(decision.n_obs),
    }


def matching_events(engine, object_id: int) -> list[dict]:
    return [
        event for event in engine._events
        if event.get("object_id") == object_id
        or object_id in event.get("object_ids", ())
    ]


def object_result(engine, object_id: int, seed: int, reuse: int,
                  initial: dict, capture: dict, started_s: float,
                  stop_reason: str) -> dict:
    record = engine._object_records[object_id]
    spawn_time_s = float(record["spawn_time_s"])
    decision = decision_facts(engine, object_id, spawn_time_s)
    events = matching_events(engine, object_id)
    tracks = set(record["associated_rejection_tracks"])
    fires = [fire for fire in capture["fires"] if fire["track_id"] in tracks]
    parks = [park for park in capture["parks"] if park["object_id"] == object_id]
    retired = object_id not in engine.sim.bean_by_uid
    park = parks[-1] if parks else None
    outcome = record["outcome"] if record["outcome"] is not None else (
        None if park is None else park["outcome"]
    )
    resolved_time_s = record["resolved_time_s"] if record["resolved_time_s"] is not None else (
        None if park is None else park["sim_time_s"]
    )
    resolution_age_s = (
        None if resolved_time_s is None else float(resolved_time_s) - spawn_time_s
    )
    outcome_events = [event for event in events if event.get("type") == "outcome"]
    event_counts = Counter(event.get("type") for event in events)
    return {
        "seed": int(seed),
        "reuse": int(reuse),
        "object_id": int(object_id),
        "spawn_time_s": spawn_time_s,
        "expected_outcome": record["expected_outcome"],
        "sampled_body": initial,
        "classification_control": {
            **decision,
            "expected_outcome": record["expected_outcome"],
            "expectation_policy_version": record["expectation_policy_version"],
            "associated_rejection_tracks": sorted(int(v) for v in tracks),
            "actual_scheduled_fires": fires,
            "jet_hit_steps": int(record["jet_hits"]),
            "own_pulse_event_count": sum(event.get("type") == "own_pulse_hit" for event in events),
            "own_pulse_hit": bool(record["own_pulse_hit"]),
        },
        "collection": {
            "outcome": outcome,
            "resolved_time_s": resolved_time_s,
            "resolution_age_s": resolution_age_s,
            "last_position_m": record["pos"] if record["pos"] is not None else (
                None if park is None else park["final_pose_before_park"][:3]
            ),
            "retired": retired,
            "unresolved": outcome is None,
            "stop_reason": stop_reason,
            "sim_seconds_observed": float(engine.sim.data.time) - started_s,
            "native_positive_contacts_before_park": parks,
            "support_observation": capture.get("support", {}).get(object_id),
            "retirement_event_count": len(parks),
            "outcome_event_count": len(outcome_events),
            "event_counts": dict(sorted(event_counts.items())),
        },
        "events": events,
    }


def run_case(repo: Path, model_path: Path, source_preset: dict, coffee: Path,
             output_path: Path, label: str, seed: int, targets: list[str],
             policy_mode: str | None, observe_support_enabled: bool) -> dict:
    run = {
        "schema_version": 1,
        "label": label,
        "seed": int(seed),
        "targets": list(targets),
        "policy_mode": policy_mode or "default",
        "status": "ok",
        "attempts": [],
        "identity": {
            "repo": str(repo),
            "model": str(model_path),
            "model_sha256": file_hash(model_path),
            "preset_sha256": file_hash(Path(source_preset["_preset_path"])),
            "source_revision": subprocess.check_output(
                ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True,
            ).strip(),
            "source_hashes": source_hashes(coffee),
        },
    }
    source_preset = copy.deepcopy(source_preset)
    source_preset.pop("_preset_path")
    engine = None
    lock_wait_s = None
    try:
        LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOCK_PATH.open("a+") as lock:
            lock_started = time.perf_counter()
            fcntl.flock(lock, fcntl.LOCK_EX)
            lock_wait_s = time.perf_counter() - lock_started
            with tempfile.TemporaryDirectory(prefix="cinta-collection-") as directory:
                staging = Path(directory)
                runtime_preset = copy.deepcopy(source_preset)
                runtime_preset["mode"] = "continuous"
                runtime_preset["requested_rate"] = 0.0
                runtime_preset["seed"] = int(seed)
                runtime_preset_path = staging / "continuous.preset.json"
                if runtime_preset.get("model_path_root") == "preset":
                    relative_model = Path(runtime_preset["model_path"])
                    staged_model = staging / relative_model
                    staged_model.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(model_path, staged_model)
                runtime_preset_path.write_text(json.dumps(runtime_preset))
                sys.path.insert(0, str(coffee))
                from engine import Engine

                engine = Engine(runtime_preset_path)
                if abs(float(engine.sim.dt) - EXPECTED_DT) > 1e-12:
                    raise ValueError(f"runtime timestep must be 0.001, got {engine.sim.dt}")
                if list(engine.profile.names) != list(EXPECTED_CLASSES):
                    raise ValueError("runtime profile class order differs from approved order")
                if list(engine.model.classes) != list(EXPECTED_CLASSES):
                    raise ValueError("model class order differs from approved order")
                if policy_mode == "keep":
                    reject_classes = set(engine.reject_classes)
                    reject_classes.discard("stone")
                    engine.set_reject_classes(sorted(reject_classes))
                elif policy_mode == "reject":
                    reject_classes = set(engine.reject_classes)
                    reject_classes.add("stone")
                    engine.set_reject_classes(sorted(reject_classes))
                capture = {"fires": [], "parks": []}
                install_observers(engine.sim, capture, observe_support_enabled)
                for reuse in range(2):
                    if engine.sim.bean_of:
                        occupied = sorted(int(body) for body in engine.sim.bean_of)
                        run["attempts"].append({
                            "reuse": reuse,
                            "skipped": True,
                            "reason": "previous object unresolved and body remains occupied",
                            "occupied_body_ids": occupied,
                        })
                        break
                    object_id = engine.inject(targets[0])
                    sample = body_sample(engine.sim, engine.sim.bean_by_uid[object_id])
                    started_s = float(engine.sim.data.time)
                    stop_reason = "retired"
                    try:
                        while float(engine.sim.data.time) - started_s < OBJECT_SECONDS:
                            engine.step()
                            if object_id not in engine.sim.bean_by_uid:
                                break
                    except Exception:
                        run["attempts"].append(object_result(
                            engine, object_id, seed, reuse, sample, capture,
                            started_s, "step_error",
                        ))
                        raise
                    if object_id in engine.sim.bean_by_uid:
                        stop_reason = "unresolved_after_two_simulated_seconds"
                    run["attempts"].append(object_result(
                        engine, object_id, seed, reuse, sample, capture,
                        started_s, stop_reason,
                    ))
                    if stop_reason != "retired":
                        break
            engine.close()
            engine = None
    except Exception as exc:
        run["status"] = "error"
        run["error"] = {
            "type": type(exc).__name__,
            "message": str(exc),
            "traceback": traceback.format_exc(),
        }
    finally:
        if engine is not None:
            try:
                engine.close()
            except Exception as exc:
                run["status"] = "error"
                run["close_error"] = f"{type(exc).__name__}: {exc}"
    run["lock_wait_s"] = lock_wait_s
    run["lock_released_after_close"] = True
    json_write(output_path, run)
    return run


def reference_comparison(mode: str, rows: list[dict]) -> dict:
    result_path = BASELINE_DIR / f"{mode}-seed-{{seed}}-reuse-{{reuse}}.json.gz"
    result = {
        "baseline_directory": str(BASELINE_DIR),
        "available": BASELINE_DIR.is_dir(),
        "basis": "raw baseline traces compare sampled bodies and normalized control facts",
        "spawn_time": "baseline decision times subtract trajectory[0].time_s; current times subtract recorded spawn_time_s",
        "wall_latency": "wall-clock latency is excluded because it is nondeterministic",
        "legacy_route_json": "not used because it lacks sampled body parameters and spawn timestamps",
        "rows": [],
    }
    for current in rows:
        key = (current["seed"], current["reuse"])
        baseline_path = Path(str(result_path).format(seed=key[0], reuse=key[1]))
        row_result = {"seed": key[0], "reuse": key[1], "baseline": str(baseline_path)}
        if not baseline_path.is_file():
            row_result["status"] = "missing_baseline"
            result["rows"].append(row_result)
            continue
        with gzip.open(baseline_path, "rt") as stream:
            baseline = json.load(stream)
        first = baseline["trajectory"][0]
        baseline_spawn = float(first["time_s"])
        baseline_decision = baseline["original"].get("decision", {})
        current_control = current["classification_control"]
        baseline_body = {
            "body_id": int(baseline["body_id"]),
            "mass_kg": float(baseline["mass_kg"]),
            "axes_m": baseline["axes_m"],
            "initial_pose": first["pose"],
            "initial_velocity": first["velocity"],
        }
        current_body = current["sampled_body"]
        body_equal = {
            "body_id": current_body["body_id"] == baseline_body["body_id"],
            "mass_kg": bool(np.isclose(current_body["mass_kg"], baseline_body["mass_kg"], rtol=0, atol=1e-15)),
            "axes_m": bool(np.allclose(current_body["axes_m"], baseline_body["axes_m"], rtol=0, atol=1e-12)),
            "initial_pose": bool(np.allclose(current_body["initial_pose"], baseline_body["initial_pose"], rtol=0, atol=1e-12)),
            "initial_velocity": bool(np.allclose(current_body["initial_velocity"], baseline_body["initial_velocity"], rtol=0, atol=1e-12)),
        }
        relative_reference = {
            "t_available_s": baseline_decision.get("t_available_s", 0.0) - baseline_spawn,
            "t_fire_s": baseline_decision.get("t_fire_s", 0.0) - baseline_spawn,
        }
        relative_current = {
            "t_decided_s": current_control["relative_t_decided_s"],
            "t_available_s": current_control["relative_t_available_s"],
            "t_fire_s": current_control["relative_t_fire_s"],
        }
        timing_equal = {
            key: bool(np.isclose(relative_current[key], relative_reference[key], rtol=0, atol=1e-9))
            for key in ("t_available_s", "t_fire_s")
        }
        row_result["facts"] = {
            "spawn_time_s": {
                "current": current["spawn_time_s"],
                "baseline": baseline_spawn,
                "comparison": "reported only, absolute times are not required to match",
            },
            "sampled_body": {
                "current": current_body,
                "baseline": baseline_body,
                "equal": body_equal,
            },
            "control": {
                "predicted_class": {
                    "current": current_control["predicted_class"],
                    "baseline": baseline["original"]["classifier"]["predicted_class"],
                    "equal": current_control["predicted_class"] == baseline["original"]["classifier"]["predicted_class"],
                },
                "reject": {"current": current_control["reject"], "baseline": baseline_decision.get("reject")},
                "scheduled": {"current": current_control["scheduled"], "baseline": baseline_decision.get("scheduled")},
                "pulse_s": {"current": current_control["pulse_s"], "baseline": baseline_decision.get("pulse_s")},
                "jet_hit_steps": {"current": current_control["jet_hit_steps"], "baseline": baseline["original"]["contact"].get("jet_hit_steps")},
                "own_pulse_event_count": {"current": current_control["own_pulse_event_count"], "baseline": baseline["original"]["contact"].get("own_pulse_events")},
                "relative_decision_timing_s": {
                    "current": relative_current,
                    "baseline": relative_reference,
                    "equal": timing_equal,
                },
            },
        }
        for name in ("reject", "scheduled", "pulse_s", "jet_hit_steps", "own_pulse_event_count"):
            pair = row_result["facts"]["control"][name]
            pair["equal"] = pair["current"] == pair["baseline"]
        row_result["status"] = "compared"
        result["rows"].append(row_result)
    return result


def main() -> int:
    args = parse_args()
    if args.observe_support and args.mode != "stone":
        raise ValueError("--observe-support is only valid with --mode stone")
    repo = args.repo.resolve()
    model_path = args.model.resolve()
    preset_path = args.preset.resolve()
    output_dir = args.output_dir.resolve()
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite existing output directory: {output_dir}")
    source_preset, coffee = validate_inputs(repo, model_path, preset_path)
    source_preset["_preset_path"] = str(preset_path)
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir()

    sys.path.insert(0, str(coffee))
    from classifier import Model

    if list(Model.load(model_path).classes) != list(EXPECTED_CLASSES):
        raise ValueError("approved model class order differs from built-in catalog order")
    settling_seconds = shared_settling_seconds()

    run_results = []
    if args.mode == "stone":
        cases = [
            ("stone-keep", "keep", ["stone"]),
            ("stone-reject", "reject", ["stone"]),
        ]
    else:
        cases = [(f"class-{name}", None, [name]) for name in EXPECTED_CLASSES]
    for label, policy_mode, targets in cases:
        for seed in EXPECTED_SEEDS:
            filename = f"{label}-seed-{seed}.json"
            run_results.append(run_case(
                repo, model_path, source_preset, coffee,
                output_dir / filename, label, seed, targets, policy_mode,
                args.observe_support,
            ))

    rows = [attempt for run in run_results for attempt in run["attempts"] if not attempt.get("skipped")]
    summary = {
        "schema_version": 1,
        "mode": args.mode,
        "identity": {
            "repo": str(repo),
            "model": str(model_path),
            "model_sha256": file_hash(model_path),
            "preset": str(preset_path),
            "preset_sha256": file_hash(preset_path),
            "source_revision": subprocess.check_output(
                ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True,
            ).strip(),
            "source_hashes": source_hashes(coffee),
        },
        "configuration": {
            "seeds": list(EXPECTED_SEEDS),
            "reuses_per_target": 2,
            "object_seconds": OBJECT_SECONDS,
            "settling_seconds": settling_seconds,
            "force_n": EXPECTED_FORCE_N,
            "lead_s": EXPECTED_LEAD_S,
            "pool_size": EXPECTED_POOL_SIZE,
            "dt_s": EXPECTED_DT,
            "class_order": list(EXPECTED_CLASSES),
            "feed_rate": 0.0,
            "observe_support": bool(args.observe_support),
            "continuation_seconds": CONTINUATION_SECONDS if args.observe_support else None,
            "application_source_changed": False,
        },
        "run_count": len(run_results),
        "error_runs": sum(run["status"] != "ok" for run in run_results),
        "skipped_reuses": sum(
            attempt.get("skipped", False)
            for run in run_results for attempt in run["attempts"]
        ),
        "unresolved_attempts": sum(
            attempt.get("collection", {}).get("unresolved", False)
            for attempt in rows
        ),
        "runs": [run["label"] + f"-seed-{run['seed']}" for run in run_results],
    }
    if args.mode == "stone":
        for mode in ("keep", "reject"):
            mode_rows = [
                attempt for attempt in rows
                if attempt["expected_outcome"] == ("reject" if mode == "reject" else "accept")
            ]
            summary[f"stone_{mode}_reference_comparison"] = reference_comparison(mode, mode_rows)
        summary["historical_observer_physical"] = {
            "stone_reject": {"accept": 8, "reject": 0, "spilled": 0},
            "stone_keep": {"accept": 6, "reject": 0, "spilled": 2},
            "asserted": False,
        }
    if args.mode == "classes":
        ages = [
            attempt["collection"]["resolution_age_s"]
            for attempt in rows
            if attempt["collection"]["resolution_age_s"] is not None
        ]
        maximum = max(ages) if ages else None
        summary["all_class_resolution_age"] = {
            "maximum_s": maximum,
            "settling_seconds": settling_seconds,
            "exceeds_settling_seconds": None if maximum is None else maximum > settling_seconds,
        }
    json_write(output_dir / "summary.json", summary)
    print(json.dumps({"output_dir": str(output_dir), **summary}, allow_nan=False))
    return 1 if summary["error_runs"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
