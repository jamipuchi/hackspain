#!/usr/bin/env python3
"""Run bounded CINTA rejection calibration cases against the trusted model."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import fcntl
import hashlib
import json
import os
from pathlib import Path
import resource
import subprocess
import sys
import tempfile
import time

for key in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
    os.environ[key] = "1"

ROOT = Path(__file__).resolve().parents[4]
COFFEE = ROOT / "sim" / "coffee_sorter"
sys.path.insert(0, str(COFFEE))

import numpy as np

from classifier import Model
from controller import Controller, Policy
from engine import Engine
from profiles import GREEN_ARABICA
from scene import Layout
from sim import SorterSim
from vision import Inspector

LOCK_PATH = Path("/private/tmp/hackspain-coffee-runtime.lock")
DEFAULT_PRESET = COFFEE / "configs" / "continuous_demo.json"
DEFAULT_MODEL = Path(
    "/Users/taras/Documents/code/hackspain/sim/coffee_sorter/models/live_green_arabica.joblib"
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def peak_rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 * 1024)


def process_cpu_seconds() -> float:
    usage = resource.getrusage(resource.RUSAGE_SELF)
    return usage.ru_utime + usage.ru_stime


def load_case(args) -> tuple[dict, Path]:
    preset = json.loads(args.preset.read_text())
    model_path = args.model.resolve()
    if not model_path.is_file():
        raise FileNotFoundError(f"trusted model is missing: {model_path}")
    preset["model_path"] = str(model_path)
    preset["seed"] = args.seed
    preset["jet_force_n"] = args.force
    preset["policy"]["lead_s"] = args.lead_ms / 1000
    return preset, model_path


def policy_from(preset: dict, anomaly: bool | None = None) -> Policy:
    config = preset["policy"]
    return Policy(
        name=config["name"],
        reject_severities=tuple(config["reject_severities"]),
        threshold=float(config["threshold"]),
        anomaly=bool(config["anomaly"] if anomaly is None else anomaly),
        base_pulse=float(config["base_pulse_s"]),
        ref_mass=float(config["ref_mass_kg"]),
        max_pulse=float(config["max_pulse_s"]),
        lead=float(config["lead_s"]),
        latency_floor=float(config["latency_floor_s"]),
        induced_delay=float(config["induced_delay_s"]),
        fixed_latency=config["fixed_latency_s"],
        target_nozzles=config["target_nozzles"],
    )


def isolated_case(args, preset: dict, model_path: Path) -> dict:
    layout = Layout(**preset["layout"])
    sim = SorterSim(GREEN_ARABICA, layout, rate=0, seed=args.seed)
    inspector = Inspector(sim)
    model = Model.load(model_path)
    controller = Controller(
        sim,
        inspector,
        model,
        policy_from(preset),
        jet_force=args.force,
    )
    controller.set_reject_classes(["stone"])
    rows = []
    try:
        for reuse in range(2):
            bean = sim.spawn(GREEN_ARABICA.by_name("stone"))
            if bean is None:
                raise RuntimeError("Stone spawn failed")
            decision_start = len(controller.decisions)
            fired_start = sim.n_fired
            activated_start = sim.n_activated
            commands = []
            activated_at = {}
            original_fire = sim.fire

            def record_fire(*fire_args, **fire_kwargs):
                fire = original_fire(*fire_args, **fire_kwargs)
                if fire is not None:
                    commands.append(fire)
                return fire

            sim.fire = record_fire
            flight = {}
            impulse = 0.0
            split_position = None
            started = float(sim.data.time)
            step = 0
            while sim.data.time - started < 1.5 and bean.body in sim.bean_of:
                sim.step()
                for fire in commands:
                    if fire.activated and id(fire) not in activated_at:
                        activated_at[id(fire)] = float(sim.data.time - sim.dt)
                qa = sim.body_qpos[bean.body]
                va = sim.body_qvel[bean.body]
                position = sim.data.qpos[qa:qa + 3]
                velocity = sim.data.qvel[va:va + 3]
                if bean.body in sim.bean_of:
                    impulse -= float(sim.data.xfrc_applied[bean.body, 2]) * sim.dt
                    crossings = (
                        ("edge", position[0] >= 0),
                        ("jet_entry", position[0] >= sim.L.ej_x - 0.01),
                        ("jet_exit", position[0] >= sim.L.ej_x + 0.01),
                    )
                    for name, crossed in crossings:
                        if crossed and name not in flight:
                            flight[name] = {
                                "sim_time_s": float(sim.data.time),
                                "position_m": position.tolist(),
                                "velocity_m_s": velocity.tolist(),
                            }
                if bean.outcome is not None and split_position is None:
                    split_position = sim.data.qpos[qa:qa + 3].tolist()
                if step % int(preset["camera_every_steps"]) == 0:
                    frame, exposure = inspector.capture()
                    controller.on_frame(frame, exposure)
                step += 1

            decisions = []
            for decision in controller.decisions[decision_start:]:
                decisions.append({
                    "predicted_class": decision.cls,
                    "stone_probability": float(decision.probs[model.classes.index("stone")]),
                    "reject_probability": float(decision.probs[controller.reject_mask].sum()),
                    "anomaly": float(decision.anomaly),
                    "anomaly_threshold": float(model.anomaly_thresh),
                    "reject": bool(decision.reject),
                    "scheduled": bool(decision.scheduled),
                    "late": bool(decision.late),
                    "pulse_s": float(decision.pulse),
                    "t_decided_s": float(decision.t_decided),
                    "t_available_s": float(decision.t_available),
                    "t_fire_s": float(decision.t_fire),
                    "nominal_t_on_s": float(decision.t_fire - controller.pol.lead),
                    "own_contact": (decision.tid, bean.uid) in sim.fire_hits,
                })
            rows.append({
                "seed": args.seed,
                "reuse": reuse,
                "object_id": bean.uid,
                "body": int(bean.body),
                "mass_kg": float(bean.mass),
                "outcome": bean.outcome,
                "split_position_m": split_position,
                "commands": sim.n_fired - fired_start,
                "activated": sim.n_activated - activated_start,
                "jet_hit_steps": int(bean.jet_hits),
                "jet_impulse_n_s": impulse,
                "pulse_windows": [
                    {
                        "nozzle": fire.nozzle,
                        "t_on_s": fire.t_on,
                        "t_off_s": fire.t_off,
                        "activated_at_s": activated_at.get(id(fire)),
                        "force_n": fire.force,
                    }
                    for fire in commands
                ],
                "flight": flight,
                "decisions": decisions,
            })
            sim.fire = original_fire
    finally:
        inspector.close()
    return {
        "kind": "isolated_stone_reject",
        "force_n": args.force,
        "lead_s": preset["policy"]["lead_s"],
        "seed": args.seed,
        "pool_size": int(sum(preset["layout"][key] for key in (
            "n_ellipsoid", "n_half", "n_box", "n_capsule"
        ))),
        "layout": asdict(layout),
        "rows": rows,
    }


def feed_case(args, preset: dict, model_path: Path) -> dict:
    bounded = json.loads(json.dumps(preset))
    bounded.pop("mode", None)
    bounded.pop("score_window_seconds", None)
    bounded["limits"]["max_sim_seconds"] = args.seconds
    bounded["limits"]["max_wall_seconds"] = None
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as stream:
        json.dump(bounded, stream)
        preset_path = Path(stream.name)
    engine = None
    try:
        engine = Engine(preset_path)
        fires = []
        activated_at = {}
        trajectories = {}
        original_fire = engine.sim.fire

        def record_fire(*fire_args, **fire_kwargs):
            fire = original_fire(*fire_args, **fire_kwargs)
            if fire is not None:
                fires.append(fire)
            return fire

        engine.sim.fire = record_fire
        while engine.sim.data.time + 1e-12 < args.seconds:
            engine.step()
            sim = engine.sim
            for fire in fires:
                if fire.activated and id(fire) not in activated_at:
                    activated_at[id(fire)] = float(sim.data.time - sim.dt)
            bodies, positions, velocities = sim.active_state()
            for body, position, velocity in zip(bodies, positions, velocities):
                bean = sim.bean_of[body]
                row = trajectories.setdefault(bean.uid, {
                    "jet_entry": None,
                    "jet_exit": None,
                    "jet_impulse_n_s": 0.0,
                })
                if row["jet_entry"] is None and position[0] >= sim.L.ej_x - 0.01:
                    row["jet_entry"] = {
                        "sim_time_s": float(sim.data.time),
                        "position_m": position.tolist(),
                        "velocity_m_s": velocity.tolist(),
                    }
                if row["jet_exit"] is None and position[0] >= sim.L.ej_x + 0.01:
                    row["jet_exit"] = {
                        "sim_time_s": float(sim.data.time),
                        "position_m": position.tolist(),
                        "velocity_m_s": velocity.tolist(),
                    }
                row["jet_impulse_n_s"] -= float(sim.data.xfrc_applied[body, 2]) * sim.dt
        report = engine.report()
        fires_by_track = {}
        for fire in fires:
            fires_by_track.setdefault(fire.uid, []).append(fire)
        decisions_by_track = {decision.tid: decision for decision in engine.controller.decisions}
        hit_tracks_by_object = {}
        for track_id, object_id in engine.sim.fire_hits:
            hit_tracks_by_object.setdefault(object_id, []).append(track_id)
        affected_ids = set(hit_tracks_by_object)
        timing_decisions = []
        for decision in engine.controller.decisions:
            if not decision.reject:
                continue
            windows = fires_by_track.get(decision.tid, [])
            affected_ids.update(decision.target_uids)
            timing_decisions.append({
                "track_id": int(decision.tid),
                "t_decided_s": float(decision.t_decided),
                "t_available_s": float(decision.t_available),
                "t_fire_s": float(decision.t_fire),
                "nominal_t_on_s": float(decision.t_fire - engine.policy.lead),
                "predicted_class": decision.cls,
                "reject": bool(decision.reject),
                "scheduled": bool(decision.scheduled),
                "late": bool(decision.late),
                "pulse_s": float(decision.pulse),
                "object_ids": [int(uid) for uid in decision.target_uids],
                "pulse_windows": [{
                    "nozzle": fire.nozzle,
                    "t_on_s": fire.t_on,
                    "t_off_s": fire.t_off,
                    "activated_at_s": activated_at.get(id(fire)),
                    "force_n": fire.force,
                } for fire in windows],
            })
        trajectory_evidence = []
        for object_id in sorted(affected_ids):
            row = trajectories.get(object_id, {
                "jet_entry": None,
                "jet_exit": None,
                "jet_impulse_n_s": 0.0,
            })
            tracks = sorted(hit_tracks_by_object.get(object_id, []))
            own = []
            collateral = []
            for track_id in tracks:
                decision = decisions_by_track.get(track_id)
                destination = own if decision and object_id in decision.target_uids else collateral
                destination.append(int(track_id))
            trajectory_evidence.append({
                "object_id": int(object_id),
                **row,
                "own_contact_tracks": own,
                "collateral_contact_tracks": collateral,
            })
        report["calibration_timing"] = {
            "decisions": timing_decisions,
            "objects": trajectory_evidence,
            "contact_pairs": len(engine.sim.fire_hits),
            "own_contact_pairs": sum(len(row["own_contact_tracks"]) for row in trajectory_evidence),
            "collateral_contact_pairs": sum(
                len(row["collateral_contact_tracks"]) for row in trajectory_evidence
            ),
        }
    finally:
        if engine is not None:
            engine.close()
        preset_path.unlink(missing_ok=True)
    report["calibration_case"] = {
        "kind": "mixed_feed",
        "force_n": args.force,
        "lead_s": preset["policy"]["lead_s"],
        "seed": args.seed,
        "seconds": args.seconds,
        "preset_source": str(args.preset.resolve()),
        "preset_source_sha256": sha256(args.preset),
    }
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("isolated", "feed"))
    parser.add_argument("--force", required=True, type=float)
    parser.add_argument("--lead-ms", required=True, type=float)
    parser.add_argument("--seed", required=True, type=int)
    parser.add_argument("--seconds", type=float, default=4.0)
    parser.add_argument("--preset", type=Path, default=DEFAULT_PRESET)
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--run-label")
    args = parser.parse_args()
    if args.force not in (0.06, 0.075, 0.09):
        parser.error("force must be 0.06, 0.075, or 0.09 N")
    if args.lead_ms not in (1.5, 3.0):
        parser.error("lead-ms must be 1.5 or 3.0")
    if not 0 < args.seconds <= 10:
        parser.error("seconds must be in the range 0 to 10")

    preset, model_path = load_case(args)
    started = time.perf_counter()
    load_before_wait = os.getloadavg()
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOCK_PATH.open("a") as lock:
        lock_started = time.perf_counter()
        fcntl.flock(lock, fcntl.LOCK_EX)
        lock_wait_seconds = time.perf_counter() - lock_started
        load_at_start = os.getloadavg()
        cpu_started = process_cpu_seconds()
        result = (
            isolated_case(args, preset, model_path)
            if args.mode == "isolated"
            else feed_case(args, preset, model_path)
        )
        process_cpu = process_cpu_seconds() - cpu_started
        load_at_end = os.getloadavg()
    result["execution"] = {
        "wall_seconds": time.perf_counter() - started,
        "lock_wait_seconds": lock_wait_seconds,
        "process_cpu_seconds": process_cpu,
        "host_load_average_before_wait": load_before_wait,
        "host_load_average_at_start": load_at_start,
        "host_load_average_at_end": load_at_end,
        "host_cpu_count": os.cpu_count(),
        "run_label": args.run_label,
        "peak_rss_mb": peak_rss_mb(),
        "python": sys.version,
        "source_revision": subprocess.check_output(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True
        ).strip(),
        "sim_sha256": sha256(COFFEE / "sim.py"),
        "controller_sha256": sha256(COFFEE / "controller.py"),
        "engine_sha256": sha256(COFFEE / "engine.py"),
        "preset_sha256": sha256(args.preset),
        "model_sha256": sha256(model_path),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    print(json.dumps({
        "output": str(args.output),
        "mode": args.mode,
        "force_n": args.force,
        "lead_ms": args.lead_ms,
        "seed": args.seed,
        "wall_seconds": result["execution"]["wall_seconds"],
    }, allow_nan=False))


if __name__ == "__main__":
    main()
