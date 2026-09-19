"""Measure scheduled jet pulses without changing controller or simulator behavior."""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys
from types import MethodType


ROOT = Path(__file__).resolve().parents[4]
SIM_DIR = ROOT / "sim" / "coffee_sorter"
sys.path.insert(0, str(SIM_DIR))

from engine import Engine  # noqa: E402
from sim import JET_HALF_X, JET_HALF_Y_FACTOR  # noqa: E402
import numpy as np  # noqa: E402


DEFAULT_PRESET = SIM_DIR / "configs" / "default_demo.json"
CONTROLLER_LATE_ALLOWANCE_S = 0.002


class PulseMeasurements:
    """Collect evaluation evidence around the existing simulation step."""

    def __init__(self, engine: Engine):
        self.engine = engine
        self.sim = engine.sim
        self._pulse_by_fire: dict[int, dict] = {}
        self._fire_by_id: dict[int, object] = {}
        self._next_pulse_id = 0
        self._sample_time: float | None = None
        self.feature_batches: list[np.ndarray] = []

    def install(self):
        original_sim_step = self.sim.step
        original_engine_step = self.engine.step
        original_predict = self.engine.model.predict

        def measured_sim_step(_sim):
            self._before_sim_step()
            original_sim_step()
            self._after_sim_step()

        def measured_engine_step(_engine):
            original_engine_step()
            self._discover_fires()

        def measured_predict(_model, features):
            self.feature_batches.append(np.array(features, copy=True))
            return original_predict(features)

        self.sim.step = MethodType(measured_sim_step, self.sim)
        self.engine.step = MethodType(measured_engine_step, self.engine)
        self.engine.model.predict = MethodType(measured_predict, self.engine.model)

    def _decision_evidence(self, track_id: int | None):
        if track_id is None:
            return None
        for evidence in reversed(self.engine._decision_evidence):
            if evidence["track_id"] == track_id:
                return evidence
        return None

    def _discover_fires(self):
        for fire in self.sim.fires:
            fire_key = id(fire)
            track_id = int(fire.uid) if fire.uid is not None else None
            decision = self.engine._decision_by_track.get(track_id)
            evidence = self._decision_evidence(track_id)
            association = self.engine._track_members.get(track_id)
            if fire_key not in self._pulse_by_fire:
                requested_on = None if decision is None else float(
                    decision.t_fire - self.engine.policy.lead
                )
                self._next_pulse_id += 1
                pulse = {
                    "pulse_id": self._next_pulse_id,
                    "track_id": track_id,
                    "nozzle": int(fire.nozzle),
                    "nozzle_y_m": float(self.sim.nozzle_y[fire.nozzle]),
                    "target_fire_time_s": None if decision is None else float(decision.t_fire),
                    "available_time_s": None if decision is None else float(decision.t_available),
                    "decision_camera_x_m": None if decision is None else float(decision.x),
                    "decision_camera_y_m": None if decision is None else float(decision.y),
                    "estimated_forward_speed_m_s": None if decision is None else float(decision.v),
                    "observed_frame_age_s": (
                        None if decision is None or association is None else
                        float(decision.t_decided - association["sim_time_s"])
                    ),
                    "requested_on_time_s": requested_on,
                    "scheduled_on_time_s": float(fire.t_on),
                    "scheduled_off_time_s": float(fire.t_off),
                    "scheduled_duration_s": float(fire.t_off - fire.t_on),
                    "requested_pulse_s": None if decision is None else float(decision.pulse),
                    "deadline_headroom_s": None if decision is None else float(
                        decision.t_fire + CONTROLLER_LATE_ALLOWANCE_S - decision.t_available
                    ),
                    "requested_lead_headroom_s": None if decision is None else float(
                        decision.t_fire - self.engine.policy.lead - decision.t_available
                    ),
                    "availability_clamp_s": None if requested_on is None else float(
                        max(0.0, fire.t_on - requested_on)
                    ),
                    "controller_late": None if decision is None else bool(decision.late),
                    "association": None,
                    "association_approximate": None,
                    "target_object_ids": [],
                    "activated": False,
                    "activation_time_s": None,
                    "intended_active_steps": 0,
                    "first_sample_time_s": None,
                    "last_sample_time_s": None,
                    "target_samples": [],
                    "contacts": [],
                }
                self._pulse_by_fire[fire_key] = pulse
                self._fire_by_id[fire_key] = fire
            pulse = self._pulse_by_fire[fire_key]
            if decision is not None:
                pulse["target_object_ids"] = [int(uid) for uid in decision.target_uids]
            if evidence is not None:
                pulse["association"] = evidence["association"]
                pulse["association_approximate"] = bool(evidence["association_approximate"])

    def _before_sim_step(self):
        self._discover_fires()
        t = float(self.sim.data.time)
        self._sample_time = t
        bodies, positions, _ = self.sim.active_state()
        rows = []
        row_by_uid = {}
        for row_index, body in enumerate(bodies):
            bean = self.sim.bean_of[int(body)]
            rows.append((bean.uid, positions[row_index]))
            row_by_uid[bean.uid] = positions[row_index]

        for fire_key, pulse in self._pulse_by_fire.items():
            fire = self._fire_by_id[fire_key]
            if not fire.t_on <= t < fire.t_off:
                continue
            pulse["intended_active_steps"] += 1
            pulse["first_sample_time_s"] = (
                t if pulse["first_sample_time_s"] is None else pulse["first_sample_time_s"]
            )
            pulse["last_sample_time_s"] = t
            target_ids = set(pulse["target_object_ids"])

            for uid in sorted(target_ids):
                position = row_by_uid.get(uid)
                if position is None:
                    pulse["target_samples"].append({
                        "sim_time_s": t,
                        "object_id": uid,
                        "active": False,
                        "position_m": None,
                        "inflight": False,
                        "x_member": False,
                        "y_member": False,
                        "z_member": False,
                        "xy_member": False,
                        "xyz_member": False,
                        "physics_contact": False,
                    })
                    continue
                membership = self._membership(fire, position)
                pulse["target_samples"].append({
                    "sim_time_s": t,
                    "object_id": uid,
                    "active": True,
                    "position_m": [float(value) for value in position],
                    **membership,
                })

            for uid, position in rows:
                membership = self._membership(fire, position)
                if not membership["physics_contact"]:
                    continue
                pulse["contacts"].append({
                    "sim_time_s": t,
                    "object_id": int(uid),
                    "is_associated_target": uid in target_ids,
                    "position_m": [float(value) for value in position],
                })

    def _membership(self, fire, position) -> dict:
        layout = self.sim.L
        x, y, z = (float(value) for value in position)
        inflight = x > 0.02
        x_member = abs(x - layout.ej_x) < JET_HALF_X
        y_member = abs(y - float(self.sim.nozzle_y[fire.nozzle])) < (
            JET_HALF_Y_FACTOR * layout.nozzle_pitch
        )
        z_member = layout.belt_z - 0.07 < z < layout.belt_z + 0.03
        xy_member = x_member and y_member
        xyz_member = xy_member and z_member
        return {
            "inflight": inflight,
            "x_member": x_member,
            "y_member": y_member,
            "z_member": z_member,
            "xy_member": xy_member,
            "xyz_member": xyz_member,
            "physics_contact": inflight and xyz_member,
        }

    def _after_sim_step(self):
        for fire_key, pulse in self._pulse_by_fire.items():
            fire = self._fire_by_id[fire_key]
            if fire.activated and not pulse["activated"]:
                pulse["activated"] = True
                pulse["activation_time_s"] = self._sample_time

    def _decision_rows(self) -> list[dict]:
        pulse_ids_by_track: dict[int, list[int]] = {}
        for pulse in self._pulse_by_fire.values():
            track_id = pulse["track_id"]
            if track_id is not None:
                pulse_ids_by_track.setdefault(track_id, []).append(pulse["pulse_id"])

        rows = []
        for decision in self.engine.controller.decisions:
            if not decision.reject:
                continue
            evidence = self._decision_evidence(decision.tid)
            association = self.engine._track_members.get(decision.tid)
            requested_on = float(decision.t_fire - self.engine.policy.lead)
            pulse_ids = sorted(pulse_ids_by_track.get(decision.tid, []))
            accepted_nozzles = sorted(
                pulse["nozzle"] for pulse in self._pulse_by_fire.values()
                if pulse["track_id"] == decision.tid
            )
            rows.append({
                "track_id": int(decision.tid),
                "object_ids": [int(uid) for uid in decision.target_uids],
                "association": None if evidence is None else evidence["association"],
                "association_approximate": None if evidence is None else bool(
                    evidence["association_approximate"]
                ),
                "scheduled": bool(decision.scheduled),
                "controller_late": bool(decision.late),
                "requested_nozzles": [int(nozzle) for nozzle in decision.nozzles],
                "accepted_nozzles": accepted_nozzles,
                "target_fire_time_s": float(decision.t_fire),
                "available_time_s": float(decision.t_available),
                "decision_camera_x_m": float(decision.x),
                "decision_camera_y_m": float(decision.y),
                "estimated_forward_speed_m_s": float(decision.v),
                "observed_frame_age_s": (
                    None if association is None else
                    float(decision.t_decided - association["sim_time_s"])
                ),
                "requested_on_time_s": requested_on,
                "deadline_headroom_s": float(
                    decision.t_fire + CONTROLLER_LATE_ALLOWANCE_S - decision.t_available
                ),
                "requested_lead_headroom_s": float(requested_on - decision.t_available),
                "availability_clamp_s": float(max(0.0, decision.t_available - requested_on)),
                "pulse_ids": pulse_ids,
            })
        return rows

    def _target_diagnostics(self, decisions: list[dict]) -> list[dict]:
        pulses_by_id = {pulse["pulse_id"]: pulse for pulse in self._pulse_by_fire.values()}
        rows = []
        for decision in decisions:
            pulses = [pulses_by_id[pulse_id] for pulse_id in decision["pulse_ids"]]
            object_ids = decision["object_ids"] or [None]
            for object_id in object_ids:
                bean = self.sim.bean_by_uid.get(object_id) if object_id is not None else None
                samples = [
                    sample
                    for pulse in pulses
                    for sample in pulse["target_samples"]
                    if sample["object_id"] == object_id
                ]
                active_samples = [sample for sample in samples if sample["active"]]
                own_contacts = [
                    contact
                    for pulse in pulses
                    for contact in pulse["contacts"]
                    if contact["object_id"] == object_id and contact["is_associated_target"]
                ]
                category, detail = self._missed_category(
                    decision, pulses, active_samples, own_contacts, bean
                )
                rows.append({
                    "track_id": decision["track_id"],
                    "object_id": object_id,
                    "outcome": None if bean is None else bean.outcome,
                    "evaluation_mass_kg": None if bean is None else float(bean.mass),
                    "resolved_time_s": None if bean is None else bean.resolved_t,
                    "last_position_m": None if bean is None else bean.last_pos,
                    "association": decision["association"],
                    "association_approximate": decision["association_approximate"],
                    "category": category,
                    "category_detail": detail,
                    "category_is_observation_not_proven_cause": True,
                    "target_active_samples": len(active_samples),
                    "own_contact_samples": len(own_contacts),
                    "x_member_samples": sum(sample["x_member"] for sample in active_samples),
                    "xy_member_samples": sum(sample["xy_member"] for sample in active_samples),
                    "xyz_member_samples": sum(sample["xyz_member"] for sample in active_samples),
                    "uncertainty": self._uncertainty(decision, samples, bean),
                })
        return rows

    def _missed_category(self, decision, pulses, active_samples, own_contacts, bean):
        if not decision["scheduled"] or not pulses:
            if decision["controller_late"]:
                return "not_scheduled_late", "The controller rejected the command after its late threshold."
            return "not_scheduled", "No valid valve command exists for this rejection decision."
        if not any(pulse["activated"] for pulse in pulses):
            return "absent_activation", "A valve command exists, but no pulse reached its on-time."
        if not active_samples:
            return "target_absent_during_pulse", "The associated target was not active during sampled pulse steps."
        if not any(sample["x_member"] for sample in active_samples):
            xs = [sample["position_m"][0] for sample in active_samples]
            lower = self.sim.L.ej_x - JET_HALF_X
            upper = self.sim.L.ej_x + JET_HALF_X
            if max(xs) <= lower:
                detail = "Every sampled target position was upstream of the jet x window."
            elif min(xs) >= upper:
                detail = "Every sampled target position was downstream of the jet x window."
            else:
                detail = "No sampled target position entered the jet x window."
            return "timing", detail
        if not any(sample["xy_member"] for sample in active_samples):
            return "lateral", "The target entered the x window, but no sample matched x and y together."
        if not any(sample["xyz_member"] for sample in active_samples):
            return "height", "The target matched x and y, but no sample matched x, y, and z together."
        if not own_contacts:
            return "contact_unconfirmed", "The sampled predicates matched, but no contact record exists."
        if bean is None or bean.outcome is None:
            return "unresolved_after_contact", "The pulse contacted the target before the evaluation outcome was available."
        if bean.outcome == "spilled":
            return "spilled_after_contact", "The target spilled after contact. The final position distinguishes excessive deflection from other spill mechanisms."
        if bean.outcome != "reject":
            return "insufficient_deflection", "The pulse contacted the target, but the measured outcome was not reject."
        return "captured", "The pulse contacted the target, and the measured outcome was reject."

    @staticmethod
    def _uncertainty(decision, samples, bean) -> list[str]:
        notes = []
        if decision["association_approximate"]:
            notes.append("Object attribution uses camera component membership and is approximate.")
        if not decision["object_ids"]:
            notes.append("The controller track has no associated ground-truth object.")
        if not samples:
            notes.append("No intended pulse sample exists within the evaluation window.")
        if bean is not None and bean.outcome is None:
            notes.append("The object outcome was unresolved when measurement stopped.")
        notes.append("The category localizes observed conditions. It does not prove a physical cause.")
        return notes

    def summary(self, report: dict) -> dict:
        self._discover_fires()
        pulses = sorted(self._pulse_by_fire.values(), key=lambda pulse: pulse["pulse_id"])
        decisions = self._decision_rows()
        target_diagnostics = self._target_diagnostics(decisions)
        collateral = []
        for pulse in pulses:
            object_ids = sorted({
                contact["object_id"]
                for contact in pulse["contacts"]
                if not contact["is_associated_target"]
            })
            if object_ids:
                collateral.append({
                    "pulse_id": pulse["pulse_id"],
                    "track_id": pulse["track_id"],
                    "object_ids": object_ids,
                    "association_approximate": pulse["association_approximate"],
                    "contact_classification_uncertain": bool(pulse["association_approximate"]),
                })
        categories = Counter(row["category"] for row in target_diagnostics)
        recorded_pairs = {
            (pulse["track_id"], contact["object_id"])
            for pulse in pulses
            for contact in pulse["contacts"]
            if pulse["track_id"] is not None
        }
        simulation_pairs = {
            (int(track_id), int(object_id))
            for track_id, object_id in self.sim.fire_hits
        }
        only_recorded = sorted(recorded_pairs - simulation_pairs)
        only_simulation = sorted(simulation_pairs - recorded_pairs)
        reconciliation = {
            "matches": not only_recorded and not only_simulation,
            "recorded_pair_count": len(recorded_pairs),
            "simulation_pair_count": len(simulation_pairs),
            "only_recorded": [list(pair) for pair in only_recorded],
            "only_simulation": [list(pair) for pair in only_simulation],
        }
        return {
            "schema_version": 1,
            "session_id": report["session_id"],
            "simulation_seconds": report["runtime"]["simulation_seconds"],
            "preset_version": report["versions"]["preset"],
            "measurement_scope": "Evaluation only. Samples use pre-force qpos at each intended pulse step.",
            "evaluator_overhead": (
                "Included in active wall time and engine physics timing. "
                "Excluded from controller control-path timing."
            ),
            "physics_predicates": {
                "inflight": "x > 0.02 m",
                "x_member": f"abs(x - ejector_x) < {JET_HALF_X} m",
                "y_member": f"abs(y - nozzle_y) < {JET_HALF_Y_FACTOR} * nozzle_pitch",
                "z_member": "belt_z - 0.07 m < z < belt_z + 0.03 m",
                "physics_contact": "inflight and simultaneous x, y, z membership",
                "membership_note": "Contact uses one simultaneous sample. Separate axis minima never imply contact.",
            },
            "counts": {
                "rejection_decisions": len(decisions),
                "valve_pulses": len(pulses),
                "activated_pulses": sum(pulse["activated"] for pulse in pulses),
                "pulses_with_collateral_contact": len(collateral),
                "target_categories": dict(sorted(categories.items())),
                "feature_batches": len(self.feature_batches),
                "feature_rows": sum(len(batch) for batch in self.feature_batches),
            },
            "contact_reconciliation": reconciliation,
            "decisions": decisions,
            "pulses": pulses,
            "target_diagnostics": target_diagnostics,
            "collateral_contacts": collateral,
        }

    def save_features(self, path: Path):
        lengths = np.asarray([len(batch) for batch in self.feature_batches], dtype=np.int64)
        offsets = np.concatenate((np.zeros(1, dtype=np.int64), np.cumsum(lengths)))
        if self.feature_batches:
            features = np.concatenate(self.feature_batches, axis=0)
        else:
            features = np.empty((0, 0), dtype=np.float64)
        np.savez_compressed(
            path,
            features=features,
            batch_offsets=offsets,
            batch_lengths=lengths,
        )


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preset", type=Path, default=DEFAULT_PRESET)
    parser.add_argument("--seconds", type=float, default=2.0)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if not 0 < args.seconds <= 10:
        parser.error("seconds must be in the range 0 to 10")
    return args


def main():
    args = parse_args()
    engine = Engine(args.preset)
    measurements = PulseMeasurements(engine)
    measurements.install()
    try:
        while engine.sim.data.time + 1e-12 < args.seconds:
            engine.step()
        report = engine.report()
        pulse_summary = measurements.summary(report)
        args.out.mkdir(parents=True, exist_ok=True)
        report_path = args.out / "report.json"
        pulse_path = args.out / "pulse-summary.json"
        features_path = args.out / "features.npz"
        report_path.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
        pulse_path.write_text(json.dumps(pulse_summary, indent=2, allow_nan=False) + "\n")
        measurements.save_features(features_path)
        print(json.dumps({
            "report": str(report_path),
            "pulse_summary": str(pulse_path),
            "features": str(features_path),
            "simulation_seconds": report["runtime"]["simulation_seconds"],
            "pulse_count": pulse_summary["counts"]["valve_pulses"],
            "contact_reconciliation_ok": pulse_summary["contact_reconciliation"]["matches"],
        }, allow_nan=False))
    finally:
        engine.close()


if __name__ == "__main__":
    main()
