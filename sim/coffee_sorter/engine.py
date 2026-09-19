"""Bounded coffee sorter engine shared by diagnostics and the live service."""
from __future__ import annotations

import os

for _thread_variable in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
    os.environ[_thread_variable] = "1"

import argparse
from collections import Counter, deque
from dataclasses import asdict
import hashlib
from importlib.metadata import PackageNotFoundError, version as package_version
import json
from pathlib import Path
import platform
import subprocess
import time
import uuid

import numpy as np
from threadpoolctl import threadpool_info

from classifier import Model
from controller import Controller, Policy
from profiles import PROFILES
from scene import Layout
from sim import SorterSim
from vision import Inspector


HERE = Path(__file__).resolve().parent
SOURCE_FILES = ("engine.py", "controller.py", "sim.py", "vision.py", "classifier.py", "profiles.py", "scene.py")


def _json_hash(value) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _wilson(successes: int, total: int) -> list[float] | None:
    if total == 0:
        return None
    z = 1.96
    p = successes / total
    centre = (p + z * z / (2 * total)) / (1 + z * z / total)
    half = z * np.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / (1 + z * z / total)
    return [max(0.0, centre - half), min(1.0, centre + half)]


def _timing_summary(values: list[float], sim_time_s: float) -> dict:
    sample = np.asarray(values, dtype=float)
    if not len(sample):
        return {"count": 0, "total_ms": 0.0, "per_sim_second_ms": None,
                "p50_ms": None, "p95_ms": None, "p99_ms": None, "max_ms": None}
    total = float(sample.sum())
    return {
        "count": int(len(sample)),
        "total_ms": total,
        "per_sim_second_ms": total / sim_time_s if sim_time_s > 0 else None,
        "p50_ms": float(np.percentile(sample, 50)),
        "p95_ms": float(np.percentile(sample, 95)),
        "p99_ms": float(np.percentile(sample, 99)),
        "max_ms": float(sample.max()),
    }


