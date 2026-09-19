"""Capture every physics step around a real coffee-sorter pulse.

The recorder imports a pinned simulator checkout. It does not change simulator,
controller, model, or policy behavior. Contact rows use the same simultaneous
position predicates that the simulator uses to apply jet force.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import fcntl
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

import numpy as np


SOURCE_FILES = (
    "sim.py",
    "scene.py",
    "profiles.py",
    "controller.py",
    "vision.py",
    "classifier.py",
)


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def json_hash(value) -> str:
    data = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(data).hexdigest()


def quantize(values, scale):
    return np.rint(np.asarray(values) * scale).astype(int).tolist()


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sim-dir", type=Path, required=True)
    parser.add_argument("--model", type=Path)
    parser.add_argument(
        "--control-replay",
        type=Path,
        help="Replay recorded valve and decision events without camera inference.",
    )
    parser.add_argument("--seconds", type=float, default=2.05)
    parser.add_argument("--capture-start", type=float, default=1.65)
    parser.add_argument("--capture-end", type=float, default=2.05)
    parser.add_argument("--rate", type=float, default=1000)
    parser.add_argument("--jet-force", type=float, default=0.06)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument(
        "--lock-file",
        type=Path,
        default=Path("/private/tmp/hackspain-coffee-runtime.lock"),
    )
    args = parser.parse_args()
    if args.seconds <= 0 or args.rate <= 0 or args.jet_force < 0:
        parser.error("seconds and rate must be positive. jet-force must be nonnegative")
    if not 0 <= args.capture_start < args.capture_end <= args.seconds:
        parser.error("capture interval must be within the simulation interval")
    return args


def machine_geometry(mujoco, sim):
    machine = []
    m, d = sim.model, sim.data
    for geom in range(m.ngeom):
        if int(m.geom_bodyid[geom]) in sim.body_index:
            continue
        material = ""
        if m.geom_matid[geom] >= 0:
            material = mujoco.mj_id2name(
                m, mujoco.mjtObj.mjOBJ_MATERIAL, int(m.geom_matid[geom])
            )
        quat = np.zeros(4)
        mujoco.mju_mat2Quat(quat, d.geom_xmat[geom])
        machine.append(
            {
                "name": mujoco.mj_id2name(m, mujoco.mjtObj.mjOBJ_GEOM, geom) or "",
                "type": int(m.geom_type[geom]),
                "size": m.geom_size[geom].tolist(),
                "pos": d.geom_xpos[geom].tolist(),
                "quat": quat.tolist(),
                "material": material,
                "rgba": m.geom_rgba[geom].tolist(),
            }
        )
    return machine


def decision_row(decision, timestamp, object_ids):
    return {
        "time": round(float(timestamp), 9),
        "trackId": int(decision.tid),
        "objectIds": object_ids,
        "predictedClass": decision.cls,
        "reject": bool(decision.reject),
        "scheduled": bool(decision.scheduled),
        "late": bool(decision.late),
        "nozzles": [int(value) for value in decision.nozzles],
        "requestedFireTime": round(float(decision.t_fire), 9),
        "pulseDuration": round(float(decision.pulse), 9),
        "cameraPosition": [float(decision.x), float(decision.y)],
        "estimatedVelocity": float(decision.v),
        "observations": int(decision.n_obs),
    }


def capture(args):
    source = args.sim_dir.resolve()
    sys.path.insert(0, str(source))
    import mujoco
    from profiles import PROFILES
    from scene import Layout
    from sim import JET_HALF_X, JET_HALF_Y_FACTOR, SorterSim

    model_path = args.model or (
        source
        / "runs/generalization/green_arabica/model/green_arabica.joblib"
    )
    model_path = model_path.resolve()
    source_revision = subprocess.check_output(
        ["git", "-C", str(source), "rev-parse", "HEAD"], text=True
    ).strip()
    control_replay = None
    if args.control_replay:
        control_replay = json.loads(args.control_replay.read_text())
        if control_replay["source"]["revision"] != source_revision:
            raise SystemExit("control replay and simulator revisions differ")
        if control_replay["source"]["modelSha256"] != file_hash(model_path):
            raise SystemExit("control replay and model hashes differ")
        recorded = control_replay["config"]
        checks = {
            "seed": (args.seed, recorded["seed"]),
            "rate": (args.rate, recorded["rate"]),
            "jet force": (args.jet_force, recorded["jetForce"]),
        }
        mismatches = [
            name for name, (requested, expected) in checks.items()
            if requested != expected
        ]
        if mismatches:
            raise SystemExit(
                "control replay mismatch: " + ", ".join(mismatches)
            )
        policy_data = recorded["policy"]
    else:
        from controller import Policy

        policy_data = asdict(Policy())
    profile = PROFILES["green_arabica"]
    sim = SorterSim(profile, Layout(), rate=args.rate, seed=args.seed)
    if sim.dt > 0.0020000001:
        raise SystemExit(f"physics timestep {sim.dt} does not provide 500 Hz poses")
    inspector = None
    controller = None
    if control_replay is None:
        from classifier import Model
        from controller import Controller, Policy
        from vision import Inspector

        policy = Policy()
        inspector = Inspector(sim)
        if not hasattr(inspector, "component_members"):
            raise SystemExit("the pinned simulator lacks component membership provenance")
        controller = Controller(
            sim,
            inspector,
            Model.load(model_path),
            policy,
            jet_force=args.jet_force,
        )
    m, d = sim.model, sim.data
    machine = machine_geometry(mujoco, sim)

    pulses = []
    pulse_by_fire = {}
    original_fire = sim.fire

    def record_fire(nozzle, t_on, duration, force, uid=None):
        fire = original_fire(nozzle, t_on, duration, force, uid)
        if fire is not None:
            pulse = {
                "pulseId": len(pulses),
                "trackId": None if uid is None else int(uid),
                "nozzle": int(nozzle),
                "requestedOn": float(t_on),
                "requestedOff": float(t_on + duration),
                "forceN": float(force),
                "activationTime": None,
                "activeStepCount": 0,
                "contactStepCount": 0,
                "contactObjectIds": [],
            }
            pulses.append(pulse)
            pulse_by_fire[id(fire)] = pulse
        return fire

    sim.fire = record_fire
    contacts = []
    frames = []
    decisions = []
    decision_state = {}
    target_ids_by_track = {}
    seen_decisions = 0
    capture_every = max(1, round(0.004 / sim.dt))
    started = time.perf_counter()
    partial_path = args.output.with_name(args.output.stem + ".partial.jsonl")
    partial = partial_path.open("w")

    if control_replay is not None:
        for t_on, t_off, nozzle, force in control_replay["fires"]:
            sim.fire(nozzle, t_on, t_off - t_on, force)
        normalized_decisions = []
        for event in control_replay["decisions"]:
            if isinstance(event, dict):
                normalized_decisions.append([
                    event["time"],
                    2 if event["reject"] else 1,
                    event["objectIds"],
                    event["late"],
                ])
            else:
                normalized_decisions.append(event)
        replay_decisions = iter(normalized_decisions)
        next_replay_decision = next(replay_decisions, None)
        decisions = [
            {
                "time": float(event[0]),
                "state": int(event[1]),
                "objectIds": [int(value) for value in event[2]],
                "late": bool(event[3]),
                "source": "recorded control replay",
            }
            for event in normalized_decisions
        ]
    else:
        replay_decisions = None
        next_replay_decision = None

    def record_force_contacts():
        timestamp = float(d.time)
        if not sim.fires or not sim.active.any():
            return
        bodies = sim.all_bodies[sim.active]
        qpos = sim.qpos_adr[sim.active]
        pos = np.stack(
            [d.qpos[qpos], d.qpos[qpos + 1], d.qpos[qpos + 2]], axis=1
        )
        inflight = pos[:, 0] > 0.02
        for fire in sim.fires:
            if not (fire.t_on <= timestamp < fire.t_off):
                continue
            pulse = pulse_by_fire[id(fire)]
            if pulse["activationTime"] is None:
                pulse["activationTime"] = round(timestamp, 9)
            pulse["activeStepCount"] += 1
            hit = (
                inflight
                & (np.abs(pos[:, 0] - sim.L.ej_x) < JET_HALF_X)
                & (
                    np.abs(pos[:, 1] - sim.nozzle_y[fire.nozzle])
                    < JET_HALF_Y_FACTOR * sim.L.nozzle_pitch
                )
                & (pos[:, 2] > sim.L.belt_z - 0.07)
                & (pos[:, 2] < sim.L.belt_z + 0.03)
            )
            for body, position in zip(bodies[hit], pos[hit]):
                object_id = int(sim.bean_of[body].uid)
                contacts.append(
                    {
                        "time": round(timestamp, 9),
                        "pulseId": pulse["pulseId"],
                        "trackId": pulse["trackId"],
                        "objectId": object_id,
                        "position": [float(value) for value in position],
                        "forceN": pulse["forceN"],
                    }
                )
                pulse["contactStepCount"] += 1
                if object_id not in pulse["contactObjectIds"]:
                    pulse["contactObjectIds"].append(object_id)

    step_count = int(round(args.seconds / sim.dt))
    for step in range(step_count):
        contact_start = len(contacts)
        record_force_contacts()
        for contact in contacts[contact_start:]:
            partial.write(json.dumps({"type": "contact", **contact}) + "\n")
        sim.step()
        timestamp = float(d.time)
        while (
            next_replay_decision is not None
            and float(next_replay_decision[0]) <= timestamp + 1e-9
        ):
            state = int(next_replay_decision[1])
            for object_id in next_replay_decision[2]:
                decision_state[int(object_id)] = state
            next_replay_decision = next(replay_decisions, None)
        if controller is not None and step % capture_every == 0:
            rgb, captured_t = inspector.capture()
            blobs, _, _, _, tracks = controller.on_frame(rgb, captured_t)
            members = inspector.component_members(blobs)
            for decision in controller.decisions[seen_decisions:]:
                blob_indices = np.flatnonzero(tracks == decision.tid)
                object_ids = sorted(
                    {
                        int(uid)
                        for index in blob_indices
                        for uid in members[index]
                    }
                )
                target_ids_by_track[int(decision.tid)] = object_ids
                state = 2 if decision.reject else 1
                for object_id in object_ids:
                    decision_state[object_id] = state
                decisions.append(decision_row(decision, d.time, object_ids))
            seen_decisions = len(controller.decisions)

        if args.capture_start - 1e-9 <= timestamp <= args.capture_end + 1e-9:
            records = []
            for body, bean in sorted(
                sim.bean_of.items(), key=lambda pair: pair[1].uid
            ):
                address = sim.body_qpos[body]
                records.extend(
                    [
                        int(bean.uid),
                        *quantize(d.qpos[address : address + 3], 10000),
                        *quantize(d.qpos[address + 3 : address + 7], 10000),
                        decision_state.get(bean.uid, 0),
                    ]
                )
            resolved = [bean for bean in sim.beans if bean.outcome]
            counters = [
                len(resolved),
                sum(bean.defect and bean.outcome == "reject" for bean in resolved),
                sum(
                    not bean.defect and bean.outcome in ("reject", "spilled")
                    for bean in resolved
                ),
            ]
            frame = {"t": round(timestamp, 9), "beans": records, "counters": counters}
            frames.append(frame)
            partial.write(json.dumps({"type": "frame", **frame}) + "\n")
        if step % max(1, round(0.25 / sim.dt)) == 0:
            partial.flush()
            print(
                f"t={timestamp:.3f}s/{args.seconds:.3f}s "
                f"frames={len(frames)} contacts={len(contacts)}",
                flush=True,
            )

    partial.close()
    if inspector is not None:
        inspector.r.close()
    wall_seconds = time.perf_counter() - started
    for contact in contacts:
        if contact["trackId"] is None:
            contact["associatedTarget"] = None
        else:
            targets = target_ids_by_track.get(contact["trackId"], [])
            contact["associatedTarget"] = contact["objectId"] in targets
    for pulse in pulses:
        pulse["contactObjectIds"].sort()
        pulse["targetObjectIds"] = (
            None
            if pulse["trackId"] is None
            else target_ids_by_track.get(pulse["trackId"], [])
        )

    class_ids = {item.name: index for index, item in enumerate(profile.classes)}
    beans = [
        [
            int(bean.uid),
            class_ids[bean.cls],
            *quantize(bean.axes, 100000),
            round(float(bean.spawn_t), 9),
            bean.outcome,
            None if bean.resolved_t is None else round(float(bean.resolved_t), 9),
        ]
        for bean in sim.beans
    ]
    evaluator_rows = [
        {
            "objectId": int(bean.uid),
            "spawnTime": round(float(bean.spawn_t), 9),
            "class": bean.cls,
            "outcome": bean.outcome,
            "outcomeTime": (
                None if bean.resolved_t is None else round(float(bean.resolved_t), 9)
            ),
            "manualInjection": False,
            "engineEpoch": None,
        }
        for bean in sim.beans
    ]
    source_hashes = {name: file_hash(source / name) for name in SOURCE_FILES}
    config = {
        "seconds": float(d.time),
        "captureStart": args.capture_start,
        "captureEnd": args.capture_end,
        "fps": round(1 / sim.dt),
        "physicsTimestep": float(sim.dt),
        "rate": args.rate,
        "jetForce": args.jet_force,
        "seed": args.seed,
        "policy": policy_data,
    }
    control_config = control_replay["config"] if control_replay is not None else config
    payload = {
        "version": 1,
        "source": {
            "revision": source_revision,
            "files": source_hashes,
            "modelSha256": file_hash(model_path),
            "mujoco": mujoco.__version__,
            "controlReplaySha256": (
                None if args.control_replay is None else file_hash(args.control_replay)
            ),
            "valveScheduleSha256": (
                None
                if control_replay is None
                else json_hash(control_replay["fires"])
            ),
        },
        "engine": {
            "simulator": "SorterSim",
            "inspector": "Inspector" if inspector is not None else None,
            "controller": (
                "Controller" if controller is not None else "recorded valve schedule"
            ),
            "profile": "green_arabica",
            "policySha256": json_hash(policy_data),
            "presetSha256": json_hash(control_config),
            "recordedControlDecisions": control_replay is not None,
            "freshClassifierEvaluation": control_replay is None,
            "newPhysicsSamples": True,
            "cameraSkipEffects": (
                None
                if control_replay is None
                else "The skipped camera changes render groups only. It does not consume the simulator RNG or write physics state."
            ),
        },
        "preset": control_config,
        "config": config,
        "layout": asdict(sim.L),
        "machine": machine,
        "classes": [
            {
                "name": item.name,
                "defect": item.defect,
                "rgb": item.rgb,
                "shape": item.shape,
            }
            for item in profile.classes
        ],
        "schema": {
            "stride": 9,
            "row": "uid,x,y,z,qw,qx,qy,qz,decision",
            "positionScale": 10000,
            "quaternionScale": 10000,
            "axesScale": 100000,
            "bean": "uid,class,ax,ay,az,spawnTime,outcome,resolvedTime",
            "decision": "0 unknown; 1 accept; 2 reject",
            "counters": "resolved beans; defects in reject; good in reject or spilled",
            "quaternionOrder": "w,x,y,z",
            "contact": "direct force membership at the pre-force physics-step pose",
        },
        "beans": beans,
        "frames": frames,
        "fires": [
            [
                pulse["requestedOn"],
                pulse["requestedOff"],
                pulse["nozzle"],
                pulse["forceN"],
            ]
            for pulse in pulses
        ],
        "pulses": pulses,
        "contacts": contacts,
        "decisions": decisions,
        "evaluatorRows": evaluator_rows,
        "scoreSnapshots": [
            {"time": frame["t"], "counters": frame["counters"], "engineEpoch": None}
            for frame in frames
        ],
        "limitations": [
            "Control replay uses source-recorded valve times without timing adjustments.",
            "Recorded valve events do not include controller track IDs or target associations.",
            "This run samples new physics from recorded commands. It does not evaluate the classifier again.",
        ] if control_replay is not None else [],
    }
    return payload, wall_seconds, partial_path


def main():
    args = parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    manifest_path = args.manifest or args.output.with_name("manifest.json")
    args.lock_file.parent.mkdir(parents=True, exist_ok=True)
    with args.lock_file.open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("shared runtime lock is busy")
        print(f"acquired shared runtime lock: {args.lock_file}", flush=True)
        payload, wall_seconds, partial_path = capture(args)
        fcntl.flock(lock, fcntl.LOCK_UN)
        print(f"released shared runtime lock: {args.lock_file}", flush=True)

    args.output.write_text(json.dumps(payload, separators=(",", ":"), allow_nan=False))
    replay_hash = file_hash(args.output)
    times = [frame["t"] for frame in payload["frames"]]
    manifest = {
        "replay": str(args.output),
        "replaySha256": replay_hash,
        "bytes": args.output.stat().st_size,
        "sourceRevision": payload["source"]["revision"],
        "sourceFilesSha256": payload["source"]["files"],
        "modelSha256": payload["source"]["modelSha256"],
        "controlReplaySha256": payload["source"]["controlReplaySha256"],
        "valveScheduleSha256": payload["source"]["valveScheduleSha256"],
        "policySha256": payload["engine"]["policySha256"],
        "presetSha256": payload["engine"]["presetSha256"],
        "frameCount": len(times),
        "firstFrameTime": times[0] if times else None,
        "lastFrameTime": times[-1] if times else None,
        "sampleRateHz": payload["config"]["fps"],
        "pulseCount": len(payload["pulses"]),
        "contactStepCount": len(payload["contacts"]),
        "objectCount": len(payload["beans"]),
        "wallSeconds": wall_seconds,
        "partialJsonl": str(partial_path),
        "partialJsonlSha256": file_hash(partial_path),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest), flush=True)


if __name__ == "__main__":
    main()
