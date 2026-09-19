"""Exercise learned destinations through Jaume's simulated robot actions."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import mujoco
import numpy as np

from policy import load_policy
from robot_contract import SortDecision, execute_sort
from sim_dataset import (
    M4_FIXTURES, MujocoPhoneCamera, SimArduino, TableCalibration,
    _fixed_table_crop, _reset_scene, _settle, build_xml, extract_features, sd,
)
from controller import ArmController
from robot_api import RobotAPI


BIN_TO_TARGET = {"bin_A": "washers", "bin_B": "screws", "bin_C": "nuts"}


def run(policy_path: Path | None, out_dir: Path, predictor=None) -> dict:
    """Use a taught pickup station, then score bin placement outside the policy."""
    if predictor is None:
        policy = load_policy(policy_path)

        def predictor(crop):
            visual = extract_features(crop)
            return policy.predict(visual["values"], valid=visual["valid"])
    out_dir.mkdir(parents=True, exist_ok=True)
    sd.configure("theker_v1")
    trials = []
    for kind, name in M4_FIXTURES.items():
        model = mujoco.MjModel.from_xml_string(build_xml())
        data = mujoco.MjData(model)
        pieces = [body for body in sd.build() if body.piece]
        subject = next(body for body in pieces if body.name == name)
        # The fixed pickup station is an experimental fixture, not visual localization.
        _reset_scene(model, data, pieces, subject, 0.080, 0.0, 0.4)
        arduino = SimArduino(model, data, pieces)
        _settle(model, data, arduino)
        camera = MujocoPhoneCamera(model, data, "A")

        def step(count: int) -> None:
            for _ in range(count):
                mujoco.mj_step(model, data)
                arduino.tick()

        try:
            before = camera.grab()
            calibration = TableCalibration()
            if not calibration.fit(before):
                raise RuntimeError("camera calibration failed")
            crop = _fixed_table_crop(before, calibration)
            prediction = predictor(crop)
            controller = ArmController(
                arduino, step, frame_grab=camera.grab,
                frame_grabs={"A": camera.grab}, calibrations={"A": calibration},
            )
            robot = RobotAPI(controller, lambda: data.site("magnet_face").xpos.copy())
            target = BIN_TO_TARGET.get(prediction.destination)
            receipts = []
            if target is not None:
                receipts = execute_sort(
                    robot, SortDecision("observed-item", 8.0, 0.0, target), set(sd.TARGETS),
                )
                step(400)
            after = camera.grab()
            cv2.imwrite(str(out_dir / f"{kind}_before.png"), before)
            cv2.imwrite(str(out_dir / f"{kind}_crop.png"), crop)
            cv2.imwrite(str(out_dir / f"{kind}_after.png"), after)
            (out_dir / f"{kind}_serial.log").write_text("\n".join(arduino.log.lines) + "\n")

            # Simulator truth stays in the evaluator after policy prediction and execution.
            expected = {"screw": "screws", "nut": "nuts", "washer": "washers"}[kind]
            final_pos = data.body(name).xpos.copy()
            destination = sd.TARGETS[expected]
            radius = float(destination["size"][0])
            in_footprint = (
                np.linalg.norm(final_pos[:2] - np.array(destination["pos"])) < radius
                and 0.0 < final_pos[2] < 0.05
            )
            trial = {
                "evaluator_kind": kind,
                "destination": prediction.destination,
                "prediction_reason": prediction.reason,
                "target": target,
                "expected_target": expected,
                "correct_destination": target == expected,
                "receipts": receipts,
                "oracle_center_in_expected_bin_footprint": bool(in_footprint),
                "final_center_m": final_pos.tolist(),
                "calibration_fit_residual_mm": calibration.reprojection_error_mm(),
            }
            trials.append(trial)
            print(json.dumps(trial), flush=True)
        finally:
            camera.renderer.close()

    result = {
        "backend": "Jaume RobotAPI + ArmController + SimArduino + MuJoCo",
        "pickup_location_source": "fixed taught station at (8, 0) cm, not visual localization",
        "physical_hardware_used": False,
        "trials": trials,
        "limits": "Three isolated trials check integration. Oracle footprint checks are not physical or visual proof of a correct sort.",
    }
    (out_dir / "results.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    run(args.policy, args.out)