class Engine:
    """One simulator, camera, model, and controller session."""

    def __init__(self, preset_path):
        startup_started = time.perf_counter()
        self.preset_path = Path(preset_path).resolve()
        self.preset = json.loads(self.preset_path.read_text())
        self.session_id = str(uuid.uuid4())
        self.seq = 0
        self._started_wall: float | None = None
        self._closed = False
        self._step_index = 0
        self._decision_index = 0
        self._event_id = 0
        self._event_counts = Counter()
        self._seen_outcomes: set[int] = set()
        self._seen_fired_tracks: set[int] = set()
        self._seen_fire_hits: set[tuple[int, int]] = set()
        self._track_members: dict[int, dict] = {}
        self._decision_by_track: dict[int, object] = {}
        self._decision_by_uid: dict[int, object] = {}
        self._decision_evidence: list[dict] = []
        self._object_records: dict[int, dict] = {}
        self._injected_ids: set[int] = set()
        self._recent_resolved = deque(maxlen=int(self.preset["limits"]["max_recent_resolved_objects"]))
        self._events = deque(maxlen=int(self.preset["limits"]["max_events"]))
        self._timings = {name: [] for name in ("physics_ms", "render_ms", "evaluation_ms",
                                               "snapshot_ms", "control_path_ms", "camera_frame_ms")}
        self._peak_active = 0

        profile_name = self.preset["profile"]
        if profile_name not in PROFILES:
            raise ValueError(f"unsupported profile: {profile_name}")
        self.profile = PROFILES[profile_name]
        layout = Layout(**self.preset["layout"])
        self.sim = SorterSim(self.profile, layout, rate=float(self.preset["requested_rate"]),
                             seed=int(self.preset["seed"]))
        self.inspector = Inspector(self.sim)

        model_path = Path(self.preset["model_path"])
        self.model_path = model_path if model_path.is_absolute() else HERE / model_path
        if not self.model_path.is_file():
            self.inspector.close()
            raise FileNotFoundError(f"trusted model is missing: {self.model_path}")
        self.model = Model.load(self.model_path)
        policy_config = self.preset["policy"]
        self.policy = Policy(
            name=policy_config["name"],
            reject_severities=tuple(policy_config["reject_severities"]),
            threshold=float(policy_config["threshold"]),
            anomaly=bool(policy_config["anomaly"]),
            base_pulse=float(policy_config["base_pulse_s"]),
            ref_mass=float(policy_config["ref_mass_kg"]),
            max_pulse=float(policy_config["max_pulse_s"]),
            lead=float(policy_config["lead_s"]),
            latency_floor=float(policy_config["latency_floor_s"]),
            induced_delay=float(policy_config["induced_delay_s"]),
            fixed_latency=policy_config["fixed_latency_s"],
            target_nozzles=policy_config["target_nozzles"],
        )
        self.controller = Controller(self.sim, self.inspector, self.model, self.policy,
                                     jet_force=float(self.preset["jet_force_n"]))
        self.model_version = _file_hash(self.model_path)
        self.policy_version = _json_hash(asdict(self.policy))
        self.preset_version = _json_hash(self.preset)
        try:
            self.source_revision = subprocess.check_output(
                ["git", "-C", str(HERE), "rev-parse", "HEAD"], text=True,
                stderr=subprocess.DEVNULL).strip()
        except (OSError, subprocess.CalledProcessError):
            self.source_revision = None
        self.packages = {}
        for name in ("mujoco", "numpy", "opencv-python-headless", "scikit-learn", "joblib", "aiohttp"):
            try:
                self.packages[name] = package_version(name)
            except PackageNotFoundError:
                self.packages[name] = None
        self.source_hashes = {name: _file_hash(HERE / name) for name in SOURCE_FILES}
        self.startup_seconds = time.perf_counter() - startup_started

    def start(self):
        """Start active wall measurement once."""
        if self._started_wall is None:
            self._started_wall = time.perf_counter()

    def _wall_elapsed(self) -> float:
        return 0.0 if self._started_wall is None else time.perf_counter() - self._started_wall

    def _event(self, event_type: str, *, object_id: int | None = None,
               object_ids: tuple[int, ...] | list[int] | None = None, **fields):
        self._event_id += 1
        self._event_counts[event_type] += 1
        event = {"event_id": self._event_id, "type": event_type,
                 "sim_time_s": float(self.sim.data.time)}
        if object_id is not None:
            event["object_id"] = int(object_id)
        if object_ids is not None:
            event["object_ids"] = [int(uid) for uid in object_ids]
        event.update(fields)
        self._events.append(event)

    def _register_bean(self, bean, injected: bool = False):
        if bean.uid in self._object_records:
            return
        spec = self.profile.by_name(bean.cls)
        geom = self.sim.body_geom[bean.body]
        self._object_records[bean.uid] = {
            "object_id": bean.uid,
            "truth_class": bean.cls,
            "required_reject": bool(bean.defect and spec.severity in self.policy.reject_severities),
            "injected": injected,
            "spawn_time_s": float(bean.spawn_t),
            "spawn_wall": time.perf_counter() if injected else None,
            "spawn_to_outcome_wall_s": None,
            "appearance_key": hashlib.sha256(f"{self.session_id}:{bean.uid}".encode()).hexdigest()[:20],
            "shape": spec.shape,
            "axes": [float(value) for value in bean.axes],
            "rgb": [float(value) for value in self.sim.model.geom_rgba[geom, :3]],
            "pos": None,
            "quat": None,
            "decisions": [],
            "associated_rejection_tracks": set(),
        }
        if injected:
            self._injected_ids.add(bean.uid)
            self._event("injected", object_id=bean.uid)
        else:
            self._event("spawned", object_id=bean.uid)

    def _update_active_records(self):
        for body, bean in self.sim.bean_of.items():
            self._register_bean(bean)
            qa = self.sim.body_qpos[body]
            pose = self.sim.data.qpos[qa:qa + 7]
            record = self._object_records[bean.uid]
            record["pos"] = [float(value) for value in pose[:3]]
            record["quat"] = [float(value) for value in pose[3:7]]
        self._peak_active = max(self._peak_active, self.sim.n_active())

    def _physical_events(self):
        for track_id in sorted(self.sim.fired_targets - self._seen_fired_tracks):
            decision = self._decision_by_track.get(track_id)
            uids = decision.target_uids if decision else ()
            for uid in uids:
                if uid in self.sim.bean_by_uid:
                    self.sim.bean_by_uid[uid].fired_target = True
            self._event("valve_activated", object_ids=uids, track_id=int(track_id))
        self._seen_fired_tracks = set(self.sim.fired_targets)

        for track_id, uid in sorted(self.sim.fire_hits - self._seen_fire_hits):
            decision = self._decision_by_track.get(track_id)
            event_type = "own_pulse_hit" if decision and uid in decision.target_uids else "collateral_jet_hit"
            self._event(event_type, object_id=uid, track_id=int(track_id))
        self._seen_fire_hits = set(self.sim.fire_hits)

        for bean in self.sim.beans:
            if bean.outcome is None or bean.uid in self._seen_outcomes:
                continue
            self._seen_outcomes.add(bean.uid)
            self._recent_resolved.append(bean.uid)
            record = self._object_records.get(bean.uid)
            if record is not None and record["spawn_wall"] is not None:
                record["spawn_to_outcome_wall_s"] = time.perf_counter() - record["spawn_wall"]
            if record is not None and bean.last_pos is not None:
                record["pos"] = [float(value) for value in bean.last_pos]
            self._event("outcome", object_id=bean.uid, outcome=bean.outcome)

    def _evaluate_frame(self, blobs, full, blob_tracks, captured_t: float):
        members = self.inspector.component_members(blobs)
        for blob_index in range(blobs.n):
            if not full[blob_index]:
                continue
            uids = tuple(int(uid) for uid in np.unique(members[blob_index]))
            track_id = int(blob_tracks[blob_index])
            if track_id >= 0:
                self._track_members[track_id] = {"uids": uids, "sim_time_s": float(captured_t)}
            for uid in uids:
                bean = self.sim.bean_by_uid.get(uid)
                if bean is None:
                    continue
                bean.camera_observations += 1
                bean.merged_observations += int(len(uids) >= 2)

        new_decisions = self.controller.decisions[self._decision_index:]
        self._decision_index = len(self.controller.decisions)
        for decision in new_decisions:
            self._decision_by_track[decision.tid] = decision
            association = self._track_members.get(decision.tid)
            uids = association["uids"] if association else ()
            decision.target_uids = tuple(uid for uid in uids if uid in self.sim.bean_by_uid)
            association_source = "unknown"
            if association:
                association_source = ("current_component" if abs(association["sim_time_s"] - captured_t) < 1e-9
                                      else "last_component")
            evidence = {
                "track_id": decision.tid,
                "sim_time_s": float(decision.t_decided),
                "predicted_class": decision.cls,
                "reject": bool(decision.reject),
                "scheduled": bool(decision.scheduled),
                "late": bool(decision.late),
                "association": association_source,
                "association_approximate": association is not None,
                "object_ids": [int(uid) for uid in decision.target_uids],
            }
            self._decision_evidence.append(evidence)
            for uid in decision.target_uids:
                record = self._object_records[uid]
                record["decisions"].append(evidence)
                self._decision_by_uid[uid] = decision
                if decision.reject:
                    record["associated_rejection_tracks"].add(decision.tid)
                    if decision.scheduled:
                        self.sim.bean_by_uid[uid].targeted = True
            self._event("decision", object_ids=decision.target_uids, track_id=int(decision.tid))

    def step(self):
        """Advance one physics step and run the camera on its configured schedule."""
        if self._closed:
            raise RuntimeError("engine is closed")
        self.start()
        started = time.perf_counter()
        self._update_active_records()
        evaluation_ms = (time.perf_counter() - started) * 1e3
        bean_count = len(self.sim.beans)
        started = time.perf_counter()
        self.sim.step()
        self._timings["physics_ms"].append((time.perf_counter() - started) * 1e3)
        started = time.perf_counter()
        for bean in self.sim.beans[bean_count:]:
            self._register_bean(bean)
        self._update_active_records()
        self._physical_events()
        evaluation_ms += (time.perf_counter() - started) * 1e3

        if self._step_index % int(self.preset["camera_every_steps"]) == 0:
            frame_started = time.perf_counter()
            started = time.perf_counter()
            frame, captured_t = self.inspector.capture()
            self._timings["render_ms"].append((time.perf_counter() - started) * 1e3)
            blobs, _, _, full, blob_tracks = self.controller.on_frame(frame, captured_t)
            self._timings["control_path_ms"].append(
                self.controller.detection_ms[-1] + self.controller.inference_ms[-1] +
                self.controller.control_ms[-1])
            started = time.perf_counter()
            self._evaluate_frame(blobs, full, blob_tracks, captured_t)
            evaluation_ms += (time.perf_counter() - started) * 1e3
            self._timings["camera_frame_ms"].append((time.perf_counter() - frame_started) * 1e3)
        self._timings["evaluation_ms"].append(evaluation_ms)
        self._step_index += 1

    def inject(self, class_name) -> int:
        """Spawn one requested physical object or raise ValueError."""
        try:
            spec = self.profile.by_name(class_name)
        except StopIteration:
            raise ValueError(f"unsupported class: {class_name}") from None
        spawn_accum = self.sim.spawn_accum
        bean = self.sim.spawn(spec)
        if bean is None:
            self.sim.spawn_accum = spawn_accum
            raise ValueError("injection failed because the spawn area is full")
        self._register_bean(bean, injected=True)
        self._update_active_records()
        self.start()
        return bean.uid

    def _snapshot_object(self, uid: int, active: bool) -> dict:
        bean = self.sim.bean_by_uid[uid]
        record = self._object_records[uid]
        decision = self._decision_by_uid.get(uid)
        decision_evidence = record["decisions"][-1] if record["decisions"] else None
        own_pulse_hit = any(
            (track_id, uid) in self.sim.fire_hits
            for track_id in record["associated_rejection_tracks"]
        )
        return {
            "object_id": uid,
            "spawn_to_outcome_wall_s": record["spawn_to_outcome_wall_s"],
            "active": active,
            "appearance_key": record["appearance_key"],
            "shape": record["shape"],
            "axes": record["axes"],
            "pos": record["pos"],
            "quat": record["quat"],
            "rgb": record["rgb"],
            "decision": (None if decision is None else {
                "track_id": decision.tid,
                "predicted_class": decision.cls,
                "reject": bool(decision.reject),
                "scheduled": bool(decision.scheduled),
                "late": bool(decision.late),
                "association": decision_evidence["association"],
                "association_approximate": decision_evidence["association_approximate"],
            }),
            "outcome": bean.outcome,
            "jet_hits": int(bean.jet_hits),
            "own_pulse_hit": own_pulse_hit,
        }

    def snapshot(self) -> dict:
        """Return one bounded, truth-free transport snapshot."""
        started = time.perf_counter()
        self.seq += 1
        active_ids = {bean.uid for bean in self.sim.bean_of.values()}
        retained = active_ids | self._injected_ids | set(self._recent_resolved)
        sim_time = float(self.sim.data.time)
        wall_elapsed = self._wall_elapsed()
        result = {
            "protocol_version": 1,
            "session_id": self.session_id,
            "seq": self.seq,
            "sim_time_s": sim_time,
            "wall_elapsed_s": wall_elapsed,
            "model_version": self.model_version,
            "policy_version": self.policy_version,
            "preset_version": self.preset_version,
            "engine_rate": sim_time / wall_elapsed if wall_elapsed > 0 else 0.0,
            "requested_rate": float(self.preset["requested_rate"]),
            "admitted_rate": len(self.sim.beans) / sim_time if sim_time > 0 else 0.0,
            "layout": asdict(self.sim.L),
            "objects": [self._snapshot_object(uid, uid in active_ids) for uid in sorted(retained)],
            "events": list(self._events)[-50:],
        }
        self._timings["snapshot_ms"].append((time.perf_counter() - started) * 1e3)
        return result

    def _object_evidence(self, bean, cohort: bool) -> dict:
        record = self._object_records[bean.uid]
        rejection_tracks = record["associated_rejection_tracks"]
        own_hits = sorted(track_id for track_id in rejection_tracks
                          if (track_id, bean.uid) in self.sim.fire_hits)
        activated = sorted(track_id for track_id in rejection_tracks if track_id in self.sim.fired_targets)
        missed_category = None
        if cohort and record["required_reject"] and bean.outcome != "reject":
            if bean.outcome is None:
                missed_category = "unresolved"
            elif bean.camera_observations == 0:
                missed_category = "not_detected"
            elif bean.merged_observations and not rejection_tracks:
                missed_category = "merged_without_target"
            elif not rejection_tracks:
                missed_category = "classification_or_tracking"
            elif not own_hits:
                missed_category = "targeted_not_hit"
            else:
                missed_category = "hit_not_captured"
        return {
            "object_id": bean.uid,
            "truth_class": bean.cls,
            "spawn_time_s": float(bean.spawn_t),
            "outcome": bean.outcome,
            "resolved_time_s": bean.resolved_t,
            "in_cohort": cohort,
            "required_reject": record["required_reject"],
            "injected": record["injected"],
            "spawn_to_outcome_wall_s": record["spawn_to_outcome_wall_s"],
            "full_camera_observations": int(bean.camera_observations),
            "merged_observations": int(bean.merged_observations),
            "ever_merged": bool(bean.merged_observations),
            "predictions": record["decisions"],
            "associated_rejection_tracks": sorted(rejection_tracks),
            "scheduled_target": bool(bean.targeted),
            "activated_rejection_tracks": activated,
            "any_jet_hit": bool(bean.jet_hits),
            "jet_hit_steps": int(bean.jet_hits),
            "own_pulse_hit_tracks": own_hits,
            "missed_category": missed_category,
            "captured_without_own_pulse_hit": bool(bean.outcome == "reject" and not own_hits),
        }

    def report(self) -> dict:
        """Return post-control evaluation truth, attribution, and timing evidence."""
        sim_time = float(self.sim.data.time)
        cohort_end = sim_time - 0.6
        in_cohort = {bean.uid for bean in self.sim.beans if 0.8 <= bean.spawn_t <= cohort_end}
        evidence = [self._object_evidence(bean, bean.uid in in_cohort) for bean in self.sim.beans]
        cohort = [row for row in evidence if row["in_cohort"]]
        required = [row for row in cohort if row["required_reject"]]
        keep = [row for row in cohort if not row["required_reject"]]
        captured = sum(row["outcome"] == "reject" for row in required)
        good_lost = sum(row["outcome"] in ("reject", "spilled") for row in keep)
        unresolved = sum(row["outcome"] is None for row in cohort)
        loss_partition = Counter(row["missed_category"] for row in required if row["missed_category"])
        wall_elapsed = self._wall_elapsed()
        timings = {
            "startup_seconds": self.startup_seconds,
            "active_wall_seconds": wall_elapsed,
            "physics": _timing_summary(self._timings["physics_ms"], sim_time),
            "inspection_render": _timing_summary(self._timings["render_ms"], sim_time),
            "detection": _timing_summary(self.controller.detection_ms, sim_time),
            "model_inference": _timing_summary(self.controller.inference_ms, sim_time),
            "control": _timing_summary(self.controller.control_ms, sim_time),
            "control_path": _timing_summary(self._timings["control_path_ms"], sim_time),
            "evaluation": _timing_summary(self._timings["evaluation_ms"], sim_time),
            "snapshot": _timing_summary(self._timings["snapshot_ms"], sim_time),
            "camera_frame": _timing_summary(self._timings["camera_frame_ms"], sim_time),
            "camera_interval_ms": self.sim.dt * int(self.preset["camera_every_steps"]) * 1000,
            "control_path_overruns": sum(
                value > self.sim.dt * int(self.preset["camera_every_steps"]) * 1000
                for value in self._timings["control_path_ms"]),
            "camera_overruns": sum(value > self.sim.dt * int(self.preset["camera_every_steps"]) * 1000
                                    for value in self._timings["camera_frame_ms"]),
        }
        measured_ms = sum(timings[name]["total_ms"] for name in ("physics", "inspection_render", "detection", "model_inference", "control", "evaluation", "snapshot"))
        timings["unattributed_wall_ms"] = max(0.0, wall_elapsed * 1000 - measured_ms)
        timings["unattributed_wall_note"] = "Includes serialization, IPC, process scheduling, and loop overhead. See service-profile.json for HTTP timings."

        return {
            "protocol_version": 1,
            "session_id": self.session_id,
            "preset": self.preset,
            "versions": {
                "preset": self.preset_version,
                "model": self.model_version,
                "policy": self.policy_version,
                "source_revision": self.source_revision,
                "source_sha256": self.source_hashes,
            },
            "runtime": {
                "simulation_seconds": sim_time,
                "active_wall_seconds": wall_elapsed,
                "engine_rate": sim_time / wall_elapsed if wall_elapsed > 0 else 0.0,
                "requested_rate": float(self.preset["requested_rate"]),
                "admitted_rate": len(self.sim.beans) / sim_time if sim_time > 0 else 0.0,
                "objects_spawned": len(self.sim.beans),
                "peak_active_bodies": self._peak_active,
                "pool_starved": self.sim.starved,
                "platform": platform.platform(),
                "python": platform.python_version(),
                "native_threadpools": [{key: pool.get(key) for key in ("internal_api", "prefix", "version", "num_threads")} for pool in threadpool_info()],
                "packages": self.packages,
                "native_thread_limits": {name: os.environ.get(name) for name in
                                         ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
                                          "VECLIB_MAXIMUM_THREADS")},
            },
            "quality": {
                "cohort_start_s": 0.8,
                "cohort_end_s": cohort_end,
                "eligible_objects": len(cohort),
                "required_defects": len(required),
                "keep_objects": len(keep),
                "captured_required_defects": captured,
                "capture": captured / len(required) if required else None,
                "capture_interval_95": _wilson(captured, len(required)),
                "good_objects_lost": good_lost,
                "good_loss": good_lost / len(keep) if keep else None,
                "good_loss_interval_95": _wilson(good_lost, len(keep)),
                "unresolved_objects": unresolved,
                "unresolved_rate": unresolved / len(cohort) if cohort else None,
                "spills_in_denominators": True,
                "unresolved_in_denominators": True,
            },
            "loss_partition": {name: int(loss_partition.get(name, 0)) for name in
                               ("unresolved", "not_detected", "merged_without_target",
                                "classification_or_tracking", "targeted_not_hit", "hit_not_captured")},
            "diagnostics": {
                "categories_are_partition_labels_not_proven_causes": True,
                "captures_without_own_pulse_hit": sum(
                    row["captured_without_own_pulse_hit"] for row in required),
                "associated_decisions": sum(bool(row["object_ids"]) for row in self._decision_evidence),
                "unassociated_decisions": sum(not row["object_ids"] for row in self._decision_evidence),
                "last_component_associations": sum(
                    row["association"] == "last_component" for row in self._decision_evidence),
                "total_events": self._event_id,
                "events_retained": len(self._events),
                "window_start_event_id": self._events[0]["event_id"] if self._events else None,
                "event_evidence_scope": "retained_window",
                "event_counts": dict(self._event_counts),
            },
            "timings": timings,
            "objects": evidence,
            "decision_evidence": self._decision_evidence,
        }

    def close(self):
        if not self._closed:
            self.inspector.close()
            self._closed = True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preset", type=Path, required=True)
    parser.add_argument("--seconds", type=float, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if not 0 < args.seconds <= 10:
        parser.error("seconds must be in the range 0 to 10")

    engine = Engine(args.preset)
    next_snapshot_wall = 0.0
    try:
        while engine.sim.data.time + 1e-12 < args.seconds:
            engine.step()
            if engine._wall_elapsed() >= next_snapshot_wall:
                engine.snapshot()
                next_snapshot_wall += 0.1
        report = engine.report()
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / "report.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
        with (args.out / "evidence.jsonl").open("w") as output:
            for event in engine._events:
                output.write(json.dumps({"kind": "event", "scope": "retained_window", **event},
                                        allow_nan=False) + "\n")
            for row in report["objects"]:
                output.write(json.dumps({"kind": "object", **row}, allow_nan=False) + "\n")
        print(json.dumps({"report": str(args.out / "report.json"),
                          "evidence": str(args.out / "evidence.jsonl"),
                          "simulation_seconds": report["runtime"]["simulation_seconds"],
                          "wall_seconds": report["runtime"]["active_wall_seconds"],
                          "engine_rate": report["runtime"]["engine_rate"]}, allow_nan=False))
    finally:
        engine.close()


if __name__ == "__main__":
    main()
