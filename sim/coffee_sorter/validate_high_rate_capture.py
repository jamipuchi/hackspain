"""Validate a dense control replay against its published coarse source."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def json_hash(value) -> str:
    data = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(data).hexdigest()


def rows(frame, stride):
    values = frame["beans"]
    return {values[index]: values[index : index + stride] for index in range(0, len(values), stride)}


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replay", type=Path, required=True)
    parser.add_argument("--source-replay", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--object-id", type=int, action="append", default=[])
    return parser.parse_args()


def validate(args):
    dense = json.loads(args.replay.read_text())
    source = json.loads(args.source_replay.read_text())
    selected = args.object_id or [1470, 1480]
    mismatches = []
    stride = int(dense["schema"]["stride"])
    position_scale = int(dense["schema"]["positionScale"])
    quaternion_scale = int(dense["schema"]["quaternionScale"])
    expected_dt = float(dense["config"]["physicsTimestep"])

    times = [float(frame["t"]) for frame in dense["frames"]]
    cadence_errors = [
        {"index": index, "previous": times[index - 1], "time": times[index], "delta": times[index] - times[index - 1]}
        for index in range(1, len(times))
        if abs((times[index] - times[index - 1]) - expected_dt) > 1e-9
    ]
    mismatches.extend({"kind": "cadence", **item} for item in cadence_errors)

    quaternion_errors = []
    max_quaternion_norm_error = 0.0
    frame_identity_errors = []
    dense_rows = {}
    for frame in dense["frames"]:
        if len(frame["beans"]) % stride:
            error = {"time": frame["t"], "values": len(frame["beans"]), "stride": stride}
            frame_identity_errors.append(error)
            mismatches.append({"kind": "schema_stride", **error})
            continue
        frame_rows = rows(frame, stride)
        dense_rows[round(float(frame["t"]), 9)] = frame_rows
        if len(frame_rows) != len(frame["beans"]) // stride:
            error = {"time": frame["t"], "reason": "duplicate object ID"}
            frame_identity_errors.append(error)
            mismatches.append({"kind": "identity", **error})
        for object_id, row in frame_rows.items():
            quat = [value / quaternion_scale for value in row[4:8]]
            norm_error = abs(math.sqrt(sum(value * value for value in quat)) - 1.0)
            max_quaternion_norm_error = max(max_quaternion_norm_error, norm_error)
            if norm_error > 2.0 / quaternion_scale:
                error = {"time": frame["t"], "objectId": object_id, "normError": norm_error}
                quaternion_errors.append(error)
                mismatches.append({"kind": "quaternion_norm", **error})

    source_pose_mismatches = []
    compared_frames = 0
    compared_objects = 0
    max_position_delta_units = 0
    max_quaternion_delta_units = 0
    for source_frame in source["frames"]:
        timestamp = round(float(source_frame["t"]), 9)
        if not times or timestamp < times[0] - 1e-9 or timestamp > times[-1] + 1e-9:
            continue
        expected = rows(source_frame, int(source["schema"]["stride"]))
        actual = dense_rows.get(timestamp)
        if actual is None:
            error = {"time": timestamp, "reason": "dense frame missing"}
            source_pose_mismatches.append(error)
            mismatches.append({"kind": "source_pose", **error})
            continue
        compared_frames += 1
        expected_ids = set(expected)
        actual_ids = set(actual)
        if expected_ids != actual_ids:
            error = {
                "time": timestamp,
                "missingObjectIds": sorted(expected_ids - actual_ids),
                "extraObjectIds": sorted(actual_ids - expected_ids),
            }
            source_pose_mismatches.append(error)
            mismatches.append({"kind": "source_identity", **error})
        for object_id in sorted(expected_ids & actual_ids):
            compared_objects += 1
            expected_row = expected[object_id]
            actual_row = actual[object_id]
            position_delta = max(abs(expected_row[i] - actual_row[i]) for i in range(1, 4))
            quaternion_delta = max(abs(expected_row[i] - actual_row[i]) for i in range(4, 8))
            max_position_delta_units = max(max_position_delta_units, position_delta)
            max_quaternion_delta_units = max(max_quaternion_delta_units, quaternion_delta)
            if position_delta or quaternion_delta:
                error = {
                    "time": timestamp,
                    "objectId": object_id,
                    "positionDeltaUnits": position_delta,
                    "quaternionDeltaUnits": quaternion_delta,
                    "expected": expected_row[1:8],
                    "actual": actual_row[1:8],
                }
                source_pose_mismatches.append(error)
                mismatches.append({"kind": "source_pose", **error})

    class_names = [item["name"] for item in dense["classes"]]
    dense_beans = {row[0]: row for row in dense["beans"]}
    source_beans = {row[0]: row for row in source["beans"]}
    selected_rows = []
    selected_presence = {}
    for object_id in selected:
        row = dense_beans.get(object_id)
        source_row = source_beans.get(object_id)
        presence = [time for time, frame_rows in dense_rows.items() if object_id in frame_rows]
        selected_presence[object_id] = presence
        selected_rows.append(
            {
                "objectId": object_id,
                "class": None if row is None else class_names[row[1]],
                "outcome": None if row is None else row[6],
                "resolvedTime": None if row is None else row[7],
                "firstPoseTime": presence[0] if presence else None,
                "lastPoseTime": presence[-1] if presence else None,
                "sourceMetadataMatches": row == source_row,
            }
        )
        if row is None or row != source_row:
            mismatches.append(
                {"kind": "selected_identity", "objectId": object_id, "dense": row, "source": source_row}
            )

    pulse_by_id = {pulse["pulseId"]: pulse for pulse in dense["pulses"]}
    contact_errors = []
    belt_w = float(dense["layout"]["belt_w"])
    pitch = belt_w / int(dense["layout"]["n_nozzles"])
    ejector_x = float(dense["layout"]["ej_x"])
    belt_z = float(dense["layout"]["belt_z"])
    for contact in dense["contacts"]:
        pulse = pulse_by_id.get(contact["pulseId"])
        position = contact["position"]
        nozzle_y = -belt_w / 2 + (pulse["nozzle"] + 0.5) * pitch if pulse else None
        reasons = []
        if pulse is None:
            reasons.append("pulse missing")
        else:
            if not pulse["requestedOn"] <= contact["time"] < pulse["requestedOff"]:
                reasons.append("contact outside pulse interval")
            if pulse["activationTime"] is None or contact["time"] < pulse["activationTime"]:
                reasons.append("contact before activation")
            if not position[0] > 0.02:
                reasons.append("object not in flight")
            if not abs(position[0] - ejector_x) < 0.010:
                reasons.append("object outside x window")
            if not abs(position[1] - nozzle_y) < 0.75 * pitch:
                reasons.append("object outside y window")
            if not belt_z - 0.07 < position[2] < belt_z + 0.03:
                reasons.append("object outside z window")
        if reasons:
            error = {"contact": contact, "reasons": reasons}
            contact_errors.append(error)
            mismatches.append({"kind": "contact_chronology", **error})

    selected_contacts = {
        object_id: [item for item in dense["contacts"] if item["objectId"] == object_id]
        for object_id in selected
    }
    for object_id, contacts in selected_contacts.items():
        row = dense_beans.get(object_id)
        if row and row[7] is not None:
            for contact in contacts:
                if contact["time"] > row[7]:
                    mismatches.append(
                        {"kind": "contact_after_outcome", "objectId": object_id, "contact": contact, "outcomeTime": row[7]}
                    )

    common_presence = sorted(set.intersection(*(set(selected_presence[value]) for value in selected)))
    selected_contact_times = [item["time"] for value in selected_contacts.values() for item in value]
    useful_interval = None
    if common_presence and selected_contact_times:
        contact_time = min(selected_contact_times)
        start = max(common_presence[0], contact_time - 0.15)
        end = min(common_presence[-1] + expected_dt, contact_time + 0.15)
        sample_count = sum(start <= value < end for value in common_presence)
        useful_interval = {
            "start": round(start, 9),
            "endExclusive": round(end, 9),
            "samples": sample_count,
            "screenSecondsAt30Fps": sample_count / 30,
            "slowdown": (sample_count / 30) / (end - start),
        }

    valve_schedule_errors = []
    if len(dense["fires"]) != len(source["fires"]):
        valve_schedule_errors.append({
            "reason": "pulse count differs",
            "dense": len(dense["fires"]),
            "source": len(source["fires"]),
        })
    for index, (actual, expected) in enumerate(zip(dense["fires"], source["fires"])):
        if actual[2] != expected[2] or any(
            abs(float(actual[item]) - float(expected[item])) > 1e-15
            for item in (0, 1, 3)
        ):
            valve_schedule_errors.append({
                "index": index,
                "dense": actual,
                "source": expected,
            })
    valve_schedule_matches = not valve_schedule_errors
    if not valve_schedule_matches:
        mismatches.extend(
            {"kind": "valve_schedule", **error} for error in valve_schedule_errors
        )
    source_hash_matches = dense["source"]["controlReplaySha256"] == file_hash(args.source_replay)
    if not source_hash_matches:
        mismatches.append({"kind": "source_hash", "reason": "control replay hash differs"})

    return {
        "passed": not mismatches,
        "replay": str(args.replay),
        "replaySha256": file_hash(args.replay),
        "sourceReplay": str(args.source_replay),
        "sourceReplaySha256": file_hash(args.source_replay),
        "sourceHashMatches": source_hash_matches,
        "valveScheduleMatches": valve_schedule_matches,
        "valveScheduleErrors": valve_schedule_errors,
        "valveScheduleToleranceSeconds": 1e-15,
        "valveScheduleSha256": json_hash(source["fires"]),
        "cadence": {
            "expectedSeconds": expected_dt,
            "sampleRateHz": 1 / expected_dt,
            "frameCount": len(times),
            "firstTime": times[0] if times else None,
            "lastTime": times[-1] if times else None,
            "errors": cadence_errors,
        },
        "schema": {
            "stride": stride,
            "quaternionOrder": dense["schema"].get("quaternionOrder"),
            "frameIdentityErrors": frame_identity_errors,
            "quaternionNormTolerance": 2.0 / quaternion_scale,
            "maximumQuaternionNormError": max_quaternion_norm_error,
            "quaternionErrors": quaternion_errors,
        },
        "sourcePoseComparison": {
            "comparedFrames": compared_frames,
            "comparedObjects": compared_objects,
            "positionToleranceMetres": 1 / position_scale,
            "quaternionComponentTolerance": 1 / quaternion_scale,
            "maximumPositionDeltaUnits": max_position_delta_units,
            "maximumQuaternionDeltaUnits": max_quaternion_delta_units,
            "mismatches": source_pose_mismatches,
        },
        "selectedObjects": selected_rows,
        "selectedContacts": selected_contacts,
        "usefulInterval": useful_interval,
        "contactErrors": contact_errors,
        "mismatches": mismatches,
    }


def main():
    args = parse_args()
    report = validate(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({
        "passed": report["passed"],
        "mismatchCount": len(report["mismatches"]),
        "output": str(args.output),
    }))
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
