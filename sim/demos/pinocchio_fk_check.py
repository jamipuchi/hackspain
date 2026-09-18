"""Demo 2: load the same UR5e in Pinocchio and MuJoCo, compare forward kinematics.

Pinocchio reads the MuJoCo MJCF directly, so one model file serves both the simulator
and the analytic kinematics/dynamics library. Also prints the mass matrix and gravity
torques at the home pose.

Run:
    cd ~/robotics && .venv/bin/python demos/pinocchio_fk_check.py
"""

from __future__ import annotations

from pathlib import Path

import mujoco
import numpy as np
import pinocchio as pin

ROOT = Path(__file__).resolve().parent.parent
MJCF = ROOT / "mujoco_menagerie" / "universal_robots_ur5e" / "ur5e.xml"


def main() -> None:
    mj_model = mujoco.MjModel.from_xml_path(str(MJCF))
    mj_data = mujoco.MjData(mj_model)

    pin_model = pin.buildModelFromMJCF(str(MJCF))
    pin_data = pin_model.createData()
    print(f"Pinocchio model: nq={pin_model.nq}, joints={[n for n in pin_model.names[1:]]}")

    # Pinocchio's MJCF loader places the root body at the identity and ignores its
    # pos/quat attributes (Menagerie's UR5e base is yawed 180 deg), so compare
    # positions expressed in the base frame.
    def mj_pos_in_base(body: str) -> np.ndarray:
        base_rot = mj_data.body("base").xmat.reshape(3, 3)
        base_pos = mj_data.body("base").xpos
        return base_rot.T @ (mj_data.body(body).xpos - base_pos)

    rng = np.random.default_rng(0)
    worst = 0.0
    for _ in range(20):
        q = rng.uniform(-np.pi, np.pi, size=mj_model.nq)

        mj_data.qpos[:] = q
        mujoco.mj_forward(mj_model, mj_data)
        mj_pos = mj_pos_in_base("wrist_3_link")

        pin.forwardKinematics(pin_model, pin_data, q)
        pin.updateFramePlacements(pin_model, pin_data)
        frame_id = pin_model.getFrameId("wrist_3_link")
        pin_pos = pin_data.oMf[frame_id].translation

        worst = max(worst, float(np.linalg.norm(mj_pos - pin_pos)))

    print(f"max |FK_mujoco - FK_pinocchio| over 20 random configs: {worst * 1e6:.3f} um")

    q_home = np.array([-1.5708, -1.5708, 1.5708, -1.5708, -1.5708, 0.0])
    M = pin.crba(pin_model, pin_data, q_home)
    g = pin.computeGeneralizedGravity(pin_model, pin_data, q_home)
    np.set_printoptions(precision=3, suppress=True)
    print("mass matrix diagonal at home:", np.diag(M))
    print("gravity torques at home [Nm]:", g)


if __name__ == "__main__":
    main()
